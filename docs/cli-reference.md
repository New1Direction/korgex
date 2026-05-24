# CLI Reference

Seluj provides a lightweight command-line interface for managing coding sessions and integrating into your development workflows.

## Installation

```bash
git clone https://github.com/New1Direction/Seluj.git
cd Seluj
pip install -r requirements.txt
```

## Usage

```bash
python -m cli.main [command] [flags]
```

Or via the shell script:

```bash
./seluj.sh [command] [flags]
```

## Commands

### `"<task description>"`

Run a new coding task. This is the primary way to use Seluj.

```bash
./seluj.sh "Add unit tests for the authentication module"
```

### `--schemas`

Print all tool schemas in JSON format. Useful for debugging or integration.

```bash
./seluj.sh --schemas
```

### `--init`

Create an `AGENTS.md` file in the current directory. Seluj reads this file for project-specific instructions, build commands, and testing patterns.

```bash
./seluj.sh --init
```

### `--help`

Display help information.

```bash
./seluj.sh --help
```

## Global Flags

| Flag | Description |
|------|-------------|
| `--repo` / `-r` | Repository root path. Defaults to current directory. |
| `--model` / `-m` | LLM model to use. Overrides `SELUJ_MODEL` env var. |
| `--schemas` | Print tool schemas and exit. |
| `--init` | Initialize AGENTS.md in repository. |
| `--help` | Display help. |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SELUJ_API_KEY` | Yes | — | API key for the LLM provider |
| `SELUJ_API_URL` | No | NousResearch API | Base URL for the LLM provider |
| `SELUJ_MODEL` | No | `deepseek/deepseek-v4-flash` | Model name |
| `SELUJ_PROVIDER` | No | `nous` | Provider name |
| `SELUJ_MAX_ITERATIONS` | No | `50` | Maximum tool calls per task |

## Examples

```bash
# Start a task in the current directory
./seluj.sh "Fix the login bug"

# Start a task in a specific repository
./seluj.sh "Add test coverage" --repo /path/to/project

# Use a specific model
SELUJ_API_KEY="sk-..." SELUJ_MODEL="gpt-4o" ./seluj.sh "Refactor the API layer"

# Print tool schemas
./seluj.sh --schemas

# Initialize AGENTS.md
cd /path/to/project && ./seluj.sh --init
```