# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 随包便携 Ollama 生命周期测试（离线确定性）

覆盖：
  - 端口已在服务/无随包 exe → 不重复拉起；
  - aboutToQuit 信号会触发 _stop_portable_ollama（覆盖主窗口 closeEvent 不走 quit() 的路径）；
  - 回收始终走进程树：taskkill /PID <pid> /T /F（杀 ollama.exe 及其派生的 llama-server）；
  - 重复调用 stop 幂等无异常；
  - 后台就绪检测：端口起来后 _on_local_engine_ready、定时器自停（不阻塞启动）；
  - 真实进程树：拉起父进程+sleep 子进程，回收后两者均终止。
"""
import os
import sys
import time
import subprocess
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from app.application import LoveAIApplication  # noqa: E402
import services.runtime_paths as rp  # noqa: E402

_qapp = QApplication.instance() or QApplication([])


def _bare_app():
    app = LoveAIApplication.__new__(LoveAIApplication)
    app._portable_ollama_proc = None
    app._ollama_ready_timer = None
    app._local_engine_ready = False
    return app


def _alive(pid: int) -> bool:
    # 字节级判定，绕开中文 Windows tasklist 的 GBK 解码问题：
    # 不使用 text=True / encoding，直接在原始 stdout 字节流里找 PID 的 ASCII 数字。
    r = subprocess.run(
        ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
        capture_output=True,
    )
    return str(int(pid)).encode("ascii") in (r.stdout or b"")


# --------------------------------------------------------------------------- #
# 拉起行为
# --------------------------------------------------------------------------- #
def test_port_in_use_closed_port_is_false():
    assert LoveAIApplication._port_in_use("127.0.0.1", 59999) is False


def test_no_spawn_when_already_serving(monkeypatch):
    app = _bare_app()
    monkeypatch.setattr(LoveAIApplication, "_port_in_use",
                        staticmethod(lambda h, p: True))
    popen = mock.Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    app._ensure_portable_ollama()
    popen.assert_not_called()


def test_no_spawn_when_no_portable_exe(monkeypatch):
    app = _bare_app()
    monkeypatch.setattr(LoveAIApplication, "_port_in_use",
                        staticmethod(lambda h, p: False))
    monkeypatch.setattr(rp, "is_portable_ollama_available", lambda: False)
    popen = mock.Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    app._ensure_portable_ollama()
    popen.assert_not_called()


# --------------------------------------------------------------------------- #
# 回收：aboutToQuit / 进程树 / 幂等
# --------------------------------------------------------------------------- #
def test_aboutToQuit_signal_triggers_stop(monkeypatch):
    app = _bare_app()
    fake_proc = mock.Mock()
    fake_proc.pid = 4242
    fake_proc.poll.return_value = None
    app._portable_ollama_proc = fake_proc
    run_mock = mock.Mock()
    monkeypatch.setattr(subprocess, "run", run_mock)
    # 与 __init__ 中一致地连接到 aboutToQuit
    _qapp.aboutToQuit.connect(app._stop_portable_ollama)
    _qapp.aboutToQuit.emit()
    fake_proc.terminate.assert_called()
    # 进程树清扫：taskkill /T /F
    assert run_mock.called
    cmd = run_mock.call_args[0][0]
    assert cmd[0] == "taskkill" and "/T" in cmd and "/F" in cmd and "4242" in cmd
    assert app._portable_ollama_proc is None


def test_stop_always_sweeps_process_tree(monkeypatch):
    app = _bare_app()
    fake_proc = mock.Mock()
    fake_proc.pid = 7777
    fake_proc.poll.return_value = None  # terminate 后仍“存活”（孙进程残留）
    app._portable_ollama_proc = fake_proc
    run_mock = mock.Mock()
    monkeypatch.setattr(subprocess, "run", run_mock)
    app._stop_portable_ollama()
    run_mock.assert_called_once()
    cmd = run_mock.call_args[0][0]
    assert "/T" in cmd and "/F" in cmd and "7777" in cmd


def test_stop_idempotent(monkeypatch):
    app = _bare_app()
    fake_proc = mock.Mock()
    fake_proc.pid = 7777
    fake_proc.poll.return_value = None
    app._portable_ollama_proc = fake_proc
    run_mock = mock.Mock()
    monkeypatch.setattr(subprocess, "run", run_mock)
    app._stop_portable_ollama()
    app._stop_portable_ollama()  # 第二次：proc 已 None，直接返回
    assert run_mock.call_count == 1
    assert app._portable_ollama_proc is None


# --------------------------------------------------------------------------- #
# 后台就绪检测（非阻塞）
# --------------------------------------------------------------------------- #
def test_readiness_tick_marks_ready_when_port_up(monkeypatch):
    app = _bare_app()
    app._ollama_ready_attempts = 0
    app._ollama_ready_max = 40
    fake_timer = mock.Mock()
    app._ollama_ready_timer = fake_timer
    monkeypatch.setattr(LoveAIApplication, "_port_in_use",
                        staticmethod(lambda h, p: True))
    app._ollama_ready_tick()
    assert app._local_engine_ready is True
    fake_timer.stop.assert_called_once()
    assert app._ollama_ready_timer is None


def test_readiness_tick_stops_when_process_exited(monkeypatch):
    app = _bare_app()
    app._ollama_ready_attempts = 0
    app._ollama_ready_max = 40
    fake_timer = mock.Mock()
    app._ollama_ready_timer = fake_timer
    monkeypatch.setattr(LoveAIApplication, "_port_in_use",
                        staticmethod(lambda h, p: False))
    exited = mock.Mock()
    exited.poll.return_value = 0
    app._portable_ollama_proc = exited
    app._ollama_ready_tick()
    fake_timer.stop.assert_called_once()
    assert app._ollama_ready_timer is None


# --------------------------------------------------------------------------- #
# 真实进程树终止（离线）
# --------------------------------------------------------------------------- #
def test_real_process_tree_is_killed():
    # 父 python 再派生一个 sleep 子进程，模拟 ollama.exe → llama-server
    code = (
        "import subprocess,sys,time;"
        "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);"
        "print('CHILD='+str(c.pid));sys.stdout.flush();"
        "time.sleep(120)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code],
                             stdout=subprocess.PIPE)
    child_pid = None
    for _ in range(40):
        raw = proc.stdout.readline()
        if not raw:
            time.sleep(0.1)
            continue
        line = raw.decode("ascii", errors="ignore")
        if line.startswith("CHILD="):
            child_pid = int(line.strip().split("=")[1])
            break
    assert child_pid is not None
    time.sleep(0.5)
    assert _alive(child_pid) is True

    app = _bare_app()
    app._portable_ollama_proc = proc
    app._stop_portable_ollama()

    time.sleep(1.5)
    assert proc.poll() is not None     # 父进程已死
    assert _alive(child_pid) is False  # 孙进程被 taskkill /T 一并杀掉
