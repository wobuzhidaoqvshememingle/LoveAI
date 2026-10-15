"""
LoveAI v2.0 - Electron 桌宠端到端联调测试
==========================================
不启动完整 PySide6 UI（offscreen 模式），只跑：
  1. pet_ws_server 起 WS 服务端（127.0.0.1:9876）
  2. Python 验证客户端连上去，发信号，看能不能收到 thinking/speaking/emotion
  3. 拉起真 Electron 桌宠（LoveAIPet.exe 或 npm start），看它能不能自动连上
  4. 再发一次 speaking，确认广播没炸

用法：
    .venv\\Scripts\\python.exe scripts\\e2e_pet_test.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import threading

# 离屏 Qt，不弹主窗口
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _run_collect_client(received: list, ready_evt: threading.Event, stop_evt: threading.Event):
    """在独立线程里跑一个 WS 客户端：连服务端，收消息，塞进 received 列表。"""
    import websockets

    async def _run():
        try:
            async with websockets.connect("ws://127.0.0.1:9876") as ws:
                ready_evt.set()  # 已连接
                # 持续收，直到 stop_evt 置位
                while not stop_evt.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            msg = {"_raw": raw}
                        received.append(msg)
                    except asyncio.TimeoutError:
                        continue
        except Exception as e:
            received.append({"event": "_client_error", "error": str(e)})

    asyncio.run(_run())


def main() -> int:
    results: list[tuple[str, bool]] = []
    print("=" * 60)
    print("LoveAI <-> Electron 桌宠 E2E 联调测试")
    print("=" * 60)

    # ---------- 1. 启动 WS 服务端 ----------
    print("\n[1/5] 启动 pet_ws_server (127.0.0.1:9876) ...")
    from app import pet_ws_server
    pet_ws_server.start_server(host="127.0.0.1", port=9876)
    time.sleep(0.8)
    ok = pet_ws_server._state["started"] and pet_ws_server._state["loop"] is not None
    results.append(("WS 服务端启动", ok))
    print(f"      started={pet_ws_server._state['started']}, "
          f"clients={len(pet_ws_server._state['clients'])}")

    # ---------- 2. Python 验证客户端连上去 ----------
    print("\n[2/5] 起 Python 验证客户端，连 ws://127.0.0.1:9876 ...")
    received: list = []
    ready_evt = threading.Event()
    stop_evt = threading.Event()
    t = threading.Thread(
        target=_run_collect_client,
        args=(received, ready_evt, stop_evt),
        daemon=True,
    )
    t.start()
    connected = ready_evt.wait(timeout=3.0)
    results.append(("验证客户端连上 WS", connected))
    print(f"      连接={'OK' if connected else 'FAIL'}")

    # ---------- 3. 发信号，看服务端有没有把事件推过来 ----------
    print("\n[3/5] 发射 Qt 信号 (analysis_progress / analysis_ready / affection_changed / new_message) ...")
    from app.signals import get_signals
    sig = get_signals()

    sig.analysis_progress.emit({"stage": "分析聊天记录", "percent": 50})
    time.sleep(0.3)
    sig.analysis_ready.emit({"module": "persona", "data": {"relationship_stage": "暧昧期"}})
    time.sleep(0.3)
    sig.affection_changed.emit({"total": 75})
    time.sleep(0.3)
    sig.new_message.emit({"person_id": "test", "content": "你好"})
    time.sleep(1.0)  # 等广播送达

    events = [m.get("event") for m in received if isinstance(m, dict)]
    print(f"      验证客户端共收到 {len(received)} 条消息:")
    for m in received:
        print(f"        -> {json.dumps(m, ensure_ascii=False)[:180]}")

    results.append(("收到 thinking 事件", "thinking" in events))
    results.append(("收到 speaking 事件", "speaking" in events))
    results.append(("收到 emotion 事件", "emotion" in events))
    results.append(("收到 hello 事件（握手）", "hello" in events))

    # 关掉验证客户端，腾出 clients 集合，下一步看真桌宠连上来
    stop_evt.set()
    t.join(timeout=3)
    time.sleep(1.0)
    clients_before = len(pet_ws_server._state["clients"])
    print(f"\n      验证客户端已断开，当前 clients={clients_before}")

    # ---------- 4. 拉起真 Electron 桌宠 ----------
    print("\n[4/5] 拉起 Electron 桌宠进程 ...")
    from app import pet_launcher
    launched = pet_launcher.launch_pet()
    print(f"      launch_pet() 返回={launched}（False=已在跑/找不到）")

    pet_connected = False
    for i in range(25):  # 最多等 25 秒（Electron 冷启动慢）
        time.sleep(1)
        n = len(pet_ws_server._state["clients"])
        if n > clients_before:
            pet_connected = True
            print(f"      ✅ 桌宠已连上 WS（等待 {i+1}s），当前 clients={n}")
            break
        if (i + 1) % 5 == 0:
            print(f"      ... 等待桌宠连接中 ({i+1}s)，clients={n}")
    results.append(("Electron 桌宠自动连上 WS", pet_connected))

    # ---------- 5. 桌宠连上后再推一次 speaking ----------
    print("\n[5/5] 桌宠连上后再推一次 speaking 事件 ...")
    sig.analysis_ready.emit({"module": "联调测试", "data": {"relationship_stage": "暧昧期"}})
    time.sleep(1.5)
    # 服务端广播不报错就 OK（看不到桌宠 UI，但 clients 集合非空说明推送目标存在）
    n_clients = len(pet_ws_server._state["clients"])
    results.append(("广播 speaking 时桌宠仍在线", n_clients > 0))
    print(f"      推送时 clients={n_clients}")

    # ---------- 清理（只停 WS 服务端，桌宠留着给用户看） ----------
    print("\n[cleanup] 停止 WS 服务端（桌宠进程保留，从托盘退出）...")
    pet_ws_server.stop_server()

    # ---------- 汇总 ----------
    print("\n" + "=" * 60)
    print("E2E 测试结果汇总")
    print("=" * 60)
    all_ok = True
    for name, ok in results:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  [{mark}] {name}")
    print("=" * 60)
    print("最终结论:", "全部通过 ✅" if all_ok else "有失败项 ❌")
    print("\n桌宠窗口应该已经出现在屏幕右下角（若已在跑则复用了已有实例）。")
    print("退出桌宠：右键托盘图标 -> 退出。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
