# LoveAI-v2 接手状态 · Phase4 加固派工（2026-10-09 16:50）

## ★ 收口进度（2026-10-09 17:40 更新）
- 五个工作包子代理**全部完成并逐批复跑核验**（Phase3 接线 + P-01~11）。
- **P-10 统计兜底已接进 llm_gateway**：chat() 末级生成层全失败先落 stat（last_layer=stat），
  stat 空再落 rules；__init__ 加 _stat_fallback_supplier（未注入→人群先验安全兜底）。
- **主控默认值收口已完成**：
  - free_model_router web_search 默认 False 且**移出 _fallback_fchat 聊天兜底链**；
    新增 augment_context_with_web_search()，并已在 consult_engine 知识不足处显式调用；
  - local_scan 默认 True（代码 _collect_local_scan_models 与设置 UI 初始勾选一致，开箱即生效）；
  - desktop/browser 默认 False；auto_send 全局审计：config/twin_sim 均 False，
    wechat_operator.auto_send_message 仅自身+测试引用、无活动流程调用（休眠，不删）。
  - 设置页同步：仅本地扫描默认勾选，网页搜索默认不勾且文案改“知识不足补全上下文”。
- **playwright/chromium 物理归集已完成**：collect_payload 新增 collect_browser_ai
  → dist_bundle\payload\browser_ai（794 文件 / 810.0MB：playwright 包 104.5MB +
  chromium-1243 431.9 + headless_shell-1243 270.1 + ffmpeg-1011 3.4）；
  LoveAI.iss 增 browserai 组件与释放规则；runtime_paths 增 bootstrap_playwright_env
  （设 PLAYWRIGHT_BROWSERS_PATH + sys.path 兜底）；LoveAI.spec collect_all playwright/greenlet。
  已验证：PLAYWRIGHT_BROWSERS_PATH 指向随包目录后 chromium 离线启动 OK（v153.0.8010.12）。
- **全量逐文件隔离回归**：123 文件，**passed=1613（1600 + UI e2e 13）、failed=0、error=0**。
  data\iso_summary.log 记 1600/0/0（test_ui_e2e_chain 120s 超时未计入）；该文件单独跑
  13 passed/149.03s（慢非卡死），已加入 harness 慢文件清单（220s）。
- **需求级验收进行中**：子代理 s_000E05jxaUW（三档人设自由生成/3次多样性/双路线）。
- **下一步 Phase5**：停 ollama serve PID 29240 → collect_payload 全量 → PyInstaller
  （--noconfirm，确认新模块真打入可调用）→ ISCC；第四闸终端实证。

## 已完成并验证（勿重复）
- **1.5B 模型已纳入**：models_local\qwen2.5-1.5b-instruct-q4_k_m.gguf =
  1,117,320,736B（1065.6MB），GGUF 魔数通过（data\download_1p5b.log，SUCCESS）。
- **真机基准（真实输出）**：
  - 1.5B：稳态 41.08 tok/s、稳态 TTFT 0.14s、冷加载 41.35s；能产出多段结构化
    NPD/依恋分析并起草"引导修复"+"制服/反制"双路线话术（不重复）。
  - 0.5B：稳态 101.64 tok/s、TTFT 0.07s、冷加载 4.68s；长分析撞 512 上限重复、
    话术题照抄指令（失败）。
  - 报告：data\benchmark_local_models.md、data\benchmark_local_quality.md。
- **标签口径已统一**为 loveai-qwen15b（主力）/ loveai-qwen05b（兜底），
  两 tag 已在本机 Ollama 物理注册（<E盘>\JZDSTool\llm_models）。
- **分工固化代码**：
  - services\local_model_manager.py（REGISTRY 幂等注册，绝不删模型；ChatML；num_ctx 8192）。
  - services\llm_gateway.py：默认主力 loveai-qwen15b、兜底 loveai-qwen05b；
    _ollama_candidate_models / _ollama_alive(任一存在) / _ollama_chat_once /
    _call_l2_ollama(先主力后兜底)。
  - app\application.py：后台 daemon 线程 ensure_registered（便携/本机两态）。
- **封包清单源码已同步**（未重跑归集）：tools\installer\LoveAI.iss L48 组件描述
  约2.7GB；scripts\collect_payload.py breakdown 加 1.5B。models_local 现约 2742.2MB。
- **当前已绿测试（隔离）**：
  - tests\test_local_model_layering.py = 19 passed
  - tests\test_local_model_manager.py = 10 passed（新建）
  - tests\test_llm_gateway_v2.py = 16 passed
  - 合计 45 测绿。

## Phase4 派工（5 个子代理并行，文件归属互斥）
- s_000cP1luQup：Phase3 零成本中枢接线（独占 free_model_router.py、settings_dialog.py；
  playwright+chromium 真机；desktop 缺登录态标 503）→ docs\零成本中枢接线报告-v3.2.md。
  已知其首跑 test_free_model_router.py：20 passed / 3 failed（custom_loopback_endpoint、
  no_key_no_ollama、rank_by_success_rate 0.5 不大于 0.5），正在修。
- s_000cPNGC8lp：P-01~04（confidence/calibration/review_loop/stats + 新建
  confidence_cap.py、silent_gold_standard.py）→ docs\加固P01-P04落地报告.md。
- s_000cPNGPFcb：P-05~06（deception_detector/model_version_manager + 新建 label_channel.py）
  → docs\加固P05-P06落地报告.md。
- s_000cPNr1Q5g：P-07~09（safety_filter/manipulation_coach/task_engine/digital_twin +
  新建 third_party_pii.py、refusal_downgrade.py）→ docs\加固P07-P09落地报告.md。
- s_000cPNrEUTj：P-10~11（新建 local_statistical_fallback.py、shadow_eval.py）
  → docs\加固P10-P11落地报告.md。**P-10 降级链接 llm_gateway 由 organizer 收口**。

## 待办（organizer）
- 各包回来后：验收文件:行号 + 隔离测试实数；由我把 local_statistical_fallback 接进
  llm_gateway.chat() 降级链末级（rules 之前/之后按 P-10：统计反应分布，标注无生成模型）。
- 全量**逐文件隔离**回归（tools\run_tests_isolated.ps1）须全绿。
- 需求级验收：多档人设（普通 / NPD+回避+创伤 / BPD）自由生成，含已读不回·数天不回，
  验证真实生成非套模板；制服与引导修复两路线都跑通。
- Phase5：停 ollama serve（防硬链接 WinError32）→ collect_payload → PyInstaller
  (LoveAI.spec --noconfirm) → ISCC(LoveAI.iss)，长构建 run_in_background 轮询；
  真机安装验证进程存活/组件物理落位/免装 Python·Ollama·pip、离线断网优雅降级；
  输出"已落地 vs 仍待增强"。旧安装器 1532.7MB（16:15，不含1.5B）已过期。

## 并发备注
- 兄弟 organizer o_000cnzh1R5O 及其子代理 s_000cPTJsdew/s_000cPTJJ4G3 不归我管；
  截至 16:50 s_000cPTJsdew 已静默约 12 分钟（其 llm_gateway 改动已被我在其基础上收口）。
