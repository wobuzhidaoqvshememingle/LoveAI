"""R3.3-79 家庭背景线索自动提取测试。

只测新增的模块级函数 extract_family_context，不改动任何现有测试。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.persona_analyzer import extract_family_context  # noqa: E402


def test_a_divorce_in_progress_and_siblings():
    msgs = ["我妈昨天问起你，我爸在闹离婚，我姐总说我"]
    r = extract_family_context(msgs)
    assert r["parents_status"] == "离婚中"
    assert "姐姐" in r["siblings"]
    assert r["family_structure"] != "未知"
    assert r["manual"] is False


def test_b_single_parent_literal():
    msgs = ["我从小没爸，跟我妈过"]
    r = extract_family_context(msgs)
    assert r["family_structure"] == "单亲"


def test_c_harmonious_parents_and_sibling():
    msgs = ["我爸妈感情很好，我妹特别粘我"]
    r = extract_family_context(msgs)
    assert r["parents_status"] == "和睦"
    assert "妹妹" in r["siblings"]


def test_d_no_clue_all_unknown():
    msgs = ["今天天气不错", "刚吃完饭"]
    r = extract_family_context(msgs)
    assert r["family_structure"] == "未知"
    assert r["parents_status"] == "未知"
    assert r["atmosphere"] == "未知"
    assert r["siblings"] == []
    assert r["evidence"] == []


def test_e_manual_override_priority():
    msgs = ["我爸妈感情很好"]  # 字面会得出 和睦/双亲
    r = extract_family_context(msgs, manual={"family_structure": "重组"})
    assert r["family_structure"] == "重组"       # manual 覆盖
    assert r["parents_status"] == "和睦"         # 未提供的键仍走字面提取
    assert r["manual"] is True


def test_f_conflict_voting_majority():
    # 紧张 2 票（msg0, msg2）vs 和睦 1 票（msg1）→ 紧张 胜
    msgs = [
        "我爸我妈吵架",
        "我爸妈感情很好",
        "爸妈老吵架，这日子没法过了",
    ]
    r = extract_family_context(msgs)
    assert r["parents_status"] == "紧张"


def test_dict_message_compat_and_evidence_dedup():
    msgs = [
        {"content": "我从小没爸，跟我妈过"},
        "我从小没爸，跟我妈过",  # 重复消息，evidence 应去重
        {"text": "忽略这条，没有 content 键"},
    ]
    r = extract_family_context(msgs)
    assert r["family_structure"] == "单亲"
    assert r["evidence"].count("我从小没爸，跟我妈过") == 1


def test_g_divorced_counts_single_parent_and_divorced():
    msgs = ["他们说我是父母离异家庭的孩子"]
    r = extract_family_context(msgs)
    assert r["family_structure"] == "单亲"
    assert r["parents_status"] == "离异"
