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

:: ── 从 config.json 读取端口 ──
for /f "tokens=2 delims=:," %%a in ('findstr /i "AGENT_SERVER_PORT" config.json 2^>nul') do (
    set PORT=%%a
    set PORT=!PORT: =!
)
if not defined PORT set PORT=8802

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

:: 检查 config.json
if not exist "config.json" (
    echo [警告] 未找到 config.json！
    echo.       请配置 API Key 后再启动
    echo.
)

:: 检查依赖是否已安装
echo [信息] 检测依赖...
python -c "import fastapi" 2>nul
if !errorlevel! neq 0 (
    echo [信息] 首次运行，正在安装依赖...
    pip install -r requirements.txt -q
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
