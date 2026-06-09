"""
Korgex TDD Self-Healing Engine — Relentless Test-Driven Repair.

Runs test commands inside the sandbox, captures failures, queries the configured
LLM for precise corrections, applies them, and loops until all tests pass.

Architecture:
    [Run Test Command] ──▶ (Exit = 0?) ──▶ [SUCCESS]
            │
            ▼ (Exit ≠ 0)
    [Extract Traceback] → [Read Target File] → [Read Context Files]
            │
            ▼
    [Query LLM for SEARCH/REPLACE patch] → [Apply Patch in Sandbox]
            │
            ▼
    [Rerun Test] (Loop up to MaxAttempts)
"""

import json
import re
from typing import Callable, Optional


def auto_heal_to_green(first_gate: dict, *, run_gate: Callable[[], dict],
                       heal_fn: Callable[[str], None],
                       record_event: Callable[..., int],
                       max_attempts: int = 3,
                       triggered_by: Optional[int] = None) -> dict:
    """Drive a RED test gate back to green, recording a verifiable repair trail.

    Given an already-failing `first_gate` ({passed, exit_code, output}), repeat
    up to `max_attempts`: call `heal_fn(failure_output)` (which spawns a healing
    subagent / applies a fix), re-run the gate via `run_gate()`, and record a
    `heal.attempt` event. On the first green, record `heal.resolved` and return;
    if attempts are exhausted while still red, record `heal.exhausted`.

    `record_event(tool_name, args, result, success, triggered_by) -> seq_id` is
    the ledger sink — because it's hash-chained, the whole repair becomes an
    auditable, replayable chain. Events are causally linked: the first attempt is
    triggered_by the red gate, each subsequent event by the prior one.

    Returns the final gate result.
    """
    gate = first_gate
    last_seq = triggered_by
    for attempt in range(1, max_attempts + 1):
        heal_fn(gate.get("output", ""))
        gate = run_gate()
        last_seq = record_event(
            "heal.attempt",
            {"attempt": attempt, "max_attempts": max_attempts},
            {"verdict": "PASSED" if gate["passed"] else "FAILED",
             "exit_code": gate["exit_code"]},
            gate["passed"], last_seq)
        if gate["passed"]:
            record_event("heal.resolved", {"attempts": attempt},
                         {"exit_code": 0}, True, last_seq)
            return gate
    record_event("heal.exhausted", {"attempts": max_attempts},
                 {"exit_code": gate["exit_code"]}, False, last_seq)
    return gate


class TDDHealer:
    """Self-healing test loop. Runs tests, parses failures, patches, retries."""
    
    def __init__(self, sandbox, api_client, model: str, max_attempts: int = 5):
        """
        Args:
            sandbox: The active Modal/Docker sandbox instance.
            api_client: OpenAI-compatible API client.
            model: LLM model name (e.g. 'gpt-4o', 'claude-sonnet-4').
            max_attempts: Max self-healing loops before giving up.
        """
        self.sandbox = sandbox
        self.client = api_client
        self.model = model
        self.max_attempts = max_attempts
    
    def heal(self, test_command: str, target_file: str, context_files: list = None) -> dict:
        """
        Main self-healing entry point.
        
        Args:
            test_command: The command to run tests (e.g. 'pytest tests/test_auth.py').
            target_file: The relative path to the file likely causing the issue.
            context_files: Additional files for context (test files, configs, etc.).
        
        Returns:
            dict with status, attempts, output.
        """
        context_files = context_files or []
        print(f"\n🚀 [TDD HEALER] Initiating self-healing loop: {test_command}")
        
        for attempt in range(1, self.max_attempts + 1):
            print(f"🔄 [TDD HEALER] Attempt {attempt}/{self.max_attempts}...")
            
            # 1. Run the tests in the sandbox
            test_result = self.sandbox.run(test_command)
            exit_code = test_result.get("exit_code", -1)
            stdout = test_result.get("stdout", "")
            stderr = test_result.get("stderr", "")
            
            # 2. Check for success
            if exit_code == 0:
                print("✅ [TDD HEALER] All tests passed!")
                return {
                    "status": "success",
                    "attempts": attempt,
                    "output": stdout,
                    "message": f"Tests passed after {attempt} attempt(s)",
                }
            
            print(f"❌ [TDD HEALER] Test failed (exit {exit_code}). Analyzing traceback...")
            
            # 3. Read the target file inside the sandbox
            file_read = self.sandbox.run(f"cat {target_file}")
            current_code = file_read.get("stdout", "")
            if not current_code:
                print(f"⚠️ [TDD HEALER] Could not read target file: {target_file}")
                continue
            
            # 4. Read context files
            context_contents = {}
            for c_file in context_files:
                c_read = self.sandbox.run(f"cat {c_file}")
                if c_read.get("stdout"):
                    context_contents[c_file] = c_read["stdout"]
            
            # 5. Request correction from LLM
            correction = self._request_correction(
                target_file=target_file,
                current_code=current_code,
                stdout=stdout,
                stderr=stderr,
                context_contents=context_contents,
            )
            
            if "error" in correction:
                print(f"⚠️ [TDD HEALER] LLM correction failed: {correction['error']}")
                continue
            
            patch_diff = correction.get("patch", "")
            explanation = correction.get("explanation", "")
            
            if not patch_diff:
                print("⚠️ [TDD HEALER] LLM returned empty patch")
                continue
            
            print(f"💡 [TDD HEALER] Fix: {explanation[:120]}...")
            
            # 6. Apply the patch in the sandbox
            patch_result = self._apply_patch_in_sandbox(target_file, patch_diff)
            
            if not patch_result.get("success"):
                print(f"⚠️ [TDD HEALER] Patch failed: {patch_result.get('error', 'unknown')}")
                continue
        
        return {
            "status": "failure",
            "attempts": self.max_attempts,
            "error": f"Tests still failing after {self.max_attempts} healing attempts",
        }
    
    def _request_correction(self, target_file: str, current_code: str,
                             stdout: str, stderr: str,
                             context_contents: dict) -> dict:
        """Query the LLM for a SEARCH/REPLACE patch to fix the test failure."""
        
        system_prompt = (
            "You are Korgex's Self-Healing Compiler. Your goal is to analyze test failures "
            "and output a precise SEARCH/REPLACE block to correct the code. "
            "Respond ONLY with valid JSON."
        )
        
        user_content = (
            f"Target File: `{target_file}`\n\n"
            f"=== CURRENT FILE CONTENT ===\n{current_code}\n\n"
        )
        
        for filename, content in context_contents.items():
            user_content += f"=== CONTEXT FILE: `{filename}` ===\n{content}\n\n"
        
        user_content += (
            f"=== TEST STDOUT ===\n{stdout}\n\n"
            f"=== TEST STDERR ===\n{stderr}\n\n"
            "Analyze the traceback. Identify the bug. "
            "Respond with a JSON object with two fields:\n"
            "1. 'explanation': A brief description of what was wrong.\n"
            "2. 'patch': A precise <<<<<<< SEARCH / ======= / >>>>>>> REPLACE block.\n"
            "Format as valid JSON."
        )
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            return {"error": str(e)}
    
    def _apply_patch_in_sandbox(self, filepath: str, patch: str) -> dict:
        """Write patch to temp file in sandbox and apply via diff engine."""
        temp_patch = ".korgex_heal.patch"
        
        # Write patch file to sandbox
        self.sandbox.run(
            f"cat << 'KORGEOF' > {temp_patch}\n{patch}\nKORGEOF"
        )
        
        # Apply using the diff engine
        cmd = (
            f"python3 -c \""
            f"import sys; sys.path.insert(0, '.'); "
            f"from src.diff_engine import apply_patch; "
            f"result = apply_patch('{filepath}', '{temp_patch}'); "
            f"print(result.get('success', False)); "
            f"sys.exit(0 if result.get('success') else 1)"
            f"\""
        )
        result = self.sandbox.run(cmd)
        
        # Cleanup
        self.sandbox.run(f"rm -f {temp_patch}")
        
        if result.get("exit_code") == 0:
            return {"success": True}
        
        return {
            "success": False,
            "error": result.get("stderr", "Patch application failed"),
        }


def extract_traceback_info(stderr: str) -> dict:
    """Parse a Python traceback to extract failed file, line, and error type."""
    info = {"file": None, "line": None, "error_type": None, "message": None}
    
    # Match: File "...", line N, in function
    for match in re.finditer(r'File "([^"]+)", line (\d+), in (\w+)', stderr):
        info["file"] = match.group(1)
        info["line"] = int(match.group(2))
    
    # Match: ErrorType: message
    for match in re.finditer(r'(\w+(?:Error|Exception|Warning)):\s*(.*)', stderr):
        info["error_type"] = match.group(1)
        info["message"] = match.group(2).strip()
        break
    
    # Match FAILED test name: pytest format
    for match in re.finditer(r'FAILED\s+(\S+)', stderr):
        if not info.get("failed_test"):
            info["failed_test"] = match.group(1)
    
    return info