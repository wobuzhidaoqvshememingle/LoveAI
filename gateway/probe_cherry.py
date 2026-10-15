# -*- coding: utf-8 -*-
"""探测Cherry Studio窗口结构"""
import uiautomation as auto

# 找Cherry Studio窗口
root = auto.WindowControl(searchDepth=1, ClassName="Chrome_WidgetWin_1", Name="Cherry Studio")
if not root.Exists(3):
    print("❌ 没找到Cherry Studio窗口")
    exit()

print("✅ 找到Cherry Studio窗口")
root.SetActive()
root.SetFocus()

# 列出所有Edit控件（输入框）
print("\n=== Edit控件（输入框） ===")
edits = []
def find_edit(ctrl, depth=0):
    if depth > 15:
        return
    try:
        if ctrl.ControlTypeName == "EditControl":
            edits.append((depth, ctrl.Name, ctrl.ClassName))
    except:
        pass
    for child in ctrl.GetChildren():
        find_edit(child, depth+1)

find_edit(root)
for i, (d, name, cls) in enumerate(edits):
    print(f"  [{i}] depth={d} name='{name[:30]}' class='{cls}'")

# 列出所有Button控件（按钮）
print("\n=== Button控件（按钮） ===")
buttons = []
def find_button(ctrl, depth=0):
    if depth > 15:
        return
    try:
        if ctrl.ControlTypeName == "ButtonControl":
            buttons.append((depth, ctrl.Name, ctrl.ClassName))
    except:
        pass
    for child in ctrl.GetChildren():
        find_button(child, depth+1)

find_button(root)
for i, (d, name, cls) in enumerate(buttons[:20]):
    print(f"  [{i}] depth={d} name='{name[:30]}' class='{cls}'")

# 列出所有Text控件（文本块）
print("\n=== Text控件（文本块，前10个） ===")
texts = []
def find_text(ctrl, depth=0):
    if depth > 15:
        return
    try:
        if ctrl.ControlTypeName == "TextControl" and ctrl.Name:
            texts.append((depth, ctrl.Name[:50]))
    except:
        pass
    for child in ctrl.GetChildren():
        find_text(child, depth+1)

find_text(root)
for i, (d, name) in enumerate(texts[:10]):
    print(f"  [{i}] depth={d} text='{name}'")
