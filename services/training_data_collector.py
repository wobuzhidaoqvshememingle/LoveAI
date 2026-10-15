# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 本地训练数据自动收集器（TrainingDataCollector）

职责：
1. 自动收集：用户消息、AI 生成的回复、用户对回复的选择 / 反馈（A/B 学习）、
   用户手动纠正，全程**只在本地落盘**，不上传任何服务器。
2. 数据标注：标记哪些回复被用户采用（used）、哪些被修改后采用（modified）、
   哪些被丢弃（rejected）、哪些被用户手动纠正（corrected）。
3. 数据清洗：去重、过滤无效对话（空消息 / 过短消息）、统一字段格式。
4. 格式转换：导出为 LoRA 微调常用的 alpaca 格式（instruction/input/output）
   或 sharegpt 格式（conversations 列表）。
5. 本地存储：项目根目录 ``data/training_data/``，用户可查看 / 删除。

对外主 API：
    - collect_interaction(user_msg, ai_reply, feedback_type, metadata) -> dict
    - export_dataset(format="alpaca") -> str(导出文件绝对路径)
    - get_stats() -> dict
    - clear_data() -> dict
    - list_records(limit) -> list[dict]

服务层纯 Python：不 import PySide6 / PyQt，便于在后台线程与测试中直接使用。
"""
from __future__ import annotations

import os
import re
import json
import time
import uuid
import logging
from datetime import datetime
from collections import Counter

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 合法反馈类型枚举
FEEDBACK_USED = "used"          # 用户直接采用了 AI 回复
FEEDBACK_MODIFIED = "modified"  # 用户修改后采用
FEEDBACK_REJECTED = "rejected"  # 用户没有采用
FEEDBACK_CORRECTED = "corrected"  # 用户手动纠正（负样本转正样本）
VALID_FEEDBACKS = (FEEDBACK_USED, FEEDBACK_MODIFIED, FEEDBACK_REJECTED, FEEDBACK_CORRECTED)

#: 可用于 SFT 训练的“正样本”反馈类型（rejected 仅留存做负向分析，不进导出）
_POSITIVE_FEEDBACKS = (FEEDBACK_USED, FEEDBACK_MODIFIED, FEEDBACK_CORRECTED)

#: 导出目录名
_EXPORT_DIRNAME = "exports"


class TrainingDataCollector:
    """本地训练数据收集器。

    Args:
        data_dir: 数据目录，默认 ``<项目根>/data/training_data``。
        event_bus: 可选 EventBus，数据收集后发布事件供 UI / 引擎联动。
    """

    #: 单条用户消息 / AI 回复的最小有效长度（去掉空白后），短于此视为无效
    MIN_VALID_LEN = 2

    def __init__(self, data_dir: str | None = None, event_bus=None) -> None:
        self.data_dir = data_dir or os.path.join(_PROJECT_ROOT, "data", "training_data")
        self.event_bus = event_bus
        os.makedirs(self.data_dir, exist_ok=True)
        self._export_dir = os.path.join(self.data_dir, _EXPORT_DIRNAME)
        os.makedirs(self._export_dir, exist_ok=True)
        self._file = os.path.join(self.data_dir, "interactions.jsonl")
        # 内存索引：用于快速去重（key -> record_id）
        self._dedup_index: dict[str, str] = {}
        self._load_dedup_index()

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _now_iso() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _visible_len(s: str) -> int:
        """去掉空白后的可见长度。"""
        return len(re.sub(r"\s", "", s or ""))

    @classmethod
    def _is_valid(cls, *texts: str) -> bool:
        """判断一条交互是否“有效”：至少用户消息与最终回复都要有内容。"""
        for t in texts:
            if cls._visible_len(t) < cls.MIN_VALID_LEN:
                return False
        return True

    def _dedup_key(self, user_msg: str, ai_reply: str, person_id: str) -> str:
        """生成去重指纹：同一人 + 同一用户消息 + 同一 AI 回复视为重复。"""
        base = f"{person_id}|{user_msg.strip()}|{ai_reply.strip()}"
        import hashlib
        return hashlib.md5(base.encode("utf-8")).hexdigest()

    def _load_dedup_index(self) -> None:
        """启动时把已有记录的去重指纹载入内存，避免重复写入。"""
        if not os.path.exists(self._file):
            return
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = rec.get("_dedup_key")
                    if key:
                        self._dedup_index[key] = rec.get("id", "")
        except Exception as e:  # noqa: BLE001
            logger.debug("加载去重索引失败: %s", e)

    # ------------------------------------------------------------------ #
    # 对外主 API：收集
    # ------------------------------------------------------------------ #
    def collect_interaction(
        self,
        user_msg: str,
        ai_reply: str,
        feedback_type: str = FEEDBACK_USED,
        metadata: dict | None = None,
    ) -> dict:
        """收集一条人机交互样本。

        Args:
            user_msg: 用户本人发出的消息（训练时的“指令 / 上文”）。
            ai_reply: AI 生成的原始回复。
            feedback_type: 反馈类型，见 ``VALID_FEEDBACKS``。非法值按 ``rejected`` 处理。
            metadata: 可选附加信息，支持字段：
                - modified_reply (str): 用户修改后真正使用的回复（feedback=modified 时必填）
                - conversation_id (str): 会话 ID
                - person_id (str): 聊天对象 ID
                - source (str): 来源（如 "analyze_chat" / "manual"）

        Returns:
            落库后的记录 dict；无效或重复时返回带 ``duplicated`` / ``skipped`` 标记的 dict。
        """
        metadata = metadata or {}
        user_msg = (user_msg or "").strip()
        ai_reply = (ai_reply or "").strip()
        modified_reply = (metadata.get("modified_reply") or "").strip()

        # 反馈类型兜底
        if feedback_type not in VALID_FEEDBACKS:
            logger.warning("未知 feedback_type=%s，按 rejected 处理", feedback_type)
            feedback_type = FEEDBACK_REJECTED

        # 数据清洗：无效对话直接丢弃（rejected 样本本身可短，但要能对上 ai_reply）
        if not self._is_valid(user_msg, ai_reply):
            logger.debug("丢弃无效交互：user_msg/ai_reply 过短")
            return {"ok": False, "skipped": True, "reason": "invalid_length"}

        # modified 反馈：若用户给了修改后回复，以其作为最终采用内容
        if feedback_type == FEEDBACK_MODIFIED and not modified_reply:
            # 用户标了“修改”却没给修改后文本，退回到原始 ai_reply
            modified_reply = ai_reply

        # 去重
        person_id = metadata.get("person_id", "default")
        key = self._dedup_key(user_msg, ai_reply, person_id)
        if key in self._dedup_index:
            logger.debug("重复交互已忽略: %s", key[:8])
            return {"ok": False, "duplicated": True, "existing_id": self._dedup_index[key]}

        record = {
            "id": uuid.uuid4().hex[:12],
            "timestamp": self._now_iso(),
            "user_message": user_msg,
            "ai_reply": ai_reply,
            "feedback": feedback_type,
            "modified_reply": modified_reply or None,
            "conversation_id": metadata.get("conversation_id"),
            "person_id": person_id,
            "source": metadata.get("source", "analyze_chat"),
            "_dedup_key": key,
        }

        try:
            with open(self._file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001
            logger.error("写入训练数据失败: %s", e)
            return {"ok": False, "reason": f"write_error: {e}"}

        self._dedup_index[key] = record["id"]
        logger.info("收集训练样本 #%s feedback=%s (累计 %d)",
                    record["id"], feedback_type, len(self._dedup_index))

        # 事件总线联动（可选）
        if self.event_bus is not None:
            try:
                self.event_bus.publish("training_data_collected", {
                    "count": len(self._dedup_index),
                    "feedback": feedback_type,
                })
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "record": record, "count": len(self._dedup_index)}

    # ------------------------------------------------------------------ #
    # 读取 / 统计
    # ------------------------------------------------------------------ #
    def _read_all(self) -> list[dict]:
        if not os.path.exists(self._file):
            return []
        records: list[dict] = []
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:  # noqa: BLE001
            logger.error("读取训练数据失败: %s", e)
        return records

    def list_records(self, limit: int | None = None) -> list[dict]:
        """返回全部记录（默认）或最新 ``limit`` 条（按时间正序返回）。"""
        records = self._read_all()
        if limit is not None and limit > 0:
            records = records[-limit:]
        return records

    def get_stats(self) -> dict:
        """返回数据统计：总数、各反馈类型分布、正样本数（可训练）、最近时间。"""
        records = self._read_all()
        total = len(records)
        feedback_counter: Counter = Counter(r.get("feedback", "unknown") for r in records)
        positive = sum(1 for r in records if r.get("feedback") in _POSITIVE_FEEDBACKS)
        persons = len({r.get("person_id") for r in records if r.get("person_id")})
        latest = records[-1]["timestamp"] if records else None
        return {
            "total": total,
            "positive_samples": positive,           # 可直接用于 SFT 的样本数
            "by_feedback": dict(feedback_counter),
            "person_count": persons,
            "latest_timestamp": latest,
            "data_dir": self.data_dir,
            "file_path": self._file,
        }

    # ------------------------------------------------------------------ #
    # 导出
    # ------------------------------------------------------------------ #
    def _positive_records(self) -> list[dict]:
        """筛选可用于训练的正样本记录。"""
        return [r for r in self._read_all() if r.get("feedback") in _POSITIVE_FEEDBACKS]

    @staticmethod
    def _final_output(r: dict) -> str:
        """取该样本“最终应学习的回复”：修改后回复优先，否则原始 AI 回复。"""
        return (r.get("modified_reply") or r.get("ai_reply") or "").strip()

    def export_dataset(self, fmt: str = "alpaca") -> str:
        """导出为 LoRA 训练格式，写入 exports/ 目录。

        Args:
            fmt: ``"alpaca"`` 或 ``"sharegpt"``。

        Returns:
            导出文件的绝对路径。无正样本时仍导出空列表文件。
        """
        fmt = (fmt or "alpaca").lower()
        records = self._positive_records()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        if fmt == "sharegpt":
            data = []
            for r in records:
                data.append({
                    "conversations": [
                        {"role": "user", "content": r.get("user_message", "")},
                        {"role": "assistant", "content": self._final_output(r)},
                    ],
                    "source": r.get("source", "analyze_chat"),
                })
            out_name = f"train_sharegpt_{ts}.json"
        else:
            # 默认 alpaca
            data = []
            for r in records:
                data.append({
                    "instruction": r.get("user_message", ""),
                    "input": "",
                    "output": self._final_output(r),
                })
            out_name = f"train_alpaca_{ts}.json"

        out_path = os.path.join(self._export_dir, out_name)
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            logger.error("导出数据集失败: %s", e)
            raise
        logger.info("已导出 %d 条 %s 格式样本 -> %s", len(data), fmt, out_path)
        return out_path

    # ------------------------------------------------------------------ #
    # 清空
    # ------------------------------------------------------------------ #
    def clear_data(self) -> dict:
        """清空全部本地训练数据（高风险操作，UI 层需二次确认）。

        Returns:
            删除的记录数。
        """
        count = len(self._read_all())
        try:
            if os.path.exists(self._file):
                os.remove(self._file)
        except Exception as e:  # noqa: BLE001
            logger.error("清空训练数据失败: %s", e)
            return {"ok": False, "reason": str(e)}
        self._dedup_index.clear()
        logger.warning("已清空全部训练数据，删除 %d 条", count)
        return {"ok": True, "deleted": count}
