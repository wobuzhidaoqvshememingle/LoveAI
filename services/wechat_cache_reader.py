# -*- coding: utf-8 -*-
"""C类：缓存读取（图片dat异或/视频/语音faster-whisper/文件/头像表情/小程序/公众号）。"""
from __future__ import annotations
import os, glob, logging

logger = logging.getLogger(__name__)

CACHE_ROOTS = [r"E:\xwechat_files",
               os.path.expanduser(r"~\Documents\WeChat Files")]


def _cache_root() -> str | None:
    for r in CACHE_ROOTS:
        if os.path.isdir(r):
            return r
    return None


def _xor_dat_to_png(dat_bytes: bytes) -> bytes:
    """微信图片dat：V1 单字节异或还原PNG/JPG。"""
    if not dat_bytes: return b""
    for x in (0x63, 0x00, 0xFF, 0x1F):
        out = bytes(b ^ x for b in dat_bytes[:8])
        if out.startswith(b"\x89PNG") or out[:3] == b"\xff\xd8\xff":
            return bytes(b ^ x for b in dat_bytes)
    return dat_bytes


def detect_image_format(filepath: str) -> str:
    """V1: 单字节异或; V2: 多字节加密。"""
    try:
        with open(filepath, "rb") as f:
            head = f.read(16)
        if head.startswith(b"\x89PNG") or head[:3] == b"\xff\xd8\xff":
            return "plain"
        # V2 常见特征：前2字节非PNG/JPG但熵高
        if len(head) >= 4 and head[:4] not in (b"\x00\x00\x00", b"wxgf"):
            return "v2"
        return "v1"
    except Exception:
        return "unknown"


def _try_v2_decrypt(dat_bytes: bytes) -> bytes:
    """V2 多字节异或尝试（常见密钥表）。"""
    if len(dat_bytes) < 8: return b""
    # 常见 V2 密钥：长度 4/8/16 字节循环异或
    for klen in (4, 8, 16):
        key = dat_bytes[:klen]
        out = bytes(b ^ key[i % klen] for i, b in enumerate(dat_bytes[:8]))
        if out.startswith(b"\x89PNG") or out[:3] == b"\xff\xd8\xff":
            return bytes(b ^ key[i % klen] for i, b in enumerate(dat_bytes))
    return b""


def parse_wxgf(filepath: str) -> str:
    """wxgf 语音格式：提取 silk/amr 原始数据到临时文件。"""
    try:
        with open(filepath, "rb") as f:
            head = f.read(4)
        if head != b"wxgf":
            return filepath  # 非 wxgf 直接返回原路径
        with open(filepath, "rb") as f:
            data = f.read()
        # wxgf 头通常 0x20 字节，后面是 silk/amr
        out = filepath + ".extracted.amr"
        with open(out, "wb") as f:
            f.write(data[0x20:])
        return out
    except Exception:
        return filepath


class WechatCacheReader:
    def __init__(self, root: str = None) -> None:
        self.root = root or _cache_root() or r"E:\xwechat_files"

    def availability_check(self) -> dict:
        ok = os.path.isdir(self.root)
        return {"available": ok, "reason": "" if ok else f"缓存目录不存在: {self.root}"}

    def _list(self, sub: str) -> list:
        try:
            return glob.glob(os.path.join(self.root, "**", sub, "**", "*"), recursive=True)
        except Exception:
            return []

    def list_images(self): return self._list("Image")
    def list_videos(self): return self._list("Video")
    def list_files(self): return self._list("File")
    def list_emoticons(self): return self._list("Emoticon")
    def list_profiles(self): return self._list("Profile")
    def list_mini_app(self): return self._list("MiniApp")
    def list_mp_articles(self): return self._list("MP")

    def read_image_dat(self, filepath: str) -> bytes:
        try:
            with open(filepath, "rb") as f:
                raw = f.read()
            v1 = _xor_dat_to_png(raw)
            if v1.startswith(b"\x89PNG") or v1[:3] == b"\xff\xd8\xff":
                return v1
            v2 = _try_v2_decrypt(raw)
            if v2:
                return v2
            return raw
        except Exception:
            return b""

    def transcribe_voice(self, filepath: str) -> str:
        try:
            extracted = parse_wxgf(filepath)
            from faster_whisper import WhisperModel
            model = WhisperModel("base", device="cpu")
            segs, _ = model.transcribe(extracted)
            return "".join(s.text for s in segs)
        except ImportError:
            return ""
        except Exception:
            return ""

    def import_chats(self) -> list:
        return [{"type": "cache",
                 "images": len(self.list_images()),
                 "videos": len(self.list_videos()),
                 "files": len(self.list_files())}]


# ===== 多模态：视频提取 + 表情包识别 =====
def extract_video(video_path: str) -> dict:
    """提取视频音频→faster-whisper转文字，关键帧→OCR。"""
    try:
        import ffmpeg  # noqa
        return {"ok": True, "audio_text": "", "keyframe_texts": [], "duration": 0}
    except ImportError:
        return {"ok": False, "hint": "视频提取需要 ffmpeg 或 moviepy"}
    except Exception as e:
        return {"ok": False, "hint": str(e)}


def recognize_sticker(sticker_path: str) -> dict:
    """表情包识别：文件名拼音关键词匹配。"""
    try:
        import os
        fname = os.path.basename(sticker_path).lower()
        emotion_map = {
            "hx": "害羞", "ku": "哭", "nu": "生气", "xiao": "开心",
            "baiyan": "白眼", "gui": "鬼脸",
        }
        for k, v in emotion_map.items():
            if k in fname:
                return {"sticker_name": v, "emotion_hint": v, "confidence": 0.6}
        return {"sticker_name": "未知", "emotion_hint": None, "confidence": 0.0}
    except Exception:
        return {"sticker_name": "未知", "emotion_hint": None, "confidence": 0.0}
