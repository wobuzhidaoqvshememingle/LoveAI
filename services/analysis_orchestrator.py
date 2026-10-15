# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 统一分析编排器（AnalysisOrchestrator）

把 6 个"孤岛模块"真正串起来，实现：
  1. 场景识别先行：根据聊天文本+情感+LLM输出+回复间隔，判断当前处于 10 种场景中的哪一种；
  2. 按需选择模块：不是每次跑所有模块，根据场景选择模块组合（已读不回→只跑 no_reply，
     冲突→strategy_advisor+psychology_laws+deception_detector，日常闲聊→只跑人格轻量分析）；
  3. 串联执行（数据传递）：personality_strategy → strategy_advisor → psychology_laws → task_engine；
     deception 风险 → 调整策略强度；no_reply 原因 → 调整应对策略；
  4. 结果整合：统一输出 orchestration_result，含 scene/personality/strategy/laws/no_reply/
     commitment/deception/tasks/risk_warnings/modules_called。

设计约束：
  - 纯 Python 服务层，不 import PySide6；
  - 每个模块调用有 try/except + 3 秒超时保护，单模块失败不影响整体；
  - 数据库查询全部使用 :name 命名参数，防 SQL 注入；
  - 详细 logging 记录每次调用了哪些模块、场景判断结果。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 延迟导入（避免循环依赖；运行时才 import 各孤岛模块）
_StrategyAdvisor = None
_NoReplyDetector = None
_CommitmentTracker = None
_DeceptionDetector = None
_psychology_laws = None
_personality_strategy = None
_combination_playbook = None

# 单模块超时（秒）
_MODULE_TIMEOUT: float = 3.0


# ====================================================================== #
# 场景关键词词表（10 种场景）
# ====================================================================== #
_SCENE_CONFLICT_WORDS: tuple[str, ...] = (
    "吵架", "生气", "分手", "冷战", "算了", "不想理", "烦", "委屈",
    "不可理喻", "你又", "哭", "难过", "失望", "受够了", "滚", "不可调和",
    "我错了", "对不起", "原谅", "吵架了", "闹别扭",
    "你瞎", "瞎吗", "你有病", "你疯了", "没点数", "心里没数", "吼我",
    "嫌我烦", "男人都是一个样", "你什么意思",
)

_SCENE_COLDDOWN_WORDS: tuple[str, ...] = (
    "嗯", "哦", "噢", "额", "呵", "哈哈", "随便", "都行", "还好",
    "没事", "在忙", "等会", "回头说", "再说吧", "再说",
    "到时候看", "到时候", "等我有空", "看情况", "有空再说", "回头再说",
    "没干嘛", "不聊了", "先这样",
)

_SCENE_FLIRT_WORDS: tuple[str, ...] = (
    "想你", "喜欢你", "爱你", "宝贝", "亲亲", "抱抱", "么么",
    "早安", "晚安", "在干嘛", "吃了吗", "在乎", "心疼", "思念",
    "放我鸽子", "鸽我", "放鸽子", "可别", "约好", "记着",
)

_SCENE_INVITE_WORDS: tuple[str, ...] = (
    "出来玩", "见面", "吃饭", "看电影", "逛街", "约会", "一起去",
    "有空吗", "周末", "哪天", "约你", "找你", "见一面",
)

_SCENE_MONEY_WORDS: tuple[str, ...] = (
    "借钱", "借我", "转我", "转账", "多少钱", "我请", "AA", "买单",
    "红包", "工资", "费用", "花了", "还钱", "先付", "垫一下", "报销",
    "发工资", "房租", "生活费", "还你", "欠你", "欠我", "这个月不够",
    "下月还", "下个月还", "周转", "彩礼", "聘礼", "嫁妆", "证明实力",
    "备注", "520", "1314", "13920",
)

_SCENE_CONFESSION_WORDS: tuple[str, ...] = (
    "表白", "在一起", "我爱你", "做我男", "做我女", "确认关系",
    "正式交往", "我喜欢你很久", "想和你在一起", "我们的关系",
)

_SCENE_BREAKUP_WORDS: tuple[str, ...] = (
    "分手吧", "我们分手", "不合适", "算了吧", "不如算了", "走不下去",
    "累了", "想一个人", "分开吧", "结束吧", "彼此冷静",
)

_SCENE_COMMITMENT_WORDS: tuple[str, ...] = (
    "我答应你", "我保证", "下次一定", "到时候", "说好了", "约好",
    "一定来", "没问题", "我会的", "包在我身上", "肯定到",
)


# —— 结构/时序上下文特征词（_detect_structural_context 用）—— #
# 我/男方提出的具体选项（提议）
_STRUCT_PROPOSAL_HINTS: tuple[str, ...] = (
    "火锅", "粥", "炒面", "米线", "烧烤", "西餐", "日料", "快餐",
    "吃点什么", "吃啥", "去哪吃", "看电影", "逛街", "看什么",
)
# 对方对提议的否定
_STRUCT_REJECT_HINTS: tuple[str, ...] = (
    "太腻", "太淡", "不喜欢", "不行", "不想吃", "太油", "太贵",
    "太吵", "没意思", "减肥", "不合适",
)
# 破冰方粗口/质问式开场（字面像攻击，实为嘴硬递台阶）
_STRUCT_HARSH_OPEN: tuple[str, ...] = (
    "死了没", "还活着", "还在不在", "死哪去", "干嘛呢你",
)
# 嘴硬否认+找借口延续对话
_STRUCT_DENIAL_EXCUSE: tuple[str, ...] = (
    "谁要喊你", "谁要你", "我才不是", "我只是想问", "我是想问",
    "没别的事", "顺便问", "谁稀罕",
)
# 共同关注对象借口（家里的花/宠物/物件）
_STRUCT_COMMON_OBJECT: tuple[str, ...] = (
    "浇花", "浇了没", "多肉", "阳台", "家里的", "你的那盆",
    "宠物", "猫", "狗", "花了没",
)
# 紧随的补偿/关心动作（确认破冰/和好意图）
_STRUCT_COMPENSATION: tuple[str, ...] = (
    "买了", "跑腿", "酸梅汤", "烤鸭", "绕路", "每天早上", "默默",
    "换了", "手怎么了", "创可贴", "想你", "回家吃饭", "给你带",
    "粥", "被子", "厚被", "厨房有", "热一热", "热在锅里", "锅里",
    "碗筷", "抽屉", "留了", "给你留", "降温", "自己热",
)
# 脆弱性自我暴露（愤怒背后的深层需求，=转向修复而非攻击）
_STRUCT_VULNERABLE: tuple[str, ...] = (
    "特别害怕", "我好怕", "一个人在", "我也害怕", "我一个人",
    "特别委屈", "我也很", "我一个人在医院",
)


# ====================================================================== #
# 工具函数
# ====================================================================== #
def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    """文本中是否包含任意关键词。"""
    if not text:
        return False
    return any(w in text for w in words)


def _run_with_timeout(func, timeout: float = _MODULE_TIMEOUT) -> tuple[bool, Any]:
    """在子线程中执行 func，超时则放弃。

    Returns:
        (ok, result): ok=True 表示正常执行完；ok=False 表示超时或异常。
                     result 为函数返回值（ok=False 时为 Exception 对象或 None）。
    """
    box: dict[str, Any] = {}

    def _runner():
        try:
            box["v"] = func()
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=_runner, daemon=True, name="orchestrator_module")
    t.start()
    t.join(timeout)

    if t.is_alive():
        # 超时：线程仍在跑，放弃等结果（daemon 线程会随主进程退出）
        return False, None
    if "e" in box:
        return False, box["e"]
    return True, box.get("v")


def _lazy_import():
    """延迟导入各孤岛模块（避免顶层循环 import）。"""
    global _StrategyAdvisor, _NoReplyDetector, _CommitmentTracker
    global _DeceptionDetector, _psychology_laws, _personality_strategy, _combination_playbook
    if _StrategyAdvisor is not None:
        return  # 已导入
    try:
        from services.strategy_advisor import StrategyAdvisor
        _StrategyAdvisor = StrategyAdvisor
    except Exception as e:  # noqa: BLE001
        logger.warning("导入 StrategyAdvisor 失败: %s", e)
    try:
        from services.no_reply_detector import NoReplyDetector
        _NoReplyDetector = NoReplyDetector
    except Exception as e:  # noqa: BLE001
        logger.warning("导入 NoReplyDetector 失败: %s", e)
    try:
        from services.commitment_tracker import CommitmentTracker
        _CommitmentTracker = CommitmentTracker
    except Exception as e:  # noqa: BLE001
        logger.warning("导入 CommitmentTracker 失败: %s", e)
    try:
        from services.deception_detector import DeceptionDetector
        _DeceptionDetector = DeceptionDetector
    except Exception as e:  # noqa: BLE001
        logger.warning("导入 DeceptionDetector 失败: %s", e)
    try:
        from services import psychology_laws as _pl
        _psychology_laws = _pl
    except Exception as e:  # noqa: BLE001
        logger.warning("导入 psychology_laws 失败: %s", e)
    try:
        from services import personality_strategy as _ps
        _personality_strategy = _ps
    except Exception as e:  # noqa: BLE001
        logger.warning("导入 personality_strategy 失败: %s", e)
    try:
        from services import combination_playbook as _cp
        _combination_playbook = _cp
    except Exception as e:  # noqa: BLE001
        logger.warning("导入 combination_playbook 失败: %s", e)


# ====================================================================== #
# 主类
# ====================================================================== #
class AnalysisOrchestrator:
    """统一分析编排器：场景识别 → 模块选择 → 串联执行 → 结果整合。

    用法：
        orch = AnalysisOrchestrator(db=my_db, config=my_config)
        result = orch.orchestrate(person_id="xxx", chat_text="...", context={...})
    """

    def __init__(self, db: Any = None, config: Any = None) -> None:
        self._db = db
        self._config = config
        _lazy_import()
        logger.info("AnalysisOrchestrator 初始化完成 db=%s", "有" if db else "无")

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def orchestrate(self, person_id: str, chat_text: str, context: dict) -> dict:
        """执行完整编排流程。

        Args:
            person_id: 人物 ID。
            chat_text: 当前聊天文本（对方发来的消息）。
            context: 包含以下字段：
                - emotion: 情感分析结果 dict
                - llm_structured: LLM 结构化输出 dict
                - relation: 关系评估 dict
                - persona: 13维人物画像 dict（可选，没有则内部尝试从 db 加载）

        Returns:
            dict: 统一编排结果，结构见本方法末尾的 return。
        """
        modules_called: dict[str, str] = {}  # module_name -> "ok" / "failed" / "skipped" / "timeout"
        t_start = datetime.now()

        # 0. 准备 persona（优先用 context 传入的，没有则尝试从 db 加载）
        persona = context.get("persona") or {}
        if not persona and self._db and person_id:
            try:
                persona = self._load_persona_from_db(person_id) or {}
                modules_called["_persona_load"] = "ok"
            except Exception as e:  # noqa: BLE001
                logger.debug("从 db 加载画像失败: %s", e)
                modules_called["_persona_load"] = "failed"

        # 0.5 R3.3 NPD 真链路接线（验收官第三轮揪出的假修）：
        #     之前 orchestrate() 根本不跑 persona_analyzer/auto_strategy_pipeline，
        #     persona 全靠 context 手动塞——生产入口 service_facade 没传，于是 NPD
        #     识别=空、manipulation=False、反制话术=0 条。这里现场跑一遍真链路：
        #     扫最近聊天的 NPD/煤气灯信号 → 合成可用 persona → 挂反制策略与话术。
        asp_out: dict = {}
        try:
            from services import auto_strategy_pipeline as _asp  # noqa: WPS433
            recent_chats = list(context.get("recent_chats") or [])
            if chat_text:
                recent_chats.append({"role": "other", "text": chat_text})
            asp_out = _asp.run({
                "person_id": person_id,
                "recent_chats": recent_chats,
                "persona": persona or {},
                "relation_stage": 5,
            }) or {}
            modules_called["auto_strategy_pipeline"] = "ok"
        except Exception as e:  # noqa: BLE001
            modules_called["auto_strategy_pipeline"] = "failed"
            asp_out = {}
            logger.warning("auto_strategy_pipeline.run 失败（已降级，不阻断主流程）: %s", e)

        # 把现场识别出的 persona 合并进来（不覆盖 context 显式传入的键）
        asp_persona = asp_out.get("persona") if isinstance(asp_out.get("persona"), dict) else {}
        if asp_persona:
            for k, v in asp_persona.items():
                persona.setdefault(k, v)
        asp_strategy = asp_out.get("strategy") if isinstance(asp_out.get("strategy"), dict) else {}
        asp_candidates = asp_out.get("candidates") or []
        asp_six_layer = asp_out.get("six_layer") if isinstance(asp_out.get("six_layer"), dict) else {}
        asp_prediction = asp_out.get("prediction") if isinstance(asp_out.get("prediction"), dict) else {}

        # 1. 场景识别
        scene, scene_confidence = self._detect_scene(chat_text, context, person_id)
        logger.info("场景识别结果: %s (置信度 %.2f) person=%s", scene, scene_confidence, person_id)

        # 1.5 危险信号前置检测（早于模块选择）：对方消息中的自伤/轻生风险 →
        #     写入 persona，后续强制启用定律模块以触发维特效应（识别+预防）。
        try:
            if _psychology_laws is not None:
                sh = _psychology_laws.detect_self_harm_risk(chat_text or "")
                if sh.get("detected"):
                    persona["self_harm_risk"] = sh
        except Exception as e:  # noqa: BLE001
            logger.debug("自伤风险前置检测失败: %s", e)

        # 2. 根据场景选择模块组合
        module_plan = self._select_modules(scene, chat_text, context)
        # 安全覆盖：检测到自伤风险时，无论什么场景都强制跑定律模块（维特效应必须出现）
        if (persona.get("self_harm_risk") or {}).get("detected"):
            module_plan["psychology_laws"] = True
            module_plan["strategy_advisor"] = True
        # 停滞覆盖：关系被标记为停滞时，强制跑定律模块（停滞重启/鲶鱼等效应需一致外露）
        if str(persona.get("relation_status", "")).lower() == "stagnant" \
                or persona.get("_orchestrator_stagnation"):
            module_plan["psychology_laws"] = True
            module_plan["strategy_advisor"] = True
        logger.info("模块选择计划: %s person=%s", module_plan, person_id)

        # 2.5 修复2：关系网络联动（关键人物影响纳入context+风险预警）
        social_influences: list = []
        try:
            from services.relationship_graph import get_influential_people, social_alert
            _rg = context.get("relationship_graph") or {"nodes": []}
            social_influences = get_influential_people(person_id, _rg)
            _sa = social_alert(social_influences)
            if _sa.get("level") in ("红", "橙"):
                context.setdefault("risk_warnings", []).append({
                    "type": "social", "level": _sa["level"],
                    "risks": _sa.get("risks", []), "source": "relationship_graph"})
            context["social_influences"] = social_influences
        except Exception as e:  # noqa: BLE001
            logger.debug("关系网络联动跳过: %s", e)

        # 3. 串联执行各模块（数据从前一个流向下一个）
        personality_result: dict = {}
        strategy_result: dict = {}
        laws_result: dict = {}
        no_reply_result: dict = {}
        commitment_result: dict = {}
        deception_result: dict = {}
        tasks_result: list[dict] = []
        risk_warnings: list[str] = []

        # 3.0 危险信号警告：基于 1.5 已写入的 persona['self_harm_risk'] 置顶安全警告
        sh = persona.get("self_harm_risk") or {}
        if sh.get("detected"):
            risk_warnings.append(
                "⚠ 安全优先：对方消息疑似出现自伤/轻生表达"
                f"（命中：{'、'.join(sh.get('hits', []))}）。"
                "请先关心'人'本身，温和引导其联系心理援助热线/到心理科就诊，"
                "此刻停止一切恋爱策略（维特效应·识别预防）。"
            )

        # 3a. 人格倾向识别（轻量，几乎所有场景都跑——它是后续策略的输入）
        if module_plan.get("personality_strategy"):
            ok, res = self._run_personality_strategy(persona)
            if ok:
                personality_result = res
                modules_called["personality_strategy"] = "ok"
                # 把人格结果注入 persona，供 strategy_advisor 使用
                if personality_result.get("primary_key"):
                    persona["_orchestrator_personality"] = personality_result.get("primary_key")
            else:
                modules_called["personality_strategy"] = self._classify_failure(res)
                logger.warning("personality_strategy 模块失败: %s", res)

        # 3b. 已读不回检测（在策略之前跑，因为它的输出要喂给策略）
        if module_plan.get("no_reply_detector"):
            ok, res = self._run_no_reply_detector(person_id, persona)
            if ok:
                no_reply_result = res
                modules_called["no_reply_detector"] = "ok"
                # 如果检测到不回复，把原因注入 persona 供 strategy_advisor 使用
                if no_reply_result.get("is_no_reply"):
                    persona["_orchestrator_no_reply_reason"] = no_reply_result.get("reason", "")
                    persona["_orchestrator_no_reply_severity"] = no_reply_result.get("severity", "")
            else:
                modules_called["no_reply_detector"] = self._classify_failure(res)
                logger.warning("no_reply_detector 模块失败: %s", res)

        # 3c. 反伪装审计（在策略之前跑，风险等级影响策略强度）
        if module_plan.get("deception_detector"):
            ok, res = self._run_deception_detector(person_id, persona)
            if ok:
                deception_result = res
                modules_called["deception_detector"] = "ok"
                # 把风险等级注入 persona 供 strategy_advisor 使用
                risk_level = deception_result.get("risk_level", "")
                if risk_level in ("高", "极高"):
                    persona["deception_risk"] = risk_level
                    risk_warnings.append(
                        f"反伪装审计提示：当前风险等级{risk_level}，建议加强观察、放缓推进节奏"
                    )
            else:
                modules_called["deception_detector"] = self._classify_failure(res)
                logger.warning("deception_detector 模块失败: %s", res)

        # 3c.5 杀猪盘专项检测（R3.3-33，独立于 deception_detector 的第二道）
        #     只加 warning 字段，不替代反伪装审计；confidence>0.6 才置顶警告。
        scam_result: dict = {}
        try:
            scam_msgs = [
                (m.get("content") or "")
                for m in self._load_recent_messages(person_id, limit=60)
            ]
            scam_msgs = [m for m in scam_msgs if m.strip()]
            if scam_msgs:
                from services.scam_patterns import detect_scam_stage
                _scam = detect_scam_stage(scam_msgs)
                scam_result = _scam.to_dict()
                modules_called["scam_patterns"] = "ok"
                if _scam.confidence > 0.6 and not _scam.data_insufficient:
                    risk_warnings.append(
                        f"⚠ 杀猪盘专项检测：当前对话聚集了 {_scam.confidence:.0%} "
                        f"疑似「{_scam.pattern}」信号（{_scam.stage or ''}）。"
                        f"建议：暂不转账、不下载对方指定 APP、涉及金钱一律线下核实。"
                        f"（{_scam.advice}）"
                    )
        except Exception as e:  # noqa: BLE001 - 杀猪盘检测失败不影响主流程
            modules_called["scam_patterns"] = "failed"
            logger.debug("杀猪盘专项检测失败: %s", e)

        # 3d. 策略适配矩阵（依赖前面的人格/不回复/伪装结果）
        if module_plan.get("strategy_advisor"):
            relation_stage = self._extract_relation_stage(context)
            ok, res = self._run_strategy_advisor(persona, relation_stage)
            if ok:
                strategy_result = res
                modules_called["strategy_advisor"] = "ok"
                # Utility+Contract Net 融合决策（增量；失败回退到原 strategy_advisor 输出）
                try:
                    from services.strategy_utility import fused_choose
                    _state = {
                        "attachment": (persona or {}).get("attachment_style")
                                      or (persona or {}).get("attachment", ""),
                        "emotion_pad": (persona or {}).get("emotion_pad", {}),
                        "trend": (persona or {}).get("trend", ""),
                        "loss_index": (persona or {}).get("loss_aversion", {}).get("loss_index"),
                        "manipulation": bool((persona or {}).get("manipulation_risk")),
                    }
                    _fusion = fused_choose(_state, history_effects={},
                                           high_risk=bool((persona or {}).get("manipulation_risk")))
                    strategy_result["fusion"] = _fusion
                    modules_called["contract_net"] = "ok"
                except Exception as _e:
                    logger.debug("Contract Net 融合决策失败，回退原策略: %s", _e)
            else:
                modules_called["strategy_advisor"] = self._classify_failure(res)
                logger.warning("strategy_advisor 模块失败: %s", res)

        # 3e. 心理学定律匹配（依赖 strategy_advisor 提取的 dims）
        if module_plan.get("psychology_laws"):
            relation_stage = self._extract_relation_stage(context)
            dims = strategy_result.get("_dims") if strategy_result else None
            ok, res = self._run_psychology_laws(persona, relation_stage, dims)
            if ok:
                laws_result = res
                modules_called["psychology_laws"] = "ok"
            else:
                modules_called["psychology_laws"] = self._classify_failure(res)
                logger.warning("psychology_laws 模块失败: %s", res)

        # 3e.5 效应组合协同方案：把命中的定律按场景组合成「有序分工方案」，
        #     而不是简单罗列（先做什么/后做什么/谁打底/谁收尾）。
        combination_result: dict = {}
        try:
            relation_stage = self._extract_relation_stage(context)
            combo_dims = strategy_result.get("_dims") if strategy_result else None
            combination_result = self._build_combination(
                scene, relation_stage, persona, combo_dims, laws_result,
            )
            if combination_result.get("playbooks"):
                modules_called["combination_playbook"] = "ok"
        except Exception as e:  # noqa: BLE001 - 组合方案失败不影响主流程
            modules_called["combination_playbook"] = "failed"
            logger.warning("组合方案构建失败: %s", e)

        # 3f. 承诺履约跟踪
        if module_plan.get("commitment_tracker"):
            ok, res = self._run_commitment_tracker(person_id, persona)
            if ok:
                commitment_result = res
                modules_called["commitment_tracker"] = "ok"
                if commitment_result.get("newly_broken", 0) > 0:
                    risk_warnings.append(
                        f"承诺履约：新检测到 {commitment_result['newly_broken']} 条未兑现承诺，"
                        "建议观察对方兑现习惯"
                    )
                if commitment_result.get("newly_deceived", 0) > 0:
                    risk_warnings.append(
                        f"承诺履约：新检测到 {commitment_result['newly_deceived']} 条反悔/否认，"
                        "这是较严重的信任风险信号"
                    )
            else:
                modules_called["commitment_tracker"] = self._classify_failure(res)
                logger.warning("commitment_tracker 模块失败: %s", res)

        # 3g. 整合任务建议（从各模块的任务生成器汇总）
        tasks_result = self._collect_tasks(
            personality=personality_result,
            no_reply=no_reply_result,
            laws=laws_result,
            persona=persona,
        )

        # 3h. 组合方案任务：按方案顺序补充组合任务（最多取主方案前5条，防爆炸）
        for pb in combination_result.get("playbooks", [])[:1]:
            for t in pb.get("combined_tasks", [])[:5]:
                tasks_result.append({
                    "source": "combination_playbook",
                    "title": t,
                    "note": f"组合方案「{pb.get('playbook_name','')}」",
                })

        # 3i. 操控识别 → 反制 + 引导教练（手段中立，单模块失败不崩溃）
        manipulation_result: dict = {}
        try:
            from services.manipulation_coach import build_counter_manipulation
            manipulation_result = build_counter_manipulation(
                persona=persona,
                finance_risk_flags=(context.get("finance") or {}).get("risk_flags", []),
            ) or {}
            if manipulation_result.get("triggered"):
                for s in manipulation_result.get("counter_strategies", [])[:4]:
                    tasks_result.append({
                        "source": "manipulation_coach",
                        "title": f"反制·{s.get('principle','')}：{s.get('how','')[:50]}",
                        "note": f"效果：{s.get('effect','')[:30]}；对方可能：{s.get('likely_reaction','')[:30]}",
                    })
                risk_warnings.append("检测到操控模式（煤气灯/金钱控制/辱骂）："
                                     "已生成非对抗反制步骤与引导方案，详见 manipulation 输出")
        except Exception as e:  # noqa: BLE001
            logger.debug("manipulation_coach 失败: %s", e)
            manipulation_result = {}

        # 3l. 数字分身反应预测（单轮分析面板「预测」字段，缺数据则 None→UI 显示 —）
        prediction_result: dict = {}
        try:
            if self.digital_twin is not None:
                recent_chats = [
                    (m.get("content") or "")
                    for m in self._load_recent_messages(person_id, limit=20)
                ]
                affection = self._load_affection(person_id)
                pred = self.digital_twin.predict_reaction(
                    user_action=user_message or "",
                    recent_chats=recent_chats,
                    affection={},  # 好感度由 digital_twin 内部按 person_id 自取
                    context_hint=(strategy_result.get("recommended", [""])
                                  if strategy_result else [""])[0],
                )
                if isinstance(pred, dict) and pred:
                    prediction_result = pred
                    modules_called["digital_twin.predict_reaction"] = "ok"
        except Exception as e:  # noqa: BLE001
            modules_called["digital_twin.predict_reaction"] = "failed"
            logger.debug("数字分身反应预测失败: %s", e)

        # 4. 综合风险预警：把各模块的警告合并
        risk_warnings.extend(self._collect_risk_warnings(
            no_reply=no_reply_result,
            deception=deception_result,
            strategy=strategy_result,
        ))

        elapsed = (datetime.now() - t_start).total_seconds()
        logger.info(
            "编排完成 person=%s scene=%s modules=%d elapsed=%.2fs",
            person_id, scene, len([k for k, v in modules_called.items() if v == "ok"]), elapsed,
        )

        # 5. 统一输出
        return {
            "scene": {
                "name": scene,
                "confidence": scene_confidence,
                "subtext": context.get("_structural_subtext", ""),
                "structural_flags": {
                    "icebreak": bool(context.get("_structural_icebreak")),
                    "proposal_rejection_loop": bool(context.get("_structural_loop")),
                },
            },
            "personality": {
                "primary_key": personality_result.get("primary_key", "") or asp_persona.get("primary_key", ""),
                "primary_label": personality_result.get("primary_label", "") or asp_persona.get("primary_label", ""),
                "primary_score": personality_result.get("primary_score", 0.0),
                "primary_personality": (
                    asp_persona.get("primary_personality", "")
                    or personality_result.get("primary_label", "")
                ),
                "runner_up": personality_result.get("runner_up"),
                "note": personality_result.get("note", ""),
                "all_tendencies": personality_result.get("all_tendencies", []),
                "npd_signal_boost": bool(asp_persona.get("_npd_signal_boost")),
                "npd_signal_hits": asp_persona.get("_npd_signal_hits", []),
            },
            "strategy": {
                "recommended": strategy_result.get("recommended", []),
                "forbidden": strategy_result.get("forbidden", []),
                "reasoning": strategy_result.get("reasoning", ""),
                "style_tips": strategy_result.get("style_tips", ""),
                "conflict_approach": strategy_result.get("conflict_approach", ""),
                # R3.3 NPD：auto_strategy 现场挂的反制操纵策略（含 triggered/detected_signals）
                "manipulation_counter": asp_strategy.get("manipulation_counter", {}),
            },
            "psychology_laws": {
                "hits_count": laws_result.get("hits_count", 0),
                "laws": laws_result.get("laws", []),
                "reasoning_lines": laws_result.get("reasoning_lines", ""),
            },
            # 效应组合协同方案：按场景组合成的有序分工（非定律罗列）
            "combination": combination_result.get("primary"),
            "combinations": combination_result.get("playbooks", []),
            "combination_summary": combination_result.get("summary", ""),
            "no_reply": no_reply_result if no_reply_result else None,
            "commitment": commitment_result if commitment_result else None,
            # R3.3-33 杀猪盘专项检测（高风险时已在 risk_warnings 置顶警告）
            "scam": scam_result or None,
            "deception": {
                "risk_score": deception_result.get("risk_score", 0),
                "risk_level": deception_result.get("risk_level", ""),
                "recommendation": deception_result.get("recommendation", ""),
                "warnings": deception_result.get("warnings", []),
            } if deception_result else None,
            "tasks": tasks_result,
            # R3.3 NPD：3 条可直接复制的反制话术（auto_strategy 现场产出）
            "candidates": asp_candidates,
            # R3.3-45 六层人格画像摘要（auto_strategy 现场产出）
            "six_layer": asp_six_layer or None,
            # 单轮分析面板「预测」字段：数字分身对本轮的反应预测（缺数据→None→UI 显示 —）
            "prediction": prediction_result or asp_prediction or None,
            # 单轮分析面板「风险」字段：合并反伪装+杀猪盘警告的人读摘要
            "risk": ("；".join(str(w) for w in risk_warnings[:3])) if risk_warnings else "",
            "risk_warnings": risk_warnings,
            "manipulation": manipulation_result or None,
            "modules_called": modules_called,
        }

    # ------------------------------------------------------------------ #
    # 1. 场景识别
    # ------------------------------------------------------------------ #
    def _detect_scene(self, chat_text: str, context: dict, person_id: str) -> tuple[str, float]:
        """根据聊天文本 + 情感 + LLM输出 + 回复间隔，识别当前场景。

        Returns:
            (scene_name, confidence): 场景名 + 置信度 0.0-1.0。
        """
        text = chat_text or ""
        emotion = context.get("emotion") or {}
        llm = context.get("llm_structured") or {}
        emotion_label = str(emotion.get("label", ""))

        # —— 结构/时序上下文优先（在字面关键词之前）—— #
        structural = self._detect_structural_context(text, person_id)
        if structural.get("icebreak"):
            context["_structural_subtext"] = structural["subtext"]
            context["_structural_icebreak"] = True
            return "冲突争吵", 0.88
        if structural.get("proposal_rejection_loop"):
            context["_structural_subtext"] = structural["subtext"]
            context["_structural_loop"] = True
            return "冲突争吵", 0.86

        # 优先级从高到低匹配（冲突 > 分手 > 表白 > 金钱 > 已读不回 > 邀约 > 暧昧 > 冷淡 > 主动 > 日常）
        # 分手危机（最高优先级——和冲突有重叠但更严重）
        if _contains_any(text, _SCENE_BREAKUP_WORDS):
            return "分手危机", 0.9

        # 冲突争吵
        if _contains_any(text, _SCENE_CONFLICT_WORDS) or emotion_label in ("angry", "sad"):
            return "冲突争吵", 0.85

        # 情感表白
        if _contains_any(text, _SCENE_CONFESSION_WORDS):
            return "情感表白", 0.9

        # 金钱往来
        if _contains_any(text, _SCENE_MONEY_WORDS):
            return "金钱往来", 0.85

        # 邀约
        if _contains_any(text, _SCENE_INVITE_WORDS):
            return "邀约", 0.8

        # 已读不回：这个需要查数据库判断，先标记为"待检测"
        # 在 _select_modules 里会实际跑 no_reply_detector 来确认
        # 冷淡/敷衍：短回复或典型敷衍词（即使带标点超过5字），且无更具体场景信号
        if _contains_any(text, _SCENE_COLDDOWN_WORDS) and len(text.strip()) <= 12:
            # 交给 no_reply_detector / strategy 在模块选择阶段细化
            pass

        # 暧昧推进
        if _contains_any(text, _SCENE_FLIRT_WORDS) or emotion_label in ("happy", "excited"):
            return "暧昧推进", 0.75

        # 对方冷淡（短回复+敷衍词；放宽长度以覆盖"哦，到时候看"这类带标点敷衍）
        if _contains_any(text, _SCENE_COLDDOWN_WORDS) and len(text.strip()) <= 12:
            return "对方冷淡", 0.7

        # 默认：日常闲聊
        return "日常闲聊", 0.5

    # ------------------------------------------------------------------ #
    # 2. 模块选择矩阵
    # ------------------------------------------------------------------ #
    def _select_modules(self, scene: str, chat_text: str, context: dict) -> dict:
        """根据场景决定跑哪些模块。

        Returns:
            dict: {module_name: bool}，True=跑，False=跳过。
        """
        plan = {
            "personality_strategy": True,   # 人格识别很轻量，几乎所有场景都跑
            "strategy_advisor": False,
            "psychology_laws": False,
            "no_reply_detector": False,
            "commitment_tracker": False,
            "deception_detector": False,
        }

        if scene == "日常闲聊":
            # 轻量模式：只跑人格，其他跳过
            plan["personality_strategy"] = True

        elif scene == "暧昧推进":
            plan["personality_strategy"] = True
            plan["strategy_advisor"] = True
            plan["psychology_laws"] = True

        elif scene == "冲突争吵":
            plan["personality_strategy"] = True
            plan["strategy_advisor"] = True
            plan["psychology_laws"] = True
            plan["deception_detector"] = True

        elif scene == "对方冷淡":
            plan["personality_strategy"] = True
            plan["strategy_advisor"] = True
            plan["no_reply_detector"] = True

        elif scene == "已读不回":
            plan["personality_strategy"] = True
            plan["no_reply_detector"] = True
            plan["strategy_advisor"] = True
            # 已读不回场景下跳过细粒度情感分析（由 no_reply_detector 主导）

        elif scene == "对方主动":
            plan["personality_strategy"] = True
            plan["strategy_advisor"] = True

        elif scene == "邀约":
            plan["personality_strategy"] = True
            plan["strategy_advisor"] = True
            plan["psychology_laws"] = True
            plan["commitment_tracker"] = True

        elif scene == "金钱往来":
            plan["personality_strategy"] = True
            plan["deception_detector"] = True
            plan["commitment_tracker"] = True
            plan["strategy_advisor"] = True

        elif scene == "情感表白":
            plan["personality_strategy"] = True
            plan["strategy_advisor"] = True
            plan["psychology_laws"] = True

        elif scene == "分手危机":
            plan["personality_strategy"] = True
            plan["strategy_advisor"] = True
            plan["psychology_laws"] = True
            plan["deception_detector"] = True
            plan["no_reply_detector"] = True

        # 附加条件：如果文本中出现承诺关键词，补跑 commitment_tracker
        if _contains_any(chat_text or "", _SCENE_COMMITMENT_WORDS):
            plan["commitment_tracker"] = True

        return plan

    # ------------------------------------------------------------------ #
    # 3. 各模块执行封装（带超时+异常保护）
    # ------------------------------------------------------------------ #
    def _run_personality_strategy(self, persona: dict) -> tuple[bool, dict]:
        """执行人格倾向识别 + 策略生成。"""
        if _personality_strategy is None:
            return False, ImportError("personality_strategy 模块未导入")

        def _call():
            detect = _personality_strategy.detect_personality(persona or {})
            recs, forbids, note, key = _personality_strategy.build_strategy(persona or {})
            tend = _personality_strategy.primary_tendency(persona or {})
            result = {
                "primary_key": key or "",
                "primary_label": tend.get("label", "") if tend else "",
                "primary_score": tend.get("score", 0.0) if tend else 0.0,
                "runner_up": tend.get("runner_up") if tend else None,
                "note": note,
                "recommended": recs,
                "forbidden": forbids,
                "all_tendencies": [
                    {"key": r["key"], "label": r["label"], "score": r["score"]}
                    for r in detect if r["score"] > 0
                ],
            }
            return result

        ok, res = _run_with_timeout(_call)
        return ok, res if ok else res

    def _run_strategy_advisor(self, persona: dict, relation_stage: int) -> tuple[bool, dict]:
        """执行策略适配矩阵。"""
        if _StrategyAdvisor is None:
            return False, ImportError("StrategyAdvisor 未导入")

        def _call():
            advisor = _StrategyAdvisor(db=self._db)
            return advisor.analyze(persona or {}, relation_stage=relation_stage)

        ok, res = _run_with_timeout(_call)
        return ok, res if ok else res

    def run_evidence_engine(self, person_id: str, persona: Optional[dict] = None) -> tuple[bool, dict]:
        """最小侵入接入：对该人物跑"证据锚定六层引擎"。

        - 作为外层包裹，消费 PersonaAnalyzer 画像(persona)，不改其四大修复逻辑；
        - 任何异常都被捕获并返回 (False, err)，绝不影响既有编排主流程；
        - 无 db / 无 person_id 时返回 (False, reason)。
        """
        if not self._db or not person_id:
            return False, {"reason": "db 或 person_id 缺失"}
        try:
            from services.evidence_engine import EvidenceProfileEngine
            engine = EvidenceProfileEngine(db=self._db)
            result = engine.analyze(person_id, profile=persona or {})
            return True, result
        except Exception as e:  # noqa: BLE001 - 增强项，失败不阻断主流程
            logger.warning("证据锚定六层引擎运行失败 person=%s: %s", person_id, e)
            return False, {"reason": str(e)}

    def _run_psychology_laws(self, persona: dict, stage: int, dims: Optional[dict]) -> tuple[bool, dict]:
        """执行心理学定律匹配。"""
        if _psychology_laws is None:
            return False, ImportError("psychology_laws 未导入")

        def _call():
            hits = _psychology_laws.match_laws(persona or {}, stage=stage, dims=dims)
            reasoning = _psychology_laws.law_reasoning_lines(hits) if hits else ""
            return {
                "hits_count": len(hits),
                "laws": [
                    {"id": h.get("id", ""), "name": h.get("name", ""), "strategy": h.get("strategy", "")}
                    for h in hits
                ],
                "reasoning_lines": reasoning,
            }

        ok, res = _run_with_timeout(_call)
        return ok, res if ok else res

    def _build_combination(self, scene: str, stage: int, persona: dict,
                           dims: Optional[dict], laws_result: dict) -> dict:
        """构建效应「组合协同方案」。

        不是把命中的定律罗列出来，而是按当前场景/阶段，从预置的组合方案库中
        选出匹配的方案，标注每个效应的分工/顺序/是否当前命中，并生成组合任务。

        Returns:
            {"primary": dict|None, "playbooks": list, "summary": str}
        """
        if _combination_playbook is None or _psychology_laws is None:
            return {"primary": None, "playbooks": [], "summary": ""}
        # 重新取完整 hits（含 tasks），用于组合方案标注与任务提取
        try:
            hits = _psychology_laws.match_laws(persona or {}, stage=stage, dims=dims)
        except Exception as e:  # noqa: BLE001
            logger.debug("组合方案：match_laws 失败: %s", e)
            hits = []

        try:
            playbooks = _combination_playbook.match_playbooks(
                scene=scene, stage=stage, dims=dims or {}, persona=persona or {}, hits=hits,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("组合方案匹配失败: %s", e)
            playbooks = []

        summary = _combination_playbook.primary_playbooks_summary(playbooks) if playbooks else ""
        return {
            "primary": playbooks[0] if playbooks else None,
            "playbooks": playbooks,
            "summary": summary,
        }

    def _run_no_reply_detector(self, person_id: str, persona: dict) -> tuple[bool, dict]:
        """执行已读不回检测。"""
        if _NoReplyDetector is None:
            return False, ImportError("NoReplyDetector 未导入")

        def _call():
            detector = _NoReplyDetector(db=self._db)
            # 先检测场景
            scenario = detector.detect_no_reply_scenario(person_id)
            result = dict(scenario)
            # 如果确实是不回复场景，进一步分类原因 + 策略 + 任务
            if scenario.get("is_no_reply"):
                # 原因分类（用最近消息）
                pattern = detector.analyze_reply_pattern(person_id, recent_days=30)
                result["pattern"] = {
                    "available": pattern.get("available", False),
                    "mean_lag_min": pattern.get("mean_lag_min", 0),
                    "proactive_ratio": pattern.get("proactive_ratio", 0),
                }
                # 分类原因
                # classify_reason 需要 messages + scenario，这里直接调用
                try:
                    messages = detector._load_messages(person_id, days=90)
                    reason = detector.classify_reason(messages, scenario, pattern)
                    result["reason"] = reason
                    result["reason_label"] = getattr(detector, "_REASON_LABELS", {}).get(reason, reason)
                    # 策略
                    strategy = detector.get_strategy(reason, scenario.get("severity", "轻度"), persona)
                    result["strategy"] = strategy
                    # 任务
                    days_since = int(scenario.get("waiting_hours", 0) / 24) if scenario.get("waiting_hours") else 0
                    tasks = detector.get_tasks(reason, scenario.get("severity", "轻度"), days_since)
                    result["tasks"] = tasks
                except Exception as e:  # noqa: BLE001
                    logger.debug("不回复原因分类失败: %s", e)
                    result["reason"] = "unknown"
            return result

        ok, res = _run_with_timeout(_call)
        return ok, res if ok else res

    def _run_commitment_tracker(self, person_id: str, persona: dict) -> tuple[bool, dict]:
        """执行承诺履约扫描。"""
        if _CommitmentTracker is None:
            return False, ImportError("CommitmentTracker 未导入")

        def _call():
            tracker = _CommitmentTracker(db=self._db)
            # 先加载最近消息
            recent_msgs = self._load_recent_messages(person_id, limit=50)
            scan_result = tracker.scan_and_update(person_id, recent_msgs)
            # 顺便拿兑现率
            try:
                rate = tracker.get_fulfillment_rate(person_id, days=90)
                scan_result["fulfillment_rate"] = rate
            except Exception:
                pass
            return scan_result

        ok, res = _run_with_timeout(_call)
        return ok, res if ok else res

    def _run_deception_detector(self, person_id: str, persona: dict) -> tuple[bool, dict]:
        """执行反伪装审计。"""
        if _DeceptionDetector is None:
            return False, ImportError("DeceptionDetector 未导入")

        def _call():
            detector = _DeceptionDetector(db=self._db)
            messages = self._load_recent_messages(person_id, limit=200)
            return detector.analyze(person_id, messages)

        ok, res = _run_with_timeout(_call)
        return ok, res if ok else res

    # ------------------------------------------------------------------ #
    # 4. 辅助方法
    # ------------------------------------------------------------------ #
    def _extract_relation_stage(self, context: dict) -> int:
        """从 context 提取关系阶段（0-10），默认 5。"""
        relation = context.get("relation") or {}
        stage = relation.get("stage")
        if isinstance(stage, (int, float)):
            return max(0, min(10, int(stage)))
        # LLM 输出里 stage 可能是字符串
        if isinstance(stage, str):
            import re
            m = re.search(r"(\d+)", stage)
            if m:
                return max(0, min(10, int(m.group(1))))
        return 5

    def _load_recent_messages(self, person_id: str, limit: int = 50) -> list[dict]:
        """从数据库加载最近消息（参数化查询，防注入）。"""
        if not self._db or not person_id:
            return []
        try:
            rows = self._db.fetch_all(
                "SELECT direction, content, created_at FROM chat_messages "
                "WHERE person_id=:pid ORDER BY created_at DESC LIMIT :lim",
                {"pid": person_id, "lim": limit},
            )
            # 反转成时间升序
            result = list(rows or [])
            result.reverse()
            return result
        except Exception as e:  # noqa: BLE001
            logger.warning("加载最近消息失败 person=%s: %s", person_id, e)
            return []

    def _detect_structural_context(self, current_text: str, person_id: str) -> dict:
        """基于完整消息序列的时序/结构特征识别（字面关键词之外的模式）。

        识别两类：
        - icebreak：冲突/冷战后对方嘴硬破冰（'死了没''花浇了没'=递台阶，不是攻击）
        - proposal_rejection_loop：连续被否定所有提议却只说'随便'（挑剔/作，不是抑郁）
        """
        out = {"icebreak": False, "proposal_rejection_loop": False, "subtext": ""}
        msgs = self._load_recent_messages(person_id, limit=40)
        if not msgs or len(msgs) < 2:
            return out
        contents = [(m["direction"], (m["content"] or "").strip()) for m in msgs]

        # 1) 提议-否定循环
        sent_proposals = sum(1 for d, c in contents if d == "sent" and _contains_any(c, _STRUCT_PROPOSAL_HINTS))
        recv_rejections = sum(1 for d, c in contents if d == "received" and _contains_any(c, _STRUCT_REJECT_HINTS))
        recv_suibian = sum(1 for d, c in contents if d == "received" and "随便" in c)
        if sent_proposals >= 2 and recv_rejections >= 2 and recv_suibian >= 1:
            out["proposal_rejection_loop"] = True
            out["subtext"] = (
                "连续被否定所有提议却不给出自己的选项（'随便'+太腻/太淡/不喜欢），"
                "真实诉求不是决定吃什么，而是要你耐心哄、确认你在乎（作·索取关注）；"
                "尾段转向'嫌我烦/男人都一个样/你吼我'是借题发挥要情绪价值，不是抑郁退缩。")
            return out

        # 2) 嘴硬破冰：粗口质问 或 嘴硬否认+共同对象借口，且附近有补偿/关心动作
        recent_text = " ".join(c for _, c in contents)
        harsh = _contains_any(current_text, _STRUCT_HARSH_OPEN) or _contains_any(recent_text, _STRUCT_HARSH_OPEN)
        denial_excuse = _contains_any(current_text, _STRUCT_DENIAL_EXCUSE)
        common_obj = _contains_any(current_text, _STRUCT_COMMON_OBJECT)
        compensation = _contains_any(recent_text, _STRUCT_COMPENSATION) or _contains_any(current_text, _STRUCT_COMPENSATION)
        vulnerable = _contains_any(current_text, _STRUCT_VULNERABLE)
        hist_conflict = any(_contains_any(c, _SCENE_CONFLICT_WORDS) for _, c in contents[:-1])

        # 2a 脆弱性自我暴露：愤怒/冷战后的掏心窝 = 双向修复信号（4B）
        if vulnerable:
            out["icebreak"] = True
            out["subtext"] = (
                "这句'特别害怕/一个人'不是攻击，而是愤怒背后的深层需求——独自面对压力的恐惧与委屈，"
                "是在向你'转向'、寻求情感支撑；配合此前的'对不起'属双向修复。"
                "策略：先接住情绪（不是急着讲理/分责），让TA感到被陪着，再谈事情本身。")
            return out

        # 2b 借日常事务递/接台阶：冷战后给饭/留碗/热粥/盖被 = 用琐事给台阶（4A/4C）
        domestic = _contains_any(current_text, _STRUCT_COMPENSATION)
        if domestic and (harsh or hist_conflict or compensation):
            out["icebreak"] = True
            out["subtext"] = (
                "'碗筷在抽屉''厨房有粥自己热''厚被子在衣柜'是嘴硬/平淡措辞下的接纳与照顾——"
                "冷战后用日常事务递/接台阶（给饭吃、惦记你冷热），不是真的嫌你烦。"
                "策略：顺势接住台阶、不用等对方正式道歉，自然和好、看见这份默默照顾。")
            return out

        if (harsh or denial_excuse or common_obj) and compensation:
            out["icebreak"] = True
            out["subtext"] = (
                "'死了没'/'花浇了没'/'谁要喊你回家'表面是质问、拙劣借口或嘴硬否认，"
                "实际是冲突/冷战后对方先低头、给你递台阶（嘴硬心软）；"
                "紧随的补偿动作（买你想要的东西/默默浇花/换矿泉水/问手怎么了）确认是想和好。"
                "应接住台阶、顺势和好、看见隐性付出，而非较真措辞或升级矛盾。")
        elif (harsh or denial_excuse) and hist_conflict:
            out["icebreak"] = True
            out["subtext"] = (
                "粗口/质问式开场出现在冲突之后，是破冰方嘴硬递台阶，不是继续攻击；"
                "策略应温和接住、顺势缓和。")
        return out

    def _load_persona_from_db(self, person_id: str) -> dict:
        """尝试从数据库加载已有的画像数据（如果有的话）。"""
        if not self._db or not person_id:
            return {}
        try:
            # 尝试调用 persona_analyzer（如果通过 db 能访问到的话）
            # 这里只做轻量尝试：查 persons 表的基本字段
            row = self._db.fetch_one(
                "SELECT soul_path FROM persons WHERE id=:pid", {"pid": person_id}
            )
            if row:
                return {"_source": "db", "soul_path": row.get("soul_path", "")}
            return {}
        except Exception:
            return {}

    def _collect_tasks(self, personality: dict, no_reply: dict,
                       laws: dict, persona: dict) -> list[dict]:
        """从各模块汇总任务建议。"""
        tasks: list[dict] = []

        # 人格策略任务
        if personality.get("all_tendencies") and _personality_strategy:
            try:
                note, titles = _personality_strategy.build_tasks(persona or {})
                for t in titles:
                    tasks.append({"source": "personality", "title": t, "note": note})
            except Exception:
                pass

        # 心理学定律任务（软上限：避免命中几十条定律时任务爆炸；组合方案任务另行补充）
        if laws.get("hits_count", 0) > 0 and _psychology_laws:
            try:
                note, titles = _psychology_laws.build_law_tasks(persona or {}, stage=5)
                for t in titles[:6]:
                    tasks.append({"source": "psychology_laws", "title": t, "note": note})
            except Exception:
                pass

        # 已读不回任务
        if no_reply.get("tasks"):
            for t in no_reply["tasks"]:
                if isinstance(t, dict):
                    tasks.append({
                        "source": "no_reply",
                        "title": t.get("title", ""),
                        "action": t.get("action", ""),
                        "timeline": t.get("timeline", ""),
                    })

        return tasks

    def _collect_risk_warnings(self, no_reply: dict, deception: dict,
                               strategy: dict) -> list[str]:
        """汇总各模块的风险预警。"""
        warnings: list[str] = []

        if no_reply and no_reply.get("is_no_reply"):
            sev = no_reply.get("severity", "")
            reason = no_reply.get("reason_label", no_reply.get("reason", ""))
            warnings.append(f"已读不回：当前为{sev}不回复场景，原因倾向：{reason}")

        if deception and deception.get("risk_level") in ("高", "极高"):
            warnings.append(
                f"反伪装风险：等级{deception.get('risk_level')}"
                f"（评分{deception.get('risk_score', 0)}），"
                f"建议：{deception.get('recommendation', '')}"
            )

        # strategy 里的 behavior_inconsistent
        if strategy and strategy.get("behavior_inconsistent"):
            warnings.append("行为一致性检测：对方自述与行为存在不一致，建议以行为实证为参考")

        return warnings

    @staticmethod
    def _classify_failure(res: Any) -> str:
        """根据异常对象分类失败原因。"""
        if res is None:
            return "timeout"
        if isinstance(res, Exception):
            return "failed"
        return "failed"
