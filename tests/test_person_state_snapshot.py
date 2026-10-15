# -*- coding: utf-8 -*-
"""人物状态快照 + 数字分身快照注入回归测试。"""
import os, sys, tempfile, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from models.database import Database
from services.person_state_snapshot import build_person_snapshot
from services.digital_twin import DigitalTwin


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="loveai_snap_")
        self.db = Database(os.path.join(self.td, "s.db"))
        self.db.init_tables()
        self.db.execute(
            "INSERT INTO persons(id,nickname,gender,occupation,relationship_stage) "
            "VALUES(:i,:n,:g,:o,:s)", {"i": "pSnap", "n": "小花", "g": "女", "o": "设计师", "s": 5})
        self.db.execute(
            "INSERT INTO current_affection(person_id,total_score) VALUES(:p,:t)",
            {"p": "pSnap", "t": 40.0})
        for d, c in [("received", "在吗"), ("sent", "在的")]:
            self.db.execute(
                "INSERT INTO chat_messages(person_id,direction,content) VALUES(:p,:d,:c)",
                {"p": "pSnap", "d": d, "c": c})

    def test_snapshot_pulls_live_data(self):
        snap = build_person_snapshot(self.db, "pSnap", extra={
            "strategy": {"direction": "给空间"},
            "loss_aversion": {"loss_index": 30}})
        # 好感度从DB实时拉（不是旧json）
        self.assertEqual(snap["affection"]["total"], 40.0)
        # 阶段由好感度实时推导（40分→低阶）
        self.assertLessEqual(snap["stage"], 3)
        # 最近对话拉到2条
        self.assertEqual(len(snap["recent_chats"]), 2)
        # 瞬时产物透传
        self.assertEqual(snap["strategy"]["direction"], "给空间")
        self.assertEqual(snap["loss_aversion"]["loss_index"], 30)

    def test_twin_injects_snapshot(self):
        pdir = os.path.join(PROJECT_ROOT, "data", "persons", "pSnapTwin")
        os.makedirs(pdir, exist_ok=True)
        try:
            with open(os.path.join(pdir, "SOUL.md"), "w", encoding="utf-8") as f:
                f.write("她性格冷淡。")
            twin = DigitalTwin("pSnapTwin", self.db, llm=None, rag=None,
                               data_dir=os.path.join(PROJECT_ROOT, "data"))
            snap = build_person_snapshot(self.db, "pSnap", extra={
                "strategy": {"direction": "给空间/轻量接触"},
                "loss_aversion": {"risk_flag": "对方投入极少"}})
            prompt = twin._build_system_prompt(
                [{"content": "在吗"}], {"total": 75}, snapshot=snap)
            # 快照块注入prompt
            self.assertIn("最新人物状态快照", prompt)
            self.assertIn("给空间", prompt)
            self.assertIn("对方投入极少", prompt)
        finally:
            import shutil
            shutil.rmtree(pdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
