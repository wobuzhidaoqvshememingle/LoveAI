# 02 · AIta（关系卡点诊断）+ PsyDI（渐进式人格探测）代码级深扒

> 扒取日期：2026-10-09
> 范围：只读源码，不改业务代码。所有函数名/阈值/公式均来自下面列出的真实文件，未找到公开仓库才会用替代项目——本次两个目标项目**都找到了真实公开仓库**，无需替代。

---

## 一、两个项目各自的仓库定位

### 1. AIta —— 关系卡点诊断（目标项目，真实存在）

- **仓库**：https://github.com/rd-0701/aita-chat-assistant
- **许可证**：MIT
- **形态**：纯前端单文件 `index.html`（约 530KB / 9500+ 行原生 JS，无框架），本地规则引擎为主 + 可选 LLM 增强（6 家厂商 OpenAI 兼容协议）。Node 后端 `server.js` 仅作 API 代理。
- **定位**：粘贴聊天记录 → 自动识别"我/对方" → 输出对方人格画像、MBTI、沟通风格仪表盘、5D 雷达图、**关系阶段判断**、情绪轨迹，以及围绕"关系卡点"的线上/线下策略。
- **作者自述（TRAE 大赛帖 forum.trae.cn/t/topic/69130）**：卡点共 7 类——回避型 / 简短回复 / 反讽 / 内敛 / 浅层外向 / 亲密信号 / 平稳型，策略围绕"卡点—根因—突破—规避"四段展开。
- **核心代码位置**：全部逻辑在 `index.html` 内联 `<script>`。关键函数（README 函数表 + 源码实读核对一致）：
  - 角色识别 `parseChatMessages`
  - 风格画像 `analyzeSpeaker` / `analyzeUserStyle`（15 维）
  - 情绪 `analyzeSentimentDeep` / `emotionShift`
  - 意图/反讽 `detectIntent` / `detectSarcasm`
  - 建议 `generateLocalSuggestion` / `styleMessage` / `contextAwareAdjust`
  - 安全 `detectCrisis` / `critiqueSuggestion`
  - AI 归一化 `normalizeAIAnalysis` / `normalizeAISuggestion`

### 2. PsyDI —— 渐进式人格探测（目标项目，真实存在）

- **仓库**：https://github.com/opendilab/PsyDI （OpenDILab / 上海人工智能实验室）
- **许可证**：Apache-2.0
- **论文**：arXiv:2408.03337《PsyDI: Towards a Personalized and Progressively In-depth Chatbot for Psychological Measurements》
- **形态**：线上是 Next.js 前端（`app/`、`components/`）+ Python 后端；但**真正的探测算法核心在 `backend/` 的 mini pipeline**，可独立跑：
  - `backend/psydi_mini_agent.py` —— 状态机主循环
  - `backend/reward_model_inference.py` —— 评分表（初始化 / 更新 / 选题）算法
  - `backend/prompt.txt` —— 咨询师 persona + 出题规则
  - `backend/llm.py` —— LLM 调用封装
- **定位**：MBTI 渐进探测。用户先丢几条"动态"，系统建一张 16 型概率表，然后**针对当前 Top-2 最纠结的两个类型追问**，多轮问答后收敛出 MBTI。

---

## 二、AIta 卡点诊断：输入特征 → 判定算法 → 输出分类

### 2.1 输入特征（喂给诊断的原始信号）

先由 `parseChatMessages` 把聊天记录切成 `myMsgs / targetMsgs` 两路，剥掉 `[14:30]` 时间戳、过滤"撤回/已读/正在输入"系统消息。然后聚合成以下统计量（源码实读）：

- `tCount`：对方消息条数
- `tStats.avgLength`：对方平均字数；`laughterCount`（哈哈/嘿嘿次数）；`hasEmoji`；`selfDisclosure`（自我暴露次数）；`followUps`（追问次数）；`topics`（话题聚类前几个）；`sarcasmMark`（反讽信号计数）
- 子评分：`tIntimacy.score / markers`（亲密度）、`tEngagement.score / followUps`（投入度）、`tSentiment.score`（情绪）、`tAvoidance.score`（回避度）
- `tStrongPosEmoji / tIntimateEmoji`：强正向/亲密表情计数
- `dynamics.targetDominance`（对方主动发起占比 0~1）、`targetInitiated`、`targetBursts`（爆发式长回复）
- `userInfo.relationship / knownDuration`：用户自报关系与认识时长（**允许外部先验覆盖纯文本推断**）

### 2.2 关系阶段判定：一个加权分 + 级联 if/else

**第一步：爱称/稳定关系硬识别（最高优先级，覆盖模板）**

```js
// COUPLE_TERMS 命中时排除"你老婆/他媳妇"这种第三方提及
const isAddressedAsCouple = (text, term) => {
  const idx = text.indexOf(term);
  if (idx < 0) return false;
  const before = text.substring(Math.max(0, idx - 2), idx);
  if (/[你他她]的?$/.test(before)) return false;  // 称谓前接你/他/她 = 提第三方
  return true;
};
const coupleTermsUsed = tCoupleTermHits.length>0 || uCoupleTermHits.length>0;
const isStableRelation = coupleTermsUsed
  || /夫妻|伴侣|情侣|恋人|老公|老婆|对象/.test(relationship);
```

**第二步：warmthScore 加权公式（源码原文）**

```js
const warmthScore = (
    tIntimacy.score
  + tEngagement.score
  + (tStrongPosEmoji + tIntimateEmoji) * 5
  + (tStats.laughterCount > 0 ? 15 : 0)
  + tSentiment.score * 0.3
  + dynamics.targetDominance * 20
) / 4;
```

**第三步：关系阶段级联（从上到下，命中即定）**

```js
if (tCount === 0)                     -> "未识别到对方消息"
else if (isStableRelation)            -> "稳定期"（爱称/自报关系，直接覆盖）
else if (warmthScore > 55)            -> "升温阶段"（互动积极/有亲密表达）
else if (avgLength>12 || followUps>0) -> "互相了解上升期"
else if (avgLength < 6)               -> "礼貌回应阶段"
else                                  -> "相互试探"（兜底）
```

**依恋类型级联（阈值全部拍死在代码里）**

```js
if (tIntimacy.score>50 && tAvoidance.score<30) -> 安全型
else if (tAvoidance.score > 45)                 -> 回避型
else if (tIntimacy.score>40 && tSentiment<40)   -> 焦虑型
else                                            -> 偏向安全型
```

### 2.3 核心：7 类"关系卡点"诊断（优先级命中制）

每个卡点输出一个结构化对象 `diag = {type, challenge, rootCause, breakthrough, avoid}`，后面所有线上/线下/时机/禁忌策略**全部围绕这一个 diag 展开**。阈值与顺序（源码原文）：

```js
const isAvoidant       = tAvoidance.score > 40;
const isShortReplier   = tStats.avgLength < 8;   // 注意：阶段判定用<6，卡点点用<8
const isIntrovert      = extroversion < 45;
const isExtrovert      = extroversion >= 55;
// 级联顺序 = 优先级，先命中先定调：
if      (isAvoidant)          -> type:'avoidant'        回避型：越追越逃，把"对话"变"留言"
else if (avgLength<6)         -> type:'shortReplier'    惜字如金：别用字数判兴趣，看回复速度/主动发起
else if (sarcasmMark>0)       -> type:'sarcasm'        敷衍信号：立刻停当前话题，换 TA 感兴趣的重启
else if (isIntrovert)         -> type:'introvert'      内敛：用"分享自己"代替"连环提问"
else if (isExtrovert && followUps==0) -> type:'shallowExtrovert'  热闹但不深：追问"那你当时什么感受"
else if (tIntimateEmoji>0)    -> type:'intimate'       已发亲密信号：接暧昧但不急于表白
else                          -> type:'plain'          平稳无记忆点：制造"只有你们懂的梗"
```

每个 diag 四件套（以 avoidant 为例）：
- `challenge`：`${nickname}有回避倾向——越靠近越想逃，你追得紧TA退得快`
- `rootCause`：`回避型的核心恐惧是"被吞噬感"，任何"你必须回应我"的压力都会触发防御`
- `breakthrough`：`把"对话"变成"留言"——你分享，TA接不接都行，消除回复压力后TA反而主动靠近`
- `avoid`：`追问、逼表态、连发消息、用"你怎么不理我"施压`

**分寸门控**（防止对陌生人给暧昧建议）：

```js
const isEarlyStage = /刚认识|陌生人|不久|几天|一周|两周/.test(knownDuration)
                  || /陌生人|刚认识/.test(relationship);
const isIntimateStage = /暧昧|恋爱|情侣|对象|男朋友|女朋友|老公|老婆|伴侣/.test(relationship);
```

### 2.4 它的"诊断"本质

AIta **没有机器学习模型**。所谓"诊断" = 一组**手工统计特征（平均字数、追问数、表情数、主动占比）→ 一组硬编码阈值 → 一棵固定优先级的 if/else 决策树 → 命中一个卡点模板 → 套模板话术**。warmthScore 是唯一的复合分，其余全是单点阈值。输出是面向用户的**解释性散文**，不是可复现的数值分类。

---

## 三、PsyDI 渐进探测：状态机 / 自适应下一题 / 停止条件 / 画像累积

### 3.1 状态机（`psydi_mini_agent.py` 实读）

```python
class PsyDIMiniAgent:
    def __init__(self):
        self.reward_model = RewardModel()      # 评分模型
        self.prompt = open('prompt.txt').read() # 咨询师 persona
        self.max_turn = 5

    def reset(self):
        self.table = None; self.history = []
        self.history.append({"role":"system","content":self.prompt})
        self.history.append({"role":"assistant","content":"你好！...请告诉我一个具体问题或情境..."})

    def run(self, init_post_list):
        self.init_table(init_post_list)                 # ① 建表
        answer = self.choose_post_to_ask(init_post_list)  # ② 选最纠结的一条动态
        count = 0; description = None
        while count < self.max_turn:                    # ③ 探测主循环
            question = self.get_question(answer)        #    LLM 出下一题
            if re.search(r'自我描述[】:：\t\*]*\s*(.*?)(?=\.|$)', question):
                description = question                  #    ④ 停止信号：LLM 自己喊"我够了"
                break
            count += 1
            answer = input(...)                         #    用户作答
        self.update_table(description or question)      # ⑤ 把整轮 Q&A 合成一段"新动态"回灌评分表
```

### 3.2 评分表怎么建 / 怎么更新（`reward_model_inference.py` 核心算法）

**四个维度先映射成 ±1 向量**：E/I、N/S、F/T、J/P。

```python
self.ei_dict = {'E':1,'I':-1}; self.ns_dict={'N':1,'S':-1}
self.ft_dict = {'F':1,'T':-1}; self.jp_dict={'J':1,'P':-1}
# LlamaForSequenceClassification(num_labels=4) 输出 4 个 logit，对应四个维度
```

**单条动态打分**：模型对"判断该动态用户多大概率是某 MBTI"输出 4 维 logit → 点乘该类型的 ±1 标签向量 → 取均值 → 再用该类型**预先统计好的均值/标准差做正态 CDF 归一化**：

```python
reward = (reward * t_label).mean().item()
mean, std = self.value_stat[mbti]            # 16 个类型的 mean/std 全部硬编码在代码里
reward = norm.cdf(reward, loc=mean, scale=std)
```

**初始化表**：对每条初始动态、对 16 个类型各打一次分，跨动态取均值后线性拉伸到 `[20, 50]`：

```python
type_table[mbti] = np.interp(rewards.mean(), [0,1], [20,50])
```

**更新表（关键：自适应学习率）**——新证据不会等权覆盖旧判断，而是**越确信的类型，权重越小**，防止反复横跳：

```python
def update_function(self, x):   # x = 旧分数(0~100)
    # 分段线性：x=50 → weight=25；x=80 → weight=10；再 clamp 到 [3,60]
    ...
def update_table(self, new_post, type_table):
    for mbti in ...:
        reward = ... # 新动态对该类型的 CDF 概率
        weight = self.update_function(old_reward)
        new_reward = 2 * weight * reward - weight
        type_table[mbti] = min(max(0, old_reward + new_reward), 100)
```

含义：初始模糊（50 分附近）时单次证据可撬动 ~25 的调整；已经很确信（80 分）时单次只动 ~10；天花板 60、地板 3，保证收敛但不锁死。

### 3.3 自适应下一题：专挑"最纠结"的维度追问

```python
def choose_post_to_ask(self, post_list):
    sorted_items = sorted(self.table.items(), key=lambda kv: kv[1], reverse=True)
    top_mbti = [k for k,_ in sorted_items[:2]]     # 当前领先的两个类型
    return self.reward_model.select_post(post_list, self.post_raw_reward, top_mbti)

def select_post(self, post_list, post_raw_reward, top_mbti):
    # 找出 Top-2 两个类型在哪个维度上不同（如 INFP vs ISFJ → [0,1,0,1]）
    diff_index = [1 if top_mbti[0][i]!=top_mbti[1][i] else 0 for i in range(4)]
    # 把候选动态在两个类型下的原始分都排序求和，按"差异维度"加权，
    # 选那条最能在二者分歧维度上拉开差距的动态来追问
    ...rank_list = einsum(...diff_index)...
    return post_list[rank_index.pop()]
```

**探测逻辑一句话**：不问新问题，而是**回看用户已说过的话里，哪一条最能区分当前两个最可能的人格类型，就围绕它追问**。这就是"渐进式、聚焦歧义点"的工程实现。

### 3.4 出题 prompt（`prompt.txt` 实读，可直接借鉴的结构）

- 人设：**MBTI 认知分析咨询师**，精通荣格八维（te/ti/fe/fi/se/si/ne/ni）。
- 约束：一次只问一个问题；选项用 **ABCD 角色扮演第一人称**写（"如果是你，你会怎么做…"），四个选项分别对应不同认知功能，区分度要大；**多轮问题必须递进、不重复**；提问时先 `*猜测潜在动机*` 再 `**过渡到下一个问题**`。
- 停止：**问 2~4 轮就停**，停止后立即按固定格式输出：

```
【你的MBTI是】：INTP
【自我描述】：<把这几轮的题目+用户所选答案，串写成一段连贯的"我是一个怎样的人"的自述，一题不漏>
```

### 3.5 停止条件（双重）

1. **LLM 自发停止**：生成内容里命中正则 `自我描述【:：…】`，说明它认为信息够了，主动转成结论；
2. **硬上限**：`max_turn = 5`（prompt 里写的是 2~4 轮，代码上限 5，留缓冲）。
停止后把整段问答合成一条"新动态"回灌评分表完成画像累积。

---

## 四、反推：它们分别怎么解决"诊断"和"探测"

- **AIta 解决"诊断"（判定一段关系卡在哪个模式）**：
  它把"关系阶段/卡点"这种模糊判断**工程化为可计算特征**——平均字数、追问次数、亲密表情数、主动发起占比、爱称命中。再用一棵**带优先级的决策树**消歧：先验（爱称/用户自报关系）> 复合分（warmthScore）> 单点阈值（字数/回避分）> 兜底。卡点不是打分排序，而是**首命中分类**，且每个类别绑定一套现成的"根因—突破—禁忌"话术模板。优点是零模型、毫秒级、可解释；缺点是阈值拍脑袋、规则之间重叠靠顺序硬解。

- **PsyDI 解决"探测"（怎么一步步问出人格画像）**：
  它的核心不是"问得更多"，而是**"在信息增益最大的地方问"**。维护一张 16 型概率表，每轮找出 Top-2 最纠结的类型，只在二者**分歧的那个维度**上挑最有区分度的旧发言来追问；更新时用**自适应学习率**（越确信越难被新证据撬动）；停止由"LLM 自己判断信息已足 + 硬轮次上限"双重控制。本质是一个**针对歧义点的主动信息收集（active probing）状态机**，而不是固定问卷。

---

## 五、LoveAI 可直接搬什么（映射到现有 services）

> LoveAI-v2 现状核对：`psydi_probe.py` 已有 `next_best_question / probe_plan / _confidence_of / _unresolved_score`；`persona_analyzer.py` 已有 `_analyze_relationship_stage / _analyze_attachment / _analyze_love_languages`；`analysis_orchestrator.py` 已有 `_extract_relation_stage(context)->int`；`digital_twin.py` 已有 `_infer_attachment() / generate_profile()`。下面是把两个开源项目的具体结构接到这些现有接口上。

### 5.1 搬到 `services\analysis_orchestrator.py` / `persona_analyzer.py`

1. **新增卡点分类器函数**（照搬 AIta 的首命中决策树）：
   ```python
   def diagnose_stuck_point(stats: dict, sub: dict, dynamics: dict) -> dict:
       # stats: avg_length, follow_ups, laughter_count, sarcasm_mark, has_emoji
       # sub: intimacy, engagement, sentiment, avoidance, extroversion
       # dynamics: target_dominance, target_initiated
       # 返回 {"type": "avoidant"|"short_replier"|"sarcasm"|"introvert"
       #            |"shallow_extrovert"|"intimate"|"plain",
       #       "challenge":..., "root_cause":..., "breakthrough":..., "avoid":...}
   ```
   阈值起点直接用 AIta：`avoidance>40 → avoidant`；`avg_length<6 → short_replier`；`sarcasm_mark>0 → sarcasm`；`extroversion<45 → introvert`；`extroversion>=55 and follow_ups==0 → shallow_extrovert`；`intimate_emoji>0 → intimate`；兜底 `plain`。把 `persona_analyzer._analyze_relationship_stage` 的输出再喂给它，策略模块直接消费 `diag["breakthrough"/"avoid"]`。

2. **warmthScore 复合分替换现阶段分**：
   ```python
   warmth = (intimacy.score + engagement.score
             + (strong_pos_emoji+intimate_emoji)*5
             + (15 if laughter_count>0 else 0)
             + sentiment.score*0.3
             + target_dominance*20) / 4
   # 阶段级联：is_stable_relation(爱称/自报) > warmth>55 > avg_len>12|followup>0 > avg_len<6 > 兜底
   ```
   关键可搬细节：**爱称识别要排除"你老婆/他媳妇"第三方提及**（`isAddressedAsCouple` 里查称谓前一两个字是否为 你/他/她），否则会把"别人聊起你对象"误判成稳定期。

3. **分寸门控**：把 `is_early_stage / is_intimate_stage` 两个正则提到 orchestrator 层，早阶段一律不下发暧昧/推进类建议（AIta 的安全边界，零成本可搬）。

### 5.2 搬到 `services\psydi_probe.py`（已有骨架，补算法）

1. **自适应学习率更新表**（照搬 `update_function` + `update_table`）：
   ```python
   def _probe_weight(old_score: float) -> float:   # 旧分越确信，权重越小
       # x=50→w=25, x=80→w=10, clamp[3,60]，分段线性
   def update_profile_table(new_evidence: str, table: dict) -> dict:
       for k in table:
           w = _probe_weight(table[k])
           delta = 2*w*new_prob[k] - w
           table[k] = min(max(0, table[k]+delta), 100)
   ```
   这正好补现有 `_confidence_of / _unresolved_score` 缺的"累积平滑"——避免每条新证据都把画像推翻重来。

2. **下一题挑歧义点**（升级 `next_best_question`）：不再通用地"找 unresolved 分最高的维度"，而是**先取当前领先的两个画像假设，找出二者分歧的维度，再从已有聊天记录里挑最能区分二者的那条发言来追问**——对应 `choose_post_to_ask + select_post`。

3. **停止条件双闸**：现有 `probe_plan(n=3)` 写死轮数。改成 ①置信度收敛（Top1-Top2 差距超过阈值）或 LLM 输出结论标记就停；②硬上限 `max_turn=5`。

### 5.3 搬到 `services\digital_twin.py` / `character_card.py`

1. **画像输出格式**（借 PsyDI prompt 的收尾）：探测结束后不要只吐标签，而是把"题目+用户选择"**串写成一段第一人称自述**（`【自我描述】`思路），作为 `digital_twin.generate_profile` 的文字产物，直接喂 `character_card.generate_from_persona`。
2. **卡片合并的证据加权**：`character_card.merge_cards` 目前是覆盖式合并，可借 `update_table` 的"越确信权重越小"做**证据增量合并**，后到的弱证据不冲掉强证据。

---

## 六、坏设计 / 避坑（两个项目都有）

**AIta 的坑：**
1. **阈值全是拍脑袋**：`warmthScore>55`、`avgLength<6`、`avoidance>40`、`extroversion<45/55`……没有任何标定/回测依据，换一批聊天样本就失真。搬过来时**阈值必须做成可配置参数，并用 LoveAI 自己的聊天数据标定**，别照抄数字。
2. **决策树"先命中先定"= 任意消歧**：一个人可能既简短又回避，因为 avoidant 排在 shortReplier 前面就永远是回避型。重叠维度应该打分取加权最高，而不是 if/else 顺序。
3. **关键词表脆弱**：`COUPLE_TERMS / SENTIMENT_POSITIVE / INTIMACY_MARKERS` 是硬词表，作者自己就踩过"哈哈我失恋了"误判（哈哈被算正向词，没看是否同时有负面事件词）。中文谐音梗/网络梗必须配"负面事件词同时出现"的互斥规则。
4. **9500 行单文件**：不可维护、不可测试。只搬逻辑，绝不搬工程形态。
5. **输出是模板散文**：同一个卡点每次措辞几乎一样，不可复现、无分数留存。我们要保留结构化 `diag` 对象 + 数值证据，文案另算。

**PsyDI 的坑：**
1. **评分模型是私有重资产**：真正打分靠一个微调的 Llama 序列分类器（`OpenDILabCommunity/PsyDI-RM-v0.1-zh`），没这个模型时 `FakeRewardModel` 直接是 `init 全 0.5、不更新、选第一条`——**mini 版脱离了它的奖励模型就是个空壳状态机**。LoveAI 不要假设能白嫖它的 RM，我们要么用 LLM-as-judge 替代打分，要么自己训。
2. **`value_stat` 的 mean/std 是它自家数据的统计量**：16 个类型的分布参数硬编码死，换人群/换语言就偏移。搬 CDF 归一化思路可以，**那组数不能直接搬**。
3. **停止条件靠正则脆弱匹配 LLM 输出里的"自我描述："**：换模型、换 prompt 措辞就可能永远命不中，最后只能靠 max_turn 兜底。我们要用结构化 JSON 结论字段（如 `{"done":true,"mbti":...}`）替代文本正则。
4. **轮次口径不一致**：prompt 说"问 2~4 轮"，代码 `max_turn=5`，文档宣传"12~15 个问题"——三处对不上。说明连作者自己都没固定探测长度，我们要以"置信度收敛"为准，别照搬任何固定数字。
5. **MBTI 本身科学性存疑**：它的探测对象是 MBTI。LoveAI 要的是依恋/关系模式画像，搬的是"状态机+歧义点选题+自适应权重"这套**方法**，不要把 16 型标签体系一起搬过来。

---

## 七、优先级初判

| 级别 | 事项 | 落到哪个文件 | 理由 | 粗估人日 |
|---|---|---|---|---|
| **P0** | 卡点分类器 `diagnose_stuck_point`（7 类 + challenge/rootCause/breakthrough/avoid 四件套） | `persona_analyzer.py` 新增 / `analysis_orchestrator.py` 消费 | LoveAI 已有关系阶段与依恋分析，缺的就是"一句话点破卡在哪 + 对应打法"，这块直接决定产品价值，且零模型依赖可立刻上线 | 3 |
| **P0** | 探测自适应权重更新 + 歧义点下一题 | `psydi_probe.py` | 已有 `next_best_question/probe_plan` 骨架，补上"越确信越难撬动"和"专挑 Top-2 分歧维度追问"，是渐进探测的灵魂 | 2 |
| **P1** | warmthScore 复合分 + 爱称第三方排除 + 早晚阶段分寸门控 | `persona_analyzer._analyze_relationship_stage` / `analysis_orchestrator._extract_relation_stage` | 替换现有阶段分，提升准确率；爱称排除和分寸门控是已知会翻车的边界，必须一起做 | 1.5 |
| **P1** | 探测结束"Q&A 串写第一人称自述"输出 | `psydi_probe.py` → `digital_twin.generate_profile` → `character_card` | 让探测产物直接长成人格卡，打通探测→画像的最后一公里 | 1.5 |
| **P2** | 奖励模型式多维打分（4 维 logit + CDF 归一化） | 待定（需先有自己的标注数据） | 依赖自有标注/微调，短期用 LLM-as-judge 代替；等数据够了再上 | 5（暂缓） |
| **P2** | 反讽/危机短路、JSON 归一化层 | `safety_filter.py` / `llm_gateway.py` | LoveAI 已有 safety_filter，仅作参考对照，不新做 | 0.5（仅评审） |

**合计建议首期投入 P0+P1 ≈ 8 人日**即可把两套开源项目的核心思路落到 LoveAI；P2 的模型层等自有数据积累后再启动。
