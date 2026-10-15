@echo off
rem ============================================================
rem  LoveAI 一键安装脚本 (Windows)
rem  作用: 创建 .venv -> 升级 pip -> 安装主程序依赖 -> 安装网关依赖
rem        -> 安装桌宠 npm 依赖。幂等, 可重复运行。
rem  用法: 双击 install.bat, 或在命令行执行 install.bat
rem ============================================================
chcp 65001 >nul
setlocal enableextensions
cd /d "%~dp0"

echo.
echo ============================================================
echo   LoveAI 一键安装开始
echo   项目根目录: %CD%
echo ============================================================
echo.

rem ---- 0. 检查 python 是否可用 ----
echo [1/5] 检查 Python 环境...
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未在 PATH 中找到 python。
    echo        请先安装 Python 3.11 或 3.12, 并在安装时勾选 Add python.exe to PATH。
    echo.
    pause
    exit /b 1
)
python --version
if errorlevel 1 goto :install_failed
echo     Python 检查通过。
echo.

rem ---- 1. 创建 .venv (已存在则跳过) ----
echo [2/5] 检查/创建虚拟环境 .venv ...
if exist ".venv\Scripts\python.exe" (
    echo     .venv 已存在, 跳过创建, 幂等运行。
) else (
    echo     未发现 .venv, 正在创建...
    python -m venv .venv
    if errorlevel 1 goto :install_failed
    echo     .venv 创建完成。
)
echo.

set "PY=.venv\Scripts\python.exe"

rem ---- 2. 升级 pip ----
echo [3/5] 升级 pip ...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto :install_failed
echo     pip 升级完成。
echo.

rem ---- 3. 安装主程序 requirements.txt ----
echo [4/5] 安装主程序依赖 requirements.txt ...
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :install_failed
echo     主程序依赖安装完成。

rem ---- 3b. 安装网关 requirements.txt ----
echo     安装网关依赖 gateway\requirements.txt ...
"%PY%" -m pip install -r gateway\requirements.txt
if errorlevel 1 goto :install_failed
echo     网关依赖安装完成。
echo.

rem ---- 4. 安装桌宠 npm 依赖 ----
echo [5/5] 安装 Electron 桌宠依赖 desktop-pet-electron npm install ...
where npm >nul 2>nul
if errorlevel 1 (
    echo [警告] 未在 PATH 中找到 npm。
    echo        桌宠功能需要 Node.js 18+。请先安装 Node.js 后重跑本脚本。
    echo        主程序仍可使用内置 Qt3D 宠物, 不影响继续。
) else (
    pushd desktop-pet-electron
    if errorlevel 1 (
        echo [错误] 进入 desktop-pet-electron 目录失败。
        popd
        goto :install_failed
    )
    call npm install
    if errorlevel 1 (
        echo [错误] npm install 失败。
        popd
        goto :install_failed
    )
    popd
    echo     桌宠依赖安装完成。
)
echo.

rem ---- 显卡检测（实时数字人可勾选模块） ----
echo.
echo [显卡检测] 检测 NVIDIA 显卡，供实时数字人模块参考...
powershell -NoProfile -ExecutionPolicy Bypass -File "digital-human\gpu-detect.ps1"
echo     （tier=low: 本地数字人帧率低建议云端; tier=none: 无NVIDIA显卡，保持2D即可）
echo.

echo ============================================================
echo   安装完成!
echo   下一步: 双击 start.bat 启动 LoveAI。
echo ============================================================
echo.
pause
exit /b 0

:install_failed
echo.
echo ============================================================
echo   [安装失败] 上方某一步返回了非零退出码。
echo   请把错误日志截图反馈到 GitHub Issues:
echo   https://github.com/LoveAI-team/LoveAI/issues
echo ============================================================
echo.
pause
exit /b 1