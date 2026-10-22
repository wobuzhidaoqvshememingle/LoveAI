# -*- coding: utf-8 -*-
"""
tests/test_wechat_oneclick_keygate_live.py — 让「取密钥 fail-closed 闸」**真的走到**。

背景：此前全仓 `tests/` 下 **0 处引用 WechatOneClick**，`WechatOneClick.__init__`
调用计数为 0；`app/widgets/wechat_export_widget.py` 的插桩目标也多为 0 次 ——
即「取密钥判定 / fail-closed」这条闸**从来没有被真正执行过的证据**。
本文件用**真实例化 + 替身注入**把它跑起来，并给出**调用计数**（只有计数为正才算覆盖）。

两头各一层：
  A. `services/wechat_oneclick.py::WechatOneClick` —— **真例化**（依赖全注入替身），
     驱动取密钥判定的三档结果：`True`（取到并校验通过）/ `False`（判定不通过）/
     `None`（取不到：无探针库 / 提取器不可用）。
  B. `app/widgets/wechat_export_widget.py` 的 fail-closed 三态闸 —— 真例化面板，
     `_keyflow_available()` 的 `None / False / True` 三档 + `_update_keyflow_gates()`
     只允许 `available is True` 放行；并实证「判定不通过时**微信函数调用次数 = 0**」。

红线（本文件严格遵守）：
  * 不跑真实取密钥流程；不碰真实微信进程/DLL/密钥缓存/聊天库。
  * 扫码登录与"提取密钥"两步不由程序代做；不索取任何 cookie。
  * 全部外部依赖用**替身**：`key_extractor` / `importer` / `reader_cls` / `decryptor` /
    `wechat_key_controller`（连真模块都不加载，直接塞 `sys.modules` 替身）/ facade。
  * 只读被测文件；不改任何产品代码。

版本钉住（另一条线仍在收尾改这些文件，版本一变本次验证即失效）：
  见 `scratch/keygate_static.txt` 与报告 §17.2 记录的字节数与 SHA256。
"""
from __future__ import annotations

import collections
import contextlib
import os
import sys
import types

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 必须在导入 Qt 相关模块（app.widgets.*）之前设置：离屏，不弹真窗口
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.wechat_oneclick import WechatOneClick  # noqa: E402

#: 全局调用计数（测试断言它为正；报告里的数字直接取自它）
COUNTS = collections.Counter()

#: 关键观测值：由用例在真实执行中自己记录，供报告直接取实测数字
OBSERVED: dict = {}


# --------------------------------------------------------------------------- #
# 小工具：可复用的"临时打桩"（不依赖 pytest fixture，便于外部脚本直接调用本文件用例）
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def _patched(obj, name, value):
    had = hasattr(obj, name)
    old = getattr(obj, name, None)
    setattr(obj, name, value)
    try:
        yield
    finally:
        if had:
            setattr(obj, name, old)
        else:
            try:
                delattr(obj, name)
            except AttributeError:
                pass


@contextlib.contextmanager
def _patched_sys_modules(name, module):
    old = sys.modules.get(name)
    sys.modules[name] = module
    try:
        yield
    finally:
        if old is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = old


@contextlib.contextmanager
def _counted(obj, name):
    """把 obj.name 包一层计数（写进 COUNTS["类名.方法名"]），行为逐字不变。"""
    real = getattr(obj, name)
    key = "%s.%s" % (getattr(obj, "__name__", obj.__class__.__name__), name)

    def wrapper(*a, **kw):
        COUNTS[key] += 1
        return real(*a, **kw)

    with _patched(obj, name, wrapper):
        yield key


# --------------------------------------------------------------------------- #
# 替身（全部离线；绝不触碰真实微信/密钥/数据库）
# --------------------------------------------------------------------------- #
class _StubKX:
    """替身密钥提取器：只实现被用到的两个入口。"""

    def __init__(self, cache=None, extract=None):
        self.cache = dict(cache or {})
        self.extract = extract if extract is not None else {
            "ok": False, "key": None, "method": None, "log": []}
        self.calls = collections.Counter()
        self.last_extract_kwargs = None

    def get_cached_key(self, path):
        self.calls["get_cached_key"] += 1
        COUNTS["kx.get_cached_key"] += 1
        return self.cache.get(path)

    def extract_key_auto(self, probe_db, version=None, enable_high_risk=False,
                         process_info=None):
        self.calls["extract_key_auto"] += 1
        COUNTS["kx.extract_key_auto"] += 1
        self.last_extract_kwargs = {"version": version,
                                    "enable_high_risk": enable_high_risk}
        return self.extract


class _StubKeyflow:
    """替身密钥编排（WeChatKeyFlow）：precheck 可配；run() 只计数、**绝不真跑**。"""

    def __init__(self, available=True, reason="替身预检通过", **extra):
        self._pre = {"available": available, "reason": reason}
        self._pre.update(extra)
        self.calls = collections.Counter()

    def precheck(self):
        self.calls["precheck"] += 1
        COUNTS["keyflow.precheck"] += 1
        return dict(self._pre)

    def run(self, **kwargs):
        # 真跑会关微信/注入 Hook —— 本验证绝不允许发生，只计数。
        self.calls["run"] += 1
        COUNTS["keyflow.run"] += 1
        return {"ok": False, "data": {}, "error": "替身：本验证不应执行 run()"}


class _StubImporter:
    def launch_wxkey_capture(self, *a, **kw):  # pragma: no cover - 不应被调用
        COUNTS["importer.launch_wxkey_capture"] += 1
        return {"ok": False, "reason": "替身：不执行"}


def _controller_cls(identity_ok, reason="替身身份判定"):
    class _StubController:
        def __init__(self, *a, **kw):
            COUNTS["controller.__init__"] += 1
            self.identity = {"ok": bool(identity_ok), "reason": reason}

    return _StubController


def _fake_kc_module(controller_cls, flow_obj=None):
    """替身 services.wechat_key_controller —— 连真模块都不加载（更不可能碰微信）。"""
    mod = types.ModuleType("services.wechat_key_controller")
    mod.WeChatKeyController = controller_cls
    mod.WeChatKeyFlow = (lambda *a, **kw: flow_obj)
    return mod


_ABSENT_ROOT = os.path.join(os.environ.get("TEMP", "."), "loveai_no_such_root_xyz")


def _acct(account_id="acct_live"):
    return {"account_id": account_id, "version_family": "4.x", "root": _ABSENT_ROOT}


def _make_oneclick(kx):
    """真例化 WechatOneClick：依赖全部注入替身。"""
    return WechatOneClick(key_extractor=kx, importer=_StubImporter(),
                          reader_cls=object, decryptor=object, config=None)


def _make_oneclick_no_extractor():
    """`self._kx is None` 那一档：把默认提取器打桩为 None 且不注入替身。"""
    with _patched(WechatOneClick, "_default_key_extractor",
                  staticmethod(lambda: None)):
        return WechatOneClick(key_extractor=None, importer=_StubImporter(),
                              reader_cls=object, decryptor=object, config=None)


def _fake_verify(return_value):
    """把 _verify 换成只计数、按配置返回的替身（避免真解密）。"""
    def fake(self, db_path, key, family):
        COUNTS["WechatOneClick._verify"] += 1
        return bool(return_value)

    return fake


# =========================================================================== #
# A. WechatOneClick 真例化 + 取密钥判定三档
# =========================================================================== #
class TestWechatOneClickKeyJudgementLive:

    def test_init_really_runs_and_object_is_real(self):
        """`WechatOneClick.__init__` 调用计数必须为正（此前全仓为 0）。"""
        kx = _StubKX()
        real_init = WechatOneClick.__init__
        before = COUNTS["WechatOneClick.__init__"]

        def counted_init(self, *a, **kw):
            COUNTS["WechatOneClick.__init__"] += 1
            return real_init(self, *a, **kw)

        with _patched(WechatOneClick, "__init__", counted_init):
            oc = WechatOneClick(key_extractor=kx, importer=_StubImporter(),
                                reader_cls=object, decryptor=object, config=None)

        # 本次构造恰好触发 1 次 __init__（相对计数，不受同进程多次运行影响）
        assert COUNTS["WechatOneClick.__init__"] - before == 1
        assert COUNTS["WechatOneClick.__init__"] >= 1
        assert isinstance(oc, WechatOneClick)
        # 真实内部状态（构造确实执行到了）：
        assert oc._kx is kx
        assert oc._progress["stage"] == "idle"
        assert oc.get_progress()["running"] is False
        assert callable(oc.high_risk_available)
        assert isinstance(oc._last_log, list)

    def test_judgement_true_branch_key_acquired(self):
        """档 True：缓存命中且校验通过 -> 判定通过、取到密钥。"""
        probe = "X:\\nonexistent\\message_0.db"
        kx = _StubKX(cache={probe: {"key": "ab" * 32, "method": "dll_read"}})
        oc = _make_oneclick(kx)
        acct, log = _acct(), []

        with _patched(WechatOneClick, "_verify", _fake_verify(True)), \
                _counted(WechatOneClick, "_has_cached_key"), \
                _counted(WechatOneClick, "_acquire_key"), \
                _counted(WechatOneClick, "_resolve_cached_key"):
            assert oc._has_cached_key(probe, "4.x") is True
            res = oc._acquire_key(acct, probe, False, log)

        assert res["ok"] is True
        assert res["key"] == "ab" * 32
        assert res["method"].startswith("per_db_cache")
        assert COUNTS["WechatOneClick._has_cached_key"] >= 1
        assert COUNTS["WechatOneClick._acquire_key"] >= 1
        assert COUNTS["WechatOneClick._verify"] >= 1
        # 缓存入口被两条路径各读一次（_has_cached_key 与 _acquire_key 各自读缓存）
        assert COUNTS["kx.get_cached_key"] >= 2
        assert any("命中并通过校验" in line for line in log)
        # 实测发现（只记录、不改产品代码）：`_acquire_key` **没有**复用
        # `_resolve_cached_key`，而是把缓存循环内联复制了一份（:913-924）；
        # 故本档 `_resolve_cached_key` 计数为 0，属正常 —— 它的覆盖由
        # `test_resolve_cached_key_returns_none_when_nothing_found` 单独保证。

    def test_judgement_false_branch_not_passed(self):
        """档 False：缓存有条目但**校验不通过**，低风险阶梯也全未命中 -> 判定不通过。"""
        probe = "X:\\nonexistent\\message_0.db"
        kx = _StubKX(cache={probe: {"key": "cd" * 32, "method": "global"}},
                     extract={"ok": False, "key": None, "method": None,
                              "log": [{"method": "memory_scan", "ok": False,
                                       "reason": "未找到"}]})
        oc = _make_oneclick(kx)
        acct, log = _acct(), []

        with _patched(WechatOneClick, "_verify", _fake_verify(False)), \
                _counted(WechatOneClick, "_has_cached_key"), \
                _counted(WechatOneClick, "_acquire_key"), \
                _counted(WechatOneClick, "_route_key_info_db"):
            assert oc._has_cached_key(probe, "4.x") is False
            res = oc._acquire_key(acct, probe, False, log)

        assert res["ok"] is False
        assert res["reason"]
        assert res["next_action"]
        assert kx.calls["extract_key_auto"] == 1
        # 未给 consent_high_risk 时**不得**静默放开高风险阶梯：
        assert kx.last_extract_kwargs["enable_high_risk"] is False
        assert COUNTS["WechatOneClick._route_key_info_db"] >= 1
        assert any(("校验未通过" in line or "未命中" in line) for line in log)

    def test_judgement_none_branch_no_probe_db(self):
        """档 None-①：没有探针库 -> 直接判"取不到"，且**不碰**提取器。"""
        kx = _StubKX()
        oc = _make_oneclick(kx)
        with _counted(WechatOneClick, "_acquire_key"):
            res = oc._acquire_key(_acct(), None, False, [])
        assert res["ok"] is False
        assert "没有可用的探针库" in res["reason"]
        assert kx.calls["get_cached_key"] == 0      # 短路，没往下走
        assert kx.calls["extract_key_auto"] == 0
        assert COUNTS["WechatOneClick._acquire_key"] >= 1

    def test_judgement_none_branch_no_extractor(self):
        """档 None-②：密钥提取器不可用 -> 判"取不到"，仍不碰微信。"""
        oc = _make_oneclick_no_extractor()
        assert oc._kx is None
        with _counted(WechatOneClick, "_acquire_key"):
            res = oc._acquire_key(_acct(), "X:\\nope\\message_0.db", False, [])
        assert res["ok"] is False
        assert "密钥提取器不可用" in res["reason"]

    def test_resolve_cached_key_returns_none_when_nothing_found(self):
        """`_resolve_cached_key` 找不到时返回**字面 None**（"取不到"的类型表达）。"""
        oc = _make_oneclick(_StubKX())
        with _counted(WechatOneClick, "_resolve_cached_key"):
            got = oc._resolve_cached_key("acct", "X:\\nonexistent\\message_0.db", "4.x")
        assert got is None
        assert COUNTS["WechatOneClick._resolve_cached_key"] >= 1

    def test_high_risk_available_false_branches(self):
        """`high_risk_available()` 的 False 档（三种成因）；它只可能回 True/False。"""
        # (a) 控制器身份不可用
        with _patched_sys_modules("services.wechat_key_controller",
                                 _fake_kc_module(_controller_cls(False, "DLL 缺失"))):
            oc = _make_oneclick(_StubKX())
            with _counted(WechatOneClick, "high_risk_available"):
                r = oc.high_risk_available()
        assert r["available"] is False
        assert "控制器 DLL 不可用" in r["reason"]
        assert COUNTS["WechatOneClick.high_risk_available"] >= 1
        assert COUNTS["controller.__init__"] >= 1

        # (b) 身份 OK 但预检判定不可注入
        flow = _StubKeyflow(available=False, reason="预检判定不可注入")
        with _patched_sys_modules("services.wechat_key_controller",
                                  _fake_kc_module(_controller_cls(True), flow)):
            oc2 = _make_oneclick(_StubKX())
            with _counted(WechatOneClick, "_ensure_keyflow"):
                r2 = oc2.high_risk_available()
        assert r2["available"] is False
        assert "不可注入" in r2["reason"]
        assert flow.calls["precheck"] == 1
        assert COUNTS["WechatOneClick._ensure_keyflow"] >= 1

        # (c) 编排模块不可用（_ensure_keyflow -> None）
        with _patched_sys_modules("services.wechat_key_controller",
                                  _fake_kc_module(_controller_cls(True))), \
                _patched(WechatOneClick, "_ensure_keyflow", lambda self: None):
            oc3 = _make_oneclick(_StubKX())
            r3 = oc3.high_risk_available()
        assert r3["available"] is False
        assert "不可用" in r3["reason"]

    def test_high_risk_available_true_branch(self):
        """`high_risk_available()` 的 True 档：预检通过，原样回传版本/特征码档/命中数。"""
        flow = _StubKeyflow(available=True, reason="预检通过", version="4.1.15.13",
                            signature_branch=">=4.1.4", signature_hits=0)
        with _patched_sys_modules("services.wechat_key_controller",
                                  _fake_kc_module(_controller_cls(True), flow)):
            oc = _make_oneclick(_StubKX())
            r = oc.high_risk_available()
        assert r["available"] is True
        assert r["version"] == "4.1.15.13"
        assert r["signature_branch"] == ">=4.1.4"
        assert r["signature_hits"] == 0
        assert flow.calls["precheck"] == 1

    def test_failed_judgement_does_not_escalate_to_wechat_calls(self):
        """判定不通过（None/False）时**微信函数调用次数 = 0**：绝不升级到会关微信的流程。"""
        probe = "X:\\nonexistent\\message_0.db"
        flow = _StubKeyflow(available=True, reason="预检通过")
        kx = _StubKX(cache={probe: {"key": "ef" * 32, "method": "global"}})

        with _patched_sys_modules("services.wechat_key_controller",
                                  _fake_kc_module(_controller_cls(True), flow)), \
                _patched(WechatOneClick, "_verify", _fake_verify(False)), \
                _counted(WechatOneClick, "_acquire_key"):
            oc = _make_oneclick(kx)
            r_none = oc._acquire_key(_acct(), None, False, [])       # 档 None
            r_false = oc._acquire_key(_acct(), probe, False, [])     # 档 False

        assert r_none["ok"] is False and r_false["ok"] is False
        assert COUNTS["WechatOneClick._acquire_key"] >= 1
        # 微信侧函数：一次都没被调用
        assert flow.calls["run"] == 0
        assert COUNTS["keyflow.run"] == 0
        assert COUNTS["importer.launch_wxkey_capture"] == 0


# =========================================================================== #
# B. 面板 fail-closed 三态闸（None / False / True）+ 0 调用实证
# =========================================================================== #
class _StubFacade:
    """替身 facade：只提供契约方法并计数；绝不接触真实微信。"""

    def __init__(self):
        self.calls = collections.Counter()

    def wechat_keyflow_precheck(self, **kw):
        self.calls["wechat_keyflow_precheck"] += 1
        return {"ok": True, "data": {"available": True, "reason": "替身"}}

    def wechat_keyflow_run(self, **kw):
        self.calls["wechat_keyflow_run"] += 1
        return {"ok": True, "data": {}}

    def wechat_oneclick_run(self, **kw):
        self.calls["wechat_oneclick_run"] += 1
        return {"ok": True, "data": {}}


def _qt():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _make_widget(facade):
    from app.widgets.wechat_export_widget import WechatExportWidget
    COUNTS["widget.__init__"] += 1
    return WechatExportWidget(facade=facade)


class TestWidgetKeyflowFailClosedGate:

    def test_tristate_gate_none_false_true(self):
        """三态闸：只有 available is True 才启用取密钥按钮；None/False 一律禁用。"""
        _qt()
        facade = _StubFacade()
        w = _make_widget(facade)
        try:
            # 档 None：面板刚打开、还没跑过 precheck
            w._keyflow_status = None
            w._update_keyflow_gates()
            assert w._keyflow_available() is None
            assert w._btn_keyflow_run.isEnabled() is False
            assert w._ch_acquire_if_missing.isEnabled() is False   # 同一道闸
            # 档 False：已确认不可用
            w._keyflow_status = {"available": False, "reason": "特征码 0 命中"}
            w._update_keyflow_gates()
            assert w._keyflow_available() is False
            assert w._btn_keyflow_run.isEnabled() is False
            assert w._ch_acquire_if_missing.isEnabled() is False   # 同一道闸
            # 档 True：已确认可用
            w._keyflow_status = {"available": True, "reason": "预检通过"}
            w._update_keyflow_gates()
            assert w._keyflow_available() is True
            assert w._btn_keyflow_run.isEnabled() is True
            assert w._ch_acquire_if_missing.isEnabled() is True    # 同一道闸放行
        finally:
            w.deleteLater()

    def test_fail_closed_zero_wechat_calls_when_not_confirmed(self):
        """None / False 两档：即使**程序化**调用 `_on_keyflow_run()`，也必须拒发（0 次）。"""
        _qt()
        facade = _StubFacade()
        w = _make_widget(facade)
        try:
            for label, status in (("none", None),
                                  ("false", {"available": False, "reason": "不可用"})):
                facade.calls.clear()
                w._keyflow_running = False
                w._running = False
                w._keyflow_worker = None
                w._keyflow_status = status
                w._update_keyflow_gates()
                assert w._btn_keyflow_run.isEnabled() is False

                before_log = w._log_view.toPlainText()
                w._on_keyflow_run()            # 绕过按钮，直接调用
                after_log = w._log_view.toPlainText()

                assert facade.calls["wechat_keyflow_run"] == 0
                assert facade.calls["wechat_oneclick_run"] == 0
                assert w._keyflow_worker is None
                assert "未执行取密钥" in after_log[len(before_log):]
                OBSERVED["failclosed_" + label] = {
                    "btn_enabled": w._btn_keyflow_run.isEnabled(),
                    "keyflow_run_calls": facade.calls["wechat_keyflow_run"],
                    "oneclick_run_calls": facade.calls["wechat_oneclick_run"],
                    "worker_created": w._keyflow_worker is not None,
                }
        finally:
            w.deleteLater()

    def test_true_branch_actually_reaches_wechat_call(self):
        """True 档：闸门不是恒关 —— 走到发往微信函数那一步，替身驱动后计 1 次。"""
        _qt()
        facade = _StubFacade()
        w = _make_widget(facade)
        created = []

        class _StubWorker:
            def __init__(self, facade_, kwargs, parent=None):
                self.facade = facade_
                self.kwargs = dict(kwargs)
                self.started = 0
                created.append(self)

                class _Sig:
                    def connect(self, *a, **k):
                        return None

                self.done = _Sig()

            def start(self):
                self.started += 1

            def run(self):
                return self.facade.wechat_keyflow_run(**self.kwargs)

        try:
            w._keyflow_status = {"available": True, "reason": "预检通过"}
            w._update_keyflow_gates()
            assert w._btn_keyflow_run.isEnabled() is True
            # 「判定通过 -> 闸打开」的证据：起跑前按钮确实是可用的
            OBSERVED["true_branch_btn_enabled_before_run"] = True

            # ① 未确认注入知情 -> 走到确认步骤即止，仍不发往微信函数
            w._confirm_injection = lambda: False
            w._on_keyflow_run()
            assert facade.calls["wechat_keyflow_run"] == 0
            assert "未确认注入知情" in w._keyflow_detail.text()

            # ② 已确认 + 替身 worker -> 真正走到发往微信函数那一步（1 次）
            from app.widgets import wechat_export_widget as wxw
            with _patched(wxw, "_KeyflowRunWorker", _StubWorker):
                w._keyflow_running = False
                w._confirm_injection = lambda: True
                w._on_keyflow_run()
                assert len(created) == 1
                assert created[0].started == 1
                assert facade.calls["wechat_keyflow_run"] == 0   # 还没执行
                created[0].run()                                  # 替身手动驱动
                assert facade.calls["wechat_keyflow_run"] == 1
                assert created[0].kwargs.get("consent_injection") is True
                OBSERVED["true_branch"] = {
                    # 注意：这是**流程已起跑后**读到的值（busy ⇒ 置灰，符合设计）；
                    # "判定通过时闸确实打开"的证据是 true_branch_btn_enabled_before_run。
                    "btn_enabled_after_run_started": w._btn_keyflow_run.isEnabled(),
                    "worker_created": len(created) == 1,
                    "worker_started": created[0].started,
                    "keyflow_run_calls": facade.calls["wechat_keyflow_run"],
                    "consent_injection": created[0].kwargs.get("consent_injection"),
                }
        finally:
            w.deleteLater()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
