# -*- coding: utf-8 -*-
"""
Token 压缩引擎。

策略:
  - 对话条数 < min_messages_to_compress 不动;
  - 保留最近 retain_last_n 条原文;
  - 更早的消息:
      * system 消息去重 (相同 content 只留第一条);
      * user/assistant 消息默认替换成占位摘要 "[earlier <role> message compressed]";
  - 但如果消息内容命中 "保护模式" (代码块 / URL / JSON / 纯数字), 则保留原文不压缩,
    避免把代码、链接、结构化数据弄丢。

开关:
  - CompressionConfig.enabled=False -> 完全跳过
  - 请求 gw_allow_fallback=False -> 跳过 (用户指定 provider, 不能乱改上下文)
  - 模型名命中 skip_compression_models 子串 -> 跳过
"""
from __future__ import annotations

import re
from typing import Any, List

from app.config import CompressionConfig
from app.models import ChatCompletionRequest, ChatMessage


# --------------------------------------------------------------------------- #
# 保护模式检测
# --------------------------------------------------------------------------- #
_RE_FENCED_CODE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_RE_INLINE_CODE = re.compile(r"`[^`\n]+`")
_RE_URL = re.compile(r"https?://[^\s)\]]+")
_RE_JSON_BLOCK = re.compile(r"^\s*[\{\[][\s\S]*[\}\]]\s*$")
_RE_NUMERIC = re.compile(r"^[\s\d\.,:\-/]+$")


def _is_protected(text: str) -> bool:
    """内容是否命中保护模式 (代码 / URL / JSON / 纯数字)。"""
    if not text:
        return False
    if _RE_FENCED_CODE.search(text):
        return True
    if _RE_INLINE_CODE.search(text):
        return True
    if _RE_URL.search(text):
        return True
    stripped = text.strip()
    if stripped and _RE_JSON_BLOCK.match(stripped):
        # 粗判 JSON: 首字符 { 或 [
        if stripped[0] in "{[":
            return True
    if _RE_NUMERIC.match(stripped):
        return True
    return False


def _content_to_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # 多模态: 拼接 text 部分
        parts = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("text") or item.get("content") or ""
                if t:
                    parts.append(str(t))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content) if content is not None else ""


# --------------------------------------------------------------------------- #
# 压缩器
# --------------------------------------------------------------------------- #
class MessageCompressor:
    def __init__(self, cfg: CompressionConfig) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------------ #
    def should_skip(self, req: ChatCompletionRequest) -> bool:
        if not self.cfg.enabled:
            return True
        if req.gw_allow_fallback is False:
            return True
        if self.cfg.skip_compression_models:
            model = req.model or ""
            for skip in self.cfg.skip_compression_models:
                if skip and skip in model:
                    return True
        if len(req.messages) < self.cfg.min_messages_to_compress:
            return True
        return False

    # ------------------------------------------------------------------ #
    def compress(self, req: ChatCompletionRequest) -> ChatCompletionRequest:
        """返回压缩后的新 req; 不应修改原 req。"""
        if self.should_skip(req):
            return req

        messages: List[ChatMessage] = list(req.messages)
        retain = max(0, int(self.cfg.retain_last_n_messages))
        cut = len(messages) - retain
        if cut <= 0:
            return req

        head = messages[:cut]
        tail = messages[cut:]

        seen_system: set[str] = set()
        compressed_head: List[ChatMessage] = []
        dropped = 0
        for msg in head:
            text = _content_to_str(msg.content)
            if msg.role == "system":
                if not self.cfg.summarize_system_prompt:
                    compressed_head.append(msg)
                    continue
                key = text.strip()
                if key in seen_system:
                    dropped += 1
                    continue
                seen_system.add(key)
                compressed_head.append(msg)
                continue
            # user / assistant / tool / developer
            if _is_protected(text):
                compressed_head.append(msg)
                continue
            # 用占位摘要替换
            compressed_head.append(
                ChatMessage(
                    role=msg.role,
                    content=f"[earlier {msg.role} message compressed]",
                    name=msg.name,
                )
            )

        new_messages = compressed_head + tail
        return req.model_copy(update={"messages": new_messages})
