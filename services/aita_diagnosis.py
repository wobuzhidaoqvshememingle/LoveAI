# -*- coding: utf-8 -*-
"""R3.3-08 AIta 关系卡点诊断（Aita = am I the asshole / r/relationship_advice 式）。

独立模块：输入近期对话状态，输出当前最可能的「关系卡点类别」+ 反讽/危机信号预警。
纯本地、确定性、无 LLM 依赖。可被 direction_compass 或其它调度方直接调用。

7 类关系卡点：
  1. pursue_withdraw      追逃循环（一方追问施压，一方回避冷战）
  2. investment_imbalance 投入失衡（单向付出、不对等）
  3. communication_breakdown 沟通断裂（聊不下去/无话可说）
  4. trust_crisis         信任危机（欺骗/言行不一/怀疑）
  5. boundary_enmeshment  边界模糊（控制/侵入/查岗）
  6. commitment_stagnation 承诺停滞（暧昧卡死/不给名分/不推进）
  7. values_conflict      价值冲突（三观/未来规划对立）

另识别：
  - 反讽 / 被动攻击信号（表面客气实则讽刺）
  - 危机信号（分手/拉黑/自伤/家暴/法律动作，分级 none/watch/alert）

公开接口：
  - AItaDiagnosis().diagnose(person_id, snapshot) -> dict
  - STUCK_POINT_CATEGORIES（7 类卡点注册表）
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: 7 类关系卡点（key: 中文名）
STUCK_POINT_CATEGORIES = {
    "pursue_withdraw": "追逃循环",
    "investment_imbalance": "投入失衡",
    "communication_breakdown": "沟通断裂",
    "trust_crisis": "信任危机",
    "boundary_enmeshment": "边界模糊",
    "commitment_stagnation": "承诺停滞",
    "values_conflict": "价值冲突",
}

# 1) 追逃循环
_SP_PURSUER = ["为什么不回", "说清楚", "你到底", "你说话啊", "逼问", "追着问",
               "给个说法", "什么意思", "解释一下"]
_SP_WITHDRAW = ["不想说", "别问我", "我想静静", "算了", "没什么好说的",
                "随你", "你爱怎么想", "冷战", "不回", "已读不回"]

# 2) 投入失衡
_SP_INVEST = ["都是我主动", "每次都是我", "我付出了", "你从不", "凭什么",
              "我一个人在撑", "你根本不投入", "我热脸贴冷屁股"]

# 3) 沟通断裂
_SP_BREAKDOWN = ["没什么好聊的", "聊不到一起", "无话可说", "各过各的",
                 "沟通不了", "说了你也不懂", "频道不一样", "鸡同鸭讲"]

# 4) 信任危机
_SP_TRUST = ["骗我", "你撒谎", "你骗我", "我不信你", "又骗", "出轨",
             "你外面有人", "隐瞒", "偷偷", "查你手机", "你说的和做的不一样"]

# 5) 边界模糊
_SP_BOUNDARY = ["你管我", "别管我", "查我手机", "不许", "控制欲", "监视",
                "你凭什么", "干涉", "翻我手机", "24小时", "随时报备"]

# 6) 承诺停滞
_SP_STAGNATION = ["到底什么关系", "什么时候确定", "一直这样", "不公开",
                  "不见家长", "拖着", "不给答案", "一直暧昧", "不拒绝不主动"]

# 7) 价值冲突
_SP_VALUES = ["三观不合", "你根本不理解", "我想要的未来", "你妈和我",
              "彩礼谈不拢", "要不要孩子", "定居哪里", "消费观", "价值观不同"]

# 反讽 / 被动攻击
SARCASM_WORDS = ["呵呵", "是哦", "你可真行", "行行行", "好好好都是我的错",
                 "您说得对", "笑死", "了不起", "您忙", "我哪敢", "不敢当",
                 "求之不得", "真是辛苦你了", "呵。", "哦。"]

# 危机信号
CRISIS_WORDS = ["分手吧", "我们分手", "拉黑", "删除", "互删", "去死",
                "我去死", "割腕", "自杀", "家暴", "动手", "打我", "报警",
                "律师", "起诉", "净身出户", "威胁"]

#: 自伤 / 人身安全类词 = 直接最高级 alert
_ALERT_SELF_HARM = {"去死", "我去死", "割腕", "自杀"}
#: 人身安全 / 法律动作 = alert
_ALERT_SAFETY = {"家暴", "动手", "打我", "报警", "律师", "起诉"}


def _clamp01(v):
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, x))


class AItaDiagnosis:
    """关系卡点诊断器（AIta）。"""

    def diagnose(self, person_id: str, snapshot: dict | None = None) -> dict:
        """诊断当前最可能的关系卡点。

        snapshot 可缺字段：
          recent_text / dialogue_text / my_text / other_text / strategy_text
          metrics: {reciprocity, mutual, ...}
          deception_risk

        Returns:
            dict: {category, category_name, confidence, scores, evidence,
                   sarcasm_detected, sarcasm_signals,
                   crisis: {level, signals}, advice}
        """
        snap = snapshot or {}
        text = " ".join([
            str(snap.get("recent_text", "") or ""),
            str(snap.get("dialogue_text", "") or ""),
            str(snap.get("my_text", "") or ""),
            str(snap.get("other_text", "") or ""),
            str(snap.get("strategy_text", "") or ""),
            str(snap.get("strategy_orientation", "") or ""),
        ])

        buckets = {
            "pursue_withdraw": _SP_PURSUER + _SP_WITHDRAW,
            "investment_imbalance": _SP_INVEST,
            "communication_breakdown": _SP_BREAKDOWN,
            "trust_crisis": _SP_TRUST,
            "boundary_enmeshment": _SP_BOUNDARY,
            "commitment_stagnation": _SP_STAGNATION,
            "values_conflict": _SP_VALUES,
        }
        scores: dict = {}
        evidence: list = []
        for cat, words in buckets.items():
            hits = [w for w in words if w in text]
            scores[cat] = len(hits)
            if hits:
                evidence.append(f"{STUCK_POINT_CATEGORIES[cat]}:{'/'.join(hits[:3])}")

        # metrics 辅助
        metrics = snap.get("metrics", {}) or {}
        reciprocity = _clamp01(metrics.get("reciprocity"))
        mutual = _clamp01(metrics.get("mutual"))
        if reciprocity is not None and reciprocity < 0.35:
            scores["investment_imbalance"] += 1
            evidence.append(f"投入失衡:reciprocity={reciprocity}")
        if mutual is not None and mutual < 0.3:
            scores["pursue_withdraw"] += 1

        if str(snap.get("deception_risk", "")).lower() in ("高", "high", "4", "5"):
            scores["trust_crisis"] += 2

        best_cat = max(scores, key=lambda k: scores[k])
        best_score = scores[best_cat]
        if best_score <= 0:
            category = None
            category_name = None
            confidence = "none"
        else:
            category = best_cat
            category_name = STUCK_POINT_CATEGORIES[best_cat]
            confidence = "high" if best_score >= 3 else ("mid" if best_score == 2 else "low")

        sarcasm_signals = [w for w in SARCASM_WORDS if w in text]
        sarcasm_detected = len(sarcasm_signals) > 0

        crisis_signals = [w for w in CRISIS_WORDS if w in text]
        if any(w in crisis_signals for w in _ALERT_SELF_HARM | _ALERT_SAFETY):
            crisis_level = "alert"
        elif crisis_signals:
            crisis_level = "watch"
        else:
            crisis_level = "none"

        advice = self._advice(category, sarcasm_detected, crisis_level)

        return {
            "category": category,
            "category_name": category_name,
            "confidence": confidence,
            "scores": scores,
            "evidence": evidence,
            "sarcasm_detected": sarcasm_detected,
            "sarcasm_signals": sarcasm_signals,
            "crisis": {"level": crisis_level, "signals": crisis_signals},
            "advice": advice,
        }

    @staticmethod
    def _advice(category: str | None, sarcasm: bool, crisis: str) -> str:
        if crisis == "alert":
            return "出现安全/破裂级危机信号，立即停止技巧博弈，优先安全与止损，必要时求助"
        if crisis == "watch":
            return "出现关系破裂信号（分手/拉黑），先降温不要逼，评估是否值得修复"
        m = {
            "pursue_withdraw": "破追逃：停止追问施压，给对方空间，用低需求感引导而非逼问",
            "investment_imbalance": "停止单向追加投入，后撤并引导对方对等投入，观察其是否补位",
            "communication_breakdown": "换沟通形式/低压力话题重建连接，不硬聊深议题",
            "trust_crisis": "暂停情感推进，先用可验证的小事重建可信度，不口头争辩",
            "boundary_enmeshment": "明确各自边界与底线，收回过度侵入，关系降速",
            "commitment_stagnation": "给一个有期限的明确选项，不无限暧昧，观察其是否愿意承诺",
            "values_conflict": "把核心分歧摆上台面对齐，无法妥协则重新评估匹配度",
        }
        base = m.get(category, "未检测到明显卡点，维持当前方向即可")
        if sarcasm:
            base += "；注意：近期出现反讽/被动攻击，表面客气实则不满，需降级对峙、正面澄清情绪"
        return base
