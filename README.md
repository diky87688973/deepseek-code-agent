# Code Web Agent v1 使用指南

> 一款基于 WEB 浏览器的 AI 助手，内嵌 20+ 工具，支持文件操作、代码诊断、Git 查询、Web 抓取等，通过对话完成复杂任务。

---

## 快速开始（30 秒）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 config.json（填写 DeepSeek API Key）
#    见下方「配置说明」

# 3. 启动
#    Windows：双击 start.bat
#    Linux：  chmod +x start.sh && ./start.sh
```

启动后浏览器访问 `http://127.0.0.1:8808` 即可开始对话。

---

## 目录结构

```
code-web-agent/
├── code_web_agent.py           # 服务端主程序
├── main_tray.py                # 系统托盘启动器（Windows 托盘图标）
├── config.json                 # 配置文件（API Key / 端口 / 路径）
├── requirements.txt            # Python 依赖
├── start.bat                   # Windows 一键启动
├── start.sh                    # Linux/macOS 一键启动
├──
├── tools/                      # 20+ 内置工具（文件读写 / Git / 诊断等）
│   ├── cli_structured_edit.py  #   文本编辑
│   ├── cli_python_inline.py    #   Python 胶水脚本
│   ├── cli_directory_list.py   #   目录浏览
│   ├── cli_web_fetch.py        #   网页抓取
│   ├── cli_git_workspace.py    #   Git 查询
│   ├── cli_regex_locate.py     #   正则定位
│   ├── ... （共 20+ 个）
│   └── tool_list_cli.json      # 工具清单定义
│
├── util/                       # 核心模块
│   ├── config_loader.py        #   配置加载器
│   ├── agent_prompt_constants.py  # 系统提示词
│   ├── agent_model_dispatch.py #   模型调度
│   ├── agent_openai_compatible_client.py # API 客户端
│   └── agent_deepseek_pricing.py  # 计费查询
│
├── code-web-agent-ui.html      # 前端界面（含 CSS/JS）
├── code-web-agent-ui.css
├── code-web-agent-ui.js
├── app_icon.ico / .png         # 托盘图标
└── model_usage_accumulator.json # 用量统计
```

---

## 配置说明

### config.json

项目根目录下的 `config.json` 是唯一必须配置的文件：

```json
{
    "CODE_WEB_AGENT_CHAT_API_BASE_URL": "https://api.deepseek.com",
    "CODE_WEB_AGENT_CHAT_API_KEY": "sk-你的API密钥",
    "CODE_WEB_AGENT_SERVER_PORT": 8808,
    "CODE_WEB_AGENT_WORKSPACE_DIR": "D:/workspace",
    "CODE_WEB_AGENT_DATA_DIR": "D:/AI_DATA_ROOT",
    "KNOWLEDGE_BASE_DIR": "D:/AI_DATA_ROOT/knowledge_base"
}
```

| 配置项 | 说明 | 是否必填 |
|--------|------|---------|
| `CODE_WEB_AGENT_CHAT_API_KEY` | API 密钥 | **必填** |
| `CODE_WEB_AGENT_SERVER_PORT` | 服务端口 | **必填** |
| `CODE_WEB_AGENT_CHAT_API_BASE_URL` | API 地址，默认 DeepSeek | 可选 |
| `CODE_WEB_AGENT_WORKSPACE_DIR` | 默认工作目录 | 可选，默认桌面 |
| `CODE_WEB_AGENT_DATA_DIR` | 数据存储目录 | 可选，默认 `~/AI_DATA_ROOT` |
| `KNOWLEDGE_BASE_DIR` | 知识库目录 | 可选 |

> 配置优先级：`config.json` > 环境变量 > 代码默认值。
> 也可通过环境变量 `CHAT_API_KEY`、`PORT` 快速配置。

---

## 启动方式

### Windows

| 方式 | 操作 |
|------|------|
| **一键启动（推荐）** | 双击 `start.bat` |
| 命令行启动 | `python main_tray.py` |
| 纯 Web 服务（无托盘） | `python code_web_agent.py` |

`start.bat` 会自动检测虚拟环境、安装依赖，启动后在系统托盘显示图标。

### Linux / macOS

```bash
# 首次使用需授权
chmod +x start.sh

# 启动
./start.sh
```

> 注意：托盘图标仅 Windows 支持，Linux/macOS 会直接启动 Web 服务。

### 验证是否启动成功

浏览器打开 `http://127.0.0.1:8808`，看到 Dark 主题的聊天界面即成功。

---

## 界面功能速览

| 区域 | 功能 |
|------|------|
| 💬 对话区 | 发送消息，AI 流式回复 |
| 📋 Todo List 面板 | 显示/跟踪执行清单进度 |
| 📂 步骤侧栏 | 每次工具调用的入参、结果、预览 |
| 📚 知识库面板 | 勾选本地文件注入到对话上下文 |
| 📁 文件浏览器 | 浏览文件系统，@引用文件 |
| 🔄 模式切换 | Auto / Plan / Execute 三种模式 |
| 📊 用量统计 | Token 消耗与费用统计 |
| 📑 多标签页 | 同时管理多个会话 |

---

## 三种执行模式

| 模式 | 适用场景 | 行为 |
|------|---------|------|
| **Auto** | 日常对话、一次性任务 | AI 自主评估→出方案→执行 |
| **Plan** | 复杂任务，先审方案再执行 | 仅输出方案 + 创建 Todo-List，**不执行写操作** |
| **Execute** | 按已确认的方案执行 | 严格按照 Todo-List 逐步执行，每步完成后勾选 |

切换方式：界面顶部按钮 / 输入 `/plan`、`/execute` 命令。

---

## 核心特性

### 知识库（RAG）

将本地文件内容注入到对话中，让 AI 了解你的项目代码或文档：

1. 在 `config.json` 中配置 `KNOWLEDGE_BASE_DIR`
2. 往该目录放入 `.md`、`.txt`、`.py`、`.json` 等文本文件
3. 在界面右侧「知识库」面板勾选需要的文件
4. AI 自动将文件内容作为参考上下文

> 支持 40+ 种文件格式，单文件限制 100KB。

### 长对话记忆

AI 自动管理上下文窗口：
- 保留最近 **60 轮**完整对话
- 超过 **800 条**消息时自动触发 AI 摘要压缩
- 历史摘要以 `【历史摘要】` 形式注入，不丢失关键信息

### KV 缓存优化（降成本）

充分利用 DeepSeek 等模型的 prefix caching 机制：
- 系统提示词和工具定义保持稳定，提高缓存命中率
- 流式响应中携带 `usage` 信息（含 cache_hit_tokens）
- 费用统计区分缓存命中/未命中价格

### 20+ 内置工具

| 类别 | 工具 |
|------|------|
| 📝 文件编辑 | 文本读写、搜索替换、正则定位、diff、patch |
| 📂 文件管理 | 复制/移动/删除/重命名、目录浏览 |
| 💻 代码诊断 | Python 语法检查、pytest 测试 |
| 🌐 网络 | 网页抓取、IP 定位、天气查询 |
| 🔧 开发 | Git 日志/diff/blame、环境探测 |
| 🧩 编排 | 多步骤任务编排、用户确认分岔 |

### 对话加密存储

所有对话记录自动加密保存到 `DATA_ROOT/cache/sessions/`：
- Windows 下使用系统级加密
- Linux/macOS 使用本地密钥加密
- 重启服务后自动恢复历史对话

---

## API 接口一览

项目同时提供 REST API（适合二次开发）：

| 端点 | 用途 |
|------|------|
| `POST /api/chat/stream` | 发送消息（SSE 流式） |
| `POST /api/chat/stop` | 停止当前生成 |
| `GET /api/chat/history` | 获取对话历史 |
| `GET /api/chat/sessions` | 获取所有会话列表 |
| `GET /api/kb/files` | 列出知识库文件 |
| `GET /api/kb/checked` | 查看当前勾选的知识库文件 |
| `PUT /api/kb/checked` | 保存勾选状态 |
| `GET /api/model-pricing` | 查询模型费用 |
| `GET /api/usage-accumulator` | 查看用量统计 |
| `GET /api/dir-browse` | 浏览文件目录 |
| `GET /health` | 健康检查 |

---

## 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 启动报错 `PORT 未设置` | config.json 缺少端口 | 配置 `CODE_WEB_AGENT_SERVER_PORT` |
| 页面打不开 | 服务未启动或端口被占用 | 检查控制台日志，确认端口未被占用 |
| API 返回 502 | API Key 无效或网络不通 | 检查 `CHAT_API_KEY` 和网络连接 |
| Linux 下 `./start.sh` 无法运行 | 文件无执行权限 | 执行 `chmod +x start.sh` |
| 模型不调用工具 | 模型不支持 function calling | 切换到 DeepSeek-v4 系列 |
| 对话记录消失了 | 加密密钥文件被删除 | 不要删除 `cache/session_encryption.key` |

---

## 调试与环境变量

| 变量名 | 默认 | 说明 |
|--------|------|------|
| `CODE_WEB_AGENT_CONSOLE_LOG` | `1` | 控制台输出调试日志 |
| `CODE_WEB_AGENT_TOOL_DEBUG` | `1` | 工具调用失败时写入 debug 日志 |
| `CHAT_API_MODELS` | `deepseek-v4-pro,deepseek-v4-flash` | 允许使用的模型列表 |
| `CHAT_API_EXTRA_HEADERS_JSON` | — | 自定义 HTTP 请求头 |
| `CHAT_COMPLETIONS_PATH` | `/v1/chat/completions` | 自定义 API 路径 |

---

> 文档版本：v1  |  更新于：2026 年  |  作者：付凡
