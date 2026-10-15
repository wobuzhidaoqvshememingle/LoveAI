"""
LoveAI v2.0 - 任务引擎（引导闭环版）

引导闭环六步：目的 → 任务 → 教程 → 执行 → 反馈 → 推进。
负责：目标拆解、任务发布、攻略方向自动附带、分步进度、反馈判定、节奏推进。

服务层纯 Python 实现，不依赖 PySide6；信号通过构造参数 signals 注入。
数据库操作统一走 Database 实例的 execute / fetch_one / fetch_all，
SQL 参数全部使用 :name 命名格式（不是 ?）。
"""
import json
import logging
import random
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.database import Database
    from services.affection_engine import AffectionEngine
    from app.signals import AppSignals

logger = logging.getLogger(__name__)

# 攻略方向缺失时的兜底文案
_DEFAULT_DIRECTION = "根据当前关系阶段稳步推进"

from services import psychology_laws  # v2.1: 心理学定律任务生成（修复引导导向）
from services import personality_strategy  # v4: 人格倾向识别与专属任务（14种）
from services.user_ability import UserAbility  # v3.0: 用户能力评估（个性化任务难度）
from services import refusal_downgrade as _p07_refusal  # v3.1 P-07: 拒绝信号自动降级

# v3.0: 单用户应用，能力画像固定存在 "self" 这一行
_SELF_USER_ID = "self"


class TaskEngine:
    """任务引擎：目标拆解 + 引导闭环推进。

    闭环六步对应：
      ① 目的：generate_main_tasks 按关系阶段拆解主线目标；
      ② 任务：_create 发布任务并自动附带攻略方向；
      ③ 教程：攻略方向由 guide_engine 生成（可空）；
      ④ 执行：_generate_steps 把现实任务拆成可勾选的分步；
      ⑤ 反馈：check_completion 根据聊天关键词判定分步/任务完成；
      ⑥ 推进：_complete → _advance_loop 自动接棒下一步，节奏不中断。
    """

    # 主线任务：关系阶段 → 3 个任务标题（引导闭环①：目标拆解）
    STAGE_GOALS: dict[int, list[str]] = {
        0: ["完善对方基础档案", "发起首次自然破冰", "找到一个共同话题"],          # 建立基本联系
        1: ["延续聊天节奏不冷场", "记住对方一个喜好", "从公事闲聊过渡到私事"],     # 从陌生到熟悉
        2: ["分享一个自己的小故事", "在对方低落时给予陪伴", "建立固定的聊天时间"],  # 建立情感连接
        # 好友/密友阶段：好感度中段。若关系刚经历降温，这些目标偏向修复与巩固，
        # 而不是回退到"首次破冰"（修复断链：冷战期不再生成初识任务）。
        3: ["主动开启一次轻松对话", "复盘近期矛盾并温和沟通", "用小事重建安全感"],  # 好友/修复降温
        4: ["保持稳定互动节奏", "深入了解对方情绪需求", "安排一次轻松的线下相处"],  # 密友/巩固连接
        5: ["制造一次独处机会", "用行动表达关心", "试探彼此未来规划"],            # 暧昧升温
        6: ["规划一次共同出行", "介绍对方进入自己圈子", "确认彼此关系定位"],       # 稳定关系
        7: ["维护日常相处质感", "共同确立一个生活小目标", "处理分歧时先共情再解决"],  # 深度关系
        8: ["规划共同的中长期生活", "正式融入彼此家庭", "建立共同的金钱观与分工"],   # 稳定伴侣
        9: ["巩固长期承诺", "共同应对一次外部压力", "规划人生下一步"],           # 长期承诺
        10: ["经营长期亲密关系", "保持仪式感与新鲜感", "携手规划未来家庭"],       # 灵魂/终身伴侣
    }

    # 主线模板的真实方向语义旁挂表（不改 STAGE_GOALS 本体）。
    # 与 STAGE_GOALS 同序：每个 stage 的 3 个模板分别标 升温/维稳/修复/通用。
    # 用途：后撤/修复方向下丢弃"升温(推进)"模板，避免方向罗盘判后撤却推推进主线。
    STAGE_TASK_DIRECTION: dict[int, list[str]] = {
        0: ["通用", "升温", "升温"], 1: ["维稳", "通用", "升温"],
        2: ["升温", "修复", "维稳"], 3: ["维稳", "修复", "修复"],
        4: ["维稳", "升温", "升温"], 5: ["升温", "维稳", "升温"],
        6: ["升温", "升温", "升温"], 7: ["维稳", "升温", "修复"],
        8: ["升温", "升温", "维稳"], 9: ["维稳", "修复", "升温"],
        10: ["维稳", "升温", "升温"],
    }

    # 限时任务触发模式（引导闭环：机会捕捉）
    # 每项：分类 / 标题 / 时限小时 / 正则 / 目标
    LIMITED_TRIGGERS: list[dict[str, Any]] = [
        {
            "category": "邀约推进",
            "title": "抓住邀约信号并落实",
            "hours": 48,
            "pattern": r"(一起|约|出来|见面).*(吃饭|见面|电影|逛街|咖啡|喝|玩|聚|约会)",
            "goal": "48小时内把口头邀约落成具体安排",
        },
        {
            "category": "关系维护",
            "title": "保持日常互动热度",
            "hours": 168,
            "pattern": r"(想你|在干嘛|最近怎么样|好久没|忙吗|睡了吗|早安|晚安)",
            "goal": "7天内维持稳定互动节奏",
        },
        {
            "category": "冲突修复",
            "title": "修复降温与负面情绪",
            "hours": 24,
            "pattern": r"(算了|不想聊|别烦|生气|难过|冷战|不理|分手|吵|烦|累了)",
            "goal": "24小时内化解负面情绪、回暖关系",
        },
    ]

    # 现实/限时任务自动分步模板（带进度条与完成关键词）
    STEP_TEMPLATES: dict[str, list[dict[str, list[str] | str]]] = {
        "邀约推进": [
            {"name": "捕捉对方兴趣点", "complete_keywords": ["兴趣", "喜欢", "想去"]},
            {"name": "拟定具体邀约方案", "complete_keywords": ["时间", "地点", "方案"]},
            {"name": "发出邀约消息", "complete_keywords": ["约", "出来", "有空"]},
            {"name": "确认对方意向", "complete_keywords": ["好啊", "可以", "行"]},
            {"name": "落实约会安排", "complete_keywords": ["定了", "周六", "周五", "说定"]},
        ],
        "关系维护": [
            {"name": "回忆近期互动亮点", "complete_keywords": ["上次", "记得", "之前"]},
            {"name": "发起自然聊天", "complete_keywords": ["在吗", "在干嘛", "最近"]},
            {"name": "延续话题并收尾", "complete_keywords": ["哈哈", "晚安", "拜拜"]},
        ],
        "冲突修复": [
            {"name": "识别冲突触发点", "complete_keywords": ["因为", "原因", "误会"]},
            {"name": "调整沟通姿态", "complete_keywords": ["抱歉", "对不起", "理解"]},
            {"name": "发送修复消息", "complete_keywords": ["聊聊", "说说", "解释"]},
            {"name": "观察情绪回暖", "complete_keywords": ["没事", "好吧", "还好"]},
        ],
        "需求引导": [
            {"name": "倾听对方需求", "complete_keywords": ["需要", "想要", "希望"]},
            {"name": "确认需求细节", "complete_keywords": ["具体", "怎么", "什么"]},
            {"name": "给出可行方案", "complete_keywords": ["建议", "方案", "可以这样"]},
            {"name": "寻求对方认可", "complete_keywords": ["怎么样", "行吗", "好吗"]},
            {"name": "约定执行方式", "complete_keywords": ["那就", "说定", "安排"]},
            {"name": "复盘需求满足度", "complete_keywords": ["满意", "效果", "下次"]},
        ],
    }

    def __init__(
        self,
        db: "Database",
        affection: "AffectionEngine",
        guide_engine: Any = None,
        twin: Any = None,
        signals: "AppSignals" = None,
        strategy_advisor: Any = None,
        commitment_tracker: Any = None,
        reasoner: Any = None,
    ) -> None:
        """初始化任务引擎。

        Args:
            db: Database 实例（execute / fetch_one / fetch_all）。
            affection: AffectionEngine 实例，用于完成奖励与读取总分/阶段。
            guide_engine: 攻略引擎实例，需提供 generate_task_direction；
                          为 None 时使用默认攻略方向。
            twin: DigitalTwin 实例，可为 None（预留扩展）。
            signals: AppSignals 实例，用于 emit task_updated；可为 None。
            strategy_advisor: StrategyAdvisor 实例（可选）。注入后任务攻略方向会
                附带"基于对方XX人格特征推荐XX技巧"的因人施策说明；为 None 时
                行为与旧版完全一致（向后兼容）。
            commitment_tracker: CommitmentTracker 实例（可选）。注入后主线任务会
                根据对方承诺兑现率调整推进策略（大胆/正常/小步验证/引导兑现习惯）；
                为 None 时行为与旧版完全一致（向后兼容）。
            reasoner: R3.3-81 AffectionReasoner 实例（可选）。注入后 check_completion
                的关键词命中会先过"对方是否真的有正向进展"的分析门禁，不再
                "命中关键词就推进"；为 None 时行为与旧版完全一致（向后兼容）。
        """
        self.db = db
        self.affection = affection
        self.guide_engine = guide_engine
        self.twin = twin
        self.signals = signals
        self.strategy_advisor = strategy_advisor
        self.commitment_tracker = commitment_tracker
        # R3.3-81：任务进度由"分析结论"驱动的门禁推理层（可选注入）
        self.reasoner = reasoner
        # 长期优化"助推-滑翔"规划器（懒加载；任何异常都回退旧 _score_candidate 路径）
        self._boost_planner = None
        # v3.0: 用户能力评估（个性化任务难度）；db 为 None 时降级为默认中等画像
        try:
            self._user_ability = UserAbility(db)
        except Exception as e:  # noqa: BLE001 - 能力模块失败不影响主引擎
            logger.warning("UserAbility 初始化失败，降级为无能力评估: %s", e)
            self._user_ability = UserAbility(None)
        logger.info(
            "TaskEngine 初始化完成 guide_engine=%s twin=%s signals=%s strategy_advisor=%s commitment_tracker=%s",
            type(guide_engine).__name__ if guide_engine else None,
            type(twin).__name__ if twin else None,
            "on" if signals else "off",
            "on" if strategy_advisor else "off",
            "on" if commitment_tracker else "off",
        )

    # ------------------------------------------------------------------ #
    # 引导闭环①②：目标拆解 + 主线任务发布
    # ------------------------------------------------------------------ #
    def generate_main_tasks(self, person_id: str, stage: int) -> list[dict]:
        """根据关系阶段生成主线任务。

        Args:
            person_id: 人物 ID。
            stage: 关系阶段（0/1/2/5/6 有预置目标，其余阶段回退到阶段0）。

        Returns:
            list[dict]: 本次创建的任务列表，每项含 id / title / guide_direction。
        """
        titles = self.STAGE_GOALS.get(stage) or self.STAGE_GOALS.get(0, [])
        # 因人施策：若注入了 strategy_advisor，按当前画像+阶段一次性算出策略说明，
        # 附到本批所有主线任务的攻略方向里（无画像/未注入则为空，行为不变）。
        strategy_note = self._compute_strategy_note(person_id, stage)
        # 履约跟踪：若注入了 commitment_tracker，按对方承诺兑现率调整推进策略
        commit_note = self._compute_commitment_note(person_id)
        if commit_note:
            strategy_note = f"{strategy_note}｜{commit_note}" if strategy_note else commit_note
        # 反伪装审计：若画像存在高伪装风险(>=60)，追加验证类任务与说明
        dec_note, verify_titles = self._compute_deception_note(person_id)
        if dec_note:
            strategy_note = f"{strategy_note}｜{dec_note}" if strategy_note else dec_note
            titles = list(titles) + list(verify_titles)
        # 投入平衡：若用户超额投入(overall>1.3)，追加"引导对方投入"类任务
        inv_note, inv_titles = self._compute_investment_note(person_id)
        if inv_note:
            strategy_note = f"{strategy_note}｜{inv_note}" if strategy_note else inv_note
            titles = list(titles) + list(inv_titles)
        # 情绪稳定应对：若对方情绪爆发型冲突/高波动，追加"应对情绪爆发"类任务
        emo_note, emo_titles = self._compute_emotion_regulation_note(person_id)
        if emo_note:
            strategy_note = f"{strategy_note}｜{emo_note}" if strategy_note else emo_note
            titles = list(titles) + list(emo_titles)
        # 精神操控防御：若 pua_score/暗黑三联征达标，追加 NPD 防御任务（区别于情绪爆发应对）
        mp_note, mp_titles = self._compute_manipulation_defense_note(person_id)
        if mp_note:
            strategy_note = f"{strategy_note}｜{mp_note}" if strategy_note else mp_note
            titles = list(titles) + list(mp_titles)
        # 人格倾向专属任务：自动识别主导人格倾向，追加引导任务（v4 新增）
        ps_note, ps_titles = self._compute_personality_tasks(person_id)
        if ps_note:
            strategy_note = f"{strategy_note}｜{ps_note}" if strategy_note else ps_note
            titles = list(titles) + list(ps_titles)
        # 心理学定律：按画像自动匹配定律并生成对应任务（v2.1 新增）
        law_note, law_titles = self._compute_psychology_law_tasks(person_id, stage)
        if law_note:
            strategy_note = f"{strategy_note}｜{law_note}" if strategy_note else law_note
            titles = list(titles) + list(law_titles)
        # 已读不回/不回复场景：按 5 种原因生成专属任务（v5 新增）
        nr_note, nr_titles = self._compute_no_reply_tasks(person_id)
        if nr_note:
            strategy_note = f"{strategy_note}｜{nr_note}" if strategy_note else nr_note
            titles = list(titles) + list(nr_titles)
        created: list[dict] = []
        # R3.3-78：先读当前关系路线，再决定任务奖励锚点（反制线→忠诚，引导线→好感）。
        # guide 路线下锚点与改动前完全一致；counter 路线下反制类任务走 loyalty 锚点。
        route = self._current_route(person_id)
        for title in titles:
            # 去重：同一人物下已存在同名 pending/active 主线任务则跳过，
            # 避免反复调用 generate_main_tasks 导致任务无限累积（修复断链）。
            dup = self.db.fetch_one(
                "SELECT id FROM tasks WHERE person_id=:pid AND title=:t "
                "AND task_type='main' AND status='pending' LIMIT 1",
                {"pid": person_id, "t": title},
            )
            if dup:
                continue
            # v3.0: 根据用户能力画像调整任务难度（新手话术/经济适配/压力降级等）
            adjusted_title, ability_note = self._adjust_task_by_ability(title)
            full_note = (
                f"{strategy_note}｜{ability_note}" if (strategy_note and ability_note)
                else (ability_note or strategy_note or "")
            )
            # R3.3-78：按路线 + 标题性质分配奖励锚点（向后兼容：guide 下零变化）
            route_tag, aff_rew, loy_rew = self._anchor_for(route, adjusted_title, 5.0)
            task = self._create(
                person_id=person_id,
                title=adjusted_title,
                task_type="main",
                affection_reward=aff_rew,
                strategy_note=full_note,
                route=route_tag,
                loyalty_reward=loy_rew,
            )
            created.append(task)
        logger.info("主线任务生成 person=%s stage=%d 数量=%d", person_id, stage, len(created))
        return created

    # ------------------------------------------------------------------ #
    # 引导闭环：机会捕捉（限时任务）
    # ------------------------------------------------------------------ #
    def generate_limited_task(self, person_id: str, message: str, emotion: dict = None) -> Optional[dict]:
        """检测聊天关键词，实时生成限时任务。

        Args:
            person_id: 人物 ID。
            message: 对方发来的聊天文本。
            emotion: 情绪分析结果（预留，可空）。

        Returns:
            dict | None: 命中时返回创建的完整任务 dict；无匹配返回 None。
        """
        message = message or ""
        emotion = emotion or {}
        for trigger in self.LIMITED_TRIGGERS:
            if re.search(trigger["pattern"], message):
                category = trigger["category"]
                title = trigger["title"]
                hours = int(trigger["hours"])
                deadline = (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
                steps = self._generate_steps(title, category)
                # v3.0: 根据用户能力画像调整限时任务难度
                adjusted_title, ability_note = self._adjust_task_by_ability(title)
                if ability_note:
                    # 限时任务没有 strategy_note，直接拼在 direction 里靠 _create 的 strategy_note 通道
                    pass
                task = self._create(
                    person_id=person_id,
                    title=adjusted_title,
                    task_type=f"limited:{category}",
                    deadline=deadline,
                    steps=steps,
                    affection_reward=3.0,
                    strategy_note=ability_note or None,
                )
                # 取回完整行，便于前端展示 deadline / steps
                full = self.db.fetch_one(
                    "SELECT * FROM tasks WHERE id=:tid", {"tid": task["id"]}
                )
                logger.info("限时任务触发 person=%s category=%s deadline=%s", person_id, category, deadline)
                self._emit("task_updated", {"task": full or task, "event": "new_limited"})
                return full or task
        return None

    # ------------------------------------------------------------------ #
    # 分步拆分
    # ------------------------------------------------------------------ #
    def _generate_steps(self, title: str, category: str) -> list[dict]:
        """为现实/限时任务自动拆分步（带进度条和完成关键词）。

        Args:
            title: 任务标题（未知分类时用于兜底）。
            category: 任务分类（邀约推进/关系维护/冲突修复/需求引导）。

        Returns:
            list[dict]: 每形如 {"name": 步骤名, "complete_keywords": [...]}。
        """
        if category in self.STEP_TEMPLATES:
            # 深拷贝，避免外部修改污染模板
            return [dict(step) for step in self.STEP_TEMPLATES[category]]
        # 未知分类：默认单步
        logger.debug("未知任务分类 %s，使用默认单步", category)
        return [{"name": f"执行：{title}", "complete_keywords": ["完成", "做好了", "搞定"]}]

    # ------------------------------------------------------------------ #
    # 任务创建（引导闭环②：自动附带攻略方向）
    # ------------------------------------------------------------------ #
    def _create(
        self,
        person_id: str,
        title: str,
        task_type: str,
        deadline: str = None,
        steps: list[dict] = None,
        affection_reward: float = 0.0,
        strategy_note: str = None,
        route: str = None,
        loyalty_reward: float = 0.0,
    ) -> dict:
        """创建任务并写入数据库，自动生成攻略方向。

        Args:
            person_id: 人物 ID。
            title: 任务标题。
            task_type: 任务类型（main / limited:xxx / daily / alternative 等）。
            deadline: 截止时间字符串，可空。
            steps: 分步列表，可空。
            affection_reward: 完成奖励好感度，默认 0。
            strategy_note: 因人施策说明（来自 strategy_advisor.analyze），
                会附加到攻略方向末尾；可空（向后兼容）。
            route: R3.3-78 双轨路线标签（"counter"/"guide"），可空；
                空 = 引导线旧行为。反制线任务完成时走 loyalty 奖励入口。
            loyalty_reward: R3.3-78 反制线忠诚度奖励（0-100），默认 0。
                仅当 route=="counter" 且 >0 时，_complete 调 add_loyalty_bonus。

        Returns:
            dict: {"id", "title", "guide_direction"}。
        """
        tid = uuid.uuid4().hex[:8]

        # 攻略方向：防御性调用 guide_engine.generate_task_direction
        direction = self._build_direction(person_id, title)
        # 因人施策：把"基于对方XX特征推荐XX技巧"的说明拼到方向末尾
        if strategy_note:
            direction = f"{direction}｜{strategy_note}"

        steps_json = json.dumps(steps, ensure_ascii=False) if steps else None

        self.db.execute(
            "INSERT INTO tasks "
            "(id, person_id, title, task_type, deadline, steps_json, "
            " current_step, status, affection_reward, guide_direction, "
            " route, loyalty_reward) "
            "VALUES (:id, :person_id, :title, :task_type, :deadline, :steps_json, "
            "        :current_step, :status, :affection_reward, :guide_direction, "
            "        :route, :loyalty_reward)",
            {
                "id": tid,
                "person_id": person_id,
                "title": title,
                "task_type": task_type,
                "deadline": deadline,
                "steps_json": steps_json,
                "current_step": 0,
                "status": "pending",
                "affection_reward": affection_reward,
                "guide_direction": direction,
                "route": route,
                "loyalty_reward": loyalty_reward,
            },
        )
        return {"id": tid, "title": title, "guide_direction": direction,
                "route": route, "loyalty_reward": loyalty_reward}

    def _build_direction(self, person_id: str, title: str) -> str:
        """生成攻略方向：优先调用 guide_engine，缺失则用默认文案。

        Args:
            person_id: 人物 ID。
            title: 任务标题。

        Returns:
            str: 攻略方向文案。
        """
        gen_dir = getattr(self.guide_engine, "generate_task_direction", None)
        if callable(gen_dir):
            try:
                result = gen_dir(person_id, title)
                if result:
                    return str(result)
            except Exception as e:  # noqa: BLE001 - 防御性兜底
                logger.warning("generate_task_direction 调用失败，使用默认方向: %s", e)
        return _DEFAULT_DIRECTION

    # ------------------------------------------------------------------ #
    # R3.3-78 双轨情感指标：反制线走忠诚度、引导线走好感度
    #   反制线(counter)：主指标=忠诚度(loyalty/听话程度)；
    #   引导线(guide)  ：主指标=好感度(affection，既有体系)。
    # 以下全部为消费端、向后兼容扩展：
    #   - AffectionEngine.get_route / add_loyalty_bonus 由并行模块交付，
    #     缺失时防御性回退 guide / 不调用，旧行为零变化；
    #   - services.dual_track.extend_reward 由并行模块交付，缺失时奖励向量原样透传。
    # ------------------------------------------------------------------ #
    def _current_route(self, person_id: str) -> str:
        """读取当前关系路线：counter / guide。

        通过 AffectionEngine.get_route(person_id) 获取（冻结契约，默认 guide）。
        方法缺失/异常时一律回退 "guide"，保证旧库旧引擎行为不变。
        """
        getter = getattr(self.affection, "get_route", None)
        if callable(getter):
            try:
                route = getter(person_id)
                if route in ("counter", "guide"):
                    return route
            except Exception as e:  # noqa: BLE001 - 路线读取失败不阻断任务
                logger.debug("get_route 调用失败，回退 guide: %s", e)
        return "guide"

    def _extend_reward_route(self, reward: dict, route: str) -> dict:
        """消费端按路线扩展奖励向量（不改 planner，只在 task_engine 侧加权）。

        调用 dual_track.extend_reward(dict(reward), route)，返回含 loyalty 维度的
        七维新 dict（counter 路线放大 loyalty、guide 路线以 affection 为主）。
        dual_track 模块/函数缺失时原样返回 dict(reward)，行为与改动前一致。
        """
        try:
            from services import dual_track
            fn = getattr(dual_track, "extend_reward", None)
            if callable(fn):
                return fn(dict(reward or {}), route)
        except Exception as e:  # noqa: BLE001 - 双轨模块未就绪时降级透传
            logger.debug("dual_track.extend_reward 不可用，奖励向量原样透传: %s", e)
        return dict(reward or {})

    # 反制线任务标题特征（操纵防御/边界立规矩/服从强化/反制话术执行类）。
    # 命中即视为"忠诚锚点"任务：完成涨 loyalty、affection 锚点下调。
    _COUNTER_TITLE_HINTS = (
        "煤气灯", "认知边界", "事实清单", "非暴力沟通", "守住边界",
        "贬低", "冷热交替", "操纵", "反制", "服从", "立规矩", "驯服",
    )

    def _is_counter_title(self, title: str) -> bool:
        """判断任务标题是否属于反制线（忠诚锚点）类。"""
        t = title or ""
        return any(h in t for h in self._COUNTER_TITLE_HINTS)

    def _anchor_for(self, route: str, title: str,
                    base_affection: float) -> tuple[Optional[str], float, float]:
        """按当前路线与任务标题计算奖励锚点。

        Returns:
            (route_tag, affection_reward, loyalty_reward)
            - guide 路线：(None, base_affection, 0.0) —— 与改动前完全一致；
            - counter 路线 + 反制类标题：("counter", 小好感, 主忠诚)；
            - counter 路线 + 普通标题：("counter", base_affection, 少量忠诚)，
              两线可并存，主指标唯一。
        """
        if route != "counter":
            return None, float(base_affection), 0.0
        if self._is_counter_title(title):
            # 忠诚锚点：affection 置小（1.0），loyalty 为主（5.0）
            return "counter", 1.0, 5.0
        # counter 路线普通任务：仍带少量忠诚增量，好感锚点保留
        return "counter", float(base_affection), 2.0

    def _category_reward_vec(self, cand: dict, direction: str) -> dict:
        """取候选任务类别的标准 MORL 奖励向量（只读复用 planner 的归一化逻辑）。

        旧回退路径的候选本身不带 expected_reward，这里用 planner 的
        raw_to_task_cand 把类别映射到 OBJECTIVE_KEYS 向量；失败时返回空 dict，
        由 _extend_reward_route 兜底。不改 planner 任何行为。
        """
        try:
            from services.boost_glide_planner import raw_to_task_cand
            return dict(raw_to_task_cand(dict(cand), direction=direction).reward)
        except Exception as e:  # noqa: BLE001
            logger.debug("raw_to_task_cand 取类别奖励向量失败: %s", e)
            return {}

    # ------------------------------------------------------------------ #
    # 因人施策：策略适配矩阵 → 任务攻略方向
    # ------------------------------------------------------------------ #
    def _compute_strategy_note(self, person_id: str, stage: int) -> str:
        """读取对方最新画像，调用 strategy_advisor 生成"因人施策"说明。

        仅当注入了 strategy_advisor 且磁盘上存在该人物画像时返回非空说明；
        否则返回空串（任务方向保持旧版行为，向后兼容）。

        Args:
            person_id: 人物 ID。
            stage: 关系阶段 0-10。

        Returns:
            str: 形如"[因人施策] 对方依恋=回避型…，推荐：给对方空间…，避免：逼问…"；
                 无 advisor / 无画像时返回 ""。
        """
        advisor = self.strategy_advisor
        if advisor is None:
            return ""
        try:
            from services.strategy_advisor import load_persona_from_disk
            persona = load_persona_from_disk(person_id)
            if not persona:
                return ""
            result = advisor.analyze(persona, stage)
            rec = "、".join(result.get("recommended", [])[:5])
            forb = "、".join(result.get("forbidden", [])[:5])
            dims = result.get("_dims", {}).get("raw", {})
            att = dims.get("attachment", "未知")
            return (
                f"[因人施策] 对方依恋={att}，推荐使用：{rec}；避免：{forb}"
            )
        except Exception as e:  # noqa: BLE001 - 策略失败不影响任务创建
            logger.debug("策略适配计算失败 person=%s: %s", person_id, e)
            return ""

    # ------------------------------------------------------------------ #
    # 履约跟踪：按对方承诺兑现率调整推进策略
    # ------------------------------------------------------------------ #
    def _compute_commitment_note(self, person_id: str) -> str:
        """读取对方承诺兑现率，生成推进策略说明。

        仅当注入了 commitment_tracker 时生效；否则返回空串（行为不变）。
        攻略导向原则：兑现率低不等于"这个人不行"，而是需要通过小承诺建立信任闭环，
        引导对方养成兑现习惯，逐步提升信任维度。

        策略映射：
          - high(>=80)   ：大胆推进，可直接邀约、深入沟通；
          - medium(60-80)：正常推进，按当前节奏稳步走；
          - low(40-60)   ：通过小承诺建立信任闭环，先以低成本小事观察对方兑现情况，
                           引导对方养成兑现习惯，逐步提升信任维度；
          - critical(<40) 或有 deceived 记录：保持观察+验证真实意图，通过无利益场景
                           判断对方真实自我，根据验证结果决定推进策略，
                           同时设定清晰底线引导对方尊重关系健康。

        Args:
            person_id: 人物 ID。

        Returns:
            str: 形如"[履约] 对方承诺兑现率90%（high），推荐大胆推进"；未注入时返回 ""。
        """
        tracker = self.commitment_tracker
        if tracker is None:
            return ""
        try:
            rate_info = tracker.get_fulfillment_rate(person_id)
        except Exception as e:  # noqa: BLE001 - 兑现率计算失败不影响任务创建
            logger.debug("兑现率计算失败 person=%s: %s", person_id, e)
            return ""

        rate = rate_info.get("rate", 100.0)
        trust = rate_info.get("trustworthy", "high")
        deceived = rate_info.get("deceived", 0)

        if trust == "critical":
            if deceived > 0:
                strategy = (
                    "存在欺骗/翻脸不认记录，建议保持观察、通过无利益场景验证对方真实意图，"
                    "创造安全氛围引导对方展现真实自我，根据验证结果决定推进策略，"
                    "同时设定清晰底线引导对方尊重关系健康"
                )
            else:
                strategy = (
                    "兑现率偏低，建议通过小承诺建立信任闭环：先通过低成本小事观察对方兑现情况，"
                    "引导对方养成兑现习惯，逐步提升信任维度，根据表现稳步推进关系"
                )
        elif trust == "low":
            strategy = (
                "通过小承诺建立信任闭环：先以低成本小事观察对方是否兑现，"
                "引导对方养成兑现习惯，逐步提升信任维度，视兑现情况再升级"
            )
        elif trust == "medium":
            strategy = "正常推进：按当前关系节奏稳步沟通与邀约，通过日常互动巩固信任"
        else:  # high
            strategy = (
                "对方守信可靠，可大胆推进：直接邀约、深入沟通、"
                "适度安排更深层的相处与规划，稳步提升亲密维度"
            )

        return f"[履约] 对方承诺兑现率{rate}%（{trust}），因此推荐：{strategy}"

    # ------------------------------------------------------------------ #
    # 反伪装审计：高风险时追加验证类任务
    # ------------------------------------------------------------------ #
    def _compute_deception_note(self, person_id: str) -> tuple[str, list[str]]:
        """读取画像中的 deception_risk，高风险(>=60)时返回验证类任务说明与任务标题。

        仅当磁盘画像存在且 data_sufficient=True、risk_score>=60 时介入；
        否则返回 ("", [])（任务方向与既有行为不变，向后兼容）。

        Returns:
            tuple: (note_str, verify_titles)。
                   note_str 形如"[反伪装] 基于伪装风险评估(72分/高)，推荐验证类任务"；
                   verify_titles 为推荐的验证类任务标题列表。
        """
        try:
            from services.strategy_advisor import load_persona_from_disk
            persona = load_persona_from_disk(person_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("读取画像失败(反伪装) person=%s: %s", person_id, e)
            return "", []
        if not persona:
            return "", []

        risk = persona.get("deception_risk") or {}
        if not isinstance(risk, dict) or not risk.get("data_sufficient", False):
            return "", []  # 数据不足，不瞎猜、不介入

        score = risk.get("risk_score", 0)
        level = risk.get("risk_level", "未知")
        try:
            score_num = float(score)
        except (TypeError, ValueError):
            score_num = 0.0
        if score_num < 60:
            return "", []  # 低/中风险不生成验证类任务

        verify_titles = [
            "通过观察对方在无利益场景下的真实表现，判断对方真实意图",
            "通过小范围互动验证对方近期承诺，引导养成兑现习惯",
            "通过持续观察收集真实行为数据，为制定精准推进策略提供依据",
        ]
        note = (
            f"[反伪装] 基于伪装风险评估（{score}分/{level}），推荐验证类任务："
            f"通过观察对方在无利益场景下的真实表现，判断对方真实意图，"
            f"创造安全氛围引导对方展现真实自我，根据验证结果制定下一步推进策略"
        )
        return note, verify_titles

    # ------------------------------------------------------------------ #
    # 投入平衡：用户超额投入时追加"引导对方投入"类任务
    # ------------------------------------------------------------------ #
    def _compute_investment_note(self, person_id: str) -> tuple[str, list[str]]:
        """读取画像 investment_balance，用户超额投入(overall>1.3)时返回引导投入任务。

        攻略导向：不是不给对方付出，而是不能单方面超额付出。
        最终目的是引导双向奔赴——创造机会让对方自然地表达关心和投入，
        形成互相滋养的健康关系，而不是单方面付出。

        任务描述统一使用攻略导向格式："通过X，引导对方Y，最终提升Z维度"。

        触发条件：
          - 画像 investment_balance.overall_ratio > 1.3（用户超额投入）。

        Returns:
            tuple: (note_str, task_titles)。未超额投入时返回 ("", [])。
        """
        try:
            from services.strategy_advisor import load_persona_from_disk
            persona = load_persona_from_disk(person_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("读取画像失败(投入平衡) person=%s: %s", person_id, e)
            return "", []
        if not persona:
            return "", []

        inv = persona.get("investment_balance")
        if not isinstance(inv, dict):
            return "", []

        status = str(inv.get("status", "")).lower()
        try:
            overall = float(inv.get("overall_ratio", 1.0))
        except (TypeError, ValueError):
            overall = 1.0

        # 仅当用户超额投入时才生成引导投入类任务（平衡/对方超额都不生成）
        if not (status == "user_overinvest" or overall > 1.3):
            return "", []

        task_titles = [
            # 1. 请对方帮小忙任务（创造双向关心的机会）
            "通过请对方帮一个小忙（推荐一本书/帮你选个东西/解答一个问题），"
            "创造低压力的机会让对方自然地表达关心，引导对方在双向互动中投入，"
            "同时改善投入平衡",
            # 2. 引导主动约会任务
            "通过下次聊天时自然表达你有空的时间窗口、但不主动替对方安排，"
            "把邀约的选择权交给对方，让对方在舒适节奏中主动发起一次约会，"
            "逐步形成双向奔赴",
            # 3. 节奏观察任务（约束④：回撤≠降频——频率稳定，只降情绪浓度）
            "通过保持联系频率稳定、只把每次互动的情绪浓度降到无压力水平，"
            "观察对方在低压下的自然回应，理解对方的投入意愿，"
            "据此把关系调整到双向平衡",
            # 4. 对等回报任务
            "通过在对方为你做一件事后适当回报但不超额，"
            "引导双方保持投入平衡，形成双向奔赴",
        ]
        note = (
            f"[投入平衡] 当前综合投入比{overall}（用户超额投入），"
            "推荐引导双向投入类任务：创造机会让对方自然地表达关心和投入，"
            "形成互相滋养的双向奔赴。"
        )
        return note, task_titles

    # ------------------------------------------------------------------ #
    # 情绪稳定应对：情绪爆发型冲突时追加应对任务
    # ------------------------------------------------------------------ #
    def _compute_emotion_regulation_note(self, person_id: str) -> tuple[str, list[str]]:
        """读取画像中的冲突风格和情绪模式，情绪爆发型/高波动时返回应对类任务。

        触发条件（满足其一）：
          - conflict_style.primary_key ∈ (emotional_outburst, destructive)
          - emotion_pattern.volatility == "高"
          - attachment.primary_type == "恐惧型"（BPD典型，推拉模式）

        攻略导向：不是被对方情绪带走，而是保持稳定、温和地设定边界，
        理解情绪爆发背后的恐惧或创伤，引导对方学习更健康的情绪调节方式。
        一致性比热情更重要——对情绪不稳的人来说，稳定本身就是最大的安全感。

        Returns:
            tuple: (note_str, task_titles)。无触发条件时返回 ("", [])。
        """
        try:
            from services.strategy_advisor import load_persona_from_disk
            persona = load_persona_from_disk(person_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("读取画像失败(情绪应对) person=%s: %s", person_id, e)
            return "", []
        if not persona:
            return "", []

        conflict = persona.get("conflict_style") or {}
        emotion = persona.get("emotion_pattern") or {}
        attachment = persona.get("attachment") or {}

        conflict_key = conflict.get("primary_key", "")
        emo_volatility = emotion.get("volatility", "低")
        att_type = attachment.get("primary_type", "")

        # 判断是否需要情绪稳定应对
        needs_emo_reg = (
            conflict_key in ("emotional_outburst", "destructive")
            or emo_volatility == "高"
            or att_type == "恐惧型"
        )
        if not needs_emo_reg:
            return "", []

        task_titles = [
            # 1. 保持情绪稳定任务
            "通过在对方情绪爆发时保持冷静不被带跑，"
            "练习做关系中的稳定锚点，理解情绪爆发背后的恐惧或创伤，"
            "等对方平静后一起回顾触发点，最终提升关系稳定性",
            # 2. 设定清晰边界任务
            "通过在对方情绪平稳时温和地告诉对方哪些行为让你受伤（如自伤威胁/言语攻击），"
            "设定清晰但温和的边界，既不冷漠也不失去认知立场，"
            "引导对方学会用健康方式表达情绪",
            # 3. 非暴力沟通任务
            "通过练习用'我感受'代替'你总是/你从不'的表达方式，"
            "在对方情绪稍平稳时用非暴力沟通谈问题，"
            "避免人身攻击和翻旧账，提升冲突修复效率",
        ]
        note = (
            f"[情绪稳定应对] 对方冲突风格={conflict.get('primary','')}，"
            f"情绪波动={emo_volatility}，依恋类型={att_type}。"
            "理解情绪爆发背后往往有恐惧或创伤根源。推荐情绪稳定类任务：保持情绪稳定做关系锚点、"
            "温和设定边界、练习非暴力沟通。一致性比热情更重要——对情绪不稳的人，稳定本身就是安全感"
        )
        return note, task_titles

    # ------------------------------------------------------------------ #
    # 精神操控防御：pua/煤气灯/贬低 时追加 NPD 应对任务（v3 新增）
    # ------------------------------------------------------------------ #
    def _compute_manipulation_defense_note(self, person_id: str) -> tuple[str, list[str]]:
        """检测到精神操控/自恋倾向时，追加 NPD 特有应对任务。

        触发条件（基于画像自动推导，不硬编码标签）：
          - risk_warning.pua_score >= 5
          - 或 dark_triad_tendencies 自恋/操纵 命中
        与「情绪爆发应对任务」的区别：
          情绪爆发任务 = 保持冷静做锚点（应对 BPD 式崩溃）；
          操控防御任务 = 识别煤气灯/守住认知边界/理解防御机制根源，引导健康沟通（应对 NPD 式模式）。
        """
        try:
            from services.strategy_advisor import load_persona_from_disk
            persona = load_persona_from_disk(person_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("读取画像失败(操控防御) person=%s: %s", person_id, e)
            return "", []
        if not persona:
            return "", []

        rw = persona.get("risk_warning") or {}
        try:
            pua = float(rw.get("pua_score", 0) or 0)
        except (TypeError, ValueError):
            pua = 0.0
        psych = persona.get("psychology") or {}
        dark = psych.get("dark_triad_tendencies") or {}
        narc = dark.get("自恋", dark.get("narcissism", 0))
        manip = dark.get("操纵", dark.get("manipulation", 0))

        triggered = pua >= 5 or (narc >= 2 and manip >= 1)
        if not triggered:
            return "", []

        task_titles = [
            "通过记录对方每次贬低/煤气灯/冷热交替的具体话术和时间，"
            "建立一份事实清单，理解这些行为往往源于对方童年形成的防御机制，"
            "用事实而不是情绪回应，最终看清对方的行为规律而不被带偏",
            "通过在对方说'是你想多了''我没说过'时不立即自我怀疑，"
            "保留聊天记录作为事实锚点，守住自己的认知，"
            "最终不再被煤气灯效应扭曲对现实的判断",
            "通过在对方情绪平稳时用非暴力沟通明确告诉对方哪些互动方式让你受伤，"
            "温和而稳定地守住边界，理解这是在引导对方建立更健康的相处方式，"
            "最终在关系中重建自我价值感",
        ]
        note = (
            f"[精神操控防御] pua_score={pua:.0f}（自恋{narc}/操纵{manip}）。"
            "理解这些贬低-煤气灯-冷热交替模式往往源于对方童年创伤形成的防御机制（NPD倾向），"
            "不是你做错了。推荐任务：记录事实清单、识别煤气灯守住自己的认知边界、"
            "在对方平静时用非暴力沟通表达感受并引导健康互动。"
            "对方的道歉多为快速平息冲突而非真正反思，不要用过度付出去'拯救'对方。"
        )
        return note, task_titles

    # ------------------------------------------------------------------ #
    # 人格倾向专属任务：按识别出的人格倾向生成引导任务（v4 新增）
    # ------------------------------------------------------------------ #
    def _compute_personality_tasks(self, person_id: str) -> tuple[str, list[str]]:
        """根据画像自动识别人格倾向，生成该倾向的专属引导任务。

        识别逻辑在 services/personality_strategy.py（基于特征加权打分，不硬编码标签）。
        与既有情绪稳定任务（BPD）/ 操控防御任务（NPD）的关系：
          - 本方法是 14 种人格的统一任务入口；
          - 无明显人格倾向时返回 ("", [])，不影响主线任务；
          - 任务为修复引导导向：引导更健康的互动，不给对方贴标签。
        """
        try:
            from services.strategy_advisor import load_persona_from_disk
            persona = load_persona_from_disk(person_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("读取画像失败(人格任务) person=%s: %s", person_id, e)
            return "", []
        if not persona:
            return "", []
        try:
            return personality_strategy.build_tasks(persona)
        except Exception as e:  # noqa: BLE001 - 人格任务失败不影响主线
            logger.debug("人格专属任务生成失败 person=%s: %s", person_id, e)
            return "", []

    # ------------------------------------------------------------------ #
    # 心理学定律：按画像生成基于定律的任务（v2.1 新增）
    # ------------------------------------------------------------------ #
    def _compute_psychology_law_tasks(self, person_id: str, stage: int) -> tuple[str, list[str]]:
        """根据当前画像+关系阶段，匹配心理学定律并生成对应任务。

        设计原则（修复引导导向）：
          - 定律是理解关系机制+引导健康互动的工具，不是算计对方的手段；
          - 间歇性强化等易被误用为操控的定律，任务一律是"识别对方是否在用这个机制
            影响你 / 自己保持健康节奏"，不输出"你去操控对方"；
          - 所有任务标题已注明"基于XX定律"，便于用户理解背后原理。

        Returns:
            tuple: (note_str, task_titles)。无画像/未命中时返回 ("", [])。
        """
        try:
            from services.strategy_advisor import load_persona_from_disk
            persona = load_persona_from_disk(person_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("读取画像失败(心理学定律) person=%s: %s", person_id, e)
            return "", []
        if not persona:
            return "", []
        try:
            return psychology_laws.build_law_tasks(persona, stage)
        except Exception as e:  # noqa: BLE001 - 定律任务失败不影响主线任务
            logger.debug("心理学定律任务生成失败 person=%s: %s", person_id, e)
            return "", []

    # ------------------------------------------------------------------ #
    # 已读不回/不回复场景：按 5 种原因生成专属任务与时间线（v5 新增）
    # ------------------------------------------------------------------ #
    def _compute_no_reply_tasks(self, person_id: str) -> tuple[str, list[str]]:
        """检测到不回复场景时，按原因（5 种）生成专属任务与时间线。

        上游流水线运行 NoReplyDetector，把结果写进 persona["no_reply"]：
            {is_no_reply, severity, reason, reason_label, waiting_hours,
             hours_since_partner_reply}
        本方法据此调用 NoReplyDetector.get_tasks 取该原因的专属任务（含时间线）。

        5 种原因任务两两不同：
          - 真忙：专注自己的事等对方主动，不催促；
          - 没兴趣：降到每周1-2次，观察2周；
          - 惩罚性沉默：不追着道歉，等对方先开口，3-7天；
          - 回避型：发低压关心，给3-7天空间；
          - NPD冷暴力：记录模式、不道歉求和、2周后评估。

        Returns:
            tuple: (note_str, task_titles)。未检测到不回复场景返回 ("", [])。
        """
        try:
            from services.strategy_advisor import load_persona_from_disk
            persona = load_persona_from_disk(person_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("读取画像失败(不回复任务) person=%s: %s", person_id, e)
            return "", []
        if not persona:
            return "", []

        nr = persona.get("no_reply")
        if not isinstance(nr, dict) or not nr.get("is_no_reply", False):
            return "", []

        reason = str(nr.get("reason", ""))
        severity = str(nr.get("severity", "中度"))
        if not reason:
            return "", []

        try:
            from services.no_reply_detector import NoReplyDetector
            waiting_h = nr.get("hours_since_partner_reply") or nr.get("waiting_hours") or 0
            try:
                days_since = max(0, int(float(waiting_h) / 24.0))
            except (TypeError, ValueError):
                days_since = 0
            tasks = NoReplyDetector().get_tasks(reason, severity, days_since)
        except Exception as e:  # noqa: BLE001 - 任务生成失败不影响主线
            logger.debug("不回复任务生成失败 person=%s: %s", person_id, e)
            return "", []

        task_titles = [t.get("title", "") for t in tasks if t.get("title")]
        label = nr.get("reason_label", reason)
        note = (
            f"[不回复场景] 当前处于不回复状态，原因判断为「{label}」（{severity}）。"
            "按该原因的专属策略行动：" + "；".join(
                f"{t.get('title','')}(时间线:{t.get('timeline','')})" for t in tasks
            )
        )
        return note, task_titles


    # ------------------------------------------------------------------ #
    # v3.0: 用户能力适配 —— 根据画像调整任务标题/方向
    # ------------------------------------------------------------------ #
    def _adjust_task_by_ability(self, title: str) -> tuple[str, str]:
        """根据当前用户能力画像调整任务标题与攻略方向。

        Returns:
            tuple: (adjusted_title, ability_note)。能力模块不可用/无调整时返回
                   (原标题, "")，向后兼容。
        """
        try:
            adjusted = self._user_ability.adjust_task_difficulty(
                _SELF_USER_ID, {"title": title})
        except Exception as e:  # noqa: BLE001 - 能力调整失败不影响任务生成
            logger.debug("用户能力调整失败: %s", e)
            return title, ""
        new_title = str(adjusted.get("title", title))
        notes = adjusted.get("_ability_notes") or []
        # 把新手话术等指南追加到 strategy_note，由 _create 拼进 guide_direction
        direction_extras: list[str] = []
        gd = adjusted.get("guide_direction") or ""
        if "[新手话术]" in gd:
            # 提取 [新手话术] 之后的部分（即话术模板块）
            marker = "[新手话术]"
            idx = gd.find(marker)
            direction_extras.append(gd[idx:])
        note = "｜".join(notes)
        if direction_extras:
            note = f"{note}｜{'｜'.join(direction_extras)}" if note else "｜".join(direction_extras)
        return new_title, note

    # ------------------------------------------------------------------ #
    # 引导闭环⑤：反馈判定
    # ------------------------------------------------------------------ #
    def check_completion(self, person_id: str, message: str, emotion: dict = None) -> None:
        """根据一条新聊天消息，判定任务分步/整体是否完成。

        流程（对每个 pending/in_progress 任务）：
          1. pending → 标记 in_progress；
          2. 若任务带 auto_keywords，命中任一关键词则 _advance_step；
          3. 若任务带 steps_json，当前步的 complete_keywords 命中则 _advance_step；
          4. 命中推进后，若 _is_all_steps_done 则 _complete。

        Args:
            person_id: 人物 ID。
            message: 新聊天文本。
            emotion: 情绪分析结果（预留，可空）。
        """
        message = message or ""
        rows = self.db.fetch_all(
            "SELECT * FROM tasks WHERE person_id=:person_id "
            "AND status IN ('pending','in_progress') ORDER BY created_at",
            {"person_id": person_id},
        )
        for row in rows:
            tid = row["id"]
            # pending -> in_progress
            if row.get("status") == "pending":
                self.db.execute(
                    "UPDATE tasks SET status='in_progress' WHERE id=:tid",
                    {"tid": tid},
                )
                row["status"] = "in_progress"

            matched = False

            # 1) auto_keywords 命中 → 推进（R3.3-81：先过"真实进展"分析门禁）
            if row.get("auto_keywords"):
                try:
                    kws = json.loads(row["auto_keywords"])
                    if isinstance(kws, list) and any(kw in message for kw in kws):
                        if self._advance_if_genuine(tid, message, emotion):
                            matched = True
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    logger.warning("解析 auto_keywords 失败 tid=%s: %s", tid, e)

            # 2) steps_json 当前步关键词命中 → 推进（R3.3-81：同样过分析门禁）
            current = self._get_current_step(tid)
            if current and current.get("complete_keywords"):
                if any(kw in message for kw in current["complete_keywords"]):
                    if self._advance_if_genuine(tid, message, emotion):
                        matched = True

            # 3) 命中推进后，若全部分步完成 → 收尾
            if matched and self._is_all_steps_done(tid):
                task = self.db.fetch_one("SELECT * FROM tasks WHERE id=:tid", {"tid": tid})
                if task:
                    task["status"] = "completed"
                    self._complete(task, "自动完成：目标达成")

    # ------------------------------------------------------------------ #
    # 分步进度内部方法
    # ------------------------------------------------------------------ #
    def _get_current_step(self, task_id: str) -> Optional[dict]:
        """获取任务当前分步（从 steps_json 与 current_step 字段）。

        Args:
            task_id: 任务 ID。

        Returns:
            dict | None: 当前步 dict；无分步或越界返回 None。
        """
        row = self.db.fetch_one(
            "SELECT steps_json, current_step FROM tasks WHERE id=:tid",
            {"tid": task_id},
        )
        if not row or not row.get("steps_json"):
            return None
        try:
            steps = json.loads(row["steps_json"])
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        idx = int(row.get("current_step") or 0)
        if isinstance(steps, list) and 0 <= idx < len(steps):
            step = steps[idx]
            return dict(step) if isinstance(step, dict) else None
        return None

    def _advance_if_genuine(self, task_id: str, message: str,
                            emotion: dict = None) -> bool:
        """R3.3-81：命中完成关键词后，是否真的推进任务进度。

        旧机制：命中关键词就 _advance_step（"做了任务/说了这句就+进度"）。
        新机制：关键词只是线索，还要看分析结论——对方是否真的表现出正向进展
        （感兴趣/配合/情绪回暖）。若推理层判定为敷衍/抵触/无真实进展，则
        **不推进**（避免"随口一说也算完成"）。

        未注入 reasoner（self.reasoner is None）时保持旧行为：直接推进，返回 True。
        """
        if self.reasoner is None:
            self._advance_step(task_id)
            return True
        try:
            verdict = self.reasoner.assess_task_progress(message, emotion)
        except Exception as e:  # noqa: BLE001
            logger.warning("reasoner.assess_task_progress 失败 tid=%s，按旧行为推进: %s",
                           task_id, e)
            self._advance_step(task_id)
            return True
        if verdict.get("is_genuine_progress"):
            self._advance_step(task_id)
            logger.info("任务推进 tid=%s（分析确认真实正向进展: %s）",
                        task_id, verdict.get("summary", ""))
            return True
        logger.info("任务未推进 tid=%s（命中关键词但分析判定无真实进展: %s）",
                    task_id, verdict.get("summary", ""))
        return False

    def _advance_step(self, task_id: str) -> None:
        """把任务推进到下一步（current_step + 1）。

        Args:
            task_id: 任务 ID。
        """
        self.db.execute(
            "UPDATE tasks SET current_step = current_step + 1 WHERE id=:tid",
            {"tid": task_id},
        )
        logger.debug("任务推进 tid=%s", task_id)

    def _is_all_steps_done(self, task_id: str) -> bool:
        """检查任务是否全部分步完成。

        无分步任务（steps_json 为空）视为已完成；
        有分步任务则 current_step >= 分步总数 即完成。

        Args:
            task_id: 任务 ID。

        Returns:
            bool: 是否已全部完成。
        """
        row = self.db.fetch_one(
            "SELECT steps_json, current_step FROM tasks WHERE id=:tid",
            {"tid": task_id},
        )
        if not row:
            return True
        if not row.get("steps_json"):
            return True
        try:
            steps = json.loads(row["steps_json"])
        except (json.JSONDecodeError, TypeError, ValueError):
            return True
        if not isinstance(steps, list) or len(steps) == 0:
            return True
        idx = int(row.get("current_step") or 0)
        return idx >= len(steps)

    # ------------------------------------------------------------------ #
    # 引导闭环⑥：完成 → 奖励 → 接棒下一步
    # ------------------------------------------------------------------ #
    def _complete(self, task: dict, reason: str) -> None:
        """完成任务：更新状态 → 发放奖励 → emit → 接棒下一步任务。

        Args:
            task: 任务 dict（需含 id / person_id / affection_reward）。
            reason: 完成原因（日志用）。
        """
        tid = task["id"]
        person_id = task.get("person_id")
        self.db.execute(
            "UPDATE tasks SET status='completed', completed_at=datetime('now') WHERE id=:tid",
            {"tid": tid},
        )

        reward = float(task.get("affection_reward") or 0)
        if reward > 0 and person_id is not None:
            try:
                self.affection.add_bonus(person_id, reward)
            except Exception as e:  # noqa: BLE001
                logger.warning("好感度奖励发放失败 tid=%s: %s", tid, e)

        # R3.3-78 反制线奖励分发：任务带 loyalty_reward>0 且 route=="counter"
        # → 调 AffectionEngine.add_loyalty_bonus（忠诚/听话程度）。
        # 与好感奖励可并存（两线可并存、主指标唯一）；契约方法缺失时静默不发。
        loyalty_reward = float(task.get("loyalty_reward") or 0)
        route_tag = task.get("route")
        if loyalty_reward > 0 and route_tag == "counter" and person_id is not None:
            add_loyalty = getattr(self.affection, "add_loyalty_bonus", None)
            if callable(add_loyalty):
                try:
                    add_loyalty(person_id, loyalty_reward,
                                reason=str(task.get("title", "任务奖励")))
                except Exception as e:  # noqa: BLE001
                    logger.warning("忠诚度奖励发放失败 tid=%s: %s", tid, e)

        # v3.0: 任务完成 → 更新用户能力画像（完成率高→社交/执行涨分）
        try:
            self._user_ability.update_from_task_completion(_SELF_USER_ID, {
                "title": task.get("title", ""),
                "success": True,
                "task_type": task.get("task_type", ""),
            })
        except Exception as e:  # noqa: BLE001
            logger.debug("能力画像更新失败 tid=%s: %s", tid, e)

        task["status"] = "completed"
        logger.info("任务完成 tid=%s reason=%s reward=%.1f", tid, reason, reward)
        self._emit("task_updated", {"task": task, "event": "completed"})

        # 节奏不中断：自动生成下一步
        if person_id is not None:
            self._advance_loop(person_id)

    def _advance_loop(self, person_id: str) -> None:
        """引导闭环第⑥步：完成后生成下一步任务。

        规则：
          - 好感度总分 >= 70 且阶段 < 10 → 创建“推进到下一阶段”主线任务；
          - 否则 → 创建“今天主动聊一次天”日常任务。

        Args:
            person_id: 人物 ID。
        """
        try:
            scores = self.affection.get_current(person_id)
            total = self.affection.get_total(scores)
            stage = self.affection.get_stage(total)
        except Exception as e:  # noqa: BLE001
            logger.warning("读取好感度失败，回退日常任务 person=%s: %s", person_id, e)
            total, stage = 50.0, 0

        if total >= 70 and stage < 10:
            self._create(
                person_id=person_id,
                title="推进到下一阶段",
                task_type="main",
                affection_reward=8.0,
            )
        else:
            self._create(
                person_id=person_id,
                title="今天主动聊一次天",
                task_type="daily",
                affection_reward=2.0,
            )

    # ------------------------------------------------------------------ #
    # 失败 → 替代任务（引导闭环不中断）
    # ------------------------------------------------------------------ #
    def handle_failed(self, task: dict) -> None:
        """任务失败：标记 failed → 生成替代任务。

        Args:
            task: 失败的任务 dict（需含 id / person_id / title）。
        """
        tid = task["id"]
        person_id = task.get("person_id")
        self.db.execute(
            "UPDATE tasks SET status='failed' WHERE id=:tid",
            {"tid": tid},
        )
        alt = self._create(
            person_id=person_id or "",
            title=f"[替代]{task.get('title', '任务')}",
            task_type="alternative",
            affection_reward=1.0,
        )
        logger.info("任务失败并替代 tid=%s alt=%s", tid, alt.get("id"))
        self._emit("task_updated", {"task": alt, "event": "alternative"})

    # ------------------------------------------------------------------ #
    # v3.0: 用户能力评估公开接口
    # ------------------------------------------------------------------ #
    def get_emotional_ladder(self) -> dict:
        """返回情感阶梯表 L1-L5 + 当前用户推荐起始级别。

        Returns:
            dict: {ladder, current_level, recommended_start_level, profile}。
        """
        try:
            return self._user_ability.get_emotional_ladder(_SELF_USER_ID)
        except Exception as e:  # noqa: BLE001
            logger.warning("读取情感阶梯失败: %s", e)
            return {"ladder": [], "current_level": "L1",
                    "recommended_start_level": "basic", "profile": {}}

    def set_user_ability_level(self, level: str) -> dict:
        """用户手动设定任务级别（basic/intermediate/advanced）。"""
        try:
            return self._user_ability.set_user_ability_level(_SELF_USER_ID, level)
        except Exception as e:  # noqa: BLE001
            logger.warning("设定能力级别失败: %s", e)
            return {}

    def get_user_ability_profile(self) -> dict:
        """读取当前用户能力画像（调试/展示用）。"""
        try:
            return self._user_ability.get_profile(_SELF_USER_ID)
        except Exception as e:  # noqa: BLE001
            logger.warning("读取能力画像失败: %s", e)
            return {}

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def get_active_tasks(self, person_id: str) -> list[dict]:
        """获取当前进行中的任务列表（pending + in_progress）。

        Args:
            person_id: 人物 ID。

        Returns:
            list[dict]: 任务行列表，按创建时间升序。
        """
        return self.db.fetch_all(
            "SELECT * FROM tasks WHERE person_id=:person_id "
            "AND status IN ('pending','in_progress') ORDER BY created_at",
            {"person_id": person_id},
        )

    def get_task_progress(self, task_id: str) -> dict:
        """获取任务进度。

        Args:
            task_id: 任务 ID。

        Returns:
            dict: {"current_step", "total_steps", "percent", "current_step_name"}。
        """
        row = self.db.fetch_one(
            "SELECT current_step, steps_json FROM tasks WHERE id=:tid",
            {"tid": task_id},
        )
        if not row:
            return {"current_step": 0, "total_steps": 0, "percent": 0.0, "current_step_name": ""}

        idx = int(row.get("current_step") or 0)
        steps: list = []
        if row.get("steps_json"):
            try:
                parsed = json.loads(row["steps_json"])
                if isinstance(parsed, list):
                    steps = parsed
            except (json.JSONDecodeError, TypeError, ValueError):
                steps = []

        total = len(steps)
        if total == 0:
            percent = 100.0 if idx > 0 else 0.0
            current_name = ""
        else:
            percent = round(min(idx, total) / total * 100, 1)
            if 0 <= idx < total:
                step = steps[idx]
                current_name = step.get("name", "") if isinstance(step, dict) else ""
            else:
                current_name = "已完成"

        return {
            "current_step": idx,
            "total_steps": total,
            "percent": percent,
            "current_step_name": current_name,
        }

    # ------------------------------------------------------------------ #
    # v3.0 补链：自定义任务持久化 / 爽约区分 / 状态联动调速
    # ------------------------------------------------------------------ #
    def add_custom_task(self, person_id: str, title: str, source: str = "orchestrator",
                        affection_reward: float = 3.0) -> Optional[dict]:
        """持久化编排器/反制教练生成的自定义任务（此前只在结果中展示，不入任务系统）。

        按标题去重：同一活跃任务已存在则不重复创建。
        """
        if not person_id or not title:
            return None
        try:
            existing = self.db.fetch_all(
                "SELECT title FROM tasks WHERE person_id=:pid AND status IN ('pending','in_progress')",
                {"pid": person_id},
            )
            if any(r["title"] == title for r in existing):
                return None
            return self._create(person_id, title, task_type=f"custom:{source}",
                               affection_reward=affection_reward)
        except Exception as e:  # noqa: BLE001
            logger.warning("add_custom_task 失败: %s", e)
            return None

    # 爽约/拖延：有事耽误（中性理由）vs 不想兑现（无理由/反复）
    _SINCERELY_DELAY = ("加班", "出差", "医院", "家里", "临时有事", "身体不舒服",
                        "堵车", "明天补", "下次", "改天")
    _EXCUSELESS_COLD = ("再说吧", "不一定", "到时候看", "看心情", "再说")

    def check_appointment_break(self, person_id: str, message: str) -> Optional[dict]:
        """检测对方对已约事项的爽约/拖延，区分「有事耽误」与「不想兑现」。

        有事耽误 → 生成「改约/后续跟进」任务（延期，不惩罚）；
        不想兑现 → 触发承诺一致性，生成「小承诺重建」任务。
        Returns: 判定 dict 或 None（无爽约信号）。
        """
        if not person_id or not message:
            return None
        msg = message
        is_break = any(k in msg for k in ("去不了", "来不了", "爽约", "下次再说",
                                          "临时", "抱歉今天", "实在不行"))
        if not is_break:
            return None

        sincerely = any(k in msg for k in self._SINCERELY_DELAY) and any(
            k in msg for k in ("改天", "下次", "明天", "补", "抱歉", "不好意思"))
        cold = any(k in msg for k in self._EXCUSELESS_COLD)

        if sincerely and not cold:
            self.add_custom_task(person_id, "主动温和改约一次（给台阶，不追问）",
                                 source="appointment_delay", affection_reward=2.0)
            return {"type": "delay", "judgment": "有事耽误（有具体理由+主动补约）",
                    "action": "生成改约跟进任务，不惩罚"}
        # 无理由/反复 → 不想兑现
        self.add_custom_task(person_id, "用一件低成本小事测试承诺一致性（对方是否兑现）",
                             source="appointment_cold", affection_reward=3.0)
        return {"type": "cold", "judgment": "疑似不想兑现（无具体理由/敷衍）",
                "action": "生成小承诺重建任务，观察是否兑现"}

    def adjust_tasks_for_state(self, person_id: str, state: dict) -> Optional[dict]:
        """根据当前实时状态动态调速：对方回避激活/愤怒时，暂停「推进型」任务，
        改发「给空间/降温」任务；对方热情/开心时，恢复推进。

        state: 来自 manipulation_coach.assess_current_state 的
               {emotion, attachment_activation, trend}。
        """
        if not person_id or not state:
            return None
        att = state.get("attachment_activation", "")
        emotion = state.get("emotion", "")
        # 推进型任务关键词
        PUSH = ("约", "表白", "推进", "确认关系", "独处", "见面", "升温", "升级")

        if "回避" in att or emotion == "愤怒":
            # 暂停推进型活跃任务（软暂停：标记为 paused，不删除）
            try:
                rows = self.db.fetch_all(
                    "SELECT id, title FROM tasks WHERE person_id=:pid "
                    "AND status='in_progress'", {"pid": person_id})
                paused = 0
                for r in rows:
                    if any(k in (r["title"] or "") for k in PUSH):
                        self.db.execute(
                            "UPDATE tasks SET status='paused' WHERE id=:tid",
                            {"tid": r["id"]})
                        paused += 1
                if paused:
                    self.add_custom_task(person_id, "轻量接触给空间（不追问、不逼表态）",
                                         source="state_space", affection_reward=1.0)
                    return {"adjusted": "downshift", "paused_push_tasks": paused,
                            "reason": f"对方当前{att}/{emotion}，推进任务会触发回避/激怒"}
            except Exception as e:  # noqa: BLE001
                logger.warning("adjust_tasks_for_state 失败: %s", e)
        return None

    # ------------------------------------------------------------------ #
    # P-07 拒绝信号自动降级（红线）
    # ------------------------------------------------------------------ #
    def apply_refusal_downgrade(self, person_id: str, decision: dict) -> Optional[dict]:
        """P-07：对方发出拒绝信号时，把【邀约/表白/升温/确认关系】等推进类任务软暂停。

        协调手段中立：
          - 只软暂停 refusal_downgrade.is_push_task 判定为"推进类"的任务；
          - 反制 / 给空间 / 设边界 / 引导对等投入等中性任务【不暂停、不受影响】；
          - 软暂停 = status='paused'（不删除），对方回温后可恢复；
          - 绝不触发自动发送（发送永远手动）。

        Args:
            person_id: 人物 ID。
            decision: refusal_downgrade.detect_refusal 的返回 dict。

        Returns:
            dict | None: {adjusted, paused_push_tasks, reason, level}；未触发返回 None。
        """
        if not person_id or not decision or not decision.get("triggered"):
            return None
        try:
            rows = self.db.fetch_all(
                "SELECT id, title FROM tasks WHERE person_id=:pid "
                "AND status IN ('pending','in_progress')", {"pid": person_id})
            paused = 0
            for r in rows:
                if _p07_refusal.is_push_task(r["title"] or ""):
                    self.db.execute(
                        "UPDATE tasks SET status='paused' WHERE id=:tid",
                        {"tid": r["id"]})
                    paused += 1
            reason = "；".join(decision.get("reasons") or [])
            if paused:
                # 降级后补一条"给空间"维护任务（不推进）
                self.add_custom_task(
                    person_id, "低压力轻量接触·给空间（不追问、不逼表态、暂停推进）",
                    source="p07_refusal", affection_reward=1.0)
            self._emit("task_updated", {"event": "p07_refusal_downgrade",
                                        "paused_push_tasks": paused,
                                        "reason": reason})
            return {"adjusted": "hold_boundary", "paused_push_tasks": paused,
                    "reason": reason, "level": decision.get("level")}
        except Exception as e:  # noqa: BLE001
            logger.warning("apply_refusal_downgrade 失败: %s", e)
            return None

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _emit(self, signal_name: str, payload: dict) -> None:
        """防御性 emit 信号（signals 为 None 或信号不存在时静默跳过）。

        Args:
            signal_name: AppSignals 上的信号名。
            payload: 发给信号的 dict。
        """
        if self.signals is None:
            return
        signal = getattr(self.signals, signal_name, None)
        if signal is None:
            return
        try:
            signal.emit(payload)
        except Exception as e:  # noqa: BLE001
            logger.warning("信号 emit 失败 signal=%s: %s", signal_name, e)

    # ================================================================== #
    # v3.1 新增能力①：对方状态自适应难度
    #   根据对方当天状态调整任务难度：
    #     主动热情 → 难度-1（简单任务即可）
    #     正常     → 难度不变
    #     冷淡     → 难度+1（需要更用心）
    #     情绪差   → 难度+2（安慰类任务，不要推进类）
    # ================================================================== #
    _DIFFICULTY_ORDER = ["简单", "普通", "困难", "地狱"]

    @classmethod
    def adjust_difficulty_by_mood(cls, task: dict, other_person_state: dict) -> dict:
        """根据对方状态调整任务难度（原地返回 task，追加 mood_adjustment 说明）。

        Args:
            task: 任务 dict，应含 difficulty（简单/普通/困难/地狱）。
            other_person_state: 对方状态 dict，可用键：
                - proactivity: "热情" / "正常" / "冷淡" / "情绪差"
                  （或 reply_speed / tone 等，内部做兜底识别）

        Returns:
            dict: 传入的 task（已改写 difficulty，并追加 mood_adjustment 字段）。
        """
        if not isinstance(task, dict):
            return task
        state = other_person_state or {}
        pro = str(state.get("proactivity", "") or "").strip()
        # 兜底识别：回复快/主动找话题 → 热情；短句/敷衍 → 冷淡
        if not pro:
            if state.get("reply_speed") in ("快", "秒回") or state.get("starts_topic"):
                pro = "热情"
            elif state.get("reply_speed") in ("慢", "半天") or state.get("short_replies"):
                pro = "冷淡"
            else:
                pro = "正常"

        shift = {"热情": -1, "正常": 0, "冷淡": 1, "情绪差": 2}.get(pro, 0)

        cur = str(task.get("difficulty", "普通"))
        if cur not in cls._DIFFICULTY_ORDER:
            cur = "普通"
        idx = cls._DIFFICULTY_ORDER.index(cur)
        new_idx = max(0, min(len(cls._DIFFICULTY_ORDER) - 1, idx + shift))
        new_diff = cls._DIFFICULTY_ORDER[new_idx]

        task["difficulty"] = new_diff
        task["mood_adjustment"] = {
            "state": pro,
            "shift": shift,
            "note": {
                "热情": "对方主动热情，简单任务维系即可，别过度用力",
                "正常": "对方状态平稳，按原难度推进",
                "冷淡": "对方冷淡/敷衍，需要更用心、更有质感的任务",
                "情绪差": "对方情绪差（刚吵完/心情不好），改安慰陪伴类任务，不要推进",
            }.get(pro, ""),
        }
        return task

    # ================================================================== #
    # v3.1 新增能力②：重复惩罚
    #   重复话术/重复地点降效果。
    #     同话术模板第2次 ×0.7；第3次及以上 ×0.4
    #     同约会地点第2次 ×0.8；3次以上建议换地方
    # ================================================================== #
    def _apply_repeat_penalty(self, task: dict, history: list[dict]) -> dict:
        """根据任务历史对当前任务施加重复惩罚（改写 effectiveness 并附说明）。

        Args:
            task: 任务 dict，可用键 template（话术模板名）/ location（地点）。
            history: 历史任务列表（过去的任务 dict，含 template/location/title）。

        Returns:
            dict: 传入的 task，追加 repeat_penalty 字段。
        """
        if not isinstance(task, dict):
            return task
        template = task.get("template") or task.get("title") or ""
        location = task.get("location") or ""

        # 统计同模板出现次数（不含本次）
        tpl_count = 0
        loc_count = 0
        for h in (history or []):
            if not isinstance(h, dict):
                continue
            if template and (h.get("template") or h.get("title")) == template:
                tpl_count += 1
            if location and h.get("location") == location:
                loc_count += 1

        penalty = 1.0
        notes: list[str] = []
        if tpl_count == 1:
            penalty *= 0.7
            notes.append("同一句话术模板第2次使用，效果×0.7，建议换个说法")
        elif tpl_count >= 2:
            penalty *= 0.4
            notes.append("同一句话术模板第3次及以上，效果×0.4，必须换话术")

        if loc_count == 1:
            penalty *= 0.8
            notes.append("同一个约会地点第2次，效果×0.8，可换个新鲜点的地方")
        elif loc_count >= 2:
            notes.append("同一约会地点已用3次以上，强烈建议换地方，避免乏味")

        task["effectiveness"] = round(penalty, 2)
        task["repeat_penalty"] = {
            "template_used_times": tpl_count,
            "location_used_times": loc_count,
            "multiplier": round(penalty, 2),
            "notes": notes,
        }
        return task

    def _fetch_task_history(self, person_id: str, limit: int = 20) -> list[dict]:
        """读取该人物最近的任务历史（用于重复惩罚）。"""
        if not self.db:
            return []
        try:
            rows = self.db.fetch_all(
                "SELECT title, task_type, status, created_at FROM tasks "
                "WHERE person_id=:pid ORDER BY created_at DESC LIMIT :lim",
                {"pid": person_id, "lim": limit},
            )
            return rows or []
        except Exception as e:  # noqa: BLE001
            logger.warning("读取任务历史失败: %s", e)
            return []

    # ================================================================== #
    # v3.1 新增能力③：错过里程碑提示
    #   限时机会错过不删，标记 missed，下次推荐时提醒。
    #     初次聊天后48h内邀约 → 错过提示
    #     暧昧期后7天内确认关系 → 错过提示
    #     吵架后24h内台阶 → 错过提示
    # ================================================================== #
    # 里程碑定义：(键, 描述, 时限小时, 错过提示语)
    MILESTONE_DEFS: list[dict[str, Any]] = [
        {
            "key": "first_invite_48h",
            "label": "初次聊天后48小时内邀约",
            "hours": 48,
            "missed_note": "这个邀约窗口错过了，下次对方再给信号要抓住，别再拖",
        },
        {
            "key": "confirm_7d",
            "label": "暧昧期后7天内确认关系",
            "hours": 168,
            "missed_note": "暧昧拖太久容易凉，抓紧挑个时机确认关系定位",
        },
        {
            "key": "reconcile_24h",
            "label": "吵架后24小时内给台阶",
            "hours": 24,
            "missed_note": "冷战太久不好，主动给个台阶，别让情绪过夜",
        },
    ]

    def check_missed_milestones(self, person_id: str) -> list[dict]:
        """检查是否有错过的限时里程碑，返回错过清单（不删除、标记 missed）。

        判定依据：
          - first_invite_48h：该人物首条聊天至今超过48h，且从未有邀约类任务/记录。
          - confirm_7d：关系阶段到暧昧(5/6)超过7天，且无确认关系类记录。
          - reconcile_24h：最近一条负面/吵架消息距今超过24h，且无修复记录。

        Returns:
            list[dict]: 每项 {key, label, status:"missed", missed_note, hours}。
        """
        if not self.db:
            return []
        missed: list[dict] = []
        try:
            # 首条聊天时间
            first_row = self.db.fetch_one(
                "SELECT MIN(created_at) AS first_at FROM chat_messages WHERE person_id=:pid",
                {"pid": person_id},
            )
            first_at = self._parse_dt(first_row.get("first_at")) if first_row else None

            # 最近消息时间 + 是否有负面/吵架
            last_rows = self.db.fetch_all(
                "SELECT content, created_at FROM chat_messages WHERE person_id=:pid "
                "ORDER BY created_at DESC LIMIT 50",
                {"pid": person_id},
            ) or []
            now = datetime.now()

            # 1) 初次邀约窗口
            if first_at:
                age_h = (now - first_at).total_seconds() / 3600.0
                if age_h > 48:
                    invited = self.db.fetch_all(
                        "SELECT id FROM tasks WHERE person_id=:pid AND title LIKE :kw "
                        "AND status IN ('pending','in_progress','done')",
                        {"pid": person_id, "kw": "%约%"},
                    )
                    if not invited:
                        m = next(x for x in self.MILESTONE_DEFS if x["key"] == "first_invite_48h")
                        missed.append({"key": m["key"], "label": m["label"],
                                       "status": "missed", "missed_note": m["missed_note"],
                                       "hours": m["hours"]})

            # 2) 吵架后台阶窗口（最近24h内有吵架信号但超24h未修复）
            conflict_kw = ("吵", "生气", "分手", "冷战", "别理", "算了", "烦")
            last_conflict_dt = None
            for r in last_rows:
                c = r.get("content", "") or ""
                if any(k in c for k in conflict_kw):
                    last_conflict_dt = self._parse_dt(r.get("created_at"))
                    break
            if last_conflict_dt:
                since_h = (now - last_conflict_dt).total_seconds() / 3600.0
                if since_h > 24:
                    repaired = self.db.fetch_all(
                        "SELECT id FROM tasks WHERE person_id=:pid AND "
                        "(title LIKE :k1 OR title LIKE :k2)",
                        {"pid": person_id, "k1": "%道歉%", "k2": "%台阶%"},
                    )
                    if not repaired:
                        m = next(x for x in self.MILESTONE_DEFS if x["key"] == "reconcile_24h")
                        missed.append({"key": m["key"], "label": m["label"],
                                       "status": "missed", "missed_note": m["missed_note"],
                                       "hours": m["hours"]})

            # 3) 暧昧期确认关系窗口（读好感阶段；阶段>=6即暧昧/热恋，超过7天无确认记录）
            try:
                total_row = self.db.fetch_one(
                    "SELECT scores_json FROM current_affection WHERE person_id=:pid",
                    {"pid": person_id},
                )
                if total_row:
                    import json as _json
                    scores = _json.loads(total_row.get("scores_json") or "{}")
                    total = sum(float(v) for v in scores.values()) / max(1, len(scores))
                    if total >= 60:
                        confirmed = self.db.fetch_all(
                            "SELECT id FROM tasks WHERE person_id=:pid AND "
                            "(title LIKE :k1 OR title LIKE :k2)",
                            {"pid": person_id, "k1": "%确认%", "k2": "%关系定位%"},
                        )
                        if not confirmed and first_at:
                            age_h = (now - first_at).total_seconds() / 3600.0
                            if age_h > 168:
                                m = next(x for x in self.MILESTONE_DEFS if x["key"] == "confirm_7d")
                                missed.append({"key": m["key"], "label": m["label"],
                                               "status": "missed", "missed_note": m["missed_note"],
                                               "hours": m["hours"]})
            except Exception as e:  # noqa: BLE001
                logger.debug("确认关系里程碑检查跳过: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.warning("错过里程碑检查失败: %s", e)
        return missed

    @staticmethod
    def _parse_dt(s) -> Optional[datetime]:
        if not s:
            return None
        if isinstance(s, datetime):
            return s
        try:
            return datetime.fromisoformat(str(s).replace("Z", ""))
        except (ValueError, AttributeError):
            return None

    # ================================================================== #
    # v3.1 联动①：direction_compass（方向罗盘）→ 任务
    #   罗盘 evaluate() 返回 gates/drift/verdict，这里映射成四方向：
    #     升温 / 维稳 / 修复 / 后撤。
    # ================================================================== #
    @staticmethod
    def direction_from_compass(compass_result: dict) -> str:
        """把方向罗盘结论映射成 升温/维稳/修复/后撤 四方向。

        映射规则：
          - 罗盘判定撤离/方向闸 fail 含撤离词 → 后撤
          - 漂移且好感趋势下滑/需修复 → 修复
          - 方向对齐、指标健康、趋势向上 → 升温
          - 其余 → 维稳
        """
        if not isinstance(compass_result, dict):
            return "维稳"
        gates = compass_result.get("gates", {}) or {}
        drift = compass_result.get("drift", {}) or {}
        direction_reason = str((gates.get("direction", {}) or {}).get("reason", ""))
        drift_level = str(drift.get("level", "none"))
        drift_reasons = drift.get("reasons", []) or []
        aligned = bool(compass_result.get("aligned"))

        # 撤离导向 → 后撤
        if any(w in direction_reason for w in ("撤离", "放弃", "止损", "撤退")):
            return "后撤"
        # 漂移（尤其 high）+ 下滑/修复相关 → 修复
        if drift.get("drift"):
            if any(("下滑" in r or "偏低" in r or "修复" in r) for r in drift_reasons):
                return "修复"
            if drift_level == "high":
                return "后撤"
            return "修复"
        # 方向对齐、健康 → 升温；否则维稳
        if aligned and (compass_result.get("alignment_score") or 0) >= 0.6:
            return "升温"
        return "维稳"

    # 四方向任务模板库（方向 → 候选任务列表）
    DIRECTION_TASK_POOL: dict[str, list[dict[str, Any]]] = {
        "升温": [
            {"title": "主动约一次轻松的线下见面", "category": "邀约推进",
             "difficulty": "普通", "base": 1.2, "stage_tags": [4, 5, 6],
             "direction_sem": "升温", "task_layer": "main", "route_hint": "any"},
            {"title": "制造一次独处机会并试探未来规划", "category": "升温试探",
             "difficulty": "困难", "base": 1.1, "stage_tags": [5, 6],
             "direction_sem": "升温", "task_layer": "main", "route_hint": "any"},
            {"title": "在氛围好时做一次轻微肢体接触试探", "category": "升温试探",
             "difficulty": "困难", "base": 1.0, "stage_tags": [6, 7],
             "direction_sem": "升温", "task_layer": "main", "route_hint": "any"},
        ],
        "维稳": [
            {"title": "日常关心：问一句对方今天过得怎样", "category": "关系维护",
             "difficulty": "简单", "base": 1.0, "stage_tags": list(range(0, 11)),
             "direction_sem": "维稳", "task_layer": "main", "route_hint": "any"},
            {"title": "提供情绪价值：接住对方一次吐槽", "category": "关系维护",
             "difficulty": "普通", "base": 1.0, "stage_tags": list(range(2, 9)),
             "direction_sem": "维稳", "task_layer": "main", "route_hint": "any"},
            {"title": "保持固定聊天频率，不推进也不后撤", "category": "关系维护",
             "difficulty": "简单", "base": 0.9, "stage_tags": list(range(0, 11)),
             "direction_sem": "维稳", "task_layer": "main", "route_hint": "any"},
        ],
        "修复": [
            {"title": "温和道歉并说明自己哪里做得不妥", "category": "冲突修复",
             "difficulty": "普通", "base": 1.2, "stage_tags": list(range(0, 11)),
             "direction_sem": "修复", "task_layer": "main", "route_hint": "any"},
            {"title": "主动给个台阶，约对方吃个饭缓和气氛", "category": "冲突修复",
             "difficulty": "普通", "base": 1.1, "stage_tags": list(range(0, 11)),
             "direction_sem": "修复", "task_layer": "main", "route_hint": "any"},
            {"title": "一起回忆一段美好的过往，软化情绪", "category": "冲突修复",
             "difficulty": "普通", "base": 1.0, "stage_tags": list(range(3, 10)),
             "direction_sem": "修复", "task_layer": "main", "route_hint": "any"},
        ],
        "后撤": [
            # 原有 3 条"后撤观察"：限时=当下观察类小任务（task_layer=limited）。
            # 约束④：回撤≠降频/断联。对高回避型，断档会被读成"对方也要离开"触发抢先撤退。
            #   正确形态="留灯任务"：连接频率稳定 + 内容低压 + 不要求回应。
            # 注意：category=后撤观察 → planner mainline_impact=-0.05 会被硬闸否决，
            #   仅作为方向语义池保留，真正落地靠下面的"铺垫型主线任务"。
            {"title": "保持每天一条轻连接：一句无压力的'在的，不急，你忙你的'式存在感问候，不追问不要求回应",
             "category": "后撤观察",
             "difficulty": "简单", "base": 1.0, "stage_tags": list(range(0, 11)),
             "direction_sem": "后撤", "task_layer": "limited"},
            {"title": "维持原联系频率，只把每次互动的情绪浓度降到无压力水平，观察对方在低压下的回应变化",
             "category": "后撤观察",
             "difficulty": "简单", "base": 1.0, "stage_tags": list(range(0, 11)),
             "direction_sem": "后撤", "task_layer": "limited"},
            {"title": "把注意力收回自己身上专注自己的事，同时保持原节奏的轻问候不缺席",
             "category": "后撤观察",
             "difficulty": "简单", "base": 0.9, "stage_tags": list(range(0, 11)),
             "direction_sem": "后撤", "task_layer": "limited"},
            # ★约束①核心：铺垫型主线任务（category=主线目标 → mainline_impact=+0.30
            #   才能过 planner 硬闸；boost_glide_planner 只读不改，故用 category 过闸）。
            #   后撤≠主线消失：回撤只是当前战术，主线=引导线/反制线的铺垫动作在此显式化。
            {"title": "给对方空间期做低压力不索取的日常关心，分享一个轻松日常不带问号",
             "category": "主线目标", "difficulty": "普通", "base": 1.15,
             "stage_tags": list(range(0, 11)), "direction_sem": "后撤铺垫",
             "task_layer": "main", "route_hint": "guide",
             "foundation": "舒适距离与安全感基底",
             "advance_trigger": "她主动开启话题/语气软化时转升温"},
            {"title": "记录对方放松状态下的主动靠近信号（主动开话题/回复变长/情绪软化），作为再拉近距离的触发",
             "category": "主线目标", "difficulty": "普通", "base": 1.15,
             "stage_tags": list(range(0, 11)), "direction_sem": "后撤铺垫",
             "task_layer": "main", "route_hint": "guide",
             "foundation": "时机捕捉与进退节奏",
             "advance_trigger": "连续2-3次主动信号后转升温"},
            {"title": "在共同话题中自然输出一次正面认知（认真对待关系、看见她的付出），不要求回应",
             "category": "主线目标", "difficulty": "普通", "base": 1.15,
             "stage_tags": list(range(0, 11)), "direction_sem": "后撤铺垫",
             "task_layer": "main", "route_hint": "guide",
             "foundation": "认知种子（让她重新评估关系）",
             "advance_trigger": "她对相关话题有情绪化回应"},
            {"title": "后撤期间保持边界：对敷衍/回避式回应不追问不讨好，记录对方真实投入信号",
             "category": "主线目标", "difficulty": "普通", "base": 1.15,
             "stage_tags": list(range(0, 11)), "direction_sem": "后撤铺垫",
             "task_layer": "main", "route_hint": "counter",
             "foundation": "边界与真实信号基线",
             "advance_trigger": "对方主动高质量回应"},
        ],
    }

    # 中级阶段线（task_layer="mid"）：后撤方向下"放缓但不断"。
    # category=关系维护 → planner mainline_impact=0.0 可过硬闸，区别于被否决的"后撤观察"。
    DIRECTION_MID_POOL: dict[str, list[dict[str, Any]]] = {
        "后撤": [
            {"title": "保持联系频率稳定，把每次互动情绪浓度降到无压力水平（内容轻、不追问、不要求回应）",
             "category": "关系维护", "difficulty": "简单", "base": 1.10,
             "stage_tags": list(range(0, 11)), "direction_sem": "后撤",
             "task_layer": "mid"},
            {"title": "在互动中多做接住对方的话的低压力回应，重建聊天舒适感",
             "category": "关系维护", "difficulty": "普通", "base": 1.05,
             "stage_tags": list(range(0, 11)), "direction_sem": "后撤",
             "task_layer": "mid"},
        ],
    }

    def generate_tasks_by_direction(self, person_id: str, direction: str) -> list[dict]:
        """根据方向罗盘给出的方向生成对应任务（方向任务池直出）。

        Args:
            person_id: 人物 ID。
            direction: 升温 / 维稳 / 修复 / 后撤。

        Returns:
            list[dict]: 该方向下的候选任务（含 category/difficulty/base）。
        """
        pool = self.DIRECTION_TASK_POOL.get(direction, self.DIRECTION_TASK_POOL["维稳"])
        out = []
        for t in pool:
            item = dict(t)
            item["direction"] = direction
            out.append(item)
        return out

    # ================================================================== #
    # v3.1 联动②：strategy_advisor（策略顾问）→ 任务权重
    #   策略 analyze() 返回 recommended/forbidden/reasoning，这里映射成五策略：
    #     慢热 / 升温 / 修复 / 后撤 / 拉扯。
    # ================================================================== #
    @staticmethod
    def strategy_from_advisor(persona: dict, strategy_result: dict) -> str:
        """把策略顾问输出 + 画像映射成 慢热/升温/修复/后撤/拉扯 五策略。

        映射规则（按优先级）：
          - 高风险/高欺骗/对方要求停止/出现撤离词 → 后撤
          - 近期冲突/情绪消极/冲突修复建议强 → 修复
          - 恐惧型/推拉/忽冷忽热信号 → 拉扯
          - 回避型/早期阶段/低主动 → 慢热
          - 暧昧/热恋阶段且趋势向上 → 升温
          - 默认 → 慢热
        """
        persona = persona or {}
        strategy_result = strategy_result or {}
        risk = str((persona.get("risk_warning", {}) or {}).get("overall_level", ""))
        deception = str(persona.get("deception_risk", ""))
        attachment = str((persona.get("attachment", {}) or {}).get("primary_type", "")
                         or (persona.get("attachment", {}) or {}).get("type", ""))
        emotion = str((persona.get("emotion_pattern", {}) or {}).get("pattern", ""))
        stage = str((persona.get("relationship_stage", {}) or {}).get("stage", ""))
        reasoning = str(strategy_result.get("reasoning", ""))
        # reasoning 为自由文本，实测对任何画像都含通用技巧措辞（如"修复关系裂痕"），
        # 子串判定会把所有画像误吞成同一策略、吞掉类型/阶段差异化。故 reasoning 形参
        # 仅保留在签名里（测试传 {}），不再参与分类——分类只由结构化画像信号驱动。

        # 后撤：高风险 / 高欺骗 / 撤离导向
        if risk in ("高", "极高") or deception in ("高", "极高"):
            return "后撤"
        # 修复：仅由结构化情绪信号驱动（emotion_pattern.pattern==消极倾向）
        if emotion == "消极倾向":
            return "修复"
        # 拉扯：恐惧型（类型优先）
        if "恐惧" in attachment:
            return "拉扯"
        # 慢热：回避型（类型优先于阶段——回避型即使暧昧期也不推升温，与留灯约束一致）
        if "回避" in attachment:
            return "慢热"
        # 升温：仅当非回避/恐惧且处于暧昧/热恋阶段
        if stage in ("暧昧期", "热恋期"):
            return "升温"
        # 默认慢热
        return "慢热"

    # 策略 → 任务类别权重表（命中该策略的任务类别放大，不匹配的缩小）
    _STRATEGY_CATEGORY_WEIGHT: dict[str, dict[str, float]] = {
        "慢热": {"关系维护": 1.4, "升温试探": 0.7, "后撤观察": 1.0, "邀约推进": 0.8,
                "冲突修复": 1.0},
        "升温": {"升温试探": 1.5, "邀约推进": 1.4, "关系维护": 1.0, "后撤观察": 0.5},
        "修复": {"冲突修复": 1.6, "关系维护": 1.1, "升温试探": 0.5, "后撤观察": 0.9},
        "后撤": {"后撤观察": 1.5, "冲突修复": 1.0, "关系维护": 1.0, "升温试探": 0.4},
        "拉扯": {"升温试探": 1.2, "关系维护": 1.1, "后撤观察": 1.1, "邀约推进": 0.9},
    }

    # ================================================================== #
    # v3.1 联动③：多源加权聚合（方向罗盘 × 策略顾问 × 好感阶段）
    #   候选任务综合分 = base × 方向匹配 × 策略匹配 × 阶段匹配。
    # ================================================================== #
    def _score_candidate(self, cand: dict, direction: str, strategy: str,
                         stage: int) -> float:
        """给单个候选任务打综合分。"""
        base = float(cand.get("base", 1.0))
        # 方向匹配：候选属于当前方向任务池 → 1.3，否则 0.7
        dir_w = 1.3 if cand.get("direction") == direction else 0.7
        # 策略匹配：按任务类别查表
        cat = cand.get("category", "")
        strat_w = self._STRATEGY_CATEGORY_WEIGHT.get(strategy, {}).get(cat, 1.0)
        # 阶段匹配：stage_tags 命中当前阶段 → 1.2，否则 0.85
        tags = cand.get("stage_tags", []) or []
        stage_w = 1.2 if (not tags or stage in tags) else 0.85
        return round(base * dir_w * strat_w * stage_w, 3)

    def generate_recommended_tasks(self, person_id: str, stage: int = None,
                                   other_state: dict = None,
                                   top_n: int = 3) -> list[dict]:
        """多源加权聚合入口：综合方向罗盘 × 策略顾问 × 好感阶段，推荐 top N 任务。

        Args:
            person_id: 人物 ID。
            stage: 关系阶段 0-10；为空时从好感度推断。
            other_state: 对方当天状态（透传给 adjust_difficulty_by_mood）。
            top_n: 返回前几个任务。

        Returns:
            list[dict]: 推荐任务，按综合分降序，附 score/direction/strategy 说明。
        """
        # 1) 推断阶段
        if stage is None:
            try:
                st = self.affection.get_stage(self.affection.get_total_with_bonus(
                    person_id, self.affection.get_current(person_id)))
                stage = int(st)
            except Exception:  # noqa: BLE001
                stage = 2

        # 2) 方向（罗盘）：尽力加载罗盘结果，失败兜底"维稳"
        direction = "维稳"
        compass_result = None
        try:
            from services.direction_compass import DirectionCompass
            compass = DirectionCompass(self.db)
            persona = self._load_persona(person_id)
            snapshot = self._build_compass_snapshot(persona, person_id)
            compass_result = compass.evaluate(person_id, snapshot)
            direction = self.direction_from_compass(compass_result)
        except Exception as e:  # noqa: BLE001
            logger.debug("方向罗盘联动失败，默认维稳: %s", e)

        # 3) 策略（策略顾问）：尽力加载，失败兜底"慢热"
        strategy = "慢热"
        persona = self._load_persona(person_id)
        try:
            if self.strategy_advisor is not None and persona:
                sres = self.strategy_advisor.analyze(persona, relation_stage=stage)
                strategy = self.strategy_from_advisor(persona, sres)
        except Exception as e:  # noqa: BLE001
            logger.debug("策略顾问联动失败，默认慢热: %s", e)

        # 4) 候选任务池：当前方向任务 + 主线阶段目标转候选。
        #    route 提前算：铺垫型主线任务按 route_hint 过滤（主线=引导线/反制线显式化）。
        route = self._current_route(person_id)
        candidates = self.generate_tasks_by_direction(person_id, direction)
        # 主线阶段目标：按"自身语义方向"挂 direction_sem，不再无脑打当前 direction；
        # 后撤/修复方向下丢弃"升温(推进)"模板，避免罗盘判后撤却推推进主线。
        sem_list = self.STAGE_TASK_DIRECTION.get(stage, [])
        for i, title in enumerate(self.STAGE_GOALS.get(stage, [])):
            sem = sem_list[i] if i < len(sem_list) else "通用"
            if direction in ("后撤", "修复") and sem == "升温":
                continue
            candidates.append({
                "title": title, "category": "主线目标",
                "difficulty": "普通", "base": 1.15 - 0.05 * i,
                "direction": direction, "direction_sem": sem,
                "task_layer": "mid", "stage_tags": [stage],
            })
        # 后撤方向：中级阶段线放缓但不断（关系维护类，mainline_impact=0.0 可过闸）。
        if direction == "后撤":
            for t in self.DIRECTION_MID_POOL.get("后撤", []):
                item = dict(t)
                item.setdefault("direction", direction)
                candidates.append(item)
        # route_hint 过滤：铺垫型主线任务只保留 any / 当前路线（其余候选无 route_hint 不受影响）。
        candidates = [
            c for c in candidates
            if not c.get("route_hint") or c.get("route_hint") in ("any", route)
        ]

        # 5) 长期优化"助推-滑翔"规划器优先（B 组）：SMDP Q/V 长期值排序 + CMDP 主线硬闸。
        #    取足候选（top_n*2 或全部）再做 task_engine 侧后处理，避免后处理没得选。
        history = self._fetch_task_history(person_id)
        try:
            from services.boost_glide_planner import (
                BoostGlidePlanner, build_decision_context)
            if self._boost_planner is None:
                self._boost_planner = BoostGlidePlanner()
            ctx = build_decision_context(
                person_id, candidates,
                affection=self.affection, compass_result=compass_result,
                stage=stage, direction=direction, horizon=12,
                persona=persona,
            )
            planned = self._boost_planner.recommend(
                ctx, top_n=max(top_n * 2, len(candidates)))
            scored = self._postprocess_planned(
                planned, direction=direction, strategy=strategy, route=route,
                history=history, other_state=other_state, top_n=top_n)
            if scored is not None:
                # 统一附加 reward_goal + 父链字段
                scored = [self._attach_task_relations(t, direction, stage, route) for t in scored]
                return scored
            logger.debug("规划器方向对齐后为空，进入回退路径")
        except Exception as e:  # noqa: BLE001
            logger.warning("助推-滑翔规划器异常，回退旧打分路径: %s", e)

        # 6) 回退路径：
        #    - 后撤/修复方向：绝不回落推进主线模板，走 _render_without_planner
        #      （作用于方向池+后撤铺垫候选，含方向对齐/DUM 区分化/主线保底）；
        #    - 其余方向：维持原 A 路径（多源静态加权）。
        if direction in ("后撤", "修复"):
            return self._render_without_planner(
                candidates, direction=direction, strategy=strategy, stage=stage,
                route=route, history=history, other_state=other_state, top_n=top_n)

        # ===== 旧路径（A 组贪心静态打分；仅其余方向保留，供 benchmark A 组与降级） =====
        scored = []
        for cand in candidates:
            score = self._score_candidate(cand, direction, strategy, stage)
            cand = dict(cand)
            cand["score"] = score
            cand["matched_direction"] = direction
            cand["matched_strategy"] = strategy
            # R3.3-78：旧回退路径同样按路线扩展奖励向量 + 标记 route。
            cand["route"] = route
            cand["expected_reward"] = self._extend_reward_route(
                self._category_reward_vec(cand, direction), route)
            _, _, cand["loyalty_reward"] = self._anchor_for(
                route, cand.get("title", ""), 0.0)
            # 叠加：重复惩罚 + 状态难度（不改排序分，只附说明字段）
            cand = self._apply_repeat_penalty(cand, history)
            if other_state:
                cand = self.adjust_difficulty_by_mood(cand, other_state)
            scored.append(cand)

        scored.sort(key=lambda x: x["score"], reverse=True)
        result = scored[:top_n]

        # 统一附加字段：reward_goal（现实奖励）+ 父链（parent_stage/parent_mainline）
        result = [self._attach_task_relations(t, direction, stage, route) for t in result]
        return result

    def _attach_task_relations(self, task: dict, direction: str, stage: int, route: str) -> dict:
        """附加 reward_goal（现实奖励）+ 父链字段（parent_stage/parent_mainline）。"""
        task = dict(task)
        # reward_goal：按当前方向推导"达成此任务后应得的现实奖励"
        reward_options = REWARD_GOAL_MAP.get(direction, ["关系稳定推进"])
        task["reward_goal"] = reward_options[0] if reward_options else "关系推进"
        # 父链：阶段线 + 主线
        task["parent_stage"] = f"阶段{stage}"
        task["parent_mainline"] = route if route else "引导线"
        return task

    # ------------------------------------------------------------------ #
    # 病灶①后处理：方向对齐 → 主线保底 → DUM 贪心类别惩罚+抖动 → rationale 区分化。
    #   全部在 task_engine 侧完成，boost_glide_planner.py 只读不改。
    # ------------------------------------------------------------------ #
    def _dir_aligned(self, pa, direction: str) -> bool:
        """方向对齐：保留 direction_sem 命中(方向/通用/None) 或 raw.direction 一致的产出。"""
        raw = pa.candidate.raw if pa.candidate else {}
        sem = raw.get("direction_sem")
        return sem in (direction, "通用", None) or raw.get("direction") == direction

    def _dum_greedy(self, work: list, top_n: int) -> list:
        """DUM 贪心多样性选择：每轮选 q-0.3*同类已选数+N(0,0.01) 最大者。

        work: list[{"q":float,"category":str}]，原地写 "final"。
        Returns: 选中的 work 子列表（按 final 降序在调用方排序）。
        """
        used = [False] * len(work)
        selected: list = []
        cat_count: dict[str, int] = {}
        while len(selected) < top_n and any(not u for u in used):
            best_i, best_final = -1, None
            for i, w in enumerate(work):
                if used[i]:
                    continue
                cnt = cat_count.get(w["category"], 0)
                final = w["q"] - 0.3 * cnt + random.gauss(0, 0.01)
                if best_final is None or final > best_final:
                    best_final, best_i = final, i
            if best_i < 0:
                break
            used[best_i] = True
            w = work[best_i]
            w["final"] = best_final
            selected.append(w)
            cat_count[w["category"]] = cat_count.get(w["category"], 0) + 1
        return selected

    def _main_floor(self, work: list, selected: list) -> None:
        """主线保底：selected 无 task_layer=='main' 时换入最高分 main（替换最低分非 main）。"""
        mains = [w for w in work if w["raw"].get("task_layer") == "main"]
        if not mains or any(w["raw"].get("task_layer") == "main" for w in selected):
            return
        pool = sorted([w for w in mains if w not in selected],
                      key=lambda w: w["q"], reverse=True)
        if not pool:
            return
        nonmain = [w for w in selected if w["raw"].get("task_layer") != "main"]
        if not nonmain:
            return
        victim = nonmain[-1]  # selected 已按 final 降序，末尾为最低分非 main
        selected.remove(victim)
        m = pool[0]
        m["final"] = m["q"] + random.gauss(0, 0.01)
        selected.append(m)

    def _decorate_post(self, cand: dict, *, score: float, direction: str,
                       strategy: str, route: str, history: list,
                       other_state, rationale: str) -> dict:
        """后处理公共尾缀：route/loyalty/新字段/重复惩罚/状态难度 + rationale 区分化。"""
        cand = dict(cand)
        cand["score"] = round(float(score), 4)
        cand["q"] = cand["score"]
        rat = f"{rationale}｜本步：{cand.get('title', '')}"
        if cand.get("task_layer") == "main" and cand.get("direction_sem") == "后撤铺垫":
            rat = f"{rat}｜埋根：{cand.get('foundation', '')}；前进触发：{cand.get('advance_trigger', '')}"
        cand["rationale"] = rat
        cand["route"] = route
        _, _, cand["loyalty_reward"] = self._anchor_for(
            route, cand.get("title", ""), 0.0)
        cand["matched_direction"] = direction
        cand["matched_strategy"] = strategy
        cand["task_layer"] = cand.get("task_layer", "")
        cand["foundation"] = cand.get("foundation", "")
        cand["advance_trigger"] = cand.get("advance_trigger", "")
        cand = self._apply_repeat_penalty(cand, history)
        if other_state:
            cand = self.adjust_difficulty_by_mood(cand, other_state)
        return cand

    def _postprocess_planned(self, planned, *, direction: str, strategy: str,
                              route: str, history: list, other_state,
                              top_n: int) -> Optional[list]:
        """planner 产出的 task_engine 侧后处理。返回 None 表示方向对齐后为空（交回退）。"""
        if not planned:
            return None
        aligned = [pa for pa in planned if self._dir_aligned(pa, direction)]
        if not aligned:
            return None
        work = []
        for pa in aligned:
            raw = dict(pa.candidate.raw) if pa.candidate else {}
            work.append({"pa": pa, "raw": raw, "q": float(pa.q),
                         "category": raw.get("category", "")})
        selected = self._dum_greedy(work, top_n)
        self._main_floor(work, selected)
        selected.sort(key=lambda w: w.get("final", w["q"]), reverse=True)

        scored = []
        for w in selected:
            pa = w["pa"]
            cand = dict(w["raw"])
            cand["v_after"] = round(float(pa.v_after), 4)
            cand["mode"] = pa.mode
            cand["expected_reward"] = self._extend_reward_route(
                dict(pa.expected_reward), route)
            cand["success_prob"] = float(pa.success_prob)
            cand["gate_passed"] = bool(pa.gate.passed)
            cand["mainline_impact"] = float(pa.gate.mainline_impact)
            cand["pareto"] = dict(pa.pareto)
            scored.append(self._decorate_post(
                cand, score=w.get("final", w["q"]), direction=direction,
                strategy=strategy, route=route, history=history,
                other_state=other_state, rationale=str(pa.rationale)))
        return scored

    def _render_without_planner(self, candidates: list, *, direction: str,
                                strategy: str, stage: int, route: str,
                                history: list, other_state, top_n: int) -> list:
        """无 planner 降级渲染（仅后撤/修复方向）：宁可用方向池/关系维护/后撤铺垫动作，
        也绝不回落推进主线模板。走 _score_candidate 静态分 + DUM 区分化 + 主线保底。"""
        work = []
        for cand in candidates:
            score = self._score_candidate(cand, direction, strategy, stage)
            raw = dict(cand)
            work.append({"raw": raw, "q": float(score),
                         "category": raw.get("category", "")})
        selected = self._dum_greedy(work, top_n)
        self._main_floor(work, selected)
        selected.sort(key=lambda w: w.get("final", w["q"]), reverse=True)

        scored = []
        for w in selected:
            cand = dict(w["raw"])
            cand["mode"] = "fallback"
            cand["expected_reward"] = self._extend_reward_route(
                self._category_reward_vec(cand, direction), route)
            cand = self._decorate_post(
                cand, score=w.get("final", w["q"]), direction=direction,
                strategy=strategy, route=route, history=history,
                other_state=other_state, rationale="静态加权降级路径")
            # 附加 reward_goal + 父链字段
            cand = self._attach_task_relations(cand, direction, stage, route)
            scored.append(cand)
        return scored

    def _load_persona(self, person_id: str) -> Optional[dict]:
        """从磁盘加载该人物最新画像（供罗盘/策略适配）。失败返回 None。"""
        try:
            from services.strategy_advisor import load_persona_from_disk
            return load_persona_from_disk(person_id)
        except Exception:  # noqa: BLE001
            return None

    def _build_compass_snapshot(self, persona: dict, person_id: str) -> dict:
        """从画像组装方向罗盘需要的 snapshot。

        进边接线（2026-10-13 外科手术式接通）：把 analyze() 已产出但此前从未流入罗盘的
        真实证据字段透传进来——轨迹向量 / 怀疑链 / 压力源归因 / 模式库匹配 / 信任 /
        冲突修复率 / 方向史 / 阈值联动 / 前瞻分离风险。纯附加键，不改既有 5 键
        (attachment/risk_level/deception_risk/affection_trend/strategy_text)，
        因此既有闸门/漂移/评分行为完全不变。
        """
        snap: dict[str, Any] = {}
        if not persona:
            return snap
        att = (persona.get("attachment", {}) or {})
        snap["attachment"] = att.get("primary_type") or att.get("type") or ""
        snap["risk_level"] = str((persona.get("risk_warning", {}) or {}).get("overall_level", ""))
        snap["deception_risk"] = str(persona.get("deception_risk", ""))
        # 好感趋势（近似：情绪模式）
        emo = (persona.get("emotion_pattern", {}) or {}).get("pattern", "")
        snap["affection_trend"] = {"积极主导": "up", "偏积极": "up",
                                   "中性平稳": "flat", "消极倾向": "down"}.get(emo, "flat")
        # 策略文本（策略顾问 reasoning）
        if self.strategy_advisor is not None:
            try:
                sres = self.strategy_advisor.analyze(persona, relation_stage=5)
                snap["strategy_text"] = sres.get("reasoning", "")
            except Exception:  # noqa: BLE001
                pass

        # ---- 以下为新增进边：真实证据透传（缺失项不臆造，置 None/空由罗盘侧如实标注）----
        # ① 轨迹向量（全量历史三段趋势 + 突变点 + 周期性）
        tv = persona.get("trajectory_vector")
        if isinstance(tv, dict) and "error" not in tv:
            snap["trajectory_vector"] = tv
        # ② 信任等级（0-100）
        tr = persona.get("trust") or {}
        snap["trust_score"] = tr.get("score")
        # ③ 怀疑链状态（score 0-100 + level）
        sc = persona.get("suspicion_chain") or {}
        if isinstance(sc, dict):
            snap["suspicion_score"] = sc.get("suspicion")
            snap["suspicion_level"] = sc.get("level")
        # ④ 压力源归因分布（category -> 命中次数）
        sa = persona.get("stress_attribution") or {}
        if isinstance(sa, dict):
            snap["stress_dist"] = sa.get("category_distribution") or {}
            snap["stress_focal_count"] = sa.get("focal_events_count") or 0
        # ⑤ 模式库匹配（事件级信号匹配结果）
        pm = persona.get("pattern_match") or {}
        if isinstance(pm, dict):
            snap["pattern_match"] = pm
        # ⑥ 冲突修复率（0-100）
        cr = persona.get("conflict_repair") or {}
        snap["repair_rate"] = cr.get("repair_rate")
        # ⑦ 方向史当前方向 + 阈值联动建议（直接透传，罗盘侧再做真实规则复核）
        dh = persona.get("direction_history") or {}
        if isinstance(dh, dict):
            snap["direction_history_current"] = dh.get("current_direction")
        # ⑧ 前瞻分离风险（低置信度，来自 analyze() forecast）
        fc = persona.get("forecast") or {}
        if isinstance(fc, dict) and "error" not in fc:
            snap["p_separation_risk_in"] = fc.get("p_separation_risk")

        # ⑨ 参考指标：关系满意度（画像透传，供罗盘 north_star 作"参考"）
        rs = persona.get("relationship_satisfaction") or {}
        if isinstance(rs, dict):
            snap["relationship_satisfaction"] = {
                "score": rs.get("score"),
                "level": rs.get("level"),
            }

        # ⑩ 主信号：她"主动参与解决"（services/consensus_signal.py 直接读库计算）
        #    倾向模块直接读库，避免依赖 persona 新字段；失败即 None，罗盘侧如实标注。
        try:
            if self.db is not None and getattr(self.db, "db_path", None):
                from services.consensus_signal import run_consensus_signal
                snap["consensus"] = run_consensus_signal(
                    self.db.db_path, person_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("共识信号计算失败(降级为主信号缺失): %s", e)
            snap["consensus"] = None

        return snap


# ===== 任务系统游戏化 =====
TASK_DIFFICULTY_XP = {"简单": 10, "普通": 25, "困难": 50, "地狱": 100}

# 现实奖励映射（按任务阶段/方向推导"达成此任务后应得的现实奖励"）
REWARD_GOAL_MAP = {
    "升温": ["对方主动靠近", "情绪互动升温", "关系亲密度提升"],
    "后撤": ["对方放松戒备", "对方反思行为", "冲突修复窗口"],
    "维稳": ["关系稳定无波动", "对方信任感维持"],
    "修复": ["矛盾和解", "对方态度软化", "沟通恢复顺畅"],
}


class GamifiedTaskEngine:
    def __init__(self):
        self._daily = {}
        self._limited = {}

    def get_task_rewards(self, difficulty="普通", task_completed=False, affection_change=0) -> dict:
        """奖励与任务真实完成挂钩：完成→好感度变化→XP→勋章解锁一条链闭环。"""
        base_xp = TASK_DIFFICULTY_XP.get(difficulty, 10)
        if not task_completed:
            return {"xp": 0, "affection_change": 0, "medal_unlocked": None,
                    "note": "任务未完成，无奖励"}

        # 完成→好感度变化→XP→勋章解锁
        xp = base_xp + affection_change  # 好感度变化加成 XP
        medal = None
        try:
            from services.service_facade import MedalService
            medal_service = MedalService()
            medal = medal_service.check_and_unlock(affection_change=affection_change, xp=xp)
        except Exception:
            pass  # MedalService 不可用时降级

        return {"xp": xp, "affection_change": affection_change, "medal_unlocked": medal,
                "note": f"完成→好感度+{affection_change}→XP+{xp}→勋章={medal or '无'}"}

    def record_task_failure(self, task_id, reason="") -> dict:
        return {"task_id": task_id, "xp_penalty": -5, "reason": reason}

    def get_daily_tasks(self, person_id):
        return self._daily.get(person_id, [])

    def refresh_daily_tasks(self, person_id, task_engine=None) -> list:
        """从真实任务推荐生成每日任务（接 generate_recommended_tasks 输出），带真实内容。"""
        if task_engine is None:
            # 降级：假任务（仅测试用）
            self._daily[person_id] = [{"id": f"d_{i}", "difficulty": "普通", "title": f"日常任务{i+1}"} for i in range(3)]
            return self._daily[person_id]

        # 真实：从 generate_recommended_tasks 取 top 3
        try:
            recommended = task_engine.generate_recommended_tasks(person_id, top_n=3)
            self._daily[person_id] = [
                {
                    "id": f"daily_{i}",
                    "title": t.get("title", ""),
                    "category": t.get("category", ""),
                    "difficulty": t.get("difficulty", "普通"),
                    "reward_goal": t.get("reward_goal", ""),  # 现实奖励字段
                    "parent_stage": t.get("parent_stage", ""),  # 三层父链：阶段线
                    "parent_mainline": t.get("parent_mainline", ""),  # 三层父链：主线
                }
                for i, t in enumerate(recommended)
            ]
        except Exception:
            # 降级
            self._daily[person_id] = [{"id": f"d_{i}", "difficulty": "普通", "title": f"日常任务{i+1}"} for i in range(3)]
        return self._daily[person_id]

    def get_limited_tasks(self, person_id):
        return self._limited.get(person_id, [])

    def check_limited_task_expiry(self, person_id=None) -> list:
        """限时任务 deadline<now 标 missed/过期（真判）。"""
        import time
        now = time.time()
        expired = []
        for pid, tasks in self._limited.items():
            if person_id and pid != person_id:
                continue
            for t in tasks:
                deadline = t.get("deadline", 0)
                if deadline and deadline < now:
                    t["status"] = "missed"
                    t["expired"] = True
                    expired.append(t)
        return expired


# ================================================================== #
# HTN 分层任务（22.2.2）——高层目标分解为任务网络（compound→primitive）
# GOAP 未实现，HTN 先落地，GOAP 排期。
# ================================================================== #

class HTNPlanner:
    """HTN 分层任务规划器：恋爱阶段分层规划。

    与 GOAP 混合：HTN 负责"分层分解"（高层目标→任务网络），
    现有 generate_recommended_tasks 负责"内容生成"（具体任务内容）。
    """

    # 高层目标→分解规则（compound task → primitive tasks）
    DECOMPOSITION_RULES = {
        "升温": [
            {"primitive": "发起轻松对话", "prerequisite": "关系稳定"},
            {"primitive": "分享个人小故事", "prerequisite": "有共同话题"},
            {"primitive": "主动关心对方状态", "prerequisite": "情感连接建立"},
        ],
        "后撤": [
            {"primitive": "减少主动联系频率", "prerequisite": "回避加剧"},
            {"primitive": "留灯保持存在感", "prerequisite": "冲突发生"},
            {"primitive": "埋前进根基（分享价值内容）", "prerequisite": "对方情绪缓和"},
        ],
        "维稳": [
            {"primitive": "维持聊天节奏", "prerequisite": "关系平稳"},
            {"primitive": "记住对方喜好", "prerequisite": "有互动记录"},
        ],
    }

    def __init__(self):
        self._task_network = {}  # person_id → 任务网络

    def decompose(self, person_id: str, high_level_goal: str, stage: int = 2) -> list:
        """高层目标分解为任务网络（compound→primitive）。

        Args:
            person_id: 人物 ID
            high_level_goal: 高层目标（升温/后撤/维稳）
            stage: 关系阶段 0-10

        Returns:
            list: primitive 任务列表（含 prerequisite）
        """
        rules = self.DECOMPOSITION_RULES.get(high_level_goal, [])
        network = []
        for rule in rules:
            network.append({
                "type": "primitive",
                "title": rule["primitive"],
                "prerequisite": rule["prerequisite"],
                "parent_goal": high_level_goal,
                "stage": stage,
            })
        self._task_network[person_id] = {
            "high_level_goal": high_level_goal,
            "stage": stage,
            "tasks": network,
        }
        return network

    def get_task_network(self, person_id: str) -> dict:
        """获取任务网络（compound→primitive 结构）。"""
        return self._task_network.get(person_id, {})


# ================================================================== #
# 动态重规划（22.2.3）——任务级反馈闭环
# 与 direction_compass 导航/迂回变轨不重复：那是方向级，这是任务级反馈闭环。
# ================================================================== #

class DynamicReplanner:
    """动态重规划器：任务执行后反馈→策略层重规划。

    反馈维度：
    - 完成状态（完成/失败/无效）
    - 好感度变化（完成→好感度+X，失败→好感度-X）
    - 失败原因（对方回避/情绪低/时机不对）

    重规划动作：
    - 失败/无效任务替换
    - 频率调整（过密→降频，过疏→升温）
    - 策略提示更新（基于反馈调整策略方向）
    """

    def __init__(self):
        self._feedback_history = {}  # person_id → 反馈历史

    def record_feedback(self, person_id: str, task_id: str, status: str,
                       affection_change: int = 0, reason: str = "") -> dict:
        """记录任务反馈。

        Args:
            person_id: 人物 ID
            task_id: 任务 ID
            status: 完成状态（completed/failed/ineffective）
            affection_change: 好感度变化
            reason: 失败原因（可选）

        Returns:
            dict: 反馈记录
        """
        feedback = {
            "task_id": task_id,
            "status": status,
            "affection_change": affection_change,
            "reason": reason,
        }
        if person_id not in self._feedback_history:
            self._feedback_history[person_id] = []
        self._feedback_history[person_id].append(feedback)
        return feedback

    def replan(self, person_id: str) -> dict:
        """基于反馈历史重规划策略提示。

        Returns:
            dict: 重规划建议（替换任务/频率调整/策略提示）
        """
        history = self._feedback_history.get(person_id, [])
        if not history:
            return {"action": "no_change", "reason": "无反馈历史"}

        # 统计失败率
        failed = [f for f in history if f["status"] in ("failed", "ineffective")]
        fail_rate = len(failed) / len(history)

        suggestions = []
        if fail_rate > 0.5:
            suggestions.append("任务失败率过高（>50%）→ 建议替换任务类型/调整难度")
        if fail_rate > 0.3:
            suggestions.append("失败率偏高（>30%）→ 建议降低频率/等待时机")

        return {
            "action": "replan" if suggestions else "no_change",
            "fail_rate": round(fail_rate, 2),
            "suggestions": suggestions,
            "feedback_count": len(history),
        }


# ================================================================== #
# 回复建议详细结构（4.3.1）——首选标记 + 弹药库结构
# ================================================================== #

class ReplySuggestionBuilder:
    """回复建议构建器：输出带首选标记 + 弹药库结构。

    每条建议附：
    - is_primary: 首选标记（True=首选，False=备选）
    - reply_text: 建议回复话术
    - rationale: 理由（为什么这么说）
    - timing: 适用时机（什么时候说）
    """

    def __init__(self):
        self._suggestions = {}  # person_id → 建议列表

    def build_suggestions(self, person_id: str, context: str, direction: str = "维稳") -> list:
        """构建回复建议（首选+备选）。

        Args:
            person_id: 人物 ID
            context: 当前聊天上下文
            direction: 当前方向（升温/后撤/维稳）

        Returns:
            list: 建议列表（含首选标记）
        """
        # 基于方向生成建议（简化版，接真实策略层后可扩展）
        suggestions_map = {
            "升温": [
                {"is_primary": True, "reply_text": "最近怎么样？想你了",
                 "rationale": "直接表达想念，升温关系", "timing": "对方主动联系时"},
                {"is_primary": False, "reply_text": "今天有个好玩的事想跟你说",
                 "rationale": "分享趣事，拉近距离", "timing": "聊天冷场时"},
            ],
            "后撤": [
                {"is_primary": True, "reply_text": "嗯，好的",
                 "rationale": "简短回应，保持低压力", "timing": "对方情绪低时"},
                {"is_primary": False, "reply_text": "忙完再说吧",
                 "rationale": "减少主动，留灯", "timing": "连续主动无果时"},
            ],
            "维稳": [
                {"is_primary": True, "reply_text": "最近还好吗？",
                 "rationale": "常规问候，维持连接", "timing": "定期问候时"},
                {"is_primary": False, "reply_text": "看你发的朋友圈了，挺有意思",
                 "rationale": "轻量互动，不施压", "timing": "对方发动态时"},
            ],
        }
        suggestions = suggestions_map.get(direction, suggestions_map["维稳"])
        self._suggestions[person_id] = suggestions
        return suggestions

    def get_suggestions(self, person_id: str) -> list:
        """获取回复建议（弹药库）。"""
        return self._suggestions.get(person_id, [])
