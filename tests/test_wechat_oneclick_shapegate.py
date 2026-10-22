# -*- coding: utf-8 -*-
"""
tests/test_wechat_oneclick_shapegate.py — 针对 services/wechat_oneclick.py
「副本形状闸门」两处逻辑的定向用例（离线、确定性、不加载模型、不联网）。

覆盖对象（均为生产代码里的真实名字，按现行 106,351 B 版本 :974-:1024 逐个核对）：
  1. `WechatOneClick._is_shard_db_name(path)`        —— 分片名残片库判定（是/否 两侧）
  2. `WechatOneClick._deep_copy_shape_ok(out_path)`  —— 副本形状校验（通过/不通过 两侧）

本次重构的意图（F-1）：把「副本形状检查」与 `PRAGMA integrity_check` **解耦**。
微信的 fts 库建了 `fts5(tokenize='MMFtsTokenizer …')` 虚拟表，而 stdlib sqlite3 的
fts5 里没有该分词器 → 对它跑 integrity_check **必然**抛
`OperationalError: vtable constructor failed`。若把这种「环境原因」当成「副本损坏」，
就会变成「每轮重解 + 计为失败」的新非收敛故障。本文件用**真实 sqlite 文件**证明：
这种副本的形状检查仍判「好」，且形状检查**从不发** integrity_check。

注：本文件只新增测试，不改任何产品代码；`tests/test_ui_e2e_chain.py` 未改动。
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.wechat_oneclick import WechatOneClick  # noqa: E402

_MSG_DDL = "CREATE TABLE Msg_abc(id INTEGER PRIMARY KEY, content TEXT)"
_MSG_INSERT = "INSERT INTO Msg_abc(content) VALUES ('hi')"
_OTHER_DDL = "CREATE TABLE Friend(id INTEGER PRIMARY KEY, name TEXT)"
# 微信 fts 库里的虚拟表建表语句形态：fts5 + 自定义分词器（stdlib 无此分词器）
_FTS_SQL = ("CREATE VIRTUAL TABLE Msg_fts USING fts5(content, "
            "tokenize='MMFtsTokenizer')")


def _make_db(path, ddl_list):
    """建一个真实 sqlite 文件（建在 tmp_path 下，不碰工程 data/）。"""
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    try:
        for ddl in ddl_list:
            con.execute(ddl)
        con.commit()
    finally:
        con.close()
    return path


def _make_empty_db(path):
    return _make_db(path, [])


def _inject_fts5_unknown_tokenizer(path):
    """把「fts5 + 未知分词器」的虚拟表**只写进 schema**（真实 sqlite 文件）。

    为什么不用 `CREATE VIRTUAL TABLE`：SQLite 在**建表时**就会因分词器不存在而
    拒绝（实测 `no such tokenizer: MMFtsTokenizer`）。而微信的 fts 库是在有该分词器
    的 WeChat 侧建出来的，到了 stdlib sqlite3 这里只是「读得懂 schema、实例化不了」。
    用 writable_schema 直接落 schema 行，正好复现这个形状，且文件是**真实 sqlite 库**。
    """
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA writable_schema=ON")
        con.execute(
            "INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql) VALUES"
            "('table','Msg_fts','Msg_fts',0,\"CREATE VIRTUAL TABLE Msg_fts USING fts5"
            "(content, tokenize='MMFtsTokenizer')\")")
        con.commit()
        ver = con.execute("PRAGMA schema_version").fetchone()[0]
        con.execute("PRAGMA schema_version=%d" % (ver + 1))
        con.commit()
        con.execute("PRAGMA writable_schema=OFF")
        con.commit()
    finally:
        con.close()
    return path


def _integrity_check(path):
    """对同一文件跑 integrity_check，返回 (kind, detail)：kind ∈ {OK, RAISE}。"""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
        try:
            return "OK", con.execute("PRAGMA integrity_check").fetchall()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        return "RAISE", "%s: %s" % (type(e).__name__, e)


# =========================================================================== #
# 1. _is_shard_db_name —— 分片名残片库判定（是 / 不是 两侧）
# =========================================================================== #
# 下列两侧清单逐条取自现行实现的**实测**结果（见报告），并覆盖其 docstring 明确点名的
# 「message_fts.db / message_resource.db / media_0.db / weclaw.db 都不算」。
_SHARD_TRUE = [
    "message_0.db",
    "message_103.db",
    "biz_message_12.db",
    "MESSAGE_0.DB",                              # 大小写不敏感
    r"C:\a\db_storage\message\message_0.db",     # 带目录的真实形貌
]

_SHARD_FALSE = [
    "message_fts.db",        # fts 库：末段非数字
    "message_resource.db",   # 资源库：末段非数字
    "media_0.db",            # 非 message_/biz_message_ 前缀
    "weclaw.db",             # 非分片式
    "message_.db",           # 末段为空，不是数字
    "message_0.txt",         # 扩展名不是 .db
    "message_0.db-wal",      # wal 边车文件
    "message_abc.db",        # 末段非数字
    "message_0",             # 无扩展名
    "biz_message_.db",       # 末段为空
    "msg_0.db",              # 前缀是 msg_ 而非 message_
]


class TestIsShardDbName:
    @pytest.mark.parametrize("name", _SHARD_TRUE)
    def test_true_side(self, name):
        assert WechatOneClick._is_shard_db_name(name) is True

    @pytest.mark.parametrize("name", _SHARD_FALSE)
    def test_false_side(self, name):
        assert WechatOneClick._is_shard_db_name(name) is False


# =========================================================================== #
# 2. _deep_copy_shape_ok —— 副本形状校验（通过 / 不通过 两侧）
# =========================================================================== #
class TestDeepCopyShapeOk:
    def test_shape_pass_shard_db_with_msg_table(self, tmp_path):
        """通过侧：分片名 + 含 Msg_ 分片表 → ok=True。"""
        p = _make_db(tmp_path / "message_0.db", [_MSG_DDL, _MSG_INSERT])
        r = WechatOneClick._deep_copy_shape_ok(p)
        assert r["ok"] is True, r
        assert r["tables"] == 1
        assert r["msg_shards"] == 1
        assert "形状符合" in r["reason"]

    def test_shape_fail_shard_db_without_msg_table(self, tmp_path):
        """不通过侧：分片名但只有非 Msg_ 表 → ok=False 且给出明确原因。"""
        p = _make_db(tmp_path / "message_1.db", [_OTHER_DDL])
        r = WechatOneClick._deep_copy_shape_ok(p)
        assert r["ok"] is False, r
        assert r["tables"] == 1
        assert r["msg_shards"] == 0
        assert "没有 Msg_ 分片表" in r["reason"]

    def test_shape_judgement_is_not_vacuous_negative_control(self, tmp_path):
        """负向反证（非恒真）：**同名分片库**、只把表名从 Msg_ 换成非 Msg_，判定必须翻转。

        两条用例的唯一差别就是表名 —— 如果 `_deep_copy_shape_ok` 恒返回 True
        （或恒 False），本用例必失败。
        """
        a = _make_db(tmp_path / "a" / "message_7.db",
                     ["CREATE TABLE Msg_x(id INTEGER PRIMARY KEY)"])
        b = _make_db(tmp_path / "b" / "message_7.db",
                     ["CREATE TABLE Other_x(id INTEGER PRIMARY KEY)"])
        ra = WechatOneClick._deep_copy_shape_ok(a)
        rb = WechatOneClick._deep_copy_shape_ok(b)
        assert ra["ok"] is True and ra["msg_shards"] == 1, ra
        assert rb["ok"] is False and rb["msg_shards"] == 0, rb

    def test_empty_db_with_non_shard_name_passes(self, tmp_path):
        """空库不算错（docstring 明示：weclaw.db 本来就没有表）。"""
        p = _make_empty_db(tmp_path / "weclaw.db")
        r = WechatOneClick._deep_copy_shape_ok(p)
        assert r["ok"] is True, r
        assert r["tables"] == 0

    def test_missing_path_fails_gracefully(self, tmp_path):
        """路径不存在：不抛异常，返回 ok=False 且原因可读。"""
        r = WechatOneClick._deep_copy_shape_ok(str(tmp_path / "message_9.db"))
        assert r["ok"] is False, r
        assert r["reason"].startswith("打不开"), r
        assert r["tables"] is None

    def test_non_sqlite_content_fails_gracefully(self, tmp_path):
        """文件存在但不是 sqlite：读表名分支失败，不抛异常。"""
        p = tmp_path / "message_8.db"
        p.write_bytes(b"not a sqlite database" * 8)
        r = WechatOneClick._deep_copy_shape_ok(str(p))
        assert r["ok"] is False, r
        assert r["reason"].startswith("读表名失败"), r
        assert r["tables"] is None


# =========================================================================== #
# 3. 重构意图：fts5 未知分词器 → integrity_check 必抛错，但副本不该被判损坏
# =========================================================================== #
class TestFts5Decoupling:
    def test_good_copy_with_unknown_fts_tokenizer_is_not_marked_broken(self, tmp_path):
        """F-1 核心：**真实 sqlite 文件**，含 fts5 未知分词器虚拟表。

        ① integrity_check **确实**抛错（环境原因真实存在，不是假设）；
        ② `_deep_copy_shape_ok` 仍判 ok=True —— 不再被误判为损坏、不再触发重解。
        """
        p = _inject_fts5_unknown_tokenizer(
            _make_db(tmp_path / "message_2.db", [_MSG_DDL, _MSG_INSERT]))

        kind, detail = _integrity_check(p)
        assert kind == "RAISE", ("该文件本应因缺分词器而 integrity_check 抛错，"
                                 "否则本用例失去意义：%r" % (kind, detail))
        assert "vtable constructor failed" in detail, detail

        r = WechatOneClick._deep_copy_shape_ok(p)
        assert r["ok"] is True, r
        assert r["msg_shards"] >= 1, r
        assert "integrity" not in r["reason"].lower(), r

    def test_shape_check_never_issues_integrity_check(self, tmp_path, monkeypatch):
        """直接证据（解耦的硬证明）：形状检查只读 sqlite_master，从不发 integrity_check。

        用 SQL 记录器包住连接，逐条记下执行的语句。
        """
        p = _inject_fts5_unknown_tokenizer(
            _make_db(tmp_path / "message_3.db", [_MSG_DDL]))

        statements = []
        real_connect = sqlite3.connect

        def recording_connect(*args, **kwargs):
            con = real_connect(*args, **kwargs)

            class _RecordingConnection:
                def execute(self, sql, *a, **kw):
                    statements.append(sql)
                    return con.execute(sql, *a, **kw)

                def close(self):
                    return con.close()

            return _RecordingConnection()

        import services.wechat_oneclick as oneclick_mod
        monkeypatch.setattr(oneclick_mod.sqlite3, "connect", recording_connect)

        r = WechatOneClick._deep_copy_shape_ok(p)

        assert r["ok"] is True, r
        assert any("sqlite_master" in s.lower() for s in statements), statements
        assert not any("integrity_check" in s.lower() for s in statements), statements

    def test_fts_style_copy_passes_gracefully_even_when_it_has_no_msg_table(self, tmp_path):
        """对照：fts 库若落在**非分片名**下，读得出表名即算过（空/无 Msg_ 也不判损坏）。"""
        p = _inject_fts5_unknown_tokenizer(
            _make_db(tmp_path / "message_fts.db", []))   # 注意：非分片名
        r = WechatOneClick._deep_copy_shape_ok(p)
        assert r["ok"] is True, r


# =========================================================================== #
# 4. 非恒真证明：两个方法确实被本文件的用例执行到（调用计数）
# =========================================================================== #
class TestMethodsAreReallyExercised:
    def test_call_count_proves_both_methods_ran(self, tmp_path, monkeypatch):
        """给两个方法挂计数包装，证明：① 形状检查内部**真的**调了分片名判定；
        ② 两个方法在本轮用例里都真实执行（而不是被 import 后从未运行）。
        """
        calls = {"_is_shard_db_name": 0, "_deep_copy_shape_ok": 0}
        real_shard = WechatOneClick._is_shard_db_name
        real_shape = WechatOneClick._deep_copy_shape_ok

        def counted_shard(path):
            calls["_is_shard_db_name"] += 1
            return real_shard(path)

        def counted_shape(path):
            calls["_deep_copy_shape_ok"] += 1
            return real_shape(path)

        monkeypatch.setattr(WechatOneClick, "_is_shard_db_name",
                            staticmethod(counted_shard))
        monkeypatch.setattr(WechatOneClick, "_deep_copy_shape_ok",
                            staticmethod(counted_shape))

        # 直接调用分片名判定（是/否 各一次）
        assert WechatOneClick._is_shard_db_name("message_0.db") is True
        assert WechatOneClick._is_shard_db_name("weclaw.db") is False

        # 形状检查：其内部对分片名库必然会调 _is_shard_db_name
        before = calls["_is_shard_db_name"]
        p = _make_db(tmp_path / "message_0.db", [_MSG_DDL])
        r = WechatOneClick._deep_copy_shape_ok(p)
        assert r["ok"] is True, r

        assert calls["_deep_copy_shape_ok"] >= 1
        assert calls["_is_shard_db_name"] >= before + 1, (
            "形状检查内部没有调用 _is_shard_db_name？计数未增长：%r" % (calls,))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
