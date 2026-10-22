# -*- coding: utf-8 -*-
r"""免费模型路由诊断脚本（R3.4 路由内核的「出口」）。

运行即把 FreeModelRouter.routing_snapshot() 打出来，证明动态惩罚 / 滑动窗口限流 /
429 冷却升级 / Sticky 这些新能力**在真实进程里能被看见**，而不是埋在代码里没有出口。

用法（在仓库根目录、用项目 venv 的 python 运行）::

    .\.venv\Scripts\python.exe scripts\free_router_diag.py             # 默认：先按 TTL 刷新目录
    .\.venv\Scripts\python.exe scripts\free_router_diag.py --offline   # 只用缓存/配置，绝不联网
    .\.venv\Scripts\python.exe scripts\free_router_diag.py --live-chat "你好"   # 真发一次免费请求
    .\.venv\Scripts\python.exe scripts\free_router_diag.py --json      # 只输出原始 JSON

说明：
- 不加 ``--offline`` 时会调一次 ``refresh_catalog()``（网络请求受 24h TTL 约束，
  与真实运行一致）；加 ``--offline`` 则完全不联网。
- 本脚本只读：不写 config、不改任何设置；唯一会变的是路由自身的状态库
  （data/free_router/router_state.db 的限流计数/冷却等级/Sticky 映射）。
- 绝不路由到付费模型：候选只来自免费池，池空时会如实报错。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

for _stream in (sys.stdout, sys.stderr):   # Windows 控制台/管道下保证中文不乱码
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _load_config() -> dict:
    """读仓库根 config.json（不存在就用空配置）。

    刻意不用 ConfigService：避免诊断脚本产生任何写盘副作用。
    """
    path = os.path.join(_ROOT, "config.json")
    if not os.path.isfile(path):
        print(f"[diag] 未发现 {path}，使用空配置（零配置免 Key 渠道仍可用）")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        print(f"[diag] 已读取配置 {path}")
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        print(f"[diag] 配置读取失败，改用空配置: {type(e).__name__}: {e}")
        return {}


def _fmt_num(value) -> str:
    return "—" if value is None else (f"{value:.1f}" if isinstance(value, float) else str(value))


def _print_table(snap: dict) -> None:
    """人读表格：一眼看出「谁被惩罚下沉、谁在冷却、谁被限流挡了」。"""
    print("")
    print(f"generated_at    : {snap.get('generated_at')}")
    print(f"last_routed_via : {snap.get('last_routed_via') or '(本进程尚未命中过)'}")
    print(f"sticky_sessions : {snap.get('sticky_sessions')}")
    mode = snap.get("mode") or {}
    print(f"mode            : free_only={mode.get('free_only')} "
          f"fail_closed={mode.get('fail_closed')}")
    pers = snap.get("persistence") or {}
    print(f"persistence     : ok={pers.get('ok')} path={pers.get('path')} "
          f"error={pers.get('error') or ''}")
    print("")
    header = (f"{'provider:model':<46} {'base':>4} {'pen':>5} {'score':>6} "
              f"{'elig':>5} {'cd_lv':>5} {'rest(s)':>8} {'ok':>3} {'fail':>4} "
              f"{'ms':>8}  limits(rpm/rpd/tpm/tpd used)")
    print(header)
    print("-" * len(header))
    for ch in snap.get("channels") or []:
        lim = ch.get("limits") or {}
        used = (f"{_fmt_num(lim.get('rpm'))}/{_fmt_num(lim.get('rpd'))}/"
                f"{_fmt_num(lim.get('tpm'))}/{_fmt_num(lim.get('tpd'))} "
                f"<- {_fmt_num(lim.get('rpm_used'))}/{_fmt_num(lim.get('rpd_used'))}/"
                f"{_fmt_num(lim.get('tpm_used'))}/{_fmt_num(lim.get('tpd_used'))}")
        print(f"{(ch.get('provider') + ':' + ch.get('model'))[:46]:<46} "
              f"{ch.get('base_priority'):>4} {ch.get('penalty'):>5} "
              f"{ch.get('effective_score'):>6} "
              f"{('Y' if ch.get('eligible') else 'N'):>5} "
              f"{ch.get('cooldown_level'):>5} {ch.get('remaining_sec'):>8} "
              f"{ch.get('ok'):>3} {ch.get('fail'):>4} "
              f"{ch.get('latency_ms'):>8}  {used}")
    print("")
    print("列含义：base=基础优先级 / pen=惩罚分 / score=base+pen（越小越优先）/ "
          "elig=当前是否会被选中 / cd_lv=429 冷却档位(0=未升级) / rest=剩余冷却秒数")
    print("注：limits 里 null = 该维度未配置上限（本项目不内置任何第三方额度数字）。")


def main() -> int:
    ap = argparse.ArgumentParser(description="免费模型路由诊断（打印 routing_snapshot）")
    ap.add_argument("--offline", action="store_true",
                    help="完全不联网：只用已有缓存与配置")
    ap.add_argument("--json", action="store_true", help="只输出原始 JSON 快照")
    ap.add_argument("--live-chat", metavar="PROMPT", default=None,
                    help="真向免费池发一次 chat 请求（会联网），验证端到端与 routed_via")
    ap.add_argument("--log-level", default="INFO",
                    help="日志级别（默认 INFO，可设 WARNING 只看告警）")
    args = ap.parse_args()

    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO),
                        format="%(levelname)s %(name)s: %(message)s")

    from services.free_model_router import FreeModelRouter

    cfg = _load_config()
    router = FreeModelRouter(cfg)

    if not args.offline:
        try:
            st = router.refresh_catalog()   # TTL 约束内不会重复联网
            print(f"[diag] 动态目录: {json.dumps(st, ensure_ascii=False)}")
        except Exception as e:  # noqa: BLE001
            print(f"[diag] 目录刷新失败（不影响快照）: {type(e).__name__}: {e}")

    if args.live_chat:
        print(f"\n[diag] 向免费池发真实请求: {args.live_chat!r}")
        try:
            out = router.chat("你是诊断用的助手，请只回一句话。", args.live_chat,
                              max_tokens=64, timeout=40.0)
            print("[diag] 命中 routed_via = "
                  f"{out.get('routed_via') or '(缺失)'} | provider={out.get('provider')} "
                  f"| model={out.get('model')}")
            print(f"[diag] tried = {out.get('tried')}")
            print(f"[diag] 回复片段 = {(out.get('text') or '')[:200]!r}")
        except Exception as e:  # noqa: BLE001
            print(f"[diag] 免费池请求失败（fail-closed，不会改道付费模型）: "
                  f"{type(e).__name__}: {e}")

    snap = router.routing_snapshot()
    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    else:
        _print_table(snap)
        print("---- 原始 routing_snapshot() JSON ----")
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
