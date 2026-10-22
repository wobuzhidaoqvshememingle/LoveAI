"""
LoveAI v2 - R3.3-43 免费 AI 通道全景清单（结构化导出）
=====================================================

把「免费/零成本」通道整理成 dict，供 UI/设置页 / 诊断页读取。

- 原始 8 条（R3.3-43，2026-10-10）：最初实测数据来自 2026-10-10 本机 PowerShell 探测，
  详见 docs/免费AI通道全景清单-20261010.md。
- 2026-10-17 补登 10 条：按 freellmapi（https://github.com/tashfeenahmed/freellmapi）
  收录的免费服务商补齐，元数据镜像自本项目 gateway/app/providers/registry.py 的
  FREE_PROVIDER_CATALOG。
- 2026-10-17 r2 修订：reachable 口径改为「是否收到服务端/边缘的 HTTP 响应」，
  并对全部 18 条重新实测/复核。详见 docs/免费AI通道全景清单-20261017.md（含原始命令与输出）。

字段约定
--------
    id                  : 通道唯一 id（全小写 + 下划线，与路由层 provider 字段命名一致）
    name                : 人类可读名
    need_login          : 是否需要登录浏览器/账号
    need_key            : 是否需要凭据（API Key / SSH 凭据等）（True=免费≠无 key）
    zero_config         : 是否真零配置（开箱即用）
    reachable           : 本机实测可达性 (True/False/"unknown")
                          【口径：2026-10-17 r2 修订，取代 R3.3-43 原口径】
                          True      = 收到了服务端/边缘的 HTTP 响应（任何状态码，
                                      含 200/401/403/405/429），即主机可达。
                                      非 HTTP 通道（如 SSH）按同口径以「服务端是否应答」判定。
                                      ★ 匿名被拒（401/403）≠ 本机网络不通，它是主机可达的证据。
                                      该通道是否真正可用，另看 need_key / need_login / note。
                          False     = 没有收到任何 HTTP 响应：
                                      连接超时 / DNS 解析失败 / 连接被拒 / TLS 握手失败。
                                      只有这一类才能表述为「本机网络不通 / 不可达」。
                          "unknown" = 需要 key 或登录态、端点不公开，匿名请求无法触达判定。
    latency_ms          : 实测往返毫秒（未测到填 None）；取值见各条 probe / note 注明的日期
    priority            : 降级链优先级，数字越小越靠上（越先尝试）
    code_path           : 代码落点；未接入的写「未接入（仅登记）」+ 元数据出处（不编造函数名）
    note                : 备注（免费≠无 key 标注、限速、额度、实测结论、接入状态）

2026-10-17 新增条目额外字段（原有 8 条不加，保持原样）
--------
    base_url            : 该通道端点（便于复现实测）
    signup_url          : 注册 / 领取 key 入口
    docs_url            : 官方文档入口
    free_tier_note      : 免费额度形态（含速率限制等级；额度会漂移，以官方为准）
    probe               : 最近一次实测的原始命令要点 + HTTP 状态 + 延迟 + 响应体片段
"""
from __future__ import annotations

from typing import Any


# 18 条通道，按降级优先级排序（数字小 = 先试）
FREE_CHANNEL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "ollama_local",
        "name": "本机 Ollama (loveai-qwen05b / loveai-qwen15b)",
        "need_login": False,
        "need_key": False,
        "zero_config": True,
        "reachable": False,
        "latency_ms": None,
        "priority": 1,
        "code_path": "services/llm_gateway.py::_call_l2_ollama",
        "models_known": ["loveai-qwen05b:latest", "loveai-qwen15b:latest"],
        "note": ("真·零配置零成本。必须先 `ollama serve`。"
                 "2026-10-17 复测：curl: (7) Failed to connect to 127.0.0.1 port 11434 "
                 "after 2051 ms: Could not connect to server —— 即「连接被拒」（本地端口无监听，"
                 "进程未起），不是超时、也不是本机网络不通；`ollama serve` 起来后本项应立即变 True。"
                 "模型名自动从 /api/tags 发现。"),
    },
    {
        "id": "pollinations",
        "name": "Pollinations.ai (openai-fast / llama / mistral)",
        "need_login": False,
        "need_key": False,
        "zero_config": True,
        "reachable": True,    # 2026-10-13 chat 200 真出文本, 886ms
        "latency_ms": 886,
        "priority": 1,
        "code_path": "services/free_model_router.py::_ZERO_CONFIG_API",
        "models_known": ["openai-fast", "openai", "llama", "mistral"],
        "note": ("完全免费无需 Key；OpenAI 兼容端点 text.pollinations.ai/openai。"
                 "latency_ms=886 为 2026-10-13 实测（chat 真出文本）；"
                 "2026-10-17 03:28 复测 GET https://text.pollinations.ai/models = 200（0.69~0.75s）仍可达。"),
    },
    {
        "id": "llm7",
        "name": "LLM7.io (DeepSeek-V4-Flash / gpt-4o-mini / claude-3.5-sonnet)",
        "need_login": False,
        "need_key": False,
        "zero_config": True,
        "reachable": True,    # 2026-10-13 chat 200 真出文本, 2292ms
        "latency_ms": 2292,
        "priority": 1,
        "code_path": "services/free_model_router.py::_ZERO_CONFIG_API",
        "models_known": ["DeepSeek-V4-Flash-0731", "gpt-4o-mini",
                         "claude-3-5-sonnet", "llama-3.3-70b"],
        "note": ("50+ 模型基础速率免注册；OpenAI 兼容端点 api.llm7.io/v1。"
                 "latency_ms=2292 为 2026-10-13 实测（chat 真出文本）；"
                 "2026-10-17 03:28 复测 GET https://api.llm7.io/v1/models = 200（0.92~0.93s，62945 B）仍可达。"),
    },
    {
        "id": "openrouter_free",
        "name": "OpenRouter :free 模型目录",
        "need_login": False,
        "need_key": True,     # 调推理要 key，但拉目录不要
        "zero_config": False,
        "reachable": True,    # 2026-10-10 HEAD /api/v1/models = 200, 726ms
        "latency_ms": 726,
        "priority": 2,
        "code_path": "services/free_model_router.py::_fetch_openrouter_free",
        "note": ("免费≠无 key：目录公开可拉（:free 后缀 + pricing 全 0），"
                 "但 chat 调用需 openrouter api_key，且每小时有速率限制。"
                 "2026-10-17 03:28 复测 GET https://openrouter.ai/api/v1/models = 200（1.27~1.33s，781088 B）仍可达。"),
    },
    {
        "id": "nvidia_nim",
        "name": "NVIDIA NIM 免费端点 (integrate.api.nvidia.com)",
        "need_login": False,
        "need_key": True,
        "zero_config": False,
        "reachable": True,    # 2026-10-10 GET = 200, 141ms
        "latency_ms": 141,
        "priority": 3,
        "code_path": "services/free_model_router.py::_fetch_nvidia",
        "note": ("免费≠无 key：build.nvidia.com 注册拿 key；"
                 "未配 key 时用 build.nvidia.com/models.md 公开目录 best-effort 解析。"
                 "2026-10-17 03:28 复测 GET https://integrate.api.nvidia.com/v1/models "
                 "= 200（0.36~0.37s，7751 B，返回真实模型目录）仍可达。"),
    },
    {
        "id": "browser_ai_hub",
        "name": "浏览器 AI 中枢 14 源（博查/密塔/天工/Kimi/元宝/通义/智谱/"
                "DeepSeek/阶跃/文心/商汤/讯飞/豆包/火山）",
        "need_login": True,   # 借浏览器登录态
        "need_key": False,
        "zero_config": False,
        "reachable": "unknown",   # 14 个源各自站点、依赖登录态，无公开可匿名探测端点
        "latency_ms": None,
        "priority": 4,
        "code_path": "services/browser_ai_hub.py",
        "note": ("借用户在浏览器里已登录的账号 cookie，免再申请 key；"
                 "风控命中自动降级 blocked；多 profile 轮询。"
                 "reachable=unknown：需浏览器登录态且无公开匿名端点，本机无法匿名触达判定（未实测）。"),
    },
    {
        "id": "desktop_ai_connector",
        "name": "Desktop AI Connector (Trae / WorkBuddy / Qoder / CodeBuddy 凭据借取)",
        "need_login": True,
        "need_key": False,
        "zero_config": False,
        "reachable": "unknown",   # 读本机 IDE 本地凭据，无独立网络端点点可探
        "latency_ms": None,
        "priority": 5,
        "code_path": "services/desktop_ai_connector.py",
        "note": ("从本机已装 IDE 的本地配置里挖 API 凭据，复用用户已有订阅。"
                 "reachable=unknown：属本机凭据借取、无独立公开端点，匿名探测无从判定（未实测）。"),
    },
    {
        "id": "hf_ssh",
        "name": "HuggingFace SSH 聊天端点 (chat.hf.co:22)",
        "need_login": False,
        "need_key": True,     # 2026-10-17 复核：SSHD 要求 publickey/keyboard-interactive 凭据
        "zero_config": False,
        "reachable": True,    # 2026-10-17 复核：SSHD 应答（匿名被拒=需凭据），非网络不通
        "latency_ms": 2028,   # 2026-10-17 03:33 实测 SSH 握手+认证尝试往返（换掉 2026-10-10 的 None）
        "priority": 99,      # 本项目实现仍是 disabled 占位，不参与自动轮询
        "code_path": "services/hf_ssh_channel.py",
        "note": ("【2026-10-17 复核，推翻原结论】原文写「chat.hf.co / benchmark-hf.co 均无公开 SSH "
                 "端点」，实测是错的：`ssh chat.hf.co` -> DNS 解析到 184.194.189.206、22 端口 "
                 "TcpTestSucceeded=True、SSHD 应答 `Permission denied (publickey,keyboard-interactive)` "
                 "—— 端点存在、服务端有应答、要求在公钥/交互式凭据（免费≠免凭据；匿名不可用），"
                 "故 reachable 由 False 改 True、need_key 由 False 改 True（非 HTTP 通道按同口径"
                 "以「服务端是否应答」判定）。对比：benchmark-hf.co -> `Could not resolve hostname`"
                 "（DNS 不存在，才是真不可达）。本项目 services/hf_ssh_channel.py 仍为 disabled 占位"
                 "（chat() 直接 raise），故 priority 保持 99，不参与自动轮询；"
                 "用户配好跳板/凭据后可启用。"),
    },

    # ======================================================================= #
    # 2026-10-17 补登：freellmapi 收录的免费服务商（元数据镜像自本项目
    # gateway/app/providers/registry.py::FREE_PROVIDER_CATALOG）
    # 全部 need_key=True / need_login=False / zero_config=False（免费≠无 key）。
    # 本批 priority 排序规则：先按实测可达性分档（True → unknown → False），
    # 档内按本轮任务给定的规范 id 顺序；本表 priority 仅作登记排序元数据
    # （运行期优先级以路由层配置为准；R3.8 起这 10 条已并入降级链）。
    # ======================================================================= #
    {
        "id": "sambanova",
        "name": "SambaNova Cloud (api.sambanova.ai/v1)",
        "need_login": False,
        "need_key": True,
        "zero_config": False,
        "reachable": True,    # 2026-10-17 03:28 GET /v1/models = 200
        "latency_ms": 725,
        "priority": 6,
        "code_path": "已登记描述符+自检（已并入降级链 R3.8）：services/free_key_channels.py::FREE_KEY_CHANNELS / channels_with_keys()；自动探测 scripts/free_channel_keycheck.py；接线路径与已应用 hook 见 docs/免费路由_需Key渠道接线路径-20261017.md；元数据镜像自 gateway/app/providers/registry.py::FREE_PROVIDER_CATALOG（name='sambanova'）",
        "base_url": "https://api.sambanova.ai/v1",
        "signup_url": "https://cloud.sambanova.ai",
        "docs_url": "https://docs.sambanova.ai",
        "free_tier_note": "Developer 计划免费 credits，按模型给速率限制；不是无限（额度以官方为准）",
        "models_known": ["DeepSeek-V3.1", "DeepSeek-V3.2", "Meta-Llama-3.3-70B-Instruct",
                         "MiniMax-M3", "gemma-4-31B-it", "gpt-oss-120b"],
        "probe": ("2026-10-17 03:28 两轮实测 GET https://api.sambanova.ai/v1/models "
                  "-> http=200 time=0.724584 / 0.741860 size=1340 ip=18.144.149.102；"
                  "body 为真实模型目录（DeepSeek-V3.1 等）-> 可达，且目录匿名可取"),
        "note": ("免费≠无 key：cloud.sambanova.ai 注册，Developer 计划给免费 credits，"
                 "有速率限制，不是无限。实测 HTTP 200（服务端目录应答）→ 主机可达；"
                 "但 chat 推理仍需 key。已登记渠道描述符与自检脚本（services/free_key_channels.py / scripts/free_channel_keycheck.py），且已于 R3.8 并入 free_model_router.candidates()；但受 fail-closed 价格门约束，光填 key 仍不会自动路由——还须把该渠道 id 登记进 llm.free.trusted_models（例 groq:*）才会进池（详见 docs/免费路由_需Key渠道接线路径-20261017.md）。"),
    },
    {
        "id": "zhipu",
        "name": "智谱 AI 开放平台 (open.bigmodel.cn/api/paas/v4)",
        "need_login": False,
        "need_key": True,
        "zero_config": False,
        "reachable": True,    # 2026-10-17 03:28 GET /models = 401（服务端鉴权应答）
        "latency_ms": 127,
        "priority": 7,
        "code_path": "已登记描述符+自检（已并入降级链 R3.8）：services/free_key_channels.py::FREE_KEY_CHANNELS / channels_with_keys()；自动探测 scripts/free_channel_keycheck.py；接线路径与已应用 hook 见 docs/免费路由_需Key渠道接线路径-20261017.md；元数据镜像自 gateway/app/providers/registry.py::FREE_PROVIDER_CATALOG（name='zhipu'）",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "signup_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "docs_url": "https://open.bigmodel.cn/dev/api",
        "free_tier_note": "glm-4-flash 永久免费（有 RPM/并发限制），其余按量计费；不是无限（额度以官方为准）",
        "models_known": ["glm-4-flash", "glm-4-air", "glm-4-plus", "glm-4.5", "glm-4.7",
                         "glm-5.3-flash"],
        "probe": ("2026-10-17 03:28 两轮实测 GET https://open.bigmodel.cn/api/paas/v4/models "
                  "-> http=401 time=0.127294 / 0.162478 size=105 ip=39.108.52.113；"
                  "body {\"error\":{\"code\":\"1001\",\"message\":\"Header中未收到Authorization参数，"
                  "无法进行身份验证。\"}} -> 服务端自身鉴权应答，主机可达，调用需 key"),
        "note": ("免费≠无 key：open.bigmodel.cn 注册领 key，glm-4-flash 永久免费但有限速，"
                 "不是无限。实测 HTTP 401（服务端鉴权 JSON，而不是网络不通）→ 主机可达；调用需 key。"
                 "已登记渠道描述符与自检脚本（services/free_key_channels.py / scripts/free_channel_keycheck.py），且已于 R3.8 并入 free_model_router.candidates()；但受 fail-closed 价格门约束，光填 key 仍不会自动路由——还须把该渠道 id 登记进 llm.free.trusted_models（例 groq:*）才会进池（详见 docs/免费路由_需Key渠道接线路径-20261017.md）。"),
    },
    {
        "id": "github_models",
        "name": "GitHub Models (models.github.ai/inference)",
        "need_login": False,
        "need_key": True,
        "zero_config": False,
        "reachable": True,    # 2026-10-17 03:28 GET /inference/models = 200（主机可达）
        "latency_ms": 970,
        "priority": 8,
        "code_path": "已登记描述符+自检（已并入降级链 R3.8）：services/free_key_channels.py::FREE_KEY_CHANNELS / channels_with_keys()；自动探测 scripts/free_channel_keycheck.py；接线路径与已应用 hook 见 docs/免费路由_需Key渠道接线路径-20261017.md；元数据镜像自 gateway/app/providers/registry.py::FREE_PROVIDER_CATALOG（name='github_models'）",
        "base_url": "https://models.github.ai/inference",
        "signup_url": "https://github.com/settings/tokens",
        "docs_url": "https://docs.github.com/en/github-models",
        "free_tier_note": "用 GitHub PAT 调用，免费层按 RPM/请求数限次，不是无限（档位以官方为准）",
        "models_known": ["gpt-4o-mini", "OpenAI-gpt-4o", "Codestral-2501", "Llama-3.3-70B-Instruct"],
        "probe": ("2026-10-17 03:28 两轮实测 GET https://models.github.ai/inference/models "
                  "-> http=200 time=0.970328 / 0.954037 size=4 ip=140.82.114.22，body \"OK\""
                  "（非模型目录，疑前置网关应答）；同轮两轮实测注册表内旧端点 "
                  "https://models.inference.ai.azure.com/models -> curl: (6) Could not resolve host"
                  "（DNS 不存在）-> 现行端点主机可达；旧端点已下线"),
        "note": ("免费≠无 key：GitHub PAT（github.com/settings/tokens）即可调用，免费有限次，"
                 "不是无限。实测现行端点 HTTP 200（应答来自 models.github.ai，140.82.114.22）"
                 "→ 主机可达；但 200 的 body 只有 \"OK\"、不是模型目录，且无 PAT 无法验证真实推理，"
                 "故本条只证明「可达」，不代表「可用」。注册表登记的旧端点 "
                 "models.inference.ai.azure.com 已 DNS 不存在（端点已迁移）。已登记渠道描述符与自检脚本（services/free_key_channels.py / scripts/free_channel_keycheck.py），且已于 R3.8 并入 free_model_router.candidates()；但受 fail-closed 价格门约束，光填 key 仍不会自动路由——还须把该渠道 id 登记进 llm.free.trusted_models（例 groq:*）才会进池（详见 docs/免费路由_需Key渠道接线路径-20261017.md）。"),
    },
    {
        "id": "cloudflare_workers_ai",
        "name": "Cloudflare Workers AI (api.cloudflare.com .../{account_id}/ai/v1)",
        "need_login": False,
        "need_key": True,
        "zero_config": False,
        "reachable": True,    # 2026-10-17 03:28 GET .../accounts/example/ai/v1/models = 405（服务端应答）
        "latency_ms": 727,
        "priority": 9,
        "code_path": "已登记描述符+自检（已并入降级链 R3.8）：services/free_key_channels.py::FREE_KEY_CHANNELS / channels_with_keys()；自动探测 scripts/free_channel_keycheck.py；接线路径与已应用 hook 见 docs/免费路由_需Key渠道接线路径-20261017.md；元数据镜像自 gateway/app/providers/registry.py::FREE_PROVIDER_CATALOG（name='cloudflare'）",
        "base_url": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        "signup_url": "https://dash.cloudflare.com",
        "docs_url": "https://developers.cloudflare.com/workers-ai",
        "free_tier_note": "Workers AI 每日免费 tokens（有日额度上限 + 速率限制），不是无限（额度以官方为准）",
        "models_known": ["@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                         "@cf/qwen/qwen2.5-coder-32b-instruct",
                         "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b"],
        "probe": ("2026-10-17 03:28 两轮实测 GET "
                  "https://api.cloudflare.com/client/v4/accounts/example/ai/v1/models "
                  "-> http=405 time=0.727072 / 0.818552 size=119 ip=104.19.193.29；"
                  "body {\"result\":null,\"success\":false,\"errors\":[{\"code\":7001,"
                  "\"message\":\"GET not supported for requested URI.\"}],\"messages\":[]} "
                  "-> Cloudflare 服务端标准错误封套，主机可达、API 在线"),
        "note": ("免费≠无 key：需 Cloudflare 账号 + 真实 account_id + API Token；"
                 "免费额度按日给 tokens，不是无限。实测 HTTP 405（服务端 JSON 封套 code 7001："
                 "该 URI 不支持 GET，真实调用需带 account_id/Token 走 POST）→ 主机可达；"
                 "本次无凭据、无法用真实 account_id 验证推理。已登记渠道描述符与自检脚本（services/free_key_channels.py / scripts/free_channel_keycheck.py），且已于 R3.8 并入 free_model_router.candidates()；但受 fail-closed 价格门约束，光填 key 仍不会自动路由——还须把该渠道 id 登记进 llm.free.trusted_models（例 groq:*）才会进池（详见 docs/免费路由_需Key渠道接线路径-20261017.md）。"),
    },
    {
        "id": "groq",
        "name": "Groq Cloud (api.groq.com/openai/v1)",
        "need_login": False,
        "need_key": True,
        "zero_config": False,
        "reachable": True,    # 2026-10-17 03:28 GET = 403（Cloudflare 边缘真实应答）
        "latency_ms": 704,
        "priority": 10,
        "code_path": "已登记描述符+自检（已并入降级链 R3.8）：services/free_key_channels.py::FREE_KEY_CHANNELS / channels_with_keys()；自动探测 scripts/free_channel_keycheck.py；接线路径与已应用 hook 见 docs/免费路由_需Key渠道接线路径-20261017.md；元数据镜像自 gateway/app/providers/registry.py::FREE_PROVIDER_CATALOG（name='groq'）",
        "base_url": "https://api.groq.com/openai/v1",
        "signup_url": "https://console.groq.com/keys",
        "docs_url": "https://console.groq.com/docs",
        "free_tier_note": "有免费层，按模型给 RPM/RPD/TPM 限速（免费层不是无限），档位以官方 rate limits 为准",
        "models_known": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                         "mixtral-8x7b-32768", "deepseek-r1-distill-llama-70b"],
        "probe": ("2026-10-17 03:28 两轮实测 GET https://api.groq.com/openai/v1/models "
                  "-> http=403 time=0.703753 / 0.850593 size=33 ip=172.64.149.20；"
                  "body {\"error\":{\"message\":\"Forbidden\"}}；响应头 HTTP/1.1 403 / "
                  "Server: cloudflare / CF-RAY: a4b977806f863393-LAX -> Cloudflare 边缘节点真实应答，"
                  "主机可达，匿名无 key 被拒"),
        "note": ("免费≠无 key：console.groq.com/keys 领 key，免费层按模型限速，不是无限。"
                 "实测 HTTP 403（边缘节点应答，带 CF-RAY，且同机同轮 sambanova 200 并存）"
                 "→ 主机可达，只是匿名/无 key 被拒；★ 不是「本机网络不通」。已登记渠道描述符与自检脚本（services/free_key_channels.py / scripts/free_channel_keycheck.py），且已于 R3.8 并入 free_model_router.candidates()；但受 fail-closed 价格门约束，光填 key 仍不会自动路由——还须把该渠道 id 登记进 llm.free.trusted_models（例 groq:*）才会进池（详见 docs/免费路由_需Key渠道接线路径-20261017.md）。"),
    },
    {
        "id": "cerebras",
        "name": "Cerebras Inference (api.cerebras.ai/v1)",
        "need_login": False,
        "need_key": True,
        "zero_config": False,
        "reachable": True,    # 2026-10-17 03:28 GET = 403（Cloudflare 边缘应答）
        "latency_ms": 734,
        "priority": 11,
        "code_path": "已登记描述符+自检（已并入降级链 R3.8）：services/free_key_channels.py::FREE_KEY_CHANNELS / channels_with_keys()；自动探测 scripts/free_channel_keycheck.py；接线路径与已应用 hook 见 docs/免费路由_需Key渠道接线路径-20261017.md；元数据镜像自 gateway/app/providers/registry.py::FREE_PROVIDER_CATALOG（name='cerebras'）",
        "base_url": "https://api.cerebras.ai/v1",
        "signup_url": "https://cloud.cerebras.ai",
        "docs_url": "https://docs.cerebras.ai",
        "free_tier_note": "免费层按日给 token 额度 + RPM 限速，不是无限（额度以官方为准）",
        "models_known": ["llama-3.3-70b", "llama3.1-8b", "qwen-3-32b"],
        "probe": ("2026-10-17 03:28 两轮实测 GET https://api.cerebras.ai/v1/models "
                  "-> http=403 time=0.734344 / 0.635717 size=17 ip=104.18.10.146 / 104.18.11.146；"
                  "body \"error code: 1009\"（Cloudflare 拒绝）；响应头 HTTP/1.1 403 / "
                  "Server: cloudflare / CF-RAY: a4b9778f9e46765e-SEA -> 边缘节点真实应答，主机可达"),
        "note": ("免费≠无 key：cloud.cerebras.ai 注册领 key，免费有日额度，不是无限。"
                 "实测 HTTP 403 + body「error code: 1009」（Cloudflare 地区/匿名策略在边缘拒绝，"
                 "带 CF-RAY 的边缘应答）→ 主机可达、请求被边缘拒；★ 不是「本机网络不通」。"
                 "已登记渠道描述符与自检脚本（services/free_key_channels.py / scripts/free_channel_keycheck.py），且已于 R3.8 并入 free_model_router.candidates()；但受 fail-closed 价格门约束，光填 key 仍不会自动路由——还须把该渠道 id 登记进 llm.free.trusted_models（例 groq:*）才会进池（详见 docs/免费路由_需Key渠道接线路径-20261017.md）。"),
    },
    {
        "id": "cohere",
        "name": "Cohere (api.cohere.ai / api.cohere.com)",
        "need_login": False,
        "need_key": True,
        "zero_config": False,
        "reachable": True,    # 2026-10-17 03:28 两处端点均 403（服务端 HTTP 应答）
        "latency_ms": 567,
        "priority": 12,
        "code_path": "已登记描述符+自检（已并入降级链 R3.8）：services/free_key_channels.py::FREE_KEY_CHANNELS / channels_with_keys()；自动探测 scripts/free_channel_keycheck.py；接线路径与已应用 hook 见 docs/免费路由_需Key渠道接线路径-20261017.md；元数据镜像自 gateway/app/providers/registry.py::FREE_PROVIDER_CATALOG（name='cohere'）",
        "base_url": "https://api.cohere.ai/compose/v1",
        "signup_url": "https://dashboard.cohere.com/api-keys",
        "docs_url": "https://docs.cohere.com",
        "free_tier_note": "Trial key 免费且按 RPM 限速，不是无限（档位以官方为准）",
        "models_known": ["command-r-plus", "command-r", "command-a-03-2025"],
        "probe": ("2026-10-17 03:28 两轮×两端点实测：https://api.cohere.ai/compose/v1/models "
                  "-> http=403 time=0.567315 / 0.517271；https://api.cohere.com/v2/models "
                  "-> http=403 time=0.595605 / 0.507026；四次均 size=134 ip=34.96.76.122，"
                  "body <!doctype html>…<title>403</title>403 Forbidden -> 服务端 HTTP 应答，主机可达"),
        "note": ("免费≠无 key：dashboard.cohere.com/api-keys 领 Trial key，免费层限速，不是无限。"
                 "实测 HTTP 403（服务端返回 403 页面）→ 主机可达、匿名/无 key 被拒；"
                 "★ 不是「本机网络不通」。另注：注册表 base_url 的 /compose/ 路径与 Cohere 现行 /v2 "
                 "不一致，接入前需核对。已登记渠道描述符与自检脚本（services/free_key_channels.py / scripts/free_channel_keycheck.py），且已于 R3.8 并入 free_model_router.candidates()；但受 fail-closed 价格门约束，光填 key 仍不会自动路由——还须把该渠道 id 登记进 llm.free.trusted_models（例 groq:*）才会进池（详见 docs/免费路由_需Key渠道接线路径-20261017.md）。"),
    },
    {
        "id": "google_gemini",
        "name": "Google Gemini API (generativelanguage.googleapis.com)",
        "need_login": False,
        "need_key": True,
        "zero_config": False,
        "reachable": False,   # 2026-10-17 03:28 两轮均 8s 连接超时（无任何 HTTP 响应）
        "latency_ms": None,
        "priority": 13,
        "code_path": "已登记描述符+自检（已并入降级链 R3.8）：services/free_key_channels.py::FREE_KEY_CHANNELS / channels_with_keys()；自动探测 scripts/free_channel_keycheck.py；接线路径与已应用 hook 见 docs/免费路由_需Key渠道接线路径-20261017.md；元数据镜像自 gateway/app/providers/registry.py::FREE_PROVIDER_CATALOG（name='gemini'）",
        "base_url": "https://generativelanguage.googleapis.com",
        "signup_url": "https://aistudio.google.com/app/apikey",
        "docs_url": "https://ai.google.dev/gemini-api/docs",
        "free_tier_note": "有免费层但按 RPM/RPD 限次（Flash 系列够日常用），不是无限（额度以官方为准）",
        "models_known": ["gemini-2.0-flash", "gemini-2.5-pro", "gemini-1.5-flash"],
        "probe": ("2026-10-17 03:28 两轮实测 GET https://generativelanguage.googleapis.com/v1beta/models "
                  "-> 两轮均 curl: (28) Connection timed out after 8011 / 8002 milliseconds，"
                  "exit=28 http=000 size=0（无任何 HTTP 响应）；DNS 可解析（172.217.113.4 等）但 TCP 连不通 "
                  "-> 本机网络层不可达"),
        "note": ("免费≠无 key：aistudio.google.com 领 key 后走免费层，不是真零配置。"
                 "实测两轮均连接超时、拿不到任何 HTTP 响应（DNS 正常、TCP 不通）→ 本机网络层不可达"
                 "（国内网络访问 Google 通常需代理）。★ 与 403 类「匿名被拒」不同，本条才是真·不可达。"
                 "已登记渠道描述符与自检脚本（services/free_key_channels.py / scripts/free_channel_keycheck.py），且已于 R3.8 并入 free_model_router.candidates()；但受 fail-closed 价格门约束，光填 key 仍不会自动路由——还须把该渠道 id 登记进 llm.free.trusted_models（例 groq:*）才会进池（详见 docs/免费路由_需Key渠道接线路径-20261017.md）。"),
    },
    {
        "id": "mistral",
        "name": "Mistral La Plateforme (api.mistral.ai/v1)",
        "need_login": False,
        "need_key": True,
        "zero_config": False,
        "reachable": False,   # 2026-10-17 03:28 两轮均 8s 连接超时（无任何 HTTP 响应）
        "latency_ms": None,
        "priority": 14,
        "code_path": "已登记描述符+自检（已并入降级链 R3.8）：services/free_key_channels.py::FREE_KEY_CHANNELS / channels_with_keys()；自动探测 scripts/free_channel_keycheck.py；接线路径与已应用 hook 见 docs/免费路由_需Key渠道接线路径-20261017.md；元数据镜像自 gateway/app/providers/registry.py::FREE_PROVIDER_CATALOG（name='mistral'）",
        "base_url": "https://api.mistral.ai/v1",
        "signup_url": "https://console.mistral.ai",
        "docs_url": "https://docs.mistral.ai",
        "free_tier_note": "La Plateforme 免费试用 credits（按量消耗 + 速率限制），不是无限（额度以官方为准）",
        "models_known": ["mistral-small-latest", "mistral-large-latest", "codestral-latest"],
        "probe": ("2026-10-17 03:28 两轮实测 GET https://api.mistral.ai/v1/models "
                  "-> 两轮均 curl: (28) Connection timed out after 8013 / 8010 milliseconds，"
                  "exit=28 http=000 size=0（无任何 HTTP 响应）；DNS 可解析（108.160.170.44）但 TCP 连不通"),
        "note": ("免费≠无 key：console.mistral.ai 注册领 key，免费试用额度按量扣减，不是无限。"
                 "实测两轮均连接超时、拿不到任何 HTTP 响应 → 本机网络层不可达。已登记渠道描述符与自检脚本（services/free_key_channels.py / scripts/free_channel_keycheck.py），且已于 R3.8 并入 free_model_router.candidates()；但受 fail-closed 价格门约束，光填 key 仍不会自动路由——还须把该渠道 id 登记进 llm.free.trusted_models（例 groq:*）才会进池（详见 docs/免费路由_需Key渠道接线路径-20261017.md）。"),
    },
    {
        "id": "huggingface",
        "name": "HuggingFace Inference API / Router (api-inference / router.huggingface.co)",
        "need_login": False,
        "need_key": True,
        "zero_config": False,
        "reachable": False,   # 2026-10-17 03:28 四个探测均 8s 连接超时（无任何 HTTP 响应）
        "latency_ms": None,
        "priority": 15,
        "code_path": "已登记描述符+自检（已并入降级链 R3.8）：services/free_key_channels.py::FREE_KEY_CHANNELS / channels_with_keys()；自动探测 scripts/free_channel_keycheck.py；接线路径与已应用 hook 见 docs/免费路由_需Key渠道接线路径-20261017.md；元数据镜像自 gateway/app/providers/registry.py::FREE_PROVIDER_CATALOG（name='huggingface'）",
        "base_url": "https://api-inference.huggingface.co/v1",
        "signup_url": "https://huggingface.co/settings/tokens",
        "docs_url": "https://huggingface.co/docs/inference-endpoints",
        "free_tier_note": "Inference API 免费层有速率限制且高峰期排队，不是无限（额度以官方为准）",
        "models_known": ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct",
                         "microsoft/Phi-3.5-mini-instruct"],
        "probe": ("2026-10-17 03:28 两轮×两端点实测：https://router.huggingface.co/v1/models "
                  "-> 两轮均 curl: (28) Connection timed out after 8012 / 8005 ms；"
                  "https://api-inference.huggingface.co/v1/models -> 两轮均 curl: (28) "
                  "Connection timed out after 8013 / 8005 ms；四个探测 http=000 size=0（无任何 HTTP 响应）"),
        "note": ("免费≠无 key：huggingface.co/settings/tokens 领 token，免费层限速，不是无限。"
                 "实测四个探测全部连接超时、拿不到任何 HTTP 响应 → 本机网络层不可达。"
                 "注：与 hf_ssh（SSH 通道 chat.hf.co:22，priority 99）是两条不同通道——"
                 "那个 SSH 端点本轮实测有应答（见 hf_ssh 条目），本条 HTTP API 通道则是不可达。"
                 "已登记渠道描述符与自检脚本（services/free_key_channels.py / scripts/free_channel_keycheck.py），且已于 R3.8 并入 free_model_router.candidates()；但受 fail-closed 价格门约束，光填 key 仍不会自动路由——还须把该渠道 id 登记进 llm.free.trusted_models（例 groq:*）才会进池（详见 docs/免费路由_需Key渠道接线路径-20261017.md）。"),
    },
]


# 便捷分组：真零配置 vs 免费但要 key/登录
def zero_config_only() -> list[dict]:
    """真·零配置零成本通道（开箱即用）。"""
    return [c for c in FREE_CHANNEL_CATALOG if c.get("zero_config")]


def free_but_need_key() -> list[dict]:
    """免费但仍需 key/登录的通道（明确标注「免费≠无 key」）。"""
    return [c for c in FREE_CHANNEL_CATALOG if not c.get("zero_config")]


def reachable_now() -> list[dict]:
    """本机本轮探测有服务端应答的通道（reachable is True）。"""
    return [c for c in FREE_CHANNEL_CATALOG if c.get("reachable") is True]


def as_dict() -> dict:
    """整包导出，供 UI/设置页直接读取。"""
    return {
        "version": "2026-10-17-r2",
        "channels": FREE_CHANNEL_CATALOG,
        "summary": {
            "total": len(FREE_CHANNEL_CATALOG),
            "zero_config_count": len(zero_config_only()),
            "need_key_count": len([c for c in FREE_CHANNEL_CATALOG if c.get("need_key")]),
            "need_login_count": len([c for c in FREE_CHANNEL_CATALOG if c.get("need_login")]),
            "reachable_count": len(reachable_now()),
        },
        "notes": [
            "「免费≠无 key」：OpenRouter/NVIDIA/Gemini/Groq/Mistral/Cerebras/SambaNova/"
            "GitHub Models/Cloudflare/Cohere/智谱/HuggingFace 都要 key，只是 key 免费申请。",
            "「真零配置」：本机 ollama（先 `ollama serve`）+ pollinations/llm7（免 Key 远端，"
            "2026-10-13 实测 chat 200，2026-10-17 复测其目录端点仍 200）。",
            "「本机不可用」：ollama_local 未起进程（连接被拒，curl exit=7）；"
            "google_gemini / mistral / huggingface 三条连接超时（无任何 HTTP 响应）。"
            "hf_ssh 的 chat.hf.co:22 有 SSHD 应答但需凭据，且本项目实现仍是 disabled 占位"
            "（priority 99，不参与轮询）。",
            "【reachable 口径（2026-10-17 r2 修订）】True=收到了服务端/边缘的 HTTP 响应"
            "（任何状态码，含 401/403/405/429），即主机可达；匿名被拒 ≠ 本机网络不通；"
            "False=没有收到任何 HTTP 响应（连接超时 / DNS 解析失败 / 连接被拒 / TLS 握手失败）；"
            "unknown=需 key 或登录态、端点不公开，匿名请求无法触达判定。非 HTTP 通道（SSH）按同口径"
            "以「服务端是否应答」判定。",
            "【r2 修订说明】上一版把 groq/cerebras/cohere 的 403 误述为「本机网络不通」——"
            "403/405 是边缘/服务端应答，恰恰证明主机可达；本轮已把这三条改为 reachable=True 并重写因果。"
            "同类复核：github_models（200）、cloudflare_workers_ai（405）由 unknown 改 True；"
            "hf_ssh（SSHD 应答 Permission denied=需凭据）由 False 改 True。",
            "「已登记≠已接入」：2026-10-17 补登的 10 条（sambanova/zhipu/github_models/"
            "cloudflare_workers_ai/groq/cerebras/cohere/google_gemini/mistral/huggingface）"
            "已从「未接入（仅登记）」更新为指向描述符模块 services/free_key_channels.py（含 "
            "channels_with_keys()）与自检脚本 scripts/free_channel_keycheck.py，接线路径见 "
            "docs/免费路由_需Key渠道接线路径-20261017.md；**已于 R3.8 并入 "
            "free_model_router.candidates()，参与降级链轮询**——但受 fail-closed 价格门约束："
            "这 10 条没有目录抓取器，价格证据集（free_ids/paid_ids）恒空，故【只填 key 仍不会路由】，"
            "还须把该渠道 id 登记进白名单 llm.free.trusted_models（例 groq:*）才会进池；"
            "漏登则 0 候选、0 出站。",
        ],
    }


if __name__ == "__main__":  # 手动跑一下打印清单
    import json
    print(json.dumps(as_dict(), ensure_ascii=False, indent=2))
