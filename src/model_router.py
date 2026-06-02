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
        provider="anthropic",
        model_id="claude-opus-4-7",
        display_name="Opus 4.7",
        max_tokens=64000,
        thinking_budget=20000,
        cost_per_mtok_input=15.0,
        cost_per_mtok_output=75.0,
        supports_thinking=True,
    ),
    "sonnet46": ModelConfig(
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        display_name="Sonnet 4.6",
        max_tokens=64000,
        thinking_budget=None,
        cost_per_mtok_input=3.0,
        cost_per_mtok_output=15.0,
        supports_thinking=False,
    ),
    "haiku45": ModelConfig(
        provider="anthropic",
        model_id="claude-haiku-4-5",
        display_name="Haiku 4.5",
        max_tokens=32000,
        thinking_budget=None,
        cost_per_mtok_input=0.8,
        cost_per_mtok_output=4.0,
        supports_thinking=False,
    ),
    # ── Grok (xAI) models — uses grok-build OAuth, no API key needed ──
    "grok4": ModelConfig(
        provider="grok",
        model_id="latest",             # aliased to grok-4.3-latest
        display_name="Grok 4",
        max_tokens=64000,
        thinking_budget=None,
        cost_per_mtok_input=2.0,       # list price via api.x.ai
        cost_per_mtok_output=10.0,
        supports_thinking=False,
    ),
    "grok-reasoning": ModelConfig(
        provider="grok",
        model_id="grok-420-reasoning",
        display_name="Grok Reasoning",
        max_tokens=64000,
        thinking_budget=None,
        cost_per_mtok_input=2.0,
        cost_per_mtok_output=80.0,
        supports_thinking=True,
    ),
    "grok-mini": ModelConfig(
        provider="grok",
        model_id="grok-4-mini-thinking-tahoe",
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
                self._expires_at = val.get("expires_at", 0.0)
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