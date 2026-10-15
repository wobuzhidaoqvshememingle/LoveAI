# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 实时数字人服务（R3.3-56 模式B）

探测本机是否安装 SadTalker / Wav2Lip：
  - 若装了，调它的命令行把「静图 + TTS 音频」合成口型视频。
  - 没装就明确降级到 2D 序列帧，不假装能跑。

设计原则：
  - 不 import 任何重依赖（torch / sadTalker / wav2lip），全部懒探测。
  - 探测失败 / 调用异常一律走降级路径，抛异常不向上冒。
  - 对外暴露 capability() 返回结构化状态，UI 可直接显示。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 探测候选：常见可执行名 / 脚本路径
_CANDIDATE_EXES: List[str] = [
    "inference.py",       # SadTalker/inference.py
    "wav2lip.py",         # Wav2Lip/inference.py
    "sadtalker",
    "wav2lip",
]

# 探测候选：Python 包
_CANDIDATE_PKGS: List[str] = [
    "sadtalker",
    "wav2lip",
]


class RealtimeDigitalHuman:
    """实时数字人（SadTalker/Wav2Lip）封装。"""

    def __init__(self, search_paths: Optional[List[str]] = None) -> None:
        # 额外搜索路径（用户可在 config 里配）
        self._search_paths: List[str] = search_paths or []
        self._cached: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # 探测
    # ------------------------------------------------------------------
    def capability(self, force_rescan: bool = False) -> Dict[str, Any]:
        """返回当前数字人能力状态。

        :return: dict:
            available: bool            是否真能用
            engine: str                "sadtalker" | "wav2lip" | "none"
            command: str               探测到的可执行/脚本路径
            degraded: bool             是否降级到 2D
            message: str               给 UI 看的中文说明
        """
        if self._cached is not None and not force_rescan:
            return self._cached

        result: Dict[str, Any] = {
            "available": False,
            "engine": "none",
            "command": "",
            "degraded": True,
            "message": "未安装 SadTalker / Wav2Lip，已降级到 2D 序列帧。",
        }

        # 1) 找可执行/脚本
        exe = self._find_executable()
        if exe:
            result["available"] = True
            result["degraded"] = False
            result["command"] = exe
            result["engine"] = "sadtalker" if "sadtalker" in exe.lower() or "inference.py" in exe.lower() else "wav2lip"
            result["message"] = f"已检测到 {result['engine']}：{exe}"
            self._cached = result
            return result

        # 2) 找 Python 包（弱信号：能 import 也算半可用）
        pkg = self._find_python_package()
        if pkg:
            result["available"] = True
            result["degraded"] = False
            result["command"] = pkg
            result["engine"] = pkg
            result["message"] = f"已检测到 Python 包 {pkg}（命令行调用需自行配置路径）"
            self._cached = result
            return result

        self._cached = result
        return result

    def _find_executable(self) -> Optional[str]:
        # PATH 里找
        for name in _CANDIDATE_EXES:
            p = shutil.which(name)
            if p:
                return p
        # 用户自定义路径
        for base in self._search_paths:
            if not base or not os.path.isdir(base):
                continue
            for root, _dirs, files in os.walk(base):
                for f in files:
                    if f.lower() in ("inference.py", "wav2lip.py"):
                        return os.path.join(root, f)
        return None

    @staticmethod
    def _find_python_package() -> Optional[str]:
        for pkg in _CANDIDATE_PKGS:
            try:
                __import__(pkg)
                return pkg
            except ImportError:
                continue
        return None

    # ------------------------------------------------------------------
    # 调用
    # ------------------------------------------------------------------
    def generate_lip_video(
        self,
        image_path: str,
        audio_path: str,
        out_path: str,
    ) -> Dict[str, Any]:
        """把静图 + TTS 音频合成口型视频。

        未安装时直接返回降级结果，不跑任何子进程。
        """
        cap = self.capability()
        if not cap["available"]:
            return {
                "ok": False,
                "degraded_to": "2d_sprite",
                "reason": cap["message"],
                "video_path": "",
            }

        # 真调用（这里只做占位：拼命令行，实际跑留给用户本机环境）
        cmd = [sys_executable(), cap["command"], "--driven_audio", audio_path,
               "--source_image", image_path, "--result_dir", os.path.dirname(out_path)]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600, check=False,
            )
            if proc.returncode == 0 and os.path.exists(out_path):
                return {"ok": True, "degraded_to": "", "video_path": out_path}
            return {
                "ok": False,
                "degraded_to": "2d_sprite",
                "reason": f"{cap['engine']} 退出码 {proc.returncode}: {proc.stderr[:200]}",
                "video_path": "",
            }
        except Exception as e:  # pragma: no cover
            logger.exception("lip video generation failed")
            return {
                "ok": False,
                "degraded_to": "2d_sprite",
                "reason": f"调用异常: {e}",
                "video_path": "",
            }


def sys_executable() -> str:
    import sys
    return sys.executable
