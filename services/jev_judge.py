# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - Jev 轻量判断层（JevJudge）

借鉴"Jev协agent"的定位：Jev 是专门做"判断"的 AI 模型——
不生成内容，只在给定选项中做选择/分类/打分，极廉价、极快。
在 LoveAI 中作为 LLM 调用前的预过滤器，减少昂贵 LLM 调用。

设计原则：
  - 纯本地规则实现，判断速度 < 10ms；
  - 内置中文正/负面情感词典（各 50+ 词）；
  - 所有方法返回结构化 dict，绝不抛异常；
  - 可选 LLM 增强模式（构造时传入 llm 实例）：本地规则先判，LLM 二次确认；
  - 手段中立：只做技术分类/打分，不做道德评价。

服务层纯 Python 实现：不 import PySide6，不依赖 Qt。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from services.llm_gateway import LLMGateway

logger = logging.getLogger(__name__)


class JevJudge:
    """轻量级判断层：消息分类 / 意图选择 / 情绪分类 / 紧急度 / 动作选择。

    Attributes:
        llm: 可选；LLMGateway 实例。传入后部分方法会做 LLM 二次确认，
              未传入时纯本地规则，毫秒级返回。
    """

    # ------------------------------------------------------------------ #
    # 内置情感词典（中文正/负面关键词各 50+）
    # ------------------------------------------------------------------ #
    _POS_WORDS: tuple = (
        "喜欢", "爱你", "想你", "开心", "高兴", "快乐", "幸福", "甜蜜",
        "心动", "暖心", "感动", "惊喜", "期待", "愿意", "好呀", "好的",
        "没问题", "谢谢你", "感谢", "么么哒", "抱抱", "亲亲", "晚安",
        "早安", "加油", "真棒", "厉害", "优秀", "好看", "漂亮", "帅",
        "温柔", "体贴", "在乎", "重视", "珍惜", "信任", "理解", "支持",
        "陪伴", "在一起", "约会", "见面", "想见面", "梦到你", "好幸福",
        "美滋滋", "嘿嘿", "哈哈", "嘻嘻", "爱了", "喜欢死了",
    )

    _NEG_WORDS: tuple = (
        "讨厌", "烦", "累", "心累", "失望", "伤心", "难过", "难受",
        "生气", "愤怒", "火大", "气炸", "分手", "冷战", "拉黑", "删除",
        "别再", "不想理", "不想说", "不想见", "无所谓", "随便", "算了",
        "滚", "去死", "垃圾", "恶心", "厌恶", "鄙视", "看不起", "敷衍",
        "欺骗", "撒谎", "背叛", "出轨", "冷落", "忽略", "忽视", "压力",
        "焦虑", "焦虑不安", "担心", "害怕", "恐惧", "孤独", "寂寞",
        "委屈", "憋屈", "不爽", "不满", "抱怨", "指责", "吵架", "矛盾",
        "心累", "好累", "疲惫", "麻木",
    )

    # 情绪细分词典（用于 classify_emotion_quick 的 happy/angry/sad）
    _HAPPY_WORDS: tuple = (
        "哈哈", "嘻嘻", "嘿嘿", "开心", "高兴", "快乐", "幸福",
        "美滋滋", "太棒", "太好了", "好耶", "哇", "惊喜",
    )
    _ANGRY_WORDS: tuple = (
        "生气", "愤怒", "火大", "气炸", "靠", "草", "妈的", "操",
        "滚", "烦死", "可恶", "气死", "怒",
    )
    _SAD_WORDS: tuple = (
        "伤心", "难过", "难受", "委屈", "憋屈", "失望", "孤独",
        "寂寞", "想哭", "哭", "心碎", "失落", "低落", "心累",
    )

    # ------------------------------------------------------------------ #
    # 消息分类关键词
    # ------------------------------------------------------------------ #
    _AD_KEYWORDS: tuple = (
        "优惠券", "折扣", "下单", "购买", "点击链接", "加微信",
        "刷单", "代理", "招商", "加盟", "免费领", "福利", "推广",
        "促销", "爆款", "限时", "抢购", "加群", "私聊优惠",
    )
    _SPAM_KEYWORDS: tuple = (
        "验证码", "中奖", "恭喜您", "请点击链接", "退订",
        "贷款", "信用卡", "代开发票", "兼职", "日赚",
        "赌博", "博彩", "色情", "代孕", "您的账户异常",
        "账户状态异常", "将被冻结", "请核实身份",
    )
    _NOTIFY_KEYWORDS: tuple = (
        "提醒", "通知", "公告", "系统", "审批", "已完成", "已发货",
        "已签收", "订单", "物流", "还款", "缴费", "截止", "日程",
        "日历", "会议", "待办", "待处理",
    )
    _URGENCY_KEYWORDS: tuple = (
        "急", "马上", "立刻", "现在", "赶紧", "出事了", "紧急",
        "救命", "出事", "来不及", "速回", "务必", "十万火急",
        "情况", "很严重", "快点",
    )
    _TIME_SENSITIVE_KEYWORDS: tuple = (
        "今天", "今晚", "现在", "马上", "马上就", "五分钟", "半小时",
        "一小时内", "中午", "晚上", "截止", "deadline", "立刻",
    )

    # 客户/商务类关键词
    _CUSTOMER_KEYWORDS: tuple = (
        "你好", "在吗", "请问", "咨询", "报价", "价格", "多少钱",
        "采购", "合作", "订单", "合同", "发票", "对公", "业务",
        "麻烦", "麻烦你", "麻烦您",
    )

    # ------------------------------------------------------------------ #
    def __init__(self, llm: Optional["LLMGateway"] = None) -> None:
        """
        初始化 JevJudge。

        Args:
            llm: 可选 LLMGateway 实例。传入后，classify_message / judge_intent
                  等方法会在本地规则之上做一次 LLM 二次确认；不传则纯本地规则。
        """
        self.llm = llm
        logger.info("JevJudge 初始化完成, llm=%s",
                    "enabled" if llm else "disabled")

    # ------------------------------------------------------------------ #
    # 1. 消息分类
    # ------------------------------------------------------------------ #
    def classify_message(self, text: str) -> dict:
        """
        消息分类：广告 / 客户 / 通知 / 社交 / 紧急 / 垃圾（6 类）。

        Args:
            text: 待分类文本。

        Returns:
            {"category": "social", "confidence": 0.0-1.0, "reason": "..."}
            无法判断时返回 category="social", confidence=0.5。
        """
        try:
            text = (text or "").strip()
            if not text:
                return {"category": "social", "confidence": 0.3,
                        "reason": "空文本，默认归为社交"}

            # 紧急优先
            if any(k in text for k in self._URGENCY_KEYWORDS):
                result = {"category": "urgent", "confidence": 0.85,
                          "reason": "命中紧急关键词"}
            # 垃圾（先于广告判断：spam 特征更明确，如中奖/验证码）
            elif any(k in text for k in self._SPAM_KEYWORDS):
                result = {"category": "spam", "confidence": 0.85,
                          "reason": "命中垃圾/诈骗关键词"}
            # 广告
            elif any(k in text for k in self._AD_KEYWORDS):
                result = {"category": "ad", "confidence": 0.8,
                          "reason": "命中广告推广关键词"}
            # 通知
            elif any(k in text for k in self._NOTIFY_KEYWORDS):
                result = {"category": "notify", "confidence": 0.75,
                          "reason": "命中系统通知关键词"}
            # 客户/商务
            elif any(k in text for k in self._CUSTOMER_KEYWORDS):
                result = {"category": "customer", "confidence": 0.7,
                          "reason": "命中客户咨询关键词"}
            else:
                result = {"category": "social", "confidence": 0.6,
                          "reason": "未命中特殊规则，默认社交消息"}

            # 可选 LLM 二次确认
            if self.llm is not None:
                confirmed = self._llm_confirm_classify(text, result["category"])
                if confirmed:
                    result["llm_confirm"] = confirmed

            return result
        except Exception as e:  # noqa: BLE001
            logger.warning("classify_message 异常: %s", e)
            return {"category": "social", "confidence": 0.5,
                    "reason": f"内部异常兜底: {e}"}

    def _llm_confirm_classify(self, text: str, local_cat: str) -> dict:
        """LLM 二次确认分类（可选增强）。失败时静默返回空。"""
        try:
            system = ("你是消息分类器。只输出一个词："
                      "ad / customer / notify / social / urgent / spam。")
            user = f"文本：{text[:200]}\n本地初判：{local_cat}\n请确认或修正："
            raw = self.llm.chat(system, user, complexity="low")
            raw = (raw or "").strip().strip('"').strip("'").lower()
            valid = {"ad", "customer", "notify", "social", "urgent", "spam"}
            for v in valid:
                if v in raw:
                    return {"category": v, "confidence": 0.9}
        except Exception as e:  # noqa: BLE001
            logger.debug("LLM 确认分类失败: %s", e)
        return {}

    # ------------------------------------------------------------------ #
    # 2. 意图判断（从给定选项中选）
    # ------------------------------------------------------------------ #
    def judge_intent(self, text: str, options: list) -> dict:
        """
        意图判断：从给定选项列表中选最匹配的一个。

        Args:
            text: 用户输入文本。
            options: 候选意图字符串列表，例如 ["约饭", "表白", "道歉", "吐槽"]。

        Returns:
            {"index": 选中的下标, "option": "对应文本",
             "confidence": 0.0-1.0, "reason": "..."}
            options 为空时返回 index=-1。
        """
        try:
            text = text or ""
            if not options:
                return {"index": -1, "option": "",
                        "confidence": 0.0, "reason": "options 为空"}

            # 本地打分：每个 option 与 text 的字符重合度
            best_idx = 0
            best_score = -1.0
            scores = []
            for i, opt in enumerate(options):
                opt = str(opt or "")
                # 简单字符重合度
                overlap = sum(1 for ch in opt if ch in text)
                score = overlap / max(1, len(opt))
                # 完全包含加分
                if opt and opt in text:
                    score += 0.5
                scores.append(score)
                if score > best_score:
                    best_score = score
                    best_idx = i

            confidence = max(0.3, min(0.9, 0.4 + best_score))
            return {
                "index": best_idx,
                "option": str(options[best_idx]),
                "confidence": round(confidence, 3),
                "reason": f"本地规则匹配，最高分={best_score:.2f}",
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("judge_intent 异常: %s", e)
            return {"index": 0, "option": str(options[0]) if options else "",
                    "confidence": 0.5, "reason": f"异常兜底: {e}"}

    # ------------------------------------------------------------------ #
    # 3. 快速情绪分类
    # ------------------------------------------------------------------ #
    def classify_emotion_quick(self, text: str) -> dict:
        """
        快速情绪分类：positive / negative / neutral / angry / sad / happy。

        本地关键词 + 情感词典，毫秒级返回。

        Args:
            text: 待分类文本。

        Returns:
            {"emotion": "happy", "confidence": 0.0-1.0,
             "pos_hits": N, "neg_hits": N, "reason": "..."}
        """
        try:
            text = text or ""
            pos = sum(1 for w in self._POS_WORDS if w in text)
            neg = sum(1 for w in self._NEG_WORDS if w in text)
            happy = sum(1 for w in self._HAPPY_WORDS if w in text)
            angry = sum(1 for w in self._ANGRY_WORDS if w in text)
            sad = sum(1 for w in self._SAD_WORDS if w in text)

            # 细分情绪优先：happy/angry/sad 命中即返回
            if angry >= 1 and angry >= happy and angry >= sad:
                return {"emotion": "angry", "confidence": 0.8,
                        "pos_hits": pos, "neg_hits": neg,
                        "reason": f"命中愤怒词{angry}个"}
            if sad >= 1 and sad >= happy:
                return {"emotion": "sad", "confidence": 0.75,
                        "pos_hits": pos, "neg_hits": neg,
                        "reason": f"命中悲伤词{sad}个"}
            if happy >= 1:
                return {"emotion": "happy", "confidence": 0.8,
                        "pos_hits": pos, "neg_hits": neg,
                        "reason": f"命中快乐词{happy}个"}

            # 粗粒度：positive / negative / neutral
            if pos > neg:
                return {"emotion": "positive", "confidence": 0.7,
                        "pos_hits": pos, "neg_hits": neg,
                        "reason": f"正面词{pos} > 负面词{neg}"}
            if neg > pos:
                return {"emotion": "negative", "confidence": 0.7,
                        "pos_hits": pos, "neg_hits": neg,
                        "reason": f"负面词{neg} > 正面词{pos}"}
            return {"emotion": "neutral", "confidence": 0.6,
                    "pos_hits": pos, "neg_hits": neg,
                    "reason": "无明显情感倾向"}
        except Exception as e:  # noqa: BLE001
            logger.warning("classify_emotion_quick 异常: %s", e)
            return {"emotion": "neutral", "confidence": 0.5,
                    "pos_hits": 0, "neg_hits": 0,
                    "reason": f"异常兜底: {e}"}

    # ------------------------------------------------------------------ #
    # 4. 紧急度判断
    # ------------------------------------------------------------------ #
    def judge_urgency(self, text: str) -> dict:
        """
        紧急度判断：0-10 分，判断是否需要立即回复。

        Args:
            text: 待判断文本。

        Returns:
            {"score": 0-10, "need_immediate": bool,
             "hits": [...命中的关键词], "reason": "..."}
        """
        try:
            text = text or ""
            hits = [k for k in self._URGENCY_KEYWORDS if k in text]
            time_hits = [k for k in self._TIME_SENSITIVE_KEYWORDS if k in text]

            # 基础分：每个紧急词 +2，每个时间敏感词 +1
            score = len(hits) * 2 + len(time_hits) * 1
            # 长度过短（单字"急"）也加一点
            if "急" in text and len(text) <= 10:
                score += 1
            score = max(0, min(10, score))

            return {
                "score": score,
                "need_immediate": score >= 5,
                "hits": hits + time_hits,
                "reason": f"紧急词{len(hits)}个 + 时间敏感词{len(time_hits)}个",
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("judge_urgency 异常: %s", e)
            return {"score": 0, "need_immediate": False,
                    "hits": [], "reason": f"异常兜底: {e}"}

    # ------------------------------------------------------------------ #
    # 5. 动作选择
    # ------------------------------------------------------------------ #
    def select_action(self, state_description: str, actions: list) -> dict:
        """
        动作选择：从给定动作列表中选当前状态下最优动作。

        Args:
            state_description: 当前状态描述（自然语言）。
            actions: 候选动作字符串列表。

        Returns:
            {"index": 选中下标, "action": "...",
             "confidence": 0.0-1.0, "reason": "..."}
        """
        try:
            state_text = state_description or ""
            if not actions:
                return {"index": -1, "action": "",
                        "confidence": 0.0, "reason": "actions 为空"}

            # 先粗判状态情绪/紧急度
            emo = self.classify_emotion_quick(state_text)
            urg = self.judge_urgency(state_text)

            # 本地打分：动作关键词与状态描述的重合度
            best_idx = 0
            best_score = -1.0
            for i, act in enumerate(actions):
                act = str(act or "")
                overlap = sum(1 for ch in act if ch in state_text)
                score = overlap / max(1, len(act))

                # 状态启发式加权
                # 状态紧急 -> 偏好"回复/立即/处理"类动作
                if urg["need_immediate"]:
                    if any(k in act for k in ("回复", "立即", "马上", "处理", "回电")):
                        score += 0.3
                # 负面情绪 -> 偏好"安慰/道歉/倾听"类动作
                if emo["emotion"] in ("negative", "angry", "sad"):
                    if any(k in act for k in ("安慰", "道歉", "倾听", "陪伴", "理解")):
                        score += 0.3
                # 正面情绪 -> 偏好"升温/约会/推进"类动作
                if emo["emotion"] in ("positive", "happy"):
                    if any(k in act for k in ("升温", "约会", "推进", "表白", "见面")):
                        score += 0.3

                if score > best_score:
                    best_score = score
                    best_idx = i

            confidence = max(0.4, min(0.85, 0.4 + best_score))
            return {
                "index": best_idx,
                "action": str(actions[best_idx]),
                "confidence": round(confidence, 3),
                "reason": (f"情绪={emo['emotion']}, 紧急度={urg['score']}, "
                           f"最高分={best_score:.2f}"),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("select_action 异常: %s", e)
            return {"index": 0, "action": str(actions[0]) if actions else "",
                    "confidence": 0.5, "reason": f"异常兜底: {e}"}

    # ------------------------------------------------------------------ #
    # 6. 批量判断
    # ------------------------------------------------------------------ #
    def batch_judge(self, items: list, judge_type: str) -> list:
        """
        批量判断：对多条文本执行同一判断类型。

        Args:
            items: 文本列表，或 judge_type=intent/action 时每项为
                   {"text":..., "options": [...]} 的 dict。
            judge_type: 可选 message / emotion / urgency / intent / action。

        Returns:
            list: 与 items 等长的判断结果列表，每项为结构化 dict。
        """
        results: list = []
        try:
            for item in (items or []):
                try:
                    if judge_type == "message":
                        text = item if isinstance(item, str) else str(item)
                        results.append(self.classify_message(text))
                    elif judge_type == "emotion":
                        text = item if isinstance(item, str) else str(item)
                        results.append(self.classify_emotion_quick(text))
                    elif judge_type == "urgency":
                        text = item if isinstance(item, str) else str(item)
                        results.append(self.judge_urgency(text))
                    elif judge_type == "intent":
                        if isinstance(item, dict):
                            results.append(self.judge_intent(
                                item.get("text", ""), item.get("options", [])))
                        else:
                            results.append(self.judge_intent(str(item), []))
                    elif judge_type == "action":
                        if isinstance(item, dict):
                            results.append(self.select_action(
                                item.get("state", ""), item.get("actions", [])))
                        else:
                            results.append(self.select_action(str(item), []))
                    else:
                        results.append({"error": f"未知 judge_type: {judge_type}"})
                except Exception as e:  # noqa: BLE001
                    logger.warning("batch_judge 单项异常: %s", e)
                    results.append({"error": str(e)})
        except Exception as e:  # noqa: BLE001
            logger.warning("batch_judge 整体异常: %s", e)
        return results
