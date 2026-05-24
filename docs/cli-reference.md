# CLI Reference

Korgex provides a lightweight command-line interface for managing coding sessions and integrating into your development workflows.

## Installation

```bash
git clone https://github.com/New1Direction/Korgex.git
cd Korgex
pip install -r requirements.txt
```

## Usage

```bash
python -m cli.main [command] [flags]
```

Or via the shell script:

```bash
./korgex.sh [command] [flags]
```

## Commands

### `"<task description>"`

Run a new coding task. This is the primary way to use Korgex.

```bash
./korgex.sh "Add unit tests for the authentication module"
```

### `--schemas`

Print all tool schemas in JSON format. Useful for debugging or integration.

```bash
./korgex.sh --schemas
```

### `--init`

Create an `AGENTS.md` file in the current directory. Korgex reads this file for project-specific instructions, build commands, and testing patterns.

```bash
./korgex.sh --init
```

### `--help`

Display help information.

```bash
./korgex.sh --help
```

## Global Flags

| Flag | Description |
|------|-------------|
| `--repo` / `-r` | Repository root path. Defaults to current directory. |
| `--model` / `-m` | LLM model to use. Overrides `KORGEX_MODEL` env var. |
| `--schemas` | Print tool schemas and exit. |
| `--init` | Initialize AGENTS.md in repository. |
| `--help` | Display help. |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KORGEX_API_KEY` | Yes | — | API key for the LLM provider |
| `KORGEX_API_URL` | No | NousResearch API | Base URL for the LLM provider |
| `KORGEX_MODEL` | No | `deepseek/deepseek-v4-flash` | Model name |
| `KORGEX_PROVIDER` | No | `nous` | Provider name |
| `KORGEX_MAX_ITERATIONS` | No | `50` | Maximum tool calls per task |

## Examples

```bash
# Start a task in the current directory
./korgex.sh "Fix the login bug"

# Start a task in a specific repository
./korgex.sh "Add test coverage" --repo /path/to/project

# Use a specific model
KORGEX_API_KEY="sk-..." KORGEX_MODEL="gpt-4o" ./korgex.sh "Refactor the API layer"

# Print tool schemas
./korgex.sh --schemas

# Initialize AGENTS.md
cd /path/to/project && ./korgex.sh --init
```