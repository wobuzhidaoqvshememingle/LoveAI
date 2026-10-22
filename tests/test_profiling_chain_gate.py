# -*- coding: utf-8 -*-
"""v3.2.0 测写凭证链门禁测试（Chain-of-Credentials Gate）。

覆盖：
  A. 端到端：构造含真实对话的 messages 跑 run_profiling_chain，每层 evidence_count/qualified/pending。
  B. 钱袋子测试：篡改/删除 S2_QUALIFY 凭证，单独调 S3_RECONCILE，
     断言它函数体不执行、raise ChainBreakError 且消息里写明缺 S2 凭证。
  C. 薄样本：喂极少消息，不足层出现在 unmet_layers 且带"缺 N 条证据"，不被 summary 句填满。
  D. 回归：由外层 pytest 命令统一跑 test_startup_probe / test_free_router_kernel /
     test_six_layer_persona。
"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.profiling_chain import (  # noqa: E402
    ChainBreakError, ChainContext, run_profiling_chain,
    s0_ingest, s1_extract, s2_qualify, s3_reconcile, s4_assemble,
)
from services.six_layer_persona import Layer, EVIDENCE_THRESHOLD  # noqa: E402


# ------------------------------------------------------------------ #
# Fixture：一份"内容丰富"的对话，覆盖六层关键词
# ------------------------------------------------------------------ #
RICH_MESSAGES = [
    {"content": "哈哈哈哈今天开心死了，我们去吃火锅吧😀", "direction": "sent"},
    {"content": "好呀好呀，我来找你，我来接你下班", "direction": "sent"},
    {"content": "我答应你，周末一定陪你，说话算话", "direction": "received"},
    {"content": "别管我，我想静静，需要空间", "direction": "received"},
    {"content": "我相信你，没关系，我理解你忙", "direction": "sent"},
    {"content": "已读不回真的很让我难过委屈", "direction": "received"},
    {"content": "我们 AA 制吧，花钱大手大脚不行", "direction": "sent"},
    {"content": "出轨是底线，赌博家暴黄赌毒绝对不行", "direction": "received"},
    {"content": "我爸妈从小对我很严，原生家庭压力大", "direction": "received"},
    {"content": "我总是觉得自己配不上，讨好型人格", "direction": "sent"},
    {"content": "又一次重复了同样的关系剧本", "direction": "received"},
    {"content": "下次再说吧，临时有事去不了", "direction": "received"},
    {"content": "生气了，火大，你怎么又不回消息", "direction": "received"},
    {"content": "秒回你，多陪陪你，不要离开我", "direction": "sent"},
    {"content": "我来买单，省钱是美德，借钱要还", "direction": "sent"},
    {"content": "忠诚很重要，边界感要有，暧昧对象不行", "direction": "received"},
    {"content": "原生家庭单亲，父母离异，从小跟妈过", "direction": "received"},
    {"content": "自卑，觉得自己没用，自我价值感低", "direction": "sent"},
    {"content": "总是遇到同样的人，重复的模式", "direction": "received"},
    {"content": "开心幸福美滋滋，满足甜蜜", "direction": "sent"},
]

# 薄样本：只有 2 条，远不足 6 层 × 3 条
THIN_MESSAGES = [
    {"content": "哈哈", "direction": "sent"},
    {"content": "好", "direction": "received"},
]


class TestProfilingChainE2E(unittest.TestCase):
    """A. 端到端：富样本跑完整链，每层有真实证据，无模板句。"""

    def test_chain_runs_full_and_no_template_evidence(self):
        res = run_profiling_chain("p_e2e", RICH_MESSAGES)
        self.assertTrue(res["ok"], f"链应跑通，实际 broken_at={res['broken_at']} reason={res['reason']}")
        # 凭证链 5 步全部 qualified
        for sid in ("S0_INGEST", "S1_EXTRACT", "S2_QUALIFY", "S3_RECONCILE", "S4_ASSEMBLE"):
            self.assertTrue(res["credentials"].get(sid), f"{sid} 凭证不合格")
        summary = res["summary"]
        self.assertIsNotNone(summary)
        per_layer = summary["per_layer"]
        # 硬断言：任何层的 top_quotes 里不许出现"画像摘要："模板句
        for layer_name, info in per_layer.items():
            for q in info.get("top_quotes", []):
                self.assertFalse(q.startswith("画像摘要："),
                                 f"{layer_name} 出现模板句假兜底: {q}")
        # 打印每层证据数（报告要用）
        print("\n[A] 端到端每层 evidence_count:")
        for layer in Layer:
            info = per_layer[layer.value]
            print(f"    {layer.value}: count={info['evidence_count']} status={info['status']}")

    def test_thin_sample_reports_unmet_not_filled(self):
        """C. 薄样本：不足层进 unmet_layers 且带'缺 N 条证据'，不被 summary 句填满。"""
        res = run_profiling_chain("p_thin", THIN_MESSAGES)
        self.assertTrue(res["ok"], "薄样本链本身不应断")
        unmet = res["unmet_layers"]
        self.assertGreater(len(unmet), 0, "薄样本必须有未达标层")
        for u in unmet:
            self.assertIn("缺", u["reason"])
            self.assertIn("证据", u["reason"])
            self.assertLess(u["evidence_count"], EVIDENCE_THRESHOLD)
        # 硬断言：summary 的 top_quotes 里不许有"画像摘要："
        per_layer = res["summary"]["per_layer"]
        for layer_name, info in per_layer.items():
            for q in info.get("top_quotes", []):
                self.assertFalse(q.startswith("画像摘要："),
                                 f"薄样本 {layer_name} 被模板句填满: {q}")
        print("\n[C] 薄样本 unmet_layers:")
        for u in unmet:
            print(f"    {u['layer']}: count={u['evidence_count']} reason={u['reason']}")


class TestCredentialGate(unittest.TestCase):
    """B. 钱袋子：篡改/删除 S2 凭证，S3 函数体一行都不许执行。"""

    def _build_valid_up_to_s2(self) -> ChainContext:
        """正常跑完 S0→S1→S2，返回 ctx。"""
        ctx = ChainContext(person_id="p_gate", messages=RICH_MESSAGES)
        s0_ingest(ctx)
        s1_extract(ctx)
        s2_qualify(ctx)
        return ctx

    def test_s3_raises_when_s2_deleted(self):
        """删掉 S2_QUALIFY 凭证 → S3 必须 raise ChainBreakError。"""
        ctx = self._build_valid_up_to_s2()
        # 人为删除 S2 凭证
        del ctx.credentials["S2_QUALIFY"]
        with self.assertRaises(ChainBreakError) as cm:
            s3_reconcile(ctx)
        self.assertIn("S2_QUALIFY", str(cm.exception))
        print(f"\n[B-删除] S3 raise: {cm.exception}")

    def test_s3_raises_when_s2_artifact_tampered(self):
        """篡改 S2_QUALIFY.artifact（但凭证对象还在）→ hash 对不上 → S3 必须 raise。"""
        ctx = self._build_valid_up_to_s2()
        # 人为篡改 artifact（改成完全不同的内容），但不改 artifact_hash
        ctx.credentials["S2_QUALIFY"].artifact = {
            "qualified_layers": ["L1_SURFACE"],  # 假数据
            "pending_layers": [],
        }
        with self.assertRaises(ChainBreakError) as cm:
            s3_reconcile(ctx)
        msg = str(cm.exception)
        self.assertIn("S2_QUALIFY", msg)
        self.assertIn("hash", msg.lower())
        print(f"\n[B-篡改] S3 raise: {msg}")

    def test_s3_does_not_mutate_profile_when_blocked(self):
        """被门禁挡住时，profile 证据数必须不变（函数体没执行）。"""
        ctx = self._build_valid_up_to_s2()
        before = {l.value: len(ctx.profile.evidence[l]) for l in Layer}
        del ctx.credentials["S2_QUALIFY"]
        try:
            s3_reconcile(ctx)
        except ChainBreakError:
            pass
        after = {l.value: len(ctx.profile.evidence[l]) for l in Layer}
        self.assertEqual(before, after, "S3 被门禁挡住后不许动 profile")

    def test_s4_raises_when_s3_deleted(self):
        """S3 也可以被独立验证：删 S3 凭证 → S4 必须 raise。"""
        ctx = self._build_valid_up_to_s2()
        s3_reconcile(ctx)  # 正常跑 S3
        del ctx.credentials["S3_RECONCILE"]
        with self.assertRaises(ChainBreakError) as cm:
            s4_assemble(ctx)
        self.assertIn("S3_RECONCILE", str(cm.exception))
        print(f"\n[B-删除S3] S4 raise: {cm.exception}")

    def test_empty_messages_breaks_at_s0(self):
        """空消息列表 → S0 qualified=False → S1 立刻断。"""
        res = run_profiling_chain("p_empty", [])
        self.assertFalse(res["ok"])
        self.assertEqual(res["broken_at"], "s1_extract")
        print(f"\n[B-空输入] broken_at={res['broken_at']} reason={res['reason']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
