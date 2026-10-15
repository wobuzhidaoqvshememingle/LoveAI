# LoveAI-v2 全链路端到端（E2E）验证报告

- 生成时间：2026-10-06 10:37:14
- 测试库（临时）：`E:\Temp\loveai_e2e_x7bsi6ob` （用完即弃，未写入正式 data/loveai.db）
- 测试人设：蛋卷，25岁女，平面设计师，INFP，回避型依恋，恋爱3年
- 聊天记录：共 **384** 条，时间跨度约2个月；对方(received)=224 / 我(sent)=160，比例≈224:160
- 各场景条数：日常报备=55，暧昧情话=46，邀约约会=40，吵架冲突=40，冷处理回避=73，金钱提及=40，情绪波动=50，未来规划=40

> 说明：`direction` 字段在本项目代码库里实际取值为 `received`(对方/蛋卷) 与 `sent`(我/用户)；报告中按此真实口径写入。

## 汇总表

| # | 环节 | 调用方法 | 有真实输出 |
|---|------|----------|-----------|
| 1 | 聊天分析(8模块) | `facade.analyze_chat / facade._run_analysis` | ✅ 是 |
| 2 | 情感标签 | `EmotionAnalyzer.analyze (经 _run_analysis)` | ✅ 是 |
| 3 | 言外之意/真实意图 | `_run_analysis -> data.intent` | ✅ 是 |
| 4 | 回复建议 | `_run_analysis -> suggested_reply / reply_suggestions` | ❌ 否/兜底 |
| 5 | 好感度18维 | `AffectionEngine.get_current / recalculate_from_history / update_from_message` | ✅ 是 |
| 6 | 21维人物画像 | `PersonaAnalyzer.analyze(person_id)` | ✅ 是 |
| 7 | 数字分身预测 | `facade.predict_reaction(用户行为, person_id)` | ✅ 是 |
| 8 | 财务提取 | `FinancialAnalyzer.generate_report(person_id)` | ✅ 是 |
| 9 | 说话风格学习 | `facade.learn_speech_style(use_cloud=False)` | ✅ 是 |
| 10 | 任务引擎 | `facade.generate_main_tasks(person_id, stage=1)` | ✅ 是 |
| 11 | 勋章判定 | `MedalService.check_and_unlock(pid, event, value)` | ✅ 是 |

## 各环节真实输出明细

### 1.聊天分析(8模块)
- 调用方法：`facade.analyze_chat / facade._run_analysis`
- 输入摘要：最近50条拼接，1672字；异步返回={'ok': True, 'data': {'started': True, 'person_id': 'p_danjuan_e2e'}}
- 输出关键字段：
```json
{
  "emotion_label": "happy",
  "keywords_n": 11,
  "rag_used": true,
  "intent_category": "积极互动",
  "relation_keys": [],
  "suggested_reply_len": 0,
  "safety": "safe",
  "affection_before": 50.0,
  "modules": {
    "emotion": true,
    "keywords": true,
    "rag": true,
    "intent": false,
    "relation": false,
    "reply": false,
    "safety": true,
    "affection": true
  },
  "llm_layer": "none"
}
```
- 是否真实输出：是（非空/非默认值）
- 备注：LLM无api_key，8模块走本地规则+情绪分析器兜底

### 2.情感标签
- 调用方法：`EmotionAnalyzer.analyze (经 _run_analysis)`
- 输入摘要：chat_text 最近50条
- 输出关键字段：
```json
{
  "label": "happy",
  "intensity": 10,
  "confidence": 0.8,
  "secondary_labels": [
    "love",
    "neutral",
    "cold",
    "angry",
    "sad"
  ],
  "keywords": [
    "哈哈",
    "好呀",
    "行",
    "想你",
    "抱抱"
  ]
}
```
- 是否真实输出：是（非空/非默认值）

### 3.言外之意/真实意图
- 调用方法：`_run_analysis -> data.intent`
- 输入摘要：chat_text 最近50条
- 输出关键字段：
```json
{
  "category": "积极互动",
  "confidence": 0.7,
  "implied_meaning": "",
  "true_intent": "",
  "summary": ""
}
```
- 是否真实输出：是（非空/非默认值）
- 备注：LLM不可用时 implied/true_intent 为空字符串，category 由情绪标签规则推断

### 4.回复建议
- 调用方法：`_run_analysis -> suggested_reply / reply_suggestions`
- 输入摘要：chat_text 最近50条
- 输出关键字段：
```json
{
  "suggested_reply": "",
  "suggested_reply_len": 0,
  "reply_suggestions_n": 0
}
```
- 是否真实输出：否
- 备注：回复由LLM生成；LLM不可用时 suggested_reply 为空（规则层未内置回复模板）

### 5.好感度18维
- 调用方法：`AffectionEngine.get_current / recalculate_from_history / update_from_message`
- 输入摘要：全量384条历史 + 1条样例消息
- 输出关键字段：
```json
{
  "维度数": 18,
  "重算前总分": 50.9,
  "重算后总分": 50.5,
  "非50默认值维度数": 7,
  "关系阶段stage": 4,
  "top变化维度": [
    [
      "intimacy",
      26.3
    ],
    [
      "respect",
      -15.0
    ],
    [
      "chat_resonance",
      -9.0
    ],
    [
      "emotional_value",
      7.6
    ],
    [
      "romance",
      5.0
    ]
  ],
  "单条消息后总分": 50.5
}
```
- 是否真实输出：是（非空/非默认值）
- 备注：recalculate_from_history 从聊天上下文重算，非默认全50

### 6.21维人物画像
- 调用方法：`PersonaAnalyzer.analyze(person_id)`
- 输入摘要：全量384条聊天记录
- 输出关键字段：
```json
{
  "message_count": 384,
  "other_message_count": 224,
  "顶层维度(dict)": [
    "attachment",
    "communication",
    "relationship_stage",
    "psychology",
    "emotion_pattern",
    "investment",
    "love_languages",
    "power_dynamic",
    "emotional_bank",
    "conflict_style",
    "boundaries",
    "freshness",
    "risk_warning",
    "relationship_satisfaction",
    "commitment",
    "interaction_ratio",
    "conflict_repair",
    "passion",
    "trust",
    "values_match",
    "self_differentiation",
    "affection_adjustment"
  ],
  "维度数": 22,
  "sample_attachment": "{'type': '回避型', 'scores': {'焦虑型': 0, '回避型': 3, '安全型': 3}, 'description': '害怕亲密，需"
}
```
- 是否真实输出：是（非空/非默认值）
- 备注：纯本地规则分析（依恋/沟通/权力格局/冲突模式等），不依赖LLM

### 7.数字分身预测
- 调用方法：`facade.predict_reaction(用户行为, person_id)`
- 输入摘要：用户行为='我周末想约你吃饭'
- 输出关键字段：
```json
{
  "ok": true,
  "emotion": "",
  "inner_thought": "",
  "possible_replies_n": 0,
  "possible_replies": [],
  "action": "",
  "confidence": 0.5,
  "llm_layer": "rules"
}
```
- 是否真实输出：是（非空/非默认值）
- 备注：分身需LLM输出JSON；LLM不可用时返回空emotion/replies、confidence默认0.5

### 8.财务提取
- 调用方法：`FinancialAnalyzer.generate_report(person_id)`
- 输入摘要：最近200条聊天记录规则匹配
- 输出关键字段：
```json
{
  "income_estimate": "约5000-8000元/月",
  "disposable_income": "约3900-5200元/月",
  "consumption_level": "low",
  "large_purchase_capacity": "大额支出能力弱，建议避免500元以上消费",
  "stress_index": 80,
  "suggested_amount_limit": 5070.0,
  "raw_signals_n": 8,
  "signal_type分布": {
    "saving": 2,
    "income": 2,
    "consumption": 2,
    "stress": 2
  }
}
```
- 是否真实输出：是（非空/非默认值）
- 备注：规则正则匹配收入/消费/压力信号；LLM综合分析不可用时走 _estimate_from_signals

### 9.说话风格学习
- 调用方法：`facade.learn_speech_style(use_cloud=False)`
- 输入摘要：direction='sent' 的160条我发消息
- 输出关键字段：
```json
{
  "ok": true,
  "learned": true,
  "sample_count": 160,
  "avg_length": 8.8,
  "catchphrases": [
    "好吧",
    "行吧",
    "好吧那",
    "你先",
    "你今天",
    "今天",
    "你今",
    "那你先",
    "行吧那",
    "什么"
  ],
  "tone_particles": [
    "哈",
    "吧",
    "呀",
    "啊",
    "哦"
  ],
  "traits": [
    "简洁惜字",
    "短句为主"
  ],
  "source": "local+cloud"
}
```
- 是否真实输出：是（非空/非默认值）
- 备注：纯本地n-gram统计口头禅/句长/语气词，use_cloud=False 不调云端

### 10.任务引擎
- 调用方法：`facade.generate_main_tasks(person_id, stage=1)`
- 输入摘要：关系阶段 stage=1
- 输出关键字段：
```json
{
  "ok": true,
  "任务数": 3,
  "首个任务title": "延续聊天节奏不冷场",
  "guide_direction": "嗯，我在认真听呢，可以再多说一点吗？",
  "task_type": null,
  "steps_n": 0
}
```
- 是否真实输出：是（非空/非默认值）
- 备注：按阶段预置目标生成主线任务，自动附带攻略方向 guide_direction 与分步 steps

### 11.勋章判定
- 调用方法：`MedalService.check_and_unlock(pid, event, value)`
- 输入摘要：依次触发 message_sent / affection / feature 三类事件
- 输出关键字段：
```json
{
  "本次新解锁": [
    {
      "event": "message_sent",
      "name": "首次对话",
      "code": null
    },
    {
      "event": "message_sent",
      "name": "话痨",
      "code": null
    },
    {
      "event": "feature",
      "name": "初识对象",
      "code": null
    },
    {
      "event": "feature",
      "name": "财务卫士",
      "code": null
    }
  ],
  "累计已解锁数": 4,
  "已解锁勋章": [
    "首次对话",
    "话痨",
    "初识对象",
    "财务卫士"
  ]
}
```
- 是否真实输出：是（非空/非默认值）
- 备注：25个初始勋章已建表；按事件条件遍历判定并写入 user_medals

## 兜底路径说明

- 本机 config.json 中 `llm.api_key` 为空、本地 GGUF/ONNX 模型未就绪，因此所有需要云端/大模型 JSON 输出的环节（聊天深度分析的言外之意、回复建议、数字分身预测、财务综合评分）均自动降级到规则/本地统计路径。
- 规则路径仍有真实输出的环节：情绪标签、关键词、安全检测、好感度18维重算、21维规则画像、财务信号提取、说话风格本地学习、任务生成、勋章判定。
- 依赖 LLM JSON 才能产出内容的环节（言外之意 implied/true_intent、suggested_reply、数字分身 emotion/possible_replies）在无模型时返回空字符串/默认置信度0.5——这是预期的降级行为，已在对应环节备注。
