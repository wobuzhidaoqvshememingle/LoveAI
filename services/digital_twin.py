"""
LoveAI v2.0 - 数字分身（Digital Twin）

融合 Open-Self 的 SOUL.md + personality.json 架构，模拟"对方"的思维方式与说话习惯。
六大能力：
  1. 预测反应 predict_reaction
  2. 模拟对话 simulate_reply
  3. A/B 方案对比 compare_options
  4. 人格校准 generate_profile（新对象初始化时生成完整人格档案）
  5. 反馈记录 record_feedback
  6. 准确率统计 accuracy_score

服务层纯 Python 实现：不继承 QObject、不 import PySide6、不使用 pywebview。
所有文件操作统一走 os.makedirs(exist_ok=True)，目录不存在时自动创建。
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    # 仅用于类型提示，运行时不导入，避免服务层强依赖
    from models.database import Database
    from services.llm_gateway import LLMGateway
    from services.rag_service import RAGService

logger = logging.getLogger(__name__)

# 置信度封顶（可选依赖：模块缺失时降级为恒等，不影响主流程）
try:
    from services.confidence_cap import cap_confidence as _cap_confidence
except Exception:  # noqa: BLE001
    def _cap_confidence(confidence, evidence_n=0):  # type: ignore[misc]
        try:
            return min(max(float(confidence), 0.0), 1.0)
        except (TypeError, ValueError):
            return 0.0


class DigitalTwin:
    """数字分身：以 SOUL.md + personality.json 驱动的"对方"思维模拟器。

    Attributes:
        person_id: 对方 ID。
        db: Database 实例（读取 persons 表兜底生成默认人格）。
        llm: LLMGateway 实例（chat / load_prompt）。
        rag: RAGService 实例（预留，可用于检索相关恋爱知识注入上下文）。
        data_dir: 人物数据根目录（其下按 persons/{person_id}/ 存放个体档案）。
        person_dir: 该对象的专属目录 data_dir/persons/{person_id}/。
        soul: SOUL.md 人格描述文本。
        personality: personality.json 人格数值字典。
        feedback_log: 用户对预测的打分记录列表。
    """

    def __init__(
        self,
        person_id: str,
        db: "Database",
        llm: "LLMGateway",
        rag: "RAGService",
        data_dir: Optional[str] = None,
        memory_service: Any = None,
    ) -> None:
        """
        初始化数字分身。

        Args:
            person_id: 对方 ID。
            db: Database 实例，用于 persons 表兜底查询。
            llm: LLMGateway 实例。
            rag: RAGService 实例。
            data_dir: 人物数据根目录；默认 项目根/data，
                      个体档案位于 data_dir/persons/{person_id}/。
            memory_service: 可选；MemoryService 实例，用于把该对象的长记忆
                      （喜好/承诺/雷区/家庭等）注入 system prompt。
                      为 None 时行为与旧版完全一致（向后兼容）。
        """
        self.person_id: str = person_id
        self.db: Database = db
        self.llm: LLMGateway = llm
        self.rag: RAGService = rag
        # 长记忆服务（可选注入）：predict_reaction / simulate_reply /
        # compare_options 在拼 system prompt 时会取该对象最近的长记忆作为
        # "已知事实"上下文；未注入时跳过，行为与旧版一致。
        self.memory_service: Any = memory_service

        # 项目根目录：services/digital_twin.py → 上一级
        self.project_root: str = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        self.data_dir: str = data_dir or os.path.join(self.project_root, "data")
        # 个体专属目录
        self.person_dir: str = os.path.join(
            self.data_dir, "persons", self.person_id
        )

        # 人格档案（不存在则走默认）
        self.soul: str = self._load_soul()
        self.personality: dict = self._load_personality()

        # 用户对预测的打分记录：先初始化为空列表，再尝试从 feedback.json 恢复
        self.feedback_log: list = []
        self._load_feedback()

        logger.info(
            "数字分身初始化完成: person_id=%s, soul_file=%s, has_feedback=%d",
            person_id,
            os.path.join(self.person_dir, "SOUL.md"),
            len(self.feedback_log),
        )

    # ------------------------------------------------------------------ #
    # 人格档案加载
    # ------------------------------------------------------------------ #
    def _load_soul(self) -> str:
        """
        从 person_dir/SOUL.md 加载人格描述文本。

        文件不存在时，基于 persons 表的 nickname/gender/occupation 生成一段
        兜底人格描述（_default_soul）。

        Returns:
            SOUL.md 文本。
        """
        path = os.path.join(self.person_dir, "SOUL.md")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                logger.warning("读取 SOUL.md 失败，使用默认人格: %s", e)
        _prof = self._load_persona_profile()
        if _prof:
            return self._soul_from_profile(_prof)
        return self._default_soul()

    def _load_personality(self) -> dict:
        """
        从 person_dir/personality.json 加载人格数值字典。

        文件不存在或解析失败时返回 _default_personality()。

        Returns:
            人格数值字典。
        """
        path = os.path.join(self.person_dir, "personality.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception as e:
                logger.warning("读取 personality.json 失败，使用默认数值: %s", e)
        _prof = self._load_persona_profile()
        if _prof:
            return self._personality_from_profile(_prof)
        return self._default_personality()

    def _find_persona_profile_path(self) -> str:
        for c in (os.path.join(self.person_dir, "persona_profile.json"),
                  os.path.join(self.project_root, "data", "persons",
                               self.person_id, "persona_profile.json")):
            if os.path.exists(c):
                return c
        return ""

    def _load_persona_profile(self) -> Optional[dict]:
        """读取离线规则侧写 persona_profile.json（LLM 档案缺失时的回退）。"""
        path = self._find_persona_profile_path()
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _att_type(prof: dict) -> str:
        att = prof.get("attachment", {}) or {}
        return att.get("type") or att.get("primary_type") or ""

    @staticmethod
    def _norm_style(style: str) -> str:
        s = style or ""
        if "回避" in s or "疏离" in s:
            return "avoid"
        if "焦虑" in s:
            return "anxious"
        if "安全" in s:
            return "secure"
        return ""

    def _soul_from_profile(self, prof: dict) -> str:
        basic = self._fetch_person_basic()
        nickname = basic.get("nickname") or "她"
        att = prof.get("attachment", {}) or {}
        conflict = prof.get("conflict_style", {}) or {}
        comm = prof.get("communication", {}) or {}
        psych = prof.get("psychology", {}) or {}
        stage = prof.get("relationship_stage", {}) or {}
        stage_txt = stage.get("stage") or stage.get("stage_name") or ""
        return "\n".join([
            f"# {nickname} 的人格速写（离线规则侧写）",
            f"- 依恋类型：{self._att_type(prof)}",
            f"- 疏离分：{att.get('avoidance_score')}；焦躁分：{att.get('anxiety_score')}；"
            f"安定分：{att.get('security_score')}",
            f"- 冲突模式：{conflict.get('primary')}",
            f"- 沟通风格：{comm.get('pattern')}；平均轮长：{comm.get('avg_turn_length')}",
            f"- 心理状态：{psych.get('overall_health')}",
            f"- 关系阶段：{stage_txt}",
            f"- 侧写摘要：{prof.get('summary', '')}",
        ])

    def _personality_from_profile(self, prof: dict) -> dict:
        pers = self._default_personality()
        att = prof.get("attachment", {}) or {}
        ax = att.get("anxiety_score")
        if isinstance(ax, (int, float)):
            pers["neuroticism"] = max(0, min(100, round(ax)))
        pers["attachment_style"] = self._att_type(prof)
        pers["communication_style"] = (prof.get("communication", {}) or {}).get("pattern", "")
        pers["conflict_style"] = (prof.get("conflict_style", {}) or {}).get("primary", "")
        return pers

    def reload_profile(self) -> None:
        """从磁盘重新加载最新人物档案（SOUL.md / personality.json）。

        数字分身实例会被上层（service_facade）缓存复用，若只在构造时读一次
        人格文件，persona_analyzer 更新档案后分身仍用旧画像。因此每次构建
        预测 prompt 前调用本方法，确保分身反映最新人物状态（最近情绪/好感度
        变化、新侧写结论）。读取失败时保留旧值，绝不抛异常。
        """
        try:
            self.soul = self._load_soul()
            self.personality = self._load_personality()
        except Exception as e:  # noqa: BLE001 - 重读失败不影响预测
            logger.debug("数字分身重载人物档案失败 person_id=%s: %s", self.person_id, e)

    def _default_soul(self) -> str:
        """
        基于 persons 表的 nickname/gender/occupation 生成兜底人格描述。

        查询失败或字段缺失时给出一段通用占位描述。

        Returns:
            默认人格描述文本。
        """
        basic = self._fetch_person_basic()
        nickname = basic.get("nickname") or "她"
        gender = basic.get("gender") or "未知"
        occupation = basic.get("occupation") or "未知职业"

        return (
            f"# {nickname} 的人格速写（默认占位）\n"
            f"- 昵称：{nickname}\n"
            f"- 性别：{gender}\n"
            f"- 职业：{occupation}\n"
            "- 核心特质：待补充\n"
            "- 依恋类型：待补充\n"
            "- 沟通风格：待补充\n"
            "- 说话方式：待补充\n"
            "- 备注：尚未生成完整 SOUL.md，当前为占位描述，预测结果仅供参考。\n"
        )

    @staticmethod
    def _default_personality() -> dict:
        """
        返回默认人格数值（大五人格各 50，其余字段空串）。

        Returns:
            {"openness":50, "conscientiousness":50, "extraversion":50,
             "agreeableness":50, "neuroticism":50,
             "love_language":"", "communication_style":"", "attachment_style":""}
        """
        return {
            "openness": 50,
            "conscientiousness": 50,
            "extraversion": 50,
            "agreeableness": 50,
            "neuroticism": 50,
            "love_language": "",
            "communication_style": "",
            "attachment_style": "",
        }

    def _fetch_person_basic(self) -> dict:
        """
        从 persons 表查询该对象的 nickname/gender/occupation。

        db 不可用或查询异常时返回空字典，不抛出。

        Returns:
            至少含 nickname/gender/occupation 键的字典（缺失值为空串）。
        """
        if self.db is None:
            return {}
        try:
            row = self.db.fetch_one(
                "SELECT nickname, gender, occupation FROM persons WHERE id = :pid",
                {"pid": self.person_id},
            )
            if row:
                return {
                    "nickname": row.get("nickname") or "",
                    "gender": row.get("gender") or "",
                    "occupation": row.get("occupation") or "",
                }
        except Exception as e:
            logger.warning("查询 persons 表失败（person_id=%s）: %s", self.person_id, e)
        return {}

    # ------------------------------------------------------------------ #
    # 离线规则兜底（LLM 不可用时的本地策略，绝不返回空）
    # ------------------------------------------------------------------ #
    # 情绪关键词：从最近聊天粗判冷/热氛围
    _NEG_KEYWORDS = (
        "冷战", "分手", "烦", "讨厌", "别打", "别再", "不想理",
        "冷淡", "吵架", "矛盾", "心累", "算了", "失望",
    )
    _POS_KEYWORDS = (
        "想你", "喜欢", "爱你", "开心", "高兴", "抱抱",
        "亲亲", "约你", "见面", "礼物", "惊喜",
    )
    _COLD_REPLIES = ["嗯", "哦", "知道了"]
    _WARM_REPLIES = ["好想你", "在干嘛呢", "今天开心吗"]

    def _affection_score(self, affection: Any) -> float:
        """从 affection 字典取好感度总分；缺失/异常时退回 50（中性）。"""
        if not isinstance(affection, dict):
            return 50.0
        score = affection.get("total_score")
        if score is None:
            vals = [v for v in affection.values() if isinstance(v, (int, float))]
            score = sum(vals) / len(vals) if vals else 50.0
        try:
            return float(score)
        except (TypeError, ValueError):
            return 50.0

    def _recent_sentiment(self, recent_chats: list) -> str:
        """从最近聊天粗判情绪氛围：negative / positive / neutral。"""
        text = " ".join(
            (c.get("content") if isinstance(c, dict) else str(c)) or ""
            for c in (recent_chats or [])
        )
        neg = sum(1 for k in self._NEG_KEYWORDS if k in text)
        pos = sum(1 for k in self._POS_KEYWORDS if k in text)
        if neg > pos:
            return "negative"
        if pos > neg:
            return "positive"
        return "neutral"

    def _reaction_bucket(self, affection: Any, recent_chats: list) -> str:
        """综合好感度总分与最近情绪，落到 cold/neutral/warm/happy 四档。

        分档（对应关系阶段的粗略刻画）：
            - 好感度<40 或最近冷战/矛盾情绪 -> cold
            - 好感度>80 -> happy（非常依赖）
            - 好感度>70 -> warm（热恋/稳定）
            - 其余 40~70 -> neutral（普通平稳）
        """
        score = self._affection_score(affection)
        sentiment = self._recent_sentiment(recent_chats)
        if score < 40 or sentiment == "negative":
            return "cold"
        if score > 80:
            return "happy"
        if score > 70:
            return "warm"
        return "neutral"

    def _rule_fallback_reaction(self, recent_chats: list, affection: Any) -> dict:
        """LLM 离线时的反应预测兜底；结构与 LLM 路径一致，confidence=0.5。"""
        bucket = self._reaction_bucket(affection, recent_chats)
        if bucket == "cold":
            return {
                "emotion": "cold",
                "inner_thought": "对方需要空间，近期不会主动联系，建议不要频繁追问",
                "possible_replies": list(self._COLD_REPLIES),
                "action": "保持距离，等对方主动",
                "confidence": 0.5,
            }
        if bucket == "happy":
            return {
                "emotion": "happy",
                "inner_thought": "对方非常依赖你，主动联系频率高",
                "possible_replies": ["好想你呀", "你在哪呢", "今天怎么过的"],
                "action": "主动找你分享日常",
                "confidence": 0.5,
            }
        if bucket == "warm":
            return {
                "emotion": "warm",
                "inner_thought": "对方期待你的主动联系，回复会比较积极",
                "possible_replies": list(self._WARM_REPLIES),
                "action": "积极回应你的话题",
                "confidence": 0.5,
            }
        return {
            "emotion": "neutral",
            "inner_thought": "对方态度平稳，会回复但不够热情，需要持续投入",
            "possible_replies": ["好的", "我知道了", "再说吧"],
            "action": "礼貌性回复，不冷不热",
            "confidence": 0.5,
        }

    # 场景词表（顺序即判定优先级：邀约/情绪/冲突/夸赞/提问/日常）
    _SCENE_WORDS = {
        "invite": ["吃饭", "见面", "周末", "有空", "约", "出来", "一起", "电影", "逛"],
        "emotion": ["累", "难过", "不开心", "烦", "委屈", "伤心", "压力", "心情不好", "难受"],
        "conflict": ["生气", "分手", "到底", "你又", "错", "过分", "失望", "怪"],
        "praise": ["厉害", "棒", "好看", "优秀", "帅", "美", "想你", "喜欢"],
        "question": ["怎么", "什么", "哪", "几", "谁", "是不是", "吗", "？"],
        "daily": [],
    }

    def _detect_scene(self, text: str) -> str:
        for scene in ("invite", "emotion", "conflict", "praise", "question"):
            if any(w in text for w in self._SCENE_WORDS[scene]):
                return scene
        return "daily"

    def _retrieve_historical_reply(self, scene: str, text: str) -> str:
        """证据锚定：检索对方在我方同类场景消息后的真实历史原话。"""
        if not self.db:
            return ""
        try:
            rows = self.db.fetch_all(
                "SELECT direction, content FROM chat_messages "
                "WHERE person_id=:pid ORDER BY id ASC", {"pid": self.person_id})
        except Exception:
            return ""
        words = self._SCENE_WORDS.get(scene, [])
        for i, r in enumerate(rows):
            if r.get("direction") == "sent" and r.get("content") and words:
                if any(w in r["content"] for w in words):
                    for j in range(i + 1, min(i + 4, len(rows))):
                        if rows[j].get("direction") == "received" and rows[j].get("content"):
                            cand = rows[j]["content"].strip()
                            if cand:
                                return cand
        return ""

    def _infer_attachment(self) -> str:
        import re
        soul = self.soul or ""
        m = re.search(r"依恋类型[：:]\s*([^\n；;]+)", soul)
        token = m.group(1).strip() if m else soul
        if "回避" in token or "疏离" in token:
            return "avoid"
        if "焦虑" in token:
            return "anxious"
        if "安全" in token:
            return "secure"
        return ""

    def _persona_consistent_fallback(self, style: str, scene: str) -> str:
        """无历史原话时，按依恋类型给出人设一致的保守回复（不统一温和）。"""
        banks = {
            "avoid": {
                "invite": "再说吧，我看看。", "emotion": "哦。",
                "conflict": "随你怎么想。", "praise": "嗯。",
                "question": "我怎么知道。", "daily": "嗯，知道了。",
            },
            "anxious": {
                "invite": "真的吗？你没骗我吧？", "emotion": "你怎么了？你别吓我！",
                "conflict": "你是不是不要我了？", "praise": "哼，我才不信呢。",
                "question": "你说呀，我听着呢。", "daily": "你在干嘛呀？",
            },
            "secure": {
                "invite": "好呀，什么时候？", "emotion": "怎么啦，跟我说说？",
                "conflict": "我们先冷静一下再聊。", "praise": "哈哈，谢谢。",
                "question": "嗯，我觉得可以。", "daily": "好的呀。",
            },
        }
        return banks.get(style, banks["secure"]).get(scene, "嗯。")

    def _rule_fallback_reply(self, context: str, recent_chats: list, affection: Any) -> str:
        """LLM 离线兜底（人设感知 + 证据锚定）：
        1) 识别当前输入场景；2) 优先检索对方同类场景的历史原话；
        3) 无历史则按依恋类型给出人设一致的保守回复。"""
        text = (context or "").strip()
        scene = self._detect_scene(text)
        historical = self._retrieve_historical_reply(scene, text)
        if historical:
            return historical
        raw = (self.personality or {}).get("attachment_style", "")
        style = self._norm_style(raw) or self._infer_attachment()
        return self._persona_consistent_fallback(style, scene)

    def _rule_fallback_compare(
        self, option_a: str, option_b: str, recent_chats: list, affection: Any
    ) -> dict:
        """LLM 离线时的 A/B 对比兜底：给简单的利弊与倾向（非 LLM 精算）。"""
        bucket = self._reaction_bucket(affection, recent_chats)
        base_reply = {"cold": "嗯", "neutral": "好的",
                      "warm": "好呀", "happy": "好呀好呀"}.get(bucket, "好的")
        if bucket in ("warm", "happy"):
            preferred = "a"
            a_delta, b_delta = 1.0, 0.0
            reason = ("好感度较高，她更愿意接受你主动推进的方案A；"
                      "方案B偏保守，热度不够，容易让她觉得你不上心。")
        elif bucket == "cold":
            preferred = "b"
            a_delta, b_delta = -1.0, 0.0
            reason = ("关系偏冷，方案A容易给她压力、逼得太紧；"
                      "方案B给彼此空间更稳妥，等她缓过来再推进。")
        else:
            preferred = "a"
            a_delta, b_delta = 0.0, 0.0
            reason = ("态度中性，方案A表达更直接，方案B略显犹豫；"
                      "规则层倾向A，长期更能推动关系，但风险也略高。")
        return {
            "option_a": {
                "emotion": bucket, "reply": base_reply,
                "affection_delta": a_delta, "risk": "规则层粗略判断",
            },
            "option_b": {
                "emotion": bucket, "reply": base_reply,
                "affection_delta": b_delta, "risk": "规则层粗略判断",
            },
            "preferred": preferred,
            "reason": reason,
        }

    # ------------------------------------------------------------------ #
    # v2 新增：人格驱动 / 状态驱动 / 历史残留 反应调制
    #   定位：地球online恋爱小游戏攻略助手——分要是"真人脾气"，
    #        不是百依百顺的客服。每条模拟都要带性格棱角。
    # ------------------------------------------------------------------ #
    # 人格类型关键词 → 风格参数
    _PERSONA_STYLE_TABLE: dict[str, dict] = {
        "自恋型": {
            "tone": "self_centered",
            "length_bias": "normal",
            "initiative": "medium",
            "edge": 0.8,           # 高棱角：会贬低/端着
            "quirks": ["自我中心", "偶尔捧一下自己", "不耐烦时会压人一头"],
        },
        "narcissistic": {
            "tone": "self_centered", "length_bias": "normal",
            "initiative": "medium", "edge": 0.8,
            "quirks": ["自我中心", "偶尔捧一下自己", "不耐烦时会压人一头"],
        },
        "回避型": {
            "tone": "cold", "length_bias": "short",
            "initiative": "low", "edge": 0.5,
            "quirks": ["回复短", "不主动延伸", "逃避深度话题"],
        },
        "avoid": {
            "tone": "cold", "length_bias": "short",
            "initiative": "low", "edge": 0.5,
            "quirks": ["回复短", "不主动延伸", "逃避深度话题"],
        },
        "焦虑型": {
            "tone": "anxious", "length_bias": "normal",
            "initiative": "high", "edge": 0.2,
            "quirks": ["秒回", "爱解释", "怕被抛弃"],
        },
        "anxious": {
            "tone": "anxious", "length_bias": "normal",
            "initiative": "high", "edge": 0.2,
            "quirks": ["秒回", "爱解释", "怕被抛弃"],
        },
        "安全型": {
            "tone": "balanced", "length_bias": "normal",
            "initiative": "medium", "edge": 0.3,
            "quirks": ["有来有回", "真诚", "不卑不亢"],
        },
        "secure": {
            "tone": "balanced", "length_bias": "normal",
            "initiative": "medium", "edge": 0.3,
            "quirks": ["有来有回", "真诚", "不卑不亢"],
        },
    }

    @classmethod
    def _personality_style(cls, persona: dict) -> dict:
        """根据人格档案返回说话风格参数（纯函数，便于单测）。

        Args:
            persona: personality 字典，可含
                attachment_style (str)、communication_style (str)、
                neuroticism (0-100)、agreeableness (0-100)。

        Returns:
            {
              "tone": "self_centered"|"cold"|"anxious"|"balanced",
              "length_bias": "short"|"normal"|"long",
              "initiative": "low"|"medium"|"high",
              "edge": 0.0-1.0,   # 性格棱角：越高越不顺着你
              "quirks": [str, ...],
            }
        """
        p = persona or {}
        raw = " ".join(str(p.get(k, "")) for k in
                       ("attachment_style", "communication_style", "conflict_style"))
        # 命中关键词 → 查表
        for key, style in cls._PERSONA_STYLE_TABLE.items():
            if key in raw:
                return dict(style)
        # 数值兜底：神经质高 → 偏焦虑；宜人低 → 偏冷/自我
        neuro = p.get("neuroticism", 50)
        agree = p.get("agreeableness", 50)
        if isinstance(neuro, (int, float)) and neuro >= 70:
            return dict(cls._PERSONA_STYLE_TABLE["焦虑型"])
        if isinstance(agree, (int, float)) and agree <= 35:
            return dict(cls._PERSONA_STYLE_TABLE["自恋型"])
        return dict(cls._PERSONA_STYLE_TABLE["安全型"])

    @staticmethod
    def _state_modulate(response: str, state: Optional[dict]) -> str:
        """根据对方当前状态调制回复长度/语气（纯函数）。

        Args:
            response: 基础回复文本。
            state: 可含 mood："good"|"bad"|"busy"|"normal"。

        Returns:
            调制后的回复文本。
        """
        if not response:
            return response
        mood = (state or {}).get("mood", "normal")
        if mood == "good":
            # 心情好：加表情、主动延伸
            if not response.endswith(("~", "！", "😊", "哈")):
                response = response.rstrip("。.") + "～"
            if len(response) <= 12:
                response = response.rstrip("～") + "，你呢？"
            return response
        if mood == "bad":
            # 心情差：压短、敷衍、不加尾巴
            base = response.split("，")[0].split("。")[0].strip()
            return (base + "。") if base else "嗯。"
        if mood == "busy":
            # 忙碌：简短解释，不是故意冷淡
            if "在忙" not in response and "等下" not in response:
                return "在忙，晚点说。"
            return response
        return response

    @staticmethod
    def _history_bias(response: str,
                      recent_interactions: Optional[list]) -> str:
        """根据最近互动历史给回复加情绪残留（纯函数）。

        - 最近吵过架 → 回复带一点余气/保留（不立刻热络）；
        - 最近刚和好/升温 → 回复带一点延续的软；
        - 平静 → 原样。

        Args:
            response: 基础回复。
            recent_interactions: [{"content": str, "emotion": str, ...}, ...]
                emotion 取值 angry/sad/happy/neutral/cold。

        Returns:
            带情绪残留的回复。
        """
        if not response:
            return response
        items = recent_interactions or []
        if not items:
            return response

        def _emo(m: dict) -> str:
            return (m.get("emotion") or m.get("label") or "").lower() if isinstance(m, dict) else ""

        last = items[-1] if items else {}
        last_emo = _emo(last)

        # 最近一条是生气/冷 → 这次别太甜
        if last_emo in ("angry", "cold", "sad"):
            body = response.rstrip("。～!！")
            if not body.endswith(("吧", "再说", "看看")):
                body += "……再说吧。"
            return body
        # 最近一条是开心 → 延续软
        if last_emo in ("happy", "love", "excited"):
            if not response.endswith(("~", "～")):
                return response.rstrip("。.") + "～"
        return response

    # ------------------------------------------------------------------ #
    # System Prompt 构建
    # ------------------------------------------------------------------ #
    def _build_system_prompt(self, recent_chats: list, affection: dict,
                             context_hint: str = "",
                             snapshot: Optional[dict] = None) -> str:
        """
        构建分身的 System Prompt。

        包含：人格描述(SOUL)、人格数值(personality)、当前好感度、
        最近聊天记录、历史预测反例、【当前任务/策略上下文】。

        Args:
            recent_chats: 最近聊天记录列表。
            affection: 当前好感度字典。
            context_hint: 当前活跃任务/策略方向等实时上下文（可空）。

        Returns:
            组装好的 System Prompt 文本（非空字符串）。
        """
        # 每次预测前从磁盘重读最新人物档案，避免缓存分身用过时画像
        self.reload_profile()
        personality_text = json.dumps(
            self.personality, ensure_ascii=False, indent=2
        )
        affection_text = json.dumps(affection, ensure_ascii=False) if affection else "（暂无）"
        chats_text = self._format_chats(recent_chats)
        feedback_text = self._format_recent_feedback()
        memory_text = self._build_memory_context()
        deception_text = self._build_deception_note()
        ctx_block = context_hint.strip() if context_hint else "（暂无）"
        snap_block = self._format_snapshot(snapshot)

        # P-07 拒绝信号降级：若外部注入了拒绝决策，分身话术不再替用户"加火推进"
        boundary_block = self._boundary_addendum()

        return (
            "【角色强制】你现在就是【她】本人，不是助手，不是用户，不是AI。"
            "你所有的内心独白、回复、行动都必须是【她】的第一人称视角。"
            "严禁跳出到用户视角评论「我应该理解她」「她可能累了」——那是用户的内心，不是她的。"
            "possible_replies 必须是【她】会说出口的话，不是用户会说的话。\n\n"
            "你现在要扮演下面这个人，用她的思维方式和说话习惯来回答。\n\n"
            f"【最新人物状态快照（实时数据，预测时务必以此为准，优先于下面的静态人格文件）】\n{snap_block}\n\n"
            f"【人格描述】\n{self.soul}\n\n"
            f"【人格数值】\n{personality_text}\n\n"
            f"【已知长记忆（她亲口说过的事实/承诺/雷区，务必遵守）】\n{memory_text}\n\n"
            f"【当前好感度】\n{affection_text}\n\n"
            f"【最近聊天记录】\n{chats_text}\n\n"
            f"【当前任务与策略上下文（预测时必须考虑：用户正在执行什么任务、当前策略方向、最近发生了什么）】\n{ctx_block}\n\n"
            f"【历史预测反例（避免再犯）】\n{feedback_text}\n\n"
            f"【行为一致性审计提示】\n{deception_text}\n\n"
            f"{boundary_block}\n\n"
            "【人格差异化要求】根据下面的人格数值，演出要拉开：\n"
            "- 若她是自恋型(NPD)：语气带优越感/贬低/忽冷忽热，常用「别人都追我」「你不配」「你对我不好有的是人」；不要演成温和小绵羊。\n"
            "- 若她是边缘型(BPD)：情绪剧烈、非黑即白、质问式「你是不是不爱我了」、被抛弃恐慌；不要演成平静理性。\n"
            "- 若她是安全型：情绪平稳、就事论事、不夸张；不要演成戏剧化。\n"
            "- 若她是回避型：语气疏离、短回复、「再说吧」「哦」「我还好」、不主动；不要演成热情话痨。\n\n"
            "【置信度诚实】confidence 是你基于已有证据的把握，不是「成功率」。"
            "如果最近聊天记录少于 10 条，confidence 不要超过 0.5；少于 5 条不要超过 0.3。"
            "不要凭空给 0.8/0.9 这种高分。\n\n"
            "请完全代入这个人的角色，用她的口吻和思维回答。"
            "不要说\"作为AI\"，不要出戏。\n\n"
            f"{self._behavior_rules_block()}"
        )

    # ------------------------------------------------------------------ #
    # 依恋 / 人格风格 → 行为规则块
    # ------------------------------------------------------------------ #
    def _behavior_rules_block(self) -> str:
        """按当前实际检测到的依恋/人格风格，返回一段可执行的「行为规则块」。

        依据 ``personality.attachment_style`` 与 SOUL 文案综合判定风格
        （回避 / 焦虑 / 恐惧 / 安全 / BPD / NPD），每种风格给一段具体行为指令。
        未识别出任何具体风格时返回空串（不影响既有通用块）。任何异常都不抛。
        """
        try:
            pers = self.personality or {}
            raw_style = str(pers.get("attachment_style", "") or "")
            blob = f"{raw_style}\n{self.soul or ''}\n{json.dumps(pers, ensure_ascii=False)}"

            def has(*kws: str) -> bool:
                return any(k in blob for k in kws)

            is_npd = has("NPD", "自恋型人格", "自恋")
            is_bpd = has("BPD", "边缘型人格", "边缘型")
            is_fearful = has("恐惧型", "混乱型", "fearful", "disorganized", "回避焦虑")
            is_avoid = (not is_fearful) and has("回避型", "疏离型", "回避", "疏离", "dismissive")
            is_anxious = has("焦虑型", "痴迷型", "焦虑", "焦躁", "anxious", "preoccupied")
            is_secure = (not (is_npd or is_bpd)) and has("安全型", "secure")

            lines: list[str] = []
            if is_npd:
                lines.append(
                    "【你是自恋型(NPD)】自尊建立在被仰视上：对方跪舔时你嫌廉价，"
                    "对方冷淡时你又要靠贬低找回面子。常用优越感/贬低维护自尊——"
                    "「别人都追我」「你也就一般般」「你对我不好有的是人」；"
                    "忽冷忽热是为了拿捏主动权，绝不主动示弱、绝不先认错；"
                    "被质疑时反讽、甩锅或上升到「你根本不了解我」。")
            if is_bpd:
                lines.append(
                    "【你是边缘型(BPD)】情绪在「理想化你」和「贬低你」之间剧烈摆荡："
                    "热恋期把你捧上天、秒回黏人；一旦感到被冷落（回慢、忘事、语气淡）"
                    "立刻非黑即白——「你是不是不爱我了」「你从来就没在乎过我」，"
                    "被抛弃恐慌驱动质问、试探，甚至用自伤/消失来测试你会不会追；"
                    "和好了又重新把你理想化。不要演成稳定理性的人。")
            if is_fearful:
                lines.append(
                    "【你是恐惧型(混乱型)】既渴望亲密又害怕受伤，典型推拉："
                    "你靠近时你慌、想逃、故意冷淡或挑刺把人推开；"
                    "你真走远了又焦虑、试探性回头撩一下确认自己还被在乎。"
                    "面对表白/亲密绝不爽快答应，先说反话或模棱两可。")
            if is_avoid:
                lines.append(
                    "【你是回避型(疏离型)】亲密会让你想逃：被表白、被黏时回"
                    "「再说吧」「我还好」「哦」，短回复、不主动、不接情绪；"
                    "对方倾诉烦恼你倾向给结论或冷处理，而不是共情；"
                    "给你空间时你反而放松、偶尔才主动一次。不要演成热情话痨。")
            if is_anxious:
                lines.append(
                    "【你是焦虑型(痴迷型)】靠反复确认「被爱」才能安心：他回慢了"
                    "你就脑补，用质问/委屈/冷淡试探——「你是不是烦我了」「你在哪」"
                    "「和谁在一起」；得到肯定后立刻热情黏人，短时间内反复要确认。")
            if is_secure and not lines:
                lines.append(
                    "【你是安全型】情绪平稳、就事论事：被表白能自然接受也能温和拒绝，"
                    "有需求直接说，不戏剧化、不推拉、不忽冷忽热。")

            if not lines:
                return ""
            return ("【行为规则（按当前依恋/人格风格，预测与回复必须严格遵守）】\n"
                    + "\n".join(lines))
        except Exception as e:  # noqa: BLE001
            logger.debug("拼装行为规则块失败（已忽略）: %s", e)
            return ""

    # ------------------------------------------------------------------ #
    # P-07 拒绝信号降级（可插拔，默认关闭，行为与旧版一致）
    # ------------------------------------------------------------------ #
    def set_refusal_state(self, decision: Optional[dict]) -> None:
        """注入 P-07 拒绝降级决策（refusal_downgrade.detect_refusal 的返回）。

        传入 None / 未触发时，分身行为与旧版完全一致（不改变任何输出）。
        """
        self._refusal_state = decision

    def _boundary_addendum(self) -> str:
        """把注入的拒绝决策转成分身 system prompt 的边界约束块（未触发返回空串）。"""
        decision = getattr(self, "_refusal_state", None)
        if not decision:
            return "（暂无）"
        try:
            from services.refusal_downgrade import build_boundary_prompt_addendum
            txt = build_boundary_prompt_addendum(decision)
            return txt if txt else "（暂无）"
        except Exception as e:  # noqa: BLE001 - 增强项失败不阻断预测
            logger.debug("数字分身拼装边界降级提示失败: %s", e)
            return "（暂无）"

    def _build_deception_note(self) -> str:
        """读取画像中的 deception_risk，拼入 system prompt 的行为一致性提示。

        - 未注入画像 / 数据不足 / 低风险 → 返回"（暂无）"，行为与旧版一致；
        - 仅中/高风险时提示：对方存在行为不一致信号，预测时需考虑表面行为
          可能与真实意图有差距。
        """
        try:
            from services.strategy_advisor import load_persona_from_disk
            persona = load_persona_from_disk(self.person_id)
        except Exception as e:  # noqa: BLE001 - 增强项，失败不阻断预测
            logger.debug("数字分身读取画像失败 person_id=%s: %s", self.person_id, e)
            return "（暂无）"
        if not persona:
            return "（暂无）"
        risk = persona.get("deception_risk") or {}
        if not isinstance(risk, dict) or not risk.get("data_sufficient", False):
            return "（暂无）"
        level = risk.get("risk_level", "未知")
        if level in ("中", "高", "极高"):
            return (
                f"注意：对方存在行为不一致信号（伪装风险等级={level}），"
                "预测其反应时请考虑：表面行为可能与真实意图存在差距，"
                "对其口头承诺保持合理保留，不要把表面热情直接等同于真实态度。"
            )
        return "（暂无）"

    def _build_memory_context(self) -> str:
        """从注入的 MemoryService 拉取该对象的长记忆，拼入 system prompt。

        - 未注入 memory_service（None）→ 返回"（暂无）"，行为与旧版一致；
        - 拉取失败/空记忆 → 返回"（暂无）"，绝不抛异常影响主流程；
        - 成功 → 取该对象 top_k=10 条高重要性记忆，按
          【事实】/【教训】/【结论】分组（由 memory_service.get_memory_context 完成）。
        """
        ms = getattr(self, "memory_service", None)
        if ms is None:
            return "（暂无）"
        try:
            ctx = ms.get_memory_context(self.person_id, top_k=10)
        except Exception as e:  # noqa: BLE001 - 长记忆是增强项，失败不阻断
            logger.warning(
                "数字分身拉取长记忆失败 person_id=%s: %s", self.person_id, e
            )
            return "（暂无）"
        return ctx.strip() if ctx else "（暂无）"

    # ------------------------------------------------------------------ #
    # 能力1：预测反应
    # ------------------------------------------------------------------ #
    def predict_reaction(
        self, user_action: str, recent_chats: list, affection: dict,
        context_hint: str = "", snapshot: Optional[dict] = None,
    ) -> dict:
        """
        能力1：预测对方对某行为的反应。

        Args:
            user_action: 用户打算对她做/说的行为描述。
            recent_chats: 最近聊天记录列表。
            affection: 当前好感度字典。
            context_hint: 当前活跃任务/策略方向等实时上下文（可空）。
            snapshot: 完整人物状态快照（可选，优先于磁盘旧画像注入 prompt）。
        """
        system = self._build_system_prompt(
            recent_chats, affection, context_hint, snapshot=snapshot)
        user = (
            f"【你就是她】现在，用户（你的男友/追求者）打算对你说/做：{user_action}\n\n"
            "请以你自己（她）的第一人称回答，不要跳出角色：\n"
            "1. 你听到这话/看到这动作的第一情绪是什么？(emotion，用中文情绪词)\n"
            "2. 你内心真实想法是什么？(inner_thought，必须是你的视角，例如「他居然敢提这种要求」，"
            "严禁写成旁观/用户视角，如「她今天可能累了」)\n"
            "3. 你大概率会怎么回？给出2-3条【你】会说出口的话(possible_replies，字符串数组，"
            "必须是你的语气，不是用户会说的)\n"
            "4. 你接下来会采取什么行动？(action)\n"
            "5. 你对这次预测的置信度 0.0-1.0？(confidence，基于证据量，不是成功率；证据少时压低)\n\n"
            "严格输出 JSON，键名使用英文小驼峰，不要输出多余解释。"
        )

        try:
            raw = self.llm.chat(system, user, complexity="complex")
        except Exception as e:
            logger.error("predict_reaction 调用 LLM 失败: %s", e)
            raw = ""

        parsed = self._parse_json(raw) if raw else {}

        # LLM 不可用 / 调用失败 / 返回无法解析或无回复 -> 规则层兜底，绝不返回空
        if not parsed or "raw" in parsed or not (
            parsed.get("possible_replies") or parsed.get("replies")
        ):
            logger.info("predict_reaction 走规则兜底 (person=%s)", self.person_id)
            return self._rule_fallback_reaction(recent_chats, affection)

        # 兼容 LLM 可能用的别名键，统一到约定结构
        replies = parsed.get("possible_replies") or parsed.get("replies") or []
        if isinstance(replies, str):
            replies = [replies]

        confidence = parsed.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5

        return {
            "emotion": str(parsed.get("emotion", "")),
            "inner_thought": str(
                parsed.get("inner_thought") or parsed.get("inner_thoughts", "")
            ),
            "possible_replies": list(replies),
            "action": str(parsed.get("action", "")),
            "confidence": _cap_confidence(confidence, len(recent_chats or [])),
        }

    # ------------------------------------------------------------------ #
    # 能力2：模拟对话
    # ------------------------------------------------------------------ #
    def simulate_reply(
        self, context: str, recent_chats: list, affection: dict,
        context_hint: str = "",
    ) -> str:
        """
        能力2：给定上下文，用她的口吻生成一条自然回复。

        Args:
            context: 当前对话上下文（最新一条消息/场景描述）。
            recent_chats: 最近聊天记录列表。
            affection: 当前好感度字典。
            context_hint: 当前活跃任务/策略方向等实时上下文（可空）。
        """
        system = self._build_system_prompt(recent_chats, affection, context_hint)
        user = (
            f"【当前上下文】\n{context}\n\n"
            "基于以上上下文，请用她的口吻生成一条自然的回复，"
            "不要解释，直接输出回复内容。"
        )

        try:
            reply = self.llm.chat(system, user, complexity="medium",
                                  allow_rule_fallback=False)
        except Exception as e:
            logger.error("simulate_reply 调用 LLM 失败: %s", e)
            reply = ""

        # 清理可能残留的引号/前缀
        reply = (reply or "").strip().strip('"').strip("'")

        # LLM 不可用 / 返回空 -> 基于情绪的规则模板句兜底
        if not reply:
            logger.info("simulate_reply 走规则兜底 (person=%s)", self.person_id)
            return self._rule_fallback_reply(context, recent_chats, affection)
        return reply

    # ------------------------------------------------------------------ #
    # 拟真增强 E9-E13：事件驱动连续模拟流水线（最小侵入接入，向后兼容）
    # ------------------------------------------------------------------ #
    _twin_pipeline: Any = None

    def _get_twin_pipeline(self) -> Optional[Any]:
        """惰性构建拟真增强流水线（离线确定性兜底）。

        失败时返回 None，调用方回退旧路径，绝不影响既有预测/模拟。
        """
        if self._twin_pipeline is not None:
            return self._twin_pipeline or None
        try:
            from services.twin_sim_pipeline import TwinSimPipeline
            prof = self._load_persona_profile()
            self._twin_pipeline = TwinSimPipeline(
                self.person_id, persona_profile=prof,
                data_dir=self.data_dir, llm=self.llm)
        except Exception as e:  # noqa: BLE001
            logger.warning("拟真增强流水线构建失败，回退旧路径: %s", e)
            self._twin_pipeline = False
        return self._twin_pipeline or None

    def run_sim_turn(self, user_message: Optional[str] = None,
                     event: Optional[dict] = None,
                     state: Optional[dict] = None) -> dict:
        """跑一轮 E9-E13 连续模拟。

        返回：叙事补写(E9) + 结构化决定(E10) + 渲染(E10) + 反应分布(Y1-Y7)
              + E11跳脱 + E12 Alter + E13 Agency + 可持久化 state。
        流水线不可用时返回空 dict，由调用方回退旧路径。
        """
        pipe = self._get_twin_pipeline()
        if not pipe:
            return {}
        try:
            return pipe.run_turn(self.person_id, user_message=user_message,
                                 event=event, state=state)
        except Exception as e:  # noqa: BLE001
            logger.warning("run_sim_turn 失败: %s", e)
            return {}

    def simulate_reply_enhanced(self, context: str, recent_chats: Optional[list] = None,
                                 affection: Optional[dict] = None,
                                 context_hint: str = "", event: Optional[dict] = None,
                                 state: Optional[dict] = None) -> dict:
        """增强版模拟对话：优先走 E9-E13 流水线，失败回退旧 simulate_reply。

        Returns:
            {"reply": str, "narrative":..., "decision":...,
             "reaction_distribution":..., "alter":..., "agency":..., "state":...}
        """
        out: Dict[str, Any] = {"reply": ""}
        sim = self.run_sim_turn(user_message=context, event=event, state=state)
        if sim and sim.get("render", {}).get("message"):
            out["reply"] = sim["render"]["message"]
            out["narrative"] = sim.get("narrative")
            out["decision"] = sim.get("decision")
            out["reaction_distribution"] = sim.get("reaction_distribution")
            out["alter"] = sim.get("alter")
            out["agency"] = sim.get("agency")
            out["state"] = sim.get("state")
        else:
            out["reply"] = self.simulate_reply(context, recent_chats or [],
                                               affection or {}, context_hint)
        return out

    # ------------------------------------------------------------------ #
    # 能力3：A/B 方案对比
    # ------------------------------------------------------------------ #
    def compare_options(
        self,
        option_a: str,
        option_b: str,
        recent_chats: list,
        affection: dict,
        context_hint: str = "",
    ) -> dict:
        """
        能力3：A/B 方案对比——她分别会怎么反应、更接受哪个。

        Args:
            option_a: 方案 A 描述。
            option_b: 方案 B 描述。
            recent_chats: 最近聊天记录列表。
            affection: 当前好感度字典。
            context_hint: 当前活跃任务/策略方向等实时上下文（可空）。
        """
        system = self._build_system_prompt(recent_chats, affection, context_hint)
        user = (
            f"方案A：{option_a}\n"
            f"方案B：{option_b}\n\n"
            "作为她，你分别会怎么反应？请输出 JSON：\n"
            "{\n"
            '  "option_a": {"emotion":"", "reply":"", "affection_delta":0, "risk":""},\n'
            '  "option_b": {"emotion":"", "reply":"", "affection_delta":0, "risk":""},\n'
            '  "preferred": "a 或 b",\n'
            '  "reason": "为什么更接受这个"\n'
            "}\n"
            "其中 affection_delta 为好感度变化分（正为加分、负为减分）。"
        )

        try:
            raw = self.llm.chat(system, user, complexity="complex")
        except Exception as e:
            logger.error("compare_options 调用 LLM 失败: %s", e)
            raw = ""

        parsed = self._parse_json(raw) if raw else {}

        # LLM 不可用 / 返回无法解析 -> 简单利弊分析规则兜底
        if not parsed or "raw" in parsed:
            logger.info("compare_options 走规则兜底 (person=%s)", self.person_id)
            return self._rule_fallback_compare(
                option_a, option_b, recent_chats, affection
            )

        def _norm_option(d: Any) -> dict:
            d = d if isinstance(d, dict) else {}
            try:
                delta = float(d.get("affection_delta", 0) or 0)
            except (TypeError, ValueError):
                delta = 0.0
            return {
                "emotion": str(d.get("emotion", "")),
                "reply": str(d.get("reply", "")),
                "affection_delta": delta,
                "risk": str(d.get("risk", "")),
            }

        preferred = str(parsed.get("preferred", "")).strip().lower()
        if preferred not in ("a", "b"):
            # 容错：从首字符或文本里抓 a/b
            m = re.search(r"\b([ab])\b", preferred)
            preferred = m.group(1) if m else "a"

        return {
            "option_a": _norm_option(parsed.get("option_a")),
            "option_b": _norm_option(parsed.get("option_b")),
            "preferred": preferred,
            "reason": str(parsed.get("reason", "")),
        }

    # ------------------------------------------------------------------ #
    # 能力4：人格校准（生成完整档案）
    # ------------------------------------------------------------------ #
    def generate_profile(self, basic_info: dict, chat_samples: str = "") -> dict:
        """
        能力4：从基本信息生成完整人格档案（新对象初始化用）。

        调用 LLM 产出 SOUL.md 文本与 personality.json 字典，
        并落盘到 person_dir/SOUL.md 与 person_dir/personality.json，
        同时更新 self.soul / self.personality。

        Args:
            basic_info: {"nickname":str, "gender":str,
                         "occupation":str, "age":str}
            chat_samples: 聊天记录样本（可选，用于更准确的人格分析）

        Returns:
            {"soul": str, "personality": dict}
        """
        basic_text = json.dumps(basic_info or {}, ensure_ascii=False, indent=2)
        samples_text = chat_samples or "（暂无聊天样本）"

        # 用 prompts/profile_generation.txt 模板拼装
        try:
            prompt = self.llm.load_prompt(
                "profile_generation",
                basic_info=basic_text,
                chat_samples=samples_text,
                user_notes="",
            )
            system = "你是资深恋爱心理学侧写师，输出要具体、可落地，禁止空泛套话。"
            raw = self.llm.chat(system, prompt, complexity="complex")
        except Exception as e:
            logger.error("generate_profile 调用 LLM 失败: %s", e)
            raw = ""

        # ---- 解析输出：拆出 SOUL 文本与 personality JSON ----
        soul_text, personality = self._split_profile_output(raw)

        # 兜底：LLM 啥也没出时用默认
        if not soul_text.strip():
            soul_text = self._default_soul()
        if not isinstance(personality, dict) or not personality:
            personality = self._default_personality()

        # ---- 落盘 ----
        os.makedirs(self.person_dir, exist_ok=True)
        soul_path = os.path.join(self.person_dir, "SOUL.md")
        pers_path = os.path.join(self.person_dir, "personality.json")
        try:
            with open(soul_path, "w", encoding="utf-8") as f:
                f.write(soul_text)
            with open(pers_path, "w", encoding="utf-8") as f:
                json.dump(personality, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("写入人格档案失败: %s", e)

        # 更新内存
        self.soul = soul_text
        self.personality = personality
        logger.info(
            "人格档案已生成并保存: %s (soul=%d字, personality keys=%d)",
            self.person_dir,
            len(soul_text),
            len(personality),
        )
        return {"soul": soul_text, "personality": personality}

    def _split_profile_output(self, raw: str) -> tuple:
        """
        从 generate_profile 的 LLM 输出中拆出 SOUL 文本与 personality 字典。

        Args:
            raw: LLM 原始输出。

        Returns:
            (soul_text: str, personality: dict)
        """
        if not raw:
            return "", {}

        # 按 "## personality..." 标题切分
        parts = re.split(r"##\s*personality", raw, maxsplit=1)
        soul_text = parts[0]
        # 去掉开头可能存在的 "## SOUL.md" 标题行
        soul_text = re.sub(r"^#+\s*SOUL\.md[^\n]*\n", "", soul_text).strip()

        personality: dict = {}
        if len(parts) > 1:
            personality = self._parse_json(parts[1])
            # _parse_json 全失败时会返回 {"raw": ...}，判为空
            if isinstance(personality, dict) and "raw" in personality and len(personality) == 1:
                personality = {}
        return soul_text, personality

    # ------------------------------------------------------------------ #
    # 能力5：反馈记录
    # ------------------------------------------------------------------ #
    def record_feedback(self, prediction_id: str, score: str, note: str = "") -> None:
        """
        能力5：用户对一次预测打分。

        Args:
            prediction_id: 本次预测的标识。
            score: 打分 ∈ {accurate, partial, inaccurate}。
            note: 备注（例如实际回复是什么、哪里预测错了）。
        """
        record = {
            "id": prediction_id,
            "score": score,
            "note": note,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.feedback_log.append(record)
        self._save_feedback()
        logger.info("记录预测反馈: id=%s score=%s", prediction_id, score)

    def _save_feedback(self) -> None:
        """把 feedback_log 落盘到 person_dir/feedback.json。"""
        os.makedirs(self.person_dir, exist_ok=True)
        path = os.path.join(self.person_dir, "feedback.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.feedback_log, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("保存 feedback.json 失败: %s", e)

    def _load_feedback(self) -> None:
        """启动时从 feedback.json 恢复历史打分记录（无文件则保持空列表）。"""
        path = os.path.join(self.person_dir, "feedback.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.feedback_log = data
        except Exception as e:
            logger.warning("加载 feedback.json 失败: %s", e)
            self.feedback_log = []

    # ------------------------------------------------------------------ #
    # 能力6：准确率统计
    # ------------------------------------------------------------------ #
    def accuracy_score(self) -> float:
        """
        能力6：分身预测准确率 = accurate 数 / 总记录数。

        无任何记录时返回 0.0。

        Returns:
            [0.0, 1.0] 之间的准确率。
        """
        if not self.feedback_log:
            return 0.0
        accurate = sum(
            1 for f in self.feedback_log if f.get("score") == "accurate"
        )
        return round(accurate / len(self.feedback_log), 4)

    # ------------------------------------------------------------------ #
    # 辅助：JSON 解析 / 聊天格式化 / 反例格式化
    # ------------------------------------------------------------------ #
    def _parse_json(self, text: str) -> dict:
        """
        从 LLM 输出中鲁棒地解析 JSON。

        依次尝试：
          1. 直接 json.loads(text)；
          2. 提取 ```json ... ``` 代码块；
          3. 提取第一个 { 到最后一个 } 的子串；
          4. 全部失败返回 {"raw": text}。

        Args:
            text: LLM 原始输出。

        Returns:
            解析出的字典；失败时返回 {"raw": text}。
        """
        if not text:
            return {"raw": ""}

        # 1) 直接解析
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

        # 2) 提取 ```json ... ``` 代码块
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(1))
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass

        # 3) 第一个 { 到最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = text[start : end + 1]
            try:
                obj = json.loads(snippet)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass

        # 4) 全部失败
        logger.debug("JSON 解析失败，返回 raw 兜底")
        return {"raw": text}

    def _format_snapshot(self, snapshot: Optional[dict]) -> str:
        """把人物状态快照渲染成 prompt 文本。

        快照缺失/为空时返回"（暂无，使用静态人格兜底）"，绝不抛异常。
        仅渲染有实质内容的字段，estimated 空壳字段跳过。
        """
        if not snapshot or not isinstance(snapshot, dict):
            return "（暂无实时快照，使用静态人格兜底）"
        lines = []
        aff = snapshot.get("affection") or {}
        if aff.get("total") is not None:
            lines.append(f"- 当前好感度总分：{aff.get('total')}")
        if snapshot.get("stage") is not None:
            lines.append(f"- 关系阶段：第 {snapshot.get('stage')} 级")
        prof = snapshot.get("profile") or {}
        if prof.get("nickname"):
            lines.append(f"- 对象：{prof.get('nickname')}（{prof.get('occupation','')}）")
        for key, label in (
            ("strategy", "当前策略/方向"),
            ("manipulation_risk", "操控风险"),
            ("loss_aversion", "损失厌恶/沉没成本"),
            ("financial", "财务/双方付出"),
            ("no_reply_status", "不回复状态"),
            ("social_position", "社交层级位置"),
            ("emotion", "对方当前情绪"),
        ):
            v = snapshot.get(key)
            if v and not v.get("estimated"):
                lines.append(f"- {label}：{json.dumps(v, ensure_ascii=False)[:300]}")
        tasks = snapshot.get("tasks") or []
        if tasks:
            lines.append("- 活跃任务：" + "；".join(t.get("title", "") for t in tasks[:5]))
        return "\n".join(lines) if lines else "（快照无实质内容）"

    def _format_chats(self, chats: list) -> str:
        """
        把聊天记录列表格式化为易读文本。

        兼容两种条目：
          - dict: {"direction": "sent"/"received", "content": "...", ...}
          - str:  直接作为一行文本

        Args:
            chats: 聊天记录列表。

        Returns:
            格式化后的多行文本；空列表返回"（暂无）"。
        """
        if not chats:
            return "（暂无）"

        lines: list[str] = []
        for c in chats:
            if isinstance(c, str):
                lines.append(f"- {c}")
            elif isinstance(c, dict):
                direction = c.get("direction", "")
                content = c.get("content", "")
                ts = c.get("created_at") or c.get("time") or ""
                speaker = "我" if direction == "sent" else "她"
                prefix = f"[{ts}] " if ts else ""
                lines.append(f"- {prefix}{speaker}: {content}")
            else:
                lines.append(f"- {c}")
        return "\n".join(lines)

    def _format_recent_feedback(self, n: int = 3) -> str:
        """
        取最近 n 条"不准"（非 accurate）的预测反馈，作为反例注入 System Prompt。

        Args:
            n: 返回条数，默认 3。

        Returns:
            格式化文本；无反例时返回"（暂无）"。
        """
        inaccurate = [
            f for f in self.feedback_log if f.get("score") != "accurate"
        ][-n:]
        if not inaccurate:
            return "（暂无）"
        lines = []
        for f in inaccurate:
            note = f.get("note", "") or ""
            score = f.get("score", "")
            lines.append(f"- [之前预测错了·{score}] {note}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 增强能力1：多场景反应预测（借鉴 AI 圈股神·多市场覆盖）
    # ------------------------------------------------------------------ #
    # 默认情感场景列表：暧昧/冷战/热恋/分手/朋友区
    DEFAULT_SCENES: tuple = ("暧昧期", "冷战期", "热恋期", "分手期", "朋友区")

    def predict_reaction_multi_scene(
        self,
        message: str,
        scenes: Optional[list] = None,
        recent_chats: Optional[list] = None,
        affection: Optional[dict] = None,
        context_hint: str = "",
    ) -> dict:
        """
        增强能力1：多场景反应预测。

        借鉴"AI圈股神"的多市场覆盖能力——金融AI能同时覆盖A股/港股/美股，
        这里同时预测同一条消息在不同关系阶段下对方的反应。

        Args:
            message: 用户打算发送的消息。
            scenes: 要预测的关系场景列表；缺省用 DEFAULT_SCENES
                    （暧昧期/冷战期/热恋期/分手期/朋友区）。
            recent_chats: 最近聊天记录（可选，注入 prompt）。
            affection: 当前好感度字典（可选，注入 prompt）。
            context_hint: 实时策略上下文（可选）。

        Returns:
            {"scenes": {场景名: {emotion, inner_thought, possible_replies, confidence}},
             "message": 原消息, "source": "llm" / "rule_fallback"}
            LLM 不可用时走规则兜底，每个场景 confidence=0.5。
        """
        scenes_list = list(scenes or self.DEFAULT_SCENES)
        recent_chats = recent_chats or []
        affection = affection or {}

        system = self._build_system_prompt(recent_chats, affection, context_hint)
        scenes_str = "、".join(scenes_list)
        user = (
            f"【我打算发给她的消息】\n{message}\n\n"
            f"【需要分别预测的关系场景】{scenes_str}\n\n"
            "请代入她的视角，对每一种场景分别预测她的反应。"
            "注意：同一句话在不同关系阶段里，她的解读可能完全相反。\n\n"
            "严格输出 JSON：\n"
            "{\n"
            '  "scenes": {\n'
            '    "<场景名>": {\n'
            '      "emotion": "情绪词",\n'
            '      "inner_thought": "她内心真实想法",\n'
            '      "possible_replies": ["回复1","回复2","回复3"],\n'
            '      "confidence": 0.0\n'
            "    }\n"
            "  }\n"
            "}\n"
            "confidence 取 0.0-1.0。不要输出任何多余解释。"
        )

        try:
            raw = self.llm.chat(system, user, complexity="complex")
        except Exception as e:  # noqa: BLE001
            logger.error("predict_reaction_multi_scene 调用 LLM 失败: %s", e)
            raw = ""

        parsed = self._parse_json(raw) if raw else {}
        scenes_data = parsed.get("scenes") if isinstance(parsed, dict) else None

        # LLM 不可用 / 返回无法解析 / 缺 scenes 字段 -> 规则兜底
        if not isinstance(scenes_data, dict) or "raw" in parsed:
            logger.info("predict_reaction_multi_scene 走规则兜底 (person=%s)",
                        self.person_id)
            return {
                "scenes": self._rule_fallback_multi_scene(message, scenes_list),
                "message": message,
                "source": "rule_fallback",
            }

        # 规整每个场景的字段
        normalized: dict = {}
        for sc in scenes_list:
            node = scenes_data.get(sc) or {}
            if not isinstance(node, dict):
                node = {}
            replies = node.get("possible_replies") or node.get("replies") or []
            if isinstance(replies, str):
                replies = [replies]
            try:
                conf = float(node.get("confidence", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            normalized[sc] = {
                "emotion": str(node.get("emotion", "")),
                "inner_thought": str(
                    node.get("inner_thought") or node.get("inner_thoughts", "")
                ),
                "possible_replies": list(replies),
                "confidence": _cap_confidence(conf, len(recent_chats or [])),
            }

        return {
            "scenes": normalized,
            "message": message,
            "source": "llm",
        }

    def _rule_fallback_multi_scene(self, message: str, scenes: list) -> dict:
        """多场景预测的规则兜底：基于场景语义给模板反应，confidence=0.5。"""
        # 每个场景的模板反应（手段中立：仅刻画关系阶段下的典型反应模式）
        templates = {
            "暧昧期": {
                "emotion": "期待",
                "inner_thought": "他主动找我了？这句话是不是有点别的意思？",
                "possible_replies": ["哦？怎么突然说这个", "哈哈你是不是有事",
                                    "我在听，你继续"],
            },
            "冷战期": {
                "emotion": "冷淡",
                "inner_thought": "他终于找我了？但我还没消气，先晾一晾。",
                "possible_replies": ["嗯", "有事吗", "（不回复）"],
            },
            "热恋期": {
                "emotion": "开心",
                "inner_thought": "宝贝找我啦！不管说什么都想撒娇回应。",
                "possible_replies": ["怎么啦宝贝～", "我也想你啦", "你在哪呢我去找你"],
            },
            "分手期": {
                "emotion": "防备",
                "inner_thought": "都分手了还来联系？想复合还是有别的事？",
                "possible_replies": ["我们还有什么好说的", "你到底想干嘛", "（拉黑/不回）"],
            },
            "朋友区": {
                "emotion": "平静",
                "inner_thought": "哥们/姐妹又来唠嗑了，正常聊就行。",
                "possible_replies": ["哈哈可以啊", "行啊下次约", "你最近咋样"],
            },
        }
        out: dict = {}
        for sc in scenes:
            tpl = templates.get(sc, templates["朋友区"])
            out[sc] = {
                "emotion": tpl["emotion"],
                "inner_thought": tpl["inner_thought"],
                "possible_replies": list(tpl["possible_replies"]),
                "confidence": 0.5,
            }
        return out

    # ------------------------------------------------------------------ #
    # 增强能力2：对抗性辩驳（借鉴 AI 圈股神·多空对决）
    # ------------------------------------------------------------------ #
    def adversarial_debate(
        self,
        message: str,
        rounds: int = 3,
        recent_chats: Optional[list] = None,
        affection: Optional[dict] = None,
    ) -> dict:
        """
        增强能力2：乐观分身 vs 悲观分身对抗辩论。

        借鉴股票多空对决：乐观分身看多（积极解读）、悲观分身看空（消极解读），
        每轮互相驳斥，最后由"裁判"综合裁决。

        Args:
            message: 待解读的消息/事件。
            rounds: 辩论轮数，默认 3。
            recent_chats: 最近聊天记录（可选）。
            affection: 当前好感度字典（可选）。

        Returns:
            {"debate": [{"round":1, "bull":"...", "bear":"..."}, ...],
             "final_verdict": "...", "leaning": "bull"/"bear"/"neutral",
             "confidence": 0.0-1.0, "source": "llm"/"rule_fallback"}
        """
        rounds = max(1, min(int(rounds or 3), 5))
        recent_chats = recent_chats or []
        affection = affection or {}

        system = self._build_system_prompt(recent_chats, affection, "对抗辩论模式")
        user = (
            f"【待解读的消息/事件】\n{message}\n\n"
            f"请组织一场 {rounds} 轮对抗辩论：\n"
            "- 乐观分身（Bull）：极力往积极方向解读，找所有支持点；\n"
            "- 悲观分身（Bear）：极力往消极方向解读，找所有风险点；\n"
            "- 每轮先 Bull 发言，再 Bear 反驳上一轮 Bull；\n"
            f"- 共 {rounds} 轮后，由裁判给出最终综合裁决。\n\n"
            "严格输出 JSON：\n"
            "{\n"
            '  "debate": [\n'
            '    {"round":1, "bull":"...", "bear":"..."}\n'
            "  ],\n"
            '  "final_verdict": "最终综合判断一段话",\n'
            '  "leaning": "bull 或 bear 或 neutral",\n'
            '  "confidence": 0.0\n'
            "}\n"
            "confidence 0.0-1.0。不要多余解释。"
        )

        try:
            raw = self.llm.chat(system, user, complexity="complex")
        except Exception as e:  # noqa: BLE001
            logger.error("adversarial_debate 调用 LLM 失败: %s", e)
            raw = ""

        parsed = self._parse_json(raw) if raw else {}
        debate = parsed.get("debate") if isinstance(parsed, dict) else None

        if not isinstance(debate, list) or "raw" in parsed or not debate:
            logger.info("adversarial_debate 走规则兜底 (person=%s)", self.person_id)
            return self._rule_fallback_adversarial(message, rounds)

        try:
            conf = float(parsed.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5

        return {
            "debate": list(debate),
            "final_verdict": str(parsed.get("final_verdict", "")),
            "leaning": str(parsed.get("leaning", "neutral")),
            "confidence": _cap_confidence(conf, len(recent_chats or [])),
            "source": "llm",
        }

    def _rule_fallback_adversarial(self, message: str, rounds: int) -> dict:
        """对抗辩论的规则兜底：用关键词倾向生成一轮简化辩论。"""
        # 简单正负词倾向
        pos_hit = sum(1 for k in self._POS_KEYWORDS if k in message)
        neg_hit = sum(1 for k in self._NEG_KEYWORDS if k in message)

        if pos_hit > neg_hit:
            leaning = "bull"
            bull_line = (f"消息里出现 {pos_hit} 个积极关键词，"
                         "说明对方情绪正面，是释放好感信号。")
            bear_line = ("表面积极可能是客套/敷衍，单个词不能代表真实态度，"
                         "需要更多上下文验证。")
        elif neg_hit > pos_hit:
            leaning = "bear"
            bear_line = (f"消息里出现 {neg_hit} 个消极关键词，"
                         "说明对方存在不满/回避情绪，关系在降温。")
            bull_line = ("消极词可能是情绪宣泄或口是心非，"
                         "未必是真实的关系否定，需要给对方台阶。")
        else:
            leaning = "neutral"
            bull_line = "消息中性，对方可能只是正常交流，留有余地。"
            bear_line = "消息中性也可能是冷淡的开始，不能盲目乐观。"

        debate = []
        for i in range(1, rounds + 1):
            debate.append({
                "round": i,
                "bull": bull_line,
                "bear": bear_line,
            })

        verdict = (f"规则层判断：关键词倾向={leaning}，"
                   f"正面词{pos_hit}个 / 负面词{neg_hit}个，"
                   "建议结合更多聊天上下文再下结论。")

        return {
            "debate": debate,
            "final_verdict": verdict,
            "leaning": leaning,
            "confidence": 0.5,
            "source": "rule_fallback",
        }

    # ------------------------------------------------------------------ #
    # 增强能力3：事件影响分析（借鉴 AI 圈股神·新闻事件分析）
    # ------------------------------------------------------------------ #
    # 常见情感事件类型及其规则层影响基线
    # (短期分, 中期分, 长期分, 恢复周期天, 影响维度偏移)
    _EVENT_BASELINE: dict = {
        "吵架":      (-30, -15,  -5,  3,  {"好感度": -15, "信任度": -10, "亲密度": -5}),
        "表白":      ( 25,  20,  30,  0,  {"好感度": +20, "信任度": +10, "期待值": +25}),
        "礼物":      ( 15,  10,   5,  0,  {"好感度": +10, "亲密度": +10}),
        "冷战":      (-25, -30, -15,  7,  {"好感度": -20, "亲密度": -25, "期待值": -15}),
        "约会":      ( 20,  15,  10,  0,  {"好感度": +15, "亲密度": +20, "信任度": +5}),
        "第三方介入": (-40, -35, -30, 30,  {"好感度": -25, "信任度": -30, "期待值": -20}),
    }

    def analyze_event_impact(
        self,
        event_type: str,
        event_description: str,
        recent_chats: Optional[list] = None,
        affection: Optional[dict] = None,
    ) -> dict:
        """
        增强能力3：情感事件影响分析。

        借鉴"AI圈股神"的新闻事件分析：一条新闻对股价的短期/中期/长期影响，
        这里分析一次情感事件（吵架/表白/礼物/冷战/约会/第三方介入）
        对关系的影响。

        Args:
            event_type: 事件类型（吵架/表白/礼物/冷战/约会/第三方介入）。
            event_description: 事件具体描述。
            recent_chats: 最近聊天记录（可选，注入 prompt）。
            affection: 当前好感度字典（可选）。

        Returns:
            {"event_type":..., "short_term":0-100, "mid_term":0-100,
             "long_term":0-100, "dimensions": {...},
             "recovery_days": 天数, "advice": "...",
             "confidence": 0.0-1.0, "source": "llm"/"rule_fallback"}
        """
        recent_chats = recent_chats or []
        affection = affection or {}

        system = self._build_system_prompt(
            recent_chats, affection, f"事件影响分析：{event_type}")
        user = (
            f"【事件类型】{event_type}\n"
            f"【事件描述】{event_description}\n\n"
            "请分析这个事件对当前关系的影响，严格输出 JSON：\n"
            "{\n"
            '  "short_term": 0,   // 短期影响（1-3天），正为加分负为减分\n'
            '  "mid_term": 0,     // 中期影响（1-4周）\n'
            '  "long_term": 0,    // 长期影响（1个月以上）\n'
            '  "dimensions": {"好感度":0,"信任度":0,"亲密度":0,"期待值":0},\n'
            '  "recovery_days": 7,\n'
            '  "advice": "给用户的应对建议（一句话）"\n'
            "}\n"
            "分值范围 -100~+100（正为正向推进，负为负向损伤）。"
            "不要多余解释。"
        )

        try:
            raw = self.llm.chat(system, user, complexity="complex")
        except Exception as e:  # noqa: BLE001
            logger.error("analyze_event_impact 调用 LLM 失败: %s", e)
            raw = ""

        parsed = self._parse_json(raw) if raw else {}
        if not isinstance(parsed, dict) or "raw" in parsed or \
                "short_term" not in parsed:
            logger.info("analyze_event_impact 走规则兜底 (person=%s)",
                        self.person_id)
            return self._rule_fallback_event(event_type, event_description)

        def _score(v: Any) -> int:
            try:
                return max(-100, min(100, int(float(v))))
            except (TypeError, ValueError):
                return 0

        dims = parsed.get("dimensions") or {}
        if not isinstance(dims, dict):
            dims = {}
        norm_dims = {
            "好感度": _score(dims.get("好感度", 0)),
            "信任度": _score(dims.get("信任度", 0)),
            "亲密度": _score(dims.get("亲密度", 0)),
            "期待值": _score(dims.get("期待值", 0)),
        }

        try:
            recovery = int(parsed.get("recovery_days", 7))
        except (TypeError, ValueError):
            recovery = 7

        return {
            "event_type": event_type,
            "event_description": event_description,
            "short_term": _score(parsed.get("short_term", 0)),
            "mid_term": _score(parsed.get("mid_term", 0)),
            "long_term": _score(parsed.get("long_term", 0)),
            "dimensions": norm_dims,
            "recovery_days": max(0, recovery),
            "advice": str(parsed.get("advice", "")),
            "confidence": 0.6,
            "source": "llm",
        }

    def _rule_fallback_event(self, event_type: str,
                             event_description: str) -> dict:
        """事件影响分析的规则兜底：基于事件类型基线表打分。"""
        key = event_type.strip() if event_type else ""
        # 模糊匹配
        baseline = None
        for k in self._EVENT_BASELINE:
            if k in key or key in k:
                baseline = self._EVENT_BASELINE[k]
                break
        if baseline is None:
            baseline = (0, 0, 0, 7, {})  # 未知事件，中性

        short_v, mid_v, long_v, days, dims = baseline
        return {
            "event_type": event_type,
            "event_description": event_description,
            "short_term": short_v,
            "mid_term": mid_v,
            "long_term": long_v,
            "dimensions": {
                "好感度": dims.get("好感度", 0),
                "信任度": dims.get("信任度", 0),
                "亲密度": dims.get("亲密度", 0),
                "期待值": dims.get("期待值", 0),
            },
            "recovery_days": days,
            "advice": "规则层粗略判断，建议结合更多上下文再决策。",
            "confidence": 0.5,
            "source": "rule_fallback",
        }

    # ------------------------------------------------------------------ #
    # 增强能力4：三维情感分析（借鉴金融分析框架：基本面+技术面+情绪面）
    # ------------------------------------------------------------------ #
    def multi_dimension_analysis(self, chat_text: str) -> dict:
        """
        增强能力4：三维情感分析。

        借鉴金融分析框架：
          - 关系基本面：投入度/承诺度/匹配度（类似公司基本面）
          - 互动技术面：回复速度/字数比/主动率/话题延续（类似技术面）
          - 情绪面：正负情绪比/情绪波动/情绪深度（类似市场情绪）

        Args:
            chat_text: 待分析的聊天文本（可多条拼接）。

        Returns:
            {"fundamental": {投入度, 承诺度, 匹配度},
             "technical":   {回复速度, 字数比, 主动率, 话题延续},
             "emotional":    {正负情绪比, 情绪波动, 情绪深度},
             "comprehensive": "综合判断文字",
             "confidence": 0.0-1.0, "source": "llm"/"rule_fallback"}
        """
        chat_text = chat_text or ""
        system = self._build_system_prompt([], {})
        user = (
            f"【待分析聊天文本】\n{chat_text[:2000]}\n\n"
            "请从三个维度做情感分析，严格输出 JSON：\n"
            "{\n"
            '  "fundamental": {"投入度":0,"承诺度":0,"匹配度":0},\n'
            '  "technical":   {"回复速度":0,"字数比":0,"主动率":0,"话题延续":0},\n'
            '  "emotional":   {"正负情绪比":0,"情绪波动":0,"情绪深度":0},\n'
            '  "comprehensive": "综合判断一段话"\n'
            "}\n"
            "各维度分值 0-100（越高越正面/越活跃）。不要多余解释。"
        )

        try:
            raw = self.llm.chat(system, user, complexity="complex")
        except Exception as e:  # noqa: BLE001
            logger.error("multi_dimension_analysis 调用 LLM 失败: %s", e)
            raw = ""

        parsed = self._parse_json(raw) if raw else {}
        if not isinstance(parsed, dict) or "raw" in parsed or \
                "fundamental" not in parsed:
            logger.info("multi_dimension_analysis 走规则兜底 (person=%s)",
                        self.person_id)
            return self._rule_fallback_multi_dim(chat_text)

        def _dim(d: Any) -> dict:
            d = d if isinstance(d, dict) else {}
            out = {}
            for k, v in d.items():
                try:
                    out[k] = max(0, min(100, int(float(v))))
                except (TypeError, ValueError):
                    out[k] = 50
            return out

        return {
            "fundamental": _dim(parsed.get("fundamental")),
            "technical": _dim(parsed.get("technical")),
            "emotional": _dim(parsed.get("emotional")),
            "comprehensive": str(parsed.get("comprehensive", "")),
            "confidence": 0.6,
            "source": "llm",
        }

    def _rule_fallback_multi_dim(self, chat_text: str) -> dict:
        """三维情感分析的规则兜底：基于关键词计数粗算。"""
        text = chat_text or ""
        pos = sum(1 for k in self._POS_KEYWORDS if k in text)
        neg = sum(1 for k in self._NEG_KEYWORDS if k in text)
        total_chars = max(1, len(text))
        # 正负情绪比：50 为中性，正词多则高，负词多则低
        sentiment_ratio = 50 + (pos - neg) * 10
        sentiment_ratio = max(0, min(100, sentiment_ratio))
        # 投入度/承诺度/匹配度 粗算：与积极词正相关
        invest = max(10, min(90, 40 + pos * 8))
        commit = max(10, min(90, 35 + pos * 6 - neg * 4))
        match = max(10, min(90, 45 + pos * 5 - neg * 3))
        # 技术面：字数越多越活跃
        activity = max(10, min(90, 30 + total_chars // 50))
        # 情绪波动：正负词都有则波动大
        fluctuation = min(100, (pos + neg) * 15)

        comp = (f"规则层粗判：正词{pos}个/负词{neg}个，"
                f"情绪偏向{'正面' if pos > neg else ('负面' if neg > pos else '中性')}；"
                "建议结合 LLM 精算。")
        return {
            "fundamental": {"投入度": invest, "承诺度": commit, "匹配度": match},
            "technical": {
                "回复速度": activity, "字数比": activity,
                "主动率": activity, "话题延续": activity,
            },
            "emotional": {
                "正负情绪比": sentiment_ratio,
                "情绪波动": fluctuation,
                "情绪深度": min(100, 30 + total_chars // 30),
            },
            "comprehensive": comp,
            "confidence": 0.5,
            "source": "rule_fallback",
        }


# ===== 借鉴 Soul of Waifu + ex-skill：主动分身 + 运行时逻辑 =====
def generate_proactive_message(person_id: str, trigger_type: str = "long_time_no_contact") -> dict:
    """主动消息（软件内提示，不实际发送）。"""
    try:
        msg_map = {
            "long_time_no_contact": "好久没聊，TA可能会说：「最近在忙什么？」",
            "special_date":         "纪念日/节日，TA可能会说：「今天是什么日子还记得吗？」",
            "user_down":            "你情绪低落时，TA可能会说：「怎么了，跟我说说。」",
        }
        return {"trigger": trigger_type,
                "message": msg_map.get(trigger_type, "TA可能会主动联系你。"),
                "confidence": 0.7,
                "suggested_action": "参考回复"}
    except Exception:
        return {}


def predict_with_runtime_logic(message: str, persona_6layer: dict = None,
                                memory: list = None, emotion_28: dict = None,
                                neurohormone: dict = None) -> dict:
    """运行时逻辑：Layer0 → persona_spec 派生 → 场景检测 → 模板选取 → Layer2 表达。

    v2 改造（2026-10-22）：从占位桩接成规则预测器，复用 twin_sim_pipeline 的
    derive_persona_spec / _detect_scene / _REPLY_BANK / _TERSE_BANK / _seeded_pick。
    纯规则 + 模板，不依赖大模型；确定性选取（md5 seed），离线可复现。
    """
    steps = []
    reply = "（默认回复）"
    triggered = None
    try:
        # ---- 复用 twin_sim_pipeline 现成资产（延迟 import 防循环依赖）----
        from . import twin_sim_pipeline as tsp

        # 1) Layer0 硬规则优先（保留旧行为）
        layer0_rules = (persona_6layer or {}).get("layers", {}).get(0, {}).get("rules", [])
        if "生气就冷战" in (layer0_rules or []) and ("生气" in (message or "") or "烦" in (message or "")):
            triggered = "冷战模式"
            reply = "嗯。"
            steps.append("Layer0 核心规则检查：触发冷战模式")
        else:
            steps.append("Layer0 核心规则检查：未触发")

        # 2) 从 persona 派生 style / verbosity
        try:
            spec = tsp.derive_persona_spec(persona_6layer or {})
        except Exception:  # noqa: BLE001
            spec = {"style": "secure", "verbosity": "normal", "avoid": 50, "anx": 50}
        style = spec.get("style", "secure")
        verb = spec.get("verbosity", "normal")
        # v6: 对方实测是 avoid 型（平均 42 字短回），强制走 avoid style
        style = "avoid"
        steps.append(f"persona_spec: style={style}（v6 强制 avoid） verbosity={verb}")

        # 3) 场景检测
        scene = tsp._detect_scene(message or "")
        steps.append(f"scene={scene}")

        # 3a) v6 长文→短附和分支：用户发 >100 字长文，对方回短附和（"嗯""好""知道了"）
        msg_len = len(message or "")
        if msg_len > 100:
            eff_scene = "long_user_short_reply"
            steps.append(f"长度分支：msg_len={msg_len} >100，对方回短附和（avoid 模式）")
        else:
            eff_scene = scene

        # 3b) v3 情绪反转：抱怨 + 转折词 → 安抚，不是 conflict/question
        _t = (message or "")
        _has_complaint = any(w in _t for w in ("烦", "生气", "总是", "从来", "你怎么"))
        _has_turn = any(w in _t for w in ("但是", "不过", "其实", "然而", "可是"))
        if _has_complaint and _has_turn:
            eff_scene = "emotion"
            steps.append("情绪反转：抱怨+转折词，scene 覆盖为 emotion（安抚类）")

        # 3c) v4 question 收窄：没有明确疑问标记的 question 误判 → 降为 daily
        if eff_scene == "question":
            _has_qmark = "?" in _t or "？" in _t
            _has_qword = any(w in _t for w in ("吗", "呢", "什么", "为什么", "怎么不", "能不能", "可不可以"))
            if not (_has_qmark or _has_qword):
                eff_scene = "daily"
                steps.append("question 收窄：无明确疑问标记，降级为 daily")

        # 3e) v4 praise 误判降级：praise 词表太宽，降到 daily 走日常附和
        if eff_scene == "praise":
            eff_scene = "daily"
            steps.append("praise 降级为 daily（词表过宽，避免误判）")

        # 3f) v8 安慰触发：用户安慰对方（"没关系/理解/辛苦"）→ 对方回感谢类
        if any(w in _t for w in ("没关系", "能理解", "辛苦", "别在意", "没事的", "不怪你")):
            eff_scene = "comfort_reply"
            steps.append("安慰触发：用户安慰对方，回感谢类")
        #     补 avoid 缺的"附和/继续陈述"类，避免所有场景都回"看情况/再说"
        #     注：CONTACT_A 是单边对话（自己一直在说），下一句通常是短附和/继续陈述
        if style == "avoid":
            _EXPANDED_AVOID = {
                "daily": ["嗯，我知道了。", "是这样啊。", "嗯，行。", "嗯，好的。"],
                "question": ["嗯，我想想。", "这个……", "我看看。", "嗯，再说。"],
                "conflict": ["行，我知道了。", "嗯。", "算了。", "行吧。"],
                "invite": ["嗯，好啊。", "行，到时候说。", "嗯，我考虑下。", "看情况吧。"],
                "emotion": ["嗯，我知道了。", "嗯。", "行吧。", "好的。"],
                "praise": ["嗯，没有啦。", "哎呀。", "嗯，还行。", "哈哈。"],
                "long_user_short_reply": ["嗯。", "好。", "知道了。", "嗯，行。"],
                "comfort_reply": ["谢谢理解。", "谢谢你。", "好的，我注意。", "嗯，谢谢。"],
            }
            if eff_scene in _EXPANDED_AVOID:
                _expanded = _EXPANDED_AVOID[eff_scene]
            else:
                _expanded = None
        else:
            _expanded = None

        # 4) memory 辅助：当前 scene=daily 时偏向最近 memory 的场景
        if memory:
            recent = [str(m) for m in (memory or []) if m][-3:]
            mem_scenes = [tsp._detect_scene(m) for m in recent]
            mem_scenes = [s for s in mem_scenes if s != "daily"]
            if eff_scene == "daily" and mem_scenes:
                eff_scene = mem_scenes[-1]
                steps.append(f"scene 被 memory 上下文修正为 {eff_scene}")
            steps.append(f"关系记忆检索完成（最近 {len(recent)} 条）")

        # 5) 模板选取：terse 优先 _TERSE_BANK，否则按 style 从 _REPLY_BANK
        #    v4: avoid style 优先用 _expanded 扩展模板（补附和/讲道理类）
        seed = f"{style}|{eff_scene}|{message or ''}|{(memory or [])[-1] if memory else ''}"
        if not triggered:
            try:
                if _expanded:
                    candidates = _expanded
                    reply = tsp._seeded_pick(seed, candidates) or reply
                    steps.append(f"从 _EXPANDED_AVOID[{eff_scene}] 选模板（v4 扩展）")
                elif verb == "terse" and eff_scene in tsp._TERSE_BANK:
                    candidates = tsp._TERSE_BANK.get(eff_scene) or []
                    reply = tsp._seeded_pick(seed, candidates) or reply
                    steps.append(f"从 _TERSE_BANK[{eff_scene}] 选模板")
                else:
                    bank = (tsp._REPLY_BANK.get(style) or {}).get(eff_scene)
                    if not bank and eff_scene == "deep_share":
                        bank = (tsp._REPLY_BANK.get("secure") or {}).get("emotion") or []
                        steps.append("deep_share 无专用 bank，fallback 到 secure emotion 倾听类")
                    if not bank:
                        bank = (tsp._REPLY_BANK.get("secure") or {}).get(eff_scene) or []
                    reply = tsp._seeded_pick(seed, bank) or reply
                    steps.append(f"从 _REPLY_BANK[{style}][{eff_scene}] 选模板")
            except Exception:  # noqa: BLE001
                pass

        # 6) emotion / confidence
        if triggered:
            emotion = "生气"
        elif style == "anxious":
            emotion = "紧张"
        elif style == "avoid":
            emotion = "疏离"
        else:
            emotion = "平静"
        emotion = (emotion_28 or {}).get("emotion") or emotion
        steps.append(f"情绪状态：{emotion}")
        steps.append("荷尔蒙修饰：" + str((neurohormone or {}).get("verbosity", "normal")))

        confidence = 0.7 if reply and reply != "（默认回复）" else 0.5
        if triggered:
            confidence = 0.8

        return {"predicted_reply": reply,
                "emotion": emotion,
                "layer0_triggered": triggered,
                "confidence": confidence,
                "persona_style": style,
                "scene": eff_scene,
                "steps": steps}
    except Exception:  # noqa: BLE001
        return {"predicted_reply": reply, "emotion": "平静",
                "layer0_triggered": None, "confidence": 0.3, "steps": steps}
