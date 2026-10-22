# -*- coding: utf-8 -*-
"""R3.5 LLMGateway → FreeModelRouter 的 messages / multi_turn 透传 + Sticky 端到端。

本文件回答一个问题：**「多轮对话不因切模型而断上下文」是否真的接进了产品链路？**

覆盖（全部离线，不联网、不起端口、不装依赖，只替换 openai.OpenAI 为假客户端）：
  1. 默认行为不变：不传新参数时 ``_call_free`` 收到的 ``messages``/``multi_turn``
     仍为 ``None``（运行时打桩断言），且四个函数的既有位置参数顺序、名称、
     默认值可用 ``inspect.signature`` 逐条核对；
  2. 传了 ``messages`` 时：免费层确实收到**同一份消息列表** → 路由层算出的
     SessionKey（首条 user 消息 SHA1）与预期一致 → 建立 sticky 绑定；
  3. 第二次同历史调用：即便配置把别的 Provider 优先级调高（不 sticky 时它本该
     胜出），仍然优先命中上次的通道（「优先不强制」的正面与反面对照）；
  4. 单轮调用（不传 messages / multi_turn）永不建立绑定；
  5. 只传 ``multi_turn=True``（调用方自己知道是多轮）也能建立绑定；
  6. P0 同层重试路径同样带上 messages / multi_turn。

运行：
    .venv\\Scripts\\python.exe -m pytest tests\\test_llm_gateway_sticky_passthrough.py -q
"""
from __future__ import annotations

import inspect
import os
import socket
import sys
import tempfile
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services import free_router_kernel as K                        # noqa: E402
from services.free_model_router import FreeModelRouter              # noqa: E402
from services.llm_gateway import LLMGateway                         # noqa: E402


# --------------------------------------------------------------------- #
# 最小配置替身（点分 get/set，与既有 tests/test_llm_gateway_v2.py 同款口径）
# --------------------------------------------------------------------- #
class FakeConfig:
    def __init__(self, store=None):
        self.store = dict(store or {})

    @classmethod
    def from_flat(cls, mapping):
        """用「点分键 → 值」的扁平字典构造（内部会搭成嵌套结构）。

        注意：直接 `FakeConfig({"a.b": 1})` 是**错的** —— get() 是按点号逐层下钻的，
        扁平键会让所有配置都读不到、静默回落到默认值。本类只提供 from_flat 入口。
        """
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


# --------------------------------------------------------------------- #
# 假 OpenAI 客户端：按模型名回话 + 记录每次出站 (model, messages, base_url)
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


class _Completions:
    def create(self, model=None, messages=None, temperature=None,
               max_tokens=None, **kw):
        _FakeOpenAI.calls.append({"model": model, "messages": messages,
                                  "base_url": _FakeOpenAI.current_base})
        return _Resp(f"[{model}] 好的，我们继续去海边")


class _Chat:
    def __init__(self):
        self.completions = _Completions()


class _FakeOpenAI:
    """替身：能当上下文管理器用（openai.OpenAI 本身也支持）。"""

    built: list = []
    calls: list = []
    current_base = ""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeOpenAI.built.append(kwargs)
        _FakeOpenAI.current_base = kwargs.get("base_url", "")
        self.chat = _Chat()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @classmethod
    def reset(cls):
        cls.built = []
        cls.calls = []
        cls.current_base = ""


def _isolated_cfg(state_dir):
    """隔离配置：关掉动态目录/桥/ollama/本地扫描/桌面AI，保证离线确定。"""
    return FakeConfig.from_flat({
        "llm.free.state_dir": state_dir,
        "llm.free.auto_catalog": "false",
        "llm.free.auto_discover_bridge": "false",
        "llm.free.include_ollama": "false",
        "llm.free.nvidia_catalog_md": "false",
        "llm.free.local_scan.enabled": "false",
        "llm.free.desktop_ai.enabled": "false",
    })


def _history(first_user="我们聊聊周末计划"):
    """一条多轮历史：含 assistant 回复 ⇒ 路由层判为多轮并建立 sticky。"""
    return [
        {"role": "user", "content": first_user},
        {"role": "assistant", "content": "好啊，你想去哪儿？"},
        {"role": "user", "content": "我想去海边"},
    ]


class _GatewayCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="loveai_sticky_")
        _FakeOpenAI.reset()
        self.cfg = _isolated_cfg(self._tmp.name)
        self.patch = mock.patch("openai.OpenAI", _FakeOpenAI)
        self.patch.start()
        # 「离线」不是假设而是强制：任何非回环 socket 连接直接判失败
        self.outbound_attempts = []
        self._real_connect = socket.socket.connect

        def _guard(self_sock, address, *a, **kw):
            host = address[0] if isinstance(address, (tuple, list)) else address
            if isinstance(host, str) and (host.startswith("127.") or host in
                                          ("::1", "localhost", "0.0.0.0")):
                return self._real_connect(self_sock, address, *a, **kw)
            self.outbound_attempts.append(address)
            raise AssertionError("本用例必须离线，却试图外连：%r" % (address,))

        socket.socket.connect = _guard

    def tearDown(self):
        socket.socket.connect = self._real_connect
        self.patch.stop()
        # Windows 下 sqlite 文件被共享连接持有 → 临时目录删不掉；
        # 内核为测试专门提供了 close_shared_connections()。
        try:
            K.close_shared_connections()
        except Exception:  # noqa: BLE001
            pass
        self._tmp.cleanup()

    def _gateway(self, router=None):
        gw = LLMGateway(self.cfg, signals=None, db=None)
        if router is not None:
            gw._free_router = router            # 跳过懒创建里的 probe_all（离线）
        return gw

    def _router(self):
        r = FreeModelRouter(self.cfg)
        # 护栏：配置真的生效了吗？状态库必须落在本用例的 tmp 里，
        # 绝不能读到仓库里 data/free_router/router_state.db 的历史 sticky。
        self.assertTrue(
            r.routing_snapshot()["persistence"]["path"].startswith(self._tmp.name),
            "隔离失效：状态库没落在 tmp，测试会串味")
        self.assertEqual(r._sticky.count(), 0, "新路由器不该带着历史 sticky")
        self.assertEqual(self.outbound_attempts, [])
        return r


# --------------------------------------------------------------------- #
# 1) 默认行为不变（签名 + 运行时双重证据）
# --------------------------------------------------------------------- #
class TestDefaultsUnchanged(_GatewayCase):

    def test_signatures_keep_existing_positional_order(self):
        chat_params = list(inspect.signature(LLMGateway.chat).parameters)
        self.assertEqual(chat_params[:8],
                         ["self", "system_prompt", "user_prompt", "complexity",
                          "temperature", "max_tokens", "allow_rule_fallback", "tier"])
        self.assertEqual(chat_params[8:], ["messages", "multi_turn"])
        sig = inspect.signature(LLMGateway.chat)
        self.assertIsNone(sig.parameters["messages"].default)
        self.assertIsNone(sig.parameters["multi_turn"].default)

        layer = list(inspect.signature(LLMGateway._call_layer).parameters)
        self.assertEqual(layer[:6], ["self", "layer", "system_prompt", "user_prompt",
                                     "temperature", "max_tokens"])
        self.assertIsNone(inspect.signature(
            LLMGateway._call_layer).parameters["messages"].default)
        self.assertIsNone(inspect.signature(
            LLMGateway._call_layer).parameters["multi_turn"].default)

        free = list(inspect.signature(LLMGateway._call_free).parameters)
        self.assertEqual(free[:5], ["self", "system_prompt", "user_prompt",
                                    "temperature", "max_tokens"])
        self.assertIsNone(inspect.signature(
            LLMGateway._call_free).parameters["messages"].default)
        self.assertIsNone(inspect.signature(
            LLMGateway._call_free).parameters["multi_turn"].default)

        fault = list(inspect.signature(LLMGateway._handle_layer_fault).parameters)
        self.assertEqual(fault[:7], ["self", "layer", "error", "system_prompt",
                                     "user_prompt", "temperature", "max_tokens"])
        self.assertEqual(fault[7:], ["messages", "multi_turn"])
        self.assertIsNone(inspect.signature(
            LLMGateway._handle_layer_fault).parameters["messages"].default)

    def test_call_free_receives_none_when_caller_omits_new_params(self):
        """运行时证据：老调用方式下，免费层拿到的仍是 None/None（逐字节等价）。"""
        router = self._router()
        gw = self._gateway(router)
        seen = []
        real = router.chat

        def _spy(*a, **kw):
            seen.append(kw)
            return real(*a, **kw)

        router.chat = _spy
        text = gw.chat("你是助手", "你好", complexity="l3")
        self.assertTrue(text)
        self.assertEqual(len(seen), 1)
        self.assertIsNone(seen[0]["messages"], "老调用方不该凭空多出 messages")
        self.assertIsNone(seen[0]["multi_turn"])
        self.assertEqual(router.routing_snapshot()["sticky_sessions"], 0)
        # 出站报文仍是 system + user 两条，形状未变
        self.assertEqual([m["role"] for m in _FakeOpenAI.calls[0]["messages"]],
                         ["system", "user"])

    def test_call_free_spy_sees_none_at_layer_level(self):
        """再看一层：_call_free 自身收到的也是 None/None。"""
        router = self._router()
        gw = self._gateway(router)
        seen = []
        real_call_free = gw._call_free

        def _spy(*a, **kw):
            seen.append(kw)
            return real_call_free(*a, **kw)

        gw._call_free = _spy
        gw.chat("你是助手", "你好", complexity="l3")
        self.assertEqual(len(seen), 1)
        self.assertIsNone(seen[0].get("messages"))
        self.assertIsNone(seen[0].get("multi_turn"))


# --------------------------------------------------------------------- #
# 2) 透传真的到路由层 + sticky 绑定
# --------------------------------------------------------------------- #
class TestPassthroughAndSticky(_GatewayCase):

    def test_messages_reach_router_and_bind_sticky(self):
        router = self._router()
        gw = self._gateway(router)
        history = _history()
        seen = []
        real = router.chat

        def _spy(*a, **kw):
            seen.append(kw)
            return real(*a, **kw)

        router.chat = _spy

        text = gw.chat("你是助手", history[-1]["content"],
                       complexity="l3", messages=history)
        self.assertIn("海边", text)

        # ① 路由层确实收到了同一份消息列表（同一个对象、内容一致）
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0]["messages"], history)
        self.assertIsNone(seen[0]["multi_turn"])

        # ② 建立了 sticky 绑定，且键 = 首条 user 消息的 SHA1
        snap = router.routing_snapshot()
        self.assertGreaterEqual(snap["sticky_sessions"], 1)
        expect_key = K.session_key_for(history[0]["content"], True)
        self.assertIn(expect_key, router._sticky.snapshot())
        self.assertEqual(router._sticky.snapshot()[expect_key],
                         snap["last_routed_via"])
        self.assertTrue(snap["last_routed_via"])
        self.assertEqual(self.outbound_attempts, [], "全程不得外连")

    def test_second_call_with_same_history_prefers_same_channel(self):
        router = self._router()
        gw = self._gateway(router)
        history = _history()

        gw.chat("你是助手", history[-1]["content"], complexity="l3",
                messages=history)
        first_channel = router.routing_snapshot()["last_routed_via"]
        first_model = _FakeOpenAI.calls[-1]["model"]
        self.assertTrue(first_channel)
        first_provider = first_channel.split(":", 1)[0]
        self.assertEqual(router.routing_snapshot()["sticky_sessions"], 1)

        # ---- 反面对照：把「另一个 Provider」做成铁定胜出的候选 ----
        # ranked() 分层为「alive 组 → unknown 组」，只改分数不足以翻盘，
        # 因此这里显式把另一个 Provider 的候选标记为探活通过（模拟 probe_all 的成功）
        # 并给 0 优先级，同时把上次命中的 Provider 压到 999。
        other = next(c for c in router.candidates() if c.provider != first_provider)
        router._alive[other.model] = True                     # = 探活通过
        self.cfg.set(f"llm.free.priority.{other.provider}", 0)
        self.cfg.set(f"llm.free.priority.{first_provider}", 999)
        ranked_now = router.ranked()
        self.assertEqual(ranked_now[0].provider, other.provider,
                         "对照前提：不带 sticky 时排序首位已换成另一个 Provider")

        # ③ 仍然优先命中同一通道（sticky 生效）
        gw.chat("你是助手", history[-1]["content"], complexity="l3",
                messages=history)
        second_channel = router.routing_snapshot()["last_routed_via"]
        second_model = _FakeOpenAI.calls[-1]["model"]
        self.assertEqual(second_channel, first_channel)
        self.assertEqual(second_model, first_model)
        self.assertEqual(router.routing_snapshot()["sticky_sessions"], 1,
                         "同一条会话不该产生第二条绑定")

        # ④ 对照收尾：同样配置下，换一条全新会话（无 sticky）确实落到另一个 Provider
        #    —— 证明第 ③ 步的同通道不是「反正都会命中」，而是 sticky 起的作用。
        fresh = _history(first_user="换个话题：明天早餐吃什么")
        gw.chat("你是助手", fresh[-1]["content"], complexity="l3", messages=fresh)
        self.assertEqual(_FakeOpenAI.calls[-1]["model"], other.model)
        self.assertEqual(self.outbound_attempts, [], "全程不得外连")

    def test_single_turn_never_binds(self):
        router = self._router()
        gw = self._gateway(router)
        gw.chat("你是助手", "你好呀", complexity="l3")
        gw.chat("你是助手", "你好呀", complexity="l3")
        self.assertEqual(router.routing_snapshot()["sticky_sessions"], 0)

    def test_explicit_multi_turn_without_messages_binds(self):
        router = self._router()
        gw = self._gateway(router)
        seen = []
        real = router.chat

        def _spy(*a, **kw):
            seen.append(kw)
            return real(*a, **kw)

        router.chat = _spy
        gw.chat("你是助手", "我们继续聊周末", complexity="l3", multi_turn=True)
        self.assertIsNone(seen[0]["messages"])
        self.assertIs(seen[0]["multi_turn"], True)
        expect_key = K.session_key_for("我们继续聊周末", True)
        self.assertIn(expect_key, router._sticky.snapshot())

    def test_helper_router_from_gateway_lazy_creation(self):
        """走真实 get_free_router()（不注入）：懒创建路径同样把 messages 送到。"""
        gw = self._gateway()                    # 不注入 → 由网关自己建
        history = _history(first_user="懒创建路径的多轮会话")
        gw.chat("你是助手", "接着说说看", complexity="l3", messages=history)
        router = gw.get_free_router()
        self.assertGreaterEqual(router.routing_snapshot()["sticky_sessions"], 1)
        expect_key = K.session_key_for(history[0]["content"], True)
        self.assertIn(expect_key, router._sticky.snapshot())


# --------------------------------------------------------------------- #
# 3) P0 同层重试路径同样带上 messages / multi_turn
# --------------------------------------------------------------------- #
class TestFaultPathPassthrough(_GatewayCase):

    def _recorder(self, gw):
        seen = []

        def _fake_call_layer(layer, system_prompt, user_prompt,
                             temperature, max_tokens,
                             messages=None, multi_turn=None):
            seen.append({"layer": layer, "messages": messages,
                         "multi_turn": multi_turn})
            return "P0 重试成功"

        gw._call_layer = _fake_call_layer
        return seen

    def test_p0_retry_carries_messages(self):
        gw = self._gateway(self._router())
        seen = self._recorder(gw)
        history = _history()
        out = gw._handle_layer_fault("free", TimeoutError("读超时"),
                                     "你是助手", "接着聊", 0.7, 100,
                                     messages=history, multi_turn=None)
        self.assertEqual(out, "P0 重试成功")
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0]["messages"], history)
        self.assertIsNone(seen[0]["multi_turn"])

    def test_p0_retry_defaults_still_none(self):
        gw = self._gateway(self._router())
        seen = self._recorder(gw)
        out = gw._handle_layer_fault("free", TimeoutError("读超时"),
                                     "你是助手", "接着聊", 0.7, 100)
        self.assertEqual(out, "P0 重试成功")
        self.assertIsNone(seen[0]["messages"])
        self.assertIsNone(seen[0]["multi_turn"])


if __name__ == "__main__":
    unittest.main()
