# -*- coding: utf-8 -*-
"""HTN 分层任务 + 动态重规划 + 回复建议结构 pytest 测试。"""
import os, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


class TestHTNPlanner:
    """22.2.2 HTN 分层任务测试。"""

    def test_decompose_warming(self):
        """高层目标'升温'分解为任务网络（compound→primitive）。"""
        from services.task_engine import HTNPlanner
        planner = HTNPlanner()
        network = planner.decompose("test_person", "升温", stage=2)
        assert len(network) > 0, "应分解出 primitive 任务"
        for t in network:
            assert t["type"] == "primitive", f"任务类型应为 primitive，实际 {t['type']}"
            assert "prerequisite" in t, "任务应含 prerequisite 字段"
            assert t["parent_goal"] == "升温", f"parent_goal 应为升温"

    def test_decompose_withdraw(self):
        """高层目标'后撤'分解为任务网络。"""
        from services.task_engine import HTNPlanner
        planner = HTNPlanner()
        network = planner.decompose("test_person", "后撤", stage=3)
        assert len(network) > 0, "应分解出 primitive 任务"
        for t in network:
            assert t["parent_goal"] == "后撤"

    def test_get_task_network(self):
        """获取任务网络（compound→primitive 结构）。"""
        from services.task_engine import HTNPlanner
        planner = HTNPlanner()
        planner.decompose("test_person", "升温", stage=2)
        network = planner.get_task_network("test_person")
        assert network["high_level_goal"] == "升温"
        assert len(network["tasks"]) > 0


class TestDynamicReplanner:
    """22.2.3 动态重规划测试。"""

    def test_record_feedback(self):
        """记录任务反馈（完成/失败/好感度变化）。"""
        from services.task_engine import DynamicReplanner
        replanner = DynamicReplanner()
        feedback = replanner.record_feedback("test_person", "task_1", "completed", affection_change=5)
        assert feedback["task_id"] == "task_1"
        assert feedback["status"] == "completed"
        assert feedback["affection_change"] == 5

    def test_replan_high_fail_rate(self):
        """高失败率（>50%）触发重规划建议。"""
        from services.task_engine import DynamicReplanner
        replanner = DynamicReplanner()
        replanner.record_feedback("test_person", "task_1", "failed", affection_change=-2)
        replanner.record_feedback("test_person", "task_2", "failed", affection_change=-1)
        replanner.record_feedback("test_person", "task_3", "completed", affection_change=5)
        result = replanner.replan("test_person")
        assert result["action"] == "replan"
        assert result["fail_rate"] > 0.5
        assert len(result["suggestions"]) > 0

    def test_replan_no_feedback(self):
        """无反馈历史不触发重规划。"""
        from services.task_engine import DynamicReplanner
        replanner = DynamicReplanner()
        result = replanner.replan("test_person_empty")
        assert result["action"] == "no_change"


class TestReplySuggestionBuilder:
    """4.3.1 回复建议详细结构测试。"""

    def test_build_suggestions_primary_mark(self):
        """回复建议带首选标记（is_primary）。"""
        from services.task_engine import ReplySuggestionBuilder
        builder = ReplySuggestionBuilder()
        suggestions = builder.build_suggestions("test_person", context="测试上下文", direction="升温")
        assert len(suggestions) >= 2, "应至少 2 条建议（首选+备选）"
        primary = [s for s in suggestions if s["is_primary"]]
        assert len(primary) == 1, "应有 1 条首选"

    def test_build_suggestions_ammo_structure(self):
        """回复建议含弹药库结构（reply_text/rationale/timing）。"""
        from services.task_engine import ReplySuggestionBuilder
        builder = ReplySuggestionBuilder()
        suggestions = builder.build_suggestions("test_person", context="测试上下文", direction="维稳")
        for s in suggestions:
            assert "reply_text" in s, "建议应含 reply_text"
            assert "rationale" in s, "建议应含 rationale"
            assert "timing" in s, "建议应含 timing"

    def test_get_suggestions(self):
        """获取回复建议（弹药库）。"""
        from services.task_engine import ReplySuggestionBuilder
        builder = ReplySuggestionBuilder()
        builder.build_suggestions("test_person", context="测试", direction="后撤")
        suggestions = builder.get_suggestions("test_person")
        assert len(suggestions) > 0
