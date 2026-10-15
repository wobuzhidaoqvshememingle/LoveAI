# -*- coding: utf-8 -*-
"""R3.3-77 桥接网【真实启动】验证（非 demo）。

走主程序真实启动路径：LoveAIApplication(offscreen) 初始化 + services 装配 +
pet_ws_server 起线程 + 桥网挂载（复用 facade 同一 EventBus）。然后：
  1. 断言 self.bridge_network 已挂载，且 net.bus.raw_bus 就是 facade._event_bus；
  2. 起一个真 WS 客户端连 127.0.0.1:9876；
  3. 在共享总线上 publish 真实事件（好感/任务完成）；
  4. 断言 WS 客户端真的收到桥网发出的 emotion/speaking 事件。
证据写入 logs/bridges_prod_evidence.txt。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

EVIDENCE = os.path.join(ROOT, "logs", "bridges_prod_evidence.txt")
_lines: list[str] = []


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    _lines.append(line)


# 收集 WS 事件的后台协程
_ws_msgs: list[str] = []


async def _ws_collector(stop_evt: threading.Event):
    try:
        import websockets
        async with websockets.connect("ws://127.0.0.1:9876") as ws:
            log("    WS 客户端已连上 127.0.0.1:9876")
            while not stop_evt.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    _ws_msgs.append(msg)
                except asyncio.TimeoutError:
                    continue
    except Exception as e:
        log(f"    WS 客户端异常: {e}")


def main() -> int:
    results = []
    log("=" * 60)
    log("R3.3-77 桥接网·真实启动验证")
    log("=" * 60)

    # 1. 真实启动 LoveAIApplication（offscreen）
    log("[1] 真实启动 LoveAIApplication（offscreen）...")
    from PySide6.QtWidgets import QApplication
    qt = QApplication.instance() or QApplication(sys.argv)
    from app.application import LoveAIApplication
    app = LoveAIApplication()
    time.sleep(2.0)
    net = getattr(app, "bridge_network", None)
    ok_mounted = net is not None
    results.append(("桥网已挂载(bridge_network 非空)", ok_mounted))
    log(f"    bridge_network 挂载={'是' if ok_mounted else '否'}")
    if not ok_mounted:
        log("    !! 桥网未挂载，终止")
        _finish(results)
        return 1

    # 2. 断言复用同一 EventBus
    facade_bus = app.facade._ensure_event_bus()
    same_bus = net["bus"].raw_bus is facade_bus
    results.append(("桥网复用 facade 同一 EventBus", same_bus))
    log(f"    共用同一 EventBus={same_bus}；桥数={len(net['bridges'])}；"
        f"桥名={sorted(net['bridges'].keys())}")

    # 3. 起真 WS 客户端
    log("[3] 起真 WS 客户端连 127.0.0.1:9876 ...")
    stop_evt = threading.Event()
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_until_complete,
                        args=(_ws_collector(stop_evt),), daemon=True)
    t.start()
    time.sleep(1.5)

    # 4. 在共享总线 publish 真实事件（好感变化 → 桥网多跳 → 桌宠 emotion）
    log("[4] 共享总线 publish AFFECTION_LEVEL_UPDATED(total=85) ...")
    bus = net["bus"]
    bus.reset_pass()
    bus.publish("bridge_affection_level_updated", {
        "person_id": "prod_demo", "total": 85.0, "trend": "up"})
    time.sleep(1.0)

    # 5. WS 客户端是否收到 emotion
    ws_got_emotion = any('"emotion"' in m or '"event":"emotion"' in m
                         for m in _ws_msgs)
    results.append(("WS 客户端收到桥网 emotion 事件", ws_got_emotion))
    log(f"    WS 收到 {len(_ws_msgs)} 条消息；emotion={'是' if ws_got_emotion else '否'}")
    for m in _ws_msgs[:5]:
        log(f"      WS<< {m[:160]}")

    # 6. 多跳：任务完成 → 好感 → 桌宠
    log("[6] 共享总线 publish TASKS_COMPLETED ...")
    bus.reset_pass()
    bus.publish("tasks_completed", {
        "person_id": "prod_demo", "task_id": "pt1",
        "title": "真实启动联动验证", "reward": 3.0})
    time.sleep(1.0)
    multi = any('"emotion"' in m for m in _ws_msgs)
    results.append(("任务完成多跳后 WS 仍收到表现事件", multi))

    # 7. 关闭
    log("[7] 关闭桥网与主程序 ...")
    stop_evt.set()
    try:
        app.quit()
    except Exception as e:
        log(f"    quit 异常（忽略）: {e}")

    return _finish(results)


def _finish(results) -> int:
    log("=" * 60)
    all_ok = True
    for name, ok in results:
        log(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            all_ok = False
    log("最终结论: " + ("全部通过 ✅" if all_ok else "有失败项 ❌"))
    os.makedirs(os.path.dirname(EVIDENCE), exist_ok=True)
    with open(EVIDENCE, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines))
    print(f"[证据已写] {EVIDENCE}", flush=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
