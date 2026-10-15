# -*- coding: utf-8 -*-
"""沉浸桌面新版式离屏截图探针（2026-10-12 参考图整改验证）。
offscreen 实例化 ImmersiveMode，喂字幕/状态，grab 成 PNG 供目检。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv[:1])
# offscreen 平台字体表常缺 CJK（中文渲染成方框）：显式加载系统字体文件再设默认族
for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
           r"C:\Windows\Fonts\simsun.ttc"):
    if os.path.exists(fp) and QFontDatabase.addApplicationFont(fp) != -1:
        break
fam = QFontDatabase.applicationFontFamilies(0) if QFontDatabase.applicationFontFamilies(0) else []
app.setFont(QFont(fam[0] if fam else "Microsoft YaHei", 10))

from app.widgets.immersive_mode import ImmersiveMode  # noqa: E402

win = ImmersiveMode(facade=None)
win.resize(1280, 720)
win.set_mode("idle")
win.show_subtitle("我会吃醋的哦，你今天都没来找我说话")
win.set_stage_label("赛博伴侣 · 地球Online\n小妖女 · 暧昧期 · 点光球说话")
app.processEvents()
out = os.path.join(ROOT, "logs", "probe_immersive_v2.png")
win.grab().save(out)
print("saved", out)
