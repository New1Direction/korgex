# Contributing to Korgex

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/New1Direction/Korgex.git
cd Korgex
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"

# Enable the opsec pre-commit guard (one-time, per clone):
git config core.hooksPath scripts/githooks
```

`pyproject.toml` is the source of truth for package dependencies. `requirements.txt`
is kept as a compatibility mirror for source checkouts and legacy automation; prefer
`pip install -e ".[dev]"` for development.

### Opsec guard

`scripts/githooks/pre-commit` blocks commits that look like reverse-engineering or
vendor-internal material (this is a public repo — keep that on the private side). Run
the `git config core.hooksPath scripts/githooks` line above once after cloning so it's
active. A genuine false positive can bypass with `OPSEC_GUARD_OK=1 git commit ...`.

## Code Style

- Python: Ruff linting/formatting conventions
- Imports: sorted with `ruff check --fix`
- Type hints: use them on all public functions

## Pull Request Process

1. Create a feature branch: `git checkout -b feat/your-change`
2. Make your changes
3. Run tests: `python -m pytest tests/`
4. Push and open a PR
5. Respond to any review feedback

## Adding a New Tool

1. Define the tool in `src/tools_impl.py` using `@register_tool`
2. Add the schema to the tool def with typed parameters
3. Write a focused regression test under `tests/`
4. Verify with: `korgex --introspect` and the relevant pytest target
