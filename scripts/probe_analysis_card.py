# -*- coding: utf-8 -*-
"""
探针：六字段面板数据平铺（问题3）

生产路径 service_facade 把 orchestrate() 返回整个塞进 data["orchestration"]，
卡片却只读顶层键。本探针：
  1) 构造一份「orchestrator 真实输出格式」的嵌套 dict（故意不放顶层 strategy/risk）
  2) 调生产路径的 set_result()（内部已接 _flatten_result）
  3) 读回每个 label，验证显示的是真实内容而不是「—」

跑法：.venv\\Scripts\\python.exe scripts\\probe_analysis_card.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.widgets.analysis_output_card import AnalysisOutputCard  # noqa: E402

# offscreen 实例化 QWidget 前必须先有 QApplication
from PySide6.QtWidgets import QApplication  # noqa: E402
_APP = QApplication.instance() or QApplication([])

FAILS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}{(' | ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def main() -> int:
    print("=" * 68)
    print("探针：六字段面板平铺（问题3）——嵌套 orchestration 必须显示真实内容")
    print("=" * 68)

    # 一份 orchestrate() 真实格式的嵌套输出（模拟 NPD 场景）
    orchestration = {
        "personality": {"primary_personality": "自恋型(NPD)", "primary_label": "自恋倾向"},
        "strategy": {
            "recommended": ["不接贬低：用'我有我的感受'划界", "降频：减少主动联系"],
            "manipulation_counter": {"triggered": True},
        },
        "risk": "检测到煤气灯/贬低模式，已生成非对抗反制步骤",
        "prediction": {"emotion": "defensive", "pickup": False,
                       "summary": "对方会继续贬低并试图让你内耗"},
        "candidates": [
            "我不接受'你太敏感'这种说法，我们先停一下。",
            "你上周明明说过……我不是翻旧账，只是不希望说完就当没发生。",
            "这件事我们各有各的责任，我不想一个人背。",
        ],
        "risk_warnings": ["煤气灯贬低"],
    }

    # 生产 data dict：顶层只有 intent/emotion，真实分析数据全在嵌套 orchestration
    data = {
        "intent": {"summary": "对方在贬低你并试图让你自责"},
        "emotion": {"label": "negative"},
        "orchestration": orchestration,
    }

    card = AnalysisOutputCard()
    card.set_result(data)

    print("\n[读回卡片六字段显示文本]")
    fields = ["intent", "strategy", "candidates", "risk", "prediction"]
    for k in fields:
        txt = card.field_text(k)
        preview = txt.replace("\n", " ⏎ ")[:70]
        print(f"   {k:11s} = {preview}")

    print("\n[断言] 关键四字段不能是「—」：")
    check("策略 显示真实内容", card.field_text("strategy") != "—",
          card.field_text("strategy")[:50])
    check("风险 显示真实内容", card.field_text("risk") != "—",
          card.field_text("risk")[:50])
    check("预测 显示真实内容", card.field_text("prediction") != "—",
          card.field_text("prediction")[:50])
    check("候选话术 显示真实内容（非—）", card.field_text("candidates") != "—",
          card.field_text("candidates").replace(chr(10), " / ")[:60])
    check("意图 显示真实内容", card.field_text("intent") != "—",
          card.field_text("intent")[:50])

    print("\n" + "=" * 68)
    if FAILS:
        print(f"结果：{len(FAILS)} 项失败 -> {FAILS}")
        return 1
    print("结果：全部 PASS —— 嵌套 orchestration 已平铺到顶层，面板不再显示「—」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
