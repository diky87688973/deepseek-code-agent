# OpenCLI 浏览器操作工具安装指南

OpenCLI 是一个基于 Chrome DevTools Protocol (CDP) 的命令行工具，让 AI Agent 能操作已登录的浏览器，利用 SSO 登录态完成内网系统自动化任务。

## 前置条件

| 组件 | 版本要求 |
|------|---------|
| Node.js | ≥ 18（推荐 24.x） |
| npm | 随 Node.js 自带 |
| Chrome 或 Edge | 最新版 |

---

## 安装步骤

### 1. 安装 Node.js

访问 https://nodejs.org 下载 LTS 版本，或通过包管理器安装：

```bash
# Windows (winget)
winget install OpenJS.NodeJS.LTS

# macOS (Homebrew)
brew install node

# Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt-get install -y nodejs
```

验证安装：

```bash
node --version
npm --version
```

### 2. 安装 OpenCLI CLI

```bash
npm install -g @jackwener/opencli
```

验证安装：

```bash
opencli --version
opencli help
```
> ⚠️ **Agent 注意**：
>
> **安装后 PATH 检测**：如果 `opencli` 命令找不到，不要重新安装，先执行：
>
> ```python
> import os, winreg
> k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Environment', 0, winreg.KEY_READ | winreg.KEY_WRITE)
> cur, _ = winreg.QueryValueEx(k, 'PATH')
> node_path = r'C:\Program Files\nodejs'
> if node_path not in cur:
>     winreg.SetValueEx(k, 'PATH', 0, winreg.REG_EXPAND_SZ, cur.rstrip(';') + ';' + node_path)
> winreg.CloseKey(k)
> ```
>
> 修复后告知用户**重新打开终端**即可生效。之后 `opencli` 可在任何新终端中直接使用。
>
> **卸载时**：从用户 PATH 中删除 `C:\Program Files\nodejs` 和 `%APPDATA%\npm` 两项。
### 3. 安装浏览器扩展

**Chrome 用户**：打开 Chrome 浏览器，访问 Chrome Web Store，搜索 **OpenCLI Extension**，安装 v1.0.17 或最新版。

**Edge 用户**：使用本目录下已修复 Edge 兼容性的打包文件：

1. 打开 Edge 浏览器，访问 `edge://extensions/`
2. 开启「开发人员模式」（右上角开关）
3. 将 `docs/chrome_plugin/opencli/opencli-extension-v1.0.17-edge.zip` 解压到本地目录
4. 点击「加载解压缩的扩展」，选择解压后的目录

> **注意**：Edge 兼容性修复包括 CDP Promise 垫片、Service Worker 注册调整等 8 处修改，详见扩展源码注释。扩展是 Agent 与浏览器通信的桥梁，必须保持启用状态。
### 4. 部署技能文件（可选，但推荐）

技能文件告诉 AI Agent 如何正确调用 OpenCLI。让 Agent 执行以下命令完成部署：

```
skill_manage(action="copy", source="docs/chrome_plugin/opencli/skills")
```

> Agent 会自动将文件复制到配置的 skills 目录并重新扫描，无需手动指定路径。

技能文件列表：

| 文件 | 用途 |
|------|------|
| `opencli-usage.md` | 总览，了解可执行的所有操作 |
| `opencli-browser.md` | 浏览器操作指南（核心） |
| `opencli-adapter-author.md` | 编写新站点适配器 |
| `opencli-autofix.md` | 命令失败时自动修复 |

---

## 验证安装

执行以下命令确认 OpenCLI 能正常与浏览器通信：
```bash
opencli doctor
```


如果输出正常，说明安装完成。

---

## 浏览器操作原则

Agent 操作浏览器时遵循以下约定：

- `opencli open` 始终创建**新窗口**（隔离容器），不干扰用户主窗口
- `opencli bind` 后可操作现有标签页，但禁止 `tab new/select/close`
- 凡涉及浏览器操作的需求，Agent 优先加载 `opencli-browser` 技能后执行

---

## 常见问题

**Q: 扩展安装后浏览器工具栏没有图标？**
A: 检查扩展是否已启用，或重新安装扩展。

**Q: `opencli bind` 提示找不到标签页？**
A: 确保浏览器已打开至少一个标签页，且扩展已启用。

**Q: 技能文件应该放在什么位置？**
A: 放入 `{DATA_ROOT}/skills/opencli/` 目录，其中 `DATA_ROOT` 是当前会话的 AI 数据根目录。
