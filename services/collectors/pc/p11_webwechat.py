# -*- coding: utf-8 -*-
"""P11 网页版微信（web.wechat / wx.qq.com）浏览器读取。

现实约束：微信自 2017 起逐步关停多数个人账号的网页版登录，2026 年大量账号
扫码后仍提示"无法登录网页版微信"。本通道：
  * available() 只探测入口端点可达性，并明确"账号是否被关停需登录方知"；
  * 不逆向凭据、不保存扫码态、不绕账号配额；
  * collect(dom_html=...) 解析用户手动登录后导出的聊天区 DOM（纯函数可单测）。
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from ..base import (SourceAdapter, Message, Availability, RISK_MEDIUM,
                    PLAT_PC, DIR_SENT, DIR_RECEIVED, ok, missing)

logger = logging.getLogger(__name__)

ENDPOINT = "https://wx.qq.com/"


# ====================================================================== #
# 纯函数：DOM → Message（可离线单测，不触网）
# ====================================================================== #
_ME_CLASS = re.compile(r"\bme\b", re.I)


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(frag: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", frag)).strip()


def parse_webchat_dom(html: str, source_id: str = "P11_webwechat",
                      peer_name: Optional[str] = None) -> List[Message]:
    """把网页版聊天区 HTML 解析成 Message 列表（纯函数，不触网不登录）。

    按 <div class="message ..."> 块切分：class 含 me=我发(sent)，否则对方(received)。
    """
    if not html:
        return []
    out: List[Message] = []
    blocks = re.split(r'(?=<div[^>]*class="[^"]*message[^"]*")', html)
    for blk in blocks:
        m = re.match(r'\s*<div[^>]*class="([^"]*)"', blk)
        if not m or "message" not in m.group(1):
            continue
        cls = m.group(1)
        # me 块才判 sent，其余（含 recv/friend/plain message）一律 received
        direction = DIR_SENT if _ME_CLASS.search(cls) else DIR_RECEIVED
        text = _strip_tags(blk)
        if not text:
            continue
        out.append(Message(
            direction=direction, content=text, timestamp=None,
            msg_type="text", source_id=source_id, peer_name=peer_name,
            speaker="我" if direction == DIR_SENT else (peer_name or "对方")))
    return out


# ====================================================================== #
# 适配器
# ====================================================================== #
class P11WebWechatAdapter(SourceAdapter):
    id = "P11_webwechat"
    name = "网页版微信浏览器读取"
    platform = PLAT_PC
    requires = frozenset({"browser"})
    risk_level = RISK_MEDIUM
    default_enabled = True
    local_runnable = False
    success_prior = 0.2
    produces_plaintext = True

    def available(self, timeout: float = 4.0) -> Availability:
        if not self._probe(timeout=timeout):
            return missing(
                f"网页版微信入口 {ENDPOINT} 不可达（网络/运营商拦截/代理）",
                ["检查网络或代理后重试",
                 "本通道不自动登录、不逆向凭据；能否用取决于账号是否仍开放网页版"])
        return ok(
            "网页版入口可达；注意：多数个人账号已被微信限制网页版登录，"
            "账号是否被关停需手动扫码方知；本适配器不绕配额、不保存凭据",
            endpoint=ENDPOINT, login_required=True)

    def collect(self, dom_html: Optional[str] = None,
                peer_name: Optional[str] = None, **opts) -> List[Message]:
        if not dom_html:
            raise RuntimeError(
                "网页版微信需手动扫码登录；本适配器不自动登录、不逆向/保存凭据。"
                "请在浏览器中登录并打开目标会话后，把聊天区 DOM 作为 dom_html= 传入解析")
        return parse_webchat_dom(dom_html, source_id=self.id, peer_name=peer_name)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _probe(timeout: float = 4.0) -> bool:
        """真实探测入口可达性（只 GET 首页，不登录）。测试可 monkeypatch。"""
        try:
            import requests
            r = requests.get(ENDPOINT, timeout=timeout,
                            headers={"User-Agent": "Mozilla/5.0 LoveAI-collector"})
            return r.status_code < 500
        except Exception:
            return False
