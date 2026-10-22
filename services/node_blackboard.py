# -*- coding: utf-8 -*-
"""
LoveAI v3.2.0 - 跨进程节点黑板（Node Blackboard）

【借鉴卡片 M5-3】细胞形态发生素梯度（Wolpert 1969）：没有总指挥，每个细胞
只根据局部浓度做决定。LoveAI 已有 Blackboard 雏形（person_state_snapshot.py
的 DB_OWNED_FIELDS），本模块把它从单进程 dict 扩展成跨进程 watch 接口。

【本轮最小形态】
- SQLite 后端（不引入消息队列/服务发现库，文件锁就够）；
- 三个虚拟节点注册：l1_onnx / l2_ollama / l3_cloud；
- 每个节点 lease 一个 field；节点挂了 lease 自动过期，其他节点可抢占。

【接口】
- register_node(node_id, role, ttl_sec)：注册/续租节点心跳。
- heartbeat(node_id)：节点报活（由节点进程每 10-20s 调一次）。
- discover_nodes()：返回当前活着的节点（lease 未过期）。
- lease_field(node_id, field, ttl_sec)：占用一个 field；被占则失败。
- release_field(node_id, field)：主动释放 field。
- node_alive(node_id)：节点是否还活着（心跳未过期）。

【硬约束】
- 纯 Python stdlib（sqlite3/time/os）；
- 不碰 knowledge/、不碰微信数据、不碰 G 盘；
- 不引入 RPC / 消息队列 / gossiping 库。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 默认 DB 路径（项目根/data/node_blackboard.db）
_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "node_blackboard.db",
)

# 默认节点 TTL（秒）：超过没 heartbeat 视为掉线
DEFAULT_NODE_TTL: int = 60
DEFAULT_LEASE_TTL: int = 30

# 三个虚拟节点的种子（M5-3 最小形态演示）
SEED_NODES: tuple[dict, ...] = (
    {"node_id": "l1_onnx",    "role": "fast_lightweight", "field": "tier.judgment"},
    {"node_id": "l2_ollama",  "role": "local_mid",        "field": "tier.draft"},
    {"node_id": "l3_cloud",   "role": "cloud_powerful",   "field": "tier.simulate"},
)


class NodeBlackboard:
    """跨进程节点黑板（SQLite 后端）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path: str = db_path or _DEFAULT_DB
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_schema()

    # ---- schema ---- #
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=5.0)
        c.row_factory = sqlite3.Row
        return c

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS nodes (
                    node_id        TEXT PRIMARY KEY,
                    role           TEXT NOT NULL,
                    registered_at  REAL NOT NULL,
                    last_heartbeat REAL NOT NULL,
                    ttl_sec        REAL NOT NULL
                )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS leases (
                    node_id     TEXT NOT NULL,
                    field       TEXT NOT NULL,
                    lease_until REAL NOT NULL,
                    ttl_sec     REAL NOT NULL,
                    PRIMARY KEY (node_id, field)
                )"""
            )

    # ---- 节点注册/心跳 ---- #
    def register_node(self, node_id: str, role: str,
                      ttl_sec: float = DEFAULT_NODE_TTL) -> dict:
        """注册或续租节点。重复调用 = 刷新心跳。"""
        now = time.time()
        with self._conn() as c:
            c.execute(
                """INSERT INTO nodes (node_id, role, registered_at, last_heartbeat, ttl_sec)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(node_id) DO UPDATE SET
                       role=excluded.role,
                       last_heartbeat=excluded.last_heartbeat,
                       ttl_sec=excluded.ttl_sec""",
                (node_id, role, now, now, ttl_sec),
            )
        return {"node_id": node_id, "role": role, "heartbeat": now, "ttl_sec": ttl_sec}

    def heartbeat(self, node_id: str) -> bool:
        """节点报活。返回 False 表示该节点未注册过。"""
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                "UPDATE nodes SET last_heartbeat=? WHERE node_id=?",
                (now, node_id),
            )
            return cur.rowcount > 0

    def node_alive(self, node_id: str) -> bool:
        now = time.time()
        with self._conn() as c:
            row = c.execute(
                "SELECT last_heartbeat, ttl_sec FROM nodes WHERE node_id=?",
                (node_id,),
            ).fetchone()
        if row is None:
            return False
        return (now - row["last_heartbeat"]) <= row["ttl_sec"]

    # ---- 节点发现 ---- #
    def discover_nodes(self) -> list[dict]:
        """返回所有活着的节点（心跳未过期），附带当前 lease 的 fields。"""
        now = time.time()
        with self._conn() as c:
            rows = c.execute(
                "SELECT node_id, role, last_heartbeat, ttl_sec FROM nodes"
            ).fetchall()
            leases = c.execute(
                "SELECT node_id, field, lease_until, ttl_sec FROM leases"
            ).fetchall()
        alive = []
        lease_by_node: dict[str, list[dict]] = {}
        for r in leases:
            if r["lease_until"] >= now:  # 过期 lease 不返回
                lease_by_node.setdefault(r["node_id"], []).append(
                    {"field": r["field"], "lease_until": r["lease_until"]}
                )
        for r in rows:
            age = now - r["last_heartbeat"]
            if age <= r["ttl_sec"]:
                alive.append({
                    "node_id": r["node_id"],
                    "role": r["role"],
                    "age_sec": round(age, 1),
                    "leases": lease_by_node.get(r["node_id"], []),
                })
        return alive

    # ---- field lease ---- #
    def lease_field(self, node_id: str, field: str,
                    ttl_sec: float = DEFAULT_LEASE_TTL) -> dict:
        """占用一个 field。若 field 已被其他节点未过期占用，返回失败。"""
        now = time.time()
        lease_until = now + ttl_sec
        with self._conn() as c:
            # 查 field 当前主人
            cur = c.execute(
                "SELECT node_id, lease_until FROM leases WHERE field=?",
                (field,),
            ).fetchone()
            if cur is not None and cur["lease_until"] >= now and cur["node_id"] != node_id:
                return {
                    "ok": False,
                    "reason": f"field '{field}' 已被节点 {cur['node_id']} lease "
                              f"(until {round(cur['lease_until'],1)})",
                    "field": field,
                }
            c.execute(
                """INSERT INTO leases (node_id, field, lease_until, ttl_sec)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(node_id, field) DO UPDATE SET
                       lease_until=excluded.lease_until,
                       ttl_sec=excluded.ttl_sec""",
                (node_id, field, lease_until, ttl_sec),
            )
            # 如果这个 field 之前被同节点续过、或被过期节点占过，顺手清理
            c.execute("DELETE FROM leases WHERE field=? AND lease_until < ?",
                      (field, now)) if False else None  # 保留上一行判断逻辑不重复删
        return {"ok": True, "node_id": node_id, "field": field,
                "lease_until": round(lease_until, 1)}

    def release_field(self, node_id: str, field: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM leases WHERE node_id=? AND field=?",
                (node_id, field),
            )
            return cur.rowcount > 0

    def list_leases(self, only_alive: bool = True) -> list[dict]:
        now = time.time()
        with self._conn() as c:
            rows = c.execute(
                "SELECT node_id, field, lease_until, ttl_sec FROM leases"
            ).fetchall()
        out = []
        for r in rows:
            if only_alive and r["lease_until"] < now:
                continue
            out.append({
                "node_id": r["node_id"], "field": r["field"],
                "lease_until": round(r["lease_until"], 1),
                "expired": r["lease_until"] < now,
            })
        return out

    # ---- 演示：seed 三个虚拟节点 ---- #
    def seed_virtual_nodes(self) -> list[dict]:
        """注册 l1_onnx / l2_ollama / l3_cloud 三个虚拟节点并各 lease 一个 field。

        用于验证最小协议能跑通；生产节点由各自进程 heartbeat 续命。
        """
        results = []
        for spec in SEED_NODES:
            self.register_node(spec["node_id"], spec["role"])
            lr = self.lease_field(spec["node_id"], spec["field"])
            results.append(lr)
        return results


# ---- 模块级便捷函数 ---- #
_default_bb: Optional[NodeBlackboard] = None


def get_blackboard(db_path: Optional[str] = None) -> NodeBlackboard:
    """进程内默认黑板单例。"""
    global _default_bb
    if _default_bb is None or db_path is not None:
        _default_bb = NodeBlackboard(db_path=db_path)
    return _default_bb
