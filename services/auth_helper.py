# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 验证码自动识别与会话保活助手（AuthHelper）

借鉴 codex-auth-helper（GitHub: zhishile/codex-auth-helper）的核心思路：
  - 本地认证状态持久化（auth.json），避免每次登录都走完整流程
  - token 过期自动刷新，刷新失败才触发重新登录
  - 验证码图片自动 OCR 识别，按类型选择识别策略

本模块在 LoveAI 语境下聚焦微信桌面端 / 网页登录场景：
  1. 验证码图片自动 OCR 识别（复用 services/ocr_service.py）
  2. 登录会话 token 自动刷新与持久化（auth.json，存 data/auth/）
  3. 验证码类型识别（滑块/文字/数字/图形），自动选择识别策略
  4. 登录状态监控与自动重连（检测 session 过期 → 触发重新登录回调）
  5. 与 wechat_operator 集成的接口（provide_verification_code 回调）

设计约束：
  - 纯 Python，不 import PySide6
  - 所有公开方法 try/except 兜底，单模块失败不影响主流程
  - OCR 不可用时走离线规则兜底（基础像素/字符特征）
  - 目录不存在时自动 makedirs(exist_ok=True)
"""
from __future__ import annotations

import os
import json
import time
import logging
import re
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 项目根目录（services/ 的上一级）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CaptchaType(Enum):
    """验证码类型枚举。"""
    SLIDER = "slider"        # 滑块验证码
    TEXT = "text"            # 文字验证码（中英混合）
    NUMBER = "number"        # 纯数字验证码
    GRAPHIC = "graphic"      # 图形验证码（算术/点选等）
    UNKNOWN = "unknown"      # 识别失败


class AuthHelper:
    """
    认证助手：管理验证码识别、token 持久化、会话保活。

    典型用法：
        helper = AuthHelper()
        helper.attach_ocr(ocr_service)          # 注入 OCR 服务
        helper.set_relogin_callback(on_relogin) # 注册重新登录回调
        code = helper.recognize_captcha(img_path)
        helper.save_token("wx", token="xxx", expires_in=7200)
        token = helper.get_valid_token("wx")
    """

    # auth.json 中各平台 token 的默认过期缓冲（秒）：提前 30 分钟刷新
    DEFAULT_REFRESH_MARGIN = 1800

    def __init__(
        self,
        ocr_service: Any = None,
        auth_dir: Optional[str] = None,
        refresh_margin: int = DEFAULT_REFRESH_MARGIN,
    ) -> None:
        """
        初始化 AuthHelper。
        :param ocr_service: OCRService 实例（可选，懒加载注入）
        :param auth_dir: 认证文件目录，默认 data/auth/
        :param refresh_margin: token 过期前提前刷新的秒数
        """
        self.ocr = ocr_service
        self.refresh_margin = int(refresh_margin) if refresh_margin else self.DEFAULT_REFRESH_MARGIN

        # 认证持久化目录
        self.auth_dir = auth_dir or os.path.join(PROJECT_ROOT, "data", "auth")
        try:
            os.makedirs(self.auth_dir, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("创建认证目录失败: %s", e)

        self.auth_file = os.path.join(self.auth_dir, "auth.json")

        # 重新登录回调：session 过期时触发，由上层（wechat_operator / UI）实现
        self._relogin_callback: Optional[Callable[[str], bool]] = None
        # 验证码回调：由 wechat_operator 注册，登录时自动取验证码
        self._provide_code_callback: Optional[Callable[[str], Optional[str]]] = None
        # token 刷新回调：用 refresh_token 换发新 token，由上层按平台实现
        # 签名: callback(platform, refresh_token) -> Optional[dict]
        #   返回 dict 至少含 token，可选 expires_in(秒) / refresh_token(轮换)
        self._refresh_callback: Optional[Callable[[str, str], Optional[dict]]] = None

        # 内存中的会话缓存（平台名 -> token dict）
        self._sessions: dict[str, dict] = {}

        # 启动时尝试加载已有 auth.json
        self._load_auth_file()

        logger.info("AuthHelper 初始化完成（auth_dir=%s）", self.auth_dir)

    # ------------------------------------------------------------------ #
    # OCR 注入
    # ------------------------------------------------------------------ #
    def attach_ocr(self, ocr_service: Any) -> None:
        """注入 OCR 服务实例（通常是 services.ocr_service.OCRService）。"""
        try:
            self.ocr = ocr_service
            logger.info("AuthHelper 已挂载 OCR 服务")
        except Exception as e:  # noqa: BLE001
            logger.warning("挂载 OCR 服务失败: %s", e)

    # ------------------------------------------------------------------ #
    # auth.json 持久化
    # ------------------------------------------------------------------ #
    def _load_auth_file(self) -> None:
        """从 auth.json 加载已有会话到内存。任何异常都不影响主流程。"""
        try:
            if not os.path.exists(self.auth_file):
                return
            with open(self.auth_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._sessions = data
                logger.info("已加载 auth.json，包含 %d 个平台会话", len(self._sessions))
        except Exception as e:  # noqa: BLE001
            logger.warning("加载 auth.json 失败（将重新开始）: %s", e)
            self._sessions = {}

    def _flush_auth_file(self) -> bool:
        """把内存会话写回 auth.json。返回是否成功。"""
        try:
            os.makedirs(self.auth_dir, exist_ok=True)
            # 先写临时文件再替换，避免半写损坏
            tmp = self.auth_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._sessions, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.auth_file)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("写入 auth.json 失败: %s", e)
            return False

    # ------------------------------------------------------------------ #
    # token 保存 / 读取 / 刷新
    # ------------------------------------------------------------------ #
    def save_token(
        self,
        platform: str,
        token: str,
        expires_in: int = 7200,
        extra: Optional[dict] = None,
    ) -> bool:
        """
        保存某平台的登录 token。
        :param platform: 平台标识，如 "wechat_web" / "wechat_pc"
        :param token: 会话 token
        :param expires_in: 有效期（秒）
        :param extra: 额外字段（cookies / refresh_token 等）
        """
        try:
            now = time.time()
            session = {
                "token": token,
                "issued_at": now,
                "expires_at": now + int(expires_in),
                "extra": extra or {},
            }
            self._sessions[platform] = session
            ok = self._flush_auth_file()
            logger.info("已保存 %s 的 token（有效期 %ds）", platform, expires_in)
            return ok
        except Exception as e:  # noqa: BLE001
            logger.warning("save_token 失败: %s", e)
            return False

    def get_valid_token(self, platform: str) -> Optional[str]:
        """
        获取某平台仍在有效期内的 token。
        若临近过期（差 refresh_margin 秒），尝试走刷新逻辑；刷新失败返回 None。
        """
        try:
            session = self._sessions.get(platform)
            if not session:
                return None
            now = time.time()
            expires_at = float(session.get("expires_at", 0))
            # 已过期
            if now >= expires_at:
                logger.info("%s 的 token 已过期，尝试刷新", platform)
                return self._try_refresh(platform)
            # 临近过期 → 提前刷新
            if expires_at - now <= self.refresh_margin:
                logger.info("%s 的 token 临近过期，提前刷新", platform)
                refreshed = self._try_refresh(platform)
                if refreshed:
                    return refreshed
                # 刷新失败但原 token 还没完全过期，先用着
                return session.get("token")
            return session.get("token")
        except Exception as e:  # noqa: BLE001
            logger.warning("get_valid_token 失败: %s", e)
            return None

    def _try_refresh(self, platform: str) -> Optional[str]:
        """
        尝试用 refresh_token 换发新 token 并更新持久化会话。

        流程：
          1. 无 refresh_token → 触发重新登录回调，返回 None；
          2. 有 refresh_token → 优先调用注册的刷新回调；未注册时若 extra 里配置
             了 refresh_url，则走通用 HTTP 刷新；
          3. 拿到新 token → 更新 token/issued_at/expires_at（并轮换 refresh_token），
             写回 auth.json，返回新 token；
          4. 任一步失败/异常 → 兜底返回 None（不抛栈）。
        """
        try:
            session = self._sessions.get(platform)
            if not session:
                return None
            extra = session.get("extra") or {}
            refresh_token = extra.get("refresh_token")
            if not refresh_token:
                # 没有 refresh_token → 触发重新登录回调
                logger.info("%s 无 refresh_token，触发重新登录流程", platform)
                self._trigger_relogin(platform)
                return None

            new = self._exchange_refresh(platform, refresh_token, extra)
            if not isinstance(new, dict) or not new.get("token"):
                logger.info("%s 刷新未拿到新 token（回调未配置或失败）", platform)
                return None

            # 用新 token 更新会话
            now = time.time()
            try:
                expires_in = int(new.get("expires_in", 7200))
            except (TypeError, ValueError):
                expires_in = 7200
            session["token"] = str(new["token"])
            session["issued_at"] = now
            session["expires_at"] = now + expires_in
            # 服务端可能下发了新的 refresh_token（轮换），更新它
            new_rt = new.get("refresh_token")
            if new_rt:
                extra["refresh_token"] = new_rt
            session["extra"] = extra
            self._sessions[platform] = session
            self._flush_auth_file()
            logger.info("%s token 已刷新（新有效期 %ds）", platform, expires_in)
            return session["token"]
        except Exception as e:  # noqa: BLE001
            logger.warning("刷新 %s token 失败: %s", platform, e)
            return None

    def _exchange_refresh(
        self, platform: str, refresh_token: str, extra: dict
    ) -> Optional[dict]:
        """
        实际执行 refresh_token → 新 token 的交换。

        优先走注册的自定义刷新回调（平台差异由上层实现）；未注册且 extra 里
        配置了 ``refresh_url`` 时，退化为通用 HTTP JSON 刷新。任一路径异常都
        返回 None，由调用方按「刷新失败」处理。
        """
        # 1) 注册的自定义刷新回调（首选，平台相关逻辑在这里）
        cb = self._refresh_callback
        if callable(cb):
            try:
                out = cb(platform, refresh_token)
                if isinstance(out, dict) and out.get("token"):
                    return out
            except Exception as e:  # noqa: BLE001
                logger.warning("%s 刷新回调异常: %s", platform, e)

        # 2) 通用 HTTP 刷新端点（仅当 extra.refresh_url 配置了才走）
        url = extra.get("refresh_url")
        if url:
            try:
                import urllib.request
                payload = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
                req = urllib.request.Request(
                    url, data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("token"):
                    return data
            except Exception as e:  # noqa: BLE001
                logger.warning("%s HTTP 刷新端点调用失败: %s", platform, e)
        return None

    def force_refresh(self, platform: str) -> Optional[str]:
        """
        上层在收到 401/被踢下线后调用：用 refresh_token 强制刷新一次并返回新 token。

        内部即 ``_try_refresh``。刷新失败返回 None（调用方应走重新登录流程）。
        """
        return self._try_refresh(platform)

    def invalidate_token(self, platform: str) -> bool:
        """主动使某平台 token 失效（登出/被踢下线时调用）。"""
        try:
            if platform in self._sessions:
                del self._sessions[platform]
                self._flush_auth_file()
                logger.info("已失效 %s 的 token", platform)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("invalidate_token 失败: %s", e)
            return False

    # ------------------------------------------------------------------ #
    # 验证码类型识别
    # ------------------------------------------------------------------ #
    @staticmethod
    def classify_captcha_type(ocr_text: str, image_size: tuple = (0, 0)) -> CaptchaType:
        """
        根据 OCR 文本与图片尺寸粗略判断验证码类型。
        - 含「滑块」「拖动」「拼图」字样 → SLIDER
        - 纯数字串（4-6 位）→ NUMBER
        - 含中英字母混排短串 → TEXT
        - 含「等于」「计算」「请输入结果」→ GRAPHIC
        - 其余 → UNKNOWN
        """
        try:
            t = (ocr_text or "").strip()
            if not t:
                return CaptchaType.UNKNOWN
            # 滑块特征词
            slider_keywords = ["滑块", "拖动", "拼图", "滑动", "向右", "拖拽"]
            if any(k in t for k in slider_keywords):
                return CaptchaType.SLIDER
            # 算术图形验证码
            graphic_keywords = ["等于", "计算", "请输入结果", "算式", "相加", "相乘"]
            if any(k in t for k in graphic_keywords):
                return CaptchaType.GRAPHIC
            # 纯数字提取
            digits = re.sub(r"\D", "", t)
            # 去掉常见干扰词后再判断
            cleaned = re.sub(r"[验证码|输入|请|点击|确认|提交]", "", t)
            cleaned_digits = re.sub(r"\D", "", cleaned)
            if 3 <= len(cleaned_digits) <= 8 and not re.search(r"[A-Za-z一-鿿]", cleaned):
                return CaptchaType.NUMBER
            # 含字母/中文短串 → 文字验证码
            if re.search(r"[A-Za-z]", cleaned) or re.search(r"[一-鿿]", cleaned):
                return CaptchaType.TEXT
            return CaptchaType.UNKNOWN
        except Exception as e:  # noqa: BLE001
            logger.debug("classify_captcha_type 失败: %s", e)
            return CaptchaType.UNKNOWN

    # ------------------------------------------------------------------ #
    # 验证码识别主入口
    # ------------------------------------------------------------------ #
    def recognize_captcha(self, image_path: str) -> dict:
        """
        识别验证码图片，返回结构化结果。
        :return: {
            "code": 识别出的验证码字符串（可能为空）,
            "type": CaptchaType.value,
            "strategy": 使用的识别策略（ocr / offline_rules / unknown）,
            "screenshot": 图片路径,
            "error": 错误信息（可选）
        }
        """
        result: dict[str, Any] = {
            "code": "",
            "type": CaptchaType.UNKNOWN.value,
            "strategy": "unknown",
            "screenshot": image_path,
        }
        try:
            if not image_path or not os.path.exists(image_path):
                result["error"] = f"验证码图片不存在: {image_path}"
                return result

            # 第一步：尝试 OCR
            ocr_text = ""
            if self.ocr is not None:
                try:
                    ocr_result = self.ocr.recognize_image(image_path) or {}
                    ocr_text = str(ocr_result.get("text", ""))
                    result["strategy"] = "ocr"
                except Exception as e:  # noqa: BLE001
                    logger.warning("OCR 识别验证码失败，降级离线规则: %s", e)
                    result["strategy"] = "offline_rules"

            # 第二步：判断验证码类型
            cap_type = self.classify_captcha_type(ocr_text)
            result["type"] = cap_type.value

            # 第三步：按类型提取验证码文本
            code = self._extract_code_by_type(ocr_text, cap_type)
            result["code"] = code

            # 第四步：OCR 没出结果时，走离线规则兜底
            if not code:
                fallback_code = self._offline_fallback(image_path, cap_type)
                if fallback_code:
                    result["code"] = fallback_code
                    result["strategy"] = "offline_rules"
                    logger.info("离线规则兜底识别出验证码: %s", fallback_code)

            return result
        except Exception as e:  # noqa: BLE001
            logger.warning("recognize_captcha 失败: %s", e)
            result["error"] = str(e)
            return result

    def _extract_code_by_type(self, ocr_text: str, cap_type: CaptchaType) -> str:
        """根据验证码类型从 OCR 文本中提取验证码字符串。"""
        try:
            t = (ocr_text or "").strip()
            if not t:
                return ""
            if cap_type == CaptchaType.NUMBER:
                # 只取数字，连续 4-6 位
                nums = re.findall(r"\d{4,6}", t)
                if nums:
                    return nums[0]
                # 退化：把所有数字拼起来取前 6 位
                digits = re.sub(r"\D", "", t)
                return digits[:6]
            if cap_type == CaptchaType.TEXT:
                # 去掉常见 UI 词，取最像验证码的短 token
                stop_words = ["验证码", "输入", "请", "点击", "确认", "提交",
                              "登录", "登录", "Refresh", "refresh", "验证码："]
                cleaned = t
                for w in stop_words:
                    cleaned = cleaned.replace(w, " ")
                tokens = [tok.strip() for tok in re.split(r"\s+", cleaned) if tok.strip()]
                for tok in tokens:
                    if 3 <= len(tok) <= 8 and re.search(r"[A-Za-z0-9]", tok):
                        return tok
                # 退化：第一个看起来像 token 的
                for tok in tokens:
                    if re.search(r"[A-Za-z0-9]", tok):
                        return tok
                return ""
            if cap_type == CaptchaType.GRAPHIC:
                # 算术题：尝试解 a+b / a-b / a*b
                m = re.search(r"(\d+)\s*([+\-x×*])\s*(\d+)", t)
                if m:
                    a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
                    if op in ("+",):
                        return str(a + b)
                    if op == "-":
                        return str(a - b)
                    if op in ("x", "×", "*"):
                        return str(a * b)
                return ""
            if cap_type == CaptchaType.SLIDER:
                # 滑块验证码没有"文本答案"，返回空串，由上层做拖动
                return ""
            return ""
        except Exception as e:  # noqa: BLE001
            logger.debug("_extract_code_by_type 失败: %s", e)
            return ""

    def _offline_fallback(self, image_path: str, cap_type: CaptchaType) -> str:
        """
        OCR 不可用/识别失败时的离线兜底。
        当前实现：基于 PIL 的基础像素统计 + 规则猜测，能出结果就出，不能就空。
        """
        try:
            # 滑块：无法离线求解，返回空
            if cap_type == CaptchaType.SLIDER:
                return ""
            # 尝试用 PIL 读取图片基本信息，做最简单的灰度统计
            try:
                from PIL import Image  # type: ignore
            except ImportError:
                return ""
            try:
                img = Image.open(image_path)
                w, h = img.size
                # 小尺寸（< 200px 高）基本就是验证码图
                if h > 200:
                    return ""
                # 转灰度看明暗分布（粗判，不出具体字符）
                gray = img.convert("L")
                pixels = list(gray.getdata())
                dark_ratio = sum(1 for p in pixels if p < 128) / max(1, len(pixels))
                # 暗色像素占比 0.1~0.5 之间认为有字符
                if 0.05 < dark_ratio < 0.6:
                    logger.debug("离线兜底：图片含字符特征（dark_ratio=%.2f），但无 OCR 无法给出具体值", dark_ratio)
                return ""
            except Exception as e:  # noqa: BLE001
                logger.debug("PIL 离线兜底失败: %s", e)
                return ""
        except Exception as e:  # noqa: BLE001
            logger.debug("_offline_fallback 失败: %s", e)
            return ""

    # ------------------------------------------------------------------ #
    # 登录状态监控
    # ------------------------------------------------------------------ #
    def check_session_alive(self, platform: str) -> bool:
        """检查某平台会话是否仍有效（不触发刷新，纯判断）。"""
        try:
            session = self._sessions.get(platform)
            if not session:
                return False
            now = time.time()
            expires_at = float(session.get("expires_at", 0))
            return now < expires_at
        except Exception as e:  # noqa: BLE001
            logger.debug("check_session_alive 失败: %s", e)
            return False

    def monitor_loop_once(self) -> dict:
        """
        一次会话巡检：遍历所有平台，找出过期/临期的，触发刷新或重新登录。
        通常由上层定时器周期调用。
        :return: {"checked": 平台数, "expired": [...], "refreshed": [...], "relogin_triggered": [...]}
        """
        report = {"checked": 0, "expired": [], "refreshed": [], "relogin_triggered": []}
        try:
            platforms = list(self._sessions.keys())
            report["checked"] = len(platforms)
            for platform in platforms:
                try:
                    if not self.check_session_alive(platform):
                        report["expired"].append(platform)
                        # 尝试刷新
                        new_token = self._try_refresh(platform)
                        if new_token:
                            report["refreshed"].append(platform)
                        else:
                            # 刷新失败 → 触发重新登录
                            self._trigger_relogin(platform)
                            report["relogin_triggered"].append(platform)
                except Exception as e:  # noqa: BLE001
                    logger.warning("巡检 %s 失败: %s", platform, e)
            return report
        except Exception as e:  # noqa: BLE001
            logger.warning("monitor_loop_once 失败: %s", e)
            return report

    # ------------------------------------------------------------------ #
    # 回调注册
    # ------------------------------------------------------------------ #
    def set_relogin_callback(self, callback: Callable[[str], bool]) -> None:
        """
        注册重新登录回调。session 过期且刷新失败时调用。
        :param callback: 入参 platform 名，返回是否重新登录成功
        """
        self._relogin_callback = callback
        logger.info("已注册重新登录回调")

    def set_verification_code_callback(self, callback: Callable[[str], Optional[str]]) -> None:
        """
        注册验证码回调（供 wechat_operator 在登录流程中调用）。
        :param callback: 入参图片路径，返回识别出的验证码字符串
        """
        self._provide_code_callback = callback
        logger.info("已注册验证码识别回调")

    def set_refresh_callback(self, callback: Callable[[str, str], Optional[dict]]) -> None:
        """
        注册 token 刷新回调（用 refresh_token 换发新 token）。

        :param callback: 签名 ``callback(platform, refresh_token) -> Optional[dict]``，
            返回 dict 至少含 ``token``，可选 ``expires_in``(秒) /
            ``refresh_token``(轮换后的新 refresh_token)。返回 None 或抛异常
            都视为刷新失败，由本类兜底。
        """
        self._refresh_callback = callback
        logger.info("已注册 token 刷新回调")

    def provide_verification_code(self, image_path: str) -> Optional[str]:
        """
        供 wechat_operator 调用的验证码识别入口。
        若上层已注册自定义回调则走自定义，否则走默认 recognize_captcha。
        """
        try:
            if self._provide_code_callback is not None:
                return self._provide_code_callback(image_path)
            result = self.recognize_captcha(image_path)
            return result.get("code") or None
        except Exception as e:  # noqa: BLE001
            logger.warning("provide_verification_code 失败: %s", e)
            return None

    def _trigger_relogin(self, platform: str) -> bool:
        """触发重新登录回调。"""
        try:
            if self._relogin_callback is None:
                logger.info("%s 需要重新登录，但未注册回调，跳过", platform)
                return False
            ok = bool(self._relogin_callback(platform))
            logger.info("%s 重新登录回调返回: %s", platform, ok)
            return ok
        except Exception as e:  # noqa: BLE001
            logger.warning("触发重新登录失败: %s", e)
            return False

    # ------------------------------------------------------------------ #
    # 状态查询
    # ------------------------------------------------------------------ #
    def get_status(self) -> dict:
        """返回 AuthHelper 当前状态（供 UI/日志展示）。"""
        try:
            platforms = {}
            now = time.time()
            for name, sess in self._sessions.items():
                expires_at = float(sess.get("expires_at", 0))
                platforms[name] = {
                    "alive": now < expires_at,
                    "expires_in": max(0, int(expires_at - now)),
                    "has_refresh_token": bool((sess.get("extra") or {}).get("refresh_token")),
                }
            return {
                "ocr_attached": self.ocr is not None,
                "auth_file": self.auth_file,
                "platforms": platforms,
                "refresh_margin": self.refresh_margin,
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("get_status 失败: %s", e)
            return {"error": str(e)}
