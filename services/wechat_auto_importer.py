# -*- coding: utf-8 -*-
"""
微信一键自动导入器（在原骨架上完善）

流程：
  1. 版本/进程检测
  2. 自动密钥提取（低风险→高风险，调用 wechat_key_extractor）
  3. 数据库解密（调用 wechat_db_decryptor）
  4. 按联系人筛选读取（调用 wechat_db_reader）
  5. 合并去重 → 结果（由上层 wechat_service 持久化）
  6. 通过 event_bus 发布 wechat_import_progress / wechat_import_complete

保留原有方法签名：register_method / auto_import / get_progress / get_log / get_result
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class WechatAutoImporter:
    """一键自动导入器。

    向后兼容：
        register_method(mid, instance, category, priority)
        auto_import(person_id, enable_advanced=False) -> dict
        get_progress() -> dict
        get_log() -> list
        get_result() -> dict
    新增：
        cancel_import() / incremental_import() / set_event_bus()
    """

    def __init__(self,
                 key_extractor=None,
                 db_reader=None,
                 event_bus=None) -> None:
        self._methods: Dict[str, Dict[str, Any]] = {}
        self._log: List[Dict[str, Any]] = []
        self._result: Dict[str, Any] = {"messages": [], "used": [], "skipped": []}
        self._cancel = False
        self._advanced = False
        # 新增依赖（懒导入，避免循环依赖）
        self._extractor = key_extractor
        self._reader = db_reader
        self._bus = event_bus
        # 进度状态
        self._total_steps: int = 6
        self._current_step: int = 0
        self._current_step_name: str = ""
        self._message_count: int = 0

    # ------------------------------------------------------------------
    # 原有方法（保持签名不变）
    # ------------------------------------------------------------------
    def register_method(self, mid: str, instance,
                        category: str = "A", priority: int = 0) -> None:
        """注册一个导入方法实例（需提供 availability_check() 和 import_chats()）。"""
        self._methods[mid] = {
            "inst": instance,
            "category": category,
            "priority": priority,
            "advanced": category == "F",
        }

    def enable_advanced(self) -> None:
        self._advanced = True

    def cancel(self) -> None:
        self._cancel = True

    def _msg_hash(self, msg: dict) -> str:
        structured = f"{msg.get('msg_id','')}|{msg.get('create_time','')}|{msg.get('talker_id','')}|{msg.get('content','')[:64]}"
        # 有结构化字段时用结构化字段；无结构化字段（如旧测试的{"x":1}）时回退到str(msg)
        s = structured if structured.strip("|") else str(msg)
        return hashlib.md5(s.encode("utf-8", "ignore")).hexdigest()

    # ------------------------------------------------------------------
    # 新增方法
    # ------------------------------------------------------------------
    def set_event_bus(self, bus) -> None:
        self._bus = bus

    def cancel_import(self) -> None:
        """取消当前导入（对外别名）。"""
        self.cancel()

    def get_import_log(self) -> List[Dict[str, Any]]:
        return self.get_log()

    def incremental_import(self, contact_filter: Optional[List[str]] = None) -> Dict[str, Any]:
        """增量导入：只取上次导入后的新消息。"""
        return self.auto_import(person_id=None,
                                 contact_filter=contact_filter,
                                 incremental=True)

    # ------------------------------------------------------------------
    # 进度
    # ------------------------------------------------------------------
    def _set_step(self, idx: int, name: str) -> None:
        self._current_step = idx
        self._current_step_name = name
        pct = int(idx * 100 / max(self._total_steps, 1))
        progress = {
            "step": idx,
            "total_steps": self._total_steps,
            "current_step_name": name,
            "percentage": pct,
            "message_count": self._message_count,
        }
        self._log.append({"kind": "progress", **progress})
        if self._bus is not None:
            try:
                self._bus.publish("wechat_import_progress", progress)
            except Exception as e:  # noqa: BLE001
                logger.debug("事件总线发布失败: %s", e)

    # ------------------------------------------------------------------
    # 核心 auto_import
    # ------------------------------------------------------------------
    def auto_import(self,
                    person_id: Optional[str] = None,
                    enable_advanced: bool = False,
                    contact_filter: Optional[List[str]] = None,
                    incremental: bool = False,
                    enable_high_risk: bool = False) -> Dict[str, Any]:
        """一键导入。

        Args:
            person_id: 兼容旧签名；按人物过滤时由外部传入
            enable_advanced: 兼容旧签名
            contact_filter: 只导入这些 wxid/群id
            incremental: True 时只取新消息
            enable_high_risk: True 时允许 DLL 注入等高风险方法
        """
        self._log = []
        self._result = {"messages": [], "used": [], "skipped": []}
        self._message_count = 0
        # 向后兼容：如果之前调用了 cancel()，本次导入直接返回空（旧行为），
        # 然后重置 _cancel 以便后续正常导入。
        if self._cancel:
            self._cancel = False
            self._result["total"] = 0
            return self._result
        if enable_advanced or enable_high_risk:
            self.enable_advanced()

        # 先跑已注册的 legacy 方法（保持原行为）
        self._run_legacy_methods()
        if self._cancel:
            return self._result

        # 再跑新的多路径 DB 导入（extractor + reader）
        if self._extractor is not None and self._reader is not None:
            try:
                self._run_db_pipeline(contact_filter=contact_filter,
                                      incremental=incremental,
                                      enable_high_risk=enable_high_risk)
            except Exception as e:  # noqa: BLE001
                logger.exception("DB 管线异常: %s", e)
                self._log.append({"kind": "step", "ok": False, "reason": str(e)})

        self._result["total"] = len(self._result["messages"])
        if self._bus is not None:
            try:
                self._bus.publish("wechat_import_complete",
                                  {"total": self._result["total"],
                                   "used": self._result["used"]})
            except Exception:
                pass
        return self._result

    # ------------------------------------------------------------------
    # 内部：legacy 已注册方法
    # ------------------------------------------------------------------
    def _run_legacy_methods(self) -> None:
        seen = set(m["hash"] for m in self._result["messages"] if "hash" in m)
        for mid, m in sorted(self._methods.items(),
                             key=lambda x: (x[1]["category"], x[1]["priority"])):
            if self._cancel:
                break
            if m["advanced"] and not self._advanced:
                self._log.append({"method": mid, "ok": False, "reason": "F类默认禁用"})
                self._result["skipped"].append(mid)
                continue
            t0 = time.time()
            try:
                avail = m["inst"].availability_check()
                if not avail.get("available"):
                    self._log.append({"method": mid, "ok": False,
                                      "reason": avail.get("reason", "不可用")})
                    continue
                msgs = m["inst"].import_chats() or []
                new_count = 0
                for msg in msgs:
                    k = self._msg_hash(msg)
                    if k in seen:
                        continue
                    seen.add(k)
                    msg = dict(msg)
                    msg["hash"] = k
                    self._result["messages"].append(msg)
                    new_count += 1
                self._result["used"].append({"method": mid, "count": new_count})
                self._message_count += new_count
                self._log.append({"method": mid, "ok": True, "count": new_count,
                                  "elapsed_ms": int((time.time() - t0) * 1000)})
            except Exception as e:  # noqa: BLE001
                self._log.append({"method": mid, "ok": False, "reason": str(e)})

    # ------------------------------------------------------------------
    # 内部：新 DB 管线
    # ------------------------------------------------------------------
    #: 单账号最多收集的消息条数（防止一次把整库灌进内存；截断会写进 log）
    MAX_DB_MESSAGES_PER_ACCOUNT = 200000

    def _run_db_pipeline(self,
                         contact_filter: Optional[List[str]],
                         incremental: bool,
                         enable_high_risk: bool) -> None:
        """多路径 DB 导入管线——统一走 WechatOneClick 调度。

        【本次修复的三个真断点】
        1. 不再硬编码 `E:\\xwechat_files`，也不再"只取 os.listdir 里第一个 wxid_ 目录"：
           改为扫描全部候选根 + **全部账号**（多账号不再丢失，换机不再失效）。
        2. 不再先要求 `detect_wechat_process()` 拿到 pid 才继续：
           有缓存密钥时本来就能离线导入，进程信息现在只作为版本判定佐证。
        3. 不再只读 `message_*.db`：交给 WechatOneClick 处理账号下**全部** db
           （contact / session / media 等能解的都解），逐库成功失败可查。

        红线：wx_key.dll / frida / chatlog_alpha 等注入类动作在 enable_high_risk 为假时
        永不触发；`enable_high_risk=True` 也只是放开低风险阶梯里的内存扫描，
        不会由本方法拉起注入窗口（那必须走 UI 侧的显式确认入口）。
        """
        from .wechat_oneclick import WechatOneClick

        self._set_step(1, "发现微信数据库（多根/多账号）")
        reader_cls = getattr(self._reader, "WeChatDBReader", None)
        if reader_cls is None:
            try:
                from .wechat_db_reader import WeChatDBReader as reader_cls  # noqa: F811
            except Exception as e:  # noqa: BLE001
                self._log.append({"kind": "step", "ok": False,
                                  "reason": f"DB 读取器不可用: {e}"})
                return

        svc = WechatOneClick(key_extractor=self._extractor, reader_cls=reader_cls)

        self._set_step(2, "取密钥 + 批量解密（缓存优先，微信未运行也可）")
        try:
            out = svc.collect_messages(
                contact_filter=contact_filter,
                incremental=incremental,
                consent_high_risk=enable_high_risk,
                max_messages_per_account=self.MAX_DB_MESSAGES_PER_ACCOUNT,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("DB 管线执行异常: %s", e)
            self._log.append({"kind": "step", "ok": False, "reason": str(e)})
            return

        # 把一键总控的逐步留痕搬进本导入器的 log（含每条密钥路线 / 每个库的结果）
        for line in (out.get("log") or []):
            self._log.append({"kind": "step", "detail": line})
        for err in (out.get("errors") or []):
            self._log.append({"kind": "step", "ok": False,
                              "account": err.get("account_id"),
                              "reason": err.get("reason"),
                              "next_action": err.get("next_action")})

        per_account = out.get("per_account") or []
        if per_account:
            for pa in per_account:
                self._log.append({
                    "kind": "step",
                    "ok": bool(pa.get("dbs_ok")),
                    "account": pa.get("account_id"),
                    "dbs_total": pa.get("dbs_total"),
                    "dbs_ok": pa.get("dbs_ok"),
                    "dbs_failed": pa.get("dbs_failed"),
                    "key_method": pa.get("key_method"),
                    "messages": pa.get("messages"),
                })

        self._set_step(4, "读取消息（按联系人筛选）")
        self._set_step(5, "合并去重")
        seen = set(m.get("hash") for m in self._result["messages"] if "hash" in m)
        new_count = 0
        for msg in (out.get("messages") or []):
            k = self._msg_hash(msg)
            if k in seen:
                continue
            seen.add(k)
            msg = dict(msg)
            msg["hash"] = k
            self._result["messages"].append(msg)
            new_count += 1
        self._message_count += new_count
        for pa in per_account:
            self._result["used"].append({
                "method": f"db:{pa.get('key_method') or 'unknown'}@{pa.get('account_id')}",
                "count": pa.get("messages", 0),
            })
        if not per_account:
            self._log.append({"kind": "step", "ok": False,
                              "reason": "未发现任何可导入的微信账号目录"})

        self._set_step(6, "完成")

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def get_progress(self) -> dict:
        return {
            "tried": len(self._log),
            "total": len(self._methods),
            "success": sum(1 for l in self._log if l.get("ok")),
            "step": self._current_step,
            "total_steps": self._total_steps,
            "current_step_name": self._current_step_name,
            "percentage": int(self._current_step * 100 / max(self._total_steps, 1)),
            "message_count": self._message_count,
        }

    def get_log(self) -> list:
        return self._log

    def get_result(self) -> dict:
        return self._result


__all__ = ["WechatAutoImporter"]
