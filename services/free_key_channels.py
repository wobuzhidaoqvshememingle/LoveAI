# -*- coding: utf-8 -*-
"""免费路由「需 Key 渠道」描述符（纯数据模块）

本模块只做一件事：把 `services/free_channel_catalog.py` 里那批
「需 Key」免费渠道（登记时 `code_path` 尚为「未接入（仅登记）」）落成一份
可直接被程序读的**渠道描述符**（base_url / 鉴权方式 / 配置键 / 默认候选模型 / 探测端点）。
R3.8（2026-10-17）起，这些描述符**已并入 `FreeModelRouter.candidates()`**（第 6 条来源）；
但受 fail-closed 价格门约束，**光填 key 仍不路由**——须把 id 登记进
`llm.free.trusted_models`（例 `groq:*`）才会进池。详见下方「接入状态」。

边界（刻意为之）
----------------
* **纯数据**：不发起任何网络请求、不 import 任何第三方库、不读环境变量以外的外部状态；
* **不写死任何 key**：只描述「key 该从哪个配置键读」，key 一律由调用方传入的 config 提供；
* **JSON 可序列化**：`json.dumps(as_dict())` 可直接跑通，方便 UI / 诊断页读；
* **可独立 import**：``python -c "import services.free_key_channels"`` 不产生副作用。

渠道清单与判据（**建立本模块时**的判据；R3.8 起这批**已并入降级链**）
--------------------------------------------------------------
当时取 ``FREE_CHANNEL_CATALOG`` 里 ``need_key is True`` 且 ``code_path`` 写
「未接入（仅登记）」的条目，共 10 条。
注意：清单落定后 ``FREE_CHANNEL_CATALOG`` 的 ``code_path`` 已于 R3.8 更新为
指向本模块，故**现在**再按「`code_path` 写『未接入（仅登记）』」这条字面判据去筛
会得到 **0 条**；本模块的 10 条以 ``FREE_KEY_CHANNELS`` 为准：
sambanova / zhipu / github_models / cloudflare_workers_ai / groq /
cerebras / cohere / google_gemini / mistral / huggingface。
（`openrouter_free`、`nvidia_nim` 虽同样 need_key，但 code_path 已指向
`services/free_model_router.py::_fetch_openrouter_free` / `_fetch_nvidia`，
属**已接入**，故不在本模块。）

字段约定
--------
    id                : 渠道唯一 id，与 free_channel_catalog 的 id 一致
    provider          : 接入降级链时的候选 provider 名（= 本模块 id）
    name              : 人类可读名
    base_url          : 官方 OpenAI 兼容端点；含 ``{account_id}`` 等占位时见 required_config
    auth_style        : 鉴权风格（``bearer`` / ``x-goog-api-key``）
    auth_header       : 该风格下的请求头模板，``{key}`` 为占位
    auth_alt          : 可选，另一套等价的鉴权写法（如 Gemini 原生路径）
    key_config        : 读 API Key 的配置键（点分路径，如 ``llm.free.groq_key``）
    models_config     : 读候选模型列表的配置键（逗号分隔）
    default_models    : 默认候选模型 id（2-3 个），来源见 models_source
    models_source     : 默认模型 id 的来源与日期（一手核实 / 镜像 / 未核验，如实标注）
    probe_path        : 相对 base_url 的探测路径（自检脚本用它发极小请求）
    extra_probes      : 可选，附加探测端点（如 token 校验端点）
    required_config   : 可选，占位符 -> 配置键（如 cloudflare 的 account_id）
    signup_url        : 注册/领 key 入口
    docs_url          : 官方文档入口
    free_tier_note    : 免费额度与限速形态（额度会漂移，以官方为准）
    env_key           : 可选，环境变量名（仅当调用方显式 include_env=True 时才读）
    auto_verifiable   : 本机能否用「配 key + 一次极小请求」自动判定可用性
    auto_verify_note  : 该判定成立/不成立的具体原因（含实测日期与原始错误）
    note              : 备注（口径纠正、端点迁移、fail-closed 提醒等）

⚠️ 安全约定
----------
``channels_with_keys()`` 返回的 dict **含明文 api_key**，仅供路由接线与自检使用；
调用方**不得**把它打印到日志、写入文件或回显到界面（自检脚本只打印掩码）。

接入状态（R3.8，2026-10-17 已应用）
-------------------------------
本模块描述符**已接入** `FreeModelRouter.candidates()` 的第 6 条来源
（落点 `services/free_model_router.py:1141-1161`），模型三级回退借道 `_models_for()`，
因此产出照样要过出口价格门。

**但「填了 key 就生效」依然不成立 —— 这是刻意的双闸：**

| 闸 | 条件 | 结果 |
|---|---|---|
| 第一道：来源闸 | 没配 key | **不产候选** |
| 第二道：价格门闸 | 配了 key，但没进 `llm.free.trusted_models` 白名单 | 判 `default_deny` 剔除 → **仍然 0 候选、0 出站** |

第二道闸为什么总关着：这 10 条**都没有目录抓取器** → 价格证据集（`free_ids` / `paid_ids`）
恒空 → 它们**只会**落到 `default_deny`。要真正进池必须**两步**
（例：`llm.free.groq_key=<key>` **加** `llm.free.trusted_models=groq:*`）。
理由见文档 §4.2 / §4.3（含 sambanova 实测 pricing 非 0 那条）：
`services/free_model_router.py` 里本模块渠道的价格门归宿。
"""
from __future__ import annotations

import os
from typing import Any, Optional

__all__ = [
    "CHANNELS_VERSION",
    "FREE_KEY_CHANNELS",
    "PROVIDER_IDS",
    "get_channel",
    "channel_ids",
    "mask_key",
    "key_for",
    "key_source",
    "models_for",
    "base_url_for",
    "missing_config",
    "channels_with_keys",
    "summary",
    "as_dict",
]

CHANNELS_VERSION = "2026-10-17-r3"

#: 掩码长度不足时的降级阈值（<= 该长度只留首尾各 1 字符）
_MASK_MIN_LEN = 8


# ====================================================================== #
# 渠道描述符（纯数据）
# ====================================================================== #
FREE_KEY_CHANNELS: list[dict[str, Any]] = [
    {
        "id": "sambanova",
        "provider": "sambanova",
        "name": "SambaNova Cloud",
        "base_url": "https://api.sambanova.ai/v1",
        "auth_style": "bearer",
        "auth_header": {"Authorization": "Bearer {key}"},
        "key_config": "llm.free.sambanova_key",
        "models_config": "llm.free.sambanova_models",
        "default_models": [
            "DeepSeek-V3.1",
            "Meta-Llama-3.3-70B-Instruct",
            "gpt-oss-120b",
        ],
        "models_source": (
            "端点实测 2026-10-17：匿名 GET https://api.sambanova.ai/v1/models = HTTP 200，"
            "返回真实目录共 6 个 id（DeepSeek-V3.1 / DeepSeek-V3.2 / Meta-Llama-3.3-70B-Instruct / "
            "MiniMax-M3 / gemma-4-31B-it / gpt-oss-120b）；此处取其中 3 个。"
            "注：本项目 registry 镜像里记的 Qwen2.5-72B-Instruct / DeepSeek-R1-Distill-Llama-70B "
            "在本轮实测目录中已不存在（镜像已过期）。"),
        "probe_path": "/models",
        "extra_probes": {},
        "required_config": {},
        "signup_url": "https://cloud.sambanova.ai",
        "docs_url": "https://docs.sambanova.ai",
        "free_tier_note": "Developer 计划免费 credits，按模型给速率限制（不是无限；额度以官方为准）",
        "env_key": "SAMBANOVA_API_KEY",
        "auto_verifiable": True,
        "auto_verify_note": (
            "端点匿名可达（2026-10-17 GET /models = 200，1340 B）；配 key 后可由自检脚本发一次 "
            "max_tokens=1 的 chat 请求自动判定。"),
        "note": (
            "免费≠无 key。★ 实测其 /v1/models 里每个模型 pricing 均**非 0**"
            "（例：DeepSeek-V3.1 prompt 0.00000300 / completion 0.00000450）"
            "→ 按本项目 free_model_router 的严格免费口径，这些 id 属**付费价**模型；"
            "sambanova 的「免费」来自 Developer 计划 credits，不是零价。"
            "接入前须由编排方确认是否满足「免费池 fail-closed 只收零价模型」的约束。"),
    },
    {
        "id": "zhipu",
        "provider": "zhipu",
        "name": "智谱 AI 开放平台 (BigModel)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "auth_style": "bearer",
        "auth_header": {"Authorization": "Bearer {key}"},
        "key_config": "llm.free.zhipu_key",
        "models_config": "llm.free.zhipu_models",
        "default_models": ["glm-4.7-flash", "glm-4-flash", "glm-4-air"],
        "models_source": (
            "本项目 .freetoken/providers.json（2026-10-12，免费token管理 skill 的配置）"
            "记 glm-4.7-flash 输入输出均 0 元、永久免费无总量上限；"
            "本项目 gateway/app/providers/registry.py::FREE_PROVIDER_CATALOG（name='zhipu'）"
            "镜像列 glm-4-flash / glm-4-air / glm-4-plus / glm-4.5 / glm-4.7 / glm-5.3-flash。"
            "★ 非一手官方文档抓取、未经带 key 核验 → 以官方文档为准。"),
        "probe_path": "/models",
        "extra_probes": {},
        "required_config": {},
        "signup_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "docs_url": "https://open.bigmodel.cn/dev/api",
        "free_tier_note": "glm-4-flash / glm-4.7-flash 永久免费（有 RPM/并发限制），其余按量计费（额度以官方为准）",
        "env_key": "ZHIPU_API_KEY",
        "auto_verifiable": True,
        "auto_verify_note": (
            "端点匿名 401（服务端中文鉴权 JSON，主机可达）；配 key 后 GET /models 或 "
            "max_tokens=1 的 chat 可自动判定。"),
        "note": (
            "免费≠无 key。2026-10-17 匿名 GET /models = 401，body "
            "{\"error\":{\"code\":\"1001\",\"message\":\"Header中未收到Authorization参数，无法进行身份验证。\"}}"
            " → 服务端自身鉴权应答，主机可达、调用需 key。"),
    },
    {
        "id": "github_models",
        "provider": "github_models",
        "name": "GitHub Models",
        "base_url": "https://models.github.ai/inference",
        "auth_style": "bearer",
        "auth_header": {"Authorization": "Bearer {key}"},
        "key_config": "llm.free.github_key",
        "models_config": "llm.free.github_models",
        "default_models": ["gpt-4o-mini", "OpenAI-gpt-4o", "Codestral-2501"],
        "models_source": (
            "本项目 registry FREE_PROVIDER_CATALOG（name='github_models'）与 "
            "services/free_channel_catalog.py::models_known（2026-10-17 登记）镜像。"
            "★ 端点匿名 /models 只回纯文本 \"OK\"、不返回目录，无法一手核验 → 以官方文档为准。"),
        "probe_path": "/models",
        "extra_probes": {},
        "required_config": {},
        "signup_url": "https://github.com/settings/tokens",
        "docs_url": "https://docs.github.com/en/github-models",
        "free_tier_note": "用 GitHub PAT 调用，免费层按 RPM/请求数限次（档位以官方为准）",
        "env_key": "GITHUB_TOKEN",
        "auto_verifiable": False,
        "auto_verify_note": (
            "匿名探测只能证「主机可达」，证不了「可用」：2026-10-17 匿名 GET /models = 200，"
            "但 body 仅 4 字节 \"OK\"（疑似前置网关应答）。本机无 PAT → 无法验证模型名与真实推理。"),
        "note": (
            "★ 端点已迁移：registry 里记的旧 base_url https://models.inference.ai.azure.com "
            "本轮实测 curl: (6) Could not resolve host（DNS 不存在）；现行端点为 "
            "https://models.github.ai/inference。"),
    },
    {
        "id": "cloudflare_workers_ai",
        "provider": "cloudflare_workers_ai",
        "name": "Cloudflare Workers AI",
        "base_url": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        "auth_style": "bearer",
        "auth_header": {"Authorization": "Bearer {key}"},
        "key_config": "llm.free.cloudflare_key",
        "models_config": "llm.free.cloudflare_models",
        "default_models": [
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            "@cf/qwen/qwen2.5-coder-32b-instruct",
            "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
        ],
        "models_source": (
            "本项目 registry FREE_PROVIDER_CATALOG（name='cloudflare'）与 "
            "services/free_channel_catalog.py::models_known（2026-10-17 登记）镜像。"
            "★ 未经带 key 一手核验 → 以官方文档为准。"),
        "probe_path": "/models",
        "extra_probes": {
            "token_verify": "https://api.cloudflare.com/client/v4/user/tokens/verify",
        },
        "required_config": {"account_id": "llm.free.cloudflare_account_id"},
        "signup_url": "https://dash.cloudflare.com",
        "docs_url": "https://developers.cloudflare.com/workers-ai",
        "free_tier_note": "Workers AI 每日免费 tokens（有日额度上限 + 速率限制；额度以官方为准）",
        "env_key": "CLOUDFLARE_API_TOKEN",
        "auto_verifiable": False,
        "auto_verify_note": (
            "调用需**同时**具备真实 account_id（填进 base_url 的 {account_id}）与 API Token，"
            "缺任一即无法发有效请求。2026-10-17 匿名 GET（account_id 填 example）只回 405，"
            "body 为 Cloudflare 标准错误封套（code 7001，GET not supported）→ 主机可达。"
            "Token 可另用 extra_probes.token_verify 单独校验。"),
        "note": (
            "★ base_url 含 {account_id} 占位，接入时必须替换为真实 account_id；"
            "自检脚本在 account_id 缺失时会明确报「缺少必填配置」而不是假装通过。"),
    },
    {
        "id": "groq",
        "provider": "groq",
        "name": "Groq Cloud",
        "base_url": "https://api.groq.com/openai/v1",
        "auth_style": "bearer",
        "auth_header": {"Authorization": "Bearer {key}"},
        "key_config": "llm.free.groq_key",
        "models_config": "llm.free.groq_models",
        "default_models": ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "gpt-oss-120b"],
        "models_source": (
            "本项目 .freetoken/providers.json（2026-10-12）列 "
            "llama-3.1-8b-instant / llama-3.3-70b-versatile / gpt-oss-120b，默认 llama-3.1-8b-instant；"
            "registry FREE_PROVIDER_CATALOG（name='groq'）镜像列 llama-3.3-70b-versatile / "
            "llama-3.1-8b-instant / mixtral-8x7b-32768 / deepseek-r1-distill-llama-70b（较旧）。"
            "★ 非一手官方抓取 → 以官方文档为准。"),
        "probe_path": "/models",
        "extra_probes": {},
        "required_config": {},
        "signup_url": "https://console.groq.com/keys",
        "docs_url": "https://console.groq.com/docs",
        "free_tier_note": "免费层按模型给 RPM/RPD/TPM 限速（免费层不是无限；档位以官方 rate limits 为准）",
        "env_key": "GROQ_API_KEY",
        "auto_verifiable": True,
        "auto_verify_note": (
            "配 key 后可由自检脚本自动判定；但 2026-10-17 匿名 GET /models = 403"
            "（Cloudflare 边缘，带 CF-RAY），本项目 .freetoken/providers.json 亦记 "
            "\"BLOCKED needs proxy\" → 本机直连可能仍被边缘拒，脚本会如实打印该错误，不伪造成功。"),
        "note": (
            "免费≠无 key。2026-10-17 匿名 403（边缘节点真实应答，带 CF-RAY；"
            "同机同轮 sambanova 200 并存）→ 主机可达、匿名/无 key 被拒；★ 不是「本机网络不通」。"),
    },
    {
        "id": "cerebras",
        "provider": "cerebras",
        "name": "Cerebras Inference",
        "base_url": "https://api.cerebras.ai/v1",
        "auth_style": "bearer",
        "auth_header": {"Authorization": "Bearer {key}"},
        "key_config": "llm.free.cerebras_key",
        "models_config": "llm.free.cerebras_models",
        "default_models": ["llama-3.3-70b", "llama3.1-8b", "qwen-3-32b"],
        "models_source": (
            "本项目 registry FREE_PROVIDER_CATALOG（name='cerebras'）与 "
            "services/free_channel_catalog.py::models_known 镜像。"
            "★ 未经一手核验 → 以官方文档为准。"),
        "probe_path": "/models",
        "extra_probes": {},
        "required_config": {},
        "signup_url": "https://cloud.cerebras.ai",
        "docs_url": "https://docs.cerebras.ai",
        "free_tier_note": "免费层按日给 token 额度 + RPM 限速（额度以官方为准）",
        "env_key": "CEREBRAS_API_KEY",
        "auto_verifiable": True,
        "auto_verify_note": (
            "配 key 后可由自检脚本自动判定；但 2026-10-17 匿名 GET /models = 403，"
            "body 为 \"error code: 1009\"（Cloudflare 边缘拒绝）→ 本机直连可能仍被边缘拒，"
            "脚本会如实打印。"),
        "note": (
            "免费≠无 key。403 + CF-RAY = 边缘真实应答（主机可达、请求被边缘拒），"
            "★ 不是「本机网络不通」。"),
    },
    {
        "id": "cohere",
        "provider": "cohere",
        "name": "Cohere",
        "base_url": "https://api.cohere.com/v2",
        "auth_style": "bearer",
        "auth_header": {"Authorization": "Bearer {key}"},
        "key_config": "llm.free.cohere_key",
        "models_config": "llm.free.cohere_models",
        "default_models": ["command-a-03-2025", "command-r-plus", "command-r"],
        "models_source": (
            "本项目 registry FREE_PROVIDER_CATALOG（name='cohere'）与 "
            "services/free_channel_catalog.py::models_known 镜像。"
            "★ 未经带 key 一手核验 → 以官方文档为准。"),
        "probe_path": "/models",
        "extra_probes": {},
        "required_config": {},
        "signup_url": "https://dashboard.cohere.com/api-keys",
        "docs_url": "https://docs.cohere.com",
        "free_tier_note": "Trial key 免费且按 RPM 限速（档位以官方为准）",
        "env_key": "COHERE_API_KEY",
        "auto_verifiable": True,
        "auto_verify_note": (
            "配 key 后可由自检脚本自动判定；2026-10-17 匿名 "
            "https://api.cohere.com/v2/models = 403（HTML 403 页）→ 主机可达、匿名被拒。"),
        "note": (
            "★ base_url 已由 registry 记的 https://api.cohere.ai/compose/v1 更正为现行 "
            "https://api.cohere.com/v2（registry 的 /compose/ 路径与 Cohere 现行 /v2 不一致，"
            "接入前须以官方文档核对）。"),
    },
    {
        "id": "google_gemini",
        "provider": "google_gemini",
        "name": "Google Gemini API",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "auth_style": "bearer",
        "auth_header": {"Authorization": "Bearer {key}"},
        "auth_alt": {
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "auth_style": "x-goog-api-key",
            "auth_header": {"x-goog-api-key": "{key}"},
            "note": "原生（非 OpenAI 兼容）路径，鉴权头为 x-goog-api-key；本轮采用 OpenAI 兼容层。",
        },
        "key_config": "llm.free.gemini_key",
        "models_config": "llm.free.gemini_models",
        "default_models": ["gemini-3-flash", "gemini-2.5-flash", "gemini-2.0-flash"],
        "models_source": (
            "本项目 .freetoken/providers.json（2026-10-12）列 gemini-3.1-flash-lite / gemini-3-flash / "
            "gemini-2.5-flash / gemini-2.5-flash-lite / gemini-2.0-flash / gemma-4-26b-a4b-it，"
            "默认 gemini-3-flash；registry FREE_PROVIDER_CATALOG（name='gemini'）镜像列 "
            "gemini-2.0-flash / gemini-2.5-pro / gemini-1.5-flash（较旧）。"
            "★ 本机端点连不通、无法一手核验 → 以官方文档为准。"),
        "probe_path": "/models",
        "extra_probes": {},
        "required_config": {},
        "signup_url": "https://aistudio.google.com/app/apikey",
        "docs_url": "https://ai.google.dev/gemini-api/docs",
        "free_tier_note": "有免费层但按 RPM/RPD 限次（Flash 系列够日常用；额度以官方为准）",
        "env_key": "GEMINI_API_KEY",
        "auto_verifiable": False,
        "auto_verify_note": (
            "2026-10-17 两轮实测 https://generativelanguage.googleapis.com/v1beta/openai/models "
            "均连接超时（curl: (28)，6s，无任何 HTTP 响应）→ 本机网络层不可达（国内直连 Google "
            "通常需代理），无法自动化验证。"),
        "note": (
            "免费≠无 key。★ 本机 TCP 连不通（需代理），与 403 类「匿名被拒」不同，"
            "本条才是真·不可达。"),
    },
    {
        "id": "mistral",
        "provider": "mistral",
        "name": "Mistral La Plateforme",
        "base_url": "https://api.mistral.ai/v1",
        "auth_style": "bearer",
        "auth_header": {"Authorization": "Bearer {key}"},
        "key_config": "llm.free.mistral_key",
        "models_config": "llm.free.mistral_models",
        "default_models": ["mistral-small-latest", "mistral-large-latest", "codestral-latest"],
        "models_source": (
            "本项目 registry FREE_PROVIDER_CATALOG（name='mistral'）与 "
            "services/free_channel_catalog.py::models_known 镜像。"
            "★ 本机端点连不通、未经一手核验 → 以官方文档为准。"),
        "probe_path": "/models",
        "extra_probes": {},
        "required_config": {},
        "signup_url": "https://console.mistral.ai",
        "docs_url": "https://docs.mistral.ai",
        "free_tier_note": "La Plateforme 免费试用 credits（按量消耗 + 速率限制；额度以官方为准）",
        "env_key": "MISTRAL_API_KEY",
        "auto_verifiable": False,
        "auto_verify_note": (
            "2026-10-17 两轮实测 https://api.mistral.ai/v1/models 均连接超时"
            "（curl: (28)，6s，无任何 HTTP 响应）→ 本机网络层不可达（需代理），无法自动化验证。"),
        "note": "免费≠无 key。★ 本机 TCP 连不通（需代理）。",
    },
    {
        "id": "huggingface",
        "provider": "huggingface",
        "name": "HuggingFace Inference (Router)",
        "base_url": "https://router.huggingface.co/v1",
        "auth_style": "bearer",
        "auth_header": {"Authorization": "Bearer {key}"},
        "key_config": "llm.free.huggingface_key",
        "models_config": "llm.free.huggingface_models",
        "default_models": [
            "meta-llama/Llama-3.3-70B-Instruct",
            "Qwen/Qwen2.5-72B-Instruct",
            "microsoft/Phi-3.5-mini-instruct",
        ],
        "models_source": (
            "本项目 registry FREE_PROVIDER_CATALOG（name='huggingface'）与 "
            "services/free_channel_catalog.py::models_known 镜像。"
            "★ 本机端点连不通、未经一手核验 → 以官方文档为准。"),
        "probe_path": "/models",
        "extra_probes": {},
        "required_config": {},
        "signup_url": "https://huggingface.co/settings/tokens",
        "docs_url": "https://huggingface.co/docs/inference-endpoints",
        "free_tier_note": "Inference 免费层有速率限制且高峰期排队（额度以官方为准）",
        "env_key": "HF_TOKEN",
        "auto_verifiable": False,
        "auto_verify_note": (
            "2026-10-17 两轮 × 两端点（router.huggingface.co/v1 与 "
            "api-inference.huggingface.co/v1）四个探测全部连接超时（curl: (28)，无任何 HTTP 响应）"
            " → 本机网络层不可达，无法自动化验证。"),
        "note": (
            "★ 端点已迁移：registry 记的 https://api-inference.huggingface.co/v1 为旧端点，"
            "现行推荐 https://router.huggingface.co/v1。与 hf_ssh（chat.hf.co:22，SSH 通道，"
            "priority 99 的 disabled 占位）是两条不同通道。"),
    },
]

#: 接入降级链时的 provider 名集合（= 各渠道的 provider 字段）
PROVIDER_IDS: tuple[str, ...] = tuple(ch["provider"] for ch in FREE_KEY_CHANNELS)

#: id -> 描述符 的只读索引
_INDEX: dict[str, dict[str, Any]] = {ch["id"]: ch for ch in FREE_KEY_CHANNELS}


# ====================================================================== #
# 纯函数工具（无网络、无副作用）
# ====================================================================== #
def _cfg_get(config: Any, dotted: str, default: Any = "") -> Any:
    """从 config 读点分路径的值，**同时**兼容两种 config 形态：

    1. ``ConfigService`` 这类带 ``.get(点分路径)`` 的对象（本项目 LLM 侧就是它：
       ``FreeModelRouter`` 用 ``self.config.get("llm.free.openrouter_key")`` 取 key）；
    2. 普通 dict：
       - 先按**字面量扁平键**取（``{"llm.free.groq_key": "..."}``）；
       - 再按**嵌套路径**取（``{"llm": {"free": {"groq_key": "..."}}}``）。

    取不到一律返回 ``default``（默认空串），绝不抛异常。
    """
    if config is None:
        return default
    if isinstance(config, dict):
        flat = config.get(dotted)
        if flat not in (None, ""):
            return flat
        cur: Any = config
        for part in dotted.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return default if cur in (None, "") else cur
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            value = getter(dotted)
        except Exception:  # noqa: BLE001 - 配置源异常一律当未配置
            return default
        return default if value in (None, "") else value
    return default


def get_channel(channel_id: str) -> Optional[dict[str, Any]]:
    """按 id 取描述符（返回原对象引用，调用方不要就地改）。"""
    return _INDEX.get(str(channel_id or "").strip().lower())


def channel_ids() -> list[str]:
    """全部渠道 id（按描述符顺序）。"""
    return [ch["id"] for ch in FREE_KEY_CHANNELS]


def mask_key(key: str) -> str:
    """掩码显示 API Key：前 4 后 3，附长度；**绝不返回明文**。

    ``sk-abc123...xyz`` -> ``sk-a…xyz (len=N)``；过短的 key 退化为首尾各 1 字符。
    空 key 返回空串。
    """
    s = str(key or "").strip()
    if not s:
        return ""
    n = len(s)
    if n < _MASK_MIN_LEN:
        return f"{s[0]}…{s[-1]} (len={n})"
    return f"{s[:4]}…{s[-3:]} (len={n})"


def key_for(channel: dict[str, Any], config: Any,
            include_env: bool = False) -> str:
    """解析该渠道的 API Key：**先 config，后（可选）环境变量**；都没有返回空串。

    :param include_env: 默认 False —— 只读 config（纯函数、可确定性测试）；
                        显式置 True 时才回退读 ``channel['env_key']`` 指向的环境变量。
    """
    cfg_key = str(channel.get("key_config") or "")
    if cfg_key:
        value = _cfg_get(config, cfg_key, "")
        if value:
            return str(value).strip()
    if include_env:
        env_name = str(channel.get("env_key") or "")
        if env_name:
            value = os.environ.get(env_name, "")
            if value:
                return value.strip()
    return ""


def key_source(channel: dict[str, Any], config: Any,
               include_env: bool = False) -> str:
    """返回 key 的来源：``"config"`` / ``"env"`` / ``""``（未配置）。"""
    cfg_key = str(channel.get("key_config") or "")
    if cfg_key and _cfg_get(config, cfg_key, ""):
        return "config"
    if include_env:
        env_name = str(channel.get("env_key") or "")
        if env_name and os.environ.get(env_name, ""):
            return "env"
    return ""


def models_for(channel: dict[str, Any], config: Any) -> list[str]:
    """候选模型列表：``models_config`` 里填了就用它（逗号分隔），否则用 default_models。"""
    cfg_key = str(channel.get("models_config") or "")
    raw = _cfg_get(config, cfg_key, "") if cfg_key else ""
    if raw:
        if isinstance(raw, (list, tuple)):
            items = [str(x).strip() for x in raw]
        else:
            items = [x.strip() for x in str(raw).split(",")]
        items = [x for x in items if x]
        if items:
            return items
    return [str(m) for m in (channel.get("default_models") or [])]


def missing_config(channel: dict[str, Any], config: Any) -> list[str]:
    """该渠道**必填但未配置**的字段（占位符名列表，如 ``["account_id"]``）。

    只检查 ``required_config``；key 是否配置由 ``key_for`` 单独判断。
    """
    missing: list[str] = []
    required = channel.get("required_config") or {}
    if isinstance(required, dict):
        for placeholder, cfg_key in required.items():
            if not _cfg_get(config, str(cfg_key), ""):
                missing.append(str(placeholder))
    return missing


def base_url_for(channel: dict[str, Any], config: Any) -> str:
    """把 base_url 里的 ``{account_id}`` 等占位符替换为实际配置值。

    缺配置时**保留原占位符**（不静默编造），由调用方用 ``missing_config`` 判断。

    ⚠️ 契约（D3）：本函数**可能返回仍含 ``{占位}`` 的地址**（必填配置缺失时）。
    若要把地址交给「会真的发出去」的代码，请用
    ``channels_with_keys()['base_url_resolved']`` —— 它保证非空时不含 ``{``。
    """
    url = str(channel.get("base_url") or "")
    required = channel.get("required_config") or {}
    if isinstance(required, dict):
        for placeholder, cfg_key in required.items():
            value = _cfg_get(config, str(cfg_key), "")
            if value:
                url = url.replace("{" + str(placeholder) + "}", str(value))
    return url


def channels_with_keys(config: Any, include_env: bool = False) -> list[dict[str, Any]]:
    """返回**已配置 key 的渠道**（读 config，纯函数，**不发任何网络请求**）。

    每个返回项 = 描述符的浅拷贝 + 以下派生字段：

        api_key         : 明文 key（⚠️ 调用方不得打印/落盘；仅供路由接线）
        key_masked      : 掩码串（可安全打印）
        key_source      : "config" / "env"
        base_url_resolved: 占位符已替换的 base_url；**必填配置不齐或仍含 ``{占位}`` 时为空串**
        models          : 实际候选模型列表（models_config 覆盖优先）
        missing_config  : 必填但未配置的字段名列表

    **契约（D3）**：``base_url_resolved`` 为空串 ⇒ 该渠道的地址**不可用**，
    原因看同一行的 ``missing_config``；非空 ⇔ 保证 **不含** ``{``，调用方可直接用它发请求。
    也就是说：本函数**永不把含未替换占位的地址**当作可用地址交出去。

    :param include_env: 默认 False（只认 config）；显式 True 时才把环境变量当兜底。
    """
    out: list[dict[str, Any]] = []
    for channel in FREE_KEY_CHANNELS:
        key = key_for(channel, config, include_env=include_env)
        if not key:
            continue
        row: dict[str, Any] = {
            k: (list(v) if isinstance(v, list) else (dict(v) if isinstance(v, dict) else v))
            for k, v in channel.items()
        }
        row["api_key"] = key
        row["key_masked"] = mask_key(key)
        row["key_source"] = key_source(channel, config, include_env=include_env)
        missing = missing_config(channel, config)
        # D3：必填配置不齐 → 不交地址；地址里若仍残留 ``{占位}`` 也一律置空（双保险）。
        # 调用方（如 FreeModelRouter.candidates()）另有 ``"{" in base`` 兜底守卫，此处是源头拦截。
        resolved = "" if missing else base_url_for(channel, config)
        if "{" in resolved:
            resolved = ""
        row["base_url_resolved"] = resolved
        row["models"] = models_for(channel, config)
        row["missing_config"] = missing
        out.append(row)
    return out


def summary(config: Any = None, include_env: bool = False) -> list[dict[str, Any]]:
    """只读概览（**掩码、无明文 key**）：供自检 / 诊断页 / 日志安全打印。"""
    rows: list[dict[str, Any]] = []
    for channel in FREE_KEY_CHANNELS:
        key = key_for(channel, config, include_env=include_env)
        rows.append({
            "id": channel["id"],
            "provider": channel["provider"],
            "base_url": base_url_for(channel, config),
            "key_config": channel["key_config"],
            "configured": bool(key),
            "key_masked": mask_key(key),
            "key_source": key_source(channel, config, include_env=include_env),
            "missing_config": missing_config(channel, config),
            "models": models_for(channel, config),
            "auto_verifiable": bool(channel.get("auto_verifiable")),
        })
    return rows


def as_dict() -> dict[str, Any]:
    """整包导出（纯数据，JSON 可序列化；**不含任何 key**）。"""
    return {
        "version": CHANNELS_VERSION,
        "provider_ids": list(PROVIDER_IDS),
        "channels": FREE_KEY_CHANNELS,
        "notes": [
            "本模块是纯数据描述符；R3.8（2026-10-17）起已并入 FreeModelRouter.candidates() "
            "的第 6 条来源（services/free_model_router.py），参与降级链候选构建。",
            "但受 fail-closed 价格门约束：**光填 key 仍不路由**（0 候选、0 出站）；"
            "须把 id 登记进 llm.free.trusted_models（例 groq:*）才会进池。",
            "接线路径见 docs/免费路由_需Key渠道接线路径-20261017.md。",
            "channels_with_keys() 返回的 dict 含明文 api_key，仅供路由接线与自检，调用方不得打印/落盘。",
        ],
    }


if __name__ == "__main__":  # 手动跑一下：只打印掩码概览，绝不含明文 key
    import json
    print(json.dumps({"version": CHANNELS_VERSION,
                      "channels": summary({})},
                     ensure_ascii=False, indent=2))
