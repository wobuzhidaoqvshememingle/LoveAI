"""
LoveAI v2.0 - 大模型 L1/L2/L3 三级路由网关
负责本地/云端模型路由、LoRA 适配器切换、训练数据收集、Prompt 模板管理、懒加载与空闲自动卸载。

三级模型：
  L1 最小模型 -> smollm2-135m ONNX 量化版（onnxruntime + tokenizers），目标 <1 秒回复
  L2 本地大模型 -> Qwen2.5-0.5B-Instruct + LoRA 适配器
                   优先 llama.cpp（GGUF + lora_path），失败降级 transformers+peft（safetensors）
  L3 云端 API  -> OpenAI 兼容格式

自动降级链：目标层失败 -> 向下一层降级 -> 规则匹配兜底。
所有重依赖（onnxruntime/tokenizers/llama_cpp/transformers/peft）均方法内懒加载，缺库不崩溃。
"""
from __future__ import annotations

import os
import json
import time
import logging
import urllib.request
import urllib.error
from typing import Optional, TYPE_CHECKING, Any

import openai

if TYPE_CHECKING:
    # 仅用于类型提示，运行时不导入（避免服务层强依赖 PySide6）
    from .config_service import ConfigService
    from app.signals import AppSignals
    from models.database import Database

logger = logging.getLogger(__name__)


class LLMGateway:
    """大模型 L1/L2/L3 三级路由网关 + LoRA 适配器管理 + 训练数据收集"""

    # 判定为 L3（复杂任务）的触发关键词
    _COMPLEX_KEYWORDS = (
        "分析", "为什么", "怎么办", "预测", "模拟",
        "言外之意", "潜台词", "数字分身", "深层", "博弈",
    )
    # 问候类短句 -> 直接 L1
    _GREETING_KEYWORDS = ("你好", "您好", "嗨", "在吗", "早上好", "晚上好", "晚安", "hi", "hello", "哈喽")
    # 短文本字数阈值：小于该字数一律按 L1 处理
    _SIMPLE_LENGTH_THRESHOLD = 15
    # 长文本字数阈值：超过该字数按 L3 处理
    _LONG_LENGTH_THRESHOLD = 120
    # L1 单次最大生成长度（保证 <1 秒）
    _L1_MAX_NEW_TOKENS = 64

    # ---- Step1 本地模型中文分层（v3.2）----
    # SmolLM2-135M 实测：英文流利，中文为字节级 BPE、生成即复读/死循环/语义乱码。
    # 因此 L1 不再承担中文自由生成；中文对话/改写/短回复最低走 L2=Qwen0.5B(Ollama HTTP)。
    # L1 仅承担「不依赖中文生成」的轻活：固定标签集打分分类 classify_l1()。
    # 强制允许 L1 生成中文（仅诊断/评测用，默认关）：config llm.local.l1.allow_chinese_generation
    # L2 Ollama HTTP 默认端点与模型（纯本地，无需 llama_cpp/transformers/torch）
    _DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434"
    # 离线主力 = Qwen2.5-1.5B（中文对话/轻分析/双路线话术）；快速兜底 = Qwen2.5-0.5B。
    _DEFAULT_OLLAMA_MODEL = "loveai-qwen15b:latest"
    _FALLBACK_OLLAMA_MODEL = "loveai-qwen05b:latest"
    # 用户可经 config llm.local.l2.ollama_model 显式切换默认模型。
    # 中文自由生成任务（这些系统提示/用户意图下，L1 一律拒绝生成）
    _ZH_GENERATIVE_HINTS = (
        "回复", "对话", "聊天", "改写", "润色", "文案", "建议", "安慰",
        "怎么", "如何", "为什么", "分析", "模拟", "人设", "分身",
    )

    # ---- R3.3-04 四层模型分工（tier -> 降级候选层链）----
    # 判断层 judgment：免费 Flash/turbo（最轻）
    # 草稿层 draft：最便宜（与判断层同链，含义=不追求质量只求快省）
    # 润色层 polish：中端（本地/免费优先，必要时才上云端）
    # 模拟层 simulate：贵模型（云端优先），但由闸门控制只在少量调用上使用
    _TIER_CHAINS = {
        "judgment": ["free", "l2", "l1"],
        "draft":    ["free", "l2", "l1"],
        "polish":   ["l2", "free", "l3"],
        "simulate": ["l3", "free", "l2"],
    }
    # R3.3-15 L3 自有 Key provider 顺序：DeepSeek(便宜强) -> 豆包(第二兜底) -> 通用 cloud
    _L3_PROVIDER_ORDER = ("deepseek", "doubao", "generic")

    # v3.2 统一恋爱指导人设（拉齐不同模型/不同 provider 的输出风格）
    # 无论走 DeepSeek / Doubao / Gemini / 免费路由 / 本地 Qwen，都会在 system_prompt
    # 前拼这段，保证对外输出的"恋爱教练"口吻一致。
    UNIFIED_COACH_PERSONA: str = (
        "你是「地球online恋爱小游戏」的专属恋爱教练，陪用户把亲密关系当成一场策略游戏在打。\n"
        "说话风格：像一个懂行的老哥/老姐，直接、不端着、不灌鸡汤；偶尔损两句但不伤人；"
        "给建议要落地到下一条微信怎么发、下次见面怎么开口，不要「分析完就完了」。\n"
        "禁用AI腔：不要「我觉得/在我看来/根据分析/总而言之/希望对你有帮助/加油」；"
        "不要每句都用「~」结尾；不要一上来就喊「亲爱的/宝贝」。\n"
        "立场：始终站在用户这边，但不许教他操控/打压/冷暴力对方；"
        "核心是「看懂对方→修好当前问题→把关系经营好」，只有家暴/出轨/持续欺骗才提示撤离。\n"
    )
    # 这些 system_prompt 是内部任务（分类/JSON抽取/数字分身模拟），不拼教练人设
    _PERSONA_BLOCK_MARKERS: tuple = (
        "只输出 JSON", "只输出json", "分类器", "数字分身", "模拟对方",
        "不要解释", "不要输出其他", "输出JSON", "输出 json",
        "语言风格分析师", "风格分析", "情绪分类", "情感分析器",
    )

    def __init__(
        self,
        config: "ConfigService",
        signals: Optional["AppSignals"] = None,
        db: Optional["Database"] = None,
    ) -> None:
        """
        初始化网关。

        Args:
            config: ConfigService 实例，读取 llm.* 配置。
            signals: AppSignals 实例（可选），用于向外推送状态。
            db: Database 实例（可选），用于训练数据收集。
        """
        self.config: ConfigService = config
        self.signals: Optional[AppSignals] = signals
        self.db: Optional[Database] = db

        # 项目根目录（本文件位于 <root>/services/llm_gateway.py，上溯两级即根目录）
        # === 随包运行时接入（新增）：包内只读资源根 + 安装目录优先的 models_local 根 ===
        try:
            from services import runtime_paths as _rp
            self.project_root = _rp.app_root()                # 包内只读资源根
            self.models_local_root = _rp.models_local_dir()   # 安装目录优先、_internal 兜底
        except Exception:
            self.project_root = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
            self.models_local_root = os.path.join(self.project_root, "models_local")

        # ---- L1 状态 ----
        self._l1_session: Any = None       # onnxruntime InferenceSession
        self._l1_tokenizer: Any = None     # tokenizers.Tokenizer
        self.l1_loaded: bool = False
        self.l1_available: bool = True

        # ---- L2 状态 ----
        self._l2_model: Any = None         # llama_cpp.Llama 或 transformers 模型
        self._l2_backend: str = ""         # "llama_cpp" / "transformers"
        self.l2_loaded: bool = False
        self.l2_available: bool = True
        self._l2_dirty: bool = False       # LoRA 切换后标记需重载

        # ---- L3 状态 ----
        self.l3_available: bool = True

        # 最近一次成功回答所用的层级（供训练数据标记来源模型）
        self.last_layer: str = "none"

        # 空闲自动卸载
        self.last_call_time: float = 0.0
        self.unload_timeout: float = float(
            self.config.get("llm.auto_unload_timeout", 300)
        )

        # Prompt 模板缓存
        self._prompt_cache: dict[str, str] = {}

        # 训练数据表是否已创建
        self._training_table_ready: bool = False

        # ---- 免费模型路由（持久实例：调用复用、后台定时刷新目录）----
        self._free_router: Any = None

        # ---- R3.3-03 难度分级（懒创建）；R3.3-04 最近一次 tier 记录 ----
        self._difficulty_router: Any = None
        self.last_tier: str = ""
        self.last_l3_provider: str = ""

        # P-10 统计兜底供应商钩子：callable(context) -> LocalStatisticalFallback | None。
        # 由组合层（application）注入真实历史计数/检索器；未注入则用人群先验安全兜底。
        self._stat_fallback_supplier = None

    # ------------------------------------------------------------------ #
    # 路径解析工具
    # ------------------------------------------------------------------ #
    def _resolve_path(self, rel_or_abs: str) -> str:
        """相对路径基于项目根目录解析，绝对路径原样返回。"""
        if not rel_or_abs:
            return ""
        if os.path.isabs(rel_or_abs):
            return rel_or_abs
        p = os.path.join(self.project_root, rel_or_abs)
        # 随包模型：安装目录 models_local 优先（runtime_paths 已封装安装目录/_internal 回退顺序）
        norm = rel_or_abs.replace("\\", "/")
        if norm.startswith("models_local/"):
            mroot = getattr(self, "models_local_root", "")
            if mroot and os.path.isdir(mroot):
                cand = os.path.join(mroot, norm[len("models_local/"):])
                if os.path.exists(cand):
                    return cand
        return p

    def _build_proxy_url(self) -> Optional[str]:
        """构建代理URL（用于httpx/openai等库）"""
        if not self.config.get("proxy.enabled", False):
            return None
        proxy_type = self.config.get("proxy.type", "http")
        host = self.config.get("proxy.host", "127.0.0.1")
        port = self.config.get("proxy.port", 7890)
        username = self.config.get("proxy.username", "")
        password = self.config.get("proxy.password", "")
        if username and password:
            return f"{proxy_type}://{username}:{password}@{host}:{port}"
        return f"{proxy_type}://{host}:{port}"

    def _flag_yes(self, key: str, default: bool = True) -> bool:
        """布尔配置：'false/0/no/off' 视为否，其余（含缺失）按 default。"""
        v = self.config.get(key, None)
        if v is None:
            return default
        return str(v).strip().lower() not in ("false", "0", "no", "off", "")

    # ------------------------------------------------------------------ #
    # 统一聊天入口
    # ------------------------------------------------------------------ #
    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        complexity: str = "auto",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        allow_rule_fallback: bool = True,
        tier: Optional[str] = None,
    ) -> str:
        """
        统一聊天接口，按复杂度自动路由到对应层级，并按降级链逐层兜底。

        Args:
            system_prompt: 系统提示词。
            user_prompt: 用户提示词。
            complexity: auto(自动判断) / l1 / l2 / l3；兼容旧值 simple/medium/complex。
            temperature: 采样温度。
            max_tokens: 最大生成 token 数。
            allow_rule_fallback: 所有真实模型层不可用时，是否返回通用规则兜底句。
                模拟特定人物（数字分身）时应传 False，由调用方走人设一致的兜底，
                避免把对方模拟成通用助手口吻。
            tier: R3.3-04 四层分工，可选 judgment/draft/polish/simulate。
                一旦指定，忽略 complexity 的层链推导，直接用该 tier 的降级链。
                不改变任何老调用方行为（默认 None）。

        Returns:
            模型输出文本；所有模型层均不可用时，allow_rule_fallback=True 返回
            规则兜底回复，False 返回空串。
        """
        # 兼容旧调用方的 simple/medium/complex
        legacy_map = {"simple": "l1", "medium": "l2", "complex": "l3"}
        complexity = legacy_map.get(complexity, complexity)

        if complexity == "auto":
            complexity = self._judge_complexity(user_prompt)

        # v3.2 统一教练人设：用户-facing 的输出拼上；内部JSON/分类/数字分身任务跳过
        if not any(marker in (system_prompt or "") for marker in self._PERSONA_BLOCK_MARKERS):
            if not system_prompt.startswith(self.UNIFIED_COACH_PERSONA[:20]):
                system_prompt = self.UNIFIED_COACH_PERSONA + "\n" + (system_prompt or "")

        # 配置可强制锁定路由层级
        mode = str(self.config.get("llm.routing.mode", "auto") or "auto")
        if mode in ("l1", "l2", "l3"):
            complexity = mode

        self.last_call_time = time.time()

        # 按目标层构造降级候选链：L3付费云端 -> FREE免费路由 -> L2本地Qwen(Ollama) -> L1本地小模型 -> 规则兜底
        # 注：L1(SmolLM2) 中文自由生成能力不足，运行期在 _call_l1 内对中文生成任务主动 raise，
        #     由本循环自然降级到下一层；此处保留 l1 在链尾是为兼容旧测试与「纯英文/极轻量」场景。
        if tier in self._TIER_CHAINS:
            # R3.3-04 显式指定四层分工：直接用 tier 链（可降级到便宜层）
            self.last_tier = tier
            chain = list(self._TIER_CHAINS[tier])
        else:
            if tier is not None:
                logger.warning("未知 tier=%s，忽略并按 complexity 推导", tier)
            self.last_tier = ""
            # l1 链：L1(SmolLM2) 中文自由生成实测乱码/复读，已在 _call_l1 内对中文主动 raise；
            # 因此短消息/寒暄若走 l1，必须继续降级到 L2(ollama Qwen)，否则中文寒暄会
            # 直接卡在统计兜底。l1 仅在纯英文/诊断场景命中。（不含 free：保持单测隔离，
            # 且 L2 已是本机中文真后端。）
            chain = {"l3": ["l3", "free", "l2", "l1"],
                     "l2": ["l2", "free", "l1"],
                     "l1": ["l1", "l2"]}.get(complexity, ["l3", "free", "l2", "l1"])

        for layer in chain:
            try:
                out = self._call_layer(layer, system_prompt, user_prompt,
                                       temperature, max_tokens)
                if out and out.strip():
                    self.last_layer = layer
                    self.last_call_time = time.time()
                    return out
            except Exception as e:
                # R3.3-30 fault_router 分级：P0 同层重试一次 / P1 降级 / P2 记日志+信号
                recovered = self._handle_layer_fault(
                    layer, e, system_prompt, user_prompt, temperature, max_tokens)
                if recovered and recovered.strip():
                    # P0 同层重试自愈成功，直接返回
                    self.last_layer = layer
                    self.last_call_time = time.time()
                    return recovered

        # 全部生成模型层不可用 -> P-10 纯统计反应分布（明确标注，不硬失败）
        logger.warning("所有生成模型层均不可用，尝试 P-10 统计兜底；allow_rule_fallback=%s",
                       allow_rule_fallback)
        stat_text = self._stat_fallback_text(user_prompt)
        if stat_text and stat_text.strip():
            self.last_layer = "stat"
            return stat_text

        # 统计兜底也不可用 -> 规则匹配兜底（最终安全网）
        self.last_layer = "rules"
        return self._rule_reply(user_prompt) if allow_rule_fallback else ""

    # ------------------------------------------------------------------ #
    # R3.3-30 故障分级接线：按层调用 + P0/P1/P2 处理
    # ------------------------------------------------------------------ #
    def _call_layer(self, layer: str, system_prompt: str, user_prompt: str,
                    temperature: float, max_tokens: int) -> str:
        """按层名分发到具体调用（chat() 主循环与 P0 重试共用）。"""
        if layer == "l3":
            return self._call_l3(system_prompt, user_prompt, temperature, max_tokens)
        if layer == "free":
            return self._call_free(system_prompt, user_prompt, temperature, max_tokens)
        if layer == "l2":
            return self._call_l2(system_prompt, user_prompt, temperature, max_tokens)
        return self._call_l1(system_prompt, user_prompt, temperature, max_tokens)

    def _handle_layer_fault(self, layer: str, error: Exception,
                            system_prompt: str, user_prompt: str,
                            temperature: float, max_tokens: int) -> Optional[str]:
        """fault_router 分级处理：
          P0（timeout/5xx/连接重置）→ 同层立即重试一次，成功则返回恢复文本；
          P1（401/403/429/404/模型崩溃）→ 沿降级链切下一层（返回 None 让主循环继续）；
          P2（磁盘满/DB锁/连续P1）     → 记日志 + 发信号（不静默），返回 None。
        """
        try:
            from services.fault_router import classify_fault, FaultLevel
        except Exception as e:  # pragma: no cover - 模块缺失则退回旧行为
            logger.warning("fault_router 不可用，按旧行为降级: %s | layer=%s err=%s",
                           e, layer, error)
            return None

        level = classify_fault(error, ctx={})
        if level is FaultLevel.P0_IMMEDIATE_RECOVER:
            # 同层立即重试一次
            logger.warning("P0 瞬时故障 layer=%s，同层重试一次: %s", layer, error)
            try:
                out = self._call_layer(layer, system_prompt, user_prompt,
                                       temperature, max_tokens)
                if out and out.strip():
                    logger.warning("P0 同层重试自愈成功 layer=%s", layer)
                    return out
            except Exception as e2:  # 重试仍失败，继续降级
                logger.warning("P0 重试仍失败 layer=%s，降级: %s", layer, e2)
            return None

        if level is FaultLevel.P2_MANUAL:
            # 人工介入：记日志 + 发信号（UI 标红）
            logger.error("【P2 需人工介入】layer=%s err=%s", layer, error)
            try:
                if self.signals is not None:
                    sig = getattr(self.signals, "llm_fault_p2", None) \
                        or getattr(self.signals, "critical_error", None)
                    if sig is not None:
                        sig.emit(f"模型层 {layer} 出现需人工介入的故障: {error}")
            except Exception as se:  # noqa: BLE001
                logger.debug("P2 信号发送失败（忽略）: %s", se)
            return None

        # P1：普通降级日志
        logger.warning("P1 可降级故障 layer=%s，切下一层: %s", layer, error)
        return None

    # ------------------------------------------------------------------ #
    # L3：云端 OpenAI 兼容 API
    # ------------------------------------------------------------------ #
    def _l3_providers(self) -> list[dict]:
        """R3.3-15 L3 自有 Key provider 链（按返回顺序尝试）：
        DeepSeek(便宜强，复杂推理主力) -> 豆包(第二兜底) -> 通用 cloud(llm.cloud.*)。

        只有配了 api_key 的 provider 才入选；通用 cloud 沿用历史设置页配置。
        任何读取异常都被吞掉，保证主流程不崩。"""
        out: list[dict] = []
        try:
            # 1) DeepSeek（主力，V4-Flash 类便宜强模型）
            ds_key = self.config.get("llm.l3.deepseek.api_key", "") or self.config.get("llm.deepseek.api_key", "")
            if ds_key:
                out.append({
                    "label": "deepseek",
                    "api_key": ds_key,
                    "api_base": self.config.get("llm.l3.deepseek.api_base", "")
                                or self.config.get("llm.deepseek.api_base", "")
                                or "https://api.deepseek.com",
                    "model": self.config.get("llm.l3.deepseek.model", "")
                             or self.config.get("llm.deepseek.model", "")
                             or "deepseek-chat",
                })
            # 2) 豆包 / 火山方舟（第二兜底，旗舰只在 DeepSeek 不达标时上）
            db_key = self.config.get("llm.l3.doubao.api_key", "") or self.config.get("llm.doubao.api_key", "")
            if db_key:
                out.append({
                    "label": "doubao",
                    "api_key": db_key,
                    "api_base": self.config.get("llm.l3.doubao.api_base", "")
                                or self.config.get("llm.doubao.api_base", "")
                                or "https://ark.cn-beijing.volces.com/api/v3",
                    "model": self.config.get("llm.l3.doubao.model", "")
                             or self.config.get("llm.doubao.model", "")
                             or "doubao-pro-4k",
                })
            # 3) 通用 cloud（历史 llm.cloud.* 设置页，最后兜底）
            g_key = self.config.get("llm.cloud.api_key", "") or self.config.get("llm.api_key", "")
            if g_key:
                out.append({
                    "label": "generic",
                    "api_key": g_key,
                    "api_base": self.config.get("llm.cloud.api_base", "") or self.config.get("llm.api_base", ""),
                    "model": self.config.get("llm.cloud.model", "") or self.config.get("llm.model", "gpt-4o-mini"),
                })
        except Exception as e:  # noqa: BLE001
            logger.warning("读取 L3 provider 配置异常（忽略）: %s", e)
        return out

    @staticmethod
    def _l3_output_substandard(text: str) -> bool:
        """R3.3-15 输出是否被判为「不达标」，用于决定是否升档到下一个更强 provider。

        粗启发：空、过短、或明显拒答/无内容。刻意保守，避免误杀正常回答。"""
        t = (text or "").strip()
        if not t:
            return True
        if len(t) < 8:
            return True
        bad = ("我无法", "作为AI", "不能回答", "抱歉，我")
        return any(b in t for b in bad)

    def _call_l3_one(self, p: dict, system_prompt: str, user_prompt: str,
                     temperature: float, max_tokens: int) -> str:
        """对单个 L3 provider 发一次请求；异常向上抛由 _call_l3 链降级。"""
        client_kwargs = {"api_key": p["api_key"], "base_url": p.get("api_base") or None}
        if self.config.get("proxy.enabled", False) and self.config.get("proxy.use_for_llm", True):
            proxy_url = self._build_proxy_url()
            if proxy_url:
                import httpx
                client_kwargs["http_client"] = httpx.Client(proxy=proxy_url, timeout=60.0)
        client = openai.OpenAI(**client_kwargs)
        response = client.chat.completions.create(
            model=p["model"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content
        return content if content is not None else ""

    def _call_l3(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """调用云端模型（OpenAI SDK 兼容格式）。异常向上抛出由 chat() 降级。

        R3.3-15：按 DeepSeek -> 豆包 -> 通用 cloud 顺序尝试；
        当前 provider 报错或输出不达标（空/过短/拒答）时，自动升到下一个更强 provider。"""
        providers = self._l3_providers()
        if not providers:
            raise RuntimeError("L3 云端未配置 api_key")

        last_err: Optional[Exception] = None
        for i, p in enumerate(providers):
            try:
                content = self._call_l3_one(p, system_prompt, user_prompt,
                                           temperature, max_tokens)
                # 输出不达标且还有下一个更强 provider -> 升档重试
                if self._l3_output_substandard(content) and i < len(providers) - 1:
                    nxt = providers[i + 1]["label"]
                    logger.info("L3 provider=%s 输出不达标，升档到 %s", p["label"], nxt)
                    continue
                self.l3_available = True
                self.last_l3_provider = p["label"]
                return content
            except Exception as e:
                last_err = e
                logger.warning("L3 provider=%s 调用失败，尝试下一个: %s", p["label"], e)
        # 全部 provider 都失败：抛出最后一个异常，由 chat() 落到 free/l2/l1
        raise RuntimeError(f"L3 全部云端 provider 失败: {last_err}") from last_err

    # ------------------------------------------------------------------ #
    # FREE：免费模型路由（OpenRouter / NVIDIA NIM / 本地 Ollama）
    # ------------------------------------------------------------------ #
    def get_free_router(self):
        """懒创建并复用持久 FreeModelRouter（后台调度器也通过它定时刷新目录）。

        R3.3-31：创建后立即 probe_all() 全量探活（总预算 5s，超时算 dead，
        任何异常都不阻塞启动）。"""
        if self._free_router is None:
            from services.free_model_router import FreeModelRouter
            self._free_router = FreeModelRouter(self.config)
            try:
                self._free_router.probe_all()
            except Exception as e:  # 探活失败不影响后续路由（全部按 unknown 处理）
                logger.warning("免费模型启动探活失败（忽略）: %s", e)
        return self._free_router

    def _call_free(self, system_prompt, user_prompt, temperature, max_tokens) -> str:
        """付费云端无 key 或失败时，走免费模型自动路由；无可用渠道抛出，由 chat() 继续降级。"""
        router = self.get_free_router()
        out = router.chat(system_prompt, user_prompt,
                          temperature=temperature, max_tokens=max_tokens)
        self.last_free_provider = out.get("provider")
        return out.get("text", "")

    # ------------------------------------------------------------------ #
    # L2：本地 Qwen2.5-0.5B + LoRA
    # ------------------------------------------------------------------ #
    def _call_l2(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """调用本地 L2 模型（Ollama HTTP 优先，llama.cpp / transformers 兜底）。"""
        self._ensure_l2_loaded()
        if self._l2_backend != "ollama_http" and self._l2_model is None:
            raise RuntimeError("L2 模型未加载")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        if self._l2_backend == "ollama_http":
            # 纯本地 Ollama HTTP 后端：不依赖 llama_cpp/transformers/torch
            return self._call_l2_ollama(system_prompt, user_prompt,
                                        temperature, max_tokens)

        if self._l2_backend == "llama_cpp":
            output = self._l2_model.create_chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return output["choices"][0]["message"]["content"]

        # transformers 后端
        prompt = self._l2_tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        ) if hasattr(self._l2_tokenizer, "apply_chat_template") else (
            f"{system_prompt}\n\n用户：{user_prompt}\n助手："
        )
        inputs = self._l2_tokenizer(prompt, return_tensors="pt")
        out_ids = self._l2_model.generate(
            **inputs, max_new_tokens=max_tokens, temperature=temperature, do_sample=temperature > 0
        )
        trimmed = out_ids[0][inputs["input_ids"].shape[1]:]
        return self._l2_tokenizer.decode(trimmed, skip_special_tokens=True)

    # ------------------------------------------------------------------ #
    # L2 后端：本地 Ollama HTTP（系统已装 Ollama，纯本地免 llama_cpp/torch）
    # ------------------------------------------------------------------ #
    def _ollama_base(self) -> str:
        return str(self.config.get("llm.local.l2.ollama_base",
                                   self._DEFAULT_OLLAMA_BASE)
                   or self._DEFAULT_OLLAMA_BASE).rstrip("/")

    def _ollama_model_name(self) -> str:
        return str(self.config.get("llm.local.l2.ollama_model",
                                   self._DEFAULT_OLLAMA_MODEL)
                   or self._DEFAULT_OLLAMA_MODEL)

    def _ollama_candidate_models(self) -> list:
        """本地候选模型（按优先级）：配置/默认主力(1.5B) → 快速兜底(0.5B)，去重。"""
        ordered = [self._ollama_model_name(), self._FALLBACK_OLLAMA_MODEL]
        seen, out = set(), []
        for m in ordered:
            m = str(m or "").strip()
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out

    @staticmethod
    def _model_present(want: str, names: list) -> bool:
        """命中「完整名」或「名:latest 与 名」等价写法。"""
        return want in names or want.split(":")[0] in [n.split(":")[0] for n in names]

    def _ollama_alive(self) -> bool:
        """GET {base}/api/tags 探测 Ollama 在线且任一本地候选模型已存在；失败静默 False。"""
        try:
            req = urllib.request.Request(self._ollama_base() + "/api/tags",
                                         headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8", "replace"))
            names = [m.get("name", "") for m in data.get("models", [])]
            return any(self._model_present(w, names)
                       for w in self._ollama_candidate_models())
        except Exception as e:
            logger.info("Ollama 探测不可用: %s", e)
            return False

    def _ollama_chat_once(self, model: str, system_prompt: str,
                          user_prompt: str, temperature: float,
                          max_tokens: int) -> str:
        """对单个 Ollama 模型发起一次 /api/chat；连接失败抛 RuntimeError。"""
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt or "你是 LoveAI 的本地中文助手。"},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": float(temperature),
                        "num_predict": int(max_tokens)},
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self._ollama_base() + "/api/chat",
                                     data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120.0) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama 调用失败({model}): {e}") from e
        return (data.get("message", {}) or {}).get("content", "") or ""

    def _call_l2_ollama(self, system_prompt: str, user_prompt: str,
                        temperature: float, max_tokens: int) -> str:
        """经 Ollama /api/chat 跑本地 Qwen：主力(1.5B)优先，空答/缺失则快速兜底(0.5B)；全失败抛错由 chat() 降级。"""
        last_err = None
        for model in self._ollama_candidate_models():
            try:
                content = self._ollama_chat_once(
                    model, system_prompt, user_prompt, temperature, max_tokens)
            except Exception as e:
                last_err = e
                logger.info("本地模型 %s 调用失败，尝试兜底模型: %s", model, e)
                continue
            if content and content.strip():
                return content
            logger.info("本地模型 %s 返回空内容，尝试兜底模型。", model)
        if last_err is not None:
            raise last_err
        raise RuntimeError("Ollama 本地候选模型均不可用或返回空内容")

    # ------------------------------------------------------------------ #
    # P-10 末级：无任何生成模型时的纯统计反应分布（不硬失败）
    # ------------------------------------------------------------------ #
    def _stat_fallback_text(self, user_prompt: str) -> str:
        """所有生成模型不可用时，以收缩历史概率+情境检索输出统计型反应分布。

        明确标注"无生成模型，仅统计预测"；统计供应商缺失/异常时回退人群先验，
        本方法自身不抛错（返回空串由 chat() 再落规则兜底）。
        """
        try:
            from services.local_statistical_fallback import (
                LocalStatisticalFallback)
            context = {"user_text": user_prompt}
            fb = None
            if callable(self._stat_fallback_supplier):
                try:
                    fb = self._stat_fallback_supplier(context)
                except Exception as e:  # noqa: BLE001
                    logger.info("统计兜底供应商不可用，用人群先验: %s", e)
            if fb is None:
                fb = LocalStatisticalFallback(
                    history_counts={}, retriever=lambda ctx: [])
            out = fb.predict(context_features=context)
            dist = out.get("distribution", {}) or {}
            top = [(k, float(v)) for k, v in
                   sorted(dist.items(), key=lambda kv: kv[1], reverse=True)
                   if float(v) > 0][:5]
            lines = [out.get("annotation", "无生成模型，仅统计预测")]
            for k, v in top:
                lines.append(f"· {k}：约{round(v * 100)}%")
            if out.get("evidence_insufficient"):
                lines.append("当前情境样本少，统计结果仅供参考。")
            return "\n".join(lines)
        except Exception as e:  # noqa: BLE001
            logger.info("统计兜底不可用，回落规则: %s", e)
            return ""

    def _ensure_l2_loaded(self) -> None:
        """懒加载 L2 模型：llama.cpp 直接跑 GGUF 优先，Ollama HTTP 为可选预装。"""
        if self.l2_loaded and not self._l2_dirty:
            return

        if self._l2_dirty:
            # LoRA 适配器切换：丢弃旧权重，强制重载
            logger.info("LoRA 适配器已切换，卸载 L2 旧权重准备重载")
            self._l2_model = None
            self.l2_loaded = False
            self._l2_dirty = False

        adapter_name = self.get_current_lora()
        lora_dir = self._resolve_path(
            self.config.get("llm.local.l2.lora_dir", "models_local/lora")
        )
        adapter_path = os.path.join(lora_dir, adapter_name)

        # ---- 后端一：llama.cpp（首选，直接跑 GGUF）----
        try:
            from llama_cpp import Llama
        except ImportError as e:
            logger.info("llama-cpp-python 未安装，L2 尝试 Ollama / transformers 后端: %s", e)
            Llama = None  # type: ignore

        if Llama is not None:
            gguf_rel = self.config.get(
                "llm.local.l2.gguf_path",
                "models_local/qwen2.5-0.5b-instruct-q4_k_m.gguf",
            )
            gguf_path = self._resolve_path(gguf_rel)
            if not os.path.exists(gguf_path):
                raise RuntimeError(f"L2 GGUF 文件不存在: {gguf_path}")
            try:
                kwargs: dict[str, Any] = dict(
                    model_path=gguf_path, n_ctx=4096, n_threads=4, verbose=False,
                )
                # 传 LoRA 适配器目录（若存在）
                if os.path.isdir(adapter_path):
                    kwargs["lora_path"] = adapter_path
                self._l2_model = Llama(**kwargs)
                self._l2_backend = "llama_cpp"
                self.l2_loaded = True
                self.l2_available = True
                logger.info("L2(llama.cpp) 加载完成: %s adapter=%s", gguf_path, adapter_name)
                return
            except Exception as e:
                logger.warning("L2 llama.cpp 加载失败，降级 Ollama/transformers: %s", e)
                self._l2_model = None

        # ---- 后端二：Ollama HTTP（可选预装，默认关，用户显式开才试）----
        # 想装就装，不想装就算了；默认 False，不主动探测。
        if self._flag_yes("llm.local.l2.use_ollama_http", False):
            try:
                if self._ollama_alive():
                    self._l2_model = None  # 权重在 Ollama 服务端，本进程不持有
                    self._l2_backend = "ollama_http"
                    self.l2_loaded = True
                    self.l2_available = True
                    logger.info("L2(ollama_http) 就绪: %s model=%s",
                                self._ollama_base(), self._ollama_model_name())
                    return
                logger.info("Ollama 不在线或无目标模型，回退 transformers")
            except Exception as e:
                logger.warning("Ollama 后端探测异常，回退: %s", e)

        # ---- 后端三：transformers + peft ----
        try:
            import torch  # noqa: F401
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from peft import PeftModel
        except ImportError as e:
            self.l2_available = False
            raise RuntimeError(f"transformers/peft/torch 均不可用，L2 本地大模型不可用: {e}")

        base_rel = self.config.get(
            "llm.local.l2.base_model_path",
            "models_local/Qwen2.5-0.5B-Instruct/Qwen--Qwen2.5-0.5B-Instruct/snapshots/master",
        )
        base_path = self._resolve_path(base_rel)
        if not os.path.isdir(base_path):
            # 兼容：直接指到整个 Qwen 目录时回退找 snapshots
            raise RuntimeError(f"L2 transformers 基座目录不存在: {base_path}")

        try:
            self._l2_tokenizer = AutoTokenizer.from_pretrained(base_path)
            model = AutoModelForCausalLM.from_pretrained(base_dir=base_path)
            if os.path.isdir(adapter_path):
                model = PeftModel.from_pretrained(model, adapter_path)
            self._l2_model = model
            self._l2_backend = "transformers"
            self.l2_loaded = True
            self.l2_available = True
            logger.info("L2(transformers+peft) 加载完成: %s adapter=%s", base_path, adapter_name)
        except Exception as e:
            self.l2_available = False
            self._l2_model = None
            raise RuntimeError(f"L2 transformers 加载失败: {e}")

    # ------------------------------------------------------------------ #
    # L1：smollm2-135m ONNX 量化
    # ------------------------------------------------------------------ #
    @staticmethod
    def _has_cjk(text: str) -> bool:
        """是否含中日韩统一表意文字（用于判定是否中文任务）。"""
        return any('\u4e00' <= ch <= '\u9fff' for ch in (text or ""))

    def _l1_allows_chinese_generation(self) -> bool:
        """诊断/评测开关：默认 False——L1(SmolLM2) 不生成中文（会复读/死循环/语义乱码）。"""
        return self._flag_yes("llm.local.l1.allow_chinese_generation", False)

    def _call_l1(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """调用 L1 最小模型（onnxruntime），贪心解码，限长保证 <1 秒。

        Step1(v3.2) 中文分层：实测 SmolLM2-135M 中文为字节级 BPE，自由生成即复读/死循环，
        因此【中文自由生成一律拒绝】，抛错由 chat() 降级到 L2(Qwen/Ollama)→free→L3→规则兜底。
        L1 仅承担不依赖中文生成的轻活：固定标签集打分分类见 classify_l1()。
        """
        # ---- 中文自由生成守门：不加载模型直接拒绝，避免乱码输出 ----
        if (not self._l1_allows_chinese_generation()
                and self._has_cjk(user_prompt)):
            raise RuntimeError(
                "L1(SmolLM2-135M) 中文自由生成能力不足（实测复读/死循环），"
                "拒绝生成中文，已转上层 L2(Qwen)/free/L3")

        self._ensure_l1_loaded()
        if self._l1_session is None or self._l1_tokenizer is None:
            raise RuntimeError("L1 模型未加载")

        import numpy as np

        prompt = (
            "<|im_start|>system\n" + system_prompt + "<|im_end|>\n"
            "<|im_start|>user\n" + user_prompt + "<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        enc = self._l1_tokenizer.encode(prompt)
        input_ids = np.array([enc.ids], dtype=np.int64)
        eos_id = self._l1_tokenizer.token_to_id("<|im_end|>")

        sess = self._l1_session
        in_meta = sess.get_inputs()
        in_names = [i.name for i in in_meta]
        shape_map = {i.name: i.shape for i in in_meta}
        out_names = [o.name for o in sess.get_outputs()]
        logits_out = out_names[0]                       # 'logits'

        # past_key_values.i.x -> present.i.x
        past_names = [n for n in in_names if n.startswith("past_key_values.")]

        def present_for(past: str) -> str:
            return "present." + past.split(".", 1)[1]   # '0.key'

        def empty_past(name: str):
            sh = shape_map[name]                        # [batch, kv_heads, seq, head_dim]
            return np.zeros((1, int(sh[1]), 0, int(sh[3])), dtype=np.float32)

        def run_step(feeds):
            res = sess.run(out_names, feeds)
            return dict(zip(out_names, res))

        # ---- 首次：完整 prompt + 空 KV ----
        feeds: dict[str, Any] = {
            "input_ids": input_ids,
            "attention_mask": np.ones_like(input_ids, dtype=np.int64),
        }
        for pn in past_names:
            feeds[pn] = empty_past(pn)
        rm = run_step(feeds)
        next_id = rm[logits_out][:, -1, :].argmax(-1, keepdims=True).astype(np.int64)
        kv = {pn: rm[present_for(pn)] for pn in past_names}

        gen = [int(next_id[0, 0])]
        cur_len = input_ids.shape[1] + 1
        # ---- 后续：单 token + 历史 KV 增量 ----
        for _ in range(min(max_tokens, self._L1_MAX_NEW_TOKENS) - 1):
            if eos_id is not None and gen[-1] == eos_id:
                break
            feeds = {"input_ids": next_id,
                     "attention_mask": np.ones((1, cur_len), dtype=np.int64)}
            for pn in past_names:
                feeds[pn] = kv[pn]
            rm = run_step(feeds)
            next_id = rm[logits_out][:, -1, :].argmax(-1, keepdims=True).astype(np.int64)
            gen.append(int(next_id[0, 0]))
            kv = {pn: rm[present_for(pn)] for pn in past_names}
            cur_len += 1

        if gen and gen[-1] == eos_id:
            gen = gen[:-1]
        return self._l1_tokenizer.decode(gen)

    def _ensure_l1_loaded(self) -> None:
        """懒加载 L1：onnxruntime InferenceSession + tokenizers.Tokenizer。"""
        if self.l1_loaded:
            return
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as e:
            self.l1_available = False
            raise RuntimeError(f"onnxruntime/tokenizers 未安装，L1 不可用: {e}")

        onnx_path = self._resolve_path(
            self.config.get("llm.local.l1.onnx_path", "models_local/smollm2-135m/model_q4.onnx")
        )
        tok_path = self._resolve_path(
            self.config.get("llm.local.l1.tokenizer_path", "models_local/smollm2-135m/tokenizer.json")
        )
        if not os.path.exists(onnx_path):
            self.l1_available = False
            raise RuntimeError(f"L1 ONNX 文件不存在: {onnx_path}")
        if not os.path.exists(tok_path):
            self.l1_available = False
            raise RuntimeError(f"L1 tokenizer 不存在: {tok_path}")

        try:
            self._l1_session = ort.InferenceSession(
                onnx_path, providers=["CPUExecutionProvider"]
            )
            self._l1_tokenizer = Tokenizer.from_file(tok_path)
            self.l1_loaded = True
            self.l1_available = True
            logger.info("L1(onnx) 加载完成: %s", onnx_path)
        except Exception as e:
            self.l1_available = False
            self._l1_session = None
            raise RuntimeError(f"L1 加载失败: {e}")

    # ------------------------------------------------------------------ #
    # L1 轻活：固定标签集打分分类（非生成，teacher-forcing 打分）
    # ------------------------------------------------------------------ #
    def classify_l1(self, text: str, labels: list[str],
                    system_prompt: str = "你是一个中文情绪分类器。") -> str:
        """用 L1(SmolLM2) 在固定标签集里打分选标签——不自由生成中文。

        原理：对每个候选标签 L，teacher-force 拼接 `prompt + L`，取模型对 L 的 token
        对数概率之和，选总分最高者。这把 135M 小模型当「评分头」用，规避其自由生成
        复读/死循环的缺陷。标签集固定、无自由文本输出，符合「L1 只做不依赖中文生成的轻活」。

        Args:
            text: 待分类文本。
            labels: 候选标签列表（非空）。
            system_prompt: 分类系统提示。

        Returns:
            得分最高的标签字符串；加载失败/无标签时抛出由调用方降级。
        """
        if not labels:
            raise ValueError("classify_l1: labels 不能为空")
        self._ensure_l1_loaded()
        if self._l1_session is None or self._l1_tokenizer is None:
            raise RuntimeError("L1 模型未加载")
        import numpy as np

        tok = self._l1_tokenizer
        sess = self._l1_session
        in_meta = sess.get_inputs()
        in_names = [i.name for i in in_meta]
        shape_map = {i.name: i.shape for i in in_meta}
        out_names = [o.name for o in sess.get_outputs()]
        logits_out = out_names[0]
        past_names = [n for n in in_names if n.startswith("past_key_values.")]

        def empty_past(name: str):
            sh = shape_map[name]
            return np.zeros((1, int(sh[1]), 0, int(sh[3])), dtype=np.float32)

        label_set = "[" + ", ".join(labels) + "]"
        prefix = (
            "<|im_start|>system\n" + system_prompt
            + f"从给定标签中选最合适的一个，只输出标签词。标签：{label_set}。<|im_end|>\n"
            "<|im_start|>user\n" + text + "<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        prefix_ids = tok.encode(prefix).ids

        best_label = labels[0]
        best_score = -float("inf")
        for lab in labels:
            lab_ids = tok.encode(lab).ids
            if not lab_ids:
                continue
            seq = np.array([prefix_ids + lab_ids], dtype=np.int64)
            feeds: dict[str, Any] = {
                "input_ids": seq,
                "attention_mask": np.ones_like(seq, dtype=np.int64),
            }
            for pn in past_names:
                feeds[pn] = empty_past(pn)
            res = sess.run(out_names, feeds)
            logits = dict(zip(out_names, res))[logits_out][0]  # [seq_len, vocab]
            # logits[t] 预测 token t+1；标签 token 位置 = prefix_len .. prefix_len+len-1
            lp_len = len(prefix_ids)
            n_lab = len(lab_ids)
            score = 0.0
            for k in range(n_lab):
                pos = lp_len + k - 1      # 预测第 lp_len+k 个 token 的 logits 位置
                logit_row = logits[pos]
                # log_softmax（数值稳定）
                m = logit_row.max()
                log_z = m + float(np.log(np.exp(logit_row - m).sum()))
                score += float(logit_row[lab_ids[k]] - log_z)
            if score > best_score:
                best_score = score
                best_label = lab
        logger.info("L1 classify_l1: text=%r labels=%s -> %s (score=%.2f)",
                    text, labels, best_label, best_score)
        return best_label

    # ------------------------------------------------------------------ #
    # 规则兜底
    # ------------------------------------------------------------------ #
    def _rule_reply(self, user_prompt: str) -> str:
        """所有模型层不可用时的关键词规则兜底回复。"""
        u = user_prompt.strip()
        if any(k in u for k in ("你好", "您好", "嗨", "在吗", "哈喽", "hi", "hello")):
            return "你好呀，我在呢，有什么想聊的？"
        if any(k in u for k in ("谢谢", "感谢", "辛苦了")):
            return "不客气，能帮到你我也很开心。"
        if any(k in u for k in ("再见", "拜拜", "先走了")):
            return "拜拜，随时回来找我聊。"
        if any(k in u for k in ("晚安", "睡了")):
            return "晚安，好好休息，明天见。"
        return "嗯，我在认真听呢，可以再多说一点吗？"

    # ------------------------------------------------------------------ #
    # 空闲自动卸载
    # ------------------------------------------------------------------ #
    def auto_unload_check(self) -> None:
        """定时器周期调用：L2 大模型空闲超时则卸载权重释放内存。"""
        if self.l2_loaded and (time.time() - self.last_call_time) > self.unload_timeout:
            logger.info("L2 模型空闲超过 %.0f 秒，自动卸载", self.unload_timeout)
            self._l2_model = None
            self.l2_loaded = False
            self._l2_backend = ""

    # ------------------------------------------------------------------ #
    # Prompt 模板管理
    # ------------------------------------------------------------------ #
    def load_prompt(self, template_name: str, **kwargs) -> str:
        """加载 prompts/ 目录下模板，{{key}} 替换变量。"""
        if template_name in self._prompt_cache:
            template = self._prompt_cache[template_name]
        else:
            template_path = os.path.join(
                self.project_root, "prompts", f"{template_name}.txt"
            )
            if not os.path.exists(template_path):
                raise FileNotFoundError(f"Prompt模板不存在: {template_path}")
            with open(template_path, "r", encoding="utf-8") as f:
                template = f.read()
            self._prompt_cache[template_name] = template

        result = template
        for k, v in kwargs.items():
            result = result.replace("{{" + k + "}}", str(v))
        return result

    # ------------------------------------------------------------------ #
    # 复杂度判定
    # ------------------------------------------------------------------ #
    def _judge_complexity(self, text: str) -> str:
        """
        规则判定任务复杂度：
          - 问候/短句/长度 < 15 -> l1
          - 长度 > 120 或命中复杂关键词 -> l3
          - 其余日常对话 -> l2
        """
        if len(text) < self._SIMPLE_LENGTH_THRESHOLD:
            return "l1"
        if any(k in text for k in self._GREETING_KEYWORDS):
            return "l1"
        if len(text) > self._LONG_LENGTH_THRESHOLD:
            return "l3"
        for kw in self._COMPLEX_KEYWORDS:
            if kw in text:
                return "l3"
        return "l2"

    # ------------------------------------------------------------------ #
    # R3.3-03 难度分级（easy/medium/hard）——规则版，可解释、可配
    # ------------------------------------------------------------------ #
    def get_difficulty_router(self):
        """懒创建并复用 DifficultyRouter；预留注入模型分类器的入口。"""
        if self._difficulty_router is None:
            from services.difficulty_router import DifficultyRouter
            self._difficulty_router = DifficultyRouter(self.config)
        return self._difficulty_router

    def classify_difficulty(self, text: str) -> str:
        """对外统一难度档位：easy / medium / hard。任何异常回落 medium，不崩主流程。"""
        try:
            return self.get_difficulty_router().classify(text)
        except Exception as e:  # noqa: BLE001
            logger.warning("classify_difficulty 异常，回落 medium: %s", e)
            return "medium"

    def explain_difficulty(self, text: str) -> dict:
        """可解释判定明细（命中关键词/长度/原因），供日志与前端展示。"""
        try:
            return self.get_difficulty_router().explain(text)
        except Exception as e:  # noqa: BLE001
            return {"tier": "medium", "length": 0, "matched_hard": [],
                    "matched_easy": [], "reason": f"异常回落:{e}"}

    # ------------------------------------------------------------------ #
    # R3.3-04 四层分工：草稿+润色两段式（draft -> polish 出灵气）
    # ------------------------------------------------------------------ #
    def chat_with_polish(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1200,
    ) -> dict:
        """草稿层(最便宜)先出粗稿，再由润色层(中端)润色成稿。

        两段任一段失败都不硬崩：草稿失败则直接跳过润色，润色失败则保留草稿原文。
        Returns: {"draft": str, "polished": str, "steps": [...]}
        """
        steps = []
        try:
            draft = self.chat(system_prompt=system_prompt, user_prompt=user_prompt,
                              tier="draft", temperature=0.5, max_tokens=max_tokens)
            steps.append({"stage": "draft", "ok": bool(draft),
                          "layer": self.last_layer})
        except Exception as e:  # noqa: BLE001
            draft = ""
            steps.append({"stage": "draft", "ok": False, "hint": str(e)[:80]})

        polished = draft
        if draft:
            try:
                polish_sys = ("你是语气润色师：在不改变原意的前提下，把草稿改得更自然、"
                              "有情绪张力、像真人说话；只输出润色后的文本。")
                polished = self.chat(system_prompt=polish_sys, user_prompt=draft,
                                     tier="polish", temperature=temperature,
                                     max_tokens=max_tokens)
                if polished and polished.strip():
                    steps.append({"stage": "polish", "ok": True,
                                  "layer": self.last_layer})
                else:
                    polished = draft
                    steps.append({"stage": "polish", "ok": False, "hint": "空输出"})
            except Exception as e:  # noqa: BLE001
                polished = draft
                steps.append({"stage": "polish", "ok": False, "hint": str(e)[:80]})
        return {"draft": draft, "polished": polished, "steps": steps}

    # ------------------------------------------------------------------ #
    # R3.3-04 模拟层触发闸门：只回结构化字段，10-20% 调用才跑贵模型
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_structured_sim(text: str) -> dict:
        """从模型输出里尽量抠出结构化字段；抠不出就给安全默认值。"""
        import re as _re
        out = {"emotion": "neutral", "pickup": False, "progress": 0.0,
               "raw": (text or "").strip()[:400]}
        t = (text or "").lower()
        if any(k in t for k in ("positive", "积极", "正面", "开心", "好感")):
            out["emotion"] = "positive"
        elif any(k in t for k in ("negative", "消极", "负面", "冷淡", "反感")):
            out["emotion"] = "negative"
        if any(k in t for k in ("接话", "会回", "愿意回", "pickup\": true", "true")):
            out["pickup"] = True
        m = _re.search(r"progress[\"'：:\s]*([0-9.]+)", t)
        if m:
            try:
                out["progress"] = max(0.0, min(1.0, float(m.group(1))))
            except ValueError:
                pass
        return out

    def simulate_reply(
        self,
        candidate_text: str,
        context: str = "",
        *,
        expand: bool = False,
        temperature: float = 0.7,
    ) -> dict:
        """模拟层：对单条候选话术跑对方心理模拟（贵模型 gated）。

        触发闸门（R3.3-04）：
          - 判定为 easy（日常寒暄）-> 不跑数字分身，直接查历史概率表，返回轻量结果；
          - medium/hard 才上 simulate 链（云端优先）；
          - 只回结构化字段（情绪正负 / 接不接话 / 推进度）；
          - 仅当 expand=True 时，才在结构化结果上再生成大段展开。
        任何异常都回落安全 dict，绝不把主流程崩掉。
        """
        tier = self.classify_difficulty(context or candidate_text)
        gate = {"tier": tier, "ran_sim": False}

        try:
            if tier == "easy":
                # 日常寒暄：跳过数字分身，走历史概率（此处给保守先验，接入真历史表后替换）
                return {**gate, "emotion": "neutral", "pickup": True,
                        "progress": 0.1, "source": "history_prob",
                        "body": "", "skipped_sim": True}

            sys = ("你在做对方收到这条话术后的心理模拟。只输出 JSON："
                   "{\"emotion\": positive|neutral|negative, "
                   "\"pickup\": true|false, \"progress\": 0~1}。不要多余解释。")
            user = f"[情境]\n{context}\n[候选话术]\n{candidate_text}"
            raw = self.chat(sys, user, tier="simulate",
                            temperature=temperature, max_tokens=300,
                            allow_rule_fallback=False)
            gate["ran_sim"] = bool(raw)
            parsed = self._parse_structured_sim(raw)

            body = ""
            if expand and raw:
                # 用户点「展开」才生成大段
                try:
                    body = self.chat(
                        "你在站在对方视角，用一两段话复盘收到这条话术后的真实心理活动。",
                        user, tier="simulate", temperature=temperature,
                        max_tokens=800, allow_rule_fallback=False) or ""
                except Exception as e:  # noqa: BLE001
                    logger.info("模拟展开生成失败（忽略）: %s", e)
            return {**gate, **parsed, "source": "simulate", "body": body}
        except Exception as e:  # noqa: BLE001
            logger.warning("simulate_reply 异常，回落安全结果: %s", e)
            return {**gate, "emotion": "neutral", "pickup": False,
                    "progress": 0.0, "source": "safe_default", "body": "",
                    "error": str(e)[:120]}

    def simulate_candidates(self, lines: list, context: str = "",
                            top_n: int = 2) -> list[dict]:
        """对多条候选话术先粗筛，只对 top_n 跑贵模型模拟（省钱）。

        - easy 难度：全部直接标记跳过（不跑数字分身）；
        - 否则按长度/简单规则给粗分，取前 top_n 进 simulate_reply，其余给轻量估计。
        """
        out: list[dict] = []
        tier = self.classify_difficulty(context or " ".join(lines))
        ranked = sorted(
            range(len(lines)),
            key=lambda i: len((lines[i] or "").strip()),
            reverse=True,
        )
        simulate_idx = set(ranked[:max(1, top_n)]) if tier != "easy" else set()
        for i, line in enumerate(lines):
            if i in simulate_idx:
                r = self.simulate_reply(line, context=context)
            else:
                r = {"tier": tier, "ran_sim": False, "emotion": "neutral",
                     "pickup": True, "progress": 0.1, "source": "coarse",
                     "body": "", "skipped_sim": True}
            out.append({"index": i, "line": line, **r})
        return out


    # ------------------------------------------------------------------ #
    # LoRA 适配器管理
    # ------------------------------------------------------------------ #
    def list_lora_adapters(self) -> list[str]:
        """扫描 lora_dir 下含 adapter_config.json 的子目录，返回适配器名称列表。"""
        lora_dir = self._resolve_path(
            self.config.get("llm.local.l2.lora_dir", "models_local/lora")
        )
        names: list[str] = []
        if not os.path.isdir(lora_dir):
            return names
        for entry in sorted(os.listdir(lora_dir)):
            sub = os.path.join(lora_dir, entry)
            if os.path.isfile(os.path.join(sub, "adapter_config.json")):
                names.append(entry)
        return names

    def get_current_lora(self) -> str:
        """返回当前生效的 LoRA 适配器名。"""
        return str(self.config.get("llm.local.l2.lora_adapter", "loveai-core") or "loveai-core")

    def select_lora_adapter(self, name: str) -> None:
        """
        选择 LoRA 适配器：写入配置；若 L2 已加载则标记 dirty，下次调用时重载。

        Args:
            name: models_local/lora/ 下的适配器目录名。
        """
        available = self.list_lora_adapters()
        if available and name not in available:
            raise ValueError(f"LoRA 适配器不存在: {name}（可选: {available}）")
        self.config.set("llm.local.l2.lora_adapter", name)
        if self.l2_loaded:
            self._l2_dirty = True
        logger.info("LoRA 适配器切换为: %s", name)

    # ------------------------------------------------------------------ #
    # 训练数据收集
    # ------------------------------------------------------------------ #
    def _ensure_training_table(self) -> None:
        """训练数据表自动建表（幂等）。"""
        if self.db is None or self._training_table_ready:
            return
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS training_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reply_text TEXT,
                context TEXT,
                person_id TEXT,
                source_model TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        self._training_table_ready = True

    def mark_good_reply(
        self,
        reply_text: str,
        context: Optional[str] = None,
        person_id: Optional[str] = None,
    ) -> int:
        """
        用户标记"好的回复"，存入 training_data 表，供后续 LoRA 微调。

        Args:
            reply_text: 被标记为好的回复文本。
            context: 对话上下文（可选）。
            person_id: 关联人物 id（可选）。

        Returns:
            新插入行的 id；db 不可用时返回 -1。
        """
        if self.db is None:
            logger.warning("mark_good_reply: db 未注入，数据未落库")
            return -1
        self._ensure_training_table()
        result = self.db.execute(
            """
            INSERT INTO training_data (reply_text, context, person_id, source_model)
            VALUES (:reply_text, :context, :person_id, :source_model)
            """,
            {
                "reply_text": reply_text,
                "context": context or "",
                "person_id": person_id or "",
                "source_model": self.last_layer,
            },
        )
        return int(result.lastrowid or 0)

    def get_training_stats(self) -> dict:
        """返回训练数据统计：总数、按 person_id 分组、按 source_model 分组。"""
        stats: dict[str, Any] = {"total": 0, "by_person": {}, "by_model": {}}
        if self.db is None:
            return stats
        self._ensure_training_table()
        total_row = self.db.fetch_one("SELECT COUNT(*) AS c FROM training_data")
        stats["total"] = int(total_row["c"]) if total_row else 0
        for row in self.db.fetch_all(
            "SELECT person_id, COUNT(*) AS c FROM training_data GROUP BY person_id"
        ):
            stats["by_person"][row["person_id"] or ""] = int(row["c"])
        for row in self.db.fetch_all(
            "SELECT source_model, COUNT(*) AS c FROM training_data GROUP BY source_model"
        ):
            stats["by_model"][row["source_model"] or ""] = int(row["c"])
        return stats

    def export_training_data(self, output_path: str) -> int:
        """
        导出全部训练数据为 JSONL（每行 {"text": ...}），供 LoRA 微调。

        Args:
            output_path: 输出 JSONL 路径。

        Returns:
            导出条数。
        """
        if self.db is None:
            raise RuntimeError("db 未注入，无法导出训练数据")
        self._ensure_training_table()
        rows = self.db.fetch_all("SELECT reply_text FROM training_data ORDER BY id")
        out_path = self._resolve_path(output_path)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        count = 0
        with open(out_path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps({"text": row["reply_text"]}, ensure_ascii=False) + "\n")
                count += 1
        logger.info("训练数据导出完成: %s 共 %d 条", out_path, count)
        return count

    # ------------------------------------------------------------------ #
    # 旧接口兼容层（safety_filter 等老调用方依赖）
    # ------------------------------------------------------------------ #
    def _call_cloud(
        self,
        text: str,
        complexity: str = "auto",
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        """兼容旧接口：单段文本走 L3 云端。"""
        return self._call_l3("", text, temperature, max_tokens)

    def _call_local(
        self,
        text: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        """兼容旧接口：单段文本走 L2 本地大模型。"""
        return self._call_l2("", text, temperature, max_tokens)

    @property
    def local_loaded(self) -> bool:
        """兼容旧状态字段：本地大模型是否已加载。"""
        return self.l2_loaded

    @property
    def local_available(self) -> bool:
        """兼容旧状态字段：本地大模型是否可用。"""
        return self.l2_available

    # ------------------------------------------------------------------ #
    # R3.3-41 4 个模型缺口诊断（L1中文/L2本地/L3云端/免费通道）
    # ------------------------------------------------------------------ #
    def diagnose_model_gaps(self) -> list[dict]:
        """返回当前各层真实状态 + 每个 gap 的 fallback 路径（R3.3-41）。

        不抛栈：任何探测失败都视为「不可用」并标注原因，绝不把诊断本身搞崩。
        每个 dict 字段：
            layer        : "l1_zh" / "l2_ollama" / "l3_cloud" / "free_catalog"
            title        : 人类可读标题
            available    : bool，该层当前是否能产出真实回复
            degraded     : bool，是否因已知能力缺陷被主动降级
            reason       : 不可用/降级原因（短句）
            fallback_to  : list[str]，按降级链顺序给出下一跳（含规则兜底）
            suggestion   : 落地根治建议（用户该做什么）
        """
        gaps: list[dict] = []

        # ---- Gap 1: L1 中文自由生成 ----
        # L1(SmolLM2-135M) 实测中文 BPE 乱码/复读，运行期 _call_l1 对中文主动 raise。
        # 该「gap」是有意设计的降级点：L1 只承担英文/分类轻活，中文必须落到 L2(ollama)。
        l1_zh_ok = False
        l1_zh_reason = ("L1(SmolLM2-135M) 中文 BPE 乱码/复读，"
                        "_call_l1 对中文自由生成主动 raise，已按设计降级")
        try:
            if self._l1_allows_chinese_generation():
                l1_zh_ok = True
                l1_zh_reason = "诊断开关 allow_chinese_generation=True，L1 允许中文（仅评测用）"
        except Exception as e:  # noqa: BLE001
            l1_zh_reason = f"配置读取异常: {e}"
        gaps.append({
            "layer": "l1_zh",
            "title": "L1 中文自由生成",
            "available": l1_zh_ok,
            "degraded": (not l1_zh_ok),
            "reason": l1_zh_reason,
            "fallback_to": ["l2_ollama", "free_router", "rules_template"],
            "suggestion": "保持现状：中文一律落 L2(ollama Qwen)；不要试图让 135M 小模型直接生成中文。",
        })

        # ---- Gap 2: L2 ollama 本机模型 ----
        # 真探 /api/tags，不硬编码模型名；探不到就标 unavailable。
        l2_ok = False
        l2_models: list[str] = []
        l2_reason = ""
        try:
            req = urllib.request.Request(self._ollama_base() + "/api/tags",
                                         headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8", "replace"))
            l2_models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            if l2_models:
                l2_ok = True
                l2_reason = f"ollama 在线，本机模型 {len(l2_models)} 个: {l2_models[:5]}"
            else:
                l2_reason = "ollama 在线但 /api/tags 返回空（没拉过模型）"
        except Exception as e:  # noqa: BLE001
            l2_reason = f"ollama 未起或不可达: {type(e).__name__}: {e}"
        gaps.append({
            "layer": "l2_ollama",
            "title": "L2 本地 Ollama",
            "available": l2_ok,
            "degraded": (not l2_ok),
            "reason": l2_reason,
            "models": l2_models,
            "fallback_to": ["free_router", "rules_template"],
            "suggestion": "启动 `ollama serve`；模型名自动从 /api/tags 发现，无需硬编码。",
        })

        # ---- Gap 3: L3 云端 Key ----
        providers = []
        try:
            providers = self._l3_providers()
        except Exception as e:  # noqa: BLE001
            providers = []
            l3_reason = f"读取 L3 配置异常: {e}"
        l3_ok = bool(providers)
        if l3_ok:
            l3_reason = f"已配置 {len(providers)} 个 provider: {[p['label'] for p in providers]}"
        else:
            l3_reason = ("未配置 DeepSeek/豆包/通用 cloud 的 api_key；"
                         "L3 不会静默失败，_call_l3 会主动 raise 让 chat() 降级")
        gaps.append({
            "layer": "l3_cloud",
            "title": "L3 云端付费 API",
            "available": l3_ok,
            "degraded": (not l3_ok),
            "reason": l3_reason,
            "fallback_to": ["free_router", "l2_ollama", "rules_template"],
            "suggestion": "在设置页或 config.json 配 `llm.l3.deepseek.api_key`（DeepSeek 最便宜）；"
                          "不配也能用，只是复杂推理走本地小模型。",
        })

        # ---- Gap 4: 免费通道动态目录 ----
        free_ok = False
        free_reason = ""
        free_counts: dict[str, int] = {}
        try:
            router = self.get_free_router()
            # 真探一次：OpenRouter 公开目录 / NVIDIA 公开 md；失败被内部吞掉
            status = router.refresh_catalog(force=False) if hasattr(router, "refresh_catalog") else {}
            for src, info in (status or {}).items():
                if isinstance(info, dict):
                    free_counts[src] = len(info.get("models", []) or [])
            if any(c > 0 for c in free_counts.values()):
                free_ok = True
                free_reason = f"动态目录拉到模型: {free_counts}"
            else:
                # 即使网络失败，本机 ollama 也算免费通道兜底
                if l2_ok:
                    free_ok = True
                    free_reason = f"远端目录拉空，回退本机 ollama: {l2_models[:3]}"
                else:
                    free_reason = "OpenRouter/NVIDIA 目录均未拉到，且本机 ollama 不可用"
        except Exception as e:  # noqa: BLE001
            free_reason = f"free_model_router 异常: {type(e).__name__}: {e}"
        gaps.append({
            "layer": "free_catalog",
            "title": "免费通道动态目录 (OpenRouter/NVIDIA/Ollama)",
            "available": free_ok,
            "degraded": (not free_ok),
            "reason": free_reason,
            "catalog_counts": free_counts,
            "fallback_to": ["l2_ollama", "rules_template"],
            "suggestion": "网络通时自动拉 OpenRouter :free 与 NVIDIA NIM 目录；"
                          "网络断时回退本机 ollama；两者都没就走规则模板，绝不抛栈。",
        })

        return gaps

    # ------------------------------------------------------------------ #
    # 状态查询
    # ------------------------------------------------------------------ #
    def get_status(self) -> dict:
        """返回网关状态快照，供前端展示。"""
        gguf_rel = self.config.get(
            "llm.local.l2.gguf_path",
            "models_local/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        )
        return {
            "l1_available": self.l1_available,
            "l2_available": self.l2_available,
            "l3_available": bool(
                self.config.get("llm.cloud.api_key", "")
                or self.config.get("llm.api_key", "")
            ),
            "current_lora": self.get_current_lora(),
            "l1_loaded": self.l1_loaded,
            "l2_loaded": self.l2_loaded,
            "local_model_path": gguf_rel,
            "l2_backend": self._l2_backend,
            "last_layer": self.last_layer,
            # Step1 v3.2：本地 Ollama(Qwen) 通道如实上报
            "ollama_base": self._ollama_base(),
            "ollama_model": self._ollama_model_name(),
            "ollama_alive": self._ollama_alive(),
        }

    # ------------------------------------------------------------------ #
    # 多模型协同：L1预处理 → L3深度分析 → L2后处理
    # ------------------------------------------------------------------ #
    def chat_with_pipeline(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> dict:
        """多模型协同流水线。每步 try/except 隔离，失败自动降级。

        Returns:
            {"final_result": str, "pipeline_steps": [每步执行/耗时/摘要]}
        """
        steps = []

        # L1 预处理（轻量，失败跳过）
        pre = ""
        try:
            t0 = time.time()
            pre = self.chat(system_prompt="意图/情感/复杂度预处理", user_prompt=user_prompt,
                            complexity="l1", temperature=0.3, max_tokens=200)
            steps.append({"step": "L1_preprocess", "ok": True, "sec": round(time.time()-t0, 2)})
        except Exception as e:
            steps.append({"step": "L1_preprocess", "ok": False, "hint": str(e)[:80]})

        # L3 深度分析（失败降级到 L2）
        deep = ""
        try:
            t0 = time.time()
            ctx = f"[预处理] {pre}\n[任务] {user_prompt}" if pre else user_prompt
            deep = self.chat(system_prompt=system_prompt, user_prompt=ctx,
                             complexity="l3", temperature=temperature, max_tokens=max_tokens)
            steps.append({"step": "L3_deep", "ok": True, "sec": round(time.time()-t0, 2)})
        except Exception:
            try:
                t0 = time.time()
                deep = self.chat(system_prompt=system_prompt, user_prompt=user_prompt,
                                 complexity="l2", temperature=temperature, max_tokens=max_tokens)
                steps.append({"step": "L3_fallback_L2", "ok": True})
            except Exception as e:
                steps.append({"step": "L3_deep", "ok": False, "hint": str(e)[:80]})

        # L2 后处理（失败返回 L3 原始）
        final = deep
        try:
            t0 = time.time()
            post = self.chat(system_prompt="用用户风格调整语气，整理格式", user_prompt=deep,
                             complexity="l2", temperature=0.5, max_tokens=max_tokens)
            if post:
                final = post
            steps.append({"step": "L2_postprocess", "ok": True, "sec": round(time.time()-t0, 2)})
        except Exception as e:
            steps.append({"step": "L2_postprocess", "ok": False, "hint": str(e)[:80]})

        return {"final_result": final, "pipeline_steps": steps}

