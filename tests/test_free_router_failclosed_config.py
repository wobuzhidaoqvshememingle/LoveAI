# -*- coding: utf-8 -*-
"""R3.6 → R3.7 fail-closed：**显式 config** 的付费模型不许进候选池。

背景（对抗性复核找到的真缺陷）：`_models_for()` 的显式 config 分支原先直接
``split`` 返回、不过价格门，于是把 `openai/gpt-4o` 写进 ``llm.free.openrouter_models``
就能让付费模型真的进 ``candidates()`` / ``ranked()``。

R3.6 的做法是「只有**正面付费证据**才剔除，证据不足则放行」。
**R3.7 把口径翻转成硬闸（fail-closed）**：候选要进池必须满足
(A) 内置本机/零配置直通源 / (B) 正面免费证据（目录五维全 0、或 id 以 ``:free`` 结尾）
/ (C) 用户显式白名单（``llm.free.trusted_models``）三者之一；**其余一律拒绝**。
正面付费证据仍然最高优先级，白名单也不得翻案。

本文件覆盖五条：
  0. 两个定价判据本身的边界（正面付费 vs 严格免费，行为未变）；
  1. 有**正面证据**时剔除，并给出「改前会进 / 改后不进」的对照证据；
  2. **没有证据 → 默认拒绝**（这是本次有意的行为变更；改前那三条断言「放行」的
     用例已随语义更新，详见文件末尾的行为变更说明）；
  3. 目录失败后，已取得的正面证据继续生效（不因断网而放行付费项）；
  4. 兜底：全部配置项都被判付费 → 报空池（fail-closed），绝不改道付费。

全部离线：出网函数 `http_get` 用假实现注入，端口一个都不开。

—— R3.7 行为变更说明（本文件被改动的用例，逐个列出）——
  · ``test_missing_or_unparsable_is_neither``：只改了注释（判据行为未变）。
  · ``test_paid_config_item_is_rejected_from_candidates``：断言集合从
    ``{vendor/zero-model, foo/unknown, bar/weird}`` 收窄为 ``{vendor/zero-model}``
    —— 后两者在新语义下是「无证据 → 默认拒绝」。
  · ``test_only_positive_evidence_is_rejected_not_absent_from_list``
    → ``test_absent_or_unparsable_is_default_denied_and_whitelist_rescues``：
    原用例断言「无证据必须放行」，与新语义直接冲突，改为断言「默认拒绝 + 白名单可救回」。
  · ``test_control_evidence_before_the_fix_it_would_have_entered``
    → ``test_control_same_config_is_now_denied_without_evidence``：
    第二个对照原先证明「断网时付费项会进」，现在反向 —— 断网时连无证据项都进不去。
  · ``TestNoEvidenceStillAllowed``（整类 3 条）→ ``TestDefaultDenyWithoutEvidence``：
    原类名与断言都在编码旧语义（无证据仍放行），改为断言默认拒绝 + 白名单救援。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.free_model_router import (                        # noqa: E402
    TRUSTED_MODELS_KEY,
    FreeModelRouter,
    _proven_paid_pricing,
    _strictly_free_pricing,
)

# 一份「全量目录」假响应：里面既有付费项，也有免费项、不可解析项
FULL_RESPONSE = {
    "data": [
        # 明显付费：prompt / completion 都非 0 → 正面证据
        {"id": "openai/gpt-4o",
         "pricing": {"prompt": "0.0000025", "completion": "0.00001"}},
        # 真免费：五项全 0（含 "0.0" / "0E0" 这类等价写法）
        {"id": "vendor/zero-model",
         "pricing": {"prompt": "0", "completion": "0.0", "request": "0E0"}},
        # pricing 存在但解析不出来 → 证据不足（既非免费证据，也非付费证据）
        {"id": "bar/weird", "pricing": {"prompt": "abc"}},
        # OpenRouter 自带的免费标记
        {"id": "some/model:free", "pricing": {}},
        # 聚合路由本身：始终跳过
        {"id": "openrouter/free", "pricing": {}},
    ]
}

CONFIG_MODELS = "openai/gpt-4o, vendor/zero-model, foo/unknown, bar/weird"


class FakeConfig:
    """点分 get/set（flat 键必须先经 from_flat 搭成嵌套，否则读不到）。"""

    def __init__(self, store=None):
        self.store = dict(store or {})

    @classmethod
    def from_flat(cls, mapping):
        cfg = cls()
        for k, v in dict(mapping or {}).items():
            cfg.set(k, v)
        return cfg

    def get(self, key, default=None):
        value = self.store
        for part in key.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value

    def set(self, key, value):
        parts = key.split(".")
        node = self.store
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value


def _ok_http(url, timeout=None, headers=None, **kw):
    return json.dumps(FULL_RESPONSE)


def _dead_http(url, timeout=None, headers=None, **kw):
    raise OSError("模拟：目录拉取失败（离线）")


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="loveai_r36_")
        # 用一个**文件**当 state_dir 的父路径 → PersistentStateStore 打不开，
        # 自动退化为纯内存态（顺带避免 Windows 下 sqlite 句柄锁住临时目录）
        self._block = os.path.join(self._tmp.name, "blocked")
        with open(self._block, "w", encoding="utf-8") as fh:
            fh.write("x")

    def tearDown(self):
        self._tmp.cleanup()

    def _cfg(self, **extra):
        base = {
            "llm.free.state_dir": self._block,
            "llm.free.enabled": "true",
            "llm.free.auto_catalog": "true",
            "llm.free.nvidia_catalog_md": "false",
            "llm.free.include_ollama": "false",
            "llm.free.auto_discover_bridge": "false",
            "llm.free.local_scan.enabled": "false",
            "llm.free.desktop_ai.enabled": "false",
            "llm.free.zero_config.enabled": "false",
            "llm.free.openrouter_key": "sk-test",
        }
        base.update(extra)
        return FakeConfig.from_flat(base)

    def _router(self, cfg, http):
        return FreeModelRouter(cfg, http_get=http)


# ------------------------------------------------------------------ #
# 0) 两个判据的边界（正面付费 vs 严格免费，别搞混）
# ------------------------------------------------------------------ #
class TestPricingPredicates(unittest.TestCase):

    def test_nonzero_is_proven_paid(self):
        self.assertTrue(_proven_paid_pricing({"prompt": "0.0000025"}))
        self.assertTrue(_proven_paid_pricing({"completion": "1"}))
        self.assertTrue(_proven_paid_pricing({"request": "0.001"}))
        self.assertFalse(_strictly_free_pricing({"prompt": "0.0000025"}))

    def test_zero_is_free_and_not_paid(self):
        zero = {"prompt": "0", "completion": "0.0", "request": "0E0",
                "input_cache_read": 0, "input_cache_write": "0.00"}
        self.assertTrue(_strictly_free_pricing(zero))
        self.assertFalse(_proven_paid_pricing(zero))

    def test_missing_or_unparsable_is_neither(self):
        # 证据不足：既不判免费，也不判付费。
        # R3.7 起这个「两边都 False」意味着**默认拒绝**（改前是显式配置走放行）。
        for bad in ({"prompt": "abc"}, {"prompt": None}, {"prompt": ""},
                    {}, None, "not-a-dict"):
            self.assertFalse(_proven_paid_pricing(bad), bad)
        self.assertFalse(_strictly_free_pricing({"prompt": "abc"}))


# ------------------------------------------------------------------ #
# 1) 正面证据 → 剔除（附「改前会进」的对照）
# ------------------------------------------------------------------ #
class TestProvenPaidRejected(_Base):

    def test_paid_config_item_is_rejected_from_candidates(self):
        cfg = self._cfg(**{"llm.free.openrouter_models": CONFIG_MODELS})
        r = self._router(cfg, _ok_http)

        models = [c.model for c in r.candidates()]
        self.assertNotIn("openai/gpt-4o", models, "正面证明付费的配置项不许进候选池")
        # R3.7：无证据的两项（foo/unknown 不在目录、bar/weird pricing 不可解析）
        # 也进不来 —— 只有拿到「五维全 0」正面免费证据的 vendor/zero-model 进得来
        self.assertEqual(set(models), {"vendor/zero-model"})
        # ranked() 同理
        self.assertNotIn("openai/gpt-4o", [c.model for c in r.ranked()])

    def test_control_same_config_is_now_denied_without_evidence(self):
        """「改前会进」的两条对照证据（缺了它，上面那条就不算证明）。"""
        # 对照一：改动前那一行就是裸 split —— 它确实会原样吐出付费模型
        old_behavior = [x.strip() for x in CONFIG_MODELS.split(",") if x.strip()]
        self.assertIn("openai/gpt-4o", old_behavior)

        # 对照二（R3.7 反向对照）：同一份配置，在「拿不到任何证据」的路径下
        # （= 过闸前的等价行为）付费项与无证据项都会作为候选出现；现在则**全被拒**
        # —— 说明拦住它们的是价格门，而不是这些配置项本来就进不去。
        r_no_evidence = self._router(
            self._cfg(**{"llm.free.openrouter_models": CONFIG_MODELS}), _dead_http)
        self.assertEqual([c.model for c in r_no_evidence.candidates()], [])

    def test_reject_logs_one_warning_naming_the_item(self):
        cfg = self._cfg(**{"llm.free.openrouter_models": CONFIG_MODELS})
        r = self._router(cfg, _ok_http)
        with self.assertLogs("services.free_model_router", level="WARNING") as cap:
            r.candidates()
            r.candidates()          # 第二次不该再刷屏
        hits = [m for m in cap.output if "openai/gpt-4o" in m and "已拒绝" in m]
        self.assertEqual(len(hits), 1, "同一条拒绝只该告警一次；实得 %r" % cap.output)
        self.assertIn("fail-closed", hits[0])
        self.assertIn("白名单也不得翻案", hits[0])

    def test_absent_or_unparsable_is_default_denied_and_whitelist_rescues(self):
        """`foo/unknown`（不在目录里）与 `bar/weird`（pricing 不可解析）：
        R3.7 起**默认拒绝**；用户把它们登记进白名单后可以放行。"""
        cfg = self._cfg(**{"llm.free.openrouter_models": "foo/unknown, bar/weird"})
        r = self._router(cfg, _ok_http)
        self.assertEqual([c.model for c in r.candidates()], [])

        cfg2 = self._cfg(**{"llm.free.openrouter_models": "foo/unknown, bar/weird",
                            TRUSTED_MODELS_KEY: "openrouter:foo/unknown, bar/weird"})
        r2 = self._router(cfg2, _ok_http)
        self.assertEqual(set(c.model for c in r2.candidates()),
                         {"foo/unknown", "bar/weird"})

    def test_dynamic_free_list_still_strictly_free(self):
        """不显式配置时，走动态目录：口径与改动前一致（只收严格免费）。"""
        r = self._router(self._cfg(), _ok_http)
        models = [c.model for c in r.candidates()]
        self.assertEqual(set(models), {"vendor/zero-model", "some/model:free"})

    def test_paid_evidence_survives_later_catalog_failure(self):
        """一次成功抓到证据后目录再断网 → 证据继续生效（不因断网放行付费项）。"""
        cfg = self._cfg(**{"llm.free.openrouter_models": CONFIG_MODELS})
        r = self._router(cfg, _ok_http)
        self.assertNotIn("openai/gpt-4o", [c.model for c in r.candidates()])
        # 断网前：拿到正面免费证据的 vendor/zero-model 在池里
        self.assertIn("vendor/zero-model", [c.model for c in r.candidates()])

        r._http = _dead_http                       # 之后再也拉不到目录
        r.refresh_catalog(force=True)
        self.assertNotIn("openai/gpt-4o", [c.model for c in r.candidates()],
                         "断网不该让已证明付费的配置项复活")
        # 断网后：免费证据沿用，不会把已证明免费的 id 误杀
        self.assertIn("vendor/zero-model", [c.model for c in r.candidates()])

    def test_all_configured_paid_yields_empty_pool(self):
        cfg = self._cfg(**{"llm.free.openrouter_models": "openai/gpt-4o"})
        r = self._router(cfg, _ok_http)
        self.assertEqual(r.candidates(), [])
        with self.assertRaises(RuntimeError) as cm:
            r.chat("你是助手", "你好")
        self.assertIn("免费模型池为空", str(cm.exception))

    def test_evidence_slot_is_per_thread_and_consumed(self):
        """证据槽按线程分（并发刷新不互相覆盖），且用完即清（不涨内存）。"""
        cfg = self._cfg(**{"llm.free.openrouter_models": CONFIG_MODELS})
        r = self._router(cfg, _ok_http)
        r.candidates()
        self.assertNotIn(threading.get_ident(), r._or_paid_ids_scratch)

        problems = []

        def _worker():
            try:
                r.refresh_catalog(force=True)
                if "openai/gpt-4o" in [c.model for c in r.candidates()]:
                    problems.append("并发刷新后付费项进池")
            except Exception as e:  # noqa: BLE001
                problems.append(repr(e))

        t = threading.Thread(target=_worker)
        t.start()
        t.join()
        self.assertEqual(problems, [])
        self.assertNotIn(threading.get_ident(), r._or_paid_ids_scratch)


# ------------------------------------------------------------------ #
# 2) 没有证据 → **默认拒绝**（R3.7 硬闸；白名单是唯一显式逃生门）
# ------------------------------------------------------------------ #
class TestDefaultDenyWithoutEvidence(_Base):

    def test_catalog_failure_yields_no_candidates(self):
        """目录拉取失败 = 一条免费证据都拿不到 → 显式配置的项一条都不放行。"""
        cfg = self._cfg(**{"llm.free.openrouter_models": CONFIG_MODELS})
        r = self._router(cfg, _dead_http)
        self.assertEqual([c.model for c in r.candidates()], [])
        with self.assertRaises(RuntimeError):
            r.chat("你是助手", "你好")

    def test_whitelist_revives_the_same_items_after_catalog_failure(self):
        """同一份配置 + 白名单 → 用户显式声明后即可放行（证明白名单真的生效）。"""
        cfg = self._cfg(**{"llm.free.openrouter_models": CONFIG_MODELS,
                           TRUSTED_MODELS_KEY: "openrouter:*, foo/unknown"})
        r = self._router(cfg, _dead_http)
        self.assertEqual(set(c.model for c in r.candidates()),
                         set(x.strip() for x in CONFIG_MODELS.split(",")))

    def test_nvidia_explicit_branch_needs_whitelist(self):
        """NVIDIA 显式配置同样过这道闸；NVIDIA 目录不提供 pricing →
        无免费证据 → R3.7 起**默认拒绝**，登记白名单后才放行。"""
        cfg = self._cfg(**{"llm.free.nvidia_key": "nvapi-test",
                           "llm.free.nvidia_models": "meta/llama-3.1-70b"})
        r = self._router(cfg, _ok_http)
        self.assertEqual([c.model for c in r.candidates()
                          if c.provider == "nvidia"], [])

        cfg2 = self._cfg(**{"llm.free.nvidia_key": "nvapi-test",
                            "llm.free.nvidia_models": "meta/llama-3.1-70b",
                            TRUSTED_MODELS_KEY: "nvidia:meta/llama-3.1-70b"})
        r2 = self._router(cfg2, _ok_http)
        self.assertEqual([c.model for c in r2.candidates()
                          if c.provider == "nvidia"], ["meta/llama-3.1-70b"])


if __name__ == "__main__":
    unittest.main()
