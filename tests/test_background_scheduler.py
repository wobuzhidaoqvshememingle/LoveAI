# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - BackgroundScheduler 单元测试

覆盖：
  1. BackgroundUpdater 可实例化、services 正确挂载
  2. 无活跃人物时本轮跳过
  3. 并发保护：上一轮没跑完，新一轮直接跳过
  4. 异常隔离：一个人物处理失败不影响其他人物
  5. 正常轮次：各服务被依次调用（好感度衰减 / 记忆 / 画像 / 财务 / 数字分身）
  6. BackgroundScheduler(QObject) start/stop 正常，状态可查
"""
import os
import sys
import shutil
import tempfile
import threading
import time
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402


# ---------------------------------------------------------------------- #
# 假服务：记录调用，可按需抛异常
# ---------------------------------------------------------------------- #
class FakeAffection:
    def __init__(self):
        self.decay_calls = []
        self.scores = {"信任": 50.0, "情绪价值": 50.0}

    def apply_daily_decay(self, person_id):
        self.decay_calls.append(person_id)

    def get_current(self, person_id):
        return dict(self.scores)


class FakeMemory:
    def __init__(self):
        self.consolidate_calls = []
        self.decay_calls = []
        self.extract_calls = []

    def consolidate_memories(self, person_id):
        self.consolidate_calls.append(person_id)
        return {"merged": 0}

    def apply_decay(self, person_id):
        self.decay_calls.append(person_id)
        return 0

    def extract_from_chat(self, person_id, messages):
        self.extract_calls.append((person_id, len(messages)))
        return []


class FakePersona:
    """analyze 可配置：对 raise_for 里的 person_id 抛异常。"""
    def __init__(self, raise_for=None):
        self.analyze_calls = []
        self.raise_for = set(raise_for or [])

    def analyze(self, person_id, person_info=None):
        if person_id in self.raise_for:
            raise RuntimeError(f"persona boom for {person_id}")
        self.analyze_calls.append(person_id)
        return {"summary": "ok", "attachment": {}, "love_languages": {},
                "power_dynamic": {}, "emotional_bank": {}, "risk_warning": {}}


class FakeFinancial:
    def __init__(self):
        self.report_calls = []

    def generate_report(self, person_id):
        self.report_calls.append(person_id)
        return {"person_id": person_id}


class FakeTwin:
    def __init__(self):
        self.predict_calls = []

    def predict_reaction(self, user_msg, recent_chats=None, affection=None):
        self.predict_calls.append(user_msg)
        return {"likely_reaction": "neutral", "confidence": 0.5}


class FakeFreeRouter:
    def __init__(self):
        self.refresh_calls = 0

    def refresh_catalog(self, force=False):
        self.refresh_calls += 1
        return {"openrouter": {"ok": True, "count": 3}}


class FakeLLM:
    def __init__(self, router):
        self._router = router

    def get_free_router(self):
        return self._router


def _make_fake_facade(db, services):
    """构造一个最小 facade：只暴露 .services 字典。"""
    class _F:
        pass
    f = _F()
    f.services = dict(services)
    f.services["db"] = db
    return f


# ====================================================================== #
# BackgroundUpdater 测试（纯 Python，不需要 QApplication）
# ====================================================================== #
class TestBackgroundUpdater(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_test_bg_")
        self.db_path = os.path.join(self.tmpdir, "test_bg.db")
        self.db = Database(self.db_path)
        self.db.init_tables()

        # 种子两个活跃人物 + 各自聊天消息
        for pid in ("pA", "pB"):
            self.db.execute(
                "INSERT INTO persons (id, nickname, is_active) VALUES (:id, :nick, 1)",
                {"id": pid, "nick": pid},
            )
            # 每个塞 15 条消息，够 persona.analyze 的 >=10 门槛
            for i in range(15):
                self.db.execute(
                    "INSERT INTO chat_messages (person_id, direction, content) "
                    "VALUES (:pid, :dir, :ct)",
                    {"pid": pid, "dir": "received", "ct": f"hello {pid} {i}"},
                )

        self.affection = FakeAffection()
        self.memory = FakeMemory()
        self.persona = FakePersona()
        self.financial = FakeFinancial()
        self.twin = FakeTwin()

        self.services = {
            "affection": self.affection,
            "memory": self.memory,
            "persona": self.persona,
            "financial": self.financial,
            "digital_twin_factory": lambda pid: self.twin,
        }
        self.facade = _make_fake_facade(self.db, self.services)

        from app.background_scheduler import BackgroundUpdater
        self.updater = BackgroundUpdater(self.facade, recent_n=10, person_timeout=5.0)

    def tearDown(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    def test_instantiation(self):
        """实例化：services 字典正确挂上。"""
        self.assertIs(self.updater.services["db"], self.db)
        self.assertEqual(self.updater.recent_n, 10)

    def test_catalog_refresh_runs_without_persons(self):
        """无活跃人物（空闲跳过）时，全局模型目录刷新仍要执行一次。"""
        empty = Database(os.path.join(self.tmpdir, "empty.db"))
        empty.init_tables()
        router = FakeFreeRouter()
        facade = _make_fake_facade(empty, {"llm": FakeLLM(router)})
        from app.background_scheduler import BackgroundUpdater
        up = BackgroundUpdater(facade)
        stats = up.run_once()
        self.assertTrue(stats.get("skipped"))          # 无人物，空闲跳过
        self.assertEqual(router.refresh_calls, 1)      # 但目录已刷新
        self.assertIn("openrouter", stats["catalog_refresh"])
        empty.engine.dispose()

    def test_catalog_refresh_no_llm_safe(self):
        """无 llm 服务时，目录刷新步骤安全跳过、不报错。"""
        empty = Database(os.path.join(self.tmpdir, "empty2.db"))
        empty.init_tables()
        facade = _make_fake_facade(empty, {})
        from app.background_scheduler import BackgroundUpdater
        up = BackgroundUpdater(facade)
        stats = up.run_once()
        self.assertTrue(stats.get("skipped"))
        self.assertNotIn("catalog_refresh", stats)
        empty.engine.dispose()
        self.assertFalse(self.updater.is_running)

    def test_no_active_persons_skips(self):
        """无活跃人物：本轮直接 skipped=True。"""
        # 清空 persons / chat_messages
        self.db.execute("DELETE FROM chat_messages")
        self.db.execute("DELETE FROM persons")
        stats = self.updater.run_once()
        self.assertTrue(stats.get("skipped"))
        self.assertEqual(stats.get("reason"), "no_active_persons")
        # 没有服务被调用
        self.assertEqual(self.affection.decay_calls, [])
        self.assertEqual(self.memory.consolidate_calls, [])

    def test_concurrent_protection(self):
        """并发保护：上一轮锁没释放时，新一轮返回 skipped。"""
        # 主测试线程持有锁，模拟"上一轮还在跑"
        acquired = self.updater._lock.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            stats = self.updater.run_once()
            self.assertTrue(stats.get("skipped"))
            self.assertEqual(stats.get("reason"), "previous_run_in_progress")
            # 此时不应该动任何服务
            self.assertEqual(self.affection.decay_calls, [])
        finally:
            self.updater._lock.release()

    def test_exception_isolation(self):
        """异常隔离：pA 整个人物处理抛异常，pB 仍正常跑完。"""
        # 用 monkeypatch 让 pA 的 _run_one_person 直接炸掉，模拟"人物处理整体失败"
        original_run_one = self.updater._run_one_person

        def _fake_run_one(person_id):
            if person_id == "pA":
                raise RuntimeError("simulated crash for pA")
            return original_run_one(person_id)

        self.updater._run_one_person = _fake_run_one

        stats = self.updater.run_once()
        self.assertFalse(stats.get("skipped"))
        pids = {p["person_id"]: p for p in stats["persons"]}
        self.assertEqual(set(pids.keys()), {"pA", "pB"})
        # pA 失败
        self.assertFalse(pids["pA"]["ok"])
        self.assertIn("simulated crash", pids["pA"]["error"])
        # pB 成功
        self.assertTrue(pids["pB"]["ok"])
        # pA 的异常被记录
        self.assertTrue(any(e["person_id"] == "pA" for e in stats["errors"]))
        # 关键：pB 的服务照常跑了（证明 pA 没影响 pB）
        self.assertIn("pB", self.affection.decay_calls)
        self.assertIn("pB", self.financial.report_calls)

    def test_subservice_failure_does_not_kill_person(self):
        """单个子服务（persona.analyze）抛异常，同人物的其他步骤仍继续。"""
        # 让 pA 的 persona 炸掉
        self.persona.raise_for.add("pA")

        stats = self.updater.run_once()
        self.assertFalse(stats.get("skipped"))
        pids = {p["person_id"]: p for p in stats["persons"]}
        # persona 异常被内层 try/except 吞掉，人物整体仍算成功
        self.assertTrue(pids["pA"]["ok"])
        self.assertTrue(pids["pB"]["ok"])
        # pA 的好感度衰减/财务/数字分身照常跑（没被 persona 异常带崩）
        self.assertIn("pA", self.affection.decay_calls)
        self.assertIn("pA", self.financial.report_calls)
        self.assertGreaterEqual(len(self.twin.predict_calls), 1)

    def test_normal_run_calls_all_services(self):
        """正常轮次：好感度衰减/记忆整理/画像/财务/数字分身都被调到。"""
        stats = self.updater.run_once()
        self.assertFalse(stats.get("skipped"))
        for p in stats["persons"]:
            self.assertTrue(p["ok"], f"{p['person_id']} 应成功: {p}")

        # 好感度衰减
        self.assertCountEqual(self.affection.decay_calls, ["pA", "pB"])
        # 记忆整理
        self.assertCountEqual(self.memory.consolidate_calls, ["pA", "pB"])
        self.assertCountEqual(self.memory.decay_calls, ["pA", "pB"])
        # 画像（消息数 15 >= 10 才会跑）
        self.assertCountEqual(self.persona.analyze_calls, ["pA", "pB"])
        # 财务
        self.assertCountEqual(self.financial.report_calls, ["pA", "pB"])
        # 数字分身（每人至少预测一次）
        self.assertGreaterEqual(len(self.twin.predict_calls), 2)


# ====================================================================== #
# BackgroundScheduler(QObject) 测试（需要 QApplication offscreen）
# ====================================================================== #
class TestBackgroundSchedulerQt(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # 确保 offscreen QApplication 存在（QTimer 实例化需要）
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_test_sched_")
        self.db_path = os.path.join(self.tmpdir, "test_sched.db")
        self.db = Database(self.db_path)
        self.db.init_tables()
        self.db.execute(
            "INSERT INTO persons (id, nickname, is_active) VALUES (:id, :nick, 1)",
            {"id": "p1", "nick": "p1"},
        )

        # 一个极简假 signals：提供 new_message / analysis_ready 信号
        from PySide6.QtCore import QObject, Signal

        class _FakeSignals(QObject):
            new_message = Signal(dict)
            analysis_ready = Signal(dict)

        self.signals = _FakeSignals()

        # 假 config
        class _Cfg:
            def get(self, key, default=None):
                table = {
                    "background.enabled": True,
                    "background.interval_minutes": 5,
                }
                return table.get(key, default)

        self.facade = _make_fake_facade(
            self.db,
            {"config": _Cfg(), "affection": FakeAffection(), "memory": FakeMemory()},
        )

        from app.background_scheduler import BackgroundScheduler
        self.sched = BackgroundScheduler(self.facade, self.signals)

    def tearDown(self):
        try:
            self.sched.stop()
        except Exception:
            pass
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_start_stop(self):
        """start/stop：is_running 状态正确翻转。"""
        self.assertFalse(self.sched.is_running())
        self.sched.start()
        self.assertTrue(self.sched.is_running())
        self.sched.stop()
        self.assertFalse(self.sched.is_running())

    def test_status_dict(self):
        """get_status 返回完整状态字段。"""
        st = self.sched.get_status()
        self.assertIn("enabled", st)
        self.assertIn("interval_ms", st)
        self.assertIn("timer_active", st)
        self.assertIn("worker_running", st)
        self.assertIn("last_run", st)
        self.assertTrue(st["enabled"])
        self.assertEqual(st["interval_ms"], 5 * 60 * 1000)

    def test_trigger_now_runs_in_daemon_thread(self):
        """trigger_now：在 daemon 线程跑一轮，不阻塞调用线程。"""
        self.sched.start()
        self.sched.trigger_now()
        # 等 daemon 线程跑完
        for _ in range(50):
            if not self.sched.updater.is_running:
                break
            time.sleep(0.05)
        self.assertFalse(self.sched.updater.is_running)
        # 至少跑过一轮（last_run 有记录）
        self.assertIn("persons", self.sched.updater.last_run_stats)


if __name__ == "__main__":
    unittest.main(verbosity=2)
