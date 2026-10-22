# -*- coding: utf-8 -*-
"""
LoveAI v3.3 — 关系指标确定性计算层（外部算法借鉴落地 · 零 LLM）

本模块只做一件事：把聊天文本变成**可复算的确定性数值**。
全部为纯规则 + 纯算术，不调用任何大模型；同一输入必定得到同一输出，
任何人拿一个计算器就能逐条复现——这正是"自主计算算法"所要求的性质。

设计原则
  1. 零 LLM：本文件内不出现任何模型调用、不做语义猜测。
  2. 可复算：每个输出都能被逐步手算校验（见 __main__ 里的论文复现样例）。
  3. 可溯源：所有阈值与权值均来自外部公开研究，不自行拍数（见下）。
  4. 不造数：本模块**不产生**好感度总分，只产生"原料指标"，
     由调用方（affection_engine / affection_reasoning）决定如何并入既有模型。
  5. 顺序确定：所有匹配均为"从左到右、长词优先"的贪心匹配，
     词表排序键含次级键，杜绝 Python 集合无序带来的结果漂移。

======================================================================
借鉴来源（可溯源的外部公开研究，非本项目自造）
======================================================================

【来源 A】亲密度分量法 —— 中文样本
  《基于抖音共同联系人的群体用户关系分析》，
  《计算机研究与发展》2022, 59(4): 796-812。
  该文以抖音真实群体为对象，用关注/通信录/点赞/评论四类行为加权求亲密度，
  并首次把**评论文本的情感值**纳入关系强度计算。权重由 300 人问卷确定。

  本模块取其**文本侧**可复用的三条：
    A1. 称谓词 6 级亲密分值
          R1 亲属类（爸/妈/哥哥/妹妹/老公/老婆）           3.0
          R2 亲昵类（亲爱的/宝贝/宝宝/乖乖/honey/baby）     2.5
          R3 恋人戏称（小淘气/小可爱/小笨蛋/小傻瓜/小猪…）  2.0
          R4 单字昵称（小X / X儿 / 阿X / 老X）              1.5
          R5 兄姐弟妹（X哥 / X姐 / X弟 / X妹）              1.2
          R6 泛亲密称谓（老弟/哥们/老铁/美女/帅哥）         0.5
    A2. 程度副词 6 档调制权值（对情感词加权）
          over 2.0 ｜ most 1.75 ｜ very 1.5 ｜ more 1.25 ｜ ish 0.75 ｜ insufficiently 0.5
        多个程度副词同时修饰时，取**几何平均数**。
    A3. 否定词：出现在情感词之前，计数为奇数时极性取反。
    分句情感值：PS_i = q1(称谓) + q2(情感词 ±1) × (∏ω)^(1/m) × (-1)^n
    原论文词典规模：正面情感词 6094 / 负面 11445 / 否定词 58。
    *本模块内置词表为**种子集**（见常量区注释），按同一口径可持续扩充。*

【来源 B】正负互动比 5:1 —— Gottman 爱情实验室
  Gottman & Levenson（华盛顿大学）；Gottman & Murray 非线性联立方程模型
  （The Mathematics of Marriage: Dynamic Nonlinear Models）。
    B1. 稳定/幸福伴侣在冲突对话中的正负影响比 ≈ 5 : 1
    B2. 走向离异的高危伴侣 ≈ 1 : 1
    B3. 依 15 分钟冲突对话预测离异，准确率 94%：
        预测会散的全部应验，多数发生在 4 年内；若干"预测会维持但不幸福"的
        后来也离婚，因此该 94% 是**特定样本内的**准确率，不是普适常数。
    B4. 婚姻五型中仅三型长期稳定：validating / volatile / conflict-avoiding。

【来源 C】末日四骑士（Four Horsemen）—— Gottman 同上
  C1. 批评 criticism ｜ C2. 防卫 defensiveness ｜ C3. 蔑视 contempt ｜ C4. 冷战 stonewalling
  C5. 其中**蔑视**是预测关系解体最强的单一因子。
  *本模块的识别为词典级粗筛，用于给出警号，不替代人工判断。*

【来源 D】上下文窗口（Context Window）—— 行为判定的前提条件
  D1. arXiv:1908.00908（真实夫妻交互数据集）用神经网分类配偶行为，报告了一个
      与直觉相反的发现：**情感行为并不只出现在目标话语本身，而显现在其周围区域**。
      按固定长度截取"目标话语 + 前后文"再分类，**窗口长度 5 时纠正效果最佳**，
      明显优于逐条独立判定。
      → 直接的工程结论：**单条消息判不出行为，必须看窗口。**
  D2. 同文的词汇系统 / 声学系统对照：**词汇系统（文本）优于声学系统**。
      即文本路径的信息量足以支撑行为分类——这是本模块只做文本侧、不引入音频的依据。
  D3. 滚动趋势 + 转折点检测：借鉴 wechatanalyzer v3.0.0（本地优先聊天记录分析工具，
      329 项测试）的滚动窗口净分与转折点思路——用窗口均值序列的**局部极值**
      定位"关系在第几条发生了转向"，而不是只报一个总体均分。

======================================================================
诚实边界（不得夸大）
======================================================================
  · 来源 B 的 5:1、94% 来自**西方样本**的冲突对话观察，用于中文私聊文本属**跨情境移植**，
    只能作参照线，不能当判决线。
  · 来源 D 的窗口长度 5 来自**英文**夫妻交互数据的分类实验（声学/词汇混合场景），
    移植到中文微信文本**未经本项目样本标定**；5 是一个有出处的起点值，
    不是测出来的最优值。window 参数开放，可按实际数据重设。
  · 本模块输出是**指标**，不是"她还爱不爱我"的结论。
  · 词典法对反讽、玩梗、方言、缩写天然失效；低分不等于关系差，
    只等于"文本层没有正性信号"。任何单条指标都不足以支撑结论。
  · 与本项目已有引擎的关系：既有 affection_reasoning 走"关键词→姿态→内心状态→delta"，
    其强项是语境消解与证据链；本模块补的是**强度刻度（称谓分级）**、
    **词级调制（程度副词/否定）**与**关系级硬阈值（正负比/四骑士）**，
    两者是互补而非替代，可互为交叉验证——同一句消息两边算出的极性若相反，
    即自动标记为"待人工复核"。
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# ====================================================================== #
# A1. 称谓词 6 级词典（分值取自来源 A 表 3）
# ====================================================================== #
_APPELLATION_GROUPS: list[tuple[float, tuple[str, ...]]] = [
    # R1 亲属类称谓 —— 3.0
    (3.0, ("老公", "老婆", "媳妇", "媳妇儿", "老伴", "孩子他爸", "孩子他妈",
           "男朋友", "女朋友", "未婚夫", "未婚妻", "爱人", "对象")),
    # R2 亲昵类称谓 —— 2.5
    (2.5, ("亲爱的", "宝贝儿", "宝宝", "宝贝", "乖乖", "honey", "baby", "达令",
           "darling", "小宝贝", "宝")),
    # R3 恋人／亲密朋友戏称 —— 2.0
    (2.0, ("小淘气", "小可爱", "小笨蛋", "小傻瓜", "小坏蛋", "小懒虫", "小馋猫",
           "小甜心", "小奶狗", "小猫咪", "小妮", "小猪", "小猫", "大猪蹄子",
           "臭宝", "傻瓜", "笨蛋", "坏蛋")),
    # R6 其他亲密称谓 —— 0.5
    (0.5, ("老弟", "老哥", "哥们", "老铁", "美女", "帅哥", "兄弟", "姐妹",
           "集美", "亲")),
]

APPELLATION_FIXED: dict[str, float] = {}
for _score, _words in _APPELLATION_GROUPS:
    for _w in _words:
        # 同词多级时保留最高分（如"宝贝"可算 R2、也含于 R3 组）
        if APPELLATION_FIXED.get(_w, 0.0) < _score:
            APPELLATION_FIXED[_w] = _score
# 长词优先，保证"小宝贝"不被"宝"抢先命中
_APPELLATION_SORTED: list[tuple[str, float]] = sorted(
    APPELLATION_FIXED.items(), key=lambda kv: (-len(kv[0]), kv[0])
)

# R4：小X / 阿X / 老X / X儿  —— 1.5
# R5：X哥 / X姐 / X弟 / X妹  —— 1.2
_R_R4 = re.compile(r"[小阿老][\u4e00-\u9fa5]|[\u4e00-\u9fa5]儿")
_R_R5 = re.compile(r"[\u4e00-\u9fa5](?:哥|姐|弟|妹)")

# R4/R5 常见误命中（日常词，不构成昵称称谓）
_R45_STOPWORDS = frozenset({
    # "小/阿/老" 开头的生活词
    "老师", "老板", "老公", "老婆", "老铁", "老哥", "老乡", "老大", "老外",
    "老家", "老是", "老人", "老早", "老实", "老远", "老是", "老练",
    "小米", "小时", "小心", "小说", "小学", "小区", "小姐", "小费", "小号",
    "小气", "小看", "小心眼", "小伙伴", "小事", "小声", "小票", "小样",
    "阿姨", "阿里", "阿婆", "阿公",
    # "X儿" 结尾的生活词
    "点儿", "会儿", "事儿", "女儿", "婴儿", "幼儿", "少儿", "男儿", "健儿",
    "活儿", "劲儿", "味儿", "劲儿", "个儿", "份儿", "词儿", "这儿", "那儿",
    "哪儿", "样儿", "款儿", "调儿", "头儿", "面儿", "边儿", "底儿",
})

# R5 误命中："X哥/X姐" 若前字属以下，多为泛称而非亲密昵称（保留但降权由调用方决定）
_R5_OK = True


def appellation_score(token: str) -> float:
    """返回一个词的称谓亲密度分值；非称谓返回 0.0。

    优先级：固定词表 > 停用词 > 单字昵称结构。
    （"老公/老婆/老铁/老哥" 既像 "老X" 又是固定称谓，
      必须先按固定词表判为 3.0 / 0.5，不能被停用词表吞掉。）
    """
    if not token:
        return 0.0
    if token in APPELLATION_FIXED:
        return APPELLATION_FIXED[token]
    if token in _R45_STOPWORDS:
        return 0.0
    if _R_R4.fullmatch(token):
        return 1.5
    if _R_R5.fullmatch(token):
        return 1.2
    return 0.0


_MASK_CHAR = "\u0000"


def _appellation_spans(text: str) -> list[tuple[int, int, float]]:
    """返回文本中全部称谓命中的 (起点, 终点, 分值)；左到右贪心、区间互不重叠。

    固定词表按"长词优先"消费；R4 / R5 正则只在**未被固定词表占用**的区间上取。
    返回区间供两处共用：取最高分（q1）与挖空（避免情感词二次命中）。
    """
    if not text:
        return []
    n = len(text)
    consumed = [False] * n
    spans: list[tuple[int, int, float]] = []

    i = 0
    while i < n:
        for w, sc in _APPELLATION_SORTED:
            if text.startswith(w, i):
                spans.append((i, i + len(w), sc))
                for k in range(i, i + len(w)):
                    consumed[k] = True
                i += len(w)
                break
        else:
            i += 1

    for regex, score in ((_R_R4, 1.5), (_R_R5, 1.2)):
        for m in regex.finditer(text):
            if m.group(0) in _R45_STOPWORDS:
                continue
            a, b = m.span()
            if any(consumed[a:b]):
                continue
            spans.append((a, b, score))
            for k in range(a, b):
                consumed[k] = True

    return spans


def _max_appellation_in(text: str) -> float:
    """在一个分句中找最高分的称谓（来源 A：无称谓则 q1 取 0）。"""
    return max((s[2] for s in _appellation_spans(text)), default=0.0)


def _mask_appellations(text: str) -> str:
    """把文本中命中的称谓整体等长挖空（替换为 \\u0000，长度不变）。

    必要性：称谓词内部可能含情感词用字——"亲爱的"含"爱"、"小可爱"含"可爱"。
    若不挖空，这些字会被情感词表**二次命中**，使 PS 虚高
    （实测"亲爱的今天辛苦了"会从 3.5 涨到 4.5）。
    挖空不改变字符串长度，故挖空串上的下标与原串一一对应。
    """
    if not text:
        return text
    chars = list(text)
    for a, b, _sc in _appellation_spans(text):
        for k in range(a, b):
            chars[k] = _MASK_CHAR
    return "".join(chars)


# ====================================================================== #
# A2. 程度副词 6 档（权值取自来源 A 表 4）
# ====================================================================== #
# 说明：论文每档给出示例词；下列每档 = 论文原词 + 同档口语同义词。
#       绝不在档间移动论文原词——例如"真"依原文属 more 档 1.25，
#       不因口语习惯上调至 very 档。
DEGREE_ADVERB_WEIGHTS: dict[str, float] = {
    "over": 2.0,            # 论文原词：超 过度 过分 过甚 忒
    "most": 1.75,           # 论文原词：充分 极其 极为 十分 非常
    "very": 1.5,            # 论文原词：大为 多么 分外 很 实在
    "more": 1.25,           # 论文原词：更 较为 那么 愈发 真
    "ish": 0.75,            # 论文原词：略微 稍微 挺 有点儿 有些
    "insufficiently": 0.5,  # 论文原词：半点 不大 不甚 不怎么 微
}
# 标记：以下词为论文原词（严格口径），加 * 者为同档口语扩充
_DEGREE_GROUPS: dict[str, tuple[str, ...]] = {
    "over": ("超", "过度", "过分", "过甚", "忒", "太", "爆", "狂"),
    "most": ("充分", "极其", "极为", "十分", "非常", "特别", "超级", "格外", "尤其"),
    "very": ("大为", "多么", "分外", "很", "实在", "蛮", "相当", "好生"),
    "more": ("更", "较为", "那么", "愈发", "真", "更加", "越发", "越"),
    "ish": ("略微", "稍微", "挺", "有点儿", "有些", "有点", "还算", "比较"),
    "insufficiently": ("半点", "不甚", "微", "几乎不"),
}

# ---------------------------------------------------------------------- #
# 「否定 × 程度」复合标记
# ---------------------------------------------------------------------- #
# 中文里 "不太 / 不大 / 不怎么" 是一个整体：既减弱程度、又反转极性。
# 若拆成"独立否定词"与"独立程度副词"两遍扫描，会产生两类错：
#   ① 重复计数："不太" 的 "不" 既被当否定词、又出现在程度表；
#   ② 次序歧义："非常" 的 "非" 被误当否定词（已在 A3 剔除单字"非"）。
# 故整段消费：程度值沿用论文档位，另计 1 次否定。
#
# ⚠ 声明过的偏离：论文把 "不大" 列为 insufficiently 档程度副词；
#   本模块为保持极性正确，将 "不大/不太/不怎么" 统一按"否定+程度"处理，
#   程度值仍取论文的 0.5。这是**文中已声明、且可逐步复算**的偏离，
#   不是静默改动。
_NEG_DEGREE_COMPOSITES: dict[str, tuple[float, int]] = {
    "不太怎么": (0.5, 1),
    "不怎么": (0.5, 1),
    "不咋地": (0.5, 1),
    "不是很": (1.5, 1),
    "不太": (0.5, 1),
    "不大": (0.5, 1),
    "不很": (1.5, 1),
    "不咋": (0.5, 1),
}
_COMPOSITE_SORTED: list[str] = sorted(
    _NEG_DEGREE_COMPOSITES, key=lambda w: (-len(w), w)
)

if True:  # 构建唯一性映射（同一词若跨档出现，取权值更高的档）
    _tmp: dict[str, str] = {}
    for _rank in ("over", "most", "very", "more", "ish", "insufficiently"):
        for _w in _DEGREE_GROUPS[_rank]:
            _prev = _tmp.get(_w)
            if _prev is None or DEGREE_ADVERB_WEIGHTS[_rank] > DEGREE_ADVERB_WEIGHTS[_prev]:
                _tmp[_w] = _rank
    DEGREE_WORD_TO_RANK: dict[str, str] = _tmp

_DEGREE_SORTED: list[str] = sorted(DEGREE_WORD_TO_RANK, key=lambda w: (-len(w), w))


def _degree_weights_before(head: str) -> list[float]:
    """扫描情感词之前的文字，贪心最长、不重叠地取程度副词权值。"""
    ws: list[float] = []
    i = 0
    n = len(head)
    while i < n:
        for dw in _DEGREE_SORTED:
            if head.startswith(dw, i):
                ws.append(DEGREE_ADVERB_WEIGHTS[DEGREE_WORD_TO_RANK[dw]])
                i += len(dw)
                break
        else:
            i += 1
    return ws


# ====================================================================== #
# A3. 否定词（原文 58 个，此处为常用种子集）
# ====================================================================== #
NEGATION_WORDS: tuple[str, ...] = (
    "不是", "不要", "不同", "不用", "不会", "不能", "不想", "不要紧",
    "不好", "没有", "没什么", "没事", "没有什么", "难以", "拒绝", "否认",
    "从不", "绝不", "毫不", "根本不", "压根不", "一点也不", "并不",
    "不", "没", "无", "勿", "别", "莫", "未", "甭", "休",
)
# 说明：单字"非"不作否定词收录——现代汉语中"非"独立表否定已罕见（多为
# "非…不可"固定结构），却会大量误伤"非常/非法/非但"，故剔除。
_NEG_SORTED: list[str] = sorted(NEGATION_WORDS, key=lambda w: (-len(w), w))


def _count_negations(head: str) -> int:
    """扫描情感词之前的文字，贪心最长、不重叠地数否定词个数。

    注意：本函数**不**回避程度副词占用区间，仅用于需要单独查看否定次数的场景；
    计算分句情感值请用 _scan_modifiers（共享消费标记，避免重复计数）。
    """
    cnt = 0
    i = 0
    n = len(head)
    while i < n:
        for nw in _NEG_SORTED:
            if head.startswith(nw, i):
                cnt += 1
                i += len(nw)
                break
        else:
            i += 1
    return cnt


def _scan_modifiers(head: str) -> tuple[list[float], int]:
    """一次扫过情感词之前的文字，同时取出程度副词权值表与否定次数。

    匹配优先级（同一位置、同样长度时）：复合标记 > 程度副词 > 否定词；
    整体左到右贪心、字符区间互不重叠。这样保证不会出现
    "非常"的"非"被当否定词、"不太"的"不"被重复计数这类错。
    """
    weights: list[float] = []
    negs = 0
    i = 0
    n = len(head)
    while i < n:
        hit: Optional[tuple[str, str]] = None
        for w in _COMPOSITE_SORTED:
            if head.startswith(w, i):
                hit = ("comp", w)
                break
        if hit is None:
            for w in _DEGREE_SORTED:
                if head.startswith(w, i):
                    hit = ("deg", w)
                    break
        if hit is None:
            for w in _NEG_SORTED:
                if head.startswith(w, i):
                    hit = ("neg", w)
                    break
        if hit is None:
            i += 1
            continue
        kind, w = hit
        if kind == "comp":
            d, k = _NEG_DEGREE_COMPOSITES[w]
            weights.append(d)
            negs += k
        elif kind == "deg":
            weights.append(DEGREE_ADVERB_WEIGHTS[DEGREE_WORD_TO_RANK[w]])
        else:
            negs += 1
        i += len(w)
    return weights, negs


# ====================================================================== #
# 情感词种子集（口径同来源 A：正面 = +1，负面 = -1）
# ====================================================================== #
# 原论文规模：正面 6094 / 负面 11445（整合 NTUSD、HowNet、DUTIR、
# 清华李军褒贬义词典、BosonNLP 五源后筛选）。
# 下列为可负责维护的**种子集**，口径一致、可分批扩充；
# 规模不足以覆盖论文全量，因此词汇覆盖弱于原论文，但**算法结构与其一致**。
#
# 收录纪律：**不得收录以程度副词或否定词开头的合成词**
# （如"很好""挺好""不开心"），否则会与程度副词/否定词调制逻辑打架。
POSITIVE_WORDS: tuple[str, ...] = (
    # 情感/态度
    "高兴", "开心", "快乐", "幸福", "喜欢", "爱你", "爱", "中意", "满意",
    "舒心", "暖心", "感动", "惊喜", "兴奋", "期待", "想念", "想你", "思念",
    "心动", "甜蜜", "浪漫", "温柔", "体贴", "懂事", "宠", "偏爱",
    # 评价/赞许
    "漂亮", "好看", "美美的", "可爱", "帅", "厉害", "优秀", "不错", "赞",
    "牛", "绝了", "靠谱", "贴心", "周到", "细心", "用心", "认真", "上进",
    "有责任感", "顾家", "有品位",
    # 待遇/受益
    "谢谢", "谢谢你", "谢谢啦", "感谢", "谢啦", "辛苦", "辛苦啦",
    "麻烦你了", "麻烦你", "好在", "幸好", "得亏", "放心", "安心", "踏实",
    # 关系正向动作
    "答应", "同意", "支持", "陪你", "等你", "接你", "帮你", "给你",
    "加油", "保重", "注意身体", "照顾好自己", "早点休息", "多喝水",
    # 互动语气
    "哈哈", "哈哈哈", "嘻嘻", "嘿嘿", "么么", "抱抱", "亲亲", "比心",
    "好呀", "好哒", "好滴", "可以呀", "没问题", "来啦", "在呢", "在的",
)

NEGATIVE_WORDS: tuple[str, ...] = (
    # 情绪低落
    "难过", "伤心", "不开心", "失落", "沮丧", "郁闷", "心烦", "委屈",
    "心寒", "心碎", "绝望", "崩溃", "疲惫不堪", "疲惫", "累", "孤单",
    "孤独", "寂寞", "空虚", "焦虑", "压力大", "烦躁", "烦", "失望",
    "心疼", "扎心", "难受", "不舒服", "哭",
    # 愤怒/对抗
    "生气", "愤怒", "气死了", "气死", "火大", "讨厌", "烦人", "可恶",
    "过分", "混蛋", "滚", "闭嘴", "别烦我", "别管我", "关你屁事",
    "凭什么", "不在乎", "不管", "冷漠", "敷衍", "无所谓", "随便",
    # 关系负向动作
    "分手", "离婚", "拉黑", "删了", "别联系", "不理你", "冷战",
    "出轨", "背叛", "骗我", "撒谎", "隐瞒", "嫌弃", "看不上", "别找我",
    "懒得理你", "不想聊", "算了", "不必了", "不用了",
    # 评价负向
    "难看", "丑", "差劲", "垃圾", "没用", "废物", "幼稚", "自私",
    "小气", "抠门", "事儿多", "作", "暗", "黑", "糟", "糟糕", "无聊",
    "没意思", "冷淡", "冷淡了", "轻浮", "油腻", "土",
    # 身心状态负向
    "生病", "疼", "痛", "发烧", "头晕", "失眠", "emo",
)

_ALL_EMO_SORTED: list[tuple[str, float]] = sorted(
    [(w, 1.0) for w in POSITIVE_WORDS] + [(w, -1.0) for w in NEGATIVE_WORDS],
    key=lambda kv: (-len(kv[0]), kv[1], kv[0]),
)


def _find_emotion_hits(text: str) -> list[tuple[int, str, float]]:
    """从左到右、长词优先、不重叠地找出全部情感词。

    扫描前先挖空称谓区间（见 _mask_appellations），使"亲爱的"里的"爱"、
    "小可爱"里的"可爱"不再被二次计为情感词。
    返回的下标基于等长挖空串，与原串一一对应。
    """
    hits: list[tuple[int, str, float]] = []
    masked = _mask_appellations(text)
    i = 0
    n = len(masked)
    while i < n:
        for w, sc in _ALL_EMO_SORTED:
            if masked.startswith(w, i):
                hits.append((i, w, sc))
                i += len(w)
                break
        else:
            i += 1
    return hits


# ====================================================================== #
# C. 末日四骑士识别词典（来源 C）
# ====================================================================== #
HORSEMEN_LEXICON: dict[str, tuple[str, ...]] = {
    # C1 批评：针对"人"而非"事"的定性攻击（"你总是…""你就是这种人"）
    "criticism": (
        "你总是", "你从来", "你永远", "你就是", "你这个人", "你这种",
        "你根本", "你就是这样", "每次都", "老是这样", "本性难移",
        "没救了", "改不了", "你懂什么", "你算什么",
    ),
    # C2 防卫：把责任反推回去 / 拒绝承担
    "defensiveness": (
        "还不是因为你", "都怪你", "是你先", "是你先的", "你也有", "你才是",
        "我没问题", "关我什么事", "又不是我的错", "我有什么办法",
        "你自己想想", "怪我咯", "那你呢",
    ),
    # C3 蔑视：贬低、嘲讽、人身侮辱 —— Gottman 体系中最强预测因子
    "contempt": (
        "呵呵", "哦呵", "就你", "就你这样", "你也配", "你配", "笑死",
        "可笑", "丢人", "幼稚", "有病", "神经病", "脑子有", "蠢",
        "傻逼", "贱", "废物", "垃圾", "没出息", "窝囊", "看不上你",
        "瞧不起", "轻蔑", "阴阳怪气", "你还好意思", "脸都不要",
        "您可真行", "服了你了", "白眼", "🙄",
    ),
    # C4 冷战：中断沟通 / 拒绝回应
    "stonewalling": (
        "不想说", "没什么好说的", "懒得说", "爱怎样怎样", "随你便",
        "关你什么事", "别问了", "就这样吧", "我走了", "不理你",
        "挂电话", "别跟我说话", "静静", "让我一个人",
    ),
}

# 情绪极性边界（来源 A 口径：PS>0 记正面，PS<0 记负面，=0 记中性）
POLARITY_EPS = 1e-9

# Gottman 正负互动比的参照线（来源 B1 / B2）
GOTTMAN_REFERENCE: dict[str, float] = {
    "stable": 5.0,   # 稳定/幸福伴侣 ≈ 5:1
    "at_risk": 1.0,  # 高危伴侣 ≈ 1:1
}

# ====================================================================== #
# D. 上下文窗口（来源 D）
# ====================================================================== #
# 窗口长度 5 = 目标条 + 前后各 2 条。
# 取值出处：arXiv:1908.00908 的窗口评估，5 时纠正效果最佳。
# 该值**不是**在中文微信文本上标定出来的，只是有出处的起点值；
# 需要时由调用方显式传入 window 覆盖，本模块不做任何自适应猜测。
WINDOW_SIZE: int = 5

# 窗口判定的三种结论（沿用极化阈值 POLARITY_EPS，不另立阈值）
CTX_CONSISTENT = "consistent"        # 单条与其窗口同向（含窗口只有自己）
CTX_SOFTENS = "context_softens"      # 单条判负、窗口整体为正 → 该负向被上下文缓和
CTX_HARDENS = "context_hardens"      # 单条判正、窗口整体为负 → 该正向被上下文吞掉


# ====================================================================== #
# 分句与分句情感值
# ====================================================================== #
_CLAUSE_SPLIT = re.compile(r"[，。！？；、,.!?;\n\r\t~～…]+")


def split_clauses(text: str) -> list[str]:
    """按标点切段；段内若含多个情感词，再按情感词位置细分。

    切分口径同来源 A：目标是"每个分句最多包含一个情感词"，
    且**程度副词与否定词必须留在其修饰的情感词所属的那一段内**。
    因此只有段内存在两个以上情感词时才切，单情感词的分句原样保留。
    """
    if not text:
        return []
    out: list[str] = []
    for seg in _CLAUSE_SPLIT.split(text):
        seg = seg.strip()
        if not seg:
            continue
        out.extend(_split_seg_by_emotion(seg))
    return [c for c in out if c]


def _split_seg_by_emotion(seg: str) -> list[str]:
    hits = _find_emotion_hits(seg)
    if len(hits) <= 1:
        return [seg]
    out: list[str] = []
    # 第一段 = 段首到第二个情感词起点（含第一个情感词及其前缀修饰）
    out.append(seg[: hits[1][0]])
    for i in range(1, len(hits)):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(seg)
        out.append(seg[hits[i][0]: end])
    return out


def clause_sentiment(clause: str) -> dict[str, Any]:
    """单个分句的情感值 PS_i（来源 A 式 (2)）。

        PS_i = q1(称谓) + q2(情感词±1) × (∏ω)^(1/m) × (-1)^n
    """
    q1 = _max_appellation_in(clause)

    hits = _find_emotion_hits(clause)
    if hits:
        at, word, q2 = hits[0]
    else:
        at, word, q2 = -1, "", 0.0

    head = clause[:at] if at >= 0 else ""

    weights, n_neg = _scan_modifiers(head) if at >= 0 else ([], 0)
    if weights:
        prod = 1.0
        for w in weights:
            prod *= w
        deg = prod ** (1.0 / len(weights))
    else:
        deg = 1.0

    sign = -1.0 if (n_neg % 2 == 1) else 1.0

    ps = q1 + q2 * deg * sign
    return {
        "clause": clause,
        "appellation": round(q1, 3),
        "emotion_word": word,
        "emotion_score": q2,
        "degree_weights": weights,
        "degree_applied": round(deg, 4),
        "negations": n_neg,
        "polarity_flipped": bool(n_neg % 2 == 1),
        "ps": round(ps, 4),
    }


def message_sentiment(text: str) -> dict[str, Any]:
    """一条消息的情感值 PS（各分句 PS_i 之和，来源 A 式 (3)）。"""
    parts = [clause_sentiment(c) for c in split_clauses(text)]
    total = sum(p["ps"] for p in parts)
    if total > POLARITY_EPS:
        polarity = "positive"
    elif total < -POLARITY_EPS:
        polarity = "negative"
    else:
        polarity = "neutral"
    return {
        "text": text,
        "clauses": parts,
        "ps": round(total, 4),
        "polarity": polarity,
        "appellation_total": round(sum(p["appellation"] for p in parts), 3),
        "max_appellation": round(max([p["appellation"] for p in parts], default=0.0), 3),
    }


# ====================================================================== #
# B. 正负互动比（Gottman 参照线）
# ====================================================================== #
def pos_neg_ratio(scores: Iterable[float]) -> dict[str, Any]:
    """由逐条 PS 序列计算正负互动比。

    口径：
      · 正面回合 = PS > 0 的条数；负面回合 = PS < 0 的条数；PS = 0 计入中性，不参与比值。
      · ratio = 正面数 / 负面数（无负面时比值不定义，用 None 表示）。
      · 与 Gottman 参照线对照：≥5 → stable；≤1 → at_risk；其余 → middle。
    """
    vals = [float(x) for x in scores]
    pos = sum(1 for v in vals if v > POLARITY_EPS)
    neg = sum(1 for v in vals if v < -POLARITY_EPS)
    neu = len(vals) - pos - neg

    ratio: Optional[float] = None
    ratio_note = ""
    if neg == 0:
        ratio_note = "无负面回合，比值不定义"
    else:
        ratio = round(pos / neg, 3)

    if ratio is None:
        zone = "no_negative"
    elif ratio >= GOTTMAN_REFERENCE["stable"]:
        zone = "stable"
    elif ratio <= GOTTMAN_REFERENCE["at_risk"]:
        zone = "at_risk"
    else:
        zone = "middle"

    return {
        "positive": pos,
        "negative": neg,
        "neutral": neu,
        "total": len(vals),
        "ratio": ratio,
        "ratio_note": ratio_note,
        "gottman_zone": zone,
        "reference": dict(GOTTMAN_REFERENCE),
        "mean_ps": round(sum(vals) / len(vals), 4) if vals else 0.0,
    }


def horsemen(text: str) -> dict[str, Any]:
    """末日四骑士词典命中（来源 C）。返回命中计数与命中词。"""
    t = text or ""
    hits: dict[str, list[str]] = {}
    for kind, words in HORSEMEN_LEXICON.items():
        got = [w for w in words if w in t]
        if got:
            hits[kind] = got
    return {
        "criticism": len(hits.get("criticism", [])),
        "defensiveness": len(hits.get("defensiveness", [])),
        "contempt": len(hits.get("contempt", [])),
        "stonewalling": len(hits.get("stonewalling", [])),
        "hits": hits,
        "contempt_flag": bool(hits.get("contempt")),
        "note": "词典级粗筛，用于给出警号；蔑视为最强预警因子。",
    }


# ====================================================================== #
# 一站式聚合
# ====================================================================== #
def _scan_messages(messages: Iterable[dict]) -> tuple[list[dict], list[float], dict, float]:
    """逐条扫描 received 消息 —— analyze_sequence 与 window_scan 的唯一共用入口。

    口径（**两处调用方共用，不得各自另写一份**）：
      · 仅 direction == "received" 的消息计入对方侧；
      · 净极性 ps_adjusted：蔑视 / 批评命中且 ps >= 0 时强制转负。

    Returns:
        (detail, ps_adjusted 序列, 四骑士累计, 最高称谓分)
    """
    detail: list[dict] = []
    ps_list: list[float] = []
    horsemen_agg = {"criticism": 0, "defensiveness": 0, "contempt": 0, "stonewalling": 0}
    app_max = 0.0

    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("direction", "received") != "received":
            continue
        content = m.get("content", "") or ""
        s = message_sentiment(content)
        h = horsemen(content)

        # 净极性：蔑视 / 批评命中即判负。
        # 理由：词典情感法对"呵呵""就你这样"这类不产生情感词命中（PS=0），
        # 但它们在 Gottman 体系里是最强的负向信号，必须在正负比里体现，
        # 否则"高频蔑视"会被算成中性。
        ps_val = s["ps"]
        if (h.get("contempt") or h.get("criticism")) and ps_val >= 0:
            ps_val = -(ps_val if ps_val > 0 else 1.0)

        ps_list.append(ps_val)
        app_max = max(app_max, s["max_appellation"])
        for k in horsemen_agg:
            horsemen_agg[k] += h[k]
        detail.append({
            "content": content,
            "ps": s["ps"],
            "ps_adjusted": round(ps_val, 4),
            "polarity": ("positive" if ps_val > POLARITY_EPS
                         else "negative" if ps_val < -POLARITY_EPS else "neutral"),
            "polarity_raw": s["polarity"],
            "max_appellation": s["max_appellation"],
            "horsemen": {k: h[k] for k in horsemen_agg},
        })

    return detail, ps_list, horsemen_agg, app_max


def analyze_sequence(messages: Iterable[dict]) -> dict[str, Any]:
    """对一串消息做确定性关系指标计算。

    Args:
        messages: 每条为 dict，需含 "content"；可选 "direction"
                  （received = 对方发来 / sent = 我方发出）。
                  仅 received 计入对方侧指标，与既有引擎口径一致。
                  可为任意可迭代对象（内部先物化，故生成器亦可）。

    Returns:
        含逐条明细、正负比、四骑士汇总、称谓强度档位、上下文窗口的字典。
    """
    msgs = list(messages)
    detail, ps_list, horsemen_agg, app_max = _scan_messages(msgs)

    return {
        "detail": detail,
        "ratio": pos_neg_ratio(ps_list),
        "horsemen_total": horsemen_agg,
        "max_appellation_level": appellation_level_label(app_max),
        "message_count": len(ps_list),
        "engine": "relation_metrics.v1 (deterministic, no-LLM)",
        "window": _window_from_series(ps_list, WINDOW_SIZE),
    }


def appellation_level_label(score: float) -> str:
    if score >= 3.0:
        return "R1_亲属类(3.0)"
    if score >= 2.5:
        return "R2_亲昵类(2.5)"
    if score >= 2.0:
        return "R3_恋人戏称(2.0)"
    if score >= 1.5:
        return "R4_单字昵称(1.5)"
    if score >= 1.2:
        return "R5_兄姐弟妹(1.2)"
    if score >= 0.5:
        return "R6_泛亲密(0.5)"
    return "无称谓"


# ====================================================================== #
# D. 上下文窗口层（来源 D）—— 「单条判不出行为，要看周围」
# ====================================================================== #
def turning_points(series: Iterable[float],
                   eps: float = POLARITY_EPS) -> list[dict[str, Any]]:
    """在数值序列上定位**转折点**（局部极大 / 局部极小）。

    判据（纯差分，可手算）：
      对内部下标 i（1 <= i <= n-2），令 d_prev = s[i] - s[i-1]、d_next = s[i+1] - s[i]：
        · d_prev > eps  且 d_next < -eps  → peak（由升转降）
        · d_prev < -eps 且 d_next > eps   → valley（由降转升）
      首尾元素无两侧可比，不参与判定。

    出处：借鉴 wechatanalyzer v3.0.0 的滚动趋势 + 转折点检测思路（来源 D3）；
    eps 沿用全模块统一的极化阈值，不另立阈值。
    """
    vals = [float(x) for x in series]
    out: list[dict[str, Any]] = []
    for i in range(1, len(vals) - 1):
        d_prev = vals[i] - vals[i - 1]
        d_next = vals[i + 1] - vals[i]
        if d_prev > eps and d_next < -eps:
            kind = "peak"
        elif d_prev < -eps and d_next > eps:
            kind = "valley"
        else:
            continue
        out.append({
            "index": i,
            "kind": kind,
            "value": round(vals[i], 4),
            "delta_in": round(d_prev, 4),
            "delta_out": round(d_next, 4),
        })
    return out


def _window_from_series(ps_list: list[float],
                        window: int = WINDOW_SIZE) -> dict[str, Any]:
    """纯函数：由 ps_adjusted 序列算出全部窗口级结果。**不读消息、不做词匹配。**

    窗口定界（偶数窗口时右侧多取一条，左偏保守）：
        half_left = (window - 1) // 2 ｜ half_right = window // 2
        span = [max(0, i - half_left), min(n, i + half_right + 1) - 1]
    例：window=5 → 前后各 2；window=4 → 前 1 后 2。

    窗口判定（来源 D1 的直接用法）：
        单条判负 & 窗口净和为正 → context_softens（该负向被上下文缓和）
        单条判正 & 窗口净和为负 → context_hardens（该正向被上下文吞掉）
        其余（含 span 只含本条） → consistent
    """
    n = len(ps_list)
    w = int(window)
    if w < 1:
        w = 1
    half_l = (w - 1) // 2
    half_r = w // 2

    windows: list[dict[str, Any]] = []
    means: list[float] = []

    for i in range(n):
        lo = max(0, i - half_l)
        hi = min(n, i + half_r + 1)
        seg = ps_list[lo:hi]
        center = ps_list[i]
        ssum = sum(seg)
        mean = (ssum / len(seg)) if seg else 0.0
        means.append(mean)
        pos = sum(1 for v in seg if v > POLARITY_EPS)
        neg = sum(1 for v in seg if v < -POLARITY_EPS)
        neu = len(seg) - pos - neg

        if center < -POLARITY_EPS and ssum > POLARITY_EPS:
            verdict = CTX_SOFTENS
            note = "该条为负、但所在窗口净和为正：单条不宜作判据"
        elif center > POLARITY_EPS and ssum < -POLARITY_EPS:
            verdict = CTX_HARDENS
            note = "该条为正、但所在窗口净和为负：单条不宜作判据"
        else:
            verdict = CTX_CONSISTENT
            note = "单条与窗口同向" if len(seg) > 1 else "窗口只含本条，无上下文可用"

        windows.append({
            "index": i,
            "span": [lo, hi - 1],
            "span_len": len(seg),
            "ps_center": round(center, 4),
            "ps_window_sum": round(ssum, 4),
            "ps_window_mean": round(mean, 4),
            "positive": pos,
            "negative": neg,
            "neutral": neu,
            "verdict": verdict,
            "note": note,
        })

    shifts = [wd for wd in windows if wd["verdict"] != CTX_CONSISTENT]

    return {
        "window_size": w,
        "half_left": half_l,
        "half_right": half_r,
        "message_count": n,
        "windows": windows,
        "net_series": [round(x, 4) for x in means],
        "turning_points": turning_points(means),
        "context_shift_count": len(shifts),
        "context_shifts": [
            {
                "index": s["index"],
                "span": s["span"],
                "ps_center": s["ps_center"],
                "ps_window_sum": s["ps_window_sum"],
                "verdict": s["verdict"],
            }
            for s in shifts
        ],
        "rule": "单条消息判不出行为，用窗口的上下文修正（来源 D1）",
    }


def window_scan(messages: Iterable[dict],
                window: int = WINDOW_SIZE) -> dict[str, Any]:
    """对外入口：对一串消息做**上下文窗口**判定。

    与 analyze_sequence 共用 _scan_messages，因此两者的逐条 ps_adjusted
    逐项相等——不存在两套口径漂移的可能（见验收 G 组）。

    Args:
        messages: 同 analyze_sequence。
        window:   窗口长度，默认 WINDOW_SIZE(=5)；须为 >=1 的整数。
    """
    msgs = list(messages)
    detail, ps_list, _h, _a = _scan_messages(msgs)
    out = _window_from_series(ps_list, window)
    out["contents"] = [d["content"] for d in detail]
    out["engine"] = "relation_metrics.window (deterministic, no-LLM)"
    return out


# ====================================================================== #
# 自检：复现来源 A 论文原文算例
# ====================================================================== #
if __name__ == "__main__":
    print("=" * 70)
    print("【自检 1】复现《计算机研究与发展》2022 论文原文算例")
    print('原文评论："玲儿今天穿的真漂亮，视频拍得很好看，就是光线有点儿暗。"')
    print("论文值：PS1=2.75（玲儿1.5 + 漂亮1×真1.25）｜PS2=1.50（好看1×很1.5）")
    print("        PS3=-0.75（暗-1×有点儿0.75）｜合计 3.50")
    print("=" * 70)
    demo = message_sentiment("玲儿今天穿的真漂亮，视频拍得很好看，就是光线有点儿暗。")
    for c in demo["clauses"]:
        print(f'  分句={c["clause"]!r:<24} 称谓={c["appellation"]:<5} '
              f'情感词={c["emotion_word"] or "—":<6} q2={c["emotion_score"]:+.0f} '
              f'程度={c["degree_applied"]}({c["degree_weights"]}) '
              f'否定={c["negations"]} → PS={c["ps"]}')
    print(f'  ▶ 合计 PS = {demo["ps"]}   论文应为 3.5 → '
          f'{"✅ 一致" if abs(demo["ps"] - 3.5) < 1e-6 else "❌ 不一致"}')
    print()

    print("=" * 70)
    print("【自检 2】称谓分级")
    print("=" * 70)
    for t in ("老公", "亲爱的", "小傻瓜", "玲儿", "阿强", "小明", "王姐", "老铁",
              "老师", "点儿", "小米"):
        print(f'  {t:<6} → {appellation_score(t):<4}  {appellation_level_label(appellation_score(t))}')
    print()

    print("=" * 70)
    print("【自检 3】程度副词与否定调制")
    print("=" * 70)
    for t in ("非常开心", "有点开心", "稍微开心", "不开心", "不太好看",
              "不是很开心", "超喜欢"):
        s = message_sentiment(t)
        c = s["clauses"][0]
        print(f'  {t:<8} → PS={s["ps"]:>7}  程度={c["degree_applied"]} '
              f'否定={c["negations"]} 极性={s["polarity"]}')
    print()

    print("=" * 70)
    print("【自检 4】正负互动比（Gottman 参照线 5:1 / 1:1）")
    print("=" * 70)
    r = analyze_sequence([
        {"content": "宝宝今天辛苦了，早点休息", "direction": "received"},
        {"content": "谢谢你记得，我好开心", "direction": "received"},
        {"content": "哈哈你真好", "direction": "received"},
        {"content": "嗯", "direction": "received"},
        {"content": "呵呵，就你这样还想管我", "direction": "received"},
    ])
    print(f'  正面 {r["ratio"]["positive"]} / 负面 {r["ratio"]["negative"]} '
          f'/ 中性 {r["ratio"]["neutral"]}')
    print(f'  ratio = {r["ratio"]["ratio"]} → 区间 = {r["ratio"]["gottman_zone"]}'
          f'（参照线 stable≥5 / at_risk≤1）')
    print(f'  四骑士累计：{r["horsemen_total"]}')
    print(f'  称谓最高档：{r["max_appellation_level"]}')
    print()

    print("=" * 70)
    print("【自检 5】确定性（同输入连跑 200 次必须同一结果）")
    print("=" * 70)
    seq = [{"content": "亲爱的你真好，谢谢你", "direction": "received"},
           {"content": "有点生气，呵呵", "direction": "received"}]
    sig = {analyze_sequence(seq)["ratio"]["ratio"] for _ in range(200)}
    print(f'  200 次运行的 ratio 取值集合 = {sig} → '
          f'{"✅ 确定" if len(sig) == 1 else "❌ 不稳定"}')
    print()

    print("=" * 70)
    print("【自检 6】上下文窗口（来源 D：单条判不出行为，窗口 5 纠正更好）")
    print("=" * 70)
    wseq = [
        {"content": "亲爱的今天辛苦了", "direction": "received"},
        {"content": "谢谢你记得，我好开心", "direction": "received"},
        {"content": "嗯", "direction": "received"},
        {"content": "有点生气", "direction": "received"},
        {"content": "那就这样吧，别联系了", "direction": "received"},
    ]
    ws = window_scan(wseq)
    for w in ws["windows"]:
        print(f'  #{w["index"]} span={str(w["span"]):<9} 本条={w["ps_center"]:>7} '
              f'窗口和={w["ps_window_sum"]:>6} 均值={w["ps_window_mean"]:>7} '
              f'→ {w["verdict"]}')
    print(f'  上下文修正条数 = {ws["context_shift_count"]}')
    for s in ws["context_shifts"]:
        print(f'    · #{s["index"]} span={s["span"]} 本条={s["ps_center"]} '
              f'窗口和={s["ps_window_sum"]} → {s["verdict"]}')
    print(f'  窗口均值序列 = {ws["net_series"]}')
    print(f'  转折点 = {ws["turning_points"]}')
    print()
    print('  ▸ 释义：#3"有点生气"单条判负，但它所在窗口整体仍为正，'
          '故标记 context_softens —— 单条不足以作判据。')
