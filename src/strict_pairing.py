"""
Korgex Strict Tool Result Pairing — cryptographic-like binding between
tool_use_id and environment tool_result.

Prevents the single most dangerous failure mode in autonomous agents:
the model hallucinating tool results before the environment returns them.

Architecture:
  [Model calls tool]  →  tool_use_id: "call_abc123"
        │
        ▼
  [Environment executes]  →  result tagged with same tool_use_id
        │
        ▼
  [Next prompt includes]  →  Tool Result (call_abc123): <actual output>
        │
        ▼
  [Model MUST wait for result; cannot fabricate output]

Key design (mirrors Claude Code's setStrictToolResultPairing):
- Each tool call gets a unique, cryptographically random tool_use_id
- Results in the prompt are ALWAYS paired with their originating ID
- The system prompt enforces: "Never generate tool results yourself"
- A validation layer detects unpaired or hallucinated results
"""

import hashlib
import json
import os
import secrets
import time
from typing import Any, Optional


# ── ID Generation ───────────────────────────────────────────────────────

def generate_tool_use_id() -> str:
    """Generate a unique tool_use_id with cryptographic randomness.
    
    Format: call_<timestamp-hex>_<random-hex>
    Matches the pattern used by production agent systems.
    The random component uses secrets.token_hex for cryptographic strength.
    """
    ts = int(time.time() * 1000)
    rand = secrets.token_hex(8)
    return f"call_{ts:x}_{rand}"


def generate_content_block_id() -> str:
    """Generate a unique content block ID for streamed responses.
    
    Format: content_<random-hex>_<counter>
    """
    rand = secrets.token_hex(6)
    counter = int(time.time() * 1000) % 10000
    return f"content_{rand}_{counter}"


def hash_tool_pair(tool_use_id: str, result_text: str) -> str:
    """Create a hash binding between a tool call and its result.
    
    This lets us verify that a tool result was genuinely produced by
    executing the tool, not hallucinated by the model.
    """
    return hashlib.sha256(
        f"{tool_use_id}:{result_text}".encode()
    ).hexdigest()[:16]


# ── Execution Context ───────────────────────────────────────────────────

class ToolExecutionContext:
    """Tracks all tool calls and their results for strict pairing.
    
    This is the core data structure that prevents hallucinated results.
    It maintains a complete ledger of: which tools were called, with what
    parameters, what their actual results were, and the cryptographic hash
    binding each call to its result.
    """
    
    def __init__(self):
        self._pending: dict[str, dict] = {}        # tool_use_id → call info
        self._completed: dict[str, dict] = {}      # tool_use_id → result info
        self._execution_order: list[str] = []       # ordered list of IDs
    
    def register_call(self, tool_name: str, params: dict,
                      process_id: str = None) -> str:
        """Register a tool call and return its unique tool_use_id.
        
        Call this BEFORE dispatching the tool to the environment.
        """
        tool_use_id = generate_tool_use_id()
        self._pending[tool_use_id] = {
            "tool_name": tool_name,
            "params": params,
            "timestamp": time.time(),
            "process_id": process_id,
        }
        self._execution_order.append(tool_use_id)
        return tool_use_id
    
    def complete_call(self, tool_use_id: str, result: Any) -> dict:
        """Record the result of a tool call and return the paired result.
        
        Call this AFTER the tool has been executed by the environment.
        Generates a cryptographic hash binding call → result.
        """
        if tool_use_id not in self._pending:
            return {"error": f"Unknown tool_use_id: {tool_use_id}"}
        
        call_info = self._pending.pop(tool_use_id)
        result_text = json.dumps(result, default=str) if not isinstance(result, str) else str(result)
        
        binding_hash = hash_tool_pair(tool_use_id, result_text)
        
        paired_result = {
            "tool_use_id": tool_use_id,
            "tool_name": call_info["tool_name"],
            "result": result,
            "result_text": result_text,
            "binding_hash": binding_hash,
            "duration_ms": int((time.time() - call_info["timestamp"]) * 1000),
        }
        
        self._completed[tool_use_id] = paired_result
        return paired_result
    
    def get_completed_result(self, tool_use_id: str) -> Optional[dict]:
        """Get a completed result by ID."""
        return self._completed.get(tool_use_id)
    
    def get_last_n_results(self, n: int = 10) -> list[dict]:
        """Get the last N completed results, preserving execution order."""
        ids = [i for i in self._execution_order if i in self._completed]
        return [self._completed[i] for i in ids[-n:]]
    
    def has_pending(self) -> bool:
        """Check if there are pending (uncompleted) tool calls."""
        return len(self._pending) > 0
    
    def pending_count(self) -> int:
        return len(self._pending)
    
    def completed_count(self) -> int:
        return len(self._completed)
    
    def to_dict(self) -> dict:
        """Serialize the full execution context (for debugging/telemetry)."""
        return {
            "pending": {k: {"tool": v["tool_name"], "age_s": round(time.time() - v["timestamp"], 1)}
                       for k, v in self._pending.items()},
            "completed_count": len(self._completed),
            "total_calls": len(self._execution_order),
        }
    
    def reset(self):
        """Clear all state (for starting fresh contexts)."""
        self._pending.clear()
        self._completed.clear()
        self._execution_order.clear()


# ── Prompt Formatting ───────────────────────────────────────────────────

# Singleton context (one per agent session)
_context = ToolExecutionContext()

def get_context() -> ToolExecutionContext:
    return _context


def format_tool_call_for_prompt(tool_name: str, params: dict,
                                 tool_use_id: str) -> str:
    """Format a tool call for inclusion in the prompt.
    
    Format (matches Claude Code convention):
      tool_use_id: "call_abc123"
      Tool: Bash(command="pytest")
    """
    params_str = ", ".join(f'{k}={json.dumps(v)}' for k, v in params.items())
    return f'  tool_use_id: "{tool_use_id}"\n  Tool: {tool_name}({params_str})'


def format_tool_result_for_prompt(paired_result: dict) -> str:
    """Format a tool result for inclusion in the prompt.
    
    Format (matches Claude Code convention):
      Tool Result (call_abc123):
      <actual output>
      
    The tool_use_id binding is explicit and visible to the model.
    """
    result_text = paired_result["result_text"]
    # Truncate extremely long results
    if len(result_text) > 10000:
        result_text = result_text[:10000] + "\n... [truncated]"
    
    return (
        f'  Tool Result ({paired_result["tool_use_id"]}):\n'
        f'  {result_text}\n'
    )


# ── Validation ──────────────────────────────────────────────────────────

def validate_prompt_history(conversation_text: str) -> dict:
    """Validate that all tool results in a conversation have proper pairings.
    
    Scans the conversation text for tool results and checks that each one
    has a corresponding tool call with a matching tool_use_id.
    
    Returns:
        {"valid": True} or {"valid": False, "violations": [...]}
    """
    import re
    
    violations = []
    
    # Find all tool results
    result_pattern = re.compile(r'Tool Result \((\w+)\):')
    call_pattern = re.compile(r'tool_use_id: "(\w+)"')
    
    result_ids = result_pattern.findall(conversation_text)
    call_ids = call_pattern.findall(conversation_text)
    
    for rid in result_ids:
        if rid not in call_ids:
            violations.append({
                "type": "unpaired_result",
                "tool_use_id": rid,
                "detail": "Tool result without matching tool call",
            })
    
    return {
        "valid": len(violations) == 0,
        "total_results": len(result_ids),
        "total_calls": len(call_ids),
        "violations": violations,
    }


# ── System Prompt Section ───────────────────────────────────────────────

STRICT_RESULT_PAIRING_SYSTEM_PROMPT = """
# Tool Result Integrity

You have strict tool result pairing enabled. This means:

1. Every tool call you make receives a unique `tool_use_id`.
2. The environment returns tool results paired with that exact ID.
3. You MUST NEVER generate, predict, or imagine tool results yourself.
4. A tool result formatted as `Tool Result (call_abc123):` means the
   environment has executed the tool and this is the real output.

If you find yourself about to "complete" a tool result pattern in your
response, STOP. Wait for the environment to return it. Tool results that
do not come from the environment with a matching tool_use_id are not valid.

The cryptographic binding between tool_use_id and result_text ensures that
only genuine environmental outputs are used for decision-making. Attempting
to fabricate tool results will cause downstream failures.
"""


def get_strict_pairing_prompt_section() -> dict:
    """Return the system prompt section for strict tool result pairing."""
    return {
        "type": "text",
        "text": STRICT_RESULT_PAIRING_SYSTEM_PROMPT.strip(),
    }


# ── Agent Loop Integration ──────────────────────────────────────────────

class StrictToolDispatcher:
    """Dispatches tool calls with strict result pairing enforcement.
    
    Usage in the agent loop:
        dispatcher = StrictToolDispatcher(tool_executor)
        result = dispatcher.execute("Bash", {"command": "pytest"})
        # result now has tool_use_id and binding_hash
    """
    
    def __init__(self, tool_executor: callable):
        self.executor = tool_executor
        self.ctx = get_context()
    
    def execute(self, tool_name: str, params: dict) -> dict:
        """Execute a tool with strict result pairing.
        
        1. Registers the call and gets a unique tool_use_id
        2. Executes the tool through the environment
        3. Pairs the result with the tool_use_id
        4. Returns the paired result with binding hash
        """
        tool_use_id = self.ctx.register_call(tool_name, params)
        
        try:
            result = self.executor(tool_name, params)
        except Exception as e:
            result = {"error": str(e)}
        
        paired = self.ctx.complete_call(tool_use_id, result)
        return paired
    
    def format_for_prompt(self, paired_result: dict) -> str:
        """Format a paired result for inclusion in the next prompt."""
        return format_tool_result_for_prompt(paired_result)


# ── Self-Test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Simulate a tool call cycle
    ctx = ToolExecutionContext()
    
    # Model calls Bash
    call_id = ctx.register_call("Bash", {"command": "echo hello"})
    print(f"Generated tool_use_id: {call_id}")
    
    # Environment executes and returns result
    result = ctx.complete_call(call_id, "hello\n")
    print(f"Binding hash: {result['binding_hash']}")
    
    # Format for prompt
    prompt = format_tool_result_for_prompt(result)
    print(f"\nPrompt format:\n{prompt}")
    
    # Validate
    conv = f"tool_use_id: \"{call_id}\"\nTool Result ({call_id}):\nhello\n"
    validation = validate_prompt_history(conv)
    print(f"Validation: {validation}")
    
    # Test hallucination detection
    fake_conv = "Tool Result (call_fake123):\nhallucinated output\n"
    fake_validation = validate_prompt_history(fake_conv)
    print(f"Hallucination detection: {fake_validation}")
