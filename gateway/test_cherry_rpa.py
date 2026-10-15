# -*- coding: utf-8 -*-
"""用键盘操作+剪贴板测试Cherry Studio"""
import time
import uiautomation as auto

# 1. 激活Cherry Studio窗口
root = auto.WindowControl(searchDepth=1, ClassName="Chrome_WidgetWin_1", Name="Cherry Studio")
if not root.Exists(3):
    print("❌ 没找到Cherry Studio窗口")
    exit()

print("✅ 找到Cherry Studio窗口")
root.SetActive()
root.SetFocus()
time.sleep(1)

# 2. 测试：点击窗口中间（大概是输入框位置）
rect = root.BoundingRectangle
print(f"窗口位置: left={rect.left}, top={rect.top}, right={rect.right}, bottom={rect.bottom}")
# 输入框大概在窗口下方1/3处
click_x = (rect.left + rect.right) // 2
click_y = int(rect.bottom * 0.8)
print(f"点击位置: ({click_x}, {click_y})")
auto.Click(click_x, click_y)
time.sleep(0.5)

# 3. 输入测试文本（用剪贴板）
import pyperclip
test_text = "你好，这是一个测试消息"
pyperclip.copy(test_text)
time.sleep(0.2)
auto.SendKeys("^v")  # Ctrl+V粘贴
time.sleep(0.5)

print(f"已输入: {test_text}")

# 4. 发送（Ctrl+Enter）
auto.SendKeys("^{Enter}")
print("已发送（Ctrl+Enter）")

# 5. 等回复（10秒）
print("等待回复...")
time.sleep(10)

# 6. 读剪贴板（全选回复区域再复制）
# 先点击回复区域中间
reply_y = int(rect.bottom * 0.5)
auto.Click(click_x, reply_y)
time.sleep(0.3)
auto.SendKeys("^a")  # Ctrl+A全选
time.sleep(0.3)
auto.SendKeys("^c")  # Ctrl+C复制
time.sleep(0.5)

# 读剪贴板
reply = pyperclip.paste()
print(f"\n读到的内容: {reply[:200]}")

print("\n✅ 测试完成")
