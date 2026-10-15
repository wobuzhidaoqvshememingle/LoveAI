"""
微信3.x适配器 - 使用pywechat127 UIAutomation

适用于微信3.x版本，UI使用标准Windows控件，UIAutomation可正常工作。
"""
import logging
import time
from typing import Any, Dict, List, Optional

from .base import BaseWeChatAdapter

logger = logging.getLogger(__name__)

# 尝试导入pywechat127
_PYWECHAT_AVAILABLE = False
try:
    from pyweixin import Navigator, Monitor, Messages, Tools  # type: ignore
    _PYWECHAT_AVAILABLE = True
except ImportError:
    try:
        import pywechat127 as _pw  # type: ignore
        Navigator = getattr(_pw, "Navigator", None)
        Monitor = getattr(_pw, "Monitor", None)
        Messages = getattr(_pw, "Messages", None)
        Tools = getattr(_pw, "Tools", None)
        _PYWECHAT_AVAILABLE = True
    except ImportError:
        _PYWECHAT_AVAILABLE = False


class WeChatAdapter3x(BaseWeChatAdapter):
    """微信3.x适配器，基于pywechat127的UIAutomation。"""

    VERSION_RANGE = "3.x"
    NAME = "pywechat127-uia"

    def __init__(self, signals=None, config=None):
        super().__init__(signals, config)
        self._navigator = None
        self._monitor = None
        self._messages = None
        self._tools = None

    def detect_version(self) -> Optional[str]:
        """检测微信版本。"""
        if not _PYWECHAT_AVAILABLE:
            return None
        try:
            info = Tools.about_weixin()
            return info.get("version") if isinstance(info, dict) else str(info)
        except Exception as e:
            logger.debug(f"3x适配器检测版本失败: {e}")
            return None

    def connect(self) -> bool:
        """连接微信。"""
        if not _PYWECHAT_AVAILABLE:
            return False
        try:
            self._navigator = Navigator()
            self._monitor = Monitor()
            self._messages = Messages()
            self._tools = Tools()
            # 测试是否能获取会话列表
            sessions = self._messages.dump_recent_sessions()
            if sessions is not None:
                self._connected = True
                return True
            return False
        except Exception as e:
            logger.warning(f"3x适配器连接失败: {e}")
            return False

    def get_current_chat_person(self) -> str:
        """获取当前聊天对象。"""
        if not self._connected:
            return ""
        try:
            person = self._navigator.get_current_chat_name()
            if person:
                self._current_person = person
            return person or self._current_person
        except Exception as e:
            logger.debug(f"3x获取当前对象失败: {e}")
            return self._current_person

    def get_new_messages(self) -> List[Dict[str, Any]]:
        """获取新消息。"""
        if not self._connected:
            return []
        try:
            msgs = self._messages.pull_messages(count=20)
            result = []
            for m in msgs or []:
                result.append({
                    "direction": "received" if m.get("is_sender", False) is False else "sent",
                    "content": m.get("content", ""),
                    "timestamp": m.get("create_time", time.time()),
                    "msg_type": m.get("msg_type", 1),
                })
            return result
        except Exception as e:
            logger.debug(f"3x获取消息失败: {e}")
            return []

    def send_message(self, text: str) -> bool:
        """发送消息。"""
        if not self._connected:
            return False
        try:
            self._messages.send_text(text)
            return True
        except Exception as e:
            logger.warning(f"3x发送消息失败: {e}")
            return False
