# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - AI 立绘生成器（R3.3-50）

按优先级降级：
  1. 本地 Stable Diffusion / ComfyUI（探测 localhost:7860 /sdapi/v1/ 可用则调用）
  2. PIL 程序化生成占位头像（圆头+发型+眼睛，根据 character_desc 选配色）
  3. 内置预设角色图路径（温柔学姐/元气少女/高冷御姐/邻家妹妹）

不调用任何云端生图 API（无 key）。

"不撞脸"机制：
  - 用感知哈希（简化版：缩到 8x8 灰度 -> 平均哈希）得到 64bit hash
  - dedupe_portrait(new_hash, existing_hashes) -> True 表示「不相似，可用」
  - 相似时调用方应换 seed 重新生成。
"""
from __future__ import annotations

import hashlib
import logging
import os
import struct
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw  # type: ignore
    _PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    _PIL_AVAILABLE = False

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_OUT_DIR = os.path.join(_PROJECT_ROOT, "assets", "portraits", "generated")

# ComfyUI / SD WebUI 默认端口
_SD_API_URL = "http://127.0.0.1:7860"
_SD_TIMEOUT_S = 1.5

# 内置预设角色（名字 -> 配色）
PRESET_CHARACTERS: Dict[str, Dict[str, Tuple[int, int, int]]] = {
    "温柔学姐": {
        "hair": (210, 180, 140),
        "skin": (255, 220, 190),
        "eye": (120, 90, 60),
    },
    "元气少女": {
        "hair": (230, 90, 90),
        "skin": (255, 220, 190),
        "eye": (180, 60, 60),
    },
    "高冷御姐": {
        "hair": (40, 40, 60),
        "skin": (245, 220, 200),
        "eye": (60, 60, 90),
    },
    "邻家妹妹": {
        "hair": (160, 110, 70),
        "skin": (255, 225, 200),
        "eye": (100, 70, 50),
    },
}


# ----------------------------------------------------------------------
# 感知哈希
# ----------------------------------------------------------------------
def _img_to_hash(img: "Image.Image") -> str:
    """8x8 平均哈希，返回 16 位 hex 字符串（64bit）。"""
    small = img.convert("L").resize((8, 8))
    pixels = list(small.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for i, p in enumerate(pixels):
        if p >= avg:
            bits |= (1 << i)
    return f"{bits:016x}"


def _hamming(h1: str, h2: str) -> int:
    """两个 16 位 hex hash 的汉明距离（bit 差异数）。"""
    if len(h1) != len(h2):
        return 64
    v = int(h1, 16) ^ int(h2, 16)
    return bin(v).count("1")


def dedupe_portrait(new_hash: str, existing_hashes: List[str], threshold: int = 10) -> bool:
    """判断新生成的脸是否「不撞脸」。

    :param new_hash: 新图感知哈希
    :param existing_hashes: 已有图哈希列表
    :param threshold: 汉明距离 <= threshold 视为相似（默认 10/64）
    :return: True 表示不相似，可以采用；False 表示撞脸，应换 seed 重来
    """
    for h in existing_hashes:
        if _hamming(new_hash, h) <= threshold:
            return False
    return True


# ----------------------------------------------------------------------
# 生成器
# ----------------------------------------------------------------------
class PortraitGenerator:
    """立绘生成器：本地 SD -> PIL 程序化 -> 内置预设。"""

    def __init__(self, out_dir: Optional[str] = None, sd_api_url: str = _SD_API_URL) -> None:
        self.out_dir = out_dir or _DEFAULT_OUT_DIR
        self.sd_api_url = sd_api_url.rstrip("/")
        os.makedirs(self.out_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 主接口
    # ------------------------------------------------------------------
    def generate_portrait(self, character_desc: str, style: str = "chibi") -> str:
        """生成一张立绘，返回图片绝对路径。

        :param character_desc: 角色描述（如 "温柔学姐" 或自由文本）
        :param style: 风格标签（chibi/realistic/anime）
        :return: 图片文件绝对路径
        """
        # 1. 尝试本地 SD
        try:
            p = self._try_local_sd(character_desc, style)
            if p:
                logger.info("portrait from local SD: %s", p)
                return p
        except Exception as e:  # pragma: no cover
            logger.debug("local SD unavailable: %s", e)

        # 2. PIL 程序化占位
        try:
            p = self._pil_procedural(character_desc, style)
            if p:
                logger.info("portrait from PIL procedural: %s", p)
                return p
        except Exception as e:  # pragma: no cover
            logger.warning("PIL procedural failed: %s", e)

        # 3. 内置预设
        p = self._preset_path(character_desc)
        logger.info("portrait fallback to preset: %s", p)
        return p

    # ------------------------------------------------------------------
    # 降级 1：本地 SD / ComfyUI
    # ------------------------------------------------------------------
    def _sd_alive(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.sd_api_url}/sdapi/v1/options", method="GET")
            with urllib.request.urlopen(req, timeout=_SD_TIMEOUT_S) as resp:
                return resp.status == 200
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def _try_local_sd(self, desc: str, style: str) -> Optional[str]:
        """R3.3-50: 真发 POST /sdapi/v1/txt2img；返回保存的 PNG 路径。

        探测 localhost:7860 不通 -> return None（降级到 PIL 程序化）。
        通了 -> 真发请求，拿 base64 图，解码保存到 out_dir。
        任何异常（网络/JSON/base64）都吞掉并 return None，绝不抛栈。
        """
        if not self._sd_alive():
            return None
        try:
            import base64 as _b64
            import json as _json
            prompt = f"{desc}, {style}, portrait, chibi, high quality, clean background"
            payload = {
                "prompt": prompt,
                "steps": 20,
                "width": 512,
                "height": 512,
                "batch_size": 1,
                "n_iter": 1,
            }
            req = urllib.request.Request(
                f"{self.sd_api_url}/sdapi/v1/txt2img",
                data=_json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                body = _json.loads(resp.read().decode("utf-8", "replace"))
            images = body.get("images") or []
            if not images:
                logger.warning("SD txt2img 返回空 images")
                return None
            b64 = images[0]
            # SD webui 有时返回 data:image/png;base64,xxx
            if "," in b64 and b64.strip().startswith("data:"):
                b64 = b64.split(",", 1)[1]
            raw = _b64.b64decode(b64)
            slug = hashlib.md5(
                f"sd|{desc}|{style}|{time.time()}".encode("utf-8")).hexdigest()[:10]
            out = os.path.join(self.out_dir, f"sd_{slug}.png")
            with open(out, "wb") as f:
                f.write(raw)
            logger.info("SD 真生成立绘: %s (%d bytes)", out, len(raw))
            return out
        except Exception as e:
            logger.warning("SD txt2img 调用失败（降级 PIL）: %s", e)
            return None

    # ------------------------------------------------------------------
    # 降级 2：PIL 程序化头像
    # ------------------------------------------------------------------
    def _pick_palette(self, character_desc: str) -> Dict[str, Tuple[int, int, int]]:
        """根据 character_desc 关键词选配色，命中不到用默认。"""
        for name, pal in PRESET_CHARACTERS.items():
            if name in character_desc:
                return pal
        # 自由文本：用描述 hash 选一个稳定配色
        h = int(hashlib.md5(character_desc.encode("utf-8")).hexdigest(), 16)
        keys = list(PRESET_CHARACTERS.keys())
        return PRESET_CHARACTERS[keys[h % len(keys)]]

    def _pil_procedural(self, character_desc: str, style: str) -> Optional[str]:
        if not _PIL_AVAILABLE:
            return None
        pal = self._pick_palette(character_desc)
        size = 256
        img = Image.new("RGBA", (size, size), (30, 30, 46, 255))
        d = ImageDraw.Draw(img)
        cx, cy = size // 2, size // 2 + 10
        # 头发
        d.ellipse([cx - 90, cy - 110, cx + 90, cy + 50], fill=pal["hair"])
        # 脸
        d.ellipse([cx - 70, cy - 80, cx + 70, cy + 70], fill=pal["skin"])
        # 眼睛
        for ex in (cx - 28, cx + 28):
            d.ellipse([ex - 8, cy - 20, ex + 8, cy - 4], fill=(255, 255, 255, 255))
            d.ellipse([ex - 5, cy - 17, ex + 5, cy - 7], fill=pal["eye"])
        # 嘴
        d.arc([cx - 14, cy + 20, cx + 14, cy + 40], start=15, end=165, fill=(180, 80, 80, 255), width=3)
        # 保存
        slug = hashlib.md5(f"{character_desc}|{style}|{time.time()}".encode("utf-8")).hexdigest()[:10]
        out = os.path.join(self.out_dir, f"portrait_{slug}.png")
        img.save(out)
        return out

    # ------------------------------------------------------------------
    # 降级 3：内置预设
    # ------------------------------------------------------------------
    def _preset_path(self, character_desc: str) -> str:
        """返回一张内置预设图路径（若 PIL 可用则现画一张 1x1 兜底图）。"""
        for name in PRESET_CHARACTERS:
            if name in character_desc:
                # 复用 PIL 画一张（不缓存也没关系，快）
                if _PIL_AVAILABLE:
                    p = self._pil_procedural(name, "preset")
                    if p:
                        return p
                break
        # 最终兜底：写一个纯色 PNG
        fallback = os.path.join(self.out_dir, "preset_default.png")
        if _PIL_AVAILABLE and not os.path.exists(fallback):
            Image.new("RGB", (64, 64), (120, 120, 160)).save(fallback)
        elif not os.path.exists(fallback):
            # 极端情况：PIL 都没有，写个空文件
            with open(fallback, "wb") as f:
                f.write(b"")
        return fallback

    # ------------------------------------------------------------------
    # 工具：算 hash（对外暴露，方便测试）
    # ------------------------------------------------------------------
    def hash_of(self, image_path: str) -> Optional[str]:
        if not _PIL_AVAILABLE or not os.path.exists(image_path):
            return None
        try:
            with Image.open(image_path) as im:
                return _img_to_hash(im)
        except Exception:
            return None
