# -*- coding: utf-8 -*-
"""24.1.1 11级闯关系统：在好感度段位映射（batch7_group1.stage_to_11）之上，
补充“关卡解锁条件 + 里程碑门 + 通关奖励 + 通关状态”的纯函数逻辑。

约束：
- 纯 Python 服务层，不 import PySide6，不调用外部 LLM。
- 复用 LEVELS11 段位名，不修改 batch7_group1.py（他人本周期可能改动）。
- 只读不写正式库：本模块不持有 db，全部输入为纯参数。
"""
from __future__ import annotations
import logging

from services.batch7_group1 import LEVELS11, stage_to_11

logger = logging.getLogger(__name__)

# 每一关的解锁里程碑（达成对应关键事件才算“闯过”本关，否则仅好感度达标）。
# key=关卡序号(1..11)，value=需要完成的里程碑标签 + 通关奖励文案。
_LEVEL_GATES: dict[int, dict] = {
    1:  {"milestone": "完成首次自然开场",       "reward": "解锁：基础开场白库"},
    2:  {"milestone": "连续3天主动且有回应",     "reward": "解锁：关系温度计周视图"},
    3:  {"milestone": "完成一次轻松话题深聊",     "reward": "解锁：暧昧话术包"},
    4:  {"milestone": "互换至少一个私人小习惯",   "reward": "解锁：默契度画像"},
    5:  {"milestone": "通过一次暧昧试探不冷场",   "reward": "解锁：升温节奏建议"},
    6:  {"milestone": "完成一次线上/线下确认",   "reward": "解锁：对象专属任务链"},
    7:  {"milestone": "共同规划一件未来小事",     "reward": "解锁：承诺追踪器"},
    8:  {"milestone": "度过一次小摩擦并修复",     "reward": "解锁：危机预警面板"},
    9:  {"milestone": "连续稳定互动满30天",       "reward": "解锁：长期记忆回顾"},
    10: {"milestone": "见过双方至少一位挚友",     "reward": "解锁：社交圈融合建议"},
    11: {"milestone": "完成终身承诺确认",         "reward": "解锁：终身伴侣纪念档案"},
}


def level_gate(level_idx: int) -> dict:
    """返回某一关(1..11)的解锁里程碑与奖励；越界钳制到合法区间。"""
    idx = min(11, max(1, level_idx))
    return {"level": idx, **_LEVEL_GATES[idx]}


def level_clear_status(affection: float, milestones_done: set | list) -> dict:
    """综合好感度与已完成里程碑，给出闯关状态。

    Args:
        affection: 当前好感度 0..100。
        milestones_done: 已完成的里程碑标签集合（命中 _LEVEL_GATES.milestone 即算闯过）。

    Returns:
        {
          "level_name","level_idx","progress",          # 段位展示（复用 stage_to_11）
          "cleared_levels":[...],                       # 已真正闯过的关卡(序号)
          "current_milestone", "current_reward",        # 当前关要求/奖励
          "next_unlock": {level, milestone, reward}|None,  # 下一关解锁条件
          "can_advance": bool,                          # 当前关是否已可进入下一关
        }
    """
    base = stage_to_11(affection)
    cur_idx = base["level_idx"]  # 1..11
    done = set(milestones_done or [])

    # 已闯过：好感度段位达到 且 该关里程碑已完成。
    cleared = []
    for lv in range(1, cur_idx + 1):
        gate = _LEVEL_GATES[lv]
        if gate["milestone"] in done:
            cleared.append(lv)

    cur_gate = _LEVEL_GATES[cur_idx]
    milestone_done = cur_gate["milestone"] in done
    can_advance = milestone_done and cur_idx < 11

    next_unlock = None
    if cur_idx < 11:
        nxt = _LEVEL_GATES[cur_idx + 1]
        next_unlock = {"level": cur_idx + 1, "milestone": nxt["milestone"],
                       "reward": nxt["reward"]}

    return {
        "level_name": base["level_name"],
        "level_idx": cur_idx,
        "next_name": base.get("next"),
        "progress": base["progress"],
        "cleared_levels": cleared,
        "current_milestone": cur_gate["milestone"],
        "current_reward": cur_gate["reward"],
        "current_milestone_done": milestone_done,
        "can_advance": can_advance,
        "next_unlock": next_unlock,
    }
