import os
import asyncio, json, traceback
import websockets

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "ws_dbg.log")
lines = []
def w(s):
    lines.append(str(s))

async def t():
    try:
        w("connecting...")
        async with websockets.connect("ws://127.0.0.1:9230", max_size=None, open_timeout=15) as ws:
            w("connected")
            h = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            w(f"HELLO: {h}")
            await ws.send(json.dumps({"type": "hi"}))
            for i in range(6):
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                keys = {k: v for k, v in m.items() if k != "data"}
                w(f"MSG[{i}]: {keys}")
            await ws.send(json.dumps({"type": "quit"}))
            w("QUIT sent")
    except Exception as e:
        w("EXC: " + repr(e))
        w(traceback.format_exc())

asyncio.run(t())
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
