# -*- coding: utf-8 -*-
"""残留项修复的定向回归测试（Predict + refine_script）。

覆盖两处修复，全部采用**生产真实数据结构**（不构造"><"式 fixture 绕开链路）：

【预测】链路
  背景第一因：AnalysisOrchestrator.__init__ 从不设置 self.digital_twin，而
  analysis_orchestrator.py 3l 步直接读它 → 恒 AttributeError 被吞 →
  modules_called 恒 "failed"，orchestration["prediction"] 只能落常量兜底；
  app/service_facade.py::_get_or_create_twin 的 DigitalTwin(self._db, self._llm)
  与 __init__(person_id, db, llm, rag, ...) 位置不符 → twin 恒 None。
  修复：编排器接受可选 digital_twin 注入；facade 修正构造调用并支持可调用工厂；
       facade 10.7.2 把权威 twin_reaction 回填到 orchestration["prediction"]。

【refine_script】尾句
  背景第一因：auto_strategy_pipeline._build_refine_context 把对方整句原话截
  20 字当 last_topic，script_library.refine_script 又按名词短语拼尾句。
  修复：refine_script 增加话题守卫 _is_topic_phrase，不合格就不拼。

说明：predict_reaction 的**真实 LLM 文本质量**需联网，无法在离线测试中断言；
本文件断言的是「链路可达 + 字段不再是常量兜底」。
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 生产真实模板与真实对方原话（取自实测 run1/live evidence 的同形数据）
PROD_TEMPLATE = "说句肉麻的：有你在，我觉得日子没那么难了。"
PROD_OTHER_MSG = "你今天怎么都不找我，是不是不在乎我了"   # 20 字整句原话


# --------------------------------------------------------------------------- #
# 1) 编排器：悬空属性已消除，且可注入
# --------------------------------------------------------------------------- #
def test_orchestrator_digital_twin_attribute_exists_and_defaults_none():
    """修复前 hasattr(orch,'digital_twin') == False；修复后必须存在且默认 None。"""
    from services.analysis_orchestrator import AnalysisOrchestrator

    orch = AnalysisOrchestrator()
    assert hasattr(orch, "digital_twin"), "digital_twin 必须被初始化（原为悬空属性）"
    assert orch.digital_twin is None, "未注入时必须为 None（不产生额外调用）"

    # 复刻 3l 步守卫表达式：不再抛 AttributeError
    assert (orch.digital_twin is not None) is False


def test_orchestrator_accepts_injected_twin_and_marks_module_ok():
    """注入替身后，真实 orchestrate() 的 3l 步必须记为 ok 且 prediction 不再兜底。"""
    from services.analysis_orchestrator import AnalysisOrchestrator

    MARKER = {
        "emotion": "warm",
        "inner_thought": "他主动找我，我其实挺开心的",
        "possible_replies": ["在呢，刚忙完", "你怎么才找我"],
        "action": "积极回应",
        "confidence": 0.62,
    }
    calls = []

    class _StubTwin:
        def predict_reaction(self, user_action, recent_chats, affection,
                             context_hint="", snapshot=None):
            calls.append({"user_action": user_action, "context_hint": context_hint})
            return dict(MARKER)

    orch = AnalysisOrchestrator(db=None, config=None, digital_twin=_StubTwin())
    result = orch.orchestrate(person_id="p_resid", chat_text="你今天怎么都不找我",
                              context={})
    assert isinstance(result, dict)

    mods = result.get("modules_called") or {}
    assert mods.get("digital_twin.predict_reaction") == "ok", (
        f"3l 步应记 ok，实际 {mods.get('digital_twin.predict_reaction')!r}（修复前恒 failed）")
    assert calls, "注入的 twin.predict_reaction 必须被真正调用"
    assert result.get("prediction") == MARKER, "prediction 必须来自真实 predict_reaction"


# --------------------------------------------------------------------------- #
# 2) facade：twin 构造调用点（签名修正 + 可调用工厂）
# --------------------------------------------------------------------------- #
class _FacadeShim:
    """最小替身：只提供 _get_or_create_twin 真正用到的属性。"""

    def __init__(self, db=None, llm=None, services=None):
        self._db = db
        self._llm = llm
        self.services = services if services is not None else {}


def _fetch_twin(shim, person_id="p1"):
    from app.service_facade import ServiceFacade
    return ServiceFacade._get_or_create_twin(shim, person_id)


def test_facade_twin_direct_creation_uses_correct_kwargs(monkeypatch):
    """修复前 DigitalTwin(self._db, self._llm) 位置不符 → TypeError → None。"""
    import services.digital_twin as dt_mod

    seen = {}

    class _Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(dt_mod, "DigitalTwin", _Recorder, raising=True)
    shim = _FacadeShim(db="DB", llm="LLM", services={"rag": "RAG"})
    twin = _fetch_twin(shim, "p42")

    assert isinstance(twin, _Recorder), "直接创建分支必须成功构造（修复前返回 None）"
    assert seen.get("person_id") == "p42"
    assert seen.get("db") == "DB"
    assert seen.get("llm") == "LLM"
    assert seen.get("rag") == "RAG"


def test_facade_twin_prefers_callable_factory():
    """应用注册的是工厂函数（application.py:783），必须被直接调用。"""
    shim = _FacadeShim(services={"digital_twin_factory": lambda pid: {"twin_of": pid}})
    assert _fetch_twin(shim, "p9") == {"twin_of": "p9"}


# --------------------------------------------------------------------------- #
# 3) refine_script 话题守卫：整句原话不拼尾句 / 名词短语仍然拼
# --------------------------------------------------------------------------- #
def test_topic_guard_rejects_full_sentence_and_keeps_noun_phrase():
    from services.script_library import _is_topic_phrase

    assert _is_topic_phrase("加班") is True
    assert _is_topic_phrase("周末去爬山") is True
    assert _is_topic_phrase(PROD_OTHER_MSG) is False          # 20 字，含标点与疑问词
    assert _is_topic_phrase("加班，很累") is False             # 含标点
    assert _is_topic_phrase("你是不是不开心") is False          # 含疑问结构
    assert _is_topic_phrase("") is False
    assert _is_topic_phrase("这是一个特别长的所谓话题因为它超过了十二个字") is False


def test_refine_script_skips_tail_for_production_full_sentence():
    """生产真实形态（last_topic 来自 _build_refine_context）不再拼破碎尾句。"""
    from services.script_library import refine_script
    from services.auto_strategy_pipeline import _build_refine_context

    snap = {"recent_chats": [{"role": "other", "text": PROD_OTHER_MSG}],
            "nickname": "", "pet_name": "", "mood": ""}
    ctx = _build_refine_context(snap)
    assert ctx["last_topic"] == PROD_OTHER_MSG, "前提：真实数据源就是整句原话"

    out = refine_script(PROD_TEMPLATE, ctx)
    assert "那事后来呢" not in out, f"不应再拼尾句，实际：{out}"
    assert out == PROD_TEMPLATE, f"应等于原模板，实际：{out}"


def test_refine_script_skips_tail_in_low_mood_branch_too():
    from services.script_library import refine_script

    out = refine_script(PROD_TEMPLATE, {"last_topic": PROD_OTHER_MSG, "mood": "低落"})
    assert "的事，要是想说说我在" not in out, f"低落分支同样不应拼，实际：{out}"


def test_refine_script_still_stitches_noun_phrase_topic():
    """对照组：合格名词话题必须照旧拼接（防过度修正）。"""
    from services.script_library import refine_script

    out = refine_script(PROD_TEMPLATE, {"last_topic": "加班", "mood": ""})
    assert out == PROD_TEMPLATE.rstrip("。！!?？") + "。对了，加班那事后来呢？", out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--tb=short"]))
