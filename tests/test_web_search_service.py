# -*- coding: utf-8 -*-
"""WebSearchService（R3.2-03 方案3 + free_ai_search 补全）离线单元测试。
全部注入 Fake searcher，不联网、不开浏览器、不依赖 services.browser_ai_hub 存在。"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.web_search_service import (  # noqa: E402
    WebSearchService,
    SearchResult,
    RetrievalOutcome,
    should_autofill,
    dedup_results,
    build_context,
    POLICY_INSUFFICIENT,
    RETRIEVED_VIA_KB,
    RETRIEVED_VIA_KEYWORD,
    RETRIEVED_VIA_WEB,
)


class CallRecorder:
    """记录 searcher 被调用的次数与参数，按预置返回值吐出。"""

    def __init__(self, returns=None, raises=None):
        self.calls = []
        self._returns = returns if returns is not None else []
        self._raises = raises

    def __call__(self, query):
        self.calls.append(query)
        if self._raises is not None:
            raise self._raises
        return self._returns


def make_svc(**kw):
    """构造一个全部依赖都注入了 recorder 的 service。
    默认 config：自动补全开、threshold=2，方便触发测试；个别用例再覆盖。"""
    cfg = kw.pop("config", {"web.autofill.enabled": True, "web.autofill.threshold": 2})
    return WebSearchService(config=cfg, **kw)


# ---------------------------------------------------------------------------
# 纯函数：should_autofill / dedup / build_context
# ---------------------------------------------------------------------------
class TestPureFunctions(unittest.TestCase):

    def test_should_autofill_threshold(self):
        # >= threshold 不触发
        self.assertFalse(should_autofill(2, 2))
        self.assertFalse(should_autofill(5, 2))
        # < threshold 触发
        self.assertTrue(should_autofill(0, 2))
        self.assertTrue(should_autofill(1, 2))
        # 自定义阈值
        self.assertTrue(should_autofill(2, 3))
        self.assertFalse(should_autofill(3, 3))

    def test_dedup_by_url_and_fingerprint(self):
        r1 = SearchResult(title="a", url="https://x.com/1", content="same body",
                          source="s", retrieved_via=RETRIEVED_VIA_KB)
        # 同一 URL，尾部斜杠/大小写/fragment 不同 → 视为重复
        r2 = SearchResult(title="a2", url="https://X.com/1/", content="other",
                          source="s", retrieved_via=RETRIEVED_VIA_KEYWORD)
        # 同内容指纹、不同 URL → 也去重
        r3 = SearchResult(title="b", url="https://y.com/2", content="same body",
                          source="w", retrieved_via=RETRIEVED_VIA_WEB)
        r4 = SearchResult(title="c", url="https://z.com/3", content="totally different",
                          source="w", retrieved_via=RETRIEVED_VIA_WEB)
        out = dedup_results([r1, r2, r3, r4])
        # r1 保留；r2(url同) 被去；r3(内容指纹同) 被去；r4 保留
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].title, "a")
        self.assertEqual(out[1].title, "c")

    def test_build_context_contains_source_and_url(self):
        results = [
            SearchResult(title="T1", url="https://a.com/1", content="body1",
                         source="本地知识库", retrieved_via=RETRIEVED_VIA_KB),
            SearchResult(title="T2", url="https://b.com/2", content="body2",
                         source="博查AI", retrieved_via=RETRIEVED_VIA_WEB),
        ]
        ctx = build_context(results)
        self.assertIn("T1", ctx)
        self.assertIn("https://a.com/1", ctx)
        self.assertIn("本地知识库", ctx)
        self.assertIn("博查AI", ctx)
        self.assertIn("https://b.com/2", ctx)

    def test_build_context_empty(self):
        self.assertEqual(build_context([]), "")


# ---------------------------------------------------------------------------
# 主链路：阈值触发 / 开关 / 顺序 / 计数 / 降级
# ---------------------------------------------------------------------------
class TestRetrieveChain(unittest.TestCase):

    def test_ge_threshold_no_web_call(self):
        kb = CallRecorder(returns=[
            {"title": "k1", "url": "https://kb/1", "content": "c1", "source": "KB"},
            {"title": "k2", "url": "https://kb/2", "content": "c2", "source": "KB"},
        ])
        kw = CallRecorder(returns=[])
        web = CallRecorder(returns=[{"title": "w", "url": "https://w/1", "content": "wc"}])
        svc = make_svc(kb_retriever=kb, keyword_retriever=kw, free_ai_searcher=web)
        out = svc.retrieve_or_autofill("q")
        self.assertEqual(len(web.calls), 0, "本地已有2条，不应触发联网")
        self.assertFalse(out.used_web)
        self.assertEqual(out.counts["web"], 0)

    def test_lt_threshold_triggers_web(self):
        kb = CallRecorder(returns=[{"title": "k1", "url": "https://kb/1", "content": "c1"}])
        kw = CallRecorder(returns=[])
        web = CallRecorder(returns=[{"title": "w1", "url": "https://w/1", "content": "wc1",
                                     "source": "博查"}])
        svc = make_svc(kb_retriever=kb, keyword_retriever=kw, free_ai_searcher=web)
        out = svc.retrieve_or_autofill("失恋怎么办")
        self.assertEqual(len(web.calls), 1, "本地仅1条，应触发联网")
        self.assertTrue(out.used_web)
        self.assertEqual(out.counts, {"kb": 1, "keyword": 0, "web": 1})
        # web 结果并入，且带来源 URL
        urls = [r.url for r in out.results]
        self.assertIn("https://w/1", urls)
        web_results = [r for r in out.results if r.retrieved_via == RETRIEVED_VIA_WEB]
        self.assertEqual(len(web_results), 1)
        self.assertEqual(web_results[0].source, "博查")

    def test_switch_off_never_webs(self):
        kb = CallRecorder(returns=[])  # 0 条
        kw = CallRecorder(returns=[])
        web = CallRecorder(returns=[{"title": "w", "url": "https://w/1", "content": "x"}])
        cfg = {"web.autofill.enabled": False, "web.autofill.threshold": 2}
        svc = WebSearchService(config=cfg, kb_retriever=kb, keyword_retriever=kw,
                               free_ai_searcher=web)
        out = svc.retrieve_or_autofill("q")
        self.assertEqual(len(web.calls), 0, "开关关时绝不联网")
        self.assertFalse(out.used_web)
        self.assertIn("autofill_disabled", out.reasons)

    def test_call_order_kb_then_keyword_then_web(self):
        order = []

        def kb(q):
            order.append("kb")
            return []

        def kw(q):
            order.append("keyword")
            return []

        def web(q):
            order.append("web")
            return [{"title": "w", "url": "https://w/1", "content": "x"}]

        svc = make_svc(kb_retriever=kb, keyword_retriever=kw, free_ai_searcher=web)
        svc.retrieve_or_autofill("q")
        self.assertEqual(order, ["kb", "keyword", "web"])

    def test_counts_after_keyword(self):
        kb = CallRecorder(returns=[{"title": "k", "url": "u1", "content": "c"}])
        kw = CallRecorder(returns=[
            {"title": "kw1", "url": "u2", "content": "x1"},
            {"title": "kw2", "url": "u3", "content": "x2"},
        ])
        web = CallRecorder(returns=[])
        svc = make_svc(kb_retriever=kb, keyword_retriever=kw, free_ai_searcher=web)
        out = svc.retrieve_or_autofill("q")
        # 1 kb + 2 keyword = 3 >= threshold → 不联网
        self.assertEqual(out.counts, {"kb": 1, "keyword": 2, "web": 0})
        self.assertEqual(len(web.calls), 0)

    def test_web_exception_graceful_degrade(self):
        kb = CallRecorder(returns=[])
        kw = CallRecorder(returns=[])

        def boom(q):
            raise ImportError("No module named 'playwright'")

        svc = make_svc(kb_retriever=kb, keyword_retriever=kw, free_ai_searcher=boom)
        out = svc.retrieve_or_autofill("q")
        self.assertFalse(out.used_web)
        self.assertTrue(out.degraded, "联网抛错应标记 degraded")
        self.assertEqual(out.counts["web"], 0)
        self.assertTrue(any("web_search_failed" in r for r in out.reasons))

    def test_web_empty_result_degraded(self):
        kb = CallRecorder(returns=[])
        kw = CallRecorder(returns=[])
        web = CallRecorder(returns=[])  # 不抛错但返回空
        svc = make_svc(kb_retriever=kb, keyword_retriever=kw, free_ai_searcher=web)
        out = svc.retrieve_or_autofill("q")
        self.assertFalse(out.used_web)
        self.assertTrue(out.degraded)

    def test_kb_or_keyword_exception_returns_empty_not_raise(self):
        def bad_kb(q):
            raise RuntimeError("kb broken")

        def bad_kw(q):
            raise RuntimeError("kw broken")

        web = CallRecorder(returns=[{"title": "w", "url": "u", "content": "c"}])
        svc = make_svc(kb_retriever=bad_kb, keyword_retriever=bad_kw, free_ai_searcher=web)
        out = svc.retrieve_or_autofill("q")
        # kb/kw 异常被吞，记 reasons；web 仍正常补全
        self.assertEqual(out.counts["kb"], 0)
        self.assertEqual(out.counts["keyword"], 0)
        self.assertTrue(out.used_web)


# ---------------------------------------------------------------------------
# AI 只整理润色、禁止自编（护栏）
# ---------------------------------------------------------------------------
class TestPolisherGuard(unittest.TestCase):

    def test_polisher_receives_exact_context(self):
        captured = {}

        def fake_polisher(query, context, sources):
            captured["context"] = context
            captured["sources"] = sources
            return "POLISHED:" + context

        kb = CallRecorder(returns=[
            {"title": "T1", "url": "https://a/1", "content": "BODY_AAA", "source": "KB"},
        ])
        svc = make_svc(kb_retriever=kb, keyword_retriever=CallRecorder(returns=[]),
                       free_ai_searcher=CallRecorder(returns=[]))
        out = svc.answer_with_sources("q", polisher=fake_polisher)
        # polisher 收到的 context 必须 == build_context(results)，不得夹带外部事实
        expected = build_context([SearchResult(title="T1", url="https://a/1",
                                               content="BODY_AAA", source="KB",
                                               retrieved_via=RETRIEVED_VIA_KB)])
        self.assertEqual(captured["context"], expected)
        self.assertIn("BODY_AAA", out["answer"])

    def test_no_context_no_polisher_call(self):
        polisher_called = []

        def naughty_polisher(query, context, sources):
            polisher_called.append(True)
            return "我瞎编的内容"

        kb = CallRecorder(returns=[])
        kw = CallRecorder(returns=[])
        web = CallRecorder(returns=[])  # 联网也空
        svc = make_svc(kb_retriever=kb, keyword_retriever=kw, free_ai_searcher=web)
        out = svc.answer_with_sources("q", polisher=naughty_polisher)
        self.assertEqual(polisher_called, [], "无上下文时绝不允许 polisher 自编")
        self.assertEqual(out["answer"], POLICY_INSUFFICIENT)
        self.assertEqual(out["context"], "")
        self.assertFalse(out["fabricated"])

    def test_no_polisher_default_just_merges(self):
        kb = CallRecorder(returns=[
            {"title": "T", "url": "https://a/1", "content": "BODY", "source": "KB"},
        ])
        svc = make_svc(kb_retriever=kb, keyword_retriever=CallRecorder(returns=[]),
                       free_ai_searcher=CallRecorder(returns=[]))
        out = svc.answer_with_sources("q")
        self.assertIn("BODY", out["answer"])
        self.assertFalse(out["fabricated"])


# ---------------------------------------------------------------------------
# auto_fill_for_mode：两模式触发条件差异
# ---------------------------------------------------------------------------
class TestAutoFillForMode(unittest.TestCase):

    def _svc_with_web_recorder(self):
        web = CallRecorder(returns=[{"title": "w", "url": "https://w/1",
                                     "content": "web body", "source": "免费AI"}])
        svc = make_svc(kb_retriever=CallRecorder(returns=[]),
                       keyword_retriever=CallRecorder(returns=[]),
                       free_ai_searcher=web)
        return svc, web

    def test_emotion_kb_snippets_empty_triggers_web(self):
        svc, web = self._svc_with_web_recorder()
        out = svc.auto_fill_for_mode("失恋", mode="emotion_consult", kb_snippets=[])
        self.assertEqual(len(web.calls), 1, "kb_snippets 空 → 应触发联网补全")
        self.assertTrue(out["used_web"])
        self.assertEqual(out["mode"], "emotion_consult")
        self.assertIn("web body", out["context"])

    def test_emotion_kb_snippets_enough_no_web(self):
        svc, web = self._svc_with_web_recorder()
        snippets = [
            {"title": "a", "url": "https://kb/1", "content": "c1"},
            {"title": "b", "url": "https://kb/2", "content": "c2"},
        ]
        out = svc.auto_fill_for_mode("失恋", mode="emotion_consult", kb_snippets=snippets)
        self.assertEqual(len(web.calls), 0, "已有>=threshold条kb片段 → 不联网")
        self.assertFalse(out["used_web"])
        self.assertIn("c1", out["context"])

    def test_chat_scripts_text_empty_triggers_web(self):
        svc, web = self._svc_with_web_recorder()
        out = svc.auto_fill_for_mode("分析她这话", mode="chat_analysis", scripts_text="")
        self.assertEqual(len(web.calls), 1, "scripts_text 空 → 应触发联网补全")
        self.assertTrue(out["used_web"])
        self.assertEqual(out["mode"], "chat_analysis")

    def test_chat_scripts_text_present_no_web(self):
        svc, web = self._svc_with_web_recorder()
        out = svc.auto_fill_for_mode("分析", mode="chat_analysis",
                                     scripts_text="她今晚回消息很慢")
        self.assertEqual(len(web.calls), 0, "已有脚本文本 → 不联网")
        self.assertFalse(out["used_web"])
        self.assertIn("她今晚回消息很慢", out["context"])

    def test_unknown_mode_raises(self):
        svc, _ = self._svc_with_web_recorder()
        with self.assertRaises(ValueError):
            svc.auto_fill_for_mode("q", mode="unknown_mode")


if __name__ == "__main__":
    unittest.main()
