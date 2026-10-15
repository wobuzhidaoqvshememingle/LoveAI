# -*- coding: utf-8 -*-
"""采集体系统一抽象：标准消息、风险等级、可用性结果、SourceAdapter 接口。

所有采集通道（PC / 安卓 / 官方编排 / 第三方 / iOS 预留）都实现 SourceAdapter，
输出统一的 Message 序列，再由 store 对齐 chat_messages 表入库。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# 常量 / 枚举（用字符串常量，避免枚举在序列化时啰嗦）
# ---------------------------------------------------------------------------

# 风险等级
RISK_NONE = "none"   # 纯离线、只读本地文件，不触网、不注入
RISK_LOW = "low"     # 被动只读、不模拟操作（如用户自己翻、后台截图）
RISK_MEDIUM = "medium"  # 主动但低频拟人，或免 root 读屏
RISK_HIGH = "high"   # 注入/hook、模拟器登录、蓝屏/休眠触发、自动遍历

# 平台
PLAT_PC = "pc"
PLAT_ANDROID = "android"
PLAT_OFFICIAL = "official"
PLAT_THIRDPARTY = "thirdparty"
PLAT_IOS = "ios"

# 方向
DIR_SENT = "sent"
DIR_RECEIVED = "received"


# ---------------------------------------------------------------------------
# 标准消息
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """统一标准消息。不确定字段一律 None，禁止编造。"""
    direction: str = DIR_RECEIVED          # sent / received
    content: Optional[str] = None
    timestamp: Optional[float] = None      # Unix 秒；未知为 None
    msg_type: str = "text"                 # 逻辑类型：text/image/voice/.../unknown
    source_id: str = ""                    # 产出该消息的 adapter id
    person_id: Optional[str] = None        # 归属人物（入库前可空，由编排补齐）
    peer_id: Optional[str] = None          # 对方 wxid / talker
    peer_name: Optional[str] = None        # 对方展示名 / 说话人名
    speaker: Optional[str] = None          # 原始说话人标签
    wechat_msg_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> Dict[str, Any]:
        """映射到 chat_messages 表列（不含 id/person 关联外的由 store 补）。"""
        created_at = ""
        if self.timestamp:
            try:
                # 与既有 ChatImporter 一致：本地墙钟 ISO
                created_at = datetime.fromtimestamp(float(self.timestamp)).isoformat()
            except Exception:
                created_at = ""
        return {
            "person_id": self.person_id,
            "direction": self.direction if self.direction in (DIR_SENT, DIR_RECEIVED)
            else DIR_RECEIVED,
            "content": self.content if self.content is not None else "",
            "msg_type": self.msg_type or "text",
            "wechat_msg_id": self.wechat_msg_id,
            "created_at": created_at,
        }

    def dedup_key(self) -> tuple:
        """去重键：优先 wechat_msg_id；否则 (方向, 内容)。"""
        if self.wechat_msg_id:
            return ("mid", self.wechat_msg_id)
        return ("dc", self.direction, (self.content or "").strip())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 可用性结果
# ---------------------------------------------------------------------------

@dataclass
class Availability:
    available: bool
    reason: str = ""
    requirements: List[str] = field(default_factory=list)  # 不可用时如何满足条件
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def ok(reason: str = "条件满足", **details) -> Availability:
    return Availability(True, reason, [], details)


def missing(reason: str, requirements: Sequence[str] = (), **details) -> Availability:
    return Availability(False, reason, list(requirements), details)


# ---------------------------------------------------------------------------
# SourceAdapter 抽象基类
# ---------------------------------------------------------------------------

class SourceAdapter(abc.ABC):
    # ---- 元数据（子类以类属性覆盖） ----
    id: str = ""
    name: str = ""
    platform: str = ""
    requires: frozenset = frozenset()      # root / key / adb / app / vision / admin / device
    risk_level: str = RISK_NONE
    default_enabled: bool = True           # 高风险通道默认 False
    local_runnable: bool = False           # 能否离线用 fixture 自测
    # 该通道历史/经验成功率先验（0-1，冷启动先验，非实测真理），用于推荐排序
    success_prior: float = 0.5
    produces_plaintext: bool = True        # 官方编排类不直接出明文 -> False

    # ---- 必须实现 ----
    @abc.abstractmethod
    def available(self) -> Availability:
        """真实探测当前环境是否满足采集条件（不得硬编码 True）。"""

    @abc.abstractmethod
    def collect(self, **opts) -> List[Message]:
        """执行采集，返回标准消息序列。"""

    # ---- 可选覆盖 ----
    def guidance(self) -> List[str]:
        """满足条件的操作引导；默认从一次 available() 的 requirements 取。"""
        try:
            a = self.available()
            return list(a.requirements)
        except Exception:
            return []

    # ---- 通用 ----
    def describe(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "platform": self.platform,
            "requires": sorted(self.requires),
            "risk_level": self.risk_level,
            "default_enabled": self.default_enabled,
            "local_runnable": self.local_runnable,
            "success_prior": self.success_prior,
            "produces_plaintext": self.produces_plaintext,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} {self.id} risk={self.risk_level}>"
