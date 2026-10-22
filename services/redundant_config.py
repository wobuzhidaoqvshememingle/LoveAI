# -*- coding: utf-8 -*-
"""
LoveAI v3.2.0 - 配置文件冗余写（Redundant Config）

【借鉴卡片】
- M4-1（QR 码 RS 纠错）：备份文件加冗余编码，损坏一部分仍可恢复。
- M4-3（冷战微缩胶卷冷备份）：原件在日常读写，冷备份只在原件损坏时读。

【本轮决定】
- LoveAI 真正需要容错冗余的不是 WS 消息（TCP 可靠传输）、不是 SQLite（事务
  已保证），而是**本地 config.json 被写一半损坏**（进程崩溃/磁盘满/杀进程）。
- 现实场景：config.json 是用户唯一配置入口，损坏后启动失败且无备份。
- 本轮先实现"双写 + SHA256 校验和"最小形态；RS(255,223) 编码登记为未来
  增强（需要 pip install reedsolo，本轮不引入新依赖）。

【接口】
- write_redundant(path, data)：原子写主文件 + 同步写 .redun 备份 + 写校验和。
- load_redundant(path) -> dict：主文件损坏时自动从 .redun 恢复；都坏才报错。
- verify_backup(path) -> bool：校验备份完整性（SHA256）。

【硬约束】
- 纯 Python 标准库（json/hashlib/os/shutil/tempfile），不引第三方；
- 写操作走临时文件 + os.replace，避免写一半崩溃留垃圾；
- 不碰 knowledge/、不碰微信数据、不碰 G 盘。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 备份文件名后缀（与主文件同目录）
REDUNDANT_SUFFIX: str = ".redun"
CHECKSUM_SUFFIX: str = ".redun.sha256"

# RS 编码状态登记（未来增强）
RS_STATUS: str = "not_implemented_round1"  # 本轮用双写+sha256 占位


def _checksum_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_bytes(path: str, data: bytes) -> None:
    """临时文件 + os.replace，保证写一半崩溃不留半成品。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".cfg_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_redundant(path: str, data: Any, indent: int = 2) -> dict:
    """原子写主 config + 备份 + 校验和。返回写入报告。

    Args:
        path: 主配置文件路径（如 data/config.json）。
        data: 可 JSON 序列化的配置 dict。
        indent: JSON 缩进。

    Returns:
        {"main": path, "backup": backup_path, "checksum": sha256, "rs": RS_STATUS}
    """
    payload = json.dumps(data, ensure_ascii=False, indent=indent).encode("utf-8")
    digest = _checksum_bytes(payload)
    backup_path = path + REDUNDANT_SUFFIX
    checksum_path = path + CHECKSUM_SUFFIX

    # 1) 写主文件
    _atomic_write_bytes(path, payload)
    # 2) 写备份（内容与主完全一致；RS 编码未来在这一层做擦除编码）
    _atomic_write_bytes(backup_path, payload)
    # 3) 写校验和（单独文件，记录 payload 的 sha256）
    _atomic_write_bytes(checksum_path, digest.encode("ascii"))

    logger.info("配置冗余写完成 main=%s backup=%s sha256=%s…",
                path, backup_path, digest[:12])
    return {
        "main": path,
        "backup": backup_path,
        "checksum": digest,
        "rs": RS_STATUS,
    }


def _read_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def verify_backup(path: str) -> bool:
    """校验备份文件的 SHA256 是否与 checksum 文件一致。"""
    backup = _read_bytes(path + REDUNDANT_SUFFIX)
    checksum = _read_bytes(path + CHECKSUM_SUFFIX)
    if backup is None or checksum is None:
        return False
    expected = checksum.decode("ascii", "replace").strip()
    return _checksum_bytes(backup) == expected


def load_redundant(path: str) -> dict:
    """加载配置；主文件损坏时自动从备份恢复。

    Returns:
        解析后的 dict。

    Raises:
        FileNotFoundError: 主文件与备份都不存在。
        ValueError: 主文件与备份都解析失败。
    """
    raw_main = _read_bytes(path)
    # 1) 主文件优先
    if raw_main is not None:
        try:
            return json.loads(raw_main.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("主配置损坏（%s），尝试从备份恢复: %s", path, e)

    # 2) 主坏了 → 备份校验和通过才用
    if verify_backup(path):
        raw_backup = _read_bytes(path + REDUNDANT_SUFFIX)
        try:
            data = json.loads(raw_backup.decode("utf-8"))
            # 顺手把好的备份回写主文件（自愈）
            try:
                write_redundant(path, data)
            except Exception as e:  # 自愈失败不阻塞加载
                logger.warning("备份恢复后回写主文件失败（忽略）: %s", e)
            logger.info("配置已从备份 %s%s 恢复", path, REDUNDANT_SUFFIX)
            return data
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error("备份也解析失败: %s", e)

    # 3) 都坏了
    if raw_main is None:
        raise FileNotFoundError(f"配置文件不存在且无备份: {path}")
    raise ValueError(f"配置主文件与备份均损坏: {path}")
