# -*- coding: utf-8 -*-
"""长久爱情神经科学定律（VTA/多巴胺）触发回归测试。"""
import os, sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.psychology_laws import match_laws


def test_neuroscience_law_triggers_in_long_term():
    # 长期/稳定/婚姻阶段（6-8）应触发
    for stage in (6, 7, 8):
        hits = match_laws(persona={}, stage=stage, dims={})
        ids = [h["id"] for h in hits]
        assert "long_term_love_neuroscience" in ids, f"stage={stage} 应触发"

def test_neuroscience_law_not_in_early_stage():
    # 初识/热恋/冲突期不应触发
    for stage in (1, 2, 3, 4, 5):
        hits = match_laws(persona={}, stage=stage, dims={})
        ids = [h["id"] for h in hits]
        assert "long_term_love_neuroscience" not in ids, f"stage={stage} 不应触发"

def test_neuroscience_law_content_neutral():
    hits = match_laws(persona={}, stage=7, dims={})
    law = next(h for h in hits if h["id"] == "long_term_love_neuroscience")
    assert "VTA" in law["definition"] or "多巴胺" in law["definition"]
    # 含对"变亲情"认知偏差的反驳
    assert "亲情" in law["strategy"]
    # 手段中立：无道德禁令
    for bad in ("禁止使用", "绝对不要"):
        assert bad not in law["strategy"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
