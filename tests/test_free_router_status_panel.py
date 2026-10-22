# -*- coding: utf-8 -*-
"""设置面板「免费通道路由状态（只读）」新增部分的单测与冒烟。

覆盖范围只限本代理在 app/widgets/settings_dialog.py 内新增的内容：
  * 纯渲染函数 render_free_router_status(snapshot, error)（不依赖 Qt 事件循环）
  * 防御性取数链 SettingsDialog._resolve_free_router / _fetch_free_router_snapshot
  * offscreen 下真实构造 SettingsDialog 并把假快照渲染进只读区

运行：
  pytest -q -s tests/test_free_router_status_panel.py
  python tests/test_free_router_status_panel.py        # 内置 __main__ 冒烟：打印原始输出
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.widgets.settings_dialog import (  # noqa: E402
    FREE_ROUTER_FOOTER,
    FREE_ROUTER_UNAVAILABLE_HINT,
    SettingsDialog,
    render_free_router_status,
)

# ---------------------------------------------------------------- #
# 假快照：2 个通道 —— ①正常可用（有成功/失败与用量）②被惩罚且正在冷却
# ---------------------------------------------------------------- #
FAKE_SNAPSHOT = {
    "generated_at": 1750000000.0,
    "last_routed_via": "openrouter:deepseek/deepseek-chat-v3:free",
    "sticky_sessions": 2,
    "channels": [
        {
            "provider": "openrouter",
            "model": "deepseek/deepseek-chat-v3:free",
            "base_priority": 100,
            "penalty": 0.0,
            "effective_score": 100.0,
            "eligible": True,
            "cooldown_until": 0.0,
            "remaining_sec": 0.0,
            "cooldown_level": 0,
            "limits": {"rpm": 20, "rpd": 200, "tpm": 60000, "tpd": 500000,
                       "rpm_used": 3, "rpd_used": 12, "tpm_used": 1200, "tpd_used": 5000},
            "ok": 8, "fail": 1, "latency_ms": 812.4,
        },
        {
            "provider": "nvidia",
            "model": "meta/llama-3.1-8b-instruct",
            "base_priority": 90,
            "penalty": 15.5,
            "effective_score": 74.5,
            "eligible": False,
            "cooldown_until": 1750000125.0,
            "remaining_sec": 125.0,
            "cooldown_level": 2,
            "limits": {"rpm": 20, "rpd": 200, "tpm": 60000, "tpd": 500000,
                       "rpm_used": 20, "rpd_used": 188, "tpm_used": 59000, "tpd_used": 470000},
            "ok": 2, "fail": 4, "latency_ms": 2400.0,
        },
    ],
    "mode": {"free_only": True, "fail_closed": True},
}


class _FakeRouter:
    def __init__(self, snapshot=None, exc=None, drop_method=False):
        self._snapshot = snapshot
        self._exc = exc
        if not drop_method:
            self.routing_snapshot = self._routing_snapshot

    def _routing_snapshot(self):
        if self._exc is not None:
            raise self._exc
        return self._snapshot


class _FakeLLM:
    def __init__(self, router=None):
        self._free_router = router


class _FakeFacade:
    def __init__(self, llm=None):
        self.services = {"llm": llm}


class _FakeConfig:
    def get(self, key, default=None):
        return default

    def set(self, key, value):
        return None


class _BareDialog:
    """仅承载 SettingsDialog 的取数方法（同一函数对象），不构造 QObject。

    Shiboken 的 QObject 子类不允许 object.__new__，故用纯 Python 宿主对象调用
    真实方法本身；Qt 路径由 offscreen 用例真实构造 SettingsDialog 覆盖。
    """

    _resolve_free_router = SettingsDialog._resolve_free_router
    _fetch_free_router_snapshot = SettingsDialog._fetch_free_router_snapshot

    def __init__(self, facade=None):
        self._facade = facade


def _bare_dialog(facade=None):
    """不经 Qt 构造，只取 SettingsDialog 的取数方法（无 QApplication 依赖）。"""
    return _BareDialog(facade)


# ---------------------------------------------------------------- #
# 1) 纯渲染函数
# ---------------------------------------------------------------- #
def test_render_fake_snapshot_two_channels():
    view = render_free_router_status(FAKE_SNAPSHOT)

    print("\n===== render_free_router_status(FAKE_SNAPSHOT) 原始输出 =====")
    print("state_text:", view["state_text"])
    for line in view["lines"]:
        print(line)
    print("footer:", view["footer"])
    print("rows:", view["rows"])
    print("states:", view["states"])
    print("available=%s eligible_count=%s cooling_count=%s"
          % (view["available"], view["eligible_count"], view["cooling_count"]))
    print("===== 原始输出结束 =====\n")

    assert view["available"] is True
    assert view["reason"] == ""
    assert view["channel_count"] == 2
    assert view["eligible_count"] == 1
    assert view["cooling_count"] == 1
    assert view["states"] == ["可用", "冷却中（2 级）"]
    assert view["state_text"] == "已刷新：共 2 条候选通道，1 条可用，1 条冷却中"

    # 顶部三要素
    assert any("本次实际由：openrouter:deepseek/deepseek-chat-v3:free" in x for x in view["head"])
    assert any("活跃 sticky 会话：2" in x for x in view["head"])
    assert any(x.startswith("快照时间：") for x in view["head"])

    # 逐通道行（14 列，字段与冻结接口一一对应）
    row0, row1 = view["rows"]
    assert len(view["columns"]) == 14 and len(row0) == 14
    assert row0[0] == "openrouter:deepseek/deepseek-chat-v3:free"
    assert row0[1] == "100" and row0[2] == "0.0" and row0[3] == "100.0"
    assert row0[4] == "是" and row0[5] == "0" and row0[6] == "0"
    assert row0[7] == "3/20" and row0[8] == "12/200"
    assert row0[9] == "1200/60000" and row0[10] == "5000/500000"
    assert row0[11] == "8" and row0[12] == "1" and row0[13] == "812.4"
    assert row1[0] == "nvidia:meta/llama-3.1-8b-instruct"
    assert row1[2] == "15.5" and row1[3] == "74.5"
    assert row1[4] == "否" and row1[5] == "125" and row1[6] == "2"

    # 底部 fail-closed 说明
    assert view["footer"].startswith("说明：免费池为空时报空池，绝不回退到付费模型（fail-closed）")
    assert view["footer"] == FREE_ROUTER_FOOTER


def test_render_unavailable_inputs():
    for bad, tag in ((None, "None"), ("not-a-dict", "str"), (123, "int"), ([], "list")):
        view = render_free_router_status(bad)
        assert view["available"] is False, tag
        assert view["state_text"] == FREE_ROUTER_UNAVAILABLE_HINT, tag
        assert view["rows"] == [] and view["channel_count"] == 0, tag
        assert view["lines"], tag
        assert view["footer"] == FREE_ROUTER_FOOTER, tag
    print("\n[不可用渲染] None →", render_free_router_status(None)["lines"])
    print("[不可用渲染] error 传入 →", render_free_router_status(None, "facade=None")["lines"])


def test_render_error_reason_wins():
    view = render_free_router_status(None, "FreeModelRouter 未提供 routing_snapshot()（内核未就绪）")
    assert view["reason"] == "FreeModelRouter 未提供 routing_snapshot()（内核未就绪）"
    assert "内核未就绪" in view["lines"][0]


def test_render_empty_pool():
    view = render_free_router_status({"generated_at": 0.0, "last_routed_via": "",
                                      "sticky_sessions": 0, "channels": [],
                                      "mode": {"free_only": True, "fail_closed": True}})
    assert view["available"] is True
    assert view["state_text"] == "候选通道 0 条（免费池为空或候选目录未就绪）"
    assert view["channel_count"] == 0
    # 空表只能陈述「0 条通道」，不得断言成因（内核 candidates() 异常也会返回空表）
    _empty_line = [x for x in view["lines"] if "候选通道 0 条" in x and "•" not in x][0]
    assert "免费池为空" in _empty_line and "或候选目录尚未就绪" in _empty_line
    assert "绝不回退" in FREE_ROUTER_FOOTER
    assert "（本次尚未路由过）" in "\n".join(view["head"])
    print("\n[空池渲染]", view["state_text"], "|", _empty_line)


def test_render_missing_and_bad_fields_never_raises():
    # 缺字段、类型全错、None 混入、垃圾元素 —— 一律不抛异常
    cases = [
        {},
        {"channels": None},
        {"channels": [None, "x", 5]},
        {"channels": [{}]},
        {"generated_at": "abc", "sticky_sessions": "abc", "last_routed_via": None,
         "channels": [{"provider": None, "model": 7, "base_priority": "x", "penalty": None,
                       "effective_score": float("nan"), "eligible": "true",
                       "remaining_sec": float("inf"), "cooldown_level": "2",
                       "limits": {"rpm": None, "rpm_used": "9"}, "ok": None, "fail": "3",
                       "latency_ms": None}]},
        {"channels": [{"limits": "not-a-dict", "eligible": 0}]},
    ]
    for i, snap in enumerate(cases):
        view = render_free_router_status(snap)
        assert isinstance(view, dict) and view["lines"], i
    v = render_free_router_status(cases[4])
    print("\n[脏数据渲染] rows =", v["rows"], "| states =", v["states"])
    assert v["rows"][0][0] == "?:7"
    assert v["rows"][0][3] == "0.0"      # NaN → 0.0
    assert v["rows"][0][4] == "是"        # "true" → True
    assert v["rows"][0][7] == "9/未配"   # 未配上限（内核 null）→「未配」而非 0
    assert v["rows"][0][12] == "3"


def test_render_null_limits_shows_unconfigured():
    """内核 limits 为 null = 未配该维上限，不得显示成 0。"""
    view = render_free_router_status({
        "channels": [{"provider": "p", "model": "m", "limits": {
            "rpm": None, "rpd": None, "tpm": 0, "tpd": 100,
            "rpm_used": 5, "rpd_used": 0, "tpm_used": 0, "tpd_used": 3}}]})
    row = view["rows"][0]
    assert row[7] == "5/未配" and row[8] == "0/未配"
    assert row[9] == "0/0" and row[10] == "3/100"
    row2 = render_free_router_status({"channels": [{"provider": "p", "model": "m"}]})["rows"][0]
    assert row2[7:11] == ["0/未配", "0/未配", "0/未配", "0/未配"]
    print("\n[null 上限渲染] rows =", row[7:11], "| 无 limits 字段 =", row2[7:11])


def test_render_fail_closed_false_warns():
    view = render_free_router_status({"channels": [], "mode": {"fail_closed": False}})
    assert "【警告】" in view["footer"]


def test_footer_states_persistence_facts():
    """页脚必须与内核事实一致：限流/冷却/sticky 落盘持久化，惩罚分只在内存。

    回归护栏：曾经的错误文案「冷却与限流计数为进程内状态，重启后清零」不得再出现。
    """
    f = FREE_ROUTER_FOOTER
    # ① 落盘项 + 相对路径
    assert "data/free_router/router_state.db" in f
    assert "重启不清零" in f
    assert "限流计数" in f and "429 冷却等级" in f and "Sticky 会话映射" in f
    # ② 仅进程内项
    assert "仅进程内" in f and "动态惩罚分" in f
    # ③ fail-closed 保留
    assert "绝不回退到付费模型（fail-closed）" in f
    # 旧错误断言不得回归
    assert "进程内状态，重启后清零" not in f
    assert "冷却与限流计数为进程内" not in f
    print("\n[页脚事实] " + f)


def test_footer_matches_kernel_source_of_truth():
    """页脚声称的落盘项/内存项，必须能在内核源码里找到依据（防止文案再次漂移）。"""
    import os
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    kern = os.path.join(root, "services", "free_router_kernel.py")
    rtr = os.path.join(root, "services", "free_model_router.py")
    if not (os.path.exists(kern) and os.path.exists(rtr)):
        print("[页脚事实] 跳过：内核源码不在预期位置")
        return
    ksrc = open(kern, encoding="utf-8").read()
    rsrc = open(rtr, encoding="utf-8").read()
    # 三张持久化表都在内核 DDL 里
    for tbl in ("rl_events", "cd429", "sticky"):
        assert re.search(r"CREATE TABLE IF NOT EXISTS %s" % tbl, ksrc), tbl
    # 惩罚分表被内核自述为「仅内存态」
    assert "仅内存态" in ksrc and "PenaltyTable" in ksrc
    # 路由状态库固定落在 free_router/router_state.db
    assert '"free_router", "router_state.db"' in rsrc
    print("[页脚事实] 内核依据齐备：rl_events/cd429/sticky 均落盘；PenaltyTable 仅内存；"
          "库路径 free_router/router_state.db")


def test_render_persistence_line_from_snapshot():
    """落盘状态行：内核自报 ok=True 显示真实路径；ok=False 显示退化为纯内存；缺失则不臆造。"""
    ok_view = render_free_router_status({
        "channels": [],
        "persistence": {"ok": True, "path": "data/free_router/router_state.db", "error": ""}})
    ok_line = [x for x in ok_view["head"] if "落盘状态" in x]
    assert ok_line and "router_state.db" in ok_line[0] and "重启不清零" in ok_line[0], ok_view["head"]

    bad_view = render_free_router_status({
        "channels": [],
        "persistence": {"ok": False, "path": "", "error": "FileExistsError: x"}})
    bad_line = [x for x in bad_view["head"] if "落盘状态" in x]
    assert bad_line and "退化为纯内存" in bad_line[0] and "FileExistsError: x" in bad_line[0]

    # 缺 persistence / 非 dict / 无 ok 键 / ok 无法判定（None、乱值、非 0/1 数字）
    # → 都不显示该行、不把「不知道」渲染成「没落盘」
    for snap in ({"channels": []},
                 {"channels": [], "persistence": "junk"},
                 {"channels": [], "persistence": []},
                 {"channels": [], "persistence": {"path": "x"}},
                 {"channels": [], "persistence": {"ok": None}},
                 {"channels": [], "persistence": {"ok": "unknown"}},
                 {"channels": [], "persistence": {"ok": 2}},
                 {"channels": [], "persistence": {"ok": float("nan")}}):
        view = render_free_router_status(snap)
        assert not [x for x in view["head"] if "落盘状态" in x], (snap, view["head"])
    no_path = render_free_router_status({"channels": [], "persistence": {"ok": True, "path": ""}})
    assert "（内核未给出路径）" in "\n".join(no_path["head"])
    # 字符串/0-1 形式的 ok 仍可判定
    for raw, want in ((0, "退化为纯内存"), ("false", "退化为纯内存"), (1, "已启用"), ("true", "已启用")):
        line = [x for x in render_free_router_status(
            {"channels": [], "persistence": {"ok": raw, "path": "p"}})["head"] if "落盘状态" in x]
        assert line and want in line[0], (raw, line)
    print("\n[落盘状态行] ok  →", ok_line[0])
    print("[落盘状态行] 失效 →", bad_line[0])
    print("[落盘状态行] 缺字段/无法判定 → 不显示该行（不臆造）")


# ---------------------------------------------------------------- #
# 2) 防御性取数链（无 Qt）
# ---------------------------------------------------------------- #
def test_resolve_without_facade():
    router, reason = _bare_dialog(None)._resolve_free_router()
    assert router is None and "facade=None" in reason
    print("\n[取数] facade=None →", reason)


def test_resolve_facade_without_llm():
    router, reason = _bare_dialog(_FakeFacade(None))._resolve_free_router()
    assert router is None and "LLMGateway 未就绪" in reason
    print("[取数] services['llm'] 为空 →", reason)


def test_resolve_llm_without_created_router():
    router, reason = _bare_dialog(_FakeFacade(_FakeLLM(None)))._resolve_free_router()
    assert router is None and "尚未创建" in reason
    print("[取数] 路由器未创建 →", reason)


def test_resolve_existing_router():
    r = _FakeRouter(FAKE_SNAPSHOT)
    got, reason = _bare_dialog(_FakeFacade(_FakeLLM(r)))._resolve_free_router()
    assert got is r and reason == ""


def test_resolve_injected_source_and_its_failure():
    dlg = _bare_dialog(None)
    dlg.ui_free_router_source = lambda: _FakeRouter(FAKE_SNAPSHOT)
    got, reason = dlg._resolve_free_router()
    assert got is not None and reason == ""

    def _boom():
        raise RuntimeError("注入方炸了")
    dlg.ui_free_router_source = _boom
    got, reason = dlg._resolve_free_router()
    assert got is None and "注入的取数回调异常" in reason
    print("[取数] 注入回调异常 →", reason)


def test_fetch_defensive_paths():
    snap, err = _bare_dialog(_FakeFacade(_FakeLLM(_FakeRouter(FAKE_SNAPSHOT))))._fetch_free_router_snapshot()
    assert snap is FAKE_SNAPSHOT and err == ""

    # 方法缺失
    snap, err = _bare_dialog(_FakeFacade(_FakeLLM(_FakeRouter(drop_method=True))))._fetch_free_router_snapshot()
    assert snap is None and "未提供 routing_snapshot()" in err

    # 抛异常
    snap, err = _bare_dialog(_FakeFacade(_FakeLLM(
        _FakeRouter(exc=RuntimeError("内核炸了")))))._fetch_free_router_snapshot()
    assert snap is None and "routing_snapshot() 异常：RuntimeError: 内核炸了" == err

    # 返回 None
    snap, err = _bare_dialog(_FakeFacade(_FakeLLM(_FakeRouter(None))))._fetch_free_router_snapshot()
    assert snap is None and err == "routing_snapshot() 返回 None"

    # 返回非 dict
    snap, err = _bare_dialog(_FakeFacade(_FakeLLM(_FakeRouter([1, 2]))))._fetch_free_router_snapshot()
    assert snap is None and "返回类型非法：list" in err
    print("\n[取数] 防御分支全部返回 (None, reason)，未抛异常")


def test_no_network_call_in_fetch(monkeypatch=None):
    """取数过程不得发起 socket 连接：用守卫替换 socket.create_connection 观察。"""
    import socket

    called = []
    real = socket.socket.connect

    def _guard(self, address, *a, **kw):
        called.append(address)
        raise AssertionError("取数链不应联网：%r" % (address,))

    socket.socket.connect = _guard
    try:
        _bare_dialog(_FakeFacade(_FakeLLM(_FakeRouter(FAKE_SNAPSHOT))))._fetch_free_router_snapshot()
        _bare_dialog(_FakeFacade(_FakeLLM(None)))._fetch_free_router_snapshot()
    finally:
        socket.socket.connect = real
    assert called == []
    print("[取数] socket.connect 未被调用（0 次）")


# ---------------------------------------------------------------- #
# 2.5) 与真实路由内核对接（routing_snapshot 由并列子代理实现；未落地则自跳过）
# ---------------------------------------------------------------- #
def test_render_real_kernel_snapshot_if_available():
    """真 FreeModelRouter.routing_snapshot() → 本面板渲染函数（字段名/类型对齐校验）。"""
    try:
        from services.free_model_router import FreeModelRouter
    except Exception as e:  # pragma: no cover
        print("\n[真内核] 跳过：FreeModelRouter 无法导入：%s" % e)
        return
    if not callable(getattr(FreeModelRouter, "routing_snapshot", None)):
        print("\n[真内核] 跳过：FreeModelRouter 尚无 routing_snapshot()（内核未就绪）")
        return

    import os as _os
    import shutil
    import socket
    import tempfile

    # 用一个「已存在的文件」占住 state_dir：内核 makedirs 会失败 ->
    # PersistentStateStore 自动退化为纯内存（不落盘、不留 sqlite 句柄，Windows 下可即时删除）。
    _fd, state_blocker = tempfile.mkstemp(prefix="frp_state_blocker_")
    _os.close(_fd)

    class _Cfg:
        """只把状态库落点指向不可建目录，其余键一律回落默认值（离线、无 key）。"""

        def get(self, key, default=None):
            if key == "llm.free.state_dir":
                return state_blocker
            return default

        def set(self, key, value):
            return None

    calls = []
    loopback = []
    real_connect = socket.socket.connect

    def _is_loopback(address):
        try:
            host = address[0]
        except Exception:  # noqa: BLE001
            return False
        return (isinstance(host, str)
                and (host.startswith("127.") or host in ("::1", "localhost", "0.0.0.0")))

    def _guard(self, address, *a, **kw):
        # 只禁止「外网」；回环（本机 Ollama 11434 等）记为 loopback 并放行，
        # 与项目 conftest 的口径一致（允许 127.0.0.1/::1）。
        if _is_loopback(address):
            loopback.append(address)
            return real_connect(self, address, *a, **kw)
        calls.append(address)
        raise AssertionError("routing_snapshot 不应访问外网：%r" % (address,))

    socket.socket.connect = _guard
    try:
        router = FreeModelRouter(_Cfg())
        snap = router.routing_snapshot()
    finally:
        socket.socket.connect = real_connect

    store = getattr(router, "_state_store", None)
    if store is not None:
        print("\n[真内核快照] 状态库落盘 ok=%s（False = 已退化为纯内存，本用例不写盘）"
              % getattr(store, "ok", "?"))
    # 清理占位文件：能删掉即证明没有遗留 sqlite 句柄
    _os.remove(state_blocker)
    assert not _os.path.exists(state_blocker)

    assert calls == [], calls
    print("\n[真内核快照] 外网调用=%d 次；回环调用=%d 次 %s"
          % (len(calls), len(loopback), loopback))
    assert isinstance(snap, dict)
    for key in ("generated_at", "last_routed_via", "sticky_sessions", "channels", "mode"):
        assert key in snap, key
    assert isinstance(snap["channels"], list)

    view = render_free_router_status(snap)
    print("\n[真内核快照] channels=%d | 外网调用=%d | 回环调用=%d | state_text=%s"
          % (len(snap["channels"]), len(calls), len(loopback), view["state_text"]))
    for line in view["lines"]:
        print("   ", line)
    for row in view["rows"]:
        assert len(row) == 14, row
    assert view["available"] is True
    assert view["channel_count"] == len(snap["channels"])
    if snap["channels"]:
        ch = snap["channels"][0]
        assert view["rows"][0][0] == "%s:%s" % (ch["provider"], ch["model"])
        print("[真内核快照] 真实 limits 渲染 =", view["rows"][0][7:11])

    # 落盘状态行必须与内核自报的 persistence 一致（本用例把状态库打成不可用 → 纯内存）
    persist = snap.get("persistence")
    if isinstance(persist, dict) and "ok" in persist:
        line = [x for x in view["head"] if "落盘状态" in x]
        assert line, view["head"]
        if persist["ok"] is False:
            assert "退化为纯内存" in line[0], line
        else:  # pragma: no cover - 取决于环境是否可写
            assert "已启用" in line[0], line
        print("[真内核快照] 落盘状态行 =", line[0])

    # ---- 真落盘分支：临时目录可写 → 状态库真的建成，页首如实给出库路径 ----
    tmp_dir = tempfile.mkdtemp(prefix="frp_store_")

    class _CfgWritable(_Cfg):
        def get(self, key, default=None):
            if key == "llm.free.state_dir":
                return tmp_dir
            return default

    socket.socket.connect = _guard
    try:
        router2 = FreeModelRouter(_CfgWritable())
        snap2 = router2.routing_snapshot()
    finally:
        socket.socket.connect = real_connect
    p2 = snap2.get("persistence") or {}
    view2 = render_free_router_status(snap2)
    print("[真内核快照] 落盘分支 persistence =", p2)
    assert p2.get("ok") is True, p2
    line2 = [x for x in view2["head"] if "落盘状态" in x]
    assert line2 and "router_state.db" in line2[0], view2["head"]
    print("[真内核快照] 真落盘状态行 =", line2[0])
    assert calls == [], calls                       # 两个分支合计仍为 0 次外网

    # 收尾：Windows 下 sqlite 句柄会锁住临时库，先关共享连接再删
    try:
        from services.free_router_kernel import close_shared_connections
        close_shared_connections()
    except Exception as e:  # noqa: BLE001
        print("[真内核快照] close_shared_connections 不可用：%s" % e)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("[真内核快照] 临时状态库目录已清理 =", not _os.path.exists(tmp_dir))


# ---------------------------------------------------------------- #
# 3) offscreen 下真实构造对话框 + 渲染
# ---------------------------------------------------------------- #
def test_dialog_offscreen_renders_panel():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as e:  # pragma: no cover
        print("[Qt 不可用] %s: %s" % (type(e).__name__, e))
        return
    app = QApplication.instance() or QApplication([])

    t0 = time.time()
    dlg = SettingsDialog(_FakeConfig(), _FakeFacade(_FakeLLM(_FakeRouter(FAKE_SNAPSHOT))), None)
    elapsed = time.time() - t0
    print("\n[offscreen] SettingsDialog 构造耗时 %.3fs（无网络/无 key）" % elapsed)

    for name in ("ui_free_router_title", "ui_free_router_hint", "ui_free_router_state",
                 "ui_free_router_refresh_btn", "ui_free_router_text", "ui_free_router_note"):
        assert hasattr(dlg, name), name

    # 等待后台取数线程回主线程渲染
    for _ in range(100):
        app.processEvents()
        if "已刷新" in dlg.ui_free_router_state.text():
            break
        time.sleep(0.02)

    print("[offscreen] state =", dlg.ui_free_router_state.text())
    print("[offscreen] text  =\n" + dlg.ui_free_router_text.toPlainText())
    print("[offscreen] note  =", dlg.ui_free_router_note.text())
    print("[offscreen] 只读 =", dlg.ui_free_router_text.isReadOnly())

    assert dlg.ui_free_router_text.isReadOnly() is True
    assert "已刷新：共 2 条候选通道，1 条可用，1 条冷却中" == dlg.ui_free_router_state.text()
    body = dlg.ui_free_router_text.toPlainText()
    assert "openrouter:deepseek/deepseek-chat-v3:free" in body
    assert "冷却中（2 级）" in body
    assert "nvidia:meta/llama-3.1-8b-instruct" in body
    assert dlg.ui_free_router_note.text().startswith("说明：免费池为空时报空池")
    assert elapsed < 5.0

    # 面本文案的事实性：不得再出现无限定的「不联网」；落盘路径必须写清
    title = dlg.ui_free_router_title.text()
    hint = dlg.ui_free_router_hint.text()
    print("[offscreen] title =", title)
    print("[offscreen] hint  =", hint)
    assert title == "免费通道路由状态（只读 · 不改变路由决策）"
    assert "不联网" not in title
    assert "外网" in hint and "本机 Ollama" in hint and "回环 127.0.0.1 / ::1" in hint
    # 不得再用「进程内」这类会把「状态不持久化」读进去的措辞
    assert "进程内" not in hint and "进程内" not in title
    assert dlg.ui_free_router_refresh_btn.toolTip() == "重新读取本地只读快照（不发外网请求、不影响路由决策）"
    assert "data/free_router/router_state.db" in dlg.ui_free_router_note.text()

    # 内核未就绪（方法不存在）时也必须渲染成不可用，且不抛异常
    dlg2 = SettingsDialog(_FakeConfig(), _FakeFacade(_FakeLLM(_FakeRouter(drop_method=True))), None)
    for _ in range(100):
        app.processEvents()
        if "不可用" in dlg2.ui_free_router_state.text() or "未创建" in dlg2.ui_free_router_state.text():
            break
        time.sleep(0.02)
    print("[offscreen] 内核未就绪 state =", dlg2.ui_free_router_state.text(),
          "| text =", dlg2.ui_free_router_text.toPlainText().replace("\n", " / "))
    assert FREE_ROUTER_UNAVAILABLE_HINT in dlg2.ui_free_router_state.text()

    # 无门面（facade=None）也必须能构造与渲染
    dlg3 = SettingsDialog(_FakeConfig(), None, None)
    for _ in range(60):
        app.processEvents()
        time.sleep(0.02)
    print("[offscreen] facade=None state =", dlg3.ui_free_router_state.text())
    assert FREE_ROUTER_UNAVAILABLE_HINT in dlg3.ui_free_router_state.text()

    # 手动点「刷新」按钮不得抛异常
    dlg.ui_free_router_refresh_btn.click()
    for _ in range(60):
        app.processEvents()
        time.sleep(0.02)
    assert dlg.ui_free_router_state.text().startswith("已刷新")


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    order = ["test_render_fake_snapshot_two_channels", "test_render_unavailable_inputs",
             "test_render_error_reason_wins", "test_render_empty_pool",
             "test_render_missing_and_bad_fields_never_raises",
             "test_render_null_limits_shows_unconfigured",
             "test_render_fail_closed_false_warns",
             "test_footer_states_persistence_facts",
             "test_footer_matches_kernel_source_of_truth",
             "test_render_persistence_line_from_snapshot",
             "test_resolve_without_facade",
             "test_resolve_facade_without_llm", "test_resolve_llm_without_created_router",
             "test_resolve_existing_router", "test_resolve_injected_source_and_its_failure",
             "test_fetch_defensive_paths", "test_no_network_call_in_fetch",
             "test_render_real_kernel_snapshot_if_available",
             "test_dialog_offscreen_renders_panel"]
    tests.sort(key=lambda kv: order.index(kv[0]) if kv[0] in order else 999)
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print("FAIL %s: %s: %s" % (name, type(e).__name__, e))
            traceback.print_exc()
    print("\n== __main__ 冒烟汇总：%d passed, %d failed ==" % (len(tests) - failed, failed))
    sys.exit(1 if failed else 0)
