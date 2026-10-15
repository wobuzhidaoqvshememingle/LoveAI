"""
微信多版本UI适配器基类

每个微信版本对应一个适配器模板，负责：
- 检测当前微信版本
- 获取当前聊天对象
- 监听新消息
- 发送消息到输入框

版本适配策略：
- 3.x: UIAutomation标准控件（pywechat127原生支持）
- 4.0: 过渡版本，部分UIA可用
- 4.1+: 完全自绘UI（MMUIRenderSubWindowHW），UIA不可用，改用截图+OCR+坐标
"""
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BaseWeChatAdapter(ABC):
    """微信适配器基类，所有版本适配器必须实现这些方法。"""

    #: 适配的微信版本范围，如 "3.x", "4.0", "4.1+"
    VERSION_RANGE: str = "unknown"

    #: 适配器名称
    NAME: str = "base"

    def __init__(self, signals: Any = None, config: Any = None):
        self.signals = signals
        self.config = config
        self._connected = False
        self._current_person = ""

    @abstractmethod
    def detect_version(self) -> Optional[str]:
        """检测当前微信版本号，返回如 '4.1.15.13'，失败返回None。"""
        pass

    @abstractmethod
    def connect(self) -> bool:
        """连接微信，返回是否成功。"""
        pass

    @abstractmethod
    def get_current_chat_person(self) -> str:
        """获取当前聊天对象的昵称/备注。"""
        pass

    @abstractmethod
    def get_new_messages(self) -> List[Dict[str, Any]]:
        """获取新消息列表，每条消息包含 direction/content/timestamp。"""
        pass

    @abstractmethod
    def send_message(self, text: str) -> bool:
        """发送消息到微信输入框。"""
        pass

    def disconnect(self):
        """断开连接，清理资源。"""
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def current_person(self) -> str:
        return self._current_person
