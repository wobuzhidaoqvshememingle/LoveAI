# -*- coding: utf-8 -*-
"""
文档/测试一致性审计脚本（只读，不改业务代码）
输出：JSON 结果到 stdout + docs/_audit_doc_test_raw.json
"""
import ast, os, re, json, sys, random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"
TESTS = ROOT / "tests"
DOCS = ROOT / "docs"

random.seed(42)

def is_real_py(p: Path) -> bool:
    name = p.name
    if name.endswith(".py.bak") or ".bak" in name:
        return False
    if not name.endswith(".py"):
        return False
    if name == "__init__.py":
        return False
    return True

# ---------- 1. Services inventory ----------
svc_files = [p for p in SERVICES.rglob("*.py") if is_real_py(p)]

svc_modules = {}      # relpath -> {classes:[], funcs:[], has_docstring_module:bool, loc:int}
all_public_classes = {}  # classname -> relpath
all_public_funcs = {}    # modname.funcname -> relpath

for f in svc_files:
    rel = f.relative_to(ROOT).as_posix()
    try:
        src = f.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        continue
    loc = src.count("\n") + 1
    try:
        tree = ast.parse(src)
    except Exception:
        continue
    classes, funcs = [], []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            classes.append(node.name)
            all_public_classes.setdefault(node.name, rel)
            # public methods inside class
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and not sub.name.startswith("_"):
                    funcs.append(f"{node.name}.{sub.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            mod = f.stem
            funcs.append(f"{mod}.{node.name}")
    svc_modules[rel] = {"classes": classes, "funcs": funcs, "loc": loc}
    for fn in funcs:
        all_public_funcs.setdefault(fn, rel)

# ---------- 2. Tests inventory ----------
test_files = [p for p in TESTS.rglob("*.py") if p.name.startswith("test_")]
test_func_count = 0
test_file_names = []
test_text_blob = ""       # 所有测试文件拼起来，用于 grep
test_imported_modules = set()

for tf in test_files:
    test_file_names.append(tf.name)
    try:
        src = tf.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    test_text_blob += "\n" + src
    test_func_count += len(re.findall(r"^\s*def\s+test_", src, re.M))
    # 提取 from services.xxx import / import services.xxx
    for m in re.findall(r"(?:from|import)\s+services\.([\w\.]+)", src):
        test_imported_modules.add(m.split(".")[0])
    for m in re.findall(r"(?:from|import)\s+(\w+)\s+import", src):
        # 粗筛：形如 from affection_engine import
        pass

# 额外：从 services 顶层模块名反查
svc_top_modules = set()
for rel in svc_modules:
    parts = rel.split("/")
    if parts[0] == "services":
        svc_top_modules.add(parts[1].replace(".py",""))

# 用测试 blob 直接搜顶层模块名出现
modules_touched_by_tests = set()
for mod in svc_top_modules:
    if re.search(rf"\b{re.escape(mod)}\b", test_text_blob):
        modules_touched_by_tests.add(mod)

# 模块级覆盖率
total_svc_mods = len(svc_top_modules)
svc_mods_with_test = modules_touched_by_tests & svc_top_modules
svc_mods_without_test = sorted(svc_top_modules - modules_touched_by_tests)

# 函数/类级粗覆盖率：在测试 blob 里搜名字
funcs_covered = 0
funcs_uncovered = []
for fn, rel in all_public_funcs.items():
    # fn 形如 "affection_engine.compute_score" 或 "Foo.bar"
    leaf = fn.split(".")[-1]
    if re.search(rf"\b{re.escape(leaf)}\b", test_text_blob):
        funcs_covered += 1
    else:
        funcs_uncovered.append(fn)

classes_covered = 0
classes_uncovered = []
for cn, rel in all_public_classes.items():
    if re.search(rf"\b{re.escape(cn)}\b", test_text_blob):
        classes_covered += 1
    else:
        classes_uncovered.append(cn)

# 随机抽样 10 个公共函数
sampled = random.sample(sorted(all_public_funcs.keys()), min(10, len(all_public_funcs)))
sample_result = []
for fn in sampled:
    leaf = fn.split(".")[-1]
    hit = bool(re.search(rf"\b{re.escape(leaf)}\b", test_text_blob))
    sample_result.append({"func": fn, "covered_by_test_blob": hit})

# ---------- 3. Doc -> code 核对 ----------
# 选核心文档：全需求补完报告、功能需求文档、工程规范、统一实施总纲 v3.1
core_docs = [
    DOCS / "全需求补完报告-20261010.md",
    DOCS / "功能需求文档.md",
    DOCS / "工程规范-全能助手标准.md",
    DOCS / "LoveAI-统一实施总纲-v3.1-加固补丁.md",
    DOCS / "LoveAI-统一实施总纲-v3.0.md",
]

# 收集所有 services 顶层模块名 + 公开类名，作为"代码里真实存在的符号表"
code_symbols = set(svc_top_modules) | set(all_public_classes.keys())

# 从文档中抽取被反引号包裹的、像 Python 模块/类名的 token（snake_case 或 CamelCase，长度>=4，含下划线或驼峰）
doc_text_blob = {}
for d in core_docs:
    if d.exists():
        try:
            doc_text_blob[d.name] = d.read_text(encoding="utf-8", errors="replace")
        except Exception:
            doc_text_blob[d.name] = ""

token_re = re.compile(r"`([A-Za-z][A-Za-z0-9_]{3,})`")
doc_claimed_tokens = {}   # token -> [docnames]
for name, txt in doc_text_blob.items():
    for tok in token_re.findall(txt):
        # 过滤掉显然不是符号的
        if tok.lower() in ("true","false","none","null","todo","fixme","python","windows","linux","https","http"):
            continue
        doc_claimed_tokens.setdefault(tok, set()).add(name)

claimed_hits, claimed_miss = [], []
for tok, docs in doc_claimed_tokens.items():
    # 命中条件：在 code_symbols 里，或在 services/ 源码里出现
    if tok in code_symbols:
        continue
    # 在 services/ 源码 blob 里搜
    found = False
    for mod in svc_top_modules:
        pass
    # 直接扫一次 services 源码 blob（下面构造）
    claimed_miss.append({"token": tok, "in_docs": sorted(docs)})

# 构造 services 源码 blob 用于二次核对
svc_src_blob = ""
for f in svc_files:
    try:
        svc_src_blob += "\n" + f.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 重新分类：claimed_miss 里再看是否真在 services 源码里出现
real_miss, actually_in_code = [], []
for item in claimed_miss:
    tok = item["token"]
    if re.search(rf"\b{re.escape(tok)}\b", svc_src_blob):
        actually_in_code.append(item)
    else:
        real_miss.append(item)

# 隐藏功能：services 里有但核心文档从未提过的模块/类
doc_blob_all = "\n".join(doc_text_blob.values())
hidden_modules = sorted([m for m in svc_top_modules if not re.search(rf"\b{re.escape(m)}\b", doc_blob_all)])
hidden_classes = sorted([c for c in all_public_classes if not re.search(rf"\b{re.escape(c)}\b", doc_blob_all)])

# ---------- 4. 工程规范抽查 ----------
# 工程规范里常见规则：bare except / print( / 函数无 docstring / 行过长
bare_except = 0
print_calls = 0
long_lines = 0
total_lines = 0
for f in svc_files:
    try:
        src = f.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    bare_except += len(re.findall(r"^\s*except\s*:", src, re.M))
    print_calls += len(re.findall(r"^\s*print\(", src, re.M))
    for ln in src.splitlines():
        total_lines += 1
        if len(ln) > 120:
            long_lines += 1

result = {
    "services": {
        "file_count": len(svc_files),
        "top_level_modules": total_svc_mods,
        "public_classes": len(all_public_classes),
        "public_funcs_including_methods": len(all_public_funcs),
        "total_loc": sum(v["loc"] for v in svc_modules.values()),
    },
    "tests": {
        "test_file_count": len(test_files),
        "test_function_count": test_func_count,
    },
    "coverage": {
        "svc_modules_with_any_test_reference": len(svc_mods_with_test),
        "svc_modules_without_test_reference": svc_mods_without_test,
        "public_funcs_covered": funcs_covered,
        "public_funcs_uncovered": len(funcs_uncovered),
        "public_classes_covered": classes_covered,
        "public_classes_uncovered": classes_uncovered[:50],
        "sample_10": sample_result,
    },
    "doc_vs_code": {
        "docs_checked": list(doc_text_blob.keys()),
        "doc_claimed_symbols_total": len(doc_claimed_tokens),
        "doc_claimed_actually_in_code": len(actually_in_code),
        "doc_claimed_not_found_in_code": len(real_miss),
        "doc_claimed_not_found_examples": real_miss[:40],
        "hidden_modules_in_code_not_in_core_docs": hidden_modules,
        "hidden_classes_in_code_not_in_core_docs_sample": hidden_classes[:60],
    },
    "engineering_rules": {
        "bare_except_count": bare_except,
        "print_call_count": print_calls,
        "long_lines_over_120": long_lines,
        "total_svc_lines": total_lines,
    },
}

out = ROOT / "docs" / "_audit_doc_test_raw.json"
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
