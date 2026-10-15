"""
LoveAI v2.0 - 全局快捷键管理器
用 QShortcut + QKeySequence 实现应用内快捷键（阶段一先做应用内，全局快捷键留阶段二）。
"""
import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget, QApplication

logger = logging.getLogger(__name__)


class ShortcutManager(QObject):
    """全局快捷键管理器。

    快捷键表（可在 config.shortcuts 中修改）：
      Ctrl+Alt+L  → 主窗口显隐切换
      Ctrl+Alt+V  → 语音输入（阶段一占位）
      Ctrl+Alt+M  → 迷你状态栏（阶段一占位）
      Ctrl+Alt+Q  → 退出应用
      Ctrl+Alt+H  → 老板键（隐藏/恢复所有窗口）

    用法:
        sm = ShortcutManager(main_window, pet_window, signals, config)
    """

    #: 请求退出应用
    quit_requested = Signal()

    def __init__(self, main_window, pet_window, signals, config, parent: QObject = None):
        """
        Args:
            main_window: MainWindow 实例（用于显隐切换）
            pet_window: FloatBall 实例（桌宠）
            signals: AppSignals 全局信号
            config: ConfigService 配置服务
            parent: 父 QObject
        """
        super().__init__(parent)
        self._main_window = main_window
        self._pet_window = pet_window
        self._signals = signals
        self._config = config
        # R3.3-6: 由 application.py 注入 SpeechService 引用（语音管线走 VoicePipeline→SmartTurn→ChineseEoT）
        self.speech_service = None
        self._voice_pipeline = None

        self._shortcuts: list[QShortcut] = []
        self._boss_key_active = False  # 老板键状态

        self._register_shortcuts()
        logger.info("ShortcutManager 初始化完成")

    # ==================================================================
    # 注册快捷键
    # ==================================================================

    def register_all(self):
        """公开接口：注册所有全局快捷键。"""
        self._register_shortcuts()

    def _register_shortcuts(self):
        """从 config.shortcuts 读取键位并注册。"""
        # 快捷键映射：配置 key → 处理函数
        mapping = {
            "main_window":  self._toggle_main_window,
            "voice_input":  self._placeholder_voice,
            "mini_status":  self._placeholder_mini,
            "quit":         self._quit_app,
        }

        # 老板键固定从 privacy.boss_key 读取
        boss_key_seq = self._config.get("privacy.boss_key", "Ctrl+Alt+H")

        # 把老板键也加入
        all_combos = {boss_key_seq: self._boss_key}

        # 从 config.shortcuts 读取
        sc = self._config.get("shortcuts", {}) or {}
        for cfg_key, handler in mapping.items():
            seq_str = sc.get(cfg_key)
            if seq_str:
                all_combos[seq_str] = handler

        # 注册到主窗口作为上下文
        parent_widget = self._main_window or QApplication.activeWindow()
        if parent_widget is None:
            logger.warning("ShortcutManager: 没有可用的父窗口，快捷键未注册")
            return

        for seq_str, handler in all_combos.items():
            try:
                seq = QKeySequence(seq_str)
                sc = QShortcut(seq, parent_widget)
                sc.setContext(Qt.ApplicationShortcut)
                sc.activated.connect(handler)
                self._shortcuts.append(sc)
                logger.debug(f"快捷键注册: {seq_str} → {handler.__name__}")
            except Exception as e:
                logger.warning(f"快捷键注册失败 [{seq_str}]: {e}")

    # ==================================================================
    # 快捷键处理函数
    # ==================================================================

    def _toggle_main_window(self):
        """Ctrl+Alt+L：主窗口显隐切换。"""
        if self._main_window is None:
            return
        if self._main_window.isVisible():
            self._main_window.hide()
        else:
            self._main_window.show()
            self._main_window.raise_()
            self._main_window.activateWindow()
        logger.debug("快捷键：主窗口切换")

    def _placeholder_voice(self):
        """Ctrl+Alt+V：语音输入。R3.3-6 真接通 SpeechService.get_voice_pipeline()。

        旧实现：直接弹「语音功能开发中」QToolTip，VoicePipeline 内部接线但没人调。
        新实现：懒启动 VoicePipeline（含 SmartTurn + ChineseEoT 中文断句），
                切换录音/停止状态；无 SpeechService 时降级提示。
        """
        logger.info("快捷键：语音输入（启动 VoicePipeline）")
        from PySide6.QtWidgets import QToolTip

        svc = getattr(self, "speech_service", None)
        if svc is None:
            # 兜底：尝试从 services 注册表取
            try:
                from app.application import get_service_registry
                reg = get_service_registry()
                svc = reg.get("speech") if reg else None
            except Exception:
                svc = None

        tip_anchor = (self._pet_window.mapToGlobal(self._pet_window.rect().topLeft())
                      if self._pet_window else None)

        def _tip(msg: str):
            if tip_anchor is None:
                logger.info("语音提示: %s", msg)
                return
            try:
                QToolTip.showText(tip_anchor, msg, self._pet_window)
            except Exception:
                logger.info("语音提示: %s", msg)

        if svc is None or not hasattr(svc, "get_voice_pipeline"):
            _tip("语音服务未就绪")
            return

        try:
            vp = svc.get_voice_pipeline()
            if vp is None:
                _tip("语音管线初始化失败")
                return
            self._voice_pipeline = vp
            # 切换：在录就停，没在录就开
            if getattr(vp, "is_listening", lambda: False)():
                vp.stop()
                _tip("已停止录音")
            else:
                vp.start()
                _tip("聆听中…（再按一次停止）")
        except Exception as e:
            logger.warning("VoicePipeline 启动失败: %s", e)
            _tip(f"语音启动失败: {e}")

    def _placeholder_mini(self):
        """Ctrl+Alt+M：迷你状态栏（阶段一占位）。"""
        logger.info("快捷键：迷你状态栏（阶段一占位）")

    def _quit_app(self):
        """Ctrl+Alt+Q：退出应用。"""
        logger.info("快捷键：退出应用")
        self.quit_requested.emit()
        self._signals.app_quitting.emit()

    def _boss_key(self):
        """Ctrl+Alt+H：老板键，隐藏所有窗口，再按恢复。"""
        if self._boss_key_active:
            self._signals.show_all_windows.emit()
            self._boss_key_active = False
            logger.info("老板键：恢复所有窗口")
        else:
            self._signals.hide_all_windows.emit()
            self._boss_key_active = True
            logger.info("老板键：隐藏所有窗口")

    # ==================================================================
    # 清理
    # ==================================================================

    def unregister_all(self):
        """注销所有快捷键（应用退出时调用）。"""
        for sc in self._shortcuts:
            try:
                sc.setEnabled(False)
                sc.deleteLater()
            except RuntimeError:
                pass
        self._shortcuts.clear()
        logger.debug("所有快捷键已注销")
