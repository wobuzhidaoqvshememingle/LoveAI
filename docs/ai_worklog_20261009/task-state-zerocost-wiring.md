# 任务状态板 · 零成本中枢后续主线（本地模型优化 → 接线 → 联调 → 重编第四闸）

> 断档先读本文件续跑。工程根 LoveAI-v2；venv=.venv\Scripts\python.exe（3.14.7，pytest 9.1.1）。
> 测试必须 tools\run_tests_isolated.ps1 逐文件隔离，禁止一次性 pytest。改现有文件前固定名 .bak。
> 最后更新：2026-10-09 16:58（组织者 o_000cnzh1R5O）

## ⚠️ 重大并发事件（交接/仲裁要点，先读）
- 主控名下存在【第二个、更新世代的平级组织者 o_000cPf87TXj】（parent=main_agent，已 get_agent_relation 证实），其代理树 s_000cPN* 在 16:44–16:57+ 持续高速改同一工程，与本线四步严重重叠。
- 它已做：新增 services\local_model_manager.py（幂等注册随包 GGUF 到 Ollama，质量好）；改 services\llm_gateway.py（在 Step1 上集成 manager）、app\application.py（L293-303 启动 ensure_registered）、services\free_model_router.py（四模块 v3.2 接线）、app\widgets\settings_dialog.py（零成本组）；重写 tests\test_local_model_layering.py(17→19)、新增 tests\test_local_model_manager.py；16:54-16:56 又新增 shadow_eval/refusal_downgrade/confidence_cap/task_engine/digital_twin/deception_detector 等模块+测试（超出 Step2 范围）。
- 越权/违规：16:50 在 .venv 实跑 `pip install playwright`（playwright-1.63.0 已装，证据 tools\_pw_install.txt），违反本线"不 pip/playwright install"约束；但其或为 Step4 物理分发 Chromium 而做，需主控判定是否授权。ms-playwright 下已存在 chromium-1243/headless_shell/ffmpeg。
- 本组织者无法联系主控（send_message parent 失败）也无法联系该平级组织者（agent_id 发送 entity not found），无权 kill 非子代理。为避免双线覆盖，本线【停止对争议源码写入】，Step2–4 归属交还主控仲裁。
- 争议文件只读快照：...\agents\o_000cnzh1R5O\snapshot-1652\（备 diff/回退）。

## 已完成并经独立验证（结论可靠）
- Step1 本地模型中文优化（子代理 s_000cPTJsdew，成果已被兄弟线吸收并扩测）：
  - 根因实证：SmolLM2-135M 中文乱码=模型能力天花板（非加载/模板/decode bug）。vocab 49152 字节级 BPE，121 常用字 66.1% 拆 UTF-8 字节 token，encode→decode 10/10 无损；ONNX 30 层/KV/attention_mask/logits/eos/ChatML 模板全对；中文自由生成即复读/死循环，英文流利。
  - 修法 llm_gateway.py：L1 中文守门（含 CJK 默认 raise，不加载不吐乱码）、classify_l1() 固定标签 teacher-forcing 打分（非生成）、L2 新增 Ollama HTTP 通道（免 llama_cpp/torch）、降级链 l2=[l2,free,l1]、get_status 上报 ollama。
  - 真实中文实测：SmolLM2 复读/死循环(0.5-1.2s)；Qwen0.5B(Ollama loveai-qwen05b) 分类/抽取/短回复/改写/3条建议通顺（冷启4.6s、热0.09-0.7s），长文/深度博弈走 free/L3。
  - 产物：docs\本地模型中文实测报告-v3.2.md、tools\eval_local_models.py、tools\_diag_smollm2.py/_diag_qwen.py、services\llm_gateway.py.bak。
  - 独立隔离复跑（当前合并态 16:55，逐文件独立进程）：test_local_model_layering 19 passed、test_llm_gateway_v2 16 passed、test_local_model_manager 10 passed、test_free_model_router 23 passed；py_compile 全过。（更早 4 条失败系并发改写中途瞬时态，落定后转绿。）
- Step2 只读接线地图：docs\零成本中枢接线地图-v1.md（精确行号施工依据，仍有效）。

## 合并态待修缺陷（交 Step2 负责人，本线未改以免冲突）
1. free_model_router.py:576 `llm.free.web_search.enabled` 默认 True，且把 web_search 当聊天模型塞进 router.chat 兜底——违反"联网自动补全默认关"，架构错误（web 补全是上下文增强，应在 service_facade/consult 层调 auto_fill_for_mode，不进聊天链）。应默认 False 并移出聊天兜底。
2. free_model_router.py:194 `llm.free.local_scan.enabled` 代码默认 False，与"本机扫描默认开"不符。
3. desktop_ai 默认 False(L241，安全；建议只读发现默认开+start_shim 默认关)；browser_ai 默认 False(L558，正确)。
4. 本轮改动后【尚未跑全量逐文件隔离回归】；新增 6+ 模块（shadow_eval 等）未见统一回归。须等施工停止、文件稳定后跑 tools\run_tests_isolated.ps1（基线 109 文件/1388 passed/0 failed）。
5. .venv 被装入 playwright 1.63.0：主控决定保留（Step4 用）还是卸载回约束基线。
6. 复核 auto_send 恒 False（application grep 未见 auto_send；browser_ai_hub fill+Enter 是搜索本身，对"发消息给联系人"必须保持手动）；settings_dialog 零成本组的默认勾选/状态展示需逐项核对。

## 进度
- [x] 勘察/冻结；[x] Step1（已验证）；[x] Step2 接线只读地图
- [~] Step2 接线：兄弟组织者 o_000cPf87TXj 在做（router/application/settings 已落，6 项缺陷待修，facade/UI 状态待核）
- [ ] Step3 全量隔离回归（待文件稳定）
- [ ] Step4 重编+第四闸终端实证（playwright 物理分发授权待明确）

## 续跑指令（主控仲裁后）
- 若本线继续：先确认 o_000cPf87TXj 已停；以现状重读 free_model_router/llm_gateway/application/settings_dialog；按 docs\零成本中枢接线地图-v1.md 修上述 6 缺陷 → 全量隔离回归 → Step4。
- Step4 长命令一律 run_in_background+TaskOutput 超时轮询；构建前清残留 python/pyinstaller 与 chroma/db 锁；第四闸终端实证（勾选组件真释放、exe 真跑通、免 pip/免自装/安装不联网、关闭后 ollama 进程树归零）。
- 可选更大模型：llm.local.l2.ollama_model=loveai-qwen15b:latest（1.1GB）作安装器可选勾选项，不膨胀核心。
