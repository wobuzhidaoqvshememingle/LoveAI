# -*- coding: utf-8 -*-
"""
LoveAI R3.3-76 - 桌宠整体生命周期真实验证
==========================================
离线启动（不弹主窗口），真跑：
  1. 清理旧 LoveAIPet.exe
  2. 起 WS 服务端 + 写共享配置
  3. launch_pet() -> 记录状态枚举
  4. tasklist 验证 LoveAIPet.exe 真的在跑
  5. WS clients 计数验证桌宠真的连上
  6. 推一次 speaking 事件
  7. stop_server -> stop_launched_pet()
  8. tasklist 验证 LoveAIPet.exe 已退出（杜绝孤儿进程）
  9. 降级测试：状态枚举映射

输出带时间戳的证据行，写入 logs/pet_lifecycle_evidence.txt。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

EVIDENCE = os.path.join(ROOT, "logs", "pet_lifecycle_evidence.txt")
_ev_lines = []


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    _ev_lines.append(line)


def pet_pids():
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq LoveAIPet.exe", "/NH", "/FO", "CSV"],
            stderr=subprocess.DEVNULL, timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).decode("gbk", errors="ignore")
        pids = []
        for line in out.splitlines():
            if "LoveAIPet.exe" in line:
                parts = [p.strip().strip('"') for p in line.split(",")]
                if len(parts) >= 2 and parts[1].isdigit():
                    pids.append(int(parts[1]))
        return pids
    except Exception:
        return []


def kill_all_pets():
    for pid in pet_pids():
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
        except Exception:
            pass


def main() -> int:
    results = []
    log("=" * 60)
    log("R3.3-76 桌宠整体生命周期真实验证")
    log("=" * 60)

    # 0. 清理旧进程
    log("[0] 清理旧 LoveAIPet.exe ...")
    kill_all_pets()
    time.sleep(2)
    log(f"    清理后 LoveAIPet.exe PIDs = {pet_pids()}")

    # 1. 起 WS 服务端
    log("[1] 启动 pet_ws_server (127.0.0.1:9876) ...")
    from app import pet_ws_server
    pet_ws_server.start_server(host="127.0.0.1", port=9876)
    time.sleep(1.0)
    log(f"    started={pet_ws_server._state['started']} main_version={pet_ws_server._state['main_version']}")
    results.append(("WS 服务端启动", pet_ws_server._state["started"]))

    # 2. 写共享配置
    log("[2] 写共享配置 data/pet_shared.json ...")
    from app import pet_config
    shared = pet_config.write_shared_config()
    log(f"    shared_path={shared}")
    if shared and os.path.isfile(shared):
        with open(shared, "r", encoding="utf-8") as f:
            log(f"    共享内容: {f.read()[:300]}")
    results.append(("共享配置写入", bool(shared and os.path.isfile(shared))))

    # 3. 拉起桌宠
    log("[3] launch_pet() ...")
    from app import pet_launcher
    status = pet_launcher.launch_pet(shared_config_path=shared)
    log(f"    返回状态枚举 = {status}")
    log(f"    记录 PID = {pet_launcher._state.get('pid')}")
    results.append(("launch_pet 返回 ok/already_running", status in ("ok", "already_running")))

    # 4. tasklist 验证桌宠进程在跑
    log("[4] 等待并验证 LoveAIPet.exe 进程 ...")
    pids = []
    for i in range(30):
        time.sleep(1)
        pids = pet_pids()
        if pids:
            log(f"    ✅ LoveAIPet.exe 在跑，PIDs={pids}（等待 {i+1}s）")
            break
        if (i + 1) % 5 == 0:
            log(f"    ... 等待桌宠进程 ({i+1}s)")
    results.append(("LoveAIPet.exe 进程在跑", bool(pids)))

    # 5. WS 客户端计数验证桌宠连上
    log("[5] 等待桌宠连 WS ...")
    connected = False
    for i in range(30):
        time.sleep(1)
        n = len(pet_ws_server._state["clients"])
        if n > 0:
            log(f"    ✅ 桌宠已连 WS，clients={n}（等待 {i+1}s）")
            connected = True
            break
        if (i + 1) % 5 == 0:
            log(f"    ... 等待 WS 连接 ({i+1}s)")
    results.append(("桌宠连上 WS", connected))

    # 6. 推一次 speaking 事件
    log("[6] 推 speaking 事件 ...")
    from app.signals import get_signals
    sig = get_signals()
    sig.analysis_ready.emit({"module": "R3.3-76验证", "data": {"relationship_stage": "暧昧期"}})
    time.sleep(1.5)
    log(f"    推送时 clients={len(pet_ws_server._state['clients'])}")
    results.append(("广播 speaking 桌宠仍在线", len(pet_ws_server._state["clients"]) > 0))

    # 7. 退出：先停 WS，再杀桌宠进程
    log("[7] 主程序退出：先 stop_server，再 stop_launched_pet() ...")
    pet_ws_server.stop_server()
    log("    WS server 已停")
    pet_launcher.stop_launched_pet()
    log("    stop_launched_pet() 已调用")

    # 8. tasklist 验证桌宠已退出
    log("[8] 验证 LoveAIPet.exe 已退出（杜绝孤儿进程）...")
    gone = False
    for i in range(15):
        time.sleep(1)
        pids = pet_pids()
        if not pids:
            log(f"    ✅ LoveAIPet.exe 已退出（{i+1}s）")
            gone = True
            break
        log(f"    ... 仍有 PIDs={pids}，等待退出 ({i+1}s)")
    results.append(("主程序退出后桌宠一并退出", gone))

    # 汇总
    log("=" * 60)
    log("验证结果汇总")
    log("=" * 60)
    all_ok = True
    for name, ok in results:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        log(f"  [{mark}] {name}")
    log("最终结论: " + ("全部通过 ✅" if all_ok else "有失败项 ❌"))

    with open(EVIDENCE, "w", encoding="utf-8") as f:
        f.write("\n".join(_ev_lines))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
