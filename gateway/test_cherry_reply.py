# -*- coding: utf-8 -*-
"""改进版：用时间戳对比读取AI回复"""
import time
import pyperclip
import uiautomation as auto

# 1. 激活窗口
root = auto.WindowControl(searchDepth=1, ClassName="Chrome_WidgetWin_1", Name="Cherry Studio")
root.SetActive()
root.SetFocus()
time.sleep(1)

rect = root.BoundingRectangle
click_x = (rect.left + rect.right) // 2

# 2. 发送前先读一次整个页面内容
auto.Click(click_x, int(rect.bottom * 0.5))  # 点中间（聊天区域）
time.sleep(0.3)
auto.SendKeys("^a")
time.sleep(0.3)
auto.SendKeys("^c")
time.sleep(0.5)
before_text = pyperclip.paste()
print(f"发送前内容长度: {len(before_text)}")

# 3. 输入并发送新消息
auto.Click(click_x, int(rect.bottom * 0.8))  # 点下方（输入框）
time.sleep(0.3)
test_msg = "1+1等于几？请只回答数字"
pyperclip.copy(test_msg)
auto.SendKeys("^v")
time.sleep(0.5)
auto.SendKeys("^{Enter}")
print(f"已发送: {test_msg}")

# 4. 等AI回复（最多30秒）
print("等待AI回复...")
for i in range(15):
    time.sleep(2)
    auto.Click(click_x, int(rect.bottom * 0.5))
    time.sleep(0.2)
    auto.SendKeys("^a")
    time.sleep(0.2)
    auto.SendKeys("^c")
    time.sleep(0.3)
    after_text = pyperclip.paste()
    
    # 计算新增内容
    if len(after_text) > len(before_text) + 10:
        new_content = after_text[len(before_text):]
        print(f"\n✅ 第{i+1}次读到新增内容:")
        print(f"新增长度: {len(new_content)}")
        print(f"新增内容: {new_content[:200]}")
        break
    else:
        print(f"  第{i+1}次: 还没变化（{len(after_text)} vs {len(before_text)}）")
else:
    print("❌ 30秒内没读到回复")

print("\n✅ 测试完成")
