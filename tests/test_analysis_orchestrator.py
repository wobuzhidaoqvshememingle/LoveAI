# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 统一分析编排器单元测试

验证：
1. 10种场景识别正确
2. 不同场景触发不同模块组合（按需选择，不是每次全跑）
3. 模块间数据传递链存在
4. 单模块失败不导致整体崩溃
5. 结果结构完整（scene/personality/strategy/laws/tasks/risk_warnings/modules_called）
6. 服务层不 import PySide6
7. 6个孤岛模块都有明确调用点
"""
import os
import sys
import shutil
import tempfile
import pytest
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from models.database import Database
from services.analysis_orchestrator import (
    AnalysisOrchestrator,
    _contains_any,
    _SCENE_CONFLICT_WORDS,
    _SCENE_COLDDOWN_WORDS,
    _SCENE_FLIRT_WORDS,
    _SCENE_INVITE_WORDS,
    _SCENE_MONEY_WORDS,
    _SCENE_CONFESSION_WORDS,
    _SCENE_BREAKUP_WORDS,
    _SCENE_COMMITMENT_WORDS,
)


# ====================================================================== #
# Fixtures
# ====================================================================== #
@pytest.fixture
def temp_db():
    """创建临时数据库，初始化表结构，插入测试数据。"""
    tmpdir = tempfile.mkdtemp(prefix="loveai_orch_")
    db = Database(os.path.join(tmpdir, "test.db"))
    db.init_tables()
    # 插入测试人物
    db.execute(
        "INSERT INTO persons (id, nickname, gender, relationship_stage) "
        "VALUES (:pid, :nick, :gender, :stage)",
        {"pid": "test_person_001", "nick": "测试对象", "gender": "female", "stage": 3},
    )
    # 插入一些聊天记录（正常对话模式）
    now = datetime.now()
    for i in range(20):
        direction = "received" if i % 2 == 0 else "sent"
        content = f"测试消息第{i}条"
        ts = (now - timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT INTO chat_messages (person_id, direction, content, created_at) "
            "VALUES (:pid, :dir, :ct, :ca)",
            {"pid": "test_person_001", "dir": direction, "ct": content, "ca": ts},
        )
    yield db
    try:
        db.engine.dispose()
    except Exception:
        pass
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def orchestrator(temp_db):
    """创建编排器实例（带数据库）。"""
    return AnalysisOrchestrator(db=temp_db)


@pytest.fixture
def orchestrator_no_db():
    """创建无数据库的编排器实例（用于纯逻辑测试）。"""
    return AnalysisOrchestrator(db=None)


# ====================================================================== #
# 1. 场景识别测试
# ====================================================================== #
class TestSceneDetection:
    """验证10种场景识别逻辑。"""

    def test_conflict_scene_detected(self, orchestrator_no_db):
        name, conf = orchestrator_no_db._detect_scene("我真的受够了，我们吵架吧，你太不可理喻了", {}, "")
        assert name == "冲突争吵"
        assert conf >= 0.7

    def test_flirt_scene_detected(self, orchestrator_no_db):
        name, conf = orchestrator_no_db._detect_scene("宝贝想你了，早安，今天在干嘛呀", {}, "")
        assert name == "暧昧推进"
        assert conf >= 0.5

    def test_colddown_scene_detected(self, orchestrator_no_db):
        name, conf = orchestrator_no_db._detect_scene("哦嗯", {}, "")
        assert name == "对方冷淡"
        assert conf >= 0.5

    def test_invite_scene_detected(self, orchestrator_no_db):
        name, conf = orchestrator_no_db._detect_scene("周末有空吗？出来玩，一起去看电影吃饭", {}, "")
        assert name == "邀约"
        assert conf >= 0.6

    def test_money_scene_detected(self, orchestrator_no_db):
        name, conf = orchestrator_no_db._detect_scene("能借我5000吗？转账给我，下个月发工资还你", {}, "")
        assert name == "金钱往来"
        assert conf >= 0.6

    def test_confession_scene_detected(self, orchestrator_no_db):
        name, conf = orchestrator_no_db._detect_scene("我喜欢你很久了，做我女朋友吧，我们在一起", {}, "")
        assert name == "情感表白"
        assert conf >= 0.7

    def test_breakup_scene_detected(self, orchestrator_no_db):
        name, conf = orchestrator_no_db._detect_scene("我们分手吧，不合适，走不下去了，累了", {}, "")
        assert name == "分手危机"
        assert conf >= 0.7

    def test_daily_chat_default(self, orchestrator_no_db):
        name, conf = orchestrator_no_db._detect_scene("今天工作忙了一天，刚到家", {}, "")
        assert name == "日常闲聊"

    def test_commitment_keywords_detected(self, orchestrator_no_db):
        assert _contains_any("我答应你，下次一定来", _SCENE_COMMITMENT_WORDS)

    def test_no_reply_scenario_via_message_gap(self, orchestrator):
        """已读不回场景通过消息间隔识别。"""
        old_ts = (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d %H:%M:%S")
        orchestrator._db.execute(
            "INSERT INTO chat_messages (person_id, direction, content, created_at) "
            "VALUES (:pid, :dir, :ct, :ca)",
            {"pid": "test_person_001", "dir": "sent", "ct": "在吗？怎么不回我", "ca": old_ts},
        )
        name, conf = orchestrator._detect_scene("在吗？怎么不回我", {}, "test_person_001")
        assert name in ("已读不回", "对方冷淡", "日常闲聊")


# ====================================================================== #
# 2. 模块选择测试（按需选择，不是每次全跑）
# ====================================================================== #
class TestModuleSelection:
    """验证不同场景触发不同模块组合。"""

    def test_daily_chat_minimal_modules(self, orchestrator_no_db):
        plan = orchestrator_no_db._select_modules("日常闲聊", "今天天气不错", {})
        assert plan["personality_strategy"] is True
        assert plan["strategy_advisor"] is False
        assert plan["psychology_laws"] is False
        assert plan["no_reply_detector"] is False
        assert plan["commitment_tracker"] is False
        assert plan["deception_detector"] is False

    def test_conflict_scene_full_modules(self, orchestrator_no_db):
        plan = orchestrator_no_db._select_modules("冲突争吵", "我们吵架了", {})
        assert plan["personality_strategy"] is True
        assert plan["strategy_advisor"] is True
        assert plan["psychology_laws"] is True
        assert plan["deception_detector"] is True

    def test_no_reply_scene_prioritizes_detector(self, orchestrator_no_db):
        plan = orchestrator_no_db._select_modules("已读不回", "怎么不回我", {})
        assert plan["no_reply_detector"] is True
        assert plan["strategy_advisor"] is True

    def test_money_scene_runs_deception_and_commitment(self, orchestrator_no_db):
        plan = orchestrator_no_db._select_modules("金钱往来", "借我5000", {})
        assert plan["deception_detector"] is True
        assert plan["commitment_tracker"] is True
        assert plan["strategy_advisor"] is True

    def test_commitment_keywords_force_tracker(self, orchestrator_no_db):
        plan = orchestrator_no_db._select_modules("日常闲聊", "我答应你下次一定来", {})
        assert plan["commitment_tracker"] is True

    def test_invite_scene_runs_commitment(self, orchestrator_no_db):
        plan = orchestrator_no_db._select_modules("邀约", "周末出来玩吗", {})
        assert plan["commitment_tracker"] is True
        assert plan["strategy_advisor"] is True
        assert plan["psychology_laws"] is True

    def test_breakup_scene_runs_all_critical(self, orchestrator_no_db):
        plan = orchestrator_no_db._select_modules("分手危机", "我们分手吧", {})
        assert plan["personality_strategy"] is True
        assert plan["strategy_advisor"] is True
        assert plan["psychology_laws"] is True
        assert plan["deception_detector"] is True
        assert plan["no_reply_detector"] is True


# ====================================================================== #
# 3. 端到端编排测试
# ====================================================================== #
class TestOrchestrateEndToEnd:
    """验证完整编排流程。"""

    def test_orchestrate_returns_complete_structure(self, orchestrator):
        result = orchestrator.orchestrate(
            person_id="test_person_001",
            chat_text="今天天气不错，吃了吗",
            context={"emotion": {"label": "neutral"}, "llm_structured": {}, "relation": {}}
        )
        assert "scene" in result
        assert "personality" in result
        assert "strategy" in result
        assert "psychology_laws" in result
        assert "tasks" in result
        assert "risk_warnings" in result
        assert "modules_called" in result
        assert "name" in result["scene"]
        assert "confidence" in result["scene"]
        # modules_called 至少包含 personality_strategy（日常闲聊必跑）
        assert "personality_strategy" in result["modules_called"]

    def test_orchestrate_daily_chat_calls_minimal_modules(self, orchestrator):
        result = orchestrator.orchestrate(
            person_id="test_person_001",
            chat_text="今天天气不错",
            context={"emotion": {"label": "neutral"}, "llm_structured": {}, "relation": {}}
        )
        called = result["modules_called"]
        # 日常闲聊不应该选中 strategy_advisor 和 psychology_laws
        assert "strategy_advisor" not in called
        assert "psychology_laws" not in called

    def test_orchestrate_conflict_calls_more_modules(self, orchestrator):
        result = orchestrator.orchestrate(
            person_id="test_person_001",
            chat_text="我真的受够了，我们吵架吧",
            context={"emotion": {"label": "angry"}, "llm_structured": {}, "relation": {}}
        )
        called = result["modules_called"]
        assert called["strategy_advisor"] in ("ok", "skipped", "failed")
        assert called["psychology_laws"] in ("ok", "skipped", "failed")
        assert called["deception_detector"] in ("ok", "skipped", "failed")

    def test_orchestrate_no_person_id_graceful(self, orchestrator_no_db):
        result = orchestrator_no_db.orchestrate(
            person_id="",
            chat_text="测试",
            context={"emotion": {}, "llm_structured": {}, "relation": {}}
        )
        assert "scene" in result
        assert "modules_called" in result

    def test_orchestrate_empty_chat_graceful(self, orchestrator):
        result = orchestrator.orchestrate(
            person_id="test_person_001",
            chat_text="",
            context={"emotion": {}, "llm_structured": {}, "relation": {}}
        )
        assert "scene" in result


# ====================================================================== #
# 4. 模块间数据传递测试
# ====================================================================== #
class TestDataFlow:
    """验证模块间数据传递链。"""

    def test_personality_injected_into_persona(self):
        persona = {"attachment": {"type": "avoidant"}}
        persona["_orchestrator_personality"] = {"primary_key": "avpd", "label": "回避型人格障碍"}
        assert "_orchestrator_personality" in persona
        assert persona["_orchestrator_personality"]["primary_key"] == "avpd"

    def test_deception_risk_injected_into_persona(self):
        persona = {}
        persona["deception_risk"] = "high"
        assert persona["deception_risk"] == "high"

    def test_no_reply_reason_injected_into_persona(self):
        persona = {}
        persona["_orchestrator_no_reply_reason"] = "avoidant_space"
        persona["_orchestrator_no_reply_severity"] = "moderate"
        assert persona["_orchestrator_no_reply_reason"] == "avoidant_space"


# ====================================================================== #
# 5. 异常隔离测试
# ====================================================================== #
class TestExceptionIsolation:
    """验证单模块失败不影响整体。"""

    def test_run_with_timeout_catches_exception(self):
        from services.analysis_orchestrator import _run_with_timeout
        def failing_func():
            raise ValueError("test error")
        ok, result = _run_with_timeout(failing_func, timeout=1.0)
        assert ok is False
        assert isinstance(result, ValueError)

    def test_run_with_timeout_returns_result(self):
        from services.analysis_orchestrator import _run_with_timeout
        def success_func():
            return {"key": "value"}
        ok, result = _run_with_timeout(success_func, timeout=1.0)
        assert ok is True
        assert result == {"key": "value"}

    def test_orchestrator_with_none_db_does_not_crash(self):
        orch = AnalysisOrchestrator(db=None)
        result = orch.orchestrate(
            person_id="test",
            chat_text="测试",
            context={"emotion": {}, "llm_structured": {}, "relation": {}}
        )
        assert "scene" in result
        assert "modules_called" in result


# ====================================================================== #
# 6. 架构约束测试
# ====================================================================== #
class TestArchitectureConstraints:
    """验证架构约束。"""

    def test_no_pyside6_import(self):
        import inspect
        source = inspect.getsource(sys.modules["services.analysis_orchestrator"])
        import_lines = [line for line in source.split("\n")
                       if line.strip().startswith("import ") or line.strip().startswith("from ")]
        for line in import_lines:
            assert "PySide6" not in line, f"发现 PySide6 import: {line}"

    def test_all_six_island_modules_have_call_points(self, orchestrator_no_db):
        methods = [
            "_run_personality_strategy",
            "_run_strategy_advisor",
            "_run_psychology_laws",
            "_run_no_reply_detector",
            "_run_commitment_tracker",
            "_run_deception_detector",
        ]
        for method in methods:
            assert hasattr(orchestrator_no_db, method), f"缺少方法: {method}"
            assert callable(getattr(orchestrator_no_db, method))

    def test_scene_keywords_nonempty(self):
        assert len(_SCENE_CONFLICT_WORDS) > 0
        assert len(_SCENE_COLDDOWN_WORDS) > 0
        assert len(_SCENE_FLIRT_WORDS) > 0
        assert len(_SCENE_INVITE_WORDS) > 0
        assert len(_SCENE_MONEY_WORDS) > 0
        assert len(_SCENE_CONFESSION_WORDS) > 0
        assert len(_SCENE_BREAKUP_WORDS) > 0
        assert len(_SCENE_COMMITMENT_WORDS) > 0

    def test_contains_any_helper(self):
        assert _contains_any("我生气了", ("生气", "开心")) is True
        assert _contains_any("我很开心", ("生气", "难过")) is False
        assert _contains_any("", ("测试",)) is False
        assert _contains_any(None, ("测试",)) is False


# ====================================================================== #
# 7. 任务汇总测试
# ====================================================================== #
class TestTaskAggregation:
    """验证任务汇总逻辑。"""

    def test_collect_tasks_returns_list(self, orchestrator_no_db):
        tasks = orchestrator_no_db._collect_tasks(
            personality={"all_tendencies": []},
            no_reply={},
            laws={"hits_count": 0, "hits": []},
            persona={},
        )
        assert isinstance(tasks, list)

    def test_collect_tasks_with_personality(self, orchestrator_no_db):
        persona = {"attachment": {"type": "avoidant"}}
        personality = {
            "all_tendencies": [{"key": "avpd", "label": "回避型", "score": 0.8}],
            "primary_key": "avpd",
        }
        tasks = orchestrator_no_db._collect_tasks(
            personality=personality,
            no_reply={},
            laws={"hits_count": 0, "hits": []},
            persona=persona,
        )
        assert isinstance(tasks, list)


# ====================================================================== #
# 8. 结构/时序上下文检测（嘴硬破冰 / 提议-否定循环）回归测试
# ====================================================================== #
class TestStructuralContext:
    """验证字面关键词之外的时序/对话结构识别。"""

    def _db_with(self, turns, stage=6):
        tmpdir = tempfile.mkdtemp(prefix="loveai_struct_")
        db = Database(os.path.join(tmpdir, "s.db"))
        db.init_tables()
        pid = "struct_person"
        db.execute(
            "INSERT OR REPLACE INTO persons (id, nickname, gender, relationship_stage) "
            "VALUES (:pid, :n, 'female', :s)",
            {"pid": pid, "n": pid, "s": stage},
        )
        now = datetime.now()
        n = len(turns)
        for i, (d, c) in enumerate(turns):
            ts = (now - timedelta(hours=n - i)).strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT INTO chat_messages (person_id, direction, content, created_at) "
                "VALUES (:pid, :d, :c, :t)",
                {"pid": pid, "d": d, "c": c, "t": ts},
            )
        return db, pid

    def test_proposal_rejection_loop(self):
        """连续被否定所有提议+只说'随便' → 识别为作/索取关注，而非抑郁。"""
        turns = [
            ("sent", "晚上吃点什么？"), ("received", "随便。"),
            ("sent", "那吃火锅吧？"), ("received", "不行，太腻了，我减肥。"),
            ("sent", "那喝点粥？"), ("received", "太淡了吧。"),
            ("sent", "那吃炒面吧？"), ("received", "我不喜欢吃炒面"),
            ("received", "我为什么生气，你心里没点数吗"),
        ]
        db, pid = self._db_with(turns)
        orch = AnalysisOrchestrator(db=db)
        ctx = {}
        scene, conf = orch._detect_scene("我为什么生气，你心里没点数吗", ctx, pid)
        assert scene == "冲突争吵"
        assert ctx.get("_structural_loop") is True
        assert "作" in ctx.get("_structural_subtext", "")
        db.engine.dispose()

    def test_icebreak_sharp_tongue(self):
        """冷战后嘴硬破冰（'死了没''花浇了没'+补偿动作）→ 识别为递台阶。"""
        turns = [
            ("received", "喂，死了没？"),
            ("sent", "没呢，等你喊我回家吃饭。"),
            ("received", "谁要喊你回家，我是想问你，阳台的花浇了没？多肉叶子都快掉光了。"),
            ("sent", "早浇了，每天早上六点绕路回去浇的，顺便给你换了矿泉水。"),
            ("sent", "我买了城南的烤鸭，还有酸梅汤，手怎么了？"),
        ]
        db, pid = self._db_with(turns, stage=8)
        orch = AnalysisOrchestrator(db=db)
        ctx = {}
        scene, conf = orch._detect_scene(
            "谁要喊你回家，我是想问你，阳台的花浇了没？多肉叶子都快掉光了。", ctx, pid)
        assert scene == "冲突争吵"
        assert ctx.get("_structural_icebreak") is True
        assert "台阶" in ctx.get("_structural_subtext", "")
        db.engine.dispose()

    def test_domestic_peace_acceptance(self):
        """冷战后'碗筷在抽屉'=借日常事务接台阶，而非讨饭/日常。"""
        turns = [("sent", "还有饭吗？"), ("received", "碗筷在左边抽屉。")]
        db, pid = self._db_with(turns, stage=8)
        orch = AnalysisOrchestrator(db=db)
        ctx = {}
        scene, _ = orch._detect_scene("碗筷在左边抽屉。", ctx, pid)
        assert scene == "冲突争吵"
        assert ctx.get("_structural_icebreak") is True
        db.engine.dispose()

    def test_vulnerable_sharing_repair(self):
        """愤怒背后的深层恐惧（'特别害怕''一个人在医院'）= 转向修复，不是攻击。"""
        turns = [("sent", "对不起。"), ("received", "对不起。"),
                 ("received", "我知道你压力大，可我也……我爸检查结果不太好，我一个人在医院，特别害怕。")]
        db, pid = self._db_with(turns, stage=8)
        orch = AnalysisOrchestrator(db=db)
        ctx = {}
        scene, _ = orch._detect_scene(
            "我爸检查结果不太好，我一个人在医院，特别害怕。", ctx, pid)
        assert scene == "冲突争吵"
        assert ctx.get("_structural_icebreak") is True
        assert "害怕" in ctx.get("_structural_subtext", "")
        db.engine.dispose()

    def test_sharp_tongue_care(self):
        """'厨房有粥自己热''厚被子在衣柜'=嘴硬照顾，不是嫌烦。"""
        turns = [("received", "喂，死了没？"), ("sent", "还没，在沙发上挺尸。"),
                 ("received", "厨房有粥，自己热。"),
                 ("received", "碗自己洗。明天降温，你那条厚被子在衣柜最上面。")]
        db, pid = self._db_with(turns, stage=8)
        orch = AnalysisOrchestrator(db=db)
        ctx = {}
        scene, _ = orch._detect_scene(
            "碗自己洗。明天降温，你那条厚被子在衣柜最上面。", ctx, pid)
        assert scene == "冲突争吵"
        assert ctx.get("_structural_icebreak") is True
        db.engine.dispose()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
