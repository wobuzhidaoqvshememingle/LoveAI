# -*- coding: utf-8 -*-
"""
LoveAI v3.2 - 本地模型中文分层 单元测试（离线确定性，不加载真实模型/不联网）

覆盖（全部 monkeypatch 假 onnx session / 假 ollama HTTP，禁止真实模型加载）：
  1. L1(SmolLM2) 默认拒绝中文自由生成（CJK 即 raise），不会乱码输出；
     诊断开关 allow_chinese_generation=true 时放行（转去加载/生成，由 fake 接住）。
  2. L1 中文守门不影响英文（英文 user_prompt 不触发守门）。
  3. L2 走 Ollama HTTP 后端：_call_l2_ollama 正确解析 /api/chat；_ollama_alive 判定模型存在/缺失。
  4. chat() 选层：中文日常对话(complexity=l2) -> L2(Qwen/ollama)，绝不落到 L1 生成；
     L2 不可用 -> free -> 规则兜底（不崩）。
  5. classify_l1：固定标签集打分，fake session 下选中分最高标签（非生成轻活）。
  6. get_status 含 ollama 字段且如实。
"""
import os
import sys
import json
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.llm_gateway import LLMGateway  # noqa: E402


class FakeConfig:
    """最小 ConfigService 替身：点分 get/set。"""

    def __init__(self) -> None:
        self.store: dict = {}

    def get(self, key, default=None):
        v = self.store
        for part in key.split("."):
            if isinstance(v, dict) and part in v:
                v = v[part]
            else:
                return default
        return v

    def set(self, key, value) -> None:
        parts = key.split(".")
        node = self.store
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value


def _make_gw():
    return LLMGateway(FakeConfig(), signals=None, db=None)


# --------------------------------------------------------------------------- #
# 1. L1 中文守门
# --------------------------------------------------------------------------- #
class TestL1ChineseGuard(unittest.TestCase):
    def test_has_cjk_detects_chinese(self):
        self.assertTrue(LLMGateway._has_cjk("她今天冷淡"))
        self.assertFalse(LLMGateway._has_cjk("hello world"))
        self.assertFalse(LLMGateway._has_cjk(""))

    def test_l1_refuses_chinese_by_default(self):
        gw = _make_gw()
        # 默认拒绝中文自由生成，且不应触发真实加载
        with self.assertRaises(RuntimeError) as cm:
            gw._call_l1("你是助手", "她今天回复很冷淡，我该不该追问？", 0.7, 50)
        self.assertIn("中文", str(cm.exception))
        self.assertFalse(gw.l1_loaded, "中文守门应在加载前拒绝")

    def test_l1_greeting_refused_not_gibberish(self):
        """问候类中文走 l1 链时被守门拦下，不产出 SmolLM2 乱码。"""
        gw = _make_gw()
        with self.assertRaises(RuntimeError):
            gw._call_l1("sys", "你好", 0.7, 20)

    def test_l1_flag_allows_chinese_bypasses_guard(self):
        """诊断开关打开时，中文守门放行（转去 _ensure_l1_loaded，由 fake 接住）。"""
        gw = _make_gw()
        gw.config.set("llm.local.l1.allow_chinese_generation", "true")
        called = {}
        def fake_load():
            called["load"] = True
            gw._l1_session = None  # 让其继续走到「未加载」raise
        gw._ensure_l1_loaded = fake_load
        with self.assertRaises(RuntimeError) as cm:
            gw._call_l1("sys", "你好", 0.7, 20)
        self.assertTrue(called.get("load"), "开关打开后应继续加载流程，而不是被守门拦下")
        self.assertIn("未加载", str(cm.exception))

    def test_l1_english_not_guarded(self):
        """英文 user_prompt 不触发中文守门（转去加载）。"""
        gw = _make_gw()
        called = {}
        def fake_load():
            called["load"] = True
            gw._l1_session = None
        gw._ensure_l1_loaded = fake_load
        with self.assertRaises(RuntimeError):
            gw._call_l1("sys", "hello how are you", 0.7, 20)
        self.assertTrue(called.get("load"))


# --------------------------------------------------------------------------- #
# 2. L2 Ollama HTTP 后端
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestL2OllamaHttp(unittest.TestCase):
    def test_ollama_alive_true_when_model_present(self):
        gw = _make_gw()
        payload = {"models": [{"name": "loveai-qwen05b:latest"}]}
        with mock.patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
            self.assertTrue(gw._ollama_alive())

    def test_ollama_alive_false_when_model_missing(self):
        gw = _make_gw()
        payload = {"models": [{"name": "llama3:latest"}]}
        with mock.patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
            self.assertFalse(gw._ollama_alive())

    def test_ollama_alive_false_on_connection_error(self):
        gw = _make_gw()
        def boom(*a, **k):
            raise OSError("refused")
        with mock.patch("urllib.request.urlopen", side_effect=boom):
            self.assertFalse(gw._ollama_alive())

    def test_call_l2_ollama_parses_content(self):
        gw = _make_gw()
        payload = {"message": {"role": "assistant",
                                "content": "你可以先别急着追问。"}}
        captured = {}
        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp(payload)
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = gw._call_l2_ollama("你是助手", "她很冷淡", 0.7, 120)
        self.assertEqual(out, "你可以先别急着追问。")
        self.assertEqual(captured["body"]["model"], "loveai-qwen15b:latest")
        self.assertEqual(captured["body"]["messages"][1]["content"], "她很冷淡")
        self.assertFalse(captured["body"]["stream"])

    def test_l2_falls_back_to_fast_when_main_empty(self):
        """主力(1.5B)返回空内容时，自动改调快速兜底(0.5B)并采用其结果。"""
        gw = _make_gw()
        seen_models = []
        def fake_urlopen(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            seen_models.append(body["model"])
            if body["model"].startswith("loveai-qwen15b"):
                return _FakeResp({"message": {"role": "assistant", "content": ""}})
            return _FakeResp({"message": {"role": "assistant",
                                          "content": "先别追问，缓一缓。"}})
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = gw._call_l2_ollama("你是助手", "她很冷淡", 0.7, 120)
        self.assertEqual(out, "先别追问，缓一缓。")
        self.assertEqual(seen_models[0], "loveai-qwen15b:latest")
        self.assertEqual(seen_models[1], "loveai-qwen05b:latest")

    def test_l2_falls_back_when_main_errors(self):
        """主力(1.5B)连接/调用报错时，仍尝试快速兜底(0.5B)。"""
        gw = _make_gw()
        seen_models = []
        def fake_urlopen(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            seen_models.append(body["model"])
            if body["model"].startswith("loveai-qwen15b"):
                raise OSError("main model busy")
            return _FakeResp({"message": {"role": "assistant", "content": "嗯。"}})
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = gw._call_l2_ollama("你是助手", "在吗", 0.7, 60)
        self.assertEqual(out, "嗯。")
        self.assertEqual(seen_models[1], "loveai-qwen05b:latest")

    def test_ensure_l2_loaded_picks_llama_cpp_first(self):
        """llama_cpp 可用时优先选 llama_cpp 后端（Ollama 可选，不主动试）。"""
        gw = _make_gw()
        with mock.patch.object(gw, "_ollama_alive") as alive:
            try:
                gw._ensure_l2_loaded()
            except RuntimeError:
                pass
            # 默认不主动探测 Ollama
            alive.assert_not_called()

    def test_ensure_l2_loaded_ollama_optional_off_by_default(self):
        """Ollama 默认关（可选预装），只有显式开才试。"""
        gw = _make_gw()
        with mock.patch.object(gw, "_ollama_alive", return_value=True) as alive:
            try:
                gw._ensure_l2_loaded()
            except RuntimeError:
                pass
            # 默认关，不探测
            alive.assert_not_called()


# --------------------------------------------------------------------------- #
# 3. chat() 选层：中文不走 SmolLM2 生成
# --------------------------------------------------------------------------- #
class TestChatRouting(unittest.TestCase):
    def test_chinese_daily_chat_uses_l2_not_l1(self):
        """中文日常对话(l2)：L2(Qwen/ollama) 优先；即使 L2 失败也不落到 L1 生成中文。"""
        gw = _make_gw()
        calls = []
        gw._call_l2 = lambda s, u, t, m: calls.append("l2") or "Qwen中文回复"
        gw._call_l1 = lambda *a, **k: self.fail("中文生成不得走 L1(SmolLM2)")
        out = gw.chat("你是助手", "今天上班好累地铁又挤又闷真烦", complexity="l2")
        self.assertEqual(out, "Qwen中文回复")
        self.assertEqual(gw.last_layer, "l2")
        self.assertIn("l2", calls)

    def test_l2_unavailable_falls_to_stat_then_rules(self):
        """L2/free 均抛错 -> P-10 统计反应分布兜底（中文绝不走 L1 乱码）。"""
        gw = _make_gw()
        def boom(*a, **k):
            raise RuntimeError("no backend")
        gw._call_l3 = boom
        gw._call_l2 = boom
        gw._call_l1 = lambda *a, **k: self.fail("L1 不得生成中文")
        gw._call_free = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no free"))
        out = gw.chat("sys", "她今天回复很冷淡，我该不该追问？", complexity="l2")
        self.assertIsInstance(out, str)
        self.assertTrue(out)
        self.assertEqual(gw.last_layer, "stat")
        self.assertIn("统计预测", out)  # 明确标注无生成模型

    def test_stat_unavailable_falls_to_rules(self):
        """统计兜底自身也不可用（返回空）时，最终落规则句，不硬失败。"""
        gw = _make_gw()
        def boom(*a, **k):
            raise RuntimeError("no backend")
        gw._call_l3 = boom
        gw._call_l2 = boom
        gw._call_l1 = boom
        gw._call_free = boom
        gw._stat_fallback_text = lambda prompt: ""  # 统计兜底失效
        out = gw.chat("sys", "她很冷淡", complexity="l2")
        self.assertIsInstance(out, str)
        self.assertTrue(out)
        self.assertEqual(gw.last_layer, "rules")

    def test_complexity_judge_unaffected(self):
        """复杂度判定保持旧契约（问候/短句->l1，日常->l2，长/复杂->l3）。"""
        gw = _make_gw()
        self.assertEqual(gw._judge_complexity("你好呀"), "l1")
        self.assertEqual(gw._judge_complexity("今天上班好累，地铁又挤又闷回家只想躺着"), "l2")
        self.assertEqual(gw._judge_complexity("帮我仔细分析一下她这句话到底是什么意思"), "l3")


# --------------------------------------------------------------------------- #
# 4. classify_l1：固定标签集打分（非生成轻活）
# --------------------------------------------------------------------------- #
class _FakeTok:
    """假 tokenizer：把每个词映射到固定 id。"""
    def __init__(self, id_map):
        self._id = id_map

    def encode(self, text):
        class E:  # noqa
            pass
        e = E()
        e.ids = [self._id.get(text, 999)]
        return e


class _FakeOnnxSession:
    """假 onnx session：在 label token 位置给「冷淡」打高分，其余打低分。"""
    def __init__(self, prefix_len):
        self.prefix_len = prefix_len
        self.vocab = 500

    def get_inputs(self):
        return [
            _M("input_ids", [1, 0]),
            _M("attention_mask", [1, 0]),
            _M("past_key_values.0.key", [1, 3, 0, 64]),
            _M("past_key_values.0.value", [1, 3, 0, 64]),
        ]

    def get_outputs(self):
        return [_M("logits", [1, 0, self.vocab])]

    def run(self, out_names, feeds):
        import numpy as np
        seq = feeds["input_ids"]
        T = seq.shape[1]
        logits = np.zeros((1, T, self.vocab), dtype="float32")  # [batch, seq, vocab]
        last_id = int(seq[0, -1])
        # 只有当被打分为「冷淡(=301)」时给高分，其余低分
        if last_id == 301:
            logits[0, :, 301] = 5.0
        else:
            logits[0, :, last_id] = 0.0
        return [logits] + [None] * 4


class _M:
    def __init__(self, name, shape):
        self.name = name
        self.shape = shape


class TestClassifyL1(unittest.TestCase):
    def test_classify_picks_highest_score_label(self):
        gw = _make_gw()
        # prefix 编码后长度 = 3（固定）；标签 token id：开心=101 难过=201 冷淡=301
        id_map = {}
        # classify_l1 会 encode(prefix) 和 encode(label)；prefix 我们不关心具体值，只取长度
        # 让 encode 按字符串长度返回稳定 ids：prefix=3个，标签各1个
        real_encode_calls = []
        tok = _FakeTok(id_map)
        def encode(text):
            real_encode_calls.append(text)
            class E:  # noqa
                pass
            e = E()
            if text == "开心":
                e.ids = [101]
            elif text == "难过":
                e.ids = [201]
            elif text == "冷淡":
                e.ids = [301]
            else:
                # prefix：给 3 个固定 id
                e.ids = [10, 20, 30]
            return e
        tok.encode = encode
        gw._l1_session = _FakeOnnxSession(prefix_len=3)
        gw._l1_tokenizer = tok
        gw.l1_loaded = True
        label = gw.classify_l1("她今天很冷淡", ["开心", "难过", "冷淡"])
        self.assertEqual(label, "冷淡")

    def test_classify_empty_labels_raises(self):
        gw = _make_gw()
        with self.assertRaises(ValueError):
            gw.classify_l1("你好", [])


# --------------------------------------------------------------------------- #
# 5. get_status 如实
# --------------------------------------------------------------------------- #
class TestStatus(unittest.TestCase):
    def test_status_includes_ollama_fields(self):
        gw = _make_gw()
        with mock.patch.object(gw, "_ollama_alive", return_value=True):
            st = gw.get_status()
        for k in ("l1_available", "l2_available", "l3_available", "l2_backend",
                  "ollama_base", "ollama_model", "ollama_alive", "last_layer"):
            self.assertIn(k, st)
        self.assertTrue(st["ollama_alive"])
        self.assertEqual(st["ollama_model"], "loveai-qwen15b:latest")


if __name__ == "__main__":
    unittest.main(verbosity=2)
