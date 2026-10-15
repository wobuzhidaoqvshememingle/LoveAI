# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 已读不回 / 不回复场景处理体系（NoReplyDetector）

恋爱中最常见也最折磨人的场景：对方不回消息。本模块负责三件事：
  1. 回复模式基线 analyze_reply_pattern：刻画对方"正常情况下多久回、何时回、
     是否主动、回复长度如何变化"；
  2. 不回复场景识别 detect_no_reply_scenario：基于"我的上一条消息等了多久 vs
     历史平均回复时间"判断当前是否处于不回复场景，并给出 轻度/中度/重度/极端；
  3. 原因分类 classify_reason：把不回复归到 5 种原因之一，并据此给出完全不同的
     应对策略 get_strategy 与任务/时间线 get_tasks。

设计原则（与 DeceptionDetector 一致，修复引导导向）：
  1. 不是有罪推定：默认偏良性。数据不足时明确标注"数据不足"，不瞎猜；
  2. 区分"回复慢"与"不回复"：回复慢 = 最终会回（仍在历史最大回复时间内）；
     不回复 = 超过历史最大回复时间仍未回；
  3. 原因分类是"基于行为模式的推断"，不是确定结论；措辞用"倾向/疑似"；
  4. 不能一概而论：5 种原因的策略、任务、时间线两两不同；
  5. 严重程度客观：基于等待时间 / 历史均值倍数，而不是用户焦虑程度；
  6. 微信已读状态当前不可用（用户尚未配置密钥）：
     识别机制在无已读状态下也能工作——完全基于回复间隔与行为模式推断；
     read_state / online_status 字段保留为 "unknown"，待微信接入后升级。

设计约束：
  - 纯 Python 服务层，不 import PySide6，不继承 QObject；
  - 数据库操作走注入的 db（execute / fetch_one / fetch_all），SQL 用 :name 命名参数；
  - db 可为 None（纯内存分析模式），此时不写库、不抛异常；
  - 所有方法带 type hints 与中文 docstring。
"""
import logging
import statistics
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 词表
# --------------------------------------------------------------------------- #
# 真忙的合理理由（对方消息中出现这些词 = 有客观事由）
BUSY_REASON_WORDS: tuple[str, ...] = (
    "加班", "出差", "开会", "生病", "发烧", "感冒", "住院", "家里有事",
    "临时有事", "有个急事", "客户那边", "项目忙", "赶方案", "在忙",
    "手机没电", "没信号", "信号不好", "在开车", "堵车",
)

# 冲突 / 吵架词（惩罚性沉默与 NPD 冷暴力的前置事件）
CONFLICT_WORDS: tuple[str, ...] = (
    "吵架", "吵", "生气", "分手", "冷战", "算了", "不想理", "烦", "委屈",
    "不可理喻", "你又", "哭", "难过", "失望", "受够了", "滚", "不可调和",
)

# 回避型主动要空间的话（"我需要静一静"类）
NEED_SPACE_WORDS: tuple[str, ...] = (
    "我需要静一静", "让我一个人", "给我点空间", "给我点时间", "我想静静",
    "我需要冷静", "先别联系", "让我缓一缓", "我需要喘口气", "各自冷静",
)

# 疏远信号：回复越来越短后常见的敷衍词
SHORT_Cold_WORDS: tuple[str, ...] = (
    "嗯", "哦", "噢", "额", "呵", "哈哈", "随便", "都行", "还好", "没事",
)

# NPD 冷暴力的"间歇性强化"：偶尔回一条冷淡/居高临下的消息
NPD_INTERMITTENT_WORDS: tuple[str, ...] = (
    "那你想怎样", "你又开始了", "随你吧", "我都说了", "行吧", "哦随便你",
    "你非要这样", "我说的还不清楚吗",
)

# --------------------------------------------------------------------------- #
# 阈值（全部客观，基于数据倍数，不基于焦虑程度）
# --------------------------------------------------------------------------- #
# 计算基线所需的最小历史回复样本数（少于则数据不足）
MIN_LAG_SAMPLES: int = 5

# 当前等待时间 / 历史平均回复时间 的倍数阈值
RATIO_ABNORMAL: float = 2.0    # >2 倍 = 异常
RATIO_SEVERE: float = 5.0      # >5 倍 = 严重
RATIO_EXTREME: float = 10.0    # >10 倍 = 极端
RATIO_LIGHT: float = 1.5       # >1.5 倍才算"轻度不回复"，否则视为正常

# 会话切分间隔（小时）：超过该间隔视为对方新发起一段对话
SESSION_GAP_HOURS: float = 4.0

# 疏远趋势阈值
TREND_SLOW_RATIO: float = 1.5     # 后段平均延迟 > 前段 1.5 倍 = 变慢趋势
TREND_SHORT_RATIO: float = 0.6    # 后段平均长度 < 前段 0.6 倍 = 变短趋势
LOW_INITIATIVE_RATIO: float = 0.2 # 主动发起比例 < 20% = 不主动

# 原因键
REASON_TRUE_BUSY: str = "true_busy"
REASON_LOSING_INTEREST: str = "losing_interest"
REASON_PUNITIVE: str = "punitive_silence"
REASON_AVOIDANT: str = "avoidant_space"
REASON_NPD: str = "npd_coldwar"

REASON_LABELS: dict[str, str] = {
    REASON_TRUE_BUSY: "真忙",
    REASON_LOSING_INTEREST: "没兴趣/在疏远",
    REASON_PUNITIVE: "惩罚性沉默",
    REASON_AVOIDANT: "回避型需要空间",
    REASON_NPD: "NPD冷暴力操控",
}

# 严重程度键
SEV_NONE: str = "无"
SEV_LIGHT: str = "轻度"
SEV_MID: str = "中度"
SEV_HEAVY: str = "重度"
SEV_EXTREME: str = "极端"


class NoReplyDetector:
    """已读不回 / 不回复场景检测器。

    纯证据驱动：默认偏良性，数据不足不瞎猜；5 种原因基于行为模式推断，
    不是确定结论；策略始终修复引导导向。
    """

    def __init__(self, db: Any = None) -> None:
        """注入 db（可选）。

        Args:
            db: Database 实例，需提供 execute / fetch_one / fetch_all；
                为 None 时进入纯内存分析模式（不写库、不抛异常）。
        """
        self.db = db
        logger.info("NoReplyDetector 初始化完成 db=%s",
                    "on" if db else "off(内存)")

    # ------------------------------------------------------------------ #
    # 消息读取与归一化（与 DeceptionDetector 同一套兼容逻辑）
    # ------------------------------------------------------------------ #
    def _load_messages(self, person_id: str, days: int = 90) -> list[dict]:
        """从 chat_messages 表读取某人物最近 days 天的聊天记录（按时间升序）。

        db 为 None 时返回空列表。
        """
        if self.db is None:
            return []
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            rows = self.db.fetch_all(
                "SELECT direction, content, created_at FROM chat_messages "
                "WHERE person_id=:pid AND created_at>=:cut "
                "ORDER BY created_at ASC",
                {"pid": person_id, "cut": cutoff},
            )
            return list(rows or [])
        except Exception as e:  # noqa: BLE001 - 读库失败不影响内存分析
            logger.warning("读取聊天记录失败 person=%s: %s", person_id, e)
            return []

    @staticmethod
    def _content(msg: dict) -> str:
        """兼容多种字段名取消息正文。"""
        for k in ("content", "text", "msg", "message"):
            v = msg.get(k)
            if v:
                return str(v)
        return ""

    @staticmethod
    def _time(msg: dict) -> Optional[str]:
        """兼容多种字段名取消息时间字符串。"""
        for k in ("created_at", "timestamp", "time", "datetime"):
            v = msg.get(k)
            if v:
                return str(v)
        return None

    @staticmethod
    def _direction(msg: dict) -> str:
        """归一消息方向为 received(对方说) / sent(我方说)。"""
        raw = str(msg.get("direction", "")).lower()
        if raw in ("received", "in", "incoming", "to_me", "partner", "other"):
            return "received"
        if raw in ("sent", "out", "outgoing", "from_me", "user", "me"):
            return "sent"
        role = str(msg.get("sender_role", "")).lower()
        if role in ("other", "partner", "received", "in"):
            return "received"
        return "sent"

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
            return datetime.fromisoformat(str(s))
        except ValueError:
            return None

    def _ordered(self, messages: list[dict]) -> list[dict]:
        """按时间升序返回消息列表。"""
        out = list(messages or [])
        out.sort(key=lambda m: self._parse_dt(self._time(m)) or datetime.min)
        return out

    def _reply_lags(self, messages: list[dict]) -> list[float]:
        """计算 我方(sent)->对方(received) 的回复延迟（分钟）。

        只取紧接在 sent 之后的第一条 received 计算间隔。
        """
        seq = self._ordered(messages)
        lags: list[float] = []
        for i in range(1, len(seq)):
            if (self._direction(seq[i]) == "received"
                    and self._direction(seq[i - 1]) == "sent"):
                t0 = self._parse_dt(self._time(seq[i - 1]))
                t1 = self._parse_dt(self._time(seq[i]))
                if t0 and t1 and t1 >= t0:
                    lags.append((t1 - t0).total_seconds() / 60.0)
        return lags

    # ------------------------------------------------------------------ #
    # 1. 回复模式分析
    # ------------------------------------------------------------------ #
    def analyze_reply_pattern(self, person_id: str, recent_days: int = 30) -> dict:
        """分析对方的回复模式（最近 recent_days 天）。

        维度：
          - 回复时间：平均 / 中位数 / 标准差 / 最快 / 最慢（分钟）；
          - 时段分布：白天(6-18)/晚上(18-24)/凌晨(0-6) 回复占比；
          - 主动发起比例：把 >4 小时间隔视为新会话，对方发起的会话占比；
          - 回复长度趋势：窗口前半段 vs 后半段平均长度，是否越来越短；
          - 回复速度趋势：前半段 vs 后半段平均延迟，是否越来越慢；
          - 已读不回次数：当前无已读状态接口，恒为 0 并标注 unavailable。

        Args:
            person_id: 人物 ID。
            recent_days: 统计窗口（天），默认 30。

        Returns:
            dict: 回复模式画像；数据不足时 available=False。
        """
        msgs = self._load_messages(person_id, days=max(recent_days, 30))
        return self._compute_pattern(person_id, msgs, recent_days)

    def _compute_pattern(
        self, person_id: str, messages: list[dict], recent_days: int
    ) -> dict:
        """analyze_reply_pattern 的纯计算内核（便于单测直接传入消息列表）。"""
        seq = self._ordered(messages)
        recv = [m for m in seq if self._direction(m) == "received"]
        if not recv:
            return {"available": False, "reason": "无对方消息，无法分析回复模式",
                    "person_id": person_id}

        lags = self._reply_lags(seq)
        lag_n = len(lags)
        mean_lag = statistics.fmean(lags) if lags else 0.0
        median_lag = statistics.median(lags) if lags else 0.0
        std_lag = statistics.pstdev(lags) if lag_n >= 2 else 0.0
        fastest = min(lags) if lags else 0.0
        slowest = max(lags) if lags else 0.0

        # 时段分布（基于对方消息发送时刻）
        day = night = dawn = 0
        for m in recv:
            t = self._parse_dt(self._time(m))
            if not t:
                continue
            h = t.hour
            if 6 <= h < 18:
                day += 1
            elif 18 <= h < 24:
                night += 1
            else:
                dawn += 1
        valid_times = day + night + dawn
        time_dist = {
            "day_ratio": round(day / valid_times, 3) if valid_times else 0.0,
            "night_ratio": round(night / valid_times, 3) if valid_times else 0.0,
            "dawn_ratio": round(dawn / valid_times, 3) if valid_times else 0.0,
        }

        # 主动发起比例：会话切分（>SESSION_GAP_HOURS 视为新会话）
        proactive_sessions = total_sessions = 0
        last_t: Optional[datetime] = None
        for m in seq:
            t = self._parse_dt(self._time(m))
            if not t:
                continue
            if last_t is None or (t - last_t).total_seconds() / 3600.0 > SESSION_GAP_HOURS:
                # 新会话起点
                total_sessions += 1
                if self._direction(m) == "received":
                    proactive_sessions += 1
            last_t = t
        initiative_ratio = (proactive_sessions / total_sessions) if total_sessions else 0.0

        # 回复长度趋势 & 速度趋势：前半段 vs 后半段
        length_trend = "stable"
        speed_trend = "stable"
        recv_times = [(self._parse_dt(self._time(m)), m)
                       for m in recv if self._parse_dt(self._time(m)) is not None]
        recv_times.sort(key=lambda x: x[0])
        if len(recv_times) >= 6:
            half = len(recv_times) // 2
            first_half = recv_times[:half]
            second_half = recv_times[half:]
            len_first = statistics.fmean(len(self._content(m)) for _, m in first_half) or 1.0
            len_second = statistics.fmean(len(self._content(m)) for _, m in second_half)
            if len_second < len_first * TREND_SHORT_RATIO:
                length_trend = "shorter"
            elif len_second > len_first * (2 - TREND_SHORT_RATIO):
                length_trend = "longer"

        # 速度趋势：按 sent->received 配对的时间先后切前后半
        lag_pairs: list[tuple[datetime, float]] = []
        for i in range(1, len(seq)):
            if (self._direction(seq[i]) == "received"
                    and self._direction(seq[i - 1]) == "sent"):
                t0 = self._parse_dt(self._time(seq[i - 1]))
                t1 = self._parse_dt(self._time(seq[i]))
                if t0 and t1 and t1 >= t0:
                    lag_pairs.append((t0, (t1 - t0).total_seconds() / 60.0))
        lag_pairs.sort(key=lambda x: x[0])
        if len(lag_pairs) >= 6:
            half = len(lag_pairs) // 2
            spd_first = statistics.fmean(v for _, v in lag_pairs[:half]) or 1.0
            spd_second = statistics.fmean(v for _, v in lag_pairs[half:])
            if spd_second > spd_first * TREND_SLOW_RATIO:
                speed_trend = "slower"
            elif spd_second < spd_first / TREND_SLOW_RATIO:
                speed_trend = "faster"

        available = lag_n >= MIN_LAG_SAMPLES
        return {
            "available": available,
            "person_id": person_id,
            "window_days": recent_days,
            "reply_lag": {
                "mean_min": round(mean_lag, 2),
                "median_min": round(median_lag, 2),
                "std_min": round(std_lag, 2),
                "fastest_min": round(fastest, 2),
                "slowest_min": round(slowest, 2),
                "sample_count": lag_n,
            },
            "time_distribution": time_dist,
            "proactive_initiative_ratio": round(initiative_ratio, 3),
            "proactive_sessions": proactive_sessions,
            "total_sessions": total_sessions,
            "reply_length_trend": length_trend,   # shorter/stable/longer
            "reply_speed_trend": speed_trend,     # slower/stable/faster
            "unread_no_reply_count": 0,
            "read_state_available": False,       # 微信已读状态未接入
            "note": ("已读状态当前不可用，主动/被动与回复快慢均基于聊天记录时间推断"
                     if available else "历史回复样本不足，无法建立可靠回复基线"),
        }

    # ------------------------------------------------------------------ #
    # 2. 不回复场景识别
    # ------------------------------------------------------------------ #
    def detect_no_reply_scenario(
        self, person_id: str, current_time: Optional[datetime] = None
    ) -> dict:
        """检测当前是否处于不回复场景。

        判断逻辑：
          1. 取最近一条消息。若最后一条是"我发的(sent)"且之后对方没回 → 正在等待；
             若最后一条是对方回的(received) → 不在等待中（对方刚说过话）。
          2. waiting_hours = 对方还没回的那条消息发出后到现在的小时数。
          3. ratio = waiting_hours*60 / 历史平均回复时间(分钟)。
          4. 区分"回复慢"与"不回复"：
               - waiting 仍在历史最大回复时间内 → 回复慢（最终会回）；
               - 超过历史最大回复时间仍未回 → 真·不回复。

        严重程度（基于 ratio，客观）：
          - 无：ratio<1.5 或对方刚说过话；
          - 轻度：1.5≤ratio<2；
          - 中度：2≤ratio<5；
          - 重度：5≤ratio<10；
          - 极端：ratio≥10。

        Args:
            person_id: 人物 ID。
            current_time: 指定"当前时间"（便于测试复现）；默认 now。

        Returns:
            dict: 见字段说明。
        """
        now = current_time or datetime.now()
        msgs = self._load_messages(person_id, days=90)
        return self._compute_scenario(person_id, msgs, now)

    def _compute_scenario(
        self, person_id: str, messages: list[dict], now: datetime
    ) -> dict:
        """detect_no_reply_scenario 的纯计算内核。"""
        seq = self._ordered(messages)
        if not seq:
            return {"is_no_reply": False, "waiting_on_partner": False,
                    "note": "无聊天记录", "person_id": person_id}

        # 历史基线
        lags = self._reply_lags(seq)
        mean_lag = statistics.fmean(lags) if lags else 0.0
        max_lag = max(lags) if lags else 0.0

        # 最后一条消息
        last_msg = seq[-1]
        waiting_on_partner = self._direction(last_msg) == "sent"

        # 对方最后一条 received 的时间
        recv_times = [self._parse_dt(self._time(m)) for m in seq
                      if self._direction(m) == "received"]
        recv_times = [t for t in recv_times if t is not None]
        last_recv = max(recv_times) if recv_times else None

        hours_since_reply = ((now - last_recv).total_seconds() / 3600.0
                             if last_recv else None)

        # 等待时长：最后一条 sent 到现在（若最后一条是 received，则不等待）
        waiting_hours = 0.0
        if waiting_on_partner:
            t_last_sent = self._parse_dt(self._time(last_msg))
            if t_last_sent:
                waiting_hours = max(0.0, (now - t_last_sent).total_seconds() / 3600.0)

        # 倍数
        ratio = (waiting_hours * 60.0 / mean_lag) if mean_lag > 0 else 0.0
        # 超过历史最大回复时间仍未回 = 真·不回复（不是回复慢）
        beyond_max = (waiting_hours * 60.0 > max_lag) if (waiting_on_partner and max_lag > 0) else False

        severity = self._severity_of(ratio, waiting_on_partner, mean_lag)
        is_no_reply = waiting_on_partner and severity != SEV_NONE

        if not waiting_on_partner:
            note = "最后一条消息来自对方，当前不在等待回复中"
        elif severity == SEV_NONE:
            note = (f"已等待 {waiting_hours:.1f} 小时，约为历史平均回复时间"
                    f"（{mean_lag:.0f}分钟）的 {ratio:.1f} 倍，仍在正常范围内")
        else:
            kind = "真·不回复（超过历史最大回复时间）" if beyond_max else "回复慢（仍在历史最大回复时间内，可能会补回）"
            note = (f"已等待 {waiting_hours:.1f} 小时，约为历史平均回复时间"
                    f"（{mean_lag:.0f}分钟）的 {ratio:.1f} 倍，判为{severity}不回复场景；{kind}")

        return {
            "person_id": person_id,
            "is_no_reply": is_no_reply,
            "waiting_on_partner": waiting_on_partner,
            "waiting_hours": round(waiting_hours, 2),
            "hours_since_partner_reply": (round(hours_since_reply, 2)
                                          if hours_since_reply is not None else None),
            "hist_mean_lag_min": round(mean_lag, 2),
            "hist_max_lag_min": round(max_lag, 2),
            "ratio_to_hist_mean": round(ratio, 2),
            "is_beyond_max": beyond_max,
            "severity": severity,
            "read_state": "unknown",      # 微信已读状态未接入
            "online_status": "unknown",   # 在线状态未接入
            "note": note,
        }

    @staticmethod
    def _severity_of(ratio: float, waiting_on_partner: bool, mean_lag: float) -> str:
        """按等待倍数给出严重程度。mean_lag 为 0（无历史）时保守判轻度。"""
        if not waiting_on_partner:
            return SEV_NONE
        if mean_lag <= 0:
            # 无基线数据：无法用倍数，保守判轻度（不瞎猜）
            return SEV_LIGHT
        if ratio >= RATIO_EXTREME:
            return SEV_EXTREME
        if ratio >= RATIO_SEVERE:
            return SEV_HEAVY
        if ratio >= RATIO_ABNORMAL:
            return SEV_MID
        if ratio >= RATIO_LIGHT:
            return SEV_LIGHT
        return SEV_NONE

    # ------------------------------------------------------------------ #
    # 3. 原因分类（5 种）
    # ------------------------------------------------------------------ #
    def classify_reason(
        self, person_id: str, context_messages: Optional[list[dict]] = None
    ) -> dict:
        """把当前不回复归到 5 种原因之一。

        5 种原因（基于行为模式推断，非确定结论）：
          - true_busy 真忙：平时回复正常、近期有合理事由、无疏远趋势；
          - losing_interest 没兴趣/在疏远：回复越来越慢+越来越短+不主动发起；
          - punitive_silence 惩罚性沉默：冲突后单次故意不回，让你焦虑；
          - avoidant_space 回避型需要空间：对方说"我需要静一静"，过几天会回来；
          - npd_coldwar NPD冷暴力操控：用沉默控制你、间歇性冷淡消息、重复出现。

        Args:
            person_id: 人物 ID。
            context_messages: 可选，直接传入消息列表（便于测试）；
                              不传则从 db 读取。

        Returns:
            dict: {reason, reason_label, confidence, signals, explanation}。
        """
        if context_messages is not None:
            msgs = context_messages
            pattern = self._compute_pattern(person_id, msgs, recent_days=30)
            scenario = self._compute_scenario(person_id, msgs, datetime.now())
        else:
            msgs = self._load_messages(person_id, days=90)
            pattern = self._compute_pattern(person_id, msgs, recent_days=30)
            scenario = self._compute_scenario(person_id, msgs, datetime.now())

        return self._reason_from_signals(pattern, scenario, msgs)

    def _reason_from_signals(
        self, pattern: dict, scenario: dict, msgs: list[dict]
    ) -> dict:
        """由回复模式 + 场景 + 消息内容推断原因。"""
        recv = [m for m in self._ordered(msgs) if self._direction(m) == "received"]
        # 只看最近 10 条对方消息做语境判断（近期语境）
        recent_recv = recv[-10:] if recv else []
        joined_recent = "\n".join(self._content(m) for m in recent_recv)
        joined_all = "\n".join(self._content(m) for m in recv)

        # --- 信号提取 ---
        has_busy_reason = any(w in joined_recent for w in BUSY_REASON_WORDS)
        has_conflict_recent = any(w in joined_recent for w in CONFLICT_WORDS)
        needs_space = any(w in joined_recent for w in NEED_SPACE_WORDS)
        slow_trend = pattern.get("reply_speed_trend") == "slower"
        short_trend = pattern.get("reply_length_trend") == "shorter"
        low_initiative = pattern.get("proactive_initiative_ratio", 1.0) < LOW_INITIATIVE_RATIO
        intermittent_cold = any(w in joined_recent for w in NPD_INTERMITTENT_WORDS)
        # 重复冷暴力历史：历史上出现过 >=2 次"冲突词之后对方长时间沉默"的近似事件
        repeat_coldwar = self._count_conflict_silence_episodes(recv) >= 2

        signals = {
            "has_busy_reason": has_busy_reason,
            "has_conflict_recent": has_conflict_recent,
            "needs_space_words": needs_space,
            "speed_slower": slow_trend,
            "length_shorter": short_trend,
            "low_initiative": low_initiative,
            "intermittent_cold_reply": intermittent_cold,
            "repeat_coldwar_history": repeat_coldwar,
        }

        # --- 决策树（顺序敏感）---
        reason = REASON_TRUE_BUSY
        confidence = 0.5
        explanation = "默认偏良性：先按真忙处理，等对方补回"

        # ① 回避型需要空间：对方明确说要静一静 / 给空间
        if needs_space:
            reason = REASON_AVOIDANT
            confidence = 0.8
            explanation = ("对方近期表达了'需要静一静/给我空间'，这更像是其自身情绪调节"
                           "方式，而非针对你的惩罚；通常 3-7 天会主动回来")
        # ② 冲突后沉默：区分单次惩罚性 vs 重复 NPD 冷暴力
        elif has_conflict_recent:
            if repeat_coldwar or intermittent_cold:
                reason = REASON_NPD
                confidence = 0.75
                explanation = ("冲突后不回，且历史上多次出现'冲突后沉默'、偶尔回一条冷淡消息"
                               "（间歇性强化），疑似用沉默作为控制手段让你焦虑/道歉")
            else:
                reason = REASON_PUNITIVE
                confidence = 0.7
                explanation = ("发生在吵架/冲突之后、平时回复正常，更像是对方用沉默"
                               "表达不满/让你焦虑的单次惩罚性沉默")
        # ③ 疏远趋势：越来越慢 + 越来越短 + 不主动
        elif slow_trend and short_trend and low_initiative:
            reason = REASON_LOSING_INTEREST
            confidence = 0.7
            explanation = ("回复速度趋势变慢、长度趋势变短、主动发起比例低，"
                           "呈现持续性的疏远信号，需要观察是否降温")
        # ④ 有合理事由且无疏远趋势 → 真忙
        elif has_busy_reason and not (slow_trend or short_trend):
            reason = REASON_TRUE_BUSY
            confidence = 0.8
            explanation = ("对方近期提到加班/出差/家事等客观事由，且无变慢/变短趋势，"
                           "大概率是真忙，忙完会补回")

        return {
            "reason": reason,
            "reason_label": REASON_LABELS.get(reason, reason),
            "confidence": confidence,
            "signals": signals,
            "explanation": explanation,
        }

    def _count_conflict_silence_episodes(self, recv: list[dict]) -> int:
        """统计历史上"出现冲突词的对方消息之后，长时间无回复"的次数。

        用于区分单次惩罚性沉默 vs 重复出现的 NPD 冷暴力模式。
        粗略实现：对方含冲突词的消息条数（>=2 即视为重复模式倾向）。
        """
        count = 0
        for m in recv:
            text = self._content(m)
            if any(w in text for w in CONFLICT_WORDS):
                count += 1
        return count

    # ------------------------------------------------------------------ #
    # 4. 应对策略（按原因 + 严重程度）
    # ------------------------------------------------------------------ #
    def get_strategy(
        self, reason: str, severity: str, persona: Optional[dict] = None
    ) -> dict:
        """根据原因与严重程度给出应对策略（5 种原因两两不同）。

        严重程度调节策略强度：
          - 轻度=观察；中度=行动；重度=设定边界；极端=评估关系。

        Args:
            reason: classify_reason 返回的原因键。
            severity: detect 返回的严重程度（轻度/中度/重度/极端）。
            persona: 对方画像（可选）；用于对回避型/NPD倾向做轻微适配。

        Returns:
            dict: {reason, recommended, forbidden, explanation}。
        """
        persona = persona or {}
        base = self._STRATEGY_TABLE.get(reason, self._STRATEGY_TABLE[REASON_TRUE_BUSY])
        recommended: list[str] = list(base["recommended"])
        forbidden: list[str] = list(base["forbidden"])
        explanation = base["explanation"]

        # 严重程度调节强度
        sev_note = self._severity_tip(reason, severity)
        if sev_note:
            explanation += f"（当前{severity}：{sev_note}）"

        # 画像轻微适配：若已知对方是回避型，空间类策略优先级提高
        # （attachment 可能是 dict 也可能是字符串，做防御性取值）
        att_raw = persona.get("attachment") if isinstance(persona, dict) else None
        if isinstance(att_raw, dict):
            att = str(att_raw.get("primary_type", ""))
        else:
            att = str(att_raw or "")
        if "回避" in att and reason in (REASON_AVOIDANT, REASON_PUNITIVE):
            recommended = ["给足空间、低压等待"] + recommended

        return {
            "reason": reason,
            "reason_label": REASON_LABELS.get(reason, reason),
            "severity": severity,
            "recommended": recommended,
            "forbidden": forbidden,
            "explanation": explanation,
        }

    # 5 种原因的策略表（两两不同；修复引导导向）
    _STRATEGY_TABLE: dict[str, dict[str, Any]] = {
        REASON_TRUE_BUSY: {
            "recommended": [
                "不催促，安心等对方忙完主动补回",
                "对方回复时表达理解：'忙完了？辛苦了'",
                "对方补回时不抱怨（不说'你终于回了/我还以为你失踪了'）",
                "保持自己的生活节奏，不把注意力全挂在等消息上",
            ],
            "forbidden": [
                "连环发消息催促",
                "抱怨/质问'你怎么才回'",
                "因为对方没回就闹情绪",
            ],
            "explanation": ("真忙场景：对方平时回复正常、近期有客观事由。"
                           "正确做法是不催促、表达理解、保持自己节奏，对方忙完会补回。"),
        },
        REASON_LOSING_INTEREST: {
            "recommended": [
                "停止过度投入，把主动发消息频率降到每周 1-2 次",
                "观察对方是否会主动找你（给 1-2 周观察期）",
                "专注自己的生活和成长，保持自身价值感",
                "观察期后再评估是否继续，不追问'你是不是不爱我了'",
            ],
            "forbidden": [
                "追问'你是不是不爱我了/你怎么变了'",
                "加倍发消息讨好",
                "因为焦虑而频繁联系",
            ],
            "explanation": ("疏远场景：回复越来越慢、越来越短、不主动。"
                           "过度投入会加速疏远；正确做法是降低主动频率、观察对方是否主动、"
                           "保持自身价值，观察期后再评估关系走向。"),
        },
        REASON_PUNITIVE: {
            "recommended": [
                "不追着道歉，等对方先开口",
                "保持自己的生活节奏，不被焦虑带走",
                "对方主动找你时温和沟通，但不立刻全盘原谅",
                "记录对方用沉默惩罚你的频率和场景",
            ],
            "forbidden": [
                "追着道歉/卑微求和",
                "连环发小作文解释",
                "反过来用冷战对攻",
            ],
            "explanation": ("惩罚性沉默：对方用不回消息让你焦虑。"
                           "追着道歉会让对方觉得'沉默'这招有效、下次还会用；"
                           "正确做法是稳住自己、等对方先开口，温和沟通并设定边界。"),
        },
        REASON_AVOIDANT: {
            "recommended": [
                "发一条低压关心消息后不再追：'知道你需要空间，我在，准备好了随时找我'",
                "给对方 3-7 天空间，不追问、不催、不发长消息",
                "利用这段时间做自己的事，保持情绪稳定",
                "对方回来后先恢复连接，再择机谈问题",
            ],
            "forbidden": [
                "追问'你到底想怎样/为什么不理我'",
                "轰炸式连发消息",
                "对方一回来就立刻翻旧账谈冲突",
            ],
            "explanation": ("回避型需要空间：这是对方的情绪调节方式，不是惩罚你。"
                           "正确做法是给空间但不切断连接——一条低压关心后安静等待，"
                           "回来后先恢复连接再谈问题。"),
        },
        REASON_NPD: {
            "recommended": [
                "识别这是沉默操控，不是你的错，不被它牵着走",
                "保持自己的生活节奏，不道歉、不求和、不发长消息",
                "记录每次冷暴力的触发点与持续时间（看清操控模式）",
                "对方恢复沟通后，温和而坚定地表达'我不接受长期冷暴力'",
                "若持续超过 2 周，认真评估这段关系是否健康",
            ],
            "forbidden": [
                "焦虑地道歉/让步求和",
                "发长消息反复解释",
                "因为对方偶尔回一条就立刻全盘妥协",
                "用冷战反制（只会变成互相折磨）",
            ],
            "explanation": ("NPD冷暴力操控：用沉默控制你、等你让步，间歇性回一条冷淡消息"
                           "（间歇性强化）。正确做法是识别操控、稳住自己、守住边界，"
                           "记录模式并评估关系健康度，而不是被情绪带着道歉。"),
        },
    }

    @staticmethod
    def _severity_tip(reason: str, severity: str) -> str:
        """按严重程度给出策略强度调节说明。"""
        table = {
            SEV_LIGHT: "先观察，不急于行动",
            SEV_MID: "开始主动调整行为（降频率/给空间）",
            SEV_HEAVY: "明确设定边界，记录模式",
            SEV_EXTREME: "认真评估关系健康度与去留",
        }
        return table.get(severity, "")

    # ------------------------------------------------------------------ #
    # 5. 任务与时间线
    # ------------------------------------------------------------------ #
    def get_tasks(
        self, reason: str, severity: str, days_since_reply: int
    ) -> list[dict]:
        """根据原因与严重程度生成对应任务与时间线（5 种原因两两不同）。

        Args:
            reason: 原因键。
            severity: 严重程度。
            days_since_reply: 距离上次对方回复的天数（用于时间线）。

        Returns:
            list[dict]: 每项 {title, action, timeline, wait_days}。
        """
        builder = self._TASK_BUILDERS.get(reason, self._TASK_BUILDERS[REASON_TRUE_BUSY])
        tasks = builder(days_since_reply, severity)
        # 严重程度对极端/NPD 类追加"评估关系"任务
        if severity == SEV_EXTREME and reason in (REASON_NPD, REASON_LOSING_INTEREST):
            tasks.append({
                "title": "评估这段关系是否还值得继续",
                "action": "结合对方长期行为模式，客观评估关系健康度，不为沉没成本继续内耗",
                "timeline": "现在即可冷静评估，不必立刻做决定",
                "wait_days": 0,
            })
        return tasks

    # 任务构造器注册表（reason -> 构造函数），类加载后在模块尾部登记
    _TASK_BUILDERS: dict[str, Any] = {}

    @staticmethod
    def _tasks_true_busy(days: int, severity: str) -> list[dict]:
        return [
            {
                "title": "专注自己的事，等对方主动回复",
                "action": "不主动发第二条催促消息，正常过自己的生活，对方忙完会补回",
                "timeline": "对方回复前不主动追加；对方回复后表达理解即可",
                "wait_days": 0,
            },
            {
                "title": "对方回复时给台阶、不抱怨",
                "action": "回复'忙完了？辛苦了'，不说'你终于回了/我还以为你失踪了'",
                "timeline": "对方一旦回复立即执行",
                "wait_days": 0,
            },
        ]

    @staticmethod
    def _tasks_losing_interest(days: int, severity: str) -> list[dict]:
        return [
            {
                "title": "把主动联系降到每周 1-2 次，观察对方是否主动",
                "action": "停止高频主动发消息，观察低压下对方会不会主动找你",
                "timeline": "观察期 2 周，期间不追问'你是不是不爱我了'",
                "wait_days": 14,
            },
            {
                "title": "专注自己的生活和成长",
                "action": "把注意力从'等他回消息'转到工作、朋友、爱好上，保持自身价值",
                "timeline": "观察期内持续进行",
                "wait_days": 14,
            },
            {
                "title": "2 周后评估是否继续",
                "action": "若 2 周后对方仍不主动、依旧冷淡，重新评估这段关系的投入是否值得",
                "timeline": "第 14 天做一次评估",
                "wait_days": 14,
            },
        ]

    @staticmethod
    def _tasks_punitive(days: int, severity: str) -> list[dict]:
        return [
            {
                "title": "不追着道歉，等对方先开口",
                "action": "稳住自己的生活节奏，不为焦虑而卑微求和，观察对方是否在乎这段关系",
                "timeline": "等待期 3-7 天，期间不主动道歉",
                "wait_days": 7,
            },
            {
                "title": "记录对方用沉默惩罚你的频率和场景",
                "action": "记下每次冲突后对方沉默的时长与触发点，识别是否成为固定模式",
                "timeline": "持续记录，7 天后回看",
                "wait_days": 7,
            },
            {
                "title": "对方主动后温和沟通，但不立刻全盘原谅",
                "action": "对方回来时先恢复连接，再温和表达'我不接受用沉默作为惩罚方式'",
                "timeline": "对方主动联系后执行",
                "wait_days": 0,
            },
        ]

    @staticmethod
    def _tasks_avoidant(days: int, severity: str) -> list[dict]:
        return [
            {
                "title": "发一条低压关心消息，然后给空间",
                "action": "发'知道你需要空间，我在，你准备好了随时找我'，之后不再追发",
                "timeline": "现在发一条，然后给 3-7 天空间",
                "wait_days": 7,
            },
            {
                "title": "利用这段时间做自己的事，保持情绪稳定",
                "action": "不追问、不催促、不发长消息，安心过自己的生活",
                "timeline": "空间期 3-7 天内",
                "wait_days": 7,
            },
            {
                "title": "对方回来后先恢复连接，再谈问题",
                "action": "对方主动回来时先聊轻松的，不要一上来就翻冲突旧账",
                "timeline": "对方回来后先恢复 1-2 天连接，再择机谈",
                "wait_days": 0,
            },
        ]

    @staticmethod
    def _tasks_npd(days: int, severity: str) -> list[dict]:
        return [
            {
                "title": "识别冷暴力操控模式，记录每次触发与持续时间",
                "action": "记下每次沉默的触发点、时长、以及你被唤起的焦虑，用事实看清模式",
                "timeline": "持续记录",
                "wait_days": 0,
            },
            {
                "title": "保持自己的节奏，不道歉、不求和",
                "action": "不被沉默操控情绪，不发长消息、不轰炸、不因为偶尔一条冷淡消息就妥协",
                "timeline": "观察期 2 周内持续",
                "wait_days": 14,
            },
            {
                "title": "对方恢复沟通后明确表达不接受冷暴力",
                "action": "在双方平静时温和而坚定地说'长期不回消息让我很难受，我希望有问题能直接沟通'",
                "timeline": "对方恢复沟通后执行",
                "wait_days": 0,
            },
            {
                "title": "2 周后评估关系健康度",
                "action": "若冷暴力反复出现且不改善，认真评估这段关系是否健康、是否值得继续",
                "timeline": "第 14 天做评估",
                "wait_days": 14,
            },
        ]


# 静态方法注册（类体内 staticmethod 无法在类定义期直接取函数对象，模块加载后补登记）
NoReplyDetector._TASK_BUILDERS = {
    REASON_TRUE_BUSY: NoReplyDetector._tasks_true_busy,
    REASON_LOSING_INTEREST: NoReplyDetector._tasks_losing_interest,
    REASON_PUNITIVE: NoReplyDetector._tasks_punitive,
    REASON_AVOIDANT: NoReplyDetector._tasks_avoidant,
    REASON_NPD: NoReplyDetector._tasks_npd,
}
