"""
LoveAI v2 - R3.3-42 HuggingFace 公开 SSH 聊天通道（占位/已探测不可用）
====================================================================

设计目标
--------
原设想：通过 SSH 到 HuggingFace 公开端点（如 ``chat.hf.co`` / ``benchmark-hf.co``），
免 API Key 借宿公共沙箱里的 ``transformers`` / ``huggingface_hub`` 跑推理，
作为零配置兜底的第六通道。

实测结论（2026-10-10，Windows PowerShell 本机）
---------------------------------------------
::

    ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o BatchMode=yes \\
        whatever@chat.hf.co exit          # exit=255，连接被拒/DNS 不通
    ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o BatchMode=yes \\
        whatever@benchmark-hf.co exit     # exit=255，同上

结论：**当前网络下这两个域名根本没有开放的 SSH 聊天入口**（HuggingFace 官方
未公开提供 ssh 到 chat 的服务；``huggingface.co`` 主站 HTTP 也在 6s 内超时）。

因此本模块按需求写成 **disabled 占位实现**：
    * 类 :class:`HFSSHChannel` 对外保留 ``chat()`` 接口；
    * ``chat()`` 在 disabled 状态下直接 :class:`RuntimeError`，不静默假答；
    * 在 ``llm_gateway`` 的降级链里把该层标为 ``disabled``，不参与自动轮询；
    * 若未来用户自己搭好跳板/配置了 SSH config，把
      :data:`HFSSHChannel.ENABLED` 置 True 并填好 ``host/user/key_path`` 即可启用。

什么时候这个通道会真正启用？
----------------------------
1. 用户自己在 ``~/.ssh/config`` 里配置了能登录的跳板（例如公司内网堡垒机 -> HF 容器）；
2. 跳板上跑着一个能执行 ``python -c "from transformers import ...; ..."`` 的沙箱；
3. 已安装 ``paramiko``（``pip install paramiko``）；
4. 在 ``config.json`` 里把 ``llm.free.hf_ssh.enabled`` 置 ``true``，并填：
   ``llm.free.hf_ssh.host`` / ``port`` / ``user`` / ``key_path`` / ``remote_model``。

在没有上述条件之前，本通道永远 disabled，不会污染降级链。
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# 探测结果缓存：2026-10-10 实测两个域名均不通。
# 这是模块级常量，测试/UI 直接读它判断是否展示该通道。
SSH_PROBE_RESULTS: dict = {
    "chat.hf.co": {"reachable": False, "exit_code": 255,
                   "note": "ssh whatever@chat.hf.co exit -> 255 (连接被拒/DNS 不通)"},
    "benchmark-hf.co": {"reachable": False, "exit_code": 255,
                        "note": "ssh whatever@benchmark-hf.co exit -> 255 (连接被拒/DNS 不通)"},
}


class HFSSHChannelDisabled(RuntimeError):
    """HF SSH 通道当前被 disabled（未探测到公开端点 / 未配置跳板）。"""


class HFSSHChannel:
    """免 Key 的 HuggingFace SSH 聊天通道（当前 disabled 占位）。

    使用方式::

        ch = HFSSHChannel(config)
        try:
            text = ch.chat("你是助手", "你好")
        except HFSSHChannelDisabled as e:
            # 降级到下一层（ollama / free router / 规则模板）
            ...
    """

    #: 模块级总开关；False = 当前环境探测不到公开 SSH 端点，chat() 一律 raise。
    ENABLED: bool = False

    def __init__(self, config=None) -> None:
        self.config = config
        # 从 config 读用户是否强制开启（默认关）
        try:
            self._forced_on = bool(
                config and getattr(config, "get", None)
                and config.get("llm.free.hf_ssh.enabled", False)
            )
        except Exception:  # noqa: BLE001
            self._forced_on = False

        # 用户自配跳板时读这些字段；未配置即空
        self.host: str = ""
        self.port: int = 22
        self.user: str = ""
        self.key_path: str = ""
        self.remote_model: str = ""
        if self._forced_on:
            try:
                self.host = str(config.get("llm.free.hf_ssh.host", "") or "")
                self.port = int(config.get("llm.free.hf_ssh.port", 22) or 22)
                self.user = str(config.get("llm.free.hf_ssh.user", "") or "")
                self.key_path = str(config.get("llm.free.hf_ssh.key_path", "") or "")
                self.remote_model = str(
                    config.get("llm.free.hf_ssh.remote_model", "") or "")
            except Exception as e:  # noqa: BLE001
                logger.warning("读取 hf_ssh 配置异常，回退 disabled: %s", e)
                self._forced_on = False

    # ------------------------------------------------------------------ #
    # 状态查询
    # ------------------------------------------------------------------ #
    def is_enabled(self) -> bool:
        """通道是否启用。当前默认 False（公开端点未探测到）。"""
        if not self._forced_on:
            return False
        # 用户强制开启时，至少要配了 host/user 才算真启用
        return bool(self.host and self.user)

    def status(self) -> dict:
        """给 UI/设置页看的状态快照。"""
        return {
            "name": "huggingface_ssh",
            "enabled": self.is_enabled(),
            "disabled_reason": ("公开 SSH 端点 chat.hf.co/benchmark-hf.co "
                                "实测不可达（exit=255）；需用户自配跳板"),
            "probe": SSH_PROBE_RESULTS,
            "host": self.host,
            "port": self.port,
            "model": self.remote_model,
        }

    # ------------------------------------------------------------------ #
    # 聊天主入口
    # ------------------------------------------------------------------ #
    def chat(self, system_prompt: str, user_prompt: str,
             temperature: float = 0.7, max_tokens: int = 512) -> str:
        """通过 SSH 到远端 HF 沙箱跑 transformers 聊天。

        当前实现：disabled -> 直接 raise :class:`HFSSHChannelDisabled`，
        由调用方降级到下一层（ollama / free router / 规则模板）。

        未来启用条件见模块 docstring；启用后应使用 paramiko：
        ::

            import paramiko
            cli = paramiko.SSHClient()
            cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            cli.connect(self.host, port=self.port,
                        username=self.user, key_filename=self.key_path, timeout=10)
            stdin, stdout, stderr = cli.exec_command(remote_cmd)
        """
        if not self.is_enabled():
            raise HFSSHChannelDisabled(
                "HF SSH endpoint not reachable in current network "
                "(chat.hf.co / benchmark-hf.co exit=255); "
                "需用户在 config.llm.free.hf_ssh 自配跳板后再启用。")
        # 真启用分支（用户自配跳板）：预留 paramiko 调用骨架。
        # 当前没有可连的端点，故只做接口占位，不发真实请求。
        raise HFSSHChannelDisabled(
            "HF SSH channel enabled in config but paramiko/remote "
            "execution stub not yet wired; please report this if you "
            "have a working bastion.")


# ---------------------------------------------------------------------- #
# llm_gateway 降级链钩子：标记本通道在链中的状态
# ---------------------------------------------------------------------- #
def describe_for_chain() -> dict:
    """llm_gateway 用这个 dict 决定是否把 hf_ssh 挂进降级链。

    当前永远返回 ``enabled=False``，即「不挂进自动降级链」。
    未来用户配好跳板后，把 :data:`HFSSHChannel.ENABLED` 与 config 同时打开即可。
    """
    return {
        "layer": "hf_ssh",
        "enabled": False,
        "reason": "公开 SSH 端点实测不可达；待用户自配跳板后启用",
        "fallback_to": ["l2_ollama", "free_router", "rules_template"],
    }
