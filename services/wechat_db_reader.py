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
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
from typing import Iterable, List, Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 4.x 消息分片表名：`Msg_<32 位 md5>`，每个表 = **一个会话**（表名是会话 wxid 的 md5）。
# 实测（本机 52 个真实库）：713 个去重表名里 699 个能用 md5(contact/session 的 username) 还原出会话。
_MSG_SHARD_RE = re.compile(r"(?i)^msg_([0-9a-f]{32})$")

# 明确**不是**消息表的表名片段（4.x 库里混在 message_*.db 中的元数据 / FTS 影子表 / 资源表）。
# 必须排除，否则会把 MessageResourceInfo / message_fts_v4_0_data 之类当成消息源。
_NON_MESSAGE_NAME_HINTS = (
    "fts", "resource", "name2id", "timestamp", "sqlite_sequence",
    "delet", "compression", "group_time", "voiceinfo",
)
# 一张表要能当"消息表"，至少得有 时间 列 + (内容 或 类型) 列
_TIME_COLS = ("createtime", "create_time", "createtime_ms")
_CONTENT_COLS = ("strcontent", "content", "message_content", "msg")
_TYPE_COLS = ("type", "local_type", "message_type", "msg_type")

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
            # 另外 biz_message_*.db 里也是同构的 `Msg_<md5>` 分片表（公众号/服务号消息；
            # 本机实测 message 目录下 biz_message_0.db = 200 张分片表 / 2,784 行）。
            # 不纳入的话，"交出条数"会永远比"全部消息表 count(*)"少这一份（实测差 2,784）。
            for sub in ("db_storage/message", "db_message"):
                dirp = os.path.join(self.db_dir, sub)
                hits = sorted(glob.glob(os.path.join(dirp, "message_*.db")))
                hits += sorted(glob.glob(os.path.join(dirp, "biz_message_*.db")))
                if hits:
                    return sorted(set(hits))
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
    def _list_message_tables(conn: sqlite3.Connection,
                             include_empty: bool = False) -> List[str]:
        """列出库里**全部**真实消息表（4.x 是数十~数百张 Msg_<md5> 分片）。

        修复说明（原先只探测"一张"表）：4.x 每个 `message_*.db` 里有 194~280 张
        `Msg_<32位hex>` 分片表，每张对应一个会话；只取一张 → 只读到约 0.4% 的消息，
        且会在 `message_resource.db` 上误选 `MessageResourceInfo`（非消息表）。
        这里改为：按名字 + 列结构双重判定，返回**全部**消息表，按行数降序（大表优先）。
        """
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        except Exception as e:  # noqa: BLE001
            logger.debug("枚举表失败：%s", e)
            return []
        names = [r[0] for r in rows if r and r[0]]
        out: List[Tuple[int, str]] = []
        for n in names:
            nl = n.lower()
            if any(h in nl for h in _NON_MESSAGE_NAME_HINTS):
                continue
            try:
                cols = [r[1] for r in conn.execute('PRAGMA table_info("%s")' % n)]
            except Exception:  # noqa: BLE001
                continue
            low = {c.lower() for c in cols}
            has_time = bool(low & set(_TIME_COLS))
            has_body = bool(low & set(_CONTENT_COLS)) or bool(low & set(_TYPE_COLS))
            if not (has_time and has_body):
                continue          # 结构不像消息表 → 不收（MessageResourceInfo 就死在这一步）
            if _MSG_SHARD_RE.match(n):
                kind = 0          # 4.x 分片表：最高优先
            elif nl in ("message", "msgs", "msg", "message_table"):
                kind = 1          # 3.x 常见整表
            elif nl.startswith("msg") or "message" in nl:
                kind = 2          # 其它疑似
            else:
                kind = 3
            if kind == 3:
                continue
            try:
                cnt = conn.execute('SELECT count(*) FROM "%s"' % n).fetchone()[0]
            except Exception:  # noqa: BLE001
                cnt = 0
            if cnt == 0 and not include_empty:
                continue
            out.append((-cnt, kind, n))
        out.sort()
        return [t for _, _, t in out]

    @staticmethod
    def _detect_message_table(conn: sqlite3.Connection) -> Optional[str]:
        """取一张"最像消息表"的表（兼容旧调用点；新代码请用 `_list_message_tables`）。"""
        tabs = WeChatDBReader._list_message_tables(conn)
        return tabs[0] if tabs else None

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
        # 4.x 的 `local_type` 会在高 32 位携带标志位：实测最多的取值是 21474836529
        # （= 0x5_0000_0031，真实类型是低 32 位的 49 = app/文件/链接卡片）。
        # 不掩掉的话整表都会落进 MSG_TYPE_NAMES 的 "other"，类型字段等于没解析。
        # 掩码对 3.x 的小整数类型是无操作，不影响旧版本。
        try:
            msg_type = int(msg_type)
        except Exception:  # noqa: BLE001
            msg_type = 0
        if msg_type < 0 or msg_type > 0xFFFFFFFF:
            msg_type &= 0xFFFFFFFF
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

        # 4.x：一张分片表 = 一个会话（表名 = md5(会话 wxid)）→ 先建 md5->wxid 索引，
        # 用于给每条消息补上真实的 talker_id / display_name，以及让 contact_filter 真正生效。
        talker_index: Dict[str, str] = {}
        if self.version == "4.x":
            try:
                talker_index = self._build_talker_index()
            except Exception as e:  # noqa: BLE001
                logger.debug("构建会话 md5 索引失败（不影响全量读取）：%s", e)
                talker_index = {}

        filter_set = set(contact_filter) if contact_filter else None
        if filter_set and not any(True for _ in filter_set):
            filter_set = None
        skipped_tables = 0

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
                tables = self._list_message_tables(conn)
                if not tables:
                    continue
                for table in tables:
                    cmap = self._column_map(conn, table)
                    if not cmap.get("create_time"):
                        continue
                    # 4.x：用表名还原这张表属于哪个会话
                    table_talker: Optional[str] = None
                    if self.version == "4.x":
                        m = _MSG_SHARD_RE.match(table)
                        if m:
                            table_talker = talker_index.get(m.group(1).lower())

                    # ---- WHERE 构造：永远不能拼出 "FROM t AND ..." 这种非法 SQL ----
                    where: List[str] = []
                    params: List[object] = []
                    if self.version != "4.x":
                        # 3.x 有真实 talker 列，可在 SQL 里过滤
                        if filter_set and cmap.get("talker"):
                            ph = ",".join("?" for _ in filter_set)
                            where.append(f'"{cmap["talker"]}" IN ({ph})')
                            params.extend(sorted(filter_set))
                        elif filter_set:
                            skipped_tables += 1
                            continue
                    if start_time is not None:
                        where.append(f'"{cmap["create_time"]}" >= ?')
                        params.append(start_time)
                    if end_time is not None:
                        where.append(f'"{cmap["create_time"]}" <= ?')
                        params.append(end_time)
                    filter_sql = (" AND " + " AND ".join(where)) if where else ""

                    # 4.x 的过滤在表级完成（一张表=一个会话）
                    if self.version == "4.x" and filter_set:
                        if not table_talker or table_talker not in filter_set:
                            skipped_tables += 1
                            continue

                    ct = cmap["create_time"]
                    # ---- 复合游标 (create_time, rowid)：保证"恰好一次"遍历 ----
                    # 原实现只用 `create_time > ?`，同一秒的多条会被跨页跳过；
                    # 且无过滤时 SQL 语法错 → 兜底语句不带游标 → 无限重放。
                    cursor_ts = start_time if start_time is not None else -1
                    cursor_rid = -1
                    while True:
                        sql = (
                            f'SELECT *, rowid AS __rid FROM "{table}" '
                            f'WHERE ("{ct}" > ? OR ("{ct}" = ? AND rowid > ?)){filter_sql} '
                            f'ORDER BY "{ct}" ASC, rowid ASC LIMIT ?'
                        )
                        qparams = [cursor_ts, cursor_ts, cursor_rid] + params + [batch_size]
                        try:
                            rows = conn.execute(sql, qparams).fetchall()
                        except Exception:  # noqa: BLE001
                            # 退路：万一该表不支持 rowid（WITHOUT ROWID），只用时间游标。
                            # 注意：**仍然带游标**，绝不回到"无游标兜底"（那会无限重放）。
                            sql2 = (f'SELECT * FROM "{table}" '
                                    f'WHERE "{ct}" > ?{filter_sql} '
                                    f'ORDER BY "{ct}" ASC LIMIT ?')
                            try:
                                rows = conn.execute(
                                    sql2, [cursor_ts] + params + [batch_size]).fetchall()
                            except Exception as e2:  # noqa: BLE001
                                logger.warning("读取表 %s 失败：%s", table, e2)
                                break
                        if not rows:
                            break
                        for row in rows:
                            normalized = self._normalize(row, cmap)
                            # 游标必须用**原始列值**（_normalize 会把毫秒除以 1000）
                            try:
                                raw_ts = row[ct]
                            except Exception:  # noqa: BLE001
                                raw_ts = normalized["create_time"]
                            if raw_ts is None:
                                raw_ts = cursor_ts
                            try:
                                rid = row["__rid"]
                            except Exception:  # noqa: BLE001
                                rid = cursor_rid + 1
                            cursor_ts, cursor_rid = raw_ts, rid
                            if table_talker:
                                normalized["talker_id"] = table_talker
                                normalized["display_name"] = self._contacts.get(
                                    table_talker, table_talker)
                            yield normalized
                        if len(rows) < batch_size:
                            break
            finally:
                conn.close()
        if skipped_tables:
            logger.debug("contact_filter 生效：跳过无法归属到目标会话的表 %d 张", skipped_tables)

    # ------------------------------------------------------------------
    # 联系人 / 会话
    # ------------------------------------------------------------------
    def _build_talker_index(self) -> Dict[str, str]:
        """建 `md5(会话 wxid) -> wxid` 索引（4.x 分片表名就是这个 md5）。

        数据来源（三处并集）：contact 库 + session 库的 username 列 + 各消息库
        `Name2Id.user_name`（后者能补上已退群/已删好友的残留会话）。
        实测覆盖率（本机真实库）：仅前两源时 713 个去重分片表名命中 699 个（97.9%）；
        加上 `Name2Id` 后，按行数计的空 talker 从 178,609 条降到约 1.4 千条。
        仍未命中的表按"会话未知"处理：talker_id 留空；带 contact_filter 时跳过该表
        （宁可少给，也不把不属于目标会话的消息混进去）。
        """
        cands = set()
        try:
            cands.update(self.get_contacts_map().keys())
        except Exception as e:  # noqa: BLE001
            logger.debug("取联系人失败：%s", e)
        try:
            cands.update(self._all_session_talkers())
        except Exception as e:  # noqa: BLE001
            logger.debug("取会话标识失败：%s", e)
        try:
            cands.update(self._message_db_talkers())
        except Exception as e:  # noqa: BLE001
            logger.debug("取消息库 Name2Id 失败：%s", e)
        idx: Dict[str, str] = {}
        for c in cands:
            if not c:
                continue
            idx[hashlib.md5(c.encode("utf-8")).hexdigest().lower()] = c
        return idx

    def _message_db_talkers(self) -> List[str]:
        """从各消息库的 `Name2Id` 表取会话标识（列 `user_name`）。

        为什么需要它：contact / session 库并不含全部会话。实测本机 18 张未还原的分片表里，
        有 4 张（合计 ≈177,158 行，含一个 159,816 行的大群）只能从消息库自带的
        `Name2Id.user_name` 里找到 wxid（已退群/已删好友的会话残留）。补上这一源之后，
        空 `talker_id` 从 178,609 条降到约 1.4 千条（见 evidence_51/52）。
        代价：需要把消息库解密出来才能读它；而 `read_messages` 本来也要解密同一批库
        （解密结果会缓存复用），所以总解密量不变。
        """
        out: List[str] = []
        for src in self._list_message_dbs():
            try:
                plain = self._decrypt_to_work(src)
            except Exception as e:  # noqa: BLE001
                logger.debug("Name2Id 预扫：解密 %s 失败：%s", src, e)
                continue
            try:
                conn = self._connect_plain(plain)
            except Exception:  # noqa: BLE001
                continue
            try:
                try:
                    rows = conn.execute('SELECT user_name FROM "Name2Id"').fetchall()
                except Exception:  # noqa: BLE001
                    continue          # 该库没有 Name2Id（media_0 / message_fts 等）
                for r in rows:
                    if r[0]:
                        out.append(str(r[0]))
            finally:
                conn.close()
        return out

    def _all_session_talkers(self) -> List[str]:
        """session 库里的**全部**会话标识（`get_session_list` 只取 200 条，不够做索引）。"""
        sdb = self._find_session_db()
        if not sdb or not os.path.exists(sdb):
            return []
        out: List[str] = []
        try:
            plain = self._decrypt_to_work(sdb)
            conn = self._connect_plain(plain)
            try:
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                for t in tables:
                    if "session" not in t.lower():
                        continue
                    cols = [r[1] for r in conn.execute(
                        f'PRAGMA table_info("{t}")').fetchall()]
                    low = {c.lower(): c for c in cols}
                    col = low.get("username") or low.get("talker") or low.get("usrname")
                    if not col:
                        continue
                    try:
                        for row in conn.execute(f'SELECT "{col}" FROM "{t}"'):
                            if row[0]:
                                out.append(str(row[0]))
                    except Exception:  # noqa: BLE001
                        continue
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            logger.debug("读取会话标识失败 %s：%s", sdb, e)
        return out

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
