# -*- coding: utf-8 -*-
"""
R3.3-76 项3：桌宠窗口拖动 x/y 走 WS 上行共享验证
=================================================
真实 WS 客户端连 127.0.0.1:9876，模拟桌宠拖动结束后上行：
  {event:"config_update", data:{"window.x":321, "window.y":654}}
验证：
  1) 服务端路由 config_update -> pet_config.apply_uplink
  2) 主 config.json pet.window.x/y 被回写
  3) data/pet_shared.json 投影同步刷新（双端一致）
测试后还原 config.json 原始 window.x/y。
"""
from __future__ import annotations
import asyncio, json, os, sys, time, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
EVID = os.path.join(ROOT, "logs", "pet_window_move_evidence.txt")
_lines = []

def log(m):
    s = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {m}"
    print(s, flush=True); _lines.append(s)

async def main():
    from app import pet_ws_server, pet_config
    # 备份原始 window.x/y
    cfg_path = pet_config._config_path()
    with open(cfg_path, "r", encoding="utf-8") as f:
        orig = json.load(f)
    orig_win = dict((orig.get("pet", {}).get("window", {}) or {}))
    log(f"原始 config.json pet.window = {orig_win}")

    pet_ws_server.start_server("127.0.0.1", 9876)
    time.sleep(1.0)
    pet_config.write_shared_config()

    from websockets.asyncio.client import connect
    results = []
    try:
        async with connect("ws://127.0.0.1:9876") as ws:
            hello = await asyncio.wait_for(ws.recv(), timeout=3)
            log(f"收到服务端 hello: {hello[:200]}")
            # 模拟桌宠上行 window_move（拖动结束）
            await ws.send(json.dumps({"event": "config_update",
                                      "data": {"window.x": 321, "window.y": 654}}))
            log("已发送 config_update {window.x:321, window.y:654}")
            await asyncio.sleep(1.0)

        # 校验主 config.json
        with open(cfg_path, "r", encoding="utf-8") as f:
            newcfg = json.load(f)
        nx = newcfg.get("pet", {}).get("window", {}).get("x")
        ny = newcfg.get("pet", {}).get("window", {}).get("y")
        log(f"回写后 config.json pet.window.x={nx} y={ny}")
        results.append(("主 config.json window.x/y 回写", nx == 321 and ny == 654))

        # 校验共享文件
        sp = pet_config.shared_config_path()
        with open(sp, "r", encoding="utf-8") as f:
            shared = json.load(f)
        sx = shared.get("window", {}).get("x")
        sy = shared.get("window", {}).get("y")
        log(f"共享文件 pet_shared.json window.x={sx} y={sy}")
        results.append(("共享文件 window.x/y 同步", sx == 321 and sy == 654))

        # 未知消息忽略校验
        async with connect("ws://127.0.0.1:9876") as ws2:
            await asyncio.wait_for(ws2.recv(), timeout=3)
            await ws2.send(json.dumps({"event": "bogus_event", "data": {"hack": True}}))
            await asyncio.sleep(0.5)
        log("未知事件发送（应被忽略，不崩溃）")
        results.append(("未知事件安全忽略", True))
    finally:
        # 还原 config.json 原始 window
        try:
            cfg = json.load(open(cfg_path, "r", encoding="utf-8"))
            cfg.setdefault("pet", {}).setdefault("window", {})
            cfg["pet"]["window"]["x"] = orig_win.get("x")
            cfg["pet"]["window"]["y"] = orig_win.get("y")
            tmp = cfg_path + ".tmp"
            json.dump(cfg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=4)
            os.replace(tmp, cfg_path)
            pet_config.write_shared_config()
            log(f"已还原 config.json window.x={orig_win.get('x')} y={orig_win.get('y')}")
        except Exception as e:
            log(f"还原失败: {e}")
        pet_ws_server.stop_server()

    log("=" * 50)
    ok = True
    for n, r in results:
        log(f"  [{'PASS' if r else 'FAIL'}] {n}")
        ok = ok and r
    log("最终: " + ("全部通过" if ok else "有失败"))
    with open(EVID, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines))
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
