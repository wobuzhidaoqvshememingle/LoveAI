"""
LoveAI v3.0 - 应用主控（原生QWidget桌面端）
全局初始化、服务注册表、窗口/托盘/快捷键的组装入口。
阶段一只注册 config / db 两个核心服务，重服务（大模型/微信监听/RAG）走懒加载。
v3.0变更：ApiBridge(QObject+@Slot) → ServiceFacade(普通Python类)，UI层全部原生QWidget。
"""
import os
import sys
import json
import logging
import threading
from logging.handlers import TimedRotatingFileHandler
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from app.signals import AppSignals, get_signals
from services.config_service import ConfigService
from models.database import Database, get_database


logger = logging.getLogger("LoveAI")


class LoveAIApplication(QObject):
    """LoveAI 应用主控。

    职责：
    1. 初始化日志（按天轮转，输出到 logs/loveai.log）
    2. 构建服务注册表 self.services，供 ServiceFacade 调用
    3. 组装 ServiceFacade / WindowManager / TrayIcon / ShortcutManager
    4. run() 进入事件循环；quit() 清理资源
    """

    def __init__(self, config_path: str = "config.json"):
        super().__init__()
        self._config_path = config_path
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            # 打包态：服务/资源根目录为 _MEIPASS（onedir 下即 exe 目录）
            self._project_root = sys._MEIPASS
        else:
            self._project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 0. 确保运行期可写目录存在（打包后首次启动尤其需要）
        self._ensure_directories()

        # 1. 日志系统
        self._setup_logging()

        # 1.5 随包便携 Ollama：端口未占用且随包 exe 就位时后台拉起 ollama.exe serve
        #     （仅应用运行期内的便携进程，退出时优雅终止；不做开机自启/系统服务）。
        self._portable_ollama_proc = None
        self._ollama_ready_timer = None
        self._local_engine_ready = False
        self._local_models_registered = False
        self._registering_models = False
        self._ensure_portable_ollama()
        # 退出路径兜底：主窗口 closeEvent 只 hide 不走 quit()，故把进程树回收同时挂到
        # QApplication.aboutToQuit（任何正常退出路径都会触发）；quit() 内仍保留调用，幂等。
        try:
            _qt_app = QApplication.instance()
            if _qt_app is not None:
                _qt_app.aboutToQuit.connect(self._stop_portable_ollama)
        except Exception:
            pass

        logger.info("=" * 50)
        logger.info("LoveAI v2.0 启动中...")
        logger.info("项目根目录: %s", self._project_root)

        # 2. 全局信号总线
        self.signals: AppSignals = get_signals()

        # 3. 服务注册表（阶段一只有 config / db，其余懒加载）
        self.services: dict[str, Any] = {}

        # 4. 初始化核心服务
        self._init_core_services()

        # 4.5 初始化阶段二服务（安全导入，模块缺失时跳过）
        self._init_phase2_services()

        # 4.6 初始化阶段三服务（游戏化+数字分身）
        self._init_phase3_services()

        # 4.7 初始化阶段四服务（语音/OCR/统计/插件/记忆/备份）
        self._init_phase4_services()

        # 4.8 本地桌面 AI 扫描（后台非阻塞，结果写入 services["desktop_ai_candidates"]；
        #     不默认启用 desktop_ai 路由，config 开关默认 False，仅让结果可见）
        self._init_desktop_ai_scan()

        # 5. 创建 ServiceFacade（所有窗口共享同一个实例，普通Python类，不继承QObject）
        #    延迟导入避免循环依赖
        from app.service_facade import ServiceFacade
        self.facade: ServiceFacade = ServiceFacade(self.services, self.signals)

        # 5.5 后台定时调度器：定期自动跑 好感度衰减/画像/数字分身/财务/记忆
        #     QTimer 在主线程触发，实际计算在 daemon 线程，不阻塞 UI。
        #     配置项：background.enabled（默认 True）/ background.interval_minutes（默认 5）
        self._scheduler = None
        try:
            from app.background_scheduler import BackgroundScheduler
            self._scheduler = BackgroundScheduler(self.facade, self.signals)
            logger.info("BackgroundScheduler 已创建，enabled=%s interval=%.1fmin",
                        self._scheduler.enabled, self._scheduler.interval_ms / 60000.0)
        except Exception as e:
            logger.warning("BackgroundScheduler 创建失败（后台自动更新不可用）: %s", e)
            self._scheduler = None

        # 6. 创建 UI 层（WindowManager / TrayManager / ShortcutManager）
        self.window_manager = self._try_create_window_manager()
        self.tray = self._try_create_tray()
        self.shortcuts = self._try_create_shortcuts()

        # 7. 信号接线：全局退出信号 -> self.quit
        self.signals.app_quitting.connect(self.quit)

        # 8. 启动桌宠联动 WebSocket 服务端（127.0.0.1:9876，后台线程，不阻塞 UI）
        #    LoveAI 主体性不动：这只是一个推送接口，把全局信号 fan-out 给桌宠客户端。
        try:
            from app import pet_ws_server
            pet_ws_server.start_server(host="127.0.0.1", port=9876)
        except Exception as e:
            logger.warning("桌宠 WS 服务端启动失败（不影响主程序）: %s", e)

        # 8.5 R3.3-76：先写桌宠共享配置（data/pet_shared.json，单一事实源=config.json pet 段），
        #    再拉起 Electron 桌宠进程（幂等；失败降级提示，不影响主程序）。
        #    启动顺序：WS server(8) -> 写共享配置 -> 拉起桌宠，保证桌宠连上即可读到配置。
        try:
            from app import pet_config
            shared_path = pet_config.write_shared_config()
        except Exception as e:
            logger.warning("写桌宠共享配置失败（不影响主程序）: %s", e)
            shared_path = None

        try:
            from app import pet_launcher
            status = pet_launcher.launch_pet(shared_config_path=shared_path)
            if status in ("no_resources", "launch_failed"):
                # 失败降级（验收点6）：桌宠资源缺失/拉起失败 -> 可见提示，不崩溃
                msg = {
                    "no_resources": "桌宠运行资源缺失，桌宠未启动（其余功能正常）。",
                    "launch_failed": "桌宠启动失败，桌宠未启动（其余功能正常）。",
                }.get(status, "桌宠未启动。")
                logger.warning("桌宠拉起状态=%s：%s", status, msg)
                try:
                    self.signals.error_occurred.emit({
                        "source": "pet_launcher",
                        "message": msg,
                        "severity": "warning",
                    })
                except Exception:
                    pass
            elif status == "already_running":
                logger.info("检测到桌宠已在运行，纳入整体生命周期托管")
            elif status == "ok":
                logger.info("桌宠已随主程序启动，纳入整体生命周期托管")
        except Exception as e:
            logger.warning("拉起 Electron 桌宠失败（不影响主程序）: %s", e)

        # 8.8 R3.3-77：把桥接网挂载进生产运行时（复用 facade 同一 EventBus）。
        #    全程 try/except：桥接失败绝不影响主程序启动。
        self.bridge_network = None
        try:
            self._mount_bridge_network()
        except Exception as e:
            logger.warning("R3.3-77 桥接网挂载失败（主程序照常运行）: %s", e)

        logger.info("LoveAIApplication 初始化完成")

    # ------------------------------------------------------------------ #
    # 初始化阶段
    # ------------------------------------------------------------------ #
    def _mount_bridge_network(self) -> None:
        """R3.3-77：把算法桥接网挂载进生产运行时。

        关键：复用 facade 已在用的【同一个 EventBus】（_ensure_event_bus 创建的那把），
        桥订阅其上已有事件（AFFECTION_CHANGED/TASKS_COMPLETED/...），生产 publish 即触发
        多跳联动。桥接层纯 Python，import 不触发 Qt。失败降级，绝不阻断主程序启动。
        """
        if getattr(self, "bridge_network", None) is not None:
            return  # 幂等：重复挂载返回既有网
        facade = getattr(self, "facade", None)
        if facade is None:
            logger.warning("R3.3-77: facade 未就绪，跳过桥接挂载")
            return

        # 复用主程序那把事件总线（绝不能另建）
        raw_bus = facade._ensure_event_bus()
        if raw_bus is None:
            logger.warning("R3.3-77: facade EventBus 不可用，跳过桥接挂载")
            return

        from services.bridges import BridgeBus, build_bridge_network
        from services.bridges.pet_sink import make_default_sink, InMemoryPetSink
        from services.direction_compass import DirectionCompass
        from services.six_layer_persona import SixLayerProfile
        from services.relationship_graph import RelationshipGraph
        from services import script_library

        affection = getattr(facade, "_affection_engine", None)
        task_engine = facade.services.get("task_engine")
        memory = getattr(facade, "_memory", None)
        db = getattr(facade, "_db", None)
        compass = DirectionCompass(db=db)
        portrait = SixLayerProfile(person_id="session")
        graph = RelationshipGraph(center_id="me")

        # 桌宠出口：生产走真实 WS 广播（loop 在跑时）；不可用则内存 sink + 记日志
        try:
            pet_sink = make_default_sink()
        except Exception:
            pet_sink = InMemoryPetSink()
            logger.info("R3.3-77: 桌宠 WS 出口不可用，桥网降级为内存 sink")

        bus = BridgeBus(bus=raw_bus)
        net = build_bridge_network(
            affection=affection, task_engine=task_engine, compass=compass,
            memory=memory, portrait=portrait, script_lib=script_library,
            pet_sink=pet_sink, graph=graph, bus=bus)
        self.bridge_network = net
        logger.info(
            "R3.3-77 桥接网已挂载：%d 座桥，共用 facade EventBus=%s",
            len(net["bridges"]), type(raw_bus).__name__)

    def _ensure_directories(self) -> None:
        """确保运行期需要的可写目录存在（data/logs/models_local/backups）。"""
        # === 随包运行时接入（新增）：可写目录落安装目录而非只读 _internal ===
        try:
            from services import runtime_paths as _rp
            _rp.ensure_runtime_dirs()
        except Exception:
            pass
        for sub in ("data", "logs", "models_local", "backups"):
            try:
                os.makedirs(os.path.join(self._project_root, sub), exist_ok=True)
            except Exception:
                # 目录创建失败不应阻断启动（数据库/日志层有各自的兜底）
                pass

    def _setup_logging(self) -> None:
        """配置根日志：文件按天轮转 + 控制台输出。"""
        # === 随包运行时接入：冻结态日志落安装目录可写 logs，避免写只读 _internal ===
        # 开发态保持 self._project_root/logs 原落点不变。
        try:
            from services import runtime_paths as _rp
            if _rp.is_frozen():
                log_dir = _rp.writable_logs_dir()
            else:
                log_dir = os.path.join(self._project_root, "logs")
        except Exception:
            log_dir = os.path.join(self._project_root, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "loveai.log")

        root_logger = logging.getLogger()
        # 避免重复添加 handler
        if getattr(root_logger, "_loveai_configured", False):
            return

        root_logger.setLevel(logging.INFO)

        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 按天轮转，保留 30 天
        file_handler = TimedRotatingFileHandler(
            log_path, when="midnight", interval=1, backupCount=30, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(logging.INFO)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(fmt)
        console_handler.setLevel(logging.INFO)

        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        root_logger._loveai_configured = True  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ #
    # 随包便携 Ollama 生命周期（P0 根因修复）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _port_in_use(host: str, port: int) -> bool:
        """探测 TCP 端口是否已有服务监听（0.3s 短超时）。"""
        import socket
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            return False

    def _ensure_portable_ollama(self) -> None:
        """端口未占用且随包 ollama_runtime/ollama.exe 就位时，后台拉起 `ollama.exe serve`。

        - 已在服务（兼容用户本机已装 Ollama）则不重复拉起；
        - 以 subprocess.Popen 后台运行，不阻塞 UI；环境变量由 runtime_paths 给出；
        - 仅做一次 0.5s 快速崩溃探测（子进程秒退则立即降级），真正的冷启动就绪
          （30~60s）交给后台 QTimer 周期探测，不在启动路径上长时阻塞；
        - 记录子进程句柄；退出路径（quit() 与 aboutToQuit）均会调用 _stop_portable_ollama，
          按进程树 terminate→taskkill /T /F 兜底，避免 ollama/llama-server 孤儿。
        """
        try:
            import subprocess
            import time
            from services import runtime_paths as _rp

            # 已在服务：本机 Ollama，不重复拉起
            if self._port_in_use("127.0.0.1", 11434):
                logger.info("检测到 127.0.0.1:11434 已有 Ollama 服务，跳过随包拉起")
                return
            # 已拉起过（句柄仍在），不重复
            if self._portable_ollama_proc is not None and self._portable_ollama_proc.poll() is None:
                return
            if not _rp.is_portable_ollama_available():
                return

            env = {**os.environ, **_rp.portable_ollama_env()}
            self._portable_ollama_proc = subprocess.Popen(
                [_rp.ollama_exe(), "serve"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            # 不阻塞启动：仅探测子进程是否秒退；冷启动就绪交给后台定时器
            time.sleep(0.5)
            if self._portable_ollama_proc.poll() is not None:
                logger.warning("随包 Ollama 进程已退出（code=%s），降级使用云端/规则",
                               self._portable_ollama_proc.returncode)
                self._portable_ollama_proc = None
                return
            self._start_ollama_ready_watch()
        except Exception as e:
            logger.warning("随包 Ollama 启动失败（降级，不影响启动）: %s", e)
            self._portable_ollama_proc = None

    # ---- 后台就绪检测（非阻塞、不阻止退出）---- #
    def _start_ollama_ready_watch(self) -> None:
        """用 QTimer 在事件循环内周期探测 11434；就绪后记录状态，超时静默降级。"""
        try:
            from PySide6.QtCore import QTimer
            self._local_engine_ready = False
            self._ollama_ready_attempts = 0
            self._ollama_ready_max = 40  # 40 × 1.5s ≈ 60s 覆盖冷启动
            timer = QTimer()
            timer.setInterval(1500)
            timer.timeout.connect(self._ollama_ready_tick)
            timer.start()
            self._ollama_ready_timer = timer
        except Exception as e:
            logger.debug("启动 Ollama 就绪检测定时器失败（忽略）: %s", e)
            self._ollama_ready_timer = None

    def _ollama_ready_tick(self) -> None:
        """定时器回调：探测端口/子进程状态，就绪或超时即停止。"""
        self._ollama_ready_attempts = getattr(self, "_ollama_ready_attempts", 0) + 1
        try:
            if self._port_in_use("127.0.0.1", 11434):
                self._on_local_engine_ready()
                return
        except Exception:
            pass
        proc = getattr(self, "_portable_ollama_proc", None)
        if proc is not None and proc.poll() is not None:
            logger.warning("随包 Ollama 进程已退出，就绪检测终止（降级云端/规则）")
            self._stop_ollama_ready_watch()
            return
        if self._ollama_ready_attempts >= self._ollama_ready_max:
            logger.warning("随包 Ollama 就绪检测超时（约60s未监听），静默降级云端/规则")
            self._stop_ollama_ready_watch()

    def _on_local_engine_ready(self) -> None:
        """本地引擎就绪回调：记录状态并停止探测，随后后台注册随包模型。"""
        first_ready = not self._local_engine_ready
        self._local_engine_ready = True
        logger.info("本地引擎（随包 Ollama）已就绪: 127.0.0.1:11434")
        self._stop_ollama_ready_watch()
        if first_ready:
            # 模型 create 可能耗时（1.5B 冷加载约数十秒），放后台线程，不阻塞 UI。
            self._register_local_models_async()

    def _register_local_models_async(self) -> None:
        """后台线程把随包 GGUF 幂等注册到 Ollama（loveai-qwen15b 主力 / loveai-qwen05b 兜底）。"""
        if self._registering_models or self._local_models_registered:
            return
        self._registering_models = True
        t = threading.Thread(target=self._register_local_models_worker,
                             name="local-model-register", daemon=True)
        t.start()

    def _register_local_models_worker(self) -> None:
        try:
            from services import runtime_paths as _rp
            from services import local_model_manager as _lmm
            portable = _rp.is_portable_ollama_available()
            models_root = _rp.models_local_dir()
            if portable:
                exe = _rp.ollama_exe()
                env = _rp.portable_ollama_env()
                # 同卷硬链接预置 blob 的目标仓库（安装目录\ollama_data\models）
                models_store = _rp.ollama_models_dir()
            else:
                # 本机已装/已运行 Ollama：CLI 走 PATH，环境不覆盖
                exe = None
                env = None
                models_store = os.environ.get("OLLAMA_MODELS")
            status = _lmm.ensure_registered(
                models_local_root=models_root, ollama_exe=exe, env=env,
                create_timeout=240.0, models_store_dir=models_store)
            self._local_models_registered = (status.get("status") == "OK")
            logger.info("随包本地模型注册结果: %s",
                        json.dumps(status, ensure_ascii=False)[:800])
        except Exception as e:  # noqa: BLE001
            logger.warning("后台注册本地模型失败（不影响使用，可云端/规则兜底）: %s", e)
        finally:
            self._registering_models = False

    def _stop_ollama_ready_watch(self) -> None:
        t = getattr(self, "_ollama_ready_timer", None)
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass
        self._ollama_ready_timer = None

    # ---- 进程树终止（回收）---- #
    def _stop_portable_ollama(self) -> None:
        """退出时终止本进程拉起的便携 Ollama 进程树（不影响用户本机已装实例）。

        terminate 仅杀 ollama.exe 本身，其派生的 llama-server 孙进程会残留；
        故 terminate→短等待→仍存活则 taskkill /PID <pid> /T /F 整树兜底。
        幂等：重复调用不报错（句柄置空后直接返回）。
        """
        # 先停就绪检测定时器，避免退出后仍触发
        self._stop_ollama_ready_watch()
        proc = getattr(self, "_portable_ollama_proc", None)
        if proc is None:
            return
        try:
            pid = proc.pid
            # 关键顺序：先 taskkill 整树（此时父进程仍存活、进程树完整），
            # 再 reap。若先 terminate() 父进程会立即死亡，孙进程（llama-server）
            # 变成孤儿，后续 taskkill /T 按父 PID 枚举不到它 → 残留。
            try:
                import subprocess
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=10,
                )
            except Exception as e:
                logger.debug("taskkill 终止 ollama 进程树失败: %s", e)
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            # 兜底：仍存活再 terminate
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
            logger.info("随包 Ollama 进程树已终止 pid=%s", pid)
        except Exception as e:
            logger.debug("终止随包 Ollama 子进程失败（忽略）: %s", e)
        finally:
            self._portable_ollama_proc = None

    def _init_core_services(self) -> None:
        """初始化阶段一的核心服务：config + db。"""
        # 配置服务
        config_service = ConfigService(self._config_path)
        self.services["config"] = config_service
        logger.info("ConfigService 就绪")

        # 数据库
        db_path = config_service.get("data.db_path", "data/loveai.db")
        if not os.path.isabs(db_path):
            # === 随包运行时接入：冻结态相对路径落安装目录可写 data，避免写只读 _internal ===
            # 开发态保持 self._project_root 原落点不变。
            try:
                from services import runtime_paths as _rp
                if _rp.is_frozen():
                    db_path = os.path.join(_rp.install_dir(), db_path)
                else:
                    db_path = os.path.join(self._project_root, db_path)
            except Exception:
                db_path = os.path.join(self._project_root, db_path)
        db: Database = get_database(db_path)
        db.init_tables()
        self.services["db"] = db
        logger.info("Database 就绪: %s", db_path)

    def _init_phase2_services(self) -> None:
        """初始化阶段二服务：LLM网关 / 情感分析 / 安全破假 / 好感度引擎 / 微信服务 / RAG检索。

        每个服务独立 try/except 导入，模块缺失或初始化失败时跳过并记日志，
        不影响其他服务和应用启动。ChatAnalyzer 依赖以上服务，在最后创建。
        """
        config = self.services.get("config")
        db = self.services.get("db")
        signals = self.signals

        # 1. LLM 网关（大模型三级路由 + 懒加载）
        try:
            from services.llm_gateway import LLMGateway
            self.services["llm"] = LLMGateway(config, signals, db=db)
            logger.info("LLMGateway 就绪")
        except Exception as e:
            logger.warning("LLMGateway 初始化失败，大模型功能不可用: %s", e)

        # 2. 情感分析（规则匹配为主，LLM增强可选）
        try:
            from services.emotion_analyzer import EmotionAnalyzer
            self.services["emotion"] = EmotionAnalyzer(self.services.get("llm"))
            logger.info("EmotionAnalyzer 就绪")
        except Exception as e:
            logger.warning("EmotionAnalyzer 初始化失败: %s", e)

        # 3. 安全破假（敏感词替换 + 云端拒绝检测 + 本地降级）
        try:
            from services.safety_filter import SafetyFilter
            self.services["safety"] = SafetyFilter()
            logger.info("SafetyFilter 就绪")
        except Exception as e:
            logger.warning("SafetyFilter 初始化失败: %s", e)

        # 4. 18维好感度引擎
        try:
            from services.affection_engine import AffectionEngine
            self.services["affection"] = AffectionEngine(db)
            logger.info("AffectionEngine 就绪")
        except Exception as e:
            logger.warning("AffectionEngine 初始化失败: %s", e)

        # 5. 微信监听服务（pywechat127 或降级模式）
        # R3.3-70 决策：微信相关功能全部暂缓。此处按 config 开关严格守闸——
        # 默认 monitor_enabled=False，关闭时不创建服务、不连接微信、不起监听线程。
        _wechat_on = bool(config.get("wechat.monitor_enabled", False))
        if not _wechat_on:
            logger.info("微信监听按 R3.3-70 决策保持关闭（wechat.monitor_enabled=False）")
        else:
          try:
            from services.wechat_service import WeChatService
            wechat = WeChatService(signals, config)
            self.services["wechat"] = wechat
            logger.info("WeChatService 就绪")
            try:
                wechat.connect()
                wechat.start_listener()
                logger.info("微信监听已启动，模式=%s", wechat.get_status().get("mode", "unknown"))
            except Exception as e:
                logger.warning("微信自动连接异常（监听线程仍运行，将自动重连）: %s", e)
                # 确保监听线程在运行（即使首次连接失败）
                if not wechat.running:
                    try:
                        wechat.start_listener()
                    except Exception:
                        pass
          except Exception as e:
            logger.warning("WeChatService 初始化失败，微信监听不可用: %s", e)

        # 5.1 微信聊天记录导入器（全盘扫描+多版本适配+5种导入方式+防封号）
        if _wechat_on:
          try:
            from services.wechat_importer import WeChatImporter
            self.services["wechat_importer"] = WeChatImporter(db, config)
            logger.info("WeChatImporter 就绪（全盘扫描+3.x/4.x适配+5种导入方式）")
          except Exception as e:
            logger.warning("WeChatImporter 初始化失败: %s", e)

        # 5.1b 微信接入40种方法+一键自动导入（R3.3-70：默认不装配，含内存dump/DB解密等敏感通道）
        if _wechat_on:
          try:
            from services.wechat_auto_importer import WechatAutoImporter
            from services.wechat_access_manager import WechatAccessManager, register_all_methods
            from services.wechat_vision_monitor import WechatVisionMonitor
            from services.wechat_cache_reader import WechatCacheReader
            from services.wechat_system_reader import WechatSystemReader
            from services.wechat_config_parser import WechatConfigParser
            from services.wechat_mobile_reader import WechatMobileReader
            from services.wechat_advanced_reader import WechatAdvancedReader
            from tools.wechat_db_decryptor import WechatDbDecryptor
            from tools.wechat_memory_dumper import WechatMemoryDumper
            self.services["wechat_auto_importer"] = WechatAutoImporter()
            self.services["wechat_access_manager"] = WechatAccessManager()
            self.services["wechat_vision_monitor"] = WechatVisionMonitor()
            self.services["wechat_cache_reader"] = WechatCacheReader()
            self.services["wechat_system_reader"] = WechatSystemReader()
            self.services["wechat_config_parser"] = WechatConfigParser()
            self.services["wechat_mobile_reader"] = WechatMobileReader()
            self.services["wechat_advanced_reader"] = WechatAdvancedReader()
            self.services["wechat_db_decryptor"] = WechatDbDecryptor()
            self.services["wechat_memory_dumper"] = WechatMemoryDumper()
            register_all_methods(self.services["wechat_auto_importer"], self.services)
            logger.info("微信40种接入服务就绪（含一键自动导入）")
          except Exception as e:
            logger.warning("微信40种接入服务初始化失败: %s", e)

        # 5.1c 微信多方法密钥提取器 + 实时消息监听器（4.x SQLCipher4）
        # R3.3-70 + 第十七章红线：内存读密钥类通道默认不加载，显式开启才装配。
        if _wechat_on:
          try:
            from services.wechat_key_extractor import WechatKeyExtractor
            from services.wechat_realtime_monitor import WechatRealtimeMonitor
            self.services["wechat_key_extractor"] = WechatKeyExtractor()
            self.services["wechat_realtime_monitor"] = WechatRealtimeMonitor()
            logger.info("微信密钥提取器+实时监听器就绪（6种提取方法+3种监听模式）")
          except Exception as e:
            logger.warning("微信密钥提取器/实时监听器初始化失败: %s", e)

        # 5.2 代理服务（内置代理管理，用于访问外部AI）
        try:
            from services.proxy_service import ProxyService
            self.services["proxy"] = ProxyService(config)
            logger.info("ProxyService 就绪（内置代理管理）")
        except Exception as e:
            logger.warning("ProxyService 初始化失败: %s", e)

        # 5.3 生理周期服务（姨妈期/排卵期/黄体期管理与攻略）
        try:
            from services.cycle_service import CycleService
            self.services["cycle"] = CycleService(db, config)
            logger.info("CycleService 就绪（生理周期管理与攻略）")
        except Exception as e:
            logger.warning("CycleService 初始化失败: %s", e)

        # 5.4 心情状态服务（实时心情标签+惯例情绪模式+自动检测）
        try:
            from services.mood_service import MoodService
            self.services["mood"] = MoodService(db, config)
            logger.info("MoodService 就绪（实时心情状态+惯例模式）")
        except Exception as e:
            logger.warning("MoodService 初始化失败: %s", e)

        # 6. RAG 检索（FTS5 + Chroma 双引擎）
        try:
            from services.rag_service import RAGService
            self.services["rag"] = RAGService(config)
            logger.info("RAGService 就绪")
        except Exception as e:
            logger.warning("RAGService 初始化失败，知识库检索不可用: %s", e)

    def _init_phase3_services(self) -> None:
        """初始化阶段三服务：数字分身/任务引擎/攻略引擎/财务分析/咨询引擎/勋章/画像。

        每个服务独立 try/except 导入，模块缺失或初始化失败时跳过。
        digital_twin 绑定 person_id，用工厂方式按需创建（不全局注册单例）。
        """
        db = self.services.get("db")
        config = self.services.get("config")
        signals = self.signals
        llm = self.services.get("llm")
        rag = self.services.get("rag")
        emotion = self.services.get("emotion")
        affection = self.services.get("affection")

        # 1. 攻略引擎（TOP二私教，双窗口模式）
        tutorial = None
        try:
            from services.tutorial_engine import TutorialEngine
            tutorial = TutorialEngine(db, rag, llm, twin=None)
            self.services["tutorial"] = tutorial
            logger.info("TutorialEngine 就绪")
        except Exception as e:
            logger.warning("TutorialEngine 初始化失败: %s", e)

        # 1.5 策略适配矩阵（因人施策：按对方 21 维画像交叉匹配推荐/禁忌技巧）
        strategy_advisor = None
        try:
            from services.strategy_advisor import StrategyAdvisor
            strategy_advisor = StrategyAdvisor(db)
            self.services["strategy_advisor"] = strategy_advisor
            logger.info("StrategyAdvisor 就绪")
        except Exception as e:
            logger.warning("StrategyAdvisor 初始化失败: %s", e)

        # 2. 任务引擎（引导闭环六步）
        try:
            from services.task_engine import TaskEngine
            self.services["task_engine"] = TaskEngine(
                db, affection, tutorial, twin=None, signals=signals,
                strategy_advisor=strategy_advisor,
            )
            logger.info("TaskEngine 就绪")
        except Exception as e:
            logger.warning("TaskEngine 初始化失败: %s", e)

        # 2.5 人物心理画像分析器
        try:
            from services.persona_analyzer import PersonaAnalyzer
            self.services["persona"] = PersonaAnalyzer(db)
            logger.info("PersonaAnalyzer 就绪")
        except Exception as e:
            logger.warning("PersonaAnalyzer 初始化失败: %s", e)

        # 3. 财务分析引擎
        try:
            from services.financial_analyzer import FinancialAnalyzer
            self.services["financial"] = FinancialAnalyzer(db, llm)
            logger.info("FinancialAnalyzer 就绪")
        except Exception as e:
            logger.warning("FinancialAnalyzer 初始化失败: %s", e)

        # 4. 情感咨询三轮引擎
        try:
            from services.consult_engine import ConsultEngine
            self.services["consult"] = ConsultEngine(
                llm, rag, emotion=emotion, signals=signals,
                strategy_advisor=strategy_advisor,
            )
            logger.info("ConsultEngine 就绪")
        except Exception as e:
            logger.warning("ConsultEngine 初始化失败: %s", e)

        # 5. 勋章服务
        try:
            from services.medal_service import MedalService
            self.services["medal"] = MedalService(db, signals=signals)
            logger.info("MedalService 就绪")
        except Exception as e:
            logger.warning("MedalService 初始化失败: %s", e)

        # 6. 画像服务
        try:
            from services.portrait_service import PortraitService
            assets_dir = os.path.join(self._project_root, "assets")
            self.services["portrait"] = PortraitService(db, assets_dir=assets_dir)
            logger.info("PortraitService 就绪")
        except Exception as e:
            logger.warning("PortraitService 初始化失败: %s", e)

        # 7. 数字分身工厂（绑定 person_id，按需创建）
        self._digital_twin_cache = {}
        self.services["digital_twin_factory"] = self._create_digital_twin
        logger.info("DigitalTwin 工厂就绪（按需创建）")

    def _create_digital_twin(self, person_id: str):
        """按需创建/缓存数字分身实例（绑定 person_id）。"""
        if person_id in self._digital_twin_cache:
            return self._digital_twin_cache[person_id]
        try:
            from services.digital_twin import DigitalTwin
            data_dir = os.path.join(self._project_root, "data")
            twin = DigitalTwin(
                person_id=person_id,
                db=self.services.get("db"),
                llm=self.services.get("llm"),
                rag=self.services.get("rag"),
                data_dir=data_dir,
                # 注入长记忆服务：分身预测时会把该对象的喜好/承诺/雷区等
                # 长记忆拼入 system prompt；未就绪时为 None，分身降级为旧行为。
                memory_service=self.services.get("memory"),
            )
            self._digital_twin_cache[person_id] = twin
            return twin
        except Exception as e:
            logger.warning("DigitalTwin 创建失败 person_id=%s: %s", person_id, e)
            return None

    def _init_phase4_services(self) -> None:
        """初始化阶段四服务：语音/OCR/统计/插件/记忆/备份。

        每个服务独立 try/except，模块缺失或失败时跳过。
        重依赖（faster-whisper/RapidOCR）懒加载，服务本身可安全初始化。
        """
        db = self.services.get("db")
        config = self.services.get("config")
        signals = self.signals
        rag = self.services.get("rag")
        wechat = self.services.get("wechat")

        # 1. 语音识别服务
        try:
            from services.speech_service import SpeechService
            self.services["speech"] = SpeechService(db=db, config=config, wechat_service=wechat, signals=signals)
            logger.info("SpeechService 就绪")
        except Exception as e:
            logger.warning("SpeechService 初始化失败: %s", e)

        # 2. OCR 截屏识别服务
        try:
            from services.ocr_service import OCRService
            self.services["ocr"] = OCRService(db=db, config=config, signals=signals)
            logger.info("OCRService 就绪")
        except Exception as e:
            logger.warning("OCRService 初始化失败: %s", e)

        # 3. 数据统计服务
        try:
            from services.stats_service import StatsService
            self.services["stats"] = StatsService(db)
            logger.info("StatsService 就绪")
        except Exception as e:
            logger.warning("StatsService 初始化失败: %s", e)

        # 4. 插件管理服务
        try:
            from services.plugin_service import PluginService
            self.services["plugin"] = PluginService(db=db, config=config, signals=signals)
            logger.info("PluginService 就绪")
        except Exception as e:
            logger.warning("PluginService 初始化失败: %s", e)

        # 5. 长期记忆服务
        try:
            from services.memory_service import MemoryService
            self.services["memory"] = MemoryService(db, config=config, rag=rag, signals=signals)
            logger.info("MemoryService 就绪")
        except Exception as e:
            logger.warning("MemoryService 初始化失败: %s", e)

        # 5b. R3.3-7：CalibrationRunner 接生产。
        # 旧实现：只在脚本里跑 demo，生产写记忆不走 MemoryGate。
        # 新实现：启动时就实例化（注入 memory_service），让回灌路径
        # _flush_calibration_to_memory -> guarded_write_calibration 真过闸门；
        # 同时把它存到 services["calibration_runner"]，供后台调度器周期性触发。
        try:
            from services.calibration_runner import CalibrationRunner
            _mem = self.services.get("memory")
            _data_dir = os.path.dirname(getattr(db, "db_path", "")) or None
            if _data_dir:
                self.services["calibration_runner"] = CalibrationRunner(
                    data_dir=_data_dir,
                    memory_service=_mem,
                    use_review_loop=True,
                )
                logger.info("CalibrationRunner 已挂接生产（memory_service=%s）",
                            "已注入" if _mem is not None else "None")
        except Exception as e:
            logger.warning("CalibrationRunner 初始化失败: %s", e)

        # 6. 备份服务
        try:
            from services.backup_service import BackupService
            self.services["backup"] = BackupService(project_root=self._project_root, config=config)
            logger.info("BackupService 就绪")
        except Exception as e:
            logger.warning("BackupService 初始化失败: %s", e)

        # 7. 用户说话风格学习服务（本地为主体，云端仅可选增强，结果回到本地）
        try:
            from services.style_learner import StyleLearner
            llm = self.services.get("llm")
            # 可读档案 user_style.md 与主数据库放同一目录，frozen 下路径也一致
            data_dir = os.path.dirname(getattr(db, "db_path", "")) or None
            self.services["style_learner"] = StyleLearner(db, llm=llm, data_dir=data_dir)
            logger.info("StyleLearner 就绪")
        except Exception as e:
            logger.warning("StyleLearner 初始化失败: %s", e)

        # 7b. 本地AI反哺训练系统（数据收集→LoRA训练→版本管理，默认关闭，数据不上传）
        try:
            from services.training_data_collector import TrainingDataCollector
            from services.training_engine import TrainingEngine
            from services.model_version_manager import ModelVersionManager
            train_collector = TrainingDataCollector(data_dir=os.path.join(self._project_root, "data", "training_data"))
            train_config = (config.get("training") if hasattr(config, "get") else {}) or {}
            version_mgr = ModelVersionManager(
                base_model_path=train_config.get("local_model_path") or None,
                adapters_dir=os.path.join(self._project_root, "models", "lora_adapters"))
            train_engine = TrainingEngine(
                config=train_config,
                data_collector=train_collector,
                version_manager=version_mgr)
            self.services["training_data_collector"] = train_collector
            self.services["training_engine"] = train_engine
            self.services["model_version_manager"] = version_mgr
            logger.info("反哺训练系统就绪（默认关闭，用户可在设置中开启）")
        except Exception as e:
            logger.warning("反哺训练系统初始化失败: %s", e)

        # 8. 第五批：抖音借鉴功能（Jev轻量判断层 / AuthHelper验证码 / CodexAdaptation）
        try:
            from services.jev_judge import JevJudge
            self.services["jev_judge"] = JevJudge(llm=self.services.get("llm"))
            logger.info("JevJudge 就绪")
        except Exception as e:
            logger.warning("JevJudge 初始化失败: %s", e)

        try:
            from services.auth_helper import AuthHelper
            data_dir = os.path.join(self._project_root, "data", "auth")
            self.services["auth_helper"] = AuthHelper(auth_dir=data_dir)
            logger.info("AuthHelper 就绪")
        except Exception as e:
            logger.warning("AuthHelper 初始化失败: %s", e)

        try:
            from services.codex_adaptation import CodexAdaptation
            self.services["codex_adaptation"] = CodexAdaptation(project_root=self._project_root)
            logger.info("CodexAdaptation 就绪")
        except Exception as e:
            logger.warning("CodexAdaptation 初始化失败: %s", e)

    # ------------------------------------------------------------------ #
    # 本地桌面 AI 扫描（非阻塞，仅把结果写入服务表，不启用路由）
    # ------------------------------------------------------------------ #
    def _init_desktop_ai_scan(self) -> None:
        """后台线程跑一次 LocalAIScanner.scan()，结果落到 services["desktop_ai_candidates"]。

        - 不阻塞启动：scan 内部含 netstat/tasklist/端口握手，可能数秒，放 daemon 线程；
        - 不默认启用 desktop_ai 路由（config 开关默认 False），仅让设置页能读到候选列表；
        - 任何异常都降级为空结果，不影响主流程。
        """
        # 先放一个占位，设置页立即能读到「扫描中」
        self.services["desktop_ai_candidates"] = {
            "status": "scanning",
            "installed": [],
            "openai_endpoints": [],
            "usable_models": [],
            "notes": ["扫描进行中…"],
        }

        def _worker():
            try:
                from services.local_ai_scanner import LocalAIScanner
                scanner = LocalAIScanner()
                result = scanner.scan() or {}
                result["status"] = "done"
                self.services["desktop_ai_candidates"] = result
                installed = result.get("installed") or []
                logger.info(
                    "本地桌面 AI 扫描完成：已安装 %d 个，OpenAI 端点 %d 个，可调用模型 %d 个",
                    len(installed),
                    len(result.get("openai_endpoints") or []),
                    len(result.get("usable_models") or []),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("本地桌面 AI 扫描失败（不影响启动）: %s", e)
                self.services["desktop_ai_candidates"] = {
                    "status": "failed",
                    "installed": [],
                    "openai_endpoints": [],
                    "usable_models": [],
                    "notes": [f"扫描失败: {e}"],
                }

        t = threading.Thread(target=_worker, name="desktop-ai-scan", daemon=True)
        t.start()

    # ------------------------------------------------------------------ #
    # UI 层安全创建（模块未实现时返回 None，不抛异常）
    # ------------------------------------------------------------------ #
    def _try_create_window_manager(self):
        """尝试创建 WindowManager，模块缺失时返回 None。"""
        try:
            from app.window_manager import WindowManager
            wm = WindowManager(self.services["config"], self.signals, self.facade)
            logger.info("WindowManager 就绪")
            return wm
        except ImportError as e:
            logger.warning("WindowManager 尚未实现，占位为 None: %s", e)
            return None
        except Exception as e:
            logger.error("WindowManager 创建失败: %s", e)
            return None

    def _try_create_tray(self):
        """尝试创建 TrayManager，模块缺失时返回 None。"""
        try:
            from app.tray import TrayManager
            tray = TrayManager(self.signals, self.services["config"])
            logger.info("TrayManager 就绪")
            return tray
        except ImportError as e:
            logger.warning("TrayManager 尚未实现，占位为 None: %s", e)
            return None
        except Exception as e:
            logger.error("TrayManager 创建失败: %s", e)
            return None

    def _try_create_shortcuts(self):
        """尝试创建 ShortcutManager，模块缺失时返回 None。"""
        try:
            from app.shortcuts import ShortcutManager
            # main_window/pet_window 由 WindowManager 创建后注入
            sm = ShortcutManager(None, None, self.signals, self.services["config"])
            logger.info("ShortcutManager 就绪")
            return sm
        except ImportError as e:
            logger.warning("ShortcutManager 尚未实现，占位为 None: %s", e)
            return None
        except Exception as e:
            logger.error("ShortcutManager 创建失败: %s", e)
            return None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _run_startup_probe(self) -> None:
        """R3.3-31：主窗口显示前全量探活一次（懒接线，失败不阻断启动）。

        - 正常只打一行 overall 日志；
        - degraded/fail 明细追加到 logs/startup_probe.log，供 UI/排障查看。
        """
        try:
            from services.startup_probe import (
                probe_all_services, write_degraded_to_logfile)
            config = self.services.get("config") or {}
            report = probe_all_services(config)
            overall = report.get("summary", {}).get("overall_status")
            logger.info("启动探活完成: overall=%s", overall)
            self._startup_probe_status = overall  # 供主窗口状态栏提示
            if overall in ("degraded", "fail"):
                path = write_degraded_to_logfile(report)
                if path:
                    logger.warning("启动探活明细已写入 %s", path)
                    self._startup_probe_log = path
                # 收集降级/失败项明细，用于状态栏提示
                items = report.get("items") or report.get("services") or {}
                bad = [k for k, v in items.items()
                       if isinstance(v, dict) and v.get("status") in ("degraded", "fail")]
                self._startup_probe_bad = bad
        except Exception as e:  # 探活本身绝不能拖垮启动
            logger.warning("启动探活跳过: %s", e)

    def _nudge_status_bar_probe(self) -> None:
        """主窗口打开后，若探活 degraded/fail，在状态栏给一条醒目提示。"""
        try:
            if self._startup_probe_status not in ("degraded", "fail"):
                return
            main_win = None
            try:
                main_win = self.window_manager.get_window('main')
            except Exception:
                main_win = None
            if main_win is None:
                return
            sb = main_win.statusBar() if hasattr(main_win, "statusBar") else None
            if sb is None:
                return
            bad = getattr(self, "_startup_probe_bad", []) or []
            detail = "、".join(bad[:5]) if bad else "部分服务"
            level = "故障" if self._startup_probe_status == "fail" else "降级"
            msg = f"⚠ 启动探活{level}：{detail} 未就绪，详见 logs/startup_probe.log"
            sb.showMessage(msg, 15000)
            logger.warning("状态栏已提示探活%s: %s", level, detail)
        except Exception as e:  # 状态栏提示失败不影响启动
            logger.debug("状态栏探活提示失败（忽略）: %s", e)

    def run(self) -> int:
        """显示主窗口+桌宠，进入 Qt 事件循环。返回退出码。"""
        qt_app = QApplication.instance()
        if qt_app is None:
            logger.error("QApplication 未初始化")
            return 1

        # 启动后台定时调度器（定期自动刷新画像/好感度/数字分身/财务/记忆）
        if self._scheduler is not None:
            try:
                self._scheduler.start()
            except Exception as e:
                logger.error("后台调度器启动失败: %s", e)

        # R3.3-31 启动全量探活：主窗口显示前跑一遍，只打一行日志，
        # degraded/fail 明细落 logs/startup_probe.log；任何异常都不阻断启动。
        self._startup_probe_status = "ok"
        self._startup_probe_log = ""
        self._startup_probe_bad: list = []
        self._run_startup_probe()

        # 显示主窗口（启动时只显示主窗口，悬浮球在主窗口关闭后显示）
        if self.window_manager is not None:
            try:
                self.window_manager.open_window('main')
                # degraded/fail → 主窗口状态栏提示（R3.3-31）
                self._nudge_status_bar_probe()
            except Exception as e:
                logger.error("显示主窗口失败: %s", e)
        else:
            logger.warning("WindowManager 未就绪，仅启动事件循环（无UI）")

        # 预创建桌宠实例（不显示，主窗口关闭后自动显示）
        if self.window_manager is not None:
            try:
                # 只创建不显示，避免与主窗口同时出现
                fb = self.window_manager.get_window('float_ball')
                if fb is None:
                    # 通过内部方法创建但不show
                    from app.widgets.float_ball import FloatBall
                    try:
                        from app.qt3d_pet import PetView
                        pet = PetView()
                        if hasattr(pet, 'get_container'):
                            fb = pet.get_container()
                        else:
                            fb = pet
                    except Exception:
                        fb = FloatBall(facade=self.window_manager._facade)
                    self.window_manager.windows['float_ball'] = fb
                    self.window_manager.windows['pet'] = fb
                    logger.info("桌宠实例已预创建（未显示）")
            except Exception as e:
                logger.error("预创建桌宠失败: %s", e)

        # 显示托盘
        if self.tray is not None:
            try:
                self.tray.show()
            except Exception as e:
                logger.error("托盘图标显示失败: %s", e)

        # 注册全局快捷键
        if self.shortcuts is not None:
            try:
                # 注入窗口引用
                if self.window_manager is not None:
                    self.shortcuts.main_window = self.window_manager.get_window('main')
                    self.shortcuts.pet_window = self.window_manager.get_window('float_ball')
                # R3.3-6: 把 SpeechService 注入快捷键管理器，让 Ctrl+Alt+V 真启动 VoicePipeline
                try:
                    self.shortcuts.speech_service = self.services.get("speech")
                except Exception:
                    pass
                self.shortcuts.register_all()
            except Exception as e:
                logger.error("快捷键注册失败: %s", e)

        logger.info("LoveAI 进入事件循环")
        return qt_app.exec()

    def quit(self) -> None:
        """清理资源、保存状态、退出应用。"""
        logger.info("LoveAI 正在退出...")

        # 0. 先停后台调度器，避免退出过程中又触发新的后台轮次
        if getattr(self, "_scheduler", None) is not None:
            try:
                self._scheduler.stop()
            except Exception as e:
                logger.error("停止后台调度器失败: %s", e)

        # 0.5 停止桌宠联动 WebSocket 服务端（关闭 server + 断开信号 + join 后台线程）
        try:
            from app import pet_ws_server
            pet_ws_server.stop_server()
        except Exception as e:
            logger.error("停止桌宠 WS 服务端失败: %s", e)

        # 0.55 R3.3-77：幂等关闭桥接网（错误隔离，不阻断退出）
        try:
            net = getattr(self, "bridge_network", None)
            if net is not None:
                net.get("shutdown", lambda: None)()
                self.bridge_network = None
        except Exception as e:
            logger.error("关闭桥接网失败（不影响退出）: %s", e)

        # 0.6 R3.3-76：主程序退出 -> 一并终止桌宠进程（杜绝孤儿进程）。
        #     顺序：先停 WS server（上方），再杀桌宠进程树；全程错误隔离。
        try:
            from app import pet_launcher
            pet_launcher.stop_launched_pet()
        except Exception as e:
            logger.error("停止桌宠进程失败（不影响退出）: %s", e)

        # 1. 通知所有模块即将退出
        try:
            self.signals.app_quitting.emit()
        except Exception as e:
            logger.error("发送 app_quitting 信号失败: %s", e)

        # 2. 停止微信监听 / 卸载模型等重服务
        for name, svc in list(self.services.items()):
            try:
                stop_fn = getattr(svc, "stop", None) or getattr(svc, "shutdown", None)
                if callable(stop_fn):
                    stop_fn()
            except Exception as e:
                logger.error("关闭服务 %s 时出错: %s", name, e)

        # 2.5 优雅终止本进程拉起的随包便携 Ollama（不影响用户本机已装实例）
        self._stop_portable_ollama()

        # 3. 关闭托盘
        if self.tray is not None:
            try:
                self.tray.hide()
            except Exception:
                pass

        # 4. 退出 Qt 事件循环
        qt_app = QApplication.instance()
        if qt_app is not None:
            qt_app.quit()

        logger.info("LoveAI 已退出")


# 兼容别名：外部脚本 / 启动链验证使用 `from app.application import Application`
Application = LoveAIApplication
