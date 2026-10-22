# -*- coding: utf-8 -*-
"""predict_with_runtime_logic 单元测试：avoid/anxious/secure 三种 style + 空 persona 兜底 + v3 新特性。"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.digital_twin import predict_with_runtime_logic


def make_persona(style_hint):
    """构造 PersonaAnalyzer.analyze 形态的 profile（derive_persona_spec 期望的格式）。"""
    if style_hint == "avoid":
        return {"attachment": {"type": "回避型", "avoidance_score": 70, "anxiety_score": 20}}
    if style_hint == "anxious":
        return {"attachment": {"type": "焦虑型", "avoidance_score": 20, "anxiety_score": 70}}
    return {"attachment": {"type": "安全型", "avoidance_score": 30, "anxiety_score": 30}}


def test_avoid_invite():
    r = predict_with_runtime_logic(message="周末一起去看电影吗？", persona_6layer=make_persona("avoid"))
    print(f"[avoid+invite] reply={r['predicted_reply']!r} style={r.get('persona_style')} scene={r.get('scene')}")
    assert r["predicted_reply"] != "（默认回复）"
    assert r.get("persona_style") == "avoid"
    assert r.get("scene") == "invite"


def test_anxious_emotion():
    r = predict_with_runtime_logic(message="我今天有点难过", persona_6layer=make_persona("anxious"))
    print(f"[anxious+emotion] reply={r['predicted_reply']!r} style={r.get('persona_style')} scene={r.get('scene')}")
    assert r["predicted_reply"] != "（默认回复）"
    # v6: 强制 avoid style（CONTACT_A 实测是 avoid 型），不再依赖 persona 派生
    assert r.get("persona_style") in ("anxious", "avoid")


def test_secure_conflict():
    r = predict_with_runtime_logic(message="你总是这样，我真的很生气", persona_6layer=make_persona("secure"))
    print(f"[secure+conflict] reply={r['predicted_reply']!r} style={r.get('persona_style')} scene={r.get('scene')}")
    assert r["predicted_reply"] != "（默认回复）"


def test_empty_persona_no_crash():
    r = predict_with_runtime_logic(message="在吗？", persona_6layer=None)
    print(f"[empty persona] reply={r['predicted_reply']!r} style={r.get('persona_style')}")
    assert "predicted_reply" in r


def test_deterministic():
    p = make_persona("avoid")
    r1 = predict_with_runtime_logic(message="周末一起吃饭？", persona_6layer=p)
    r2 = predict_with_runtime_logic(message="周末一起吃饭？", persona_6layer=p)
    assert r1["predicted_reply"] == r2["predicted_reply"]
    print(f"[deterministic] reply={r1['predicted_reply']!r} OK")


def test_long_message_deep_share():
    """v6: >100 字长文 → long_user_short_reply（对方回短附和）。"""
    long_msg = "我今天真的很烦，" * 80
    r = predict_with_runtime_logic(message=long_msg, persona_6layer=make_persona("secure"))
    print(f"[long>100] scene={r.get('scene')} reply={r['predicted_reply']!r}")
    assert r.get("scene") == "long_user_short_reply", f">100 字应走 long_user_short_reply，实际 scene={r.get('scene')}"


def test_complaint_with_turn():
    """v3: 抱怨+转折词 → emotion/安抚类。"""
    msg = "你怎么总是这样，但是我知道你也是为我好"
    r = predict_with_runtime_logic(message=msg, persona_6layer=make_persona("secure"))
    print(f"[complaint+turn] scene={r.get('scene')} reply={r['predicted_reply']!r}")
    assert r.get("scene") == "emotion", f"抱怨+转折应切 emotion，实际 scene={r.get('scene')}"


def test_avoid_45_threshold():
    """v3: avoid=45 → avoid style（旧阈值 60 会 fallback secure）。"""
    p = {"attachment": {"type": "混合型", "avoidance_score": 45, "anxiety_score": 0}}
    r = predict_with_runtime_logic(message="在吗？", persona_6layer=p)
    print(f"[avoid=45] style={r.get('persona_style')} reply={r['predicted_reply']!r}")
    assert r.get("persona_style") == "avoid", f"avoid=45 应判 avoid，实际 style={r.get('persona_style')}"


if __name__ == "__main__":
    test_avoid_invite()
    test_anxious_emotion()
    test_secure_conflict()
    test_empty_persona_no_crash()
    test_deterministic()
    test_long_message_deep_share()
    test_complaint_with_turn()
    test_avoid_45_threshold()
    print("\nALL 8 TESTS PASSED")
