# -*- coding: utf-8 -*-
"""
LoveAI R3.3-78 - 生产路线分流接线测试。

验证 app.service_facade._update_affection_from_analysis 在收到编排结果时：
  result.data.orchestration.manipulation（analysis_orchestrator 经
  build_counter_manipulation 产出）→ resolve_route → engine.set_route：
    - triggered=True 且有反制策略 → set_route("counter")
    - triggered=False / 无 manipulation → 保持/回到 "guide"

说明：不实例化完整 ServiceFacade（依赖 DB/服务装配过重），改用最小替身
承载该方法实际访问到的成员（_affection_engine / signals / services /
_emit_event），直接调用未绑定方法——与生产路径同一段代码。
"""
import os
import sys
import shutil
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.affection_engine import AffectionEngine  # noqa: E402
from app.service_facade import ServiceFacade  # noqa: E402


class _NoopSignal:
    def emit(self, payload):  # noqa: ANN001
        pass


class _StubSignals:
    def __init__(self) -> None:
        self.affection_changed = _NoopSignal()
        self.loyalty_changed = _NoopSignal()


class _StubFacade:
    """承载 _update_affection_from_analysis 真实访问到的成员。"""

    def __init__(self, engine: AffectionEngine) -> None:
        self._affection_engine = engine
        self.signals = _StubSignals()
        self.services: dict = {}

    def _emit_event(self, *args, **kwargs) -> None:  # noqa: ANN001
        pass


def _manip(triggered: bool) -> dict:
    return {
        "triggered": triggered,
        "manipulation_patterns": [{"key": "gaslighting"}] if triggered else [],
        "counter_strategies": [{"principle": "渐进减量"}] if triggered else [],
    }


class TestRouteProductionWiring(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_routewire_")
        self.db = Database(os.path.join(self.tmpdir, "t.db"))
        self.db.init_tables()
        self.engine = AffectionEngine(self.db)
        self.facade = _StubFacade(self.engine)
        self.pid = "pw1"

    def tearDown(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run(self, result: dict) -> None:
        ServiceFacade._update_affection_from_analysis(
            self.facade, self.pid, "今天聊了操控话题", result)

    def test_01_counter_scenario_sets_route_counter(self):
        """反制场景（triggered=True + 反制策略）→ get_route == counter。"""
        result = {"data": {"orchestration": {"manipulation": _manip(True)}}}
        self._run(result)
        self.assertEqual(self.engine.get_route(self.pid), "counter")

    def test_02_not_triggered_stays_guide(self):
        """triggered=False → 保持 guide。"""
        result = {"data": {"orchestration": {"manipulation": _manip(False)}}}
        self._run(result)
        self.assertEqual(self.engine.get_route(self.pid), "guide")

    def test_03_no_manipulation_block_stays_guide(self):
        """编排结果里没有 manipulation 块 → guide。"""
        result = {"data": {"orchestration": {}}}
        self._run(result)
        self.assertEqual(self.engine.get_route(self.pid), "guide")

    def test_04_flip_counter_then_guide(self):
        """同一对象先检出反制、下一轮分析无反制 → 路线可被翻回 guide。"""
        self._run({"data": {"orchestration": {"manipulation": _manip(True)}}})
        self.assertEqual(self.engine.get_route(self.pid), "counter")
        self._run({"data": {"orchestration": {"manipulation": _manip(False)}}})
        self.assertEqual(self.engine.get_route(self.pid), "guide")

    def test_05_corrupt_result_does_not_raise(self):
        """畸形 result（缺 data）不应抛异常，路线缺省 guide。"""
        self._run({"data": None})
        self.assertEqual(self.engine.get_route(self.pid), "guide")


if __name__ == "__main__":
    unittest.main(verbosity=2)
