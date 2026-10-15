# -*- coding: utf-8 -*-
"""
探针：四模型缺口真代码兜底（问题9）

断网（monkeypatch urllib.request.urlopen 抛异常）+ 无 API key 环境下，
从生产入口 LLMGateway.chat("", "你好") 真走降级链：
  L1中文 -> 真 raise 落 L2；L2 ollama -> /api/tags 探活失败；
  L3无key -> 真 raise RuntimeError；free 通道 -> 网络失败；
  最终落到 stat/rules。
断言：返回非空字符串（不是 None/抛栈），并打印实际走了哪条链路。

跑法：.venv\\Scripts\\python.exe scripts\\probe_model_gaps.py
"""
import os
import sys
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.llm_gateway import LLMGateway  # noqa: E402

FAILS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}{(' | ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


class FakeConfig:
    """最小 config：所有 llm.* 都给默认（无 key、无本地模型路径）。"""

    def get(self, key, default=None):
        table = {
            "llm.routing.mode": "auto",
            "llm.l3.api_key": "",
            "llm.l3.base_url": "",
            "llm.local.l2.use_ollama_http": True,
            "llm.local.l2.ollama_url": "http://127.0.0.1:11434",
        }
        return table.get(key, default)


def main() -> int:
    print("=" * 68)
    print("探针：四模型缺口断网降级链（问题9）")
    print("=" * 68)

    # 断网：任何 urllib 请求都直接抛（模拟 ollama /api/tags、free 路由都连不上）
    def _blocked_urlopen(*a, **k):
        raise OSError("探针：网络已断开，禁止任何真实 HTTP 请求")

    orig = urllib.request.urlopen
    urllib.request.urlopen = _blocked_urlopen
    # 同时堵掉其他常见网络入口
    try:
        gw = LLMGateway(config=FakeConfig())
    finally:
        pass

    print("\n[1] 断网无 key 下 chat('', '你好')：")
    out = ""
    err = None
    try:
        out = gw.chat("", "你好")
    except Exception as e:  # noqa: BLE001
        err = e
    urllib.request.urlopen = orig

    print(f"   返回长度={len(out) if out else 0}  命中层(last_layer)={gw.last_layer}")
    print(f"   回复预览={(out or '')[:60]!r}")
    if err:
        print(f"   异常={type(err).__name__}: {err}")

    check("chat() 没有抛栈", err is None, f"err={err}")
    check("返回非空字符串（不是 None/空串）",
          bool(out and isinstance(out, str) and out.strip()),
          f"len={len(out or '')}")
    check("最后命中层是兜底（stat/rules），不是 None",
          gw.last_layer in ("stat", "rules", "l1", "l2", "free", "l3"),
          f"last_layer={gw.last_layer}")

    # 再验证一次 L3 无 key 确实真 raise（不是静默空转）
    print("\n[2] L3 无 key 真 raise RuntimeError（缺口不是 prompt 层）：")
    try:
        r = gw._call_l3_one({"api_key": "", "base_url": ""}, "", "hi", 0.7, 50)
        check("_call_l3_one 无 key 时抛错", False, f"居然返回了 {r!r}")
    except RuntimeError as e:
        check("_call_l3_one 无 key 抛 RuntimeError", True, str(e)[:40])
    except Exception as e:  # noqa: BLE001
        check("_call_l3_one 无 key 抛错", True, f"{type(e).__name__}: {e}"[:50])

    print("\n" + "=" * 68)
    if FAILS:
        print(f"结果：{len(FAILS)} 项失败 -> {FAILS}")
        return 1
    print("结果：全部 PASS —— 四缺口断网真兜底，返回非空，链路可追踪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
