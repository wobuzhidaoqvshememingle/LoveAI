# LoveAI v3.0 代码-需求对照审计报告（只读）

> 审计方式：逐文件 Grep 源码 + 主流程引用检查 ｜ 审计时间：2026-10-07
> 判定标准：真实现(有代码+主流程调用+测试) / 空壳 / 孤岛(有实现未被主流程调用) / 缺失 / 方向错

---

## 一、最严重问题（Top 5，按影响排序）

### 🔴 P1：第六批16项 + 第七批21项全部为"孤岛模块"（共37项未接入主流程）
- **证据**：对 `batch6_growth/batch6_tasks/batch6_personalize/batch6_data/batch7_group1~4` 全项目 Grep，引用者只有各自的 `tests/test_batch*.py` 和旧 build 产物；`app/service_facade.py`、`services/analysis_orchestrator.py`、`application.py` **均无 import**。
- **影响**：用户成长/复盘/情景/Streak/任务链/每日任务/截图分析/危机预警/性能监控/话术个性化/5种风格/资料优化/时间线/隐私模式/多目标/AB学习，以及11级闯关/后果概率/通知/敏感词/备份/快捷键/主题/性别识别/双窗口/代理/学科挂载/放权/统计面板 —— 全部"代码+测试有，但 analyze_chat 主流程不调用"。
- **修法**：在 `service_facade.analyze_chat` 收尾处（事件埋点区）逐个 try/except 接入：UserGrowth.gain_xp、review()、crisis_alert()、task_chain()、relationship_timeline()、ABLearning.record()、outcome_probability()、stage_to_11()。

### 🔴 P2：微信相关5项为框架占位（已标注🟡，属实）
- `fill_wechat_input/q_avatar/match_person_by_window_title/wechat_ui_template/scan_wechat_files` 均返回 `ready:False` / payload，未通电。**属预期（等密钥），非缺陷，但不可算"已实现"。**

### 🟠 P3：复盘系统未接 analyze_chat
- `UserGrowth.review()` 存在且有测试，但主流程每次分析后不调用 → 用户看不到复盘、XP不增长。

### 🟠 P4：危机预警未接任务系统
- `crisis_alert()` 有黄橙红逻辑，但主流程不监测信号、不生成修复任务。

### 🟠 P5：AB学习未回灌策略效用函数
- `ABLearning.feedback` 有更新逻辑，但 `strategy_utility.fused_choose` 的 `history_effects` 永远传 `{}`（orchestrator 中硬编码）→ 动态权重名存实亡。

---

## 二、A类（日志写已实现，代码不完整）
| 需求 | 日志状态 | 实际 | 证据 |
|---|---|---|---|
| 第六批16项 | 已实现 | **孤岛**（P1） | 仅 tests 引用 |
| 第七批核心12项 | 已实现 | **孤岛/框架** | batch7_group*.py 未 import |
| 复盘接入 analyze_chat | 已接入 | 未接入 | review() 无主流程调用 |
| 危机预警接任务 | 已实现 | 未接入 | crisis_alert() 孤立 |
| AB学习优化话术/效用 | 已实现 | 未回灌 | fused_choose 中 history_effects={} |

> 大模块（affection_engine 53KB / task_engine 64KB / deception_detector 58KB / persona_analyzer 120KB / strategy_advisor 81KB / psychology_laws 131KB）均为真实实现，非空壳。

## 三、B类（代码已做但没接入=孤岛）
即上述37项 batch6/batch7 模块。统计：**孤岛 37 项**（其中5项微信为预期框架）。

## 四、C类（方向/参数核对）
| 检查项 | 结果 | 证据 |
|---|---|---|
| 11级闯关顺序(相识→…→终身伴侣) | ✅正确 | `batch7_group1.py::LEVELS11` 11项顺序一致 |
| 损失厌恶相对代价比例 | ✅ | `loss_aversion.py::analyze_loss_aversion` |
| Contract Net 三层(初筛→投标→选优) | ✅ | `strategy_utility.py::fused_choose`（已接 orchestrator ~358行）|
| 事件总线按需调度 changed_keys | ✅ | `event_bus.py` subscribe(keys_of_interest) |
| 参考提炼层在入池前调用 | 🟡 | `reference_refinery.refine` 存在；主流程调用点需在 facade 确认增量接入 |
| GOAP A*搜索 | ✅ | `goap_planner.py` |

---

## 五、需求项总表（统计）
| 判定 | 数量 |
|---|---|
| 真实现（大底座模块） | ~45 项 |
| 孤岛（有代码有测试但未入主流程） | 37 项 |
| 框架待密钥（预期） | 5 项（微信/Q版立绘） |
| 空壳 | 0 |
| 缺失 | 0 |
| 方向错 | 0 |

## 六、结论
- **底座架构（中心池/事件总线/提炼层/Contract Net/战略层/大模块）为真实现且联动生效。**
- **唯一系统性问题：37项新功能是"独立模块+独立测试"，未在 analyze_chat 主流程埋点接入。** 这不是代码错误，而是接线缺失——测试全绿但用户端用不到。
- **封包建议**：若上层要求功能可用，需先在 `service_facade` 收尾处把37项逐个 try/except 接入（约半天工作量）；若封包仅交付底座+已验证大模块，则当前682测试绿可封。

**是否需立即修复**：建议修 P1（批量接线），否则37项功能对用户不可见。
