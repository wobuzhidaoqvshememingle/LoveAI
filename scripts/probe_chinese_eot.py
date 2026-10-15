# -*- coding: utf-8 -*-
"""
探针：中文 EoT 开关 + Smart Turn VAD 真触发（问题1 / 问题4）

不走单测 mock 构造，从生产入口 SpeechService().get_voice_pipeline() 拿真管线：
  1) pipeline._use_chinese_eot 必须是 True（之前验收官实测是 False）
  2) pipeline.vad 必须是 EnergyVAD（之前是 SilentVAD，永不触发、对话空转）
  3) detect_chinese_eot("你好，最近怎么样？", silence=800ms) 必须 eot=True
  4) 喂「大音量 PCM16 帧 + 静音帧」，必须真触发 speech_start→speech_end→flush(ASR 被调)

跑法：.venv\\Scripts\\python.exe scripts\\probe_chinese_eot.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.speech_service import SpeechService  # noqa: E402
from services.chinese_eot import detect_chinese_eot  # noqa: E402
from services.voice_pipeline import (  # noqa: E402
    PipelineState, EnergyVAD,
)

FAILS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}{(' | ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def pcm16(amp: int, n: int = 1600) -> bytes:
    """构造 n 个 PCM16 小端样本（amp=0 即静音）。"""
    import array
    a = array.array("h")
    for _ in range(n):
        a.append(amp)
    return a.tobytes()


def main() -> int:
    print("=" * 68)
    print("探针：中文 EoT 开关 + EnergyVAD 生产触发（问题1/4）")
    print("=" * 68)

    # ---- 从生产入口拿管线 ----
    svc = SpeechService()
    pipeline = svc.get_voice_pipeline()
    check("生产入口 get_voice_pipeline() 返回非空管线", pipeline is not None)
    if pipeline is None:
        print("\n管线为 None，无法继续。")
        return 1

    print("\n[1] 生产管线关键开关：")
    eot_flag = getattr(pipeline, "_use_chinese_eot", None)
    vad_type = type(pipeline.vad).__name__
    check("pipeline._use_chinese_eot == True", eot_flag is True,
          f"实际={eot_flag}")
    check("pipeline.vad 是 EnergyVAD（不再是 SilentVAD）",
          vad_type == "EnergyVAD", f"实际={vad_type}")

    print("\n[2] detect_chinese_eot 函数级判定：")
    now = time.monotonic()
    v1 = detect_chinese_eot("你好，最近怎么样？", silence_ms=800,
                             last_token_ts=now - 0.8, now=now)
    check("句末问号+沉默800ms → eot=True", v1.get("eot") is True,
          f"reason={v1.get('reason')}")
    v2 = detect_chinese_eot("然后呢", silence_ms=200,
                            last_token_ts=now - 0.2, now=now)
    check("连接词尾巴+短沉默 → eot=False（延迟切轮）", v2.get("eot") is False,
          f"reason={v2.get('reason')}")
    v3 = detect_chinese_eot("嗯", silence_ms=300,
                            last_token_ts=now - 0.3, now=now)
    check("短应答词+500ms内 → eot=False（backchannel）", v3.get("eot") is False,
          f"reason={v3.get('reason')}")

    print("\n[3] 喂假音频：验证 EnergyVAD 真触发 speech_start→speech_end→flush：")
    # 离线无 Whisper：用一个桩替换 heavy transcribe（不换管线构造，只替代重依赖）
    calls = {"asr": 0}
    orig_transcribe = pipeline.asr.transcribe

    def _spy_transcribe(audio: bytes) -> str:
        calls["asr"] += 1
        # 真生产 ASR 是 SpeechServiceASRAdapter；这里只统计它是否被 flush 调用
        try:
            return orig_transcribe(audio)
        except Exception:
            return "今天好累"
    pipeline.asr.transcribe = _spy_transcribe

    seen_states = []
    pipeline.on_state_change = lambda s: seen_states.append(s.value)
    pipeline.start()
    check("pipeline.start() 后进入 LISTENING",
          pipeline.state == PipelineState.LISTENING, f"state={pipeline.state.value}")

    # 大音量帧 → 应触发 speech_start（RMS>=250）
    for _ in range(12):
        pipeline.feed_audio(pcm16(amp=8000))
    check("喂大音量帧后进入 SPEAKING_USER",
          pipeline.state == PipelineState.SPEAKING_USER,
          f"state={pipeline.state.value}")

    # 静音帧 → hangover(8帧) 后触发 speech_end → flush → ASR 被调
    for _ in range(14):
        pipeline.feed_audio(pcm16(amp=0))
    time.sleep(0.05)

    check("speech_end 后 ASR.transcribe 被 flush 调用 ≥1 次",
          calls["asr"] >= 1, f"asr_calls={calls['asr']}")
    check("状态机出现过 listening 与 speaking_user",
          "listening" in seen_states and "speaking_user" in seen_states,
          f"seen={seen_states}")
    # 之前 SilentVAD 永远不会进入 SPEAKING_USER —— 这就是「空转」的反证
    check("不再是 SilentVAD 空转（speech_start 真触发）",
          "speaking_user" in seen_states)

    pipeline.stop()

    print("\n" + "=" * 68)
    if FAILS:
        print(f"结果：{len(FAILS)} 项失败 -> {FAILS}")
        return 1
    print("结果：全部 PASS —— 中文 EoT 生产开关已打开，EnergyVAD 真触发不再空转。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
