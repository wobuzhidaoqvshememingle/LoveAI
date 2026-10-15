"""
LoveAI v2.0 - 画像服务
人物照片管理、立绘匹配、头像生成、照片→立绘转换。
在没有 AI 生图模型时，采用「规则匹配现有立绘帧 + 特征标签」的降级方案，
不依赖任何外部生图 API；Pillow 不可用时特征提取自动跳过，整体不崩溃。
"""
import os
import json
import time
import shutil
import logging
from datetime import datetime

# Pillow 为可选依赖：不可用时特征提取优雅降级，不影响主流程
try:
    from PIL import Image  # type: ignore
    _PIL_AVAILABLE = True
except Exception:  # pragma: no cover - 环境差异
    Image = None  # type: ignore
    _PIL_AVAILABLE = False

from sqlalchemy import text

logger = logging.getLogger(__name__)

# 项目根目录 = 本文件上两级（LoveAI-v2/services/portrait_service.py -> LoveAI-v2/）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_ASSETS_DIR = os.path.join(_PROJECT_ROOT, "assets")

# 允许的图片扩展名
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

# 文件名 -> 情绪标签 的关键词映射（按命中优先级从上到下）
_EMOTION_KEYWORDS = [
    ("angry", "angry"),
    ("happy", "happy"),
    ("confused", "confused"),
    ("shy", "love"),       # 害羞/暧昧 -> love
    ("sad", "sad"),
    ("cry", "sad"),
    ("cold", "cold"),
    ("excited", "excited"),
]


def _infer_emotion(filename: str) -> str:
    """从文件名推断情绪标签，未命中已知情绪关键词时返回 neutral。

    :param filename: 帧文件名（如 imm_chibi_happy.png）
    :return: 情绪标签字符串
    """
    name = filename.lower()
    for keyword, emotion in _EMOTION_KEYWORDS:
        if keyword in name:
            return emotion
    return "neutral"


def _normalize_gender(gender: str) -> str:
    """把数据库里的性别值规范成 male/female，无法识别时默认 male。

    :param gender: 原始性别字段
    :return: 'male' 或 'female'
    """
    if not gender:
        return "male"
    g = str(gender).strip().lower()
    if g in ("female", "f", "女", "girl", "woman"):
        return "female"
    return "male"


class PortraitService:
    """画像服务：人物照片管理、立绘匹配、头像生成、照片→立绘转换。"""

    def __init__(self, db, assets_dir: str = None):
        """初始化画像服务。

        :param db: Database 实例（models.database.Database）
        :param assets_dir: 资产目录，默认为 项目根/assets/
        """
        self.db = db
        self.assets_dir = assets_dir or _DEFAULT_ASSETS_DIR
        if not os.path.isdir(self.assets_dir):
            logger.warning(f"资产目录不存在: {self.assets_dir}")

        # 扫描立绘帧
        self.male_frames = self._scan_frames("frames_male")
        self.female_frames = self._scan_frames("frames_female")
        logger.info(
            f"立绘帧加载完成: male={len(self.male_frames)}, female={len(self.female_frames)}"
        )

        # 人物照片目录：assets/portraits/
        self.portraits_dir = os.path.join(self.assets_dir, "portraits")
        os.makedirs(self.portraits_dir, exist_ok=True)

        # person_photos 表不存在则自动创建
        self._ensure_photos_table()

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _ensure_photos_table(self) -> None:
        """创建 person_photos 表（若不存在）。"""
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS person_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id TEXT,
                path TEXT,
                type TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )

    def _insert(self, sql: str, params: dict) -> int:
        """执行 INSERT 并返回自增主键 id（使用 session 保证 lastrowid 有效）。

        :param sql: 带 :name 占位符的 INSERT 语句
        :param params: 参数字典
        :return: 新插入行的 id
        """
        session = self.db.get_session()
        try:
            result = session.execute(text(sql), params)
            session.commit()
            return int(result.lastrowid)
        finally:
            session.close()

    def _frames_by_gender(self, gender: str) -> list:
        """按规范化性别返回对应帧列表。

        :param gender: 原始性别字段
        :return: 帧 dict 列表
        """
        return self.female_frames if _normalize_gender(gender) == "female" else self.male_frames

    # ------------------------------------------------------------------ #
    # 立绘帧扫描
    # ------------------------------------------------------------------ #
    def _scan_frames(self, subdir: str) -> list:
        """扫描立绘帧目录，返回帧列表。

        :param subdir: 相对 assets 的子目录名（frames_male / frames_female）
        :return: [{"filename":..., "path":绝对路径, "emotion":..., "gender":...}]，
                 目录不存在时返回空列表
        """
        frames = []
        target_dir = os.path.join(self.assets_dir, subdir)
        if not os.path.isdir(target_dir):
            logger.warning(f"立绘帧目录不存在: {target_dir}")
            return frames

        gender = "female" if "female" in subdir.lower() else "male"
        for fname in sorted(os.listdir(target_dir)):
            ext = os.path.splitext(fname)[1].lower()
            full = os.path.join(target_dir, fname)
            if ext in _IMAGE_EXTS and os.path.isfile(full):
                frames.append({
                    "filename": fname,
                    "path": os.path.abspath(full),
                    "emotion": _infer_emotion(fname),
                    "gender": gender,
                })
        return frames

    # ------------------------------------------------------------------ #
    # 照片管理
    # ------------------------------------------------------------------ #
    def upload_photo(self, person_id: str, photo_path: str,
                     photo_type: str = "profile") -> dict:
        """上传人物照片：复制到 portraits 目录并写入数据库。

        :param person_id: 人物 id
        :param photo_path: 源照片路径（须存在且为图片）
        :param photo_type: 照片类型，默认 profile
        :return: {"id":..., "path":..., "type":...}
        :raises FileNotFoundError: 源文件不存在
        :raises ValueError: 不是支持的图片类型
        """
        if not os.path.isfile(photo_path):
            raise FileNotFoundError(f"照片不存在: {photo_path}")

        ext = os.path.splitext(photo_path)[1].lower()
        if ext not in _IMAGE_EXTS:
            raise ValueError(f"不支持的图片格式: {ext}")

        # 目标路径：portraits/{person_id}/{timestamp}_{photo_type}.{ext}
        person_dir = os.path.join(self.portraits_dir, person_id)
        os.makedirs(person_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dest_name = f"{timestamp}_{photo_type}{ext}"
        dest_path = os.path.join(person_dir, dest_name)
        shutil.copy2(photo_path, dest_path)
        dest_path = os.path.abspath(dest_path)

        photo_id = self._insert(
            "INSERT INTO person_photos (person_id, path, type) "
            "VALUES (:person_id, :path, :type)",
            {"person_id": person_id, "path": dest_path, "type": photo_type},
        )
        logger.info(f"照片上传成功: person={person_id}, id={photo_id}, type={photo_type}")
        return {"id": photo_id, "path": dest_path, "type": photo_type}

    def get_photos(self, person_id: str, photo_type: str = None) -> list:
        """获取人物照片列表（按上传时间倒序）。

        :param person_id: 人物 id
        :param photo_type: 可选，按类型过滤
        :return: 照片 dict 列表
        """
        if photo_type:
            return self.db.fetch_all(
                "SELECT * FROM person_photos WHERE person_id=:pid AND type=:type "
                "ORDER BY created_at DESC",
                {"pid": person_id, "type": photo_type},
            )
        return self.db.fetch_all(
            "SELECT * FROM person_photos WHERE person_id=:pid ORDER BY created_at DESC",
            {"pid": person_id},
        )

    def set_profile_photo(self, person_id: str, photo_id: int) -> bool:
        """设置头像照片：把指定照片路径写入 persons.portrait_path。

        :param person_id: 人物 id
        :param photo_id: person_photos.id
        :return: 是否成功
        """
        row = self.db.fetch_one(
            "SELECT path FROM person_photos WHERE id=:id AND person_id=:pid",
            {"id": photo_id, "pid": person_id},
        )
        if not row or not row.get("path"):
            logger.warning(f"设置头像失败，照片不存在: photo_id={photo_id}")
            return False

        self.db.execute(
            "UPDATE persons SET portrait_path=:path, updated_at=datetime('now') WHERE id=:pid",
            {"path": row["path"], "pid": person_id},
        )
        logger.info(f"头像设置成功: person={person_id}, photo_id={photo_id}")
        return True

    def delete_photo(self, person_id: str, photo_id: int) -> bool:
        """删除照片：同时删除文件与数据库记录。

        :param person_id: 人物 id
        :param photo_id: person_photos.id
        :return: 是否成功
        """
        row = self.db.fetch_one(
            "SELECT path FROM person_photos WHERE id=:id AND person_id=:pid",
            {"id": photo_id, "pid": person_id},
        )
        if not row:
            logger.warning(f"删除照片失败，记录不存在: photo_id={photo_id}")
            return False

        # 删除文件（文件不存在不视为失败）
        path = row.get("path")
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError as e:
                logger.warning(f"照片文件删除失败: {path}, {e}")

        self.db.execute(
            "DELETE FROM person_photos WHERE id=:id AND person_id=:pid",
            {"id": photo_id, "pid": person_id},
        )
        logger.info(f"照片已删除: photo_id={photo_id}")
        return True

    # ------------------------------------------------------------------ #
    # 立绘匹配
    # ------------------------------------------------------------------ #
    def match_portrait_frame(self, person_id: str, emotion: str = "neutral"):
        """为人物匹配合适的立绘帧（用于桌宠/头像显示）。

        1. 从 persons 表取 gender；2. 按性别选 male/female 帧；
        3. 优先匹配 emotion 标签；4. 无匹配则退回 neutral 帧或第一帧。

        :param person_id: 人物 id
        :param emotion: 期望情绪，默认 neutral
        :return: 帧文件绝对路径；没有任何帧时返回 None
        """
        person = self.db.fetch_one(
            "SELECT gender FROM persons WHERE id=:id", {"id": person_id}
        )
        gender = person.get("gender") if person else None
        frames = self._frames_by_gender(gender)
        if not frames:
            logger.warning(f"无可匹配立绘帧: gender={gender}")
            return None

        # 优先命中请求情绪
        target = (emotion or "neutral").lower()
        for frame in frames:
            if frame["emotion"] == target:
                return frame["path"]

        # 退回 neutral 帧
        for frame in frames:
            if frame["emotion"] == "neutral":
                return frame["path"]

        # 最后兜底：第一帧
        return frames[0]["path"]

    def list_available_frames(self, gender: str = None) -> dict:
        """列出可用立绘帧（按情绪分组）。

        :param gender: 'male'/'female'；为 None 时返回两组
        :return: {"male": {emotion: [path,...]}, "female": {...}}
        """
        result = {}
        groups = []
        if gender is None or _normalize_gender(gender) == "male":
            groups.append(("male", self.male_frames))
        if gender is None or _normalize_gender(gender) == "female":
            groups.append(("female", self.female_frames))

        for gname, frames in groups:
            bucket = {}
            for frame in frames:
                bucket.setdefault(frame["emotion"], []).append(frame["path"])
            result[gname] = bucket
        return result

    # ------------------------------------------------------------------ #
    # 特征提取 & 照片→立绘
    # ------------------------------------------------------------------ #
    def extract_features(self, photo_path: str) -> dict:
        """从照片提取基本特征（尺寸/主色调/亮度），不做人脸检测。

        :param photo_path: 照片路径
        :return: {"width","height","dominant_color":"#RRGGBB","brightness":0-100,
                  "has_face":None}；Pillow 不可用或读取失败时返回 {"available": False}
        """
        if not _PIL_AVAILABLE or Image is None:
            return {"available": False}

        if not os.path.isfile(photo_path):
            return {"available": False, "error": "file_not_found"}

        try:
            with Image.open(photo_path) as im:
                im = im.convert("RGB")
                width, height = im.size

                # 缩小后量化取主色调，降低计算量
                small = im.resize((50, 50))
                quantized = small.quantize(colors=1)
                palette = quantized.getpalette() or [0, 0, 0, 0, 0, 0]
                idx = quantized.getpixel((0, 0))
                r, g, b = palette[idx * 3], palette[idx * 3 + 1], palette[idx * 3 + 2]

                # 平均亮度（Rec.601  luminance）映射到 0-100
                pixels = list(small.getdata())
                n = len(pixels) or 1
                total = sum(0.299 * pr + 0.587 * pg + 0.114 * pb
                            for pr, pg, pb in pixels)
                brightness = round(total / n / 255 * 100, 1)

                return {
                    "width": width,
                    "height": height,
                    "dominant_color": f"#{r:02X}{g:02X}{b:02X}",
                    "brightness": brightness,
                    "has_face": None,  # 不做人脸检测，留空
                }
        except Exception as e:  # 图片损坏等
            logger.warning(f"特征提取失败: {photo_path}, {e}")
            return {"available": False, "error": str(e)}

    def generate_portrait_from_photo(self, person_id: str, photo_path: str) -> dict:
        """照片→立绘转换（简化版）：无 AI 生图时用特征提取 + 规则匹配立绘帧。

        :param person_id: 人物 id
        :param photo_path: 源照片路径
        :return: {"matched_frame":..., "features":{...}, "config_saved":bool}
        :raises FileNotFoundError: 照片不存在
        """
        if not os.path.isfile(photo_path):
            raise FileNotFoundError(f"照片不存在: {photo_path}")

        # 1. 提取基本特征（PIL 不可用时自动降级）
        features = self.extract_features(photo_path)

        # 2. 按性别匹配最接近的立绘帧（规则方案：匹配 neutral 默认帧）
        matched_frame = self.match_portrait_frame(person_id, "neutral")

        # 3. 生成 portrait_config.json
        person = self.db.fetch_one(
            "SELECT gender FROM persons WHERE id=:id", {"id": person_id}
        )
        gender = _normalize_gender(person.get("gender") if person else None)

        person_dir = os.path.join(self.portraits_dir, person_id)
        os.makedirs(person_dir, exist_ok=True)
        config_path = os.path.join(person_dir, "portrait_config.json")
        config = {
            "person_id": person_id,
            "gender": gender,
            "matched_frame": matched_frame,
            "features": features,
            "emotion_preference": "neutral",
            "source_photo": os.path.abspath(photo_path),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

        config_saved = False
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            config_saved = True
        except OSError as e:
            logger.warning(f"portrait_config.json 保存失败: {config_path}, {e}")

        # 4. 把匹配帧写入 persons.portrait_path
        if matched_frame:
            self.db.execute(
                "UPDATE persons SET portrait_path=:path, updated_at=datetime('now') "
                "WHERE id=:pid",
                {"path": matched_frame, "pid": person_id},
            )

        logger.info(f"照片→立绘完成: person={person_id}, frame={matched_frame}")
        return {
            "matched_frame": matched_frame,
            "features": features,
            "config_saved": config_saved,
        }

    # ------------------------------------------------------------------ #
    # 配置读取
    # ------------------------------------------------------------------ #
    def get_portrait_config(self, person_id: str) -> dict:
        """获取人物立绘配置。

        :param person_id: 人物 id
        :return: {"profile_photo","matched_frame","emotion_frames":{emotion:path},"features":{...}}
        """
        person = self.db.fetch_one(
            "SELECT gender, portrait_path FROM persons WHERE id=:id", {"id": person_id}
        )
        gender = person.get("gender") if person else None
        matched_frame = person.get("portrait_path") if person else None

        # 当前头像（profile 类型最新一张）
        photos = self.get_photos(person_id, "profile")
        profile_photo = photos[0]["path"] if photos else None

        # 按情绪各取一个代表帧
        frames = self._frames_by_gender(gender)
        emotion_frames = {}
        for frame in frames:
            emotion_frames.setdefault(frame["emotion"], frame["path"])

        # 读取已保存的特征配置
        features = {}
        config_path = os.path.join(
            self.portraits_dir, person_id, "portrait_config.json"
        )
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                features = saved.get("features", {})
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"读取 portrait_config.json 失败: {config_path}, {e}")

        return {
            "profile_photo": profile_photo,
            "matched_frame": matched_frame,
            "emotion_frames": emotion_frames,
            "features": features,
        }
