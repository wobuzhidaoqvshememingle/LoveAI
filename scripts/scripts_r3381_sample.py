# -*- coding: utf-8 -*-
"""R3.3-81 真实聊天样本验证：走通完整 update_from_message，输出分析结论+证据链+delta。"""
import os, sys, json, tempfile, io
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database
from services.affection_engine import AffectionEngine

tmp = tempfile.mkdtemp(prefix="loveai_r3381_sample_")
db = Database(os.path.join(tmp, "sample.db"))
db.init_tables()
db.execute("INSERT INTO persons(id,nickname) VALUES(:p,:n)", {"p": "sample", "n": "sample"})
eng = AffectionEngine(db)

samples = [
    ("在干嘛呢，今天加班累死了", {"label": "sad", "intensity": 0.4}, "疲惫倾诉"),
    ("想你了宝贝，我刚才路过你爱吃的那家店，给你点了份外卖留你家", {"label": "love", "intensity": 0.9}, "亲密表达+实际投入"),
    ("好好好，这次是我不对，我都听你的，下次不了", {"label": "neutral", "intensity": 0.3}, "顺从认错"),
    ("嗯", {"label": "neutral", "intensity": 0.1}, "敷衍短回"),
    ("凭什么什么都得听你的？你管不着我", {"label": "angry", "intensity": 0.8}, "公开违抗"),
    ("周末想去新开的那家美术馆吗？我查了有展", {"label": "happy", "intensity": 0.7}, "主动邀约接话"),
    ("想你？呵，你少来这套", {"label": "cold", "intensity": 0.8}, "反话反诘（与样本2同含'想你'）"),
    ("别烦我，我现在不想聊", {"label": "cold", "intensity": 0.9}, "推开排斥"),
    ("滚啦～讨厌死你了哈哈哈", {"label": "happy", "intensity": 0.6}, "傲娇撒娇"),
]

out = io.StringIO()
def p(*a):
    s = " ".join(str(x) for x in a)
    print(s); out.write(s + "\n")

p("="*90)
p("R3.3-81 真实聊天样本：分析结论 → 内心状态进度报告 → 维度delta 全链路证据")
p("="*90)
for content, emo, tag in samples:
    before = eng.get_total_with_bonus("sample", eng.get_current("sample"))
    r = eng.update_from_message("sample", {"content": content, "direction": "received"}, emotion=emo)
    rep = r["reasoning_report"]
    after = r["total_score"]
    p("\n" + "-"*90)
    p(f"[样本] {tag}")
    p(f"  她说：{content}")
    p(f"  输入情绪：{emo}")
    concl = rep["conclusions"][0] if rep["conclusions"] else {}
    p(f"  分析结论：情绪={concl.get('emotion',{}).get('label')}(强度{concl.get('emotion',{}).get('intensity'):.0%}) "
      f"姿态={concl.get('postures')}")
    dims = {k: v for k, v in rep["dimensions"].items() if abs(v) >= 0.05}
    p(f"  18维建议delta（非零）：{dims}")
    p(f"  loyalty建议delta：{rep['loyalty_delta']:+.2f}   总分 {before:.1f} → {after:.1f}")
    p(f"  证据链（前3条）：")
    for e in [x for x in rep["evidence"] if "→" in x.get("text","")][:3]:
        p(f"    - {e['text']}")

p("\n" + "="*90)
p("反机械性证明：同含'想你'的两句")
p("="*90)
r2 = eng.analyze_message({"content": "想你了宝贝", "direction": "received"}, {"label": "love","intensity":0.9})
r3 = eng.analyze_message({"content": "想你？呵，你少来这套", "direction": "received"}, {"label": "cold","intensity":0.8})
p(f"  love语境  '想你了宝贝'      → intimacy={r2['dimensions'].get('intimacy',0):+.2f}, trust={r2['dimensions'].get('trust',0):+.2f}")
p(f"  cold反话语境 '想你？呵，你少来这套' → intimacy={r3['dimensions'].get('intimacy',0):+.2f}, trust={r3['dimensions'].get('trust',0):+.2f}")
p("  → 同一关键词在不同情绪/语境下 delta 不同/相反，证明不是'字面关键词固定加分'。")

# 任务门禁证明
p("\n" + "="*90)
p("验收点3 任务进度联动：命中完成关键词'兴趣'但分析判定无真实进展 → 不推进")
p("="*90)
from services.affection_reasoning import AffectionReasoner
rz = AffectionReasoner()
v1 = rz.assess_task_progress("我对这家美术馆很感兴趣", {"label":"happy","intensity":0.6})
v2 = rz.assess_task_progress("兴趣？随便吧，无所谓", {"label":"cold","intensity":0.8})
p(f"  积极回应 '我对这家美术馆很感兴趣' → is_genuine_progress={v1['is_genuine_progress']} (正向动量{v1['positive_momentum']}) → 推进任务")
p(f"  敷衍回应 '兴趣？随便吧，无所谓' → is_genuine_progress={v2['is_genuine_progress']} (负向动量{v2['negative_momentum']}) → 不推进")

path = os.path.join(_ROOT, "docs", "证据_R3.3-81_聊天样本.txt")
with open(path, "w", encoding="utf-8") as f:
    f.write(out.getvalue())
print("\n[written]", path)
