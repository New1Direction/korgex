"""
Seluj — Core Agent Loop.

Orchestrates the full Jules workflow:
Explore → Plan → Approve → Execute → Verify → Pre-commit → Submit
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

from src.tool_base import (
    get_tool_schemas, dispatch_tool, get_context,
    TOOL_REGISTRY
)
import src.tools_impl as tools_impl

# Load the Seluj prompt from prompt.md if it exists.
# If missing, use a minimal fallback so the code still runs.
# This keeps the prompt protected from public repos.
_PROMPT_PATH = Path(__file__).parent.parent / "prompt.md"
if _PROMPT_PATH.exists():
    SYSTEM_PROMPT = _PROMPT_PATH.read_text().strip()
else:
    SYSTEM_PROMPT = "You are Seluj, an extremely skilled software engineer. Complete the user's coding task using the tools available to you. Explore the codebase first, formulate a plan, execute with verification, and submit when done."


class SelujAgent:
    """The Seluj agent loop."""
    
    def __init__(self, repo_root: str = None, model: str = None):
        self.repo_root = repo_root or os.getcwd()
        self.model = model or os.environ.get("SELUJ_MODEL", "deepseek/deepseek-v4-flash")
        self.provider = os.environ.get("SELUJ_PROVIDER", "nous")
        self.context = get_context()
        self.context["repo_root"] = self.repo_root
        self.conversation_history = []
        
        # Initialize tools
        tools_impl.init(self.repo_root)
        
    def get_system_message(self) -> dict:
        """Build the system message with prompt + tool schemas."""
        tool_schemas = get_tool_schemas()
        tools_json = json.dumps(tool_schemas, indent=2)
        
        return {
            "role": "system",
            "content": f"{SYSTEM_PROMPT}\n\nAvailable Tools:\n{tools_json}"
        }
    
    def run_task(self, task_description: str) -> dict:
        """Run a full agent task: explore → plan → execute → submit."""
        messages = [self.get_system_message()]
        
        # Step 1: User prompt
        messages.append({"role": "user", "content": task_description})
        
        # Step 2: Agent exploration loop (limited iterations)
        max_iterations = int(os.environ.get("SELUJ_MAX_ITERATIONS", "50"))
        
        for iteration in range(max_iterations):
            # Call the LLM
            response = self._call_llm(messages)
            
            # Check for tool calls
            tool_calls = response.get("tool_calls", [])
            
            if not tool_calls:
                # No tools means final response
                assistant_msg = {
                    "role": "assistant",
                    "content": response.get("content", "")
                }
                messages.append(assistant_msg)
                
                # Check if done
                if response.get("content", "").strip():
                    return {
                        "result": response["content"],
                        "iterations": iteration + 1,
                        "messages": messages,
                    }
                continue
            
            # Process tool calls
            for tool_call in tool_calls:
                tool_name = tool_call.get("name", "")
                tool_args = json.loads(tool_call.get("arguments", "{}")) if isinstance(tool_call.get("arguments"), str) else tool_call.get("arguments", {})
                
                # Execute tool
                tool_result = dispatch_tool(tool_name, tool_args, self.context)
                
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_call.get("id", f"call_{iteration}_{tool_name}"),
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(tool_args),
                        }
                    }]
                })
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", f"call_{iteration}_{tool_name}"),
                    "content": json.dumps(tool_result, default=str),
                })
        
        return {
            "result": "Max iterations reached",
            "iterations": max_iterations,
            "messages": messages,
        }
    
    def _call_llm(self, messages: list) -> dict:
        """Call the configured LLM. Falls back to mock mode if no API available."""
        try:
            from openai import OpenAI
            
            client = OpenAI(
                base_url=os.environ.get(
                    "SELUJ_API_URL",
                    "https://inference-api.provider.com/v1"
                ),
                api_key=os.environ.get("SELUJ_API_KEY", ""),
            )
            
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=[],
                max_tokens=4096,
                temperature=0.7,
            )
            
            choice = response.choices[0]
            msg = choice.message
            
            result = {"content": msg.content or ""}
            
            if msg.tool_calls:
                result["tool_calls"] = [
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                    for tc in msg.tool_calls
                ]
            
            return result
            
        except Exception as e:
            # Mock mode for testing without API
            return self._mock_llm(messages)
    
    def _mock_llm(self, messages: list) -> dict:
        """Mock LLM for testing without API credentials."""
        last_msg = messages[-1]["content"] if messages else ""
        
        # Simple mock: just say hi and suggest exploring
        return {
            "content": "I'll start by exploring the codebase to understand the project structure.",
            "tool_calls": [
                {
                    "id": "mock_1",
                    "name": "list_files",
                    "arguments": json.dumps({"path": "."}),
                }
            ]
        }


def print_tool_schemas():
    """Print all tool schemas in Jules' array format."""
    schemas = get_tool_schemas()
    print(json.dumps(schemas, indent=2))


def main():
    """Entry point when run as module."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Seluj — Autonomous AI Software Engineer")
    parser.add_argument("task", nargs="?", help="Task description")
    parser.add_argument("--repo", "-r", help="Repository root path")
    parser.add_argument("--model", "-m", help="Model to use")
    parser.add_argument("--schemas", action="store_true", help="Print tool schemas and exit")
    parser.add_argument("--init", action="store_true", help="Initialize AGENTS.md in repo")
    
    args = parser.parse_args()
    
    if args.schemas:
        print_tool_schemas()
        return
    
    if args.init:
        agents_content = """# Seluj - Autonomous AI Software Engineer

You are Seluj, an extremely skilled software engineer.
Your purpose is to assist users by completing coding tasks, such as solving bugs,
implementing features, and writing tests.

## Core Directives
1. PLAN FIRST: Explore the codebase (list_files, read_file). Read this file and README.md.
   Ask clarifying questions. Articulate the plan using set_plan.
2. VERIFY WORK: After every modification, use read_file or list_files to confirm success.
   Do NOT mark a plan step complete until you've verified.
3. EDIT SOURCE, NOT ARTIFACTS: If a file is a build artifact (dist/, build/, node_modules/,
   __pycache__/, .next/), trace back to its source.
4. PROACTIVE TESTING: Find and run relevant tests. Plans should include testing steps.
5. DIAGNOSE BEFORE CHANGING: Read error logs and configs before installing packages.
6. SOLVE AUTONOMOUSLY: Ask only if ambiguous, stuck after multiple attempts, or scope-changing.

## Git Merge Diff Format
Use SEARCH/REPLACE blocks with exact markers:
```
<<<<<<< SEARCH
  old code here
=======
  new code here
>>>>>>> REPLACE
```

## Plan Format
Numbered steps with Markdown. Include a pre-commit step described as:
"ensure proper testing, verification, review, and reflection are done"
Do NOT mention tool names in plan steps.
"""
        dst = os.path.join(os.getcwd(), "AGENTS.md")
        with open(dst, "w") as f:
            f.write(agents_content)
        print(f"Created {dst}")
        return
    
    if not args.task:
        parser.print_help()
        return
    
    agent = SelujAgent(repo_root=args.repo, model=args.model)
    result = agent.run_task(args.task)
    
    print(f"\n{'='*60}")
    print(f"SELUJ RESULT ({result['iterations']} iterations)")
    print(f"{'='*60}")
    print(result["result"])


if __name__ == "__main__":
    main()