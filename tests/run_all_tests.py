# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 服务模块单元测试运行器

用法：
    python tests/run_all_tests.py

会自动加载 tests/ 目录下所有 test_*.py 并运行，输出测试结果。
也可直接用 pytest：
    .venv/Scripts/python.exe -m pytest tests/ -v
"""
import os
import sys
import unittest

# 项目根目录（tests/run_all_tests.py → 上一级）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 服务层不依赖 Qt，但保险起见设置 offscreen 平台插件
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    """加载并运行 tests/ 下所有 test_*.py。

    Returns:
        int: 退出码（0=全部通过，1=有失败）。
    """
    # 从 tests 目录开始发现测试
    start_dir = os.path.dirname(os.path.abspath(__file__))
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=start_dir, pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 汇总
    print("\n" + "=" * 60)
    print(f"运行 {result.testsRun} 个测试，"
          f"失败 {len(result.failures)} 个，"
          f"错误 {len(result.errors)} 个，"
          f"跳过 {len(result.skipped)} 个")
    if result.failures:
        print("\n--- 失败明细 ---")
        for test, tb in result.failures:
            print(f"[FAIL] {test}")
    if result.errors:
        print("\n--- 错误明细 ---")
        for test, tb in result.errors:
            print(f"[ERROR] {test}")

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
