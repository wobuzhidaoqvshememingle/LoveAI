# -*- coding: utf-8 -*-
"""第六批第一批：用户成长/复盘/情景模拟/XP Streak。"""
import os, sys, tempfile, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from models.database import Database
from services.batch6_growth import UserGrowth, ScenarioSimulator, XPStreak


class TestBatch6A(unittest.TestCase):
    def test_growth_level_unlock(self):
        g = UserGrowth()
        r = g.gain_xp(250, "策略力")
        self.assertGreaterEqual(r["level"], 3)
        self.assertIn("深度情感投标", r["unlocked"])

    def test_review_gives_xp(self):
        g = UserGrowth()
        r = g.review("开场自然", "追问太多", "下次留空间", "对方回复变短")
        self.assertGreater(r["xp_gained"], 0)

    def test_simulator_scores(self):
        sim = ScenarioSimulator()
        r = sim.score_reply("我理解你累了，周末一起放松下？", "推进")
        self.assertGreater(r["score"], 60)

    def test_streak_bonus(self):
        st = XPStreak()
        st.login()
        st.streak = 7
        gained = st.add(10)
        self.assertAlmostEqual(gained, 15.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
