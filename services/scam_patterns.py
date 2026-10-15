# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - R3.3-33 六种杀猪盘模式枚举 + 时序链检测

设计原则（与 deception_detector 保持一致）：
  1. 不做有罪推定：命中信号 <3 个时 confidence<0.3，并明确标注"数据不足"，
     绝不输出"对方是骗子"之类的定性结论。
  2. 只报"信号聚集"，不报"人"：advice 用"出现 N 个可疑信号，建议进一步观察"，
     不使用"骗子/杀猪盘"等标签直接扣到对方头上。
  3. 时序链是参考模板，不是铁律：真实对话可能跳阶段、并行多阶段，
     本模块只做"当前最像哪条链的哪一段"的概率提示。

六种模式：
  INVESTMENT_SCAM  投资诈骗：荐股 / 虚拟币 / 外汇
  ROMANCE_SCAM     网恋杀猪盘：先建立感情再要钱
  GAMBLING_SCAM    博彩 / 彩票外挂
  PIG_BUTCHERING   杀猪盘通用：长期养号后大额收割
  BIRD_KILLING     杀鸟盘：兼职刷单 / 点赞先小返后大吞
  FISH_KILLING     杀鱼盘：冒充客服 / 公检法 / 退款诈骗

纯 Python 服务层，不依赖 PySide6 / 数据库；db 可为 None。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 枚举
# --------------------------------------------------------------------------- #
class ScamPatternType(str, Enum):
    """六种诈骗模式枚举。继承 str 便于 JSON 序列化与日志打印。"""

    INVESTMENT_SCAM = "investment_scam"      # 投资诈骗：荐股/虚拟币/外汇
    ROMANCE_SCAM = "romance_scam"            # 网恋杀猪盘：先感情后要钱
    GAMBLING_SCAM = "gambling_scam"          # 博彩/彩票外挂
    PIG_BUTCHERING = "pig_butchering"         # 杀猪盘通用：长期养号后大额收割
    BIRD_KILLING = "bird_killing"             # 杀鸟盘：刷单/点赞先小返后大吞
    FISH_KILLING = "fish_killing"             # 杀鱼盘：冒充客服/公检法/退款


# --------------------------------------------------------------------------- #
# 时序链定义
# --------------------------------------------------------------------------- #
# 每条链是 list[dict]，每个 dict：
#   stage       : 阶段名（str）
#   keywords    : 典型话术/行为信号关键词（list[str]）
#   window_days : (lo, hi) 典型时间窗（天），仅作参考
#   risk_flag   : 该阶段的风险升级标志（str，人读）
#   weight      : 该阶段命中在总分中的权重（越靠后越重，默认 1.0）

SCAM_CHAINS: dict[ScamPatternType, list[dict[str, Any]]] = {
    # --- 网恋杀猪盘：先建立感情，再引入项目，最后收割 ---
    ScamPatternType.ROMANCE_SCAM: [
        {
            "stage": "stage1_建立信任",
            "keywords": ["早安", "晚安", "我想你", "我们好近", "在干嘛", "吃饭了吗",
                         "心疼你", "宝贝", "亲爱的", "早安呀"],
            "window_days": (1, 14),
            "risk_flag": "高频情绪问候，超出普通网友密度",
            "weight": 1.0,
        },
        {
            "stage": "stage2_情感升温",
            "keywords": ["未来", "规划", "我只有你", "非你不可", "以后我们",
                         "想和你有个家", "离不开你", "你是我的唯一"],
            "window_days": (14, 30),
            "risk_flag": "关系未见面即做重大未来承诺",
            "weight": 1.2,
        },
        {
            "stage": "stage3_引入项目",
            "keywords": ["我最近在做", "你要不要试试", "稳赚", "带我一起",
                         "内部渠道", "跟着我操作", "我带你", "低风险高收益"],
            "window_days": (30, 45),
            "risk_flag": "情感升温后首次出现投资/赚钱话题",
            "weight": 1.5,
        },
        {
            "stage": "stage4_收割",
            "keywords": ["再投一笔", "出金要缴税", "系统维护", "账户被冻结",
                         "保证金", "验资", "再充一点就能提现", "额度不够"],
            "window_days": (45, 60),
            "risk_flag": "以各种理由阻止出金，要求追加投入",
            "weight": 2.0,
        },
    ],

    # --- 投资诈骗：直接荐股/虚拟币/外汇，少走感情路线 ---
    ScamPatternType.INVESTMENT_SCAM: [
        {
            "stage": "stage1_建立专业人设",
            "keywords": ["操盘手", "分析师", "内部消息", "庄家", "建仓", "加仓",
                         "我老师", "我师父", "私募", "涨停板"],
            "window_days": (1, 10),
            "risk_flag": "主动展示金融专业身份与收益截图",
            "weight": 1.0,
        },
        {
            "stage": "stage2_晒收益诱导",
            "keywords": ["今天又赚了", "翻了倍", "收益截图", "日入", "月入",
                         "跟着我买", "这个币要拉", "内部群"],
            "window_days": (10, 25),
            "risk_flag": "高频晒盈利，诱导对方跟风",
            "weight": 1.3,
        },
        {
            "stage": "stage3_引导入金",
            "keywords": ["下载这个APP", "注册这个平台", "我帮你开户",
                         "先充一点试试", "入金", "USDT", "数字货币",
                         "外汇平台", "荐股群"],
            "window_days": (25, 40),
            "risk_flag": "引导到非官方平台/APP入金",
            "weight": 1.6,
        },
        {
            "stage": "stage4_收割",
            "keywords": ["再补仓", "爆仓了", "需要保证金", "提现要交税",
                         "通道费", "账户异常", "需要验资才能出金"],
            "window_days": (40, 60),
            "risk_flag": "阻止出金 / 要求追加资金",
            "weight": 2.0,
        },
    ],

    # --- 博彩/彩票外挂 ---
    ScamPatternType.GAMBLING_SCAM: [
        {
            "stage": "stage1_诱导下注",
            "keywords": ["彩票", "六合彩", "时时彩", "大小单双", "赔率",
                         "下注", "下注网站", "博彩"],
            "window_days": (1, 10),
            "risk_flag": "主动提及博彩/彩票平台",
            "weight": 1.0,
        },
        {
            "stage": "stage2_晒中奖",
            "keywords": ["中了", "又中了", "稳赚不赔", "跟着我下注",
                         "这个平台靠谱", "我有内部计划"],
            "window_days": (10, 20),
            "risk_flag": "高频晒中奖截图，制造暴富幻觉",
            "weight": 1.3,
        },
        {
            "stage": "stage3_收割",
            "keywords": ["充值", "上分", "提现失败", "需要流水",
                         "再打几局才能提现", "账户冻结"],
            "window_days": (20, 40),
            "risk_flag": "以流水/冻结为由阻止提现",
            "weight": 2.0,
        },
    ],

    # --- 杀猪盘通用：长期养号后大额收割（跨模式兜底） ---
    ScamPatternType.PIG_BUTCHERING: [
        {
            "stage": "stage1_养号",
            "keywords": ["早安", "晚安", "关心你", "在干嘛", "多喝热水",
                         "天冷加衣", "注意休息"],
            "window_days": (1, 30),
            "risk_flag": "长期高频情绪价值输出，无明确索取",
            "weight": 1.0,
        },
        {
            "stage": "stage2_引入赚钱机会",
            "keywords": ["我在做一个项目", "带你赚钱", "稳赚", "内部渠道",
                         "低风险", "高收益"],
            "window_days": (30, 60),
            "risk_flag": "养号后首次出现金钱/项目话题",
            "weight": 1.5,
        },
        {
            "stage": "stage3_大额收割",
            "keywords": ["再投一笔", "出金要缴税", "系统维护", "账户冻结",
                         "保证金", "验资", "额度不够"],
            "window_days": (60, 90),
            "risk_flag": "以各种理由阻止出金，要求追加投入",
            "weight": 2.0,
        },
    ],

    # --- 杀鸟盘：兼职刷单/点赞先小返后大吞 ---
    ScamPatternType.BIRD_KILLING: [
        {
            "stage": "stage1_轻量兼职",
            "keywords": ["刷单", "点赞", "关注公众号", "抖音关注", "小红书点赞",
                         "兼职", "日结", "一单几块"],
            "window_days": (1, 7),
            "risk_flag": "以低门槛小兼职吸引入局",
            "weight": 1.0,
        },
        {
            "stage": "stage2_小返利建立信任",
            "keywords": ["已返款", "提现到账", "佣金已打", "再来一单",
                         "连单", "组合单"],
            "window_days": (7, 14),
            "risk_flag": "前期小额返利正常，培养提现习惯",
            "weight": 1.2,
        },
        {
            "stage": "stage3_大额吞没",
            "keywords": ["连单任务", "需要垫资", "订单未完成", "卡单",
                         "需要补单", "再充值才能返款", "任务超时"],
            "window_days": (14, 30),
            "risk_flag": "以连单/卡单为由要求持续垫资",
            "weight": 2.0,
        },
    ],

    # --- 杀鱼盘：冒充客服/公检法/退款诈骗 ---
    ScamPatternType.FISH_KILLING: [
        {
            "stage": "stage1_冒充身份",
            "keywords": ["客服", "工号", "我是XX平台客服", "公安局", "检察院",
                         "反诈中心", "网警", "你涉嫌"],
            "window_days": (1, 3),
            "risk_flag": "主动来电/私信冒充官方机构",
            "weight": 1.0,
        },
        {
            "stage": "stage2_制造恐慌/利益",
            "keywords": ["你涉嫌洗钱", "你的账户涉案", "需要配合调查",
                         "快递理赔", "订单异常", "商品质量问题退款",
                         "多打了钱要退回"],
            "window_days": (1, 5),
            "risk_flag": "用恐慌或利益诱导配合操作",
            "weight": 1.5,
        },
        {
            "stage": "stage3_转账/屏幕共享",
            "keywords": ["屏幕共享", "下载这个会议软件", "把钱转到安全账户",
                         "验证你的银行卡", "告诉我验证码", "我帮你操作"],
            "window_days": (1, 7),
            "risk_flag": "要求屏幕共享 / 转账 / 交出验证码",
            "weight": 2.0,
        },
    ],
}


# --------------------------------------------------------------------------- #
# 报告结构
# --------------------------------------------------------------------------- #
@dataclass
class ScamReport:
    """杀猪盘模式检测报告。"""

    pattern: Optional[str] = None          # 命中的模式名（str(ScamPatternType)）
    stage: Optional[str] = None            # 推断出的当前阶段
    confidence: float = 0.0                # 0-1
    evidence: list[str] = field(default_factory=list)   # 命中的原话
    advice: str = ""                       # 谨慎措辞的建议
    data_insufficient: bool = False        # 信号不足标记
    scores: dict[str, float] = field(default_factory=dict)  # 各模式得分（调试用）

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "stage": self.stage,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
            "advice": self.advice,
            "data_insufficient": self.data_insufficient,
            "scores": self.scores,
        }


# --------------------------------------------------------------------------- #
# 主检测函数
# --------------------------------------------------------------------------- #
#: 信号数 < 该阈值时视为"数据不足"
MIN_SIGNALS_FOR_CONFIDENT: int = 3


def _count_hits(text: str, keywords: list[str]) -> list[str]:
    """返回 text 中实际命中的关键词列表。"""
    hits: list[str] = []
    for kw in keywords:
        if kw and kw in text:
            hits.append(kw)
    return hits


def detect_scam_stage(chat_history: list[str]) -> ScamReport:
    """根据聊天记录推断最像的诈骗模式与当前阶段。

    Args:
        chat_history: 逐条聊天记录（str 列表），不区分说话人。

    Returns:
        ScamReport：含 pattern / stage / confidence / evidence / advice。
        命中信号 <3 个时 confidence<0.3 并标 data_insufficient=True。
    """
    report = ScamReport()

    if not chat_history:
        report.advice = "无聊天记录，无法评估。"
        report.data_insufficient = True
        return report

    joined = "\n".join(chat_history)

    # --- 对每种模式打分 ---
    pattern_scores: dict[ScamPatternType, float] = {}
    pattern_stage_hits: dict[ScamPatternType, list[tuple[str, int, list[str]]]] = {}
    all_evidence: list[str] = []

    for ptype, chain in SCAM_CHAINS.items():
        score = 0.0
        stage_stats: list[tuple[str, int, list[str]]] = []  # (stage, hit_count, kw_hits)
        for stage_def in chain:
            kws = stage_def["keywords"]
            weight = float(stage_def.get("weight", 1.0))
            kw_hits = _count_hits(joined, kws)
            n = len(kw_hits)
            stage_stats.append((stage_def["stage"], n, kw_hits))
            score += n * weight
        pattern_scores[ptype] = score
        pattern_stage_hits[ptype] = stage_stats

    # 归一化到 0-100（以最高分作为满分基准）
    max_score = max(pattern_scores.values()) if pattern_scores else 0.0
    if max_score > 0:
        scores_pct = {
            ptype.value: round(score / max_score * 100, 1)
            for ptype, score in pattern_scores.items()
        }
    else:
        scores_pct = {ptype.value: 0.0 for ptype in pattern_scores}
    report.scores = scores_pct

    # --- 取 top1 模式 ---
    top_pattern = max(pattern_scores, key=lambda k: pattern_scores[k])
    top_raw = pattern_scores[top_pattern]

    # 收集 top1 模式命中的原话
    for msg in chat_history:
        for stage_def in SCAM_CHAINS[top_pattern]:
            for kw in stage_def["keywords"]:
                if kw and kw in msg:
                    if msg not in all_evidence:
                        all_evidence.append(msg)
                    break

    report.evidence = all_evidence[:8]  # 最多带 8 条原话

    # --- 数据不足判定 ---
    total_hits = int(round(top_raw))
    if total_hits < MIN_SIGNALS_FOR_CONFIDENT:
        report.pattern = top_pattern.value
        report.stage = None
        report.confidence = min(0.29, 0.1 * total_hits)
        report.data_insufficient = True
        report.advice = (
            f"仅检测到 {total_hits} 个弱信号（阈值 {MIN_SIGNALS_FOR_CONFIDENT}），"
            f"数据不足，暂无法判定为 {top_pattern.value}，建议继续观察。"
        )
        return report

    # --- 推断当前阶段：取该模式下加权命中数最多的阶段 ---
    # 后期阶段权重更高（收割阶段比建立信任更严重），用 n*weight 做选择
    stage_stats = pattern_stage_hits[top_pattern]
    best_stage = max(stage_stats, key=lambda x: x[1] * next(
        sd.get("weight", 1.0) for sd in SCAM_CHAINS[top_pattern] if sd["stage"] == x[0]
    ))
    report.pattern = top_pattern.value
    report.stage = best_stage[0]

    # --- confidence：随命中数递增，封顶 0.92 ---
    report.confidence = min(0.92, 0.3 + 0.12 * total_hits)

    # --- 谨慎措辞 advice ---
    # 找到该阶段的 risk_flag
    risk_flag = ""
    for sd in SCAM_CHAINS[top_pattern]:
        if sd["stage"] == best_stage[0]:
            risk_flag = sd.get("risk_flag", "")
            break
    report.advice = (
        f"对话中聚集了 {total_hits} 个疑似「{top_pattern.value}」信号，"
        f"当前最像处于「{best_stage[0]}」阶段（{risk_flag}）。"
        f"这只是信号聚集提示，不代表对方一定有恶意；"
        f"建议：暂不转账、不下载对方指定的非官方 APP、"
        f"对涉及金钱/出金/保证金的要求一律暂缓并线下核实。"
    )
    logger.info(
        "detect_scam_stage: pattern=%s stage=%s confidence=%.2f hits=%d",
        report.pattern, report.stage, report.confidence, total_hits,
    )
    return report


# --------------------------------------------------------------------------- #
# 便捷导出
# --------------------------------------------------------------------------- #
def detect_scam_stage_dict(chat_history: list[str]) -> dict[str, Any]:
    """detect_scam_stage 的 dict 版本，便于直接 JSON 序列化。"""
    return detect_scam_stage(chat_history).to_dict()


__all__ = [
    "ScamPatternType",
    "SCAM_CHAINS",
    "ScamReport",
    "detect_scam_stage",
    "detect_scam_stage_dict",
    "MIN_SIGNALS_FOR_CONFIDENT",
]
