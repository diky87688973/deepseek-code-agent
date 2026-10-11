@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

title DeepSeek 本地模型网关 (model_gateway)

echo.
echo === DeepSeek 本地模型网关启动 ===
echo.

:: ── 检查是否在项目根目录 ──
if not exist "model_gateway.py" (
    echo [错误] 请在项目根目录下运行此脚本
    echo.       未找到 model_gateway.py
    echo.
    pause
    exit /b 1
)

if not exist "config.ini" (
    echo [错误] 未找到 config.ini，无法读取端口与密钥
    echo.
    pause
    exit /b 1
)

:: ── 检查 python ──
python --version >nul 2>nul
if !errorlevel! neq 0 (
    echo [错误] 找不到 python 命令，请先安装 Python 并加入 PATH
    echo.
    pause
    exit /b 1
)

:: ── 自动检测并激活虚拟环境 ──
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo [信息] 已激活虚拟环境: venv
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    echo [信息] 已激活虚拟环境: .venv
)

:: ── 依赖检查（用 python -m pip 保证装到同一个解释器）──
python -c "import flask, waitress" >nul 2>nul
if !errorlevel! neq 0 (
    echo [信息] 缺少 flask / waitress，正在安装...
    python -m pip install flask waitress
    if !errorlevel! neq 0 (
        echo [错误] 依赖安装失败，请手动执行: python -m pip install flask waitress
        echo.
        pause
        exit /b 1
    )
)

:: ── 从 config.ini 读取端口 / 密钥 / 默认模型（与 model_gateway.py 一致）──
set PORT=18802
set AKEY=
set DSDEFAULT=
for /f "usebackq tokens=1,* delims==" %%a in (`python -c "import configparser as p;c=p.ConfigParser();c.read('config.ini',encoding='utf-8');print('PORT='+c.get('model_gateway','port',fallback='18802'));print('AKEY='+c.get('model_gateway','api_key',fallback=''));print('DSDEFAULT='+c.get('model_reasoning','default_model',fallback=''))" 2^>nul`) do set "%%a=%%b"

:: ── 端口占用检查：已在监听则直接提示，避免 WinError 10048 ──
set BUSY=
for /f "usebackq delims=" %%x in (`netstat -ano ^| findstr /r /c:"LISTENING" ^| findstr /c:":!PORT! "`) do set BUSY=1
if defined BUSY (
    echo.
    echo [提示] 端口 !PORT! 已在监听，代理可能已经在运行了
    echo.
        echo        浏览器直接打开会显示 401，属正常（该接口需要 API Key）
    echo        如需重启，请先关闭已有的代理窗口
    echo.
    pause
    exit /b 0
)

:: ── 打印客户端要填的信息 ──
echo.
echo ------------------------------------------------------------
echo   其他 agent 客户端按下面填写：
echo.
echo     Base URL : http://127.0.0.1:!PORT!/v1
echo     API Key  : !AKEY!
if defined DSDEFAULT echo     模型名   : !DSDEFAULT!
echo.
echo   关闭本窗口即停止网关。
echo ------------------------------------------------------------
echo.

python model_gateway.py

echo.
echo [信息] 代理已退出
echo.
pause
