"""
LoveAI v3.0 - 好感度引擎（游戏化重构版）

v3.0 核心变化：
  1. 10 个分数区间（每区间 10 分），每个区间有明确的名称/含义/关系状态/
     解锁内容/对方典型表现/用户该做什么/区间风险。
  2. 好感度不再是黑箱：每次变化都写 affection_events 表，记录原因
     （"因为对方主动约你，亲密感+3"），UI 可直接展示变化历史。
  3. 进度可视化接口 get_progress()：当前分 / 当前区间 / 距下一阶段差多少 /
     推荐做什么 / 近 7/30 天趋势。
  4. 攻略成功不再是"刷到 100 分"：evaluate_courtship_success() 综合判定
     （80+ 稳定两周 + 对方主动发起 >40% + 投入比 0.7-1.3 + 冲突能修复）。
  5. 维度保持 18 维（旧 scores_json 完全兼容），但新增两个"派生元指标"
     （对方主动投入度 / 冲突修复力）用于成功判定与 UI，不写入 scores_json。
  6. 阶段跃迁写 affection_stage_log，便于勋章/任务/UI 高亮"升级了"。

向后兼容：
  - WEIGHTS / DIMENSION_NAMES 键集合与 v2 完全一致，旧 current_affection.scores_json
    可直接读取（缺维度补 50，多维度忽略）。
  - get_total / update_from_message / recalculate_from_history / apply_daily_decay /
    take_daily_snapshot / get_history / add_bonus / reset 签名不变。
  - get_stage() 返回值改为 0-9（对应 10 个区间），旧调用方 task_engine /
    strategy_advisor / digital_twin 均按区间语义自适应，无需改动。
"""
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.database import Database

logger = logging.getLogger(__name__)


class AffectionEngine:
    """好感度引擎（v3.0 游戏化版）。

    每个维度取值区间 [0, 100]，初始 50。
    总分 = 归一化加权平均 + 关系长度加成（封顶 +8），区间 [0, 100]。
    单日单维度累计变化上限 ±10；7 天无互动后高分维度每天向 50 缓慢回归 0.5%。
    """

    # ------------------------------------------------------------------ #
    # 18 维配置（v3.0：键集合与 v2 完全一致，权重仅微调 trust/security 权重，
    # 因为二者高度相关——security 本质是 trust 的外延——避免重复计分）
    # ------------------------------------------------------------------ #
    WEIGHTS: dict[str, float] = {
        "physical_attraction": 0.02,   # 外在吸引力（边缘·激情/外貌）
        "chat_resonance": 0.11,        # 聊天共鸣（核心·沟通）
        "humor": 0.02,                 # 幽默感（边缘）
        "thoughtfulness": 0.09,        # 体贴度（核心）
        "trust": 0.15,                 # 信任感（核心·最高权重）
        "respect": 0.06,               # 尊重感（次核心）
        "common_interest": 0.03,       # 共同兴趣（边缘）
        "emotional_value": 0.08,       # 情绪价值（核心）
        "future_alignment": 0.11,      # 未来规划契合（核心·承诺）
        "family": 0.02,                # 家庭关系（边缘）
        "spending": 0.02,              # 消费观念（边缘）
        "lifestyle": 0.02,            # 生活习惯（边缘）
        "independence": 0.03,          # 独立性（边缘）
        "security": 0.08,              # 安全感（核心·与 trust 重叠，v3.0 由 0.09→0.08）
        "growth": 0.03,                # 共同成长（边缘）
        "romance": 0.03,               # 浪漫感（边缘·激情）
        "emotional_stability": 0.06,   # 情绪稳定（次核心）
        "intimacy": 0.11,              # 亲密感（核心）
    }

    # 关系长度加成（封顶 +8）：抵消长期稳定关系的自然衰减
    RELATION_BONUS_TIERS: list[tuple[int, float]] = [
        (0, 0.0),
        (30, 2.0),
        (90, 4.0),
        (180, 6.0),
        (365, 8.0),
    ]

    DIMENSION_NAMES: dict[str, str] = {
        "physical_attraction": "外在吸引力",
        "chat_resonance": "聊天共鸣",
        "humor": "幽默感",
        "thoughtfulness": "体贴度",
        "trust": "信任感",
        "respect": "尊重感",
        "common_interest": "共同兴趣",
        "emotional_value": "情绪价值",
        "future_alignment": "未来规划契合",
        "family": "家庭关系",
        "spending": "消费观念",
        "lifestyle": "生活习惯",
        "independence": "独立性",
        "security": "安全感",
        "growth": "共同成长",
        "romance": "浪漫感",
        "emotional_stability": "情绪稳定",
        "intimacy": "亲密感",
    }

    DAILY_CAP: float = 10.0
    DECAY_AFTER_DAYS: int = 7
    # v3.0：衰减速度由 1% 降到 0.5%/天，且只衰减 >50 的维度（低分不自动回升，
    # 避免"躺平也能涨分"的不真实感；高分关系也不会因一周沉默就打回中性）。
    DECAY_RATIO: float = 0.005
    LLM_HINT_SCALE: float = 0.5

    # ------------------------------------------------------------------ #
    # R3.3-78 双轨指标：忠诚度（loyalty）独立维度
    #   语义 = 听话程度 / 遵从度 / 配合度（反制线主指标）。
    #   与 18 维好感度**并列存储在同一行 scores_json 的顶层 "loyalty" 键**，
    #   但独立计算、独立日衰减、独立单日上限，绝不并入 WEIGHTS 加权总分。
    # ------------------------------------------------------------------ #
    LOYALTY_KEY: str = "loyalty"
    LOYALTY_DEFAULT: float = 50.0
    # 忠诚度单日上限（与好感的 DAILY_CAP=10 区分：服从变化更缓）。
    LOYALTY_DAILY_CAP: float = 8.0
    # 忠诚度日衰减比例（独立于好感 DECAY_RATIO=0.005；习惯比好感更顽固，
    # 因此回归速度刻意设得与好感不同——0.012/天，仅衰减 >50 的高忠诚度）。
    LOYALTY_DECAY_RATIO: float = 0.012

    # 顺从/配合关键词（对方发来 = 对方听话/配合 → 忠诚上升）。
    _LOYALTY_COMPLY_KEYWORDS: tuple[str, ...] = (
        "听你的", "你说了算", "都听你的", "你决定", "我改", "我错了",
        "对不起", "遵命", "答应你", "下次不了", "好吧听你", "那我改",
    )
    # 违抗/对抗关键词（对方发来 = 拒绝遵从 → 忠诚下降）。
    _LOYALTY_DEFY_KEYWORDS: tuple[str, ...] = (
        "凭什么", "我凭什么", "不关你事", "你管不着", "就不", "我不改",
        "懒得理你", "关我屁事", "你算什么", "别指挥我",
    )

    # 当前路线（counter=反制线 / guide=引导线），内存态，由 facade 在拿到
    # manipulation 上下文时调 set_route 刷新；缺省 guide。
    ROUTES: tuple[str, ...] = ("counter", "guide")

    # ------------------------------------------------------------------ #
    # v3.0：10 个分数区间定义（每区间 10 分）
    # ------------------------------------------------------------------ #
    INTERVALS: list[dict[str, Any]] = [
        {
            "stage": 0, "min": 0.0, "max": 10.0, "name": "陌生人",
            "meaning": "完全没兴趣，或刚认识毫无印象",
            "relation_state": "无关系",
            "unlock": ["基础功能"],
            "typical_behavior": "礼貌客气，话题止于公事/寒暄",
            "user_action": "先做一次基础聊天分析，找到对方画像",
            "risk": "强行推进需求感暴露，直接被判死刑",
        },
        {
            "stage": 1, "min": 10.0, "max": 20.0, "name": "认识",
            "meaning": "知道有这个人，但谈不上好感",
            "relation_state": "普通认识",
            "unlock": ["基础聊天分析"],
            "typical_behavior": "回复简短礼貌，不主动延伸话题",
            "user_action": "维持稳定但低压力的聊天节奏，找一个共同话题",
            "risk": "过度热情/查户口，让对方觉得被冒犯",
        },
        {
            "stage": 2, "min": 20.0, "max": 30.0, "name": "有点印象",
            "meaning": "偶尔会想起这个人，不反感",
            "relation_state": "普通朋友边缘",
            "unlock": ["人物档案基础"],
            "typical_behavior": "会回消息，偶尔接梗",
            "user_action": "记住对方一个喜好，从公事聊到私事",
            "risk": "把'不反感'误读成'有意思'，过早表白",
        },
        {
            "stage": 3, "min": 30.0, "max": 40.0, "name": "不反感",
            "meaning": "愿意聊天，但不会主动找你",
            "relation_state": "普通朋友",
            "unlock": ["情感咨询基础"],
            "typical_behavior": "你发什么她接什么，但从不主动开启话题",
            "user_action": "分享一个自己的小故事，在对方低落时陪伴",
            "risk": "单方面高频率输出，把对方聊烦",
        },
        {
            "stage": 4, "min": 40.0, "max": 50.0, "name": "普通朋友",
            "meaning": "正常互动，无暧昧",
            "relation_state": "朋友",
            "unlock": ["任务系统基础"],
            "typical_behavior": "能聊兴趣/日常，偶有关心但克制",
            "user_action": "建立固定聊天时间，安排一次轻松的线下相处",
            "risk": "一直停在友谊区不敢升级，被发朋友卡",
        },
        {
            "stage": 5, "min": 50.0, "max": 60.0, "name": "有好感",
            "meaning": "暧昧初期，互有好感但未挑明",
            "relation_state": "暧昧初期",
            "unlock": ["策略适配矩阵"],
            "typical_behavior": "会主动找话题，语气变软，有小撒娇",
            "user_action": "制造独处机会，用行动表达关心，试探未来规划",
            "risk": "急于要一个'名分'，把暧昧吓跑",
        },
        {
            "stage": 6, "min": 60.0, "max": 70.0, "name": "暧昧中",
            "meaning": "互有好感，关系正在推进",
            "relation_state": "暧昧期",
            "unlock": ["限时任务", "关系类勋章"],
            "typical_behavior": "主动报备行程，吃醋，分享日常细节",
            "user_action": "规划一次共同出行，介绍对方进入自己圈子",
            "risk": "忽冷忽热让对方失去信心，或被第三方截胡",
        },
        {
            "stage": 7, "min": 70.0, "max": 80.0, "name": "热恋",
            "meaning": "确定关系，高浓度亲密",
            "relation_state": "热恋期",
            "unlock": ["深度功能", "高级勋章"],
            "typical_behavior": "高频率互动，公开秀恩爱，投入明显加大",
            "user_action": "维护日常相处质感，处理分歧先共情再解决",
            "risk": "热恋期滤镜下忽视价值观冲突，激情退去后崩盘",
        },
        {
            "stage": 8, "min": 80.0, "max": 90.0, "name": "深度绑定",
            "meaning": "稳定亲密，互相依赖",
            "relation_state": "稳定期",
            "unlock": ["关系维护功能"],
            "typical_behavior": "信任度高，能直接谈不满，冲突能修复",
            "user_action": "规划中长期生活，正式融入彼此家庭",
            "risk": "把稳定当理所当然，停止投入导致温水煮青蛙",
        },
        {
            "stage": 9, "min": 90.0, "max": 100.0, "name": "灵魂伴侣",
            "meaning": "双向奔赴的极致状态",
            "relation_state": "极致状态",
            "unlock": ["终极勋章", "隐藏功能"],
            "typical_behavior": "高度默契，彼此是默认选项",
            "user_action": "经营长期亲密关系，保持仪式感与新鲜感",
            "risk": "理论极限，现实中极难达到；追求 100 分会制造焦虑",
        },
    ]

    # 攻略成功判定阈值（v3.0 核心：成功 ≠ 100 分）
    SUCCESS_MIN_SCORE: float = 80.0       # 总分门槛
    SUCCESS_STABLE_DAYS: int = 14         # 连续稳定天数
    SUCCESS_STABLE_SWING: float = 10.0    # 这 14 天内最大波动 < 此值
    SUCCESS_PROACTIVE_RATIO: float = 0.40  # 对方主动发起对话占比 > 此值
    SUCCESS_INVEST_MIN: float = 0.7       # 投入比健康下限
    SUCCESS_INVEST_MAX: float = 1.3       # 投入比健康上限
    SUCCESS_REPAIR_DAYS: int = 3          # 冲突后 N 天内恢复视为可修复

    # 积极/消极情绪标签
    _POSITIVE_EMOTIONS = {"happy", "love", "excited", "joy", "开心", "喜爱", "兴奋"}
    _NEGATIVE_EMOTIONS = {"angry", "sad", "cold", "失望", "生气", "难过"}
    _POS_EMO_DIMS: dict[str, float] = {
        "emotional_value": 2.0, "intimacy": 1.5, "romance": 1.5, "chat_resonance": 1.5,
    }
    _NEG_EMO_DIMS: dict[str, float] = {
        "emotional_value": 2.0, "security": 1.5, "trust": 1.5,
    }

    _KEYWORD_RULES: dict[str, dict[str, float]] = {
        "想你": {"intimacy": 2.0, "emotional_value": 1.5},
        "开心": {"emotional_value": 1.5},
        "喜欢你": {"intimacy": 2.0, "trust": 1.5},
        "谢谢": {"respect": 1.5, "emotional_value": 1.0},
        "算了": {"emotional_value": -1.5, "security": -1.0},
        "随便": {"respect": -1.5, "emotional_value": -1.0},
        "不想聊": {"chat_resonance": -2.0, "emotional_value": -1.5},
        "别烦我": {"respect": -2.0, "security": -1.5},
        "我爱你": {"intimacy": 3.0, "emotional_value": 2.0, "romance": 2.0},
        "爱你": {"intimacy": 2.0, "emotional_value": 1.5, "romance": 1.5},
        "宝贝": {"intimacy": 1.5, "romance": 1.0},
        "老婆": {"intimacy": 2.0, "romance": 1.5, "future_alignment": 1.0},
        "老公": {"intimacy": 2.0, "romance": 1.5, "future_alignment": 1.0},
        "么么哒": {"intimacy": 2.0, "romance": 1.5},
        "晚安": {"emotional_value": 1.0, "intimacy": 0.5},
        "早安": {"emotional_value": 1.0, "intimacy": 0.5},
        "注意身体": {"emotional_value": 2.0, "trust": 1.5, "security": 1.5},
        "别太累": {"emotional_value": 2.0, "trust": 1.5},
        "记得吃饭": {"emotional_value": 2.0, "trust": 1.5, "security": 1.0},
        "点了外卖": {"emotional_value": 3.0, "trust": 2.0, "security": 2.0},
        "给你点": {"emotional_value": 2.5, "trust": 2.0},
        "怎么了": {"emotional_value": 1.5, "trust": 1.0, "chat_resonance": 1.0},
        "宝宝": {"intimacy": 1.5, "emotional_value": 1.0},
        "好呀": {"chat_resonance": 1.5, "emotional_value": 1.0},
        "一起": {"intimacy": 1.5, "future_alignment": 1.0},
        "约会": {"romance": 2.0, "intimacy": 1.5},
        "见面": {"intimacy": 1.5, "romance": 1.0},
        "不要脸": {"intimacy": 1.0, "romance": 0.5},
        "滚": {"intimacy": 0.5},
        "打你": {"intimacy": 1.0, "romance": 0.5},
        "油嘴滑舌": {"intimacy": 1.0, "romance": 0.5},
        "嗯": {"chat_resonance": -0.5},
        "哦哦": {"chat_resonance": -0.5},
        "呵呵": {"respect": -1.0, "emotional_value": -1.0},
        "随便你": {"respect": -1.5, "emotional_value": -1.0},
        "关你什么事": {"respect": -2.0, "trust": -1.5},
        "别管我": {"security": -1.5, "trust": -1.0},
    }

    _INVESTMENT_KEYWORDS = [
        "点了外卖", "给你点", "给你买", "帮你", "陪你", "等你",
        "接你", "送你", "给你做", "记得吃饭", "注意身体", "别太累",
        "早点睡", "多喝水", "穿多点", "小心",
    ]
    _PROACTIVE_LOVE_KEYWORDS = [
        "我爱你", "爱你", "想你", "想你了", "喜欢你", "么么哒",
        "宝贝", "宝宝", "晚安宝贝", "早安",
    ]
    _TSUNDERE_PATTERNS = [
        (["滚", "谁是你老婆", "不要脸"], ["打你", "表情", "哈哈", "继续聊天"]),
        (["油嘴滑舌"], ["继续聊天", "关心"]),
    ]

    # 重大事件（表白/确定关系/吵架/分手）的维度冲击表
    MILESTONE_IMPACTS: dict[str, dict[str, float]] = {
        "confess_love": {"intimacy": 8.0, "trust": 5.0, "emotional_value": 5.0,
                          "romance": 6.0, "future_alignment": 4.0},
        "confirm_relationship": {"intimacy": 10.0, "trust": 6.0, "future_alignment": 8.0,
                                 "emotional_value": 4.0, "security": 4.0},
        "quarrel": {"trust": -8.0, "security": -6.0, "emotional_value": -6.0,
                    "intimacy": -4.0},
        "cold_war": {"intimacy": -5.0, "chat_resonance": -4.0, "emotional_value": -3.0},
        "reconcile": {"trust": 5.0, "security": 5.0, "emotional_value": 6.0,
                      "intimacy": 3.0},
        "breakup": {"trust": -15.0, "intimacy": -12.0, "emotional_value": -10.0,
                    "security": -10.0, "future_alignment": -10.0},
    }

    def __init__(self, db: "Database") -> None:
        self.db = db
        # R3.3-78：当前对象路线（内存态），{person_id: "counter"|"guide"}。
        self._routes: dict[str, str] = {}
        # R3.3-81：分析→delta 推理层（替代旧"关键词→固定delta"机械打分）。
        # 懒加载导入，失败不影响引擎启动（退化为空报告=无变化）。
        self._reasoner = None
        try:
            from services.affection_reasoning import AffectionReasoner
            self._reasoner = AffectionReasoner()
        except Exception as e:  # noqa: BLE001
            logger.warning("AffectionReasoner 初始化失败，R3.3-81 推理层未启用: %s", e)
        if set(self.WEIGHTS.keys()) != set(self.DIMENSION_NAMES.keys()):
            logger.warning("WEIGHTS 与 DIMENSION_NAMES 维度集合不一致")
        logger.info(
            "AffectionEngine v3.0 初始化 维度数=%d 权重和=%.3f 区间数=%d",
            len(self.WEIGHTS), sum(self.WEIGHTS.values()), len(self.INTERVALS),
        )

    # ------------------------------------------------------------------ #
    # 基础读写
    # ------------------------------------------------------------------ #
    def get_current(self, person_id: str) -> dict[str, float]:
        scores: dict[str, float] = {dim: 50.0 for dim in self.WEIGHTS}
        row = self.db.fetch_one(
            "SELECT scores_json FROM current_affection WHERE person_id=:person_id",
            {"person_id": person_id},
        )
        stored: dict = {}
        if row and row.get("scores_json"):
            try:
                parsed = json.loads(row["scores_json"])
                if isinstance(parsed, dict):
                    stored = parsed
                    for dim in self.WEIGHTS:
                        if dim in stored and stored[dim] is not None:
                            scores[dim] = float(stored[dim])
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning("解析 current_affection 失败: %s", e)
        # R3.3-78：loyalty 是与 18 维并列的顶层独立键，缺省 50。
        # 注意：其余历史遗留杂键（如 obsolete_old_dim）一律不回吐，保持向后兼容。
        scores[self.LOYALTY_KEY] = self._clamp_value(
            stored.get(self.LOYALTY_KEY, self.LOYALTY_DEFAULT)
        )
        return scores

    @staticmethod
    def _clamp_value(v: float) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 50.0
        return max(0.0, min(100.0, f))

    def _clamp_scores(self, scores: dict[str, float]) -> dict[str, float]:
        return {dim: self._clamp_value(scores.get(dim, 50.0)) for dim in self.WEIGHTS}

    def _get_relation_bonus(self, person_id: str) -> float:
        first_at: Optional[str] = None
        row = self.db.fetch_one(
            "SELECT MIN(created_at) AS first_at FROM chat_messages WHERE person_id=:pid",
            {"pid": person_id},
        )
        if row and row.get("first_at"):
            first_at = row["first_at"]
        else:
            row = self.db.fetch_one(
                "SELECT created_at FROM persons WHERE id=:pid",
                {"pid": person_id},
            )
            if row and row.get("created_at"):
                first_at = row["created_at"]
        if not first_at:
            return 0.0
        first_dt = self._parse_dt(first_at)
        if first_dt is None:
            return 0.0
        days = (datetime.utcnow() - first_dt).days
        bonus = 0.0
        for threshold, b in self.RELATION_BONUS_TIERS:
            if days >= threshold:
                bonus = b
        return bonus

    def get_total_with_bonus(self, person_id: str, scores: dict[str, float]) -> float:
        base = self.get_total(scores)
        bonus = self._get_relation_bonus(person_id)
        return round(self._clamp_value(base + bonus), 1)

    def _save_current(self, person_id: str, scores: dict[str, float]) -> float:
        clamped = self._clamp_scores(scores)
        # R3.3-78：loyalty 随同一行 scores_json 落库，但不进 clamped 的加权统计。
        # 入参带 loyalty 就用入参值；没带（如历史重算路径）则保留库里已有值。
        loyalty = scores.get(self.LOYALTY_KEY)
        if loyalty is None:
            loyalty = self._read_stored_loyalty(person_id)
        clamped[self.LOYALTY_KEY] = self._clamp_value(loyalty)
        total = self.get_total_with_bonus(person_id, clamped)
        self.db.execute(
            "INSERT OR REPLACE INTO current_affection "
            "(person_id, scores_json, total_score, updated_at) "
            "VALUES (:person_id, :scores_json, :total_score, datetime('now'))",
            {
                "person_id": person_id,
                "scores_json": json.dumps(clamped, ensure_ascii=False),
                "total_score": total,
            },
        )
        return total

    def _read_stored_loyalty(self, person_id: str) -> float:
        """仅从库里读已有 loyalty 值，缺省 LOYALTY_DEFAULT（不新建好感记录）。"""
        row = self.db.fetch_one(
            "SELECT scores_json FROM current_affection WHERE person_id=:person_id",
            {"person_id": person_id},
        )
        if row and row.get("scores_json"):
            try:
                stored = json.loads(row["scores_json"])
                if isinstance(stored, dict) and stored.get(self.LOYALTY_KEY) is not None:
                    return self._clamp_value(stored[self.LOYALTY_KEY])
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        return self.LOYALTY_DEFAULT

    # ------------------------------------------------------------------ #
    # v3.0：变化原因日志
    # ------------------------------------------------------------------ #
    def _log_event(
        self,
        person_id: str,
        dim: str,
        delta: float,
        reason: str,
        total_before: float,
        total_after: float,
        event_type: str = "message",
    ) -> None:
        """把一次好感度变化写入 affection_events（游戏化反馈的核心数据源）。"""
        try:
            self.db.execute(
                "INSERT INTO affection_events "
                "(person_id, changed_at, dim, delta, reason, "
                " total_before, total_after, stage_before, stage_after, event_type) "
                "VALUES (:pid, datetime('now'), :dim, :delta, :reason, "
                " :tb, :ta, :sb, :sa, :et)",
                {
                    "pid": person_id, "dim": dim, "delta": round(delta, 2),
                    "reason": reason[:300],
                    "tb": round(total_before, 1), "ta": round(total_after, 1),
                    "sb": self.get_stage(total_before),
                    "sa": self.get_stage(total_after),
                    "et": event_type,
                },
            )
        except Exception as e:
            logger.debug("写 affection_events 失败（忽略）: %s", e)

    def _maybe_log_stage_up(
        self, person_id: str, total_before: float, total_after: float
    ) -> None:
        """若本次变化跨过了区间边界，写一条 affection_stage_log。"""
        sb, sa = self.get_stage(total_before), self.get_stage(total_after)
        if sb != sa:
            try:
                self.db.execute(
                    "INSERT INTO affection_stage_log "
                    "(person_id, stage_from, stage_to, score_at) "
                    "VALUES (:pid, :sf, :st, :sc)",
                    {"pid": person_id, "sf": sb, "st": sa, "sc": round(total_after, 1)},
                )
                logger.info(
                    "好感度阶段跃迁 person=%s %s→%s (%.1f→%.1f)",
                    person_id, sb, sa, total_before, total_after,
                )
            except Exception as e:
                logger.debug("写 affection_stage_log 失败: %s", e)

    @staticmethod
    def _build_reasons(
        message: dict,
        emotion: Optional[dict],
        analysis: Optional[dict],
        applied: dict[str, float],
    ) -> list[str]:
        """根据本次消息/情绪/大模型提示，生成人类可读的变化原因列表。"""
        reasons: list[str] = []
        content = (message.get("content", "") or "") if isinstance(message, dict) else ""
        direction = message.get("direction", "received") if isinstance(message, dict) else "received"
        emotion = emotion or {}
        label = str(emotion.get("label", "") or "")
        try:
            intensity = float(emotion.get("intensity", 0.5))
        except (TypeError, ValueError):
            intensity = 0.5

        # 情绪原因
        if direction == "received" and label:
            if label in AffectionEngine._POSITIVE_EMOTIONS:
                reasons.append(f"对方发来积极情绪「{label}」（强度{intensity:.0%}），情绪价值/亲密感提升")
            elif label in AffectionEngine._NEGATIVE_EMOTIONS:
                reasons.append(f"对方发来消极情绪「{label}」（强度{intensity:.0%}），安全感/信任受挫")

        # 关键词原因（只挑实际触发的）
        triggered_kw: list[str] = []
        for kw, dims in AffectionEngine._KEYWORD_RULES.items():
            if kw and kw in content:
                # 只记录有实际变化的维度
                real = [d for d, dv in dims.items() if abs(applied.get(d, 0.0)) >= 0.3]
                if real:
                    triggered_kw.append(kw)
        if triggered_kw:
            # 去重保序
            seen = []
            for k in triggered_kw:
                if k not in seen:
                    seen.append(k)
            reasons.append("关键词触发：" + "、".join(f"「{k}」" for k in seen[:5]))

        # 大模型提示
        hints = (analysis or {}).get("affection_hints") or {}
        if isinstance(hints, dict) and hints:
            top = sorted(hints.items(), key=lambda kv: -abs(float(kv[1] or 0)))[:3]
            desc = "，".join(f"{AffectionEngine.DIMENSION_NAMES.get(d, d)}{('+' if float(v) >= 0 else '')}{float(v):.1f}"
                            for d, v in top)
            reasons.append(f"大模型评价：{desc}")

        # 兜底
        if not reasons:
            moved = [(d, v) for d, v in applied.items() if abs(v) >= 0.3]
            if moved:
                d, v = moved[0]
                reasons.append(f"互动微调：{AffectionEngine.DIMENSION_NAMES.get(d, d)} {'+' if v>=0 else ''}{v:.1f}")
            else:
                reasons.append("正常互动，无显著变化")
        return reasons

    # ------------------------------------------------------------------ #
    # 实时更新
    # ------------------------------------------------------------------ #
    def update_from_message(
        self,
        person_id: str,
        message: dict,
        emotion: Optional[dict] = None,
        analysis: Optional[dict] = None,
    ) -> dict:
        emotion = emotion or {}
        analysis = analysis or {}

        scores = self.get_current(person_id)
        total_before = self.get_total_with_bonus(person_id, scores)
        today = date.today().isoformat()
        baseline = self._get_today_baseline(person_id, today, scores)

        sub_msgs = self._split_messages(message)

        # R3.3-81：先做"分析→内心状态进度报告"（情绪+意图+关系姿态推断→带证据链），
        # 再由报告映射到各维度 delta。彻底废除"字面关键词→固定delta"的机械打分。
        report = self._build_reasoning_report(sub_msgs, emotion, analysis)
        reasoned_dims: dict[str, float] = report.get("dimensions", {}) or {}
        reasoned_loyalty: float = float(report.get("loyalty_delta", 0.0) or 0.0)

        new_scores: dict[str, float] = {}
        applied: dict[str, float] = {}

        # R3.3-78：好感循环只遍历 18 个好感维，loyalty 不参与此循环。
        for dim in self.WEIGHTS:
            old = scores[dim]
            # delta 来自推理层报告，不再是关键词机械打分
            raw = reasoned_dims.get(dim, 0.0)
            base = baseline.get(dim, old)
            cumulative = old - base
            remaining_pos = self.DAILY_CAP - cumulative
            remaining_neg = -self.DAILY_CAP - cumulative
            clamped = max(remaining_neg, min(remaining_pos, raw))
            clamped = max(-self.DAILY_CAP, min(self.DAILY_CAP, clamped))
            new_val = self._clamp_value(old + clamped)
            new_scores[dim] = round(new_val, 2)
            applied[dim] = round(new_val - old, 2)

        # R3.3-78：忠诚度独立增量（独立权重表 / 独立单日上限），不并入好感 applied。
        old_loyalty = self._clamp_value(scores.get(self.LOYALTY_KEY, self.LOYALTY_DEFAULT))
        loyalty_raw = reasoned_loyalty
        loyalty_delta = max(-self.LOYALTY_DAILY_CAP,
                            min(self.LOYALTY_DAILY_CAP, loyalty_raw))
        new_loyalty = round(self._clamp_value(old_loyalty + loyalty_delta), 2)
        new_scores[self.LOYALTY_KEY] = new_loyalty

        total_after = self._save_current(person_id, new_scores)
        stage = self.get_stage(total_after)

        # v3.0：写变化原因 + 阶段跃迁
        # R3.3-81：优先用推理层证据链（"她说X→推断她内心Y→Z维度Δ"）作为原因，
        # 无报告时回退旧 _build_reasons。
        reasons = self._build_reasons_from_report(report, applied)
        total_delta = round(total_after - total_before, 1)
        self._log_event(
            person_id, "__total__", total_delta,
            "；".join(reasons), total_before, total_after, event_type="message",
        )
        self._maybe_log_stage_up(person_id, total_before, total_after)

        logger.debug(
            "好感度更新 person=%s total=%.1f→%.1f stage=%d 变化维度=%d",
            person_id, total_before, total_after, stage,
            sum(1 for v in applied.values() if abs(v) > 1e-6),
        )
        return {
            "scores": new_scores,
            "total_score": total_after,
            "stage": stage,
            "applied": applied,
            "reasons": reasons,
            "total_before": total_before,
            "total_after": total_after,
            "loyalty": new_loyalty,
            "loyalty_delta": round(loyalty_delta, 2),
            "reasoning_report": report,
        }

    # ------------------------------------------------------------------ #
    # R3.3-81：分析→delta 推理层对接
    # ------------------------------------------------------------------ #
    def _build_reasoning_report(
        self,
        sub_msgs: list[dict],
        emotion: Optional[dict],
        analysis: Optional[dict],
    ) -> dict:
        """调用 AffectionReasoner 产出内心状态进度报告（带证据链）。

        推理层不可用时返回空报告（delta 全 0），不抛栈、不影响落库。
        """
        if self._reasoner is None:
            return {"dimensions": {}, "loyalty_delta": 0.0, "evidence": [],
                    "conclusions": [], "summary": "推理层未启用，无变化"}
        try:
            return self._reasoner.build_report(sub_msgs, emotion or {}, analysis or {})
        except Exception as e:  # noqa: BLE001
            logger.warning("AffectionReasoner.build_report 失败，按无变化处理: %s", e)
            return {"dimensions": {}, "loyalty_delta": 0.0, "evidence": [],
                    "conclusions": [], "summary": f"推理失败:{e}"}

    def analyze_message(self, message: dict,
                        emotion: Optional[dict] = None) -> dict:
        """对外可调用：对一条消息跑完整分析→内心状态进度报告（供展示/测试）。"""
        sub_msgs = self._split_messages(message)
        return self._build_reasoning_report(sub_msgs, emotion, {})

    @staticmethod
    def _build_reasons_from_report(report: Optional[dict],
                                   applied: dict[str, float]) -> list[str]:
        """把推理层证据链转成人类可读原因列表（可复核、可展示）。"""
        report = report or {}
        reasons: list[str] = []
        summary = report.get("summary")
        if summary:
            reasons.append(summary)
        # 挑实际有变化的维度证据，最多 4 条，避免刷屏
        evs = report.get("evidence") or []
        dim_evs = [e for e in evs if isinstance(e, dict)
                   and "→" in str(e.get("text", ""))
                   and "→" in str(e.get("text", ""))]
        picked = 0
        seen = set()
        for e in dim_evs:
            txt = str(e.get("text", ""))
            # 用"→Z维度"后的尾段去重，同一维度只展示最强一条
            tail = txt.split("→")[-1]
            key = tail.split(" ")[0]
            if key in seen:
                continue
            seen.add(key)
            reasons.append(txt[:120])
            picked += 1
            if picked >= 4:
                break
        if not reasons:
            moved = [(d, v) for d, v in applied.items() if abs(v) >= 0.3]
            if moved:
                d, v = moved[0]
                reasons.append(f"互动微调：{AffectionEngine.DIMENSION_NAMES.get(d, d)} "
                               f"{'+' if v>=0 else ''}{v:.1f}")
            else:
                reasons.append("分析结论：无真实关系进展，分数未做机械加减")
        return reasons

    def _calc_loyalty_delta(
        self,
        sub_msgs: list[dict],
        emotion: Optional[dict] = None,
        analysis: Optional[dict] = None,
    ) -> float:
        """R3.3-78：忠诚度增量（R3.3-81 改为由推理层姿态结论驱动）。

        语义 = 对方对用户指令/要求的遵从度变化。只统计对方发来的消息。
        旧版"顺从/违抗关键词→固定分"已废除，改由 AffectionReasoner 推断
        "顺从配合/违抗对抗"姿态后产出（带证据链）。
        """
        if not sub_msgs:
            return 0.0
        # R3.3-81：改由推理层"顺从/违抗姿态"结论驱动（带证据链），
        # 不再做字面关键词加减。推理层不可用时返回 0。
        report = self._build_reasoning_report(sub_msgs, emotion, analysis)
        return float(report.get("loyalty_delta", 0.0) or 0.0)

    def _split_messages(self, message) -> list[dict]:
        if isinstance(message, (list, tuple)):
            out = [m for m in message if isinstance(m, dict)]
            return out or [{"content": "", "direction": "received"}]
        if not isinstance(message, dict):
            return [{"content": str(message or ""), "direction": "received"}]
        content = message.get("content", "") or ""
        if "\n" in content:
            lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
            if len(lines) > 1:
                return [dict(message, content=ln) for ln in lines]
        return [message]

    def _calc_delta_weighted(self, dim, sub_msgs, emotion=None, analysis=None) -> float:
        """R3.3-81：某 18 维的建议增量——改由推理层报告给出。

        保留旧签名以兼容可能的外部调用；不再做关键词机械打分。
        """
        if not sub_msgs:
            return 0.0
        report = self._build_reasoning_report(sub_msgs, emotion, analysis)
        return float((report.get("dimensions") or {}).get(dim, 0.0))

    def recalculate_from_history(self, person_id: str) -> dict:
        messages = self.db.fetch_all(
            "SELECT direction, content, msg_type FROM chat_messages "
            "WHERE person_id=:pid ORDER BY id ASC",
            {"pid": person_id},
        )
        if not messages:
            return {"scores": self.get_current(person_id), "total_score": 50.0,
                    "stage": 0, "analysis": {"error": "无聊天记录"}}

        scores = {dim: 50.0 for dim in self.DIMENSION_NAMES}
        analysis = {
            "total_messages": len(messages), "sent_count": 0, "received_count": 0,
            "proactive_love": 0, "investment_acts": 0, "tsundere_moments": 0,
            "conversation_starts": 0, "perfunctory_count": 0,
        }

        prev_direction = None
        for i, msg in enumerate(messages):
            direction = msg["direction"]
            content = msg.get("content", "") or ""
            msg_type = msg.get("msg_type", "text")

            if direction == "sent":
                analysis["sent_count"] += 1
            else:
                analysis["received_count"] += 1

            if direction == "received" and prev_direction != "received":
                if i == 0 or messages[i-1]["direction"] == "sent":
                    analysis["conversation_starts"] += 1

            if direction == "received":
                is_proactive = False
                if i > 0 and messages[i-1]["direction"] == "sent":
                    prev_content = messages[i-1].get("content", "") or ""
                    demand_patterns = ["说爱我", "想我吗", "叫我", "说想我", "亲我"]
                    if not any(p in prev_content for p in demand_patterns):
                        is_proactive = True
                else:
                    is_proactive = True
                if is_proactive:
                    for kw in self._PROACTIVE_LOVE_KEYWORDS:
                        if kw in content:
                            analysis["proactive_love"] += 1
                            scores["intimacy"] = min(100, scores["intimacy"] + 1.5)
                            scores["emotional_value"] = min(100, scores["emotional_value"] + 1.0)
                            scores["romance"] = min(100, scores["romance"] + 1.0)
                            break

            if direction == "received":
                for kw in self._INVESTMENT_KEYWORDS:
                    if kw in content:
                        analysis["investment_acts"] += 1
                        scores["trust"] = min(100, scores["trust"] + 2.0)
                        scores["security"] = min(100, scores["security"] + 1.5)
                        scores["emotional_value"] = min(100, scores["emotional_value"] + 1.5)
                        break

            if direction == "received":
                tsundere_words = ["滚", "不要脸", "谁是你老婆", "油嘴滑舌", "打你"]
                if any(w in content for w in tsundere_words):
                    is_tsundere = False
                    if i + 1 < len(messages):
                        next_msg = messages[i+1]
                        next_content = next_msg.get("content", "") or ""
                        if next_msg["direction"] == "received" or "表情" in next_content or "哈哈" in next_content:
                            is_tsundere = True
                    if msg_type in ("image", "animation", "sticker") or "表情" in content:
                        is_tsundere = True
                    if len(content) <= 10 and any(w in content for w in ["滚", "不要脸", "打你"]):
                        is_tsundere = True
                    if is_tsundere:
                        analysis["tsundere_moments"] += 1
                        scores["intimacy"] = min(100, scores["intimacy"] + 1.0)
                        scores["romance"] = min(100, scores["romance"] + 0.5)
                        scores["chat_resonance"] = min(100, scores["chat_resonance"] + 0.5)

            if direction == "received" and len(content) <= 3:
                if content in ("嗯", "哦", "哦哦", "呵呵", "。。。"):
                    analysis["perfunctory_count"] += 1
                    scores["chat_resonance"] = max(0, scores["chat_resonance"] - 0.5)

            for kw, dims in self._KEYWORD_RULES.items():
                if kw in content:
                    for dim, delta in dims.items():
                        factor = 1.0 if direction == "received" else 0.3
                        scores[dim] = max(0, min(100, scores[dim] + delta * factor))
            prev_direction = direction

        total = len(messages)
        if total > 0:
            other_ratio = analysis["received_count"] / total
            if other_ratio > 0.5:
                scores["emotional_value"] = min(100, scores["emotional_value"] + (other_ratio - 0.5) * 10)
                scores["intimacy"] = min(100, scores["intimacy"] + (other_ratio - 0.5) * 5)
            if analysis["conversation_starts"] > 0:
                start_ratio = analysis["conversation_starts"] / max(1, total // 5)
                scores["emotional_value"] = min(100, scores["emotional_value"] + min(5, start_ratio * 3))
                scores["security"] = min(100, scores["security"] + min(3, start_ratio * 2))
            if analysis["investment_acts"] > 0:
                scores["trust"] = min(100, scores["trust"] + analysis["investment_acts"] * 1.0)
                scores["future_alignment"] = min(100, scores["future_alignment"] + analysis["investment_acts"] * 0.5)
            if analysis["tsundere_moments"] > 0:
                scores["romance"] = min(100, scores["romance"] + analysis["tsundere_moments"] * 0.8)
                scores["humor"] = min(100, scores["humor"] + analysis["tsundere_moments"] * 0.5)

        total_score = self._save_current(person_id, scores)
        stage = self.get_stage(total_score)
        try:
            self.db.execute(
                "UPDATE persons SET relationship_stage=:stage WHERE id=:id",
                {"stage": stage, "id": person_id},
            )
        except Exception:
            pass

        logger.info(
            "好感度重算 person=%s total=%.1f stage=%d", person_id, total_score, stage,
        )
        return {"scores": scores, "total_score": total_score, "stage": stage, "analysis": analysis}

    def _get_today_baseline(self, person_id, today, current_scores):
        row = self.db.fetch_one(
            "SELECT scores_json FROM affection_snapshots "
            "WHERE person_id=:person_id AND date=:today ORDER BY id DESC LIMIT 1",
            {"person_id": person_id, "today": today},
        )
        if row and row.get("scores_json"):
            try:
                stored = json.loads(row["scores_json"])
                if isinstance(stored, dict):
                    return stored
            except (json.JSONDecodeError, TypeError):
                pass
        return dict(current_scores)

    # v3.2 校准：我方消息对好感度的影响系数（我方示好不等于对方更喜欢你）
    SENT_KEYWORD_FACTOR: float = 0.2
    # 反向傲娇词（单独出现=真凶，搭配 playful 标记=撒娇）
    _TSUNDERE_HARD_WORDS = ("滚", "不要脸", "谁是你老婆", "油嘴滑舌", "打你", "讨厌")
    _TSUNDERE_PLAYFUL_MARKERS = ("哈哈", "哈哈哈", "表情", "~", "～", "呀", "啦", "哦", "😊", "😘", "😍", "🤗", "😳")
    # 追问/反问词（对方愿意接话=聊天共鸣正向）
    _QUESTION_BACK_MARKERS = ("吗", "呢", "？", "?", "怎么", "为啥", "为什么", "真的假的", "然后呢")
    # 敷衍词（短回复且无追问=冷处理）
    _COLD_SHORT_MARKERS = ("嗯", "哦", "哦哦", "呵呵", "。。。", "...", "额", "呃")

    def _is_tsundere_playful(self, content: str) -> bool:
        """判断一句带'滚/讨厌/打你'的消息是傲娇撒娇还是真发火。"""
        if not any(w in content for w in self._TSUNDERE_HARD_WORDS):
            return False
        # 带 playful 标记 = 撒娇（哈哈/表情/~/呀/啦/颜文字）
        if any(m in content for m in self._TSUNDERE_PLAYFUL_MARKERS):
            return True
        # 单独一个"滚/不要脸/讨厌"且无任何软标记 = 真发火（不是傲娇）
        return False

    def _calc_delta(self, dim, msg, emotion=None, analysis=None) -> float:
        """R3.3-81：单维增量——改由推理层"内心状态进度"报告给出。

        保留旧签名兼容外部调用。旧"关键词→固定delta"机械打分已废除：
        现在 delta = 情绪 + 意图 + 关系姿态 推断后的合成结果（带证据链），
        同一关键词在不同语境下 delta 不同。
        """
        report = self._build_reasoning_report([msg], emotion, analysis)
        return float((report.get("dimensions") or {}).get(dim, 0.0))

    # ------------------------------------------------------------------ #
    # 总分 / 区间 / 阶段
    # ------------------------------------------------------------------ #
    def get_total(self, scores: dict[str, float]) -> float:
        weight_sum = sum(self.WEIGHTS.values())
        if weight_sum <= 0:
            return 50.0
        total = sum(scores.get(dim, 50.0) * w for dim, w in self.WEIGHTS.items())
        return round(total / weight_sum, 1)

    @classmethod
    def get_interval(cls, total_score: float) -> dict[str, Any]:
        """根据总分返回完整区间定义 dict（v3.0 新增）。"""
        for iv in cls.INTERVALS:
            if iv["min"] <= total_score < iv["max"] or (iv["max"] >= 100.0 and total_score >= iv["max"] - 1e-9):
                return iv
        return cls.INTERVALS[-1]

    @classmethod
    def get_stage(cls, total_score: float) -> int:
        """v3.0：按 10 分一档返回阶段 0-9。

        <10→0 陌生人；<20→1 认识；<30→2 有点印象；<40→3 不反感；
        <50→4 普通朋友；<60→5 有好感；<70→6 暧昧中；<80→7 热恋；
        <90→8 深度绑定；>=90→9 灵魂伴侣。
        """
        if total_score < 10:
            return 0
        if total_score < 20:
            return 1
        if total_score < 30:
            return 2
        if total_score < 40:
            return 3
        if total_score < 50:
            return 4
        if total_score < 60:
            return 5
        if total_score < 70:
            return 6
        if total_score < 80:
            return 7
        if total_score < 90:
            return 8
        return 9

    # ------------------------------------------------------------------ #
    # 衰减 / 快照 / 历史
    # ------------------------------------------------------------------ #
    def apply_daily_decay(self, person_id: str) -> None:
        row = self.db.fetch_one(
            "SELECT created_at FROM chat_messages "
            "WHERE person_id=:person_id ORDER BY created_at DESC LIMIT 1",
            {"person_id": person_id},
        )
        if not row or not row.get("created_at"):
            return
        last_dt = self._parse_dt(row["created_at"])
        if last_dt is None:
            return
        days_silent = (datetime.utcnow() - last_dt).days
        if days_silent < self.DECAY_AFTER_DAYS:
            return

        scores = self.get_current(person_id)
        total_before = self.get_total_with_bonus(person_id, scores)
        new_scores: dict[str, float] = {}
        # R3.3-78：好感衰减只遍历 18 维；loyalty 走独立衰减率，分开处理。
        for dim in self.WEIGHTS:
            score = scores[dim]
            # v3.0：只让 >50 的维度向 50 回归，低分不自动回升
            if score > 50.0:
                v = score + (50.0 - score) * self.DECAY_RATIO
            else:
                v = score
            new_scores[dim] = round(self._clamp_value(v), 2)
        # 忠诚度独立衰减（比例 LOYALTY_DECAY_RATIO，与好感 0.005 不同）。
        old_loy = scores.get(self.LOYALTY_KEY, self.LOYALTY_DEFAULT)
        if old_loy > 50.0:
            new_loy = old_loy + (50.0 - old_loy) * self.LOYALTY_DECAY_RATIO
        else:
            new_loy = old_loy
        new_scores[self.LOYALTY_KEY] = round(self._clamp_value(new_loy), 2)
        total_after = self._save_current(person_id, new_scores)
        self._log_event(
            person_id, "__total__", round(total_after - total_before, 1),
            f"沉默{days_silent}天，高分维度缓慢向中性回归{self.DECAY_RATIO*100:.1f}%",
            total_before, total_after, event_type="decay",
        )
        logger.info("好感度衰减 person=%s 沉默%d天", person_id, days_silent)

    def take_daily_snapshot(self, person_id: str) -> None:
        scores = self.get_current(person_id)
        total = self.get_total_with_bonus(person_id, scores)
        today = date.today().isoformat()
        self.db.execute(
            "DELETE FROM affection_snapshots WHERE person_id=:person_id AND date=:today",
            {"person_id": person_id, "today": today},
        )
        self.db.execute(
            "INSERT INTO affection_snapshots (person_id, date, scores_json, total_score) "
            "VALUES (:person_id, :today, :scores_json, :total_score)",
            {
                "person_id": person_id, "today": today,
                "scores_json": json.dumps(scores, ensure_ascii=False),
                "total_score": total,
            },
        )

    def get_history(self, person_id: str, days: int = 30) -> list[dict]:
        rows = self.db.fetch_all(
            "SELECT id, person_id, date, scores_json, total_score, created_at "
            "FROM affection_snapshots WHERE person_id=:person_id "
            "ORDER BY date DESC, id DESC LIMIT :days",
            {"person_id": person_id, "days": days},
        )
        for row in rows:
            row["scores"] = {}
            if row.get("scores_json"):
                try:
                    row["scores"] = json.loads(row["scores_json"])
                except (json.JSONDecodeError, TypeError):
                    pass
        return rows

    # ------------------------------------------------------------------ #
    # v3.0：游戏化反馈接口（进度 / 变化历史 / 推荐 / 趋势）
    # ------------------------------------------------------------------ #
    def get_recent_events(self, person_id: str, limit: int = 20) -> list[dict]:
        """最近 N 条好感度变化事件（含原因），按时间倒序。"""
        try:
            rows = self.db.fetch_all(
                "SELECT id, changed_at, dim, delta, reason, total_before, "
                " total_after, stage_before, stage_after, event_type "
                "FROM affection_events WHERE person_id=:pid "
                "ORDER BY id DESC LIMIT :lim",
                {"pid": person_id, "lim": limit},
            )
            return rows or []
        except Exception as e:
            logger.debug("读 affection_events 失败: %s", e)
            return []

    def get_progress(self, person_id: str) -> dict[str, Any]:
        """UI 友好的进度数据：当前分/区间/距下一档差多少/推荐动作/趋势。"""
        scores = self.get_current(person_id)
        total = self.get_total_with_bonus(person_id, scores)
        iv = self.get_interval(total)
        stage = iv["stage"]

        # 距下一区间
        if stage < len(self.INTERVALS) - 1:
            next_iv = self.INTERVALS[stage + 1]
            points_to_next = round(next_iv["min"] - total, 1)
            next_name = next_iv["name"]
        else:
            next_iv = None
            points_to_next = 0.0
            next_name = "已是最高区间"

        # 当前区间内进度（0-1）
        span = iv["max"] - iv["min"]
        progress_in = round((total - iv["min"]) / span, 2) if span > 0 else 1.0

        # 近 7/30 天趋势
        hist = self.get_history(person_id, days=30)
        trend_7d = [h["total_score"] for h in reversed(hist[:7])]
        trend_30d = [h["total_score"] for h in reversed(hist[:30])]

        return {
            "total_score": total,
            "stage": stage,
            "interval": iv,
            "interval_name": iv["name"],
            "meaning": iv["meaning"],
            "relation_state": iv["relation_state"],
            "typical_behavior": iv["typical_behavior"],
            "user_action": iv["user_action"],
            "risk": iv["risk"],
            "unlock": iv["unlock"],
            "points_to_next": points_to_next,
            "next_interval_name": next_name,
            "progress_in_interval": max(0.0, min(1.0, progress_in)),
            "progress_bar": {
                "current": total,
                "interval_start": iv["min"],
                "interval_end": iv["max"],
                "next_threshold": next_iv["min"] if next_iv else iv["max"],
            },
            "recommended_actions": self.get_recommended_actions(total),
            "recent_events": self.get_recent_events(person_id, limit=10),
            "trend_7d": trend_7d,
            "trend_30d": trend_30d,
            "scores": scores,
        }

    def get_recommended_actions(self, total_score: float) -> list[str]:
        """根据当前区间返回推荐动作（直接来自区间定义）。"""
        iv = self.get_interval(total_score)
        return [iv["user_action"], f"注意本阶段风险：{iv['risk']}"]

    # ------------------------------------------------------------------ #
    # v3.0：双向奔赴 / 投入比 / 冲突修复 派生指标
    # ------------------------------------------------------------------ #
    # 两次消息间隔超过此值（分钟）算开启新会话
    SESSION_GAP_MINUTES: int = 30

    def get_proactive_ratio(self, person_id: str) -> float:
        """对方主动发起对话的会话占比（0-1）。

        语义：把消息按时间分组为"会话"——两条消息间隔超过 30 分钟就视为新会话。
        每个会话的第一条消息由谁发出，就记谁"主动开了这场对话"。
        对方主动率 = 对方开启的会话数 / 总会话数。

        这样"我连发 100 条后对方回 1 条"只会被算作 1 个会话，且开这场会话的是我，
        不会再误判为对方主动。
        """
        rows = self.db.fetch_all(
            "SELECT direction, created_at FROM chat_messages "
            "WHERE person_id=:pid ORDER BY id ASC",
            {"pid": person_id},
        )
        if not rows:
            return 0.0
        session_openers: list[str] = []
        last_dt = None
        gap = timedelta(minutes=self.SESSION_GAP_MINUTES)
        for r in rows:
            d = r["direction"]
            dt = self._parse_dt(r.get("created_at") or "")
            new_session = True
            if dt is not None and last_dt is not None:
                delta = dt - last_dt
                if timedelta(0) <= delta < gap:
                    new_session = False
            if new_session:
                session_openers.append(d)
            if dt is not None:
                last_dt = dt
        if not session_openers:
            return 0.0
        other_openers = sum(1 for d in session_openers if d == "received")
        return round(other_openers / len(session_openers), 2)

    def get_investment_balance(self, person_id: str) -> float:
        """双方投入比 = 对方消息数 / 我方消息数（>1 对方投入多，<1 我方投入多）。

        健康区间 [0.7, 1.3]。
        """
        rows = self.db.fetch_all(
            "SELECT direction, COUNT(*) AS c FROM chat_messages WHERE person_id=:pid GROUP BY direction",
            {"pid": person_id},
        )
        sent = received = 0
        for r in (rows or []):
            if r["direction"] == "sent":
                sent = int(r["c"])
            else:
                received = int(r["c"])
        if sent <= 0:
            return 9.9 if received > 0 else 1.0
        return round(received / sent, 2)

    def _conflict_repaired(self, person_id: str) -> bool:
        """检查近 30 天内是否出现过"大跌后 N 天内恢复"。

        规则：从 affection_snapshots 找相邻快照差 < -8（一次冲突/大跌），
        检查之后 SUCCESS_REPAIR_DAYS 天内是否回弹到大跌前的 -3 分以内。
        若无任何大跌事件，返回 True（无需修复即视为健康）。
        """
        hist = self.db.fetch_all(
            "SELECT date, total_score FROM affection_snapshots WHERE person_id=:pid "
            "ORDER BY date ASC LIMIT 60",
            {"pid": person_id},
        )
        if len(hist) < 3:
            return True
        for i in range(1, len(hist)):
            drop = hist[i]["total_score"] - hist[i-1]["total_score"]
            if drop < -8.0:
                pre = hist[i-1]["total_score"]
                # 向后看 N 个快照
                for j in range(i+1, min(i + self.SUCCESS_REPAIR_DAYS + 1, len(hist))):
                    if hist[j]["total_score"] >= pre - 3.0:
                        return True
                # 大跌且未恢复
                return False
        return True

    def evaluate_courtship_success(self, person_id: str) -> dict[str, Any]:
        """攻略成功综合判定（v3.0 核心）。

        成功 ≠ 100 分，而是：
          1. 总分 ≥ 80；
          2. 近 14 天快照最大波动 < 10（稳定）；
          3. 对方主动发起对话占比 > 40%（双向奔赴，不是单方面舔）；
          4. 投入比在 [0.7, 1.3]；
          5. 冲突能在 3 天内修复。
        """
        scores = self.get_current(person_id)
        total = self.get_total_with_bonus(person_id, scores)

        # 稳定性：近 14 天快照
        hist = self.db.fetch_all(
            "SELECT date, total_score FROM affection_snapshots WHERE person_id=:pid "
            "ORDER BY date DESC LIMIT :n",
            {"pid": person_id, "n": self.SUCCESS_STABLE_DAYS},
        )
        if len(hist) >= 7:
            vals = [h["total_score"] for h in hist]
            swing = round(max(vals) - min(vals), 1)
            stable = swing < self.SUCCESS_STABLE_SWING
        else:
            swing = None
            stable = False  # 快照不足，视为未稳定

        proactive = self.get_proactive_ratio(person_id)
        balance = self.get_investment_balance(person_id)
        repaired = self._conflict_repaired(person_id)

        checks = {
            "score_ge_80": total >= self.SUCCESS_MIN_SCORE,
            "stable_14d": stable,
            "proactive_gt_40": proactive > self.SUCCESS_PROACTIVE_RATIO,
            "in_balance": self.SUCCESS_INVEST_MIN <= balance <= self.SUCCESS_INVEST_MAX,
            "conflict_repairable": repaired,
        }
        success = all(checks.values())
        return {
            "success": success,
            "total_score": total,
            "swing_14d": swing,
            "proactive_ratio": proactive,
            "investment_balance": balance,
            "conflict_repairable": repaired,
            "checks": checks,
            "passed": sum(checks.values()),
            "total_checks": len(checks),
            "note": (
                "攻略成功不是刷到100分。80+且稳定、对方主动>40%、投入平衡、冲突可修复，"
                "就是一段健康的关系。90+已是非常好的状态。"
            ),
        }

    # ------------------------------------------------------------------ #
    # v3.0：重大事件（表白/确定关系/吵架/分手）
    # ------------------------------------------------------------------ #
    def record_milestone(self, person_id: str, event_type: str, note: str = "") -> dict:
        """记录重大关系事件并对对应维度施加冲击。

        event_type 见 MILESTONE_IMPACTS：
          confess_love / confirm_relationship / quarrel / cold_war / reconcile / breakup。
        """
        impact = self.MILESTONE_IMPACTS.get(event_type)
        if not impact:
            return {"ok": False, "error": f"未知事件类型 {event_type}"}

        scores = self.get_current(person_id)
        total_before = self.get_total_with_bonus(person_id, scores)
        for dim, delta in impact.items():
            if dim in scores:
                scores[dim] = self._clamp_value(scores[dim] + delta)
        total_after = self._save_current(person_id, scores)
        label_map = {
            "confess_love": "表白", "confirm_relationship": "确定关系",
            "quarrel": "吵架", "cold_war": "冷战", "reconcile": "和好",
            "breakup": "分手",
        }
        reason = f"重大事件「{label_map.get(event_type, event_type)}」"
        if note:
            reason += f"：{note}"
        self._log_event(person_id, "__total__", round(total_after - total_before, 1),
                        reason, total_before, total_after, event_type="milestone")
        self._maybe_log_stage_up(person_id, total_before, total_after)
        return {"ok": True, "total_before": total_before, "total_after": total_after,
                "event_type": event_type, "stage": self.get_stage(total_after)}

    # ------------------------------------------------------------------ #
    # 奖励 / 重置
    # ------------------------------------------------------------------ #
    def add_bonus(self, person_id: str, bonus: float, reason: str = "任务奖励") -> None:
        if bonus <= 0:
            return
        scores = self.get_current(person_id)
        total_before = self.get_total_with_bonus(person_id, scores)
        per = bonus / max(1, len(self.WEIGHTS))
        for dim in self.WEIGHTS:
            old = scores.get(dim, 50.0)
            scores[dim] = round(max(0.0, min(100.0, old + per)), 2)
        total_after = self._save_current(person_id, scores)
        self._log_event(person_id, "__total__", round(total_after - total_before, 1),
                        reason, total_before, total_after, event_type="bonus")
        self._maybe_log_stage_up(person_id, total_before, total_after)
        logger.info("好感度奖励 person=%s bonus=%.1f", person_id, bonus)

    # ------------------------------------------------------------------ #
    # R3.3-78：忠诚度（loyalty）公共接口——与 add_bonus 对称
    # ------------------------------------------------------------------ #
    def get_loyalty(self, person_id: str) -> float:
        """返回当前忠诚度（0-100），缺省 50。"""
        if not person_id:
            return self.LOYALTY_DEFAULT
        return float(self.get_current(person_id).get(self.LOYALTY_KEY, self.LOYALTY_DEFAULT))

    def add_loyalty_bonus(self, person_id: str, bonus: float,
                          reason: str = "任务奖励") -> None:
        """反制线任务奖励入口：只加忠诚度，不动 18 维好感度。"""
        if bonus <= 0 or not person_id:
            return
        scores = self.get_current(person_id)
        old = self._clamp_value(scores.get(self.LOYALTY_KEY, self.LOYALTY_DEFAULT))
        new = self._clamp_value(old + bonus)
        scores[self.LOYALTY_KEY] = new
        self._save_current(person_id, scores)
        self._log_event(person_id, self.LOYALTY_KEY, round(new - old, 2),
                        reason, old, new, event_type="loyalty_bonus")
        logger.info("忠诚度奖励 person=%s bonus=%.1f → %.1f", person_id, bonus, new)

    # ------------------------------------------------------------------ #
    # R3.3-78：当前路线（counter=反制线 / guide=引导线）
    # ------------------------------------------------------------------ #
    def get_route(self, person_id: str) -> str:
        """返回当前对象路线："counter" | "guide"，缺省 "guide"。

        路线为内存态：facade 在拿到 manipulation_coach 上下文时调 set_route
        刷新；未刷新过的对象一律按引导线处理。
        """
        return self._routes.get(person_id, "guide") if person_id else "guide"

    def set_route(self, person_id: str, route: str) -> None:
        """由 facade / task_engine 在反制场景检出后调用，刷新当前路线。"""
        if person_id and route in self.ROUTES:
            self._routes[person_id] = route

    def reset(self, person_id: str) -> None:
        scores = {dim: 50.0 for dim in self.WEIGHTS}
        scores[self.LOYALTY_KEY] = 50.0  # R3.3-78：忠诚度一并归中性
        self._save_current(person_id, scores)
        logger.info("好感度已重置 person=%s", person_id)

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_dt(s: str) -> Optional[datetime]:
        if not s:
            return None
        for fmt in (
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f",
        ):
            try:
                return datetime.strptime(s, fmt)
            except (ValueError, TypeError):
                continue
        return None

    # ================================================================== #
    # v3.1 新增能力①：三大类汇总（用户一眼能看懂）
    #   18维太细 → 汇总成 吸引力 / 信任 / 舒适度 三个数。
    # ================================================================== #
    THREE_AXES_MAP: dict[str, list[str]] = {
        "attraction": {  # 吸引力
            "label": "吸引力",
            "dims": ["physical_attraction", "humor", "romance", "chat_resonance"],
        },
        "trust": {  # 信任
            "label": "信任",
            "dims": ["trust", "security", "thoughtfulness", "respect"],
        },
        "comfort": {  # 舒适度
            "label": "舒适度",
            "dims": ["emotional_stability", "emotional_value", "intimacy", "independence"],
        },
    }

    def summarize_to_three_axes(self, detailed_scores: dict[str, float]) -> dict[str, Any]:
        """把 18 维细分配成 吸引力/信任/舒适度 三大类。

        Args:
            detailed_scores: 18 维 dict（dim->score）。

        Returns:
            dict: {
              "attraction": {"label","score","dims":{dim:score}},
              "trust":      {...},
              "comfort":    {...},
              "overall": 三类均值（一眼看懂的总印象）,
            }
        """
        out: dict[str, Any] = {}
        for axis, cfg in self.THREE_AXES_MAP.items():
            dims_used = [d for d in cfg["dims"] if d in detailed_scores]
            values = [float(detailed_scores[d]) for d in dims_used]
            avg = round(sum(values) / len(values), 1) if values else 50.0
            out[axis] = {
                "label": cfg["label"],
                "score": avg,
                "level": self._axis_level(avg),
                "dims": {d: detailed_scores[d] for d in dims_used},
            }
        scores = [out[a]["score"] for a in ("attraction", "trust", "comfort")]
        out["overall"] = round(sum(scores) / len(scores), 1)
        return out

    @staticmethod
    def _axis_level(score: float) -> str:
        if score >= 75:
            return "高"
        if score >= 55:
            return "中高"
        if score >= 40:
            return "中等"
        return "偏低"

    # ================================================================== #
    # v3.1 新增能力②：能量维度（0-100）
    #   深度话题费能量，能量低了对方会累，聊天频率要降。
    #     深度话题 -15 / 中等话题 -5 / 浅层话题 +3 / 对方主动 +10
    #   能量 < 20 → 建议"今天聊太多了，让对方休息一下"。
    # ================================================================== #
    ENERGY_DEEP_KW = [
        "过去", "童年", "原生家庭", "创伤", "以前", "小时候",
        "未来", "以后", "将来", "梦想", "规划", "结婚", "孩子", "人生",
    ]
    ENERGY_SHALLOW_KW = [
        "早安", "晚安", "哈哈", "哈哈哈", "嗯", "哦", "好的", "表情",
        "在吗", "呵呵", "嘿嘿", "啦",
    ]
    ENERGY_MEDIUM_KW = [
        "工作", "加班", "吃饭", "忙", "累", "下班", "今天", "明天",
        "吐槽", "老板", "同事", "外卖",
    ]
    ENERGY_LOW_THRESHOLD: float = 20.0
    ENERGY_MAX: float = 100.0
    ENERGY_MIN: float = 0.0

    def _energy_path(self, person_id: str) -> str:
        import os
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "persons", person_id, "energy.json")

    def get_energy(self, person_id: str) -> float:
        """读取当前能量（0-100），默认 80。"""
        import json
        import os
        try:
            path = self._energy_path(person_id)
            if not os.path.exists(path):
                return 80.0
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return float(data.get("energy", 80.0))
        except Exception:  # noqa: BLE001
            return 80.0

    def _save_energy(self, person_id: str, energy: float) -> None:
        import json
        import os
        try:
            path = self._energy_path(person_id)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"energy": round(energy, 1),
                           "updated_at": datetime.now().isoformat()},
                          f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            logger.warning("能量保存失败: %s", e)

    def apply_energy_from_message(self, person_id: str, content: str,
                                  other_initiated: bool = False) -> dict:
        """根据一条消息内容更新能量值。

        Args:
            person_id: 人物 ID。
            content: 消息文本。
            other_initiated: 是否对方主动发起。

        Returns:
            dict: {"energy", "delta", "level", "suggestion"}
        """
        cur = self.get_energy(person_id)
        delta = 0.0
        # 话题深度判定
        is_deep = any(k in (content or "") for k in self.ENERGY_DEEP_KW)
        is_shallow = any(k in (content or "") for k in self.ENERGY_SHALLOW_KW)
        is_medium = any(k in (content or "") for k in self.ENERGY_MEDIUM_KW)

        if is_deep:
            delta -= 15.0
        elif is_shallow:
            delta += 3.0
        elif is_medium:
            delta -= 5.0
        # 对方主动加能量
        if other_initiated:
            delta += 10.0

        new_energy = max(self.ENERGY_MIN, min(self.ENERGY_MAX, cur + delta))
        self._save_energy(person_id, new_energy)

        suggestion = ""
        if new_energy < self.ENERGY_LOW_THRESHOLD:
            suggestion = "今天聊太多了，让对方休息一下，主动降频别再追问"

        return {
            "energy": round(new_energy, 1),
            "delta": round(delta, 1),
            "level": self._energy_level(new_energy),
            "suggestion": suggestion,
        }

    @staticmethod
    def _energy_level(e: float) -> str:
        if e >= 70:
            return "充沛"
        if e >= 40:
            return "平稳"
        if e >= 20:
            return "偏低"
        return "透支"

    # ================================================================== #
    # v3.1 新增能力③：变化原因人性化
    #   不是"亲密感+3"，而是"因为对方主动约你下周吃饭，感觉他很认真"。
    # ================================================================== #
    REASON_TEMPLATES: dict[str, str] = {
        "other_initiated_date": "因为对方主动约你，感觉他很认真",
        "other_replies_fast": "因为对方回复很快，感觉他很在意你",
        "other_replies_short": "因为对方回复很短，感觉他今天可能有点累",
        "quarrel": "因为刚才吵了一架，大家都有点情绪",
        "other_cares": "因为对方主动关心你，感觉他心里有你",
        "other_forgets": "因为对方答应的事又没做到，有点小失落",
    }

    @classmethod
    def explain_change(cls, delta: float, reason_event: str,
                       dimension_name: str = "") -> str:
        """把冷冰冰的分数变化翻译成一句人话。

        Args:
            delta: 分数变化（正/负）。
            reason_event: 事件类型键（对应 REASON_TEMPLATES）。
            dimension_name: 维度中文名（如"亲密感"），可空。

        Returns:
            str: 人性化解释。
        """
        base = cls.REASON_TEMPLATES.get(reason_event, "因为一次互动，关系有了小变化")
        sign = "提升" if delta >= 0 else "回落"
        if dimension_name:
            return f"{base}，{dimension_name}{sign}{abs(round(delta,1)):.1f}"
        return f"{base}（变化{delta:+.1f}）"


