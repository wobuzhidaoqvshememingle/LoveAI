# -*- coding: utf-8 -*-
"""
微信导出面板控件（PySide6 QWidget）

嵌入到 settings_dialog 的"微信导出"Tab 中。
不直接修改 settings_dialog.py，由外部在集成时 addTab(self, WechatExportWidget(), "微信导出")。
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QProgressBar,
    QCheckBox, QListWidget, QListWidgetItem, QGroupBox, QTextEdit,
    QFormLayout, QSpinBox,
)

logger = logging.getLogger(__name__)


class _ImportWorker(QThread):
    """后台执行 auto_import，避免阻塞 UI。"""
    progress = Signal(dict)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, importer, kwargs: dict) -> None:
        super().__init__()
        self._importer = importer
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._importer.auto_import(**self._kwargs)
            self.finished.emit(result)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class WechatExportWidget(QWidget):
    """微信导出面板。"""

    def __init__(self, importer=None, monitor=None, parent=None) -> None:
        super().__init__(parent)
        self._importer = importer
        self._monitor = monitor
        self._worker: Optional[_ImportWorker] = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # 状态区
        status_box = QGroupBox("微信状态")
        form = QFormLayout(status_box)
        self._version_label = QLabel("未检测")
        self._process_label = QLabel("未运行")
        self._dbpath_label = QLabel("-")
        self._keystate_label = QLabel("未提取")
        form.addRow("版本：", self._version_label)
        form.addRow("进程：", self._process_label)
        form.addRow("数据目录：", self._dbpath_label)
        form.addRow("密钥状态：", self._keystate_label)
        root.addWidget(status_box)

        # 操作区
        action_box = QGroupBox("操作")
        v = QVBoxLayout(action_box)

        row1 = QHBoxLayout()
        self._btn_import = QPushButton("一键导入聊天记录")
        self._btn_import.setMinimumHeight(36)
        self._btn_import.clicked.connect(self._on_import)
        row1.addWidget(self._btn_import)

        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.clicked.connect(self._on_cancel)
        row1.addWidget(self._btn_cancel)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        self._ch_realtime = QCheckBox("启用实时监听（默认关闭）")
        self._ch_realtime.toggled.connect(self._on_toggle_realtime)
        row2.addWidget(self._ch_realtime)
        row2.addWidget(QLabel("轮询间隔(秒)："))
        self._spin_interval = QSpinBox()
        self._spin_interval.setRange(2, 60)
        self._spin_interval.setValue(5)
        row2.addWidget(self._spin_interval)
        v.addLayout(row2)

        self._ch_high_risk = QCheckBox("启用高风险方法（DLL注入模拟，默认禁用）")
        self._ch_high_risk.toggled.connect(self._on_toggle_high_risk)
        v.addWidget(self._ch_high_risk)

        root.addWidget(action_box)

        # 进度
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        root.addWidget(self._progress)
        self._step_label = QLabel("就绪")
        root.addWidget(self._step_label)

        # 联系人多选
        contact_box = QGroupBox("联系人筛选（不选 = 全部）")
        cl = QVBoxLayout(contact_box)
        self._contact_list = QListWidget()
        self._contact_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self._contact_list.setMaximumHeight(120)
        cl.addWidget(self._contact_list)
        root.addWidget(contact_box)

        # 日志
        log_box = QGroupBox("导入日志")
        ll = QVBoxLayout(log_box)
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumHeight(140)
        ll.addWidget(self._log_view)
        root.addWidget(log_box)

        root.addStretch()

    # ------------------------------------------------------------------
    # 槽
    # ------------------------------------------------------------------
    def _on_import(self) -> None:
        if self._importer is None:
            self._append_log("导入器未注入（importer=None）")
            return
        contacts = [item.data(Qt.ItemDataRole.UserRole)
                    for item in self._contact_list.selectedItems()]
        self._btn_import.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._log_view.clear()
        self._worker = _ImportWorker(
            self._importer,
            kwargs={
                "contact_filter": contacts or None,
                "enable_high_risk": self._ch_high_risk.isChecked(),
            })
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_cancel(self) -> None:
        if self._importer is not None:
            self._importer.cancel_import()
        self._append_log("已请求取消")

    def _on_toggle_realtime(self, on: bool) -> None:
        if self._monitor is None:
            self._append_log("监听器未注入（monitor=None）")
            return
        try:
            if on:
                self._monitor.start(mode="polling",
                                     interval=self._spin_interval.value())
            else:
                self._monitor.stop()
        except Exception as e:  # noqa: BLE001
            self._append_log(f"实时监听异常: {e}")

    def _on_toggle_high_risk(self, on: bool) -> None:
        if on:
            self._append_log("⚠️ 已启用高风险方法：将尝试 DLL 注入模拟扫描")

    def _on_progress(self, p: dict) -> None:
        self._progress.setValue(p.get("percentage", 0))
        self._step_label.setText(f"[{p.get('step')}/{p.get('total_steps')}] "
                                 f"{p.get('current_step_name')} · "
                                 f"已导入 {p.get('message_count', 0)} 条")

    def _on_finished(self, result: dict) -> None:
        self._btn_import.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._progress.setValue(100)
        total = result.get("total", 0)
        self._step_label.setText(f"完成，共导入 {total} 条")
        self._append_log(f"导入完成：{total} 条消息，使用方法 {[u.get('method') for u in result.get('used', [])]}")

    def _on_failed(self, err: str) -> None:
        self._btn_import.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._append_log(f"❌ 失败：{err}")

    def _append_log(self, text: str) -> None:
        self._log_view.append(text)
        logger.info(text)

    # ------------------------------------------------------------------
    # 外部接口
    # ------------------------------------------------------------------
    def set_status(self, version: str = "", process: str = "",
                   db_path: str = "", key_state: str = "") -> None:
        if version:
            self._version_label.setText(version)
        if process:
            self._process_label.setText(process)
        if db_path:
            self._dbpath_label.setText(db_path)
        if key_state:
            self._keystate_label.setText(key_state)

    def set_contacts(self, contacts: list) -> None:
        """contacts: [{wxid, display_name}]"""
        self._contact_list.clear()
        for c in contacts:
            item = QListWidgetItem(c.get("display_name") or c.get("wxid", "?"))
            item.setData(Qt.ItemDataRole.UserRole, c.get("wxid"))
            self._contact_list.addItem(item)


__all__ = ["WechatExportWidget"]
