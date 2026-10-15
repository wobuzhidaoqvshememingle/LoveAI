# -*- coding: utf-8 -*-
"""实测Cherry Studio桌面端RPA适配器"""
import sys
sys.path.insert(0, '.')

from app.providers.desktop_detect import detect_installed_apps

print("=== 1. 检测本机安装的AI桌面端软件 ===")
apps = detect_installed_apps()
for app in apps:
    print(f"  {app.name}: {app.exe_path}")

print("\n=== 2. 尝试用uiautomation找Cherry Studio窗口 ===")
try:
    import uiautomation as auto
    root = auto.WindowControl(searchDepth=1, ClassName="Chrome_WidgetWin_1")
    if root.Exists(3):
        name = root.Name or ""
        print(f"  找到窗口: {name}")
        if "cherry" in name.lower():
            print("  ✅ 是Cherry Studio窗口")
        else:
            print(f"  ⚠️ 不是Cherry Studio，是: {name}")
    else:
        print("  ❌ 没找到Chrome_WidgetWin_1窗口")
except Exception as e:
    print(f"  ❌ 错误: {e}")

print("\n=== 3. 列出所有打开的窗口 ===")
try:
    import uiautomation as auto
    root = auto.GetRootControl()
    windows = []
    for win in root.GetChildren():
        try:
            if win.ClassName == "Chrome_WidgetWin_1" and win.Name:
                windows.append(win.Name)
        except:
            pass
    print(f"  找到{len(windows)}个Chrome窗口:")
    for w in windows[:10]:
        print(f"    - {w}")
except Exception as e:
    print(f"  ❌ 错误: {e}")
