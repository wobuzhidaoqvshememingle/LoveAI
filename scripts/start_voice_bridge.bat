@echo off
REM LoveAI-v2 系统精灵语音后端桥接启动脚本（关系分析助手，非恋爱对象）
REM 用法：双击本文件，或在 cmd 中执行 scripts\start_voice_bridge.bat

setlocal
cd /d "%~dp0.."
echo [voice-bridge] 项目根: %CD%

REM 激活 venv
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo [voice-bridge] 激活 venv 失败，退出
    exit /b 1
)

echo [voice-bridge] 启动 pet_voice_bridge.py ...
python scripts\pet_voice_bridge.py

endlocal
