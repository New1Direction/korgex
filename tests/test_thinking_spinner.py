"""Thinking-spinner: cover the silent gap before the first token.

The REPL sat dead-silent between submit and the first streamed token (model
latency + network). The agent now runs a 'thinking…' spinner during that gap and
clears it the instant the first stream event arrives. These tests pin the
contract without needing a real TTY/model.
"""
from src.agent import KorgexAgent


def test_thinking_is_noop_when_not_interactive(tmp_path):
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    with a._thinking() as stop:
        # non-interactive → no spinner object, stop() is a safe no-op
        stop(); stop()  # idempotent, never raises


def test_thinking_yields_a_stop_callable(tmp_path):
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    with a._thinking() as stop:
        assert callable(stop)


def test_openai_stream_clears_spinner_on_first_chunk(tmp_path, monkeypatch):
    """The on_first hook fires exactly once, on the first chunk."""
    a = KorgexAgent(model="gpt-4o", repo_root=str(tmp_path), interactive=False)

    # a fake OpenAI stream of two text chunks
    class _Delta:
        def __init__(self, t): self.content = t; self.tool_calls = None
    class _Choice:
        def __init__(self, t): self.delta = _Delta(t)
    class _Chunk:
        def __init__(self, t): self.choices = [_Choice(t)]
    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw): return [_Chunk("hel"), _Chunk("lo")]

    monkeypatch.setattr(a, "_get_session", lambda: _FakeSession())
    fired = []
    a._call_openai_streaming(_Client(), [{"role": "user", "content": "hi"}], [],
                             on_first=lambda: fired.append(1))
    assert fired == [1], "on_first must fire exactly once (on the first chunk)"


class _FakeSession:
    def stream_event(self, sse): return False  # never interrupt
