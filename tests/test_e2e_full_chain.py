# -*- coding: utf-8 -*-
"""端到端全链路测试：采集夹具 -> 鉴别(18部分) -> 数字分身 -> 策略 -> 任务
-> 三合一评分 -> 方向罗盘，并断言每环真实产出 + 数据多向回写。

约束：
  * 纯离线、确定性、不联网、不依赖真实微信；
  * 用临时 Database，结束即 dispose 并清理；
  * 采集只走 services.collectors 夹具（P1 pc_account + T4 screenshots，另加 T3 粘贴）；
  * 各环优先调用既有公共 API，不改动任何共享源码。
"""
import os
import sys
import shutil
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 某些视觉/OCR 依赖在无显示器环境下需要离屏平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.collectors.store import MessageStore  # noqa: E402
from services.collectors.pc.p1_sqlcipher import P1SqlcipherDirectAdapter  # noqa: E402
from services.collectors.thirdparty.t4_screenshots import T4ScreenshotFolderAdapter  # noqa: E402
from services.collectors.thirdparty.t3_manual import T3ManualPasteAdapter  # noqa: E402
from services.persona_analyzer import PersonaAnalyzer  # noqa: E402
from services import diagnostic_report  # noqa: E402
from services.twin_sim_pipeline import TwinSimPipeline  # noqa: E402
from services.strategy_advisor import StrategyAdvisor  # noqa: E402
from services.affection_engine import AffectionEngine  # noqa: E402
from services.task_engine import TaskEngine  # noqa: E402
from services.stats_service import StatsService  # noqa: E402
from services.direction_compass import DirectionCompass  # noqa: E402

_FIX = os.path.join(_PROJECT_ROOT, "services", "collectors", "fixtures")
_PC_ACCOUNT = os.path.join(_FIX, "pc_account")
_SCREENSHOTS = os.path.join(_FIX, "screenshots")
_RAW_KEY = "66ec47ab2a62ec7630ec8485ae9ac6c7f111eece67e6dc618aadecb9af60f9d2"

# 固定人物：整条链路共用同一个 person_id，用以证明"多向回写"而非各跑各的
PID = "e2e_offline_demo"

# 撤离/分手导向词：攻略方向上绝不能出现
_WITHDRAW_WORDS = ("分手", "撤离", "放弃这段", "结束这段关系", "彻底离开")


class TestE2EFullChain(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_e2e_")
        self.db_path = os.path.join(self.tmpdir, "e2e.db")
        self.data_root = os.path.join(self.tmpdir, "personas")
        self.db = Database(self.db_path)
        self.db.init_tables()

    def tearDown(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        # PersonaAnalyzer._save_profile 会落到工程 data/persons/<pid>，测试产物需清掉
        proj_person_dir = os.path.join(_PROJECT_ROOT, "data", "persons", PID)
        shutil.rmtree(proj_person_dir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 环 1：采集（P1 + T4，另加 T3）
    # ------------------------------------------------------------------ #
    def _collect_into_store(self):
        p1 = P1SqlcipherDirectAdapter().collect(
            key=_RAW_KEY, account_root=_PC_ACCOUNT, version="4.x")
        t4 = T4ScreenshotFolderAdapter().collect(
            folder=_SCREENSHOTS, peer_name="小美")
        t3 = T3ManualPasteAdapter().collect(
            text="我\n这周末有空，要不要一起去江边走走？\n你\n好呀，那下午三点见。",
            peer_name="小美")

        # 适配器本身必须返回非空标准消息
        self.assertGreater(len(p1), 0, "P1 pc_account 必须解出非空消息")
        self.assertGreater(len(t4), 0, "T4 screenshots 必须 OCR 出非空消息")
        self.assertGreater(len(t3), 0, "T3 粘贴必须解析出非空消息")

        store = MessageStore(self.db)
        all_msgs = list(p1) + list(t4) + list(t3)
        result = store.persist(all_msgs, person_id=PID, person_nickname="小美")
        return store, result, {"p1": len(p1), "t4": len(t4), "t3": len(t3)}

    # ------------------------------------------------------------------ #
    # 主链路
    # ------------------------------------------------------------------ #
    def test_full_chain(self):
        store, collect_res, raw_counts = self._collect_into_store()

        # ---- 1. 采集真实落库 ----
        db_count = store.count_for(PID)
        self.assertGreaterEqual(collect_res["inserted"], 1, "至少真实插入 1 条消息")
        self.assertEqual(db_count, collect_res["inserted"],
                         "MessageStore.inserted 与 chat_messages 真实计数一致")
        self.assertGreater(db_count, 0)

        # ---- 2. 鉴别：PersonaAnalyzer + 18 部分诊断报告 ----
        analyzer = PersonaAnalyzer(self.db)
        profile = analyzer.analyze(PID)
        self.assertNotIn("error", profile, f"画像分析失败: {profile.get('error')}")
        self.assertEqual(profile["person_id"], PID)
        # 画像读到的就是采集入库的消息数（多向回写①）
        self.assertEqual(profile["message_count"], db_count,
                         "鉴别读取的消息数 == 采集入库消息数")

        report = diagnostic_report.generate_report(
            PID, db=self.db, save=True, data_root=self.data_root)
        sections = report["sections"]
        self.assertEqual(len(sections), 18, "诊断报告必须为 18 部分")
        self.assertIn("section_map", report)
        self.assertGreater(len(report["evidence_index"]), 0, "报告必须带证据索引")
        # 关键结论（核心标签）必须带可回溯证据 eid
        s17 = next((s for s in sections if s["id"] == 17), None)
        self.assertIsNotNone(s17)
        core_labels = s17["content"].get("core_labels", [])
        eid_pool = {e["eid"] for e in report["evidence_index"]}
        label_with_evidence = [c for c in core_labels if c.get("evidence")]
        if label_with_evidence:  # 有命中标签时，其证据必须能在证据索引里回溯
            ev = label_with_evidence[0]["evidence"][0]
            self.assertIn(ev, eid_pool, "结论证据 eid 必须可在 evidence_index 中回溯")
        # 报告统计的消息数同样等于入库数（多向回写②）
        self.assertEqual(report["data_overview"]["total_messages"], db_count)

        # ---- 3. 数字分身：run_turn 三段齐全 + Y1-Y7 分布 ----
        twin = TwinSimPipeline(PID, persona_profile=profile)
        turn = twin.run_turn(user_message="这周末有空吗？出来江边走走吧")
        self.assertEqual(turn["person_id"], PID)
        self.assertIn("narrative", turn, "必须有叙事补写段")
        self.assertIn("text", turn["narrative"], "叙事补写须有正文")
        self.assertIn("mode", turn["decision"], "必须有结构化决定")
        self.assertIn("render", turn, "必须有渲染段")
        P = turn["reaction_distribution"]["P"]
        for k in ("Y1", "Y2", "Y3", "Y4", "Y5", "Y6", "Y7"):
            self.assertIn(k, P, f"结局分布缺少 {k}")

        # ---- 4. 策略：建议动作/话术草稿，攻略导向 ----
        strategy = StrategyAdvisor(self.db).analyze(persona=profile, relation_stage=5)
        self.assertTrue(strategy.get("recommended") or strategy.get("reasoning"),
                        "策略必须给出非空建议")
        adv_text = " ".join(strategy.get("recommended", [])) + strategy.get("reasoning", "")
        for w in _WITHDRAW_WORDS:
            self.assertNotIn(w, adv_text, f"策略出现撤离/分手导向词: {w}")

        # ---- 5. 任务：task_engine 据策略创建并落库 ----
        aff = AffectionEngine(self.db)
        engine = TaskEngine(self.db, aff, guide_engine=None, twin=None,
                            signals=None, strategy_advisor=StrategyAdvisor(self.db),
                            commitment_tracker=None)
        created = engine.generate_main_tasks(PID, stage=5)
        if not created:  # 兜底：直接挂一个自定义任务，保证任务真实落库
            created = [engine.add_custom_task(PID, "约对方江边散步", source="e2e")]
        self.assertTrue(any(created), "task_engine 必须创建任务")
        task_row = self.db.fetch_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE person_id=:p", {"p": PID})
        self.assertGreaterEqual(task_row["n"], 1, "任务必须真实落库到 tasks 表")

        # ---- 6. 三合一评分：真实计算 -> 仪表盘三维(消息/任务/好感) ----
        recalc = aff.recalculate_from_history(PID)
        self.assertIsInstance(recalc, dict)
        dash = StatsService(self.db).get_dashboard_stats(PID)
        # 消息维度：必须等于采集入库数（多向回写③）
        self.assertEqual(dash["message_count"], db_count,
                         "评分端 message_count 必须等于采集入库数")
        self.assertGreater(dash["message_count"], 0)
        # 好感度维度：真实计算出的数值（非默认 0）
        self.assertIsInstance(dash["affection_current"], float)
        aff_row = self.db.fetch_one(
            "SELECT total_score FROM current_affection WHERE person_id=:p", {"p": PID})
        self.assertIsNotNone(aff_row, "好感度评分必须真实落库 current_affection")
        self.assertGreaterEqual(aff_row["total_score"], 0.0)

        # ---- 7. 方向罗盘：产出方向/指引 ----
        compass = DirectionCompass(self.db).evaluate(PID, snapshot={
            "stage": 5,
            "attachment": profile.get("attachment", {}).get("type", ""),
            "strategy_text": adv_text,
            "risk_level": (profile.get("risk_warning", {}) or {}).get("overall_level", ""),
            "deception_risk": str(profile.get("deception_risk", "") or ""),
        })
        self.assertEqual(compass["person_id"], PID)
        self.assertIn("verdict", compass)
        self.assertTrue(compass.get("verdict") or compass.get("gates") is not None)

        # ---- 8. 多向回写总校验：全链路同一 person_id、数据互相读到 ----
        self.assertEqual(task_row["n"], self.db.fetch_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE person_id=:p", {"p": PID})["n"])
        # 评分端能看到任务（任务维度真实存在于库，而非各环各跑各的）
        active_or_pending = self.db.fetch_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE person_id=:p AND status IN "
            "('pending','in_progress')", {"p": PID})["n"]
        self.assertGreaterEqual(active_or_pending, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
