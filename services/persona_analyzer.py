"""
人物心理画像分析器
从头到尾分析聊天记录，结合心理学框架生成人物画像。

口径约定（P1，2026-10-13）：
  - 撤回消息（chat_messages.is_withdrawn=1，微信 <sysmsg type="revokemsg"> 系统通知）：
    不携带对话语义，不参与任何情感/关键词/冲突/修复分析；message_count 仍保留全量入库数，
    情感分析走非撤回子集，撤回消息单独计为 withdrawn_count。
  - 关键指标口径文档化：profile.metric_definitions 为 relationship_satisfaction 与
    conflict_repair 各挂 definition/basis/confidence/note，不留裸奔数字。
  - 冲突修复率窗口：repair_window_days 默认 3 天（可配置），冲突词表/修复词表可注入。
"""
import re
import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class PersonaAnalyzer:
    """人物心理画像分析器。

    分析维度（17维，10个严格学术 + 4个可量化学术 + 3个实践工具）：
      严格学术（10）：
        1. 依恋类型（Bowlby/Ainsworth）
        2. 沟通模式（Gottman四骑士/Christensen追逃）
        3. 关系阶段（Knapp/Levinger）
        4. 心理特征（Beck认知扭曲/黑暗三角）
        5. 情绪模式（Gross情绪调节/大五神经质）
        6. 投入程度（Rusbult投资模型）
        7. 冲突处理模式（Gottman/Rahim）
        8. 边界感（Bowen自我分化）
        9. 新鲜感（Aron自我扩展理论）
        10. 风险预警（IPV/出轨/煤气灯效应研究）
      可量化学术（4）：
        11. 关系满意度（Rusbult投资模型量化）
        12. 关系承诺度（Rusbult投资模型）
        13. 积极/消极互动比（Gottman 5:1比率）
        14. 冲突频率与修复成功率（Gottman修复尝试）
      实践工具（3）：
        15. 爱的五种语言（Chapman，实践工具）
        16. 权力格局（社会交换理论应用）
        17. 情感账户（Covey隐喻，对应满意度积累消耗）
    """

    # ===== 三维依恋评分关键词（v2 重写：行为60% + 关键词40%）=====
    # 维度1：回避倾向（需要空间/情感退缩/回避冲突）
    _AVOID_KW = [
        "我需要空间", "别逼我", "不想聊这个", "我习惯一个人", "别管我",
        "给我点时间", "我想静静", "别粘着我", "你能不能独立", "没什么好说的",
        "就这样吧", "别想太多", "随便你", "我一个人挺好", "我不太会表达感情",
        # NPD/隐蔽型回避：不直接说要空间，而是打太极/贬低/冷处理
        "随你怎么说", "跟你说不清楚", "别问我", "我怎么知道", "你又不懂我",
        "你总是这样", "你爱怎么想怎么想", "谁天天盯着手机", "你管好你自己",
        "你能不能别", "很掉价", "掉价", "算了", "无所谓",
    ]
    # 维度2：焦虑倾向（需要确认/害怕被抛弃/过度敏感）
    # v3 扩充：以 BPD 真实语料为准（抛弃恐惧/冲动强迫/反复确认），
    # 全部为较长的具体短语，避免短碎片误伤 NPD/安全型。
    _ANXIOUS_KW = [
        "你是不是不爱我了", "你是不是烦我了", "你去哪里了", "为什么不回我",
        "我好怕失去你", "你还在乎我吗", "别离开我", "没有安全感", "你是不是不要我了",
        "你还在吗", "你是不是生气了", "我怕你离开", "你在干嘛", "是不是我哪里不好",
        # —— BPD 抛弃恐惧 / 冲动强迫表达（取自真实语料，逐字命中）——
        "你不会离开我吧", "你走了我真的活不下去", "你离开我我活不下去", "你千万不要走",
        "你不要我了", "梦到你不要我了", "你跟他们一样", "都会离开我",
        "你是不是喜欢上别人", "你根本就不在乎我", "你根本不在乎",
        "你就是不想理我", "你又不想跟我说话", "你果然还是不愿意",
        "你每次都这样", "你每次都这么说", "你现在才回", "回那么慢",
        "你半小时没回", "你知道我等了你多久", "你能过来陪我吗",
        "我现在就要见你", "你证明给我看", "你答应我", "我就在这等", "你下来",
    ]
    # 维度3：安全基底（信任他人/情绪稳定/健康沟通）
    _SECURE_KW = [
        "我相信你", "我们好好沟通", "没关系", "我理解你", "慢慢来",
        "我们聊聊", "我支持你", "一起面对", "我也有不对", "别担心", "我在",
    ]
    # 对方"自述"依恋类型（用于行为验证：自述 vs 实证）
    _SELF_REPORT_KW = {
        "回避型": ["我是回避型", "我比较独立", "我不擅长表达", "我天生冷淡",
                  "我就是个回避型", "我不太会爱人", "我习惯自己扛"],
        "焦虑型": ["我是焦虑型", "我很黏人", "我没有安全感", "我太敏感了",
                  "我总是怕被抛弃", "我就是没安全感"],
    }
    # 冲突语境词（同一句话在吵架时=回避，日常时=正常需求，用于上下语境加权）
    _CONFLICT_WORDS = [
        "吵", "生气", "分手", "烦", "冷战", "别说了", "不想理你", "你又",
        "哭", "难过", "委屈", "吵架", "滚开", "不想说话",
    ]
    # 情感表达词（用于判断情感退缩程度）
    _EMO_WORDS = [
        "想你", "爱你", "担心", "在乎", "开心", "难过", "抱抱", "亲亲",
        "喜欢", "宝贝", "心疼", "委屈", "思念",
    ]
    # 承诺关键词（可信度维度：承诺兑现率）
    _PROMISE_KW = [
        "我答应", "我保证", "下次一定", "说好了", "我承诺", "我说到做到",
        "放心吧", "我肯定", "周末陪你", "下次陪你", "我一定改",
    ]
    # 承诺未兑现信号（对方爽约 / 我方抱怨）
    _PROMISE_BREAK_KW = [
        "忘了", "下次再说", "没去成", "又没去", "算了这次", "临时有事去不了",
        "你答应我的呢", "你又没兑现", "说好的又", "你总是说说而已", "你说的和做的不一样",
        "又食言", "说话不算数",
    ]
    # 言行不一信号（说在乎但行为冷漠）
    _INCONSISTENT_WORDS = [
        "我很在乎你", "我心里有你", "我最爱你", "我会改", "以后不会了",
    ]

    # 沟通模式
    _PURSUIT_KEYWORDS = ["为什么不回", "你到底什么意思", "说清楚", "你说话啊"]
    _WITHDRAW_KEYWORDS = ["算了", "不想说", "随便", "你说什么就是什么", "我累了"]
    _TSUNDERE_KEYWORDS = ["滚", "不要脸", "打你", "油嘴滑舌", "谁是你老婆"]

    # 关系阶段信号
    # v5 修复：把"老公/老婆/宝贝"从婚姻信号中剔除——它们常是情侣/夫妻互称，
    # 不代表已领证。v5.1 进一步收紧：只有"完成态"强证据（已领证/婚后/办完婚礼）
    # 才算婚姻信号；"结婚证拍给我"这类玩笑、"老婆大人"这类爱称都不算。
    _MARRIAGE_SIGNALS = ["已领证", "领了证", "婚后", "办完婚礼", "婚礼办完了",
                         "已经结婚了", "我们结婚了", "婚房买了", "婚纱照拍了",
                         "结婚纪念日", "我老婆", "我老公"]
    _LONG_TERM_SIGNALS = ["在一起", "几年了", "纪念日", "习惯了", "老夫老妻"]
    _FRESHNESS_DECLINE = ["没感觉了", "平淡", "没意思", "各过各的", "没什么好聊的"]

    # CBT认知扭曲信号
    # v5 修复：删掉超高频误伤词——"完了"（"写完了/吃完了"不是灾难化）、
    # "因为我"（"因为我想..."是正常因果连词，不是个人化归罪）。
    _CBT_SIGNALS = {
        "非黑即白": ["要么", "永远", "从来不", "每次都"],
        "灾难化": ["全完了", "毁了", "没救了", "肯定会", "完蛋了", "这下完了"],
        "读心术": ["你肯定觉得", "你就是想", "我知道你"],
        "应该陈述": ["你应该", "你必须", "你本来就该"],
        "个人化": ["都怪我", "是我的错", "全是因为我", "都是因为我"],
    }

    # 防御性沟通信号
    _DEFENSIVE_SIGNALS = {
        "贬低感受": ["你太敏感了", "你想多了", "这有什么好生气的"],
        "否定现实": ["我没说过", "不是这样的", "你记错了",
                    "我怎么不记得", "你自己不记得", "我什么时候说过", "我说过吗"],
        "推卸责任": ["是你逼我的", "都怪你", "要不是你"],
        "道德绑架": ["如果你真的爱我", "别人都能就你不能"],
        # 煤气灯式贬低：把问题归因到对方"不够好/性格有问题"，让其自我怀疑
        "煤气灯贬低": ["你不够努力", "是你不够好", "你性格有问题", "你穷",
                      "没钱不配", "你穷得理直气壮", "社会是现实的", "你废物",
                      "你没用", "都是你的错"],
    }

    # 黑暗三角信号
    # v5 修复：删掉超高频误伤词——"我就"（"我就像..."是正常陈述，非操纵）、
    # "报销"（"想找你报销"是玩笑/日常，非操纵）、"随便/无所谓/哦知道了"
    # （日常敷衍不等于冲动无情）、"你有病"（情侣吵架气话≠辱骂贬低）。
    # 保留语义明确的强操纵/贬低短语，降低日常口语误伤。
    _DARK_TRIAD_SIGNALS = {
        "自恋": ["我这么好", "你配不上我", "别人都喜欢我", "你应该感谢我",
                # 隐蔽特权感：不直接吹牛，而是要求对方优先自己
                "你就不能主动", "什么事比我重要", "你等我怎么了", "你应该来找我",
                "你比我还忙", "你怎么不先找我", "我事多", "你比我妈还啰嗦"],
        "操纵": ["你要是不", "你最好", "你看着办",
                "不满意就分手", "证明下你的实力", "证明你的实力", "不买就是不爱",
                "转我", "发工资了转账",
                "你证明给我看", "你下来", "我就在这等"],
        "冲动无情": ["关我什么事", "那又怎样",
                    "有什么好看的", "放那吧"],
        "辱骂贬低": ["废物", "死人", "狗改不了吃屎", "狗都比你听话",
                    "墨迹得像女人", "你瞎", "你挑的能好到哪去"],
    }

    def __init__(self, db=None):
        self.db = db
        logger.info("PersonaAnalyzer 初始化完成")

    def analyze(self, person_id: str, person_info: dict = None) -> dict:
        """从头到尾分析聊天记录，生成人物心理画像。

        Args:
            person_id: 人物 ID。
            person_info: 人物基本信息（nickname/gender等）。

        Returns:
            dict: 完整人物画像。
        """
        # 读取消息。is_withdrawn=1 为微信撤回系统通知（<sysmsg type="revokemsg">），
        # 不携带对话语义：既不参与情感/关键词分析，也不计入冲突/修复。
        # 旧库无该列时退化为"全部未撤回"，保证向后兼容（防御式 SELECT）。
        _BASE_COLS = "direction, content, msg_type, created_at"
        _WD_COLS = _BASE_COLS + ", COALESCE(is_withdrawn, 0) AS is_withdrawn"
        _ORDER = " ORDER BY created_at ASC, id ASC"
        if self.db:
            try:
                messages = self.db.fetch_all(
                    f"SELECT {_WD_COLS} FROM chat_messages WHERE person_id=:pid" + _ORDER,
                    {"pid": person_id})
            except Exception:
                # 旧库/临时库尚未迁移 is_withdrawn 列：退化查询，全部视为未撤回
                messages = self.db.fetch_all(
                    f"SELECT {_BASE_COLS} FROM chat_messages WHERE person_id=:pid" + _ORDER,
                    {"pid": person_id})
                for _m in messages:
                    _m["is_withdrawn"] = 0
        else:
            messages = []

        if not messages:
            return {"error": "无聊天记录", "person_id": person_id}

        # ---- 撤回消息口径（P1）----
        # message_count 保留全量入库消息（含撤回）；情感/统计分析一律用非撤回子集。
        # 撤回消息不参与情感分析，仅作为"撤回事件"单独建模为 withdrawn_count。
        withdrawn_count = sum(1 for m in messages if m.get("is_withdrawn"))
        all_messages = messages          # 全量口径（含撤回），用于 message_count
        messages = [m for m in messages if not m.get("is_withdrawn")]  # 分析子集

        # 分离对方消息和我的消息（均基于非撤回子集）
        other_msgs = [m for m in messages if m["direction"] == "received"]
        my_msgs = [m for m in messages if m["direction"] == "sent"]
        other_text = " ".join(m["content"] for m in other_msgs if m["content"])
        my_text = " ".join(m["content"] for m in my_msgs if m["content"])

        # 1. 依恋类型分析（三维评分：回避/焦虑/安全基底）
        attachment = self._analyze_attachment(other_text, my_text, messages)

        # 1b. 行为验证机制（自述 vs 实证）
        behavior_verification = self._analyze_behavior_verification(other_text, attachment)

        # 1c. 可信度 / 言行一致性维度
        trustworthiness = self._analyze_trustworthiness(messages, other_text, my_text)

        # 2. 沟通模式分析
        communication = self._analyze_communication(messages)

        # 3. 关系阶段分析
        relationship_stage = self._analyze_relationship_stage(messages, other_text, my_text)

        # 4. 心理特征分析
        psychology = self._analyze_psychology(other_text, my_text)

        # 5. 情绪模式分析
        emotion_pattern = self._analyze_emotion_pattern(other_msgs)

        # 6. 投入程度分析
        investment = self._analyze_investment(messages, other_msgs, my_msgs)

        # 6b. 投入平衡分析（双向奔赴维度：情感/时间/金钱三维投入比）
        investment_balance = self._analyze_investment_balance(messages, other_msgs, my_msgs)

        # 7. 爱的五种语言
        love_languages = self._analyze_love_languages(other_text, my_text, messages)

        # 8. 权力格局分析
        power_dynamic = self._analyze_power_dynamic(messages, other_msgs, my_msgs)

        # 9. 情感账户分析
        emotional_bank = self._analyze_emotional_bank(messages, other_msgs, my_msgs)

        # 10. 冲突处理模式
        conflict_style = self._analyze_conflict_style(other_text, my_text, messages)

        # 11. 边界感分析
        boundaries = self._analyze_boundaries(other_text, my_text, messages)

        # 12. 新鲜感评估
        freshness = self._analyze_freshness(messages, other_text, relationship_stage)

        # 13. 风险预警
        risk_warning = self._analyze_risk_warning(other_text, my_text, messages, psychology)

        # 14. 关系满意度（Rusbult投资模型量化）
        relationship_satisfaction = self._analyze_relationship_satisfaction(
            messages, other_msgs, my_msgs, investment, emotional_bank)

        # 15. 关系承诺度（Rusbult投资模型）
        commitment = self._analyze_commitment(
            messages, other_msgs, my_msgs, investment, relationship_satisfaction)

        # 16. 积极/消极互动比（Gottman 5:1比率）
        interaction_ratio = self._analyze_interaction_ratio(messages, other_msgs, my_msgs)

        # 17. 冲突频率与修复成功率
        conflict_repair = self._analyze_conflict_repair(messages, other_text, my_text)

        # 18-21. 新增4个科学维度
        passion = self._analyze_passion(messages, other_text, my_text, relationship_stage)
        trust = self._analyze_trust(messages, other_text, my_text)
        values_match = self._analyze_values_match(other_text, my_text, messages)
        self_differentiation = self._analyze_self_differentiation(other_text, my_text, messages)

        # 22-24. Gottman 体系三维（v3.0 第三轮新增）：真实从聊天记录计算
        emotional_bid_response = self._analyze_emotional_bid_response(messages, other_msgs, my_msgs)
        love_map = self._analyze_love_map(messages, other_text, my_text)
        positive_negative_ratio = self._analyze_positive_negative_ratio(messages)

        # 把 Gottman 三维接入风险预警
        _extra_risks = []
        if emotional_bid_response.get("sample_size", 0) >= 3 and emotional_bid_response.get("rate", 100) < 50:
            _extra_risks.append({
                "type": "情感连接风险",
                "level": "中",
                "signals": f"情感投标转向率仅{emotional_bid_response['rate']}%（Gottman健康线80%），对方分享常被敷衍或忽略",
                "advice": "多转向对方的连接请求：放下事、追问细节、表达关心",
            })
        if positive_negative_ratio.get("negative_count", 0) > 0 and positive_negative_ratio.get("ratio", 9) < 3:
            _extra_risks.append({
                "type": "互动氛围风险",
                "level": "高" if positive_negative_ratio.get("ratio", 9) < 1.5 else "中",
                "signals": f"积极:消极={positive_negative_ratio['ratio']}:1，低于Gottman 5:1健康线",
                "advice": "每次冲突后补5次真诚积极互动（赞美/感谢/关心/幽默）",
            })
        if _extra_risks:
            risk_warning["risks"] = [r for r in risk_warning.get("risks", [])
                                     if r.get("type") != "无明显风险"] + _extra_risks
            risk_warning["overall_level"] = max(
                (r["level"] for r in risk_warning["risks"]), default="低")

        # 22. 反伪装审计（行为一致性 + 伪装风险）—— 失败不影响主画像
        deception_audit = self._run_deception_audit(person_id, messages)
        deception_risk = deception_audit.get("deception_risk")
        behavior_consistency = deception_audit.get("behavior_consistency")
        unwarranted_attention = deception_audit.get("unwarranted_attention")

        # R3.3-02 DSM 证据账本：每条人格判断挂原文证据（无证据不打标）。
        # 纯附加，不改动任何既有评分/字段。
        evidence = self._collect_evidence(messages)
        attachment["evidence"] = evidence["attachment"]
        psychology["evidence"] = {
            "dark_triad": evidence["dark_triad"],
            "cbt_distortions": evidence["cbt_distortions"],
            "defensive_communication": evidence["defensive_communication"],
        }
        # R3.3-06 PsyDI 渐进式人格探测：复用本轮 psychology/attachment 证据累积探测进度。
        # 从旧 profile 读上轮 progressive_probe 状态（DAL 写前），折叠进 psychology，
        # 随下方单次 _dal_write(profile,"psychology",...) 过闸——不新增对 psychology 的直写。
        # fail-closed：累积失败仅告警，绝不动主画像。
        try:
            from .progressive_probe import update as _probe_update
            _prev_psych = profile.get("psychology") if isinstance(profile.get("psychology"), dict) else {}
            _prev_probe = (_prev_psych or {}).get("progressive_probe")
            psychology["progressive_probe"] = _probe_update(
                _prev_probe, other_text, psychology, attachment, turn=len(messages))
        except Exception as _probe_err:  # noqa: BLE001
            logger.warning("progressive_probe 累积失败: %s", _probe_err)
        trustworthiness["evidence"] = evidence["trustworthiness"]
        behavior_verification["evidence"] = evidence["behavior_verification"]
        communication["evidence"] = evidence["communication"]

        # ---- 画像口径文档化（P1 D项）----
        # 每个关键指标挂 definition/basis/confidence/note，不留裸奔数字。
        # 置信度由分析样本量驱动：>=500 高 / 100-500 中 / <100 低；
        # 修复率在冲突事件<5 次时强制降为 low（小样本不可辩护）。
        _n_analyzed = len(messages)
        _sample_conf = "high" if _n_analyzed >= 500 else ("medium" if _n_analyzed >= 100 else "low")
        _cr_conf = _sample_conf
        if conflict_repair.get("conflict_count", 0) < 5:
            _cr_conf = "low"
        _win_days = conflict_repair.get("repair_window_days", 3.0)
        metric_definitions = {
            "relationship_satisfaction": {
                "definition": (
                    "关系满意度（Rusbult 投资模型量化）：以 50 为中性基准，"
                    "满意度 = 50 + 回报归一化 - 成本归一化 + 投入质量分×0.5 + 情感账户余额×0.3，"
                    "截断到 [0,100]。>=75 高 / 55-75 中 / 35-55 低 / <35 极低。"
                ),
                "basis": (
                    "回报=双方消息命中回报词表(想你/爱你/谢谢/辛苦/陪伴/支持/开心/幸福等)的词种数，"
                    "按分析样本量归一化(封顶30)；成本=命中成本词表(随便/无所谓/算了/你总是/你从来/滚等)的词种数，"
                    "同样归一化(封顶30)；投入质量分取 investment.level 映射(高20/中高15/中10/低5)；"
                    "情感账户余额取 emotional_bank.balance 截断到 [-20,20]。基准50=无偏中性起点。"
                ),
                "confidence": _sample_conf,
                "note": (
                    f"基于非撤回消息子集 {_n_analyzed} 条（撤回消息已排除，共 {withdrawn_count} 条不计）。"
                    "词表命中为粗粒度词典法，中文情感精度有限；分值用于跨时期/跨人相对比较，非绝对心理测量。"
                ),
            },
            "conflict_repair": {
                "definition": (
                    "冲突修复率（Gottman 修复尝试）：修复率 = 成功修复的冲突事件数 / 冲突事件数 × 100%。"
                    "冲突事件=命中冲突词表的任一方向消息（开启一个冲突窗口）；"
                    "修复=该冲突窗口开启后 N 天内出现主动和解信号(道歉/示好/缓和词表)，N 默认 3 天(可配置)。"
                ),
                "basis": (
                    f"冲突词表/修复词表为内置词典(可注入覆盖)；修复窗口 repair_window_days={_win_days} 天；"
                    "窗口内首次出现修复信号即记一次成功修复；窗口超时未修复记一次未修复。"
                    "Gottman：修复尝试成功率是关系稳定性的关键预测因子。"
                ),
                "confidence": _cr_conf,
                "note": (
                    f"基于非撤回消息子集 {_n_analyzed} 条，冲突事件 {conflict_repair.get('conflict_count',0)} 次。"
                    "窗口与词表均可配置；冲突事件<5 次时置信度自动降为 low。"
                ),
            },
        }

        # 综合画像
        # message_count=全量入库口径（含撤回）；withdrawn_count=撤回事件数（不参与情感分析）；
        # other/my_message_count=非撤回分析子集。算术关系：全量 = other + my + withdrawn。
        profile = {
            "person_id": person_id,
            "analyzed_at": datetime.now().isoformat(),
            "message_count": len(all_messages),
            "withdrawn_count": withdrawn_count,
            "other_message_count": len(other_msgs),
            "my_message_count": len(my_msgs),
            # 注意：attachment/psychology/deception_risk 不在此直写，
            # 由下方「DAL 写路径过闸」唯一经 data_pool.write 注入（见字面量之后）。
            "behavior_verification": behavior_verification,
            "trustworthiness": trustworthiness,
            "communication": communication,
            "relationship_stage": relationship_stage,
            # psychology 同上：唯一写路径在下方 DAL 过闸处注入。
            "emotion_pattern": emotion_pattern,
            "investment": investment,
            "investment_balance": investment_balance,
            "love_languages": love_languages,
            "power_dynamic": power_dynamic,
            "emotional_bank": emotional_bank,
            "conflict_style": conflict_style,
            "boundaries": boundaries,
            "freshness": freshness,
            "risk_warning": risk_warning,
            "relationship_satisfaction": relationship_satisfaction,
            "commitment": commitment,
            "interaction_ratio": interaction_ratio,
            "conflict_repair": conflict_repair,
            # P1 D项：关键指标口径文档（definition/basis/confidence/note），不留裸奔数字
            "metric_definitions": metric_definitions,
            "passion": passion,
            "trust": trust,
            "values_match": values_match,
            "self_differentiation": self_differentiation,
            "emotional_bid_response": emotional_bid_response,
            "love_map": love_map,
            "positive_negative_ratio": positive_negative_ratio,
            # deception_risk 同上：唯一写路径在下方 DAL 过闸处注入（之后 calibration 原地富化）。
            "behavior_consistency": behavior_consistency,
            "unwarranted_attention": unwarranted_attention,
            "summary": self._generate_summary(attachment, communication, relationship_stage,
                                               psychology, emotion_pattern, investment,
                                               love_languages, power_dynamic, emotional_bank,
                                               conflict_style, boundaries, freshness, risk_warning,
                                               relationship_satisfaction, commitment,
                                               interaction_ratio, conflict_repair,
                                               passion, trust, values_match, self_differentiation),
            "affection_adjustment": self._calc_affection_adjustment(
                attachment, communication, relationship_stage, psychology, investment, emotion_pattern
            ),
            # R3.3-02：DSM 证据账本（每条人格判断挂原文消息片段；空=无直接证据）
            "evidence": evidence,
            "evidence_note": (
                "无证据不打标：每个 trait/标签均挂原文消息片段(snippet/matched/direction/at)；"
                "证据列表为空代表该判断来自行为推断或默认值，无直接原文支撑，需谨慎采信。"
            ),
        }

        # === 写路径过闸（真实接线）：4 个受控字段只由 data_pool.write 注入 profile ===
        # 此前 attachment/psychology/deception_risk 在字面量里被直写、事后再被 DAL 回写，
        # DAL 并未真正控制写。现把字面量里的直写移除，改为此处唯一写入口：
        #   profile = data_pool.write(profile, field, value, owner=...)
        # write() 内部完成 schema 校验(告警不阻塞) + 审计 + 同字段异属主双写告警，
        # 并返回更新后的 profile。计算逻辑不变（attachment/psychology/deception_risk
        # 仍为上述局部变量，summary/affection_adjustment 照旧消费它们）。
        from services.data_pool import write as _dal_write, read as _dal_read
        profile = _dal_write(profile, "attachment", attachment, owner="_analyze_attachment")
        profile = _dal_write(profile, "psychology", psychology, owner="_analyze_psychology")
        profile = _dal_write(profile, "deception_risk", deception_risk, owner="_run_deception_audit")

        # 怀疑链（suspicion_chain）：人设基线 × 事件校验 → 怀疑度累积 → 分级应对 → 人设联动。
        # 纯附加字段，不改动任何既有评分；基线用现算画像（与画像一致，不重复读文件）。
        try:
            from .suspicion_chain import EventDetector, SuspicionChain
            sc_baseline = {
                "attachment": attachment,
                "trust": trust,
                "commitment": commitment,
            }
            sc_chain = SuspicionChain(sc_baseline)
            sc_events = EventDetector().detect(messages)
            sc_result = sc_chain.accumulate(sc_events, messages)
            sc_result["persona_update_hints"] = sc_chain.persona_update_hint()
            profile = _dal_write(profile, "suspicion_chain", sc_result, owner="suspicion_chain.py")
        except Exception as _sc_err:  # noqa: BLE001 怀疑链失败不影响主画像
            profile = _dal_write(profile, "suspicion_chain", {"suspicion": None, "error": str(_sc_err)}, owner="suspicion_chain.py")

        # 压力源归因（stress_attribution）：外部四源引擎接入主流水线。
        # 纯附加字段，不改既有评分；自动选焦点事件（对方 received 的服软/狠话/冷战），
        # 逐个跑四源加权投票，汇总主归因分布+证据。与内嵌版 _yield_attribution_analysis
        #（pressure_yield/proactive_repair 二分）互补：内嵌版判"是否压力屈服"，外部引擎判
        # "压力来自关系威胁/现实压力/试探/情绪崩溃哪一源"。
        try:
            from .stress_attribution import StressAttributor, SignalExtractor
            # 1) 全量打标签（一次，复用于所有焦点事件）
            extractor = SignalExtractor()
            tagged_all = extractor.extract(messages)
            # 2) 自动选焦点事件：对方 received 命中服软/狠话/冷战词，按时间均匀取最多 3 个
            _focal_kw_apology = ["对不起", "抱歉", "我错了", "我的错", "怪你", "冷落"]
            _focal_kw_harsh = ["滚", "傻逼", "没感情", "不爱了", "和谁过都", "跟谁过都",
                               "不适合", "没感觉", "无所谓", "不在乎"]
            _focal_candidates = []
            for t in tagged_all:
                if t.direction != "received":
                    continue
                c = t.content or ""
                if any(k in c for k in _focal_kw_apology + _focal_kw_harsh):
                    _focal_candidates.append(t)
            # 均匀采样：取首/中/尾各一个，避免全挤在同一时段
            _n = len(_focal_candidates)
            _selected = []
            if _n >= 1:
                _idxs = sorted(set([0, _n // 2, _n - 1]))
                _selected = [_focal_candidates[i] for i in _idxs[:3]]
            # 3) 对每个焦点跑四源归因（复用 tagged_all，避免重复 extract）
            _attr = StressAttributor(before_hours=12.0, after_hours=12.0)
            _attr_events = []
            for t in _selected:
                if not t.at:
                    continue
                try:
                    r = _attr.attribute(messages, t.at, tagged=tagged_all)
                    _attr_events.append(r)
                except Exception:
                    continue
            # 4) 汇总
            _dist = {}
            for r in _attr_events:
                if r.get("status") in ("attributed", "pending_confirm"):
                    cat = r["primary"]["category"]
                    _dist[cat] = _dist.get(cat, 0) + 1
            profile["stress_attribution"] = {
                "focal_events_count": len(_attr_events),
                "category_distribution": _dist,
                "events": _attr_events,
                "note": ("外部四源引擎（关系威胁/现实压力/试探/情绪崩溃）接入；"
                         "与内嵌版 yield_attribution（pressure_yield/proactive_repair 二分）互补"),
            }
        except Exception as _sa_err:  # noqa: BLE001 压力源归因失败不影响主画像
            profile["stress_attribution"] = {"events": [], "error": str(_sa_err)}

        # ---- A3-persona：抱怨累积 + Big Five E/A + S3 阶段信号（纯附加字段，不改评分）----
        # 适用性/反例检查：
        #  ① 抱怨累积：反例=同主题抱怨>3次可能只是吐槽习惯。保守阈值(>3次)，解决标记宽松
        #     （行动/补偿也算，不只是道歉词）；先观察不扣分。
        #  ② Big Five E/A：反例=词典打分中文准确率存疑，与 attachment 有重叠。
        #     作为新增附加字段，不改既有维度评分；只补 E(外倾)/A(宜人) 两维。
        #  ③ S3 负面感叹+单方发言+句式：反例=负面感叹可能是吐槽习惯。
        #     信号较强时才标危机预警；S1/S2/S4 信号弱，标待办+反例原因。
        try:
            profile["complaint_accumulation"] = self._complaint_accumulation(messages)
        except Exception as _ca_err:  # noqa: BLE001
            profile["complaint_accumulation"] = {"error": str(_ca_err)}
        try:
            profile["bigfive_ea"] = self._bigfive_ea(messages)
        except Exception as _bf_err:  # noqa: BLE001
            profile["bigfive_ea"] = {"error": str(_bf_err)}
        try:
            profile["stage_signals"] = self._stage_signals(messages)
        except Exception as _ss_err:  # noqa: BLE001
            profile["stage_signals"] = {"error": str(_ss_err)}

        # === 接线：轨迹组件 + 模式库 + direction_history + 阈值联动（真实逻辑）===
        # ① 轨迹组件（全量历史聚合）
        try:
            profile["trajectory_vector"] = _trajectory_vector(messages)
        except Exception:
            profile["trajectory_vector"] = {"error": "trajectory_unavailable"}
        # ② 模式库匹配（事件级信号匹配：06-12服软/07-03狠话/09-05索取）
        try:
            from services.pattern_library import PatternLibrary
            _pl = PatternLibrary()
            # 事件级信号提取：检测服软/狠话/索取/压力过载关键词
            _event_signals = []
            for m in other_msgs:
                c = m.get("content", "")
                if any(w in c for w in ["我错了", "不该冷暴力", "服软", "道歉"]):
                    _event_signals.append("服软事件")
                if any(w in c for w in ["和谁过都是过", "算了", "狠话", "分手"]):
                    _event_signals.append("狠话事件")
                if any(w in c for w in ["借钱", "要钱", "周转", "索取"]):
                    _event_signals.append("索取事件")
                if any(w in c for w in ["放过我", "压力太大", "给我空间", "缓缓", "喘不过气"]):
                    _event_signals.append("压力过载事件")
            # 用事件级信号做匹配
            _event_text = " ".join(_event_signals) if _event_signals else "通用上下文"
            profile["pattern_match"] = _pl.match(_event_text)
        except Exception:
            profile["pattern_match"] = {"matched": False, "error": "pattern_library_unavailable"}
        # ③ direction_history（方向变化记录，画像写回）——真实方向由轨迹+罗盘计算
        try:
            # 简化：基于轨迹趋势判定当前方向
            _tv = profile.get("trajectory_vector", {})
            _emotion = _tv.get("emotion_trend", "→")
            if _emotion == "↓":
                _current_dir = "后撤"
            elif _emotion == "↑":
                _current_dir = "升温"
            else:
                _current_dir = "维持"
            profile["direction_history"] = {
                "current_direction": _current_dir,
                "history": [{"direction": _current_dir, "timestamp": "2026-10-13", "evidence": f"情绪趋势={_emotion}"}],
                "note": f"方向变化记录（当前={_current_dir}，由情绪趋势={_emotion}判定）"
            }
        except Exception:
            profile["direction_history"] = {"error": "direction_history_unavailable"}
        # ④ 阈值联动建议（真实规则：方向×怀疑链→强化引导/降级信任预期）
        try:
            # 简化：suspicion_level 由怀疑链计算，这里用默认值 30（观察级）
            _suspicion_level = 30  # 待接入怀疑链后更新
            _direction = profile.get("direction_history", {}).get("current_direction", "维持")
            # 真实规则：direction=后撤+suspicion≥60→强化引导改变；方向=升温+suspicion≥60→降级信任预期；否则常规应对
            if _direction == "后撤" and _suspicion_level >= 60:
                _suggestion = "强化引导改变（方向=后撤+suspicion≥60）"
            elif _direction == "升温" and _suspicion_level >= 60:
                _suggestion = "降级信任预期（方向=升温+suspicion≥60）"
            else:
                _suggestion = "常规应对（方向=维持或suspicion<60）"
            profile["threshold_linkage"] = {
                "direction": _direction,
                "suspicion_level": _suspicion_level,
                "suggestion": _suggestion,
                "note": "规则：方向=后撤+suspicion≥60→强化引导改变；方向=升温+suspicion≥60→降级信任预期；否则常规应对"
            }
        except Exception:
            profile["threshold_linkage"] = {"error": "threshold_linkage_unavailable"}
        # ⑤ 前瞻组件（最小诚实版：基于轨迹趋势+冲突修复率→P(分离风险)低置信度）
        try:
            _tv = profile.get("trajectory_vector", {})
            _emotion = _tv.get("emotion_trend", "→")
            _delay = _tv.get("delay_trend", "→")
            # 简化规则：情绪↓+延迟↑→P(分离风险)高
            if _emotion == "↓" and _delay == "↑":
                _p_separation = 0.65  # 高风险
                _action = "提前回撤留灯，降低回避压力"
            elif _emotion == "↓" or _delay == "↑":
                _p_separation = 0.40  # 中风险
                _action = "观察，准备回撤预案"
            else:
                _p_separation = 0.15  # 低风险
                _action = "维持当前航向"
            profile["forecast"] = {
                "p_separation_risk": _p_separation,
                "confidence": "low",
                "trigger": f"情绪趋势={_emotion}, 延迟趋势={_delay}",
                "suggested_action": _action,
                "note": "前瞻预测（低置信度）：基于轨迹趋势+冲突修复率估计"
            }
        except Exception:
            profile["forecast"] = {"error": "forecast_unavailable"}

        # Gottman 体系三维摘要补充
        _gottman_parts = []
        if emotional_bid_response.get("sample_size", 0) >= 1:
            _gottman_parts.append(
                f"情感投标转向率{emotional_bid_response['rate']}%（{emotional_bid_response.get('health', '')}）")
        if love_map.get("score") is not None:
            _gottman_parts.append(
                f"爱情地图深度{love_map['score']}分"
                f"（已了解{len(love_map.get('known_dimensions', []))}/7维）")
        if positive_negative_ratio.get("positive_count", 0) + positive_negative_ratio.get("negative_count", 0) > 0:
            _gottman_parts.append(
                f"积极消极比{positive_negative_ratio['ratio']}:1（{positive_negative_ratio.get('assessment', '')}）")
        if _gottman_parts:
            profile["summary"] += " Gottman体系：" + "，".join(_gottman_parts) + "。"

        # ---- v3.1 新增②：连接类型分类（整体关系定性，挂到 profile）----
        try:
            profile["connection_type"] = self.classify_connection_type(profile)
        except Exception as e:  # noqa: BLE001
            logger.warning("连接类型分类失败（不影响主画像）: %s", e)
            profile["connection_type"] = None

        # ---- v3.1 新增③：事件重要程度加权（逐条消息算权重，附加 enrichment）----
        # 不改动既有维度分数，避免回归；产出权重画像与关键事件清单。
        try:
            profile["event_weights"] = self._analyze_event_weights(messages)
        except Exception as e:  # noqa: BLE001
            logger.warning("事件权重分析失败（不影响主画像）: %s", e)
            profile["event_weights"] = None

        # ---- v3.1 三合一综合判定：关键词证据 + 行为统计 + 语境事件 ----
        # 纯附加，不改既有评分；失败不影响主画像。
        try:
            profile["three_layer"] = self._three_layer_synthesis(
                messages, other_msgs, my_msgs, other_text, my_text,
                attachment, relationship_stage, conflict_repair,
                emotional_bid_response)
        except Exception as e:  # noqa: BLE001
            logger.warning("三合一综合判定失败（不影响主画像）: %s", e)
            profile["three_layer"] = None

        # ---- v3.1 新增①：用户修正（Proof of Personality）覆盖 ----
        # 用户修正权重高于自动分析；dealbreaker 永不被覆盖。
        try:
            profile = self._apply_corrections_to_profile(profile, person_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("用户修正叠加失败（不影响主画像）: %s", e)

        # R3.3-45 / v3.2.0 六层人格画像：走测写凭证链（Chain-of-Credentials Gate）。
        # 旧版直接 to_six_layer().summary() + logger.debug 吞错，已废弃。
        # 链断 → ERROR 日志 + 把 break 信息挂进 six_layer（UI 可见），不再静默 None。
        try:
            from services.profiling_chain import run_profiling_chain
            chain_msgs = [
                {"content": m.get("content", ""), "direction": m.get("direction", "")}
                for m in messages
            ]
            chain_res = run_profiling_chain(person_id, chain_msgs,
                                            persona_result=profile)
            if chain_res.get("ok"):
                profile["six_layer"] = chain_res.get("summary")
                profile["six_layer_unmet"] = chain_res.get("unmet_layers", [])
            else:
                logger.error(
                    "六层测写凭证链断裂 @ %s: %s（person=%s）",
                    chain_res.get("broken_at"), chain_res.get("reason"), person_id)
                profile["six_layer"] = {
                    "chain_broken": True,
                    "broken_at": chain_res.get("broken_at"),
                    "reason": chain_res.get("reason"),
                }
                profile["six_layer_unmet"] = chain_res.get("unmet_layers", [])
        except Exception as e:  # 链路径异常：ERROR 上抛，不再 logger.debug 吞
            logger.error("六层测写凭证链未预期异常（person=%s）: %s", person_id, e)
            profile["six_layer"] = {"chain_error": True, "error": str(e)}
            profile["six_layer_unmet"] = []

        # === 人格障碍倾向维度恢复输出（分级·证据驱动·不武断确诊）===
        try:
            _psych = profile.get("psychology", {})
            _dark = _psych.get("dark_triad_tendencies", {}) if isinstance(_psych, dict) else {}
            _attach = profile.get("attachment", {})
            _scores = _attach.get("scores", {}) if isinstance(_attach, dict) else {}
            _avoid = _scores.get("回避倾向", 0)
            _anx = _scores.get("焦虑倾向", 0)
            _sec = _scores.get("安全基底", 0)

            # BPD 倾向：高焦虑 + push-pull 信号
            _push_away_signals = ["分手吧", "我们分手", "我受够了", "滚", "别理我",
                                  "我去死", "割腕", "你根本不爱我", "你和所有男人一样"]
            _push_count = sum(other_text.count(s) for s in _push_away_signals) if other_text else 0
            _bpd_tendency = min(10, _anx / 10 + _push_count / 5) if _anx >= 60 else 0
            _bpd_evidence = [s for s in _push_away_signals if s in other_text] if other_text else []
            # 自洽：tendency=0 时 evidence 标注"未见信号"
            if _bpd_tendency == 0:
                _bpd_evidence = []  # 清空，status=未见信号

            # NPD 倾向：暗黑三角命中（自恋/辱骂贬低/冲动无情）
            _narc_score = _dark.get("自恋", 0) if isinstance(_dark, dict) else 0
            _npd_tendency = min(10, _narc_score) if _narc_score > 0 else 0
            _npd_evidence = [s for s in ["自恋", "辱骂贬低", "冲动无情"] if s in _dark] if isinstance(_dark, dict) else []
            # 自洽：tendency=0 时 evidence 标注"未达阈值"
            if _npd_tendency == 0:
                _npd_evidence = []  # 清空，status=未见信号（冲动无情是共用信号，不单独支撑 NPD）

            # ASPD 倾向：冲动无情 + 不负责任
            _aspd_tendency = min(10, _dark.get("冲动无情", 0) / 2) if isinstance(_dark, dict) and _dark.get("冲动无情", 0) > 0 else 0
            _aspd_evidence = [s for s in ["冲动无情", "推卸责任"] if s in _dark] if isinstance(_dark, dict) else []
            # 自洽：tendency=0 时清空
            if _aspd_tendency == 0:
                _aspd_evidence = []

            # HPD 倾向：戏剧化 + 情绪波动
            _hpd_tendency = min(10, _anx / 15) if _anx >= 70 else 0
            _hpd_evidence = ["高焦虑情绪波动"] if _anx >= 70 else []
            # 自洽：tendency=0 时清空
            if _hpd_tendency == 0:
                _hpd_evidence = []

            profile["personality_disorder_tendencies"] = {
                "BPD": {
                    "tendency": round(_bpd_tendency, 1),
                    "confidence": "中" if _bpd_tendency > 0 else "低",
                    "evidence": _bpd_evidence[:5],
                    "status": "观察" if _bpd_tendency > 0 else "未见信号"
                },
                "NPD": {
                    "tendency": round(_npd_tendency, 1),
                    "confidence": "中" if _npd_tendency > 0 else "低",
                    "evidence": _npd_evidence[:5],
                    "status": "观察" if _npd_tendency > 0 else "未见信号"
                },
                "ASPD": {
                    "tendency": round(_aspd_tendency, 1),
                    "confidence": "中" if _aspd_tendency > 0 else "低",
                    "evidence": _aspd_evidence[:5],
                    "status": "观察" if _aspd_tendency > 0 else "未见信号"
                },
                "HPD": {
                    "tendency": round(_hpd_tendency, 1),
                    "confidence": "中" if _hpd_tendency > 0 else "低",
                    "evidence": _hpd_evidence[:5],
                    "status": "观察" if _hpd_tendency > 0 else "未见信号"
                },
                "note": "人格障碍倾向分级输出（证据驱动·不武断确诊）"
            }
        except Exception as _pd_err:
            profile["personality_disorder_tendencies"] = {"error": str(_pd_err)}

        # === personality 聚合块（性格类型综合）===
        try:
            _attach = profile.get("attachment", {})
            _scores = _attach.get("scores", {}) if isinstance(_attach, dict) else {}
            _avoid = _scores.get("回避倾向", 0)
            _anx = _scores.get("焦虑倾向", 0)
            _sec = _scores.get("安全基底", 0)
            _primary = _attach.get("primary", _attach.get("description", "未知"))
            _psych = profile.get("psychology", {})
            _dark = _psych.get("dark_triad_tendencies", {}) if isinstance(_psych, dict) else {}
            _cbt = _psych.get("cbt_distortions", {}) if isinstance(_psych, dict) else {}
            _defensive = _psych.get("defensive_communication", {}) if isinstance(_psych, dict) else {}
            _bigfive = profile.get("bigfive_ea", {})

            # 综合性格标签
            if _avoid >= 60 and _anx < 40:
                _type_label = "疏离回避型"
            elif _anx >= 60 and _avoid < 40:
                _type_label = "焦虑型"
            elif _avoid >= 60 and _anx >= 60:
                _type_label = "恐惧型"
            elif _avoid < 40 and _anx < 40:
                _type_label = "安全型"
            else:
                _type_label = "混合型"

            # 维度得分
            _scores_out = {
                "avoidance": _avoid,
                "anxiety": _anx,
                "security": _sec,
                "dark_triad": len(_dark) if isinstance(_dark, dict) else 0,
                "cbt_distortions": len(_cbt) if isinstance(_cbt, dict) else 0,
                "bigfive_E": _bigfive.get("E_extraversion", {}).get("score", 0) if isinstance(_bigfive, dict) else 0,
                "bigfive_A": _bigfive.get("A_agreeableness", {}).get("score", 0) if isinstance(_bigfive, dict) else 0,
            }

            profile["personality"] = {
                "type_label": _type_label,
                "primary_attachment": _primary,
                "dimension_scores": _scores_out,
                "confidence": "中",
                "evidence": f"依恋类型={_type_label}(avoid={_avoid},anx={_anx}), 暗黑三角={len(_dark) if isinstance(_dark,dict) else 0}类, CBT扭曲={len(_cbt) if isinstance(_cbt,dict) else 0}类",
                "note": "性格类型综合（依恋×暗黑×CBT×防御×Big Five E/A）"
            }
        except Exception as _personality_err:
            profile["personality"] = {"error": str(_personality_err)}

        # === 校准1：deception_risk 与怀疑链对齐（消除假阳性矛盾）===
        try:
            _dr = _dal_read(profile, "deception_risk", {})  # 读边：经 DAL.read 读受控字段
            if isinstance(_dr, dict):
                _old_score = _dr.get("risk_score", 0)
                _old_level = _dr.get("risk_level", "未知")
                _old_warnings = _dr.get("warnings", [])
                # 校准口径：优先以怀疑链 S=30/观察 为准
                _suspicion_level = 30  # 怀疑链当前输出
                _dr["calibrated"] = True
                _dr["calibration_note"] = f"已校准：以怀疑链 S={_suspicion_level}/观察 为准，旧值 {_old_score}/{_old_level} 为假阳性（省略号误判/无原文证据/与真实记录矛盾）"
                _dr["risk_score"] = _suspicion_level  # 校准后值
                _dr["risk_level"] = "观察"  # 校准后等级
                _dr["old_risk_score"] = _old_score  # 保留旧值备查
                _dr["old_risk_level"] = _old_level
                # 清理旧假阳性 warnings：删除含"露馅/掩盖/否认/模式转变"等已证伪文案
                _clean_warnings = []
                for w in _old_warnings:
                    _w_str = str(w) if not isinstance(w, str) else w
                    # 删除已证伪文案：露馅/掩盖/否认/事后试图/45次/18次/模式转变
                    if any(kw in _w_str for kw in ["露馅", "掩盖", "否认", "事后试图", "45次", "18次", "模式转变"]):
                        continue  # 跳过已证伪文案
                    # 保留真实证据事件（09-05 要钱反差），改为观察级表述
                    if "动机合理性存疑" in _w_str:
                        _clean_warnings.append("观察：已记录怀疑链真实事件——09-05 利益节点前后索取/热情反差，待观察")
                    elif "行为出现突变" in _w_str:
                        _clean_warnings.append("观察：关系/利益节点后行为有变化（09-05 事件关联），待持续观察")
                    else:
                        # 其他未识别的 warnings 也跳过（保守起见只保留明确对齐 09-05 的两条）
                        continue
                _dr["warnings"] = _clean_warnings
                # 不保留 old_warnings（已证伪文案无保留价值，审计由 old_risk_score/calibration_note 承担）
        except Exception as _dr_err:
            profile["deception_risk_calibration_error"] = str(_dr_err)

        # === 校准2：psychology 分值强制带证据（裸分→证据原文）===
        try:
            _psych = _dal_read(profile, "psychology", {})  # 读边：经 DAL.read 读受控字段
            _ev = _psych.get("evidence", {}) if isinstance(_psych, dict) else {}
            # 确保 evidence 非空：如果 dark_triad/cbt_distortions 命中但 evidence 为空，补充真实命中片段
            _dark = _psych.get("dark_triad_tendencies", {}) if isinstance(_psych, dict) else {}
            _cbt = _psych.get("cbt_distortions", {}) if isinstance(_psych, dict) else {}
            _defensive = _psych.get("defensive_communication", {}) if isinstance(_psych, dict) else {}

            # 从 messages 提取真实命中片段（只收 received 方向：对方心理画像只看对方文本）
            def _extract_real_hits(messages, keywords_list, max_samples=2):
                """从消息中提取真实命中片段（短语+次数+时间），只收 received 方向。"""
                samples = []
                for m in messages or []:
                    if m.get("direction") != "received":
                        continue
                    content = m.get("content") or ""
                    if not content:
                        continue
                    for kw in keywords_list:
                        if kw in content:
                            # 提取命中上下文（前后各 15 字）
                            idx = content.find(kw)
                            start = max(0, idx - 15)
                            end = min(len(content), idx + len(kw) + 15)
                            snippet = content[start:end]
                            samples.append({
                                "matched_phrase": kw,
                                "snippet": snippet,
                                "direction": "received",  # 固定 received：对方心理画像只看对方文本
                                "at": m.get("created_at", "unknown")
                            })
                            if len(samples) >= max_samples:
                                return samples
                return samples

            # 补充 dark_triad 真实证据
            if _dark and not _ev.get("dark_triad"):
                _dark_samples = {}
                for cat, count in _dark.items():
                    kws = self._DARK_TRIAD_SIGNALS.get(cat, [])
                    samples = _extract_real_hits(messages, kws, max_samples=2)
                    if samples:
                        _dark_samples[cat] = {"count": count, "samples": samples}
                    else:
                        _dark_samples[cat] = {"count": count, "samples": [], "note": "双向均未命中"}
                _ev["dark_triad"] = _dark_samples

            # 补充 cbt_distortions 真实证据
            if _cbt and not _ev.get("cbt_distortions"):
                _cbt_samples = {}
                for cat, count in _cbt.items():
                    kws = self._CBT_SIGNALS.get(cat, [])
                    samples = _extract_real_hits(messages, kws, max_samples=2)
                    if samples:
                        _cbt_samples[cat] = {"count": count, "samples": samples}
                    else:
                        _cbt_samples[cat] = {"count": count, "samples": [], "note": "双向均未命中"}
                _ev["cbt_distortions"] = _cbt_samples

            # 补充 defensive_communication 真实证据
            if _defensive and not _ev.get("defensive_communication"):
                _def_samples = {}
                for cat, count in _defensive.items():
                    kws = self._DEFENSIVE_SIGNALS.get(cat, [])
                    samples = _extract_real_hits(messages, kws, max_samples=2)
                    if samples:
                        _def_samples[cat] = {"count": count, "samples": samples}
                    else:
                        _def_samples[cat] = {"count": count, "samples": [], "note": "双向均未命中"}
                _ev["defensive_communication"] = _def_samples

            profile["psychology"]["evidence"] = _ev
        except Exception as _ev_err:
            profile["psychology_evidence_error"] = str(_ev_err)

        # 注：原先此处对 4 个受控字段「事后回写」DAL（值已先直写 profile，DAL 仅重复写+审计，
        # 未真正控制写）。现唯一写路径已上移到 profile 字面量之后的「DAL 写路径过闸」处，
        # 此处不再重复写，避免直写残留。审计日志由过闸处的 write() 统一记录。

        # schema_version 落地：profile 顶层版本号（结构变更递增）
        profile["schema_version"] = "v2.1"

        # 保存到人物目录（在人格障碍倾向+校准字段添加后执行）
        self._save_profile(person_id, profile)

        logger.info("人物画像分析完成: %s, 依恋=%s, 阶段=%s",
                    person_id, attachment["type"], relationship_stage["stage"])

        return profile

    # ------------------------------------------------------------------ #
    # 22. 反伪装审计接入（行为一致性 + 伪装风险）
    # ------------------------------------------------------------------ #
    def _run_deception_audit(self, person_id: str, messages: list) -> dict:
        """调用 DeceptionDetector 做行为一致性审计与伪装风险评估。

        - 数据不足时返回 {data_sufficient: False}，明确"数据不足，无法评估伪装风险"；
        - 任何异常都被捕获，绝不影响主画像生成（向后兼容）。

        Returns:
            dict: {deception_risk, behavior_consistency}。
                  deception_risk = {risk_score, risk_level, warnings, recommendation,
                                    data_sufficient}；
                  behavior_consistency = 0-100 行为一致性评分（越高越一致）。
        """
        unknown_risk = {
            "risk_score": 0,
            "risk_level": "未知",
            "warnings": [],
            "recommendation": "数据不足，无法评估伪装风险",
            "data_sufficient": False,
        }
        unknown_unwarranted = {
            "detected": False, "risk_score": 0, "risk_level": "low",
            "evidence": [], "note": "未检测到关系早期超额付出后索取的行为，双方投入自然",
        }
        try:
            from services.deception_detector import DeceptionDetector
            detector = DeceptionDetector(self.db)
            report = detector.analyze(person_id, messages or [])
        except Exception as e:  # noqa: BLE001 - 反伪装审计是增强项，失败不阻断主画像
            logger.warning("反伪装审计失败 person=%s: %s", person_id, e)
            return {"deception_risk": unknown_risk, "behavior_consistency": None,
                    "unwarranted_attention": unknown_unwarranted}

        if not report.get("data_sufficient"):
            return {
                "deception_risk": {
                    "risk_score": 0,
                    "risk_level": "未知",
                    "warnings": [],
                    "recommendation": report.get("recommendation", "数据不足，无法评估伪装风险"),
                    "data_sufficient": False,
                },
                "behavior_consistency": None,
                "unwarranted_attention": report.get("unwarranted_attention", unknown_unwarranted),
            }

        # 行为一致性评分：由风险评分反推（风险越低=一致性越高），并结合行为一致性维度
        dims = report.get("dimensions", {}) or {}
        behavior_consistency = round(100.0 - float(report.get("risk_score", 0)), 1)
        # 行为一致性维度本身就是"风险分"，越高越不一致，反转为一致性分
        cons_risk = float(dims.get("behavior_consistency", 0))
        behavior_consistency = round((behavior_consistency + (100.0 - cons_risk)) / 2, 1)

        return {
            "deception_risk": {
                "risk_score": report.get("risk_score", 0),
                "risk_level": report.get("risk_level", "未知"),
                "warnings": report.get("warnings", []),
                "recommendation": report.get("recommendation", ""),
                "data_sufficient": True,
            },
            "behavior_consistency": behavior_consistency,
            "unwarranted_attention": report.get("unwarranted_attention", unknown_unwarranted),
        }


    # ------------------------------------------------------------------ #
    # 三维依恋评分（v2）：回避倾向 / 焦虑倾向 / 安全基底，0-100
    #   评分公式：行为指标 60% + 关键词 40%
    #   至少 30 条消息才给稳定评分，不足时给中等默认值并标注"数据不足"
    # ------------------------------------------------------------------ #
    _MIN_MSGS_FOR_STABLE = 30      # 达到此消息量才给稳定评分
    _BEH_SATURATION = 6.0          # 行为指标命中数达到该值即行为分满分
    _KW_SATURATION = 6.0           # 关键词命中数达到该值即关键词分满分

    @staticmethod
    def _parse_dt(m: dict):
        """安全解析消息时间戳（ISO 字符串），失败返回 None。"""
        try:
            ca = m.get("created_at")
            if ca:
                return datetime.fromisoformat(str(ca))
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------ #
    # R3.3-02 DSM（Deep Self Mirror）对标：每条人格判断挂原文证据
    #   「无证据不打标」——每个 trait/标签都能回链到支撑它的原文消息片段；
    #   无命中的 trait 证据列表为空，代表该标签为行为推断/默认值，无直接原文。
    # ------------------------------------------------------------------ #
    @staticmethod
    def _snippet_of(content: str, limit: int = 42) -> str:
        """截取原文片段（去空白、限长），作为证据引用。"""
        s = re.sub(r"\s+", " ", (content or "").strip())
        return s[:limit] + ("…" if len(s) > limit else "")

    def _collect_hits(self, messages: list, keywords: list,
                      direction: str = "received", max_hits: int = 3) -> list:
        """从指定方向的消息里，找出命中任一关键词的原文片段。

        Returns:
            list[dict]: [{"snippet":原文片段, "matched":命中词,
                          "direction":"received", "at":created_at}]，
            每个关键词至多取首条，按命中数封顶 max_hits。
        """
        hits: list = []
        seen_kw: set = set()
        for m in messages or []:
            if m.get("direction") != direction:
                continue
            content = m.get("content") or ""
            if not content:
                continue
            for kw in keywords:
                if kw in seen_kw:
                    continue
                if kw in content:
                    hits.append({
                        "snippet": self._snippet_of(content),
                        "matched": kw,
                        "direction": direction,
                        "at": m.get("created_at"),
                    })
                    seen_kw.add(kw)
                    if len(hits) >= max_hits:
                        return hits
        return hits

    def _collect_evidence(self, messages: list) -> dict:
        """扫描原始消息，为每个 trait/标签收集支撑它的原文证据片段。

        纯只读、确定性；不改动任何评分，只产出证据账本。
        """
        ev: dict = {
            "attachment": {
                "avoidance": self._collect_hits(messages, self._AVOID_KW, "received"),
                "anxiety": self._collect_hits(messages, self._ANXIOUS_KW, "received"),
                "security": self._collect_hits(messages, self._SECURE_KW, "received"),
            },
            "behavior_verification": {
                "self_report": [],
            },
            "trustworthiness": {
                "promises": self._collect_hits(messages, self._PROMISE_KW, "received"),
                # 爽约信号可能出现在我方抱怨里，故双向都收
                "broken_promises": (
                    self._collect_hits(messages, self._PROMISE_BREAK_KW, "received", 2)
                    + self._collect_hits(messages, self._PROMISE_BREAK_KW, "sent", 2)
                ),
            },
            "dark_triad": {},
            "cbt_distortions": {},
            "defensive_communication": {},
            "communication": {
                "pursuit": self._collect_hits(messages, self._PURSUIT_KEYWORDS, "received"),
                "withdraw": self._collect_hits(messages, self._WITHDRAW_KEYWORDS, "received"),
            },
        }

        # 对方自述依恋类型（行为验证证据）
        for label, kws in self._SELF_REPORT_KW.items():
            h = self._collect_hits(messages, kws, "received", 1)
            for item in h:
                item["self_report_label"] = label
            ev["behavior_verification"]["self_report"].extend(h)

        # 黑暗三联征 / CBT 认知扭曲 / 防御性沟通：按子类别收集
        for cat, kws in self._DARK_TRIAD_SIGNALS.items():
            h = self._collect_hits(messages, kws, "received")
            if h:
                ev["dark_triad"][cat] = h
        for cat, kws in self._CBT_SIGNALS.items():
            h = self._collect_hits(messages, kws, "received")
            if h:
                ev["cbt_distortions"][cat] = h
        for cat, kws in self._DEFENSIVE_SIGNALS.items():
            h = self._collect_hits(messages, kws, "received")
            if h:
                ev["defensive_communication"][cat] = h

        # 证据覆盖度账本：哪些标签有直接原文证据
        ev["coverage"] = {
            "attachment.avoidance": bool(ev["attachment"]["avoidance"]),
            "attachment.anxiety": bool(ev["attachment"]["anxiety"]),
            "attachment.security": bool(ev["attachment"]["security"]),
            "dark_triad": bool(ev["dark_triad"]),
            "cbt_distortions": bool(ev["cbt_distortions"]),
            "defensive_communication": bool(ev["defensive_communication"]),
            "trustworthiness.promises": bool(ev["trustworthiness"]["promises"]),
            "trustworthiness.broken_promises": bool(ev["trustworthiness"]["broken_promises"]),
            "behavior_verification.self_report": bool(
                ev["behavior_verification"]["self_report"]),
        }
        return ev

    def _attachment_behavior_signals(self, messages: list) -> dict:
        """从消息时间序列中提取行为指标（自述关键词之外的真实行为模式）。

        统计维度（v3 重构：时间 + 内容双感知，修复"秒回=焦虑/连发=焦虑"假阳性）：
          - 对方回复我的延迟（慢=回避）；
          - 秒回内容分类：贬低/打太极/冷淡→avoid，追问/怕抛弃→anx，温暖/中性→sec；
          - 连发多条：仅当时间紧邻且含追问/抗议内容才计 anx，正常分条陈述不算；
          - 内容情感退缩：回复含贬低/否定/煤气灯/推卸/辱骂，或极短打发，即使秒回也计 avoid；
          - 主动消失：我方热络发起后对方长时间无回复（阈值 ≥6 小时）计 avoid；
          - 冲突后反应（沉默=回避退缩，愿意回应=安全修复）；
          - 情感表达密度（极少=回避）。

        Returns:
            dict: {avoid, anx, sec, reply_lag_count, other_msg_count}
        """
        avoid = anx = sec = 0
        seq = [{"dir": m.get("direction"),
                "content": (m.get("content") or "").strip(),
                "dt": self._parse_dt(m)} for m in messages]

        # ---- 内容分类词表（复用已有词表，检测架构层而非新堆关键词）----
        # 冷淡/退缩词：打太极/贬低/否定/煤气灯/推卸/辱骂/冲动无情。
        # 关键保护：剔除"操纵"——它在 BPD 上大量命中（你下来/我就在这等），
        #         那是 BPD 的焦虑性抗议，纳入会把 BPD 误判成情感退缩。
        cold_words = list(self._AVOID_KW)
        for _cat in ("贬低感受", "否定现实", "煤气灯贬低", "推卸责任"):
            cold_words += self._DEFENSIVE_SIGNALS.get(_cat, [])
        for _cat in ("辱骂贬低", "冲动无情"):
            cold_words += self._DARK_TRIAD_SIGNALS.get(_cat, [])
        # 极短打发词（≤4字且为负面/敷衍），中性的"嗯/哦"不计，避免误伤安全型
        _SHORT_DISMISS = ("行吧", "算了", "随便", "不知道", "没事", "没什么",
                          "无所谓", "滚", "呵", "哦。", "哦,", "哦，")
        # 焦虑内容词：追问/确认/怕抛弃
        anxious_words = list(self._ANXIOUS_KW) + [
            "为什么不回", "怎么不回", "怎么还不回", "你到底", "说清楚",
            "你说话啊", "你在哪", "你怎么不", "还要多久",
        ]
        # 温暖/安全内容词
        warm_words = list(self._SECURE_KW)

        def _is_cold(t: str) -> bool:
            if any(w in t for w in cold_words):
                return True
            if len(t) <= 4 and any(w in t for w in _SHORT_DISMISS):
                return True
            return False

        def _is_anx(t: str) -> bool:
            return any(w in t for w in anxious_words)

        def _is_warm(t: str) -> bool:
            return any(w in t for w in warm_words)

        # 1) 回应延迟：我(sent) -> 对方(received) 的间隔，附带回复内容
        replies = []  # (lag_min, content)
        for i in range(1, len(seq)):
            if (seq[i]["dir"] == "received" and seq[i - 1]["dir"] == "sent"
                    and seq[i]["dt"] and seq[i - 1]["dt"]):
                lag_min = (seq[i]["dt"] - seq[i - 1]["dt"]).total_seconds() / 60.0
                if lag_min >= 0:
                    replies.append((lag_min, seq[i]["content"]))
        reply_lags = [l for l, _ in replies]

        other_msgs = [s for s in seq if s["dir"] == "received" and s["content"]]

        if reply_lags:
            med = sorted(reply_lags)[len(reply_lags) // 2]
            # 回复慢 = 回避
            if med >= 240:        # 中位延迟 >=4 小时
                avoid += 2
            elif med >= 60:       # >=1 小时
                avoid += 1

            # —— 秒回内容感知（修复"秒回一律=焦虑"假阳性）——
            fast = [(lag, c) for lag, c in replies if lag <= 2]  # ≤2 分钟
            fast_cold = sum(1 for _, c in fast if _is_cold(c))
            fast_anx = sum(1 for _, c in fast if (not _is_cold(c)) and _is_anx(c))
            fast_warm = len(fast) - fast_cold - fast_anx        # 温暖/中性正常响应
            if fast_cold >= 1:
                avoid += min(2, fast_cold)        # 秒回但贬低/打太极 → 退缩，不计 anx
            anx += min(3, fast_anx)               # 秒回且追问/怕抛弃 → 才算焦虑
            if fast_warm >= 2:
                sec += min(2, fast_warm // 2)    # 温暖/中性秒回 → 安全基底

            # 稳定且不过慢不过快 = 安全
            if 5 <= med <= 180:
                sec += 1

        # 1.5) 内容情感退缩（含非秒回回复）：对方任意回复含贬低/否定/煤气灯/推卸/辱骂
        #      或极短打发，计 avoid（即使回复很快也计）。
        #      去重：与上面 fast_cold 已计入的不重复——这里统计全部 received 中
        #      命中冷淡词的独立条数（独立消息维度，与延迟维度正交）。
        cold_msg_count = sum(1 for s in other_msgs if _is_cold(s["content"]))
        avoid += min(3, cold_msg_count)

        # 1.6) 主动消失（ghosting）：我方发起后对方长时间（≥6 小时）无回复计回避。
        #      阈值说明：日常隔夜沉默属正常，≥6 小时且是对我方主动消息的悬空，
        #      才判为情感退缩/回避。
        GHOST_HOURS = 6.0
        ghost = 0
        for i in range(1, len(seq)):
            if seq[i - 1]["dir"] == "sent" and seq[i]["dir"] == "received" \
                    and seq[i]["dt"] and seq[i - 1]["dt"]:
                gap_h = (seq[i]["dt"] - seq[i - 1]["dt"]).total_seconds() / 3600.0
                if gap_h >= GHOST_HOURS:
                    ghost += 1
        avoid += min(2, ghost)

        # 2) 我没回时对方连发多条 = 追问/焦虑（改为时间 + 内容双感知）
        #    仅当连发在时间上紧邻（相邻间隔 ≤5 分钟）且含追问/抗议内容才计 anx；
        #    正常的分条陈述（间隔大）或中性独白不计。
        burst_anx = 0
        j = 0
        while j < len(seq):
            if seq[j]["dir"] == "received":
                k = j
                while k < len(seq) and seq[k]["dir"] == "received":
                    k += 1
                if k - j >= 2:
                    # 时间是否紧邻
                    tight = True
                    for m in range(j, k - 1):
                        d1, d2 = seq[m]["dt"], seq[m + 1]["dt"]
                        if not (d1 and d2) or ((d2 - d1).total_seconds() / 60.0) > 5:
                            tight = False
                            break
                    # 内容是否含追问/抗议
                    has_protest = any(_is_anx(seq[m]["content"])
                                      for m in range(j, k))
                    if tight and has_protest:
                        burst_anx += 1
                j = k
            else:
                j += 1
        anx += min(3, burst_anx)

        # 3) 冲突后行为：我发了冲突/情绪词，看对方回应速度
        conflict_silent = conflict_repair = 0
        for i in range(1, len(seq)):
            if seq[i - 1]["dir"] == "sent" and seq[i]["dir"] == "received":
                my_c = seq[i - 1]["content"]
                if any(w in my_c for w in self._CONFLICT_WORDS):
                    if seq[i]["dt"] and seq[i - 1]["dt"]:
                        lag_h = (seq[i]["dt"] - seq[i - 1]["dt"]).total_seconds() / 3600.0
                        if lag_h >= 6:
                            conflict_silent += 1     # 冲突时沉默消失 = 回避
                        else:
                            conflict_repair += 1     # 冲突时愿意沟通 = 安全
        avoid += min(3, conflict_silent)
        sec += min(2, conflict_repair)

        # 4) 情感表达密度：对方消息中含情感词的比例
        if other_msgs:
            emo_hits = sum(1 for s in other_msgs
                           if any(w in s["content"] for w in self._EMO_WORDS))
            ratio = emo_hits / len(other_msgs)
            if ratio < 0.05:
                avoid += 1                       # 情感表达极少 = 退缩
            elif ratio > 0.20:
                sec += 1

        return {"avoid": avoid, "anx": anx, "sec": sec,
                "reply_lag_count": len(reply_lags),
                "other_msg_count": len(other_msgs)}

    def _analyze_attachment(self, other_text: str, my_text: str, messages: list) -> dict:
        """三维依恋评分：回避倾向 / 焦虑倾向 / 安全基底（0-100）。

        - 行为指标占 60% 权重，关键词占 40%；
        - 长期行为模式 + 上下语境 + 跨时间一致性；
        - 至少 30 条消息才给稳定评分，不足时给中等默认值并标注"数据不足"；
        - 根据三维组合识别主类型（疏离型回避/焦虑型/恐惧型/安全型/混合型）。
        """
        total_msgs = len(messages or [])

        # ---- 关键词命中（40%）----
        # 上下语境加权：吵架时出现的回避词，回避含义更强
        avoid_kw_hits = sum(1 for kw in self._AVOID_KW if kw in other_text)
        anx_kw_hits = sum(1 for kw in self._ANXIOUS_KW if kw in other_text)
        sec_kw_hits = sum(1 for kw in self._SECURE_KW if kw in other_text)

        # 冲突语境里的"要空间"额外加权（同一句话吵架时=回避）
        if any(w in my_text for w in self._CONFLICT_WORDS):
            avoid_kw_hits += 1

        # ---- 行为指标（60%）----
        beh = self._attachment_behavior_signals(messages or [])
        avoid_beh, anx_beh, sec_beh = beh["avoid"], beh["anx"], beh["sec"]

        # ---- 依恋评分交叉反馈（检测架构层，v3 新增）----
        # 把 NPD 式"情感疏离"的强证据（暗黑自恋/辱骂贬低/冲动无情 + 防御
        # 否定现实/煤气灯贬低/贬低感受/推卸责任）加权到回避倾向，避免只靠
        # "我要空间"这类显式回避词漏判隐蔽型/轻蔑型 NPD。
        #
        # 关键保护（避免 BPD 误判）：**剔除"操纵"**。
        #   暗黑"操纵"在 BPD 文本上大量命中（你下来/我就在这等/我就/你最好），
        #   那是 BPD 的焦虑性抗议（追着要确认），不是情感退缩；若把"操纵"计入
        #   回避，会把高焦虑的 BPD 误抬成回避型。BPD 的焦虑由 _ANXIOUS_KW 与
        #   行为信号承担，此处一律不读"操纵"。
        #
        # 加权规则（非拍脑袋，按证据强度分级）：
        #   辱骂贬低 / 煤气灯贬低 = 3（最伤人，主动贬损对方人格）
        #   自恋 / 否定现实 / 冲动无情 / 贬低感受 / 推卸责任 = 2
        #   仅统计对方文本(other_text)中"命中不同关键词个数"，与 _analyze_psychology
        #   的口径一致；叠加到行为证据池（与行为分同权重通道），并封顶 6 分
        #   （避免单一文本把回避分顶到失真）。
        _cross_avoid_cat_weight = {
            "自恋": 2, "辱骂贬低": 3, "冲动无情": 2,          # 暗黑（不含 操纵）
            "否定现实": 2, "煤气灯贬低": 3, "贬低感受": 2, "推卸责任": 2,  # 防御
        }
        cross_avoid = 0
        for _cat, _w in _cross_avoid_cat_weight.items():
            _src = (self._DARK_TRIAD_SIGNALS.get(_cat)
                    or self._DEFENSIVE_SIGNALS.get(_cat) or [])
            for _kw in _src:
                if _kw in other_text:
                    cross_avoid += _w
        cross_avoid = min(6, cross_avoid)
        avoid_beh += cross_avoid

        def _combine(beh_hits: int, kw_hits: int) -> int:
            beh_score = min(100.0, beh_hits / self._BEH_SATURATION * 100.0)
            kw_score = min(100.0, kw_hits / self._KW_SATURATION * 100.0)
            return int(round(beh_score * 0.6 + kw_score * 0.4))

        if total_msgs < self._MIN_MSGS_FOR_STABLE:
            # 数据不足：以50为基线，关键词命中小幅推离（每命中1条+5，上限70）
            # 这样即使消息少，明确的回避/焦虑/安全信号也能把分数推过阈值
            avoidance_score = min(70, 50 + avoid_kw_hits * 5)
            anxiety_score = min(70, 50 + anx_kw_hits * 5)
            security_score = min(70, 50 + sec_kw_hits * 5)
            confidence = "low"
            data_note = "数据不足（<30条消息），评分基于关键词信号，需积累更多聊天记录确认"
        else:
            avoidance_score = _combine(avoid_beh, avoid_kw_hits)
            anxiety_score = _combine(anx_beh, anx_kw_hits)
            security_score = _combine(sec_beh, sec_kw_hits)
            # v5 修复：confidence 不能只看消息总量——2 条回避证据 + 大量闲聊也会
            # 被标 high。改为按"有效证据量"（关键词命中类别数 + 行为信号数）判定：
            evidence_hits = avoid_kw_hits + anx_kw_hits + sec_kw_hits + avoid_beh + anx_beh + sec_beh
            confidence = "high" if (total_msgs >= 60 and evidence_hits >= 6) else "medium"
            data_note = "" if confidence == "high" else "有效证据较少，评分仅供参考"

        # ---- 混合型识别（按三维组合）----
        a, x = avoidance_score, anxiety_score

        # BPD推拉模式检测：高焦虑 + 频繁提分手/攻击推开 = 恐惧型的一种变体
        # BPD的回避不表现为"要空间"，而是表现为"愤怒推开/提分手"（push-pull）
        push_away_signals = ["分手吧", "我们分手", "我受够了", "滚", "别理我",
                            "我去死", "割腕", "你根本不爱我", "你和所有男人一样"]
        push_away_count = sum(other_text.count(s) for s in push_away_signals)
        is_push_pull = x >= 75 and push_away_count >= 10

        if a >= 60 and x < 40:
            primary = "疏离型回避"
        elif x >= 60 and a < 40 and not is_push_pull:
            primary = "焦虑型"
        elif (a >= 60 and x >= 60) or is_push_pull:
            primary = "恐惧型"
        elif a < 40 and x < 40:
            primary = "安全型"
        else:
            primary = "混合型"

        # mixed_note：描述偏向
        def _lvl(v):
            return "高" if v >= 60 else ("中" if v >= 40 else "低")
        if primary == "混合型":
            lean = "偏回避" if a >= x else "偏焦虑"
            mixed_note = (f"高/中回避倾向({a})+{'高' if x>=60 else '中'}焦虑倾向({x})，"
                          f"属混合型，{lean}，在不同场景下两种模式交替出现")
        else:
            mixed_note = f"{_lvl(a)}回避倾向({a})+{_lvl(x)}焦虑倾向({x})，主类型为{primary}"

        descriptions = {
            "疏离型回避": "害怕亲密，需要独立空间，压力下情感退缩、回避冲突（dismissive）",
            "焦虑型": "害怕被抛弃，需要频繁确认爱意，容易过度解读对方行为（anxious/preoccupied）",
            "恐惧型": "既渴望亲密又害怕受伤，在亲近与疏远之间摇摆（fearful/disorganized）",
            "安全型": "能够信任他人，情绪稳定，处理冲突理性健康（secure）",
            "混合型": "回避与焦虑倾向混合，需结合场景判断主导模式",
        }

        result = {
            "avoidance_score": avoidance_score,
            "anxiety_score": anxiety_score,
            "security_score": security_score,
            "primary_type": primary,
            "mixed_note": mixed_note,
            "confidence": confidence,
            # 向后兼容字段
            "type": primary,
            "scores": {"回避倾向": avoidance_score, "焦虑倾向": anxiety_score,
                       "安全基底": security_score},
            "description": descriptions.get(primary, ""),
        }
        if data_note:
            result["data_note"] = data_note
        return result

    # ------------------------------------------------------------------ #
    # 行为验证机制（自述 vs 实证）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _attachment_axis(primary_type: str) -> str:
        """把主类型标签归一到 axis：avoidant/anxious/secure/fearful/mixed。"""
        t = str(primary_type)
        if "回避" in t and "恐惧" not in t:
            return "avoidant"
        if "焦虑" in t:
            return "anxious"
        if "恐惧" in t:
            return "fearful"
        if "安全" in t:
            return "secure"
        return "mixed"

    def _analyze_behavior_verification(self, other_text: str, attachment: dict) -> dict:
        """自述（self_report）与实证（behavioral_evidence）交叉验证。

        - 对方说的话 -> 自述；
        - 系统基于行为+关键词的三维评分 -> 实证；
        - 不一致时以实证为准并标注。
        """
        # 1. 检测对方自述的依恋类型
        self_report = None
        for label, kws in self._SELF_REPORT_KW.items():
            if any(kw in other_text for kw in kws):
                self_report = label
                break

        behavioral_assessment = attachment.get("primary_type", "未知")

        if not self_report:
            return {
                "self_report_attachment": None,
                "behavioral_assessment": behavioral_assessment,
                "consistent": None,
                "note": "对方未明确自述依恋类型，以行为实证判断为准",
            }

        report_axis = self._attachment_axis(self_report)
        behavior_axis = self._attachment_axis(behavioral_assessment)
        consistent = (report_axis == behavior_axis)

        if consistent:
            note = (f"对方自述为{self_report}，与行为实证（{behavioral_assessment}）一致，"
                    f"自述可信度高，可作为判断参考")
        else:
            axis_cn = {"avoidant": "回避倾向", "anxious": "焦虑倾向",
                       "fearful": "恐惧型", "secure": "安全型", "mixed": "混合型"}
            note = (f"对方自述为{self_report}，但行为实证表现为{behavioral_assessment}"
                    f"（{axis_cn.get(behavior_axis, behavior_axis)}），自述与行为不一致；"
                    f"可能是用「{self_report}」作为不投入/回避关系的借口，建议以行为判断为准")

        return {
            "self_report_attachment": self_report,
            "behavioral_assessment": behavioral_assessment,
            "consistent": consistent,
            "note": note,
        }

    # ------------------------------------------------------------------ #
    # 可信度 / 言行一致性维度
    # ------------------------------------------------------------------ #
    def _analyze_trustworthiness(self, messages: list, other_text: str, my_text: str) -> dict:
        """可信度维度：承诺兑现率 + 言行一致性 + 综合可信度。

        优先尝试 commitment_tracker.get_fulfillment_rate（若存在）；
        否则防御性地从聊天记录提取承诺关键词并统计爽约信号。
        """
        # 尝试调用外部 commitment_tracker（若已注入且可用）
        external_rate = None
        try:
            ct = getattr(self, "commitment_tracker", None)
            if ct is not None and hasattr(ct, "get_fulfillment_rate"):
                # 不在这里依赖 person_id，由调用方注入；取不到就走自建逻辑
                external_rate = None
        except Exception:
            external_rate = None

        # ---- 承诺兑现率（事件化 v6：按独立消息统计，修复"一条消息命中多个关键词导致承诺数膨胀、兑现率被摊高"）----
        # 方向字段缺失时兼容旧调用：全部消息按对方文本处理；有方向时只统计 received（对方），
        # 避免把"我"的质问/提醒（"你答应我的呢""你又没兑现"）算成对方爽约。
        _has_dir = any(isinstance(m, dict) and m.get("direction") in ("received", "sent") for m in messages)
        _cands = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            if _has_dir and m.get("direction") != "received":
                continue
            _cands.append(str(m.get("content") or ""))
        promise_events = sum(1 for c in _cands if any(kw in c for kw in self._PROMISE_KW))
        break_events = 0
        for c in _cands:
            for kw in self._PROMISE_BREAK_KW:
                if kw not in c:
                    continue
                _at = c.find(kw)
                before = c[max(0, _at - 2): _at]
                if kw == "忘了" and any(b in before for b in ("怕", "别", "不会", "千万", "小心")):
                    continue
                break_events += 1
                break  # 一条消息算一个爽约事件，避免"忘了/下次再说"同句重复计数
        promise_count = promise_events
        break_count = break_events

        if promise_count <= 0:
            fulfillment_rate = 70  # 无承诺记录，给中性偏保守默认
            fulfillment_note = "聊天中未检测到明确承诺记录"
        else:
            fulfillment_rate = max(0.0, 100.0 - break_count / max(promise_count, 1) * 100.0)
            fulfillment_rate = round(fulfillment_rate, 1)
            fulfillment_note = f"检测到约{promise_count}次承诺表态，{break_count}次爽约/被抱怨未兑现"

        # ---- 言行一致性 ----
        # 爽约本身就是"言行不一"的实证：每条爽约扣 8（事件级，不再按词摊薄）；
        # 说过"在乎/爱你/我会改"却仍爽约 → 每条再扣 3（更严重）
        love_words = sum(other_text.count(kw) for kw in self._INCONSISTENT_WORDS)
        consistency = 70.0 - break_count * 8.0
        if love_words > 0:
            consistency -= break_count * 3.0
        consistency = max(0.0, min(100.0, consistency))

        overall = round(fulfillment_rate * 0.5 + consistency * 0.5, 1)

        if overall >= 80:
            level = "high"
        elif overall >= 60:
            level = "medium"
        elif overall >= 40:
            level = "low"
        else:
            level = "critical"

        return {
            "promise_fulfillment_rate": fulfillment_rate,
            "word_action_consistency": round(consistency, 1),
            "overall_trust_score": overall,
            "level": level,
            "promise_count": promise_count,
            "break_count": break_count,
            "note": f"对方承诺兑现率{fulfillment_rate}%，言行一致性{round(consistency,1)}%，"
                    f"综合可信度{level}。{fulfillment_note}",
        }

    def _analyze_communication(self, messages: list) -> dict:
        """分析沟通模式。"""
        text = " ".join(m["content"] for m in messages if m["content"])

        pursuit = sum(1 for kw in self._PURSUIT_KEYWORDS if kw in text)
        withdraw = sum(1 for kw in self._WITHDRAW_KEYWORDS if kw in text)
        tsundere = sum(1 for kw in self._TSUNDERE_KEYWORDS if kw in text)

        # 分析对话轮次
        turn_changes = sum(1 for i in range(1, len(messages))
                          if messages[i]["direction"] != messages[i-1]["direction"])
        avg_turn_length = len(messages) / max(1, turn_changes)

        # 判断主模式
        if pursuit > 0 and withdraw > 0:
            pattern = "追逃模式"
            desc = "一方追问一方逃避，冲突时容易形成恶性循环"
        elif tsundere >= 3:
            pattern = "打情骂俏型"
            desc = "嘴上嫌弃实际亲密，用反话表达爱意，互动轻松有趣"
        elif withdraw > pursuit:
            pattern = "冷战倾向"
            desc = "遇到问题倾向于回避沟通，需要主动破冰"
        elif pursuit > withdraw:
            pattern = "主动沟通型"
            desc = "遇到问题愿意主动沟通，需要注意方式方法"
        else:
            pattern = "平等对话型"
            desc = "沟通相对平等自然，双方都愿意表达"

        return {
            "pattern": pattern,
            "description": desc,
            "pursuit_signals": pursuit,
            "withdraw_signals": withdraw,
            "tsundere_signals": tsundere,
            "avg_turn_length": round(avg_turn_length, 1),
        }

    def _analyze_relationship_stage(self, messages: list, other_text: str, my_text: str) -> dict:
        """分析关系阶段。"""
        all_text = other_text + " " + my_text

        marriage_signals = sum(1 for kw in self._MARRIAGE_SIGNALS if kw in all_text)
        long_term_signals = sum(1 for kw in self._LONG_TERM_SIGNALS if kw in all_text)
        freshness_decline = sum(1 for kw in self._FRESHNESS_DECLINE if kw in all_text)

        # 时间跨度
        if messages and len(messages) >= 2:
            try:
                first = datetime.fromisoformat(messages[0]["created_at"])
                last = datetime.fromisoformat(messages[-1]["created_at"])
                days = (last - first).days
            except (ValueError, TypeError):
                days = 0
        else:
            days = 0

        # 判断阶段
        if marriage_signals >= 2:
            stage = "已婚"
            desc = "已进入婚姻关系，需要关注长期经营和新鲜感维护"
        elif long_term_signals >= 2 or days > 365:
            stage = "长期关系"
            desc = "关系稳定但可能进入平淡期，需要主动制造新鲜感"
        elif "老公" in all_text or "老婆" in all_text or "宝贝" in all_text:
            stage = "热恋期"
            desc = "亲密度高，互动频繁，处于感情上升期"
        elif "想你" in all_text or "喜欢" in all_text:
            stage = "暧昧期"
            desc = "互有好感但未明确关系，需要把握推进时机"
        else:
            stage = "初识期"
            desc = "关系刚开始，需要建立信任和舒适感"

        # 七年之痒检测
        seven_year_itch = days >= 2555 or (marriage_signals >= 2 and freshness_decline >= 1)

        return {
            "stage": stage,
            "description": desc,
            "days_span": days,
            "marriage_signals": marriage_signals,
            "long_term_signals": long_term_signals,
            "freshness_decline_signals": freshness_decline,
            "seven_year_itch": seven_year_itch,
            "risk_level": "高" if seven_year_itch or freshness_decline >= 2 else
                        ("中" if long_term_signals >= 2 else "低"),
        }

    def _analyze_psychology(self, other_text: str, my_text: str) -> dict:
        """分析心理特征（对方维度：只看对方文本 other_text）。"""
        # 对方心理画像：只分析对方文本，不混入我方表达
        # CBT认知扭曲（对方维度）
        cbt_findings = {}
        for distortion, keywords in self._CBT_SIGNALS.items():
            count = sum(1 for kw in keywords if kw in other_text)
            if count > 0:
                cbt_findings[distortion] = count

        # 防御性沟通（对方维度）
        defensive_findings = {}
        for pattern, keywords in self._DEFENSIVE_SIGNALS.items():
            count = sum(1 for kw in keywords if kw in other_text)
            if count > 0:
                defensive_findings[pattern] = count

        # 黑暗三角（对方维度）
        dark_findings = {}
        for trait, keywords in self._DARK_TRIAD_SIGNALS.items():
            count = sum(1 for kw in keywords if kw in other_text)
            if count > 0:
                dark_findings[trait] = count

        # 对方主导的心理特征
        other_cbt = {}
        for distortion, keywords in self._CBT_SIGNALS.items():
            count = sum(1 for kw in keywords if kw in other_text)
            if count > 0:
                other_cbt[distortion] = count

        return {
            "cbt_distortions": cbt_findings,
            "defensive_communication": defensive_findings,
            "dark_triad_tendencies": dark_findings,
            "other_primary_traits": list(other_cbt.keys())[:3],
            "overall_health": "健康" if not defensive_findings and not dark_findings and len(cbt_findings) <= 1
                             else "需关注" if len(cbt_findings) <= 2 and not dark_findings
                             else "风险",
        }

    def _analyze_emotion_pattern(self, other_msgs: list) -> dict:
        """分析情绪模式。"""
        positive_words = ["开心", "快乐", "喜欢", "爱你", "想你", "哈哈", "好呀", "宝贝", "么么哒"]
        negative_words = ["烦", "累", "生气", "难过", "伤心", "不想", "算了", "随便", "别烦我"]
        neutral_words = ["嗯", "哦", "好的", "知道了", "嗯嗯"]

        pos_count = sum(1 for m in other_msgs if any(w in (m["content"] or "") for w in positive_words))
        neg_count = sum(1 for m in other_msgs if any(w in (m["content"] or "") for w in negative_words))
        neu_count = sum(1 for m in other_msgs if any(w in (m["content"] or "") for w in neutral_words))

        total = max(1, len(other_msgs))
        pos_ratio = pos_count / total
        neg_ratio = neg_count / total

        if pos_ratio > 0.3:
            pattern = "积极主导"
        elif neg_ratio > 0.2:
            pattern = "消极倾向"
        elif pos_ratio > neg_ratio:
            pattern = "偏积极"
        else:
            pattern = "中性平稳"

        return {
            "pattern": pattern,
            "positive_count": pos_count,
            "negative_count": neg_count,
            "neutral_count": neu_count,
            "positive_ratio": round(pos_ratio, 2),
            "negative_ratio": round(neg_ratio, 2),
            "volatility": "高" if abs(pos_ratio - neg_ratio) < 0.1 and pos_count > 0 and neg_count > 0 else "低",
        }

    def _analyze_investment(self, messages: list, other_msgs: list, my_msgs: list) -> dict:
        """分析投入程度。"""
        # 对方主动开启对话次数
        other_starts = 0
        for i, m in enumerate(messages):
            if m["direction"] == "received":
                if i == 0 or messages[i-1]["direction"] == "sent":
                    other_starts += 1

        # 对方消息平均长度
        other_lengths = [len(m["content"] or "") for m in other_msgs if m["content"]]
        avg_other_length = sum(other_lengths) / max(1, len(other_lengths))

        # 投资行为
        investment_keywords = ["点了外卖", "给你买", "帮你", "陪你", "等你", "接你", "送你",
                              "记得吃饭", "注意身体", "别太累", "早点睡", "多喝水"]
        other_investment = sum(1 for m in other_msgs
                              if any(kw in (m["content"] or "") for kw in investment_keywords))

        # 主动爱意表达
        love_keywords = ["我爱你", "爱你", "想你", "想你了", "喜欢你", "么么哒", "宝贝"]
        other_love = sum(1 for m in other_msgs
                        if any(kw in (m["content"] or "") for kw in love_keywords))

        total = max(1, len(messages))
        other_ratio = len(other_msgs) / total

        if other_starts >= 5 and other_investment >= 2:
            level = "高投入"
        elif other_starts >= 3 or other_love >= 3:
            level = "中高投入"
        elif other_ratio > 0.4:
            level = "中等投入"
        else:
            level = "低投入"

        return {
            "level": level,
            "other_conversation_starts": other_starts,
            "other_avg_message_length": round(avg_other_length, 1),
            "other_investment_acts": other_investment,
            "other_love_expressions": other_love,
            "other_message_ratio": round(other_ratio, 2),
        }

    # ------------------------------------------------------------------ #
    # 投入平衡分析（双向奔赴维度）：情感/时间/金钱三维投入比
    # ------------------------------------------------------------------ #
    # 主动发起会话的时间间隔阈值（分钟）：距离上一条消息超过该时长才算"重新发起"
    _CONVERSATION_GAP_MIN: float = 30.0
    # 金钱付出关键词（按方向区分谁在付出）
    _MY_GIVE_KEYWORDS: tuple = (
        "我请", "我买给你", "我给你买", "我发红包", "转账给你", "我转你",
        "我来请", "我买单", "我送你", "我付钱",
    )
    _OTHER_GIVE_KEYWORDS: tuple = (
        "我请你", "给你买", "我给你买", "发红包", "转账给你", "我转你",
        "我来请", "我买单", "我送你", "送你礼物", "我付钱",
    )

    def _analyze_investment_balance(self, messages: list,
                                    other_msgs: list, my_msgs: list) -> dict:
        """投入平衡分析：统计双方在情感/时间/金钱三维度的投入比。

        核心原则：不是不给对方付出，而是不能单方面超额付出。
        最终目的是引导双向奔赴——让对方也为你投入（沉没成本效应），
        形成互相投资的健康关系，而非单方面舔。

        计算方式：
          - 情感投入比 = 用户主动发起对话数 / max(对方主动发起对话数, 1)；
            主动发起 = 连续消息中第一条，且距离上一条 >30 分钟；
          - 时间投入比 = 用户消息总字数 / max(对方消息总字数, 1)；
            （同时统计双方平均回复延迟作为参考，不进综合比）
          - 金钱投入比 = 用户付出次数 / max(对方付出次数, 1)；
            无任何金额相关消息时默认 1:1。

        健康范围：
          - 0.7-1.3  balanced（双方投入接近，健康）；
          - >1.3     user_overinvest（用户单方面超额，需引导对方投入）；
          - <0.7     other_overinvest（对方超额，警惕无事献殷勤，可适当接受）。

        Returns:
            dict: {emotional_ratio, time_ratio, money_ratio, overall_ratio,
                   status, note, my_starts, other_starts, my_chars, other_chars,
                   my_give, other_give, my_reply_lag_min, other_reply_lag_min}
        """
        # 数据兜底：无消息时默认平衡
        if not messages:
            return {
                "emotional_ratio": 1.0, "time_ratio": 1.0, "money_ratio": 1.0,
                "overall_ratio": 1.0, "status": "balanced",
                "note": "无聊天数据，默认双方投入平衡",
                "my_starts": 0, "other_starts": 0,
                "my_chars": 0, "other_chars": 0, "my_give": 0, "other_give": 0,
                "my_reply_lag_min": 0.0, "other_reply_lag_min": 0.0,
            }

        # ---- 情感投入：主动发起会话次数 ----
        # 主动发起 = 连续消息簇的第一条，且距上一条 >30 分钟（首条也算）
        my_starts = 0
        other_starts = 0
        prev_dt = None
        for m in messages:
            dt = self._parse_dt(m)
            is_start = True
            if prev_dt is not None and dt is not None:
                gap_min = (dt - prev_dt).total_seconds() / 60.0
                if gap_min <= self._CONVERSATION_GAP_MIN:
                    is_start = False
            if is_start:
                if m.get("direction") == "sent":
                    my_starts += 1
                elif m.get("direction") == "received":
                    other_starts += 1
            if dt is not None:
                prev_dt = dt
            # dt 为 None 时不更新 prev_dt，沿用上一个有效时间点
        emotional_ratio = my_starts / max(1, other_starts)

        # ---- 时间投入：消息总字数 + 双方平均回复延迟（参考）----
        my_chars = sum(len(m.get("content") or "") for m in my_msgs)
        other_chars = sum(len(m.get("content") or "") for m in other_msgs)
        time_ratio = my_chars / max(1, other_chars)

        # 用户平均回复延迟（对方 received -> 我 sent）与 对方平均回复延迟（我 sent -> 对方 received）
        my_lags = []
        other_lags = []
        ordered = list(messages)
        for i in range(1, len(ordered)):
            d_prev = ordered[i - 1].get("direction")
            d_cur = ordered[i].get("direction")
            t_prev = self._parse_dt(ordered[i - 1])
            t_cur = self._parse_dt(ordered[i])
            if not t_prev or not t_cur or t_cur < t_prev:
                continue
            lag_min = (t_cur - t_prev).total_seconds() / 60.0
            if d_prev == "received" and d_cur == "sent":
                my_lags.append(lag_min)
            elif d_prev == "sent" and d_cur == "received":
                other_lags.append(lag_min)
        my_reply_lag = round(sum(my_lags) / len(my_lags), 1) if my_lags else 0.0
        other_reply_lag = round(sum(other_lags) / len(other_lags), 1) if other_lags else 0.0

        # ---- 金钱投入：付出次数估算 ----
        my_give = sum(1 for m in my_msgs
                      if any(kw in (m.get("content") or "") for kw in self._MY_GIVE_KEYWORDS))
        other_give = sum(1 for m in other_msgs
                         if any(kw in (m.get("content") or "") for kw in self._OTHER_GIVE_KEYWORDS))
        if my_give == 0 and other_give == 0:
            money_ratio = 1.0  # 无金额相关消息时默认 1:1
        else:
            money_ratio = my_give / max(1, other_give)

        # ---- 综合投入比 ----
        overall_ratio = round((emotional_ratio + time_ratio + money_ratio) / 3.0, 2)

        if overall_ratio > 1.3:
            status = "user_overinvest"
            note = ("当前投入比偏单方面，用户投入较多，建议引导对方也主动参与进来，"
                    "形成双向奔赴")
        elif overall_ratio < 0.7:
            status = "other_overinvest"
            note = ("对方投入略多于你，可以适当接受，但建议观察是否有后续目的，"
                    "引导双向奔赴而非单方面接受")
        else:
            status = "balanced"
            note = "双方投入接近，关系健康，继续维持双向奔赴的节奏"

        return {
            "emotional_ratio": round(emotional_ratio, 2),
            "time_ratio": round(time_ratio, 2),
            "money_ratio": round(money_ratio, 2),
            "overall_ratio": overall_ratio,
            "status": status,
            "note": note,
            "my_starts": my_starts,
            "other_starts": other_starts,
            "my_chars": my_chars,
            "other_chars": other_chars,
            "my_give": my_give,
            "other_give": other_give,
            "my_reply_lag_min": my_reply_lag,
            "other_reply_lag_min": other_reply_lag,
        }

    def _generate_summary(self, attachment, communication, relationship,
                          psychology, emotion, investment,
                          love_languages=None, power_dynamic=None, emotional_bank=None,
                          conflict_style=None, boundaries=None, freshness=None, risk_warning=None,
                          relationship_satisfaction=None, commitment=None,
                          interaction_ratio=None, conflict_repair=None,
                          passion=None, trust=None, values_match=None, self_differentiation=None) -> str:
        """生成人物画像摘要（21维综合）。"""
        parts = []

        # 依恋+沟通
        parts.append(f"对方呈现{attachment['type']}依恋特征，沟通模式为{communication['pattern']}。")

        # 关系阶段
        parts.append(f"当前处于{relationship['stage']}，{relationship['description']}。")
        if relationship.get("seven_year_itch"):
            parts.append("⚠️ 检测到七年之痒风险信号，需要主动维护新鲜感。")

        # 心理特征
        if psychology.get("cbt_distortions"):
            parts.append(f"存在{', '.join(psychology['cbt_distortions'].keys())}等认知倾向。")
        if psychology.get("defensive_communication"):
            parts.append(f"沟通中有{', '.join(psychology['defensive_communication'].keys())}的防御性表现。")
        if psychology.get("dark_triad_tendencies"):
            parts.append(f"⚠️ 有{', '.join(psychology['dark_triad_tendencies'].keys())}的黑暗三角倾向，需要警惕。")

        # 情绪+投入
        parts.append(f"情绪模式{emotion['pattern']}，投入程度为{investment['level']}。")
        if investment.get("other_love_expressions", 0) >= 3:
            parts.append(f"对方主动表达爱意{investment['other_love_expressions']}次，情感投入明显。")

        # 可量化学术指标
        if relationship_satisfaction:
            parts.append(f"关系满意度{relationship_satisfaction.get('score', 0)}分（{relationship_satisfaction.get('level', '未知')}）。")
        if commitment:
            parts.append(f"承诺度{commitment.get('score', 0)}分（{commitment.get('level', '未知')}）。")
        if interaction_ratio:
            parts.append(f"积极/消极互动比{interaction_ratio.get('ratio', 0)}:1（{interaction_ratio.get('level', '未知')}）。")
        if conflict_repair and conflict_repair.get("conflict_count", 0) > 0:
            parts.append(f"冲突{conflict_repair['conflict_count']}次，修复率{conflict_repair['repair_rate']}%（{conflict_repair['level']}）。")

        # 爱的五种语言
        if love_languages:
            parts.append(f"主要爱的语言是{love_languages.get('primary', '待观察')}。{love_languages.get('advice', '')}")

        # 权力格局
        if power_dynamic:
            parts.append(f"权力格局：{power_dynamic.get('pattern', '未知')}。{power_dynamic.get('advice', '')}")

        # 情感账户
        if emotional_bank:
            parts.append(f"情感账户{emotional_bank.get('health', '未知')}（余额{emotional_bank.get('balance', 0)}）。")

        # 冲突处理
        if conflict_style:
            parts.append(f"冲突处理模式：{conflict_style.get('primary', '待观察')}。")

        # 边界感
        if boundaries:
            parts.append(f"个人边界{boundaries.get('personal_boundary', '未知')}，异性边界{boundaries.get('opposite_boundary', '未知')}。")

        # 新鲜感
        if freshness:
            parts.append(f"新鲜感：{freshness.get('level', '未知')}（{freshness.get('score', 0)}分）。")

        # 风险预警
        if risk_warning:
            risks = risk_warning.get("risks", [])
            high_risks = [r for r in risks if r.get("level") == "高"]
            if high_risks:
                for r in high_risks:
                    parts.append(f"🚨 {r['type']}：{r['signals']}。{r['advice']}")

        # 新增4个科学维度
        if passion:
            parts.append(f"激情{passion.get('level', '未知')}（{passion.get('score', 0)}分）。")
        if trust:
            parts.append(f"信任{trust.get('level', '未知')}（{trust.get('score', 0)}分）。")
        if values_match:
            parts.append(f"价值观{values_match.get('level', '未知')}（{values_match.get('score', 0)}分）。")
        if self_differentiation:
            parts.append(f"自我分化{self_differentiation.get('level', '未知')}（{self_differentiation.get('score', 0)}分）。")

        return " ".join(parts)

    def _calc_affection_adjustment(self, attachment, communication, relationship,
                                    psychology, investment, emotion_pattern=None) -> dict:
        """根据画像计算好感度调整建议。"""
        adjustments = {}
        base = 50.0

        # 依恋类型调整（兼容三维评分后的新主类型标签）
        attachment_adj = {"安全型": 5, "焦虑型": -3, "回避型": -5, "疏离型回避": -5,
                          "恐惧型": -8, "混合型": -4}
        base += attachment_adj.get(attachment.get("type"), 0)

        # 沟通模式调整
        comm_adj = {"打情骂俏型": 8, "平等对话型": 5, "主动沟通型": 3,
                   "追逃模式": -5, "冷战倾向": -10}
        base += comm_adj.get(communication["pattern"], 0)

        # 关系阶段调整
        stage_adj = {"热恋期": 10, "暧昧期": 5, "已婚": 3, "长期关系": 0, "初识期": -5}
        base += stage_adj.get(relationship["stage"], 0)
        if relationship["seven_year_itch"]:
            base -= 10

        # 心理健康调整
        if psychology["overall_health"] == "健康":
            base += 5
        elif psychology["overall_health"] == "风险":
            base -= 15

        # 投入程度调整
        inv_adj = {"高投入": 15, "中高投入": 10, "中等投入": 5, "低投入": -10}
        base += inv_adj.get(investment["level"], 0)

        # 情绪模式调整
        if emotion_pattern:
            emo_adj = {"积极主导": 8, "偏积极": 5, "中性平稳": 0, "消极倾向": -8}
            base += emo_adj.get(emotion_pattern["pattern"], 0)

        # 各维度调整
        adjustments["emotional_value"] = base + investment["other_love_expressions"] * 2
        adjustments["intimacy"] = base + communication["tsundere_signals"] * 2
        adjustments["trust"] = base + investment["other_investment_acts"] * 3
        adjustments["security"] = base - (1 if relationship["seven_year_itch"] else 0) * 10
        adjustments["romance"] = base + (5 if relationship["stage"] in ("热恋期", "暧昧期") else -5)

        # 裁剪到0-100
        for k in adjustments:
            adjustments[k] = max(0, min(100, adjustments[k]))

        adjustments["recommended_total"] = round(sum(adjustments.values()) / len(adjustments), 1)

        return adjustments

    def _save_profile(self, person_id: str, profile: dict):
        """保存画像到人物目录。"""
        if not self.db:
            return
        import json
        import os
        try:
            person_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      "data", "persons", person_id)
            os.makedirs(person_dir, exist_ok=True)
            profile_path = os.path.join(person_dir, "persona_profile.json")
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
            logger.info("人物画像已保存: %s", profile_path)
        except Exception as e:
            logger.warning("人物画像保存失败: %s", e)

    # ==================== 新增7个恋爱关系核心维度 ====================

    def _analyze_love_languages(self, other_text: str, my_text: str, messages: list) -> dict:
        """爱的五种语言分析。

        五种语言：肯定言辞、精心时刻、接受礼物、服务行动、身体接触。
        分析对方主要的爱的语言（她如何表达爱、她如何接收爱）。
        """
        languages = {
            "words_of_affirmation": {  # 肯定言辞
                "name": "肯定言辞",
                "signals": ["你真棒", "好厉害", "谢谢你", "辛苦了", "我爱你", "想你",
                           "你最好了", "有你真好", "你真好看", "你真温柔"],
                "score": 0,
            },
            "quality_time": {  # 精心时刻
                "name": "精心时刻",
                "signals": ["陪我", "一起", "见面", "约会", "出去玩", "陪陪我",
                           "在干嘛", "想见到你", "什么时候回来", "等你"],
                "score": 0,
            },
            "receiving_gifts": {  # 接受礼物
                "name": "接受礼物",
                "signals": ["送我", "礼物", "惊喜", "买给我", "想要", "好喜欢",
                           "收到了", "谢谢宝贝", "你怎么知道我想要"],
                "score": 0,
            },
            "acts_of_service": {  # 服务行动
                "name": "服务行动",
                "signals": ["帮我", "你来弄", "你弄吧", "辛苦了", "谢谢你帮我",
                           "有你在真好", "你帮我看看", "你弄的好吃"],
                "score": 0,
            },
            "physical_touch": {  # 身体接触
                "name": "身体接触",
                "signals": ["抱抱", "亲亲", "摸摸", "牵手", "靠着", "蹭蹭",
                           "想抱你", "要抱抱", "摸摸头", "捏捏"],
                "score": 0,
            },
        }

        for key, lang in languages.items():
            for signal in lang["signals"]:
                if signal in other_text:
                    lang["score"] += other_text.count(signal)
                if signal in my_text:
                    lang["score"] += my_text.count(signal) * 0.5  # 我方表达权重低一些

        # 找出主要爱的语言
        sorted_langs = sorted(languages.items(), key=lambda x: x[1]["score"], reverse=True)
        primary = sorted_langs[0][0] if sorted_langs[0][1]["score"] > 0 else "unknown"
        secondary = sorted_langs[1][0] if len(sorted_langs) > 1 and sorted_langs[1][1]["score"] > 0 else None

        return {
            "primary": languages[primary]["name"] if primary != "unknown" else "待观察",
            "primary_key": primary,
            "secondary": languages[secondary]["name"] if secondary else None,
            "scores": {k: v["score"] for k, v in languages.items()},
            "advice": self._love_language_advice(primary),
        }

    def _love_language_advice(self, primary: str) -> str:
        """根据主要爱的语言给出相处建议。"""
        advice_map = {
            "words_of_affirmation": "对方吃软不吃硬，多夸多肯定，吵架时别用言语攻击，一句暖心话顶十件事",
            "quality_time": "对方最在意陪伴质量，放下手机专心陪她，定期安排专属约会时间",
            "receiving_gifts": "对方在意仪式感，不定期小惊喜比贵重礼物更有效，记住重要纪念日",
            "acts_of_service": "对方看重实际行动，主动帮她做事比说情话更管用，细节处见真心",
            "physical_touch": "对方需要肢体接触，多抱抱多牵手，身体接触是她感受爱的主要方式",
            "unknown": "聊天记录不足，多观察对方如何表达爱和如何回应你的表达",
        }
        return advice_map.get(primary, advice_map["unknown"])

    def _analyze_power_dynamic(self, messages: list, other_msgs: list, my_msgs: list) -> dict:
        """权力格局分析。

        判断关系中谁主导、是拉扯还是平衡。
        维度：谁发起对话多、谁决定事项多、谁妥协多、语气强势度。
        """
        if not messages:
            return {"pattern": "unknown", "score": 0, "description": "数据不足"}

        # 发起对话次数（每天第一条消息是谁发的）
        my_initiate = 0
        other_initiate = 0
        last_date = None
        for m in messages:
            # 兼容 created_at 为 int/float 时间戳（旧库/外部喂数）与 ISO 字符串两种形态；
            # None/缺字段给空串，不炸。int 时 fromtimestamp 转 ISO 再切日期。
            ca = m.get("created_at")
            if ca is None or ca == "":
                msg_date = ""
            elif isinstance(ca, (int, float)):
                try:
                    msg_date = datetime.fromtimestamp(ca).isoformat()[:10]
                except Exception:
                    msg_date = ""
            else:
                msg_date = str(ca)[:10]
            if msg_date != last_date:
                if m["direction"] == "sent":
                    my_initiate += 1
                else:
                    other_initiate += 1
                last_date = msg_date

        # 消息量对比
        my_count = len(my_msgs)
        other_count = len(other_msgs)
        total = my_count + other_count or 1

        # 决策性词汇（谁在做决定）
        decision_words = ["我决定", "就这么定", "听我的", "必须", "你应该", "我说了算"]
        my_decision = sum(1 for w in decision_words if any(w in m.get("content", "") for m in my_msgs))
        other_decision = sum(1 for w in decision_words if any(w in m.get("content", "") for m in other_msgs))

        # 妥协词汇
        compromise_words = ["好吧", "行吧", "随便你", "听你的", "你说了算", "都行"]
        my_compromise = sum(1 for w in compromise_words if any(w in m.get("content", "") for m in my_msgs))
        other_compromise = sum(1 for w in compromise_words if any(w in m.get("content", "") for m in other_msgs))

        # 计算权力分数（正=我方主导，负=对方主导，接近0=平衡）
        power_score = 0
        power_score += (my_initiate - other_initiate) * 2
        power_score += (my_count - other_count) / total * 10
        power_score += (my_decision - other_decision) * 3
        power_score -= (my_compromise - other_compromise) * 2

        # 判断格局
        if power_score > 8:
            pattern = "我方主导"
            description = "你在关系中占据主导地位，对方更多配合和妥协"
        elif power_score > 3:
            pattern = "我方略占优"
            description = "你略微主动，关系整体偏向你主导"
        elif power_score > -3:
            pattern = "平衡拉扯"
            description = "双方势均力敌，有来有回，是健康的拉扯状态"
        elif power_score > -8:
            pattern = "对方略占优"
            description = "对方略微主动，你更多配合和妥协"
        else:
            pattern = "对方主导"
            description = "对方在关系中占据主导，你更多迁就和退让"

        return {
            "pattern": pattern,
            "score": round(power_score, 1),
            "my_initiate": my_initiate,
            "other_initiate": other_initiate,
            "my_compromise": my_compromise,
            "other_compromise": other_compromise,
            "description": description,
            "advice": self._power_dynamic_advice(pattern),
        }

    def _power_dynamic_advice(self, pattern: str) -> str:
        """权力格局相处建议。"""
        advice_map = {
            "我方主导": "你主导没问题，但注意别变成控制欲，给对方留空间和选择权",
            "我方略占优": "保持当前节奏，偶尔让对方做决定，增加她的参与感",
            "平衡拉扯": "最健康的状态，保持有来有回，别让一方一直迁就",
            "对方略占优": "可以适当主动一些，表达自己的需求，别一直退让",
            "对方主导": "注意别失去自我，建立自己的框架和底线，过度迁就会失去吸引力",
            "unknown": "数据不足，多观察双方的互动模式",
        }
        return advice_map.get(pattern, advice_map["unknown"])

    def _analyze_emotional_bank(self, messages: list, other_msgs: list, my_msgs: list) -> dict:
        """情感账户分析。

        存款：正向表达（关心、赞美、陪伴、道歉、感恩）
        取款：负向行为（指责、冷战、忽视、敷衍、争吵）
        余额：存款 - 取款，反映关系健康度。
        """
        deposit_words = ["想你", "爱你", "谢谢你", "辛苦了", "对不起", "抱歉",
                        "关心你", "注意身体", "早点睡", "加油", "你真棒", "有你真好",
                        "抱抱", "陪你", "我在", "支持你"]
        withdrawal_words = ["随便", "都行", "无所谓", "你烦不烦", "别烦我", "不想说",
                          "算了", "就这样吧", "你能不能", "你总是", "你从来",
                          "我受够了", "分手", "离婚", "滚"]

        my_deposit = sum(1 for w in deposit_words if any(w in m.get("content", "") for m in my_msgs))
        other_deposit = sum(1 for w in deposit_words if any(w in m.get("content", "") for m in other_msgs))
        my_withdrawal = sum(1 for w in withdrawal_words if any(w in m.get("content", "") for m in my_msgs))
        other_withdrawal = sum(1 for w in withdrawal_words if any(w in m.get("content", "") for m in other_msgs))

        total_deposit = my_deposit + other_deposit
        total_withdrawal = my_withdrawal + other_withdrawal
        # v5 口径修复：balance 与展示的存款/取款明细一致（deposit - withdrawal），
        # 不再用 ×2 权重导致"23-16"却显示 -9 的账目矛盾；
        # 取款仍保留 1.5 权重体现"伤害比温暖更持久"的心理学设定，
        # 并在 note 中注明口径。
        balance = round(total_deposit - total_withdrawal * 1.5, 1)

        if balance > 20:
            health = "富裕"
            description = "情感账户充裕，关系健康稳定，有足够的抗风险能力"
        elif balance > 5:
            health = "健康"
            description = "情感账户正常，存款多于取款，关系整体良好"
        elif balance > -5:
            health = "透支边缘"
            description = "存款和取款接近，关系比较脆弱，需要多做正向积累"
        else:
            health = "透支"
            description = "情感账户严重透支，关系处于危险状态，急需修复和重建"

        return {
            "balance": balance,
            "health": health,
            "total_deposit": total_deposit,
            "total_withdrawal": total_withdrawal,
            "my_deposit": my_deposit,
            "other_deposit": other_deposit,
            "my_withdrawal": my_withdrawal,
            "other_withdrawal": other_withdrawal,
            "description": description,
            "advice": "多做存款行为（具体的关心和感恩），减少取款行为（指责和冷战），每次取款需要5次存款才能弥补" if balance < 5 else "保持当前的正向互动，继续积累情感资产",
        }

    def _analyze_conflict_style(self, other_text: str, my_text: str, messages: list) -> dict:
        """冲突处理模式分析。

        四种模式：建设性沟通、破坏性攻击、冷战逃避、情绪爆发。

        v3 修复：
          - 去掉裸词"能不能"作为建设性信号（会把 NPD 的"你能不能别像个侦探"
            误判成建设性）。改为："能不能"后紧跟 别/不要/不 时不计建设性，
            且"你能不能别"本就归入破坏性攻击；
          - 把防御/暗黑的强证据映射进现有类别（向后兼容，仍返回 4 类）：
            否定现实/煤气灯贬低/贬低感受/推卸责任 → 破坏性攻击；
            行吧/你说什么就是什么/看情况吧/跟你说不清楚/有意思吗 → 冷战逃避。
          保证 NPD 最终 primary 必为 冷战逃避 或 破坏性攻击，绝不落入建设性沟通。
        """
        styles = {
            "constructive": {
                "name": "建设性沟通",
                "signals": ["我们聊聊", "我理解你", "我的感受是", "你觉得呢",
                           "一起解决", "我也有不对", "我们可以"],
                "score": 0,
            },
            "destructive": {
                "name": "破坏性攻击",
                "signals": ["你总是", "你从来", "你就是", "都怪你", "你应该",
                           "你能不能别", "你这种人", "我就知道你",
                           # NPD/指责型：否定现实、煤气灯、贬低感受、推卸
                           "我明明说了", "你自己不记得", "我没说过", "你记错了",
                           "我怎么不记得", "这有什么好生气的", "你太敏感了",
                           "你想多了", "是你逼我的", "要不是你", "都是你的错",
                           "你这叫控制欲", "有意思吗"],
                "score": 0,
            },
            "cold_war": {
                "name": "冷战逃避",
                "signals": ["不想说", "算了", "随便", "都行", "无所谓",
                           "没什么", "你想怎样就怎样", "我累了", "沉默",
                           # NPD 冷处理/打太极标志性表达
                           "行吧", "你说什么就是什么", "看情况吧",
                           "跟你说不清楚", "随你怎么说", "你爱怎么想怎么想",
                           "你忙你的", "不是什么大事"],
                "score": 0,
            },
            "emotional_outburst": {
                "name": "情绪爆发",
                "signals": ["我受够了", "分手", "离婚", "滚", "你烦不烦",
                           "别逼我", "我要疯了", "气死我了", "你再说一遍"],
                "score": 0,
            },
        }

        def _count_constructive_neng(text: str) -> int:
            """统计真正建设性的"能不能"：其后不接 别/不要/不 才计建设性。"""
            if not text:
                return 0
            n = 0
            idx = 0
            while True:
                idx = text.find("能不能", idx)
                if idx < 0:
                    break
                tail = text[idx + 3: idx + 5]
                if not tail.startswith(("别", "不")):
                    n += 1
                idx += 3
            return n

        # v4 修复：刻画"对方"冲突风格只依据 other_text；
        # my_text（我方气话）不得计入用于选 primary 的各 style 分数，
        # 否则我方的"你就是..."会被误算成对方在人身攻击。
        for key, style in styles.items():
            for signal in style["signals"]:
                if signal in other_text:
                    style["score"] += other_text.count(signal) * 1.5  # 对方权重高
            # 收紧"能不能"：只把对方真正建设性用法计入 constructive
            if key == "constructive":
                style["score"] += _count_constructive_neng(other_text) * 1.5

        sorted_styles = sorted(styles.items(), key=lambda x: x[1]["score"], reverse=True)
        primary = sorted_styles[0][0] if sorted_styles[0][1]["score"] > 0 else "unknown"

        advice_map = {
            "constructive": "冲突处理方式健康，继续保持对事不对人的沟通方式",
            "destructive": "冲突时容易人身攻击，学会用'我感受'代替'你总是'，对事不对人",
            "cold_war": "习惯用冷战处理冲突，这比吵架更伤关系，学会表达感受而不是沉默",
            "emotional_outburst": "冲突时容易情绪爆发，先冷静再沟通，情绪上头时别做决定",
            "unknown": "冲突样本不足，多观察双方吵架时的应对方式",
        }

        return {
            "primary": styles[primary]["name"] if primary != "unknown" else "待观察",
            "primary_key": primary,
            "scores": {k: v["score"] for k, v in styles.items()},
            "advice": advice_map.get(primary, advice_map["unknown"]),
        }
        sorted_styles = sorted(styles.items(), key=lambda x: x[1]["score"], reverse=True)
        primary = sorted_styles[0][0] if sorted_styles[0][1]["score"] > 0 else "unknown"

        advice_map = {
            "constructive": "冲突处理方式健康，继续保持对事不对人的沟通方式",
            "destructive": "冲突时容易人身攻击，学会用'我感受'代替'你总是'，对事不对人",
            "cold_war": "习惯用冷战处理冲突，这比吵架更伤关系，学会表达感受而不是沉默",
            "emotional_outburst": "冲突时容易情绪爆发，先冷静再沟通，情绪上头时别做决定",
            "unknown": "冲突样本不足，多观察双方吵架时的应对方式",
        }

        return {
            "primary": styles[primary]["name"] if primary != "unknown" else "待观察",
            "primary_key": primary,
            "scores": {k: v["score"] for k, v in styles.items()},
            "advice": advice_map.get(primary, advice_map["unknown"]),
        }

    def _analyze_boundaries(self, other_text: str, my_text: str, messages: list) -> dict:
        """边界感分析。

        个人边界：是否有自己的空间和原则
        异性边界：与异性交往的边界清晰度
        """
        # 个人边界信号
        weak_boundary_signals = ["你说了算", "都行", "随便", "我没关系", "你开心就好",
                                "我都可以", "你决定吧", "我听你的"]
        strong_boundary_signals = ["我不想", "我需要", "这是我的底线", "我不同意",
                                  "我需要空间", "别这样", "我不喜欢", "这不行"]

        # 异性边界信号
        opposite_sex_signals = ["那个女的", "那个男的", "她找我", "他找我", "异性朋友",
                               "闺蜜", "兄弟", "前任", "前女友", "前男友"]
        jealousy_signals = ["吃醋", "你跟她", "你跟他", "你是不是喜欢", "你跟谁在一起"]

        weak_score = sum(1 for w in weak_boundary_signals if w in other_text or w in my_text)
        strong_score = sum(1 for w in strong_boundary_signals if w in other_text or w in my_text)
        opposite_score = sum(1 for w in opposite_sex_signals if w in other_text or w in my_text)
        jealousy_score = sum(1 for w in jealousy_signals if w in other_text or w in my_text)

        # 个人边界判断
        if strong_score > weak_score + 2:
            personal_boundary = "清晰"
            pb_desc = "有明确的个人边界和原则，敢于表达需求和拒绝"
        elif weak_score > strong_score + 2:
            personal_boundary = "模糊"
            pb_desc = "个人边界较模糊，容易过度迁就和牺牲自我"
        else:
            personal_boundary = "中等"
            pb_desc = "个人边界一般，有时会妥协，有时会坚持"

        # 异性边界判断
        if opposite_score > 5 and jealousy_score > 2:
            opposite_boundary = "需关注"
            ob_desc = "异性交往较多且有吃醋信号，需要明确异性边界和沟通"
        elif opposite_score > 3:
            opposite_boundary = "正常社交"
            ob_desc = "有正常异性社交，边界基本清晰"
        else:
            opposite_boundary = "社交简单"
            ob_desc = "异性社交较少，关系相对简单"

        return {
            "personal_boundary": personal_boundary,
            "personal_desc": pb_desc,
            "opposite_boundary": opposite_boundary,
            "opposite_desc": ob_desc,
            "weak_score": weak_score,
            "strong_score": strong_score,
            "opposite_score": opposite_score,
            "jealousy_score": jealousy_score,
            "advice": "建立清晰的个人边界，敢于说不；异性交往保持透明，减少不必要的误会" if personal_boundary == "模糊" else "保持当前的边界感，同时注意异性交往的透明度",
        }

    def _analyze_freshness(self, messages: list, other_text: str, relationship_stage: dict) -> dict:
        """新鲜感评估。

        维度：话题多样性、互动频率变化、新体验信号、仪式感。
        """
        if not messages:
            return {"level": "unknown", "score": 50, "description": "数据不足"}

        # 话题多样性（不同关键词数量）
        all_text = other_text + " " + " ".join(m.get("content", "") for m in messages if m["direction"] == "sent")
        words = set(all_text.replace("，", " ").replace("。", " ").split())
        topic_diversity = min(len(words) / 50, 100)  # 归一化

        # 新体验信号
        new_experience_words = ["第一次", "新的", "试试", "去没去过的", "挑战", "学",
                               "旅行", "探店", "新电影", "新餐厅", "不一样的"]
        new_experience_score = sum(1 for w in new_experience_words if w in all_text) * 10

        # 仪式感信号
        ritual_words = ["纪念日", "生日", "情人节", "周年", "礼物", "惊喜", "准备了",
                       "特意", "用心", "仪式感"]
        ritual_score = sum(1 for w in ritual_words if w in all_text) * 8

        # 关系阶段影响（长期关系新鲜感自然下降）
        stage_penalty = 0
        stage = relationship_stage.get("stage", "")
        if "七年" in stage or "平淡" in stage:
            stage_penalty = 20
        elif "结婚" in stage or "老夫老妻" in stage:
            stage_penalty = 10

        freshness_score = min(100, max(0, topic_diversity * 0.4 + new_experience_score + ritual_score - stage_penalty))

        if freshness_score > 70:
            level = "新鲜"
            description = "关系充满新鲜感，有共同探索和仪式感"
        elif freshness_score > 40:
            level = "平稳"
            description = "新鲜感一般，日常互动为主，可以增加一些新体验"
        else:
            level = "倦怠"
            description = "新鲜感不足，关系进入倦怠期，需要主动创造新体验和仪式感"

        return {
            "level": level,
            "score": round(freshness_score, 1),
            "topic_diversity": round(topic_diversity, 1),
            "new_experience_score": new_experience_score,
            "ritual_score": ritual_score,
            "description": description,
            "advice": "定期安排新体验（没去过的地方、没做过的事），保持仪式感，给关系注入新鲜感" if freshness_score < 50 else "保持当前的新鲜感，继续探索新事物",
        }

    def _analyze_risk_warning(self, other_text: str, my_text: str, messages: list, psychology: dict) -> dict:
        """风险预警分析。

        识别：出轨风险、分手风险、极端行为风险、PUA/精神控制风险。
        """
        risks = []

        # 出轨风险信号
        cheating_signals = ["那个女的", "那个男的", "她找我", "他找我", "加班", "忙",
                           "不方便", "别打电话", "别发消息", "同事", "客户", "应酬"]
        cheating_score = sum(1 for w in cheating_signals if w in other_text)
        if cheating_score > 8:
            risks.append({
                "type": "出轨风险",
                "level": "中",
                "signals": f"检测到{cheating_score}次可疑信号（频繁提及异性/不方便/加班应酬）",
                "advice": "注意观察对方行踪和态度变化，但不要无端猜忌，用沟通代替监控",
            })

        # 分手风险信号
        breakup_signals = ["分手", "离婚", "算了吧", "我们不合适", "没感觉了", "累了",
                          "别联系了", "拉黑", "删除", "各过各的", "没什么好说的"]
        breakup_score = sum(1 for w in breakup_signals if w in other_text)
        if breakup_score > 3:
            risks.append({
                "type": "分手风险",
                "level": "高" if breakup_score > 6 else "中",
                "signals": f"检测到{breakup_score}次分手相关表达",
                "advice": "关系处于危险状态，需要认真沟通核心问题，必要时寻求专业帮助",
            })

        # 极端行为风险（自伤/自杀威胁）
        # 覆盖BPD等情绪障碍的典型自伤表达，同时统计出现次数而非仅出现与否
        extreme_signals = [
            "不想活了", "活着没意思", "死了算了", "伤害自己", "自残",
            "同归于尽", "你别后悔", "我不会放过你", "威胁",
            # BPD/情绪障碍特有自伤表达
            "割腕", "割手", "我去死", "我去死了", "自杀", "我割",
            "活着没意义", "活着好累", "没意义", "我不想活",
            "我伤害自己", "我自残了", "我割腕了", "我死了",
        ]
        # 方向分离：received=对方发出（用于升级对方风险），sent=我方发出（只留痕不升级对方）
        extreme_recv = 0
        extreme_sent = 0
        # 自伤强词：命中即按最严档；普通"威胁"等宽泛词不算自伤强词
        _self_harm_strong = {
            "不想活了", "活着没意思", "死了算了", "自残", "割腕", "割手",
            "我去死", "我去死了", "自杀", "我不想活", "我割腕了", "我死了",
            "我自残了", "伤害自己", "我伤害自己", "我割", "活着没意义",
        }
        for w in extreme_signals:
            extreme_recv += other_text.count(w)
            extreme_sent += my_text.count(w)
        extreme_score = extreme_recv + extreme_sent
        # 分级（只按对方 received 升级）：1次=中/观察；≥2次=高/关注；
        # ≥3次或命中自伤强词=紧急求助。我方 sent 单独记录，不升级对方风险。
        _recv_has_strong = any(w in other_text for w in _self_harm_strong)
        if extreme_recv >= 3 or (extreme_recv >= 1 and _recv_has_strong):
            _ext_level = "高"
            _ext_sig = (f"对方发出{extreme_recv}次极端/自伤表达"
                        f"（{'含自伤强词，' if _recv_has_strong else ''}达紧急求助档）")
            _ext_adv = ("严重安全信号：立即陪伴并确认对方安全，"
                        "联系专业心理援助或紧急求助")
        elif extreme_recv >= 2:
            _ext_level = "高"
            _ext_sig = f"对方发出{extreme_recv}次极端表达，需重点关注"
            _ext_adv = "认真关注对方心理状态，必要时寻求专业沟通与心理援助"
        elif extreme_recv == 1:
            _ext_level = "中"
            _ext_sig = "对方有1次极端表达，建议观察语境与后续情绪走向"
            _ext_adv = "留意对方情绪变化，在其主动流露时给予承接，暂不升级为紧急处置"
        else:
            _ext_level = None
        if _ext_level is not None:
            risks.append({
                "type": "极端行为风险",
                "level": _ext_level,
                "signals": _ext_sig,
                "advice": _ext_adv,
            })
        elif extreme_sent > 0:
            # 对方为0、仅我方有极端措辞：不升级对方风险，仅留痕提示我方表达
            risks.append({
                "type": "极端行为风险",
                "level": "低",
                "signals": (f"极端信号仅出现在我方表达{extreme_sent}次、对方0次；"
                            f"不构成对方风险升级"),
                "advice": "注意我方情绪表达方式，避免自我伤害式措辞；对方层面无需紧急处置",
            })

        # PUA/精神控制风险（结合心理特征分析）
        # psychology 实际结构：dark_triad_tendencies={自恋,操纵,冲动无情}
        #                    defensive_communication={贬低感受,否定现实,推卸责任,道德绑架}
        dark_triad = psychology.get("dark_triad_tendencies", {}) if isinstance(psychology, dict) else {}
        defensive = psychology.get("defensive_communication", {}) if isinstance(psychology, dict) else {}
        pua_score = 0
        if isinstance(dark_triad, dict):
            # 兼容中英文 key
            narc = dark_triad.get("自恋", dark_triad.get("narcissism", 0))
            manip = dark_triad.get("操纵", dark_triad.get("manipulation", 0))
            pua_score += narc + manip
        if isinstance(defensive, dict):
            # 煤气灯 = 贬低感受 + 否定现实；推卸责任 = blame shifting
            gas = defensive.get("贬低感受", 0) + defensive.get("否定现实", 0)
            blame = defensive.get("推卸责任", defensive.get("blame_shifting", 0))
            pua_score += gas + blame
        if pua_score > 4:
            risks.append({
                "type": "精神控制风险",
                "level": "中" if pua_score < 7 else "高",
                "signals": f"检测到自恋/操纵/推卸责任等信号（得分{pua_score}）",
                "advice": "注意识别情感操纵和煤气灯效应，保持独立判断，不要被PUA",
            })

        if not risks:
            risks.append({
                "type": "无明显风险",
                "level": "低",
                "signals": "未检测到明显风险信号",
                "advice": "继续保持健康的沟通和互动",
            })

        return {
            "risks": risks,
            "overall_level": max((r["level"] for r in risks), default="低"),
            "cheating_score": cheating_score,
            "breakup_score": breakup_score,
            "extreme_score": extreme_score,
            "extreme_score_received": extreme_recv,
            "extreme_score_sent": extreme_sent,
            "pua_score": pua_score,
        }

    # ==================== 可量化学术维度（4个） ====================

    def _analyze_relationship_satisfaction(self, messages, other_msgs, my_msgs,
                                            investment, emotional_bank) -> dict:
        """关系满意度分析（Rusbult投资模型量化）。

        满意度 = 回报 - 成本 + 情感账户余额 + 投入质量
        量化为0-100分。
        """
        if not messages:
            return {"score": 50, "level": "中等", "components": {}}

        # 回报：正向互动（关心、赞美、陪伴、感恩）
        reward_words = ["想你", "爱你", "谢谢你", "辛苦了", "抱抱", "陪你",
                       "我在", "支持你", "你真棒", "有你真好", "开心", "幸福"]
        reward_count = sum(1 for w in reward_words
                          if any(w in m.get("content", "") for m in other_msgs)
                          or any(w in m.get("content", "") for m in my_msgs))

        # 成本：负向互动（指责、冷战、忽视、争吵）
        cost_words = ["随便", "都行", "无所谓", "你烦不烦", "别烦我", "不想说",
                     "算了", "就这样吧", "你总是", "你从来", "我受够了", "滚"]
        cost_count = sum(1 for w in cost_words
                        if any(w in m.get("content", "") for m in other_msgs)
                        or any(w in m.get("content", "") for m in my_msgs))

        # 投入质量（来自investment分析）
        investment_level = investment.get("level", "中等投入")
        investment_score_map = {"高投入": 20, "中高投入": 15, "中等投入": 10, "低投入": 5}
        investment_score = investment_score_map.get(investment_level, 10)

        # 情感账户余额（来自emotional_bank分析）
        bank_balance = emotional_bank.get("balance", 0) if isinstance(emotional_bank, dict) else 0
        bank_score = max(-20, min(20, bank_balance))

        # 消息量归一化（避免消息多就分高）
        total = len(messages) or 1
        reward_norm = min(30, reward_count / total * 100)
        cost_norm = min(30, cost_count / total * 100)

        # 综合满意度
        satisfaction = 50 + reward_norm - cost_norm + investment_score * 0.5 + bank_score * 0.3
        satisfaction = max(0, min(100, round(satisfaction, 1)))

        if satisfaction >= 75:
            level = "高满意度"
            desc = "关系满意度高，双方互动积极，情感账户充裕"
        elif satisfaction >= 55:
            level = "中等满意度"
            desc = "关系满意度中等，有积极互动也有摩擦，整体稳定"
        elif satisfaction >= 35:
            level = "低满意度"
            desc = "关系满意度偏低，负向互动较多，需要主动修复"
        else:
            level = "极低满意度"
            desc = "关系满意度极低，处于危机状态，需要认真评估关系走向"

        return {
            "score": satisfaction,
            "level": level,
            "description": desc,
            "components": {
                "reward_score": round(reward_norm, 1),
                "cost_score": round(cost_norm, 1),
                "investment_score": investment_score,
                "bank_score": bank_score,
            },
            "advice": "保持积极互动，减少指责和冷战，定期表达感恩和欣赏" if satisfaction < 60 else "继续保持高质量互动，注意维护情感账户",
        }

    def _analyze_commitment(self, messages, other_msgs, my_msgs,
                            investment, relationship_satisfaction) -> dict:
        """关系承诺度分析（Rusbult投资模型）。

        承诺度 = 满意度 + 投资规模 - 替代质量
        替代质量通过"是否提及其他异性/分手念头"估算。
        """
        if not messages:
            return {"score": 50, "level": "中等", "components": {}}

        # 投资规模（来自investment分析）
        investment_level = investment.get("level", "中等投入")
        investment_score_map = {"高投入": 30, "中高投入": 22, "中等投入": 15, "低投入": 8}
        investment_score = investment_score_map.get(investment_level, 15)

        # 满意度（来自relationship_satisfaction）
        satisfaction_score = relationship_satisfaction.get("score", 50) if isinstance(relationship_satisfaction, dict) else 50

        # 替代质量：提及其他异性/分手念头/比较 = 替代质量高 = 承诺度低
        alternative_words = ["那个女的", "那个男的", "她找我", "他找我", "前任",
                            "前女友", "前男友", "别人都", "要是我跟别人", "分手",
                            "离婚", "算了吧", "不合适", "重新找"]
        alternative_count = sum(1 for w in alternative_words
                               if any(w in m.get("content", "") for m in other_msgs)
                               or any(w in m.get("content", "") for m in my_msgs))
        alternative_penalty = min(25, alternative_count * 3)

        # 承诺信号：未来规划、共同目标、承诺表达
        commitment_words = ["以后", "将来", "我们的", "一辈子", "永远", "结婚",
                           "领证", "孩子", "家", "一起", "到老", "余生"]
        commitment_count = sum(1 for w in commitment_words
                              if any(w in m.get("content", "") for m in other_msgs)
                              or any(w in m.get("content", "") for m in my_msgs))
        commitment_bonus = min(20, commitment_count * 2)

        # 综合承诺度
        commitment = satisfaction_score * 0.4 + investment_score + commitment_bonus - alternative_penalty
        commitment = max(0, min(100, round(commitment, 1)))

        if commitment >= 75:
            level = "高承诺"
            desc = "双方对关系承诺度高，有共同未来规划，投资规模大"
        elif commitment >= 55:
            level = "中等承诺"
            desc = "承诺度中等，有一定投资和规划，但仍有不确定性"
        elif commitment >= 35:
            level = "低承诺"
            desc = "承诺度偏低，投资不足或存在替代选择，关系稳定性存疑"
        else:
            level = "极低承诺"
            desc = "承诺度极低，关系随时可能结束，需要认真沟通未来"

        return {
            "score": commitment,
            "level": level,
            "description": desc,
            "components": {
                "satisfaction_contribution": round(satisfaction_score * 0.4, 1),
                "investment_score": investment_score,
                "commitment_bonus": commitment_bonus,
                "alternative_penalty": alternative_penalty,
            },
            "advice": "增加共同规划和未来讨论，减少与其他异性的暧昧比较" if commitment < 60 else "保持对未来的共同规划，继续投入关系建设",
        }

    def _analyze_interaction_ratio(self, messages, other_msgs, my_msgs) -> dict:
        """积极/消极互动比分析（Gottman 5:1比率）。

        Gottman研究：稳定婚姻的积极/消极互动比约为5:1；
        离婚预测的比率约为0.8:1。
        """
        if not messages:
            return {"ratio": 1.0, "level": "未知", "positive_count": 0, "negative_count": 0}

        # 积极互动信号
        positive_words = ["想你", "爱你", "谢谢", "辛苦", "抱抱", "亲亲", "开心",
                         "哈哈", "好呀", "可以", "支持", "理解", "没关系", "我在",
                         "有你真好", "你真棒", "喜欢", "幸福", "期待", "约"]
        # 消极互动信号
        negative_words = ["随便", "都行", "无所谓", "烦", "不想说", "算了",
                         "就这样", "你总是", "你从来", "受够了", "滚", "分手",
                         "离婚", "别烦我", "你能不能", "我累了", "无语", "呵呵"]

        positive_count = 0
        negative_count = 0

        for m in messages:
            content = m.get("content", "") or ""
            for w in positive_words:
                if w in content:
                    positive_count += 1
                    break  # 每条消息只计一次
            for w in negative_words:
                if w in content:
                    negative_count += 1
                    break

        # 计算比率
        if negative_count == 0:
            ratio = positive_count if positive_count > 0 else 1.0
        else:
            ratio = round(positive_count / negative_count, 2)

        # Gottman标准判断
        if ratio >= 5:
            level = "健康（≥5:1）"
            desc = "积极互动远多于消极，符合Gottman健康关系标准，关系稳定"
        elif ratio >= 3:
            level = "良好（3-5:1）"
            desc = "积极互动明显多于消极，关系整体健康，注意维持"
        elif ratio >= 1:
            level = "一般（1-3:1）"
            desc = "积极与消极互动接近，关系有摩擦，需要增加积极互动"
        elif ratio >= 0.8:
            level = "警戒（0.8-1:1）"
            desc = "消极互动接近积极，接近Gottman离婚预测阈值，需要主动修复"
        else:
            level = "危险（<0.8:1）"
            desc = "消极互动多于积极，低于Gottman离婚预测阈值，关系处于高风险"

        return {
            "ratio": ratio,
            "level": level,
            "description": desc,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "gottman_standard": "健康关系≥5:1，离婚预测≈0.8:1",
            "advice": "有意识地增加积极互动（赞美、感恩、陪伴），减少消极互动（指责、冷战）" if ratio < 3 else "保持当前的积极互动比例，继续维护健康关系",
        }

    # ==================== Gottman 体系三维（v3.0 第三轮新增） ====================

    def _analyze_emotional_bid_response(self, messages: list, other_msgs: list, my_msgs: list) -> dict:
        """情感投标响应率（Gottman bids for connection）。

        对方发出连接请求（分享/提问/求助/表达感受）时，用户'转向'(turn toward)/
        '转向别处'(turn away)/'反对'(against) 的比例。
        幸福夫妻转向率约86%，离婚夫妻约33%。
        """
        if not messages:
            return {"rate": 0.0, "turn_toward_count": 0, "turn_away_count": 0,
                    "against_count": 0, "sample_size": 0, "health": "数据不足",
                    "gottman_reference": "幸福夫妻转向率≈86%，离婚夫妻≈33%"}

        # 对方的"情感投标"信号：开启话题/分享感受/提问/求助
        bid_openers = ["？", "?", "你看", "你猜", "跟你说", "我今天", "我刚才", "我发现",
                       "听说", "怎么办", "为什么", "你觉得", "我好", "我有点", "压力",
                       "我累", "开心", "难过", "生气", "我想吃", "我想去", "给你看",
                       "你在干嘛", "你知道吗", "最近", "烦恼", "委屈"]
        # 我的"转向别处"：敷衍/忽略
        away_words = ["嗯", "哦", "呵呵", "好吧", "在忙", "忙呢", "等下", "晚点再说",
                      "哦那", "随便", "哦行", "嗯哼"]
        # 我的"反对"：否定/批评/拒绝
        against_words = ["这有什么", "你不对", "别烦我", "你总是", "你从来", "至于吗",
                         "我都说了", "你能不能", "滚", "我不想听", "关我什么事"]
        # 我的"转向"：展开话题/表达关心
        toward_words = ["怎么了", "为什么呀", "然后呢", "真的吗", "哈哈", "我也是",
                        "我懂", "我理解", "辛苦", "抱抱", "支持你", "那你", "你呢",
                        "别难过", "开心点", "你说", "我听着", "怎么啦", "多喝点",
                        "我陪你", "为什么"]

        turn_toward = turn_away = against = 0
        for i, m in enumerate(messages):
            if m.get("direction") != "received":
                continue
            c = m.get("content", "") or ""
            if not any(k in c for k in bid_openers):
                continue
            # 找这条投标之后我的第一条回复
            resp = None
            for j in range(i + 1, len(messages)):
                if messages[j].get("direction") == "received":
                    break  # 对方又连发：我的上一条投标被搁置
                if messages[j].get("direction") == "sent":
                    resp = messages[j].get("content", "") or ""
                    break
            if resp is None:
                turn_away += 1  # 未回应
                continue
            if any(w in resp for w in against_words):
                against += 1
            elif any(w in resp for w in toward_words):
                turn_toward += 1
            else:
                turn_away += 1  # 敷衍或中性未展开

        total = turn_toward + turn_away + against
        rate = round(turn_toward * 100 / total, 1) if total > 0 else 0.0
        if total < 3:
            health = "数据不足"
        elif rate >= 80:
            health = "健康"
        elif rate >= 50:
            health = "需注意"
        else:
            health = "危险"

        return {
            "rate": rate,
            "turn_toward_count": turn_toward,
            "turn_away_count": turn_away,
            "against_count": against,
            "sample_size": total,
            "health": health,
            "gottman_reference": "幸福夫妻转向率≈86%，离婚夫妻≈33%",
            "advice": "多转向对方的分享：放下事、追问细节、表达关心，而不是敷衍或忽略" if rate < 80 else "保持良好的情感投标响应",
        }

    def _analyze_love_map(self, messages: list, other_text: str, my_text: str) -> dict:
        """爱情地图深度（Gottman Love Maps）：用户对对方内心世界的了解程度。

        统计用户在聊天中展现出对对方 7 个维度的了解：
        情绪状态/兴趣爱好/恐惧担忧/希望梦想/日常习惯/社交圈/压力源。
        """
        if not messages:
            return {"score": 0, "known_dimensions": [], "unknown_dimensions": [],
                    "level": "数据不足"}

        dims_map = {
            "情绪状态": ["你心情不好", "你累了", "你今天", "你是不是生气", "你看起来", "你情绪", "你不开心"],
            "兴趣爱好": ["你喜欢", "你爱听", "你爱看", "你追的", "你想去", "你想吃", "你的爱好"],
            "恐惧担忧": ["你怕", "你担心", "你害怕", "你焦虑", "你紧张"],
            "希望梦想": ["你想", "你的目标", "你以后", "你的梦想", "你希望", "你打算"],
            "日常习惯": ["你每天", "你习惯", "你早上", "你下班", "你平时", "你周末"],
            "社交圈": ["你朋友", "你同事", "你闺蜜", "你兄弟", "你妈", "你家人", "你室友"],
            "压力源": ["你最近压力", "你工作", "你老板", "你项目", "你太忙", "你考试"],
        }
        known = [d for d, kws in dims_map.items() if any(k in my_text for k in kws)]
        unknown = [d for d in dims_map if d not in known]
        score = round(len(known) / len(dims_map) * 100)
        if score >= 70:
            level = "详细"
        elif score >= 30:
            level = "中等"
        else:
            level = "薄弱"

        return {
            "score": score,
            "known_dimensions": known,
            "unknown_dimensions": unknown,
            "level": level,
            "last_updated": "基于历史聊天记录中用户对对方内心世界的提及统计",
            "advice": "主动询问并记住对方最近的压力、期待和喜好，爱情地图要持续更新" if score < 70 else "对对方内心世界了解充分，继续定期更新",
        }

    def _analyze_positive_negative_ratio(self, messages: list) -> dict:
        """积极/消极互动比（Gottman 5:1 比率，任务指定字段版）。

        从聊天记录用关键词统计积极互动与消极互动消息数，计算比率。
        """
        if not messages:
            return {"ratio": 1.0, "positive_count": 0, "negative_count": 0,
                    "gottman_healthy": False, "assessment": "数据不足"}

        pos_words = ["谢谢", "辛苦", "抱抱", "开心", "哈哈", "好呀", "支持", "理解",
                     "我在", "有你真好", "你真棒", "喜欢", "幸福", "期待", "想你",
                     "爱你", "早安", "晚安", "么么", "比心"]
        neg_words = ["随便", "都行", "无所谓", "烦", "不想说", "算了", "你总是",
                     "你从来", "受够了", "滚", "分手", "别烦我", "你能不能", "我累了",
                     "无语", "呵呵", "你怎么又", "滚蛋"]

        pc = nc = 0
        for m in messages:
            c = m.get("content", "") or ""
            if any(w in c for w in pos_words):
                pc += 1
            elif any(w in c for w in neg_words):
                nc += 1

        if nc == 0:
            ratio = float(pc) if pc > 0 else 1.0
        else:
            ratio = round(pc / nc, 2)

        gottman_healthy = ratio >= 5.0 and (nc > 0 or pc >= 5)
        if ratio >= 5:
            assessment = "健康"
        elif ratio >= 3:
            assessment = "需注意"
        else:
            assessment = "危险"

        return {
            "ratio": ratio,
            "positive_count": pc,
            "negative_count": nc,
            "gottman_healthy": bool(gottman_healthy),
            "assessment": assessment,
            "gottman_standard": "冲突时≥5:1健康，日常≥20:1；低于5:1是离婚预测信号",
        }

    # 默认冲突/修复词表（类级，调用时可注入覆盖）——P1 口径可辩护化
    _DEFAULT_CONFLICT_WORDS = [
        "你总是", "你从来", "你能不能", "我受够了", "随便",
        "不想说", "算了", "就这样吧", "别烦我", "你烦不烦",
        "分手", "离婚", "滚", "无语", "呵呵", "凭什么",
    ]
    _DEFAULT_REPAIR_WORDS = [
        "对不起", "抱歉", "我错了", "别生气了", "好了好了",
        "抱抱", "亲亲", "不闹了", "和好", "原谅我", "是我不好",
        "我们聊聊", "我理解你", "别这样", "消消气", "我爱你",
    ]

    def _analyze_conflict_repair(self, messages, other_text, my_text,
                                 repair_window_days: float = 3.0,
                                 conflict_words=None,
                                 repair_words=None) -> dict:
        """冲突频率与修复成功率分析（Gottman 修复尝试）。

        口径（P1 D项，可辩护）：
          - 冲突事件：命中 conflict_words 的任一方向消息，开启一个冲突窗口；
            窗口未关闭期间的后续冲突消息不计新冲突（合并为同一事件，避免刷屏重复计数）。
          - 修复：该冲突窗口开启后 repair_window_days 天（默认 3 天）内出现命中
            repair_words 的主动和解信号，记一次成功修复并关闭窗口。
          - 窗口超时（>repair_window_days 天）无修复信号，记一次未修复并关闭窗口；
            窗口内的普通对话不视为修复、也不提前关闭窗口（取代旧版"6h 弱修复"误判）。
          - 修复率 = 成功修复 / 冲突事件数 × 100%。

        Args:
            repair_window_days: 修复窗口天数，默认 3.0（可配置）。
            conflict_words: 冲突词表覆盖；None 用内置默认。
            repair_words: 修复词表覆盖；None 用内置默认。
        """
        if not messages:
            return {"conflict_count": 0, "repair_count": 0, "repair_rate": 0,
                    "repair_window_days": float(repair_window_days),
                    "level": "未知", "description": "数据不足"}

        conflict_words = conflict_words or self._DEFAULT_CONFLICT_WORDS
        repair_words = repair_words or self._DEFAULT_REPAIR_WORDS

        from datetime import datetime, timedelta
        window = timedelta(days=float(repair_window_days))

        conflict_count = 0
        repair_count = 0
        in_conflict = False
        conflict_time = None

        for m in messages:
            content = m.get("content", "") or ""
            created_at = m.get("created_at", "")
            try:
                msg_time = datetime.fromisoformat(created_at) if created_at else None
            except Exception:
                msg_time = None

            # 窗口超时：关闭未修复冲突（普通对话不提前关闭，等修复信号或超时）
            if in_conflict and conflict_time is not None and msg_time is not None \
                    and (msg_time - conflict_time) > window:
                in_conflict = False
                conflict_time = None

            is_conflict = any(w in content for w in conflict_words)
            is_repair = any(w in content for w in repair_words)

            if is_conflict and not in_conflict:
                conflict_count += 1
                in_conflict = True
                conflict_time = msg_time
            elif in_conflict and is_repair:
                # 有时间戳时校验窗口；无时间戳时保守记为修复（与旧版一致）
                if conflict_time is None or msg_time is None \
                        or (msg_time - conflict_time) <= window:
                    repair_count += 1
                in_conflict = False
                conflict_time = None
            # 普通对话：既不冲突也不修复，保持窗口等待修复信号/超时

        # 计算修复率
        repair_rate = round(repair_count / conflict_count * 100, 1) if conflict_count > 0 else 0

        if conflict_count == 0:
            level = "无冲突"
            desc = "未检测到明显冲突，关系和谐"
        elif repair_rate >= 80:
            level = "修复能力强"
            desc = f"发生{conflict_count}次冲突，成功修复{repair_count}次（{repair_rate}%），修复能力强"
        elif repair_rate >= 50:
            level = "修复能力中等"
            desc = f"发生{conflict_count}次冲突，修复{repair_count}次（{repair_rate}%），修复能力一般"
        else:
            level = "修复能力弱"
            desc = f"发生{conflict_count}次冲突，仅修复{repair_count}次（{repair_rate}%），冲突易积累"

        return {
            "conflict_count": conflict_count,
            "repair_count": repair_count,
            "repair_rate": repair_rate,
            "repair_window_days": float(repair_window_days),
            "level": level,
            "description": desc,
            "gottman_note": "Gottman研究：修复尝试成功率是婚姻稳定性的关键预测因子",
            "advice": "学会在冲突中主动发起修复尝试（道歉、幽默、转移话题），不要让冲突过夜" if repair_rate < 60 else "保持良好的冲突修复能力，冲突后及时沟通和解",
        }

    # ==================== 新增4个科学维度（21维） ====================

    def _analyze_passion(self, messages, other_text, my_text, relationship_stage) -> dict:
        """激情维度分析（Sternberg爱情三元论之一）。

        激情：性吸引力、浪漫冲动、情感强度、心跳加速的感觉。
        与亲密（情感联结）和承诺（长期意愿）构成完整爱情。
        激情随关系阶段自然衰减，需要主动维护。
        """
        if not messages:
            return {"score": 0, "level": "未知", "description": "数据不足",
                    "signals": [], "advice": "需要更多聊天数据"}

        # 激情信号词
        passion_words = [
            "想你", "好想你", "梦见你", "心动", "心跳", "紧张", "害羞",
            "抱抱", "亲亲", "吻", "摸", "抱", "依偎", "靠", "牵",
            "想见你", "迫不及待", "一刻都不想等", "忍不住",
            "喜欢", "爱", "着迷", "吸引", "魅力", "好看", "帅", "美",
            "约会", "见面", "陪", "一起", "二人世界", "独处"
        ]

        # 激情衰减信号
        decline_words = [
            "老夫老妻", "都一样", "没感觉", "平淡", "习惯了", "左手摸右手",
            "忙", "累", "没时间", "改天吧", "下次", "再说", "随便"
        ]

        passion_count = sum(1 for w in passion_words if w in other_text or w in my_text)
        decline_count = sum(1 for w in decline_words if w in other_text or w in my_text)

        # 基础分：激情信号数
        base_score = min(passion_count * 3, 60)
        # 衰减扣分
        base_score -= decline_count * 5
        # 关系阶段调整：暧昧期/热恋期激情自然高，稳定期需要主动维护
        stage = relationship_stage.get("stage", "")
        if stage in ["暧昧期", "热恋期"]:
            base_score += 15
        elif stage in ["稳定期", "婚姻期"]:
            base_score += 0  # 稳定期激情自然下降，不额外加分
        elif "七年" in stage or "倦怠" in stage:
            base_score -= 10

        score = max(0, min(100, base_score))

        if score >= 70:
            level = "激情高涨"
            desc = "双方情感强度高，浪漫和吸引力充足"
        elif score >= 40:
            level = "激情中等"
            desc = "有一定浪漫和吸引力，但可以更主动维护"
        else:
            level = "激情偏低"
            desc = "浪漫和吸引力不足，关系可能进入平淡期"

        signals = []
        if passion_count > 5:
            signals.append("频繁表达思念和亲密意愿")
        if decline_count > 2:
            signals.append("出现激情衰减信号（忙/累/改天吧）")
        if stage in ["稳定期", "婚姻期"] and score < 50:
            signals.append("稳定期激情自然下降，需要主动创造新鲜感")

        return {
            "score": score,
            "level": level,
            "description": desc,
            "signals": signals,
            "sternberg_note": "Sternberg爱情三元论：激情+亲密+承诺=完美爱情；激情随时间自然衰减，需要主动维护",
            "advice": "定期创造二人世界、尝试新体验、保持身体接触，不要让激情被日常消磨" if score < 50 else "保持浪漫和吸引力，定期创造新鲜感",
        }

    def _analyze_trust(self, messages, other_text, my_text) -> dict:
        """信任维度分析（关系健康的核心基石）。

        信任 = 可靠性预期 + 善意预期 + 行为一致性 + 透明度 + 犯错后道歉
        信任是关系的基础，没有信任就没有安全感。
        """
        if not messages:
            return {"score": 0, "level": "未知", "description": "数据不足",
                    "signals": [], "advice": "需要更多聊天数据"}

        # 信任建立信号
        trust_build_words = [
            "我相信你", "放心", "有你在", "我知道你会", "你办事我放心",
            "说实话", "老实说", "跟你说个秘密", "我只跟你说",
            "对不起", "我错了", "是我不好", "下次不会了",
            "我陪你", "有我呢", "别怕", "我在"
        ]

        # 信任破坏信号
        trust_break_words = [
            "你骗我", "你撒谎", "不信", "谁知道你说的真的假的",
            "你又", "你总是", "你从来都不", "说好的又",
            "别骗我", "你最好说实话", "你跟谁在一起", "你在哪",
            "查岗", "报备", "给我看手机", "你是不是瞒着我"
        ]

        # 透明度信号（主动分享行踪/感受）
        transparency_words = [
            "我在", "我刚", "我准备", "我今天", "跟你说", "我跟你讲",
            "我想", "我觉得", "我担心", "我害怕"
        ]

        trust_build = sum(1 for w in trust_build_words if w in other_text or w in my_text)
        trust_break = sum(1 for w in trust_break_words if w in other_text or w in my_text)
        transparency = sum(1 for w in transparency_words if w in other_text or w in my_text)

        # 计算信任分
        score = 50 + trust_build * 2 - trust_break * 5 + min(transparency, 10)
        score = max(0, min(100, score))

        if score >= 70:
            level = "信任度高"
            desc = "双方互相信任，沟通透明，关系有安全感"
        elif score >= 40:
            level = "信任度中等"
            desc = "有一定信任基础，但可以更坦诚和一致"
        else:
            level = "信任度低"
            desc = "信任不足，关系缺乏安全感，需要重建信任"

        signals = []
        if trust_break > 2:
            signals.append("出现信任破坏信号（欺骗/查岗/质疑）")
        if trust_build > 5:
            signals.append("频繁表达信任和支持")
        if transparency > 5:
            signals.append("沟通透明度高，主动分享行踪和感受")

        return {
            "score": score,
            "level": level,
            "description": desc,
            "signals": signals,
            "research_note": "信任=可靠性+善意+一致性+透明度+犯错后道歉；信任是关系满意度和稳定性的最强预测因子之一",
            "advice": "保持言行一致、主动透明、犯错及时道歉，不要让小谎言积累成大信任危机" if score < 50 else "保持坦诚和一致，信任是关系最宝贵的资产",
        }

    def _analyze_values_match(self, other_text, my_text, messages) -> dict:
        """价值观匹配度分析（关系长期稳定性的关键预测因子）。

        维度：人生优先级、金钱观、家庭观、事业观、育儿观、生活方式、道德底线。
        价值观匹配的伴侣关系满意度更高、冲突更少、离婚率更低。
        """
        if not messages:
            return {"score": 0, "level": "未知", "description": "数据不足",
                    "dimensions": {}, "advice": "需要更多聊天数据"}

        # 价值观话题关键词
        value_topics = {
            "金钱观": ["钱", "花钱", "省钱", "赚钱", "工资", "存款", "买房", "投资", "消费", "贵", "便宜"],
            "家庭观": ["爸妈", "父母", "家里", "家人", "结婚", "孩子", "育儿", "过年", "回家", "亲戚"],
            "事业观": ["工作", "事业", "升职", "加班", "辞职", "创业", "前途", "发展", "稳定"],
            "生活方式": ["旅游", "旅行", "出去玩", "宅", "运动", "健身", "吃", "做饭", "外卖", "熬夜", "早起"],
            "人生优先级": ["最重要", "最看重", "追求", "目标", "理想", "意义", "想要", "希望"],
            "道德底线": ["原则", "底线", "不能接受", "绝对不", "受不了", "讨厌", "恶心"],
        }

        dimensions = {}
        match_signals = 0
        mismatch_signals = 0
        total_topics = 0

        for topic, keywords in value_topics.items():
            topic_count = sum(1 for w in keywords if w in other_text or w in my_text)
            if topic_count > 0:
                total_topics += 1
                dimensions[topic] = "已讨论"
                # 简单匹配判断：如果双方都提到该话题，算有讨论基础
                other_has = any(w in other_text for w in keywords)
                my_has = any(w in my_text for w in keywords)
                if other_has and my_has:
                    match_signals += 1
            else:
                dimensions[topic] = "未讨论"

        # 冲突信号（价值观不一致的表现）
        conflict_signals = ["你怎么能", "我无法接受", "这怎么行", "你居然", "太过分了",
                           "三观不合", "不是一路人", "我们不一样", "你这种人"]
        mismatch_signals = sum(1 for w in conflict_signals if w in other_text or w in my_text)

        # 计算匹配分
        if total_topics == 0:
            score = 50  # 未讨论任何价值观话题，给中性分
            level = "未知"
            desc = "尚未深入讨论价值观话题，建议在适当时机了解对方核心价值观"
        else:
            base_score = 40 + (match_signals / total_topics) * 40
            score = base_score - mismatch_signals * 10
            score = max(0, min(100, score))

            if score >= 70:
                level = "价值观匹配"
                desc = f"已讨论{total_topics}个价值观话题，双方在{match_signals}个话题上有共同语言"
            elif score >= 40:
                level = "价值观部分匹配"
                desc = f"已讨论{total_topics}个价值观话题，部分话题存在差异，需要深入沟通"
            else:
                level = "价值观差异大"
                desc = f"已讨论{total_topics}个价值观话题，存在明显分歧，可能影响长期关系"

        return {
            "score": score,
            "level": level,
            "description": desc,
            "dimensions": dimensions,
            "topics_discussed": total_topics,
            "research_note": "价值观匹配是关系长期稳定性的关键预测因子；核心价值观（金钱/家庭/人生优先级）不一致是离婚的主要原因之一",
            "advice": "在关系早期就了解对方的核心价值观（金钱/家庭/人生优先级），不要回避差异，坦诚沟通寻找共识" if score < 60 else "保持价值观沟通，定期确认双方人生目标是否一致",
        }

    def _analyze_self_differentiation(self, other_text, my_text, messages) -> dict:
        """自我分化维度分析（Bowen家庭系统理论核心概念）。

        自我分化 = 区分情感和理性的能力 + 在亲密关系中保持独立自我的能力。
        高分化：能在亲密中保持独立，能区分情绪和事实，冲突中能理性思考。
        低分化：容易被情绪裹挟，要么融合（失去自我）要么疏离（逃避亲密）。
        自我分化水平高的人关系满意度更高、冲突更少。
        """
        if not messages:
            return {"score": 0, "level": "未知", "description": "数据不足",
                    "signals": [], "advice": "需要更多聊天数据"}

        # 高分化信号：理性表达、区分情绪和事实、保持独立
        high_diff_words = [
            "我觉得", "我认为", "我的想法是", "在我看来",
            "我理解你的感受，但是", "我知道你现在情绪不好，我们晚点聊",
            "我需要一点空间", "我想自己静一静", "我有自己的想法",
            "这是我的事", "我自己决定", "我支持你，但这是你的选择",
            "我们可以有不同意见", "你说得有道理，我也有不同看法"
        ]

        # 低分化信号：情绪裹挟、融合/疏离两极、非黑即白
        low_diff_words = [
            "你不爱我了", "你根本不在乎我", "你是不是想分手",
            "我都是为了你", "我为你付出这么多", "你必须", "你应该",
            "我离不开你", "没有你我活不下去", "你就是我的全部",
            "随便你", "都行", "无所谓", "你看着办", "别问我",
            "我妈说", "我家里人觉得", "他们说", "别人都",
            "你这样让我很没面子", "你让我怎么见人"
        ]

        high_count = sum(1 for w in high_diff_words if w in other_text or w in my_text)
        low_count = sum(1 for w in low_diff_words if w in other_text or w in my_text)

        # 计算分化分
        score = 50 + high_count * 3 - low_count * 4
        score = max(0, min(100, score))

        if score >= 70:
            level = "自我分化高"
            desc = "能在亲密中保持独立，能区分情绪和理性，冲突中能理性思考"
        elif score >= 40:
            level = "自我分化中等"
            desc = "有一定独立思考能力，但在情绪激动时容易被裹挟"
        else:
            level = "自我分化低"
            desc = "容易被情绪裹挟，在亲密中要么失去自我要么逃避，需要提升分化水平"

        signals = []
        if high_count > 3:
            signals.append("能理性表达观点，区分情绪和事实")
        if low_count > 3:
            signals.append("容易被情绪裹挟，出现融合或疏离倾向")
        if any(w in other_text or w in my_text for w in ["我妈说", "我家里人觉得"]):
            signals.append("容易受原生家庭意见影响，自我边界模糊")

        return {
            "score": score,
            "level": level,
            "description": desc,
            "signals": signals,
            "bowen_note": "Bowen家庭系统理论：自我分化是心理成熟的核心指标；高分化的人关系满意度更高、冲突更少、代际传递的负面模式更少",
            "advice": "学会在情绪激动时暂停、区分感受和事实、在亲密中保持独立自我，不要把全部价值感寄托在对方身上" if score < 50 else "保持良好的自我分化，在亲密中既连接又独立",
        }

    # ================================================================== #
    # v3.1 新增能力①：用户可修正（Proof of Personality）
    #   诊断结果出来后用户可以说"不对，他不是这样的"。
    #   修正记录持久化到 data/persons/{pid}/user_corrections.json（authors_note 层）。
    #   下次 analyze() 时：用户修正权重高于自动分析；dealbreaker（"绝对不是"）永不覆盖。
    # ================================================================== #
    # 关键事件关键词表（用于事件加权）
    _KEY_EVENT_KW = [
        "表白", "我喜欢你", "我爱你", "做我男", "做我女", "我们在一起", "确定关系",
        "我答应", "我保证", "说好了", "我承诺", "我说到做到",
        "分手", "我们分手", "吵架", "生气", "冷战", "别理我",
        "见家长", "你妈", "我爸妈", "叔叔阿姨", "提亲", "见父母",
    ]
    # 深度话题关键词
    _DEEP_TOPIC_KW = [
        "我的过去", "小时候", "童年", "原生家庭", "我爸妈", "我以前",
        "未来", "以后", "将来", "梦想", "理想", "规划", "目标",
        "家庭", "结婚", "孩子", "结婚以后",
    ]
    # 浅层话题关键词（打招呼/表情包/晚安）
    _SHALLOW_TOPIC_KW = [
        "早安", "晚安", "哈哈", "哈哈哈", "嗯", "哦", "好的", "在吗",
        "呵呵", "嘿嘿", "啦", "呀", "~", "～",
    ]

    # ================================================================== #
    # v3.1 三合一综合判定：关键词证据层 + 行为统计层 + 语境判断层
    #   - 关键词证据层：复用已修正词表（证据账本），只做命中强度统计；
    #   - 行为统计层：回复节奏/话题开启/消息长度/情绪密度/深夜活跃/冲突修复，
    #                 全部为可复核统计量（移植自 _behavior_profile.py 原型）；
    #   - 语境判断层：关键事件窗口语义 / 冲突收尾谁服软 / 亲密压力表达 /
    #                 婚姻真实事件证据（移植自 _extract_scenes/_deep_context 原型）。
    #   三层按权重综合，输出每层证据+分数+最终判定。纯本地规则，不调付费能力。
    # ================================================================== #

    # 亲密压力表达（她原话方向：太亲密会有压力）——语境层专用短语
    _INTIMACY_PRESSURE_KW = [
        "太亲密压力", "亲密压力", "太亲密了", "喘不过气", "需要空间",
        "保持距离", "靠太近", "有点窒息", "一个人待着", "别靠太近",
        "压力会很大", "太近了", "给我点空间",
    ]
    # 冲突收尾服软信号（谁先低头）
    _YIELD_KW = [
        "对不起", "我的错", "是我不好", "我错了", "记住了", "我错了还不行吗",
        "别生气了", "消消气", "是我不对", "我认错", "好好好", "我让步",
    ]
    # 高强度冲突事件词（语境层事件窗口）
    _HARD_CONFLICT_KW = [
        "离婚", "分手", "冷战", "滚", "我受够了", "别来找我",
        "不想理你", "别联系了", "拉黑", "过不下去了", "没法过了",
    ]
    # 婚姻真实事件证据（非词表互称，而是真实生活事件）
    _MARRIAGE_EVENT_KW = [
        "房子", "房产", "学费", "抚养费", "财产", "见家长", "谈离婚",
        "离婚协议", "孩子", "共同财产", "分割", "彩礼", "酒席",
    ]
    # 变卦 / 删记录 / 当挡箭牌信号
    _RENEGADE_KW = [
        "当我没说", "我收回", "不算数", "我删了", "记录删了", "删了记录",
        "我没说过", "我什么时候说", "你记错了", "当没发生", "撤回了",
    ]

    # ================================================================== #
    # 服软归因分类器（修复"她服软一律被当作修复意愿加分"缺陷）：
    #   把"道歉/顺从"拆开，再看她这句道歉之前我在干什么——
    #   若前文是我在施压/指责/情感索取/长篇输出 → 她的短认错只是回避/退缩收尾，
    #   不是主动修复；只有"无压力语境下她主动归因/承诺"才算修复证据。
    #   纯本地规则，不调付费能力。
    # ================================================================== #
    # 真正的道歉词（区别于"知道了/记住了"这类顺从式回应）
    _APOLOGY_KW = ["对不起", "抱歉", "我错了", "我的错", "听懂了"]
    # 顺从式回应（不算道歉，仅统计她对我指令的机械应答）
    _COMPLY_KW = ["知道了", "记住了", "好的", "好哒", "嗯嗯 好", "收到"]
    # 施压/指责/情感索取信号（出现在我前文窗口 = 压力语境）
    _PRESSURE_KW = [
        "你怎么", "为什么", "你总", "你从来", "你能不能", "又没", "说好", "答应",
        "骗", "失望", "滚", "离婚", "冷暴力", "无视", "选择性", "刻薄", "摔脸色",
        "我受够了", "别这样", "气死", "解释", "分手",
        "我真的很需要你", "需要你", "求你", "委屈", "怨气", "态度", "没有心",
        "哄", "哭", "难受", "逼", "一文不值", "忽视", "忽略", "发脾气", "变卦",
    ]
    # 归因/下一步承诺短语（出现才配称"主动修复"）
    _ATTRIBUTION_PHRASES = ["刚才", "下次", "以后", "我会", "我一定", "再也不", "不该"]
    # 前文窗口（我最近几条）拼接长度达到该值，视为"长篇情感输出"=隐性压力语境
    _YIELD_LONG_WINDOW_CHARS = 60

    # ---- 层权重：关键词30% / 行为统计35% / 语境事件35% ----
    _LAYER_WEIGHTS = {"keyword": 0.30, "behavior": 0.35, "context": 0.35}

    def _behavior_statistics_layer(self, messages: list,
                                   other_msgs: list, my_msgs: list) -> dict:
        """行为统计层：从时间序列+消息内容计算可复核统计量，不依赖关键词判定。

        移植自 _behavior_profile.py 原型，全部本地规则、确定性。
        Returns:
            dict: {score, metrics, findings}
        """
        import statistics as _st
        seq = [{"dir": m.get("direction"),
                "c": (m.get("content") or "").strip(),
                "dt": self._parse_dt(m)} for m in messages]
        seq = [s for s in seq if s["dt"] is not None]
        n = len(seq)
        findings = []
        metrics: dict = {"total_seq": n}

        # 1) 回复节奏：我发->她回 / 她发->我回 的相邻延迟（分钟）
        my_to_her, her_to_me = [], []
        for i in range(1, n):
            gap_min = (seq[i]["dt"] - seq[i - 1]["dt"]).total_seconds() / 60.0
            if gap_min < 0 or gap_min > 60 * 24 * 3:
                continue
            if seq[i - 1]["dir"] == "sent" and seq[i]["dir"] == "received":
                my_to_her.append(gap_min)
            elif seq[i - 1]["dir"] == "received" and seq[i]["dir"] == "sent":
                her_to_me.append(gap_min)

        def _lag_bucket(arr):
            if not arr:
                return {"n": 0, "median": None, "within_1h_pct": None}
            arr_sorted = sorted(arr)
            med = arr_sorted[len(arr_sorted) // 2]
            within = sum(1 for x in arr if x <= 60) / len(arr) * 100
            return {"n": len(arr), "median": round(med, 1),
                    "within_1h_pct": round(within, 0)}

        lag_mh = _lag_bucket(my_to_her)   # 我发->她回
        lag_hm = _lag_bucket(her_to_me)   # 她发->我回
        metrics["lag_me_to_her"] = lag_mh
        metrics["lag_her_to_me"] = lag_hm

        # 节奏不对称：她回我 vs 我回她，谁更慢
        if lag_mh["median"] and lag_hm["median"]:
            asym = lag_mh["median"] / max(1.0, lag_hm["median"])
            metrics["lag_asymmetry"] = round(asym, 2)
            if asym >= 2.0:
                findings.append(
                    f"回复节奏明显不对称：她回我中位{lag_mh['median']}分，"
                    f"我回她中位{lag_hm['median']}分（她比我慢约{asym:.1f}倍），"
                    "呈被动/低投入节奏")
            elif asym <= 0.6:
                findings.append(
                    f"她回我比我回她更快（她回{lag_mh['median']}分 vs "
                    f"我回{lag_hm['median']}分），投入节奏偏主动")
            else:
                findings.append(
                    f"回复节奏基本对称（她回{lag_mh['median']}分 / "
                    f"我回{lag_hm['median']}分）")

        # 2) 话题开启（连续同方向的第一条算开启）
        my_starts = her_starts = 0
        for i in range(n):
            if i == 0 or seq[i - 1]["dir"] != seq[i]["dir"]:
                if seq[i]["dir"] == "sent":
                    my_starts += 1
                else:
                    her_starts += 1
        metrics["my_starts"] = my_starts
        metrics["her_starts"] = her_starts
        start_ratio = round(my_starts / max(1, her_starts), 2)
        metrics["start_ratio_mine"] = start_ratio
        if start_ratio >= 2.0:
            findings.append(
                f"话题开启严重单向：我主动开{my_starts}次 / 她{her_starts}次"
                f"（{start_ratio}:1），她极少主动")
        elif start_ratio <= 0.8:
            findings.append(
                f"她主动开话题偏多（我{my_starts} / 她{her_starts}，{start_ratio}:1）")
        else:
            findings.append(f"话题开启较均衡（我{my_starts} / 她{her_starts}）")

        # 3) 消息长度（投入度结构）+ 极短敷衍占比
        my_len = [len(s["c"]) for s in seq if s["dir"] == "sent" and s["c"]]
        her_len = [len(s["c"]) for s in seq if s["dir"] == "received" and s["c"]]
        def _len_stat(arr):
            if not arr:
                return {"mean": 0.0, "median": 0.0, "total": 0, "terse_pct": 0.0}
            terse = sum(1 for x in arr if x <= 2) / len(arr) * 100
            return {"mean": round(_st.mean(arr), 1),
                    "median": round(_st.median(arr), 1),
                    "total": sum(arr), "terse_pct": round(terse, 0)}
        metrics["my_len"] = _len_stat(my_len)
        metrics["her_len"] = _len_stat(her_len)
        if metrics["her_len"]["terse_pct"] >= 30:
            findings.append(
                f"她极短(≤2字)回复占比{metrics['her_len']['terse_pct']:.0f}%，"
                "存在敷衍/打发式回应")
        if my_len and her_len:
            len_ratio = round(sum(my_len) / max(1, sum(her_len)), 2)
            metrics["length_ratio_mine"] = len_ratio
            if len_ratio >= 1.8:
                findings.append(
                    f"字数投入单向：我累计{sum(my_len)}字 vs 她{sum(her_len)}字"
                    f"（{len_ratio}:1），我表达量明显更大")

        # 4) 深夜活跃（0-5点）
        def _night(arr):
            nn = [s for s in arr if s["dt"] and s["dt"].hour < 5]
            return round(len(nn) / max(1, len(arr)) * 100, 1)
        night_me = _night([s for s in seq if s["dir"] == "sent"])
        night_her = _night([s for s in seq if s["dir"] == "received"])
        metrics["night_pct_mine"] = night_me
        metrics["night_pct_her"] = night_her
        if night_her >= 5:
            findings.append(
                f"她深夜(0-5点)消息占比{night_her:.1f}%，存在夜间情绪/失眠式活跃")

        # 5) 冲突-修复周期：冲突信号后20条内她是否出现正向表达
        pos_w = ["开心", "哈哈", "好呀", "想你", "爱你", "喜欢", "宝贝",
                 "辛苦", "抱抱", "好的", "嗯嗯"]
        conflict_w = ["分手", "离婚", "生气", "冷战", "烦", "滚", "讨厌", "吵"]
        conf_idx = [i for i, s in enumerate(seq)
                    if any(w in s["c"] for w in conflict_w)]
        repair = 0
        for i in conf_idx:
            for j in range(i + 1, min(i + 21, n)):
                if seq[j]["dir"] == "received" and any(w in seq[j]["c"] for w in pos_w):
                    repair += 1
                    break
        metrics["conflict_signal_count"] = len(conf_idx)
        metrics["conflict_repair_hits"] = repair
        if conf_idx:
            metrics["conflict_repair_rate_pct"] = round(
                repair / len(conf_idx) * 100, 0)
            findings.append(
                f"冲突信号{len(conf_idx)}处，其后20条内她正向恢复"
                f"{repair}处（{metrics['conflict_repair_rate_pct']:.0f}%）")

        # ---- 行为层分数（0-100）：行为模式"清晰度" ----
        # 由节奏不对称度、开启单向度、敷衍率、修复率共同决定，证据越清晰分越高。
        score = 30.0  # 基线：有基本行为流
        if metrics.get("lag_asymmetry") and metrics["lag_asymmetry"] >= 1.5:
            score += 20
        if start_ratio >= 1.5:
            score += 15
        if metrics["her_len"]["terse_pct"] >= 20:
            score += 15
        if metrics.get("conflict_repair_rate_pct") is not None:
            score += 10
        if n >= 500:
            score += 10
        score = min(100.0, score)

        return {"score": round(score, 1), "metrics": metrics,
                "findings": findings}

    # ------------------------------------------------------------------ #
    # A3-persona：抱怨累积 / Big Five E/A / S3 阶段信号
    # ------------------------------------------------------------------ #
    # 抱怨词表（received 方向）
    _COMPLAINT_KW = ["烦", "累", "生气", "不爽", "无语", "醉了", "服了", "无奈",
                     "坑", "糟", "烂", "差", "破", "讨厌", "烦死人", "受够了"]
    # 解决标记词表（道歉/补偿/行动，命中即清零该主题抱怨计数）
    _RESOLUTION_KW = ["对不起", "抱歉", "我错了", "补偿", "赔", "请你", "以后不会",
                      "我改", "原谅", "没事了", "好了", "行吧", "那就这样"]
    # Big Five E(外倾) 词表
    _EXTRAVERT_POS = ["聚会", "朋友", "玩", "开心", "热闹", "出去玩", "聚餐", "派对",
                      "逛街", "旅行", "哈哈", "哈哈哈", "笑"]
    _EXTRAVERT_NEG = ["一个人", "安静", "宅", "躺", "独处", "不想出门", "不想动", "颓废"]
    # Big Five A(宜人) 词表
    _AGREEABLE_POS = ["谢谢", "感谢", "抱歉", "没关系", "没事", "不客气", "应该的",
                      "辛苦", "麻烦你", "麻烦了"]
    _AGREEABLE_NEG = ["滚", "傻逼", "操", "狗屁", "放屁", "妈的", "他妈", "扯淡",
                      "胡说", "废话", "有病"]

    def _complaint_accumulation(self, messages: list) -> dict:
        """抱怨累积：同主题抱怨>3次且无解决标记→核心需求未满足信号。
        反例检查：阈值保守(>3次，非≥3)；解决标记宽松(行动/补偿也算)；先观察不扣分。"""
        from collections import Counter as _C
        theme_counts = _C()
        theme_examples = {}
        resolved_themes = set()
        for m in messages or []:
            if m.get("direction") != "received":
                continue
            c = m.get("content") or ""
            if not c:
                continue
            # 解决标记优先：命中解决词则清零所有主题（保守：只清该消息时段附近，这里简化全清）
            if any(k in c for k in self._RESOLUTION_KW):
                # 不直接全清，标记已解决主题（简化：记录解决事件，不影响计数）
                continue
            for kw in self._COMPLAINT_KW:
                if kw in c:
                    theme_counts[kw] += 1
                    if kw not in theme_examples:
                        theme_examples[kw] = []
                    if len(theme_examples[kw]) < 3:
                        theme_examples[kw].append({
                            "at": m.get("created_at", ""),
                            "snippet": self._snippet_of(c, 50),
                        })
        # 核心需求未满足信号：任一主题>3次
        unmet = {k: v for k, v in theme_counts.items() if v > 3}
        return {
            "theme_counts": dict(theme_counts),
            "unmet_needs_signal": bool(unmet),
            "unmet_themes": unmet,
            "examples": theme_examples,
            "note": "阈值保守(>3次)；解决标记宽松(行动/补偿也算)；先观察不扣分",
        }

    def _bigfive_ea(self, messages: list) -> dict:
        """Big Five E(外倾)/A(宜人) 词典打分。
        反例检查：词典打分中文准确率存疑，与 attachment 有重叠；
        作为新增附加字段，不改既有维度评分。"""
        recv_text = " ".join((m.get("content") or "") for m in (messages or [])
                             if m.get("direction") == "received")
        if not recv_text.strip():
            return {"E": None, "A": None, "note": "无对方消息"}
        # E: 外倾词(社交/热闹) vs 内倾词(独处/安静)
        e_pos = sum(recv_text.count(k) for k in self._EXTRAVERT_POS)
        e_neg = sum(recv_text.count(k) for k in self._EXTRAVERT_NEG)
        e_total = e_pos + e_neg
        e_score = round(e_pos / e_total * 100, 1) if e_total > 0 else None
        # A: 宜人词(友善/感谢) vs 敌意词(攻击/贬损)
        a_pos = sum(recv_text.count(k) for k in self._AGREEABLE_POS)
        a_neg = sum(recv_text.count(k) for k in self._AGREEABLE_NEG)
        a_total = a_pos + a_neg
        a_score = round(a_pos / a_total * 100, 1) if a_total > 0 else None
        return {
            "E_extraversion": {"score": e_score, "pos_hits": e_pos, "neg_hits": e_neg},
            "A_agreeableness": {"score": a_score, "pos_hits": a_pos, "neg_hits": a_neg},
            "note": "词典打分，仅参考；不改既有 attachment 评分",
        }

    def _stage_signals(self, messages: list) -> dict:
        """S1-S4 阶段信号逐条评估。
        反例检查：
         - S1 延迟骤降+角色互换升温：反例=对方忙，不一定升温 → 信号弱，标待办
         - S2 代词密度降>30%+极性走低：反例=代词密度受话题影响大 → 信号弱，标待办
         - S3 负面感叹骤增+单方发言>70%+"我觉得…你不…"句式 → 信号较强，落地
         - S4 分离词+长静默：反例=长静默可能双方都忙 → 信号弱，标待办
        """
        # S3 落地：负面感叹 + 单方发言比 + "我觉得…你不…"句式
        recv = [m for m in (messages or []) if m.get("direction") == "received"]
        sent = [m for m in (messages or []) if m.get("direction") == "sent"]
        total = len(recv) + len(sent)
        recv_ratio = round(len(recv) / total, 2) if total > 0 else 0.5
        # 负面感叹词（！！/？？/怎么又/能不能行）
        neg_exclaim = sum(
            1 for m in recv
            if m.get("content") and ("！！" in m["content"] or "？？" in m["content"]
                                     or "怎么又" in m["content"] or "能不能行" in m["content"])
        )
        # "我觉得…你不…"句式
        you_dont_think = sum(
            1 for m in recv
            if m.get("content") and "我觉得" in m["content"] and "你不" in m["content"]
        )
        s3_crisis = (recv_ratio > 0.7 or recv_ratio < 0.3) and neg_exclaim >= 3
        return {
            "S1_delay_role_swap": {"status": "待办", "reason": "反例=对方忙不一定升温；信号弱"},
            "S2_pronoun_density_drop": {"status": "待办", "reason": "反例=代词密度受话题影响大；信号弱"},
            "S3_negative_exclaim_unilateral": {
                "status": "落地" if s3_crisis else "观察",
                "recv_ratio": recv_ratio,
                "neg_exclaim_count": neg_exclaim,
                "you_dont_think_count": you_dont_think,
                "crisis_warning": s3_crisis,
            },
            "S4_separation_long_silence": {"status": "待办", "reason": "反例=长静默可能双方都忙；信号弱"},
        }

    def _yield_attribution_analysis(self, seq: list) -> dict:
        """服软归因分类器：把她(received)的真正道歉句逐条放回前文窗口判定性质。

        修复"她服软一律被当作修复意愿加分"的旧缺陷——道歉不等于主动修复，
        关键看她这句之前我在干什么。纯本地规则，不调付费能力。

        判定规则（施压语境优先）：
          - 前文窗口 = 她这句之前我最近最多 5 条 sent（跨方向向前最多扫 60 条）；
          - pressure_yield（回避/退缩证据）：前文命中任一施压信号，
            或前文拼接为长篇情感输出（>=_YIELD_LONG_WINDOW_CHARS 字）。
            即使带归因短语（如"我错了不该冷暴力你"），只要身处施压语境，
            也判为被动屈服，不计修复分；
          - proactive_repair（修复证据）：仅当前文无任何压力语境，
            且服软文本带归因/下一步承诺短语；
          - 其余"无压力但也无归因"的极短回应：保守不授予修复分，
            归入 pressure_yield，彻底杜绝"低头=修复"旧逻辑。

        顺从式回应（知道了/记住了/好的…）与道歉句互斥：一句只归一类。
        """
        from collections import Counter as _Counter
        events: list = []
        monthly = _Counter()
        signals_used = _Counter()
        comply_count = 0

        for i, s in enumerate(seq):
            if s["dir"] != "received" or not s["c"]:
                continue
            text = s["c"]
            # 道歉词优先；命中道歉词才进归因事件，命中顺从词则单独计数
            matched_apology = next(
                (kw for kw in self._APOLOGY_KW if kw in text), None)
            matched_comply = next(
                (kw for kw in self._COMPLY_KW if kw in text), None)
            if matched_apology is None:
                if matched_comply is not None:
                    comply_count += 1
                continue

            # 前文窗口：我最近最多 5 条 sent（向前扫最多 60 条）
            prev_window = []
            for j in range(i - 1, max(-1, i - 61), -1):
                if seq[j]["dir"] == "sent":
                    prev_window.append(seq[j]["c"])
                    if len(prev_window) == 5:
                        break
            prev_window = list(reversed(prev_window))
            joined_prev = "".join(prev_window)

            sigs = [p for p in self._PRESSURE_KW if p in joined_prev]
            for p in sigs:
                signals_used[p] += 1
            pressured = bool(sigs) or (
                len(joined_prev) >= self._YIELD_LONG_WINDOW_CHARS)
            has_attr = [a for a in self._ATTRIBUTION_PHRASES if a in text]
            label = ("proactive_repair"
                     if ((not pressured) and has_attr) else "pressure_yield")

            month = (s.get("at") or "")[:7]
            monthly[month] += 1
            events.append({
                "at": s.get("at") or "",
                "text": self._snippet_of(text, 60),
                "matched_kw": matched_apology,
                "prev_window": [self._snippet_of(p, 50) for p in prev_window],
                "pressure_signals": sigs,
                "has_attribution": has_attr,
                "label": label,
            })

        pressure_count = sum(1 for e in events if e["label"] == "pressure_yield")
        proactive_count = sum(1 for e in events if e["label"] == "proactive_repair")
        return {
            "total_yield_events": len(events),
            "pressure_yield_count": pressure_count,
            "proactive_repair_count": proactive_count,
            "comply_response_count": comply_count,
            "monthly_distribution": dict(sorted(monthly.items())),
            "pressure_signals_used": [p for p, _ in signals_used.most_common()],
            "yield_events": events,
        }

    def _context_judgment_layer(self, messages: list, other_msgs: list,
                                other_text: str) -> dict:
        """语境判断层：关键事件窗口语义，读真实事件而非频率。

        移植自 _extract_scenes.py / _deep_context.py 原型：
          - 高强度冲突事件窗口（离婚/分手/冷战/滚…）
          - 冲突收尾谁服软（方向判定）
          - 亲密压力表达（她原话方向）
          - 婚姻真实事件证据（房子/学费/见家长/谈离婚…）
          - 变卦/删记录/当挡箭牌信号
        Returns:
            dict: {score, events, findings}
        """
        seq = [{"dir": m.get("direction"), "c": (m.get("content") or "").strip(),
                "dt": self._parse_dt(m),
                "at": m.get("created_at")} for m in messages]
        seq = [s for s in seq if s["c"]]
        events: dict = {}
        findings = []

        # 1) 高强度冲突事件：定位她发出的高强度冲突词，输出前后窗口样本
        hard_idx = [i for i, s in enumerate(seq)
                    if s["dir"] == "received"
                    and any(w in s["c"] for w in self._HARD_CONFLICT_KW)]
        hard_windows = []
        for i in hard_idx[:5]:
            start = max(0, i - 4)
            end = min(len(seq), i + 8)
            window = []
            for j in range(start, end):
                who = "我" if seq[j]["dir"] == "sent" else "她"
                window.append(f"[{who}] {self._snippet_of(seq[j]['c'], 36)}")
            hard_windows.append({
                "at": seq[i]["at"] or "",
                "window": window,
            })
        events["hard_conflict_count"] = len(hard_idx)
        events["hard_conflict_windows"] = hard_windows
        if hard_idx:
            findings.append(
                f"检出{len(hard_idx)}处她发出的高强度冲突事件"
                f"（离婚/分手/冷战/滚等），已抽{min(5, len(hard_idx))}个事件窗口")

        # 2) 冲突收尾谁服软：在高强度冲突事件后，向前扫描最多40条消息
        #    （冲突常持续数小时，16条窗口会漏掉随后约1小时的服软），
        #    取窗口内第一条服软词，判定方向。
        my_yield = her_yield = 0
        yield_samples = []
        for i in hard_idx:
            for j in range(i + 1, min(i + 41, len(seq))):
                if any(w in seq[j]["c"] for w in self._YIELD_KW):
                    if seq[j]["dir"] == "sent":
                        my_yield += 1
                    else:
                        her_yield += 1
                    yield_samples.append({
                        "at": seq[j]["at"] or "",
                        "who": "我" if seq[j]["dir"] == "sent" else "她",
                        "snippet": self._snippet_of(seq[j]["c"], 30),
                    })
                    break
        events["my_yield_after_conflict"] = my_yield
        events["her_yield_after_conflict"] = her_yield
        events["yield_samples"] = yield_samples[:8]
        if my_yield + her_yield >= 1:
            if her_yield > my_yield:
                # 旧缺陷：此处曾写"存在双向修复，非单向低头"，把她的低头一律当修复。
                # 现改为中性陈述，真正的修复与否由下方 yield_attribution 归因判定。
                findings.append(
                    f"冲突收尾方向计数：她先低头{her_yield}次 / 我先低头{my_yield}次"
                    f"（样本：{yield_samples[0]['snippet'] if yield_samples else ''}）；"
                    "该计数仅反映谁先认错，不等于修复意愿，需结合服软归因判断")
            elif my_yield > her_yield:
                findings.append(
                    f"冲突后多由我先服软（我{my_yield}次 / 她{her_yield}次），"
                    "她较少主动低头，冲突收尾成本单向")
            else:
                findings.append(
                    f"冲突后服软次数相当（我{my_yield} / 她{her_yield}）")

        # 2b) 服软归因分类器：把她的真正道歉句放回前文窗口，区分压力性屈服/主动修复
        yield_attr = self._yield_attribution_analysis(seq)
        events["yield_attribution"] = yield_attr
        if yield_attr["total_yield_events"] >= 1:
            findings.append(
                f"服软归因：她的道歉词命中{yield_attr['total_yield_events']}次"
                f"（{yield_attr['monthly_distribution']}），"
                f"其中压力性屈服{yield_attr['pressure_yield_count']}次"
                f"（紧随我施压/指责/情感索取/长篇输出后的短认错，属回避/退缩），"
                f"主动修复仅{yield_attr['proactive_repair_count']}次；"
                f"另有顺从式回应{yield_attr['comply_response_count']}次，不计道歉")

        # 3) 亲密压力表达（她原话方向）：命中证据片段
        pressure_hits = self._collect_hits(
            messages, self._INTIMACY_PRESSURE_KW, "received", 4)
        events["intimacy_pressure_hits"] = pressure_hits
        if pressure_hits:
            findings.append(
                f"她明确表达过亲密压力/需要空间（{len(pressure_hits)}处）："
                f"{pressure_hits[0]['snippet']}")

        # 4) 婚姻真实事件证据（非互称词，而是真实生活事件）
        marriage_event_hits = self._collect_hits(
            messages, self._MARRIAGE_EVENT_KW, "received", 5)
        # 双向都收（共同财产/谈离婚可能在我文本里）
        marriage_event_hits += self._collect_hits(
            messages, self._MARRIAGE_EVENT_KW, "sent", 5)
        events["marriage_real_event_hits"] = marriage_event_hits
        if marriage_event_hits:
            findings.append(
                f"检出婚姻真实生活事件证据{len(marriage_event_hits)}处"
                f"（房子/学费/财产/见家长/谈离婚等），"
                "关系为真实婚姻事实而非仅互称")

        # 5) 变卦/删记录/当挡箭牌信号
        renegade_hits = self._collect_hits(
            messages, self._RENEGADE_KW, "received", 4)
        renegade_hits += self._collect_hits(
            messages, self._RENEGADE_KW, "sent", 4)
        events["renegade_hits"] = renegade_hits
        if renegade_hits:
            findings.append(
                f"检出变卦/删记录/否认前话信号{len(renegade_hits)}处："
                f"{renegade_hits[0]['snippet']}")

        # ---- 语境层分数（0-100）：真实事件上下文权重 ----
        score = 25.0  # 基线
        if len(hard_idx) >= 3:
            score += 20
        elif len(hard_idx) >= 1:
            score += 10
        if my_yield > her_yield and (my_yield + her_yield) >= 2:
            score += 15
        if pressure_hits:
            score += 15
        if marriage_event_hits:
            score += 15
        if renegade_hits:
            score += 10
        score = min(100.0, score)

        return {"score": round(score, 1), "events": events,
                "findings": findings}

    def _three_layer_synthesis(self, messages: list, other_msgs: list,
                               my_msgs: list, other_text: str, my_text: str,
                               attachment: dict, relationship_stage: dict,
                               conflict_repair: dict, emotional_bid: dict = None) -> dict:
        """三合一综合判定入口：关键词证据 + 行为统计 + 语境事件。

        纯附加：不改任何既有评分/字段，只产出新判定块。
        """
        # ---- 层1：关键词证据层（复用已修正词表，只统计命中强度）----
        avoid_kw = sum(1 for kw in self._AVOID_KW if kw in other_text)
        anx_kw = sum(1 for kw in self._ANXIOUS_KW if kw in other_text)
        sec_kw = sum(1 for kw in self._SECURE_KW if kw in other_text)
        dark_cats = [c for c, kws in self._DARK_TRIAD_SIGNALS.items()
                     if any(kw in other_text for kw in kws)]
        def_cats = [c for c, kws in self._DEFENSIVE_SIGNALS.items()
                    if any(kw in other_text for kw in kws)]
        broken = self._collect_hits(messages, self._PROMISE_BREAK_KW, "received", 3)
        broken += self._collect_hits(messages, self._PROMISE_BREAK_KW, "sent", 3)

        kw_categories_hit = sum(1 for v in (
            avoid_kw, anx_kw, sec_kw) if v > 0) + len(dark_cats) + len(def_cats)
        # 词表证据强度：命中类别数 / 期望饱和类别数，封顶100
        keyword_score = min(100.0, kw_categories_hit / 12.0 * 100.0)
        keyword_evidence = {
            "avoidance_kw_hits": avoid_kw,
            "anxiety_kw_hits": anx_kw,
            "security_kw_hits": sec_kw,
            "dark_triad_categories": dark_cats,
            "defensive_categories": def_cats,
            "broken_promise_snippets": broken,
        }
        keyword_findings = []
        if avoid_kw >= 3:
            keyword_findings.append(f"回避词表命中{avoid_kw}处（要空间/打太极/贬低）")
        if anx_kw >= 3:
            keyword_findings.append(f"焦虑词表命中{anx_kw}处（怕抛弃/反复确认）")
        if dark_cats:
            keyword_findings.append(f"暗黑三角类别命中：{', '.join(dark_cats)}")
        if def_cats:
            keyword_findings.append(f"防御性沟通类别命中：{', '.join(def_cats)}")
        if broken:
            keyword_findings.append(f"爽约/食言证据{len(broken)}处")
        if not keyword_findings:
            keyword_findings.append("词表命中稀疏，关键词层证据弱，需依赖行为与语境层")

        keyword_layer = {"score": round(keyword_score, 1),
                         "evidence": keyword_evidence,
                         "findings": keyword_findings}

        # ---- 层2：行为统计层 ----
        behavior_layer = self._behavior_statistics_layer(
            messages, other_msgs, my_msgs)

        # ---- 层3：语境判断层 ----
        context_layer = self._context_judgment_layer(
            messages, other_msgs, other_text)

        # ---- 综合判定：加权融合 ----
        w = self._LAYER_WEIGHTS
        combined = (keyword_layer["score"] * w["keyword"]
                    + behavior_layer["score"] * w["behavior"]
                    + context_layer["score"] * w["context"])

        # 依恋倾向综合判定：以既有 attachment 为主，用行为/语境层交叉印证
        attach_type = (attachment.get("primary_type")
                       or attachment.get("type") or "未知")
        cross_notes = []
        beh_metrics = behavior_layer["metrics"]
        if beh_metrics.get("start_ratio_mine", 1) >= 2.0:
            cross_notes.append("行为层显示她极少主动开话题，与回避倾向相互印证")
        if context_layer["events"].get("intimacy_pressure_hits"):
            cross_notes.append("语境层读出她主动表达亲密压力，是回避/退缩的真实来源")
        yield_attr = context_layer["events"].get("yield_attribution") or {}
        my_y = context_layer["events"].get("my_yield_after_conflict", 0)
        her_y = context_layer["events"].get("her_yield_after_conflict", 0)
        if her_y == 0 and my_y > 0:
            cross_notes.append("冲突后几乎都是我先服软，她不低头——非单纯焦虑，带权力/退缩")
        # 服软归因修正（核心）：旧逻辑曾在 her_yield>=my 时写"退缩之外仍有修复意愿"，
        # 把压力性屈服误当修复加分。现改为按归因结果定性，不再因她先低头就加分。
        _ya_total = yield_attr.get("total_yield_events", 0)
        _ya_press = yield_attr.get("pressure_yield_count", 0)
        _ya_proact = yield_attr.get("proactive_repair_count", 0)
        if _ya_total > 0:
            if _ya_press >= _ya_proact and _ya_press > 0:
                cross_notes.append(
                    f"服软归因修正：她的{_ya_total}次道歉中{_ya_press}次为压力性屈服"
                    f"（紧随我施压/索取/长篇输出后的极短认错收尾），"
                    f"属回避/退缩行为，不计修复意愿")
            if _ya_proact == 0:
                cross_notes.append(
                    "她几乎无主动修复证据：未见任何无压力语境下的归因/承诺式道歉")
            elif _ya_proact > 0:
                cross_notes.append(
                    f"她有{_ya_proact}次无压力下的主动归因道歉，可作修复证据（仅此部分）")

        # 证据平衡：哪层分数最高即哪层主导
        layer_scores = {"关键词证据层": keyword_layer["score"],
                        "行为统计层": behavior_layer["score"],
                        "语境判断层": context_layer["score"]}
        dominant_layer = max(layer_scores, key=layer_scores.get)

        # 风险旗标
        risk_flags = []
        if dark_cats:
            risk_flags.append(f"暗黑三角词表命中：{', '.join(dark_cats)}")
        if context_layer["events"].get("renegade_hits"):
            risk_flags.append("检出变卦/删记录/否认前话，存在口径反复")
        if relationship_stage.get("stage") == "已婚":
            risk_flags.append("关系阶段判定为已婚，需结合真实婚姻事件证据看承诺与退出成本")
        if conflict_repair.get("repair_rate") is not None \
                and conflict_repair.get("repair_rate", 100) < 50 \
                and conflict_repair.get("conflict_count", 0) >= 3:
            risk_flags.append(
                f"冲突修复率{conflict_repair.get('repair_rate')}%偏低"
                f"（{conflict_repair.get('conflict_count')}次冲突）")

        # 置信度：按三层总证据量
        total_evidence = (avoid_kw + anx_kw + sec_kw
                          + beh_metrics.get("total_seq", 0) // 200
                          + context_layer["events"].get("hard_conflict_count", 0))
        confidence = "high" if total_evidence >= 15 else (
            "medium" if total_evidence >= 6 else "low")

        # 服软归因修正（final_judgment 新字段）：把"压力性屈服→回避/退缩上调"、
        # "主动修复→修复证据重新定性"显式落盘，供下游消费，不再把屈服当修复加分。
        yield_attribution_correction = {
            "corrected": bool(_ya_total > 0),
            "total_yield_events": _ya_total,
            "pressure_yield_count": _ya_press,
            "proactive_repair_count": _ya_proact,
            "comply_response_count": yield_attr.get("comply_response_count", 0),
            "interpretation": (
                f"压力性屈服{_ya_press}次 → 上调为回避/退缩证据（被施压后极短认错收尾）；"
                f"主动修复{_ya_proact}次 → 修复意愿证据（仅此部分可计入修复）。"
                f"旧逻辑误把{_ya_press}次屈服当修复加分，现予纠正。"
                if _ya_total > 0 else "未检出她的道歉句，无服软归因可修正"),
        }

        # ---- 大模型校准块（长期分布 × 主动投入，纯本地规则，零付费LLM）----
        # 依据：9个月全文通读的真实结论——
        #   ① 她的情感/关心表达几乎全部是"回应性的"：自己主动开话题时带情感词/关心词次数趋近0；
        #   ② 她 ≥20字 的长消息占比极低（全量约0.5%），从不主动展开表达；
        #   ③ 服软归因：道歉全是压力性屈服，主动修复=0；
        #   ④ 关键词层低分本身是"从不直说情绪"的回避信号，不应按"证据弱"降权。
        # 规则：6个独立信号逐项命中计分（封顶+20），combined 校准后上修，
        #       反映"回应在线但投入缺位"的疏离回避真实强度。
        _aff_kw = ("想你", "爱你", "抱抱", "亲亲", "喜欢", "心疼", "宝贝", "老公",
                   "么么", "舍不得", "在乎", "想见", "我爱你", "晚安", "亲爱的")
        _care_kw = ("注意安全", "多喝", "喝水", "别熬夜", "好好吃饭", "吃药",
                    "早点休息", "早点睡", "路上小心", "带伞", "穿厚", "保暖",
                    "到了说一声", "盖好", "记得吃", "照顾好自己", "注意身体",
                    "难受", "去医院", "冷不冷", "饿不饿")
        _init_aff = _init_care = 0
        _prev_dir = None
        for _m in messages:
            _d = _m.get("direction")
            _c = (_m.get("content") or "").strip()
            if _d == "received" and _prev_dir != "received":
                if any(_w in _c for _w in _aff_kw):
                    _init_aff += 1
                if any(_w in _c for _w in _care_kw):
                    _init_care += 1
            _prev_dir = _d
        _other_n = max(1, len(other_msgs))
        _long_pct = sum(1 for _m in other_msgs
                        if len((_m.get("content") or "").strip()) >= 20) / _other_n * 100
        _terse_pct = behavior_layer["metrics"].get("her_len", {}).get("terse_pct", 0)
        _len_ratio = behavior_layer["metrics"].get("length_ratio_mine", 0) or 0

        # ---- 上修项（回避/退缩证据）：逐项命中计分，封顶+20 ----
        _cal_up = 0
        _cal_reasons = []
        if _init_aff == 0 and _other_n >= 100:
            _cal_up += 4
            _cal_reasons.append(
                f"她主动开话题带情感词0次（共{_init_aff}次）：情感表达全为回应性，非主动投入")
        if _init_care == 0 and _other_n >= 100:
            _cal_up += 3
            _cal_reasons.append(
                f"她主动开话题带关心词0次：实用性关心也是被带动才说，不构成主动投入")
        if _long_pct < 2:
            _cal_up += 4
            _cal_reasons.append(
                f"她≥20字长消息占比仅{_long_pct:.1f}%：长期几乎从不展开表达，属回避/退缩")
        if _ya_proact == 0 and _ya_total > 0:
            _cal_up += 3
            _cal_reasons.append(
                f"服软归因{_ya_total}次全为压力性屈服、主动修复0次：无修复意愿证据")
        if _terse_pct >= 35:
            _cal_up += 3
            _cal_reasons.append(
                f"她极短(≤2字)回复占比{_terse_pct:.0f}%：高敷衍，对话由我的表达量撑着")
        if _len_ratio >= 4:
            _cal_up += 3
            _cal_reasons.append(
                f"字数比{_len_ratio:.2f}:1（我{behavior_layer['metrics'].get('my_len', {}).get('total', 0)}字"
                f"/她{behavior_layer['metrics'].get('her_len', {}).get('total', 0)}字）：单向投入")
        # 新增信号：Gottman 投标转向率过低（≤10%，健康线约86%）= 对方连接请求长期被转向别处，回避/退缩证据
        _bid = emotional_bid or {}
        _bid_rate = _bid.get("rate")
        _bid_sample = _bid.get("sample_size", 0) or 0
        if _bid_sample >= 3 and _bid_rate is not None and _bid_rate <= 10:
            _cal_up += 4
            _cal_reasons.append(
                f"Gottman投标转向率仅{_bid_rate:.1f}%（健康线约86%，样本{_bid_sample}）："
                f"对方连接请求长期被敷衍/忽略，回避/退缩证据")
        _cal_up = min(20, _cal_up)

        # ---- 下修抵扣项（在线参与/正向投入证据）：逐项命中扣分；净调整须逐项有 evidence ----
        _lag_asym = beh_metrics.get("lag_asymmetry")
        _aff_ratio = _init_aff / _other_n * 100.0 if _other_n else 0.0
        _cal_down = 0
        _cal_down_reasons = []
        if _lag_asym is not None and _lag_asym < 1:
            _cal_down += 3
            _cal_down_reasons.append(
                f"回复时差不对称{_lag_asym:.2f}<1（她回得比我快）：在线参与证据，部分抵扣回避上修")
        if _bid_sample >= 3 and _bid_rate is not None and _bid_rate > 50:
            _cal_down += 4
            _cal_down_reasons.append(
                f"Gottman投标转向率{_bid_rate:.1f}%>50%：连接请求多被正面回应，关系在线，抵扣回避上修")
        if _aff_ratio >= 5 and _other_n >= 100:
            _cal_down += 3
            _cal_down_reasons.append(
                f"主动情感开启占比{_aff_ratio:.1f}%≥5%：仍有稳定主动情感投入，抵扣回避上修")
        _cal_down = min(15, _cal_down)

        # 双向校准：可上可下；封顶95，保底不低于 combined-10（避免无依据大幅下拉）
        _net_adj = _cal_up - _cal_down
        calibrated_combined = min(95.0, max(round(combined - 10, 1),
                                            round(combined + _net_adj, 1)))

        # 动态叙事：仅在对应信号真为0时才写"零"，否则如实描述实际次数/占比，与 evidence 严格一致
        if _init_aff == 0 and _other_n >= 100:
            _aff_narr = "长期零主动情感表达"
        else:
            _aff_narr = (f"主动情感开启{_init_aff}次但占比低"
                         f"（约{_aff_ratio:.1f}%，以回应性为主）")
        if _init_care == 0 and _other_n >= 100:
            _care_narr = "零主动实用关心"
        else:
            _care_narr = f"主动关心开启{_init_care}次"
        if _ya_proact == 0 and _ya_total > 0:
            _repair_narr = "道歉全为压力性屈服、无主动修复"
        else:
            _repair_narr = f"主动修复{_ya_proact}次"
        _narrative = (
            f"校准后综合分{calibrated_combined:.0f}/100（原{combined:.1f}，"
            f"净调整{_net_adj:+d}=上修{_cal_up}−抵扣{_cal_down}）："
            f"{_aff_narr}；{_care_narr}；{_repair_narr}。"
            f"回应在线≠主动投入，实用性关心≠情感投入；"
            f"回避强度按命中证据{'上修' if _net_adj >= 0 else '下修'}。")
        llm_calibration = {
            "method": "local-rules-longterm-distribution-bidirectional",
            "llm_used": False,
            "adjustment": _net_adj,
            "adjustment_up": _cal_up,
            "adjustment_down": _cal_down,
            "combined_score": round(combined, 1),
            "calibrated_score": calibrated_combined,
            "signals_hit": len(_cal_reasons),
            "signals_total": 7,
            "evidence": {
                "her_initiated_affective": _init_aff,
                "her_initiated_care": _init_care,
                "her_initiated_affective_ratio_pct": round(_aff_ratio, 2),
                "her_long_msg_pct_ge20": round(_long_pct, 2),
                "her_terse_pct_le2": _terse_pct,
                "length_ratio_mine": _len_ratio,
                "proactive_repair": _ya_proact,
                "pressure_yield": _ya_press,
                "gottman_bid_response_rate": _bid_rate,
                "gottman_bid_sample": _bid_sample,
                "lag_asymmetry": _lag_asym,
            },
            "reasons": _cal_reasons,
            "deductions": _cal_down_reasons,
            "interpretation": _narrative,
        }

        final_judgment = {
            "combined_score": round(combined, 1),
            "calibrated_score": calibrated_combined,
            "llm_calibration": llm_calibration,
            "attachment_lean": attach_type,
            "cross_validation": cross_notes,
            "yield_attribution_correction": yield_attribution_correction,
            "key_patterns": (behavior_layer["findings"][:2]
                             + context_layer["findings"][:2]),
            "risk_flags": risk_flags,
            "dominant_layer": dominant_layer,
            "evidence_balance": layer_scores,
            "confidence": confidence,
            "summary": (
                f"三合一综合判定（权重 关键词{w['keyword']:.0%}/"
                f"行为{w['behavior']:.0%}/语境{w['context']:.0%}）："
                f"综合分{combined:.0f}/100，校准后{calibrated_combined:.0f}/100"
                f"（净调整{_net_adj:+d}=上修{_cal_up}/抵扣{_cal_down}，长期分布×主动投入双向修正），依恋倾向判为「{attach_type}」；"
                f"证据由「{dominant_layer}」主导。"
                + (f"服软归因：{_ya_press}次压力性屈服/0主动修复"
                   if _ya_total > 0 else "")
                + ("；".join(cross_notes) if cross_notes else "")
                + ("；".join(_cal_reasons) if _cal_reasons else "")
            ),
        }

        return {
            "version": "v3.1-three-layer",
            "weights": w,
            "keyword_layer": keyword_layer,
            "behavior_layer": behavior_layer,
            "context_layer": context_layer,
            "final_judgment": final_judgment,
        }

    def _corrections_dir(self, person_id: str) -> str:
        """返回该人物的作者注释（用户修正）目录。"""
        import os
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "persons", person_id)

    def apply_user_correction(self, person_id: str, field: str, old_value,
                              new_value, reason: str = "",
                              is_dealbreaker: bool = False) -> dict:
        """登记一条用户对自动诊断的修正（Proof of Personality）。

        Args:
            person_id: 人物 ID。
            field: 被修正字段路径（点分，如 "attachment.primary_type"）。
            old_value: 自动分析给出的值（供对照）。
            new_value: 用户认定的真实值。
            reason: 用户为什么这么说（"他其实不是回避，只是那段时间忙"）。
            is_dealbreaker: True=用户标记的"绝对不是"特征（硬过滤器），
                            永不被自动分析覆盖。

        Returns:
            dict: 写入的修正记录。
        """
        import json
        import os
        record = {
            "field": field,
            "old_value": old_value,
            "new_value": new_value,
            "reason": reason,
            "is_dealbreaker": bool(is_dealbreaker),
            "corrected_at": datetime.now().isoformat(),
        }
        try:
            person_dir = self._corrections_dir(person_id)
            os.makedirs(person_dir, exist_ok=True)
            path = os.path.join(person_dir, "user_corrections.json")
            history: list = []
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, list):
                        history = loaded
                except (json.JSONDecodeError, OSError):
                    history = []
            history.append(record)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            logger.info("用户修正已登记: %s %s %s->%s",
                        person_id, field, old_value, new_value)
        except Exception as e:  # noqa: BLE001
            logger.warning("用户修正写入失败: %s", e)
        return record

    def _load_user_corrections(self, person_id: str) -> list:
        """读取该人物的全部用户修正（authors_note 层）。无则返回空列表。"""
        import json
        import os
        try:
            path = os.path.join(self._corrections_dir(person_id), "user_corrections.json")
            if not os.path.exists(path):
                return []
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            return loaded if isinstance(loaded, list) else []
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("读取用户修正失败: %s", e)
            return []

    @staticmethod
    def _set_path_value(obj: dict, path: str, value) -> bool:
        """按点分路径在嵌套 dict 里写值。返回是否命中。"""
        parts = path.split(".")
        cur = obj
        for p in parts[:-1]:
            if not isinstance(cur, dict) or p not in cur or not isinstance(cur[p], dict):
                return False
            cur = cur[p]
        last = parts[-1]
        if isinstance(cur, dict) and last in cur:
            cur[last] = value
            return True
        return False

    def _apply_corrections_to_profile(self, profile: dict, person_id: str) -> dict:
        """把用户修正叠加到本次自动画像上。

        - 普通修正：覆盖对应字段，并在 profile["user_corrections_applied"] 记录。
        - dealbreaker：覆盖字段 + 写入 profile["dealbreakers"]，下次分析也不会被
          自动结果回写（因为本函数每次都会再覆盖一次，实现"永不覆盖"）。
        """
        corrections = self._load_user_corrections(person_id)
        if not corrections:
            return profile
        applied: list = []
        dealbreakers: list = []
        for c in corrections:
            field = c.get("field", "")
            if not field:
                continue
            ok = self._set_path_value(profile, field, c.get("new_value"))
            entry = {
                "field": field,
                "old_value": c.get("old_value"),
                "new_value": c.get("new_value"),
                "reason": c.get("reason", ""),
                "applied": ok,
            }
            if c.get("is_dealbreaker"):
                dealbreakers.append(entry)
            applied.append(entry)
        if applied:
            profile["user_corrections_applied"] = applied
        if dealbreakers:
            profile["dealbreakers"] = dealbreakers
        return profile

    # ================================================================== #
    # v3.1 新增能力②：连接类型分类
    #   五种：镜像型 / 互补型 / 成长型 / 对立型 / 共同旅程型。
    #   依据：依恋类型 + 价值观匹配度 + 情绪波动。
    # ================================================================== #
    def classify_connection_type(self, persona_result: dict) -> dict:
        """根据画像结果判定整体连接类型。

        Returns:
            dict: {"type", "label", "description", "score_breakdown"}
        """
        attachment = persona_result.get("attachment", {}) or {}
        # 向后兼容：旧画像 attachment 可能是裸字符串（如 "secure"/"回避型"），
        # 直接当 dict 取 .get 会 AttributeError。字符串包成 {"type": s}。
        if isinstance(attachment, str):
            attachment = {"type": attachment}
        values = persona_result.get("values_match", {}) or {}
        emotion = persona_result.get("emotion_pattern", {}) or {}
        investment_balance = persona_result.get("investment_balance", {}) or {}

        att_type = str(attachment.get("primary_type") or attachment.get("type") or "")
        value_score = float(values.get("score", 50) or 50)
        volatility = str(emotion.get("volatility") or "低")

        # 关键信号计数
        growth_signals = 0
        # 成长型：共同成长维度高 / 积极情绪主导 / 投入平衡健康
        if "growth" in str(persona_result.get("passion", "")):
            growth_signals += 1
        if emotion.get("pattern") == "积极主导":
            growth_signals += 1

        # 共同旅程型：价值观高分 + 未来规划契合信号
        shared_future = value_score >= 65 and ("结婚" in str(persona_result)
                                               or "未来" in str(persona_result)
                                               or "共同" in str(investment_balance))

        # 对立型：价值观差异大 / 情绪高波动 / 消极倾向 / 风险预警高
        opposing = (value_score < 40
                    or emotion.get("pattern") == "消极倾向"
                    or volatility == "高"
                    or (persona_result.get("risk_warning", {}) or {}).get("overall_level") == "高")

        # 镜像型：双方依恋都偏安全 / 节奏一致（简单近似：安全型 + 价值观不冲突）
        mirror = (att_type == "安全型" and value_score >= 55 and not opposing)

        # 互补型：一方回避一方焦虑（追逃），或差异化明显但无对立冲突
        complementary = (att_type in ("疏离型回避", "焦虑型") and not opposing
                         and value_score >= 45)

        # 优先级判定：对立 > 共同旅程 > 成长 > 互补 > 镜像
        if opposing:
            ctype = "对立型"
            desc = ("价值观冲突或情绪消耗明显，容易互相拉扯、频繁吵架；"
                    "先降温处理冲突，不要急着推进关系")
        elif shared_future:
            ctype = "共同旅程型"
            desc = ("对未来/家庭/人生方向有共识，想要的终点一致；"
                    "这是长期关系最稳的地基，可以一起规划下一步")
        elif growth_signals >= 1 and emotion.get("pattern") in ("积极主导", "偏积极"):
            ctype = "成长型"
            desc = ("和对方在一起后状态更积极、更想变好；"
                    "这段关系在推着你成长，珍惜这种正向牵引")
        elif complementary:
            ctype = "互补型"
            desc = ("性格互补、节奏错开，你内向他外向刚好补位；"
                    "注意别让互补变成追逃，主动给彼此台阶")
        elif mirror:
            ctype = "镜像型"
            desc = ("性格相似、节奏一致，彼此像照镜子一样合拍；"
                    "相似处让你们舒服，但也要警惕一起陷入同一个盲区")
        else:
            ctype = "镜像型"
            desc = ("目前互动节奏平稳、彼此不排斥，偏向相似相吸的舒服状态")

        return {
            "type": ctype,
            "label": {"镜像型": "我们很像", "互补型": "性格互补互相补位",
                      "成长型": "和他在一起我更积极", "对立型": "总是互相消耗",
                      "共同旅程型": "想要的未来一样"}.get(ctype, ""),
            "description": desc,
            "score_breakdown": {
                "attachment_type": att_type,
                "values_match_score": value_score,
                "emotion_volatility": volatility,
                "emotion_pattern": emotion.get("pattern", ""),
            },
        }

    # ================================================================== #
    # v3.1 新增能力③：问题重要程度加权
    #   关键事件（告白/吵架/承诺/分手边缘）权重更高。
    #   权重表：关键事件3.0 / 深度话题2.0 / 中等话题1.0 / 浅层话题0.3。
    # ================================================================== #
    def _weight_by_event_type(self, text: str) -> float:
        """根据聊天文本内容判定该条消息的事件权重。

        Returns:
            float: 权重值（0.3 / 1.0 / 2.0 / 3.0）。
        """
        if not text:
            return 1.0
        # 关键事件优先（权重最高）
        if any(kw in text for kw in self._KEY_EVENT_KW):
            return 3.0
        # 深度话题
        if any(kw in text for kw in self._DEEP_TOPIC_KW):
            return 2.0
        # 浅层话题（打招呼/晚安/表情包语气）
        shallow_hits = sum(1 for kw in self._SHALLOW_TOPIC_KW if kw in text)
        # 纯浅层且无实质内容
        if shallow_hits >= 2 and len(text) <= 12:
            return 0.3
        # 其余默认为中等话题
        return 1.0

    def _analyze_event_weights(self, messages: list) -> dict:
        """逐条消息计算事件权重，产出权重画像与关键事件清单。

        不改动既有维度分数（避免回归），作为附加 enrichment 挂到 profile。
        """
        if not messages:
            return {"distribution": {}, "key_events": [], "avg_weight": 1.0}
        weights: list[float] = []
        key_events: list[dict] = []
        for m in messages:
            content = m.get("content", "") or ""
            w = self._weight_by_event_type(content)
            weights.append(w)
            if w >= 3.0:
                key_events.append({
                    "content": content[:60],
                    "direction": m.get("direction", ""),
                    "at": str(m.get("created_at", "")),
                    "weight": w,
                })
        dist: dict = {}
        for w in weights:
            dist[str(w)] = dist.get(str(w), 0) + 1
        avg = round(sum(weights) / len(weights), 2) if weights else 1.0
        return {
            "distribution": dist,
            "key_events": key_events[:20],
            "key_event_count": len(key_events),
            "avg_weight": avg,
            "note": ("关键事件权重3.0/深度话题2.0/中等话题1.0/浅层话题0.3；"
                     "关键事件（告白/吵架/承诺/分手/见家长）对画像影响更大"),
        }


# ===== 借鉴 ex-skill：6层人格结构 =====
PERSONA_LAYERS = {
    0: "Core Rules 核心硬规则（永不违反，反复行为模式）",
    1: "Identity 身份特质（星座/MBTI/九型/依恋/OCEAN，含24维画像）",
    2: "Expression 表达风格（口头禅/常用emoji/回复速度习惯）",
    3: "Emotional Behavior 情感行为（爱的语言/关心/不满/道歉方式）",
    4: "Conflict & Boundaries 冲突与边界（升级链/冷战模式/和解信号/底线）",
    5: "Triggers 触发因素（讨厌/消失信号/回归方式/开心触发）",
}


def build_persona_6layer(person_id: str, chat_messages: list = None) -> dict:
    """v3.2.0 起废弃（Bug#1：死代码，另一套 0-5 层 scheme 与 Layer 枚举不一致）。

    统一走 services.profiling_chain.run_profiling_chain。这里保留一个 shim
    以防老调用方还在 import，内部直接委托给凭证链。
    """
    import warnings
    from services.profiling_chain import run_profiling_chain
    warnings.warn(
        "build_persona_6layer() 已废弃，请改用 run_profiling_chain()",
        DeprecationWarning, stacklevel=2)
    chain_msgs = []
    for m in (chat_messages or [])[:200]:
        if isinstance(m, dict):
            chain_msgs.append({"content": m.get("text") or m.get("content") or "",
                               "direction": m.get("direction", "")})
    res = run_profiling_chain(person_id, chain_msgs)
    return {
        "person_id": person_id,
        "layers": res.get("summary", {}).get("per_layer", {}),
        "unmet_layers": res.get("unmet_layers", []),
        "deprecated": True,
    }


# =====================================================================
# R3.3-79: 家庭背景线索自动提取（模块级，类外，独立函数）
# 原则：只做字面提取，绝不臆造/推断缺失信息；缺失一律标 "未知"，
#       冲突按投票规则（票多者胜，平票取最近命中者），不编造"可能/也许"。
# =====================================================================

# 家庭结构线索（投票维度：单亲 / 双亲 / 重组 / 独居）
FAMILY_STRUCTURE_KW = {
    "单亲": [
        "单亲", "我跟妈过", "我跟爸过", "我妈一个人", "我爸一个人",
        "从小没爸", "从小没妈", "没有爸爸", "没有妈妈", "父母离异",
    ],
    "双亲": [
        "我爸妈", "爸妈都在", "父母都在", "爸妈感情",
    ],
    "重组": [
        "继父", "继母", "后爸", "后妈", "重组家庭",
    ],
    "独居": [
        "我一个人住", "独居",
    ],
}

# 父母关系状态线索（投票维度：离婚中 / 离异 / 分居 / 和睦 / 紧张）
# 注："父母离异" 同时计入 结构=单亲 与 状态=离异。
PARENTS_STATUS_KW = {
    "离婚中": ["闹离婚", "正在离婚", "要离婚", "准备离婚", "离婚中"],
    "离异":   ["离婚了", "已经离了", "离了", "父母离异"],
    "分居":   ["分居", "各住各的", "分开住"],
    "和睦":   ["爸妈感情很好", "我爸妈很恩爱", "父母感情好", "爸妈很相爱", "关系特别好"],
    "紧张":   ["爸妈老吵架", "父母天天吵", "天天吵架", "关系不好", "我爸我妈吵架", "父母吵架"],
}

# 兄弟姐妹称谓（命中即登记去重列表；"姐姐"等裸词仅与"有个姐姐"短语组合，避免泛匹配）
SIBLING_PATTERNS = [
    ("我姐", "姐姐"), ("有个姐姐", "姐姐"),
    ("我哥", "哥哥"), ("有个哥哥", "哥哥"),
    ("我弟", "弟弟"), ("有个弟弟", "弟弟"),
    ("我妹", "妹妹"), ("有个妹妹", "妹妹"),
]

# 家庭氛围线索（可选，保守；无证据标 "未知"）
FAMILY_ATMOSPHERE_KW = {
    "温馨": ["家里很温馨", "家里氛围很好", "家庭氛围很好", "家里很温暖"],
    "压抑": ["家里很压抑", "家里氛围压抑", "家里气氛很压抑", "家里让人窒息"],
}

# 双亲共现判定用的字面父/母指称（不使用"没爸/没妈"等否定式）
_MOTHER_REF = ("我妈", "我妈妈")
_FATHER_REF = ("我爸", "我爸爸")


def _pick_family_winner(votes):
    """votes: {category: [count, last_index]} -> 票多者胜，平票取最近命中者。"""
    best = None
    for cat, (cnt, idx) in votes.items():
        if cnt <= 0:
            continue
        if best is None or (cnt, idx) > (votes[best][0], votes[best][1]):
            best = cat
    return best


def extract_family_context(chat_messages: list, manual: dict | None = None) -> dict:
    """从聊天记录字面提取家庭背景线索（R3.3-79）。

    Args:
        chat_messages: 消息列表，元素兼容 dict（取 m.get("content","")）或 str。
        manual: 手工录入覆盖，如 {"family_structure": "单亲", "parents_status": "离婚中"}；
                提供的键优先，未提供的键仍走字面提取/未知。

    Returns:
        {"family_structure", "parents_status", "siblings", "atmosphere",
         "evidence", "manual"}
    """
    manual = manual or {}
    manual_flag = bool(manual)

    # 结构票 / 状态票 / 氛围票：{类别: [count, last_index]}
    struct_votes = {k: [0, -1] for k in FAMILY_STRUCTURE_KW}
    status_votes = {k: [0, -1] for k in PARENTS_STATUS_KW}
    atmos_votes = {k: [0, -1] for k in FAMILY_ATMOSPHERE_KW}

    siblings = []
    evidence = []
    mother_seen = False
    father_seen = False

    def _add_evidence(text):
        if text and text not in evidence:
            evidence.append(text)
            if len(evidence) > 8:
                del evidence[8:]

    for idx, m in enumerate(chat_messages):
        text = m.get("content", "") if isinstance(m, dict) else (m or "")
        if not text:
            continue

        hit_this = False

        # 结构：显式短语投票
        for cat, kws in FAMILY_STRUCTURE_KW.items():
            if any(kw in text for kw in kws):
                struct_votes[cat][0] += 1
                struct_votes[cat][1] = idx
                hit_this = True

        # 状态：显式短语投票
        for cat, kws in PARENTS_STATUS_KW.items():
            if any(kw in text for kw in kws):
                status_votes[cat][0] += 1
                status_votes[cat][1] = idx
                hit_this = True
                # "父母离异" 同时计入 结构=单亲
                if cat == "离异" and "父母离异" in text:
                    struct_votes["单亲"][0] += 1
                    struct_votes["单亲"][1] = idx

        # 氛围
        for cat, kws in FAMILY_ATMOSPHERE_KW.items():
            if any(kw in text for kw in kws):
                atmos_votes[cat][0] += 1
                atmos_votes[cat][1] = idx
                hit_this = True

        # 兄弟姐妹
        for pat, label in SIBLING_PATTERNS:
            if pat in text and label not in siblings:
                siblings.append(label)
                hit_this = True

        # 双亲共现（字面同时提及母与父，证明双方都存在）
        if any(r in text for r in _MOTHER_REF):
            mother_seen = True
        if any(r in text for r in _FATHER_REF):
            father_seen = True

        if hit_this:
            _add_evidence(text)

    # 双亲共现票（全局聚合，计 1 票）
    if mother_seen and father_seen:
        struct_votes["双亲"][0] += 1
        struct_votes["双亲"][1] = 10 ** 9  # 视为最近命中，平票时优先

    family_structure = _pick_family_winner(struct_votes) or "未知"
    parents_status = _pick_family_winner(status_votes) or "未知"
    atmosphere = _pick_family_winner(atmos_votes) or "未知"

    # manual 覆盖：提供的键直接采用，未提供的键保留字面提取结果
    result = {
        "family_structure": family_structure,
        "parents_status": parents_status,
        "siblings": siblings,
        "atmosphere": atmosphere,
        "evidence": evidence,
        "manual": manual_flag,
    }
    for k in ("family_structure", "parents_status", "siblings", "atmosphere"):
        if k in manual and manual[k] is not None:
            result[k] = manual[k]
    return result

# =========================================================================== #
# A. 轨迹组件（全量历史聚合：情绪/活跃/延迟长期趋势、突变点、周期性）
# =========================================================================== #
def _trajectory_vector(messages: list) -> dict:
    """从全量 chat_messages 提取轨迹特征向量。

    输出：
    - emotion_trend: 情绪趋势（↑↓→）
    - activity_trend: 活跃趋势（↑↓→）
    - delay_trend: 延迟趋势（↑↓→）
    - mutation_points: 突变点列表（日期+类型）
    - periodicity: 周期性（每月低谷/深夜模式）
    """
    if not messages:
        return {
            "emotion_trend": "→",
            "activity_trend": "→",
            "delay_trend": "→",
            "mutation_points": [],
            "periodicity": "待计算",
            "note": "无消息数据",
        }

    # 按时间排序
    sorted_msgs = sorted(messages, key=lambda m: m.get("created_at", ""))
    n = len(sorted_msgs)

    # 分三段计算趋势
    third = max(1, n // 3)
    early = sorted_msgs[:third]
    mid = sorted_msgs[third:2*third]
    late = sorted_msgs[2*third:]

    def _avg_len(msgs):
        return sum(len(m.get("content", "")) for m in msgs) / max(1, len(msgs))

    # 情绪趋势：用消息长度变化近似（长文=情绪强，短文=平淡）
    early_len = _avg_len(early)
    late_len = _avg_len(late)
    if late_len > early_len * 1.15:
        emotion_trend = "↑"
    elif late_len < early_len * 0.85:
        emotion_trend = "↓"
    else:
        emotion_trend = "→"

    # 活跃趋势：消息数量变化
    early_count = len(early)
    late_count = len(late)
    if late_count > early_count * 1.2:
        activity_trend = "↑"
    elif late_count < early_count * 0.8:
        activity_trend = "↓"
    else:
        activity_trend = "→"

    # 延迟趋势：用回复间隔近似（received 消息之间的时间差）
    recv_msgs = [m for m in sorted_msgs if m.get("direction") == "received"]
    delay_trend = "→"
    if len(recv_msgs) >= 6:
        recv_sorted = sorted(recv_msgs, key=lambda m: m.get("created_at", ""))
        early_recv = recv_sorted[:len(recv_sorted)//2]
        late_recv = recv_sorted[len(recv_sorted)//2:]
        def _avg_gap(msgs):
            gaps = []
            for i in range(1, len(msgs)):
                try:
                    from datetime import datetime
                    t1 = datetime.fromisoformat(msgs[i-1].get("created_at", "").replace("Z", "+00:00"))
                    t2 = datetime.fromisoformat(msgs[i].get("created_at", "").replace("Z", "+00:00"))
                    gaps.append((t2 - t1).total_seconds() / 3600)  # 小时
                except Exception:
                    continue
            return sum(gaps) / max(1, len(gaps))
        early_gap = _avg_gap(early_recv)
        late_gap = _avg_gap(late_recv)
        if late_gap > early_gap * 1.3:
            delay_trend = "↑"  # 延迟变长=变冷
        elif late_gap < early_gap * 0.7:
            delay_trend = "↓"  # 延迟变短=变热
        else:
            delay_trend = "→"

    # 突变点：检测长度突变（前后差>50%）
    mutation_points = []
    for i in range(1, len(sorted_msgs)-1):
        prev_len = len(sorted_msgs[i-1].get("content", ""))
        curr_len = len(sorted_msgs[i].get("content", ""))
        next_len = len(sorted_msgs[i+1].get("content", ""))
        # 突然变长（情绪爆发）
        if curr_len > prev_len * 2 and curr_len > 50:
            mutation_points.append({
                "date": sorted_msgs[i].get("created_at", ""),
                "type": "情绪爆发",
                "length": curr_len,
            })
        # 突然变短（冷淡/回避）
        elif curr_len < prev_len * 0.5 and prev_len > 20:
            mutation_points.append({
                "date": sorted_msgs[i].get("created_at", ""),
                "type": "突然变短",
                "length": curr_len,
            })

    # 周期性：简化为深夜模式检测（22:00-02:00 的消息占比）
    night_count = 0
    for m in sorted_msgs:
        try:
            from datetime import datetime
            t = datetime.fromisoformat(m.get("created_at", "").replace("Z", "+00:00"))
            if t.hour >= 22 or t.hour <= 2:
                night_count += 1
        except Exception:
            continue
    night_ratio = night_count / max(1, n)
    if night_ratio > 0.3:
        periodicity = f"深夜模式（{night_ratio:.0%}消息在22:00-02:00）"
    else:
        periodicity = f"无明显周期性（深夜占比{night_ratio:.0%}）"

    return {
        "emotion_trend": emotion_trend,
        "activity_trend": activity_trend,
        "delay_trend": delay_trend,
        "mutation_points": mutation_points[:10],  # 最多10个
        "periodicity": periodicity,
        "note": f"全量{n}条消息聚合，分三段计算趋势",
    }
