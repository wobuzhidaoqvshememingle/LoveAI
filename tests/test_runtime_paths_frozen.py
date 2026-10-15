# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - runtime_paths 冻结态/开发态路径解析测试（离线确定性）

覆盖 P1 根因修复：冻结态 db/日志/数据应解析到安装目录可写 data/logs，
_internal 仅作只读包内资源根；开发态保持原落点不变。
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import services.runtime_paths as rp  # noqa: E402


def test_dev_mode_paths_unchanged():
    assert rp.is_frozen() is False
    root = _PROJECT_ROOT
    assert os.path.abspath(rp.app_root()) == os.path.abspath(root)
    assert os.path.abspath(rp.install_dir()) == os.path.abspath(root)
    assert os.path.abspath(rp.writable_data_dir()) == os.path.abspath(
        os.path.join(root, "data"))
    assert os.path.abspath(rp.writable_logs_dir()) == os.path.abspath(
        os.path.join(root, "logs"))


def test_frozen_paths_resolve_to_writable_install_dir(tmp_path, monkeypatch):
    internal = tmp_path / "_internal"
    internal.mkdir()
    monkeypatch.setattr(rp, "_FROZEN", True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "LoveAI.exe"),
                        raising=False)

    assert rp.is_frozen() is True
    # 包内只读资源根 = _internal
    assert os.path.abspath(rp.app_root()) == os.path.abspath(str(internal))
    # 可写安装根 = exe 同级（非 _internal）
    assert os.path.abspath(rp.install_dir()) == os.path.abspath(str(tmp_path))
    # 可写数据 / 日志落在安装目录
    assert os.path.abspath(rp.writable_data_dir()) == os.path.abspath(
        str(tmp_path / "data"))
    assert os.path.abspath(rp.writable_logs_dir()) == os.path.abspath(
        str(tmp_path / "logs"))
