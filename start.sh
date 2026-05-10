#!/usr/bin/env bash
# DeepSeek Code Agent v1 - Linux/macOS 快速启动脚本

set -e

APP_NAME="DeepSeek Code Agent v1"

cat <<'EOF'

 ╔══════════════════════════════════════╗
 ║   DeepSeek Code Agent v1  快速启动   ║
 ╚══════════════════════════════════════╝

EOF

# 检查是否在项目根目录
if [ ! -f "main_tray.py" ]; then
    echo "[错误] 请在项目根目录下运行此脚本"
    echo "       未找到 main_tray.py"
    exit 1
fi

# 自动检测并激活虚拟环境
if [ -d "venv" ]; then
    source venv/bin/activate
    echo "[信息] 已激活虚拟环境: venv"
elif [ -d ".venv" ]; then
    source .venv/bin/activate
    echo "[信息] 已激活虚拟环境: .venv"
else
    echo "[信息] 未找到虚拟环境，使用系统 Python"
fi

# 检查 config.ini（与 util.config_loader 一致）
if [ ! -f "config.ini" ]; then
    echo "[警告] 未找到 config.ini！"
    echo "       请配置 [model] api_key、[server] port 等（参见仓库内 config.ini 示例）"
    echo ""
fi

# 从 config.ini 读取 port 供提示（首条 ^port *=）
PORT_HINT=8801
if [ -f "config.ini" ]; then
    _line="$(grep -E '^[[:space:]]*port[[:space:]]*=' config.ini 2>/dev/null | head -1)"
    if [ -n "$_line" ]; then
        PORT_HINT="$(echo "$_line" | sed -e 's/^[^=]*=[[:space:]]*//' -e 's/[[:space:]]*$//')"
    fi
fi

# 检查依赖是否已安装
echo "[信息] 检测依赖..."
python3 -c "import fastapi" 2>/dev/null || python -c "import fastapi" 2>/dev/null || {
    echo "[信息] 首次运行，正在安装依赖..."
    pip3 install -r requirements.txt -q 2>/dev/null || pip install -r requirements.txt -q || {
        echo "[错误] 依赖安装失败，请手动执行: pip install -r requirements.txt"
        exit 1
    }
    echo "[信息] 依赖安装完成"
}

# 检测 python 命令
PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON_CMD="$cmd"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "[错误] 未找到 Python，请先安装 Python 3.8+"
    exit 1
fi

echo "[信息] 正在启动 $APP_NAME ..."
echo "[信息] 托盘功能仅 Windows 支持，Linux/macOS 将直接启动 Web 服务"
echo "[信息] 服务启动后请访问 http://127.0.0.1:${PORT_HINT}"
echo "[信息] 按 Ctrl+C 即可停止服务"
echo ""

$PYTHON_CMD main_tray.py
