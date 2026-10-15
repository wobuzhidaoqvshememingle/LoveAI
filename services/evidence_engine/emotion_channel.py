# -*- coding: utf-8 -*-
"""第3层 情绪通道层（emotion_channel）。

对【每条消息】独立做情绪/共情标注（复用 services/emotion_analyzer.py 的离线
规则分析器），作为独立于关键词标签的情绪通道，喂给层次聚合。情绪通道约可提升
关系级判断 ~6%（sentiment-aware Big Five 约 +6.04%，Affective-NLI）。

诚实：这是逐句规则情绪标注，不使用 LLM（use_llm=False）。
"""
from services.emotion_analyzer import EmotionAnalyzer


class EmotionChannel:
    def __init__(self):
        self._analyzer = EmotionAnalyzer()

    def annotate(self, messages: list) -> list:
        """对 raw_stream 归档后的每条消息追加情绪标注。

        Returns:
            list: 原 messages（含 idx），每条追加
                  {"emotion": label, "intensity": 0-10, "emo_conf": 0-1,
                   "is_question": bool, "polarity": pos/neg/neu}
        """
        pos_labels = {"happy", "love", "gratitude", "excited", "relieved"}
        neg_labels = {"angry", "sad", "anxious", "fear", "disgust", "hurt"}
        for m in messages:
            r = self._analyzer.analyze(m.get("content", ""), use_llm=False)
            label = r.get("label", "neutral")
            m["emotion"] = label
            m["intensity"] = r.get("intensity", 0)
            m["emo_conf"] = r.get("confidence", 0.3)
            m["is_question"] = r.get("is_question", False)
            if label in pos_labels:
                m["polarity"] = "pos"
            elif label in neg_labels:
                m["polarity"] = "neg"
            else:
                m["polarity"] = "neu"
        return messages
