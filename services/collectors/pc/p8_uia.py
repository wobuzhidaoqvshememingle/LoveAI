# -*- coding: utf-8 -*-
"""P8 PC UIA 控件树读取（uiautomation）。

免注入、只读控件树：枚举微信聊天窗口内的消息气泡控件，按气泡左右几何判方向。
注意：微信 4.1.x 控件树严重退化（消息区多为自绘，UIA 拿不到气泡），此时
collect 会抛 UiaDegradedError，由编排层自动降级到 P6 被动视觉 / P7 自动视觉。

只读取、不点击、不发送。当前 venv 默认未装 uiautomation，available() 会真实探测并给出
安装引导；测试用 monkeypatch + 伪造控件项验证「控件项→Message」解析，不驱动真微信。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..base import (SourceAdapter, Message, Availability, RISK_MEDIUM,
                    PLAT_PC, DIR_SENT, DIR_RECEIVED, ok, missing)
from .. import env

logger = logging.getLogger(__name__)

MOD = "uiautomation"
# 与 vision.py 一致的气泡几何阈值：右侧气泡=我(sent)，左侧=对方(received)
_SENT_RATIO = 0.62
_RECV_RATIO = 0.45


class UiaDegradedError(RuntimeError):
    """UIA 控件树退化/读不到消息控件：由编排降级到 P6/P7 视觉通道。"""


# ====================================================================== #
# 纯函数：控件项 → Message（可离线单测）
# ====================================================================== #
def parse_chat_controls(items: Sequence[Dict[str, Any]],
                        window_width: int,
                        source_id: str = "P8_uia",
                        peer_name: Optional[str] = None) -> List[Message]:
    """把伪造/真实的控件项列表解析成 Message。

    每个 item: {"text": str, "rect": (l, t, r, b), "ctrl_type": str=...}
    方向按气泡中心横坐标相对窗口宽度判定（微信气泡：我靠右、对方靠左）。
    时间分隔行/空文本跳过。
    """
    out: List[Message] = []
    if window_width <= 0:
        return out
    rows = []
    for it in items:
        text = (it.get("text") or "").strip()
        rect = it.get("rect")
        if not text or not rect or len(rect) != 4:
            continue
        l, _t, r, _b = rect
        cx = (l + r) / 2.0
        ratio = cx / float(window_width)
        direction = DIR_SENT if ratio >= _SENT_RATIO else (
            DIR_RECEIVED if ratio <= _RECV_RATIO else DIR_RECEIVED)
        rows.append({"dir": direction, "text": text, "top": _t, "bottom": _b})
    rows.sort(key=lambda d: d["top"])
    # 聚合同方向、垂直紧邻的多行（同一气泡折行）
    cur: Optional[Dict[str, Any]] = None
    for row in rows:
        h = max(1.0, row["bottom"] - row["top"])
        if cur and row["dir"] == cur["dir"] and row["top"] - cur["bottom"] <= h * 0.9:
            cur["text"] = cur["text"] + "\n" + row["text"]
            cur["bottom"] = row["bottom"]
        else:
            if cur:
                out.append(_make(cur, source_id, peer_name))
            cur = dict(row)
    if cur:
        out.append(_make(cur, source_id, peer_name))
    return out


def _make(cluster: Dict[str, Any], source_id: str,
          peer_name: Optional[str]) -> Message:
    return Message(
        direction=cluster["dir"],
        content=cluster["text"].strip(),
        timestamp=None,  # UIA 文本通常不含可靠时间，不臆造
        msg_type="text",
        source_id=source_id,
        peer_name=peer_name,
        speaker="我" if cluster["dir"] == DIR_SENT else (peer_name or "对方"),
    )


# ====================================================================== #
# 适配器
# ====================================================================== #
class P8UiaAdapter(SourceAdapter):
    id = "P8_uia"
    name = "PC UIA控件树读取(免注入只读)"
    platform = PLAT_PC
    requires = frozenset({"admin"})
    risk_level = RISK_MEDIUM
    default_enabled = True
    local_runnable = False
    success_prior = 0.45
    produces_plaintext = True

    def available(self) -> Availability:
        if not env.has_module(MOD):
            return missing(
                "未安装 uiautomation 模块",
                [f"pip install {MOD}",
                 "安装后以能读取桌面 UI 的权限运行（同用户会话即可，通常无需管理员）",
                 "若微信 4.1.x 控件树退化读不到气泡，将自动降级到 P6 被动视觉 / P7 自动视觉"])
        if not env.wechat_processes():
            return missing(
                "uiautomation 已装，但未检测到运行中的微信窗口",
                ["登录并打开微信、进入目标聊天窗口后再采集"])
        return ok("uiautomation 可用且检测到微信进程（注意 4.1.x 控件树可能退化）",
                  module=True, wechat_window=True)

    def collect(self, peer_name: Optional[str] = None,
                max_depth: int = 12, **opts) -> List[Message]:
        if not env.has_module(MOD):
            raise UiaDegradedError(
                "uiautomation 未安装，无法读取控件树；请 pip install uiautomation，"
                "或由编排降级到 P6 被动视觉")
        try:
            import uiautomation as ua  # type: ignore
        except Exception as e:
            raise UiaDegradedError(f"uiautomation 导入失败：{e}；降级到 P6/P7")

        try:
            win = ua.WindowControl(ClassName="WeChatMainWndForPC")
            if not win.Exists(1.0):
                raise UiaDegradedError("未找到微信主窗口(WeChatMainWndForPC)；降级到 P6/P7")
            items, win_w = self._dump_chat_items(ua, win, max_depth=max_depth)
        except UiaDegradedError:
            raise
        except Exception as e:
            raise UiaDegradedError(f"枚举微信控件树失败（4.1.x 常见退化）：{e}；降级到 P6/P7")

        if not items:
            raise UiaDegradedError(
                "控件树可读但消息区为空/自绘退化；请降级到 P6 被动视觉或 P7 自动视觉")
        return parse_chat_controls(items, win_w, source_id=self.id,
                                   peer_name=peer_name)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _dump_chat_items(ua, win, max_depth: int
                         ) -> Tuple[List[Dict[str, Any]], int]:
        """深度遍历窗口，收集疑似消息气泡的 Text/ListItem 控件。

        返回 (items, window_width)。退化时返回空列表，由 collect 抛降级异常。
        """
        items: List[Dict[str, Any]] = []
        try:
            rect = win.BoundingRectangle
            win_w = int(rect.width()) if rect else 0
        except Exception:
            win_w = 0
        # 真实遍历较脆（4.x 控件名多为空），这里做 best-effort：
        # 递归抓所有有 Name 的文本控件，交给纯函数按几何判方向
        def walk(ctrl, depth: int) -> None:
            if depth > max_depth:
                return
            try:
                name = getattr(ctrl, "Name", "") or ""
                ctype = type(ctrl).__name__
            except Exception:
                return
            if name and ctype in ("TextControl", "ListItemControl",
                                  "ButtonControl", "PaneControl"):
                try:
                    r = ctrl.BoundingRectangle
                    items.append({
                        "text": str(name),
                        "rect": (int(r.left), int(r.top), int(r.right), int(r.bottom)),
                        "ctrl_type": ctype,
                    })
                except Exception:
                    pass
            try:
                for child in ctrl.GetChildren():
                    walk(child, depth + 1)
            except Exception:
                return
        walk(win, 0)
        return items, win_w
