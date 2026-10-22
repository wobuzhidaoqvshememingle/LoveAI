# -*- coding: utf-8 -*-
"""tests/test_reply_chain_production_keys.py — 【回复】链路回归测试（R-Reply）。

设计初衷 / 教训来源
-------------------
这条缺陷此前没被测出来，正是因为 tests/test_ui_e2e_chain.py:385 用 **fixture 键**
（``data["replies"]``）构造输入，恰好绕开了真实生产键。本文件**只用生产真实键**构造
输入，并调用真实生产代码，四个出口全覆盖：

    真实生产链路：
      services/auto_strategy_pipeline._build_candidates
        → orchestration_result["candidates"]        (services/analysis_orchestrator.py)
        → data["reply_candidates"] / data["reply"]  (app/service_facade.py，本次新增透传)
        → 四个出口：
            1. 三个候选框            app/widgets/main_window.py::_populate_reply_candidates
            2. 「回复建议」卡片       app/widgets/main_window.py::_on_analysis_ready
            3. 「一键填入微信」       app/widgets/main_window.py::_on_fill_wechat
            4. 输出卡候选话术         app/widgets/analysis_output_card.py::set_result

运行：
  .venv\\Scripts\\python.exe -m pytest tests\\test_reply_chain_production_keys.py -q -p no:cacheprovider --tb=short
"""
from __future__ import annotations

import os
import sys

# 离屏 Qt：必须在创建 QApplication 之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest  # noqa: E402

from app.service_facade import collect_reply_candidates  # noqa: E402


# --------------------------------------------------------------------------- #
# 生产真实候选话术：与 auto_strategy_pipeline 的 script_library 产出同形（list[str]）
# --------------------------------------------------------------------------- #
PROD_CANDIDATES = [
    "刚忙完，正想着给你发条消息呢。",
    "你今天说话比平时短，是不是累了？",
    "有空聊聊吗？不急，你方便的时候。",
]


def _production_payload() -> dict:
    """构造与真实生产**完全同形**的分析结果（键名取自真实代码路径，不造 fixture 键）。

    键结构依据：
      * data["reply_candidates"] / data["reply"]  ← service_facade._run_analysis 本次新增透传
      * data["orchestration"]["candidates"]       ← analysis_orchestrator.orchestrate 的真实产出位置
      * data["suggested_reply"] / data["reply_suggestions"] ← 生产既有键
    """
    cands = list(PROD_CANDIDATES)
    return {
        "module": "chat_analysis",
        "data": {
            "person_id": "p1",
            "emotion": {"label": "中性"},
            "suggested_reply": cands[0],
            "reply_suggestions": [],
            "reply_candidates": cands,
            "reply": cands[0],
            "orchestration": {
                "candidates": cands,
                "strategy": {"recommended": ["稳定回应"]},
                "scene": {"name": "日常闲聊", "confidence": 0.6},
            },
        },
    }


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# =========================================================================== #
# 0. 生产链路契约：生产者 → orchestration → facade 透传
# =========================================================================== #
def test_00_pipeline_producer_returns_three_candidates():
    """真实生产者 _build_candidates 必须产出 3 条非空话术（缺陷前提：生产侧本来就有）。"""
    from services.auto_strategy_pipeline import _build_candidates

    cands = _build_candidates(
        {"recommended": ["稳定回应", "先听再说"]},
        {"recent_chats": [{"role": "other", "text": "今天好累啊"}],
         "user_intent": "升温"},
    )
    assert isinstance(cands, list)
    assert len(cands) == 3, f"生产侧应为 3 条候选，实得 {len(cands)}"
    assert all(isinstance(c, str) and c.strip() for c in cands)


def test_01_facade_helper_reads_orchestration_candidates_and_falls_back():
    """collect_reply_candidates：orchestration 候选优先，随后 LLM 建议、单条兜底。"""
    # 1. 真实主数据源：orchestration["candidates"]
    assert collect_reply_candidates({"candidates": PROD_CANDIDATES}) == PROD_CANDIDATES

    # 2. LLM 结构化建议兜底
    llm = {"reply_suggestions": [{"content": "A"}, {"content": "B"}]}
    assert collect_reply_candidates({}, llm, "") == ["A", "B"]

    # 3. 单条字符串兜底
    assert collect_reply_candidates(None, None, "就这一句") == ["就这一句"]

    # 4. 上限 3 条 + 空值过滤
    many = collect_reply_candidates({"candidates": ["1", "", "2", "3", "4"]})
    assert many == ["1", "2", "3"]

    # 5. 全空 → 空列表（不造假数据）
    assert collect_reply_candidates({}, {}, "") == []


def test_02_ui_extractor_reads_production_keys_including_nested():
    """消费侧提取器必须认生产真实键：顶层 reply_candidates 与嵌套 orchestration.candidates。"""
    from app.widgets.main_window import _extract_reply_candidates

    data = _production_payload()["data"]
    assert _extract_reply_candidates(data) == PROD_CANDIDATES

    # 只有嵌套键（修复前的真实生产形态）也必须能读到 —— 这正是原缺陷点
    nested_only = {"orchestration": {"candidates": PROD_CANDIDATES}}
    assert _extract_reply_candidates(nested_only) == PROD_CANDIDATES

    # 旧键兼容：其他调用方若仍发 replies/reply_options，不能被打破
    assert _extract_reply_candidates({"replies": PROD_CANDIDATES}) == PROD_CANDIDATES
    assert _extract_reply_candidates(
        {"reply_options": [{"text": "T1"}, {"reply": "T2"}]}) == ["T1", "T2"]
    # 旧单键兼容
    assert _extract_reply_candidates({"reply": "单条"}) == ["单条"]
    # 无数据 → 空（UI 应显示占位而非假话术）
    assert _extract_reply_candidates({}) == []


# =========================================================================== #
# 1~4. 四个出口：用真实槽函数 / 真实渲染函数吃生产 payload
# =========================================================================== #
class _WindowShim:
    """最小主窗口替身：只提供真实槽函数访问得到的属性，槽函数本身用 MainWindow 真代码。"""

    def __init__(self):
        from PySide6.QtWidgets import QLabel, QTextEdit
        from app.widgets.analysis_output_card import AnalysisOutputCard
        from app.widgets.main_window import _ANALYSIS_CARDS

        self._analysis_card_labels = {k: QLabel("（暂无数据）") for k, _ in _ANALYSIS_CARDS}
        self._reply_candidate_edits = [QTextEdit() for _ in range(3)]
        self._reply_candidates_cache = []
        self._analysis_output_card = AnalysisOutputCard()
        self._timeline_widget = None
        self._current_person_id = "p1"
        self._refreshed = []

    # _on_analysis_ready 里会调用的刷新钩子（记录即可，避免拉起整窗）
    def _refresh_task_list(self):
        self._refreshed.append("task_list")

    def _refresh_tasks_page(self):
        self._refreshed.append("tasks_page")

    def _refresh_mood_state(self):
        self._refreshed.append("mood")

    def _refresh_cycle_display(self):
        self._refreshed.append("cycle")

    def _refresh_quick_panel(self):
        self._refreshed.append("quick")

    def _get_current_person_id(self):
        return "p1"

    # 真实槽函数 _on_analysis_ready 会调用它；这里仍走 MainWindow 的真实实现
    def _populate_reply_candidates(self, data: dict):
        from app.widgets.main_window import MainWindow
        return MainWindow._populate_reply_candidates(self, data)


def test_03_real_slot_fills_three_boxes_reply_card_and_output_card(qt_app):
    """真实 _on_analysis_ready 吃生产 payload → 出口 1/2/4 同时出 3 句真话术。"""
    from app.widgets.main_window import MainWindow

    shim = _WindowShim()
    MainWindow._on_analysis_ready(shim, _production_payload())

    # 出口 1：三个候选框
    boxes = [b.toPlainText() for b in shim._reply_candidate_edits]
    assert boxes == PROD_CANDIDATES, f"三个候选框内容不对: {boxes}"

    # 出口 2：8 卡片里的「回复建议」卡片（不再是「（暂无数据）」）
    card = shim._analysis_card_labels["reply"].text()
    assert card != "（暂无数据）", "「回复建议」卡片恒空 —— 缺陷未修复"
    for c in PROD_CANDIDATES:
        assert c in card, f"「回复建议」卡片缺少候选: {c}"

    # 出口 4：单轮分析输出卡的候选话术
    out_card = shim._analysis_output_card._labels["candidates"].text()
    assert out_card != "—"
    for i, c in enumerate(PROD_CANDIDATES):
        assert f"{i + 1}. {c}" in out_card, f"输出卡缺少编号候选 {i + 1}"

    # 缓存与候选框同源（供一键填入取用）
    assert shim._reply_candidates_cache == PROD_CANDIDATES

    # 其余卡片仍然照旧渲染（不因本次改动受影响）
    assert shim._analysis_card_labels["emotion"].text() != ""
    assert shim._refreshed and "task_list" in shim._refreshed


def test_04_reply_card_prefers_real_candidates_over_single_reply():
    """卡片取值函数：有候选→逐条列出；无候选→回落 data['reply']（旧行为不变）。"""
    from app.widgets.main_window import _analysis_card_value

    data = _production_payload()["data"]
    val = _analysis_card_value("reply", data, PROD_CANDIDATES)
    assert val.split("\n") == PROD_CANDIDATES

    # 无候选 → 回落单条 reply
    assert _analysis_card_value("reply", {"reply": "只有一条"}, []) == "只有一条"
    # 什么都没有 → 空串（上层渲染成「（暂无数据）」）
    assert _analysis_card_value("reply", {}, []) == ""

    # 非 reply 卡片维持原逻辑（dict/list 走 json 序列化）
    other = _analysis_card_value("emotion", {"emotion": {"label": "开心"}}, [])
    assert "开心" in other


class _ComboStub:
    def currentData(self):
        return "p1"


class _WechatStub:
    def __init__(self):
        self.filled = []

    def fill_text(self, text):
        self.filled.append(text)
        return True


class _FacadeStub:
    def __init__(self, wechat):
        self.services = {"wechat": wechat}


class _FillShim:
    """「一键填入微信」所需的最小窗口替身。"""

    def __init__(self, wechat, cands, card_text="（暂无数据）"):
        from PySide6.QtWidgets import QLabel
        self._person_combo = _ComboStub()
        self._current_person_id = "p1"
        self._facade = _FacadeStub(wechat)
        self._reply_candidates_cache = list(cands)
        self._reply_candidate_edits = []
        self._analysis_card_labels = {"reply": QLabel(card_text)}


def test_05_fill_wechat_uses_real_candidates_not_card_text(qt_app, monkeypatch):
    """出口 3：一键填入必须取真实候选数据（不再从标签文本剥前缀）。"""
    from app.widgets import main_window as mw

    calls = []

    class _MB:
        @staticmethod
        def information(parent, title, text):
            calls.append(("information", title, text))

        @staticmethod
        def warning(parent, title, text):
            calls.append(("warning", title, text))

    monkeypatch.setattr(mw, "QMessageBox", _MB)

    wechat = _WechatStub()
    # 卡片文本故意写空占位，模拟修复前「卡片恒空」的现场
    shim = _FillShim(wechat, PROD_CANDIDATES, card_text="（暂无数据）")
    mw.MainWindow._on_fill_wechat(shim)

    assert wechat.filled == [PROD_CANDIDATES[0]], \
        f"一键填入未取真实候选: {wechat.filled}"
    assert not any("暂无建议回复" in c[2] for c in calls), \
        "卡片为空时仍报「暂无建议回复」—— 缺陷未修复"


def test_06_fill_wechat_without_candidates_gives_clear_hint(qt_app, monkeypatch):
    """无候选时给明确提示，不得填入空串、不得抛栈。"""
    from app.widgets import main_window as mw

    calls = []

    class _MB:
        @staticmethod
        def information(parent, title, text):
            calls.append(("information", title, text))

        @staticmethod
        def warning(parent, title, text):
            calls.append(("warning", title, text))

    monkeypatch.setattr(mw, "QMessageBox", _MB)

    wechat = _WechatStub()
    shim = _FillShim(wechat, [], card_text="（暂无数据）")
    mw.MainWindow._on_fill_wechat(shim)

    assert wechat.filled == [], "无候选时不应调用微信填入"
    assert any("暂无建议回复" in c[2] for c in calls), f"缺少明确提示: {calls}"


def test_07_no_candidates_clears_boxes_and_never_fakes(qt_app):
    """无候选时三个候选框清空、卡片回落占位（不造假数据）。"""
    from app.widgets.main_window import MainWindow

    shim = _WindowShim()
    MainWindow._on_analysis_ready(shim, {"module": "chat_analysis", "data": {}})

    assert [b.toPlainText() for b in shim._reply_candidate_edits] == ["", "", ""]
    assert shim._analysis_card_labels["reply"].text() == "（暂无数据）"
    assert shim._reply_candidates_cache == []
