---
description: Detect the build system and incrementally fix build/type errors with minimal, safe changes.
argument-hint: [blank]
---
<!-- Adapted from the ECC project (github.com/affaan-m/ECC), MIT-licensed. -->
# Build & Fix

Incrementally fix build and type errors with the smallest safe changes.

## 1 — Detect the build system and run it
| Indicator | Build command |
|---|---|
| `package.json` with a build script | `npm run build` (or `pnpm build`) |
| `tsconfig.json` (TypeScript only) | `npx tsc --noEmit` |
| `Cargo.toml` | `cargo build` |
| `go.mod` | `go build ./...` |
| `pyproject.toml` / `setup.py` | `ruff check .` then `python -m compileall -q .` (or `mypy .`) |
| `pom.xml` / `build.gradle` | `mvn compile` / `./gradlew compileJava` |

## 2 — Parse and group errors
Capture stderr, group errors by file, fix imports/types before logic errors, and count the total so you can track progress.

## 3 — Fix loop (one error at a time)
1. **Read** the file around the error. 2. **Diagnose** the root cause. 3. **Fix minimally** with Edit. 4. **Re-run** the build to confirm the error is gone and nothing new broke. 5. **Next.**

## 4 — Guardrails — stop and ask the user if
- a fix introduces more errors than it resolves,
- the same error survives 3 attempts (a deeper issue), or
- the fix needs architectural changes, not just a build fix.
