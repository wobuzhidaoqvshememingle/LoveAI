"""
LoveAI-v2 主窗口（PySide6 原生 QWidget 重写版）
- QMainWindow 外壳：顶部栏 + 左侧导航 + 右侧 QStackedWidget 内容区 + 底部状态栏
- 烫金暗色 QSS 主题，通过 ServiceFacade 调用后端服务
- 关闭时 hide() 而非 close()，配合全局信号总线
"""

import os
import json
import logging

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QCloseEvent, QPixmap, QPainter, QBrush, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QStackedWidget,
    QScrollArea, QGroupBox, QTextEdit, QStatusBar, QFrame,
    QButtonGroup, QMessageBox, QCheckBox, QCalendarWidget, QListWidget,
    QListWidgetItem, QSplitter, QDateEdit, QProgressBar, QSlider,
)

from app.service_facade import ServiceFacade
from app.signals import get_signals
from app.widgets.common.affection_ring import AffectionRing
from app.widgets.common.radar_chart import RadarChart
from app.widgets.common.gilded_button import GildedButton
from app.widgets.common.timeline_widget import TimelineWidget
from app.widgets.kanban_widget import KanbanWidget
from app.widgets.digital_twin_panel import DigitalTwinPanel
from app.widgets.quick_panel import QuickPanel
from app.widgets.profile_optimize_card import ProfileOptimizeCard

# 动效背景（可选依赖：PySide6 / offscreen 测试环境下导入失败时静默降级）
try:
    from app.dynamic_background import DynamicBackground
    _HAS_DYNAMIC_BG = True
except Exception:
    _HAS_DYNAMIC_BG = False
    DynamicBackground = None  # type: ignore[assignment]

# 单轮分析输出卡（意图/把握/策略/3候选/风险/预测 六字段固定槽位）
try:
    from app.widgets.analysis_output_card import AnalysisOutputCard
    _HAS_ANALYSIS_CARD = True
except Exception:
    _HAS_ANALYSIS_CARD = False
    AnalysisOutputCard = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# 导航页索引常量
PAGE_CHAT_ANALYSIS = 0
PAGE_CONSULT = 1
PAGE_PROFILE = 2
PAGE_HERO_WALL = 3
PAGE_MEDAL_WALL = 4
PAGE_CALENDAR = 5
PAGE_RELATION_GRAPH = 6
PAGE_TASKS = 7
PAGE_TWIN = 8

# 聊天分析页 8 个卡片的标题与 key
_ANALYSIS_CARDS = [
    ("emotion", "情绪与言外之意"),
    ("unknown", "事实推测 / 未知信息"),
    ("reply", "回复建议"),
    ("situation", "当前局面"),
    ("action", "行动建议"),
    ("consequence", "后果预判"),
    ("tutorial", "操作教程"),
    ("observation", "观察窗口"),
]


def _load_qss(window: QWidget) -> None:
    """加载 resources/qss/main.qss（可选，文件不存在时静默跳过）。"""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        # __file__ 在 app/widgets/ 下，往上三级到项目根
        root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        qss_path = os.path.join(root, "resources", "qss", "main.qss")
        if os.path.exists(qss_path):
            with open(qss_path, encoding="utf-8") as f:
                window.setStyleSheet(f.read())
    except Exception:
        logger.debug("QSS 加载失败，使用内联默认样式", exc_info=True)


class _CircularAvatar(QLabel):
    """圆形头像控件：默认显示首字符，有图片时裁剪为圆形。"""

    def __init__(self, text: str = "?", size: int = 44, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.setText(text[:1] or "?")
        self.setStyleSheet(
            "background-color: #6366f1; color: #ffffff;"
            "border-radius: %dpx; font-weight: 600; font-size: %dpt;"
            % (size // 2, max(10, size // 4))
        )

    def set_pixmap(self, path: str) -> None:
        """从文件路径加载图片并裁剪为圆形。"""
        if not path or not os.path.exists(path):
            return
        pm = QPixmap(path)
        if pm.isNull():
            return
        pm = pm.scaled(self._size, self._size, Qt.KeepAspectRatioByExpanding,
                       Qt.SmoothTransformation)
        # 用 QPainter 裁剪圆形
        rounded = QPixmap(self._size, self._size)
        rounded.fill(Qt.transparent)
        p = QPainter(rounded)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor("#3a3060")))
        p.drawEllipse(0, 0, self._size, self._size)
        p.setClipRect(0, 0, self._size, self._size)
        # 居中绘制
        x = (self._size - pm.width()) // 2
        y = (self._size - pm.height()) // 2
        p.drawPixmap(x, y, pm)
        p.end()
        self.setPixmap(rounded)
        self.setText("")


class MainWindow(QMainWindow):
    """LoveAI 主窗口。

    用法::

        win = MainWindow(facade)
        win.show()
    """

    def __init__(self, facade: ServiceFacade, parent: QWidget = None):
        super().__init__(parent)
        self._facade = facade
        self._signals = get_signals()
        self._current_person_id: str | None = None
        self._analysis_card_labels: dict[str, QLabel] = {}

        # 标题版本号读配置，不再硬编码（避免发版后标题与实际版本脱节）
        _ver = "3.1.0"
        try:
            _cfg = facade.services.get("config")
            _ver = _cfg.get("app.version", _ver) if _cfg else _ver
        except Exception:
            pass
        self.setWindowTitle(f"LoveAI v{_ver}")
        self.resize(1180, 780)
        _load_qss(self)

        self._build_ui()
        self._connect_signals()
        self._reload_active_person()

        # 动效背景：主窗口底层铺一层 DynamicBackground，不挡交互（lower 到最底）
        self._init_dynamic_background()
        # 启动时读回 ui_settings（动效背景开关 / 悬浮卡透明度）
        self._apply_ui_settings()

    # ------------------------------------------------------------------ #
    # ui_settings：开关状态启动时读回（config.json 的 ui_settings 段）
    # ------------------------------------------------------------------ #
    def _ui_config(self):
        """安全拿到 ConfigService；不可用返回 None。"""
        try:
            return getattr(self._facade, "_config", None)
        except Exception:
            return None

    def _apply_ui_settings(self) -> None:
        """按 config 的 ui_settings 段应用界面开关；任何异常都不影响主窗口起来。"""
        try:
            cfg = self._ui_config()
            if cfg is None:
                return
            # 1) 动效背景开关（默认开）——关掉时背景层退化为纯色（不是隐藏整个 widget）
            try:
                bg_on = bool(cfg.get("ui_settings.dynamic_background_enabled", True))
            except Exception:
                bg_on = True
            if getattr(self, "_bg_widget", None) is not None:
                try:
                    self._bg_widget.set_enabled(bg_on)
                except Exception:
                    pass
            # 2) 悬浮卡透明度（0-100，默认 90）——存到属性，供悬浮窗/卡片取用；
            #    同时接到动效背景层（滑条控制粒子/连线不透明度）
            try:
                self._floating_card_opacity = int(cfg.get("ui_settings.floating_card_opacity", 90))
            except Exception:
                self._floating_card_opacity = 90
            if getattr(self, "_bg_widget", None) is not None:
                try:
                    self._bg_widget.set_opacity(self._floating_card_opacity)
                except Exception:
                    pass
        except Exception as e:
            logger.debug("应用 ui_settings 失败（忽略）: %s", e)

    # ------------------------------------------------------------------ #
    # 动效背景（底层粒子层，不可交互）
    # ------------------------------------------------------------------ #
    def _init_dynamic_background(self) -> None:
        """在主窗口底层创建 DynamicBackground；offscreen / Qt 缺失时静默跳过。"""
        self._bg_widget = None
        if not _HAS_DYNAMIC_BG or DynamicBackground is None:
            return
        try:
            self._bg_widget = DynamicBackground(self, particle_count=60)
            # 放到所有内容之下，不挡交互
            self._bg_widget.lower()
            # 跟随主窗口尺寸
            self._bg_widget.resize(self.size())
            self._bg_widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        except Exception as e:
            logger.debug("DynamicBackground 创建失败（忽略，不影响主窗口）: %s", e)
            self._bg_widget = None

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 虚函数签名
        super().resizeEvent(event)
        try:
            if self._bg_widget is not None:
                self._bg_widget.resize(self.size())
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # UI 构建
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        central = QWidget()
        # 中央容器透明：让 lowered 在最底的动效背景层（DynamicBackground）能透出来；
        # 各内容页自身带微透深色底（rgba），视觉上与原 #0f0f1a 几乎一致。
        central.setAttribute(Qt.WA_StyledBackground, False)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_top_bar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_nav_bar())
        body.addWidget(self._build_stack(), stretch=1)

        # 全局右侧快捷面板（26.3.14：最近任务 + 好感度四维 + 快捷动作）
        self._quick_panel = QuickPanel()
        self._quick_panel.setFixedWidth(248)
        self._quick_panel.action_triggered.connect(self._on_quick_action)
        body.addWidget(self._quick_panel)

        root.addLayout(body, stretch=1)

        self._build_status_bar()

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(64)
        bar.setStyleSheet(
            "QWidget#topBar { background-color: #1a1a1d;"
            "border-bottom: 1px solid rgba(255,255,255,0.08); }"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(12)

        # 左侧：头像 + 昵称 + 阶段徽章
        self._avatar = _CircularAvatar("?", 48)
        lay.addWidget(self._avatar)

        info_box = QVBoxLayout()
        info_box.setSpacing(2)
        self._nickname_label = QLabel("未选择对象")
        self._nickname_label.setStyleSheet("color: #e5e5e8; font-size: 12pt; font-weight: 600;")
        self._badge = QLabel("#badge")
        self._badge.setStyleSheet(
            "color: #71717a; font-size: 9pt; background: transparent;"
        )
        info_box.addWidget(self._nickname_label)
        info_box.addWidget(self._badge)
        lay.addLayout(info_box)

        # 好感度环
        self._affection_ring = AffectionRing()
        self._affection_ring.setMinimumSize(56, 56)
        self._affection_ring.setMaximumSize(56, 56)
        lay.addWidget(self._affection_ring)

        lay.addStretch(1)

        # 右侧：搜索框 + 设置按钮
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("搜索对象（回车切换）…")
        self._search_edit.setFixedWidth(240)
        self._search_edit.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.06); color: #e5e5e8;"
            "border: 1px solid rgba(255,255,255,0.10); border-radius: 8px; padding: 6px 10px; }"
            "QLineEdit:focus { border: 1px solid #6366f1; }"
        )
        self._search_edit.returnPressed.connect(self._on_search_person)
        lay.addWidget(self._search_edit)

        self._settings_btn = QPushButton("设置")
        self._settings_btn.setStyleSheet(
            "QPushButton { color: #e5e5e8; background: rgba(255,255,255,0.06);"
            "border: 1px solid rgba(255,255,255,0.10); border-radius: 8px; padding: 6px 14px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.10); color: #fff; }"
        )
        self._settings_btn.clicked.connect(self._on_settings)
        lay.addWidget(self._settings_btn)
        return bar

    def _build_nav_bar(self) -> QWidget:
        nav = QWidget()
        nav.setObjectName("sideBar")
        nav.setFixedWidth(200)
        nav.setStyleSheet(
            "QWidget#sideBar { background-color: #26262b; border-right: 1px solid rgba(255,255,255,0.06); }"
        )
        lay = QVBoxLayout(nav)
        lay.setContentsMargins(10, 16, 10, 16)
        lay.setSpacing(4)

        self._nav_buttons: list[GildedButton] = []
        nav_items = [
            ("聊天分析", PAGE_CHAT_ANALYSIS),
            ("情感咨询", PAGE_CONSULT),
            ("人物档案", PAGE_PROFILE),
            ("英雄墙", PAGE_HERO_WALL),
            ("勋章墙", PAGE_MEDAL_WALL),
            ("日历备忘", PAGE_CALENDAR),
            ("NPC 关系图", PAGE_RELATION_GRAPH),
            ("任务列表", PAGE_TASKS),
            ("数字分身", PAGE_TWIN),
        ]
        self._nav_group = QButtonGroup(self)
        for text, idx in nav_items:
            btn = GildedButton(text)
            btn.setCheckable(True)
            btn.setMinimumHeight(40)
            btn.set_gold_level(1)
            self._nav_group.addButton(btn, idx)
            btn.clicked.connect(lambda checked, i=idx: self._switch_page(i))
            lay.addWidget(btn)
            self._nav_buttons.append(btn)
        lay.addStretch(1)

        # 默认选中第一页
        if self._nav_buttons:
            self._nav_buttons[0].setChecked(True)
        return nav

    def _build_stack(self) -> QWidget:
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_chat_analysis_page())
        self._stack.addWidget(self._build_consult_page())
        self._stack.addWidget(self._build_profile_page())
        self._stack.addWidget(self._build_hero_wall_page())
        self._stack.addWidget(self._build_medal_wall_page())
        self._stack.addWidget(self._build_calendar_page())
        self._stack.addWidget(self._build_relation_graph_page())
        self._stack.addWidget(self._build_tasks_page())
        self._stack.addWidget(self._build_twin_page())
        return self._stack

    # ---- 页面 1：聊天分析 ----
    def _build_chat_analysis_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background-color: #1f1f22;")
        v = QVBoxLayout(page)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        # 顶部工具行
        tool = QHBoxLayout()
        tool.addWidget(QLabel("当前对象："))
        self._person_combo = QComboBox()
        self._person_combo.setStyleSheet(
            "QComboBox { background: rgba(255,255,255,0.06); color: #e5e5e8; border: 1px solid rgba(255,255,255,0.10); padding: 5px 8px; border-radius: 8px; }"
            "QComboBox:focus { border: 1px solid #6366f1; }"
        )
        tool.addWidget(self._person_combo, stretch=1)

        # 截图分析：可勾选项（勾选后自动截图当前屏幕识别）
        self._screenshot_check = QCheckBox("截图分析")
        self._screenshot_check.setStyleSheet("QCheckBox { color: #c8c8cf; spacing: 6px; background: transparent; }")
        self._screenshot_check.setToolTip("勾选后，分析时自动截取当前屏幕并识别文字")
        tool.addWidget(self._screenshot_check)

        self._load_btn = GildedButton("加载分析")
        self._load_btn.set_gold_level(2)
        self._load_btn.clicked.connect(self._on_load_analysis)
        tool.addWidget(self._load_btn)

        # 填入微信按钮：把建议回复一键填入微信输入框
        self._fill_wechat_btn = GildedButton("填入微信")
        self._fill_wechat_btn.set_gold_level(1)
        self._fill_wechat_btn.setToolTip("把建议回复一键填入微信输入框（不自动发送）")
        self._fill_wechat_btn.clicked.connect(self._on_fill_wechat)
        tool.addWidget(self._fill_wechat_btn)
        v.addLayout(tool)

        # 心情状态行（类似微信状态，显示当前心情，点击可手动切换）
        mood_row = QHBoxLayout()
        self._mood_state_btn = QPushButton("💭 设置状态")
        self._mood_state_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.06); color: #e5e5e8; border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px; padding: 6px 14px; text-align: left;
            }
            QPushButton:hover { background: rgba(255,255,255,0.10); border-color: rgba(255,255,255,0.18); }
        """)
        self._mood_state_btn.setCursor(Qt.PointingHandCursor)
        self._mood_state_btn.clicked.connect(self._show_mood_picker)
        mood_row.addWidget(self._mood_state_btn)
        # 惯例情绪模式提示（如"一到晚上就没安全感"）
        self._mood_pattern_label = QLabel("")
        self._mood_pattern_label.setStyleSheet("color: #71717a; font-size: 11px;")
        self._mood_pattern_label.setWordWrap(True)
        mood_row.addWidget(self._mood_pattern_label, stretch=1)
        v.addLayout(mood_row)

        # 候选回复：3 个话术并排（左右三块，不是一个长列表）
        cand_group = QGroupBox("候选回复（三选一）")
        cand_group.setStyleSheet(
            "QGroupBox { color:#a1a1aa; border:1px solid rgba(255,255,255,0.08); border-radius:10px;"
            "margin-top:12px; padding-top:10px; background: rgba(255,255,255,0.02); }"
            "QGroupBox::title { left:12px; padding:0 6px; }")
        cand_lay = QHBoxLayout(cand_group)
        cand_lay.setSpacing(8)
        self._reply_candidate_edits: list[QTextEdit] = []
        for i in range(3):
            box = QTextEdit()
            box.setReadOnly(True)
            box.setPlaceholderText(f"候选 {i + 1}（暂无）")
            box.setMaximumHeight(96)
            box.setStyleSheet(
                "QTextEdit { background: rgba(255,255,255,0.04); color:#e5e5e8;"
                "border:1px solid rgba(255,255,255,0.08); border-radius:8px; padding:8px; }")
            cand_lay.addWidget(box, stretch=1)
            self._reply_candidate_edits.append(box)
        v.addWidget(cand_group)

        # R3.3-22 单轮分析输出卡：意图 + 预测有效度 + 策略 + 3候选 + 风险 + 预测
        self._analysis_output_card = None
        if _HAS_ANALYSIS_CARD and AnalysisOutputCard is not None:
            try:
                self._analysis_output_card = AnalysisOutputCard()
                self._analysis_output_card.setMaximumHeight(260)
                v.addWidget(self._analysis_output_card)
            except Exception as e:
                logger.debug("AnalysisOutputCard 创建失败（忽略）: %s", e)
                self._analysis_output_card = None

        # 主体：左右分栏 —— 左侧8模块卡片，右侧实时任务列表
        splitter = QSplitter(Qt.Horizontal)

        # 左侧：滚动区 8 张卡片
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setSpacing(10)
        grid.setContentsMargins(0, 0, 0, 0)

        for i, (key, title) in enumerate(_ANALYSIS_CARDS):
            box = QGroupBox(title)
            box.setStyleSheet(
                "QGroupBox { color: #a1a1aa; border: 1px solid rgba(255,255,255,0.08);"
                "border-radius: 10px; margin-top: 12px; padding-top: 10px; background: rgba(255,255,255,0.02); }"
                "QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }"
            )
            bv = QVBoxLayout(box)
            lbl = QLabel("（暂无数据）")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #c8c8cf; background: transparent;")
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            bv.addWidget(lbl)
            self._analysis_card_labels[key] = lbl

            r, c = divmod(i, 2)
            grid.addWidget(box, r, c)

        scroll.setWidget(inner)
        splitter.addWidget(scroll)

        # 右侧：实时任务列表（任务引擎自动更新，无需手动触发）
        task_panel = QWidget()
        task_lay = QVBoxLayout(task_panel)
        task_lay.setContentsMargins(4, 4, 4, 4)
        task_header = QLabel("<b style='color:#e5e5e8'>实时任务</b>")
        task_lay.addWidget(task_header)
        self._task_list_widget = QListWidget()
        self._task_list_widget.setStyleSheet("""
            QListWidget { background: rgba(255,255,255,0.03); color: #e5e5e8; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 4px; }
            QListWidget::item { padding: 8px 10px; border-radius: 6px; color: #c8c8cf; }
            QListWidget::item:hover { background: rgba(255,255,255,0.06); }
            QListWidget::item:selected { background: rgba(99,102,241,0.25); color: #fff; }
        """)
        task_lay.addWidget(self._task_list_widget, stretch=1)
        # 任务统计
        self._task_stats_label = QLabel("进行中: 0 | 已完成: 0")
        self._task_stats_label.setStyleSheet("color: #71717a; font-size: 11px;")
        task_lay.addWidget(self._task_stats_label)
        splitter.addWidget(task_panel)

        # 设置分栏比例（卡片占大部分，任务栏窄一些）
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([700, 240])

        v.addWidget(splitter, stretch=1)
        return page

    # ---- 页面 2：情感咨询 ----
    def _build_consult_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background-color: #1f1f22;")
        v = QVBoxLayout(page)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(8)

        # 模式切换
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("咨询模式："))
        self._consult_mode = QComboBox()
        self._consult_mode.addItem("主窗口模式（自我成长）", "self")
        self._consult_mode.addItem("目标窗口模式（对象视角）", "other")
        self._consult_mode.setStyleSheet(
            "QComboBox { background: rgba(255,255,255,0.06); color: #e5e5e8; border: 1px solid rgba(255,255,255,0.10); padding: 5px 8px; border-radius: 8px; }"
            "QComboBox:focus { border: 1px solid #6366f1; }"
        )
        mode_row.addWidget(self._consult_mode, stretch=1)
        v.addLayout(mode_row)

        # 历史（借鉴 Cherry Studio：用户消息右对齐浅灰气泡，AI 消息左对齐无气泡）
        self._consult_history = QTextEdit()
        self._consult_history.setReadOnly(True)
        self._consult_history.setStyleSheet(
            "QTextEdit { background: #1a1a1d; color: #e5e5e8; border: none; border-radius: 10px; padding: 12px; }"
        )
        v.addWidget(self._consult_history, stretch=1)

        # 输入行
        in_row = QHBoxLayout()
        self._consult_input = QLineEdit()
        self._consult_input.setPlaceholderText("输入你的情感困惑…")
        self._consult_input.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.06); color: #e5e5e8; border: 1px solid rgba(255,255,255,0.10); padding: 8px 12px; border-radius: 10px; }"
            "QLineEdit:focus { border: 1px solid #6366f1; background: rgba(255,255,255,0.08); }"
        )
        self._consult_input.returnPressed.connect(self._on_send_consult)
        in_row.addWidget(self._consult_input, stretch=1)

        send_btn = GildedButton("发送")
        send_btn.set_gold_level(2)
        send_btn.clicked.connect(self._on_send_consult)
        in_row.addWidget(send_btn)
        v.addLayout(in_row)
        return page

    # ---- 页面 3：人物档案 ----
    def _build_profile_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background-color: #1f1f22;")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        h = QHBoxLayout(host)
        h.setContentsMargins(14, 14, 14, 14)
        h.setSpacing(14)

        # 左侧：基本信息表单 + 心理学侧写 + 资料优化建议卡
        left = QVBoxLayout()
        left.addWidget(QLabel("<h3 style='color:#e5e5e8'>基本信息</h3>"))
        self._profile_fields: dict[str, QLineEdit] = {}
        for field, label_text in [
            ("nickname", "昵称"),
            ("wechat_nickname", "微信昵称"),
            ("gender", "性别"),
            ("birthday", "生日"),
            ("occupation", "职业"),
            ("relationship_stage", "关系阶段"),
        ]:
            row = QHBoxLayout()
            row.addWidget(QLabel(label_text + "："))
            ed = QLineEdit()
            ed.setStyleSheet(
                "QLineEdit { background: rgba(255,255,255,0.06); color: #e5e5e8; border: 1px solid rgba(255,255,255,0.10); padding: 5px 8px; border-radius: 8px; }"
            )
            self._profile_fields[field] = ed
            row.addWidget(ed, stretch=1)
            left.addLayout(row)

        left.addWidget(QLabel("<h3 style='color:#e5e5e8'>心理学侧写</h3>"))
        self._portrait_edit = QTextEdit()
        self._portrait_edit.setReadOnly(True)
        self._portrait_edit.setMinimumHeight(150)
        self._portrait_edit.setStyleSheet(
            "QTextEdit { background: rgba(255,255,255,0.04); color: #e5e5e8; border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 6px; }"
        )
        left.addWidget(self._portrait_edit)

        # 23.7 个人资料优化建议卡
        self._profile_optimize_card = ProfileOptimizeCard()
        left.addWidget(self._profile_optimize_card)
        left.addStretch(1)
        h.addLayout(left, stretch=1)

        # 右侧：18 维雷达图 + 完整诊断报告入口 + 关系时间线（23.8）
        right = QVBoxLayout()
        right.addWidget(QLabel("<h3 style='color:#e5e5e8'>好感度雷达</h3>"))
        self._radar = RadarChart()
        self._radar.setMinimumHeight(300)
        right.addWidget(self._radar)
        self._diag_btn = QPushButton("🔬 完整诊断报告（18部分）")
        self._diag_btn.setStyleSheet(
            "QPushButton { color:#ffffff; background:#6366f1; padding:8px 12px; font-weight:600; border-radius:8px; border:none; }"
            "QPushButton:hover { background:#7c7ff5; }"
        )
        self._diag_btn.clicked.connect(self._on_show_diagnostic_report)
        right.addWidget(self._diag_btn)

        right.addWidget(QLabel("<h3 style='color:#e5e5e8'>关系时间线</h3>"))
        self._timeline_widget = TimelineWidget()
        self._timeline_widget.setMinimumHeight(270)
        right.addWidget(self._timeline_widget)
        h.addLayout(right, stretch=1)

        scroll.setWidget(host)
        outer.addWidget(scroll)
        return page

    # ---- 页面 4：英雄墙 ----
    def _build_hero_wall_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background-color: #1f1f22;")
        v = QVBoxLayout(page)
        v.setContentsMargins(14, 14, 14, 14)
        label = QLabel("<h3 style='color:#e5e5e8'>英雄墙 — 点击对象卡片切换</h3>")
        v.addWidget(label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        self._hero_inner = QWidget()
        self._hero_grid = QGridLayout(self._hero_inner)
        self._hero_grid.setSpacing(12)
        self._hero_grid.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self._hero_inner)
        v.addWidget(scroll, stretch=1)
        return page

    # ---- 页面 5：勋章墙 ----
    def _build_medal_wall_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background-color: #1f1f22;")
        v = QVBoxLayout(page)
        v.setContentsMargins(14, 14, 14, 14)
        v.addWidget(QLabel("<h3 style='color:#e5e5e8'>勋章墙</h3>"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        self._medal_inner = QWidget()
        self._medal_grid = QGridLayout(self._medal_inner)
        self._medal_grid.setSpacing(12)
        scroll.setWidget(self._medal_inner)
        v.addWidget(scroll, stretch=1)
        return page

    # ---- 页面 6：日历备忘（生理周期日历 + 备忘录）----
    def _build_calendar_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background-color: #1f1f22;")
        main_lay = QHBoxLayout(page)
        main_lay.setContentsMargins(14, 14, 14, 14)
        main_lay.setSpacing(12)

        # ===== 左侧：生理周期日历 =====
        left_panel = QWidget()
        left_lay = QVBoxLayout(left_panel)
        left_lay.setContentsMargins(0, 0, 0, 0)

        left_lay.addWidget(QLabel("<h3 style='color:#e5e5e8'>生理周期日历</h3>"))

        # 日历控件
        self._cycle_calendar = QCalendarWidget()
        self._cycle_calendar.setStyleSheet("""
            QCalendarWidget { background: #26262b; color: #e5e5e8; }
            QCalendarWidget QToolButton { color: #e5e5e8; background: transparent; padding: 6px; border-radius: 6px; }
            QCalendarWidget QMenu { background: #26262b; color: #e5e5e8; }
            QCalendarWidget QSpinBox { background: #26262b; color: #e5e5e8; }
            QCalendarWidget QAbstractItemView {
                background: #26262b; color: #e5e5e8;
                selection-background-color: #6366f1; selection-color: #ffffff;
            }
            QCalendarWidget QWidget#qt_calendar_navigationbar { background: #1f1f22; }
        """)
        self._cycle_calendar.setGridVisible(True)
        left_lay.addWidget(self._cycle_calendar)

        # 生理周期录入区
        entry_group = QGroupBox("周期录入")
        entry_lay = QVBoxLayout()

        # 当前阶段显示
        self._cycle_phase_label = QLabel("当前阶段：暂无记录")
        self._cycle_phase_label.setWordWrap(True)
        self._cycle_phase_label.setStyleSheet("color: #e5e5e8; padding: 4px; font-weight: 600;")
        entry_lay.addWidget(self._cycle_phase_label)

        # 标记按钮行（女性）
        btn_row1 = QHBoxLayout()
        self._mark_period_btn = QPushButton("标记姨妈开始")
        self._mark_period_btn.setStyleSheet("background: #e74c3c; color: white; padding: 6px;")
        self._mark_period_btn.clicked.connect(self._mark_period_start)
        self._mark_period_end_btn = QPushButton("标记姨妈结束")
        self._mark_period_end_btn.setStyleSheet("background: #27ae60; color: white; padding: 6px;")
        self._mark_period_end_btn.clicked.connect(self._mark_period_end)
        btn_row1.addWidget(self._mark_period_btn)
        btn_row1.addWidget(self._mark_period_end_btn)
        entry_lay.addLayout(btn_row1)

        # 标记按钮行（男性情绪周期/大姨夫）
        btn_row_male = QHBoxLayout()
        self._mark_male_low_btn = QPushButton("标记低落期开始")
        self._mark_male_low_btn.setStyleSheet("background: #6366f1; color: white; padding: 6px;")
        self._mark_male_low_btn.setToolTip("男性情绪周期（大姨夫）：观察到对方情绪低落/冷漠/需要独处时标记")
        self._mark_male_low_btn.clicked.connect(self._mark_male_low_start)
        btn_row_male.addWidget(self._mark_male_low_btn)
        male_hint = QLabel("（男性大姨夫）")
        male_hint.setStyleSheet("color: #888; font-size: 10px;")
        btn_row_male.addWidget(male_hint)
        entry_lay.addLayout(btn_row_male)

        # 周期长度设置
        len_row = QHBoxLayout()
        len_row.addWidget(QLabel("周期天数："))
        self._cycle_length = QComboBox()
        self._cycle_length.addItems(["26", "27", "28", "29", "30", "31", "32", "35"])
        self._cycle_length.setCurrentText("28")
        len_row.addWidget(self._cycle_length)
        len_row.addWidget(QLabel("经期天数："))
        self._period_length = QComboBox()
        self._period_length.addItems(["3", "4", "5", "6", "7"])
        self._period_length.setCurrentText("5")
        len_row.addWidget(self._period_length)
        entry_lay.addLayout(len_row)

        # 阶段图例
        legend = QLabel(
            "<span style='color:#e74c3c'>■</span> 经期　"
            "<span style='color:#3498db'>■</span> 卵泡期　"
            "<span style='color:#f39c12'>■</span> 排卵期　"
            "<span style='color:#9b59b6'>■</span> 黄体期"
        )
        legend.setStyleSheet("font-size: 11px; padding: 4px;")
        entry_lay.addWidget(legend)

        # 攻略提示
        self._cycle_tip_label = QLabel("")
        self._cycle_tip_label.setWordWrap(True)
        self._cycle_tip_label.setStyleSheet("color: #71717a; font-size: 11px; padding: 4px 8px; background: rgba(255,255,255,0.04); border-radius: 6px;")
        entry_lay.addWidget(self._cycle_tip_label)

        entry_group.setLayout(entry_lay)
        left_lay.addWidget(entry_group)

        main_lay.addWidget(left_panel, stretch=3)

        # ===== 右侧：备忘录 =====
        right_panel = QWidget()
        right_lay = QVBoxLayout(right_panel)
        right_lay.setContentsMargins(0, 0, 0, 0)

        right_lay.addWidget(QLabel("<h3 style='color:#e5e5e8'>备忘录</h3>"))

        # 备忘录列表
        self._memo_list = QListWidget()
        self._memo_list.setStyleSheet("""
            QListWidget { background: rgba(255,255,255,0.03); color: #e5e5e8; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 4px; }
            QListWidget::item { padding: 8px 10px; border-radius: 6px; color: #c8c8cf; }
            QListWidget::item:hover { background: rgba(255,255,255,0.06); }
        """)
        right_lay.addWidget(self._memo_list, stretch=1)

        # 添加备忘录
        add_group = QGroupBox("添加备忘")
        add_lay = QVBoxLayout()
        self._memo_input = QLineEdit()
        self._memo_input.setPlaceholderText("输入备忘内容...")
        self._memo_input.returnPressed.connect(self._add_memo)
        add_lay.addWidget(self._memo_input)

        memo_btn_row = QHBoxLayout()
        self._memo_category = QComboBox()
        self._memo_category.addItems(["日常", "重要", "纪念日", "待办", "喜恶", "其他"])
        add_memo_btn = QPushButton("添加")
        add_memo_btn.clicked.connect(self._add_memo)
        del_memo_btn = QPushButton("删除选中")
        del_memo_btn.setStyleSheet("background: #e74c3c; color: white;")
        del_memo_btn.clicked.connect(self._delete_memo)
        memo_btn_row.addWidget(self._memo_category)
        memo_btn_row.addWidget(add_memo_btn)
        memo_btn_row.addWidget(del_memo_btn)
        add_lay.addLayout(memo_btn_row)
        add_group.setLayout(add_lay)
        right_lay.addWidget(add_group)

        main_lay.addWidget(right_panel, stretch=2)

        return page

    # ---- 页面 7：NPC 关系网络图 ----
    def _build_relation_graph_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background-color: #1f1f22;")
        v = QVBoxLayout(page)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(8)
        v.addWidget(QLabel("<h3 style='color:#e5e5e8'>NPC 关系图</h3>"))

        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        # 图控件（可选依赖：PySide6/offscreen 缺失时静默降级）
        self._rel_graph_widget = None
        try:
            from app.widgets.common.relationship_graph_widget import RelationshipGraphWidget
            self._rel_graph_widget = RelationshipGraphWidget()
            self._rel_graph_widget.setMinimumHeight(420)
            cl.addWidget(self._rel_graph_widget, stretch=1)
        except Exception as e:
            logger.debug("RelationshipGraphWidget 不可用（显示空态）: %s", e)
            self._rel_graph_widget = None

        # 空态标签（无数据或控件不可用时显示）
        self._rel_empty_label = QLabel("暂无 NPC 关系数据")
        self._rel_empty_label.setAlignment(Qt.AlignCenter)
        self._rel_empty_label.setStyleSheet(
            "color:#71717a; font-size:13px; padding:40px;"
            "background:rgba(255,255,255,0.02); border:1px dashed rgba(255,255,255,0.10); border-radius:10px;"
        )
        cl.addWidget(self._rel_empty_label)

        v.addWidget(container, stretch=1)

        # 异步刷新一次（等对象/聊天数据就绪后再拉）
        QTimer.singleShot(0, self._refresh_relation_graph)
        return page

    def _refresh_relation_graph(self) -> None:
        """从聊天记录提取 NPC 关系图；无数据时显示空态「暂无 NPC 关系数据」。"""
        widget = getattr(self, "_rel_graph_widget", None)
        empty = getattr(self, "_rel_empty_label", None)
        if widget is None:
            if empty is not None:
                empty.show()
            return

        graph = None
        try:
            pid = self._get_current_person_id()
            if pid:
                from services.relationship_graph import extract_from_chats, layout_graph
                resp = self._facade.get_chat_messages(pid, limit=200)
                if resp.get("ok"):
                    msgs = (resp.get("data") or {}).get("messages", [])
                    raw = extract_from_chats(pid, msgs or [])
                    # 注入中心「我」节点：extract_from_chats 只产出 NPC 节点，
                    # 缺中心节点会让 source=pid 的边在布局里被丢弃。补上后力导向以「我」为原点。
                    raw_nodes = raw.get("nodes", []) or []
                    if not any(n.get("id") == pid for n in raw_nodes):
                        raw_nodes = [{"id": pid, "name": "我", "relation_type": "其他",
                                      "intimacy": 100}] + raw_nodes
                        raw["nodes"] = raw_nodes
                    # 力导向布局（Fruchterman-Reingold）：得到 [-1,1] 居中坐标；
                    # 再投影成 2D（x/y）喂给原生 QWidget 自绘控件。
                    laid = layout_graph(raw, iterations=200, seed=42, dims=3)
                    # 适配控件坐标系：[-1,1] 居中 -> [0,1]；边键 source/target -> source_id/target_id
                    graph = {
                        "center": laid.get("center", pid),
                        "colors": laid.get("colors", {}),
                        "nodes": [
                            {
                                "id": n["id"],
                                "name": n.get("name", n["id"]),
                                "relation_type": n.get("relation_type", "其他"),
                                "intimacy": n.get("intimacy", 30),
                                "x": n.get("x", 0.0) * 0.5 + 0.5,
                                "y": n.get("y", 0.0) * 0.5 + 0.5,
                            }
                            for n in laid.get("nodes", [])
                        ],
                        "edges": [
                            {
                                "source_id": e.get("source"),
                                "target_id": e.get("target"),
                                "strength": e.get("strength", 0.5),
                            }
                            for e in laid.get("edges", [])
                        ],
                    }
        except Exception as e:
            logger.debug("提取 NPC 关系图失败（空态兜底）: %s", e)
            graph = None

        if graph and graph.get("nodes"):
            try:
                widget.set_graph(graph)
            except Exception:
                pass
            widget.show()
            if empty is not None:
                empty.hide()
        else:
            widget.hide()
            if empty is not None:
                empty.show()

    # ---- 页面 7：任务列表（进度总览）----
    def _build_tasks_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background-color: #1f1f22;")
        v = QVBoxLayout(page)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(QLabel("<h3 style='color:#e5e5e8'>任务看板</h3>"))
        head.addStretch(1)
        self._tasks_stats_label = QLabel("全部任务: 0")
        self._tasks_stats_label.setStyleSheet("color:#b0b0c0; font-size:12px;")
        head.addWidget(self._tasks_stats_label)
        refresh_btn = GildedButton("刷新")
        refresh_btn.set_gold_level(1)
        refresh_btn.clicked.connect(self._refresh_tasks_page)
        head.addWidget(refresh_btn)
        v.addLayout(head)

        # 26.2.5 四列任务看板（横向滚动：待办 / 进行中 / 已完成 / 已放弃）
        board_scroll = QScrollArea()
        board_scroll.setWidgetResizable(True)
        board_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        board_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        board_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self._kanban = KanbanWidget()
        self._kanban.board_changed.connect(self._on_kanban_changed)
        board_scroll.setWidget(self._kanban)
        v.addWidget(board_scroll, stretch=1)

        # 对象切换后/分析完成后自动刷新一次
        QTimer.singleShot(0, self._refresh_tasks_page)
        return page

    def _refresh_tasks_page(self) -> None:
        """驱动四列任务看板：从 facade.get_task_board 分桶渲染；任何异常都空态兜底。"""
        kanban = getattr(self, "_kanban", None)
        if kanban is None:
            return
        pid = self._get_current_person_id()
        total = 0
        try:
            if pid:
                kanban.load_from_facade(self._facade, pid)
                resp = self._facade.get_task_board(pid)
                if resp.get("ok"):
                    total = int((resp.get("data") or {}).get("total") or 0)
        except Exception as e:
            logger.debug("任务看板刷新失败: %s", e)
        if hasattr(self, "_tasks_stats_label"):
            self._tasks_stats_label.setText(f"全部任务: {total}")

    def _on_kanban_changed(self) -> None:
        """看板内完成/放弃任务后：联动刷新统计、右侧快捷面板与实时任务列表。"""
        self._refresh_tasks_page()
        self._refresh_task_list()
        self._refresh_quick_panel()

    # ---- 页面 9：数字分身管理（26.2.10）----
    def _build_twin_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background-color: #1f1f22;")
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        self._twin_panel = DigitalTwinPanel()
        self._twin_panel.mode_changed.connect(self._on_twin_mode_changed)
        v.addWidget(self._twin_panel)
        QTimer.singleShot(0, self._refresh_twin_panel)
        return page

    def _refresh_twin_panel(self) -> None:
        panel = getattr(self, "_twin_panel", None)
        if panel is None:
            return
        pid = self._get_current_person_id()
        try:
            panel.load_from_facade(self._facade, pid or "")
        except Exception as e:
            logger.debug("数字分身面板刷新失败: %s", e)

    def _on_twin_mode_changed(self, mode: str) -> None:
        try:
            sb = self.statusBar()
            if sb is not None:
                names = {"partner": "伴侣分身", "growth": "成长分身", "adversary": "对手分身"}
                sb.showMessage(f"数字分身已切换为：{names.get(mode, mode)}", 4000)
        except Exception:
            pass

    # ---- 右侧快捷面板（26.3.14）----
    def _refresh_quick_panel(self) -> None:
        panel = getattr(self, "_quick_panel", None)
        if panel is None:
            return
        pid = self._get_current_person_id()
        try:
            panel.load_from_facade(self._facade, pid or "")
        except Exception as e:
            logger.debug("右侧快捷面板刷新失败: %s", e)

    def _on_quick_action(self, action_name: str) -> None:
        """快捷面板动作：截图分析 / 代聊 / 查档案 —— 跳到对应页并尽量直接触发。"""
        try:
            if action_name in ("查档案",):
                self._switch_page(PAGE_PROFILE)
                if hasattr(self, "_nav_buttons") and PAGE_PROFILE < len(self._nav_buttons):
                    self._nav_buttons[PAGE_PROFILE].setChecked(True)
            elif action_name in ("代聊",):
                self._switch_page(PAGE_CONSULT)
                if hasattr(self, "_nav_buttons") and PAGE_CONSULT < len(self._nav_buttons):
                    self._nav_buttons[PAGE_CONSULT].setChecked(True)
                self._consult_input.setFocus()
            elif action_name in ("截图分析",):
                self._switch_page(PAGE_CHAT_ANALYSIS)
                if hasattr(self, "_nav_buttons") and PAGE_CHAT_ANALYSIS < len(self._nav_buttons):
                    self._nav_buttons[PAGE_CHAT_ANALYSIS].setChecked(True)
                # 勾选截图分析并直接触发一次分析
                if hasattr(self, "_screenshot_check"):
                    self._screenshot_check.setChecked(True)
                self._on_load_analysis()
            else:
                # 其余动作默认回到聊天分析页
                self._switch_page(PAGE_CHAT_ANALYSIS)
        except Exception as e:
            logger.debug("快捷动作触发失败: %s", e)

    # ---- 五个新组件统一随对象切换刷新 ----
    def _refresh_new_components(self) -> None:
        pid = self._get_current_person_id() or ""
        # 时间线（23.8）
        tl = getattr(self, "_timeline_widget", None)
        if tl is not None:
            try:
                tl.load_from_facade(self._facade, pid)
            except Exception as e:
                logger.debug("时间线刷新失败: %s", e)
        # 资料优化卡（23.7）
        poc = getattr(self, "_profile_optimize_card", None)
        if poc is not None:
            try:
                poc.load_from_facade(self._facade, pid)
            except Exception as e:
                logger.debug("资料优化卡刷新失败: %s", e)
        # 看板 / 分身 / 快捷面板
        self._refresh_tasks_page()
        self._refresh_twin_panel()
        self._refresh_quick_panel()

    # ---- 生理周期相关方法 ----
    def _get_current_person_id(self):
        """获取当前选中的人物ID（下拉框优先，未填充时回退到已记录的激活对象）。"""
        # 下拉框当前项
        data = self._person_combo.currentData() if hasattr(self, "_person_combo") else None
        if data:
            return data
        # 首启填充下拉框之前、或刚切换尚未同步时，回退到已记录的激活对象
        return getattr(self, "_current_person_id", None)

    def _mark_period_start(self):
        """标记姨妈期开始。"""
        pid = self._get_current_person_id()
        date = self._cycle_calendar.selectedDate()
        date_str = date.toString("yyyy-MM-dd")
        try:
            if self._facade and hasattr(self._facade, 'services'):
                cycle = self._facade.services.get('cycle')
                if cycle and hasattr(cycle, 'record_period_start'):
                    cycle.record_period_start(pid, date_str)
                    QMessageBox.information(self, "周期", f"已标记 {date_str} 为姨妈期开始")
                    self._refresh_cycle_display()
                    return
            QMessageBox.information(self, "周期", f"姨妈开始: {date_str}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"标记失败: {e}")

    def _mark_period_end(self):
        """标记姨妈期结束。"""
        pid = self._get_current_person_id()
        date = self._cycle_calendar.selectedDate()
        date_str = date.toString("yyyy-MM-dd")
        try:
            if self._facade and hasattr(self._facade, 'services'):
                cycle = self._facade.services.get('cycle')
                if cycle and hasattr(cycle, 'record_period_end'):
                    cycle.record_period_end(pid, date_str)
                    QMessageBox.information(self, "周期", f"已标记 {date_str} 为姨妈期结束")
                    self._refresh_cycle_display()
                    return
            QMessageBox.information(self, "周期", f"姨妈结束: {date_str}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"标记失败: {e}")

    def _mark_male_low_start(self):
        """标记男性低落期（大姨夫）开始。"""
        pid = self._get_current_person_id()
        date = self._cycle_calendar.selectedDate()
        date_str = date.toString("yyyy-MM-dd")
        try:
            if self._facade and hasattr(self._facade, 'services'):
                cycle = self._facade.services.get('cycle')
                if cycle and hasattr(cycle, 'record_male_low_phase'):
                    result = cycle.record_male_low_phase(pid, date_str)
                    if result.get("ok"):
                        QMessageBox.information(self, "男性周期",
                            f"已标记 {date_str} 为低落期开始\n"
                            f"系统将自动推算精力充沛期/平稳期/恢复期")
                        self._refresh_cycle_display()
                        return
            QMessageBox.information(self, "男性周期", f"低落期开始: {date_str}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"标记失败: {e}")

    def _refresh_cycle_display(self):
        """刷新周期阶段显示和攻略提示（自动判断男女）。"""
        pid = self._get_current_person_id()
        try:
            if self._facade and hasattr(self._facade, 'services'):
                cycle = self._facade.services.get('cycle')
                if cycle and pid:
                    # 自动根据性别选择
                    phase = None
                    gender = "female"
                    if hasattr(cycle, 'get_current_phase_auto'):
                        phase = cycle.get_current_phase_auto(pid)
                        if hasattr(cycle, '_get_person_gender'):
                            gender = cycle._get_person_gender(pid)
                    elif hasattr(cycle, 'get_current_phase'):
                        phase = cycle.get_current_phase(pid)

                    if phase:
                        name = phase.get('phase_name', phase.get('name', ''))
                        gender_label = "（男）" if gender == "male" else ""
                        self._cycle_phase_label.setText(f"当前阶段{gender_label}：{name}")
                        # 攻略提示（直接用服务返回的advice）
                        advice = phase.get('advice', '')
                        if advice:
                            self._cycle_tip_label.setText(f"攻略提示：{advice}")
                        else:
                            # 兼容旧逻辑
                            tips = {
                                "经期": "经期情绪易波动，宜体贴照顾，避免争执，可主动关心身体状况",
                                "卵泡期": "卵泡期情绪稳定、心情较好，适合邀约和推进关系",
                                "排卵期": "排卵期状态佳、魅力感强，适合约会、表白等重要行动",
                                "黄体期": "黄体期后期易烦躁焦虑（经前综合征），宜包容理解，少硬碰硬",
                                "低落": "他需要空间而非追问，别逼他说话，默默陪伴，给台阶下",
                                "精力": "对方状态好，适合一起做有挑战性的事，给予认可和崇拜",
                                "恢复": "他恢复后会有补偿心理，适合适度撒娇和提小要求",
                            }
                            for key, tip in tips.items():
                                if key in str(name):
                                    self._cycle_tip_label.setText(f"攻略提示：{tip}")
                                    break
        except Exception as e:
            logger.debug(f"刷新周期显示失败: {e}")

    # ---- 备忘录相关方法 ----
    def _add_memo(self):
        """添加备忘录条目。"""
        text = self._memo_input.text().strip()
        if not text:
            return
        category = self._memo_category.currentText()
        try:
            pid = self._get_current_person_id()
            if self._facade and hasattr(self._facade, 'services'):
                memory = self._facade.services.get('memory')
                if memory and pid and hasattr(memory, 'add_memory'):
                    memory.add_memory(pid, "memo", text, category=category, source="manual")
            # 添加到列表显示
            item = QListWidgetItem(f"【{category}】{text}")
            if category == "重要":
                item.setForeground(QColor("#e74c3c"))
            elif category == "纪念日":
                item.setForeground(QColor("#e5e5e8"))
            self._memo_list.addItem(item)
            self._memo_input.clear()
        except Exception as e:
            QMessageBox.warning(self, "错误", f"添加失败: {e}")

    def _delete_memo(self):
        """删除选中的备忘录。"""
        current = self._memo_list.currentRow()
        if current >= 0:
            self._memo_list.takeItem(current)

    def _build_status_bar(self) -> None:
        sb = QStatusBar()
        sb.setStyleSheet(
            "QStatusBar { background: #1a1a1d; color: #71717a;"
            "border-top: 1px solid rgba(255,255,255,0.06); }"
        )
        self.setStatusBar(sb)
        self._status_level = QLabel("等级: -")
        self._status_coins = QLabel("金币: -")
        self._status_medals = QLabel("勋章: 0/0")
        sb.addWidget(self._status_level)
        sb.addWidget(self._status_coins)
        sb.addPermanentWidget(self._status_medals)

    # ------------------------------------------------------------------ #
    # 信号连接
    # ------------------------------------------------------------------ #
    def _connect_signals(self) -> None:
        self._signals.person_switched.connect(self._on_person_switched)
        self._signals.affection_changed.connect(self._on_affection_changed)
        self._signals.analysis_ready.connect(self._on_analysis_ready)
        self._signals.task_updated.connect(self._on_task_updated)
        self._signals.medal_unlocked.connect(self._on_medal_unlocked)
        self._signals.app_quitting.connect(self.close)

    # ------------------------------------------------------------------ #
    # 数据加载
    # ------------------------------------------------------------------ #
    def _reload_active_person(self) -> None:
        """从 facade 拉取当前激活对象并刷新顶部栏 / 各页面。"""
        try:
            resp = self._facade.get_active_person()
            if not resp.get("ok"):
                return
            data = resp.get("data") or {}
            person = data.get("person")
            pid = data.get("active_person_id")
            # 先填充并选中激活对象，保证后续 _apply_person 内 _get_current_person_id 可用
            self._reload_person_combo()
            if person:
                self._current_person_id = pid
                self._apply_person(person)
            self._reload_affection()
            self._reload_hero_wall()
            self._reload_medal_wall()
        except Exception:
            logger.exception("加载当前对象失败")

    def _apply_person(self, person: dict) -> None:
        """把 person 字典填充到顶部栏与档案表单。"""
        nickname = person.get("nickname") or person.get("wechat_nickname") or "未命名"
        self._nickname_label.setText(nickname)
        stage = person.get("relationship_stage", 0)
        self._badge.setText(f"阶段 {stage} · {person.get('gender','?')} · {person.get('occupation','')}")
        portrait = person.get("portrait_path") or ""
        self._avatar.set_pixmap(portrait)

        for field, ed in self._profile_fields.items():
            val = person.get(field, "")
            ed.setText(str(val) if val is not None else "")
        self._portrait_edit.setPlainText(person.get("soul_path") or "")

        # 刷新实时任务列表、周期显示、备忘录
        self._refresh_task_list()
        self._refresh_cycle_display()
        self._refresh_memo_list()
        self._refresh_mood_state()
        # 五个新组件（时间线/看板/分身/快捷面板/资料优化卡）随对象切换联动
        self._refresh_new_components()
        # 若 NPC 关系图页已构建，跟随当前对象刷新
        if hasattr(self, "_rel_graph_widget") or hasattr(self, "_rel_empty_label"):
            try:
                self._refresh_relation_graph()
            except Exception:
                pass

    def _refresh_task_list(self) -> None:
        """从任务引擎加载实时任务列表（自动更新，无需手动触发）。"""
        if not hasattr(self, '_task_list_widget'):
            return
        pid = self._get_current_person_id()
        self._task_list_widget.clear()
        active_count = 0
        done_count = 0
        try:
            task_engine = None
            if hasattr(self._facade, 'services'):
                task_engine = self._facade.services.get('task_engine')
            if task_engine and pid and hasattr(task_engine, 'get_active_tasks'):
                tasks = task_engine.get_active_tasks(pid)
                active_count = len(tasks)
                for t in tasks:
                    title = t.get('title') or t.get('name') or '任务'
                    status = t.get('status', '')
                    priority = t.get('priority', 'normal')
                    # 进度
                    progress = ""
                    if t.get('current_step') is not None and t.get('total_steps'):
                        progress = f" ({t['current_step']}/{t['total_steps']})"
                    # 状态图标
                    icon = "○" if status == 'pending' else "◐"
                    item = QListWidgetItem(f"{icon} {title}{progress}")
                    if status == 'in_progress':
                        item.setForeground(QColor("#e5e5e8"))
                    if priority == 'high':
                        item.setForeground(QColor("#e74c3c"))
                    self._task_list_widget.addItem(item)
            # 统计已完成任务
            if task_engine and pid and hasattr(task_engine, 'db'):
                try:
                    done_rows = task_engine.db.fetch_all(
                        "SELECT COUNT(*) as cnt FROM tasks WHERE person_id=:pid AND status='completed'",
                        {"pid": pid}
                    )
                    if done_rows:
                        done_count = done_rows[0].get('cnt', 0)
                except Exception:
                    pass
            if hasattr(self, '_task_stats_label'):
                self._task_stats_label.setText(f"进行中: {active_count} | 已完成: {done_count}")
        except Exception as e:
            logger.debug(f"刷新任务列表失败: {e}")

    def _refresh_memo_list(self) -> None:
        """从记忆服务加载备忘录列表。"""
        if not hasattr(self, '_memo_list'):
            return
        pid = self._get_current_person_id()
        self._memo_list.clear()
        try:
            memory = None
            if hasattr(self._facade, 'services'):
                memory = self._facade.services.get('memory')
            if memory and pid and hasattr(memory, 'get_memories'):
                memos = memory.get_memories(pid, memory_type="memo")
                for m in memos or []:
                    content = m.get('content', '')
                    category = m.get('category', '日常')
                    item = QListWidgetItem(f"【{category}】{content}")
                    if category == "重要":
                        item.setForeground(QColor("#e74c3c"))
                    elif category == "纪念日":
                        item.setForeground(QColor("#e5e5e8"))
                    self._memo_list.addItem(item)
        except Exception as e:
            logger.debug(f"刷新备忘录失败: {e}")

    # ---- 心情状态相关方法 ----
    def _refresh_mood_state(self) -> None:
        """刷新当前心情状态显示和惯例情绪模式。"""
        if not hasattr(self, '_mood_state_btn'):
            return
        pid = self._get_current_person_id()
        try:
            mood = None
            if hasattr(self._facade, 'services'):
                mood = self._facade.services.get('mood')
            if mood and pid:
                # 当前心情
                current = mood.get_current_mood(pid)
                if current:
                    emoji = current.get('emoji', '')
                    name = current.get('name', '')
                    self._mood_state_btn.setText(f"{emoji} {name}")
                else:
                    self._mood_state_btn.setText("💭 设置状态")
                # 惯例情绪模式
                patterns = mood.get_patterns(pid)
                pattern_texts = []
                for p in (patterns or [])[:3]:
                    tag_name = p.get('mood_tag', p.get('tag', ''))
                    # 转换tag为中文名
                    tag_info = mood.MOOD_TAGS.get(tag_name, {})
                    cname = tag_info.get('name', tag_name)
                    time_hint = p.get('time_pattern', p.get('pattern', ''))
                    if time_hint:
                        pattern_texts.append(f"{cname}（{time_hint}）")
                    else:
                        pattern_texts.append(cname)
                if pattern_texts:
                    self._mood_pattern_label.setText("惯例模式：" + "、".join(pattern_texts))
                else:
                    self._mood_pattern_label.setText("")
        except Exception as e:
            logger.debug(f"刷新心情状态失败: {e}")

    def _show_mood_picker(self) -> None:
        """弹出心情标签选择器（分组显示，点击设置状态）。"""
        pid = self._get_current_person_id()
        if not pid:
            QMessageBox.warning(self, "提示", "请先选择对象")
            return
        # 构建选择对话框
        from PySide6.QtWidgets import QDialog, QGridLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("设置心情状态")
        dlg.setStyleSheet("background: #1f1f22; color: #e5e5e8;")
        dlg.setMinimumWidth(380)
        dl = QVBoxLayout(dlg)

        mood = None
        if hasattr(self._facade, 'services'):
            mood = self._facade.services.get('mood')

        # 按分类分组显示
        categories = [
            ("positive", "积极"),
            ("neutral", "中性"),
            ("negative", "消极"),
            ("relationship", "关系"),
        ]
        for cat_key, cat_name in categories:
            dl.addWidget(QLabel(f"<b style='color:#e5e5e8'>{cat_name}</b>"))
            grid = QGridLayout()
            col = 0
            row = 0
            for tag_key, info in (mood.MOOD_TAGS if mood else {}).items():
                if info.get('category') != cat_key:
                    continue
                btn = QPushButton(f"{info.get('emoji','')} {info.get('name','')}")
                color = info.get('color', '#888')
                btn.setStyleSheet(f"""
                    QPushButton {{ background: rgba(255,255,255,0.05); color: #e5e5e8; border: 1px solid {color};
                    border-radius: 8px; padding: 6px 10px; }}
                    QPushButton:hover {{ background: {color}; color: #1f1f22; }}
                """)
                btn.clicked.connect(lambda checked, t=tag_key: self._set_mood_and_close(dlg, pid, t))
                grid.addWidget(btn, row, col)
                col += 1
                if col >= 4:
                    col = 0
                    row += 1
            dl.addLayout(grid)

        # 清除状态按钮
        clear_btn = QPushButton("清除当前状态")
        clear_btn.setStyleSheet("background: #e74c3c; color: white; padding: 6px;")
        clear_btn.clicked.connect(lambda: (self._clear_mood(pid), dlg.accept()))
        dl.addWidget(clear_btn)

        dlg.exec()

    def _set_mood_and_close(self, dlg, pid: str, tag: str) -> None:
        """设置心情并关闭选择器。"""
        try:
            resp = self._facade.set_mood(pid, tag)
            if resp.get("ok"):
                self._refresh_mood_state()
            else:
                QMessageBox.warning(self, "错误", resp.get("error", "设置失败"))
        except Exception as e:
            QMessageBox.warning(self, "错误", f"设置失败: {e}")
        dlg.accept()

    def _clear_mood(self, pid: str) -> None:
        """清除当前心情状态。"""
        try:
            mood = self._facade.services.get('mood')
            if mood and hasattr(mood, 'db'):
                mood.db.execute(
                    "UPDATE mood_records SET is_active=0 WHERE person_id=:pid AND is_active=1",
                    {"pid": pid}
                )
            self._refresh_mood_state()
        except Exception as e:
            logger.debug(f"清除心情失败: {e}")

    def _reload_person_combo(self) -> None:
        """刷新顶部 + 聊天分析页的对象下拉框。"""
        try:
            resp = self._facade.list_persons()
            if not resp.get("ok"):
                return
            data = resp.get("data") or {}
            persons = data.get("persons") or []
            active_id = data.get("active_person_id")
            self._person_combo.blockSignals(True)
            self._person_combo.clear()
            for p in persons:
                name = p.get("nickname") or p.get("wechat_nickname") or p.get("id")
                self._person_combo.addItem(name, p.get("id"))
            # 选中当前激活
            if active_id:
                idx = self._person_combo.findData(active_id)
                if idx >= 0:
                    self._person_combo.setCurrentIndex(idx)
            self._person_combo.blockSignals(False)
        except Exception:
            logger.debug("刷新对象下拉框失败", exc_info=True)

    def _reload_affection(self) -> None:
        if not self._current_person_id:
            return
        try:
            resp = self._facade.get_affection(self._current_person_id)
            if not resp.get("ok"):
                return
            data = resp.get("data") or {}
            total = float(data.get("total", 50.0))
            self._affection_ring.set_value(total)
            # 雷达图：scores 是 dict，取 18 维（不足补 0）
            scores = data.get("scores") or {}
            labels = list(scores.keys())[:18]
            values = [float(scores.get(k, 0)) for k in labels]
            while len(labels) < 18:
                labels.append("")
                values.append(0.0)
            self._radar.set_data(labels, values)
        except Exception:
            logger.debug("刷新好感度失败", exc_info=True)

    def _reload_hero_wall(self) -> None:
        # 清空旧 item
        while self._hero_grid.count():
            item = self._hero_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        try:
            resp = self._facade.list_persons()
            if not resp.get("ok"):
                return
            persons = (resp.get("data") or {}).get("persons") or []
            for i, p in enumerate(persons):
                card = self._make_hero_card(p)
                r, c = divmod(i, 4)
                self._hero_grid.addWidget(card, r, c)
        except Exception:
            logger.debug("刷新英雄墙失败", exc_info=True)

    def _make_hero_card(self, p: dict) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; }"
            "QFrame:hover { border: 1px solid #6366f1; background: rgba(99,102,241,0.08); }"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 10, 10, 10)
        name = p.get("nickname") or p.get("wechat_nickname") or "?"
        v.addWidget(_CircularAvatar(name[:1], 40))
        v.addWidget(QLabel(f"<b style='color:#e5e5e8'>{name}</b>"))
        v.addWidget(QLabel(f"<span style='color:#b0b0c0'>阶段 {p.get('relationship_stage',0)}</span>"))

        pid = p.get("id")
        # 点击切换对象
        def _on_click(_checked=False, _pid=pid):
            if not _pid:
                return
            r = self._facade.switch_person(_pid)
            if r.get("ok"):
                self._on_person_switched({"person_id": _pid})

        card.mousePressEvent = lambda e, fn=_on_click: fn()
        return card

    def _reload_medal_wall(self) -> None:
        while self._medal_grid.count():
            item = self._medal_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        try:
            pid = self._current_person_id or ""
            resp = self._facade.get_all_medals(pid)
            if not resp.get("ok"):
                return
            medals = resp.get("data") or []
            unlocked = 0
            for i, m in enumerate(medals):
                name = m.get("name") or m.get("medal_id") or "勋章"
                got = bool(m.get("unlocked") or m.get("is_unlocked"))
                if got:
                    unlocked += 1
                cell = QLabel(f"{'★' if got else '☆'} {name}")
                cell.setAlignment(Qt.AlignCenter)
                cell.setStyleSheet(
                    "QLabel { padding: 10px; border-radius: 10px; "
                    "background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); "
                    "color: %s; }" % ("#a5b4fc" if got else "#555560")
                )
                cell.setMinimumSize(110, 60)
                r, c = divmod(i, 4)
                self._medal_grid.addWidget(cell, r, c)
            self._status_medals.setText(f"勋章: {unlocked}/{len(medals)}")
        except Exception:
            logger.debug("刷新勋章墙失败", exc_info=True)

    # ------------------------------------------------------------------ #
    # 事件回调
    # ------------------------------------------------------------------ #
    def _switch_page(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)

    def _on_settings(self) -> None:
        """打开设置面板。"""
        try:
            from app.widgets.settings_dialog import SettingsDialog
            config = self._facade._config if hasattr(self._facade, '_config') else None
            dlg = SettingsDialog(config, self._facade, self)
            dlg.exec()
        except Exception as e:
            logger.error(f"打开设置面板失败: {e}", exc_info=True)
            QMessageBox.warning(self, "设置", f"设置面板打开失败: {e}")

    # ------------------------------------------------------------------ #
    # 18 部分完整诊断报告查看器（原生 QDialog，滚动渲染）
    # ------------------------------------------------------------------ #
    def _on_show_diagnostic_report(self) -> None:
        """前台查看人物 18 部分诊断报告：顶部核心结论+人物底色白描，下方逐部分渲染。
        未校准显示「未校准/待观察」；不做病理审判、不做去留建议。"""
        from PySide6.QtWidgets import QDialog as _QDialog
        pid = self._get_current_person_id()
        name = self._nickname_label.text() if hasattr(self, "_nickname_label") else ""

        dlg = _QDialog(self)
        dlg.setWindowTitle(f"完整诊断报告（18部分）· {name or pid or ''}")
        dlg.resize(760, 720)
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(10, 10, 10, 10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        host = QWidget()
        host.setStyleSheet("background: #1a1a1d;")
        col = QVBoxLayout(host)
        col.setContentsMargins(12, 12, 12, 12)
        col.setSpacing(10)
        scroll.setWidget(host)

        def _label(text, bold=False, color="#e8e8e8"):
            lb = QLabel(text)
            lb.setWordWrap(True)
            lb.setStyleSheet(
                f"color:{color}; font-weight:{'bold' if bold else 'normal'};"
                "background:transparent; border:none;"
            )
            return lb

        resp = self._facade.get_diagnostic_report(pid)
        if not resp.get("ok"):
            col.addWidget(_label(f"暂无法生成诊断报告：{resp.get('error','未知错误')}",
                                 color="#f59e0b"))
            dlg.exec()
            return

        report = resp.get("data") or {}
        ov = report.get("data_overview") or {}
        cal = (report.get("credibility") or {}).get("calibration") or {}

        # —— 顶部：核心结论 ——
        col.addWidget(_label("核心结论", bold=True, color="#e5e5e8"))
        col.addWidget(_label(
            f"消息样本：共 {ov.get('total_messages',0)} 条"
            f"（对方 {ov.get('other_messages',0)} / 我 {ov.get('my_messages',0)}）；"
            f"含冲突事件：{ov.get('has_conflict')}；含承诺事件：{ov.get('has_commitment')}。"
        ))
        cal_txt = cal.get("label") or cal.get("level") or ""
        col.addWidget(_label(
            f"校准状态：{cal_txt if cal_txt else '未校准'}　"
            f"（置信度 {cal.get('score','—') if isinstance(cal, dict) else '—'}；"
            "样本不足的部分以「待观察」为准，不做定论）"
        ))

        # —— 人物底色白描 ——
        col.addWidget(_label("人物底色白描", bold=True, color="#e5e5e8"))
        portrait = (report.get("portrait_plain") or "").strip()
        col.addWidget(_label(portrait if portrait else "（样本尚不足，人物底色白描待观察）"))

        col.addWidget(_label("—— 18 部分明细 ——", bold=True, color="#e5e5e8"))

        # —— 18 部分逐段渲染 ——
        for sec in report.get("sections", []):
            sid = sec.get("id", "?")
            title = sec.get("title", "")
            status = sec.get("status", "ok")
            badge = "未校准/待观察" if status != "ok" else "已校准"
            color = "#9ca3af" if status != "ok" else "#e8e8e8"
            col.addWidget(_label(f"{sid}. {title}　［{badge}］", bold=True, color="#e5e5e8"))
            content = sec.get("content") or {}
            if status != "ok" or not content:
                col.addWidget(_label("　证据不足，待观察。", color=color))
                continue
            for k, v in content.items():
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                col.addWidget(_label(f"　{k}：{v}"))

        col.addWidget(_label(
            "说明：本报告只做事实与倾向诊断，服务于「看懂之后如何对症引导关系变好」，"
            "不做去留建议。",
            color="#9ca3af"))
        dlg.exec()

    def _on_search_person(self) -> None:
        """搜索对象：按输入关键字匹配昵称，回车后切换到匹配的对象。"""
        keyword = self._search_edit.text().strip()
        if not keyword:
            return
        try:
            resp = self._facade.list_persons()
            if not resp.get("ok"):
                return
            persons = (resp.get("data") or {}).get("persons") or []
            kw = keyword.lower()
            for p in persons:
                name = (p.get("nickname") or p.get("wechat_nickname") or "").lower()
                if kw in name:
                    pid = p.get("id")
                    if pid:
                        self._facade.switch_person(pid)
                        self._on_person_switched({"person_id": pid})
                        QMessageBox.information(self, "搜索", f"已切换到：{p.get('nickname') or p.get('wechat_nickname')}")
                    return
            QMessageBox.information(self, "搜索", f"未找到包含「{keyword}」的对象")
        except Exception as e:
            logger.debug("搜索对象失败: %s", e)

    def _on_load_analysis(self) -> None:
        """触发一次聊天分析（通过 facade.analyze_chat 异步执行）。"""
        pid = self._person_combo.currentData() or self._current_person_id
        if not pid:
            QMessageBox.warning(self, "提示", "请先选择对象")
            return
        # 取最近聊天文本
        resp = self._facade.get_chat_messages(pid, limit=50)
        chat_text = ""
        if resp.get("ok"):
            d = resp.get("data") or {}
            msgs = d.get("messages", []) if isinstance(d, dict) else []
            chat_text = "\n".join(
                f"{'我' if m.get('direction') in ('sent', 'out') else '对方'}: {m.get('content','')}"
                for m in msgs
            )
        # 截图分析：勾选时自动截屏→OCR→追加到聊天文本末尾
        if hasattr(self, '_screenshot_check') and self._screenshot_check.isChecked():
            ocr_text = self._ocr_screenshot_recognize()
            if ocr_text:
                chat_text = chat_text + "\n\n[屏幕识别文字]\n" + ocr_text
                logger.info("截图分析：已追加OCR识别文字 %d 字", len(ocr_text))
        if not chat_text.strip():
            QMessageBox.information(self, "提示", "暂无聊天记录可分析")
            return
        r = self._facade.analyze_chat(chat_text, pid)
        if not r.get("ok"):
            QMessageBox.warning(self, "分析失败", r.get("error", "未知错误"))

    def _ocr_screenshot_recognize(self) -> str:
        """截取当前屏幕并OCR识别，返回识别到的文字；失败返回空串不阻塞分析。"""
        try:
            ocr = None
            if hasattr(self._facade, 'services'):
                ocr = self._facade.services.get('ocr')
            if ocr is None:
                logger.warning("截图分析：OCR服务不可用")
                return ""
            result = ocr.capture_and_recognize(region=None)
            text = (result or {}).get("text", "") or ""
            if result.get("error"):
                logger.warning("截图分析：OCR错误: %s", result.get("error"))
            return text.strip()
        except Exception as e:
            logger.warning("截图分析：OCR异常: %s", e)
            return ""

    def _on_fill_wechat(self) -> None:
        """把建议回复一键填入微信输入框（不自动发送）。"""
        pid = self._person_combo.currentData() or self._current_person_id
        if not pid:
            QMessageBox.warning(self, "提示", "请先选择对象")
            return
        # 从回复建议卡片获取建议回复
        reply_lbl = self._analysis_card_labels.get("reply")
        reply_text = ""
        if reply_lbl:
            reply_text = reply_lbl.text()
        # 清理卡片标签前缀
        for prefix in ["建议回复：", "回复：", "（暂无数据）"]:
            reply_text = reply_text.replace(prefix, "")
        reply_text = reply_text.strip()
        if not reply_text:
            QMessageBox.information(self, "提示", "暂无建议回复，请先执行分析")
            return
        # 调用微信服务填入
        try:
            wechat = None
            if hasattr(self._facade, 'services'):
                wechat = self._facade.services.get('wechat')
            if wechat and hasattr(wechat, 'fill_text'):
                ok = wechat.fill_text(reply_text)
                if ok:
                    QMessageBox.information(self, "填入微信", "建议回复已填入微信输入框，请检查后手动发送。")
                else:
                    QMessageBox.warning(self, "填入失败", "填入微信失败，请手动复制。")
            else:
                # 退化为复制到剪贴板
                import pyperclip
                pyperclip.copy(reply_text)
                QMessageBox.information(self, "复制", "微信服务不可用，回复已复制到剪贴板，请手动粘贴。")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"填入失败: {e}")

    def _on_send_consult(self) -> None:
        text = self._consult_input.text().strip()
        if not text:
            return
        mode = self._consult_mode.currentData() or "self"
        # 借鉴 Cherry Studio：用户消息右对齐浅灰气泡（rgba(255,255,255,0.08)），AI 左对齐无气泡
        import html as _html
        _u = _html.escape(text)
        self._consult_history.append(
            f"<div align='right' style='margin:6px 0;'>"
            f"<span style='background-color:rgba(255,255,255,0.10); color:#ffffff; "
            f"padding:8px 14px; border-radius:10px; display:inline-block;'>{_u}</span></div>"
        )
        self._consult_input.clear()
        try:
            resp = self._facade.consult(text, mode)
            if resp.get("ok"):
                data = resp.get("data") or {}
                answer = data.get("answer") or data.get("reply") or str(data)
                _a = _html.escape(answer)
                self._consult_history.append(
                    f"<div style='margin:6px 0;'>"
                    f"<span style='color:#e5e5e8; padding:4px 0;'>{_a}</span></div>"
                )
            else:
                self._consult_history.append(
                    f"<p style='color:#f87171; margin:6px 0;'><b>错误:</b> {_html.escape(resp.get('error',''))}</p>"
                )
        except Exception as e:
            self._consult_history.append(f"<p style='color:#ef5350'>异常: {e}</p>")

    # ---- 全局信号回调 ----
    def _on_person_switched(self, payload: dict) -> None:
        pid = payload.get("person_id") or payload.get("id")
        if pid:
            self._current_person_id = pid
            # 先同步下拉框到新对象，保证 _apply_person 内取到的是切换后的 id
            self._reload_person_combo()
            r = self._facade.get_person(pid)
            if r.get("ok"):
                self._apply_person(r.get("data") or {})
        self._reload_affection()
        self._reload_hero_wall()
        self._reload_medal_wall()

    def _on_affection_changed(self, payload: dict) -> None:
        total = payload.get("total")
        if total is not None:
            try:
                self._affection_ring.set_value(float(total))
            except Exception:
                pass
        self._reload_affection()
        # 好感度变化联动时间线与右侧快捷面板四维
        pid = self._get_current_person_id()
        tl = getattr(self, "_timeline_widget", None)
        if tl is not None and pid:
            try:
                tl.load_from_facade(self._facade, pid)
            except Exception:
                pass
        self._refresh_quick_panel()

    def _on_analysis_ready(self, payload: dict) -> None:
        data = payload.get("data") or {}
        # 8 张卡片填充；data 可能是嵌套 dict
        for key, lbl in self._analysis_card_labels.items():
            val = data.get(key, "")
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False, indent=2)
            lbl.setText(str(val) if val else "（暂无数据）")
        # R3.3-22 单轮分析输出卡（六字段）
        if getattr(self, "_analysis_output_card", None) is not None:
            try:
                self._analysis_output_card.set_result(data)
            except Exception:
                pass
        # 3 个候选话术并排填充
        self._populate_reply_candidates(data)
        # 分析完成后自动刷新后台状态：任务、心情、周期、时间线、快捷面板
        self._refresh_task_list()
        self._refresh_tasks_page()
        self._refresh_mood_state()
        self._refresh_cycle_display()
        pid = self._get_current_person_id()
        if pid and getattr(self, "_timeline_widget", None) is not None:
            try:
                self._timeline_widget.load_from_facade(self._facade, pid)
            except Exception:
                pass
        self._refresh_quick_panel()

    def _populate_reply_candidates(self, data: dict) -> None:
        """从分析结果提取最多 3 条候选话术，并排填入三栏；无数据则清空。"""
        edits = getattr(self, "_reply_candidate_edits", None)
        if not edits:
            return
        cands: list[str] = []
        for key in ("reply_candidates", "replies", "suggested_replies", "reply_options"):
            raw = data.get(key)
            if isinstance(raw, list) and raw:
                for item in raw:
                    if isinstance(item, str):
                        cands.append(item)
                    elif isinstance(item, dict):
                        cands.append(str(item.get("text") or item.get("reply") or item.get("content") or ""))
                break
        # 兜底：没有列表时，把 reply 卡片文本作为候选 1
        if not cands:
            reply = data.get("reply", "")
            if isinstance(reply, str) and reply.strip():
                cands.append(reply)
        cands = [c.strip() for c in cands if c and str(c).strip()]
        for i, box in enumerate(edits):
            box.setPlainText(cands[i] if i < len(cands) else "")

    def _on_task_updated(self, payload: dict) -> None:
        # 任务更新时自动刷新实时任务列表、四列看板与右侧快捷面板
        self._refresh_task_list()
        self._refresh_tasks_page()
        self._refresh_quick_panel()

    def _on_medal_unlocked(self, payload: dict) -> None:
        self._reload_medal_wall()

    # ------------------------------------------------------------------ #
    # 关闭事件：隐藏而非退出
    # ------------------------------------------------------------------ #
    def closeEvent(self, event: QCloseEvent) -> None:
        event.accept()
        self.hide()
        try:
            self._signals.hide_all_windows.emit()
        except Exception:
            pass
