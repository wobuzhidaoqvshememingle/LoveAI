# -*- coding: utf-8 -*-
"""第6层 概率置信层（confidence）。

按样本量做校准并诚实标注，采用"贝叶斯式"经验更新（非真实后验，是确定性校准表）：
  - <200 条：约 55%；
  - 满 1 个月时间跨度 且 >=3 次分歧/冲突证据：约 70%；
  - 再多轮反馈/重复模式：约 80%（封顶）。

诚实天花板：纯文本人格识别真实准确率 60-75%，因此任何情况下总评置信
不得越过该诚实区间（封顶 0.80）。置信透传到每个标签与总评。

P-01 加固（v3.1）：calibrate() 新增可选参数 n_eff / calibrated / downgraded，
在既有粗校准结果上再叠加"证据量→置信上限硬封顶"（见 services.confidence_cap）。
不传 n_eff 时行为与 v3.0 完全一致（向后兼容，不推翻已有逻辑）。
"""

from services.confidence_cap import apply_evidence_cap


class Confidence:
    HARD_CEILING = 0.80          # 纯文本 60-75% 天花板对应的诚实上限
    MIN_SAMPLES_BASE = 0.55      # <200 条约 55%

    def calibrate(self, n_messages: int, span_days: float = 0.0,
                  n_disagreement: int = 0, repeat_patterns: int = 0,
                  n_eff: float = None, calibrated: bool = False,
                  downgraded: bool = False) -> dict:
        """
        Args:
            n_messages: 有效消息数。
            span_days: 时间跨度（天）。
            n_disagreement: 分歧/冲突/反复证据次数（否定现实/冷战/推拉等）。
            repeat_patterns: 重复出现的稳定模式数。
            n_eff: 时间衰减后的有效证据量（P-01）；为 None 时不施加硬封顶（向后兼容）。
            calibrated: 是否已通过 P-02 校准达标（ECE<=0.10 稳态线）。
            downgraded: 是否命中 P-04 高压降档。

        Returns:
            dict: {value 0-1, level 低/中/高, basis, ceiling_note}
        """
        if n_messages < 200:
            base = self.MIN_SAMPLES_BASE
        else:
            base = 0.65

        # 满 1 个月 + 分歧证据 → 约 70%
        if span_days >= 28 and n_disagreement >= 3:
            base = max(base, 0.70)
        # 多轮重复模式 → 再校准，但封顶
        if repeat_patterns >= 3:
            base = max(base, 0.75)
        if repeat_patterns >= 6:
            base = 0.80

        value = round(min(base, self.HARD_CEILING), 2)

        # P-01 硬封顶：仅在传入 n_eff 时叠加；不传则保持 v3.0 原行为。
        p01 = None
        if n_eff is not None:
            p01 = apply_evidence_cap(value, n_eff,
                                   calibrated=calibrated, downgraded=downgraded)
            value = p01["capped"]
            if not p01["drives_strategy"]:
                value = 0.0

        level = "高" if value >= 0.75 else ("中" if value >= 0.65 else "低")
        if p01 is not None and not p01["drives_strategy"]:
            level = "假设"
        result = {
            "value": value,
            "level": level,
            "basis": (f"消息数={n_messages}, 跨度={span_days:.0f}天, "
                      f"分歧证据={n_disagreement}, 重复模式={repeat_patterns}"),
            "ceiling_note": "纯文本人格识别真实准确率约60-75%，本置信不越此天花板，"
                            "病理倾向仅作提示、不替代临床诊断",
        }
        if p01 is not None:
            result["p01"] = p01
        return result
