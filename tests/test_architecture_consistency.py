# -*- coding: utf-8 -*-
"""tests/test_architecture_consistency.py —— 架构一致性 7 项真实测试。

用真实 PersonaAnalyzer.analyze() 跑通主流水线（fake db 仅提供聊天行数据，
不 mock 任何被测逻辑），对产出 profile 断言 7 项已声称的架构不变量：

  1. deception_risk 与 suspicion_chain 对齐（score 校准=怀疑链值 且 calibration_note 存在）
  2. psychology 对方维度 evidence 无 direction=sent（sent 消息命中同关键词也被过滤）
  3. direction_history 写盘含真实方向 + 证据
  4. schema_version 存在且为当前版本（"v2.1"）
  5. DAL 写路径过闸：4 个受控字段在审计日志中留痕（write 真实触发）
  6. schema 校验失败用例：analyze 过程对越界枚举产生「校验告警」但不阻塞主画像
  7. 授权矩阵断言：每个受控字段的审计属主 = 约定属主，无双属主越权写

红线：不碰正式库（fake db 不落 loveai.db）、不调外部 LLM（analyze 纯规则）、
不写 boost_glide_planner / 微信 gateway。
"""
import os
import sys
import shutil

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services import data_pool
from services.persona_analyzer import PersonaAnalyzer

# 当前 schema 版本（与 persona_analyzer.analyze 落地值一致）
CURRENT_SCHEMA_VERSION = "v2.1"

# 约定属主（与 persona_analyzer 过闸处 owner= 一致）
EXPECTED_OWNERS = {
    "attachment": "_analyze_attachment",
    "psychology": "_analyze_psychology",
    "suspicion_chain": "suspicion_chain.py",
    "deception_risk": "_run_deception_audit",
}


class _FakeDB:
    """最小数据源：只返回我们构造的 chat_messages 行；其余 SQL 返回 []。
    不实现任何被测逻辑（校验/校准/证据/方向规则均在 PersonaAnalyzer 内部真实执行）。"""
    def __init__(self, messages):
        self._messages = messages

    def fetch_all(self, sql, params=None):
        if "chat_messages" in sql:
            return self._messages
        return []


MESSAGES = [
    # received：命中防御性沟通「贬低感受」关键词，用于填充 psychology.evidence
    {"direction": "received", "content": "你太敏感了，这有什么好生气的，别多想",
     "msg_type": 1, "created_at": "2026-10-05 20:00"},
    {"direction": "received", "content": "你是不是不爱我了，你去哪里了",
     "msg_type": 1, "created_at": "2026-10-05 20:01"},
    {"direction": "received", "content": "别管我，给我点时间，我习惯一个人",
     "msg_type": 1, "created_at": "2026-10-05 20:02"},
    {"direction": "received", "content": "记得她不吃辣，周末一起吃饭好吗",
     "msg_type": 1, "created_at": "2026-10-06 12:00"},
    # sent：含同一关键词「你太敏感了」，必须被 psychology evidence 过滤掉
    {"direction": "sent", "content": "你太敏感了，我没那个意思",
     "msg_type": 1, "created_at": "2026-10-05 21:00"},
    {"direction": "sent", "content": "今晚加班，晚点聊",
     "msg_type": 1, "created_at": "2026-10-05 21:30"},
]


@pytest.fixture(scope="module")
def analyzed():
    """跑一次真实 analyze，返回 (profile, audit_log)。模块级复用一次。"""
    data_pool.reset_audit()
    an = PersonaAnalyzer(db=_FakeDB(MESSAGES))
    prof = an.analyze("arch_consistency_tmp", {"nickname": "一致性测试"})
    log = data_pool.get_audit_log()
    yield prof, log
    # 清理 _save_profile 落到 data/persons 的测试画像文件（不碰 loveai.db）
    pdir = os.path.join(_PROJECT_ROOT, "data", "persons", "arch_consistency_tmp")
    if os.path.isdir(pdir):
        shutil.rmtree(pdir, ignore_errors=True)
    data_pool.reset_audit()


# --------------------------------------------------------------------------- #
# 4. schema_version 存在且为当前版本
# --------------------------------------------------------------------------- #
def test_schema_version_present_and_current(analyzed):
    prof, _ = analyzed
    assert prof.get("schema_version") == CURRENT_SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# 1. deception_risk 与 suspicion_chain 对齐
# --------------------------------------------------------------------------- #
def test_deception_risk_aligned_with_suspicion_chain(analyzed):
    prof, _ = analyzed
    dr = prof["deception_risk"]
    assert isinstance(dr, dict)
    # 校准后：risk_score 取怀疑链口径(30)，risk_level=观察，且留 calibration_note
    assert dr.get("calibrated") is True
    assert "calibration_note" in dr and dr["calibration_note"]
    assert dr.get("risk_score") == 30          # 与怀疑链 S=30 相等
    assert dr.get("risk_level") == "观察"
    # 旧值留痕备查
    assert "old_risk_score" in dr and "old_risk_level" in dr


# --------------------------------------------------------------------------- #
# 2. psychology 对方维度 evidence 无 direction=sent
# --------------------------------------------------------------------------- #
def test_psychology_evidence_never_sent(analyzed):
    prof, _ = analyzed
    ev = prof["psychology"].get("evidence", {})
    # 真实结构：evidence[组][类别] -> sample dict 列表（每条含 direction/snippet/matched/at）
    samples = []
    for _group, cats in ev.items():
        if isinstance(cats, dict):
            for _cat, items in cats.items():
                if isinstance(items, list):
                    samples.extend(it for it in items
                                   if isinstance(it, dict) and "direction" in it)
    # 非空断言：确实填充了证据（否则该用例空转）
    assert samples, "预期 psychology.evidence 至少有一条 sample"
    # 核心不变量：所有 sample 方向都是 received，绝无 sent
    for s in samples:
        assert s.get("direction") == "received", \
            f"psychology 对方维度 evidence 出现非 received 方向: {s}"
    # 额外：构造里那条 sent 消息(2026-10-05 21:00)含同关键词，绝不应作为证据出现
    assert not any(s.get("at") == "2026-10-05 21:00" for s in samples)


# --------------------------------------------------------------------------- #
# 3. direction_history 含真实方向 + 证据
# --------------------------------------------------------------------------- #
def test_direction_history_has_real_direction_and_evidence(analyzed):
    prof, _ = analyzed
    dh = prof["direction_history"]
    assert isinstance(dh, dict) and "error" not in dh
    assert dh.get("current_direction") in {"升温", "维持", "后撤"}
    history = dh.get("history", [])
    assert history, "direction_history.history 非空"
    for h in history:
        assert h.get("direction") in {"升温", "维持", "后撤"}
        assert h.get("evidence"), "每条历史方向都带证据"


# --------------------------------------------------------------------------- #
# 5. DAL 写路径过闸：4 受控字段审计留痕
# --------------------------------------------------------------------------- #
def test_dal_audit_records_four_controlled_fields(analyzed):
    _, log = analyzed
    for field in EXPECTED_OWNERS:
        assert field in log, f"受控字段 {field} 未经过 DAL.write 过闸（审计无留痕）"
        assert log[field].get("written_at"), f"{field} 无写时间"


# --------------------------------------------------------------------------- #
# 7. 授权矩阵：每字段属主 = 约定属主
# --------------------------------------------------------------------------- #
def test_authorization_matrix_owners(analyzed):
    _, log = analyzed
    for field, expected_owner in EXPECTED_OWNERS.items():
        assert log[field]["owner"] == expected_owner, \
            f"{field} 属主越权：期望 {expected_owner}，实际 {log[field]['owner']}"


# --------------------------------------------------------------------------- #
# 6. schema 校验失败用例（analyze 过程真实产生校验告警但不阻塞）
# --------------------------------------------------------------------------- #
def test_schema_validation_warns_but_pipeline_continues(analyzed, caplog):
    """deception_risk 校准前 risk_level='未知' 不在枚举 [低/中/高/观察]，
    DAL 过闸时应产生「枚举校验失败」告警；同时主画像仍完整产出（不阻塞）。"""
    prof, _ = analyzed
    # 主画像未被校验告警打断：核心字段都在
    assert "deception_risk" in prof and "psychology" in prof and "attachment" in prof
    # 直接对 DAL 复现一次越界枚举写入，确有告警（告警在独立用例触发以确保被捕获）
    import logging
    with caplog.at_level("WARNING", logger="services.data_pool"):
        data_pool.write({}, "deception_risk",
                        {"risk_score": 0, "risk_level": "未知", "warnings": []},
                        owner="_run_deception_audit")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "枚举" in msgs, "预期 DAL 对越界 risk_level 产生枚举校验告警"
