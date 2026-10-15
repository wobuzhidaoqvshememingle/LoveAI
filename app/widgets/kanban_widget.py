"""
任务看板控件（需求 26.2.5）
- 四列看板：待办 / 进行中 / 已完成 / 已放弃，列标题带数量
- 每列渲染任务卡片（复用 common.task_card.TaskCard），展示标题 / 难度 / 进度 / 截止
- 待办、进行中卡片可一键「完成 / 放弃」，调用 ServiceFacade 后整板刷新并广播 board_changed
- 数据来自 ServiceFacade.get_taskboard（分桶）+ list_tasks（补全进度/截止等明细）
- 深色 indigo 主题
"""

from __future__ import annotations

import json
import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea,
)

from app.widgets.common.task_card import TaskCard

logger = logging.getLogger(__name__)

# 看板列：内部 key -> (对外中文标题, get_task_board 返回的分桶 key, 主题色)
_COLUMNS = [
    ("pending",     "待办",   "待开始", "#a1a1aa"),
    ("in_progress", "进行中", "进行中", "#6366f1"),
    ("completed",   "已完成", "已完成", "#34d399"),
    ("abandoned",   "已放弃", "已过期", "#71717a"),
]

_DIFFICULTY_CN = {
    "easy": "简单", "normal": "普通", "hard": "困难",
    "expert": "较难", "extreme": "极难", "": "普通",
}


class KanbanWidget(QWidget):
    """四列任务看板。"""

    board_changed = Signal()   # 完成/放弃任务后发出，主窗口据此联动快捷面板等

    def __init__(self, parent=None):
        super().__init__(parent)
        self._facade = None
        self._person_id: str = ""
        self._column_layers: dict[str, QVBoxLayout] = {}
        self._column_count_labels: dict[str, QLabel] = {}
        self._column_empty: dict[str, QLabel] = {}
        self._build_ui()
        self.setStyleSheet("background: transparent;")

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        for key, title, _bucket, color in _COLUMNS:
            col = QFrame()
            col.setObjectName("kanbanCol")
            col.setMinimumWidth(236)
            col.setStyleSheet(
                "QFrame#kanbanCol { background: rgba(255,255,255,0.02);"
                "border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; }"
            )
            cv = QVBoxLayout(col)
            cv.setContentsMargins(10, 10, 10, 10)
            cv.setSpacing(8)

            # 列头：色点 + 标题 + 数量
            head = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; background: transparent;")
            head.addWidget(dot)
            name = QLabel(title)
            name.setStyleSheet("color: #e5e5e8; font-weight: 600; background: transparent;")
            head.addWidget(name)
            head.addStretch(1)
            cnt = QLabel("0")
            cnt.setStyleSheet("color: #a1a1aa; background: transparent;")
            self._column_count_labels[key] = cnt
            head.addWidget(cnt)
            cv.addLayout(head)

            # 列体：纵向滚动
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setStyleSheet(
                "QScrollArea { border: none; background: transparent; }"
                "QScrollArea > QWidget > QWidget { background: transparent; }"
            )
            body = QWidget()
            body.setStyleSheet("background: transparent;")
            lay = QVBoxLayout(body)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(8)
            lay.addStretch(1)
            scroll.setWidget(body)
            cv.addWidget(scroll, stretch=1)

            empty = QLabel("暂无任务")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                "color:#555560; font-size:11px; padding:18px 4px;"
                "border:1px dashed rgba(255,255,255,0.10); border-radius:8px;"
                "background: transparent;"
            )
            lay.insertWidget(0, empty)
            self._column_empty[key] = empty

            self._column_layers[key] = lay
            outer.addWidget(col, stretch=1)

    # ------------------------------------------------------------------ #
    # 数据加载
    # ------------------------------------------------------------------ #
    def load_from_facade(self, facade, person_id: str) -> bool:
        """拉看板数据并渲染；异常时清空为空态。"""
        self._facade = facade
        self._person_id = person_id or ""
        # 先清空各列卡片（保留空态标签与尾部弹簧）
        for key, lay in self._column_layers.items():
            self._clear_cards(lay)

        buckets: dict[str, list] = {b: [] for _, _, b, _ in _COLUMNS}
        total = 0
        detail: dict[str, dict] = {}
        try:
            if facade is not None and self._person_id:
                resp = facade.get_task_board(self._person_id)
                if resp.get("ok"):
                    data = resp.get("data") or {}
                    raw_cols = data.get("columns") or {}
                    total = int(data.get("total") or 0)
                    for _, _t, bucket, _c in _COLUMNS:
                        buckets[bucket] = raw_cols.get(bucket) or []
                # 补全明细（进度/截止/任务类型）
                full = facade.list_tasks(self._person_id, "")
                if full.get("ok"):
                    for row in (full.get("data") or []):
                        if isinstance(row, dict) and row.get("id"):
                            detail[str(row["id"])] = row
        except Exception as e:  # noqa: BLE001
            logger.debug("看板加载失败（空态兜底）: %s", e)

        rendered = 0
        for key, _title, bucket, _color in _COLUMNS:
            tasks = buckets.get(bucket) or []
            self._column_count_labels[key].setText(str(len(tasks)))
            empty = self._column_empty[key]
            if not tasks:
                empty.show()
                continue
            empty.hide()
            lay = self._column_layers[key]
            for t in tasks:
                card = self._make_card(key, t, detail.get(str(t.get("id"))))
                # 插到尾部弹簧之前
                lay.insertWidget(lay.count() - 1, card)
                rendered += 1
        return rendered > 0 or total > 0

    def _clear_cards(self, lay: QVBoxLayout) -> None:
        """删除列内所有 TaskCard（保留空态标签 index0 与 addStretch 弹簧）。"""
        empty = None
        # 从后往前移除 widget 项
        for i in range(lay.count() - 1, -1, -1):
            item = lay.itemAt(i)
            w = item.widget()
            if isinstance(w, TaskCard):
                lay.removeWidget(w)
                w.deleteLater()
            elif isinstance(w, QLabel) and w.text() == "暂无任务":
                empty = w
        if empty is not None:
            empty.show()

    # ------------------------------------------------------------------ #
    def _make_card(self, col_key: str, board_task: dict, full_row: dict | None) -> TaskCard:
        row = full_row or board_task
        tid = str(board_task.get("id") or row.get("id") or "")
        title = board_task.get("title") or row.get("title") or "未命名任务"

        difficulty_raw = str(row.get("difficulty") or "normal")
        difficulty = _DIFFICULTY_CN.get(difficulty_raw, difficulty_raw)
        deadline = str(row.get("deadline") or "").strip() or "无"
        task_type = str(row.get("task_type") or "").strip()
        type_label = {"daily": "日常", "stage": "阶段", "challenge": "挑战"}.\
            get(task_type, task_type or "任务")

        progress = self._progress_pct(col_key, row)

        card = TaskCard()
        card.set_task({
            "id": tid,
            "title": title,
            "type": type_label,
            "difficulty": difficulty,
            "progress": progress,
            "deadline": deadline,
        })
        terminal = col_key in ("completed", "abandoned")
        card.set_actions_visible(not terminal)
        if not terminal:
            card.task_completed.connect(self._on_complete)
            card.task_abandoned.connect(self._on_abandon)
        return card

    @staticmethod
    def _progress_pct(col_key: str, row: dict) -> int:
        if col_key == "completed":
            return 100
        cur = row.get("current_step")
        total_steps = None
        steps_json = row.get("steps_json")
        if steps_json:
            try:
                parsed = json.loads(steps_json) if isinstance(steps_json, str) else steps_json
                if isinstance(parsed, list) and parsed:
                    total_steps = len(parsed)
            except (json.JSONDecodeError, TypeError):
                total_steps = None
        try:
            cur = int(cur or 0)
        except (ValueError, TypeError):
            cur = 0
        if total_steps:
            return max(0, min(100, int(cur / total_steps * 100)))
        return 50 if col_key == "in_progress" else 0

    # ------------------------------------------------------------------ #
    # 卡片操作
    # ------------------------------------------------------------------ #
    def _on_complete(self, task_id: str) -> None:
        if self._facade and task_id:
            try:
                self._facade.complete_task(task_id)
            except Exception as e:  # noqa: BLE001
                logger.debug("完成任务失败: %s", e)
        self.load_from_facade(self._facade, self._person_id)
        self.board_changed.emit()

    def _on_abandon(self, task_id: str) -> None:
        if self._facade and task_id:
            try:
                self._facade.abandon_task(task_id)
            except Exception as e:  # noqa: BLE001
                logger.debug("放弃任务失败: %s", e)
        self.load_from_facade(self._facade, self._person_id)
        self.board_changed.emit()
