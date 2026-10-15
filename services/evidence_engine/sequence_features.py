# -*- coding: utf-8 -*-
"""第2层 序列特征层（sequence_features）。

【确定性/统计性层次序列打分器（rule/statistical hierarchical scorer）】。
实现"词 → 消息 → 话题段 → 关系"的层次聚合：
  - 每条消息生成特征向量：词法命中 / 回避 / 焦虑 / 暗黑 / 防御 / 长度 / 响应角色；
  - 按话题分段聚合（段内均值 + 命中消息数）；
  - 最后到关系级动态（全对方消息汇总）。

诚实声明：这里没有神经注意力/循环网络；用命中计数与比率做层次加权，是
层次序列建模（消息级 > 词级）的确定性近似。词表直接复用 PersonaAnalyzer 的
内层信号词表，保证与已修复的四大检测链一致，不另起炉灶。
"""
from services.persona_analyzer import PersonaAnalyzer


class SequenceFeatures:
    def __init__(self):
        # 复用内层词表（检测架构一致，不重复造词袋）
        self.AVOID = PersonaAnalyzer._AVOID_KW
        self.ANX = PersonaAnalyzer._ANXIOUS_KW
        self.SECURE = PersonaAnalyzer._SECURE_KW
        self.DARK = PersonaAnalyzer._DARK_TRIAD_SIGNALS
        self.DEFENSIVE = PersonaAnalyzer._DEFENSIVE_SIGNALS
        self.EMO = PersonaAnalyzer._EMO_WORDS

    def _msg_vector(self, m: dict) -> dict:
        c = m.get("content", "")
        d = m.get("direction")
        vec = {
            "idx": m["idx"],
            "direction": d,
            "length": len(c),
            "avoid": [kw for kw in self.AVOID if kw in c],
            "anx": [kw for kw in self.ANX if kw in c],
            "secure": [kw for kw in self.SECURE if kw in c],
            "emo": [w for w in self.EMO if w in c],
            "dark": {},
            "defensive": {},
        }
        for cat, kws in self.DARK.items():
            hits = [kw for kw in kws if kw in c]
            if hits:
                vec["dark"][cat] = hits
        for cat, kws in self.DEFENSIVE.items():
            hits = [kw for kw in kws if kw in c]
            if hits:
                vec["defensive"][cat] = hits
        return vec

    def build(self, messages: list, segments: list) -> dict:
        """
        Args:
            messages: raw_stream 归档 + emotion_channel 标注后的消息列表。
            segments: raw_stream 产出的分段索引。

        Returns:
            dict: {
              "msg_vectors": [...],          # 每条消息的特征向量
              "segment_features": [...],     # 段级聚合
              "relationship": {...},         # 关系级（对方消息汇总）
            }
        """
        msg_vectors = [self._msg_vector(m) for m in messages]
        # 把方向/情绪挂回向量，便于段聚合
        for v, m in zip(msg_vectors, messages):
            v["polarity"] = m.get("polarity", "neu")
            v["emotion"] = m.get("emotion", "neutral")

        segment_features = []
        for seg in segments:
            vs = msg_vectors[seg["start_idx"]:seg["end_idx"] + 1]
            segment_features.append({
                "seg_id": seg["seg_id"],
                "n_messages": len(vs),
                "n_avoid": sum(1 for v in vs if v["avoid"]),
                "n_anx": sum(1 for v in vs if v["anx"]),
                "n_dark": sum(1 for v in vs if v["dark"]),
                "n_defensive": sum(1 for v in vs if v["defensive"]),
                "neg_ratio": self._ratio([v for v in vs if v["polarity"] == "neg"], vs),
                "pos_ratio": self._ratio([v for v in vs if v["polarity"] == "pos"], vs),
            })

        other_vs = [v for v in msg_vectors if v["direction"] == "received"]
        relationship = {
            "n_other": len(other_vs),
            "avoid_hits": self._collect(other_vs, "avoid"),
            "anx_hits": self._collect(other_vs, "anx"),
            "dark_hits": self._collect_dark(other_vs),
            "defensive_hits": self._collect_defensive(other_vs),
            "neg_ratio": self._ratio([v for v in other_vs if v["polarity"] == "neg"], other_vs),
            "pos_ratio": self._ratio([v for v in other_vs if v["polarity"] == "pos"], other_vs),
            "emo_density": self._ratio([v for v in other_vs if v["emo"]], other_vs),
        }
        return {"msg_vectors": msg_vectors,
                "segment_features": segment_features,
                "relationship": relationship}

    @staticmethod
    def _ratio(num, den):
        return round(len(num) / max(1, len(den)), 3)

    @staticmethod
    def _collect(vs, key):
        out = {}
        for v in vs:
            for kw in v[key]:
                out.setdefault(kw, []).append(v["idx"])
        return out

    @staticmethod
    def _collect_dark(vs):
        out = {}
        for v in vs:
            for cat, kws in v["dark"].items():
                for kw in kws:
                    out.setdefault(cat, {}).setdefault(kw, []).append(v["idx"])
        return out

    @staticmethod
    def _collect_defensive(vs):
        out = {}
        for v in vs:
            for cat, kws in v["defensive"].items():
                for kw in kws:
                    out.setdefault(cat, {}).setdefault(kw, []).append(v["idx"])
        return out
