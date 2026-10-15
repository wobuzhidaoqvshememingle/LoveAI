# -*- coding: utf-8 -*-
"""快照阀门机制回归测试：白名单/DB字段拒覆盖/失败隔离/版本链。"""
import os, sys, tempfile, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from models.database import Database
from services.person_state_snapshot import build_person_snapshot


class TestSnapshotValve(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="loveai_valve_")
        self.db = Database(os.path.join(self.td, "s.db"))
        self.db.init_tables()
        self.db.execute(
            "INSERT INTO persons(id,nickname,gender,occupation,relationship_stage) "
            "VALUES(:i,:n,:g,:o,:s)", {"i": "pV", "n": "小王", "g": "男", "o": "司机", "s": 3})
        self.db.execute("INSERT INTO current_affection(person_id,total_score) VALUES(:p,:t)",
                       {"p": "pV", "t": 65.0})

    def test_illegal_extra_field_warned_not_break(self):
        snap = build_person_snapshot(self.db, "pV", extra={"unknown_thing": 123})
        # 非法字段不进快照，但快照构建正常
        self.assertNotIn("unknown_thing", snap)
        self.assertIn("affection", snap)

    def test_extra_cannot_override_db_field(self):
        snap = build_person_snapshot(self.db, "pV", extra={"affection": {"total": 999}})
        # DB 字段不被 extra 覆盖：好感度仍是DB的65，不是999
        self.assertEqual(snap["affection"]["total"], 65.0)

    def test_failed_field_estimated_others_ok(self):
        # memory_service 传一个会抛异常的对象 → memory 标 estimated，其他正常
        class Boom:
            def get_memory_context(self, *a, **k):
                raise RuntimeError("记忆服务挂了")
        snap = build_person_snapshot(self.db, "pV", memory_service=Boom())
        self.assertTrue(snap["memory"].get("estimated"))
        self.assertEqual(snap["affection"]["total"], 65.0)  # 其他字段正常

    def test_version_and_chain(self):
        s1 = build_person_snapshot(self.db, "pV", update_chain=["emotion", "strategy"])
        s2 = build_person_snapshot(self.db, "pV")
        # 版本号递增
        self.assertGreater(s2["snapshot_version"], s1["snapshot_version"])
        # 循环链被记录
        self.assertEqual(s1["update_chain"], ["emotion", "strategy"])
        # 时间戳映射存在
        self.assertIn("affection", s1["updated_at_map"])
        self.assertIn("built_at", s1)

    def test_loss_index_validation(self):
        # loss_index 存在但非数值 → 标 estimated
        snap = build_person_snapshot(self.db, "pV", extra={"loss_aversion": {"loss_index": "很高"}})
        self.assertTrue(snap["loss_aversion"].get("estimated"))
        # 合法 loss_aversion 透传
        snap2 = build_person_snapshot(self.db, "pV", extra={"loss_aversion": {"loss_index": 25}})
        self.assertEqual(snap2["loss_aversion"]["loss_index"], 25)


if __name__ == "__main__":
    unittest.main(verbosity=2)
