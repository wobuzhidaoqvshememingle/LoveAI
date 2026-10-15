# -*- coding: utf-8 -*-
"""24.1.1 11级闯关解锁逻辑单测（services/level_clearing.py）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.level_clearing import level_gate, level_clear_status  # noqa: E402
from services.batch7_group1 import LEVELS11  # noqa: E402


def test_level_gate_bounds_and_content():
    # 越界钳制到 1..11
    assert level_gate(0)["level"] == 1
    assert level_gate(99)["level"] == 11
    g = level_gate(5)
    assert g["milestone"] and g["reward"]


def test_display_reuses_eleven_levels():
    r = level_clear_status(45, [])
    # 好感度45 → idx=5 (45//10=4 → idx=5)，名称应落在11级段位内
    assert r["level_name"] in LEVELS11
    assert 1 <= r["level_idx"] <= 11


def test_no_milestone_not_cleared_even_if_affection_high():
    # 好感度够高但没里程碑：cleared 应为空，can_advance=False
    r = level_clear_status(55, [])
    assert r["cleared_levels"] == []
    assert r["can_advance"] is False
    assert r["current_milestone_done"] is False


def test_milestone_clears_level_and_unlocks_next():
    # 完成第1关里程碑 + 好感度到第2关段位 → 第1关闯过，且可进入下一关
    done = {"完成首次自然开场"}
    r = level_clear_status(15, done)  # 15//10=1 → idx=2
    assert 1 in r["cleared_levels"]
    # 当前关=第2关，其里程碑未做 → can_advance=False
    assert r["current_milestone_done"] is False


def test_can_advance_when_current_milestone_done():
    done = {"完成首次自然开场", "连续3天主动且有回应"}
    r = level_clear_status(25, done)  # 25//10=2 → idx=3
    # 当前第3关里程碑未做 → can_advance False
    assert r["can_advance"] is False
    # 再把第3关里程碑也完成
    done.add("完成一次轻松话题深聊")
    r2 = level_clear_status(25, done)
    assert r2["current_milestone_done"] is True
    assert r2["can_advance"] is True
    assert r2["next_unlock"]["level"] == 4


def test_max_level_has_no_next_unlock():
    # 好感度100 + 全部11个里程碑
    all_done = {level_gate(i)["milestone"] for i in range(1, 12)}
    r = level_clear_status(100, all_done)
    assert r["level_idx"] == 11
    assert r["next_unlock"] is None
    assert r["cleared_levels"] == list(range(1, 12))
