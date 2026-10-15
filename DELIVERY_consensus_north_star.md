# LoveAI 罗盘新增：共识 vs 顺从信号 + 北极星改造（P1）

日期：2026-10-13　范围：`services/consensus_signal.py`（新增）、`services/direction_compass.py`、`services/task_engine.py`、`tests/test_consensus_signal.py`（新增）。

## 1. 北极星口径（用户已定，不再问）
- **主信号** = 她「主动参与解决」：她提方案/建议、让步但带自己需求（"…可以，但你要…"）、
  协作措辞（"我们一起""咱俩""商量"）、主动推进话题/修复冲突。
- **参考指标** = 关系满意度（画像 `relationship_satisfaction.score`），只做佐证/背离提示，
  不盖过主信号。

## 2. 共识 vs 顺从（核心区别）
| 类别 | 定义 | 例子 | 是否计入主信号 |
|---|---|---|---|
| consensus 主动参与 | 她为解决关系投入，提方案/让步带需求/协作 | "我们一起把问题说开"、"可以，但你要早点睡" | **是** |
| compliance 顺从同意 | 无投入的点头/服软，无新信息、无提议 | "嗯""好吧""都行""听你的""哦""好的" | **否** |

**与既有"回撤≠降频"逻辑的边界（铁律，写在模块 docstring）**：
1. 顺从型同意**绝不**触发回撤/降频信号；
2. 顺从型同意**绝不**计入修复/升温证据；
3. 罗盘 `north_star` 是纯附加键，**不回流** gates/drift/aligned（已加测试 `test_existing_logic_unchanged` 证明有/无它 `aligned`、`drift` 结果一致）；
4. 每事件带 `counts_toward_main_signal` 布尔，consensus=True、compliance=False。

误报防护（真实库踩到后收紧）：`要不是…`、`不如不听/还不如`、`就这样吧/先这样吧/随便吧`
（放弃口吻）一律判 neutral，不读成主动参与。

## 3. 主信号分公式
`active_participation_score = 100 × (0.7 × 主动占比 + 0.3 × 近30天活跃度因子)`
- 主动占比 = consensus/(consensus+compliance)；
- 活跃度因子 = min(1, 近30天 consensus 数/3)；
- 达标：主动占比 ≥ 0.5 且 近30天趋势 ≠ down。

## 4. 快照接线（task_engine._build_compass_snapshot）
- ⑨ 参考指标：从 persona_profile.json 读 `relationship_satisfaction.{score,level}` → `snap["relationship_satisfaction"]`；
- ⑩ 主信号：调 `run_consensus_signal(self.db.db_path, person_id)` **直接读库算**（不依赖 persona 新字段，最稳），失败降级 None。

## 5. evaluate() 返回 JSON 示例片段（真实库 person_id=demo0001 实测）
```json
"north_star": {
  "definition": "北极星 = 主动参与解决(主信号) + 关系满意度(参考)",
  "main_signal": {
    "role": "主信号",
    "active_participation_score": 0.3,
    "consensus_event_count": 3,
    "consensus_rate": 0.005,
    "compliance_event_count": 624,
    "trend_30d": {"consensus_recent": 0, "consensus_prior_30d": 0,
                  "compliance_recent": 90, "momentum": "flat"},
    "reached": false
  },
  "reference": {
    "role": "参考指标",
    "relationship_satisfaction": {"score": 59.4, "level": "中等满意度"}
  },
  "combined_reading": "主信号未达标（主动参与分 0.3）且满意度 59.4 偏低：她既少主动投入也不快乐；重点补低压沟通，创造她愿意主动提需求/方案的窗口。"
}
```
实测主动参与证据（仅 3 条，带原文）：
- 2026-06-02 `咱俩不是一直挺好的吗`（协作措辞:咱俩, msg_id=2932）
- 2026-06-12 `咱俩的事`（协作措辞:咱俩, msg_id=1623）
- 2026-07-07 `问他晚上要不要出去吃好吃的`（提出方案, msg_id=6064）

## 6. 验收
- `pytest tests/test_consensus_signal.py` → **22 passed**；
- `pytest tests/test_direction_compass.py` → **16 passed**（既有逻辑无回归）；
- 红线：`boost_glide_planner.py` SHA256 = `3BBE363E…E360E5C701`（逐字节一致，未动）；
- 未改 persona_analyzer.py / suspicion_chain.py / llm_gateway.py / free_model_router.py / data_pool.py；只读 loveai.db，未写库。
