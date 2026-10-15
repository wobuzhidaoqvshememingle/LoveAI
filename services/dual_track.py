# -*- coding: utf-8 -*-
"""
LoveAI v3.3 - R3.3-78 双轨情感指标：路线分流纯函数模块。

设计约束（冻结契约，供 task_engine / boost_glide_planner 等并行模块 import）：
  - 本模块只依赖标准库 / typing，**禁止 import services 包内任何模块**，避免循环导入。
  - resolve_route / main_metric / extend_reward / ROUTE_WEIGHTS 签名即冻结接口，
    后续不得改动参数名与返回结构，只能向后追加。

两条路线：
  - "counter"（反制线）：对方有操纵/对抗/NPD 倾向，需要反制/驯服，主指标=忠诚度。
  - "guide"（引导线）：正常恋爱经营，主指标=好感度。
"""
from typing import Any, Optional

# 七维奖励向量键（boost_glide_planner 目标函数按路线加权使用）。
REWARD_KEYS: tuple[str, ...] = (
    "affection",     # 好感度（引导线主指标）
    "loyalty",       # 忠诚度/听话程度（反制线主指标）
    "mainline",      # 主线进度
    "trust",         # 信任
    "reciprocity",   # 互惠/对等投入
    "emotion_safety",# 情绪安全
    "growth",        # 共同成长
)

# 两套路线权重（和值约等于 1，仅用于把一个标量奖励摊到七维）。
# counter 线：忠诚权重大头（0.40），好感降权（0.15）——反制线奖励锚定听话度。
# guide  线：好感权重大头（0.40），忠诚降权（0.10）——引导线奖励锚定好感。
# 两套权重刻意拉开，保证 extend_reward 按路线产出明显不同的七维向量。
ROUTE_WEIGHTS: dict[str, dict[str, float]] = {
    "counter": {
        "affection": 0.15,
        "loyalty": 0.40,
        "mainline": 0.15,
        "trust": 0.10,
        "reciprocity": 0.10,
        "emotion_safety": 0.05,
        "growth": 0.05,
    },
    "guide": {
        "affection": 0.40,
        "loyalty": 0.10,
        "mainline": 0.15,
        "trust": 0.15,
        "reciprocity": 0.10,
        "emotion_safety": 0.05,
        "growth": 0.05,
    },
}

# 判定为反制场景时，manipulation_output 里需要看到的"反制确实被触发"信号。
# 与 services.manipulation_coach.build_counter_manipulation 的返回结构对齐：
#   triggered=True 且 manipulation_patterns 非空（检出了煤气灯/金钱控制/辱骂/单向投入）。
_COUNTER_TRUTHY_GOALS = {"反制驯服", "立刻止损"}


def _persona_flags_counter(persona: Optional[dict]) -> bool:
    """读 persona 的心理学标签，判定是否带 NPD/操纵倾向。

    结构对齐 manipulation_coach._detect_patterns：
      psychology.dark_triad_tendencies = {"操纵": True, "辱骂贬低": True, ...}
      psychology.defensive_communication = ["煤气灯贬低", "贬低感受", ...]
    """
    if not isinstance(persona, dict):
        return False
    psych = persona.get("psychology")
    if not isinstance(psych, dict):
        return False
    dark = psych.get("dark_triad_tendencies")
    if isinstance(dark, dict):
        if dark.get("操纵") or dark.get("辱骂贬低"):
            return True
    defensive = psych.get("defensive_communication")
    if isinstance(defensive, (list, tuple)):
        for item in defensive:
            s = str(item or "")
            if "煤气灯" in s or "贬低" in s:
                return True
    return False


def resolve_route(
    persona: Optional[dict] = None,
    manipulation_output: Optional[dict] = None,
) -> str:
    """确定性判定当前关系走反制线还是引导线。

    Args:
        persona: 人物画像 dict（可空）；带 dark_triad/防御性沟通标签 → counter。
        manipulation_output: build_counter_manipulation 的返回 dict（可空）；
            triggered=True 且检出了操控模式 → counter；user_goal 为
            "反制驯服"/"立刻止损" 也算反制意图。

    Returns:
        "counter" 或 "guide"（不抛异常，缺省一律 guide）。
    """
    mo = manipulation_output if isinstance(manipulation_output, dict) else {}
    if mo:
        # 反制策略真实触发：检出了操控模式，或反制步骤非空。
        patterns = mo.get("manipulation_patterns")
        strategies = mo.get("counter_strategies")
        if mo.get("triggered") is True and (
            (isinstance(patterns, (list, tuple)) and len(patterns) > 0)
            or (isinstance(strategies, (list, tuple)) and len(strategies) > 0)
        ):
            return "counter"
        # 用户目标明确选择反制/止损。
        if str(mo.get("user_goal", "") or "") in _COUNTER_TRUTHY_GOALS:
            return "counter"

    if _persona_flags_counter(persona):
        return "counter"

    return "guide"


def main_metric(route: str) -> str:
    """路线对应的主指标名：counter→loyalty，其余→affection。"""
    return "loyalty" if route == "counter" else "affection"


def extend_reward(reward: Optional[dict], route: str) -> dict[str, float]:
    """把一个奖励 dict 扩展成完整七维奖励向量（新 dict，绝不改入参）。

    规则：
      1. 输出一定包含全部 REWARD_KEYS 七个键。
      2. reward 里显式给出的某维数值 → 原样透传（四舍五入到 4 位）。
      3. reward 里未给出的维 → 用"标量奖励基数 × 该路线权重"扩展；
         基数依次取 reward["total"] / reward["amount"] / reward["value"]，
         都没有则基数为 0.0（缺键补 0.0）。
      4. route 未知时按 guide 处理。

    Args:
        reward: 原始奖励 dict（可为 None）。
        route: "counter" / "guide"。

    Returns:
        dict: 七维 {key: float}，与入参无引用共享。
    """
    reward = reward if isinstance(reward, dict) else {}
    weights = ROUTE_WEIGHTS.get(route, ROUTE_WEIGHTS["guide"])

    base: float = 0.0
    for k in ("total", "amount", "value"):
        v = reward.get(k)
        if v is not None:
            try:
                base = float(v)
                break
            except (TypeError, ValueError):
                base = 0.0

    out: dict[str, float] = {}
    for key in REWARD_KEYS:
        if key in reward:
            try:
                out[key] = round(float(reward[key]), 4)
            except (TypeError, ValueError):
                out[key] = 0.0
        else:
            out[key] = round(base * weights.get(key, 0.0), 4)
    return out
