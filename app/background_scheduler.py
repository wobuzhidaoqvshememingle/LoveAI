# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 后台定时调度器

把原本只在用户点"加载分析"时才触发的服务（好感度衰减 / 画像刷新 / 数字分身预测 /
财务提取 / 记忆整理）改为按固定间隔在后台自动跑，不需要用户手动点分析。

架构分层（严格遵守"服务层不 import PySide6"约束）：

1. BackgroundUpdater（纯 Python，本文件上半部分，绝不 import PySide6）
   - 实际计算逻辑：遍历活跃人物、调用 facade.services 里的各服务
   - 并发保护：上一轮没跑完，新一轮直接跳过
   - 单人物超时保护：超过 person_timeout 秒则放弃等待并记日志
   - 异常隔离：单个人物失败不影响其他人物
   - 空闲跳过：没有任何活跃人物时本轮直接返回
   - 该类可脱离 Qt 事件循环独立单元测试

2. BackgroundScheduler(QObject)（本文件下半部分，才允许 import PySide6）
   - 持有 QTimer，只负责"定时触发"
   - 每次触发把 BackgroundUpdater.run_once 丢进 daemon 线程执行，不阻塞 UI
   - 通过 facade.services["config"] 读 background.enabled / background.interval_minutes
   - 可选：连接 signals.new_message，收到新消息后 1 分钟内再加速跑一轮
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional


logger = logging.getLogger("LoveAI.background")


# ====================================================================== #
# 第一部分：纯 Python 后台更新器（不依赖 Qt，可独立测试）
# ====================================================================== #
class BackgroundUpdater:
    """后台更新器：遍历活跃人物，逐个跑轻量刷新。

    只通过 facade.services 字典访问既有服务实例，不重复造轮子。
    """

    DEFAULT_RECENT_N = 50          # 每轮只处理最近 N 条新消息
    PERSON_TIMEOUT_SEC = 30.0       # 单人物处理超时（秒）
    ACCELERATE_DELAY_MS = 60_000   # 新消息后多久加速跑一轮

    def __init__(
        self,
        facade: Any,
        recent_n: int = DEFAULT_RECENT_N,
        person_timeout: float = PERSON_TIMEOUT_SEC,
    ) -> None:
        """
        Args:
            facade: ServiceFacade 实例（或任何带 .services 字典的对象）。
            recent_n: 每轮最多扫描最近多少条聊天消息。
            person_timeout: 单人物处理超时（秒），超时放弃等待。
        """
        self.facade = facade
        self.services: dict = getattr(facade, "services", {}) or {}
        self.recent_n = max(1, int(recent_n))
        self.person_timeout = float(person_timeout)

        self._lock = threading.Lock()
        self._running = False
        self.last_run_stats: dict = {}
        # v3.2.0 Bug#3 修复：记录每个 person 上次跑测写链时的消息水位。
        # 水位（msg_count）没涨 = 没新证据 = 不进链，禁止到点直接空过/重复跑。
        self._profiling_watermark: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # 状态查询
    # ------------------------------------------------------------------ #
    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def run_once(self) -> dict:
        """跑一轮后台更新。可重复调用，内部自带并发保护。

        Returns:
            本轮统计 dict；若被并发保护/空闲跳过，则带 skipped=True。
        """
        # 并发保护：上一轮没跑完，本轮直接跳过（非阻塞抢锁）
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            logger.info("[bg] 上一轮仍在执行，本轮跳过")
            return {"skipped": True, "reason": "previous_run_in_progress"}

        try:
            self._running = True
            t0 = time.time()
            person_ids = self.get_active_person_ids()
            stats: dict = {
                "started_at": t0,
                "persons": [],
                "errors": [],
            }

            # 全局维护（与人物无关，无活跃人物也执行）：刷新免费模型目录 / 探测本机桥接
            self._refresh_model_catalog(stats)

            # 空闲跳过：没有任何活跃人物
            if not person_ids:
                logger.info("[bg] 无活跃人物，本轮跳过")
                stats["skipped"] = True
                stats["reason"] = "no_active_persons"
                stats["duration_sec"] = round(time.time() - t0, 3)
                self.last_run_stats = stats
                return stats

            logger.info("[bg] 开始处理 %d 个活跃人物", len(person_ids))
            for pid in person_ids:
                try:
                    self._run_one_person_with_timeout(pid)
                    stats["persons"].append({"person_id": pid, "ok": True})
                except Exception as e:
                    # 异常隔离：单个人物失败不影响其他人
                    logger.warning("[bg] 人物 %s 后台更新失败: %s", pid, e)
                    err_item = {"person_id": pid, "error": str(e)}
                    stats["persons"].append({"person_id": pid, "ok": False, "error": str(e)})
                    stats["errors"].append(err_item)

            stats["duration_sec"] = round(time.time() - t0, 2)
            self.last_run_stats = stats
            logger.info(
                "[bg] 本轮完成：%d 人成功, %d 人失败, 耗时 %.2fs",
                sum(1 for p in stats["persons"] if p.get("ok")),
                sum(1 for p in stats["persons"] if not p.get("ok")),
                stats["duration_sec"],
            )
            return stats
        finally:
            self._running = False
            try:
                self._lock.release()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 活跃人物查询
    # ------------------------------------------------------------------ #
    def get_active_person_ids(self) -> list:
        """返回需要后台更新的人物 ID 列表。

        规则：persons.is_active=1，或在 chat_messages 里至少有一条记录。
        """
        db = self.services.get("db")
        if not db:
            return []
        try:
            rows = db.fetch_all(
                """
                SELECT DISTINCT p.id AS id
                  FROM persons p
                 WHERE p.is_active = 1
                    OR EXISTS (
                        SELECT 1 FROM chat_messages c WHERE c.person_id = p.id
                    )
                 ORDER BY p.id
                """
            )
            out = []
            for r in rows or []:
                pid = r["id"] if isinstance(r, dict) else (r[0] if r else None)
                if pid:
                    out.append(pid)
            return out
        except Exception as e:
            logger.warning("[bg] 查询活跃人物失败: %s", e)
            return []

    # ------------------------------------------------------------------ #
    # 全局维护：免费模型目录定时刷新（与人物无关）
    # ------------------------------------------------------------------ #
    def _refresh_model_catalog(self, stats: dict) -> None:
        """刷新免费模型动态目录（受 TTL，含 OpenRouter/NVIDIA/本机桥接探测）。
        通过持久 LLMGateway 复用同一路由器；异常隔离，不影响其它后台任务。"""
        llm = self.services.get("llm")
        if llm is None or not hasattr(llm, "get_free_router"):
            return
        try:
            router = llm.get_free_router()
            status = router.refresh_catalog()  # 受 TTL（默认24h），不强制
            stats["catalog_refresh"] = {
                src: {"ok": st.get("ok"), "count": st.get("count")}
                for src, st in status.items()}
        except Exception as e:
            logger.debug("[bg] 免费模型目录刷新失败: %s", e)
            stats.setdefault("errors", []).append(
                {"module": "free_catalog", "error": str(e)})

    # ------------------------------------------------------------------ #
    # 单人物处理（带超时）
    # ------------------------------------------------------------------ #
    def _run_one_person_with_timeout(self, person_id: str) -> None:
        """在子线程里跑单人物更新，join(person_timeout)；超时则放弃等待。

        注：Python 无法强杀线程，超时后子线程仍会在后台自然结束，
        但主调度循环不再等它，避免一个慢任务拖垮整轮。
        """
        box: dict = {}

        def _worker() -> None:
            try:
                box["result"] = self._run_one_person(person_id)
            except BaseException as e:  # noqa: BLE001 - 统一收进 box
                box["error"] = e

        t = threading.Thread(
            target=_worker, daemon=True, name=f"bg_upd_{person_id}"
        )
        t.start()
        t.join(self.person_timeout)

        if t.is_alive():
            logger.warning(
                "[bg] 人物 %s 处理超过 %.1fs，跳过等待（线程在后台继续）",
                person_id, self.person_timeout,
            )
            raise TimeoutError(
                f"person {person_id} exceeded {self.person_timeout}s"
            )
        if "error" in box:
            raise box["error"]

    # ------------------------------------------------------------------ #
    # 单人物具体更新内容（复用 facade 已有服务实例）
    # ------------------------------------------------------------------ #
    def _run_one_person(self, person_id: str) -> dict:
        out: dict = {"person_id": person_id}

        # ---- 1. 好感度日常衰减（沉默 7 天以上每维向 50 回归 1%）----
        affection = self.services.get("affection")
        if affection is not None and hasattr(affection, "apply_daily_decay"):
            try:
                affection.apply_daily_decay(person_id)
                out["affection_decay"] = True
            except Exception as e:
                logger.debug("[bg] affection.apply_daily_decay(%s) 失败: %s", person_id, e)

        # ---- 2. 记忆整理：合并相似记忆 + 按时间衰减 ----
        memory = self.services.get("memory")
        if memory is not None:
            try:
                if hasattr(memory, "consolidate_memories"):
                    memory.consolidate_memories(person_id)
                    out["memory_consolidate"] = True
            except Exception as e:
                logger.debug("[bg] memory.consolidate_memories(%s) 失败: %s", person_id, e)
            try:
                if hasattr(memory, "apply_decay"):
                    memory.apply_decay(person_id)
                    out["memory_decay"] = True
            except Exception as e:
                logger.debug("[bg] memory.apply_decay(%s) 失败: %s", person_id, e)

        # ---- 3. 拉最近 N 条消息（供记忆提取 / 数字分身复用）----
        recent_msgs = self._fetch_recent_messages(person_id, self.recent_n)

        # ---- 4. 记忆提取：从最近消息抽事实/偏好/承诺 ----
        if memory is not None and recent_msgs and hasattr(memory, "extract_from_chat"):
            try:
                msgs = [
                    {
                        "content": (m.get("content") if isinstance(m, dict) else (m[0] if m else "")) or "",
                        "direction": (m.get("direction") if isinstance(m, dict) else (m[1] if len(m) > 1 else "received")) or "received",
                    }
                    for m in recent_msgs
                ]
                memory.extract_from_chat(person_id, msgs)
                out["memory_extract_count"] = len(msgs)
            except Exception as e:
                logger.debug("[bg] memory.extract_from_chat(%s) 失败: %s", person_id, e)

        # ---- 5. 画像刷新（v3.2.0：按消息水位增量触发，不跑空链）----
        # Bug#3 修复：旧版 msg_count>=10 就每轮重跑 analyze，无新证据也空过。
        # 新版：记录上次处理水位，水位涨了才进测写链；异常 ERROR 上抛不吞。
        persona = self.services.get("persona")
        profile = None
        if persona is not None and hasattr(persona, "analyze"):
            msg_count = self._count_messages(person_id)
            last_wm = self._profiling_watermark.get(person_id, 0)
            if msg_count >= 10 and msg_count > last_wm:
                try:
                    profile = persona.analyze(person_id)
                    self._profiling_watermark[person_id] = msg_count
                    out["persona_refreshed"] = True
                    out["persona_msg_count"] = msg_count
                    out["persona_watermark_prev"] = last_wm
                except Exception as e:
                    # 链路径关键异常：ERROR 日志（不再 logger.debug 吞）
                    logger.error("[bg] persona.analyze(%s) 测写链失败: %s", person_id, e)
                    out["persona_refresh_error"] = str(e)
            elif msg_count >= 10:
                out["persona_refresh_skipped_no_new_evidence"] = True
                out["persona_watermark"] = last_wm
                out["persona_msg_count"] = msg_count

        # ---- 5.5 策略适配矩阵重算（因时制宜：画像更新后自动重算策略）----
        # 轻量、不阻塞；检测到策略漂移时写入 out 供上层（scheduler）发信号通知 UI。
        try:
            advisor = self.services.get("strategy_advisor")
            if advisor is not None and profile and not profile.get("error"):
                # 取当前关系阶段（好感度推导），读不到就用 5
                stage = 5
                if affection is not None and hasattr(affection, "get_current"):
                    try:
                        scores = affection.get_current(person_id)
                        total = affection.get_total(scores)
                        stage = affection.get_stage(total)
                    except Exception:
                        stage = 5
                sres = advisor.analyze(profile, stage)
                advisor.record_strategy(person_id, profile, sres)
                change = advisor.detect_strategy_change(person_id, sres)
                out["strategy_recalculated"] = True
                if change.get("changed"):
                    out["strategy_changed"] = change
                    logger.info(
                        "[bg] 人物 %s 策略适配发生变更: %s",
                        person_id, change.get("reason", ""),
                    )
        except Exception as e:
            logger.debug("[bg] strategy_advisor 重算失败 person=%s: %s", person_id, e)

        # ---- 6. 财务提取：generate_report 内部会扫最近消息抽信号 ----
        financial = self.services.get("financial")
        if financial is not None and hasattr(financial, "generate_report"):
            try:
                financial.generate_report(person_id)
                out["financial_report"] = True
            except Exception as e:
                logger.debug("[bg] financial.generate_report(%s) 失败: %s", person_id, e)

        # ---- 7. 数字分身预测：用最近一条消息预测对方反应 ----
        try:
            twin = None
            factory = self.services.get("digital_twin_factory")
            if callable(factory):
                twin = factory(person_id)
            if twin is not None and hasattr(twin, "predict_reaction") and recent_msgs:
                last_msg_text = (
                    recent_msgs[0].get("content") if isinstance(recent_msgs[0], dict)
                    else (recent_msgs[0][0] if recent_msgs[0] else "")
                ) or ""
                aff_scores = None
                if affection is not None and hasattr(affection, "get_current"):
                    try:
                        aff_scores = affection.get_current(person_id)
                    except Exception:
                        aff_scores = None
                twin.predict_reaction(
                    last_msg_text[:100],
                    recent_chats=[
                        {"content": (m.get("content") if isinstance(m, dict) else (m[0] if m else "")) or ""}
                        for m in recent_msgs[:10]
                    ],
                    affection=aff_scores,
                )
                out["twin_predicted"] = True
        except Exception as e:
            logger.debug("[bg] digital_twin.predict_reaction(%s) 失败: %s", person_id, e)

        # ---- 8. 任务进度批量回填：用最近 N 条消息批量跑 check_completion ----
        # （修复断链：实时 analyze_chat 路径已接入，这里兜底覆盖导入/离线期间漏判的对话；
        #   check_completion 是纯关键词匹配，轻量不阻塞；分步推进幂等，重复喂旧消息不会乱）
        task_engine = self.services.get("task_engine")
        if task_engine is not None and recent_msgs and hasattr(task_engine, "check_completion"):
            try:
                # _fetch_recent_messages 是倒序（新→旧），改成“旧→新”顺序喂入，分步才不乱序
                ordered = list(reversed(recent_msgs))
                checked = 0
                for m in ordered:
                    content = (m.get("content") if isinstance(m, dict) else (m[0] if m else "")) or ""
                    if content:
                        task_engine.check_completion(person_id, content)
                        checked += 1
                out["task_progress_checked"] = checked
            except Exception as e:
                logger.debug("[bg] task_engine.check_completion(%s) 批量失败: %s", person_id, e)

        return out

    # ------------------------------------------------------------------ #
    # DB 小工具
    # ------------------------------------------------------------------ #
    def _fetch_recent_messages(self, person_id: str, limit: int) -> list:
        db = self.services.get("db")
        if not db:
            return []
        try:
            rows = db.fetch_all(
                """SELECT content, direction, created_at
                     FROM chat_messages
                    WHERE person_id=:pid
                    ORDER BY created_at DESC LIMIT :limit""",
                {"pid": person_id, "limit": limit},
            )
            return list(rows or [])
        except Exception as e:
            logger.debug("[bg] 拉取最近消息失败 %s: %s", person_id, e)
            return []

    def _count_messages(self, person_id: str) -> int:
        db = self.services.get("db")
        if not db:
            return 0
        try:
            row = db.fetch_one(
                "SELECT COUNT(*) AS cnt FROM chat_messages WHERE person_id=:pid",
                {"pid": person_id},
            )
            if not row:
                return 0
            return int(row["cnt"] if isinstance(row, dict) else row[0])
        except Exception:
            return 0


# ====================================================================== #
# 第二部分：Qt 包装层（这一层才允许 import PySide6）
# ====================================================================== #
# 注意：上面 BackgroundUpdater 类本身完全不依赖 PySide6，可脱离 Qt 单元测试。
# 下面的 import 仅服务于 BackgroundScheduler(QObject)。
from PySide6.QtCore import QObject, QTimer  # noqa: E402


class BackgroundScheduler(QObject):
    """定时调度器：QTimer 负责触发，daemon 线程执行 BackgroundUpdater。

    用法：
        sched = BackgroundScheduler(facade, signals)
        sched.start()
        ...
        sched.stop()
    """

    DEFAULT_INTERVAL_MS = 5 * 60 * 1000  # 默认 5 分钟

    def __init__(
        self,
        facade: Any,
        signals: Optional[Any] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        """
        Args:
            facade: ServiceFacade 实例。
            signals: AppSignals 全局信号总线（可选，用于新消息加速 + 结果通知UI）。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self.facade = facade
        self.signals = signals

        # 配置读取
        config = (getattr(facade, "services", {}) or {}).get("config")
        self.enabled: bool = True
        self.interval_ms: int = self.DEFAULT_INTERVAL_MS
        if config is not None:
            try:
                self.enabled = bool(config.get("background.enabled", True))
                minutes = config.get("background.interval_minutes", 5)
                self.interval_ms = max(1, int(float(minutes))) * 60 * 1000
            except Exception as e:
                logger.warning("[bg] 读取 background.* 配置失败，使用默认: %s", e)

        # 实际干活的纯 Python 更新器
        self.updater = BackgroundUpdater(facade)

        # QTimer：只负责定时触发，不做重活
        self._timer = QTimer(self)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self._on_tick)

        # 新消息加速定时器（single-shot）
        self._accel_timer: Optional[QTimer] = None

        self._stopped = True

        # 连接新消息信号：收到新消息时 1 分钟内再跑一轮
        if signals is not None and hasattr(signals, "new_message"):
            try:
                signals.new_message.connect(self._on_new_message)
            except Exception as e:
                logger.debug("[bg] 连接 new_message 信号失败: %s", e)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """启动定时调度。配置禁用则直接返回。"""
        if not self.enabled:
            logger.info("[bg] 调度器被配置禁用 (background.enabled=False)")
            return
        if self._stopped:
            self._stopped = False
            self._timer.start()
            logger.info(
                "[bg] 调度器已启动，间隔 %.1f 分钟", self.interval_ms / 60000.0
            )

    def stop(self) -> None:
        """停止定时调度。"""
        self._stopped = True
        try:
            self._timer.stop()
        except Exception:
            pass
        if self._accel_timer is not None:
            try:
                self._accel_timer.stop()
            except Exception:
                pass
        logger.info("[bg] 调度器已停止")

    # ------------------------------------------------------------------ #
    # 状态查询
    # ------------------------------------------------------------------ #
    def is_running(self) -> bool:
        return (not self._stopped) and self._timer.isActive()

    def get_status(self) -> dict:
        """返回调度器状态（供 UI / 日志查询）。"""
        return {
            "enabled": self.enabled,
            "interval_ms": self.interval_ms,
            "timer_active": self._timer.isActive(),
            "worker_running": self.updater.is_running,
            "last_run": self.updater.last_run_stats,
        }

    def trigger_now(self) -> None:
        """外部手动触发一次（不等定时器）。"""
        self._on_tick()

    # ------------------------------------------------------------------ #
    # 内部槽
    # ------------------------------------------------------------------ #
    def _on_tick(self) -> None:
        """QTimer 到点触发：在 daemon 线程跑更新，绝不阻塞 UI。"""
        if self._stopped:
            return
        # 并发保护：updater 内部已有锁，这里再快速拦一道
        if self.updater.is_running:
            logger.debug("[bg] 上一轮仍在跑，跳过本次 tick")
            return

        t = threading.Thread(
            target=self._run_in_thread, daemon=True, name="bg_scheduler_tick"
        )
        t.start()

    def _run_in_thread(self) -> None:
        try:
            stats = self.updater.run_once()
            # 通过信号通知 UI 刷新（后台线程 emit Qt 信号是线程安全的，
            # 会自动排队到主线程执行）。窗口没开着也没副作用。
            if self.signals is not None and hasattr(self.signals, "analysis_ready"):
                try:
                    self.signals.analysis_ready.emit({
                        "module": "background_refresh",
                        "data": {"background_stats": stats},
                    })
                except Exception:
                    pass
        except Exception as e:
            logger.exception("[bg] 后台调度轮次异常: %s", e)

    def _on_new_message(self, msg: dict) -> None:
        """收到新微信消息时，缩短下次触发时间（1 分钟内再跑一次）。"""
        try:
            if self._stopped:
                return
            if self._accel_timer is None:
                self._accel_timer = QTimer(self)
                self._accel_timer.setSingleShot(True)
                self._accel_timer.timeout.connect(self._on_tick)
            self._accel_timer.start(self.updater.ACCELERATE_DELAY_MS)
            logger.debug("[bg] 收到新消息，%dms 后加速跑一轮", self.updater.ACCELERATE_DELAY_MS)
        except Exception:
            pass
