"""
任务卡片控件
- 显示任务标题、类型标签、难度、进度条、截止时间、完成/放弃按钮
- 完成信号 task_completed(str)，放弃信号 task_abandoned(str)
- 烫金暗色风格
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton
)
from PySide6.QtCore import Qt, Signal


class TaskCard(QWidget):
    """任务卡片"""

    task_completed = Signal(str)   # 发出任务 ID
    task_abandoned = Signal(str)  # 发出任务 ID

    def __init__(self, parent=None):
        super().__init__(parent)
        self._task_id = ""

        # --- 布局 ---
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # 第一行：标题 + 类型标签
        row_top = QHBoxLayout()
        self._title_label = QLabel("任务标题")
        self._title_label.setStyleSheet(
            "color: #f4d03f; font-size: 11pt; font-weight: bold;"
        )
        self._type_badge = QLabel("类型")
        self._type_badge.setObjectName("badge")
        row_top.addWidget(self._title_label, stretch=1)
        row_top.addWidget(self._type_badge)
        root.addLayout(row_top)

        # 第二行：难度 + 截止时间
        row_meta = QHBoxLayout()
        self._difficulty_label = QLabel("难度: -")
        self._difficulty_label.setStyleSheet("color: #b0b0c0; font-size: 9pt;")
        self._deadline_label = QLabel("截止: -")
        self._deadline_label.setStyleSheet("color: #b0b0c0; font-size: 9pt;")
        row_meta.addWidget(self._difficulty_label)
        row_meta.addStretch()
        row_meta.addWidget(self._deadline_label)
        root.addLayout(row_meta)

        # 第三行：进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFixedHeight(16)
        self._progress_bar.setStyleSheet(
            "QProgressBar { background-color: rgba(0,0,0,0.35);"
            "border:1px solid rgba(255,255,255,0.08); border-radius:4px;"
            "text-align:center; color:#ffffff; }"
            "QProgressBar::chunk { background-color:#27ae60; border-radius:3px; }"
        )
        root.addWidget(self._progress_bar)

        # 第四行：完成 / 放弃按钮
        row_btn = QHBoxLayout()
        row_btn.addStretch()
        self._btn_complete = QPushButton("完成")
        self._btn_complete.setObjectName("btnComplete")
        self._btn_complete.setCursor(Qt.PointingHandCursor)
        self._btn_abandon = QPushButton("放弃")
        self._btn_abandon.setObjectName("btnAbandon")
        self._btn_abandon.setCursor(Qt.PointingHandCursor)
        row_btn.addWidget(self._btn_complete)
        row_btn.addWidget(self._btn_abandon)
        root.addLayout(row_btn)

        # 卡片整体样式
        self.setObjectName("card")
        self.setMinimumHeight(140)

        # 信号绑定
        self._btn_complete.clicked.connect(self._on_complete)
        self._btn_abandon.clicked.connect(self._on_abandon)

    def set_task(self, task_dict: dict):
        """填充任务数据
        :param task_dict: 包含 id, title, type, difficulty, progress, deadline 等字段
        """
        self._task_id = str(task_dict.get("id", ""))
        self._title_label.setText(str(task_dict.get("title", "未命名任务")))
        self._type_badge.setText(str(task_dict.get("type", "类型")))
        self._difficulty_label.setText(
            f"难度: {task_dict.get('difficulty', '-')}"
        )
        self._deadline_label.setText(
            f"截止: {task_dict.get('deadline', '-')}"
        )
        progress = task_dict.get("progress", 0)
        try:
            self._progress_bar.setValue(int(progress))
        except (ValueError, TypeError):
            self._progress_bar.setValue(0)

    def set_actions_visible(self, visible: bool) -> None:
        """显示/隐藏「完成/放弃」操作按钮。

        看板的「已完成 / 已放弃」终态列只做展示，不再允许操作，调用本方法收起按钮。
        """
        self._btn_complete.setVisible(bool(visible))
        self._btn_abandon.setVisible(bool(visible))

    # ---- 按钮回调 ----
    def _on_complete(self):
        self.task_completed.emit(self._task_id)

    def _on_abandon(self):
        self.task_abandoned.emit(self._task_id)
