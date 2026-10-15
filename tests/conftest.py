# -*- coding: utf-8 -*-
"""pytest 全局配置 —— 网络隔离 + 超时兜底。

背景：测试套件里有不少用例会发起真实外网请求（拉模型目录、探测免费 provider、
下载 chroma embedding 模型等）。当对端不可达 / 被防火墙丢包时，请求会无限阻塞
在 ssl/socket recv，导致整个套件挂死、无法干净退出也打印不出汇总；还有一些
名字是「离线降级」的用例实际偷偷联网。

策略（对标 pytest-socket 的通行做法）：
  1. 默认禁止访问外网，仅允许回环地址（127.0.0.1/::1）；
  2. 确需联网的用例显式标记 ``@pytest.mark.network``，才放行真实网络；
  3. 另给 socket 设宽松默认超时作为兜底。

这样意外联网会立即抛出明确错误（而非挂起几十秒），离线核心套件可快速、
确定性地跑完；真实环境集成测试统一在有网络时手动/CI 单独运行。
"""
from __future__ import annotations

import socket

# 兜底超时（秒），仅防止任何遗漏的无限等待。
socket.setdefaulttimeout(10.0)

# 允许连接的回环目标
_LOCAL_HOSTS = {"127.0.0.1", "::1", "0.0.0.0", "localhost"}

_orig_connect = socket.socket.connect
_orig_connect_ex = getattr(socket.socket, "connect_ex", None)


def _host_of(address):
    if isinstance(address, (tuple, list)) and address:
        return address[0]
    return None


def _guarded_connect(self, address):
    host = _host_of(address)
    if host is None or host in _LOCAL_HOSTS:
        return _orig_connect(self, address)
    raise ConnectionError(
        f"测试默认禁止外网连接 {address}；如确需联网，请给用例加 "
        f"@pytest.mark.network"
    )


def _guarded_connect_ex(self, address):
    host = _host_of(address)
    if host is None or host in _LOCAL_HOSTS:
        if _orig_connect_ex is not None:
            return _orig_connect_ex(self, address)
        return _orig_connect(self, address)
    # 外网：connect_ex 语义是返回错误码而非抛异常
    return 101  # ENETUNREACH


import pytest


# 默认不收集的「真实环境 / 需外网 / 重集成」测试文件（相对 tests 目录）。
# 这些用例保留在仓库中，在具备完整环境（外网、登录态、GPU/外设）时可显式
# 指定路径单独运行，例如： pytest tests/test_rag_real_world.py
collect_ignore = [
    # 真实环境 / 需外网
    "test_rag_real_world.py",
    "test_batch17_wechat_real.py",
    "test_batch16_wechat_40.py",
    "test_wechat_multi_method.py",
    "test_hf_ssh_channel.py",
    "test_web_search_service.py",
    "test_browser_ai_hub.py",
    "test_portable_ollama_lifecycle.py",
    "test_local_ai_scanner.py",
    "test_free_channel_catalog.py",
    "test_zero_cost_wiring.py",
    # 重端到端 / GUI（需显示或拉起重组件）
    "test_ui_e2e_chain.py",
    "test_voice_pipeline_e2e.py",
    "test_auto_strategy_e2e.py",
    "test_immersive_voice_window.py",
    "test_relationship_graph_widget.py",
    "test_analysis_panel.py",
    "test_dynamic_bg.py",
]


@pytest.fixture(autouse=True)
def _network_guard(request, monkeypatch):
    """默认阻断外网；标记 network 的用例放行。"""
    if request.node.get_closest_marker("network"):
        yield
        return
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    if _orig_connect_ex is not None:
        monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    yield


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "network: 需要真实外网连接的测试（离线/CI 默认禁止联网）",
    )
