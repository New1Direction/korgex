"""Tests for human-friendly errors (src/errors.py).

Raw provider/SDK errors are cryptic ("401", a stack trace, a socket error). When a
turn dies, the user should get a clear sentence: what failed and what to do about
it. humanize_error pattern-matches the common failure modes; anything unrecognized
falls through to the original text (never hide the real error).
"""
from src import errors as E


class TestHumanizeError:
    def test_auth_failure_points_to_setup(self):
        msg = E.humanize_error("Error code: 401 - invalid api key")
        assert "key" in msg.lower()
        assert "setup" in msg.lower()

    def test_rate_limit_suggests_wait_or_switch(self):
        msg = E.humanize_error("429 Too Many Requests")
        assert "rate" in msg.lower()
        assert "/model" in msg or "wait" in msg.lower()

    def test_model_not_found_points_to_model_command(self):
        msg = E.humanize_error("404 model `gpt-9` not found")
        assert "model" in msg.lower()
        assert "/model" in msg

    def test_connection_error_mentions_network(self):
        msg = E.humanize_error("Connection error: failed to establish a new connection")
        assert "connect" in msg.lower() or "network" in msg.lower()

    def test_context_length_suggests_clear_or_bigger_model(self):
        msg = E.humanize_error("This model's maximum context length is 8192 tokens")
        assert "context" in msg.lower()
        assert "/clear" in msg or "larger" in msg.lower()

    def test_quota_mentions_billing_or_credit(self):
        msg = E.humanize_error("You exceeded your current quota, please check your billing")
        assert "credit" in msg.lower() or "quota" in msg.lower() or "billing" in msg.lower()

    def test_service_unavailable_suggests_retry_or_switch(self):
        msg = E.humanize_error("Error 503: Service Unavailable")
        assert "unavailable" in msg.lower()
        assert "/model" in msg or "retry" in msg.lower()

    def test_every_rule_message_is_clean_printable_text(self):
        # Guard against corrupt/control bytes in a user-facing message — an em-dash
        # once got mangled into \x1a\x14, and the substring assertions sailed past it.
        for _needles, message in E._RULES:
            assert message.isprintable(), f"non-printable chars in: {message!r}"

    def test_unknown_error_falls_through_to_original(self):
        msg = E.humanize_error("some totally novel failure xyz")
        assert "some totally novel failure xyz" in msg

    def test_accepts_an_exception_object(self):
        msg = E.humanize_error(ValueError("401 unauthorized"))
        assert "key" in msg.lower()

    def test_empty_is_safe(self):
        assert isinstance(E.humanize_error(""), str)
        assert isinstance(E.humanize_error(None), str)
