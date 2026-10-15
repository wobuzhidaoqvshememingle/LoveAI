# -*- coding: utf-8 -*-
"""
pet_view.py - Qt3DWindow 封装：桌宠窗口

设计要点：
    - Qt3DWindow 本身不是 QWidget，无法直接 setLayout / addWidget。
      通过 QWidget.createWindowContainer() 把 3D 窗口包成一个 QWidget，
      再把它放进一个顶层 QWidget（FramelessWindowHint + WA_TranslucentBackground）。
    - 鼠标拖拽移动窗口，松开时回弹（播放 reminder 动画）。
    - 右键菜单：主界面 / 回复建议 / 情感咨询 / 当前任务 / 切换对象 / 隐藏。
    - play_emotion(emotion) 切换骨骼动画。
    - show_bubble(text) 在窗口上方弹出气泡 QLabel。
    - start_blink() / stop_blink() 用 QTimer 控制窗口透明度做闪烁提醒。

透明说明：
    Qt3DWindow 默认 clear color 设为 (0,0,0,0)，容器 QWidget 设
    WA_TranslucentBackground + FramelessWindowHint。不同平台/驱动下
    OpenGL 透明合成效果可能不一致；若透明效果不理想，可先改为深色
    背景（QColor(30, 30, 40)）保证功能可用，后续再调透明。
"""

import os

from PySide6.QtCore import (
    Qt,
    QTimer,
    QPoint,
    QRect,
)
from PySide6.QtGui import (
    QColor,
    QPainter,
    QFont,
    QCursor,
    QAction,
    QVector3D,
)
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QMenu,
    QApplication,
)
from PySide6.Qt3DCore import Qt3DCore
from PySide6.Qt3DExtras import Qt3DExtras
from PySide6.Qt3DRender import Qt3DRender
QEntity = Qt3DCore.QEntity
QTransform = Qt3DCore.QTransform
Qt3DWindow = Qt3DExtras.Qt3DWindow
QPhongMaterial = Qt3DExtras.QPhongMaterial
QDirectionalLight = Qt3DRender.QDirectionalLight
QCamera = Qt3DRender.QCamera

# 同目录模块
from .skeleton import build_skeleton_entity, SKELETON_DATA, get_default_transform
from .animations import BoneAnimator, ANIMATIONS, play_animation
from .gltf_loader import try_load_custom_model


class PetView(Qt3DWindow):
    """
    3D 桌宠渲染窗口。

    用法：
        view = PetView()
        container = view.get_container()
        container.show()
    """

    # 气泡默认停留时间（毫秒）
    BUBBLE_DURATION = 4000

    def __init__(self, parent=None):
        super().__init__(parent)

        # ---- 渲染背景：透明（若驱动不支持会退化为黑底，注释里已说明） ----
        self.setClearColor(QColor(0, 0, 0, 0))

        # ---- 场景根节点 ----
        self._root_entity = QEntity()

        # ---- 光照 ----
        self._light = QDirectionalLight(self._root_entity)
        self._light.setWorldDirection(QVector3D(0.5, -1.0, -0.8))
        light_transform = QTransform()
        light_transform.setTranslation(QVector3D(2, 4, 5))
        self._light.addComponent(light_transform)

        # ---- 相机 ----
        camera = self.camera()
        camera.setPosition(QVector3D(0.0, 1.5, 4.0))
        camera.setViewCenter(QVector3D(0.0, 1.0, 0.0))
        camera.setNearPlane(0.1)
        camera.setFarPlane(100.0)

        # ---- 骨骼人形（先尝试加载自定义 GLTF，失败则用程序生成） ----
        self._bone_entities = {}
        custom_ent = try_load_custom_model()
        if custom_ent is not None:
            custom_ent.setParent(self._root_entity)
            # 自定义模型没有 bone_entities 映射，动画会跳过（不报错）
        else:
            _, self._bone_entities = build_skeleton_entity(self._root_entity)

        # ---- 动画播放器 ----
        self._animator = BoneAnimator(self._bone_entities, parent=self)

        # ---- 设置场景 ----
        self.setRootEntity(self._root_entity)

        # ---- 拖拽状态 ----
        self._drag_offset = None        # QPoint
        self._dragging = False

        # ---- 容器 QWidget（延迟到 get_container） ----
        self._container = None
        self._bubble_label = None
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._hide_bubble)

        # ---- 闪烁 ----
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(400)
        self._blink_state = False
        self._blink_timer.timeout.connect(self._toggle_blink)

        # 默认播放 idle
        QTimer.singleShot(100, lambda: self.play_emotion("idle"))

    # ------------------------------------------------------------------
    # 容器与窗口属性
    # ------------------------------------------------------------------
    def get_container(self):
        """
        把 Qt3DWindow 包装成 QWidget，方便设置无边框/置顶/子控件。

        Returns
        -------
        QWidget
            顶层容器，show() 即可。
        """
        if self._container is not None:
            return self._container

        container = QWidget()
        container.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool           # 不在任务栏显示
        )
        container.setAttribute(Qt.WA_TranslucentBackground, True)
        container.resize(400, 500)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        # 把 3D 窗口嵌进来
        three_widget = self.createWindowContainer(self, container)
        layout.addWidget(three_widget)

        # 安装事件过滤器：在 container 上捕获鼠标
        three_widget.setMouseTracking(True)
        three_widget.installEventFilter(self)
        container.installEventFilter(self)

        self._container = container
        return container

    # ------------------------------------------------------------------
    # 鼠标拖拽 + 右键菜单
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                self._dragging = True
                self._drag_offset = event.globalPosition().toPoint() - self._container.frameGeometry().topLeft()
                return True
            elif event.button() == Qt.RightButton:
                self._show_context_menu(event.globalPosition().toPoint())
                return True

        elif event.type() == QEvent.MouseMove:
            if self._dragging and self._drag_offset is not None:
                new_pos = event.globalPosition().toPoint() - self._drag_offset
                self._container.move(new_pos)
                return True

        elif event.type() == QEvent.MouseButtonRelease:
            if event.button() == Qt.LeftButton and self._dragging:
                self._dragging = False
                # 松开后回弹：播放 reminder
                self.play_emotion("reminder")
                return True

        return super().eventFilter(obj, event)

    def _show_context_menu(self, global_pos):
        """右键菜单。"""
        menu = QMenu()
        act_main = menu.addAction("打开主界面")
        act_reply = menu.addAction("回复建议")
        act_emo = menu.addAction("情感咨询")
        act_task = menu.addAction("当前任务")
        act_switch = menu.addAction("切换对象")
        menu.addSeparator()
        act_hide = menu.addAction("隐藏")

        chosen = menu.exec(global_pos)
        if chosen == act_main:
            self.show_bubble("主界面打开（待对接主程序）")
        elif chosen == act_reply:
            self.show_bubble("回复建议（待对接）")
        elif chosen == act_emo:
            self.show_bubble("情感咨询（待对接）")
        elif chosen == act_task:
            self.show_bubble("当前任务（待对接）")
        elif chosen == act_switch:
            self.show_bubble("切换对象（待对接）")
        elif chosen == act_hide:
            self._container.hide()

    # ------------------------------------------------------------------
    # 表情 / 动画
    # ------------------------------------------------------------------
    def play_emotion(self, emotion_name):
        """
        切换播放骨骼动画。

        Parameters
        ----------
        emotion_name : str
            ANIMATIONS 中的 key：idle / happy / confused / angry / sad / shy / reminder
        """
        if emotion_name not in ANIMATIONS:
            return
        play_animation(self._animator, emotion_name)

    # ------------------------------------------------------------------
    # 气泡提醒
    # ------------------------------------------------------------------
    def show_bubble(self, text, duration_ms=None):
        """
        在窗口上方弹出一个气泡文本。

        Parameters
        ----------
        text : str
        duration_ms : int, optional
            停留时间，默认 BUBBLE_DURATION。
        """
        if self._container is None:
            return

        # 复用旧 label
        if self._bubble_label is None:
            self._bubble_label = QLabel(self._container)
            self._bubble_label.setStyleSheet(
                "background-color: rgba(255, 255, 255, 230);"
                "color: #333;"
                "border: 1px solid #ccc;"
                "border-radius: 10px;"
                "padding: 6px 10px;"
            )
            self._bubble_label.setAlignment(Qt.AlignCenter)
            self._bubble_label.setWordWrap(True)
            self._bubble_label.setFont(QFont("Microsoft YaHei", 10))

        self._bubble_label.setText(text)
        self._bubble_label.adjustSize()

        # 放在容器顶部居中
        cw = self._container.width()
        lw = self._bubble_label.width()
        lh = self._bubble_label.height()
        self._bubble_label.setGeometry((cw - lw) // 2, 10, max(lw, 80), lh + 4)
        self._bubble_label.show()
        self._bubble_label.raise_()

        # 定时隐藏
        self._bubble_timer.start(duration_ms or self.BUBBLE_DURATION)

    def _hide_bubble(self):
        if self._bubble_label is not None:
            self._bubble_label.hide()

    # ------------------------------------------------------------------
    # 闪烁提醒
    # ------------------------------------------------------------------
    def start_blink(self):
        """开始窗口透明度闪烁。"""
        if self._container is None:
            return
        self._blink_timer.start()

    def stop_blink(self):
        """停止闪烁，恢复完全不透明。"""
        self._blink_timer.stop()
        if self._container is not None:
            self._container.setWindowOpacity(1.0)
        self._blink_state = False

    def _toggle_blink(self):
        if self._container is None:
            return
        self._blink_state = not self._blink_state
        self._container.setWindowOpacity(0.3 if self._blink_state else 1.0)
