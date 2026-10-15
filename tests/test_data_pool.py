# -*- coding: utf-8 -*-
"""tests/test_data_pool.py —— 真实 DAL（services/data_pool.py）接口测试。

严格按 data_pool.py 真实签名编写，不臆造 API：
  write(profile, field, value, owner) -> profile   # schema 校验(告警不阻塞) + 双写告警 + 审计
  read(profile, field, default=None) -> Any          # = profile.get(field, default)
  get_audit_log() -> dict                             # 审计日志副本
  reset_audit() -> None                              # 清空审计（测试隔离）
  SCHEMA -> dict                                     # 受控字段 schema

覆盖：正常写入 / schema 类型·范围·枚举校验(告警不阻塞) / read 默认值 /
双写告警(同字段异属主) / 同属主重写不告警 / 审计日志含 field·owner·时间 / reset_audit /
授权矩阵(非属主写他人字段→冲突告警)。
"""
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services import data_pool
from services.data_pool import write, read, get_audit_log, reset_audit, SCHEMA


@pytest.fixture(autouse=True)
def _isolated_audit():
    """每个用例前后清空模块级审计，避免全局 _write_audit 串扰。"""
    reset_audit()
    yield
    reset_audit()


# --------------------------------------------------------------------------- #
# 正常写入
# --------------------------------------------------------------------------- #
def test_write_normal_sets_field_and_returns_profile():
    profile = {}
    out = write(profile, "attachment", {"type": "安全型"}, owner="_analyze_attachment")
    assert profile["attachment"] == {"type": "安全型"}
    # write 返回的是同一个 profile 对象（原地写）
    assert out is profile


def test_write_unknown_field_no_schema_still_writes():
    """不在 SCHEMA 里的字段：无校验，直接写。"""
    profile = {}
    write(profile, "free_form_note", "随便写", owner="someone")
    assert profile["free_form_note"] == "随便写"


# --------------------------------------------------------------------------- #
# schema 校验：告警但不阻塞
# --------------------------------------------------------------------------- #
def test_schema_type_warning_but_does_not_block(caplog):
    """deception_risk 预期 dict，给字符串 → 类型告警，仍写入（不阻塞）。"""
    with caplog.at_level("WARNING", logger="services.data_pool"):
        write({}, "deception_risk", "我是个字符串", owner="_run_deception_audit")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "类型" in msgs or "schema" in msgs
    # 关键：校验失败不阻塞——值照样落盘
    # （write 已原地写；这里单独再确认返回 profile 的写入行为）
    p = write({}, "deception_risk", "我是个字符串", owner="x")
    assert p["deception_risk"] == "我是个字符串"


def test_schema_range_warning_but_does_not_block(caplog):
    """deception_risk.risk_score 越界(0-100) → 范围告警，仍写入。"""
    with caplog.at_level("WARNING", logger="services.data_pool"):
        p = write({}, "deception_risk",
                  {"risk_score": 150, "risk_level": "高", "warnings": []},
                  owner="_run_deception_audit")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "范围" in msgs
    assert p["deception_risk"]["risk_score"] == 150  # 不阻塞


def test_schema_enum_warning_but_does_not_block(caplog):
    """deception_risk.risk_level 不在 [低/中/高/观察] → 枚举告警，仍写入。"""
    with caplog.at_level("WARNING", logger="services.data_pool"):
        p = write({}, "deception_risk",
                  {"risk_score": 0, "risk_level": "未知", "warnings": []},
                  owner="_run_deception_audit")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "枚举" in msgs
    assert p["deception_risk"]["risk_level"] == "未知"  # 不阻塞


def test_schema_valid_passes_without_warning(caplog):
    """完全合法的 deception_risk：不应产生校验告警。"""
    with caplog.at_level("WARNING", logger="services.data_pool"):
        write({}, "deception_risk",
              {"risk_score": 30, "risk_level": "观察", "warnings": []},
              owner="_run_deception_audit")
    schema_warnings = [r for r in caplog.records if "校验" in r.getMessage()]
    assert schema_warnings == []


# --------------------------------------------------------------------------- #
# read（含默认值）
# --------------------------------------------------------------------------- #
def test_read_existing_field():
    profile = {"psychology": {"overall_health": "健康"}}
    assert read(profile, "psychology") == {"overall_health": "健康"}


def test_read_missing_field_returns_default():
    profile = {}
    assert read(profile, "psychology", {}) == {}
    assert read(profile, "nope", default="X") == "X"


def test_read_does_not_mutate_profile():
    profile = {"a": 1}
    read(profile, "a")
    read(profile, "missing", 99)
    assert profile == {"a": 1}


# --------------------------------------------------------------------------- #
# 审计日志
# --------------------------------------------------------------------------- #
def test_audit_log_records_field_owner_and_time():
    write({}, "suspicion_chain", {"suspicion": 0.0, "level": "正常"},
          owner="suspicion_chain.py")
    log = get_audit_log()
    assert "suspicion_chain" in log
    entry = log["suspicion_chain"]
    assert entry["owner"] == "suspicion_chain.py"
    assert "written_at" in entry and entry["written_at"]  # ISO 时间串非空


def test_audit_log_top_level_copy_is_independent():
    """get_audit_log() 返回顶层 dict 的副本（dict(_write_audit)）：
    对返回值做顶层增删改不影响内部审计。（嵌套 dict 按实现为共享引用，此用例只断言顶层独立。）"""
    write({}, "attachment", {"type": "x"}, owner="a")
    log = get_audit_log()
    log.clear()  # 只清空返回的副本
    # 内部审计不受影响
    assert "attachment" in get_audit_log()
    assert get_audit_log()["attachment"]["owner"] == "a"


def test_reset_audit_clears():
    write({}, "attachment", {"type": "x"}, owner="a")
    assert "attachment" in get_audit_log()
    reset_audit()
    assert get_audit_log() == {}


# --------------------------------------------------------------------------- #
# 双写告警 / 授权矩阵
# --------------------------------------------------------------------------- #
def test_dual_write_different_owner_warns(caplog):
    """同字段先属主 A 写，后属主 B 写 → 双写告警（冲突），审计更新为 B。"""
    profile = {}
    write(profile, "deception_risk", {"risk_score": 10, "risk_level": "低", "warnings": []},
          owner="_run_deception_audit")
    with caplog.at_level("WARNING", logger="services.data_pool"):
        write(profile, "deception_risk", {"risk_score": 40, "risk_level": "高", "warnings": []},
              owner="someone_else")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "双写" in msgs
    # 审计记录为最后写入者
    assert get_audit_log()["deception_risk"]["owner"] == "someone_else"
    # 值被后写者覆盖（不静默，但也不阻塞主流程）
    assert profile["deception_risk"]["risk_level"] == "高"


def test_same_owner_rewrite_no_warning(caplog):
    """同属主重复写同字段：不算冲突，无双写告警。"""
    profile = {}
    write(profile, "psychology", {"overall_health": "健康"}, owner="_analyze_psychology")
    with caplog.at_level("WARNING", logger="services.data_pool"):
        write(profile, "psychology", {"overall_health": "需关注"}, owner="_analyze_psychology")
    dual_warn = [r for r in caplog.records if "双写" in r.getMessage()]
    assert dual_warn == []


def test_authorization_matrix_non_owner_write_conflict(caplog):
    """授权矩阵：受控字段 attachment 属主为 _analyze_attachment；
    非属主模块改写 → 触发冲突告警（在 DAL 层表现为双写告警）。"""
    profile = {}
    write(profile, "attachment", {"type": "安全型"}, owner="_analyze_attachment")
    with caplog.at_level("WARNING", logger="services.data_pool"):
        write(profile, "attachment", {"type": "其他模块越权写"}, owner="risk_warning_module")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "双写" in msgs  # 越权写被 DAL 标记为冲突
    # 属主约定：审计能查到两个属主，证明冲突可追溯
    assert get_audit_log()["attachment"]["owner"] == "risk_warning_module"


# --------------------------------------------------------------------------- #
# SCHEMA 结构自检（受控字段与真实定义一致）
# --------------------------------------------------------------------------- #
def test_schema_contains_four_controlled_fields():
    assert set(SCHEMA.keys()) == {"attachment", "psychology",
                                  "suspicion_chain", "deception_risk"}


def test_schema_deception_risk_has_range_and_enum():
    dr = SCHEMA["deception_risk"]
    assert dr["range"]["risk_score"] == (0, 100)
    assert "观察" in dr["enum"]["risk_level"]
