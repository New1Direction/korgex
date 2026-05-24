# Claude Code + Claude Max Reverse Engineering — Complete Findings
# Date: 2026-05-24
# Method: mitmproxy browser MITM + direct API probing

## API SURFACES

### 1. CLI API (api.anthropic.com/v1/messages)
- Auth: API key / OAuth
- System prompt: 4-block (attribution, identity, core, session) ~27K chars
- 12 user-facing tools
- 11 beta headers
- SSE streaming: 8 event types

### 2. Web REST (claude.ai/api/)
- Auth: sessionKey cookie + Cloudflare
- Endpoints mapped:
  - /organizations/{org}/chat_conversations
  - /organizations/{org}/projects
  - /organizations/{org}/usage
  - /organizations/{org}/memory
  - /organizations/{org}
  - /account/domain_density
  - /event_logging/v2/batch
- MCP SSE stream: /v1/sessions (server_list, server_base events)

### 3. Web Completion (POST .../chat_conversations/{id}/completion)
- Auth: sessionKey cookie (browser)
- Content-Type: application/json
- Accept: text/event-stream
- POST body keys: prompt, timezone, personalized_styles, locale, model, tools, turn_message_uuids, attachments, files, sync_sources, rendering_mode, create_conversation_params

## COMPLETION PAYLOAD STRUCTURE

```json
{
  "prompt": "<user message>",
  "timezone": "America/Edmonton",
  "personalized_styles": [{"type": "default", "key": "Default", "name": "Normal", "prompt": "Normal\\n", "isDefault": true}],
  "locale": "en-US",
  "model": "claude-opus-4-7",
  "tools": [<see tool section>],
  "turn_message_uuids": {"human_message_uuid": "...", "assistant_message_uuid": "..."},
  "attachments": [],
  "files": [],
  "sync_sources": [],
  "rendering_mode": "messages",
  "create_conversation_params": {
    "name": "",
    "model": "claude-opus-4-7",
    "project_uuid": "<uuid>",
    "include_conversation_preferences": true,
    "paprika_mode": "extended",
    "compass_mode": null,
    "is_temporary": false,
    "enabled_imagine": true
  }
}
```

## INTERNAL CODENAMES

### From usage API:
- omelette: Separate quota bucket (Claude Design — April 2026 design/UI tool)
- tangelo: Unknown, null — likely A/B test group
- iguana_necktie: Unknown, null — likely A/B test group
- seven_day_oauth_apps: OAuth app usage tracking
- seven_day_cowork: Cowork mode usage tracking
- seven_day_opus / seven_day_sonnet: Per-model usage tracking

### From conversation settings:
- paprika_mode: "extended" — feature toggle in completion params
- compass_mode: null — feature toggle (disabled)
- enabled_imagine: true — image generation feature
- enabled_monkeys_in_a_barrel: true — unknown feature
- enabled_saffron: true — unknown feature
- enabled_turmeric: true — unknown feature
- tool_search_mode: "auto" — tool discovery behavior
- thinking_mode: "extended" — extended thinking enabled
- preview_feature_uses_artifacts: true — artifacts preview

### From experiment events:
- cash-homepage_hide_navbar: A/B test (variation 1)
- claudified_melange_memory_migration_v2: A/B test (variation 0)

### From beta headers:
- ccr-byoc-2025-07-29: Bring-your-own-cloud feature

## MCP SERVERS (discovered via SSE + completion payload)

| Server | URL | Tools |
|--------|-----|-------|
| Google Drive | https://drivemcp.googleapis.com/mcp/v1 | 8 (copy_file, create_file, download_file_content, get_file_metadata, get_file_permissions, list_recent_files, read_file_content, search_files) |
| Gmail | https://gmailmcp.googleapis.com/mcp/v1 | TBD |
| Google Calendar | https://calendarmcp.googleapis.com/mcp/v1 | TBD |
| Hugging Face | https://huggingface.co/mcp?login&gradio=none | TBD |

MCP tool format:
```json
{
  "name": "tool_name",
  "description": "...",
  "input_schema": {...},
  "integration_name": "Google Drive",
  "mcp_server_uuid": "6c90d474-...",
  "mcp_server_url": "https://drivemcp.googleapis.com/mcp/v1",
  "needs_approval": true,
  "backend_execution": true,
  "read_only_hint": false,
  "is_mcp_app": false
}
```

## BUILT-IN TOOLS (from completion payload)

| Tool | Type | Description |
|------|------|-------------|
| web_search | web_search_v0 | Web search |
| artifacts | artifacts_v0 | Artifact generation |
| repl | repl_v0 | Code execution |
| show_widget | (full schema) | SVG/HTML widget rendering |
| read_me | (full schema) | Widget system setup |
| weather_fetch | widget | Weather widget |
| recipe_display_v0 | widget | Recipe display |
| places_map_display_v0 | widget | Map display |
| message_compose_v1 | widget | Message composer |
| ask_user_input_v0 | widget | User input elicitation |
| recommend_claude_apps | widget | App recommendations |
| places_search | widget | Place search |
| fetch_sports_data | widget | Sports data |

## MEMORY SYSTEM

Endpoint: GET /api/organizations/{org}/memory
Response format:
```json
{
  "memory": "<8125 char auto-generated profile>",
  "controls": ["<behavioral rule 1>", "<behavioral rule 2>", "..."],
  "updated_at": "2026-05-21T01:03:12.531000+00:00"
}
```

Memory structure (auto-generated from conversation history):
- **Work context** — Current projects, tools, workflows
- **Personal context** — Interests, preferences, knowledge systems
- **Top of mind** — Active priorities
- **Brief history** — Recent work sessions
- **Other instructions** — Behavioral controls

Controls are explicit behavioral rules like:
- "Never mention, reference, or suggest X"
- "Save Y as potentially useful later"

## MAX USAGE STRUCTURE

```json
{
  "five_hour": {"utilization": 33.0, "resets_at": "..."},
  "seven_day": {"utilization": 3.0, "resets_at": "..."},
  "seven_day_oauth_apps": null,
  "seven_day_opus": null,
  "seven_day_sonnet": {"utilization": 0.0, "resets_at": "..."},
  "seven_day_cowork": null,
  "seven_day_omelette": {"utilization": 0.0, "resets_at": null},
  "tangelo": null,
  "iguana_necktie": null,
  "omelette_promotional": null,
  "extra_usage": {
    "is_enabled": true,
    "monthly_limit": 14300,
    "used_credits": 743.0,
    "utilization": 5.19,
    "currency": "CAD"
  }
}
```

Max = rate limit tier, not different API. Same generation format.

## WEB CLIENT HEADERS (captured from Brave)

```
anthropic-device-id: <uuid>
anthropic-anonymous-id: claudeai.v1.<uuid>
anthropic-client-platform: web_claude_ai
anthropic-client-version: 1.0.0
anthropic-client-sha: 9f94910bb319abfbb3f61a3950f57e2804cf87be
x-activity-session-id: <uuid>
x-organization-uuid: <org-uuid>
```

## PROMPT INJECTION RESULT

Direct request for system prompt was refused. Claude confirmed:
- System prompt covers: searches, formatting, safety, tool usage, memory handling
- Project knowledge file (Claude_prompting_guide.md) injected as context
- Custom instructions shape conversation approach
- Memory scoped per-project
- Cannot share system prompt verbatim

## RAG INJECTION FORMAT (EXTRACTED)

Claude leaked the format when asked about the marker document:

**Format: `<documents>` block**

- Project KB files are injected as a `<documents>` block
- Each file is visible by filename (e.g. `test_rag_marker.txt`)
- Direct attachment into conversation context — no retrieval/RAG lookup step
- Injection happens **server-side** (not in the client POST payload)
- The client only sends `project_uuid` in `create_conversation_params`
- The server assembles the full prompt: system prompt + memory + `<documents>` + tools

Anthropic has explicit guardrails against revealing wrapper tags, headers, or system formatting verbatim.

## SSE STREAM FORMAT (CAPTURED LIVE)

```
event: conversation_ready
data: {"type":"conversation_ready"}

event: message_start
data: {"type":"message_start","message":{"id":"...","role":"assistant","model":"claude-opus-4-7",...}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":"","citations":[]}}

event: content_block_delta  (repeated)
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"..."}}

event: content_block_stop
data: {"type":"content_block_stop","index":0,"stop_timestamp":"..."}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null,"stop_details":null}}

event: message_limit
data: {"type":"message_limit","message_limit":{"type":"within_limit","windows":{"5h":{"status":"within_limit","utilization":0.4},"7d":{"status":"within_limit","utilization":0.03}}}}

event: message_stop
data: {"type":"message_stop"}
```

## WHAT'S STILL NEEDED

1. Full system prompt (assembled server-side, not visible in POST body)
   - Option B: Upload known marker doc, MITM the diff
   - Option C: Mobile app or desktop client may have different payload
2. SSE stream from /completion (long-lived connection, capture addon may not save)
3. GrowthBook/Statsig full config (include_system_prompts=true)
4. Project RAG injection format (how docs get embedded in prompt)
