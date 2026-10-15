# -*- coding: utf-8 -*-
"""模式库（Pattern Library）——蛋卷真实事件归纳的案例库。

职责：
- 把蛋卷真实事件归纳成（行为模式→心理归因→应对）案例；
- 提供 match(context)→{case_id, pattern, attribution, suggested_response, similarity}；
- 规则兜底：无匹配时返回通用应对。

案例数据来源：蛋卷真实聊天记录（data/loveai.db，只读提取）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Case:
    """案例：行为模式→心理归因→应对。"""
    case_id: str
    pattern: str           # 行为模式描述
    attribution: str       # 心理归因
    suggested_response: str  # 建议应对
    keywords: list[str] = field(default_factory=list)  # 匹配关键词
    example: str = ""      # 真实事件示例


# 蛋卷真实事件归纳的案例库
PATTERN_CASES: list[Case] = [
    Case(
        case_id="case_001",
        pattern="深夜情绪崩溃（凌晨发长文/哭/自责）",
        attribution="压力过载，非不爱——白天压抑+深夜放大→情绪决堤",
        suggested_response="承接情绪+不评判+给安全感+次日温和复盘",
        keywords=["深夜", "凌晨", "崩溃", "哭", "自责", "长文"],
        example="06-12 22:47 服软后深夜情绪波动"
    ),
    Case(
        case_id="case_002",
        pattern="要钱/借钱后回复延迟翻倍",
        attribution="利益动机试探——测试对方底线与可索取空间",
        suggested_response="温和边界+不拒绝但不无条件满足+观察后续",
        keywords=["借钱", "要钱", "延迟", "不回"],
        example="蛋卷历史事件中索取后延迟案例"
    ),
    Case(
        case_id="case_003",
        pattern="服软后立即反弹（道歉后马上又狠话）",
        attribution="表演性服软——情绪性妥协，非真认知改变",
        suggested_response="不接反弹+稳定承接+等待真反思信号",
        keywords=["服软", "道歉", "反弹", "狠话", "又"],
        example="06-12 服软 vs 07-03 狠话（跨月反弹）"
    ),
    Case(
        case_id="case_004",
        pattern="连续冷战/不回消息超过3天",
        attribution="回避型自我保护——冲突后退缩充电，非主动惩罚",
        suggested_response="不追问+给空间+轻量留灯+等待主动回归",
        keywords=["冷战", "不回", "三天", "消失"],
        example="蛋卷历史事件中冷战案例"
    ),
    Case(
        case_id="case_005",
        pattern="突然高频热情+未来规划（见家长/承诺未来）",
        attribution="目的达成前表演——确认关系前过度投入",
        suggested_response="温和接住+不立即承诺+观察持续性",
        keywords=["突然", "热情", "未来", "承诺", "见家长"],
        example="蛋卷历史事件中承诺案例"
    ),
    Case(
        case_id="case_006",
        pattern="情绪压力过载→明确要求空间（放过我/爱压力太大/缓缓）",
        attribution="关系压力过载——非不爱，是被逼近临界点需要降压喘息",
        suggested_response="给空间后撤留灯+不追问不施压+等她回归",
        keywords=["放过我", "压力太大", "给空间", "缓缓", "喘不过气", "压力过载"],
        example="05-14 03:13 放过我一周/03:16 爱压力太大"
    ),
]


class PatternLibrary:
    """模式库：案例匹配 + 建议应对。"""

    def __init__(self, cases: list[Case] | None = None):
        self.cases = cases or PATTERN_CASES

    def match(self, context: str) -> dict:
        """匹配案例库，返回最相似案例。

        Args:
            context: 当前上下文（消息内容/事件描述）

        Returns:
            dict: {case_id, pattern, attribution, suggested_response, similarity, matched}
        """
        if not context:
            return {
                "matched": False,
                "case_id": None,
                "pattern": None,
                "attribution": None,
                "suggested_response": "通用应对：温和承接+观察+不极端",
                "similarity": 0.0,
            }

        best_case: Optional[Case] = None
        best_score: float = 0.0

        for case in self.cases:
            score = 0.0
            for kw in case.keywords:
                if kw in context:
                    score += 1.0
            # 归一化：命中关键词数 / 总关键词数
            if case.keywords:
                score = score / len(case.keywords)
            if score > best_score:
                best_score = score
                best_case = case

        if best_case and best_score > 0:
            return {
                "matched": True,
                "case_id": best_case.case_id,
                "pattern": best_case.pattern,
                "attribution": best_case.attribution,
                "suggested_response": best_case.suggested_response,
                "similarity": round(best_score, 3),
            }
        else:
            return {
                "matched": False,
                "case_id": None,
                "pattern": None,
                "attribution": None,
                "suggested_response": "通用应对：温和承接+观察+不极端",
                "similarity": 0.0,
            }

    def list_cases(self) -> list[dict]:
        """列出所有案例（调试用）。"""
        return [
            {
                "case_id": c.case_id,
                "pattern": c.pattern,
                "attribution": c.attribution,
                "suggested_response": c.suggested_response,
                "example": c.example,
            }
            for c in self.cases
        ]
