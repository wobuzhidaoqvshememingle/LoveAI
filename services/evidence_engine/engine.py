# -*- coding: utf-8 -*-
"""六层统一入口：EvidenceProfileEngine。

消费 PersonaAnalyzer 的画像作为输入之一，并自行重算消息级特征（不修改
persona_analyzer.py 的四大修复逻辑）。输出证据锚定的标签 + 置信 + 二元互动模式。
"""
from datetime import datetime

from .raw_stream import RawStream
from .emotion_channel import EmotionChannel
from .sequence_features import SequenceFeatures
from .evidence_anchor import EvidenceAnchor
from .dyadic_pattern import DyadicPattern
from .confidence import Confidence


class EvidenceProfileEngine:
    """证据锚定的层次序列侧写引擎（六层）。"""

    def __init__(self, db=None):
        self.db = db
        self._raw = RawStream()
        self._emo = EmotionChannel()
        self._seq = SequenceFeatures()
        self._anchor = EvidenceAnchor()
        self._dyad = DyadicPattern()
        self._conf = Confidence()

    def _fetch_messages(self, person_id):
        if not self.db:
            return []
        return self.db.fetch_all(
            "SELECT direction, content, msg_type, created_at FROM chat_messages "
            "WHERE person_id=:pid ORDER BY id ASC",
            {"pid": person_id})

    def analyze(self, person_id: str, profile: dict = None,
                messages: list = None) -> dict:
        """
        Args:
            person_id: 人物 ID。
            profile: PersonaAnalyzer.analyze() 的画像（可选，作为输入之一消费）。
            messages: 直接传入消息列表（测试/无库时用）；为 None 则从 db 拉取。

        Returns:
            dict: 六层聚合结果，含 labels(带 evidence+confidence)、dyadic、
                  confidence、内层画像摘要。
        """
        msgs = messages if messages is not None else self._fetch_messages(person_id)

        # 第1层 原始流
        stream = self._raw.build(msgs)
        ordered = stream["messages"]

        # 第3层 情绪通道（独立通道，逐句标注）
        self._emo.annotate(ordered)

        # 第2层 序列特征（词→消息→段→关系）
        seq = self._seq.build(ordered, stream["segments"])

        # 第6层 置信（先算样本量/跨度/分歧，供证据层透传）
        span_days = self._span_days(ordered)
        # 分歧证据计数：对方否定现实/推卸/冷战命中的不同消息数
        n_disagreement = self._count_disagreement(seq)
        repeat_patterns = len(seq["relationship"]["dark_hits"]) + \
            len(seq["relationship"]["defensive_hits"])
        conf = self._conf.calibrate(stream["n_messages"], span_days,
                                    n_disagreement, repeat_patterns)

        # 第4层 证据锚定
        anchored = self._anchor.bind(seq["relationship"], seq["msg_vectors"],
                                     ordered, conf["value"])

        # 第5层 二元互动
        dyadic = self._dyad.detect(ordered)

        return {
            "person_id": person_id,
            "n_messages": stream["n_messages"],
            "segments": stream["segments"],
            "confidence": conf,
            "labels": anchored["labels"],
            "suppressed_labels": anchored["suppressed"],
            "dyadic_patterns": dyadic,
            "relationship_features": {
                "neg_ratio": seq["relationship"]["neg_ratio"],
                "pos_ratio": seq["relationship"]["pos_ratio"],
                "emo_density": seq["relationship"]["emo_density"],
            },
            # 消费内层画像（不改动其逻辑，仅引用）
            "inner_attachment": (profile or {}).get("attachment", {}),
            "inner_conflict_style": (profile or {}).get("conflict_style", {}),
            "engine_note": "rule/statistical hierarchical scorer；证据锚定为确定性"
                           "规则，病理标签仅作倾向提示，不替代临床诊断",
        }

    @staticmethod
    def _span_days(ordered):
        dts = [m["dt"] for m in ordered if m.get("dt")]
        if len(dts) < 2:
            return 0.0
        return (max(dts) - min(dts)).total_seconds() / 86400.0

    @staticmethod
    def _count_disagreement(seq):
        rel = seq["relationship"]
        idx = set()
        for cat, mp in rel.get("dark_hits", {}).items():
            for k, idxs in mp.items():
                idx.update(idxs)
        for cat, mp in rel.get("defensive_hits", {}).items():
            for k, idxs in mp.items():
                idx.update(idxs)
        return len(idx)
