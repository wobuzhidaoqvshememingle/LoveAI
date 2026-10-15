# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - SpeechService 单元测试

覆盖：
  - __init__ 不崩溃 / get_status 默认 available=False
  - _ensure_model 缺 faster-whisper 返回 False
  - start_recording 录音库不可用降级
  - stop_recording 未录音时降级
  - transcribe / transcribe_file / transcribe_and_fill 模型不可用降级
  - mock 模型时 transcribe 返回拼接文本、fill_text 被调用
  - cleanup 清理临时文件与模型句柄
"""
import os
import sys
import types
import shutil
import tempfile
import unittest
import wave
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.speech_service import SpeechService  # noqa: E402


def _make_silent_wav(path: str, seconds: float = 0.2, rate: int = 16000) -> str:
    """生成一段静音 WAV 文件供转写测试使用。"""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = int(rate * seconds)
        wf.writeframes(b"\x00\x00" * frames)
    return path


class TestSpeechService(unittest.TestCase):
    """SpeechService 测试类（无真实录音/模型，全部 mock / 降级路径）。"""

    def setUp(self) -> None:
        self.wechat = MagicMock()
        self.signals = MagicMock()
        self.svc = SpeechService(
            db=None, config=None, wechat_service=self.wechat, signals=self.signals,
        )
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_speech_")

    def tearDown(self) -> None:
        try:
            self.svc.cleanup()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 初始化 / 状态
    # ------------------------------------------------------------------ #
    def test_init_no_crash(self) -> None:
        """__init__ 不应崩溃，依赖与运行态属性就位。"""
        self.assertIsNone(self.svc.model)
        self.assertFalse(self.svc.model_available)
        self.assertFalse(self.svc.is_recording)
        self.assertEqual(self.svc.sample_rate, 16000)
        self.assertIs(self.svc.wechat_service, self.wechat)

    def test_init_reads_config(self) -> None:
        """传入 config 时应读取 speech.model_size / speech.device。"""
        cfg = MagicMock()
        cfg.get.side_effect = lambda k, d: {"speech.model_size": "small",
                                            "speech.device": "cpu"}.get(k, d)
        svc = SpeechService(db=None, config=cfg)
        self.assertEqual(svc._model_size, "small")

    def test_get_status_default_unavailable(self) -> None:
        """未加载模型时 get_status 的 available 应为 False。"""
        # mock掉录音库，模拟未安装环境
        with patch.dict(sys.modules, {"pyaudio": None, "sounddevice": None}):
            status = self.svc.get_status()
        self.assertFalse(status["available"])
        self.assertFalse(status["recording"])
        self.assertIn("model_size", status)
        # mock后录音库应为不可用
        with patch.dict(sys.modules, {"pyaudio": None, "sounddevice": None}):
            self.assertFalse(self.svc._check_recording_lib())

    # ------------------------------------------------------------------ #
    # Whisper 模型懒加载
    # ------------------------------------------------------------------ #
    def test_ensure_model_without_faster_whisper(self) -> None:
        """屏蔽 faster_whisper 导入时 _ensure_model 应返回 False。"""
        with patch.dict(sys.modules, {"faster_whisper": None}):
            result = self.svc._ensure_model()
        self.assertFalse(result)
        self.assertFalse(self.svc.model_available)

    def test_ensure_model_already_loaded(self) -> None:
        """模型已加载时直接返回 True，不再走导入。"""
        self.svc.model = MagicMock()
        self.svc.model_available = True
        self.assertTrue(self.svc._ensure_model())

    # ------------------------------------------------------------------ #
    # 录音控制
    # ------------------------------------------------------------------ #
    def test_start_recording_no_audio_libs(self) -> None:
        """pyaudio / sounddevice 均缺失时 start_recording 返回错误。"""
        with patch.dict(sys.modules, {"pyaudio": None, "sounddevice": None}):
            result = self.svc.start_recording()
        self.assertFalse(result.get("recording"))
        self.assertIn("error", result)

    def test_start_recording_idempotent(self) -> None:
        """已在录音时重复 start 直接返回成功（这里 mock 成录音中）。"""
        self.svc.is_recording = True
        result = self.svc.start_recording()
        self.assertTrue(result["recording"])

    def test_stop_recording_when_not_recording(self) -> None:
        """未在录音时 stop_recording 降级返回 error。"""
        result = self.svc.stop_recording()
        self.assertIsNone(result.get("wav_path"))
        self.assertIn("error", result)

    # ------------------------------------------------------------------ #
    # 转写
    # ------------------------------------------------------------------ #
    def test_transcribe_no_file(self) -> None:
        """无音频文件时 transcribe 返回空 text + error。"""
        result = self.svc.transcribe(None)
        self.assertEqual(result["text"], "")
        self.assertIn("error", result)

    def test_transcribe_model_unavailable(self) -> None:
        """音频存在但模型不可用时返回空 text + error，不抛异常。"""
        wav = _make_silent_wav(os.path.join(self.tmpdir, "x.wav"))
        # 模型目录不存在 + faster-whisper 缺失 → 不可用
        result = self.svc.transcribe(wav)
        self.assertEqual(result["text"], "")
        self.assertIn("error", result)

    def test_transcribe_file_not_exists(self) -> None:
        """transcribe_file 对不存在文件返回 error。"""
        result = self.svc.transcribe_file(os.path.join(self.tmpdir, "nope.wav"))
        self.assertEqual(result["text"], "")
        self.assertIn("文件不存在", result["error"])

    def test_transcribe_file_empty_path(self) -> None:
        """空路径也应降级。"""
        result = self.svc.transcribe_file("")
        self.assertEqual(result["text"], "")
        self.assertIn("error", result)

    def test_transcribe_with_mock_model(self) -> None:
        """mock 模型时应拼接多段文本。"""
        wav = _make_silent_wav(os.path.join(self.tmpdir, "m.wav"))
        seg1 = types.SimpleNamespace(start=0.0, end=1.0, text="你好")
        seg2 = types.SimpleNamespace(start=1.0, end=2.0, text="世界")
        info = types.SimpleNamespace(language="zh", duration=2.0)
        model = MagicMock()
        model.transcribe.return_value = ([seg1, seg2], info)
        self.svc.model = model
        self.svc.model_available = True

        result = self.svc.transcribe(wav)
        self.assertEqual(result["text"], "你好世界")
        self.assertEqual(result["language"], "zh")
        self.assertEqual(len(result["segments"]), 2)

    def test_transcribe_exception_degrades(self) -> None:
        """模型转写抛异常时降级为空 text + error。"""
        wav = _make_silent_wav(os.path.join(self.tmpdir, "e.wav"))
        model = MagicMock()
        model.transcribe.side_effect = RuntimeError("boom")
        self.svc.model = model
        self.svc.model_available = True
        result = self.svc.transcribe(wav)
        self.assertEqual(result["text"], "")
        self.assertIn("error", result)

    # ------------------------------------------------------------------ #
    # 转写 + 填入微信
    # ------------------------------------------------------------------ #
    def test_transcribe_and_fill_calls_wechat(self) -> None:
        """识别出文本后应调用 wechat_service.fill_text 并清理临时 WAV。"""
        wav = _make_silent_wav(os.path.join(self.tmpdir, "f.wav"))
        self.svc._temp_wav = wav  # 模拟最近一次录音产物
        seg = types.SimpleNamespace(start=0.0, end=1.0, text="在吗")
        info = types.SimpleNamespace(language="zh", duration=1.0)
        model = MagicMock()
        model.transcribe.return_value = ([seg], info)
        self.svc.model = model
        self.svc.model_available = True
        self.wechat.fill_text.return_value = True

        result = self.svc.transcribe_and_fill()
        self.assertEqual(result["text"], "在吗")
        self.assertTrue(result["filled"])
        self.wechat.fill_text.assert_called_once_with("在吗")
        # 临时 WAV 应被清理
        self.assertFalse(os.path.exists(wav))

    def test_transcribe_and_fill_degraded_when_no_text(self) -> None:
        """模型不可用时 transcribe_and_fill 降级，不崩溃、不填微信。"""
        result = self.svc.transcribe_and_fill()
        self.assertEqual(result["text"], "")
        self.assertFalse(result["filled"])
        self.wechat.fill_text.assert_not_called()

    def test_transcribe_and_fill_wechat_exception_safe(self) -> None:
        """微信填入抛异常时 filled=False，不向上抛。"""
        wav = _make_silent_wav(os.path.join(self.tmpdir, "g.wav"))
        self.svc._temp_wav = wav
        seg = types.SimpleNamespace(start=0.0, end=1.0, text="你好")
        info = types.SimpleNamespace(language="zh", duration=1.0)
        model = MagicMock()
        model.transcribe.return_value = ([seg], info)
        self.svc.model = model
        self.svc.model_available = True
        self.wechat.fill_text.side_effect = RuntimeError("wechat down")

        result = self.svc.transcribe_and_fill()
        self.assertEqual(result["text"], "你好")
        self.assertFalse(result["filled"])

    # ------------------------------------------------------------------ #
    # 清理
    # ------------------------------------------------------------------ #
    def test_cleanup_removes_temp_wav_and_resets_model(self) -> None:
        """cleanup 应删除临时 WAV、释放模型句柄。"""
        wav = _make_silent_wav(os.path.join(self.tmpdir, "c.wav"))
        self.svc._temp_wav = wav
        self.svc.model = MagicMock()
        self.svc.model_available = True

        self.svc.cleanup()
        self.assertFalse(os.path.exists(wav))
        self.assertIsNone(self.svc.model)
        self.assertFalse(self.svc.model_available)
        self.assertIsNone(self.svc._temp_wav)

    def test_cleanup_when_recording(self) -> None:
        """cleanup 在录音中也应安全退出（线程句柄容错）。"""
        self.svc.is_recording = True
        self.svc._record_thread = None
        try:
            self.svc.cleanup()
        except Exception as e:  # noqa: BLE001
            self.fail(f"cleanup 不应抛异常: {e}")
        self.assertFalse(self.svc.is_recording)


if __name__ == "__main__":
    unittest.main(verbosity=2)
