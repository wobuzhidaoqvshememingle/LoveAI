# -*- coding: utf-8 -*-
"""数据访问层（Data Access Layer, DAL）

定位：约定+校验+审计层（非沙箱，Python 无强制访问控制）
职责：
  - 写前 schema 校验（字段类型/范围/枚举）
  - 写后审计记录（谁写了什么、何时写）
  - 同字段双写告警（不静默覆盖，日志/输出标记）
  - 读路径统一入口（从 profile 取，不重算）

"写受控"由三件套共同实现：
  1. 属主约定（每字段唯一属主）
  2. analyze() 写序（固定顺序写入）
  3. 冲突告警（同字段双写时告警，不阻塞主流程）
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Schema 定义：受控字段的类型/范围/枚举
SCHEMA: Dict[str, Dict[str, Any]] = {
    "attachment": {
        "type": dict,
        "required_keys": ["scores", "type", "description"],
    },
    "psychology": {
        "type": dict,
        "required_keys": ["cbt_distortions", "dark_triad_tendencies", "overall_health"],
    },
    "suspicion_chain": {
        "type": dict,
        "required_keys": ["suspicion", "level"],
    },
    "deception_risk": {
        "type": dict,
        "required_keys": ["risk_score", "risk_level", "warnings"],
        "range": {"risk_score": (0, 100)},
        "enum": {"risk_level": ["低", "中", "高", "观察"]},
    },
}

# 写序记录：字段 → 最后写入者（用于双写告警）
_write_audit: Dict[str, Dict[str, Any]] = {}


def write(profile: Dict[str, Any], field: str, value: Any, owner: str) -> Dict[str, Any]:
    """写入 profile 字段（带 schema 校验 + 双写告警 + 审计）

    Args:
        profile: 画像 dict
        field: 字段名
        value: 字段值
        owner: 写入者（模块名）

    Returns:
        更新后的 profile
    """
    # Schema 校验
    if field in SCHEMA:
        spec = SCHEMA[field]
        expected_type = spec.get("type")
        if expected_type and not isinstance(value, expected_type):
            logger.warning("DAL schema 校验失败: %s 类型=%s 预期=%s",
                           field, type(value).__name__, expected_type.__name__)

        # 范围校验
        range_spec = spec.get("range", {})
        if range_spec and isinstance(value, dict):
            for k, (min_v, max_v) in range_spec.items():
                v = value.get(k)
                if v is not None and not (min_v <= v <= max_v):
                    logger.warning("DAL 范围校验失败: %s.%s=%s 超出 [%s,%s]",
                                   field, k, v, min_v, max_v)

        # 枚举校验
        enum_spec = spec.get("enum", {})
        if enum_spec and isinstance(value, dict):
            for k, allowed in enum_spec.items():
                v = value.get(k)
                if v is not None and v not in allowed:
                    logger.warning("DAL 枚举校验失败: %s.%s=%s 不在 %s",
                                   field, k, v, allowed)

    # 双写告警：同字段已被写入过，检查是否为同一 owner
    if field in _write_audit:
        prev_owner = _write_audit[field]["owner"]
        if prev_owner != owner:
            logger.warning("DAL 双写告警: %s 已由 %s 写入，现被 %s 覆盖",
                           field, prev_owner, owner)

    # 审计记录
    _write_audit[field] = {
        "owner": owner,
        "written_at": datetime.now().isoformat(),
    }

    # 执行写入
    profile[field] = value
    return profile


def read(profile: Dict[str, Any], field: str, default: Any = None) -> Any:
    """从 profile 读取字段（读路径统一入口）

    Args:
        profile: 画像 dict
        field: 字段名
        default: 字段不存在时的默认值

    Returns:
        字段值或默认值
    """
    return profile.get(field, default)


def get_audit_log() -> Dict[str, Dict[str, Any]]:
    """获取写审计日志（用于调试/验证）"""
    return dict(_write_audit)


def reset_audit():
    """重置审计日志（测试用）"""
    global _write_audit
    _write_audit = {}


# --------------------------------------------------------------------------- #
# 兼容 contracts 数据类（旧 services/data_pool/contracts 语义，按 affection_reasoning
# 的真实使用点推导重建，供中心数据池约束通道复用）。
# --------------------------------------------------------------------------- #
@dataclass
class Message:
    """聊天消息（对齐旧 contracts.Message）。"""
    role: str = "user"
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "metadata": dict(self.metadata),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "Message":
        if not isinstance(d, dict):
            d = {}
        return cls(
            role=d.get("role", "user"),
            content=d.get("content", ""),
            metadata=dict(d.get("metadata") or {}),
            timestamp=float(d.get("timestamp") or datetime.now().timestamp()),
        )


@dataclass
class Evidence:
    """证据条目（对齐旧 contracts.Evidence）：text/source/confidence/metadata。"""
    text: str = ""
    source: str = ""
    confidence: float = 0.5
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "source": self.source,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "Evidence":
        if not isinstance(d, dict):
            d = {}
        return cls(
            text=d.get("text", ""),
            source=d.get("source", ""),
            confidence=float(d.get("confidence") if d.get("confidence") is not None else 0.5),
            metadata=dict(d.get("metadata") or {}),
        )
