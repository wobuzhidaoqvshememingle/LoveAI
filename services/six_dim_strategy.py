# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 六维战略评分（Six-Dimension Strategy Scorer）

与现有模块区分：
- services/relationship_axes.py：关系健康度六轴（Trust/Attraction/...）——本模块**不是**
- services/strategy_advisor.py:_extract_dimensions：六维人格——本模块**不是**
- services/strategy_utility.py:fused_choose：五策略效用——本模块**不是**

本模块对**一条候选话术/策略**做现实可行性打分，六个维度各 0-10，总分 0-60：
    a. reciprocal       互惠：双方投入是否对等（sent vs received 条数/字数）
    b. reliable         可靠：对方承诺兑现率（从聊天记录搜承诺词与兑现词）
    c. attraction       吸引：对方主动度 + 情绪正向度
    d. values           价值观：聊天中价值观关键词重合度
    e. feasibility      现实可行：财务可行性（financial_analyzer.check_demand_feasibility）
    f. opportunity_cost 机会成本：放弃此方案的代价（关系越好越不怕换，得分越高=越值得放弃）

纯规则评分，不调用 LLM。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 词表
# --------------------------------------------------------------------------- #
_COMMITMENT_WORDS = ("我保证", "一定", "下次", "周末", "我会", "答应你", "说好了", "保证")
_FULFILLMENT_WORDS = ("到了", "买了", "做了", "完成", "兑现", "已经", "好了", "弄好了", "搞完", "交付")

_POSITIVE_WORDS = (
    "开心", "喜欢", "想你", "爱", "哈哈", "好呀", "嗯嗯", "谢谢", "棒",
    "美", "感动", "幸福", "期待", "嗯呢", "嘿嘿", "哈哈哈哈", "哇",
)

# 价值观四类关键词
_VALUE_BUCKETS: dict[str, tuple[str, ...]] = {
    "money": ("省钱", "花钱", "工资", "存款", "买房", "奢侈", "抠门", "aa", "AA",
              "礼物", "钱", "理财", "投资"),
    "family": ("爸妈", "结婚", "孩子", "回家", "相亲", "家人", "父母", "老家"),
    "career": ("工作", "加班", "升职", "辞职", "创业", "事业", "项目", "老板"),
    "lifestyle": ("旅游", "旅行", "宅", "健身", "熬夜", "周末", "喝酒", "聚会",
                  "运动", "电影", "追剧"),
}

_DANGEROUS_MONEY_WORDS = ("借钱", "贷款", "网贷", "透支", "抵押")


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _clamp(v: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, float(v)))


def _round1(v: float) -> float:
    return round(_clamp(v), 1)


def _count_chars(text: str) -> int:
    return len(text or "")


# --------------------------------------------------------------------------- #
# 主类
# --------------------------------------------------------------------------- #
class SixDimensionStrategyScorer:
    """六维战略评分器。

    依赖注入：
        db: 提供 fetch_all / fetch_one 的数据库句柄（与 models.database 同接口）。
        financial_analyzer: 可选，需有 check_demand_feasibility(person_id, value, name)。
        affection_engine: 可选，需有 get_current(person_id) -> dict。
    """

    def __init__(
        self,
        db: Any = None,
        financial_analyzer: Any = None,
        affection_engine: Any = None,
    ) -> None:
        self.db = db
        self.financial = financial_analyzer
        self.affection = affection_engine

    # ------------------------------------------------------------------ #
    # 对外主入口
    # ------------------------------------------------------------------ #
    def score(
        self,
        person_id: str,
        strategy_text: str,
        db: Any = None,
        context: Optional[dict] = None,
    ) -> dict:
        """对一条候选策略打六维分。

        Args:
            person_id: 对方人物 ID。
            strategy_text: 候选话术/策略描述。
            db: 可临时覆盖 db（缺省用 self.db）。
            context: 额外上下文（如 distance/days/resource 信号）。

        Returns:
            {
              "reciprocal": 0-10,
              "reliable":   0-10,
              "attraction": 0-10,
              "values":     0-10,
              "feasibility":0-10,
              "opportunity_cost": 0-10,
              "total":      0-60,
              "reasons": {dim: "一句话依据"},
            }
        """
        context = context or {}
        db = db or self.db

        sent_msgs, recv_msgs = self._load_messages(person_id, db)

        r_recip, why_recip = self._score_reciprocal(sent_msgs, recv_msgs)
        r_rel, why_rel = self._score_reliable(recv_msgs)
        r_attr, why_attr = self._score_attraction(sent_msgs, recv_msgs)
        r_val, why_val = self._score_values(sent_msgs, recv_msgs)
        r_feas, why_feas = self._score_feasibility(person_id, strategy_text, context)
        r_opp, why_opp = self._score_opportunity_cost(person_id)

        dims = {
            "reciprocal": r_recip,
            "reliable": r_rel,
            "attraction": r_attr,
            "values": r_val,
            "feasibility": r_feas,
            "opportunity_cost": r_opp,
        }
        reasons = {
            "reciprocal": why_recip,
            "reliable": why_rel,
            "attraction": why_attr,
            "values": why_val,
            "feasibility": why_feas,
            "opportunity_cost": why_opp,
        }
        total = round(sum(dims.values()), 1)
        return {
            **dims,
            "total": total,
            "reasons": reasons,
        }

    @staticmethod
    def combine(sim_score: float, six_dim_total: float) -> float:
        """综合分 = 模拟实测分 + 六维战略分。

        规则：
            sim_score >= 60（样本达标）：综合 = 0.6*sim + 0.4*(six_dim/60*100)
            否则：                     综合 = 0.3*sim + 0.7*(six_dim/60*100)
        """
        try:
            sim = float(sim_score)
        except (TypeError, ValueError):
            sim = 0.0
        try:
            six = float(six_dim_total)
        except (TypeError, ValueError):
            six = 0.0
        sim = max(0.0, min(100.0, sim))
        six = max(0.0, min(60.0, six))
        norm = six / 60.0 * 100.0
        if sim >= 60:
            combined = 0.6 * sim + 0.4 * norm
        else:
            combined = 0.3 * sim + 0.7 * norm
        return round(combined, 1)

    # ------------------------------------------------------------------ #
    # 数据加载
    # ------------------------------------------------------------------ #
    def _load_messages(self, person_id: str, db: Any) -> tuple[list[str], list[str]]:
        """从 chat_messages 加载 sent / received 文本列表。失败返回 ([], [])。"""
        if not db or not person_id:
            return [], []
        try:
            rows = db.fetch_all(
                "SELECT direction, content FROM chat_messages "
                "WHERE person_id=:pid ORDER BY created_at ASC",
                {"pid": person_id},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("six_dim: 加载 chat_messages 失败: %s", e)
            return [], []
        sent: list[str] = []
        recv: list[str] = []
        for r in rows or []:
            content = r.get("content") or ""
            direction = r.get("direction") or ""
            if direction == "sent":
                sent.append(content)
            elif direction == "received":
                recv.append(content)
        return sent, recv

    # ------------------------------------------------------------------ #
    # a. 互惠 reciprocal
    # ------------------------------------------------------------------ #
    @staticmethod
    def _score_reciprocal(sent: list[str], recv: list[str]) -> tuple[float, str]:
        n_s, n_r = len(sent), len(recv)
        if n_s == 0 and n_r == 0:
            return 5.0, "无聊天数据，给中性分 5"
        if n_s == 0 or n_r == 0:
            return 1.0, f"单边剃头：我发{n_s}条/对方回{n_r}条"
        balance = min(n_s, n_r) / max(n_s, n_r)
        score = 1.0 + balance * 9.0  # 0→1, 1→10
        why = f"我发{n_s}条/对方回{n_r}条，对等度={balance:.2f}"
        return _round1(score), why

    # ------------------------------------------------------------------ #
    # b. 可靠 reliable
    # ------------------------------------------------------------------ #
    @staticmethod
    def _score_reliable(recv: list[str]) -> tuple[float, str]:
        commitments: list[int] = []  # 收到承诺的消息下标
        for i, text in enumerate(recv):
            if any(w in text for w in _COMMITMENT_WORDS):
                commitments.append(i)
        if not commitments:
            return 5.0, "聊天中未检出承诺词，给中性分 5"
        fulfilled = 0
        for idx in commitments:
            later = recv[idx + 1:]
            if any(any(w in t for w in _FULFILLMENT_WORDS) for t in later):
                fulfilled += 1
        rate = fulfilled / len(commitments)
        score = 1.0 + rate * 9.0
        why = f"对方共{len(commitments)}次承诺，后续兑现{fulfilled}次（兑现率{rate*100:.0f}%）"
        return _round1(score), why

    # ------------------------------------------------------------------ #
    # c. 吸引 attraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _score_attraction(sent: list[str], recv: list[str]) -> tuple[float, str]:
        n_s, n_r = len(sent), len(recv)
        total = n_s + n_r
        if total == 0:
            return 5.0, "无聊天数据，给中性分 5"
        share = n_r / total
        share_score = _clamp(share * 10 * 2)  # 0.5→10, 0.1→2
        if n_r == 0:
            pos_score = 5.0
        else:
            pos_cnt = sum(1 for t in recv if any(w in t for w in _POSITIVE_WORDS))
            pos_ratio = pos_cnt / n_r
            pos_score = 5.0 + pos_ratio * 5.0
        score = 0.6 * share_score + 0.4 * pos_score
        why = (f"对方主动占比{share*100:.0f}%，正向情绪词{pos_cnt}/{n_r}条"
               if n_r else "对方未回复，主动度极低")
        return _round1(score), why

    # ------------------------------------------------------------------ #
    # d. 价值观 values
    # ------------------------------------------------------------------ #
    @staticmethod
    def _buckets_in(texts: list[str]) -> set[str]:
        joined = "\n".join(texts or [])
        hit = set()
        for bucket, words in _VALUE_BUCKETS.items():
            if any(w in joined for w in words):
                hit.add(bucket)
        return hit

    @classmethod
    def _score_values(cls, sent: list[str], recv: list[str]) -> tuple[float, str]:
        my_buckets = cls._buckets_in(sent)
        if not my_buckets:
            return 5.0, "我方聊天未检出价值观关键词，给中性分 5"
        their_buckets = cls._buckets_in(recv)
        overlap = my_buckets & their_buckets
        ratio = len(overlap) / len(my_buckets)
        score = 3.0 + ratio * 7.0
        why = (f"我方价值观簇={sorted(my_buckets)}，"
               f"对方重合={sorted(overlap) or '无'}（重合率{ratio*100:.0f}%）")
        return _round1(score), why

    # ------------------------------------------------------------------ #
    # e. 现实可行 feasibility
    # ------------------------------------------------------------------ #
    _MONEY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(元|块钱|块|rmb|RMB|人民币)", re.IGNORECASE)

    def _score_feasibility(
        self,
        person_id: str,
        strategy_text: str,
        context: dict,
    ) -> tuple[float, str]:
        text = strategy_text or ""
        # 1) 危险词直接压分
        if any(w in text for w in _DANGEROUS_MONEY_WORDS):
            return 1.0, f"策略含危险资金词（{[w for w in _DANGEROUS_MONEY_WORDS if w in text]}）"

        # 2) 抽金额
        m = self._MONEY_RE.search(text)
        if not m:
            # 无金额信号，给中性 5
            return 5.0, "策略未检出金额信号，默认中性 5"

        amount = float(m.group(1))
        item_name = (text[:20] + "…") if len(text) > 20 else text

        # 3) 有 financial_analyzer → 调真实检查
        if self.financial is not None and person_id:
            try:
                res = self.financial.check_demand_feasibility(person_id, amount, item_name)
                risk = res.get("risk_level", "medium")
                mapping = {"low": 9.0, "medium": 6.5, "high": 4.0, "dangerous": 1.5}
                score = mapping.get(risk, 5.0)
                why = f"财务可行性 risk={risk}，{res.get('suggestion', '')}"
                return _round1(score), why
            except Exception as e:  # noqa: BLE001
                logger.warning("six_dim: check_demand_feasibility 失败: %s", e)
                return 5.0, f"财务检查调用失败({e})，给中性 5"

        # 4) 无 analyzer：粗估
        if amount >= 5000:
            return 2.0, f"检出金额{amount:.0f}元但无财务分析器，按高风险估 2"
        if amount >= 1000:
            return 4.5, f"检出金额{amount:.0f}元，中等偏高"
        return 7.0, f"检出金额{amount:.0f}元，金额较小"

    # ------------------------------------------------------------------ #
    # f. 机会成本 opportunity_cost
    # ------------------------------------------------------------------ #
    def _score_opportunity_cost(self, person_id: str) -> tuple[float, str]:
        """关系越好 → 越不怕换 → 放弃代价越小 → 分越高。"""
        if self.affection is None or not person_id:
            return 5.0, "无好感度数据，给中性分 5"
        try:
            scores = self.affection.get_current(person_id) or {}
        except Exception as e:  # noqa: BLE001
            logger.warning("six_dim: affection.get_current 失败: %s", e)
            return 5.0, f"好感度读取失败({e})，给中性分 5"

        # 取总分：优先 total_score 字段，否则加权平均
        total = scores.get("total_score") if isinstance(scores, dict) else None
        if total is None and isinstance(scores, dict) and scores:
            vals = [v for v in scores.values() if isinstance(v, (int, float))]
            total = sum(vals) / len(vals) if vals else 50.0
        try:
            total = float(total or 50.0)
        except (TypeError, ValueError):
            total = 50.0
        score = total / 10.0  # 50→5, 80→8, 20→2
        why = f"当前关系健康度{total:.0f}/100，越健康越能承担换方案成本"
        return _round1(score), why


# --------------------------------------------------------------------------- #
# 模块级便捷函数
# --------------------------------------------------------------------------- #
_default_scorer: Optional[SixDimensionStrategyScorer] = None


def get_default_scorer() -> SixDimensionStrategyScorer:
    global _default_scorer
    if _default_scorer is None:
        _default_scorer = SixDimensionStrategyScorer()
    return _default_scorer


def score_strategy(person_id: str, strategy_text: str,
                   db: Any = None, context: Optional[dict] = None) -> dict:
    return get_default_scorer().score(person_id, strategy_text, db=db, context=context)


def combine_scores(sim_score: float, six_dim_total: float) -> float:
    return SixDimensionStrategyScorer.combine(sim_score, six_dim_total)


# =========================================================================== #
# v2 新增：阶段化落地策略（地球online恋爱小游戏攻略助手）
#   - 不是空泛建议，是"下次聊天/见面具体做什么"
#   - 四阶段：Meet(初识) → Date(约会) → Deepen(深入) → Resolve(确认/解决)
#   - 每个策略三件套：action(具体行为) + effect(数值影响) + when(执行时机)
# =========================================================================== #

# 关系维度（与好感度/关系轴对齐，不与上面评分器的六维混）
RELATIONSHIP_DIMS = ["信任", "亲密", "吸引力", "舒适度", "投入对等", "价值观"]

# 行为影响因子表：不同行为对各维度的加减分
BEHAVIOR_IMPACT: dict[str, dict[str, int]] = {
    "主动分享秘密":       {"信任": 5, "亲密": 3},
    "赞美对方":           {"吸引力": 3, "舒适度": 2},
    "记住对方说过的话":   {"信任": 4, "亲密": 2},
    "吵架后主动给台阶":   {"信任": 3, "舒适度": 5},
    "忽冷忽热":           {"吸引力": 2, "信任": -3, "舒适度": -2},
    "秒回消息":           {"舒适度": 3, "投入对等": -1},
    "等对方先找":         {"投入对等": 2, "舒适度": -2},
    "认真听对方说话":     {"信任": 3, "亲密": 2},
    "聊自己的糗事":       {"吸引力": 2, "信任": 2},
    "公共场合秀恩爱":     {"亲密": 2, "舒适度": -3},
}


def _a(action: str, effect: str, when: str) -> dict:
    return {"action": action, "effect": effect, "when": when}


# 阶段 × 维度 → 策略列表
STAGE_STRATEGIES: dict[str, dict[str, list[dict]]] = {
    "Meet": {
        "信任": [
            _a("主动分享一个小秘密，测试对方会不会回应", "信任+3", "下次深度聊天时"),
            _a("记住对方随口提过的一个细节，下次提起来", "信任+4", "3天内聊天时"),
        ],
        "吸引力": [
            _a("聊一件自己做得不错的事，但别吹", "吸引力+2", "下次聊天时"),
            _a("发一张不刻意的日常照片", "吸引力+2", "今天内"),
        ],
        "舒适度": [
            _a("找对方擅长的话题问一句，让对方有表达欲", "舒适度+3", "下次聊天时"),
            _a("别连环问，聊两句停一下，给对方回的空间", "舒适度+2", "全程"),
        ],
        "投入对等": [
            _a("对方发几条，你回几条，别剃头", "投入对等+3", "全程"),
        ],
        "亲密": [
            _a("不急着叫昵称，先把人聊舒服", "亲密+1", "保持"),
        ],
        "价值观": [
            _a("轻聊一次周末怎么过，看节奏合不合", "价值观+2", "下周聊天时"),
        ],
    },
    "Date": {
        "信任": [
            _a("约好的时间提前10分钟到", "信任+4", "约会当天"),
            _a("主动买单或AA分得干脆，别纠结", "信任+2", "约会吃饭时"),
        ],
        "吸引力": [
            _a("穿得干净整齐，比平时用心一档", "吸引力+3", "约会当天"),
            _a("见面时夸一句对方今天的变化", "吸引力+2", "刚见面时"),
        ],
        "舒适度": [
            _a("选对方说过想去的地方，别自作主张", "舒适度+4", "定地方时"),
            _a("约会节奏松一点，别排满", "舒适度+2", "全程"),
        ],
        "亲密": [
            _a("散步时走慢半步，给靠近留空间", "亲密+2", "约会散步时"),
        ],
        "投入对等": [
            _a("这次你安排，下次让对方挑地方", "投入对等+3", "约会后约"),
        ],
        "价值观": [
            _a("聊一次旅行偏好，看消费/节奏合不合", "价值观+2", "约会吃饭时"),
        ],
    },
    "Deepen": {
        "信任": [
            _a("一起做一件需要合作的事（做饭/拼拼图），建立默契", "信任+5", "下次见面时"),
            _a("主动说一件自己的软肋，不端着", "信任+4", "深度聊天时"),
        ],
        "亲密": [
            _a("聊一次小时候的事，交换成长背景", "亲密+4", "深夜聊天时"),
            _a("散步时自然地碰一下手，看反应", "亲密+3", "下次散步时"),
        ],
        "吸引力": [
            _a("在对方不擅长的事上顶上去，露一手", "吸引力+3", "自然场景"),
        ],
        "舒适度": [
            _a("对方累的时候别硬聊，说句『今天先到这』", "舒适度+3", "对方疲惫时"),
        ],
        "投入对等": [
            _a("主动一次，再让对方主动一次，别一个人撑", "投入对等+3", "一周内"),
        ],
        "价值观": [
            _a("轻聊一次未来3年的打算，看方向合不合", "价值观+3", "深度聊天时"),
        ],
    },
    "Resolve": {
        "信任": [
            _a("吵架后主动给台阶，别冷战过夜", "信任+3", "吵架当天"),
            _a("承诺过的事写下来，到点兑现", "信任+5", "下次承诺时"),
        ],
        "亲密": [
            _a("明确一次关系状态，别含糊", "亲密+5", "下次单独相处时"),
        ],
        "舒适度": [
            _a("把底线说清楚，别让对方猜", "舒适度+4", "平静时谈"),
        ],
        "吸引力": [
            _a("偶尔制造一次小惊喜，不是送礼", "吸引力+2", "本月内"),
        ],
        "投入对等": [
            _a("账算清：谁花的时间/钱，心里有数", "投入对等+3", "每月复盘"),
        ],
        "价值观": [
            _a("把结婚/孩子/异地这些硬问题摆上桌", "价值观+5", "稳定期谈"),
        ],
    },
}


def strategy_by_stage(stage: str, dimension: str) -> list[dict]:
    """返回某阶段某维度的可执行策略列表。

    Args:
        stage: 阶段名，取值 Meet/Date/Deepen/Resolve。
        dimension: 关系维度，取值 RELATIONSHIP_DIMS 之一。

    Returns:
        [{"action": "...", "effect": "信任+5", "when": "..."}, ...]
        查不到时返回空列表（调用方给"下一步"引导，不中断）。
    """
    return list(STAGE_STRATEGIES.get(stage, {}).get(dimension, []))


def next_steps_after_analysis() -> list[str]:
    """分析完给用户的"下一步可以做什么"（避免流程结束断档）。

    定位：我们是地球online恋爱小游戏攻略助手，不是恋爱专家。
    """
    return [
        "选一条上面的策略，下次聊天/见面直接用",
        "把对方最近3条消息丢进来，看看情绪走势",
        "去话术库挑3条下一句（自动按人格/阶段微调）",
        "模拟一条你打算发的话，看看对方大概会怎么回",
    ]


__all__ = [
    "SixDimensionStrategyScorer", "get_default_scorer",
    "score_strategy", "combine_scores",
    # v2 新增
    "RELATIONSHIP_DIMS", "BEHAVIOR_IMPACT", "STAGE_STRATEGIES",
    "strategy_by_stage", "next_steps_after_analysis",
]
