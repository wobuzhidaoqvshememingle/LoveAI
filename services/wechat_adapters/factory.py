"""
微信适配器工厂 - 根据微信版本自动选择对应适配器

版本适配策略：
1. 先检测微信版本
2. 根据版本选择对应的适配器模板
3. 如果特定版本适配器不可用，降级到通用适配器

支持的版本模板：
- 3.x: pywechat127 UIAutomation
- 4.0: 过渡版本（尝试UIA，失败降级OCR）
- 4.1+: 截图+OCR+坐标
"""
import logging
from typing import Any, Optional

from .base import BaseWeChatAdapter
from .adapter_3x import WeChatAdapter3x
from .adapter_41 import WeChatAdapter41

logger = logging.getLogger(__name__)


class WeChatAdapterFactory:
    """微信适配器工厂，根据版本自动选择。"""

    # 版本到适配器的映射
    _ADAPTERS = {
        "3": WeChatAdapter3x,
        "4.0": WeChatAdapter3x,  # 4.0尝试UIA
        "4.1": WeChatAdapter41,
        "4.2": WeChatAdapter41,
        "4.3": WeChatAdapter41,
    }

    @classmethod
    def detect_version(cls) -> Optional[str]:
        """检测当前微信版本号。"""
        # 先尝试3x适配器检测（pywechat127）
        adapter_3x = WeChatAdapter3x()
        version = adapter_3x.detect_version()
        if version:
            return version
        # 再尝试41适配器检测
        adapter_41 = WeChatAdapter41()
        version = adapter_41.detect_version()
        if version:
            return version
        return None

    @classmethod
    def create(cls, version: str = None, signals=None, config=None) -> BaseWeChatAdapter:
        """
        根据版本创建适配器。

        Args:
            version: 微信版本号，如 '4.1.15.13'，为None时自动检测
            signals: 信号对象
            config: 配置对象

        Returns:
            对应的适配器实例
        """
        if version is None:
            version = cls.detect_version()
            logger.info(f"自动检测到微信版本: {version}")

        # 匹配版本
        adapter_cls = None
        if version:
            # 精确匹配主版本.次版本
            for key in ["4.3", "4.2", "4.1", "4.0", "3"]:
                if version.startswith(key):
                    adapter_cls = cls._ADAPTERS.get(key)
                    break

        if adapter_cls is None:
            # 默认使用41适配器（最通用）
            logger.warning(f"未找到版本 {version} 的适配器，使用默认4.1+适配器")
            adapter_cls = WeChatAdapter41

        adapter = adapter_cls(signals=signals, config=config)
        logger.info(f"选择适配器: {adapter.NAME} (版本范围: {adapter.VERSION_RANGE})")
        return adapter

    @classmethod
    def create_and_connect(cls, version: str = None, signals=None, config=None) -> Optional[BaseWeChatAdapter]:
        """创建适配器并尝试连接，失败时自动降级。"""
        adapter = cls.create(version, signals, config)

        # 尝试连接
        if adapter.connect():
            return adapter

        # 连接失败，尝试降级到其他适配器
        logger.warning(f"适配器 {adapter.NAME} 连接失败，尝试降级...")

        # 降级顺序：41 -> 3x
        for fallback_cls in [WeChatAdapter41, WeChatAdapter3x]:
            if not isinstance(adapter, fallback_cls):
                fallback = fallback_cls(signals=signals, config=config)
                if fallback.connect():
                    logger.info(f"降级到适配器: {fallback.NAME}")
                    return fallback

        logger.error("所有适配器都连接失败")
        return None
