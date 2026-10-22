# -*- coding: utf-8 -*-
"""免费路由「需 Key 渠道」连通性自检（只读、掩码、不伪造）

对 ``services/free_key_channels.py`` 登记的那批「需 Key」免费渠道，
逐个读 config 里的 key，**只对已配置 key 的渠道**发一次极小请求
（``POST /chat/completions``，``max_tokens=1``）验证可用性，并打印：

    id | 是否配 key（掩码）| 实测结果 | HTTP 状态或异常类型 | 延迟ms | 实际命中的 model

行为约定
--------
* **未配 key 的渠道**：打印「未配置 key（跳过）」，**不作为失败**；
* **配了 key 但必填配置不齐**（如 cloudflare 缺 ``account_id``）：打印
  ``缺必填配置（account_id）：跳过``，**也不作为失败**（退出码不受影响）——
  因为此时该渠道的地址根本不可用，发请求没有意义；
* **绝不把请求发往含未替换占位符的地址**：地址一律取自
  ``services/free_key_channels.py::channels_with_keys()`` 的 ``base_url_resolved``
  （该函数的契约：为空串 ⇒ 地址不可用、原因见 ``missing_config``；非空 ⇔ 不含 ``{``），
  本脚本**不再自己拼 URL**；
* **绝不打印 key 明文**：只打掩码（前 4 后 3）；任何错误文本在打印前先做掩码清洗，
  防止 key 从上游错误信息里漏出来；
* **退出码**：全部通过 = 0；有用例真失败 = 1；全部未配 key 也算 0；
* 只把请求发往**该渠道自己的官方端点**（base_url 来自描述符，不转发到任何第三方）。

可选开关
--------
``--probe-anonymous``：额外对每个渠道的 ``probe_path`` 发一次**不带鉴权**的 GET，
用于证明「探测代码路径确实在发真实 HTTP 请求」，并记录端点真实状态码。
该段结果**不计入退出码**，纯证据用途（不发送任何 key）。

用法（仓库根、项目 venv）::

    .\\.venv\\Scripts\\python.exe scripts\\free_channel_keycheck.py --all
    .\\.venv\\Scripts\\python.exe scripts\\free_channel_keycheck.py --provider groq
    .\\.venv\\Scripts\\python.exe scripts\\free_channel_keycheck.py --all --include-env
    .\\.venv\\Scripts\\python.exe scripts\\free_channel_keycheck.py --all --probe-anonymous
    .\\.venv\\Scripts\\python.exe scripts\\free_channel_keycheck.py --all --json

说明：本脚本**只读**——不写 config、不改设置、不落盘任何 key。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

for _stream in (sys.stdout, sys.stderr):   # Windows 控制台/管道下保证中文不乱码
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

from services import free_key_channels as fkc  # noqa: E402

DEFAULT_CHAT_TIMEOUT = 15.0
DEFAULT_PROBE_TIMEOUT = 8.0
_PROBE_QUESTION = "ping"

# 兜底清洗：即便调用方忘了传 secret，也把常见 key 形态掩掉
_GENERIC_KEY_RE = re.compile(
    r"\b(sk-[A-Za-z0-9\-_]{6,}|sk-or-v1-[A-Za-z0-9]{6,}|gsk_[A-Za-z0-9]{6,}"
    r"|AIza[A-Za-z0-9\-_]{6,}|hf_[A-Za-z0-9]{6,}|xai-[A-Za-z0-9]{6,}"
    r"|csk-[A-Za-z0-9]{6,}|[A-Za-z0-9]{32,})\b")
_AUTH_HEADER_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9\-_.]{6,})", re.IGNORECASE)


def _redact(text: object, secrets: list[str] | None = None) -> str:
    """掩码清洗：把实际 key 与常见 key 形态从任意文本里替换掉。**输出永远不含明文 key**。"""
    s = "" if text is None else str(text)
    for secret in (secrets or []):
        token = str(secret or "").strip()
        if len(token) >= 6 and token in s:
            s = s.replace(token, fkc.mask_key(token))
    s = _AUTH_HEADER_RE.sub(lambda m: m.group(1) + fkc.mask_key(m.group(2)), s)
    s = _GENERIC_KEY_RE.sub(lambda m: fkc.mask_key(m.group(1)), s)
    return s


def _load_config(path: str) -> dict:
    """读 config（不存在就用空配置）。刻意不用 ConfigService，避免任何写盘副作用。"""
    if not path or not os.path.isfile(path):
        print(f"[keycheck] 未发现配置文件 {path or '(未指定)'}，按空配置处理（各渠道将显示未配置 key）")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        print(f"[keycheck] 已读取配置 {path}")
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        print(f"[keycheck] 配置读取失败，按空配置处理: {type(e).__name__}: {_redact(e)}")
        return {}


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    # 某些渠道（如 sambanova）在本机 schannel 吊销检查离线时会握手失败；
    # Python/OpenSSL 默认不做该检查，这里保持默认严格校验，不主动放宽。
    return ctx


def _http(url: str, method: str = "GET", headers: dict | None = None,
          body: bytes | None = None, timeout: float = DEFAULT_CHAT_TIMEOUT):
    """发一次 HTTP 请求，返回 (status, text, elapsed_ms)。

    任何异常向上抛给调用方分类；抛之前把本次耗时挂到异常的 ``elapsed_ms`` 上，
    以便失败用例也能报出延迟（HTTPError 同样是「真实往返」，不发就报不出延迟）。
    """
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=headers or {})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:  # noqa: S310
            raw = resp.read(20000)
            return (getattr(resp, "status", None) or resp.getcode(),
                    raw.decode("utf-8", "replace"), (time.time() - t0) * 1000.0)
    except Exception as e:  # noqa: BLE001 - 附上耗时后原样抛出
        try:
            e.elapsed_ms = (time.time() - t0) * 1000.0
        except Exception:  # noqa: BLE001
            pass
        raise


def _build_headers(channel: dict, key: str) -> dict:
    """按描述符的 auth_header 模板构造请求头（只在此处接触明文 key，且绝不外泄）。"""
    headers = {"Accept": "application/json", "User-Agent": "LoveAI-FreeChannelKeyCheck/1.0"}
    template = channel.get("auth_header") or {"Authorization": "Bearer {key}"}
    for name, value in template.items():
        headers[name] = str(value).replace("{key}", key)
    return headers


def check_channel(channel: dict, contract_row: dict | None, config: dict,
                  include_env: bool, timeout: float) -> dict:
    """检查单个渠道。返回结构化结果（不含明文 key）。

    ``contract_row``：该渠道在 ``channels_with_keys(config, include_env)`` 里的那一行
    （未配 key 时为 ``None``）。**地址一律取它的 ``base_url_resolved``**，本函数不再自行
    拼 URL —— 这样「必填配置不齐 → 不交地址」的契约就自动生效（D3/契约修正）。
    """
    cid = channel["id"]
    key = fkc.key_for(channel, config, include_env=include_env)
    result: dict = {
        "id": cid,
        "key_masked": fkc.mask_key(key),
        "key_source": fkc.key_source(channel, config, include_env=include_env),
        "configured": bool(key),
        "status": "",
        "detail": "",
        "latency_ms": None,
        "model": "",
        "failed": False,
        "skip_reason": "",
        "messages": [],
    }

    if not key:
        result["status"] = "未配key"
        result["detail"] = "未配置 key（跳过）"
        result["messages"] = [
            f"  未配置 key -> 跳过（请填 {channel['key_config']}"
            f"{' 或设环境变量 ' + str(channel['env_key']) if channel.get('env_key') else ''}）",
            f"  注册入口 : {channel.get('signup_url', '')}",
        ]
        return result

    missing = fkc.missing_config(channel, config)
    base = str((contract_row or {}).get("base_url_resolved") or "").rstrip("/")
    # 契约：base_url_resolved 为空串 ⇒ 地址不可用（原因看 missing_config）。
    # 另加 ``"{" in base`` 兜底：绝不把含未替换占位的地址发出去。
    if missing or not base or "{" in base:
        why = ",".join(str(x) for x in missing) or "地址不可用"
        result["status"] = "跳过"
        result["skip_reason"] = "missing_config"
        result["detail"] = f"缺必填配置（{why}）：跳过"
        result["messages"] = [
            f"  已配 key，但缺少必填配置 {missing or ['(未知)']} -> 跳过（不假装通过、也不发无意义请求）",
            "  需要的配置键 : "
            + ", ".join(str(v) for v in (channel.get("required_config") or {}).values()),
            "  说明：地址不可用时脚本不发任何请求（契约见 services/free_key_channels.py"
            "::channels_with_keys 的 base_url_resolved）。",
        ]
        return result

    models = fkc.models_for(channel, config)
    if not models:
        result["status"] = "不可用"
        result["detail"] = "无候选模型"
        result["failed"] = True
        result["messages"] = [f"  已配 key，但候选模型列表为空（{channel['models_config']}）"]
        return result

    model = models[0]
    result["model"] = model
    url = base + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": _PROBE_QUESTION}],
        "max_tokens": 1,
        "temperature": 0,
        "stream": False,
    }).encode("utf-8")
    headers = _build_headers(channel, key)
    headers["Content-Type"] = "application/json"

    try:
        status, text, ms = _http(url, "POST", headers=headers, body=payload, timeout=timeout)
        result["latency_ms"] = round(ms, 1)
        usable = False
        try:
            data = json.loads(text)
            choices = data.get("choices") or []
            usable = bool(choices) and (
                choices[0].get("message") is not None
                or choices[0].get("text") is not None)
        except Exception:  # noqa: BLE001 - 非 JSON 响应：2xx 也不算可用
            usable = False
        if 200 <= int(status) < 300 and usable:
            result["status"] = "可用"
            result["detail"] = f"HTTP {status}"
        else:
            result["status"] = "不可用"
            result["detail"] = f"HTTP {status}" + ("" if usable else "（响应异常）")
            result["failed"] = True
        result["messages"] = [
            f"  model={model} url={url}",
            f"  HTTP {status} | 响应体(<=200字): {_redact(text[:200], [key])!r}",
        ]
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(300).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        tag = {401: "鉴权失败", 403: "被拒/无权限", 404: "端点或模型不存在",
               429: "限流", 402: "额度用尽"}.get(e.code, "")
        result["status"] = "不可用"
        result["detail"] = f"HTTP {e.code}" + (f" [{tag}]" if tag else "")
        elapsed = getattr(e, "elapsed_ms", None)
        result["latency_ms"] = round(elapsed, 1) if elapsed is not None else None
        result["failed"] = True
        result["messages"] = [
            f"  model={model} url={url}",
            f"  HTTP {e.code}{(' [' + tag + ']') if tag else ''} | 响应体(<=300字): "
            f"{_redact(body, [key])!r}",
        ]
    except Exception as e:  # noqa: BLE001 - 超时/连接/DNS/TLS
        result["status"] = "不可用"
        result["detail"] = f"{type(e).__name__}"
        result["failed"] = True
        result["messages"] = [
            f"  model={model} url={url}",
            f"  异常: {type(e).__name__}: {_redact(str(e)[:200], [key])}",
        ]
    return result


def anonymous_probe(channel: dict, config: dict, timeout: float) -> dict:
    """**不带鉴权**探一次 probe_path：证明探测代码路径真的在发 HTTP，并记录端点真实状态。

    不发任何 key；结果只作证据，**不计入退出码**。

    地址同样遵守契约（不自行拼含占位的 URL）：必填配置不齐或地址里仍有 ``{`` 时
    **不发请求**，直接给一行 ``跳过`` 证据行。
    """
    missing = fkc.missing_config(channel, config)
    base = fkc.base_url_for(channel, config).rstrip("/")
    if missing or not base or "{" in base:
        why = ",".join(str(x) for x in missing) or "地址不可用"
        return {"id": channel["id"], "method": "GET", "url": "",
                "status": "跳过(缺必填配置)",
                "latency_ms": None, "bytes": 0,
                "body": f"缺必填配置（{why}）：跳过（不发请求）"}
    url = base + str(channel.get("probe_path") or "/models")
    row = {"id": channel["id"], "method": "GET", "url": url,
           "status": "", "latency_ms": None, "bytes": 0, "body": ""}
    headers = {"Accept": "application/json",
               "User-Agent": "LoveAI-FreeChannelKeyCheck/1.0"}
    try:
        status, text, ms = _http(url, "GET", headers=headers, timeout=timeout)
        row.update(status=f"HTTP {status}", latency_ms=round(ms, 1),
                   bytes=len(text.encode("utf-8", "replace")),
                   body=_redact(text[:160]))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(300).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        elapsed = getattr(e, "elapsed_ms", None)
        row.update(status=f"HTTP {e.code}",
                   latency_ms=round(elapsed, 1) if elapsed is not None else None,
                   bytes=len(body.encode("utf-8", "replace")),
                   body=_redact(body[:160]))
    except Exception as e:  # noqa: BLE001
        row.update(status=type(e).__name__, body=_redact(str(e)[:160]))
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="免费路由「需 Key 渠道」连通性自检（只读、掩码、不伪造）")
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--provider", help="只检查指定渠道 id（见 services/free_key_channels.py）")
    sel.add_argument("--all", action="store_true", help="检查全部渠道")
    ap.add_argument("--config", default=os.path.join(_ROOT, "config.json"),
                    help="config.json 路径（默认仓库根 config.json）")
    ap.add_argument("--include-env", action="store_true",
                    help="config 里没填时，回退读渠道描述符的 env_key 环境变量")
    ap.add_argument("--probe-anonymous", action="store_true",
                    help="额外做一次不带鉴权的 GET 探测（证据用，不计入退出码）")
    ap.add_argument("--timeout", type=float, default=DEFAULT_CHAT_TIMEOUT,
                    help=f"chat 探测超时秒数（默认 {DEFAULT_CHAT_TIMEOUT}）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON（掩码后）")
    args = ap.parse_args(argv)

    config = _load_config(args.config)

    if args.all:
        channels = list(fkc.FREE_KEY_CHANNELS)
    else:
        channel = fkc.get_channel(args.provider)
        if channel is None:
            print(f"[keycheck] 未知渠道 id: {args.provider}")
            print("[keycheck] 可选: " + ", ".join(fkc.channel_ids()))
            return 2
        channels = [channel]

    # 地址的唯一来源：描述符契约（base_url_resolved 为空 ⇒ 地址不可用）。
    # 只把请求发往这里的非空地址 —— 脚本绝不自行拼 URL、也绝不发含 ``{占位}`` 的地址。
    contract = {r["id"]: r for r in fkc.channels_with_keys(config, include_env=args.include_env)}

    rows: list[dict] = []
    for channel in channels:
        rows.append(check_channel(channel, contract.get(channel["id"]), config,
                                  args.include_env, args.timeout))

    anon_rows: list[dict] = []
    if args.probe_anonymous:
        for channel in channels:
            anon_rows.append(anonymous_probe(channel, config, DEFAULT_PROBE_TIMEOUT))

    failed = [r for r in rows if r["failed"]]
    skipped = [r for r in rows if not r["configured"]]
    skipped_cfg = [r for r in rows if r["configured"] and r.get("skip_reason")]
    passed = [r for r in rows
              if r["configured"] and not r["failed"] and not r.get("skip_reason")]
    attempted = [r for r in rows if r["configured"] and not r.get("skip_reason")]

    if args.json:
        print(json.dumps({
            "config": args.config,
            "include_env": bool(args.include_env),
            "rows": rows,
            "anonymous_probe": anon_rows,
            "summary": {"total": len(rows), "passed": len(passed),
                        "failed": len(failed), "skipped_no_key": len(skipped),
                        "skipped_missing_config": len(skipped_cfg),
                        "attempted": len(attempted),
                        "real_connectivity_verified": bool(attempted)},
        }, ensure_ascii=False, indent=2))
    else:
        print("")
        print("=" * 108)
        print("免费路由「需 Key 渠道」自检  (id | 配key? | 结果 | HTTP/异常 | 延迟ms | model)")
        print("=" * 108)
        header = f"{'id':<22} {'key':<18} {'结果':<8} {'HTTP/异常':<18} {'延迟ms':>9}  model"
        print(header)
        print("-" * len(header))
        for r in rows:
            key_col = r["key_masked"] if r["configured"] else "(未配置)"
            lat = "" if r["latency_ms"] is None else f"{r['latency_ms']:.1f}"
            print(f"{r['id']:<22} {key_col:<18} {r['status']:<8} "
                  f"{r['detail']:<18} {lat:>9}  {r['model']}")
        print("-" * len(header))
        for r in rows:
            print(f"\n[{r['id']}] {r['detail']}")
            for line in r["messages"]:
                print(line)

        if anon_rows:
            print("")
            print("=" * 108)
            print("附：不带鉴权的 GET 探测（无 key、仅证明探测路径在发真实 HTTP；不计入退出码）")
            print("=" * 108)
            for a in anon_rows:
                lat = "" if a["latency_ms"] is None else f"{a['latency_ms']:.1f}"
                print(f"{a['id']:<22} {a['status']:<14} {lat:>9} ms  "
                      f"bytes={a['bytes']:<6} {a['body'][:70]!r}")

        print("")
        print("=" * 108)
        print(f"汇总: 共 {len(rows)} 条 | 通过 {len(passed)} | 真失败 {len(failed)} | "
              f"未配key跳过 {len(skipped)}"
              + (f" | 缺必填配置跳过 {len(skipped_cfg)}" if skipped_cfg else ""))
        if attempted:
            print(f"真实连通验证: 已发起 {len(attempted)} 条真实请求"
                  f"（通过 {len(passed)}，失败 {len(failed)}，仅发往各渠道官方端点）")
        elif skipped_cfg:
            print(f"真实连通验证: 未做（有 {len(skipped_cfg)} 条渠道配了 key 但缺必填配置，"
                  f"已按契约跳过、**未发任何请求**："
                  f"{', '.join(r['id'] for r in skipped_cfg)}）")
        else:
            print("真实连通验证: 未做（本轮没有任何渠道配置 key，"
                  "全部为「未配置 key（跳过）」—— 未做真实连通验证）")
        print("=" * 108)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
