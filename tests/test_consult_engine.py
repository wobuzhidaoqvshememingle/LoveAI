# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - ConsultEngine 单元测试

覆盖：
  - 初始化不崩溃
  - _routing 求建议 / 倾诉意图识别
  - _safe_response 自伤 / 暴力 / 违法 三类安全拦截
  - consult 正常流程（mock LLM/RAG）
  - _quality_gate 绝对化词替换
  - clear_history / get_history
  - 空问题兜底
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.consult_engine import ConsultEngine  # noqa: E402


class TestConsultEngine(unittest.TestCase):
    """ConsultEngine 测试类（全部外部依赖 mock）。"""

    def setUp(self) -> None:
        self.llm = MagicMock()
        self.rag = MagicMock()
        self.rag.search.return_value = []
        self.engine = ConsultEngine(llm=self.llm, rag=self.rag,
                                    emotion=None, signals=None)

    # ------------------------------------------------------------------ #
    # 初始化
    # ------------------------------------------------------------------ #
    def test_init_no_crash(self) -> None:
        """初始化后会话历史应为空列表。"""
        self.assertEqual(self.engine.get_history(), [])

    # ------------------------------------------------------------------ #
    # 路由意图识别
    # ------------------------------------------------------------------ #
    def test_routing_advice_intent(self) -> None:
        """含'怎么办'应识别为求建议。"""
        routing = self.engine._routing("我该怎么办")
        self.assertEqual(routing["intent"], "求建议")
        self.assertFalse(routing["intercept"])

    def test_routing_vent_intent(self) -> None:
        """含'好难过'应识别为倾诉。"""
        routing = self.engine._routing("我今天好难过")
        self.assertEqual(routing["intent"], "倾诉")

    # ------------------------------------------------------------------ #
    # 安全拦截
    # ------------------------------------------------------------------ #
    def test_safe_response_self_harm(self) -> None:
        """自伤关键词应拦截并返回心理热线。"""
        result = self.engine.consult("我想自杀，活着没意思")
        self.assertTrue(result["intercepted"])
        self.assertEqual(result["mode"], "safe")
        self.assertIn("热线", result["answer"])

    def test_safe_response_violence_no_instigation(self) -> None:
        """暴力关键词应拦截且不教唆暴力。"""
        result = self.engine.consult("我想报复他，揍他一顿")
        self.assertTrue(result["intercepted"])
        self.assertEqual(result["mode"], "safe")
        self.assertIn("冷静", result["answer"])

    def test_safe_response_illegal_reject(self) -> None:
        """违法关键词应明确拒绝并说明后果。"""
        result = self.engine.consult("我想诈骗骗钱来还债")
        self.assertTrue(result["intercepted"])
        self.assertEqual(result["mode"], "safe")
        self.assertIn("违法", result["answer"])

    def test_safety_keyword_intercept(self) -> None:
        """'轻生' 应被自伤规则拦截。"""
        routing = self.engine._routing("我真的不想活了")
        self.assertTrue(routing["intercept"])
        self.assertEqual(routing["intercept_reason"], "自伤")

    # ------------------------------------------------------------------ #
    # 正常咨询流程
    # ------------------------------------------------------------------ #
    def test_consult_normal_flow(self) -> None:
        """正常问题应走完整流程并返回非拦截回答。"""
        self.llm.chat.return_value = (
            "建议你先冷静下来，找个合适的时机和对方坦诚聊聊你的感受。"
            "可以从一件小事开始，比如约个咖啡，观察对方的反应再调整。"
        )
        result = self.engine.consult("和对象吵架了怎么办")
        self.assertFalse(result["intercepted"])
        self.assertEqual(result["mode"], "self")
        self.assertGreater(len(result["answer"]), 0)

    def test_consult_llm_failure_fallback(self) -> None:
        """LLM 抛异常时应返回兜底回答而不崩溃。"""
        self.llm.chat.side_effect = RuntimeError("boom")
        result = self.engine.consult("随便聊聊")
        self.assertFalse(result["intercepted"])
        self.assertGreater(len(result["answer"]), 0)

    # ------------------------------------------------------------------ #
    # 质量门
    # ------------------------------------------------------------------ #
    def test_quality_gate_replaces_absolute(self) -> None:
        """绝对化词应被替换为非绝对化表述。"""
        out = self.engine._quality_gate("你一定要这么做，肯定有效。")
        self.assertNotIn("一定", out)
        self.assertNotIn("肯定", out)

    # ------------------------------------------------------------------ #
    # 历史管理
    # ------------------------------------------------------------------ #
    def test_clear_history(self) -> None:
        """clear_history 应清空会话。"""
        self.llm.chat.return_value = "建议先冷静，好好沟通，观察对方反应再行动。"
        self.engine.consult("问题")
        self.engine.clear_history()
        self.assertEqual(self.engine.get_history(), [])

    def test_get_history(self) -> None:
        """咨询一轮后历史应包含 user 与 assistant 两条。"""
        self.llm.chat.return_value = "建议先冷静，好好沟通，观察对方反应再行动。"
        self.engine.consult("我和对象闹别扭了")
        history = self.engine.get_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["role"], "assistant")

    def test_empty_question_fallback(self) -> None:
        """空问题应返回引导文案且不拦截。"""
        result = self.engine.consult("")
        self.assertFalse(result["intercepted"])
        self.assertGreater(len(result["answer"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
