# -*- coding: utf-8 -*-
"""证据锚定的层次序列侧写引擎（六层）。

设计取向（诚实声明）：
  - 本机未捆绑神经 NLI / RNN / Transformer；本包实现为【确定性/统计性的
    层次序列打分器（rule/statistical hierarchical scorer）】，不声称学到了
    注意力或神经网络。
  - 学术依据：纯文本人格识别真实准确率天花板约 60-75%（Essays/myPersonality；
    GPT-3 生成 71%/真实 65%；中文 F1 63-66%；依恋语音 59-83%）；层次序列建模
    优于词袋（消息级 > 词级）；情绪/共情独立通道约 +6%；证据锚定=NLI 蕴含验证
    的确定性近似（标签成立必须有原文证据句）。
  - 病理标签（NPD/BPD）只输出"倾向性 + 置信度 + 证据"，作倾向提示，
    不替代临床诊断。
"""

from .engine import EvidenceProfileEngine  # noqa: F401

__all__ = ["EvidenceProfileEngine"]
