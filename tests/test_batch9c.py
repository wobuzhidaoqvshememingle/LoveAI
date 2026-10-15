# -*- coding: utf-8 -*-
"""第八批剩余第三批：模块权限/配置备份。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.batch9_admin import ModulePermission, ConfigBackup


class TestBatch9C(unittest.TestCase):
    def test_permission_balanced(self):
        mp = ModulePermission("平衡")
        self.assertEqual(mp.allowed("strategy"), "自动")
        self.assertEqual(mp.allowed("代聊发送"), "需确认")

    def test_permission_conservative_skips(self):
        mp = ModulePermission("保守")
        self.assertEqual(mp.allowed("高危"), "跳过")

    def test_config_backup_restore(self):
        cb = ConfigBackup(); cb.backup({"k":"v"})
        self.assertEqual(cb.restore(), {"k":"v"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
