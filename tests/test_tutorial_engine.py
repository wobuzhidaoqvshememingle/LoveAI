# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - TutorialEngine 单元测试

覆盖：
  - guides 表自动创建
  - generate_task_direction 非空 / LLM=None 时返回默认方向
  - search_guides 空库返回空 list
  - get_guide 不存在 id 返回 None
  - _generate_self_guide mock JSON 解析
  - _generate_target_guide 查询 persons + current_affection
  - list_guides 按 mode 过滤
  - generate_step_by_step_tutorial 展开 plan
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.tutorial_engine import TutorialEngine  # noqa: E402


class TestTutorialEngine(unittest.TestCase):
    """TutorialEngine 测试类（临时 SQLite + mock LLM/RAG）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_test_tutorial_")
        self.db_path = os.path.join(self.tmpdir, "test_tutorial.db")
        self.db = Database(self.db_path)
        self.db.init_tables()
        self.llm = MagicMock()
        self.rag = MagicMock()
        self.rag.search.return_value = []
        self.engine = TutorialEngine(self.db, self.rag, self.llm, twin=None)
        # 预置一个人物与好感度，供 target 模式查询
        self.db.execute(
            "INSERT INTO persons (id, nickname, gender, occupation, "
            "relationship_stage) VALUES (:id,:n,:g,:o,:s)",
            {"id": "p1", "n": "苏晚", "g": "女", "o": "设计师", "s": 2},
        )
        self.db.execute(
            "INSERT INTO current_affection (person_id, total_score) "
            "VALUES (:pid, :ts)",
            {"pid": "p1", "ts": 62.0},
        )

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 初始化
    # ------------------------------------------------------------------ #
    def test_init_creates_guides_table(self) -> None:
        """__init__ 后 guides 表应可查询。"""
        rows = self.db.fetch_all("SELECT * FROM guides")
        self.assertIsInstance(rows, list)

    # ------------------------------------------------------------------ #
    # 任务方向
    # ------------------------------------------------------------------ #
    def test_generate_task_direction_nonempty(self) -> None:
        """LLM 可用时 generate_task_direction 返回非空文本。"""
        self.llm.chat.return_value = "主动找对方感兴趣的话题，语气轻松。"
        direction = self.engine.generate_task_direction("发起破冰", "p1")
        self.assertIsInstance(direction, str)
        self.assertGreater(len(direction), 0)

    def test_generate_task_direction_llm_none(self) -> None:
        """LLM=None 时应返回默认方向。"""
        eng = TutorialEngine(self.db, self.rag, llm=None)
        direction = eng.generate_task_direction("任意任务")
        self.assertEqual(direction, TutorialEngine.DEFAULT_DIRECTION)

    # ------------------------------------------------------------------ #
    # 攻略库检索
    # ------------------------------------------------------------------ #
    def test_search_guides_empty(self) -> None:
        """空库且 RAG 无结果时返回空 list。"""
        results = self.engine.search_guides("破冰")
        self.assertEqual(results, [])

    def test_get_guide_not_found(self) -> None:
        """不存在的攻略 id 应返回 None。"""
        self.assertIsNone(self.engine.get_guide("no_such_id"))

    # ------------------------------------------------------------------ #
    # self 模式攻略生成
    # ------------------------------------------------------------------ #
    def test_generate_self_guide_parses_json(self) -> None:
        """_generate_self_guide 应正确解析 LLM JSON 并落库。"""
        self.llm.chat.return_value = (
            '{"title":"提升沟通表达",'
            '"analysis":"现状偏内向",'
            '"plan":[{"step":"第1步","action":"练习开场白","timeline":"第1周"}],'
            '"tips":["多倾听"],"resources":["书"],"milestones":[]}'
        )
        guide = self.engine.generate_guide("我不会聊天", mode="self")
        self.assertEqual(guide["mode"], "self")
        self.assertEqual(guide["title"], "提升沟通表达")
        self.assertIn("id", guide)
        self.assertEqual(len(guide["plan"]), 1)
        # 已落库
        self.assertIsNotNone(self.engine.get_guide(guide["id"]))

    def test_self_guide_llm_failure_fallback(self) -> None:
        """LLM 抛异常时走兜底 self 攻略。"""
        self.llm.chat.side_effect = RuntimeError("boom")
        guide = self.engine.generate_guide("随便问问", mode="self")
        self.assertEqual(guide["mode"], "self")
        self.assertIn("plan", guide)

    # ------------------------------------------------------------------ #
    # target 模式攻略生成
    # ------------------------------------------------------------------ #
    def test_generate_target_guide(self) -> None:
        """target 模式应查询 persons + current_affection 并返回攻略。"""
        self.llm.chat.return_value = (
            '{"title":"暧昧升温攻略",'
            '"situation_analysis":"双方有好感",'
            '"strategy":"制造独处",'
            '"plan":[{"step":"第1步","action":"约饭","timeline":"本周"}],'
            '"risk_warnings":["别推进太快"],'
            '"expected_outcome":"关系升温",'
            '"alternative_plans":["Plan B"]}'
        )
        guide = self.engine.generate_guide("怎么推进关系", mode="target",
                                           person_id="p1")
        self.assertEqual(guide["mode"], "target")
        self.assertEqual(guide["title"], "暧昧升温攻略")
        self.assertIn("strategy", guide)

    def test_target_missing_person_falls_back(self) -> None:
        """target 模式缺 person_id 应降级为 self 模式。"""
        self.llm.chat.return_value = '{"title":"x","analysis":"a","plan":[],"tips":[],"resources":[],"milestones":[]}'
        guide = self.engine.generate_guide("问题", mode="target", person_id=None)
        self.assertEqual(guide["mode"], "self")

    # ------------------------------------------------------------------ #
    # 列表过滤
    # ------------------------------------------------------------------ #
    def test_list_guides_filter_by_mode(self) -> None:
        """list_guides(mode='self') 应只返回 self 攻略。"""
        self.llm.chat.return_value = '{"title":"s","analysis":"a","plan":[],"tips":[],"resources":[],"milestones":[]}'
        self.engine.generate_guide("q1", mode="self")
        self.llm.chat.return_value = '{"title":"t","situation_analysis":"a","strategy":"s","plan":[],"risk_warnings":[],"expected_outcome":"e","alternative_plans":[]}'
        self.engine.generate_guide("q2", mode="target", person_id="p1")
        self_only = self.engine.list_guides(mode="self")
        self.assertTrue(all(g["mode"] == "self" for g in self_only))
        self.assertGreaterEqual(len(self_only), 1)

    # ------------------------------------------------------------------ #
    # 分步教程展开
    # ------------------------------------------------------------------ #
    def test_step_by_step_tutorial_expands_plan(self) -> None:
        """generate_step_by_step_tutorial 应把 plan 每一步展开。"""
        self.llm.chat.return_value = '{"title":"t","analysis":"a","plan":[{"step":"第1步","action":"练习开场白","timeline":"本周"}],"tips":[],"resources":[],"milestones":[]}'
        guide = self.engine.generate_guide("q", mode="self")
        # 展开时 LLM 返回单步详情 JSON
        self.llm.chat.return_value = (
            '{"detail":"详细步骤","script_example":"嗨，最近怎样",'
            '"tips":"自然一点"}'
        )
        tutorial = self.engine.generate_step_by_step_tutorial(guide["id"])
        self.assertEqual(tutorial["guide_id"], guide["id"])
        self.assertEqual(len(tutorial["steps"]), 1)
        self.assertIn("detail", tutorial["steps"][0])

    def test_step_by_step_unknown_guide(self) -> None:
        """攻略不存在时返回空 steps。"""
        out = self.engine.generate_step_by_step_tutorial("bad_id")
        self.assertEqual(out["steps"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
