# -*- coding: utf-8 -*-
"""统一接入管理器：5种微信接入方式的注册/优先级/去重/合并。"""
from __future__ import annotations
import hashlib, logging

logger = logging.getLogger(__name__)

DEFAULT_PRIORITY = ["db_decrypt", "vision", "memory_dump", "cache", "config", "mobile_backup", "mobile_root"]


class WechatAccessManager:
    def __init__(self) -> None:
        self._methods: dict[str, dict] = {}
        self._priority: list[str] = list(DEFAULT_PRIORITY)
        self._seen: set[str] = set()

    def register_method(self, name: str, instance) -> None:
        self._methods[name] = {"instance": instance, "enabled": False, "available": True}

    def enable_method(self, name: str) -> bool:
        if name in self._methods:
            self._methods[name]["enabled"] = True
            return True
        return False

    def disable_method(self, name: str) -> None:
        if name in self._methods:
            self._methods[name]["enabled"] = False

    def set_priority(self, priority: list[str]) -> None:
        self._priority = priority

    def get_available_methods(self) -> list[str]:
        return [n for n in self._priority if n in self._methods and self._methods[n]["enabled"]]

    def _msg_hash(self, msg: dict) -> str:
        return hashlib.md5(str(msg).encode("utf-8")).hexdigest()

    def deduplicate_messages(self, messages: list) -> list:
        out = []
        for m in messages:
            k = self._msg_hash(m)
            if k in self._seen: continue
            self._seen.add(k)
            out.append(m)
        return out

    def collect_messages(self) -> list:
        all_msgs = []
        for name in self.get_available_methods():
            try:
                inst = self._methods[name]["instance"]
                msgs = getattr(inst, "read_messages", lambda: [])() or []
                all_msgs.extend(msgs)
            except Exception as e:
                logger.debug("collect %s skip: %s", name, e)
        return self.deduplicate_messages(all_msgs)

    def merge_messages(self, person_id: str, messages: list) -> dict:
        return {"person_id": person_id, "merged": len(messages)}

    def get_status(self) -> dict:
        return {n: {"enabled": v["enabled"], "available": v["available"]}
                for n, v in self._methods.items()}


# ===== 40种方法目录 =====
METHODS_40 = [
    # A类 10种
    {"id":"A01_self_decrypt","cat":"A","name":"自写DB解密"},
    {"id":"A02_memory_dump","cat":"A","name":"内存转储"},
    {"id":"A03_backup_parse","cat":"A","name":"官方备份"},
    {"id":"A04_copy_offline","cat":"A","name":"复制离线解密"},
    {"id":"A05_wal","cat":"A","name":"WAL文件"},
    {"id":"A06_temp","cat":"A","name":"临时文件"},
    {"id":"A07_crash_dump","cat":"A","name":"崩溃dump"},
    {"id":"A08_registry","cat":"A","name":"注册表"},
    {"id":"A09_dll_export","cat":"A","name":"DLL导出"},
    {"id":"A10_env","cat":"A","name":"环境变量"},
    # B类 7种
    {"id":"B01_screenshot_ocr","cat":"B","name":"截图OCR"},
    {"id":"B02_screen_record","cat":"B","name":"屏幕录制"},
    {"id":"B03_ui_auto","cat":"B","name":"UI Automation"},
    {"id":"B04_narrator","cat":"B","name":"Narrator"},
    {"id":"B05_notification","cat":"B","name":"通知栏"},
    {"id":"B06_cef_debug","cat":"B","name":"CEF远程调试"},
    {"id":"B07_magnifier","cat":"B","name":"放大镜OCR"},
    # C类 7种
    {"id":"C01_image","cat":"C","name":"图片缓存"},
    {"id":"C02_video","cat":"C","name":"视频缓存"},
    {"id":"C03_voice","cat":"C","name":"语音转文字"},
    {"id":"C04_file","cat":"C","name":"文件缓存"},
    {"id":"C05_emoticon","cat":"C","name":"头像表情"},
    {"id":"C06_mini_app","cat":"C","name":"小程序存储"},
    {"id":"C07_mp","cat":"C","name":"公众号缓存"},
    # D类 6种
    {"id":"D01_clip_history","cat":"D","name":"剪贴板历史"},
    {"id":"D02_ime_dict","cat":"D","name":"输入法词库"},
    {"id":"D03_dns","cat":"D","name":"DNS缓存"},
    {"id":"D04_net_meta","cat":"D","name":"网络元数据"},
    {"id":"D05_file_helper","cat":"D","name":"文件传输助手"},
    {"id":"D06_favorites","cat":"D","name":"收藏"},
    # E类 5种
    {"id":"E01_backup","cat":"E","name":"手机备份"},
    {"id":"E02_root","cat":"E","name":"手机root"},
    {"id":"E03_xposed","cat":"E","name":"Xposed"},
    {"id":"E04_screencast","cat":"E","name":"投屏OCR"},
    {"id":"E05_notify_sync","cat":"E","name":"通知同步"},
    # F类 5种
    {"id":"F01_hook","cat":"F","name":"API Hook(只读)"},
    {"id":"F02_debugger","cat":"F","name":"调试器附加"},
    {"id":"F03_proxy","cat":"F","name":"代理MITM"},
    {"id":"F04_sandbox","cat":"F","name":"沙箱监控"},
    {"id":"F05_multi","cat":"F","name":"多开共享"},
]


def get_all_methods() -> list:
    return METHODS_40


def get_methods_by_category(cat: str) -> list:
    return [m for m in METHODS_40 if m["cat"] == cat]


# ===== 一键注册40种方法到 auto_importer =====
def register_all_methods(auto_importer, services: dict) -> int:
    """根据 METHODS_40 目录，把 services 中已有实例按类别注册到 auto_importer。"""
    n = 0
    try:
        # A 类（数据库）
        db_dec = services.get("wechat_db_decryptor")
        if db_dec:
            auto_importer.register_method("A01_self_decrypt", db_dec, "A", 1); n += 1
        mem = services.get("wechat_memory_dumper")
        if mem:
            auto_importer.register_method("A02_memory_dump", mem, "A", 2); n += 1
        # B 类（视觉）
        vis = services.get("wechat_vision_monitor")
        if vis:
            auto_importer.register_method("B01_screenshot_ocr", vis, "B", 1); n += 1
        # C 类（缓存）
        cache = services.get("wechat_cache_reader")
        if cache:
            auto_importer.register_method("C01_image", cache, "C", 1); n += 1
        # D 类（系统）
        sys_r = services.get("wechat_system_reader")
        if sys_r:
            auto_importer.register_method("D01_clip_history", sys_r, "D", 1); n += 1
        # E 类（手机）
        mob = services.get("wechat_mobile_reader")
        if mob:
            auto_importer.register_method("E01_backup", mob, "E", 1); n += 1
        # F 类（高级，默认禁用）
        adv = services.get("wechat_advanced_reader")
        if adv:
            auto_importer.register_method("F01_hook", adv, "F", 1); n += 1
    except Exception as e:
        logger.debug("register_all_methods skip: %s", e)
    return n
