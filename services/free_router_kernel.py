# -*- coding: utf-8 -*-
r"""
免费模型路由内核（free_router_kernel）

把 freellmapi（Node.js，MIT）的路由**内核机制**等价移植为本项目的 Python
标准库实现，供 ``services.free_model_router.FreeModelRouter`` 接线调用。
本模块只做纯机制：不发网络请求、不认识 Provider 语义、不读 config，
因此可以完全离线、确定性地单测。

对应 7 条内核机制：

1. 动态惩罚路由 ``PenaltyTable``   —— 实际排序 = 基础优先级 + 惩罚分；
   429 惩罚 +3（上限 10）、成功 -1、每 120s 自动衰减 1。
   效果：被限流的模型自动下沉、冷却后自动回到原位，无需手工调 Provider 顺序。
2. 滑动窗口 ``RateLimitLedger``     —— 「过去 60 秒内 N 次」的滑动窗口（不是
   「每分钟归零」的固定窗口）；内存 + SQLite 双写：内存微秒级查询，
   SQLite 保证进程重启后计数不丢（不会一重启就突破限额）。
3. 四维限流 ``RateLimiter``         —— RPM / RPD / TPM / TPD，任一维度触顶
   就跳过该 Provider。
4. 冷却升级 ``Cooldown429``         —— 同一模型 24h 内第 N 次 429：
   2min → 10min → 1h → 24h；状态持久化。
5. Sticky Session ``StickySessionStore`` —— 首条 user 消息的 SHA1 作 SessionKey，
   仅对多轮对话生效，30 分钟有效，命中只是「优先」不是「强制」。
6. 跳过集合 ``SkipSet`` + 重试预算 ``MAX_ROUTE_ATTEMPTS`` —— 失败过的
   「模型 + Key」组合不再重试；整条链路最多 20 次。
7. 可观测性                        —— 各组件 ``snapshot()/report()`` 供
   ``FreeModelRouter.routing_snapshot()`` 汇总（对齐 freellmapi 的 x-routed-via 留痕）。

硬约束（与本模块的实现直接相关）：
- **纯标准库**（hashlib / os / sqlite3 / threading / time），无任何第三方依赖；
- **不写系统目录**：落盘路径由调用方给出（FreeModelRouter 走
  ``services.runtime_paths.writable_data_dir()``）；
- **持久化失败必须静默降级为纯内存**：落盘打不开/写不进去，只记 log，
  绝不抛异常打断路由，也绝不因此放宽任何 fail-closed 判断；
- **只统计、不路由**：本模块从不选择模型，也不产生任何网络或计费副作用。
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

__all__ = [
    # 常量
    "PENALTY_429_INC", "PENALTY_MAX", "PENALTY_MIN", "PENALTY_SUCCESS_DEC",
    "PENALTY_DECAY_INTERVAL_SEC", "PENALTY_DECAY_STEP",
    "SLIDING_WINDOW_SEC", "DAY_WINDOW_SEC", "RATE_LIMIT_DIMS",
    "COOLDOWN_429_LADDER", "COOLDOWN_429_WINDOW_SEC",
    "STICKY_TTL_SEC", "MAX_ROUTE_ATTEMPTS",
    "PROVIDER_BASE_PRIORITY", "DEFAULT_BASE_PRIORITY", "KNOWN_PROVIDERS",
    # 函数
    "base_priority_for", "effective_score", "session_key_for", "key_hash",
    "pair_key", "channel_tag", "build_limit_specs", "close_shared_connections",
    # 类
    "PersistentStateStore", "RateLimitLedger", "RateLimitSpec", "RateLimiter",
    "PenaltyTable", "Cooldown429", "StickySessionStore", "SkipSet",
]

# ====================================================================== #
# 常量（集中在此，便于单测直接引用）
#
# 注意：这里**不改动** free_model_router 既有的 R3.3-30/32 参数
# （COOLDOWN_BASE_SEC=30 / COOLDOWN_MAX_SEC=300 / BACKOFF_RETRY_DELAYS=(1,2,4)
#  / PROBE_*），那些属于 5xx 熔断与同模型退避，语义不同、表也不同：
#   · 5xx 熔断      -> free_model_router.CooldownTable（30s 起步翻倍、封顶 5min）
#   · 429 分级冷却  -> 本模块 Cooldown429（2min/10min/1h/24h）
# ====================================================================== #

# --- 1. 动态惩罚路由 ---
PENALTY_429_INC = 3.0                # 一次 429：惩罚 +3
PENALTY_MAX = 10.0                   # 惩罚上限
PENALTY_MIN = 0.0                    # 惩罚下限（0 = 回到基础优先级「原位」）
PENALTY_SUCCESS_DEC = 1.0            # 一次成功：惩罚 -1
PENALTY_DECAY_INTERVAL_SEC = 120.0   # 每 2 分钟自动衰减 1
PENALTY_DECAY_STEP = 1.0

# --- 2/3. 滑动窗口 + 四维限流 ---
SLIDING_WINDOW_SEC = 60.0            # 「过去 60 秒内 N 次」
DAY_WINDOW_SEC = 86400.0             # 「过去 24 小时内 N 次」
RATE_LIMIT_DIMS = ("rpm", "rpd", "tpm", "tpd")

# --- 4. 429 冷却升级 ---
COOLDOWN_429_LADDER = (120.0, 600.0, 3600.0, 86400.0)   # 2min / 10min / 1h / 24h
COOLDOWN_429_WINDOW_SEC = 86400.0                        # 升级计数的观察窗

# --- 5. Sticky Session ---
STICKY_TTL_SEC = 1800.0              # 30 分钟

# --- 6. Fallback / 重试预算 ---
MAX_ROUTE_ATTEMPTS = 20              # 整条链路最多 20 次尝试

# --- 基础优先级：数字越小越优先 ---
# 分三档：local（零配额、零成本，最优先）→ 远端免费池（含零配置免 Key 的
# pollinations / llm7）→ browser_ai 兜底。
# 远端档内部的相对次序刻意与「改动前 ranked() 在成功率并列时的既有次序」一致
# （candidates() 里 openrouter/nvidia 的静态目录在前、零配置渠道在后），这样
# 基础优先级只是把原来隐含的次序**显式化、可覆盖化**，不改变既有的默认行为。
# effective_score = base_priority + penalty，因此「惩罚分」只会把通道往下压，
# 不会翻上来；惩罚衰减归零后自动回到这里的基础位置（原位）。
PROVIDER_BASE_PRIORITY: dict[str, int] = {
    "ollama": 0,
    "local_bridge": 1,
    "local_scan": 2,
    "custom": 3,
    "desktop": 4,
    "openrouter": 10,
    "nvidia": 11,
    "pollinations": 20,
    "llm7": 21,
    "browser_ai": 90,
}
DEFAULT_BASE_PRIORITY = 50
KNOWN_PROVIDERS = tuple(PROVIDER_BASE_PRIORITY)


# ====================================================================== #
# 小工具
# ====================================================================== #
def base_priority_for(provider: str, overrides: Optional[dict] = None) -> int:
    """基础优先级：优先取调用方给的 per-provider 覆盖，否则查内置表。"""
    if overrides:
        raw = overrides.get(provider)
        try:
            if raw is not None and str(raw).strip() != "":
                return int(float(str(raw).strip()))
        except (TypeError, ValueError):
            pass
    return int(PROVIDER_BASE_PRIORITY.get(provider, DEFAULT_BASE_PRIORITY))


def effective_score(base_priority: int, penalty: float) -> float:
    """实际排序分 = 基础优先级 + 惩罚分（**越小越优先**）。"""
    return round(float(base_priority) + float(penalty or 0.0), 3)


def channel_tag(provider: str, model: str) -> str:
    """通道标识（与 chat() 的 routed_via 同形）：``provider:model``。"""
    return f"{provider}:{model}"


def key_hash(api_key: str) -> str:
    """Key 的短哈希：跳过集合用它做「模型 + Key」组合键，避免明文 Key 进内存表。"""
    return hashlib.sha1((api_key or "").encode("utf-8")).hexdigest()[:12]


def pair_key(provider: str, model: str, api_key: str) -> str:
    """跳过集合的组合键 = provider + model + Key 哈希。"""
    return f"{provider}|{model}|{key_hash(api_key)}"


def session_key_for(first_user_message: str, multi_turn: bool) -> str:
    """SessionKey = 首条 user 消息的 SHA1（仅多轮对话才给 Key）。

    非多轮 / 空消息一律返回空串 —— 这样 Sticky 天然「只对多轮生效」。
    """
    if not multi_turn:
        return ""
    text = (first_user_message or "").strip()
    if not text:
        return ""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _to_num(value) -> Optional[float]:
    """配置值 → float；空/非法 → None（**不猜、不编默认额度**）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def build_limit_specs(config, providers=None, nested_key: str = "llm.free.limits") -> dict:
    """从 config 读四维上限，返回 ``{provider: RateLimitSpec}``（只含配置过的）。

    两种写法都支持（嵌套优先）::

        {"llm.free.limits": {"llm7": {"rpm": 20, "tpd": 50000}}}
        {"llm.free.limits.llm7.rpm": "20"}

    **不内置任何 provider 配额默认值**：没有配就是「无上限」，因为凭空写一个
    第三方免费额度数字等于编数；真实额度应由使用者按自己的账号填。
    """
    def _get(key):
        try:
            return config.get(key)
        except Exception:  # noqa: BLE001 - 配置源异常一律当未配置
            return None

    nested = _get(nested_key)
    pool = set(providers or KNOWN_PROVIDERS)
    if isinstance(nested, dict):
        pool |= {k for k in nested.keys() if isinstance(k, str)}

    specs: dict[str, RateLimitSpec] = {}
    for provider in sorted(pool):
        vals: dict[str, Optional[float]] = {d: None for d in RATE_LIMIT_DIMS}
        if isinstance(nested, dict) and isinstance(nested.get(provider), dict):
            for dim in RATE_LIMIT_DIMS:
                vals[dim] = _to_num(nested[provider].get(dim))
        for dim in RATE_LIMIT_DIMS:
            if vals[dim] is None:
                vals[dim] = _to_num(_get(f"{nested_key}.{provider}.{dim}"))
        spec = RateLimitSpec(rpm=vals["rpm"], rpd=vals["rpd"],
                             tpm=vals["tpm"], tpd=vals["tpd"])
        if spec.configured:
            specs[provider] = spec
    return specs


# ====================================================================== #
# 持久化层：SQLite 状态仓
# ====================================================================== #
_DDL = (
    """CREATE TABLE IF NOT EXISTS rl_events (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           provider TEXT NOT NULL,
           ts REAL NOT NULL,
           tokens REAL NOT NULL DEFAULT 0
       )""",
    "CREATE INDEX IF NOT EXISTS idx_rl_events_provider_ts ON rl_events(provider, ts)",
    """CREATE TABLE IF NOT EXISTS cd429 (
           key TEXT PRIMARY KEY,
           level INTEGER NOT NULL DEFAULT 0,
           last_429 REAL NOT NULL DEFAULT 0,
           unlock_at REAL NOT NULL DEFAULT 0
       )""",
    """CREATE TABLE IF NOT EXISTS sticky (
           session_key TEXT PRIMARY KEY,
           channel TEXT NOT NULL,
           updated_at REAL NOT NULL
       )""",
)

_CONN_CACHE: dict[str, sqlite3.Connection] = {}
_CONN_LOCK = threading.Lock()


def _shared_connection(db_path: str) -> sqlite3.Connection:
    """同进程按路径复用一条连接（WAL + check_same_thread=False）。

    多线程首建竞态由全局锁挡住；SQLite 侧用 timeout 兜住并发写。
    """
    with _CONN_LOCK:
        conn = _CONN_CACHE.get(db_path)
        if conn is not None:
            return conn
        conn = sqlite3.connect(db_path, timeout=5.0, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except Exception as e:  # pragma: no cover - 极少见，降级不致命
            logger.info("SQLite PRAGMA 设置失败（忽略）: %s", e)
        for ddl in _DDL:
            conn.execute(ddl)
        conn.commit()
        _CONN_CACHE[db_path] = conn
        return conn


def close_shared_connections() -> None:
    """关闭并清空进程内的共享连接缓存（测试/退出时释放 sqlite 文件句柄）。

    生产运行不会调用它（同路径复用一条连接即可）；单元测试用它保证临时目录能删干净。
    """
    with _CONN_LOCK:
        for conn in list(_CONN_CACHE.values()):
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        _CONN_CACHE.clear()


class PersistentStateStore:
    """路由状态的落盘层（滑动窗口计数 / 429 冷却等级 / Sticky 映射）。

    设计原则：
    - 打不开、建不了表、写不进去 → ``ok=False``，所有写操作静默 no-op，
      调用方自动退化为纯内存态；**绝不抛异常打断路由**；
    - 读操作失败返回空，不臆造数据（表空就是表空）。
    """

    def __init__(self, db_path: str) -> None:
        self.path = db_path or ""
        self.ok = False
        self.error = ""
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        if not self.path:
            self.error = "未指定状态库路径"
            return
        try:
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._conn = _shared_connection(self.path)
            self.ok = True
        except Exception as e:  # noqa: BLE001 - 落盘不可用必须降级而不是崩
            self.error = f"{type(e).__name__}: {e}"
            logger.info("路由状态库不可用，退化为纯内存态: %s", self.error)

    # ---- 基础 ----
    def status(self) -> dict:
        return {"ok": bool(self.ok), "path": self.path, "error": self.error}

    def _rows(self, sql: str, args=()) -> list:
        if not self.ok or self._conn is None:
            return []
        with self._lock:
            try:
                return list(self._conn.execute(sql, args).fetchall())
            except Exception as e:  # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
                logger.info("路由状态库读取失败（按空处理）: %s", e)
                return []

    def _write(self, sql: str, args=()):
        if not self.ok or self._conn is None:
            return None
        with self._lock:
            try:
                cur = self._conn.execute(sql, args)
                self._conn.commit()
                return cur
            except Exception as e:  # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
                logger.info("路由状态库写入失败（忽略）: %s", e)
                return None

    # ---- 滑动窗口事件 ----
    def rl_add_event(self, provider: str, ts: float, tokens: float = 0.0) -> int:
        cur = self._write(
            "INSERT INTO rl_events(provider, ts, tokens) VALUES(?,?,?)",
            (str(provider), float(ts), float(tokens)))
        try:
            return int(cur.lastrowid) if cur is not None and cur.lastrowid else 0
        except Exception:  # noqa: BLE001
            return 0

    def rl_set_tokens(self, rowid: int, tokens: float) -> None:
        if rowid:
            self._write("UPDATE rl_events SET tokens=? WHERE id=?",
                        (float(tokens), int(rowid)))

    def rl_rows(self, provider: str, since: float, until: float) -> list:
        """返回 [since, until] 区间内的事件 ``[(ts, tokens), ...]``。

        区间双闭：未来时间戳（> until）不进内存，避免污染当前窗口。
        """
        rows = self._rows(
            "SELECT ts, tokens FROM rl_events WHERE provider=? AND ts>=? AND ts<=? "
            "ORDER BY ts", (str(provider), float(since), float(until)))
        out = []
        for r in rows:
            try:
                out.append((float(r[0]), float(r[1])))
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def rl_prune(self, before: float) -> None:
        self._write("DELETE FROM rl_events WHERE ts < ?", (float(before),))

    # ---- 429 冷却等级 ----
    def cd429_all(self) -> list:
        rows = self._rows("SELECT key, level, last_429, unlock_at FROM cd429")
        out = []
        for r in rows:
            try:
                out.append({"key": str(r[0]), "level": int(r[1]),
                            "last_429": float(r[2]), "unlock_at": float(r[3])})
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def cd429_put(self, key: str, level: int, last_429: float, unlock_at: float) -> None:
        self._write(
            "INSERT INTO cd429(key, level, last_429, unlock_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET level=excluded.level, "
            "last_429=excluded.last_429, unlock_at=excluded.unlock_at",
            (str(key), int(level), float(last_429), float(unlock_at)))

    def cd429_delete(self, key: str) -> None:
        self._write("DELETE FROM cd429 WHERE key=?", (str(key),))

    # ---- Sticky 映射 ----
    def sticky_all(self) -> list:
        rows = self._rows("SELECT session_key, channel, updated_at FROM sticky")
        out = []
        for r in rows:
            try:
                out.append({"session_key": str(r[0]), "channel": str(r[1]),
                            "updated_at": float(r[2])})
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def sticky_put(self, session_key: str, channel: str, updated_at: float) -> None:
        self._write(
            "INSERT INTO sticky(session_key, channel, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(session_key) DO UPDATE SET channel=excluded.channel, "
            "updated_at=excluded.updated_at",
            (str(session_key), str(channel), float(updated_at)))

    def sticky_prune(self, before: float) -> None:
        self._write("DELETE FROM sticky WHERE updated_at < ?", (float(before),))


# ====================================================================== #
# 2. 滑动窗口计数台账（内存 + SQLite 双写）
# ====================================================================== #
class RateLimitLedger:
    """滑动窗口计数台账。

    事件单位 = 一次出站请求 ``(ts, tokens)``；token 数在拿到响应后回填
    （无 usage 时由调用方估算）。查询规则：只统计

        ``0 <= now - ts <= window``

    的事件 —— 未来时间戳（时钟回拨 / 测试注入 mock 时钟）不计入，天然隔离。

    双写策略：
    - **内存**：``_events[provider]`` 列表，查询是纯内存遍历（微秒级）；
    - **SQLite**：同一份事件同步落盘；某 provider 在本进程「首次被访问」时
      从 SQLite 回灌最近 24h 的事件（``_hydrate``），因此**进程重启后计数不丢**，
      不会出现「重启就突破限额」。
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None,
                 store: Optional[PersistentStateStore] = None,
                 retention_sec: float = DAY_WINDOW_SEC) -> None:
        self._clock = clock or time.time
        self._store = store
        self._retention = float(retention_sec)
        self._lock = threading.RLock()
        self._events: dict[str, list] = {}
        self._purged_at: dict[str, float] = {}

    # ---------------- 写 ----------------
    def note_request(self, provider: str, tokens: float = 0.0) -> list:
        """记一次请求；返回可变事件引用（供 note_tokens 回填，勿序列化）。"""
        ts = float(self._clock())
        with self._lock:
            self._hydrate(provider, ts)
            rowid = 0
            if self._store is not None and self._store.ok:
                rowid = self._store.rl_add_event(provider, ts, float(tokens))
            ev = [ts, float(tokens), rowid]
            self._events[provider].append(ev)
            return ev

    def note_tokens(self, event, tokens: float) -> None:
        """响应到达后回填 token 用量（内存 + SQLite 同步更新同一行）。"""
        if not isinstance(event, list) or len(event) < 3:
            return
        try:
            event[1] = float(tokens)
            rowid = event[2]
        except (TypeError, ValueError):
            return
        if self._store is not None and self._store.ok and rowid:
            self._store.rl_set_tokens(rowid, tokens)

    # ---------------- 读 ----------------
    def usage(self, provider: str, now: Optional[float] = None) -> dict:
        """返回四维当前用量：``{rpm, rpd, tpm, tpd}``。

        rpm/tpm 走 60s 滑动窗口；rpd/tpd 走 24h 窗口。
        """
        at = float(self._clock()) if now is None else float(now)
        with self._lock:
            self._hydrate(provider, at)
            evs = list(self._events.get(provider, []))
            self._maybe_purge(provider, at)
        out = {"rpm": 0, "rpd": 0, "tpm": 0.0, "tpd": 0.0}
        for ev in evs:
            try:
                ts, tokens = float(ev[0]), float(ev[1])
            except (TypeError, ValueError, IndexError):
                continue
            age = at - ts
            if age < 0 or age > DAY_WINDOW_SEC:   # 未来 / 超 24h：不计入
                continue
            if age <= SLIDING_WINDOW_SEC:
                out["rpm"] += 1
                out["tpm"] += tokens
            out["rpd"] += 1
            out["tpd"] += tokens
        return out

    # ---------------- 内部 ----------------
    def _hydrate(self, provider: str, now: float) -> None:
        """首次访问该 provider：从 SQLite 回灌最近 24h 事件（重启不丢计数）。"""
        if provider in self._events:
            return
        rows: list = []
        if self._store is not None and self._store.ok:
            rows = self._store.rl_rows(provider, now - DAY_WINDOW_SEC, now)
        self._events[provider] = [[float(ts), float(tk), 0] for ts, tk in rows]
        self._purged_at.setdefault(provider, now)

    def _maybe_purge(self, provider: str, now: float) -> None:
        """低频清理：每 5 分钟最多一次，删内存与库中 24h 前的事件。"""
        last = self._purged_at.get(provider, 0.0)
        if now - last < 300.0 and last > 0:
            return
        self._purged_at[provider] = now
        cutoff = now - self._retention
        evs = self._events.get(provider)
        if evs:
            self._events[provider] = [e for e in evs
                                      if float(e[0]) >= cutoff or float(e[0]) > now]
        if self._store is not None and self._store.ok:
            self._store.rl_prune(min(cutoff, now))


# ====================================================================== #
# 3. 四维限流
# ====================================================================== #
@dataclass(frozen=True)
class RateLimitSpec:
    """一个 Provider 的四维上限；``None`` = 该维度未配置 = 不拦。"""

    rpm: Optional[float] = None
    rpd: Optional[float] = None
    tpm: Optional[float] = None
    tpd: Optional[float] = None

    @property
    def configured(self) -> bool:
        return any(v is not None for v in (self.rpm, self.rpd, self.tpm, self.tpd))

    def as_dict(self) -> dict:
        return {d: getattr(self, d) for d in RATE_LIMIT_DIMS}

    def exceeded(self, used: dict) -> Optional[str]:
        """任一维度「已达上限」（used >= limit）→ 返回第一个触顶维度名。"""
        for dim in RATE_LIMIT_DIMS:
            limit = getattr(self, dim)
            if limit is None:
                continue
            try:
                if float(used.get(dim, 0) or 0) >= float(limit):
                    return dim
            except (TypeError, ValueError):
                continue
        return None


class RateLimiter:
    """四维限流闸门：RPM（每分钟请求）/ RPD（每天请求）/ TPM（每分钟 Token）/
    TPD（每天 Token），**任一维度触顶就跳过该 Provider**。

    未配置任何维度的 Provider 视为无上限（零配置开箱可用）；上限只来自
    config（见 ``build_limit_specs``），本类不内置任何第三方额度数字。
    """

    def __init__(self, ledger: RateLimitLedger,
                 specs: Optional[dict] = None,
                 clock: Optional[Callable[[], float]] = None) -> None:
        self._ledger = ledger
        self._specs: dict[str, RateLimitSpec] = dict(specs or {})
        self._clock = clock or time.time

    def spec_for(self, provider: str) -> RateLimitSpec:
        return self._specs.get(provider) or RateLimitSpec()

    def configured(self) -> dict:
        """已配置上限的 Provider → 四维上限（供冷却报告 / 快照展示）。"""
        return {p: s.as_dict() for p, s in self._specs.items()}

    def usage(self, provider: str) -> dict:
        return self._ledger.usage(provider)

    # ---- 出站记账（转发到同一本账，调用方只需持有闸门一个对象）----
    # 「先记账、再出站」：本次请求在真正发出前就计入四维用量，因此下一轮
    # blocked_dim() 立刻能看见它——这正是滑动窗口「不超发」的关键。
    def note_request(self, provider: str, tokens: float = 0.0):
        return self._ledger.note_request(provider, tokens)

    def note_tokens(self, event, tokens: float) -> None:
        """响应回来后回填本次实际 Token（估算值），用于 TPM/TPD 统计。"""
        self._ledger.note_tokens(event, tokens)

    def blocked_dim(self, provider: str) -> Optional[str]:
        """返回触顶的维度名；None = 放行。"""
        spec = self.spec_for(provider)
        if not spec.configured:
            return None
        return spec.exceeded(self._ledger.usage(provider))

    def allow(self, provider: str) -> bool:
        return self.blocked_dim(provider) is None

    def report(self, provider: str) -> dict:
        """供 routing_snapshot()：上限 + 当前用量（8 个键，JSON 可序列化）。"""
        spec = self.spec_for(provider)
        used = self._ledger.usage(provider)
        out = {"rpm": spec.rpm, "rpd": spec.rpd, "tpm": spec.tpm, "tpd": spec.tpd}
        for dim in RATE_LIMIT_DIMS:
            out[f"{dim}_used"] = used.get(dim, 0)
        return out


# ====================================================================== #
# 1. 动态惩罚路由
# ====================================================================== #
class PenaltyTable:
    """动态惩罚分表：**实际排序分 = 基础优先级 + 惩罚分**（越小越优先）。

    - 429：+3（累计上限 10）；
    - 成功：-1（下限 0，即「回到原位」）；
    - 时间衰减：每 120s 自动 -1（惰性结算，读写时按已过的整周期数一次性扣减）。

    键 = ``provider:model``（比裸模型名更精确，避免跨 Provider 同名模型互相污染）。
    仅内存态：惩罚分由 429/成功事件在秒级重新累积，落盘真源是限流计数与冷却等级。
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None) -> None:
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._t: dict[str, dict] = {}

    def _decayed(self, key: str, now: float) -> dict:
        e = self._t.get(key)
        if e is None:
            return {"value": 0.0, "last_decay_ts": float(now)}
        steps = int((now - float(e.get("last_decay_ts", now))) // PENALTY_DECAY_INTERVAL_SEC)
        if steps > 0:
            e["value"] = max(PENALTY_MIN, float(e["value"]) - steps * PENALTY_DECAY_STEP)
            e["last_decay_ts"] = float(e["last_decay_ts"]) + steps * PENALTY_DECAY_INTERVAL_SEC
        return e

    def value(self, key: str) -> float:
        now = float(self._clock())
        with self._lock:
            return round(float(self._decayed(key, now)["value"]), 3)

    def add(self, key: str, delta: float) -> float:
        now = float(self._clock())
        with self._lock:
            e = self._decayed(key, now)
            e["value"] = min(PENALTY_MAX, max(PENALTY_MIN, float(e["value"]) + float(delta)))
            self._t[key] = e
            return round(float(e["value"]), 3)

    def on_429(self, key: str) -> float:
        return self.add(key, PENALTY_429_INC)

    def on_success(self, key: str) -> float:
        return self.add(key, -PENALTY_SUCCESS_DEC)

    def clear(self, key: str) -> None:
        with self._lock:
            self._t.pop(key, None)

    def snapshot(self) -> dict:
        return {k: self.value(k) for k in list(self._t.keys())}


# ====================================================================== #
# 4. 429 冷却升级
# ====================================================================== #
class Cooldown429:
    """429 冷却升级：同一模型 24h 内第 N 次被限流 →

        N=1 → 2 分钟；N=2 → 10 分钟；N=3 → 1 小时；N≥4 → 24 小时。

    设计意图：反复被限流说明免费额度可能见底，与其每几秒试一次，不如等额度刷新。
    升级计数只在「距上次 429 超过 24h」时归零（严格按「同一模型 24 小时内第 N 次」
    计数，成功**不**重置，否则退避阶梯会失去意义）。状态持久化，重启不清零。
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None,
                 store: Optional[PersistentStateStore] = None,
                 ladder=COOLDOWN_429_LADDER,
                 window_sec: float = COOLDOWN_429_WINDOW_SEC) -> None:
        self._clock = clock or time.time
        self._store = store
        self._ladder = tuple(float(x) for x in ladder)
        self._window = float(window_sec)
        self._lock = threading.RLock()
        self._t: dict[str, dict] = {}
        if store is not None and store.ok:
            for row in store.cd429_all():
                self._t[row["key"]] = {"level": int(row["level"]),
                                       "last_429": float(row["last_429"]),
                                       "unlock_at": float(row["unlock_at"])}

    def record_429(self, key: str, now: Optional[float] = None) -> dict:
        """记一次「重试用尽仍被限流」，返回本次等级与冷却时长。"""
        at = float(self._clock()) if now is None else float(now)
        with self._lock:
            e = self._t.get(key) or {"level": 0, "last_429": 0.0, "unlock_at": 0.0}
            level = int(e["level"])
            last = float(e["last_429"])
            if last and (at - last) > self._window:
                level = 0                       # 超过观察窗 → 退避阶梯归零
            level += 1
            sec = self._ladder[min(level, len(self._ladder)) - 1]
            e["level"] = level
            e["last_429"] = at
            e["unlock_at"] = at + sec
            self._t[key] = e
            if self._store is not None and self._store.ok:
                self._store.cd429_put(key, level, at, e["unlock_at"])
            return {"key": key, "level": level,
                    "cooldown_sec": sec, "unlock_at": e["unlock_at"]}

    def is_cooling(self, key: str, now: Optional[float] = None) -> bool:
        at = float(self._clock()) if now is None else float(now)
        with self._lock:
            e = self._t.get(key)
        return bool(e) and at < float(e["unlock_at"])

    def remaining(self, key: str, now: Optional[float] = None) -> float:
        at = float(self._clock()) if now is None else float(now)
        with self._lock:
            e = self._t.get(key)
        if not e:
            return 0.0
        return max(0.0, round(float(e["unlock_at"]) - at, 1))

    def unlock_at(self, key: str) -> float:
        with self._lock:
            e = self._t.get(key)
        return float(e["unlock_at"]) if e else 0.0

    def level(self, key: str) -> int:
        with self._lock:
            e = self._t.get(key)
        return int(e["level"]) if e else 0

    def snapshot(self) -> list:
        now = float(self._clock())
        out = []
        with self._lock:
            for key, e in self._t.items():
                out.append({
                    "key": key,
                    "level": int(e["level"]),
                    "last_429": float(e["last_429"]),
                    "unlock_at": float(e["unlock_at"]),
                    "remaining_sec": max(0.0, round(float(e["unlock_at"]) - now, 1)),
                })
        out.sort(key=lambda x: x["remaining_sec"], reverse=True)
        return out


# ====================================================================== #
# 5. Sticky Session
# ====================================================================== #
class StickySessionStore:
    """Sticky Session：SessionKey(首条 user 消息 SHA1) → ``provider:model``。

    - **只在多轮对话下建立与命中**（消息里出现过 assistant 历史）；
    - 首次路由成功后才写入；
    - 30 分钟有效（超时即失效并清理）；
    - 命中只是「优先」不是「强制」：上次的模型若已限流/冷却，正常走 Fallback。
    - 映射持久化（重启后 30 分钟内的会话仍能命中）。
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None,
                 store: Optional[PersistentStateStore] = None,
                 ttl_sec: float = STICKY_TTL_SEC) -> None:
        self._clock = clock or time.time
        self._store = store
        self._ttl = float(ttl_sec)
        self._lock = threading.RLock()
        self._t: dict[str, dict] = {}
        if store is not None and store.ok:
            now = float(self._clock())
            for row in store.sticky_all():
                if now - row["updated_at"] <= self._ttl:
                    self._t[row["session_key"]] = {"channel": row["channel"],
                                                   "updated_at": row["updated_at"]}

    def get(self, session_key: str) -> str:
        """命中返回 ``provider:model``；无/过期返回空串（顺带清掉过期项）。"""
        if not session_key:
            return ""
        at = float(self._clock())
        with self._lock:
            e = self._t.get(session_key)
            if not e:
                return ""
            if at - float(e["updated_at"]) > self._ttl:
                self._t.pop(session_key, None)
                if self._store is not None and self._store.ok:
                    self._store.sticky_prune(at - self._ttl)
                return ""
            return str(e["channel"])

    def set(self, session_key: str, channel: str) -> None:
        if not session_key or not channel:
            return
        at = float(self._clock())
        with self._lock:
            self._t[session_key] = {"channel": str(channel), "updated_at": at}
            if self._store is not None and self._store.ok:
                self._store.sticky_put(session_key, channel, at)
                self._store.sticky_prune(at - self._ttl)

    def count(self) -> int:
        """存活（未过期）会话数：供 routing_snapshot().sticky_sessions。"""
        at = float(self._clock())
        with self._lock:
            expired = [k for k, v in self._t.items() if at - float(v["updated_at"]) > self._ttl]
            for k in expired:
                self._t.pop(k, None)
            if expired and self._store is not None and self._store.ok:
                self._store.sticky_prune(at - self._ttl)
            return len(self._t)

    def snapshot(self) -> dict:
        return {k: v["channel"] for k, v in list(self._t.items())}


# ====================================================================== #
# 6. 跳过集合
# ====================================================================== #
class SkipSet:
    """本次请求内的「模型 + Key」跳过集合（对齐 freellmapi：失败组合不再重试）。

    键含 Key 哈希：同一个模型换了 Key 仍可再试，同一个 Key 下的同一模型失败即跳过。
    """

    def __init__(self) -> None:
        self._s: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def key_of(provider: str, model: str, api_key: str) -> str:
        return pair_key(provider, model, api_key)

    @staticmethod
    def candidate_key(candidate) -> str:
        return pair_key(getattr(candidate, "provider", ""),
                        getattr(candidate, "model", ""),
                        getattr(candidate, "api_key", ""))

    def add(self, provider: str, model: str, api_key: str) -> str:
        k = pair_key(provider, model, api_key)
        with self._lock:
            self._s.add(k)
        return k

    def add_candidate(self, candidate) -> str:
        k = self.candidate_key(candidate)
        with self._lock:
            self._s.add(k)
        return k

    def has_candidate(self, candidate) -> bool:
        k = self.candidate_key(candidate)
        with self._lock:
            return k in self._s

    def __len__(self) -> int:
        with self._lock:
            return len(self._s)
