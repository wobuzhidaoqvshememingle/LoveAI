# -*- coding: utf-8 -*-
"""微信PC端配置/日志分析（只读）。"""
from __future__ import annotations
import os, glob, logging

logger = logging.getLogger(__name__)


class WechatConfigParser:
    def __init__(self, root: str = None) -> None:
        self.root = root or os.path.expanduser("~\\Documents\\WeChat Files")

    def find_config_files(self) -> list[str]:
        try:
            return glob.glob(os.path.join(self.root, "**", "*.ini"), recursive=True)
        except Exception:
            return []

    def parse_config(self, config_path: str) -> dict:
        try:
            if not os.path.exists(config_path): return {}
            with open(config_path, "r", encoding="utf-8", errors="ignore") as f:
                return {"path": config_path, "content": f.read()[:500]}
        except Exception:
            return {}

    def find_log_files(self) -> list[str]:
        try:
            return glob.glob(os.path.join(self.root, "**", "*.log"), recursive=True)
        except Exception:
            return []

    def parse_log(self, log_path: str) -> dict:
        return {"path": log_path}

    def get_contact_list(self) -> list:
        return []

    def get_user_info(self) -> dict:
        return {}


# ===== 账号信息获取（pywxdump 借鉴，代码自写） =====
def get_account_info() -> dict:
    """从配置/注册表/内存获取账号信息。"""
    info = {"nickname": "", "wxid": "", "phone": "", "email": "", "avatar_path": ""}
    try:
        import os, glob
        for root in (r"E:\xwechat_files", os.path.expanduser(r"~\Documents\WeChat Files")):
            if not os.path.isdir(root): continue
            wxids = [d for d in os.listdir(root) if d.startswith("wxid_")]
            if wxids:
                info["wxid"] = wxids[0]
                break
    except Exception:
        pass
    return info


# 头像搜索时跳过的「大附件/缓存」目录名（这些目录可达几十 GB、几十万文件，
# 且不可能包含 Profile 头像；不剪枝会让递归遍历近乎卡死）
_AVATAR_SKIP_DIRS = {
    "FileStorage", "MsgAttach", "File", "Video", "Cache", "Backup", "Temp",
    "Attachment", "FileStorageBackup", "crash", "radium", "Storage",
}
# 安全阀：单个 wxid 目录最多遍历的子目录数与最大深度（Profile 通常在 2-4 层）
_AVATAR_MAX_DIRS = 1500
_AVATAR_MAX_DEPTH = 5


def _find_avatar_in_wxid(wxid_dir: str) -> str:
    """在单个 wxid 数据目录内受限搜索 Profile/*.jpg，找到即返回。

    用 os.walk 顶向下遍历，剪枝大附件目录，配合目录数/深度安全阀，
    避免在几十 GB 的消息附件树里无限递归。
    """
    import os
    base_depth = wxid_dir.rstrip(os.sep).count(os.sep)
    seen_dirs = 0
    for cur_dir, dirnames, filenames in os.walk(wxid_dir, topdown=True):
        # 剪枝：原地移除大附件/缓存目录，阻止 os.walk 下钻
        dirnames[:] = [d for d in dirnames if d not in _AVATAR_SKIP_DIRS]
        seen_dirs += 1
        depth = cur_dir.rstrip(os.sep).count(os.sep) - base_depth
        # 安全阀：超深度或遍历目录过多即停止，不再向下
        if depth >= _AVATAR_MAX_DEPTH or seen_dirs >= _AVATAR_MAX_DIRS:
            dirnames[:] = []
            continue
        # 命中 Profile 目录：取其中第一张 jpg（头像），立即返回
        if os.path.basename(cur_dir) == "Profile":
            for fn in filenames:
                if fn.lower().endswith((".jpg", ".jpeg")):
                    return os.path.join(cur_dir, fn)
    return ""


def get_avatar() -> str:
    """获取当前账号头像路径（受限搜索，不扫整棵附件树）。"""
    try:
        import os
        for root in (r"E:\xwechat_files", os.path.expanduser(r"~\Documents\WeChat Files")):
            if not os.path.isdir(root):
                continue
            # 先定位 wxid 目录，把搜索范围限制在账号目录内
            wxid_dirs = [os.path.join(root, d) for d in os.listdir(root)
                         if d.startswith("wxid_") and os.path.isdir(os.path.join(root, d))]
            # 4.x 也可能直接以手机号/别的命名，兜底扫 root 下一层目录
            if not wxid_dirs:
                wxid_dirs = [os.path.join(root, d) for d in os.listdir(root)
                             if os.path.isdir(os.path.join(root, d))]
            for wxid_dir in wxid_dirs:
                hit = _find_avatar_in_wxid(wxid_dir)
                if hit:
                    return hit
    except Exception:
        pass
    return ""


def get_all_accounts() -> list:
    try:
        import os
        out = []
        for root in (r"E:\xwechat_files", os.path.expanduser(r"~\Documents\WeChat Files")):
            if not os.path.isdir(root): continue
            for d in os.listdir(root):
                if d.startswith("wxid_"):
                    out.append({"wxid": d, "path": os.path.join(root, d)})
        return out
    except Exception:
        return []
