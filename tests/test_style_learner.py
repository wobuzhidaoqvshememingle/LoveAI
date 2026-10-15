# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - StyleLearner 单元测试

测试用户说话风格学习服务：
  - 样本不足处理 / 只学习本人 sent 消息（不混入 received）
  - 语气词 / 句尾 / 口头禅 / 标点 / 表情包 emoji 统计
  - 本地 traits 归纳
  - 档案存储与读取 / build_style_context 注入文本
  - maybe_update 阈值自动触发
  - 云端可选增强（mock LLM）与无 LLM 降级
"""
import os
import sys
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.style_learner import StyleLearner  # noqa: E402

_SELF_MSGS = [
    "行吧，那就这样", "哈哈哈 [捂脸]", "你吃饭了吗呀", "好的好的", "嗯，我知道了吧",
    "在吗", "我马上来哈", "随便吧", "哈哈哈哈", "好呀好呀",
    "嗯嗯", "那行吧", "我去，这么牛", "笑死我了 [偷笑]", "可以的",
    "等我一下哈", "行吧行吧", "你说啥？", "真的假的？", "好吧",
    "到了吗", "来了来了", "哈哈 [捂脸]", "行吧", "好的哈",
]
_OTHER_MSGS = ["你在干嘛", "今天吃什么", "好的呢", "嗯嗯好", "哈哈哈笑死"]


class TestStyleLearner(unittest.TestCase):
    """StyleLearner 测试类（临时 SQLite 文件数据库）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_test_style_")
        self.db_path = os.path.join(self.tmpdir, "test_style.db")
        self.db = Database(self.db_path)
        self.db.init_tables()
        self.learner = StyleLearner(self.db, llm=None, data_dir=self.tmpdir)

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass

    def _insert(self, msgs, direction):
        for m in msgs:
            self.db.execute(
                "INSERT INTO chat_messages (person_id, direction, content, msg_type) "
                "VALUES ('p1',:d,:c,'text')", {"d": direction, "c": m}
            )

    # ------------------------------------------------------------------ #
    def test_insufficient_samples(self):
        """少于 5 条本人消息时不学习。"""
        self._insert(["好的", "嗯"], "sent")
        r = self.learner.learn_now(use_cloud=False)
        self.assertFalse(r["learned"])
        self.assertIsNone(self.learner.get_profile())

    def test_only_learns_self_messages(self):
        """只统计 sent 消息，不混入 received。"""
        self._insert(_SELF_MSGS, "sent")
        self._insert(_OTHER_MSGS, "received")
        r = self.learner.learn_now(use_cloud=False)
        self.assertTrue(r["learned"])
        p = self.learner.get_profile()
        self.assertEqual(p["sample_count"], len(_SELF_MSGS))

    def test_tone_particles(self):
        """语气词统计。"""
        self._insert(_SELF_MSGS, "sent")
        self.learner.learn_now(use_cloud=False)
        p = self.learner.get_profile()
        self.assertIn("哈", p["tone_particles"])
        self.assertIn("吧", p["tone_particles"])

    def test_endings(self):
        """句尾字统计。"""
        self._insert(_SELF_MSGS, "sent")
        self.learner.learn_now(use_cloud=False)
        p = self.learner.get_profile()
        endings = p["endings"]
        self.assertTrue(endings.get("哈", 0) >= 1 or endings.get("吧", 0) >= 1)

    def test_catchphrases(self):
        """跨多条消息的标志性短语被提取。"""
        self._insert(_SELF_MSGS, "sent")
        self.learner.learn_now(use_cloud=False)
        p = self.learner.get_profile()
        self.assertIn("行吧", p["catchphrases"])

    def test_punctuation(self):
        """问号 / 感叹号统计。"""
        self._insert(_SELF_MSGS, "sent")
        self.learner.learn_now(use_cloud=False)
        p = self.learner.get_profile()
        self.assertGreaterEqual(p["punctuation"]["question"], 2)

    def test_stickers_and_emoji(self):
        """微信表情包占位文字识别。"""
        self._insert(_SELF_MSGS, "sent")
        self.learner.learn_now(use_cloud=False)
        p = self.learner.get_profile()
        self.assertIn("[捂脸]", p["emoji"]["sticker_top"])

    def test_traits(self):
        """本地规则归纳风格标签。"""
        self._insert(_SELF_MSGS, "sent")
        r = self.learner.learn_now(use_cloud=False)
        self.assertTrue(len(r["traits"]) >= 1)
        self.assertIn("简洁惜字", r["traits"])

    def test_persistence_and_markdown(self):
        """档案落库并生成可读 md。"""
        self._insert(_SELF_MSGS, "sent")
        self.learner.learn_now(use_cloud=False)
        row = self.db.fetch_one("SELECT sample_count, source FROM user_speech_style WHERE id=1")
        self.assertEqual(row["sample_count"], len(_SELF_MSGS))
        self.assertEqual(row["source"], "local")
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "user_style.md")))

    def test_build_context_empty_vs_filled(self):
        """无样本给保守提示，有样本含特征。"""
        empty = self.learner.build_style_context()
        self.assertIn("暂无", empty)
        self._insert(_SELF_MSGS, "sent")
        self.learner.learn_now(use_cloud=False)
        filled = self.learner.build_style_context()
        self.assertIn("用户本人说话习惯", filled)
        self.assertIn("行吧", filled)

    def test_maybe_update_threshold(self):
        """新增达到阈值才重算。"""
        self._insert(_SELF_MSGS, "sent")
        r1 = self.learner.maybe_update()
        self.assertTrue(r1["learned"])
        r2 = self.learner.maybe_update()
        self.assertFalse(r2["learned"])

    def test_cloud_enhance_without_llm(self):
        """无 LLM 时云端增强返回 None，本地档案不受影响。"""
        self._insert(_SELF_MSGS, "sent")
        self.learner.learn_now(use_cloud=False)
        self.assertIsNone(self.learner.enrich_with_cloud())
        p = self.learner.get_profile()
        self.assertNotIn("cloud_summary", p)

    def test_cloud_enhance_with_mock_llm(self):
        """mock LLM 云端增强结果合并回本地。"""
        class MockLLM:
            def l3_available(self):
                return True

            def chat(self, system_prompt, user_prompt, complexity=None):
                return "总体简洁直接；特征：爱用'行吧'、句尾'哈'；模仿时保持短句。"

        learner = StyleLearner(self.db, llm=MockLLM(), data_dir=self.tmpdir)
        self._insert(_SELF_MSGS, "sent")
        summary = learner.enrich_with_cloud(force=True)
        self.assertIsNotNone(summary)
        p = learner.get_profile()
        self.assertIn("简洁", p["cloud_summary"])
        row = self.db.fetch_one("SELECT source FROM user_speech_style WHERE id=1")
        self.assertEqual(row["source"], "local+cloud")

    def test_cloud_enhance_failure_degrades(self):
        """云端抛错时优雅降级，不影响本地学习。"""
        class FailLLM:
            def l3_available(self):
                return True

            def chat(self, *a, **k):
                raise RuntimeError("network down")

        learner = StyleLearner(self.db, llm=FailLLM(), data_dir=self.tmpdir)
        self._insert(_SELF_MSGS, "sent")
        self.assertIsNone(learner.enrich_with_cloud(force=True))
        r = learner.learn_now(use_cloud=True)
        self.assertTrue(r["learned"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
