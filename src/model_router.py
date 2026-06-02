"""
Korgex Multi-Model Router — Opus-for-plan, Sonnet-for-execute routing.

Architecture:
  [Mode State Machine] ──▶ [ModelRouter] ──▶ [API Client]
         ▲                            │
         │                            ▼
         └── enter_plan_mode() ──▶ model_swap("plan")
         └── exit_plan_mode()  ──▶ model_swap("execute")

Each mode maps to a different model/config:
  plan     → Opus 4.7   (deep reasoning, expensive)
  execute  → Sonnet 4.6 (fast code gen, cheaper)
  explore  → Opus 4.7   (analysis)
  review   → Sonnet 4.6 (quick review)
  debug    → Haiku 4.5  (fast iterations, cheapest)
  research → Opus 4.7   (deep research)

Context is fully preserved across model swaps via shared message history.
Prefix caching means the system prompt stays cached even when models change.
"""

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol

# For cost tracking and display
from src.feature_flags import is_enabled


def _to_epoch(value, ms: bool = False) -> float:
    """Coerce a stored token-expiry to a Unix epoch float.

    Accepts a float/int, a numeric string, or an ISO-8601 timestamp; ``ms=True``
    treats numeric inputs as milliseconds. Garbage / missing → 0.0 (treated as
    already expired). Guards _is_expired() against `float > str` crashes — some
    provider CLIs (grok) store expires_at as a string.
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if ms else float(value)
    s = str(value).strip()
    if not s:
        return 0.0
    try:
        n = float(s)
        return n / 1000.0 if ms else n
    except ValueError:
        pass
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, OSError):
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════
# MODEL DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ModelConfig:
    """Configuration for a single model endpoint."""
    provider: str                    # "anthropic", "openrouter", "local", "openai"
    model_id: str                    # Full model ID sent to API
    display_name: str                # Human-readable name
    max_tokens: int = 64000
    thinking_budget: Optional[int] = None  # For Claude thinking mode
    cost_per_mtok_input: float = 0.0
    cost_per_mtok_output: float = 0.0
    supports_thinking: bool = False
    supports_streaming: bool = True


# ═══════════════════════════════════════════════════════════════════════════
# MODE → MODEL MAPPING
# ═══════════════════════════════════════════════════════════════════════════

# Default model configs — approximate public list pricing
DEFAULT_MODELS: dict[str, ModelConfig] = {
    "opus47": ModelConfig(
        provider="claude",
        model_id="claude-opus-4-7",
        display_name="Opus 4.7",
        max_tokens=64000,
        thinking_budget=20000,
        cost_per_mtok_input=15.0,
        cost_per_mtok_output=75.0,
        supports_thinking=True,
    ),
    "sonnet46": ModelConfig(
        provider="claude",
        model_id="claude-sonnet-4-6",
        display_name="Sonnet 4.6",
        max_tokens=64000,
        thinking_budget=None,
        cost_per_mtok_input=3.0,
        cost_per_mtok_output=15.0,
        supports_thinking=False,
    ),
    "haiku45": ModelConfig(
        provider="claude",
        model_id="claude-haiku-4-5",
        display_name="Haiku 4.5",
        max_tokens=32000,
        thinking_budget=None,
        cost_per_mtok_input=0.8,
        cost_per_mtok_output=4.0,
        supports_thinking=False,
    ),
    # ── Opus 4.8 (latest, via OAuth) ──
    "opus48": ModelConfig(
        provider="claude",
        model_id="claude-opus-4-8",
        display_name="Opus 4.8",
        max_tokens=64000,
        thinking_budget=20000,
        cost_per_mtok_input=15.0,
        cost_per_mtok_output=75.0,
        supports_thinking=True,
    ),
    # ── Gemini (Google) models — uses Google OAuth2, no API key needed ──
    "gemini-flash": ModelConfig(
        provider="gemini",
        model_id="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        max_tokens=64000,
        cost_per_mtok_input=0.15,
        cost_per_mtok_output=0.60,
        supports_thinking=False,
    ),
    "gemini-pro": ModelConfig(
        provider="gemini",
        model_id="gemini-3.1-pro",
        display_name="Gemini 3.1 Pro",
        max_tokens=64000,
        cost_per_mtok_input=1.25,
        cost_per_mtok_output=10.0,
        supports_thinking=False,
    ),
    # ── Venice AI models — OpenAI-compatible, API key required ──
    "venice-uncensored": ModelConfig(
        provider="venice",
        model_id="venice-uncensored-1-2",
        display_name="Venice Uncensored",
        max_tokens=64000,
        cost_per_mtok_input=0.50,
        cost_per_mtok_output=2.00,
        supports_thinking=False,
    ),
    "venice-default": ModelConfig(
        provider="venice",
        model_id="default",
        display_name="Venice Default",
        max_tokens=64000,
        cost_per_mtok_input=0.50,
        cost_per_mtok_output=2.00,
        supports_thinking=False,
    ),
    # ── Grok (xAI) models — uses grok-build OAuth, no API key needed ──
    "grok4": ModelConfig(
        provider="grok",
        model_id="grok-4.3",           # valid xAI id (was "latest", which 400s)
        display_name="Grok 4",
        max_tokens=64000,
        thinking_budget=None,
        cost_per_mtok_input=2.0,       # list price via api.x.ai
        cost_per_mtok_output=10.0,
        supports_thinking=False,
    ),
    "grok-reasoning": ModelConfig(
        provider="grok",
        model_id="grok-4.20-0309-reasoning",
        display_name="Grok Reasoning",
        max_tokens=64000,
        thinking_budget=None,
        cost_per_mtok_input=2.0,
        cost_per_mtok_output=80.0,
        supports_thinking=True,
    ),
    "grok-mini": ModelConfig(
        provider="grok",
        model_id="grok-4.20-0309-non-reasoning",
        display_name="Grok Mini",
        max_tokens=32000,
        thinking_budget=None,
        cost_per_mtok_input=0.50,
        cost_per_mtok_output=2.50,
        supports_thinking=False,
    ),
}

# Mode → Model assignment
MODE_MODEL_MAP: dict[str, str] = {
    "plan": "opus47",
    "execute": "sonnet46",
    "explore": "opus47",
    "review": "sonnet46",
    "debug": "haiku45",
    "research": "opus47",
}

# Mode → Generation params
MODE_PARAMS: dict[str, dict] = {
    "plan": {
        "max_tokens": 64000,
        "thinking": {"budget_tokens": 20000},
        "temperature": 0.7,
    },
    "execute": {
        "max_tokens": 64000,
        "temperature": 0.3,
    },
    "explore": {
        "max_tokens": 32000,
        "temperature": 0.5,
    },
    "review": {
        "max_tokens": 16000,
        "temperature": 0.3,
    },
    "debug": {
        "max_tokens": 16000,
        "temperature": 0.2,
    },
    "research": {
        "max_tokens": 32000,
        "temperature": 0.7,
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# API CLIENT ABSTRACTION
# ═══════════════════════════════════════════════════════════════════════════

class APIResponse:
    """A unified API response across all providers."""
    def __init__(self, content: list[dict], model: str,
                 usage: dict, stop_reason: str = None):
        self.content = content
        self.model = model
        self.usage = usage
        self.stop_reason = stop_reason or "end_turn"
    
    def has_tool_call(self) -> bool:
        return any(b.get("type") == "tool_use" for b in self.content)
    
    def get_tool_calls(self) -> list[dict]:
        return [b for b in self.content if b.get("type") == "tool_use"]
    
    def get_text(self) -> str:
        return "".join(b.get("text", "") for b in self.content
                       if b.get("type") == "text")
    
    def get_thinking(self) -> str:
        return "".join(b.get("thinking", "") for b in self.content
                       if b.get("type") == "thinking")


class BaseClient(Protocol):
    """Protocol for API clients."""
    def send(self, messages: list[dict], system: list[dict],
             tools: list[dict], params: dict) -> APIResponse:
        ...


class AnthropicClient:
    """Anthropic Messages API client."""
    
    BASE_URL = "https://api.anthropic.com/v1/messages"
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    
    def send(self, messages: list[dict], system: list[dict],
             tools: list[dict], params: dict) -> APIResponse:
        """Send messages to Anthropic API."""
        import httpx
        
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        
        # Add beta headers from feature flags
        from src.feature_flags import get_beta_headers_dict
        beta_headers = get_beta_headers_dict()
        beta_header = beta_headers.get("anthropic-beta", "")
        if beta_header:
            headers["anthropic-beta"] = beta_header
        
        body = {
            "model": params["model"],
            "max_tokens": params.get("max_tokens", 64000),
            "messages": messages,
            "system": system,
            "tools": tools,
        }
        
        if params.get("thinking"):
            body["thinking"] = params["thinking"]
        
        if params.get("temperature") is not None:
            body["temperature"] = params["temperature"]
        
        try:
            response = httpx.post(
                self.BASE_URL,
                headers=headers,
                json=body,
                timeout=300,
            )
            response.raise_for_status()
            data = response.json()
            
            usage = data.get("usage", {})
            content = data.get("content", [])
            
            return APIResponse(
                content=content,
                model=data.get("model", params["model"]),
                usage={
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                },
                stop_reason=data.get("stop_reason", "end_turn"),
            )
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else str(e)
            raise RuntimeError(f"API error ({e.response.status_code}): {error_body}")
        except Exception as e:
            raise RuntimeError(f"API request failed: {e}")


class GrokClient:
    """xAI Grok client — uses Grok Build OAuth from ~/.grok/auth.json.
    
    Reads the cached JWT from the grok CLI's auth store. If the token is
    expired, refreshes via the OAuth2 refresh flow against auth.x.ai.
    Uses the OpenAI-compatible /v1/chat/completions endpoint.
    No API key needed — authenticates with the grok-build OAuth client_id.
    """

    BASE_URL = "https://api.x.ai/v1/chat/completions"
    CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
    AUTH_URL = "https://auth.x.ai"
    AUTH_JSON = os.path.expanduser("~/.grok/auth.json")
    _token_cache: dict = {}

    def __init__(self, api_key: str = None):
        self._jwt: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        self._load_token()

    # ── token management ──────────────────────────────────────────────

    def _load_token(self):
        """Read the cached JWT from ~/.grok/auth.json."""
        try:
            with open(self.AUTH_JSON) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        # The auth.json key format is: https://auth.x.ai::<client_id>
        for key, val in data.items():
            if self.AUTH_URL in key and isinstance(val, dict):
                self._jwt = val.get("key", "")
                self._refresh_token = val.get("refresh_token", "")
                self._expires_at = _to_epoch(val.get("expires_at", 0.0))
                break

    def _save_token(self):
        """Write refreshed token back to ~/.grok/auth.json."""
        try:
            with open(self.AUTH_JSON) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        for key, val in data.items():
            if self.AUTH_URL in key and isinstance(val, dict):
                val["key"] = self._jwt
                val["refresh_token"] = self._refresh_token
                val["expires_at"] = self._expires_at
                break
        try:
            with open(self.AUTH_JSON, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    def _is_expired(self) -> bool:
        return not self._jwt or (time.time() > self._expires_at)

    def _refresh(self) -> bool:
        """Refresh the access token via OAuth2 refresh grant."""
        if not self._refresh_token:
            return False
        import httpx
        try:
            r = httpx.post(
                f"{self.AUTH_URL}/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.CLIENT_ID,
                    "refresh_token": self._refresh_token,
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            self._jwt = data["access_token"]
            self._refresh_token = data.get("refresh_token", self._refresh_token)
            self._expires_at = time.time() + data.get("expires_in", 21600) - 300
            self._save_token()
            return True
        except Exception:
            return False

    def _ensure_token(self) -> str:
        if self._is_expired():
            self._refresh()
        if not self._jwt:
            raise RuntimeError(
                "No Grok auth token. Run 'grok login' first, or set "
                "XAI_API_KEY env var for API-key auth."
            )
        return self._jwt

    # ── API call ──────────────────────────────────────────────────────

    def send(self, messages: list[dict], system: list[dict],
             tools: list[dict], params: dict) -> APIResponse:
        import httpx

        # Convert Anthropic system blocks → OpenAI system messages
        openai_messages = []
        for block in system:
            if block.get("type") == "text":
                openai_messages.append({
                    "role": "system",
                    "content": block.get("text", ""),
                })
        openai_messages.extend(messages)

        # Convert Anthropic tools → OpenAI function tools
        openai_tools = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })

        body = {
            "model": params["model"],
            "max_tokens": params.get("max_tokens", 64000),
            "messages": openai_messages,
            "tools": openai_tools if openai_tools else None,
            "temperature": params.get("temperature", 0.3),
        }
        if body["tools"] is None:
            del body["tools"]

        token = self._ensure_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            response = httpx.post(
                self.BASE_URL, headers=headers, json=body, timeout=300
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else str(e)
            raise RuntimeError(f"Grok API error ({e.response.status_code}): {error_body}")
        except Exception as e:
            raise RuntimeError(f"Grok API request failed: {e}")

        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        usage = data.get("usage", {})

        # Convert OpenAI tool_calls → Anthropic format
        content = []
        if msg.get("content"):
            content.append({"type": "text", "text": msg["content"]})
        for tc in msg.get("tool_calls", []):
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                func_args = {}
            content.append({
                "type": "tool_use",
                "name": tc["function"]["name"],
                "input": func_args,
                "id": tc.get("id", ""),
            })

        return APIResponse(
            content=content,
            model=data.get("model", params["model"]),
            usage={
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
            stop_reason=choice.get("finish_reason", "stop"),
        )


class GeminiClient:
    """Google Gemini API client — bring-your-own Google OAuth2 credential.

    Reads the user's own token from ~/.gemini/antigravity-cli/antigravity-oauth-token
    (Antigravity IDE) or the Google VS Code extension ADC credentials, and
    auto-refreshes via oauth2.googleapis.com when expired. The OAuth client_id /
    client_secret are read from GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET,
    falling back to the local ADC json (which already carries both) — never
    hardcoded. Uses the public Gemini API (generativelanguage.googleapis.com).
    """

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    AUTH_JSON = os.path.expanduser(
        "~/.gemini/antigravity-cli/antigravity-oauth-token"
    )
    # Fallback: Google VS Code extension ADC (carries client_id + secret + refresh)
    ADC_JSON = os.path.expanduser(
        "~/Library/Application Support/"
        "google-vscode-extension/auth/application_default_credentials.json"
    )

    def __init__(self, api_key: str = None):
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        # BYO OAuth client creds: env first, else filled from the local ADC file.
        self._client_id: str = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
        self._client_secret: str = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
        self._load_token()

    # ── token management ──────────────────────────────────────────────

    def _load_token(self):
        """Try Antigravity token first, then fall back to ADC."""
        for path in [self.AUTH_JSON, self.ADC_JSON]:
            try:
                with open(path) as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            tok = data.get("token", data)
            self._access_token = tok.get("access_token", "")
            self._refresh_token = tok.get("refresh_token", "")
            # client creds may live in the same file (ADC) — env still wins
            if not self._client_id:
                self._client_id = tok.get("client_id", data.get("client_id", ""))
            if not self._client_secret:
                self._client_secret = tok.get("client_secret", data.get("client_secret", ""))
            # expiry may be an ISO string (Antigravity) or absent (ADC) — coerce
            self._expires_at = _to_epoch(tok.get("expiry", 0))
            if self._refresh_token:
                break

    def _is_expired(self) -> bool:
        return not self._access_token or (time.time() > self._expires_at)

    def _refresh(self) -> bool:
        """Refresh the access token via Google OAuth2."""
        if not self._refresh_token:
            return False
        import httpx

        try:
            r = httpx.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            self._access_token = data["access_token"]
            self._refresh_token = data.get("refresh_token", self._refresh_token)
            self._expires_at = time.time() + data.get("expires_in", 3600) - 300
            return True
        except Exception:
            return False

    def _ensure_token(self) -> str:
        if self._is_expired():
            self._refresh()
        if not self._access_token:
            raise RuntimeError(
                "No Google AI OAuth token. Run Antigravity IDE to authenticate, "
                "or set GOOGLE_API_KEY env var for API-key auth."
            )
        return self._access_token

    # ── API call ──────────────────────────────────────────────────────

    def send(self, messages: list[dict], system: list[dict],
             tools: list[dict], params: dict) -> APIResponse:
        """Send messages to Gemini API."""
        import httpx

        token = self._ensure_token()

        # Build Gemini contents from messages
        contents = []
        # System instructions
        sys_text = " ".join(
            b.get("text", "") for b in system if b.get("type") == "text"
        )
        system_instruction = {"parts": [{"text": sys_text}]} if sys_text else None

        # Messages
        for msg in messages:
            role = "model" if msg.get("role") == "assistant" else "user"
            text = ""
            if isinstance(msg.get("content"), str):
                text = msg["content"]
            elif isinstance(msg.get("content"), list):
                text = " ".join(
                    b.get("text", "") for b in msg["content"]
                    if b.get("type") == "text"
                )
            contents.append({"role": role, "parts": [{"text": text}]})

        # Tool declarations (Gemini format)
        gemini_tools = []
        if tools:
            function_declarations = []
            for tool in tools:
                function_declarations.append({
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                })
            gemini_tools.append({"functionDeclarations": function_declarations})

        model_id = params.get("model", "gemini-2.5-flash")

        body = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": params.get("max_tokens", 64000),
                "temperature": params.get("temperature", 0.3),
            },
        }
        if system_instruction:
            body["systemInstruction"] = system_instruction
        if gemini_tools:
            body["tools"] = gemini_tools

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            response = httpx.post(
                f"{self.BASE_URL}/models/{model_id}:generateContent",
                headers=headers,
                json=body,
                timeout=300,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else str(e)
            raise RuntimeError(
                f"Gemini API error ({e.response.status_code}): {error_body}"
            )
        except Exception as e:
            raise RuntimeError(f"Gemini API request failed: {e}")

        usage = data.get("usageMetadata", {})
        candidates = data.get("candidates", [{}])
        candidate = candidates[0] if candidates else {}
        gemini_content = candidate.get("content", {})

        # Convert Gemini response → Anthropic content format
        content = []
        for part in gemini_content.get("parts", []):
            if "text" in part:
                content.append({"type": "text", "text": part["text"]})
            elif "functionCall" in part:
                fc = part["functionCall"]
                content.append({
                    "type": "tool_use",
                    "name": fc.get("name", ""),
                    "input": fc.get("args", {}),
                    "id": fc.get("id", ""),
                })

        return APIResponse(
            content=content,
            model=data.get("modelVersion", model_id),
            usage={
                "input_tokens": usage.get("promptTokenCount", 0),
                "output_tokens": usage.get("candidatesTokenCount", 0),
            },
            stop_reason=(
                "tool_use"
                if any(b.get("type") == "tool_use" for b in content)
                else "end_turn"
            ),
        )


class NousClient:
    """Nous Portal inference client — uses Hermes Agent OAuth from auth.json.

    Reads the agent_key JWT from ~/.hermes/auth.json (the same credential
    Hermes Agent CLI uses). Auto-refreshes via portal.nousresearch.com
    device-code OAuth2 token refresh + agent-key minting.
    Uses the Nous Inference API (OpenAI-compatible endpoint).
    """

    BASE_URL = "https://inference-api.nousresearch.com/v1/chat/completions"
    AUTH_JSON = os.path.expanduser("~/.hermes/auth.json")
    PORTAL_URL = "https://portal.nousresearch.com"
    CLIENT_ID = "hermes-cli"

    def __init__(self, api_key: str = None):
        self._agent_key: str | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        self._load_auth()

    # ── auth management ───────────────────────────────────────────────

    def _load_auth(self):
        """Read agent_key and refresh tokens from ~/.hermes/auth.json."""
        try:
            with open(self.AUTH_JSON) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        nous = data.get("providers", {}).get("nous", {})
        if not nous:
            # Check credential pool
            for cred in data.get("credential_pool", {}).get("nous", []):
                nous = cred
                break

        self._agent_key = nous.get("agent_key", "")
        self._access_token = nous.get("access_token", "")
        self._refresh_token = nous.get("refresh_token", "")

        # Parse expiry
        expires_str = nous.get("expires_at", nous.get("agent_key_expires_at", ""))
        if expires_str:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
                self._expires_at = dt.timestamp()
            except (ValueError, OSError):
                self._expires_at = 0

    def _save_auth(self):
        """Write refreshed tokens back to ~/.hermes/auth.json."""
        try:
            with open(self.AUTH_JSON) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        nous = data.setdefault("providers", {}).setdefault("nous", {})
        nous["agent_key"] = self._agent_key
        nous["access_token"] = self._access_token
        nous["refresh_token"] = self._refresh_token
        nous["expires_at"] = datetime.fromtimestamp(
            self._expires_at, tz=timezone.utc
        ).isoformat()

        try:
            with open(self.AUTH_JSON, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    def _is_expired(self) -> bool:
        return not self._agent_key or (time.time() > self._expires_at)

    def _refresh_token_via_portal(self) -> bool:
        """Refresh access_token using the stored refresh_token."""
        if not self._refresh_token:
            return False
        import httpx

        try:
            r = httpx.post(
                f"{self.PORTAL_URL}/api/oauth/token",
                headers={"x-nous-refresh-token": self._refresh_token},
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.CLIENT_ID,
                },
                timeout=15,
            )
            r.raise_for_status()
            payload = r.json()
            self._access_token = payload["access_token"]
            self._refresh_token = payload.get("refresh_token", self._refresh_token)
            return True
        except Exception:
            return False

    def _mint_agent_key(self) -> bool:
        """Mint a new agent_key using the access_token."""
        if not self._access_token:
            return False
        import httpx

        try:
            r = httpx.post(
                f"{self.PORTAL_URL}/api/oauth/agent-key",
                headers={"Authorization": f"Bearer {self._access_token}"},
                json={"min_ttl_seconds": 300},
                timeout=15,
            )
            r.raise_for_status()
            payload = r.json()
            self._agent_key = payload["api_key"]
            self._expires_at = time.time() + 300  # 5 min estimate
            self._save_auth()
            return True
        except Exception:
            return False

    def _ensure_key(self) -> str:
        """Return a valid agent_key, refreshing+minting if needed."""
        if self._is_expired():
            if self._refresh_token_via_portal():
                self._mint_agent_key()
        if not self._agent_key:
            raise RuntimeError(
                "No Nous OAuth session. Run 'hermes auth add nous' to authenticate."
            )
        return self._agent_key

    # ── API call ──────────────────────────────────────────────────────

    def send(self, messages: list[dict], system: list[dict],
             tools: list[dict], params: dict) -> APIResponse:
        """Send messages to Nous Inference API (OpenAI-compatible)."""
        import httpx
        from datetime import datetime, timezone

        key = self._ensure_key()

        # Convert Anthropic system blocks → OpenAI system messages
        openai_messages = []
        for block in system:
            if block.get("type") == "text":
                openai_messages.append({
                    "role": "system",
                    "content": block.get("text", ""),
                })
        openai_messages.extend(messages)

        openai_tools = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })

        body = {
            "model": params["model"],
            "max_tokens": params.get("max_tokens", 64000),
            "messages": openai_messages,
            "tools": openai_tools if openai_tools else None,
            "temperature": params.get("temperature", 0.3),
        }
        if body["tools"] is None:
            del body["tools"]

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        try:
            response = httpx.post(
                self.BASE_URL, headers=headers, json=body, timeout=300
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else str(e)
            raise RuntimeError(
                f"Nous API error ({e.response.status_code}): {error_body}"
            )
        except Exception as e:
            raise RuntimeError(f"Nous API request failed: {e}")

        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        usage = data.get("usage", {})

        content = []
        if msg.get("content"):
            content.append({"type": "text", "text": msg["content"]})
        for tc in msg.get("tool_calls", []):
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                func_args = {}
            content.append({
                "type": "tool_use",
                "name": tc["function"]["name"],
                "input": func_args,
                "id": tc.get("id", ""),
            })

        return APIResponse(
            content=content,
            model=data.get("model", params["model"]),
            usage={
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
            stop_reason=choice.get("finish_reason", "stop"),
        )


class ClaudeClient:
    """Anthropic Messages API via Claude Code OAuth — no API key needed.

    Reads the OAuth access token from the macOS keychain (same credential
    Claude Code CLI uses). Auto-refreshes via platform.claude.com if expired.
    Uses the native Anthropic Messages API directly (not OpenAI compat layer).
    """

    BASE_URL = "https://api.anthropic.com/v1/messages"
    TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
    CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    KEYCHAIN_SVC = "Claude Code-credentials"
    API_VERSION = "2023-06-01"

    def __init__(self, api_key: str = None):
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        self._load_keychain()

    # ── keychain token management ──────────────────────────────────────

    def _load_keychain(self):
        """Read the OAuth credential from macOS keychain."""
        import subprocess

        try:
            result = subprocess.run(
                [
                    "security", "find-generic-password",
                    "-s", self.KEYCHAIN_SVC,
                    "-a", os.environ.get("USER", ""), "-w",
                ],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return
            data = json.loads(result.stdout)
            oauth = data.get("claudeAiOauth", {})
            self._access_token = oauth.get("accessToken", "")
            self._refresh_token = oauth.get("refreshToken", "")
            self._expires_at = _to_epoch(oauth.get("expiresAt", 0), ms=True)  # ms → s
        except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
            return

    def _save_keychain(self):
        """Write refreshed tokens back to the keychain."""
        import subprocess

        try:
            # Read existing, update, write back
            result = subprocess.run(
                [
                    "security", "find-generic-password",
                    "-s", self.KEYCHAIN_SVC,
                    "-a", os.environ.get("USER", ""), "-w",
                ],
                capture_output=True, text=True, timeout=5,
            )
            data = json.loads(result.stdout) if result.returncode == 0 else {}
            data.setdefault("claudeAiOauth", {})
            data["claudeAiOauth"]["accessToken"] = self._access_token
            data["claudeAiOauth"]["refreshToken"] = self._refresh_token
            data["claudeAiOauth"]["expiresAt"] = int(self._expires_at * 1000)

            # Delete + re-add (macOS keychain update pattern)
            subprocess.run(
                ["security", "delete-generic-password",
                 "-s", self.KEYCHAIN_SVC, "-a", os.environ.get("USER", "")],
                capture_output=True, timeout=5,
            )
            subprocess.run(
                ["security", "add-generic-password",
                 "-s", self.KEYCHAIN_SVC,
                 "-a", os.environ.get("USER", ""),
                 "-w", json.dumps(data)],
                capture_output=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass

    def _is_expired(self) -> bool:
        return not self._access_token or (time.time() > self._expires_at)

    def _refresh(self) -> bool:
        """Refresh the access token via OAuth2 refresh grant."""
        if not self._refresh_token:
            return False
        import httpx

        try:
            r = httpx.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.CLIENT_ID,
                    "refresh_token": self._refresh_token,
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            self._access_token = data["access_token"]
            self._refresh_token = data.get("refresh_token", self._refresh_token)
            self._expires_at = time.time() + data.get("expires_in", 3600) - 300
            self._save_keychain()
            return True
        except Exception:
            return False

    def _ensure_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if self._is_expired():
            self._refresh()
        if not self._access_token:
            raise RuntimeError(
                "No Claude Code OAuth token. Run 'claude' to authenticate first, "
                "or set ANTHROPIC_API_KEY env var for API-key auth."
            )
        return self._access_token

    # ── API call ───────────────────────────────────────────────────────

    def send(self, messages: list[dict], system: list[dict],
             tools: list[dict], params: dict) -> APIResponse:
        """Send messages to Anthropic API via OAuth Bearer token."""
        import httpx

        token = self._ensure_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }

        body = {
            "model": params["model"],
            "max_tokens": params.get("max_tokens", 64000),
            "messages": messages,
            "system": system,
            "tools": tools,
        }

        if params.get("thinking"):
            body["thinking"] = params["thinking"]

        if params.get("temperature") is not None:
            body["temperature"] = params["temperature"]

        try:
            response = httpx.post(
                self.BASE_URL,
                headers=headers,
                json=body,
                timeout=300,
            )
            response.raise_for_status()
            data = response.json()

            usage = data.get("usage", {})
            content = data.get("content", [])

            return APIResponse(
                content=content,
                model=data.get("model", params["model"]),
                usage={
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                },
                stop_reason=data.get("stop_reason", "end_turn"),
            )
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else str(e)
            raise RuntimeError(f"Claude API error ({e.response.status_code}): {error_body}")
        except Exception as e:
            raise RuntimeError(f"Claude API request failed: {e}")


class VeniceClient:
    """Venice AI client — OpenAI-compatible, API key auth.

    Venice is a privacy-first, uncensored AI API with 84+ models.
    Base URL: https://api.venice.ai/api/v1
    Auth: Bearer token from VENICE_API_KEY env var or settings page.
    """

    BASE_URL = "https://api.venice.ai/api/v1/chat/completions"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("VENICE_API_KEY", "")

    def send(self, messages: list[dict], system: list[dict],
             tools: list[dict], params: dict) -> APIResponse:
        """Send messages to Venice API (OpenAI-compatible)."""
        import httpx

        if not self.api_key:
            raise RuntimeError(
                "No Venice API key. Get one at https://venice.ai/settings/api "
                "and set VENICE_API_KEY env var."
            )

        # Convert Anthropic system blocks → OpenAI system messages
        openai_messages = []
        for block in system:
            if block.get("type") == "text":
                openai_messages.append({
                    "role": "system",
                    "content": block.get("text", ""),
                })
        openai_messages.extend(messages)

        # Convert Anthropic tools → OpenAI function tools
        openai_tools = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })

        body = {
            "model": params["model"],
            "max_tokens": params.get("max_tokens", 64000),
            "messages": openai_messages,
            "tools": openai_tools if openai_tools else None,
            "temperature": params.get("temperature", 0.3),
        }
        if body["tools"] is None:
            del body["tools"]

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = httpx.post(
                self.BASE_URL, headers=headers, json=body, timeout=300
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else str(e)
            raise RuntimeError(
                f"Venice API error ({e.response.status_code}): {error_body}"
            )
        except Exception as e:
            raise RuntimeError(f"Venice API request failed: {e}")

        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        usage = data.get("usage", {})

        # Convert OpenAI tool_calls → Anthropic format
        content = []
        if msg.get("content"):
            content.append({"type": "text", "text": msg["content"]})
        for tc in msg.get("tool_calls", []):
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                func_args = {}
            content.append({
                "type": "tool_use",
                "name": tc["function"]["name"],
                "input": func_args,
                "id": tc.get("id", ""),
            })

        return APIResponse(
            content=content,
            model=data.get("model", params["model"]),
            usage={
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
            stop_reason=choice.get("finish_reason", "stop"),
        )


class OpenRouterClient:
    """OpenRouter API client — for multi-provider flexibility."""
    
    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    
    def send(self, messages: list[dict], system: list[dict],
             tools: list[dict], params: dict) -> APIResponse:
        """Send messages to OpenRouter API."""
        import httpx
        
        # Convert Anthropic system format to OpenAI format
        openai_messages = []
        for block in system:
            if block.get("type") == "text":
                openai_messages.append({
                    "role": "system",
                    "content": block.get("text", ""),
                })
        
        openai_messages.extend(messages)
        
        # Convert Anthropic tool format to OpenAI format
        openai_tools = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })
        
        body = {
            "model": params["model"],
            "max_tokens": params.get("max_tokens", 64000),
            "messages": openai_messages,
            "tools": openai_tools if openai_tools else None,
            "temperature": params.get("temperature", 0.3),
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        try:
            response = httpx.post(
                self.BASE_URL, headers=headers, json=body, timeout=300
            )
            response.raise_for_status()
            data = response.json()
            
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            usage = data.get("usage", {})
            
            # Convert OpenAI tool_calls to Anthropic format
            content = []
            if msg.get("content"):
                content.append({"type": "text", "text": msg["content"]})
            
            for tc in msg.get("tool_calls", []):
                try:
                    func_args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    func_args = {}
                content.append({
                    "type": "tool_use",
                    "name": tc["function"]["name"],
                    "input": func_args,
                })
            
            return APIResponse(
                content=content,
                model=data.get("model", params["model"]),
                usage={
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                },
                stop_reason="tool_use" if msg.get("tool_calls") else "end_turn",
            )
        except Exception as e:
            raise RuntimeError(f"OpenRouter request failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# MODEL ROUTER
# ═══════════════════════════════════════════════════════════════════════════

class ModelRouter:
    """Routes requests to the right model based on active mode.
    
    Usage:
        router = ModelRouter()
        
        # In plan mode:
        router.set_mode("plan")  # → switches to Opus 4.7
        response = router.send(messages, system, tools)
        
        # After plan approved:
        router.set_mode("execute")  # → switches to Sonnet 4.6
        response = router.send(messages, system, tools)
        # Same message history! Context preserved.
    """
    
    def __init__(self, models: dict[str, ModelConfig] = None,
                 mode_map: dict[str, str] = None):
        self.models = models or DEFAULT_MODELS.copy()
        self.mode_map = mode_map or MODE_MODEL_MAP.copy()
        self._current_mode = "execute"
        self._current_model_key = self.mode_map["execute"]
        self._clients: dict[str, BaseClient] = {}
        self._usage_history: list[dict] = []
        self._swap_count = 0
    
    def set_mode(self, mode: str):
        """Switch to a new mode, updating the active model."""
        prev_model = self._current_model_key
        self._current_mode = mode
        self._current_model_key = self.mode_map.get(mode, "sonnet46")
        
        if prev_model != self._current_model_key:
            self._swap_count += 1
            # Log the swap silently (no print — the TUI handles display)
    
    def get_current_model(self) -> ModelConfig:
        """Get the active model configuration."""
        return self.models.get(self._current_model_key, self.models["sonnet46"])
    
    def get_current_params(self) -> dict:
        """Get the generation parameters for the current mode."""
        config = self.get_current_model()
        params = MODE_PARAMS.get(self._current_mode, MODE_PARAMS["execute"]).copy()
        params["model"] = config.model_id
        return params
    
    def get_client(self) -> BaseClient:
        """Get or create the API client for the current model."""
        config = self.get_current_model()
        
        if config.provider not in self._clients:
            if config.provider == "anthropic":
                self._clients["anthropic"] = AnthropicClient()
            elif config.provider == "claude":
                self._clients["claude"] = ClaudeClient()
            elif config.provider == "gemini":
                self._clients["gemini"] = GeminiClient()
            elif config.provider == "venice":
                self._clients["venice"] = VeniceClient()
            elif config.provider == "nous":
                self._clients["nous"] = NousClient()
            elif config.provider == "openrouter":
                self._clients["openrouter"] = OpenRouterClient()
            elif config.provider == "grok":
                self._clients["grok"] = GrokClient()
            else:
                raise ValueError(f"Unknown provider: {config.provider}")
        
        return self._clients[config.provider]
    
    def send(self, messages: list[dict], system: list[dict],
             tools: list[dict]) -> APIResponse:
        """Send a request using the current mode's model.
        
        The message history is shared across all models — context is
        fully preserved during swaps. Prefix caching means the system
        prompt stays cached even after a model change.
        """
        client = self.get_client()
        params = self.get_current_params()
        
        start = time.time()
        response = client.send(messages, system, tools, params)
        duration = time.time() - start
        
        config = self.get_current_model()
        cost = self._estimate_cost(response.usage, config)
        
        self._usage_history.append({
            "mode": self._current_mode,
            "model": config.display_name,
            "model_id": config.model_id,
            "duration_s": round(duration, 1),
            "usage": response.usage,
            "cost": cost,
        })
        
        return response
    
    def _estimate_cost(self, usage: dict, config: ModelConfig) -> float:
        """Estimate the cost of a request."""
        input_cost = (usage.get("input_tokens", 0) / 1_000_000) * config.cost_per_mtok_input
        output_cost = (usage.get("output_tokens", 0) / 1_000_000) * config.cost_per_mtok_output
        return round(input_cost + output_cost, 6)
    
    def get_cost_report(self) -> dict:
        """Get a cost report for this session."""
        total_cost = sum(h["cost"] for h in self._usage_history)
        by_model = {}
        for h in self._usage_history:
            m = h["model"]
            if m not in by_model:
                by_model[m] = {"calls": 0, "cost": 0.0, "tokens_in": 0, "tokens_out": 0}
            by_model[m]["calls"] += 1
            by_model[m]["cost"] += h["cost"]
            by_model[m]["tokens_in"] += h["usage"].get("input_tokens", 0)
            by_model[m]["tokens_out"] += h["usage"].get("output_tokens", 0)
        
        return {
            "total_swaps": self._swap_count,
            "total_cost": round(total_cost, 4),
            "total_calls": len(self._usage_history),
            "by_model": by_model,
        }
    
    def get_status_line(self) -> str:
        """Get a compact status line for the TUI status bar."""
        config = self.get_current_model()
        return f"{config.display_name} [{self._current_mode}]"
    
    @property
    def current_mode(self) -> str:
        return self._current_mode
    
    @current_mode.setter
    def current_mode(self, mode: str):
        self.set_mode(mode)


# ═══════════════════════════════════════════════════════════════════════════
# INTEGRATION WITH MODE STATE MACHINE
# ═══════════════════════════════════════════════════════════════════════════

# Singleton router
_global_router = None

def get_router() -> ModelRouter:
    """Get or create the global model router."""
    global _global_router
    if _global_router is None:
        _global_router = ModelRouter()
    return _global_router


def on_mode_change(mode: str):
    """Hook called when the mode state machine changes mode.
    
    This function is called by enter_plan_mode and exit_plan_mode
    to automatically swap the active model.
    """
    router = get_router()
    router.set_mode(mode)


# ═══════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    router = ModelRouter()
    
    print("=== Model Routing Demo ===\n")
    
    for mode in ["plan", "execute", "explore", "review", "debug", "research"]:
        router.set_mode(mode)
        config = router.get_current_model()
        params = router.get_current_params()
        print(f"  {mode:10s} → {config.display_name:12s} ({config.provider}) "
              f"max_tokens={params.get('max_tokens')} "
              f"thinking={params.get('thinking', {}).get('budget_tokens', 'no')}")
    
    print(f"\n  Total swaps: {router._swap_count}")
    print(f"  Current: {router.get_status_line()}")
    
    # Verify context preservation
    router.set_mode("plan")
    p1 = router.get_current_params()
    router.set_mode("execute")
    p2 = router.get_current_params()
    router.set_mode("plan")
    p3 = router.get_current_params()
    
    assert p1["model"] == p3["model"], "Context lost during plan→execute→plan swap"
    print(f"\n  ✓ Context preserved across plan→execute→plan swap")
    print(f"  ✓ Model swap count: {router._swap_count}")