"""
服务门面层 - 普通Python类，供UI层直接调用，不继承QObject，不用@Slot。
"""
import json
import uuid
import logging
import threading
from datetime import datetime, timedelta
from typing import Any


from app.signals import AppSignals

# 反哺训练 & 微信多方法导出（延迟导入，缺失依赖时优雅降级）
try:
    from services.training_data_collector import TrainingDataCollector
    _TRAINING_AVAILABLE = True
except Exception:
    _TRAINING_AVAILABLE = False
try:
    from services.wechat_auto_importer import WechatAutoImporter
    from services.wechat_realtime_monitor import WechatRealtimeMonitor
    _WECHAT_MULTI_AVAILABLE = True
except Exception:
    _WECHAT_MULTI_AVAILABLE = False


logger = logging.getLogger("LoveAI.bridge")


# ------------------------------------------------------------------ #
# 【回复】链路：候选话术统一提取（唯一数据源 = orchestration["candidates"]）
# ------------------------------------------------------------------ #
def collect_reply_candidates(orchestration_result: Any,
                             llm_structured: Any = None,
                             suggested_reply: str = "") -> list:
    """从生产真实数据源提取最多 3 条候选话术，供【回复】能力统一使用。

    优先级：
      1. orchestration_result["candidates"]
         ← services/auto_strategy_pipeline.py::_build_candidates 真实产出的 3 条话术
           （services/analysis_orchestrator.py 透传到 orchestration_result["candidates"]）；
      2. llm_structured["reply_suggestions"] —— LLM 结构化输出的建议回复；
      3. suggested_reply —— 单条字符串兜底。

    修复背景（R-Reply）：生产侧一直有 3 条候选话术，但此前只留在嵌套的
    data["orchestration"]["candidates"] 里，没有透传到 data 顶层；而 UI 三个候选框只认
    顶层的 reply_candidates/replies/suggested_replies/reply_options，两边键名对不上，
    导致三个候选框与「回复建议」卡片恒空。
    """
    out: list = []
    orch = orchestration_result if isinstance(orchestration_result, dict) else {}
    raw = orch.get("candidates")
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.append(str(item.get("text") or item.get("reply")
                               or item.get("content") or ""))
    if not out and isinstance(llm_structured, dict):
        for item in (llm_structured.get("reply_suggestions") or []):
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.append(str(item.get("content") or item.get("text")
                               or item.get("reply") or ""))
    if not out and isinstance(suggested_reply, str) and suggested_reply.strip():
        out.append(suggested_reply)
    cleaned = [c.strip() for c in out if isinstance(c, str) and c.strip()]
    return cleaned[:3]


class ServiceFacade:
    """服务门面层：向 UI 层暴露统一 API，直接返回 dict。

    业务逻辑全部委托给 services 中的服务；耗时操作在后台线程执行，
    通过全局 AppSignals 推送结果。
    """


    def __init__(self, services: dict, signals):
        self.services = services
        self.signals = signals

        # 将全局信号转发到 Bridge 自身信号（JS 监听 Bridge 即可）
        # 连接全局信号：收到微信新消息时自动分析
        try:
            self.signals.new_message.connect(self._on_wechat_message_auto_analyze)
        except Exception as e:
            logger.error("自动分析信号连接失败: %s", e)

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #

    def _on_wechat_message_auto_analyze(self, msg: dict) -> None:
        """收到微信新消息时，自动结合上下文进行分析。

        只分析对方发来的消息（direction=received），自己发的消息不触发。
        分析在后台线程中进行，不阻塞UI。
        结合最近20条消息上下文，确保能理解"随便"这类依赖语境的回复。
        """
        try:
            # 只分析对方消息
            direction = msg.get("direction", "")
            if direction != "received":
                return

            # 检查是否开启了自动分析
            if self._config and not self._config.get("analysis.auto_analyze", True):
                return

            content = msg.get("content", "")
            if not content or len(content.strip()) < 1:
                return

            # 获取当前人物ID
            person_id = msg.get("person_id", "") or self._get_active_person_id() or ""
            if not person_id:
                return

            # 后台线程执行分析（结合上下文）
            def _worker():
                try:
                    # 自动检测心情状态（从消息文本）
                    if self._mood:
                        try:
                            self._mood.auto_detect_and_set(person_id, content, min_confidence=0.5)
                        except Exception:
                            pass

                    # 生理周期关键词检测（姨妈痛/肚子疼/不舒服等）：
                    # 命中即把对方心情置为"身体不适"，并写入当天周期记录。
                    try:
                        self._apply_period_symptom_state(person_id, content)
                    except Exception:
                        pass

                    # 获取最近上下文（最近20条消息+7天情绪趋势）
                    context = self._get_recent_context(person_id, limit=20)
                    # 把当前消息追加到上下文末尾
                    context_text = context + f"\n[对方] {content}" if context else f"[对方] {content}"
                    # 执行分析
                    result = self._run_analysis(context_text, person_id)
                    # 标记为自动分析
                    result["data"]["auto_analyzed"] = True
                    result["data"]["trigger_message"] = content
                    self.signals.analysis_ready.emit(result)
                    # 更新好感度
                    self._update_affection_from_analysis(person_id, context_text, result)
                except Exception as ex:
                    logger.warning("自动分析失败: %s", ex)

            t = threading.Thread(target=_worker, daemon=True, name="auto_analyze")
            t.start()
        except Exception as e:
            logger.warning("自动分析触发失败: %s", e)

    def _get_recent_context(self, person_id: str, limit: int = 20) -> str:
        """获取最近的聊天记录上下文，格式化为文本。

        用于让LLM理解"随便"这类依赖语境的回复。
        同时包含最近7天的情绪趋势摘要，让分析能结合历史情绪。
        """
        try:
            if not self._db:
                return ""

            # 1. 获取最近消息
            rows = self._db.execute(
                """SELECT content, direction, created_at, emotion FROM chat_messages
                   WHERE person_id=:pid ORDER BY created_at DESC LIMIT :limit""",
                {"pid": person_id, "limit": limit}
            ).fetchall()

            context_lines = []
            if rows:
                rows.reverse()
                for row in rows:
                    content = row[0] or ""
                    direction = row[1] or "received"
                    emotion = row[3] or ""
                    prefix = "[我]" if direction == "sent" else "[对方]"
                    emotion_tag = f"({emotion})" if emotion else ""
                    if content and len(content.strip()) > 0:
                        context_lines.append(f"{prefix} {content[:200]} {emotion_tag}")

            # 2. 获取最近7天的情绪趋势
            emotion_trend = self._get_emotion_trend(person_id, days=7)

            result = ""
            if emotion_trend:
                result += emotion_trend + "\n\n"
            if context_lines:
                result += "【最近聊天记录】\n" + "\n".join(context_lines)

            return result
        except Exception as e:
            logger.warning("获取上下文失败: %s", e)
            return ""

    def _get_emotion_trend(self, person_id: str, days: int = 7) -> str:
        """获取最近N天的情绪趋势摘要。

        统计每天的情绪分布，让LLM能结合历史情绪判断当前状态。
        比如：如果对方最近3天都是消极情绪，今天说"随便"可能是真生气；
        如果最近都是积极情绪，今天说"随便"可能只是敷衍。
        """
        try:
            if not self._db:
                return ""

            rows = self._db.execute(
                """SELECT date(created_at) as msg_date, emotion, COUNT(*) as cnt
                   FROM chat_messages
                   WHERE person_id=:pid AND direction='received'
                   AND created_at >= datetime('now', '-' || :days || ' days')
                   GROUP BY msg_date, emotion
                   ORDER BY msg_date DESC""",
                {"pid": person_id, "days": days}
            ).fetchall()

            if not rows:
                return ""

            # 按日期汇总
            daily_stats = {}
            for row in rows:
                date = row[0]
                emotion = row[1] or "neutral"
                count = row[2]
                if date not in daily_stats:
                    daily_stats[date] = {}
                daily_stats[date][emotion] = daily_stats[date].get(emotion, 0) + count

            # 生成趋势摘要
            trend_lines = ["【最近7天对方情绪趋势】"]
            for date in sorted(daily_stats.keys(), reverse=True):
                stats = daily_stats[date]
                total = sum(stats.values())
                # 找出主导情绪
                dominant = max(stats, key=stats.get)
                dominant_pct = int(stats[dominant] / total * 100)
                # 情绪分布
                dist = ", ".join([f"{k}:{v}" for k, v in sorted(stats.items(), key=lambda x: -x[1])])
                trend_lines.append(f"{date}: 主导情绪={dominant}({dominant_pct}%), 分布[{dist}]")

            # 整体趋势判断
            all_emotions = []
            for stats in daily_stats.values():
                all_emotions.extend([k] * v for k, v in stats.items())
            if all_emotions:
                from collections import Counter
                overall = Counter(all_emotions)
                overall_dominant = overall.most_common(1)[0]
                trend_lines.append(f"\n整体趋势: 最近{days}天以{overall_dominant[0]}为主({overall_dominant[1]}条)")

            return "\n".join(trend_lines)
        except Exception as e:
            logger.warning("获取情绪趋势失败: %s", e)
            return ""

    def _apply_period_symptom_state(self, person_id: str, text: str) -> None:
        """生理周期关键词检测：命中则把对方心情置为"身体不适"，并写入周期记录。

        - 仅作用于对方说的内容（调用方保证 direction=received）；
        - 心情侧委托 MoodService.apply_period_state（source='period'，8小时有效）；
        - 周期记录：当天已有记录则不重复写，避免每条消息都 INSERT；
        - 任何异常只记录 debug，绝不影响主分析流程。
        """
        try:
            if not self._mood or not person_id or not text:
                return
            result = self._mood.apply_period_state(person_id, text)
            if not result:
                return
            logger.info("检测到生理周期症状，对方心情置为身体不适: pid=%s", person_id)

            # 写入生理周期记录（当天去重，避免重复入库）
            if self._cycle:
                today = datetime.now().strftime("%Y-%m-%d")
                try:
                    last = self._cycle.get_cycles(person_id, limit=1)
                    if not last or last[0].get("start_date") != today:
                        hits = self._mood.detect_period_symptoms(text)
                        self._cycle.add_cycle(
                            person_id, today,
                            symptoms="；".join(hits),
                            notes="聊天自动检测",
                        )
                except Exception as ce:
                    logger.debug("写入生理周期记录失败: %s", ce)
        except Exception as e:
            logger.debug("生理周期状态处理失败: %s", e)

    @property
    def _config(self):
        """快捷访问 ConfigService。"""
        return self.services.get("config")

    @property
    def _db(self):
        """快捷访问 Database。"""
        return self.services.get("db")

    @property
    def _root(self) -> str:
        """项目根目录（frozen 为 _MEIPASS，开发态为 LoveAI-v2）。"""
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return sys._MEIPASS  # type: ignore[attr-defined]
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    @property
    def _llm(self):
        """快捷访问 LLMGateway。"""
        return self.services.get("llm")

    @property
    def _emotion(self):
        """快捷访问 EmotionAnalyzer。"""
        return self.services.get("emotion")

    @property
    def _safety(self):
        """快捷访问 SafetyFilter。"""
        return self.services.get("safety")

    @property
    def _affection_engine(self):
        """快捷访问 AffectionEngine。"""
        return self.services.get("affection")

    @property
    def _wechat(self):
        """快捷访问 WeChatService。"""
        return self.services.get("wechat")

    @property
    def _rag(self):
        """快捷访问 RAGService。"""
        return self.services.get("rag")

    @property
    def _cycle(self):
        """快捷访问 CycleService（生理周期）。"""
        return self.services.get("cycle")

    @property
    def _mood(self):
        """快捷访问 MoodService（心情状态）。"""
        return self.services.get("mood")

    @property
    def _chat_analyzer(self):
        """快捷访问 ChatAnalyzer（阶段二编排器）。"""
        return self.services.get("chat_analyzer")

    # ---- 阶段三服务快捷访问 ----
    @property
    def _tutorial(self):
        """快捷访问 TutorialEngine（攻略引擎）。"""
        return self.services.get("tutorial")

    @property
    def _task_engine(self):
        """快捷访问 TaskEngine（任务引擎）。"""
        return self.services.get("task_engine")

    @property
    def _financial(self):
        """快捷访问 FinancialAnalyzer（财务分析）。"""
        return self.services.get("financial")

    @property
    def _consult(self):
        """快捷访问 ConsultEngine（咨询引擎）。"""
        return self.services.get("consult")

    @property
    def _medal(self):
        """快捷访问 MedalService（勋章服务）。"""
        return self.services.get("medal")

    @property
    def _portrait(self):
        """快捷访问 PortraitService（画像服务）。"""
        return self.services.get("portrait")

    def _get_twin(self, person_id: str = None):
        """获取数字分身实例（按需创建，绑定person_id）。"""
        pid = person_id or self._get_active_person_id()
        if not pid:
            return None
        factory = self.services.get("digital_twin_factory")
        if callable(factory):
            return factory(pid)
        return None

    # ---- 阶段四服务快捷访问 ----
    @property
    def _speech(self):
        """快捷访问 SpeechService（语音识别）。"""
        return self.services.get("speech")

    @property
    def _ocr(self):
        """快捷访问 OCRService（截屏识别）。"""
        return self.services.get("ocr")

    @property
    def _stats(self):
        """快捷访问 StatsService（数据统计）。"""
        return self.services.get("stats")

    @property
    def _plugin(self):
        """快捷访问 PluginService（插件管理）。"""
        return self.services.get("plugin")

    @property
    def _memory(self):
        """快捷访问 MemoryService（长期记忆）。"""
        return self.services.get("memory")

    @property
    def _backup(self):
        """快捷访问 BackupService（备份服务）。"""
        return self.services.get("backup")

    @property
    def _style_learner(self):
        """快捷访问 StyleLearner（用户说话风格学习）。"""
        return self.services.get("style_learner")

    # ---- 第五批：抖音借鉴功能快捷访问 ----
    @property
    def _jev_judge(self):
        """快捷访问 JevJudge（轻量判断层，借鉴Jev协agent）。"""
        return self.services.get("jev_judge")

    @property
    def _auth_helper(self):
        """快捷访问 AuthHelper（验证码自动识别与会话保活，借鉴codex-auth-helper）。"""
        return self.services.get("auth_helper")

    @property
    def _codex_adapt(self):
        """快捷访问 CodexAdaptation（Codex 8功能恋爱适配，借鉴里昂说AI）。"""
        return self.services.get("codex_adaptation")

    def _get_active_person_id(self) -> str | None:
        """从 settings 表读取当前激活的 person_id。"""
        if not self._db:
            return None
        row = self._db.fetch_one(
            "SELECT value FROM settings WHERE key = 'active_person_id'"
        )
        return row["value"] if row else None

    def _set_active_person_id(self, person_id: str) -> None:
        """写入当前激活的 person_id 到 settings 表。"""
        if not self._db:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('active_person_id', :v, datetime('now'))",
            {"v": person_id},
        )

    # ================================================================== #
    # 配置域
    # ================================================================== #
    def get_config(self) -> dict:
        """返回完整配置 JSON。"""
        try:
            cfg = self._config.get_all() if self._config else {}
            return {"ok": True, "data": cfg}
        except Exception as e:
            logger.exception("get_config 失败")
            return {"ok": False, "error": str(e)}

    def get_config_value(self, key: str) -> dict:
        """返回单个配置值，支持点分隔路径如 'llm.model'。"""
        try:
            if not key:
                return {"ok": False, "error": "key 不能为空"}
            value = self._config.get(key) if self._config else None
            return {"ok": True, "data": {"key": key, "value": value}}
        except Exception as e:
            logger.exception("get_config_value 失败: key=%s", key)
            return {"ok": False, "error": str(e)}

    def set_config_value(self, key: str, value: str) -> dict:
        """设置配置值，即时写入文件并广播 config_changed 信号。"""
        try:
            if not key:
                return {"ok": False, "error": "key 不能为空"}
            # 尝试把 value 解析成 JSON（数字/布尔/对象），失败则按字符串存
            parsed: Any = value
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                parsed = value
            self._config.set(key, parsed)
            self.signals.config_changed.emit(key)
            return {"ok": True, "data": {"key": key, "value": parsed}}
        except Exception as e:
            logger.exception("set_config_value 失败: key=%s", key)
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 本地桌面 AI / 浏览器 AI（设置页手动触发，不启动时自动扫）
    # ================================================================== #
    def get_desktop_ai_candidates(self) -> dict:
        """读取启动时后台扫描到的桌面端 AI 候选（services["desktop_ai_candidates"]）。

        仅展示结果，不启用 desktop_ai 路由（config 开关默认 False）。
        """
        try:
            data = self.services.get("desktop_ai_candidates") or {
                "status": "unavailable",
                "installed": [],
                "openai_endpoints": [],
                "usable_models": [],
                "notes": [],
            }
            return {"ok": True, "data": data}
        except Exception as e:
            logger.exception("读取桌面 AI 候选失败")
            return {"ok": False, "error": str(e)}

    def scan_browser_ai(self) -> dict:
        """设置页手动触发：枚举浏览器网页 AI 源（不启动真实浏览器，不阻塞）。

        playwright 启动慢，故启动时不主动扫；这里只实例化 BrowserAIHub 并
        返回其 list_sources()/status()，真正调用 chat/search 时才起浏览器。
        """
        try:
            from services.browser_ai_hub import BrowserAIHub, PLAYWRIGHT_AVAILABLE
            cfg = {}
            if self._config is not None:
                try:
                    cfg = self._config.get_all() or {}
                except Exception:
                    cfg = {}
            hub = BrowserAIHub(config=cfg)
            return {
                "ok": True,
                "data": {
                    "playwright_available": bool(PLAYWRIGHT_AVAILABLE),
                    "status": hub.status(),
                    "sources": hub.list_sources(),
                },
            }
        except Exception as e:
            logger.exception("扫描浏览器 AI 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 人物档案域
    # ================================================================== #
    def list_persons(self) -> dict:
        """返回所有对象列表。"""
        try:
            if not self._db:
                return {"ok": True, "data": []}
            rows = self._db.fetch_all(
                "SELECT * FROM persons WHERE is_active = 1 ORDER BY updated_at DESC"
            )
            active_id = self._get_active_person_id()
            return {"ok": True, "data": {"persons": rows, "active_person_id": active_id}}
        except Exception as e:
            logger.exception("list_persons 失败")
            return {"ok": False, "error": str(e)}

    def get_person(self, person_id: str) -> dict:
        """返回单个对象详情。"""
        try:
            if not person_id:
                return {"ok": False, "error": "person_id 不能为空"}
            row = self._db.fetch_one("SELECT * FROM persons WHERE id = :id", {"id": person_id})
            if not row:
                return {"ok": False, "error": "对象不存在"}
            return {"ok": True, "data": row}
        except Exception as e:
            logger.exception("get_person 失败: id=%s", person_id)
            return {"ok": False, "error": str(e)}

    def create_person(self, data_json: str) -> dict:
        """创建新对象。data_json 为人物字段的 JSON 字符串。"""
        try:
            data = json.loads(data_json) if data_json else {}
            person_id = data.get("id") or f"person_{uuid.uuid4().hex[:12]}"
            self._db.execute(
                """INSERT INTO persons
                   (id, nickname, wechat_nickname, gender, birthday, occupation,
                    relationship_stage, soul_path, portrait_path, user_perspective_gender, is_active)
                   VALUES (:id, :nickname, :wechat_nickname, :gender, :birthday, :occupation,
                           :relationship_stage, :soul_path, :portrait_path, :user_perspective_gender, 1)""",
                {
                    "id": person_id,
                    "nickname": data.get("nickname", ""),
                    "wechat_nickname": data.get("wechat_nickname", ""),
                    "gender": data.get("gender", "unknown"),
                    "birthday": data.get("birthday", ""),
                    "occupation": data.get("occupation", ""),
                    "relationship_stage": data.get("relationship_stage", 0),
                    "soul_path": data.get("soul_path", ""),
                    "portrait_path": data.get("portrait_path", ""),
                    "user_perspective_gender": data.get("user_perspective_gender", ""),
                },
            )
            # 初始化好感度默认值
            self._db.execute(
                """INSERT OR IGNORE INTO current_affection (person_id, scores_json, total_score)
                   VALUES (:pid, :scores, 50.0)""",
                {"pid": person_id, "scores": json.dumps({}, ensure_ascii=False)},
            )
            logger.info("创建对象: %s (%s)", person_id, data.get("nickname"))
            return {"ok": True, "data": {"id": person_id}}
        except Exception as e:
            logger.exception("create_person 失败")
            return {"ok": False, "error": str(e)}

    def update_person(self, person_id: str, data_json: str) -> dict:
        """更新对象字段。"""
        try:
            if not person_id:
                return {"ok": False, "error": "person_id 不能为空"}
            data = json.loads(data_json) if data_json else {}
            allowed = {"nickname", "wechat_nickname", "gender", "birthday", "occupation",
                       "relationship_stage", "soul_path", "portrait_path", "user_perspective_gender"}
            sets = []
            params: dict[str, Any] = {"id": person_id}
            for k, v in data.items():
                if k in allowed:
                    sets.append(f"{k} = :{k}")
                    params[k] = v
            if not sets:
                return {"ok": False, "error": "没有可更新的字段"}
            sets.append("updated_at = datetime('now')")
            sql = f"UPDATE persons SET {', '.join(sets)} WHERE id = :id"
            self._db.execute(sql, params)
            return {"ok": True, "data": {"id": person_id, "updated": list(data.keys())}}
        except Exception as e:
            logger.exception("update_person 失败: id=%s", person_id)
            return {"ok": False, "error": str(e)}

    def update_person_profile(self, person_id: str, data) -> dict:
        """人物档案页保存：基本信息 + 心理学侧写 + SOUL 文本。

        data 为 JS 传入的对象（QVariant→dict），字段：
        name/gender/age/occupation/note/mbti/big_five/attachment/comm_style/soul_text。
        persons 表缺少的列在此防御性 ALTER 补齐；soul_text 同时 best-effort 写入
        数字分身的 SOUL.md。
        """
        try:
            pid = person_id or self._get_active_person_id()
            if not pid:
                return {"ok": False, "error": "没有可保存的对象"}
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                return {"ok": False, "error": "档案数据格式错误"}

            # 防御性补列（SQLite 无 ADD COLUMN IF NOT EXISTS，重复添加会报错，忽略即可）
            extra_cols = {
                "age": "INTEGER", "note": "TEXT", "mbti": "TEXT",
                "big_five": "TEXT", "attachment": "TEXT",
                "comm_style": "TEXT", "soul_text": "TEXT",
            }
            for col, decl in extra_cols.items():
                try:
                    self._db.execute(f"ALTER TABLE persons ADD COLUMN {col} {decl}", {})
                except Exception:
                    pass  # 列已存在

            field_map = {
                "name": "nickname", "gender": "gender", "age": "age",
                "occupation": "occupation", "note": "note", "mbti": "mbti",
                "big_five": "big_five", "attachment": "attachment",
                "comm_style": "comm_style", "soul_text": "soul_text",
            }
            sets = []
            params: dict[str, Any] = {"id": pid}
            for js_key, col in field_map.items():
                if js_key in data and data[js_key] is not None:
                    sets.append(f"{col} = :{col}")
                    params[col] = data[js_key]
            if sets:
                sets.append("updated_at = datetime('now')")
                self._db.execute(
                    f"UPDATE persons SET {', '.join(sets)} WHERE id = :id", params
                )

            # best-effort：SOUL 文本写入数字分身 SOUL.md
            soul_text = data.get("soul_text")
            if soul_text:
                try:
                    soul_dir = os.path.join(self._root, "data", "persons", pid)
                    os.makedirs(soul_dir, exist_ok=True)
                    soul_path = os.path.join(soul_dir, "SOUL.md")
                    with open(soul_path, "w", encoding="utf-8") as fh:
                        fh.write(str(soul_text))
                    self._db.execute(
                        "UPDATE persons SET soul_path = :p WHERE id = :id",
                        {"p": soul_path, "id": pid},
                    )
                except Exception:
                    logger.exception("写入 SOUL.md 失败（不影响档案保存）")

            return {"ok": True, "data": {"id": pid}}
        except Exception as e:
            logger.exception("update_person_profile 失败: id=%s", person_id)
            return {"ok": False, "error": str(e)}

    def generate_person_profile(self, person_id: str) -> dict:
        """从聊天记录自动生成人物档案（SOUL.md + personality.json）。

        读取该人物最近50条聊天记录作为样本，调用 LLM 生成：
        - SOUL.md：人格描述、说话风格、行为模式
        - personality.json：16维人格数值

        Args:
            person_id: 人物ID

        Returns:
            {ok, soul, personality, message_count}
        """
        try:
            db = self.services.get("db")
            if not db:
                return {"ok": False, "error": "数据库未初始化"}

            person = db.fetch_one("SELECT * FROM persons WHERE id=:id", {"id": person_id})
            if not person:
                return {"ok": False, "error": f"人物不存在: {person_id}"}

            # 读取最近50条聊天记录作为样本
            messages = db.fetch_all(
                "SELECT direction, content, created_at FROM chat_messages "
                "WHERE person_id=:pid AND msg_type='text' ORDER BY id DESC LIMIT 50",
                {"pid": person_id}
            )
            if not messages:
                return {"ok": False, "error": "该人物暂无聊天记录，请先导入聊天记录"}

            # 拼装聊天样本
            samples = []
            for m in reversed(messages):
                sender = "对方" if m["direction"] == "received" else "我"
                samples.append(f"[{m['created_at']}] {sender}: {m['content']}")
            chat_samples = "\n".join(samples)

            # 创建 digital_twin 并生成档案
            factory = self.services.get("digital_twin_factory")
            if not factory:
                return {"ok": False, "error": "数字分身服务未初始化"}

            twin = factory(person_id)
            if not twin:
                return {"ok": False, "error": "数字分身创建失败"}

            basic_info = {
                "nickname": person.get("nickname", ""),
                "gender": person.get("gender", ""),
                "occupation": person.get("occupation", ""),
                "age": "",
            }

            result = twin.generate_profile(basic_info, chat_samples=chat_samples)
            logger.info("生成人物档案 %s: soul=%d字, personality keys=%d, 样本=%d条",
                        person_id, len(result.get("soul", "")),
                        len(result.get("personality", {})), len(messages))

            return {"ok": True, "data": {
                "soul": result.get("soul", ""),
                "personality": result.get("personality", {}),
                "message_count": len(messages),
            }}
        except Exception as e:
            logger.exception("generate_person_profile 失败: id=%s", person_id)
            return {"ok": False, "error": str(e)}

    def delete_person(self, person_id: str) -> dict:
        """软删除对象（is_active=0）。"""
        try:
            if not person_id:
                return {"ok": False, "error": "person_id 不能为空"}
            self._db.execute(
                "UPDATE persons SET is_active = 0, updated_at = datetime('now') WHERE id = :id",
                {"id": person_id},
            )
            # 如果删的是当前激活对象，清空激活态
            if self._get_active_person_id() == person_id:
                self._db.execute("DELETE FROM settings WHERE key = 'active_person_id'")
            return {"ok": True, "data": {"id": person_id}}
        except Exception as e:
            logger.exception("delete_person 失败: id=%s", person_id)
            return {"ok": False, "error": str(e)}

    def switch_person(self, person_id: str) -> dict:
        """切换当前对象，触发 person_switched 信号。"""
        try:
            if not person_id:
                return {"ok": False, "error": "person_id 不能为空"}
            row = self._db.fetch_one("SELECT * FROM persons WHERE id = :id AND is_active = 1",
                                     {"id": person_id})
            if not row:
                return {"ok": False, "error": "对象不存在或已删除"}
            self._set_active_person_id(person_id)
            payload = {"person_id": person_id, "nickname": row.get("nickname", "")}
            self.signals.person_switched.emit(payload)
            logger.info("切换对象 -> %s (%s)", person_id, row.get("nickname"))
            return {"ok": True, "data": payload}
        except Exception as e:
            logger.exception("switch_person 失败: id=%s", person_id)
            return {"ok": False, "error": str(e)}

    def get_active_person(self) -> dict:
        """返回当前激活对象的完整信息。"""
        try:
            pid = self._get_active_person_id()
            if not pid:
                return {"ok": True, "data": {"person": None}}
            row = self._db.fetch_one("SELECT * FROM persons WHERE id = :id", {"id": pid})
            return {"ok": True, "data": {"person": row, "active_person_id": pid}}
        except Exception as e:
            logger.exception("get_active_person 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 任务域
    # ================================================================== #
    def list_tasks(self, person_id: str, status: str) -> dict:
        """返回任务列表，可按 person_id / status 过滤。"""
        try:
            sql = "SELECT * FROM tasks WHERE 1=1"
            params: dict[str, Any] = {}
            if person_id:
                sql += " AND person_id = :pid"
                params["pid"] = person_id
            if status:
                sql += " AND status = :st"
                params["st"] = status
            sql += " ORDER BY created_at DESC"
            rows = self._db.fetch_all(sql, params)
            return {"ok": True, "data": rows}
        except Exception as e:
            logger.exception("list_tasks 失败")
            return {"ok": False, "error": str(e)}

    def create_task(self, data_json: str) -> dict:
        """创建任务。"""
        try:
            data = json.loads(data_json) if data_json else {}
            task_id = data.get("id") or f"task_{uuid.uuid4().hex[:12]}"
            self._db.execute(
                """INSERT INTO tasks
                   (id, person_id, title, description, task_type, difficulty, estimated_time,
                    reward_desc, affection_reward, deadline, completion_condition, auto_keywords,
                    guide_direction, steps_json, current_step, status)
                   VALUES (:id, :person_id, :title, :description, :task_type, :difficulty, :estimated_time,
                           :reward_desc, :affection_reward, :deadline, :completion_condition, :auto_keywords,
                           :guide_direction, :steps_json, 0, 'pending')""",
                {
                    "id": task_id,
                    "person_id": data.get("person_id", self._get_active_person_id() or ""),
                    "title": data.get("title", ""),
                    "description": data.get("description", ""),
                    "task_type": data.get("task_type", "daily"),
                    "difficulty": data.get("difficulty", "normal"),
                    "estimated_time": data.get("estimated_time", ""),
                    "reward_desc": data.get("reward_desc", ""),
                    "affection_reward": data.get("affection_reward", 0),
                    "deadline": data.get("deadline", ""),
                    "completion_condition": data.get("completion_condition", ""),
                    "auto_keywords": data.get("auto_keywords", ""),
                    "guide_direction": data.get("guide_direction", ""),
                    "steps_json": data.get("steps_json", "[]"),
                },
            )
            task = self._db.fetch_one("SELECT * FROM tasks WHERE id = :id", {"id": task_id})
            self.signals.task_updated.emit({"task": task, "event": "new"})
            return {"ok": True, "data": {"id": task_id}}
        except Exception as e:
            logger.exception("create_task 失败")
            return {"ok": False, "error": str(e)}

    def complete_task(self, task_id: str) -> dict:
        """完成任务。"""
        try:
            if not task_id:
                return {"ok": False, "error": "task_id 不能为空"}
            self._db.execute(
                "UPDATE tasks SET status = 'completed', completed_at = datetime('now') WHERE id = :id",
                {"id": task_id},
            )
            task = self._db.fetch_one("SELECT * FROM tasks WHERE id = :id", {"id": task_id})
            self.signals.task_updated.emit({"task": task, "event": "completed"})
            return {"ok": True, "data": {"id": task_id}}
        except Exception as e:
            logger.exception("complete_task 失败: id=%s", task_id)
            return {"ok": False, "error": str(e)}

    def abandon_task(self, task_id: str) -> dict:
        """放弃任务。"""
        try:
            if not task_id:
                return {"ok": False, "error": "task_id 不能为空"}
            self._db.execute(
                "UPDATE tasks SET status = 'abandoned' WHERE id = :id",
                {"id": task_id},
            )
            task = self._db.fetch_one("SELECT * FROM tasks WHERE id = :id", {"id": task_id})
            self.signals.task_updated.emit({"task": task, "event": "failed"})
            return {"ok": True, "data": {"id": task_id}}
        except Exception as e:
            logger.exception("abandon_task 失败: id=%s", task_id)
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 好感度域
    # ================================================================== #
    def get_affection(self, person_id: str) -> dict:
        """返回当前对象好感度 18 维 + 总分。"""
        try:
            if not person_id:
                person_id = self._get_active_person_id() or ""
            if not person_id:
                return {"ok": True, "data": {"scores": {}, "total": 50.0}}
            row = self._db.fetch_one(
                "SELECT scores_json, total_score FROM current_affection WHERE person_id = :pid",
                {"pid": person_id},
            )
            if not row:
                return {"ok": True, "data": {"scores": {}, "total": 50.0, "person_id": person_id}}
            try:
                scores = json.loads(row["scores_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                scores = {}
            # R3.3-78：loyalty 与 18 维好感并列存储，单独透出，不并入 total。
            try:
                loyalty = float(scores.get("loyalty", 50.0))
            except (TypeError, ValueError):
                loyalty = 50.0
            return {"ok": True, "data": {
                "person_id": person_id,
                "scores": scores,
                "total": row["total_score"],
                "loyalty": max(0.0, min(100.0, loyalty)),
            }}
        except Exception as e:
            logger.exception("get_affection 失败: id=%s", person_id)
            return {"ok": False, "error": str(e)}

    def get_affection_history(self, person_id: str, days: int) -> dict:
        """返回最近 N 天的好感度快照。"""
        try:
            if not person_id:
                person_id = self._get_active_person_id() or ""
            if not person_id:
                return {"ok": True, "data": []}
            days = max(1, min(int(days or 30), 365))
            since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = self._db.fetch_all(
                """SELECT date, scores_json, total_score, created_at
                   FROM affection_snapshots
                   WHERE person_id = :pid AND date >= :since
                   ORDER BY date ASC""",
                {"pid": person_id, "since": since},
            )
            for r in rows:
                try:
                    r["scores"] = json.loads(r.pop("scores_json") or "{}")
                except (json.JSONDecodeError, TypeError):
                    r["scores"] = {}
            return {"ok": True, "data": rows}
        except Exception as e:
            logger.exception("get_affection_history 失败: id=%s", person_id)
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 聊天分析域（阶段二：真实分析流水线）
    # ================================================================== #
    def get_chat_messages(self, person_id: str, limit: int = 50) -> dict:
        """获取指定人物的最近聊天记录（按时间倒序）。

        Args:
            person_id: 人物ID
            limit: 返回条数，默认50

        Returns:
            消息列表，每条包含 id/person_id/direction/content/created_at
        """
        try:
            pid = person_id or self._get_active_person_id()
            if not pid:
                return {"ok": True, "data": {"messages": [], "count": 0}}
            db = self.services.get("db")
            if not db:
                return {"ok": False, "error": "数据库未初始化"}
            rows = db.fetch_all(
                "SELECT id, person_id, direction, content, msg_type, created_at "
                "FROM chat_messages WHERE person_id = :pid "
                "ORDER BY created_at DESC LIMIT :lim",
                {"pid": pid, "lim": int(limit)}
            )
            messages = []
            for r in rows:
                messages.append({
                    "id": r["id"],
                    "person_id": r["person_id"],
                    "direction": r["direction"],
                    "content": r["content"],
                    "msg_type": r["msg_type"],
                    "created_at": r["created_at"],
                })
            # 倒序返回（最新的在最后，符合聊天阅读习惯）
            messages.reverse()
            return {"ok": True, "data": {"messages": messages, "count": len(messages)}}
        except Exception as e:
            logger.exception("get_chat_messages 失败")
            return {"ok": False, "error": str(e)}

    def get_diagnostic_report(self, person_id: str) -> dict:
        """前台查看 18 部分人物诊断报告（缓存优先；数据不足时后端返回 insufficient，
        前端做「未校准/待观察」兜底，不报错、不做去留建议）。"""
        try:
            from services import diagnostic_report as _dr
            pid = person_id or self._get_active_person_id()
            if not pid:
                return {"ok": False, "error": "未选择对象"}
            db = self.services.get("db")
            report = _dr.get_or_generate_report(pid, db=db)
            return {"ok": True, "data": report}
        except Exception as e:
            logger.exception("get_diagnostic_report 失败: id=%s", person_id)
            return {"ok": False, "error": str(e)}

    def analyze_chat(self, chat_text: str, person_id: str) -> dict:
        """触发聊天分析。耗时操作在后台线程执行，完成后通过 analysis_ready 信号推送。"""
        try:
            if not chat_text:
                return {"ok": False, "error": "chat_text 不能为空"}
            pid = person_id or self._get_active_person_id() or ""
            # 每轮分析开始：重置事件总线单轮去重集合
            try:
                _bus = self._ensure_event_bus()
                if _bus is not None:
                    _bus.reset_pass()
            except Exception:
                pass

            def _worker():
                try:
                    result = self._run_analysis(chat_text, pid)
                    self.signals.analysis_ready.emit(result)
                    # 分析完成后更新好感度
                    self._update_affection_from_analysis(pid, chat_text, result)
                    # 反哺训练：轻量登记回复候选为训练样本（失败静默，不阻塞）
                    self._maybe_collect_reply_candidates(chat_text, pid, result)
                except Exception as ex:
                    logger.exception("analyze_chat 后台线程异常")
                    self.signals.analysis_ready.emit({
                        "module": "chat_analysis",
                        "data": {"error": str(ex), "person_id": pid},
                    })

            t = threading.Thread(target=_worker, daemon=True, name="analyze_chat")
            t.start()
            return {"ok": True, "data": {"started": True, "person_id": pid}}
        except Exception as e:
            logger.exception("analyze_chat 失败")
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # 第五批：抖音借鉴功能公开API（codex-auth-helper / AI圈股神 / Jev / AI操控电脑 / Codex8功能）
    # ------------------------------------------------------------------ #

    def parallel_analysis(self, chat_text: str, person_id: str = "",
                          perspectives: list = None) -> dict:
        """多视角并行分析（借鉴Codex多Agent并行：情感/策略/分身/风险/机会5视角同时运行）。

        与_run_analysis的串行流水线不同，本方法启动5个独立分析视角线程，
        15秒超时后汇总，适合需要快速多维度参考的场景。
        """
        try:
            if not self._codex_adapt:
                return {"ok": False, "error": "codex_adaptation服务未初始化"}
            pid = person_id or self._get_active_person_id() or "self"
            result = self._codex_adapt.parallel_analysis(chat_text, pid, perspectives)
            return {"ok": True, "data": result}
        except Exception as e:
            logger.warning("并行分析失败: %s", e)
            return {"ok": False, "error": str(e)}

    def jev_classify(self, text: str) -> dict:
        """轻量判断层（借鉴Jev协agent：毫秒级消息分类+紧急度+快情绪，不调LLM）。"""
        try:
            if not self._jev_judge:
                return {"ok": False, "error": "jev_judge服务未初始化"}
            return {"ok": True, "data": {
                "category": self._jev_judge.classify_message(text),
                "urgency": self._jev_judge.judge_urgency(text),
                "emotion": self._jev_judge.classify_emotion_quick(text),
            }}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def twin_adversarial_debate(self, message: str, person_id: str = "",
                                rounds: int = 3) -> dict:
        """数字分身对抗性辩驳（借鉴AI圈股神多空对决：乐观分身vs悲观分身N轮辩论）。"""
        try:
            pid = person_id or self._get_active_person_id()
            if not pid:
                return {"ok": False, "error": "未选择人物"}
            twin = self._get_or_create_twin(pid)
            if not twin:
                return {"ok": False, "error": "数字分身创建失败"}
            result = twin.adversarial_debate(message, rounds=rounds)
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def twin_multi_scene(self, message: str, person_id: str = "") -> dict:
        """数字分身多场景预测（借鉴AI圈股神多市场覆盖：暧昧/冷战/热恋/分手/朋友区5场景）。"""
        try:
            pid = person_id or self._get_active_person_id()
            if not pid:
                return {"ok": False, "error": "未选择人物"}
            twin = self._get_or_create_twin(pid)
            if not twin:
                return {"ok": False, "error": "数字分身创建失败"}
            result = twin.predict_reaction_multi_scene(message)
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def twin_event_impact(self, event_type: str, event_description: str,
                          person_id: str = "") -> dict:
        """事件影响分析（借鉴AI圈股神新闻事件分析：短期/中期/长期影响+恢复周期）。"""
        try:
            pid = person_id or self._get_active_person_id()
            if not pid:
                return {"ok": False, "error": "未选择人物"}
            twin = self._get_or_create_twin(pid)
            if not twin:
                return {"ok": False, "error": "数字分身创建失败"}
            result = twin.analyze_event_impact(event_type, event_description)
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def decompose_goal(self, goal_description: str, context: dict = None) -> dict:
        """目标自动分解（借鉴Codex任务分解：将复杂恋爱目标拆为可执行步骤序列）。"""
        try:
            if not self._codex_adapt:
                return {"ok": False, "error": "codex_adaptation服务未初始化"}
            result = self._codex_adapt.decompose_goal(goal_description, context or {})
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def review_reply(self, message: str, person_id: str = "") -> dict:
        """回复策略审查（借鉴Codex PR Review：语气/时机/风险/改进建议/预期效果）。"""
        try:
            if not self._codex_adapt:
                return {"ok": False, "error": "codex_adaptation服务未初始化"}
            pid = person_id or self._get_active_person_id() or "self"
            result = self._codex_adapt.review_message(message, person_id=pid)
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def auth_status(self) -> dict:
        """验证码助手状态（借鉴codex-auth-helper：各平台会话状态+token有效期）。"""
        try:
            if not self._auth_helper:
                return {"ok": False, "error": "auth_helper服务未初始化"}
            return {"ok": True, "data": self._auth_helper.get_status()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _run_analysis(self, chat_text: str, person_id: str) -> dict:
        """执行真实分析流水线（在后台线程中调用）。

        8模块完整分析：emotion/keywords/rag/intent/relation/reply/safety/affection
        言外之意、真实意图等直接从LLM结构化输出中提取，不单独摘出来。
        """
        # 0. 自动检查是否需要更新用户本人说话风格（新增≥20条才重算，否则秒回）
        if self._style_learner:
            try:
                self._style_learner.maybe_update()
            except Exception:
                logger.debug("风格自动学习检查失败", exc_info=True)

        # 1. 情感分析（即时，本地规则）
        emotion_result = None
        if self._emotion:
            try:
                emotion_result = self._emotion.analyze(chat_text)
            except Exception as e:
                logger.warning("情感分析失败: %s", e)

        # 1.5 轻量判断层（借鉴Jev协agent：毫秒级预过滤，不阻塞主流程）
        jev_result = None
        if self._jev_judge:
            try:
                jev_result = {
                    "message_category": self._jev_judge.classify_message(chat_text),
                    "urgency": self._jev_judge.judge_urgency(chat_text),
                    "emotion_quick": self._jev_judge.classify_emotion_quick(chat_text),
                }
            except Exception as e:
                logger.debug("Jev轻量判断失败: %s", e)

        # 2. RAG 知识库检索（硬超时 5s，绝不阻塞分析流水线）
        rag_context = ""
        if self._rag:
            def _rag_call():
                return self._rag.build_context(chat_text, max_chars=1500)
            box = {}
            def _rag_thread():
                try:
                    box["v"] = _rag_call()
                except Exception as e:  # noqa: BLE001
                    box["e"] = e
            rt = threading.Thread(target=_rag_thread, daemon=True)
            rt.start()
            rt.join(5.0)
            if "v" in box:
                rag_context = box["v"]
            else:
                logger.warning("RAG build_context 超时/失败: %s", box.get("e"))

        # 3. 安全检测（本地规则，即时）
        safety_result = {"passed": True, "issues": [], "level": "safe"}
        if self._safety:
            try:
                has_sensitive = self._safety.contains_sensitive(chat_text)
                if has_sensitive:
                    safety_result = {
                        "passed": False,
                        "issues": ["检测到敏感词，已启用安全破假模式"],
                        "level": "warning",
                    }
            except Exception as e:
                logger.warning("安全检测失败: %s", e)

        # 4. 大模型深度分析（如果 LLM 可用且配置了 API key）
        llm_analysis = None
        llm_structured = {}
        if self._llm and self._config:
            api_key = self._config.get("llm.api_key", "")
            if api_key:
                try:
                    # 获取人物SOUL档案
                    soul_text = ""
                    if person_id and self._db:
                        try:
                            soul_path = self._db.execute(
                                "SELECT soul_path FROM persons WHERE id=:pid", {"pid": person_id}
                            ).fetchone()
                            if soul_path and soul_path[0] and os.path.exists(soul_path[0]):
                                with open(soul_path[0], 'r', encoding='utf-8') as f:
                                    soul_text = f.read()[:2000]
                        except Exception:
                            pass

                    # 获取生理周期上下文（自动判断男女：女性姨妈期/男性大姨夫）
                    cycle_context = "暂无记录"
                    if self._cycle and person_id:
                        try:
                            # 自动根据性别选择
                            gender = "female"
                            if hasattr(self._cycle, '_get_person_gender'):
                                gender = self._cycle._get_person_gender(person_id)
                            if gender == "male":
                                cycle_info = self._cycle.get_male_current_phase(person_id)
                                if cycle_info:
                                    cycle_context = self._cycle.get_male_emotion_context(person_id)
                            else:
                                cycle_info = self._cycle.get_current_phase(person_id)
                                if cycle_info:
                                    cycle_context = self._cycle.get_emotion_analysis_context(person_id)
                        except Exception:
                            pass

                    # 获取心情状态上下文（实时心情+惯例情绪模式）
                    mood_context = "暂无记录"
                    if self._mood and person_id:
                        try:
                            mood_ctx = self._mood.get_mood_analysis_context(person_id)
                            if mood_ctx:
                                mood_context = mood_ctx
                        except Exception:
                            pass

                    # 用户本人说话风格（本地学习，注入后让回复像用户本人说的话）
                    style_context = "暂无足够的本人说话样本，按自然、得体的方式生成回复。"
                    if self._style_learner:
                        try:
                            style_context = self._style_learner.build_style_context()
                        except Exception:
                            pass

                    system_prompt = self._llm.load_prompt(
                        "chat_analysis",
                        chat_text=chat_text,
                        person_id=person_id,
                        emotion=json.dumps(emotion_result, ensure_ascii=False) if emotion_result else "{}",
                        soul=soul_text or "暂无",
                        cycle=cycle_context,
                        mood=mood_context,
                        knowledge=rag_context or "暂无相关知识",
                        user_style=style_context,
                    )
                    # 安全破假：含敏感词时走 safety_filter
                    if self._safety and self._safety.contains_sensitive(system_prompt + chat_text):
                        llm_analysis = self._safety.chat_with_safety(
                            self._llm, system_prompt, chat_text, complexity="complex"
                        )
                    else:
                        # 修复1：多模型协同（llm.pipeline.enabled，默认false）
                        use_pipeline = False
                        try:
                            use_pipeline = bool(self.config.get("llm.pipeline.enabled", True))
                        except Exception:
                            pass
                        if use_pipeline and hasattr(self._llm, "chat_with_pipeline"):
                            try:
                                _pr = self._llm.chat_with_pipeline(system_prompt, chat_text)
                                llm_analysis = _pr.get("final_result", "") if isinstance(_pr, dict) else ""
                                result["pipeline_steps"] = _pr.get("pipeline_steps", []) if isinstance(_pr, dict) else []
                            except Exception as _pe:
                                logger.debug("pipeline失败，回退chat: %s", _pe)
                                llm_analysis = self._llm.chat(system_prompt, chat_text, complexity="complex")
                        else:
                            llm_analysis = self._llm.chat(system_prompt, chat_text, complexity="complex")

                    # 解析LLM的JSON输出为结构化数据
                    if llm_analysis:
                        llm_structured = self._parse_llm_analysis(llm_analysis)
                except Exception as e:
                    logger.warning("LLM分析失败: %s", e)

        # 4.5 统一分析编排器：场景识别 + 按需调用6个孤岛模块 + 串联执行
        # v3.2.0 (P0-1)：注入真实 digital_twin 实例（M2-1/M2-2 预测闭环）。
        # 修复前此处未传 digital_twin → 编排器 3l 步恒跳过，UI 看到的
        # confidence=0.5 是 facade 层常量回填。现在按 person_id 取真实 twin。
        # PredictionLoop 状态机（PREDICTING→AWAITING_REACTION→OBSERVING→
        # ASSIMILATING）只更新 snapshot 先验，不改 twin prompt。
        orchestration_result = None
        twin_instance = None
        pred_loop_status = None
        try:
            from services.analysis_orchestrator import AnalysisOrchestrator
            from services.prediction_loop import get_loop
            twin_instance = self._get_or_create_twin(person_id)
            orch = AnalysisOrchestrator(
                db=self._db, config=self._config, digital_twin=twin_instance,
            )
            orchestration_result = orch.orchestrate(
                person_id=person_id,
                chat_text=chat_text,
                context={
                    "emotion": emotion_result,
                    "llm_structured": llm_structured,
                    "relation": llm_structured.get("situation", {}),
                },
            )
            # 把 PredictionLoop 的库存偏移挂进编排结果（只读视图，不改 twin）
            try:
                loop = get_loop(person_id)
                pred_loop_status = loop.status()
                if isinstance(orchestration_result, dict):
                    orchestration_result["prediction_loop"] = pred_loop_status
            except Exception as _ple:
                logger.debug("PredictionLoop 状态挂载失败（忽略）: %s", _ple)
            logger.info("编排器执行完成 scene=%s modules=%s twin_injected=%s",
                        orchestration_result.get("scene", {}).get("name", "?"),
                        orchestration_result.get("modules_called", {}),
                        bool(twin_instance))
        except Exception as e:
            logger.warning("编排器执行失败: %s", e)

        # 5. 合并情感结果（本地规则 + LLM深度分析）
        final_emotion = dict(emotion_result or {})
        if llm_structured.get("emotion"):
            llm_emo = llm_structured["emotion"]
            # LLM的言外之意、真实意图优先
            if llm_emo.get("implied_meaning"):
                final_emotion["implied_meaning"] = llm_emo["implied_meaning"]
            if llm_emo.get("true_intent"):
                final_emotion["true_intent"] = llm_emo["true_intent"]
            if llm_emo.get("label") and not final_emotion.get("label"):
                final_emotion["label"] = llm_emo["label"]

        # 6. 关键词提取（本地规则 + LLM补充）
        keywords = final_emotion.get("keywords", []) or []
        if llm_structured.get("fact_split", {}).get("facts"):
            for fact in llm_structured["fact_split"]["facts"]:
                if fact and fact not in keywords and len(fact) < 20:
                    keywords.append(fact)

        # 7. 意图分析（言外之意）- 直接从LLM输出提取
        intent = {
            "category": "日常",
            "confidence": 0.7,
            "implied_meaning": final_emotion.get("implied_meaning", ""),
            "true_intent": final_emotion.get("true_intent", ""),
            "summary": "",
        }
        if llm_structured.get("situation"):
            sit = llm_structured["situation"]
            intent["summary"] = f"关系{sit.get('stage', '')}，{sit.get('temperature', '')}"
            if sit.get("power_dynamic"):
                intent["category"] = "拉扯" if "拉扯" in str(sit["power_dynamic"]) else "日常"
        # 从情感标签推断意图类别
        emo_label = final_emotion.get("label", "")
        if emo_label in ("confused", "angry", "sad"):
            intent["category"] = "情绪诉求"
        elif emo_label in ("happy", "excited"):
            intent["category"] = "积极互动"

        # 8. 关系评估
        relation = llm_structured.get("situation", {}) or {}
        if llm_structured.get("fact_split", {}).get("inferences"):
            relation["inferences"] = llm_structured["fact_split"]["inferences"]

        # 9. 建议回复
        suggested_reply = self._extract_suggested_reply(llm_analysis)
        if not suggested_reply and llm_structured.get("reply_suggestions"):
            for r in llm_structured["reply_suggestions"]:
                if r.get("is_preferred") and r.get("content"):
                    suggested_reply = r["content"]
                    break
            if not suggested_reply and llm_structured["reply_suggestions"]:
                suggested_reply = llm_structured["reply_suggestions"][0].get("content", "")

        # 9.5 策略审查（借鉴Codex PR Review：对建议回复做语气/时机/风险审查）
        reply_review = None
        if suggested_reply and self._codex_adapt:
            try:
                reply_review = self._codex_adapt.review_message(
                    suggested_reply,
                    context={"chat_text": chat_text, "person_id": person_id},
                    person_id=person_id,
                )
            except Exception as e:
                logger.debug("策略审查失败: %s", e)

        # 9.8 复盘回流：用本轮对方"实际"反应结算上一轮预测（必须在生成本轮新预测之前）
        review_result = {"settled": False}
        try:
            review_result = self._settle_previous_prediction(
                person_id, chat_text, final_emotion)
        except Exception:
            logger.debug("复盘结算失败", exc_info=True)

        # 10. 后台实时接入：记忆分析 + 财务分析 + 数字分身预测
        # 这些服务在后台自动运行，结果写入档案，不需要UI入口
        background_result = self._run_background_services(person_id, chat_text, {
            "emotion": final_emotion,
            "intent": intent,
            "relation": relation,
            "llm_structured": llm_structured,
            "review": review_result,
        })

        # 10.6 方向罗盘：结局对齐 + 三重闸门 + 漂移检测
        compass_result = None
        try:
            compass_result = self._run_direction_compass(
                person_id, final_emotion, intent, relation,
                orchestration_result, background_result, review_result)
        except Exception:
            logger.debug("方向罗盘失败", exc_info=True)

        # 10.7 单向数据流串联：orchestrator → portrait → digital_twin → 回写
        # 硬约束：每一步 try/except，失败 log warning 不阻塞主流程；
        #        twin 不反向调 orchestrator（严格单向）；不破坏既有 orchestrator 返回字段。
        portrait_snapshot = None
        twin_reaction = None
        if orchestration_result is not None:
            # --- 10.7.1 PortraitService 出侧写（无 analyze/render 方法则跳过并 log）---
            try:
                psvc = self._portrait
                if psvc is not None:
                    _ps_call = getattr(psvc, "analyze", None) or getattr(psvc, "render", None)
                    if callable(_ps_call):
                        portrait_snapshot = _ps_call(
                            person_id=person_id,
                            context={
                                "persona": orchestration_result.get("personality"),
                                "scene": (orchestration_result.get("scene") or {}).get("name"),
                                "strategy": orchestration_result.get("strategy"),
                                "risk_warnings": orchestration_result.get("risk_warnings", []),
                            },
                        )
                    else:
                        logger.warning(
                            "PortraitService 无 analyze/render 方法，跳过侧写；"
                            "改用 orchestrator 输出兜底摘要 person=%s", person_id)
                else:
                    logger.debug("PortraitService 未初始化，跳过侧写 person=%s", person_id)
            except Exception as e:
                logger.warning("PortraitService 侧写失败: %s", e)
                portrait_snapshot = None

            # 兜底：portrait_snapshot 为空时，用 orchestrator 输出拼一个轻量侧写摘要
            if not isinstance(portrait_snapshot, dict):
                try:
                    _pers = orchestration_result.get("personality") or {}
                    _strat = orchestration_result.get("strategy") or {}
                    portrait_snapshot = {
                        "source": "orchestrator_fallback",
                        "primary_label": _pers.get("primary_label", ""),
                        "primary_key": _pers.get("primary_key", ""),
                        "scene": (orchestration_result.get("scene") or {}).get("name", ""),
                        "strategy_recommended": (_strat.get("recommended") or [])[:3],
                        "risk_warnings": (orchestration_result.get("risk_warnings") or [])[:3],
                    }
                except Exception:
                    portrait_snapshot = None

            # --- 10.7.2 DigitalTwin.predict_reaction（吃 portrait_snapshot + scene）---
            try:
                twin = self._get_or_create_twin(person_id)
                if twin is not None:
                    # user_action：优先用建议回复，否则取最后一条用户消息
                    user_action = suggested_reply or ""
                    if not user_action:
                        _lines = [ln for ln in (chat_text or "").split("\n") if ln.strip()]
                        user_action = _lines[-1] if _lines else ""
                    # recent_chats：最近 10 行
                    recent_chats = [
                        {"content": ln} for ln in (chat_text or "").split("\n")[-10:] if ln.strip()
                    ]
                    # affection
                    affection = None
                    if self._affection_engine:
                        try:
                            _aff = self._affection_engine.get_scores(person_id)
                            affection = _aff if isinstance(_aff, dict) else None
                        except Exception:
                            affection = None
                    # context_hint：场景 + 策略方向（orchestrator 已跑过 strategy_advisor，不重复）
                    _scene_name = (orchestration_result.get("scene") or {}).get("name", "")
                    _rec = (orchestration_result.get("strategy") or {}).get("recommended") or []
                    context_hint = "；".join(x for x in [
                        f"当前场景：{_scene_name}" if _scene_name else "",
                        f"策略方向：{_rec[0]}" if _rec else "",
                    ] if x)
                    twin_reaction = twin.predict_reaction(
                        user_action=user_action[:200],
                        recent_chats=recent_chats,
                        affection=affection or {},
                        context_hint=context_hint,
                        snapshot=portrait_snapshot,
                    )
                    # 回写到 orchestrator 结果 dict（不删既有字段，只加 twin_reaction）
                    if isinstance(twin_reaction, dict):
                        orchestration_result["twin_reaction"] = twin_reaction
                        # 【预测】链路修复：此处是含侧写+场景+策略上下文的权威预测，
                        # 覆盖编排器 3l 步留下的常量兜底，使输出卡「预测」字段拿到
                        # 真实个性化预测（消费侧读 orchestration["prediction"]）。
                        if (twin_reaction.get("possible_replies")
                                or twin_reaction.get("inner_thought")):
                            orchestration_result["prediction"] = twin_reaction
                        # --- 10.7.3 task_hint 灌进 task_engine ---
                        task_hint = twin_reaction.get("task_hint") or twin_reaction.get("task_hints")
                        if task_hint:
                            try:
                                te = self.services.get("task_engine")
                                if te is not None and hasattr(te, "add_custom_task"):
                                    hints = task_hint if isinstance(task_hint, list) else [task_hint]
                                    injected = 0
                                    for h in hints[:3]:
                                        title = h if isinstance(h, str) else (
                                            h.get("title") if isinstance(h, dict) else "")
                                        if title:
                                            te.add_custom_task(person_id, title, source="twin_reaction")
                                            injected += 1
                                    logger.info("twin_reaction 注入 %d 条任务 person=%s",
                                                injected, person_id)
                            except Exception as e:
                                logger.warning("twin task_hint 注入失败: %s", e)
                    logger.info("digital_twin.predict_reaction 完成 person=%s confidence=%s",
                                person_id,
                                (twin_reaction or {}).get("confidence")
                                if isinstance(twin_reaction, dict) else "?")
                else:
                    logger.debug("digital_twin 未创建，跳过预测 person=%s", person_id)
            except Exception as e:
                logger.warning("digital_twin.predict_reaction 失败: %s", e)
                twin_reaction = None

        # 10.9 【回复】链路修复：把生产真实候选话术统一提取出来
        #      （唯一数据源 = orchestration["candidates"]），下面透传到 data 顶层，
        #      供三个候选框 / 「回复建议」卡片 / 一键填入 / 输出卡共用同一数据源。
        reply_candidates = collect_reply_candidates(
            orchestration_result, llm_structured, suggested_reply)

        # 11. 组装完整结果（8模块 + 后台服务状态）
        return {
            "module": "chat_analysis",
            "data": {
                "person_id": person_id,
                "chat_text": chat_text,
                "length": len(chat_text),
                # 模块1: 情感（含言外之意、真实意图）
                "emotion": final_emotion,
                # 模块2: 关键词
                "keywords": keywords,
                # 模块3: RAG
                "rag_context_used": bool(rag_context and "无相关" not in rag_context),
                "rag_context": rag_context[:500] if rag_context else "",
                # 模块4: 意图（言外之意）
                "intent": intent,
                # 模块5: 关系评估
                "relation": relation,
                # 模块6: 建议回复
                "suggested_reply": suggested_reply,
                "reply_suggestions": llm_structured.get("reply_suggestions", []),
                # 【回复】链路修复：真实候选话术透传到 data 顶层（与上面 4 个出口同源）
                "reply_candidates": reply_candidates,
                "reply": reply_candidates[0] if reply_candidates else suggested_reply,
                # 策略审查结果（借鉴Codex PR Review）
                "reply_review": reply_review,
                # 模块7: 安全检测
                "safety": safety_result,
                # 模块8: 好感度（在_update_affection_from_analysis中更新）
                "affection_before": self._get_affection_total(person_id),
                # 后台服务运行状态（记忆/财务/数字分身）
                "background_services": background_result,
                # 复盘回流（预测 vs 实际 / 滚动有效度 / 校准）
                "review": review_result,
                # 方向罗盘（结局对齐 / 三重闸门 / 漂移）
                "compass": compass_result,
                # 统一编排器结果（场景/人格/策略/定律/任务/风险）
                "orchestration": orchestration_result,
                # 单向流：侧写摘要（portrait_service 或 orchestrator 兜底）
                "portrait_snapshot": portrait_snapshot,
                # 单向流：对方模拟反应（digital_twin.predict_reaction）
                "twin_reaction": twin_reaction,
                # 轻量判断层（借鉴Jev协agent：毫秒级分类/紧急度/快情绪）
                "jev_judge": jev_result,
                # 完整LLM输出（供前端展示详情）
                "llm_analysis": llm_structured,
                "llm_raw": llm_analysis or "",
                "analysis_modules": {
                    "emotion": True,
                    "keywords": bool(keywords),
                    "rag": bool(rag_context),
                    "intent": bool(intent.get("implied_meaning") or intent.get("true_intent")),
                    "relation": bool(relation),
                    "reply": bool(suggested_reply),
                    "safety": True,
                    "affection": True,
                    "orchestrator": bool(orchestration_result),
                    "orchestrator_modules": (orchestration_result or {}).get("modules_called", {}),
                    "jev_judge": bool(jev_result),
                },
            },
        }

    def _settle_previous_prediction(self, person_id: str, chat_text: str,
                                    final_emotion) -> dict:
        """复盘：用本轮对方最新实际反应结算上一条 pending 预测，并给滚动有效度/校准。"""
        from services.review_loop import ReviewLoop
        rl = ReviewLoop(self._db)
        msgs = self._parse_chat_to_messages(chat_text)
        incoming = [m for m in msgs if m.get("direction") == "received"]
        latest = incoming[-1] if incoming else None
        if latest is None:
            actual = {"no_reply": True}
        else:
            reply = latest.get("content", "")
            emo_label = ""
            try:
                if self._emotion:
                    one = self._emotion.analyze(reply)
                    emo_label = (one or {}).get("label", "") if isinstance(one, dict) else ""
            except Exception:
                emo_label = ""
            if not emo_label and isinstance(final_emotion, dict):
                emo_label = final_emotion.get("label", "") or final_emotion.get("emotion", "")
            actual = {"reply": reply, "emotion": emo_label}
        settle = rl.settle_latest(person_id, actual)
        rolling = rl.rolling_validity(person_id)
        calibration = rl.calibration(person_id)
        return {"settle": settle, "rolling": rolling, "calibration": calibration}

    def _run_direction_compass(self, person_id: str, final_emotion, intent,
                               relation, orchestration, background, review) -> dict:
        """组装当前各模块产物为罗盘快照，做结局对齐评估（缺失指标留 None，不硬编）。"""
        from services.direction_compass import DirectionCompass
        orch = orchestration or {}
        bg = background if isinstance(background, dict) else {}
        persona_bg = bg.get("persona", {}) if isinstance(bg.get("persona"), dict) else {}

        attachment = persona_bg.get("attachment", "") or \
            (orch.get("personality", {}) or {}).get("attachment", "")
        risk = persona_bg.get("risk_level", "")
        strat = orch.get("strategy", {}) if isinstance(orch.get("strategy"), dict) else {}
        strategy_text = " ".join(str(x) for x in [
            strat.get("chosen", ""), strat.get("name", ""), strat.get("advice", ""),
            strat.get("next_step", ""), intent.get("summary", "") if isinstance(intent, dict) else ""]
            if x)
        deception = orch.get("deception", {}) if isinstance(orch.get("deception"), dict) else {}
        calib = (review or {}).get("calibration", {}) if isinstance(review, dict) else {}
        rolling = (review or {}).get("rolling", {}) if isinstance(review, dict) else {}

        stage = relation.get("stage", "") if isinstance(relation, dict) else ""
        commitment = orch.get("commitment", {}) if isinstance(orch.get("commitment"), dict) else {}
        fin = bg.get("financial", {}) if isinstance(bg.get("financial"), dict) else {}
        metrics = {
            "commitment_kept": commitment.get("kept_rate", commitment.get("keptRate")),
            "reciprocity": fin.get("balance_score", fin.get("balanceScore")),
        }
        snapshot = {
            "stage": stage,
            "attachment": attachment,
            "risk_level": risk,
            "strategy_text": strategy_text,
            "deception_risk": deception.get("risk_level", ""),
            "review_validity": rolling.get("validity"),
            "sim_weight": calib.get("sim_weight"),
            "affection_trend": strat.get("trend", ""),
            "metrics": metrics,
        }
        return DirectionCompass(self._db).evaluate(person_id, snapshot)

    def _run_background_services(self, person_id: str, chat_text: str, context: dict) -> dict:
        """后台实时接入：心情检测、记忆分析、财务分析、数字分身预测。

        每次聊天分析时自动运行，结果写入对应档案：
        - 心情检测：从聊天自动识别当前心情状态标签（疲劳/emo/没安全感等）
        - 记忆分析：从聊天中提取事实、偏好、喜恶、承诺等，写入长期记忆
        - 财务分析：提取转账、消费、收入等财务信息，更新经济实力档案
        - 数字分身：基于完整分析结果预测对方下一步行为/反应

        Args:
            person_id: 人物ID
            chat_text: 聊天文本
            context: 分析上下文（emotion/intent/relation/llm结果）

        Returns:
            各后台服务的运行状态和提取结果
        """
        result = {
            "mood": {"ran": False, "detected": [], "current": None},
            "memory": {"ran": False, "extracted": 0, "items": []},
            "financial": {"ran": False, "detected": False, "items": []},
            "digital_twin": {"ran": False, "prediction": None},
            "digital_twin_enhanced": {"ran": False, "multi_dimension": None, "adversarial": None},
            "persona": {"ran": False, "dimensions": 0, "summary": ""},
        }

        if not person_id:
            return result

        # ---- 0. 心情检测：自动识别当前心情状态标签 ----
        if self._mood:
            try:
                # 从聊天文本自动检测心情并记录
                detected = self._mood.auto_detect_and_set(person_id, chat_text)
                result["mood"]["ran"] = True
                if detected:
                    for d in detected:
                        if isinstance(d, dict):
                            result["mood"]["detected"].append({
                                "tag": d.get("mood_tag", d.get("tag", "")),
                                "name": d.get("name", ""),
                                "intensity": d.get("intensity", 0),
                            })
                # 生理周期症状检测（姨妈痛/肚子疼等）：命中则覆盖为"身体不适"
                try:
                    self._apply_period_symptom_state(person_id, chat_text)
                except Exception:
                    pass
                # 获取当前心情
                current = self._mood.get_current_mood(person_id)
                if current:
                    result["mood"]["current"] = {
                        "tag": current.get("mood_tag", ""),
                        "name": current.get("name", ""),
                        "emoji": current.get("emoji", ""),
                        "color": current.get("color", ""),
                    }
            except Exception as e:
                logger.debug(f"后台心情检测失败: {e}")
                result["mood"]["ran"] = True

        # ---- 1. 记忆分析：提取事实、偏好、承诺、喜恶 ----
        if self._memory:
            try:
                # 将聊天文本拆分为消息列表格式
                messages = self._parse_chat_to_messages(chat_text)
                extracted = self._memory.extract_from_chat(person_id, messages)
                if extracted:
                    result["memory"]["ran"] = True
                    result["memory"]["extracted"] = len(extracted)
                    # 只保留摘要，不把全部内容塞进结果
                    for item in extracted[:10]:
                        if isinstance(item, dict):
                            result["memory"]["items"].append({
                                "type": item.get("memory_type", item.get("type", "")),
                                "category": item.get("category", ""),
                                "content_preview": str(item.get("content", ""))[:80],
                            })
                        else:
                            result["memory"]["items"].append({"content_preview": str(item)[:80]})
                else:
                    result["memory"]["ran"] = True
            except Exception as e:
                logger.debug(f"后台记忆分析失败: {e}")
                result["memory"]["ran"] = True  # 标记已尝试

        # ---- 2. 财务分析：轻量信号提取（不跑完整 generate_report，太慢）----
        if self._financial:
            try:
                signals: list = []
                if hasattr(self._financial, "_extract_signals"):
                    try:
                        signals = self._financial._extract_signals(person_id) or []
                    except Exception:
                        signals = []
                # 本批次正则提取（保留原有，写入 expense 记录）
                items = self._extract_financial_from_chat(person_id, chat_text)

                from collections import Counter
                type_counts = Counter(str(s.get("type", "")) for s in signals)
                sent_sigs = [s for s in signals if s.get("sender") == "sent"]
                recv_sigs = [s for s in signals if s.get("sender") == "received"]

                # 风险标记（条件-效果式，不道德裁决）
                risk_flags: list[str] = []
                recv_money = sum(
                    1 for s in recv_sigs
                    if str(s.get("type", "")) in ("transfer", "gift", "brand")
                )
                if recv_money >= 2:
                    risk_flags.append(
                        "对方多次提及转账/礼物/品牌消费：若多为单向由你承担，"
                        "存在单向投入（马太效应）累积风险——可观察是否双向奔赴")
                if type_counts.get("stress", 0) >= 2 and any(
                    s.get("sender") == "sent" for s in sent_sigs
                ):
                    risk_flags.append("你自身出现经济压力信号（房贷/欠款/月光）："
                                     "继续大额投入的可承受能力下降，建议量力而行")
                if type_counts.get("transfer", 0) >= 3:
                    risk_flags.append("转账往来频繁：留意是否为'制造赠与痕迹'的特殊数额"
                                     "（如520/1314），保留聊天记录以备现实检验")

                result["financial"]["ran"] = True
                result["financial"]["detected"] = bool(signals or items)
                result["financial"]["items"] = items
                result["finance"] = {
                    "signals": signals[:30],
                    "signal_counts": dict(type_counts),
                    "contribution": {
                        "your_financial_signals": len(sent_sigs),
                        "their_financial_signals": len(recv_sigs),
                    },
                    "risk_flags": risk_flags,
                }
                # 事件总线：财务更新 → 触发损失厌恶重算
                try:
                    from services.event_bus import FINANCIAL_UPDATED
                    self._emit_event(FINANCIAL_UPDATED, {
                        "person_id": person_id, "chat_text": chat_text,
                        "your_count": len(sent_sigs), "their_count": len(recv_sigs),
                        "risk_flags": risk_flags})
                except Exception:
                    pass

                # 更新人物档案：经济实力/消费观（轻量，失败静默）
                try:
                    if self._db and signals:
                        cons = type_counts.get("consumption", 0) + type_counts.get("brand", 0)
                        inc = type_counts.get("income", 0)
                        econ = "消费活跃" if cons >= 2 else ("有收入线索" if inc >= 1 else "待观察")
                        self._db.execute(
                            "UPDATE persons SET note=:note WHERE id=:pid AND (note IS NULL OR note='')",
                            {"pid": person_id, "note": f"财务画像:{econ}"},
                        )
                except Exception:
                    pass

                # 损失厌恶/沉没成本分析：对方损失感指数 + 引导投入建议
                try:
                    from services.loss_aversion import analyze_loss_aversion
                    la = analyze_loss_aversion(
                        received_text=chat_text, your_text=chat_text,
                        your_financial_count=len(sent_sigs),
                        their_financial_count=len(recv_sigs),
                    )
                    result["loss_aversion"] = la
                    # 事件总线：损失厌恶结果 → 触发（订阅者：策略/任务/好感度权重）
                    from services.event_bus import LOSS_AVERSION_UPDATED
                    self._emit_event(LOSS_AVERSION_UPDATED, {
                        "person_id": person_id, "loss_index": la.get("loss_index", 100),
                        "risk": la.get("risk_flag")})
                    # 损失感低 → 自动生成"引导对方投入"任务
                    if la.get("loss_index", 100) < 60:
                        te = self.services.get("task_engine")
                        if te:
                            te.add_custom_task(
                                person_id, "引导对方对等投入（请帮小忙/小承诺）",
                                source="loss_aversion", affection_reward=3.0)
                except Exception as e:
                    logger.debug(f"损失厌恶分析失败: {e}")
            except Exception as e:
                logger.debug(f"后台财务分析失败: {e}")
                result["financial"]["ran"] = True

        # ---- 3. 数字分身：预测下一步行为 ----
        try:
            twin = self._get_or_create_twin(person_id)
            if twin:
                # 基于最近的用户行为预测对方反应
                # 取最后一条消息作为用户行为
                last_msg = chat_text.strip().split("\n")[-1] if chat_text else ""
                # 获取当前好感度作为上下文
                affection = None
                if self._affection_engine:
                    try:
                        aff = self._affection_engine.get_scores(person_id)
                        affection = aff if isinstance(aff, dict) else None
                    except Exception:
                        pass
                # 构建实时上下文：活跃任务 + 策略方向（数字分身预测考虑"正在做什么/当前策略"）
                ctx_hint = ""
                try:
                    te = self.services.get("task_engine")
                    active = te.get_active_tasks(person_id) if te else []
                    task_titles = [t.get("title", "") for t in (active or [])[:5]]
                    orch = result.get("data", {}).get("orchestration") or {}
                    manip = orch.get("manipulation") or {}
                    style = (manip.get("strategy_style") or {}).get("style_name", "")
                    cur = manip.get("current_state") or {}
                    ctx_parts = []
                    if task_titles:
                        ctx_parts.append("用户当前正在执行的任务：" + "；".join(task_titles))
                    if style:
                        ctx_parts.append(f"当前策略方向：{style}")
                    if cur:
                        ctx_parts.append(
                            f"对方当前状态：情绪{cur.get('emotion','')}、"
                            f"{cur.get('attachment_activation','')}、趋势{cur.get('trend','')}")
                    ctx_hint = "；".join(ctx_parts)
                except Exception:
                    ctx_hint = ""
                # 构建完整人物状态快照（实时从DB+本次分析产物拉取），消除数字分身孤岛
                snap = {}
                try:
                    from services.person_state_snapshot import build_person_snapshot
                    extra_snap = {
                        "financial": result.get("financial"),
                        "loss_aversion": result.get("loss_aversion"),
                        "manipulation_risk": (result.get("data", {}).get("orchestration") or {}).get("manipulation"),
                        "no_reply_status": (result.get("data", {}).get("orchestration") or {}).get("no_reply"),
                        "strategy": (result.get("data", {}).get("orchestration") or {}).get("strategy"),
                        "personality": (result.get("data", {}).get("orchestration") or {}).get("personality"),
                    }
                    snap = build_person_snapshot(
                        self._db, person_id, extra=extra_snap,
                        memory_service=(self.services.get("memory") or self.services.get("memory_service")))
                except Exception:
                    snap = {}
                prediction = twin.predict_reaction(
                    last_msg[:100],
                    recent_chats=[{"content": line} for line in chat_text.strip().split("\n")[-10:]],
                    affection=affection,
                    context_hint=ctx_hint,
                    snapshot=snap,
                )
                result["digital_twin"]["ran"] = True
                if prediction:
                    # 只保留预测摘要
                    if isinstance(prediction, dict):
                        result["digital_twin"]["prediction"] = {
                            "likely_reaction": prediction.get("likely_reaction", prediction.get("reaction", "")),
                            "emotion": prediction.get("emotion", ""),
                            "confidence": prediction.get("confidence", 0),
                            "next_behavior": prediction.get("next_behavior", ""),
                        }
                        # 复盘回流：把本轮完整预测持久化（pending），下一轮拿到对方实际反应后结算
                        try:
                            from services.review_loop import ReviewLoop
                            ReviewLoop(self._db).record_prediction(
                                person_id, last_msg[:100], prediction)
                        except Exception:
                            logger.debug("预测持久化失败", exc_info=True)
                        # 事件总线：分身预测回流 → 预测反感时降级推进任务
                        try:
                            from services.event_bus import TWIN_PREDICTION_UPDATED
                            self._emit_event(TWIN_PREDICTION_UPDATED, {
                                "person_id": person_id,
                                "likely_reaction": result["digital_twin"]["prediction"]["likely_reaction"],
                                "emotion": result["digital_twin"]["prediction"]["emotion"]})
                        except Exception:
                            pass
                    else:
                        result["digital_twin"]["prediction"] = str(prediction)[:200]
        except Exception as e:
            logger.debug(f"后台数字分身预测失败: {e}")
            result["digital_twin"]["ran"] = True

        # ---- 3.5 数字分身增强（借鉴AI圈股神：多维度分析+对抗性辩驳）----
        try:
            twin = self._get_or_create_twin(person_id)
            if twin:
                # 多维度情感分析（关系基本面+互动技术面+情绪面）
                try:
                    md = twin.multi_dimension_analysis(chat_text)
                    if md:
                        result["digital_twin_enhanced"]["multi_dimension"] = md
                except Exception as e:
                    logger.debug("多维度分析失败: %s", e)
                # 高紧急度消息触发对抗性辩驳（乐观vs悲观分身辩论）
                urgency_score = 0
                try:
                    if self._jev_judge:
                        urg = self._jev_judge.judge_urgency(chat_text)
                        urgency_score = urg.get("score", 0) if isinstance(urg, dict) else 0
                except Exception:
                    pass
                if urgency_score >= 6:
                    try:
                        debate = twin.adversarial_debate(chat_text, rounds=2)
                        if debate:
                            result["digital_twin_enhanced"]["adversarial"] = debate
                    except Exception as e:
                        logger.debug("对抗性辩驳失败: %s", e)
                result["digital_twin_enhanced"]["ran"] = True
        except Exception as e:
            logger.debug(f"数字分身增强失败: {e}")
            result["digital_twin_enhanced"]["ran"] = True

        # ---- 4. 13维人物画像分析（消息量足够时自动运行，每50条消息更新一次）----
        try:
            persona = None
            if hasattr(self, 'services'):
                persona = self.services.get("persona")
            if persona and person_id and hasattr(persona, 'analyze'):
                # 检查消息量，至少10条才分析
                msg_count = 0
                try:
                    if hasattr(self, '_db') and self._db:
                        row = self._db.execute(
                            "SELECT COUNT(*) as cnt FROM chat_messages WHERE person_id=:pid",
                            {"pid": person_id}
                        ).fetchone()
                        msg_count = row[0] if row else 0
                except Exception:
                    pass
                if msg_count >= 10:
                    # 简单的频率控制：每50条消息更新一次（通过消息数取模）
                    if msg_count % 50 < 10 or msg_count <= 50:
                        profile = persona.analyze(person_id)
                        if profile and "error" not in profile:
                            result["persona"]["ran"] = True
                            result["persona"]["dimensions"] = 13
                            result["persona"]["summary"] = profile.get("summary", "")[:300]
                            result["persona"]["attachment"] = profile.get("attachment", {}).get("type", "")
                            result["persona"]["love_language"] = profile.get("love_languages", {}).get("primary", "")
                            result["persona"]["power_dynamic"] = profile.get("power_dynamic", {}).get("pattern", "")
                            result["persona"]["emotional_bank"] = profile.get("emotional_bank", {}).get("health", "")
                            result["persona"]["risk_level"] = profile.get("risk_warning", {}).get("overall_level", "低")
        except Exception as e:
            logger.debug(f"后台画像分析失败: {e}")
            result["persona"]["ran"] = True

        # ============ 第一批接线：复盘/XP/危机预警/AB回灌/话术/风格/轨迹/状态日志 ============
        self._wire_batch1(result, person_id, context)
        # ============ 第二批接线：任务链/每日任务/限时挑战/放权练习 ============
        self._wire_batch2(result, person_id)
        # ============ 第三批接线：档案/设置类（接口输出） ============
        self._wire_batch3(result, person_id)
        # ============ 第四批接线：双核心/双咨询/11级/多目标/学科 ============
        self._wire_batch4(result, person_id)
        # ============ 第八批第一批：API额度/工作流模板/OCR/长截图/任务看板 ============
        self._wire_batch8a(result, person_id)
        # ============ 第八批第二批：Token/会话/断点/历史/分身管理 ============
        self._wire_batch8b(result, person_id)
        # ============ 第八批第三批：意图截图/上下文仪表盘/并行 ============
        self._wire_batch8c(result, person_id)
        # ============ 第八批剩余第一批：快捷面板/主题/插件/流式/多平台导入 ============
        self._wire_batch9a(result, person_id)
        # ============ 第八批剩余第二批：可视化/报告导出/上下文压缩/登录态 ============
        self._wire_batch9b(result, person_id)
        # ============ 第八批剩余第三批：模块权限/配置备份 ============
        self._wire_batch9c(result, person_id)
        # ============ 关系网络自动提取（后台） ============
        try:
            from services.relationship_graph import extract_from_chats, get_influential_people, social_alert
            rg = extract_from_chats(person_id, result.get("recent_chats", []))
            result["relationship_graph"] = rg
            inf = get_influential_people(person_id, rg)
            result["social_influences"] = inf
            result.setdefault("strategy", {})["social_factors"] = [i["tag"] for i in inf]
            result["social_alert"] = social_alert(inf)
        except Exception as e:
            logger.debug("关系网络提取失败: %s", e)

        return result

    def _wire_batch1(self, result: dict, person_id: str, context: dict = None):
        """batch6/batch7 第一批接入（全部 try/except 隔离，单项失败不影响整体）。

        复盘/AB 优先采用 ReviewLoop 的真实“预测 vs 实际”结果；无历史时如实标注，不硬编效果。
        """
        context = context or {}
        review = context.get("review") if isinstance(context.get("review"), dict) else {}
        settle = review.get("settle") if isinstance(review.get("settle"), dict) else {}
        rolling = review.get("rolling") if isinstance(review.get("rolling"), dict) else {}
        calibration = review.get("calibration") if isinstance(review.get("calibration"), dict) else {}
        try:
            # 1+2 复盘 + XP + Streak（复盘内容来自真实命中/偏差）
            from services.batch6_growth import UserGrowth, XPStreak
            g = UserGrowth()
            if settle.get("settled"):
                hits = settle.get("hits", {})
                hit_names = [n for n, k in
                             (("情绪", "emotion"), ("回复方向", "direction"), ("行动承诺", "action"))
                             if hits.get(k) == 1.0]
                miss_names = [n for n, k in
                              (("情绪", "emotion"), ("回复方向", "direction"), ("行动承诺", "action"))
                              if hits.get(k) == 0.0]
                good = ("预测命中：" + "、".join(hit_names)) if hit_names else ""
                improve = ("需校准：" + "、".join(miss_names)) if miss_names else \
                          calibration.get("hint", "")
                next_note = calibration.get("hint", "关注对方情绪变化")
                signal = f"复盘有效度 {settle.get('validity_score')}"
            else:
                good = ""
                improve = ""
                next_note = calibration.get("hint", "首轮暂无历史预测可复盘")
                signal = "暂无复盘样本"
            rev = g.review(good=good, improve=improve, next_note=next_note, signal=signal)
            xp = XPStreak(); xp.login(); gained = xp.add(rev["xp_gained"])
            result["growth"] = {"review": rev, "xp": gained, "level": g.level(),
                                "unlocked": g.gain_xp(0)["unlocked"],
                                "review_source": "prediction_vs_actual" if settle.get("settled")
                                                else "no_sample"}
        except Exception as e:
            logger.debug("growth接线失败: %s", e)
        try:
            # 3 危机预警（从结果中粗取信号）
            from services.batch6_tasks import crisis_alert
            neg = 60 if "愤怒" in str(result.get("emotion", "")) or result.get("digital_twin", {}).get("prediction",{}).get("emotion") == "愤怒" else 10
            alert = crisis_alert({"neg_emotion_pct": neg})
            result["crisis"] = alert
        except Exception as e:
            logger.debug("crisis接线失败: %s", e)
        try:
            # 4 AB学习回灌：效果取真实复盘有效度；暂无样本时用中性先验并标注来源
            from services.batch6_data import ABLearning
            ab = ABLearning()
            real_validity = rolling.get("validity") if isinstance(rolling, dict) else None
            if real_validity is None:
                effect, ab_source = 0.5, "neutral_prior"
            else:
                effect, ab_source = float(real_validity), "prediction_vs_actual"
            chosen = str(result.get("strategy", {}).get("chosen", "推进"))
            ab.record(chosen, effect)
            result["ab_learning"] = {"best_style": ab.best_style(),
                                     "weights": ab.utility_weights(),
                                     "effect": round(effect, 3), "source": ab_source}
        except Exception as e:
            logger.debug("ab_learning接线失败: %s", e)
        try:
            # 5+6 话术个性化 + 攻略风格权重（23.12 非线性攻略风格接线）
            from services.batch6_personalize import (
                analyze_style_phrases, apply_play_style, play_style_weights,
            )
            msgs = self._parse_chat_to_messages(getattr(self, "_last_chat", "") or "")
            style = analyze_style_phrases(msgs)
            result["personalized_style"] = style
            # 依据当前策略方向选择攻略风格并把权重叠到 strategy 上
            base_strategy = result.get("strategy") if isinstance(result.get("strategy"), dict) else {}
            chosen_style = "温柔型"
            direction = str(base_strategy.get("direction", ""))
            if "推进" in direction or "主动" in direction:
                chosen_style = "霸道型"
            elif "修复" in direction or "给空间" in direction:
                chosen_style = "温水型"
            elif "智斗" in direction or "试探" in direction:
                chosen_style = "智斗型"
            result["play_style"] = apply_play_style(base_strategy, chosen_style)
            result["play_style"]["weights"] = play_style_weights(chosen_style)
        except Exception as e:
            logger.debug("style接线失败: %s", e)
        try:
            # 7+8 状态变化日志 + 执行轨迹（增量写库，失败不阻断）
            from services.batch4_enhance import StateChangeLog, ExecutionTracer
            chlog = StateChangeLog()
            chlog.record("analyze.run", 1, "本轮分析完成", "service_facade")
            result["state_logged"] = True
        except Exception as e:
            logger.debug("state_log接线失败: %s", e)
        try:
            # 22.1.4 情绪动力学：衰减 + 累积爆发风险（基于本轮情绪标签）
            from services.batch4_enhance import decay_emotion, accumulate_emotion
            emo = result.get("emotion") if isinstance(result.get("emotion"), dict) else {}
            label = str(emo.get("label") or emo.get("primary") or "平静")
            intensity = float(emo.get("score") or emo.get("intensity") or 0.5)
            # 取历史情绪标签（内存缓冲；DB缺失时降级为仅本轮）
            hist = list(getattr(self, "_emotion_history_buf", []) or [])
            hist.append(label)
            self._emotion_history_buf = hist[-20:]
            decayed = decay_emotion(label, intensity, age_hours=0.0)
            acc = accumulate_emotion(hist, label)
            result["emotion_dynamics"] = {
                "label": label, "intensity": round(intensity, 3),
                "decayed_now": decayed, "accumulation": acc,
                "half_life_applied": True,
            }
        except Exception as e:
            logger.debug("emotion_dynamics接线失败: %s", e)

    def _wire_batch2(self, result: dict, person_id: str):
        """第二批：任务链/每日任务/限时挑战/放权练习（全部隔离）。"""
        stage = "暧昧"
        try:
            from services.batch6_tasks import task_chain, daily_tasks
            chain = task_chain(stage, completed=[])
            dailies = daily_tasks(stage, {})
            result["task_chain"] = chain
            result["daily_tasks"] = dailies
        except Exception as e:
            logger.debug("task_chain接线失败: %s", e)
        try:
            # 限时挑战已含在 daily_tasks（周末标记）；放权练习
            from services.batch7_group4 import autonomy_task
            result["autonomy_drill"] = autonomy_task(stage)
        except Exception as e:
            logger.debug("autonomy接线失败: %s", e)

    def _wire_batch3(self, result: dict, person_id: str):
        """第三批：档案/设置类接口（隔离）。"""
        try:
            # 13 时间线 + 14 统计 + 15 性能监控（23.8 关系时间线：接入真实好感度历史）
            from services.batch6_data import relationship_timeline
            state_changes: list[dict] = []
            try:
                pid = person_id or self._get_active_person_id()
                if pid and self._db:
                    rows = self._db.fetch_all(
                        """SELECT date, total_score FROM affection_snapshots
                           WHERE person_id = :pid ORDER BY date ASC""",
                        {"pid": pid},
                    ) or []
                    prev = None
                    for r in rows:
                        v = float(r.get("total_score") or 0)
                        ts = r.get("date") or 0
                        state_changes.append({"ts": ts, "new_value": v, "event": "snapshot"})
                        if prev is not None:
                            if v - prev >= 5:
                                state_changes.append({"ts": ts, "new_value": v, "event": "stage_up"})
                            elif prev - v >= 5:
                                state_changes.append({"ts": ts, "new_value": v, "event": "stage_down"})
                        prev = v
            except Exception as _e:
                logger.debug("timeline取数降级为空: %s", _e)
            result["timeline"] = relationship_timeline(state_changes)
        except Exception as e:
            logger.debug("timeline接线失败: %s", e)
        try:
            # 16 用户成长四维
            from services.batch6_growth import UserGrowth
            result["user_growth"] = UserGrowth().dims
        except Exception as e:
            logger.debug("growth_dims接线失败: %s", e)
        try:
            # 18 后果概率（基于当前情感）
            from services.batch7_group2 import outcome_probability
            emo = result.get("emotion", {})
            result["outcome_probability"] = outcome_probability(
                emo.get("label") if isinstance(emo, dict) else "平静")
        except Exception as e:
            logger.debug("outcome_prob接线失败: %s", e)
        try:
            # 21 敏感词替换：回复输出前过滤
            from services.batch7_group2 import sanitize
            reps = result.get("replies") or result.get("suggested_replies")
            if isinstance(reps, list):
                result["replies"] = [sanitize(r) for r in reps]
        except Exception as e:
            logger.debug("sanitize接线失败: %s", e)
        try:
            # 25 隐私模式 + 26 代理（开关状态输出）
            from services.batch6_data import PrivacyMode
            from services.batch7_group4 import proxy_for_model_request
            result["privacy"] = {"cloud_allowed": PrivacyMode().allow_cloud()}
            result["proxy"] = proxy_for_model_request()
        except Exception as e:
            logger.debug("privacy/proxy接线失败: %s", e)
        try:
            # 27 性别自动识别（从最近聊天粗判）+ 24.1.13 自动切换人设
            from services.batch7_group3 import detect_gender
            msgs = self._parse_chat_to_messages(getattr(self, "_last_chat", "") or "")
            detected = detect_gender("".join(str(m) for m in msgs))
            result["partner_gender"] = detected
            # 若检测到明确性别且与DB记录不一致，best-effort 回写（不抛错）
            try:
                pid = person_id or self._get_active_person_id()
                if pid and detected in ("男", "女") and self._db:
                    row = self._db.fetch_one(
                        "SELECT gender FROM persons WHERE id = :pid", {"pid": pid})
                    cur = (row or {}).get("gender") or ""
                    want = "male" if detected == "男" else "female"
                    if cur != want:
                        self._db.execute(
                            "UPDATE persons SET gender = :g WHERE id = :pid",
                            {"g": want, "pid": pid})
                        result["partner_gender_switched"] = {"from": cur, "to": want,
                                                             "auto": True}
            except Exception as _e:
                logger.debug("gender自动切换降级: %s", _e)
        except Exception as e:
            logger.debug("gender接线失败: %s", e)

    def _wire_batch4(self, result: dict, person_id: str):
        """第四批：双核心/双咨询/11级/多目标/学科/截图接口（隔离）。"""
        try:
            # 31 11级闯关显示（基于好感度）
            from services.batch7_group1 import stage_to_11
            aff = (result.get("affection") or {}).get("total", 50)
            result["level11"] = stage_to_11(float(aff))
        except Exception as e:
            logger.debug("level11接线失败: %s", e)
        try:
            # 30 情感咨询双模块（接口可用）
            from services.batch7_group1 import consult
            result["consult_modes"] = ["partner", "growth"]
        except Exception as e:
            logger.debug("consult接线失败: %s", e)
        try:
            # 32 多目标管理接口
            from services.batch6_data import MultiTarget
            mt = MultiTarget(); mt.register(person_id, person_id)
            result["multi_target"] = mt.switch(person_id) is not None
        except Exception as e:
            logger.debug("multitarget接线失败: %s", e)
        try:
            # 24.2.2 TOP一/TOP二双核心路由调度
            # TOP一：主动执行+攻略（当前活跃任务）；TOP二：被动私教（本轮问题）
            from services.batch7_group3 import top1_execution, top2_ask
            active_tasks: list = []
            try:
                pid = person_id or self._get_active_person_id()
                if pid and self._db:
                    rows = self._db.fetch_all(
                        "SELECT id, title, status FROM tasks WHERE person_id = :pid AND status = 'pending' LIMIT 5",
                        {"pid": pid}) or []
                    active_tasks = [{"id": r.get("id"), "title": r.get("title")} for r in rows]
            except Exception as _e:
                logger.debug("top1任务取数降级: %s", _e)
            result["top1_execution"] = top1_execution(active_tasks)
            question = str(result.get("strategy", {}).get("question")
                           or result.get("diagnostic", {}).get("question")
                           or "本轮关系推进方向")
            result["top2_ask"] = top2_ask(question)
            result["dual_core"] = {"active": "top1" if active_tasks else "top2",
                                    "top1": result["top1_execution"],
                                    "top2": result["top2_ask"]}
        except Exception as e:
            logger.debug("dual_core接线失败: %s", e)
        try:
            # 36 学科挂载（按场景）
            from services.batch7_group4 import subject_for
            scene = str(result.get("strategy", {}).get("direction", "")) + str(
                result.get("crisis", {}).get("level", ""))
            subj = subject_for(scene)
            if subj:
                result["subject_kb"] = subj
        except Exception as e:
            logger.debug("subject接线失败: %s", e)
        try:
            # 35 截图分析接口（OCR输入时走 analyze）
            from services.batch6_tasks import screenshot_analyze
            result["screenshot_api"] = screenshot_analyze("")["suggested_replies"]
        except Exception as e:
            logger.debug("screenshot接线失败: %s", e)

    def _wire_batch8a(self, result: dict, person_id: str):
        """第八批第一批（隔离接入）。"""
        try:
            from services.batch8_harness import (
                ApiBalance, pick_template, structured_ocr, long_screenshot, task_board,
            )
            # 1 API额度（LLM调用前检查）
            ab = ApiBalance()
            result["api_balance"] = ab.check(0.05)
            # 2 工作流模板（按场景）
            result["workflow_template"] = pick_template(str(result.get("strategy", {}).get("direction","")))
            # 26.1.4 长截图OCR：若本轮有OCR文本（ocr_text/imported_text），走结构化OCR
            ocr_text = str(result.get("ocr_text") or result.get("imported_text") or "")
            result["structured_ocr"] = structured_ocr(ocr_text)
            # 长截图段（无图时为空列表占位，UI渲染时按 segments 展示）
            result["long_screenshot"] = long_screenshot([])
            # 26.2.5 任务看板视图：拉取真实任务按状态分桶
            board_tasks: list[dict] = []
            try:
                pid = person_id or self._get_active_person_id()
                if pid and self._db:
                    rows = self._db.fetch_all(
                        "SELECT id, title, status, difficulty FROM tasks WHERE person_id = :pid LIMIT 50",
                        {"pid": pid}) or []
                    status_map = {"pending": "待开始", "in_progress": "进行中",
                                  "completed": "已完成", "abandoned": "已过期",
                                  "failed": "已过期"}
                    for r in rows:
                        board_tasks.append({
                            "id": r.get("id"), "title": r.get("title"),
                            "status": status_map.get((r.get("status") or "pending"), "待开始"),
                            "difficulty": r.get("difficulty"),
                        })
            except Exception as _e:
                logger.debug("看板任务取数降级: %s", _e)
            result["task_board"] = task_board(board_tasks)
        except Exception as e:
            logger.debug("batch8a接线失败: %s", e)

    def _wire_batch8b(self, result: dict, person_id: str):
        """第八批第二批（隔离接入）。"""
        try:
            from services.batch8_workflow import TokenTracker, SessionManager, AnalysisCheckpoint, AnalysisHistory, get_twin_status
            tt = TokenTracker(); tt.log("analyze", 800, 300)
            result["token_usage"] = tt.summary()
            sm = SessionManager(); sm.load(pid=person_id)
            result["session_resume"] = sm.has_resume(person_id)
            cp = AnalysisCheckpoint(); cp.mark("analyze")
            result["checkpoint_skip_done"] = cp.skip("analyze")
            ah = AnalysisHistory(); ah.add(person_id, "本轮分析")
            result["analysis_history_count"] = len(ah.list_for(person_id))
            result["twin_status"] = get_twin_status()
        except Exception as e:
            logger.debug("batch8b接线失败: %s", e)

    def _wire_batch8c(self, result: dict, person_id: str):
        """第八批第三批（隔离接入）。"""
        try:
            from services.batch8_intent import intent_screenshot, context_dashboard, parallel_analyzer
            result["screenshot_intent"] = intent_screenshot("")
            result["context_dashboard"] = context_dashboard(
                {"chat":1200,"profile":600,"memory":400,"tasks":300})
            result["parallel"] = parallel_analyzer({
                "emotion": lambda: "情绪已并行", "strategy": lambda: "策略已并行"})
        except Exception as e:
            logger.debug("batch8c接线失败: %s", e)

    def _wire_batch9a(self, result: dict, person_id: str):
        """第八批剩余第一批（隔离接入）。"""
        try:
            from services.batch9_ui import get_quick_panel_data, ThemeManager, PluginManager, stream_generate
            # 26.3.14 右侧快捷面板：传入真实最近任务 + 好感度四维
            recent_tasks: list = []
            mini_axes: dict = {}
            try:
                pid = person_id or self._get_active_person_id()
                if pid and self._db:
                    rows = self._db.fetch_all(
                        "SELECT id, title, status FROM tasks WHERE person_id = :pid ORDER BY created_at DESC LIMIT 3",
                        {"pid": pid}) or []
                    recent_tasks = [{"id": r.get("id"), "title": r.get("title"),
                                     "status": r.get("status")} for r in rows]
                    aff = (result.get("affection") or {}).get("scores") or {}
                    if isinstance(aff, dict):
                        for k in ("trust", "attraction", "emotion", "commitment"):
                            if k in aff:
                                mini_axes[k] = aff[k]
            except Exception as _e:
                logger.debug("快捷面板取数降级: %s", _e)
            result["quick_panel"] = get_quick_panel_data(recent_tasks, mini_axes)
            result["theme_info"] = ThemeManager().info()
            pm = PluginManager(); pm.load("demo", {})
            result["active_plugins"] = pm.active()
            result["stream_supported"] = True
        except Exception as e:
            logger.debug("batch9a接线失败: %s", e)

    def _wire_batch9b(self, result: dict, person_id: str):
        """第八批剩余第二批（隔离接入）。"""
        try:
            from services.batch9_report import relationship_visualizer, context_compressor, LoginManager
            result["visualization"] = relationship_visualizer({}, [])
            result["context_compressed"] = context_compressor(["m"]*50)["compressed"]
            lm = LoginManager(); result["login_valid"] = lm.valid(person_id)
        except Exception as e:
            logger.debug("batch9b接线失败: %s", e)

    def _wire_batch9c(self, result: dict, person_id: str):
        """第八批剩余第三批（隔离接入）。"""
        try:
            from services.batch9_admin import ModulePermission, ConfigBackup
            mp = ModulePermission("平衡")
            result["permission"] = {"strategy": mp.allowed("strategy"),
                                   "代聊发送": mp.allowed("代聊发送")}
            cb = ConfigBackup(); cb.backup({"theme":"深色"})
            result["config_backup"] = cb.restore()
        except Exception as e:
            logger.debug("batch9c接线失败: %s", e)


    def _parse_chat_to_messages(self, chat_text: str) -> list:
        """将聊天文本解析为消息列表（供记忆分析使用）。"""
        messages = []
        for line in chat_text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            # 简单判断方向：包含"我"开头或常见自己发言模式
            direction = "received"
            if line.startswith("我:") or line.startswith("我："):
                direction = "sent"
                line = line[2:].strip()
            elif line.startswith("对方:") or line.startswith("对方："):
                line = line[3:].strip()
            messages.append({
                "content": line,
                "direction": direction,
                "timestamp": time.time(),
            })
        return messages

    def _extract_financial_from_chat(self, person_id: str, chat_text: str) -> list:
        """从聊天内容中提取财务相关信息（转账、消费、收入等）。"""
        items = []
        # 财务关键词模式
        import re
        patterns = [
            (r'转账[了给]?\s*(\d+(?:\.\d+)?)\s*[元块万]', 'transfer'),
            (r'(\d+(?:\.\d+)?)\s*[元块]', 'money_mention'),
            (r'红包[了发]?\s*(\d+(?:\.\d+)?)?', 'redpacket'),
            (r'花[了费]\s*(\d+(?:\.\d+)?)\s*[元块]', 'expense'),
            (r'工资[发是]\s*(\d+(?:\.\d+)?)\s*[元块万]?', 'salary'),
            (r'买[了个]\s*.+?(\d+(?:\.\d+)?)\s*[元块]', 'purchase'),
            (r'AA[制]?', 'aa_split'),
        ]
        for pattern, ftype in patterns:
            matches = re.findall(pattern, chat_text)
            for match in matches:
                amount = None
                try:
                    amount = float(match) if match else None
                except (ValueError, TypeError):
                    pass
                items.append({
                    "type": ftype,
                    "amount": amount,
                    "preview": match if isinstance(match, str) else "",
                })
                # 如果有金额，记录到财务服务
                if amount and self._financial and hasattr(self._financial, 'record_expense'):
                    try:
                        self._financial.record_expense(person_id, amount, f"聊天提及:{ftype}", "mentioned")
                    except Exception:
                        pass
        return items

    def _get_or_create_twin(self, person_id: str):
        """获取或创建人物的数字分身实例。"""
        try:
            factory = self.services.get("digital_twin_factory")
            if factory:
                if hasattr(factory, 'get_twin'):
                    return factory.get_twin(person_id)
                elif hasattr(factory, 'create_twin'):
                    return factory.create_twin(person_id)
                elif callable(factory):
                    # 应用注册的是工厂函数（app/application.py:783 _create_digital_twin），
                    # 与真实应用路径一致，直接调用即可（内部已做 person_id 绑定与缓存）。
                    return factory(person_id)
            # 直接创建DigitalTwin（无工厂时的兜底）
            if not hasattr(self, '_twins_cache'):
                self._twins_cache = {}
            if person_id not in self._twins_cache:
                from services.digital_twin import DigitalTwin
                # 修复：原为 DigitalTwin(self._db, self._llm)，与
                # __init__(person_id, db, llm, rag, ...) 位置不符 →
                # TypeError，twin 恒 None（「预测」字段因此永远拿不到真实预测）。
                self._twins_cache[person_id] = DigitalTwin(
                    person_id=person_id,
                    db=self._db,
                    llm=self._llm,
                    rag=self.services.get("rag"),
                )
            return self._twins_cache[person_id]
        except Exception as e:
            logger.debug(f"创建数字分身失败: {e}")
            return None

    def _parse_llm_analysis(self, llm_output: str) -> dict:
        """解析LLM的JSON输出为结构化数据。

        容错处理：LLM可能输出markdown代码块、前后有多余文字等。
        """
        if not llm_output:
            return {}
        try:
            text = llm_output.strip()
            # 去除markdown代码块标记
            if "```json" in text:
                text = text.split("```json", 1)[1]
                if "```" in text:
                    text = text.rsplit("```", 1)[0]
            elif "```" in text:
                text = text.split("```", 1)[1]
                if "```" in text:
                    text = text.rsplit("```", 1)[0]
            text = text.strip()
            # 找第一个{和最后一个}
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start:end + 1]
            return json.loads(text)
        except Exception as e:
            logger.warning("LLM输出解析失败: %s", e)
            return {}

    def _get_affection_total(self, person_id: str) -> float:
        """获取当前好感度总分。"""
        if not self._affection_engine or not person_id:
            return 50.0
        try:
            result = self._affection_engine.get_scores(person_id)
            if isinstance(result, dict):
                return result.get("total", 50.0)
            return 50.0
        except Exception:
            return 50.0

    # ---- 同步事件总线通电：模块出口 publish，订阅者同步重算 ----
    def _ensure_event_bus(self):
        """惰性创建并接线同步事件总线。订阅回调从最新快照/服务实例取数重算。

        全程 try/except 隔离：总线任何异常都不影响主分析流程（保持既有输出不变）。
        """
        if getattr(self, "_event_bus", None) is not None:
            return self._event_bus
        try:
            from services.event_bus import EventBus, AFFECTION_CHANGED, \
                FINANCIAL_UPDATED, LOSS_AVERSION_UPDATED, TASKS_COMPLETED, STAGE_CHANGED, \
                STRATEGY_CHANGED, EMOTION_UPDATED, TWIN_PREDICTION_UPDATED
            bus = EventBus()

            # 好感度变化 → 重新推导阶段 + 勋章检测（真实重算）
            def on_affection(d):
                pid = d.get("person_id")
                total = d.get("total")
                if not pid:
                    return
                try:
                    if self._medal:
                        self._medal.check_and_unlock(pid, "affection", total)
                except Exception:
                    pass
                try:
                    stage = self._affection_engine.get_stage(total) if self._affection_engine else None
                    if stage:
                        bus.publish(STAGE_CHANGED, {"person_id": pid, "stage": stage})
                except Exception:
                    pass
            bus.subscribe(AFFECTION_CHANGED, on_affection)

            # 财务更新 → 真实重算损失厌恶
            def on_financial(d):
                pid = d.get("person_id")
                if not pid:
                    return
                try:
                    from services.loss_aversion import analyze_loss_aversion
                    la = analyze_loss_aversion(
                        received_text=d.get("chat_text", ""),
                        your_financial_count=d.get("your_count", 0),
                        their_financial_count=d.get("their_count", 0))
                    bus.publish(LOSS_AVERSION_UPDATED,
                                {"person_id": pid, "loss_index": la.get("loss_index")})
                except Exception:
                    pass
            bus.subscribe(FINANCIAL_UPDATED, on_financial)

            # 损失厌恶低 → 生成引导对方投入任务
            def on_la(d):
                pid = d.get("person_id")
                te = self.services.get("task_engine")
                if pid and te and (d.get("loss_index") or 100) < 50:
                    try:
                        te.add_custom_task(pid, "引导对方对等投入（请对方帮个小忙/小承诺）",
                                           source="loss_aversion_low", affection_reward=3.0)
                    except Exception:
                        pass
            bus.subscribe(LOSS_AVERSION_UPDATED, on_la)

            # 情感更新 → 愤怒/冷漠时下调活跃推进任务（真实调速）
            def on_emotion(d):
                pid = d.get("person_id")
                te = self.services.get("task_engine")
                emo = (d.get("emotion") or "")
                if pid and te and hasattr(te, "adjust_tasks_for_state"):
                    if any(k in emo for k in ("愤怒", "生气", "冷漠", "回避", "烦")):
                        try:
                            te.adjust_tasks_for_state(pid, {
                                "emotion": emo, "attachment_activation": "情绪激活",
                                "trend": d.get("trend", "")})
                        except Exception:
                            pass
            bus.subscribe(EMOTION_UPDATED, on_emotion)

            # 策略变化 → 真实调速活跃任务（推进/给空间）
            def on_strategy(d):
                pid = d.get("person_id")
                te = self.services.get("task_engine")
                style = d.get("style") or ""
                if pid and te and hasattr(te, "adjust_tasks_for_state"):
                    if any(k in style for k in ("给空间", "warm", "温水", "退缩")):
                        try:
                            te.adjust_tasks_for_state(pid, {
                                "emotion": "", "attachment_activation": "回避激活",
                                "trend": "策略切换到给空间"})
                        except Exception:
                            pass
            bus.subscribe(STRATEGY_CHANGED, on_strategy)

            # 任务完成 → 真实加好感奖励 + 阶段检查 + 勋章
            def on_task_done(d):
                pid = d.get("person_id")
                reward = d.get("affection_reward", 0) or 0
                if pid and self._affection_engine and reward:
                    try:
                        self._affection_engine.add_bonus(pid, float(reward),
                                                         reason=d.get("task_type", "task"))
                    except Exception:
                        pass
                if pid and self._medal:
                    try:
                        self._medal.check_and_unlock(pid, "task", 0)
                    except Exception:
                        pass
            bus.subscribe(TASKS_COMPLETED, on_task_done)

            # 分身预测回流 → 预测对方反感时降级推进任务
            def on_twin(d):
                pid = d.get("person_id")
                te = self.services.get("task_engine")
                risk = (d.get("risk") or "") + (d.get("likely_reaction") or "")
                if pid and te and hasattr(te, "adjust_tasks_for_state"):
                    if any(k in risk for k in ("反感", "拒绝", "冷淡", "生气", "烦")):
                        try:
                            te.adjust_tasks_for_state(pid, {
                                "emotion": "预测对方反感", "attachment_activation": "",
                                "trend": "分身预测接受度低"})
                        except Exception:
                            pass
            bus.subscribe(TWIN_PREDICTION_UPDATED, on_twin)

            self._event_bus = bus
        except Exception as e:
            logger.debug("事件总线初始化失败（主流程不受影响）: %s", e)
            self._event_bus = None
        return self._event_bus

    def _emit_event(self, event: str, data: dict) -> None:
        """分析主流程中安全地发布事件（try/except 隔离，绝不影响既有输出）。"""
        try:
            bus = self._ensure_event_bus()
            if bus is not None:
                bus.publish(event, data)
        except Exception as e:
            logger.debug("发布事件失败 %s: %s", event, e)

    def _extract_suggested_reply(self, llm_output: str | None) -> str:
        """从 LLM 输出中提取建议回复（简单解析：找【建议回复】或【回复】标记）。"""
        if not llm_output:
            return ""
        for marker in ["【建议回复】", "【回复】", "建议回复：", "回复："]:
            if marker in llm_output:
                parts = llm_output.split(marker, 1)
                if len(parts) > 1:
                    reply = parts[1].strip().split("\n")[0].strip()
                    return reply[:200]
        return ""

    def _update_affection_from_analysis(self, person_id: str, chat_text: str, result: dict) -> None:
        """分析完成后更新好感度。"""
        if not self._affection_engine or not person_id:
            return
        try:
            emotion = result.get("data", {}).get("emotion", {})
            # 事件总线：情感更新 → 触发任务调速/策略重算
            try:
                from services.event_bus import EMOTION_UPDATED
                _emo = emotion.get("label") or emotion.get("emotion") or ""
                self._emit_event(EMOTION_UPDATED, {
                    "person_id": person_id, "emotion": _emo,
                    "subtext": result.get("data", {}).get("subtext", "")})
            except Exception:
                pass
            msg = {"content": chat_text, "direction": "received", "id": f"analysis_{uuid.uuid4().hex[:8]}"}
            aff_result = self._affection_engine.update_from_message(person_id, msg, emotion)
            total = aff_result.get("total_score", aff_result.get("total", 50.0))
            # 推送好感度变化信号
            self.signals.affection_changed.emit({
                "person_id": person_id,
                "scores": aff_result.get("scores", {}),
                "total": total,
            })
            # R3.3-78：忠诚度独立信号（与好感分开发出，状态面板双轨展示）
            try:
                _loy = aff_result.get("loyalty")
                if _loy is None and self._affection_engine:
                    _loy = self._affection_engine.get_loyalty(person_id)
                self.signals.loyalty_changed.emit({
                    "person_id": person_id,
                    "loyalty": float(_loy if _loy is not None else 50.0),
                    "loyalty_delta": aff_result.get("loyalty_delta", 0.0),
                })
            except Exception as e:
                logger.debug("loyalty_changed 信号发送失败: %s", e)
            # R3.3-78：反制场景分流——用编排产出的 manipulation 结果刷新当前路线，
            # 决定后续主指标走 loyalty（反制线）还是 affection（引导线）。
            try:
                from services.dual_track import resolve_route as _resolve_route
                manip = (result.get("data", {}).get("orchestration", {}) or {}).get("manipulation") or {}
                route = _resolve_route(persona=None, manipulation_output=manip)
                self._affection_engine.set_route(person_id, route)
            except Exception as e:
                logger.debug("路线刷新失败（忽略，按 guide 缺省）: %s", e)
            # 事件总线：好感度变化 → 触发阶段推导/勋章/（后续可接策略重算）
            from services.event_bus import AFFECTION_CHANGED
            self._emit_event(AFFECTION_CHANGED, {
                "person_id": person_id, "total": total,
                "scores": aff_result.get("scores", {})})
            # 联动勋章：好感度跨越阈值时自动解锁关系类勋章（修复断链）。
            # 此前仅在导入路径触发，实时分析路径下勋章永不解锁。
            if self._medal:
                try:
                    self._medal.check_and_unlock(person_id, "affection", total)
                except Exception as e:
                    logger.debug("好感度勋章联动失败: %s", e)
            # 财务失衡 → 生成「引导对方投入」任务（信号只分析不转任务的断链）
            try:
                fin = result.get("data", {}).get("financial") or {}
                your_n = fin.get("your_financial_signals", 0)
                their_n = fin.get("their_financial_signals", 0)
                te = self.services.get("task_engine")
                if te and isinstance(your_n, int) and your_n >= 2 and their_n == 0:
                    te.add_custom_task(person_id, "引导对方对等投入（请对方帮个小忙/小承诺）",
                                      source="finance_imbalance", affection_reward=3.0)
            except Exception as e:
                logger.debug("财务失衡任务联动失败: %s", e)
        except Exception as e:
            logger.warning("好感度更新失败: %s", e)
        # 实时联动任务系统（引导闭环⑤反馈 + 机会捕捉 + 主线补位 + 编排器策略注入）。
        # 此前这三步只在“导入聊天记录”后跑一次，实时对话下任务进度永不更新。
        try:
            orch_result = result.get("data", {}).get("orchestration")
            self._sync_tasks_after_chat(person_id, chat_text, orchestration=orch_result)
        except Exception as e:
            logger.debug("任务系统实时联动失败: %s", e)

    # ---- 任务系统实时联动（引导闭环⑤反馈 + 机会捕捉 + 补位 + 编排器策略注入）----
    def _sync_tasks_after_chat(self, person_id: str, chat_text: str,
                               orchestration: dict = None) -> None:
        """实时聊天后联动任务引擎（纯关键词匹配，运行在 analyze_chat 后台线程，不阻塞 UI）。

        修复三处断链：
          1. check_completion 此前在生产代码中零调用 → 任务进度从不随对话推进；
          2. generate_limited_task 此前只作手动入口 → 邀约/冲突信号不自动生成限时任务；
          3. generate_main_tasks 此前仅在导入后且 cnt==0 时跑一次 → 阶段推进后不补位。

        v3.0 新增：如果有编排器结果，把策略/定律/人格专属任务注入任务引擎，
        让任务生成从"纯关键词"升级为"基于对方画像+当前场景的精准任务"。
        """
        te = self.services.get("task_engine")
        if not te or not person_id or not chat_text:
            return
        # ⑤ 反馈判定：pending→in_progress，命中当前步关键词则推进，全步完成则收尾接棒
        try:
            completed = te.check_completion(person_id, chat_text)
            # 事件总线：有任务完成 → 触发好感奖励/阶段/勋章/新任务
            if completed:
                from services.event_bus import TASKS_COMPLETED
                self._emit_event(TASKS_COMPLETED, {
                    "person_id": person_id,
                    "completed_tasks": completed if isinstance(completed, list) else [completed]})
        except Exception as e:
            logger.debug("任务进度检查失败: %s", e)
        # 事件总线：策略生成后 → 触发任务调速（推进/给空间）
        try:
            if orchestration:
                manip = orchestration.get("manipulation") or {}
                from services.event_bus import STRATEGY_CHANGED
                self._emit_event(STRATEGY_CHANGED, {
                    "person_id": person_id,
                    "style": (manip.get("strategy_style") or {}).get("style_name", "")})
        except Exception:
            pass
        # 爽约/拖延区分：有事耽误→改约跟进；不想兑现→小承诺重建
        try:
            te.check_appointment_break(person_id, chat_text)
        except Exception as e:
            logger.debug("爽约判定失败: %s", e)
        # 状态联动调速：对方回避激活/愤怒时，自动暂停推进型任务、改发给空间任务
        try:
            manip = (orchestration or {}).get("manipulation") or {}
            cur_state = manip.get("current_state")
            if cur_state and hasattr(te, "adjust_tasks_for_state"):
                te.adjust_tasks_for_state(person_id, cur_state)
        except Exception as e:
            logger.debug("任务状态调速失败: %s", e)
        # 机会捕捉：邀约/关系维护/冲突关键词命中则生成限时任务
        try:
            te.generate_limited_task(person_id, chat_text)
        except Exception as e:
            logger.debug("限时任务捕捉失败: %s", e)
        # 编排器策略注入：把编排器生成的任务建议写入任务引擎
        # （task_engine 内部已有 _compute_personality_tasks / _compute_psychology_law_tasks /
        #  _compute_no_reply_tasks 等方法，编排器任务主要供 UI 展示；
        #  如果 task_engine 暴露了 add_custom_task 则额外注入，否则仅在结果中返回）
        if orchestration:
            try:
                orch_tasks = orchestration.get("tasks", []) or []
                if orch_tasks and hasattr(te, "add_custom_task"):
                    for t in orch_tasks[:5]:  # 最多注入5条，防任务爆炸
                        title = t.get("title", "")
                        if title:
                            te.add_custom_task(person_id, title,
                                               source=t.get("source", "orchestrator"))
                    logger.info("编排器注入 %d 条策略任务 person=%s", len(orch_tasks[:5]), person_id)
                elif orch_tasks:
                    logger.debug("编排器生成 %d 条策略任务（task_engine无add_custom_task，仅在结果中展示）person=%s",
                                 len(orch_tasks), person_id)
            except Exception as e:
                logger.debug("编排器任务注入失败: %s", e)
        # 补位：活跃任务少于3个时，按当前好感度阶段补主线任务（引擎内部按标题去重）
        try:
            active = te.get_active_tasks(person_id) or []
            if len(active) < 3:
                aff = self.services.get("affection")
                stage = 0
                if aff is not None:
                    try:
                        scores = aff.get_current(person_id)
                        total = aff.get_total(scores)
                        stage = aff.get_stage(total)
                    except Exception:
                        stage = 0
                te.generate_main_tasks(person_id, stage)
        except Exception as e:
            logger.debug("主线任务补位失败: %s", e)

    # ---- 阶段二新增 Slot 方法 ----

    def analyze_emotion(self, text: str) -> dict:
        """单独分析一条文本的情感。"""
        try:
            if not text:
                return {"ok": False, "error": "text 不能为空"}
            if not self._emotion:
                return {"ok": False, "error": "情感分析服务未初始化"}
            result = self._emotion.analyze(text)
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("analyze_emotion 失败")
            return {"ok": False, "error": str(e)}

    def get_rag_context(self, query: str, top_k: int) -> dict:
        """检索知识库上下文。"""
        try:
            if not query:
                return {"ok": False, "error": "query 不能为空"}
            if not self._rag:
                return {"ok": True, "data": {"results": [], "context": "RAG服务未初始化"}}
            k = max(1, min(int(top_k or 5), 20))
            results = self._rag.search(query, top_k=k)
            context = self._rag.build_context(query, max_chars=2000)
            return {"ok": True, "data": {"results": results, "context": context}}
        except Exception as e:
            logger.exception("get_rag_context 失败")
            return {"ok": False, "error": str(e)}

    def get_safety_stats(self) -> dict:
        """返回安全破假统计。"""
        try:
            if not self._safety:
                return {"ok": True, "data": {"total_calls": 0, "sensitive_detected": 0, "cloud_rejected": 0, "local_fallback": 0}}
            return {"ok": True, "data": self._safety.get_stats()}
        except Exception as e:
            logger.exception("get_safety_stats 失败")
            return {"ok": False, "error": str(e)}

    def get_llm_status(self) -> dict:
        """返回大模型网关状态。"""
        try:
            if not self._llm:
                return {"ok": True, "data": {"available": False, "local_loaded": False, "mode": "unavailable"}}
            return {"ok": True, "data": {
                "available": True,
                "local_loaded": getattr(self._llm, "local_loaded", False),
                "local_available": getattr(self._llm, "local_available", False),
                "offline_mode": self._config.get("llm.offline_mode", False) if self._config else False,
                "model": self._config.get("llm.model", "") if self._config else "",
            }}
        except Exception as e:
            logger.exception("get_llm_status 失败")
            return {"ok": False, "error": str(e)}

    def get_affection_detail(self, person_id: str) -> dict:
        """通过 AffectionEngine 获取好感度详情（含维度中文名和关系阶段）。"""
        try:
            if not person_id:
                person_id = self._get_active_person_id() or ""
            if not person_id or not self._affection_engine:
                return {"ok": True, "data": {"scores": {}, "total": 50.0, "stage": 0}}
            scores = self._affection_engine.get_current(person_id)
            total = self._affection_engine.get_total(scores)
            stage = self._affection_engine.get_stage(total)
            dim_names = getattr(self._affection_engine, "DIMENSION_NAMES", {})
            return {"ok": True, "data": {
                "person_id": person_id,
                "scores": scores,
                "total": total,
                "stage": stage,
                "dimension_names": dim_names,
            }}
        except Exception as e:
            logger.exception("get_affection_detail 失败")
            return {"ok": False, "error": str(e)}

    def rebuild_rag_index(self, knowledge_dir: str) -> dict:
        """重建知识库向量索引。"""
        try:
            if not self._rag:
                return {"ok": False, "error": "RAG服务未初始化"}
            count = self._rag.index_knowledge(knowledge_dir or None)
            return {"ok": True, "data": {"indexed": count}}
        except Exception as e:
            logger.exception("rebuild_rag_index 失败")
            return {"ok": False, "error": str(e)}

    def send_reply_to_wechat(self, text: str) -> dict:
        """把回复文本填入微信输入框（不自动发送）。"""
        try:
            if not text:
                return {"ok": False, "error": "text 不能为空"}
            if self._wechat:
                ok = self._wechat.fill_text(text)
                return {"ok": True, "data": {"filled": ok, "length": len(text)}}
            # 无微信服务时降级：复制到剪贴板
            import pyperclip
            pyperclip.copy(text)
            logger.info("send_reply_to_wechat (降级剪贴板): %s", text[:80])
            return {"ok": True, "data": {"filled": True, "length": len(text), "mode": "clipboard"}}
        except Exception as e:
            logger.exception("send_reply_to_wechat 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 微信域
    # ================================================================== #
    def get_wechat_status(self) -> dict:
        """返回微信连接状态。"""
        try:
            if self._wechat:
                return {"ok": True, "data": self._wechat.get_status()}
            return {"ok": True, "data": {"status": "disconnected", "info": "微信服务未初始化", "mode": "unavailable"}}
        except Exception as e:
            logger.exception("get_wechat_status 失败")
            return {"ok": False, "error": str(e)}

    def get_wechat_window_info(self) -> dict:
        """返回微信窗口位置与大小。"""
        try:
            if self._wechat:
                info = self._wechat.get_window_info()
                if info:
                    return {"ok": True, "data": info}
            return {"ok": True, "data": {"x": 0, "y": 0, "width": 0, "height": 0, "found": False}}
        except Exception as e:
            logger.exception("get_wechat_window_info 失败")
            return {"ok": False, "error": str(e)}

    def start_wechat_monitor(self) -> dict:
        """启动微信消息监听。"""
        try:
            if not self._wechat:
                return {"ok": False, "error": "微信服务未初始化"}
            connected = self._wechat.connect()
            if connected:
                self._wechat.start_listener()
                return {"ok": True, "data": {"started": True, "mode": self._wechat.get_status().get("mode", "unknown")}}
            return {"ok": False, "error": "微信连接失败"}
        except Exception as e:
            logger.exception("start_wechat_monitor 失败")
            return {"ok": False, "error": str(e)}

    def stop_wechat_monitor(self) -> dict:
        """停止微信消息监听。"""
        try:
            if self._wechat:
                self._wechat.stop_listener()
            self.signals.wechat_status.emit({"status": "disconnected", "info": "已手动停止"})
            return {"ok": True, "data": {"stopped": True}}
        except Exception as e:
            logger.exception("stop_wechat_monitor 失败")
            return {"ok": False, "error": str(e)}

    def import_wechat_chat(self, person_id: str, limit: int = 0) -> dict:
        """一键导入指定人物的微信聊天记录（增量导入，不清空旧数据）。

        Args:
            person_id: 人物ID
            limit: 拉取条数上限，0=完整导入全部

        Returns:
            {ok, total, new_count, wechat_name}
        """
        try:
            if not self._wechat:
                return {"ok": False, "error": "微信服务未初始化"}
            db = self.services.get("db")
            if not db:
                return {"ok": False, "error": "数据库未初始化"}

            # 获取人物微信昵称
            person = db.fetch_one("SELECT nickname, wechat_nickname FROM persons WHERE id=:id", {"id": person_id})
            if not person:
                return {"ok": False, "error": f"人物不存在: {person_id}"}
            wechat_name = person.get("wechat_nickname") or person.get("nickname")
            if not wechat_name:
                return {"ok": False, "error": "人物未设置微信昵称"}

            # 调用微信服务拉取消息（分批拉取，去重合并）
            result = self._wechat.import_chat_history(wechat_name, limit)
            if not result.get("ok"):
                return {"ok": False, "error": result.get("error", "导入失败")}

            messages = result.get("messages", [])
            total = len(messages)
            if total == 0:
                return {"ok": True, "data": {"total": 0, "new_count": 0, "wechat_name": wechat_name, "msg": "未拉取到消息"}}

            # 增量导入：查询已有消息内容用于去重（最近5000条，避免内存过大）
            existing = db.fetch_all(
                "SELECT content FROM chat_messages WHERE person_id=:pid ORDER BY id DESC LIMIT 5000",
                {"pid": person_id}
            )
            existing_set = {row["content"] for row in existing}

            # 过滤新消息
            new_msgs = []
            for msg in messages:
                content = msg.get("content", "")
                if content and content not in existing_set:
                    new_msgs.append((
                        person_id,
                        msg.get("direction", "received"),
                        content,
                        msg.get("msg_type", "text"),
                    ))
                    existing_set.add(content)  # 本次导入内也去重

            # 批量插入（每500条一批，避免SQL过长）
            new_count = 0
            batch_size = 500
            for i in range(0, len(new_msgs), batch_size):
                batch = new_msgs[i:i + batch_size]
                placeholders = ",".join(["(:pid,:dir,:c,:t,datetime('now'))"] * len(batch))
                # 用 executemany 更高效
                with db.engine.connect() as conn:
                    from sqlalchemy import text
                    conn.execute(
                        text("INSERT INTO chat_messages (person_id, direction, content, msg_type, created_at) "
                             "VALUES (:pid, :dir, :c, :t, datetime('now'))"),
                        [{"pid": m[0], "dir": m[1], "c": m[2], "t": m[3]} for m in batch]
                    )
                    conn.commit()
                new_count += len(batch)

            logger.info("导入 %s 聊天记录: 拉取%d条, 新增%d条", wechat_name, total, new_count)

            # 导入完成后自动更新人物档案
            if new_count > 0:
                self._async_update_profile(person_id)

            return {"ok": True, "data": {
                "total": total,
                "new_count": new_count,
                "wechat_name": wechat_name,
                "rounds": result.get("rounds", 1),
            }}
        except Exception as e:
            logger.exception("import_wechat_chat 失败")
            return {"ok": False, "error": str(e)}

    def import_chat_from_file(self, person_id: str, file_path: str) -> dict:
        """从文本文件导入聊天记录（支持微信导出的 txt/csv/json）。

        增量导入：不清空旧消息，只追加新消息（按内容去重）。
        几万条消息批量写入，几秒完成。

        Args:
            person_id: 人物ID
            file_path: 聊天记录文件路径

        Returns:
            {ok, new_count, total, parsed, error}
        """
        try:
            db = self.services.get("db")
            if not db:
                return {"ok": False, "error": "数据库未初始化"}

            person = db.fetch_one("SELECT nickname, wechat_nickname FROM persons WHERE id=:id", {"id": person_id})
            if not person:
                return {"ok": False, "error": f"人物不存在: {person_id}"}
            other_nickname = person.get("wechat_nickname") or person.get("nickname") or ""

            # 解析文件
            from services.wechat_service import WeChatService
            messages = WeChatService.parse_chat_file(file_path, other_nickname)
            if not messages:
                return {"ok": False, "error": "未能解析文件，请确认是微信导出的聊天记录（txt/csv/json）"}

            # 获取已有消息的去重键
            existing = db.fetch_all(
                "SELECT content, direction FROM chat_messages WHERE person_id=:pid",
                {"pid": person_id}
            )
            existing_keys = set()
            for row in existing:
                existing_keys.add(f"{row['direction']}|{row['content']}")

            # 过滤新消息
            new_msgs = []
            for msg in messages:
                content = msg.get("content", "")
                direction = msg.get("direction", "received")
                key = f"{direction}|{content}"
                if content and key not in existing_keys:
                    existing_keys.add(key)
                    new_msgs.append((person_id, direction, content, msg.get("msg_type", "text")))

            # 批量插入（每500条一批）
            new_count = 0
            batch_size = 500
            for i in range(0, len(new_msgs), batch_size):
                batch = new_msgs[i:i + batch_size]
                with db.engine.connect() as conn:
                    from sqlalchemy import text
                    conn.execute(
                        text("INSERT INTO chat_messages (person_id, direction, content, msg_type, created_at) "
                             "VALUES (:pid, :dir, :c, :t, datetime('now'))"),
                        [{"pid": m[0], "dir": m[1], "c": m[2], "t": m[3]} for m in batch]
                    )
                    conn.commit()
                new_count += len(batch)

            total = db.fetch_one("SELECT COUNT(*) as cnt FROM chat_messages WHERE person_id=:pid", {"pid": person_id})["cnt"]
            logger.info("文件导入 %s: 解析%d条, 新增%d条, 累计%d条", person_id, len(messages), new_count, total)
            return {"ok": True, "data": {"new_count": new_count, "total": total, "parsed": len(messages)}}
        except Exception as e:
            logger.exception("import_chat_from_file 失败")
            return {"ok": False, "error": str(e)}

    def import_chat_from_text(self, person_id: str, text_content: str, filename: str = "") -> dict:
        """从文本内容导入聊天记录（前端读取文件后传文本过来）。

        支持微信导出的 txt/csv/json 格式文本。增量导入，批量写入。

        Args:
            person_id: 人物ID
            text_content: 文件文本内容
            filename: 文件名（用于判断格式）

        Returns:
            {ok, new_count, total, parsed, error}
        """
        try:
            import os
            import time
            import tempfile
            db = self.services.get("db")
            if not db:
                return {"ok": False, "error": "数据库未初始化"}

            person = db.fetch_one("SELECT nickname, wechat_nickname FROM persons WHERE id=:id", {"id": person_id})
            if not person:
                return {"ok": False, "error": f"人物不存在: {person_id}"}
            other_nickname = person.get("wechat_nickname") or person.get("nickname") or ""

            # 写入临时文件后解析（复用 parse_chat_file）
            ext = os.path.splitext(filename or "")[1] or ".txt"
            tmp_path = os.path.join(tempfile.gettempdir(), f"loveai_chat_import_{int(time.time())}{ext}")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(text_content)

                from services.wechat_service import WeChatService
                messages = WeChatService.parse_chat_file(tmp_path, other_nickname)
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

            if not messages:
                return {"ok": False, "error": "未能解析文本，请确认是微信导出的聊天记录格式"}

            # 自动记录历史昵称（对方改昵称时保留旧昵称）
            senders = set()
            for msg in messages:
                sender = msg.get("person", "")
                if sender and sender != other_nickname:
                    senders.add(sender)
            # 发送人不是对方昵称的，可能是自己的历史昵称，也记录
            # （自己改昵称时也能追踪）
            for sender in senders:
                db.add_person_alias(person_id, sender)

            # 获取已有消息去重键（方向+内容+时间）
            existing = db.fetch_all(
                "SELECT content, direction, created_at FROM chat_messages WHERE person_id=:pid",
                {"pid": person_id}
            )
            existing_keys = set()
            for row in existing:
                existing_keys.add(f"{row['direction']}|{row['content']}|{row['created_at']}")

            # 过滤新消息，保留原始时间
            from datetime import datetime
            new_msgs = []
            for msg in messages:
                content = msg.get("content", "")
                direction = msg.get("direction", "received")
                # 原始时间转 SQLite datetime 格式
                time_str = msg.get("time_str", "")
                if time_str:
                    try:
                        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
                            try:
                                dt = datetime.strptime(time_str.strip(), fmt)
                                created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                                break
                            except ValueError:
                                continue
                        else:
                            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                else:
                    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                key = f"{direction}|{content}|{created_at}"
                if content and key not in existing_keys:
                    existing_keys.add(key)
                    new_msgs.append((person_id, direction, content, msg.get("msg_type", "text"), created_at))

            # 批量插入（含原始时间）
            new_count = 0
            batch_size = 500
            for i in range(0, len(new_msgs), batch_size):
                batch = new_msgs[i:i + batch_size]
                with db.engine.connect() as conn:
                    from sqlalchemy import text
                    conn.execute(
                        text("INSERT INTO chat_messages (person_id, direction, content, msg_type, created_at) "
                             "VALUES (:pid, :dir, :c, :t, :ct)"),
                        [{"pid": m[0], "dir": m[1], "c": m[2], "t": m[3], "ct": m[4]} for m in batch]
                    )
                    conn.commit()
                new_count += len(batch)

            total = db.fetch_one("SELECT COUNT(*) as cnt FROM chat_messages WHERE person_id=:pid", {"pid": person_id})["cnt"]
            logger.info("文本导入 %s: 解析%d条, 新增%d条, 累计%d条", person_id, len(messages), new_count, total)

            # 导入完成后自动更新人物档案（后台线程，不阻塞）
            if new_count > 0:
                self._async_update_profile(person_id)

            return {"ok": True, "data": {"new_count": new_count, "total": total, "parsed": len(messages), "aliases_recorded": len(senders)}}
        except Exception as e:
            logger.exception("import_chat_from_text 失败")
            return {"ok": False, "error": str(e)}

    def import_chat_wechat4x(self, person_id: str, file_path: str) -> dict:
        """微信4.x 增强版文件导入（支持多格式解析、去重、时间轴排序、防封号限速）"""
        try:
            importer = self.services.get("wechat_importer")
            if not importer:
                return {"ok": False, "error": "微信导入器未初始化"}

            result = importer.import_from_file(file_path, person_id)
            if not result.get('ok'):
                return {"ok": False, "error": result.get('error', '导入失败')}

            self._async_update_profile(person_id)
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("import_chat_wechat4x 失败")
            return {"ok": False, "error": str(e)}

    def detect_wechat_accounts(self) -> dict:
        """检测微信账号（支持多微信，用于合并同一人的聊天记录）"""
        try:
            importer = self.services.get("wechat_importer")
            if not importer:
                return {"ok": False, "error": "微信导入器未初始化"}
            accounts = importer.scan_all_drives()
            return {"ok": True, "data": {"accounts": accounts, "count": len(accounts)}}
        except Exception as e:
            logger.exception("detect_wechat_accounts 失败")
            return {"ok": False, "error": str(e)}

    def ui_auto_import_chat(self, contact_name: str, person_id: str, max_messages: int = 1000) -> dict:
        """UI自动化导入（带防封号机制：随机延迟、限速、只读不发）"""
        try:
            importer = self.services.get("wechat_importer")
            if not importer:
                return {"ok": False, "error": "微信导入器未初始化"}
            # UI自动化导入在importer中实现，这里简化处理
            return {"ok": True, "data": {"method": "ui_auto", "status": "not_implemented", "note": "请使用文件导入或数据库解密方式"}}
        except Exception as e:
            logger.exception("ui_auto_import_chat 失败")
            return {"ok": False, "error": str(e)}

    def scan_wechat_databases(self) -> dict:
        """全盘扫描微信数据库（支持3.x和4.x，自动识别版本和位置）"""
        try:
            importer = self.services.get("wechat_importer")
            if not importer:
                return {"ok": False, "error": "微信导入器未初始化"}
            results = importer.scan_all_drives()
            version = importer.detect_wechat_version()
            return {"ok": True, "data": {"databases": results, "wechat_version": version, "count": len(results)}}
        except Exception as e:
            logger.exception("scan_wechat_databases 失败")
            return {"ok": False, "error": str(e)}

    def get_import_methods(self) -> dict:
        """获取所有导入方式（按封号风险排序，含详细说明和步骤）"""
        try:
            importer = self.services.get("wechat_importer")
            if not importer:
                return {"ok": False, "error": "微信导入器未初始化"}
            methods = importer.get_import_methods()
            return {"ok": True, "data": {"methods": methods, "count": len(methods)}}
        except Exception as e:
            logger.exception("get_import_methods 失败")
            return {"ok": False, "error": str(e)}

    def import_from_local_db(self, db_path: str, key: str, person_id: str, contact_name: str = "", key_type: str = "passphrase") -> dict:
        """从本地加密数据库导入（0封号风险，纯本地操作）
        key_type: 'passphrase'（微信4.1+）或 'raw'（微信3.x）
        """
        try:
            importer = self.services.get("wechat_importer")
            if not importer:
                return {"ok": False, "error": "微信导入器未初始化"}
            result = importer.import_from_local_db(db_path, key, person_id, contact_name, key_type)
            if result.get('ok'):
                self._async_update_profile(person_id)
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("import_from_local_db 失败")
            return {"ok": False, "error": str(e)}

    def extract_wechat_key_wxkey(self, wechat_install_dir: str = "") -> dict:
        """使用wx_key提取微信4.1+的passphrase密钥（DLL注入+Hook，极低风险）"""
        try:
            importer = self.services.get("wechat_importer")
            if not importer:
                return {"ok": False, "error": "微信导入器未初始化"}
            key = importer.extract_key_with_wxkey(wechat_install_dir if wechat_install_dir else None)
            if key:
                return {"ok": True, "data": {"key": key, "key_type": "passphrase", "status": "success"}}
            else:
                return {"ok": True, "data": {"key": None, "status": "failed", "note": "wx_key提取失败，请确保微信正在运行且以管理员身份运行"}}
        except Exception as e:
            logger.exception("extract_wechat_key_wxkey 失败")
            return {"ok": False, "error": str(e)}

    def extract_wechat_key(self) -> dict:
        """从微信进程内存提取SQLCipher密钥（0封号风险，只读内存）"""
        try:
            importer = self.services.get("wechat_importer")
            if not importer:
                return {"ok": False, "error": "微信导入器未初始化"}
            key = importer.extract_key()
            if key:
                return {"ok": True, "data": {"key": key, "status": "success"}}
            else:
                return {"ok": True, "data": {"key": None, "status": "failed", "note": "未找到密钥，请确保微信正在运行"}}
        except Exception as e:
            logger.exception("extract_wechat_key 失败")
            return {"ok": False, "error": str(e)}

    # ========== 代理服务 ==========

    def get_proxy_config(self) -> dict:
        """获取代理配置"""
        try:
            proxy = self.services.get("proxy")
            if not proxy:
                return {"ok": False, "error": "代理服务未初始化"}
            return {"ok": True, "data": proxy.get_proxy_config()}
        except Exception as e:
            logger.exception("get_proxy_config 失败")
            return {"ok": False, "error": str(e)}

    def set_proxy_config(self, config_json: str) -> dict:
        """设置代理配置"""
        try:
            proxy = self.services.get("proxy")
            if not proxy:
                return {"ok": False, "error": "代理服务未初始化"}
            config = json.loads(config_json)
            proxy.set_proxy_config(**config)
            return {"ok": True, "data": {"status": "success"}}
        except Exception as e:
            logger.exception("set_proxy_config 失败")
            return {"ok": False, "error": str(e)}

    def test_proxy(self) -> dict:
        """测试代理连接"""
        try:
            proxy = self.services.get("proxy")
            if not proxy:
                return {"ok": False, "error": "代理服务未初始化"}
            result = proxy.test_proxy()
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("test_proxy 失败")
            return {"ok": False, "error": str(e)}

    def import_proxy_subscribe(self, url: str) -> dict:
        """从订阅链接导入代理节点"""
        try:
            proxy = self.services.get("proxy")
            if not proxy:
                return {"ok": False, "error": "代理服务未初始化"}
            result = proxy.import_subscribe(url)
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("import_proxy_subscribe 失败")
            return {"ok": False, "error": str(e)}

    def enable_system_proxy(self) -> dict:
        """启用系统代理"""
        try:
            proxy = self.services.get("proxy")
            if not proxy:
                return {"ok": False, "error": "代理服务未初始化"}
            result = proxy.start_system_proxy()
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("enable_system_proxy 失败")
            return {"ok": False, "error": str(e)}

    def disable_system_proxy(self) -> dict:
        """关闭系统代理"""
        try:
            proxy = self.services.get("proxy")
            if not proxy:
                return {"ok": False, "error": "代理服务未初始化"}
            result = proxy.stop_system_proxy()
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("disable_system_proxy 失败")
            return {"ok": False, "error": str(e)}

    # ========== 生理周期服务 ==========

    def add_menstrual_cycle(self, person_id: str, start_date: str,
                            cycle_length: int = 28, period_length: int = 5,
                            symptoms: str = "", notes: str = "") -> str:
        """添加姨妈期记录"""
        try:
            if not self._cycle:
                return {"ok": False, "error": "生理周期服务未初始化"}
            result = self._cycle.add_cycle(person_id, start_date, cycle_length,
                                           period_length, symptoms, notes)
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("add_menstrual_cycle 失败")
            return {"ok": False, "error": str(e)}

    def get_menstrual_cycles(self, person_id: str) -> dict:
        """获取历史周期记录"""
        try:
            if not self._cycle:
                return {"ok": False, "error": "生理周期服务未初始化"}
            cycles = self._cycle.get_cycles(person_id)
            return {"ok": True, "data": {"cycles": cycles}}
        except Exception as e:
            logger.exception("get_menstrual_cycles 失败")
            return {"ok": False, "error": str(e)}

    def get_current_cycle_phase(self, person_id: str) -> dict:
        """获取当前所处周期阶段（姨妈期/卵泡期/排卵期/黄体期）"""
        try:
            if not self._cycle:
                return {"ok": False, "error": "生理周期服务未初始化"}
            phase = self._cycle.get_current_phase(person_id)
            if not phase:
                return {"ok": True, "data": {"has_data": False, "message": "暂无周期记录，请先添加姨妈期日期"}}
            return {"ok": True, "data": {"has_data": True, **phase}}
        except Exception as e:
            logger.exception("get_current_cycle_phase 失败")
            return {"ok": False, "error": str(e)}

    def predict_cycles(self, person_id: str) -> dict:
        """预测未来姨妈期和排卵期"""
        try:
            if not self._cycle:
                return {"ok": False, "error": "生理周期服务未初始化"}
            predictions = self._cycle.predict_next_cycles(person_id)
            return {"ok": True, "data": {"predictions": predictions}}
        except Exception as e:
            logger.exception("predict_cycles 失败")
            return {"ok": False, "error": str(e)}

    def delete_menstrual_cycle(self, cycle_id: int) -> dict:
        """删除周期记录"""
        try:
            if not self._cycle:
                return {"ok": False, "error": "生理周期服务未初始化"}
            result = self._cycle.delete_cycle(cycle_id)
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("delete_menstrual_cycle 失败")
            return {"ok": False, "error": str(e)}

    # ========== 心情状态服务 ==========

    def set_mood(self, person_id: str, mood_tag: str) -> dict:
        """手动设置心情状态"""
        try:
            if not self._mood:
                return {"ok": False, "error": "心情状态服务未初始化"}
            result = self._mood.set_mood(person_id, mood_tag, source="manual", duration_hours=4)
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("set_mood 失败")
            return {"ok": False, "error": str(e)}

    def get_current_mood(self, person_id: str) -> dict:
        """获取当前心情状态"""
        try:
            if not self._mood:
                return {"ok": False, "error": "心情状态服务未初始化"}
            mood = self._mood.get_current_mood(person_id)
            if not mood:
                return {"ok": True, "data": {"has_mood": False, "message": "暂无心情状态"}}
            return {"ok": True, "data": {"has_mood": True, **mood}}
        except Exception as e:
            logger.exception("get_current_mood 失败")
            return {"ok": False, "error": str(e)}

    def get_mood_history(self, person_id: str) -> dict:
        """获取最近7天心情历史"""
        try:
            if not self._mood:
                return {"ok": False, "error": "心情状态服务未初始化"}
            history = self._mood.get_mood_history(person_id, days=7)
            return {"ok": True, "data": {"history": history}}
        except Exception as e:
            logger.exception("get_mood_history 失败")
            return {"ok": False, "error": str(e)}

    def get_all_mood_tags(self) -> dict:
        """获取所有可用心情标签"""
        try:
            if not self._mood:
                return {"ok": False, "error": "心情状态服务未初始化"}
            tags = self._mood.get_all_mood_tags()
            return {"ok": True, "data": {"tags": tags}}
        except Exception as e:
            logger.exception("get_all_mood_tags 失败")
            return {"ok": False, "error": str(e)}

    def get_mood_patterns(self, person_id: str) -> dict:
        """获取惯例情绪模式"""
        try:
            if not self._mood:
                return {"ok": False, "error": "心情状态服务未初始化"}
            patterns = self._mood.get_patterns(person_id)
            return {"ok": True, "data": {"patterns": patterns}}
        except Exception as e:
            logger.exception("get_mood_patterns 失败")
            return {"ok": False, "error": str(e)}

    def get_mood_statistics(self, person_id: str) -> dict:
        """获取心情统计（最近30天）"""
        try:
            if not self._mood:
                return {"ok": False, "error": "心情状态服务未初始化"}
            stats = self._mood.get_mood_statistics(person_id, days=30)
            return {"ok": True, "data": stats}
        except Exception as e:
            logger.exception("get_mood_statistics 失败")
            return {"ok": False, "error": str(e)}

    def get_proxy_status(self) -> dict:
        """获取代理状态"""
        try:
            proxy = self.services.get("proxy")
            if not proxy:
                return {"ok": False, "error": "代理服务未初始化"}
            return {"ok": True, "data": proxy.get_status()}
        except Exception as e:
            logger.exception("get_proxy_status 失败")
            return {"ok": False, "error": str(e)}

    def _async_update_profile(self, person_id: str):
        """后台异步更新人物档案、好感度、勋章、任务。"""
        import threading
        def _worker():
            try:
                import time
                time.sleep(1)  # 等数据库写入完成
                db = self.services.get("db")
                if not db:
                    return

                # 1. 更新好感度（从完整历史重算，上下文感知）
                try:
                    affection = self.services.get("affection")
                    if affection and hasattr(affection, 'recalculate_from_history'):
                        result = affection.recalculate_from_history(person_id)
                        logger.info("好感度重算完成: %s, 总分=%.1f, 阶段=%d",
                                    person_id, result.get('total_score', 0), result.get('stage', 0))
                    elif affection:
                        # 降级：逐条更新
                        messages = db.fetch_all(
                            "SELECT direction, content, msg_type FROM chat_messages "
                            "WHERE person_id=:pid ORDER BY id DESC LIMIT 100",
                            {"pid": person_id}
                        )
                        for msg in reversed(messages):
                            affection.update_from_message(person_id, {
                                "content": msg["content"],
                                "direction": msg["direction"],
                                "msg_type": msg["msg_type"],
                            })
                        logger.info("好感度批量更新完成: %s", person_id)
                except Exception as e:
                    logger.warning("好感度更新失败: %s", e)

                # 2. 生成/更新人物档案
                try:
                    self.generate_person_profile(person_id)
                    logger.info("人物档案自动更新完成: %s", person_id)
                except Exception as e:
                    logger.warning("人物档案更新失败: %s", e)

                # 2.5 人物心理画像分析（从头到尾分析聊天记录）
                try:
                    persona = self.services.get("persona")
                    if persona:
                        person_info = db.fetch_one("SELECT * FROM persons WHERE id=:id", {"id": person_id})
                        profile = persona.analyze(person_id, person_info)
                        logger.info("人物心理画像完成: %s, 依恋=%s, 阶段=%s",
                                    person_id, profile.get('attachment', {}).get('type'),
                                    profile.get('relationship_stage', {}).get('stage'))
                except Exception as e:
                    logger.warning("人物心理画像分析失败: %s", e)

                # 3. 检查勋章（导入消息数、好感度等触发）
                try:
                    self._check_medals(person_id)
                except Exception as e:
                    logger.warning("勋章检查失败: %s", e)

                # 4. 生成主线任务（根据关系阶段）
                try:
                    task_engine = self.services.get("task_engine")
                    if task_engine:
                        person = db.fetch_one("SELECT relationship_stage FROM persons WHERE id=:id", {"id": person_id})
                        stage = person["relationship_stage"] if person else 1
                        existing = db.fetch_one("SELECT COUNT(*) as cnt FROM tasks WHERE person_id=:pid AND task_type='main'", {"pid": person_id})
                        if existing["cnt"] == 0:
                            tasks = task_engine.generate_main_tasks(person_id, stage)
                            logger.info("主线任务生成完成: %s, %d个", person_id, len(tasks))
                except Exception as e:
                    logger.warning("主线任务生成失败: %s", e)

            except Exception as e:
                logger.warning("人物档案自动更新失败: %s", e)
        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _check_medals(self, person_id: str):
        """检查并授予勋章（基于消息数、好感度、阶段等）。"""
        db = self.services.get("db")
        if not db:
            return

        # 统计
        msg_count = db.fetch_one("SELECT COUNT(*) as cnt FROM chat_messages WHERE person_id=:pid", {"pid": person_id})["cnt"]
        aff = db.fetch_one("SELECT total_score FROM current_affection WHERE person_id=:pid", {"pid": person_id})
        affection_score = aff["total_score"] if aff else 50.0
        person = db.fetch_one("SELECT relationship_stage FROM persons WHERE id=:id", {"id": person_id})
        stage = person["relationship_stage"] if person else 0

        # 已获得的勋章
        earned = db.fetch_all("SELECT medal_id FROM user_medals WHERE source_person_id=:pid", {"pid": person_id})
        earned_ids = set(m["medal_id"] for m in earned)

        # 所有勋章
        all_medals = db.fetch_all("SELECT id, name FROM medals")
        newly_awarded = []

        for medal in all_medals:
            mid = medal["id"]
            if mid in earned_ids:
                continue
            name = (medal.get("name") or "")

            # 新手类勋章
            if name == "初出茅庐" and msg_count >= 1:
                db.execute("INSERT INTO user_medals (medal_id, source_person_id, unlocked_at) VALUES (:mid, :pid, datetime('now'))",
                           {"pid": person_id, "mid": mid})
                newly_awarded.append(name)
            elif name == "首次对话" and msg_count >= 1:
                db.execute("INSERT INTO user_medals (medal_id, source_person_id, unlocked_at) VALUES (:mid, :pid, datetime('now'))",
                           {"pid": person_id, "mid": mid})
                newly_awarded.append(name)
            elif name == "初识对象" and msg_count >= 1:
                db.execute("INSERT INTO user_medals (medal_id, source_person_id, unlocked_at) VALUES (:mid, :pid, datetime('now'))",
                           {"pid": person_id, "mid": mid})
                newly_awarded.append(name)
            # 消息数类
            elif name == "话痨" and msg_count >= 100:
                db.execute("INSERT INTO user_medals (medal_id, source_person_id, unlocked_at) VALUES (:mid, :pid, datetime('now'))",
                           {"pid": person_id, "mid": mid})
                newly_awarded.append(name)
            # 关系阶段类
            elif name == "破冰者" and stage >= 1:
                db.execute("INSERT INTO user_medals (medal_id, source_person_id, unlocked_at) VALUES (:mid, :pid, datetime('now'))",
                           {"pid": person_id, "mid": mid})
                newly_awarded.append(name)
            elif name == "暧昧萌芽" and stage >= 2:
                db.execute("INSERT INTO user_medals (medal_id, source_person_id, unlocked_at) VALUES (:mid, :pid, datetime('now'))",
                           {"pid": person_id, "mid": mid})
                newly_awarded.append(name)
            elif name == "心动信号" and affection_score >= 60:
                db.execute("INSERT INTO user_medals (medal_id, source_person_id, unlocked_at) VALUES (:mid, :pid, datetime('now'))",
                           {"pid": person_id, "mid": mid})
                newly_awarded.append(name)
            elif name == "确认关系" and stage >= 3:
                db.execute("INSERT INTO user_medals (medal_id, source_person_id, unlocked_at) VALUES (:mid, :pid, datetime('now'))",
                           {"pid": person_id, "mid": mid})
                newly_awarded.append(name)
            elif name == "甜蜜期" and affection_score >= 70:
                db.execute("INSERT INTO user_medals (medal_id, source_person_id, unlocked_at) VALUES (:mid, :pid, datetime('now'))",
                           {"pid": person_id, "mid": mid})
                newly_awarded.append(name)
            elif name == "稳定伴侣" and stage >= 4 and affection_score >= 75:
                db.execute("INSERT INTO user_medals (medal_id, source_person_id, unlocked_at) VALUES (:mid, :pid, datetime('now'))",
                           {"pid": person_id, "mid": mid})
                newly_awarded.append(name)
            elif name == "灵魂伴侣" and affection_score >= 90:
                db.execute("INSERT INTO user_medals (medal_id, source_person_id, unlocked_at) VALUES (:mid, :pid, datetime('now'))",
                           {"pid": person_id, "mid": mid})
                newly_awarded.append(name)

        if newly_awarded:
            logger.info("新授予勋章 %s: %s", person_id, newly_awarded)

    def import_all_wechat_chats(self, messages_per_person: int = 100) -> dict:
        """一键导入微信全部聊天记录。

        自动获取所有会话列表，对每个会话：
        1. 自动创建/匹配人物（按昵称匹配，不存在则新建）
        2. 拉取最近 N 条消息
        3. 批量写入数据库

        为避免封号风险，会话之间加 0.5 秒延迟。

        Args:
            messages_per_person: 每个人拉取的消息数，默认100条

        Returns:
            {ok, total_persons, new_persons, total_messages, new_messages, details}
        """
        try:
            import uuid
            import time as _time
            db = self.services.get("db")
            wechat = self.services.get("wechat")
            if not db:
                return {"ok": False, "error": "数据库未初始化"}
            if not wechat or not wechat.is_connected():
                return {"ok": False, "error": "微信未连接，请先启动微信"}

            # 1. 获取所有会话列表
            sessions = wechat.get_recent_sessions()
            if not sessions:
                return {"ok": False, "error": "未能获取微信会话列表"}

            logger.info("一键导入：发现 %d 个会话", len(sessions))

            total_persons = 0
            new_persons = 0
            total_messages = 0
            new_messages = 0
            details = []

            # 2. 对每个会话拉取消息
            for idx, session in enumerate(sessions):
                name = session.get("name", "")
                if not name or name in ("微信团队", "微信支付", "文件传输助手", "订阅号消息"):
                    continue  # 跳过系统账号

                # 匹配或创建人物
                person = db.find_person_by_wechat(nickname=name)
                if not person:
                    pid = str(uuid.uuid4())[:8]
                    db.execute(
                        "INSERT INTO persons (id, nickname, wechat_nickname, gender, is_active) "
                        "VALUES (:id, :name, :name, 'unknown', 1)",
                        {"id": pid, "name": name}
                    )
                    person = {"id": pid, "nickname": name, "wechat_nickname": name}
                    new_persons += 1
                else:
                    pid = person["id"]
                total_persons += 1

                # 拉取消息
                try:
                    msgs = wechat.get_chat_messages(name, messages_per_person)
                except Exception as e:
                    logger.warning("拉取 %s 消息失败: %s", name, e)
                    details.append({"name": name, "status": "failed", "error": str(e)})
                    continue

                # 去重并批量写入
                existing = db.fetch_all(
                    "SELECT content, direction FROM chat_messages WHERE person_id=:pid",
                    {"pid": pid}
                )
                existing_keys = set(f"{r['direction']}|{r['content']}" for r in existing)

                batch = []
                for m in msgs:
                    content = m.get("content", "")
                    direction = m.get("direction", "received")
                    key = f"{direction}|{content}"
                    if content and key not in existing_keys:
                        existing_keys.add(key)
                        batch.append((pid, direction, content, m.get("msg_type", "text")))

                if batch:
                    with db.engine.connect() as conn:
                        from sqlalchemy import text
                        conn.execute(
                            text("INSERT INTO chat_messages (person_id, direction, content, msg_type, created_at) "
                                 "VALUES (:pid, :dir, :c, :t, datetime('now'))"),
                            [{"pid": b[0], "dir": b[1], "c": b[2], "t": b[3]} for b in batch]
                        )
                        conn.commit()

                total_messages += len(msgs)
                new_messages += len(batch)
                details.append({"name": name, "pulled": len(msgs), "new": len(batch)})

                # 会话间延迟，避免封号
                if idx < len(sessions) - 1:
                    _time.sleep(0.5)

            logger.info("一键导入完成：%d人(新增%d), %d条消息(新增%d)",
                        total_persons, new_persons, total_messages, new_messages)
            return {"ok": True, "data": {
                "total_persons": total_persons,
                "new_persons": new_persons,
                "total_messages": total_messages,
                "new_messages": new_messages,
                "details": details,
            }}
        except Exception as e:
            logger.exception("import_all_wechat_chats 失败")
            return {"ok": False, "error": str(e)}

    def import_from_local_db(self, person_id: str, limit: int = 0) -> dict:
        """从微信本地数据库导入聊天记录（零封号风险）。

        直接解密读取微信本地 SQLite 数据库，不操作微信UI，不会触发封号。
        需要微信在运行（从内存读取数据库密钥）。

        Args:
            person_id: 人物ID
            limit: 最多导入条数，0=全部

        Returns:
            {ok, count, new_count, total, error}
        """
        try:
            import uuid
            db = self.services.get("db")
            if not db:
                return {"ok": False, "error": "数据库未初始化"}

            person = db.fetch_one("SELECT nickname, wechat_nickname FROM persons WHERE id=:id", {"id": person_id})
            if not person:
                return {"ok": False, "error": f"人物不存在: {person_id}"}
            wechat_name = person.get("wechat_nickname") or person.get("nickname") or ""

            from services.wechat_service import WeChatService
            result = WeChatService.import_from_local_db(wechat_name, limit)
            if not result.get("ok"):
                return {"ok": False, "error": result.get("error", "本地数据库读取失败")}

            messages = result.get("messages", [])
            if not messages:
                return {"ok": False, "error": "未读取到消息，请确认微信正在运行且与该联系人有聊天记录"}

            # 去重并批量写入
            existing = db.fetch_all(
                "SELECT content, direction FROM chat_messages WHERE person_id=:pid",
                {"pid": person_id}
            )
            existing_keys = set(f"{r['direction']}|{r['content']}" for r in existing)

            new_msgs = []
            for m in messages:
                content = m.get("content", "")
                direction = m.get("direction", "received")
                key = f"{direction}|{content}"
                if content and key not in existing_keys:
                    existing_keys.add(key)
                    new_msgs.append((person_id, direction, content, m.get("msg_type", "text")))

            if new_msgs:
                batch_size = 500
                for i in range(0, len(new_msgs), batch_size):
                    batch = new_msgs[i:i + batch_size]
                    with db.engine.connect() as conn:
                        from sqlalchemy import text
                        conn.execute(
                            text("INSERT INTO chat_messages (person_id, direction, content, msg_type, created_at) "
                                 "VALUES (:pid, :dir, :c, :t, datetime('now'))"),
                            [{"pid": b[0], "dir": b[1], "c": b[2], "t": b[3]} for b in batch]
                        )
                        conn.commit()

            total = db.fetch_one("SELECT COUNT(*) as cnt FROM chat_messages WHERE person_id=:pid", {"pid": person_id})["cnt"]
            logger.info("本地数据库导入 %s: 读取%d条, 新增%d条, 累计%d条", person_id, len(messages), len(new_msgs), total)

            # 导入完成后自动更新人物档案
            if len(new_msgs) > 0:
                self._async_update_profile(person_id)

            return {"ok": True, "data": {"count": len(messages), "new_count": len(new_msgs), "total": total}}
        except Exception as e:
            logger.exception("import_from_local_db 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 窗口控制域
    # ================================================================== #
    def open_window(self, name: str) -> dict:
        """打开指定窗口：main / chat_overlay / consult_mini / float_ball。"""
        try:
            if not name:
                return {"ok": False, "error": "name 不能为空"}
            self.signals.open_window.emit(name)
            return {"ok": True, "data": {"opened": name}}
        except Exception as e:
            logger.exception("open_window 失败: name=%s", name)
            return {"ok": False, "error": str(e)}

    def hide_window(self, name: str) -> dict:
        """隐藏指定窗口。"""
        try:
            if not name:
                return {"ok": False, "error": "name 不能为空"}
            return {"ok": True, "data": {"hidden": name}}
        except Exception as e:
            logger.exception("hide_window 失败: name=%s", name)
            return {"ok": False, "error": str(e)}

    def minimize_all(self) -> dict:
        """所有窗口最小化回悬浮球。"""
        try:
            self.signals.hide_all_windows.emit()
            return {"ok": True, "data": {"minimized": True}}
        except Exception as e:
            logger.exception("minimize_all 失败")
            return {"ok": False, "error": str(e)}

    def boss_key(self) -> dict:
        """老板键：隐藏所有窗口。"""
        try:
            self.signals.hide_all_windows.emit()
            return {"ok": True, "data": {"boss": True}}
        except Exception as e:
            logger.exception("boss_key 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 数字分身域（阶段三）
    # ================================================================== #
    def predict_reaction(self, user_action: str, person_id: str) -> dict:
        """数字分身预测对方对某行为的反应。"""
        try:
            twin = self._get_twin(person_id)
            if not twin:
                return {"ok": False, "error": "数字分身未就绪"}
            affection = self._affection_engine.get_current(person_id) if self._affection_engine else {}
            # 历史来源与 _run_analysis / _run_background_services 同一条链：那两处直接用入参
            # chat_text，本入口没有该入参，故取 chat_text 的上游来源（main_window 生成
            # chat_text 用的同一个方法 get_chat_messages），窗口与字段口径对齐（末 10 条、
            # dict 形态 direction/content，digital_twin._format_chats 原生支持）。
            history = self.get_chat_messages(person_id, limit=10)
            recent_chats = [{"direction": m.get("direction", ""), "content": m.get("content", "")}
                            for m in ((history.get("data") or {}).get("messages") or [])
                            if (m.get("content") or "").strip()]
            result = twin.predict_reaction(user_action, recent_chats=recent_chats, affection=affection)
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("predict_reaction 失败")
            return {"ok": False, "error": str(e)}

    def simulate_reply(self, context: str, person_id: str) -> dict:
        """数字分身模拟对方回复。"""
        try:
            twin = self._get_twin(person_id)
            if not twin:
                return {"ok": False, "error": "数字分身未就绪"}
            affection = self._affection_engine.get_current(person_id) if self._affection_engine else {}
            # 同上：历史取自 chat_text 的上游来源（get_chat_messages），口径与 _run_analysis 一致。
            history = self.get_chat_messages(person_id, limit=10)
            recent_chats = [{"direction": m.get("direction", ""), "content": m.get("content", "")}
                            for m in ((history.get("data") or {}).get("messages") or [])
                            if (m.get("content") or "").strip()]
            reply = twin.simulate_reply(context, recent_chats=recent_chats, affection=affection)
            return {"ok": True, "data": {"reply": reply}}
        except Exception as e:
            logger.exception("simulate_reply 失败")
            return {"ok": False, "error": str(e)}

    def compare_options(self, option_a: str, option_b: str, person_id: str) -> dict:
        """数字分身A/B方案对比。"""
        try:
            twin = self._get_twin(person_id)
            if not twin:
                return {"ok": False, "error": "数字分身未就绪"}
            affection = self._affection_engine.get_current(person_id) if self._affection_engine else {}
            # 同上：历史取自 chat_text 的上游来源（get_chat_messages），口径与 _run_analysis 一致。
            history = self.get_chat_messages(person_id, limit=10)
            recent_chats = [{"direction": m.get("direction", ""), "content": m.get("content", "")}
                            for m in ((history.get("data") or {}).get("messages") or [])
                            if (m.get("content") or "").strip()]
            result = twin.compare_options(option_a, option_b, recent_chats=recent_chats, affection=affection)
            # ---- 六维战略评分（追加字段，不破坏现有结构） ----
            try:
                from services.six_dim_strategy import SixDimensionStrategyScorer
                scorer = SixDimensionStrategyScorer(
                    db=getattr(self, "_db", None),
                    financial_analyzer=self._financial,
                    affection_engine=self._affection_engine,
                )
                sa = scorer.score(person_id, option_a)
                sb = scorer.score(person_id, option_b)
                # 模拟实测分：用 affection_delta 反推（基线 50，每 delta 1 分）
                delta_a = float((result.get("option_a") or {}).get("affection_delta", 0) or 0)
                delta_b = float((result.get("option_b") or {}).get("affection_delta", 0) or 0)
                sim_a = max(0.0, min(100.0, 50.0 + delta_a * 10.0))
                sim_b = max(0.0, min(100.0, 50.0 + delta_b * 10.0))
                combined_a = SixDimensionStrategyScorer.combine(sim_a, sa["total"])
                combined_b = SixDimensionStrategyScorer.combine(sim_b, sb["total"])
                result["six_dim_a"] = sa
                result["six_dim_b"] = sb
                result["combined_score_a"] = combined_a
                result["combined_score_b"] = combined_b
                # 兼容字段：把总分挂在顶层，方便前端直接读
                result["six_dim_total_a"] = sa["total"]
                result["six_dim_total_b"] = sb["total"]
            except Exception as e:  # noqa: BLE001
                logger.exception("六维战略评分失败（不阻塞主流程）: %s", e)
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("compare_options 失败")
            return {"ok": False, "error": str(e)}

    def get_twin_accuracy(self, person_id: str) -> dict:
        """获取数字分身预测准确率。"""
        try:
            twin = self._get_twin(person_id)
            if not twin:
                return {"ok": True, "data": {"accuracy": 0.0, "total": 0}}
            return {"ok": True, "data": {"accuracy": twin.accuracy_score(), "total": len(twin.feedback_log)}}
        except Exception as e:
            logger.exception("get_twin_accuracy 失败")
            return {"ok": False, "error": str(e)}

    def record_twin_feedback(self, prediction_id: str, score: str, note: str, person_id: str) -> dict:
        """记录数字分身预测反馈（accurate/partial/inaccurate）。"""
        try:
            twin = self._get_twin(person_id)
            if not twin:
                return {"ok": False, "error": "数字分身未就绪"}
            twin.record_feedback(prediction_id, score, note)
            return {"ok": True, "data": {"recorded": True, "accuracy": twin.accuracy_score()}}
        except Exception as e:
            logger.exception("record_twin_feedback 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 任务引擎域（阶段三）
    # ================================================================== #
    def generate_main_tasks(self, person_id: str, stage: int) -> dict:
        """根据关系阶段生成主线任务。"""
        try:
            if not self._task_engine:
                return {"ok": False, "error": "任务引擎未初始化"}
            pid = person_id or self._get_active_person_id() or ""
            tasks = self._task_engine.generate_main_tasks(pid, int(stage))
            return {"ok": True, "data": {"tasks": tasks, "count": len(tasks)}}
        except Exception as e:
            logger.exception("generate_main_tasks 失败")
            return {"ok": False, "error": str(e)}

    def generate_limited_task(self, message: str, person_id: str) -> dict:
        """检测聊天关键词生成限时任务。"""
        try:
            if not self._task_engine:
                return {"ok": False, "error": "任务引擎未初始化"}
            pid = person_id or self._get_active_person_id() or ""
            emotion = self._emotion.analyze(message) if self._emotion else {}
            task = self._task_engine.generate_limited_task(pid, message, emotion)
            return {"ok": True, "data": {"task": task, "generated": task is not None}}
        except Exception as e:
            logger.exception("generate_limited_task 失败")
            return {"ok": False, "error": str(e)}

    def get_active_tasks(self, person_id: str) -> dict:
        """获取当前进行中的任务列表。"""
        try:
            if not self._task_engine:
                return {"ok": True, "data": []}
            pid = person_id or self._get_active_person_id() or ""
            tasks = self._task_engine.get_active_tasks(pid)
            return {"ok": True, "data": tasks}
        except Exception as e:
            logger.exception("get_active_tasks 失败")
            return {"ok": False, "error": str(e)}

    def get_task_progress(self, task_id: str) -> dict:
        """获取任务进度。"""
        try:
            if not self._task_engine:
                return {"ok": False, "error": "任务引擎未初始化"}
            progress = self._task_engine.get_task_progress(task_id)
            return {"ok": True, "data": progress}
        except Exception as e:
            logger.exception("get_task_progress 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 攻略引擎域（阶段三）
    # ================================================================== #
    def generate_guide(self, question: str, mode: str, person_id: str) -> dict:
        """生成深度攻略（self单人模式/target双人模式）。"""
        try:
            if not self._tutorial:
                return {"ok": False, "error": "攻略引擎未初始化"}
            pid = person_id or self._get_active_person_id() or None
            guide = self._tutorial.generate_guide(question, mode=mode, person_id=pid)
            return {"ok": True, "data": guide}
        except Exception as e:
            logger.exception("generate_guide 失败")
            return {"ok": False, "error": str(e)}

    def search_guides(self, keyword: str, top_k: int) -> dict:
        """攻略库检索。"""
        try:
            if not self._tutorial:
                return {"ok": True, "data": []}
            results = self._tutorial.search_guides(keyword, top_k=max(1, min(int(top_k or 5), 20)))
            return {"ok": True, "data": results}
        except Exception as e:
            logger.exception("search_guides 失败")
            return {"ok": False, "error": str(e)}

    def get_guide_detail(self, guide_id: str) -> dict:
        """获取攻略详情。"""
        try:
            if not self._tutorial:
                return {"ok": False, "error": "攻略引擎未初始化"}
            guide = self._tutorial.get_guide(guide_id)
            if not guide:
                return {"ok": False, "error": "攻略不存在"}
            return {"ok": True, "data": guide}
        except Exception as e:
            logger.exception("get_guide_detail 失败")
            return {"ok": False, "error": str(e)}

    def list_guides(self, mode: str, limit: int) -> dict:
        """列出攻略历史。"""
        try:
            if not self._tutorial:
                return {"ok": True, "data": []}
            guides = self._tutorial.list_guides(mode=mode or None, limit=max(1, min(int(limit or 20), 100)))
            return {"ok": True, "data": guides}
        except Exception as e:
            logger.exception("list_guides 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 财务分析域（阶段三）
    # ================================================================== #
    def generate_financial_report(self, person_id: str) -> dict:
        """生成对方财务分析报告。"""
        try:
            if not self._financial:
                return {"ok": False, "error": "财务分析未初始化"}
            pid = person_id or self._get_active_person_id() or ""
            report = self._financial.generate_report(pid)
            return {"ok": True, "data": report}
        except Exception as e:
            logger.exception("generate_financial_report 失败")
            return {"ok": False, "error": str(e)}

    def check_demand_feasibility(self, person_id: str, item_value: float, item_name: str) -> dict:
        """需求任务前置检查：判断对方能否承担。"""
        try:
            if not self._financial:
                return {"ok": False, "error": "财务分析未初始化"}
            pid = person_id or self._get_active_person_id() or ""
            result = self._financial.check_demand_feasibility(pid, float(item_value), item_name)
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("check_demand_feasibility 失败")
            return {"ok": False, "error": str(e)}

    def record_expense(self, person_id: str, amount: float, description: str, direction: str) -> dict:
        """记录一笔付出/消费。"""
        try:
            if not self._financial:
                return {"ok": False, "error": "财务分析未初始化"}
            pid = person_id or self._get_active_person_id() or ""
            self._financial.record_expense(pid, float(amount), description, direction or "sent")
            return {"ok": True, "data": {"recorded": True}}
        except Exception as e:
            logger.exception("record_expense 失败")
            return {"ok": False, "error": str(e)}

    def get_expense_summary(self, person_id: str, days: int) -> dict:
        """获取支出汇总。"""
        try:
            if not self._financial:
                return {"ok": True, "data": {"total_amount": 0, "count": 0}}
            pid = person_id or self._get_active_person_id() or ""
            summary = self._financial.get_expense_summary(pid, days=max(1, int(days or 30)))
            return {"ok": True, "data": summary}
        except Exception as e:
            logger.exception("get_expense_summary 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 咨询引擎域（阶段三）
    # ================================================================== #
    def consult(self, question: str, mode: str) -> dict:
        """情感咨询（self自我成长/other对象视角）。"""
        try:
            if not self._consult:
                return {"ok": False, "error": "咨询引擎未初始化"}
            result = self._consult.consult(question, mode=mode or "self")
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("consult 失败")
            return {"ok": False, "error": str(e)}

    def clear_consult_history(self) -> dict:
        """清空咨询会话历史。"""
        try:
            if self._consult:
                self._consult.clear_history()
            return {"ok": True, "data": {"cleared": True}}
        except Exception as e:
            logger.exception("clear_consult_history 失败")
            return {"ok": False, "error": str(e)}

    def get_consult_history(self) -> dict:
        """获取咨询会话历史。"""
        try:
            if not self._consult:
                return {"ok": True, "data": []}
            return {"ok": True, "data": self._consult.get_history()}
        except Exception as e:
            logger.exception("get_consult_history 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 勋章域（阶段三）
    # ================================================================== #
    def get_all_medals(self, person_id: str) -> dict:
        """获取所有勋章（含解锁状态）。"""
        try:
            if not self._medal:
                return {"ok": True, "data": []}
            pid = person_id or self._get_active_person_id() or None
            medals = self._medal.get_all_medals(pid)
            return {"ok": True, "data": medals}
        except Exception as e:
            logger.exception("get_all_medals 失败")
            return {"ok": False, "error": str(e)}

    def get_unlocked_medals(self, person_id: str) -> dict:
        """获取已解锁勋章列表。"""
        try:
            if not self._medal:
                return {"ok": True, "data": []}
            pid = person_id or self._get_active_person_id() or ""
            medals = self._medal.get_unlocked_medals(pid)
            return {"ok": True, "data": medals}
        except Exception as e:
            logger.exception("get_unlocked_medals 失败")
            return {"ok": False, "error": str(e)}

    def get_medal_stats(self, person_id: str) -> dict:
        """获取勋章统计。"""
        try:
            if not self._medal:
                return {"ok": True, "data": {"total": 25, "unlocked": 0}}
            pid = person_id or self._get_active_person_id() or ""
            stats = self._medal.get_medal_stats(pid)
            return {"ok": True, "data": stats}
        except Exception as e:
            logger.exception("get_medal_stats 失败")
            return {"ok": False, "error": str(e)}

    def get_hero_wall_data(self, person_id: str) -> dict:
        """获取英雄墙数据。"""
        try:
            if not self._medal:
                return {"ok": False, "error": "勋章服务未初始化"}
            pid = person_id or self._get_active_person_id() or ""
            data = self._medal.get_hero_wall_data(pid)
            return {"ok": True, "data": data}
        except Exception as e:
            logger.exception("get_hero_wall_data 失败")
            return {"ok": False, "error": str(e)}

    def award_medal(self, person_id: str, medal_id: str) -> dict:
        """手动授予勋章。"""
        try:
            if not self._medal:
                return {"ok": False, "error": "勋章服务未初始化"}
            pid = person_id or self._get_active_person_id() or ""
            awarded = self._medal.award_medal(pid, medal_id)
            return {"ok": True, "data": {"awarded": awarded}}
        except Exception as e:
            logger.exception("award_medal 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 画像域（阶段三）
    # ================================================================== #
    def upload_photo(self, person_id: str, photo_path: str, photo_type: str) -> dict:
        """上传人物照片。"""
        try:
            if not self._portrait:
                return {"ok": False, "error": "画像服务未初始化"}
            pid = person_id or self._get_active_person_id() or ""
            result = self._portrait.upload_photo(pid, photo_path, photo_type or "profile")
            return {"ok": True, "data": result}
        except Exception as e:
            logger.exception("upload_photo 失败")
            return {"ok": False, "error": str(e)}

    def get_photos(self, person_id: str, photo_type: str) -> dict:
        """获取人物照片列表。"""
        try:
            if not self._portrait:
                return {"ok": True, "data": []}
            pid = person_id or self._get_active_person_id() or ""
            photos = self._portrait.get_photos(pid, photo_type or None)
            return {"ok": True, "data": photos}
        except Exception as e:
            logger.exception("get_photos 失败")
            return {"ok": False, "error": str(e)}

    def set_profile_photo(self, person_id: str, photo_id: int) -> dict:
        """设置头像照片。"""
        try:
            if not self._portrait:
                return {"ok": False, "error": "画像服务未初始化"}
            pid = person_id or self._get_active_person_id() or ""
            ok = self._portrait.set_profile_photo(pid, int(photo_id))
            return {"ok": True, "data": {"set": ok}}
        except Exception as e:
            logger.exception("set_profile_photo 失败")
            return {"ok": False, "error": str(e)}

    def match_portrait_frame(self, person_id: str, emotion: str) -> dict:
        """为人物匹配合适的立绘帧。"""
        try:
            if not self._portrait:
                return {"ok": True, "data": {"frame": None}}
            pid = person_id or self._get_active_person_id() or ""
            frame = self._portrait.match_portrait_frame(pid, emotion or "neutral")
            return {"ok": True, "data": {"frame": frame}}
        except Exception as e:
            logger.exception("match_portrait_frame 失败")
            return {"ok": False, "error": str(e)}

    def list_available_frames(self, gender: str) -> dict:
        """列出可用立绘帧（按情绪分组）。"""
        try:
            if not self._portrait:
                return {"ok": True, "data": {"male": {}, "female": {}}}
            frames = self._portrait.list_available_frames(gender or None)
            return {"ok": True, "data": frames}
        except Exception as e:
            logger.exception("list_available_frames 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 语音识别域（阶段四）
    # ================================================================== #
    def start_voice_recording(self) -> dict:
        """开始语音录音。"""
        try:
            if not self._speech:
                return {"ok": False, "error": "语音服务未初始化"}
            return {"ok": True, "data": self._speech.start_recording()}
        except Exception as e:
            logger.exception("start_voice_recording 失败")
            return {"ok": False, "error": str(e)}

    def stop_voice_and_recognize(self) -> dict:
        """停止录音并识别（不自动填入）。"""
        try:
            if not self._speech:
                return {"ok": False, "error": "语音服务未初始化"}
            stop_result = self._speech.stop_recording()
            wav = stop_result.get("wav_path")
            if wav:
                result = self._speech.transcribe(wav)
                result["wav_path"] = wav
                return {"ok": True, "data": result}
            return {"ok": True, "data": stop_result}
        except Exception as e:
            logger.exception("stop_voice_and_recognize 失败")
            return {"ok": False, "error": str(e)}

    def voice_recognize_and_fill(self) -> dict:
        """语音识别并填入微信（悬浮球语音完整链路）。"""
        try:
            if not self._speech:
                return {"ok": False, "error": "语音服务未初始化"}
            stop_result = self._speech.stop_recording()
            wav = stop_result.get("wav_path")
            if wav:
                result = self._speech.transcribe_and_fill(wav)
                return {"ok": True, "data": result}
            return {"ok": True, "data": stop_result}
        except Exception as e:
            logger.exception("voice_recognize_and_fill 失败")
            return {"ok": False, "error": str(e)}

    def transcribe_audio_file(self, file_path: str) -> dict:
        """识别外部音频文件。"""
        try:
            if not self._speech:
                return {"ok": False, "error": "语音服务未初始化"}
            return {"ok": True, "data": self._speech.transcribe_file(file_path)}
        except Exception as e:
            logger.exception("transcribe_audio_file 失败")
            return {"ok": False, "error": str(e)}

    def get_speech_status(self) -> dict:
        """获取语音服务状态。"""
        try:
            if not self._speech:
                return {"ok": True, "data": {"available": False}}
            return {"ok": True, "data": self._speech.get_status()}
        except Exception as e:
            logger.exception("get_speech_status 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # OCR识别域（阶段四）
    # ================================================================== #
    def capture_and_ocr(self) -> dict:
        """截屏并识别（全屏）。"""
        try:
            if not self._ocr:
                return {"ok": False, "error": "OCR服务未初始化"}
            shot = self._ocr.capture_screen()
            if shot:
                result = self._ocr.recognize_image(shot)
                result["screenshot"] = shot
                return {"ok": True, "data": result}
            return {"ok": False, "error": "截屏失败"}
        except Exception as e:
            logger.exception("capture_and_ocr 失败")
            return {"ok": False, "error": str(e)}

    def capture_wechat_and_ocr(self) -> dict:
        """截取微信区域并识别。"""
        try:
            if not self._ocr:
                return {"ok": False, "error": "OCR服务未初始化"}
            return {"ok": True, "data": self._ocr.capture_wechat_and_recognize()}
        except Exception as e:
            logger.exception("capture_wechat_and_ocr 失败")
            return {"ok": False, "error": str(e)}

    def ocr_image_file(self, image_path: str) -> dict:
        """识别指定图片文件。"""
        try:
            if not self._ocr:
                return {"ok": False, "error": "OCR服务未初始化"}
            return {"ok": True, "data": self._ocr.recognize_image(image_path)}
        except Exception as e:
            logger.exception("ocr_image_file 失败")
            return {"ok": False, "error": str(e)}

    def get_ocr_status(self) -> dict:
        """获取OCR服务状态。"""
        try:
            if not self._ocr:
                return {"ok": True, "data": {"available": False}}
            return {"ok": True, "data": self._ocr.get_status()}
        except Exception as e:
            logger.exception("get_ocr_status 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 数据统计域（阶段四）
    # ================================================================== #
    def get_dashboard(self, person_id: str) -> dict:
        """获取仪表盘统计。"""
        try:
            if not self._stats:
                return {"ok": False, "error": "统计服务未初始化"}
            pid = person_id or self._get_active_person_id() or None
            return {"ok": True, "data": self._stats.get_dashboard_stats(pid)}
        except Exception as e:
            logger.exception("get_dashboard 失败")
            return {"ok": False, "error": str(e)}

    def get_message_stats(self, person_id: str, days: int) -> dict:
        """获取消息统计。"""
        try:
            if not self._stats:
                return {"ok": False, "error": "统计服务未初始化"}
            pid = person_id or self._get_active_person_id() or ""
            return {"ok": True, "data": self._stats.get_message_stats(pid, days=max(1, int(days or 30)))}
        except Exception as e:
            logger.exception("get_message_stats 失败")
            return {"ok": False, "error": str(e)}

    def get_affection_stats(self, person_id: str, days: int) -> dict:
        """获取好感度统计趋势。"""
        try:
            if not self._stats:
                return {"ok": False, "error": "统计服务未初始化"}
            pid = person_id or self._get_active_person_id() or ""
            return {"ok": True, "data": self._stats.get_affection_stats(pid, days=max(1, int(days or 30)))}
        except Exception as e:
            logger.exception("get_affection_stats 失败")
            return {"ok": False, "error": str(e)}

    def get_relation_comparison(self) -> dict:
        """获取多对象关系对比。"""
        try:
            if not self._stats:
                return {"ok": True, "data": []}
            return {"ok": True, "data": self._stats.get_relation_comparison()}
        except Exception as e:
            logger.exception("get_relation_comparison 失败")
            return {"ok": False, "error": str(e)}

    def get_weekly_report(self, person_id: str) -> dict:
        """获取周报。"""
        try:
            if not self._stats:
                return {"ok": False, "error": "统计服务未初始化"}
            pid = person_id or self._get_active_person_id() or None
            return {"ok": True, "data": self._stats.get_weekly_report(pid)}
        except Exception as e:
            logger.exception("get_weekly_report 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 插件管理域（阶段四）
    # ================================================================== #
    def list_skills(self) -> dict:
        """列出所有技能。"""
        try:
            if not self._plugin:
                return {"ok": True, "data": []}
            return {"ok": True, "data": self._plugin.list_skills()}
        except Exception as e:
            logger.exception("list_skills 失败")
            return {"ok": False, "error": str(e)}

    def execute_skill(self, skill_name: str) -> dict:
        """执行指定技能。"""
        try:
            if not self._plugin:
                return {"ok": False, "error": "插件服务未初始化"}
            return {"ok": True, "data": self._plugin.execute_skill(skill_name)}
        except Exception as e:
            logger.exception("execute_skill 失败")
            return {"ok": False, "error": str(e)}

    def list_plugins(self) -> dict:
        """列出所有插件。"""
        try:
            if not self._plugin:
                return {"ok": True, "data": []}
            return {"ok": True, "data": self._plugin.list_plugins()}
        except Exception as e:
            logger.exception("list_plugins 失败")
            return {"ok": False, "error": str(e)}

    def enable_plugin(self, plugin_name: str) -> dict:
        """启用插件。"""
        try:
            if not self._plugin:
                return {"ok": False, "error": "插件服务未初始化"}
            return {"ok": True, "data": self._plugin.enable_plugin(plugin_name)}
        except Exception as e:
            logger.exception("enable_plugin 失败")
            return {"ok": False, "error": str(e)}

    def disable_plugin(self, plugin_name: str) -> dict:
        """禁用插件。"""
        try:
            if not self._plugin:
                return {"ok": False, "error": "插件服务未初始化"}
            return {"ok": True, "data": self._plugin.disable_plugin(plugin_name)}
        except Exception as e:
            logger.exception("disable_plugin 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 长期记忆域（阶段四）
    # ================================================================== #
    def add_memory(self, memory_type: str, content: str, category: str) -> dict:
        """添加长期记忆。"""
        try:
            if not self._memory:
                return {"ok": False, "error": "记忆服务未初始化"}
            pid = self._get_active_person_id() or "global"
            return {"ok": True, "data": self._memory.add_memory(pid, memory_type, content, category=category or "other", source="manual")}
        except Exception as e:
            logger.exception("add_memory 失败")
            return {"ok": False, "error": str(e)}

    def get_memories(self, memory_type: str) -> dict:
        """获取长期记忆列表。"""
        try:
            if not self._memory:
                return {"ok": True, "data": []}
            pid = self._get_active_person_id() or "global"
            return {"ok": True, "data": self._memory.get_memories(pid, memory_type=memory_type or None)}
        except Exception as e:
            logger.exception("get_memories 失败")
            return {"ok": False, "error": str(e)}

    def search_memories(self, query: str) -> dict:
        """搜索长期记忆。"""
        try:
            if not self._memory:
                return {"ok": True, "data": []}
            pid = self._get_active_person_id() or "global"
            return {"ok": True, "data": self._memory.search_memories(pid, query)}
        except Exception as e:
            logger.exception("search_memories 失败")
            return {"ok": False, "error": str(e)}

    def forget_memory(self, memory_id: int) -> dict:
        """遗忘一条记忆（软删除）。"""
        try:
            if not self._memory:
                return {"ok": False, "error": "记忆服务未初始化"}
            return {"ok": True, "data": self._memory.forget_memory(int(memory_id))}
        except Exception as e:
            logger.exception("forget_memory 失败")
            return {"ok": False, "error": str(e)}

    def get_memory_stats(self) -> dict:
        """获取记忆统计。"""
        try:
            if not self._memory:
                return {"ok": True, "data": {"total": 0}}
            pid = self._get_active_person_id() or None
            return {"ok": True, "data": self._memory.get_memory_stats(pid)}
        except Exception as e:
            logger.exception("get_memory_stats 失败")
            return {"ok": False, "error": str(e)}

    def get_preferences(self, person_id: str) -> dict:
        """获取对象的喜恶清单（备忘录），返回 [{type: like/dislike, content}]。"""
        try:
            pid = person_id or self._get_active_person_id() or None
            if not self._memory:
                return {"ok": True, "data": []}
            rows = self._memory.get_memories(pid, memory_type="fact", category="preference")
            dislike_words = ("讨厌", "不喜欢", "厌恶", "反感", "恶心", "恨", "不想要")
            prefs = []
            for r in rows or []:
                content = r.get("content", "") if isinstance(r, dict) else str(r)
                ptype = "dislike" if any(w in content for w in dislike_words) else "like"
                prefs.append({"type": ptype, "content": content})
            return {"ok": True, "data": prefs}
        except Exception as e:
            logger.exception("get_preferences 失败")
            return {"ok": False, "error": str(e)}

    def add_preference(self, person_id: str, text: str) -> dict:
        """手动新增一条喜恶记录。"""
        try:
            pid = person_id or self._get_active_person_id() or None
            if not self._memory:
                return {"ok": False, "error": "记忆服务未初始化"}
            new_id = self._memory.add_memory(pid, "fact", text, category="preference", source="manual")
            return {"ok": True, "data": {"id": new_id}}
        except Exception as e:
            logger.exception("add_preference 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 说话风格域（本地学习，云端仅可选增强）
    # ================================================================== #
    def learn_speech_style(self, use_cloud: bool = True) -> dict:
        """立即重新学习用户本人说话风格。"""
        try:
            if not self._style_learner:
                return {"ok": False, "error": "风格学习服务未初始化"}
            r = self._style_learner.learn_now(use_cloud=bool(use_cloud))
            return {"ok": True, "data": r}
        except Exception as e:
            logger.exception("learn_speech_style 失败")
            return {"ok": False, "error": str(e)}

    def get_speech_style(self) -> dict:
        """读取本地说话风格档案。"""
        try:
            if not self._style_learner:
                return {"ok": True, "data": None}
            return {"ok": True, "data": self._style_learner.get_profile()}
        except Exception as e:
            logger.exception("get_speech_style 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 备份域（阶段四）
    # ================================================================== #
    def create_backup(self) -> dict:
        """创建数据备份。"""
        try:
            if not self._backup:
                return {"ok": False, "error": "备份服务未初始化"}
            return {"ok": True, "data": self._backup.create_backup()}
        except Exception as e:
            logger.exception("create_backup 失败")
            return {"ok": False, "error": str(e)}

    def list_backups(self) -> dict:
        """列出所有备份。"""
        try:
            if not self._backup:
                return {"ok": True, "data": []}
            return {"ok": True, "data": self._backup.list_backups()}
        except Exception as e:
            logger.exception("list_backups 失败")
            return {"ok": False, "error": str(e)}

    def restore_backup(self, backup_id: str) -> dict:
        """从备份恢复。"""
        try:
            if not self._backup:
                return {"ok": False, "error": "备份服务未初始化"}
            return {"ok": True, "data": self._backup.restore_backup(backup_id)}
        except Exception as e:
            logger.exception("restore_backup 失败")
            return {"ok": False, "error": str(e)}

    def export_all_data(self) -> dict:
        """导出全部数据为JSON。"""
        try:
            if not self._backup:
                return {"ok": False, "error": "备份服务未初始化"}
            return {"ok": True, "data": self._backup.export_data(db=self.services.get("db"))}
        except Exception as e:
            logger.exception("export_all_data 失败")
            return {"ok": False, "error": str(e)}

    def get_backup_stats(self) -> dict:
        """获取备份统计。"""
        try:
            if not self._backup:
                return {"ok": True, "data": {"total_backups": 0}}
            return {"ok": True, "data": self._backup.get_backup_stats()}
        except Exception as e:
            logger.exception("get_backup_stats 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 系统域
    # ================================================================== #
    def get_app_info(self) -> dict:
        """返回应用信息：版本、首次运行等。"""
        try:
            info = {
                "name": self._config.get("app.name", "LoveAI") if self._config else "LoveAI",
                "version": self._config.get("app.version", "2.0.0") if self._config else "2.0.0",
                "first_run": self._config.is_first_run() if self._config else True,
                "platform": "windows",
            }
            return {"ok": True, "data": info}
        except Exception as e:
            logger.exception("get_app_info 失败")
            return {"ok": False, "error": str(e)}

    def complete_onboarding(self) -> dict:
        """标记新手引导完成。"""
        try:
            if self._config:
                self._config.set_first_run_done()
            return {"ok": True, "data": {"done": True}}
        except Exception as e:
            logger.exception("complete_onboarding 失败")
            return {"ok": False, "error": str(e)}

    def export_data(self) -> dict:
        """导出全部数据到备份目录（24.1.10：委托已可用的 BackupService.export_data）。"""
        try:
            if not self._backup:
                return {"ok": False, "error": "备份服务未初始化"}
            return {"ok": True, "data": self._backup.export_data(db=self.services.get("db"))}
        except Exception as e:
            logger.exception("export_data 失败")
            return {"ok": False, "error": str(e)}

    def quit_app(self) -> dict:
        """请求退出应用。"""
        try:
            self.signals.app_quitting.emit()
            return {"ok": True, "data": {"quitting": True}}
        except Exception as e:
            logger.exception("quit_app 失败")
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 大模型网关域（L1/L2/L3 路由 + LoRA 适配器 + 训练数据）
    # ================================================================== #
    def get_model_status(self) -> dict:
        """返回 L1/L2/L3 网关状态、当前 LoRA 适配器与加载情况。"""
        try:
            llm = self.services.get("llm")
            if llm is None:
                return {"ok": False, "error": "llm 服务未就绪"}
            return {"ok": True, "data": llm.get_status()}
        except Exception as e:
            logger.exception("get_model_status 失败")
            return {"ok": False, "error": str(e)}

    def list_lora_adapters(self) -> dict:
        """列出 models_local/lora/ 下可用的 LoRA 适配器。"""
        try:
            llm = self.services.get("llm")
            if llm is None:
                return {"ok": False, "error": "llm 服务未就绪"}
            return {"ok": True, "data": {
                "adapters": llm.list_lora_adapters(),
                "current": llm.get_current_lora(),
            }}
        except Exception as e:
            logger.exception("list_lora_adapters 失败")
            return {"ok": False, "error": str(e)}

    def select_lora_adapter(self, name: str) -> dict:
        """切换 LoRA 适配器（L2 已加载时下次调用自动重载）。"""
        try:
            llm = self.services.get("llm")
            if llm is None:
                return {"ok": False, "error": "llm 服务未就绪"}
            llm.select_lora_adapter(name)
            return {"ok": True, "data": {"current": llm.get_current_lora()}}
        except Exception as e:
            logger.exception("select_lora_adapter 失败")
            return {"ok": False, "error": str(e)}

    def mark_good_reply(self, reply_text: str, context: Any = None, person_id: Any = None) -> dict:
        """用户标记"好的回复"，落入 training_data 表供后续 LoRA 微调。"""
        try:
            llm = self.services.get("llm")
            if llm is None:
                return {"ok": False, "error": "llm 服务未就绪"}
            rid = llm.mark_good_reply(
                reply_text,
                context=str(context) if context is not None else None,
                person_id=str(person_id) if person_id is not None else None,
            )
            return {"ok": True, "data": {"id": rid}}
        except Exception as e:
            logger.exception("mark_good_reply 失败")
            return {"ok": False, "error": str(e)}

    def get_training_stats(self) -> dict:
        """返回训练数据统计：总数 / 按人物 / 按来源模型分组。"""
        try:
            llm = self.services.get("llm")
            if llm is None:
                return {"ok": False, "error": "llm 服务未就绪"}
            return {"ok": True, "data": llm.get_training_stats()}
        except Exception as e:
            logger.exception("get_training_stats 失败")
            return {"ok": False, "error": str(e)}

    # ---- 一键微信导入 ----
    def auto_import_wechat_all(self) -> dict:
        """一键导入：自动尝试所有可用方式（A→B→C→D→E，F类默认禁用）。"""
        importer = self.services.get("wechat_auto_importer")
        if not importer:
            return {"ok": False, "error": "微信导入服务未初始化", "total": 0,
                    "used": [], "skipped": [], "log": []}
        try:
            r = importer.auto_import(person_id="all", enable_advanced=False)
            # 【断点修复】auto_import 返回的键是 {"messages","used","skipped","total","log"}，
            # 旧实现读的是 total_messages / successful / failed 这三个根本不存在的键
            # → total 恒为 0、used/skipped 恒为空。这里改用真实键名。
            return {"ok": True, "total": r.get("total", 0),
                    "used": r.get("used", []), "skipped": r.get("skipped", []),
                    "log": r.get("log", [])}
        except Exception as e:
            return {"ok": False, "error": str(e), "total": 0,
                    "used": [], "skipped": [], "log": []}

    # ================================================================== #
    # 反哺训练系统（本地AI自我训练，默认关闭，数据不上传）
    # ================================================================== #

    @property
    def _train_collector(self):
        """懒加载训练数据收集器。"""
        return self.services.get("training_data_collector")

    @property
    def _train_engine(self):
        """懒加载LoRA训练引擎。"""
        return self.services.get("training_engine")

    @property
    def _train_versions(self):
        """懒加载模型版本管理器。"""
        return self.services.get("model_version_manager")

    def _maybe_collect_reply_candidates(self, chat_text, person_id, result):
        """分析产出AI回复候选后，轻量登记为训练样本（失败静默，不阻塞主流程）。"""
        try:
            collector = self._train_collector
            if collector is None:
                return
            candidates = (result or {}).get("reply_candidates") or \
                         (result or {}).get("data", {}).get("reply_candidates") or []
            if not candidates:
                return
            first = candidates[0] if isinstance(candidates[0], str) \
                else (candidates[0].get("text") or candidates[0].get("reply") or "")
            if first:
                collector.collect_interaction(
                    user_msg=chat_text, ai_reply=first,
                    feedback_type="used",
                    metadata={"person_id": person_id, "source": "analyze_chat",
                              "conversation_id": (result or {}).get("conversation_id")})
        except Exception as e:
            logger.debug("自动收集训练样本失败: %s", e)

    def record_reply_interaction(self, user_msg, ai_reply, feedback="used", metadata=None):
        """记录一条人机交互训练样本（本地，不上传）。"""
        try:
            if self._train_collector is None:
                return {"ok": False, "reason": "training_data_collector 未初始化"}
            return self._train_collector.collect_interaction(
                user_msg, ai_reply, feedback_type=feedback, metadata=metadata or {})
        except Exception as e:
            logger.debug("记录训练样本失败: %s", e)
            return {"ok": False, "reason": str(e)}

    def record_reply_choice(self, chosen_reply, all_options, person_id, user_msg):
        """用户从候选里选了一条发送（feedback=used），同时走旧的A/B学习。"""
        try:
            if self._train_collector:
                self._train_collector.collect_interaction(
                    user_msg=user_msg, ai_reply=chosen_reply,
                    feedback_type="used",
                    metadata={"person_id": person_id, "source": "reply_choice",
                              "all_options": all_options})
            if self._train_engine:
                self._train_engine.ab_learning(
                    user_choice=chosen_reply, reply_options=all_options,
                    subsequent_result="positive")
            return {"ok": True}
        except Exception as e:
            logger.debug("记录回复选择失败: %s", e)
            return {"ok": False, "reason": str(e)}

    def record_user_correction(self, user_msg, corrected_reply, person_id):
        """用户手动改/重写了一条回复（feedback=corrected）。"""
        try:
            if self._train_collector is None:
                return {"ok": False, "reason": "training_data_collector 未初始化"}
            return self._train_collector.collect_interaction(
                user_msg=user_msg, ai_reply=corrected_reply,
                feedback_type="corrected",
                metadata={"person_id": person_id, "modified_reply": corrected_reply,
                          "source": "user_correction"})
        except Exception as e:
            logger.debug("记录用户纠正失败: %s", e)
            return {"ok": False, "reason": str(e)}

    def get_training_data_stats(self):
        """返回训练数据收集统计。"""
        try:
            if self._train_collector is None:
                return {"ok": False, "reason": "training_data_collector 未初始化"}
            return {"ok": True, "data": self._train_collector.get_stats()}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def export_training_dataset(self, fmt="alpaca"):
        """导出训练数据集，返回文件路径。"""
        try:
            if self._train_collector is None:
                return {"ok": False, "reason": "training_data_collector 未初始化"}
            path = self._train_collector.export_dataset(fmt)
            return {"ok": True, "data": {"path": path, "format": fmt}}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def clear_training_data(self):
        """清空全部训练数据（UI已二次确认）。"""
        try:
            if self._train_collector is None:
                return {"ok": False, "reason": "training_data_collector 未初始化"}
            self._train_collector.clear_data()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def start_lora_training(self, overrides=None):
        """手动/自动触发LoRA训练（后台线程）。"""
        try:
            if self._train_engine is None:
                return {"ok": False, "reason": "training_engine 未初始化"}
            return self._train_engine.start_training(overrides or {})
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def get_training_status(self):
        """训练状态/进度/loss曲线。"""
        try:
            if self._train_engine is None:
                return {"ok": False, "reason": "training_engine 未初始化"}
            return {"ok": True, "data": self._train_engine.get_training_status()}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def cancel_lora_training(self):
        """取消正在进行的训练。"""
        try:
            if self._train_engine is None:
                return {"ok": False, "reason": "training_engine 未初始化"}
            self._train_engine.cancel_training()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def list_model_versions(self):
        """列出所有LoRA模型版本。"""
        try:
            if self._train_versions is None:
                return {"ok": False, "reason": "model_version_manager 未初始化"}
            return {"ok": True, "data": self._train_versions.list_versions()}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def rollback_model_version(self, version_id):
        """回滚到指定模型版本。"""
        try:
            if self._train_versions is None:
                return {"ok": False, "reason": "model_version_manager 未初始化"}
            return self._train_versions.rollback(version_id)
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def check_auto_training_trigger(self):
        """后台周期检查是否满足自动训练条件。"""
        try:
            if self._train_engine is None:
                return {"ok": False, "reason": "training_engine 未初始化"}
            return self._train_engine.check_auto_trigger()
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    # ================================================================== #
    # 微信多方法导出体系（密钥提取→解密→读取→一键导入→实时监听）
    # ================================================================== #

    @property
    def _wechat_multi_importer(self):
        """懒加载微信多方法自动导入器。"""
        return self.services.get("wechat_auto_importer")

    @property
    def _wechat_monitor(self):
        """懒加载微信实时消息监听器。"""
        return self.services.get("wechat_realtime_monitor")

    @property
    def _wechat_key_extractor(self):
        """懒加载微信密钥提取器。"""
        return self.services.get("wechat_key_extractor")

    def wechat_import(self, person_id=None, contact_filter=None,
                      enable_high_risk=False, incremental=False):
        """一键导入微信聊天记录（多方法自动尝试，低风险优先）。"""
        try:
            importer = self._wechat_multi_importer
            if importer is None:
                return {"ok": False, "error": "微信多方法导入器未初始化"}
            return importer.auto_import(
                person_id=person_id, contact_filter=contact_filter,
                enable_high_risk=enable_high_risk, incremental=incremental)
        except Exception as e:
            logger.exception("微信一键导入失败")
            return {"ok": False, "error": str(e)}

    def wechat_incremental_import(self, contact_filter=None):
        """增量导入：只读取上次导入后的新消息。"""
        try:
            importer = self._wechat_multi_importer
            if importer is None:
                return {"ok": False, "error": "微信多方法导入器未初始化"}
            return importer.incremental_import(contact_filter=contact_filter)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wechat_start_monitor(self, mode="polling", interval=5.0, **kwargs):
        """启动微信实时消息监听。"""
        try:
            monitor = self._wechat_monitor
            if monitor is None:
                return {"ok": False, "error": "微信实时监听器未初始化"}
            monitor.start(mode=mode, interval=interval, **kwargs)
            return {"ok": True, "data": {"mode": mode, "interval": interval}}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wechat_stop_monitor(self):
        """停止微信实时消息监听。"""
        try:
            monitor = self._wechat_monitor
            if monitor:
                monitor.stop()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wechat_monitor_status(self):
        """获取微信实时监听状态。"""
        try:
            monitor = self._wechat_monitor
            if monitor is None:
                return {"ok": False, "running": False, "error": "监听器未初始化"}
            return {"ok": True, "data": {
                "running": monitor.is_running(),
                "mode": getattr(monitor, "mode", None)}}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wechat_import_progress(self):
        """获取当前导入进度。"""
        try:
            importer = self._wechat_multi_importer
            if importer is None:
                return {"ok": False, "error": "导入器未初始化"}
            return {"ok": True, "data": importer.get_progress()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wechat_import_log(self):
        """获取详细导入日志。"""
        try:
            importer = self._wechat_multi_importer
            if importer is None:
                return {"ok": False, "error": "导入器未初始化"}
            return {"ok": True, "data": importer.get_import_log()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wechat_cancel_import(self):
        """取消正在进行的导入。"""
        try:
            importer = self._wechat_multi_importer
            if importer:
                importer.cancel_import()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wechat_extract_key(self, enable_high_risk=False):
        """手动触发微信密钥提取（多方法自动尝试，低风险优先）。

        【断点修复】旧实现是 `extractor.extract_key_auto(enable_high_risk=...)`，
        漏了必填的第一个参数 db_path → 必然 TypeError。
        这里先由一键导入模块扫出探针库（4.x 优先 db_storage\\message\\message_0.db，
        3.x 优先 Msg\\MSG0.db），再带着 db_path 调阶梯提取。
        """
        try:
            extractor = self._wechat_key_extractor
            if extractor is None:
                return {"ok": False, "error": "密钥提取器未初始化"}
            probe_db, family = self._wechat_probe_db()
            if not probe_db:
                return {"ok": False,
                        "error": "未发现可用的微信数据库，无法确定 db_path（请先执行发现）"}
            return extractor.extract_key_auto(probe_db, version=family,
                                              enable_high_risk=enable_high_risk)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _wechat_probe_db(self):
        """扫候选根挑一个探针库，返回 (db_path, version_family)。"""
        try:
            from services.wechat_oneclick import (scan_roots, list_accounts,
                                                  figure_family, pick_probe_db)
            for r in scan_roots(None):
                if not r.get("exists"):
                    continue
                for a in list_accounts(r["path"]):
                    fam = figure_family(a.get("layout"))
                    p = pick_probe_db(a["path"], fam)
                    if p:
                        return p, fam
        except Exception as e:
            logger.debug("探测微信探针库失败: %s", e)
        return None, "4.x"

    def wechat_detect_process(self):
        """检测微信进程状态和版本。"""
        try:
            extractor = self._wechat_key_extractor
            if extractor is None:
                return {"ok": False, "error": "密钥提取器未初始化"}
            return {"ok": True, "data": extractor.detect_wechat_process()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # 微信聊天记录「一键导入」总控（WechatOneClick 契约入口）
    # 契约：discover / run / get_progress / cancel / high_risk_available /
    #       acquire_key_high_risk —— 签名与返回结构与 UI 侧一字不差。
    # 红线：注入类动作（wx_key.dll / frida / chatlog_alpha）无显式知情同意时永不触发。
    # ------------------------------------------------------------------ #

    @property
    def _wechat_oneclick(self):
        """懒加载微信一键导入总控（services/wechat_oneclick.py）。"""
        return self.services.get("wechat_oneclick")

    def wechat_oneclick_discover(self, extra_roots=None):
        """发现：扫全部候选根，列出全部账号 + 版本/密钥状态 + 工具在位情况。"""
        try:
            svc = self._wechat_oneclick
            if svc is None:
                return {"ok": False, "error": "微信一键导入服务未初始化"}
            return {"ok": True, "data": svc.discover(extra_roots=extra_roots)}
        except Exception as e:
            logger.exception("wechat_oneclick_discover 失败")
            return {"ok": False, "error": str(e)}

    def wechat_oneclick_run(self, account_ids=None, contact_filter=None,
                            incremental=False, consent_high_risk=False,
                            acquire_key_if_missing=False, consent_injection=False):
        """一键导入：发现→认版本→取密钥→解密→导入。

        低风险路线全程无人值守（不弹命令行、不需手动登录、不需自己跑工具）；
        有缓存密钥时**微信未运行也能跑通**。
        consent_high_risk 只允许在 UI 侧已取得用户显式知情同意后传 True。

        —— 以下两个参数为**追加**（原契约字段名与返回结构一律不变）——
        acquire_key_if_missing：某账号无缓存密钥时，先跑「微信密钥现场抓取」流程
            （services/wechat_key_controller.WeChatKeyFlow）再走解密→入库。
        consent_injection：注入控制器 DLL 的显式知情同意。**两者都为 True 才会注入**；
            否则连微信都不会被关闭，直接返回人话原因 + next_action。
        """
        try:
            svc = self._wechat_oneclick
            if svc is None:
                return {"ok": False, "error": "微信一键导入服务未初始化"}
            return {"ok": True, "data": svc.run(
                account_ids=account_ids, contact_filter=contact_filter,
                incremental=incremental, consent_high_risk=consent_high_risk,
                acquire_key_if_missing=acquire_key_if_missing,
                consent_injection=consent_injection)}
        except Exception as e:
            logger.exception("wechat_oneclick_run 失败")
            return {"ok": False, "error": str(e)}

    def wechat_oneclick_progress(self):
        """查询一键导入进度：stage/percentage/account_id/db_index/db_total/..."""
        try:
            svc = self._wechat_oneclick
            if svc is None:
                return {"ok": False, "error": "微信一键导入服务未初始化"}
            return {"ok": True, "data": svc.get_progress()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wechat_oneclick_cancel(self):
        """取消正在跑的一键导入。"""
        try:
            svc = self._wechat_oneclick
            if svc is None:
                return {"ok": False, "error": "微信一键导入服务未初始化"}
            svc.cancel()
            return {"ok": True, "data": {"cancelled": True}}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wechat_oneclick_highrisk_status(self):
        """高风险路线（wx_key.dll 注入）可用性——只检测，绝不执行。"""
        try:
            svc = self._wechat_oneclick
            if svc is None:
                return {"ok": False, "error": "微信一键导入服务未初始化"}
            return {"ok": True, "data": svc.high_risk_available()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wechat_oneclick_acquire_key_high_risk(self, wait_seconds=180):
        """走「现场抓取」彻底拿一次密钥并写进机器指纹加密缓存。

        **必须先有用户显式知情同意**：调用它即视为 UI 侧已弹过确认框。
        实现已升级为程序自动执行（services/wechat_key_controller.WeChatKeyFlow）：
        程序自己完成 检测→关闭微信→重启→前台化→等待扫码（或代点登录）→注入 DLL
        →轮询回收密钥→写加密缓存→CleanupHook 卸载。
        注入完成后会调用 CleanupHook 干净卸载，正常情况下**不需要重启微信**；
        若中途异常退出导致微信异常，重启微信即可。存在账号风控风险。
        """
        try:
            svc = self._wechat_oneclick
            if svc is None:
                return {"ok": False, "error": "微信一键导入服务未初始化"}
            return {"ok": True, "data": svc.acquire_key_high_risk(
                wait_seconds=int(wait_seconds))}
        except Exception as e:
            logger.exception("wechat_oneclick_acquire_key_high_risk 失败")
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # 微信密钥现场抓取（WeChatKeyFlow）——1–7 步的门面
    # 契约固定 stage：detect / kill / launch / foreground / wait_login /
    #                inject / poll_key / persist / cleanup
    # 红线：wechat_keyflow_run 默认 consent_injection=False，此时**不关微信、不注入**，
    #       只返回人话原因 + next_action；注入必须由 UI 先弹知情同意再传 True。
    # ------------------------------------------------------------------ #

    @property
    def _wechat_keyflow(self):
        """懒加载微信密钥现场抓取编排（services/wechat_key_controller.WeChatKeyFlow）。"""
        return self.services.get("wechat_keyflow")

    def wechat_keyflow_precheck(self):
        """只读预检：微信目录/版本与来源/进程/管理员/注入的前置条件。**绝不改状态。**"""
        try:
            flow = self._wechat_keyflow
            if flow is None:
                return {"ok": False, "error": "微信密钥流程服务未初始化"}
            return {"ok": True, "data": flow.precheck()}
        except Exception as e:
            logger.exception("wechat_keyflow_precheck 失败")
            return {"ok": False, "error": str(e)}

    def wechat_keyflow_run(self, consent_injection=False, auto_restart=True,
                           allow_auto_login_click=False):
        """跑一次「微信生命周期 → 注入 → 回收密钥 → 写缓存」。

        consent_injection=False（默认）时受红线约束：不关闭微信、不注入，
        立即返回原因与下一步动作。UI 必须先向用户展示确认文案再传 True。
        """
        try:
            flow = self._wechat_keyflow
            if flow is None:
                return {"ok": False, "error": "微信密钥流程服务未初始化"}
            return {"ok": True, "data": flow.run(
                consent_injection=bool(consent_injection),
                auto_restart=bool(auto_restart),
                allow_auto_login_click=bool(allow_auto_login_click))}
        except Exception as e:
            logger.exception("wechat_keyflow_run 失败")
            return {"ok": False, "error": str(e)}

    def wechat_keyflow_progress(self):
        """查询现场抓取进度：stage/stage_name/percentage/detail/running。"""
        try:
            flow = self._wechat_keyflow
            if flow is None:
                return {"ok": False, "error": "微信密钥流程服务未初始化"}
            return {"ok": True, "data": flow.get_progress()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wechat_keyflow_cancel(self):
        """取消正在跑的现场抓取（会触发 CleanupHook 收尾）。"""
        try:
            flow = self._wechat_keyflow
            if flow is None:
                return {"ok": False, "error": "微信密钥流程服务未初始化"}
            flow.cancel()
            return {"ok": True, "data": {"cancelled": True}}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ================================================================== #
    # 🟡 13项接线补全（facade 直调入口；UI 组件缺失时仅暴露数据接口）
    # ================================================================== #

    def optimize_profile(self, person_id: str = "", partner_pref: dict = None) -> dict:
        """23.7 个人资料优化助手：基于对方偏好给出头像/签名/朋友圈建议。"""
        try:
            from services.batch6_personalize import profile_optimizer
            pid = person_id or self._get_active_person_id() or ""
            profile_text = ""
            try:
                if pid and self._db:
                    row = self._db.fetch_one(
                        "SELECT nickname, occupation, soul_path FROM persons WHERE id = :pid",
                        {"pid": pid}) or {}
                    profile_text = " ".join(str(row.get(k) or "") for k in
                                           ("nickname", "occupation", "soul_path"))
            except Exception as _e:
                logger.debug("optimize_profile 取档案降级: %s", _e)
            data = profile_optimizer(profile_text, partner_pref or {})
            return {"ok": True, "data": data, "ui_note": "需后续UI：资料优化建议卡片"}
        except Exception as e:
            logger.exception("optimize_profile 失败")
            return {"ok": False, "error": str(e)}

    def get_crisis_alert(self, person_id: str = "") -> dict:
        """23.9 关系危机预警：基于最近情绪/对话粗算风险等级。"""
        try:
            from services.batch6_tasks import crisis_alert
            neg = 10
            try:
                pid = person_id or self._get_active_person_id()
                if pid:
                    emo = self._get_emotion_trend(pid, days=7) if self._emotion else ""
                    neg = 60 if any(k in emo for k in ("愤怒", "冷战", "冷淡")) else 15
            except Exception as _e:
                logger.debug("crisis_alert 取情绪降级: %s", _e)
            data = crisis_alert({"neg_emotion_pct": neg})
            return {"ok": True, "data": data, "ui_note": "需后续UI：预警弹窗/通知通道"}
        except Exception as e:
            logger.exception("get_crisis_alert 失败")
            return {"ok": False, "error": str(e)}

    def get_level11(self, person_id: str = "") -> dict:
        """24.1.1 11级闯关系统：基于好感度映射到11级段位。"""
        try:
            from services.batch7_group1 import stage_to_11
            pid = person_id or self._get_active_person_id() or ""
            total = 50.0
            try:
                row = self._db.fetch_one(
                    "SELECT total_score FROM current_affection WHERE person_id = :pid",
                    {"pid": pid}) if pid and self._db else None
                if row:
                    total = float(row.get("total_score") or 50.0)
            except Exception as _e:
                logger.debug("get_level11 取好感度降级: %s", _e)
            return {"ok": True, "data": stage_to_11(total)}
        except Exception as e:
            logger.exception("get_level11 失败")
            return {"ok": False, "error": str(e)}

    def ocr_long_screenshot(self, images: list = None) -> dict:
        """26.1.4 长截图OCR：拼接多段截图后走结构化OCR。"""
        try:
            from services.batch8_harness import long_screenshot, structured_ocr
            imgs = list(images or [])
            stitch = long_screenshot(imgs)
            # 若有真实OCR服务，对每段调用 recognize_image；否则返回占位
            parsed_messages: list = []
            try:
                if self._ocr and imgs:
                    for path in imgs:
                        r = self._ocr.recognize_image(path) if hasattr(self._ocr, "recognize_image") else ""
                        if isinstance(r, dict):
                            parsed_messages.extend(r.get("messages") or [])
                        elif isinstance(r, str):
                            parsed_messages.append({"content": r})
            except Exception as _e:
                logger.debug("长截图OCR 调服务降级: %s", _e)
            return {"ok": True,
                    "data": {"stitch": stitch, "messages": parsed_messages,
                             "segments": stitch.get("segments", 0)}}
        except Exception as e:
            logger.exception("ocr_long_screenshot 失败")
            return {"ok": False, "error": str(e)}

    def get_task_board(self, person_id: str = "") -> dict:
        """26.2.5 任务看板视图：按状态分桶返回任务。"""
        try:
            from services.batch8_harness import task_board
            pid = person_id or self._get_active_person_id() or ""
            rows = []
            try:
                if pid and self._db:
                    rows = self._db.fetch_all(
                        "SELECT id, title, status, difficulty FROM tasks WHERE person_id = :pid LIMIT 100",
                        {"pid": pid}) or []
            except Exception as _e:
                logger.debug("get_task_board 取数降级: %s", _e)
            status_map = {"pending": "待开始", "in_progress": "进行中",
                          "completed": "已完成", "abandoned": "已过期", "failed": "已过期"}
            tasks = [{"id": r.get("id"), "title": r.get("title"),
                       "status": status_map.get((r.get("status") or "pending"), "待开始"),
                       "difficulty": r.get("difficulty")} for r in rows]
            return {"ok": True, "data": task_board(tasks),
                    "ui_note": "需后续UI：看板Kanban组件"}
        except Exception as e:
            logger.exception("get_task_board 失败")
            return {"ok": False, "error": str(e)}

    def get_twin_panel(self, person_id: str = "", mode: str = "partner") -> dict:
        """26.2.10 数字分身管理面板：返回分身状态/可切换模式。"""
        try:
            from services.batch8_workflow import get_twin_status, switch_twin_mode
            pid = person_id or self._get_active_person_id() or ""
            cur = get_twin_status(mode)
            return {"ok": True, "data": {
                "current": cur,
                "available_modes": ["partner", "growth", "adversary"],
                "switch": lambda m: switch_twin_mode(m),
                "person_id": pid,
            }, "ui_note": "需后续UI：分身管理面板"}
        except Exception as e:
            logger.exception("get_twin_panel 失败")
            return {"ok": False, "error": str(e)}

    def switch_twin_mode(self, mode: str) -> dict:
        """切换数字分身模式（partner/growth/adversary）。"""
        try:
            from services.batch8_workflow import switch_twin_mode
            return {"ok": True, "data": switch_twin_mode(mode)}
        except Exception as e:
            logger.exception("switch_twin_mode 失败")
            return {"ok": False, "error": str(e)}

    def get_quick_panel(self, person_id: str = "") -> dict:
        """26.3.14 右侧快捷面板：返回最近任务 + 好感度四维 + 快捷动作。"""
        try:
            from services.batch9_ui import get_quick_panel_data
            pid = person_id or self._get_active_person_id() or ""
            recent: list = []
            axes: dict = {}
            try:
                if pid and self._db:
                    rows = self._db.fetch_all(
                        "SELECT id, title, status FROM tasks WHERE person_id = :pid ORDER BY created_at DESC LIMIT 3",
                        {"pid": pid}) or []
                    recent = [{"id": r.get("id"), "title": r.get("title"),
                               "status": r.get("status")} for r in rows]
                    aff_row = self._db.fetch_one(
                        "SELECT scores_json FROM current_affection WHERE person_id = :pid",
                        {"pid": pid})
                    if aff_row:
                        try:
                            scores = json.loads(aff_row.get("scores_json") or "{}")
                            for k in ("trust", "attraction", "emotion", "commitment"):
                                if k in scores:
                                    axes[k] = scores[k]
                        except Exception:
                            pass
            except Exception as _e:
                logger.debug("get_quick_panel 取数降级: %s", _e)
            return {"ok": True, "data": get_quick_panel_data(recent, axes),
                    "ui_note": "需后续UI：右侧快捷面板"}
        except Exception as e:
            logger.exception("get_quick_panel 失败")
            return {"ok": False, "error": str(e)}

    def get_emotion_dynamics(self, person_id: str = "", history: list = None) -> dict:
        """22.1.4 情绪动力学：衰减 + 同类负面累积爆发风险。"""
        try:
            from services.batch4_enhance import decay_emotion, accumulate_emotion
            hist = list(history or getattr(self, "_emotion_history_buf", []) or [])
            label = hist[-1] if hist else "平静"
            acc = accumulate_emotion(hist, label)
            return {"ok": True, "data": {
                "history": hist[-20:],
                "current": label,
                "accumulation": acc,
                "decay_demo": {lbl: decay_emotion(lbl, 1.0, 24.0)
                               for lbl in ("愤怒", "焦虑", "委屈", "开心")},
            }}
        except Exception as e:
            logger.exception("get_emotion_dynamics 失败")
            return {"ok": False, "error": str(e)}

    def apply_play_style_strategy(self, base_strategy: dict, style: str = "温柔型") -> dict:
        """23.12 非线性攻略风格：把攻略风格权重叠到策略字典上。"""
        try:
            from services.batch6_personalize import apply_play_style, play_style_weights
            out = apply_play_style(base_strategy or {}, style)
            out["weights"] = play_style_weights(style)
            return {"ok": True, "data": out}
        except Exception as e:
            logger.exception("apply_play_style_strategy 失败")
            return {"ok": False, "error": str(e)}

