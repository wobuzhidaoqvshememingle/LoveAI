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
    _free_router_snap_done = Signal(object)  # 免费通道路由快照取数完成（跨线程回主线程更新UI）

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

        # ---- 追加：免费通道路由状态（只读展示，v3.3 新增）----
        # 数据源：FreeModelRouter.routing_snapshot()（冻结接口）。只读已存在的实例，
        # 不调用 get_free_router()（首次创建会 probe_all() 联网探活），故不发外网请求、不阻塞 UI。
        self.ui_free_router_title = QLabel("免费通道路由状态（只读 · 不改变路由决策）")
        self.ui_free_router_title.setStyleSheet(
            "color:#d4af37; font-size:12px; font-weight:bold;")
        zero_lay.addWidget(self.ui_free_router_title)

        self.ui_free_router_hint = QLabel(
            "数据来自 FreeModelRouter.routing_snapshot()（本地只读快照）：动态惩罚分、四维限流用量"
            "（rpm/rpd/tpm/tpd）、分级冷却、Sticky Session、最近一次实际路由均可在此查看"
            "（生效分越小越优先；上限显示「未配」表示内核未配该维上限）。"
            "本区只读取快照、不发外网请求，也不触发 probe_all 联网探活（内核枚举候选通道时"
            "会探测本机 Ollama，仅回环 127.0.0.1 / ::1）；点「刷新」手动重取。")
        self.ui_free_router_hint.setWordWrap(True)
        self.ui_free_router_hint.setStyleSheet("color: #b0b0c0; font-size: 11px;")
        zero_lay.addWidget(self.ui_free_router_hint)

        fr_row = QHBoxLayout()
        self.ui_free_router_state = QLabel(FREE_ROUTER_UNAVAILABLE_HINT)
        self.ui_free_router_state.setWordWrap(True)
        self.ui_free_router_state.setStyleSheet("color:#e0e0e0; font-size:11px;")
        fr_row.addWidget(self.ui_free_router_state, stretch=1)
        self.ui_free_router_refresh_btn = QPushButton("刷新")
        self.ui_free_router_refresh_btn.setToolTip("重新读取本地只读快照（不发外网请求、不影响路由决策）")
        self.ui_free_router_refresh_btn.clicked.connect(self._refresh_free_router_status)
        fr_row.addWidget(self.ui_free_router_refresh_btn)
        zero_lay.addLayout(fr_row)

        self.ui_free_router_text = QTextEdit()
        self.ui_free_router_text.setReadOnly(True)
        self.ui_free_router_text.setMinimumHeight(120)
        self.ui_free_router_text.setMaximumHeight(220)
        self.ui_free_router_text.setStyleSheet(
            "QTextEdit { background: #0f3460; color: #e0e0e0; border: 1px solid #d4af37;"
            " font-family: Consolas, 'Courier New', monospace; font-size: 11px; }")
        zero_lay.addWidget(self.ui_free_router_text)

        self.ui_free_router_note = QLabel(FREE_ROUTER_FOOTER)
        self.ui_free_router_note.setWordWrap(True)
        self.ui_free_router_note.setStyleSheet("color:#b0b0c0; font-size:11px;")
        zero_lay.addWidget(self.ui_free_router_note)

        # 先渲染「不可用」骨架（无网络/无 key/Ollama 未起时也安全），再后台取一次真快照
        self._free_router_snap_done.connect(self._on_free_router_snapshot)
        self._apply_free_router_view(render_free_router_status(None, "尚未刷新"))
        self._refresh_free_router_status()

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
                importer=importer, monitor=monitor, facade=self._facade, parent=self)
            return self._wechat_export_widget
        except Exception as e:
            logger.warning("微信导出 Tab 加载失败: %s", e)
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.addWidget(QLabel("微信导出模块暂不可用（初始化失败）。"))
            lay.addStretch()
            return w

    # ------------------------------------------------------------------ #
    # 免费通道路由状态（只读，v3.3 新增）：取数 + 渲染适配
    # 铁律：只读、不发外网请求、不改路由状态、不阻塞 UI 线程、任何异常都降级为「不可用」。
    # ------------------------------------------------------------------ #
    def _resolve_free_router(self):
        """返回 (router_or_None, reason)。只取「已存在」的路由器实例，不触发任何网络动作。

        取数链（自上而下，全防御）：
          1) 外部注入回调 self.ui_free_router_source()（可选，供测试/其它调用方注入）；
          2) facade.services["llm"]（LLMGateway），退化再试 facade._llm；
          3) 该 gateway 上已存在的懒建实例 llm._free_router（或 llm.free_router）。
        刻意不调用 LLMGateway.get_free_router()：其首次创建会执行 probe_all() 联网探活（预算 5s），
        与「取数不发外网请求、不阻塞」冲突；因此路由器尚未创建时如实报「未创建」而不是替它初始化。
        """
        src = getattr(self, "ui_free_router_source", None)
        if callable(src):
            try:
                return src(), ""
            except Exception as e:  # noqa: BLE001 - 注入方异常不得拖垮设置面板
                return None, f"注入的取数回调异常：{type(e).__name__}: {e}"

        facade = getattr(self, "_facade", None)
        if facade is None:
            return None, "服务门面不可用（facade=None）"

        llm = None
        services = getattr(facade, "services", None)
        if isinstance(services, dict):
            try:
                llm = services.get("llm")
            except Exception:  # noqa: BLE001
                llm = None
        if llm is None:
            try:
                llm = getattr(facade, "_llm", None)
            except Exception:  # noqa: BLE001
                llm = None
        if llm is None:
            return None, "LLMGateway 未就绪（services['llm'] 为空）"

        for attr in ("_free_router", "free_router"):
            try:
                router = getattr(llm, attr, None)
            except Exception:  # noqa: BLE001
                router = None
            if router is not None:
                return router, ""
        return None, ("免费路由器实例尚未创建（未主动初始化：首次创建会触发联网探活，"
                      "故在首次免费调用/后台刷新后才可用）")

    def _fetch_free_router_snapshot(self):
        """后台线程调用：返回 (snapshot_or_None, error_text)。全程不发外网请求、不改路由状态。"""
        router, reason = self._resolve_free_router()
        if router is None:
            return None, reason

        fn = getattr(router, "routing_snapshot", None)
        if not callable(fn):
            return None, "FreeModelRouter 未提供 routing_snapshot()（内核未就绪）"
        try:
            snap = fn()
        except Exception as e:  # noqa: BLE001 - 内核异常不得拖垮设置面板
            return None, f"routing_snapshot() 异常：{type(e).__name__}: {e}"
        if snap is None:
            return None, "routing_snapshot() 返回 None"
        if not isinstance(snap, dict):
            return None, f"routing_snapshot() 返回类型非法：{type(snap).__name__}"
        return snap, ""

    def _refresh_free_router_status(self):
        """点「刷新」（或构造时自动调用一次）：后台线程取进程内快照，回主线程渲染。"""
        try:
            self.ui_free_router_refresh_btn.setEnabled(False)
            self.ui_free_router_state.setText("读取中…（读取本地只读快照，不发外网请求）")
        except Exception:  # noqa: BLE001
            pass

        def _worker():
            snap, err = None, ""
            try:
                snap, err = self._fetch_free_router_snapshot()
            except Exception as e:  # noqa: BLE001
                snap, err = None, f"取数线程异常：{type(e).__name__}: {e}"
            try:
                self._free_router_snap_done.emit({"snapshot": snap, "error": err})
            except Exception:  # noqa: BLE001
                pass

        import threading
        threading.Thread(target=_worker, name="free-router-status", daemon=True).start()

    def _on_free_router_snapshot(self, payload):
        """主线程槽：把 {snapshot, error} 渲染进只读区；任何异常都不外泄。"""
        try:
            data = payload if isinstance(payload, dict) else {}
            view = render_free_router_status(data.get("snapshot"), data.get("error") or "")
            self._apply_free_router_view(view)
        except Exception as e:  # noqa: BLE001
            logger.debug("免费路由状态渲染失败: %s", e)
        finally:
            try:
                self.ui_free_router_refresh_btn.setEnabled(True)
            except Exception:  # noqa: BLE001
                pass

    def _apply_free_router_view(self, view):
        """控件适配层：把纯函数 render_free_router_status() 的结果写进只读控件。"""
        if not isinstance(view, dict):
            view = render_free_router_status(None, "视图数据非法")
        try:
            self.ui_free_router_state.setText(
                _fr_text(view.get("state_text"), "") or FREE_ROUTER_UNAVAILABLE_HINT)
            self.ui_free_router_text.setPlainText(
                "\n".join(_fr_text(x, "") for x in (view.get("lines") or [])))
            self.ui_free_router_note.setText(
                _fr_text(view.get("footer"), "") or FREE_ROUTER_FOOTER)
        except Exception as e:  # noqa: BLE001
            logger.debug("免费路由状态控件写入失败: %s", e)


# ====================================================================== #
# 免费通道路由状态：模块级纯函数（输入 snapshot dict → 输出展示视图）
# 不依赖 Qt 事件循环，可被 tests/ 直接 import 单测。
# 契约：纯函数（自身不触网、绝不抛异常）——方法/字段/类型任一缺失都降级为「不可用」或占位符。
# ====================================================================== #
FREE_ROUTER_UNAVAILABLE_HINT = "路由状态不可用（内核未就绪）"
FREE_ROUTER_FOOTER = (
    "说明：免费池为空时报空池，绝不回退到付费模型（fail-closed）。"
    "落盘持久化、重启不清零：限流计数（按请求事件落盘，重启后回灌）、429 冷却等级与解锁时间、"
    "Sticky 会话映射；落盘文件 data/free_router/router_state.db"
    "（开发态=工程根 data/，冻结态=安装目录 data/；状态库打不开时会自动退化为纯内存）。"
    "仅进程内、重启清零：动态惩罚分；「本次实际由」也只有本进程命中过才有值。"
)
FREE_ROUTER_COLUMNS = [
    "通道", "基础优先级", "惩罚分", "生效分", "可用", "冷却剩余(s)", "冷却等级",
    "rpm", "rpd", "tpm", "tpd", "成功", "失败", "延迟(ms)",
]


def _fr_num(value, default=0.0):
    """任意值 → float；None/非法/NaN/±Inf 一律回落 default（不引入 math，保持零依赖）。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if out != out or (out - out) != 0.0:  # NaN 或 ±Inf
        return float(default)
    return out


def _fr_int(value, default=0):
    """任意值 → int（经 float 容错）。"""
    try:
        return int(_fr_num(value, default))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _fr_bool(value, default=False):
    """任意值 → bool，容忍字符串 'true/1/yes/是' 等写法。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on", "是", "真")
    return bool(default)


def _fr_tri_bool(value):
    """三态布尔：True / False / None（None = 无法判定，调用方**不得**当成 False 臆造结论）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value) if value in (0, 1) else None
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "y", "on", "是", "真"):
            return True
        if v in ("0", "false", "no", "n", "off", "否", "假"):
            return False
    return None


def _fr_text(value, default=""):
    """任意值 → str；None 回落 default。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _fr_limit_pair(used, cap):
    """限流用量列：'已用/上限'；上限为 null（内核未配该维上限）时显示 '已用/未配'。"""
    used_n = _fr_int(used, 0)
    if cap is None or (isinstance(cap, str) and not cap.strip()):
        return f"{used_n}/未配"
    try:
        cap_n = int(float(cap))
    except (TypeError, ValueError):
        return f"{used_n}/未配"
    return f"{used_n}/{cap_n}"


def _fr_fmt_ts(ts):
    """epoch 秒 → 本地 'YYYY-MM-DD HH:MM:SS'；<=0 或非法时返回占位符。"""
    v = _fr_num(ts, 0.0)
    if v <= 0:
        return "（无）"
    try:
        import time as _time
        return _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(v))
    except Exception:  # noqa: BLE001 - 时间异常只降级为数字
        return f"{v:.0f}"


def render_free_router_status(snapshot, error=""):
    """纯函数：FreeModelRouter.routing_snapshot() 的 dict（冻结接口）→ 展示视图。

    返回 dict：
      available(bool) / reason(str) / state_text(str)  —— 顶部一行状态
      head(list[str]) / columns(list[str]) / rows(list[list[str]]) —— 顶部字段与逐通道行（结构化，便于断言）
      lines(list[str]) —— 展示用文本（head + 通道块）
      footer(str) —— 底部 fail-closed 说明
      channel_count/eligible_count/cooling_count(int)
    输入为 None/非 dict/字段缺失/类型异常时一律降级，不抛异常。
    """
    view = {
        "available": False,
        "reason": _fr_text(error, "").strip(),
        "state_text": FREE_ROUTER_UNAVAILABLE_HINT,
        "head": [],
        "columns": list(FREE_ROUTER_COLUMNS),
        "rows": [],
        "states": [],
        "lines": [],
        "footer": FREE_ROUTER_FOOTER,
        "channel_count": 0,
        "eligible_count": 0,
        "cooling_count": 0,
    }
    if not isinstance(snapshot, dict):
        if not view["reason"]:
            view["reason"] = ("未取到快照（内核未就绪）" if snapshot is None
                              else f"快照类型非法：{type(snapshot).__name__}")
        view["lines"] = [f"{FREE_ROUTER_UNAVAILABLE_HINT}：{view['reason']}"]
        return view

    generated_at = _fr_num(snapshot.get("generated_at"), 0.0)
    last_via = _fr_text(snapshot.get("last_routed_via"), "").strip() or "（本次尚未路由过）"
    sticky = _fr_int(snapshot.get("sticky_sessions"), 0)
    raw_channels = snapshot.get("channels")
    channels = raw_channels if isinstance(raw_channels, list) else []
    mode = snapshot.get("mode") if isinstance(snapshot.get("mode"), dict) else {}

    view["available"] = True
    head = [
        f"快照时间：{_fr_fmt_ts(generated_at)}",
        f"本次实际由：{last_via}",
        f"活跃 sticky 会话：{sticky}",
        f"候选通道：{len(channels)} 条",
    ]
    # 落盘状态：内核附加字段 ``persistence`` = PersistentStateStore.status()（可能不存在）。
    # 只按内核自报如实显示；``ok`` 用三态判定——无法判定（缺键/None/乱值）就不显示该行，
    # 绝不把「不知道」渲染成「没落盘」。它决定「重启是否清零」，与底部说明互为印证。
    persistence = snapshot.get("persistence")
    if isinstance(persistence, dict):
        store_ok = _fr_tri_bool(persistence.get("ok"))
        if store_ok is True:
            db_path = _fr_text(persistence.get("path"), "").strip() or "（内核未给出路径）"
            head.append(f"落盘状态：已启用（重启不清零）→ {db_path}")
        elif store_ok is False:
            db_err = _fr_text(persistence.get("error"), "").strip()
            head.append("落盘状态：未启用，已退化为纯内存（重启清零）"
                        + (f"：{db_err}" if db_err else ""))
    view["head"] = head

    rows, states, cooling = [], [], 0
    for item in channels:
        ch = item if isinstance(item, dict) else {}
        limits = ch.get("limits") if isinstance(ch.get("limits"), dict) else {}
        provider = _fr_text(ch.get("provider"), "?") or "?"
        model = _fr_text(ch.get("model"), "?") or "?"
        base = _fr_int(ch.get("base_priority"), 0)
        penalty = _fr_num(ch.get("penalty"), 0.0)
        effective = _fr_num(ch.get("effective_score"), 0.0)
        eligible = _fr_bool(ch.get("eligible"), False)
        remaining = _fr_num(ch.get("remaining_sec"), 0.0)
        level = _fr_int(ch.get("cooldown_level"), 0)
        ok_n = _fr_int(ch.get("ok"), 0)
        fail_n = _fr_int(ch.get("fail"), 0)
        latency = _fr_num(ch.get("latency_ms"), 0.0)

        if remaining > 0:
            state = f"冷却中（{level} 级）"
            cooling += 1
        elif eligible:
            state = "可用"
        else:
            state = "不可用"

        rows.append([
            f"{provider}:{model}", str(base), f"{penalty:.1f}", f"{effective:.1f}",
            "是" if eligible else "否", f"{remaining:.0f}", str(level),
            f"{_fr_limit_pair(limits.get('rpm_used'), limits.get('rpm'))}",
            f"{_fr_limit_pair(limits.get('rpd_used'), limits.get('rpd'))}",
            f"{_fr_limit_pair(limits.get('tpm_used'), limits.get('tpm'))}",
            f"{_fr_limit_pair(limits.get('tpd_used'), limits.get('tpd'))}",
            str(ok_n), str(fail_n), f"{latency:.1f}",
        ])
        states.append(state)

    view["rows"] = rows
    view["states"] = states
    view["channel_count"] = len(rows)
    view["eligible_count"] = len([s for s in states if s == "可用"])
    view["cooling_count"] = cooling

    lines = list(head)
    lines.append("-" * 62)
    if not rows:
        lines.append("（候选通道 0 条：免费池为空，或候选目录尚未就绪；"
                     "按 fail-closed 不会改道付费模型）")
    for row, state in zip(rows, states):
        lines.append(f"• {row[0]}  [{state}]")
        lines.append(f"    基础优先级 {row[1]} | 惩罚分 {row[2]} | 生效分 {row[3]} | "
                     f"冷却剩余 {row[5]}s（等级 {row[6]}）")
        lines.append(f"    限流 rpm {row[7]} | rpd {row[8]} | tpm {row[9]} | tpd {row[10]}")
        lines.append(f"    成功 {row[11]} | 失败 {row[12]} | 延迟 {row[13]}ms")
    view["lines"] = lines

    if not rows:
        view["state_text"] = "候选通道 0 条（免费池为空或候选目录未就绪）"
    else:
        view["state_text"] = (f"已刷新：共 {len(rows)} 条候选通道，"
                              f"{view['eligible_count']} 条可用，{cooling} 条冷却中")

    footer = FREE_ROUTER_FOOTER
    if mode and _fr_bool(mode.get("fail_closed"), True) is False:
        footer += "【警告】快照声明 fail_closed=False，非预期状态，请检查路由内核。"
    view["footer"] = footer
    return view
