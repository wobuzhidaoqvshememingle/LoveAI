# -*- coding: utf-8 -*-
"""第八批第二批：Token/会话/断点/历史/分身接口。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.batch8_workflow import (TokenTracker, SessionManager,
    AnalysisCheckpoint, AnalysisHistory, get_twin_status, switch_twin_mode)


class TestBatch8B(unittest.TestCase):
    def test_token_summary(self):
        t = TokenTracker(); t.log("emotion", 500, 200)
        self.assertEqual(t.summary()["total_in"], 500)

    def test_session_resume(self):
        s = SessionManager(); s.save("p", {"x":1})
        self.assertTrue(s.has_resume("p"))

    def test_checkpoint_skip(self):
        c = AnalysisCheckpoint(); c.mark("emotion")
        self.assertTrue(c.skip("emotion"))

    def test_history(self):
        h = AnalysisHistory(); h.add("p", "a"); h.add("p", "b")
        self.assertEqual(len(h.list_for("p")), 2)

    def test_twin_switch(self):
        self.assertTrue(switch_twin_mode("user")["switched"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
