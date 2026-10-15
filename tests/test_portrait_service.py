# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - PortraitService 单元测试

覆盖：
  - 初始化不崩溃 / _scan_frames 返回列表（male>0, female>0）
  - list_available_frames male/female 分组
  - match_portrait_frame male 帧 / 未知情绪回退 neutral
  - upload_photo / get_photos / set_profile_photo
  - extract_features PIL 不可用返回 available:False
  - generate_portrait_from_photo 不崩溃
  - delete_photo / person_photos 表自动创建
"""
import os
import sys
import shutil
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.portrait_service import PortraitService  # noqa: E402


def _touch(path: str) -> None:
    """写一个带 .png 后缀的占位文件（不要求是真图）。"""
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 32)


class TestPortraitService(unittest.TestCase):
    """PortraitService 测试类（临时 SQLite + 临时 assets 目录）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_test_portrait_")
        self.assets_dir = os.path.join(self.tmpdir, "assets")
        os.makedirs(os.path.join(self.assets_dir, "frames_male"))
        os.makedirs(os.path.join(self.assets_dir, "frames_female"))
        # 占位立绘帧
        _touch(os.path.join(self.assets_dir, "frames_male", "imm_idle.png"))
        _touch(os.path.join(self.assets_dir, "frames_male", "imm_chibi_happy.png"))
        _touch(os.path.join(self.assets_dir, "frames_male", "imm_chibi_angry.png"))
        _touch(os.path.join(self.assets_dir, "frames_female", "f01_stand.png"))
        _touch(os.path.join(self.assets_dir, "frames_female", "f02_left.png"))

        self.db_path = os.path.join(self.tmpdir, "test_portrait.db")
        self.db = Database(self.db_path)
        self.db.init_tables()
        # 预置人物
        self.db.execute(
            "INSERT INTO persons (id, nickname, gender) VALUES (:id,:n,:g)",
            {"id": "p_female", "n": "苏晚", "g": "女"},
        )
        self.db.execute(
            "INSERT INTO persons (id, nickname, gender) VALUES (:id,:n,:g)",
            {"id": "p_male", "n": "阿强", "g": "male"},
        )
        self.svc = PortraitService(self.db, assets_dir=self.assets_dir)
        # 测试用源照片
        self.src_photo = os.path.join(self.tmpdir, "src.png")
        _touch(self.src_photo)

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 初始化 / 帧扫描
    # ------------------------------------------------------------------ #
    def test_init_no_crash(self) -> None:
        """初始化后 male/female 帧列表为列表类型。"""
        self.assertIsInstance(self.svc.male_frames, list)
        self.assertIsInstance(self.svc.female_frames, list)

    def test_scan_frames_returns_lists(self) -> None:
        """male 与 female 帧数量都应 > 0。"""
        self.assertGreater(len(self.svc.male_frames), 0)
        self.assertGreater(len(self.svc.female_frames), 0)

    def test_person_photos_table_created(self) -> None:
        """person_photos 表应自动创建可查询。"""
        rows = self.db.fetch_all("SELECT * FROM person_photos")
        self.assertIsInstance(rows, list)

    def test_list_available_frames_groups(self) -> None:
        """list_available_frames 应返回 male/female 两组。"""
        data = self.svc.list_available_frames()
        self.assertIn("male", data)
        self.assertIn("female", data)

    # ------------------------------------------------------------------ #
    # 立绘匹配
    # ------------------------------------------------------------------ #
    def test_match_frame_male(self) -> None:
        """male 人物应匹配到 frames_male 下的帧。"""
        path = self.svc.match_portrait_frame("p_male", "neutral")
        self.assertIsNotNone(path)
        self.assertIn("frames_male", path.replace("\\", "/"))

    def test_match_frame_female(self) -> None:
        """female 人物应匹配到 frames_female 下的帧。"""
        path = self.svc.match_portrait_frame("p_female", "neutral")
        self.assertIsNotNone(path)
        self.assertIn("frames_female", path.replace("\\", "/"))

    def test_match_frame_emotion_fallback(self) -> None:
        """未知情绪应回退到 neutral 帧而非报错。"""
        path = self.svc.match_portrait_frame("p_male", "no_such_emotion")
        self.assertIsNotNone(path)
        # neutral 帧即 imm_idle.png
        self.assertIn("imm_idle", path)

    # ------------------------------------------------------------------ #
    # 照片管理
    # ------------------------------------------------------------------ #
    def test_upload_photo(self) -> None:
        """upload_photo 应复制文件并返回 id/path/type。"""
        out = self.svc.upload_photo("p_female", self.src_photo, "profile")
        self.assertIn("id", out)
        self.assertIn("path", out)
        self.assertEqual(out["type"], "profile")
        self.assertTrue(os.path.isfile(out["path"]))

    def test_get_photos(self) -> None:
        """上传后 get_photos 应返回列表。"""
        self.svc.upload_photo("p_female", self.src_photo, "profile")
        photos = self.svc.get_photos("p_female")
        self.assertEqual(len(photos), 1)
        self.assertIn("path", photos[0])

    def test_set_profile_photo(self) -> None:
        """set_profile_photo 应把照片路径写入 persons.portrait_path。"""
        up = self.svc.upload_photo("p_female", self.src_photo, "profile")
        ok = self.svc.set_profile_photo("p_female", up["id"])
        self.assertTrue(ok)
        row = self.db.fetch_one(
            "SELECT portrait_path FROM persons WHERE id=:id", {"id": "p_female"})
        self.assertTrue(len(row["portrait_path"]) > 0)

    def test_delete_photo(self) -> None:
        """delete_photo 应删除记录且文件被移除。"""
        up = self.svc.upload_photo("p_female", self.src_photo, "profile")
        photo_path = up["path"]
        self.assertTrue(os.path.isfile(photo_path))
        ok = self.svc.delete_photo("p_female", up["id"])
        self.assertTrue(ok)
        self.assertEqual(len(self.svc.get_photos("p_female")), 0)

    # ------------------------------------------------------------------ #
    # 特征提取 / 照片转立绘
    # ------------------------------------------------------------------ #
    def test_extract_features_pil_unavailable(self) -> None:
        """Pillow 不可用时 extract_features 返回 available:False。"""
        feat = self.svc.extract_features(self.src_photo)
        self.assertFalse(feat.get("available", True))

    def test_generate_portrait_no_crash(self) -> None:
        """照片→立绘在 PIL 不可用时仍应正常返回且不崩溃。"""
        out = self.svc.generate_portrait_from_photo("p_female", self.src_photo)
        self.assertIn("matched_frame", out)
        self.assertIn("features", out)
        self.assertIn("config_saved", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
