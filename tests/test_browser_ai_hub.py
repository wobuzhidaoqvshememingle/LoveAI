# -*- coding: utf-8 -*-
"""BrowserAIHub 单元测试（全部离线：Fake Page/Browser + 固定 rng + 假时钟，
不开真实浏览器、不联网、不 import playwright）。"""
import os, random, sys, unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import services.browser_ai_hub as bai  # noqa: E402
from services.browser_ai_hub import (  # noqa: E402
    BROWSER_AI_SOURCES, BrowserAIAllFailed, BrowserAIHub, BrowserUnavailable,
    compute_deadline, is_valid_answer,
)

# --------------------------------------------------------------------------- #
# 14 个迁移源（name/url 逐字来自 crawler.FREE_AI_LIST）
# --------------------------------------------------------------------------- #
MIGRATED_14 = [
    ("博查AI搜索", "https://www.bochaai.com", "search", 10, False),
    ("密塔AI搜索", "https://metaso.cn", "search", 12, False),
    ("天工", "https://www.tiangong.cn", "search", 12, False),
    ("Kimi", "https://kimi.moonshot.cn", "chat", 15, False),
    ("腾讯元宝", "https://yuanbao.tencent.com", "chat", 15, False),
    ("通义千问", "https://www.tongyi.com", "chat", 12, False),
    ("智谱清言", "https://chatglm.cn", "chat", 12, False),
    ("DeepSeek", "https://chat.deepseek.com", "chat", 15, False),
    ("阶跃StepChat", "https://www.stepchat.com", "chat", 12, False),
    ("文心一言", "https://yiyan.baidu.com", "chat", 12, False),
    ("商汤商量", "https://chat.sensetime.com", "chat", 12, False),
    ("讯飞星火", "https://xinghuo.xfyun.cn", "chat", 12, True),
    ("豆包", "https://www.doubao.com", "chat", 15, True),
    ("火山方舟",
     "https://console.volcengine.com/ark/region:ark+cn-beijing/experience",
     "chat", 10, True),
]

# ---- 正文样本 ----
QUERY = "恋爱聊天技巧"
# 密塔式真回答（>300 字、含查询词、标点正常）
REAL_ANSWER = (
    "密塔AI搜索结果：关于恋爱聊天技巧，综合多篇高赞回答整理如下。"
    "第一，开场白要轻松自然，避免查户口式连续提问，先用一句生活化观察开场。"
    "第二，多用开放式提问引导对方表达，比如问她最近开心的事，而不是只问是不是。"
    "第三，学会倾听并回应对方的情绪价值，她吐槽同事时先共情，再给建议。"
    "第四，话题递进要循序渐进，从兴趣爱好慢慢过渡到价值观和未来规划。"
    "第五，暧昧期不要急于表白，多用推拉制造张力，让关系自然升温。"
    "第六，遇到冷场时可以分享一个有趣的见闻，而不是连续追问对方；"
    "幽默是最好的润滑剂，但不要过度自黑或硬讲笑话。"
    "第七，观察她回复的长度和速度，同步调整自己的投入度，节奏对等关系才舒服。"
    "此外，聊天节奏也很重要：不要秒回每一条消息，保持自己的生活节奏；"
    "偶尔分享自己的日常，让她有参与感；遇到意见分歧时先接住情绪，再讨论事情本身。"
    "持续练习一段时间，对话质量会明显提升。"
)
# 博查式导航页（>300 字但全是导航词、几乎无标点）
_NAV_BLOCK = ("首页 热点 热搜榜 分类 发现 产品 下载客户端 关于我们 备案信息 "
              "帮助中心 登录 注册 科技 财经 体育 娱乐 更多 ")
NAV_PAGE = _NAV_BLOCK * 6
# 登录引导页
_LOGIN_BLOCK = "扫码登录 立即登录 微信登录 手机号登录 下载App 注册账号 "
LOGIN_PAGE = _LOGIN_BLOCK * 12


class FakeElement:
    def __init__(self, page):
        self._page = page

    def fill(self, text):
        self._page.filled = text

    def press(self, key):
        self._page.pressed = key


class FakePage:
    """Page duck-type：行为由 FakeBrowser._sites 按 url 决定。"""

    def __init__(self, browser):
        self._browser = browser
        self._url = None
        self.closed = False
        self.filled = None
        self.pressed = None

    def goto(self, url, **kw):
        self._url = url
        site = self._browser._sites.get(url, {})
        err = site.get("goto_error")
        if isinstance(err, Exception):
            raise err

    def query_selector(self, sel):
        site = self._browser._sites.get(self._url, {})
        return FakeElement(self) if sel in site.get("selectors_present", []) else None

    def inner_text(self, sel):
        return self._browser._sites.get(self._url, {}).get("body", "")

    def title(self):
        return ""

    def close(self):
        self.closed = True


class FakeBrowser:
    """sites: {url: {goto_error?:Exception, selectors_present?:[...], body?:str}}"""

    def __init__(self, sites):
        self._sites = sites
        self.pages = []
        self.closed = False

    def new_page(self):
        p = FakePage(self)
        self.pages.append(p)
        return p

    def close(self):
        self.closed = True


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_hub(sites, seed=20261009, **cfg):
    return BrowserAIHub(
        config=cfg or None,
        browser_factory=lambda: FakeBrowser(sites),
        rng=random.Random(seed),
        clock=Clock(),
        sleeper=lambda s: None,
    )


class TestRegistry(unittest.TestCase):

    # 1) 14 源完整迁移且字段齐全；需登录源如实标注
    def test_14_sources_complete_and_fields(self):
        by_name = {s["name"]: s for s in BROWSER_AI_SOURCES}
        self.assertEqual(len(by_name), len(BROWSER_AI_SOURCES))
        for name, url, stype, wait, login in MIGRATED_14:
            self.assertIn(name, by_name, f"缺少迁移源 {name}")
            s = by_name[name]
            for field in ("sid", "name", "url", "type", "wait", "requires_login",
                          "note", "input_selectors", "content_selector"):
                self.assertIn(field, s, f"{name} 缺字段 {field}")
            self.assertEqual(s["url"], url)
            self.assertEqual(s["type"], stype)
            self.assertEqual(s["wait"], wait)
            self.assertEqual(s["requires_login"], login)
            self.assertIsInstance(s["input_selectors"], list)
            self.assertGreaterEqual(len(s["input_selectors"]), 1)
        login_sources = {s["name"] for s in BROWSER_AI_SOURCES if s["requires_login"]}
        self.assertEqual(login_sources, {"讯飞星火", "豆包", "火山方舟"})

    # 方案1 声明源存在，同一 type 枚举
    def test_declared_sources_present(self):
        by_name = {s["name"]: s for s in BROWSER_AI_SOURCES}
        self.assertIn("Perplexity", by_name)
        self.assertIn("Gemini", by_name)
        self.assertIn("浏览器自带AI", by_name)
        self.assertEqual(by_name["浏览器自带AI"]["type"], "browser_builtin")


class TestPlaywrightMissing(unittest.TestCase):

    # 2) playwright 缺失：模块照常 import；chat 优雅抛 BrowserUnavailable，
    #    search 优雅返回空（不伪造）
    def test_playwright_missing_graceful(self):
        old = bai.PLAYWRIGHT_AVAILABLE
        bai.PLAYWRIGHT_AVAILABLE = False
        try:
            hub = BrowserAIHub()  # 无注入工厂 → 走真实工厂
            with self.assertRaises(BrowserUnavailable):
                hub.chat(query="hi")
            self.assertEqual(hub.search("hi"), [])
            self.assertEqual(hub.status()["overall"], "unavailable")
        finally:
            bai.PLAYWRIGHT_AVAILABLE = old


class TestRotationOrder(unittest.TestCase):

    # 3) 固定 rng：密塔最高优先、search 型整体先于 chat/browser_builtin
    def test_metaso_first_and_search_first(self):
        hub = make_hub({})
        order = [s["name"] for s in hub.plan_order()]
        self.assertEqual(order[0], "密塔AI搜索")
        types = [s["type"] for s in hub.plan_order()]
        last_search = max(i for i, t in enumerate(types) if t == "search")
        first_non_search = min(i for i, t in enumerate(types) if t != "search")
        self.assertLess(last_search, first_non_search, "search 型必须整体在前")

    # 同一种子顺序确定
    def test_order_deterministic(self):
        o1 = [s["name"] for s in make_hub({}).plan_order()]
        o2 = [s["name"] for s in make_hub({}).plan_order()]
        self.assertEqual(o1, o2)


class TestFailover(unittest.TestCase):

    # 4) 密塔异常 → 天工/博查导航页判无效 → 转移到 Kimi 成功；attempts 全程记录
    def test_error_then_invalid_then_success(self):
        sites = {
            "https://metaso.cn": {"goto_error": Exception("timeout boom")},
            "https://www.tiangong.cn": {"selectors_present": ["textarea"],
                                        "body": NAV_PAGE},
            "https://www.bochaai.com": {"selectors_present": ["textarea"],
                                        "body": NAV_PAGE},
            "https://kimi.moonshot.cn": {"selectors_present": ["textarea"],
                                        "body": REAL_ANSWER},
        }
        browser = FakeBrowser(sites)
        hub = BrowserAIHub(config=None, browser_factory=lambda: browser,
                           rng=random.Random(20261009), clock=Clock(),
                           sleeper=lambda s: None)
        out = hub.chat(query=QUERY)
        self.assertEqual(out["_meta"]["source"], "Kimi")
        attempts = out["_meta"]["attempts"]
        self.assertEqual(attempts[0]["name"], "密塔AI搜索")
        self.assertEqual(attempts[0]["status"], "error")
        # 博查/天工（无论第几个被轮到）必须被判导航页无效
        for nm in ("博查AI搜索", "天工"):
            rec = [a for a in attempts if a["name"] == nm][0]
            self.assertEqual(rec["status"], "invalid")
            self.assertEqual(rec["reason"], "nav_boilerplate")
        kimi = [a for a in attempts if a["name"] == "Kimi"][0]
        self.assertEqual(kimi["status"], "ok")
        # 每个用过的页面都被关闭、浏览器最终关闭
        self.assertTrue(all(p.closed for p in browser.pages))
        self.assertTrue(browser.closed)
        # 成功即返回，不继续尝试后续源
        ok_attempts = [a for a in attempts if a["status"] == "ok"]
        self.assertEqual(len(ok_attempts), 1)

    # 首个源即成功：密塔直接命中
    def test_metaso_hits_first(self):
        sites = {"https://metaso.cn": {"selectors_present": ["textarea"],
                                       "body": REAL_ANSWER}}
        hub = make_hub(sites)
        out = hub.chat(query=QUERY)
        self.assertEqual(out["_meta"]["source"], "密塔AI搜索")
        attempts = out["_meta"]["attempts"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "ok")

    # 5) 全部失败：chat 抛 typed 异常且带 attempts；search 返回 []
    def test_all_failed_typed(self):
        hub = make_hub({})  # 任何 url 都没有输入框 → 全 invalid
        with self.assertRaises(BrowserAIAllFailed) as cm:
            hub.chat(query=QUERY)
        self.assertTrue(len(cm.exception.attempts) > 10)
        self.assertTrue(all(a["status"] == "invalid" for a in cm.exception.attempts))
        self.assertEqual(make_hub({}).search(QUERY), [])


class TestTimeout(unittest.TestCase):

    # 每源超时纯函数 + 硬上限 ≤15s
    def test_deadline_and_cap(self):
        self.assertEqual(compute_deadline(100.0, 12.0), 112.0)
        self.assertEqual(BrowserAIHub()._per_source_timeout(), 12.0)
        hub = BrowserAIHub(config={"browser_ai.per_source_timeout": "20"})
        self.assertLessEqual(hub._per_source_timeout(), 15.0)
        self.assertEqual(hub._per_source_timeout(), 15.0)


class TestValidity(unittest.TestCase):

    # 6) 有效性判断四类：密塔式长正文有效 / 博查导航页无效 / 过短无效 / 登录引导无效
    def test_validity_cases(self):
        ok, reason = is_valid_answer(REAL_ANSWER, query=QUERY)
        self.assertTrue(ok); self.assertEqual(reason, "ok")

        ok, reason = is_valid_answer(NAV_PAGE, source="博查AI搜索", query=QUERY)
        self.assertFalse(ok); self.assertEqual(reason, "nav_boilerplate")

        ok, reason = is_valid_answer("只有一句话", query=QUERY)
        self.assertFalse(ok); self.assertEqual(reason, "too_short")

        ok, reason = is_valid_answer(LOGIN_PAGE, source="豆包", query=QUERY)
        self.assertFalse(ok); self.assertEqual(reason, "login_gate")

    # 长度阈值可配
    def test_min_len_configurable(self):
        ok, reason = is_valid_answer("一百二十字以上的正文内容。" * 15, min_len=100)
        self.assertTrue(ok, reason)


class TestWireFormats(unittest.TestCase):

    def setUp(self):
        self.sites = {"https://metaso.cn": {"selectors_present": ["textarea"],
                                            "body": REAL_ANSWER}}

    # 7) chat 返回 OpenAI ChatCompletion 形态 + _meta 带来源
    def test_chat_openai_shape(self):
        out = make_hub(self.sites).chat(query=QUERY)
        for k in ("id", "object", "created", "model", "choices", "_meta"):
            self.assertIn(k, out)
        self.assertEqual(out["object"], "chat.completion")
        ch = out["choices"][0]
        self.assertEqual(ch["index"], 0)
        self.assertEqual(ch["message"]["role"], "assistant")
        self.assertIn(QUERY, ch["message"]["content"])
        self.assertEqual(ch["finish_reason"], "stop")
        self.assertEqual(out["_meta"]["source"], "密塔AI搜索")
        self.assertEqual(out["_meta"]["source_url"], "https://metaso.cn")
        self.assertFalse(out["_meta"]["requires_login"])
        self.assertIn("attempts", out["_meta"])

    # messages 入参：取最后一条 user 消息
    def test_chat_messages_input(self):
        out = make_hub(self.sites).chat(messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": QUERY}])
        self.assertIn(QUERY, out["choices"][0]["message"]["content"])

    # 8) search() 固定线格式：严格 {title, url, content, source}
    def test_search_wire_format(self):
        res = make_hub(self.sites).search(QUERY)
        self.assertIsInstance(res, list)
        self.assertEqual(len(res), 1)
        item = res[0]
        self.assertEqual(set(item.keys()), {"title", "url", "content", "source"})
        self.assertEqual(item["title"], f"密塔AI搜索:{QUERY}")
        self.assertEqual(item["url"], "https://metaso.cn")
        self.assertEqual(item["source"], "密塔AI搜索")
        self.assertTrue(item["content"].startswith("密塔AI搜索结果"))
        self.assertLessEqual(len(item["content"]), 3000)


class TestStatusAndList(unittest.TestCase):

    # list_sources / status：字段齐全；需登录源默认 needs_login
    def test_list_and_status(self):
        hub = make_hub({
            "https://metaso.cn": {"selectors_present": ["textarea"],
                                  "body": REAL_ANSWER}})
        lst = hub.list_sources()
        self.assertTrue(any(s["display"] == "密塔AI搜索" for s in lst))
        by_id = {s["source_id"]: s for s in lst}
        for k in ("source_id", "kind", "display", "models", "openai_base_url",
                  "requires_login", "zero_key", "status", "confidence",
                  "notes", "source_url"):
            self.assertIn(k, by_id["metaso"])
        self.assertTrue(by_id["doubao"]["requires_login"])
        st = hub.status()
        self.assertIn("sources", st)
        self.assertEqual(st["sources"]["豆包"]["status"], "needs_login")
        # 密塔成功一次后状态变 available
        hub.chat(query=QUERY)
        self.assertEqual(hub.status()["sources"]["密塔AI搜索"]["status"],
                         "available")


if __name__ == "__main__":
    unittest.main()
