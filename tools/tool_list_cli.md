# 文本处理 CLI 工具列表（供大模型调用）

本文档描述当前可调用的 **10** 个 CLI 工具：`cli_structured_edit.py`、`cli_directory_list.py`、`cli_git_workspace.py`、`cli_unified_diagnose.py`、`cli_regex_locate.py`、`cli_text_diff.py`、`cli_patch_apply.py`、`cli_command_exec.py`、`cli_test_report.py`、`cli_file_ops.py`。

（`工具库/文本工具` 目录下仅上述 10 个 `cli_*.py`，无「实现 + 壳」成对脚本。）

**编排小编排**（`工具库/文本工具/编排/`，子进程调用上述 CLI；**供 agent**：必填命令行参数或 `--request-file`，见各脚本 `--help` 与 `示例_request_行号双锚.json`）：见下文「编排工具」。**编排_agent.py** 可按 JSON **spec**（`--spec-file`）或内置 **preset**（`--preset` + `--params-file`）顺序调度，子进程 stdout/stderr 直通终端；对 `cli_structured_edit` 的 `replace_range` / `replace_literal` 写盘步骤可自动打印 **unified diff** 预览（见 `示例_spec_编排agent.json`）。

**未来改进与长期计划**（语义检索、LSP、mypy 等）：见同目录 `工具库_未来改进.md`。

## 统一区间协议

- **全串字符**（`replace_range`、`extract.mode=offsets`、`delete_segments.masks[]`、`cli_regex_locate` 命中区间）：`0-based` 半开 **`[start, end)`**；`end` 在 offsets 中可 `-1/-2` 倒推。
- **行/列**（`insert`、`extract.mode=lines` / `lines_columns`）：**`startLine` / `endLine` / `startColumn` / `endColumn`**，语义见各 `payload.type`（行级多为 1-based）。
- **Git blame 行范围**（仅 `cli_git_workspace.py --mode blame`）：**`--startLine` / `--endLine`**（1-based 闭区间，须成对）。

---

## 工具 1：cli_structured_edit.py

- **脚本路径**：`D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_structured_edit.py`
- **用途**：文件写入（追加/插入/替换）；从文件或内存文本**提取**片段；按索引区间与/或字面短语**删除**片段（原独立「文本提取」「文本掩码」能力并入本工具）。
- **输出**：写盘类成功时输出 `ok`；提取/删除类无 `outFile` 时把结果文本写到 `stdout`；`--jsonOut` 时统一 `{ok,data,error}`。

### 输入约定

- **写盘类** `payload.type`：`append` / `append_line` / `insert` / `replace_range` / `replace_literal`  
  - 必须提供目标文件：`--file` 或 `request.file`  
  - `payload`：`--payload` / `--payloadFile` / `--payloadStdin` 三选一，或放在 `request.payload`
- **读入类** `payload.type`：`extract` / `delete_segments`  
  - 内容源**必须且只能**选一个：`--file` / `--text` / `--textStdin`（或 `request` 中对应字段；`request.textStdin: true` 表示正文从 stdin 读）
  - 不能与「整份 request 从 stdin 读」共用同一 stdin（需使用 `requestFile` 等避免冲突）

### request 示例（推荐）

```json
{
  "file": "D:/a.txt",
  "encoding": "auto",
  "payload": {
    "type": "extract",
    "mode": "offsets",
    "start": 100,
    "end": 300,
    "outFile": "D:/clip.txt"
  }
}
```

### payload.type 摘要

| type | 作用 |
|------|------|
| append | 文件末尾追加 `text`（文件不存在时自动创建） |
| append_line | 若末尾无换行则补 `\n` 再追加 `text` |
| insert | 在 `[startLine,startColumn]`（1-based）插入 `text` |
| replace_range | 按 `[start,end)` 替换为 `text` |
| replace_literal | `oldText` → `newText`，`count` 默认 -1 为全部 |
| extract | `mode`: `lines`（按行闭区间）、`lines_columns`（行列矩形）、`offsets`（全串半开区间）；可选 `outFile` / `outEncoding`；内容源可用 `--file` / `--text` / `--textStdin` / `--url` |
| delete_segments | `masks` 与/或 `dropPhrases` 至少一项非空；`masks` 为 `[{start,end}]` 半开 0-based、无负索引；可选 `outFile` |

### delete_segments 中 masks 示例

```json
[
  { "start": 0, "end": 10 },
  { "start": 20, "end": 23 }
]
```

### 调用示例

```bash
python "D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_structured_edit.py" --file "D:\a.txt" --payload "{\"type\":\"extract\",\"mode\":\"lines\",\"startLine\":10,\"endLine\":20}"
python "D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_structured_edit.py" --text "第一行\n第二行" --payload "{\"type\":\"extract\",\"mode\":\"lines_columns\",\"startLine\":2,\"startColumn\":1,\"endLine\":2,\"endColumn\":-1}"
python "D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_structured_edit.py" --file "D:\a.txt" --payload "{\"type\":\"delete_segments\",\"dropPhrases\":[\"噪声词\"],\"outFile\":\"D:\\a_clean.txt\"}"
python "D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_structured_edit.py" --file "D:\demo\out.txt" --payload "{\"type\":\"append_line\",\"text\":\"下一行\"}"
```

---

## 工具 2：cli_directory_list.py

- **脚本路径**：`D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_directory_list.py`
- **用途**：目录/文件列表，支持递归与 glob；在 **Git 仓库内**（`--root` 向上能找到 `.git`）且本机有 `git` 时，**默认按 `.gitignore` 排除**被忽略路径（与 `git check-ignore` 一致）；路径中含 `.git` 的条目一律不返回（减噪）。
- **核心参数**：`--root`、`--recursive`、`--glob`、`--type`、`--limit`、`--noGitignore`（显式关闭忽略规则）、`--jsonOut`
- **`--jsonOut` 时 `data` 额外字段**：`respectGitignoreRequested`、`gitRepoRoot`（解析到的仓库根或 `null`）、`gitignoreApplied`、`gitignoreNote`（未应用时的原因说明）

---

## 工具 3：cli_git_workspace.py

- **脚本路径**：`D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_git_workspace.py`
- **用途**：Git **只读**结构化查询，由 `--mode` 选择：
  - **`worktree`（默认）**：`git status --porcelain=v1 -b` + 工作区/暂存区 `git diff`（可 `--maxDiffChars` 截断，`data.diffTruncated`）。
  - **`log`**：最近 `--logMax` 条提交，`data.entries[]` 含 `commit` / `subject` / `author` / `date`。
  - **`blame`**：对 `--blamePath`（相对 `root`）跑 `git blame --line-porcelain`；可选 **`--startLine` / `--endLine`**（1-based 闭区间，须成对）；超大文件需行范围以免超 `BUILTIN` 限制。
  - **`show`**：`--showRef`（默认 `HEAD`）的提交说明、`--stat` 摘要、变更文件列表（`name-status`）与补丁文本（可截断）。
- **核心参数**：`--root`、`--mode`、`--maxDiffChars`、`--logMax`、`--blamePath`、`--startLine`、`--endLine`（blame）、`--showRef`、`--jsonOut`
- **截断可观测字段**：`worktree` 返回 `rawDiffWorktreeChars` / `rawDiffStagedChars` / `effectiveMaxDiffChars`；`show` 返回 `rawPatchChars` / `effectiveMaxDiffChars`。
- **依赖**：本机 `git` 在 PATH 中。

---

## 工具 4：cli_unified_diagnose.py

- **脚本路径**：`D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_unified_diagnose.py`
- **用途**：在 `--root` 下对匹配的 `.py` 做 **`ast` 语法解析**；若本机有 **`ruff`**，再在根目录执行 `ruff check . --output-format=json`，合并为统一的 `data.diagnostics[]`（字段：`file, source, rule, severity, line, column, endLine, endColumn, message`）。
- **顶层 `--jsonOut`**：`{ ok, data, error }`；其中 **`ok` 表示无 `severity=error` 的项**（仅有 `warning` 时仍为 `true`）。
- **核心参数**：`--root`（必填）、`--glob`（默认 `**/*.py`，用于**语法扫描**的文件枚举）、`--limit`、`--encoding`（支持 `auto`）、`--timeoutSec`、`--noRuff`、`--jsonOut`
- **依赖**：`ruff` 可选；未安装时仅语法扫描，`data.notes` 说明原因。

---

## 工具 5：cli_regex_locate.py

- **脚本路径**：`D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_regex_locate.py`
- **用途**：单文件或目录正则检索，返回命中位置；`--rangesOut` 可导出区间 JSON 供 `delete_segments.masks` 使用。
- **核心参数**：`--target`、`--pattern`、`--ignoreCase`、`--multiline`、`--recursive`、`--glob`、`--encoding`（支持 `auto`）、`--limit`、`--rangesOut`、`--jsonOut`

---

## 工具 6：cli_text_diff.py

- **脚本路径**：`D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_text_diff.py`
- **用途**：两侧文本或文件对比，输出 unified diff 与摘要。
- **输入**：左侧 `--leftFile` 或 `--leftText` 二选一；右侧 `--rightFile` 或 `--rightText` 二选一。
- **核心参数**：`--encoding`（支持 `auto`）、`--context`、`--jsonOut`

---

## 工具 7：cli_patch_apply.py

- **脚本路径**：`D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_patch_apply.py`
- **用途**：按 unified diff 批量修改已存在文件。
- **输入**：`--patchText` / `--patchFile` / `--patchStdin` 三选一。
- **约束**：仅修改 `--root` 下路径；仅 update；不支持 rename/add/delete。
- **核心参数**：`--root`、`--dryRun`、`--jsonOut`

---

## 工具 8：cli_command_exec.py

- **脚本路径**：`D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_command_exec.py`
- **用途**：执行编译/测试/检查等命令，返回结构化结果。
- **核心参数**：`--command`、`--cwd`、`--timeoutSec`、`--safeMode`、`--jsonOut`、`--outputFile`
- **`--jsonOut` 失败结构**：`error` 含 `code` / `type` / `message` / `exitCode` / `hint` / `retryable`（便于 Agent 决策重试）。

---

## 工具 9：cli_test_report.py

- **脚本路径**：`D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_test_report.py`
- **用途**：在 `--root` 下执行 `pytest --junitxml=...`（另加 `--pytestArgs` 分词参数），解析 JUnit 为 `data.summary` + `data.cases[]`（`status`：`passed` / `failure` / `error` / `skipped`，含 `file` / `line` / `message` / `details` 等）。
- **顶层 `ok`**：`pytest` 退出码为 **0** 且不存在 `failure`/`error` 用例时为 `true`。
- **核心参数**：`--root`、`--pytestArgs`、`--timeoutSec`、`--keepJunit`（保留 xml 并在 `data.junitPath` 返回）、`--jsonOut`
- **依赖**：本机 `pytest` 在 PATH 中。

---

## 工具 10：cli_file_ops.py

---

## 工具 11：cli_todo_list.py

- **脚本路径**：`D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_todo_list.py`
- **用途**：创建/勾选/取消勾选/折叠/关闭执行清单（Todo List）。前端在输入框上方专有区域展示，全部完成前不可关闭。
- **核心参数**：`--action`（create/check/uncheck/collapse/close）、`--items`（create 时 JSON 数组）、`--itemIndex`（check/uncheck 时 0-based 索引）、`--listId`（create 返回，check/uncheck/collapse/close 时必填）、`--jsonOut`
- **输出**：`{ok, data: {listId, items: [{text, done}]}, error}`

- **脚本路径**：`D:\FanFiles\PycharmProjects\AI\工具库\文本工具\cli_file_ops.py`
- **用途**：文件/目录级操作：**delete**（删文件；删目录须 `--recursive`）、**rename**（`Path.rename`，同卷典型改名）、**copy**（`copy2` / `copytree`）、**move**（`shutil.move`，可跨卷）。
- **核心参数**：`--action`、`--source`；`rename`/`copy`/`move` 另需 **`--dest`**；可选 **`--root`**（`source`/`dest` 均须落在其下）；**`--recursive`**（仅 delete 目录）；**`--dryRun`**；**`--jsonOut`**
- **说明**：`rename` 与 `move` 语义不同：跨卷或复杂移动请用 `move`；`copy` 目录时目标侧使用 `dirs_exist_ok=True`。

---

## 编排工具（`编排/` 目录）

- **编排_agent.py**：**`--spec-file`** 读 JSON（顶层 `steps[]` 每步含 `run` 字符串数组，可选 `label` / `no_preview` / `preview_structured`；另有可选 `encoding_default`、`diff_context`、`verbose`、`no_preview`）；或 **`--preset line_dual_anchor --params-file`**（`params.request_file` 必填，可选 `json_out`）。识别到 **`cli_structured_edit` + `--payloadFile`** 且 payload 为 **`replace_range` / `replace_literal`** 时，在真正执行该步前用 **`cli_text_diff.py`** 打印 diff。示例壳：**`示例_spec_编排agent.json`**。
- **编排_agent.py spec 最小结构**：`{ "encoding_default":"utf-8", "diff_context":3, "steps":[{"label":"x","run":["python","..."],"preview_structured":true}] }`。
- **公共**：`编排_公共.py`（供其它编排脚本 import，含子进程调 `cli_structured_edit` / `cli_regex_locate` 与 `run_slice_between_regex`）。
- **编排_抽取行到文件.py**：`--source-file --start-line --end-line --out-file`（`extract` lines）。
- **编排_正则导出masks.py**：`--target --pattern --masks-out`（同 `cli_regex_locate --rangesOut`）。
- **编排_字面批量删除.py**：`--file --out-file` + 至少一次 `--drop-phrase`。
- **编排_双锚点区间提取.py**：`--file --out-file`，可选 `--left-pattern` / `--right-pattern`（省略则从头或到尾）。
- **编排_行号双锚与字面清理.py**：**`--request-file` JSON**，或同时 **`--source-file --line --out-file`**；可选 `--drop-phrase`（可重复）等。字段名见 **`示例_request_行号双锚.json`**（蛇形：`source_file`、`out_file`、`drop_phrases`…）。
- **编排_masks删除区间.py**：`--file --masks-json --out-file`（`[{start,end}]`）。
- **编排_正则导出masks并删除区间.py**：同文件上「导出 masks → `delete_segments`」一键链。
- **编排_正则命中原文拼接.py**：`--target --pattern --out-file`，可选 `--separator`、`--no-merge-overlaps`。

### 高频链速查（agent 按需选脚本）

| 链 | 含义 | 推荐 |
|----|------|------|
| 多步 CLI 顺序执行、写盘前终端看 diff | 编排调度 + review 减负 | `编排_agent.py --spec-file …` 或 `--preset line_dual_anchor --params-file …` |
| 大文件某行 → 去广告词 → 左右锚取主体 → 可选尾锚截断 | 语料单行提纯 | `编排_行号双锚与字面清理.py --request-file …` |
| 正则命中区间 → 从正文删掉 | 去格式噪声 / 敏感模板句 | `编排_正则导出masks并删除区间.py`（优先一步法；仅在复用 masks 时用分步） |
| 正则命中 → 只保留命中原文（按出现顺序拼接） | 关键词摘录、无生成拼接 | `编排_正则命中原文拼接.py` |
| 只抽若干行 | 抽样读行 | `编排_抽取行到文件.py` |
| 双锚截取整文件一段 | 锚点间正文 | `编排_双锚点区间提取.py` |
| 批量删固定字面 | 固定垃圾短语 | `编排_字面批量删除.py` |

---

## 给大模型的调用约束（建议）

- 长 JSON 优先 `requestFile` / `payloadFile`，避免 shell 转义。
- `extract` / `delete_segments` 优先 `--outFile` 落盘，减少 stdout 截断风险。
- `delete_segments` 与 `cli_regex_locate.py --rangesOut` 可组合：先正则导出区间，再作为 `masks` 传入。
- 改代码后优先跑 `cli_unified_diagnose.py --root <项目根> --jsonOut`，再跑 `cli_test_report.py --root <项目根> --jsonOut`；仍可用 `cli_command_exec.py` 做任意自定义命令。
- `cli_file_ops.py` 为破坏性操作：优先 `--dryRun` + `--root` 限制范围，再执行真实 delete/move。
