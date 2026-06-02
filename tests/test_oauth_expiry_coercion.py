"""OAuth token expiry must be coerced to a numeric epoch before comparison.

Real-world bug: the grok CLI writes `expires_at` to auth.json as a STRING, so
`_is_expired()`'s `time.time() > self._expires_at` did `float > str` → TypeError,
killing the whole token load. The loaders now run every stored expiry through
`_to_epoch`, which accepts a float, a numeric string, or an ISO-8601 timestamp
(and milliseconds for the keychain), so a string expiry can never crash again.
"""
import json

import src.model_router as MR


def test_to_epoch_float_passthrough():
    assert MR._to_epoch(1700000000.0) == 1700000000.0


def test_to_epoch_numeric_string():
    assert MR._to_epoch("1700000000") == 1700000000.0


def test_to_epoch_iso_string():
    assert MR._to_epoch("2030-01-01T00:00:00+00:00") > 1_800_000_000


def test_to_epoch_milliseconds():
    assert MR._to_epoch(1700000000000, ms=True) == 1700000000.0


def test_to_epoch_garbage_and_none():
    assert MR._to_epoch(None) == 0.0
    assert MR._to_epoch("not-a-timestamp") == 0.0
    assert MR._to_epoch("") == 0.0


def test_grok_load_token_survives_string_expiry(tmp_path, monkeypatch):
    # Regression: a STRING expires_at must not crash _is_expired.
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({
        "https://auth.x.ai::client": {
            "key": "jwt-tok", "refresh_token": "r", "expires_at": "1700000000",
        }
    }))
    monkeypatch.setattr(MR.GrokClient, "AUTH_JSON", str(auth))
    c = MR.GrokClient()
    assert isinstance(c._expires_at, float)
    assert c._is_expired() in (True, False)   # comparison works, no TypeError
