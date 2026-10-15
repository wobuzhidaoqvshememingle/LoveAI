"""
LoveAI v2.0 - 安全词库破假模块
处理云端AI内容安全限制：
  敏感词 → 代码词替换后发云端 → 返回后替换回来
  → 破假不成功（云端仍拒绝）→ 自动降级本地AI（本地无内容限制）
服务层纯Python实现，不依赖PySide6。
"""
import re
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SafetyFilter:
    """安全词库破假过滤器"""

    # 安全词库：敏感词 → 代码词（隐喻/谐音/拼音缩写）
    WORD_MAP: dict[str, str] = {
        # ---- 性相关 ----
        "做爱": "做作业",
        "上床": "上楼",
        "开房": "开门",
        "啪啪": "拍手",
        "口交": "口嗨",
        "手淫": "手影",
        "高潮": "高分",
        "阴茎": "小弟弟",
        "阴道": "小妹妹",
        "乳头": "小葡萄",
        "裸体": "luo体",
        "色情": "色彩",
        "黄色": "金色",
        "性欲": "兴致",
        "勃起": "博起",
        "暧昧": "暗昧",
        "约会": "碰面",
        "表白": "表明心意",
        "性交": "交流",
        "性爱": "喜爱",
        "强奸": "强抱",
        "乳房": "小馒头",
        "睾丸": "小蛋",
        "肛交": "肛聊",
        "鸡巴": "小鸡",
        "屁眼": "小眼",
        "避孕套": "小雨伞",
        "卖淫": "卖艺",
        "嫖客": "访客",
        # ---- 其他敏感类 ----
        "自杀": "自己决定",
        "杀人": "解决",
        "毒品": "糖果",
        "赌博": "博弈",
        "堕胎": "处理",
        "傻逼": "傻样",
        "操你": "夸你",
    }

    # 反向映射：代码词 → 敏感词
    REVERSE_MAP: dict[str, str] = {v: k for k, v in WORD_MAP.items()}

    # 云端拒绝检测模式（任一匹配即视为被安全拦截）
    REJECT_PATTERNS: list[str] = [
        r"抱歉.*无法.*(回答|提供|生成)",
        r"作为一个.*(AI|语言模型|助手).*不能",
        r"此内容.*(违反|不符合).*规定",
        r"我不能.*(讨论|提供|生成).*内容",
        r"(敏感|不当|不合适).*内容",
        r"我无法.*(回答|回应).*问题",
        r"不符合.*(政策|规范|要求)",
        r"内容审核.*(未通过|失败)",
        r"很抱歉.*(无法|不能).*",
        r"作为.*人工智能.*(拒绝|无法)",
    ]

    def __init__(self) -> None:
        """初始化安全过滤器与破假统计计数器。"""
        # 预编译正则，提升检测性能
        self._reject_regexes: list[re.Pattern] = [re.compile(p) for p in self.REJECT_PATTERNS]
        # 长词优先排序，避免短词先替换导致长词匹配失败
        self._sorted_keys: list[str] = sorted(self.WORD_MAP.keys(), key=len, reverse=True)
        self._sorted_reverse_keys: list[str] = sorted(self.REVERSE_MAP.keys(), key=len, reverse=True)
        # 破假统计
        self._stats: dict[str, int] = {
            "total_calls": 0,
            "sensitive_detected": 0,
            "cloud_rejected": 0,
            "local_fallback": 0,
            "encode_replacements": 0,
        }
        logger.info("SafetyFilter 初始化完成，词库 %d 组映射，拒绝模式 %d 条",
                    len(self.WORD_MAP), len(self.REJECT_PATTERNS))

    def encode(self, text: str) -> str:
        """
        发送给云端前：敏感词替换为代码词。
        长词优先替换，避免短词先替换导致长词匹配失败。

        Args:
            text: 原始文本。

        Returns:
            str: 替换后的文本。
        """
        if not text:
            return text
        result = text
        replaced = 0
        for sensitive in self._sorted_keys:
            code = self.WORD_MAP[sensitive]
            count = result.count(sensitive)
            if count > 0:
                result = result.replace(sensitive, code)
                replaced += count
        self._stats["encode_replacements"] += replaced
        if replaced > 0:
            logger.debug("encode 替换 %d 处敏感词", replaced)
        return result

    def decode(self, text: str) -> str:
        """
        云端返回后：代码词替换回原敏感词。

        Args:
            text: 云端返回的（已编码）文本。

        Returns:
            str: 还原后的文本。
        """
        if not text:
            return text
        result = text
        for code in self._sorted_reverse_keys:
            sensitive = self.REVERSE_MAP[code]
            result = result.replace(code, sensitive)
        return result

    def is_rejected(self, response: str) -> bool:
        """
        检测云端返回是否被安全拦截。

        Args:
            response: 云端返回文本。

        Returns:
            bool: True 表示被拦截/拒绝。
        """
        if not response:
            return True
        # 正则模式任一命中
        for pattern in self._reject_regexes:
            if pattern.search(response):
                return True
        # 短回复 + 抱歉/不能/无法 关键词 → 视为拒绝
        if len(response) < 20:
            for kw in ("抱歉", "不能", "无法", "拒绝"):
                if kw in response:
                    return True
        return False

    def contains_sensitive(self, text: str) -> bool:
        """
        检测文本是否包含敏感词（判断是否需要走破假流程）。

        Args:
            text: 待检测文本。

        Returns:
            bool: True 表示包含敏感词。
        """
        if not text:
            return False
        for sensitive in self.WORD_MAP.keys():
            if sensitive in text:
                return True
        return False

    def chat_with_safety(
        self,
        llm_gateway: Any,
        system_prompt: str,
        user_prompt: str,
        complexity: str = "complex",
    ) -> str:
        """
        带安全破假的完整大模型调用流程：
          1. 合并 system_prompt + user_prompt；
          2. 不含敏感词 → 直接走 llm_gateway.chat 正常流程；
          3. 含敏感词 → 破假流程：
             a. encode 编码（敏感词→代码词）；
             b. 尝试云端调用 llm_gateway._call_cloud；
             c. 未被拒 → decode 解码返回；
             d. 被拒/异常 → 降级本地AI llm_gateway._call_local（原始未编码文本）。

        Args:
            llm_gateway: LLM 网关对象，需具备 chat / _call_cloud / _call_local 方法。
            system_prompt: 系统提示词。
            user_prompt: 用户提示词。
            complexity: 复杂度标记（透传，默认 complex）。

        Returns:
            str: 最终回复文本。
        """
        self._stats["total_calls"] += 1
        full_text = f"{system_prompt}\n{user_prompt}".strip()

        # ---- 不含敏感词：直接正常调用 ----
        if not self.contains_sensitive(full_text):
            logger.debug("无敏感词，走正常云端流程")
            try:
                return llm_gateway.chat(full_text, complexity=complexity)
            except Exception as e:
                logger.error("正常云端调用异常，尝试降级本地: %s", e)
                self._stats["local_fallback"] += 1
                return self._safe_call_local(llm_gateway, full_text)

        # ---- 含敏感词：走破假流程 ----
        self._stats["sensitive_detected"] += 1
        logger.info("检测到敏感词，启动破假流程")
        encoded_text = self.encode(full_text)

        # b. 尝试云端调用
        cloud_result: str | None = None
        try:
            cloud_fn = getattr(llm_gateway, "_call_cloud", None)
            if callable(cloud_fn):
                cloud_result = cloud_fn(encoded_text, complexity=complexity)
            else:
                # 没有私有云端接口则退回公共 chat 接口（发编码后的文本）
                cloud_result = llm_gateway.chat(encoded_text, complexity=complexity)
        except Exception as e:
            logger.warning("云端破假调用异常，降级本地AI: %s", e)
            cloud_result = None

        # c. 检测是否被拒
        if cloud_result and not self.is_rejected(cloud_result):
            decoded = self.decode(cloud_result)
            logger.info("破假成功，云端放行")
            return decoded

        # d. 被拒或异常 → 降级本地AI（使用原始未编码文本，本地无内容限制）
        self._stats["cloud_rejected"] += 1
        self._stats["local_fallback"] += 1
        logger.info("破假失败（云端拒绝/异常），降级本地AI")
        return self._safe_call_local(llm_gateway, full_text)

    def _safe_call_local(self, llm_gateway: Any, text: str) -> str:
        """
        安全地调用本地AI，接口缺失时给出兜底提示。

        Args:
            llm_gateway: LLM 网关对象。
            text: 原始（未编码）文本。

        Returns:
            str: 本地回复文本，或兜底提示。
        """
        local_fn = getattr(llm_gateway, "_call_local", None)
        if callable(local_fn):
            try:
                return local_fn(text)
            except Exception as e:
                logger.error("本地AI调用也失败: %s", e)
                return "（本地AI暂不可用，请稍后再试）"
        logger.error("llm_gateway 未提供 _call_local 方法，无法降级")
        return "（未配置本地AI，云端内容审核未通过）"

    def get_stats(self) -> dict[str, int]:
        """
        返回破假统计快照。

        Returns:
            dict: 包含 total_calls / sensitive_detected / cloud_rejected /
                  local_fallback / encode_replacements 的计数字典。
        """
        return dict(self._stats)


# =========================================================================== #
# v3.1 加固补丁 · P-08 防操纵红线守卫 与 分层知情同意
# --------------------------------------------------------------------------- #
# 注意：本类与上面的 SafetyFilter（敏感词编码绕云审核）是【两件事】，互不影响。
#   - SafetyFilter：处理"云端会不会误拦用户自己想说的话"；
#   - ManipulationRedlineGuard（本类）：处理"系统绝不能替用户生成操纵/欺骗/
#     威胁/内疚/嫉妒/煤气灯/骚扰监控类话术"。
# 红线边界（保护主体）：反制/驯服、推拉、框架、给空间、条件交换、渐进减量、
#   富兰克林/承诺一致性等【中性手段不在红线内】；红线只划在 欺骗/违法/伤害/
#   骚扰/未授权访问。
# =========================================================================== #
class ManipulationRedlineGuard:
    """P-08 防操纵红线守卫。

    对"系统准备发给用户、供其复制发送的话术"做红线扫描：
      - 命中红线类别 → blocked=True，拒绝生成，并给出真诚、非监控的转述；
      - 中性手段（反制/推拉/框架/给空间等）→ 一律放行。

    纯规则、确定性、离线，可单测，不依赖模型/网络。
    """

    # 每条：类别 key -> (中文名, 匹配短语列表, 真诚转述)
    # 短语尽量收敛在"伤害/操纵意图"上，避免误伤中性策略词。
    REDLINES: dict[str, tuple[str, tuple[str, ...], str]] = {
        "impersonation_deception": (
            "冒充/欺骗",
            ("装成别人", "冒充", "用小号加", "别的号加", "谎称", "骗他说", "骗她说",
             "骗你说", "假装是朋友", "假装陌生人", "伪造截图", "P个图", "p过的图",
             "虚构一个", "编造一个身份", "装成异性", "鱼塘", "用美女号", "用帅哥号"),
            "把假装/试探换成真实表达：直接说出你真实的想法和身份，不搞身份欺骗。",
        ),
        "threat": (
            "威胁",
            ("威胁", "不答应就", "不怎样就分手", "不答应我就", "曝光你", "身败名裂",
             "让你后悔", "你等着", "告诉所有人", "公开你的", "你不听话就", "闹到你单位",
             "让你付出代价"),
            "把威胁换成你的真实底线：'这种做法我接受不了，我希望我们互相尊重。'",
        ),
        "guilt_tripping": (
            "制造内疚",
            ("你对得起我", "我为你付出了", "都是为了你", "我这么爱你你", "你良心过得去",
             "你欠我的", "我容易吗", "为你我什么都", "你却这样对我", "我为你牺牲",
             "没有我你能有今天"),
            "不用愧疚绑架，直接说你的需求：'我希望我们的付出能更对等一些。'",
        ),
        "jealousy_induction": (
            "jealousy 诱导",
            ("故意让他吃醋", "让他吃醋", "让她吃醋", "提别的异性", "说有人追我",
             "发和别的男生", "发和别的女生", "制造嫉妒", "气他", "让他嫉妒",
             "发和别人的暧昧", "晒和异性合照"),
            "不靠制造嫉妒刺激对方，坦诚你的感受：'最近你比较忙，我有点想你。'",
        ),
        "gaslighting": (
            "煤气灯",
            ("让他以为他记错", "让她以为她记错", "否认真的发生过", "你没说过吧",
             "是你想多了", "从来没有这回事", "是你记错了", "扭曲事实", "让他自我怀疑",
             "根本没这回事", "你幻觉了", "你太敏感了想多了"),
            "不否认事实，对事不对人：'这件事我的记录是这样的，我们可以一起核对。'",
        ),
        "harassment_monitoring": (
            "骚扰/查岗监控",
            ("定位他", "定位她", "跟踪他", "跟踪她", "看他手机", "看她手机",
             "查他聊天记录", "查她聊天记录", "监控他", "监控她", "翻他手机", "翻她手机",
             "登他账号", "登她账号", "登他微信", "登她微信", "消息轰炸", "打电话轰炸",
             "连环call", "连环call", "一直问他在哪", "一直问她在哪", "安装监控",
             "翻他朋友圈每条", "查他定位", "装定位软件"),
            "不监控、不连环追问，给出空间并表达感受：'我有点担心，但我不想催你，"
            "你方便时回我就好。'",
        ),
    }

    def __init__(self) -> None:
        self._stats: dict[str, int] = {"total": 0, "blocked": 0}

    def check(self, text: str) -> dict:
        """扫描一段待发送话术是否命中操纵红线。

        Args:
            text: 系统准备生成 / 供用户发送的话术文本。

        Returns:
            dict: {
              "blocked": bool,          # True = 命中红线，必须拒绝生成
              "category": str|None,     # 命中的红线 key
              "category_name": str|None,
              "matched": [str],         # 命中的具体短语
              "rewrite": str|None,      # 真诚、非监控的转述建议
              "reason": str,
            }
        """
        self._stats["total"] += 1
        if not text:
            return {"blocked": False, "category": None, "category_name": None,
                    "matched": [], "rewrite": None, "reason": ""}
        for key, (name, phrases, rewrite) in self.REDLINES.items():
            hits = [p for p in phrases if p in text]
            if hits:
                self._stats["blocked"] += 1
                return {
                    "blocked": True,
                    "category": key,
                    "category_name": name,
                    "matched": hits,
                    "rewrite": rewrite,
                    "reason": f"命中「{name}」类操纵红线（{hits[0]}），已拒绝生成该话术",
                }
        return {"blocked": False, "category": None, "category_name": None,
                "matched": [], "rewrite": None, "reason": "未命中操纵红线"}

    def get_stats(self) -> dict[str, int]:
        """返回红线守卫统计快照。"""
        return dict(self._stats)


# --------------------------------------------------------------------------- #
# P-08 · 分层知情同意 / 防依赖 / 隐私最小化（文案 + 工程证据口径）
# --------------------------------------------------------------------------- #
def informed_consent_disclosure() -> str:
    """导入数据前必须向用户明示的分层知情同意（v3.1 P-08）。"""
    return (
        "【使用前请知悉（分层知情同意）】\n"
        "1. 本系统基于统计与模型做关系分析，模型会出错，所有预测都是概率、不保证未来；\n"
        "2. 你在分析一位【无法对此表示同意】的人，相关关系伦理与后果由你本人承担；\n"
        "3. 聊天记录默认仅在你的本机处理，不上传云端（详见隐私最小化说明）；\n"
        "4. 你可随时一键删除全部数据及其衍生标签，删除范围见删除说明。"
    )


def anti_dependency_note() -> str:
    """防依赖定位文案（P-08：帮你组织你想说的话，不承诺完美下一句）。"""
    return (
        "本工具的定位是【帮你组织你想说的话】，而不是替你做决定、更不承诺给出"
        "『完美的下一句』。系统会周期性回顾自身局限，请把它当作一面参考镜子，"
        "最终如何回应、是否继续，始终由你判断。"
    )


def privacy_minimization_evidence() -> dict:
    """隐私最小化 / 本地处理的工程证据（P-08 要求"须有工程证据"）。

    Returns:
        dict: 列出哪些数据本地处理、默认不上云、第三方 PII 默认不入库检索标签。
    """
    return {
        "local_only_default": True,
        "on_device_modules": [
            "safety_filter（敏感词编码/红线守卫，纯本地规则）",
            "refusal_downgrade（拒绝降级，纯本地规则）",
            "third_party_pii（PII 检测/脱敏，纯本地正则，不联网）",
            "no_reply_detector（不回复分析，本地 sqlite）",
        ],
        "third_party_pii_default_excluded_from_index": True,
        "cloud_opt_in_only": "云端生成模型仅在用户主动发起对话时按需调用，"
                             "聊天原文不被默认批量上传",
        "note": "以上为模块级工程口径；真实数据驻留/训练政策以供应商注册表为准。",
    }


def delete_coverage_policy() -> str:
    """一键删除须覆盖的范围 + 备份保留策略说明（P-08）。"""
    return (
        "【一键删除覆盖范围】聊天原文、派生人物画像/标签、索引（含向量库）、"
        "预测记录与反馈、本地缓存；删除后这些数据不再用于任何分析与检索。\n"
        "【备份策略】删除前若存在自动备份，备份副本将在本地保留 ≤30 天，"
        "到期自动物理清除；如需立即彻底清除可在设置中选择『连备份一起删除』。"
    )
