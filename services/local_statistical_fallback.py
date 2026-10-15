# -*- coding: utf-8 -*-
"""
LoveAI v3.1 加固补丁 P-10：无 LLM 纯本地韧性末级兜底。

定位：降级链末级——当所有生成模型（云端 LLM、本地小模型、数字分身规则引擎）
均不可用时，仍以"收缩后的历史概率 + 情境检索"输出统计型反应分布，
标注"无生成模型，仅统计预测"，不硬失败、不抛异常给上层。

设计原则（纯函数、离线确定性、可单测）：
  1. 不 import 任何 LLM / chroma / DB / PySide6；所有数据通过构造或参数注入。
  2. 收缩采用经验贝叶斯式加性平滑（Laplace / Dirichlet-multinomial 收缩）：
       shrunk_p[k] = (counts[k] + prior_strength * prior[k]) / (N + prior_strength)
     历史越多越相信自己，历史越少越向人群先验靠拢。
  3. 情境检索：由调用方注入"检索器"（callable: context_features -> list of
     (reaction_category, weight)）；未注入或检索为空时，回退全局历史先验。
  4. 证据不足（样本量 < MIN_EVIDENCE）时强降档：置信 level 置"极低"，
     并在输出里强标 "evidence_insufficient": true。
  5. 输出永远归一化（概率和=1，浮点容差 1e-9），永远带 annotation 字段。

反应类别沿用 twin_sim_pipeline 的 Y1-Y7 离散类，保证下游可对接：
  Y1 积极推进 / Y2 正向接纳 / Y3 中性 / Y4 敷衍回避 /
  Y5 防御冲突 / Y6 已读不回 / Y7 未读或无回应
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

# ====================================================================== #
# 离散反应类别（与 twin_sim_pipeline.Y_LABELS 对齐，只读引用避免循环依赖）
# ====================================================================== #
REACTION_CATEGORIES: Tuple[str, ...] = (
    "Y1", "Y2", "Y3", "Y4", "Y5", "Y6", "Y7",
)
REACTION_LABELS: Dict[str, str] = {
    "Y1": "积极推进",
    "Y2": "正向接纳",
    "Y3": "中性",
    "Y4": "敷衍回避",
    "Y5": "防御冲突",
    "Y6": "已读不回",
    "Y7": "未读或无回应",
}

# 人群先验（冷启动先验；真实人群分布由校准层后续覆盖，这里给一个保守偏"中性"的默认）
# 依据：日常关系中"中性/敷衍/不回"占比偏高，"积极推进/正向接纳"偏低。
DEFAULT_POPULATION_PRIOR: Dict[str, float] = {
    "Y1": 0.08,
    "Y2": 0.12,
    "Y3": 0.20,
    "Y4": 0.22,
    "Y5": 0.10,
    "Y6": 0.16,
    "Y7": 0.12,
}

# 收缩强度（等价于伪计数；越大越向先验收拢）
DEFAULT_PRIOR_STRENGTH: float = 20.0
# 证据不足阈值（低于此样本量强降档）
MIN_EVIDENCE: int = 10
# 归一化浮点容差
_PROB_EPS: float = 1e-9

# 输出里固定的标注文案
NO_GENERATIVE_ANNOTATION: str = "无生成模型，仅统计预测"


# ====================================================================== #
# 纯函数工具
# ====================================================================== #
def normalize_probs(probs: Dict[str, float]) -> Dict[str, float]:
    """将任意类别->数值的 dict 归一化为概率（和=1）。全 0 时回退均匀分布。"""
    total = sum(v for v in probs.values() if v > 0)
    if total <= 0:
        n = len(probs)
        if n == 0:
            return {}
        return {k: 1.0 / n for k in probs}
    out = {k: max(0.0, v) / total for k, v in probs.items()}
    # 消除浮点残差，让和精确为 1
    s = sum(out.values())
    if abs(s - 1.0) > _PROB_EPS and s > 0:
        # 把残差塞到最大项
        max_k = max(out, key=lambda k: out[k])
        out[max_k] += (1.0 - s)
    return out


def shrink_counts(
    counts: Dict[str, float],
    prior: Optional[Dict[str, float]] = None,
    strength: float = DEFAULT_PRIOR_STRENGTH,
) -> Dict[str, float]:
    """经验贝叶斯收缩：把历史计数向人群先验收拢。

    shrunk[k] = (counts[k] + strength * prior[k]) / (N + strength)

    返回已归一化的概率分布。
    """
    if prior is None:
        prior = dict(DEFAULT_POPULATION_PRIOR)
    # 补全缺失类别
    cats = set(counts.keys()) | set(prior.keys())
    merged: Dict[str, float] = {}
    for c in cats:
        cnt = float(counts.get(c, 0.0))
        pr = float(prior.get(c, 1.0 / len(REACTION_CATEGORIES)))
        merged[c] = cnt + strength * pr
    return normalize_probs(merged)


def align_to_categories(
    dist: Dict[str, float],
    categories: Tuple[str, ...] = REACTION_CATEGORIES,
) -> Dict[str, float]:
    """把任意分布补全/裁剪到标准 REACTION_CATEGORIES 并归一化。"""
    merged: Dict[str, float] = {c: float(dist.get(c, 0.0)) for c in categories}
    return normalize_probs(merged)


# ====================================================================== #
# 主类
# ====================================================================== #
class LocalStatisticalFallback:
    """P-10 末级纯统计兜底生成器。

    用法（最小示例）::

        fb = LocalStatisticalFallback()
        out = fb.predict(context_features={"scene": "conflict"})
        # out["distribution"]["Y4"] == 0.22-ish
        # out["annotation"] == "无生成模型，仅统计预测"

    注入历史与检索器::

        fb = LocalStatisticalFallback(
            history_counts={"Y1": 5, "Y4": 20, ...},
            retriever=lambda ctx: [("Y1", 0.4), ("Y2", 0.3)],
            population_prior={"Y1": 0.1, ...},
        )
    """

    def __init__(
        self,
        history_counts: Optional[Dict[str, float]] = None,
        retriever: Optional[Callable[[Dict[str, Any]], List[Tuple[str, float]]]] = None,
        population_prior: Optional[Dict[str, float]] = None,
        prior_strength: float = DEFAULT_PRIOR_STRENGTH,
        min_evidence: int = MIN_EVIDENCE,
    ) -> None:
        self.history_counts: Dict[str, float] = dict(history_counts or {})
        self.retriever = retriever
        self.population_prior: Dict[str, float] = align_to_categories(
            population_prior or DEFAULT_POPULATION_PRIOR
        )
        self.prior_strength = float(prior_strength)
        self.min_evidence = int(min_evidence)

    # ------------------------------------------------------------------ #
    # 核心入口
    # ------------------------------------------------------------------ #
    def predict(self, context_features: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """输入情境特征，输出统计型反应分布（永远不抛异常）。

        Returns:
            dict: 结构如下
            {
              "distribution": {cat: prob, ...},   # 概率和=1
              "labels": {cat: 中文标签},
              "top_reaction": "Y4",
              "top_prob": 0.25,
              "level": "极低" | "低" | "中" | "高",
              "evidence_insufficient": bool,
              "n_history": int,
              "n_retrieved": int,
              "annotation": "无生成模型，仅统计预测",
              "context_used": {...},
              "shrunk": bool,
            }
        """
        ctx = dict(context_features or {})

        # 1) 检索：情境特征 -> 加权类别列表
        retrieved: List[Tuple[str, float]] = []
        try:
            if self.retriever is not None:
                retrieved = list(self.retriever(ctx) or [])
        except Exception:
            # 检索器本身坏了也不能拖垮兜底
            retrieved = []

        # 2) 合并历史计数 + 检索权重（检索权重作为额外"软计数"）
        combined: Dict[str, float] = dict(self.history_counts)
        for cat, w in retrieved:
            if cat in REACTION_CATEGORIES:
                combined[cat] = combined.get(cat, 0.0) + float(w)

        # 3) 收缩到人群先验
        n_history = sum(float(v) for v in self.history_counts.values())
        shrunk = shrink_counts(
            combined,
            prior=self.population_prior,
            strength=self.prior_strength,
        )
        distribution = align_to_categories(shrunk)

        # 4) 证据不足判定：原始历史样本量（不含检索软计数）
        evidence_insufficient = n_history < self.min_evidence
        if evidence_insufficient:
            level = "极低"
        else:
            # 用分布熵近似置信度：越尖锐置信越高
            entropy = -sum(p * math.log(p + 1e-12) for p in distribution.values() if p > 0)
            max_entropy = math.log(len(REACTION_CATEGORIES))
            sharpness = 1.0 - (entropy / max_entropy)
            if sharpness >= 0.35:
                level = "中"
            elif sharpness >= 0.15:
                level = "低"
            else:
                level = "极低"

        top_cat = max(distribution, key=lambda k: distribution[k])
        return {
            "distribution": distribution,
            "labels": dict(REACTION_LABELS),
            "top_reaction": top_cat,
            "top_prob": round(distribution[top_cat], 6),
            "level": level,
            "evidence_insufficient": evidence_insufficient,
            "n_history": int(n_history),
            "n_retrieved": len(retrieved),
            "annotation": NO_GENERATIVE_ANNOTATION,
            "context_used": ctx,
            "shrunk": True,
        }

    # ------------------------------------------------------------------ #
    # 便捷：直接返回 top1 文本（给 UI / gateway 接线用）
    # ------------------------------------------------------------------ #
    def predict_top_text(self, context_features: Optional[Dict[str, Any]] = None) -> str:
        out = self.predict(context_features)
        cat = out["top_reaction"]
        return f"[{NO_GENERATIVE_ANNOTATION}] {REACTION_LABELS.get(cat, cat)}"
