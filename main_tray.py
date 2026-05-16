#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek Code Agent - 系统托盘启动器

功能：
 - 后台启动 FastAPI + uvicorn Web 服务（端口 8801）
 - 在 Windows 任务栏通知区显示托盘图标
 - 右键菜单：「打开界面」（打开浏览器）/「退出服务」（关闭）
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

# ── 单实例端口锁（防止重复启动）──
# 通过占用 18801 端口来标记实例已在运行
_LOCK_PORT = 18801
_lock_socket = None

def _lock_single_instance():
    """尝试绑定锁端口，失败则表示已有实例在运行"""
    global _lock_socket
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _lock_socket.bind(("127.0.0.1", _LOCK_PORT))
        _lock_socket.listen(1)
        return  # 绑定成功，本实例持有锁
    except OSError:
        # 端口已被占用，说明已有实例
        _show_message("DeepSeek Code Agent 已在运行中\n请在系统托盘中查找图标")
        sys.exit(0)

def _show_message(text: str):
    """弹出提示框（仅 Windows）"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "提示", 0x40 | 0x0)
    except Exception:
        pass

_lock_single_instance()

# ── 最先加载配置（覆盖环境变量）──
from util.config_loader import load_config
_AGENT_CONFIG = load_config(verbose=True)

# ── 路径兼容：源码 / PyInstaller 打包后 ──
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys._MEIPASS)          # 只读代码目录
else:
    BASE_DIR = Path(__file__).resolve().parent

os.chdir(str(BASE_DIR))

sys.path.insert(0, str(BASE_DIR))

# ── DATA_ROOT：可写数据目录，从 AGENT_CONFIG 读取 ──
_dr = str(_AGENT_CONFIG.get("AGENT_DATA_ROOT_DIR") or "").strip()
if len(_dr) >= 2 and _dr[0] == _dr[-1] and _dr[0] in ("'", '"'):
    _dr = _dr[1:-1].strip()
if not _dr:
    print("FATAL: AGENT_DATA_ROOT_DIR 未设置！请在 config.ini 的 [workspace] 节配置 data_root", flush=True)
    sys.exit(1)
DATA_ROOT = Path(_dr).expanduser().resolve()
# 向环境变量写入回收站路径（供 file_ops 等工具读取 AGENT_RECYCLE_ROOT）
os.environ.setdefault("AGENT_RECYCLE_ROOT", str(DATA_ROOT / "AI_安全删除回收站"))

HOST = str(_AGENT_CONFIG["AGENT_SERVER_HOST"]).strip()
port_str = str(_AGENT_CONFIG["AGENT_SERVER_PORT"]).strip()
if not port_str:
    print("FATAL: AGENT_SERVER_PORT 未设置！请在 config.ini 的 [server] 节配置 port", flush=True)
    sys.exit(1)
PORT = int(port_str)
SERVER_URL = f"http://{HOST}:{PORT}"

uvicorn_server = None
shutdown_event = threading.Event()

# ── UNLOCK_CODE_UPDATE：是否跳过 ACL 锁定（更新时用）──
_unlock_update = str(_AGENT_CONFIG.get("UNLOCK_CODE_UPDATE") or "").strip().lower() in ("true", "1", "yes")


# ── ACL 安全锁 ──
def _lock_agent_root():
    """将 AGENT_ROOT 设为 Everyone 只读（含子目录），拦截所有写入。"""
    if _unlock_update:
        _log("UNLOCK_CODE_UPDATE=true，跳过 ACL 锁定")
        return
    target = str(BASE_DIR)
    # 先清理上次异常退出可能残留的只读锁，再重新加锁
    try:
        import subprocess
        unlock_cmd = ["icacls", target, "/inheritance:e", "/grant", "Everyone:(OI)(CI)F"]
        subprocess.run(unlock_cmd, capture_output=True, text=True, timeout=30)
    except Exception:
        pass
    _log(f"正在锁定 AGENT_ROOT: {target}")
    try:
        import subprocess
        cmd = ["icacls", target, "/inheritance:r", "/grant", "Everyone:(OI)(CI)R"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            _log(f"AGENT_ROOT 已锁定为只读")
        else:
            _log(f"icacls 锁定失败(可能非管理员): {r.stderr.strip()}")
    except Exception as e:
        _log(f"ACL 锁定异常: {e}")


def _unlock_agent_root():
    """退出时恢复 AGENT_ROOT 为完全控制（便于下次更新）。"""
    if _unlock_update:
        return
    target = str(BASE_DIR)
    _log(f"正在解锁 AGENT_ROOT: {target}")
    try:
        import subprocess
        cmd = ["icacls", target, "/inheritance:e", "/grant", "Everyone:(OI)(CI)F"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            _log(f"AGENT_ROOT 已解锁")
        else:
            _log(f"icacls 解锁失败: {r.stderr.strip()}")
    except Exception as e:
        _log(f"ACL 解锁异常: {e}")


# ── 远程版本检测 ──
def _check_remote_version():
    """检查远程是否有新版本（仅日志记录，不阻塞启动）。"""
    try:
        import urllib.request
        import json
        url = "https://api.github.com/repos/Fan/DeepSeekCodeAgent/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "DeepSeekCodeAgent"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            remote_ver = data.get("tag_name", "") or data.get("name", "")
            if remote_ver:
                _log(f"远程最新版本: {remote_ver}")
    except Exception as e:
        _log(f"版本检测失败: {e}")


def _log(msg: str):
    """追加日志到 DATA_ROOT/temp/server.log"""
    import datetime
    try:
        log_dir = DATA_ROOT / "temp"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(str(log_dir / "server.log"), "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def start_server():
    global uvicorn_server
    _log("正在启动 uvicorn 服务器...")
    import uvicorn
    from deepseek_code_agent import app
    try:
        # log_config=None 禁用 uvicorn 默认日志配置（兼容 --noconsole 无控制台环境）
        config = uvicorn.Config(app, host=HOST, port=PORT, log_config=None, reload=False)
        uvicorn_server = uvicorn.Server(config)
        _log(f"uvicorn 配置完成: {HOST}:{PORT}")
        uvicorn_server.run()
    except Exception as e:
        _log(f"服务器启动失败: {e}")
        print(f"[main_tray] Server error: {e}", file=sys.stderr, flush=True)


def open_browser():
    webbrowser.open(SERVER_URL)


def stop_server():
    global uvicorn_server
    if uvicorn_server:
        uvicorn_server.should_exit = True
        uvicorn_server.force_exit = True


def create_tray_icon():
    import pystray
    from PIL import Image

    # 加载托盘图标：优先用大图（自动缩放），回退到小图，再无则画简易图标
    icon_128 = BASE_DIR / "res" / "img" / "app_icon_128x128.png"
    icon_16 = BASE_DIR / "res" / "img" / "app_icon_16x16.png"
    if icon_128.exists():
        img = Image.open(icon_128)
    elif icon_16.exists():
        img = Image.open(icon_16)
    else:
        # 回退：简易图标
        from PIL import ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([(4, 4), (60, 60)], radius=12, fill=(41, 128, 185, 255))
        draw.text((14, 10), "C", fill=(255, 255, 255, 255))

    menu = pystray.Menu(
        pystray.MenuItem("\U0001f310 \u6253\u5f00\u754c\u9762", lambda: open_browser()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("\u274c \u9000\u51fa\u670d\u52a1", on_exit),
    )

    icon = pystray.Icon("deepseek_code_agent", img, "DeepSeek Code Agent v1\n\u540e\u53f0 AI \u670d\u52a1\u8fd0\u884c\u4e2d", menu)
    return icon


def _clean_temp_dir():
    """退出时清理 temp 目录下的所有内容（逻辑删除：移入回收站）"""
    temp_dir = DATA_ROOT / "temp"
    recycle_dir = DATA_ROOT / "AI_安全删除回收站"
    if not temp_dir.exists():
        return
    try:
        import shutil
        ts = __import__('datetime').datetime.now().strftime('%Y%m%dT%H%M%S')
        bucket = recycle_dir / f"temp_cleanup_{ts}"
        shutil.move(str(temp_dir), str(bucket))
        temp_dir.mkdir(parents=True, exist_ok=True)  # 重建空 temp 目录
        _log(f"temp 目录已清理，移至: {bucket}")
    except Exception as e:
        _log(f"temp 清理失败: {e}")


def on_exit(icon, item=None):
    stop_server()
    _clean_temp_dir()
    _unlock_agent_root()  # 退出时解锁 AGENT_ROOT
    shutdown_event.set()
    icon.stop()


def main():
    import time
    _log(f"启动 DeepSeek Code Agent v1")
    _log(f"服务器地址: {SERVER_URL}")
    _log(f"AGENT_ROOT: {BASE_DIR}")
    _log(f"DATA_ROOT: {DATA_ROOT}")
    _log(f"UNLOCK_CODE_UPDATE: {_unlock_update}")
    _log(f"config.ini 存在: {(BASE_DIR / 'config.ini').exists()}")
    _log(f"PORT env: {os.environ.get('PORT') or '未设置'}")
    _log(f"CHAT_API_BASE_URL: {os.environ.get('CHAT_API_BASE_URL') or '未设置'}")
    print(f"[main_tray] 正在启动 DeepSeek Code Agent v1...", flush=True)
    print(f"[main_tray] 服务器地址: {SERVER_URL}", flush=True)

    # ── 1. 远程版本检测（不阻塞启动）──
    _check_remote_version()

    # ── 2. 锁定 AGENT_ROOT（除非 UNLOCK_CODE_UPDATE=true）──
    _lock_agent_root()

    # ── 3. 确保运行时数据目录结构存在 ──
    _dirs = [
        "cache/sessions",
        "cache/excerpts",
        "temp",
        "AI_安全删除回收站",
    ]
    for _sub in _dirs:
        (DATA_ROOT / _sub).mkdir(parents=True, exist_ok=True)
    _log(f"DATA_ROOT: {DATA_ROOT}")
    print(f"[main_tray] 数据目录: {DATA_ROOT}", flush=True)

    # ── 4. 启动服务器 ──
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    time.sleep(3)
    _log(f"服务器线程已启动")

    # ── 5. 创建托盘图标 ──
    tray_icon = create_tray_icon()
    _log(f"托盘图标已创建")
    print(f"[main_tray] 托盘图标已加载，右键可打开界面或退出", flush=True)
    tray_icon.run()

    # ── 退出 ──
    _unlock_agent_root()  # 保底解锁
    _log(f"服务已停止")
    print(f"[main_tray] 服务已停止", flush=True)


if __name__ == "__main__":
    main()
