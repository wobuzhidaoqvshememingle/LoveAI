# 任务状态板 — 随包运行时接入 · 第3轮：Ollama 孤儿回收 + 就绪检测

## 状态：✅ 完成（真实随包 ollama 冷启动 11434 监听留待封包冒烟）

## 问题1（P0）孤儿回收 — app\application.py
实测：随包 ollama.exe serve 自动起服务（PID=6384、11434 HTTP200），但 CloseMainWindow 后主进程退出，
ollama 仍存活、端口仍监听 → 孤儿。

根因排查：
- 主窗口 closeEvent（app\widgets\main_window.py L1446）只 `event.accept()` + `hide()` + emit `hide_all_windows`，
  **不走 app_quitting / quit()**；app_quitting.connect(quit) 在 application.py L101，仅托盘/菜单一列路径触发。
- `_stop_portable_ollama` 原只在 quit() 内调用 → closeEvent 退出路径根本走不到。

修复：
- __init__（约 L55-66）初始化 `_portable_ollama_proc/_ollama_ready_timer/_local_engine_ready` 后调 `_ensure_portable_ollama()`，
  紧接 `QApplication.instance().aboutToQuit.connect(self._stop_portable_ollama)`（try/except 包裹），覆盖所有正常退出路径；
  quit() 内调用保留，幂等。
- `_stop_portable_ollama()` 进程树回收顺序修正（关键）：**先 `taskkill /PID <pid> /T /F` 整树清扫（此时父进程仍存活、树完整），
  再 `proc.wait(5s)`，兜底再 terminate**。
  - 实测反例：先 terminate() 父进程会立即死亡，孙进程（llama-server）成孤儿，后续 taskkill /T 按父 PID 枚举不到 → 残留。
    已在 test_real_process_tree_is_killed 中复现并验证修复后孙进程归零。

## 问题2（P1）就绪检测非阻塞 — app\application.py
- 冷启动 30~60s 超过原 25×0.2s≈5s 阻塞轮询。
- `_ensure_portable_ollama` 拉起后仅 `time.sleep(0.5)` 做秒退崩溃探测，真正就绪交给后台 QTimer：
  - `_start_ollama_ready_watch()`：QTimer 1.5s 间隔、最多 40 次≈60s；
  - `_ollama_ready_tick()`：探测 11434 / 子进程状态，就绪→`_on_local_engine_ready()`（置 `_local_engine_ready=True`、停表）；
    子进程退出或超时→静默降级停表；
  - `_stop_ollama_ready_watch()`：退出时停表，不阻止关闭。
- 启动路径不再长时阻塞。

## 测试结果
- tests\test_portable_ollama_lifecycle.py（9 例，含 aboutToQuit 触发回收 / taskkill 整树 / stop 幂等 /
  后台就绪 tick / 真实父+孙进程树终止）→ **9 passed in 3.40s**。
- 相关回归：tests\test_fts_index_idempotent.py + tests\test_runtime_paths_frozen.py → **4 passed in 0.85s**。

## 开发态冒烟
- main.py 启动存活（pid 29080），日志「窗口创建成功: main」「托盘图标已显示」「LoveAI 进入事件循环」，
  RAG Chroma 仍从 `<repo>\data\chroma_db` 加载（开发态等价）。
- 仓库无 ollama_runtime\ollama.exe → 11434 NOT_LISTENING、无 ollama 进程，静默降级无报错。
- taskkill /PID 29080 /T /F 后 MAIN_GONE、NO_OLLAMA_PROCESS（无残留）。

## 备份
改前固定名 .bak：app\application.py.bak（36893 bytes，覆盖旧版）。

## 边界遵守
未改 collect_payload / LoveAI.spec / LoveAI.iss / persona_analyzer / 诊断代理独占文件。
未跑全量隔离回归（调用方统一触发）。

## 待封包冒烟验证
- 真实随包 ollama.exe serve 冷启动后 11434 监听、aboutToQuit/CloseMainWindow 退出后 ollama+llama-server 进程树归零。
- 管理员装到 Program Files 只读场景下 db/logs 落点。
