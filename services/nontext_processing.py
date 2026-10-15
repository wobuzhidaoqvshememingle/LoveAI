# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 非文本消息高深度处理流水线 (nontext_processing)
================================================================

处理 chat_messages 中 person_id='demo0001' (蛋卷) 的非文本消息：
  - 47 自定义表情  3 图片  34 语音  43 视频  50 VoIP通话  48 位置
  - 10000 系统消息  + 大数位掩码 local_type（实为 appmsg XML 分享卡片：
    引用回复/转账/红包/音乐分享/聊天记录转发/文件/行程卡片/小程序链接）

【关键数据事实（侦察确认，不可伪造）】
1. loveai.db.chat_messages.content：文本消息是明文；非文本消息存的是
   Python repr 字符串 b'\\x28\\xb5/\\xfd...'，还原成 bytes 后是 zstd 帧
   (magic 28 B5 2F FD)，解压得到 XML。本模块 decompress_content() 处理。
2. 媒体文件可达性 = **不可映射**（已双向验证）：
   - E:\\xwechat_files\\...\\msg\\attach 下 .dat 是 XOR 加密图，但文件名
     是微信内部缓存 key，**不等于内容 md5**（实测解密后 md5≠文件名），
     且该文件名不出现在任何消息 XML / 解密 message db / danjuan 源库中，
     无 join 键。
   - data\\wechat_decrypted 里的 message_0.db 是 5 行测试夹具，无媒体索引表。
   因此：图片不做像素 OCR、语音不做音频转写、视频不抽帧 —— 这些 code path
   已写好（resolve_media/ocr_image/transcribe_voice/extract_video_frame），
   但因 resolve_media() 恒返回 None 而走「媒体不可及」分支，如实标注。
3. 真正能高深度提取的是 **解压后的 XML 文本**：引用回复正文、转账金额、
   红包、音乐歌词、聊天记录、位置 POI、系统文案、通话状态。

写入表 nontext_annotations（幂等，按 chat_message_id upsert）。
"""
from __future__ import annotations

import ast
import io
import logging
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("LoveAI.nontext")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOVAI_DB = os.path.join(PROJECT_ROOT, "data", "loveai.db")
MEDIA_INDEX = os.path.join(PROJECT_ROOT, "data", "_media_index.json")

PERSON_ID = "demo0001"

# zstd 懒加载（可选依赖，缺失时仅影响解压）
try:
    import zstandard  # type: ignore
    _ZSTD_DCTX = zstandard.ZstdDecompressor()
except Exception:  # pragma: no cover
    _ZSTD_DCTX = None


# --------------------------------------------------------------------------- #
# 解压
# --------------------------------------------------------------------------- #
def to_bytes(raw: Any) -> bytes:
    """content 列 -> bytes。处理 sqlite TEXT(明文或 repr) / BLOB。"""
    if raw is None:
        return b""
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s[:2] in ("b'", 'b"'):
            try:
                v = ast.literal_eval(s)
                if isinstance(v, bytes):
                    return v
            except Exception:
                pass
        return s.encode("utf-8")
    try:
        return bytes(raw)
    except Exception:
        return b""


def decompress_content(raw: Any) -> str:
    """content -> 解压后的文本（XML 或明文）。解压失败返回原始可解码串。"""
    b = to_bytes(raw)
    if len(b) >= 4 and b[0] == 0x28 and b[1] == 0xB5 and b[2] == 0x2F and b[3] == 0xFD:
        if _ZSTD_DCTX is None:
            return ""
        try:
            out = _ZSTD_DCTX.decompress(b, max_output_size=256 * 1024 * 1024)
        except Exception:
            try:
                out = _ZSTD_DCTX.stream_reader(io.BytesIO(b)).read()
            except Exception:
                out = b
        return out.decode("utf-8", errors="replace")
    # 明文（文本消息 / 系统消息）
    if isinstance(raw, str):
        return raw
    return b.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# 媒体解析（已验证不可映射；保留 code path）
# --------------------------------------------------------------------------- #
_MEDIA_INDEX_CACHE: Optional[dict] = None


def _load_media_index() -> dict:
    global _MEDIA_INDEX_CACHE
    if _MEDIA_INDEX_CACHE is not None:
        return _MEDIA_INDEX_CACHE
    try:
        import json
        with open(MEDIA_INDEX, encoding="utf-8") as f:
            _MEDIA_INDEX_CACHE = json.load(f)
    except Exception as e:
        logger.warning("媒体索引加载失败 %s: %s", MEDIA_INDEX, e)
        _MEDIA_INDEX_CACHE = {}
    return _MEDIA_INDEX_CACHE


def resolve_media(*md5s: str) -> Optional[dict]:
    """按候选 md5 在磁盘媒体索引中找文件。侦察结论：恒返回 None。
    保留此函数以便将来媒体可达时无缝启用。"""
    idx = _load_media_index()
    for m in md5s:
        if m and m in idx:
            return idx[m]
    return None


_MAGICS = [(b"\xff\xd8\xff", "jpg"), (b"\x89PNG", "png"), (b"GIF8", "gif"),
           (b"BM", "bmp"), (b"RIFF", "webp")]


def decrypt_dat(data: bytes) -> Optional[bytes]:
    """微信 .dat 单字节 XOR 解密。命中图片魔数则返回解密字节。"""
    if len(data) < 4:
        return None
    for magic, ext in _MAGICS:
        k = data[0] ^ magic[0]
        if all((data[i] ^ k) == magic[i] for i in range(len(magic))):
            return bytes(b ^ k for b in data)
    return None


def ocr_image_path(path: str) -> str:
    """对已解密图片文件做 rapidocr。仅在媒体可达时调用。"""
    try:
        from services.ocr_service import OCRService
        svc = OCRService()
        r = svc.recognize_image(path)
        return (r.get("text") or "").strip()
    except Exception as e:
        logger.warning("OCR 失败 %s: %s", path, e)
        return ""


def transcribe_audio_path(path: str) -> str:
    """对 wav 音频做 whisper 转写。仅在媒体可达时调用。"""
    try:
        from services.stt_service import WhisperSTT
        stt = WhisperSTT()
        return stt.transcribe_wav(path).strip()
    except Exception as e:
        logger.warning("ASR 失败 %s: %s", path, e)
        return ""


# --------------------------------------------------------------------------- #
# XML 解析：按 local_type 出标注
# --------------------------------------------------------------------------- #
def _xml_root(xml: str) -> Optional[ET.Element]:
    try:
        return ET.fromstring(xml)
    except Exception:
        # 微信 XML 常有未转义 &，容错：去掉 XML 声明后再试
        try:
            return ET.fromstring(xml.split("?>", 1)[-1])
        except Exception:
            return None


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _clean(s: str) -> str:
    s = s.replace("&#x20;", " ").replace("&#x0A;", "\n").replace("&amp;", "&")
    s = re.sub(r"\s+", " ", s).strip()
    return s


# 情感启发式（弱信号，仅供情感分析并入；confidence 低）
_POS_KW = ["哈哈", "哈哈哈", "喜欢", "爱你", "爱", "谢谢", "好呀", "好的", "嗯呢",
           "恭喜发财", "大吉大利", "红包", "转账", "晚安", "早安", "想你", "亲",
           "好滴", "ok", "OK", "哈哈哈", "甜", "笑"]
_NEG_KW = ["撤回", "忙线", "重症", "病房", "护具", "生气", "烦", "累", "痛",
           "难过", "哭", "吵", "分手", "别", "讨厌", "哼", "怎么啦", "？？", "？"]


def _emotion_hint(text: str, category: str) -> tuple[str, float, float]:
    """返回 (emotion_hint, emotion_score 0..1, confidence)。弱启发式。"""
    if not text:
        return ("", None, 0.2)
    pos = sum(1 for k in _POS_KW if k in text)
    neg = sum(1 for k in _NEG_KW if k in text)
    if pos == 0 and neg == 0:
        return ("中性", 0.5, 0.3)
    if pos > neg:
        return ("偏正向", 0.6 + min(0.2, 0.05 * pos), 0.4)
    if neg > pos:
        return ("偏负向/关切", 0.4 - min(0.2, 0.05 * neg), 0.4)
    return ("中性", 0.5, 0.3)


def parse_record(msg_type: str, xml: str) -> dict:
    """核心：按 local_type 把解压 XML 解析成一条 annotation 字段集。
    返回 dict（不含 id/chat_message_id，由写库层补）。"""
    out = {
        "media_type": "other",
        "parse_status": "failed",
        "label": "",
        "text_content": "",
        "category": "其他",
        "emotion_hint": "",
        "emotion_score": None,
        "confidence": 0.2,
        "source": "none",
        "media_path": None,
    }

    if msg_type == "47":
        # 自定义表情 <emoji md5=... width height>
        out["media_type"] = "emoji"
        out["category"] = "表情包"
        root = _xml_root(xml)
        md5 = ""
        if root is not None:
            em = root.find("emoji")
            if em is not None:
                md5 = em.get("md5", "")
        out["label"] = "自定义表情" + (f"({md5[:8]})" if md5 else "")
        out["text_content"] = ""  # 表情无文本
        # 媒体不可映射：拿到 md5 但拿不到表情图
        out["parse_status"] = "partial"
        out["source"] = "rule"
        out["confidence"] = 0.5
        return out

    if msg_type == "3":
        # 图片 <img md5=... cdnthumbwidth cdnthumbheight length>
        out["media_type"] = "image"
        out["category"] = "图片"
        root = _xml_root(xml)
        md5 = orig = ""
        w = h = ""
        if root is not None:
            img = root.find("img")
            if img is not None:
                md5 = img.get("md5", "")
                orig = img.get("originsourcemd5", "")
                w = img.get("cdnthumbwidth", "")
                h = img.get("cdnthumbheight", "")
        # 尝试媒体（恒 None）
        rec = resolve_media(md5, orig)
        if rec is not None:
            out["media_path"] = rec["path"]
            dec = decrypt_dat(open(rec["path"], "rb").read()) if rec["ext"] == ".dat" else None
            if dec:
                tmp = os.path.join(PROJECT_ROOT, "data", "_cur_img.png")
                open(tmp, "wb").write(dec)
                ocr = ocr_image_path(tmp)
                if ocr:
                    out["text_content"] = f"[图片文字] {ocr}"
                    out["source"] = "ocr"
                    out["parse_status"] = "parsed"
                    out["confidence"] = 0.7
                    return out
        out["label"] = f"图片{(' ' + w + 'x' + h) if w and h else ''}(媒体不可映射)"
        out["text_content"] = ""
        out["parse_status"] = "partial"
        out["source"] = "none"
        out["confidence"] = 0.4
        return out

    if msg_type == "34":
        # 语音 <voicemsg voiceformat length voicelength>
        out["media_type"] = "voice"
        out["category"] = "语音"
        root = _xml_root(xml)
        vlen = ""
        if root is not None:
            vm = root.find("voicemsg")
            if vm is not None:
                vlen = vm.get("voicelength", "")
        sec = (int(vlen) / 1000.0) if vlen.isdigit() else None
        rec = resolve_media()  # voicemsg XML 无 md5
        if rec is not None:
            txt = transcribe_audio_path(rec["path"])
            if txt:
                out["text_content"] = txt
                out["label"] = f"语音{(' %.1fs' % sec) if sec else ''}"
                out["parse_status"] = "parsed"
                out["source"] = "asr"
                out["confidence"] = 0.7
                out["media_path"] = rec["path"]
                return out
        out["label"] = f"语音{(' %.1fs' % sec) if sec else ''}"
        out["text_content"] = ("语音 %.1fs（媒体文件不可映射，未转写）" % sec) if sec else "语音（媒体不可映射，未转写）"
        out["parse_status"] = "failed"
        out["source"] = "none"
        out["confidence"] = 0.3
        return out

    if msg_type == "43":
        # 视频 <videomsg newmd5 playlength cdnthumbwidth cdnthumbheight>
        out["media_type"] = "video"
        out["category"] = "视频"
        root = _xml_root(xml)
        newmd5 = orig = ""
        play = ""
        if root is not None:
            v = root.find("videomsg")
            if v is not None:
                newmd5 = v.get("newmd5", "")
                orig = v.get("originsourcemd5", "")
                play = v.get("playlength", "")
        rec = resolve_media(newmd5, orig)
        if rec is not None:
            out["media_path"] = rec["path"]
            out["label"] = f"视频{(' ' + play + 's') if play else ''}(已定位)"
            out["parse_status"] = "partial"
            out["source"] = "video_frame"
            out["confidence"] = 0.6
            return out
        out["label"] = f"视频{(' ' + play + 's') if play else ''}(媒体不可映射)"
        out["text_content"] = f"视频 {play}s（媒体不可映射，未抽帧）" if play else "视频（媒体不可映射，未抽帧）"
        out["parse_status"] = "partial"
        out["source"] = "none"
        out["confidence"] = 0.4
        return out

    if msg_type == "50":
        # VoIP 通话气泡 <voipmsg><VoIPBubbleMsg><msg>对方忙线中</msg>...
        out["media_type"] = "other"
        out["category"] = "通话"
        root = _xml_root(xml)
        status = ""
        if root is not None:
            inner = root.find(".//VoIPBubbleMsg")
            if inner is not None:
                m = inner.find("msg")
                status = _text(m)
                dur = inner.find("duration")
                d = _text(dur)
                if d and d.isdigit() and int(d) > 0:
                    status = f"{status} 通话{int(d)}s" if status else f"通话{int(d)}s"
        out["label"] = "语音/视频通话"
        out["text_content"] = status or "通话记录"
        out["parse_status"] = "parsed"
        out["source"] = "rule"
        out["confidence"] = 0.8
        return out

    if msg_type == "48":
        # 位置 <location x y label poialias cityname poiPhone/>
        out["media_type"] = "other"
        out["category"] = "位置"
        root = _xml_root(xml)
        if root is not None:
            loc = root.find("location")
            if loc is not None:
                label = loc.get("label", "")
                poi = loc.get("poialias", "")
                city = loc.get("cityname", "")
                out["text_content"] = _clean(" / ".join(x for x in [poi, label, city] if x))
                out["label"] = poi or label or "位置"
        out["parse_status"] = "parsed"
        out["source"] = "rule"
        out["confidence"] = 0.85
        return out

    if msg_type == "10000":
        # 系统消息（明文）：撤回 / 添加好友 / 其他
        out["media_type"] = "other"
        out["category"] = "系统消息"
        txt = xml.strip()
        out["text_content"] = txt
        if "撤回" in txt:
            out["label"] = "消息撤回"
        elif "你已添加" in txt or "以上是打招呼" in txt:
            out["label"] = "添加好友/打招呼"
        else:
            out["label"] = "系统通知"
        out["parse_status"] = "parsed"
        out["source"] = "unicode"
        out["confidence"] = 0.9
        return out

    # 大数位掩码 local_type -> appmsg XML
    root = _xml_root(xml)
    appmsg = root.find("appmsg") if root is not None else None
    if appmsg is None:
        out["label"] = "未知消息"
        out["text_content"] = xml[:200]
        out["parse_status"] = "failed"
        out["source"] = "none"
        return out

    atype = _text(appmsg.find("type"))
    title = _clean(_text(appmsg.find("title")))
    des = _clean(_text(appmsg.find("des")))

    if atype == "57":
        # 引用回复：title=回复内容，refermsg=被引用消息
        out["category"] = "引用回复"
        ref = appmsg.find("refermsg")
        ref_txt = ""
        ref_name = ""
        if ref is not None:
            ref_txt = _clean(_text(ref.find("content")))
            ref_name = _text(ref.find("displayname"))
        parts = []
        if title:
            parts.append(title)
        if ref_txt:
            parts.append(f"[引用{ref_name+':' if ref_name else ''}{ref_txt}]")
        out["text_content"] = " ".join(parts)
        out["label"] = "引用回复"
        out["parse_status"] = "parsed"
        out["source"] = "rule"
        out["confidence"] = 0.8
        return out

    if atype == "2000":
        out["category"] = "转账"
        out["label"] = "微信转账"
        out["text_content"] = des or title
        out["parse_status"] = "parsed"
        out["source"] = "rule"
        out["confidence"] = 0.85
        return out

    if atype == "2001":
        out["category"] = "红包"
        out["label"] = "微信红包"
        out["text_content"] = des or title
        out["parse_status"] = "parsed"
        out["source"] = "rule"
        out["confidence"] = 0.85
        return out

    if atype == "76":
        out["category"] = "音乐分享"
        out["label"] = f"音乐《{title}》" + (f" - {des}" if des else "")
        # 取歌词前若干行
        lyric_el = appmsg.find("songlyric")
        lyric = _text(lyric_el)
        out["text_content"] = _clean(lyric)[:500] if lyric else (title + (" " + des if des else ""))
        out["parse_status"] = "parsed"
        out["source"] = "rule"
        out["confidence"] = 0.85
        return out

    if atype == "19":
        out["category"] = "聊天记录转发"
        out["label"] = title or "聊天记录"
        out["text_content"] = des[:800]
        out["parse_status"] = "parsed"
        out["source"] = "rule"
        out["confidence"] = 0.8
        return out

    if atype == "6":
        out["category"] = "文件"
        out["label"] = f"文件 {title}"
        out["text_content"] = title
        out["parse_status"] = "parsed"
        out["source"] = "rule"
        out["confidence"] = 0.8
        return out

    if atype == "5":
        out["category"] = "行程/位置卡片"
        out["label"] = title or "行程卡片"
        out["text_content"] = (title + (" " + des if des else "")).strip()
        out["parse_status"] = "parsed"
        out["source"] = "rule"
        out["confidence"] = 0.75
        return out

    if atype == "51":
        out["category"] = "链接卡片"
        out["label"] = title or "分享链接"
        out["text_content"] = (title + (" " + des if des else "")).strip()
        out["parse_status"] = "parsed"
        out["source"] = "rule"
        out["confidence"] = 0.7
        return out

    # 默认：其它 appmsg 分享
    out["category"] = "分享卡片"
    out["label"] = title or f"应用消息(type={atype})"
    out["text_content"] = (title + (" " + des if des else "")).strip()
    out["parse_status"] = "parsed" if out["text_content"] else "partial"
    out["source"] = "rule"
    out["confidence"] = 0.7
    return out


# --------------------------------------------------------------------------- #
# DB 写入
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS nontext_annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_message_id INTEGER,
    media_type TEXT,
    parse_status TEXT,
    label TEXT,
    text_content TEXT,
    category TEXT,
    emotion_hint TEXT,
    emotion_score REAL,
    confidence REAL,
    source TEXT,
    media_path TEXT,
    created_at TEXT
)
"""


def init_table(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA)
    conn.commit()


def upsert_annotation(conn: sqlite3.Connection, chat_message_id: int, fields: dict) -> None:
    """按 chat_message_id 幂等写入：先删后插。"""
    conn.execute("DELETE FROM nontext_annotations WHERE chat_message_id=?", (chat_message_id,))
    eh, esc, conf = _emotion_hint(fields.get("text_content", "") or "", fields.get("category", ""))
    conn.execute(
        """INSERT INTO nontext_annotations
           (chat_message_id, media_type, parse_status, label, text_content, category,
            emotion_hint, emotion_score, confidence, source, media_path, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            chat_message_id,
            fields.get("media_type"),
            fields.get("parse_status"),
            fields.get("label"),
            fields.get("text_content"),
            fields.get("category"),
            eh or fields.get("emotion_hint"),
            esc if esc is not None else fields.get("emotion_score"),
            fields.get("confidence"),
            fields.get("source"),
            fields.get("media_path"),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def iter_nontext_messages(conn: sqlite3.Connection):
    cur = conn.execute(
        """SELECT id, msg_type, content FROM chat_messages
           WHERE person_id=? AND msg_type!='1' ORDER BY id""",
        (PERSON_ID,),
    )
    return cur.fetchall()


def run(batch_log: Optional[str] = None, limit: Optional[int] = None) -> dict:
    """跑全量（或 limit 条）非文本消息。返回统计 dict。"""
    conn = sqlite3.connect(LOVAI_DB)
    init_table(conn)
    rows = iter_nontext_messages(conn)
    if limit:
        rows = rows[:limit]
    stats = {"total": len(rows), "parsed": 0, "partial": 0, "failed": 0,
             "by_type": {}, "errors": 0}
    for i, (cid, mtype, content) in enumerate(rows):
        try:
            xml = decompress_content(content)
            fields = parse_record(str(mtype), xml)
            upsert_annotation(conn, cid, fields)
            st = fields["parse_status"]
            stats[st] += 1
            bt = stats["by_type"].setdefault(str(mtype), {"total": 0, "parsed": 0, "partial": 0, "failed": 0})
            bt["total"] += 1
            bt[st] += 1
        except Exception as e:
            stats["errors"] += 1
            logger.warning("id=%s type=%s 处理失败: %s", cid, mtype, e)
        if (i + 1) % 100 == 0:
            conn.commit()
            logger.info("进度 %d/%d", i + 1, len(rows))
    conn.commit()
    conn.close()
    return stats


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    s = run(limit=lim)
    print(json.dumps(s, ensure_ascii=False, indent=2))
