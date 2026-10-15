# -*- coding: utf-8 -*-
"""补充：时间戳 bak 计数 + script_library 条数 + 全 docs 模块提及统计"""
import re, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

# 1. timestamped .bak violation
bak_files = list((ROOT/"services").rglob("*.bak*")) + list((ROOT/"tests").rglob("*.bak*"))
ts_bak = [p.name for p in bak_files if re.search(r"\.bak[_-]?\d{6,}", p.name)]
plain_bak = [p.name for p in bak_files if p not in ts_bak]

# 2. script_library count
sl = (ROOT/"services/script_library.py").read_text(encoding="utf-8", errors="replace")
# 粗数：以 "xxx": 开头的字符串条数（话术库通常是 dict/list）
script_items = len(re.findall(r'^\s*"[^\n"]{4,}"\s*:', sl, re.M))

# 3. 全 docs/*.md 提及 services 顶层模块次数
docs_md = list((ROOT/"docs").glob("*.md"))
svc_mods = [p.stem for p in (ROOT/"services").glob("*.py") if not p.name.startswith("_")]
mod_mentions = {}
all_doc_blob = ""
for d in docs_md:
    try:
        all_doc_blob += "\n" + d.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
for m in svc_mods:
    n = len(re.findall(rf"\b{re.escape(m)}\b", all_doc_blob))
    mod_mentions[m] = n

never_mentioned = sorted([m for m,n in mod_mentions.items() if n == 0])
rarely_mentioned = sorted([(m,n) for m,n in mod_mentions.items() if 0 < n <= 2], key=lambda x:x[1])

print(json.dumps({
    "bak_total": len(bak_files),
    "timestamped_bak_violations": ts_bak,
    "plain_bak_count": len(plain_bak),
    "script_library_items_rough": script_items,
    "docs_total_md_files": len(docs_md),
    "svc_modules_never_mentioned_in_any_doc": never_mentioned,
    "svc_modules_mentioned_1_2_times": rarely_mentioned,
}, ensure_ascii=False, indent=2))
