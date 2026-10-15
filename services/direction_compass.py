# -*- coding: utf-8 -*-
"""方向罗盘（结局对齐，防"步步对、结局错"）。

职责：
1. 用"可证伪的成功结构性指标"衡量关系是否朝好结局（双向奔赴）发展；
2. 三重闸门：方向闸（是否朝好结局，无撤离导向）、匹配闸（是否对症此人此时）、
   真伪闸（是否被伪装/表演或低有效度预测误导）；
3. 漂移检测：关键指标连续恶化 / 策略与结局背离时给出 drift；
4. 结局倒推：从目标指标反推当前最该补的维度，供任务引擎。

纯本地、确定性实现；指标数据由调用方在 snapshot 中组装，缺失项标 None（不硬编）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 撤离/放弃导向词（出现即方向闸失败）
_WITHDRAW_WORDS = ["分手", "撤离", "放弃", "不值得", "离开", "止损", "算了别",
                   "拉黑", "断联", "撤退", "退出", "下一个", "换人"]
# 合规停止（对方明确拒绝/要求停止）
_RESPECT_STOP = ["对方明确拒绝", "要求停止", "停止联系", "尊重对方"]
# 推进/修复/引导导向词
_ADVANCE_WORDS = ["推进", "修复", "引导", "升温", "重建", "对等投入", "双向",
                  "回归", "降压", "给空间", "破冰", "拉近", "安抚", "邀约",
                  "培养", "巩固", "缓和", "复合"]

# 成功指标定义（key: 中文名）
SUCCESS_DIMENSIONS = {
    "mutual": "双向奔赴",
    "commitment_kept": "承诺兑现",
    "intimacy": "亲密感",
    "passion": "激情",
    "bonding": "承诺绑定",
    "repair": "修复力",
    "reciprocity": "对等投入",
    "stability": "稳定性",
    "satisfaction": "关系满意度",
}

# 回避型对象忌讳的施压动作
_PRESSURE_WORDS = ["逼", "施压", "追问", "黏", "高频表白", "催促", "摊牌", "哭闹"]
# 回避型对象适配动作
_SPACE_WORDS = ["给空间", "降压", "低需求", "间歇", "引导投入", "后撤", "松弛"]
# 焦虑型对象忌讳的动作
_ANXIOUS_BAD = ["长期冷淡", "故意不回", "过度制造稀缺", "冷暴力", "忽冷忽热"]
# 焦虑型对象适配动作
_ANXIOUS_GOOD = ["稳定回应", "安全感", "确定性", "一致", "可靠", "安抚"]

# ------------------------------------------------------------------ #
# R3.3-08 AIta 关系卡点诊断：实现已抽到独立模块 services/aita_diagnosis.py。
# 这里仅做 re-export（保持本模块既有符号可被外部引用）+ 委托调用。
# ------------------------------------------------------------------ #
from services.aita_diagnosis import (  # noqa: E402
    AItaDiagnosis,
    STUCK_POINT_CATEGORIES,
    SARCASM_WORDS as _SARCASM_WORDS,
    CRISIS_WORDS as _CRISIS_WORDS,
)


def _clamp01(v) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, x))


def _text_has(text: str, words: list) -> str | None:
    for w in words:
        if w in text:
            return w
    return None


class DirectionCompass:
    """结局对齐罗盘。"""

    def __init__(self, db=None) -> None:
        self.db = db

    def evaluate(self, person_id: str, snapshot: dict | None = None) -> dict:
        """评估当前方向对齐情况。

        snapshot 字段（均可缺）：
          stage / attachment / risk_level / strategy_text / strategy_orientation
          deception_risk / review_validity / words_actions_gap
          metrics: {mutual, commitment_kept, intimacy, passion, bonding, repair,
                    reciprocity, stability, satisfaction}
          affection_trend: up/flat/down
        """
        snap = snapshot or {}
        # 1. 成功指标
        raw_metrics = snap.get("metrics", {}) or {}
        metrics = {k: _clamp01(raw_metrics.get(k)) for k in SUCCESS_DIMENSIONS}
        present = [v for v in metrics.values() if v is not None]
        alignment_score = round(sum(present) / len(present), 3) if present else None
        # R3.3-23 数字诚实：对外暴露的 alignment_score（预测有效度）走 confidence_cap 封顶，
        # 永不回 1.0；内部闸门判断仍用未封顶值，保持原有逻辑不变。
        alignment_score_raw = alignment_score
        if alignment_score_raw is not None:
            try:
                from services.confidence_cap import cap_score as _cap_score
                alignment_score = _cap_score(alignment_score_raw, len(present))
            except Exception:
                pass

        # 2. 三重闸门
        direction_gate = self._direction_gate(snap)
        match_gate = self._match_gate(snap)
        truth_gate = self._truth_gate(snap)
        gates = {"direction": direction_gate, "match": match_gate, "truth": truth_gate}

        # 3. 漂移检测
        drift = self._drift(snap, metrics, gates)

        # 4. 结局倒推（当前最该补的维度）
        gaps = self._backward_gaps(metrics)

        # 5. R3.3-08 AIta 关系卡点诊断（7 类卡点 + 反讽/危机信号）
        stuck_point = self.diagnose_stuck_point(person_id, snap)

        aligned = (
            direction_gate["pass"] is True
            and match_gate["level"] != "fail"
            and truth_gate["level"] != "fail"
            and drift["drift"] is not True
            and (alignment_score is None or alignment_score >= 0.45)
        )

        verdict = self._verdict(aligned, gates, drift, alignment_score)

        # === 导航范式附加字段（真实证据驱动，2026-10-13 接线）===
        # 这些字段此前读 snapshot 的 trajectory_summary/trust_level/attribution_summary/
        # p_retreat/p_warm/p_stable，但 _build_compass_snapshot 从未写入这些键，导致全事件
        # 回退到默认值（"待计算"/None/恒 0.5/0.3/0.2）。现改为从真实进边字段
        # (trajectory_vector / suspicion_score / stress_dist / pattern_match / trust_score
        # / repair_rate) 推导。纯输出侧计算，不改既有 gates/drift/aligned 逻辑。
        nav = self._navigation(snap, drift)

        # === 北极星（2026-10-13 新增，纯附加键）===
        # 用户定稿：北极星 = 她"主动参与解决"为主信号 + 关系满意度为参考指标。
        # 主信号来自 snapshot["consensus"]（services/consensus_signal.py 直接读库算），
        # 参考指标来自 snapshot["relationship_satisfaction"]（画像透传）。
        # 绝不回流 gates/drift/aligned——只做展示与解读。
        north_star = self._north_star(snap)

        destination = "引导线（主线目标，长期不变）"
        current_position = nav["current_position"]
        direction_probability = nav["direction_probability"]
        deviation = {
            "is_deviating": drift.get("drift", False) is True,
            "reason": drift.get("reason", "无漂移"),
            "severity": "high" if drift.get("drift") else "low",
        }
        recalculation = {
            "triggered": drift.get("drift", False) is True or nav["detour"]["triggered"],
            "new_heading": ("后撤（战术回撤）" if drift.get("drift")
                            else (nav["detour"]["heading"] if nav["detour"]["triggered"]
                                  else "维持当前航向")),
            "impact_on_destination": "无（终点不变，只是战术调整）",
        }
        path_status = nav["path_status"]
        detour_plan = nav["detour"]["plan"]
        fallback_plan = nav["fallback_plan"]
        multi_path_options = nav["multi_path_options"]

        return {
            "person_id": person_id,
            "aligned": aligned,
            "alignment_score": alignment_score,
            "metrics": metrics,
            "gates": gates,
            "drift": drift,
            "gaps": gaps,
            "stuck_point": stuck_point,
            "verdict": verdict,
            # === 导航范式附加字段 ===
            "destination": destination,
            "current_position": current_position,
            "direction_probability": direction_probability,
            "deviation": deviation,
            "recalculation": recalculation,
            "path_status": path_status,
            "detour_plan": detour_plan,
            "fallback_plan": fallback_plan,
            "multi_path_options": multi_path_options,
            # === 新增真实证据输出（附加键，不改既有键）===
            "p_separation_risk": nav["p_separation_risk"],
            "threshold_linkage": nav["threshold_linkage"],
            "direction_history": nav["direction_history"],
            # === 北极星（主动参与为主信号 + 满意度为参考）===
            "north_star": north_star,
        }

    # ------------------------------------------------------------------ #
    # 导航字段真实推导（2026-10-13 接线）
    # ------------------------------------------------------------------ #
    # 推导式（可复现、确定性，全部来自 snapshot 真实进边字段，不调外部 LLM）：
    #
    # 方向概率 direction_probability（三元 P后撤/P升温/P维稳）：
    #   基线权重 [后撤=0.30, 升温=0.35, 维稳=0.35]（先验：关系以维稳为主、略偏升温）。
    #   逐条证据加减权（delta，写在 _DIR_EVIDENCE 里），再把三项归一化到和=1：
    #     情绪趋势 emotion_trend:  ↓ +后撤0.10 / ↑ +升温0.08 / → 0
    #     延迟趋势 delay_trend:    ↑(回复间隔变长=变冷) +后撤0.10 / ↓(变短=变热) +升温0.05 / → 0
    #     怀疑链 suspicion_score: >=60 +后撤0.10 / >=30(观察) +后撤0.04 / 否则0
    #     压力归因 stress_dist:     含关系威胁 relationship_threat +后撤0.10
    #                              含现实压力 real_world_pressure +后撤0.05（给空间降压）
    #     模式库 pattern_match:     case_001/003/004/006（情绪崩溃/服软反弹/冷战/压力过载）+后撤0.08
    #                              case_005（表演热情）+升温0.05 / 无匹配 0
    #     信任 trust_score:        <50 +后撤0.08 / 否则0
    #     冲突修复 repair_rate:    <50 且有焦点冲突(focal>0) +后撤0.08 / 否则0
    #   归一化：p_i = raw_i / sum(raw)，round 3；evidence 列表记录实际触发的条目。
    #
    # 路径状态 path_status：
    #   绕行中 = 已触发真实绕行（关系威胁归因 / 给空间类模式命中 / 怀疑>=60 / 修复率低且有焦点）
    #   拥堵   = 延迟趋势↑(持续变冷) 且情绪趋势↓（延迟恶化+情绪走低）
    #   通畅   = 其余（趋势平稳或转热、无威胁归因）
    #
    # 绕行幅度 detour magnitude（分级，可测量）：
    #   大幅=后撤留灯：关系威胁归因 或 怀疑>=60
    #   轻度=换话术降压：仅现实压力归因 或 给空间类模式命中
    #   无：未触发绕行
    #
    # 前瞻 p_separation_risk：优先用 analyze() forecast 的低置信度值；
    #   缺失时用 情绪↓或(威胁归因) 推 0.40 / 双低 推 0.15（低置信度，标注来源）。
    #
    # 阈值联动 threshold_linkage：真实规则——
    #   后撤方向 且 suspicion>=60 → 强化引导改变；
    #   升温方向 且 suspicion>=60 → 降级信任预期；
    #   否则 → 常规应对。方向取 direction_probability 的 argmax。
    # ------------------------------------------------------------------ #
    def _navigation(self, snap: dict, drift: dict) -> dict:
        # --- 取真实进边字段（缺失即 None，不臆造占位）---
        tv = snap.get("trajectory_vector") or {}
        emotion = tv.get("emotion_trend")
        delay = tv.get("delay_trend")
        activity = tv.get("activity_trend")
        periodicity = tv.get("periodicity")
        mutation_n = len(tv.get("mutation_points", []) or [])
        trust_score = snap.get("trust_score")
        suspicion_score = snap.get("suspicion_score")
        suspicion_level = snap.get("suspicion_level")
        stress_dist = snap.get("stress_dist") or {}
        stress_focal = snap.get("stress_focal_count") or 0
        pm = snap.get("pattern_match") or {}
        pm_matched = bool(pm.get("matched"))
        pm_case = pm.get("case_id")
        pm_pattern = pm.get("pattern")
        repair_rate = snap.get("repair_rate")

        # --- current_position（消除"待计算"）---
        if tv:
            traj_str = (f"情绪{emotion or '→'}/活跃{activity or '→'}/"
                        f"延迟{delay or '→'}/突变{mutation_n}点/"
                        f"{periodicity or '周期性未知'}")
        else:
            traj_str = "未实现：trajectory_vector 缺失（原因=画像未产出轨迹向量）"
        if trust_score is not None:
            try:
                tlv = float(trust_score)
                trust_label = (f"{tlv:.0f}/100（"
                               + ("低信任" if tlv < 50 else "中等" if tlv < 70 else "较高")
                               + f"，怀疑链={suspicion_level or '未知'}）")
            except (TypeError, ValueError):
                trust_label = f"trust_score={trust_score}（非数值）"
        else:
            trust_label = "未实现：trust_score 缺失（原因=画像未产出信任分）"
        if stress_dist:
            attr_str = "；".join(f"{k}×{v}" for k, v in stress_dist.items())
        elif stress_focal == 0 and tv:
            attr_str = "无显著压力归因（0 个焦点冲突事件被归因）"
        else:
            attr_str = "未实现：stress_attribution 缺失（原因=画像未产出归因）"
        current_position = {
            "trajectory": traj_str,
            "trust_level": trust_label,
            "attribution": attr_str,
        }

        # --- direction_probability（证据加权归一化）---
        raw = {"retreat": 0.30, "warm": 0.35, "stable": 0.35}
        evidence = []
        if emotion == "↓":
            raw["retreat"] += 0.10; evidence.append("情绪趋势↓→+后撤")
        elif emotion == "↑":
            raw["warm"] += 0.08; evidence.append("情绪趋势↑→+升温")
        if delay == "↑":
            raw["retreat"] += 0.10; evidence.append("延迟趋势↑(变冷)→+后撤")
        elif delay == "↓":
            raw["warm"] += 0.05; evidence.append("延迟趋势↓(变热)→+升温")
        try:
            sscore = float(suspicion_score) if suspicion_score is not None else 0.0
        except (TypeError, ValueError):
            sscore = 0.0
        if sscore >= 60:
            raw["retreat"] += 0.10; evidence.append(f"怀疑链{sscore:.0f}≥60→+后撤")
        elif sscore >= 30:
            raw["retreat"] += 0.04; evidence.append(f"怀疑链{sscore:.0f}观察→+后撤")
        has_threat = any(k in ("relationship_threat",) for k in stress_dist)
        has_rwp = any(k in ("real_world_pressure",) for k in stress_dist)
        if has_threat:
            raw["retreat"] += 0.10; evidence.append("含关系威胁归因→+后撤")
        if has_rwp:
            raw["retreat"] += 0.05; evidence.append("含现实压力归因→+后撤(给空间)")
        give_space_cases = {"case_001", "case_003", "case_004", "case_006"}
        if pm_matched and pm_case in give_space_cases:
            raw["retreat"] += 0.08; evidence.append(f"模式库命中{pm_case}({pm_pattern})→+后撤")
        elif pm_matched and pm_case == "case_005":
            raw["warm"] += 0.05; evidence.append("模式库命中case_005表演热情→+升温(谨慎)")
        try:
            tval = float(trust_score) if trust_score is not None else None
        except (TypeError, ValueError):
            tval = None
        if tval is not None and tval < 50:
            raw["retreat"] += 0.08; evidence.append(f"信任{tval:.0f}<50→+后撤")
        try:
            rval = float(repair_rate) if repair_rate is not None else None
        except (TypeError, ValueError):
            rval = None
        if rval is not None and rval < 50 and stress_focal > 0:
            raw["retreat"] += 0.08; evidence.append(f"修复率{rval:.0f}<50且有焦点冲突→+后撤")

        total = sum(raw.values())
        if total > 0:
            p_retreat = round(raw["retreat"] / total, 3)
            p_warm = round(raw["warm"] / total, 3)
            p_stable = round(raw["stable"] / total, 3)
        else:
            p_retreat = p_warm = p_stable = 0.333
        direction_probability = {
            "P后撤有效": p_retreat,
            "P升温": p_warm,
            "P维稳": p_stable,
            "evidence": evidence or ["无显著方向性证据→维持先验分布"],
            "note": "方向=证据加权归一化(基线0.30/0.35/0.35)，非恒值模板",
        }

        # --- path_status（由真实轨迹/归因判定，非 strategy_text 关键词）---
        detour_triggered = has_threat or (pm_matched and pm_case in give_space_cases) \
            or sscore >= 60 or (rval is not None and rval < 50 and stress_focal > 0)
        if delay == "↑" and emotion == "↓":
            path_status = "拥堵"
        elif detour_triggered:
            path_status = "绕行中"
        else:
            path_status = "通畅"

        # --- detour_plan（真实触发+幅度分级+回归条件）---
        if has_threat or sscore >= 60:
            magnitude = "大幅=后撤留灯"
            trigger = "关系威胁归因 或 怀疑链≥60"
        elif has_rwp or (pm_matched and pm_case in give_space_cases):
            magnitude = "轻度=换话术降压/给空间"
            trigger = "现实压力归因 或 给空间类模式命中"
        else:
            magnitude = "无"
            trigger = "无阻塞证据"
        detour_plan = {
            "trigger": trigger,
            "magnitude": magnitude,
            "return_condition": "她主动靠近/延迟缩短/情绪回升/冲突修复→回主路",
            "impact_on_destination": "埋根基(低压稳定)，创造升温条件",
        }
        detour = {"triggered": detour_triggered, "plan": detour_plan,
                  "heading": "后撤（给空间/留灯）" if detour_triggered else "维持当前航向"}

        # --- fallback_plan（真实失败触发条件+替代动作）---
        fallback_plan = {
            "failure_trigger": ("延迟趋势持续↑(变冷) 且 情绪↓（拥堵持续）"
                                if path_status == "拥堵"
                                else "绕行后仍无主动回归信号"),
            "alternative_action": "降级为低频率留灯(不追问/不施压)，等待主动回归",
            "return_condition": "她主动发消息/延迟缩短→恢复常规节奏",
        }

        # --- multi_path_options ---
        multi_path_options = [
            {"path": "主路径=引导线",
             "status": "阻塞中" if path_status in ("绕行中", "拥堵") else "通畅"},
            {"path": "备选=后撤留灯",
             "status": "绕行中" if path_status == "绕行中" else ("待命" if path_status != "通畅" else "待命")},
        ]

        # --- p_separation_risk（前瞻，低置信度）---
        p_in = snap.get("p_separation_risk_in")
        if p_in is not None:
            try:
                p_separation_risk = {"value": round(float(p_in), 3), "confidence": "low",
                                     "source": "analyze()forecast(轨迹趋势+修复率)"}
            except (TypeError, ValueError):
                p_separation_risk = {"value": None, "confidence": "low",
                                     "source": "未实现：forecast 值非数值"}
        else:
            # 缺失时的诚实推导（低置信度）
            if emotion == "↓" or has_threat:
                p_separation_risk = {"value": 0.40, "confidence": "low",
                                     "source": "未实现forecast；情绪↓或关系威胁→保守估0.40"}
            else:
                p_separation_risk = {"value": 0.15, "confidence": "low",
                                     "source": "未实现forecast；趋势平稳→保守估0.15"}

        # --- threshold_linkage（方向×怀疑链真实规则）---
        dir_argmax = ("后撤" if p_retreat >= p_warm and p_retreat >= p_stable
                      else "升温" if p_warm >= p_stable else "维稳")
        if dir_argmax == "后撤" and sscore >= 60:
            suggestion = "强化引导改变（方向=后撤+suspicion≥60）"
        elif dir_argmax == "升温" and sscore >= 60:
            suggestion = "降级信任预期（方向=升温+suspicion≥60）"
        else:
            suggestion = f"常规应对（方向={dir_argmax}，suspicion={sscore:.0f}<60）"
        threshold_linkage = {
            "direction": dir_argmax,
            "suspicion_level": round(sscore, 1),
            "suggestion": suggestion,
            "note": "规则：后撤+suspicion≥60→强化引导改变；升温+suspicion≥60→降级信任预期；否则常规",
        }

        # --- direction_history（本次 analyze 的真实方向+证据）---
        direction_history = {
            "current_direction": dir_argmax,
            "p_dist": {"后撤": p_retreat, "升温": p_warm, "维稳": p_stable},
            "evidence": evidence or ["无显著方向性证据"],
            "pattern_match": {"case_id": pm_case, "pattern": pm_pattern,
                              "matched": pm_matched},
            "note": "每次罗盘评估记录真实方向与证据(由 direction_probability argmax 定)",
        }

        return {
            "current_position": current_position,
            "direction_probability": direction_probability,
            "path_status": path_status,
            "detour": detour,
            "fallback_plan": fallback_plan,
            "multi_path_options": multi_path_options,
            "p_separation_risk": p_separation_risk,
            "threshold_linkage": threshold_linkage,
            "direction_history": direction_history,
        }

    # ------------------------------------------------------------------ #
    # 北极星（2026-10-13）：主动参与解决 = 主信号，关系满意度 = 参考指标
    # ------------------------------------------------------------------ #
    # 边界（与"回撤≠降频"逻辑解耦）：
    #   * 顺从型同意(compliance)已在 consensus_signal 模块里被排除出主信号；
    #   * 本方法只读展示，绝不修改 gates/drift/aligned/metrics；
    #   * 满意度只是"参考佐证"，不能反过来盖过主信号。
    def _north_star(self, snap: dict) -> dict:
        cons = snap.get("consensus") or {}
        sat = snap.get("relationship_satisfaction") or {}

        # --- 主信号：她主动参与解决 ---
        score = cons.get("active_participation_score")
        reached = cons.get("main_signal_reached")
        trend = cons.get("trend_30d") or {}
        main_signal = {
            "role": "主信号",
            "active_participation_score": score,           # 0-100
            "consensus_event_count": cons.get("consensus_event_count"),
            "consensus_rate": cons.get("consensus_rate"),  # 主动/(主动+顺从)
            "compliance_event_count": cons.get("compliance_event_count"),
            "trend_30d": trend,
            "reached": reached,
            "note": cons.get("main_signal_note"),
            "definition": ("主信号=她主动参与解决（提方案/让步带需求/协作措辞/主动修复）；"
                           "顺从点头不计入"),
        }

        # --- 参考指标：关系满意度 ---
        sat_score = sat.get("score")
        sat_level = sat.get("level")
        reference = {
            "role": "参考指标",
            "relationship_satisfaction": {
                "score": sat_score,        # 0-100
                "level": sat_level,
            },
            "note": "满意度仅作佐证/背离提示，不盖过主信号",
        }

        # --- 两者结合解读 ---
        reading = self._north_star_reading(reached, score, sat_score)
        return {
            "definition": "北极星 = 主动参与解决(主信号) + 关系满意度(参考)",
            "main_signal": main_signal,
            "reference": reference,
            "combined_reading": reading,
        }

    @staticmethod
    def _north_star_reading(reached, score, sat_score) -> str:
        if reached is None or score is None:
            return ("主信号证据不足（她的主动参与事件过少），暂不下结论；"
                    "不拿满意度硬撑判断。")
        sat_low = (sat_score is not None and sat_score < 60)
        if reached and not sat_low:
            return (f"主信号达标（主动参与分 {score}）+ 满意度"
                    f"{sat_score} 佐证一致：她在主动投入解决，关系朝双向奔赴发展。")
        if reached and sat_low:
            return (f"主信号达标（主动参与分 {score}）但满意度 {sat_score} 偏低："
                    f"她在参与解决却仍不快乐，需查满意度根因，勿只看'她理你'。")
        if (not reached) and not sat_low:
            return (f"主信号未达标（主动参与分 {score}），但满意度 {sat_score} 尚可："
                    f"警惕'伪平稳'——她多是顺从点头、少主动投入，表面平静不等于双向奔赴。")
        return (f"主信号未达标（主动参与分 {score}）且满意度 {sat_score} 偏低："
                f"她既少主动投入也不快乐；重点补低压沟通，创造她愿意主动提需求/方案的窗口。")

    # ------------------------------------------------------------------ #
    # 闸门
    # ------------------------------------------------------------------ #
    def _direction_gate(self, snap: dict) -> dict:
        text = self._strategy_text(snap)
        stop = _text_has(text, _RESPECT_STOP)
        if stop:
            return {"pass": True, "level": "pass", "reason": f"合规停止：{stop}"}
        bad = _text_has(text, _WITHDRAW_WORDS)
        if bad:
            return {"pass": False, "level": "fail", "reason": f"含撤离导向：{bad}"}
        good = _text_has(text, _ADVANCE_WORDS)
        if not good:
            return {"pass": False, "level": "warn", "reason": "缺少朝好结局的推进/修复动作"}
        return {"pass": True, "level": "pass", "reason": f"方向朝好结局（{good}）"}

    def _match_gate(self, snap: dict) -> dict:
        text = self._strategy_text(snap)
        attachment = str(snap.get("attachment", "")).lower()
        stage = str(snap.get("stage", ""))
        problems: list[str] = []

        is_avoidant = ("回避" in attachment or "avoid" in attachment)
        is_anxious = ("焦虑" in attachment or "anx" in attachment)

        if is_avoidant:
            pressure = _text_has(text, _PRESSURE_WORDS)
            if pressure:
                problems.append(f"回避型对象却施压（{pressure}）")
            elif not _text_has(text, _SPACE_WORDS):
                problems.append("回避型对象缺少降压/给空间动作")
        if is_anxious:
            bad = _text_has(text, _ANXIOUS_BAD)
            if bad:
                problems.append(f"焦虑型对象却{bad}")
            elif not _text_has(text, _ANXIOUS_GOOD):
                problems.append("焦虑型对象缺少稳定回应/安全感动作")

        # 高风险对象需有边界/反制/引导对等投入
        risk = str(snap.get("risk_level", ""))
        if risk in ("4", "5", "高", "极高") and not _text_has(text,
                ["反制", "边界", "对等投入", "驯服", "引导"]):
            problems.append("高风险对象缺少边界/反制/引导对等投入")

        if problems:
            level = "fail" if any("施压" in p or "却" in p for p in problems) else "warn"
            return {"pass": False, "level": level, "reason": "；".join(problems)}
        note = "策略与依恋/风险匹配"
        if stage:
            note += f"（阶段：{stage}）"
        return {"pass": True, "level": "pass", "reason": note}

    def _truth_gate(self, snap: dict) -> dict:
        problems: list[str] = []
        deception = str(snap.get("deception_risk", "")).lower()
        if deception in ("高", "high", "4", "5"):
            problems.append("欺骗/伪装风险高，勿直接采信对方自述")
        gap = snap.get("words_actions_gap")
        gapv = _clamp01(gap)
        if gapv is not None and gapv >= 0.6:
            problems.append("言行差距大，可能存在表演")
        validity = _clamp01(snap.get("review_validity"))
        sim_weight = _clamp01(snap.get("sim_weight"))
        if validity is not None and validity < 0.5 and sim_weight is not None \
                and sim_weight > 0.55:
            problems.append("预测有效度偏低却给模拟过高权重")
        if problems:
            return {"pass": False, "level": "warn", "reason": "；".join(problems)}
        return {"pass": True, "level": "pass", "reason": "未发现被伪装/低有效度预测误导"}

    # ------------------------------------------------------------------ #
    # 漂移
    # ------------------------------------------------------------------ #
    def _drift(self, snap: dict, metrics: dict, gates: dict) -> dict:
        reasons: list[str] = []
        if gates["direction"]["level"] == "fail":
            reasons.append(gates["direction"]["reason"])
        trend = str(snap.get("affection_trend", "")).lower()
        if trend in ("down", "降", "下滑", "下降"):
            reasons.append("好感趋势下滑")
        # 关键指标连续偏低
        for key in ("mutual", "reciprocity", "repair"):
            v = metrics.get(key)
            if v is not None and v < 0.35:
                reasons.append(f"{SUCCESS_DIMENSIONS[key]}偏低")
        drift = len(reasons) > 0
        level = "high" if gates["direction"]["level"] == "fail" or len(reasons) >= 2 \
            else ("mid" if reasons else "none")
        return {"drift": drift, "level": level,
                "reasons": reasons,
                "suggestion": ("暂停当前推进，回到结局倒推重新对齐"
                               if drift else "方向稳定")}

    # ------------------------------------------------------------------ #
    # 结局倒推
    # ------------------------------------------------------------------ #
    def _backward_gaps(self, metrics: dict) -> list[dict]:
        gaps = []
        for key, name in SUCCESS_DIMENSIONS.items():
            v = metrics.get(key)
            if v is None:
                continue
            if v < 0.5:
                gaps.append({"dimension": key, "name": name, "current": v,
                             "priority": "high" if v < 0.35 else "mid"})
        gaps.sort(key=lambda g: g["current"])
        return gaps

    # ------------------------------------------------------------------ #
    # R3.3-08 AIta 关系卡点诊断（委托独立模块 services/aita_diagnosis.py）
    # ------------------------------------------------------------------ #
    def diagnose_stuck_point(self, person_id: str, snapshot: dict | None = None) -> dict:
        """诊断当前最可能的关系卡点（AIta 式）。

        实现委托给 :class:`services.aita_diagnosis.AItaDiagnosis`，本方法仅做
        兼容封装，保留既有调用方式。返回结构见 AItaDiagnosis.diagnose。
        """
        if getattr(self, "_aita", None) is None:
            self._aita = AItaDiagnosis()
        return self._aita.diagnose(person_id, snapshot)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _strategy_text(snap: dict) -> str:
        parts = [
            str(snap.get("strategy_text", "") or ""),
            str(snap.get("strategy_orientation", "") or ""),
        ]
        return " ".join(parts)

    @staticmethod
    def _verdict(aligned: bool, gates: dict, drift: dict,
                 alignment_score) -> str:
        if aligned:
            base = "方向对齐，继续按结局倒推推进"
        else:
            failed = [name for name, g in gates.items()
                      if g["level"] == "fail"]
            name_map = {"direction": "方向", "match": "匹配", "truth": "真伪"}
            if failed:
                base = "方向偏离：" + "、".join(name_map.get(f, f) for f in failed) + "闸未过"
            elif drift["drift"]:
                base = "出现漂移，需重新对齐"
            else:
                base = "对齐度不足，需补强"
        if alignment_score is not None:
            base += f"（对齐分 {alignment_score}）"
        return base
