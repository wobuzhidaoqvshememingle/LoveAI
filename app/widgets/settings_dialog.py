"""
LoveAI v3.0 - 设置面板（原生QWidget）
包含：大模型设置、微信设置、代理设置、通用设置、隐私设置、数据管理
"""
import os
import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget, QLabel,
    QLineEdit, QPushButton, QComboBox, QCheckBox, QGroupBox, QFormLayout,
    QTextEdit, QMessageBox, QFileDialog, QProgressBar, QSpinBox,
    QApplication, QSlider, QRadioButton, QButtonGroup,
)
from PySide6.QtCore import Qt, Signal

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """设置面板对话框，多Tab组织。"""

    settings_saved = Signal(dict)  # 设置保存时发射完整配置
    _scan_done = Signal(dict)       # 手动本地AI扫描完成（跨线程回主线程更新UI）

    def __init__(self, config, facade=None, parent=None):
        super().__init__(parent)
        self._config = config
        self._facade = facade
        self._scan_done.connect(self._on_scan_done)
        self.setWindowTitle("设置")
        self.setMinimumSize(700, 550)
        self.setStyleSheet("""
            QDialog { background: #1a1a2e; }
            QTabWidget::pane { border: 1px solid #d4af37; background: #16213e; }
            QTabBar::tab { background: #1a1a2e; color: #c0c0c0; padding: 8px 16px; }
            QTabBar::tab:selected { background: #16213e; color: #d4af37; border-bottom: 2px solid #d4af37; }
            QGroupBox { border: 1px solid #d4af37; border-radius: 4px; margin-top: 12px; padding-top: 12px; color: #d4af37; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            QLabel { color: #e0e0e0; }
            QLineEdit, QComboBox, QSpinBox { background: #0f3460; color: #e0e0e0; border: 1px solid #d4af37; padding: 4px 8px; border-radius: 3px; }
            QPushButton { background: #d4af37; color: #1a1a2e; border: none; padding: 6px 16px; border-radius: 3px; font-weight: bold; }
            QPushButton:hover { background: #f4d03f; }
            QCheckBox { color: #e0e0e0; spacing: 8px; }
            QTextEdit { background: #0f3460; color: #e0e0e0; border: 1px solid #d4af37; }
        """)
        self._build_ui()
        self._load_config()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_llm_tab(), "大模型")
        tabs.addTab(self._build_wechat_tab(), "微信")
        tabs.addTab(self._build_wechat_export_tab(), "微信导出")
        tabs.addTab(self._build_proxy_tab(), "代理")
        tabs.addTab(self._build_plugin_tab(), "插件")
        tabs.addTab(self._build_general_tab(), "通用")
        tabs.addTab(self._build_privacy_tab(), "隐私与数据")
        tabs.addTab(self._build_training_tab(), "AI 训练")
        layout.addWidget(tabs)

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存设置")
        save_btn.clicked.connect(self._save_config)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    # ---- 大模型设置 ----
    def _build_llm_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        # L3 云端模型
        cloud_group = QGroupBox("L3 云端模型（复杂推理）")
        form = QFormLayout()
        self.llm_api_key = QLineEdit()
        self.llm_api_key.setEchoMode(QLineEdit.Password)
        self.llm_api_base = QLineEdit()
        self.llm_model = QLineEdit()
        form.addRow("API Key：", self.llm_api_key)
        form.addRow("API Base：", self.llm_api_base)
        form.addRow("模型名称：", self.llm_model)
        cloud_group.setLayout(form)
        lay.addWidget(cloud_group)

        # 路由策略
        route_group = QGroupBox("模型路由策略")
        route_form = QFormLayout()
        self.route_strategy = QComboBox()
        self.route_strategy.addItems(["自动（按复杂度）", "优先云端", "优先本地", "仅本地"])
        route_form.addRow("路由策略：", self.route_strategy)
        self.l1_enabled = QCheckBox("启用 L1 小模型（简单回复）")
        self.l2_enabled = QCheckBox("启用 L2 本地大模型（LoRA微调）")
        route_form.addRow(self.l1_enabled)
        route_form.addRow(self.l2_enabled)
        route_group.setLayout(route_form)
        lay.addWidget(route_group)

        # 本地模型路径
        local_group = QGroupBox("本地模型路径")
        local_form = QFormLayout()
        self.l1_path = QLineEdit()
        self.l2_path = QLineEdit()
        local_form.addRow("L1 ONNX：", self.l1_path)
        local_form.addRow("L2 模型目录：", self.l2_path)
        local_group.setLayout(local_form)
        lay.addWidget(local_group)

        # 零成本 AI 供给来源（v3.2 接线：FreeModelRouter 读取下列 config 开关）
        zero_group = QGroupBox("零成本 AI 供给（免费/本地，无需付费 key）")
        zero_lay = QVBoxLayout()
        zero_hint = QLabel(
            "仅「本地扫描」默认开；网页搜索/桌面AI/自动补全/浏览器自动化默认关。\n"
            "网页搜索只在本地知识不足时补全上下文、不直接充当回复。发送永远手动，本页任何开关都不会自动代发消息。")
        zero_hint.setWordWrap(True)
        zero_hint.setStyleSheet("color: #b0b0c0; font-size: 11px;")
        zero_lay.addWidget(zero_hint)
        self.zero_local_scan = QCheckBox("启用本地 AI 扫描（自动发现本机已装模型/客户端）")
        self.zero_desktop = QCheckBox("启用桌面 AI 连接器（Trae/WorkBuddy 登录态；未装则自动 503）")
        self.zero_web_search = QCheckBox("启用网页搜索（本地知识不足时联网补全上下文，默认关）")
        self.zero_web_autofill = QCheckBox("启用自动补全（本地结果不足时联网补全，较慢）")
        self.zero_browser_ai = QCheckBox("启用浏览器自动化（Playwright 驱动网页 AI，慢/不稳）")
        zero_lay.addWidget(self.zero_local_scan)
        zero_lay.addWidget(self.zero_desktop)
        zero_lay.addWidget(self.zero_web_search)
        zero_lay.addWidget(self.zero_web_autofill)
        zero_lay.addWidget(self.zero_browser_ai)
        zero_group.setLayout(zero_lay)
        lay.addWidget(zero_group)

        lay.addStretch()
        return w

    # ---- 微信设置 ----
    def _build_wechat_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        monitor_group = QGroupBox("监听设置")
        mon_form = QFormLayout()
        self.wechat_monitor = QCheckBox("启用微信消息监听")
        self.wechat_auto_switch = QCheckBox("自动切换当前聊天对象")
        self.wechat_auto_analyze = QCheckBox("新消息自动分析")
        mon_form.addRow(self.wechat_monitor)
        mon_form.addRow(self.wechat_auto_switch)
        mon_form.addRow(self.wechat_auto_analyze)
        monitor_group.setLayout(mon_form)
        lay.addWidget(monitor_group)

        import_group = QGroupBox("聊天记录导入")
        import_lay = QVBoxLayout()
        self.import_status = QLabel("未导入")
        import_lay.addWidget(self.import_status)
        # 一键导入大按钮
        self.btn_one_click_import = QPushButton("🚀 一键导入微信聊天记录（自动匹配最佳方式）")
        self.btn_one_click_import.setMinimumHeight(48)
        self.btn_one_click_import.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #e94b35, stop:1 #ff9966); color: white; font-size: 14px;"
            "font-weight: bold; border-radius: 6px; padding: 8px; }"
            "QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #d93a24, stop:1 #ff8855); }"
            "QPushButton:disabled { background: #888; color: #ccc; }")
        self.btn_one_click_import.clicked.connect(self._one_click_import)
        import_lay.addWidget(self.btn_one_click_import)
        btn_row = QHBoxLayout()
        self.import_backup_btn = QPushButton("从官方备份导入")
        self.import_backup_btn.clicked.connect(self._import_from_backup)
        self.import_db_btn = QPushButton("从本地数据库导入")
        self.import_db_btn.clicked.connect(self._import_from_db)
        self.import_file_btn = QPushButton("从文本文件导入")
        self.import_file_btn.clicked.connect(self._import_from_file)
        btn_row.addWidget(self.import_backup_btn)
        btn_row.addWidget(self.import_db_btn)
        btn_row.addWidget(self.import_file_btn)
        import_lay.addLayout(btn_row)
        self.import_progress = QProgressBar()
        self.import_progress.setVisible(False)
        import_lay.addWidget(self.import_progress)
        import_group.setLayout(import_lay)
        lay.addWidget(import_group)

        lay.addStretch()
        return w

    # ---- 代理设置 ----
    def _build_proxy_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        proxy_group = QGroupBox("代理设置")
        form = QFormLayout()
        self.proxy_enabled = QCheckBox("启用代理")
        self.proxy_type = QComboBox()
        self.proxy_type.addItems(["HTTP", "SOCKS5", "sing-box订阅"])
        self.proxy_host = QLineEdit()
        self.proxy_port = QSpinBox()
        self.proxy_port.setRange(1, 65535)
        self.proxy_port.setValue(7890)
        self.proxy_subscribe = QLineEdit()
        self.proxy_subscribe.setPlaceholderText("sing-box订阅URL")
        form.addRow(self.proxy_enabled)
        form.addRow("代理类型：", self.proxy_type)
        form.addRow("主机：", self.proxy_host)
        form.addRow("端口：", self.proxy_port)
        form.addRow("订阅URL：", self.proxy_subscribe)
        proxy_group.setLayout(form)
        lay.addWidget(proxy_group)

        usage_group = QGroupBox("代理用途")
        self.proxy_llm = QCheckBox("大模型请求走代理")
        self.proxy_wechat = QCheckBox("微信相关请求走代理")
        lay.addWidget(self.proxy_llm)
        lay.addWidget(self.proxy_wechat)

        test_btn = QPushButton("测试代理连接")
        test_btn.clicked.connect(self._test_proxy)
        lay.addWidget(test_btn)

        lay.addStretch()
        return w

    # ---- 插件管理 ----
    def _build_plugin_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        # 已安装插件列表
        list_group = QGroupBox("已安装插件")
        list_lay = QVBoxLayout()
        self.plugin_list = QTextEdit()
        self.plugin_list.setReadOnly(True)
        self.plugin_list.setMaximumHeight(200)
        self.plugin_list.setPlaceholderText("加载中...")
        list_lay.addWidget(self.plugin_list)
        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._refresh_plugins)
        install_btn = QPushButton("安装插件")
        install_btn.clicked.connect(self._install_plugin)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(install_btn)
        list_lay.addLayout(btn_row)
        list_group.setLayout(list_lay)
        lay.addWidget(list_group)

        # 插件说明
        info_group = QGroupBox("说明")
        info_label = QLabel(
            "插件系统用于扩展LoveAI功能。\n"
            "数字分身、财务分析、记忆分析等核心功能已内置后台运行，无需插件。\n"
            "插件目录：plugins/\n"
            "将插件文件夹放入plugins目录后点击刷新即可加载。"
        )
        info_label.setWordWrap(True)
        info_group_layout = QVBoxLayout()
        info_group_layout.addWidget(info_label)
        info_group.setLayout(info_group_layout)
        lay.addWidget(info_group)

        lay.addStretch()

        # 延迟加载插件列表
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, self._refresh_plugins)

        return w

    def _refresh_plugins(self):
        """刷新插件列表。"""
        try:
            if self._facade and hasattr(self._facade, 'services'):
                plugin_service = self._facade.services.get('plugin')
                if plugin_service and hasattr(plugin_service, 'list_plugins'):
                    plugins = plugin_service.list_plugins()
                    if plugins:
                        text = ""
                        for p in plugins:
                            name = p.get('name', '未知')
                            version = p.get('version', '1.0')
                            desc = p.get('description', '')
                            enabled = p.get('enabled', True)
                            status = "✓ 已启用" if enabled else "✗ 已禁用"
                            text += f"【{name}】v{version} - {status}\n  {desc}\n\n"
                        self.plugin_list.setPlainText(text)
                        return
            self.plugin_list.setPlainText("暂无已安装插件")
        except Exception as e:
            self.plugin_list.setPlainText(f"加载插件失败: {e}")

    def _install_plugin(self):
        """安装插件。"""
        path = QFileDialog.getExistingDirectory(self, "选择插件文件夹")
        if path:
            QMessageBox.information(self, "插件", f"插件安装功能开发中。\n路径: {path}")

    # ---- 通用设置 ----
    def _build_general_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        ui_group = QGroupBox("界面")
        ui_form = QFormLayout()
        self.ui_theme = QComboBox()
        self.ui_theme.addItems(["暗色（烫金）", "亮色", "跟随系统"])
        self.ui_font = QComboBox()
        self.ui_font.addItems(["小", "中", "大"])
        self.pet_enabled = QCheckBox("启用3D桌宠")
        self.float_ball_mode = QComboBox()
        self.float_ball_mode.addItems(["金球模式（64px）", "桌宠模式（180×240）"])
        ui_form.addRow("主题：", self.ui_theme)
        ui_form.addRow("字体：", self.ui_font)
        ui_form.addRow("默认悬浮球模式：", self.float_ball_mode)
        ui_form.addRow(self.pet_enabled)
        ui_group.setLayout(ui_form)
        lay.addWidget(ui_group)

        # ===== R3.3-56 桌宠双模式 =====
        pet_mode_group = QGroupBox("桌宠模式")
        pet_mode_lay = QVBoxLayout()
        self.pet_mode_2d = QRadioButton("模式A：2D 序列帧（默认，CPU 低，呼吸/眨眼/口型同步）")
        self.pet_mode_realtime = QRadioButton("模式B：实时数字人（SadTalker/Wav2Lip，配置高才开；未装自动降级到2D）")
        self.pet_mode_btn_group = QButtonGroup(self)
        self.pet_mode_btn_group.addButton(self.pet_mode_2d, 0)
        self.pet_mode_btn_group.addButton(self.pet_mode_realtime, 1)
        self.pet_mode_2d.setChecked(True)
        pet_mode_hint = QLabel(
            "模式B 需要本机已安装 SadTalker 或 Wav2Lip；未检测到时启动桌宠会自动降级到 2D。"
        )
        pet_mode_hint.setWordWrap(True)
        pet_mode_hint.setStyleSheet("color:#b0b0c0; font-size:11px;")
        pet_mode_lay.addWidget(self.pet_mode_2d)
        pet_mode_lay.addWidget(self.pet_mode_realtime)
        pet_mode_lay.addWidget(pet_mode_hint)
        pet_mode_group.setLayout(pet_mode_lay)
        lay.addWidget(pet_mode_group)

        shortcut_group = QGroupBox("快捷键")
        sc_form = QFormLayout()
        self.sc_main = QLineEdit("Ctrl+Alt+L")
        self.sc_voice = QLineEdit("Ctrl+Alt+V")
        self.sc_boss = QLineEdit("Ctrl+Alt+H")
        sc_form.addRow("主界面：", self.sc_main)
        sc_form.addRow("语音：", self.sc_voice)
        sc_form.addRow("老板键：", self.sc_boss)
        shortcut_group.setLayout(sc_form)
        lay.addWidget(shortcut_group)

        # 说话风格学习
        style_group = QGroupBox("说话风格")
        style_lay = QVBoxLayout()
        style_hint = QLabel("从历史聊天记录中学习你的说话风格（口头禅、句式、语气），用于生成更像你的回复。")
        style_hint.setWordWrap(True)
        style_hint.setStyleSheet("color: #b0b0c0; font-size: 11px;")
        style_lay.addWidget(style_hint)
        style_btn_row = QHBoxLayout()
        self.learn_style_btn = QPushButton("重新学习说话风格")
        self.learn_style_btn.clicked.connect(self._learn_speech_style)
        self.view_style_btn = QPushButton("查看当前风格")
        self.view_style_btn.clicked.connect(self._view_speech_style)
        style_btn_row.addWidget(self.learn_style_btn)
        style_btn_row.addWidget(self.view_style_btn)
        style_lay.addLayout(style_btn_row)
        style_group.setLayout(style_lay)
        lay.addWidget(style_group)

        # ===== ui_settings：界面与零成本AI中枢（5 个开关/滑条）===== #
        ui_ai_group = QGroupBox("界面与零成本AI（ui_settings）")
        ui_ai_lay = QVBoxLayout()
        ui_ai_lay.setSpacing(8)

        # 1) 动效背景 开/关
        self.ui_dynamic_bg = QCheckBox("动效背景（主窗口粒子流动背景）")
        ui_ai_lay.addWidget(self.ui_dynamic_bg)

        # 2) 悬浮卡透明度（滑条 0-100）
        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("悬浮卡透明度："))
        self.ui_card_opacity = QSlider(Qt.Horizontal)
        self.ui_card_opacity.setRange(0, 100)
        self.ui_card_opacity.setValue(90)
        self.ui_card_opacity.setTickPosition(QSlider.TicksBelow)
        self.ui_card_opacity_label = QLabel("90%")
        self.ui_card_opacity_label.setMinimumWidth(42)
        self.ui_card_opacity.valueChanged.connect(
            lambda v: self.ui_card_opacity_label.setText(f"{v}%"))
        op_row.addWidget(self.ui_card_opacity, stretch=1)
        op_row.addWidget(self.ui_card_opacity_label)
        ui_ai_lay.addLayout(op_row)

        # 3) 本地AI扫描 开/关 + 手动扫描按钮
        scan_row = QHBoxLayout()
        self.ui_local_scan = QCheckBox("本地AI扫描（自动发现本机已装模型/客户端）")
        scan_row.addWidget(self.ui_local_scan, stretch=1)
        self.ui_scan_now_btn = QPushButton("手动扫描")
        self.ui_scan_now_btn.clicked.connect(self._manual_local_scan)
        scan_row.addWidget(self.ui_scan_now_btn)
        ui_ai_lay.addLayout(scan_row)
        self.ui_scan_status = QLabel("")
        self.ui_scan_status.setStyleSheet("color:#b0b0c0; font-size:11px;")
        self.ui_scan_status.setWordWrap(True)
        ui_ai_lay.addWidget(self.ui_scan_status)

        # 4) 浏览器AI中枢 开/关
        self.ui_browser_ai = QCheckBox("浏览器AI中枢（自动后台启用 Playwright 网页AI）")
        ui_ai_lay.addWidget(self.ui_browser_ai)

        # 5) 免费模型自动轮换 开/关
        self.ui_free_rotate = QCheckBox("免费模型自动轮换（FreeModelRouter 故障转移/轮换）")
        ui_ai_lay.addWidget(self.ui_free_rotate)

        ui_ai_group.setLayout(ui_ai_lay)
        lay.addWidget(ui_ai_group)

        lay.addStretch()
        return w

    # ---- 隐私与数据 ----
    def _build_privacy_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        privacy_group = QGroupBox("隐私")
        self.privacy_encrypt = QCheckBox("聊天记录本地加密存储")
        self.privacy_upload = QCheckBox("允许匿名使用统计（不包含聊天内容）")
        self.privacy_lock = QCheckBox("锁屏时隐藏桌宠")
        lay.addWidget(self.privacy_encrypt)
        lay.addWidget(self.privacy_upload)
        lay.addWidget(self.privacy_lock)

        data_group = QGroupBox("数据管理")
        data_lay = QVBoxLayout()
        self.data_path = QLineEdit()
        data_lay.addWidget(QLabel("数据存储目录："))
        data_lay.addWidget(self.data_path)
        btn_row = QHBoxLayout()
        backup_btn = QPushButton("立即备份")
        backup_btn.clicked.connect(self._do_backup)
        restore_btn = QPushButton("恢复备份")
        restore_btn.clicked.connect(self._do_restore)
        clear_btn = QPushButton("清除所有数据")
        clear_btn.setStyleSheet("background: #e74c3c; color: white;")
        clear_btn.clicked.connect(self._clear_data)
        btn_row.addWidget(backup_btn)
        btn_row.addWidget(restore_btn)
        btn_row.addWidget(clear_btn)
        data_lay.addLayout(btn_row)
        data_group.setLayout(data_lay)
        lay.addWidget(data_group)

        lay.addStretch()
        return w

    # ---- 说话风格 ----
    def _learn_speech_style(self):
        """重新学习说话风格（耗时几秒，带loading状态）。"""
        if not self._facade or not hasattr(self._facade, 'learn_speech_style'):
            QMessageBox.warning(self, "说话风格", "说话风格服务不可用。")
            return
        self.learn_style_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            r = self._facade.learn_speech_style(use_cloud=True)
            if r.get("ok"):
                data = r.get("data") or {}
                samples = data.get("sample_count", data.get("samples", "?"))
                features = data.get("features", data.get("style_features", {}))
                feat_text = ""
                if isinstance(features, dict):
                    feat_text = "\n".join(f"  · {k}: {v}" for k, v in list(features.items())[:8])
                QMessageBox.information(self, "学习完成",
                    f"说话风格学习完成！\n样本数: {samples}\n\n风格特征:\n{feat_text or '（无详细特征）'}")
            else:
                QMessageBox.warning(self, "学习失败", r.get("error", "未知错误"))
        except Exception as e:
            logger.error("学习说话风格失败: %s", e, exc_info=True)
            QMessageBox.warning(self, "学习失败", f"异常: {e}")
        finally:
            QApplication.restoreOverrideCursor()
            self.learn_style_btn.setEnabled(True)

    def _view_speech_style(self):
        """查看当前说话风格画像。"""
        if not self._facade or not hasattr(self._facade, 'get_speech_style'):
            QMessageBox.warning(self, "说话风格", "说话风格服务不可用。")
            return
        try:
            r = self._facade.get_speech_style()
            if not r.get("ok"):
                QMessageBox.warning(self, "查看失败", r.get("error", "未知错误"))
                return
            data = r.get("data")
            if not data:
                QMessageBox.information(self, "当前风格", "尚未学习说话风格，请先点击「重新学习说话风格」。")
                return
            # 格式化展示风格画像
            lines = []
            if isinstance(data, dict):
                for key, label in [
                    ("catchphrases", "口头禅"),
                    ("sentence_patterns", "句式特征"),
                    ("tone", "语气"),
                    ("emoji_usage", "常用表情"),
                    ("avg_length", "平均句长"),
                    ("sample_count", "样本数"),
                ]:
                    val = data.get(key)
                    if val:
                        if isinstance(val, (list, dict)):
                            val = ", ".join(str(v) for v in val) if isinstance(val, list) else str(val)
                        lines.append(f"{label}: {val}")
                # 其他字段兜底
                for k, v in data.items():
                    if k not in ("catchphrases", "sentence_patterns", "tone",
                                 "emoji_usage", "avg_length", "sample_count"):
                        if isinstance(v, (str, int, float)):
                            lines.append(f"{k}: {v}")
            if not lines:
                lines.append(str(data))
            QMessageBox.information(self, "当前说话风格", "\n".join(lines))
        except Exception as e:
            logger.error("查看说话风格失败: %s", e, exc_info=True)
            QMessageBox.warning(self, "查看失败", f"异常: {e}")

    # ---- 加载配置 ----
    # ---- 手动本地AI扫描（后台线程，结果回写 services + 状态标签）----
    def _manual_local_scan(self):
        """点击「手动扫描」：后台跑一次 LocalAIScanner.scan()，不阻塞UI。"""
        try:
            self.ui_scan_now_btn.setEnabled(False)
            self.ui_scan_status.setText("扫描中…（探测本机已装模型/客户端，约数秒）")
        except Exception:
            pass

        def _worker():
            result = {"status": "failed", "installed": [], "openai_endpoints": [],
                      "usable_models": [], "notes": []}
            try:
                from services.local_ai_scanner import LocalAIScanner
                result = LocalAIScanner().scan() or {}
                result["status"] = "done"
            except Exception as e:  # noqa: BLE001
                result["notes"] = [f"扫描失败: {e}"]
            try:
                self._scan_done.emit(result)
            except Exception:
                pass

        import threading
        threading.Thread(target=_worker, name="manual-local-scan", daemon=True).start()

    def _on_scan_done(self, result: dict):
        """扫描完成回主线程：写回 facade.services 并更新状态标签。"""
        try:
            if self._facade is not None and hasattr(self._facade, "services"):
                try:
                    self._facade.services["desktop_ai_candidates"] = result
                except Exception:
                    pass
            installed = result.get("installed") or []
            endpoints = result.get("openai_endpoints") or []
            models = result.get("usable_models") or []
            status = result.get("status", "")
            if status == "done":
                self.ui_scan_status.setText(
                    f"扫描完成：已安装 {len(installed)} 个，OpenAI 端点 {len(endpoints)} 个，"
                    f"可调用模型 {len(models)} 个。")
            else:
                notes = result.get("notes") or ["扫描失败"]
                self.ui_scan_status.setText("扫描：" + "；".join(notes))
        except Exception as e:
            logger.debug("手动扫描结果回写失败: %s", e)
        finally:
            try:
                self.ui_scan_now_btn.setEnabled(True)
            except Exception:
                pass

    def _load_config(self):
        try:
            self.llm_api_key.setText(self._config.get("llm.cloud.api_key", ""))
            self.llm_api_base.setText(self._config.get("llm.cloud.api_base", "https://api.openai.com/v1"))
            self.llm_model.setText(self._config.get("llm.cloud.model", "gpt-4o-mini"))
            self.route_strategy.setCurrentIndex(0)
            self.l1_enabled.setChecked(self._config.get("llm.l1.enabled", True))
            self.l2_enabled.setChecked(self._config.get("llm.l2.enabled", True))
            self.l1_path.setText(self._config.get("llm.l1.onnx_path", "models_local/smollm2-135m/model_q4.onnx"))
            self.l2_path.setText(self._config.get("llm.l2.model_dir", "models_local/Qwen2.5-0.5B-Instruct"))

            # 零成本 AI 供给开关：仅「本地扫描」默认开（开箱即生效）；
            # 桌面AI/网页搜索/自动补全/浏览器自动化默认关（网页搜索仅在知识不足时显式补全）。
            self.zero_local_scan.setChecked(self._config.get("llm.free.local_scan.enabled", True))
            self.zero_desktop.setChecked(self._config.get("llm.free.desktop_ai.enabled", False))
            self.zero_web_search.setChecked(self._config.get("llm.free.web_search.enabled", False))
            self.zero_web_autofill.setChecked(self._config.get("web.autofill.enabled", False))
            self.zero_browser_ai.setChecked(self._config.get("browser_ai.enabled", False))

            self.wechat_monitor.setChecked(self._config.get("wechat.monitor_enabled", True))
            self.wechat_auto_switch.setChecked(self._config.get("wechat.auto_switch_person", True))
            self.wechat_auto_analyze.setChecked(self._config.get("wechat.auto_analyze", False))

            self.proxy_enabled.setChecked(self._config.get("proxy.enabled", False))
            self.proxy_host.setText(self._config.get("proxy.host", ""))
            self.proxy_port.setValue(self._config.get("proxy.port", 7890))
            self.proxy_subscribe.setText(self._config.get("proxy.subscribe_url", ""))
            self.proxy_llm.setChecked(self._config.get("proxy.use_for_llm", True))
            self.proxy_wechat.setChecked(self._config.get("proxy.use_for_wechat", False))

            self.pet_enabled.setChecked(self._config.get("ui.pet_enabled", True))
            # R3.3-56 桌宠模式：2d / realtime
            _pmode = self._config.get("pet.mode", "2d")
            if _pmode == "realtime":
                self.pet_mode_realtime.setChecked(True)
            else:
                self.pet_mode_2d.setChecked(True)
            # 默认悬浮球模式：0=ball（金球），1=pet（桌宠）
            _fmode = self._config.get("ui.float_ball_mode", "ball")
            self.float_ball_mode.setCurrentIndex(1 if _fmode == "pet" else 0)
            self.privacy_encrypt.setChecked(self._config.get("privacy.encrypt_chat", True))
            self.privacy_upload.setChecked(self._config.get("privacy.allow_analytics", False))
            self.privacy_lock.setChecked(self._config.get("privacy.hide_pet_on_lock", False))
            self.data_path.setText(self._config.get("data.path", "data"))

            # ui_settings 5 开关读回
            self.ui_dynamic_bg.setChecked(self._config.get("ui_settings.dynamic_background_enabled", True))
            self.ui_card_opacity.setValue(int(self._config.get("ui_settings.floating_card_opacity", 90)))
            self.ui_local_scan.setChecked(self._config.get("ui_settings.local_ai_scan_enabled", True))
            self.ui_browser_ai.setChecked(self._config.get("ui_settings.browser_ai_hub_enabled", False))
            self.ui_free_rotate.setChecked(self._config.get("ui_settings.free_model_rotation_enabled", True))
        except Exception as e:
            logger.warning(f"加载配置失败: {e}")

    # ---- 保存配置 ----
    def _save_config(self):
        try:
            self._config.set("llm.cloud.api_key", self.llm_api_key.text())
            self._config.set("llm.cloud.api_base", self.llm_api_base.text())
            self._config.set("llm.cloud.model", self.llm_model.text())
            self._config.set("llm.l1.enabled", self.l1_enabled.isChecked())
            self._config.set("llm.l2.enabled", self.l2_enabled.isChecked())
            self._config.set("llm.l1.onnx_path", self.l1_path.text())
            self._config.set("llm.l2.model_dir", self.l2_path.text())

            # 零成本 AI 供给开关持久化（被 FreeModelRouter 读取）
            self._config.set("llm.free.local_scan.enabled", self.zero_local_scan.isChecked())
            self._config.set("llm.free.desktop_ai.enabled", self.zero_desktop.isChecked())
            self._config.set("llm.free.web_search.enabled", self.zero_web_search.isChecked())
            self._config.set("web.autofill.enabled", self.zero_web_autofill.isChecked())
            self._config.set("browser_ai.enabled", self.zero_browser_ai.isChecked())

            self._config.set("wechat.monitor_enabled", self.wechat_monitor.isChecked())
            self._config.set("wechat.auto_switch_person", self.wechat_auto_switch.isChecked())
            self._config.set("wechat.auto_analyze", self.wechat_auto_analyze.isChecked())

            self._config.set("proxy.enabled", self.proxy_enabled.isChecked())
            self._config.set("proxy.host", self.proxy_host.text())
            self._config.set("proxy.port", self.proxy_port.value())
            self._config.set("proxy.subscribe_url", self.proxy_subscribe.text())
            self._config.set("proxy.use_for_llm", self.proxy_llm.isChecked())
            self._config.set("proxy.use_for_wechat", self.proxy_wechat.isChecked())

            self._config.set("ui.pet_enabled", self.pet_enabled.isChecked())
            # R3.3-56 桌宠模式写入 config.json pet.mode
            self._config.set("pet.mode", "realtime" if self.pet_mode_realtime.isChecked() else "2d")
            self._config.set("ui.float_ball_mode", "pet" if self.float_ball_mode.currentIndex() == 1 else "ball")
            self._config.set("privacy.encrypt_chat", self.privacy_encrypt.isChecked())
            self._config.set("privacy.allow_analytics", self.privacy_upload.isChecked())
            self._config.set("privacy.hide_pet_on_lock", self.privacy_lock.isChecked())

            # ui_settings 5 开关持久化
            self._config.set("ui_settings.dynamic_background_enabled", self.ui_dynamic_bg.isChecked())
            self._config.set("ui_settings.floating_card_opacity", self.ui_card_opacity.value())
            self._config.set("ui_settings.local_ai_scan_enabled", self.ui_local_scan.isChecked())
            self._config.set("ui_settings.browser_ai_hub_enabled", self.ui_browser_ai.isChecked())
            self._config.set("ui_settings.free_model_rotation_enabled", self.ui_free_rotate.isChecked())
            # 镜像到服务实际读取的键（开关即时生效，无需改 gateway）
            self._config.set("llm.free.local_scan.enabled", self.ui_local_scan.isChecked())
            self._config.set("browser_ai.enabled", self.ui_browser_ai.isChecked())
            self._config.set("llm.free.enabled", self.ui_free_rotate.isChecked())

            QMessageBox.information(self, "设置", "设置已保存。")
            self.settings_saved.emit(self._config.get_all())
            self.accept()
        except Exception as e:
            logger.error(f"保存设置失败: {e}", exc_info=True)
            QMessageBox.warning(self, "错误", f"保存失败: {e}")

    # ---- 导入功能 ----
    def _one_click_import(self):
        """一键导入：后台线程调用 facade.auto_import_wechat_all()。"""
        self.import_status.setText("正在自动尝试所有可用方式...")
        self.import_progress.setVisible(True)
        self.import_progress.setRange(0, 0)
        self.btn_one_click_import.setEnabled(False)

        def worker():
            try:
                result = self._facade.auto_import_wechat_all()
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._on_import_done(result))
            except Exception as e:
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._on_import_error(str(e)))

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _on_import_done(self, result):
        self.import_progress.setVisible(False)
        self.btn_one_click_import.setEnabled(True)
        total = result.get("total", 0)
        used = result.get("used", [])
        skipped = result.get("skipped", [])
        log = result.get("log", [])
        msg = f"导入完成！\n\n共导入 {total} 条消息\n成功方式：{len(used)} 种\n跳过方式：{len(skipped)} 种\n\n日志（最近10条）：\n"
        for l in log[-10:]:
            status = "✓" if l.get("ok") else "✗"
            msg += f"{status} {l.get('method')}: {l.get('reason', l.get('count', ''))}\n"
        self.import_status.setText(f"已导入 {total} 条消息")
        QMessageBox.information(self, "一键导入完成", msg)

    def _on_import_error(self, error_msg):
        self.import_progress.setVisible(False)
        self.btn_one_click_import.setEnabled(True)
        self.import_status.setText("导入失败")
        QMessageBox.warning(self, "导入失败",
            f"错误：{error_msg}\n\n请检查微信是否已打开，或尝试其他方式。")

    def _import_from_backup(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择微信备份文件", "", "备份文件 (*.db *.bak *.sqlite);;所有文件 (*)")
        if not path:
            return
        self.import_status.setText(f"正在解析备份: {os.path.basename(path)}...")
        self.import_progress.setVisible(True)
        self.import_progress.setRange(0, 0)

        def worker():
            try:
                parser = self._facade.services.get("wechat_backup_parser")
                result = parser.parse_backup(path) if parser else {"ok": False, "hint": "备份解析器未初始化"}
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._on_import_done(
                    {"total": result.get("count", 0), "used": ["backup"], "skipped": [],
                     "log": [{"method": "backup", "ok": result.get("ok", False),
                              "reason": result.get("hint", "")}]}))
            except Exception as e:
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._on_import_error(str(e)))

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _import_from_db(self):
        self.import_status.setText("正在自动查找微信进程并解密...")
        self.import_progress.setVisible(True)
        self.import_progress.setRange(0, 0)

        def worker():
            try:
                dec = self._facade.services.get("wechat_db_decryptor")
                result = dec.run_full_pipeline() if dec else {"ok": False, "hint": "解密器未初始化"}
                from PySide6.QtCore import QTimer
                if result.get("ok"):
                    QTimer.singleShot(0, lambda: self._on_import_done(
                        {"total": 0, "used": ["db_decrypt"], "skipped": [],
                         "log": [{"method": "db", "ok": True, "reason": "密钥已找到"}]})
                    )
                else:
                    QTimer.singleShot(0, lambda: self._on_import_error(
                        result.get("hint", "未找到微信数据库密钥，请以管理员身份运行或使用一键导入的其他方式。")))
            except Exception as e:
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._on_import_error(str(e)))

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _import_from_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择聊天记录文本", "", "文本文件 (*.txt *.csv *.json);;所有文件 (*)")
        if not path:
            return
        self.import_status.setText(f"正在解析文本: {os.path.basename(path)}...")
        try:
            count = 0
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        count += 1
            self.import_status.setText(f"已读取 {count} 行（待匹配人物后导入）")
            QMessageBox.information(self, "导入", f"已读取 {count} 行文本记录。\n文件: {path}")
        except Exception as e:
            self._on_import_error(str(e))

    def _test_proxy(self):
        QMessageBox.information(self, "代理测试", "代理连接测试功能开发中。")

    def _do_backup(self):
        # 24.1.10：接线已可用的 BackupService（此前仅弹“开发中”）
        try:
            if not self._facade:
                QMessageBox.warning(self, "备份", "服务未初始化。")
                return
            r = self._facade.create_backup()
            if r.get("ok"):
                d = r.get("data", {})
                QMessageBox.information(
                    self, "备份",
                    f"备份完成：{d.get('filename', '?')}\n"
                    f"大小：{d.get('size_mb', '?')} MB\n"
                    f"内容：{', '.join(d.get('contents', [])) or '无'}")
            else:
                QMessageBox.warning(self, "备份", f"备份失败：{r.get('error', '未知错误')}")
        except Exception as e:  # noqa: BLE001 - UI 兜底不弹崩溃
            logger.exception("立即备份失败")
            QMessageBox.warning(self, "备份", f"备份异常：{e}")

    def _do_restore(self):
        # 24.1.10：列出已有备份，确认后恢复最新一份（恢复前 BackupService 会自动再备份）
        try:
            if not self._facade:
                QMessageBox.warning(self, "恢复", "服务未初始化。")
                return
            lst = self._facade.list_backups()
            backups = (lst or {}).get("data", []) if isinstance(lst, dict) else []
            if not backups:
                QMessageBox.information(self, "恢复", "暂无可用备份，请先创建备份。")
                return
            latest = backups[0]
            reply = QMessageBox.question(
                self, "确认恢复",
                f"将恢复最新备份：\n{latest.get('filename', '?')}\n"
                f"（恢复前会自动再做一份安全备份）是否继续？",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            r = self._facade.restore_backup(latest.get("backup_id", ""))
            if r.get("ok"):
                d = r.get("data", {})
                QMessageBox.information(
                    self, "恢复",
                    f"恢复完成：{', '.join(d.get('restored_items', [])) or '无'}\n"
                    f"安全备份：{d.get('pre_restore_backup', '?')}")
            else:
                QMessageBox.warning(self, "恢复", f"恢复失败：{r.get('error', '未知错误')}")
        except Exception as e:  # noqa: BLE001
            logger.exception("恢复备份失败")
            QMessageBox.warning(self, "恢复", f"恢复异常：{e}")

    def _clear_data(self):
        reply = QMessageBox.question(self, "确认", "确定要清除所有数据吗？此操作不可恢复！",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            QMessageBox.information(self, "清除", "数据清除功能开发中。")

    # ---- AI 训练 Tab ----
    def _build_training_tab(self):
        """AI 反哺训练设置面板（本地训练，数据不上传）。"""
        try:
            from app.widgets.training_settings_widget import TrainingSettingsWidget
            collector = getattr(self._facade, "_train_collector", None) if self._facade else None
            engine = getattr(self._facade, "_train_engine", None) if self._facade else None
            versions = getattr(self._facade, "_train_versions", None) if self._facade else None
            self._training_widget = TrainingSettingsWidget(
                collector=collector, engine=engine, version_manager=versions, parent=self)
            return self._training_widget
        except Exception as e:
            logger.warning("AI 训练 Tab 加载失败: %s", e)
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.addWidget(QLabel("训练模块暂不可用（未安装依赖或初始化失败）。"))
            lay.addStretch()
            return w

    # ---- 微信导出 Tab ----
    def _build_wechat_export_tab(self):
        """微信多方法导出面板（密钥提取→解密→导入→实时监听）。"""
        try:
            from app.widgets.wechat_export_widget import WechatExportWidget
            importer = getattr(self._facade, "_wechat_multi_importer", None) if self._facade else None
            monitor = getattr(self._facade, "_wechat_monitor", None) if self._facade else None
            self._wechat_export_widget = WechatExportWidget(
                importer=importer, monitor=monitor, parent=self)
            return self._wechat_export_widget
        except Exception as e:
            logger.warning("微信导出 Tab 加载失败: %s", e)
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.addWidget(QLabel("微信导出模块暂不可用（初始化失败）。"))
            lay.addStretch()
            return w
