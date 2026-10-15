# -*- coding: utf-8 -*-
"""LoveAI v2.0 - 六层人格画像结构（R3.3-45）。

设计：
  - 从表层到深层六层：表层印象 / 行为模式 / 情绪模式 / 依恋类型 / 价值观与底线 / 核心自我。
  - 越表层越易变、证据来得快；越深层越难改、证据要攒。
  - 与 persona_analyzer.py 对接：不重写它，只加 `to_six_layer(persona_result)` 转换函数，
    把 persona_analyzer 的 17 维输出重新挂到六层上。
  - writable_to_memory：L4/L5/L6 进 world_info（长期、稳定、要写进记忆），
    L1/L2/L3 进 vector（短期、易变、做语义检索）。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Layer(str, Enum):
    L1_SURFACE = "L1_SURFACE"      # 表层印象：说话风格/口头禅/表情包/响应速度
    L2_BEHAVIOR = "L2_BEHAVIOR"    # 行为模式：回复习惯/主动频率/爽约率/承诺兑现率
    L3_EMOTION = "L3_EMOTION"      # 情绪模式：触发点/平复时间/情绪起伏/共情能力
    L4_ATTACHMENT = "L4_ATTACHMENT"  # 依恋类型：安全/焦虑/回避/恐惧
    L5_VALUE = "L5_VALUE"          # 价值观与底线：金钱观/忠诚度/家庭观/道德红线
    L6_CORE = "L6_CORE"            # 核心自我：原生家庭烙印/自我价值感/关系剧本


# 每层最少证据数，低于此数标"待补"
EVIDENCE_THRESHOLD = 3

# 进 world_info 的层（稳定、长期）
WORLD_INFO_LAYERS = frozenset({Layer.L4_ATTACHMENT, Layer.L5_VALUE, Layer.L6_CORE})
# 进 vector 的层（易变、短期、语义检索）
VECTOR_LAYERS = frozenset({Layer.L1_SURFACE, Layer.L2_BEHAVIOR, Layer.L3_EMOTION})


@dataclass
class SixLayerProfile:
    """六层画像容器。"""

    person_id: str = ""
    evidence: dict[Layer, list[dict]] = field(default_factory=lambda: {l: [] for l in Layer})

    # ------------------------------------------------------------------ #
    # 证据录入
    # ------------------------------------------------------------------ #
    def add_evidence(self, layer: Layer, evidence: dict) -> None:
        """往对应层挂一条证据。

        evidence 建议字段：
          source    : 来源（chat / persona_analyzer / user_report / manual）
          quote     : 原文或观察句
          weight    : 权重 0.5-2.0（默认 1.0）
          ts        : 时间戳（可选）
        """
        if not isinstance(layer, Layer):
            raise TypeError(f"layer 必须是 Layer 枚举，收到 {type(layer)}")
        ev = dict(evidence or {})
        ev.setdefault("source", "unknown")
        ev.setdefault("weight", 1.0)
        self.evidence[layer].append(ev)

    # ------------------------------------------------------------------ #
    # 置信度
    # ------------------------------------------------------------------ #
    def confidence_per_layer(self) -> dict[str, float]:
        """每层按证据数算置信度。

        规则：n 条证据 → min(1.0, n / EVIDENCE_THRESHOLD)。
        权重不参与（先保持简单，避免过拟合）。
        返回 key 用 str(Layer) 便于 JSON 序列化。
        """
        out: dict[str, float] = {}
        for layer in Layer:
            n = len(self.evidence.get(layer, []))
            out[layer.value] = round(min(1.0, n / EVIDENCE_THRESHOLD), 3)
        return out

    def pending_layers(self) -> list[str]:
        """证据不足（<3 条）的层，标"待补"。"""
        out = []
        for layer in Layer:
            if len(self.evidence.get(layer, [])) < EVIDENCE_THRESHOLD:
                out.append(layer.value)
        return out

    # ------------------------------------------------------------------ #
    # 摘要
    # ------------------------------------------------------------------ #
    def summary(self) -> dict[str, Any]:
        """六层各自的画像摘要 + 整体成熟度。"""
        per_layer: dict[str, Any] = {}
        conf = self.confidence_per_layer()
        for layer in Layer:
            evs = self.evidence.get(layer, [])
            quotes = [e.get("quote", "") for e in evs if e.get("quote")]
            per_layer[layer.value] = {
                "evidence_count": len(evs),
                "confidence": conf[layer.value],
                "status": "ok" if len(evs) >= EVIDENCE_THRESHOLD else "pending",
                "top_quotes": quotes[:5],
            }
        # 整体成熟度：深层权重更高（L6/L5/L4 各 0.25，L3/L2/L1 各 ~0.083）
        weights = {
            Layer.L1_SURFACE: 0.08,
            Layer.L2_BEHAVIOR: 0.10,
            Layer.L3_EMOTION: 0.12,
            Layer.L4_ATTACHMENT: 0.22,
            Layer.L5_VALUE: 0.22,
            Layer.L6_CORE: 0.26,
        }
        maturity = sum(conf[l.value] * weights[l] for l in Layer)
        return {
            "person_id": self.person_id,
            "per_layer": per_layer,
            "pending_layers": self.pending_layers(),
            "maturity": round(maturity, 3),
            "maturity_label": self._maturity_label(maturity),
        }

    @staticmethod
    def _maturity_label(m: float) -> str:
        if m >= 0.8:
            return "成熟画像"
        if m >= 0.5:
            return "基本成型"
        if m >= 0.25:
            return "初步印象"
        return "信息不足"

    # ------------------------------------------------------------------ #
    # 记忆分层
    # ------------------------------------------------------------------ #
    def writable_to_memory(self) -> dict[str, list[dict]]:
        """决定哪些层进 world_info，哪些进 vector。

        - world_info: L4/L5/L6（稳定、长期、要写进长期记忆）
        - vector    : L1/L2/L3（易变、短期、做语义检索）
        """
        world: list[dict] = []
        vec: list[dict] = []
        for layer in Layer:
            evs = self.evidence.get(layer, [])
            if layer in WORLD_INFO_LAYERS:
                world.extend(evs)
            elif layer in VECTOR_LAYERS:
                vec.extend(evs)
        return {"world_info": world, "vector": vec}


# ------------------------------------------------------------------ #
# 与 persona_analyzer.py 对接：把 17 维结果重挂到六层
# ------------------------------------------------------------------ #
def to_six_layer(persona_result: dict,
                 person_id: Optional[str] = None) -> SixLayerProfile:
    """把 persona_analyzer.analyze() 的输出重组成 SixLayerProfile。

    不重写 persona_analyzer，只做字段映射。persona_result 缺字段时静默跳过，
    不抛栈。
    """
    persona_result = persona_result or {}
    prof = SixLayerProfile(person_id=person_id or persona_result.get("person_id", ""))

    def _g(*keys, default=None):
        cur: Any = persona_result
        for k in keys:
            if isinstance(cur, dict):
                cur = cur.get(k)
            else:
                return default
            if cur is None:
                return default
        return cur

    # ---- L1 表层印象：说话风格/响应速度 ----
    # 兼容通用键 communication_style，也吃 persona_analyzer 真实键 communication
    style = _g("communication_style") or _g("style") or _g("communication") or {}
    if isinstance(style, dict):
        for k in ("tone", "catchphrase", "emoji", "reply_speed", "mode",
                  "avg_length", "response_speed"):
            v = style.get(k)
            if v:
                prof.add_evidence(Layer.L1_SURFACE, {
                    "source": "persona_analyzer",
                    "quote": f"表层.{k}={v}",
                })
    # 关系阶段也算表层上下文（真实键 relationship_stage.stage）
    stage = _g("relationship_stage")
    if isinstance(stage, dict):
        stage = stage.get("stage") or stage.get("label") or stage.get("name")
    if stage:
        prof.add_evidence(Layer.L1_SURFACE, {
            "source": "persona_analyzer",
            "quote": f"关系阶段={stage}",
        })

    # ---- L2 行为模式：回复习惯/主动频率/承诺兑现 ----
    behavior = _g("behavior") or {}
    if isinstance(behavior, dict):
        for k in ("initiation_rate", "cancel_rate", "fulfillment_rate",
                  "avg_reply_delay", "streak_count"):
            v = behavior.get(k)
            if v is not None:
                prof.add_evidence(Layer.L2_BEHAVIOR, {
                    "source": "persona_analyzer",
                    "quote": f"行为.{k}={v}",
                })
    # persona_analyzer 真实键：investment（投入）+ commitment（承诺履约）
    invest = _g("investment") or {}
    if isinstance(invest, dict):
        for k in ("initiative", "initiation", "frequency", "response_rate"):
            v = invest.get(k)
            if v not in (None, "", {}):
                prof.add_evidence(Layer.L2_BEHAVIOR, {
                    "source": "persona_analyzer",
                    "quote": f"行为.投入.{k}={v}",
                })
    commit = _g("commitment") or {}
    if isinstance(commit, dict):
        for k in ("kept_rate", "fulfillment", "score", "level"):
            v = commit.get(k)
            if v is not None:
                prof.add_evidence(Layer.L2_BEHAVIOR, {
                    "source": "persona_analyzer",
                    "quote": f"行为.承诺履约.{k}={v}",
                })

    # ---- L3 情绪模式 ----
    emo = _g("emotion_pattern") or {}
    if isinstance(emo, dict):
        for k in ("volatility", "triggers", "recovery_time", "empathy",
                  "dominant", "negative_ratio"):
            v = emo.get(k)
            if v not in (None, "", {}):
                prof.add_evidence(Layer.L3_EMOTION, {
                    "source": "persona_analyzer",
                    "quote": f"情绪.{k}={v}",
                })
    # 冲突处理模式也归情绪/互动层
    cstyle = _g("conflict_style") or {}
    if isinstance(cstyle, dict):
        for k in ("style", "repair_rate", "repair"):
            v = cstyle.get(k)
            if v not in (None, "", {}):
                prof.add_evidence(Layer.L3_EMOTION, {
                    "source": "persona_analyzer",
                    "quote": f"情绪.冲突.{k}={v}",
                })

    # ---- L4 依恋类型（真实键 attachment.type / scores）----
    att = _g("attachment") or {}
    if isinstance(att, dict):
        for k in ("primary_type", "type", "avoidance_score", "anxiety_score",
                  "security_score", "avoidance", "anxiety", "security"):
            v = att.get(k)
            if v is not None:
                prof.add_evidence(Layer.L4_ATTACHMENT, {
                    "source": "persona_analyzer",
                    "quote": f"依恋.{k}={v}",
                })
    elif isinstance(att, str) and att:
        prof.add_evidence(Layer.L4_ATTACHMENT, {
            "source": "persona_analyzer",
            "quote": f"依恋.primary_type={att}",
        })

    # ---- L5 价值观与底线 ----
    values = _g("values") or _g("value_line") or _g("values_match") or {}
    if isinstance(values, dict):
        for k in ("money_view", "loyalty_view", "family_view", "moral_redline",
                  "score", "alignment"):
            v = values.get(k)
            if v not in (None, "", {}):
                prof.add_evidence(Layer.L5_VALUE, {
                    "source": "persona_analyzer",
                    "quote": f"价值观.{k}={v}",
                })
    # 边界感也归底线
    bnd = _g("boundaries") or {}
    if isinstance(bnd, dict):
        for k in ("score", "enforce", "type"):
            v = bnd.get(k)
            if v not in (None, "", {}):
                prof.add_evidence(Layer.L5_VALUE, {
                    "source": "persona_analyzer",
                    "quote": f"价值观.边界.{k}={v}",
                })
    # 黑暗三联征也归到 L5（道德底线/操纵倾向）
    dark = _g("psychology", "dark_triad_tendencies") or {}
    if isinstance(dark, dict) and dark:
        prof.add_evidence(Layer.L5_VALUE, {
            "source": "persona_analyzer",
            "quote": f"黑暗三联征={dark}",
        })

    # ---- L6 核心自我 ----
    core = _g("core_self") or {}
    if isinstance(core, dict):
        for k in ("family_origin", "self_worth", "relationship_script"):
            v = core.get(k)
            if v:
                prof.add_evidence(Layer.L6_CORE, {
                    "source": "persona_analyzer",
                    "quote": f"核心.{k}={v}",
                })
    # 自我分化 / 人格剧本 也算核心
    sd = _g("self_differentiation") or {}
    if isinstance(sd, dict):
        for k in ("score", "level"):
            v = sd.get(k)
            if v is not None:
                prof.add_evidence(Layer.L6_CORE, {
                    "source": "persona_analyzer",
                    "quote": f"核心.自我分化.{k}={v}",
                })
    # 人格倾向（NPD/BPD/回避型等）算核心自我剧本
    primary = _g("primary_personality") or _g("personality_type")
    if primary:
        prof.add_evidence(Layer.L6_CORE, {
            "source": "persona_analyzer",
            "quote": f"人格剧本={primary}",
        })
    # 兜底：某层仍空时，用 summary 顶一条，保证每层都有可读证据
    summary = _g("summary")
    if summary:
        summary = str(summary)[:80]
        for layer in Layer:
            if not prof.evidence.get(layer):
                prof.add_evidence(layer, {
                    "source": "persona_analyzer",
                    "quote": f"画像摘要：{summary}",
                })

    return prof


__all__ = [
    "Layer", "SixLayerProfile", "EVIDENCE_THRESHOLD",
    "WORLD_INFO_LAYERS", "VECTOR_LAYERS",
    "to_six_layer",
]
