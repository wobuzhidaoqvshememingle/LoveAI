# -*- coding: utf-8 -*-
"""第4层 证据锚定层（evidence_anchor，核心）。

每个标签采用"NLI 式蕴含验证"的确定性近似：**标签成立必须在原文找到 >=1 条
满足蕴含条件的证据句**（特征/关键词命中且语境相符），并把
【证据原文 quote + 时间点 ts + 消息序号 idx】绑定到该标签。
找不到证据句的标签一律不输出（或降级为"待观察"）。

诚实：这是确定性证据绑定规则，不是神经 NLI。它交付论文要求的功能性质——
可追溯、不做无依据诊断。病理标签（NPD/BPD）按"倾向性 + 置信度 + 证据"输出，
仅作倾向提示，明确不替代临床诊断。
"""


class EvidenceAnchor:
    # 病理标签的临床免责说明
    _CLINICAL_NOTE = "倾向性识别（确定性证据规则），不替代临床诊断"

    # 反证（counter_evidence）关键词：与各倾向相反的表达，命中即作为反证绑定。
    # P-05：每个标签必须绑定 原文引用 + n + 区间 + 反证。
    _WARM_WORDS = ("我想你", "爱你", "宝贝", "在乎", "心疼", "想见你", "抱抱", "乖乖")
    _CALM_COMMIT_WORDS = ("我们好好的", "别多想", "我一直都在", "说开了就好", "踏实")

    def bind(self, rel: dict, msg_vectors: list, msgs: list,
             overall_conf: float) -> dict:
        """
        Args:
            rel: sequence_features 的 relationship 汇总。
            msg_vectors: 每条消息的特征向量。
            msgs: raw_stream 归档消息（含 ts/content/direction）。
            overall_conf: confidence 层给出的总置信 0-1。

        Returns:
            dict: {
              "labels": {label_key: {tendency, confidence, evidence:[...], note}},
              "suppressed": [labels_without_evidence],
            }
        """
        by_idx = {m["idx"]: m for m in msgs}
        labels = {}

        def _ev(kw_index_map):
            """把 {kw: [idx,...]} 转成证据句列表（去重、最多3条）。"""
            out, seen = [], set()
            for kw, idxs in kw_index_map.items():
                for ix in idxs:
                    if ix in seen:
                        continue
                    seen.add(ix)
                    m = by_idx.get(ix, {})
                    out.append({"quote": m.get("content", ""),
                                "ts": m.get("ts"), "idx": ix, "via": kw})
                    if len(out) >= 3:
                        return out
            return out

        def _cold_ev():
            """内容情感退缩证据：含回避/防御/暗黑但非操纵的对方消息。"""
            out, seen = [], set()
            for v in msg_vectors:
                if v["direction"] != "received":
                    continue
                is_cold = (v["avoid"] or v["defensive"]
                           or any(c not in ("操纵",) for c in v["dark"]))
                if is_cold and v["idx"] not in seen:
                    seen.add(v["idx"])
                    m = by_idx.get(v["idx"], {})
                    out.append({"quote": m.get("content", ""),
                                "ts": m.get("ts"), "idx": v["idx"],
                                "via": "内容退缩"})
                    if len(out) >= 3:
                        break
            return out

        def _counter(anti_words):
            """反证（P-05）：对方消息中出现与该倾向相反的表达，最多2条。"""
            out, seen = [], set()
            for v in msg_vectors:
                if v["direction"] != "received":
                    continue
                m = by_idx.get(v["idx"], {})
                content = m.get("content", "")
                if any(w in content for w in anti_words) and v["idx"] not in seen:
                    seen.add(v["idx"])
                    out.append({"quote": content, "ts": m.get("ts"),
                                "idx": v["idx"], "via": "反证(相反表达)"})
                    if len(out) >= 2:
                        break
            return out

        # ---- 1. 回避倾向 ----
        avoid_ev = _ev(rel["avoid_hits"]) or _cold_ev()
        if avoid_ev:
            labels["依恋-回避倾向"] = self._mk(
                "明显" if len(avoid_ev) >= 2 else "存在",
                overall_conf, avoid_ev, "情感退缩/要空间/打太极",
                counter_evidence=_counter(self._WARM_WORDS))

        # ---- 2. 焦虑倾向（BPD 抛弃恐惧反复确认）----
        anx_ev = _ev(rel["anx_hits"])
        if anx_ev:
            labels["依恋-焦虑倾向"] = self._mk(
                "明显" if len(anx_ev) >= 2 else "存在",
                overall_conf, anx_ev, "怕抛弃/反复确认/冲动强迫",
                counter_evidence=_counter(self._CALM_COMMIT_WORDS))

        # ---- 3. 暗黑-自恋 ----
        narcissism_ev = _ev(rel["dark_hits"].get("自恋", {}))
        if narcissism_ev:
            labels["暗黑-自恋倾向"] = self._mk(
                "存在", overall_conf, narcissism_ev,
                "特权感/要求被优先/贬低", self._CLINICAL_NOTE,
                counter_evidence=_counter(self._WARM_WORDS))

        # ---- 4. 暗黑-操纵（BPD 抗议也在此标注，但归为焦虑抗议而非回避）----
        manip_ev = _ev(rel["dark_hits"].get("操纵", {}))
        if manip_ev:
            labels["暗黑-操纵/施压"] = self._mk(
                "存在", overall_conf, manip_ev,
                "施压/以退为进（BPD 语境下多为焦虑性抗议）", self._CLINICAL_NOTE,
                counter_evidence=_counter(self._WARM_WORDS))

        # ---- 5. 煤气灯/否定现实/贬低感受（防御）----
        def_ev = _ev({k: idxs for cat in rel["defensive_hits"].values()
                      for k, idxs in cat.items()})
        if def_ev:
            labels["煤气灯/防御性贬低"] = self._mk(
                "存在", overall_conf, def_ev,
                "否定现实/贬低感受/推卸责任", self._CLINICAL_NOTE,
                counter_evidence=_counter(self._CALM_COMMIT_WORDS))

        # ---- 6. 情绪不稳定倾向（负向情绪占比 + 焦虑证据同时存在）----
        if rel["neg_ratio"] >= 0.2 and anx_ev:
            labels["情绪不稳定倾向"] = self._mk(
                "存在", overall_conf, anx_ev[:2],
                "负向情绪主导且伴随反复确认", self._CLINICAL_NOTE,
                counter_evidence=_counter(self._CALM_COMMIT_WORDS))

        # 这些标签若无证据则被抑制
        suppressed = [k for k in
                      ("依恋-回避倾向", "依恋-焦虑倾向", "暗黑-自恋倾向",
                       "暗黑-操纵/施压", "煤气灯/防御性贬低", "情绪不稳定倾向")
                      if k not in labels]
        return {"labels": labels, "suppressed": suppressed}

    @staticmethod
    def _mk(tendency, conf, evidence, basis, note=None, counter_evidence=None):
        # P-05：每个标签绑定 原文引用(evidence) + n + 区间(interval) + 反证。
        ts_vals = [e.get("ts") for e in evidence if e.get("ts") is not None]
        return {
            "tendency": tendency,
            "confidence": conf,
            "basis": basis,
            "evidence": evidence,
            "n": len(evidence),
            "interval": {
                "start": min(ts_vals) if ts_vals else None,
                "end": max(ts_vals) if ts_vals else None,
            },
            "counter_evidence": list(counter_evidence or []),
            "note": note or "确定性证据规则",
        }
