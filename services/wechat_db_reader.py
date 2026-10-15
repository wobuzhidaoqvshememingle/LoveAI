# -*- coding: utf-8 -*-
"""
多版本微信数据库读取器（4.x SQLCipher4 / 3.x wxSQLite3）

职责：
- 自动解密所需 .db 到临时目录（不污染原库）
- 分页读取消息，统一标准化为 dict 输出
- 支持联系人筛选、时间范围筛选、增量读取、双号合并
- 大库不一次性 load 进内存，按页 / 按时间游标迭代

消息类型常量（与微信原生 Type 对齐）：
    1   文字
    3   图片
    34  语音
    43  视频
    47  表情
    49  文件 / 链接 / 公众号卡片（sub_type 细分）
    10000 系统消息
"""
from __future__ import annotations

import glob
import json
import logging
import os
import sqlite3
import tempfile
from typing import Iterable, List, Dict, Optional, Sequence

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATE_FILE = os.path.join(PROJECT_ROOT, "data", "wechat_import_state.json")
DECRYPT_CACHE_DIR = os.path.join(PROJECT_ROOT, "data", "wechat_decrypted")

# 消息类型映射
MSG_TYPE_NAMES = {
    1: "text",
    3: "image",
    34: "voice",
    43: "video",
    47: "emoji",
    49: "app",          # 文件 / 链接 / 转账 / 小程序卡片
    10000: "system",
    10002: "revoke",
}


class WeChatDBReader:
    """微信数据库读取器。

    Args:
        db_dir: 微信账号目录（4.x: E:\\xwechat_files\\wxid_xxx_yyy\\；
                3.x: ...\\WeChat Files\\wxid_xxx\\）
        key: 32 字节 hex 密钥
        version: "4.x" / "3.x"
        work_dir: 解密后明文库落盘目录；默认 data/wechat_decrypted/
    """

    def __init__(self, db_dir: str, key: str, version: str = "4.x",
                 work_dir: Optional[str] = None) -> None:
        self.db_dir = os.path.abspath(db_dir)
        self.key = key
        self.version = version
        self.work_dir = work_dir or DECRYPT_CACHE_DIR
        os.makedirs(self.work_dir, exist_ok=True)
        self._contacts: Dict[str, str] = {}   # wxid -> display_name

    # ------------------------------------------------------------------
    # 解密工具
    # ------------------------------------------------------------------
    def _decrypt_to_work(self, src_db: str,
                         encryption_type: Optional[str] = None) -> str:
        """把加密库解密到 work_dir 下同名 .db；若已是明文库则直接返回。"""
        from . import wechat_db_decryptor as dec
        rel = os.path.relpath(src_db, self.db_dir)
        out = os.path.join(self.work_dir, self.version, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if os.path.exists(out):
            return out
        et = encryption_type or ("sqlcipher4" if self.version == "4.x" else "wxsqlite3")
        if dec.detect_encryption_type(src_db) == "plain":
            import shutil
            shutil.copyfile(src_db, out)
            return out
        dec.decrypt_db(src_db, self.key, out, encryption_type=et)
        return out

    def _connect_plain(self, db_path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # 消息文件发现
    # ------------------------------------------------------------------
    def _list_message_dbs(self) -> List[str]:
        """枚举所有消息分片库（4.x message_*.db；3.x MSG*.db）。"""
        if self.version == "4.x":
            # 4.x 实际路径：<wxid_dir>/db_storage/message/message_*.db
            for sub in ("db_storage/message", "db_message"):
                pattern = os.path.join(self.db_dir, sub, "message_*.db")
                hits = sorted(glob.glob(pattern))
                if hits:
                    return hits
            return []
        else:
            pattern = os.path.join(self.db_dir, "Msg", "Multi", "MSG*.db")
        return sorted(glob.glob(pattern))

    def _find_session_db(self) -> Optional[str]:
        if self.version == "4.x":
            for sub in ("db_storage/session", "session"):
                p = os.path.join(self.db_dir, sub, "session.db")
                if os.path.exists(p):
                    return p
            return None
        p = os.path.join(self.db_dir, "Msg", "session", "session.db")
        return p if os.path.exists(p) else None

    def _find_contact_db(self) -> Optional[str]:
        if self.version == "4.x":
            for sub in ("db_storage/contact", "contact", "db_contact"):
                p = os.path.join(self.db_dir, sub)
                if os.path.isdir(p):
                    dbs = glob.glob(os.path.join(p, "*.db"))
                    if dbs:
                        return dbs[0]
                if os.path.isfile(p + ".db"):
                    return p + ".db"
        else:
            p = os.path.join(self.db_dir, "Msg", "MicroMsg.db")
            if os.path.exists(p):
                return p
        return None

    # ------------------------------------------------------------------
    # 表结构自适应
    # ------------------------------------------------------------------
    @staticmethod
    def _detect_message_table(conn: sqlite3.Connection) -> Optional[str]:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = [r[0] for r in rows]
        # 优先明确命名
        for pref in ("Message", "message", "MSG", "msg"):
            for n in names:
                if n.lower() == pref.lower():
                    return n
        for n in names:
            if "message" in n.lower() or n.upper().startswith("MSG"):
                return n
        return None

    def _column_map(self, conn: sqlite3.Connection, table: str) -> Dict[str, str]:
        """返回逻辑名 -> 实际列名 的映射（按大小写不敏感匹配）。"""
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        lowered = {c.lower(): c for c in cols}

        def pick(*cands):
            for c in cands:
                if c.lower() in lowered:
                    return lowered[c.lower()]
            return None

        return {
            "msg_id": pick("MsgSvrID", "server_id", "local_id", "msglocalId", "rowid"),
            "create_time": pick("CreateTime", "create_time", "createtime"),
            "talker": pick("TalkerId", "talker", "talker_id", "username", "peer"),
            "content": pick("StrContent", "content", "message_content", "strcontent", "msg"),
            "type": pick("Type", "message_type", "local_type", "msg_type"),
            "sub_type": pick("SubType", "sub_type", "messagesubtype"),
            "is_sender": pick("IsSender", "is_sender", "issend", "isSend"),
        }

    # ------------------------------------------------------------------
    # 标准化
    # ------------------------------------------------------------------
    def _normalize(self, row: sqlite3.Row, cmap: Dict[str, str]) -> Dict:
        def g(key):
            col = cmap.get(key)
            if not col:
                return None
            v = row[col]
            return v

        ts = g("create_time") or 0
        # 4.x 时间戳可能是毫秒
        if ts and ts > 10_000_000_000:
            ts = ts // 1000
        talker = g("talker") or ""
        msg_type = g("type") or 0
        sub_type = g("sub_type") or 0
        is_sender = g("is_sender") or 0
        if isinstance(is_sender, str):
            is_sender = 1 if is_sender in ("1", "true", "True") else 0
        return {
            "msg_id": g("msg_id"),
            "create_time": int(ts),
            "talker_id": talker,
            "content": (g("content") or ""),
            "msg_type": int(msg_type),
            "sub_type": int(sub_type),
            "is_sender": int(is_sender),
            "display_name": self._contacts.get(talker, talker),
            "type_name": MSG_TYPE_NAMES.get(int(msg_type), "other"),
        }

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def read_messages(self,
                      contact_filter: Optional[Sequence[str]] = None,
                      start_time: Optional[int] = None,
                      end_time: Optional[int] = None,
                      batch_size: int = 2000) -> Iterable[Dict]:
        """迭代读取所有消息（跨分片库合并）。

        Args:
            contact_filter: 只导出这些 wxid 的消息；None 表示全部
            start_time / end_time: Unix 秒
            batch_size: 每页拉取行数
        """
        # 先加载联系人昵称
        try:
            self._contacts = self.get_contacts_map()
        except Exception as e:  # noqa: BLE001
            logger.debug("加载联系人失败（不影响消息读取）：%s", e)
            self._contacts = {}

        db_files = self._list_message_dbs()
        if not db_files:
            logger.warning("未找到消息分片库：%s", self.db_dir)
            return

        for src in db_files:
            try:
                plain = self._decrypt_to_work(src)
            except Exception as e:  # noqa: BLE001
                logger.warning("解密 %s 失败：%s", src, e)
                continue
            try:
                conn = self._connect_plain(plain)
            except Exception as e:  # noqa: BLE001
                logger.warning("打开明文库失败 %s：%s", plain, e)
                continue
            try:
                table = self._detect_message_table(conn)
                if not table:
                    continue
                cmap = self._column_map(conn, table)
                if not cmap.get("create_time"):
                    continue
                # 构造 WHERE
                where = []
                params: list = []
                if contact_filter and cmap.get("talker"):
                    ph = ",".join("?" for _ in contact_filter)
                    where.append(f'"{cmap["talker"]}" IN ({ph})')
                    params.extend(contact_filter)
                if start_time and cmap.get("create_time"):
                    where.append(f'"{cmap["create_time"]}" >= ?')
                    params.append(start_time)
                if end_time and cmap.get("create_time"):
                    where.append(f'"{cmap["create_time"]}" <= ?')
                    params.append(end_time)
                where_sql = (" WHERE " + " AND ".join(where)) if where else ""
                # 按 create_time 游标分页
                last_ts = start_time or 0
                while True:
                    sql = (f'SELECT * FROM "{table}"{where_sql} '
                           f'AND "{cmap["create_time"]}" > ? '
                           f'ORDER BY "{cmap["create_time"]}" ASC LIMIT ?')
                    qparams = params + [last_ts, batch_size]
                    try:
                        rows = conn.execute(sql, qparams).fetchall()
                    except Exception:
                        # 退化：不带分页直接一次拉
                        sql2 = (f'SELECT * FROM "{table}"{where_sql} '
                                f'ORDER BY "{cmap["create_time"]}" ASC LIMIT ?')
                        rows = conn.execute(sql2, params + [batch_size * 5]).fetchall()
                    if not rows:
                        break
                    for row in rows:
                        normalized = self._normalize(row, cmap)
                        last_ts = max(last_ts, normalized["create_time"])
                        yield normalized
                    if len(rows) < batch_size:
                        break
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # 联系人 / 会话
    # ------------------------------------------------------------------
    def get_contacts_map(self) -> Dict[str, str]:
        """返回 {wxid: 昵称}。"""
        cdb = self._find_contact_db()
        out: Dict[str, str] = {}
        if not cdb or not os.path.exists(cdb):
            return out
        try:
            plain = self._decrypt_to_work(cdb)
            conn = self._connect_plain(plain)
            try:
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                for t in tables:
                    tl = t.lower()
                    if "contact" not in tl and "user" not in tl:
                        continue
                    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()]
                    low = {c.lower(): c for c in cols}
                    id_col = low.get("username") or low.get("wxid") or low.get("usrname")
                    name_col = (low.get("nickname") or low.get("nick")
                                or low.get("conRemark") or low.get("remark"))
                    if not id_col or not name_col:
                        continue
                    for row in conn.execute(
                            f'SELECT "{id_col}" AS u, "{name_col}" AS n FROM "{t}"').fetchall():
                        if row["u"]:
                            out[row["u"]] = row["n"] or row["u"]
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            logger.debug("读取联系人失败 %s：%s", cdb, e)
        return out

    def get_contact_list(self) -> List[Dict]:
        m = self.get_contacts_map()
        return [{"wxid": k, "display_name": v} for k, v in m.items()]

    def get_session_list(self) -> List[Dict]:
        """获取最近会话列表（粗略，用于 UI 展示）。"""
        sdb = self._find_session_db()
        if not sdb or not os.path.exists(sdb):
            return []
        out: List[Dict] = []
        try:
            plain = self._decrypt_to_work(sdb)
            conn = self._connect_plain(plain)
            try:
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                for t in tables:
                    if "session" not in t.lower():
                        continue
                    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()]
                    low = {c.lower(): c for c in cols}
                    id_col = low.get("username") or low.get("talker") or low.get("usrname")
                    ts_col = (low.get("modifydate") or low.get("lastmsgtime")
                              or low.get("create_time") or low.get("timestamp"))
                    if not id_col:
                        continue
                    sql = f'SELECT "{id_col}" AS u'
                    if ts_col:
                        sql += f', "{ts_col}" AS ts'
                    sql += f' FROM "{t}" ORDER BY ts DESC LIMIT 200'
                    for row in conn.execute(sql).fetchall():
                        out.append({"talker_id": row["u"],
                                    "last_time": row["ts"] if ts_col else 0})
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            logger.debug("读取会话列表失败：%s", e)
        return out

    # ------------------------------------------------------------------
    # 增量
    # ------------------------------------------------------------------
    @staticmethod
    def _state() -> Dict:
        if not os.path.exists(STATE_FILE):
            return {}
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _save_state(state: Dict) -> None:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def state_key(self, contact_filter: Optional[Sequence[str]] = None) -> str:
        base = os.path.basename(self.db_dir.rstrip("\\"))
        cf = "+".join(sorted(contact_filter)) if contact_filter else "*"
        return f"{self.version}|{base}|{cf}"

    def get_incremental_messages(self,
                                 contact_filter: Optional[Sequence[str]] = None,
                                 since_ts: Optional[int] = None) -> Iterable[Dict]:
        """只读取上次导入之后的新消息；读完自动更新 state。"""
        state = self._state()
        key = self.state_key(contact_filter)
        last_ts = since_ts or state.get(key, {}).get("last_create_time", 0)
        new_max = last_ts
        for msg in self.read_messages(contact_filter=contact_filter,
                                      start_time=last_ts + 1):
            new_max = max(new_max, msg["create_time"])
            yield msg
        state[key] = {"last_create_time": new_max,
                      "updated_at": __import__("time").time()}
        self._save_state(state)

    @staticmethod
    def reset_incremental(db_dir: str, version: str = "4.x",
                          contact_filter: Optional[Sequence[str]] = None) -> None:
        """重置指定库的增量游标。"""
        state = WeChatDBReader._state()
        base = os.path.basename(os.path.abspath(db_dir).rstrip("\\"))
        cf = "+".join(sorted(contact_filter)) if contact_filter else "*"
        key = f"{version}|{base}|{cf}"
        state.pop(key, None)
        WeChatDBReader._save_state(state)


# ---------------------------------------------------------------------------
# 双号合并
# ---------------------------------------------------------------------------
def merge_accounts(accounts_data: Sequence[Dict]) -> List[Dict]:
    """把多个账号的消息列表按 create_time 升序合并，并按 msg_id 去重。

    Args:
        accounts_data: [{messages: [...], account: "wxid_xxx"}, ...]
    """
    merged: List[Dict] = []
    seen = set()
    for acc in accounts_data:
        acc_name = acc.get("account", "")
        for m in acc.get("messages", []):
            mid = m.get("msg_id") or f"{m.get('create_time')}|{m.get('talker_id')}|{m.get('content')}"
            dedup_key = (acc_name, mid)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            m = dict(m)
            m["account"] = acc_name
            merged.append(m)
    merged.sort(key=lambda x: x.get("create_time", 0))
    return merged


__all__ = [
    "WeChatDBReader",
    "merge_accounts",
    "MSG_TYPE_NAMES",
]
