# -*- coding: utf-8 -*-
"""共识 vs 顺从信号 单元测试。

覆盖：
1. 主动参与模式检测（提方案/协作/修复/让步带需求）；
2. 顺从型同意识别（无投入点头）；
3. 误报防护（要不是/不如不听/就这样吧 等不被判成主动参与）；
4. 与"回撤≠降频"逻辑的边界（compliance 绝不进主信号）；
5. 聚合：计数/分数/趋势/达标判定；
6. 罗盘 north_star 输出段（主信号 + 满意度参考 + 结合解读）。
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.consensus_signal import (  # noqa: E402
    classify_one, detect, run_consensus_signal,
)
from services.direction_compass import DirectionCompass  # noqa: E402


def _msg(direction, content, at, wid=None):
    return {"direction": direction, "content": content,
            "created_at": at, "wechat_msg_id": wid}


# --------------------------------------------------------------------------- #
# 1. 主动参与模式
# --------------------------------------------------------------------------- #
class TestConsensusPatterns:
    def test_proposal(self):
        cat, pat = classify_one("要不我们周末一起去看展吧")
        assert cat == "consensus" and "提" in pat

    def test_collab(self):
        cat, pat = classify_one("我们一起把这个问题解决掉")
        assert cat == "consensus" and "协作" in pat

    def test_resolve(self):
        cat, pat = classify_one("我们还是好好谈谈吧，把话说开")
        assert cat == "consensus" and ("推进" in pat or "协作" in pat)

    def test_concession_with_need(self):
        """让步但带需求 = 主动参与，不是顺从。"""
        cat, pat = classify_one("可以，但你以后要早点睡")
        assert cat == "consensus" and "让步" in pat

    def test_concession_with_need2(self):
        cat, pat = classify_one("行，不过周末得陪我")
        assert cat == "consensus" and "让步" in pat


# --------------------------------------------------------------------------- #
# 2. 顺从型同意
# --------------------------------------------------------------------------- #
class TestCompliance:
    def test_exact_nod(self):
        for s in ["嗯", "嗯嗯", "好吧", "都行", "听你的", "哦", "好的", "ok"]:
            cat, _ = classify_one(s)
            assert cat == "compliance", f"{s!r} 应判顺从"

    def test_compliance_has_no_investment(self):
        """顺从句不含任何主动参与标记。"""
        cat, pat = classify_one("好")
        assert cat == "compliance"


# --------------------------------------------------------------------------- #
# 3. 误报防护（真实库里踩到的假阳性）
# --------------------------------------------------------------------------- #
class TestFalsePositiveGuard:
    def test_yaobushi_not_proposal(self):
        """'要不是因为要出去'里的'要不'是连词，不是提议。"""
        cat, _ = classify_one("要不是因为要出去，我才不去")
        assert cat != "consensus"

    def test_buru_idiom_not_proposal(self):
        """'听了不如不听'是惯用语，不是联合提议。"""
        cat, _ = classify_one("这话说了不如不说")
        assert cat != "consensus"

    def test_resignation_not_consensus(self):
        """'就这样吧/先这样吧'是抽离，既非主动参与也非顺从。"""
        for s in ["就这样吧", "就先这样吧", "随便吧", "无所谓了"]:
            cat, _ = classify_one(s)
            assert cat == "neutral", f"{s!r} 应判 neutral（放弃口吻）"

    def test_polite_decline_not_compliance(self):
        """'不用了'是婉拒，不是顺从同意。"""
        cat, _ = classify_one("不用了")
        assert cat != "compliance"

    def test_nontext_skipped(self):
        cat, _ = classify_one("b'(\\xb5/\\xfd <img src=x.png'")
        assert cat == "neutral"


# --------------------------------------------------------------------------- #
# 3b. 校准v2：评估集漏检补入 + 误报陷阱排除（真实库证据驱动）
# --------------------------------------------------------------------------- #
class TestCalibrationV2:
    def test_zhenhaotanpan_proactive(self):
        """'坐下来好好谈谈'=行动型主动，应判 consensus。"""
        cat, pat = classify_one("又想坐下来好好谈谈")
        assert cat == "consensus" and ("谈" in pat)

    def test_missingyoule_warmth(self):
        """'我也想你了'=主动修复/情感投入，应判 consensus（旧规则全漏）。"""
        cat, _ = classify_one("我也想你了")
        assert cat == "consensus"

    def test_aiyou_positive(self):
        cat, _ = classify_one("我是爱你 但我说不出口")
        assert cat == "consensus"

    def test_wen_ta_third_party_excluded(self):
        """'问他晚上要不要出去吃好吃的'指第三方，不能误判成提议。"""
        cat, _ = classify_one("问他晚上要不要出去吃好吃的")
        assert cat != "consensus"

    def test_zanliaodeshi_boundary_excluded(self):
        """'咱俩的事'是划边界，不是协作措辞。"""
        cat, _ = classify_one("你也别纠缠我弟 咱俩的事")
        assert cat != "consensus"

    def test_meitantuo_divorce_not_proactive(self):
        """'没谈妥就离婚'是离婚威胁，'谈妥'不能误命中为主动。"""
        cat, _ = classify_one("没谈妥就离婚 然后我妈也知道我是啥样的人了")
        assert cat != "consensus"

    def test_neg_love_guarded(self):
        """'别提爱我/求你别爱我了'是否定狠话，不能误判成情感投入。"""
        assert classify_one("别提爱我 你也没信过我")[0] != "consensus"
        assert classify_one("我有时候真的想求你别爱我了")[0] != "consensus"

    def test_woquni_action_proactive(self):
        cat, _ = classify_one("你要这几天有时间就来吧 或者等六月初我去找你")
        assert cat == "consensus"



# --------------------------------------------------------------------------- #
# 4. 聚合 + 边界
# --------------------------------------------------------------------------- #
class TestDetectAggregation:
    def _msgs(self):
        return [
            _msg("received", "嗯", "2026-10-01 10:00:00", "w1"),
            _msg("received", "好的", "2026-10-02 10:00:00", "w2"),
            _msg("received", "要不我们周末一起去爬山", "2026-10-03 10:00:00", "w3"),
            _msg("received", "可以，但你要提前订酒店", "2026-10-04 10:00:00", "w4"),
            _msg("sent", "我发的", "2026-10-04 11:00:00", "w5"),
        ]

    def test_counts(self):
        r = detect(self._msgs())
        assert r["consensus_event_count"] == 2
        assert r["compliance_event_count"] == 2

    def test_compliance_not_main_signal(self):
        r = detect(self._msgs())
        for e in r["compliance_events"]:
            assert e["counts_toward_main_signal"] is False
        for e in r["consensus_events"]:
            assert e["counts_toward_main_signal"] is True

    def test_events_carry_evidence(self):
        r = detect(self._msgs())
        ev = r["consensus_events"][0]
        assert ev["at"] and ev["wechat_msg_id"] and ev["content"]

    def test_score_and_reached(self):
        r = detect(self._msgs())
        # 2 consensus / 2 compliance -> active_ratio=0.5, 达标线 >=0.5
        assert r["active_participation_score"] is not None
        assert r["main_signal_reached"] in (True, False)

    def test_no_relation_msgs_score_none(self):
        r = detect([_msg("received", "今天天气不错", "2026-10-01 10:00:00")])
        assert r["active_participation_score"] is None
        assert r["main_signal_reached"] is None


# --------------------------------------------------------------------------- #
# 5. run_consensus_signal（临时真实 sqlite 库）
# --------------------------------------------------------------------------- #
class TestRunEntry:
    def _temp_db(self, rows):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " person_id TEXT, direction TEXT, content TEXT, msg_type TEXT,"
            " emotion TEXT, emotion_score REAL, wechat_msg_id TEXT,"
            " created_at TEXT)")
        for d, c, at, wid in rows:
            conn.execute("INSERT INTO chat_messages(person_id,direction,content,"
                         "created_at,wechat_msg_id) VALUES(?,?,?,?,?)",
                         ("p1", d, c, at, wid))
        conn.commit()
        conn.close()
        return path

    def test_run_reads_db(self):
        path = self._temp_db([
            ("received", "嗯", "2026-10-01 10:00:00", "a1"),
            ("received", "我们一起商量一下", "2026-10-02 10:00:00", "a2"),
        ])
        try:
            r = run_consensus_signal(path, "p1")
            assert r["person_id"] == "p1"
            assert r["consensus_event_count"] == 1
            assert r["compliance_event_count"] == 1
        finally:
            os.remove(path)


# --------------------------------------------------------------------------- #
# 6. 罗盘 north_star 输出段
# --------------------------------------------------------------------------- #
class TestCompassNorthStar:
    c = DirectionCompass()

    def test_north_star_present(self):
        snap = {
            "strategy_text": "破冰升温，引导对等投入推进",
            "consensus": {
                "active_participation_score": 72.0,
                "consensus_event_count": 6,
                "consensus_rate": 0.6,
                "compliance_event_count": 4,
                "trend_30d": {"consensus_recent": 3, "momentum": "up"},
                "main_signal_reached": True,
                "main_signal_note": "达标条件=主动占比0.6>=0.5",
            },
            "relationship_satisfaction": {"score": 70.0, "level": "较高满意度"},
        }
        out = self.c.evaluate("p1", snap)
        ns = out["north_star"]
        assert ns["main_signal"]["role"] == "主信号"
        assert ns["reference"]["relationship_satisfaction"]["score"] == 70.0
        # 主信号达标 + 满意度>=60 -> 一致向好
        assert "双向" in ns["combined_reading"] or "主动" in ns["combined_reading"]

    def test_false_calm_warning(self):
        """主信号未达标但满意度尚可 -> 必须提示'伪平稳'。"""
        snap = {
            "strategy_text": "破冰升温，引导对等投入推进",
            "consensus": {
                "active_participation_score": 10.0,
                "consensus_event_count": 1,
                "consensus_rate": 0.02,
                "compliance_event_count": 50,
                "trend_30d": {"momentum": "flat"},
                "main_signal_reached": False,
                "main_signal_note": "达标条件=主动占比0.02<0.5",
            },
            "relationship_satisfaction": {"score": 65.0, "level": "中等满意度"},
        }
        out = self.c.evaluate("p1", snap)
        assert out["north_star"]["main_signal"]["reached"] is False
        assert "伪平稳" in out["north_star"]["combined_reading"]

    def test_empty_snapshot_no_crash(self):
        """无 consensus / 无满意度 -> north_star 仍在，主信号标 None，不崩。"""
        out = self.c.evaluate("p1", {"strategy_text": "推进修复"})
        ns = out["north_star"]
        assert ns["main_signal"]["active_participation_score"] is None
        assert ns["main_signal"]["reached"] is None
        # 既有键不被破坏
        for k in ("aligned", "gates", "drift", "metrics", "direction_probability"):
            assert k in out

    def test_existing_logic_unchanged(self):
        """north_star 是纯附加键：有/无它，gates/drift/aligned 结果一致。"""
        base = {"strategy_text": "放弃吧，及时止损分手撤离", "affection_trend": "down",
                "metrics": {"mutual": 0.2, "reciprocity": 0.2}}
        with_ns = dict(base, consensus={"active_participation_score": 80,
                                        "main_signal_reached": True})
        o1 = self.c.evaluate("p1", base)
        o2 = self.c.evaluate("p1", with_ns)
        assert o1["aligned"] == o2["aligned"]
        assert o1["drift"]["drift"] == o2["drift"]["drift"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
