# SillyTavern 记忆系统（记忆四层）代码级深扒

> 本文件只扒 SillyTavern 一个项目，下钻到真实源码函数。所有路径均为 `web.fetch` 实际读过的文件，分支 `release`。
> 抓取日期：2026-10-09。

---

## 仓库与定位

| 项 | 值 |
|---|---|
| 仓库 | https://github.com/SillyTavern/SillyTavern |
| 语言 | 前端 Vanilla JS（ES Module，jQuery UI）+ Node.js/Express 后端 |
| 许可证 | GNU AGPL v3.0 |
| 分支 | `release`（稳定分支，本次所有 raw 文件均取自此分支） |
| 向量库 | **vectra**（npm 包，文件型本地向量索引 `vectra.LocalIndex`，余弦相似度，纯本地文件持久化，无外部 DB 服务） |

> 注意：SillyTavern 的"记忆四层"不是官方命名，社区约定俗成指：①角色卡固有设定（main prompt）②世界书/World Info（关键词触发 lorebook）③向量记忆（embedding recall / RAG）④摘要长期记忆（summarization）。本文按这四层拆。

---

## 核心代码文件位置（实际读过）

| 文件路径 | 作用 |
|---|---|
| `public/scripts/world-info.js` | 世界书引擎。条目 schema、关键词匹配、选择性逻辑、递归扫描、token 预算、sticky/cooldown/delay 时序效应、min activations。约千行级。 |
| `public/scripts/extensions/vectors/index.js` | 向量扩展前端。召回编排 `rearrangeChat`、查询文本构造 `getQueryText`、chunk 切分、把召回消息从对话里"抽走再插回指定 depth"。93KB。 |
| `src/endpoints/vectors.js` | 向量后端。Express 路由 `/api/vector/query`、`/insert`、`/list`；`vectra.LocalIndex` 的建库/upsert/query；score 阈值过滤。24KB。 |
| `public/scripts/extensions/memory/index.js` | 摘要/长期记忆扩展。触发条件判断 `getSummaryPromptForNow`、摘要 prompt 拼装 `getRawSummaryPrompt`、结果写回 `mes.extra.memory` 并 `setExtensionPrompt` 注入。 |

未单独精读但从上述文件 import 关系可确认的机制：`setExtensionPrompt(tag, text, position, depth, include_wi, role)` 是 SillyTavern 所有"层"往 prompt 里塞内容的统一管道，靠 **tag 的数字前缀决定拼接顺序**（见下）。

---

## 四层各自的真实实现

### 第 0 层公共底座：统一注入管道 `setExtensionPrompt`

四层不是各写各的，全部走同一个函数，靠两个轴控制：**position（插到 prompt 哪个位置）+ depth（插到离最后一条消息多远）**。扩展 tag 前缀数字决定先后：

```js
// 实际出现的 tag（来自源码常量）
const MODULE_NAME = '1_memory';        // 摘要层
export const EXTENSION_PROMPT_TAG = '3_vectors';            // 向量召回层
export const EXTENSION_PROMPT_TAG_DB = '4_vectors_data_bank'; // 资料库层
// 世界书条目注入 depth 由条目自身 extensions.depth 决定，默认 DEFAULT_DEPTH = 4
```

`extension_prompt_types.IN_PROMPT` = 注入到主 prompt 区；`extension_prompt_roles.SYSTEM` = 以 system 角色发。**depth 越大越靠上（越接近角色设定），depth=0 就是紧贴最后一条用户消息**。这是理解四层协同的钥匙。

---

### 第 1 层：角色卡固有设定（main prompt / character definition）

**机制**：角色卡的 description / personality / scenario / creator's note 是**静态常驻块**，永远在上下文顶部，不参与触发逻辑。世界书引擎反过来把它们当作"待扫描文本"的一部分——条目可以配置成只在匹配到角色卡某字段时才激活。

证据在 `newWorldInfoEntryDefinition` 里这一组布尔开关（`public/scripts/world-info.js`）：

```js
matchPersonaDescription:   { default: false },  // 匹配偶娃描述
matchCharacterDescription: { default: false },  // 匹配角色卡 description
matchCharacterPersonality: { default: false }, // 匹配角色卡 personality
matchCharacterDepthPrompt: { default: false },
matchScenario:             { default: false },  // 匹配角色卡 scenario
matchCreatorNotes:         { default: false },  // 匹配 creator note
```

扫描时这些"chat-independent context"被装进 `WIGlobalScanData`，和最近 N 条对话拼一起做关键词匹配（源码注释：*"Chat independent data to be scanned, such as persona and character descriptions"*）。

**关键设计**：角色卡设定本身不"花钱做召回"，它是锚；世界书条目通过上面 6 个 match 开关挂到这个锚上。LoveAI 做关系侧写时，"关系卡点/人设基线"就该常驻这一层。

---

### 第 2 层：世界书 / World Info（关键词触发 lorebook）

这是 SillyTavern 最成熟、最值得抄的一层。

#### 2.1 条目数据结构（`newWorldInfoEntryDefinition`，真实字段）

```js
export const newWorldInfoEntryDefinition = {
  key:             { default: [] },        // 主关键词数组（keys）
  keysecondary:    { default: [] },        // 副关键词数组（secondary_keys）
  comment:         { default: '' },        // 条目备注（给人看的名字）
  content:         { default: '' },         // 真正注入 prompt 的正文
  constant:        { default: false },     // = true 即"恒显"，无视关键词永远激活
  vectorized:      { default: false },      // 是否参与向量召回（第3层联动）
  selective:       { default: true },       // 是否启用副关键词逻辑
  selectiveLogic:  { default: 0 },        // AND_ANY=0 / NOT_ALL=1 / NOT_ANY=2 / AND_ALL=3
  order:           { default: 100 },       // 插入顺序权重，越大越靠前（sortFn: b.order - a.order）
  position:        { default: 0 },         // before_char / after_char / @depth
  depth:           { default: 4 },         // 注入深度（DEFAULT_DEPTH = 4）
  probability:     { default: 100 },       // 激活后再掷一次概率（100=必中）
  useProbability:  { default: true },
  disable:         { default: false },     // = enabled 取反
  ignoreBudget:    { default: false },     // 无视 token 预算，强制注入
  excludeRecursion:{ default: false },      // 递归扫描时不再把它的正文当扫描源
  preventRecursion:{ default: false },     // 这条激活后不触发下一轮递归
  delayUntilRecursion: { default: 0 },     // 延迟到第 N 轮递归才生效（分级触发）
  scanDepth:       { default: null },      // 本条自定义扫描深度，null 用全局
  caseSensitive:   { default: null },      // 覆盖全局大小写
  matchWholeWords: { default: null },
  group / groupOverride / groupWeight: {...}, // 同组互斥/加权
  sticky:          { default: null },       // 时序效应：激活后粘住 N 轮
  cooldown:        { default: null },      // 冷却 N 轮才能再激活
  delay:           { default: null },      // 延迟 N 轮才首次激活
};
```

#### 2.2 评分（`scoreKey`，真实逻辑）

对每个条目，拿扫描窗口里的文本数命中了几个主/副关键词，按逻辑算分：

```js
// 主关键词每命中一个 +1
for (const key of entry.key) if (this.matchKeys(bufferState, key, entry)) primaryScore++;
// 副关键词按 selectiveLogic 组合
case AND_ANY:  return primaryScore + secondaryScore;            // 任一命中即叠加
case AND_ALL:  return secondaryScore === 副关键词总数
               ? primaryScore + secondaryScore : primaryScore; // 全部命中才叠加
```

没有主关键词命中 → 直接 0 分淘汰。`NOT_ALL`/`NOT_ANY` 是"否定触发"（正文里**不**出现某词才激活，适合写"别再提那件事"这类排除条目）。

#### 2.3 token 预算（核心公式）

```js
// checkWorldInfo() 开头
let budget = Math.round(world_info_budget * maxContext / 100) || 1;
// world_info_budget 默认 25（即上下文窗口的 25%），world_info_budget_cap 可硬封顶
if (world_info_budget_cap > 0 && budget > world_info_budget_cap) budget = world_info_budget_cap;
```

激活条目按 order 排序后逐条注入，**每加一条都重算 token，超预算就停**：

```js
if (!entry.ignoreBudget &&
    (textToScanTokens + await getTokenCountAsync(newContent)) >= budget) {
  token_budget_overflowed = true; break;   // 但 ignoreBudget=true 的条目仍可挤进来
}
```

#### 2.4 选择性递归（selective recursion）

激活的条目正文会被**追加进扫描缓冲**，触发下一轮扫描（用新注入的正文里的关键词再去激活别的条目），形成"链式召唤"：

```js
if (world_info_recursive && !token_budget_overflowed && successfulNewEntriesForRecursion.length) {
  nextScanState = scan_state.RECURSION;   // 用本轮激活条目的 content 再扫一轮
}
// 把本轮新激活条目的正文拼进 recurse buffer
const text = successfulNewEntriesForRecursion.map(x => x.content).join('\n');
buffer.addRecurse(text);
```

`delayUntilRecursion` 让条目"等到第 N 轮递归才醒"，实现多跳因果链；`max_recursion_steps` 防止无限递归（与 min_activations 互斥）。

#### 2.5 常驻/可选/恒显三种态

- **恒显（constant=true）**：不过关键词，永远激活，永远占预算（除非预算爆了且非 ignoreBudget）。
- **可选（constant=false）**：走关键词 + 概率 + 预算三道门。
- **sticky/cooldown/delay**：跨轮次的时序状态机（`WorldInfoTimedEffects`），让条目"激活后粘几轮/冷却几轮"，避免高频抖动。

#### 2.6 min activations（保底激活数）

`world_info_min_activations`（默认 0=关）：如果激活条目数不够 N，就**自动扩大扫描深度**（往前翻更多历史消息再扫），直到凑够 N 条或触顶 `world_info_min_activations_depth_max`。

---

### 第 3 层：向量记忆 / embedding recall（RAG）

#### 3.1 存储后端（`src/endpoints/vectors.js`，真实）

```js
import vectra from 'vectra';
// 每个 (source, collectionId, model) 一个独立文件夹
const pathToFile = path.join(directories.vectors, sanitize(source),
                             sanitize(collectionId), sanitize(model));
const store = new vectra.LocalIndex(pathToFile);
await store.createIndex();
// upsert：metadata 存 { hash, text, index }，hash 用消息文本字符串哈希去重
await store.upsertItem({ vector, metadata: { hash, text, index } });
```

关键点：**用消息文本的字符串哈希当主键去重**（`getStringHash(mes)`），编辑/重复消息不会重复入库。

#### 3.2 召回查询（`queryCollection`，真实阈值过滤）

```js
// POST /api/vector/query  body: { collectionId, searchText, topK, source, threshold }
const vector = await getVector(source, settings, searchText, /*isQuery*/ true, dirs);
const result = await store.queryItems(vector, topK);          // vectra 内部余弦 topK
const metadata = result.filter(x => x.score >= threshold)    // 再按分数阈值砍一刀
                        .map(x => x.item.metadata);
```

默认 `topK=10`、前端再用 `score_threshold=0.25` 过滤。多库查询 `multiQueryCollection` 会跨 collection 拉结果、**全局按 score 降序排、再统一阈值、再切 topK**。

#### 3.3 前端编排（`rearrangeChat`，这是最值得抄的设计）

它不是"把召回内容追加进 prompt"，而是**真的把命中的旧消息从对话数组里抽出来，重插到指定 depth**：

```js
const settings = {
  template: 'Past events:\n{{text}}',
  depth: 2,            // 召回内容插在离最新消息第 2 层
  protect: 5,          // 最近 5 条受保护，绝不参与召回/抽出
  insert: 3,          // 召回 topK=3 条
  query: 2,           // 用最近 2 条消息反向拼查询文本
  score_threshold: 0.25,
  message_chunk_size: 400,
};

async function rearrangeChat(chat, ...) {
  const queryText = await getQueryText(chat, 'chat');   // 最近 query 条消息倒序拼接
  const queryResults = await queryCollection(chatId, queryText, settings.insert);
  const queryHashes = queryResults.hashes;
  const retainMessages = chat.slice(-settings.protect); // 尾巴 5 条不动
  // 命中且不在保护尾巴里的消息 → 收集成 queriedMessages
  // 再从原 chat 数组里 splice 删掉
  for (const message of chat) if (queriedMessages.includes(message)) chat.splice(...);
  // 拼成 "名字: 正文" 块，套 template，插到 depth=2
  const insertedText = getPromptText(queriedMessages);
  setExtensionPrompt(EXTENSION_PROMPT_TAG, insertedText, settings.position, settings.depth, ...);
}
```

查询文本构造 `getQueryText`：**取最近 `query`(=2) 条消息、倒序、拼一起**当 query（不是拿用户最后一句，而是最近两句的滑动窗口）。

#### 3.4 长消息先摘要再入库

```js
summarize: false,
summary_threshold: 200,   // 正文 >=200 字符才先摘要
summary_prompt: 'Ignore previous instructions. Summarize the most important parts '
              + 'of the message. Limit yourself to 250 words or less. Your response '
              + 'should include nothing but the summary.',
summary_retries: 2,
```

入库时若消息超长，先调 LLM 压成 ≤250 词摘要，再把摘要拿去 embedding（`cachedSummaries` 按 hash 缓存）。

---

### 第 4 层：摘要 / 长期记忆（summarization）

在 `public/scripts/extensions/memory/index.js`，这是"压缩旧对话"的一层。

#### 4.1 触发条件（`getSummaryPromptForNow`，真实阈值）

```js
const defaultSettings = {
  promptWords: 200,          // 目标摘要 ≤200 词
  promptInterval: 10,        // 每满 10 条消息触发一次摘要
  promptForceWords: 0,       // 或攒够 N 个词才触发（0=不启用词数门）
  depth: 2,                  // 摘要注入深度
  template: '[Summary: {{summary}}]',
  source: 'extras',          // 摘要走哪个 LLM（extras/main/webllm）
};
// 判断：从上次摘要那条消息往后数
let messagesSinceLastSummary = 0, wordsSinceLastSummary = 0;
if (messagesSinceLastSummary >= extension_settings.memory.promptInterval
    && (!promptForceWords || wordsSinceLastSummary >= promptForceWords)) {
  // 触发摘要
}
```

即**双门触发**：消息数门（默认 10 条）AND 词数门（可选）。

#### 4.2 摘要 prompt（真实默认 prompt）

```js
const defaultPrompt = 'Ignore previous instructions. Summarize the most important facts '
  + 'and events in the story so far. If a summary already exists in your memory, use that '
  + 'as a base and expand with new facts. Limit the summary to {{words}} words or less. '
  + 'Your response should include nothing but the summary.';
```

关键：**"如果已有摘要，以它为基础增量扩展"**——增量式滚动摘要，不是每次重算全量。

#### 4.3 摘要喂什么、存哪

`getRawSummaryPrompt`：把"上一份摘要 + 上次摘要后到现在的消息"反向拼成 buffer，**按目标上下文窗口（`getSourceContextSize`，webllm 取 maxContext*0.75）截断**，喂给摘要 LLM：

```js
// bufferString = latestSummary + 反向最近消息（按 token 预算裁断）
// 摘要结果写进"倒数第二条消息"的 extra.memory
mes.extra.memory = value;
// 同时 setExtensionPrompt('1_memory', formatMemoryValue(value), position, depth=2, scan, SYSTEM)
```

**摘要持久化在消息对象的 `extra.memory` 字段里**（跟聊天存档一起存），不是独立数据库。下次摘要时 `getLatestMemoryFromChat` 从聊天尾部反向扫出最近一条带 `extra.memory` 的消息，作为增量基础。

---

## 四层协同与 token 预算（反推）

一次生成时，SillyTavern 按这个顺序从大到小占用上下文（数字=depth，越大越靠上=越像"设定"）：

```
[depth 高]
  角色卡 description/personality/scenario      ← 第1层 常驻锚，占满顶部
  世界书 @depth 条目（DEFAULT_DEPTH=4）        ← 第2层 按 order 排，预算=maxContext*25%
  摘要 [Summary: ...] @depth=2               ← 第4层 滚动增量摘要
  向量召回 Past events @depth=2               ← 第3层 topK=3，从对话里抽出来插这
  资料库 chunks @depth=4 (SYSTEM)             ← 第3层扩展
[depth 低]
  最近 protect=5 条原始对话（原样保留）        ← 不召回、不压缩
  最新一条用户消息
```

预算分配的真实逻辑：

1. **先算总账** `maxContext`（模型上下文窗口扣掉预留回复）。
2. **世界书独占一块**：`budget = round(25% * maxContext)`，可被 `world_info_budget_cap` 硬封顶。爆预算后非 `ignoreBudget` 条目全停。
3. **摘要层**走"目标词数"自管（默认 200 词 ≈ 几百 token），不抢世界书预算；它靠 `promptInterval=10` 攒够才生成，是**离线异步**压旧对话，把旧消息腾出来。
4. **向量层** `insert=3` 条硬上限 + `score_threshold=0.25`，召回条数少，token 占用可控；`protect=5` 保证最近 5 条原文永远在。
5. **世界书扫描文本**反过来包含第 1 层（角色卡 6 个 match 开关）+ 最近 N 条对话，实现"人设锚 ↔ 动态条目"双向联动。

协同精髓：**常驻层管"不变的基线"，世界书管"按当下剧情激活的知识点"，向量层管"跨剧情但当下相关的往事"，摘要层管"把太久远的事压成一句话"**。四层用同一个 depth 坐标体系叠放。

---

## LoveAI 可直接搬什么（映射到现有模块）

> 目标文件：`services\memory_service.py`、`services\direction_compass.py`。以下为建议的新函数签名 / 数据结构，可直接落地。

### P0 立刻抄

**1. 世界书条目数据结构 → `memory_service.py` 新增 `LoreEntry` dataclass**

```python
@dataclass
class LoreEntry:
    keys: list[str]                 # 主关键词
    secondary_keys: list[str] = []
    content: str = ''               # 注入正文
    constant: bool = False          # 恒显
    selective_logic: int = 0        # 0=AND_ANY 1=NOT_ALL 2=NOT_ANY 3=AND_ALL
    order: int = 100                # 排序权重
    depth: int = 4                  # 注入深度
    probability: int = 100
    ignore_budget: bool = False
    sticky: int | None = None       # 激活后粘 N 轮
    cooldown: int | None = None
```

对应关系：LoveAI 的"关系卡点标签/互动规则"完全可以做成 LoreEntry——比如"对方提到前任"这条 key 命中 → 注入"此刻对方处于比较型焦虑"的关系侧写。

**2. 预算公式直接抄**

```python
def wi_budget(max_context: int, budget_pct: int = 25, cap: int = 0) -> int:
    budget = round(max_context * budget_pct / 100) or 1
    if cap > 0: budget = min(budget, cap)
    return budget
# 注入循环里：逐条加 token，超预算停，但 ignore_budget 的条目豁免
```

**3. 增量滚动摘要 prompt（原样可用）**

```
Ignore previous instructions. Summarize the most important facts and events so far.
If a summary already exists in your memory, use that as a base and expand with new facts.
Limit the summary to {{words}} words or less. Your response should include nothing but the summary.
```
→ `memory_service.py` 新增 `roll_summary(old_summary: str, new_messages: list[str], words: int=200) -> str`，触发门：每 `prompt_interval=10` 条消息（LoveAI 可调到 6~8）。摘要存消息对象 `extra['summary']`，下次反向扫最近一条带 summary 的当增量基座。

### P1 值得搬

**4. 向量召回的"抽出-重插"而非"追加"**

```python
# direction_compass.py 或 memory_service.py
def recall_and_relocate(chat: list[dict], query: str,
                        top_k: int = 3, protect: int = 5,
                        score_threshold: float = 0.25, depth: int = 2):
    # 1. query = 最近 query(=2) 条消息倒序拼接（不是单取最后一句）
    # 2. 向量库 topK，再 filter score >= 0.25
    # 3. 命中消息从 chat 里 splice 删掉（protect 尾巴 5 条不动）
    # 4. 套 template "Past events:\n{{text}}" 插到 depth=2
```
关键抄点：**`protect=5` 保护最近 N 条原文不被召回抽走** + **`score_threshold=0.25` 硬过滤** + **用最近 2 条滑动窗口当 query**。

**5. 字符串哈希去重入库**：`hash(mes)` 当主键，编辑过的消息重算 hash 重入库，不重复。

**6. 6 个 match 开关**：让"关系规则条目"可配置成只在匹配到"角色卡基线/当前人设/场景"时激活，而不是盲扫全部对话。

### P2 缓做

**7. selective recursion（链式召唤）+ delay_until_recursion 分级**：LoveAI 关系场景里"触发A卡点→激活B应对策略"的多跳逻辑可借鉴，但实现复杂，二期再上。

**8. sticky/cooldown/delay 时序状态机**：避免同一条关系规则高频抖动激活，二期加。

---

## 坏设计 / 避坑

1. **向量召回是"硬抽消息"**：`rearrangeChat` 直接 `chat.splice()` 删除原消息，若召回命中把一条**仍在保护窗附近**的消息抽走，会造成上下文断裂；SillyTavern 靠 `protect=5` 硬挡，但这个 magic number 对长对话偏短。LoveAI 别照搬 splice 删除，建议**软召回（注入副本）**而非物理删除，避免历史错位。
2. **score_threshold=0.25 是全局写死的手感值**，不同 embedding 模型（ada-002 vs mxbai vs bge-m3）分数分布差异巨大，0.25 换模型就失灵。LoveAI 必须按自己用的 embedding 模型重新标定阈值，别直接抄数字。
3. **摘要触发门 promptInterval=10 条**对快节奏情感对话太粗（10 条可能跨了好几个情绪转折点），容易把关键冲突压没。LoveAI 建议降到 6~8 条，或加"情绪剧变事件强制触发摘要"。
4. **世界书预算上限 `world_info_budget > 100 就强制重置回 25`**（源码里有这段 `if (world_info_budget > 100) world_info_budget = 25`），是个硬编码怪癖，LoveAI 不要抄这个粗暴重置。
5. **vectra 是纯文件 JSON 索引**，数据量上万条后查询/写入性能一般，且无增量持久化优化。LoveAI 若消息量大，建议直接用 Chroma/SQLite-vss，只抄它的"hash 主键 + score 过滤 + topK"逻辑，不抄存储层。
6. **AGPL v3.0 传染性**：SillyTavern 整仓 AGPL，代码思路/算法可抄，但**不能直接复制其 JS 源码进 LoveAI**（会传染协议）。只搬设计与数据结构，用 Python 重写。

---

## 优先级初判

| 优先级 | 项 | 理由 | 预估人日 |
|---|---|---|---|
| **P0** | LoreEntry 数据结构 + 预算公式 + 增量滚动摘要 prompt | 直接撑起 LoveAI"关系基线常驻 + 旧对话压缩"两大刚需，纯逻辑无外部依赖 | 2 人日 |
| **P0** | 摘要双门触发（interval + 词数）+ extra.memory 持久化 | 风险低、收益立竿见影 | 1 人日 |
| **P1** | 向量软召回（topK + 0.25 阈值 + protect + 滑动 query） | 需先定 embedding 模型并重标定阈值；用软召回替代物理 splice | 2~3 人日 |
| **P1** | 字符串哈希去重 + 长消息先摘要再 embedding | 配合召回层一起上 | 0.5 人日 |
| **P2** | selective recursion 链式召唤 + delay_until_recursion | 多跳关系因果链，二期深化才需要 | 2 人日 |
| **P2** | sticky/cooldown/delay 时序状态机 | 防抖动，体验优化项 | 1.5 人日 |

**P0 合计 ≈ 3 人日**，可在不引入向量库的前提下先把"常驻层 + 摘要层"落地，立即改善长对话记忆衰减问题。
