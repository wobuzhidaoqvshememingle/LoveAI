# -*- coding: utf-8 -*-
"""
微信一键导出面板（WechatExportWidget）

面向不懂技术的终端用户：面板主体只有一个醒目的大按钮
「一键导入微信聊天记录」。点一次即自动完成：

    发现库 → 识别版本 → 取密钥 → 解密 → 导入

全程不需要命令行、不需要手动注入、不需要手动登录。密钥获取也内建在窗口里，
用户不必再去外面找人扒密钥。

已知限制（本机实测，界面必须同屏告知，见 _LIMITATION_HINT）：
本机微信 4.1.15.13 上上游 wx_key 的特征码 0 命中（该工具只覆盖到 4.1.2.x），
且本机没有 C/C++ 工具链无法重编 → 本机取不到密钥。因此只有已在本机取过密钥
（有缓存）的账号能一键跑通；从未取过密钥的账号在本机不可用。禁止任何"改/降级微信版本"的动作。

后端由 app/service_facade.py 的 ServiceFacade 提供（冻结契约；本面板只按形状
解析返回结果，不自实现后端逻辑）：

「一键导入」链路（7–9 段：解密 → 入库）：
    facade.wechat_oneclick_discover()
    facade.wechat_oneclick_run(account_ids=None, contact_filter=None,
                               incremental=False, consent_high_risk=False,
                               acquire_key_if_missing=False, consent_injection=False)
    facade.wechat_oneclick_progress()
    facade.wechat_oneclick_cancel()
    facade.wechat_oneclick_highrisk_status()
    facade.wechat_oneclick_acquire_key_high_risk()

「取密钥」链路（1–6 段：检测微信 → 关微信 → 重启 → 前台化 → 等扫码登录 → 取密钥）：
    facade.wechat_keyflow_precheck()
    facade.wechat_keyflow_run(consent_injection=False, auto_restart=True,
                              allow_auto_login_click=False)
    facade.wechat_keyflow_progress()
    facade.wechat_keyflow_cancel()

两块合起来就是终端用户要的一条完整链路：拿密钥 → 解密 → 入库。
wechat_oneclick_run 的两个新增参数是可选的：只有用户在界面上开了
「无缓存密钥的账号自动走取密钥流程」并确认过注入知情，才会把它们传 True；
后端若尚未升级该参数，本面板会明确提示而不是悄悄丢弃用户意图。

统一返回形状：{"ok": bool, "data": {...}, "error": optional str}
成功读 data，失败读 error；data / error 缺失时一律防御处理，绝不崩溃。

「关微信」这道闸（单一判定标准，fail-closed）：
「取密钥区」里所有会产生副作用的控件（一键取密钥 / 无缓存自动取密钥 / 代点登录）
一律要求**只读预检已明确判定可用**（wechat_keyflow_precheck 返回 available is True）
才可点；available 为 None（尚未判定）或 False（判定不通过）时整段置灰并给出人话原因。
判定口径只有 _keyflow_available() 一个来源，_update_keyflow_gates() 与 _on_keyflow_run()
入口共用它，不允许出现「按钮灰了但入口还能走」的双标准；_on_oneclick() 的自动取密钥
支路同样以 isEnabled() + available 判断拦下。唯一例外是「检测」按钮（只读，不碰微信进程），
保持可点，否则用户没有任何复核手段。这样可保证：关微信只可能发生在判定通过之后。

降级策略：
  - facade 为 None / 契约方法缺失 / 调用抛异常 → 显示人话提示
    「后端一键服务尚未就绪」「后端取密钥服务尚未就绪」，并置灰对应控件；
    一键导入契约不可用时还可选择回退到旧版导入通道（importer）。
  - 保留旧的两个能力入口：联系人筛选下拉、实时监听开关（监听器仍由 monitor 注入）。

构造签名向后兼容：WechatExportWidget(importer=None, monitor=None, facade=None, parent=None)
"""
from __future__ import annotations

import inspect
import logging
import os
from typing import Any, Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPalette, QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)

#: 沿用 settings_dialog.py / training_settings_widget.py 的既有配色（深色 + 金色）
_STYLE = """
    QGroupBox { border: 1px solid #d4af37; border-radius: 4px; margin-top: 12px;
                padding-top: 12px; color: #d4af37; font-weight: bold; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
    QLabel { color: #e0e0e0; }
    QComboBox, QSpinBox { background: #0f3460; color: #e0e0e0;
        border: 1px solid #d4af37; padding: 4px 8px; border-radius: 3px; }
    QPushButton { background: #d4af37; color: #1a1a2e; border: none;
        padding: 6px 16px; border-radius: 3px; font-weight: bold; }
    QPushButton:hover { background: #f4d03f; }
    QPushButton:disabled { background: #555555; color: #999999; }
    QCheckBox { color: #e0e0e0; spacing: 8px; }
    QProgressBar { background: #0f3460; color: #ffffff; border: 1px solid #d4af37;
        border-radius: 3px; text-align: center; }
    QProgressBar::chunk { background: #d4af37; }
    QTextEdit { background: #0f3460; color: #e0e0e0; border: 1px solid #d4af37; }
    QTableWidget { background: #0f3460; color: #e0e0e0; border: 1px solid #d4af37;
        gridline-color: #2a4a7a; }
    QTableWidget QTableCornerButton::section { background: #1a1a2e; }
    QHeaderView::section { background: #1a1a2e; color: #d4af37;
        border: 1px solid #2a4a7a; padding: 3px; }
    QScrollArea { background: transparent; border: none; }
    QScrollBar:vertical { background: #0f3460; width: 10px; margin: 0; }
    QScrollBar::handle:vertical { background: #d4af37; border-radius: 5px; min-height: 24px; }
    /* 面板底：必须是深色，否则内容区会用 Qt 默认调色板 Window(#efefef) 自动填充，
       把本面板的浅色文案（QLabel #e0e0e0 / #c0c0c0 等）压成浅底浅字、肉眼几乎看不见。 */
    #wechatExportRoot { background: #16213e; }
    #wechatExportContent { background: #16213e; }
"""

#: 高风险取密钥路线的知情同意文案（必须完整包含风险点）
HIGH_RISK_WARNING = (
    "您正在启用「高风险取密钥」路线，请先确认已知情：\n\n"
    "1. 这是 DLL 注入方式：把模块注入到微信进程里去抓数据库密钥；\n"
    "2. 流程正常结束时会自动卸载 Hook 并清理；若中途异常退出（例如被强杀），微信可能需要重新启动；\n"
    "3. 存在账号风控风险，请自行评估后再决定；\n"
    "4. 只有在低风险路线（已缓存密钥等）都失败时，才需要用到它；\n"
    "5. 本机当前微信版本 4.1.15.13 同样扫不到特征码（见面板顶部「已知限制」）。\n\n"
    "是否仍要启用？"
)

#: 表格列定义：（表头, 列宽）
_COLUMNS = (
    ("导入", 46),
    ("账号", 108),
    ("版本", 148),
    ("库目录", 200),
    ("库数量", 58),
    ("可导入", 62),
    ("密钥状态", 76),
)

#: 「取密钥」流程的九个阶段，顺序即步骤条展示顺序（stage 取值与后端契约一致）
KEYFLOW_STAGES = (
    ("detect", "检测微信"),
    ("kill", "关闭微信"),
    ("launch", "重启微信"),
    ("foreground", "前台化"),
    ("wait_login", "等待扫码登录"),
    ("inject", "注入"),
    ("poll_key", "回收密钥"),
    ("persist", "写入密钥缓存"),
    ("cleanup", "清理"),
)
_STAGE_ORDER = {key: i for i, (key, _name) in enumerate(KEYFLOW_STAGES)}
_STAGE_NAMES = dict(KEYFLOW_STAGES)
_STAGE_TOTAL = len(KEYFLOW_STAGES)

#: 阶段结果的落库名（后端可能只给 stages 而没给 stage_name）
_ALL_STAGE_KEYS = tuple(_STAGE_ORDER)

#: 完整链路说明（两块的从属关系写在一句话里；并明确指向顶部的已知限制，避免读成"万能可用"）
_CHAIN_HINT = (
    "完整链路：取密钥（检测 → 关微信 → 重启 → 前台化 → 等扫码登录 → 取密钥，第 1–6 段）"
    " → 解密 → 入库（第 7–9 段）。下面两块属于同一条链路：先取到密钥，再一键导入；"
    "本机能不能取到密钥，请先看顶部「已知限制」。")

#: 已知限制（必读）：本机微信版本取不到密钥。
#: 只讲"点一次全自动"而不讲这条，就是过度承诺；必须与绿色卖点同屏、位置紧随其后。
_LIMITATION_HINT = (
    "已知限制（必读）：本机微信 4.1.15.13 目前取不到密钥——上游 wx_key 的特征码只覆盖到 4.1.2.x，"
    "这个版本扫不到；本机也没有 C/C++ 工具链，无法重编特征码。"
    "所以：只有已取过密钥（有缓存）的账号能一键跑通，从未取过密钥的账号在本机不可用。"
    "下一步可选：① 由你自己安装一个上游已支持的微信版本、用它取到密钥后再回来导入"
    "（本工具不会改动或降级你的微信）；② 先只导入已有密钥的账号——在账号表里取消勾选没有密钥的账号。")

#: 还没确认「本机能不能取密钥」时的人话提示。
#: 存在的理由：这个按钮一按就会先关闭并重启用户的微信（auto_restart=True），
#: 所以「可用性尚未确认」（available 为 None，面板刚打开、还没跑过 precheck）时绝不允许点击。
_NEED_PRECHECK_HINT = (
    "还没确认本机能不能取密钥：请先点「检测」。未确认前「一键取密钥」不可点"
    "——它会先关闭并重启你的微信。")

#: 「一键取密钥」按钮的 tooltip（可用时）：把真实动作如实写出来
_KEYFLOW_RUN_TOOLTIP = (
    "一键取密钥：会先自动关闭并重启你的微信，然后在前台弹出的微信窗口等你扫码登录，"
    "再注入并回收密钥；注入前还会再弹一次知情确认。\n"
    "只在检测确认「本机可取密钥」时才可点。")

#: 注入前的显式知情同意文案（四个风险点必须齐全；旧高风险路线那套「抓完就崩」的说法不要再用）
INJECT_WARNING = (
    "即将执行「取密钥」流程。注意：这一步会先自动关闭你正在使用的微信、再自动重启它，"
    "然后在前台弹出的微信窗口里等你扫码登录；请先确认此刻可以中断微信使用。\n\n"
    "流程顺序：关闭微信 → 重启微信 → 前台化 → 等你扫码登录 → 取密钥。\n"
    "请先确认你已知情：\n\n"
    "1. 该流程会向微信进程写入 Hook（注入），用于拦截数据库密钥；\n"
    "2. 流程正常结束时会自动卸载 Hook 并清理，不需要你手动处理；\n"
    "3. 若流程中途异常退出（例如被强杀），微信可能需要重新启动；\n"
    "4. 涉及自动化操作，存在账号风控风险，请自行评估后再继续。\n\n"
    "是否继续取密钥？"
)

#: 「代点登录」能力不可用的关键词（后端没给显式字段时按这些词兜底判断）
_AUTO_LOGIN_UNAVAILABLE_KEYWORDS = (
    "uiautomation", "ui automation", "ui 自动化", "自动化依赖", "自动化点击",
    "automation", "pyautogui", "pywinauto", "自动化",
)
_AUTO_LOGIN_TOOLTIP = (
    "默认关闭。开启后由程序代点登录窗口的「登录」按钮，省去手动点一下。\n"
    "若本机缺少 UI 自动化依赖，本开关会自动置灰并在原因里说明。")

#: 「无缓存密钥的账号自动走取密钥流程」开关的 tooltip（置灰时会在后面追加置灰原因）
_ACQUIRE_TOOLTIP = (
    "默认关闭。开启后，没有缓存密钥的账号会先自动走一遍取密钥（关微信 → 重启 →\n"
    "等你扫码登录 → 取密钥），再解密入库；需要你在弹窗里确认注入知情后才会生效。\n"
    "注意：本机当前微信版本取不到密钥（见顶部「已知限制」），开启后这些账号仍会失败。")

#: 契约不可用时的重试提示（两处复用）
_RETRY_HINT = "可查看下方运行日志；若为后端未就绪，请稍后重试。"


# ----------------------------------------------------------------------
# 契约解析工具（对 data / error 缺失做防御）
# ----------------------------------------------------------------------
def _unwrap(result: Any) -> tuple[bool, Optional[dict], str]:
    """按冻结契约解析 {"ok","data","error"}，任何异常形状都安全返回。

    Returns: (ok, data, error_text)
    """
    if not isinstance(result, dict):
        return False, None, "后端返回结构异常（不是字典）：%s" % type(result).__name__
    ok = bool(result.get("ok", False))
    data = result.get("data")
    data = data if isinstance(data, dict) else None
    error = result.get("error")
    error = str(error) if error else ""
    if not ok:
        return False, data, error or "后端返回 ok=False 且未给出原因"
    if data is None:
        return False, None, error or "后端返回缺少 data 字段"
    return True, data, ""


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _version_text(acc: dict) -> str:
    """版本号 + 版本族；未识别时给「未识别（按 4.x 兜底）」而不是留空。"""
    version = str(acc.get("version") or "").strip()
    family = str(acc.get("version_family") or "").strip()
    known_family = family if family in ("4.x", "3.x") else ""
    if version and known_family:
        return "%s（%s）" % (version, known_family)
    if version:
        return "%s（族未识别，按 4.x 兜底）" % version
    if known_family:
        return "未识别（%s，按 4.x 兜底）" % known_family
    return "未识别（按 4.x 兜底）"


def _key_state_text(acc: dict) -> str:
    state = str(acc.get("key_state") or "").strip().lower()
    if state == "cached":
        return "已缓存"
    if state == "none":
        return "无，需现取"
    return state or "未知"


def _yes_no(flag: Any) -> str:
    return "是" if flag else "否"


def _esc(text: Any) -> str:
    """把任意值转成可安全放进 Qt 富文本的字符串。"""
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _short_path(path: str) -> str:
    """把用户主目录前缀缩成 ~，让「库目录」列尽量不被省略号截断（完整路径进 tooltip）。"""
    if not path:
        return "-"
    try:
        home = os.path.expanduser("~")
    except Exception:  # noqa: BLE001
        home = ""
    if home and path.lower().startswith(home.lower()):
        return "~" + path[len(home):]
    return path


# ----------------------------------------------------------------------
# 后台线程
# ----------------------------------------------------------------------
class _DiscoverWorker(QThread):
    """后台调用 wechat_oneclick_discover()，不阻塞 UI。"""

    done = Signal(dict)

    def __init__(self, facade, parent=None) -> None:
        super().__init__(parent)
        self._facade = facade

    def run(self) -> None:  # noqa: D102
        try:
            result = self._facade.wechat_oneclick_discover()
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": "发现账号异常：%s: %s" % (type(e).__name__, e)}
        self.done.emit(result if isinstance(result, dict) else {"ok": False, "error": "返回结构异常"})


class _RunWorker(QThread):
    """后台调用 wechat_oneclick_run()，主线程用 QTimer 轮询进度。"""

    done = Signal(dict)

    def __init__(self, facade, kwargs: dict, parent=None) -> None:
        super().__init__(parent)
        self._facade = facade
        self._kwargs = kwargs

    def run(self) -> None:  # noqa: D102
        try:
            result = self._facade.wechat_oneclick_run(**self._kwargs)
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": "一键导入异常：%s: %s" % (type(e).__name__, e)}
        self.done.emit(result if isinstance(result, dict) else {"ok": False, "error": "返回结构异常"})


class _LegacyImportWorker(QThread):
    """降级通道：调用旧 importer.auto_import()，仅在契约不可用时启用。"""

    done = Signal(dict)

    def __init__(self, importer, kwargs: dict, parent=None) -> None:
        super().__init__(parent)
        self._importer = importer
        self._kwargs = kwargs

    def run(self) -> None:  # noqa: D102
        try:
            result = self._importer.auto_import(**self._kwargs)
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": "旧版导入异常：%s: %s" % (type(e).__name__, e)}
        self.done.emit(result if isinstance(result, dict) else {"ok": False, "error": "返回结构异常"})


class _PrecheckWorker(QThread):
    """后台调用 wechat_keyflow_precheck()，不阻塞 UI。"""

    done = Signal(dict)

    def __init__(self, facade, parent=None) -> None:
        super().__init__(parent)
        self._facade = facade

    def run(self) -> None:  # noqa: D102
        try:
            result = self._facade.wechat_keyflow_precheck()
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": "取密钥预检异常：%s: %s" % (type(e).__name__, e)}
        self.done.emit(result if isinstance(result, dict) else {"ok": False, "error": "返回结构异常"})


class _KeyflowRunWorker(QThread):
    """后台调用 wechat_keyflow_run()，主线程用 QTimer 轮询 wechat_keyflow_progress()。"""

    done = Signal(dict)

    def __init__(self, facade, kwargs: dict, parent=None) -> None:
        super().__init__(parent)
        self._facade = facade
        self._kwargs = kwargs

    def run(self) -> None:  # noqa: D102
        try:
            result = self._facade.wechat_keyflow_run(**self._kwargs)
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": "取密钥流程异常：%s: %s" % (type(e).__name__, e)}
        self.done.emit(result if isinstance(result, dict) else {"ok": False, "error": "返回结构异常"})


# ----------------------------------------------------------------------
# 面板
# ----------------------------------------------------------------------
class WechatExportWidget(QWidget):
    """微信一键导出面板。

    构造签名向后兼容：``WechatExportWidget(importer=None, monitor=None,
    facade=None, parent=None)``。
    """

    def __init__(self, importer=None, monitor=None, facade=None, parent=None) -> None:
        super().__init__(parent)
        # -- 注入依赖（全部可选，缺失时走降级）--
        self._importer = importer
        self._monitor = monitor
        self._facade = facade

        # -- 后台任务引用（防 GC）--
        self._discover_worker: Optional[_DiscoverWorker] = None
        self._run_worker: Optional[_RunWorker] = None
        self._legacy_worker: Optional[_LegacyImportWorker] = None
        self._precheck_worker: Optional[_PrecheckWorker] = None
        self._keyflow_worker: Optional[_KeyflowRunWorker] = None

        # -- 状态 --
        self._accounts: list = []
        self._running = False
        self._highrisk_consent = False
        self._auto_discovered = False
        self._legacy_mode = False

        # -- 取密钥流程状态 --
        self._keyflow_running = False
        self._keyflow_status: Optional[dict] = None   # 最近一次 precheck 的 data
        self._keyflow_stage_states: dict = {}         # stage -> {"ok":bool,"detail":str}
        self._inject_consent = False
        self._auto_prechecked = False
        # 「代点登录」不可用的持久判决：预检或运行结果一旦判定不可用就记下来，
        # 直到下一次预检刷新才重新评估（否则运行期的判决会被 gates 重算覆盖掉）。
        self._auto_login_blocked_reason = ""
        # 「无缓存自动取密钥」置灰理由的落地记录（避免每次重算 gates 都往日志里刷同一条）
        self._acquire_note = ""

        # -- 进度轮询（主线程 QTimer → wechat_oneclick_progress）--
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(700)
        self._poll_timer.timeout.connect(self._poll_progress)

        # -- 取密钥进度轮询（主线程 QTimer → wechat_keyflow_progress）--
        self._keyflow_timer = QTimer(self)
        self._keyflow_timer.setInterval(700)
        self._keyflow_timer.timeout.connect(self._poll_keyflow_progress)

        self.setObjectName("wechatExportRoot")
        self.setStyleSheet(_STYLE)
        # 把窗口底色显式刷成工程深色：QSS 的 background 在纯 QWidget 上要配合
        # WA_StyledBackground 才生效，这里用调色板 + autoFillBackground 双保险，
        # 保证面板在任何父容器（含未套深色主题的场景）下都是深底、浅字可读。
        self._paint_dark(self)
        self._build_ui()
        self._fill_table([])  # 初始就给出「尚未扫描」占位，不留空白表
        self._update_keyflow_gates()  # 初始按后端契约就绪情况置灰/启用

    # ------------------------------------------------------------------
    # 深色底（本面板的文案用浅色，底必须深；否则浅底浅字看不见）
    # ------------------------------------------------------------------
    @staticmethod
    def _paint_dark(widget: QWidget) -> None:
        """把 widget 的窗口底色刷成工程深色 #16213e（只动 Window 角色，不碰其它调色板角色）。"""
        try:
            pal = widget.palette()
            pal.setColor(QPalette.ColorRole.Window, QColor("#16213e"))
            widget.setPalette(pal)
            widget.setAutoFillBackground(True)
        except Exception as e:  # noqa: BLE001  纯观感问题，绝不影响功能
            logger.debug("面板底色设置跳过：%s", e)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # 外层只放一个滚动容器：设置对话框的 Tab 高度偏小时可滚动，避免内容被永久裁切；
        # 高度充足时（≥ 内容自然高度）不出现滚动条。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll)

        content = QWidget()
        content.setObjectName("wechatExportContent")
        self._scroll.setWidget(content)
        self._content = content
        self._paint_dark(content)  # 同根：内容区底必须是 #16213e，不能是调色板默认 #efefef

        root = QVBoxLayout(content)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        # —— 顶部卖点 + 紧随其后的已知限制（只讲卖点不讲限制就是过度承诺）——
        hint = QLabel(
            "点一次按钮即自动完成：发现库 → 识别版本 → 取密钥 → 解密 → 导入"
            "（无需命令行、无需手动注入或登录）。本机有已知限制，请先看下面一行。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8fd18f; padding: 2px;")
        root.addWidget(hint)

        self._limit_label = QLabel(_LIMITATION_HINT)
        self._limit_label.setWordWrap(True)
        self._limit_label.setObjectName("wechatExportLimit")
        # 琥珀色：醒目但不吓人（深底 #16213e 上对比度 ≈ 9:1）
        self._limit_label.setStyleSheet("color: #f0c674; padding: 2px 2px 4px 2px;")
        root.addWidget(self._limit_label)

        # —— 主体：一键导入 ——
        one = QGroupBox("一键导入")
        ov = QVBoxLayout(one)
        ov.setSpacing(6)

        btn_row = QHBoxLayout()
        self._btn_oneclick = QPushButton("一键导入微信聊天记录")
        self._btn_oneclick.setMinimumHeight(48)
        self._btn_oneclick.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_oneclick.setStyleSheet(
            "QPushButton { background: #d4af37; color: #1a1a2e; border: none;"
            " border-radius: 6px; font-size: 15pt; font-weight: bold; }"
            "QPushButton:hover { background: #f4d03f; }"
            "QPushButton:disabled { background: #555555; color: #999999; }")
        self._btn_oneclick.clicked.connect(self._on_oneclick)
        btn_row.addWidget(self._btn_oneclick, 1)

        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.setMinimumHeight(48)
        self._btn_cancel.setMinimumWidth(84)
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_cancel)
        ov.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        ov.addWidget(self._progress)

        info_row = QHBoxLayout()
        self._stage_label = QLabel("就绪：等待一键导入")
        self._stage_label.setStyleSheet("color: #e0e0e0;")
        info_row.addWidget(self._stage_label, 1)
        self._count_label = QLabel("第 0/0 个库 · 已导入 0 条")
        self._count_label.setStyleSheet("color: #c0c0c0;")
        info_row.addWidget(self._count_label)
        ov.addLayout(info_row)
        root.addWidget(one)

        # —— 链路说明：把「取密钥」和「一键导入」说成同一条链路 ——
        chain = QLabel(_CHAIN_HINT)
        chain.setWordWrap(True)
        chain.setStyleSheet("color: #8fd18f; padding: 2px;")
        root.addWidget(chain)

        # —— 取密钥（第 1–6 段）：关微信 → 重启 → 等登录 → 取密钥 ——
        kf = QGroupBox("取密钥（关微信 → 重启 → 等登录 → 取密钥）")
        kv = QVBoxLayout(kf)
        kv.setSpacing(4)

        kf_head = QHBoxLayout()
        kf_head.setSpacing(8)
        self._btn_keyprecheck = QPushButton("检测")
        self._btn_keyprecheck.setMinimumWidth(66)
        self._btn_keyprecheck.setToolTip(
            "检测微信目录 / 版本与版本来源 / 进程与主 PID / 管理员权限 /\n"
            "控制器 DLL 与导出名 / 特征码分支 / 本机是否具备取密钥条件。")
        self._btn_keyprecheck.clicked.connect(self._on_keyprecheck)
        kf_head.addWidget(self._btn_keyprecheck)

        self._precheck_view = QLabel("尚未检测（进面板会自动检测一次）")
        self._precheck_view.setWordWrap(True)
        self._precheck_view.setTextFormat(Qt.TextFormat.RichText)
        self._precheck_view.setStyleSheet("color: #c0c0c0;")
        kf_head.addWidget(self._precheck_view, 1)
        kv.addLayout(kf_head)

        kf_run = QHBoxLayout()
        kf_run.setSpacing(8)
        self._btn_keyflow_run = QPushButton("一键取密钥（自动关微信 → 重启 → 等你扫码）")
        self._btn_keyflow_run.setMinimumHeight(34)
        self._btn_keyflow_run.setMinimumWidth(180)
        self._btn_keyflow_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_keyflow_run.clicked.connect(self._on_keyflow_run)
        kf_run.addWidget(self._btn_keyflow_run, 1)

        self._btn_keyflow_cancel = QPushButton("取消取密钥")
        self._btn_keyflow_cancel.setMinimumHeight(34)
        self._btn_keyflow_cancel.setEnabled(False)
        self._btn_keyflow_cancel.clicked.connect(self._on_keyflow_cancel)
        kf_run.addWidget(self._btn_keyflow_cancel)

        self._ch_auto_login_click = QCheckBox("代点登录")
        self._ch_auto_login_click.setChecked(False)
        self._ch_auto_login_click.setToolTip(_AUTO_LOGIN_TOOLTIP)
        kf_run.addWidget(self._ch_auto_login_click)

        self._ch_acquire_if_missing = QCheckBox("无缓存密钥的账号自动走取密钥流程")
        self._ch_acquire_if_missing.setChecked(False)
        self._ch_acquire_if_missing.setToolTip(_ACQUIRE_TOOLTIP)
        kf_run.addWidget(self._ch_acquire_if_missing)
        kv.addLayout(kf_run)

        self._keyflow_progress = QProgressBar()
        self._keyflow_progress.setRange(0, 100)
        self._keyflow_progress.setValue(0)
        self._keyflow_progress.setMaximumHeight(22)
        kv.addWidget(self._keyflow_progress)

        self._keyflow_steps = QLabel()
        self._keyflow_steps.setWordWrap(True)
        self._keyflow_steps.setTextFormat(Qt.TextFormat.RichText)
        kv.addWidget(self._keyflow_steps)

        self._keyflow_detail = QLabel("就绪：等待取密钥")
        self._keyflow_detail.setWordWrap(True)
        self._keyflow_detail.setStyleSheet("color: #c0c0c0;")
        kv.addWidget(self._keyflow_detail)
        root.addWidget(kf)
        self._render_steps("")

        # —— 自动匹配到的账号列表 ——
        acc = QGroupBox("自动匹配到的微信账号")
        av = QVBoxLayout(acc)
        av.setSpacing(4)

        head = QHBoxLayout()
        self._btn_rescan = QPushButton("重新扫描")
        self._btn_rescan.setMinimumWidth(88)
        self._btn_rescan.clicked.connect(self._on_discover)
        head.addWidget(self._btn_rescan)
        self._scan_label = QLabel("尚未扫描")
        self._scan_label.setStyleSheet("color: #c0c0c0;")
        head.addWidget(self._scan_label, 1)
        av.addLayout(head)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
        for i, (_, w) in enumerate(_COLUMNS):
            self._table.setColumnWidth(i, w)
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Interactive)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.setMaximumHeight(104)
        av.addWidget(self._table)
        root.addWidget(acc)

        # —— 结果与失败原因 ——
        res = QGroupBox("结果与失败原因")
        rv = QVBoxLayout(res)
        rv.setSpacing(2)
        self._result_view = QTextEdit()
        self._result_view.setReadOnly(True)
        self._result_view.setPlaceholderText(
            "运行结束后，这里会显示：总条数、每个账号的库成功/失败数量与消息条数、"
            "密钥方式，以及每一条失败的原因 + 下一步动作。")
        self._result_view.setMinimumHeight(52)
        self._result_view.setMaximumHeight(70)
        rv.addWidget(self._result_view)
        root.addWidget(res)

        # —— 辅助（可选）：联系人筛选 / 实时监听 / 增量 ——
        aux = QGroupBox("辅助设置（可选）")
        aul = QHBoxLayout(aux)
        aul.setSpacing(8)
        aul.addWidget(QLabel("联系人筛选："))
        self._contact_combo = QComboBox()
        self._contact_combo.setMinimumWidth(150)
        self._contact_combo.addItem("全部联系人", None)
        aul.addWidget(self._contact_combo)
        self._ch_incremental = QCheckBox("增量导入（仅新增消息）")
        aul.addWidget(self._ch_incremental)
        self._ch_realtime = QCheckBox("启用实时监听（默认关闭）")
        self._ch_realtime.toggled.connect(self._on_toggle_realtime)
        aul.addWidget(self._ch_realtime)
        aul.addWidget(QLabel("间隔(秒)："))
        self._spin_interval = QSpinBox()
        self._spin_interval.setRange(2, 60)
        self._spin_interval.setValue(5)
        self._spin_interval.setMaximumWidth(70)
        aul.addWidget(self._spin_interval)
        aul.addStretch(1)
        root.addWidget(aux)

        # —— 高风险路线（默认关闭，必须显式知情）——
        hr = QGroupBox("高级 · 高风险取密钥（默认关闭）")
        hrv = QVBoxLayout(hr)
        hrv.setSpacing(4)

        hr_row = QHBoxLayout()
        hr_row.setSpacing(8)
        self._ch_high_risk = QCheckBox("启用高风险取密钥（DLL 注入）")
        self._ch_high_risk.setChecked(False)
        self._ch_high_risk.setToolTip(
            "高风险路线：把模块注入微信进程以抓取数据库密钥。\n"
            "· 注入期间若异常退出（被强杀等），微信可能需要重新启动；正常结束时无需手动处理；\n"
            "· 存在账号风控风险；\n"
            "· 仅在低风险路线（已缓存密钥等）都失败时才需要。\n"
            "注意：本机当前微信版本同样扫不到特征码，见顶部「已知限制」。")
        self._ch_high_risk.toggled.connect(self._on_toggle_high_risk)
        hr_row.addWidget(self._ch_high_risk)

        self._highrisk_status = QLabel("高风险通道：未扫描")
        self._highrisk_status.setStyleSheet("color: #c0c0c0;")
        hr_row.addWidget(self._highrisk_status)
        hr_row.addStretch(1)

        self._btn_highrisk_key = QPushButton("高风险取密钥")
        self._btn_highrisk_key.setEnabled(False)
        self._btn_highrisk_key.setStyleSheet(
            "QPushButton { background: #2a1a1a; color: #ff9b9b; border: 1px solid #e05a5a;"
            " padding: 6px 16px; border-radius: 3px; font-weight: bold; }"
            "QPushButton:hover { background: #3a1f1f; }"
            "QPushButton:disabled { background: #2a2a2a; color: #777777;"
            " border: 1px solid #555555; }")
        self._btn_highrisk_key.clicked.connect(self._on_acquire_key_high_risk)
        hr_row.addWidget(self._btn_highrisk_key)
        hrv.addLayout(hr_row)

        # 警告条默认隐藏（不占高度），勾选并确认知情后才出现
        self._highrisk_warn = QLabel(
            "⚠ 已启用高风险路线：DLL 注入微信进程；注入期间若异常退出，微信可能需要重新启动；"
            "存在账号风控风险。")
        self._highrisk_warn.setWordWrap(True)
        self._highrisk_warn.setStyleSheet("color: #ff9b9b; padding: 2px;")
        self._highrisk_warn.setVisible(False)
        hrv.addWidget(self._highrisk_warn)
        root.addWidget(hr)

        # —— 运行日志 ——
        log = QGroupBox("运行日志")
        lv = QVBoxLayout(log)
        lv.setSpacing(2)
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMinimumHeight(46)
        self._log_view.setMaximumHeight(60)
        lv.addWidget(self._log_view)
        root.addWidget(log)

        root.addStretch(1)

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: D102
        super().showEvent(event)
        # 进面板即自动扫描一次（只扫一次，之后靠「重新扫描」）
        if not self._auto_discovered:
            self._auto_discovered = True
            QTimer.singleShot(0, self._on_discover)
        # 取密钥预检同样自动跑一次（后端未就绪时只写人话提示，不报错）
        if not self._auto_prechecked:
            self._auto_prechecked = True
            QTimer.singleShot(0, self._on_keyprecheck)

    def resizeEvent(self, event) -> None:  # noqa: D102
        super().resizeEvent(event)
        # 富文本 QLabel 的 sizeHint 不按换行计算，宽度不够时第二行会被裁掉，
        # 所以每次尺寸变化都按真实宽度重新量一遍高度。
        self._fit_rich_labels()

    def _fit_rich_labels(self) -> None:
        """把三个富文本标签的高度按「真实宽度 + 实际 HTML」量出来，防裁行。"""
        for label in (getattr(self, "_precheck_view", None),
                      getattr(self, "_keyflow_steps", None),
                      getattr(self, "_keyflow_detail", None)):
            if label is None:
                continue
            html = label.text()
            if not html:
                continue
            doc = QTextDocument()
            doc.setDefaultFont(label.font())
            doc.setHtml(html)
            doc.setTextWidth(max(320, label.width()))
            need = int(doc.size().height()) + 4
            if label.minimumHeight() != need:
                label.setMinimumHeight(need)

    # ------------------------------------------------------------------
    # 契约调用helper
    # ------------------------------------------------------------------
    def _facade_fn(self, name: str):
        """取门面方法；不可用时返回 (None, 人话原因)。"""
        if self._facade is None:
            return None, "后端一键服务尚未就绪（facade 未注入）"
        fn = getattr(self._facade, name, None)
        if not callable(fn):
            return None, "后端一键服务尚未就绪（缺少接口 %s）" % name
        return fn, ""

    def _call_facade(self, name: str, *args, **kwargs):
        """统一调用 + 异常兜底。返回 (result_dict_or_None, 人话原因)。"""
        fn, why = self._facade_fn(name)
        if fn is None:
            return None, why
        try:
            result = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            return None, "调用 %s 异常：%s: %s" % (name, type(e).__name__, e)
        if not isinstance(result, dict):
            return None, "%s 返回结构异常（不是字典）" % name
        return result, ""

    # ------------------------------------------------------------------
    # 发现账号
    # ------------------------------------------------------------------
    def _on_discover(self) -> None:
        fn, why = self._facade_fn("wechat_oneclick_discover")
        if fn is None:
            self._scan_label.setText("自动匹配：" + why)
            self._append_log(why + "；仍可直接点击「一键导入」，由后端自行发现。")
            self._set_highrisk_status("高风险通道：未知", why)
            self._fill_table([])
            return
        if self._discover_worker is not None and self._discover_worker.isRunning():
            return
        self._scan_label.setText("正在扫描本机微信数据目录…")
        self._btn_rescan.setEnabled(False)
        self._discover_worker = _DiscoverWorker(self._facade, self)
        self._discover_worker.done.connect(self._on_discover_done)
        # QThread.finished 不带参数，不能直连 setEnabled(bool)，否则触发时 TypeError
        self._discover_worker.finished.connect(lambda: self._btn_rescan.setEnabled(True))
        self._discover_worker.start()

    def _on_discover_done(self, result: dict) -> None:
        ok, data, err = _unwrap(result)
        if not ok:
            self._scan_label.setText("扫描失败：" + err)
            self._append_log("自动匹配失败：" + err)
            self._fill_table([])
            return

        accounts = [a for a in _as_list(data.get("accounts")) if isinstance(a, dict)]
        self._accounts = accounts
        importable = [a for a in accounts if a.get("importable")]
        roots = [r for r in _as_list(data.get("roots_scanned")) if isinstance(r, dict)]
        tools = data.get("tools") if isinstance(data.get("tools"), dict) else {}
        total_db = data.get("total_db", 0)

        self._scan_label.setText(
            "已扫描 %d 个数据根目录，发现 %d 个账号（可导入 %d 个），共 %s 个库"
            % (len(roots), len(accounts), len(importable), total_db))
        self._append_log(
            "自动匹配完成：账号 %d 个（可导入 %d 个）；工具就绪情况：wx_key=%s sqlcipher3=%s "
            "frida=%s weflow=%s"
            % (len(accounts), len(importable),
               _yes_no(tools.get("wx_key_exe")), _yes_no(tools.get("sqlcipher3")),
               _yes_no(tools.get("frida")), _yes_no(tools.get("weflow"))))
        for line in _as_list(data.get("log")):
            self._append_log(str(line))

        self._fill_table(accounts)
        self._refresh_highrisk_status()

    def _fill_table(self, accounts: list) -> None:
        self._table.clearSpans()
        self._table.setRowCount(0)
        for a in accounts:
            row = self._table.rowCount()
            self._table.insertRow(row)

            chk = QTableWidgetItem("")
            chk.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            chk.setCheckState(
                Qt.CheckState.Checked if a.get("importable") else Qt.CheckState.Unchecked)
            if not a.get("importable"):
                chk.setToolTip(str(a.get("reason") or "该账号当前不可导入"))
            self._table.setItem(row, 0, chk)

            root_path = str(a.get("root") or "-")
            # 注释列只放「库总数」这一个数，明细进 tooltip，避免窄列被省略号截断
            db_cell = QTableWidgetItem(str(a.get("db_count", 0)))
            db_cell.setToolTip("库共 %s 个，其中消息库 %s 个"
                               % (a.get("db_count", 0), a.get("message_db_count", 0)))

            cells = (
                (1, QTableWidgetItem(str(a.get("display") or a.get("account_id") or "未知账号"))),
                (2, QTableWidgetItem(_version_text(a))),
                (3, QTableWidgetItem(_short_path(root_path))),
                (4, db_cell),
                (5, QTableWidgetItem("可导入" if a.get("importable") else "不可导入")),
                (6, QTableWidgetItem(_key_state_text(a))),
            )
            self._table.item(row, 0).setToolTip(
                str(a.get("reason") or "账号标识：%s" % a.get("account_id", "?")))
            for col, item in cells:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if col == 3:
                    item.setToolTip(root_path)          # 路径过长时靠 tooltip 看全
                elif not a.get("importable"):
                    item.setToolTip(str(a.get("reason") or ""))
                self._table.setItem(row, col, item)

        if not accounts:
            # 空表给一行人话占位，避免一大块空白
            self._table.insertRow(0)
            tip = QTableWidgetItem("尚未扫描到微信账号：点左侧「重新扫描」，或直接点上面的「一键导入」。")
            tip.setFlags(Qt.ItemFlag.NoItemFlags)
            self._table.setItem(0, 0, tip)
            self._table.setSpan(0, 0, 1, len(_COLUMNS))

    def _selected_account_ids(self) -> list:
        ids: list = []
        for row in range(self._table.rowCount()):
            chk = self._table.item(row, 0)
            if chk is None or chk.checkState() != Qt.CheckState.Checked:
                continue
            acc = self._accounts[row] if row < len(self._accounts) else {}
            aid = acc.get("account_id")
            if aid:
                ids.append(aid)
        return ids

    # ------------------------------------------------------------------
    # 一键导入
    # ------------------------------------------------------------------
    def _on_oneclick(self) -> None:
        if self._running:
            return
        fn, why = self._facade_fn("wechat_oneclick_run")
        if fn is None:
            self._degrade(why)
            return

        account_ids = self._selected_account_ids() or None
        consent = bool(self._highrisk_consent and self._ch_high_risk.isChecked())
        kwargs = {
            "account_ids": account_ids,
            "contact_filter": self._contact_filter_value(),
            "incremental": self._ch_incremental.isChecked(),
            "consent_high_risk": consent,
        }
        # 可选：无缓存密钥的账号先自动走一遍取密钥（必须先在弹窗里确认注入知情）
        if (self._ch_acquire_if_missing.isEnabled()
                and self._ch_acquire_if_missing.isChecked()):
            avail = self._keyflow_available()
            if avail is not True:
                # 与 _update_keyflow_gates / _on_keyflow_run 同一套判定标准（fail-closed）：
                # 可用性不是明确的 True，就绝不把 acquire_key_if_missing 传下去 ——
                # 这条支路会先关闭并重启微信，判定没通过就不许走。
                self._append_log(
                    "未启用「自动取密钥」：本机取密钥可用性为「%s」，该支路会先关闭并重启微信，"
                    "本次只导入已缓存密钥的账号（可先在取密钥区点「检测」确认）。"
                    % ("尚未确认" if avail is None else "不可用"))
                self._result_view.setPlainText(
                    "说明：本次没有为「无缓存密钥的账号」自动取密钥。\n\n"
                    "原因：本机取密钥可用性为「%s」——该支路会先自动关闭并重启你的微信，"
                    "判定没通过就不执行，以免白白重启一次。\n\n"
                    "本次只导入已缓存密钥的账号；如需尝试取密钥，请先在上方取密钥区点「检测」，"
                    "看到「可用性 可用」后再开启该开关。"
                    % ("尚未确认" if avail is None else "不可用"))
            else:
                if not self._confirm_injection():
                    self._stage_label.setText("已取消（未确认注入知情）")
                    self._result_view.setPlainText(
                        "已取消：开启「无缓存密钥的账号自动走取密钥流程」后，"
                        "需要先确认注入知情才会执行。\n\n"
                        "本次未启动导入。若只想导入已缓存密钥的账号，"
                        "请先关闭该开关再点「一键导入」。")
                    self._append_log("已取消一键导入：未确认注入知情。")
                    return
                if self._run_accepts_acquire():
                    kwargs["acquire_key_if_missing"] = True
                    kwargs["consent_injection"] = True
                    self._append_log("本次会为没有缓存密钥的账号自动走一遍取密钥"
                                     "（acquire_key_if_missing=True, consent_injection=True）。")
                else:
                    self._append_log("后端 wechat_oneclick_run 尚未支持 acquire_key_if_missing "
                                     "参数，本次不会自动取密钥；请先更新后端后再试。")

        self._running = True
        self._legacy_mode = False
        self._btn_oneclick.setEnabled(False)
        self._btn_rescan.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._progress.setValue(0)
        self._result_view.clear()
        self._count_label.setText("第 0/0 个库 · 已导入 0 条")
        self._stage_label.setText("正在启动一键导入…")
        self._append_log("开始一键导入：账号=%s 联系人=%s 增量=%s 高风险=%s 自动取密钥=%s"
                         % (account_ids or "全部自动匹配", kwargs["contact_filter"] or "全部",
                            kwargs["incremental"], consent,
                            kwargs.get("acquire_key_if_missing", False)))
        self._update_keyflow_gates()

        self._run_worker = _RunWorker(self._facade, kwargs, self)
        self._run_worker.done.connect(self._on_run_done)
        self._run_worker.start()
        self._poll_timer.start()

    def _degrade(self, why: str) -> None:
        """契约不可用时的降级：人话提示 + 可选旧版通道。"""
        self._append_log(why)
        if self._importer is None:
            self._result_view.setPlainText(
                "%s。\n\n旧版导入通道同样不可用（importer=None），请等后端接入后再试。" % why)
            self._stage_label.setText("后端一键服务尚未就绪")
            return

        box = QMessageBox(self)
        box.setWindowTitle("一键导入")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(why + "。")
        box.setInformativeText("本机的旧版导入通道可用。是否改用旧版通道继续导入？")
        yes_btn = box.addButton("使用旧版导入", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is yes_btn:
            self._start_legacy_import()

    def _start_legacy_import(self) -> None:
        self._running = True
        self._legacy_mode = True
        self._btn_oneclick.setEnabled(False)
        self._btn_rescan.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._progress.setValue(0)
        self._stage_label.setText("旧版通道导入中…")
        self._append_log("已切换到旧版导入通道。")
        self._legacy_worker = _LegacyImportWorker(
            self._importer,
            {"contact_filter": self._contact_filter_value(),
             "enable_high_risk": bool(self._highrisk_consent and self._ch_high_risk.isChecked())},
            self)
        self._legacy_worker.done.connect(self._on_run_done)
        self._legacy_worker.start()

    def _on_cancel(self) -> None:
        if not self._running:
            return
        if self._legacy_mode:
            try:
                if self._importer is not None and hasattr(self._importer, "cancel_import"):
                    self._importer.cancel_import()
            except Exception as e:  # noqa: BLE001
                self._append_log("旧版通道取消失败：%s" % e)
        else:
            result, why = self._call_facade("wechat_oneclick_cancel")
            if result is None:
                self._append_log("取消请求未送达：" + why)
            elif not result.get("ok"):
                self._append_log("取消失败：" + str(result.get("error") or "未知原因"))
        self._append_log("已请求取消，等待当前步骤收尾…")
        self._stage_label.setText("正在取消…")

    # ------------------------------------------------------------------
    # 进度轮询
    # ------------------------------------------------------------------
    def _poll_progress(self) -> None:
        if self._legacy_mode:
            return
        result, why = self._call_facade("wechat_oneclick_progress")
        if result is None:
            return
        ok, data, err = _unwrap(result)
        if not ok or data is None:
            return
        try:
            pct = int(data.get("percentage") or 0)
        except (TypeError, ValueError):
            pct = 0
        self._progress.setValue(max(0, min(100, pct)))
        stage_name = str(data.get("stage_name") or data.get("stage") or "处理中")
        self._stage_label.setText(stage_name)
        db_index = data.get("db_index", 0)
        db_total = data.get("db_total", 0)
        msg_count = data.get("message_count", 0)
        self._count_label.setText(
            "第 %s/%s 个库 · 已导入 %s 条" % (db_index, db_total, msg_count))
        if data.get("running") is False and self._stage_label.text() == "处理中":
            self._stage_label.setText("收尾中…")

    # ------------------------------------------------------------------
    # 结果
    # ------------------------------------------------------------------
    def _on_run_done(self, result: dict) -> None:
        self._poll_timer.stop()
        self._running = False
        self._btn_oneclick.setEnabled(True)
        self._btn_rescan.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._update_keyflow_gates()

        if self._legacy_mode:
            self._render_legacy_result(result)
            return

        ok, data, err = _unwrap(result)
        if not ok:
            self._progress.setValue(0)
            self._stage_label.setText("导入失败")
            self._result_view.setPlainText(
                "导入失败：\n%s\n\n可查看下方运行日志；若为后端未就绪，请稍后重试。" % err)
            self._append_log("导入失败：" + err)
            for line in _as_list((data or {}).get("log")):
                self._append_log(str(line))
            return

        self._progress.setValue(100)
        lines = self._format_run_lines(data)
        self._result_view.setPlainText("\n".join(lines))
        for line in lines:
            logger.info(line)
        for line in _as_list(data.get("log")):
            self._append_log(str(line))
        total = data.get("total_messages", 0)
        self._stage_label.setText("完成，共导入 %s 条消息" % total)
        self._append_log("一键导入结束：共 %s 条消息" % total)

    def _render_legacy_result(self, result: dict) -> None:
        self._progress.setValue(100)
        if not isinstance(result, dict):
            result = {}
        if not result.get("ok", True) and result.get("error"):
            self._stage_label.setText("旧版通道导入失败")
            self._result_view.setPlainText("旧版通道导入失败：\n%s" % result.get("error"))
            self._append_log("旧版通道失败：" + str(result.get("error")))
            return
        total = result.get("total", 0)
        used = _as_list(result.get("used"))
        self._stage_label.setText("旧版通道完成，共导入 %s 条" % total)
        self._result_view.setPlainText(
            "旧版通道完成，共导入 %s 条消息。\n使用方法：%s"
            % (total, "，".join(str(u.get("method")) for u in used if isinstance(u, dict)) or "未提供"))
        self._append_log("旧版通道完成：%s 条" % total)

    def _format_run_lines(self, data: dict) -> list:
        """把契约结果翻译成人话（总条数 / 每账号 / 失败原因+下一步）。"""
        lines: list = []
        total = data.get("total_messages", 0)
        lines.append("总计导入 %s 条消息。" % total)

        per = [a for a in _as_list(data.get("per_account")) if isinstance(a, dict)]
        if per:
            lines.append("")
            lines.append("各账号结果：")
            for a in per:
                lines.append(
                    "· 账号 %s（%s）：库成功 %s / 共 %s（失败 %s），消息 %s 条，密钥方式 %s（来源 %s）"
                    % (a.get("account_id", "未知"),
                       a.get("version_family") or "未识别",
                       a.get("dbs_ok", 0), a.get("dbs_total", 0), a.get("dbs_failed", 0),
                       a.get("messages", 0),
                       a.get("key_method") or "未使用", a.get("key_source") or "未知"))
        else:
            lines.append("")
            lines.append("各账号结果：后端未返回 per_account。")

        used = [u for u in _as_list(data.get("used")) if isinstance(u, dict)]
        if used:
            lines.append("")
            lines.append("取密钥方式汇总："
                         + "，".join("%s×%s" % (u.get("method", "?"), u.get("count", 0))
                                     for u in used))

        errors = [e for e in _as_list(data.get("errors")) if isinstance(e, dict)]
        lines.append("")
        if errors:
            lines.append("失败原因与下一步动作：")
            for i, e in enumerate(errors, 1):
                lines.append(
                    "%d) 【账号 %s / 步骤 %s】原因：%s　→　下一步：%s"
                    % (i, e.get("account_id") or "全局", e.get("step") or "未知",
                       e.get("reason") or "后端未提供原因",
                       e.get("next_action") or "后端未提供下一步动作"))
        else:
            lines.append("失败项：无。")
        return lines

    # ------------------------------------------------------------------
    # 高风险
    # ------------------------------------------------------------------
    def _set_highrisk_status(self, short: str, detail: str = "") -> None:
        """状态行只放短词；完整原因进 tooltip + 运行日志，避免把这一行撑宽。"""
        self._highrisk_status.setText(short)
        self._highrisk_status.setToolTip(detail or short)
        if detail:
            self._append_log("%s —— %s" % (short, detail))

    def _refresh_highrisk_status(self) -> None:
        result, why = self._call_facade("wechat_oneclick_highrisk_status")
        if result is None:
            self._set_highrisk_status("高风险通道：未知", why)
            return
        ok, data, err = _unwrap(result)
        if not ok or data is None:
            self._set_highrisk_status("高风险通道：不可用", err)
            return
        available = bool(data.get("available"))
        detail = str(data.get("reason") or ("环境就绪" if available else "环境不满足"))
        self._set_highrisk_status(
            "高风险通道：可用" if available else "高风险通道：不可用", detail)

    def _on_toggle_high_risk(self, on: bool) -> None:
        if on and not self._highrisk_consent:
            box = QMessageBox(self)
            box.setWindowTitle("高风险取密钥 · 请确认知情")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText("这是高风险路线，请先确认已了解风险。")
            box.setInformativeText(HIGH_RISK_WARNING)
            ok_btn = box.addButton("我已知情，仍然启用", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not ok_btn:
                self._ch_high_risk.blockSignals(True)
                self._ch_high_risk.setChecked(False)
                self._ch_high_risk.blockSignals(False)
                self._highrisk_warn.setVisible(False)
                self._btn_highrisk_key.setEnabled(False)
                self._append_log("已取消启用高风险路线（保持默认关闭）。")
                return
            self._highrisk_consent = True
            self._append_log(
                "用户已显式确认高风险路线：DLL 注入方式；异常退出时微信可能需要重新启动；"
                "存在账号风控风险；仅在低风险路线失败时才需要。")
        self._highrisk_warn.setVisible(bool(on))
        self._btn_highrisk_key.setEnabled(bool(on) and self._highrisk_consent)

    def _on_acquire_key_high_risk(self) -> None:
        if not (self._ch_high_risk.isChecked() and self._highrisk_consent):
            QMessageBox.information(
                self, "高风险取密钥",
                "请先勾选上方的「启用高风险取密钥路线」并确认知情，才能执行 DLL 注入取密钥。")
            return
        result, why = self._call_facade("wechat_oneclick_acquire_key_high_risk")
        if result is None:
            self._append_log("高风险取密钥未执行：" + why)
            QMessageBox.warning(self, "高风险取密钥", why)
            return
        ok, data, err = _unwrap(result)
        if ok:
            self._append_log("高风险取密钥已执行：" + str((data or {}).get("message") or "成功"))
            self._refresh_highrisk_status()
        else:
            self._append_log("高风险取密钥失败：" + err)
            QMessageBox.warning(self, "高风险取密钥", "执行失败：" + err)

    # ------------------------------------------------------------------
    # 取密钥流程（1–6 段：检测微信 → 关微信 → 重启 → 前台化 → 等扫码 → 取密钥）
    # ------------------------------------------------------------------
    def _on_keyprecheck(self) -> None:
        """调 wechat_keyflow_precheck()，把本机条件摊开给用户看。"""
        fn, why = self._facade_fn("wechat_keyflow_precheck")
        if fn is None:
            self._keyflow_status = None
            self._precheck_view.setText(
                '<span style="color:#ff9b9b">后端取密钥服务尚未就绪</span>'
                '<span style="color:#c0c0c0">（%s）</span>' % _esc(why))
            self._append_log("取密钥预检：" + why)
            self._update_keyflow_gates()
            return
        if self._precheck_worker is not None and self._precheck_worker.isRunning():
            return
        self._btn_keyprecheck.setEnabled(False)
        self._precheck_view.setText("正在检测微信环境…")
        self._precheck_worker = _PrecheckWorker(self._facade, self)
        self._precheck_worker.done.connect(self._on_keyprecheck_done)
        self._precheck_worker.finished.connect(
            lambda: self._btn_keyprecheck.setEnabled(True))
        self._precheck_worker.start()

    def _on_keyprecheck_done(self, result: dict) -> None:
        ok, data, err = _unwrap(result)
        if not ok or data is None:
            self._keyflow_status = None
            self._precheck_view.setText(
                '<span style="color:#ff9b9b">检测失败</span>'
                '<span style="color:#c0c0c0">：%s</span>' % _esc(err))
            self._append_log("取密钥预检失败：" + err)
            self._update_keyflow_gates()
            return
        # 新一次预检是最新信息：清掉上一次的持久判决，重新按本次数据评估
        self._auto_login_blocked_reason = ""
        self._auto_login_note = ""
        self._acquire_note = ""
        self._keyflow_status = data
        self._precheck_view.setText(self._format_precheck_html(data))
        self._fit_rich_labels()
        available = bool(data.get("available"))
        reason = str(data.get("reason") or ("本机条件满足" if available else "本机条件不满足"))
        self._append_log("取密钥预检：%s（版本 %s / 来源 %s / 管理员 %s / DLL %s）"
                         % ("可用" if available else "不可用",
                            data.get("version") or "未识别",
                            data.get("version_source") or "unknown",
                            _yes_no(data.get("is_admin")),
                            _yes_no(data.get("dll_ok"))))
        if reason:
            self._append_log("取密钥预检说明：" + reason)
        self._update_keyflow_gates()

    def _format_precheck_html(self, data: dict) -> str:
        """把预检结果压成 4 行富文本（太长会撑高面板，所以每行都收敛）。"""
        src_map = {"file_version": "文件版本", "version_dir": "目录名",
                   "registry_raw": "注册表", "unknown": "未识别"}
        src = str(data.get("version_source") or "unknown")
        # 契约写的是 ge_4.1.4 / lt_4.1.4，但真实后端实测会给出 ">=4.1.4" 这类写法：
        # 两种都认；都不认识时原样显示后端的值，不要吞成「未识别」。
        branch = str(data.get("signature_branch") or "")
        branch_zh = {"ge_4.1.4": "ge_4.1.4（≥4.1.4）",
                     ">=4.1.4": "ge_4.1.4（≥4.1.4）",
                     "lt_4.1.4": "lt_4.1.4（<4.1.4）",
                     "<4.1.4": "lt_4.1.4（<4.1.4）"}.get(branch)
        if not branch_zh:
            branch_zh = branch if branch and branch.lower() != "unknown" else "未识别"
        exports = [str(x) for x in _as_list(data.get("dll_exports"))]
        shown = "、".join(exports[:3]) + (" 等 %d 个" % len(exports) if len(exports) > 3 else "")
        pids = _as_list(data.get("pids"))
        main_pid = data.get("main_pid")
        available = bool(data.get("available"))
        reason = str(data.get("reason") or "")
        lines = [
            '微信目录 <span style="color:#8fb3e0">%s</span>　版本 <b>%s</b>（来源 %s）'
            % (_esc(_short_path(str(data.get("wechat_dir") or "未找到"))),
               _esc(data.get("version") or "未识别"), _esc(src_map.get(src, src))),
            '进程 %s%s　主 PID %s　管理员 %s（需要提权 %s）'
            % ("运行中" if data.get("process_running") else "未运行",
               ("（共 %d 个 PID）" % len(pids)) if pids else "",
               _esc(main_pid if main_pid is not None else "-"),
               "是" if data.get("is_admin") else "否",
               "是" if data.get("need_admin") else "否"),
            '控制器 DLL <b>%s</b>：%s%s'
            % ("正常" if data.get("dll_ok") else "不可用",
               _esc(_short_path(str(data.get("dll_path") or "-"))),
               ("　导出 %d 个（%s）" % (len(exports), _esc(shown))) if exports
               else "　导出：后端未提供"),
            '特征码分支 <b>%s</b>　可用性 <span style="color:%s">%s</span>%s'
            % (_esc(branch_zh), "#8fd18f" if available else "#ff9b9b",
               "可用" if available else "不可用",
               ("　原因：%s" % _esc(reason)) if reason else ""),
        ]
        return "<br>".join(lines)

    @staticmethod
    def _auto_login_capability(data: dict) -> tuple[bool, str]:
        """判断「代点登录」是否可用：后端显式字段优先，其次按依赖关键词兜底。

        后端契约里没有专门的字段，所以这里两种来源都看，避免留下
        「按了没反应」的开关。
        """
        if not isinstance(data, dict):
            return True, ""
        for key in ("auto_login_available", "allow_auto_login_click_available",
                    "ui_auto_available", "uia_available", "auto_click_available"):
            if key in data:
                reason = str(data.get("auto_login_reason") or data.get("reason") or "")
                return bool(data.get(key)), reason
        chunks = [str(data.get("reason") or ""), str(data.get("detail") or ""),
                  str(data.get("auto_login_reason") or "")]
        for item in _as_list(data.get("notes")):
            chunks.append(str(item))
        text = " ".join(x for x in chunks if x)
        low = text.lower()
        for kw in _AUTO_LOGIN_UNAVAILABLE_KEYWORDS:
            if kw in low:
                return False, text.strip() or kw
        return True, ""

    def _set_auto_login_unavailable(self, reason: str) -> None:
        """代点登录不可用：关掉并置灰，附人话原因（判决持久到下一次预检）。"""
        reason = str(reason or "本机缺少 UI 自动化依赖")
        self._auto_login_blocked_reason = reason
        self._ch_auto_login_click.blockSignals(True)
        self._ch_auto_login_click.setChecked(False)
        self._ch_auto_login_click.blockSignals(False)
        self._ch_auto_login_click.setEnabled(False)
        tip = "代点登录不可用（已自动置灰）：%s" % reason
        self._ch_auto_login_click.setToolTip(tip)
        if getattr(self, "_auto_login_note", "") != tip:
            self._auto_login_note = tip
            self._append_log(tip)

    def _keyflow_available(self):
        """最近一次 precheck 给出的 available：True / False / None（尚未确认，面板刚打开还没检测）。"""
        data = self._keyflow_status if isinstance(self._keyflow_status, dict) else None
        return data.get("available") if data else None

    def _update_keyflow_gates(self) -> None:
        """按「后端契约是否就绪 + 预检可用性 + 是否在跑」统一决定控件可用性。"""
        busy = bool(self._keyflow_running)
        oneclick_busy = bool(self._running)
        pre_ready = self._facade_fn("wechat_keyflow_precheck")[0] is not None
        run_ready = self._facade_fn("wechat_keyflow_run")[0] is not None
        one_ready = self._facade_fn("wechat_oneclick_run")[0] is not None
        data = self._keyflow_status if isinstance(self._keyflow_status, dict) else None
        available = self._keyflow_available()

        # 取密钥按钮的启用条件：**必须已经确认可用**（available is True），也就是 fail-closed。
        # 不能用「available is not False」：面板刚打开、还没跑过 precheck 时 available 是 None，
        # 那种状态下按钮可点，一按就会先关闭并重启用户的微信（auto_restart=True），
        # 然后才在注入那步失败 —— 用户的微信被白白重启一次，这个窗口必须堵掉。
        # 闸（单一判定标准）：整个「取密钥区」里凡是会产生副作用、也就是会去
        # **关闭 / 重启用户的微信**的控件，一律要求「只读预检已明确判定可用」
        # （available is True）才可点。available 为 None（未判定）或 False（判定不通过）
        # 一律置灰 —— 这就是「关微信必须发生在只读判定之后」在界面上的落地。
        # 判定口径只有 _keyflow_available() 一个来源，_on_keyflow_run() 入口的双保险
        # 也读它，不允许出现「按钮灰了但入口还能走」或反过来的双标准。
        keyflow_ok = available is True
        run_enabled = bool(run_ready and keyflow_ok and not busy and not oneclick_busy)
        disable_reason = ""
        if not run_ready:
            disable_reason = "后端取密钥服务尚未就绪（缺少接口 wechat_keyflow_run）"
        elif available is None:
            disable_reason = _NEED_PRECHECK_HINT
        elif not keyflow_ok:
            disable_reason = ("本机取密钥不可用：" +
                              str(data.get("reason") or "原因未提供，见上方检测结果"))

        # 「检测」是**只读**判定（只读版本 / 进程 / DLL，不碰微信进程、不关不重启），
        # 所以它不受这道闸限制，保持可点：否则判定不通过后用户没有任何复核手段。
        self._btn_keyprecheck.setEnabled(pre_ready and not busy)
        self._btn_keyflow_run.setEnabled(run_enabled)
        self._btn_keyflow_run.setToolTip(
            _KEYFLOW_RUN_TOOLTIP if run_enabled else (disable_reason or "暂不可用"))
        self._btn_keyflow_cancel.setEnabled(run_ready and busy)

        # 「无缓存密钥的账号自动走取密钥流程」同样会走「关微信 → 重启」，所以挂同一道闸；
        # 置灰后 _on_oneclick() 里的 isEnabled() 判断自然不会把 acquire_key_if_missing 传下去。
        acquire_enabled = bool(one_ready and keyflow_ok and not busy and not oneclick_busy)
        self._ch_acquire_if_missing.setEnabled(acquire_enabled)
        if acquire_enabled:
            self._ch_acquire_if_missing.setToolTip(_ACQUIRE_TOOLTIP)
            self._acquire_note = ""
        else:
            why_off = ("后端一键导入尚未就绪" if not one_ready else
                       (disable_reason or "正在运行，结束前不可改"))
            self._ch_acquire_if_missing.setToolTip(
                _ACQUIRE_TOOLTIP + "\n\n【已置灰】" + why_off)
            if not busy and not oneclick_busy:
                note = "无缓存自动取密钥已置灰：" + why_off
                if getattr(self, "_acquire_note", "") != note:
                    self._acquire_note = note
                    self._append_log(note)

        # 阶段文案：未确认可用时给一句人话；一旦有了确定结论（可用或不可用）就撤掉这句，
        # 否则检测已经跑完、结论是「不可用」时，这行还写着「请先点检测」就过期误导了。
        cur_detail = self._keyflow_detail.text()
        if run_ready and not busy and available is None:
            if cur_detail in ("", "就绪：等待取密钥"):
                self._keyflow_detail.setText(_NEED_PRECHECK_HINT)
        elif cur_detail == _NEED_PRECHECK_HINT:
            self._keyflow_detail.setText("就绪：等待取密钥")

        auto_ok, auto_reason = (True, "")
        if data is not None:
            auto_ok, auto_reason = self._auto_login_capability(data)
        if not keyflow_ok:
            # 同一道闸：取密钥区整体置灰时，代点登录开关也一样灰（它只在取密钥流程里才有意义）
            auto_ok, auto_reason = False, (
                "取密钥尚未确认可用（见上方检测结果）" if available is None
                else "本机取密钥不可用（见上方检测结果）")
        if auto_ok and self._auto_login_blocked_reason:
            # 运行期已判定不可用（例如 errors 里说明缺 uiautomation），
            # 预检没提这件事时也要沿用这个判决，不能又把它启用回来。
            auto_ok, auto_reason = False, self._auto_login_blocked_reason
        if not auto_ok:
            self._set_auto_login_unavailable(auto_reason)
        else:
            self._ch_auto_login_click.setEnabled(bool(keyflow_ok and run_ready and not busy))
            self._ch_auto_login_click.setToolTip(_AUTO_LOGIN_TOOLTIP)

        if not pre_ready and not busy:
            self._precheck_view.setText(
                '<span style="color:#ff9b9b">后端取密钥服务尚未就绪</span>'
                '<span style="color:#c0c0c0">（缺少接口 wechat_keyflow_precheck）</span>')
        if not run_ready and not busy:
            self._keyflow_detail.setText(
                "后端取密钥服务尚未就绪（缺少接口 wechat_keyflow_run）")

    def _render_steps(self, current: str = "", states: Optional[dict] = None) -> None:
        """九个 stage 画成一条步骤条：✓ 已完成 / ● 当前 / ○ 未开始 / ✗ 失败。"""
        states = states if isinstance(states, dict) else {}
        cur_idx = _STAGE_ORDER.get(current, -1)
        parts = []
        for idx, (key, name) in enumerate(KEYFLOW_STAGES):
            info = states.get(key)
            info = info if isinstance(info, dict) else None
            if info is not None and info.get("ok") is True:
                glyph, color = "✓", "#8fd18f"
            elif info is not None and info.get("ok") is False:
                glyph, color = "✗", "#ff9b9b"
            elif info is not None:
                glyph, color = "•", "#c0c0c0"
            elif 0 <= idx < cur_idx:
                glyph, color = "✓", "#8fd18f"
            elif idx == cur_idx:
                glyph, color = "●", "#d4af37"
            else:
                glyph, color = "○", "#9a9a9a"
            weight = "bold" if idx == cur_idx else "normal"
            parts.append('<span style="color:%s;font-weight:%s">%s %s</span>'
                         % (color, weight, glyph, name))
        sep = '<span style="color:#9a9a9a"> → </span>'
        self._keyflow_steps.setText(sep.join(parts))
        self._fit_rich_labels()

    def _confirm_injection(self) -> bool:
        """注入前的显式知情确认；只有点了确认按钮才返回 True。"""
        box = QMessageBox(self)
        box.setWindowTitle("取密钥 · 请确认注入知情")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("取密钥会先关闭并重启你的微信，再向微信进程写入 Hook；请先确认已知情。")
        box.setInformativeText(INJECT_WARNING)
        ok_btn = box.addButton("我已知情，开始取密钥", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(ok_btn)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        approved = box.clickedButton() is ok_btn
        self._inject_consent = bool(approved)
        self._append_log("注入知情确认：%s（consent_injection=%s）"
                         % ("已确认" if approved else "未确认", approved))
        return approved

    def _on_keyflow_run(self) -> None:
        if self._keyflow_running or self._running:
            return
        # 双保险：按钮被置灰时不执行。这个动作会先关闭并重启用户的微信，
        # 绝不能从「能不能取密钥尚未确认」（available 为 None）或「已确认不可用」的状态发出去。
        if not self._btn_keyflow_run.isEnabled():
            why_off = self._btn_keyflow_run.toolTip() or _NEED_PRECHECK_HINT
            self._keyflow_detail.setText(why_off)
            self._append_log("未执行取密钥：" + why_off)
            return
        fn, why = self._facade_fn("wechat_keyflow_run")
        if fn is None:
            self._keyflow_degrade(why)
            return
        # 注入前必须弹一次显式知情确认；不确认就什么都不做
        if not self._confirm_injection():
            self._keyflow_detail.setText("已取消：未确认注入知情，未执行任何注入。")
            return

        kwargs = {
            "consent_injection": True,
            "auto_restart": True,
            "allow_auto_login_click": bool(self._ch_auto_login_click.isEnabled()
                                           and self._ch_auto_login_click.isChecked()),
        }
        self._keyflow_running = True
        self._keyflow_stage_states = {}
        self._keyflow_progress.setValue(0)
        self._result_view.clear()
        self._keyflow_detail.setText("正在启动取密钥流程…")
        self._render_steps("")
        self._btn_keyflow_run.setEnabled(False)
        self._btn_keyflow_cancel.setEnabled(True)
        self._btn_keyprecheck.setEnabled(False)
        self._append_log("开始取密钥流程：consent_injection=True auto_restart=True "
                         "allow_auto_login_click=%s" % kwargs["allow_auto_login_click"])

        self._keyflow_worker = _KeyflowRunWorker(self._facade, kwargs, self)
        self._keyflow_worker.done.connect(self._on_keyflow_done)
        self._keyflow_worker.start()
        self._keyflow_timer.start()

    def _on_keyflow_cancel(self) -> None:
        result, why = self._call_facade("wechat_keyflow_cancel")
        if result is None:
            self._append_log("取密钥取消请求未送达：" + why)
        elif not result.get("ok"):
            self._append_log("取密钥取消失败：" + str(result.get("error") or "未知原因"))
        self._keyflow_detail.setText("正在取消取密钥，等待当前阶段收尾…")
        self._append_log("已请求取消取密钥。")

    def _poll_keyflow_progress(self) -> None:
        result, why = self._call_facade("wechat_keyflow_progress")
        if result is None:
            return
        ok, data, err = _unwrap(result)
        if not ok or data is None:
            return
        stage = str(data.get("stage") or "")
        name = str(data.get("stage_name") or "") or _STAGE_NAMES.get(stage, "处理中")
        try:
            pct = int(data.get("percentage") or 0)
        except (TypeError, ValueError):
            pct = 0
        self._keyflow_progress.setValue(max(0, min(100, pct)))
        self._render_steps(stage)
        idx = _STAGE_ORDER.get(stage, -1)
        head = "[%d/%d %s] " % (idx + 1, _STAGE_TOTAL, name) if idx >= 0 else ""
        detail = str(data.get("detail") or "")
        self._keyflow_detail.setText(head + (detail or name))
        self._fit_rich_labels()

    def _on_keyflow_done(self, result: dict) -> None:
        self._keyflow_timer.stop()
        self._keyflow_running = False
        self._btn_keyflow_cancel.setEnabled(False)
        self._btn_keyprecheck.setEnabled(True)

        ok, data, err = _unwrap(result)
        if not ok or data is None:
            self._keyflow_progress.setValue(0)
            self._render_steps("")
            self._keyflow_detail.setText("取密钥失败：" + err)
            self._fit_rich_labels()
            self._result_view.setPlainText(
                "取密钥失败：\n%s\n\n%s" % (err, _RETRY_HINT))
            self._append_log("取密钥失败：" + err)
            self._update_keyflow_gates()
            return

        stages = [s for s in _as_list(data.get("stages")) if isinstance(s, dict)]
        states: dict = {}
        for s in stages:
            key = str(s.get("stage") or "")
            if key:
                states[key] = {"ok": s.get("ok"), "detail": str(s.get("detail") or "")}
        self._keyflow_stage_states = states
        last_stage = str(stages[-1].get("stage") or "") if stages else ""
        self._render_steps(last_stage, states)

        acquired = bool(data.get("key_acquired"))
        self._keyflow_progress.setValue(
            100 if acquired else max(0, min(99, self._keyflow_progress.value())))
        self._keyflow_detail.setText(
            "取密钥完成：%s" % ("已拿到密钥（%s 字节）" % data.get("key_hex_len", 0)
                                if acquired else "未拿到密钥，见下方原因"))
        self._fit_rich_labels()
        self._result_view.setPlainText("\n".join(self._format_keyflow_lines(data)))
        for line in _as_list(data.get("log")):
            self._append_log(str(line))
        self._append_log("取密钥结束：key_acquired=%s key_hex_len=%s key_method=%s"
                         % (acquired, data.get("key_hex_len", 0),
                            data.get("key_method") or "未提供"))
        self._apply_run_capability_notes(data)
        self._update_keyflow_gates()

    def _apply_run_capability_notes(self, data: dict) -> None:
        """运行结果若说明「代点登录」不可用，连同原因一起置灰。"""
        for e in _as_list(data.get("errors")):
            if not isinstance(e, dict):
                continue
            reason = str(e.get("reason") or "")
            low = reason.lower()
            if reason and any(kw in low for kw in _AUTO_LOGIN_UNAVAILABLE_KEYWORDS):
                self._set_auto_login_unavailable(reason)
                return

    def _format_keyflow_lines(self, data: dict) -> list:
        """取密钥结果翻译成人话：九阶段逐条 + 密钥信息 + 失败原因与下一步。"""
        lines: list = []
        acquired = bool(data.get("key_acquired"))
        lines.append("取密钥结果：%s" % ("成功拿到密钥" if acquired else "未拿到密钥"))
        lines.append("密钥长度：%s 字节　方式：%s　来源：%s"
                     % (data.get("key_hex_len", 0),
                        data.get("key_method") or "未提供",
                        data.get("key_source") or "未提供"))
        lines.append("微信是否重启过：%s　是否已清理：%s"
                     % (_yes_no(data.get("wechat_restarted")),
                        _yes_no(data.get("cleanup_done"))))

        stages = [s for s in _as_list(data.get("stages")) if isinstance(s, dict)]
        lines.append("")
        if stages:
            lines.append("九个阶段逐条结果：")
            for s in stages:
                key = str(s.get("stage") or "?")
                name = str(s.get("stage_name") or "") or _STAGE_NAMES.get(key, key)
                mark = "✓ 完成" if s.get("ok") else "✗ 失败"
                detail = str(s.get("detail") or "")
                lines.append("· %s %s：%s"
                             % (mark, name, detail or "（后端未提供细节）"))
        else:
            lines.append("九个阶段逐条结果：后端未返回 stages。")

        errors = [e for e in _as_list(data.get("errors")) if isinstance(e, dict)]
        lines.append("")
        if errors:
            lines.append("失败原因与下一步动作：")
            for i, e in enumerate(errors, 1):
                lines.append("%d) 【步骤 %s】原因：%s　→　下一步：%s"
                             % (i, e.get("stage") or "未知",
                                e.get("reason") or "后端未提供原因",
                                e.get("next_action") or "后端未提供下一步动作"))
        else:
            lines.append("失败项：无。")
        return lines

    def _keyflow_degrade(self, why: str) -> None:
        """取密钥契约不可用：人话提示 + 置灰，不报错崩溃。"""
        self._precheck_view.setText(
            '<span style="color:#ff9b9b">后端取密钥服务尚未就绪</span>'
            '<span style="color:#c0c0c0">（%s）</span>' % _esc(why))
        self._keyflow_detail.setText("后端取密钥服务尚未就绪：" + why)
        self._append_log("取密钥：" + why)
        self._fit_rich_labels()
        self._update_keyflow_gates()

    def _run_accepts_acquire(self) -> bool:
        """探查 wechat_oneclick_run 是否已支持新增的 acquire_key_if_missing 参数。

        后端由另一个执行体并行实现，可能还没升级到新签名；这里既不硬传（会 TypeError），
        也不悄悄丢弃用户意图（会明确提示）。
        """
        fn, _why = self._facade_fn("wechat_oneclick_run")
        if fn is None:
            return False
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return False
        for p in sig.parameters.values():
            if p.kind is inspect.Parameter.VAR_KEYWORD:
                return True
        return "acquire_key_if_missing" in sig.parameters

    # ------------------------------------------------------------------
    # 旧入口：实时监听 / 联系人
    # ------------------------------------------------------------------
    def _contact_filter_value(self):
        """下拉框 → contact_filter（未选具体联系人时返回 None = 全部）。"""
        data = self._contact_combo.currentData()
        if data in (None, "", []):
            return None
        return [data]

    def _on_toggle_realtime(self, on: bool) -> None:
        if self._monitor is None:
            self._append_log("监听器未注入（monitor=None），实时监听不可用。")
            self._stage_label.setText("实时监听不可用：监听器未注入（monitor=None）")
            return
        try:
            if on:
                self._monitor.start(mode="polling", interval=self._spin_interval.value())
                self._append_log("已开启实时监听（轮询间隔 %s 秒）" % self._spin_interval.value())
            else:
                self._monitor.stop()
                self._append_log("已关闭实时监听。")
        except Exception as e:  # noqa: BLE001
            self._append_log("实时监听异常：%s" % e)

    # ------------------------------------------------------------------
    # 外部接口（向后兼容）
    # ------------------------------------------------------------------
    def set_contacts(self, contacts: list) -> None:
        """兼容旧接口。contacts: [{wxid, display_name}]"""
        self._contact_combo.clear()
        self._contact_combo.addItem("全部联系人", None)
        for c in contacts or []:
            if not isinstance(c, dict):
                continue
            label = c.get("display_name") or c.get("wxid") or "?"
            self._contact_combo.addItem(str(label), c.get("wxid") or label)

    def set_status(self, version: str = "", process: str = "",
                   db_path: str = "", key_state: str = "") -> None:
        """兼容旧接口：外部状态写入运行日志（新版不再单独展示状态行）。"""
        parts = []
        if version:
            parts.append("版本 %s" % version)
        if process:
            parts.append("进程 %s" % process)
        if db_path:
            parts.append("数据目录 %s" % db_path)
        if key_state:
            parts.append("密钥状态 %s" % key_state)
        if parts:
            self._append_log("外部状态：" + " · ".join(parts))

    # ------------------------------------------------------------------
    def _append_log(self, text: str) -> None:
        self._log_view.append(text)
        logger.info(text)


__all__ = ["WechatExportWidget"]
