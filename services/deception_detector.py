# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 反伪装审计系统（DeceptionDetector / 行为一致性审计）

设计原则（必须遵守）：
  1. 不是有罪推定：大多数人不是伪装者，系统基于证据、不基于猜测。
     只有出现足够多的不一致信号时才提示风险，不默认所有人都在伪装。
  2. 数据不足时不瞎猜：行为基线需要至少 3 个月数据，不足则明确标注
     "数据不足，无法评估伪装风险"，绝不输出任何评分或定性结论。
  3. 输出谨慎：用"多个不一致信号，建议进一步观察"而不是"对方在骗你"。
     风险等级只用 低/中/高/极高，绝不使用"骗子"等定性标签。
  4. 区分"偶然失误"与"伪装破裂"：
       - 偶然失误 = 非伪装者一时疏忽，事后有歉意/修正、不重复；
       - 伪装破裂 = 伪装者疏忽，事后试图掩盖/否认/转移话题，且类似事件重复出现。

职责：
  1. 行为基线建立 build_baseline：基于 ≥3 个月数据刻画一个人的"正常行为范围"；
  2. 异常偏离检测 detect_anomalies：识别突然变好/突然变差/场景不一致，
     并区分"偶然偏离"与"模式转变"；
  3. 伪装风险评估 assess_risk：五维加权算出 0-100 风险分与等级；
  4. 露馅事件识别 detect_slip_events：识别酒后/争吵/背后评价/利益反应四类
     不经意露馅，并区分偶然失误与伪装破裂；
  5. 时间维度信任累积 calculate_temporal_trust：按短/中/长期加权信任，
     并检测"目的达成后行为突变"；
  6. 综合分析 analyze：主入口，串起以上五步输出完整报告。

设计约束：
  - 纯 Python 服务层，不 import PySide6，不继承 QObject；
  - 数据库操作统一走注入的 db（execute / fetch_one / fetch_all），
    SQL 参数全部使用 :name 命名格式；
  - db 可为 None（纯内存分析模式），此时不写库、不抛异常；
  - 所有方法带 type hints 与中文 docstring。
"""
import json
import logging
import statistics
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 词表
# --------------------------------------------------------------------------- #
# 积极情感词
POSITIVE_EMO_WORDS: tuple[str, ...] = (
    "想你", "爱你", "喜欢", "宝贝", "开心", "在乎", "心疼", "思念", "亲亲",
    "抱抱", "乖乖", "么么", "幸福", "暖心", "可爱", "优秀", "想见你", "爱死",
    "好喜欢你", "离不开你", "你最好了",
)

# 消极情感词
NEGATIVE_EMO_WORDS: tuple[str, ...] = (
    "烦", "生气", "难过", "委屈", "失望", "不想理", "冷漠", "呵呵", "心寒",
    "不开心", "郁闷", "烦你", "讨厌", "受够了", "心累",
)

# 冲突语境词
CONFLICT_WORDS: tuple[str, ...] = (
    "吵", "生气", "分手", "烦", "冷战", "不想理", "吵架", "哭", "委屈",
    "滚开", "不可理喻", "你又",
)

# 利益/金钱关键词（用于金钱行为模式 & 重大利益节点识别）
MONEY_KEYWORDS: tuple[str, ...] = (
    "借钱", "借我", "转我", "转账", "多少钱", "我请", "AA", "买单", "红包",
    "工资", "费用", "花了", "还钱", "先付", "垫一下", "报销",
)

# 重大利益节点关键词（好行为若发生在这些节点前，动机存疑）
INTEREST_NODE_KEYWORDS: tuple[str, ...] = (
    "借钱", "借我钱", "转我", "帮我个忙", "求你", "陪我", "见家长",
    "确认关系", "在一起", "领证", "结婚", "借点", "周转一下", "担保",
)

# 深夜/酒后/疲劳状态词（防御降低，言论更真实）
LATE_NIGHT_STATE_WORDS: tuple[str, ...] = (
    "喝多了", "喝醉了", "微醺", "喝了点", "好累", "撑不住", "好困",
    "加班到", "熬夜", "头好晕", "醉了",
)

# 第三方负面评价词（背后说的话往往更真实）
THIRD_PARTY_NEGATIVE_WORDS: tuple[str, ...] = (
    "前任", "闺蜜", "同事", "我朋友", "他妈", "贱人", "傻逼", "小气",
    "不靠谱", "渣", "白眼狼", "势利", "假", "心机",
)

# 争吵中与"温和人设"不符的狠话
HARSH_OUT_OF_CHARACTER_WORDS: tuple[str, ...] = (
    "滚", "去死", "懒得理你", "你真烦", "不可理喻", "有病", "废物",
    "懒得说", "随便你吧",
)

# 事后歉意/修正词（= 偶然失误的证据）
APOLOGY_CORRECTION_WORDS: tuple[str, ...] = (
    "对不起我刚才", "我不是那个意思", "说错话了", "抱歉", "我刚才失态",
    "我收回", "别往心里去", "我口误", "我不是故意", "不好意思我",
)

# 事后掩盖/否认/转移话题词（= 伪装破裂的证据）
DEFLECTION_WORDS: tuple[str, ...] = (
    "我什么时候说过", "你想多了", "不说这个了", "我可没说", "你记错了",
    "别无理取闹", "我没那个意思", "哪有这回事", "你别脑补", "我不记得说过",
    "算了不说了",
)

# 合理理由词（偶然偏离的解释：生病/加班/出差…）
REASONABLE_REASON_WORDS: tuple[str, ...] = (
    "生病", "发烧", "感冒", "住院", "加班", "出差", "开会", "堵车", "临时有事",
    "家里有事", "朋友出事", "手机坏了", "没信号", "睡过头",
)

# 关系阶段目的节点词（用于"目的达成突变"检测）
RELATIONSHIP_NODE_WORDS: tuple[str, ...] = (
    "在一起", "确认关系", "做我女朋友", "做我男朋友", "见家长", "领证",
    "结婚", "纪念日", "第一次",
)

# --------------------------------------------------------------------------- #
# 无事献殷勤识别词表（早期超额付出 + 献殷勤后索取）
# --------------------------------------------------------------------------- #
# 关系早期窗口（天）：前 3 个月内的超额付出才可疑
UNWARRANTED_EARLY_DAYS: int = 90
# 献殷勤后短期索取窗口（天）：付出后 <2 周即索取
UNWARRANTED_PURSUE_TO_DEMAND_DAYS: int = 14

# 超前承诺关键词（关系早期就给出重大未来承诺 = 超前画饼）
EARLY_PROMISE_KEYWORDS: tuple[str, ...] = (
    "我养你", "嫁给我", "娶你", "我们结婚", "领证吧", "房产证写你",
    "工资卡给你", "一辈子对你好", "永远爱你", "我的都是你的",
    "把所有都给你", "我命都是你的", "你是我唯一",
)
# 贵重礼物 / 大额付出关键词
EARLY_GIFT_KEYWORDS: tuple[str, ...] = (
    "送你个包", "给你买手机", "给你买车", "钻戒", "项链", "名牌包",
    "奢侈品", "转你5200", "转你1314", "红包5200", "红包1314",
    "给你转了", "送你个大礼", "给你买了个", "给你转了520",
)
# 索取 / 金钱要求关键词
DEMAND_KEYWORDS: tuple[str, ...] = (
    "借钱", "借我钱", "转我", "周转一下", "帮我个忙", "帮我还",
    "担保", "垫一下", "借点", "先借", "发个红包", "支持一下",
)
# 快速推进关系 / 见家长 / 性索取关键词
DEMAND_RELATION_KEYWORDS: tuple[str, ...] = (
    "见家长", "确认关系", "在一起吧", "开房", "过来陪我",
    "去你那", "陪我一晚",
)
# 小恩小惠（付出小，却可能换大索取）
SMALL_GIVE_KEYWORDS: tuple[str, ...] = (
    "请你吃饭", "请你喝", "请你喝奶茶", "请你喝咖啡", "送你小礼物",
    "给你带了奶茶", "小惊喜", "请你看电影",
)

# 最小数据量要求：基线至少 90 天
MIN_BASELINE_DAYS: int = 90

# 异常判定的标准差倍数
SIGMA_MULTIPLIER: float = 2.0

# 持续时长阈值（天）
WARM_SUSTAIN_DAYS: int = 3      # 突然变好需持续 > 3 天才计入风险
COLD_SUSTAIN_DAYS: int = 14     # 突然变差需持续 > 14 天才计入风险
MODE_SHIFT_DAYS: int = 14       # 超过该时长无合理理由 = 模式转变

# 风险等级阈值
RISK_LOW: int = 30
RISK_MID: int = 60
RISK_HIGH: int = 80


class DeceptionDetector:
    """反伪装审计器：行为一致性审计 + 伪装风险评估。

    纯证据驱动：数据不足时不评估；信号不足时不下结论；
    输出措辞始终谨慎，不使用定性标签。
    """

    def __init__(self, db: Any = None) -> None:
        """注入 db（可选）并自动建表。

        Args:
            db: Database 实例，需提供 execute / fetch_one / fetch_all；
                为 None 时进入纯内存分析模式（不写库、不抛异常）。
        """
        self.db = db
        if self.db is not None:
            self._ensure_table()
        logger.info("DeceptionDetector 初始化完成 db=%s",
                    "on" if db else "off(内存)")

    # ------------------------------------------------------------------ #
    # 建表
    # ------------------------------------------------------------------ #
    def _ensure_table(self) -> None:
        """创建 deception_events 与 behavior_baseline 表（IF NOT EXISTS，幂等）。"""
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS deception_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id TEXT,
                event_type TEXT,
                content TEXT,
                context TEXT,
                detected_at TEXT,
                severity TEXT,
                classification TEXT,
                notes TEXT
            )
            """
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_deception_person "
            "ON deception_events(person_id, event_type)"
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS behavior_baseline (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id TEXT,
                baseline_json TEXT,
                created_at TEXT,
                data_span_days INTEGER,
                message_count INTEGER
            )
            """
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_baseline_person "
            "ON behavior_baseline(person_id)"
        )

    # ------------------------------------------------------------------ #
    # 工具方法
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
        """把消息方向归一为 received(对方说) / sent(我方说)。"""
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
    @lru_cache(maxsize=8192)
    def _parse_dt(s: Optional[str]) -> Optional[datetime]:
        """宽松解析时间字符串，失败返回 None。
        零风险优化：lru_cache 缓存同一时间戳字符串的解析结果，避免重复 strptime。"""
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

    @staticmethod
    def _safe_mean(xs: list[float]) -> float:
        """均值，空列表返回 0.0。"""
        return statistics.fmean(xs) if xs else 0.0

    @staticmethod
    def _safe_std(xs: list[float]) -> float:
        """样本标准差，长度<2 返回 0.0。"""
        if len(xs) < 2:
            return 0.0
        try:
            return statistics.pstdev(xs)
        except statistics.StatisticsError:
            return 0.0

    @staticmethod
    def _safe_median(xs: list[float]) -> float:
        """中位数，空列表返回 0.0。"""
        return statistics.median(xs) if xs else 0.0

    def _received(self, messages: list[dict]) -> list[dict]:
        """从消息列表中筛出对方说的消息（按时间升序）。"""
        out = [m for m in (messages or []) if self._normalize_direction(m) == "received"]
        out.sort(key=lambda m: self._parse_dt(self._msg_time(m)) or datetime.min)
        return out

    def _ordered(self, messages: list[dict]) -> list[dict]:
        """把消息列表按时间升序返回。"""
        out = list(messages or [])
        out.sort(key=lambda m: self._parse_dt(self._msg_time(m)) or datetime.min)
        return out

    def _reply_lags(self, messages: list[dict]) -> list[float]:
        """计算 我方(sent)->对方(received) 的回复延迟（分钟）。

        只取紧接在 sent 之后的第一条 received 消息计算间隔。
        """
        seq = self._ordered(messages)
        lags: list[float] = []
        for i in range(1, len(seq)):
            if (self._normalize_direction(seq[i]) == "received"
                    and self._normalize_direction(seq[i - 1]) == "sent"):
                t0 = self._parse_dt(self._msg_time(seq[i - 1]))
                t1 = self._parse_dt(self._msg_time(seq[i]))
                if t0 and t1 and t1 >= t0:
                    lags.append((t1 - t0).total_seconds() / 60.0)
        return lags

    def _emotion_density(self, messages: list[dict]) -> tuple[float, float, float]:
        """计算情感表达统计。

        Returns:
            tuple: (积极词频率, 消极词频率, 情感表达密度)。
                   频率=命中词数/消息数；密度=命中情感词总数/消息数。
        """
        recv = self._received(messages)
        if not recv:
            return 0.0, 0.0, 0.0
        pos_hits = 0
        neg_hits = 0
        emo_hits = 0
        for m in recv:
            text = self._msg_content(m)
            for w in POSITIVE_EMO_WORDS:
                c = text.count(w)
                pos_hits += c
                emo_hits += c
            for w in NEGATIVE_EMO_WORDS:
                c = text.count(w)
                neg_hits += c
                emo_hits += c
        n = len(recv)
        return pos_hits / n, neg_hits / n, emo_hits / n

    # ------------------------------------------------------------------ #
    # 1. 行为基线建立
    # ------------------------------------------------------------------ #
    def build_baseline(self, person_id: str, messages: list[dict]) -> dict:
        """基于至少 3 个月数据建立行为基线。

        基线维度：
          - 回复延迟模式：平均延迟、标准差、时段分布（白天/晚上/凌晨）；
          - 情感表达：积极词频率、消极词频率、情感表达密度；
          - 冲突处理模式：冲突时逃避/攻击/沟通的比例；
          - 金钱行为模式：付出/索取/AA 倾向；
          - 承诺兑现率时间分布：按季度；
          - 分阶段基线：初识期(0-2月)/暧昧期(2-6月)/热恋期(6-12月)/稳定期(>12月)。

        Args:
            person_id: 人物 ID。
            messages: 该人物全部聊天记录（含 sent/received）。

        Returns:
            dict: {available, reason?, ...基线维度...}。
                  数据不足时返回 {available: False, reason: "数据不足..."}。
        """
        msgs = self._ordered(messages)
        recv = self._received(msgs)
        if not recv:
            return {"available": False, "reason": "数据不足，需要至少3个月数据"}

        # 时间跨度
        times = [self._parse_dt(self._msg_time(m)) for m in recv]
        times = [t for t in times if t is not None]
        if not times:
            return {"available": False, "reason": "数据不足，需要至少3个月数据"}
        t_min, t_max = min(times), max(times)
        span_days = (t_max - t_min).days
        if span_days < MIN_BASELINE_DAYS:
            return {
                "available": False,
                "reason": "数据不足，需要至少3个月数据",
                "data_span_days": span_days,
                "message_count": len(recv),
            }

        # 回复延迟
        lags = self._reply_lags(msgs)
        lag_mean = self._safe_mean(lags)
        lag_std = self._safe_std(lags)
        lag_median = self._safe_median(lags)

        # 时段分布（基于对方消息发送时刻）
        day = night = dawn = 0
        for t in times:
            h = t.hour
            if 6 <= h < 18:
                day += 1
            elif 18 <= h < 24:
                night += 1
            else:
                dawn += 1
        total = len(times) or 1
        time_dist = {
            "day_ratio": round(day / total, 3),
            "night_ratio": round(night / total, 3),
            "dawn_ratio": round(dawn / total, 3),
        }

        # 情感表达
        pos_freq, neg_freq, emo_density = self._emotion_density(recv)

        # 冲突处理模式：在含冲突词的对方消息中，看其后续措辞倾向
        conflict_escape = conflict_attack = conflict_comm = 0
        for m in recv:
            text = self._msg_content(m)
            if not any(cw in text for cw in CONFLICT_WORDS):
                continue
            if any(k in text for k in ("不想说", "算了", "随便", "我累了", "不想理")):
                conflict_escape += 1
            elif any(k in text for k in ("滚", "不可理喻", "你又", "有病")):
                conflict_attack += 1
            else:
                conflict_comm += 1
        conflict_total = conflict_escape + conflict_attack + conflict_comm
        conflict_pattern = {
            "escape_ratio": round(conflict_escape / conflict_total, 3) if conflict_total else 0.0,
            "attack_ratio": round(conflict_attack / conflict_total, 3) if conflict_total else 0.0,
            "communicate_ratio": round(conflict_comm / conflict_total, 3) if conflict_total else 0.0,
            "conflict_msg_count": conflict_total,
        }

        # 金钱行为模式：从含金额关键词的消息统计付出/索取/AA
        give = take = aa = 0
        for m in recv:
            text = self._msg_content(m)
            if not any(k in text for k in MONEY_KEYWORDS):
                continue
            if any(k in text for k in ("我请", "我买单", "给你买", "我来付", "送你")):
                give += 1
            elif any(k in text for k in ("借我", "转我", "帮我付", "垫一下", "先付")):
                take += 1
            elif "AA" in text:
                aa += 1
        money_total = give + take + aa
        money_pattern = {
            "give_ratio": round(give / money_total, 3) if money_total else 0.5,
            "take_ratio": round(take / money_total, 3) if money_total else 0.0,
            "aa_ratio": round(aa / money_total, 3) if money_total else 0.0,
            "money_msg_count": money_total,
        }

        # 分阶段基线
        stages = self._staged_baseline(recv, t_min)

        baseline: dict[str, Any] = {
            "available": True,
            "data_span_days": span_days,
            "message_count": len(recv),
            "reply_lag": {
                "mean_min": round(lag_mean, 2),
                "std_min": round(lag_std, 2),
                "median_min": round(lag_median, 2),
                "sample_count": len(lags),
            },
            "time_distribution": time_dist,
            "emotion": {
                "positive_freq": round(pos_freq, 3),
                "negative_freq": round(neg_freq, 3),
                "density": round(emo_density, 3),
            },
            "conflict_pattern": conflict_pattern,
            "money_pattern": money_pattern,
            "stages": stages,
        }

        # 入库
        if self.db is not None:
            try:
                self.db.execute(
                    "INSERT INTO behavior_baseline "
                    "(person_id, baseline_json, created_at, data_span_days, message_count) "
                    "VALUES (:pid, :bj, :ca, :sd, :mc)",
                    {
                        "pid": person_id,
                        "bj": json.dumps(baseline, ensure_ascii=False),
                        "ca": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "sd": span_days,
                        "mc": len(recv),
                    },
                )
            except Exception as e:  # noqa: BLE001 - 写库失败不影响返回
                logger.warning("行为基线入库失败 person=%s: %s", person_id, e)

        logger.info(
            "行为基线建立 person=%s span=%ddays msgs=%d lag_mean=%.1fmin",
            person_id, span_days, len(recv), lag_mean,
        )
        return baseline

    def _staged_baseline(self, recv: list[dict], t_min: datetime) -> dict:
        """按关系阶段切分基线：初识(0-2月)/暧昧(2-6月)/热恋(6-12月)/稳定(>12月)。

        每个阶段给出回复延迟中位数与情感密度的正常范围参考。
        """
        buckets: dict[str, list[dict]] = {
            "initial": [],     # 初识期 0-2月
            "ambiguous": [],   # 暧昧期 2-6月
            "passionate": [],  # 热恋期 6-12月
            "stable": [],      # 稳定期 >12月
        }
        for m in recv:
            t = self._parse_dt(self._msg_time(m))
            if not t:
                continue
            months = (t - t_min).days / 30.0
            if months < 2:
                buckets["initial"].append(m)
            elif months < 6:
                buckets["ambiguous"].append(m)
            elif months < 12:
                buckets["passionate"].append(m)
            else:
                buckets["stable"].append(m)

        result: dict[str, Any] = {}
        for name, ms in buckets.items():
            if not ms:
                result[name] = {"message_count": 0, "note": "该阶段无数据"}
                continue
            pos_f, neg_f, density = self._emotion_density(ms)
            result[name] = {
                "message_count": len(ms),
                "emotion_density": round(density, 3),
                "positive_freq": round(pos_f, 3),
            }
        return result

    # ------------------------------------------------------------------ #
    # 2. 异常偏离检测
    # ------------------------------------------------------------------ #
    def detect_anomalies(
        self,
        person_id: str,
        recent_messages: list[dict],
        baseline: dict,
    ) -> list[dict]:
        """检测近期行为相对基线的突然偏离，区分偶然偏离与模式转变。

        检测类型：
          - sudden_warmth：突然变好（近期情感频率 > 基线均值+2σ 且持续>3天）；
          - sudden_cold：突然变差（近期回复延迟 > 基线均值+2σ，或情感频率
            < 基线均值-2σ，持续>2周）；
          - inconsistency：场景不一致（私下/公开、对你/对他人态度差异过大）。

        偏离分类：
          - 偶然偏离：1-2 次、有合理理由（生病/加班/出差）→ 不计入风险；
          - 模式转变：持续>2周、无合理理由 → 计入风险。

        Args:
            person_id: 人物 ID。
            recent_messages: 近期聊天记录（通常取最近 30-45 天）。
            baseline: build_baseline 返回的基线 dict。

        Returns:
            list[dict]: 异常事件列表，每项含 type/severity/duration_days/
                        description/evidence/classification。
        """
        anomalies: list[dict] = []
        if not baseline or not baseline.get("available"):
            return anomalies

        recv = self._received(recent_messages)
        if not recv:
            return anomalies

        bl_lag = baseline.get("reply_lag", {})
        bl_mean = float(bl_lag.get("mean_min", 0.0))
        bl_std = float(bl_lag.get("std_min", 0.0))
        bl_median = float(bl_lag.get("median_min", 0.0))

        bl_emo = baseline.get("emotion", {})
        bl_pos = float(bl_emo.get("positive_freq", 0.0))
        bl_density = float(bl_emo.get("density", 0.0))

        # 近期统计
        recent_lags = self._reply_lags(recent_messages)
        recent_lag_mean = self._safe_mean(recent_lags)
        r_pos, r_neg, r_density = self._emotion_density(recent_messages)

        # 近期时间跨度（持续天数）
        times = [self._parse_dt(self._msg_time(m)) for m in recv]
        times = [t for t in times if t is not None]
        duration_days = (max(times) - min(times)).days if len(times) >= 2 else 1

        # 是否有合理理由
        joined_text = "\n".join(self._msg_content(m) for m in recv)
        has_reason = any(k in joined_text for k in REASONABLE_REASON_WORDS)

        # --- 突然变好 ---
        warm_threshold = bl_pos + SIGMA_MULTIPLIER * (bl_std / 10.0 + 0.1)
        # 用中位数倍数法兜底，避免基线 std 过小导致阈值失真
        warm_ratio = (r_pos / bl_pos) if bl_pos > 0 else (1.0 if r_pos > 0 else 0.0)
        if (r_pos > warm_threshold and warm_ratio >= 1.5
                and duration_days >= WARM_SUSTAIN_DAYS):
            classification = self._classify_deviation(has_reason, duration_days, warm=True)
            # 检查是否发生在重大利益节点前
            near_node = any(k in joined_text for k in INTEREST_NODE_KEYWORDS)
            anomalies.append({
                "type": "sudden_warmth",
                "severity": "medium" if near_node else "low",
                "duration_days": duration_days,
                "classification": classification,
                "description": (
                    f"近期情感表达频率({r_pos:.2f})明显高于基线({bl_pos:.2f})"
                    f"且持续{duration_days}天"
                    + ("，且出现在重大利益节点前后，动机合理性需关注" if near_node else "")
                ),
                "evidence": self._pick_evidence(recv, POSITIVE_EMO_WORDS, 3),
                "near_interest_node": near_node,
            })

        # --- 突然变差 ---
        cold_lag_threshold = max(bl_mean + SIGMA_MULTIPLIER * bl_std, bl_median * 3)
        emotion_drop = bl_density - SIGMA_MULTIPLIER * 0.1
        lag_too_slow = recent_lag_mean > cold_lag_threshold and recent_lag_mean > 0
        emo_too_low = (r_density < emotion_drop and bl_density > 0.1
                       and r_density < bl_density * 0.5)
        if (lag_too_slow or emo_too_low) and duration_days >= (COLD_SUSTAIN_DAYS - 3):
            classification = self._classify_deviation(has_reason, duration_days, warm=False)
            anomalies.append({
                "type": "sudden_cold",
                "severity": "high" if classification == "模式转变" else "low",
                "duration_days": duration_days,
                "classification": classification,
                "description": (
                    f"近期回复延迟({recent_lag_mean:.0f}min)/情感密度({r_density:.2f})"
                    f"相对基线(延迟{bl_mean:.0f}min/密度{bl_density:.2f})出现明显回落，"
                    f"持续{duration_days}天"
                    + ("，且无合理理由，属持续性行为变化" if classification == "模式转变"
                       else "，但存在合理理由或为短期波动")
                ),
                "evidence": self._pick_evidence(recv, NEGATIVE_EMO_WORDS, 3),
            })

        # --- 场景不一致（粗略：消息中同时出现对第三方的负面与对你的正面）---
        thirdparty_neg = any(k in joined_text for k in THIRD_PARTY_NEGATIVE_WORDS)
        self_praise = any(k in joined_text for k in ("我对你最好", "我最爱你", "我心里只有你"))
        if thirdparty_neg and self_praise:
            anomalies.append({
                "type": "inconsistency",
                "severity": "medium",
                "duration_days": duration_days,
                "classification": "模式转变",
                "description": "私下/背后评价他人与对你的公开表现存在较大反差，"
                               "多场景一致性待观察",
                "evidence": self._pick_evidence(recv, THIRD_PARTY_NEGATIVE_WORDS, 3),
            })

        return anomalies

    def _classify_deviation(self, has_reason: bool, duration_days: int, warm: bool) -> str:
        """把一次偏离分类为'偶然偏离'或'模式转变'。

        规则：
          - 有合理理由 → 偶然偏离；
          - 无合理理由且持续超过 MODE_SHIFT_DAYS → 模式转变；
          - 其余（短期、无理由但未达时长）→ 偶然偏离。
        """
        if has_reason:
            return "偶然偏离"
        if duration_days >= MODE_SHIFT_DAYS:
            return "模式转变"
        return "偶然偏离"

    @staticmethod
    def _pick_evidence(recv: list[dict], keywords: tuple[str, ...], n: int) -> list[str]:
        """从对方消息中挑出命中关键词的片段作为证据。"""
        ev: list[str] = []
        for m in recv:
            text = DeceptionDetector._msg_content(m)
            if any(k in text for k in keywords):
                ev.append(text[:40])
            if len(ev) >= n:
                break
        return ev

    # ------------------------------------------------------------------ #
    # 3. 伪装风险评估
    # ------------------------------------------------------------------ #
    def assess_risk(
        self,
        person_id: str,
        baseline: dict,
        anomalies: list[dict],
        events: list[dict],
    ) -> dict:
        """五维加权计算伪装风险评分（0-100）。

        维度与权重：
          | 维度           | 权重 | 说明 |
          | 行为一致性     | 30%  | 长期行为是否稳定（标准差小=一致性高=风险低）|
          | 动机合理性     | 20%  | 好行为是否发生在重大利益节点前 |
          | 露馅事件数量   | 20%  | 不经意暴露真实态度的事件数 |
          | 多场景一致性   | 15%  | 不同场景下是否同一人 |
          | 目的达成后变化 | 15%  | 达到目的后是否行为突变 |

        风险等级（修复引导导向：即使有风险，策略也是观察验证+理解根源+引导改善，而非撤离）：
          - 低（<30）：行为一致性良好，可以正常推进关系；
          - 中（30-60）：检测到一些不一致信号，建议通过小范围互动进一步观察，根据反应调整推进节奏；
          - 高（60-80）：多个不一致信号，建议保持观察、验证真实意图，同时理解不一致背后的原因，根据结果决定如何引导关系改善；
          - 极高（>80）：建议谨慎推进、持续验证真实自我，可接受则继续，创造安全氛围引导对方展现真实一面，同时设定底线保护关系健康。

        Returns:
            dict: {risk_score, risk_level, dimensions, warnings,
                  recommendation, data_sufficient}。
        """
        # 数据充足性：仅当基线可用才评估
        if not baseline or not baseline.get("available"):
            return {
                "risk_score": 0,
                "risk_level": "未知",
                "dimensions": {},
                "warnings": [],
                "recommendation": "数据不足，无法评估伪装风险",
                "data_sufficient": False,
            }

        warnings: list[str] = []

        # 维度1：行为一致性（30%）—— 基线越稳定风险越低；模式转变异常扣分
        bl_lag = baseline.get("reply_lag", {})
        mean_lag = float(bl_lag.get("mean_min", 1.0)) or 1.0
        std_lag = float(bl_lag.get("std_min", 0.0))
        cv = std_lag / mean_lag if mean_lag > 0 else 0.0
        # 变异系数小=一致。cv<0.3 → 基础风险15；0.3-0.6 → 35；>0.6 → 60
        if cv < 0.3:
            consistency_risk = 15.0
        elif cv < 0.6:
            consistency_risk = 35.0
        else:
            consistency_risk = 60.0
        mode_shifts = [a for a in anomalies if a.get("classification") == "模式转变"]
        consistency_risk += len(mode_shifts) * 25.0
        consistency_risk = min(consistency_risk, 100.0)
        if mode_shifts:
            warnings.append(f"检测到 {len(mode_shifts)} 处持续性行为偏离（模式转变）")

        # 维度2：动机合理性（20%）—— 突然变好且靠近利益节点 = 高风险；
        # 目的达成突变本身即说明前期好行为可能是"工具性表演"，一并拉高动机风险
        purpose_shifts = [a for a in anomalies if a.get("type") == "purpose_shift"]
        warm_near_node = [a for a in anomalies
                          if a.get("type") == "sudden_warmth" and a.get("near_interest_node")]
        warm_any = [a for a in anomalies if a.get("type") == "sudden_warmth"]
        if warm_near_node or purpose_shifts:
            motive_risk = 80.0
            warnings.append("对方明显的热情提升发生在重大利益节点前后，动机合理性存疑")
        elif warm_any:
            motive_risk = 40.0
        else:
            motive_risk = 10.0

        # 维度3：露馅事件数量（20%）—— 伪装破裂权重高于偶然失误
        break_events = [e for e in events if e.get("classification") == "possible_break"]
        accidental_events = [e for e in events if e.get("classification") == "accidental"]
        slip_risk = min(100.0, len(break_events) * 40.0 + len(accidental_events) * 10.0)
        if break_events:
            warnings.append(f"记录到 {len(break_events)} 次事后试图掩盖/否认的露馅事件")

        # 维度4：多场景一致性（15%）
        incons = [a for a in anomalies if a.get("type") == "inconsistency"]
        scene_risk = 60.0 * len(incons) if incons else 10.0
        if incons:
            warnings.append("不同场景下对方表现存在反差，多场景一致性待验证")

        # 维度5：目的达成后变化（15%）—— purpose_shift 异常由 analyze 注入
        purpose_risk = 80.0 if purpose_shifts else 10.0
        if purpose_shifts:
            warnings.append("检测到对方在达成关系/利益目的后行为出现突变")

        dimensions = {
            "behavior_consistency": round(consistency_risk, 1),
            "motive_rationality": round(motive_risk, 1),
            "slip_events": round(slip_risk, 1),
            "multi_scene_consistency": round(scene_risk, 1),
            "post_purpose_change": round(purpose_risk, 1),
        }

        # 加权综合（注意：这里的子分是"风险分"，越高越危险）
        risk_score = round(
            consistency_risk * 0.30
            + motive_risk * 0.20
            + slip_risk * 0.20
            + scene_risk * 0.15
            + purpose_risk * 0.15,
            1,
        )

        risk_level, recommendation = self._level_and_recommendation(risk_score)

        return {
            "risk_score": risk_score,
            "risk_level": risk_level,
            "dimensions": dimensions,
            "warnings": warnings,
            "recommendation": recommendation,
            "data_sufficient": True,
        }

    @staticmethod
    def _level_and_recommendation(score: float) -> tuple[str, str]:
        """按风险分给出等级与攻略导向的建议文字。

        修复引导导向原则：即使有风险，策略也是"观察验证+理解根源+引导改善"，
        而不是"建议撤离"。极端情况（家暴、出轨、持续欺骗）可以提示严重风险，
        但常规问题都应该是修复引导导向——通过验证判断真实自我，如果可接受则继续推进，
        同时创造安全氛围引导对方展现真实一面。
        """
        if score < RISK_LOW:
            return "低", "行为一致性良好，可以正常推进关系。"
        if score < RISK_MID:
            return "中", ("检测到一些不一致信号，建议通过小范围互动进一步观察，"
                        "根据对方反应调整推进节奏。")
        if score < RISK_HIGH:
            return "高", ("多个不一致信号，建议保持观察、通过无利益场景验证对方真实意图，"
                        "同时理解这些不一致背后可能的原因，根据验证结果决定如何引导关系改善，"
                        "不急于重大投入。")
        return "极高", ("存在较多行为不一致和目的导向行为，建议谨慎推进，"
                      "通过持续验证判断对方真实自我。"
                      "如果真实自我可接受则继续推进，创造安全氛围引导对方展现真实一面，"
                      "同时设定清晰底线保护关系健康；"
                      "如果涉及家暴、持续欺骗、出轨等严重问题，请优先保护自身安全。")

    # ------------------------------------------------------------------ #
    # 4. 露馅事件识别
    # ------------------------------------------------------------------ #
    def detect_slip_events(self, messages: list[dict]) -> list[dict]:
        """识别"不经意露馅"场景，并区分偶然失误与伪装破裂。

        场景类型：
          1. late_night：酒后/深夜/疲劳状态（凌晨1-5点或状态词），防御降低；
          2. conflict_outburst：争吵中的失控表达（平时温和突然说狠话）；
          3. thirdparty_badmouthing：对第三方的负面评价（背后说的话更真实）；
          4. interest_reaction：涉及利益时的真实反应（钱/时间/精力分配）。

        区分规则：
          - 偶然失误(accidental)：后续消息含歉意/修正词、不重复；
          - 伪装破裂(possible_break)：后续消息含掩盖/否认/转移话题词，或同类事件重复。

        Returns:
            list[dict]: 露馅事件列表（已写入 deception_events 表）。
        """
        msgs = self._ordered(messages)
        recv_idx = [i for i, m in enumerate(msgs)
                    if self._normalize_direction(m) == "received"]

        events: list[dict] = []
        seen_contents: list[str] = []

        for i in recv_idx:
            m = msgs[i]
            text = self._msg_content(m)
            if not text:
                continue
            t = self._parse_dt(self._msg_time(m))
            event_type = self._classify_slip_scene(m, t, text)
            if not event_type:
                continue

            # 看后续 10 条消息内的反应（歉意修正 vs 掩盖否认）
            followup = " ".join(self._msg_content(msgs[j])
                                for j in range(i + 1, min(i + 11, len(msgs))))
            has_apology = any(k in followup for k in APOLOGY_CORRECTION_WORDS)
            has_deflection = any(k in followup for k in DEFLECTION_WORDS)

            # 重复检测：同类内容是否出现过
            repeated = text[:20] in seen_contents
            seen_contents.append(text[:20])

            if has_deflection or repeated:
                classification = "possible_break"
                severity = "high"
                notes = "事后出现掩盖/否认/转移话题，或同类事件重复出现"
            elif has_apology:
                classification = "accidental"
                severity = "low"
                notes = "事后有歉意/修正，判为偶然失误"
            else:
                # 无明确后续反应：保守标注为待观察，不计入"破裂"
                classification = "accidental"
                severity = "medium"
                notes = "未观察到明确的掩盖或歉意，暂按偶然失误记录待观察"

            event = {
                "person_id": str(m.get("person_id", "")),
                "event_type": event_type,
                "content": text.strip()[:120],
                "context": "",
                "detected_at": (t or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
                "severity": severity,
                "classification": classification,
                "notes": notes,
            }
            events.append(event)

            if self.db is not None:
                try:
                    self.db.execute(
                        "INSERT INTO deception_events "
                        "(person_id, event_type, content, context, detected_at, "
                        " severity, classification, notes) "
                        "VALUES (:pid, :et, :ct, :cx, :da, :sv, :cl, :nt)",
                        {
                            "pid": event["person_id"],
                            "et": event_type,
                            "ct": event["content"],
                            "cx": event["context"],
                            "da": event["detected_at"],
                            "sv": severity,
                            "cl": classification,
                            "nt": notes,
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("露馅事件入库失败: %s", e)

        if events:
            logger.info("露馅事件识别 msgs=%d events=%d", len(msgs), len(events))
        return events

    def _classify_slip_scene(self, m: dict, t: Optional[datetime], text: str) -> Optional[str]:
        """判断一条对方消息是否命中某类露馅场景，返回场景类型或 None。"""
        # 1. 酒后/深夜/疲劳
        if t is not None and (1 <= t.hour <= 5):
            return "late_night"
        if any(k in text for k in LATE_NIGHT_STATE_WORDS):
            return "late_night"
        # 2. 争吵失控：含冲突词且含狠话
        if (any(k in text for k in CONFLICT_WORDS)
                and any(k in text for k in HARSH_OUT_OF_CHARACTER_WORDS)):
            return "conflict_outburst"
        # 3. 对第三方负面评价
        if any(k in text for k in THIRD_PARTY_NEGATIVE_WORDS):
            return "thirdparty_badmouthing"
        # 4. 涉及利益时的反应
        if any(k in text for k in MONEY_KEYWORDS):
            return "interest_reaction"
        return None

    # ------------------------------------------------------------------ #
    # 5. 时间维度信任累积
    # ------------------------------------------------------------------ #
    def calculate_temporal_trust(self, person_id: str, messages: list[dict]) -> dict:
        """按时间窗口加权计算信任分，并检测目的达成后行为突变。

        加权规则：
          - 短期好行为（<3个月）：权重 0.3，可能是表演；
          - 中期一致（3-12个月）：权重 0.6；
          - 长期一致（>1年）：权重 1.0，伪装成本极高。

        目的达成突变检测：
          - 目的节点（确认关系/借钱/见家长/承诺未来）前30天 vs 后30天，
            回复延迟/情感表达频率差异 > 2σ → 标记 purpose_achievement_shift。

        Returns:
            dict: {temporal_trust_score, short_term_rate, mid_term_rate,
                  long_term_rate, purpose_achievement_shifts, note}。
        """
        msgs = self._ordered(messages)
        recv = self._received(msgs)
        if not recv:
            return {
                "temporal_trust_score": 0,
                "short_term_rate": 0.0,
                "mid_term_rate": 0.0,
                "long_term_rate": 0.0,
                "purpose_achievement_shifts": [],
                "note": "无聊天记录",
            }

        times = [self._parse_dt(self._msg_time(m)) for m in recv]
        times = [t for t in times if t is not None]
        t_min = min(times)
        now = max(times)

        # 分桶：短期(<90天) / 中期(90-365天) / 长期(>365天)
        # 零风险优化：复用预解析的 times，避免 bucket_rate 内部重复 parse_dt
        recv_with_time = list(zip(recv, times))
        def bucket_rate(days_start: float, days_end: float) -> tuple[float, int]:
            """某时间窗口内的'一致性率'：1 - 标准差变异系数。"""
            bucket = []
            for m, t in recv_with_time:
                age_days = (now - t).days
                if days_start <= age_days < days_end:
                    bucket.append(m)
            if len(bucket) < 3:
                return 0.0, len(bucket)
            _, _, density = self._emotion_density(bucket)
            # 简单一致性：情感密度稳定（这里用 1 - 近期波动近似）
            lags = self._reply_lags(bucket)
            if len(lags) >= 2:
                cv = self._safe_std(lags) / (self._safe_mean(lags) or 1.0)
                rate = max(0.0, 1.0 - min(cv, 1.0))
            else:
                rate = 0.7
            return round(rate, 3), len(bucket)

        short_rate, short_n = bucket_rate(0, 90)
        mid_rate, mid_n = bucket_rate(90, 365)
        long_rate, long_n = bucket_rate(365, 36500)

        # 加权信任分（长期权重最高）
        trust_score = round(
            (short_rate * 0.3 + mid_rate * 0.6 + long_rate * 1.0) * 100, 1
        )

        # 目的达成突变检测
        shifts = self._detect_purpose_shifts(msgs, now)

        note = (
            f"短期一致性{short_rate:.2f}(权重0.3) / 中期{mid_rate:.2f}(权重0.6) / "
            f"长期{long_rate:.2f}(权重1.0)；"
            f"检测到{len(shifts)}处目的达成突变。"
        )

        return {
            "temporal_trust_score": trust_score,
            "short_term_rate": short_rate,
            "mid_term_rate": mid_rate,
            "long_term_rate": long_rate,
            "purpose_achievement_shifts": shifts,
            "note": note,
        }

    def _detect_purpose_shifts(self, msgs: list[dict], now: datetime) -> list[dict]:
        """检测目的节点前后 30 天行为指标的突变。

        Returns:
            list[dict]: 检测到的突变事件（每项含 node/date/metric/before/after）。
        """
        shifts: list[dict] = []
        # 找目的节点：对方消息中出现关系节点词
        node_dates: list[datetime] = []
        for m in msgs:
            if self._normalize_direction(m) != "received":
                continue
            text = self._msg_content(m)
            t = self._parse_dt(self._msg_time(m))
            if t and any(k in text for k in RELATIONSHIP_NODE_WORDS + INTEREST_NODE_KEYWORDS):
                node_dates.append(t)

        for node_dt in node_dates[:10]:  # 最多检测10个节点
            before = [m for m in msgs
                      if self._parse_dt(self._msg_time(m))
                      and node_dt - timedelta(days=30) <= self._parse_dt(self._msg_time(m)) < node_dt]
            after = [m for m in msgs
                     if self._parse_dt(self._msg_time(m))
                     and node_dt < self._parse_dt(self._msg_time(m)) <= node_dt + timedelta(days=30)]
            if len(before) < 3 or len(after) < 3:
                continue

            _, _, before_density = self._emotion_density(before)
            _, _, after_density = self._emotion_density(after)
            before_lag = self._safe_mean(self._reply_lags(before))
            after_lag = self._safe_mean(self._reply_lags(after))

            # 情感密度骤降 >50% 或回复延迟 >2倍 = 突变
            density_drop = (before_density - after_density) / before_density if before_density > 0 else 0
            lag_spike = (after_lag / before_lag) if before_lag > 0 else 1.0
            if density_drop > 0.5 or lag_spike > 2.0:
                shifts.append({
                    "node": "关系/利益目的节点",
                    "date": node_dt.strftime("%Y-%m-%d"),
                    "metric": "情感密度骤降" if density_drop > 0.5 else "回复延迟骤增",
                    "before": round(before_density, 3) if density_drop > 0.5 else round(before_lag, 1),
                    "after": round(after_density, 3) if density_drop > 0.5 else round(after_lag, 1),
                })
        return shifts

    # ------------------------------------------------------------------ #
    # 6. 无事献殷勤识别（早期超额付出 + 献殷勤后索取）
    # ------------------------------------------------------------------ #
    def detect_unwarranted_attention(self, messages: list[dict]) -> dict:
        """无事献殷勤识别：关系早期超额付出 + 献殷勤后短期索取。

        检测逻辑：
          1. 关系早期(前3个月)对方出现贵重礼物/超前承诺（刚认识就"我养你"/"嫁给我"）；
          2. 超额付出后短期内(<2周)提出索取（借钱/帮忙/性/快速推进关系）；
          3. 付出与回报不对等：小恩小惠(请吃饭) 换 大索取(借钱/大额帮忙)。

        风险等级（攻略导向：不是"对方在捞你"，而是"建议观察目的，引导双向奔赴"）：
          - 低（<30）：正常追求期的殷勤，无后续索取；
          - 中（30-60）：殷勤后有索取，但金额/程度不大；
          - 高（60-80）：明显的投资-索取模式，付出小索取大；
          - 极高（>80）：早期大额付出后立即大额索取，典型投资-索取模式。

        Returns:
            dict: {detected, risk_score, risk_level, evidence, note}。
        """
        msgs = self._ordered(messages)
        recv = [m for m in msgs if self._normalize_direction(m) == "received"]
        if len(recv) < 3:
            return self._unwarranted_default()

        times = [self._parse_dt(self._msg_time(m)) for m in recv]
        valid = [(m, t) for m, t in zip(recv, times) if t is not None]
        if len(valid) < 3:
            return self._unwrapped_default()
        t_min = min(t for _, t in valid)

        promise_events: list[tuple[datetime, str]] = []
        gift_events: list[tuple[datetime, str]] = []
        demand_events: list[tuple[datetime, str]] = []
        small_give_events: list[tuple[datetime, str]] = []
        big_demand_found = False

        for m, t in valid:
            text = self._msg_content(m)
            days = (t - t_min).days
            in_early = days < UNWARRANTED_EARLY_DAYS
            if in_early:
                for kw in EARLY_PROMISE_KEYWORDS:
                    if kw in text:
                        promise_events.append((t, f"早期超前承诺：{text[:30]}"))
                        break
                for kw in EARLY_GIFT_KEYWORDS:
                    if kw in text:
                        gift_events.append((t, f"早期贵重付出：{text[:30]}"))
                        break
            for kw in DEMAND_KEYWORDS + DEMAND_RELATION_KEYWORDS:
                if kw in text:
                    demand_events.append((t, f"索取/要求：{text[:30]}"))
                    break
            # 大额金钱索取标记（用于付出-回报不对等判定）
            if any(k in text for k in DEMAND_KEYWORDS):
                big_demand_found = True
            for kw in SMALL_GIVE_KEYWORDS:
                if kw in text:
                    small_give_events.append((t, f"小恩小惠：{text[:30]}"))
                    break

        pursuit_events = promise_events + gift_events
        # 无早期超额付出 → 不构成无事献殷勤（正常追求）
        if not pursuit_events:
            return self._unwarranted_default()

        score = 0.0
        evidence: list[str] = []
        for _, ev in promise_events:
            score += 12.0
            evidence.append(ev)
        for _, ev in gift_events:
            score += 18.0
            evidence.append(ev)

        # 献殷勤后短期(<14天)即索取
        timed_demands = 0
        for dt, ev in demand_events:
            score += 15.0
            evidence.append(ev)
            for pt, _ in pursuit_events:
                gap = (dt - pt).days
                if 0 <= gap <= UNWARRANTED_PURSUE_TO_DEMAND_DAYS:
                    timed_demands += 1
                    evidence.append(f"付出后{gap}天即索取（短期索取信号）")
                    break
        score += timed_demands * 12.0

        # 付出与回报不对等：小恩小惠 + 大额金钱索取
        if small_give_events and big_demand_found:
            score += 20.0
            evidence.append("存在小恩小惠伴随大额索取的付出-回报不对等模式")

        score = round(min(score, 100.0), 1)

        if score < RISK_LOW:
            level = "low"
            note = ("检测到关系早期的殷勤付出，但暂无后续索取，属正常追求期表现，"
                    "可自然推进关系")
        elif score < RISK_MID:
            level = "medium"
            note = ("检测到对方在关系早期有超额付出后索取的行为，建议观察是否有明确目的，"
                    "引导双向奔赴而非单方面接受")
        elif score < RISK_HIGH:
            level = "high"
            note = ("检测到明显的早期超额付出后索取模式，建议观察是否有明确目的、保持投入平衡，"
                    "引导双向奔赴，不单方面接受大额好处")
        else:
            level = "critical"
            note = ("检测到关系早期大额付出后立即大额索取的典型模式，建议观察是否有明确目的，"
                    "引导双向奔赴而非单方面接受，重大利益前设定清晰底线")

        return {
            "detected": True,
            "risk_score": score,
            "risk_level": level,
            "evidence": evidence[:10],
            "note": note,
        }

    @staticmethod
    def _unwarranted_default() -> dict:
        """无事献殷勤未触发时的默认返回。"""
        return {
            "detected": False,
            "risk_score": 0,
            "risk_level": "low",
            "evidence": [],
            "note": "未检测到关系早期超额付出后索取的行为，双方投入自然",
        }

    # ------------------------------------------------------------------ #
    # 7. 综合分析（主入口）
    # ------------------------------------------------------------------ #
    def analyze(self, person_id: str, messages: list[dict]) -> dict:
        """综合分析主入口：按顺序执行基线→异常→露馅→时间信任→风险评估。

        Args:
            person_id: 人物 ID。
            messages: 该人物全部聊天记录。

        Returns:
            dict: 完整反伪装审计报告；数据不足时 data_sufficient=False。
        """
        msgs = self._ordered(messages)
        if not msgs:
            return {
                "person_id": person_id,
                "data_sufficient": False,
                "risk_score": 0,
                "risk_level": "未知",
                "recommendation": "数据不足，无法评估伪装风险",
                "warnings": [],
                "unwarranted_attention": self._unwarranted_default(),
            }

        # 无事献殷勤识别（独立于基线，早期超额付出+短期索取即可触发）
        unwarranted = self.detect_unwarranted_attention(msgs)

        # 切分：最近 30 天作为"近期窗口"用于异常检测，其余作为基线
        times = [self._parse_dt(self._msg_time(m)) for m in msgs]
        valid_times = [t for t in times if t is not None]
        now = max(valid_times) if valid_times else datetime.now()
        recent_cutoff = now - timedelta(days=30)

        baseline_msgs = []
        recent_msgs = []
        for m, t in zip(msgs, times):
            if t is None:
                baseline_msgs.append(m)
            elif t >= recent_cutoff:
                recent_msgs.append(m)
            else:
                baseline_msgs.append(m)

        # 1. 基线（数据不足直接返回）
        baseline = self.build_baseline(person_id, baseline_msgs)
        if not baseline.get("available"):
            return {
                "person_id": person_id,
                "data_sufficient": False,
                "risk_score": 0,
                "risk_level": "未知",
                "warnings": [],
                "recommendation": baseline.get("reason", "数据不足，无法评估伪装风险"),
                "reason": baseline.get("reason"),
                "unwarranted_attention": unwarranted,
            }

        # 2. 异常偏离检测
        anomalies = self.detect_anomalies(person_id, recent_msgs, baseline)

        # 3. 露馅事件识别
        events = self.detect_slip_events(msgs)

        # 4. 时间维度信任
        temporal = self.calculate_temporal_trust(person_id, msgs)

        # 把目的达成突变注入 anomalies，供风险评估"目的达成后变化"维度使用
        for shift in temporal.get("purpose_achievement_shifts", []):
            anomalies.append({
                "type": "purpose_shift",
                "severity": "high",
                "duration_days": 60,
                "classification": "模式转变",
                "description": (
                    f"目的节点({shift.get('date')})后检测到{shift.get('metric')}："
                    f"{shift.get('before')} → {shift.get('after')}"
                ),
                "evidence": [shift.get("node", "")],
            })

        # 5. 风险评估
        risk = self.assess_risk(person_id, baseline, anomalies, events)

        return {
            "person_id": person_id,
            "data_sufficient": True,
            "baseline": {
                "data_span_days": baseline.get("data_span_days"),
                "message_count": baseline.get("message_count"),
            },
            "anomalies": anomalies,
            "slip_events": events,
            "temporal_trust": temporal,
            "risk_score": risk.get("risk_score", 0),
            "risk_level": risk.get("risk_level", "未知"),
            "dimensions": risk.get("dimensions", {}),
            "warnings": risk.get("warnings", []),
            "recommendation": risk.get("recommendation", ""),
            "unwarranted_attention": unwarranted,
        }


# =========================================================================== #
# P-06 伪装识别 与 侧写慢更新（补缺口：纯函数 + 门控）
# --------------------------------------------------------------------------- #
# 设计口径（《LoveAI-统一实施总纲 v3.1 加固补丁》P-06）：
#   * 证据独立性：同一混杂簇（周末+深夜+同一话题）内多条线索只计一次；
#     稳定倾向需 ≥3 个不相关情境方向一致；基线用个体内 z 分数（不用人群规范）；
#   * 露馅信号触发"自然解释清单"排除；排除不掉且重复 ≥3 次才入"待验证假设池"，
#     单次暴露永不输出；过程控制用 CUSUM 类方法而非单一阈值；
#   * 任何异常结论强制给"此判断可能不成立，因为…"；
#   * 更新步长受限：单次最多移动 k=1 个后验标准差；重大跨档改判需
#     多模型一致 + 跨场景证据 + 审计日志；
#   * 高功能/强一致性伪装：只"报告异常模式"，永不下人格判决；临床类只做倾向提示。
#
# 诚实声明：以下为纯 Python 确定性函数，不联网、不加载真实模型、不碰真实 DB。
# 现有 build_baseline / detect_anomalies / assess_risk 逻辑保持不变，这里只补门控。
# =========================================================================== #

#: CUSUM 过程控制参数（漂移灵敏度 h、上/下报警阈值）
CUSUM_DRIFT: float = 0.5      # 允许的正常漂移（单位：与 target 同尺度）
CUSUM_UPPER: float = 5.0     # 上侧报警阈值
CUSUM_LOWER: float = -5.0     # 下侧报警阈值

#: 露馅信号入"待验证假设池"所需的有效重复次数
HYPOTHESIS_MIN_REPEAT: int = 3

#: 稳定倾向所需的不相关情境方向数
MIN_INDEPENDENT_SITUATIONS: int = 3

#: 单次更新最多移动的后验标准差倍数 k
POSTERIOR_STEP_K: float = 1.0

#: 风险档序（跨档改判的"档"）
RISK_TIER_ORDER: tuple[str, ...] = ("低", "中", "高", "极高")


def individual_zscore(value: float, baseline_mean: float,
                      baseline_std: float) -> float:
    """个体内 z 分数（P-06：基线一律用个体内 z，不用人群规范）。

    std 为 0 或缺失时返回 0.0（无偏离）。
    """
    if baseline_std is None or baseline_std <= 0:
        return 0.0
    return (value - baseline_mean) / baseline_std


def cusum_update(s_hi: float, s_lo: float, value: float,
                 target: float, drift: float = CUSUM_DRIFT) -> tuple[float, float]:
    """双侧 CUSUM 单步更新（过程控制，替代单一阈值）。

    - s_hi 上侧累积：只记正向偏离；
    - s_lo 下侧累积：只记负向偏离。

    Returns:
        (new_s_hi, new_s_lo)。
    """
    s_hi = max(0.0, s_hi + (value - target) - drift)
    s_lo = min(0.0, s_lo + (value - target) + drift)
    return s_hi, s_lo


def cusum_alarm(s_hi: float, s_lo: float,
                upper: float = CUSUM_UPPER,
                lower: float = CUSUM_LOWER) -> Optional[str]:
    """CUSUM 是否报警。

    Returns:
        "up" / "down" / None。报警后调用方应自行重置对应侧累积。
    """
    if s_hi > upper:
        return "up"
    if s_lo < lower:
        return "down"
    return None


def confound_cluster_key(signal: dict) -> tuple:
    """混杂簇键：周末 + 深夜 + 同一话题。

    signal 字段：is_weekend(bool) / is_late_night(bool) / topic(str)。
    同键信号视为同一混杂簇，计数只计一次。
    """
    return (bool(signal.get("is_weekend")),
            bool(signal.get("is_late_night")),
            str(signal.get("topic", "")))


def dedupe_confounded_signals(signals: list[dict]) -> list[dict]:
    """同一混杂簇内多条线索只计一次（证据独立性：去冗余）。

    同簇只保留第一条代表信号，并把该簇命中条数记入 ``cluster_hits``。
    """
    seen: dict[tuple, dict] = {}
    order: list[tuple] = []
    for s in signals:
        key = confound_cluster_key(s)
        if key not in seen:
            rep = dict(s)
            rep["cluster_hits"] = 1
            seen[key] = rep
            order.append(key)
        else:
            seen[key]["cluster_hits"] += 1
    return [seen[k] for k in order]


def evidence_independence_gate(signals: list[dict],
                                min_dirs: int = MIN_INDEPENDENT_SITUATIONS
                                ) -> dict:
    """稳定倾向的证据独立性门控。

    先做混杂簇去重，再统计"不相关情境方向"数（signal['direction']）。
    只有 ≥ min_dirs 个方向一致指向"不一致/伪装"，才允许上升为稳定倾向；
    否则只能报"单/少数线索观察"，不得定性。

    Returns:
        dict: {cluster_count, distinct_dirs, directions, passes, blocked}。
    """
    deduped = dedupe_confounded_signals(signals)
    dirs = {str(s.get("direction", "")) for s in deduped
            if s.get("direction")}
    passes = len(dirs) >= min_dirs
    return {
        "cluster_count": len(deduped),
        "distinct_dirs": len(dirs),
        "directions": sorted(dirs),
        "passes": passes,
        "blocked": not passes,
    }


#: 自然解释清单（外部事件 / 设备 / 工作周期 / 时段变化）
NATURAL_EXPLAIN_TYPES: tuple[str, ...] = (
    "external_event",   # 外部事件：生病/家人变故/突发事务
    "device",           # 设备：手机坏了/没信号/换机
    "work_cycle",       # 工作周期：项目冲刺/出差/值班/季节性加班
    "timezone_shift",   # 时段变化：作息改变/倒时差/夏令时
)


def natural_excludes(signal: dict, external_events: list[dict]) -> bool:
    """判断某露馅信号是否被"自然解释清单"排除。

    若存在任一外部事件，其类型属于自然解释清单、且覆盖信号时间窗，
    则该信号判为"可自然解释"，不入伪装证据。

    Args:
        signal: {ts: float}。
        external_events: 每项 {type, start, end}（type ∈ NATURAL_EXPLAIN_TYPES）。

    Returns:
        True = 被自然解释排除；False = 排除不掉，需继续观察。
    """
    sig_ts = signal.get("ts")
    if sig_ts is None:
        return False
    for ev in external_events:
        if ev.get("type") not in NATURAL_EXPLAIN_TYPES:
            continue
        start, end = ev.get("start"), ev.get("end")
        if start is None or end is None:
            continue
        if start <= sig_ts <= end:
            return True
    return False


class HypothesisPool:
    """待验证假设池：露馅信号经自然解释排除后，重复 ≥3 次才入池；单次暴露永不输出。

    用法：每观测到一个露馅信号调用 :meth:`observe`。
      - 若可被自然解释 → 不计入有效重复；
      - 排除不掉但有效重复 < 3 → 不输出（仅内部计数）；
      - 排除不掉且有效重复 ≥ 3 → 入池（pending），方可作为"待验证假设"对外提示。
    """

    def __init__(self, min_repeat: int = HYPOTHESIS_MIN_REPEAT) -> None:
        self.min_repeat = min_repeat
        self._observed: dict[str, int] = {}
        self._explained: dict[str, int] = {}
        self._pool: list[dict] = []

    def observe(self, signal_type: str, explained_by_natural: bool) -> dict:
        """登记一次露馅信号观测。

        Returns:
            dict: {entered_pool, effective_repeat, reason}。
                  单次暴露 / 不足 min_repeat 一律 entered_pool=False。
        """
        self._observed[signal_type] = self._observed.get(signal_type, 0) + 1
        if explained_by_natural:
            self._explained[signal_type] = self._explained.get(signal_type, 0) + 1
            return {"entered_pool": False, "effective_repeat":
                    self._observed[signal_type] - self._explained[signal_type],
                    "reason": "已被自然解释排除"}

        effective = self._observed[signal_type] - self._explained.get(signal_type, 0)
        if effective >= self.min_repeat:
            self._pool.append({"signal_type": signal_type, "repeat": effective})
            return {"entered_pool": True, "effective_repeat": effective,
                    "reason": f"排除不掉且重复{effective}次，进入待验证假设池"}
        return {"entered_pool": False, "effective_repeat": effective,
                "reason": f"单次/有效重复{effective}<{self.min_repeat}，永不输出"}

    def pending(self) -> list[dict]:
        """当前已入池的待验证假设（去重后）。"""
        seen: dict[str, dict] = {}
        for item in self._pool:
            seen[item["signal_type"]] = item
        return list(seen.values())


def limit_posterior_step(current: float, proposed: float,
                         baseline_std: float,
                         k: float = POSTERIOR_STEP_K) -> dict:
    """更新步长受限：单次更新最多移动 k 个后验标准差，防反复横跳。

    Returns:
        dict: {current, proposed_uncapped, applied, moved_sigma, clamped}。
    """
    if baseline_std is None or baseline_std <= 0:
        # 无可靠尺度时不移动，保守固定
        return {"current": current, "proposed_uncapped": proposed,
                "applied": current, "moved_sigma": 0.0, "clamped": False}
    max_move = k * baseline_std
    delta = proposed - current
    clamped = abs(delta) > max_move
    applied = current + max(-max_move, min(max_move, delta))
    return {"current": current, "proposed_uncapped": proposed,
            "applied": applied, "moved_sigma": round(abs(applied - current) / baseline_std, 4),
            "clamped": clamped}


def cross_tier_requires_approval(current_level: str,
                                 proposed_level: str) -> dict:
    """重大跨档改判是否需要审批。

    跨档（低/中/高/极高 之间变化）即视为重大改判，需：
    多模型一致 + 跨场景证据齐备 + 审计日志。

    Returns:
        dict: {needs_approval, current_idx, proposed_idx}。
    """
    try:
        ci = RISK_TIER_ORDER.index(current_level)
    except ValueError:
        ci = -1
    try:
        pi = RISK_TIER_ORDER.index(proposed_level)
    except ValueError:
        pi = -1
    needs = (ci >= 0 and pi >= 0 and ci != pi)
    return {"needs_approval": needs, "current_idx": ci, "proposed_idx": pi}


def reverse_statement(conclusion: str, reasons: list[str]) -> str:
    """强制反向陈述：任何异常结论都要同时给"此判断可能不成立，因为…"。"""
    joined = "；".join(r for r in reasons if r) or ("证据仍不充分，"
                                                     "可能只是外部因素或短期波动")
    return f"{conclusion}（此判断可能不成立，因为：{joined}）"


def report_anomaly_only(findings: list[dict]) -> dict:
    """高功能/强一致性伪装下，只"报告异常模式"，永不下人格判决。

    Returns:
        dict: {anomaly_patterns, personality_verdict: None,
               clinical_note: '仅异常模式/倾向提示，不替代临床诊断，不下人格定论'}。
    """
    return {
        "anomaly_patterns": findings,
        "personality_verdict": None,
        "clinical_note": ("仅报告异常行为模式/倾向提示，不下人格判决，"
                          "不替代临床诊断"),
    }
