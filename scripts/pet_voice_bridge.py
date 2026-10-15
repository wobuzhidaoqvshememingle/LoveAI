"""LoveAI-v2 系统精灵语音后端桥接（关系分析助手，独立运行，不依赖 Qt 主程序）。

职责：
- WebSocket 服务 127.0.0.1:9876（桌宠端连接，双向事件）
- HTTP 静态服务 127.0.0.1:9877（对外暴露 data/voice_cache/ 下的 MP3）
- 上行：
    * user_text / user_voice_text {text}   -> 直接进对话
    * start_recording                       -> 清空该连接的录音缓冲
    * 二进制帧（bytes）                      -> 追加为 16kHz 单声道 PCM16
    * stop_recording                        -> 对缓冲跑本地 Whisper STT -> 进对话
- 处理：LLM provider（本地GGUF/Ollama/云兼容/模板自动选）-> TTS provider（edge/克隆）
- 下行：speech_state / ai_thinking / asr_result / ai_speak

启动：.venv\\Scripts\\python.exe scripts\\pet_voice_bridge.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Set

# ---- 路径定位：项目根 = scripts/ 的上一级 ----
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VOICE_CACHE_DIR = ROOT / "data" / "voice_cache"
LOG_DIR = ROOT / "logs"
VOICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

WS_HOST = "127.0.0.1"
WS_PORT = 9876
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 9877

SYSTEM_PROMPT = (
    "你是温柔、有耐心的恋爱关系分析助手（游戏系统精灵），说话简短（不超过50字），"
    "用口语化中文，偶尔带点语气词；帮用户复盘与真实对象的互动、给出现实建议，"
    "你是预测与反馈工具，不是用户的恋爱对象。"
)
HISTORY_MAX_MESSAGES = 40

# ---- 日志 ----
logger = logging.getLogger("voice_bridge")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
_fh = logging.FileHandler(LOG_DIR / "voice_bridge.log", encoding="utf-8")
_fh.setFormatter(_fmt)
logger.handlers = [_sh, _fh]
logger.propagate = False
for _name in ("services.edge_tts_service", "LoveAI.stt", "LoveAI.llm_provider", "LoveAI.tts_provider"):
    _l = logging.getLogger(_name)
    _l.setLevel(logging.INFO)
    _l.handlers = [_sh, _fh]
    _l.propagate = False

# ---- 对话历史（内存） ----
history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]


def _trim_history() -> None:
    global history
    if len(history) > HISTORY_MAX_MESSAGES + 1:
        history = [history[0]] + history[-(HISTORY_MAX_MESSAGES):]


# ---- 启动时装配 provider（懒，但一次性决定用哪条路） ----
from services.llm_providers import build_provider as _build_llm  # noqa: E402
from services.tts_providers import build_tts_provider as _build_tts  # noqa: E402
from services.stt_service import get_stt  # noqa: E402

LLM = _build_llm()
TTS = _build_tts()
STT = get_stt()
DH = None  # 数字人按需构建（见 _handle_user_text）


async def _llm_reply(user_text: str) -> str:
    history.append({"role": "user", "content": user_text})
    try:
        text = await LLM.chat(history)
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM 调用失败: %s", e)
        text = ""
    if not text:
        text = f"老公，我听到你说「{user_text}」啦～"
    history.append({"role": "assistant", "content": text})
    _trim_history()
    return text


# ---- WS 连接管理 ----
from websockets.asyncio.server import ServerConnection, serve  # noqa: E402

connected: Set[ServerConnection] = set()
# 每条连接的录音缓冲：{ws: bytearray}
rec_buf: dict[ServerConnection, bytearray] = {}


async def _broadcast(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    dead = []
    for ws in list(connected):
        try:
            await ws.send(text)
        except Exception as e:  # noqa: BLE001
            logger.warning("广播失败，剔除连接: %s", e)
            dead.append(ws)
    for ws in dead:
        connected.discard(ws)


async def _handle_user_text(user_text: str, source: str) -> None:
    """完整处理一条用户输入：thinking -> LLM -> TTS -> ai_speak -> idle。"""
    logger.info("收到%s: %r", source, user_text)
    await _broadcast({"event": "speech_state", "state": "processing"})
    await _broadcast({"event": "ai_thinking", "text": "嗯…让我想想～", "emotion": "neutral"})

    reply_text = await _llm_reply(user_text)
    logger.info("AI 回复[%s]: %r", LLM.name, reply_text)

    ts = int(time.time() * 1000)
    ext = "wav" if TTS.name == "clone" else "mp3"
    out_path = VOICE_CACHE_DIR / f"reply_{ts}.{ext}"
    try:
        await TTS.synthesize(reply_text, str(out_path))
    except Exception as e:  # noqa: BLE001
        logger.error("TTS 合成失败，退回 edge-tts: %s", e)
        try:
            from services.edge_tts_service import EdgeTTSService
            out_path = VOICE_CACHE_DIR / f"reply_{ts}.mp3"
            await EdgeTTSService().synthesize(reply_text, str(out_path))
        except Exception as e2:  # noqa: BLE001
            logger.error("edge-tts 也失败: %s", e2)
            await _broadcast({"event": "ai_speak", "text": reply_text, "audio_url": "", "emotion": "sad"})
            await _broadcast({"event": "speech_state", "state": "idle"})
            return

    await _broadcast({"event": "speech_state", "state": "speaking"})
    audio_url = f"http://{HTTP_HOST}:{HTTP_PORT}/{out_path.name}"
    await _broadcast({
        "event": "ai_speak", "text": reply_text, "audio_url": audio_url, "emotion": "happy",
    })
    await asyncio.sleep(1.0)
    await _broadcast({"event": "speech_state", "state": "idle"})


async def _handle_stop_recording(ws: ServerConnection) -> None:
    """录音结束：取缓冲 PCM -> Whisper 转写 -> 当作语音输入进对话。"""
    pcm = bytes(rec_buf.pop(ws, b""))
    if not pcm:
        await _broadcast({"event": "speech_state", "state": "idle"})
        return
    if not STT.available():
        st = STT.status()
        hint = st.get("hint") or "本地语音识别不可用"
        logger.warning("STT 不可用（%s），放弃这段录音", st.get("reason"))
        # 原来是静默回 idle，用户完全不知道发生了什么（假阳性）。
        # 现按桌宠气泡如实播报原因 + 修复指引。
        await _broadcast({"event": "ai_thinking",
                          "text": f"（本地语音识别没就绪：{hint}）",
                          "emotion": "neutral"})
        await asyncio.sleep(1.5)
        await _broadcast({"event": "speech_state", "state": "idle"})
        return
    await _broadcast({"event": "speech_state", "state": "processing"})
    # STT 推理较慢，丢线程池
    text = await asyncio.to_thread(STT.transcribe_pcm16, pcm, 16000)
    text = (text or "").strip()
    logger.info("Whisper 转写: %r （%d bytes PCM）", text, len(pcm))
    if not text:
        await _broadcast({"event": "ai_thinking", "text": "（没听清，再说一次？）", "emotion": "neutral"})
        await asyncio.sleep(1.2)
        await _broadcast({"event": "speech_state", "state": "idle"})
        return
    # 把识别到的原话回显给桌宠气泡
    await _broadcast({"event": "asr_result", "text": text})
    await _handle_user_text(text, "真麦克风语音")


async def _ws_handler(websocket: ServerConnection) -> None:
    connected.add(websocket)
    peer = getattr(websocket, "remote_address", None)
    logger.info("桌宠连接 %s（当前 %d 个客户端）", peer, len(connected))
    try:
        await websocket.send(json.dumps(
            {"event": "hello", "data": {"version": "3.0", "status": "connected",
                                        "llm": LLM.name, "tts": TTS.name,
                                        "stt": STT.available()}},
            ensure_ascii=False,
        ))
        await websocket.send(json.dumps({"event": "speech_state", "state": "idle"}, ensure_ascii=False))

        async for raw in websocket:
            # 1) 二进制 = 录音 PCM16 帧
            if isinstance(raw, (bytes, bytearray)):
                rec_buf.setdefault(websocket, bytearray()).extend(raw)
                continue
            # 2) 文本 JSON
            try:
                msg = json.loads(raw)
            except Exception:  # noqa: BLE001
                logger.warning("无法解析上行消息: %r", raw[:200] if isinstance(raw, str) else type(raw))
                continue
            event = msg.get("event")
            if event == "user_text":
                await _handle_user_text(msg.get("text", ""), "键盘输入")
            elif event == "user_voice_text":
                await _handle_user_text(msg.get("text", ""), "语音文本")
            elif event == "start_recording":
                rec_buf[websocket] = bytearray()
                await _broadcast({"event": "speech_state", "state": "recording"})
            elif event == "stop_recording":
                await _handle_stop_recording(websocket)
            elif event == "ping":
                await websocket.send(json.dumps({"event": "pong"}, ensure_ascii=False))
            else:
                logger.info("未知上行事件: %r", msg)
    except Exception as e:  # noqa: BLE001
        logger.warning("连接异常 %s: %s", peer, e)
    finally:
        connected.discard(websocket)
        rec_buf.pop(websocket, None)
        logger.info("桌宠断开 %s（剩余 %d 个客户端）", peer, len(connected))


async def _http_static_server() -> None:
    from aiohttp import web
    app = web.Application()
    app.router.add_static("/", path=str(VOICE_CACHE_DIR), name="voice_cache")

    async def _health(_req):
        return web.json_response({"ok": True, "llm": LLM.name, "tts": TTS.name,
                                  "stt": STT.available(), "dir": str(VOICE_CACHE_DIR)})
    app.router.add_get("/_health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HTTP_HOST, HTTP_PORT)
    await site.start()
    logger.info("HTTP 静态服务已启动: http://%s:%d/  ->  %s", HTTP_HOST, HTTP_PORT, VOICE_CACHE_DIR)


async def _main() -> None:
    logger.info("启动：llm=%s tts=%s stt=%s", LLM.name, TTS.name, STT.available())
    await _http_static_server()
    async with serve(_ws_handler, WS_HOST, WS_PORT, max_size=8 * 1024 * 1024) as server:
        logger.info("WS 服务已启动: ws://%s:%d （等待桌宠连接）", WS_HOST, WS_PORT)
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，退出。")
