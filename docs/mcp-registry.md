# Publishing korg-ledger to the MCP Registry

The `modelcontextprotocol/servers` README is **no longer the catalog** — it now
hosts only the steering group's reference servers and points everyone at the
[MCP Registry](https://registry.modelcontextprotocol.io/). Community servers are
published there via a `server.json` manifest + the `mcp-publisher` CLI. A README
PR would be rejected; this is the current path.

`server.json` (repo root) is the publish-ready manifest for **`io.github.New1Direction/korg-ledger`**
(`korgex mcp-server`, stdio).

## Prerequisite: korgex on PyPI

The registry references an installable package; the manifest points at
`pypi:korgex`. korgex currently ships as a GitHub Release wheel, so publish to
PyPI first (one-time):

```bash
pip install build twine
python -m build
twine upload dist/korgex-0.6.0*        # needs a PyPI API token (yours)
```

(If the `korgex` name is taken on PyPI, pick a distinct dist name and update
`packages[0].identifier` in `server.json`.)

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

The namespace auth and the PyPI upload both require **your** credentials
(GitHub OAuth in a browser + a PyPI token), so the final publish is a step you
run — everything up to it (the manifest, the install path, the runtime args) is
prepared here. Re-run `mcp-publisher publish` on each `version` bump.

## Validate before publishing

```bash
korgex mcp-server <<< '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'   # lists korg_verify/korg_audit/korg_import
```

Schemas evolve — check `server.json`'s `$schema` against the live registry
schema before publishing and bump the date if needed.
