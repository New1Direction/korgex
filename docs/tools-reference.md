# Tools Reference

korgex has a two-layer tool architecture:

- **User-facing tools** (~12) — what the LLM sees. Named and described in Claude Code style. The agent calls these by name.
- **Internal handlers** (49+) — what actually runs. internal `tool_*` functions in `src/tools_impl.py`. The router translates user-facing calls into internal handler calls.

---

## User-facing tools (what the LLM calls)

These are the tools registered in `src/tool_abstraction.py` and passed to the LLM in every request.

| Tool | Key parameters | Purpose |
|------|---------------|---------|
| **Read** | `file_path`, `offset?`, `limit?` | Read a file from disk, optionally paginated |
| **Write** | `file_path`, `content` | Create or fully overwrite a file |
| **Edit** | `file_path`, `old_string`, `new_string`, `replace_all?` | Surgical string replacement. Internally converted to a SEARCH/REPLACE block. |
| **Bash** | `command`, `timeout?` | Execute a shell command |
| **Grep** | `pattern`, `path?`, `glob?`, `output_mode?` | Regex search over file contents |
| **Glob** | `pattern`, `path?` | Find files by name pattern |
| **Agent** | `description`, `prompt`, `subagent_type?`, `model?` | Delegate a sub-task to a specialised agent |
| **AskUserQuestion** | `questions` | Ask the user a clarifying question |
| **TaskCreate** | `tasks` | Create a task list to track multi-step work |
| **Skill** | `skill`, `args?` | Invoke an installed skill by name |
| **ToolSearch** | `query` | Discover available tools by keyword at runtime |

Plus any tools registered from MCP servers at startup (via `--mcp`).

### How routing works

```
LLM calls Read(file_path="/src/foo.py")
    │
    ▼
tool_abstraction.route_tool_call("Read", {"file_path": "/src/foo.py"})
    │  param_map: file_path → filepath
    │  inject:   context = {"repo_root": cwd}
    ▼
tools_impl.tool_read_file(filepath="/src/foo.py", context={"repo_root": ...})
```

The router also:
- Applies **adapters** for structural transforms (Edit's `old_string`/`new_string` → a SEARCH/REPLACE `merge_diff` string)
- **Filters** kwargs the handler doesn't accept (e.g. `Read.offset` is silently dropped if the handler doesn't have it)
- **Catches all exceptions** and returns `{"error": ...}` so a single tool failure never kills the agent loop
- Routes **MCP tools** directly to `MCPServerManager.call_tool()` instead of the local handler table

---

## Internal handlers (what actually runs)

These live in `src/tools_impl.py`. You don't call these directly — the router maps user-facing tool calls to them.

### File operations

| Handler | Description |
|---------|-------------|
| `tool_read_file` | Read file contents. Returns `{content, filepath, size}`. |
| `tool_write_file` | Create or overwrite a file. Creates parent directories automatically. |
| `tool_replace_with_git_merge_diff` | Apply SEARCH/REPLACE blocks to a file. |
| `tool_delete_file` | Delete a file. |
| `tool_rename_file` | Rename or move a file or directory. |
| `tool_restore_file` | Restore a file to its last git-committed state (`git checkout --`). |
| `tool_reset_all` | Hard-reset the entire repo (`git reset --hard && git clean -fd`). |
| `tool_list_files` | List files in a directory. |

### Execution

| Handler | Description |
|---------|-------------|
| `tool_run_in_bash_session` | Run a bash command. Uses sandbox if configured, otherwise direct subprocess. |
| `tool_run_test_with_self_healing` | Run tests and auto-patch failures up to N times (requires sandbox). |
| `tool_google_search` | Web search (requires `web_tools`). |
| `tool_view_text_website` | Fetch a URL as plain text. |

### Image and media

| Handler | Description |
|---------|-------------|
| `tool_view_image` | Download an image from a URL (30s timeout, 25 MB cap) and return base64. |
| `tool_read_image_file` | Read a local image file and return base64. |
| `tool_read_media_file` | Read a local image or video file. |
| `tool_capture_screenshot` | Take a headless Chrome screenshot of a URL (requires Selenium). |
| `tool_analyze_image` | Analyze a local image file and return base64 + metadata for vision models. |

### Planning and tracking

| Handler | Description |
|---------|-------------|
| `tool_set_plan` | Write a markdown plan to `.korgex/plan.md`. |
| `tool_plan_step_complete` | Append a completed step to `.korgex/steps.json`. |
| `tool_record_user_approval_for_plan` | Write an approval marker to `.korgex/approved`. |

### User interaction

| Handler | Description |
|---------|-------------|
| `tool_message_user` | Print a message to the user. |
| `tool_request_user_input` | Prompt the user for input (blocking). |

### GitHub

| Handler | Description |
|---------|-------------|
| `tool_github_create_pr` | Create a pull request. |
| `tool_github_list_prs` | List PRs for a repo. |
| `tool_github_get_pr_comments` | Get comments on a PR. |
| `tool_github_reply_to_pr_comment` | Reply to a PR comment. |
| `tool_github_create_issue` | Create a GitHub issue with optional labels. |

Requires `GITHUB_TOKEN` in the environment. Initialised lazily on first call.

### Memory

| Handler | Description |
|---------|-------------|
| `tool_memory_save` | Save a persistent memory entry to `~/.claude/projects/.../memory/`. |
| `tool_memory_delete` | Delete a memory entry by slug. |
| `tool_memory_search` | Search memory entries by keyword. |
| `tool_memory_list` | List all memory entries, optionally filtered by type. |

### Analysis

| Handler | Description |
|---------|-------------|
| `tool_get_codebase_impact_report` | AST-based import graph: which files are affected if you change a symbol. |
| `tool_get_god_nodes` | Find the highest-fanout files (highest risk to refactor). |
| `tool_get_performance_profile` | Run `cProfile` on a command and return top-N slowest functions (requires sandbox). |
| `tool_get_compressed_file_context` | Return an AST skeleton of a large file with non-target functions collapsed. |

### MCP management (runtime)

| Handler | Description |
|---------|-------------|
| `tool_mcp_connect` | Connect to an MCP server and discover its tools. |
| `tool_mcp_disconnect` | Disconnect an MCP server and remove its tools. |
| `tool_mcp_list` | List all connected MCP servers and their tools. |

### Mode and pairing

| Handler | Description |
|---------|-------------|
| `tool_enter_plan_mode` | Switch to plan mode (read-only, Opus model). |
| `tool_exit_plan_mode` | Return to execute mode (Sonnet model). |
| `tool_tool_use_status` | Show pending and completed tool call pairs. |

### Deprecated (kept for compatibility)

| Handler | Replacement |
|---------|-------------|
| `tool_grep` | Use `Bash` with `grep -r` or the `Grep` user tool |
| `tool_create_file_with_block` | Use `Write` |
| `tool_overwrite_file_with_block` | Use `Write` |
