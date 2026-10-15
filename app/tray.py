"""
LoveAI v2.0 - 系统托盘管理器
提供托盘图标、右键菜单、新消息闪烁、通知气泡。
"""
import os
import logging

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtGui import QIcon, QAction
from PySide6.QtWidgets import QSystemTrayIcon, QApplication, QMenu

logger = logging.getLogger(__name__)


class TrayManager(QObject):
    """系统托盘图标管理器。

    用法:
        tray = TrayManager(signals, config)
        tray.show()
    """

    #: 用户请求退出应用
    quit_requested = Signal()

    def __init__(self, signals, config, parent: QObject = None):
        """
        Args:
            signals: AppSignals 全局信号
            config: ConfigService 配置服务
            parent: 父 QObject
        """
        super().__init__(parent)
        self._signals = signals
        self._config = config

        # 创建托盘图标
        self._tray = QSystemTrayIcon()
        self._tray.setIcon(self._load_icon())
        self._tray.setToolTip("LoveAI v3.0")

        # 托盘菜单
        self._build_menu()

        # 新消息闪烁定时器
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._blink_step)
        self._blink_state = False

        # 连接信号：新消息时闪烁
        self._signals.new_message.connect(self._on_new_message)
        self._signals.app_quitting.connect(self.hide)

        # 双击托盘 → 打开主窗口
        self._tray.activated.connect(self._on_activated)

        logger.info("TrayManager 初始化完成")

    # ==================================================================
    # 初始化
    # ==================================================================

    def _load_icon(self) -> QIcon:
        """加载托盘图标；没有图标文件时用程序默认图标。"""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            icons_dir = os.path.join(project_root, "assets", "icons")
            for fname in ("app.ico", "app.png", "tray.ico", "tray.png"):
                path = os.path.join(icons_dir, fname)
                if os.path.exists(path):
                    icon = QIcon(path)
                    if not icon.isNull():
                        logger.debug(f"托盘图标已加载: {path}")
                        return icon
        except Exception as e:
            logger.warning(f"加载托盘图标失败: {e}")
        # 兜底：用应用程序默认图标
        return QApplication.windowIcon()

    def _build_menu(self):
        """构建托盘右键菜单。"""
        menu = QMenu()

        actions = [
            ("打开主界面",   lambda: self._signals.open_window.emit("main")),
            ("代聊窗口",     lambda: self._signals.open_window.emit("chat_overlay")),
            ("咨询小窗",     lambda: self._signals.open_window.emit("consult_mini")),
            ("显示桌宠",     lambda: self._signals.open_window.emit("pet")),
        ]
        for label, callback in actions:
            act = QAction(label, self)
            act.triggered.connect(callback)
            menu.addAction(act)

        menu.addSeparator()

        settings_act = QAction("设置", self)
        settings_act.triggered.connect(lambda: self._signals.open_window.emit("main"))
        menu.addAction(settings_act)

        menu.addSeparator()

        quit_act = QAction("退出", self)
        quit_act.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_act)

        self._tray.setContextMenu(menu)

    # ==================================================================
    # 公开接口
    # ==================================================================

    def show(self):
        """显示托盘图标。"""
        self._tray.show()
        logger.info("托盘图标已显示")

    def hide(self):
        """隐藏托盘图标。"""
        self._blink_timer.stop()
        self._tray.hide()
        logger.info("托盘图标已隐藏")

    def show_message(self, title: str, text: str, msec: int = 3000):
        """弹出托盘通知气泡。

        Args:
            title: 通知标题
            text: 通知内容
            msec: 显示时长（毫秒）
        """
        try:
            self._tray.showMessage(title, text, self._tray.icon(), msec)
        except Exception as e:
            logger.warning(f"托盘通知显示失败: {e}")

    # ==================================================================
    # 新消息闪烁
    # ==================================================================

    def _on_new_message(self, data: dict):
        """收到新消息时托盘图标闪烁 + 弹通知。"""
        if not self._config.get("notification.new_message", True):
            return
        try:
            content = data.get("content", "")
            direction = data.get("direction", "in")
            if direction == "in" and content:
                # 闪烁
                if not self._blink_timer.isActive():
                    self._blink_timer.start()
                # 通知
                self.show_message("新消息", content[:50], 2000)
        except Exception as e:
            logger.warning(f"处理新消息托盘提示失败: {e}")

    def _blink_step(self):
        """闪烁定时器：在正常图标和空图标之间切换。"""
        self._blink_state = not self._blink_state
        if self._blink_state:
            self._tray.setIcon(QIcon())  # 空图标
        else:
            self._tray.setIcon(self._load_icon())
            # 闪烁 3 次后停止（约 1.5 秒）
            self._blink_timer.stop()
            self._tray.setIcon(self._load_icon())

    # ==================================================================
    # 事件
    # ==================================================================

    def _on_activated(self, reason):
        """托盘激活事件：双击打开主窗口。"""
        if reason == QSystemTrayIcon.DoubleClick:
            self._signals.open_window.emit("main")
