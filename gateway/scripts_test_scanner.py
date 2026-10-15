# -*- coding: utf-8 -*-
"""验证 free_scanner 真能从 OpenRouter 拉免费模型。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.free_scanner import FreeModelScanner


async def main():
    scanner = FreeModelScanner(cache_path="data/free_models.json", api_key="")
    report = await scanner.scan(force=True)
    print(f"扫描时间: {report.checked_at}")
    print(f"免费模型总数: {len(report.all_free_ids)}")
    print(f"\n=== 合格池 (eligible: 免费+tools+>=100B) ===")
    for m in report.eligible:
        print(f"  ✓ {m.model_id:50s} ctx={m.context_length:>8} params={m.total_parameters}B")
    print(f"\n=== 待验证池 (needs_verification: 免费+tools+参数未知) ===")
    for m in report.needs_verification[:10]:
        print(f"  ? {m.model_id:50s} ctx={m.context_length:>8}")
    print(f"\n=== 不合格 (rejected) 数量: {len(report.rejected)} ===")

    # 测试 fail-closed 解析
    print("\n=== fail-closed 解析测试 ===")
    for test_id in ["openai/gpt-4o", "google/gemma-4-31b-it", "nonexistent/model"]:
        try:
            resolved, warn = scanner.resolve_free_model(test_id)
            print(f"  输入 '{test_id}' -> '{resolved}'")
            if warn:
                print(f"    警告: {warn}")
        except ValueError as e:
            print(f"  输入 '{test_id}' -> 拒绝: {e}")

if __name__ == "__main__":
    asyncio.run(main())
