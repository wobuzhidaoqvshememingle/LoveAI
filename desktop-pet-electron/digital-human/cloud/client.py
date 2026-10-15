#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
digital-human/cloud/client.py — 云端数字人兜底客户端（豆包 Seedance 产物轨）

【v2 变更，2026-10-11】移除 D-ID API 集成，改为「豆包大模型直接生成产物」接入：
  - 云端轨不再调用任何第三方付费 API（D-ID 上传/创建 talk/轮询/Basic 鉴权全部删除）。
  - 生成动作由豆包大模型（doubao-creative-video skill，General「照片动起来」图生视频，
    seedance 2.0 fast，5 秒）直接完成，产物为一个 mp4 文件。
  - 本客户端只负责「产物接入校验」：确认产物存在、非空、格式正确，供 Electron 桌宠播放。

设计原则（fail-closed 铁律，写进代码逻辑）：
  - config.json 里 enabled=false → 任何调用直接返回 {"ok":false,...}，绝不读取产物之外的内容
  - 产物缺失 / 空文件 / 非 mp4 → 结构化错误码，绝不尝试播放或伪造产物
  - 不使用任何网络请求；不携带任何密钥；不写入 stdout 日志之外的东西
  - 只用 Python 标准库（Electron 侧用项目 venv 直接跑，那个环境没有 requests）

用法（CLI）：
  python client.py verify --video <path> [--expect-frames N]
  → stdout 最后一行必为 JSON：{"ok":true,"video_path":"...","bytes":N} 或 {"ok":false,"code":"...","reason":"..."}

用法（import）：
  from client import CloudDigitalHuman
  c = CloudDigitalHuman()
  result = c.verify(video_path)
  # result 即上面那个 dict
"""

import argparse
import json
import os
import struct
import sys

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, "config.json")

# 最小可接受产物：1KB（低于此值视为空/损坏产物）
MIN_VIDEO_BYTES = 1024

# 错误码枚举（与 README_API.md 失败模式表一一对应）
ERR_DISABLED = "disabled"          # config.enabled != true
ERR_NO_SOURCE = "no_source"        # 产物路径未配置或不存在
ERR_BAD_VIDEO = "bad_video"        # 文件空 / 太小 / 非 mp4（无 ftyp box）
ERR_VERIFY = "verify_error"        # 校验过程本身异常（IO 错误等）


def _fail(code, reason):
    """构造一个 fail-closed 返回 dict。"""
    return {"ok": False, "code": code, "reason": str(reason)[:500]}


def _load_config():
    """读取 config.json。文件不存在 / JSON 坏 → 返回默认（enabled=false）。"""
    default = {
        "enabled": False,
        "mode": "seedance_product",
        "product_path": "",
        "generated_by": "",
        "note": "",
    }
    try:
        # utf-8-sig 兼容记事本/PowerShell 写出的 BOM
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            return default
        for k, v in default.items():
            cfg.setdefault(k, v)
        return cfg
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError):
        return default


# ---------------------------------------------------------------------------
# mp4 结构头校验（纯标准库，不依赖 ffmpeg/cv2）
# ---------------------------------------------------------------------------

def _is_mp4(path):
    """
    用 ISO BMFF box 头判断是否为 mp4 家族（moov/mdat 或 ftyp 起始）。
    读取文件前 32 字节，检查是否以 4 字节大小 + 'ftyp' 开头（主流 mp4）。
    备注：部分 mp4 首个 box 是 'moov' 或 'mdat'，ftyp 非绝对；这里同时接受
    ftyp / moov / mdat / free 等起始 box，但必须能解析出至少一个 box 头。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return False
    if len(head) < 8:
        return False
    size, box_type = struct.unpack(">I4s", head[:8])
    if size < 8 or size > (64 * 1024 * 1024):
        # 大小不合理 → 不是合法 box
        return False
    return box_type in (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide")


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class CloudDigitalHuman:
    """云端数字人客户端（当前 provider=seedance_product，豆包大模型直接生成产物）。

    典型用法：
        c = CloudDigitalHuman()
        r = c.verify("dh_talk.mp4")
        if r["ok"]:
            print(r["video_path"])
    """

    def __init__(self, config_path=None):
        global CONFIG_PATH
        if config_path:
            CONFIG_PATH = config_path
        self.cfg = _load_config()

    # ---- 前置门禁（fail-closed 核心）----
    def _guard_enabled(self):
        """返回 None 表示通过；否则返回 fail dict。"""
        if self.cfg.get("enabled") is not True:
            return _fail(ERR_DISABLED,
                         "cloud digital-human disabled in config.json (enabled != true)")
        return None

    def _resolve_product_path(self, video_path=None):
        """确定产物路径：优先 CLI 传入，其次 config.product_path。"""
        p = video_path or self.cfg.get("product_path") or ""
        if not p:
            return None
        if not os.path.isabs(p):
            # 相对路径相对 cloud 目录上一级（desktop-pet-electron/digital-human）？
            # 契约：config.product_path 相对 LoveAI-v2 项目根；这里统一按项目根解析。
            return os.path.normpath(os.path.join(_HERE, "..", "..", "..", "..", p))
        return p

    # ---- 产物校验 ----
    def verify(self, video_path=None):
        """主入口。返回 {"ok":bool, ...} dict。"""
        # 1) 门禁：未启用 → 直接 fail-closed
        guard = self._guard_enabled()
        if guard is not None:
            return guard

        # 2) 产物路径解析
        p = self._resolve_product_path(video_path)
        if not p:
            return _fail(ERR_NO_SOURCE,
                         "no product path configured; set config.json product_path or pass --video")

        # 3) 存在性 / 非空 / 大小
        if not os.path.isfile(p):
            return _fail(ERR_NO_SOURCE, "product not found: %s" % p)
        size = os.path.getsize(p)
        if size < MIN_VIDEO_BYTES:
            return _fail(ERR_BAD_VIDEO,
                         "product too small (%d bytes < %d); treat as broken product" % (size, MIN_VIDEO_BYTES))

        # 4) mp4 结构头校验
        if not _is_mp4(p):
            return _fail(ERR_BAD_VIDEO,
                         "product is not a valid mp4 (no ISO BMFF box header): %s" % p)

        return {"ok": True, "video_path": p, "bytes": size}


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _main(argv=None):
    parser = argparse.ArgumentParser(
        description="Cloud digital-human fallback client (Seedance product track)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("verify", help="verify a Seedance-generated talking-video product (mp4)")
    g.add_argument("--video", default=None, help="path to product mp4 (default: config.product_path)")

    args = parser.parse_args(argv)

    if args.cmd == "verify":
        client = CloudDigitalHuman()
        result = client.verify(args.video)
        # stdout 最后一行必须是一行 JSON（Electron 集成方读这行）
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0 if result.get("ok") else 1

    return 2


if __name__ == "__main__":
    sys.exit(_main())
