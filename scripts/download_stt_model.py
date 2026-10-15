# -*- coding: utf-8 -*-
"""预下载本地语音识别（STT）模型权重 —— 离线/内网分发场景使用。

背景：faster-whisper 的权重不在 pip 包里，首次调用 WhisperSTT 才会从
HuggingFace 拉到 ~/.cache/huggingface/hub。装机用户若处于无网环境，
语音识别会持续报"模型权重未下载"（stt_service.status() 可查）。

用法（在项目根，用项目 venv）：
    .venv\\Scripts\\python.exe scripts\\download_stt_model.py
    .venv\\Scripts\\python.exe scripts\\download_stt_model.py --size small
    .venv\\Scripts\\python.exe scripts\\download_stt_model.py --dir D:\\models\\whisper

size 可选 tiny/base/small/medium（默认 base，与 stt_service 默认一致）。
下载后会做一次 1 秒静音转写自检，确认真的能跑，而不是只把文件拉下来。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    ap = argparse.ArgumentParser(description="预下载 faster-whisper 模型权重并自检")
    ap.add_argument("--size", default=os.environ.get("LOVEAI_STT_MODEL", "base"),
                    choices=["tiny", "base", "small", "medium"],
                    help="模型规格，默认 base（约150MB）")
    ap.add_argument("--dir", default=os.environ.get("LOVEAI_STT_MODEL_DIR"),
                    help="下载缓存目录，默认 HuggingFace 标准缓存位置")
    args = ap.parse_args()

    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        print(f"[FAIL] faster-whisper 未安装：{e}")
        print("       请先 pip install -r requirements.txt")
        return 2

    if args.dir:
        os.environ.setdefault("HF_HOME", args.dir)
        print(f"[i] 使用缓存目录: {args.dir}")

    print(f"[i] 开始下载/加载 Systran/faster-whisper-{args.size} …（首次需联网）")
    try:
        model = WhisperModel(args.size, device="cpu", compute_type="int8")
    except Exception as e:
        print(f"[FAIL] 加载失败：{e}")
        print("       离线环境请改在有网机器上执行本脚本，再拷贝 HF 缓存目录。")
        return 1

    # 自检：喂 1 秒静音，确认真能出结果（结果应为空串，但流程必须通）
    try:
        import numpy as np
        silence = np.zeros(16000, dtype=np.float32)
        segments, info = model.transcribe(silence, language="zh", beam_size=1)
        _ = list(segments)
        print(f"[OK] whisper-{args.size} 已就绪，可离线使用"
              f"（检测语言={getattr(info, 'language', 'zh')}）")
        print("[OK] 现在 stt_service.status() 应返回 available=True")
        return 0
    except Exception as e:
        print(f"[FAIL] 权重已下载但自检失败：{e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
