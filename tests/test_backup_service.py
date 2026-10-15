# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - BackupService 单元测试

覆盖：
  - __init__ 目录创建 / create_backup 生成 zip / manifest 内容
  - list_backups / restore_backup / delete_backup
  - apply_retention_policy 清理旧备份
  - export_data / import_data JSON
  - get_backup_stats / should_auto_backup
  - 备份文件路径穿越安全检查

所有测试均在 tempfile.mkdtemp() 临时项目目录内进行，不污染真实项目。
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
import zipfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.backup_service import BackupService  # noqa: E402


def _build_fake_project(project_root: str) -> None:
    """在临时目录构造一个最小项目结构（data/ + config.json）。"""
    data_dir = os.path.join(project_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "app.db"), "wb") as f:
        f.write(b"SQLITE-DUMB-CONTENT" * 100)
    with open(os.path.join(project_root, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"speech": {"model_size": "base"}}, f, ensure_ascii=False)


class TestBackupService(unittest.TestCase):
    """BackupService 测试类（project_root 全部指向临时目录）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_backup_")
        self.project_root = os.path.join(self.tmpdir, "proj")
        os.makedirs(self.project_root, exist_ok=True)
        _build_fake_project(self.project_root)
        self.svc = BackupService(project_root=self.project_root, config=None)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 初始化
    # ------------------------------------------------------------------ #
    def test_init_creates_dirs(self) -> None:
        self.assertTrue(os.path.isdir(self.svc.backups_dir))
        self.assertTrue(os.path.isdir(self.svc.exports_dir))
        self.assertEqual(self.svc.project_root, self.project_root)

    # ------------------------------------------------------------------ #
    # 备份创建 / 清单
    # ------------------------------------------------------------------ #
    def test_create_backup_creates_zip(self) -> None:
        r = self.svc.create_backup(note="测试")
        self.assertNotIn("error", r)
        self.assertTrue(os.path.isfile(r["path"]))
        with zipfile.ZipFile(r["path"]) as zf:
            names = zf.namelist()
        self.assertIn("MANIFEST.json", names)
        self.assertIn("config.json", names)

    def test_manifest_content(self) -> None:
        r = self.svc.create_backup(note="我的备份")
        with zipfile.ZipFile(r["path"]) as zf:
            manifest = json.loads(zf.read("MANIFEST.json"))
        self.assertEqual(manifest["app_version"], "2.0.0")
        self.assertEqual(manifest["note"], "我的备份")
        self.assertEqual(manifest["backup_id"], r["backup_id"])
        self.assertIsInstance(manifest["contents"], list)

    # ------------------------------------------------------------------ #
    # 列表 / 删除
    # ------------------------------------------------------------------ #
    def test_list_backups(self) -> None:
        self.svc.create_backup()
        backups = self.svc.list_backups()
        self.assertEqual(len(backups), 1)
        self.assertIn("filename", backups[0])
        self.assertIn("size_mb", backups[0])

    def test_delete_backup(self) -> None:
        r = self.svc.create_backup()
        bid = r["backup_id"]
        self.assertTrue(self.svc.delete_backup(bid).get("deleted"))
        self.assertEqual(self.svc.list_backups(), [])

    def test_delete_backup_not_exists(self) -> None:
        self.assertIn("error", self.svc.delete_backup("no_such_backup"))

    # ------------------------------------------------------------------ #
    # 恢复
    # ------------------------------------------------------------------ #
    def test_restore_backup_to_target(self) -> None:
        r = self.svc.create_backup()
        target = os.path.join(self.tmpdir, "restored")
        os.makedirs(target, exist_ok=True)
        res = self.svc.restore_backup(r["path"], target_dir=target)
        self.assertTrue(res["restored"])
        self.assertTrue(os.path.isfile(os.path.join(target, "config.json")))

    def test_restore_backup_not_exists(self) -> None:
        res = self.svc.restore_backup(os.path.join(self.tmpdir, "ghost.zip"))
        self.assertIn("error", res)

    def test_path_traversal_safe(self) -> None:
        """zip 内含 ../ 穿越条目时应被跳过，不写到目标目录之外。"""
        bad_zip = os.path.join(self.tmpdir, "bad.zip")
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("../evil_danger.txt", "evil")
            zf.writestr("data/ok.txt", "ok")
        target = os.path.join(self.tmpdir, "restore_target")
        os.makedirs(target, exist_ok=True)
        res = self.svc.restore_backup(bad_zip, target_dir=target)
        self.assertTrue(res["restored"])
        # 正常文件被恢复
        self.assertTrue(os.path.isfile(os.path.join(target, "data", "ok.txt")))
        # 穿越文件未写到 target 内，也没写到 target 的父目录
        self.assertFalse(os.path.exists(os.path.join(target, "evil_danger.txt")))
        self.assertFalse(os.path.exists(
            os.path.join(os.path.dirname(target), "evil_danger.txt")))

    # ------------------------------------------------------------------ #
    # 保留策略
    # ------------------------------------------------------------------ #
    def test_apply_retention_policy(self) -> None:
        """超过 max_backups 的旧备份应被清理。"""
        # 手工放 8 个不同名的合法空 zip（list_backups 按文件名倒序）
        for i in range(1, 9):
            name = f"backup_20260101_00000{i}.zip"
            with zipfile.ZipFile(os.path.join(self.svc.backups_dir, name), "w"):
                pass
        result = self.svc.apply_retention_policy(max_backups=3, max_age_days=30)
        self.assertEqual(result["kept"], 3)
        self.assertEqual(len(result["deleted"]), 5)
        self.assertEqual(len(self.svc.list_backups()), 3)

    # ------------------------------------------------------------------ #
    # 导出 / 导入
    # ------------------------------------------------------------------ #
    def test_export_data_requires_db(self) -> None:
        self.assertIn("error", self.svc.export_data(db=None))

    def test_export_import_roundtrip(self) -> None:
        """导出 JSON 后再导入新库，persons 数据一致。"""
        # 源库
        src = Database(os.path.join(self.tmpdir, "src.db"))
        src.init_tables()
        src.execute("INSERT INTO persons (id, nickname) VALUES ('p1', 'Alice')")

        exp = self.svc.export_data(db=src)
        self.assertNotIn("error", exp)
        self.assertTrue(os.path.isfile(exp["path"]))
        self.assertEqual(exp["table_counts"].get("persons"), 1)
        src.engine.dispose()

        # 新库导入
        dst = Database(os.path.join(self.tmpdir, "dst.db"))
        dst.init_tables()
        imp = self.svc.import_data(exp["path"], db=dst)
        self.assertTrue(imp["imported"])
        row = dst.fetch_one("SELECT nickname FROM persons WHERE id='p1'")
        self.assertIsNotNone(row)
        self.assertEqual(row["nickname"], "Alice")
        dst.engine.dispose()

    def test_import_data_not_exists(self) -> None:
        dst = Database(os.path.join(self.tmpdir, "dst2.db"))
        dst.init_tables()
        r = self.svc.import_data(os.path.join(self.tmpdir, "nope.json"), db=dst)
        self.assertIn("error", r)
        dst.engine.dispose()

    # ------------------------------------------------------------------ #
    # 统计 / 自动备份判断
    # ------------------------------------------------------------------ #
    def test_get_backup_stats(self) -> None:
        self.svc.create_backup()
        stats = self.svc.get_backup_stats()
        self.assertGreaterEqual(stats["total_backups"], 1)
        self.assertIsNotNone(stats["latest_backup"])
        self.assertIn("backups_dir", stats)

    def test_should_auto_backup_no_backup(self) -> None:
        """全新目录无任何备份时应返回 True。"""
        fresh_root = os.path.join(self.tmpdir, "fresh")
        os.makedirs(fresh_root, exist_ok=True)
        fresh_svc = BackupService(project_root=fresh_root)
        self.assertTrue(fresh_svc.should_auto_backup())

    def test_get_backup_stats_empty(self) -> None:
        fresh_root = os.path.join(self.tmpdir, "fresh2")
        os.makedirs(fresh_root, exist_ok=True)
        fresh_svc = BackupService(project_root=fresh_root)
        stats = fresh_svc.get_backup_stats()
        self.assertEqual(stats["total_backups"], 0)
        self.assertIsNone(stats["latest_backup"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
