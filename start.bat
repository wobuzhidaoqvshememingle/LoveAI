@echo off
rem ============================================================
rem  LoveAI 一键启动脚本 (Windows)
rem ------------------------------------------------------------
rem  启动顺序:
rem    1) 在新窗口启动 网关 (FastAPI, 127.0.0.1:8787)
rem       目录: gateway\   命令: python -m uvicorn app.main:app
rem       控制台: http://127.0.0.1:8787/   OpenAI 兼容: /v1
rem    2) 等待端口 8787 就绪
rem    3) 在本窗口启动 主程序 (PySide6 桌面端, main.py)
rem       主程序会经 app\pet_launcher.py 自动尝试拉起桌宠
rem       桌宠 WebSocket 端口: 9876 (Electron 或内置 Qt3D)
rem ============================================================
chcp 65001 >nul
setlocal enableextensions
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
set "GW_PORT=8787"

echo.
echo ============================================================
echo   LoveAI 启动
echo   项目根目录: %CD%
echo ============================================================
echo.

rem ---- 0. 前置检查: .venv 是否存在 ----
if not exist "%PY%" (
    echo [错误] 未找到虚拟环境: %PY%
    echo        请先双击运行 install.bat 完成安装, 再运行本脚本。
    echo.
    pause
    exit /b 1
)
echo [OK] 已找到 .venv: %PY%
echo.

rem ---- 1. 若网关端口已被占用, 则跳过重复启动 ----
echo [1/3] 检查网关端口 127.0.0.1:%GW_PORT% ...
powershell -NoProfile -Command "try { $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',%GW_PORT%); $c.Close(); exit 0 } catch { exit 1 }"
if not errorlevel 1 (
    echo     端口 %GW_PORT% 已在监听, 复用已有网关进程。
    goto :gw_ready
)

rem ---- 2. 在新窗口启动网关 ----
echo [2/3] 在新窗口启动网关 (gateway\, uvicorn :%GW_PORT%) ...
start "LoveAI Gateway :%GW_PORT%" cmd /k "cd /d ""%~dp0gateway"" && ""..\.venv\Scripts\python.exe"" -m uvicorn app.main:app --host 127.0.0.1 --port %GW_PORT%"
if errorlevel 1 (
    echo [错误] 无法在新窗口拉起网关。
    pause
    exit /b 1
)

rem ---- 3. 等待网关端口就绪 (最多 ~40 秒) ----
echo     等待网关就绪 (最多 40 秒) ...
set /a TRIES=0
:wait_gw
powershell -NoProfile -Command "try { $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',%GW_PORT%); $c.Close(); exit 0 } catch { exit 1 }"
if not errorlevel 1 goto :gw_ready
set /a TRIES+=1
if %TRIES% GEQ 40 (
    echo.
    echo [警告] 等待 40 秒后网关仍未监听 %GW_PORT%。
    echo        请查看刚弹出的 "LoveAI Gateway" 窗口里的报错。
    echo        仍尝试继续启动主程序, 但主程序可能无法连上网关。
    echo.
    goto :run_main
)
ping -n 2 127.0.0.1 >nul
goto :wait_gw

:gw_ready
echo     网关已就绪: http://127.0.0.1:%GW_PORT%/
echo.

:run_main
rem ---- 4. 启动主程序 (本窗口, 主程序会自动拉起桌宠) ----
echo [3/3] 启动主程序 main.py (PySide6 桌面端, 桌宠 WS :9876) ...
echo     关闭本窗口即结束主程序; 网关请单独关闭其窗口。
echo.
"%PY%" main.py
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo 主程序已正常退出。
) else (
    echo [提示] 主程序退出码: %RC%
)
echo.
pause
exit /b %RC%