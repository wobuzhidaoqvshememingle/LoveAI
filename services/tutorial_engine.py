"""
LoveAI v2.0 - 攻略引擎（TOP二私教咨询）

双窗口模式：
  - 主窗口单人模式（mode='self'）：针对用户自身的深度提升方案；
  - 目标窗口双人模式（mode='target'）：针对具体任务目标的关系攻略。

按需生成深度攻略、分步教程，并提供攻略库检索（guides 表 + RAG 知识库）。
不主动推送，由 UI 层 / TaskEngine 按需调用。

服务层纯 Python 实现，不依赖 PySide6 / QObject，禁止 pywebview。
数据库操作统一走 Database 实例的 execute / fetch_one / fetch_all，
SQL 参数一律使用 :name 命名格式。
LLM 调用失败时返回兜底结果，绝不崩溃。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.database import Database
    from services.llm_gateway import LLMGateway
    from services.rag_service import RAGService

logger = logging.getLogger(__name__)


class TutorialEngine:
    """攻略引擎：深度攻略生成 / 分步教程展开 / 攻略库检索。"""

    #: 任务方向生成失败（LLM 不可用）时的兜底文案
    DEFAULT_DIRECTION = "根据当前关系阶段稳步推进，注意观察对方反馈"

    # ------------------------------------------------------------------ #
    # 初始化
    # ------------------------------------------------------------------ #
    def __init__(
        self,
        db: "Database",
        rag: "RAGService",
        llm: "LLMGateway",
        twin: Any = None,
    ) -> None:
        """初始化攻略引擎。

        Args:
            db: Database 实例（需具备 execute / fetch_one / fetch_all 方法）。
            rag: RAGService 实例，用于检索恋爱知识库上下文；可为 None。
            llm: LLMGateway 实例；可为 None（此时所有生成走兜底结果）。
            twin: DigitalTwin 实例（可选）。目标窗口双人模式时用于模拟对方视角，
                  可为 None。本引擎不直接调用其方法，仅保存引用供上层联动。
        """
        self.db = db
        self.rag = rag
        self.llm = llm
        self.twin = twin

        # 防御：确保 guides 表存在
        self._ensure_guides_table()

        logger.info(
            "TutorialEngine 初始化完成: rag=%s, llm=%s, twin=%s",
            self.rag is not None, self.llm is not None, self.twin is not None,
        )

    def _ensure_guides_table(self) -> None:
        """创建 guides 攻略表（若不存在）。"""
        try:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS guides ("
                "id TEXT PRIMARY KEY, "
                "mode TEXT, "
                "title TEXT, "
                "content_json TEXT, "
                "person_id TEXT, "
                "created_at TEXT DEFAULT (datetime('now'))"
                ")"
            )
        except Exception as e:
            # 表已存在或连接异常时仅记录日志，不抛出（__init__ 不得崩溃）
            logger.warning("guides 表创建失败: %s", e)

    # ------------------------------------------------------------------ #
    # 对外主入口：生成深度攻略
    # ------------------------------------------------------------------ #
    def generate_guide(
        self,
        question: str,
        mode: str = "target",
        person_id: Optional[str] = None,
        user_profile: Optional[dict] = None,
        related_task_id: Optional[str] = None,
    ) -> dict:
        """用户提问 -> 生成深度攻略（双窗口模式分发）。

        Args:
            question: 用户原始提问。
            mode: 'self' 主窗口单人模式（用户自身提升）；
                  'target' 目标窗口双人模式（针对具体对象的关系攻略）。
            person_id: 目标人物 ID（target 模式必填）。
            user_profile: 用户自身档案 dict。
            related_task_id: 关联任务 ID（可选）。

        Returns:
            攻略 dict，至少包含 id / mode / title / plan 等字段。
        """
        if mode == "self":
            return self._generate_self_guide(question, user_profile)

        # target 模式缺少 person_id 时降级为单人模式，避免崩溃
        if not person_id:
            logger.warning("target 模式缺少 person_id，降级为 self 模式")
            return self._generate_self_guide(question, user_profile)

        return self._generate_target_guide(
            question, person_id, user_profile, related_task_id
        )

    # ------------------------------------------------------------------ #
    # 主窗口：单人自我提升攻略
    # ------------------------------------------------------------------ #
    def _generate_self_guide(
        self,
        question: str,
        user_profile: Optional[dict] = None,
    ) -> dict:
        """生成主窗口（单人模式）的深度自我提升方案。

        Args:
            question: 用户提问。
            user_profile: 用户自身档案 dict。

        Returns:
            攻略 dict：title / analysis / plan(含时间节点) / tips /
            resources / milestones，并附加 id 与 mode='self'。
        """
        system_prompt = (
            "你是一名资深的亲密关系私教教练，擅长帮助用户进行自我提升"
            "（外在形象、沟通表达、情绪管理、恋爱心态、社交圈层等）。\n"
            "请根据用户的提问和自身档案，输出一份深度、可落地的自我提升方案。\n"
            "严格输出 JSON（不要使用 markdown 代码块、不要任何多余文字），结构如下：\n"
            "{\n"
            '  "title": "攻略标题（15字以内）",\n'
            '  "analysis": "现状分析：用户的优势、短板与核心问题（200字以内）",\n'
            '  "plan": [{"step": "第1步", "action": "具体动作", '
            '"timeline": "时间节点，如 第1周"}],\n'
            '  "tips": ["注意事项或常见误区1", "..."],\n'
            '  "resources": ["推荐书籍/课程/练习方法1", "..."],\n'
            '  "milestones": [{"name": "里程碑名称", '
            '"metric": "可量化指标，如 体重下降5kg"}]\n'
            "}"
        )

        profile_text = (
            json.dumps(user_profile, ensure_ascii=False)
            if user_profile else "（暂无用户档案）"
        )
        user_prompt = (
            f"用户提问：{question}\n\n"
            f"用户档案：\n{profile_text}\n\n"
            "请严格按要求输出 JSON。"
        )

        guide: dict = {}
        if self.llm is not None:
            try:
                raw = self.llm.chat(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    complexity="complex",
                    temperature=0.7,
                    max_tokens=3000,
                )
                guide = self._parse_llm_json(raw)
            except Exception as e:
                logger.warning("self 模式攻略生成失败，使用兜底: %s", e)

        if not guide:
            guide = self._fallback_self_guide(question)

        guide_id = self._save_guide(
            mode="self",
            title=guide.get("title", "自我提升方案"),
            content=guide,
            person_id=None,
        )
        guide["id"] = guide_id
        guide["mode"] = "self"
        return guide

    # ------------------------------------------------------------------ #
    # 目标窗口：双人关系攻略
    # ------------------------------------------------------------------ #
    def _generate_target_guide(
        self,
        question: str,
        person_id: str,
        user_profile: Optional[dict] = None,
        related_task_id: Optional[str] = None,
    ) -> dict:
        """生成目标窗口（双人模式）的关系攻略。

        流程：
          1. 查询对方档案（persons 表）；
          2. 查询当前好感度（current_affection 表）；
          3. 若有关联任务，查询任务信息（tasks 表）；
          4. RAG 检索相关知识（rag.search，top_k=3）；
          5. 构建 prompt，调用 LLM（complex）；
          6. 解析 JSON 并保存到 guides 表；
          7. 返回攻略 dict，附加 mode='target' 与 id。

        Args:
            question: 用户提问。
            person_id: 目标人物 ID。
            user_profile: 用户自身档案 dict。
            related_task_id: 关联任务 ID（可选）。

        Returns:
            攻略 dict：title / situation_analysis / strategy / plan /
            risk_warnings / expected_outcome / alternative_plans，
            并附加 id 与 mode='target'。
        """
        # 1. 对方档案
        person = self.db.fetch_one(
            "SELECT id, nickname, wechat_nickname, gender, birthday, "
            "occupation, relationship_stage, soul_path "
            "FROM persons WHERE id=:person_id",
            {"person_id": person_id},
        )

        # 2. 当前好感度
        affection = self.db.fetch_one(
            "SELECT person_id, scores_json, total_score, updated_at "
            "FROM current_affection WHERE person_id=:person_id",
            {"person_id": person_id},
        )

        # 3. 关联任务
        task: Optional[dict] = None
        if related_task_id:
            task = self.db.fetch_one(
                "SELECT id, title, description, task_type, difficulty, "
                "estimated_time, deadline, status "
                "FROM tasks WHERE id=:task_id",
                {"task_id": related_task_id},
            )

        # 4. RAG 知识库检索
        rag_context = "（无相关知识库内容）"
        if self.rag is not None:
            try:
                hits = self.rag.search(question, top_k=3)
                if hits:
                    chunks = [
                        f"【{h.get('title', '')}】"
                        f"{(h.get('content', '') or '')[:200]}"
                        for h in hits
                    ]
                    rag_context = "\n".join(chunks)
            except Exception as e:
                logger.warning("RAG 检索失败，跳过上下文: %s", e)

        # 组装上下文文本
        person_text = json.dumps(person, ensure_ascii=False) if person else "（未找到对方档案）"
        affection_text = (
            f"总分={affection.get('total_score')}，维度={affection.get('scores_json')}"
            if affection else "（暂无好感度记录，默认初始阶段）"
        )
        task_text = json.dumps(task, ensure_ascii=False) if task else "（无关联任务）"
        profile_text = (
            json.dumps(user_profile, ensure_ascii=False)
            if user_profile else "（暂无用户档案）"
        )
        twin_note = (
            "（系统已接入数字分身，请在攻略中适当体现对方视角的预判）"
            if self.twin is not None else ""
        )

        system_prompt = (
            "你是顶尖恋爱攻略专家（TOP二私教咨询），擅长双人关系操盘。\n"
            "请结合双方档案、当前好感度、关联任务与知识库，为用户输出一份"
            "针对目标对象的关系攻略。\n"
            "严格输出 JSON（不要使用 markdown 代码块、不要任何多余文字），结构如下：\n"
            "{\n"
            '  "title": "攻略标题（15字以内）",\n'
            '  "situation_analysis": "双方态势分析：双方位置、筹码、当前关系阶段",\n'
            '  "strategy": "核心策略（一句话主线）",\n'
            '  "plan": [{"step": "第1步", "action": "具体动作/话术方向", '
            '"timeline": "时间节点"}],\n'
            '  "risk_warnings": ["可能踩的坑1", "..."],\n'
            '  "expected_outcome": "预期结果",\n'
            '  "alternative_plans": ["Plan B 备选1", "..."]\n'
            "}"
        )

        user_prompt = (
            f"用户提问：{question}\n\n"
            f"对方档案：\n{person_text}\n\n"
            f"当前好感度：\n{affection_text}\n\n"
            f"关联任务：\n{task_text}\n\n"
            f"用户自身档案：\n{profile_text}\n\n"
            f"知识库参考：\n{rag_context}\n\n"
            f"{twin_note}\n\n"
            "请严格按要求输出 JSON。"
        )

        guide: dict = {}
        if self.llm is not None:
            try:
                raw = self.llm.chat(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    complexity="complex",
                    temperature=0.7,
                    max_tokens=3000,
                )
                guide = self._parse_llm_json(raw)
            except Exception as e:
                logger.warning("target 模式攻略生成失败，使用兜底: %s", e)

        if not guide:
            guide = self._fallback_target_guide(question)

        guide_id = self._save_guide(
            mode="target",
            title=guide.get("title", "关系攻略"),
            content=guide,
            person_id=person_id,
        )
        guide["id"] = guide_id
        guide["mode"] = "target"
        return guide

    # ------------------------------------------------------------------ #
    # 任务轻量攻略方向（TaskEngine 调用）
    # ------------------------------------------------------------------ #
    def generate_task_direction(
        self,
        task_title: str,
        person_id: Optional[str] = None,
    ) -> str:
        """为任务生成轻量攻略方向（任务生成时附带）。

        Args:
            task_title: 任务标题。
            person_id: 目标人物 ID（预留，当前不影响方向生成）。

        Returns:
            不超过 200 字的纯文本攻略方向；LLM 不可用时返回默认方向。
        """
        if self.llm is None:
            return self.DEFAULT_DIRECTION

        system_prompt = (
            "你是恋爱私教教练。请为给定任务生成一句话攻略方向和关键注意点。"
            "要求：直接输出纯文本，不要 JSON、不要标题、不要 markdown，"
            "控制在 200 字以内。"
        )
        user_prompt = f"为任务\"{task_title}\"生成一句话攻略方向和关键注意点。"

        try:
            text = self.llm.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                complexity="simple",
                temperature=0.5,
                max_tokens=300,
            )
            text = (text or "").strip()
            if not text:
                return self.DEFAULT_DIRECTION
            return text[:200]
        except Exception as e:
            logger.warning("任务方向生成失败，返回默认方向: %s", e)
            return self.DEFAULT_DIRECTION

    # ------------------------------------------------------------------ #
    # 攻略库检索 / 详情 / 列表
    # ------------------------------------------------------------------ #
    def search_guides(self, keyword: str, top_k: int = 5) -> list:
        """攻略库检索：历史攻略 + RAG 知识库合并去重。

        Args:
            keyword: 检索关键词。
            top_k: 返回条数上限。

        Returns:
            list[dict]：[{"id","title","summary","source","created_at"}]，
            source ∈ {"history", "knowledge"}；无结果时返回空列表。
        """
        results: list = []
        seen: set[str] = set()

        # 1. 历史攻略（guides 表标题 / 内容模糊匹配）
        try:
            rows = self.db.fetch_all(
                "SELECT id, title, content_json, created_at FROM guides "
                "WHERE title LIKE :kw OR content_json LIKE :kw "
                "ORDER BY created_at DESC LIMIT :limit",
                {"kw": f"%{keyword}%", "limit": top_k},
            )
            for row in rows:
                title = row.get("title") or "未命名攻略"
                summary = self._extract_summary(row.get("content_json"))
                item = {
                    "id": row.get("id") or "",
                    "title": title,
                    "summary": summary,
                    "source": "history",
                    "created_at": row.get("created_at") or "",
                }
                key = title[:30]
                if key not in seen:
                    seen.add(key)
                    results.append(item)
        except Exception as e:
            logger.warning("历史攻略检索失败: %s", e)

        # 2. RAG 知识库检索
        if self.rag is not None:
            try:
                hits = self.rag.search(keyword, top_k=top_k)
                for h in hits:
                    title = h.get("title") or "知识库条目"
                    content = (h.get("content") or "")[:120]
                    key = title[:30]
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append({
                        "id": "",
                        "title": title,
                        "summary": content,
                        "source": "knowledge",
                        "created_at": "",
                    })
            except Exception as e:
                logger.warning("RAG 知识库检索失败: %s", e)

        return results[:top_k]

    def get_guide(self, guide_id: str) -> Optional[dict]:
        """获取单个攻略详情。

        Args:
            guide_id: 攻略 ID。

        Returns:
            攻略 dict（content_json 已解析合并到顶层，可直接取 plan/tips 等）；
            不存在时返回 None。
        """
        row = self.db.fetch_one(
            "SELECT id, mode, title, content_json, person_id, created_at "
            "FROM guides WHERE id=:id",
            {"id": guide_id},
        )
        if not row:
            return None

        result: dict = {
            "id": row.get("id"),
            "mode": row.get("mode"),
            "title": row.get("title") or "",
            "person_id": row.get("person_id"),
            "created_at": row.get("created_at"),
        }
        # 解析 content_json 并合并到顶层，方便调用方直接取 plan/tips 等
        content_raw = row.get("content_json")
        if content_raw:
            try:
                content = json.loads(content_raw)
                if isinstance(content, dict):
                    result.update(content)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("攻略内容解析失败(id=%s): %s", guide_id, e)
        return result

    def list_guides(self, mode: Optional[str] = None, limit: int = 20) -> list:
        """列出攻略历史。

        Args:
            mode: 可选，按模式过滤（'self' / 'target'）。
            limit: 返回条数上限。

        Returns:
            list[dict]：攻略摘要列表，按创建时间倒序。
        """
        try:
            if mode:
                return self.db.fetch_all(
                    "SELECT id, mode, title, person_id, created_at FROM guides "
                    "WHERE mode=:mode ORDER BY created_at DESC LIMIT :limit",
                    {"mode": mode, "limit": limit},
                )
            return self.db.fetch_all(
                "SELECT id, mode, title, person_id, created_at FROM guides "
                "ORDER BY created_at DESC LIMIT :limit",
                {"limit": limit},
            )
        except Exception as e:
            logger.warning("攻略列表查询失败: %s", e)
            return []

    # ------------------------------------------------------------------ #
    # 攻略 -> 分步教程展开
    # ------------------------------------------------------------------ #
    def generate_step_by_step_tutorial(self, guide_id: str) -> dict:
        """将攻略扩展为分步教程：plan 中每一步展开为详细执行步骤。

        Args:
            guide_id: 攻略 ID。

        Returns:
            {"guide_id": "...", "steps": [{"step","detail","script_example","tips"}]}；
            攻略不存在时返回空 steps。
        """
        guide = self.get_guide(guide_id)
        if not guide:
            logger.warning("攻略不存在，无法展开分步教程: %s", guide_id)
            return {"guide_id": guide_id, "steps": []}

        plan = guide.get("plan") or []
        title = guide.get("title", "")

        steps: list = []
        for item in plan:
            step_text = self._plan_item_text(item)
            steps.append(self._expand_step(title, step_text))

        return {"guide_id": guide_id, "steps": steps}

    # ------------------------------------------------------------------ #
    # 内部工具方法
    # ------------------------------------------------------------------ #
    def _save_guide(
        self,
        mode: str,
        title: str,
        content: dict,
        person_id: Optional[str] = None,
    ) -> str:
        """将攻略保存到 guides 表，返回生成的攻略 ID。

        Args:
            mode: 'self' / 'target'。
            title: 攻略标题。
            content: 攻略完整 dict（序列化为 content_json）。
            person_id: 关联人物 ID（self 模式为 None）。

        Returns:
            str: 攻略 ID。
        """
        guide_id = uuid.uuid4().hex
        try:
            self.db.execute(
                "INSERT INTO guides (id, mode, title, content_json, person_id, created_at) "
                "VALUES (:id, :mode, :title, :content_json, :person_id, datetime('now'))",
                {
                    "id": guide_id,
                    "mode": mode,
                    "title": title or "",
                    "content_json": json.dumps(content, ensure_ascii=False),
                    "person_id": person_id,
                },
            )
        except Exception as e:
            # 保存失败不影响返回，仅记录日志
            logger.warning("攻略保存失败(id=%s): %s", guide_id, e)
        return guide_id

    def _expand_step(self, guide_title: str, step_text: str) -> dict:
        """对 plan 中的单步调用 LLM 展开为详细教程。

        Args:
            guide_title: 所属攻略标题。
            step_text: 该步的原始描述。

        Returns:
            {"step","detail","script_example","tips"}；LLM 不可用时返回兜底结构。
        """
        fallback = {
            "step": step_text,
            "detail": "",
            "script_example": "",
            "tips": "",
        }
        if self.llm is None or not step_text:
            return fallback

        system_prompt = (
            "你是恋爱私教教练。针对攻略中的某一步，给出：详细执行步骤、"
            "可直接使用的话术示例、注意事项。\n"
            "严格输出 JSON（不要 markdown 代码块），结构：\n"
            '{"detail":"详细执行步骤（200字以内）",'
            '"script_example":"一句可直接照用的话术示例",'
            '"tips":"关键注意点"}'
        )
        user_prompt = (
            f"攻略标题：{guide_title}\n"
            f"本步任务：{step_text}\n"
            "请严格按要求输出 JSON。"
        )
        try:
            raw = self.llm.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                complexity="medium",
                temperature=0.7,
                max_tokens=600,
            )
            data = self._parse_llm_json(raw)
            if not data:
                return fallback
            return {
                "step": step_text,
                "detail": str(data.get("detail", "") or ""),
                "script_example": str(data.get("script_example", "") or ""),
                "tips": str(data.get("tips", "") or ""),
            }
        except Exception as e:
            logger.warning("分步教程展开失败(step=%s): %s", step_text, e)
            return fallback

    @staticmethod
    def _plan_item_text(item: Any) -> str:
        """把 plan 列表项统一转成纯文本描述（兼容 dict / str）。"""
        if isinstance(item, dict):
            return (
                item.get("action")
                or item.get("step")
                or json.dumps(item, ensure_ascii=False)
            )
        return str(item)

    @staticmethod
    def _parse_llm_json(text: str) -> dict:
        """健壮解析 LLM 输出的 JSON：去 markdown 围栏 -> 直接解析 -> 截取花括号解析。

        Args:
            text: LLM 原始输出。

        Returns:
            dict；解析失败返回空 dict。
        """
        if not text:
            return {}
        text = text.strip()

        # 去掉 ```json ... ``` 围栏
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # 直接解析
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            pass

        # 截取第一个 { 到最后一个 } 再解析
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start:end + 1])
                return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, TypeError):
                pass
        return {}

    @staticmethod
    def _extract_summary(content_json: Optional[str]) -> str:
        """从 content_json 中提取一段摘要用于检索结果展示（120字以内）。"""
        if not content_json:
            return ""
        try:
            data = json.loads(content_json)
        except (json.JSONDecodeError, TypeError):
            return ""
        if not isinstance(data, dict):
            return ""
        for key in ("analysis", "situation_analysis", "strategy", "expected_outcome"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:120]
        return ""

    @staticmethod
    def _fallback_self_guide(question: str) -> dict:
        """LLM 不可用时的单人模式兜底攻略。"""
        return {
            "title": "自我提升方案",
            "analysis": "大模型暂不可用，已为你生成基础方案，建议稍后重试获取深度版。",
            "plan": [
                {"step": "第1步", "action": f"围绕「{question[:50]}」梳理现状", "timeline": "本周"},
            ],
            "tips": ["保持节奏，记录每次尝试后的反馈"],
            "resources": [],
            "milestones": [],
        }

    @staticmethod
    def _fallback_target_guide(question: str) -> dict:
        """LLM 不可用时的双人模式兜底攻略。"""
        return {
            "title": "关系攻略",
            "situation_analysis": "大模型暂不可用，无法分析双方态势，建议稍后重试。",
            "strategy": "根据当前关系阶段稳步推进",
            "plan": [
                {"step": "第1步", "action": f"围绕「{question[:50]}」观察对方反馈", "timeline": "本周"},
            ],
            "risk_warnings": ["避免一次性推进过快，注意对方情绪信号"],
            "expected_outcome": "维持关系温度，为下一步互动铺路",
            "alternative_plans": ["若无进展则降低频率，回归自我提升"],
        }
