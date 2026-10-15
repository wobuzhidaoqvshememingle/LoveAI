"""
备份服务：自动备份、数据导出、数据导入、备份管理。

职责：
- 一键备份数据库/配置/记忆/档案到 backups/ 目录（zip压缩）
- 自动定时备份（按配置间隔）
- 数据导出（JSON格式，可迁移）
- 数据导入（从备份恢复）
- 备份列表管理（保留策略：自动清理旧备份）

约束：
- 纯 Python，不继承 QObject，不 import PySide6
- 不使用 pywebview
"""

import os
import sys
import json
import time
import shutil
import zipfile
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class BackupService:
    """备份与恢复服务。"""

    def __init__(self, project_root: str = None, config=None):
        """
        Args:
            project_root: 项目根目录（默认由 __file__ 推导）
            config: ConfigService 实例（可选，读取备份配置）
        """
        if project_root:
            self.project_root = project_root
        else:
            self.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.config = config
        self.backups_dir = os.path.join(self.project_root, "backups", "data_backups")
        self.exports_dir = os.path.join(self.project_root, "backups", "exports")
        os.makedirs(self.backups_dir, exist_ok=True)
        os.makedirs(self.exports_dir, exist_ok=True)
        self._auto_backup_timer = None

    # ------------------------------------------------------------------
    # 备份
    # ------------------------------------------------------------------
    def create_backup(self, include_assets: bool = False, note: str = "") -> dict:
        """创建完整备份（zip压缩包）。

        Args:
            include_assets: 是否包含 assets 目录（立绘帧，体积较大，默认不含）
            note: 备份备注

        Returns:
            {"backup_id", "filename", "path", "size_mb", "created_at", "contents": [...]}
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_id = f"backup_{timestamp}"
        zip_filename = f"{backup_id}.zip"
        zip_path = os.path.join(self.backups_dir, zip_filename)

        # 需要备份的目录/文件
        items_to_backup = [
            ("data", "data"),                     # 数据库目录
            ("config.json", "config.json"),       # 配置文件
        ]
        if include_assets:
            items_to_backup.append(("assets", "assets"))

        backed_up = []
        manifest = {
            "backup_id": backup_id,
            "created_at": datetime.now().isoformat(),
            "app_version": "2.0.0",
            "note": note,
            "include_assets": include_assets,
            "contents": [],
        }

        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for src_rel, arc_prefix in items_to_backup:
                    src_path = os.path.join(self.project_root, src_rel)
                    if not os.path.exists(src_path):
                        continue
                    if os.path.isfile(src_path):
                        zf.write(src_path, arc_prefix)
                        manifest["contents"].append({"path": arc_prefix, "size": os.path.getsize(src_path)})
                        backed_up.append(src_rel)
                    elif os.path.isdir(src_path):
                        for root, dirs, files in os.walk(src_path):
                            # 排除临时/缓存
                            dirs[:] = [d for d in dirs if d not in ("__pycache__", "screenshots", "tmp")]
                            for f in files:
                                if f.endswith((".db-wal", ".db-shm", ".tmp", ".log")):
                                    continue
                                fpath = os.path.join(root, f)
                                arcname = os.path.join(arc_prefix, os.path.relpath(fpath, src_path))
                                try:
                                    zf.write(fpath, arcname)
                                    manifest["contents"].append({"path": arcname, "size": os.path.getsize(fpath)})
                                except Exception as e:
                                    logger.warning("备份文件跳过 %s: %s", fpath, e)
                        backed_up.append(src_rel)

                # 写入清单
                zf.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))

            size_mb = round(os.path.getsize(zip_path) / (1024 * 1024), 2)
            logger.info("备份完成: %s (%.2f MB), 包含: %s", zip_filename, size_mb, backed_up)
            return {
                "backup_id": backup_id,
                "filename": zip_filename,
                "path": zip_path,
                "size_mb": size_mb,
                "created_at": manifest["created_at"],
                "note": note,
                "contents": backed_up,
            }
        except Exception as e:
            logger.exception("备份失败")
            # 清理不完整的zip
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except Exception:
                    pass
            return {"error": f"备份失败: {e}"}

    def list_backups(self) -> list:
        """列出所有备份（按时间倒序）。

        Returns:
            [{"backup_id", "filename", "path", "size_mb", "created_at", "note"}]
        """
        backups = []
        if not os.path.exists(self.backups_dir):
            return backups
        for f in sorted(os.listdir(self.backups_dir), reverse=True):
            if f.endswith(".zip"):
                fpath = os.path.join(self.backups_dir, f)
                stat = os.stat(fpath)
                # 尝试从zip读取manifest
                note = ""
                created_at = datetime.fromtimestamp(stat.st_mtime).isoformat()
                try:
                    with zipfile.ZipFile(fpath, "r") as zf:
                        if "MANIFEST.json" in zf.namelist():
                            manifest = json.loads(zf.read("MANIFEST.json"))
                            note = manifest.get("note", "")
                            created_at = manifest.get("created_at", created_at)
                except Exception:
                    pass
                backups.append({
                    "backup_id": f.replace(".zip", ""),
                    "filename": f,
                    "path": fpath,
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "created_at": created_at,
                    "note": note,
                })
        return backups

    def restore_backup(self, backup_path: str, target_dir: str = None) -> dict:
        """从备份恢复（解压覆盖）。

        Args:
            backup_path: 备份zip路径（或backup_id）
            target_dir: 恢复目标目录（默认项目根）

        Returns:
            {"restored": True, "restored_items": [...], "path": target_dir}
        """
        # 如果传入的是backup_id，构造完整路径
        if not os.path.exists(backup_path) and not backup_path.endswith(".zip"):
            backup_path = os.path.join(self.backups_dir, f"{backup_path}.zip")

        if not os.path.exists(backup_path):
            return {"error": f"备份文件不存在: {backup_path}"}

        target = target_dir or self.project_root
        restored_items = []

        try:
            # 恢复前先创建一个安全备份（防止恢复出错导致数据丢失）
            safety = self.create_backup(note=f"恢复前自动备份 {os.path.basename(backup_path)}")
            logger.info("恢复前安全备份: %s", safety.get("filename", "失败"))

            with zipfile.ZipFile(backup_path, "r") as zf:
                # 读取manifest
                top_dirs = set()
                for name in zf.namelist():
                    if name == "MANIFEST.json":
                        continue
                    # 安全检查：防止路径穿越
                    norm = os.path.normpath(name)
                    if norm.startswith("..") or os.path.isabs(norm):
                        logger.warning("跳过不安全路径: %s", name)
                        continue
                    zf.extract(name, target)
                    top_dirs.add(norm.split(os.sep)[0])
                restored_items = list(top_dirs)

            logger.info("恢复完成: %s -> %s, 项目: %s", backup_path, target, restored_items)
            return {
                "restored": True,
                "restored_items": restored_items,
                "path": target,
                "pre_restore_backup": safety.get("filename"),
            }
        except Exception as e:
            logger.exception("恢复失败")
            return {"error": f"恢复失败: {e}"}

    def delete_backup(self, backup_id: str) -> dict:
        """删除指定备份。

        Args:
            backup_id: 备份ID（不含.zip）

        Returns:
            {"deleted": True}
        """
        zip_path = os.path.join(self.backups_dir, f"{backup_id}.zip")
        if not os.path.exists(zip_path):
            return {"error": "备份不存在"}
        try:
            os.remove(zip_path)
            logger.info("已删除备份: %s", backup_id)
            return {"deleted": True}
        except Exception as e:
            return {"error": f"删除失败: {e}"}

    def apply_retention_policy(self, max_backups: int = 10, max_age_days: int = 30) -> dict:
        """备份保留策略：自动清理旧备份。

        Args:
            max_backups: 最多保留备份数
            max_age_days: 备份最大保留天数

        Returns:
            {"kept": N, "deleted": [...], "freed_mb": N}
        """
        backups = self.list_backups()
        deleted = []
        freed_bytes = 0
        cutoff = datetime.now() - timedelta(days=max_age_days)

        # 按时间倒序，保留最新的max_backups个
        for idx, b in enumerate(backups):
            should_delete = False
            reason = ""
            if idx >= max_backups:
                should_delete = True
                reason = f"超过最大保留数{max_backups}"
            else:
                try:
                    created = datetime.fromisoformat(b["created_at"])
                    if created < cutoff:
                        should_delete = True
                        reason = f"超过{max_age_days}天"
                except Exception:
                    pass

            if should_delete:
                size = b.get("size_mb", 0)
                result = self.delete_backup(b["backup_id"])
                if result.get("deleted"):
                    deleted.append({"backup_id": b["backup_id"], "reason": reason})
                    freed_bytes += size * 1024 * 1024

        return {
            "kept": len(backups) - len(deleted),
            "deleted": deleted,
            "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        }

    # ------------------------------------------------------------------
    # 数据导出/导入（JSON格式，跨设备迁移）
    # ------------------------------------------------------------------
    def export_data(self, db=None) -> dict:
        """导出全部业务数据为JSON（不含二进制资产）。

        Args:
            db: Database实例

        Returns:
            {"export_id", "path", "size_mb", "tables": {...}}
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_id = f"export_{timestamp}"
        export_filename = f"{export_id}.json"
        export_path = os.path.join(self.exports_dir, export_filename)

        if db is None:
            return {"error": "Database 实例未提供"}

        # 导出的表（业务数据，排除内部sqlite表）
        tables_to_export = [
            "persons", "chat_messages", "tasks", "current_affection",
            "affection_snapshots", "user_medals", "guides", "financial_reports",
            "expense_records", "memos", "long_term_memory", "person_photos",
            "settings",
        ]

        export_data = {
            "export_id": export_id,
            "exported_at": datetime.now().isoformat(),
            "app_version": "2.0.0",
            "format": "loveai-json-v1",
            "tables": {},
        }

        try:
            engine = db.engine
            with engine.connect() as conn:
                from sqlalchemy import text
                for table in tables_to_export:
                    try:
                        rows = conn.execute(text(f"SELECT * FROM {table}")).fetchall()
                        cols = list(rows[0]._mapping.keys()) if rows else []
                        export_data["tables"][table] = {
                            "columns": cols,
                            "rows": [dict(r._mapping) for r in rows],
                        }
                    except Exception as e:
                        logger.debug("导出表 %s 跳过: %s", table, e)
                        export_data["tables"][table] = {"columns": [], "rows": [], "skipped": str(e)}

            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2, default=str)

            size_mb = round(os.path.getsize(export_path) / (1024 * 1024), 2)
            table_counts = {t: len(d.get("rows", [])) for t, d in export_data["tables"].items()}
            logger.info("数据导出完成: %s (%.2f MB), 表: %s", export_filename, size_mb, table_counts)
            return {
                "export_id": export_id,
                "filename": export_filename,
                "path": export_path,
                "size_mb": size_mb,
                "exported_at": export_data["exported_at"],
                "table_counts": table_counts,
            }
        except Exception as e:
            logger.exception("数据导出失败")
            return {"error": f"导出失败: {e}"}

    def import_data(self, import_path: str, db=None, merge: bool = True) -> dict:
        """从JSON导出文件导入数据。

        Args:
            import_path: 导出JSON路径
            db: Database实例
            merge: True=合并（保留现有，插入新），False=清空后导入

        Returns:
            {"imported": True, "tables": {table: count}}
        """
        if not os.path.exists(import_path):
            return {"error": f"导入文件不存在: {import_path}"}
        if db is None:
            return {"error": "Database 实例未提供"}

        try:
            with open(import_path, encoding="utf-8") as f:
                data = json.load(f)

            if data.get("format") != "loveai-json-v1":
                return {"error": "不支持的导出格式"}

            imported_counts = {}
            engine = db.engine
            from sqlalchemy import text

            with engine.begin() as conn:
                for table, tdata in data.get("tables", {}).items():
                    rows = tdata.get("rows", [])
                    if not rows:
                        imported_counts[table] = 0
                        continue
                    cols = tdata.get("columns") or list(rows[0].keys())
                    try:
                        if not merge:
                            conn.execute(text(f"DELETE FROM {table}"))
                        col_list = ", ".join(cols)
                        param_list = ", ".join(f":{c}" for c in cols)
                        sql = text(f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({param_list})")
                        count = 0
                        for row in rows:
                            try:
                                conn.execute(sql, {c: row.get(c) for c in cols})
                                count += 1
                            except Exception:
                                pass
                        imported_counts[table] = count
                    except Exception as e:
                        logger.warning("导入表 %s 失败: %s", table, e)
                        imported_counts[table] = 0

            logger.info("数据导入完成: %s, 数量: %s", import_path, imported_counts)
            return {"imported": True, "tables": imported_counts, "source": import_path}
        except Exception as e:
            logger.exception("数据导入失败")
            return {"error": f"导入失败: {e}"}

    # ------------------------------------------------------------------
    # 自动备份
    # ------------------------------------------------------------------
    def get_backup_stats(self) -> dict:
        """获取备份统计信息。"""
        backups = self.list_backups()
        total_size = sum(b.get("size_mb", 0) for b in backups)
        return {
            "total_backups": len(backups),
            "total_size_mb": round(total_size, 2),
            "latest_backup": backups[0] if backups else None,
            "backups_dir": self.backups_dir,
            "exports_dir": self.exports_dir,
        }

    def should_auto_backup(self, interval_hours: float = 24) -> bool:
        """判断是否应该自动备份（距上次备份超过间隔）。"""
        backups = self.list_backups()
        if not backups:
            return True
        latest = backups[0]
        try:
            latest_time = datetime.fromisoformat(latest["created_at"])
            return datetime.now() - latest_time > timedelta(hours=interval_hours)
        except Exception:
            return True

    def auto_backup_if_needed(self, interval_hours: float = 24) -> Optional[dict]:
        """如果需要则执行自动备份。

        Returns:
            备份结果dict，或None（不需要备份时）
        """
        # 从config读取间隔
        if self.config:
            try:
                interval_hours = float(self.config.get("backup.interval_hours", interval_hours))
            except Exception:
                pass

        if self.should_auto_backup(interval_hours):
            logger.info("执行自动备份（间隔%.0f小时）", interval_hours)
            result = self.create_backup(note="自动备份")
            # 应用保留策略
            if self.config:
                try:
                    max_keep = int(self.config.get("backup.max_backups", 10))
                    self.apply_retention_policy(max_backups=max_keep)
                except Exception:
                    pass
            return result
        return None
