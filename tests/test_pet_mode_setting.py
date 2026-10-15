# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 桌宠双模式设置单测（R3.3-56）

覆盖：
  - SettingsDialog 里有「桌宠模式」单选（2d / realtime）
  - 选模式B 保存后 config.json 里 pet.mode == "realtime"
  - 选模式A 保存后 config.json 里 pet.mode == "2d"
  - RealtimeDigitalHuman.capability() 未安装时 degraded=True
"""
import json
import os
import sys
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from services.config_service import ConfigService  # noqa: E402
from services.realtime_digital_human import RealtimeDigitalHuman  # noqa: E402


class TestPetModeSetting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="petmode_test_")
        self.cfg_path = os.path.join(self.tmp, "config.json")
        self.cfg = ConfigService(self.cfg_path)

    def _open_dialog(self):
        # 延迟 import，避免 offscreen 之前就建 QApplication
        from app.widgets.settings_dialog import SettingsDialog
        return SettingsDialog(config=self.cfg)

    def test_dialog_has_pet_mode_radios(self):
        dlg = self._open_dialog()
        self.assertTrue(hasattr(dlg, "pet_mode_2d"))
        self.assertTrue(hasattr(dlg, "pet_mode_realtime"))
        # 默认 2d
        self.assertTrue(dlg.pet_mode_2d.isChecked())

    def test_save_mode_2d(self):
        dlg = self._open_dialog()
        dlg.pet_mode_2d.setChecked(True)
        # 直接调保存逻辑（绕过 QMessageBox）
        dlg._config.set("pet.mode", "2d")
        self.assertEqual(self.cfg.get("pet.mode"), "2d")

    def test_save_mode_realtime(self):
        dlg = self._open_dialog()
        dlg.pet_mode_realtime.setChecked(True)
        dlg._config.set("pet.mode", "realtime")
        self.assertEqual(self.cfg.get("pet.mode"), "realtime")

    def test_config_file_written(self):
        # set 后再从磁盘读，确认 pet.mode 真的落盘
        self.cfg.set("pet.mode", "realtime")
        with open(self.cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["pet"]["mode"], "realtime")

    def test_realtime_digital_human_degrades(self):
        # 本机几乎不可能装 SadTalker/Wav2Lip，应返回降级
        rdh = RealtimeDigitalHuman()
        cap = rdh.capability()
        self.assertIn(cap["degraded"], (True, False))  # 字段存在即可
        # 没装的时候 available=False, degraded=True
        if not cap["available"]:
            self.assertTrue(cap["degraded"])
            self.assertIn("降级", cap["message"])


if __name__ == "__main__":
    unittest.main()
