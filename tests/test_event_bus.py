# -*- coding: utf-8 -*-
"""同步事件总线测试：多订阅者同时触发、链式传播、跨模块传播、循环保护。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.event_bus import EventBus, AFFECTION_CHANGED, STAGE_CHANGED, \
    TASKS_COMPLETED, FINANCIAL_UPDATED, LOSS_AVERSION_UPDATED, STRATEGY_CHANGED, \
    EMOTION_UPDATED


class TestEventBus(unittest.TestCase):
    def test_multi_subscribers_fired_simultaneously(self):
        """emotion_updated → 策略/好感度/分身/画像 多订阅者同时被触发。"""
        bus = EventBus()
        log = []
        for mod in ("strategy", "affection", "twin", "profile"):
            bus.subscribe(EMOTION_UPDATED, lambda d, m=mod: log.append(m))
        bus.reset_pass()
        bus.publish(EMOTION_UPDATED, {"emotion": "愤怒"})
        self.assertEqual(set(log), {"strategy", "affection", "twin", "profile"})

    def test_chain_propagation(self):
        """任务完成→好感度上升→阶段推进→新任务（链式）。"""
        bus = EventBus()
        events = []
        # tasks_completed → 好感度上升 → publish affection_changed
        bus.subscribe(TASKS_COMPLETED, lambda d: (events.append("affect_up"),
                        bus.publish(AFFECTION_CHANGED, {"total": 72})))
        # affection_changed → 阶段推进 → publish stage_changed
        bus.subscribe(AFFECTION_CHANGED, lambda d: (events.append("stage_up"),
                        bus.publish(STAGE_CHANGED, {"stage": 7})))
        bus.subscribe(STAGE_CHANGED, lambda d: events.append("new_task"))
        bus.reset_pass()
        bus.publish(TASKS_COMPLETED, {"task": "约见成功"})
        self.assertIn("affect_up", events)
        self.assertIn("stage_up", events)
        self.assertIn("new_task", events)

    def test_cross_module_finance_to_strategy(self):
        """财务→损失厌恶→策略→任务 跨模块链式传播。"""
        bus = EventBus()
        seq = []
        bus.subscribe(FINANCIAL_UPDATED, lambda d: (seq.append("loss_recalc"),
                        bus.publish(LOSS_AVERSION_UPDATED, {"loss_index": 30})))
        bus.subscribe(LOSS_AVERSION_UPDATED, lambda d: (seq.append("strategy_adjust"),
                        bus.publish(STRATEGY_CHANGED, {"style": "warm_water"})))
        bus.subscribe(STRATEGY_CHANGED, lambda d: seq.append("task_adjust"))
        bus.reset_pass()
        bus.publish(FINANCIAL_UPDATED, {"imbalance": True})
        self.assertEqual(seq, ["loss_recalc", "strategy_adjust", "task_adjust"])

    def test_dedup_same_event_in_one_pass(self):
        """同一事件同一轮不重复触发。"""
        bus = EventBus()
        n = []
        bus.subscribe(AFFECTION_CHANGED, lambda d: n.append(1))
        bus.reset_pass()
        bus.publish(AFFECTION_CHANGED, {})
        bus.publish(AFFECTION_CHANGED, {})  # 第二次应被去重
        self.assertEqual(len(n), 1)

    def test_loop_protection(self):
        """A↔B 乒乓不会无限循环：深度上限截断。"""
        bus = EventBus(max_depth=3)
        count = {"ab": 0}
        # A publish B ; B publish A —— 会互相触发，直到深度截断
        bus.subscribe(AFFECTION_CHANGED, lambda d: (count.__setitem__("ab", count["ab"]+1),
                        bus.publish(STAGE_CHANGED, {"loop": True})))
        bus.subscribe(STAGE_CHANGED, lambda d: bus.publish(AFFECTION_CHANGED, {"loop": True}))
        bus.reset_pass()
        bus.publish(AFFECTION_CHANGED, {})
        # 深度截断后应远小于无限自增（有限值）
        self.assertLess(count["ab"], 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
