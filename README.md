# DeepSeek Code Agent v1.5

<p align="center">
  <strong>🤖 基于 WEB 浏览器的 AI 桌面助手</strong>
  <br>
  内嵌丰富工具，通过对话完成文件编辑、代码诊断、多 Agent 协同、TTS 语音朗读等复杂任务。
</p>

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/版本-v1.5-blue" alt="版本"></a>
  <a href="#"><img src="https://img.shields.io/badge/Python-3.8+-brightgreen" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/许可证-Apache%202.0-green" alt="许可证"></a>
  <a href="#"><img src="https://img.shields.io/badge/模型-DeepSeek%20v4-orange" alt="模型"></a>
  <a href="#"><img src="https://img.shields.io/badge/语音-edge--tts%20%7C%20控制台-9cf" alt="TTS"></a>
</p>

---

## ✨ 核心特性

- **🧠 多 Agent 协同**：可创建多个 AI 助手分工协作，互发消息接力执行，形成高效协作流水线
- **📊 上下文分层管理**：对话、工具、摘要等分区管理，界面实时可视化各层监控，完全掌控 token 用量分布
- **⚡ KV 缓存优化**：充分利用 DeepSeek prefix caching，有效降低使用成本
- **🔒 加密存储**：对话数据加密保存，保障隐私安全
- **🛡️ ACL 防篡改**：防止项目文件被意外篡改
- **🔊 TTS 语音朗读**：AI 回答实时语音播报，支持 edge-tts（免费、高音质、多音色），支持每会话独立开关

---

## 🖼️ 界面截图

![主界面](res/img/界面预览.png)

![知识库](res/img/知识库.png)

![浅色主题](res/img/白天模式风格.png)

![沉浸模式 · 多会话分栏](res/img/沉浸模式_多会话分栏.png)

![输入区 · 上下文容量条](res/img/上下文容量视图.png)

---

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 如需语音功能（可选）
pip install edge-tts

# 3. 配置 config.ini（填写 API Key 和端口）
#    语音默认关闭，在 [tts] 节将 enabled = false 改为 true 即可默认开启

# 4. 启动
#    Windows：双击 start.bat
#    Linux：  chmod +x start.sh && ./start.sh
#    或直接： python deepseek_code_agent3.py
```

启动后浏览器访问 `http://127.0.0.1:8801` 打开主界面；多列沉浸布局访问 `http://127.0.0.1:8801/immersive`。

**大重构 / 发版前回归**：只说「按文档回归」→ Agent 读 [`回归测试方案.md`](回归测试方案.md) 后 **自动执行** `python scripts/run_layer0.py`（含 `check_agent_v3_health` 等全套 Layer 0）。

---

## 📖 使用方式

### 💬 基础对话

直接在输入框输入问题，AI 会调用工具完成任务：

> "读取桌面的 test.txt 文件内容"
> "帮我查一下 8.8.8.8 的 IP 归属地"
> "当前目录有哪些文件？"

### 🔄 模式切换

| 命令 | 模式 | 用途 |
|------|------|------|
| 输入 `/plan` | Plan 模式 | 先输出方案，**不执行任何写操作**，适合复杂任务先审后做 |
| 输入 `/execute` | Execute 模式 | 严格按照已创建的 Todo-List 执行，每步完成后自动勾选 |
| 默认（不输入命令） | Auto 模式 | AI 自主评估→出方案→执行，适合日常对话 |

也可点击界面顶部的 Auto / Plan / Execute 按钮切换。

### 📎 引用本地文件（@）

在输入框中用 `@` 后跟文件路径，AI 会自动读取文件内容：

> `@C:\project\main.py 帮我分析这个文件的代码质量`
> `@D:\config.json 检查配置是否正确`

路径支持绝对路径和相对路径，也可以点击输入框旁的 📁 按钮打开文件浏览器选择。

### 📚 知识库

将常用文档放入知识库目录，对话时勾选即可让 AI 参考：

1. 在 `config.ini` 的 `[knowledge_base]` 节中配置 `dir`（如 `D:/AI_DATA_ROOT/knowledge_base`）
2. 往该目录放入待参考的文件（如 `.md`、`.txt`、`.py`、`.json`、表格类等）
3. 在界面右侧「📚 知识库」面板勾选本次对话需要的文件
4. AI 自动将可读内容作为参考上下文

### 🔊 语音朗读

> 需要先 `pip install edge-tts`

- 顶栏 🔊/🔇 按钮控制语音开关
- 支持每会话独立开关
- 支持 6 种中英文音色（晓晓、晓伊、云健、云希、云扬、晓辰）
- Markdown 格式符号自动过滤，朗读不跳字符
- 默认关闭，可在 `config.ini` 的 `[tts]` 节中设置 `enabled = true` 改为默认开启

### 📋 Todo-List（执行清单）

在 Plan 模式下，AI 会自动创建 Todo-List。你也可以手动要求：

> "帮我列出今天要做的事情清单"

清单会在界面右侧「📋 Todo List」面板显示，每完成一项自动勾选。

### 📂 文件浏览

点击输入框旁的 📁 按钮打开文件浏览器，可以：

- 浏览本地文件系统
- 选择文件后自动插入 `@路径` 到输入框
- 支持 Windows 盘符列表

### 🛑 停止生成

如果 AI 正在生成回答或调用工具，点击输入框旁的停止按钮即可中断，不会影响已有对话历史。

---

## 📁 目录结构

```text
deepseek-code-agent/
├── deepseek_code_agent3.py     # 当前入口（FastAPI → agent_v3）
├── agent_v3/                   # 宿主：HTTP、core 子模块、live_state
├── scripts/run_layer0.py       # 回归 Layer 0 一键门禁
├── 回归测试方案.md             # 发版/重构后回归（Layer 0～2）
├── config.ini                  # 配置文件
├── requirements.txt            # Python 依赖
├── start.bat / start.sh        # 一键启动脚本
├── README.md                   # 本文件
├── 版本日志.md                 # 版本演进历史
├── 里程碑计划.md               # 版本演进与路线图
│
├── res/
│   ├── html/                   # UI 模板
│   ├── css/                    # 样式表
│   ├── js/                     # 前端 JS
│   ├── img/                    # 截图资源
│
├── tools/                      # 内置工具（catalog 38 个 function）
│   ├── read_file.py / write_file.py
│   ├── grep_files.py / glob_files.py
│   ├── run_command.py / python_inline.py
│   ├── kling_generate.py       # 可灵 AI 视频生成
│   └── ...
│
└── util/                       # 核心模块
    ├── tts/                    # TTS 语音合成模块
    ├── config_loader.py
    ├── agent_model_dispatch.py
    └── ...
```

---

## ⚙️ 配置说明

```ini
[model]
api_key = sk-你的API密钥

[server]
port = 8801

[tts]
engine = edge          # console=静默 / edge=语音
voice = zh-CN-XiaoxiaoNeural
enabled = false        # 默认关闭，改为 true 开机自动语音
```

完整配置说明见 `config.ini`。

---

## 🌐 API 接口

| 端点 | 方法 | 用途 |
|------|------|------|
| `/` | GET | 主页面 |
| `/immersive` | GET | 沉浸模式页面 |
| `/api/chat/send` | POST | 提交用户消息 |
| `/api/chat/stop` | POST | 停止当前生成 |
| `/api/chat/title` | POST | 自动生成会话标题 |
| `/api/chat/user-confirm` | POST | 提交用户确认 |
| `/api/chat/history` | GET | 获取对话历史 |
| `/api/chat/sessions` | GET | 获取会话列表 |
| `/api/chat/ui-state` | GET/PUT | 读写界面布局状态 |
| `/api/events/stream` | GET | 全局 SSE 事件流 |
| `/api/kb/files` | GET | 列出知识库文件 |
| `/api/kb/checked` | GET/PUT | 读写知识库勾选状态 |
| `/api/model-pricing` | GET | 查询模型定价 |
| `/api/reasoning-effort` | GET/PUT | 读写推理档位 |
| `/api/usage-accumulator` | GET/PUT | 用量统计 |
| `/api/dir-browse` | GET | 浏览文件目录 |
| `/api/tts/state` | PUT | 设置会话 TTS 开关 |
| `/health` | GET | 健康检查 |

---

## 🤝 参与贡献

欢迎提交 Issue 和 PR！可以做的事：

- 🐛 报告 Bug
- 💡 提出新功能建议
- 📝 完善文档
- 🔧 提交代码改进

---

## 📜 许可证

[Apache 2.0](LICENSE)

---

> **⭐ 如果这个项目对你有帮助，欢迎给个 Star！**
>
> 文档版本：v1.5  |  更新于：2026-07-11  |  作者：Fan
