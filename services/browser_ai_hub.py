# -*- coding: utf-8 -*-
"""
浏览器 AI 中枢（BrowserAIHub）——LoveAI v3.2.0 需求 R3.2-02（方案1 + 方案2）。

方案1：把「浏览器自带 AI」以及 Perplexity / Gemini 等网页助手登记为可接入源，
        与 14 个迁移源共用同一套 Page/Browser 驱动接口（默认 enabled，可配置关闭）。
方案2：Playwright 持久化上下文（launch_persistent_context）复用登录态/会话，
        user_data_dir 与 headless 均可配。

迁移自 2026-10-01 crawler.py 的 free_ai_search（逐字迁移 name/url/wait/type/note）：
    random.shuffle 随机轮换 → new_page → goto(networkidle, timeout)
    → 多候选选择器找输入框（textarea / input[type=text]）→ fill(query) → press(Enter)
    → 按每源 wait 等待 AI 出回答 → inner_text('body') → len>300 才算有效
    → 异常 / 无效自动换下一个。

旧实测结论已固化进有效性判断与优先级：
    - 密塔AI搜索（搜索型）实测成功返回约 1189 字真回答 → 搜索型最高优先；
    - 博查AI搜索能打开但返回的是导航内容而非真回答 → 判 nav_boilerplate 无效；
    - 天工当时输入框不可见 → 输入框多候选选择器 + 失败转移；
    - 单源约 10~15s → 每源超时硬上限 15s。

边界（R3.2-05）：本期只新增本文件 + 离线测试，不接线 FreeModelRouter / LLMGateway /
设置 UI / main / rag / application。playwright 仅在工厂函数内延迟 import；
venv 未装 playwright 时本模块照常 import，暴露 PLAYWRIGHT_AVAILABLE=False，
调用 chat/search 时优雅抛 BrowserUnavailable / 返回空结果（数字诚实）。

────────────────────────────────────────────────────────────────────────
R3.3-84｜CDP 挂载 + 节流（2026-10-16 融入自 EAgent 思路 / github.com/YaoGaoChaoLe/EAgent）
    该项目的做法：用 Edge 的调试模式起 CDP，脚本连上去模拟人操作 DeepSeek 网页版，
    拿到 0 token 的模型输出；并自加「字符长度限制 + 几秒延迟执行」降低被封概率。
    与本模块比对后，只有两点是本模块原本缺的，故只补这两点（其余本模块已有且更全：
    17 源 > 它 1 源、OpenAI 兼容封装、风控识别降级、多 profile 轮询）：
      ① CDP 挂载（browser_ai.cdp_url）：connect_over_cdp 挂到【用户已经在用的浏览器】，
         直接复用其现有登录态，零额外登录、零新 profile；与既有
         launch_persistent_context（独立 profile，需单独登录一次）并存，CDP 优先。
         CDP 模式下 close() 只断开调试连接，绝不关闭用户正在使用的浏览器窗口。
      ② 节流（browser_ai.max_query_chars / browser_ai.min_interval）：把「限长 + 限速」
         做成事前动作，降低触发风控的概率；与既有 detect_risk_control（事后识别并降级）互补。
    三个新键默认全部关闭 / 不限（"" / 0 / 0），不改变原有行为。
────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import logging
import random
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# playwright 可用性探测：真正的 sync_playwright 只在工厂函数内延迟 import
# --------------------------------------------------------------------------- #
def _detect_playwright() -> bool:
    try:
        import playwright.sync_api  # noqa: F401  仅探测，不启动浏览器
        return True
    except Exception:  # 未安装 / 无浏览器运行时 → 优雅降级
        return False


PLAYWRIGHT_AVAILABLE: bool = _detect_playwright()


class BrowserUnavailable(RuntimeError):
    """playwright 未安装 / 浏览器无法启动 / 未注入浏览器工厂时抛出。"""


class BrowserAIAllFailed(RuntimeError):
    """全部源均尝试失败（异常 / 超时 / 无效）。attempts 携带每源尝试状态。"""

    def __init__(self, message: str, attempts: Optional[list] = None) -> None:
        super().__init__(message)
        self.attempts: list = attempts or []


# --------------------------------------------------------------------------- #
# 默认输入框选择器候选（旧实测：天工输入框可能不可见，故给多候选 + 失败转移）
# --------------------------------------------------------------------------- #
_DEFAULT_INPUT_SELECTORS = [
    "textarea",
    "div[contenteditable='true']",
    "input[type='text']",
    "input[type='search']",
]
_DEFAULT_CONTENT_SELECTOR = "body"

DEFAULT_MIN_ANSWER_LEN = 300     # 沿用旧实现 len(answer) > 300 的经验阈值（可配）
DEFAULT_PER_SOURCE_TIMEOUT = 12.0  # 秒
MAX_PER_SOURCE_TIMEOUT = 15.0    # 硬上限：旧实测单源 10~15s


# --------------------------------------------------------------------------- #
# 14 源注册表（name/url/wait/type/note 逐字迁移自 crawler.FREE_AI_LIST）
# 另含方案1 声明的可接入源（Perplexity / Gemini / 浏览器自带AI）。
# --------------------------------------------------------------------------- #
BROWSER_AI_SOURCES = [
    # ---- 搜索型（search）：排序优先，密塔最高优先 ----
    {"sid": "metaso", "name": "密塔AI搜索", "url": "https://metaso.cn",
     "type": "search", "wait": 12, "requires_login": False,
     "note": "2026-10-01 实测成功返回约1189字真回答，搜索型首选",
     "input_selectors": ["textarea", "input[type='text']"],
     "content_selector": "body"},
    {"sid": "bochaai", "name": "博查AI搜索", "url": "https://www.bochaai.com",
     "type": "search", "wait": 10, "requires_login": False,
     "note": "能打开但实测常返回导航页而非真回答，须靠有效性过滤判无效",
     "input_selectors": ["textarea", "input[type='text']", "input[type='search']"],
     "content_selector": "body"},
    {"sid": "tiangong", "name": "天工", "url": "https://www.tiangong.cn",
     "type": "search", "wait": 12, "requires_login": False,
     "note": "实测输入框可能不可见，多候选选择器 + 失败转移",
     "input_selectors": ["textarea", "input[type='text']",
                         "div[contenteditable='true']", "input[type='search']"],
     "content_selector": "body"},
    # ---- 对话型（chat） ----
    {"sid": "kimi", "name": "Kimi", "url": "https://kimi.moonshot.cn",
     "type": "chat", "wait": 15, "requires_login": False, "note": "",
     "input_selectors": list(_DEFAULT_INPUT_SELECTORS),
     "content_selector": "body"},
    {"sid": "yuanbao", "name": "腾讯元宝", "url": "https://yuanbao.tencent.com",
     "type": "chat", "wait": 15, "requires_login": False, "note": "",
     "input_selectors": list(_DEFAULT_INPUT_SELECTORS),
     "content_selector": "body"},
    {"sid": "tongyi", "name": "通义千问", "url": "https://www.tongyi.com",
     "type": "chat", "wait": 12, "requires_login": False, "note": "",
     "input_selectors": list(_DEFAULT_INPUT_SELECTORS),
     "content_selector": "body"},
    {"sid": "chatglm", "name": "智谱清言", "url": "https://chatglm.cn",
     "type": "chat", "wait": 12, "requires_login": False, "note": "",
     "input_selectors": list(_DEFAULT_INPUT_SELECTORS),
     "content_selector": "body"},
    {"sid": "deepseek", "name": "DeepSeek", "url": "https://chat.deepseek.com",
     "type": "chat", "wait": 15, "requires_login": False, "note": "",
     "input_selectors": list(_DEFAULT_INPUT_SELECTORS),
     "content_selector": "body"},
    {"sid": "stepchat", "name": "阶跃StepChat", "url": "https://www.stepchat.com",
     "type": "chat", "wait": 12, "requires_login": False, "note": "",
     "input_selectors": list(_DEFAULT_INPUT_SELECTORS),
     "content_selector": "body"},
    {"sid": "yiyan", "name": "文心一言", "url": "https://yiyan.baidu.com",
     "type": "chat", "wait": 12, "requires_login": False, "note": "",
     "input_selectors": list(_DEFAULT_INPUT_SELECTORS),
     "content_selector": "body"},
    {"sid": "sensetime", "name": "商汤商量", "url": "https://chat.sensetime.com",
     "type": "chat", "wait": 12, "requires_login": False, "note": "",
     "input_selectors": list(_DEFAULT_INPUT_SELECTORS),
     "content_selector": "body"},
    # ---- 需登录（如实标注），排在后面 ----
    {"sid": "xinghuo", "name": "讯飞星火",
     "url": "https://xinghuo.xfyun.cn",
     "type": "chat", "wait": 12, "requires_login": True, "note": "需登录",
     "input_selectors": list(_DEFAULT_INPUT_SELECTORS),
     "content_selector": "body"},
    {"sid": "doubao", "name": "豆包", "url": "https://www.doubao.com",
     "type": "chat", "wait": 15, "requires_login": True, "note": "需登录",
     "input_selectors": list(_DEFAULT_INPUT_SELECTORS),
     "content_selector": "body"},
    {"sid": "volcengine-ark", "name": "火山方舟",
     "url": "https://console.volcengine.com/ark/region:ark+cn-beijing/experience",
     "type": "chat", "wait": 10, "requires_login": True, "note": "需登录",
     "input_selectors": list(_DEFAULT_INPUT_SELECTORS),
     "content_selector": "body"},
    # ---- 方案1 声明的可接入源（同一驱动接口，默认 enabled 可配，需现场复核）----
    {"sid": "perplexity", "name": "Perplexity",
     "url": "https://www.perplexity.ai",
     "type": "search", "wait": 12, "requires_login": False,
     "note": "方案1声明接入源；国内网络可达性与选择器需现场复核",
     "input_selectors": ["textarea", "input[type='text']"],
     "content_selector": "body"},
    {"sid": "gemini", "name": "Gemini", "url": "https://gemini.google.com",
     "type": "chat", "wait": 12, "requires_login": False,
     "note": "方案1声明接入源；国内网络可达性与选择器需现场复核",
     "input_selectors": list(_DEFAULT_INPUT_SELECTORS),
     "content_selector": "body"},
    {"sid": "builtin-ai", "name": "浏览器自带AI", "url": "about:builtin-ai",
     "type": "browser_builtin", "wait": 5, "requires_login": False,
     "note": "Edge/Chrome 内置AI 能力开放后接入；当前为声明占位，"
             "goto 会自然失败转移，不伪造回答",
     "input_selectors": [],
     "content_selector": "body"},
]

# 方案1 声明源（便于配置开关；默认参与轮换，browser_ai.include_declared=false 关闭）
DECLARED_SOURCE_NAMES = ("Perplexity", "Gemini", "浏览器自带AI")


# --------------------------------------------------------------------------- #
# 内容有效性纯函数：区分真回答 vs 导航页 / 过短 / 登录墙 / 跑题
# --------------------------------------------------------------------------- #
_LOGIN_GATE_MARKERS = [
    "扫码登录", "立即登录", "登录后", "下载App", "下载 App",
    "手机号登录", "微信登录", "注册账号",
]
_NAV_MARKERS = [
    "首页", "导航", "热点", "热搜", "榜单", "分类", "发现",
    "下载客户端", "关于我们", "备案", "帮助中心", "产品",
]


def compute_deadline(start: float, timeout: float) -> float:
    """纯函数：截止时刻 = 起始时刻 + 超时（秒）。"""
    return float(start) + float(timeout)


def is_valid_answer(text: str, source: Optional[str] = None,
                    query: Optional[str] = None,
                    min_len: int = DEFAULT_MIN_ANSWER_LEN) -> tuple:
    """判断正文是否为「真回答」。返回 (ok: bool, reason: str)。

    reason ∈ ok / too_short / login_gate / nav_boilerplate / irrelevant。
    """
    text = (text or "").strip()
    if len(text) < min_len:
        return False, "too_short"

    # 登录引导铺满正文 → 登录墙（登录/扫码/下载App 等标记密度过高）
    login_hits = sum(text.count(m) for m in _LOGIN_GATE_MARKERS)
    if login_hits and login_hits / max(len(text), 1) >= 0.02:
        return False, "login_gate"

    # 导航页：导航/菜单词密集、几乎没有句子标点（博查式导航内容）
    nav_hits = sum(text.count(m) for m in _NAV_MARKERS)
    punct = sum(text.count(p) for p in "。！？；，、,.!?;:")
    if nav_hits >= 5 and punct < len(text) * 0.01:
        return False, "nav_boilerplate"

    # 与 query 的相关性：整句命中或任一分词命中
    if query and query.strip():
        q = query.strip()
        if q not in text:
            terms = [t for t in re.split(r"[\s，。？！,.!?;；、]+", q) if len(t) >= 2]
            if terms and not any(t in text for t in terms):
                return False, "irrelevant"
    return True, "ok"


# --------------------------------------------------------------------------- #
# 反爬 / 风控检测（R3.3-05）
# --------------------------------------------------------------------------- #
# 说明（数字诚实）：豆包/抖音系的 a_bogus、各站的 msToken 等请求签名，都是目标站
# 前端 JS 在浏览器内现算的。我们用 Playwright 驱动【真实页面】，networkidle 等待页面
# JS 跑完，签名会由页面自己生成——我们不伪造、也无法离线伪造这些签名。
# 能做的反爬对抗只有：① 注入基础 stealth 参数降低自动化指纹；
# ② 一旦站点仍返回验证码/人机校验/频率限制页，就识别出来并把该源【降级为不可用】，
#    在 status() 里如实说明，而不是硬刚风控。
_RISK_MARKERS = [
    "安全验证", "安全检测", "滑动验证", "滑块验证", "拖动滑块", "拖动拼图",
    "人机验证", "验证码", "请完成验证", "请滑动", "拼图形验证码",
    "访问过于频繁", "请求过于频繁", "操作过于频繁", "稍后再试",
    "captcha", "verify you are human", "are you a human", "robot check",
]


def detect_risk_control(text: str) -> tuple:
    """检测正文是否为风控/反爬拦截页（验证码/人机校验/频率限制）。

    Returns:
        (blocked: bool, reason: str)。命中返回 (True, "risk_control")，否则 (False, "")。
    """
    t = (text or "")
    if not t:
        return False, ""
    low = t.lower()
    hits = sum(low.count(m.lower()) for m in _RISK_MARKERS)
    if hits >= 1:
        return True, "risk_control"
    return False, ""


# --------------------------------------------------------------------------- #
# 注册描述符（未来映射 free_model_router.Candidate；本期不 import free_model_router）
# --------------------------------------------------------------------------- #
@dataclass
class SourceDescriptor:
    source_id: str
    kind: str = "web_ai"
    display: str = ""
    models: list = field(default_factory=list)
    openai_base_url: Optional[str] = None
    requires_login: bool = False
    zero_key: bool = True
    status: str = "unknown"          # unknown/available/needs_login/failed/unavailable
    confidence: float = 0.5
    notes: str = ""
    source_url: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# 真实浏览器工厂（playwright 仅在此函数内延迟 import；方案2 复用登录态）
# --------------------------------------------------------------------------- #
class _RealBrowser:
    """对 playwright Browser / PersistentContext 的薄封装（duck-type 协议）。"""

    def __init__(self, pw, handle) -> None:
        self._pw = pw
        self._handle = handle

    def new_page(self):
        return self._handle.new_page()

    def close(self) -> None:
        try:
            self._handle.close()
        finally:
            self._pw.stop()


class _CdpBrowser:
    """CDP 挂载封装：连到【用户已经开着的】浏览器，复用其真实登录态。

    与 _RealBrowser 的差别：
      - 页面从浏览器已有的 context 里开（用户 cookie / 登录态在那里），
        没有 context 时才新建一个；
      - close() 只 stop playwright 断开调试连接，**不调用 browser.close()**：
        对 CDP 连接调 close() 会连带清理该浏览器上的上下文，可能打断用户
        正在使用的窗口。用户的浏览器必须原样留着。
    """

    def __init__(self, pw, browser) -> None:
        self._pw = pw
        self._browser = browser

    def new_page(self):
        contexts = list(getattr(self._browser, "contexts", []) or [])
        if contexts:
            return contexts[0].new_page()
        return self._browser.new_context().new_page()

    def close(self) -> None:
        try:
            self._pw.stop()
        except Exception:
            pass


def _launch_real_browser(user_data_dir: Optional[str] = None,
                         headless: bool = True,
                         cdp_url: Optional[str] = None):
    """启动 / 挂载浏览器。

    cdp_url 非空 → CDP 挂载用户已在运行的浏览器（复用其登录态，零额外登录）；
    user_data_dir 非空 → 持久化上下文复用登录态/会话（方案2）；
    都没有 → 开一个全新 Chromium（headless 由配置决定）。
    """
    from playwright.sync_api import sync_playwright  # 延迟 import

    # 基础 stealth：降低自动化指纹（a_bogus/msToken 仍由页面 JS 现算，这里只减被封概率）
    stealth_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
    ]
    p = sync_playwright().start()
    try:
        if cdp_url:
            # CDP 挂载：连到用户已用 --remote-debugging-port 打开的浏览器
            browser = p.chromium.connect_over_cdp(cdp_url)
            return _CdpBrowser(p, browser)
        if user_data_dir:
            handle = p.chromium.launch_persistent_context(
                user_data_dir, headless=headless, args=stealth_args)
        else:
            handle = p.chromium.launch(headless=headless, args=stealth_args)
        return _RealBrowser(p, handle)
    except Exception:
        p.stop()
        raise


# --------------------------------------------------------------------------- #
# BrowserAIHub
# --------------------------------------------------------------------------- #
class BrowserAIHub:
    """浏览器网页 AI 的 OpenAI 兼容中枢。

    构造函数全部依赖可注入（browser_factory / rng / clock / sleeper / config），
    离线测试用 Fake Page/Browser 驱动，不联网、不开真实浏览器。
    """

    def __init__(self,
                 config: Optional[dict] = None,
                 browser_factory: Optional[Callable[[], object]] = None,
                 rng: Optional[random.Random] = None,
                 clock: Optional[Callable[[], float]] = None,
                 sleeper: Optional[Callable[[float], None]] = None) -> None:
        self.config = config or {}
        self._browser_factory = browser_factory
        self._rng = rng or random.Random()
        self._clock = clock or time.time
        self._sleep = sleeper or time.sleep
        # 运行期健康表：name -> {status, confidence, error}
        self._runtime: dict = {}
        # 多账号轮询：持久化 profile(user_data_dir) 列表的轮转游标
        self._acct_idx: int = 0
        self._current_account: str = ""

    # ------------------------- dotted-key 配置 ------------------------- #
    def _flag(self, key: str, default: bool = True) -> bool:
        return str(self.config.get(key, "true" if default else "false")).strip().lower() \
            not in ("false", "0", "no", "off")

    def _cfg(self, key: str, default: str = "") -> str:
        v = self.config.get(key, default)
        return v if isinstance(v, str) else str(v)

    def _min_len(self) -> int:
        try:
            return int(float(self.config.get(
                "browser_ai.min_answer_len", str(DEFAULT_MIN_ANSWER_LEN))))
        except (TypeError, ValueError):
            return DEFAULT_MIN_ANSWER_LEN

    def _per_source_timeout(self) -> float:
        """每源超时（秒），硬上限 15s。"""
        try:
            t = float(self.config.get(
                "browser_ai.per_source_timeout", str(DEFAULT_PER_SOURCE_TIMEOUT)))
        except (TypeError, ValueError):
            t = DEFAULT_PER_SOURCE_TIMEOUT
        return min(t, MAX_PER_SOURCE_TIMEOUT)

    def _goto_timeout_ms(self) -> int:
        return int(self._per_source_timeout() * 1000)

    # ---- R3.3-84 CDP 挂载 + 节流（三键默认全关，不改变原行为）---- #
    def _cdp_url(self) -> str:
        """CDP 挂载地址（如 http://127.0.0.1:9222）。空 = 关闭，走原逻辑。"""
        return self._cfg("browser_ai.cdp_url", "").strip()

    def _max_query_chars(self) -> int:
        """单次发送的字符上限（防封限长）。0 = 不限。"""
        try:
            n = int(float(self.config.get("browser_ai.max_query_chars", "0")))
        except (TypeError, ValueError):
            n = 0
        return n if n > 0 else 0

    def _min_interval(self) -> float:
        """相邻两源之间的最小间隔秒数（防封限速）。0 = 不限。"""
        try:
            v = float(self.config.get("browser_ai.min_interval", "0"))
        except (TypeError, ValueError):
            v = 0.0
        return v if v > 0 else 0.0

    def _account_profiles(self) -> list:
        """多账号 profile 列表（每个=一个独立登录态目录）。

        支持 config["browser_ai.user_data_dirs"] 为 list 或逗号分隔字符串；
        未配置时回退到单值 browser_ai.user_data_dir。空则返回 []。
        """
        raw = self.config.get("browser_ai.user_data_dirs")
        dirs: list = []
        if isinstance(raw, (list, tuple)):
            dirs = [str(x).strip() for x in raw if str(x).strip()]
        elif isinstance(raw, str) and raw.strip():
            dirs = [x.strip() for x in raw.split(",") if x.strip()]
        if not dirs:
            single = self._cfg("browser_ai.user_data_dir", "").strip()
            if single:
                dirs = [single]
        return dirs

    @staticmethod
    def _initial_conf(src: dict) -> float:
        if src.get("requires_login"):
            return 0.3
        return 0.6 if src.get("type") == "search" else 0.5

    # ------------------------- 源池与轮换顺序 ------------------------- #
    def _pool(self) -> list:
        sources = [dict(s) for s in BROWSER_AI_SOURCES]
        if not self._flag("browser_ai.include_declared", True):
            sources = [s for s in sources if s["name"] not in DECLARED_SOURCE_NAMES]
        return sources

    def plan_order(self) -> list:
        """search 型优先、密塔最高优先；其余（含 search 里的博查/天工）按 rng 洗牌。"""
        sources = self._pool()
        search = [s for s in sources if s["type"] == "search"]
        rest = [s for s in sources if s["type"] != "search"]
        meta = [s for s in search if s["name"] == "密塔AI搜索"]
        other_search = [s for s in search if s["name"] != "密塔AI搜索"]
        self._rng.shuffle(other_search)
        self._rng.shuffle(rest)
        return meta + other_search + rest

    # ------------------------- 浏览器生命周期 ------------------------- #
    def _open_browser(self):
        if self._browser_factory is not None:
            self._current_account = "injected"
            return self._browser_factory()
        if not PLAYWRIGHT_AVAILABLE:
            raise BrowserUnavailable(
                "playwright 未安装：浏览器AI能力不可用（离线/未装环境优雅降级）。"
                "请随包分发 Playwright/Chromium（R3.2-06）后启用。")
        headless = self._flag("browser_ai.headless", True)
        cdp_url = self._cdp_url()
        if cdp_url:
            # CDP 挂载优先：直接借用户已在运行浏览器的登录态，不动 profile 目录
            self._current_account = f"cdp:{cdp_url}"
            return _launch_real_browser(headless=headless, cdp_url=cdp_url)
        # 多账号轮询：有 profile 列表则按游标轮换，复用各自已登录态
        profiles = self._account_profiles()
        if profiles:
            udd = profiles[self._acct_idx % len(profiles)]
            self._acct_idx += 1
        else:
            udd = self._cfg("browser_ai.user_data_dir", "").strip() or None
        self._current_account = udd or "ephemeral"
        return _launch_real_browser(user_data_dir=udd, headless=headless)

    # ------------------------- 单源驱动 ------------------------- #
    def _drive(self, browser, source: dict, query: str):
        """隔离在 Page duck-type 协议后的单源驱动。
        返回 (raw_text, status, reason)；status ∈ ok/invalid/error。"""
        page = browser.new_page()
        try:
            page.goto(source["url"], wait_until="networkidle",
                      timeout=self._goto_timeout_ms())
            # 多候选选择器找输入框（天工教训：可能不可见 → 找不到就判 invalid 转移）
            box = None
            for sel in (source.get("input_selectors") or _DEFAULT_INPUT_SELECTORS):
                box = page.query_selector(sel)
                if box is not None:
                    break
            if box is None:
                return None, "invalid", "no_input_selector_visible"
            # R3.3-84 防封限长：超长查询先截断再发送（0 = 不限）
            cap = self._max_query_chars()
            box.fill(query[:cap] if cap else query)
            box.press("Enter")
            self._sleep(float(source.get("wait", 10)))
            raw = page.inner_text(source.get("content_selector")
                                  or _DEFAULT_CONTENT_SELECTOR)
            # 反爬/风控：命中验证码/人机校验/频率限制页 → 直接判 blocked，降级该源
            blocked, rk = detect_risk_control(raw)
            if blocked:
                return None, "blocked", rk
            ok, reason = is_valid_answer(raw, source=source["name"], query=query,
                                         min_len=self._min_len())
            if not ok:
                return None, "invalid", reason
            return raw, "ok", reason
        except Exception as e:  # 超时/崩溃/反爬 → 记录并换下一个
            return None, "error", f"{type(e).__name__}: {e}"
        finally:
            try:
                page.close()
            except Exception:
                pass

    # ------------------------- 健康记录 ------------------------- #
    def _record_runtime(self, src: dict, status: str, reason: str) -> None:
        slot = self._runtime.setdefault(
            src["name"], {"status": "unknown",
                          "confidence": self._initial_conf(src), "error": ""})
        if status == "ok":
            slot["status"] = "available"
            slot["confidence"] = min(0.9, slot["confidence"] + 0.2)
            slot["error"] = ""
        elif reason == "risk_control" or status == "blocked":
            # 风控/反爬：自动降级为不可用，写明原因（数字诚实，不硬刚）
            slot["status"] = "blocked"
            slot["confidence"] = 0.05
            slot["error"] = ("触发风控/人机验证（a_bogus/msToken/captcha 等）："
                             "该源已自动降级为不可用，请稍后或换账号重试")
        else:
            slot["status"] = "needs_login" if (
                src.get("requires_login") or reason == "login_gate") else "failed"
            slot["confidence"] = max(0.1, slot["confidence"] - 0.2)
            slot["error"] = reason

    # ------------------------- 轮换 + 故障转移 ------------------------- #
    def _collect(self, query: str):
        """逐源尝试，返回 (raw_text, source, attempts)；全部失败返回 (None, None, attempts)。"""
        browser = self._open_browser()
        attempts: list = []
        last_start: Optional[float] = None
        try:
            for src in self.plan_order():
                # R3.3-84 防封限速：相邻两源之间至少隔 min_interval 秒（默认 0=不限）
                gap = self._min_interval()
                if gap > 0 and last_start is not None:
                    waited = self._clock() - last_start
                    if waited < gap:
                        self._sleep(gap - waited)
                last_start = self._clock()
                t0 = last_start
                deadline = compute_deadline(t0, self._per_source_timeout())
                raw, status, reason = self._drive(browser, src, query)
                latency = self._clock() - t0
                # 超过每源截止时刻仍拿到内容 → 按超时记失败（数字诚实）
                if status == "ok" and self._clock() >= deadline:
                    status, reason = "timeout", "per_source_deadline_exceeded"
                attempts.append({
                    "name": src["name"], "type": src["type"],
                    "status": status, "reason": reason,
                    "latency": round(latency, 3)})
                self._record_runtime(src, status, reason)
                if status == "ok":
                    return raw, src, attempts
        finally:
            try:
                browser.close()
            except Exception:
                pass
        return None, None, attempts

    # ------------------------- 对外 OpenAI 兼容封装 ------------------------- #
    def chat(self, messages=None, query: Optional[str] = None,
             model: Optional[str] = None, timeout: Optional[float] = None) -> dict:
        """OpenAI ChatCompletion 形态。messages 或 query 二选一。"""
        if query is None and messages:
            for m in reversed(messages or []):
                if isinstance(m, dict) and m.get("role") == "user":
                    query = m.get("content")
                    break
        if not query:
            raise ValueError("chat 需要 query，或含 user 消息的 messages")

        raw, src, attempts = self._collect(query)
        if raw is None or src is None:
            raise BrowserAIAllFailed(
                f"浏览器AI全部源均失败，已尝试: "
                f"{[a['name'] for a in attempts]}", attempts=attempts)
        return {
            "id": f"chatcmpl-browserai-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model or f"browser-ai:{src['name']}",
            "choices": [{"index": 0,
                          "message": {"role": "assistant",
                                      "content": raw.strip()},
                          "finish_reason": "stop"}],
            "_meta": {
                "source": src["name"],
                "source_url": src["url"],
                "requires_login": bool(src.get("requires_login")),
                "confidence": self._runtime.get(
                    src["name"], {}).get("confidence", 0.5),
                "account": self._current_account,
                "account_pool_size": len(self._account_profiles()),
                "attempts": attempts,
            },
        }

    def search(self, query: str, model: Optional[str] = None,
               timeout: Optional[float] = None,
               max_content: int = 3000) -> list:
        """给联网补全模块（R3.2-03）用的固定线格式：
        [{title, url, content, source}, ...]。全失败返回 []（不伪造）。"""
        try:
            raw, src, attempts = self._collect(query)
        except BrowserUnavailable:
            return []
        if raw is None or src is None:
            return []
        return [{
            "title": f"{src['name']}:{query}",
            "url": src["url"],
            "content": raw.strip()[:max_content],
            "source": src["name"],
        }]

    # ------------------------- 清单与状态 ------------------------- #
    def list_sources(self) -> list:
        out = []
        for s in self._pool():
            slot = self._runtime.get(s["name"], {})
            out.append(SourceDescriptor(
                source_id=s["sid"],
                kind="web_ai",
                display=s["name"],
                models=[f"browser-{s['type']}"],
                requires_login=bool(s.get("requires_login")),
                zero_key=True,
                status=slot.get("status", "needs_login" if s.get("requires_login")
                                else "unknown"),
                confidence=slot.get("confidence", self._initial_conf(s)),
                notes=s.get("note", ""),
                source_url=s["url"],
            ).as_dict())
        return out

    def status(self) -> dict:
        overall = "unavailable" if (
            not PLAYWRIGHT_AVAILABLE and self._browser_factory is None) else "available"
        src_map = {}
        blocked = []
        for s in BROWSER_AI_SOURCES:
            slot = self._runtime.get(s["name"], {})
            st = slot.get("status", "needs_login" if s.get("requires_login")
                          else "unknown")
            src_map[s["name"]] = {
                "status": st,
                "requires_login": bool(s.get("requires_login")),
                "confidence": slot.get("confidence", self._initial_conf(s)),
                "error": slot.get("error", ""),
            }
            if st == "blocked":
                blocked.append(s["name"])
        return {
            "playwright_available": PLAYWRIGHT_AVAILABLE,
            "overall": overall,
            "sources": src_map,
            # R3.3-05：被风控降级的源清单 + 反爬诚实说明 + 多账号数
            "degraded": blocked,
            "account_count": len(self._account_profiles()),
            # R3.3-84：CDP 挂载 + 节流 的实际生效值（透明展示，供设置页/诊断页回显）
            "cdp_url": self._cdp_url(),
            "max_query_chars": self._max_query_chars(),
            "min_interval": self._min_interval(),
            "anti_bot_note": (
                "a_bogus/msToken 等请求签名由目标站前端 JS 在浏览器内现算，"
                "Playwright 驱动真实页面时由页面自动生成，本层不伪造；"
                "若站点仍返回验证码/人机校验/频率限制页，则识别并自动降级该源为不可用。"),
        }
