"""
Korgex Interactive TUI — Streaming, Diffs, Spinners, Interrupts.

Bridges the gap between a headless backend and a polished interactive feel.

Features:
1. SSE stream parser → interleaved thinking (gray) + text (normal) + tool status
2. Rich unified diffs before Edit/Write execution, with [y/N] prompt
3. SIGINT trap → UserInterrupt event (not kill)
4. Ephemeral spinners that resolve to compact checkmarks

Parses the documented Anthropic-style streaming SSE format (8 event types),
as used by korgex's streaming bridge.
"""

import difflib
import json
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

# ── Rich imports ────────────────────────────────────────────────────────
from rich.console import Console
from rich.prompt import Confirm

# force_terminal so rich still emits color when stdout is wrapped by
# prompt_toolkit's patch_stdout (which makes the stream look non-tty otherwise).
console = Console(force_terminal=True)


# ═══════════════════════════════════════════════════════════════════════════
# 1. SSE STREAM PARSER — the streaming 8-event SSE format
# ═══════════════════════════════════════════════════════════════════════════

class SSEEvent(Enum):
    MESSAGE_START = "message_start"
    CONTENT_BLOCK_START = "content_block_start"
    CONTENT_BLOCK_DELTA = "content_block_delta"
    CONTENT_BLOCK_STOP = "content_block_stop"
    MESSAGE_DELTA = "message_delta"
    MESSAGE_STOP = "message_stop"
    PING = "ping"
    ERROR = "error"


class ContentBlockType(Enum):
    TEXT = "text"
    THINKING = "thinking"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"


class DeltaType(Enum):
    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"
    INPUT_JSON_DELTA = "input_json_delta"


@dataclass
class SSEMessage:
    """A single parsed SSE event from the stream."""
    event: SSEEvent
    data: dict = field(default_factory=dict)
    raw: str = ""


def parse_sse_stream(raw_stream: str) -> list[SSEMessage]:
    """Parse a raw SSE stream into structured events.
    
    Handles both the Anthropic API format documented at:
    https://docs.anthropic.com/en/api/messages-streaming
    
    Plus a few synthesized events for tool status.
    """
    messages = []
    current_event = None
    current_data = []

    for line in raw_stream.split("\n"):
        line = line.rstrip()
        
        if line.startswith("event:"):
            # Save previous event if there was one
            if current_event is not None:
                messages.append(SSEMessage(
                    event=SSEEvent(current_event),
                    data=_parse_sse_data("\n".join(current_data)),
                    raw="\n".join(current_data),
                ))
                current_data = []
            current_event = line[6:].strip()
            
        elif line.startswith("data:"):
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                messages.append(SSEMessage(event=SSEEvent.MESSAGE_STOP))
                current_event = None
                current_data = []
            else:
                current_data.append(data_str)
                
        elif line.startswith("id:"):
            pass  # Skip event IDs for now
        elif line == "":
            # Empty line = event boundary
            if current_event is not None and current_data:
                messages.append(SSEMessage(
                    event=SSEEvent(current_event),
                    data=_parse_sse_data("\n".join(current_data)),
                ))
                current_event = None
                current_data = []
    
    # Flush remaining
    if current_event is not None and current_data:
        messages.append(SSEMessage(
            event=SSEEvent(current_event),
            data=_parse_sse_data("\n".join(current_data)),
        ))
    
    return messages


def _parse_sse_data(data: str) -> dict:
    """Parse SSE data string into a dict. Returns {} on failure."""
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return {"raw": data}


# ═══════════════════════════════════════════════════════════════════════════
# 2. STREAMING RENDERER — interleaved thinking + text + tool status
# ═══════════════════════════════════════════════════════════════════════════

class StreamRenderer:
    """Renders SSE stream events to the terminal in real-time.
    
    Thinking → dimmed gray
    Text → normal terminal output
    Tool use → compact status line with tool name + params
    """
    
    def __init__(self):
        self._thinking_buffer = ""
        self._text_buffer = ""
        self._current_tool = None
        self._tool_start_time = None
        self._last_spinner = 0
        self._text_block = None  # live StreamBlock for the assistant's prose
        self._respond_spinner = None  # "responding…" spinner while a reply buffers
    
    def handle_event(self, event: SSEMessage):
        """Process a single SSE event and render it."""
        try:
            handler = {
                SSEEvent.MESSAGE_START: self._on_message_start,
                SSEEvent.CONTENT_BLOCK_START: self._on_content_block_start,
                SSEEvent.CONTENT_BLOCK_DELTA: self._on_content_block_delta,
                SSEEvent.CONTENT_BLOCK_STOP: self._on_content_block_stop,
                SSEEvent.MESSAGE_DELTA: self._on_message_delta,
                SSEEvent.MESSAGE_STOP: self._on_message_stop,
                SSEEvent.PING: lambda e: None,
                SSEEvent.ERROR: self._on_error,
            }
            handler.get(event.event, lambda e: None)(event)
        except Exception:
            pass  # Don't crash on render errors
    
    def _on_message_start(self, event: SSEMessage):
        # Intentionally quiet — no per-message "⚡ model" banner (kept the output clean).
        return
    
    def _on_content_block_start(self, event: SSEMessage):
        block = event.data.get("content_block", {})
        btype = block.get("type", "")
        
        if btype == "thinking":
            self._thinking_buffer = ""
        elif btype == "text":
            self._text_buffer = ""
        elif btype == "tool_use":
            name = block.get("name", "")
            params = block.get("input", {})
            self._show_tool_status(name, params)
    
    def _on_content_block_delta(self, event: SSEMessage):
        delta = event.data.get("delta", {})
        dtype = delta.get("type", "")
        
        if dtype == "thinking_delta":
            # Buffer reasoning but don't stream it raw — it's noise in the transcript.
            self._thinking_buffer += delta.get("thinking", "")

        elif dtype == "text_delta":
            # Stream tokens LIVE so the reply appears as it generates (responsive,
            # like the reference agents) — buffering the whole thing first felt slow.
            # A "▎ korgex" header on the first token, then clean flowing prose.
            text = delta.get("text", "")
            from src.pt_output import emit, render_rich
            if not self._text_buffer:
                emit("\n" + render_rich("[bold #a5de67]▎ korgex[/bold #a5de67]").rstrip("\n") + "\n")
            self._text_buffer += text
            emit(text)

        elif dtype == "input_json_delta":
            partial = delta.get("partial_json", "")
            if partial and self._current_tool:
                self._show_tool_status(
                    self._current_tool["name"],
                    self._current_tool["params"],
                    partial=partial,
                )
    
    def _maybe_markdown(self, block):
        """Opt-in ($KORGEX_MARKDOWN=1): after a reply streams plain, re-render it as
        markdown (highlighted code, headings, lists) below a dim separator. Default
        off — the live plain stream stays the canonical output, so we never fight
        the scrollback by repainting mid-stream."""
        import os
        if os.environ.get("KORGEX_MARKDOWN", "").strip().lower() not in ("1", "true", "yes", "on"):
            return
        text = getattr(block, "buffer", "")
        if not text or not any(m in text for m in ("```", "\n#", "\n- ", "\n* ", "**")):
            return  # only re-render when there's actual markdown structure
        try:
            from src import render as _R
            from src.pt_output import emit, render_rich
            emit("\n" + render_rich("[dim]── formatted ──[/dim]") + "\n")
            emit(_R.render_markdown(text).rstrip("\n") + "\n")
        except Exception:
            pass

    def _on_content_block_stop(self, event: SSEMessage):
        # The text streamed live; just close the block with a newline so the next
        # output (tool line / next turn) starts clean.
        if self._text_buffer:
            from src.pt_output import emit
            emit("\n")
        self._thinking_buffer = ""
        self._text_buffer = ""
    
    def _on_message_delta(self, event: SSEMessage):
        # Quiet — no "━━━ N tok ━━━" footer; keep the reply clean.
        return
    
    def _on_message_stop(self, event: SSEMessage):
        # Clear any lingering tool status
        self._current_tool = None
    
    def _on_error(self, event: SSEMessage):
        error = event.data.get("error", {})
        msg = error.get("message", "Unknown error")
        console.print(f"\n[bold red]✗ Error:[/bold red] {msg}")
    
    def _show_tool_status(self, name: str, params: dict, partial: str = ""):
        """Show a compact tool-call line: ◆ verb target (dim detail), routed
        through the prompt_toolkit-aware sink so it sits cleanly above the input.
        While params still stream in (partial), suppress — render once on the full
        call so we don't print a half-formed line per chunk."""
        self._current_tool = {"name": name, "params": params}
        if partial:
            return
        from src import render as _R
        from src.pt_output import emit, render_rich
        emit("\n" + render_rich(_R.tool_line(name, params)).rstrip("\n") + "\n")


# ═══════════════════════════════════════════════════════════════════════════
# 3. RICH DIFFS — intercept Edit/Write, show unified diff, ask [y/N]
# ═══════════════════════════════════════════════════════════════════════════

def render_unified_diff(file_path: str, old_content: str, new_content: str) -> str:
    """Generate a rich, colorized unified diff.
    
    A standard unified-diff format for SEARCH/REPLACE edits:
    - Red lines with - prefix for removed content
    - Green lines with + prefix for added content
    - @@ hunks showing line numbers
    """
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        n=3,  # 3 lines of context
    ))
    
    if not diff:
        return ""
    
    # Build rich output
    result = [f"\n[bold]Diff: {file_path}[/bold]"]
    
    for line in diff:
        line = line.rstrip()
        if line.startswith("+++") or line.startswith("---"):
            result.append(f"[dim]{line}[/dim]")
        elif line.startswith("@@"):
            result.append(f"[cyan]{line}[/cyan]")
        elif line.startswith("+"):
            result.append(f"[green]{line}[/green]")
        elif line.startswith("-"):
            result.append(f"[red]{line}[/red]")
        elif line.startswith("\\"):
            result.append(f"[yellow]{line}[/yellow]")
        else:
            result.append(line)
    
    return "\n".join(result)


def should_confirm_edit(file_path: str, old_content: str,
                         new_content: str) -> bool:
    """Determine if an edit needs user confirmation.
    
    Confirmation is needed for:
    - Files over 100 lines being modified
    - Edits that delete more than 20 lines
    - Edits to critical files (Makefile, Dockerfile, CI configs)
    """
    CRITICAL_FILES = [
        "Makefile", "Dockerfile", "docker-compose", ".github/workflows",
        "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
        "requirements.txt", "Gemfile",
    ]
    
    old_lines = len(old_content.splitlines())
    new_lines = len(new_content.splitlines())
    
    # Check if it's a critical file
    basename = os.path.basename(file_path)
    if any(crit in basename for crit in CRITICAL_FILES):
        return True
    
    # Large files
    if old_lines > 100:
        return True
    
    # Significant deletions
    deletions = sum(1 for line in old_content.splitlines()
                    if line.strip() and line not in new_content)
    if deletions > 20:
        return True
    
    return False


def preview_edit_interactive(file_path: str, old_content: str,
                              new_content: str) -> bool:
    """Show a rich diff preview and ask for confirmation if needed.
    
    Returns True if the edit should proceed, False if rejected.
    """
    if not os.path.exists(file_path):
        # New file — no diff needed, just confirm
        line_count = new_content.count("\n") + 1
        console.print(f"\n[bold yellow]📄 Create {file_path}[/bold yellow] [dim]({line_count} lines)[/dim]")
        if line_count > 50:
            return Confirm.ask("  Create this file?", default=True)
        return True
    
    diff_text = render_unified_diff(file_path, old_content, new_content)
    if not diff_text:
        return True
    
    console.print(diff_text)
    
    if should_confirm_edit(file_path, old_content, new_content):
        return Confirm.ask("\n[bold yellow]Apply this edit?[/bold yellow]", default=True)
    
    return True


# ═══════════════════════════════════════════════════════════════════════════
# 4. INTERRUPT HANDLER — SIGINT → UserInterrupt (not kill)
# ═══════════════════════════════════════════════════════════════════════════

class InterruptHandler:
    """Handles Ctrl+C with graceful interrupt semantics.
    
    First Ctrl+C within a generation: sends UserInterrupt event
    Two quick Ctrl+Cs: force kill
    """
    
    def __init__(self):
        self._interrupted = False
        self._force_kill = False
        self._last_interrupt = 0
        self._original_handler = None
    
    def install(self):
        """Install the signal handler."""
        self._original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handler)
    
    def restore(self):
        """Restore the original handler."""
        if self._original_handler:
            signal.signal(signal.SIGINT, self._original_handler)
    
    def _handler(self, sig, frame):
        now = time.time()
        
        if now - self._last_interrupt < 2.0:
            # Double Ctrl+C in 2 seconds → force kill
            self._force_kill = True
            print("\n[bold red]Force kill...[/bold red]")
            self.restore()
            os.kill(os.getpid(), signal.SIGINT)
            return
        
        self._last_interrupt = now
        
        if self._interrupted:
            # Already interrupted — second single press is a reminder
            print("\n[yellow]⏳ Already interrupted. Press Ctrl+C again to force quit.[/yellow]")
        else:
            self._interrupted = True
            print("\n[yellow]⏸ Interrupted. Generation stopped. You can give new instructions.[/yellow]")
    
    def was_interrupted(self) -> bool:
        """Check if the user has pressed Ctrl+C."""
        result = self._interrupted
        self._interrupted = False  # Consume the interrupt
        return result
    
    def should_force_kill(self) -> bool:
        return self._force_kill


# ═══════════════════════════════════════════════════════════════════════════
# 5. EPHEMERAL SPINNERS → compact checkmarks
# ═══════════════════════════════════════════════════════════════════════════

class Spinner:
    """A context manager for transient spinners.
    
    Usage:
        with Spinner("Running pytest...") as sp:
            result = run_tests()
            sp.succeed("Tests passed")  # replaces spinner with ✓
    
    The spinner disappears on completion, leaving only the checkmark line.
    """
    
    SPINNER_CHARS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    
    def __init__(self, message: str = ""):
        self.message = message
        self._thread = None
        self._stop = False
        self._result = None
        self._spin_idx = 0
    
    def __enter__(self):
        self._stop = False
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self
    
    def __exit__(self, *args):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=0.5)
        self._clear_line()

    @staticmethod
    def _clear_line():
        from src.pt_output import emit_raw
        emit_raw("\r\033[2K")  # carriage return + erase the whole line

    def _spin(self):
        from src.pt_output import emit_raw
        while not self._stop:
            char = self.SPINNER_CHARS[self._spin_idx % len(self.SPINNER_CHARS)]
            self._spin_idx += 1
            # RAW write (not prompt_toolkit) — its renderer strips \r, which made
            # frames append instead of overwrite. \r + ESC[2K overwrites one line.
            emit_raw(f"\r\033[2K\033[2m{char} {self.message}\033[0m")
            time.sleep(0.08)
        self._clear_line()
    
    def succeed(self, message: str = None):
        """Replace spinner with a green checkmark."""
        if message:
            console.print(f"[green]✓[/green] {message}")
        self._stop = True
    
    def fail(self, message: str = None):
        """Replace spinner with a red X."""
        if message:
            console.print(f"[red]✗[/red] {message}")
        self._stop = True
    
    def warn(self, message: str = None):
        """Replace spinner with a yellow warning."""
        if message:
            console.print(f"[yellow]⚠[/yellow] {message}")
        self._stop = True


# ═══════════════════════════════════════════════════════════════════════════
# 6. INTERACTIVE SESSION — ties everything together
# ═══════════════════════════════════════════════════════════════════════════

class InteractiveSession:
    """Full interactive TUI session for Korgex.
    
    Wires together:
    - SSE stream processing → interleaved thinking/text/tool display
    - Spinner management for long-running operations
    - Interrupt handling with graceful UserInterrupt
    - Rich diff previews with [y/N] prompts
    
    Usage:
        session = InteractiveSession()
        session.start()
        session.stream_event(sse_message)  # for each SSE event
        session.tool_spinner("Running tests...")  # context manager
        session.stop()
    """
    
    def __init__(self):
        self.renderer = StreamRenderer()
        self.interrupt = InterruptHandler()
        self._running = False
    
    def start(self):
        """Begin a turn: arm the interrupt handler. No decorative frame — in a
        REPL this runs once PER TURN, so a '╔══ … ──' banner per message was wrong
        (it made every reply look like a whole session opening/closing)."""
        self._running = True
        self.interrupt.install()

    def stop(self):
        """End a turn: restore the interrupt handler. No closing frame (see start)."""
        self._running = False
        self.interrupt.restore()
    
    def stream_event(self, event: SSEMessage):
        """Process a single SSE event from the stream."""
        if self.interrupt.was_interrupted():
            # Inject a synthetic interrupt event
            console.print("\n[yellow]⏸ User interrupted. Awaiting instructions...[/yellow]")
            return True  # Signal that we were interrupted
        self.renderer.handle_event(event)
        return False
    
    def stream_raw(self, raw_sse: str):
        """Process a raw SSE string (full response)."""
        events = parse_sse_stream(raw_sse)
        for event in events:
            if self.stream_event(event):
                return True
        return False
    
    def spinner(self, message: str = "") -> Spinner:
        """Create a spinner context manager."""
        return Spinner(message)
    
    def confirm_action(self, file_path: str, old_content: str,
                        new_content: str) -> bool:
        """Show a diff and ask for confirmation."""
        return preview_edit_interactive(file_path, old_content, new_content)
    
    def print_info(self, message: str):
        """Print an info line."""
        console.print(f"[dim]ℹ {message}[/dim]")
    
    def print_success(self, message: str):
        """Print a success line."""
        console.print(f"[green]✓ {message}[/green]")
    
    def print_error(self, message: str):
        """Print an error line."""
        console.print(f"[red]✗ {message}[/red]")
    
    def print_warning(self, message: str):
        """Print a warning line."""
        console.print(f"[yellow]⚠ {message}[/yellow]")


# ═══════════════════════════════════════════════════════════════════════════
# SELF-TEST / DEMO
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Demo the interactive session
    session = InteractiveSession()
    session.start()
    
    # Simulate an SSE stream
    sample_sse = """
event: message_start
data: {"type":"message_start","message":{"model":"claude-opus-4-7","usage":{"cache_read_input_tokens":70181}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Let me analyze the codebase structure first."}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"I'll look at the main entry point."}}

event: content_block_stop
data: {"type":"content_block_stop","index":1}

event: content_block_start
data: {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","name":"Read","input":{"file_path":"src/main.py"}}}

event: content_block_stop
data: {"type":"content_block_stop","index":2}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":42,"iterations":[{"input_tokens":6,"output_tokens":42}]}}

event: message_stop
data: {"type":"message_stop"}
"""
    
    print("\n=== Demo: SSE Streaming ===\n")
    session.stream_raw(sample_sse)
    
    print("\n=== Demo: Spinners ===\n")
    with session.spinner("Running pytest...") as sp:
        time.sleep(1.5)
        sp.succeed("Tests passed (12 passed, 0 failed)")
    
    with session.spinner("Installing dependencies...") as sp:
        time.sleep(1.0)
        sp.succeed("All deps installed")
    
    print("\n=== Demo: Diff Preview ===\n")
    old = 'def hello():\n    print("hello world")\n    return True\n'
    new = 'def hello(name: str):\n    print(f"hello {name}")\n    return True\n'
    preview_edit_interactive("src/greeting.py", old, new)
    
    print("\n=== Demo: Interrupt ===\n")
    print("  Press Ctrl+C to test interrupt handling")
    print("  (or press Enter to skip)")
    try:
        input()
    except KeyboardInterrupt:
        pass
    
    session.stop()
    print("\nDemo complete.")