# -*- coding: utf-8 -*-
"""复盘回流引擎（预测 vs 实际）。

每轮分析时数字分身给出预测，本模块负责：
1. record_prediction：把预测持久化到 prediction_records（status=pending）；
2. settle_latest：下一轮拿到对方"实际"反应后，对上一条未结算预测逐项结算
   （情绪 / 回复方向 / 后续行动），计算本条有效度；
3. rolling_validity：统计滚动预测有效度（命中率，随样本量给出置信边界）；
4. calibration：把系统性偏差转成可执行的校准提示（用于修正模拟实测分权重）。

纯本地、确定性实现（不依赖 LLM），全部 SQL 使用命名参数。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# --- 情绪归一化 ---
# 桶：positive / neutral / negative / angry / avoid
_EMOTION_SYNONYMS: dict[str, list[str]] = {
    "positive": ["开心", "高兴", "喜欢", "期待", "感动", "撒娇", "兴奋", "热情", "愉悦",
                 "幸福", "害羞", "惊喜", "happy", "excited", "warm", "joy", "love", "pos"],
    "neutral": ["平静", "平淡", "一般", "中立", "neutral", "calm", "ok", "fine", "normal",
                "平常", "还好"],
    "negative": ["难过", "委屈", "失望", "焦虑", "担心", "失落", "沮丧", "低落", "遗憾",
                 "纠结", "迷茫", "sad", "anxious", "confused", "depressed", "down", "neg"],
    "angry": ["愤怒", "生气", "烦", "厌恶", "恼火", "暴躁", "气愤", "angry", "mad",
              "furious", "hate", "反感"],
    "avoid": ["冷淡", "回避", "冷漠", "敷衍", "防备", "无所谓", "疏远", "逃避", "cold",
              "distant", "avoid", "guarded", "indifferent", "回避型"],
}
# 极性大类（用于部分命中）
_POLARITY: dict[str, str] = {
    "positive": "pos", "neutral": "neu", "negative": "neg",
    "angry": "neg", "avoid": "neu",
}

# 敷衍/回避信号
_EVADE_MARKERS = ["哦", "嗯", "额", "啊", "呵呵", "好吧", "随便", "无所谓", "再说",
                  "忙", "不知道", "算了", "ok", "fine", "。", "?", "？"]
# 积极延伸信号
_ENGAGE_MARKERS = ["？", "?", "一起", "为什么", "怎么", "什么", "呢", "嘛", "哈哈",
                   "然后", "后来", "你呢", "真的吗", "说说"]
# 具体行动/承诺信号
_ACTION_MARKERS = ["明天", "今天", "后天", "周末", "下周", "晚上", "早上", "中午",
                   "一起", "我请", "到时候", "安排", "地点", "门口", "车站", "店",
                   "答应", "保证", "一定", "约", "几点", "分钟", "小时", "见"]
# 强共同安排/承诺词（单方做不出，需双方或明确许诺）
_COMMITMENT_WORDS = ["一起", "我请", "见面", "见", "约会", "约", "答应", "保证",
                     "一定", "安排", "陪", "门口", "车站", "几点", "等你", "去找你"]
# 仅时间词 + 模糊线上延续（"明天继续聊"）不算具体共同安排
_VAGUE_FOLLOWUP = ["继续聊", "找话题", "再说", "看看", "聊聊", "回你", "理你"]


def _emotion_bucket(value) -> str:
    """把任意情绪标签（中/英文、单值）归一到五个桶；无法判定为 neutral。"""
    if not value:
        return "neutral"
    text = str(value).strip().lower()
    if not text:
        return "neutral"
    # 直接命中桶名
    if text in _EMOTION_SYNONYMS:
        return text
    for bucket, words in _EMOTION_SYNONYMS.items():
        for w in words:
            if w.lower() in text:
                return bucket
    return "neutral"


def _direction_from_reply(reply) -> str:
    """根据单条回复文本推断方向桶：engage / neutral / evade。"""
    if reply is None:
        return "evade"
    text = str(reply).strip()
    if not text:
        return "evade"
    length = len(text)
    # 极短且为敷衍词 → evade
    if length <= 4:
        for m in _EVADE_MARKERS:
            if m in text:
                return "evade"
        return "evade" if length <= 2 else "neutral"
    engage = sum(1 for m in _ENGAGE_MARKERS if m in text)
    if engage >= 1 and length >= 8:
        return "engage"
    if length >= 12:
        return "engage"
    return "neutral"


def _has_action(text) -> bool:
    """文本中是否出现具体行动/承诺信号。"""
    if not text:
        return False
    t = str(text)
    return sum(1 for m in _ACTION_MARKERS if m in t) >= 1


def _concrete_commitment(text) -> bool:
    """是否为“具体共同安排/明确承诺”，而非模糊的线上后续（如“明天继续聊”）。"""
    if not text:
        return False
    t = str(text)
    strong = ("一起", "我请", "见", "约", "答应", "保证", "一定", "门口",
              "陪", "等你", "安排", "几点", "车站")
    if any(w in t for w in strong):
        return True
    return False


class ReviewLoop:
    """复盘回流：预测持久化 → 实际结算 → 滚动有效度 → 校准。"""

    def __init__(self, db) -> None:
        self.db = db

    # ------------------------------------------------------------------ #
    # 1. 记录预测
    # ------------------------------------------------------------------ #
    def record_prediction(self, person_id: str, trigger_input: str,
                          prediction: dict, turn_ref: str = "") -> int:
        """把本轮分身预测持久化为 pending 记录，返回记录 id（失败返回 -1）。"""
        if not person_id or not isinstance(prediction, dict):
            return -1
        try:
            payload = json.dumps(prediction, ensure_ascii=False)
            res = self.db.execute(
                """INSERT INTO prediction_records
                   (person_id, turn_ref, trigger_input, predicted_json, status)
                   VALUES (:pid, :ref, :trg, :pred, 'pending')""",
                {"pid": person_id, "ref": turn_ref or self._now(),
                 "trg": (str(trigger_input) or "")[:200], "pred": payload},
            )
            # SQLite 经 SQLAlchemy 取 lastrowid
            rid = getattr(res, "lastrowid", None)
            if rid is None:
                row = self.db.fetch_one(
                    "SELECT MAX(id) AS mid FROM prediction_records WHERE person_id=:pid",
                    {"pid": person_id})
                rid = (row or {}).get("mid")
            return int(rid) if rid is not None else -1
        except Exception as e:
            logger.debug("record_prediction 失败: %s", e)
            return -1

    # ------------------------------------------------------------------ #
    # 2. 结算上一条预测
    # ------------------------------------------------------------------ #
    def settle_latest(self, person_id: str, actual: dict,
                      turn_ref: str = "") -> dict:
        """用对方本轮实际反应结算最近一条 pending 预测。

        actual 可含：emotion / reply / reply_len / has_action / no_reply(bool)
        无 pending 记录时返回 {settled: False}。
        """
        if not person_id:
            return {"settled": False, "reason": "no_person"}
        try:
            rec = self.db.fetch_one(
                """SELECT * FROM prediction_records
                   WHERE person_id=:pid AND status='pending'
                   ORDER BY id DESC LIMIT 1""",
                {"pid": person_id})
            if not rec:
                return {"settled": False, "reason": "no_pending"}

            predicted = self._loads(rec.get("predicted_json"))
            no_reply = bool(actual.get("no_reply"))

            # --- 情绪命中 ---
            pred_emo_bucket = _emotion_bucket(predicted.get("emotion"))
            actual_emo = "avoid" if no_reply else actual.get("emotion")
            actual_emo_bucket = _emotion_bucket(actual_emo)
            emotion_hit = self._bucket_hit(pred_emo_bucket, actual_emo_bucket)

            # --- 回复方向命中 ---
            pred_replies = predicted.get("possible_replies") or predicted.get("replies") or []
            if isinstance(pred_replies, str):
                pred_replies = [pred_replies]
            if pred_replies:
                # 取候选回复里出现最多的方向桶
                pred_dir = self._majority([_direction_from_reply(r) for r in pred_replies])
            else:
                pred_dir = _direction_from_reply(predicted.get("likely_reaction"))
            if no_reply:
                actual_dir = "evade"
            elif actual.get("reply") is not None:
                actual_dir = _direction_from_reply(actual.get("reply"))
            else:
                actual_dir = _direction_from_reply(actual.get("reply_len", 0) and "x" *
                                                   int(actual.get("reply_len", 0)))
            direction_hit = 1.0 if pred_dir == actual_dir else 0.0

            # --- 后续行动/承诺命中（仅“具体共同安排/明确承诺”计；模糊线上后续 N/A，不进分母）---
            pred_action_text = predicted.get("action") or predicted.get("next_behavior") or ""
            pred_concrete = _concrete_commitment(pred_action_text)
            if "has_action" in actual:
                actual_concrete = bool(actual.get("has_action"))
            else:
                actual_concrete = _concrete_commitment(actual.get("reply"))
            if pred_concrete:
                action_hit = 1.0 if actual_concrete else 0.0
                action_applies = True
            else:
                action_hit = None       # 模糊后续，本轮无法证伪
                action_applies = False

            applicable = [emotion_hit, direction_hit]
            if action_applies:
                applicable.append(action_hit)
            validity = round(sum(applicable) / len(applicable), 3)

            actual_payload = json.dumps(
                {"emotion_bucket": actual_emo_bucket, "direction": actual_dir,
                 "has_action": actual_concrete,
                 "emotion_raw": actual.get("emotion", ""),
                 "reply": (str(actual.get("reply", "")) or "")[:300],
                 "no_reply": no_reply},
                ensure_ascii=False)

            self.db.execute(
                """UPDATE prediction_records SET
                     actual_json=:act, status='settled',
                     emotion_hit=:eh, direction_hit=:dh, action_hit=:ah,
                     validity_score=:val, settled_at=:ts
                   WHERE id=:rid""",
                {"act": actual_payload, "eh": emotion_hit, "dh": direction_hit,
                 "ah": action_hit, "val": validity, "ts": self._now(),
                 "rid": rec["id"]})

            return {
                "settled": True, "record_id": rec["id"],
                "predicted": {"emotion": pred_emo_bucket, "direction": pred_dir,
                              "has_action": pred_concrete},
                "actual": {"emotion": actual_emo_bucket, "direction": actual_dir,
                           "has_action": actual_concrete},
                "hits": {"emotion": emotion_hit, "direction": direction_hit,
                         "action": action_hit},
                "validity_score": validity,
            }
        except Exception as e:
            logger.debug("settle_latest 失败: %s", e)
            return {"settled": False, "reason": "error", "error": str(e)}

    def mark_pending_expired(self, person_id: str) -> int:
        """把超过本轮仍无法结算的旧 pending 标为 expired（如长期无任何回应）。"""
        try:
            res = self.db.execute(
                """UPDATE prediction_records SET status='expired', settled_at=:ts
                   WHERE person_id=:pid AND status='pending'""",
                {"pid": person_id, "ts": self._now()})
            return getattr(res, "rowcount", 0) or 0
        except Exception as e:
            logger.debug("mark_expired 失败: %s", e)
            return 0

    # ------------------------------------------------------------------ #
    # 3. 滚动有效度
    # ------------------------------------------------------------------ #
    def rolling_validity(self, person_id: str, window: int = 20) -> dict:
        """统计最近 window 条已结算记录的命中率与置信边界。"""
        try:
            rows = self.db.fetch_all(
                """SELECT emotion_hit, direction_hit, action_hit, validity_score
                   FROM prediction_records
                   WHERE person_id=:pid AND status='settled'
                   ORDER BY id DESC LIMIT :lim""",
                {"pid": person_id, "lim": max(1, int(window))})
            n = len(rows)
            if n == 0:
                return {"n": 0, "validity": None, "ci": None, "by_dimension": {}}
            avg = lambda key: round(sum(r[key] for r in rows if r[key] is not None) /
                                    max(1, sum(1 for r in rows if r[key] is not None)), 3)
            validity = round(sum(r["validity_score"] for r in rows) / n, 3)
            # Wilson 简化：用正态近似的 95% 区间
            margin = round(1.96 * ((validity * (1 - validity) / n) ** 0.5), 3) if n else 0
            return {
                "n": n,
                "validity": validity,
                "ci": [max(0.0, round(validity - margin, 3)),
                       min(1.0, round(validity + margin, 3))],
                "by_dimension": {
                    "emotion": avg("emotion_hit"),
                    "direction": avg("direction_hit"),
                    "action": avg("action_hit")},
                "pending": self._count_pending(person_id),
            }
        except Exception as e:
            logger.debug("rolling_validity 失败: %s", e)
            return {"n": 0, "validity": None, "ci": None, "by_dimension": {}}

    # ------------------------------------------------------------------ #
    # 4. 校准提示
    # ------------------------------------------------------------------ #
    def calibration(self, person_id: str) -> dict:
        """基于滚动有效度输出校准建议（模拟实测分权重 + 偏差维度）。"""
        stat = self.rolling_validity(person_id)
        n = stat.get("n", 0)
        if not n:
            return {"available": False, "sim_weight": 0.4,
                    "hint": "样本不足，以战略分为准（模拟权重 0.4）",
                    "validity": None}
        validity = stat["validity"]
        byd = stat.get("by_dimension", {})
        # 找最弱维度
        weak = [k for k, v in byd.items() if v is not None and v < 0.5]
        # 模拟实测分权重随真实命中率在 0.4-0.7 间调整
        sim_weight = round(0.4 + 0.3 * max(0.0, min(1.0, validity)), 2)
        if validity >= 0.7:
            band = "可信"
        elif validity >= 0.55:
            band = "一般"
        else:
            band = "偏低"
        return {
            "available": True,
            "validity": validity,
            "band": band,
            "sim_weight": sim_weight,
            "weak_dimensions": weak,
            "ci": stat.get("ci"),
            "hint": self._calibration_hint(band, weak),
        }

    # --- 工具 ---
    @staticmethod
    def _calibration_hint(band: str, weak: list) -> str:
        name_map = {"emotion": "情绪", "direction": "回复方向", "action": "行动承诺"}
        if band == "偏低":
            dims = "、".join(name_map.get(w, w) for w in weak) or "整体"
            return f"预测有效度偏低，重点核对{dims}；降低模拟实测分权重，以战略分为准"
        if band == "一般":
            dims = "、".join(name_map.get(w, w) for w in weak)
            return f"预测有效度一般" + (f"，{dims}偏差较明显" if dims else "")
        return "预测有效度可信，可适当提高模拟实测分权重"

    @staticmethod
    def _bucket_hit(pred_b: str, actual_b: str) -> float:
        if pred_b == actual_b:
            return 1.0
        if _POLARITY.get(pred_b) == _POLARITY.get(actual_b):
            return 0.5
        return 0.0

    @staticmethod
    def _majority(items: list) -> str:
        if not items:
            return "neutral"
        counts: dict[str, int] = {}
        for x in items:
            counts[x] = counts.get(x, 0) + 1
        return max(counts.items(), key=lambda kv: kv[1])[0]

    def _count_pending(self, person_id: str) -> int:
        row = self.db.fetch_one(
            "SELECT COUNT(*) AS c FROM prediction_records WHERE person_id=:pid AND status='pending'",
            {"pid": person_id})
        return int((row or {}).get("c", 0))

    @staticmethod
    def _loads(text) -> dict:
        try:
            return json.loads(text) if text else {}
        except Exception:
            return {}

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
