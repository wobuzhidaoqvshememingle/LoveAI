# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 判断/推荐反馈服务（点“对 / 错 / 存疑”数据飞轮，P0）

定位（与既有 training_data_collector 的区别）：
- ``services/training_data_collector.py`` 收集的是“用户对某条 AI 回复
  采用 / 修改后采用 / 丢弃”的【训练样本】（used/modified/rejected），
  落 ``data/training_data/interactions.jsonl``，用于 SFT 微调；
- 本服务是【另一套独立子系统】：只收集用户对 AI 每一轮“判断 / 推荐”
  本身的校准反馈——这一轮罗盘指得对不对、策略推荐靠不靠谱、任务建议合不合理
  ——用户点 对(right) / 错(wrong) / 存疑(doubt)，可选写备注。
  落 ``data/loveai.db`` 的 ``feedback_records`` 表，用于回流校准 persona / compass，
  不产出 SFT 样本。

存储 schema（固定，见任务书）::

    feedback_records(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT,
        judgment_type TEXT,           -- compass / strategy / task / emotion
        judgment_id TEXT,             -- 该轮判断/推荐的稳定标识，由输出侧生成
        feedback TEXT,                -- right | wrong | doubt（枚举校验）
        note TEXT,                    -- 可选备注
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )

设计要点：
- 服务层纯 Python：仅依赖标准库 ``sqlite3``，不 import PySide6，
  可在后台线程 / CLI / 测试中直接使用；
- ``db_path`` 可注入：生产默认 ``data/loveai.db``（冻结态走 runtime_paths），
  测试传临时库路径，绝不碰正式 loveai.db；
- 写库前对 feedback 做【严格枚举校验】，非法值抛 ``ValueError``，
  绝不静默兜底（保证数字真实）。

对外主 API：
    - generate_judgment_id(judgment_type) -> str        # 输出侧打标
    - record_feedback(...) -> dict                      # 写一条反馈
    - list_feedback(person_id, limit) -> list[dict]      # 读近期反馈
    - get_stats(person_id) -> dict                       # 对/错/存疑计数与比例
"""
from __future__ import annotations

import os
import uuid
import sqlite3
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 反馈枚举（写库前严格校验，非法值抛错）
FB_RIGHT = "right"   # 这轮判断/推荐“对”了
FB_WRONG = "wrong"   # 这轮判断/推荐“错”了
FB_DOUBT = "doubt"   # “存疑”，拿不准，需要更多证据
VALID_FEEDBACKS = (FB_RIGHT, FB_WRONG, FB_DOUBT)

#: 判断/推荐类型（输出侧打标；这里仅做提示，不强制校验，便于后续扩展）
JUDGMENT_TYPES = ("compass", "strategy", "task", "emotion")

#: 开发态默认库
_FALLBACK_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "loveai.db")


def _resolve_default_db_path() -> str:
    """默认库路径：冻结态走 runtime_paths.writable_data_dir()，开发态回退项目根/data。"""
    try:
        from services import runtime_paths  # 延迟导入，避免循环/冻结导入问题
        return os.path.join(runtime_paths.writable_data_dir(), "loveai.db")
    except Exception as e:  # noqa: BLE001
        logger.debug("runtime_paths 不可用，回退默认库路径: %s", e)
        return _FALLBACK_DB_PATH


class FeedbackService:
    """判断/推荐反馈服务。

    Args:
        db_path: SQLite 库路径。默认 ``<可写数据目录>/loveai.db``。
            测试时传入临时库路径即可，不影响正式库。
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or _resolve_default_db_path()
        data_dir = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(data_dir, exist_ok=True)
        self._ensure_table()
        logger.info("FeedbackService 初始化完成 db=%s", self.db_path)

    # ------------------------------------------------------------------ #
    # 连接 / 建表
    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        """CREATE TABLE IF NOT EXISTS（幂等，写库前调用一次）。"""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS feedback_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        person_id TEXT,
                        judgment_type TEXT,
                        judgment_id TEXT,
                        feedback TEXT,
                        note TEXT,
                        created_at TEXT DEFAULT (datetime('now','localtime'))
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_feedback_person_time "
                    "ON feedback_records(person_id, created_at)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_feedback_judgment "
                    "ON feedback_records(judgment_id)"
                )
        except Exception as e:  # noqa: BLE001
            logger.error("feedback_records 建表失败: %s", e)
            raise

    # ------------------------------------------------------------------ #
    # 输出侧：生成 judgment_id
    # ------------------------------------------------------------------ #
    @staticmethod
    def generate_judgment_id(judgment_type: str = "strategy") -> str:
        """输出侧为每轮判断/推荐生成一个稳定标识。

        格式：``<judgment_type>_<YYYYMMDDTHHMMSS>_<8位随机hex>``。
        同一轮判断/推荐的“对/错/存疑”反馈都引用这同一个 id。
        """
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        return f"{judgment_type}_{ts}_{uuid.uuid4().hex[:8]}"

    # ------------------------------------------------------------------ #
    # 写：record_feedback
    # ------------------------------------------------------------------ #
    def record_feedback(
        self,
        person_id: str,
        judgment_type: str,
        judgment_id: str,
        feedback: str,
        note: str = "",
    ) -> dict:
        """记录一条“对/错/存疑”反馈。

        Args:
            person_id: 聊天对象 ID（如 ``demo0001``）。
            judgment_type: 判断/推荐类型，如 compass/strategy/task/emotion。
            judgment_id: 输出侧生成的该轮判断/推荐稳定标识。
            feedback: ``right`` / ``wrong`` / ``doubt``（非法值抛 ValueError）。
            note: 可选备注（用户为什么觉得错/存疑）。

        Returns:
            dict: {"ok": True, "id": <rowid>, ...}。校验失败抛 ValueError。
        """
        person_id = (person_id or "").strip()
        judgment_type = (judgment_type or "").strip()
        judgment_id = (judgment_id or "").strip()
        feedback = (feedback or "").strip().lower()
        note = (note or "").strip()

        # 严格枚举校验（数字真实，不静默兜底）
        if feedback not in VALID_FEEDBACKS:
            raise ValueError(
                f"非法 feedback={feedback!r}，必须是 {VALID_FEEDBACKS} 之一"
            )
        if not person_id:
            raise ValueError("person_id 不能为空")
        if not judgment_id:
            raise ValueError("judgment_id 不能为空（输出侧应先调用 generate_judgment_id）")
        if not judgment_type:
            raise ValueError("judgment_type 不能为空")

        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "INSERT INTO feedback_records "
                    "(person_id, judgment_type, judgment_id, feedback, note) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (person_id, judgment_type, judgment_id, feedback, note or None),
                )
                rowid = cur.lastrowid
        except Exception as e:  # noqa: BLE001
            logger.error("写入反馈失败: %s", e)
            return {"ok": False, "reason": f"write_error: {e}"}

        logger.info("记录反馈 #%s person=%s type=%s feedback=%s",
                    rowid, person_id, judgment_type, feedback)
        return {
            "ok": True,
            "id": rowid,
            "person_id": person_id,
            "judgment_type": judgment_type,
            "judgment_id": judgment_id,
            "feedback": feedback,
            "note": note or None,
        }

    # ------------------------------------------------------------------ #
    # 读：list_feedback
    # ------------------------------------------------------------------ #
    def list_feedback(self, person_id: str, limit: int = 50) -> list[dict]:
        """返回某对象最近的反馈记录（按时间倒序）。"""
        person_id = (person_id or "").strip()
        limit = max(1, int(limit or 50))
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, person_id, judgment_type, judgment_id, feedback, note, created_at "
                    "FROM feedback_records WHERE person_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (person_id, limit),
                ).fetchall()
        except Exception as e:  # noqa: BLE001
            logger.error("读取反馈失败: %s", e)
            return []
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # 读：get_stats（对/错/存疑计数与比例）
    # ------------------------------------------------------------------ #
    def get_stats(self, person_id: str) -> dict:
        """返回某对象的反馈统计：对/错/存疑计数与占比。

        供 persona/compass 校准消费：wrong 占比高 -> 该类判断需要下调置信度 /
        触发重校准；right 占比高 -> 维持当前画像与罗盘方向。
        """
        person_id = (person_id or "").strip()
        counts = {FB_RIGHT: 0, FB_WRONG: 0, FB_DOUBT: 0}
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT feedback, COUNT(*) AS c FROM feedback_records "
                    "WHERE person_id = ? GROUP BY feedback",
                    (person_id,),
                ).fetchall()
        except Exception as e:  # noqa: BLE001
            logger.error("统计反馈失败: %s", e)
            rows = []
        for r in rows:
            fb = r["feedback"]
            if fb in counts:
                counts[fb] = r["c"]
        total = sum(counts.values())

        def _ratio(n: int) -> float:
            return round(n / total, 4) if total > 0 else 0.0

        return {
            "person_id": person_id,
            "total": total,
            "right": counts[FB_RIGHT],
            "wrong": counts[FB_WRONG],
            "doubt": counts[FB_DOUBT],
            "right_ratio": _ratio(counts[FB_RIGHT]),
            "wrong_ratio": _ratio(counts[FB_WRONG]),
            "doubt_ratio": _ratio(counts[FB_DOUBT]),
            # 校准建议（消费方可直接读这两个字段）
            "accuracy": _ratio(counts[FB_RIGHT]),                 # 命中率 = right/total
            "needs_recalibration": total > 0 and _ratio(counts[FB_WRONG]) >= 0.3,
        }

    # ------------------------------------------------------------------ #
    # 校准消费：按判断类型拆分统计（供 compass / strategy 各自消费）
    # ------------------------------------------------------------------ #
    def get_stats_by_type(self, person_id: str) -> dict:
        """按 judgment_type 拆分统计：``{type: {total, right, wrong, doubt, accuracy}}``。

        罗盘（compass）与策略（strategy）是两套不同判断，需要分别看命中率，
        不能混在一起算平均。
        """
        person_id = (person_id or "").strip()
        out: dict[str, dict] = {}
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT judgment_type, feedback, COUNT(*) AS c "
                    "FROM feedback_records WHERE person_id = ? "
                    "GROUP BY judgment_type, feedback",
                    (person_id,),
                ).fetchall()
        except Exception as e:  # noqa: BLE001
            logger.error("按类型统计反馈失败: %s", e)
            rows = []
        for r in rows:
            jt = r["judgment_type"] or "unknown"
            bucket = out.setdefault(jt, {"total": 0, FB_RIGHT: 0, FB_WRONG: 0, FB_DOUBT: 0})
            bucket[r["feedback"]] = bucket.get(r["feedback"], 0) + r["c"]
            bucket["total"] += r["c"]
        for jt, bucket in out.items():
            t = bucket["total"]
            bucket["accuracy"] = round(bucket[FB_RIGHT] / t, 4) if t > 0 else 0.0
        return out


# ------------------------------------------------------------------ #
# 模块级便捷单例（CLI / 脚本直接用，生产库）
# ------------------------------------------------------------------ #
_default_service: Optional[FeedbackService] = None


def get_feedback_service() -> FeedbackService:
    """返回生产库默认 FeedbackService（惰性单例）。"""
    global _default_service
    if _default_service is None:
        _default_service = FeedbackService()
    return _default_service


if __name__ == "__main__":  # 手动自查：python -m services.feedback_service
    import json
    svc = FeedbackService()
    print(json.dumps({"db_path": svc.db_path, "table_ready": True},
                     ensure_ascii=False, indent=2))
