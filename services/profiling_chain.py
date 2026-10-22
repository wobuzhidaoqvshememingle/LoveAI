# -*- coding: utf-8 -*-
"""LoveAI v3.2.0 - 测写凭证链（Chain-of-Credentials Gate）。

把"六层人格画像"流程从一串互相 skip 的 try/except 改成一条确定性凭证链：

    S0_INGEST → 凭证0 → S1_EXTRACT → 凭证1 → S2_QUALIFY → 凭证2
              → S3_RECONCILE → 凭证3 → S4_ASSEMBLE → 凭证4

每步产出 StepCredential = {step_id, artifact, artifact_hash=sha256(canonical_json(artifact)), qualified}。
每步函数体第一行硬调 ctx.require(上一步 step_id)：
  - 上一步凭证不存在 / artifact_hash 对不上（被篡改）/ qualified=False
    → 立刻 raise ChainBreakError，本步函数体一行都不执行。
这是前置依赖，不是事后检查。

对外暴露 run_profiling_chain(person_id, messages) -> dict：
  {ok, broken_at, reason, credentials:{step_id:qualified}, summary, pending_layers, unmet_layers}

设计原则：
  - 空层就是空层，绝不拿"画像摘要：..."模板句冒充证据。
  - 薄样本跑完会诚实地把不足层写进 unmet_layers（带"缺 N 条证据"）。
  - S3 补扫补不出就显式记"源材料耗尽，无法补跑"，不许编。
  - 链断一环（异常/篡改/前置不合格），后面全不跑，runner 显式记录断在哪一步。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from services.six_layer_persona import (
    Layer,
    SixLayerProfile,
    EVIDENCE_THRESHOLD,
    to_six_layer,
)

logger = logging.getLogger("LoveAI.profiling_chain")


# ---------------------------------------------------------------------- #
# 异常与凭证
# ---------------------------------------------------------------------- #
class ChainBreakError(Exception):
    """凭证链断裂：缺前置凭证 / 凭证被篡改 / 前置凭证不合格。"""


def _canonical_json(obj: Any) -> str:
    """确定性 JSON 序列化（key 排序、ensure_ascii=False），用于算 artifact_hash。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class StepCredential:
    step_id: str
    artifact: Any
    qualified: bool
    artifact_hash: str = ""

    def __post_init__(self) -> None:
        if not self.artifact_hash:
            self.artifact_hash = _sha256(_canonical_json(self.artifact))


class ChainContext:
    """凭证链上下文：持有每步凭证，提供 require / record。"""

    def __init__(self, person_id: str, messages: list[dict],
                 persona_result: Optional[dict] = None) -> None:
        self.person_id = person_id
        self.messages = messages or []
        # 生产侧可直接传入已算好的 17 维结果（避免重复扫描）；None 则 S1 自己扫
        self.persona_result = persona_result
        self.credentials: dict[str, StepCredential] = {}
        # S1 产出的 SixLayerProfile（S2/S3/S4 共享同一个 profile 对象）
        self.profile: Optional[SixLayerProfile] = None

    # ---- 前置门禁：本步函数体第一行调它 ----
    def require(self, step_id: str) -> None:
        cred = self.credentials.get(step_id)
        if cred is None:
            raise ChainBreakError(
                f"凭证链断裂：缺前置凭证 {step_id}，本步禁止执行")
        if not cred.qualified:
            raise ChainBreakError(
                f"凭证链断裂：前置凭证 {step_id} qualified=False，本步禁止执行")
        # 防篡改：重算 hash 对比
        current_hash = _sha256(_canonical_json(cred.artifact))
        if current_hash != cred.artifact_hash:
            raise ChainBreakError(
                f"凭证链断裂：前置凭证 {step_id} artifact_hash 不匹配"
                f"（可能被篡改），本步禁止执行")

    def record(self, step_id: str, artifact: Any, qualified: bool) -> StepCredential:
        cred = StepCredential(step_id=step_id, artifact=artifact, qualified=qualified)
        self.credentials[step_id] = cred
        return cred


# ---------------------------------------------------------------------- #
# 关键词信号扫描（S1 用：把 messages 扫成 persona_result 形状，再喂给 to_six_layer）
# ---------------------------------------------------------------------- #
# 每条信号 = (正则, 挂到 persona_result 的哪个字段路径, 取值)
# 这里只做字面命中，绝不臆断；命中一条就是一条真实证据。

_L1_EMOJI = r"[哈哈嘻嘻😊🥰😘😉😂🤔😏😴😭生气讨厌]"
_L1_STAGE_WORDS = [
    ("暧昧期", r"(暧昧|心动|喜欢上你|对你有感觉)"),
    ("热恋期", r"(热恋|天天想见你|分不开)"),
    ("磨合期", r"(磨合|吵架|总是因为.*吵|三观不合)"),
    ("稳定期", r"(稳定|结婚|见家长|一辈子)"),
]

_L2_INITIATIVE = [r"我去找你", r"我来接你", r"我订", r"我安排", r"我主动"]
_L2_COMMIT = [r"我答应", r"我保证", r"说话算话", r"一定", r"不骗你", r"信守承诺"]
_L2_CANCEL = [r"下次吧", r"再说", r"临时有事", r"去不了"]

_L3_HAPPY = [r"开心", r"高兴", r"幸福", r"美滋滋", r"爽"]
_L3_SAD = [r"难过", r"委屈", r"想哭", r"失落", r"沮丧"]
_L3_ANGRY = [r"生气", r"火大", r"气死", r"烦", r"受不了"]
_L3_TRIGGER = [r"已读不回", r"冷战", r"拉黑", r"不回消息", r"消失"]

_L4_CLINGY = [r"你在哪", r"怎么不理我", r"秒回", r"多陪陪我", r"不要离开我"]
_L4_AVOID = [r"我想静静", r"别管我", r"需要空间", r"一个人待着", r"别黏我"]
_L4_SECURE = [r"我相信你", r"没关系", r"我理解", r"你忙你的"]

_L5_MONEY = [r"AA制", r"各付各", r"我来买单", r"省钱", r"花钱大手大脚", r"借钱"]
_L5_LOYALTY = [r"忠诚", r"出轨", r"暧昧对象", r"备胎", r"边界"]
_L5_FAMILY = [r"父母", r"结婚", r"孩子", r"原生家庭", r"爸妈"]
_L5_REDLINE = [r"赌博", r"家暴", r"欺骗", r"撒谎", r"黄赌毒"]

_L6_FAMILY = [r"我爸", r"我妈", r"原生家庭", r"从小", r"单亲", r"父母离异"]
_L6_SELFWORTH = [r"我配不上", r"自卑", r"自我价值", r"觉得自己没用", r"讨好型"]
_L6_SCRIPT = [r"总是遇到", r"重复的模式", r"又一次", r"剧本", r"关系模式"]


def _hits(text: str, patterns: list[str]) -> list[str]:
    out = []
    for p in patterns:
        if re.search(p, text):
            out.append(p)
    return out


def scan_messages_to_persona_result(messages: list[dict]) -> dict:
    """把 messages 扫成 persona_analyzer 风格的 dict（只装真实命中，空字段就空）。"""
    texts = []
    for m in messages:
        c = m.get("content") or m.get("text") or ""
        if c:
            texts.append(c)
    blob = " ".join(texts)

    result: dict[str, Any] = {"person_id": ""}

    # L1 表层
    style: dict[str, Any] = {}
    if re.search(r"[哈哈嘻嘻]", blob):
        style["tone"] = "轻松爱笑"
    if re.search(r"[😊🥰😘😉😂]", blob):
        style["emoji"] = "常用表情"
    if style:
        result["communication"] = style
    for label, pat in _L1_STAGE_WORDS:
        if re.search(pat, blob):
            result["relationship_stage"] = {"stage": label}
            break

    # L2 行为/投入/承诺
    behavior: dict[str, Any] = {}
    if _hits(blob, _L2_INITIATIVE):
        behavior["initiation_rate"] = round(len(_hits(blob, _L2_INITIATIVE)) / 5.0, 2)
    if _hits(blob, _L2_CANCEL):
        behavior["cancel_rate"] = round(len(_hits(blob, _L2_CANCEL)) / 5.0, 2)
    commit: dict[str, Any] = {}
    if _hits(blob, _L2_COMMIT):
        commit["kept_rate"] = round(len(_hits(blob, _L2_COMMIT)) / 5.0, 2)
        commit["level"] = "有承诺意识"
    if behavior:
        result["behavior"] = behavior
    if commit:
        result["commitment"] = commit

    # L3 情绪
    emo: dict[str, Any] = {}
    happy_n = len(_hits(blob, _L3_HAPPY))
    sad_n = len(_hits(blob, _L3_SAD))
    angry_n = len(_hits(blob, _L3_ANGRY))
    if happy_n:
        emo["dominant"] = "积极"
    if sad_n + angry_n:
        emo["negative_ratio"] = round((sad_n + angry_n) / max(1, happy_n + sad_n + angry_n), 2)
    triggers = _hits(blob, _L3_TRIGGER)
    if triggers:
        emo["triggers"] = triggers
    if emo:
        result["emotion_pattern"] = emo

    # L4 依恋
    att: dict[str, Any] = {}
    cling = _hits(blob, _L4_CLINGY)
    avoid = _hits(blob, _L4_AVOID)
    sec = _hits(blob, _L4_SECURE)
    if cling and not avoid:
        att["primary_type"] = "anxious"
        att["anxiety_score"] = 65
    elif avoid and not cling:
        att["primary_type"] = "avoidant"
        att["avoidance_score"] = 65
    elif sec:
        att["primary_type"] = "secure"
        att["security_score"] = 70
    if att:
        result["attachment"] = att

    # L5 价值观/底线
    values: dict[str, Any] = {}
    if _hits(blob, _L5_MONEY):
        values["money_view"] = "/".join(_hits(blob, _L5_MONEY)[:2])
    if _hits(blob, _L5_LOYALTY):
        values["loyalty_view"] = "/".join(_hits(blob, _L5_LOYALTY)[:2])
    if _hits(blob, _L5_FAMILY):
        values["family_view"] = "/".join(_hits(blob, _L5_FAMILY)[:2])
    if _hits(blob, _L5_REDLINE):
        values["moral_redline"] = "/".join(_hits(blob, _L5_REDLINE)[:2])
    if values:
        result["values"] = values

    # L6 核心自我
    core: dict[str, Any] = {}
    if _hits(blob, _L6_FAMILY):
        core["family_origin"] = "/".join(_hits(blob, _L6_FAMILY)[:2])
    if _hits(blob, _L6_SELFWORTH):
        core["self_worth"] = "/".join(_hits(blob, _L6_SELFWORTH)[:2])
    if _hits(blob, _L6_SCRIPT):
        core["relationship_script"] = "/".join(_hits(blob, _L6_SCRIPT)[:2])
    if core:
        result["core_self"] = core

    return result


# ---------------------------------------------------------------------- #
# S3 补扫：对每个 pending 层用更细的关键词再扫一次原始消息
# ---------------------------------------------------------------------- #
_LAYER_RESCAN_PATTERNS: dict[Layer, list[tuple[str, str]]] = {
    Layer.L1_SURFACE: [
        ("表层.补扫.口头禅", r"(哈哈|嗯|哦|好呀|好吧|随便|都行)"),
        ("表层.补扫.emoji", r"[😀-🿿]"),
        ("表层.补扫.回复速度", r"(秒回|马上|稍等|等一下|晚点)"),
    ],
    Layer.L2_BEHAVIOR: [
        ("行为.补扫.主动", r"(我来找你|我去接|我买了|我请客|我安排)"),
        ("行为.补扫.爽约", r"(下次|再说|临时|去不了|忘了)"),
        ("行为.补扫.履约", r"(说到做到|兑现|记得|做到了)"),
    ],
    Layer.L3_EMOTION: [
        ("情绪.补扫.开心", r"(开心|高兴|幸福|满足|甜蜜)"),
        ("情绪.补扫.低落", r"(难过|委屈|伤心|沮丧|心累)"),
        ("情绪.补扫.愤怒", r"(生气|火大|烦|受不了|爆发)"),
    ],
    Layer.L4_ATTACHMENT: [
        ("依恋.补扫.焦虑", r"(你在哪|怎么不理我|多陪我|不要离开)"),
        ("依恋.补扫.回避", r"(想静静|别管我|要空间|一个人)"),
        ("依恋.补扫.安全", r"(我相信你|没关系|理解|放心)"),
    ],
    Layer.L5_VALUE: [
        ("价值观.补扫.金钱", r"(AA|买单|省钱|花钱|借钱|还钱)"),
        ("价值观.补扫.忠诚", r"(忠诚|出轨|暧昧|备胎|边界感)"),
        ("价值观.补扫.底线", r"(赌博|家暴|撒谎|欺骗|黄赌毒)"),
    ],
    Layer.L6_CORE: [
        ("核心.补扫.原生家庭", r"(爸|妈|原生家庭|从小|单亲|离异)"),
        ("核心.补扫.自我价值", r"(配不上|自卑|没用|讨好|价值感)"),
        ("核心.补扫.关系剧本", r"(总是|重复|又一次|模式|剧本)"),
    ],
}


# ---------------------------------------------------------------------- #
# 五步
# ---------------------------------------------------------------------- #
def s0_ingest(ctx: ChainContext) -> StepCredential:
    # S0 是链头，无上一步，不调 require。
    msgs = ctx.messages or []
    texts = [str(m.get("content") or m.get("text") or "") for m in msgs]
    joined = " ".join(t for t in texts if t)
    artifact = {
        "msg_count": len(msgs),
        "text_hash": _sha256(joined)[:16],
    }
    qualified = len(msgs) > 0
    return ctx.record("S0_INGEST", artifact, qualified)


def s1_extract(ctx: ChainContext) -> StepCredential:
    ctx.require("S0_INGEST")  # 前置门禁：S0 没跑/不合格/被篡改 → 立刻 raise
    if ctx.persona_result is not None:
        # 生产侧已算好 17 维结果，直接映射（不重复扫描）
        persona_result = dict(ctx.persona_result)
    else:
        persona_result = scan_messages_to_persona_result(ctx.messages)
    persona_result["person_id"] = ctx.person_id
    prof = to_six_layer(persona_result, person_id=ctx.person_id)

    # 硬清洗：不允许"画像摘要：..."模板句进入证据（去假兜底）
    for layer in Layer:
        cleaned = [e for e in prof.evidence[layer]
                   if not str(e.get("quote", "")).startswith("画像摘要：")]
        prof.evidence[layer] = cleaned

    ctx.profile = prof

    artifact = {
        layer.value: {
            "evidence_count": len(prof.evidence[layer]),
            "quotes": [e.get("quote", "") for e in prof.evidence[layer]],
        }
        for layer in Layer
    }
    # 合格标准：真跑了（每层要么有真实证据要么空着），且无模板句冒充
    has_template = any(
        str(e.get("quote", "")).startswith("画像摘要：")
        for layer in Layer for e in prof.evidence[layer]
    )
    qualified = not has_template
    return ctx.record("S1_EXTRACT", artifact, qualified)


def s2_qualify(ctx: ChainContext) -> StepCredential:
    ctx.require("S1_EXTRACT")
    prof = ctx.profile
    if prof is None:
        raise ChainBreakError("S2: profile 不存在（S1 未产出 SixLayerProfile）")
    qualified_layers: list[str] = []
    pending_layers: list[dict] = []
    for layer in Layer:
        n = len(prof.evidence[layer])
        if n >= EVIDENCE_THRESHOLD:
            qualified_layers.append(layer.value)
        else:
            needed = EVIDENCE_THRESHOLD - n
            pending_layers.append({
                "layer": layer.value,
                "evidence_count": n,
                "needed": needed,
                "reason": f"缺 {needed} 条证据（当前 {n}/{EVIDENCE_THRESHOLD}）",
            })
    artifact = {
        "qualified_layers": qualified_layers,
        "pending_layers": pending_layers,
    }
    # S2 这一步本身合格 = 真做了分层判定（不管薄样本多薄）
    return ctx.record("S2_QUALIFY", artifact, True)


def s3_reconcile(ctx: ChainContext) -> StepCredential:
    ctx.require("S2_QUALIFY")  # 前置门禁：S2 没跑/被篡改 → 本步函数体不执行
    prof = ctx.profile
    if prof is None:
        raise ChainBreakError("S3: profile 不存在")

    s2 = ctx.credentials["S2_QUALIFY"].artifact
    pending = s2.get("pending_layers", []) or []
    blob = " ".join(
        str(m.get("content") or m.get("text") or "") for m in ctx.messages
    )

    rescale_report: list[dict] = []
    for p in pending:
        layer = Layer(p["layer"])
        before = len(prof.evidence[layer])
        added = 0
        for quote, pat in _LAYER_RESCAN_PATTERNS.get(layer, []):
            if re.search(pat, blob):
                prof.add_evidence(layer, {
                    "source": "chain_s3_rescan",
                    "quote": f"{quote}（补扫命中）",
                    "weight": 0.8,
                })
                added += 1
        after = len(prof.evidence[layer])
        if after < EVIDENCE_THRESHOLD:
            note = f"源材料耗尽，无法补跑（补扫后 {after}/{EVIDENCE_THRESHOLD}，仍缺 {EVIDENCE_THRESHOLD - after}）"
        else:
            note = f"补扫成功（{before}→{after}）"
        rescale_report.append({
            "layer": layer.value,
            "before": before,
            "after": after,
            "added": added,
            "note": note,
        })

    artifact = {"rescale": rescale_report}
    # S3 合格 = 对每个 pending 层都尝试了补扫并诚实记录结果（不管补没补出来）
    return ctx.record("S3_RECONCILE", artifact, True)


def s4_assemble(ctx: ChainContext) -> StepCredential:
    ctx.require("S3_RECONCILE")
    prof = ctx.profile
    if prof is None:
        raise ChainBreakError("S4: profile 不存在")

    # 硬门禁：任何层若唯一证据是"画像摘要：..."模板句 = 凭证不合格
    for layer in Layer:
        evs = prof.evidence[layer]
        if evs and all(str(e.get("quote", "")).startswith("画像摘要：") for e in evs):
            raise ChainBreakError(
                f"S4 门禁：{layer.value} 唯一证据是模板句，凭证不合格，拒绝出 summary")

    summary = prof.summary()

    # pending 层必须带"未达标/缺 N 条证据"（从 S2+S3 合并）
    s2 = ctx.credentials["S2_QUALIFY"].artifact
    s3 = ctx.credentials["S3_RECONCILE"].artifact
    # 用 S3 之后的实际证据数重算 unmet
    unmet: list[dict] = []
    for layer in Layer:
        n = len(prof.evidence[layer])
        if n < EVIDENCE_THRESHOLD:
            needed = EVIDENCE_THRESHOLD - n
            unmet.append({
                "layer": layer.value,
                "evidence_count": n,
                "needed": needed,
                "reason": f"未达标：缺 {needed} 条证据（当前 {n}/{EVIDENCE_THRESHOLD}）",
            })
    # 把 S3 的补扫 note 合并进去
    rescale_map = {r["layer"]: r["note"] for r in s3.get("rescale", [])}
    for u in unmet:
        if u["layer"] in rescale_map:
            u["rescan_note"] = rescale_map[u["layer"]]

    artifact = {
        "summary": summary,
        "unmet_layers": unmet,
        "pending_layers_after_rescale": [u["layer"] for u in unmet],
    }
    return ctx.record("S4_ASSEMBLE", artifact, True)


# ---------------------------------------------------------------------- #
# 对外入口
# ---------------------------------------------------------------------- #
def run_profiling_chain(person_id: str, messages: list[dict],
                        persona_result: Optional[dict] = None) -> dict:
    """跑完整凭证链。断在某一步就返回 ok=False + broken_at。

    Args:
        person_id: 人物 ID。
        messages: 原始消息列表（S0 校验非空，S3 补扫用）。
        persona_result: 可选，生产侧已算好的 17 维结果；None 则 S1 自己扫。
    """
    ctx = ChainContext(person_id=person_id, messages=messages or [],
                       persona_result=persona_result)
    steps: list[Callable[[ChainContext], StepCredential]] = [
        s0_ingest, s1_extract, s2_qualify, s3_reconcile, s4_assemble,
    ]
    broken_at: Optional[str] = None
    reason: str = ""
    current_step_name = "s0_ingest"
    try:
        for step in steps:
            current_step_name = step.__name__
            step(ctx)
    except ChainBreakError as e:
        broken_at = current_step_name  # 断在这一步（它函数体没执行）
        reason = str(e)
        logger.error("测写凭证链断裂 @ %s: %s", broken_at, reason)
    except Exception as e:  # 链路径上的异常不允许被静默吞
        broken_at = current_step_name
        reason = f"{type(e).__name__}: {e}"
        logger.exception("测写凭证链未预期异常 @ %s: %s", broken_at, reason)

    credentials_report = {sid: c.qualified for sid, c in ctx.credentials.items()}
    out: dict[str, Any] = {
        "ok": broken_at is None,
        "broken_at": broken_at,
        "reason": reason,
        "credentials": credentials_report,
        "person_id": person_id,
    }
    if "S4_ASSEMBLE" in ctx.credentials:
        art = ctx.credentials["S4_ASSEMBLE"].artifact
        out["summary"] = art.get("summary")
        out["pending_layers"] = art.get("pending_layers_after_rescale", [])
        out["unmet_layers"] = art.get("unmet_layers", [])
    else:
        out["summary"] = None
        out["pending_layers"] = []
        out["unmet_layers"] = []
    return out


__all__ = [
    "ChainBreakError", "ChainContext", "StepCredential",
    "run_profiling_chain", "scan_messages_to_persona_result",
    "s0_ingest", "s1_extract", "s2_qualify", "s3_reconcile", "s4_assemble",
]
