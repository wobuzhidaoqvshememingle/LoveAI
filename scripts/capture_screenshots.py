# -*- coding: utf-8 -*-
"""
离屏截图 + 挂载自检脚本

构建「真实」的 MainWindow + ServiceFacade（仅注入 config/db 两个核心服务，
其余服务缺失时代码本就有空态兜底），用 offscreen 平台逐页渲染并截图，
同时断言 5 个新组件确实拿到了真实数据。

用法（项目根目录，用 venv）：
    .venv\\Scripts\\python.exe scripts\\capture_screenshots.py
"""
from __future__ import annotations

import os
import sys

# 默认使用真实屏幕平台以正确渲染中文字体；如需无界面可设 QT_QPA_PLATFORM=offscreen
if "--offscreen" in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from services.config_service import ConfigService
from models.database import get_database
from app.signals import get_signals
from app.service_facade import ServiceFacade
from app.widgets.main_window import (
    MainWindow, PAGE_CHAT_ANALYSIS, PAGE_PROFILE, PAGE_TASKS, PAGE_TWIN,
)

SHOT_DIR = os.path.join(ROOT, "docs", "screenshots")


def main() -> int:
    os.makedirs(SHOT_DIR, exist_ok=True)
    app = QApplication(sys.argv)

    cfg = ConfigService(os.path.join(ROOT, "config.json"))
    db_rel = cfg.get("data.db_path", "data/loveai.db")
    db_path = db_rel if os.path.isabs(db_rel) else os.path.join(ROOT, db_rel.replace("/", os.sep))
    db = get_database(db_path)
    db.init_tables()

    services = {"config": cfg, "db": db}
    facade = ServiceFacade(services, get_signals())

    win = MainWindow(facade)
    win.resize(1480, 980)
    win.show()
    QTest.qWait(1400)  # 等好感度环动画 / singleShot 刷新完成

    pid = win._get_current_person_id()
    print(f"[shot] 当前对象 pid={pid}")
    failures: list[str] = []

    def grab_window(name: str):
        path = os.path.join(SHOT_DIR, name)
        ok = win.grab().save(path, "PNG")
        print(f"[shot] {'OK ' if ok else 'FAIL'} {name}")
        if not ok:
            failures.append(name)

    def grab_widget(widget, name: str):
        path = os.path.join(SHOT_DIR, name)
        ok = widget.grab().save(path, "PNG")
        print(f"[shot] {'OK ' if ok else 'FAIL'} {name} ({widget.width()}x{widget.height()})")
        if not ok:
            failures.append(name)

    def goto(page_idx: int):
        win._switch_page(page_idx)
        if page_idx < len(win._nav_buttons):
            win._nav_buttons[page_idx].setChecked(True)
        QTest.qWait(700)

    # ---- 1. 主界面（含右侧快捷面板）----
    goto(PAGE_CHAT_ANALYSIS)
    grab_window("main-dashboard.png")

    # ---- 2. 人物档案（含时间线）----
    # 让资料优化卡先生成一条真实建议（模拟选择「偏好真诚」）
    try:
        win._profile_optimize_card._pref_combo.setCurrentIndex(1)
        win._profile_optimize_card.load_from_facade(facade, pid, {"喜欢": "真诚"})
        QTest.qWait(300)
    except Exception as e:
        print("[shot] 资料优化卡生成失败:", e)
    goto(PAGE_PROFILE)
    grab_window("character-file.png")
    grab_widget(win._timeline_widget, "timeline.png")
    grab_widget(win._profile_optimize_card, "profile-optimize-card.png")

    # ---- 3. 任务看板 ----
    goto(PAGE_TASKS)
    grab_window("tasks-medals.png")

    # ---- 4. 数字分身面板 ----
    goto(PAGE_TWIN)
    grab_window("digital-twin.png")

    # ---- 数据断言：确认组件拿到真实数据，而非空态 ----
    if win._timeline_widget.is_empty():
        failures.append("timeline-empty")
        print("[assert] FAIL 时间线无快照")
    else:
        print(f"[assert] OK 时间线点数={len(win._timeline_widget._points)}")

    board = facade.get_task_board(pid).get("data") or {}
    cols = (board.get("columns") or {})
    print("[assert] 看板分栏计数:", {k: len(v) for k, v in cols.items()})
    if board.get("total", 0) < 4:
        failures.append("kanban-too-few")

    qp = facade.get_quick_panel(pid).get("data") or {}
    print("[assert] 快捷面板最近任务数=", len(qp.get("recent_tasks") or []),
          " 四维=", qp.get("mini_axes"))
    if len(qp.get("recent_tasks") or []) == 0:
        failures.append("quickpanel-no-tasks")

    twin = facade.get_twin_panel(pid, "partner").get("data") or {}
    print("[assert] 分身当前状态=", twin.get("current"),
          " 可切换模式=", twin.get("available_modes"))

    opt = facade.optimize_profile(pid, {"喜欢": "真诚"}).get("data") or {}
    print("[assert] 资料优化建议=", opt.get("suggestions"))
    if not opt.get("suggestions"):
        failures.append("profile-opt-no-tips")

    QTest.qWait(200)
    print("[shot] 输出目录:", SHOT_DIR)
    if failures:
        print("[shot] 存在失败项:", failures)
        return 1
    print("[shot] 全部截图与数据断言通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
