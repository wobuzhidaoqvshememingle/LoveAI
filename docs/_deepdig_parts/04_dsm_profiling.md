# 04 · DSM（Deep Self Mirror）侧写引擎 + 标签体系 深度扒取

> 扒取时间：2026-10-09。本文全部结论基于**真实源码逐行阅读**（git clone 到本地 `docs/_deepdig_parts/_dsm_src/`，--depth 1，main 分支 v0.6.2-web）。确证（源码里读到的）与推测（我的推断）分开标注。

---

## 一、仓库定位

- **真实仓库**：https://gitlab.com/cognitive-functors/dsm
- **许可证**：**AGPL-3.0** + 附加《Ethical Provisions》（见 `LICENSE`、`ETHICAL_PROVISIONS.md`）。
  - ⚠️ 对 LoveAI 是**强 copyleft**：若直接 copy 代码进我们的闭源/桌面分发版，AGPL 会要求整套 LoveAI 开源。**结论：只抄「算法思想 + 数据结构 + prompt 写法」，不要抄代码字面量。**
- **作者/引用**：Selyutin I.G., Kovalev N.I. (2026). *Deep Self Mirror (DSM): A Z₃³ Multi-Layer Psycho-Cognitive Profiling System from Natural Language.*
- **配套模型**：C4-Cognitive-Classifier（ONNX，HuggingFace `HangJang/C4-Cognitive-Classifier-v1`，light~416MB / heavy~1.1GB）——27 状态 Z₃³ 认知拓扑分类头。这部分是神经模型，LoveAI 短期不必搬。
- **它到底是什么**：把用户多年 ChatGPT/Claude/Gemini 导出的聊天记录，跑 **8 层（L1~L7）、25+ 模型、400+ 指标**，产出 Big Five / Dark Triad / Schwartz 价值观 / 依恋风格 / 认知偏差 / Jungian shadow / C4-META 27 态等侧写，**每个结论都挂着用户原话引用**。
- **关键架构事实（确证，`engine/blend.py` docstring）**：
  > "By default DSM scores are **purely rule-based** (deterministic spaCy + lexicon patterns) and the LLM is used **only to write the narrative summary**."

  也就是说——**打分靠词典/规则，LLM 只负责写人话总结**。`--blend` 才可选地让 LLM 给个对照分。这是它"可复现、不幻觉"的命根子。

源码目录骨架（`src/`）：
```
pipeline.py            # 8 层编排总入口（AnalysisPipeline.run_full_analysis）
layers/                # L1 语言层 → L7 自适应/阴影层，每层一个文件
  confidence.py        # ★ 置信度分带（唯一真源）
  _marker_layer.py     # ★ 轻量词典计数层的公共骨架
  _negation.py         # ★ 否定/引语/第三人称遮蔽 + 证据截取
  l3_big_five.py       # ★ 最完整的"Tag→facet→evidence"范式样本
  l4_bias_spans.py     # ★ 候选 span 抽取 + 便宜上下文过滤
  l4_adjudicate.py     # ★ LLM keep/drop/uncertain 裁决器（fail-closed）
lexicons/              # ★ 标签词典（每个标签=一组双语正则 marker）
engine/
  norms.py             # ★ 年龄/性别常模 → 百分位
  blend.py             # ★ 规则分 vs AI 分 非破坏性对照
  prompts.py           # 各层 LLM 抽取/总结 prompt
exporters/             # 报告渲染（html/docx/md，report_schema.py）
```

---

## 二、标签体系（taxonomy）

### 2.1 顶层维度 = "层（Layer）"，不是一张扁平表

DSM 的标签不是一次性拍出来的，而是**分层流水线**，每层产出自己的标签族（确证，`pipeline.py` 的 run_* 方法清单）：

| 层 | 干什么 | 产出的标签族 |
|---|---|---|
| L1 linguistic | 语言基础统计 | 词数、句长、词频 |
| L2 semantic/topics | 主题 | 话题分布 |
| **L3 psychological** | 人格/动机/情绪 | **Big Five(OCEAN)、Dark Triad、Schwartz价值观、Moral Foundations、Needs(nAch/nAff/nPow)、Locus of Control、Regulatory Focus、Grit、VADER情绪** |
| **L4 cognitive** | 认知/人际/动力 | **认知偏差(bias)、Beach扭曲、防御机制、人际空间、关系模式、心理动力** |
| L5 narrative | 叙事/元 | 原型(archetypes)、VIA优势、心理灵活性、反思功能、时间取向 |
| L6 therapeutic | 临床向 | 情绪调节、早期适应不良图式(EMS)、认知信任、治疗关系 |
| L7 adaptive/shadow | 整合层 | 阴影侧写、人生脚本、自适应模式、校准、交叉验证 |

> LoveAI 是"关系分析/伴侣画像"，真正对标的是 **L3（人格特质）+ L4（关系模式/人际空间/防御机制）+ L7（交叉验证/校准）**。L5/L6 临床向先不碰。

### 2.2 单个标签的数据结构（确证，`l3_big_five.py`）

这是 DSM 里最完整、最值得直接抄的 Tag schema：

```python
@dataclass
class BigFiveFacet:                       # 二级子特质（facet）
    name: str
    score: float                          # 0-100
    evidence: list[str] = ...             # 最多 10 条原话引用
    marker_count: int = 0

@dataclass
class BigFiveFactor:                      # 一级维度（O/C/E/A/N）
    name: str
    code: str                             # "O"
    score: float                          # 0-100
    facets: dict[str, BigFiveFacet]       # 下挂 facets
    evidence_high: list[str]              # 支持"高分极"的原话
    evidence_low: list[str]               # 支持"低分极"的原话
    total_markers: int = 0
    # to_dict() 里派生：
    #   level: high(>=70)/moderate(>=40)/low
    #   sparse: total_markers < 3
```

落到 DB（确证，`l3_big_five.py:809` 的 `CREATE TABLE`）：
```sql
CREATE TABLE l3_big_five_profile (
  id VARCHAR PRIMARY KEY DEFAULT 'user',
  factors JSON,        # {O:{name,score,level,total_markers}, ...}
  facets JSON,         # {O:{fantasy:{score,marker_count}, ...}}
  evidence JSON,       # {O:{high:[...≤10条], low:[...≤10条]}}
  confidence VARCHAR, # very_low/low/medium/high/very_high
  confidence_score DOUBLE
);
```

### 2.3 标签怎么定义 = 双语正则 marker 集（确证，`lexicons/needs.py`、`l3_big_five.py`）

每个标签 = 一个 **{high: {facet: {en:[...], ru:[...]}}, low: {general:{...}}}** 的正则词表。例（nAch 成就需求，`lexicons/needs.py`）：
```python
NACH_MARKERS = {"en": [r"\b(?:want|wanted) to be the (?:best|first|top)\b",
                       r"\b(?:need|strive|aim) to (?:achieve|win|succeed|excel)\b",
                       ...],
                "ru": [r"\b(?:хочу|хотел[а]?|стремлюсь) быть (?:лучшим|первым)\b", ...]}
```
- **双向极点**：high markers 算"高分极命中数"，low markers 算"低分极命中数"。
- **按语言归一**：命中数 ÷ **同语言** marker 总数（不是除以全部 marker），防止俄语词典大就压过英语（确证 `split_keywords_by_lang` / PRD-149）。

### 2.4 取值与置信度（确证）

**因子分怎么算**（`l3_big_five.py:961-969`）——这是核心算法：
```python
total = high_count + low_count
if total >= 3:
    score = (high_count / total) * 100     # 高/低命中比 → 0~100
else:
    score = 50.0                            # ★ 不足3个marker → 中立50，并标 sparse
```
- facet 分 = `min(100, round(命中数/同语言marker数 *100))`。
- **置信度不是 per-tag 的，而是 per-文案量的全局分带**（`layers/confidence.py`，全项目唯一真源）：

| 词数 word_count | 等级 | 数值 |
|---|---|---|
| <300 | very_low | 0.2 |
| <1000 | low | 0.4 |
| <3000 | medium | 0.6 |
| <10000 | high | 0.8 |
| ≥10000 | very_high | 0.95 |

- **稀疏门**：`total_markers < 3` → `sparse=True`，此时不给 0/100，hold 在 50 中立。这是防"3句话就敢下结论"的关键设计。

### 2.5 年龄/性别常模校正（确证，`engine/norms.py`）

给分数加"和同龄人比"的语境：
```python
norms = BigFiveNorms(age=35, gender='m')
cmp = norms.compare('O', 75)
# → {'norm':62, 'diff':+13, 'percentile':74,
#    'percentile_calibrated': False,          # ★ 永远 False
#    'percentile_kind':'uncalibrated_norm_position',
#    'label':'↗ above', 'text':'...'}
```
- 常模表是 Soto&John(2012)/Weisberg(2011) 的**方向近似手调值**（0-100），不是真人口普查。
- z = (user−norm)/SD(=20) → 正态 CDF → 百分位。
- diff>12 显著高 / >5 偏高 / <-5 偏低 / <-12 显著低。
- **关键诚实设计**：`percentile_calibrated` 永远为 False，并强制下游文案**禁止说"你超过了X%的人"**——因为词典分和问卷常模根本不在一个量纲上。

---

## 三、侧写引擎流程（抽取 → 证据累积 → 置信度更新 → 报告）

### 3.1 总流程（确证）

```
聊天导出(chatgpt/claude/...)
  → parsers 解析进 DuckDB messages 表(content, role, subject_id, timestamp)
  → mask_non_self_spans(text)   # 先剥掉引语/第三人称转述（防别人的话污染自己画像）
  → L1→L7 各层依次跑，每层把结果写进自己独立的 DuckDB 表
  → exporters 汇总成 HTML / DOCX / FULL_PROFILE.md
```
- **只分析 `role='user'`** 的消息（确证 `_fetch_user_text` / `analyze_from_db`）——AI 说的话不计入"你"。
- 预飞行：空 DB 直接 raise，不会给你一张全绿但没数据的报告（`run_full_analysis` preflight）。

### 3.2 证据怎么挂到标签上（确证，`_negation.py`）

核心函数 `negation_aware_find()`：
```python
for m in pattern.finditer(text):
    boundary = max(prefix.rfind('.'), rfind('!'), rfind('?'), rfind('\n'), rfind(';'))
    if not _negated(tokens, idx, window=3, boundary):  # 前3个token内是否定词，且不跨句
        kept.append(m)
# 证据 = 命中位置前后各截50字符，加 "..."
snippet = text[max(0,m.start()-50) : m.end()+50]
```
- **否定窗=前3个token，且不能跨句**（"我不焦虑。我很焦虑"不会被否定吞掉第二句）。
- **否定词表**中英双语合并（`NEGATION_WORDS`：not/n't/never.../не/нет/никогда...）。
- **自/他者遮蔽**（`mask_non_self_spans`）：引语 `«"..."»` 和第三人称转述 `he said that...` / `он сказал, что...` 整段替换成空格——别人说的话绝不进你的画像。
- 每个标签最多挂 **10 条原话**（`evidence[:10]`），前后 high/low 各一份。

### 3.3 两阶段"候选 → 裁决"（确证，偏差类标签专用：`l4_bias_spans.py` + `l4_adjudicate.py`）

对**高假阳性**标签（认知偏差），DSM 不直接信词典，而是：

**阶段1 便宜规则过滤**（`detect_bias_spans` + `apply_context_filters`）：
```python
# 命中即生成候选：
{candidate_id: uuid, bias_type, marker, start, end, span_text,
 context_before(120字), context_after(120字), speaker_role}
# 然后跑上下文过滤器，直接给 decision：
- speaker 是 assistant/ai/bot          → drop (conf 0.95)   # AI说的不算
- 前40字有否定词                        → 标记 negation
- 在引号内 / "you said" / 习语(idiom)   → drop
- 高FP类型(confirmation_bias等)        → uncertain (0.45)   # 升级给LLM
- 否则                                  → keep (0.75)
```

**阶段2 LLM 裁决**（`BiasAdjudicator`，只对 uncertain/需复核的候选）：
- system prompt 锁死边界："只许 keep/drop/uncertain，**不许发明新 bias_type，不许下诊断，不许给治疗建议**，只回一个 JSON"。
- 输出 schema：`{candidate_id, bias_type, decision, confidence:0-1, reason_codes[], reason_short:≤120字}`。
- **fail-closed**：解析失败/超时/被 fence 拦截 → **一律 drop**，绝不 auto-keep（`parse_bias_verdict` 返回 None 即 drop）。
- **预算闸门**（`adjudicate_budget_for_corpus`）：≤500条消息最多40次LLM调用/180s；≤2000条→80次/360s；更多→120次/480s。
- provider 白名单：默认只允许本地 LM（Ollama/LM Studio/loopback），公网 API 必须显式 `allow_api_adjudicate`。

### 3.4 置信度更新 / 规则 vs AI 融合（确证，`engine/blend.py`）

`--blend` 模式做"现实校验对照"，**非破坏性**：
- 规范分 `factors[X].score` 永远保留**规则值**（可复现基线不动）。
- LLM 对同一文本另打一套分，存进 `_blend` 块：
```python
contrast[key] = {"rule": 75, "ai": 62.0, "delta": -13.0, "agreement": "low"}
# agreement: |delta|<=8 high; <=20 medium; 否则 low
```
- 多源加权合并 `_weighted_merge`：按"该 key 谁给过分"做归一化加权平均（rule/local/api 权重来自 `PrecisionConfig`）。
- 设计哲学（原文）：**"规则说你语言里可测地表现出的" vs "AI 推测的"之间的 gap 才是洞察点**，不是把两者平均成一个数糊你脸上。

### 3.5 报告渲染（确证，`src/exporters/`）

- 多格式：`full_profile_html.py`(HTML) / `docx_profile.py`(Word) / `summary_profile.py` / `report_schema.py`(机器可读 JSON schema)。
- 样例：`docs/examples/example-profile-en.html` —— 顶部 confidence badge 写 "Scores compared to age 34 · female norms"，每个维度配 evidence 引用列表。
- 报告里**强制带校准披露**（README 提到 v0.6.2 加了 "relative Schwartz scale + saturation disclosures"）——即"以下是相对位置，不是确诊"。

---

## 四、LoveAI 可直接搬什么

> 映射目标：`services/digital_twin.py`（数字孪生/侧写累积）、`services/character_card.py`（角色卡渲染）。
> ⚠️ AGPL 约束：**搬思想和数据结构，不搬代码字面量**。下面给出的是"我们该长什么样"，不是"把它文件拷过来"。

### 4.1 直接抄的 Tag 数据结构（建议落到 digital_twin.py）

```python
@dataclass
class ProfileTag:
    code: str                 # "nAch" / "N_neuroticism" / "relationship_avoidant"
    dimension: str            # 所属维度: motive / bigfive / attachment / defense / relation
    pole: str                 # "high" / "low" / "neutral"
    score: float              # 0-100
    level: str                # high/moderate/low（>=70 / >=40 / <40）
    marker_count: int         # 命中数
    sparse: bool              # marker_count < 3 → True（不给极端分，hold 50）
    evidence: list[str]       # ≤10 条用户原话片段（前后50字，加...）
    confidence: str          # very_low/low/medium/high/very_high（按累计词数分带）
    calibrated: bool = False  # ★ 常模比较一律 False，文案禁止说"超过X%人"
```

### 4.2 直接抄的函数

| DSM 函数 | 位置 | LoveAI 对应 | 干什么 |
|---|---|---|---|
| `confidence_band(word_count)` | `layers/confidence.py` | digital_twin.py 里一个纯函数 | 唯一真源的置信度分带（300/1000/3000/10000 词数阈值） |
| `negation_aware_find()` | `layers/_negation.py` | evidence 抽取 | 前3token否定窗、不跨句、命中前后截50字做证据 |
| `mask_non_self_spans()` | `layers/_negation.py` | **关系分析必搬** | 剥掉引语+"他/她说"——**防止把对方说的话算成用户自己的特质** |
| `score = high/(high+low)*100`，<3个marker hold 50 | `l3_big_five.py:961` | 所有极性标签打分 | 双向命中比打分 + 稀疏中立 |
| `apply_context_filters()` | `l4_bias_spans.py` | 高假阳性标签前置过滤 | AI发言drop/引号内drop/习语drop/高FP升级 |
| `BiasAdjudicator`(fail-closed) | `l4_adjudicate.py` | 关系卡点/偏差诊断 | LLM 只许 keep/drop/uncertain，解析失败一律 drop |
| `BigFiveNorms.compare()` | `engine/norms.py` | 想加"和同龄异性比"时 | z→百分位，但**必须带 calibrated=False 披露** |
| `blend` 非破坏性对照 | `engine/blend.py` | digital_twin 里"规则分 vs AI 推断"对照面板 | 规范分不动，AI 分存 `_blend`，gap 才是洞察 |

### 4.3 可抄的抽取/裁决 prompt 写法（落到 engine/prompts 层）

裁决类 prompt 的**边界锁**值得原样借鉴（`l4_adjudicate.py:65`）：
> "You adjudicate *candidates*. You may **ONLY** decide keep/drop/uncertain for the given candidate_id and type. **Never invent a new type. Never diagnose. Never give therapy advice.** Reply with a single JSON object only."

要点：① 只许三值决策；② 不许发明新标签；③ 不许诊断/建议；④ 强制 JSON。LoveAI 做"关系卡点诊断"时照这个写，能防 LLM 自由发挥瞎贴标签。

### 4.4 标签维度建议（LoveAI 关系场景精简版）

不必搬 400 指标，先这几族（都在 DSM 里有现成词表思路）：
- **关系模式**（对标 `l4_relationship_patterns.py` / `relationship_patterns.py` 词典）：依恋类型、追逃、指责-讨好。
- **防御机制**（对标 `defense_mechanisms.py`）：否认/投射/合理化。
- **动机三需**（对标 `lexicons/needs.py`）：nAch 成就 / nAff 亲和 / nPow 权力——关系里"要控制 vs 要亲密"直接用。
- **Big Five 精简版**：只要 O/C/E/A/N 五个 factor + evidence，不要全 facet。

---

## 五、坏设计 / 避坑（它自己都承认的短板）

1. **置信度是"文案量"不是"证据质量"**（确证 `confidence.py`）：1万字就 very_high=0.95，但这1万字全是车轱辘话也照样 0.95。**它没有按"标签独立证据条数/跨情境一致性"加权置信度**——LoveAI 应补：置信度 = f(词数分带, 独立证据条数, 跨对话一致性)，别只看字数。
2. **词典打分本质是词频LIWC，效度存疑**（它自己 `norms.py` docstring 都写了：词典分 vs 问卷常模不同量纲，百分位未校准）。别信"算法测出来你的人格"，只当"你语言里频繁出现的模式"。
3. **LLM 只在 maximum 模式调，且非确定性**（`blend.py`）：同一段文本两次跑 AI 分会不一样，所以它把规范分锁死成规则值。**LoveAI 若做在线增量累积，千万别拿 LLM 分直接覆盖历史分**——会漂移。
4. **AGPL 传染性**：见第一节，别抄字面代码。
5. **多语言归一靠字符占比**（`split_keywords_by_lang`：混语≥25%两种都跑），中文场景得重写——它只有 en/ru，**没有中文词典**，LoveAI 的中文 marker 要自己造。
6. **稀疏阈值是拍的**（<3 marker 中立、词数分带阈值）——它自己标注是 hand-tuned，LoveAI 可参考但要用自己语料重标定。
7. **fail-closed 有代价**：解析失败全 drop，会漏真阳性（真偏差被当噪声丢了）。关系场景里"漏报卡点"比"误报"代价高，需权衡。

---

## 六、优先级初判（P0/P1/P2）

| 优先级 | 搬什么 | 理由 | 预估人日 |
|---|---|---|---|
| **P0** | `ProfileTag` 数据结构（含 sparse/evidence/confidence）+ `confidence_band` 分带 + `negation_aware_find` 证据截取 + `mask_non_self_spans` 自他者遮蔽 | 这是整个侧写引擎的骨架，关系分析里"把对方的话算成自己"是致命错误，必须先有 | 2-3 人日 |
| **P0** | 双向极性打分 `high/(high+low)*100` + <3 marker hold 50 | 10行算法，但决定"敢不敢下结论"，防过度解读 | 0.5 人日 |
| **P1** | 两阶段"候选→裁决"（便宜规则过滤 + LLM keep/drop/uncertain + fail-closed + prompt边界锁） | 直接服务"关系卡点诊断"，防 LLM 瞎贴标签 | 2-3 人日 |
| **P1** | 规则分 vs AI 分 非破坏性对照（`_blend` 块） | LoveAI 已有 persona_analyzer/数字孪生，加个"系统说的 vs AI感觉的"对照面板，提升可信度 | 1.5 人日 |
| **P2** | 常模百分位 + `calibrated=False` 披露 | 锦上添花，且中文无现成常模，别硬上 | 1 人日（含披露文案） |
| **P2** | 完整 8 层 / C4 神经分类器 / 多格式报告导出 | 体量太大，LoveAI 现阶段用不上 | 暂不投入 |

**一句话**：先搬"Tag=分维度+极性分+稀疏门+≤10条原话证据+按字数置信度"这套**可复现骨架**（P0），再叠"规则抽候选→LLM 三值裁决 fail-closed"做关系卡点（P1）；AI 只写人话总结、不碰规范分，是它最该学的工程纪律。

---

### 附：本次实际扒取的真实文件路径（均在 `_dsm_src/` 下）
- `src/layers/confidence.py`、`src/layers/_marker_layer.py`、`src/layers/_negation.py`
- `src/layers/l3_big_five.py`、`src/layers/l4_bias_spans.py`、`src/layers/l4_adjudicate.py`
- `src/engine/norms.py`、`src/engine/blend.py`、`src/pipeline.py`
- `src/lexicons/needs.py`
- `LICENSE`(AGPL-3.0)、`README.md`、`src/exporters/`(report_schema.py / full_profile_html.py)
