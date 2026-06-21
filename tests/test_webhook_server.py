import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from src import webhook_server as W


def _sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature_rejects_unsigned_without_secret(monkeypatch):
    monkeypatch.delenv("KORGEX_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("KORGEX_WEBHOOK_ALLOW_UNSIGNED", raising=False)

    assert W.verify_signature(b"{}", "") is False


def test_verify_signature_allows_unsigned_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("KORGEX_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("KORGEX_WEBHOOK_ALLOW_UNSIGNED", "1")

    assert W.verify_signature(b"{}", "") is True


def test_verify_signature_accepts_valid_github_signature(monkeypatch):
    monkeypatch.setenv("KORGEX_WEBHOOK_SECRET", "test-secret")
    monkeypatch.delenv("KORGEX_WEBHOOK_ALLOW_UNSIGNED", raising=False)
    body = b'{"ok": true}'

    assert W.verify_signature(body, _sig("test-secret", body)) is True


def test_verify_signature_rejects_invalid_github_signature(monkeypatch):
    monkeypatch.setenv("KORGEX_WEBHOOK_SECRET", "test-secret")

    assert W.verify_signature(b"{}", "sha256=not-valid") is False
    assert W.verify_signature(b"{}", "") is False


def test_webhook_endpoint_rejects_unsigned_by_default(monkeypatch):
    monkeypatch.delenv("KORGEX_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("KORGEX_WEBHOOK_ALLOW_UNSIGNED", raising=False)
    app = W.create_webhook_app()
    assert app is not None

    r = TestClient(app).post("/webhook", json={"action": "created"}, headers={"X-GitHub-Event": "issues"})

    assert r.status_code == 401


def test_webhook_endpoint_accepts_valid_signature(monkeypatch):
    monkeypatch.setenv("KORGEX_WEBHOOK_SECRET", "test-secret")
    seen = {}
    monkeypatch.setattr(W, "_process_webhook", lambda event, data: seen.update(event=event, data=data))

    class ImmediateThread:
        def __init__(self, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(W.threading, "Thread", ImmediateThread)
    app = W.create_webhook_app()
    assert app is not None

    body = json.dumps({"action": "created"}).encode()
    r = TestClient(app).post(
        "/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": _sig("test-secret", body),
        },
    )

    assert r.status_code == 200
    assert r.json() == {"status": "received", "event": "issues"}
    assert seen == {"event": "issues", "data": {"action": "created"}}
