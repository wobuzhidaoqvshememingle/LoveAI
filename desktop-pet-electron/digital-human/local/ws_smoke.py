import asyncio, json, sys
import websockets

async def main():
    # try ports 9230..9239
    for port in range(9230, 9240):
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}", max_size=None) as ws:
                hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                print("RECV hello:", hello)
                await ws.send(json.dumps({"type": "hi"}))
                # read status + a few frames
                status = None
                frames = 0
                for _ in range(12):
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                    if msg.get("type") == "status":
                        status = msg
                        print("RECV status:", status)
                    elif msg.get("type") == "frame":
                        frames += 1
                        if frames == 1:
                            print(f"RECV frame #1: fmt={msg['fmt']} w={msg['w']} h={msg['h']} datalen={len(msg['data'])}")
                print(f"TOTAL frames received: {frames}")
                await ws.send(json.dumps({"type": "quit"}))
                print("SEND quit: OK")
                print("SMOKE_TEST: PASS")
                return 0
        except (OSError, asyncio.TimeoutError):
            continue
    print("SMOKE_TEST: FAIL no port")
    return 1

sys.exit(asyncio.run(main()))
