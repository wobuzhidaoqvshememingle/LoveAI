# -*- coding: utf-8 -*-
"""
探针：NPD 识别生产链路（问题2）

不走单测手动塞 persona，从生产入口 AnalysisOrchestrator().orchestrate() 真走一遍：
  - 喂 12 条 NPD 聊天（煤气灯/贬低/PUA 话术）
  - 断言：
      1) personality.primary_personality 含「自恋」或「NPD」
      2) strategy.manipulation_counter.triggered == True
      3) candidates 有 ≥3 条非空反制话术

跑法：.venv\\Scripts\\python.exe scripts\\probe_npd_production.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.analysis_orchestrator import AnalysisOrchestrator  # noqa: E402

FAILS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}{(' | ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


# 12 条典型 NPD/煤气灯聊天（对方口吻）
NPD_CHATS = [
    "你太敏感了吧，我就是随口一说。",
    "都是你的错，要不是你整天胡思乱想，我们能吵成这样？",
    "我从没说过那句话，你自己记错了。",
    "你是不是有问题？这点小事也要揪着不放。",
    "我这是为你好，你还不领情。",
    "你离开我就没人要了，只有我能忍你。",
    "你想多了，我跟她真的只是普通朋友。",
    "你朋友都不了解你，只有我才懂你。",
    "你不配穿成这样出门，给谁看呢。",
    "不是我不回你，是你太烦人了，我需要空间。",
    "你有病吧，这点事也要生气，真搞不懂你。",
    "我这都是为我们以后打算，你却只顾着你自己。",
]


def main() -> int:
    print("=" * 68)
    print("探针：NPD 识别生产链路（问题2）——从 orchestrate() 真走一遍")
    print("=" * 68)

    orch = AnalysisOrchestrator(db=None, config=None)

    recent = [{"role": "other", "text": t} for t in NPD_CHATS[:-1]]
    # 生产入口：context 里绝不手动塞 persona（验收官就是揪这个）
    result = orch.orchestrate(
        person_id="probe_npd",
        chat_text=NPD_CHATS[-1],
        context={
            "recent_chats": recent,
            "emotion": {"label": "negative"},
            "llm_structured": {},
            "relation": {},
        },
    )

    print("\n[关键模块] modules_called =", result.get("modules_called"))
    pers = result.get("personality") or {}
    strat = result.get("strategy") or {}
    cand = result.get("candidates") or []
    mc = strat.get("manipulation_counter") or {}

    print("\n[1] personality:")
    print("   primary_personality =", pers.get("primary_personality"))
    print("   npd_signal_boost    =", pers.get("npd_signal_boost"))
    print("   npd_signal_hits      =", pers.get("npd_signal_hits"))
    pp = str(pers.get("primary_personality", ""))
    check("primary_personality 含「自恋」或「NPD」",
          ("自恋" in pp) or ("NPD" in pp.upper()), f"实际={pp!r}")

    print("\n[2] strategy.manipulation_counter:")
    print("   triggered =", mc.get("triggered"))
    print("   signals   =", mc.get("detected_signals"))
    check("manipulation_counter.triggered == True",
          mc.get("triggered") is True, f"实际={mc.get('triggered')!r}")

    print("\n[3] candidates 反制话术：")
    for i, c in enumerate(cand[:4], 1):
        print(f"   {i}. {c}")
    nonempty = [c for c in cand if str(c).strip()]
    check("candidates ≥3 条非空话术", len(nonempty) >= 3,
          f"实际 {len(nonempty)} 条")

    # 额外：顶层 manipulation（build_counter_manipulation）也应触发
    manip = result.get("manipulation") or {}
    print("\n[4] manipulation（manipulation_coach）triggered =",
          manip.get("triggered"))
    check("manipulation_coach.triggered == True",
          manip.get("triggered") is True,
          f"实际={manip.get('triggered')!r}")

    print("\n" + "=" * 68)
    if FAILS:
        print(f"结果：{len(FAILS)} 项失败 -> {FAILS}")
        return 1
    print("结果：全部 PASS —— NPD 识别链路在生产入口真的走通了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
