# 任务状态 · 18 部分详尽人物诊断报告落地

## 任务目标
为 LoveAI-v2 鉴别引擎落地"18 部分详尽人物诊断报告"：核心组装器 + 真实三份报告 + 前台集成 + 测试 + 全量隔离回归。

## 进度板
- [x] 1. 核心组装器 services/diagnostic_report.py（generate_report / render_markdown / get_or_generate_report / Wilson / 18 部分）
- [x] 2. 测试 tests/test_diagnostic_report.py（11 用例全过）
- [x] 3. 三份真实报告落盘（data/persons/{t_healthy,t_npd,t_bpd}/diagnostic_report.{json,md}）
- [x] 4. 前台集成（app/bridge.py 两方法 + character_file_renderer.py 诊断区块 + ui/index.html 折叠导航）
- [ ] 5. 全量隔离回归 tools/run_tests_isolated.ps1（目标 failed=0 error=0，文件数=92）

## 已完成/产物路径
- 新增 services/diagnostic_report.py
- 新增 tests/test_diagnostic_report.py
- 改动 app/bridge.py（+get_diagnostic_report / +get_diagnostic_report_markdown）
- 改动 services/character_file_renderer.py（+render_diagnostic_block，render_character_file 增可选参数 diagnostic_report）
- 改动 ui/index.html（档案页"查看完整诊断报告"卡片 + 脚本）
- 三份报告：data/persons/t_healthy|t_npd|t_bpd/diagnostic_report.json + .md

## 完成标准（验收口径）
- 18 部分齐全（id 1-18、key、title）；每条关键结论锚定 evidence_index 的 eid；
- 正反证据计数+n；Wilson CI∈[0,1] 且 low≤p≤high；portrait_plain 300-600 字；
- 病理仅"倾向/偏高"+"不替代专业诊断"；风险分级整数 1-5；actionable 非空且无撤离导向；
- 全量隔离回归 failed=0 error=0。

## 待办/问题
- 【二轮复核修复完成】
  · persona_analyzer._analyze_conflict_style：删 my_text 计分分支，只按 other_text 刻画对方风格。
    p_slowwarm→待观察（不再误判破坏性攻击）；t_npd→冷战逃避；t_bpd→破坏性攻击；t_healthy→待观察。
  · diagnostic_report：NPD basis 去"堵门"；timeline 按优先级唯一消息计数（BPD timeline=负性=21 对齐）；
    白描收尾按证据分支+补字引用本案例 next_move，不再三份逐字共享。
  · 针对性测试：test_diagnostic_report(17)+persona 系列(72)=89 passed。
- 全量隔离回归仍由主控统一触发（本侧不自行跑）。

## 关键设计决策
- 主库 data/loveai.db 无 t_* 消息（仅 persona_profile.json）；报告证据用 CHAT_* 常量解析后喂入，落盘自包含。
- bridge 缓存优先：磁盘已有 diagnostic_report.json 直接读回，否则现场生成落盘。
