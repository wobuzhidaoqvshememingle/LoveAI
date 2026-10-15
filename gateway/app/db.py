# -*- coding: utf-8 -*-
"""
SQLite 持久层。

表:
  request_logs       每次请求一行 (元数据, 不记正文)
  provider_daily     按 provider+日期 聚合的用量
  gateway_api_keys   调用方访问网关的 key (hash 存储)
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS request_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    api_key_name TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    status TEXT NOT NULL,           -- success | error | fallback
    error_type TEXT,
    strategy TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON request_logs(ts);
CREATE INDEX IF NOT EXISTS idx_logs_provider_ts ON request_logs(provider, ts);

CREATE TABLE IF NOT EXISTS provider_daily (
    provider TEXT NOT NULL,
    day TEXT NOT NULL,              -- YYYY-MM-DD
    requests INTEGER DEFAULT 0,
    successes INTEGER DEFAULT 0,
    failures INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    avg_latency_ms REAL DEFAULT 0,
    PRIMARY KEY (provider, day)
);

CREATE TABLE IF NOT EXISTS gateway_api_keys (
    key_hash TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    budget_tokens INTEGER DEFAULT 0,  -- 0 = 不限
    enabled INTEGER DEFAULT 1,
    created_at REAL NOT NULL
);

-- 自动采集的模型目录 (从各厂商 /models 端点拉取, 路由时用)
CREATE TABLE IF NOT EXISTS models_catalog (
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    context_window INTEGER,
    cost_per_1k_in REAL DEFAULT 0,
    cost_per_1k_out REAL DEFAULT 0,
    rpm_limit INTEGER,
    tpm_limit INTEGER,
    is_free INTEGER DEFAULT 0,
    raw_meta TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (provider, model_name)
);

-- 用户粘贴的 Key (自动识别厂商后落库, 密文存储)
CREATE TABLE IF NOT EXISTS discovered_keys (
    api_key_cipher TEXT PRIMARY KEY,  -- AES-256-GCM 密文
    provider_type TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    models TEXT,                       -- JSON list
    detected_at REAL NOT NULL,
    last_tested_at REAL,
    last_health_ok INTEGER DEFAULT 0
);
"""


class Database:
    """同步 SQLite 包装 (self-hosted 低并发, 同步足够; 连接加锁防并发写)。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock, self._connect() as conn:
            cur = conn.cursor()
            try:
                yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def log_request(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        status: str,
        error_type: Optional[str] = None,
        strategy: Optional[str] = None,
        api_key_name: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> int:
        ts = ts if ts is not None else time.time()
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO request_logs
                   (ts, api_key_name, provider, model, prompt_tokens, completion_tokens,
                    latency_ms, status, error_type, strategy)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (ts, api_key_name, provider, model, prompt_tokens, completion_tokens,
                 latency_ms, status, error_type, strategy),
            )
            # 聚合到 provider_daily
            day = time.strftime("%Y-%m-%d", time.localtime(ts))
            success = 1 if status == "success" else 0
            failure = 1 if status != "success" else 0
            cur.execute(
                """INSERT INTO provider_daily
                   (provider, day, requests, successes, failures,
                    prompt_tokens, completion_tokens, avg_latency_ms)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(provider, day) DO UPDATE SET
                     requests = requests + 1,
                     successes = successes + ?,
                     failures = failures + ?,
                     prompt_tokens = prompt_tokens + ?,
                     completion_tokens = completion_tokens + ?,
                     avg_latency_ms = (avg_latency_ms * (requests - 1) + ?) / requests""",
                (provider, day, 1, success, failure, prompt_tokens, completion_tokens, latency_ms,
                 success, failure, prompt_tokens, completion_tokens, latency_ms),
            )
            return cur.lastrowid

    # ------------------------------------------------------------------ #
    # 读取: 用量窗口
    # ------------------------------------------------------------------ #
    def window_usage(self, provider: str, now: Optional[float] = None) -> dict[str, int]:
        """返回最近 60 秒 / 当天的请求数和 token 数。用于 RPM/RPD/TPM/TPD 判定。"""
        now = now if now is not None else time.time()
        minute_ago = now - 60.0
        day_start = time.mktime(time.strptime(time.strftime("%Y-%m-%d", time.localtime(now)), "%Y-%m-%d"))
        with self.cursor() as cur:
            cur.execute(
                """SELECT
                     SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END) AS req_min,
                     SUM(CASE WHEN ts >= ? THEN prompt_tokens + completion_tokens ELSE 0 END) AS tok_min,
                     SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END) AS req_day,
                     SUM(prompt_tokens + completion_tokens) AS tok_day
                   FROM request_logs
                   WHERE provider = ? AND ts >= ?""",
                (minute_ago, minute_ago, day_start, provider, day_start),
            )
            row = cur.fetchone()
        return {
            "rpm": int(row["req_min"] or 0),
            "tpm": int(row["tok_min"] or 0),
            "rpd": int(row["req_day"] or 0),
            "tpd": int(row["tok_day"] or 0),
        }

    def provider_health_snapshot(self, provider: str, window_sec: int = 300) -> dict[str, Any]:
        """最近 window_sec 秒内的成功率和平均延迟。"""
        now = time.time()
        since = now - window_sec
        with self.cursor() as cur:
            cur.execute(
                """SELECT
                     COUNT(*) AS total,
                     SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok,
                     AVG(latency_ms) AS avg_lat,
                     MAX(ts) AS last_ts
                   FROM request_logs WHERE provider=? AND ts>=?""",
                (provider, since),
            )
            row = cur.fetchone()
        total = int(row["total"] or 0)
        ok = int(row["ok"] or 0)
        return {
            "provider": provider,
            "total": total,
            "successes": ok,
            "failures": total - ok,
            "success_rate": (ok / total) if total else 1.0,
            "avg_latency_ms": float(row["avg_lat"] or 0.0),
            "last_ts": float(row["last_ts"] or 0.0),
        }

    def recent_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM request_logs ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(r) for r in cur.fetchall()]
