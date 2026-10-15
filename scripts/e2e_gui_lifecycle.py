# -*- coding: utf-8 -*-
"""
R3.3-76 项2：桌宠生命周期 真 GUI（非 offscreen）端到端验证
=========================================================
真实 QApplication 事件循环：
  启动接线(=application.py) -> 桌宠随动 -> tasklist/WS 实证 ->
  quit() -> LoveAIPet.exe 整树终止 -> tasklist 实证。
带超时保护与强制清理，绝不残留桌宠进程。
"""
from __future__ import annotations
import os, sys, time, subprocess, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
EVID = os.path.join(ROOT, "logs", "pet_gui_lifecycle_evidence.txt")
_lines = []

def log(m):
    s = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {m}"
    print(s, flush=True); _lines.append(s)

def pet_pids():
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq LoveAIPet.exe", "/NH", "/FO", "CSV"],
            stderr=subprocess.DEVNULL, timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).decode("gbk", "ignore")
        pids = [int(l.split(",")[1].strip().strip('"')) for l in out.splitlines()
                if "LoveAIPet.exe" in l and len(l.split(",")) >= 2]
        return pids
    except Exception:
        return []

def force_clean():
    for pid in pet_pids():
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
        except Exception:
            pass

def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    app = QApplication(sys.argv)
    log(f"真实 QApplication 启动（platform={app.platformName()})")

    from app import pet_ws_server, pet_launcher, pet_config
    results = []
    state = {}

    def step1_startup():
        log("[step1] 启动接线：WS server -> 写共享配置 -> 拉起桌宠")
        pet_ws_server.start_server("127.0.0.1", 9876)
        sp = pet_config.write_shared_config()
        st = pet_launcher.launch_pet(shared_config_path=sp)
        state["status"] = st
        state["pid"] = pet_launcher._state.get("pid")
        log(f"    status={st} pid={state['pid']}")
        QTimer.singleShot(6000, step2_verify)

    def step2_verify():
        pids = pet_pids()
        n = len(pet_ws_server._state["clients"])
        log(f"[step2] 实证：LoveAIPet.exe PIDs={pids} WS clients={n}")
        results.append(("GUI启动后桌宠进程在跑", bool(pids)))
        results.append(("GUI启动后桌宠连上WS", n > 0))
        QTimer.singleShot(1500, step3_quit)

    def step3_quit():
        log("[step3] 模拟主程序 quit()：先停 WS server 再杀桌宠")
        pet_ws_server.stop_server()
        pet_launcher.stop_launched_pet()
        QTimer.singleShot(2500, step4_verify_gone)

    def step4_verify_gone():
        pids = pet_pids()
        log(f"[step4] quit()后 LoveAIPet.exe PIDs={pids}")
        results.append(("quit()后桌宠整树退出(无孤儿)", not pids))
        app.quit()

    # 全局兜底：35 秒强制清理退出
    def bailout():
        log("[bailout] 超时兜底：强制清理桌宠并退出")
        force_clean()
        results.append(("超时兜底被触发(异常路径)", False))
        app.quit()

    QTimer.singleShot(800, step1_startup)
    QTimer.singleShot(35000, bailout)
    app.exec()

    # 退出后再确认一次
    time.sleep(1)
    pids = pet_pids()
    log(f"[final] 事件循环结束后 LoveAIPet.exe PIDs={pids}")
    if pids:
        force_clean()
        log("[final] 检测到残留，已强制清理")

    log("=" * 50)
    ok = True
    for n, r in results:
        log(f"  [{'PASS' if r else 'FAIL'}] {n}"); ok = ok and r
    log("最终: " + ("全部通过" if ok else "有失败"))
    with open(EVID, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines))
    return 0 if ok else 1

if __name__ == "__main__":
    force_clean()
    time.sleep(1)
    sys.exit(main())
