"""
LoveAI v2.0 - 情感分析服务
对聊天消息进行情绪分类、强度评估、关键词提取。
两层架构：规则匹配（快速、本地、无API调用）为主力，LLM增强为可选。
阶段一以规则匹配为主，LLM增强仅预留接口。
"""
import logging
import re
from typing import Optional, Any

logger = logging.getLogger(__name__)


class EmotionAnalyzer:
    """情感分析器：规则匹配 + 可选LLM增强。

    7+1 种基础标签：happy(开心) / love(爱慕) / excited(兴奋) / neutral(中性)
    / confused(疑惑) / sad(难过) / angry(生气) / cold(冷淡)。
    """

    # 平局时的优先级（越靠前越优先）：angry > sad > love > excited > happy > confused > neutral > cold
    LABEL_PRIORITY = [
        "angry", "sad", "love", "excited",
        "happy", "confused", "neutral", "cold",
    ]

    # 情感标签对应的颜色（前端弹幕显示）
    EMOTION_COLORS = {
        "happy": "#4ade80",     # 绿色 = 积极
        "love": "#4ade80",      # 绿色 = 积极
        "excited": "#4ade80",   # 绿色 = 积极
        "neutral": "#fbbf24",    # 黄色 = 中性
        "confused": "#c084fc",   # 紫色 = 言外之意
        "sad": "#f87171",       # 红色 = 消极/危机
        "angry": "#f87171",      # 红色 = 消极/危机
        "cold": "#f87171",       # 红色 = 消极/危机
    }

    # 生理周期症状关键词（只做叠加标记，不参与主标签判定，避免破坏现有分类）
    PERIOD_KEYWORDS = [
        "姨妈", "痛经", "肚子疼", "肚子痛", "不舒服", "流血",
        "经期", "大姨妈", "生理期", "来事了", "腰酸",
    ]

    def __init__(self, llm_gateway: Any = None):
        """
        初始化情感分析器。

        Args:
            llm_gateway: 可选的 LLM 网关实例，用于 LLM 增强分析；
                          阶段一可传 None，仅使用规则匹配。
        """
        self.llm = llm_gateway

        # 关键词词典：按情感标签分组（注意部分词跨标签，需结合词频与优先级判定）
        self.keyword_dict: dict[str, list[str]] = {
            "happy": ['哈哈', '开心', '高兴', '好呀', '好啊', '可以', '行',
                      '嗯！', '太棒了', '喜欢', '爱了', '嘻嘻', '嘿嘿'],
            "love": ['想你', '喜欢', '爱你', '宝贝', '亲爱的', '么么',
                     '抱抱', '亲亲', '心动', '暧昧'],
            "excited": ['哇', '天啊', '真的吗', '太好', '激动', '期待', '终于'],
            "neutral": ['嗯', '哦', '好的', '知道了', '行吧', '随便', '都可以'],
            "confused": ['什么', '为啥', '为什么', '怎么', '？？',
                         '啥意思', '不懂', '不明白'],
            "sad": ['难过', '伤心', '哭', '不开心', '失落', '沮丧',
                    '郁闷', '心烦', '累', '失望', '委屈', '心寒', '心碎',
                    '难过死了', '好失望', '心寒了'],
            "angry": ['生气', '烦', '滚', '闭嘴', '讨厌', '气死',
                      '愤怒', '别烦我', '不在乎', '凭什么', '气死我',
                      '过分', '哼', '可恶', '气死了'],
            "cold": ['哦', '嗯', '随便', '都行', '不想聊', '静静',
                     '算了', '关你屁事', '无所谓', '呵', '哦呵'],
        }

        # 疑问词（用于疑问句检测）
        self.question_words = ['什么', '为什么', '为啥', '怎么', '吗', '呢']

        logger.info("EmotionAnalyzer 初始化完成")

    # ------------------------------------------------------------------ #
    # 主分析入口
    # ------------------------------------------------------------------ #
    def analyze(self, text: str, use_llm: bool = False) -> dict:
        """
        分析单条消息的情感。

        Args:
            text: 消息文本。
            use_llm: 是否启用 LLM 增强分析（阶段一仅预留接口）。

        Returns:
            dict: 分析结果，包含
                label / intensity(0-10) / confidence(0-1) /
                keywords / secondary_labels / is_question /
                message_length / response_speed_hint(fast/medium/slow)。
        """
        # 1. 预处理
        if text is None:
            text = ""
        cleaned = text.strip()
        message_length = len(cleaned)

        # 空消息直接返回中性默认值
        if not cleaned:
            return self._empty_result()

        # 2. 关键词匹配：遍历所有标签，记录每个标签的命中关键词
        label_hits: dict[str, list[str]] = {}
        all_keywords: list[str] = []
        for label, keywords in self.keyword_dict.items():
            hits = [kw for kw in keywords if kw in cleaned]
            if hits:
                label_hits[label] = hits
                all_keywords.extend(hits)

        # 3. 主标签选择：命中数最多者胜，平局按 LABEL_PRIORITY
        if label_hits:
            sorted_labels = sorted(
                label_hits.items(),
                key=lambda item: (-len(item[1]), self._priority_rank(item[0])),
            )
            primary_label = sorted_labels[0][0]
            primary_count = len(sorted_labels[0][1])
            secondary_labels = [lab for lab, _ in sorted_labels[1:]]
        else:
            primary_label = "neutral"
            primary_count = 0
            secondary_labels = []

        # 4. 强度计算（0-10）
        intensity = self._calculate_intensity(cleaned, all_keywords, primary_count)

        # 5. 置信度
        if primary_count == 0:
            confidence = 0.3
        else:
            confidence = round(min(0.95, 0.5 + 0.1 * primary_count), 2)

        # 6. 疑问句检测
        is_question = self._detect_question(cleaned)

        # 7. 回复速度暗示
        speed_hint = self._estimate_speed(cleaned, primary_label, intensity)

        # 8. LLM 增强（可选，阶段一留接口）
        if use_llm and self.llm is not None:
            try:
                llm_result = self._llm_enhance(cleaned, primary_label)
                if llm_result:
                    primary_label = llm_result.get("label", primary_label)
                    intensity = llm_result.get("intensity", intensity)
                    confidence = llm_result.get("confidence", confidence)
            except Exception as e:
                logger.warning(f"LLM 增强分析失败，回退规则结果: {e}")

        result = {
            "label": primary_label,
            "intensity": intensity,
            "confidence": confidence,
            "keywords": all_keywords,
            "secondary_labels": secondary_labels,
            "is_question": is_question,
            "message_length": message_length,
            "response_speed_hint": speed_hint,
        }

        # 9. 生理周期影响检测（叠加层：只加标记，不改主标签/强度/置信度）
        period_hits = [kw for kw in self.PERIOD_KEYWORDS if kw in cleaned]
        if period_hits:
            result["period_influenced"] = True
            result["period_keywords"] = period_hits
            result["note"] = "可能受生理周期影响"
        else:
            result["period_influenced"] = False
            result["period_keywords"] = []
            result["note"] = ""

        logger.debug(
            f"情感分析: '{cleaned[:20]}' -> {primary_label} "
            f"(强度{intensity}, 置信{confidence})"
        )
        return result

    # ------------------------------------------------------------------ #
    # 批量分析
    # ------------------------------------------------------------------ #
    def batch_analyze(self, messages: list) -> list:
        """
        批量分析消息列表，每条调用 analyze。

        Args:
            messages: [{"content": "...", "direction": "sent/received"}, ...]

        Returns:
            list[dict]: 每条消息的分析结果列表，附带 direction 字段。
        """
        results: list[dict] = []
        for msg in messages:
            content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
            try:
                result = self.analyze(content)
                if isinstance(msg, dict):
                    result["direction"] = msg.get("direction", "received")
                results.append(result)
            except Exception as e:
                logger.error(f"批量分析单条消息失败: {e}")
                results.append({
                    "label": "neutral", "intensity": 0, "confidence": 0.0,
                    "keywords": [], "secondary_labels": [],
                    "is_question": False, "message_length": len(content),
                    "response_speed_hint": "medium",
                    "period_influenced": False, "period_keywords": [], "note": "",
                })
        logger.info(f"批量分析完成: {len(results)} 条消息")
        return results

    # ------------------------------------------------------------------ #
    # 颜色映射
    # ------------------------------------------------------------------ #
    def get_emotion_color(self, label: str) -> str:
        """
        返回情感标签对应的十六进制颜色值（用于前端弹幕显示）。

        - happy / love / excited → "#4ade80"（绿色 = 积极）
        - neutral → "#fbbf24"（黄色 = 中性）
        - confused → "#c084fc"（紫色 = 言外之意）
        - sad / angry / cold → "#f87171"（红色 = 消极/危机）
        """
        return self.EMOTION_COLORS.get(label, "#fbbf24")

    # ------------------------------------------------------------------ #
    # 内部工具方法
    # ------------------------------------------------------------------ #
    def _calculate_intensity(self, text: str, keyword_hits: list,
                              primary_count: int = 0) -> int:
        """
        计算情感强度（0-10）。

        计分规则：
            - 基础分：每个命中关键词 +2
            - 感叹号：每个 ！/! +1（上限 +3）
            - 重复字：连续重复 2 次 +1（如"哈哈"），4 次及以上再 +1（如"哈哈哈哈"）
            - 主标签多词命中：同一主标签命中 2 个及以上关键词，+1
            - 省略号/句号结尾：-1（冷淡信号）
            - 单字/双字回复（如"哦""嗯"）：强度上限 2
            - 最终 clamp 到 [0, 10]
        """
        score = 0

        # 基础分：命中关键词数量
        score += len(keyword_hits) * 2

        # 感叹号加分（上限 +3）
        exclaim_count = text.count("！") + text.count("!")
        score += min(exclaim_count, 3)

        # 重复字检测：任意字符连续重复
        max_run = self._max_repeat_run(text)
        if max_run >= 4:
            score += 2
        elif max_run >= 2:
            score += 1

        # 主标签多词命中加成（同一情绪用多个词表达 = 强度更高）
        if primary_count >= 2:
            score += 1

        # 省略号/句号结尾扣分（冷淡信号）
        stripped = text.rstrip()
        if stripped.endswith(("。", "...", "。。。", "……")):
            score -= 1

        # 单字/双字回复强度限制
        if len(stripped) <= 2:
            score = min(score, 2)

        # 边界
        score = max(0, min(10, score))
        return score

    def _max_repeat_run(self, text: str) -> int:
        """返回文本中任意字符连续重复的最大次数（如"哈哈哈哈" -> 4）。"""
        max_run = 1
        current = 1
        for i in range(1, len(text)):
            if text[i] == text[i - 1]:
                current += 1
                max_run = max(max_run, current)
            else:
                current = 1
        return max_run

    def _detect_question(self, text: str) -> bool:
        """检测文本是否为疑问句（含问号或疑问词）。"""
        if "？" in text or "?" in text:
            return True
        for word in self.question_words:
            if word in text:
                return True
        return False

    def _estimate_speed(self, text: str, label: str, intensity: int) -> str:
        """
        根据文本与情感估计回复速度暗示。

        Returns:
            "fast"   - 短消息、感叹号、积极情绪，应快速回应
            "medium" - 普通陈述句，正常节奏
            "slow"   - 冷淡/省略号/长消极消息，宜慢回或冷处理
        """
        # 省略号 / 冷淡标签 → 慢
        if "..." in text or "。。" in text or "……" in text:
            return "slow"
        if label == "cold":
            return "slow"
        # 强积极 + 感叹号/短消息 → 快
        if label in ("happy", "love", "excited"):
            if "！" in text or "!" in text or len(text) <= 10:
                return "fast"
        if intensity >= 7 and ("！" in text or "!" in text):
            return "fast"
        return "medium"

    def _priority_rank(self, label: str) -> int:
        """返回标签在优先级列表中的排名（越小越优先）。"""
        try:
            return self.LABEL_PRIORITY.index(label)
        except ValueError:
            return len(self.LABEL_PRIORITY)

    def _llm_enhance(self, text: str, current_label: str) -> Optional[dict]:
        """
        阶段二：LLM 精细情绪判定（规则之上的增强层）。

        通过注入的 LLM 网关做更细腻的情绪判断，返回
        {"label": ..., "intensity": ..., "confidence": ...}。

        安全降级（绝不崩）：
          - 网关不可用 / 无 chat 方法 / 超时 / 抛异常 → 返回 None，回退规则结果；
          - 网关走 ``allow_rule_fallback=False``，无真实模型时返回空串 → 返回 None；
          - 返回无法解析成合法 JSON，或 label 不在允许集合内 → 返回 None。
        """
        llm = self.llm
        if llm is None or not callable(getattr(llm, "chat", None)):
            return None

        allowed = ", ".join(self.LABEL_PRIORITY)
        system_prompt = (
            "你是一名中文情感分析器。只输出 JSON，不要解释。\n"
            f"合法情绪标签只能是以下之一：{allowed}。\n"
            "强度 intensity 取 0-10 整数；置信度 confidence 取 0.0-1.0。\n"
            '输出形如：{"label": "happy", "intensity": 6, "confidence": 0.8}'
        )
        user_prompt = (
            f"消息：{text}\n"
            f"规则初判标签：{current_label}\n"
            "请结合语气、标点、上下文微妙情绪，给出更准确的标签/强度/置信度。"
        )

        try:
            raw = llm.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                complexity="l1",
                temperature=0.0,
                max_tokens=120,
                allow_rule_fallback=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 情绪增强调用失败，回退规则结果: %s", e)
            return None

        if not raw or not str(raw).strip():
            return None

        data = self._parse_llm_emotion_json(str(raw))
        if not data:
            logger.debug("LLM 情绪增强返回无法解析，回退规则结果")
            return None

        label = data.get("label")
        if label not in self.LABEL_PRIORITY:
            return None

        try:
            intensity = int(round(float(data.get("intensity", 0))))
        except (TypeError, ValueError):
            intensity = None
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = None

        result: dict = {"label": label}
        if intensity is not None:
            result["intensity"] = max(0, min(10, intensity))
        if confidence is not None:
            result["confidence"] = max(0.0, min(1.0, confidence))
        return result

    def _parse_llm_emotion_json(self, raw: str) -> Optional[dict]:
        """从 LLM 返回里抠出 JSON 对象并解析；失败返回 None。"""
        import json as _json
        import re as _re
        try:
            # 直接尝试整体解析
            return _json.loads(raw)
        except Exception:  # noqa: BLE001
            pass
        m = _re.search(r"\{[^{}]*\}", raw, _re.S)
        if not m:
            return None
        try:
            return _json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            return None

    def _empty_result(self) -> dict:
        """空消息的默认分析结果。"""
        return {
            "label": "neutral",
            "intensity": 1,
            "confidence": 0.3,
            "keywords": [],
            "secondary_labels": [],
            "is_question": False,
            "message_length": 0,
            "response_speed_hint": "medium",
            "period_influenced": False,
            "period_keywords": [],
            "note": "",
        }

    # ------------------------------------------------------------------ #
    # v2 新增：0-100 情绪强度（不破坏现有 0-10 的 intensity）
    # ------------------------------------------------------------------ #
    # 极端词/脏话：命中即高强度（"烦死了" > "有点烦"）
    _EXTREME_WORDS = [
        "烦死了", "气死我", "气死了", "气炸", "疯了", "崩溃",
        "滚", "闭嘴", "滚蛋", "有病", "无语",
        "心碎", "心寒", "心凉", "绝望", "完蛋",
        "太开心了", "乐死了", "爱死", "感动死",
    ]

    def intensity_100(self, text: str, *,
                      reply_speed: Optional[str] = None) -> dict:
        """估计一条消息的 0-100 情绪强度（与现有 0-10 intensity 并存）。

        依据：
          - 感叹号数量：每个 ！/! +6，上限 +24；
          - 极端词/脏话：命中一个 +18，上限 +45；
          - 正向重复（哈哈/嘻嘻/嘿嘿/哇 出现）：+15；
          - 回复长度：长文(>50字) +10，超短(<=3字) -15；
          - 回复速度（外部传入）：fast(秒回) +10，slow(半天) -10。

        Args:
            text: 消息文本。
            reply_speed: 可选，"fast"/"medium"/"slow"，由上层按时间差判断。

        Returns:
            {"intensity_100": 0-100 整数,
             "signals": {"exclaim": int, "extreme_hits": [...],
                         "length_bonus": int, "speed_bonus": int,
                         "rep_bonus": int}}
        """
        t = (text or "").strip()
        score = 25  # 基线
        exclaim = t.count("！") + t.count("!")
        score += min(exclaim * 6, 24)

        extreme_hits = [w for w in self._EXTREME_WORDS if w in t]
        score += min(len(extreme_hits) * 18, 45)

        # 正向重复：哈哈/嘻嘻/嘿嘿/哇
        rep_bonus = 15 if any(p in t for p in ("哈哈", "嘻嘻", "嘿嘿", "哇")) else 0
        score += rep_bonus

        length_bonus = 0
        if len(t) > 50:
            length_bonus = 10
        elif len(t) <= 3:
            length_bonus = -15
        score += length_bonus

        speed_bonus = 0
        if reply_speed == "fast":
            speed_bonus = 10
        elif reply_speed == "slow":
            speed_bonus = -10
        score += speed_bonus

        score = max(0, min(100, int(round(score))))
        return {
            "intensity_100": score,
            "signals": {
                "exclaim": exclaim,
                "extreme_hits": extreme_hits,
                "length_bonus": length_bonus,
                "speed_bonus": speed_bonus,
                "rep_bonus": rep_bonus,
            },
        }

    # ------------------------------------------------------------------ #
    # v2 新增：情绪变化追踪（渐变 vs 突变）
    # ------------------------------------------------------------------ #
    SUDDEN_DELTA = 60   # 相邻强度差 >= 60 → 突变
    GRADUAL_DELTA = 15  # 平均相邻差 > 15 且单调 → 渐变

    def track_emotion_trend(self, person_id: Optional[str],
                            recent_messages: list) -> dict:
        """追踪对方最近一串消息的情绪走势。

        Args:
            person_id: 对方 ID（仅用于日志/上下文，不查库）。
            recent_messages: [{"content": str, ...}, ...]，按时间正序。

        Returns:
            {
              "trend": "stable" | "gradual" | "sudden",
              "delta": int,           # 相邻最大强度差
              "warning": str | None,  # 突变提示
              "sequence": [int, ...], # 每条消息的 0-100 强度
              "direction": "up" | "down" | "flat",
            }
        """
        if not recent_messages:
            return {"trend": "stable", "delta": 0, "warning": None,
                    "sequence": [], "direction": "flat"}

        sequence: list[int] = []
        for m in recent_messages:
            content = m.get("content", "") if isinstance(m, dict) else str(m)
            sequence.append(self.intensity_100(content)["intensity_100"])

        if len(sequence) < 2:
            return {"trend": "stable", "delta": 0, "warning": None,
                    "sequence": sequence, "direction": "flat"}

        deltas = [abs(sequence[i] - sequence[i - 1])
                  for i in range(1, len(sequence))]
        max_delta = max(deltas)

        # 方向：末点 - 起点
        net = sequence[-1] - sequence[0]
        direction = "up" if net > 10 else ("down" if net < -10 else "flat")

        warning = None
        if max_delta >= self.SUDDEN_DELTA:
            trend = "sudden"
            warning = ("对方情绪突然变化，注意是不是你说错话了——"
                       "先别追问，冷一下再看。")
        elif sum(deltas) / len(deltas) > self.GRADUAL_DELTA:
            trend = "gradual"
            if direction == "down":
                warning = "对方情绪在慢慢变冷，留意最近是不是话题/节奏出了问题。"
        else:
            trend = "stable"

        return {
            "trend": trend,
            "delta": max_delta,
            "warning": warning,
            "sequence": sequence,
            "direction": direction,
        }

    # ------------------------------------------------------------------ #
    # v2 新增：语境感知情绪（同一句话在不同语境下情绪不同）
    # ------------------------------------------------------------------ #
    # 这些"短话"在不同语境下含义反转，需要靠 context 校正
    _CONTEXT_SENSITIVE_PHRASES = ("随便你", "无所谓", "哦", "呵呵", "都行", "你定")

    def _context_aware_emotion(self, text: str,
                               context: Optional[dict] = None) -> tuple[str, int]:
        """结合语境校正单条消息的情绪类别与强度（0-100）。

        Args:
            text: 消息文本。
            context: 可含：
                in_fight (bool)        —— 最近是不是在吵架；
                ambiguous_warming (bool)—— 是不是刚暧昧升温；
                recent_label (str)     —— 上一条消息的情绪标签。

        Returns:
            (category, intensity_100)。category 取值同主标签集。
            识别不出时回退到规则结果 + 低置信提示，绝不"停住"。
        """
        ctx = context or {}
        base = self.analyze(text)
        label = base.get("label", "neutral")
        inten = self.intensity_100(text)["intensity_100"]

        # 只对"语境敏感短话"做校正
        if not any(p in (text or "") for p in self._CONTEXT_SENSITIVE_PHRASES):
            return label, inten

        in_fight = bool(ctx.get("in_fight", False))
        warming = bool(ctx.get("ambiguous_warming", False))

        if in_fight:
            # 吵架时说"随便你"= 生气
            return "angry", max(inten, 85)
        if warming:
            # 暧昧期说"随便你"= 撒娇/试探
            return "love", max(inten, 60)
        # 平时 = 无所谓/冷淡
        return "cold", min(inten, 30)

    # ------------------------------------------------------------------ #
    # v2 新增：未说出口的需求（FriendFixAI 思路）
    #   不只识别情绪类别，还推测"她没说出来但其实想要什么"。
    # ------------------------------------------------------------------ #
    # 情绪 + 语境 → 未说出口需求
    _UNSPOKEN_NEED_TABLE: dict[tuple[str, str], str] = {
        ("angry", "in_fight"): "其实想要你先服软，给个台阶，不是真要分对错",
        ("angry", "normal"): "其实想要你注意到她在生气，主动问一句",
        ("cold", "normal"): "其实想要你主动找她，但又不想显得自己急",
        ("cold", "long_silence"): "其实在等你先开口，看你在不在乎",
        ("sad", "normal"): "其实想要你听她说，不是急着给建议",
        ("happy", "normal"): "其实想要你顺着夸一句，或者接她的话头",
        ("love", "normal"): "其实想要你也表达一下，别光她一头热",
        ("anxious", "normal"): "其实想要你给个确定答复，别让她猜",
        ("confused", "normal"): "其实想要你把话说清楚，别绕",
    }

    def infer_unspoken_need(self, label: str,
                            context: Optional[dict] = None) -> str:
        """根据情绪标签 + 语境，推测对方"没说出口但其实想要什么"。

        Args:
            label: 情绪标签（analyze 输出的 label）。
            context: 可含 in_fight / long_silence 等语境标记。

        Returns:
            一句话需求描述；识别不出时返回通用兜底（不停住）。
        """
        ctx = context or {}
        key = ""
        if ctx.get("in_fight"):
            key = "in_fight"
        elif ctx.get("long_silence"):
            key = "long_silence"
        else:
            key = "normal"
        need = self._UNSPOKEN_NEED_TABLE.get((label, key))
        if need:
            return need
        # 兜底：按大类给一句
        if label in ("angry", "sad", "cold"):
            return "其实想被你在意一下，别装没看见"
        if label in ("happy", "love", "excited"):
            return "其实想跟你多聊两句，别太快结束"
        return "其实想知道你现在在干嘛，有没有想她"


# ------------------------------------------------------------------ #
# v2 模块级便捷函数（无实例也能用）
# ------------------------------------------------------------------ #
_default_analyzer: Optional[EmotionAnalyzer] = None


def get_default_analyzer() -> EmotionAnalyzer:
    global _default_analyzer
    if _default_analyzer is None:
        _default_analyzer = EmotionAnalyzer()
    return _default_analyzer


def intensity_100(text: str, *, reply_speed: Optional[str] = None) -> int:
    """模块级：0-100 情绪强度。"""
    return get_default_analyzer().intensity_100(
        text, reply_speed=reply_speed)["intensity_100"]


def track_emotion_trend(person_id: Optional[str],
                        recent_messages: list) -> dict:
    """模块级：情绪趋势追踪。"""
    return get_default_analyzer().track_emotion_trend(
        person_id, recent_messages)


# ===== 借鉴 Soul of Waifu：28种情绪扩展 =====
EMOTIONS_28 = {
    "开心":   {"keywords": ["哈哈","开心","高兴","笑死"], "duration_minutes": 60, "decay_curve": "linear",    "affection_impact": +2, "strategy_hint": "顺势聊"},
    "兴奋":   {"keywords": ["太棒了","终于","激动"],       "duration_minutes": 30, "decay_curve": "exponential","affection_impact": +3, "strategy_hint": "同频共振"},
    "满足":   {"keywords": ["满足","挺好","不错"],         "duration_minutes": 120,"decay_curve": "linear",    "affection_impact": +1, "strategy_hint": "稳定延续"},
    "感激":   {"keywords": ["谢谢","感谢","辛苦"],         "duration_minutes": 90, "decay_curve": "exponential","affection_impact": +2, "strategy_hint": "适度回应"},
    "温柔":   {"keywords": ["心疼","抱抱","宝贝"],         "duration_minutes": 90, "decay_curve": "linear",    "affection_impact": +3, "strategy_hint": "回应温柔"},
    "期待":   {"keywords": ["什么时候","期待","想"],       "duration_minutes": 180,"decay_curve": "step",      "affection_impact": +2, "strategy_hint": "推进"},
    "安心":   {"keywords": ["好的","嗯","行"],             "duration_minutes": 60, "decay_curve": "linear",    "affection_impact": +1, "strategy_hint": "轻松延续"},
    "亲密":   {"keywords": ["想你","晚安","早安"],         "duration_minutes": 120,"decay_curve": "linear",    "affection_impact": +4, "strategy_hint": "双向亲密"},
    "感动":   {"keywords": ["哭了","好感动","破防"],       "duration_minutes": 120,"decay_curve": "exponential","affection_impact": +4, "strategy_hint": "深度共情"},
    "自豪":   {"keywords": ["我做到了","骄傲","厉害"],     "duration_minutes": 90, "decay_curve": "exponential","affection_impact": +1, "strategy_hint": "肯定赞美"},
    "难过":   {"keywords": ["难过","伤心","难受","想哭"],  "duration_minutes": 240,"decay_curve": "exponential","affection_impact": -2, "strategy_hint": "陪伴倾听"},
    "愤怒":   {"keywords": ["生气","烦死","气","滚"],      "duration_minutes": 1440,"decay_curve": "step",     "affection_impact": -4, "strategy_hint": "降温共情"},
    "焦虑":   {"keywords": ["焦虑","担心","慌","怕"],      "duration_minutes": 180,"decay_curve": "linear",    "affection_impact": -1, "strategy_hint": "稳定安抚"},
    "失望":   {"keywords": ["失望","算了","无所谓"],       "duration_minutes": 300,"decay_curve": "linear",    "affection_impact": -3, "strategy_hint": "真诚修复"},
    "嫉妒":   {"keywords": ["吃醋","嫉妒","谁啊"],         "duration_minutes": 120,"decay_curve": "exponential","affection_impact": -1, "strategy_hint": "澄清定位"},
    "孤独":   {"keywords": ["一个人","孤独","没人"],       "duration_minutes": 240,"decay_curve": "linear",    "affection_impact": +1, "strategy_hint": "陪伴"},
    "疲惫":   {"keywords": ["累","疲惫","困"],             "duration_minutes": 300,"decay_curve": "linear",    "affection_impact": 0,  "strategy_hint": "不打扰"},
    "无聊":   {"keywords": ["无聊","没意思","没劲"],       "duration_minutes": 180,"decay_curve": "linear",    "affection_impact": 0,  "strategy_hint": "换话题"},
    "尴尬":   {"keywords": ["尴尬","不好意思","社死"],     "duration_minutes": 120,"decay_curve": "exponential","affection_impact": -1, "strategy_hint": "轻松化解"},
    "害怕":   {"keywords": ["害怕","恐惧","吓人"],         "duration_minutes": 180,"decay_curve": "exponential","affection_impact": -1, "strategy_hint": "保护安抚"},
    "平静":   {"keywords": ["平静","还好","一般"],         "duration_minutes": 9999,"decay_curve": "linear",   "affection_impact": 0,  "strategy_hint": "正常交流"},
    "思考":   {"keywords": ["想想","考虑","思考"],         "duration_minutes": 60, "decay_curve": "linear",    "affection_impact": 0,  "strategy_hint": "给空间"},
    "犹豫":   {"keywords": ["犹豫","纠结","不知道"],       "duration_minutes": 120,"decay_curve": "linear",    "affection_impact": 0,  "strategy_hint": "提供选项"},
    "好奇":   {"keywords": ["为什么","什么","真的"],       "duration_minutes": 30, "decay_curve": "exponential","affection_impact": +1, "strategy_hint": "开放话题"},
    "冷漠":   {"keywords": ["哦","嗯","随便"],             "duration_minutes": 9999,"decay_curve": "step",      "affection_impact": -2, "strategy_hint": "不纠缠"},
    "疏离":   {"keywords": ["别烦","空间","独立"],         "duration_minutes": 9999,"decay_curve": "step",      "affection_impact": -2, "strategy_hint": "给空间"},
    "试探":   {"keywords": ["在吗","干嘛","最近"],         "duration_minutes": 30, "decay_curve": "exponential","affection_impact": 0,  "strategy_hint": "回应热度"},
    "防御":   {"keywords": ["不是我","你管","凭什么"],     "duration_minutes": 240,"decay_curve": "step",      "affection_impact": -3, "strategy_hint": "不硬刚"},
}


def classify_emotion_28(text: str) -> dict:
    """返回最匹配的情绪名 + confidence。"""
    if not text:
        return {"emotion": "平静", "confidence": 0.3, "attrs": EMOTIONS_28["平静"]}
    best, best_score = "平静", 0
    for name, attrs in EMOTIONS_28.items():
        score = sum(1 for kw in attrs["keywords"] if kw in text)
        if score > best_score:
            best, best_score = name, score
    conf = min(1.0, 0.3 + best_score * 0.25)
    return {"emotion": best, "confidence": conf, "attrs": EMOTIONS_28[best]}
