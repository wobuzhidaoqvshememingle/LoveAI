# -*- coding: utf-8 -*-
"""
WebSearchAutoFill —— 零成本 AI 能力中枢「联网搜索与知识不足自动补全」
对应需求：R3.2-03（方案3 + free_ai_search 补全），关联 R3.2-04（统一注册/数字诚实）、
R3.2-05（联动关系与文件边界）。

标准链路（必须落地，可离线断言）：
  ① 用户提问 → 先查本地知识库（kb_retriever）；
  ② 本地不足 → 关键词检索（keyword_retriever）；
  ③ 合并后仍少于阈值（默认 < threshold=2 条）且自动补全开关打开 → 自动调免费 AI 搜索
     （free_ai_searcher，底层 BrowserAIHub / free_ai_search）补全；
  ④ 联网结果并入上下文，逐条标注来源与 retrieved_via；
  ⑤ 上游 AI 只做整理润色、禁止自编（answer_with_sources 护栏：无上下文时直接返回
     “资料不足”提示，绝不喂空 context 给 polisher 让它凭空生成）。

设计铁律：
  - 全部外部依赖（kb/keyword/web searcher、clock、rng、config）构造注入，便于离线注入 Fake；
  - browser_ai_hub 仅在默认 free_ai_searcher 函数体内 try 延迟 import；playwright 缺失 /
    模块不存在 / 运行异常一律优雅降级（used_web=False、记入 reasons），绝不抛出阻断主链路；
  - config 为扁平 dotted-key（例如 "web.autofill.enabled" / "web.autofill.threshold" /
    "web.timeout"）；自动补全默认关（R3.2-04：浏览器自动化与自动补全默认关，手动走通再开）；
  - 数字诚实：每条结果标注 source / url / retrieved_via；联网失败如实 degraded，不夸大。

本期不接线：不改 llm_gateway / application / rag / main / 设置 UI；本文件只提供未来可一键
接入 emotion_consult（看 kb_snippets）与 chat_analysis（看 scripts_text）的纯函数与编排类。
"""
from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量 / 策略（数字诚实 & 护栏）
# ---------------------------------------------------------------------------
RETRIEVED_VIA_KB = "kb"            # 本地知识库 / 脚本片段
RETRIEVED_VIA_KEYWORD = "keyword"  # 关键词检索 fallback
RETRIEVED_VIA_WEB = "web_ai"       # 免费 AI 联网搜索

DEFAULT_THRESHOLD = 2               # 本地结果 < 2 条才考虑联网补全
DEFAULT_ENABLED = False             # 自动补全默认关（R3.2-04）
DEFAULT_TIMEOUT = 15                # 单次联网超时（秒）

# 护栏：无任何上下文时，polisher 不得凭空生成，直接返回该提示（数字诚实）
POLICY_INSUFFICIENT = (
    "资料不足：本地知识库与联网补全都未返回可用内容，以下不作推断、不编造。"
)
# 没有注入 polisher 时，仅做“整理拼接”，不追加任何外部事实
POLICY_NO_POLISHER_PREFIX = "参考以下资料整理（未作额外推断、未联网补充）："


# ---------------------------------------------------------------------------
# 数据类与纯函数
# ---------------------------------------------------------------------------
@dataclass
class SearchResult:
    """单条检索结果，必须可溯源（带来源 URL / 来源名）。"""

    title: str
    url: str = ""
    content: str = ""
    source: str = ""                       # 来源名：本地知识库 / 博查AI / 聊天脚本 ...
    retrieved_via: str = RETRIEVED_VIA_KB   # kb | keyword | web_ai
    score: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "source": self.source,
            "retrieved_via": self.retrieved_via,
            "score": self.score,
        }


@dataclass
class RetrievalOutcome:
    """retrieve_or_autofill 的结构化结果，可溯源、可计数、可降级。"""

    query: str
    results: List[SearchResult] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=lambda: {"kb": 0, "keyword": 0, "web": 0})
    used_web: bool = False
    threshold: int = DEFAULT_THRESHOLD
    sources: List[str] = field(default_factory=list)
    degraded: bool = False
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "results": [r.as_dict() for r in self.results],
            "counts": dict(self.counts),
            "used_web": self.used_web,
            "threshold": self.threshold,
            "sources": list(self.sources),
            "degraded": self.degraded,
            "reasons": list(self.reasons),
        }


def should_autofill(n_results: int, threshold: int = DEFAULT_THRESHOLD) -> bool:
    """纯函数：本地结果数 < threshold 才需要补全（默认 <2）。开关在外层另行判断。"""
    try:
        n = int(n_results)
        t = int(threshold)
    except (TypeError, ValueError):
        return False
    return n < t


def _norm_url(url: str) -> str:
    """URL 归一化：去 fragment、去尾部斜杠、scheme/host 小写。归一化失败返回原值。"""
    if not url:
        return ""
    u = url.strip()
    try:
        parts = urlsplit(u)
        scheme = (parts.scheme or "").lower()
        netloc = (parts.netloc or "").lower()
        path = parts.path or ""
        if path.endswith("/"):
            path = path.rstrip("/")
        # query 保留（同一 URL 不同 query 常指向不同结果页），fragment 丢弃
        return urlunsplit((scheme, netloc, path, parts.query, ""))
    except Exception:
        return u.split("#", 1)[0].rstrip("/")


def _content_fp(content: str) -> str:
    """内容指纹：折叠空白后取前 500 字做 md5，用于近似去重。"""
    norm = re.sub(r"\s+", " ", (content or "")).strip()
    return hashlib.md5(norm[:500].encode("utf-8", "ignore")).hexdigest()


def dedup_results(results: List[SearchResult]) -> List[SearchResult]:
    """按“归一化 URL + 内容指纹”去重，保留首次出现。空 URL 退化为仅按内容指纹去重。"""
    seen_urls = set()
    seen_fps = set()
    out: List[SearchResult] = []
    for r in results or []:
        url_key = _norm_url(r.url)
        fp = _content_fp(r.content)
        if url_key and url_key in seen_urls:
            continue
        if fp and fp in seen_fps:
            continue
        if url_key:
            seen_urls.add(url_key)
        if fp:
            seen_fps.add(fp)
        out.append(r)
    return out


def build_context(results: List[SearchResult]) -> str:
    """把每条结果拼成带引用的上下文（含来源 URL）。空列表返回空串——
    调用方据此判断“有没有材料可喂给 polisher”。"""
    if not results:
        return ""
    blocks = []
    for i, r in enumerate(results, 1):
        src = r.source or r.retrieved_via
        url_part = f"（来源链接：{r.url}）" if r.url else ""
        blocks.append(f"[{i}] {r.title}\n来源：{src}{url_part}\n{r.content}")
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# 默认 retriever / searcher（延迟 import，任何异常返回 []，绝不阻断）
# ---------------------------------------------------------------------------
def _coerce_result(obj: Any, via: str, default_source: str) -> Optional[SearchResult]:
    """把下游返回的 dict / tuple / SearchResult 统一成 SearchResult。无法识别返回 None。"""
    if isinstance(obj, SearchResult):
        if not obj.retrieved_via or obj.retrieved_via == "kb":
            obj.retrieved_via = via
        if not obj.source:
            obj.source = default_source
        return obj
    if isinstance(obj, Mapping):
        return SearchResult(
            title=str(obj.get("title") or obj.get("name") or ""),
            url=str(obj.get("url") or obj.get("link") or ""),
            content=str(obj.get("content") or obj.get("snippet") or obj.get("text") or ""),
            source=str(obj.get("source") or default_source),
            retrieved_via=via,
            score=obj.get("score"),
        )
    if isinstance(obj, (tuple, list)):
        # 兼容旧 free_ai_search 的 (title, url, content) / (title, url, content, source)
        title = obj[0] if len(obj) > 0 else ""
        url = obj[1] if len(obj) > 1 else ""
        content = obj[2] if len(obj) > 2 else ""
        source = obj[3] if len(obj) > 3 else default_source
        return SearchResult(title=str(title), url=str(url), content=str(content),
                            source=str(source), retrieved_via=via)
    return None


def _to_results(raw: Any, via: str, default_source: str) -> List[SearchResult]:
    out: List[SearchResult] = []
    for item in (raw or []):
        sr = _coerce_result(item, via, default_source)
        if sr is not None and (sr.content or sr.title):
            out.append(sr)
    return out


def default_kb_retriever(query: str) -> List[SearchResult]:
    """默认本地知识库检索：尽力延迟接入 services.rag_service / unified_search。
    模块缺失 / 接口不符 / 运行异常 → 一律返回 []，绝不阻断主链路。
    真正的接线待封包收尾后由接线任务完成（本期不 import 也能跑离线测试）。"""
    try:
        mod = None
        try:
            from services import rag_service as mod  # type: ignore
        except Exception:
            mod = None
        if mod is None:
            try:
                from services import unified_search as mod  # type: ignore
            except Exception:
                mod = None
        if mod is None:
            return []
        for name in ("search", "query", "search_local", "fts_search"):
            fn = getattr(mod, name, None)
            if callable(fn):
                return _to_results(fn(query), RETRIEVED_VIA_KB, "本地知识库")
    except Exception as e:  # pragma: no cover - 离线环境必走异常分支
        logger.debug("default_kb_retriever degraded: %s", e)
    return []


def default_keyword_retriever(query: str) -> List[SearchResult]:
    """默认关键词 fallback 检索：尽力延迟接入 unified_search 关键词链路；异常返回 []。"""
    try:
        mod = None
        try:
            from services import unified_search as mod  # type: ignore
        except Exception:
            mod = None
        if mod is None:
            return []
        for name in ("keyword_search", "search_by_keyword", "fallback_search"):
            fn = getattr(mod, name, None)
            if callable(fn):
                return _to_results(fn(query), RETRIEVED_VIA_KEYWORD, "关键词检索")
    except Exception as e:  # pragma: no cover
        logger.debug("default_keyword_retriever degraded: %s", e)
    return []


def default_free_ai_searcher(query: str) -> List[dict]:
    """默认免费 AI 联网搜索：函数体内延迟 import services.browser_ai_hub.BrowserAIHub，
    调其 search(query) -> list[{title,url,content,source}]。
    - 模块尚不存在（并行开发中）/ playwright 未装 / 运行异常 → 直接 raise，由
      WebSearchService 主链路捕获并优雅降级（web_used=False、记 reasons）；
    - 绝不吞掉后假装搜到东西（数字诚实）。
    """
    from services.browser_ai_hub import BrowserAIHub  # 延迟 import，顶层不依赖
    hub = BrowserAIHub()
    raw = hub.search(query) or []
    out: List[dict] = []
    for item in raw:
        if isinstance(item, Mapping):
            out.append({
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "content": str(item.get("content") or item.get("text") or ""),
                "source": str(item.get("source") or "免费AI搜索"),
            })
    return out


# ---------------------------------------------------------------------------
# 编排类
# ---------------------------------------------------------------------------
class WebSearchService:
    """链路编排：本地KB → 关键词 →（不足且开关开）免费AI补全 → 合并去重 → 带来源上下文。"""

    POLICY_INSUFFICIENT = POLICY_INSUFFICIENT
    POLICY_NO_POLISHER_PREFIX = POLICY_NO_POLISHER_PREFIX

    def __init__(
        self,
        kb_retriever: Optional[Callable[[str], Any]] = None,
        keyword_retriever: Optional[Callable[[str], Any]] = None,
        free_ai_searcher: Optional[Callable[[str], List[dict]]] = None,
        clock: Optional[Callable[[], float]] = None,
        rng: Optional[random.Random] = None,
        config: Optional[Mapping] = None,
    ):
        self._kb = kb_retriever or default_kb_retriever
        self._kw = keyword_retriever or default_keyword_retriever
        self._web = free_ai_searcher or default_free_ai_searcher
        self._clock = clock or time.time
        self._rng = rng or random.Random()
        self._cfg: Mapping = config or {}

    # -- config 读取（扁平 dotted-key）-----------------------------------
    def _cfg_get(self, key: str, default: Any) -> Any:
        if not self._cfg:
            return default
        try:
            v = self._cfg.get(key, None)
        except Exception:
            v = getattr(self._cfg, key, None)
        return default if v is None else v

    # -- 内部：安全调用一个 retriever -------------------------------------
    @staticmethod
    def _safe_call(fn: Callable[[str], Any], query: str, via: str,
                   default_source: str, reasons: List[str]) -> List[SearchResult]:
        try:
            raw = fn(query)
        except Exception as e:
            reasons.append(f"{via}_retriever_error: {type(e).__name__}: {e}")
            return []
        return _to_results(raw, via, default_source)

    # -- 主链路 -----------------------------------------------------------
    def retrieve_or_autofill(self, query: str) -> RetrievalOutcome:
        reasons: List[str] = []
        threshold = int(self._cfg_get("web.autofill.threshold", DEFAULT_THRESHOLD))
        enabled = bool(self._cfg_get("web.autofill.enabled", DEFAULT_ENABLED))

        # ① 本地知识库
        kb_res = self._safe_call(self._kb, query, RETRIEVED_VIA_KB, "本地知识库", reasons)
        # ② 关键词检索
        kw_res = self._safe_call(self._kw, query, RETRIEVED_VIA_KEYWORD, "关键词检索", reasons)

        merged = dedup_results(kb_res + kw_res)
        counts = {"kb": len(kb_res), "keyword": len(kw_res), "web": 0}

        need_web = should_autofill(len(merged), threshold)
        used_web = False
        degraded = False

        # ③ 不足且开关开 → 免费 AI 补全
        if need_web and enabled:
            try:
                raw_web = self._web(query) or []
                web_res = _to_results(raw_web, RETRIEVED_VIA_WEB, "免费AI搜索")
            except Exception as e:
                web_res = []
                reasons.append(f"web_search_failed: {type(e).__name__}: {e}")
            counts["web"] = len(web_res)
            if web_res:
                merged = dedup_results(merged + web_res)
                used_web = True
            else:
                degraded = True  # 尝试了联网但没拿到东西（playwright缺/模块缺/超时/空结果）
                reasons.append("web_no_result")
        elif need_web and not enabled:
            reasons.append("autofill_disabled")
        else:
            reasons.append("enough_local_results")

        sources = self._collect_sources(merged)
        return RetrievalOutcome(
            query=query,
            results=merged,
            counts=counts,
            used_web=used_web,
            threshold=threshold,
            sources=sources,
            degraded=degraded,
            reasons=reasons,
        )

    @staticmethod
    def _collect_sources(results: List[SearchResult]) -> List[str]:
        out = []
        for r in results:
            name = r.source or r.retrieved_via
            if r.url:
                out.append(f"{name} <{r.url}>")
            else:
                out.append(name)
        return out

    # -- AI 只整理润色、禁止自编（护栏）-----------------------------------
    def answer_with_sources(
        self,
        query: str,
        polisher: Optional[Callable[[str, str, List[str]], str]] = None,
        outcome: Optional[RetrievalOutcome] = None,
    ) -> Dict[str, Any]:
        """polisher 签名 (query, context, sources) -> str，只允许基于 context 重排润色。
        护栏：无 context 时绝不调用 polisher（防止它凭空生成），直接返回 POLICY_INSUFFICIENT。
        """
        if outcome is None:
            outcome = self.retrieve_or_autofill(query)
        context = build_context(outcome.results)
        sources = self._collect_sources(outcome.results)

        if not context.strip():
            # 数字诚实：没材料就明说，不让 polisher 编
            return {
                "answer": self.POLICY_INSUFFICIENT,
                "context": "",
                "sources": sources,
                "used_web": outcome.used_web,
                "degraded": outcome.degraded,
                "fabricated": False,
            }

        if polisher is None:
            answer = f"{self.POLICY_NO_POLISHER_PREFIX}\n\n{context}"
        else:
            answer = polisher(query, context, sources)

        return {
            "answer": answer,
            "context": context,
            "sources": sources,
            "used_web": outcome.used_web,
            "degraded": outcome.degraded,
            "fabricated": False,
        }

    # -- 面向 emotion_consult / chat_analysis 的一键接入 ------------------
    def auto_fill_for_mode(
        self,
        query: str,
        mode: str,
        kb_snippets: Optional[List[Any]] = None,
        scripts_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """未来接线用：emotion_consult 看 kb_snippets，chat_analysis 看 scripts_text。
        - emotion_consult：kb_snippets 为空 或条数 < threshold → 触发补全；
        - chat_analysis：scripts_text 为空串 → 触发补全（已有脚本上下文则不补）。
        返回 {context, sources, used_web, counts, mode}，封包收尾后两个模式里各加一行即可。
        """
        threshold = int(self._cfg_get("web.autofill.threshold", DEFAULT_THRESHOLD))
        if mode == "emotion_consult":
            existing = _to_results(kb_snippets or [], RETRIEVED_VIA_KB, "本地知识库片段")
            need = len(existing) < threshold
        elif mode == "chat_analysis":
            if scripts_text and scripts_text.strip():
                existing = [SearchResult(
                    title="聊天脚本参考", url="", content=scripts_text.strip(),
                    source="本地聊天脚本", retrieved_via=RETRIEVED_VIA_KB)]
            else:
                existing = []
            need = len(existing) == 0  # scripts_text 空才补
        else:
            raise ValueError(f"unknown mode: {mode!r}（仅支持 emotion_consult / chat_analysis）")

        counts = {"kb": len(existing), "keyword": 0, "web": 0}
        used_web = False
        reasons: List[str] = []
        merged: List[SearchResult] = list(existing)

        if need:
            outcome = self.retrieve_or_autofill(query)
            merged = dedup_results(existing + outcome.results)
            # 重新计数（去重后按 retrieved_via 归类）
            counts = {
                "kb": sum(1 for r in merged if r.retrieved_via == RETRIEVED_VIA_KB),
                "keyword": sum(1 for r in merged if r.retrieved_via == RETRIEVED_VIA_KEYWORD),
                "web": sum(1 for r in merged if r.retrieved_via == RETRIEVED_VIA_WEB),
            }
            used_web = outcome.used_web
            reasons = list(outcome.reasons)
        else:
            reasons.append("existing_snippets_enough")

        return {
            "context": build_context(merged),
            "sources": self._collect_sources(merged),
            "used_web": used_web,
            "counts": counts,
            "mode": mode,
            "degraded": False,
            "reasons": reasons,
        }
