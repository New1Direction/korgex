# CLI Reference

KorgKode provides a lightweight command-line interface for managing coding sessions and integrating into your development workflows.

## Installation

```bash
git clone https://github.com/New1Direction/KorgKode.git
cd KorgKode
pip install -r requirements.txt
```

## Usage

```bash
python -m cli.main [command] [flags]
```

Or via the shell script:

```bash
./korgkode.sh [command] [flags]
```

## Commands

### `"<task description>"`

Run a new coding task. This is the primary way to use KorgKode.

```bash
./korgkode.sh "Add unit tests for the authentication module"
```

### `--schemas`

Print all tool schemas in JSON format. Useful for debugging or integration.

```bash
./korgkode.sh --schemas
```

### `--init`

Create an `AGENTS.md` file in the current directory. KorgKode reads this file for project-specific instructions, build commands, and testing patterns.

```bash
./korgkode.sh --init
```

### `--help`

Display help information.

```bash
./korgkode.sh --help
```

## Global Flags

| Flag | Description |
|------|-------------|
| `--repo` / `-r` | Repository root path. Defaults to current directory. |
| `--model` / `-m` | LLM model to use. Overrides `KORGKODE_MODEL` env var. |
| `--schemas` | Print tool schemas and exit. |
| `--init` | Initialize AGENTS.md in repository. |
| `--help` | Display help. |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KORGKODE_API_KEY` | Yes | — | API key for the LLM provider |
| `KORGKODE_API_URL` | No | NousResearch API | Base URL for the LLM provider |
| `KORGKODE_MODEL` | No | `deepseek/deepseek-v4-flash` | Model name |
| `KORGKODE_PROVIDER` | No | `nous` | Provider name |
| `KORGKODE_MAX_ITERATIONS` | No | `50` | Maximum tool calls per task |

## Examples

```bash
# Start a task in the current directory
./korgkode.sh "Fix the login bug"

# Start a task in a specific repository
./korgkode.sh "Add test coverage" --repo /path/to/project

# Use a specific model
KORGKODE_API_KEY="sk-..." KORGKODE_MODEL="gpt-4o" ./korgkode.sh "Refactor the API layer"

# Print tool schemas
./korgkode.sh --schemas

# Initialize AGENTS.md
cd /path/to/project && ./korgkode.sh --init
```