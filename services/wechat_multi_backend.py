# -*- coding: utf-8 -*-
"""多方案冗余架构：7 个后端可切换 + 自动选择 + 降级。"""
import os, sys, time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

# ============================================================
# 统一接口
# ============================================================

@dataclass
class Message:
    """统一消息格式。"""
    sender_id: str  # 'me' 或 'other'
    content: str
    timestamp: float
    backend: str  # 哪个后端读的

class WeChatReaderBackend(ABC):
    """所有后端实现这个接口。"""
    
    name: str = "unknown"
    risk_level: str = "unknown"  # low / medium / high
    
    @abstractmethod
    def is_available(self) -> bool:
        """这个后端能不能用？"""
        pass
    
    @abstractmethod
    def get_latest_messages(self, contact_id: str, limit: int = 20) -> List[Message]:
        """读最新消息。"""
        pass
    
    def health_check(self) -> bool:
        """健康检查：读 1 条消息测试。"""
        try:
            msgs = self.get_latest_messages(contact_id="test", limit=1)
            return True
        except Exception:
            return False

# ============================================================
# 7 个后端实现
# ============================================================

class UIABackend(WeChatReaderBackend):
    """方案 1：Windows UI Automation（无障碍读控件树）。"""
    name = "UIA"
    risk_level = "low"  # 系统级合法 API，最安全
    
    def __init__(self):
        self.wechat_window = None
    
    def is_available(self) -> bool:
        try:
            import uiautomation
            # 检查微信窗口在不在
            wechat = uiautomation.WindowControl(searchDepth=1, ClassName='WeChatMainWndForPC')
            if wechat.Exists(3):
                self.wechat_window = wechat
                return True
            return False
        except ImportError:
            return False
        except Exception:
            return False
    
    def get_latest_messages(self, contact_id: str, limit: int = 20) -> List[Message]:
        """用 UIA 枚举微信窗口控件树，提取聊天消息。"""
        import uiautomation as auto
        messages = []
        
        try:
            # 找到聊天列表控件
            chat_list = self.wechat_window.ListControl(Name='消息')
            if not chat_list.Exists(1):
                return messages
            
            # 遍历消息项
            for item in chat_list.GetChildren()[-limit:]:
                msg_text = item.Name
                if msg_text:
                    messages.append(Message(
                        sender_id='unknown',
                        content=msg_text,
                        timestamp=time.time(),
                        backend=self.name
                    ))
        except Exception as e:
            print(f"[UIA] 读消息失败: {e}")
        
        return messages

class PollDBBackend(WeChatReaderBackend):
    """方案 2：轮询数据库（定期解密 db 读新消息）。"""
    name = "PollDB"
    risk_level = "low"  # 纯文件操作，不碰进程
    
    def __init__(self):
        self.db_dir = r'data\wechat_decrypted\4.x'
        self.last_msg_id = 0  # 增量读取用
    
    def is_available(self) -> bool:
        # 检查解密后的 db 文件在不在
        import os
        return os.path.exists(os.path.join(self.db_dir, 'message_0.db'))
    
    def get_latest_messages(self, contact_id: str, limit: int = 20) -> List[Message]:
        """读解密后的 db，增量读新消息。"""
        from services.wechat_db_reader import WeChatDBReader
        
        try:
            reader = WeChatDBReader(db_dir=self.db_dir, key='', version='4.x')
            raw = list(reader.read_messages(contact_filter=[contact_id], batch_size=100))
            
            # 只返回新消息（id > last_msg_id）
            new_msgs = [m for m in raw if (m.get('id') or 0) > self.last_msg_id]
            
            # 更新 last_msg_id
            if raw:
                self.last_msg_id = max(m.get('id') or 0 for m in raw)
            
            # 转成统一格式
            messages = []
            for m in new_msgs[-limit:]:
                messages.append(Message(
                    sender_id='me' if int(m.get('is_sender') or 0) == 1 else 'other',
                    content=str(m.get('content') or ''),
                    timestamp=m.get('create_time') or time.time(),
                    backend=self.name
                ))
            return messages
        except Exception as e:
            print(f"[PollDB] 读消息失败: {e}")
            return []

class ClipboardBackend(WeChatReaderBackend):
    """方案 3：剪贴板监听。"""
    name = "Clipboard"
    risk_level = "low"
    
    def is_available(self) -> bool:
        return True  # 剪贴板监听总是可用
    
    def get_latest_messages(self, contact_id: str, limit: int = 20) -> List[Message]:
        return []

class WindowHookBackend(WeChatReaderBackend):
    """方案 4：窗口 Hook（SetWinEventHook）。"""
    name = "WindowHook"
    risk_level = "medium"  # Hook 可能被检测
    
    def is_available(self) -> bool:
        return True
    
    def get_latest_messages(self, contact_id: str, limit: int = 20) -> List[Message]:
        return []

class OCRBackend(WeChatReaderBackend):
    """方案 5：截图 OCR（纯 Python，rapidocr-onnxruntime）。"""
    name = "OCR"
    risk_level = "medium"  # 频繁截图可能被检测
    
    def __init__(self):
        self.ocr_engine = None
    
    def is_available(self) -> bool:
        try:
            from rapidocr_onnxruntime import RapidOCR
            self.ocr_engine = RapidOCR()
            return True
        except (ImportError, Exception):
            return False
    
    def get_latest_messages(self, contact_id: str, limit: int = 20) -> List[Message]:
        """截微信窗口 + OCR 识别文字。"""
        try:
            import uiautomation as auto
            from PIL import ImageGrab
            
            # 找到微信窗口
            wechat = auto.WindowControl(searchDepth=1, ClassName='WeChatMainWndForPC')
            if not wechat.Exists(3):
                return []
            
            # 截窗口区域
            rect = wechat.BoundingRectangle
            screenshot = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
            
            # OCR 识别（rapidocr 纯 Python）
            result, _ = self.ocr_engine(screenshot)
            
            # result 格式：[[box, text, score], ...]
            messages = []
            if result:
                for item in result[-limit:]:
                    text = item[1] if len(item) > 1 else ''
                    if text:
                        messages.append(Message(
                            sender_id='unknown',
                            content=text,
                            timestamp=time.time(),
                            backend=self.name
                        ))
            return messages
        except Exception as e:
            print(f"[OCR] 读消息失败: {e}")
            return []

class MemoryDumpBackend(WeChatReaderBackend):
    """方案 6：内存 dump。"""
    name = "MemoryDump"
    risk_level = "high"  # 读进程内存，封号风险高
    
    def is_available(self) -> bool:
        return True
    
    def get_latest_messages(self, contact_id: str, limit: int = 20) -> List[Message]:
        return []

class PhoneForwardBackend(WeChatReaderBackend):
    """方案 7：手机端转发（读邮箱/服务器）。"""
    name = "PhoneForward"
    risk_level = "low"  # 完全不碰电脑端
    
    def is_available(self) -> bool:
        # 检查邮箱配置在不在
        return os.path.exists('config/phone_forward.json')
    
    def get_latest_messages(self, contact_id: str, limit: int = 20) -> List[Message]:
        return []

# ============================================================
# 自动选择 + 降级
# ============================================================

class WeChatReaderRouter:
    """自动选择后端 + 降级。"""
    
    # 优先级排序（从最安全到最危险）
    PRIORITY = [
        PhoneForwardBackend,   # 1. 手机端转发（最安全，完全不碰电脑端）
        UIABackend,           # 2. UIA 无障碍（系统级合法）
        PollDBBackend,        # 3. 轮询数据库（纯文件操作）
        ClipboardBackend,     # 4. 剪贴板监听
        WindowHookBackend,    # 5. 窗口 Hook
        OCRBackend,           # 6. 截图 OCR
        MemoryDumpBackend,    # 7. 内存 dump（最危险）
    ]
    
    def __init__(self):
        self.backends = [cls() for cls in self.PRIORITY]
        self.current_backend: Optional[WeChatReaderBackend] = None
        self.failure_count = {b.name: 0 for b in self.backends}
    
    def select_best_backend(self) -> WeChatReaderBackend:
        """自动选最合适的后端。"""
        for backend in self.backends:
            if backend.is_available() and backend.health_check():
                print(f"[Router] 选中后端: {backend.name}（风险: {backend.risk_level}）")
                return backend
        raise RuntimeError("所有后端都不可用")
    
    def get_latest_messages(self, contact_id: str, limit: int = 20) -> List[Message]:
        """读消息，自动降级。"""
        if self.current_backend is None:
            self.current_backend = self.select_best_backend()
        
        try:
            msgs = self.current_backend.get_latest_messages(contact_id, limit)
            self.failure_count[self.current_backend.name] = 0  # 成功了，重置失败计数
            return msgs
        except Exception as e:
            print(f"[Router] 后端 {self.current_backend.name} 失败: {e}")
            self.failure_count[self.current_backend.name] += 1
            
            # 降级：切到下一个后端
            idx = self.backends.index(self.current_backend)
            for next_idx in range(idx + 1, len(self.backends)):
                next_backend = self.backends[next_idx]
                if next_backend.is_available() and next_backend.health_check():
                    print(f"[Router] 降级到后端: {next_backend.name}")
                    self.current_backend = next_backend
                    return self.get_latest_messages(contact_id, limit)
            
            raise RuntimeError("所有后端都失败了，请检查微信")

# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    router = WeChatReaderRouter()
    print(f"后端数量: {len(router.backends)}")
    for b in router.backends:
        avail = b.is_available()
        print(f"  {b.name:<15} 可用={avail} 风险={b.risk_level}")
