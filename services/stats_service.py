"""
LoveAI v2.0 - 数据统计服务

聚合全应用数据，生成统计报表、趋势分析、使用洞察。
服务层纯 Python 实现，不依赖 PySide6，不使用 pywebview。
数据库操作统一走 Database 实例的 fetch_one / fetch_all，
SQL 参数全部使用 :name 命名格式。

所有公开方法均做防御性处理：查询异常时返回空默认值，绝不向上抛出导致崩溃。
"""
import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.database import Database

logger = logging.getLogger(__name__)


class StatsService:
    """数据统计服务：聚合全应用数据，生成统计报表、趋势分析、使用洞察。

    不继承 QObject，纯数据层服务。所有方法为只读查询（除 get_export_data
    聚合外不写库），可在任意线程中安全调用。
    """

    def __init__(self, db: "Database") -> None:
        """初始化统计服务。

        Args:
            db: Database 实例（需具备 fetch_one / fetch_all / execute 方法）。
        """
        self.db = db
        self._ensure_schema()
        logger.info("StatsService 初始化完成")

    def _ensure_schema(self) -> None:
        """确保 user_medals 表具备 person_id 列。

        database.py 原始 DDL 中该列为 source_person_id；
        MedalService 会迁移出 person_id 列。此处做防御性补列，
        使统计服务在未运行 MedalService 的全新库上也能正确按人统计。
        """
        try:
            self.db.execute("ALTER TABLE user_medals ADD COLUMN person_id TEXT")
        except Exception:
            # 列已存在，忽略
            pass

    # ------------------------------------------------------------------ #
    # 内部工具方法
    # ------------------------------------------------------------------ #
    def _count(self, sql: str, params: Optional[dict] = None) -> int:
        """执行 COUNT 查询并返回整数结果，异常时返回 0。

        Args:
            sql: 查询 SQL，需返回别名为 cnt 的列。
            params: 命名参数字典。

        Returns:
            int: 计数结果，默认 0。
        """
        try:
            row = self.db.fetch_one(sql, params or {})
            val = (row or {}).get("cnt", 0)
            return int(val) if val is not None else 0
        except Exception as e:  # noqa: BLE001 - 防御性兜底
            logger.warning("COUNT 查询失败: %s | sql=%s", e, sql[:100])
            return 0

    def _query_all(self, sql: str, params: Optional[dict] = None) -> list[dict]:
        """执行多行查询，异常时返回空列表。

        Args:
            sql: 查询 SQL。
            params: 命名参数字典。

        Returns:
            list[dict]: 结果行列表。
        """
        try:
            return self.db.fetch_all(sql, params or {})
        except Exception as e:  # noqa: BLE001
            logger.warning("多行查询失败: %s | sql=%s", e, sql[:100])
            return []

    def _query_one(self, sql: str, params: Optional[dict] = None) -> Optional[dict]:
        """执行单行查询，异常时返回 None。

        Args:
            sql: 查询 SQL。
            params: 命名参数字典。

        Returns:
            Optional[dict]: 单行结果，异常或无结果时为 None。
        """
        try:
            return self.db.fetch_one(sql, params or {})
        except Exception as e:  # noqa: BLE001
            logger.warning("单行查询失败: %s | sql=%s", e, sql[:100])
            return None

    @staticmethod
    def _person_where(person_id: Optional[str], alias: str = "") -> tuple[str, dict]:
        """构造按人物过滤的 WHERE 片段。

        Args:
            person_id: 人物 ID，为 None 时不过滤。
            alias: 表别名（如 'm.'），空串表示无前缀。

        Returns:
            tuple[str, dict]: (WHERE 片段或空串, 参数字典)。
        """
        if person_id:
            return f"WHERE {alias}person_id=:pid", {"pid": person_id}
        return "", {}

    # ------------------------------------------------------------------ #
    # 仪表盘核心统计
    # ------------------------------------------------------------------ #
    def get_dashboard_stats(self, person_id: Optional[str] = None) -> dict:
        """获取仪表盘核心统计（首页数据）。

        Args:
            person_id: 人物 ID；为 None 时统计全应用汇总数据。

        Returns:
            dict: 含 message_count / task_completed / task_active /
                  affection_current / affection_trend / medals_unlocked /
                  login_days / analysis_count / response_time_avg。
        """
        result: dict[str, Any] = {
            "message_count": 0,
            "task_completed": 0,
            "task_active": 0,
            "affection_current": 0.0,
            "affection_trend": [],
            "medals_unlocked": 0,
            "login_days": 0,
            "analysis_count": 0,
            "response_time_avg": 0.0,
        }

        try:
            # --- 消息总数 ---
            msg_where, msg_params = self._person_where(person_id)
            result["message_count"] = self._count(
                f"SELECT COUNT(*) AS cnt FROM chat_messages {msg_where}",
                msg_params,
            )

            # --- 任务统计 ---
            task_where, task_params = self._person_where(person_id)
            connector = "AND" if person_id else "WHERE"
            result["task_completed"] = self._count(
                f"SELECT COUNT(*) AS cnt FROM tasks {task_where} "
                f"{connector} status='completed'",
                task_params,
            )
            result["task_active"] = self._count(
                f"SELECT COUNT(*) AS cnt FROM tasks {task_where} "
                f"{connector} status='in_progress'",
                task_params,
            )

            # --- 当前好感度 ---
            if person_id:
                row = self._query_one(
                    "SELECT total_score FROM current_affection WHERE person_id=:pid",
                    {"pid": person_id},
                )
                if row and row.get("total_score") is not None:
                    result["affection_current"] = float(row["total_score"])
            else:
                row = self._query_one(
                    "SELECT COALESCE(SUM(total_score), 0) AS s FROM current_affection"
                )
                if row and row.get("s") is not None:
                    result["affection_current"] = float(row["s"])

            # --- 近7天好感度趋势 ---
            since = (date.today() - timedelta(days=6)).isoformat()
            if person_id:
                rows = self._query_all(
                    "SELECT date, total_score FROM affection_snapshots "
                    "WHERE person_id=:pid AND date>=:since ORDER BY date",
                    {"pid": person_id, "since": since},
                )
                result["affection_trend"] = [
                    {"date": r.get("date", ""), "total": r.get("total_score") or 0.0}
                    for r in rows
                ]
            else:
                rows = self._query_all(
                    "SELECT date, AVG(total_score) AS total_score "
                    "FROM affection_snapshots WHERE date>=:since "
                    "GROUP BY date ORDER BY date",
                    {"since": since},
                )
                result["affection_trend"] = [
                    {"date": r.get("date", ""), "total": r.get("total_score") or 0.0}
                    for r in rows
                ]

            # --- 已解锁勋章数 ---
            result["medals_unlocked"] = self._count(
                "SELECT COUNT(*) AS cnt FROM user_medals"
            )

            # --- 使用天数（按消息日期去重） ---
            if person_id:
                row = self._query_one(
                    "SELECT COUNT(DISTINCT date(created_at)) AS cnt "
                    "FROM chat_messages WHERE person_id=:pid",
                    {"pid": person_id},
                )
            else:
                row = self._query_one(
                    "SELECT COUNT(DISTINCT date(created_at)) AS cnt FROM chat_messages"
                )
            if row and row.get("cnt") is not None:
                result["login_days"] = int(row["cnt"])

            # --- 分析次数 ---
            analysis_where, analysis_params = self._person_where(person_id)
            result["analysis_count"] = self._count(
                f"SELECT COUNT(*) AS cnt FROM analysis_records {analysis_where}",
                analysis_params,
            )

            # --- 平均回复时间（当前表结构无该字段，默认 0.0） ---
            result["response_time_avg"] = 0.0

        except Exception as e:  # noqa: BLE001
            logger.exception("get_dashboard_stats 异常: %s", e)

        return result

    # ------------------------------------------------------------------ #
    # 消息统计
    # ------------------------------------------------------------------ #
    def get_message_stats(self, person_id: str, days: int = 30) -> dict:
        """消息统计（按天/按方向/按时段/按情绪）。

        Args:
            person_id: 人物 ID。
            days: 统计最近多少天，默认 30。

        Returns:
            dict: 含 daily / by_direction / by_hour / by_emotion /
                  total / avg_per_day。
        """
        result: dict[str, Any] = {
            "daily": [],
            "by_direction": {"sent": 0, "received": 0},
            "by_hour": {"凌晨": 0, "上午": 0, "下午": 0, "晚上": 0},
            "by_emotion": {},
            "total": 0,
            "avg_per_day": 0.0,
        }

        try:
            since = (date.today() - timedelta(days=days - 1)).isoformat()
            base_params = {"pid": person_id, "since": since}

            # --- 按天统计（分方向） ---
            rows = self._query_all(
                "SELECT date(created_at) AS d, direction, COUNT(*) AS cnt "
                "FROM chat_messages "
                "WHERE person_id=:pid AND date(created_at)>=:since "
                "GROUP BY d, direction ORDER BY d",
                base_params,
            )
            daily_map: dict[str, dict] = {}
            for r in rows:
                d = r.get("d", "")
                if d not in daily_map:
                    daily_map[d] = {"date": d, "sent": 0, "received": 0}
                if r.get("direction") == "sent":
                    daily_map[d]["sent"] = int(r.get("cnt", 0))
                else:
                    daily_map[d]["received"] = int(r.get("cnt", 0))
            result["daily"] = sorted(daily_map.values(), key=lambda x: x["date"])

            # --- 按方向汇总 ---
            dir_rows = self._query_all(
                "SELECT direction, COUNT(*) AS cnt FROM chat_messages "
                "WHERE person_id=:pid AND date(created_at)>=:since "
                "GROUP BY direction",
                base_params,
            )
            for r in dir_rows:
                d = r.get("direction", "")
                result["by_direction"][d] = int(r.get("cnt", 0))
            # 保证 sent/received 键存在
            result["by_direction"].setdefault("sent", 0)
            result["by_direction"].setdefault("received", 0)

            # --- 按时段统计 ---
            hour_rows = self._query_all(
                "SELECT CASE "
                "  WHEN CAST(strftime('%H', created_at) AS INTEGER) BETWEEN 0 AND 5 THEN '凌晨' "
                "  WHEN CAST(strftime('%H', created_at) AS INTEGER) BETWEEN 6 AND 11 THEN '上午' "
                "  WHEN CAST(strftime('%H', created_at) AS INTEGER) BETWEEN 12 AND 17 THEN '下午' "
                "  ELSE '晚上' END AS period, "
                "COUNT(*) AS cnt FROM chat_messages "
                "WHERE person_id=:pid AND date(created_at)>=:since "
                "GROUP BY period",
                base_params,
            )
            for r in hour_rows:
                result["by_hour"][r.get("period", "其他")] = int(r.get("cnt", 0))

            # --- 按情绪统计 ---
            emo_rows = self._query_all(
                "SELECT emotion, COUNT(*) AS cnt FROM chat_messages "
                "WHERE person_id=:pid AND date(created_at)>=:since "
                "AND emotion IS NOT NULL AND emotion<>'' "
                "GROUP BY emotion ORDER BY cnt DESC",
                base_params,
            )
            result["by_emotion"] = {
                r.get("emotion", "未知"): int(r.get("cnt", 0)) for r in emo_rows
            }

            # --- 总数与日均 ---
            result["total"] = (
                result["by_direction"].get("sent", 0)
                + result["by_direction"].get("received", 0)
            )
            result["avg_per_day"] = round(result["total"] / max(days, 1), 1)

        except Exception as e:  # noqa: BLE001
            logger.exception("get_message_stats 异常: %s", e)

        return result

    # ------------------------------------------------------------------ #
    # 好感度统计与趋势
    # ------------------------------------------------------------------ #
    def get_affection_stats(self, person_id: str, days: int = 30) -> dict:
        """好感度统计与趋势。

        Args:
            person_id: 人物 ID。
            days: 统计最近多少天，默认 30。

        Returns:
            dict: 含 daily / change_7d / change_30d / highest / lowest /
                  current / trend。
        """
        result: dict[str, Any] = {
            "daily": [],
            "change_7d": 0.0,
            "change_30d": 0.0,
            "highest": 0.0,
            "lowest": 0.0,
            "current": 0.0,
            "trend": "stable",
        }

        try:
            since = (date.today() - timedelta(days=days - 1)).isoformat()
            rows = self._query_all(
                "SELECT date, total_score FROM affection_snapshots "
                "WHERE person_id=:pid AND date>=:since "
                "ORDER BY date",
                {"pid": person_id, "since": since},
            )

            daily_list: list[dict] = []
            scores: list[float] = []
            for r in rows:
                score = r.get("total_score")
                if score is None:
                    continue
                score = float(score)
                daily_list.append({"date": r.get("date", ""), "total": score})
                scores.append(score)

            result["daily"] = daily_list

            if scores:
                result["highest"] = max(scores)
                result["lowest"] = min(scores)
                result["current"] = scores[-1]

            # 当前好感度以 current_affection 表为准（更实时）
            cur_row = self._query_one(
                "SELECT total_score FROM current_affection WHERE person_id=:pid",
                {"pid": person_id},
            )
            if cur_row and cur_row.get("total_score") is not None:
                result["current"] = float(cur_row["total_score"])

            # 7天前 / 30天前的快照分数
            def _score_at(offset_days: int) -> Optional[float]:
                target = (date.today() - timedelta(days=offset_days)).isoformat()
                row = self._query_one(
                    "SELECT total_score FROM affection_snapshots "
                    "WHERE person_id=:pid AND date<=:target "
                    "ORDER BY date DESC LIMIT 1",
                    {"pid": person_id, "target": target},
                )
                if row and row.get("total_score") is not None:
                    return float(row["total_score"])
                # 追踪天数不足时回退到窗口内最早快照，避免变化恒为 0
                if scores:
                    return scores[0]
                return None

            score_7d = _score_at(7)
            score_30d = _score_at(days)
            if score_7d is not None:
                result["change_7d"] = round(result["current"] - score_7d, 1)
            if score_30d is not None:
                result["change_30d"] = round(result["current"] - score_30d, 1)

            # 趋势判断：依据近7天变化
            c7 = result["change_7d"]
            if c7 > 2:
                result["trend"] = "up"
            elif c7 < -2:
                result["trend"] = "down"
            else:
                result["trend"] = "stable"

        except Exception as e:  # noqa: BLE001
            logger.exception("get_affection_stats 异常: %s", e)

        return result

    # ------------------------------------------------------------------ #
    # 任务统计
    # ------------------------------------------------------------------ #
    def get_task_stats(self, person_id: Optional[str] = None) -> dict:
        """任务统计。

        Args:
            person_id: 人物 ID；为 None 时统计全部任务。

        Returns:
            dict: 含 by_status / by_type / completion_rate / failed_rate /
                  avg_completion_hours / total。
        """
        result: dict[str, Any] = {
            "by_status": {
                "pending": 0,
                "in_progress": 0,
                "completed": 0,
                "failed": 0,
                "expired": 0,
            },
            "by_type": {},
            "completion_rate": 0.0,
            "failed_rate": 0.0,
            "avg_completion_hours": 0.0,
            "total": 0,
        }

        try:
            where, params = self._person_where(person_id)

            # --- 按状态统计 ---
            status_rows = self._query_all(
                f"SELECT status, COUNT(*) AS cnt FROM tasks {where} GROUP BY status",
                params,
            )
            for r in status_rows:
                st = r.get("status", "pending")
                result["by_status"][st] = int(r.get("cnt", 0))

            # --- 按类型统计 ---
            type_rows = self._query_all(
                f"SELECT task_type, COUNT(*) AS cnt FROM tasks {where} "
                "GROUP BY task_type",
                params,
            )
            result["by_type"] = {
                r.get("task_type", "未分类"): int(r.get("cnt", 0)) for r in type_rows
            }

            # --- 总数 / 完成率 / 失败率 ---
            total = sum(result["by_status"].values())
            result["total"] = total
            completed = result["by_status"].get("completed", 0)
            failed = result["by_status"].get("failed", 0)
            if total > 0:
                result["completion_rate"] = round(completed / total * 100, 1)
                result["failed_rate"] = round(failed / total * 100, 1)

            # --- 平均完成时长（小时）---
            if person_id:
                dur_sql = (
                    "SELECT AVG((julianday(completed_at) - julianday(created_at)) * 24) AS avg_h "
                    "FROM tasks WHERE person_id=:pid AND status='completed' "
                    "AND completed_at IS NOT NULL AND created_at IS NOT NULL"
                )
                dur_params = {"pid": person_id}
            else:
                dur_sql = (
                    "SELECT AVG((julianday(completed_at) - julianday(created_at)) * 24) AS avg_h "
                    "FROM tasks WHERE status='completed' "
                    "AND completed_at IS NOT NULL AND created_at IS NOT NULL"
                )
                dur_params = {}
            dur_row = self._query_one(dur_sql, dur_params)
            if dur_row and dur_row.get("avg_h") is not None:
                result["avg_completion_hours"] = round(float(dur_row["avg_h"]), 1)

        except Exception as e:  # noqa: BLE001
            logger.exception("get_task_stats 异常: %s", e)

        return result

    # ------------------------------------------------------------------ #
    # 应用使用统计
    # ------------------------------------------------------------------ #
    def get_usage_stats(self, days: int = 30) -> dict:
        """应用使用统计。

        Args:
            days: 统计最近多少天，默认 30。

        Returns:
            dict: 含 daily_active / feature_usage / total_active_days /
                  most_used_feature。
        """
        result: dict[str, Any] = {
            "daily_active": [],
            "feature_usage": {},
            "total_active_days": 0,
            "most_used_feature": "",
        }

        try:
            since = (date.today() - timedelta(days=days - 1)).isoformat()

            # --- 每日活跃（以聊天消息日期为活跃依据） ---
            rows = self._query_all(
                "SELECT date(created_at) AS d, COUNT(*) AS msg_cnt "
                "FROM chat_messages WHERE date(created_at)>=:since "
                "GROUP BY d ORDER BY d",
                {"since": since},
            )
            result["daily_active"] = [
                {"date": r.get("d", ""), "count": int(r.get("msg_cnt", 0))}
                for r in rows
            ]
            result["total_active_days"] = len(result["daily_active"])

            # --- 功能使用次数 ---
            feature_counts: dict[str, int] = {
                "分析": self._count(
                    "SELECT COUNT(*) AS cnt FROM analysis_records "
                    "WHERE date(created_at)>=:since",
                    {"since": since},
                ),
                "财务": self._count(
                    "SELECT COUNT(*) AS cnt FROM financial_reports "
                    "WHERE date(created_at)>=:since",
                    {"since": since},
                ),
                "攻略": self._count(
                    "SELECT COUNT(*) AS cnt FROM tutorials "
                    "WHERE date(created_at)>=:since",
                    {"since": since},
                ),
                "分身预测": self._count(
                    "SELECT COUNT(*) AS cnt FROM twin_feedback "
                    "WHERE date(created_at)>=:since",
                    {"since": since},
                ),
                "记忆": self._count(
                    "SELECT COUNT(*) AS cnt FROM memories "
                    "WHERE date(created_at)>=:since",
                    {"since": since},
                ),
                "聊天": self._count(
                    "SELECT COUNT(*) AS cnt FROM chat_messages "
                    "WHERE date(created_at)>=:since",
                    {"since": since},
                ),
            }
            result["feature_usage"] = feature_counts

            # --- 最常用功能 ---
            if feature_counts:
                result["most_used_feature"] = max(
                    feature_counts, key=lambda k: feature_counts[k]
                )

        except Exception as e:  # noqa: BLE001
            logger.exception("get_usage_stats 异常: %s", e)

        return result

    # ------------------------------------------------------------------ #
    # 多对象关系对比
    # ------------------------------------------------------------------ #
    def get_relation_comparison(self) -> list[dict]:
        """多对象关系对比（所有对象的核心指标），按好感度降序。

        Returns:
            list[dict]: 每项含 person_id / nickname / affection_total /
                        task_count / message_count / medal_count /
                        last_interaction。
        """
        result: list[dict] = []
        try:
            rows = self._query_all(
                "SELECT p.id AS person_id, p.nickname, p.created_at, "
                "  (SELECT COALESCE(SUM(ca.total_score), 0) FROM current_affection ca "
                "    WHERE ca.person_id=p.id) AS affection_total, "
                "  (SELECT COUNT(*) FROM tasks t WHERE t.person_id=p.id) AS task_count, "
                "  (SELECT COUNT(*) FROM chat_messages m WHERE m.person_id=p.id) AS message_count, "
                "  (SELECT COUNT(*) FROM user_medals um WHERE um.person_id=p.id) AS medal_count, "
                "  (SELECT MAX(m2.created_at) FROM chat_messages m2 "
                "    WHERE m2.person_id=p.id) AS last_interaction "
                "FROM persons p "
                "ORDER BY affection_total DESC"
            )
            for r in rows:
                result.append({
                    "person_id": r.get("person_id", ""),
                    "nickname": r.get("nickname") or "未命名",
                    "affection_total": float(r.get("affection_total") or 0),
                    "task_count": int(r.get("task_count") or 0),
                    "message_count": int(r.get("message_count") or 0),
                    "medal_count": int(r.get("medal_count") or 0),
                    "last_interaction": r.get("last_interaction"),
                })
        except Exception as e:  # noqa: BLE001
            logger.exception("get_relation_comparison 异常: %s", e)
        return result

    # ------------------------------------------------------------------ #
    # 周报
    # ------------------------------------------------------------------ #
    def get_weekly_report(self, person_id: Optional[str] = None) -> dict:
        """生成周报：聚合本周数据并对比上周，规则生成亮点与建议。

        Args:
            person_id: 人物 ID；为 None 时生成全应用周报。

        Returns:
            dict: 含 week_range / metrics / vs_last_week / highlights /
                  suggestions。
        """
        result: dict[str, Any] = {
            "week_range": "",
            "metrics": {
                "message_count": 0,
                "task_completed": 0,
                "affection_change": 0.0,
                "medals_unlocked": 0,
                "analysis_count": 0,
            },
            "vs_last_week": {
                "message_count": 0,
                "task_completed": 0,
                "analysis_count": 0,
            },
            "highlights": [],
            "suggestions": [],
        }

        try:
            today = date.today()
            monday_this = today - timedelta(days=today.weekday())
            sunday_this = monday_this + timedelta(days=6)
            monday_last = monday_this - timedelta(days=7)
            sunday_last = monday_this - timedelta(days=1)

            result["week_range"] = f"{monday_this.isoformat()} ~ {sunday_this.isoformat()}"

            def _range_params(start: date, end: date) -> dict:
                p = {"start": start.isoformat(), "end": end.isoformat()}
                if person_id:
                    p["pid"] = person_id
                return p

            def _msg_count(start: date, end: date) -> int:
                sql = "SELECT COUNT(*) AS cnt FROM chat_messages WHERE date(created_at)>=:start AND date(created_at)<=:end"
                p = {"start": start.isoformat(), "end": end.isoformat()}
                if person_id:
                    sql += " AND person_id=:pid"
                    p["pid"] = person_id
                return self._count(sql, p)

            def _task_completed(start: date, end: date) -> int:
                sql = "SELECT COUNT(*) AS cnt FROM tasks WHERE status='completed' AND date(completed_at)>=:start AND date(completed_at)<=:end"
                p = {"start": start.isoformat(), "end": end.isoformat()}
                if person_id:
                    sql += " AND person_id=:pid"
                    p["pid"] = person_id
                return self._count(sql, p)

            def _analysis_count(start: date, end: date) -> int:
                sql = "SELECT COUNT(*) AS cnt FROM analysis_records WHERE date(created_at)>=:start AND date(created_at)<=:end"
                p = {"start": start.isoformat(), "end": end.isoformat()}
                if person_id:
                    sql += " AND person_id=:pid"
                    p["pid"] = person_id
                return self._count(sql, p)

            # --- 本周指标 ---
            result["metrics"]["message_count"] = _msg_count(monday_this, sunday_this)
            result["metrics"]["task_completed"] = _task_completed(monday_this, sunday_this)
            result["metrics"]["analysis_count"] = _analysis_count(monday_this, sunday_this)

            # 本周解锁勋章
            medal_sql = "SELECT COUNT(*) AS cnt FROM user_medals WHERE date(unlocked_at)>=:start AND date(unlocked_at)<=:end"
            result["metrics"]["medals_unlocked"] = self._count(
                medal_sql,
                {"start": monday_this.isoformat(), "end": sunday_this.isoformat()},
            )

            # 本周好感度变化
            aff = self.get_affection_stats(person_id, days=7) if person_id else {
                "change_7d": 0.0,
            }
            result["metrics"]["affection_change"] = aff.get("change_7d", 0.0)

            # --- 上周对比 ---
            result["vs_last_week"]["message_count"] = (
                result["metrics"]["message_count"] - _msg_count(monday_last, sunday_last)
            )
            result["vs_last_week"]["task_completed"] = (
                result["metrics"]["task_completed"] - _task_completed(monday_last, sunday_last)
            )
            result["vs_last_week"]["analysis_count"] = (
                result["metrics"]["analysis_count"] - _analysis_count(monday_last, sunday_last)
            )

            # --- 规则生成亮点与建议 ---
            highlights: list[str] = []
            suggestions: list[str] = []

            if result["metrics"]["task_completed"] > 0:
                highlights.append(
                    f"本周完成 {result['metrics']['task_completed']} 个任务，执行力在线"
                )
            if result["metrics"]["medals_unlocked"] > 0:
                highlights.append(
                    f"本周解锁 {result['metrics']['medals_unlocked']} 枚勋章"
                )
            if result["metrics"]["affection_change"] > 2:
                highlights.append(
                    f"好感度本周上升 {result['metrics']['affection_change']:.1f} 分，关系升温"
                )
            if result["metrics"]["message_count"] > 100:
                highlights.append(
                    f"本周互动 {result['metrics']['message_count']} 条，交流活跃"
                )

            if result["metrics"]["task_completed"] == 0:
                suggestions.append("本周暂无任务完成，建议从一个低难度日常任务开始")
            if result["metrics"]["affection_change"] < -2:
                suggestions.append("好感度本周下滑，建议复盘近7天聊天记录寻找原因")
            if result["metrics"]["message_count"] < 20:
                suggestions.append("本周互动偏少，可主动开启一个轻松话题破冰")
            if result["metrics"]["analysis_count"] == 0:
                suggestions.append("本周未使用聊天分析，建议分析对方最近几条消息")
            if not suggestions:
                suggestions.append("保持当前节奏，可尝试推进下一个关系里程碑")

            result["highlights"] = highlights
            result["suggestions"] = suggestions

        except Exception as e:  # noqa: BLE001
            logger.exception("get_weekly_report 异常: %s", e)

        return result

    # ------------------------------------------------------------------ #
    # 导出全部统计数据
    # ------------------------------------------------------------------ #
    def get_export_data(self) -> dict:
        """导出全部统计数据（用于备份）。

        Returns:
            dict: 聚合各统计方法结果的完整字典。
        """
        export: dict[str, Any] = {
            "exported_at": datetime.now().isoformat(),
            "dashboard": {},
            "message_stats": {},
            "affection_stats": {},
            "task_stats": {},
            "usage_stats": {},
            "relation_comparison": [],
            "weekly_report": {},
        }
        try:
            export["dashboard"] = self.get_dashboard_stats()
            export["task_stats"] = self.get_task_stats()
            export["usage_stats"] = self.get_usage_stats(days=30)
            export["relation_comparison"] = self.get_relation_comparison()
            export["weekly_report"] = self.get_weekly_report()

            # 为每个对象导出消息与好感度明细
            persons = self._query_all("SELECT id FROM persons")
            per_person: dict[str, dict] = {}
            for p in persons:
                pid = p.get("id", "")
                if not pid:
                    continue
                per_person[pid] = {
                    "message_stats": self.get_message_stats(pid, days=30),
                    "affection_stats": self.get_affection_stats(pid, days=30),
                }
            export["per_person"] = per_person

        except Exception as e:  # noqa: BLE001
            logger.exception("get_export_data 异常: %s", e)
        return export
