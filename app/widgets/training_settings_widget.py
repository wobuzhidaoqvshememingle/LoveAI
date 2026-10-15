# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - AI 反哺训练设置面板（TrainingSettingsWidget）

一个独立的 QWidget，可作为 Tab 嵌入 settings_dialog，也可单独使用。
风格与 settings_dialog.py 保持一致（深色 #1a1a2e + 金色 #d4af37）。

包含：
  - 训练开关（默认关闭）
  - 训练频率下拉框 / 自动训练阈值
  - 数据管理：样本数、导出数据、清空数据（高风险需确认）
  - 模型版本列表：当前版本标记、数据量/loss/评分、回滚按钮
  - 训练进度：进度条 / 当前 loss / 预计时间
  - 手动训练按钮 / 本地模型路径配置
  - 隐私说明：所有数据只在本地处理

构造时可传入 collector / engine / version_manager；均为可选，
未注入时按钮进入“演示态”（点击弹提示），不影响面板渲染。
"""
from __future__ import annotations

import os
import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QPushButton, QComboBox, QCheckBox, QSpinBox, QProgressBar,
    QListWidget, QListWidgetItem, QMessageBox, QFileDialog,
)
from PySide6.QtCore import Qt, Signal

logger = logging.getLogger(__name__)

#: 与 settings_dialog.py 一致的配色
_STYLE = """
    QGroupBox { border: 1px solid #d4af37; border-radius: 4px; margin-top: 12px;
                padding-top: 12px; color: #d4af37; font-weight: bold; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
    QLabel { color: #e0e0e0; }
    QLineEdit, QComboBox, QSpinBox { background: #0f3460; color: #e0e0e0;
        border: 1px solid #d4af37; padding: 4px 8px; border-radius: 3px; }
    QPushButton { background: #d4af37; color: #1a1a2e; border: none;
        padding: 6px 16px; border-radius: 3px; font-weight: bold; }
    QPushButton:hover { background: #f4d03f; }
    QPushButton:disabled { background: #555; color: #999; }
    QCheckBox { color: #e0e0e0; spacing: 8px; }
    QListWidget { background: #0f3460; color: #e0e0e0; border: 1px solid #d4af37; }
    QProgressBar { background: #0f3460; color: #fff; border: 1px solid #d4af37;
        border-radius: 3px; text-align: center; }
    QProgressBar::chunk { background: #d4af37; }
"""


class TrainingSettingsWidget(QWidget):
    """AI 反哺训练设置面板。"""

    #: 设置变更时发射（key, value），由 service_facade 持久化到 config.json
    config_changed = Signal(str, object)
    #: 请求开始手动训练
    manual_train_requested = Signal()

    def __init__(self, collector=None, engine=None, version_manager=None,
                 parent=None) -> None:
        super().__init__(parent)
        self.collector = collector
        self.engine = engine
        self.version_manager = version_manager
        self.setStyleSheet(_STYLE)
        self._build_ui()
        self.refresh_all()

    # ------------------------------------------------------------------ #
    # UI 构建
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # —— 隐私说明（置顶）——
        privacy = QLabel("🔒 隐私说明：所有训练数据与模型微调都只在本机处理，不上传任何服务器。")
        privacy.setStyleSheet("color: #8fd18f; padding: 4px;")
        privacy.setWordWrap(True)
        root.addWidget(privacy)

        # —— 训练开关与频率 ——
        train_group = QGroupBox("训练开关")
        train_form = QFormLayout()
        self.train_enabled = QCheckBox("启用本地 AI 反哺训练（默认关闭）")
        self.train_enabled.toggled.connect(self._on_enabled_toggled)
        train_form.addRow(self.train_enabled)

        self.frequency = QComboBox()
        self.frequency.addItems(["手动", "实时", "每日", "每周", "每月"])
        self.frequency.currentIndexChanged.connect(self._on_frequency_changed)
        train_form.addRow("自动训练频率：", self.frequency)

        self.auto_threshold = QSpinBox()
        self.auto_threshold.setRange(10, 100000)
        self.auto_threshold.setSingleStep(50)
        self.auto_threshold.setValue(500)
        self.auto_threshold.setSuffix(" 条")
        self.auto_threshold.valueChanged.connect(self._on_threshold_changed)
        train_form.addRow("自动触发阈值：", self.auto_threshold)
        train_group.setLayout(train_form)
        root.addWidget(train_group)

        # —— 本地模型路径 ——
        model_group = QGroupBox("本地模型（LoRA 微调目标）")
        model_form = QFormLayout()
        path_row = QHBoxLayout()
        self.local_model_path = QLineEdit()
        self.local_model_path.setPlaceholderText("未配置本地模型则无法训练，仅支持云端生成")
        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._browse_model)
        path_row.addWidget(self.local_model_path)
        path_row.addWidget(browse_btn)
        model_form.addRow("基础模型路径：", path_row)
        model_group.setLayout(model_form)
        root.addWidget(model_group)

        # —— 数据管理 ——
        data_group = QGroupBox("训练数据")
        data_lay = QVBoxLayout()
        self.data_count_label = QLabel("已收集：0 条（可训练正样本 0 条）")
        data_lay.addWidget(self.data_count_label)
        data_btn_row = QHBoxLayout()
        self.export_btn = QPushButton("导出数据集")
        self.export_btn.clicked.connect(self._export_data)
        self.clear_btn = QPushButton("清空数据")
        self.clear_btn.setStyleSheet("background:#c0392b; color:#fff;")
        self.clear_btn.clicked.connect(self._clear_data)
        data_btn_row.addWidget(self.export_btn)
        data_btn_row.addWidget(self.clear_btn)
        data_btn_row.addStretch()
        data_lay.addLayout(data_btn_row)
        data_group.setLayout(data_lay)
        root.addWidget(data_group)

        # —— 训练进度 ——
        prog_group = QGroupBox("训练进度")
        prog_lay = QVBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        prog_lay.addWidget(self.progress_bar)
        self.progress_label = QLabel("状态：空闲")
        prog_lay.addWidget(self.progress_label)
        self.eta_label = QLabel("")
        prog_lay.addWidget(self.eta_label)
        btn_row = QHBoxLayout()
        self.train_btn = QPushButton("立即手动训练")
        self.train_btn.clicked.connect(self._manual_train)
        self.cancel_btn = QPushButton("取消训练")
        self.cancel_btn.clicked.connect(self._cancel_train)
        btn_row.addWidget(self.train_btn)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch()
        prog_lay.addLayout(btn_row)
        prog_group.setLayout(prog_lay)
        root.addWidget(prog_group)

        # —— 模型版本列表 ——
        ver_group = QGroupBox("模型版本（点“回滚”切换）")
        ver_lay = QVBoxLayout()
        self.version_list = QListWidget()
        ver_lay.addWidget(self.version_list)
        ver_group.setLayout(ver_lay)
        root.addWidget(ver_group)

        root.addStretch()

    # ------------------------------------------------------------------ #
    # 数据刷新（由外部定时器或保存后调用）
    # ------------------------------------------------------------------ #
    def refresh_all(self) -> None:
        """从 collector / engine / version_manager 拉取最新状态刷新面板。"""
        self._refresh_data_stats()
        self._refresh_versions()
        self._refresh_progress()

    def _refresh_data_stats(self) -> None:
        if self.collector is None:
            self.data_count_label.setText("已收集：（未接入数据收集器）")
            return
        try:
            s = self.collector.get_stats()
            self.data_count_label.setText(
                f"已收集：{s['total']} 条（可训练正样本 {s['positive_samples']} 条）")
        except Exception as e:  # noqa: BLE001
            self.data_count_label.setText(f"统计失败：{e}")

    def _refresh_versions(self) -> None:
        self.version_list.clear()
        if self.version_manager is None:
            self.version_list.addItem("（未接入版本管理器）")
            return
        try:
            current = self.version_manager.get_current_version()
            current_id = current.get("version_id") if current else None
            for v in self.version_manager.list_versions():
                marker = " ●当前" if v.get("version_id") == current_id else ""
                item = (f"{v.get('version_id')}{marker}  "
                        f"数据 {v.get('training_data_count', 0)} 条 / "
                        f"loss {v.get('final_loss')} / "
                        f"评分 {v.get('style_score')}  "
                        f"[{v.get('created_at')}]")
                list_item = QListWidgetItem(item)
                list_item.setData(Qt.UserRole, v.get("version_id"))
                self.version_list.addItem(list_item)
        except Exception as e:  # noqa: BLE001
            self.version_list.addItem(f"版本列表读取失败：{e}")

    def _refresh_progress(self) -> None:
        if self.engine is None:
            return
        try:
            st = self.engine.get_training_status()
        except Exception:  # noqa: BLE001
            return
        state = st.get("state", "idle")
        self.progress_bar.setValue(int(st.get("progress", 0)))
        loss = st.get("current_loss")
        self.progress_label.setText(
            f"状态：{state}　消息：{st.get('message', '')}"
            + (f"　当前 loss：{loss:.4f}" if loss is not None else ""))
        eta = st.get("estimated_remaining_sec")
        self.eta_label.setText(f"预计剩余：{eta:.0f} 秒" if eta else "")

    # ------------------------------------------------------------------ #
    # 事件回调
    # ------------------------------------------------------------------ #
    def _on_enabled_toggled(self, checked: bool) -> None:
        self.config_changed.emit("training.enabled", bool(checked))

    def _on_frequency_changed(self, idx: int) -> None:
        mapping = {0: "manual", 1: "realtime", 2: "daily", 3: "weekly", 4: "monthly"}
        self.config_changed.emit("training.frequency", mapping.get(idx, "manual"))

    def _on_threshold_changed(self, val: int) -> None:
        self.config_changed.emit("training.auto_threshold", int(val))

    def _browse_model(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择本地模型目录")
        if path:
            self.local_model_path.setText(path)
            self.config_changed.emit("training.local_model_path", path)

    def _export_data(self) -> None:
        if self.collector is None:
            QMessageBox.information(self, "提示", "未接入数据收集器")
            return
        fmt, ok = QFileDialog.getSaveFileName(
            self, "导出训练数据", "train_alpaca.json", "JSON (*.json)")
        if not ok:
            return
        try:
            out = self.collector.export_dataset("alpaca")
            QMessageBox.information(self, "导出成功", f"数据集已导出：\n{out}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(e))

    def _clear_data(self) -> None:
        if self.collector is None:
            return
        ret = QMessageBox.warning(
            self, "确认清空",
            "将删除全部本地训练数据，且不可恢复！确定继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            r = self.collector.clear_data()
            QMessageBox.information(self, "完成", f"已删除 {r.get('deleted', 0)} 条数据")
            self._refresh_data_stats()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "清空失败", str(e))

    def _manual_train(self) -> None:
        if self.engine is None:
            QMessageBox.information(self, "提示", "未接入训练引擎")
            return
        path = self.local_model_path.text().strip()
        if not path:
            QMessageBox.warning(self, "缺少配置", "未配置本地模型路径，无法训练。\n请先选择基础模型目录。")
            return
        r = self.engine.start_training({"local_model_path": path})
        if not r.get("ok"):
            QMessageBox.warning(self, "无法开始训练", r.get("reason", "未知原因"))
        else:
            QMessageBox.information(self, "已启动", "训练已在后台开始，请查看进度。")

    def _cancel_train(self) -> None:
        if self.engine is None:
            return
        r = self.engine.cancel_training()
        if not r.get("ok"):
            QMessageBox.information(self, "提示", r.get("reason", "当前没有训练任务"))
