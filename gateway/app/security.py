# -*- coding: utf-8 -*-
"""
AES-256-GCM 密钥加密存储。

主密钥来源: 环境变量 GATEWAY_MASTER_KEY (32 字节随机串, 启动时如果没有就生成并
持久化到 data_dir/.master_key, 后续复用)。

密文格式: base64( nonce(12B) || ciphertext || tag(16B) )
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_MASTER_KEY_CACHE: bytes | None = None


def _load_or_create_master_key(master_key_env: str, data_dir: str) -> bytes:
    global _MASTER_KEY_CACHE
    if _MASTER_KEY_CACHE is not None:
        return _MASTER_KEY_CACHE
    env_val = os.environ.get(master_key_env, "").strip()
    if env_val:
        # 支持 hex 或 base64
        try:
            key = bytes.fromhex(env_val)
        except ValueError:
            key = base64.b64decode(env_val)
        if len(key) != 32:
            raise ValueError(f"GATEWAY_MASTER_KEY must be 32 bytes, got {len(key)}")
        _MASTER_KEY_CACHE = key
        return key
    # 没配环境变量, 落盘到 data_dir
    key_path = Path(data_dir) / ".master_key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        key = bytes.fromhex(key_path.read_text(encoding="utf-8").strip())
    else:
        key = AESGCM.generate_key(bit_length=256)
        key_path.write_text(key.hex(), encoding="utf-8")
        try:
            key_path.chmod(0o600)
        except Exception:
            pass
    _MASTER_KEY_CACHE = key
    return key


def encrypt(plaintext: str, data_dir: str = "./data", master_key_env: str = "GATEWAY_MASTER_KEY") -> str:
    key = _load_or_create_master_key(master_key_env, data_dir)
    aes = AESGCM(key)
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt(token: str, data_dir: str = "./data", master_key_env: str = "GATEWAY_MASTER_KEY") -> str:
    key = _load_or_create_master_key(master_key_env, data_dir)
    raw = base64.b64decode(token)
    nonce, ct = raw[:12], raw[12:]
    aes = AESGCM(key)
    return aes.decrypt(nonce, ct, None).decode("utf-8")
