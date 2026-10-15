"""
LoveAI v3.0 - 四档窗口管理器（原生QWidget桌面端）
统一管理主窗口/代聊悬浮窗/咨询小窗/桌宠悬浮球/迷你状态栏的创建、显示、隐藏和状态持久化。

四档窗口体系：
  第一档 main          —— 主窗口（完整功能面板，QMainWindow）
  第二档 chat_overlay  —— 代聊悬浮窗（贴在微信旁边，QWidget）
  第三档 consult_mini —— 情感咨询小窗（侧边弹出，QWidget）
  第四档 float_ball/pet —— 桌宠+悬浮球（常驻桌面，Qt3DWindow/QWidget）
  附加 status_bar      —— 迷你状态栏（QWidget）
"""
import json
import os
import logging
from typing import Dict, Optional, Any

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


class WindowManager(QObject):
    """四档窗口管理器，负责窗口生命周期和状态记忆。

    用法:
        wm = WindowManager(config_service, signals, bridge)
        wm.open_window('main')
        wm.hide_window('main')
    """

    #: 窗口位置/尺寸发生变化时发射: {window_name: {"x":int,"y":int,"width":int,"height":int}}
    window_state_changed = Signal(dict)

    #: 合法的窗口名称列表
    VALID_WINDOWS = ("main", "chat_overlay", "consult_mini", "float_ball", "pet", "status_bar", "immersive")

    def __init__(self, config: Any, signals: Any, facade: Any, parent: QObject = None):
        """
        Args:
            config: ConfigService 实例，用于读取 window 配置段
            signals: AppSignals 全局信号总线
            facade: ServiceFacade 服务门面（供窗口调用服务）
            parent: 父 QObject
        """
        super().__init__(parent)
        self._config = config
        self._signals = signals
        self._facade = facade
        self.windows: Dict[str, QWidget] = {}

        # 窗口状态缓存 {name: {"x":..,"y":..,"width":..,"height":..,"visible":bool}}
        self._state: Dict[str, dict] = {}
        self._state_path = self._config.get("window.state_path", "data/window_state.json")

        # 延迟保存定时器，避免拖动时频繁写盘
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._do_save_state)

        # 加载历史状态
        self._load_state()

        # 连接全局信号
        self._signals.open_window.connect(self.open_window)
        self._signals.hide_all_windows.connect(self.minimize_all)
        self._signals.show_all_windows.connect(self.show_all)
        self._signals.app_quitting.connect(self.close_all)

        logger.info("WindowManager 初始化完成")

    # ------------------------------------------------------------------
    # 窗口创建
    # ------------------------------------------------------------------

    def _create_window(self, name: str) -> Optional[QWidget]:
        """按档位创建对应窗口实例。

        Args:
            name: 窗口名称，必须在 VALID_WINDOWS 中

        Returns:
            创建好的 QWidget 实例；失败返回 None
        """
        try:
            if name == "main":
                from app.widgets.main_window import MainWindow
                win = MainWindow(facade=self._facade)
            elif name in ("float_ball", "pet"):
                # 桌宠和悬浮球：优先使用Qt3D桌宠，失败回退QWidget悬浮球
                try:
                    from app.qt3d_pet import PetView
                    win = PetView()
                    # Qt3DWindow需要包成QWidget才能统一管理
                    if hasattr(win, 'get_container'):
                        win = win.get_container()
                except Exception:
                    from app.widgets.float_ball import FloatBall
                    win = FloatBall(facade=self._facade)
                # 如果 float_ball 已存在（同一个实例），直接返回
                if "float_ball" in self.windows:
                    return self.windows["float_ball"]
                # 启动时按配置决定初始模式（ball / pet）
                try:
                    initial_mode = self._config.get("ui.float_ball_mode", "ball")
                    if hasattr(win, "set_mode"):
                        win.set_mode(initial_mode)
                except Exception:
                    logger.debug("读取 float_ball_mode 配置失败，使用 ball", exc_info=True)
            elif name == "chat_overlay":
                from app.widgets.chat_overlay import ChatOverlay
                win = ChatOverlay(facade=self._facade)
            elif name == "consult_mini":
                from app.widgets.consult_mini import ConsultMini
                win = ConsultMini(facade=self._facade)
            elif name == "status_bar":
                from app.widgets.status_bar import StatusBarMini
                win = StatusBarMini(facade=self._facade)
            elif name == "immersive":
                # 沉浸式语音桌面模式（全屏无边框，独立实例，不参与几何恢复）
                from app.widgets.immersive_mode import ImmersiveMode
                win = ImmersiveMode(facade=self._facade)
                try:
                    win.immersive_closed.connect(self._on_immersive_closed)
                except Exception:
                    pass
            else:
                logger.warning(f"未知窗口名称: {name}")
                return None

            # 恢复历史位置和尺寸（沉浸式全屏不参与）
            if name != "immersive":
                self._restore_geometry(win, name)

            # 监听窗口移动/缩放，触发状态保存（沉浸式全屏不持久化几何）
            if name != "immersive":
                geo = win.geometry()
                self._state[name] = {
                    "x": geo.x(), "y": geo.y(),
                    "width": geo.width(), "height": geo.height(),
                    "visible": False,
                }

            logger.info(f"窗口创建成功: {name}")
            return win

        except Exception as e:
            logger.error(f"创建窗口失败 [{name}]: {e}", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def open_window(self, name: str):
        """打开指定档位窗口；已存在则 show+raise，不存在则创建。

        Args:
            name: main / chat_overlay / consult_mini / float_ball / pet
        """
        if name not in self.VALID_WINDOWS:
            logger.warning(f"open_window: 非法窗口名 '{name}'")
            return

        # float_ball 和 pet 共享同一个实例
        lookup = "float_ball" if name == "pet" else name

        win = self.windows.get(lookup)
        if win is None:
            win = self._create_window(name)
            if win is None:
                return
            self.windows[lookup] = win
            # pet 别名也指向同一个实例
            if lookup == "float_ball":
                self.windows["pet"] = win

        # 如果是 'pet' 请求，切换为桌宠模式
        if name == "pet" and hasattr(win, "set_mode"):
            win.set_mode("pet")
        elif name == "float_ball" and hasattr(win, "set_mode"):
            win.set_mode("ball")

        # 互斥逻辑：主窗口与悬浮球不同时显示（沉浸式不参与互斥，自己管理全屏）
        if name == "immersive":
            # 进入沉浸式时隐藏主窗口和悬浮球，退出时由 _on_immersive_closed 处理
            mw = self.windows.get("main")
            if mw is not None and mw.isVisible():
                self._save_geometry(mw, "main")
                mw.hide()
            fb = self.windows.get("float_ball")
            if fb is not None and fb.isVisible():
                self._save_geometry(fb, "float_ball")
                fb.hide()
        elif name == "main":
            # 打开主窗口时隐藏悬浮球/桌宠
            fb = self.windows.get("float_ball")
            if fb is not None and fb.isVisible():
                self._save_geometry(fb, "float_ball")
                fb.hide()
                if "float_ball" in self._state:
                    self._state["float_ball"]["visible"] = False
        elif name in ("float_ball", "pet"):
            # 打开悬浮球/桌宠时隐藏主窗口
            mw = self.windows.get("main")
            if mw is not None and mw.isVisible():
                self._save_geometry(mw, "main")
                mw.hide()
                if "main" in self._state:
                    self._state["main"]["visible"] = False

        win.show()
        win.raise_()
        win.activateWindow()

        if name in self._state:
            self._state[name]["visible"] = True
        logger.debug(f"窗口已打开: {name}")

    def hide_window(self, name: str):
        """隐藏指定窗口。

        Args:
            name: 窗口名称
        """
        lookup = "float_ball" if name == "pet" else name
        win = self.windows.get(lookup)
        if win is not None:
            self._save_geometry(win, name)
            win.hide()
            if name in self._state:
                self._state[name]["visible"] = False
            logger.debug(f"窗口已隐藏: {name}")

    def _on_immersive_closed(self):
        """沉浸式模式关闭时回调：恢复悬浮球显示。"""
        fb = self.windows.get("float_ball")
        if fb is not None and not fb.isVisible():
            fb.show()
            fb.raise_()
            if "float_ball" in self._state:
                self._state["float_ball"]["visible"] = True
        logger.info("沉浸式模式已关闭，恢复悬浮球")

    def minimize_all(self):
        """最小化所有窗口，回到悬浮球（第四档），桌宠显示。"""
        for name, win in self.windows.items():
            if name in ("float_ball", "pet", "immersive"):
                continue
            if win.isVisible():
                self._save_geometry(win, name)
                win.hide()
                if name in self._state:
                    self._state[name]["visible"] = False
        # 确保悬浮球/桌宠显示（主窗口关闭后回到悬浮球模式）
        fb = self.windows.get("float_ball")
        if fb is not None and not fb.isVisible():
            fb.show()
            fb.raise_()
            if "float_ball" in self._state:
                self._state["float_ball"]["visible"] = True
        logger.info("所有窗口已最小化，回到悬浮球模式")

    def show_all(self):
        """恢复所有之前显示过的窗口。"""
        for name, win in self.windows.items():
            if name in ("float_ball", "pet", "immersive"):
                continue
            if self._state.get(name, {}).get("visible", False):
                win.show()
                win.raise_()
        logger.info("所有窗口已恢复")

    def close_all(self):
        """关闭所有窗口（应用退出时调用）。"""
        for name, win in list(self.windows.items()):
            try:
                self._save_geometry(win, name)
                win.close()
                win.deleteLater()
            except Exception as e:
                logger.warning(f"关闭窗口异常 [{name}]: {e}")
        self.windows.clear()
        self._save_timer.stop()
        self._do_save_state()
        logger.info("所有窗口已关闭")

    # ------------------------------------------------------------------
    # 状态持久化
    # ------------------------------------------------------------------

    def _restore_geometry(self, win: QWidget, name: str):
        """从缓存状态恢复窗口位置和尺寸。"""
        st = self._state.get(name)
        if st and "x" in st and "width" in st:
            try:
                win.setGeometry(st["x"], st["y"], st["width"], st["height"])
                logger.debug(f"窗口位置已恢复 [{name}]: {st['x']},{st['y']} {st['width']}x{st['height']}")
                return
            except Exception as e:
                logger.warning(f"恢复窗口位置失败 [{name}]: {e}")

        # 没有历史状态，用配置文件里的默认值
        if name == "main":
            pos = self._config.get("window.main_pos", [100, 100])
            size = self._config.get("window.main_size", [1100, 750])
            win.setGeometry(pos[0], pos[1], size[0], size[1])

    def _save_geometry(self, win: QWidget, name: str):
        """从窗口读取当前几何信息写入缓存，并触发延迟保存。"""
        try:
            geo = win.geometry()
            self._state[name] = {
                "x": geo.x(), "y": geo.y(),
                "width": geo.width(), "height": geo.height(),
                "visible": win.isVisible(),
            }
            self.window_state_changed.emit({name: self._state[name]})
            self._save_timer.start()  # 防抖
        except Exception as e:
            logger.warning(f"保存窗口几何失败 [{name}]: {e}")

    def _load_state(self):
        """从 JSON 文件加载窗口状态。"""
        if not os.path.exists(self._state_path):
            logger.debug(f"窗口状态文件不存在，使用默认: {self._state_path}")
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                self._state = json.load(f)
            logger.info(f"窗口状态已加载: {list(self._state.keys())}")
        except Exception as e:
            logger.error(f"加载窗口状态失败: {e}")
            self._state = {}

    def _do_save_state(self):
        """立即把状态缓存写入 JSON 文件。"""
        try:
            os.makedirs(os.path.dirname(self._state_path) or ".", exist_ok=True)
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
            logger.debug(f"窗口状态已保存: {self._state_path}")
        except Exception as e:
            logger.error(f"保存窗口状态失败: {e}")

    def get_window(self, name: str) -> Optional[QWidget]:
        """按名字获取窗口实例（可能尚未创建）。"""
        lookup = "float_ball" if name == "pet" else name
        return self.windows.get(lookup)

    def shutdown(self):
        """应用退出前调用：保存状态并清理信号连接。"""
        self.close_all()
        try:
            self._signals.open_window.disconnect(self.open_window)
            self._signals.hide_all_windows.disconnect(self.minimize_all)
            self._signals.show_all_windows.disconnect(self.show_all)
            self._signals.app_quitting.disconnect(self.close_all)
        except (RuntimeError, TypeError):
            pass
        logger.info("WindowManager 已关闭")
