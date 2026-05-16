@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

title DeepSeek Code Agent

:: ── 自动提权：ACL 安全锁需要管理员权限 ──
:: 用 whoami 检查管理员组 SID，比 net session 更可靠
whoami /groups | find "S-1-16-12288" >nul 2>&1
if !errorlevel! neq 0 (
    echo [信息] ACL 安全锁需要管理员权限，正在提权...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b 0
)

echo.
echo === DeepSeek Code Agent v1 快速启动 ===
echo.

:: ── 从 config.ini [server] 读取 port（首条匹配 ^port *=）──
set PORT=
for /f "usebackq tokens=2 delims==" %%a in (`findstr /r /i "^[ ]*port[ ]*=" config.ini 2^>nul`) do (
    if not defined PORT (
        set PORT=%%a
        set PORT=!PORT: =!
    )
)
if not defined PORT (
    echo [错误] 未在 config.ini 的 [server] 节找到 port 配置
    pause
    exit /b 1
)

:: 检查是否在项目根目录
if not exist "main_tray.py" (
    echo [错误] 请在项目根目录下运行此脚本
    echo.       未找到 main_tray.py
    pause
    exit /b 1
)

:: 自动检测并激活虚拟环境
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo [信息] 已激活虚拟环境: venv
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    echo [信息] 已激活虚拟环境: .venv
) else (
    echo [信息] 未找到虚拟环境，使用系统 Python
)

:: 检查 config.ini（与 util.config_loader 一致）
if not exist "config.ini" (
    echo [警告] 未找到 config.ini！
    echo.       请复制模板并配置 [model] api_key、[server] port 等后再启动
    echo.
)

:: 检查依赖是否已安装
echo [信息] 检测依赖...
python -c "import fastapi" 2>nul
if !errorlevel! neq 0 (
    echo [信息] 首次运行，正在安装依赖...
    pip install -r requirements.txt
    if !errorlevel! neq 0 (
        echo [错误] 依赖安装失败，请手动执行: pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo [信息] 依赖安装完成
)

echo [信息] 正在启动 DeepSeek Code Agent, 请稍候...
echo.

python main_tray.py

pause
