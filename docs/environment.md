# Environment Setup

KorgKode runs each task inside a secure sandbox. This lets it clone your repository, install dependencies, run tests, and verify changes without affecting your local environment.

## Sandbox Requirements

KorgKode's sandbox runs Ubuntu Linux and includes the following preinstalled tools:

```
-------- Python --------
python3: Python 3.12+
pip, pipx, poetry, uv
pytest, ruff, black, mypy

-------- Node.js --------
node: v22+
npm, pnpm, yarn
eslint, prettier

-------- Go --------
go: go1.24+

-------- Rust --------
rustc: rustc 1.87+
cargo

-------- Java --------
java: OpenJDK 21+
maven, gradle

-------- C/C++ --------
clang, gcc, cmake, ninja

-------- Infrastructure --------
docker, docker compose
git, curl, jq, yq, tmux, ripgrep
```

## Providing a Setup Script

For projects with complex dependencies, provide a setup script that KorgKode runs before starting work:

```bash
# Example setup.sh
npm install
npm run build
python -m pytest tests/ -x
```

## Environment Snapshots

After a successful setup, KorgKode can snapshot the environment for use in future tasks. This is especially useful for projects with long setup times.

## Validation Tips

- Include commands to install packages, run linters, or execute tests
- Check installed versions by adding commands like `node -v` to your setup script
- Keep your setup lightweight and fast