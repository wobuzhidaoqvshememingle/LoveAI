# 05 · UncommonRoute / 模型难度分级路由 · 深度扒取

> 任务范围：只扒「难度分级路由」这一块——它怎么判难度、分几档、阈值多少、怎么兜底、有没有反馈闭环。
> 扒取日期：2026-10-09。确证信息来自 PyPI 官方页面 + GitHub raw 源码；推测处已显式标注。

---

## 仓库定位

### UncommonRoute（真实存在，不是想象中的项目）

- **仓库**：https://github.com/anjieyang/UncommonRoute
- **作者**：Anjie Yang（Commonstack 团队）
- **许可证**：MIT
- **包名**：`uncommon-route`（PyPI，当前已到 0.7.21；本扒取基于 0.2.7 文档，结构稳定）
- **定位原话**："Don't route by habit. Route by difficulty." —— 本地 4 层 LLM 路由器，官方宣称 92.3% 分类准确率、0.5ms 延迟、比「永远用 Opus」省 67%。
- **它不是 SaaS 黑盒**：分类器是本地的 Averaged Perceptron，权重存在 `model.json`，可用自己的数据 ~26 秒重训。

> 红线核验：本项目**不是**小众到找不到——它有 PyPI 包、有 GitHub 仓库、有 MIT 许可证、有 arXiv TwinRouterBench 引用（被当作「rule-based」对照组）。所以主参考就是它本身，不需要拿别的项目顶替。

### 同时参考的真实开源路由器（用于对照「纯难度规则」vs「ML 偏好分类」两条路线）

| 项目 | 仓库 | 路线 | 本扒取读到的真实源码 |
|---|---|---|---|
| **RouteLLM** | lm-sys/RouteLLM | ML 偏好分类（不是抽象难度） | `routellm/routers/routers.py` 全文已读 |
| **llmrouter** | alexrudloff/llmrouter | 规则文件 `ROUTES.md` 驱动 | 仅 SKILL.md 描述，未深读源码 |

---

## 难度怎么打分为真

### 1. UncommonRoute：三级级联（cascade）分类器

PyPI 文档里给的架构图（确证）：

```
Input Prompt
 │
 ▼
┌─────────────────────┐
│ 1. Trivial Override │ greeting / empty / very long → 即时决策，不进模型
└─────────┬───────────┘
 │
 ▼
┌─────────────────────┐
│ 2. Learned Model    │ Averaged Perceptron on 39 features（avg 356µs）
│   12 structural      │ + 15 unicode + 12 keyword
└─────────┬───────────┘
 │
 ▼
┌─────────────────────┐
│ 3. Rule Fallback    │ 训练好的权重不可用时，用手调权重兜底
└─────────┬───────────┘
 │
 ▼
 Tier + Model + Cost
```

**关键设计点**：先过 trivial override 砍掉「根本不用动脑」的请求（空文本、纯问候、超长），剩下的才进 39 特征感知机。这就是它能做到 0.5ms 的原因——大多数请求在第 1 层就分流了，根本不跑特征工程。

### 2. 39 个特征（全部列名，确证自 PyPI）

**Structural（12 个）：**
`normalized_length, enumeration_density, sentence_count, code_markers, math_symbols, nesting_depth, vocabulary_diversity, avg_word_length, alphabetic_ratio, functional_intent, unique_concept_density, requirement_phrases`

**Unicode（15 个）：**
`basic_latin, latin_ext, cyrillic, arabic, devanagari, thai, hangul_jamo, cjk_unified, hiragana, katakana, hangul_syllables, punctuation, digits, symbols_math, ...`

> 这组特征的真实用途不是「识别语言」，而是**间接判断任务类型**：CJK + 高 requirement_phrases 密度 = 中文多需求诉求；code_markers + math_symbols 高 = 代码/数学；enumeration_density 高 = 「帮我列 5 点」这种结构化请求。中文场景下 cjk_unified 是必加特征。

**Keyword（12 个）：**
`code_presence, reasoning_markers, technical_terms, creative_markers, simple_indicators, imperative_verbs, constraint_count, output_format, domain_specificity, agentic_task, analytical_verbs, multi_step_patterns`

> 注意：它**不是**我们现在 `difficulty_router.py` 那种「命中关键词就判 hard」的硬规则。关键词在这里被压成 12 个**聚合二值/计数特征**（例如 `reasoning_markers` 是「出现几处推理连接词」的计数，不是单个词），再喂给感知机做加权。这是它比纯关键词准得多的原因。

### 3. 打分函数本身：Averaged Perceptron（确证）

PyPI「Benchmarks」段原文：
- 模型类型：**Averaged Perceptron**
- 训练集：1,904 prompts（repo 自带）
- 本地重训时间：**~26 秒**
- 训练准确率：98.6%
- 特征族：39（structural + unicode + keyword）

重训入口（确证）：
```python
from uncommon_route.router.classifier import train_and_save_model
train_and_save_model("bench/data/train.jsonl")
```

项目真实文件结构（确证，来自 PyPI Project Structure 段）：
```
uncommon_route/router/
├── classifier.py     # 三级级联总控
├── learned.py        # Averaged Perceptron（ScriptAgnosticClassifier）
├── structural.py     # 12 structural + 15 unicode 特征提取
├── keywords.py       # 12 keyword 特征提取
├── selector.py       # tier → model + fallback chain 选择
└── model.json        # 训练好的权重（可替换）
```

> 我们没抓到 classifier.py 的逐行源码（GitHub 对爬虫返回 robots 限制），但文件职责、特征清单、模型类型、训练入口、基准数字都是官方文档白纸黑字。**推测**：`structural.py` 就是一组纯函数 `extract_features(text) -> list[float]`，39 维，全部 O(n) 字符串扫描，这也是它 356µs 能跑完的原因。

### 4. 对照：RouteLLM 走的是另一条路（真实源码 routers.py 已读）

RouteLLM **不判抽象难度**，它判「strong 模型赢 weak 模型的概率」。核心抽象（确证源码）：

```python
class Router(abc.ABC):
    # 返回 0~1，表示 strong 模型的胜率
    @abc.abstractmethod
    def calculate_strong_win_rate(self, prompt): ...

    def route(self, prompt, threshold, routed_pair):
        if self.calculate_strong_win_rate(prompt) >= threshold:
            return routed_pair.strong
        else:
            return routed_pair.weak
```

它内置 5 种路由器（确证源码）：

| 类 | 怎么打分 | 开销 |
|---|---|---|
| `RandomRouter` | 随机 0~1 | 基线 |
| `MatrixFactorizationRouter` | MFModel.pred_win_rate(strong_id, weak_id, prompt)，向量内积 | 轻 |
| `CausalLLMRouter` | 微调 Llama-3-8B，5 个 special token `[[1]]..[[5]]` 输出等级，`score_threshold=4` | 重（要起 8B 模型） |
| `BERTRouter` | BERT 三分类，softmax 后取 label1+label2 概率作为「强模型赢」 | 中 |
| `SWRankingRouter` | 用 text-embedding-3-small 算 prompt embedding，跟 Arena 历史 embedding 算相似度，再加权 Elo 胜率 | 中（要调 OpenAI embedding） |

**RouteLLM 的阈值不是「难度分」，是「成本-质量 cutoff」**：例如 `router-mf-0.11593` 意思是「强模型胜率 ≥ 0.116 才走 GPT-4，否则 GPT-3.5」。这个数字是在 MT-Bench 上扫出来的——你愿意为多少质量付多少钱。

### 5. 两条路线对我们的启示（结论）

- **UncommonRoute 路线**（规则 + 轻量感知机）：适合我们——本地、0.5ms、可解释、能用自己的中文对话数据重训。
- **RouteLLM 路线**（ML 偏好分类）：要标注「哪段对话该用强模型」，我们没有这个标注数据，**现阶段不搬**，但它的「二元胜率 + cutoff」抽象值得抄——这比我们现在写死「hard 就走强模型」更灵活。

---

## 分级标准与阈值表

### UncommonRoute 4 档（确证，含真实模型和价格）

| 档位 | 触发场景 | 默认主模型（输入/输出 $/1M） | Fallback 链 | 例子 |
|---|---|---|---|---|
| **SIMPLE** | 问候、查询、翻译 | moonshot/kimi-k2.5（$0.60/$3.00） | gemini-2.5-flash-lite（$0.10/$0.40）, deepseek-chat（$0.28/$0.42） | "what is 2+2" |
| **MEDIUM** | 代码任务、解释、总结 | moonshot/kimi-k2.5 | deepseek-chat, gemini-2.5-flash-lite | "explain quicksort" |
| **COMPLEX** | 多需求系统设计 | google/gemini-3.1-pro（$2.00/$12.00） | gemini-2.5-pro, gpt-5.2（$1.75/$14）, claude-sonnet-4.6（$3.00/$15） | "design a distributed DB with 5 requirements" |
| **REASONING** | 形式证明、数学推导 | xai/grok-4-1-fast-reasoning（$0.20/$0.50） | deepseek-reasoner, o4-mini, o3 | "prove sqrt(2) is irrational" |

基准线对照：anthropic/claude-opus-4.6 = $5.00/$25.00。

**5 种 Profile（同一档走不同模型表）**：`auto`（默认）/ `eco`（最便宜优先）/ `premium`（质量优先）/ `free`（免费优先，best-effort）/ `agentic`（工具调用密集）。这意味着「档位」和「具体模型」之间还隔了一层 profile——同一个 COMPLEX 档，auto 走 gemini-pro，premium 走 opus。

### 官方基准数字（确证）

- Held-out：763 个手写 prompt，15 种语言，35 个类别。
- 分类准确率 92.3%，延迟 0.5ms。
- **MEDIUM 档 F1 = 88.7%，REASONING 档 F1 = 97.8%**——钱主要在 MEDIUM 档省下来，因为大多数请求都落在 MEDIUM。
- 真实 131 请求 agent 编码会话模拟：总成本 $1.7529 → $0.5801，省 67%，质量保留 93.5%。

### 我们现在的阈值（自己代码里确证）

`services/difficulty_router.py`：

| 档位 | 触发条件 | 我们的默认阈值 |
|---|---|---|
| easy | 命中 easy 寒暄词 **或** 文本长度 ≤ 15 | `easy_length = 15` |
| hard | 命中 hard 关键词 **或** 文本长度 ≥ 120 | `hard_length = 120` |
| medium | 其余 | 默认中档 |

**对比差距**：
1. 我们 3 档，它 4 档（多一个 REASONING——纯数学/形式证明，这种请求对我们其实少见）。
2. 我们的「难度」只看「长度 + 硬关键词命中」，它看 39 维聚合特征。
3. 我们 hard_keywords 里全是「侧写/六维/评分/NPD/BPD/挽回」这种**业务词**，这其实是「任务类型路由」不是「难度路由」——把它们混在一起会误判（一句"为什么他出轨"会被打成 hard，但其实是个日常抱怨）。

---

## 降级 / 兜底 / 反馈闭环

### UncommonRoute 的完整兜底链（确证）

1. **分类器级兜底**：trivial override → 感知机 → 手调规则权重（model 不可用时）。
2. **模型级 fallback chain**：每档有显式 fallback 列表，上游 reject 一个模型自动试下一个。
3. **能力过滤**：请求带 `tools` 或图片时，先把「不支持 tool calling / 多模态」的模型从候选里踢掉，再按 profile 排序，不是单纯按价格排。
4. **Session 状态机**（这是最值得抄的一段）：
   - 默认「每次请求重新分类」；
   - 可选「**只升不降**」：分类器说 COMPLEX 但 session 是 MEDIUM → 升级；分类器说 SIMPLE 但 session 是 MEDIUM → **保持 MEDIUM 不降级**（避免任务中途抖动）；
   - 例外：tool-result 步骤绕过 session hold，直接用分类器建议；
   - **30 分钟无活动 session 自动过期**；
   - **三次相同请求 → 自动升到下一档**（"three-strike escalation"，tool-result 步骤跳过）。
5. **Bandit 选择器**：每档内部不是写死主模型，而是按 profile 权重 + 预测 token 成本 + 观测延迟/吞吐 + 观测可靠性 + 用户反馈 + cache 亲和度 + BYOK 偏好，加一个**有界探索 bonus**，低采样候选会被试探。探索按 `profile + tier` 分桶，保证 SIMPLE/MEDIUM 敢试，REASONING 不乱动。
6. **反馈闭环**：`POST /v1/feedback` 在线学习；`/v1/selector` 能看每个候选的实时打分。
7. **Spend control**：per_request / hourly / daily / session 四档限额，超限返回 HTTP 429 + `reset_in_seconds`。
8. **硬钉模式**：`--mode hard-pin` 把某档钉死在主模型上，只有上游报错才 fallback；默认 `adaptive` 用 bandit。

### 我们自己已有的兜底（free_model_router.py 确证）

我们其实**已经有一套相当成熟的状态机**，只是没跟难度档连起来：

| 机制 | 我们的实现 | 参数 |
|---|---|---|
| 429 限流 | 同模型指数退避 | 1s → 2s → 4s，最多 3 次 |
| 5xx 熔断 | 连续失败翻倍冷却 | 30s 起步，封顶 5min |
| 401/403 | 永久禁用该模型 | 直到重启 |
| 启动探活 | max_tokens=1 的 "hi" | 单候选 2s 超时，总预算 5s |
| 排序 | alive > unknown；本地 > 云端；成功率降序 → 延迟升序 | — |
| 空回复 | HTTP 200 但无内容 → 不标记成功，切下一个 | — |

**差距**：我们的兜底是「免费渠道之间互切」，没有「同一档内按 bandit 选模型」，也没有「只升不降 session」和「三次相同请求升级」。

---

## LoveAI 可直接搬什么（映射到我们 services\ 下实际文件）

> 我们的真实 router 相关文件（已 ls 确证）：
> - `services/difficulty_router.py` ← 难度分级器（3 档规则版）
> - `services/free_model_router.py` ← 免费模型候选排序 + 熔断/退避/探活
> - `services/llm_gateway.py` ← 网关
> - `services/local_statistical_fallback.py` / `refusal_downgrade.py` / `confidence_cap.py` ← 其它降级
>
> 以下每条都给出「改哪个文件、加什么函数、数据结构长什么样」。

### 搬 1：把「硬关键词命中」改成「聚合计数特征」（改 difficulty_router.py）

**现状问题**：`hit_hard = [kw for kw in self.hard_keywords if kw in t]`，命中一个就 hard。
**搬法**：照 UncommonRoute 的 keyword 族，把单个词变成 12 个左右的计数/二值特征：

```python
# 新增 services/difficulty_features.py（或直接塞进 difficulty_router.py）
def extract_features(text: str) -> dict:
    t = text or ""
    return {
        "normalized_length": len(t) / 500.0,          # 截断归一
        "sentence_count": t.count("。") + t.count(".") + t.count("！") + t.count("?"),
        "code_markers": sum(t.count(x) for x in ("```", "def ", "import ", "{", "=")),
        "math_symbols": sum(t.count(x) for x in ("+", "-", "*", "/", "=", "∑", "∫")),
        "enumeration_density": sum(t.count(x) for x in ("1.", "2.", "3.", "、", "（1）")),
        "cjk_ratio": sum(1 for c in t if '一' <= c <= '龥') / max(1, len(t)),
        "reasoning_markers": sum(t.count(x) for x in ("为什么", "凭什么", "怎么", "难道", "其实", "本质")),
        "analytical_verbs": sum(t.count(x) for x in ("分析", "预测", "判断", "推导", "评估")),
        "imperative_verbs": sum(t.count(x) for x in ("帮我", "给我", "写一份", "列一下")),
        "constraint_count": sum(t.count(x) for x in ("不要", "必须", "一定", "但是", "不过")),
        "creative_markers": sum(t.count(x) for x in ("安慰", "夸", "哄", "撒娇", "情话")),
        "agentic_task": int(any(k in t for k in ("侧写", "六维", "数字分身", "博弈"))),
    }
```

然后 `DifficultyRouter._rule` 里不再是「命中 hard 词就 hard」，而是给这些特征加权求和（先用手调权重，跑一段时间再换成感知机）。

### 搬 2：预留的 classifier 接口真接上感知机（改 difficulty_router.py）

我们已经留了 `classifier: Optional[Callable[[str], Optional[str]]]` 注入点（第 68、110 行），**这个架构抄对了**。下一步：

```python
# 新增 services/perceptron_classifier.py
# 用 sklearn.linear_model.Perceptron(warm_start=True) 即可，模型文件存 config 目录
class PerceptronDifficultyClassifier:
    def __init__(self, model_path: str):
        self.clf = joblib.load(model_path) if exists else None
    def __call__(self, text: str):
        if self.clf is None:
            return None  # 回落规则
        x = np.array([list(extract_features(text).values())]).reshape(1, -1)
        return self.clf.predict(x)[0]  # 返回 easy/medium/hard
```

训练数据来源：我们自己的对话日志 + 人工标注 500~1000 条（UncommonRoute 用了 1904 条，26 秒训完）。**不用一上来就训，先用手调权重跑通，攒够数据再训。**

### 搬 3：把 3 档扩成「3 档 + REASONING 子档」（改 difficulty_router.py 的 _TIERS）

我们不一定要抄 4 档，但要把「纯数学/形式证明」从 COMPLEX 里拆出来——这种请求在关系分析场景几乎没有，**对我们其实 P2，不急着加**。先保持 3 档。

### 搬 4：抄 Session「只升不降 + 三次相同请求升级」（新建 services/difficulty_session.py）

这是 UncommonRoute 最值钱的一段逻辑，直接搬：

```python
# 新建 services/difficulty_session.py
SESSION_TTL_SEC = 1800          # 30 分钟
THREE_STRIKE_N = 3              # 三次相同请求升级

class DifficultySession:
    def __init__(self):
        self._state: dict[str, dict] = {}   # session_id -> {"tier":..., "last":..., "same_count":..., "last_sig":...}

    def decide(self, session_id: str, prompt_sig: str, classifier_tier: str) -> str:
        now = time.time()
        st = self._state.get(session_id)
        if st and now - st["last"] > SESSION_TTL_SEC:
            st = None
        if st is None:
            tier = classifier_tier
        else:
            # 只升不降
            order = ["easy", "medium", "hard"]
            if order.index(classifier_tier) > order.index(st["tier"]):
                tier = classifier_tier          # 升级
            else:
                tier = st["tier"]               # 不降级
            # 三次相同请求升级
            if prompt_sig == st["last_sig"]:
                st["same_count"] += 1
                if st["same_count"] >= THREE_STRIKE_N and tier != "hard":
                    tier = order[order.index(tier) + 1]
            else:
                st["same_count"] = 1
        self._state[session_id] = {"tier": tier, "last": now,
                                   "same_count": st["same_count"] if st else 1,
                                   "last_sig": prompt_sig}
        return tier
```

**为什么重要**：我们现在每条消息独立判档，同一段对话里用户先聊两句 easy、突然甩一个长问题判 hard、下一句又回到 easy——模型来回切，既贵又跳戏。这个状态机直接解决。

### 搬 5：把「档位 → 模型」做成可配置表 + fallback 链（改 llm_gateway.py）

现在我们的 `difficulty_router.py` 只输出 tier，**没有定义「tier 对应哪个模型」**。照 UncommonRoute 的表，在 config 里加：

```json
{
  "llm.tier_models": {
    "easy":   {"primary": "free-ollama-qwen2.5:7b", "fallback": ["openrouter:free", "gemini-flash-lite"]},
    "medium": {"primary": "kimi-k2.5",              "fallback": ["deepseek-chat", "gemini-flash-lite"]},
    "hard":   {"primary": "gemini-3.1-pro",          "fallback": ["claude-sonnet", "gpt-4o"]}
  }
}
```

然后网关在拿到 tier 后，先试 primary，失败沿 fallback 链走——**这个 fallback 机制我们 free_model_router.py 已经写好了**（429 退避 / 5xx 熔断 / 401 永久禁用），直接复用，不要重写。

### 搬 6：Spend control（新建 services/spend_control.py）

UncommonRoute 的 per_request / hourly / daily / session 四档限额，我们现在没有。对个体户自己用其实不急需，但如果以后要给别人用，**这个是防账单爆炸的最后一道闸**。P2。

---

## 坏设计 / 避坑

好的坏的都记下来：

1. **UncommonRoute 的 MEDIUM 档才是战场**——它官方原话"most routers can tell cheap from expensive. The money is won or lost in the middle." 我们如果只做「easy vs hard」二分类，会把大量该走便宜模型的请求错判给 hard。**不要做二分类，至少 3 档，且 MEDIUM 档要有足够多的训练样本。**

2. **规则驱动的坑（我们自己 now 在踩）**：把业务词（侧写/NPD/挽回）当难度词用。用户说「我妈昨晚给我打电话又开始念我了」——没有 hard 词，但其实是个需要共情的中等问题；用户说「帮我分析一下他为什么 NPD」——命中 NPD，但其实一句话就能答。**难度特征和任务类型特征要分开两组，不要混在一个列表里。**

3. **RouteLLM 式 ML 分类的坑**：需要「strong vs weak」偏好标注数据，我们没有。直接上 BERT/8B 微调是过度工程——ai-tldr 那篇评测原话："Many teams spend weeks building sophisticated classifiers and discover a five-rule heuristic would have done the job." **先规则，再感知机，最后才考虑微调。**

4. **UncommonRoute 的 bandit 选择器对我们是过度设计**——它要跨十几个模型按成本/延迟/可靠性在线学习。我们候选模型就三五个，写死 fallback 链比 bandit 更可控。**P2，不搬。**

5. **只升不降 session 的坑**：如果用户在 session 中段从「深度分析」切回「随便聊聊」，session 会把后续闲聊也钉在 hard 档多烧钱。**30 分钟 TTL 必须严格执行，且要在前端/日志里能看到「当前 session 钉在 hard 档」，给用户一个手动 reset 的入口。**

6. **三次相同请求升级的坑**：如果用户在反复发同一句话（比如网络卡了重发），会被误判成「这个问题答不好，升级」。**要先判断「上一次是不是失败了」再触发 three-strike，不要把网络重试算进去。**

7. **超长文本当 trivial 处理的坑**：UncommonRoute 把 very long 直接当 override——但我们的场景里，用户粘一大段聊天记录过来恰恰是**最需要强模型**的（侧写/分析）。**不要抄它的 very-long override 方向，我们的 very-long 应该往 hard 走，不是 SIMPLE。**

---

## 优先级初判

| 优先级 | 事项 | 落点文件 | 理由 | 人日估算 |
|---|---|---|---|---|
| **P0** | 把硬关键词列表拆成「难度特征」vs「任务类型特征」两组，先修误判 | `difficulty_router.py` | 现在业务词当难度词用，误判率高，改起来 0 成本 | 0.5 人日 |
| **P0** | 抄 Session「只升不降 + 30min TTL」状态机 | 新建 `difficulty_session.py`，网关接线 | 解决来回切模型的跳戏和浪费，逻辑自包含，可独立测 | 1.5 人日 |
| **P0** | 把「tier → 主模型 + fallback 链」做成 config 表，网关接线复用 free_model_router 的熔断/退避 | `llm_gateway.py` + config | 现在 tier 输出了但没人消费，这是闭环的最后一环 | 1 人日 |
| **P1** | 实现 12~15 维聚合特征函数（extract_features），先用手调权重 | 新建 `difficulty_features.py` | 比现在的「命中即 hard」准一档，且为以后感知机铺路 | 1 人日 |
| **P1** | 攒 500~1000 条中文对话标注数据，训 Averaged Perceptron，接上预留的 classifier 注入点 | 新建 `perceptron_classifier.py` | 我们已有注入接口，接上就能升级规则；但要先攒数据 | 3 人日（含标注） |
| **P2** | 加 REASONING 子档 | `difficulty_router.py` | 关系分析场景几乎用不到纯数学推导 | 0.5 人日 |
| **P2** | Spend control 四档限额 | 新建 `spend_control.py` | 自用不急需，给别人用时才是刚需 | 1 人日 |
| **P2** | Bandit 选择器 | `selector.py` 自建 | 候选模型少，过度设计，先不做 | — |
| **P2** | 抄 UncommonRoute 的 step-aware routing（tool-result 用便宜模型） | 网关 | 我们现在 agentic 工作流还不重，等 task_engine 铺开再做 | 1 人日 |

**一句话结论**：UncommonRoute 最值钱的不是那个 92.3% 的感知机，而是它的「**tier + profile + session 只升不降 + fallback 链 + spend control**」整套产品化骨架。我们现在只做了骨架的第一块（tier 输出），P0 三板斧（拆特征 / session 状态机 / tier→模型表）接上就能立刻省一大笔冤枉钱，感知机反而排在 P1。
