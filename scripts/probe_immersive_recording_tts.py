# -*- coding: utf-8 -*-
"""
探针：沉浸式录音走 voice_pipeline/speech_service（问题5）
     + 真实 TTS 路径驱动口型（问题8）

问题5：点录音 → 必须走 facade.start_voice_recording()（=SpeechService/voice_pipeline），
       不再自己抱 QAudioRecorder。
问题8：触发 TTS 播放路径 → PetAnimator 嘴型必须真的在变（之前只有手动喂 level 才动）。

跑法：.venv\\Scripts\\python.exe scripts\\probe_immersive_recording_tts.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from app.widgets.immersive_mode import ImmersiveMode  # noqa: E402

FAILS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}{(' | ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


class StubFacade:
    """只记录语音录音域调用，模拟生产 ServiceFacade 的入口形状。"""

    def __init__(self):
        self.start_calls = 0
        self.stop_calls = 0

    def start_voice_recording(self):
        self.start_calls += 1
        return {"ok": True, "data": {"wav_path": ""}}

    def stop_voice_and_recognize(self):
        self.stop_calls += 1
        return {"ok": True, "data": {"text": "你好，在吗"}}

    # 其余入口空实现，避免沉浸式窗其他逻辑崩
    def get_active_person(self):
        return None

    def consult(self, *a, **k):
        return {"ok": True, "data": {"reply": "我在听，你说。"}}


def main() -> int:
    print("=" * 68)
    print("探针：沉浸式录音后端 + TTS 口型驱动（问题5/8）")
    print("=" * 68)

    facade = StubFacade()
    win = ImmersiveMode(facade=facade)

    print("\n[1] 点麦克风开始录音：")
    win._start_recording_internal()
    check("facade.start_voice_recording() 被调用（走 SpeechService）",
          facade.start_calls == 1, f"calls={facade.start_calls}")
    check("_recorder_backend == speech_service（不是 QAudioRecorder）",
          win._recorder_backend == "speech_service",
          f"backend={win._recorder_backend}")
    check("没有 new 出 QAudioRecorder 实例",
          win._audio_recorder is None,
          f"audio_recorder={win._audio_recorder}")

    print("\n[2] 停止录音：")
    win._stop_recording_internal()
    check("facade.stop_voice_and_recognize() 被调用",
          facade.stop_calls == 1, f"calls={facade.stop_calls}")
    # stop 是 singleShot(200) 后跑 _run_stt_pipeline，等一下
    time.sleep(0.3)
    _APP.processEvents()

    print("\n[3] TTS 口型同步（真实播放路径）：")
    anim = getattr(win, "_animator", None)
    check("PetAnimator 已创建（_animator 非空）", anim is not None)
    if anim is not None:
        # 先停掉 _run_stt_pipeline 触发的真实 edge-tts 播放，避免 EndOfMedia 竞态
        win._stop_playback()
        before = anim.current_state()["mouth"]
        win._start_tts_mouth()
        # 确定性单步驱动口型（不等定时器墙钟）
        mouths_seen = set()
        for _ in range(12):
            win._tts_mouth_step()
            mouths_seen.add(anim.current_state()["mouth"])
        after = anim.current_state()["mouth"]
        print(f"   播放前嘴型={before!r}，播放中见过={mouths_seen}")
        check("TTS 播放中嘴型真的在变（见过 ≥2 种元音）",
              len(mouths_seen) >= 2, f"seen={mouths_seen}")
        check("播放中嘴型不是 rest（闭嘴）",
              not mouths_seen.issubset({"rest"}),
              f"seen={mouths_seen}")
        win._stop_tts_mouth()
        rest = anim.current_state()["mouth"]
        check("停止 TTS 后嘴型回到 rest", rest == "rest", f"mouth={rest!r}")

    print("\n" + "=" * 68)
    if FAILS:
        print(f"结果：{len(FAILS)} 项失败 -> {FAILS}")
        return 1
    print("结果：全部 PASS —— 沉浸式录音走 SpeechService，TTS 播放真的驱动口型。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
