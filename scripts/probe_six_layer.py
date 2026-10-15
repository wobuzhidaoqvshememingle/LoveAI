# -*- coding: utf-8 -*-
"""
探针：六层人格真接入 analyze()（问题7）

之前 six_layer_persona 模块写好了，但 persona_analyzer.analyze() 从不调它（孤岛）。
本探针：
  1) FakeDB 喂真实形状的聊天记录
  2) 从生产入口 PersonaAnalyzer(db).analyze() 真跑
  3) 断言返回 dict 有 six_layer 字段，且 L1~L6 每层 evidence_count >= 1

跑法：.venv\\Scripts\\python.exe scripts\\probe_six_layer.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.persona_analyzer import PersonaAnalyzer  # noqa: E402

FAILS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}{(' | ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


# 真实形状的聊天记录（对方=received，我=sent）
CHAT_ROWS = [
    {"direction": "received", "content": "你今天在干嘛呀？", "msg_type": "text", "created_at": "2026-10-01 09:00:00"},
    {"direction": "sent", "content": "刚忙完工作，你呢？", "msg_type": "text", "created_at": "2026-10-01 09:05:00"},
    {"direction": "received", "content": "我周三晚上要去上瑜伽课", "msg_type": "text", "created_at": "2026-10-01 09:06:00"},
    {"direction": "received", "content": "别临时约我，我不喜欢计划被打乱", "msg_type": "text", "created_at": "2026-10-01 09:10:00"},
    {"direction": "sent", "content": "好的，那周末再约", "msg_type": "text", "created_at": "2026-10-01 09:12:00"},
    {"direction": "received", "content": "嗯再说吧", "msg_type": "text", "created_at": "2026-10-02 10:00:00"},
    {"direction": "sent", "content": "上次答应你的电影票我买好了", "msg_type": "text", "created_at": "2026-10-03 19:00:00"},
    {"direction": "received", "content": "我忘了这回事，下次吧", "msg_type": "text", "created_at": "2026-10-03 20:00:00"},
]


class FakeDB:
    """按 SQL 关键字返回不同结果，对齐 analyze() 的查询形状。"""

    def fetch_all(self, sql, params=None):
        if "chat_messages" in sql and "direction" in sql:
            return CHAT_ROWS
        # 其它统计查询一律空（各 _analyze_* 自带空表兜底）
        return []

    def fetch_one(self, sql, params=None):
        return None


def main() -> int:
    print("=" * 68)
    print("探针：六层人格接入 analyze()（问题7）")
    print("=" * 68)

    analyzer = PersonaAnalyzer(db=FakeDB())
    profile = analyzer.analyze("probe_person", person_info={})

    print("\n[1] analyze() 返回 dict 含 six_layer 字段：")
    six = profile.get("six_layer")
    check("profile 有 six_layer 键", isinstance(six, dict),
          f"type={type(six).__name__}")

    if isinstance(six, dict):
        per = six.get("per_layer") or {}
        print("\n[2] 每层证据数：")
        all_nonempty = True
        for layer in ("L1_SURFACE", "L2_BEHAVIOR", "L3_EMOTION",
                      "L4_ATTACHMENT", "L5_VALUE", "L6_CORE"):
            info = per.get(layer) or {}
            n = info.get("evidence_count", 0)
            status = info.get("status", "?")
            quote = (info.get("top_quotes") or [""])[0]
            print(f"   {layer:14s} n={n} status={status} | {quote[:40]}")
            if n < 1:
                all_nonempty = False
        check("六层每层 evidence_count >= 1（不再有空层孤岛）",
              all_nonempty)
        print(f"\n   maturity={six.get('maturity')} "
              f"label={six.get('maturity_label')} "
              f"pending={six.get('pending_layers')}")

    print("\n" + "=" * 68)
    if FAILS:
        print(f"结果：{len(FAILS)} 项失败 -> {FAILS}")
        return 1
    print("结果：全部 PASS —— 六层人格在主程序真接通，每层有证据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
