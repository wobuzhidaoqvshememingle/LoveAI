# UI E2E 自动化测试 — 任务状态（封包前返工后）

- 当前状态：**13 passed in 63.17s**（2026-10-09 13:32）。
- 完成标准：pytest tests/test_ui_e2e_chain.py -q -p no:cacheprovider --tb=short 全绿；截图非空。

## 本轮改动（授权文件，均已 .bak 备份）
1. app/service_facade.py
   - get_chat_messages（L906-924）：`?` 位置参数 → 命名参数 `:pid/:lim`；`db.execute`+裸迭代（tuple 报错）→ `db.fetch_all`（字典列表）。
   - 新增 get_diagnostic_report(person_id)（L929 起）：调 services.diagnostic_report.get_or_generate_report。
   - import_chat_from_text（L2865-2867）：补 `import time`（原 NameError 根因在此文件，非 wechat_service.py）。
2. app/widgets/main_window.py
   - _on_load_analysis（L1209-1214）：data 当 list → d.get("messages",[])；方向 'out' → ('sent','out')。
   - _build_profile_page（L453 起）：右侧雷达下加 `_diag_btn`「🔬 完整诊断报告（18部分）」。
   - 新增 `_on_show_diagnostic_report`（L1183 起）：滚动 QDialog，顶部核心结论+人物底色白描，下方 18 部分逐段渲染，未校准显示「未校准/待观察」。
3. tests/test_ui_e2e_chain.py：test_02 增 get_chat_messages 契约断言；test_04 改真实走 get_chat_messages；新增 test_13 诊断查看器。
4. tools/ui_e2e_helper.py：seed 方向映射 in→received / out→sent。

## 截图（data/ui_shots/，均非空）
01 主窗口 / 02 聊天分析 / 03 分析卡 / 04 档案页 / 05 任务面板 / 06 设置 / 07 心情回写 / 08 悬浮候选 / 09 诊断报告查看器。

## import_chat_from_text 实测结论
修复后 ok=true，new_count=3/parsed=3/aliases_recorded=1。根因是 service_facade.py 局部 import 漏 time（已补）。

## 仍待增强（未硬凑）
- 候选话术 采用/修改/拒绝、星级评分的原生按钮仍未补：牵涉在 ChatOverlay 加交互并接 record_reply_choice/record_user_correction/record_twin_feedback，本轮未做（后端回写已在 test_10 验证）。
