---
name: building-an-mcp-server
description: Author or connect a Model Context Protocol server so an agent gains new tools
version: 1.0
trust: built-in
---

MCP is how an agent gains capabilities it doesn't have natively (a service, a
database, an internal API). korgex is MCP-native — it can both consume servers and
act as one.

**Connecting an existing server (most common)**
1. `korgex mcp catalog` to see curated presets; `korgex mcp add <alias> [--global]`
   to add one. For a custom server: `--command <cmd> --args "…"` (stdio) or
   `--url <url> --header "Authorization: Bearer ${TOKEN}"` (remote).
2. Put secrets in env and reference them as `${VAR}` in the config — never inline a token.
3. korgex auto-connects configured servers at startup; tools appear namespaced as
   `server__tool`.

**Authoring a server**
1. Decide the tools: each gets a name, a one-line description (this is what the
   model sees), and a JSON-Schema `inputSchema`. Keep each tool single-purpose.
2. Implement the protocol: handle `initialize` → `tools/list` → `tools/call` over
   JSON-RPC (stdio or HTTP). Return results as content blocks; surface tool faults
   as an error result, not a crash.
3. Validate inputs at the boundary and treat all args as untrusted (see
   security-review). Don't expose destructive operations without a guard.
4. Test the handler purely (feed it requests, assert responses) before wiring a host.
5. Register it with `korgex mcp add` and confirm tools list + a sample call work.
