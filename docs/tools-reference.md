# Tools Reference

KorgKode exposes **33 tools** across 9 categories. Each tool has a name, description, and typed parameters.

## File Operations

### list_files
Lists all files and directories under the given path. Directories show a trailing slash.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | STRING | No | Directory to list. Defaults to repo root. |

### read_file
Reads the full content of a file. Returns an error if the file doesn't exist.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filepath` | STRING | Yes | Path relative to repo root. |

### write_file
Creates a new file or overwrites an existing one.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filepath` | STRING | Yes | Path to create or overwrite. |
| `content` | STRING | Yes | Content to write. |

### replace_with_git_merge_diff
Targeted search-and-replace using Git merge diff format (SEARCH/REPLACE blocks).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filepath` | STRING | Yes | File to modify. |
| `merge_diff` | STRING | Yes | Diff with `<<<<<<< SEARCH` / `=======` / `>>>>>>> REPLACE` blocks. |

### delete_file
Deletes a file. Errors if the file doesn't exist.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filepath` | STRING | Yes | Path to delete. |

### rename_file
Renames or moves a file or directory.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filepath` | STRING | Yes | Original path. |
| `new_filepath` | STRING | Yes | New path. |

### restore_file
Restores a file to its original git state.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filepath` | STRING | Yes | File to restore. |

### reset_all
Resets the entire codebase to its original git state. No parameters.

## Planning

### set_plan
Sets the task plan in markdown format. Called after initial exploration.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `plan` | STRING | Yes | Markdown plan with numbered steps. |

### plan_step_complete
Marks the current plan step as complete. Must verify changes before calling.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `message` | STRING | Yes | Description of what was accomplished. |

### record_user_approval_for_plan
Records user approval of the plan. Called once at the start. No parameters.

## Execution

### run_in_bash_session
Runs a bash command in the sandbox. Successive calls share the same session.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | STRING | Yes | Bash command to run. |

### google_search
Searches the web for up-to-date information.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | STRING | Yes | Search query. |

### view_text_website
Fetches a website as plain text.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | STRING | Yes | URL to fetch. |

### view_image
Loads and displays an image from a URL.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | STRING | Yes | Image URL. |

### read_image_file
Reads an image file from the local machine.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filepath` | STRING | Yes | Path to image file. |

### read_media_file
Reads a media file (image or video) from the machine. Supports PNG, JPG, JPEG, WebP, WebM.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filepath` | STRING | Yes | Path to media file. |

## User Interaction

### message_user
Sends a message to the user. Use for updates, not questions.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `message` | STRING | Yes | Message content. |
| `continue_working` | BOOLEAN | No | Whether to continue after sending. |

### request_user_input
Asks the user a question and waits for a response.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `message` | STRING | Yes | Question to ask. |

## Code Review

### request_code_review
Requests a code review for the current changes. No parameters.

### read_pr_comments
Reads pending pull request comments. No parameters.

### reply_to_pr_comments
Replies to PR comments. Input is a JSON string.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `replies` | STRING | Yes | JSON: `[{"comment_id": "...", "reply": "..."}]` |

## Frontend Verification

### frontend_verification_instructions
Returns instructions for writing Playwright scripts. No parameters.

### frontend_verification_complete
Marks frontend verification as complete with a screenshot.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `screenshot_path` | STRING | Yes | Path to verification screenshot. |
| `additional_media_paths` | ARRAY | No | Additional media files (e.g., .webm recordings). |

### start_live_preview_instructions
Returns instructions for starting a live preview server. No parameters.

## Delivery

### pre_commit_instructions
Returns the list of pre-commit steps to run. Call before `submit`. No parameters.

### submit
Commits the current code and requests user approval to push.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `branch_name` | STRING | Yes | Git branch name. |
| `commit_message` | STRING | Yes | Commit message. |
| `title` | STRING | Yes | Submission title. |
| `description` | STRING | Yes | Submission description. |

## Memory

### initiate_memory_recording
Starts recording information for use in future tasks. No parameters.

## Subagents

### call_hello_world_agent
Calls a sub-agent with a message.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `message` | STRING | Yes | Message to the sub-agent. |

### done
Signals that a sub-agent has completed its task.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `summary` | STRING | Yes | Summary of what was accomplished. |

## Deprecated Tools

These tools exist for backward compatibility but should not be used:

| Tool | Replacement |
|------|-------------|
| `grep` | Use `run_in_bash_session` with `grep` |
| `create_file_with_block` | Use `write_file` |
| `overwrite_file_with_block` | Use `write_file` |