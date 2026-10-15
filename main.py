"""
LoveAI v3.0 - 程序入口（原生QWidget桌面端）
负责初始化QApplication、全局异常捕获、启动主应用。
v3.0变更：移除QWebEngine依赖，全部原生QWidget+Qt3D。
"""
import sys
import os
import logging
import traceback
from logging.handlers import TimedRotatingFileHandler

# === 随包运行时接入（新增）===
# 进程最早处初始化便携 Ollama 环境变量与可写目录；开发态检测不到 ollama_runtime，
# 自动退化为不改环境，零副作用。任何异常都不阻断启动。
try:
    from services import runtime_paths as _rp
    _rp.bootstrap_ollama_env()   # 随包 Ollama 时设置 OLLAMA_MODELS/HOST，不污染 AppData
    _rp.ensure_runtime_dirs()    # 创建可写 data/logs/ollama_data
    _rp.bootstrap_playwright_env()  # 随包 Chromium 时设 PLAYWRIGHT_BROWSERS_PATH，免 playwright install
except Exception:
    pass

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt


def _setup_fatal_exception_logging():
    """配置未捕获异常的钩子，写入日志文件，避免静默崩溃。"""
    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger = logging.getLogger("LoveAI.fatal")
        logger.critical(
            "未捕获异常:\n" + "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        )

    sys.excepthook = _hook


def main() -> int:
    """应用主入口。"""
    # 项目根目录：
    # - 开发态：本文件所在目录
    # - PyInstaller 打包态（onedir）：sys._MEIPASS（即 exe 所在目录，
    #   --add-data 收集的 ui/prompts/skills/assets/knowledge 均在此处）
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        project_root = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # 高DPI支持（PySide6 默认开启，这里显式声明以防后续版本变更）
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("LoveAI")
    app.setOrganizationName("LoveAI")
    app.setApplicationDisplayName("LoveAI")

    # 全局异常钩子（在 Application 初始化 logging 之前先做兜底）
    _setup_fatal_exception_logging()

    # 延迟导入，确保项目根目录已加入 sys.path
    from app.application import LoveAIApplication

    loveai = LoveAIApplication(config_path=os.path.join(project_root, "config.json"))
    return loveai.run()


if __name__ == "__main__":
    sys.exit(main())
