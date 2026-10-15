# -*- coding: utf-8 -*-
"""CollectorRegistry：枚举全部采集通道、真实可用性检测、推荐排序、统一运行入库。

通道发现采用"全清单 + 容错导入"：清单即权威通道总表；尚未实现的模块记录为
加载失败，已实现的自动纳入，不硬编码可用性。
"""
from __future__ import annotations

import importlib
import logging
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence

from .base import (SourceAdapter, Message, Availability,
                   RISK_NONE, RISK_LOW, RISK_MEDIUM, RISK_HIGH)

logger = logging.getLogger(__name__)

# (相对模块路径, 类名, 通道编号) —— 权威通道清单
MODULE_SPECS: List[tuple] = [
    # ---- PC ----
    (".pc.p1_sqlcipher", "P1SqlcipherDirectAdapter", "P1"),
    (".pc.p2_memory_key", "P2MemoryKeyAdapter", "P2"),
    (".pc.p3_minidump_oracle", "P3MiniDumpOracleAdapter", "P3"),
    (".pc.p4_hiberfil", "P4HiberfilAdapter", "P4"),
    (".pc.p5_bsod_dump", "P5BsodDumpAdapter", "P5"),
    (".pc.p6_passive_vision", "P6PassiveVisionAdapter", "P6"),
    (".pc.p7_auto_vision", "P7AutoVisionAdapter", "P7"),
    (".pc.p8_uia", "P8UiaAdapter", "P8"),
    (".pc.p9_frida_hook", "P9FridaHookAdapter", "P9"),
    (".pc.p10_downgrade", "P10DowngradeAdapter", "P10"),
    (".pc.p11_webwechat", "P11WebWechatAdapter", "P11"),
    # ---- 安卓 ----
    (".android.a1_adb_uia", "A1AdbUiaAdapter", "A1"),
    (".android.a2_adb_ocr", "A2AdbOcrAdapter", "A2"),
    (".android.a3_accessibility_export", "A3AccessibilityExportAdapter", "A3"),
    (".android.a4_root_db", "A4RootDbAdapter", "A4"),
    (".android.a5_emulator", "A5EmulatorAdapter", "A5"),
    (".android.a6_scrcpy", "A6ScrcpyAdapter", "A6"),
    (".android.a7_vendor_backup", "A7VendorBackupAdapter", "A7"),
    # ---- 官方编排 ----
    (".orchestration.o1_official_migration", "O1OfficialMigrationAdapter", "O1"),
    (".orchestration.o2_multi_device", "O2MultiDeviceAdapter", "O2"),
    # ---- 第三方导入 ----
    (".thirdparty.t1_commercial", "T1CommercialAdapter", "T1"),
    (".thirdparty.t2_opensource", "T2OpensourceAdapter", "T2"),
    (".thirdparty.t3_manual", "T3ManualPasteAdapter", "T3"),
    (".thirdparty.t4_screenshots", "T4ScreenshotFolderAdapter", "T4"),
    # ---- iOS 预留 ----
    (".ios_reserve", "IosReserveAdapter", "iOS"),
]

_RISK_PENALTY = {RISK_NONE: 0.0, RISK_LOW: 0.05, RISK_MEDIUM: 0.18, RISK_HIGH: 0.38}


class CollectorRegistry:
    def __init__(self, auto_discover: bool = True) -> None:
        self._adapters: "OrderedDict[str, SourceAdapter]" = OrderedDict()
        self._load_errors: Dict[str, str] = {}
        self._availability: Dict[str, Availability] = {}
        if auto_discover:
            self.discover()

    # ------------------------------------------------------------------
    # 注册 / 发现
    # ------------------------------------------------------------------
    def register(self, adapter: SourceAdapter) -> None:
        if not adapter.id:
            raise ValueError("adapter 缺少 id")
        self._adapters[adapter.id] = adapter

    def discover(self) -> "CollectorRegistry":
        pkg = __package__  # services.collectors
        for rel, cls_name, code in MODULE_SPECS:
            mod_path = (pkg + rel) if rel.startswith(".") else rel
            try:
                mod = importlib.import_module(mod_path, pkg)
                cls = getattr(mod, cls_name)
                self.register(cls())
            except Exception as e:  # 未实现/导入失败：记录而非崩溃
                self._load_errors[code] = f"{type(e).__name__}: {e}"
        return self

    def load_errors(self) -> Dict[str, str]:
        return dict(self._load_errors)

    # ------------------------------------------------------------------
    def adapters(self) -> List[SourceAdapter]:
        return list(self._adapters.values())

    def get(self, adapter_id: str) -> SourceAdapter:
        return self._adapters[adapter_id]

    def ids(self) -> List[str]:
        return list(self._adapters.keys())

    # ------------------------------------------------------------------
    # 可用性
    # ------------------------------------------------------------------
    def check_all(self, force: bool = False) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for adapter in self._adapters.values():
            if force or adapter.id not in self._availability:
                try:
                    self._availability[adapter.id] = adapter.available()
                except Exception as e:
                    self._availability[adapter.id] = Availability(
                        False, f"可用性探测异常: {type(e).__name__}: {e}")
            a = self._availability[adapter.id]
            row = adapter.describe()
            row["availability"] = a.to_dict()
            rows.append(row)
        return rows

    def availability_of(self, adapter_id: str, force: bool = False) -> Availability:
        adapter = self.get(adapter_id)
        if force or adapter_id not in self._availability:
            self._availability[adapter_id] = adapter.available()
        return self._availability[adapter_id]

    # ------------------------------------------------------------------
    # 推荐排序
    # ------------------------------------------------------------------
    def recommend(self, platform: Optional[str] = None,
                  include_disabled: bool = False) -> List[Dict[str, Any]]:
        scored = []
        for adapter in self._adapters.values():
            if platform and adapter.platform != platform:
                continue
            if not include_disabled and not adapter.default_enabled:
                continue
            a = self.availability_of(adapter.id)
            cond = 1.0 if a.available else 0.15
            score = (adapter.success_prior * cond
                     - _RISK_PENALTY.get(adapter.risk_level, 0.2))
            scored.append((round(score, 4), adapter, a))
        scored.sort(key=lambda t: t[0], reverse=True)
        out = []
        for score, adapter, a in scored:
            out.append({"id": adapter.id, "name": adapter.name,
                        "platform": adapter.platform,
                        "risk_level": adapter.risk_level,
                        "available": a.available, "score": score,
                        "reason": a.reason,
                        "requirements": a.requirements})
        return out

    # ------------------------------------------------------------------
    # 运行 / 入库
    # ------------------------------------------------------------------
    def run(self,
            ids: Optional[Sequence[str]] = None,
            db: Any = None,
            person_id: Optional[str] = None,
            person_nickname: Optional[str] = None,
            confirm_high_risk: bool = False,
            persist: bool = True,
            **collect_opts) -> Dict[str, Any]:
        targets = [self.get(i) for i in ids] if ids else self.adapters()
        ran: List[str] = []
        skipped: List[Dict[str, str]] = []
        all_messages: List[Message] = []
        seen_keys = set()
        errors: Dict[str, str] = {}

        for adapter in targets:
            if adapter.risk_level == RISK_HIGH and not confirm_high_risk:
                skipped.append({"id": adapter.id,
                                "reason": "高风险通道，需显式确认 confirm_high_risk=True"})
                continue
            a = self.availability_of(adapter.id)
            if not a.available:
                skipped.append({"id": adapter.id, "reason": f"条件不满足: {a.reason}"})
                continue
            try:
                msgs = adapter.collect(**collect_opts)
            except Exception as e:
                errors[adapter.id] = f"{type(e).__name__}: {e}"
                continue
            added = 0
            for m in msgs:
                key = m.dedup_key()
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                all_messages.append(m)
                added += 1
            ran.append(adapter.id)
            logger.info("通道 %s 产出(去重后)=%d", adapter.id, added)

        result: Dict[str, Any] = {
            "ran": ran, "skipped": skipped, "errors": errors,
            "message_count": len(all_messages)}

        if persist and db is not None:
            from .store import MessageStore
            store = MessageStore(db)
            result["store"] = store.persist(
                all_messages, person_id=person_id,
                person_nickname=person_nickname)
        else:
            result["messages"] = [m.to_dict() for m in all_messages]
        return result

    # ------------------------------------------------------------------
    # 文本总表
    # ------------------------------------------------------------------
    def render_table(self, force: bool = False) -> str:
        rows = self.check_all(force=force)
        lines = [f"共 {len(rows)} 个已加载通道（另有 {len(self._load_errors)} 个未实现）："]
        lines.append(f"{'编号':<4}{'平台':<10}{'风险':<7}{'可用':<6}名称 / 理由")
        for r in rows:
            a = r["availability"]
            code = r["id"].split("_")[0]
            mark = "是" if a["available"] else "否"
            lines.append(f"{code:<5}{r['platform']:<10}{r['risk_level']:<7}{mark:<6}"
                         f"{r['name']}  — {a['reason']}")
        if self._load_errors:
            lines.append("未实现通道: " + ", ".join(
                f"{k}({v.split(':')[0]})" for k, v in self._load_errors.items()))
        return "\n".join(lines)
