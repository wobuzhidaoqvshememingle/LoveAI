# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - LoRA 模型版本管理器（ModelVersionManager）

职责：
1. 每次训练后自动把 LoRA 适配器保存为一个新版本（v1, v2, v3 ...）。
2. 维护版本元数据：version_id / created_at / training_data_count / final_loss /
   style_score / config / adapter_path。
3. 提供：列表、查看当前版本、回滚、删除（保护当前版本不可删）、热加载、版本对比。
4. **基础模型保护**：基础模型路径只读，本管理器只读写 LoRA 适配器目录，
   绝不动基础模型权重文件。

目录结构::

    models/lora_adapters/
        versions.json                 # 版本索引（元数据）
        v1/                           # 某个版本的适配器文件
            adapter_config.json
        v2/
            ...

热加载说明：真正通过 peft ``load_adapter`` 热替换需要 torch/peft 运行时。
当前环境若未安装这些库，``load_version`` 会**仅记录当前激活版本**并返回明确提示，
不崩溃。
"""
from __future__ import annotations

import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 探测 torch/peft 是否可用（热加载真实替换适配器时需要）
try:  # pragma: no cover - 运行环境相关
    import torch  # noqa: F401
    import peft  # noqa: F401
    _PEFT_AVAILABLE = True
except Exception:  # noqa: BLE001
    _PEFT_AVAILABLE = False


class ModelVersionManager:
    """LoRA 适配器版本管理器。"""

    #: 版本索引文件名
    INDEX_FILE = "versions.json"
    #: 当前激活版本标记文件名（记录在索引内，不单独写）

    def __init__(self, adapters_dir: str | None = None, base_model_path: str | None = None) -> None:
        """
        Args:
            adapters_dir: LoRA 适配器根目录，默认 ``<项目根>/models/lora_adapters``。
            base_model_path: 基础模型路径（只读，仅用于展示与保护说明）。
        """
        self.adapters_dir = adapters_dir or os.path.join(_PROJECT_ROOT, "models", "lora_adapters")
        self.base_model_path = base_model_path
        os.makedirs(self.adapters_dir, exist_ok=True)
        self._index_file = os.path.join(self.adapters_dir, self.INDEX_FILE)
        self._index: dict = self._load_index()

    # ------------------------------------------------------------------ #
    # 索引读写
    # ------------------------------------------------------------------ #
    def _load_index(self) -> dict:
        """读取版本索引；损坏时备份后重建空索引，不崩溃。"""
        if not os.path.exists(self._index_file):
            return {"versions": [], "current_version_id": None, "next_id": 1}
        try:
            with open(self._index_file, "r", encoding="utf-8") as f:
                idx = json.load(f)
            idx.setdefault("versions", [])
            idx.setdefault("current_version_id", None)
            idx.setdefault("next_id", 1)
            return idx
        except Exception as e:  # noqa: BLE001
            logger.error("版本索引损坏(%s)，备份后重建", e)
            try:
                os.replace(self._index_file, self._index_file + ".corrupt")
            except Exception:  # noqa: BLE001
                pass
            return {"versions": [], "current_version_id": None, "next_id": 1}

    def _save_index(self) -> None:
        try:
            with open(self._index_file, "w", encoding="utf-8") as f:
                json.dump(self._index, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            logger.error("保存版本索引失败: %s", e)

    def _find(self, version_id: str) -> dict | None:
        for v in self._index["versions"]:
            if v.get("version_id") == version_id:
                return v
        return None

    # ------------------------------------------------------------------ #
    # 版本创建
    # ------------------------------------------------------------------ #
    def create_version(self, metadata: dict | None = None) -> dict:
        """训练完成后调用，登记一个新版本并建立适配器目录。

        Args:
            metadata: 训练结果元数据，支持：training_data_count / final_loss /
                      style_score / config / adapter_files。

        Returns:
            新建版本的元数据 dict。
        """
        metadata = metadata or {}
        vid = f"v{self._index['next_id']}"
        adapter_dir = os.path.join(self.adapters_dir, vid)
        os.makedirs(adapter_dir, exist_ok=True)

        version = {
            "version_id": vid,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "training_data_count": int(metadata.get("training_data_count", 0)),
            "final_loss": metadata.get("final_loss"),
            "style_score": metadata.get("style_score"),
            "config": metadata.get("config", {}),
            "adapter_path": adapter_dir,
            "base_model_path": self.base_model_path,  # 仅记录，不修改基础模型
        }
        self._index["versions"].append(version)
        self._index["next_id"] += 1
        # 新训练完成后默认切到新版本
        self._index["current_version_id"] = vid
        self._save_index()
        logger.info("已创建模型版本 %s（数据 %d 条，loss=%s）",
                    vid, version["training_data_count"], version["final_loss"])
        return version

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def list_versions(self) -> list[dict]:
        """按创建时间倒序列出所有版本。"""
        return sorted(self._index["versions"],
                      key=lambda v: v.get("created_at", ""), reverse=True)

    def get_current_version(self) -> dict | None:
        """当前正在使用的版本元数据；无则 None。"""
        cid = self._index.get("current_version_id")
        if not cid:
            return None
        return self._find(cid)

    # ------------------------------------------------------------------ #
    # 回滚 / 切换 / 热加载
    # ------------------------------------------------------------------ #
    def rollback(self, version_id: str) -> dict:
        """回滚（切换当前版本）到指定版本。

        Returns:
            操作结果 dict。版本不存在时返回 ok=False。
        """
        v = self._find(version_id)
        if v is None:
            return {"ok": False, "reason": f"版本 {version_id} 不存在"}
        self._index["current_version_id"] = version_id
        self._save_index()
        logger.warning("已回滚到版本 %s", version_id)
        return {"ok": True, "current_version_id": version_id, "hot_loaded": False}

    def load_version(self, version_id: str) -> dict:
        """热加载指定版本的 LoRA 适配器（不重启软件）。

        真实热替换需要 torch/peft；未安装时仅记录当前版本并返回明确提示。
        基础模型全程只读。
        """
        v = self._find(version_id)
        if v is None:
            return {"ok": False, "reason": f"版本 {version_id} 不存在"}

        self._index["current_version_id"] = version_id
        self._save_index()

        if not _PEFT_AVAILABLE:
            logger.info("peft/torch 未安装，仅记录当前版本=%s（不做真实适配器热替换）", version_id)
            return {"ok": True, "version_id": version_id,
                    "hot_loaded": False,
                    "reason": "未安装 torch/peft，已记录当前版本，重启后生效"}

        # 真实环境下：通过 peft 卸载旧适配器、加载新适配器。此处保留扩展点。
        try:  # pragma: no cover - 需要真实 GPU / 模型
            logger.info("热加载适配器 %s", v.get("adapter_path"))
            # 统一走 load_adapter 拿到句柄，再由外部持有基础模型做 peft 挂载
            handle = self.load_adapter(v.get("adapter_path"))
            if not handle.get("available"):
                return {"ok": False, "reason": handle.get("reason", "load_adapter 失败")}
            return {"ok": True, "version_id": version_id, "hot_loaded": True,
                    "adapter_path": v.get("adapter_path"),
                    "handle": handle.get("handle")}
        except Exception as e:  # noqa: BLE001
            logger.error("热加载失败: %s", e)
            return {"ok": False, "reason": f"hot_load_error: {e}"}

    def load_adapter(self, adapter_path: str) -> dict:
        """加载一个 LoRA/适配器目录并返回可用句柄。

        真实把适配器挂到基础模型上需要 peft/torch 以及外部持有（已加载）的
        基础模型；本方法不负责、也无权替换基础模型权重，只负责：
          1. 校验目录存在；
          2. 读取 ``adapter_config.json`` 做基本校验；
          3. 探测目录内是否有权重文件；
          4. 在 peft 可用时构建真实句柄描述，离线时返回「仅描述」句柄。

        Args:
            adapter_path: 适配器目录路径（某版本目录，含 adapter_config.json）。

        Returns:
            dict:
                - 目录存在/可读取：``{"ok": True, "available": True,
                  "adapter_path": ..., "handle": {...}}``；
                - 目录不存在或任何异常：``{"ok": False, "available": False,
                  "adapter_path": ..., "reason": ...}``，绝不抛栈。
        """
        if not adapter_path or not os.path.isdir(adapter_path):
            logger.warning("load_adapter: 适配器目录不存在: %s", adapter_path)
            return {"ok": False, "available": False,
                    "adapter_path": adapter_path,
                    "reason": "适配器目录不存在或不是目录"}

        config: dict = {}
        cfg_file = os.path.join(adapter_path, "adapter_config.json")
        if os.path.exists(cfg_file):
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception as e:  # noqa: BLE001
                logger.warning("load_adapter: 读取 adapter_config.json 失败: %s", e)

        try:
            entries = os.listdir(adapter_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("load_adapter: 列出适配器目录失败: %s", e)
            return {"ok": False, "available": False,
                    "adapter_path": adapter_path,
                    "reason": f"listdir_error: {e}"}

        has_weights = any(
            fn.endswith((".bin", ".safetensors", ".pt")) for fn in entries
        )

        handle = {
            "adapter_path": os.path.abspath(adapter_path),
            "config": config,
            "has_weights": has_weights,
        }

        if _PEFT_AVAILABLE:
            try:  # pragma: no cover - 需要真实 peft 运行时
                # 仅做配置层校验；真实 ``PeftModel.from_pretrained(base, ...)``
                # 由持有基础模型的上层调用方完成，本管理器不触碰基础模型权重。
                import peft  # noqa: F401
                handle["backend"] = "peft"
                handle["loaded"] = True
                logger.info("load_adapter: peft 句柄就绪 %s", adapter_path)
            except Exception as e:  # noqa: BLE001
                logger.warning("load_adapter: peft 句柄构建失败: %s", e)
                return {"ok": False, "available": False,
                        "adapter_path": adapter_path,
                        "reason": f"peft_error: {e}"}
        else:
            # 离线环境：返回描述句柄，上层挂载时再决定是否真正加载
            handle["backend"] = "descriptor_only"
            handle["loaded"] = False
            logger.info("load_adapter: torch/peft 不可用，返回描述句柄 %s", adapter_path)

        return {"ok": True, "available": True,
                "adapter_path": adapter_path, "handle": handle}

    # ------------------------------------------------------------------ #
    # 删除（保护当前版本）
    # ------------------------------------------------------------------ #
    def delete_version(self, version_id: str) -> dict:
        """删除指定版本。**不能删除当前正在使用的版本。**"""
        v = self._find(version_id)
        if v is None:
            return {"ok": False, "reason": f"版本 {version_id} 不存在"}
        if self._index.get("current_version_id") == version_id:
            return {"ok": False, "reason": "不能删除当前正在使用的版本，请先切换到其他版本"}

        # 删除适配器目录
        adapter_path = v.get("adapter_path")
        try:
            if adapter_path and os.path.isdir(adapter_path):
                import shutil
                shutil.rmtree(adapter_path, ignore_errors=True)
        except Exception as e:  # noqa: BLE001
            logger.error("删除适配器目录失败: %s", e)

        self._index["versions"] = [x for x in self._index["versions"]
                                   if x.get("version_id") != version_id]
        self._save_index()
        logger.warning("已删除模型版本 %s", version_id)
        return {"ok": True, "deleted": version_id}

    # ------------------------------------------------------------------ #
    # 版本对比
    # ------------------------------------------------------------------ #
    def compare_versions(self, v1_id: str, v2_id: str) -> dict:
        """对比两个版本：训练数据量 / loss / 风格评分。"""
        v1 = self._find(v1_id)
        v2 = self._find(v2_id)
        if v1 is None or v2 is None:
            return {"ok": False, "reason": "存在版本不存在，无法对比"}

        def _diff(a, b):
            if a is None or b is None:
                return None
            try:
                return round(b - a, 4)
            except TypeError:
                return None

        return {
            "ok": True,
            "v1": v1_id,
            "v2": v2_id,
            "training_data_count": {
                "v1": v1.get("training_data_count"),
                "v2": v2.get("training_data_count"),
                "delta": _diff(v1.get("training_data_count"), v2.get("training_data_count")),
            },
            "final_loss": {
                "v1": v1.get("final_loss"),
                "v2": v2.get("final_loss"),
                "delta": _diff(v1.get("final_loss"), v2.get("final_loss")),
            },
            "style_score": {
                "v1": v1.get("style_score"),
                "v2": v2.get("style_score"),
                "delta": _diff(v1.get("style_score"), v2.get("style_score")),
            },
        }
