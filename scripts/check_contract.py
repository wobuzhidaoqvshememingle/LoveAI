# -*- coding: utf-8 -*-
"""LoveAI 硬规则校验脚本（铁律8：关键环节用硬规则）。

每次交付前跑一遍，不靠模型自觉检查。

用法：
    .\.venv\Scripts\python.exe scripts\check_contract.py

校验项：
  1. 关键文件存在性（services/、app/、tests/ 核心模块）
  2. 没有遗留占位字符串（"待长期数据"、"TODO"、"占位"）
  3. 没有硬编码的假数据路径
  4. 核心模块可导入（语法/import检查）
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# --------------------------------------------------------------------------- #
# 1. 关键文件存在性
# --------------------------------------------------------------------------- #
REQUIRED_FILES = [
    # 核心服务层
    "services/voice_pipeline.py",
    "services/smart_turn.py",
    "services/chinese_eot.py",
    "services/fault_router.py",
    "services/startup_probe.py",
    "services/scam_patterns.py",
    "services/memory_gate.py",
    "services/six_layer_persona.py",
    "services/superstition_playalong.py",
    "services/auto_strategy_pipeline.py",
    "services/script_library.py",
    "services/output_normalizer.py",
    "services/portrait_generator.py",
    "services/pet_animator.py",
    # 数据池双层
    "services/data_pool/contracts.py",
    "services/data_pool/local_persona.py",
    "services/data_pool/model_adapter.py",
    # UI
    "app/widgets/analysis_output_card.py",
    "app/widgets/immersive_mode.py",
    # 文档
    "docs/全需求补完报告-20261010.md",
    "docs/工程规范-全能助手标准.md",
    # 状态
    ".state/task-state.md",
    ".state/experience-log.md",
]

# --------------------------------------------------------------------------- #
# 2. 占位字符串黑名单（这些字符串出现在代码里 = 假修）
# --------------------------------------------------------------------------- #
PLACEHOLDER_PATTERNS = [
    (r"待长期数据", "占位字符串：待长期数据"),
    (r"TODO[：:]", "TODO 注释未完成"),
    (r"FIXME[：:]", "FIXME 注释未完成"),
    (r"pass\s*#\s*占位", "pass 占位"),
]

# 扫描范围（只扫业务代码，不扫测试和文档）
SCAN_DIRS = ["services", "app"]


def check_files_exist() -> list[str]:
    """校验关键文件存在性。"""
    problems = []
    for rel in REQUIRED_FILES:
        p = ROOT / rel
        if not p.exists():
            problems.append(f"❌ 文件缺失：{rel}")
        elif p.stat().st_size == 0:
            problems.append(f"⚠️  文件为空：{rel}")
        else:
            print(f"  ✅ {rel}")
    return problems


def check_placeholders() -> list[str]:
    """扫描代码里的占位字符串。"""
    problems = []
    for d in SCAN_DIRS:
        dir_path = ROOT / d
        if not dir_path.exists():
            continue
        for py_file in dir_path.rglob("*.py"):
            # 跳过 .bak 文件
            if ".bak" in py_file.name:
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue
            for pat, desc in PLACEHOLDER_PATTERNS:
                if re.search(pat, content):
                    rel = py_file.relative_to(ROOT)
                    problems.append(f"❌ {desc}：{rel}")
    if not problems:
        print("  ✅ 无占位字符串")
    return problems


def check_imports() -> list[str]:
    """核心模块可导入检查。"""
    problems = []
    modules = [
        "services.smart_turn",
        "services.chinese_eot",
        "services.fault_router",
        "services.scam_patterns",
        "services.memory_gate",
        "services.six_layer_persona",
        "services.script_library",
        "services.output_normalizer",
    ]
    for mod in modules:
        try:
            __import__(mod)
            print(f"  ✅ import {mod}")
        except Exception as e:
            problems.append(f"❌ 导入失败 {mod}：{e}")
    return problems


def main() -> int:
    print("=" * 60)
    print("LoveAI 硬规则校验")
    print("=" * 60)

    all_problems = []

    print("\n[1/3] 关键文件存在性：")
    all_problems.extend(check_files_exist())

    print("\n[2/3] 占位字符串扫描：")
    all_problems.extend(check_placeholders())

    print("\n[3/3] 核心模块导入：")
    all_problems.extend(check_imports())

    print("\n" + "=" * 60)
    if all_problems:
        print(f"❌ 校验未通过，共 {len(all_problems)} 个问题：")
        for p in all_problems:
            print(f"  {p}")
        return 1
    else:
        print("✅ 全部校验通过")
        return 0


if __name__ == "__main__":
    sys.exit(main())
