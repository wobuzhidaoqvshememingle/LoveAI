# -*- coding: utf-8 -*-
"""R3.4 免费路由内核单测。

覆盖（对应任务清单的 7 条）：
  1. 动态惩罚路由：429 +3（封顶 10）/ 成功 -1（下限 0）/ 每 120s 衰减 1；
  2. 滑动窗口限流：60s 滑动边界（跨 60s 半滑）、内存 + SQLite 双写（重启不丢计数）；
  3. 四维限流：RPM / RPD / TPM / TPD 任一触顶即跳过该 Provider；
  4. 429 冷却升级：2min / 10min / 1h / 24h 分级 + 持久化 + 24h 观察窗归零；
  5. Sticky Session：命中、只对多轮生效、30 分钟 TTL、「优先不强制」；
  6. 跳过集合 + 20 次重试预算 + 401/403 不进重试链；
  7. 可观测性：routing_snapshot() 冻结接口 + chat() 的 routed_via。
外加 fail-closed 不变式（免费池空 → 报空池、一个请求都不发）。

全部离线：不联网、不扫本机端口；每个用例把状态目录指到自己的 tmp，不污染 data/。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services import free_router_kernel as K                          # noqa: E402
from services.free_model_router import (                              # noqa: E402
    BACKOFF_MAX_RETRIES,
    STRICT_FREE_COST_KEYS,
    Candidate,
    FreeModelRouter,
    _strictly_free_pricing,
)


# --------------------------------------------------------------------- #
# 假 OpenAI 客户端（**按模型名**分派；记录每次调用 (model, base_url)）
# --------------------------------------------------------------------- #
class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = None


class _HttpErr(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status_code = status


def _seq(*outcomes):
    """按顺序返回结果（_Resp 或 Exception）；用完后重复最后一个。"""
    box = {"i": 0}

    def _handler():
        i = box["i"]
        box["i"] = i + 1
        return outcomes[min(i, len(outcomes) - 1)]
    return _handler


class _FakeOpenAI:
    behavior: dict = {}      # model -> handler() -> _Resp | Exception
    calls: list = []         # [(model, base_url), ...]

    def __init__(self, api_key=None, base_url=None, timeout=None):
        self._base = base_url
        self.chat = self
        self.completions = self

    def create(self, model=None, messages=None, **kwargs):
        # 分派键必须是模型名：同一 Provider 下多个模型共用 base_url
        _FakeOpenAI.calls.append((model, self._base))
        handler = _FakeOpenAI.behavior.get(model)
        if handler is None:
            return _Resp("ok-default")
        out = handler()
        if isinstance(out, Exception):
            raise out
        return out


def _cfg(tmp_state_dir: str, **extra) -> dict:
    """离线单测基线配置：无本地渠道、无动态目录、无零配置渠道、无桥接。"""
    cfg = {
        "llm.free.include_ollama": "false",
        "llm.free.auto_catalog": "false",
        "llm.free.nvidia_catalog_md": "false",
        "llm.free.zero_config.enabled": "false",
        "llm.free.auto_discover_bridge": "false",
        "llm.free.desktop_ai.enabled": "false",
        "llm.free.local_scan.enabled": "false",
        "llm.free.state_dir": tmp_state_dir,
    }
    cfg.update(extra)
    return cfg


class _KernelCase(unittest.TestCase):
    """公共夹具：可控时钟 + 假 openai + 各用例独立状态目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="free_router_kernel_",
                                                ignore_cleanup_errors=True)
        self.t = [1000.0]                      # 可控时钟（改 self.t[0] 即推进时间）
        self._patches = [
            mock.patch.object(FreeModelRouter, "_collect_local_scan_models",
                              lambda self: []),
            mock.patch.object(FreeModelRouter, "_collect_desktop_models",
                              lambda self: []),
            mock.patch("openai.OpenAI", _FakeOpenAI),
        ]
        for p in self._patches:
            p.start()
        _FakeOpenAI.behavior = {}
        _FakeOpenAI.calls = []

    def tearDown(self):
        for p in self._patches:
            p.stop()
        K.close_shared_connections()           # 释放 sqlite 句柄，保证 tmp 能删干净
        self._tmp.cleanup()

    # ---- 构造 ----
    def make(self, **extra) -> FreeModelRouter:
        r = FreeModelRouter(_cfg(self._tmp.name, **extra), clock=lambda: self.t[0])
        r._sleep = lambda seconds: None       # _sleep 是实例属性（可注入），不真睡
        return r

    @staticmethod
    def or_cfg(**extra) -> dict:
        base = {"llm.free.openrouter_key": "sk-test",
                "llm.free.openrouter_models": "model-a:free,model-b:free"}
        base.update(extra)
        return base

    def make_or(self, **extra) -> FreeModelRouter:
        return self.make(**self.or_cfg(**extra))

    def make_or_plus_zero_config(self, **extra) -> FreeModelRouter:
        """openrouter 打头，pollinations / llm7 当后续免费 Provider。

        这里把 pollinations/llm7 的基础优先级显式覆盖成 30/31，让用例不依赖
        kernel 默认表的具体数字。

        用来验证「某 Provider 被四维限流挡住后，整 Provider 被跳过、继续用别的免费
        Provider」——即限流是 Provider 级，不是单通道级。
        """
        return self.make(**self.or_cfg(**{
            "llm.free.zero_config.enabled": "true",
            "llm.free.priority.pollinations": "30",
            "llm.free.priority.llm7": "31",
            **extra}))

    @staticmethod
    def chan_key_of(router: FreeModelRouter, model: str) -> str:
        """假客户端的分派键 = 模型名（并顺带校验该模型确实在候选里）。

        为何不用 base_url：同一 Provider 下 model-a / model-b 共用同一个
        base_url，用 base_url 当键会让两条通道互相覆盖行为定义 —— 测的就不是
        真实路由了。真实出站序列另有 _FakeOpenAI.calls（记 model + base_url）。
        """
        for c in router.candidates():
            if c.model == model:
                return model
        raise AssertionError(f"未找到候选 {model}")

    def chan(self, snap: dict, model: str) -> dict:
        for ch in snap["channels"]:
            if ch["model"] == model:
                return ch
        raise AssertionError(f"快照里没有通道 {model}")


# ===================================================================== #
# 1. 动态惩罚路由
# ===================================================================== #
class TestPenaltyTable(_KernelCase):

    def test_429_raise_success_decay_and_cap(self):
        pen = K.PenaltyTable(clock=lambda: self.t[0])
        self.assertEqual(pen.value("p:m"), 0.0)
        self.assertEqual(pen.on_429("p:m"), 3.0)          # 一次 429 → +3
        self.assertEqual(pen.on_429("p:m"), 6.0)
        self.assertEqual(pen.on_429("p:m"), 9.0)
        self.assertEqual(pen.on_429("p:m"), 10.0)         # 累计封顶 10
        self.assertEqual(pen.on_429("p:m"), 10.0)

        self.t[0] += 120.0                                 # 每 2 分钟衰减 1
        self.assertEqual(pen.value("p:m"), 9.0)
        self.t[0] += 481.0                                 # 再走 4 个整周期（120×4）
        self.assertEqual(pen.value("p:m"), 5.0)

        self.assertEqual(pen.on_success("p:m"), 4.0)       # 成功 -1
        pen.clear("p:m")
        self.assertEqual(pen.value("p:m"), 0.0)

    def test_success_floor_is_zero(self):
        pen = K.PenaltyTable(clock=lambda: self.t[0])
        self.assertEqual(pen.on_success("p:m"), 0.0)       # 0-1 → 下限 0（回到原位）
        self.assertEqual(pen.on_success("p:m"), 0.0)

    def test_effective_score_is_base_plus_penalty(self):
        self.assertEqual(K.effective_score(20, 3.0), 23.0)
        self.assertEqual(K.effective_score(0, 0.0), 0.0)

    def test_429_penalty_sinks_channel_then_decays_back(self):
        """端到端：429 后该通道排序分变大（自动下沉），衰减后自动回到原位。"""
        r = self.make_or()
        a = self.chan_key_of(r, "model-a:free")
        b = self.chan_key_of(r, "model-b:free")
        # A：先 429 一次，退避后同模型重试成功（**不**进 429 冷却）
        _FakeOpenAI.behavior[a] = _seq(_HttpErr(429), _Resp("a-ok-after-429"))
        _FakeOpenAI.behavior[b] = _seq(_Resp("b-ok"))

        out = r.chat("sys", "user")
        self.assertEqual(out["routed_via"], "openrouter:model-a:free")
        self.assertFalse(r._cd429.is_cooling("openrouter:model-a:free"))

        snap = r.routing_snapshot()
        ch_a = self.chan(snap, "model-a:free")
        # 429 一次 +3，同一次请求退避后重试成功 -1 → 净 +2（两条规则各自生效）
        self.assertEqual(ch_a["penalty"], 2.0)
        self.assertEqual(ch_a["base_priority"], 10)          # openrouter 基础优先级
        self.assertEqual(ch_a["effective_score"], 12.0)      # 10 + 2
        self.assertEqual(self.chan(snap, "model-b:free")["effective_score"], 10.0)

        # 惩罚分把 A 压到 B 之后 → 下次请求自动改走 B（无需手工调 Provider 顺序）。
        # 注：既有 R3.3 有「alive 候选整体排 unknown 之前」的兜底层；A 刚成功已
        # alive、B 还是 unknown，要先让两者同层，惩罚分的排序效果才看得见。
        r._alive["model-b:free"] = True
        self.assertEqual(r.ranked()[0].model, "model-b:free")
        self.assertEqual(r.chat("sys", "user")["routed_via"],
                         "openrouter:model-b:free")

        # 每 2 分钟衰减 1
        self.t[0] += 120.0
        self.assertEqual(self.chan(r.routing_snapshot(), "model-a:free")["penalty"], 1.0)
        # 再衰减足够久 → 惩罚归零 = 回到基础优先级「原位」
        self.t[0] += 1200.0
        ch_a2 = self.chan(r.routing_snapshot(), "model-a:free")
        self.assertEqual(ch_a2["penalty"], 0.0)
        self.assertEqual(ch_a2["effective_score"], float(ch_a2["base_priority"]))

    def test_priority_override_changes_order(self):
        """llm.free.priority.<provider> 覆盖基础优先级，直接改变排序。"""
        # 默认 openrouter(20) 打头；把它覆盖成 77 后，pollinations(30) 应反超
        r = self.make_or_plus_zero_config(**{"llm.free.priority.openrouter": "77"})
        snap = r.routing_snapshot()
        ch_a = self.chan(snap, "model-a:free")
        self.assertEqual(ch_a["base_priority"], 77)
        self.assertEqual(ch_a["effective_score"], 77.0)
        self.assertEqual(snap["channels"][0]["provider"], "pollinations")
        self.assertEqual(r.ranked()[0].provider, "pollinations")


# ===================================================================== #
# 2. 滑动窗口 + SQLite 双写
# ===================================================================== #
class TestSlidingWindow(_KernelCase):

    def test_window_slides_not_fixed(self):
        ledger = K.RateLimitLedger(clock=lambda: self.t[0])
        for _ in range(60):
            ledger.note_request("prov")
        self.assertEqual(ledger.usage("prov")["rpm"], 60)
        self.t[0] = 1059.0                     # +59s：仍在窗口内
        self.assertEqual(ledger.usage("prov")["rpm"], 60)
        self.t[0] = 1060.0                     # +60s：边界仍计入
        self.assertEqual(ledger.usage("prov")["rpm"], 60)
        self.t[0] = 1060.001                   # 越过 60s：整批滑出窗口
        u = ledger.usage("prov")
        self.assertEqual(u["rpm"], 0)
        self.assertEqual(u["rpd"], 60)         # 但 24h 窗口内仍然在

    def test_window_partial_slide(self):
        """半滑动：老事件滑出、新事件仍在 —— 固定窗口做不到这个效果。"""
        ledger = K.RateLimitLedger(clock=lambda: self.t[0])
        ledger.note_request("prov")            # t=1000
        self.t[0] = 1050.0
        ledger.note_request("prov")            # t=1050
        self.assertEqual(ledger.usage("prov")["rpm"], 2)
        self.t[0] = 1061.0                     # t=1000 滑出，t=1050 还在
        self.assertEqual(ledger.usage("prov")["rpm"], 1)

    def test_token_dims_and_future_timestamp_guard(self):
        ledger = K.RateLimitLedger(clock=lambda: self.t[0])
        ev = ledger.note_request("prov")
        ledger.note_tokens(ev, 1234)
        u = ledger.usage("prov")
        self.assertEqual((u["rpm"], u["tpm"], u["tpd"]), (1, 1234, 1234))
        # 未来时间戳（跨进程 / 跨时钟写入）不得污染当前窗口
        self.t[0] = 500.0
        self.assertEqual(ledger.usage("prov")["rpm"], 0)

    def test_sqlite_dual_write_survives_restart(self):
        path = os.path.join(self._tmp.name, "free_router", "router_state.db")
        store1 = K.PersistentStateStore(path)
        self.assertTrue(store1.ok, store1.error)
        ledger1 = K.RateLimitLedger(clock=lambda: self.t[0], store=store1)
        for _ in range(3):
            ledger1.note_request("prov", tokens=7)
        self.assertEqual(ledger1.usage("prov")["rpm"], 3)

        # 确实落了盘：绕开所有内存态直接查 SQLite
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute(
                "SELECT provider, ts, tokens FROM rl_events WHERE provider='prov'"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r[2] == 7 for r in rows))

        # 模拟「进程重启」：内存全空的新台账，仍从 SQLite 回灌出同样计数
        ledger2 = K.RateLimitLedger(clock=lambda: self.t[0],
                                    store=K.PersistentStateStore(path))
        self.assertNotIn("prov", ledger2._events)          # 内存里本来是空的
        self.assertEqual(ledger2.usage("prov")["rpm"], 3)  # 来自落盘计数


# ===================================================================== #
# 3. 四维限流
# ===================================================================== #
class TestFourDimLimit(_KernelCase):

    def test_each_dim_blocks_independently(self):
        cases = [
            ({"rpm": 5}, {"rpm": 4}, None),
            ({"rpm": 5}, {"rpm": 5}, "rpm"),
            ({"rpd": 100}, {"rpd": 99}, None),
            ({"rpd": 100}, {"rpd": 100}, "rpd"),
            ({"tpm": 1000}, {"tpm": 999.9}, None),
            ({"tpm": 1000}, {"tpm": 1000}, "tpm"),
            ({"tpd": 5000}, {"tpd": 4999}, None),
            ({"tpd": 5000}, {"tpd": 5000}, "tpd"),
        ]
        for limits, used, expect in cases:
            spec = K.RateLimitSpec(**{**{"rpm": None, "rpd": None,
                                         "tpm": None, "tpd": None}, **limits})
            got = spec.exceeded({**{"rpm": 0, "rpd": 0, "tpm": 0, "tpd": 0}, **used})
            self.assertEqual(got, expect, f"limits={limits} used={used}")

    def test_unconfigured_provider_is_never_blocked(self):
        limiter = K.RateLimiter(K.RateLimitLedger(clock=lambda: self.t[0]), specs={})
        self.assertIsNone(limiter.blocked_dim("whatever"))
        self.assertTrue(limiter.allow("whatever"))
        self.assertEqual(limiter.configured(), {})

    def test_limiter_uses_ledger_usage(self):
        ledger = K.RateLimitLedger(clock=lambda: self.t[0])
        limiter = K.RateLimiter(ledger,
                                specs={"prov": K.RateLimitSpec(rpm=2, tpd=100)},
                                clock=lambda: self.t[0])
        self.assertTrue(limiter.allow("prov"))
        ledger.note_request("prov")
        ledger.note_request("prov")
        self.assertEqual(limiter.blocked_dim("prov"), "rpm")     # 触顶即挡
        self.assertFalse(limiter.allow("prov"))
        rep = limiter.report("prov")
        self.assertEqual(rep["rpm"], 2.0)
        self.assertEqual(rep["rpm_used"], 2)
        self.assertEqual(rep["tpd"], 100.0)

    def test_rpm_cap_skips_whole_provider_and_falls_through(self):
        r = self.make_or_plus_zero_config(**{"llm.free.limits": {"openrouter": {"rpm": 2}}})
        base_a = self.chan_key_of(r, "model-a:free")
        _FakeOpenAI.behavior[base_a] = _seq(_Resp("a-ok"))

        self.assertEqual(r.chat("s", "u")["model"], "model-a:free")
        self.assertEqual(r.chat("s", "u")["model"], "model-a:free")
        # openrouter 的 rpm 用满 2 → **整个 Provider** 被跳过（A、B 两通道都不再入选）
        snap = r.routing_snapshot()
        self.assertFalse(self.chan(snap, "model-a:free")["eligible"])
        self.assertFalse(self.chan(snap, "model-b:free")["eligible"])
        self.assertEqual(self.chan(snap, "model-a:free")["limits"]["rpm_used"], 2)
        # …并自动下落到别的免费 Provider（不是改道付费：pollinations 也是免费池成员）
        out3 = r.chat("s", "u")
        self.assertEqual(out3["provider"], "pollinations")

    def test_rpm_cap_with_single_provider_fails_closed(self):
        """只配了一个 Provider 且它触顶 → 池空 → 报空池，绝不换个 Provider 乱发。"""
        r = self.make_or(**{"llm.free.limits": {"openrouter": {"rpm": 1}}})
        base_a = self.chan_key_of(r, "model-a:free")
        _FakeOpenAI.behavior[base_a] = _seq(_Resp("a-ok"))
        self.assertEqual(r.chat("s", "u")["model"], "model-a:free")
        calls_before = len(_FakeOpenAI.calls)
        with self.assertRaises(RuntimeError) as cm:
            r.chat("s", "u")
        self.assertIn("免费模型池为空", str(cm.exception))
        self.assertEqual(len(_FakeOpenAI.calls), calls_before)   # 没有发出任何请求

    def test_tpd_cap_blocks_after_token_estimate(self):
        r = self.make_or_plus_zero_config(**{"llm.free.limits": {"openrouter": {"tpd": 10}}})
        base_a = self.chan_key_of(r, "model-a:free")
        _FakeOpenAI.behavior[base_a] = _seq(_Resp("a1"))

        long_prompt = "x" * 400                      # 估算 ≈100 token ≥ tpd=10
        self.assertEqual(r.chat("s", long_prompt)["model"], "model-a:free")
        out2 = r.chat("s", long_prompt)
        self.assertEqual(out2["provider"], "pollinations")
        self.assertGreater(self.chan(r.routing_snapshot(),
                                     "model-a:free")["limits"]["tpd_used"], 10)


# ===================================================================== #
# 4. 429 冷却升级
# ===================================================================== #
class TestCooldown429Escalation(_KernelCase):

    def test_ladder_2min_10min_1h_24h(self):
        cd = K.Cooldown429(clock=lambda: self.t[0])
        for level, expect in enumerate([120.0, 600.0, 3600.0, 86400.0, 86400.0], start=1):
            info = cd.record_429("openrouter:m")
            self.assertEqual(info["level"], level)
            self.assertEqual(info["cooldown_sec"], expect)
            self.assertEqual(info["unlock_at"], self.t[0] + expect)
            self.assertTrue(cd.is_cooling("openrouter:m"))
            self.assertEqual(cd.level("openrouter:m"), level)
            self.assertEqual(cd.remaining("openrouter:m"), expect)

    def test_window_resets_after_24h(self):
        cd = K.Cooldown429(clock=lambda: self.t[0])
        cd.record_429("m")            # 第 1 档
        cd.record_429("m")            # 第 2 档
        self.t[0] += 86401.0          # 距上次 429 超过 24h → 阶梯归零
        info = cd.record_429("m")
        self.assertEqual(info["level"], 1)
        self.assertEqual(info["cooldown_sec"], 120.0)

    def test_cooldown_persists_across_restart(self):
        path = os.path.join(self._tmp.name, "free_router", "router_state.db")
        cd1 = K.Cooldown429(clock=lambda: self.t[0],
                            store=K.PersistentStateStore(path))
        cd1.record_429("m")
        cd1.record_429("m")
        cd1.record_429("m")
        self.assertEqual(cd1.level("m"), 3)
        self.assertEqual(cd1.remaining("m"), 3600.0)

        cd2 = K.Cooldown429(clock=lambda: self.t[0],
                            store=K.PersistentStateStore(path))
        self.assertEqual(cd2.level("m"), 3)               # 等级从库里恢复
        self.assertEqual(cd2.remaining("m"), 3600.0)      # 冷却截止时间也恢复
        self.assertTrue(cd2.is_cooling("m"))

    def test_router_escalates_after_backoff_exhausted(self):
        r = self.make_or()
        base_a = self.chan_key_of(r, "model-a:free")
        base_b = self.chan_key_of(r, "model-b:free")
        _FakeOpenAI.behavior[base_a] = _seq(_HttpErr(429))     # A 一直 429
        _FakeOpenAI.behavior[base_b] = _seq(_Resp("b-ok"))

        out = r.chat("s", "u")
        self.assertEqual(out["routed_via"], "openrouter:model-b:free")
        # A：1 次首试 + 3 次退避重试全部 429 → 才升级冷却
        a_calls = [c for c in _FakeOpenAI.calls if c[0] == base_a]
        self.assertEqual(len(a_calls), 1 + BACKOFF_MAX_RETRIES)

        ch_a = self.chan(r.routing_snapshot(), "model-a:free")
        self.assertEqual(ch_a["cooldown_level"], 1)
        self.assertEqual(ch_a["remaining_sec"], 120.0)         # 第 1 档 2 分钟
        self.assertFalse(ch_a["eligible"])
        self.assertEqual(ch_a["penalty"], 10.0)                # 4×3=12 → 封顶 10

        # 冷却到期 → 重新可用
        self.t[0] += 121.0
        ch_a2 = self.chan(r.routing_snapshot(), "model-a:free")
        self.assertEqual(ch_a2["remaining_sec"], 0.0)
        self.assertTrue(ch_a2["eligible"])

    def test_second_429_escalates_to_10min(self):
        r = self.make_or()
        base_a = self.chan_key_of(r, "model-a:free")
        base_b = self.chan_key_of(r, "model-b:free")
        _FakeOpenAI.behavior[base_a] = _seq(_HttpErr(429))
        _FakeOpenAI.behavior[base_b] = _seq(_Resp("b-ok"))

        r.chat("s", "u")
        row = r._cd429.snapshot()[0]
        self.assertEqual((row["level"], row["remaining_sec"]), (1, 120.0))

        # 白盒把 B 禁掉，聚焦验证 A 的第 2 档升级（否则 A 已被惩罚下沉，会先走 B）
        r._disabled.add("model-b:free")
        self.t[0] += 121.0                                     # 第 1 档冷却过期
        # A 是唯一候选且再次被限流 → 整条链失败（fail-closed：报失败，不改道付费）
        with self.assertRaises(RuntimeError):
            r.chat("s", "u")
        cd = {row["key"]: row for row in r._cd429.snapshot()}
        self.assertEqual(cd["openrouter:model-a:free"]["level"], 2)
        self.assertEqual(cd["openrouter:model-a:free"]["remaining_sec"], 600.0)


# ===================================================================== #
# 5. Sticky Session
# ===================================================================== #
class TestStickySession(_KernelCase):

    MSGS = [
        {"role": "user", "content": "第一条 user 消息"},
        {"role": "assistant", "content": "上一轮的回答"},
        {"role": "user", "content": "继续追问"},
    ]
    FIRST = "第一条 user 消息"

    def test_session_key_is_sha1_of_first_user_message(self):
        expect = hashlib.sha1(self.FIRST.encode("utf-8")).hexdigest()
        self.assertEqual(K.session_key_for(self.FIRST, True), expect)
        self.assertEqual(K.session_key_for(self.FIRST, False), "")   # 单轮不给键
        self.assertEqual(K.session_key_for("", True), "")

    def test_key_uses_first_user_message_in_message_list(self):
        r = self.make_or()
        _FakeOpenAI.behavior[self.chan_key_of(r, "model-a:free")] = _seq(_Resp("a-ok"))
        r.chat("s", "ignored", messages=self.MSGS)
        expect = hashlib.sha1(self.FIRST.encode("utf-8")).hexdigest()
        self.assertEqual(list(r._sticky.snapshot().keys()), [expect])
        self.assertEqual(r.routing_snapshot()["sticky_sessions"], 1)

    def test_single_turn_never_creates_sticky(self):
        r = self.make_or()
        _FakeOpenAI.behavior[self.chan_key_of(r, "model-a:free")] = _seq(_Resp("a-ok"))
        out = r.chat("s", "只有一轮的提问")
        self.assertEqual(out["routed_via"], "openrouter:model-a:free")
        self.assertEqual(r.routing_snapshot()["sticky_sessions"], 0)
        self.assertEqual(r._sticky.snapshot(), {})

    def test_sticky_prefers_previous_model_but_not_enforced(self):
        r = self.make_or()
        base_a = self.chan_key_of(r, "model-a:free")
        _FakeOpenAI.behavior[self.chan_key_of(r, "model-b:free")] = _seq(_Resp("b-ok"))
        # ① 多轮首轮：A 命中 → 建立 Sticky
        _FakeOpenAI.behavior[base_a] = _seq(_Resp("a-ok"))
        self.assertEqual(r.chat("s", "x", messages=self.MSGS)["routed_via"],
                         "openrouter:model-a:free")

        # ② 白盒给 A 记一次 429 惩罚：自然排序已变成 B 优先（说明下面确是 Sticky 起作用）
        r._penalty.on_429("openrouter:model-a:free")
        r._alive["model-b:free"] = True      # 同层，才比得出惩罚分（见上一条注释）
        self.assertEqual(r.ranked()[0].model, "model-b:free")
        # 但 Sticky 命中 → 仍优先走上次的 A（「优先」而非「强制」）
        self.assertEqual(r.chat("s", "x", messages=self.MSGS)["routed_via"],
                         "openrouter:model-a:free")

        # ③ A 进了 429 冷却 → 不再强制，正常 Fallback 到 B，并把 Sticky 改写到 B
        r._cd429.record_429("openrouter:model-a:free")
        self.assertEqual(r.chat("s", "x", messages=self.MSGS)["routed_via"],
                         "openrouter:model-b:free")
        self.assertEqual(list(r._sticky.snapshot().values()),
                         ["openrouter:model-b:free"])

    def test_second_chat_keeps_same_model_in_multi_turn(self):
        """多轮对话中途不换模型（Sticky 的核心价值）。"""
        r = self.make_or()
        _FakeOpenAI.behavior[self.chan_key_of(r, "model-a:free")] = _seq(_Resp("a-ok"))
        first = r.chat("s", "x", messages=self.MSGS)["routed_via"]
        second = r.chat("s", "x", messages=self.MSGS)["routed_via"]
        self.assertEqual(first, second)

    def test_sticky_expires_after_30_minutes(self):
        r = self.make_or()
        _FakeOpenAI.behavior[self.chan_key_of(r, "model-a:free")] = _seq(_Resp("a-ok"))
        r.chat("s", "x", messages=self.MSGS)
        self.assertEqual(r.routing_snapshot()["sticky_sessions"], 1)
        self.t[0] += 1800.0                      # 恰好 30 分钟：仍在有效期内
        self.assertEqual(r.routing_snapshot()["sticky_sessions"], 1)
        self.t[0] += 1.0                         # 超过 30 分钟 → 失效并被清理
        self.assertEqual(r.routing_snapshot()["sticky_sessions"], 0)
        self.assertEqual(r._sticky.snapshot(), {})

    def test_explicit_multi_turn_flag_works_without_messages(self):
        r = self.make_or()
        _FakeOpenAI.behavior[self.chan_key_of(r, "model-a:free")] = _seq(_Resp("a-ok"))
        r.chat("s", "多轮会话的首轮", multi_turn=True)
        self.assertEqual(r.routing_snapshot()["sticky_sessions"], 1)
        r.chat("s", "单轮提问", multi_turn=False)
        self.assertEqual(r.routing_snapshot()["sticky_sessions"], 1)  # 单轮不新增不命中


# ===================================================================== #
# 6. 跳过集合 / 重试预算 / 401 不进重试链
# ===================================================================== #
class TestSkipSetAndRetryBudget(_KernelCase):

    def test_skip_set_is_scoped_to_model_and_key(self):
        r = self.make_or()
        cand_a = next(c for c in r.candidates() if c.model == "model-a:free")
        skip = K.SkipSet()
        self.assertFalse(skip.has_candidate(cand_a))
        skip.add_candidate(cand_a)
        self.assertTrue(skip.has_candidate(cand_a))
        self.assertEqual(len(skip), 1)
        # 换 Key / 换模型 → 不受影响（同一模型换了 Key 仍可再试）
        other = Candidate(cand_a.provider, cand_a.base_url, "sk-another", cand_a.model)
        self.assertFalse(skip.has_candidate(other))
        self.assertNotEqual(K.SkipSet.candidate_key(other),
                            K.SkipSet.candidate_key(cand_a))

    def test_failed_model_is_not_retried_within_same_call(self):
        r = self.make_or()
        base_a = self.chan_key_of(r, "model-a:free")
        base_b = self.chan_key_of(r, "model-b:free")
        _FakeOpenAI.behavior[base_a] = _seq(_HttpErr(500))
        _FakeOpenAI.behavior[base_b] = _seq(_Resp("b-ok"))
        out = r.chat("s", "u")
        self.assertEqual(out["routed_via"], "openrouter:model-b:free")
        self.assertEqual([c[0] for c in _FakeOpenAI.calls], [base_a, base_b])

    def test_network_error_switches_candidate(self):
        r = self.make_or()
        base_a = self.chan_key_of(r, "model-a:free")
        base_b = self.chan_key_of(r, "model-b:free")
        _FakeOpenAI.behavior[base_a] = _seq(ConnectionRefusedError("refused"))
        _FakeOpenAI.behavior[base_b] = _seq(_Resp("b-ok"))
        self.assertEqual(r.chat("s", "u")["routed_via"],
                         "openrouter:model-b:free")
        self.assertEqual([c[0] for c in _FakeOpenAI.calls], [base_a, base_b])

    def test_timeout_switches_candidate(self):
        r = self.make_or()
        base_a = self.chan_key_of(r, "model-a:free")
        base_b = self.chan_key_of(r, "model-b:free")
        _FakeOpenAI.behavior[base_a] = _seq(TimeoutError("timeout"))
        _FakeOpenAI.behavior[base_b] = _seq(_Resp("b-ok"))
        self.assertEqual(r.chat("s", "u")["routed_via"],
                         "openrouter:model-b:free")

    def test_401_not_retried_and_permanently_disabled(self):
        r = self.make_or()
        base_a = self.chan_key_of(r, "model-a:free")
        base_b = self.chan_key_of(r, "model-b:free")
        _FakeOpenAI.behavior[base_a] = _seq(_HttpErr(401))
        _FakeOpenAI.behavior[base_b] = _seq(_Resp("b-ok"))

        out = r.chat("s", "u")
        self.assertEqual(out["routed_via"], "openrouter:model-b:free")
        # 认证失败只打一次，不进重试链
        self.assertEqual(len([c for c in _FakeOpenAI.calls if c[0] == base_a]), 1)
        self.assertIn("model-a:free", r._disabled)
        self.assertFalse(self.chan(r.routing_snapshot(),
                                   "model-a:free")["eligible"])

        # 之后也不再尝试被永久禁用的模型
        before = len(_FakeOpenAI.calls)
        r.chat("s", "u")
        self.assertEqual([c for c in _FakeOpenAI.calls[before:] if c[0] == base_a], [])

    def test_403_also_not_retried(self):
        r = self.make_or()
        base_a = self.chan_key_of(r, "model-a:free")
        _FakeOpenAI.behavior[base_a] = _seq(_HttpErr(403))
        _FakeOpenAI.behavior[self.chan_key_of(r, "model-b:free")] = _seq(_Resp("b-ok"))
        self.assertEqual(r.chat("s", "u")["routed_via"],
                         "openrouter:model-b:free")
        self.assertEqual(len([c for c in _FakeOpenAI.calls if c[0] == base_a]), 1)

    def test_429_retries_same_model_before_switching(self):
        r = self.make_or()
        base_a = self.chan_key_of(r, "model-a:free")
        _FakeOpenAI.behavior[base_a] = _seq(_HttpErr(429), _HttpErr(429),
                                            _Resp("a-ok"))
        _FakeOpenAI.behavior[self.chan_key_of(r, "model-b:free")] = _seq(_Resp("b-ok"))
        self.assertEqual(r.chat("s", "u")["routed_via"],
                         "openrouter:model-a:free")
        self.assertEqual(len([c for c in _FakeOpenAI.calls if c[0] == base_a]), 3)

    def test_chain_retry_budget_is_20_attempts(self):
        models = ",".join(f"m{i}:free" for i in range(22))
        r = self.make(**{"llm.free.openrouter_key": "sk-test",
                         "llm.free.openrouter_models": models})
        for c in r.candidates():
            _FakeOpenAI.behavior[c.model] = _seq(_HttpErr(500))
        with self.assertRaises(RuntimeError):
            r.chat("s", "u")
        self.assertEqual(len(_FakeOpenAI.calls), K.MAX_ROUTE_ATTEMPTS)
        self.assertEqual(K.MAX_ROUTE_ATTEMPTS, 20)


# ===================================================================== #
# 7. 可观测性 + fail-closed
# ===================================================================== #
class TestSnapshotAndFailClosed(_KernelCase):

    def test_empty_pool_fails_closed_without_any_call(self):
        r = self.make()                       # 所有渠道关掉 → 免费池为空
        with self.assertRaises(RuntimeError) as cm:
            r.chat("s", "u")
        self.assertIn("免费模型池为空", str(cm.exception))
        self.assertEqual(_FakeOpenAI.calls, [])          # 一个请求都没发
        snap = r.routing_snapshot()
        self.assertEqual(snap["channels"], [])
        # R3.7：mode 追加了价格门字段（free_only / fail_closed 含义不变），
        # 所以这里不再整体相等比较，而是逐键断言原语义 + 新字段存在。
        self.assertIs(snap["mode"]["free_only"], True)
        self.assertIs(snap["mode"]["fail_closed"], True)
        self.assertEqual(snap["mode"]["price_gate"], "fail_closed_whitelist")
        self.assertEqual(snap["mode"]["trusted_models"], [])
        self.assertEqual(snap["mode"]["trusted_models_count"], 0)
        self.assertEqual(snap["last_routed_via"], "")

    def test_all_providers_are_known_free_providers(self):
        r = self.make(**{"llm.free.openrouter_key": "sk-test",
                         "llm.free.nvidia_key": "sk-test",
                         "llm.free.zero_config.enabled": "true"})
        providers = {c.provider for c in r.candidates()}
        self.assertTrue(providers)
        self.assertTrue(providers <= set(K.KNOWN_PROVIDERS))

    def test_routing_snapshot_is_json_serializable_and_complete(self):
        r = self.make_or()
        _FakeOpenAI.behavior[self.chan_key_of(r, "model-a:free")] = _seq(_Resp("a-ok"))
        out = r.chat("s", "u")

        snap = r.routing_snapshot()
        blob = json.dumps(snap, ensure_ascii=False)      # 必须可序列化
        self.assertEqual(json.loads(blob), snap)         # 且能无损往返

        self.assertIsInstance(snap["generated_at"], float)
        self.assertEqual(snap["last_routed_via"], out["routed_via"])
        self.assertIs(snap["sticky_sessions"], 0)
        # R3.7：mode 为追加式扩展（free_only / fail_closed 语义未变）
        self.assertIs(snap["mode"]["free_only"], True)
        self.assertIs(snap["mode"]["fail_closed"], True)
        self.assertEqual(snap["mode"]["price_gate"], "fail_closed_whitelist")
        self.assertEqual(snap["mode"]["trusted_models"], [])
        self.assertIsInstance(snap["mode"]["trusted_models_count"], int)
        self.assertIsInstance(snap["channels"], list)

        ch = self.chan(snap, "model-a:free")
        for key in ("provider", "model", "base_priority", "penalty",
                    "effective_score", "eligible", "cooldown_until",
                    "remaining_sec", "cooldown_level", "limits", "ok", "fail",
                    "latency_ms"):
            self.assertIn(key, ch)
        self.assertIsInstance(ch["base_priority"], int)
        self.assertIsInstance(ch["penalty"], float)
        self.assertIsInstance(ch["effective_score"], float)
        self.assertIsInstance(ch["eligible"], bool)
        self.assertIsInstance(ch["cooldown_level"], int)
        self.assertIsInstance(ch["remaining_sec"], float)
        self.assertEqual(ch["ok"], 1)
        self.assertEqual(ch["fail"], 0)
        for key in ("rpm", "rpd", "tpm", "tpd",
                    "rpm_used", "rpd_used", "tpm_used", "tpd_used"):
            self.assertIn(key, ch["limits"])
        # 排序键：effective_score 升序
        scores = [c["effective_score"] for c in snap["channels"]]
        self.assertEqual(scores, sorted(scores))
        self.assertIn("persistence", snap)
        self.assertTrue(snap["persistence"]["ok"])

    def test_routed_via_matches_provider_and_model(self):
        r = self.make_or()
        _FakeOpenAI.behavior[self.chan_key_of(r, "model-a:free")] = _seq(_Resp("a-ok"))
        out = r.chat("s", "u")
        self.assertEqual(out["routed_via"], f"{out['provider']}:{out['model']}")
        self.assertIn("tried", out)
        self.assertEqual(out["tried"], ["openrouter:model-a:free"])

    def test_snapshot_is_read_only_no_network(self):
        """routing_snapshot() 绝不能联网刷新（设置页后台线程会调它）。"""
        r = self.make_or(**{"llm.free.auto_catalog": "true"})
        with mock.patch.object(FreeModelRouter, "_fetch_openrouter_free",
                               side_effect=AssertionError("不该联网")), \
             mock.patch.object(FreeModelRouter, "_fetch_local_bridges",
                               side_effect=AssertionError("不该探测本机桥")), \
             mock.patch.object(FreeModelRouter, "_fetch_nvidia",
                               side_effect=AssertionError("不该联网")):
            snap = r.routing_snapshot()
        self.assertTrue(snap["channels"])            # 仍然给出已知通道
        self.assertFalse(r._catalog_frozen)          # 用完必须复位
        # 复位后目录刷新照常可用（这里只验证没被永久冻住）
        self.assertTrue(r.refresh_catalog() is not None)

    def test_state_dir_follows_config(self):
        r = self.make_or()
        db = os.path.join(self._tmp.name, "free_router", "router_state.db")
        self.assertTrue(os.path.isfile(db))
        self.assertTrue(r.routing_snapshot()["persistence"]["ok"])
        self.assertEqual(r.routing_snapshot()["persistence"]["path"], db)


class TestStrictFreePricing(_KernelCase):
    """fail-closed 硬约束：五维计价全 0 才算免费，任一项非 0 即不算。"""

    def test_all_five_zero_is_free(self):
        self.assertTrue(_strictly_free_pricing(
            {"prompt": "0", "completion": "0", "request": "0",
             "input_cache_read": "0", "input_cache_write": "0"}))
        self.assertTrue(_strictly_free_pricing({}))                      # 缺字段=未计费
        self.assertTrue(_strictly_free_pricing({"prompt": 0, "completion": 0}))
        self.assertTrue(_strictly_free_pricing({"prompt": "0.0", "completion": "0E0"}))

    def test_each_non_zero_dimension_is_rejected(self):
        base = {"prompt": "0", "completion": "0", "request": "0",
                "input_cache_read": "0", "input_cache_write": "0"}
        for key in STRICT_FREE_COST_KEYS:
            bad = dict(base)
            bad[key] = "0.0001"
            self.assertFalse(_strictly_free_pricing(bad), f"{key} 非 0 竟然算免费")
        self.assertFalse(_strictly_free_pricing({"prompt": "0", "completion": "0",
                                                 "request": "0.5"}))

    def test_unparseable_or_none_is_rejected(self):
        for bad_val in (None, "", "abc", "免费", [], {}):
            for key in ("prompt", "request", "input_cache_write"):
                self.assertFalse(
                    _strictly_free_pricing({"prompt": "0", "completion": "0", key: bad_val}),
                    f"{key}={bad_val!r} 竟然算免费")
        self.assertFalse(_strictly_free_pricing("not-a-dict"))
        self.assertFalse(_strictly_free_pricing(None))

    def test_paid_model_never_enters_candidate_pool(self):
        """付费项（哪怕只有 request / cache 维度非 0）不得进入 OpenRouter 候选。

        直接跑真实 _fetch_openrouter_free()（只把出网 _http 换掉），
        验证的是产品代码里的过滤逻辑，不是测试里复刻的一份。
        """
        r = self.make_or()
        payload = {"data": [
            {"id": "vendor/free-all", "pricing": {"prompt": "0", "completion": "0",
                                                  "request": "0",
                                                  "input_cache_read": "0",
                                                  "input_cache_write": "0"}},
            {"id": "vendor/free-suffix:free", "pricing": {}},
            {"id": "vendor/free-missing-keys", "pricing": {"prompt": "0"}},
            {"id": "vendor/paid-prompt", "pricing": {"prompt": "0.0001"}},
            {"id": "vendor/paid-completion", "pricing": {"completion": "0.0002"}},
            {"id": "vendor/paid-request", "pricing": {"prompt": "0", "completion": "0",
                                                      "request": "0.001"}},
            {"id": "vendor/paid-cache-read", "pricing": {"input_cache_read": "0.00002"}},
            {"id": "vendor/paid-cache-write", "pricing": {"input_cache_write": "0.00002"}},
            {"id": "vendor/unknown-price", "pricing": {"prompt": "n/a"}},
            {"id": "openrouter/free", "pricing": {"prompt": "0"}},
        ]}
        with mock.patch.object(r, "_http", return_value=json.dumps(payload)):
            ids = r._fetch_openrouter_free()
        for ok in ("vendor/free-all", "vendor/free-suffix:free",
                   "vendor/free-missing-keys"):
            self.assertIn(ok, ids, f"{ok} 是免费项，应进池")
        for paid in ("vendor/paid-prompt", "vendor/paid-completion",
                     "vendor/paid-request", "vendor/paid-cache-read",
                     "vendor/paid-cache-write", "vendor/unknown-price"):
            self.assertNotIn(paid, ids, f"{paid} 是付费/不可确认项，不该进免费池")
        self.assertNotIn("openrouter/free", ids)      # 聚合路由本身仍被跳过


if __name__ == "__main__":
    unittest.main()
