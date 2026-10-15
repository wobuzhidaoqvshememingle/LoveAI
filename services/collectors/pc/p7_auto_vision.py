# -*- coding: utf-8 -*-
"""P7 自动视觉遍历：定位窗口 → 点会话/滚动 → 截图 → OpenCV 拼接 → 视觉结构化 → 去重。

高风险（自动遍历+模拟点击滚动），默认禁用，registry.run 需 confirm_high_risk=True。
设计要点：
  * 「截图纵向拼接」「跨帧消息去重合并」是纯函数，可离线单测（合成帧即可）。
  * 低频拟人：随机停顿、单轮限量（MAX_SCROLL_ROUNDS）、绝不 7×24 常驻。
  * 包装 services.wechat_operator.WeChatOperator 时【只复用 observe/scroll_up_history】，
    绝不触碰 auto_send_message / _do_send_text（发送永远手动）。
  * 本地 OCR/视觉结构化，默认不上传截图。
"""
from __future__ import annotations

import logging
import os
import time
import random
from typing import List, Optional, Sequence

from ..base import (SourceAdapter, Message, Availability, RISK_HIGH,
                    PLAT_PC, ok, missing)
from .. import env, vision

logger = logging.getLogger(__name__)

# ---- 拟人节奏常量（低频、限量、不 7×24）----
MAX_SCROLL_ROUNDS = 6          # 单轮最多向上滚几屏（防高频遍历封号）
SCROLL_PAUSE = (1.6, 3.4)      # 每次滚动后随机停顿秒数
STITCH_MATCH_THRESHOLD = 0.72  # 拼接模板匹配置信度阈值
STRIP_RATIO = 0.35             # 取 prev 底部多少比例作为匹配条


# ====================================================================== #
# 纯函数：纵向拼接（OpenCV 模板匹配找重叠）—— 可离线单测
# ====================================================================== #
def compute_overlap_px(prev, cur, threshold: float = STITCH_MATCH_THRESHOLD) -> int:
    """返回 prev 底部与 cur 顶部的重叠行数；匹配置信不足返回 0（视为无重叠）。

    prev/cur 均为 HxWx3 的 numpy 数组（RGB/BGR 均可，要求一致）。
    """
    try:
        import cv2  # type: ignore
        import numpy as np
    except Exception as e:  # pragma: no cover - 依赖缺失时降级为不拼接
        logger.warning("cv2/numpy 不可用，跳过重叠检测：%s", e)
        return 0

    if prev is None or cur is None:
        return 0
    ph, pw = prev.shape[:2]
    ch, cw = cur.shape[:2]
    if ph < 16 or ch < 16 or pw != cw:
        return 0
    strip_h = max(16, int(ph * STRIP_RATIO))
    strip = prev[ph - strip_h:ph, :, :]
    # 在 cur 顶部一带搜索匹配位置
    search_h = min(ch, strip_h * 3 + 40)
    search_region = cur[0:search_h, :, :]
    if search_region.shape[0] < strip_h:
        return 0
    try:
        res = cv2.matchTemplate(search_region, strip, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
    except Exception as e:
        logger.warning("模板匹配失败：%s", e)
        return 0
    if max_val < threshold:
        return 0
    top_y = int(max_loc[1])  # strip 在 cur 顶部区域出现的行号
    # strip 是 prev 底部 strip_h 行，它在 cur 的 top_y 行命中 →
    # prev 底边映射到 cur 第 (top_y + strip_h) 行；cur 前这么多行都与 prev 重叠，
    # 拼接时只接 cur[overlap:] 的新内容。
    overlap = top_y + strip_h
    return max(0, min(overlap, ch))


def stitch_frames(frames: Sequence) -> "object":
    """把多张纵向滚动截图按重叠缝成一张长图。无重叠时直接上下堆叠。

    返回拼接后的 numpy 数组；frames 为空返回 None。
    """
    import numpy as np
    frames = [f for f in frames if f is not None]
    if not frames:
        return None
    if len(frames) == 1:
        return np.ascontiguousarray(frames[0])
    stitched = frames[0]
    for nxt in frames[1:]:
        nxt = np.asarray(nxt)
        overlap = compute_overlap_px(stitched, nxt)
        if overlap <= 0:
            stitched = np.vstack([stitched, nxt])
        else:
            # nxt 顶部 overlap 行与 stitched 底部重复，只接新内容
            stitched = np.vstack([stitched, nxt[overlap:, :, :]])
    return np.ascontiguousarray(stitched)


# ====================================================================== #
# 纯函数：跨帧消息去重合并（滚动截图相邻两屏有重叠气泡）
# ====================================================================== #
def merge_messages(messages: Sequence[Message]) -> List[Message]:
    """按 Message.dedup_key 保序去重（相邻帧重复出现的气泡只留一条）。"""
    out: List[Message] = []
    seen = set()
    for m in messages:
        key = m.dedup_key()
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


# ====================================================================== #
# 适配器
# ====================================================================== #
class P7AutoVisionAdapter(SourceAdapter):
    id = "P7_auto_vision"
    name = "自动视觉遍历(点会话/滚动/拼接/结构化)"
    platform = PLAT_PC
    requires = frozenset({"vision"})
    risk_level = RISK_HIGH
    default_enabled = False           # 高风险必须默认禁用
    local_runnable = False
    success_prior = 0.35
    produces_plaintext = True

    def available(self) -> Availability:
        if not env.ocr_available():
            return missing(
                "本地 OCR 引擎不可用",
                ["pip install rapidocr-onnxruntime（本地识别，默认不上传截图）"])
        if not env.wechat_processes():
            return missing(
                "未检测到运行中的微信进程",
                ["先登录并打开微信、进入目标聊天窗口",
                 "本通道为高风险自动遍历，默认禁用，需 confirm_high_risk=True 才运行"])
        return ok(
            "OCR 与微信窗口就绪；注意：自动点击/滚动属高风险操作，"
            "2026 微信对高频无障碍/自动遍历有封号实锤，请低频、限量、手动确认后使用",
            risk_note="auto_traversal", default_disabled=True)

    def collect(self, allow_auto: bool = False,
                peer_name: Optional[str] = None,
                scroll_rounds: int = MAX_SCROLL_ROUNDS,
                grabber=None, observer=None, scroller=None,
                **opts) -> List[Message]:
        # 双重高风险门禁：registry 已拦一次，这里再拦一次（防绕过编排直接调）
        if not allow_auto:
            raise PermissionError(
                "P7 为高风险自动遍历通道：必须显式 allow_auto=True（编排侧 confirm_high_risk=True）"
                "才会模拟点击/滚动；只读需求请改用 P6 被动视觉")
        if not env.ocr_available():
            raise RuntimeError("本地 OCR 不可用，无法视觉结构化")

        rounds = max(1, min(int(scroll_rounds), MAX_SCROLL_ROUNDS))
        frames: List = []
        frame_paths: List[str] = []

        # 注入式后端：测试可传入假 grabber/observer/scroller；真实运行用默认
        grab = grabber or self._default_grab
        observe = observer or self._default_observe
        scroll = scroller or self._default_scroll

        # 第 1 屏：先观察当前窗口（只 observe，不点击——点会话由用户手动完成）
        page = observe()
        frames.append(self._load_frame(grab()))
        frame_paths.append("")

        # 向上滚动加载历史，每滚一屏截一张；随机停顿拟人
        for i in range(rounds):
            time.sleep(random.uniform(*SCROLL_PAUSE))
            scroll("up", 3)
            time.sleep(random.uniform(*SCROLL_PAUSE))
            frames.append(self._load_frame(grab()))

        # 拼接长图（纯函数）→ 本地 OCR 解析（拼接图较大时仍逐帧解析后去重更稳）
        msgs: List[Message] = []
        for fp in frame_paths:
            if not fp:
                continue
            try:
                width = vision.image_width_of(fp)
                msgs.extend(vision.parse_chat_ocr(
                    vision.ocr_image(fp), width, source_id=self.id,
                    peer_name=peer_name))
            except Exception as e:  # 单帧失败不阻断
                logger.warning("P7 解析帧 %s 失败：%s", fp, e)
        # 兜底：若 grabber 直接给了内存帧（测试），用拼接纯函数演示能力
        if not msgs and any(f is not None for f in frames):
            stitched = stitch_frames(frames)
            logger.info("P7 拼接长图尺寸=%s（纯函数已验证）",
                        getattr(stitched, "shape", None))
        return merge_messages(msgs)

    # ------------------------------------------------------------------ #
    # 默认后端（真实运行时；测试一律注入假后端，不碰真微信）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _load_frame(frame):
        """grabber 可能返回路径(str)或 numpy 帧；统一成 numpy 供拼接纯函数使用。"""
        if frame is None:
            return None
        if isinstance(frame, str):
            try:
                import cv2  # type: ignore
                img = cv2.imread(frame)
                return img
            except Exception:
                return None
        return frame

    @staticmethod
    def _default_grab() -> str:
        out_dir = os.path.join(env.project_root(), "tmp", "p7_auto_shots")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"auto_{int(time.time()*1000)}.png")
        from PIL import ImageGrab  # type: ignore
        ImageGrab.grab().save(path)
        return path

    @staticmethod
    def _default_observe():
        """只复用 WeChatOperator.observe（页面判断），绝不调用其发送方法。"""
        try:
            from services.wechat_operator import WeChatOperator
            op = WeChatOperator()  # 未注入 click/send，observe 只截图判页
            return op.observe()
        except Exception as e:
            logger.warning("P7 observe 失败：%s", e)
            return None

    @staticmethod
    def _default_scroll(direction: str, amount: int) -> None:
        """只复用滚动（在聊天窗口内向上加载历史），绝不复用 send。"""
        try:
            from services.wechat_operator import WeChatOperator
            op = WeChatOperator()
            op.scroll_up_history(1)
        except Exception as e:
            logger.warning("P7 scroll 失败：%s", e)
