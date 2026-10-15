# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 本地 AI 反哺训练系统单元测试

覆盖：
  - TrainingDataCollector：收集交互 / 去重 / 导出 alpaca / 导出 sharegpt / 统计 / 清空
  - TrainingEngine：训练状态管理 / 参数默认值 / 资源与依赖预检（无 GPU 优雅降级）/
                    自动触发阈值检查 / 取消训练 / 风格相似度评估 / 旧方法向后兼容
  - ModelVersionManager：版本创建 / 列表 / 回滚 / 删除保护 / 热加载 / 版本对比

所有测试均不依赖真实 GPU 与真实模型；torch/peft 未安装时走优雅降级分支，
需要真实训练线程的用例通过 monkeypatch _preflight / _fit_lora 模拟。
"""
import os
import sys
import time
import json
import shutil
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.training_data_collector import (  # noqa: E402
    TrainingDataCollector, FEEDBACK_USED, FEEDBACK_MODIFIED,
    FEEDBACK_REJECTED, FEEDBACK_CORRECTED, VALID_FEEDBACKS,
)
from services.model_version_manager import ModelVersionManager  # noqa: E402
from services.training_engine import (  # noqa: E402
    TrainingEngine, STATE_IDLE, STATE_RUNNING, STATE_COMPLETED, STATE_FAILED,
    _TRAIN_DEPS_AVAILABLE,
)


class _TmpDirMixin:
    """每个用例一个临时目录，避免污染项目真实 data/ models/。"""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="loveai_train_test_")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


# ================================================================== #
# TrainingDataCollector
# ================================================================== #
class TestTrainingDataCollector(_TmpDirMixin, unittest.TestCase):

    def _make(self):
        return TrainingDataCollector(
            data_dir=os.path.join(self._tmp, "training_data"))

    def test_collect_interaction_records_required_fields(self):
        c = self._make()
        r = c.collect_interaction(
            user_msg="在干嘛呢", ai_reply="刚忙完，你呢",
            feedback_type=FEEDBACK_USED,
            metadata={"person_id": "p1", "conversation_id": "c1", "source": "test"})
        self.assertTrue(r["ok"])
        rec = r["record"]
        for field in ("id", "timestamp", "user_message", "ai_reply",
                      "feedback", "conversation_id", "person_id", "source"):
            self.assertIn(field, rec, f"缺少字段 {field}")
        self.assertEqual(rec["feedback"], FEEDBACK_USED)
        self.assertEqual(rec["person_id"], "p1")

    def test_feedback_type_enum_complete(self):
        self.assertEqual(VALID_FEEDBACKS,
                         ("used", "modified", "rejected", "corrected"))

    def test_dedup(self):
        c = self._make()
        kw = dict(user_msg="你好", ai_reply="你好呀",
                  metadata={"person_id": "p1"})
        r1 = c.collect_interaction(feedback_type=FEEDBACK_USED, **kw)
        r2 = c.collect_interaction(feedback_type=FEEDBACK_USED, **kw)
        self.assertTrue(r1["ok"])
        self.assertFalse(r2["ok"])
        self.assertTrue(r2["duplicated"])
        self.assertEqual(c.get_stats()["total"], 1)

    def test_invalid_messages_skipped(self):
        c = self._make()
        r = c.collect_interaction(user_msg="a", ai_reply="b",  # 过短（<2 可见字符）
                                  feedback_type=FEEDBACK_USED)
        self.assertFalse(r["ok"])
        self.assertTrue(r["skipped"])

    def test_modified_uses_modified_reply(self):
        c = self._make()
        r = c.collect_interaction(
            user_msg="晚上吃什么", ai_reply="吃火锅吧",
            feedback_type=FEEDBACK_MODIFIED,
            metadata={"modified_reply": "今晚吃粥吧，清淡点"})
        rec = r["record"]
        self.assertEqual(rec["modified_reply"], "今晚吃粥吧，清淡点")

    def test_export_alpaca_format(self):
        c = self._make()
        c.collect_interaction("问题A", "答案A", FEEDBACK_USED)
        c.collect_interaction("问题B", "答案B-原始", FEEDBACK_MODIFIED,
                              metadata={"modified_reply": "答案B-改后"})
        c.collect_interaction("问题C", "答案C", FEEDBACK_REJECTED)  # 负样本不导出
        path = c.export_dataset("alpaca")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 2)  # rejected 不导出
        self.assertEqual(data[0], {"instruction": "问题A", "input": "", "output": "答案A"})
        self.assertEqual(data[1]["output"], "答案B-改后")  # modified 取改后

    def test_export_sharegpt_format(self):
        c = self._make()
        c.collect_interaction("你好", "你好呀", FEEDBACK_USED)
        path = c.export_dataset("sharegpt")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)
        convs = data[0]["conversations"]
        self.assertEqual(convs[0]["role"], "user")
        self.assertEqual(convs[0]["content"], "你好")
        self.assertEqual(convs[1]["role"], "assistant")
        self.assertEqual(convs[1]["content"], "你好呀")

    def test_get_stats(self):
        c = self._make()
        c.collect_interaction("u1", "a1", FEEDBACK_USED)
        c.collect_interaction("u2", "a2", FEEDBACK_REJECTED)
        s = c.get_stats()
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["positive_samples"], 1)  # used 正样本
        self.assertEqual(s["by_feedback"]["used"], 1)
        self.assertEqual(s["by_feedback"]["rejected"], 1)

    def test_clear_data(self):
        c = self._make()
        c.collect_interaction("u1", "a1", FEEDBACK_USED)
        r = c.clear_data()
        self.assertTrue(r["ok"])
        self.assertEqual(r["deleted"], 1)
        self.assertEqual(c.get_stats()["total"], 0)


# ================================================================== #
# TrainingEngine
# ================================================================== #
class TestTrainingEngine(_TmpDirMixin, unittest.TestCase):

    def _make_collector(self):
        return TrainingDataCollector(
            data_dir=os.path.join(self._tmp, "training_data"))

    def _make_versions(self):
        return ModelVersionManager(
            adapters_dir=os.path.join(self._tmp, "lora_adapters"))

    def test_initial_state_idle(self):
        e = TrainingEngine()
        self.assertEqual(e.get_training_status()["state"], STATE_IDLE)

    def test_default_training_params(self):
        e = TrainingEngine()
        cfg = e._get_train_config()
        self.assertFalse(cfg["enabled"])           # 默认关闭
        self.assertEqual(cfg["learning_rate"], 1e-4)
        self.assertEqual(cfg["batch_size"], 4)
        self.assertEqual(cfg["num_epochs"], 3)
        self.assertEqual(cfg["lora_r"], 8)
        self.assertEqual(cfg["lora_alpha"], 16)
        self.assertEqual(cfg["auto_threshold"], 500)
        self.assertEqual(cfg["frequency"], "manual")

    def test_start_without_local_model_returns_hint(self):
        """未配置本地模型 -> 明确提示，不 crash。"""
        e = TrainingEngine()
        r = e.start_training()  # 无 local_model_path
        self.assertFalse(r["ok"])
        self.assertIn("未配置本地模型", r["reason"])

    def test_start_without_deps_returns_hint(self):
        """配了本地模型但没装 torch/peft -> 明确提示（优雅降级）。"""
        if _TRAIN_DEPS_AVAILABLE:
            self.skipTest("本环境已安装训练依赖，跳过降级分支")
        e = TrainingEngine()
        r = e.start_training({"local_model_path": "/fake/model"})
        self.assertFalse(r["ok"])
        self.assertIn("依赖", r["reason"])
        self.assertEqual(e.get_training_status()["state"], STATE_FAILED)

    def test_auto_trigger_disabled_by_default(self):
        coll = self._make_collector()
        e = TrainingEngine(data_collector=coll)
        # 开关默认关，即使堆数据也不触发
        for i in range(10):
            coll.collect_interaction(f"u{i}", f"a{i}", FEEDBACK_USED)
        r = e.check_auto_trigger()
        self.assertFalse(r["triggered"])
        self.assertIn("未开启", r["reason"])

    def test_auto_trigger_threshold(self):
        coll = self._make_collector()
        e = TrainingEngine(config={"training": {"enabled": True,
                                                "frequency": "manual",
                                                "auto_threshold": 3}},
                           data_collector=coll)
        # 不足阈值
        coll.collect_interaction("u1", "a1", FEEDBACK_USED)
        r = e.check_auto_trigger()
        self.assertFalse(r["triggered"])
        # 达到阈值但 manual 模式 -> 不自动触发
        coll.collect_interaction("u2", "a2", FEEDBACK_USED)
        coll.collect_interaction("u3", "a3", FEEDBACK_USED)
        r = e.check_auto_trigger()
        self.assertFalse(r["triggered"])
        self.assertIn("手动", r["reason"])
        # 切到 daily 模式 -> 触发
        e._set_train_config("frequency", "daily")
        r = e.check_auto_trigger()
        self.assertTrue(r["triggered"])

    def _wait_state(self, engine, target, timeout=8.0):
        """轮询等待训练状态到达 target。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if engine.get_training_status()["state"] == target:
                return True
            time.sleep(0.05)
        return False

    def test_cancel_running_training(self):
        """monkeypatch 真实训练为可取消的假训练，验证状态机与取消。"""
        coll = self._make_collector()
        versions = self._make_versions()
        e = TrainingEngine(data_collector=coll, version_manager=versions)

        # 绕过预检（无 torch），直接进线程
        e._preflight = lambda cfg: (True, "")

        def fake_fit(train_cfg, progress_cb):
            for i in range(100):
                if e._cancel_event.is_set():
                    break
                progress_cb(i, 1.0 - i * 0.01, 60.0)
                time.sleep(0.02)
            return {"ok": True, "final_loss": 0.1, "data_count": 10, "test_set": []}

        e._fit_lora = fake_fit  # type: ignore

        r = e.start_training({"local_model_path": "/fake/model"})
        self.assertTrue(r["ok"])
        self.assertTrue(self._wait_state(e, STATE_RUNNING, timeout=3))

        # 取消
        c = e.cancel_training()
        self.assertTrue(c["ok"])
        # 回到 idle（已取消）
        self.assertTrue(self._wait_state(e, STATE_IDLE, timeout=5))
        st = e.get_training_status()
        self.assertIn("取消", st["message"])

    def test_completed_training_registers_version(self):
        """让假训练跑完，验证完成态 + 版本登记 + 状态。"""
        coll = self._make_collector()
        versions = self._make_versions()
        e = TrainingEngine(data_collector=coll, version_manager=versions)
        e._preflight = lambda cfg: (True, "")

        def fake_fit(train_cfg, progress_cb):
            for i in range(10):
                progress_cb((i + 1) * 10, 0.9 - i * 0.08, None)
                time.sleep(0.005)
            return {"ok": True, "final_loss": 0.123, "data_count": 42,
                    "test_set": [{"reference": "今晚吃粥吧", "generated": "今晚吃粥吧"}] * 5}

        e._fit_lora = fake_fit  # type: ignore
        r = e.start_training({"local_model_path": "/fake/model"})
        self.assertTrue(r["ok"])
        self.assertTrue(self._wait_state(e, STATE_COMPLETED, timeout=5))

        st = e.get_training_status()
        self.assertEqual(st["state"], STATE_COMPLETED)
        self.assertEqual(st["progress"], 100)
        # 版本已登记
        vlist = versions.list_versions()
        self.assertEqual(len(vlist), 1)
        self.assertEqual(vlist[0]["training_data_count"], 42)
        self.assertEqual(vlist[0]["final_loss"], 0.123)

    def test_evaluate_style_similarity_no_testset_rollback(self):
        e = TrainingEngine()
        r = e.evaluate_style_similarity([])
        self.assertEqual(r["total_score"], 0.0)
        self.assertEqual(r["recommended_action"], "rollback")

    def test_evaluate_style_similarity_high_score(self):
        e = TrainingEngine()
        # 高度一致的样本 -> 高分，不建议回滚
        test_set = [
            {"reference": "在干嘛呢，吃了吗", "generated": "在干嘛呢，吃了吗呀"},
            {"reference": "哈哈哈哈笑死我了", "generated": "哈哈哈哈笑死我了哈"},
        ]
        r = e.evaluate_style_similarity(test_set)
        self.assertGreater(r["total_score"], 60)
        self.assertIsNone(r["recommended_action"])

    def test_backward_compat_old_methods(self):
        e = TrainingEngine()
        # learn_from_chat
        r = e.learn_from_chat([], {"target_sentiment": "positive", "strategy": "s1"})
        self.assertAlmostEqual(r["weight"], 0.6, places=2)
        # ab_learning
        r = e.ab_learning("A", ["A", "B"], "positive")
        self.assertTrue(r["success"])
        # generate_scenario
        self.assertEqual(e.generate_scenario("表白")["type"], "表白")
        # score_user_reply
        sc = e.score_user_reply({}, "你在干嘛呢？我们聊聊吧")
        self.assertGreater(sc["score"], 50)
        # get_personal_growth
        self.assertEqual(e.get_personal_growth("p1")["level"], 1)
        # get_training_stats 仍含旧键
        st = e.get_training_stats()
        self.assertIn("learn_count", st)
        self.assertIn("ab_count", st)


# ================================================================== #
# ModelVersionManager
# ================================================================== #
class TestModelVersionManager(_TmpDirMixin, unittest.TestCase):

    def _make(self):
        return ModelVersionManager(
            adapters_dir=os.path.join(self._tmp, "lora_adapters"),
            base_model_path="/fake/base")

    def test_create_and_list_version(self):
        m = self._make()
        v = m.create_version({"training_data_count": 100, "final_loss": 0.5,
                              "style_score": 70.0, "config": {"lr": 1e-4}})
        self.assertEqual(v["version_id"], "v1")
        lst = m.list_versions()
        self.assertEqual(len(lst), 1)
        # 新建后默认切到新版本
        self.assertEqual(m.get_current_version()["version_id"], "v1")

    def test_auto_increment_version_id(self):
        m = self._make()
        v1 = m.create_version({"training_data_count": 10})
        v2 = m.create_version({"training_data_count": 20})
        self.assertEqual(v1["version_id"], "v1")
        self.assertEqual(v2["version_id"], "v2")
        self.assertEqual(len(m.list_versions()), 2)

    def test_rollback(self):
        m = self._make()
        m.create_version({})  # v1
        m.create_version({})  # v2，当前=v2
        r = m.rollback("v1")
        self.assertTrue(r["ok"])
        self.assertEqual(m.get_current_version()["version_id"], "v1")

    def test_delete_current_protected(self):
        m = self._make()
        m.create_version({})  # v1，当前
        r = m.delete_version("v1")
        self.assertFalse(r["ok"])
        self.assertIn("当前", r["reason"])
        self.assertEqual(len(m.list_versions()), 1)  # 没删掉

    def test_delete_non_current(self):
        m = self._make()
        m.create_version({})  # v1 当前
        m.create_version({})  # v2 当前
        # 现在当前是 v2，删 v1 应成功
        r = m.delete_version("v1")
        self.assertTrue(r["ok"])
        self.assertEqual(len(m.list_versions()), 1)
        self.assertEqual(m.get_current_version()["version_id"], "v2")

    def test_load_version_graceful_without_peft(self):
        m = self._make()
        m.create_version({})  # v1
        r = m.load_version("v1")
        # 无 peft 时也应 ok=True，仅记录版本，不崩溃
        self.assertTrue(r["ok"])

    def test_compare_versions(self):
        m = self._make()
        m.create_version({"training_data_count": 100, "final_loss": 0.8, "style_score": 60})
        m.create_version({"training_data_count": 300, "final_loss": 0.4, "style_score": 85})
        cmp = m.compare_versions("v1", "v2")
        self.assertTrue(cmp["ok"])
        self.assertEqual(cmp["training_data_count"]["delta"], 200)
        self.assertEqual(cmp["final_loss"]["delta"], -0.4)
        self.assertEqual(cmp["style_score"]["delta"], 25)

    def test_compare_nonexistent(self):
        m = self._make()
        m.create_version({})
        cmp = m.compare_versions("v1", "vX")
        self.assertFalse(cmp["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
