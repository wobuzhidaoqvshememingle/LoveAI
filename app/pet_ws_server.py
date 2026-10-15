"""
LoveAI v2.0 - 桌宠联动 WebSocket 服务端
=====================================
在后台线程跑独立的 asyncio event loop，监听 127.0.0.1:9876，
把 LoveAI 全局信号总线（AppSignals）的事件实时推送给桌宠客户端。

设计要点：
1. 不阻塞 UI：WS server 跑在 daemon 后台线程，主线程（Qt 事件循环）完全无感。
2. 线程安全：PySide6 信号在主线程发射，通过 loop.call_soon_threadsafe 把
   广播任务投递到 WS 所在的 asyncio loop，避免跨线程直接操作。
3. 多客户端广播：维护一个 set，所有事件 fan-out 到全部连接的桌宠实例。
4. 优雅启停：start_server() 幂等；stop_server() 关闭 server + 断开信号 +
   join 后台线程，退出不留孤儿。
5. 只监听 127.0.0.1，不对外网暴露。
"""
import asyncio
import json
import logging
import time
import threading
from typing import Any

logger = logging.getLogger("LoveAI.pet_ws")

# 心跳参数（可被 config.json pet 段覆盖）
_HEARTBEAT_INTERVAL = 15.0   # 服务端每 15s 广播一次 ping
_HEARTBEAT_TIMEOUT = 45.0    # 客户端 >45s 无任何上行则剔除
_MAX_MSG_BYTES = 64 * 1024   # 上行消息有界：64KB

# 模块级单例状态
_state: dict[str, Any] = {
    "thread": None,          # 后台线程对象
    "loop": None,            # 该线程内的 asyncio event loop
    "server": None,          # websockets Server 对象
    "clients": set(),        # 当前已连接的桌宠 websocket 集合
    "client_meta": {},       # ws -> {"last_seen": float, "client_version": str}
    "heartbeat_task": None,  # 心跳广播/失联清扫协程
    "host": "127.0.0.1",
    "port": 9876,
    "started": False,
    "signals_connected": False,
    "main_version": "2.0.0",
}


# ------------------------------------------------------------------ #
# 事件内容构造（信号 payload -> 桌宠事件）
# ------------------------------------------------------------------ #
def _extract_relationship_stage(data: dict) -> str:
    """从 analysis_ready 的 data 里尽量提取关系阶段字符串（探测多种字段名）。"""
    if not isinstance(data, dict):
        return ""
    for key in ("relationship_stage", "stage", "relationship", "phase", "stage_name"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # data 可能再嵌套一层
    inner = data.get("data")
    if isinstance(inner, dict):
        for key in ("relationship_stage", "stage", "relationship", "phase"):
            v = inner.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _make_speech_text(payload: dict) -> str:
    """根据 analysis_ready 数据生成一句简短的角色台词。"""
    if not isinstance(payload, dict):
        return "分析完了哦~"
    module = payload.get("module")
    if isinstance(module, str) and module:
        return f"{module}分析完了哦~"
    return "分析完了哦~"


# ------------------------------------------------------------------ #
# 信号 -> WS 事件映射（这些槽函数在主线程被 Qt 信号触发）
# ------------------------------------------------------------------ #
def _on_analysis_progress(payload: dict) -> None:
    """分析进行中 -> thinking 事件"""
    try:
        stage = (payload or {}).get("stage", "")
        percent = (payload or {}).get("percent", 0)
        if stage:
            text = f"{stage}..."
        else:
            text = f"正在分析... {percent}%"
        event = {
            "event": "thinking",
            "text": text,
            "emotion": "neutral",
        }
        _broadcast(event)
    except Exception as e:
        logger.debug("转发 analysis_progress 失败: %s", e)


def _on_analysis_ready(payload: dict) -> None:
    """分析完成 -> speaking 事件（带关系阶段）"""
    try:
        data = (payload or {}).get("data", payload)
        stage = _extract_relationship_stage(data if isinstance(data, dict) else {})
        event = {
            "event": "speaking",
            "text": _make_speech_text(payload if isinstance(payload, dict) else {}),
            "emotion": "happy",
            "relationship_stage": stage,
        }
        _broadcast(event)
    except Exception as e:
        logger.debug("转发 analysis_ready 失败: %s", e)


def _on_affection_changed(payload: dict) -> None:
    """好感度变化 -> emotion 事件"""
    try:
        total = (payload or {}).get("total", 0)
        event = {
            "event": "emotion",
            "data": {"affection": total},
        }
        _broadcast(event)
    except Exception as e:
        logger.debug("转发 affection_changed 失败: %s", e)


def _on_new_message(payload: dict) -> None:
    """收到新微信消息 -> thinking 事件"""
    try:
        event = {
            "event": "thinking",
            "text": "收到新消息，想想怎么回...",
            "emotion": "neutral",
        }
        _broadcast(event)
    except Exception as e:
        logger.debug("转发 new_message 失败: %s", e)


# ------------------------------------------------------------------ #
# 广播（线程安全：从任意线程投递到 WS loop）
# ------------------------------------------------------------------ #
def _broadcast(event: dict) -> None:
    """从任意线程调用：把事件调度到 WS loop 上广播给所有客户端。"""
    loop = _state.get("loop")
    if loop is None or not loop.is_running():
        return
    try:
        # call_soon_threadsafe 是跨线程提交协程回调的唯一安全方式
        loop.call_soon_threadsafe(_schedule_broadcast, event)
    except Exception as e:
        logger.debug("调度广播失败: %s", e)


def _schedule_broadcast(event: dict) -> None:
    """在 WS loop 线程内执行：创建广播任务。"""
    try:
        asyncio.create_task(_do_broadcast(event))
    except Exception as e:
        logger.debug("创建广播任务失败: %s", e)


async def _do_broadcast(event: dict) -> None:
    """真正的广播实现：并发给所有客户端发送；单客户端失败就剔除。"""
    msg = json.dumps(event, ensure_ascii=False)
    clients = list(_state["clients"])
    if not clients:
        return

    async def _send_one(ws):
        try:
            await ws.send(msg)
        except Exception:
            # 发送失败说明连接已断，从集合里剔除
            try:
                _state["clients"].discard(ws)
            except Exception:
                pass

    await asyncio.gather(*(_send_one(ws) for ws in clients), return_exceptions=True)


# ------------------------------------------------------------------ #
# 客户端上行消息处理（有界大小 + schema 校验 + 未知消息忽略）
# ------------------------------------------------------------------ #
def _touch_client(ws) -> None:
    meta = _state["client_meta"].get(ws)
    if meta is not None:
        meta["last_seen"] = time.time()


async def _handle_incoming(ws, raw: Any) -> None:
    """解析桌宠上行消息并路由。任何异常只记 debug，绝不踢连接。"""
    # 1) 大小有界（二进制帧直接忽略）
    if isinstance(raw, (bytes, bytearray)):
        return
    if not isinstance(raw, str):
        return
    if len(raw) > _MAX_MSG_BYTES:
        logger.debug("桌宠上行消息过大(%dB)，忽略", len(raw))
        return
    # 任何合法上行都视为"活着"，刷新心跳时间
    _touch_client(ws)
    try:
        msg = json.loads(raw)
    except Exception:
        return
    if not isinstance(msg, dict):
        return
    event = msg.get("event")
    data = msg.get("data", {}) if isinstance(msg.get("data"), dict) else {}

    if event == "pong":
        # 心跳回执：已在 _touch_client 刷新时间，这里无需动作
        return

    if event == "hello":
        # 客户端报到：携带桌宠版本号，与主程序版本对齐校验
        client_ver = str(data.get("version", ""))
        meta = _state["client_meta"].get(ws)
        if meta is not None:
            meta["client_version"] = client_ver
        main_ver = _state.get("main_version", "2.0.0")
        if client_ver and client_ver != main_ver:
            logger.warning("桌宠版本与主程序不一致：桌宠=%s 主程序=%s", client_ver, main_ver)
            try:
                await ws.send(json.dumps({
                    "event": "version_mismatch",
                    "data": {"pet": client_ver, "main": main_ver},
                }, ensure_ascii=False))
            except Exception:
                pass
        else:
            logger.info("桌宠版本对齐：%s", client_ver or "未知")
        return

    if event == "config_update":
        # 桌宠设置变更上行：回写主 config.json pet 段 + 共享文件
        try:
            from app import pet_config
            pet_config.apply_uplink(data)
        except Exception as e:
            logger.debug("上行配置回写失败: %s", e)
        return

    if event == "speaking_state":
        # 桌宠说话状态（开始/结束朗读）上行，仅记录日志
        logger.debug("桌宠说话状态: %s", data)
        return

    # 未知事件：静默忽略（保持协议向前兼容）


# ------------------------------------------------------------------ #
# 心跳广播 + 失联清扫（在 WS loop 线程内运行）
# ------------------------------------------------------------------ #
async def _heartbeat_loop() -> None:
    """周期广播 ping，并剔除 >timeout 无上行的失联客户端。"""
    while True:
        try:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
        except Exception:
            return
        # 1) 广播 ping
        try:
            await _do_broadcast({"event": "ping", "data": {"ts": time.time()}})
        except Exception:
            pass
        # 2) 清扫失联客户端
        now = time.time()
        for ws in list(_state["clients"]):
            meta = _state["client_meta"].get(ws)
            if meta is None:
                continue
            last = meta.get("last_seen", now)
            if now - last > _HEARTBEAT_TIMEOUT:
                logger.info("桌宠客户端心跳超时(%.0fs)，断开连接", now - last)
                try:
                    await ws.close(code=1000, reason="heartbeat timeout")
                except Exception:
                    pass
                _state["clients"].discard(ws)
                _state["client_meta"].pop(ws, None)


# ------------------------------------------------------------------ #
# WebSocket 连接处理
# ------------------------------------------------------------------ #
async def _handle_client(websocket) -> None:
    """每个新连接进入这里：发 hello（带主程序版本），登记元数据，
    阻塞读直到断开；上行消息（pong/hello/config_update）在此路由。"""
    main_ver = _state.get("main_version", "2.0.0")
    # 1) 新连接握手：立即推送 hello 事件（携带真实主程序版本号）
    hello = {
        "event": "hello",
        "data": {"version": main_ver, "status": "connected"},
    }
    try:
        await websocket.send(json.dumps(hello, ensure_ascii=False))
    except Exception:
        return

    # 2) 登记客户端集合与心跳元数据
    _state["clients"].add(websocket)
    _state["client_meta"][websocket] = {"last_seen": time.time(), "client_version": ""}
    logger.info("桌宠客户端已连接，当前连接数=%d", len(_state["clients"]))

    try:
        # 3) 阻塞读：路由上行消息；客户端断开时自然退出
        async for raw in websocket:
            try:
                await _handle_incoming(websocket, raw)
            except Exception as e:
                logger.debug("处理桌宠上行消息异常: %s", e)
    except Exception:
        pass
    finally:
        _state["clients"].discard(websocket)
        _state["client_meta"].pop(websocket, None)
        logger.info("桌宠客户端断开，当前连接数=%d", len(_state["clients"]))


# ------------------------------------------------------------------ #
# 后台线程：跑 asyncio loop
# ------------------------------------------------------------------ #
def _run_server_thread(host: str, port: int, ready_event: threading.Event) -> None:
    """后台线程入口：建 loop、起 websockets server、serve_forever 直到 stop。"""
    loop = asyncio.new_event_loop()
    _state["loop"] = loop
    asyncio.set_event_loop(loop)
    try:
        # websockets 17.x 新版 API：from websockets.asyncio.server import serve
        from websockets.asyncio.server import serve

        async def _start():
            # 主程序版本号（用于桌宠版本对齐校验）
            try:
                from app import pet_config
                _state["main_version"] = pet_config.app_version()
            except Exception:
                pass
            server = await serve(_handle_client, host=host, port=port)
            _state["server"] = server
            # 启动心跳广播/失联清扫协程
            _state["heartbeat_task"] = asyncio.create_task(_heartbeat_loop())
            ready_event.set()
            logger.info("桌宠 WS 服务端已启动: ws://%s:%d (main_version=%s)",
                        host, port, _state["main_version"])
            # 阻塞直到 server.close() 被调用
            await server.serve_forever()

        loop.run_until_complete(_start())
    except Exception as e:
        logger.error("桌宠 WS 服务端启动失败: %s", e)
        # 即使失败也要 set ready，防止 start_server 卡死
        ready_event.set()
    finally:
        try:
            loop.close()
        except Exception:
            pass
        _state["loop"] = None


# ------------------------------------------------------------------ #
# 对外 API：start_server / stop_server
# ------------------------------------------------------------------ #
def start_server(host: str = "127.0.0.1", port: int = 9876) -> None:
    """启动桌宠 WS 服务端（幂等）。在后台线程跑，不阻塞 UI 线程。"""
    if _state["started"]:
        logger.info("桌宠 WS 服务端已在运行，跳过重复启动")
        return
    _state["host"] = host
    _state["port"] = port
    ready = threading.Event()
    t = threading.Thread(
        target=_run_server_thread,
        args=(host, port, ready),
        name="pet-ws-server",
        daemon=True,
    )
    t.start()
    _state["thread"] = t
    _state["started"] = True
    # 等 server 真正 listen 或失败（最多 3 秒，超时不阻塞 app 启动）
    ready.wait(timeout=3.0)

    # 接线全局信号总线（主线程调用 connect 是安全的）
    try:
        from app.signals import get_signals
        sig = get_signals()
        sig.analysis_progress.connect(_on_analysis_progress)
        sig.analysis_ready.connect(_on_analysis_ready)
        sig.affection_changed.connect(_on_affection_changed)
        sig.new_message.connect(_on_new_message)
        _state["signals_connected"] = True
        logger.info("桌宠 WS 信号总线已接线 "
                    "(analysis_progress/analysis_ready/affection_changed/new_message)")
    except Exception as e:
        logger.warning("桌宠 WS 信号接线失败: %s", e)


def stop_server() -> None:
    """停止 WS 服务端（幂等）。关闭 server、断开信号、join 后台线程。"""
    if not _state["started"]:
        return
    _state["started"] = False

    # 1) 断开 Qt 信号连接（必须在主线程调用，这里由 app.quit() 触发）
    if _state.get("signals_connected"):
        try:
            from app.signals import get_signals
            sig = get_signals()
            for s, fn in (
                (sig.analysis_progress, _on_analysis_progress),
                (sig.analysis_ready, _on_analysis_ready),
                (sig.affection_changed, _on_affection_changed),
                (sig.new_message, _on_new_message),
            ):
                try:
                    s.disconnect(fn)
                except Exception:
                    pass
        except Exception:
            pass
        _state["signals_connected"] = False

    # 2) 在 WS loop 线程内关闭 server 和所有客户端
    loop = _state.get("loop")
    server = _state.get("server")
    if loop is not None and server is not None:
        async def _shutdown():
            try:
                # 0) 取消心跳/清扫协程
                hb = _state.get("heartbeat_task")
                if hb is not None:
                    hb.cancel()
                    _state["heartbeat_task"] = None
                # 先关所有客户端连接
                for ws in list(_state["clients"]):
                    try:
                        await ws.close(code=1001, reason="server shutdown")
                    except Exception:
                        pass
                _state["clients"].clear()
                _state["client_meta"].clear()
                # 再关 server（这会让 serve_forever() 返回，后台线程退出）
                server.close()
                await server.wait_closed()
            except Exception:
                pass
        try:
            future = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
            future.result(timeout=5)
        except Exception as e:
            logger.debug("关闭 WS server 异常: %s", e)

    # 3) join 后台线程（最多 3 秒）
    t = _state.get("thread")
    if t is not None:
        t.join(timeout=3)

    _state["server"] = None
    _state["thread"] = None
    logger.info("桌宠 WS 服务端已停止")
