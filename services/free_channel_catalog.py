"""
LoveAI v2 - R3.3-43 免费 AI 通道全景清单（结构化导出）
=====================================================

把 6 类「免费/零成本」通道整理成 dict，供 UI/设置页 / 诊断页读取。
所有实测数据来自 2026-10-10 本机 PowerShell 探测（详见 docs/免费AI通道全景清单-20261010.md）。

字段约定
--------
    id             : 通道唯一 id（与 llm_gateway 降级链 layer 名对齐）
    name           : 人类可读名
    need_login     : 是否需要登录浏览器/账号
    need_key       : 是否需要 API Key（True=免费≠无 key）
    zero_config    : 是否真零配置（开箱即用）
    reachable      : 本机实测可达性 (True/False/"unknown")
    latency_ms     : 探测到的延迟（毫秒），未探测填 None
    priority       : 降级链优先级，数字越小越靠上（越先尝试）
    code_path      : 代码落点文件（相对项目根）
    note           : 备注（免费≠无 key 标注、限速、额度等）
"""
from __future__ import annotations

from typing import Any


# 6 类通道，按降级优先级排序（数字小 = 先试）
FREE_CHANNEL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "ollama_local",
        "name": "本机 Ollama (loveai-qwen05b / loveai-qwen15b)",
        "need_login": False,
        "need_key": False,
        "zero_config": True,
        "reachable": False,   # 2026-10-10 探测时 ollama 进程未起（连 127.0.0.1:11434 拒）
        "latency_ms": None,   # 起服务后实测 <50ms 本机回环
        "priority": 1,
        "code_path": "services/llm_gateway.py::_call_l2_ollama",
        "models_known": ["loveai-qwen05b:latest", "loveai-qwen15b:latest"],
        "note": "真·零配置零成本。必须先 `ollama serve`；模型名自动从 /api/tags 发现。",
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
        "note": "完全免费无需 Key；OpenAI 兼容端点 text.pollinations.ai/openai。",
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
        "note": "50+ 模型基础速率免注册；OpenAI 兼容端点 api.llm7.io/v1。",
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
                 "但 chat 调用需 openrouter api_key，且每小时有速率限制。"),
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
                 "未配 key 时用 build.nvidia.com/models.md 公开目录 best-effort 解析。"),
    },
    {
        "id": "browser_ai_hub",
        "name": "浏览器 AI 中枢 14 源（博查/密塔/天工/Kimi/元宝/通义/智谱/"
                "DeepSeek/阶跃/文心/商汤/讯飞/豆包/火山）",
        "need_login": True,   # 借浏览器登录态
        "need_key": False,
        "zero_config": False,
        "reachable": "unknown",   # 14 个源各自站点，不做全量探测
        "latency_ms": None,
        "priority": 4,
        "code_path": "services/browser_ai_hub.py",
        "note": ("借用户在浏览器里已登录的账号 cookie，免再申请 key；"
                 "风控命中自动降级 blocked；多 profile 轮询。"),
    },
    {
        "id": "desktop_ai_connector",
        "name": "Desktop AI Connector (Trae / WorkBuddy / Qoder / CodeBuddy 凭据借取)",
        "need_login": True,
        "need_key": False,
        "zero_config": False,
        "reachable": "unknown",
        "latency_ms": None,
        "priority": 5,
        "code_path": "services/desktop_ai_connector.py",
        "note": "从本机已装 IDE 的本地配置里挖 API 凭据，复用用户已有订阅。",
    },
    {
        "id": "hf_ssh",
        "name": "HuggingFace SSH 公开聊天 (chat.hf.co)",
        "need_login": False,
        "need_key": False,
        "zero_config": False,
        "reachable": False,   # 2026-10-10 ssh exit=255
        "latency_ms": None,
        "priority": 99,      # 不可用，排到最底，不参与自动轮询
        "code_path": "services/hf_ssh_channel.py",
        "note": ("实测 chat.hf.co / benchmark-hf.co 均无公开 SSH 端点；"
                 "当前为 disabled 占位实现，chat() 直接 raise。"
                 "用户自配跳板后可启用。"),
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
    """本机本轮探测可达的通道。"""
    return [c for c in FREE_CHANNEL_CATALOG if c.get("reachable") is True]


def as_dict() -> dict:
    """整包导出，供 UI/设置页直接读取。"""
    return {
        "version": "2026-10-10",
        "channels": FREE_CHANNEL_CATALOG,
        "summary": {
            "total": len(FREE_CHANNEL_CATALOG),
            "zero_config_count": len(zero_config_only()),
            "need_key_count": len([c for c in FREE_CHANNEL_CATALOG if c.get("need_key")]),
            "need_login_count": len([c for c in FREE_CHANNEL_CATALOG if c.get("need_login")]),
            "reachable_count": len(reachable_now()),
        },
        "notes": [
            "「免费≠无 key」：OpenRouter/NVIDIA 要 key，只是 key 免费申请。",
            "「真零配置」：本机 ollama（起服务后）+ pollinations/llm7（免 Key 远端，2026-10-13 实测 chat 200）。",
            "「不可用」：hf_ssh 本轮探测不通，已写成 disabled 占位。",
        ],
    }


if __name__ == "__main__":  # 手动跑一下打印清单
    import json
    print(json.dumps(as_dict(), ensure_ascii=False, indent=2))
