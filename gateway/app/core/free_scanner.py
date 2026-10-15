# -*- coding: utf-8 -*-
"""
OpenRouter 免费模型自动发现与维护 (参考 FreeToken-Bots, fail-closed 设计)。

核心安全原则:
  - 用户输入的模型 ID 必须经过最新扫描验证才能使用
  - 缺少 :free 后缀时自动纠正并大声警告 (否则会路由到付费版本)
  - 不在免费列表里的 ID 直接拒绝, 不试探
  - 永远不自动路由到付费模型

三态分类:
  - eligible:           免费 + 支持 tools + total_parameters >= 100B
  - needs_verification: 免费 + 支持 tools + 参数量未知
  - rejected:           免费但不支持 tools 或参数量太小 (只记录不使用)

原子写入: 临时文件 -> fsync -> os.replace, 防止写一半损坏。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_TTL_SEC = 24 * 3600


# --------------------------------------------------------------------------- #
# 参数大小启发式 (OpenRouter /models 不返回 total_parameters 字段,
# 原逻辑因此永远把所有模型丢进 needs_verification, eligible 池恒空。
# 这里从 model id / name 正则推断参数量级)。
# --------------------------------------------------------------------------- #
import re as _re

# 匹配 "70b" / "2.6b" / "405b" / "120b-a12b" (MoE 总参数) 等
_PARAMS_RE = _re.compile(r"(\d{1,3}(?:\.\d+)?)\s*b\b", _re.IGNORECASE)
# MoE 激活参数 (如 a3b / a12b / a22b) — 用总参数, 忽略激活数
_SMALL_HINTS = ("nano", "mini", "small", "8b", "3b", "1.8b", "4b", "2.6b")
_LARGE_HINTS = ("405b", "235b", "671b", "550b", "120b", "123b", "ultra", "mega", "400b")
# 非 chat 端点 (音乐/分类/元路由), 不进路由候选
_NON_CHAT_PATTERNS = (
    "lyria",            # Google 音乐生成
    "content-safety",   # 安全分类器
    "openrouter/free",  # 元路由, 不是具体模型
    "tts",              # 语音合成
    "embedding",
    "whisper",
)


def _estimate_params_b(model_id: str, name: str) -> Optional[float]:
    """从 model id / name 启发式推断总参数量 (单位 B)。推断不出返回 None。"""
    text = f"{model_id} {name}".lower()
    m = _PARAMS_RE.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    # 关键词兜底
    if any(h in text for h in _LARGE_HINTS):
        return 200.0  # 标记为大模型, 具体数值不精确
    return None


def _is_non_chat(model_id: str, name: str) -> bool:
    text = f"{model_id} {name}".lower()
    return any(p in text for p in _NON_CHAT_PATTERNS)


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class FreeModel:
    model_id: str
    name: str = ""
    context_length: int = 0
    pricing_prompt: float = 0.0
    pricing_completion: float = 0.0
    supports_tools: bool = False
    total_parameters: Optional[float] = None  # 单位 B, None = 未知
    tier: str = "rejected"                    # eligible | needs_verification | rejected
    last_probe_status: str = "never"          # ok | rate_limited | quota_exhausted | offline | error
    last_probe_at: float = 0.0
    probe_fail_streak: int = 0


@dataclass
class ScanReport:
    checked_at: float = 0.0
    eligible: List[FreeModel] = field(default_factory=list)
    needs_verification: List[FreeModel] = field(default_factory=list)
    rejected: List[FreeModel] = field(default_factory=list)
    all_free_ids: Set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "checked_at": self.checked_at,
            "eligible": [self._m_to_dict(m) for m in self.eligible],
            "needs_verification": [self._m_to_dict(m) for m in self.needs_verification],
            "rejected": [self._m_to_dict(m) for m in self.rejected],
            "all_free_ids": sorted(self.all_free_ids),
        }

    @staticmethod
    def _m_to_dict(m: FreeModel) -> dict:
        return {
            "id": m.model_id, "name": m.name, "context_length": m.context_length,
            "pricing_prompt": m.pricing_prompt, "pricing_completion": m.pricing_completion,
            "supports_tools": m.supports_tools, "total_parameters": m.total_parameters,
            "tier": m.tier, "last_probe_status": m.last_probe_status,
            "last_probe_at": m.last_probe_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScanReport":
        r = cls(checked_at=d.get("checked_at", 0.0))
        for key, tier in [("eligible", "eligible"),
                          ("needs_verification", "needs_verification"),
                          ("rejected", "rejected")]:
            for md in d.get(key, []):
                m = FreeModel(
                    model_id=md["id"], name=md.get("name", ""),
                    context_length=md.get("context_length", 0),
                    pricing_prompt=md.get("pricing_prompt", 0),
                    pricing_completion=md.get("pricing_completion", 0),
                    supports_tools=md.get("supports_tools", False),
                    total_parameters=md.get("total_parameters"),
                    tier=tier,
                    last_probe_status=md.get("last_probe_status", "never"),
                    last_probe_at=md.get("last_probe_at", 0.0),
                )
                if tier == "eligible":
                    r.eligible.append(m)
                elif tier == "needs_verification":
                    r.needs_verification.append(m)
                else:
                    r.rejected.append(m)
        r.all_free_ids = set(d.get("all_free_ids", []))
        return r


# --------------------------------------------------------------------------- #
# 原子写入
# --------------------------------------------------------------------------- #
def atomic_write_json(path: Path, data: dict) -> None:
    """临时文件 -> fsync -> os.replace, 权限 600。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


# --------------------------------------------------------------------------- #
# 扫描器
# --------------------------------------------------------------------------- #
class FreeModelScanner:
    """OpenRouter 免费模型扫描 + 探测 + fail-closed 路由。"""

    def __init__(self, cache_path: str | Path, api_key: str = "") -> None:
        self.cache_path = Path(cache_path)
        self.api_key = api_key
        self._report: Optional[ScanReport] = None
        self._load_cache()

    # ------------------------------------------------------------------ #
    # 缓存
    # ------------------------------------------------------------------ #
    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self._report = ScanReport.from_dict(data)
        except Exception as e:
            logger.warning("free scanner cache unreadable: %s", e)
            self._report = None

    def _cache_fresh(self) -> bool:
        return (self._report is not None and
                time.time() - self._report.checked_at < CACHE_TTL_SEC)

    # ------------------------------------------------------------------ #
    # 扫描
    # ------------------------------------------------------------------ #
    async def scan(self, force: bool = False) -> ScanReport:
        """拉 OpenRouter /models, 筛免费, 分三档。"""
        if not force and self._cache_fresh():
            return self._report  # type: ignore[return-value]

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(OPENROUTER_MODELS_URL, headers=headers)
            r.raise_for_status()
            raw = r.json()

        entries = raw.get("data", [])
        report = ScanReport(checked_at=time.time())
        for e in entries:
            pricing = e.get("pricing") or {}
            prompt = float(pricing.get("prompt") or 0)
            completion = float(pricing.get("completion") or 0)
            request = float(pricing.get("request") or 0)
            cache_read = float(pricing.get("input_cache_read") or 0)
            cache_write = float(pricing.get("input_cache_write") or 0)
            # 严格免费: 任何一项不为 0 都不算
            if not (prompt == 0 and completion == 0 and request == 0
                    and cache_read == 0 and cache_write == 0):
                continue
            mid = e.get("id", "")
            if not mid:
                continue
            report.all_free_ids.add(mid)

            # 非 chat 端点 (音乐/分类/元路由) 直接拒收, 不进候选
            if _is_non_chat(mid, e.get("name", "")):
                m = FreeModel(
                    model_id=mid, name=e.get("name", ""),
                    context_length=e.get("context_length") or 0,
                    pricing_prompt=prompt, pricing_completion=completion,
                    supports_tools=False,
                    total_parameters=None,
                )
                m.tier = "rejected"
                m.last_probe_status = "non_chat"
                report.rejected.append(m)
                continue

            supported = e.get("supported_parameters") or []
            tools_ok = "tools" in supported
            # OpenRouter 不返回参数字段, 先用官方字段, 再用启发式
            total_p = e.get("total_parameters") or e.get("parameter_count")
            if isinstance(total_p, (int, float)):
                params_b = float(total_p) / 1e9 if total_p > 1e6 else float(total_p)
            else:
                params_b = _estimate_params_b(mid, e.get("name", ""))

            m = FreeModel(
                model_id=mid,
                name=e.get("name", ""),
                context_length=e.get("context_length") or 0,
                pricing_prompt=prompt, pricing_completion=completion,
                supports_tools=tools_ok,
                total_parameters=params_b,
            )
            # 三态分类 (修复: 启发式补全参数推断, tiny 模型拒收)
            # eligible:           支持 tools + 推断 >= 100B (真·大模型, 适合主力路由)
            # needs_verification: 支持 tools + 参数未知 / 30-100B (可用但需实测)
            # rejected:           不支持 tools / 模型太小 (<15B, 能力不足)
            if not tools_ok:
                m.tier = "rejected"
                m.last_probe_status = "no_tools"
                report.rejected.append(m)
            elif params_b is not None and params_b < 15:
                # 小模型 (2.6B / 8B 级别) 不进路由池, 避免误判为合格
                m.tier = "rejected"
                m.last_probe_status = "too_small"
                report.rejected.append(m)
            elif tools_ok and params_b is not None and params_b >= 100:
                m.tier = "eligible"
                report.eligible.append(m)
            else:
                # tools ok 但参数未知或中等 (15-100B)
                m.tier = "needs_verification"
                report.needs_verification.append(m)

        self._report = report
        atomic_write_json(self.cache_path, report.to_dict())
        logger.info("free scan: %d free, %d eligible, %d needs_verify, %d rejected",
                    len(report.all_free_ids), len(report.eligible),
                    len(report.needs_verification), len(report.rejected))
        return report

    # ------------------------------------------------------------------ #
    # fail-closed 模型 ID 解析
    # ------------------------------------------------------------------ #
    def resolve_free_model(self, requested: str) -> tuple[str, str]:
        """把用户输入的模型 ID 解析成已验证的免费 ID。

        Returns:
            (resolved_id, warning):  resolved_id 是可用的免费 ID; warning 非空表示有纠正。
        Raises:
            ValueError: 模型不在最新免费列表里, 拒绝调用 (fail-closed)。
        """
        if self._report is None:
            raise ValueError("free model list not loaded; run scan first")
        known = self._report.all_free_ids
        if requested in known:
            return requested, ""
        # 自动补 :free 后缀
        suffixed = requested if requested.endswith(":free") else requested + ":free"
        if suffixed in known:
            warn = (f"WARNING: '{requested}' is not a verified-free ID; "
                    f"using verified-free '{suffixed}' instead. "
                    f"Calling '{requested}' directly would use the PAID variant.")
            return suffixed, warn
        raise ValueError(
            f"'{requested}' is not in the latest free model list. "
            f"Refusing to call it to avoid charges. Run a fresh scan.")

    # ------------------------------------------------------------------ #
    # 探测
    # ------------------------------------------------------------------ #
    async def probe_model(self, m: FreeModel) -> str:
        """发一个 PONG 探测, 返回状态: ok | rate_limited | quota_exhausted | offline。"""
        if not self.api_key:
            m.last_probe_status = "no_key"
            return "no_key"
        url = "https://openrouter.ai/api/v1/chat/completions"
        body = {"model": m.model_id,
                "messages": [{"role": "user", "content": "Reply PONG"}],
                "max_tokens": 64}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(url, json=body,
                                      headers={"Authorization": f"Bearer {self.api_key}"})
            m.last_probe_at = time.time()
            if r.status_code == 200:
                m.last_probe_status = "ok"
                m.probe_fail_streak = 0
                return "ok"
            if r.status_code == 429:
                m.last_probe_status = "rate_limited"
                m.probe_fail_streak += 1
                return "rate_limited"
            if r.status_code in (402, 403):
                m.last_probe_status = "quota_exhausted"
                m.probe_fail_streak += 1
                return "quota_exhausted"
            m.last_probe_status = "offline"
            m.probe_fail_streak += 1
            return "offline"
        except Exception as e:
            m.last_probe_status = f"error:{type(e).__name__}"
            m.probe_fail_streak += 1
            return m.last_probe_status

    async def probe_eligible(self) -> Dict[str, int]:
        """探测所有合格池模型。返回统计。"""
        if self._report is None:
            return {}
        stats = {"ok": 0, "rate_limited": 0, "quota_exhausted": 0, "offline": 0, "other": 0}
        for m in self._report.eligible:
            status = await self.probe_model(m)
            stats[status] = stats.get(status, 0) + 1
        # 写回缓存
        atomic_write_json(self.cache_path, self._report.to_dict())
        return stats

    # ------------------------------------------------------------------ #
    # 路由候选
    # ------------------------------------------------------------------ #
    def routing_candidates(self) -> List[str]:
        """按优先级返回可用免费模型 ID。

        修复: 原逻辑要求 last_probe_status=="ok", 但没配 OpenRouter key 时
        probe_model 只会返回 "no_key", 导致合格池恒空、永远降级到 needs_verification。
        现在: eligible 里"未确认失败"的模型 (ok/never/no_key) 都算候选;
        只有明确失败 (rate_limited/quota_exhausted/offline/error) 才排除。
        """
        if self._report is None:
            return []
        _BAD = {"rate_limited", "quota_exhausted", "offline"}
        out: List[str] = []
        for m in self._report.eligible:
            # 探测失败且连续失败 -> 排除; 未探测/无 key/探测 ok -> 可用
            if m.probe_fail_streak > 0 and m.last_probe_status in _BAD:
                continue
            if m.last_probe_status and m.last_probe_status.startswith("error"):
                continue
            out.append(m.model_id)
        if not out:
            # eligible 全挂才降级到 needs_verification (同样过滤明确失败)
            for m in self._report.needs_verification:
                if m.probe_fail_streak > 0 and m.last_probe_status in _BAD:
                    continue
                out.append(m.model_id)
        return out

    # ------------------------------------------------------------------ #
    # diff 报告
    # ------------------------------------------------------------------ #
    def diff_from_previous(self, old: Optional[ScanReport]) -> dict:
        """对比上次扫描, 返回新增/下线/价格变动。"""
        if old is None or self._report is None:
            return {"new": [], "removed": [], "price_changed": []}
        new_ids = set(self._report.all_free_ids)
        old_ids = set(old.all_free_ids)
        return {
            "new": sorted(new_ids - old_ids),
            "removed": sorted(old_ids - new_ids),
            "price_changed": [],  # 简化: 免费模型价格都是 0, 不会变
        }
