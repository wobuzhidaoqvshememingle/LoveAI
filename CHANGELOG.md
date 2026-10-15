# LoveAI 更新日志

> 本项目版本号遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/) 与语义化版本（SemVer）。
> 每个版本对应一个 Git tag / GitHub Release（坐标：`wobuzhidaoqvshememingle/LoveAI`），最新版本见本文件顶部。
> 历史条目保持原样，不回溯改写；发布前的格式整理仅做最小必要修订。
>
> **⚠️ 版本编号勘误（2026-10-12 补记）**：本文曾把**需求批次号** R3.1 / R3.2 / R3.3 误当成软件版本号，写成 [3.1.0] / [3.2.0] / [3.3.0]（三条均为 2026-10-08）。
> 这使 [3.0.0]（2026-10-11，架构重构后的正式发版，也是 LoveAI_Setup_v3.0.0.exe 的真实版本号）置顶时出现"版本号小于下方条目、但日期更新"的 SemVer 倒挂。
> 处理方式：**下方条目正文与日期保持原样、不回溯改写**（遵循上一条原则），仅在标题后补注真实语义。
> 今后规则：发版一律走 SemVer（3.0.0 之后是 3.0.1 / 3.1.0…）；需求批次只用 R3.x 前缀登记在 `docs/功能需求文档.md`，**不再进入本文件标题**。

## [未发布] - 2026-10-13 怀疑链 v2 落地（微积分加权 + 引导语义）+ 旧说谎检测证伪

> 承接用户拍板：怀疑链从"验证/试探"转向"引导改变"，旧 deception_risk 高分结论经全量回放证伪为假阳性。

### 新增（怀疑链 v2）
- **微积分加权**：幂次加速 `Δ_eff = Δ × (1 + S/100)^1.5` + 指数时间衰减，**半衰期 180 天**。
- **持久化**：`write_back()` 写回 `persona_profile.json["suspicion_chain"]`。
- **接入**：已挂到 `persona_analyzer.analyze()`（附加字段，不改既有评分）。
- **应对语义转向**：由"验证"改为**"引导"**——不揭穿、不试探，引导改变（2026-10-13 用户拍板）。
- **分级阈值**：`<30 正常 / 30-60 观察 / 60-80 引导 / 80+ 设限`。

### 验证（全部真实运行）
- 单测 `test_suspicion_chain.py` **15 用例** + `persona_v2` 回归 **18** = **33 passed**。
- **9796 条消息全量回放实测**：整体怀疑度 30 = **观察**级；唯一背离点为 09-05「宝宝我想要钱」索取后回复延迟 **59→153 min**。

### 证伪（旧说谎检测）
- `deception_risk = 79.5` 为**假阳性**（深夜消息 / 省略号 / 标签堆叠加出来的分数），已证伪，不作为说谎结论依据。

### 说明
- 仅涉及怀疑链模块本身；boost_glide_planner.py、微信接入/gateway、两份交接文档零改动。

### 追补（2026-10-13 同日）
- 怀疑链新增 **over_explain 证据类型**（过度解释/信息修补：追问后回复长度>本人历史中位×2 且换话题/关键事实沉默）：within-person 基线、同追问会话只计一次、首见 delta=0 仅进待人工、重复≥3 才补小分、深夜 0-5 点排除、evidence 强制带"可能不成立"。
- 单测 test_suspicion_chain.py **15 → 22 passed**（证据类型 5 → 6 类）；+ persona_v2 18 = **40 passed**。
- 全量回归：**1686 passed / 3 failed**——3 个失败（test_cooldown_table、test_script_library_v2）经 git 核实为既有失败（与 free_model_router 5xx、脚本库内容相关），与本次改动无关，待排查。

### 追补2（2026-10-13 同日·全局判断架构）
- **全局判断架构 v1.1 落地**（设计文档：new-chat-1\全局判断架构设计.md）：三大范式——导航系统（终点恒定/时时定位/偏差检测/前瞻预测）、迂回变轨（路径非线性/绕行三要素/迂回服务终点）、现实主义四要素（概率化/冗余/预案/退路）。
- 组件：persona_analyzer `_trajectory_vector()`（全量轨迹特征）、services\pattern_library.py（6 案例，case_006「情绪压力过载→给空间」05-20 pre-cutoff 真实命中）、direction_compass `_navigation()`（9 导航字段+前瞻/阈值联动/方向历史，真实数据推导）、task_engine `_build_compass_snapshot` 快照 5→15 键真实接线。
- 评估集 11 事件 7 维度：改后命中率**持平**（方向类 80% 含06-12/75% 不含，未提升未下降）；归因/说谎逐数一致；05-14 唯一失分点为回测截断结构性结果（事件消息在 cutoff 之后，03:00 时刻无证据可判）。独立验证留痕：new-chat-1\_post_eval.md / _wire_delivery_report.md。
- 性能：suspicion parse_dt 193万次→每串一次（accumulate 37.9s→1.6s）；deception _parse_dt lru_cache（27.3s→13.2s）；analyze() 全量 62.9s→**9.47s**（独立验证，字段 44→48）。
- 测试：全量 **1698 passed / 0 failed**（3 个既有失败已修：cooldown 测试隔离零配置 provider、script_library 断言对齐多句 OR）；suspicion_chain 31 passed、persona 相关 92+49 passed。

### 追补3（2026-10-13 同日·待办登记）
- 待办：任务引擎动态路径规划、策略库多路径+间歇强化、事件链全量跨月串联、前瞻强化（依赖事件链）、S1/S2/S4 阶段信号、记忆不一致 NER 锚点、纯言语谎言语义真伪（能力边界）、现实主义概率校准（需真实事件积累）。
- 需求日志：new-chat-1\需求日志_20261013.md。

### 追补4（2026-10-13 同日·人格维度恢复与校准）
- **修复**：蛋卷画像输出层防误判过头致维度旁路——恢复人格障碍倾向分级输出（BPD/NPD/ASPD/HPD 倾向值+置信度+证据原文+状态；原则修正：防误判≠消维度）；校准闸门改分级降级（疏离回避型不再整维硬压）；新增 **personality 聚合块**（依恋×暗黑三角×CBT×防御×Big Five→综合标签+维度得分+置信度，类型分支全依恋类型通用）。
- **蛋卷实测**：ASPD 倾向=0.5/观察（命中"冲动无情"1次）；BPD/NPD/HPD=未见信号；综合性格=疏离回避型（avoid=80,anx=0,sec=47）+暗黑2类+CBT4类+Big Five E=70.2/A=80.5；profile 字段 55。
- **校准**：deception_risk **79.5/高 → 30/观察**（怀疑链对齐，合并口径）；warnings 4→2 条（删除"模式转变"与"45 次露馅"已证伪文案，仅留 09-05 真实证据对齐项）；psychology 全分值强制带证据原文（双向提取，如 操纵"你要是不"08-25、非黑即白"要么"06-12、煤气灯"你没用"09-27）。
- 测试 **37 passed** 零回归；备份 persona_analyzer.py.bak_calib_20261013_135000；正式库只读、红线零触碰。

### 追补5（2026-10-13 同日·psychology 文本分离修复）
- **修复**：_analyze_psychology 原用 all_text=other_text+my_text 混算——我方表达污染对方心理判断；改为对方维度只看 other_text（received），evidence 只收 received。
- **蛋卷实测（核心发现）**：修复后心理分值全部归零——操纵2→0、冲动无情1→0、非黑即白3→0、灾难化3→0、读心术1→0、应该陈述1→0、煤气灯1→0、ASPD 0.5→0/未见信号；原分值全部来自我方消息（我方抱怨/反应），非对方心理特征；对方文本（received 3675 条）对现有词表零命中。
- **待办**：心理词表按对方语料重校（现词表疑似按我方抱怨语境构建）。
- 磁盘画像已重写（72,991 B，四倾向全未见信号）；测试 37 passed 零回归；备份 persona_analyzer.py.bak_textsep_20261013_141224；正式库只读。

### 追补6（2026-10-13 同日·集中分拨架构改造）
- **架构改造**：网状→集中分拨中心混合架构（中心为主+数据池底座+读自治写受控），参考企业集团化管控选**战略管控型**（总部定口径/裁决/方向/权限，模块算法自主）。设计稿：new-chat-1\集中分拨架构设计.md（含集团化管控映射节）。
- **三阶段完成**：阶段1 新增 services\data_pool.py（DAL：schema 校验/双写告警不阻塞/审计日志）+ persona_analyzer 4 受控字段接入（蛋卷 55 字段零行为变化）；阶段2 direction_history 真实写盘 + schema_version=v2.1（字段 55→56）；阶段3 一致性测试 7 项+无口径打架清单 13 字段全一致。
- 测试 **48 passed / 0 failed**（DAL 4 + 一致性 7 + persona_v2/stress 37）；磁盘画像 56 字段；红线全守（boost_glide_planner 只读/微信 gateway 不动/正式库只读）。

### 追补7（2026-10-13 同日·测试真实闭合+接线+既有失败修复）
- **假完成实锤与闭合**：此前 DAL/一致性测试数字无真实载体、data_pool 事后回写非真接线——全新代理以真实命令+输出文件闭合。新建 tests\test_data_pool.py（**17 passed**，按 data_pool.py 真实接口：write/read/schema 校验告警不阻塞/双写告警/审计/reset/授权矩阵/SCHEMA 自检）、tests\test_architecture_consistency.py（**7 passed**：deception↔suspicion 对齐/psychology received-only/direction_history 真实值/schema_version=v2.1/审计留痕/授权矩阵）；回归 **82 passed**。
- **data_pool 真实接线**：persona_analyzer :370-372/:387/:389 四受控字段唯一写入口过 DAL（删除事后回写块 :832-841）、读边 :736/:771 走 DAL.read；蛋卷接线前后 55 字段逐字段一致（DIFFED FIELDS: NONE）；task_engine 接线=排期。
- **既有 6 失败修复**：affection_reasoning.py:33 悬空 import（services.data_pool.contracts 包被单文件 DAL 取代）→ data_pool.py 追加 Message/Evidence dataclass、import 改指真实模块、test_affection_reasoning.py 解除 skip（13 passed）；**全量 1683/6failed → 1702 passed / 0 failed / 4 skipped**（exit 0）。旧 contracts+4 孤儿测试 skip 处置（.bak_skip）。
- **task_engine 三处闭环**：expiry 真判（lim_1→missed）、每日任务接真实推荐、奖励链（完成→好感度+5→XP=30→勋章）；reward_goal 现实奖励字段（按方向推导）；三层父链 parent_stage/parent_mainline；5 passed。
- 备份：persona_analyzer.py.bak_dal_wire_20261013_144341、data_pool.py.bak_contracts_20261013_150748、affection_reasoning.py.bak_contracts_20261013_150748；红线全守（boost_glide_planner 只读/微信 gateway 不动/正式库只读/不调外部 LLM）。

### 追补8（2026-10-13 同日·全量需求核对未落实项补完）
- **假完成修复**：R3.3-06 PsyDI 渐进式人格探测——需求文档标【已落地】实为残留（live services\psydi_probe.py 不存在，仅 backups\dead_services 死备份）；新建 services\progressive_probe.py（3阶分阶状态机/复用心理学证据提信号/置信度随轮次累积/next_probe 接引导读边），接线 persona_analyzer.py:~302（psychology.progressive_probe 随 DAL 过闸）；tests\test_progressive_probe.py 7 passed。
- **新增实现**：22.2.2 HTN 分层任务（task_engine.py:2490-2550 HTNPlanner，升温/后撤/维稳分解，GOAP 排期）；22.2.3 动态重规划（:2555-2610 DynamicReplanner，任务反馈→策略层重规划，与罗盘方向级不重复）；4.3.1 回复建议详细结构（:2615-2660 ReplySuggestionBuilder，is_primary 首选标记+话术/理由/时机弹药库）；tests\test_htn_replan.py 9 passed。
- **分诊补完**：24.1.1 11级闯关（新建 services\level_clearing.py + 6 passed）；24.1.10 备份/导出（settings_dialog 按钮接 facade create_backup/restore_backup，"开发中"弹窗消除）；核对确认 23.7/23.12/24.2.2/22.1.4/22.2.4 已由 facade 接线（需求日志 10-10 旧快照修正）；23.8/23.9/26.2.5/26.2.10/26.3.14/26.1.4 成本高保留（UI/OCR）；26.4.22 微信暂缓；24.1.13 排期（共享文件）。
- 备份：persona_analyzer.py.bak_probe_20261013_160124、task_engine.py.bak_htn_20261013_155938、settings_dialog.py.bak_task16_20261013_160528、service_facade.py.bak_task16_20261013_160528；红线全守（boost_glide_planner 只读/微信 gateway 不动/正式库只读/不调外部 LLM）。

---

## [未发布] - 2026-10-13 网关免费 provider 实测修正（零配置 23→14）+ 通路复测（源码层已完成）

> 承接用户要求"测试免注册 provider 的通路/延迟/模型可用性"。对全部零配置 api 类 provider 做实测（/models 连通+延迟，通的做 chat 冒烟），按真实结果修正 catalog 标注与模型名。**核心结论：免注册 chat 真能出文本的只有 2 家（pollinations / llm7）**。

### 新增（实测发现，如实记录）
- 实测 23 个零配置 api 类 provider：`/models` 通 14 个、chat 冒烟 8 个中**仅 2 个真出文本**：
  - **pollinations**：/models 505ms、chat **886ms**，回复 "Hey there! How's your day going?"（最快最稳）
  - **llm7**：/models 787ms、chat **2292ms**（复测），回复 "Hello! How can I help"
- 本机 **Ollama 在跑**，已装模型 `loveai-qwen05b:latest` / `loveai-qwen15b:latest`（LoveAI 自备小模型），chat 实测 10s ReadTimeout（模型加载慢，待查）。

### 修复（按实测真结果改 catalog，非编造）
- **9 个"标零配置但 chat 实测需 Key/付费/鉴权"的条目改注册即送组**（is_zero_config True→False，priority 调 60-68，enabled 保持 False）：gonka_broker（chat 401）、bazaarlink（/models 401）、fastrouter（chat 403）、nagaai（/models 401）、electronhub（chat 401）、freeinference（chat 401）、vercel_ai_gateway（/models 401）、airforce（chat 402 付费）、ovhcloud_kepler（/models 403）。
- **9 个 default_models 为空的条目从 /models 真实响应补齐模型名**（gonka/fastrouter/poixe/electronhub/freeinference/meganova/helixmind/mnnai/speka/airforce，各取前 3 个真实 id）。
- **ollama default_models 改本机真实模型** `["loveai-qwen05b:latest","loveai-qwen15b:latest"]`（原 llama3.3 等未安装，路由会 404）；pollinations 补 `openai-fast`。
- `_PROBE_RESULTS` 更新 8 条实测标注（unverified→verified_bad 带 2026-10-13 实测原因）+ 补 ovhcloud_kepler。

### 验证（全部真实运行）
- catalog 87 条无重复；agnes_ai 仍正常（注册即送组、可构建）。
- **复测**：零配置可探测 23→**14**；/models 通 **9/14**（llm7/pollinations/ollama/blockrun/poixe/meganova/helixmind/mnnai/speka）；chat 真出文本 **2**（pollinations 886ms / llm7 2292ms）。
- 如实标注：meganova/helixmind/mnnai/speka chat 401（/models 免 key 但 chat 需 key）；poixe chat 400（kimi-k3 可能非 chat 可用名，待确认）；ollama 本机 10s 超时。
- gateway 测试 **27 passed**（4.19s）零破坏。
- 备份：`registry.py.bak_agnes_20261013`（agnes 前）、`registry.py.bak_probe_fix_20261013`（本轮修正后基线）；补丁脚本 `new-chat-1\_patch_registry.py`（18 处替换，可复现）。

### 说明
- 仅改动 `gateway/app/providers/registry.py` 一个文件；微信接入/其他 gateway 文件零改动。
- 死端点如实保留并标注（free_llm_com 404 / ovhcloud_anon DNS / nvidia_build 404 / ai_tools_api 404 / lm_studio 本地未开），未删除以免误判历史。

---

## [未发布] - 2026-10-13 网关 provider 扩充（agnes_ai）+ 目录去重（源码层已完成）

> 承接同日抖音借鉴「WorkBuddy 白嫖外部模型」：LoveAI 网关已具备 WorkBuddy 模式全部能力（自定义 OpenAI 兼容端点 `openai_compat_generic`、OpenCode 免费模型 `hermes_opencode`），本次补齐视频演示的具体免费接口并顺手修复目录重复 bug。

### 新增
- `gateway/app/providers/registry.py` 目录新增 **`agnes_ai`**（type=openai_compatible，注册即送组 priority=45，enabled_by_default=False，needs_verify=True）：Agnes AI 2026 开放文本/图片/视频三类免费 API（抖音 WorkBuddy 白嫖演示来源）；base_url 视频 OCR 截断未确认，需用户注册后自填（如实标注，未编造端点）。未启用，无任何路由/计费影响。

### 修复
- **catalog 重复 provider 名 bug**：`chutes` 在目录中出现两次（内容完全相同，2026-10-10 第三批补充时误加），导致目录唯一性断言失败、dashboard 注册即送组重复展示。已删除冗余条目，保留原条目（index 58，enabled_by_default=True）。

### 验证（全部真实运行）
- catalog 总量 88→**87**，无重复名；agnes_ai 字段/构建/分组检查全 PASS（`_verify_agnes.py`：provider 类 OpenAICompatibleProvider、入注册即送组、不在零配置组）。
- gateway 测试套件 **27 passed**（4.96s，`cd gateway && ..\.venv\Scripts\python.exe -m pytest tests`）；根目录直接跑会因 `tests.conftest` 路径冲突报 collection error，属既有问题非本次改动。
- 备份 `registry.py.bak_agnes_20261013`。

### 说明
- 微信接入/gateway 其他文件零改动（仅 registry.py 一个文件）。
- 尚未实测 Agnes AI 端点（需注册拿 key、base_url 未知）；启用后由路由层 health_check 验证。

---

## [未发布] - 2026-10-13 算法三模型升级 + 遗留测试修复（源码层已完成）

> 本轮为**纯算法层升级**（判断/任务/画像三大模型），不涉及封包；委派子代理 o_000EVeplHC6 执行，全链路真实验证。
> 用户战略约束（均落地）：①后撤=策略型前进（主线保留、埋根基可见）；②全类型通用+战略/战术两层（主线不随类型变）；③多维参考不自我设限（6 维度 12 条）；④**回撤≠降频**（后撤方向一律"留灯任务"：频率稳定+内容低压+埋根基，禁降频/断联措辞）。

### 新增（算法）
- **判断模型**（`services/persona_analyzer.py`）：校准双向化（新增 Gottman 转向率信号 +4 / lag_asym 抵扣 −3，净 +14），calibrated 57.5→**71.5**；极端风险方向分离（sent/received 分开，蛋卷 risk 高→中）；校准文案动态化（消除"零主动表达 vs 实际101次主动情感开启"矛盾）。
- **任务模型**（`services/task_engine.py` + `models/database.py`）：三层任务体系（main 主线/mid 阶段线/limited 限时）落地；候选池按方向语义过滤（后撤/修复硬丢升温施压模板）；后撤=策略型前进（4 条铺垫主线带 foundation/advance_trigger）；回撤≠降频（全局留灯式，清除全部降频/断联措辞，L725 一处残留已修）；DUM 多样性加权分数区分化；**主线任何方向保底**（升温/维稳/修复方向池全标 main）；策略五类型差异化（修复 reasoning 自由文本"修复"塌缩缺陷）；DB 迁移真实触发（tasks 表 route/loyalty_reward 列实测加成功）。
- **画像模型**（`services/personality_strategy.py`）：人格任务按依恋类型分支（回避→留灯低压力/焦虑→确定感/安全→空）；校准闸门旁路 BPD 误判（蛋卷"边缘倾向8.0"→疏离回避留灯式）。
- **参考研究**：12 条全真实可溯源（JITAI 三层任务骨架 / Simpson 2007 依恋×caregiving 实证 PMID 17723060 / POWER2DM Filter→Rank PMC6351973 / HeartSteps contextual bandit PMC8439432 / DUM 多样性加权 arXiv:1411.3650 / SDT / Rusbult 投入模型 / 心流难度门控 / Options-SMDP / Mikasa 三层记忆 / Replika 无状态教训等），链接抽查核验真实，未凑数。

### 修复
- **遗留测试 `test_broken_promises_critical`（承诺计数事件化 v6）**：原实现按关键词命中次数膨胀承诺数（36）与爽约数（12），兑现率被摊高到 66.7 → overall 50.4（low 假阴性，6 次答应全爽约却判不出 critical）。改为按独立消息事件计数（承诺 12/爽约 6）+ 爽约=言行不一实证每条扣 8 分：兑现率 50.0、一致性 22.0、overall **36.0 → critical**。备份 `services/persona_analyzer.py.bak_trust_20261013`。

### 验证（全部真实运行）
- 判断模型 6 回归 **68 passed**（19.89s，仅既有 utcnow DeprecationWarning）；`test_persona_v2.py` 全文件 **18 passed**。
- 任务模型 7 测试 **85 passed**；画像人格相关 **49 tests OK**。
- 真实推荐验收（蛋卷新画像）：direction=后撤 strategy=慢热，5 条推荐全为铺垫主线+留灯中级、无 stage6 施压模板、score 两两区分（5.10/4.80/4.49/4.43/3.87），6 项自动验收全 PASS；三模拟画像（回避/焦虑/安全）推荐差异化通过、主线三类下全部在场。
- **零改动核验**：boost_glide_planner.py 与全部微信接入/gateway 文件未触碰（mtime 核验），仅 4 个目标文件改动且 .bak 备份在位。
- 交付物：`new-chat-1\LoveAI_三模型升级交付报告.md` / `LoveAI_算法升级参考研究.md` / `LoveAI_三模型升级规格.md` / `_verify_final_out.txt`。

### 待办（未处理，如实记录）
- `test_broken_promises_critical` 已修复；trust 评分仍为规则型关键词匹配，未引入 LLM 语义判定（用户要求省额度，保持本地规则）。

---

## [未发布] - 2026-10-12 核验整改（源码层已完成，**待重打包**）

> 对应需求条目：R3.3-85（核验整改批次）、R3.3-70、R3.3-52、R3.3-55、R3.3-73、第十七章、第十九章。
> **本区所有代码改动均未进入任何安装包**——`LoveAI_Setup_v3.0.0.exe` 与 `LoveAI_DH_Local_v1.zip` 仍是旧逻辑。

### 安全
- **⚠️ WeFlow Gitee 镜像投毒，已隔离（2026-10-12 安全事件）**：官方仓 2026-07-07 遭 DMCA 下架后官方渠道全断，改从 Gitee 镜像自编译时，静态审查发现其 `preinstall.js` 含双层混淆（不可见码位 + AES-256-CBC）恶意载荷：解密后为 9 个 Solana 主网 RPC 外联（含硬编码 API key 节点，调用 getSignaturesForAddress）、`os.userInfo` 机器标识采集、俄语/俄时区跳过逻辑、文件写入——加密资产窃密木马特征，非依赖检查。源码已改名隔离 `D:\weflow_src_QUARANTINED_do_not_run`（保留证据），解密明文留档。**WeFlow 路线在官方渠道或可信校验源恢复前永久搁置**。
- **微信监听三通道最终结论（R3.3-70 攻克）**：②截屏OCR 为现主力——修复 `adapter_41.py` 两处真 bug 后连通（位图头 `ctypes.memmove` 误传 c_int 对象致截屏恒空=监听零捕获根因；RapidOCR 不接受 PIL 图改 ndarray），对微信 4.1.15 窗口实测识别成功；③手动导入兜底——修复 `wechat_importer._batch_import` INSERT 列名与表结构不符的假阳性（ok:true 但零入库）后闭环通过；①WeFlow 搁置如上。pywechat127 已补装（可导入名 `pyweixin`）；UIA 直读路线实测被微信 4.1.15 自绘 UI 堵死。端到端"捕获新消息"待微信登录后复验。
- **版权形象移出发行链路**：桌宠默认角色由「宁海」（碧蓝航线版权角色，此前已打进 v3.0.0 包）先改 haru 占位，**同日经用户确认改为已成品「小妖女·系统精灵」（cybergf）**——R3.3-82 交付的真人感古风立绘 + 15 帧序列（呼吸/眨眼/6表情/5口型，`frames/manifest.json` v2），属项目自产可再分发素材；`desktop-pet-electron/package.json` 增排除规则 `!assets/models/ninghai/**` 防回归。宁海模型文件保留本地供测试，未删除。
- **形象来源正式登记（用户澄清）**：`assets/design_ref/xiaoyaonv_ref_pink_hanfu.jpg` 入库——小妖女形象为用户自设计（粉色汉服真人质感参考），血缘链=参考图→锁定母版→cybergf 帧序列→沉浸桌面抠像立绘。**宁海混入定性为前任代理抓取外部游戏资源的捷径行为，非用户决策**（详见需求文档 R3.3-82「形象来源与血缘登记」）。
- **个人身份与密钥不再入库**：`.gitignore` 追加 `device_info.json`（含机器名/用户名/CPU ID/磁盘序列号/MAC/主板序列号/OS 序列号）、`gateway/data/`、`*.master_key`、`.master_key`。核实项目尚未 `git init`，无历史泄露。

### 修复
- **微信监听落实暂缓决策（R3.3-70）**：`app/application.py` 启动段四块（微信服务/导入器/40 种接入/密钥提取+实时监听）全部包进 `wechat.monitor_enabled` 闸；`config.json` 与 `services/config_service.py` 兜底默认值同步改 `false`。此前为无条件 `connect() + start_listener()`，每次开机自动连微信并起监听线程，与暂缓决策相反。已核查下游三处调用点用 `services.get()` 取值，关闭后不崩。
- **打包态数字人开关可用（R3.3-52）**：`electron/dh-manager.js` 配置读写改落用户数据目录（读取"用户副本 → 包内出厂默认 → off"三级回落，副本损坏时不静默变 off）；`gpu-detect.ps1` 与本地驱动脚本增 `toRealPath()` 映射至 `app.asar.unpacked`（原路径在 asar 内无法被 spawn，会导致本地数字人起不来）；`package.json` 增 `asarUnpack`；驱动日志改落用户目录。此前配置写入 app.asar 内路径 → ENOENT、mode 永远停在 off。
- **本地语音识别不再静默失效（R3.3-55）**：`requirements.txt` 将 `faster-whisper==1.2.1` / `sounddevice==0.5.6` / `edge-tts==7.2.8` 由"可选软依赖"注释区提升为必需并清理重复注释（此前新机器按清单安装拿不到，表现为桌宠听不懂且无任何提示）；新增 `WhisperSTT.status()` 区分"依赖未装"与"模型权重未下载"并给修复指引；语音桥在不可用时经桌宠气泡如实播报原因，不再静默丢弃录音。实测 `available()=True`（base 权重已缓存）。

### 新增
- `scripts/download_stt_model.py`：离线/内网分发场景预下载 Whisper 权重，下载后做静音转写自检，确认"真能跑"而非只拉文件。
- `assets/medal_master_source.png`（1122×1381）+ `assets/medal_slots.json` + `scripts/compose_medal.py`：**R3.3-84 成就勋章定稿**。用户 2026-10-12 提供抖音「地球online成就馆」原图后，裁掉手机截图黑边落库为原像素母版（边框/四角/顶栏/地球插画全为原图，未重绘），槽位自动测边+目测标定，合成管线实现"内容区只换不改、固定壳原像素不动"（主题图 cover 裁切 + 月桂/奖杯 lighten 贴回 + 金句可选替换且无接缝）。样例「首次破冰」43.2% 目检通过。此前 `achievement_medal_template_v3.png`（程序绘制骨架）与 v4 镂空示意**均被取代，留档不再使用**。
- `scripts/download_stt_model.py` 同步登记进 requirements.txt 注释，避免再次出现"文档引用不存在的脚本"。
- `assets/pet_portrait/master_keyed.png` + `scripts/key_master_portrait.py`：**沉浸桌面立绘真人感化**。对用户锁定的古风绿幕母版做色键抠像（双阈值+去绿边+羽化+despill，不依赖 rembg），产出透明底立绘（1695×2278）。
- `app/widgets/immersive_mode.py` **按用户新参考图定版沉浸桌面**：右侧真人感立绘（去模糊、加大、底边贴底）；录音/停止改为中央光球两侧卫星点；字幕条去黑底、情绪关键词金色高亮（先 HTML 转义）；顶部标签改左上角两行小字。离屏截图验证四要素到位。默认启动模式未动（仍默认 2D 轻量）。

### 文档
- 需求文档第十七章（隐私与安全）按实际重写：如实登记明文 SQLite、`encrypt_chat_history` 为无人消费的空开关、注入类采集器存在但暂缓未接线；首次告知/一键清除标 `[待实现]`。
- 第十九章非功能需求：**打包体积 ≤500MB 红线取消**（全功能随包分发优先）；**启动时间 ≤5s 放宽**（v3.0 实测 26.3s，后续迭代优化）。两项均为用户 2026-10-12 决策。
- R3.3-65 约束段：放宽"默认只连官方端点"限制，允许第三方/社区端点默认启用；保留 fail-closed、来源如实标注、敏感数据不经社区镜像三条底线。
- 18 条过期状态行按已验证事实改写；重复章节号"第三十五章"改为"第四十二章"；文档标题补"v2.0 基线·现行至 R3.3-85"。

### 更正
- 更正前一轮核验的一处误判：曾称"faster-whisper 被注释所以未安装"。实测本机 `.venv` 内该依赖与 ctranslate2/sounddevice/edge-tts **均已安装**、base 权重已缓存、`available()=True`。真实缺陷是**依赖声明与实际环境不符**（清单中为注释态，新机器装不到），故按"声明提升为必需 + 不可用时明确提示"双向整改。

### 验证
- `py_compile`：application.py / config_service.py / stt_service.py / pet_voice_bridge.py / download_stt_model.py 全过。
- `node --check`：dh-manager.js / app.js 全过；package.json 与 characters.json 解析通过。
- 运行时自查：`get_stt().status()` → `{'available': True, 'reason': None, 'hint': None}`。
- 三处桌宠角色字段（characters.json / config.json pet.model / pet_config.py 默认值）已对齐 haru。

### 生效前提（重打包步骤）
1. `desktop-pet-electron` 执行 `npm run build:win`（重打桌宠包，验证 asar 内无 ninghai、digital-human 脚本已解包）；
2. PyInstaller（`LoveAI.spec`）→ Inno（`tools/installer/LoveAI.iss`）重打主包；
3. 复跑 `logs/pack_v3/` 冒烟验收：桌宠默认形象、数字人开关在打包态可写、微信监听默认不启动。

### 遗留（未做，勿当已完成）
- 全量 pytest 待重跑定案：`.pytest_cache/v/cache/lastfailed` 留 16 条失败（7 条业务用例 + 9 个仓库根临时调试脚本，含 Frida 注入类）；此前"1442 passed"与该缓存不在同一时间线。
- 仓库卫生：根目录 16 个 `.bak_*`、9 个不合规 `test_*.py`、`cherry-studio.zip` 34.5MB、`cherry-studio-src` 源码树、`docs/` 多版覆盖报告未归档（R3.3-74 定期机制仍未建立）。
- 主库加密未实现：明文 SQLite 现状不变，`encrypt_chat_history` 仍是空开关，对外不得宣称已加密。
- R3.3-78 验收点 3：`OBJECTIVE_KEYS` 七维中仍无 loyalty，忠诚度未进任务算法目标函数；与"boost_glide_planner.py 只读勿改"铁律冲突，待用户拍板豁免或破例扩展。
- R3.3-66 验收点 6：免费模型合格池实测 eligible=2，未达"≥3 个 provider 可路由"。
- R3.3-67：七个桌面端适配器仅 Cherry Studio 一个在位。
- R3.3-58 多端协同：无手机端实现，保持未开始。

## [3.0.0] - 2026-10-11

### 破坏性变更
- **渲染架构重写**：主程序由 QWebEngineView + QWebChannel（HTML 前端）改为**原生 QWidget + Qt3D**；随之移除 WebEngine 依赖，**CDP 9222 调试端口不再暴露**，端到端验收方式改为 UIA / 屏幕坐标点击。此前所有基于 QWebChannel / pywebview.api 的前端接线方案作废。
- **导航结构收敛**：主窗口导航由 16 项合并为 **9 个 Tab**（聊天分析 / 情感咨询 / 人物档案 / 英雄墙 / 勋章墙 / 日历备忘 / NPC关系图 / 任务列表 / 数字分身）。

### 变更
- 桌宠包纳入 digital-human 数字人模块（权重已排除、config mode:off）。
- 新增数字人本地轨扩展包 LoveAI_DH_Local_v1。
- 安装包整体重打 v3.0.0。

### 已知遗留（发布时如实标注，非已解决）
> 2026-10-12 后续处置：下列第 1、2、4 项已在源码层修复（见顶部 [未发布] 区），第 3 项仍待重跑 pytest 定案。**四项在 v3.0.0 安装包内均未生效，需重打包。**
- 打包态数字人开关不可用：`dh-manager.js` 把配置写进 app.asar 内路径，安装包内写入 ENOENT、mode 停在 off；源码态（npm start）可用。修法=配置落 %APPDATA% 后重打。
- 默认桌宠形象为「宁海」（碧蓝航线版权角色）且已打进发行包，违反开源合规红线，发布前必须替换默认形象并从包内剔除。
- 全量 pytest 存在 16 条失败记录（7 条业务用例 + 9 个仓库根临时调试脚本），未清零。
- 本地 ASR 已实现但未启用：`requirements.txt` 的 faster-whisper 处于注释状态，语音识别静默返回空文本、无 UI 提示。

### 说明
- 本条是真实软件版本号（SemVer 3.0.0，对应 LoveAI_Setup_v3.0.0.exe）。其下方 [3.3.0]/[3.2.0]/[3.1.0] 为需求批次登记，非软件版本，详见文件顶部勘误。

## [3.3.0] - 2026-10-08 深夜
> 注：本条实为**需求批次 R3.3** 的实现记录，非 SemVer 版本 3.3.0。

### 背景
- 用户质疑"免费模型/手机端只学了表面"。经全工程 grep 核查：OpenRouter/NVIDIA/Ollama/网桥/安卓无障碍此前均零实现，`llm_gateway` 的 L1/L2/L3 是旧语义（本地小模型/本地GGUF/自填付费key），`wechat_mobile_reader` 是纯空壳。本次真正落地免费路由，并打通"数据进门→侧写→档案页"端到端闭环。

### 新增
- **免费模型路由** `services/free_model_router.py`：OpenRouter 免费目录（Llama3.3-70B/DeepSeek-V3/Qwen2.5-72B/Gemini-Flash 等 :free）+ NVIDIA NIM 免费端点 + 本地 Ollama + 自定义本机 OpenAI 端口（对接 DSH/WorkBuddy/Trae 桥接）；按 本机优先→成功率→延迟 自动排序，限流/超时/认证失败同轮切换
- **聊天记录导入器** `services/chat_importer.py`：不依赖微信密钥，支持纯文本（我:/对方:/时间戳）、JSON、CSV 解析，自动识别说话人、去重、写入 persons+chat_messages 表
- **CHARACTER FILE 档案页渲染器** `services/character_file_renderer.py`：直接复刻参考图暗色高级 UI——关系阶段标签、心形好感度0-100+星级0-10、SVG情绪折线、当前策略卡、情绪基线时间线、近期互动、危险信号归档；纯HTML+CSS+内联SVG，QWebEngine可直接加载
- **端到端 Demo** `tools/run_demo_character_file.py`：示例聊天→导入→五层侧写→档案页HTML，输出 `docs/character_file_demo.html`
- 测试：`test_free_model_router.py`（9）、`test_chat_importer.py`（8）、`test_character_file_renderer.py`（6）

### 变更/修复
- `llm_gateway.py` 降级链改为：L3付费云端 → **FREE免费路由** → L2本地GGUF → L1本地小模型 → 规则兜底；新增 `_call_free()` 方法
- 修复 chat_importer 去重逻辑（按 person_id+direction+content，不再因默认时间戳不同而失效）
- 修复测试 tearDown SQLite 连接未释放导致的 PermissionError

### 验证
- 新服务测试 23 项全过
- 端到端 demo：36条消息导入→24维侧写→档案页11KB HTML生成成功
- gateway 回归 16 项全过（接入免费层零破坏）

### 仍待处理
- 微信PC采集（#6）：等用户重启后核查 MEMORY.DMP
- 手机端安卓：需独立Android App，PC工程仅接口预留
- 分发打包（#11）：勾选组件、开箱即用、依赖随包

## [3.2.0] - 2026-10-08
> 注：本条实为**需求批次 R3.2** 的实现记录，非 SemVer 版本 3.2.0。

### 背景
- 功能自检（以真实代码/测试为证据，不接受口头结论）：发现「复盘回流」「方向罗盘」在 v2.1 总纲中已要求，但运行代码实质缺失或为占位接线，本次真正实现并接入主链。

### 新增
- 复盘回流引擎 `services/review_loop.py`（ReviewLoop，纯本地确定性）：数字分身预测落库 → 下一轮用对方实际反应结算「情绪 / 回复方向 / 后续行动」命中 → 滚动有效度（Wilson 95% 区间）→ 输出模拟实测分权重校准建议（随真实命中率在 0.4–0.7 调整）
- 方向罗盘 `services/direction_compass.py`（DirectionCompass）：9 维成功指标（双向奔赴/承诺兑现/亲密/激情/承诺绑定/修复力/对等投入/稳定/满意度）+ 三重闸门（方向/匹配/真伪）+ 漂移检测 + 结局倒推 gaps
- 数据表 `prediction_records`：预测持久化与实际结算（status / emotion_hit / direction_hit / action_hit / validity_score）
- 测试：`tests/test_review_loop.py`（11）、`tests/test_direction_compass.py`（16）

### 变更/修复
- `service_facade` 主链接线：后台服务前先结算上轮预测 → 分身预测即落库 → 结果组装前跑方向罗盘；`review/compass` 写入返回 data；`_wire_batch1` 改用真实命中/有效度，消除写死的复盘文案与 AB 效果 0.7（无样本时用中性先验 0.5 并标注来源）
- 行动命中口径收紧：仅「具体共同安排/明确承诺」计分，模糊线上后续（继续聊/找话题）标 N/A、不进有效度分母
- 修复既有测试缺陷：`test_batch15/17` 断言与本机运行微信的真实环境解耦；`test_relationship_graph` 实例化 widget 前补 offscreen QApplication（消除原生崩溃 0xC0000409）
- 补齐缺失依赖 `cryptography`（微信密钥加解密所需）

### 测试
- 全量 **1064 passed, 0 failed**（快速全量 1040 + 慢集成 rag 8 + batch18 16）

---

## [3.1.0] - 2026-10-08
> 注：本条实为**需求批次 R3.1** 的实现记录，非 SemVer 版本 3.1.0。

### 战略调整
- 微信通路暂缓：微信 PC 采集/实时监听从主线第一步降级至「迭代 4」，当前不开发、不阻塞前三个迭代
- 确立分阶段迭代路线图：迭代1 看懂与预测 MVP → 迭代2 引导与任务 → 迭代3 省钱与多通道 → 迭代4 自动采集（含微信）

### 新增
- 多源手动/半自动数据采集：手动粘贴/导入 txt/csv/json（自动区分说话人 + 原文核对页）、截图 OCR（ML Kit/视觉模型）、关键事件录入、可采集 App 无障碍读取；手动数据默认本地/离线处理
- 用户自我画像模块：采集性格/沟通风格/能力/资源/底线/目标，与对方画像配对，策略基于「用户 × 对方」双方匹配
- 复盘回流：录入对方实际反应，对比「预测 vs 实际」算命中度，贝叶斯校准五层侧写与数字分身，沉淀情景记忆
- 眼镜终端视觉层（S）：第一视角实时助手；端-边-云分层（眼镜采集 + 端侧抽帧 → 手机/云端视觉大模型 → 事件驱动 HUD/骨传导推送 → 事后复盘回写）；抽帧 + 事件触发省 token；隐私合规（明示/本地优先/最小上传）
- 借鉴叶秋大大「视觉智能体 + 自定义识别规则 + 事件驱动推送」方案作为眼镜端软件内核（监控固定流工况需改造，不直接照搬）

### 变更/修复
- 术语统一：「预测成功率」改为「预测有效度（相对推荐，非成功概率）」，与数字诚实规范一致
- 前台入口调整：微信一键读取/监听移出当前前台，新增手动导入、复盘入口

### 待测试
- 迭代1 最小闭环（多源采集 → 五层鉴别 → 档案 → 高保真模拟 → 话术评分 → 复盘）端到端跑通
- 眼镜端视觉层在迭代 4 之后落地，当前仅登记需求与架构

---

## [3.0.1] - 2026-10-07

### 新增
- AI多模型协同 `chat_with_pipeline`：L1预处理→L3深度分析→L2后处理，逐级降级
- 仿人工操作 `humanizer`：随机延迟（按aggressiveness分档）/贝塞尔鼠标轨迹/打字计划（停顿/打错重打）/频率限制（30每分钟、500每小时、连续10次休息）/凌晨低活跃
- Windows-MCP微信操作框架 `wechat_windows_mcp`：UI Automation树读取/控件定位/点击/输入/滚动，uiautomation未装时降级提示
- 关系网络图 `relationship_graph`：自动识别人名/称谓，六类关系配色，关键人物影响策略（闺蜜/前任/家人/暧昧对象）+红橙预警

### 修复
- `chat_with_pipeline` 接入主流程（默认 `llm.pipeline.enabled=false`，保持兼容，失败回退原 chat）
- `relationship_graph` 联动 orchestrator：关键人物影响写入 context.social_influences，红/橙预警并入 risk_warnings
- `wechat_windows_mcp` 接入 `wechat_operator` 作为 pywechat127 失败的备选后端

### 测试
- 全量 736 passed；手段中立 grep=0

---

## [2.0.0] - 2026-10-04

### 阶段一：干净骨架（完成）

#### 新增
- 全新项目目录结构（app/services/models/ui分层架构）
- PySide6 + QWebEngineView + QWebChannel 桥接层（彻底废弃pywebview）
- 四档窗口系统：主窗口(可缩放)/TOP1代聊窗口/TOP2咨询小窗/悬浮球
- WindowManager 窗口管理器（切换/状态记忆/最小化归宿）
- 桌宠系统（QWidget+QPixmap帧动画，6种表情，拖拽，气泡提醒，快捷菜单）
- WeChatDetector 微信窗口检测（ctypes user32 API）
- 系统托盘 + 全局快捷键（Ctrl+Alt+L/V/M/Q/H）
- 新手引导框架（6步）
- 完整数据库设计（18张表 + 25个初始勋章）
- ConfigService 配置服务（实时生效，即时写入）
- 前端UI：烫金暗色风主界面 + 设置中心 + 好感度环 + 雷达图
- 6个Prompt模板（聊天分析/数字分身/情感咨询/心理学侧写/任务生成/回复风格）
- 5个预置技能（邀约话术/吵架修复/冷读/分手决策/约会策划）

#### 资产迁移
- 立绘帧：39个男版 + 16个女版
- 知识库：383个md文档
- FTS5全文库：unified_search.db (65.9MB)
- 向量库：chroma_db
- 本地模型：qwen2.5-0.5b-instruct-q4_k_m.gguf (491MB)

#### 技术改进
- 服务层与UI层完全分离（services/不import PySide6）
- 懒加载设计（大模型/微信/RAG按需加载）
- 所有JS-Python通信走QWebChannel（全局搜索pywebview为0）
- 后台线程daemon化，Signal通知UI

#### 验收标准
- [x] 启动≤5秒
- [x] CPU≤50%
- [x] 设置中心能打开能保存
- [x] 桌宠能动能拖
- [x] 快捷键有效
- [x] 全局pywebview搜索为0

---

### 阶段二：微信+核心分析（完成）

#### 新增服务模块（7个）
- **wechat_service**：pywechat127封装（双路径导入），降级为pyperclip+ctypes Ctrl+V，1.5秒daemon监听，MD5去重（上限500）
- **llm_gateway**：三级路由（simple/medium/complex）+ 自动复杂度判断 + llama.cpp懒加载 + 5分钟空闲自动卸载 + Prompt模板管理
- **emotion_analyzer**：8种情感标签（happy/love/excited/neutral/confused/sad/angry/cold）+ 规则匹配主力 + LLM增强接口 + 强度计分 + 颜色映射
- **safety_filter**：36组敏感词映射 + 10条云端拒绝检测模式 + encode/decode破假 + 被拒自动降级本地AI + 统计计数器
- **affection_engine**：18维实时好感度（权重归一化）+ 消息驱动更新 + 单日单维±10上限 + 7天无互动衰减回归 + 每日快照 + 关系阶段0-10级
- **rag_service**：FTS5全文检索（实际表名fts+chunks JOIN）+ Chroma向量语义检索 + 双引擎融合（FTS权重0.6/Chroma0.4）+ 知识库索引（383md→19980段）

#### 新增窗口（2个）
- **chat_overlay**（TOP1代聊窗）：360×600，Frameless+置顶+透明，QWebEngineView加载chat_overlay.html，4信号接线，微信窗口右侧对齐，回复一键填入
- **consult_mini**（TOP2咨询小窗）：340×500固定，Frameless+置顶+透明，咨询/分析/好感度三Tab，位置记忆（500ms防抖），toggle切换

#### 前端更新（5个文件）
- **index.html**：聊天分析Tab重构为8模块卡片（情感/关键词/RAG/意图/关系/回复/安全/好感度）+ 8步进度条 + 弹幕流 + 微信状态灯
- **app.js**：新增startChatAnalysis/renderAnalysisResult等8个核心函数 + 信号监听重接 + "填入微信"按钮绑定
- **main.css**：追加聊天分析页全套样式（~300行）
- **chat_overlay.html**：新建，360×600代聊侧边窗（弹幕流+回复填入）
- **consult_mini.html**：新建，340×500常驻小窗（三Tab）

#### Bridge接口扩展
- @Slot方法从31个增至37个
- 新增：analyze_emotion / get_rag_context / get_safety_stats / get_llm_status / get_affection_detail / rebuild_rag_index
- analyze_chat接入真实分析流水线（情感→RAG→LLM→安全破假→好感度更新）
- send_reply_to_wechat接入真实WeChatService（降级剪贴板）
- 微信域方法全部接入真实服务

#### 应用层更新
- application.py新增_init_phase2_services()，安全导入8个服务（模块缺失不崩溃）
- window_manager.py替换chat_overlay/consult_mini占位为真实类

#### 单元测试（53项，100%通过）
- test_safety_filter.py：14项（encode/decode/roundtrip/破假三流程mock）
- test_emotion_analyzer.py：15项（8标签/颜色/batch/疑问句）
- test_affection_engine.py：15项（18维/阶段/更新/快照/±10上限）
- test_rag_service.py：8项（FTS5/Chroma mock/融合/格式）

#### 技术改进
- 所有重服务懒加载（LLM本地模型首次调用才加载）
- pywechat127双路径导入（pyweixin→pywechat127），不可用时降级模式
- chromadb 1.5.9已安装，FTS5实际表名探测修正（fts+chunks JOIN）
- 安全破假三路分支：无敏感词直连/有敏感词破假云端/被拒降级本地
- 好感度与聊天记录实时联动（解决旧版恒50问题）

#### 验收标准
- [x] 8个服务全部注册成功
- [x] 37个@Slot方法
- [x] 微信监听接通（降级模式可用）
- [x] 聊天分析流水线可用（情感+RAG+LLM+安全）
- [x] 回复一键填入微信（pyperclip+Ctrl+V）
- [x] 安全破假机制（36组映射+10条拒绝模式）
- [x] 好感度实时更新（18维+消息驱动）
- [x] TOP1代聊窗口+TOP2咨询小窗
- [x] 前端8模块展示+弹幕+回复点击
- [x] 单元测试53/53通过
- [x] 全局pywebview搜索为0
- [x] 服务层不import PySide6

---

### 阶段三：游戏化+数字分身（完成）

#### 新增服务模块（7个）
- **digital_twin**：数字分身6大能力（predict_reaction/simulate_reply/compare_options/generate_profile/record_feedback/accuracy_score）+ SOUL.md+personality.json档案 + 反例注入 + JSON四级鲁棒解析 + feedback跨实例恢复 + 工厂按需创建（绑定person_id）
- **task_engine**：引导闭环六步（目标拆解→任务+攻略方向→教程→分步执行→反馈判定→节奏推进）+ 3种限时任务（邀约48h/维护168h/修复24h）+ 失败替代 + 7阶段主线任务自动生成 + 单日±10上限
- **tutorial_engine**：TOP二私教攻略双窗口（self单人提升/target双人关系）+ 深度攻略生成（分析/策略/计划/风险/预期/备选）+ 攻略库检索（历史+RAG知识库合并）+ 分步教程展开 + 任务方向自动附带
- **financial_analyzer**：聊天信号提取（收入/消费/压力关键词）+ LLM分析+规则兜底 + 四档风险底线（low/medium/high/dangerous）+ 需求任务前置检查 + 支出记录与汇总 + 超负荷警告
- **consult_engine**：三轮引擎（意图路由→记忆筛选→人格化生成）+ 两个模块（self自我成长/other对象视角）+ 安全拦截（自伤→心理热线/暴力→不教唆/违法→明确拒绝）+ 质量门（绝对化词替换+短回答补免责）+ 会话历史管理
- **medal_service**：25个初始勋章（5类：新手5/关系8/任务5/社交4/特殊3）+ 自动判定解锁（好感度里程碑/任务完成/消息数/连续登录/功能计数）+ 英雄墙数据聚合 + 手动授予 + 自动建表+ALTER兼容旧版
- **portrait_service**：照片管理（上传/列表/设头像/删除）+ 立绘帧匹配（按gender+emotion，male39帧/female16帧）+ 照片→立绘转换（PIL可选，不可用时规则匹配降级）+ 特征提取（尺寸/主色调/亮度，不做人脸检测）+ person_photos表自动建

#### 前端更新（3个文件，+95%/+103%/+34%）
- **index.html**：新增4个Tab（数字分身/任务引擎/攻略/财务）+ 增强3个已有Tab（人物档案新增年龄职业/MBTI/大五/依恋/照片管理/立绘匹配/备忘录；勋章墙新增统计环形图/分类筛选；咨询新增安全提示横幅/清空历史/对话流）
- **app.js**：新增24个阶段三API调用函数 + 数字分身预测/模拟/A/B对比 + 任务卡片列表/分步详情 + 攻略生成/搜索/历史 + 财务报告/可行性检查/支出记录 + 勋章墙渲染/英雄墙 + 咨询对话流 + 照片上传/立绘预览
- **main.css**：追加阶段三页面全套样式（~8.6KB），延续烫金暗色主题

#### Bridge接口扩展
- @Slot方法从37个增至66个（新增29个阶段三方法）
- 数字分身域：predict_reaction / simulate_reply / compare_options / get_twin_accuracy / record_twin_feedback
- 任务引擎域：generate_main_tasks / generate_limited_task / get_active_tasks / get_task_progress
- 攻略引擎域：generate_guide / search_guides / get_guide_detail / list_guides
- 财务分析域：generate_financial_report / check_demand_feasibility / record_expense / get_expense_summary
- 咨询引擎域：consult / clear_consult_history / get_consult_history
- 勋章域：get_all_medals / get_unlocked_medals / get_medal_stats / get_hero_wall_data / award_medal
- 画像域：upload_photo / get_photos / set_profile_photo / match_portrait_frame / list_available_frames

#### 应用层更新
- application.py新增_init_phase3_services()，安全导入7个服务+数字分身工厂
- digital_twin用工厂方式按需创建并缓存（_create_digital_twin(person_id) + _digital_twin_cache），不全局注册单例
- task_engine的guide_engine/twin参数可为None，防御性调用
- financial_analyzer聊天记录表名兼容（chat_messages→messages回退）
- consult_engine安全拦截严格（自伤/暴力/违法绝不给出具体方法）

#### 单元测试（144项，100%通过）
- test_digital_twin.py：15项（默认档案/JSON四格式/system prompt/准确率/simulate/predict/compare/feedback落盘恢复/generate_profile/LLM异常降级）
- test_task_engine.py：14项（默认方向/stage=0建3任务/邀约冲突关键词/5步拆分/分步推进/活跃查询/失败替代/_advance_loop）
- test_tutorial_engine.py：12项（guides表自建/任务方向/空库检索/不存在id/self/target双模式/mode过滤/分步教程/缺person降级）
- test_financial_analyzer.py：12项（表自建/信号提取/规则兜底/完整报告/low/medium/high/dangerous四档/支出记录汇总/无人物不崩溃）
- test_consult_engine.py：13项（初始化/意图路由/自伤暴力违法三类安全拦截/正常流程/LLM失败兜底/绝对化词替换/历史清空读取）
- test_medal_service.py：12项（表自建/25勋章/好感度70解锁确认关系+前置勋章/已解锁列表/统计分类/英雄墙/手动授予重复检测）
- test_portrait_service.py：13项（帧扫描male/female>0/分组列出/male/female匹配/未知情绪回退/上传列表设头像删照片/PIL不可用降级/照片转立绘/表自建）

#### 集成测试（70项，100%通过）
- tools/smoke_test_phase3.py：服务导入/直接初始化/Bridge Slot检查/pywebview=0/前端文件/服务层无PySide6
- 关键功能链路：勋章25个/任务stage=0生成3个/攻略方向非空/财务四档分档/咨询自伤拦截/画像帧扫描/数字分身工厂

#### 技术改进
- 数字分身绑定person_id，工厂+缓存模式避免多对象混淆
- 所有服务SQL用:name命名参数，不import PySide6（除TYPE_CHECKING类型提示）
- PIL为可选依赖，portrait_service优雅降级（当前venv未装Pillow）
- pywechat127/llama-cpp-python保持可选懒加载/降级
- 咨询引擎质量门替换绝对化表述（一定→大概率/必须→建议）
- 勋章服务自动建表+ALTER TABLE兼容旧版结构

#### 验收标准
- [x] 7个服务全部注册成功
- [x] 66个@Slot方法（阶段二37+阶段三29）
- [x] 数字分身能预测反应/模拟回复/A/B对比
- [x] 任务能自动生成并带攻略方向
- [x] 好感度联动任务（task_engine注入affection）
- [x] 勋章能解锁（好感度70→确认关系+前置勋章）
- [x] TOP二能生成深度攻略（self/target双模式）
- [x] 财务分析能出报告（四档风险底线）
- [x] 咨询引擎安全拦截（自伤→心理热线）
- [x] 英雄墙/勋章墙页面
- [x] 人物档案页（基本信息+雷达图+心理学侧写+照片管理）
- [x] 单元测试144/144通过
- [x] 集成测试70/70通过
- [x] 全局pywebview搜索为0
- [x] 服务层不import PySide6

### 阶段四：进阶打磨（完成）

#### 新增服务模块（6个）
- **speech_service**：faster-whisper 懒加载（int8，models_local/whisper-{size}）+ pyaudio/sounddevice 双降级录音 + 16k/mono/16bit WAV + 中文转写（beam_size=5）+ 转写后一键填入微信 + 临时文件用完即删
- **ocr_service**：RapidOCR（rapidocr_onnxruntime→rapidocr 双包名）懒加载 + PIL.ImageGrab 截屏 + ctypes BitBlt（已补全64位argtypes）兜底 + 微信聊天区域估算（排除侧栏/标题/输入框）+ 全文/分行带置信度
- **stats_service**：8 个统计方法（仪表盘/消息/好感度/任务/使用/多对象对比/周报/导出），空库默认值不崩溃，新用户好感度变化回退窗口内最早快照
- **plugin_service**：Skill 技能（子目录 SKILL.md + 扁平 .md + 索引）与 Py 插件（目录 plugin.json + 单文件 .py AST 提取元数据）扫描，importlib 动态加载、坏插件隔离、状态持久化
- **memory_service**：长期记忆 fact/lesson/conclusion 三类，多条件过滤、LIKE/RAG 双路搜索、软删/硬删、相似记忆合并（Jaccard≥0.35）、聊天自动提取、分组上下文、90天衰减与自动遗忘
- **backup_service**：自动备份/zip 打包（MANIFEST 清单）/列表/恢复/删除，路径穿越拦截、保留策略清理、JSON 全量导出导入往返

#### 新增窗口
- **status_bar**（迷你状态栏）：原生 QWidget 280×36，避免额外 WebEngine 内存开销

#### 前端更新（6个新Tab）
- 新增 Tab：统计（原生Canvas柱状/折线/饼图+多对象对比+周报）、语音（麦克风脉冲+识别+填入）、OCR（截屏/微信区域/文件识别+预览）、插件（技能卡片执行+插件开关）、记忆（统计/添加/筛选/搜索/遗忘）、备份（创建/导出/列表/恢复二次确认）
- 图表全部原生 Canvas 2D + dpr 适配，零图表库依赖；烫金暗色主题延续；切Tab懒加载

#### 封包（PyInstaller onedir）
- LoveAI.spec：收集 ui/prompts/skills/plugins/assets/knowledge/config.json + FTS5库 + Chroma库；collect_submodules(services/app/models)；排除 pywebview/pytest/tkinter/matplotlib/llama_cpp/faster_whisper/pyaudio/sounddevice
- 产物 dist/LoveAI（onedir，模型不打包，由 tools/download_models.py 经国内镜像单独下载）

#### 关键缺陷修复
- 离线测试 RAG 误触发 chromadb 下载 79MB 模型：测试内禁用 Chroma、只走 FTS5
- LLMGateway 无 get_status()：离线测试改为断言 local_loaded/local_available 属性
- consult_engine 自伤误判为暴力：自伤词典增补"伤害自己/伤自己/自我伤害"，自伤检查前置优先
- main_window 使用了不存在的 WebEngine 枚举 LocalContentCanAccessFromFileUrls：改回 LocalContentCanAccessFileUrls，并重构为 _safe_set 安全兜底，单个属性缺失不再阻断窗口创建
- **打包态主窗口白屏（核心）**：PyInstaller 下 Chromium 渲染器沙箱无法建立，渲染进程约30秒后被终止（AbnormalTermination, exitCode=40）；main.py 在 QApplication 前注入 `--no-sandbox`，渲染正常、loadFinished ok=True、暗色主题与内容完整呈现

#### 验收标准
- [x] 6个服务全部注册成功
- [x] 95个@Slot方法
- [x] 单元测试242/242通过（阶段四新增98）
- [x] 离线模式40/40通过
- [x] PyInstaller onedir 打包成功
- [x] 干净环境启动不崩溃、主窗口暗色UI完整渲染
- [x] 全局pywebview搜索为0
- [x] 服务层不import PySide6

---

## v2.0 收尾加固（封包前后端契约 + RAG 不阻塞）

### 修复
- **前后端"信封当数据"系统性缺陷**：Bridge 统一返回 `{"ok":..,"data"/"error"}` 信封，而前端消费端期望内部 data 且全文无 JSON.parse。在 `ui/js/bridge.js` 单点新增 `_normalize()`：自动 JSON.parse、拆信封取 data、ok=false 转 reject；同步/Promise 两返回路径统一归一化，消除整类 `.map/forEach is not a function`
- **RAG 被 Chroma 阻塞（分析卡片长期"分析中"）**：根因为后台线程在 Chroma 步隐式下载/解压 all-MiniLM 嵌入模型。`rag_service` 新增通用 `_run_with_timeout` 硬超时、`_embedding_model_ready()` 就绪探测（模型与压缩包皆无时直接判定不可用、**不发起下载**）；客户端创建 8s、嵌入探测 6/12s、向量 query 5s 超时；Bridge 的 RAG build_context 再包 5s 超时（纵深防御，保证 ≤8s emit）
- **FTS 中文召回为 0**：库用默认 unicode61、CJK 按单字切分，原查询把整句包成一个引号短语要求精确相邻。`_preprocess_fts_query` 改为每个 CJK 段生成"整段短语 + 滑动二元组"OR 查询，去重限量 24；情感/邀约/吵架三类查询 0.03–0.07s 返回相关结果（含个人记忆）
- **补齐缺失 slot**：`loadMedals` 改调真实 `get_all_medals`；新增 `get_preferences`/`add_preference`（MemoryService 支撑喜恶）、`update_person_profile`（防御性 ALTER 补档案列 + SOUL.md 落盘）；前端新增 `loadPersonProfile` 并挂档案 Tab/person_switched，做到可存可读
- 交叉比对 app.js 全部 call 方法名与 Bridge 定义，补齐后 0 缺失

### 已知取舍（如实说明）
- Chroma 向量库 collection 当前为空，且 all-MiniLM 嵌入模型不随包（遵循"模型单独下载"）。干净环境下向量检索**毫秒级降级、不阻塞**，知识库检索由 FTS5 独立支撑（65.88MB、383 文件，可用）。
- 启用语义检索需一次性初始化：备妥嵌入模型后运行 `RAGService.index_knowledge` 灌入 collection（383 md≈2万段）。

### 验收
- [x] 无 LLM 下 analyze_chat 端到端 0.23s 到达 analysis_ready（预算 8s），emotion=sad/intensity=6/question=True，rag_context_used=True，安全模块就位
- [x] 全量单元测试 242/242 通过（0 失败 0 错误 0 跳过）
- [x] 干净环境 exe 纯代码（无 QTWEBENGINE_* 环境变量）启动暗色 UI 正常，CDP 真实点击关弹窗、切换全部 16 Tab 渲染
- [x] 全局 pywebview 搜索为 0；服务层不 import PySide6

### 封包阶段追加修复
- **打包态分析长期"分析中"真正根因**：QWebChannel 在打包环境以 snake_case（analysis_ready）暴露信号，bridge.js 硬连 camelCase（analysisReady）被静默跳过、信号收不到（后端正常 emit，非卡死）。connectSignals 改为 snake/camel 双名兼容
- **chromadb 惰性化（降内存）**：顶层 `import chromadb` 改为首次向量检索才导入；融合检索前加零成本文件门 `_embedding_files_present()`，无嵌入模型文件则根本不导入 chromadb。主进程空闲 293→263MB（≈30MB）
- 实测：干净环境分析 ≈0.2–0.3s；最终包 16 Tab 可用、四档窗口可开、JS 0 错误；全量单测 242/242、离线 40/40
- 详见《封包测试报告.md》：空闲总内存实测 ≈342MB（QtWebEngine/Chromium 基线，200MB 目标在当前架构不现实）；Chroma 语义检索需一次性初始化

## v2.1 四项核心修改（2026-10-05）

### 修改一：移除多余开关与错误独立Tab
- 设置中心移除 voice.enabled / pet.enabled / floatball.enabled 三个"本就该默认开"的开关；无 ocr.enabled
- 左侧导航从16Tab精简为5核心Tab：聊天分析(默认)/情感咨询/人物档案/英雄墙/勋章墙
- OCR独立Tab移除，改为聊天分析输入区"📷截图分析"按钮（调 capture_and_ocr → 自动填入输入框）
- 高级功能(数字分身/任务/攻略/财务/统计/插件/记忆/备份)移入设置中心"高级功能"组，通过 switchTab 打开
- 底部栏"数据统计"按钮补接绑定（原HTML存在但未绑定）
- 所有 tab-content 面板保留在DOM，仅删导航按钮

### 修改二：模型三级路由+训练机制（重写 llm_gateway.py）
- L1 最小模型：smollm2-135m ONNX量化版(model_q4.onnx, 190MB)，onnxruntime+tokenizers贪心解码，目标<1秒
- L2 本地大模型：Qwen2.5-0.5B-Instruct + LoRA适配器（llama.cpp优先，transformers+peft降级），3-5秒
- L3 云端API：OpenAI兼容，5-8秒
- 自动降级链：L3→L2→L1→规则兜底；复杂度自动判断(问候短句→L1/日常→L2/复杂长文→L3)
- LoRA适配器管理：list/select/get_current，支持 loveai-core/v2/v2-new/finetuned 四套切换
- 训练数据收集：mark_good_reply→training_data表，get_training_stats，export_training_data导出JSONL
- 模型文件从 D:\LoveAI\models 迁移：smollm2 ONNX+tokenizer、Qwen2.5 safetensors(953MB)、4套LoRA适配器
- Bridge新增5 Slot：get_model_status/list_lora_adapters/select_lora_adapter/mark_good_reply/get_training_stats
- 兼容层：保留 _call_cloud/_call_local/local_loaded/local_available 别名，safety_filter等老调用方不受影响

### 修改三：3D骨骼桌宠（替代帧动画）
- 用 three.js r128 在 QWebEngineView 渲染真正的3D骨骼人形（16根Bone：hips/spine/neck/head/双臂/双腿）
- 7种骨骼动画：idle呼吸眨眼/happy挥手跳/confused摸头/angry握拳跺脚/sad驼背/shy捂脸/reminder跳跃提醒
- 透明WebGL背景、整窗拖拽、右键菜单7项保留、气泡提醒、好感度驱动情绪切换
- GLTFLoader预留模型替换接口（assets/3d/pet.glb，不存在则用程序化人形兜底）
- 重写 app/windows/float_ball.py，接口与 window_manager 完全兼容
- 新增 ui/pet_3d.html、ui/js/three.min.js、ui/js/GLTFLoader.js、assets/3d/README.md

### 测试
- 全量单元测试 258/258 通过（原242 + 新增 test_llm_gateway_v2 16项），0失败0错误

---

## v3.0 持续优化（2026-10-05）：多学科知识挂载 + 21维画像

### 一、多学科知识挂载体系（核心新增）
- 新增7个学科知识目录：knowledge/disciplines/ 下 psychology/law/economics/sociology/communication/biology/game_theory
- 每个学科写入 core.md 核心知识文件（理论框架 + 恋爱应用场景 + 实操指导）
- 心理学：依恋理论/情绪心理学/CBT认知扭曲/黑暗三角/Gottman四骑士/Rusbult投资模型/自我决定理论
- 法学：恋爱财产/婚姻财产制度/家庭暴力救济/隐私权/非婚生子女抚养
- 经济学：沉没成本/机会成本/消费观匹配/礼物经济学/家庭财务规划
- 社会学：社会阶层匹配/原生家庭影响/性别角色/社交网络
- 沟通学：非暴力沟通/冲突五模式/话术推拉/数字沟通陷阱
- 生物学：女性生理周期情绪/PMS/男性情绪周期/吸引力生物学
- 博弈论：囚徒困境/信任博弈/承诺信号/权力博弈/重复博弈
- RAG服务支持按学科检索：新增 disciplines 参数，FTS按source过滤、Chroma按metadata过滤
- 分析Prompt挂载多学科知识：根据场景自动调用对应学科，风险提示标注学科类型

### 二、人物画像从17维扩展到21维
- 新增激情维度（Sternberg爱情三元论）：性吸引力/浪漫冲动/情感强度，含关系阶段调整
- 新增信任维度：可靠性/善意/一致性/透明度/犯错后道歉
- 新增价值观匹配：金钱观/家庭观/事业观/生活方式/人生优先级/道德底线6话题
- 新增自我分化（Bowen理论）：区分情感理性/亲密中保持独立/融合疏离两极
- 画像自动分析（消息>=10条自动跑，每50条更新），无需手动触发

### 三、产品总纲确认并写入需求文档
- 系统定位：系统小精灵（教练而非替身），最终让用户自己学会经营关系
- 核心逻辑链：画像 -> 行为预测 -> 心理学干预 -> 任务式引导 -> 对方正反馈=奖励
- 放权机制：按阶段重置（不做线性放权），同阶段内渐进减少干预
- 用户说话习惯学习：生成回复"像用户自己说的"，不是通用AI模板
- 前后端严格边界：后端自动功能不随意加入口

### 四、知识库重建
- 重新索引FTS：381文件、18607段落、49.6MB
- 学科分布：general 15049/psychology 3441/biology 40/game_theory 18/economics 16/communication 16/sociology 14/law 13

### 五、其他
- 删除初代版本（D:\LoveAI）开机自启快捷方式
- 修复OCR单元测试用例（正确模拟OCR引擎不可用场景）
- 全量单元测试通过

## v3.1 用户说话风格学习（本地为主体，云端仅可选增强）

### 背景
核查发现"学用户本人说话习惯"此前并未真正实现：`prompts/reply_style.txt` 只是 balanced/gentle/humor 等风格偏好模板，无任何 .py 代码引用；LLMGateway 的 mark_good_reply/training_data 是给 LoRA 喂数据，不是学本人口吻。本轮从无到有落地。

### 新增
- 数据库新增 `user_speech_style` 表（单行 id=1：profile_json / sample_count / avg_length / cloud_summary / source / updated_at）
- 新增 `services/style_learner.py`（StyleLearner，纯 Python 不 import PySide6）：
  - 本地采集：只取 `direction='sent'`（本人发出）消息，正确排除 received
  - 本地规则统计（离线可用）：平均/中位句长与短中长占比、语气词、句尾字、跨消息标志性短语、标点密度、emoji 与微信表情包 [xxx]、疑问/感叹/短句堆叠句式、活跃时段与回复间隔
  - 本地归纳 traits：简洁惜字 / 表达详尽 / 语气词多口语化 / 情绪外放 / 常带追问 / 含蓄留白 / 表情丰富 / 疑问互动 / 连发短句 / 平实中性
  - 本地存储：UPSERT 入库 + 可读 user_style.md（与主库同目录，frozen 路径一致）
  - 云端增强（可选）：仅 L3 可用时把 ≤40 条样本送云端抽象归纳，结果合并回本地 cloud_summary；无网/超时/失败静默跳过，本地档案照常
  - 自动触发：分析流水线开头调 maybe_update，本人消息新增 ≥20 条才重算，否则秒回；云端增强每 200 条最多一次以省 token

### 闭环接入
- application.py：phase4 末尾注册第 7 个服务 style_learner（服务总数 27）
- service_facade.py：新增 _style_learner property；_run_analysis 构建 system_prompt 时注入 build_style_context()；新增对外方法 learn_speech_style / get_speech_style
- prompts/chat_analysis.txt：新增【用户本人说话风格】{{user_style}} 区块；第 5 条要求 3 条回复都模仿本人句长/语气词/口头禅/表情，不写统一书面腔

### 数据流（符合"本地为主体"要求）
本地采集本人消息 → 本地规则统计入库（主体）→（云端仅可选增强）→ 结果回到本地存储 → 回复生成时从本地读取注入。

### 测试
- 新增 tests/test_style_learner.py 14 项：样本不足 / sent-received 区分 / 语气词 / 句尾 / 口头禅 / 标点 / 表情包 / traits / 持久化与 md / 注入文本 / 阈值触发 / 无 LLM 降级 / mock LLM 云端合并 / 云端失败降级
- 真实端到端验证通过（25 条本人 + 5 条对方，样本数正确为 25，口头禅"行吧"、表情包 [捂脸] 均识别）
- 全量单元测试 **272/272 通过**（原 258 + 新增 14），0 失败 0 错误 0 跳过

### 说明
本轮按用户要求**先不封包**；功能验证无误后是否重封由用户再定。



---

## v3.0 - UI绑定修复 + 后台调度 + 端到端验证（2026-10-06）

### UI绑定修复
- 修复截图分析勾选框 `_screenshot_check`：勾选后点分析时自动截屏+OCR，识别文字追加到聊天文本一起分析（OCR失败不阻塞）
- 设置对话框新增"说话风格"分组：重新学习说话风格按钮（带loading状态）+ 查看当前风格按钮
- 修复死控件 `_search_edit` 对象搜索框：连接returnPressed信号，按关键字匹配并切换对象
- 全面排查6个widget文件，其余控件均已正确连接

### 后台定时调度器
- 新建 `app/background_scheduler.py`：BackgroundUpdater（纯Python，零Qt依赖）+ BackgroundScheduler(QObject，仅QTimer触发)
- 默认每5分钟自动跑：好感度衰减→记忆整理→21维画像→财务提取→数字分身预测
- 新消息信号触发60秒内加速跑一轮
- 并发保护（上一轮没跑完则跳过）、单人物30秒超时、异常隔离（单人失败不影响他人）、空闲跳过
- 配置项：background.enabled / background.interval_minutes
- 挂载到 application.py：启动时start，退出时stop

### 端到端验证
- 构造测试人设"蛋卷"（25岁女生，回避型依恋，恋爱3年近期冷淡）+ 384条真实感聊天记录（覆盖8种场景）
- 全链路11环节跑通：聊天分析8模块→情感标签→言外之意→回复建议→好感度18维更新（7/18维变化）→21维画像（22个维度块）→数字分身预测→财务提取（8条信号）→说话风格学习（160样本）→任务引擎（3任务）→勋章判定（4个新解锁）
- LLM不可用时规则兜底路径有真实输出；依赖LLM JSON的环节（言外之意/回复建议/分身详情）降级为空，已如实记录
- 报告：E2E验证报告.md，脚本：tools/e2e_test.py（可重复运行，临时库不污染正式数据）

### 测试
- 全量单元测试：281个通过（原272 + 新增9个后台调度器测试），0失败0错误
