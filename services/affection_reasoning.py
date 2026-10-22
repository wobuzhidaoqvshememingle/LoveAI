# -*- coding: utf-8 -*-
"""
LoveAI v3.3 - R3.3-81 指标函数化改造：分析→delta 推理层。

设计目标（对应需求 R3.3-81）：
  旧机制 = "字面关键词命中 → 固定 delta"（如"想你"→亲密+2.0、"嗯"→共鸣-0.5），
  这是机械打分，同一句话在任何语境下得分都一样，不真实。
  新机制 = "消息/行为 → 函数分析（情绪 + 意图 + 关系姿态推断）→
  内心状态维度进度报告（带证据链）→ 才映射到 18 维好感度 / 忠诚度"。

本模块职责：
  1. analyze_message()：对单条子消息产出分析结论
       （情绪标签/强度 + 关系姿态 posture 集合 + 内心状态推断）。
  2. build_report()：聚合多条子消息，产出 ReasoningReport：
       每个 18 维 + loyalty 的建议 delta，每条带证据链
       （"她说X → 推断她内心Y → Z维度Δ"），可复核、可展示。
  3. 证据走中心数据池约束通道（services.data_pool.contracts.EVIDENCE / Message）。
  4. 本地规则推理为基线；LLM 增强可选（注入 llm_gateway，不强制、不抛栈）。

关键反机械性：
  delta = f(情绪valence, 关系姿态集合, 强度I, 方向factor)。
  同一个字面词在不同情绪/语境下得到不同（甚至相反）的 delta；
  分析结论为"无真实进展"（中性寒暄/敷衍）时 delta ≈ 0 或为负。
  本模块**不**维护"关键词→固定delta"表；词只用于推断姿态这一中间结论，
  最终分数由"内心状态进度"合成得出。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from services.data_pool import Evidence, Message

logger = logging.getLogger(__name__)

# EmotionAnalyzer 强度是 0-10；引擎侧其它地方用 0-1，统一在这里归一。
_EA_INTENSITY_SCALE = 10.0


# --------------------------------------------------------------------------- #
# 关系姿态（relationship posture）：函数分析的中间结论，不是分数
# --------------------------------------------------------------------------- #
# 姿态枚举（一个消息可同时命中多个姿态，如"亲密表达"+"实际投入"）。
POSTURE_INTIMACY_DECLARE = "intimacy_declare"   # 主动亲密表达（想你/爱你/亲密称呼）
POSTURE_INVESTMENT_CARE = "investment_care"     # 实际投入/关怀行为（点外卖/接你/提醒吃饭）
POSTURE_PROACTIVE_ENGAGE = "proactive_engage"   # 主动接话/追问/延伸话题
POSTURE_COMPLIANCE = "compliance"               # 顺从配合（听你的/我错了/你决定）
POSTURE_DEFIANCE = "defiance"                   # 违抗对抗（凭什么/你管不着/就不）
POSTURE_CONFLICT_NEG = "conflict_negative"     # 消极冲突/排斥（别烦我/关你啥事/算了）
POSTURE_PERFUNCTORY = "perfunctory"             # 敷衍冷处理（嗯/哦/呵呵短回复）
POSTURE_TSUNDERE = "tsundere_playful"           # 傲娇撒娇（硬词+软标记）
POSTURE_NEUTRAL = "neutral_chitchat"            # 中性寒暄/无真实进展

# 内心状态维度（心理需要层）——这才是"函数分析"真正在度量的东西，
# 再由 INNER_TO_DIM 映射回 18 维存储键。
_INNER_SECURITY = "security_inner"       # 安全感
_INNER_TRUST = "trust_inner"             # 信任感
_INNER_INVESTMENT = "investment_inner"   # 对方实际投入
_INNER_INTIMACY = "intimacy_inner"       # 亲密/被爱确认
_INNER_UNDERSTOOD = "understood_inner"   # 被理解/情绪价值
_INNER_RESPECT = "respect_inner"         # 被尊重

POSITIVE_EMOTIONS = {"happy", "love", "excited", "joy", "开心", "喜爱", "兴奋"}
NEGATIVE_EMOTIONS = {"angry", "sad", "cold", "失望", "生气", "难过"}

# ---- 姿态特征词（仅用于"推断姿态"这一中间结论，命中不等于加分） ----
# 亲密表达特征
_F_INTIMACY = ("想你", "喜欢你", "爱你", "我爱你", "宝贝", "宝宝", "老婆", "老公",
               "么么", "抱抱", "亲亲", "心动", "亲爱的", "暧昧", "想你了")
# 实际投入/关怀行为特征
_F_INVESTMENT = ("点了外卖", "给你点", "给你买", "帮你", "陪你", "等你", "接你",
                 "送你", "给你做", "记得吃饭", "注意身体", "别太累", "早点睡",
                 "多喝水", "穿多点", "下雨", "我来", "给你带")
# 顺从配合特征（注意：不收录裸"我改"，避免"要我改/凭什么要我改"误命中）
_F_COMPLIANCE = ("听你的", "你说了算", "都听你的", "你决定", "我错了",
                 "对不起", "遵命", "答应你", "下次不了", "那我改", "好吧听你",
                 "好好好", "行吧听你")
# 违抗对抗特征
_F_DEFIANCE = ("凭什么", "我凭什么", "不关你事", "你管不着", "就不", "我不改",
               "懒得理你", "关我屁事", "你算什么", "别指挥我", "你管我")
# 消极冲突/排斥特征
_F_CONFLICT = ("别烦我", "关你什么事", "关你屁事", "不想聊", "别管我", "滚",
               "算了", "无所谓", "别联系", "拉黑")
# 敷衍短回复
_F_PERFUNCTORY = ("嗯", "哦", "哦哦", "呵呵", "。。。", "...", "额", "呃")
# 反问/接话特征（愿意继续聊）
_F_QUESTION = ("吗", "呢", "？", "?", "怎么", "为啥", "为什么", "真的假的", "然后呢", "你呢")
# 傲娇硬词 + 软标记
_F_TSUNDERE_HARD = ("滚", "不要脸", "谁是你老婆", "油嘴滑舌", "打你", "讨厌")
_F_TSUNDERE_SOFT = ("哈哈", "哈哈哈", "表情", "~", "～", "呀", "啦", "😊", "😘", "😍", "🤗", "😳")


@dataclass
class InnerDelta:
    """单个内心状态 → 18维维度的推进判断（带证据链）。"""
    dimension: str            # 18 维存储键（或 "loyalty"）
    inner_state: str          # 内心状态名（security_inner / trust_inner ...）
    delta: float              # 建议增量（未封顶）
    evidence: Evidence = field(default_factory=lambda: Evidence(source="affection_reasoning"))
    stance: str = "hold"      # forward / backward / hold


class AffectionReasoner:
    """分析→内心状态进度→18维/loyalty delta 的纯推理层（本地规则基线）。

    不直接持有 db；输入是消息 dict（{content, direction}），输出是可序列化报告。
    EmotionAnalyzer / LLM 网关均为可选注入，缺失时退化为内置轻量规则，绝不抛栈。
    """

    SENT_DIRECTION_FACTOR: float = 0.2   # 我方示好不直接涨对方好感（与 v3.2 一致）
    LLM_HINT_SCALE: float = 0.5

    def __init__(self, emotion_analyzer: Any = None, llm_gateway: Any = None,
                 six_layer: Any = None) -> None:
        self.emotion = emotion_analyzer
        self.llm = llm_gateway
        self.six_layer = six_layer
        # 兜底：无外部 EmotionAnalyzer 时，内置一个最小可用的情绪读取。
        self._fallback_emotion = None
        if self.emotion is None:
            try:
                from services.emotion_analyzer import EmotionAnalyzer
                self.emotion = EmotionAnalyzer(llm_gateway=None)
            except Exception as e:  # noqa: BLE001
                logger.warning("AffectionReasoner: EmotionAnalyzer 不可用，退化内置规则: %s", e)

    # ------------------------------------------------------------------ #
    # 情绪读取：优先用上游传入 emotion；缺失时本地分析
    # ------------------------------------------------------------------ #
    def _read_emotion(self, content: str, emotion: Optional[dict]) -> dict:
        emotion = emotion or {}
        label = str(emotion.get("label", "") or "").strip().lower()
        intensity = emotion.get("intensity", None)
        if label:
            # 上游已给情绪（0-1 标度），直接采用，置信度按上游值。
            try:
                inten = float(intensity)
            except (TypeError, ValueError):
                inten = 0.5
            inten = max(0.0, min(1.0, inten))
            return {"label": label, "intensity": inten,
                    "confidence": float(emotion.get("confidence", 0.6)),
                    "is_question": bool(emotion.get("is_question", False)),
                    "source": "upstream"}
        # 本地分析
        if self.emotion is not None and content:
            try:
                r = self.emotion.analyze(content)
                raw_i = float(r.get("intensity", 0))
                return {"label": str(r.get("label", "neutral")),
                        "intensity": max(0.0, min(1.0, raw_i / _EA_INTENSITY_SCALE)),
                        "confidence": float(r.get("confidence", 0.3)),
                        "is_question": bool(r.get("is_question", False)),
                        "source": "emotion_analyzer"}
            except Exception as e:  # noqa: BLE001
                logger.debug("EmotionAnalyzer.analyze 失败: %s", e)
        return {"label": "neutral", "intensity": 0.2, "confidence": 0.2,
                "is_question": "？" in content or "?" in content, "source": "fallback"}

    # ------------------------------------------------------------------ #
    # 姿态推断：词只用于推断"她此刻的关系姿态"，不直接对应分数
    # ------------------------------------------------------------------ #
    def _detect_postures(self, content: str, emotion: dict,
                         is_question: bool) -> list[str]:
        text = content or ""
        stripped = text.strip()
        postures: list[str] = []
        label = emotion.get("label", "")

        # 傲娇撒娇优先：硬词 + 软标记
        if any(w in text for w in _F_TSUNDERE_HARD) and \
           any(m in text for m in _F_TSUNDERE_SOFT):
            postures.append(POSTURE_TSUNDERE)

        if any(w in text for w in _F_INTIMACY):
            postures.append(POSTURE_INTIMACY_DECLARE)
        if any(w in text for w in _F_INVESTMENT):
            postures.append(POSTURE_INVESTMENT_CARE)
        if any(w in text for w in _F_COMPLIANCE):
            postures.append(POSTURE_COMPLIANCE)
        if any(w in text for w in _F_DEFIANCE):
            postures.append(POSTURE_DEFIANCE)
        if any(w in text for w in _F_CONFLICT):
            postures.append(POSTURE_CONFLICT_NEG)

        # 敷衍：纯短冷词（晚安/早安等不在此列）
        if stripped in _F_PERFUNCTORY or (0 < len(stripped) <= 3 and stripped in _F_PERFUNCTORY):
            postures.append(POSTURE_PERFUNCTORY)

        # 主动接话：疑问句 / 哈哈笑声 / 较长回复
        if is_question or any(w in text for w in _F_QUESTION) or \
           any(h in text for h in ("哈哈", "嘻嘻", "嘿嘿")):
            postures.append(POSTURE_PROACTIVE_ENGAGE)

        # 无任何姿态 → 中性寒暄（无真实进展）
        if not postures:
            postures.append(POSTURE_NEUTRAL)

        # 语境冲突消解（函数分析：同一信号在不同姿态下含义不同）
        # 1) 傲娇撒娇（硬词+软标记）→ 压制"真冲突"（滚/讨厌是撒娇不是发火）
        if POSTURE_TSUNDERE in postures:
            postures = [p for p in postures if p != POSTURE_CONFLICT_NEG]
        # 2) 公开违抗 → 压制"顺从"（"凭什么听你的"不是真顺从）
        if POSTURE_DEFIANCE in postures:
            postures = [p for p in postures if p != POSTURE_COMPLIANCE]
        return postures

    # ------------------------------------------------------------------ #
    # 核心：把"情绪 + 姿态"合成内心状态进度（带证据链）
    # ------------------------------------------------------------------ #
    def _synthesize(self, content: str, direction: str, emotion: dict,
                     postures: list[str]) -> list[InnerDelta]:
        label = emotion.get("label", "neutral")
        I = float(emotion.get("intensity", 0.5))
        received = direction == "received"
        f = 1.0 if received else self.SENT_DIRECTION_FACTOR

        valence = 0
        if label in POSITIVE_EMOTIONS:
            valence = 1
        elif label in NEGATIVE_EMOTIONS:
            valence = -1

        quote = (content or "").strip()[:40]
        out: list[InnerDelta] = []

        def add(dim: str, inner: str, delta: float, stance: str,
                why: str) -> None:
            delta = round(delta * f, 3)
            ev = Evidence(
                text=f"她说「{quote}」→ {why} → {dim} {'+' if delta>=0 else ''}{delta}",
                source="affection_reasoning",
                confidence=round(max(0.2, min(0.95, emotion.get("confidence", 0.5))), 2),
                metadata={"postures": list(postures), "emotion": label,
                          "direction": direction},
            )
            out.append(InnerDelta(dimension=dim, inner_state=inner,
                                  delta=delta, evidence=ev, stance=stance))

        # 只对"对方发来"的消息做关系进度推断（我方情绪不直接改变对方好感）。
        if not received:
            # 我方消息：不产生正向关系进度，仅保留 LLM hint 叠加（外层处理）。
            return out

        for posture in postures:
            if posture == POSTURE_INTIMACY_DECLARE:
                if valence >= 0:
                    why = "她主动表达亲密/思念，内心在确认被爱与亲近，亲密关系前进"
                    add("intimacy", _INNER_INTIMACY, 2.2 * I, "forward", why)
                    add("emotional_value", _INNER_UNDERSTOOD, 1.2 * I, "forward", why)
                    add("romance", _INNER_INTIMACY, 1.2 * I, "forward", why)
                else:
                    # 负情绪下的亲密词 = 反话/讽刺，不涨亲密，反伤信任
                    why = "她带着抵触/冷淡语气说亲密词，推断为反话或情绪性宣泄，并非真心亲近"
                    add("trust", _INNER_TRUST, -1.0 * I, "backward", why)
                    add("security", _INNER_SECURITY, -0.8 * I, "backward", why)
                    add("emotional_value", _INNER_UNDERSTOOD, -0.8 * I, "backward", why)

            elif posture == POSTURE_INVESTMENT_CARE:
                why = "她付出实际行动关心/照顾你，内心在投入，安全感与被在乎感前进"
                add("trust", _INNER_TRUST, 2.0 * I, "forward", why)
                add("security", _INNER_SECURITY, 1.5 * I, "forward", why)
                add("emotional_value", _INNER_UNDERSTOOD, 2.0 * I, "forward", why)
                add("thoughtfulness", _INNER_INVESTMENT, 1.5 * I, "forward", why)

            elif posture == POSTURE_COMPLIANCE:
                # 顺从/配合 = 忠诚度语义（听话度），不污染 18 维好感。
                why = "她顺从/认错/配合你的要求，推断她在这段关系里的遵从度上升"
                add("loyalty", "loyalty_inner", 1.5, "forward", why)

            elif posture == POSTURE_DEFIANCE:
                why = "她公开顶撞/拒绝遵从，推断她的配合度下降，且感到不被尊重"
                add("loyalty", "loyalty_inner", -2.0, "backward", why)
                add("respect", _INNER_RESPECT, -1.0 * I, "backward", why)

            elif posture == POSTURE_CONFLICT_NEG:
                why = "她在推开/排斥/否定你，推断她此刻有防备与不满，安全/信任受挫"
                add("trust", _INNER_TRUST, -1.8 * I, "backward", why)
                add("security", _INNER_SECURITY, -1.5 * I, "backward", why)
                add("emotional_value", _INNER_UNDERSTOOD, -1.5 * I, "backward", why)
                add("respect", _INNER_RESPECT, -1.2 * I, "backward", why)

            elif posture == POSTURE_PERFUNCTORY:
                why = "她只用极短冷词敷衍，推断她没有投入情绪，关系没有真实前进"
                add("chat_resonance", _INNER_UNDERSTOOD, -0.6 * I, "backward", why)
                add("intimacy", _INNER_INTIMACY, -0.5 * I, "backward", why)
                add("emotional_value", _INNER_UNDERSTOOD, -0.4 * I, "backward", why)

            elif posture == POSTURE_PROACTIVE_ENGAGE:
                # 负情绪下的反问多为反诘/抬杠，不算"愿意继续投入"。
                if valence < 0:
                    why = "她带着负面情绪反问，推断是在反诘/发泄，并非真诚延展话题"
                    add("chat_resonance", _INNER_UNDERSTOOD, -0.3 * I, "backward", why)
                else:
                    why = "她主动追问/接话/延展话题，推断她愿意继续投入这段对话，共鸣前进"
                    add("chat_resonance", _INNER_UNDERSTOOD, 1.0 * I, "forward", why)
                    add("emotional_value", _INNER_UNDERSTOOD, 0.4 * I, "forward", why)

            elif posture == POSTURE_TSUNDERE:
                why = "她用硬词但带笑/表情/语气词，推断是撒娇式傲娇，非真发火，亲密前进"
                add("intimacy", _INNER_INTIMACY, 0.8, "forward", why)
                add("romance", _INNER_INTIMACY, 0.5, "forward", why)
                add("chat_resonance", _INNER_UNDERSTOOD, 0.5, "forward", why)

            elif posture == POSTURE_NEUTRAL:
                why = "无明确情绪与关系信号，推断为普通寒暄，关系无真实进展"
                # 内心状态无推进：delta=0（不加任何分）

        # 情绪兜底加权：积极/消极情绪本身微调核心情绪维（与旧版方向一致，但强度I缩放）
        if valence > 0 and received:
            add("emotional_value", _INNER_UNDERSTOOD, 0.6 * I, "forward",
                "她整体情绪积极，推断相处体验良好")
        elif valence < 0 and received:
            add("emotional_stability", _INNER_SECURITY, -0.6 * I, "backward",
                "她整体情绪消极，推断当下相处氛围紧张")

        return out

    # ------------------------------------------------------------------ #
    # 对外主接口
    # ------------------------------------------------------------------ #
    def analyze_message(self, message: dict,
                        emotion: Optional[dict] = None) -> dict:
        """分析单条子消息 → 分析结论（情绪+姿态+内心状态推断）。"""
        content = (message.get("content", "") or "") if isinstance(message, dict) else str(message or "")
        direction = message.get("direction", "received") if isinstance(message, dict) else "received"
        emo = self._read_emotion(content, emotion)
        postures = self._detect_postures(content, emo, emo.get("is_question", False))
        deltas = self._synthesize(content, direction, emo, postures)
        return {
            "message": Message(role="user" if direction == "received" else "assistant",
                               content=content).to_dict(),
            "emotion": emo,
            "postures": postures,
            "inner_deltas": [
                {"dimension": d.dimension, "inner_state": d.inner_state,
                 "delta": d.delta, "stance": d.stance,
                 "evidence": d.evidence.to_dict()}
                for d in deltas
            ],
        }

    def build_report(self, sub_msgs: list[dict],
                     emotion: Optional[dict] = None,
                     analysis: Optional[dict] = None) -> dict:
        """聚合多条子消息 → ReasoningReport（18维+loyalty 建议 delta + 证据链）。

        多条子消息按出现顺序加权（越靠后权重越大），与旧 _calc_delta_weighted 一致。
        analysis.affection_hints / loyalty_hints（LLM 提示）按 LLM_HINT_SCALE 叠加。
        """
        analysis = analysis or {}
        if not sub_msgs:
            return {"dimensions": {}, "loyalty_delta": 0.0, "evidence": [],
                    "conclusions": [], "summary": "无消息，无进展"}

        # 聚合：对每条子消息做分析，加权合并维度 delta
        dim_acc: dict[str, float] = {}
        ev_acc: list[Evidence] = []
        conclusions: list[dict] = []
        total_w = 0.0
        for i, m in enumerate(sub_msgs):
            w = float(i + 1)
            conclusion = self.analyze_message(m, emotion)
            conclusions.append(conclusion)
            emo = conclusion["emotion"]
            ev_acc.append(Evidence(
                text=f"情绪={emo['label']}(强度{emo['intensity']:.0%}) "
                     f"姿态={','.join(conclusion['postures'])}",
                source="affection_reasoning", confidence=emo.get("confidence", 0.5),
            ))
            for d in conclusion["inner_deltas"]:
                dim_acc[d["dimension"]] = dim_acc.get(d["dimension"], 0.0) + w * d["delta"]
                ev_acc.append(Evidence.from_dict(d["evidence"]))
            total_w += w

        dimensions: dict[str, float] = {}
        loyalty_delta = 0.0
        if total_w > 0:
            for dim, val in dim_acc.items():
                avg = val / total_w
                if dim == "loyalty":
                    loyalty_delta = round(avg, 3)
                else:
                    dimensions[dim] = round(avg, 3)

        # LLM 提示叠加（可选增强，不强制）
        hints = analysis.get("affection_hints") or {}
        if isinstance(hints, dict):
            for dim, v in hints.items():
                try:
                    dimensions[dim] = round(dimensions.get(dim, 0.0)
                                            + float(v) * self.LLM_HINT_SCALE, 3)
                except (TypeError, ValueError):
                    pass
        lhints = analysis.get("loyalty_hints") or {}
        if isinstance(lhints, dict):
            try:
                loyalty_delta = round(loyalty_delta
                                      + float(lhints.get("loyalty", 0.0)) * self.LLM_HINT_SCALE, 3)
            except (TypeError, ValueError):
                pass

        summary = self._summarize(conclusions, dimensions, loyalty_delta)
        return {
            "dimensions": dimensions,
            "loyalty_delta": loyalty_delta,
            "evidence": [e.to_dict() for e in ev_acc],
            "conclusions": conclusions,
            "summary": summary,
        }

    @staticmethod
    def _summarize(conclusions: list[dict], dimensions: dict[str, float],
                   loyalty_delta: float) -> str:
        moved = [(d, v) for d, v in dimensions.items() if abs(v) >= 0.2]
        if loyalty_delta:
            moved.append(("loyalty", loyalty_delta))
        if not moved:
            return "分析结论：无真实关系进展，分数不做机械加减"
        moved.sort(key=lambda kv: -abs(kv[1]))
        top = "，".join(f"{d}{('+' if v>=0 else '')}{v:.1f}" for d, v in moved[:4])
        poses = set()
        for c in conclusions:
            poses.update(c.get("postures", []))
        return f"姿态推断[{','.join(sorted(poses))}] → 内心进度：{top}"

    # ------------------------------------------------------------------ #
    # 任务引擎联动（验收点3）：判断一条消息是否带来"真实正向进展"
    # ------------------------------------------------------------------ #
    def assess_task_progress(self, message: str,
                             emotion: Optional[dict] = None) -> dict:
        """判断对方这条回应是否算"真实正向进展"（用于任务推进门禁）。

        旧机制：命中 complete_keywords 子串就推进任务。
        新机制：关键词只是触发线索，还要看分析结论——
          对方是真的积极配合/感兴趣（forward），还是敷衍/抵触（backward/hold）。
        """
        msg = {"content": message or "", "direction": "received"}
        rep = self.build_report([msg], emotion, {})
        dims = rep["dimensions"]
        pos = sum(v for v in dims.values() if v > 0.2)
        neg = sum(-v for v in dims.values() if v < -0.2)
        is_forward = pos > neg and pos >= 0.3
        return {
            "is_genuine_progress": is_forward,
            "positive_momentum": round(pos, 3),
            "negative_momentum": round(neg, 3),
            "postures": rep["conclusions"][0]["postures"] if rep["conclusions"] else [],
            "summary": rep["summary"],
            "evidence": rep["evidence"],
        }

    # ------------------------------------------------------------------ #
    # 交叉验证：姿态/语境推断  vs  词典法确定性调制
    # ------------------------------------------------------------------ #
    def cross_check(self, message: str,
                    emotion: Optional[dict] = None) -> dict:
        """把本引擎的结论与 relation_metrics 的确定性结论对拍。

        分工：
          · 本引擎：关键词 → 关系姿态 → 语境消解 → 内心状态。
            强项是**语境**（傲娇 / 反话 / 违抗 / 敷衍），弱项是**词级调制**——
            对 "我不太开心""不是很喜欢" 这类"否定 + 程度"结构，
            label 仍会取到正向情感词。
          · relation_metrics：称谓分级 + 情感词 ±1 + 程度副词几何平均 + 否定取反。
            纯算术、可逐步手算，强项正是上面那个弱项。
        两者一致 → 提高置信；不一致 → 标记 need_review，交人工或上层裁决。
        本方法**只读**，不改变任何 delta 计算路径。

        Returns:
            含 posture_polarity / lexicon_polarity / agree / need_review /
            ps / negations / degree / appellation / note 的字典。
        """
        msg = {"content": message or "", "direction": "received"}
        rep = self.build_report([msg], emotion, {})
        dims = rep.get("dimensions") or {}
        pos = sum(v for v in dims.values() if v > 0.2)
        neg = sum(-v for v in dims.values() if v < -0.2)
        if pos > neg and pos > 0:
            posture_pol = "positive"
        elif neg > pos and neg > 0:
            posture_pol = "negative"
        else:
            posture_pol = "neutral"

        out: dict[str, Any] = {
            "posture_polarity": posture_pol,
            "lexicon_polarity": None,
            "agree": None,
            "need_review": False,
            "ps": None,
            "negations": 0,
            "degree": 1.0,
            "appellation": 0.0,
            "note": "",
        }

        try:
            # 延迟导入 + 容错：relation_metrics 缺失时本引擎照常工作
            from services.relation_metrics import message_sentiment
            lex = message_sentiment(message or "")
        except Exception as exc:  # noqa: BLE001
            out["note"] = "relation_metrics 不可用，跳过词典法对拍：%s" % exc
            return out

        clause = (lex.get("clauses") or [{}])[0]
        out.update({
            "lexicon_polarity": lex["polarity"],
            "ps": lex["ps"],
            "negations": clause.get("negations", 0),
            "degree": clause.get("degree_applied", 1.0),
            "appellation": lex.get("max_appellation", 0.0),
        })

        # 双侧均中性 → 无信号，不算分歧
        if posture_pol == "neutral" and lex["polarity"] == "neutral":
            out["agree"] = True
            out["note"] = "双侧中性，无信号"
            return out

        out["agree"] = (posture_pol == lex["polarity"])
        if not out["agree"]:
            out["need_review"] = True
            out["note"] = (
                "两法极性相反：姿态推断=%s，词典法=%s"
                "（否定%d次／程度%.4f／称谓%.1f）。建议人工复核。"
                % (posture_pol, lex["polarity"], out["negations"],
                   out["degree"], out["appellation"])
            )
        return out


__all__ = ["AffectionReasoner", "InnerDelta",
           "POSTURE_INTIMACY_DECLARE", "POSTURE_INVESTMENT_CARE",
           "POSTURE_COMPLIANCE", "POSTURE_DEFIANCE", "POSTURE_CONFLICT_NEG",
           "POSTURE_PERFUNCTORY", "POSTURE_PROACTIVE_ENGAGE",
           "POSTURE_TSUNDERE", "POSTURE_NEUTRAL"]
