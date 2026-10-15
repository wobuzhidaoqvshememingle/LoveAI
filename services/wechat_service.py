"""
LoveAI v2.0 - 微信监听 + 自动输入服务

封装 pywechat127（pure UIA，不注入不 Hook，支持微信 4.1+）。
当 pywechat127 不可用时，优雅降级为 pyperclip + ctypes 键盘模拟（Ctrl+V）。

设计约束：
- 本模块不继承 QObject，不 import PySide6；signals 通过构造参数注入。
- pywechat127 的导入必须 try/except ImportError，未安装时 import 本模块不报错。
- 监听线程为 daemon=True，不阻塞主进程退出。
- 消息去重用集合，上限 500 条，超过后裁剪。
"""
import ctypes
import hashlib
import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 尝试导入 pywechat127（pure UIA）
# pip 包名为 pywechat127，运行时导入名通常为 pyweixin；两种路径都尝试一次。
# 任何导入失败都置 _PYWECHAT_AVAILABLE=False，进入降级模式。
# ---------------------------------------------------------------------------
_PYWECHAT_AVAILABLE = False
_pw_Navigator = None
_pw_Monitor = None
_pw_Messages = None
_pw_Tools = None

try:  # 标准导入路径：from pyweixin import ...
    from pyweixin import Navigator, Monitor, Messages, Tools  # type: ignore
    _pw_Navigator = Navigator
    _pw_Monitor = Monitor
    _pw_Messages = Messages
    _pw_Tools = Tools
    _PYWECHAT_AVAILABLE = True
except ImportError:
    try:  # 备用路径：import pywechat127，按属性取类
        import pywechat127 as _pw  # type: ignore
        _pw_Navigator = getattr(_pw, "Navigator", None)
        _pw_Monitor = getattr(_pw, "Monitor", None)
        _pw_Messages = getattr(_pw, "Messages", None)
        _pw_Tools = getattr(_pw, "Tools", None)
        _PYWECHAT_AVAILABLE = True
    except ImportError:
        _PYWECHAT_AVAILABLE = False


class WeChatService:
    """微信监听 + 自动输入服务。

    正常模式（pywechat127 可用）：自动监听新消息、自动识别聊天对象切换、
        通过 UIA 把文本填入微信输入框。
    降级模式（pywechat127 未安装）：无法自动读取消息，文本填入退化为
        pyperclip 复制 + ctypes 模拟 Ctrl+V；监听线程仍运行但消息列表为空。
    """

    #: 消息去重集合上限
    _MAX_MSG_HASH = 500
    #: 超过上限后保留的条数
    _KEPT_MSG_HASH = 200
    #: WeChat 主窗口类名列表（新版微信用 Qt51514QWindowIcon，旧版用 WeChatMainWndForPC）
    _WECHAT_WND_CLASSES = [
        "WeChatMainWndForPC",   # 微信 3.x 旧版
        "Qt51514QWindowIcon",   # 微信 4.x 新版
        "WeChatMainWnd",        # 其他变体
    ]
    #: 微信窗口标题关键词（类名都找不到时用标题枚举回退）
    _WECHAT_TITLE_KEYWORDS = ["微信", "WeChat"]

    def __init__(self, signals: Any, config: Any) -> None:
        """初始化微信服务。

        Args:
            signals: AppSignals 实例，提供 new_message / person_switched /
                wechat_status 三个信号（通过参数注入，本模块不直接 import PySide6）。
            config: ConfigService 实例，读取 wechat.* 配置项。
        """
        self.signals = signals
        self.config = config

        # 运行态
        self.connected: bool = False
        self.listener_thread: Optional[threading.Thread] = None
        self.running: bool = False
        self.current_person: Optional[str] = None

        # 消息去重集合（元素为消息指纹 hex 串）
        self.last_msg_hash: set = set()

        # 监听间隔：从 config 读取 wechat.monitor_interval_ms（默认 1500ms）
        try:
            interval_ms = self.config.get("wechat.monitor_interval_ms", 1500)
            self.monitor_interval: float = float(interval_ms) / 1000.0
        except (TypeError, ValueError):
            self.monitor_interval = 1.5
        if self.monitor_interval <= 0:
            self.monitor_interval = 1.5

        # pywechat127 是否可用（模块级 try/except 的结果）
        self.pywechat_available: bool = _PYWECHAT_AVAILABLE
        # 运行模式：'normal'（pywechat127完整功能）/ 'degraded'（剪贴板降级）/ 'adapter'（多版本适配器）
        self._mode: str = "normal" if self.pywechat_available else "degraded"

        # 多版本UI适配器（微信4.1+自绘UI用截图+OCR）
        self._adapter = None
        self._wechat_version: Optional[str] = None

        # 可选的窗口检测器（app.wechat_detector.WeChatDetector 可能尚未实现）
        self._detector: Any = None
        try:
            from app.wechat_detector import WeChatDetector  # type: ignore
            self._detector = WeChatDetector()
        except Exception:
            # app 包尚未就绪时静默跳过，get_window_info 会回退到 ctypes
            self._detector = None

        logger.info(
            "WeChatService 初始化完成 | pywechat_available=%s | 监听间隔=%.2fs",
            self.pywechat_available, self.monitor_interval,
        )

    # ------------------------------------------------------------------ #
    # 连接
    # ------------------------------------------------------------------ #
    def connect(self) -> bool:
        """连接微信并检测版本。

        - pywechat127 可用且 Tools.about_weixin() 成功：normal 模式，完整功能。
        - pywechat127 可用但连接失败（如微信4.1.15 UI不兼容）：自动降级为
          degraded 模式，只要检测到微信窗口存在就返回 True。
        - pywechat127 不可用：degraded 模式（剪贴板+Ctrl+V）。
        - 检测不到微信窗口：返回 False。

        Returns:
            连接成功（含降级模式）返回 True，异常返回 False。
        """
        # 先检测微信窗口是否存在
        wechat_window = self._get_wechat_window()
        wechat_running = wechat_window is not None

        if self.pywechat_available and _pw_Tools is not None:
            try:
                info = _pw_Tools.about_weixin()
                # 快速测试UI自动化是否可用（微信4.1.15+ UI自绘，dump_recent_sessions会失败）
                ui_available = False
                if _pw_Messages is not None:
                    try:
                        import pywinauto.timings as _pwt
                        old_timeout = _pwt.Timings.window_find_timeout
                        _pwt.Timings.window_find_timeout = 1.5  # 短超时快速测试
                        try:
                            _pw_Messages.dump_recent_sessions()
                            ui_available = True
                        except Exception:
                            ui_available = False
                        finally:
                            _pwt.Timings.window_find_timeout = old_timeout
                    except Exception:
                        ui_available = False

                if ui_available:
                    self.connected = True
                    self._mode = "normal"
                    self._emit_wechat_status("connected", info=str(info))
                    logger.info("微信连接成功（normal模式）：%s", info)
                else:
                    # UI自动化不可用（微信4.1.15+自绘UI），尝试多版本适配器
                    adapter = self._try_create_adapter(info)
                    if adapter:
                        self._adapter = adapter
                        self._mode = "adapter"
                        self.connected = True
                        self._wechat_version = adapter.detect_version() or "4.1+"
                        self._emit_wechat_status(
                            "adapter",
                            info=f"微信{self._wechat_version}，使用{adapter.NAME}适配器（截图+OCR）"
                        )
                        logger.info("使用多版本适配器: %s (版本: %s)", adapter.NAME, self._wechat_version)
                    else:
                        # 适配器也不可用，降级为剪贴板模式
                        self.connected = True
                        self._mode = "degraded"
                        self._emit_wechat_status(
                            "degraded",
                            info="pywechat127 UI自动化不兼容，适配器初始化失败，已降级为窗口检测+剪贴板模式"
                        )
                        logger.warning("UI自动化不可用且适配器初始化失败，降级为剪贴板模式")
                return True
            except Exception as e:
                # pywechat127连接失败但微信窗口存在 → 自动降级
                if wechat_running:
                    self.connected = True
                    self._mode = "degraded"
                    self._emit_wechat_status(
                        "degraded",
                        info=f"pywechat127不兼容当前微信版本，已降级为剪贴板模式（{e}）"
                    )
                    logger.warning("pywechat127连接失败，自动降级为剪贴板模式：%s", e)
                    return True
                else:
                    self.connected = False
                    self._emit_wechat_status("error", error=str(e))
                    logger.warning("pywechat127连接失败且未检测到微信窗口：%s", e)
                    return False

        # pywechat127 未安装 → 降级模式
        if wechat_running:
            self.connected = True
            self._mode = "degraded"
            self._emit_wechat_status(
                "degraded", info="pywechat127未安装，使用降级模式（剪贴板+Ctrl+V）"
            )
            logger.info("pywechat127未安装，进入降级模式（检测到微信窗口）")
            return True
        else:
            self.connected = False
            self._emit_wechat_status("error", error="未检测到微信窗口，请先启动微信")
            logger.warning("未检测到微信窗口")
            return False

    def _try_create_adapter(self, version_info=None):
        """尝试创建多版本UI适配器。

        当pywechat127的UI自动化不可用时（如微信4.1+自绘UI），
        使用基于截图+OCR的适配器来监听消息。

        Args:
            version_info: pywechat127返回的版本信息

        Returns:
            适配器实例，失败返回None
        """
        try:
            from services.wechat_adapters.factory import WeChatAdapterFactory
            # 从version_info中提取版本号
            version = None
            if isinstance(version_info, dict):
                version = version_info.get("version")
            elif isinstance(version_info, str):
                version = version_info
            adapter = WeChatAdapterFactory.create_and_connect(
                version=version,
                signals=self.signals,
                config=self.config
            )
            return adapter
        except Exception as e:
            logger.warning(f"创建多版本适配器失败: {e}")
            return None

    # ------------------------------------------------------------------ #
    # 当前聊天对象
    # ------------------------------------------------------------------ #
    def get_current_chat_person(self) -> str:
        """获取当前聊天窗口对方昵称（用于自动切换对象）。

        normal模式：通过 dump_recent_sessions 获取最近会话；
        degraded模式：直接从窗口标题解析（不调用UI自动化，避免滑动微信界面）。

        Returns:
            当前会话对方昵称；无法获取时返回空字符串。
        """
        # 适配器模式：使用多版本适配器获取当前对象
        if self._mode == "adapter" and self._adapter:
            try:
                person = self._adapter.get_current_chat_person()
                if person:
                    return person
            except Exception as e:
                logger.debug(f"适配器获取当前对象失败: {e}")
        # 降级模式：直接从窗口标题解析，不调用dump_recent_sessions
        if self._mode == "degraded" or not self.pywechat_available:
            title = self._get_wechat_window_title()
            if title and title != "微信":
                return title
            return ""
        try:
            # normal模式：从最近会话列表取第一个
            if _pw_Messages is not None:
                sessions = _pw_Messages.dump_recent_sessions()
                if sessions and len(sessions) > 0:
                    first = sessions[0]
                    if isinstance(first, (tuple, list)) and len(first) > 0:
                        name = str(first[0]).strip()
                        if name and name not in ("微信", "公众号", "文件传输助手"):
                            return name
            # 兜底：从微信窗口标题解析
            title = self._get_wechat_window_title()
            if title and title != "微信":
                return title
            return ""
        except Exception as e:
            logger.debug("获取当前聊天对象失败：%s", e)
            # 异常时也走窗口标题兜底
            title = self._get_wechat_window_title()
            if title and title != "微信":
                return title
            return ""

    # ------------------------------------------------------------------ #
    # 监听线程
    # ------------------------------------------------------------------ #
    def start_listener(self) -> None:
        """启动后台监听线程（daemon）。已在运行则直接返回。"""
        if self.running:
            logger.debug("监听线程已在运行，跳过启动")
            return
        self.running = True
        self.listener_thread = threading.Thread(
            target=self._listen_loop, name="WeChatListener", daemon=True
        )
        self.listener_thread.start()
        logger.info("微信监听线程已启动")

    def _listen_loop(self) -> None:
        """监听循环（运行在 daemon 线程中）。

        每 monitor_interval 秒执行一次：
        0. 自动重连检测：微信窗口出现但未连接时自动 connect；
        1. 检测当前聊天对象，变化时 emit person_switched；
        2. 拉取新消息，按指纹去重后逐条 emit new_message；
        3. 控制去重集合大小。
        任何异常都被捕获并记录，绝不崩溃退出线程。
        """
        while self.running:
            try:
                # 0. 自动重连：微信窗口存在但未连接时，尝试连接
                if not self.connected:
                    win = self.get_window_info()
                    if win is not None:
                        logger.info("检测到微信窗口，自动重连...")
                        try:
                            self.connect()
                        except Exception as e:
                            logger.debug("自动重连失败: %s", e)

                # 1. 检测当前聊天对象
                person = self.get_current_chat_person()
                # 聊天对象切换
                if person and person != self.current_person:
                    self.current_person = person
                    self.signals.person_switched.emit({"wechat_nickname": person})
                    logger.info("切换聊天对象：%s", person)

                # 拉取并推送新消息
                new_msgs = self._get_new_messages()
                for msg in new_msgs:
                    key = self._make_msg_key(msg)
                    if key not in self.last_msg_hash:
                        self.last_msg_hash.add(key)
                        self.signals.new_message.emit(msg)

                # 控制去重集合大小
                if len(self.last_msg_hash) > self._MAX_MSG_HASH:
                    # set 无序，转 list 后保留尾部近似最近的 N 条
                    kept = list(self.last_msg_hash)[-self._KEPT_MSG_HASH:]
                    self.last_msg_hash = set(kept)
            except Exception as e:
                # 监听出错不崩溃，下一轮重试
                logger.warning("监听循环异常（已捕获）：%s", e)
            time.sleep(self.monitor_interval)

    def _get_new_messages(self) -> List[Dict[str, Any]]:
        """获取新消息。

        normal模式：对当前会话调用 pull_messages(friend, number)；
        degraded模式：不调用UI自动化（避免刷微信界面），返回空列表。
        后续可用截图+OCR方案替代。

        Returns:
            消息列表，每条格式：
            ``{'content': str, 'direction': 'received', 'timestamp': float, 'person': str}``
        """
        # 适配器模式：使用多版本适配器获取消息（截图+OCR）
        if self._mode == "adapter" and self._adapter:
            try:
                raw_msgs = self._adapter.get_new_messages()
                msgs = []
                for m in raw_msgs:
                    parsed = {
                        "content": m.get("content", ""),
                        "direction": m.get("direction", "received"),
                        "timestamp": m.get("timestamp", 0),
                        "person": self.current_person or "",
                        "msg_type": m.get("msg_type", 1),
                    }
                    if parsed["content"]:
                        msgs.append(parsed)
                return msgs
            except Exception as e:
                logger.debug(f"适配器获取消息失败: {e}")
                return []
        # 降级模式：不调用pull_messages，避免滑动微信界面
        if self._mode == "degraded" or not self.pywechat_available:
            return []
        try:
            msgs: List[Dict[str, Any]] = []

            # normal模式：对当前会话用 pull_messages(friend, number)
            person = self.current_person or self.get_current_chat_person()
            if person and _pw_Messages is not None and hasattr(_pw_Messages, "pull_messages"):
                try:
                    raw = _pw_Messages.pull_messages(person, 30)
                    if isinstance(raw, list):
                        for item in raw:
                            parsed = self._parse_pywechat_msg(item, person)
                            if parsed:
                                msgs.append(parsed)
                except Exception as e:
                    logger.debug("pull_messages(%s) 失败: %s", person, e)

            return msgs
        except Exception as e:
            logger.debug("获取新消息失败：%s", e)
            return []

    @staticmethod
    def _parse_pywechat_msg(item: Any, default_person: str = "") -> Optional[Dict[str, Any]]:
        """解析 pywechat127 返回的单条消息为统一格式。

        pywechat127 实际字段（中文）：消息发送人/消息内容/消息类型
        兼容英文字段：content/text/msg, direction/type, sender/talker 等。
        """
        if not isinstance(item, dict):
            return None
        content = (
            item.get("消息内容")
            or item.get("content")
            or item.get("text")
            or item.get("msg")
            or item.get("message")
            or ""
        )
        if not content:
            return None
        # 发送人
        sender = (
            item.get("消息发送人")
            or item.get("person")
            or item.get("talker")
            or item.get("sender")
            or item.get("from_user")
            or ""
        )
        # 判断方向：发送人等于自己则为 sent，否则为 received
        direction = "received"
        msg_type = str(item.get("消息类型") or item.get("type") or "").lower()
        if item.get("direction"):
            direction = str(item["direction"])
        elif item.get("is_sender") is True or item.get("is_self") is True or item.get("is_my_bubble") is True:
            direction = "sent"
        elif sender and default_person and sender == default_person:
            # 发送人等于当前会话对象 → 对方发的 → received
            direction = "received"
        elif sender and sender not in (default_person, ""):
            # 发送人不是当前会话对象 → 可能是自己发的 → sent
            # （pywechat127 中自己的消息发送人显示为自己的昵称）
            direction = "sent"
        # 时间戳
        ts = item.get("timestamp") or item.get("time") or item.get("create_time") or item.get("消息时间")
        if isinstance(ts, str):
            try:
                ts = float(ts)
            except (ValueError, TypeError):
                ts = time.time()
        elif not isinstance(ts, (int, float)):
            ts = time.time()
        return {
            "content": str(content),
            "direction": direction,
            "timestamp": float(ts),
            "person": str(sender or default_person),
        }

    # ------------------------------------------------------------------ #
    # 文本文件导入聊天记录
    # ------------------------------------------------------------------ #
    @staticmethod
    def parse_chat_file(file_path: str, other_nickname: str = "") -> List[Dict[str, Any]]:
        """从文本文件解析微信聊天记录。

        支持格式：
        1. 带时间: `2024-01-01 12:00:00 昵称: 消息内容`
        2. 带时间括号: `昵称(2024-01-01 12:00): 消息内容`
        3. 无时间: `昵称: 消息内容`
        4. CSV: `时间,发送人,内容`
        5. JSON: `[{"time":"...","sender":"...","content":"..."}]`

        方向判断：发送人等于 other_nickname（对方昵称）就是 received，否则是 sent。

        Args:
            file_path: 聊天记录文件路径（.txt/.csv/.json）
            other_nickname: 对方的微信昵称，用于判断方向

        Returns:
            消息列表 [{content, direction, timestamp, person, msg_type}]
        """
        import os
        import re
        import json
        import csv

        if not os.path.exists(file_path):
            return []

        ext = os.path.splitext(file_path)[1].lower()
        messages = []

        # JSON 格式
        if ext == ".json":
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        content = str(item.get("content") or item.get("msg") or item.get("text") or "")
                        if not content:
                            continue
                        sender = str(item.get("sender") or item.get("from") or item.get("talker") or "")
                        direction = "received" if (other_nickname and sender == other_nickname) else "sent"
                        ts = item.get("time") or item.get("timestamp") or item.get("create_time") or time.time()
                        messages.append({
                            "content": content,
                            "direction": direction,
                            "timestamp": float(ts) if isinstance(ts, (int, float)) else time.time(),
                            "person": sender,
                            "msg_type": item.get("type", "text"),
                        })
                return messages
            except Exception:
                pass

        # CSV 格式
        if ext == ".csv":
            try:
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        content = str(row.get("内容") or row.get("content") or row.get("消息") or "")
                        if not content:
                            continue
                        sender = str(row.get("发送人") or row.get("sender") or row.get("昵称") or "")
                        direction = "received" if (other_nickname and sender == other_nickname) else "sent"
                        ts_str = str(row.get("时间") or row.get("time") or row.get("日期") or "")
                        messages.append({
                            "content": content,
                            "direction": direction,
                            "timestamp": time.time(),
                            "person": sender,
                            "msg_type": "text",
                        })
                return messages
            except Exception:
                pass

        # TXT 格式（逐行解析）
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="gbk") as f:
                lines = f.readlines()

        # 正则：匹配 "时间 昵称: 内容" 或 "昵称(时间): 内容" 或 "昵称: 内容"
        # 时间格式：2024-01-01 12:00:00 或 2024/01/01 12:00
        time_pattern = r'(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)'
        pattern_with_time = re.compile(r'^' + time_pattern + r'\s+(.+?):\s*(.*)$')
        pattern_paren_time = re.compile(r'^(.+?)\(' + time_pattern + r'\):\s*(.*)$')
        pattern_no_time = re.compile(r'^(.+?):\s*(.*)$')

        current_sender = None
        current_content = []
        current_time_str = None

        def _parse_time_str(time_str: str) -> float:
            """把时间字符串转成时间戳，失败返回当前时间。"""
            try:
                from datetime import datetime
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
                    try:
                        return datetime.strptime(time_str.strip(), fmt).timestamp()
                    except ValueError:
                        continue
            except Exception:
                pass
            return time.time()

        def _detect_msg_type(content: str) -> str:
            """根据内容检测消息类型：text/voice/image/video/file/emoji/system。"""
            c = content.strip()
            # 语音消息标记
            voice_patterns = [r'^\[语音\]', r'^\[语音消息\]', r'^\[语音\]\s*\d+\s*秒', r'语音通话时长', r'^\[语音通话\]']
            for p in voice_patterns:
                if re.match(p, c):
                    return "voice"
            # 图片
            image_patterns = [r'^\[图片\]', r'^\[表情\]', r'^\[动画表情\]', r'^\[表情包\]']
            for p in image_patterns:
                if re.match(p, c):
                    return "image"
            # 视频
            video_patterns = [r'^\[视频\]', r'^\[小视频\]']
            for p in video_patterns:
                if re.match(p, c):
                    return "video"
            # 文件
            file_patterns = [r'^\[文件\]', r'^\[文档\]', r'^\[链接\]']
            for p in file_patterns:
                if re.match(p, c):
                    return "file"
            # 系统消息
            system_patterns = [r'^撤回了一条消息', r'^对方撤回了', r'^你撤回了', r'^以上是打招呼', r'^我通过了', r'^你已添加']
            for p in system_patterns:
                if re.match(p, c):
                    return "system"
            return "text"

        def flush_message():
            nonlocal current_sender, current_content, current_time_str
            if current_sender and current_content:
                content = "\n".join(current_content).strip()
                if content:
                    direction = "received" if (other_nickname and current_sender == other_nickname) else "sent"
                    ts = _parse_time_str(current_time_str) if current_time_str else time.time()
                    msg_type = _detect_msg_type(content)
                    messages.append({
                        "content": content,
                        "direction": direction,
                        "timestamp": ts,
                        "time_str": current_time_str or "",
                        "person": current_sender,
                        "msg_type": msg_type,
                    })
            current_sender = None
            current_content = []
            current_time_str = None

        for line in lines:
            line = line.rstrip("\n\r")
            if not line.strip():
                continue

            # 尝试匹配带时间的格式
            m = pattern_with_time.match(line)
            if m:
                flush_message()
                current_time_str = m.group(1).strip()
                current_sender = m.group(2).strip()
                current_content = [m.group(3).strip()]
                continue

            # 尝试匹配括号时间格式
            m = pattern_paren_time.match(line)
            if m:
                flush_message()
                current_sender = m.group(1).strip()
                current_time_str = m.group(2).strip()
                current_content = [m.group(3).strip()]
                continue

            # 尝试匹配无时间格式
            m = pattern_no_time.match(line)
            if m and len(m.group(1)) < 30:  # 昵称不会太长
                flush_message()
                current_sender = m.group(1).strip()
                current_time_str = None
                current_content = [m.group(2).strip()]
                continue

            # 多行消息：追加到当前消息
            if current_sender:
                current_content.append(line.strip())

        flush_message()
        return messages

    # ------------------------------------------------------------------ #
    # 从本地数据库导入（pywxdump 解密读取，零封号风险）
    # ------------------------------------------------------------------ #
    @staticmethod
    def import_from_local_db(person_name: str = "", limit: int = 0) -> Dict[str, Any]:
        """从微信本地数据库读取聊天记录（零封号风险）。

        直接读取并解密微信本地 SQLite 数据库，不操作微信UI，不会触发封号。
        需要微信在运行（从内存读取数据库密钥）。

        Args:
            person_name: 联系人昵称/备注，空则返回所有会话列表
            limit: 每人最多返回的消息数，0=全部

        Returns:
            dict: {ok, count, messages, sessions, error}
        """
        try:
            import os
            import tempfile
            import hashlib
            import sqlite3

            # 懒加载 pywxdump
            try:
                from pywxdump import get_wx_info, decrypt
            except ImportError:
                return {"ok": False, "count": 0, "messages": [], "error": "pywxdump 未安装"}

            # 1. 获取微信信息（密钥、数据目录、wxid）
            wx_info = get_wx_info()
            if not wx_info or not wx_info.get("key"):
                return {"ok": False, "count": 0, "messages": [], "error": "无法获取微信密钥，请确保微信正在运行"}

            key = wx_info["key"]
            wxid = wx_info.get("wxid", "")
            data_dir = wx_info.get("data_dir", "")
            if not data_dir or not os.path.exists(data_dir):
                return {"ok": False, "count": 0, "messages": [], "error": f"微信数据目录不存在: {data_dir}"}

            # 2. 找到 MSG.db 文件
            msg_dir = os.path.join(data_dir, "Msg", "Multi")
            if not os.path.exists(msg_dir):
                msg_dir = os.path.join(data_dir, "Msg")
            if not os.path.exists(msg_dir):
                return {"ok": False, "count": 0, "messages": [], "error": f"找不到消息数据库目录: {msg_dir}"}

            db_files = sorted([f for f in os.listdir(msg_dir) if f.startswith("MSG") and f.endswith(".db")])
            if not db_files:
                return {"ok": False, "count": 0, "messages": [], "error": "找不到 MSG.db 文件"}

            # 3. 解密第一个 MSG.db（包含所有会话的消息索引）
            tmp_dir = tempfile.mkdtemp(prefix="loveai_wx_")
            decrypted_path = os.path.join(tmp_dir, "MSG_decrypted.db")
            src_db = os.path.join(msg_dir, db_files[0])
            success, msg = decrypt(key, src_db, decrypted_path)
            if not success:
                return {"ok": False, "count": 0, "messages": [], "error": f"解密失败: {msg}"}

            # 4. 查询会话列表或指定联系人的消息
            conn = sqlite3.connect(decrypted_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 获取所有会话表（Chat_xxxxxx，xxxxxx是联系人wxid的md5前6位）
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Chat_%'")
            chat_tables = [row[0] for row in cursor.fetchall()]

            if not person_name:
                # 返回会话列表（需要 MicroMsg.db 匹配昵称）
                sessions = []
                for t in chat_tables:
                    cursor.execute(f"SELECT COUNT(*) as cnt FROM {t}")
                    cnt = cursor.fetchone()[0]
                    if cnt > 0:
                        sessions.append({"table": t, "count": cnt})
                conn.close()
                return {"ok": True, "count": 0, "messages": [], "sessions": sessions}

            # 5. 查找指定联系人的聊天表
            # 微信用联系人 wxid 的 md5 前6位作为表名后缀
            # 先尝试直接匹配，再遍历所有表查找
            target_table = None
            for t in chat_tables:
                cursor.execute(f"SELECT StrTalker FROM {t} LIMIT 1")
                row = cursor.fetchone()
                if row and row[0]:
                    # 检查这个表的消息是否包含目标联系人
                    cursor.execute(f"SELECT COUNT(*) FROM {t} WHERE StrTalker LIKE ? OR StrContent LIKE ?",
                                   (f"%{person_name}%", f"%{person_name}%"))
                    if cursor.fetchone()[0] > 0:
                        target_table = t
                        break

            if not target_table and chat_tables:
                # 兜底：用第一个有消息的表
                for t in chat_tables:
                    cursor.execute(f"SELECT COUNT(*) as cnt FROM {t}")
                    if cursor.fetchone()[0] > 0:
                        target_table = t
                        break

            if not target_table:
                conn.close()
                return {"ok": False, "count": 0, "messages": [], "error": f"未找到联系人「{person_name}」的聊天记录"}

            # 6. 查询消息
            limit_sql = f"LIMIT {limit}" if limit > 0 else ""
            cursor.execute(f"""
                SELECT StrTalker, StrContent, CreateTime, Type, IsSender
                FROM {target_table}
                ORDER BY CreateTime ASC
                {limit_sql}
            """)
            rows = cursor.fetchall()
            conn.close()

            # 7. 转换为统一格式
            messages = []
            for row in rows:
                content = row["StrContent"] or ""
                if not content.strip():
                    continue
                msg_type = "text"
                msg_type_id = row["Type"] or 1
                if msg_type_id == 3:
                    msg_type = "image"
                elif msg_type_id == 34:
                    msg_type = "voice"
                elif msg_type_id == 43:
                    msg_type = "video"
                elif msg_type_id == 49:
                    msg_type = "file"
                elif msg_type_id == 10000:
                    msg_type = "system"

                direction = "sent" if row["IsSender"] == 1 else "received"
                messages.append({
                    "content": content,
                    "direction": direction,
                    "timestamp": row["CreateTime"] or 0,
                    "person": row["StrTalker"] or person_name,
                    "msg_type": msg_type,
                })

            # 清理临时文件
            try:
                os.remove(decrypted_path)
                os.rmdir(tmp_dir)
            except Exception:
                pass

            return {"ok": True, "count": len(messages), "messages": messages, "sessions": []}
        except Exception as e:
            logger.exception("import_from_local_db 失败")
            return {"ok": False, "count": 0, "messages": [], "error": str(e)}

    # ------------------------------------------------------------------ #
    # 一键导入聊天记录（实时拉取）
    # ------------------------------------------------------------------ #
    def import_chat_history(self, person_name: str, limit: int = 0) -> Dict[str, Any]:
        """一键导入指定联系人的微信聊天记录。

        流程：
        1. 确保微信窗口在前台；
        2. 通过搜索框搜索并打开该联系人的聊天窗口；
        3. 循环调用 pull_messages 拉取消息，每次自动向上滚动加载更早历史；
        4. 去重合并，直到连续2轮没有新消息（说明已到顶部）；
        5. limit>0 时最多拉取 limit 条，limit=0 表示完整导入全部。

        Args:
            person_name: 微信联系人昵称/备注
            limit: 拉取消息条数上限，0=完整导入全部

        Returns:
            dict: {ok, count, messages, error, rounds}
        """
        if not self.pywechat_available:
            return {"ok": False, "count": 0, "messages": [], "error": "pywechat127 未安装"}

        try:
            import time as _t
            from pyweixin import Messages

            # 1. 确保微信窗口在前台
            win = self.get_window_info()
            if win is None:
                return {"ok": False, "count": 0, "messages": [], "error": "未检测到微信窗口，请先启动微信"}

            # 2. 尝试打开聊天窗口（搜索方式）
            try:
                import pywinauto
                from pywinauto import Application
                app = Application(backend="uia").connect(title="微信", timeout=5)
                wx_win = app.window(title="微信")
                wx_win.set_focus()
                _t.sleep(0.8)
                search_box = wx_win.child_window(title="搜索", control_type="Edit")
                if search_box.exists():
                    search_box.click_input()
                    _t.sleep(0.3)
                    search_box.type_keys("^a")
                    _t.sleep(0.2)
                    search_box.type_keys(person_name)
                    _t.sleep(1.2)
                    search_box.type_keys("{ENTER}")
                    _t.sleep(1.5)
                    logger.info("已通过搜索打开 %s 的聊天窗口", person_name)
            except Exception as e:
                logger.debug("搜索打开聊天窗口失败: %s", e)

            # 3. 循环拉取消息（完整导入模式）
            all_msgs = []
            seen_keys = set()
            rounds = 0
            no_new_count = 0
            batch_size = 100  # 每轮拉取数量

            while True:
                rounds += 1
                try:
                    raw = Messages.pull_messages(person_name, batch_size)
                except Exception as e:
                    logger.debug("pull_messages 第%d轮失败: %s", rounds, e)
                    _t.sleep(1.5)
                    no_new_count += 1
                    if no_new_count >= 3:
                        break
                    continue

                if not raw:
                    no_new_count += 1
                    if no_new_count >= 2:
                        break
                    _t.sleep(1)
                    continue

                # 去重合并
                new_count = 0
                for item in raw:
                    content = item.get("消息内容") or item.get("content") or ""
                    sender = item.get("消息发送人") or ""
                    key = f"{sender}|{content}|{len(all_msgs)}"
                    # 用内容+发送人做去重键（更精确）
                    dedup_key = f"{sender}|{content}"
                    if dedup_key not in seen_keys:
                        seen_keys.add(dedup_key)
                        all_msgs.append(item)
                        new_count += 1

                logger.info("第%d轮拉取: 新增%d条, 累计%d条", rounds, new_count, len(all_msgs))

                # 达到上限
                if limit > 0 and len(all_msgs) >= limit:
                    all_msgs = all_msgs[:limit]
                    break

                # 连续2轮没有新消息，说明已到顶部
                if new_count == 0:
                    no_new_count += 1
                    if no_new_count >= 2:
                        break
                else:
                    no_new_count = 0

                # 向上滚动加载更早消息（模拟鼠标滚轮）
                try:
                    import pywinauto
                    from pywinauto import Application
                    app = Application(backend="uia").connect(title="微信", timeout=5)
                    wx_win = app.window(title="微信")
                    # 找到聊天消息区域并向上滚动
                    chat_area = wx_win.child_window(title="消息", control_type="List")
                    if chat_area.exists():
                        chat_area.scroll("up", amount=5)
                        _t.sleep(0.8)
                except Exception:
                    pass  # 滚动失败不影响，pull_messages内部也会滚动

                _t.sleep(0.5)

                # 安全上限：最多20轮，防止无限循环
                if rounds >= 20:
                    logger.info("达到最大轮数20，停止拉取")
                    break

            if not all_msgs:
                return {"ok": False, "count": 0, "messages": [],
                        "error": "未能拉取消息，请确保已打开该联系人聊天窗口"}

            # 4. 解析消息
            messages = []
            for item in all_msgs:
                parsed = self._parse_pywechat_msg(item, person_name)
                if parsed:
                    messages.append(parsed)

            logger.info("导入 %s 聊天记录 %d 条（%d轮）", person_name, len(messages), rounds)
            return {"ok": True, "count": len(messages), "messages": messages,
                    "error": "", "rounds": rounds}

        except Exception as e:
            logger.exception("导入聊天记录失败")
            return {"ok": False, "count": 0, "messages": [], "error": str(e)}

        except Exception as e:
            logger.exception("导入聊天记录失败")
            return {"ok": False, "count": 0, "messages": [], "error": str(e)}

    # ------------------------------------------------------------------ #
    # 自动填入
    # ------------------------------------------------------------------ #
    def fill_text(self, text: str) -> bool:
        """将文本填入微信对话框（不自动发送）。

        流程：
        1. 校验微信窗口是否存在；
        2. pywechat127 可用时优先调用 Messages.send_text / input_text；
        3. 失败或不可用时降级为 pyperclip.copy + ctypes 模拟 Ctrl+V；
        4. 按键模拟也失败时，仅复制到剪贴板并提示用户手动 Ctrl+V。

        Args:
            text: 要填入的文本。

        Returns:
            成功填入（或至少复制到剪贴板）返回 True；剪贴板都不可用返回 False。
        """
        if not text:
            logger.debug("fill_text 收到空文本，跳过")
            return False

        # 校验微信窗口是否存在
        if self.get_window_info() is None:
            logger.warning("fill_text 失败：未检测到微信窗口")
            return False

        # 适配器模式：优先使用多版本适配器发送
        if self._mode == "adapter" and self._adapter:
            try:
                if self._adapter.send_message(text):
                    logger.info("适配器发送消息成功（%d 字）", len(text))
                    return True
            except Exception as e:
                logger.warning("适配器发送失败，降级剪贴板：%s", e)

        # 优先走 pywechat127 UIA
        if self.pywechat_available and _pw_Messages is not None:
            try:
                sender = (
                    getattr(_pw_Messages, "send_text", None)
                    or getattr(_pw_Messages, "input_text", None)
                    or getattr(_pw_Messages, "set_text", None)
                )
                if callable(sender):
                    sender(text)
                    logger.info("pywechat127 填入文本成功（%d 字）", len(text))
                    return True
            except Exception as e:
                logger.warning("pywechat127 填入失败，降级剪贴板：%s", e)

        # 降级：pyperclip 复制
        try:
            import pyperclip  # type: ignore
            pyperclip.copy(text)
            logger.info("已复制到剪贴板（%d 字）", len(text))
        except Exception as e:
            logger.error("pyperclip 复制失败：%s", e)
            return False

        # 模拟 Ctrl+V 粘贴到微信输入框
        if self._simulate_ctrl_v():
            logger.info("已模拟 Ctrl+V 粘贴到微信输入框")
        else:
            logger.warning("按键模拟不可用，请手动 Ctrl+V 粘贴")
        return True

    def _simulate_ctrl_v(self) -> bool:
        """通过 ctypes 在当前前台窗口模拟 Ctrl+V。

        Returns:
            模拟成功返回 True；非 Windows 或调用失败返回 False。
        """
        try:
            user32 = ctypes.windll.user32
            VK_CONTROL = 0x11
            VK_V = 0x56
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            user32.keybd_event(VK_V, 0, 0, 0)
            user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.05)
            return True
        except Exception as e:
            logger.debug("Ctrl+V 模拟失败：%s", e)
            return False

    # ------------------------------------------------------------------ #
    # 窗口信息
    # ------------------------------------------------------------------ #
    def get_window_info(self) -> Optional[Dict[str, Any]]:
        """获取微信窗口位置和大小（用于 overlay 对齐）。

        优先调用 app.wechat_detector.WeChatDetector.get_wechat_window()；
        不可用时回退到 ctypes FindWindowW("WeChatMainWndForPC") + GetWindowRect。

        Returns:
            ``{'x','y','width','height','is_focused','is_visible'}``；找不到窗口返回 None。
        """
        # 优先走 WeChatDetector
        if self._detector is not None:
            try:
                info = self._detector.get_wechat_window()
                if info:
                    return info
            except Exception as e:
                logger.debug("WeChatDetector 获取窗口失败，回退 ctypes：%s", e)

        # 回退：ctypes 直接检测（先试类名列表，再试标题枚举）
        try:
            import ctypes.wintypes as wintypes

            user32 = ctypes.windll.user32
            hwnd = None

            # 1. 按类名列表依次查找
            for cls in self._WECHAT_WND_CLASSES:
                hwnd = user32.FindWindowW(cls, None)
                if hwnd:
                    logger.debug("按类名 %s 找到微信窗口 hwnd=%s", cls, hwnd)
                    break

            # 2. 类名都找不到时，枚举窗口按标题匹配
            if not hwnd:
                hwnd = self._find_wechat_by_title()

            if not hwnd:
                return None

            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            return {
                "x": int(rect.left),
                "y": int(rect.top),
                "width": int(rect.right - rect.left),
                "height": int(rect.bottom - rect.top),
                "is_focused": bool(user32.GetForegroundWindow() == hwnd),
                "is_visible": bool(user32.IsWindowVisible(hwnd)),
            }
        except Exception as e:
            logger.debug("ctypes 检测微信窗口失败：%s", e)
            return None

    def _find_wechat_by_title(self) -> int:
        """枚举顶层窗口，按标题关键词匹配微信窗口（新版微信类名变化时的兜底）。"""
        try:
            import ctypes.wintypes as wintypes
            user32 = ctypes.windll.user32
            result = []

            def _enum_cb(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value or ""
                for kw in self._WECHAT_TITLE_KEYWORDS:
                    if kw in title:
                        # 排除微信的子窗口/弹窗（主窗口通常有一定宽度）
                        rect = wintypes.RECT()
                        user32.GetWindowRect(hwnd, ctypes.byref(rect))
                        w = rect.right - rect.left
                        if w > 300:  # 主窗口宽度通常 > 300px
                            result.append(hwnd)
                            return False  # 找到就停止枚举
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(
                ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
            )
            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
            return result[0] if result else 0
        except Exception:
            return 0

    def _get_wechat_window(self):
        """检测微信主窗口是否存在，返回窗口句柄或None。支持多类名+标题回退。"""
        try:
            user32 = ctypes.windll.user32
            for cls in self._WECHAT_WND_CLASSES:
                hwnd = user32.FindWindowW(cls, None)
                if hwnd:
                    return hwnd
            return self._find_wechat_by_title()
        except Exception:
            return None

    def _get_wechat_window_title(self) -> str:
        """读取微信窗口标题（辅助判断当前会话名）。支持多类名+标题回退。"""
        try:
            user32 = ctypes.windll.user32
            hwnd = None
            for cls in self._WECHAT_WND_CLASSES:
                hwnd = user32.FindWindowW(cls, None)
                if hwnd:
                    break
            if not hwnd:
                hwnd = self._find_wechat_by_title()
            if not hwnd:
                return ""
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return ""
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value or ""
        except Exception:
            return ""

    # ------------------------------------------------------------------ #
    # 停止 / 状态
    # ------------------------------------------------------------------ #
    def stop_listener(self) -> None:
        """停止监听线程，并等待其结束（join 超时 2 秒）。"""
        self.running = False
        if self.listener_thread is not None and self.listener_thread.is_alive():
            self.listener_thread.join(timeout=2.0)
            if self.listener_thread.is_alive():
                logger.warning("监听线程 2 秒内未退出（daemon，进程退出时自动结束）")
        self.listener_thread = None
        logger.info("微信监听线程已停止")

    def is_connected(self) -> bool:
        """返回当前连接状态。"""
        return self.connected

    def get_status(self) -> Dict[str, Any]:
        """返回微信服务状态快照。

        Returns:
            ``{'connected', 'pywechat_available', 'current_person',
               'listening', 'mode'}``，mode 为 'normal' 或 'degraded'。
        """
        return {
            "connected": self.connected,
            "pywechat_available": self.pywechat_available,
            "current_person": self.current_person or "",
            "listening": self.running,
            "mode": self._mode,
            "wechat_version": self._wechat_version or "",
            "adapter_name": self._adapter.NAME if self._adapter else "",
        }

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _emit_wechat_status(self, status: str, info: str = "", error: str = "") -> None:
        """安全地通过 signals 推送微信状态（信号不存在也不抛异常）。"""
        try:
            payload: Dict[str, Any] = {"status": status}
            if info:
                payload["info"] = info
            if error:
                payload["error"] = error
            self.signals.wechat_status.emit(payload)
        except Exception as e:
            logger.debug("推送 wechat_status 信号失败：%s", e)

    @staticmethod
    def _pick(item: Any, keys: tuple) -> Any:
        """从 dict 或对象中按候选键顺序取第一个非空值。"""
        if isinstance(item, dict):
            for k in keys:
                if k in item and item[k]:
                    return item[k]
            return None
        for k in keys:
            v = getattr(item, k, None)
            if v:
                return v
        return None

    @staticmethod
    def _make_msg_key(msg: Dict[str, Any]) -> str:
        """根据消息内容生成去重指纹（MD5，避免长串占用内存）。"""
        person = msg.get("person", "") or ""
        direction = msg.get("direction", "") or ""
        ts = msg.get("timestamp", "") or ""
        content = msg.get("content", "") or ""
        raw = f"{person}|{direction}|{ts}|{content}"
        return hashlib.md5(raw.encode("utf-8", errors="ignore")).hexdigest()
