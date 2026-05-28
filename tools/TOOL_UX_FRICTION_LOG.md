# 工具易用性摩擦日志

供 Agent 与开发者**追加**记录；用于打磨 `tool_list_agent.json` 与各 `tools/*.py` 的默认行为、命名与文档。

## 记录格式（复制后填空）

| 日期 | 工具 | 场景 | 摩擦 | 建议 |
|------|------|------|------|------|
| （例）2026-05-09 | read_file.py | 读大文件局部 | … | … |

---

<!-- 新记录追加在下方 -->

| 2026-05-27 | session_* | 模型仍传 `action=send` | 已从 catalog 移除 action；宿主剥离遗留字段 | 见 `agent_hints.session_collab` |
| 2026-05-27 | session_wait | `suspend=false` 仍被挂起 | 宿主仅认 `data.suspend` | `should_suspend_after_session_wait` |
| 2026-05-27 | file_search | 进度线程无 cid → Restricted | `execute_tool_script` 须传 `conversation_id` | `test_tool_regression_fixes` |
| 2026-05-27 | 多工具 | restrict/run_type 在只读 schema 造成困惑 | P2：只读 catalog 精简 + 宿主剥离 | `check_catalog_param_policy` |
| 2026-05-27 | data_table/glob | source、pattern 与 path/glob_pattern 别名 | P3：唯一参数名 + 显式拒绝废弃名 | `test_catalog_param_policy` |
| 2026-05-27 | replace_in_file | JSON 真换行 vs 源码字面 `\n` 混淆 | `raw=true` 将实际换行/制表符转为字面 `\n`、`\t`；catalog 示例 + `agent_hints.replace_in_file_raw` | `test_friction_fixes` |
| 2026-05-27 | session_wait | 跨 Tab 会话 wait 读错哨兵 | `sender_cid` 指定发出 session_send 的 conversation_id | `test_friction_fixes` |
| 2026-05-27 | python_inline | 字符串里含 `grep_files` 误拒 | `_forbid_inline_search` 改为仅匹配真实函数调用 | `test_friction_fixes` |
