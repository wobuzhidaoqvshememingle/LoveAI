"""
LoveAI-v2 悬浮球（FloatBall）— 双模式
- ball 模式：64×64 圆形 QWidget，金色圆 + 白心（paintEvent 绘制），左键菜单
- pet 模式：180×240 桌宠窗口，内嵌 QWebEngineView 加载 ui/pet_3d.html 的 3D 骨骼小人
- 两种模式都可拖拽；pet 模式下单击角色打开代聊窗
- new_message 时闪烁（windowOpacity 在 0.3 / 1.0 间切换）
"""

import os
import logging

from PySide6.QtCore import Qt, QTimer, QPoint, QRectF, QUrl, QEvent
from PySide6.QtGui import (
    QPainter, QBrush, QColor, QPen, QMouseEvent, QAction, QPainterPath,
)
from PySide6.QtWidgets import QWidget, QMenu

# QWebEngineView 可选依赖：不可用时 pet 模式自动回退纯 QPainter ball
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings
    _WEBENGINE_AVAILABLE = True
except Exception:  # noqa: BLE001
    QWebEngineView = None
    QWebEngineSettings = None
    _WEBENGINE_AVAILABLE = False

from app.service_facade import ServiceFacade
from app.signals import get_signals

logger = logging.getLogger(__name__)


def _pet_html_path() -> str:
    """ui/pet_3d.html 的绝对路径（相对本文件 ../ui/pet_3d.html）。"""
    return os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "ui", "pet_3d.html")
    )


class FloatBall(QWidget):
    """轻量级悬浮球 / 桌宠双模式窗口。"""

    BALL_SIZE = 64
    PET_SIZE = (180, 240)

    def __init__(self, facade: ServiceFacade, parent: QWidget = None):
        super().__init__(parent)
        self._facade = facade
        self._signals = get_signals()
        self._drag_pos: QPoint | None = None
        self._drag_origin: QPoint | None = None   # 按下时窗口的 topLeft
        self._blink_state = True
        self._mode = "ball"          # 当前模式 ball / pet
        self._web = None             # QWebEngineView 实例（懒加载）
        self._web_loaded = False

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setFixedSize(self.BALL_SIZE, self.BALL_SIZE)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # 闪烁定时器
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(400)
        self._blink_timer.timeout.connect(self._toggle_blink)

        self._connect_signals()

    # ------------------------------------------------------------------ #
    def _connect_signals(self) -> None:
        self._signals.new_message.connect(self.start_blink)
        self._signals.open_window.connect(self._on_open_window)
        self._signals.hide_all_windows.connect(self.hide)
        self._signals.show_all_windows.connect(self.show)

    def _on_open_window(self, name: str) -> None:
        # 悬浮球本身常驻，不响应 open_window；菜单里手动触发其他窗口
        pass

    # ------------------------------------------------------------------ #
    # 双模式核心：set_mode
    # ------------------------------------------------------------------ #
    def set_mode(self, mode: str) -> None:
        """切换 ball / pet 模式。WebEngine 不可用时 pet 自动回退 ball。"""
        new_mode = "pet" if mode == "pet" else "ball"
        if new_mode == self._mode:
            return

        if new_mode == "pet" and not _WEBENGINE_AVAILABLE:
            logger.warning("QWebEngineView 不可用，pet 模式回退为 ball")
            new_mode = "ball"

        self._mode = new_mode

        if new_mode == "ball":
            if self._web is not None:
                self._web.hide()
            self.setFixedSize(self.BALL_SIZE, self.BALL_SIZE)
            self.update()
        else:  # pet
            self._ensure_web()
            if self._web is not None:
                self._web.show()
                self._web.raise_()
            self.setFixedSize(self.PET_SIZE[0], self.PET_SIZE[1])
            # pet 模式下 paintEvent 跳过（web 覆盖整窗）
            self.update()

        logger.info("FloatBall 模式切换为: %s", self._mode)

    def current_mode(self) -> str:
        return self._mode

    def _ensure_web(self) -> None:
        """懒加载 QWebEngineView，加载 pet_3d.html。"""
        if self._web is not None or not _WEBENGINE_AVAILABLE:
            return
        try:
            self._web = QWebEngineView(self)
            # 透明背景，让桌宠之外的区域穿透
            self._web.setAttribute(Qt.WA_TranslucentBackground, True)
            self._web.setStyleSheet("background: transparent;")
            if QWebEngineSettings is not None:
                s = self._web.settings()
                s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
                s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
                s.setAttribute(QWebEngineSettings.WebGLEnabled, True)
            html_path = _pet_html_path()
            if os.path.exists(html_path):
                self._web.load(QUrl.fromLocalFile(html_path))
                self._web_loaded = True
            else:
                logger.warning("pet_3d.html 不存在: %s", html_path)
            # 转发鼠标事件到 FloatBall 的拖拽逻辑
            self._web.installEventFilter(self)
            self._web.resize(self.size())
            self._web.hide()
        except Exception:
            logger.error("创建 QWebEngineView 失败", exc_info=True)
            self._web = None

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        if self._web is not None:
            self._web.resize(self.size())

    # ------------------------------------------------------------------ #
    # 绘制：pet 模式跳过，ball 模式画金球
    # ------------------------------------------------------------------ #
    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt 签名
        # pet 模式下 QWebEngineView 覆盖整窗，跳过 QPainter 绘制
        if self._mode == "pet" and self._web is not None and self._web.isVisible():
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(2, 2, self.width() - 4, self.height() - 4)

        # 金色圆形背景（径向渐变感用两色叠加）
        p.setBrush(QBrush(QColor("#d4af37")))
        p.setPen(QPen(QColor("#ffd700"), 2))
        p.drawEllipse(rect)

        # 内层暗金，制造立体感
        inner = QRectF(8, 8, self.width() - 16, self.height() - 16)
        p.setBrush(QBrush(QColor("#b8860b")))
        p.setPen(Qt.NoPen)
        p.drawEllipse(inner)

        # 白色心形（用两条贝塞尔模拟）
        cx = self.width() / 2
        cy = self.height() / 2 + 2
        p.setBrush(QBrush(QColor("#fff8dc")))
        p.setPen(Qt.NoPen)

        path = QPainterPath()
        path.moveTo(cx, cy + 10)
        # 左半
        path.cubicTo(cx - 16, cy - 2, cx - 8, cy - 12, cx, cy - 4)
        # 右半
        path.cubicTo(cx + 8, cy - 12, cx + 16, cy - 2, cx, cy + 10)
        p.drawPath(path)
        p.end()

    # ------------------------------------------------------------------ #
    # 闪烁
    # ------------------------------------------------------------------ #
    def start_blink(self) -> None:
        if not self._blink_timer.isActive():
            self._blink_state = True
            self._blink_timer.start()

    def stop_blink(self) -> None:
        self._blink_timer.stop()
        self.setWindowOpacity(1.0)

    def _toggle_blink(self) -> None:
        self._blink_state = not self._blink_state
        self.setWindowOpacity(1.0 if self._blink_state else 0.3)

    # ------------------------------------------------------------------ #
    # ball 模式：左键菜单
    # ------------------------------------------------------------------ #
    def contextMenuEvent(self, event) -> None:  # noqa: ANN001
        if self._mode != "ball":
            return
        self._show_menu(event.globalPos())

    # ------------------------------------------------------------------ #
    # 整窗拖拽（ball 模式下由 FloatBall 自身接收；pet 模式下由 eventFilter 转发）
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_origin = self.frameGeometry().topLeft()
            self._drag_pos = event.globalPosition().toPoint() - self._drag_origin
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        # 短距离移动视为"单击"
        if self._drag_pos is not None:
            cur = self.frameGeometry().topLeft()
            moved = (cur - self._drag_origin).manhattanLength() if self._drag_origin else 0
            self._drag_pos = None
            self._drag_origin = None
            if moved < 5 and event.button() == Qt.LeftButton:
                if self._mode == "ball":
                    self._show_menu(event.globalPosition().toPoint())
                else:
                    # pet 模式：单击角色 → 打开代聊窗
                    self._signals.open_window.emit("chat_overlay")
                event.accept()
                return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------ #
    # pet 模式：转发 QWebEngineView 的鼠标事件到 FloatBall 拖拽逻辑
    # ------------------------------------------------------------------ #
    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        if obj is self._web and self._mode == "pet":
            et = event.type()
            if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_origin = self.frameGeometry().topLeft()
                self._drag_pos = event.globalPosition() - self._drag_origin
                return True  # 吞掉事件，不让 web 处理
            if et == QEvent.MouseMove and (event.buttons() & Qt.LeftButton) and self._drag_pos is not None:
                self.move(event.globalPosition() - self._drag_pos)
                return True
            if et == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                if self._drag_pos is not None:
                    cur = self.frameGeometry().topLeft()
                    moved = (cur - self._drag_origin).manhattanLength() if self._drag_origin else 0
                    self._drag_pos = None
                    self._drag_origin = None
                    if moved < 5:
                        # 单击 pet 角色 → 打开代聊窗
                        self._signals.open_window.emit("chat_overlay")
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ #
    # ball 模式菜单
    # ------------------------------------------------------------------ #
    def _show_menu(self, pos) -> None:  # noqa: ANN001
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #16213e; color: #f4d03f;"
            "border: 1px solid #d4af37; }"
            "QMenu::item:selected { background: #1a3a5c; }"
        )

        a_main = QAction("打开主界面", self)
        a_main.triggered.connect(lambda: self._signals.open_window.emit("main"))
        menu.addAction(a_main)

        a_chat = QAction("代聊窗", self)
        a_chat.triggered.connect(lambda: self._signals.open_window.emit("chat_overlay"))
        menu.addAction(a_chat)

        a_consult = QAction("咨询小窗", self)
        a_consult.triggered.connect(lambda: self._signals.open_window.emit("consult_mini"))
        menu.addAction(a_consult)

        menu.addSeparator()

        a_tasks = QAction("当前任务", self)
        a_tasks.triggered.connect(self._show_tasks)
        menu.addAction(a_tasks)

        a_switch = QAction("切换对象", self)
        a_switch.triggered.connect(self._switch_person)
        menu.addAction(a_switch)

        menu.addSeparator()

        # 切换桌宠 / 金球
        a_switch_mode = QAction("切换为桌宠模式" if self._mode != "pet" else "切换为金球模式", self)
        a_switch_mode.triggered.connect(self._toggle_mode)
        menu.addAction(a_switch_mode)

        menu.addSeparator()

        a_hide = QAction("隐藏所有窗口", self)
        a_hide.triggered.connect(self._signals.hide_all_windows.emit)
        menu.addAction(a_hide)

        menu.exec(pos if hasattr(pos, "x") else pos)

    def _toggle_mode(self) -> None:
        self.set_mode("pet" if self._mode != "pet" else "ball")

    def _show_tasks(self) -> None:
        try:
            r = self._facade.get_active_person()
            pid = (r.get("data") or {}).get("active_person_id") if r.get("ok") else None
            if not pid:
                return
            tr = self._facade.list_tasks(pid, "pending")
            tasks = tr.get("data") or []
            titles = "\n".join(f"· {t.get('title','?')}" for t in tasks[:8]) or "暂无待办"
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "当前任务", titles)
        except Exception:
            logger.debug("查询任务失败", exc_info=True)

    def _switch_person(self) -> None:
        try:
            r = self._facade.list_persons()
            if not r.get("ok"):
                return
            data = r.get("data") or {}
            persons = data.get("persons") or []
            active = data.get("active_person_id")
            if not persons:
                return
            # 简单轮转：取列表中第一个 != active 的
            next_pid = None
            for p in persons:
                if p.get("id") != active:
                    next_pid = p.get("id")
                    break
            if not next_pid:
                next_pid = persons[0].get("id")
            if next_pid:
                self._facade.switch_person(next_pid)
        except Exception:
            logger.debug("切换对象失败", exc_info=True)


# ===== 微信前台检测 + 快捷面板（offscreen 安全） =====
def detect_wechat_foreground() -> bool:
    """ctypes GetForegroundWindow + GetWindowText 检测微信是否在前台。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""
        return ("微信" in title) or ("Weixin" in title)
    except Exception:
        return False


def copy_to_clipboard(text: str) -> bool:
    """ctypes OpenClipboard + SetClipboardData CF_UNICODETEXT。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        CF_UNICODETEXT = 13
        if not user32.OpenClipboard(0): return False
        try:
            h = user32.GlobalAlloc(0x0042, (len(text) + 1) * 2)
            ctypes.windll.kernel32.GlobalLock(h)
            ctypes.memmove(h, text.encode("utf-16-le"), (len(text) + 1) * 2)
            ctypes.windll.kernel32.GlobalUnlock(h)
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_UNICODE, h)
            return True
        finally:
            user32.CloseClipboard()
    except Exception:
        return False
