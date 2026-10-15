# -*- coding: utf-8 -*-
"""
路由引擎。

输入: ChatCompletionRequest + 已注册 provider 集合。
输出: 有序候选列表 [(provider, model, layer), ...]。

6 种策略:
  priority       按 ProviderConfig.priority 升序 (数字越小越优先)
  cost_first     按 cost_tier (free<low<high) 升序, 再按 priority
  latency_first  按最近 5min 平均延迟升序 (无历史记 1000ms 中性值)
  quality_first  按 cost_tier 降序 (贵的当质量好), 再按 priority
  load_balance   内存轮询 (round-robin)
  auto           综合评分: 健康 + 成本 + 延迟 + 优先级

Fallback 4 层 (由候选列表顺序体现):
  layer 1  首选 provider (gw_preferred_provider 指定, 或同模型最优)
  layer 2  同模型其他 provider
  layer 3  降级类似模型 (同模型族, 如 gpt-4o -> gpt-4o-mini)
  layer 4  最小可用模型 (任意 provider 的任意模型, 优先 free)
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from app.config import GatewayConfig, ProviderConfig
from app.db import Database
from app.models import ChatCompletionRequest


# --------------------------------------------------------------------------- #
# 候选
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Candidate:
    provider: str
    model: str
    layer: int  # 1 / 2 / 3 / 4


_COST_RANK = {"free": 0, "low": 1, "high": 2}


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #
class Router:
    def __init__(self, cfg: GatewayConfig, db: Database) -> None:
        self.cfg = cfg
        self.db = db
        self._rr_lock = threading.Lock()
        self._rr_index = 0

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def route(
        self,
        req: ChatCompletionRequest,
        registered: Optional[Set[str]] = None,
    ) -> List[Candidate]:
        """生成有序候选列表。registered: 已注册的 provider name 集合 (None=cfg.providers)。"""
        registered = registered if registered is not None else {p.name for p in self.cfg.providers}
        strategy = (req.gw_strategy or self.cfg.default_strategy or "auto").lower()

        # 所有 enabled + registered 的 provider
        providers = [
            p for p in self.cfg.providers
            if p.enabled and p.name in registered
        ]
        if not providers:
            return []

        model = req.model or ""

        # 同模型的 provider 列表
        model_index = self.cfg.models_index()
        same_model: List[ProviderConfig] = [
            p for p in providers if model in p.models
        ]

        # 1) 按策略排同模型 provider
        same_model_ranked = self._rank(providers=same_model, strategy=strategy)

        # 2) 拆分 layer 1 / 2
        candidates: List[Candidate] = []
        preferred = req.gw_preferred_provider
        if preferred:
            preferred_list = [p for p in same_model_ranked if p.name == preferred]
            rest = [p for p in same_model_ranked if p.name != preferred]
            for p in preferred_list:
                candidates.append(Candidate(p.name, model, 1))
            for p in rest:
                candidates.append(Candidate(p.name, model, 2))
        else:
            for i, p in enumerate(same_model_ranked):
                candidates.append(Candidate(p.name, model, 1 if i == 0 else 2))

        # 3) layer 3: 类似模型 (其他 provider, 其他模型族)
        family = self._model_family(model)
        similar_seen: set[tuple[str, str]] = set()
        for p in providers:
            if p.name in {c.provider for c in candidates}:
                continue
            for m in p.models:
                if m == model:
                    continue
                if family and self._model_family(m) == family:
                    key = (p.name, m)
                    if key not in similar_seen:
                        similar_seen.add(key)
                        candidates.append(Candidate(p.name, m, 3))

        # 4) layer 4: 最小可用 (任意 provider 任意模型, 优先 free/low)
        used = {(c.provider, c.model) for c in candidates}
        minimal_pool = [
            (p, m)
            for p in sorted(providers, key=lambda x: (_COST_RANK.get(x.cost_tier, 1), x.priority))
            for m in p.models
            if (p.name, m) not in used
        ]
        for p, m in minimal_pool:
            candidates.append(Candidate(p.name, m, 4))

        return candidates

    # ------------------------------------------------------------------ #
    # 排序核心
    # ------------------------------------------------------------------ #
    def _rank(self, *, providers: List[ProviderConfig], strategy: str) -> List[ProviderConfig]:
        if not providers:
            return []

        if strategy == "priority":
            return sorted(providers, key=lambda p: (p.priority, p.name))

        if strategy == "cost_first":
            return sorted(providers, key=lambda p: (_COST_RANK.get(p.cost_tier, 1), p.priority, p.name))

        if strategy == "quality_first":
            # 贵的优先
            return sorted(providers, key=lambda p: (-_COST_RANK.get(p.cost_tier, 1), p.priority, p.name))

        if strategy == "latency_first":
            scored = []
            for p in providers:
                snap = self.db.provider_health_snapshot(p.name, window_sec=300)
                lat = snap["avg_latency_ms"] if snap["total"] > 0 else 1000.0
                scored.append((lat, p.priority, p.name, p))
            scored.sort()
            return [s[3] for s in scored]

        if strategy == "load_balance":
            with self._rr_lock:
                n = len(providers)
                start = self._rr_index % n
                self._rr_index = (self._rr_index + 1) % n
            ordered = providers[start:] + providers[:start]
            return ordered

        # auto (默认): 健康分 + 成本 + 延迟 + 优先级
        scored = []
        for p in providers:
            snap = self.db.provider_health_snapshot(p.name, window_sec=300)
            cost = _COST_RANK.get(p.cost_tier, 1)
            lat = snap["avg_latency_ms"] if snap["total"] > 0 else 800.0
            # 成功率惩罚
            sr = snap["success_rate"]
            score = cost * 1000.0 + lat + (1.0 - sr) * 5000.0 + p.priority
            scored.append((score, p.name, p))
        scored.sort()
        return [s[2] for s in scored]

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _model_family(model: str) -> str:
        """'gpt-4o-2024-08' -> 'gpt-4o'; 'claude-3-5-sonnet' -> 'claude-3'。"""
        if not model:
            return ""
        # 去掉日期后缀
        parts = model.split("-")
        if not parts:
            return model
        head = parts[0]
        if len(parts) > 1:
            # 保留两段族名 (如 claude-3, gpt-4)
            family = "-".join(parts[:2]) if parts[1].replace(".", "").isdigit() else head
        else:
            family = head
        return family.lower()
