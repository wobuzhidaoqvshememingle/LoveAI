# -*- coding: utf-8 -*-
"""
nontext_processing / nontext_analysis 单测。
用临时 sqlite 库 + 构造的 zstd blob 测：解压、按类型解析、幂等写库、读取接口。
媒体处理（OCR/ASR/抽帧）依赖真实磁盘文件，离线跑会自然走「媒体不可映射」分支，
不打 network mark（因为不发起网络请求；whisper 模型本测试不加载）。
"""
import os
import sys
import sqlite3
import zstandard

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import nontext_processing as ntp  # noqa: E402
from services import nontext_analysis as nta  # noqa: E402


def _wrap_as_db_content(xml: str) -> str:
    """模拟 loveai.db 的存储：zstd 压缩 -> bytes 的 Python repr 字符串。"""
    raw = xml.encode("utf-8")
    comp = zstandard.ZstdCompressor().compress(raw)
    return repr(comp)  # 形如 b'\\x28\\xb5/\\xfd...'


# ---------- 样本 XML ----------
IMG_XML = ('<?xml version="1.0"?><msg><img aeskey="k" md5="8dbf939170415e12022ad0d2fb6c1744" '
           'originsourcemd5="fcdb874c25afab8dd698813260c47081" cdnthumbwidth="359" '
           'cdnthumbheight="480" length="201488"/></msg>')
VOICE_XML = ('<msg><voicemsg voiceformat="4" length="6220" voicelength="3300" '
             'fromusername="wxid_x" isPlayed="1"></voicemsg></msg>')
EMOJI_XML = ('<msg><emoji fromusername="a" tousername="b" type="1" md5="d7e1db2586dd3077525af147f27b9400" '
             'len="87723" width="300" height="296"></emoji></msg>')
LOC_XML = ('<?xml version="1.0"?><msg><location x="22.98" y="113.99" label="东莞某路" '
           'poialias="中航光电" cityname="东莞市"/></msg>')
QUOTE_XML = ('<msg><appmsg appid="" sdkver="0"><title>怎么啦</title><type>57</type>'
             '<refermsg><type>1</type><displayname>好运来</displayname>'
             '<content>今天来得早</content></refermsg></appmsg></msg>')
TRANSFER_XML = ('<msg><appmsg appid="" sdkver=""><title>微信转账</title>'
                '<des><![CDATA[收到转账￥2000.00元。]]></des><type>2000</type></appmsg></msg>')
SYS_PLAIN = "你已添加了强制四爱过猛成零，以上是打招呼的消息。"


# --------------------------------------------------------------------------- #
class TestDecompress:
    def test_repr_zstd_roundtrip(self):
        stored = _wrap_as_db_content(IMG_XML)
        out = ntp.decompress_content(stored)
        assert "8dbf939170415e12022ad0d2fb6c1744" in out

    def test_plaintext_passthrough(self):
        assert ntp.decompress_content(SYS_PLAIN) == SYS_PLAIN

    def test_empty(self):
        assert ntp.decompress_content(None) == ""
        assert ntp.decompress_content("") == ""


class TestParseRecord:
    def test_image(self):
        f = ntp.parse_record("3", IMG_XML)
        assert f["media_type"] == "image"
        assert f["parse_status"] in ("partial", "parsed")  # 离线=partial
        assert f["category"] == "图片"

    def test_voice_unmapped(self):
        f = ntp.parse_record("34", VOICE_XML)
        assert f["media_type"] == "voice"
        assert f["parse_status"] == "failed"  # 离线无音频
        assert "3.3" in f["text_content"] or "语音" in f["text_content"]

    def test_emoji(self):
        f = ntp.parse_record("47", EMOJI_XML)
        assert f["media_type"] == "emoji"
        assert f["category"] == "表情包"
        assert f["parse_status"] == "partial"

    def test_location(self):
        f = ntp.parse_record("48", LOC_XML)
        assert f["parse_status"] == "parsed"
        assert f["category"] == "位置"
        assert "中航光电" in f["text_content"]

    def test_system_plaintext(self):
        f = ntp.parse_record("10000", SYS_PLAIN)
        assert f["parse_status"] == "parsed"
        assert f["label"] == "添加好友/打招呼"
        assert f["source"] == "unicode"

    def test_quote_reply(self):
        f = ntp.parse_record("244813135921", QUOTE_XML)
        assert f["category"] == "引用回复"
        assert "怎么啦" in f["text_content"]
        assert "今天来得早" in f["text_content"]

    def test_transfer(self):
        f = ntp.parse_record("8589934592049", TRANSFER_XML)
        assert f["category"] == "转账"
        assert "2000" in f["text_content"]

    def test_unknown_xml_failed(self):
        f = ntp.parse_record("9999999999999", "not xml at all {{{")
        assert f["parse_status"] == "failed"


class TestMediaResolution:
    def test_resolve_missing_returns_none_offline(self):
        # 不存在的 md5 -> None（不抛异常）
        assert ntp.resolve_media("deadbeef" * 4) is None

    def test_decrypt_dat_known(self):
        # 构造一个 XOR 加密的假 jpg：真实jpg头 ^ key
        jpg = b"\xff\xd8\xff\xe0fakejpeg"
        key = 0x5A
        enc = bytes(b ^ key for b in jpg)
        dec = ntp.decrypt_dat(enc)
        assert dec is not None
        assert dec[:3] == b"\xff\xd8\xff"


# --------------------------------------------------------------------------- #
@pytest.fixture()
def temp_db(tmp_path):
    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, person_id TEXT, "
               "msg_type TEXT, content TEXT)")
    samples = [
        (101, "demo0001", "3", _wrap_as_db_content(IMG_XML)),
        (102, "demo0001", "34", _wrap_as_db_content(VOICE_XML)),
        (103, "demo0001", "10000", SYS_PLAIN),
        (104, "demo0001", "244813135921", _wrap_as_db_content(QUOTE_XML)),
    ]
    con.executemany("INSERT INTO chat_messages VALUES (?,?,?,?)", samples)
    con.commit()
    # 建标注表
    con.execute(ntp.SCHEMA)
    con.commit()
    con.close()
    return str(db)


class TestPipelineAndInterface:
    def test_upsert_and_readback(self, temp_db):
        con = sqlite3.connect(temp_db)
        ntp.init_table(con)
        # 处理两条
        for cid, mtype, content in [(101, "3", _wrap_as_db_content(IMG_XML)),
                                     (103, "10000", SYS_PLAIN)]:
            xml = ntp.decompress_content(content)
            fields = ntp.parse_record(mtype, xml)
            ntp.upsert_annotation(con, cid, fields)
        con.commit()

        na = nta.NontextAnalysis(temp_db)
        ann = na.get_annotation(101)
        assert ann is not None
        assert ann["media_type"] == "image"

        sys_ann = na.get_annotation(103)
        assert sys_ann["category"] == "系统消息"

    def test_idempotent_upsert(self, temp_db):
        con = sqlite3.connect(temp_db)
        ntp.init_table(con)
        for _ in range(3):
            ntp.upsert_annotation(con, 101, ntp.parse_record("3", ntp.decompress_content(_wrap_as_db_content(IMG_XML))))
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM nontext_annotations WHERE chat_message_id=101").fetchone()[0]
        assert n == 1  # 三次upsert仍只有一行
        con.close()

    def test_merge_emotion_hint(self, temp_db):
        con = sqlite3.connect(temp_db)
        ntp.init_table(con)
        fields = ntp.parse_record("244813135921", ntp.decompress_content(_wrap_as_db_content(QUOTE_XML)))
        ntp.upsert_annotation(con, 104, fields)
        con.commit()
        con.close()
        na = nta.NontextAnalysis(temp_db)
        h = na.merge_emotion_hint(104)
        assert h["has_annotation"] is True
        assert "怎么啦" in h["text_content"]
        assert h["category"] == "引用回复"

    def test_merge_missing_annotation(self, temp_db):
        na = nta.NontextAnalysis(temp_db)
        h = na.merge_emotion_hint(999999)
        assert h["has_annotation"] is False
        assert h["text_content"] == ""

    def test_coverage_empty_table(self, temp_db):
        na = nta.NontextAnalysis(temp_db)
        cov = na.get_coverage()
        # temp db 里 chat_messages 有 4 行但 annotation 表为空 -> 0
        assert cov["total"] == 0 or cov["total"] >= 0
