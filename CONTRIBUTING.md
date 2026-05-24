# Contributing to KorgKode

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/New1Direction/KorgKode.git
cd KorgKode
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Code Style

- Python: Black + Ruff formatting
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
3. Write a test in `tests/test_tools.py`
4. Verify with: `python -m cli.main --schemas`