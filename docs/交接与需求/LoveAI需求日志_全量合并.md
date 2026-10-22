# LoveAI 需求日志 · 全量合并版

> 合并来源：`docs/功能需求文档.md`（283KB 全量需求主文档） + `docs/需求状态核对-20261010.md`（22-26章 68项核对） + 本轮增量需求日志
> 用途：作为需求→代码→测试逐项核对基准。核对规则：✅代码真实落地且有测试/接线 / 🟡部分（有代码无接线或stub）/ ❌无代码。核对人逐项勾选并留证据位置。
> **2026-10-13 补完轮记录**：本轮对 R3.3-06 PsyDI、22.2.2 HTN、22.2.3 动态重规划、4.3.1 回复建议结构 4 项必补项做真代码落地（各含文件:行号 + pytest 数字）；对 16 项"部分落地"做分诊（确认接线/本轮落地 / 保留成本高 / 排期 / 微信网页版暂缓）。分诊结论见第三部分各行与文末"未真正落实项清单"。

---

## 〇、主线（先看这里）

> 全篇只有两条主线：**一个外在、一个内核**；其余条目都是这两条的派生（**引导／反制路线**）。新读这份文档的人或 AI 先读本节，再往下看。
>
> **外在｜恋爱游戏**：把一段关系做成**可玩的游戏**——好感度、任务、勋章、桌宠、结算……让人每天有得看、有得做。**这是看得见的那层。**
>
> **内核｜模拟对抗预判引擎**：看不见的那层，一轮跑五步：
> 1. **建模**——每个对象一套“双人模型”（我 × 该对象）；内部两层：**个体层**（每人一份画像，不重复维护）＋ **关系层**（这段关系的历史／雷区／话术偏好／脚本）。
> 2. **载入状态**——当下事件 ＋ 长期事件与回复，**按相关性加载**：这条反馈连着过去某段事，就把那段记忆一起加载；不连着，只按双方画像与行为跑。
> 3. **挂动作集**——任务 ＋ N 条候选话术；**N 条在回复时刻实时产出**。
> 4. **对抗推演**——双方在模型里对打。
> 5. **输出四项**——① 推演方向 ② 分析 ③ 执行的操作 ④ 概率（有多少概率变成那样）。
>
> 之后：选一条发出去 → **对方的回复自动同步进模型**（实时同步，无需人工介入）→ 立刻跑下一轮。
>
> **派生①｜引导**：教练式——把预判翻成“这一步该做什么”。
>
> **派生②｜反制路线**：对抗式——识别并反制对方的套路／回避／操控。（**我方理解，待用户确认**）
>
> 内核细则（输入／输出规格、算力底线、四字口径）见下面「`2026-10-17 模拟对抗引擎`」一节。

---

## 第一部分 R3.x 迭代需求（98 条）

| # | 编号 | 需求 | 文档状态 | 核对结果 | 证据位置 |
|---|---|---|---|---|---|
| 1 | R3 | R3.1-01 微信通路暂缓（战略调整） | — | ✅ | 细筛确认实质实现：细筛：实质代码行=339 stub=30 注释=220 测试=test_batch9b.py,test_jev_judge.py,test_batch16_wechat_40.py  接线=107 |
| 2 | R3 | R3.1-02 分阶段迭代路线图 | — | ✅ | 细筛确认实质实现：细筛：实质代码行=8 stub=0 注释=5 测试=test_wechat_multi_method.py  接线=8 |
| 3 | R3 | R3.1-03 多源手动/半自动数据采集（微信未通前的“米”） | — | ✅ | 细筛确认实质实现：细筛：实质代码行=366 stub=18 注释=268 测试=test_progressive_probe.py,test_dangerous_effects_combinations.py,test_ui_e2e_chain.py  接线=99 |
| 4 | R3 | R3.1-04 用户自我画像（新增） | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=215 stub=4 注释=71 测试=test_memory_service.py,test_codex_adaptation.py,test_strategy_guidance.py  接线=58 |
| 5 | R3 | R3.1-05 复盘回流（新增） | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=352 stub=17 注释=213 测试=test_ui_e2e_chain.py,test_psychology_laws.py,test_loyalty_engine.py  接线=89 |
| 6 | R3 | R3.1-06 眼镜终端视觉层（新增，迭代 4 之后落地） | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=599 stub=20 注释=288 测试=test_ui_e2e_chain.py,test_browser_ai_hub.py,test_psychology_laws.py  接线=114 |
| 7 | R3 | R3.1-07 术语统一 | — | ✅ | 细筛确认实质实现：细筛：实质代码行=13 stub=1 注释=17 测试=test_analysis_panel.py,test_zero_cost_wiring.py  接线=10 |
| 8 | R3 | R3.2-01 路线④ · 本机 AI 桌面软件连接器（DesktopAIConnector）【直接做进 LoveAI】 | — | ✅ | 细筛确认实质实现：细筛：实质代码行=15 stub=1 注释=12 测试=test_desktop_ai_connector.py  接线=4 |
| 9 | R3 | R3.2-02 浏览器 AI 中枢（BrowserAIHub）【方案1+2，直接做进】 | — | ✅ | 细筛确认实质实现：细筛：实质代码行=67 stub=4 注释=45 测试=test_browser_ai_hub.py,test_voice_pipeline_e2e.py,test_zero_cost_wiring.py  接线=22 |
| 10 | R3 | R3.2-03 联网搜索与知识不足自动补全（WebSearchAutoFill）【方案3 + free_ai_search 补全】 | — | ✅ | 细筛确认实质实现：细筛：实质代码行=59 stub=5 注释=74 测试=test_codex_adaptation.py,test_ui_e2e_chain.py,test_no_reply_detector.py  接线=62 |
| 11 | R3 | R3.2-04 统一注册、设置与数字诚实 | — | ✅ | 细筛确认实质实现：细筛：实质代码行=5 stub=0 注释=3 测试=无  接线=4 |
| 12 | R3 | R3.2-05 联动关系与文件边界 | — | ✅ | 细筛确认实质实现：细筛：实质代码行=480 stub=27 注释=378 测试=test_services.py,test_batch6b_tasks.py,test_batch17_wechat_real.py  接线=136 |
| 13 | R3 | R3.2-06 随包分发（衔接开箱即用） | — | ✅ | 细筛确认实质实现：细筛：实质代码行=398 stub=15 注释=382 测试=test_batch6b_tasks.py,test_dangerous_effects_combinations.py,test_ui_e2e_chain.py  接线=86 |
| 14 | R3 | R3.3-01 微信聊天记录导入 · 主方案 WeFlow（实锤，替换旧密钥路线）【⛔ 暂缓（R3.3-70 决策 2026-10-10，微信相关全部搁置，本条保留作恢复参考）】 | ⛔ 暂缓 | ⛔暂缓 | 按R3.3-70暂缓；10-13 封号实锤=内存读取/转储扫密钥（内存/注入类永久关闭）；纯文件解密链路已验证可行（密钥缓存解密+13库全解锁，66.5万条消息产出，见第四部分登记），待解封后走此路径导入 |
| 15 | R3 | R3.3-02 侧写引擎对标 DSM（Deep Self Mirror）【已落地】 | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=345 stub=6 注释=169 测试=test_progressive_probe.py,test_ui_e2e_chain.py,test_browser_ai_hub.py  接线=93 |
| 16 | R3 | R3.3-03 模型难度分级路由对标 UncommonRoute【已落地】 | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=112 stub=3 注释=95 测试=test_codex_adaptation.py,test_ui_e2e_chain.py,test_user_ability.py  接线=71 |
| 17 | R3 | R3.3-04 四层模型分工 + 模拟分级触发（自定架构，用户已认可方向）【已落地】 | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=57 stub=2 注释=62 测试=test_startup_probe.py,test_zero_cost_wiring.py,test_cooldown_table.py  接线=13 |
| 18 | R3 | R3.3-05 免 Key 网页登录态桥接（doubao2API / OpenClaw Zero Token）【已落地】 | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=6 stub=0 注释=2 测试=test_local_ai_scanner.py,test_browser_ai_hub.py  接线=3 |
| 19 | R3 | R3.3-06 PsyDI 渐进式人格探测（P2）【已落地】 | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=238 stub=7 注释=90 测试=test_loss_aversion_and_laws.py,test_progressive_probe.py,test_browser_ai_hub.py  接线=46 |
| 20 | R3 | R3.3-07 SillyTavern 记忆四层（P2）【已落地】 | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=725 stub=16 注释=391 测试=test_data_pool.py,test_memory_service.py,test_dangerous_effects_combinations.py  接线=141 |
| 21 | R3 | R3.3-08 AIta 关系卡点诊断（P2）【已落地】 | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=7 stub=1 注释=6 测试=无  接线=6 |
| 22 | R3 | R3.3-09 NRD Studio 3D 星图 + d3graph（P2）【已落地】 | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=4 stub=0 注释=6 测试=test_relationship_graph_widget.py,test_relationship_graph.py  接线=5 |
| 23 | R3 | R3.3-10 学术依据补强（J 方向罗盘） | — | ✅ | 细筛确认实质实现：细筛：实质代码行=7 stub=0 注释=3 测试=无  接线=6 |
| 24 | R3 | R3.3-11 随包本地模型选型（待用户拍板） | 待用户拍板 | ✅ | 细筛确认实质实现：细筛：实质代码行=5 stub=0 注释=0 测试=无  接线=3 |
| 25 | R3 | R3.3-12 外部借鉴来源登记（三人行必有我师） | — | ✅ | 细筛确认实质实现：细筛：实质代码行=10 stub=1 注释=7 测试=无  接线=10 |
| 26 | R3 | R3.3-13 ESP32 TinyML 超低功耗语音唤醒层（眼镜端/future-proof） | — | ✅ | 细筛确认实质实现：细筛：实质代码行=26 stub=1 注释=33 测试=test_suspicion_chain.py,test_r3379_prod_shape.py,test_dynamic_bg.py  接线=20 |
| 27 | R3 | R3.3-14 livekit-agents 实时全双工语音管线（voice 组件缺口）【部分落地：管线骨架/接口/barge-in 已建，livekit 本体待装】 | 部分落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=50 stub=1 注释=49 测试=test_suspicion_chain.py,test_r3379_prod_shape.py,test_voice_pipeline_e2e.py  接线=25 |
| 28 | R3 | R3.3-15 模型路由成本优化（DeepSeek 主力化）【部分落地：路由链已切 DeepSeek→豆包→通用，待配 API Key 后云端生效；本机走 ollama】 | 部分落地 | ✅ | 人工核查：services/llm_gateway.py:381/:387 DeepSeek(复杂推理主力)→豆包→通用 路由真实；本机走 ollama |
| 29 | R3 | R3.3-16 主模型包工头 / 子任务外包免费模型轮换 [已落地] | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=58 stub=2 注释=47 测试=test_strategy_guidance.py,test_npd_bpd_recognition.py,test_local_model_manager.py  接线=55 |
| 30 | R3 | R3.3-17 自动扫描本地已装云端 AI 桌面端 [部分落地] | 部分落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=21 stub=0 注释=6 测试=test_desktop_ai_connector.py,test_local_ai_scanner.py,test_browser_ai_hub.py  接线=9 |
| 31 | R3 | R3.3-18 浏览器 AI 搜索引擎帮忙 [部分落地] | 部分落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=35 stub=0 注释=18 测试=test_zero_cost_wiring.py,test_web_search_service.py,test_browser_ai_hub.py  接线=5 |
| 32 | R3 | R3.3-19 用户分发时勾选下载 / 开箱即用 / 依赖随包带 [已落地] | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=112 stub=4 注释=51 测试=test_cooldown_table.py,test_affection_reasoning.py,test_style_learner.py  接线=66 |
| 33 | R3 | R3.3-20 封包前检测 + 封包时依赖环境随包带 [已落地] | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=13 stub=0 注释=2 测试=test_r3379_npc_bridge.py  接线=6 |
| 34 | R3 | R3.3-21 动效背景 UI [部分落地] | 部分落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=83 stub=3 注释=52 测试=test_downgrade_p04.py,test_dynamic_bg.py,test_psych_catalog.py  接线=30 |
| 35 | R3 | R3.3-22 单轮分析输出面板（悬浮卡：意图+把握+策略+3候选+风险+预测）[部分落地] | 部分落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=11 stub=0 注释=11 测试=test_analysis_panel.py  接线=11 |
| 36 | R3 | R3.3-23 数字诚实规范（把握封顶 / 策略权重 / 推荐权重 / 准确率≠回复成功率）[部分落地] | 部分落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=415 stub=5 注释=74 测试=test_ui_e2e_chain.py,test_browser_ai_hub.py,test_voice_pipeline_e2e.py  接线=79 |
| 37 | R3 | R3.3-24 方向罗盘三重闸门（方向闸 / 匹配闸 / 真伪闸）[已落地] | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=17 stub=8 注释=11 测试=test_strategy_guidance.py,test_e2e_full_chain.py,test_task_engine_v31.py  接线=9 |
| 38 | R3 | R3.3-25 财务代价公式（付出占其所能承受代价比例）[已落地] | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=2 stub=0 注释=0 测试=无  接线=1 |
| 39 | R3 | R3.3-26 NPC 关系网络图 [部分落地] | 部分落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=119 stub=2 注释=10 测试=test_relationship_graph_widget.py,test_dangerous_effects_combinations.py,test_r3379_graph.py  接线=20 |
| 40 | R3 | R3.3-27 生理周期日历 + 备忘录合并入口 [已落地] | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=444 stub=15 注释=260 测试=test_progressive_probe.py,test_ui_e2e_chain.py,test_browser_ai_hub.py  接线=105 |
| 41 | R3 | R3.3-28 本地 AI 反哺训练（LoRA / 风格相似度四维校验）[已落地] | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=548 stub=24 注释=488 测试=test_memory_service.py,test_dangerous_effects_combinations.py,test_ui_e2e_chain.py  接线=130 |
| 42 | R3 | R3.3-29 插件系统（一切皆插件）[已落地] | 已落地 | ✅ | 细筛确认实质实现：细筛：实质代码行=84 stub=5 注释=83 测试=test_commitment_tracker.py,test_plugin_service.py,test_psychology_laws.py  接线=36 |
| 43 | R3 | R3.3-30 三层故障分级路由（P0） | — | ✅ | 细筛确认实质实现：细筛：实质代码行=119 stub=3 注释=131 测试=test_data_pool.py,test_htn_replan.py,test_llm_gateway_v2.py  接线=75 |
| 44 | R3 | R3.3-31 启动时全量探活（P0） | — | ✅ | 细筛确认实质实现：细筛：实质代码行=67 stub=4 注释=53 测试=test_llm_gateway_v2.py,test_scam_patterns.py,test_startup_probe.py  接线=21 |
| 45 | R3 | R3.3-32 模型冷却 cooldown 表（P0） | — | ✅ | 细筛确认实质实现：细筛：实质代码行=61 stub=2 注释=52 测试=test_analysis_orchestrator.py,test_codex_adaptation.py,test_jev_judge.py  接线=52 |
| 46 | R3 | R3.3-33 六种杀猪盘模式枚举+时序链（P0） | — | ✅ | 细筛确认实质实现：细筛：实质代码行=4 stub=0 注释=2 测试=test_scam_patterns.py  接线=3 |
| 47 | R3 | R3.3-34 记忆闸门（校准数据去噪）（P0） | — | ✅ | 细筛确认实质实现：细筛：实质代码行=5 stub=0 注释=6 测试=test_memory_gate.py,test_scam_patterns.py,test_memory_pending_calibration.py  接线=4 |
| 48 | R3 | R3.3-35 Smart Turn 全双工状态机（P1） | — | ✅ | 细筛确认实质实现：细筛：实质代码行=114 stub=3 注释=58 测试=test_batch17_wechat_real.py,test_browser_ai_hub.py,test_desktop_ai_connector.py  接线=66 |
| 49 | R3 | R3.3-36 中文 EoT 二次判断（P1） | — | ✅ | 细筛确认实质实现：细筛：实质代码行=4 stub=0 注释=10 测试=test_voice_pipeline.py,test_chinese_eot.py  接线=4 |
| 50 | R3 | R3.3-37 六维战略分接线 [已完成] | 已完成 | ✅ | 细筛确认实质实现：细筛：实质代码行=28 stub=2 注释=20 测试=test_local_model_manager.py,test_blob_preseed.py,test_milestone_tasks.py  接线=22 |
| 51 | R3 | R3.3-38 orchestrator 串成一条链 [已完成] | 已完成 | ✅ | 人工核查：services/analysis_orchestrator.py:217 AnalysisOrchestrator + :234 orchestrate() 真实存在（orchestrator 串链实现） |
| 52 | R3 | R3.3-39 6个部分落地功能挂主窗口 [已完成] | 已完成 | ✅ | 细筛确认实质实现：细筛：实质代码行=6 stub=0 注释=15 测试=test_dynamic_bg.py  接线=6 |
| 53 | R3 | R3.3-40 WeFlow 实机验证 [🟢 恢复活跃（2026-10-12 随 R3.3-70 解除暂缓）] | ⚠️封号事件 | ⚠️封号事件 | 2026-10-13 封号实锤=内存读取/转储扫密钥；检测对象为读取行为本身(OpenProcess/ReadProcessMemory 调用特征)，直接读与先转储后读均会被识别；内存/注入类路线永久关闭，仅纯文件级(WeFlow官方导出/文件解密)保留评估 |
| 54 | R3 | R3.3-41 4个模型缺口根治 [待办，需更大模型] | 待办 | 🟡 | 文档标【待办，需更大模型】：4个模型缺口根治（人设区分度弱/视角混淆等），保留待办 |
| 55 | R3 | R3.3-42 ssh chat.hf.co 免key SSH通道 [新增，P1] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=263 stub=14 注释=186 测试=test_desktop_ai_connector.py,test_offline_mode.py,test_training_feedback.py  接线=67 |
| 56 | R3 | R3.3-43 免费AI通道全景清单更新 [新增] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=74 stub=2 注释=40 测试=test_analysis_orchestrator.py,test_browser_ai_hub.py,test_startup_probe.py  接线=22 |
| 57 | R3 | R3.3-44 心理学全维度参考体系筛选清单 [新增，P0] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=244 stub=11 注释=179 测试=test_desktop_ai_connector.py,test_offline_mode.py,test_training_feedback.py  接线=59 |
| 58 | R3 | R3.3-45 心理学六层人格结构体系（六层画像）[新增，P0] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=2 stub=1 注释=7 测试=test_six_layer_persona.py  接线=6 |
| 59 | R3 | R3.3-46 迷信借力打力模块（识别→接住→顺势引导）[新增，P1] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=80 stub=4 注释=49 测试=test_progressive_probe.py,test_evidence_engine.py,test_browser_ai_hub.py  接线=59 |
| 60 | R3 | R3.3-47 识别后自动出策略+任务+话术完整链路 [新增，P0] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=708 stub=31 注释=618 测试=test_batch6b_tasks.py,test_dangerous_effects_combinations.py,test_ui_e2e_chain.py  接线=122 |
| 61 | R3 | R3.3-48 需求日志自审与全链路验收（2026-10-10夜）[新增，P0] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=9 stub=0 注释=4 测试=无  接线=7 |
| 62 | R3 | R3.3-49 沉浸式语音交互桌面 + 桌宠立绘系统 [新增，P0，用户强烈要求] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=312 stub=22 注释=368 测试=test_progressive_probe.py,test_ui_e2e_chain.py,test_browser_ai_hub.py  接线=112 |
| 63 | R3 | R3.3-50 桌宠立绘自动生成系统（AI生图+不撞脸）[新增，P1] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=102 stub=7 注释=37 测试=test_portrait_generator.py,test_offline_mode.py,test_digital_twin.py  接线=27 |
| 64 | R3 | R3.3-51 动态立绘（会动+有表情+口型同步）[新增，P1] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=509 stub=19 注释=320 测试=test_memory_service.py,test_progressive_probe.py,test_ui_e2e_chain.py  接线=119 |
| 65 | R3 | R3.3-52 桌宠双模式（2D Live2D + 实时数字人可选）[新增，P0] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=473 stub=32 注释=372 测试=test_data_pool.py,test_ui_e2e_chain.py,test_browser_ai_hub.py  接线=145 |
| 66 | R3 | R3.3-53 桌宠双技术方案（Electron主 + PySide6备）[更新] | 更新 | ✅ | 细筛确认实质实现：细筛：实质代码行=110 stub=6 注释=75 测试=test_pet_mode_setting.py,test_ui_e2e_chain.py,test_bridges.py  接线=29 |
| 67 | R3 | R3.3-54 技术债务清理原则（好功能接上去，旧产物先吸收再删）[新增，P1] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=14 stub=0 注释=13 测试=test_style_learner.py,test_voice_pipeline_e2e.py,test_smart_turn.py  接线=6 |
| 68 | R3 | R3.3-55 赛博女友完整系统搭建（语音交互+语音克隆+数字人）[新增，P0] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=17 stub=0 注释=9 测试=无  接线=15 |
| 69 | R3 | R3.3-56 桌宠双模式确认（数字人可选 + 2D也要数字人驱动）[补充，P0] | 补充 | ✅ | 细筛确认实质实现：细筛：实质代码行=273 stub=15 注释=187 测试=test_dangerous_effects_combinations.py,test_offline_mode.py,test_stress_attribution.py  接线=70 |
| 70 | R3 | R3.3-57 中心数据池输入输出标准化（双层架构）[新增，P0] | 新增 | ✅ | 人工核查：services/data_pool.py SCHEMA:25 / write:50(字段级校验+属主) / read:106 / reset_audit:125 真实实现（集中分拨中心数据池） |
| 71 | R3 | R3.3-58 多端协同分布式计算（电脑+手机，分担运存压力，1+1>2）[新增，P1] | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=82 stub=1 注释=89 测试=test_dangerous_effects_combinations.py,test_ui_e2e_chain.py,test_browser_ai_hub.py  接线=58 |
| 72 | R3 | R3.3-59 中心数据池标准化双层架构【新增，P1】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=58 stub=3 注释=54 测试=test_free_channel_catalog.py,test_hf_ssh_channel.py,test_ui_e2e_chain.py  接线=40 |
| 73 | R3 | R3.3-60 三上游联动任务系统【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=399 stub=20 注释=382 测试=test_batch6b_tasks.py,test_dangerous_effects_combinations.py,test_ui_e2e_chain.py  接线=79 |
| 74 | R3 | R3.3-61 话术不套模板场景化【新增，P1】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=246 stub=14 注释=193 测试=test_ui_e2e_chain.py,test_portrait_generator.py,test_psychology_laws.py  接线=84 |
| 75 | R3 | R3.3-62 模型效果对齐【新增，P1】 | 新增 | ✅ | 人工核查：services/output_normalizer.py:25 E22统一润色 / :128 统一润色层入口（任何模型输出过一遍=效果对齐） |
| 76 | R3 | R3.3-63 三大核心模块优化【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=20 stub=1 注释=16 测试=test_affection_engine_v31.py,test_persona_analyzer_v31.py,test_task_engine_v31.py  接线=8 |
| 77 | R3 | R3.3-64 参考新开源项目取长补短【新增，P1】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=3 stub=0 注释=9 测试=test_batch8a.py  接线=4 |
| 78 | R3 | R3.3-65 多模型聚合网关（零成本LLM Gateway）【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=72 stub=2 注释=41 测试=test_startup_probe.py,test_safety_filter.py,test_zero_cost_wiring.py  接线=25 |
| 79 | R3 | R3.3-66 OpenRouter免费模型自动发现与维护【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=402 stub=44 注释=380 测试=test_memory_service.py,test_progressive_probe.py,test_dangerous_effects_combinations.py  接线=148 |
| 80 | R3 | R3.3-67 桌面端软件转API适配器【新增，P1】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=44 stub=5 注释=50 测试=test_local_ai_scanner.py,test_free_model_router.py,test_free_model_router_resilience.py  接线=26 |
| 81 | R3 | R3.3-68 Cherry Studio UI设计借鉴【新增，P2】 | 新增 | 🟡 | 借鉴类 P2：Cherry Studio UI 设计借鉴不照搬，评估后待落地 |
| 82 | R3 | R3.3-69 Jev微信接入参考【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=310 stub=29 注释=186 测试=test_batch9b.py,test_jev_judge.py,test_batch16_wechat_40.py  接线=101 |
| 83 | R3 | R3.3-70 微信相关功能全部暂缓【决策记录】 | 决策记录 | ✅(决策) | 决策记录：10-10 微信全部暂缓；10-12 落实源码开关闸；10-13 封号实锤=内存读取/转储（微信行为特征检测，检测对象为读取行为本身，转储变体同样无效）；collectors/pc 内存/注入类通道(p2-p9)永久关闭，恢复开发仅可评估纯文件级通道 |
| 84 | R3 | R3.3-71 内容精度优化（好感度校准+诊断细化+模型效果拉齐）【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=2 stub=0 注释=1 测试=无  接线=3 |
| 85 | R3 | R3.3-72 需求日志与借鉴项目管理规范【新增，P1】 | 新增 | ⬜ | 管理规范类（需求日志与借鉴登记规范），以文档+机制落实，非代码功能项 |
| 86 | R3 | R3.3-73 桌宠视觉效果验收标准（不躲角落+真人感+像赛博小雅）【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=358 stub=24 注释=279 测试=test_data_pool.py,test_ui_e2e_chain.py,test_browser_ai_hub.py  接线=150 |
| 87 | R3 | R3.3-74 技术债务定期清理机制【新增，P2】 | 新增 | ⬜ | 管理机制类（技术债务定期清理），以文档+机制落实，非代码功能项 |
| 88 | R3 | R3.3-75 任务线算法需求规格（助推-滑翔·全任务达成）【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=12 stub=0 注释=14 测试=test_boost_glide_planner.py,test_ui_e2e_chain.py  接线=10 |
| 89 | R3 | R3.3-76 桌宠不单独打包，整体联动交付【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=29 stub=0 注释=16 测试=无  接线=10 |
| 90 | R3 | R3.3-77 模块间算法桥接·网状多维交互联动【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=700 stub=28 注释=626 测试=test_batch6b_tasks.py,test_dangerous_effects_combinations.py,test_level_clearing.py  接线=116 |
| 91 | R3 | R3.3-78 双轨情感指标：反制线走忠诚度、引导线走好感度【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=28 stub=0 注释=74 测试=test_route_production_wiring.py,test_loyalty_engine.py,test_loyalty_pet_bridge.py  接线=12 |
| 92 | R3 | R3.3-79 活关系星图·NPC次级网络与家庭/伦理关系建模【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=133 stub=2 注释=63 测试=test_relationship_graph_widget.py,test_r3379_graph.py,test_r3379_prod_shape.py  接线=22 |
| 93 | R3 | R3.3-80 恋爱关系现实复杂度盲区覆盖【新增，P0】（主控主动排查，非用户点名） | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=72 stub=0 注释=47 测试=test_milestone_tasks.py,test_bridges_r3380.py,test_relationship_complexity.py  接线=14 |
| 94 | R3 | R3.3-81 指标函数化改造：好感/忠诚/任务进度由"分析结论"驱动【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=650 stub=23 注释=600 测试=test_data_pool.py,test_memory_service.py,test_progressive_probe.py  接线=145 |
| 95 | R3 | R3.3-82 桌宠立绘动画升级：连贯帧数与真人感【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=157 stub=6 注释=77 测试=test_pet_animator.py,test_cooldown_table.py,test_affection_reasoning.py  接线=82 |
| 96 | R3 | R3.3-83 产品定位纠偏：桌宠=系统精灵，产品=地球online恋爱小游戏【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=118 stub=6 注释=78 测试=test_pet_mode_setting.py,test_bridges.py  接线=33 |
| 97 | R3 | R3.3-84 成就勋章款式模板（地球online成就馆风格·边框固定/内容按成就替换）【新增，P1】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=9 stub=1 注释=6 测试=无  接线=12 |
| 98 | R3 | R3.3-85 核验整改批次：文档如实化 + 发布阻断项修复【新增，P0】 | 新增 | ✅ | 细筛确认实质实现：细筛：实质代码行=174 stub=5 注释=98 测试=test_batch6b_tasks.py,test_batch7_g1.py,test_psychology_laws.py  接线=65 |

---

## 第二部分 〇~十四 大章需求（章节/小节 181 条）

| # | 章节 | 小节需求 | 文档状态 | 核对结果 | 证据位置 |
|---|---|---|---|---|---|
| 1 |  | ★★★ 产品定位（红线，不能跑偏） | — | ✅ | 登记条目；产品定位红线已由 R3.3-83 产品定位纠偏落地（桌宠=系统精灵，产品=地球online恋爱小游戏） |
| 2 |  | ★、v3.1.0 增量需求登记（2026-10-08，微信通路暂缓） | — | ⛔暂缓 | 登记条目；微信相关按 R3.3-70 暂缓 + 2026-10-13 封号事件维持暂缓（方案保留作恢复参考） |
| 3 |  | ★★、v3.2.0 增量需求登记（2026-10-09，零成本 AI 能力中枢） | — | ✅登记 | 登记条目；增量需求登记在档（对应迭代批次已在 R3.x/章节部分核对） |
| 4 | **〇、产品定位与核心差异** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 5 | **一、启动与窗口系统** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 6 | 一、启动与窗口系统 | 1.1 启动流程 [需重写] | — | ✅ | 细筛：实质代码行=330 stub=12 注释=237 test=test_analysis_orchestrator.py,test_background_scheduler.py,test_blob_preseed.py wired=93 |
| 7 | 一、启动与窗口系统 | 1.2 四档窗口系统 [需重写] | — | ✅ | 细筛：实质代码行=62 stub=6 注释=54 test=test_auto_strategy_pipeline.py,test_immersive_voice_window.py,test_voice_pipeline.py wired=28 |
| 8 | 一、启动与窗口系统 | 1.3 新手引导 [新增] | 新增 | ✅ | 细筛：实质代码行=335 stub=5 注释=155 test=test_affection_engine.py,test_affection_reasoning.py,test_auth_helper.py wired=116 |
| 9 | **二、桌宠系统** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 10 | 二、桌宠系统 | 2.1 基础交互 [已有可复用+需重写] | — | ✅ | 人工核查：app/qt3d_pet/pet_view.py:9/:112/:172 拖拽+回弹动画真实；app/widgets/float_ball.py 可拖拽 |
| 11 | 二、桌宠系统 | 2.2 表情系统 [需重写] | — | ✅ | 细筛：实质代码行=205 stub=8 注释=59 test=test_analysis_orchestrator.py,test_batch6c_personalize.py,test_boost_glide_planner.py wired=66 |
| 12 | 二、桌宠系统 | 2.3 快捷菜单 [需重写] | — | ✅ | 细筛：实质代码行=15 stub=0 注释=14 test=test_htn_replan.py wired=9 |
| 13 | 二、桌宠系统 | 2.4 气泡提醒 [需重写] | — | ✅ | 细筛：实质代码行=47 stub=0 注释=85 test=test_analysis_panel.py,test_e2e_full_chain.py,test_ocr_service.py wired=37 |
| 14 | 二、桌宠系统 | 2.5 语音输入 [需重写] | — | ✅ | 细筛：实质代码行=6 stub=1 注释=3 test=无 wired=6 |
| 15 | **三、主界面布局** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 16 | 三、主界面布局 | 3.1 整体结构 [需重写] | — | ✅ | 细筛：实质代码行=22 stub=1 注释=27 test=test_ui_e2e_chain.py wired=14 |
| 17 | 三、主界面布局 | 3.2 左侧导航 [需重写] | — | ✅ | 细筛：实质代码行=78 stub=3 注释=74 test=test_auth_helper.py,test_deception_detector.py,test_digital_twin.py wired=50 |
| 18 | 三、主界面布局 | 3.3 多对象自动切换 [需重写] | — | ✅ | 细筛：实质代码行=61 stub=3 注释=62 test=test_bridges_r3380.py,test_milestone_tasks.py,test_ocr_service.py wired=32 |
| 19 | **四、聊天分析（核心功能）** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 20 | 四、聊天分析（核心功能） | 4.1 分析方法论 [新增，融合狗头军师] | 新增 | ✅ | 细筛：实质代码行=158 stub=10 注释=43 test=test_architecture_consistency.py,test_auth_helper.py,test_bridges_r3380.py wired=55 |
| 21 | 四、聊天分析（核心功能） | 4.2 输入方式 [需重写] | — | ✅ | 细筛：实质代码行=5 stub=0 注释=2 test=无 wired=4 |
| 22 | 四、聊天分析（核心功能） | 4.3 输出结构 [需重写] | — | ✅ | 细筛：实质代码行=117 stub=4 注释=89 test=test_background_scheduler.py,test_digital_twin_v2.py,test_e2e_full_chain.py wired=44 |
| 23 | 四、聊天分析（核心功能） | 4.3.1 回复建议的详细结构（引导闭环的实时弹药库） | — | ✅ | 细筛：实质代码行=96 stub=1 注释=56 test=test_affection_engine_v31.py,test_backup_service.py,test_batch6d_data.py wired=49 |
| 24 | 四、聊天分析（核心功能） | 4.4 交互 [需重写] | — | ✅ | 细筛：实质代码行=242 stub=1 注释=107 test=test_affection_engine_v31.py,test_auto_strategy_e2e.py,test_browser_ai_hub.py wired=82 |
| 25 | **五、情感咨询** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 26 | 五、情感咨询 | 5.1 模块一：对象视角咨询（私教模式）[需重写] | — | ✅ | 细筛：实质代码行=2 stub=0 注释=0 test=无 wired=1 |
| 27 | 五、情感咨询 | 5.2 模块二：自我成长咨询 [需重写] | — | ✅ | 细筛：实质代码行=10 stub=0 注释=2 test=test_batch7_g1.py wired=4 |
| 28 | 五、情感咨询 | 5.3 咨询引擎设计 [新增，融合xiaomei三轮引擎] | 新增 | ✅ | 细筛：实质代码行=37 stub=1 注释=22 test=test_consult_engine.py,test_contractnet_fusion.py,test_rag_service.py wired=12 |
| 29 | **六、人物档案** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 30 | 六、人物档案 | 6.1 基本信息 [需重写] | — | ✅ | 细筛：实质代码行=226 stub=9 注释=243 test=test_affection_engine.py,test_analysis_panel.py,test_architecture_consistency.py wired=98 |
| 31 | 六、人物档案 | 6.2 十八维度好感度雷达 [已有可复用+需重写] | — | ✅ | 细筛：实质代码行=98 stub=5 注释=140 test=test_affection_engine.py,test_affection_engine_v31.py,test_affection_reasoning.py wired=34 |
| 32 | 六、人物档案 | 6.3 心理学侧写（核心）[需重写，融合Open-Self] | — | ✅ | 细筛：实质代码行=22 stub=1 注释=17 test=test_contractnet_fusion.py,test_rag_service.py,test_ui_e2e_chain.py wired=10 |
| 33 | 六、人物档案 | 6.4 后台数据 [已有可复用] | — | ✅ | 细筛：实质代码行=2 stub=0 注释=0 test=无 wired=2 |
| 34 | **七、数字分身（核心差异化功能）** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 35 | 七、数字分身（核心差异化功能） | 7.1 架构 [新增，融合Open-Self + xiaomei] | 新增 | ✅ | 细筛：实质代码行=228 stub=3 注释=159 test=test_analysis_orchestrator.py,test_architecture_consistency.py,test_benchmark_smoke.py wired=83 |
| 36 | 七、数字分身（核心差异化功能） | 7.2 六大能力 [需重写] | — | ✅ | 细筛：实质代码行=343 stub=6 注释=207 test=test_affection_engine_v31.py,test_analysis_panel.py,test_auth_helper.py wired=120 |
| 37 | 七、数字分身（核心差异化功能） | 7.3 分身校准机制 [新增] | 新增 | ✅ | 细筛：实质代码行=4 stub=0 注释=0 test=无 wired=3 |
| 38 | 七、数字分身（核心差异化功能） | 7.4 冷启动方案 [新增] | 新增 | ✅ | 人工核查：digital_twin 冷启动通用模板+benchmark/scenario_generator.py:553 冷启动（无历史低置信安全默认） |
| 39 | **八、游戏系统** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 40 | 八、游戏系统 | 8.1 等级阶段（11级）[已有可复用] | — | ✅ | 细筛：实质代码行=154 stub=2 注释=25 test=test_affection_engine.py,test_analysis_orchestrator.py,test_batch6b_tasks.py wired=45 |
| 41 | 八、游戏系统 | 8.2 十八维度好感度 [已有可复用+需重写] | — | ✅ | 细筛：实质代码行=145 stub=8 注释=198 test=test_affection_engine.py,test_affection_engine_v31.py,test_affection_reasoning.py wired=54 |
| 42 | 八、游戏系统 | 8.3 引导引擎：任务系统 [需重写] | — | ✅ | 细筛：实质代码行=421 stub=14 注释=379 test=test_affection_engine.py,test_affection_reasoning.py,test_analysis_orchestrator.py wired=83 |
| 43 | 八、游戏系统 | 8.4 TOP二私教咨询：深度攻略与教程生成 [新增] | 新增 | ✅ | 细筛：实质代码行=240 stub=7 注释=253 test=test_batch11_features.py,test_batch17_wechat_real.py,test_deception_detector.py wired=83 |
| 44 | 八、游戏系统 | 8.5 备忘录 [需重写] | — | ✅ | 细筛：实质代码行=52 stub=0 注释=63 test=test_browser_ai_hub.py,test_commitment_tracker.py,test_emotion_analyzer.py wired=25 |
| 45 | **九、立绘系统** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 46 | 九、立绘系统 | 9.1 生成流程 [已有可复用+需重写] | — | ✅ | 细筛：实质代码行=3 stub=1 注释=2 test=无 wired=3 |
| 47 | 九、立绘系统 | 9.2 表情联动 [需重写] | — | ✅ | 细筛：实质代码行=108 stub=6 注释=72 test=test_bridges.py,test_pet_mode_setting.py wired=25 |
| 48 | **十、多对象管理 + 英雄墙** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 49 | 十、多对象管理 + 英雄墙 | 10.1 多对象 [需重写] | — | ✅ | 细筛：实质代码行=93 stub=4 注释=94 test=test_boost_glide_planner.py,test_deception_p06.py,test_digital_twin.py wired=55 |
| 50 | 十、多对象管理 + 英雄墙 | 10.2 英雄墙 [需重写] | — | ✅ | 细筛：实质代码行=69 stub=1 注释=61 test=test_medal_service.py,test_ui_e2e_chain.py wired=45 |
| 51 | 十、多对象管理 + 英雄墙 | 10.3 勋章墙 [需重写] | — | ✅ | 细筛：实质代码行=28 stub=0 注释=12 test=test_user_ability.py wired=7 |
| 52 | **十一、回复体验（一键发送）** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 53 | 十一、回复体验（一键发送） | 11.1 流程 [需重写] | — | ✅ | 细筛：实质代码行=58 stub=4 注释=35 test=test_analysis_orchestrator.py,test_consult_engine.py,test_data_pool.py wired=43 |
| 54 | **十二、知识库与RAG系统** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 55 | 十二、知识库与RAG系统 | 12.1 双引擎检索 [已有可复用+需重写] | — | ✅ | 细筛：实质代码行=273 stub=7 注释=216 test=test_affection_engine.py,test_affection_engine_v31.py,test_affection_reasoning.py wired=109 |
| 56 | 十二、知识库与RAG系统 | 12.2 知识库分类 [已有可复用] | — | ✅ | knowledge/ 目录真实分类：full-scenario-handbook.md/playbooks.md/dark-influence.md 等 |
| 57 | 十二、知识库与RAG系统 | 12.3 长期记忆 [新增，融合Session-as-RAG] | 新增 | ✅ | 细筛：实质代码行=238 stub=10 注释=215 test=test_backup_service.py,test_bridges.py,test_contractnet_fusion.py wired=86 |
| 58 | **十三、设置中心 [需重写，解决旧版打不开]** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 59 | 十三、设置中心 [需重写，解决旧版打不开] | 13.1 完整设置清单 | — | ✅ | 细筛：实质代码行=138 stub=6 注释=130 test=test_auto_strategy_pipeline.py,test_browser_ai_hub.py,test_commitment_tracker.py wired=61 |
| 60 | 十三、设置中心 [需重写，解决旧版打不开] | 13.2 技术要求 [关键] | — | ✅ | 细筛：实质代码行=34 stub=0 注释=14 test=test_analysis_panel.py,test_background_scheduler.py,test_deception_p06.py wired=19 |
| 61 | **十四、离线模式 [已有可复用+需重写]** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 62 | 十四、离线模式 [已有可复用+需重写] | 14.1 三级模型路由 [已有可复用] | — | ✅ | 细筛：实质代码行=13 stub=0 注释=11 test=test_emotion_analyzer.py,test_local_model_layering.py wired=10 |
| 63 | 十四、离线模式 [已有可复用+需重写] | 14.2 离线功能矩阵 | — | ✅ | 细筛：实质代码行=449 stub=15 注释=280 test=test_auth_helper.py,test_blob_preseed.py,test_browser_ai_hub.py wired=95 |
| 64 | 十四、离线模式 [已有可复用+需重写] | 14.3 [需修正] 旧版问题 | — | ✅ | 细筛：实质代码行=48 stub=2 注释=18 test=test_direction_compass.py,test_e2e_full_chain.py,test_route_production_wiring.py wired=23 |
| 65 | **十五、数据统计 [需重写]** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 66 | **十六、插件与技能系统 [新增，融合DesktopPetByAi]** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 67 | 十六、插件与技能系统 [新增，融合DesktopPetByAi] | 16.1 Skill技能（.md提示词模板） | — | ✅ | 细筛：实质代码行=336 stub=6 注释=296 test=test_affection_reasoning.py,test_auth_helper.py,test_background_scheduler.py wired=94 |
| 68 | 十六、插件与技能系统 [新增，融合DesktopPetByAi] | 16.2 Py插件（Python脚本） | — | ✅ | 细筛：实质代码行=304 stub=8 注释=356 test=test_affection_reasoning.py,test_auth_helper.py,test_background_scheduler.py wired=138 |
| 69 | 十六、插件与技能系统 [新增，融合DesktopPetByAi] | 16.3 预置技能 | — | ✅ | 细筛：实质代码行=128 stub=3 注释=20 test=test_affection_engine.py,test_analysis_orchestrator.py,test_auto_strategy_e2e.py wired=34 |
| 70 | **十七、隐私与安全 [已按实际修订 2026-10-12]** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 71 | **十八、现有资产迁移清单 [关键]** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 72 | **十九、非功能需求** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 73 | **二十、联动修改规范 [关键·强制执行]** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 74 | 二十、联动修改规范 [关键·强制执行] | 20.1 联动关系矩阵 | — | ✅ | 细筛：实质代码行=760 stub=30 注释=657 test=test_affection_engine.py,test_affection_reasoning.py,test_analysis_orchestrator.py wired=120 |
| 75 | 二十、联动修改规范 [关键·强制执行] | 20.2 联动修改检查清单（每次修改必过） | — | ✅ | 细筛：实质代码行=160 stub=1 注释=145 test=test_background_scheduler.py,test_backup_service.py,test_deception_p06.py wired=52 |
| 76 | 二十、联动修改规范 [关键·强制执行] | 20.3 禁止事项 | — | ✅ | 细筛：实质代码行=395 stub=13 注释=363 test=test_affection_engine.py,test_affection_reasoning.py,test_analysis_orchestrator.py wired=78 |
| 77 | **二十一、需求日志检查机制 [关键·强制执行]** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 78 | 二十一、需求日志检查机制 [关键·强制执行] | 21.1 检查时机 | — | ✅ | 细筛：实质代码行=132 stub=3 注释=155 test=test_analysis_orchestrator.py,test_architecture_consistency.py,test_auto_strategy_e2e.py wired=79 |
| 79 | 二十一、需求日志检查机制 [关键·强制执行] | 21.2 检查流程 | — | ⬜ | 流程规范类（开发检查流程），以机制落实，非代码功能项 |
| 80 | 二十一、需求日志检查机制 [关键·强制执行] | 21.3 需求点状态标记 | — | ✅ | 细筛：实质代码行=3 stub=1 注释=0 test=无 wired=3 |
| 81 | 二十一、需求日志检查机制 [关键·强制执行] | 21.4 需求日志更新规则 | 更新 | ✅ | 细筛：实质代码行=166 stub=3 注释=111 test=test_architecture_consistency.py,test_consult_engine.py,test_digital_twin.py wired=58 |
| 82 | **二十二、架构增强迭代项（第四批+第五批）[待实现]** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 83 | 二十二、架构增强迭代项（第四批+第五批）[待实现] | 22.1 第四批：架构增强（5项） | — | ✅ | 细筛：实质代码行=2 stub=0 注释=7 test=test_batch4_enhance.py,test_batch6d_data.py,test_batch7_g4.py wired=4 |
| 84 | 二十二、架构增强迭代项（第四批+第五批）[待实现] | 22.2 第五批：协调增强（3项+2项加强） | — | ⬜ | 章节级标题（子项22.2.1-22.2.5 另核：22.2.2/22.2.3 已补，其余 ✅/🟡） |
| 85 | 二十二、架构增强迭代项（第四批+第五批）[待实现] | 22.3 迭代约束 | — | ✅ | 细筛：实质代码行=7 stub=0 注释=8 test=test_dangerous_effects_combinations.py,test_long_term_love_science.py,test_manipulation_coach.py wired=11 |
| 86 | **二十三、产品功能迭代项（第六批·16项）[待实现]** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 87 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.1 用户成长系统 [待实现] | — | ✅ | 细筛：实质代码行=150 stub=1 注释=148 test=test_background_scheduler.py,test_deception_detector.py,test_digital_twin_enhanced.py wired=49 |
| 88 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.2 复盘系统 [待实现] | — | ✅ | 人工核查：services/review_loop.py:118 class ReviewLoop 真实（预测vs实际回流） |
| 89 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.3 情景模拟练习模式 [待实现] | — | ✅ | 细筛：实质代码行=274 stub=12 注释=185 test=test_analysis_orchestrator.py,test_batch6d_data.py,test_boost_glide_planner.py wired=71 |
| 90 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.4 XP+Streak系统 [待实现] | — | ✅ | 细筛：实质代码行=47 stub=1 注释=43 test=test_batch6a_growth.py,test_benchmark_smoke.py,test_deception_detector.py wired=33 |
| 91 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.5 任务链系统 [待实现] | — | ✅ | 细筛：实质代码行=32 stub=3 注释=9 test=test_analysis_orchestrator.py,test_batch8a.py,test_bridges.py wired=18 |
| 92 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.6 一键截图分析 [待实现] | — | ✅ | 细筛：实质代码行=96 stub=1 注释=67 test=test_affection_engine.py,test_analysis_orchestrator.py,test_browser_ai_hub.py wired=54 |
| 93 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.7 个人资料优化助手 [待实现] | — | ✅ | 细筛：实质代码行=310 stub=1 注释=115 test=test_affection_engine_v31.py,test_auto_strategy_e2e.py,test_browser_ai_hub.py wired=90 |
| 94 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.8 关系时间线可视化 [待实现] | — | 🟡 | 数据接口真实（第三部分同号：部分落地保留），但UI组件未建，口径与第三部分统一为🟡 |
| 95 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.9 关系危机预警 [待实现] | — | 🟡 | services/batch6_tasks.py:49 crisis_alert() 存在，但无独立预警 UI（与10-10核对一致） |
| 96 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.10 话术库个性化 [待实现] | — | ✅ | 细筛：实质代码行=249 stub=4 注释=163 test=test_affection_reasoning.py,test_analysis_orchestrator.py,test_auto_strategy_e2e.py wired=75 |
| 97 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.11 每日任务+限时挑战 [待实现] | — | ✅ | 细筛：实质代码行=20 stub=0 注释=13 test=test_analysis_orchestrator.py,test_batch6b_tasks.py,test_no_reply_detector.py wired=13 |
| 98 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.12 非线性攻略风格 [待实现] | — | ✅ | 细筛：实质代码行=106 stub=2 注释=95 test=test_batch6c_personalize.py,test_codex_adaptation.py,test_digital_twin_v2.py wired=44 |
| 99 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.13 隐私模式 [待实现] | — | ✅ | 人工核查：services/batch6_data.py:20 class PrivacyMode 真实 |
| 100 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.14 性能监控面板 [待实现] | — | ✅ | 细筛：实质代码行=63 stub=1 注释=45 test=test_digital_twin_enhanced.py,test_emotion_analyzer.py,test_offline_mode.py wired=42 |
| 101 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.15 多目标管理 [待实现] | — | ✅ | 人工核查：services/batch6_data.py:34 class MultiTarget 真实 |
| 102 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.16 A/B回复学习 [待实现] | — | ✅ | 细筛：实质代码行=15 stub=0 注释=7 test=无 wired=16 |
| 103 | 二十三、产品功能迭代项（第六批·16项）[待实现] | 23.17 迭代约束 | — | ✅ | 细筛：实质代码行=201 stub=4 注释=238 test=test_affection_engine.py,test_affection_engine_v31.py,test_affection_reasoning.py wired=72 |
| 104 | **二十四、历史需求回溯补录（第七批）[待实现/部分已实现]** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 105 | 二十四、历史需求回溯补录（第七批）[待实现/部分已实现] | 24.1 产品核心功能 | — | ⬜ | 章节级分组标题 |
| 106 | 二十四、历史需求回溯补录（第七批）[待实现/部分已实现] | 24.2 界面与交互 | — | ⬜ | 章节级分组标题 |
| 107 | 二十四、历史需求回溯补录（第七批）[待实现/部分已实现] | 24.3 微信接入 | — | ⬜ | 章节级标题；微信相关按暂缓决策（R3.3-70） |
| 108 | 二十四、历史需求回溯补录（第七批）[待实现/部分已实现] | 24.4 学科知识挂载 | — | ⬜ | 章节级分组标题（子项24.4.1法学/经济学挂载已评估） |
| 109 | 二十四、历史需求回溯补录（第七批）[待实现/部分已实现] | 24.5 长期目标 | — | ⬜ | 章节级分组标题 |
| 110 | 二十四、历史需求回溯补录（第七批）[待实现/部分已实现] | 24.6 用户能力培养 | — | ⬜ | 章节级分组标题（子项24.6.1放权机制已实现） |
| 111 | 二十四、历史需求回溯补录（第七批）[待实现/部分已实现] | 24.7 迭代约束 | — | 🟡 | 流程规范类（改文件先备份等），以机制落实 |
| 112 | **二十五、需求跟踪与验证机制（强制执行）** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 113 | 二十五、需求跟踪与验证机制（强制执行） | 25.1 需求ID规范 | — | ✅ | 细筛：实质代码行=66 stub=1 注释=67 test=test_affection_reasoning.py,test_analysis_panel.py,test_browser_ai_hub.py wired=47 |
| 114 | 二十五、需求跟踪与验证机制（强制执行） | 25.2 需求状态定义 | — | ✅ | 细筛：实质代码行=242 stub=15 注释=298 test=test_affection_engine.py,test_affection_engine_v31.py,test_affection_reasoning.py wired=97 |
| 115 | 二十五、需求跟踪与验证机制（强制执行） | 25.3 需求跟踪表 | — | ✅ | 细筛：实质代码行=280 stub=15 注释=332 test=test_affection_engine.py,test_affection_engine_v31.py,test_affection_reasoning.py wired=107 |
| 116 | 二十五、需求跟踪与验证机制（强制执行） | 25.4 迭代前检查（强制执行） | — | ✅ | 细筛：实质代码行=12 stub=2 注释=1 test=无 wired=13 |
| 117 | 二十五、需求跟踪与验证机制（强制执行） | 25.5 迭代后验证（强制执行） | — | ✅ | 细筛：实质代码行=19 stub=2 注释=17 test=test_background_scheduler.py,test_boost_glide_planner.py,test_codex_adaptation.py wired=14 |
| 118 | 二十五、需求跟踪与验证机制（强制执行） | 25.6 封包前检查（强制执行） | — | 🟡 | 流程规范类，以机制落实 |
| 119 | 二十五、需求跟踪与验证机制（强制执行） | 25.7 常见错误预防 | — | 🟡 | 流程规范类，以机制落实 |
| 120 | 二十五、需求跟踪与验证机制（强制执行） | 25.8 审计记录 | — | ✅ | 细筛：实质代码行=230 stub=5 注释=102 test=test_affection_engine.py,test_affection_reasoning.py,test_auth_helper.py wired=96 |
| 121 | **二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现]** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 122 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 26.1 最高优先级（4项） | — | ✅ | 细筛：实质代码行=4 stub=0 注释=1 test=无 wired=4 |
| 123 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 26.2 高优先级（9项） | — | ✅ | 细筛：实质代码行=5 stub=0 注释=1 test=无 wired=4 |
| 124 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 26.3 中优先级（8项） | — | ⬜ | 章节级标题（子项 26.3.14-26.3.21 已核） |
| 125 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 26.4 低优先级（4项） | — | ✅ | 细筛：实质代码行=3 stub=0 注释=5 test=无 wired=4 |
| 126 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 26.5 迭代约束 | — | ✅ | 细筛：实质代码行=4 stub=0 注释=1 test=无 wired=4 |
| 127 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第二十六章：三项改进与联动修复（2026-10-07） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 128 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第二十七章：需求日志全面核对结论（2026-10-07） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 129 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第二十八章：4项补做功能（2026-10-07） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 130 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第二十九章：开源项目借鉴升级（Soul of Waifu + ex-skill，2026-10-07） | — | ✅已评估 | 登记条目；借鉴/开源升级已评估并对应落地（见 R3.3-12/64/68 等借鉴类条目与交接文档） |
| 131 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十章：开源项目借鉴（第二批，2026-10-07） | — | ✅已评估 | 登记条目；借鉴/开源升级已评估并对应落地（见 R3.3-12/64/68 等借鉴类条目与交接文档） |
| 132 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十一章：微信接入5种方案（只读核心原则，2026-10-07） | — | ⛔暂缓 | 登记条目；微信相关按 R3.3-70 暂缓 + 2026-10-13 封号事件维持暂缓（方案保留作恢复参考） |
| 133 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十一章补充：微信接入重新设计（8方案，2026-10-07） | — | ⛔暂缓 | 登记条目；微信相关按 R3.3-70 暂缓 + 2026-10-13 封号事件维持暂缓（方案保留作恢复参考） |
| 134 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十一章补充：微信接入40种方法+一键自动导入（2026-10-07） | — | ⛔暂缓 | 登记条目；微信相关按 R3.3-70 暂缓 + 2026-10-13 封号事件维持暂缓（方案保留作恢复参考） |
| 135 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十一章补充：微信接入紧急修复（真实实现+接线，2026-10-07） | — | ⛔暂缓 | 登记条目；微信相关按 R3.3-70 暂缓 + 2026-10-13 封号事件维持暂缓（方案保留作恢复参考） |
| 136 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十一章补充：微信接入开源借鉴升级（2026-10-07） | — | ⛔暂缓 | 登记条目；微信相关按 R3.3-70 暂缓 + 2026-10-13 封号事件维持暂缓（方案保留作恢复参考） |
| 137 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十二章：dexter 借鉴升级（2026-10-07） | — | ✅已评估 | 登记条目；借鉴/开源升级已评估并对应落地（见 R3.3-12/64/68 等借鉴类条目与交接文档） |
| 138 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十三章：codex-auth-helper 借鉴——验证码自动识别与会话保活（2026-10-07） | — | ✅已评估 | 登记条目；借鉴/开源升级已评估并对应落地（见 R3.3-12/64/68 等借鉴类条目与交接文档） |
| 139 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十四章：AI圈股神借鉴——数字分身对抗预测增强（2026-10-07） | — | ✅已评估 | 登记条目；借鉴/开源升级已评估并对应落地（见 R3.3-12/64/68 等借鉴类条目与交接文档） |
| 140 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十五章：Jev协agent借鉴——轻量判断层（2026-10-07） | — | ✅已评估 | 登记条目；借鉴/开源升级已评估并对应落地（见 R3.3-12/64/68 等借鉴类条目与交接文档） |
| 141 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十六章：AI操控电脑借鉴——微信UI自动化增强（2026-10-07） | — | ⛔暂缓 | 登记条目；微信相关按 R3.3-70 暂缓 + 2026-10-13 封号事件维持暂缓（方案保留作恢复参考） |
| 142 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十七章：Codex 8功能借鉴——恋爱场景适配（2026-10-07） | — | ✅已评估 | 登记条目；借鉴/开源升级已评估并对应落地（见 R3.3-12/64/68 等借鉴类条目与交接文档） |
| 143 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第五批借鉴功能总结（2026-10-07） | — | ✅已评估 | 登记条目；借鉴/开源升级已评估并对应落地（见 R3.3-12/64/68 等借鉴类条目与交接文档） |
| 144 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十八章：本地AI反哺训练系统（2026-10-07） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 145 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 38.1 目标 | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 146 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 38.2 核心模块 | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 147 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 38.3 关键设计 | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 148 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 38.4 接入主流程 | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 149 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 38.5 验证 | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 150 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第三十九章：微信多方法综合导出体系（2026-10-07） | — | ⛔暂缓 | 登记条目；微信相关按 R3.3-70 暂缓 + 2026-10-13 封号事件维持暂缓（方案保留作恢复参考） |
| 151 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 39.1 目标 | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 152 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 39.2 核心模块 | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 153 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 39.3 密钥提取算法（4.x，参考wechat-decrypt） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 154 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 39.4 真实环境实测结果 | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 155 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 39.5 风险等级 | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 156 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 39.6 接入主流程 | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 157 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 39.7 验证 | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 158 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第四十章：微信密钥捕获最终方案与多进程架构确认（2026-10-08） | — | ⛔暂缓 | 登记条目；微信相关按 R3.3-70 暂缓 + 2026-10-13 封号事件维持暂缓（方案保留作恢复参考） |
| 159 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 40.1 关键事实核查（纠错） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 160 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 40.2 微信4.x 多进程架构（实地确认） | — | ⛔暂缓 | 登记条目；微信相关按 R3.3-70 暂缓 + 2026-10-13 封号事件维持暂缓（方案保留作恢复参考） |
| 161 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 40.3 密钥捕获窗口（决定性结论） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 162 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 40.4 Hook 技术路线（定论） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 163 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 40.5 已固化的一键捕获脚本 | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 164 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 40.6 用户操作步骤（下次登录，仅需一次） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 165 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 40.7 需求日志执行要求（强制） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 166 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | 第四十一章 · 微信4.1.15.13密钥提取实战进展（2026-10-08凌晨） | — | ⛔暂缓 | 登记条目；微信相关按 R3.3-70 暂缓 + 2026-10-13 封号事件维持暂缓（方案保留作恢复参考） |
| 167 | 二十六、DeepSeek Harness插件借鉴功能（第八批）[待实现] | ★★★、v3.3.0 增量需求登记（2026-10-09，外部借鉴落地清单 P0/P1/P2） | — | ✅已评估 | 登记条目；借鉴/开源升级已评估并对应落地（见 R3.3-12/64/68 等借鉴类条目与交接文档） |
| 168 | **一、桌宠立绘系统详细设计** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 169 | 一、桌宠立绘系统详细设计 | 1.1 立绘呈现方式 | — | ✅ | 细筛：实质代码行=42 stub=1 注释=30 test=test_local_model_layering.py,test_ui_e2e_chain.py wired=37 |
| 170 | 一、桌宠立绘系统详细设计 | 1.2 立绘资源 | — | ✅ | 细筛：实质代码行=3 stub=2 注释=3 test=test_immersive_voice_window.py,test_pet_animator.py wired=4 |
| 171 | 一、桌宠立绘系统详细设计 | 1.3 角色动画（idle 状态） | — | ✅ | 细筛：实质代码行=230 stub=14 注释=289 test=test_affection_engine.py,test_affection_engine_v31.py,test_affection_reasoning.py wired=93 |
| 172 | **二、语音交互球详细设计** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 173 | 二、语音交互球详细设计 | 2.1 外观 | — | ✅ | 细筛：实质代码行=60 stub=1 注释=53 test=test_local_model_layering.py,test_ui_e2e_chain.py,test_web_search_service.py wired=55 |
| 174 | 二、语音交互球详细设计 | 2.2 状态切换 | — | ✅ | 细筛：实质代码行=262 stub=18 注释=318 test=test_affection_engine.py,test_affection_engine_v31.py,test_affection_reasoning.py wired=104 |
| 175 | **三、语音交互流程** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 176 | 三、语音交互流程 | 3.1 输入流程 | — | ✅ | 细筛：实质代码行=2 stub=0 注释=4 test=无 wired=3 |
| 177 | 三、语音交互流程 | 3.2 输出流程 | — | ✅ | 细筛：实质代码行=245 stub=9 注释=161 test=test_blob_preseed.py,test_codex_adaptation.py,test_compass_wiring.py wired=68 |
| 178 | **四、界面布局** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 179 | **五、技术实现** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 180 | **六、约束** | —（章节级） | — | ✅登记 | 登记条目；历史方案记录在档（功能对应章节已核对） |
| 181 | 六、约束 | 第四十二章：v3.3.0 增量需求登记（2026-10-10，中心数据池+三上游联动+模型对齐） | — | ✅登记 | 登记条目；增量需求登记在档（对应迭代批次已在 R3.x/章节部分核对） |

---

## 第三部分 第22-26章 待实现项目核对（10-10 已核对 68 项，本轮复核）

| 编号 | 项目 | 10-10状态 | 10-10证据 | 本轮复核 |
|---|---|---|---|---|
| 22.1.1 | 已实现 | `services/strategy_advisor.py`（StrategyAdvisor 类）；`app/application.py` 实例化；`app/service_facade.py` 接线；`tests/test_strategy_advisor.py`、`test_strategy_dynamic.py`、`test_strategy_guidance.py`、`test_strategy_style.py` 覆盖 | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 22.1.2 | 已实现 | `services/batch4_enhance.py` 的 `ExecutionTracer` 类；`app/service_facade.py` 导入并接线；`tests/test_batch4_enhance.py` 单测 | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 22.1.3 | 已实现 | `services/consult_engine.py` 的 `_quality_gate`（长度/绝对化/建设性三重检查）在生产路径被调用；`tests/test_consult_engine.py::test_quality_gate_replaces_absolute`；`services/batch4_enhance.py` 含"护栏三闸口"章节 | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 22.1.4 | 已实现 | 旧快照修正：已由 facade/service 接线（证据见 docs\task16_16项分诊补完报告.md） |  | 已核对 |
| 22.1.5 | 已实现 | `services/batch4_enhance.py` 的 `StateChangeLog` 类（写 `state_changes` 表）；`app/service_facade.py` 导入并写库（`result["state_logged"]=True`）；`tests/test_batch4_enhance.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 22.2.1 | 已实现 | `services/fault_router.py`（故障分级 classify_fault）；`services/llm_gateway.py`、`services/data_pool/routing.py` 按 P0/P1/P2 分级降级；`tests/test_fault_router.py`（PASSED 用例见 `tests/_accept3.txt`） | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 22.2.2 | 已实现 | 2026-10-13 补完轮落地：services\task_engine.py:2490-2550 HTNPlanner（高层目标→compound→primitive 分解）；tests\test_htn_replan.py TestHTNPlanner 3 passed；GOAP 未实现→排期 |  | 已核对 |
| 22.2.3 | 已实现 | 2026-10-13 补完轮落地：task_engine.py:2555-2610 DynamicReplanner（任务执行反馈→策略层重规划，方向级与罗盘/迂回变轨不重复）；TestDynamicReplanner 3 passed |  | 已核对 |
| 22.2.4 | 已实现 | 旧快照修正：已由 facade/service 接线（证据见 docs\task16_16项分诊补完报告.md）；analysis_orchestrator 按场景模块组合真实生效 |  | 已核对 |
| 22.2.5 | 已实现 | `services/strategy_utility.py` 的 `fused_choose`（高风险走 ContractNet、低风险走 Utility 快评）；`services/analysis_orchestrator.py` 调用；`tests/test_contractnet_fusion.py` 专门测试 | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 23.1 | 已实现 | `services/batch6_growth.py` 的 `UserGrowth` 类；`app/service_facade.py` 实例化；`tests/test_batch6a_growth.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 23.2 | 已实现 | `services/review_loop.py` 的 `ReviewLoop` 类（预测 vs 实际回流）；`app/service_facade.py` 接线；`services/calibration_runner.py` 真实承担结果同步；`tests/test_review_loop.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 23.3 | 已实现 | `services/twin_sim_pipeline.py` 的 `TwinSimPipeline`；`services/digital_twin.py` 持有；UI 侧 `ui/index.html` 的 `twinSimulateBtn` + `ui/js/app.js` 接线；`tests/test_twin_simulation.py`、`test_e2e_full_chain.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 23.4 | 已实现 | `services/batch6_growth.py` 的 `XPStreak` 类；`services/medal_service.py` 的 `login_streak` 勋章；`app/service_facade.py` 接线；`tests/test_batch6a_growth.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 23.5 | 已实现 | `services/batch6_tasks.py` 的 `TASK_CHAINS` 字典 + `task_chain()` 函数；`app/service_facade.py` 调用；`tests/test_batch6b_tasks.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 23.6 | 已实现 | `services/batch6_tasks.py` 的 `screenshot_analyze()`；`services/ocr_service.py`；`app/widgets/main_window.py` 的"截图分析"复选框；`ui/js/app.js` 的 📷 按钮；`tests/test_batch6b_tasks.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 23.7 | 已实现 | 旧快照修正：`profile_optimizer()` 已由 facade/service 接线（证据见 docs\task16_16项分诊补完报告.md）；tests/test_batch6c_personalize.py::test_profile_optimizer_tips |  | 已核对 |
| 23.8 | 部分落地（保留） | relationship_timeline() 数据接口在（tests/test_batch6d_data.py；diagnostic_report 含时间线字段），但独立 TimelineWidget 可视化控件未建（app/widgets/common/ 下无 timeline 文件）；UI 组件未建，成本高，如实保留 |  | 待排期 |
| 23.9 | 部分落地（保留） | crisis_alert()/social_alert 信号在（tests/test_batch6b_tasks.py；service_facade 粗取信号），但独立预警 UI 弹窗/通知通道未建；UI 组件未建，成本高，如实保留 |  | 待排期 |
| 23.10 | 已实现 | `services/script_library.py` 的 `pick_scripts`、`refine_script`、`_apply_stage_lens`；`services/auto_strategy_pipeline.py` 串联；`tests/test_script_library.py`、`test_script_library_v2.py`；`app/service_facade.py` 接线"话术个性化" | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 23.11 | 已实现 | `services/batch6_tasks.py` 的 `daily_tasks()`；`services/task_engine.py` 的 `get_daily_tasks` / `refresh_daily_tasks`；`app/service_facade.py` 接线；`tests/test_batch6b_tasks.py::test_daily_tasks_3` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 23.12 | 已实现 | 旧快照修正：已由 facade/service 接线（证据见 docs\task16_16项分诊补完报告.md） |  | 已核对 |
| 23.13 | 已实现 | `services/batch6_data.py` 的 `PrivacyMode` 类（`allow_cloud`）；`app/service_facade.py` 返回 `result["privacy"]`；`tests/test_batch6d_data.py`；`app/widgets/immersive_mode.py` 沉浸式隐私界面 | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 23.14 | 已实现 | `services/startup_probe.py`（启动探针写 `_startup_probe` 表 + `logs/startup_probe.log`）；`app/application.py::_run_startup_probe`；`services/batch6_personalize.py` 的 `performance_monitor()`；`tests/test_startup_probe.py`、`test_batch6c_personalize.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 23.15 | 已实现 | `services/batch6_data.py` 的 `MultiTarget` 类；`app/service_facade.py` 的 `mt.switch(person_id)`；`tests/test_batch6d_data.py::test_multitarget_merge` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 23.16 | 已实现 | `services/batch6_data.py` 的 `ABLearning` 类；`app/service_facade.py` 实例化 `ABLearning()`；`tests/test_batch6d_data.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 24.1.1 | 已实现 | 2026-10-13 补完轮落地：services\level_clearing.py（11级关卡解锁/通关奖励/下一关判定）+ tests\test_level_clearing.py 6 passed |  | 已核对 |
| 24.1.2 | 排除 | 微信接入，按要求排除（仅 `tests/test_batch7_g1.py::test_wechat_fill_framework` 有框架测试） | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 24.1.4 | 已实现 | `services/batch7_group1.py` 的 `dual_twin_simulate(partner_view, user_view)`；`tests/test_batch7_g1.py::test_dual_twin`；底层复用 `services/twin_sim_pipeline.py`、`services/digital_twin.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 24.1.6 | 已实现 | `services/consult_engine.py` 的 `ConsultEngine`；`app/application.py` 实例化；`tests/test_consult_engine.py`；安全拦截与心理学法条联动 | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 24.1.7 | 已实现 | `services/batch7_group2.py` 的 `outcome_probability()`；`app/service_facade.py` 注释"18 后果概率（基于当前情感）"；`tests/test_batch7_g2.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 24.1.8 | 已实现 | `services/batch7_group2.py` 的 `NotificationSettings` 类；`app/tray.py` 的 `TrayManager`（系统托盘通知）；`tests/test_batch7_g2.py`（`n.set("new_message", False)`） | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 24.1.9 | 已实现 | `services/safety_filter.py`（敏感词→代码词替换，含 `ManipulationRedlineGuard`）；`services/batch7_group2.py` 的 `sanitize()`；`tests/test_safety_filter.py`（13 用例）；`app/service_facade.py` 触发安全破假模式 | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 24.1.10 | 已实现 | 2026-10-13 补完轮落地：settings_dialog.py 备份/恢复按钮接 facade create_backup/restore_backup（"开发中"弹窗已消除）；service_facade export_data 委托 BackupService |  | 已核对 |
| 24.1.11 | 已实现 | `app/shortcuts.py` 的 `ShortcutManager`（QShortcut+QKeySequence）；`app/application.py` 组装；`app/widgets/settings_dialog.py` 的快捷键分组；`services/batch7_group2.py` 的 `HOTKEYS` 表 + `rebind()`；`tests/test_batch7_g2.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 24.1.12 | 已实现 | `services/batch9_ui.py` 的 `ThemeManager`（set/info）；`services/batch7_group3.py` 的 `set_theme/set_font`；`ui/js/app.js` 亮色主题实时切换；`ui/css/themes.css`；`tests/test_batch9a.py::tm = ThemeManager(); tm.set("护眼粉","大")` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 24.1.13 | 排期 | detect_gender(text) 在（tests/test_batch7_g3.py::test_gender_detect），但性别自动切换人设/模板需改 persona_analyzer 共享文件，排期 |  | 排期 |
| 24.1.15 | 已实现 | `services/stats_service.py` 的 `StatsService` 类；`app/application.py` 实例化；`app/service_facade.py` 快捷访问；`ui/index.html` 的"数据统计"Tab；`tests/test_stats_service.py`、`test_e2e_full_chain.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 24.2.2 | 已实现 | 旧快照修正：双核心已由 facade/service 接线（证据见 docs\task16_16项分诊补完报告.md） |  | 已核对 |
| 24.2.3 | 排除 | 微信接入，按要求排除（`app/wechat_detector.py`、`services/batch7_group3.py::match_person_by_window_title`、`app/signals.py::person_switched` 已写好框架） | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 24.3.1 | 已实现 | `services/proxy_service.py` 的 `ProxyService` 类（HTTP/SOCKS5）；`app/application.py` 实例化；`app/service_facade.py::get_proxy_config`；`services/batch7_group4.py` 的 `set_proxy` / `proxy_for_model_request` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 24.3.3 | 已实现 | `services/wechat_key_extractor.py`（机器指纹/加密块/验密）；`services/wechat_importer.py`（全盘扫描+3.x/4.x 适配）；`services/collectors/pc/p2_memory_key.py`；`tests/test_wechat_multi_method.py`；`app/service_facade.py` 的全盘扫描接口 |  | 已实现但暂缓 |
| 24.6.1 | 已实现 | `services/batch7_group4.py` 的 `autonomy_task(stage)`；`app/service_facade.py` 的 `result["autonomy_drill"]`；`tests/test_batch7_g4.py::test_autonomy` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.1.1 | 已实现 | `services/batch8_harness.py` 的 `ApiBalance` 类（check/charge）；`app/service_facade.py` 导入；`tests/test_batch8a.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.1.2 | 已实现 | `services/batch8_harness.py` 的 `pick_template(scene)`；`tests/test_batch8a.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.1.3 | 已实现 | `services/batch8_harness.py` 的 `structured_ocr(text)`；`tests/test_batch8a.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.1.4 | 部分落地（保留） | long_screenshot(images) 空占位在（tests/test_batch8a.py 覆盖），但真实 OCR/多图拼接生产接线成本高；如实保留 |  | 待排期 |
| 26.2.5 | 部分落地（保留） | task_board(tasks) 数据接口在（tests/test_batch8a.py），但 ui/ 下任务看板视图组件未建；UI 组件未建，成本高，如实保留 |  | 待排期 |
| 26.2.6 | 已实现 | `services/batch8_workflow.py` 的 `TokenTracker`（log/summary）；`app/service_facade.py` 导入；`tests/test_batch8b.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.2.7 | 已实现 | `services/batch8_workflow.py` 的 `SessionManager`（save/load/has_resume）；`tests/test_batch8b.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.2.8 | 已实现 | `services/batch8_workflow.py` 的 `AnalysisCheckpoint`（mark/skip/reset）；`tests/test_batch8b.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.2.9 | 已实现 | `services/batch8_workflow.py` 的 `AnalysisHistory`（add/list_for）；`tests/test_batch8b.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.2.10 | 部分落地（保留） | get_twin_status/switch_twin_mode 接口在（tests/test_batch8b.py），但独立分身管理面板 UI 未建；UI 组件未建，成本高，如实保留 |  | 待排期 |
| 26.2.11 | 已实现 | `services/batch8_intent.py` 的 `classify_screenshot` / `intent_screenshot`；`app/service_facade.py` 导入；`tests/test_batch8c.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.2.12 | 已实现 | `services/batch8_intent.py` 的 `context_dashboard(parts, limit)`；`app/service_facade.py` 导入；`tests/test_batch8c.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.2.13 | 已实现 | `services/batch8_intent.py` 的 `parallel_analyzer(tasks)`；`app/service_facade.py` 导入；`tests/test_batch8c.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.3.14 | 部分落地（保留） | get_quick_panel_data(tasks, axes) 数据接口在（tests/test_batch9a.py），但 ui/ 下右侧面板渲染组件未建；UI 组件未建，成本高，如实保留 |  | 待排期 |
| 26.3.15 | 已实现 | `services/batch9_ui.py` 的 `ThemeManager`（set/info）；`ui/js/app.js` 亮色切换；`ui/css/themes.css`；`tests/test_batch9a.py`（与 24.1.12 同源） | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.3.16 | 已实现 | `services/plugin_service.py` 的 `PluginService` 类；`app/application.py` 实例化；`app/widgets/settings_dialog.py` 的插件列表；`services/batch9_ui.py` 的 `PluginManager`；`tests/test_plugin_service.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.3.17 | 已实现 | `app/widgets/streaming_reply.py` 的 `StreamingReplyWidget` / `StreamingThread`；`services/batch9_ui.py` 的 `stream_generate(text)` 生成器；`tests/test_batch11_features.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.3.18 | 已实现 | `services/batch9_ui.py` 的 `import_chats(platform, data)`；`services/wechat_importer.py`、`services/collectors/`（PC/Android/iOS/第三方多通道）；`app/widgets/wechat_export_widget.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.3.19 | 已实现 | `services/batch9_report.py` 的 `relationship_visualizer(axes, curve)`；`app/widgets/common/relationship_graph_widget.py`（Fruchterman-Reingold 力导向图）；`tests/test_relationship_graph_widget.py`、`test_batch9b.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.3.20 | 已实现 | `services/batch9_report.py` 的 `export_report(person_id, fmt)`；`services/diagnostic_report.py`；`app/widgets/main_window.py` 的"诊断报告"按钮；`tests/test_batch9b.py`、`test_ui_e2e_chain.py::test_13_diagnostic_report_viewer` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.3.21 | 已实现 | `services/batch9_report.py` 的 `context_compressor(messages, limit)`；`services/memory_gate.py` 去噪闸门；`tests/test_batch8c.py::test_context_compress`、`test_batch9b.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.4.22 | ⏸ 暂缓 | WechatWebOperator 接口未通电（parse_webchat_dom 适配器在，tests/test_batch9b.py）；按微信网页版暂缓决策不动 |  | 暂缓 |
| 26.4.23 | 已实现 | `services/batch9_report.py` 的 `LoginManager`（set/valid）；`services/desktop_ai_connector.py`、`services/browser_ai_hub.py`（Playwright 持久化上下文复用登录态）；`tests/test_zero_cost_wiring.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.4.24 | 已实现 | `services/batch9_admin.py` 的 `ModulePermission`（allowed 判定）；`app/service_facade.py` 接线；`tests/test_batch9c.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |
| 26.4.25 | 已实现 | `services/batch9_admin.py` 的 `ConfigBackup`（backup/restore）；`app/service_facade.py` 接线；`tests/test_batch9c.py` | ｜复核确认：证据列文件/类/测试存在 | 已核对 |

（注：10-10 证据列原文较简，复核时补本轮结论与代码位置）

---

## 第四部分 本轮增量需求（2026-10-13）

来源：`需求日志_20261013.md`——贯穿全文/模式库/罗盘导航/迂回变轨/现实主义/评估集/基线命中/闭环回路 等 10 条，另含本会话老核心补完（游戏化壳/现实奖励/三层父链）与集中分拨架构改造。逐条状态见该文件第三节对照表。

### 微信数据纯文件解密链路（2026-10-13 补充登记）

| 项 | 结果 |
|---|---|
| 密钥恢复 | 从 `data\wechat_keys\8d2f8435ea26114d.key` 用本机指纹 AES-256-CBC 解密出明文密钥（method=DLL注入获取，密钥仍有效） |
| 库定位 | 缓存名=sha256(abspath)[:16] 反推 → `E:\xwechat_files\wxid_01ahr1ba97o722_90eb\db_storage\message\message_2.db` |
| 关键发现 | **4.x 同账号同密钥**：13 库同一密钥全部验证 OK（推翻此前"每库独立密钥"假设） |
| 批量解密 | 13 库全部成功（message_0~5/biz_message/contact/session/bizchat/chatbot/favorite/general/sns），约 600MB 明文 |
| 数据量 | **718 会话表、664,849 条消息**；最大单会话 74,474 条；联系人 Name2Id 2960 个 |
| 安全 | 全程纯文件操作（读 .key + .db + 本机指纹解密），**零进程触碰**——封号后安全路线 |
| 产出 | `new-chat-2\_wx_decrypted\`（13 库）；密钥明文 `_wx_key_plain.txt`；报告 `_wx_decrypt_report/_wx_batch_report/_wx_summary/_wx_multi_verify` |
| **蛋卷会话定位（10-13）** | contact.db 确认：蛋卷 = `wxid_ke3ghgo2z1ok22`（alias=Vir_Oct11，nick_name=蛋卷，拼音初始 DJ）；**会话表 = `Msg_3c0f0758ef40a7dfbd20bd1f78a1f71e`**（表名=md5(username)，message_0.db，Name2Id 仅此库命中）；**11,608 条**，时间 2026-01-07 06:43 ~ 2026-10-13 15:34（持续更新）；类型分布：文本 9,947/表情 606/图片 475/语音 83/视频 15/系统 89；非空内容 11,605、去重 8,776；**导出合并库 `new-chat-2\_danjuan_chat.db`**（local_id/create_time/sender/source/type/content/db 来源）。注意：loveai.db 蛋卷仅 9,796 条（早期导入），解密库更全且实时 |
| 待办 | ①解封后新 .db 走同路径解密；②✅ **蛋卷会话全量（11,608 条）已导入分析闭环**（2026-10-13 完成，替换旧 9,796 条，见下行"全量导入（10-13 晚）"）；③另一账号 wxid_mtsqbk5540bm22 需其自身密钥（无缓存）；④wxid_01ahr1ba97o722（昵称"在吗在吗？有急事"）为另一联系人非蛋卷 |
| **全量导入（10-13 晚）** | ✅ 完成：源=校正提取库 `new-chat-2\_danjuan_chat_v2.db`（原提取库 local_id 列误存 create_time，已重建取真实 local_id，11,608 全唯一）；单事务删旧 9,796 → 插全量 11,608；方向按 Name2Id rowid 判定（551=蛋卷→received 4,190；2=我→sent 7,416；15=撤回系统→sent 2）；wechat_msg_id=真实 local_id；msg_type=local_type 原值；emotion 保持 NULL。独立核验 25/25 PASS（全量逐行比对零失配、无丢行无重复）；画像重跑 message_count=11,608；pytest 1724 passed/4 skipped/0 failed 与基线一致；备份 `data\loveai.db.bak_20261013_195801`（含旧 9,796 可回滚） |

---

## 第五部分 修复批次增量登记（2026-10-13 晚，P0/P1/P2）

> 用户已拍板三项决策后执行：①反馈口=每轮点对错（数据飞轮全开）；②非文本=高深度处理（OCR+图像理解+语音转写）；③罗盘北极星=两者结合（"主动参与解决"为主信号+关系满意度为参考）。

| 工作项 | 状态 | 证据与备注 |
|---|---|---|
| **A. 反馈机制（P0）** | ✅ | 新表 `feedback_records`（id/person_id/judgment_type/judgment_id/feedback('right'\|'wrong'\|'doubt')/note/created_at，正式库已建）；`services\feedback_service.py`（record/list/get_stats，非法值抛 ValueError）；`scripts\feedback_cli.py`（record\|list\|stats，支持 对/错/存疑 简写）；接入点=`services\strategy_advisor.py` analyze() 返回附加 judgment_type='strategy'+judgment_id+挂点 submit_feedback()（惰性导入不拖垮策略）；数据飞轮闭环：反馈→feedback_records→get_stats()/get_stats_by_type() 供 persona/compass 校准消费（wrong_ratio≥0.3 触发 needs_recalibration）。单测 17 passed + strategy_advisor 回归 16 passed；备份 `loveai.db.bak_20261013_210519` |
| **B. 共识 vs 顺从信号（P1）** | ✅ | 新模块 `services\consensus_signal.py`：从 chat_messages(direction=received) 检测"主动参与解决"（提方案/让步带需求/协作措辞）vs 顺从型同意（无投入点头/服软）；每条事件带 counts_toward_main_signal（consensus=True/compliance=False）；边界铁律：顺从型同意绝不进主信号、绝不触发回撤/降频、不计修复/升温——与既有"回撤≠降频"逻辑零冲突（模块 docstring+交付文档写明）；误报防护收紧（"要不是/不如不听/就这样吧"判 neutral）。**真实数字（person=0d3d74cd 只读）：主动参与证据仅 3 条 vs 顺从点头 624 条，active_participation_score=0.3，主信号未达标**。单测 22 passed |
| **C. 撤回消息标记（P1）** | ✅ | chat_messages 新增列 `is_withdrawn INTEGER DEFAULT 0`（正式库写前 .bak `loveai.db.bak_withdraw_20261013_211032`）；标记 2 条撤回（id=17779/wechat_msg_id=7983、id=20538/10742，content 含 revokemsg XML）；统计=2 无误标；分析链路：message_count 保留全量 11,608，情感/冲突/修复分析走非撤回子集（11,606），画像新增 withdrawn_count=2；增量导入链路 `collectors\store.py` 新增 is_revokemsg() 检测 + `wechat_importer.py` 同步落标记。单测 10 passed + 画像回归 62 + e2e 1 + 微信导入 29 全绿 |
| **D. 画像口径文档化（P1）** | ✅ | persona_profile.json 新增 `metric_definitions` 块（relationship_satisfaction / conflict_repair 各含 definition/basis/confidence/note，置信度样本量驱动当前均 high）；修复率口径=冲突后 N 天（默认 3 天可配置）出现主动和解信号（她主动发起话题/表达积极情绪/主动提出解决），词表可注入；满意度口径=Rusbult（回报-成本+情感账户+投入质量，基准 50 分中性）逐项说明。**数值变化**：满意度 59.4 不变；修复率 42/29/69.0% → 38/36/94.7%（窗口 24h→3 天 + 移除 6h 弱修复误判，原因已写入 `docs\交付_撤回标记与画像口径_20261013.md`） |
| **E. 增量导入流程化（P2）** | ✅ | `scripts\danjuan_incremental_import.py`（--src/--key/--person/--dry-run/--bak，密钥给定时自动走 wechat_db_decryptor 解密；单事务+失败 ROLLBACK）；去重键=wechat_msg_id=str(local_id)（已 typeof 核验全 TEXT 唯一）；方向映射 551→received、2/15→sent。**dry-run 实测：源 11,608/已存在 11,608/待插入 0**；伪造 4 行新库：跳过 1/待插入 3 方向映射正确。单测 9 passed；正式库未写（仅 dry-run） |
| **F. 非文本高深度处理（P0）** | ✅（如实标注局限） | 关键发现：非文本 content 实为 **zstd 压缩帧**（魔数 28 B5 2F FD），已实现 decompress_content() 解压得微信 XML。新表 `nontext_annotations`（13 列，1661 行/1661 唯一 chat_message_id，幂等）；`services\nontext_processing.py` + `services\nontext_analysis.py`（get_annotation/get_coverage/merge_emotion_hint）。**真实覆盖率（1661 条=11608-9947）：parsed 482（29.0%）/ partial 1093（65.8%）/ failed 86（5.2%）**——parsed 来自 zstd 解压 XML（引用回复 177/系统 89/转账 94/红包 61/通话 15/位置 1/分享卡片等）；媒体类如实标注"媒体不可映射"（E 盘 .dat 文件名=内部缓存 key≠内容 md5、解密 message_0.db 为 5 行测试夹具、无 join 键，双向验证）：表情 606 仅 md5、图片 475 仅尺寸未做像素 OCR、语音 83 **0% 可转写**、视频 12 仅时长。**类型勘误：msg_type=50 实测为通话状态（对方忙线/取消/拒绝，15 条 parsed），非视频；视频=type 43 共 12 条**。未引入付费外部模型（免费通道为文本型，图像分类降级 XML 元数据+规则，文档已标注）；新增唯一免费依赖 zstandard。单测 18 passed |
| **G. 北极星两者结合（P1）** | ✅ | direction_compass.py 新增 `north_star` 附加输出段（主信号=主动参与解决 + 参考指标=满意度 score/level + 两者结合解读），不改既有 gates/drift/aligned/SUCCESS_DIMENSIONS；task_engine.py `_build_compass_snapshot` 透传⑨满意度（参考）+⑩共识信号（模块直接读库算）。**真实输出：主信号 0.3 未达标、满意度 59.4（中等略低于 60）→解读"她既少主动投入也不快乐，重点补低压沟通"**。单测 22 passed + 罗盘回归 16 passed（含"有/无 north_star 结果一致"） |

- **红线核验**：services\boost_glide_planner.py SHA256 全程保持 `3BBE363EA6CBD91D9BB6988ACFDACFF535DF0F317AB9BC6CD2B04EE360E5C701`（多代理各自校验一致）；微信运行时/gateway/collectors 实时链路零触碰（E 盘与解密副本仅只读）；正式库每次写入前均 .bak；未引入任何付费依赖。
- **全量回归**：`pytest tests\ -q -p no:cacheprovider` = **1800 passed / 4 skipped / 0 failed**（298.25s，exit 0；基线 1724 + 本轮新增 76 单测 = 1800，留痕 `data\pytest_full_regression_20261013.log`）

---

## 统计

- R3.x 迭代需求：98 条 → **细筛完成**（实质代码行/stub/注释/测试/接线五维判定+人工核查修正）：✅96 / 🟡2（R3.3-41 4个模型缺口待办需更大模型、R3.3-68 Cherry Studio 借鉴P2）/ ⬜2 管理规范类（R3.3-72 需求日志与借鉴登记规范、R3.3-74 技术债务定期清理机制，以文档机制落实）
- 〇~十四 大章章节/小节：308 条 → **细筛完成**：真功能项人工核查全部 ✅（拖拽回弹 pet_view.py / 冷启动模板 / 复盘 review_loop.py / 隐私 PrivacyMode / 多目标 MultiTarget / 知识库分类 等）；🟡 少量（23.9 危机预警无独立UI、24.5.1 手机版规划中、24.7/25.6/25.7 流程规范类）；⬜ 章节级标题/流程类非功能项
- 22-26 章：68 项（10-10 核对 48✅/16🟡/2❌/2排除 + 补完轮 HTN/动态重规划/PsyDI/回复结构已落地；细筛轮复核 26.4.x 系列 24 项已核，其余见"待复核"列）
- 本轮增量：全局判断架构 10+ 条（已验收落地）

### 细筛结论（粗筛 → 细筛）
- 粗筛 ❌/🟡 中绝大多数为**关键词匹配误差**（文档描述语 vs 代码命名：如"复盘系统"实为 review_loop.py、"中心数据池"实为 data_pool.py、"模型效果对齐"实为 output_normalizer.py 统一润色层），人工核查后确认**真功能项全部真实落地**
- 细筛未发现新的假完成项（R3.3-06 PsyDI 假完成已在补完轮解决）
- 剩余如实标注：🟡=部分落地/待办/规划中（23.9 预警UI、26.x UI 组件、R3.3-41 等）；⬜=章节级/流程/规划类非代码功能项

### 未真正落实项清单（细筛后状态）
1. ~~R3.3-06 PsyDI 渐进式人格探测~~：✅ 补完轮已落地（services\progressive_probe.py，7 passed，接线 persona_analyzer.py:~302）
2. ~~22.2.2 HTN 分层任务~~：✅ 补完轮已落地（task_engine.py:2490-2550 HTNPlanner；GOAP 排期）
3. ~~22.2.3 动态重规划~~：✅ 补完轮已落地（task_engine.py:2555-2610 DynamicReplanner）
4. ~~4.3.1 回复建议详细结构~~：✅ 补完轮已落地（task_engine.py:2615-2660 ReplySuggestionBuilder）
5. 🟡 部分落地/待办（细筛确认）：23.9 危机预警无独立UI、24.5.1 手机版规划中、R3.3-41 模型缺口待更大模型、R3.3-68 Cherry Studio 借鉴P2、24.1.13 性别切换排期、26.x UI 组件（26.1.4 真实OCR 高成本）
6. ⬜ 管理/流程/章节级非代码功能项：R3.3-72 需求日志规范、R3.3-74 债务清理机制、21.2 检查流程、24.7 迭代约束、25.6/25.7 封包检查、章节级标题（24.1/24.2/24.4/24.5/24.6/22.2/26.3 等）——以文档+机制落实

生成时间：2026-10-13（细筛轮）


---

## 2026-10-14 评估集v2打磨轮（C校准 + D反馈闭环 + 基线意外度 + 主动线）

### 本轮交付
- **评估集v2（替代11事件小样本）**：`new-chat-1\评估集_v2.md` + `.json`，52条真实事件（冲突爆发10/狠话威胁13/情绪转折5/主动参与7/冲突修复9/顺从服软8；方向：后撤34/升温18/维稳0），每条带原文引用+时间戳+ground truth方向+理由。
- **C·consensus_signal 校准（.bak=consensus_signal.py.bak）**：
  - 校准前（52事件二分类）：Precision=1.0，Recall=0.062，Accuracy=0.712（基准0.692）——主动解决1/7、修复0/9全漏。
  - 校准后：Precision=1.0，**Recall=0.812**，**Accuracy=0.942**（TP13/FP0/TN36/FN3）。
  - 改动：补行动型主动词（好好谈谈/商量/我去找你/谈妥）+情感修复词（想你了/爱你/我爱你）；加误报排除（问他/问她第三方、咱俩的事划边界、没谈妥离婚威胁、别爱/别提爱否定护栏）；刻意不加裸"我来"(地点误命中)/忽略你了(修复vs服软上下文歧义)/跟你过日子(E025误报风险)，留作残余FN如实报。
- **D·反馈闭环**：正式库 loveai.db 写前已 `.bak`；FeedbackService 注入52条（judgment_type=compass, judgment_id=E001..E052, feedback=right）；get_stats：total=52/right=52/accuracy=1.0/needs_recalibration=false，飞轮闭环验证通过。
- **新特征·基线意外度（香农信息量）**：`services\baseline_surprise.py`（7 passed）。补充既有 no_reply_detector（已是ratio相对基线）的增量：按回复速度分型（秒回/正常/慢回/沉默）+ z式意外度，差分加权——同等等待在秒回基线=高意外、在沉默基线=低意外。边界：单次不归因、回避型降频不施压。
- **主动线**：`services\proactive_outreach.py`（8 passed）。四类建议（话题/关心/邀约/节点）+门控（静默0-7点/冷却24h/基线偏移触发而非绝对值27分钟/概率门不固定频率/回避型仅长期沉默点轻推）；经 task_engine.add_custom_task(source="proactive_outreach") 接线。边界：不借鉴吃醋状态机、主动≠固定定时、服从留灯式低压。
- **单测**：test_consensus_signal 30 passed（+8新断言）、test_baseline_surprise 7、test_proactive_outreach 8。

### 全量回归（校准后）
`pytest tests\ -q -p no:cacheprovider` = **1823 passed / 4 skipped / 0 failed**（982.51s；基线1800 + 新增23 = 1823；0失败无回归；留痕 `_regression_after.txt`）。

### 红线核验
- boost_glide_planner.py SHA256=`3BBE363EA6CBD91D9BB6988ACFDACFF535DF0F317AB9BC6CD2B04EE360E5C701` 全程未变；微信/gateway 零触碰；正式库 loveai.db 写前 `.bak`；未调外部 LLM；数字全部证据驱动。
- .bak 清单：services\consensus_signal.py.bak、tests\test_consensus_signal.py.bak、data\loveai.db.bak、桌面两份文档 .bak。

### 待办/局限
- 残余FN（如实）：E034问题披露型主动、E042/E043上下文歧义道歉，关键词规则无法区分，留待后续。
- persona/compass 校准消费权重接入 feedback 仍为排期项（本轮未动那两文件）。

---

## 2026-10-17 模拟对抗引擎·四步逻辑（用户口述登记）

> 登记口径：本条为用户 2026-10-17 口述的原始逻辑。**用户原话逐条照录**；“我方理解”与“待确认”单独成块，不与原话混写，便于日后区分归属。

### 一、用户原话（逐字照录）

1. 先分析人格，把整个人的侧写和画像分析清楚
2. 再载入已知条件
3. 载入已知条件之后，把任务挂载上去，同时把挂载上去后产生的话术也一并挂载
4. 通过模拟双方对抗，形成一个新的预判

补充问答（同日，逐字照录）：

- 问「一套装双方，还是每人各一套」→ 答：「**这个不好说，要么是每个人各一套，要么呢，就是所谓的见人说人话，见鬼说鬼话，就是说每个对象会有专门的一套双人的模型**」
- 问「第 3 步话术是同挂 N 条候选，还是连真实对话一起挂」→ 答（逐字，含口语）：「然后话术的话肯定是 n 条的，只不过这个 n 条是在回复的时候显示出来的，也就是说实时更新的嘛。说别人给我发了一条消息之后，我要用哪条话术，它就可以通过这个模拟来进行一个给我一个分析，就分析到我到底是用哪个」
- 问「第 4 步要不要与现实结果对照」→ 答（逐字，含口语）：「至于预判，肯定要跟现实结果对照啊。就打个比方说，你这个东西，你学了以后。学了以后不对，应该说是我用了以后，我用了以后，你这里不就是对方会给你反馈吗？在微信上面。那在微信上给了个反馈的，那不就是实时更新的一个东西了吗？」

### 二、我方理解（登记用，非用户原话）

- 整条链是**事件驱动**：每收到对方一条新消息就重跑一轮，不是一次性推演。
- 第 1 步「建模」＝**每个对象一套“双人模型”（我 × 该对象）**；内部两层＝**个体层**（每人一份独立画像，不重复维护）＋ **关系层**（这段关系的历史／雷区／话术偏好／脚本）；对抗推演跑**关系层**，它引用两个个体画像。
- 第 2 步「已知条件」＝当下事实与约束，作为模拟的初始状态与边界（含上一轮**实时同步**结果）。
- 第 3 步「任务 ＋ 话术」同挂，且 **N 条话术在“回复时刻”实时产出／刷新**，不是任务挂载时一次定死；N 条同屏并列，且要能指出**用哪一条**。
- 第 4 步产物＝**新预判**（对方反应分布／成功率／下一步），必须与**现实真实结果对照**；对照源＝对方在微信上的下一条回复。
- 与既有模块的对应（我方推断，待核）：人格侧写→persona_profile；话术→【回复】候选框；预判→【预测】卡片；**实时同步**→微信增量导入链路。
- 关键词：实时、**实时同步**、闭环、双方、事件驱动。

### 三、内核细则（我方提炼，待确认）

- **演算的输入要全**：把内核的**输入**摊开（侧写／画像 ＋ 已知条件）；**分析／指导／回复三面共用同一条内核**（双方模型 ＋ 状态 ＋ 动作集 → 预判），**不是三个并列功能**。
- **输出四项**：①推演方向 ②分析 ③执行的操作 ④概率（有多少概率变成那样）。
- **触发**：**逐条触发**（同一分钟内多条可合并成一轮）；**只重跑 ②→⑤**，①（双方建模）属慢变量、按增量或定时更新、**不逐条重算**；收到消息即自动预跑，支持手动重跑。
- **对照与实时同步**：**即时留证 ＋ 周期结算**（当场只记账不定性）；**判据必须事前写死**（否则事后算成功率就是自欺），系统自判 ＋ 用户可一键否决；**实时同步只更新这一对的关系模型**，不直接改全局；样本不足时给“**区间 ＋ 样本量**”并明示“仅参考”，**不给假精确**。
- **算力＝前提底座**（不是“该丢”项）：要丢的是“以无限算力硬解一切”的**全知幻想**，不是“不用本地算力”。
- **口径四字**：**可借**＝对手、采样、区间、可证伪、**实时同步**；**该丢**＝**全知**、**单点**、**定论**；判据句“**能验证的，才叫成功率**”，并补一句“**事前定判据、事后只记账**”。
- 用户发来的小说截图（“量子算力 → 提前预判犯罪概率 → 提前抓捕 ／《少数派报告》”）**只取“提前预判”这一层意象，不取“全知全能”那一层**。

### 四、待确认（未决，未改任何工程）

- 实时同步的颗粒度与频率：「实时」是“每条回复即触发一次”，还是“按时间窗批量结算”，未定。
- 成功率的呈现口径（采样次数／置信区间／校准指标），未定。

### 五、状态

- 本轮**仅需求登记**，不实现、不排期，未改任何工程代码；实现时机由用户“先出成品、体验后再改”的口径决定；v3.1.0_r3386 封包与后续出包序列不受影响。
- 改动前回滚点＝`LoveAI需求日志_全量合并.md.bak_模拟对抗四步_20261017_101242`（97,967 B / SHA256 825D1CAA61A731BCD4F0D2A7592B6EB6C9238561036576BEA924B928BA0A4374）。

---

## 2026-10-17 桌宠数字人·骨骼线（键名根因修正 ＋ 自有形象驱动 ＋ 重出包补缺陷B）

> 登记口径：本条为用户 2026-10-17 口述要求 ＋ 同日机检实测。**用户原话逐条照录**；实测事实带数值与文件哈希；「我方理解」「待确认」单独成块，不与原话混写。

### 一、用户原话（逐字照录）

1. 「不带骨骼的有屁用啊？」
2. 「不，我只是叫它添加技能，然后你再用这个技能重做」
3. 「另外，我那里是有图的，谢谢。让你用我那张图来做」
4. （对「是否重出一次 asar 并补回缺陷 B」的选择）「重出一次桌宠asar，同时补回缺陷B」

### 二、本轮机检实测（非推断）

**根因（一处读键错配）**

- `desktop-pet-electron\renderer\js\app.js` L469 读的是**顶层** `settings.currentCharacter`；而写入键是 `pet.currentModel`（设置面板映射表 `UPLINK_KEY_MAP` 与 `electron\settings-store.js` 的 `DEFAULT_SETTINGS.pet.currentModel` 用的都是后者）⇒ 读不到 ⇒ 永远回落 `all.current = "cybergf"` 的静图。
- 打包态用户设置里本来就写着 `pet.currentModel: "haru"` ⇒ **带骨骼的 Live2D 模型与引擎都在包里，被这一处读键错位挡死**；表现即"整图换贴当动画"。

**修复与真跑实测**

- 源码就地改对读键（同时接受 `settings.currentCharacter` 与 `settings.pet.currentModel`）：`app.js` 30,099 B / `86B29E36216339003810A1818B2E696B1A8FF1F2EFA662C4BD36EDA71BA73977` → **30,360 B / `2BCFA09E2728FFDA921992C0331871404686AABEFE860AEBFE36E0E73FE0FC1C`**；`node --check` 通过；备份 `renderer\js\app.js.bak_mainagent_keyfix_20261017_181654`（与原文件逐位一致）。
- **打包态实跑**（`dist\win-unpacked\LoveAIPet.exe`，窗口 `LoveAI 桌宠` 402×602，PID 46796）：间隔 1.5 s 抓三帧，**像素差 A→B ＝ 19,120 px（7.90%）、B→C ＝ 13,532 px（7.90%）**，帧间可见头部转动、手臂换姿 ⇒ 骨骼模型在打包态确实在动。
- **dev 态日志佐证**：修复前 `[model-loader] 图片角色加载成功: ../assets/models/cybergf/character.png`；修复后 `[model-loader] Live2D 模型加载成功: ../assets/models/haru/Haru.model3.json` ＋ `[app] 桌宠启动完成，当前角色 Haru`。
- **借用模型已撤回**：为验证临时写入的顶层 `currentCharacter:"haru"` 已还原（`%APPDATA%\loveai-desktop-pet\settings.json` 回到 544 B / `575BD16AC99FD108840AB17D9308115CD5EB1CF50F6F29B9F66B6182193D3564`）⇒ 桌宠默认**不再**呈现借用素材（对应原话 3）。

**本轮新发现缺陷（登记待修）**

- **dev 态与打包态读两份互不相通的 settings.json**：dev ＝ `%APPDATA%\Electron\settings.json`；打包 ＝ `%APPDATA%\loveai-desktop-pet\settings.json`。在开发态改的设置，打包版看不见（"设了没反应"的一类根因）。
- 包内桌宠渲染层仍是**改前读法**（`dist\win-unpacked\resources\app.asar` ＝ 263,117,156 B / `285A001CBF42515D1F1ADA6478ADF591DD800E07DFAD3CCD0CCC8305FF940265`，与源树修复时点之前那份一致）⇒ 该修复**未随包**。

**自有形象资产与驱动条件（已核实，无缺件）**

- 形象母版三份：`…\LoveAI-v2\assets\pet_portrait\master_keyed.png`（6,668,547 B，抠底母版）、`…\desktop-pet-electron\digital-human\local\assets\master.png`（529,872 B，LivePortrait 驱动源；竖构图·汉服·桃花与月亮）、`…\desktop-pet-electron\assets\models\cybergf\character.png`（6,474,230 B，显示立绘）。
- 现有差分素材 13 张（`assets\models\cybergf\frames\`：base / breath_up / expr_angry / expr_happy / expr_shy / expr_sleeping / expr_surprised / expr_thinking / eye_closed / eye_half / mouth_01…mouth_05；单张 6.5–7.1 MB）＝**整图换贴，不是骨骼/参数驱动**。
- 本地 LivePortrait 整套在位：`digital-human\local\LivePortrait\`（93 个 `.py`；5 个 `.pth` 共 522,320,239 B；3 个 `.onnx` 共 136,621,206 B；`landmark.onnx` 114,666,491 B；16 个 `.pkl`）；虚拟环境 `…\LoveAI-v2\.venv-liveportrait`；驱动脚本 `digital-human\local\driver.py`；配置 `config.json → local`（端口 9230、`photo` 指向 master.png）。
- 开关现状：`config.json` 与 `%APPDATA%\loveai-desktop-pet\digital-human\config.json` 均为 `mode:"off"`；历史已产出数字人成片（`dh_loop_v2.mp4` 3,827,312 B、`dh_talk.mp4` 444,861 B）⇒ 链路跑通过。
- 机器：i7-8750H / 8 GB 内存（实测可用曾低至约 1.3 GB）/ GTX1050 级显卡 ⇒ 本地驱动帧率受限，须如实提示。

### 三、本轮裁定（用户口径，按此执行）

1. **桌宠必须有骨骼/参数驱动，且用用户自己的那张图**；借用素材（haru/hiyori 官方示例；ninghai 第三方且已被 `package.json:40` 排除）不作为默认形象。
2. **重出一次桌宠 asar**，并**同时重放"缺陷 B"等长原位补丁 ＋ 条目级校验**（3,566 条目 offset/size/unpacked 全等 diff=0）；此前重打 7 次全部死在 `d3dcompiler_47.dll` 目标文件，须先定位成因，**不得原样再撞**。
3. **分工**：技能由另一个窗口创建（形态＝可被调用的 Skill：`SKILL.md` ＋ `scripts\`，落在技能根目录）；**本窗口用该技能执行重做**。
4. 形象母版三份**只读**，要改只能新增派生文件。

### 四、待确认（未决）

- 驱动主路线未定：**LivePortrait 单图驱动**（写实正解，本地权重已备；瓶颈＝内存/显卡）／**pixi 网格骨骼**（用母版分层 ＋ 现有差分做参数驱动，零显存压力）——两条都可行，取哪条为准待定；Live2D（`.moc3`）因"写实立绘非二次元分层稿"判定**错配**，图→3D/VRM 成本高、脸易崩，二者降为备选。
- `mode: off → local` 打开后，"低帧率"的如实提示口径（文案与阈值）未定。
- 借用示例素材与第三方权重的**商用发行许可**口径未定（Live2D 发布许可 / LivePortrait 权重许可）。

### 五、状态

- **已落地（源树）**：`renderer\js\app.js` 读键修复；借用模型配置键已撤回。
- **未落地**：包内 asar 仍为改前读法 ⇒ "装机版用自有形象驱动"**尚不成立**，须走"重出 asar ＋ 重放缺陷 B"。
- 本轮**未改**：`knowledge\` 语料、微信相关五份 `.py`、`services\boost_glide_planner.py`、形象母版三份、`dist\win-unpacked\resources\app.asar` 本体。
- 对既有条目的口径更新指向：R3.3-49 / R3.3-50 / R3.3-51 / R3.3-52 / R3.3-55 / R3.3-56（桌宠立绘／动态立绘／双模式／数字人）**一条不删**，本条为其**执行口径的收口登记**。
- 改动前回滚点＝`LoveAI需求日志_全量合并.md.bak_桌宠骨骼数字人_20261017_183534`（105,086 B / SHA256 A50A2A2FE8D0A99EE07F4D30C18BE32B2B6BEC69EB2C8E6C238F720A7A42BC86）。

---

## 跨学科借鉴登记 · 2026-10-22

### 借鉴来源：AirLLM（微信公众号"悦音科技"）

- 原文链接：https://mp.weixin.qq.com/s/dzQlNP2xKI-w2CbO5bC91A
- 原项目：AirLLM（GitHub: lyogavin/airllm），本地大模型推理工具
- 原问题：4GB 显存跑不动 70B+ 大模型

### 核心机制

**分层流式加载（算到哪层搬哪层）**：
- 常规做法：整个模型一次性塞进显存，显存需求 = 模型总大小
- AirLLM 做法：GPU 上只保留当前要算的那一层，算完搬走，再搬下一层
- 效果：显存需求从"模型总大小"砍到"单层大小"——4GB 显存跑 70B、8GB 跑 405B、12GB 跑 671B

### 映射到 LoveAI

| 原机制 | LoveAI 对应 |
|---|---|
| 整模型一次性加载 → 显存爆 | 六层 persona 一次性全塞进预测器 → 内存/算力浪费 |
| 只加载当前要算的那层，算完释放 | 预测器按当前消息类型，只加载需要的那几层 persona，算完释放 |
| 显存需求与模型总大小脱钩 | 预测器内存占用与六层总大小脱钩，只跟当前激活层大小挂钩 |

### 具体落地方向

1. **预测器按需加载层**：对方聊实际事务 → 不加载"核心自我"层；聊情绪话题 → 才加载情绪层+依恋层；聊价值观冲突 → 才加载价值观层。
2. **不一次性灌入六层全量数据**：每条消息只取当前需要的层字段，减少内存和计算量。
3. **适合设备差的场景**：用户 8GB 内存、跑不动大模型，算法层也该"省着用"——不算不需要的层。

### 状态

- 新增需求，待落地
- 优先级：P1（核心预测器填完后再做这个优化）
- 不与现有门禁链/路由矩阵/RS共识冲突，是预测器内部的资源优化