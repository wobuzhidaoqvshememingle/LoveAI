# -*- coding: utf-8 -*-
"""
R3.3-03 难度分级路由（对标 UncommonRoute 的轻量难度分层）

把一次请求切成 easy / medium / hard 三档，供网关据此挑选模型：
  - easy   寒暄/短消息/打招呼  -> 免费小模型（Flash/turbo），便宜快
  - medium 普通日常建议        -> 中端模型
  - hard   复杂侧写/六维评分/策略生成/话术推演 -> 贵模型/强推理模型

设计要点：
  1. 本轮用【规则版】（关键词 + 长度阈值），规则完全可解释、可经 config 配；
  2. 预留「感知机 / 模型分类器」接口：构造时可注入 classifier(text) -> tier；
     一旦注入且能给出有效档位，就走模型分类器，规则退为兜底；
  3. 所有判定对空文本/异常安全，绝不抛错给主流程。

config 约定（dot 风格，ConfigService 与普通 dict 都兼容）：
  llm.difficulty.easy_keywords   逗号分隔的寒暄关键词
  llm.difficulty.hard_keywords   逗号分隔的复杂任务关键词
  llm.difficulty.easy_length    长度 <= 该值且非复杂 -> easy（默认 15）
  llm.difficulty.hard_length     长度 >= 该值 -> hard（默认 120）
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 对外合法档位
EASY = "easy"
MEDIUM = "medium"
HARD = "hard"
_TIERS = (EASY, MEDIUM, HARD)

# P0 卡2：四色任务分级（与 model_routing_matrix 对齐）
# 懒导入避免顶层循环依赖；model_routing_matrix 本身不 import difficulty_router，
# 但保持懒导入更安全，未来即便反向依赖也不会炸。
def _import_task_color():
    try:
        from services.model_routing_matrix import TaskColor
        return TaskColor
    except Exception:  # noqa: BLE001 - 模块缺失时降级为 None，绝不外抛
        return None

_DEFAULT_EASY_KW = (
    "你好", "您好", "嗨", "哈喽", "在吗", "在不在", "早上好", "晚上好",
    "晚安", "睡了", "谢谢", "感谢", "再见", "拜拜", "hi", "hello",
)
_DEFAULT_HARD_KW = (
    "侧写", "六维", "评分", "打分", "策略", "话术", "话术推演", "模拟",
    "数字分身", "博弈", "深层", "潜台词", "言外之意", "分析", "预测",
    "为什么", "怎么办", "性格", "人设", "依恋", "NPD", "BPD", "出轨",
    "挽回", "冷读", "动机", "心理",
)


def _as_list(cfg_get, key: str, default: tuple) -> list[str]:
    v = cfg_get(key, None)
    if v is None:
        return list(default)
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def _as_int(cfg_get, key: str, default: int) -> int:
    try:
        return int(float(cfg_get(key, default)))
    except (TypeError, ValueError):
        return default


class DifficultyRouter:
    """规则版难度分级器；可注入模型分类器做未来升级。"""

    def __init__(self,
                 config=None,
                 classifier: Optional[Callable[[str], Optional[str]]] = None) -> None:
        # config 必须支持 .get(key, default)；None 时退为纯默认规则
        self._cfg = (config or {}).get
        self.easy_keywords = _as_list(self._cfg, "llm.difficulty.easy_keywords",
                                      _DEFAULT_EASY_KW)
        self.hard_keywords = _as_list(self._cfg, "llm.difficulty.hard_keywords",
                                      _DEFAULT_HARD_KW)
        self.easy_length = _as_int(self._cfg, "llm.difficulty.easy_length", 15)
        self.hard_length = _as_int(self._cfg, "llm.difficulty.hard_length", 120)
        # 预留：注入后用模型分类器（感知机/小模型）判档；None 用规则
        self.classifier = classifier

    # ------------------------------------------------------------------ #
    # 规则判定（可解释）
    # ------------------------------------------------------------------ #
    def _rule(self, text: str) -> tuple[str, dict]:
        """返回 (tier, 命中明细)。明细用于 explain/调试。"""
        t = (text or "").strip()
        n = len(t)
        hit_hard = [kw for kw in self.hard_keywords if kw and kw in t]
        hit_easy = [kw for kw in self.easy_keywords if kw and kw in t]

        if hit_hard or n >= self.hard_length:
            reason = "命中复杂关键词" if hit_hard else f"长文本({n}>={self.hard_length})"
            return HARD, {"tier": HARD, "length": n,
                          "matched_hard": hit_hard, "matched_easy": hit_easy,
                          "reason": reason}
        if hit_easy or n <= self.easy_length:
            reason = "命中寒暄关键词" if hit_easy else f"短文本({n}<={self.easy_length})"
            return EASY, {"tier": EASY, "length": n,
                          "matched_hard": hit_hard, "matched_easy": hit_easy,
                          "reason": reason}
        return MEDIUM, {"tier": MEDIUM, "length": n,
                        "matched_hard": hit_hard, "matched_easy": hit_easy,
                        "reason": "默认中档日常建议"}

    # ------------------------------------------------------------------ #
    # 对外
    # ------------------------------------------------------------------ #
    def classify(self, text: str) -> str:
        """返回 easy/medium/hard。任何异常都回落到规则 medium，绝不外抛。"""
        # 1) 模型分类器优先（预留接口）
        if callable(self.classifier):
            try:
                tier = self.classifier(text)
                if tier in _TIERS:
                    return tier
            except Exception as e:  # noqa: BLE001
                logger.info("难度模型分类器异常，回落规则: %s", e)
        # 2) 规则
        try:
            tier, _ = self._rule(text)
            return tier
        except Exception as e:  # noqa: BLE001
            logger.warning("难度规则判定异常，回落 medium: %s", e)
            return MEDIUM

    def explain(self, text: str) -> dict:
        """可解释判定：返回档位 + 命中规则 + 原因（供日志/前端展示）。"""
        try:
            tier, detail = self._rule(text)
        except Exception as e:  # noqa: BLE001
            return {"tier": MEDIUM, "length": 0, "matched_hard": [],
                    "matched_easy": [], "reason": f"规则异常回落:{e}"}
        detail["via_classifier"] = bool(callable(self.classifier))
        return detail

    # ------------------------------------------------------------------ #
    # P0 卡2：难度分级 -> 四色任务（GREEN/YELLOW/RED/BLACK）
    # ------------------------------------------------------------------ #
    # 映射表（写死）：easy -> GREEN；medium -> YELLOW；hard -> RED。
    # BLACK（关键决策/高风险）不由难度分级自动产生，由调用方显式标注
    # （例如：分手决策、金钱往来、是否报警等场景），避免误把普通 hard 当 BLACK。
    _TIER_TO_COLOR = {
        EASY: "GREEN",
        MEDIUM: "YELLOW",
        HARD: "RED",
    }

    def color_for(self, tier: str) -> str:
        """把 easy/medium/hard 映射为四色字符串。

        返回 "GREEN" / "YELLOW" / "RED" / "BLACK"（字符串，避免强制依赖
        model_routing_matrix.TaskColor 的 import；上层可自行转枚举）。

        未识别 tier 默认回落到 "YELLOW"（中等风险，偏保守）。
        """
        return self._TIER_TO_COLOR.get(tier, "YELLOW")

    def color_of(self, text: str) -> str:
        """便捷入口：classify(text) -> color 字符串。"""
        return self.color_for(self.classify(text))
