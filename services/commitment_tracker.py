# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 对方履约跟踪模块（CommitmentTracker）

职责：
  1. 从聊天记录中自动提取"承诺"（对方答应的 received / 我方答应的 sent）；
  2. 对一条 pending 承诺，根据后续聊天记录把履约结果分成四类：
       - time_delay  时间耽误（加班/出差/堵车…）→ delayed，可原谅
       - event_delay 有事耽误（生病/家人住院/突发…）→ delayed，可原谅
       - no_reason   无理由不兑现（7天无下文也无解释）→ broken，兑现习惯待引导
       - reneged     明确反悔/翻脸不认（"我什么时候说过"）→ deceived，严重信号
  3. 计算对方（direction=received）的承诺兑现率与可信度等级；
  4. 批量扫描 pending 承诺并自动更新状态（供后台调度调用）。

设计约束：
  - 纯 Python 服务层，不 import PySide6，不继承 QObject；
  - 数据库操作统一走注入的 db（execute / fetch_one / fetch_all），
    SQL 参数全部使用 :name 命名格式；
  - 所有方法带 type hints 与中文 docstring。
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 关键词词表
# --------------------------------------------------------------------------- #
# 承诺触发词（出现即视为"做出承诺"）
PROMISE_KEYWORDS: tuple[str, ...] = (
    "我答应你", "我保证", "下次一定", "到时候", "说好了", "约好",
    "一定来", "没问题", "我会的", "包在我身上", "肯定到", "绝不反悔", "我发誓",
)

# 兑现行为词（后续消息出现 = 对方已兑现）
FULFILL_KEYWORDS: tuple[str, ...] = (
    "约到了", "做好了", "给你买了", "我们去吧", "做到了", "完成了",
    "兑现了", "到了", "买了", "送你", "搞定了", "办好了", "已经弄好了",
)

# 时间耽误理由词（加班/出差/堵车…，属于可原谅延期）
TIME_DELAY_KEYWORDS: tuple[str, ...] = (
    "加班", "出差", "开会", "堵车", "工作忙", "临时加班",
    "项目忙", "赶着", "路上堵", "塞车", "临时有事工作",
)

# 时间耽误通常伴随的"改期/顺延"措辞
TIME_RESCHEDULE_KEYWORDS: tuple[str, ...] = (
    "延期", "推迟", "改天", "下周", "下次", "晚两天", "晚一天",
    "行吗", "行不", "补回来", "补你", "周日", "周一", "周二", "周三",
    "周四", "周五", "周六", "明天", "后天",
)

# 突发事件理由词（生病/家人/意外…，属于可原谅延期）
EVENT_DELAY_KEYWORDS: tuple[str, ...] = (
    "生病", "家人住院", "家里有事", "朋友出事", "意外", "发烧", "感冒",
    "身体不舒服", "住院", "家里人", "突然", "肚子疼", "不舒服", "急诊",
)

# 反悔/否认词（翻脸不认、假装没说过 → 欺骗）
RENEGED_KEYWORDS: tuple[str, ...] = (
    "我什么时候说过", "没这回事", "你记错了", "我可没说", "我没说过",
    "不记得我说过", "我没答应", "哪有这回事", "我可没答应", "你想多了",
    "故意撒谎", "翻脸不认",
)

# 无兑现无解释的宽限天数
NO_REASON_GRACE_DAYS: int = 7

# 可信度等级阈值（有效兑现率 = (fulfilled+delayed)/total，delayed 视为可原谅）
TRUST_HIGH: int = 80
TRUST_MEDIUM: int = 60
TRUST_LOW: int = 40


class CommitmentTracker:
    """对方履约跟踪器：承诺提取 + 履约分类 + 兑现率统计。"""

    def __init__(self, db: Any) -> None:
        """注入 db 并自动建表 commitments。

        Args:
            db: Database 实例，需提供 execute / fetch_one / fetch_all。
        """
        self.db = db
        self._ensure_table()
        logger.info("CommitmentTracker 初始化完成")

    # ------------------------------------------------------------------ #
    # 建表
    # ------------------------------------------------------------------ #
    def _ensure_table(self) -> None:
        """创建 commitments 表（IF NOT EXISTS，幂等）。"""
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS commitments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id TEXT,
                promise_text TEXT,
                promised_at TEXT,
                expected_by TEXT,
                status TEXT,
                fulfillment_type TEXT,
                actual_at TEXT,
                notes TEXT,
                direction TEXT
            )
            """
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_commit_person_status "
            "ON commitments(person_id, status)"
        )

    # ------------------------------------------------------------------ #
    # 工具：消息字段兼容 / 时间解析 / 方向归一
    # ------------------------------------------------------------------ #
    @staticmethod
    def _msg_content(msg: dict) -> str:
        """兼容多种字段名取消息正文。"""
        for k in ("content", "text", "msg", "message"):
            v = msg.get(k)
            if v:
                return str(v)
        return ""

    @staticmethod
    def _msg_time(msg: dict) -> Optional[str]:
        """兼容多种字段名取消息时间字符串。"""
        for k in ("created_at", "timestamp", "time", "datetime"):
            v = msg.get(k)
            if v:
                return str(v)
        return None

    @staticmethod
    def _normalize_direction(msg: dict) -> str:
        """把聊天消息方向归一为 'received'(对方说) / 'sent'(我方说)。

        兼容值：received/in/incoming/to_me/partner/other → received；
                sent/out/outgoing/from_me/user/me → sent。
        """
        raw = str(msg.get("direction", "")).lower()
        if raw in ("received", "in", "incoming", "to_me", "partner", "other"):
            return "received"
        if raw in ("sent", "out", "outgoing", "from_me", "user", "me"):
            return "sent"
        # 兜底：带 sender_role/from 字段
        role = str(msg.get("sender_role", "")).lower()
        if role in ("other", "partner", "received", "in"):
            return "received"
        if role in ("me", "user", "self", "sent", "out"):
            return "sent"
        return "received"

    @staticmethod
    def _parse_dt(s: Optional[str]) -> Optional[datetime]:
        """宽松解析时间字符串，失败返回 None。"""
        if not s:
            return None
        s = str(s).strip().replace("T", " ")
        for fmt in (
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
        ):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None

    # ------------------------------------------------------------------ #
    # ① 承诺提取
    # ------------------------------------------------------------------ #
    def extract_promises(self, messages: list[dict]) -> list[dict]:
        """从聊天记录中提取承诺并写入 commitments 表。

        对方说的消息命中承诺关键词 → direction=received（对方答应的）；
        我方说的消息命中承诺关键词 → direction=sent（我方答应的，用于对比）。

        去重：同一 person_id + promise_text + promised_at 不重复插入。

        Args:
            messages: 聊天记录列表，每条至少含 content/text 与 direction；
                      可选 person_id / created_at。

        Returns:
            list[dict]: 本次新提取并入库的承诺记录。
        """
        new_rows: list[dict] = []
        for msg in messages or []:
            text = self._msg_content(msg)
            if not text:
                continue
            hit = next((kw for kw in PROMISE_KEYWORDS if kw in text), None)
            if not hit:
                continue
            person_id = str(msg.get("person_id", ""))
            direction = self._normalize_direction(msg)
            promised_at = self._msg_time(msg) or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            promise_text = text.strip()

            # 去重
            dup = self.db.fetch_one(
                "SELECT id FROM commitments WHERE person_id=:pid "
                "AND promise_text=:pt AND promised_at=:pa LIMIT 1",
                {"pid": person_id, "pt": promise_text, "pa": promised_at},
            )
            if dup:
                continue

            self.db.execute(
                "INSERT INTO commitments "
                "(person_id, promise_text, promised_at, expected_by, status, "
                " fulfillment_type, actual_at, notes, direction) "
                "VALUES (:pid, :pt, :pa, :eb, :st, :ft, :at, :nt, :dr)",
                {
                    "pid": person_id,
                    "pt": promise_text,
                    "pa": promised_at,
                    "eb": None,
                    "st": "pending",
                    "ft": None,
                    "at": None,
                    "nt": f"命中关键词:{hit}",
                    "dr": direction,
                },
            )
            row = {
                "person_id": person_id,
                "promise_text": promise_text,
                "promised_at": promised_at,
                "direction": direction,
                "status": "pending",
            }
            new_rows.append(row)

        if new_rows:
            logger.info("提取承诺 new=%d total_msgs=%d", len(new_rows), len(messages or []))
        return new_rows

    # ------------------------------------------------------------------ #
    # ② 履约分类
    # ------------------------------------------------------------------ #
    def classify_fulfillment(
        self,
        person_id: str,
        promise_id: int,
        followup_messages: list[dict],
    ) -> dict:
        """对一条 pending 承诺，根据后续聊天记录判定履约情况并更新数据库。

        判定优先级：
          1. 后续出现兑现行为词          → status=fulfilled
          2. 后续出现反悔/否认词          → status=deceived, type=reneged
          3. 后续出现时间耽误理由+改期措辞 → status=delayed, type=time_delay
          4. 后续出现突发事件理由        → status=delayed, type=event_delay
          5. 承诺超过 7 天无兑现无解释    → status=broken,  type=no_reason
          6. 其余                        → 保持 pending（继续观察）

        Args:
            person_id: 人物 ID。
            promise_id: commitments 表主键 id。
            followup_messages: 该承诺之后的聊天记录列表。

        Returns:
            dict: {status, fulfillment_type, notes, actual_at}。
        """
        row = self.db.fetch_one(
            "SELECT * FROM commitments WHERE id=:pid", {"pid": promise_id}
        )
        if not row:
            return {"status": "unknown", "fulfillment_type": None,
                    "notes": "承诺记录不存在", "actual_at": None}

        promised_at = row.get("promised_at")
        promised_dt = self._parse_dt(promised_at)

        # 汇总后续消息文本与时间
        later_texts: list[str] = []
        later_times: list[str] = []
        for m in followup_messages or []:
            t = self._msg_content(m)
            if t:
                later_texts.append(t)
                later_times.append(self._msg_time(m) or "")
        joined = "\n".join(later_texts)

        result: dict[str, Any] = {
            "status": row.get("status", "pending"),
            "fulfillment_type": row.get("fulfillment_type"),
            "notes": row.get("notes"),
            "actual_at": row.get("actual_at"),
        }

        # 1) 兑现行为
        if any(kw in joined for kw in FULFILL_KEYWORDS):
            result.update({
                "status": "fulfilled",
                "fulfillment_type": None,
                "notes": "后续出现兑现行为",
                "actual_at": later_times[0] if later_times else datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        # 2) 明确反悔 / 否认
        elif any(kw in joined for kw in RENEGED_KEYWORDS):
            result.update({
                "status": "deceived",
                "fulfillment_type": "reneged",
                "notes": "对方否认/反悔承诺",
                "actual_at": None,
            })
        # 3) 时间耽误（理由 + 改期措辞同时出现，或仅理由也判延期）
        elif any(kw in joined for kw in TIME_DELAY_KEYWORDS):
            reschedule = any(kw in joined for kw in TIME_RESCHEDULE_KEYWORDS)
            result.update({
                "status": "delayed",
                "fulfillment_type": "time_delay",
                "notes": f"时间耽误(加班/出差等){'且已改期' if reschedule else ''}",
                "actual_at": None,
            })
        # 4) 突发事件耽误
        elif any(kw in joined for kw in EVENT_DELAY_KEYWORDS):
            result.update({
                "status": "delayed",
                "fulfillment_type": "event_delay",
                "notes": "突发事件耽误(生病/家人等)",
                "actual_at": None,
            })
        # 5) 超过 7 天无兑现无解释 → no_reason
        elif promised_dt and (datetime.now() - promised_dt) >= timedelta(days=NO_REASON_GRACE_DAYS):
            result.update({
                "status": "broken",
                "fulfillment_type": "no_reason",
                "notes": f"承诺后超过{NO_REASON_GRACE_DAYS}天无兑现无解释",
                "actual_at": None,
            })
        # 6) 仍在观察期
        else:
            return result

        # 写回数据库
        self.db.execute(
            "UPDATE commitments SET status=:st, fulfillment_type=:ft, "
            "actual_at=:at, notes=:nt WHERE id=:pid",
            {
                "st": result["status"],
                "ft": result["fulfillment_type"],
                "at": result["actual_at"],
                "nt": result["notes"],
                "pid": promise_id,
            },
        )
        logger.info(
            "承诺判定 id=%s status=%s type=%s",
            promise_id, result["status"], result["fulfillment_type"],
        )
        return result

    # ------------------------------------------------------------------ #
    # ③ 兑现率
    # ------------------------------------------------------------------ #
    def get_fulfillment_rate(self, person_id: str, days: int = 90) -> dict:
        """计算对方（direction=received）的承诺兑现率与可信度等级。

        只统计 status != pending 的承诺。有效兑现数 = fulfilled + delayed
        （time_delay / event_delay 属可原谅延期，不计入失信）。

        Args:
            person_id: 人物 ID。
            days: 统计近 N 天的承诺（按 promised_at），默认 90。

        Returns:
            dict: {total, fulfilled, delayed, broken, deceived, rate, trustworthy}。
                  无已决承诺时 rate=100、trustworthy=high（无失信记录，默认信任）。
        """
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self.db.fetch_all(
            "SELECT status, fulfillment_type FROM commitments "
            "WHERE person_id=:pid AND direction='received' "
            "AND status != 'pending' AND promised_at >= :since",
            {"pid": person_id, "since": since},
        )
        total = len(rows)
        fulfilled = sum(1 for r in rows if r.get("status") == "fulfilled")
        delayed = sum(1 for r in rows if r.get("status") == "delayed")
        broken = sum(1 for r in rows if r.get("status") == "broken")
        deceived = sum(1 for r in rows if r.get("status") == "deceived")

        if total == 0:
            return {
                "total": 0, "fulfilled": 0, "delayed": 0,
                "broken": 0, "deceived": 0,
                "rate": 100.0, "trustworthy": "high",
            }

        # 可原谅延期视为"守信但晚到"，计入有效兑现
        rate = round((fulfilled + delayed) / total * 100, 1)

        # 有 deceived 记录一律压到 critical（欺骗不可原谅）
        if deceived > 0:
            trustworthy = "critical"
        elif rate >= TRUST_HIGH:
            trustworthy = "high"
        elif rate >= TRUST_MEDIUM:
            trustworthy = "medium"
        elif rate >= TRUST_LOW:
            trustworthy = "low"
        else:
            trustworthy = "critical"

        return {
            "total": total,
            "fulfilled": fulfilled,
            "delayed": delayed,
            "broken": broken,
            "deceived": deceived,
            "rate": rate,
            "trustworthy": trustworthy,
        }

    # ------------------------------------------------------------------ #
    # ④ 承诺历史
    # ------------------------------------------------------------------ #
    def get_commitment_history(self, person_id: str, limit: int = 20) -> list[dict]:
        """获取某人物的承诺历史，按承诺时间倒序。

        Args:
            person_id: 人物 ID。
            limit: 最多返回条数，默认 20。

        Returns:
            list[dict]: 承诺记录列表。
        """
        return self.db.fetch_all(
            "SELECT * FROM commitments WHERE person_id=:pid "
            "ORDER BY promised_at DESC, id DESC LIMIT :lim",
            {"pid": person_id, "lim": int(limit)},
        )

    # ------------------------------------------------------------------ #
    # ⑤ 批量扫描更新
    # ------------------------------------------------------------------ #
    def scan_and_update(self, person_id: str, recent_messages: list[dict]) -> dict:
        """扫描该人物所有 pending 承诺，用近期聊天记录批量判定履约情况。

        Args:
            person_id: 人物 ID。
            recent_messages: 最近的聊天记录（通常取最近 50 条）。

        Returns:
            dict: {updated, newly_broken, newly_deceived}。
        """
        pendings = self.db.fetch_all(
            "SELECT id FROM commitments WHERE person_id=:pid "
            "AND direction='received' AND status='pending'",
            {"pid": person_id},
        )
        updated = 0
        newly_broken = 0
        newly_deceived = 0
        for p in pendings:
            before = self.db.fetch_one(
                "SELECT status FROM commitments WHERE id=:pid", {"pid": p["id"]}
            )
            res = self.classify_fulfillment(person_id, p["id"], recent_messages or [])
            after = res.get("status")
            if after != (before.get("status") if before else "pending"):
                updated += 1
                if after == "broken":
                    newly_broken += 1
                elif after == "deceived":
                    newly_deceived += 1
        logger.info(
            "scan_and_update person=%s pending=%d updated=%d broken=%d deceived=%d",
            person_id, len(pendings), updated, newly_broken, newly_deceived,
        )
        return {
            "updated": updated,
            "newly_broken": newly_broken,
            "newly_deceived": newly_deceived,
        }
