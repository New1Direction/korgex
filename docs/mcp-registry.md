# Publishing korg-ledger to the MCP Registry

The `modelcontextprotocol/servers` README is **no longer the catalog** — it now
hosts only the steering group's reference servers and points everyone at the
[MCP Registry](https://registry.modelcontextprotocol.io/). Community servers are
published there via a `server.json` manifest + the `mcp-publisher` CLI. A README
PR would be rejected; this is the current path.

`server.json` (repo root) is the publish-ready manifest for **`io.github.New1Direction/korg-ledger`**
(`korgex mcp-server`, stdio). Keep both manifest version fields aligned with the
current `pyproject.toml` package version before publishing.

## Prerequisite: korgex on PyPI

The registry references an installable package; the manifest points at
`pypi:korgex`. korgex is published to PyPI from GitHub Releases via
`.github/workflows/publish.yml` using PyPI Trusted Publishing (OIDC), so there is
no manual API token or `twine upload` step for normal releases.

Before updating the registry for a new version:

```bash
python -m build
python -m twine check dist/*
python -m venv /tmp/korgex-pypi-smoke
/tmp/korgex-pypi-smoke/bin/pip install -U korgex==<version>
/tmp/korgex-pypi-smoke/bin/korgex --version
```

## Publish to the registry

```bash
# 1. install the publisher
brew install mcp-publisher           # or: download from the registry releases

# 2. authenticate the namespace — proves you own the `io.github.New1Direction/*`
#    namespace via GitHub OAuth (interactive; must be done by the New1Direction owner)
mcp-publisher login github

# 3. validate + publish the manifest
mcp-publisher publish ./server.json
```

The namespace auth requires **your** GitHub credentials. Re-run
`mcp-publisher publish` on each korgex version bump after the PyPI release is
available and `server.json` has been updated.

## Validate before publishing

```bash
korgex mcp-server <<< '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'   # lists korg_verify/korg_audit/korg_import
```

Schemas evolve — check `server.json`'s `$schema` against the live registry
schema before publishing and bump the date if needed.
