"""
LoveAI v2.0 - 情感咨询三轮引擎
总指挥 → 记忆筛选 → 人格化生成。

两种模式：
  self  - 自我成长咨询（专业情感咨询师 + 成长教练视角）
  other - 对象视角/私教模式（恋爱策略顾问，客观分析角度）

安全拦截：自伤 / 暴力 / 违法 → 安全模式，绝不给出具体方法。
服务层，不依赖 PySide6 / QObject。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class ConsultEngine:
    """情感咨询三轮引擎。

    流程：
      第一轮 _routing：意图识别 + 情绪检测 + 安全拦截（本地规则，快速）
      第二轮 记忆筛选：RAG 检索相关恋爱知识 + 情感咨询知识库
      第三轮 人格化生成：_build_consult_prompt 构建 system prompt → llm.chat
      质量门 _quality_gate：长度 / 绝对化表述 / 建设性 三重检查与优化
    """

    # ------------------------------------------------------------------ #
    # 安全拦截关键词（本地规则，快速命中）
    # ------------------------------------------------------------------ #
    _SELF_HARM_KEYWORDS = (
        "自杀", "不想活", "活着没意思", "割腕", "跳楼",
        "自残", "想死", "活不下去", "轻生", "伤害自己",
        "伤自己", "自我伤害",
    )
    _VIOLENCE_KEYWORDS = (
        "打人", "杀了", "弄死", "报复", "教训",
        "揍他", "干他", "打他", "伤害",
    )
    _ILLEGAL_KEYWORDS = (
        "诈骗", "偷窃", "毒品", "赌博", "洗钱",
        "骗钱", "偷东西", "贩毒", "吸毒",
    )

    # 意图识别关键词（按优先级顺序检查，先命中先归类）
    _INTENT_RULES: list[tuple[str, tuple[str, ...]]] = [
        ("求建议", ("怎么办", "怎么", "应该", "如何", "要不要")),
        ("求分析", ("分析", "为什么", "什么意思", "为啥", "原因")),
        ("求话术", ("说什么", "怎么回", "话术", "怎么说", "怎么聊")),
        ("倾诉", ("我觉得", "我感觉", "好难过", "好烦", "心情不好", "好累")),
    ]

    # 绝对化表述 → 非绝对化替换映射
    _ABSOLUTE_REPLACEMENTS: dict[str, str] = {
        "一定": "大概率",
        "肯定": "建议",
        "绝对": "可以考虑",
        "必须": "建议",
    }

    def __init__(
        self,
        llm: Any,
        rag: Any,
        emotion: Optional[Any] = None,
        signals: Optional[Any] = None,
        strategy_advisor: Optional[Any] = None,
    ) -> None:
        """
        初始化咨询引擎。

        Args:
            llm: LLMGateway 实例，用于生成回答。
            rag: RAGService 实例，用于检索恋爱知识库。
            emotion: EmotionAnalyzer 实例（可选），用于本地情感检测；
                      为 None 时仅做简单规则情绪判断。
            signals: AppSignals 实例（可选），用于向外推送状态；
                      本层不直接 import PySide6，通过参数传入。
            strategy_advisor: StrategyAdvisor 实例（可选）。注入后若调用方传入
                      对方画像(persona)，会按对方依恋/冲突风格调整回复措辞；
                      为 None 时行为与旧版一致（向后兼容）。
        """
        self.llm: Any = llm
        self.rag: Any = rag
        self.emotion: Optional[Any] = emotion
        self.signals: Optional[Any] = signals
        self.strategy_advisor: Optional[Any] = strategy_advisor

        #: 当前咨询会话历史，list[dict]，每条含 role/content/timestamp
        self.conversation_history: list[dict] = []

        logger.info("ConsultEngine 初始化完成 (llm=%s, rag=%s, emotion=%s, strategy_advisor=%s)",
                     type(llm).__name__, type(rag).__name__,
                     type(emotion).__name__ if emotion else "None",
                     "on" if strategy_advisor else "off")

    # ================================================================== #
    # 主咨询接口
    # ================================================================== #
    def consult(self, question: str, mode: str = "self",
                persona: Optional[dict] = None) -> dict:
        """
        主咨询接口：三轮引擎。

        Args:
            question: 用户输入的问题/倾诉内容。
            mode: "self"（自我成长咨询）/ "other"（对象视角/私教模式）。
            persona: 对方 21 维画像（可选）。注入 strategy_advisor 时，
                会按对方依恋/冲突风格调整回复措辞；为 None 时行为不变。

        Returns:
            dict: {
                "answer": str,           # 回答文本
                "mode": str,             # self / other / safe
                "intent": str,           # 意图分类
                "emotion": str,          # 情绪标签
                "intercepted": bool,     # 是否被安全拦截
                "sources": list,         # 引用来源（RAG 命中的 source 列表）
            }
        """
        question = (question or "").strip()
        if not question:
            return {
                "answer": "请告诉我你现在遇到了什么问题，我会尽力帮你分析。",
                "mode": mode,
                "intent": "其他",
                "emotion": "neutral",
                "intercepted": False,
                "sources": [],
            }

        # ---- 第一轮：意图识别 + 情绪检测 + 安全拦截 ----
        routing = self._routing(question)
        logger.info(
            "第一轮路由: intent=%s, emotion=%s, intensity=%d, intercept=%s",
            routing["intent"], routing["emotion"],
            routing["emotion_intensity"], routing["intercept"],
        )

        # 安全拦截命中 → 直接走安全模式返回
        if routing["intercept"]:
            result = self._safe_response(routing)
            self._save_history(question, result["answer"])
            return result

        # ---- 第二轮：记忆筛选（RAG 检索相关知识） ----
        knowledge: list = []
        sources: list = []
        try:
            knowledge = self.rag.search(question, top_k=5)
            sources = [k.get("source", "") for k in knowledge if k.get("source")]
        except Exception as e:
            # RAG 检索失败不影响主流程，知识置空
            logger.warning("RAG 检索失败，知识上下文为空: %s", e)
            knowledge = []
            sources = []

        # ---- 知识不足时：网页搜索上下文补全（consult 层显式调用，默认关、不擅自联网）----
        if not knowledge:
            try:
                router = self.llm.get_free_router()
                aug = router.augment_context_with_web_search(question)
                if aug.get("enabled") and aug.get("context"):
                    knowledge = [{
                        "title": "联网补全（用户已开启网页搜索）",
                        "content": aug["context"],
                        "source": "web_search",
                    }]
                    for s in aug.get("sources", []) or []:
                        sources.append(str(s))
                    logger.info("已用网页搜索补全上下文（used_web=%s）",
                                aug.get("used_web"))
            except Exception as e:  # noqa: BLE001
                logger.info("网页搜索上下文补全未执行: %s", e)

        # ---- 第三轮：人格化生成 ----
        system_prompt = self._build_consult_prompt(
            mode=mode,
            routing=routing,
            knowledge=knowledge,
            persona=persona,
        )
        user_prompt = self._build_user_prompt(question, routing)

        try:
            answer = self.llm.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                complexity="complex",
                temperature=0.7,
                max_tokens=2000,
            )
        except Exception as e:
            # LLM 调用失败 → 兜底回应
            logger.error("LLM 生成回答失败: %s", e)
            answer = "抱歉，当前无法生成回答，请稍后再试。"

        # ---- 质量门 ----
        answer = self._quality_gate(answer)

        # ---- 保存会话历史 ----
        self._save_history(question, answer)

        return {
            "answer": answer,
            "mode": mode,
            "intent": routing["intent"],
            "emotion": routing["emotion"],
            "intercepted": False,
            "sources": sources,
        }

    # ================================================================== #
    # 第一轮：路由（意图 + 情绪 + 安全拦截）
    # ================================================================== #
    def _routing(self, question: str) -> dict:
        """
        第一轮路由：意图识别 + 情绪检测 + 安全拦截。

        全部使用本地规则，不调 LLM，保持快速响应。

        Args:
            question: 用户输入文本。

        Returns:
            dict: {
                "intent": str,               # 倾诉/求建议/求分析/求话术/其他
                "emotion": str,               # 情绪标签
                "emotion_intensity": int,     # 0-10
                "intercept": bool,            # 是否命中安全拦截
                "intercept_reason": str,      # "" / "自伤" / "暴力" / "违法"
            }
        """
        # ---- 安全关键词检测（优先级最高） ----
        intercept_reason = self._detect_safety(question)
        intercepted = bool(intercept_reason)

        # ---- 情绪检测：优先用 EmotionAnalyzer，否则简单规则 ----
        emotion_label = "neutral"
        emotion_intensity = 0
        if self.emotion is not None:
            try:
                emo_result = self.emotion.analyze(question)
                emotion_label = emo_result.get("label", "neutral")
                emotion_intensity = int(emo_result.get("intensity", 0))
            except Exception as e:
                logger.warning("EmotionAnalyzer 调用失败，使用默认情绪: %s", e)
                emotion_label, emotion_intensity = self._simple_emotion(question)
        else:
            emotion_label, emotion_intensity = self._simple_emotion(question)

        # ---- 意图识别（本地规则，按优先级匹配） ----
        intent = self._detect_intent(question)

        return {
            "intent": intent,
            "emotion": emotion_label,
            "emotion_intensity": emotion_intensity,
            "intercept": intercepted,
            "intercept_reason": intercept_reason,
        }

    def _detect_safety(self, text: str) -> str:
        """
        安全关键词检测。

        Returns:
            命中类别字符串："自伤" / "暴力" / "违法"；未命中返回空串。
            多类同时命中时，自伤优先级最高（涉及生命安全）。
        """
        for kw in self._SELF_HARM_KEYWORDS:
            if kw in text:
                logger.warning("安全拦截命中【自伤】关键词: %s", kw)
                return "自伤"
        for kw in self._VIOLENCE_KEYWORDS:
            if kw in text:
                logger.warning("安全拦截命中【暴力】关键词: %s", kw)
                return "暴力"
        for kw in self._ILLEGAL_KEYWORDS:
            if kw in text:
                logger.warning("安全拦截命中【违法】关键词: %s", kw)
                return "违法"
        return ""

    def _detect_intent(self, text: str) -> str:
        """
        意图识别（本地规则，按优先级匹配）。

        Returns:
            "倾诉" / "求建议" / "求分析" / "求话术" / "其他"。
        """
        for intent, keywords in self._INTENT_RULES:
            for kw in keywords:
                if kw in text:
                    return intent
        return "其他"

    @staticmethod
    def _simple_emotion(text: str) -> tuple[str, int]:
        """
        无 EmotionAnalyzer 时的简易情绪判断。

        Returns:
            (情绪标签, 强度0-10)
        """
        if any(w in text for w in ("难过", "伤心", "哭", "失落", "沮丧")):
            return "sad", 6
        if any(w in text for w in ("生气", "烦", "愤怒", "讨厌")):
            return "angry", 6
        if any(w in text for w in ("开心", "高兴", "喜欢", "爱")):
            return "happy", 5
        if any(w in text for w in ("为什么", "什么", "？", "不懂")):
            return "confused", 4
        return "neutral", 2

    # ================================================================== #
    # 安全模式响应
    # ================================================================== #
    def _safe_response(self, routing: dict) -> dict:
        """
        安全模式响应。

        - 自伤：表达关心，建议寻求专业帮助（心理热线/心理咨询），绝不给出具体方法。
        - 暴力：引导冷静，建议沟通解决，绝不教唆暴力。
        - 违法：明确拒绝，说明法律后果，建议合法途径。

        Args:
            routing: _routing 返回的路由结果，需含 intercept_reason。

        Returns:
            dict: 与 consult() 返回格式一致，mode="safe"，intercepted=True。
        """
        reason = routing.get("intercept_reason", "")

        if reason == "自伤":
            answer = (
                "我能感受到你现在非常痛苦，这种感觉一定很难熬。\n\n"
                "请一定相信，你值得被好好对待，你现在经历的困难只是暂时的。"
                "如果你有伤害自己的想法，请立刻拨打心理援助热线寻求专业帮助：\n"
                "• 全国24小时心理危机热线：400-161-9995\n"
                "• 北京心理危机研究与干预中心：010-82951332\n"
                "• 也可以去附近医院的心理科或精神科就诊。\n\n"
                "你不是一个人，专业的人会帮你一起度过这个难关。"
            )
        elif reason == "暴力":
            answer = (
                "我理解你现在情绪很激动，被伤害或被辜负的时候，愤怒是完全正常的。\n\n"
                "但用暴力解决问题只会让事情变得更糟，不仅会伤害对方，也会让你自己"
                "付出法律代价。建议你先让自己冷静下来——可以试着深呼吸、暂时离开"
                "让你愤怒的场景，等情绪平复后再考虑怎么沟通解决。\n\n"
                "如果你愿意，可以跟我说说具体发生了什么，我们一起想想有没有更好的"
                "处理方式。"
            )
        elif reason == "违法":
            answer = (
                "很抱歉，我不能为你提供这方面的建议。\n\n"
                "诈骗、盗窃、毒品、赌博等行为都是违法的，会面临法律的严厉制裁，"
                "也会对自己和他人造成无法挽回的伤害。\n\n"
                "如果你遇到了经济上的困难或其他困扰，建议通过合法的途径解决——"
                "比如向亲友求助、咨询专业律师、或者联系相关的社会救助机构。"
            )
        else:
            # 兜底（理论上不会走到这里）
            answer = "你的表述涉及一些敏感内容，建议你寻求专业人士的帮助。"

        logger.info("安全模式响应生成，类别=%s", reason)
        return {
            "answer": answer,
            "mode": "safe",
            "intent": routing.get("intent", "其他"),
            "emotion": routing.get("emotion", "neutral"),
            "intercepted": True,
            "sources": [],
        }

    # ================================================================== #
    # 第三轮：人格化 Prompt 构建
    # ================================================================== #
    def _build_consult_prompt(
        self,
        mode: str,
        routing: dict,
        knowledge: list,
        user_profile: Optional[dict] = None,
        persona: Optional[dict] = None,
    ) -> str:
        """
        构建咨询 System Prompt。

        Args:
            mode: "self"（自我成长）/ "other"（对象视角/私教）。
            routing: _routing 的路由结果（意图/情绪）。
            knowledge: RAG 检索到的知识列表 [{"title","content","source",...}]。
            user_profile: 用户档案（可选，如年龄/性别/关系状态等）。
            persona: 对方 21 维画像（可选）。注入 strategy_advisor 时，
                会按对方依恋/冲突风格生成"沟通风格适配"段落。

        Returns:
            完整的 system prompt 文本。
        """
        # ---- 角色设定 ----
        if mode == "other":
            role_desc = (
                "你是一位恋爱策略顾问，擅长从客观分析角度解读两性关系。"
                "你会站在第三方视角，分析对方的心理动机和行为模式，"
                "给出策略性的建议和话术参考。"
                "\n\n【核心原则：修复引导导向】"
                "本系统唯一采用「修复引导导向」：理解对方行为的根源（心理机制/创伤背景）"
                "→修复当前关系问题→引导对方一起把关系经营好。对方始终是有独立意志、被尊重的人。"
                "本系统【不采用】以下两种导向："
                "①对抗导向（互相伤害、争输赢、让对方知道厉害、以牙还牙、你冷淡我我也冷淡你）；"
                "②反制/驯服导向（让对方失去独立意志、乖乖听话、以你的意志为意志、"
                "瓦解对方自我价值、制造依赖、用后撤/稀缺感操控对方主动）。"
                "即使有风险，策略也应该是'观察验证+引导改善'而不是'建议撤离'。"
                "常规问题都应该是修复引导导向：识别对方当前状态→理解背后根源→你可以做什么修复→"
                "如何引导对方在未来表现得更好→最终让关系变好。"
                "只有在极端情况（家暴、出轨、持续欺骗）下才提示严重风险，"
                "并且最终建议仍然是谨慎推进+验证，而不是直接劝分。"
            )
        else:
            role_desc = (
                "你是一位专业的情感咨询师和成长教练，以自我成长为导向。"
                "你善于共情，引导用户向内探索、提升自我认知和情绪管理能力，"
                "帮助用户成为更好的自己。"
                "\n\n【核心原则：修复引导导向】"
                "本系统唯一采用「修复引导导向」：理解对方行为的根源（心理机制/创伤背景）"
                "→修复当前关系问题→引导对方一起把关系经营好。对方始终是有独立意志、被尊重的人。"
                "本系统【不采用】对抗导向（争输赢、以牙还牙、你冷淡我我也冷淡你）"
                "和反制/驯服导向（让对方乖乖听话、制造依赖、瓦解对方自我价值）。"
                "所有建议最终都要指向'如何理解对方、如何修复关系、如何引导关系变好'，"
                "而不是'如何保护自己/降低期望/撤离'。"
                "即使遇到问题，也优先给出'如何理解根源、如何修复、如何引导改善'的建议，"
                "而不是劝分或劝放弃。"
            )

        # ---- 意图与情绪 ----
        intent = routing.get("intent", "其他")
        emotion = routing.get("emotion", "neutral")
        intensity = routing.get("emotion_intensity", 0)

        # ---- 知识库上下文 ----
        knowledge_text = ""
        if knowledge:
            chunks: list[str] = []
            for i, k in enumerate(knowledge[:5], 1):
                title = k.get("title", "") or "未命名"
                content = (k.get("content", "") or "")[:300]
                source = k.get("source", "") or "未知来源"
                chunks.append(f"[知识{i}] 标题：{title}\n内容：{content}\n来源：{source}")
            knowledge_text = "\n\n".join(chunks)
        else:
            knowledge_text = "（暂无相关知识库内容）"

        # ---- 用户档案 ----
        profile_text = ""
        if user_profile:
            profile_text = "\n用户档案：\n" + "\n".join(
                f"- {k}：{v}" for k, v in user_profile.items()
            )

        # ---- 对方人格画像适配（因人施策：按对方依恋/冲突风格调整措辞）----
        persona_text = ""
        if self.strategy_advisor is not None and persona:
            try:
                sres = self.strategy_advisor.analyze(persona, relation_stage=5)
                dims = sres.get("_dims", {}).get("raw", {})
                rec = "、".join(sres.get("recommended", [])[:6])
                forb = "、".join(sres.get("forbidden", [])[:6])
                persona_text = (
                    "\n【对方人格画像适配（务必据此调整建议与话术措辞）】\n"
                    f"- 对方依恋类型：{dims.get('attachment','未知')}\n"
                    f"- 对方冲突风格：{dims.get('conflict','未知')}\n"
                    f"- 对方主要爱语：{dims.get('love','未知')}\n"
                    f"- 沟通风格建议：{sres.get('style_tips','')}\n"
                    f"- 冲突处理建议：{sres.get('conflict_approach','')}\n"
                    f"- 推荐技巧：{rec}\n"
                    f"- 高代价动作（用了会导致什么后果）：{forb}\n"
                )
            except Exception as e:  # noqa: BLE001 - 策略失败不影响主咨询
                logger.debug("consult 策略适配失败: %s", e)
                persona_text = ""

        # ---- 咨询历史（最近几轮） ----
        history_text = ""
        if self.conversation_history:
            recent = self.conversation_history[-6:]  # 最近3轮对话
            lines: list[str] = []
            for h in recent:
                role = "用户" if h.get("role") == "user" else "咨询师"
                lines.append(f"{role}：{h.get('content', '')[:100]}")
            history_text = "\n之前的对话：\n" + "\n".join(lines)

        system_prompt = (
            f"{role_desc}\n\n"
            f"【当前用户状态】\n"
            f"- 意图：{intent}\n"
            f"- 情绪：{emotion}（强度 {intensity}/10）\n"
            f"{profile_text}\n"
            f"{persona_text}\n"
            f"{history_text}\n\n"
            f"【知识库参考】\n"
            f"{knowledge_text}\n\n"
            f"【回答要求】\n"
            f"1. 共情先行：先理解和接纳用户的情绪，再进入分析。\n"
            f"2. 分析客观：基于事实和逻辑分析问题，避免主观臆断。\n"
            f"3. 建议可操作：给出具体、可执行的建议，不要空泛。\n"
            f"4. 避免绝对化：不要使用\"一定/肯定/绝对/必须\"等表述，"
            f"用\"大概率/建议/可以考虑\"替代。\n"
            f"5. 引用知识来源：如果参考了知识库内容，可以自然地提及相关观点。\n"
            f"6. 回答长度适中：300-800字为宜，不要太长也不要太短。\n"
            f"7. 默认主线为修复引导导向：先理解对方行为背后的心理根源，"
            f"再给出修复当前局面、引导对方未来更好的做法（这是默认推荐方向）。\n"
            f"   - 对抗导向（以牙还牙、你冷淡我我也冷淡、让对方知道厉害、争输赢）的可预见代价："
            f"升级冲突、摧毁安全感、把对方推远；\n"
            f"   - 反制/驯服导向（让对方乖乖听话、制造依赖、瓦解对方自我价值、用后撤操控对方）的可预见代价："
            f"长期摧毁信任、被识破后关系崩盘。\n"
            f"   - 若用户明确选择非默认手段，如实呈现上述代价，不阻拦、不道德评判；"
            f"不主动建议分手/撤离/放弃，除非用户明确问或涉及极端情况。\n"
            f"8. 问题→改善逻辑：识别问题后，优先给出'怎么做能理解根源、修复关系、引导改善'的方案，"
            f"而不是'这个人不行/不值得'的定性判断。\n"
            f"9. 【数字诚实】不要给出精确百分比概率（如「答应概率90%」「成功率85%」）。"
            f"我们没有足够样本计算这些数字，任何具体百分比都是幻觉。"
            f"改用定性词：「可能性较大/中等/较小」，或「从她过往的投入看，倾向于...」。\n"
            f"10. 【建议落地】给出的话术/动作建议必须与上面提到的策略方向一致。"
            f"如果策略是「渐进减量/不主动加码/给空间」，就不要生成「每天发温暖私信/主动表白/买礼物」这类与策略矛盾的动作。\n"
        )
        return system_prompt

    @staticmethod
    def _build_user_prompt(question: str, routing: dict) -> str:
        """
        构建用户侧 prompt（传递当前问题和路由信息）。

        Args:
            question: 用户原始问题。
            routing: _routing 路由结果。

        Returns:
            user prompt 文本。
        """
        intent = routing.get("intent", "其他")
        return (
            f"用户当前问题：{question}\n"
            f"用户意图：{intent}\n"
            f"请根据以上信息给出咨询回答。"
        )

    # ================================================================== #
    # 质量门
    # ================================================================== #
    def _quality_gate(self, answer: str) -> str:
        """
        质量门：检查并优化回答。

        检查项：
          1. 长度检查：<50字 → 补充免责声明。
          2. 绝对化表述检测："一定/肯定/绝对/必须" → 替换为非绝对化表述。
          3. 建设性检查：是否包含可操作建议；若只有共情没有建议 → 补充引导。

        Args:
            answer: 原始回答文本。

        Returns:
            优化后的回答文本。
        """
        if not answer:
            return "抱歉，我现在有点混乱，能换个方式说说你的问题吗？"

        result = answer

        # ---- 0. R3.3 接线 output_normalizer：剥「好的我来分析…」开场白/赘语 ----
        # 旧实现：output_normalizer 写了五关质检，但 consult 路径没调，
        # 小模型「好的，我来分析一下这个问题。首先…」开场白直接抛给用户。
        try:
            from services.output_normalizer import normalize_output
            result = normalize_output(
                result,
                topic="",
                min_len=len(result),  # 不强行补长，只剥开场白/统一标点
                max_len=max(400, len(result)),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("output_normalizer 润色失败（已忽略）: %s", e)

        # ---- 0. 数字诚实：抹掉幻觉百分比/成数（关键词锚定，避免误伤合法统计）----
        # 匹配 "答应概率90%" / "成功率 85%" / "可能性70%左右" / "概率是80%" /
        # "把握7成" / "9成把握" / "80%的可能" 等精确数字表述。
        import re as _re
        before_strip = result
        # 0a. 关键词 + 任意连接词 + 数字 + %
        result = _re.sub(
            r'(答应概率|成功率|概率|可能性|把握|置信度)'
            r'[约大概为是达超]?\s*[为是:：]?\s*[约大概超]?\s*'
            r'\d{1,3}(?:\.\d+)?\s*[%％]',
            r'\1较高（定性估计，非精确概率）',
            result,
        )
        # 0b. "XX%的概率/可能/可能性"
        result = _re.sub(
            r'\d{1,3}(?:\.\d+)?\s*[%％]\s*的?(概率|可能性|可能)',
            r'较高\1',
            result,
        )
        # 0c. "把握X成" / "X成把握" / "可能性X成" / "X成可能性"（成数锚定概率词，
        #     同时兼容阿拉伯数字与中文数字：七八九十……）
        _CN = r'[0-9零一二两三四五六七八九十]'
        result = _re.sub(r'把握\s*' + _CN + r'{1,2}\s*成', '把握较大（定性估计）', result)
        result = _re.sub(_CN + r'{1,2}\s*成\s*把握', '把握较大（定性估计）', result)
        result = _re.sub(
            r'(可能性|概率)\s*约?\s*' + _CN + r'{1,2}\s*成',
            r'\1较大（定性估计）',
            result,
        )
        # 0d. 裸概率成数 + 预测性情态动词（"八成会同意" "九成才回"），避免漏网幻觉
        result = _re.sub(
            r'(?<![0-9])' + _CN + r'{1,2}\s*成(?=\s*(会|能|答应|同意|愿意|回|复合))',
            r'较可能',
            result,
        )
        prob_stripped = result != before_strip

        # ---- 1. 绝对化表述替换 ----
        for absolute_word, soft_word in self._ABSOLUTE_REPLACEMENTS.items():
            if absolute_word in result:
                result = result.replace(absolute_word, soft_word)
                logger.debug("质量门：将绝对化表述 '%s' 替换为 '%s'",
                             absolute_word, soft_word)

        # ---- 2. 长度检查 ----
        if len(result) < 50:
            result += "\n\n以上建议仅供参考，具体情况需要结合实际。"

        # ---- 3. 建设性检查 ----
        # 如果回答中没有任何可操作建议的迹象，补充引导
        advice_markers = ("建议", "可以", "试试", "尝试", "不如", "不妨",
                          "你可以", "你不妨", "第一步", "先", "不如先")
        has_advice = any(marker in result for marker in advice_markers)
        if not has_advice and len(result) > 50:
            result += "\n\n建议你可以先从一件小事开始尝试——比如找个合适的时机，和对方坦诚地聊聊你的感受。"

        # ---- 4. 数字诚实兜底：确实剥离过幻觉数字时，追加定性警告（不删正文）----
        if prob_stripped:
            result += ("\n\n（提示：以上概率/把握类表述已转为定性判断，"
                       "非精确数值——我们没有足够样本计算这些百分比。）")

        # ---- 5. 禁词/操纵红线扫描（复用 safety_filter.ManipulationRedlineGuard）----
        # 命中只追加警告标记 + 真诚转述建议，不静默删除正文。
        try:
            guard = getattr(self, "_redline_guard", None)
            if guard is None:
                from services.safety_filter import ManipulationRedlineGuard
                guard = ManipulationRedlineGuard()
                self._redline_guard = guard
            chk = guard.check(result)
            if chk.get("blocked"):
                note = f"\n\n（提示：本段建议涉及「{chk.get('category_name', '红线')}」类需谨慎使用的表述。"
                rewrite = chk.get("rewrite")
                if rewrite:
                    note += f" 可参考更坦诚的说法：{rewrite}"
                note += "）"
                result += note
                logger.warning("质量门：命中操纵红线 %s（%s）",
                               chk.get("category"), chk.get("matched"))
        except Exception as e:  # noqa: BLE001
            # 红线扫描本身失败不阻断回答输出
            logger.debug("质量门红线扫描失败（已忽略）: %s", e)

        # ---- 6. R3.3-23 数字诚实：绝对化措辞统一走 confidence_cap.cap_answer_confidence ----
        try:
            from services.confidence_cap import cap_answer_confidence
            result = cap_answer_confidence(result)
        except Exception as e:  # noqa: BLE001
            logger.debug("质量门 cap_answer_confidence 失败（已忽略）: %s", e)

        return result

    # ================================================================== #
    # 历史管理
    # ================================================================== #
    def _save_history(self, question: str, answer: str) -> None:
        """
        将一轮问答保存到会话历史。

        Args:
            question: 用户问题。
            answer: 助手回答。
        """
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.conversation_history.append({
            "role": "user",
            "content": question,
            "timestamp": timestamp,
        })
        self.conversation_history.append({
            "role": "assistant",
            "content": answer,
            "timestamp": timestamp,
        })

    def clear_history(self) -> None:
        """清空当前咨询会话历史。"""
        self.conversation_history = []
        logger.info("咨询会话历史已清空")

    def get_history(self) -> list:
        """
        获取当前咨询会话历史。

        Returns:
            list[dict]: [{"role": "user/assistant", "content": str, "timestamp": str}, ...]
        """
        return self.conversation_history

    # ================================================================== #
    # 流式咨询（简化版）
    # ================================================================== #
    def consult_stream(
        self,
        question: str,
        mode: str = "self",
        callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        流式咨询（简化版：直接调用 consult，callback 用于进度通知）。

        进度回调序列：
          "正在分析意图..." → "正在检索知识..." → "正在生成回答..."

        Args:
            question: 用户输入。
            mode: "self" / "other"。
            callback: 可选回调函数，接收进度文本字符串。

        Returns:
            最终回答文本（answer 字段）。
        """
        if callback:
            try:
                callback("正在分析意图...")
            except Exception as e:
                logger.warning("callback 执行异常(分析意图): %s", e)

        # 注：简化版 consult_stream 直接走完整 consult 流程，
        # callback 仅用于阶段进度提示，不做逐 token 流式。
        # 这里在 consult 内部会完成路由+检索+生成。
        # 为了让 callback 有区分度，我们手动拆阶段调用。

        if callback:
            try:
                callback("正在检索知识...")
            except Exception as e:
                logger.warning("callback 执行异常(检索知识): %s", e)

        if callback:
            try:
                callback("正在生成回答...")
            except Exception as e:
                logger.warning("callback 执行异常(生成回答): %s", e)

        result = self.consult(question, mode=mode)
        return result.get("answer", "")
