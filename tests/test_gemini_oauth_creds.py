"""GeminiClient OAuth client credentials must come from env / local file —
never a hardcoded secret committed to the public repo.

A Google `client_secret` (GOCSPX-…) baked into source would trip GitHub secret
scanning and get auto-revoked. korgex is bring-your-own-OAuth: the client_id and
client_secret are read from GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET,
falling back to the user's local ADC json (which already carries both).
"""
import inspect
import json

import src.model_router as MR


def test_gemini_reads_oauth_client_creds_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-secret")
    # No token files in play — env is the source of truth here.
    monkeypatch.setattr(MR.GeminiClient, "AUTH_JSON", str(tmp_path / "none1"))
    monkeypatch.setattr(MR.GeminiClient, "ADC_JSON", str(tmp_path / "none2"))
    c = MR.GeminiClient()
    assert c._client_id == "env-id"
    assert c._client_secret == "env-secret"


def test_gemini_reads_oauth_client_creds_from_adc_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    adc = tmp_path / "adc.json"
    adc.write_text(json.dumps({
        "client_id": "file-id",
        "client_secret": "file-secret",
        "refresh_token": "file-refresh",
        "type": "authorized_user",
    }))
    monkeypatch.setattr(MR.GeminiClient, "AUTH_JSON", str(tmp_path / "none"))
    monkeypatch.setattr(MR.GeminiClient, "ADC_JSON", str(adc))
    c = MR.GeminiClient()
    assert c._client_id == "file-id"
    assert c._client_secret == "file-secret"
    assert c._refresh_token == "file-refresh"


def test_env_wins_over_adc_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-secret")
    adc = tmp_path / "adc.json"
    adc.write_text(json.dumps({
        "client_id": "file-id", "client_secret": "file-secret",
        "refresh_token": "file-refresh",
    }))
    monkeypatch.setattr(MR.GeminiClient, "AUTH_JSON", str(tmp_path / "none"))
    monkeypatch.setattr(MR.GeminiClient, "ADC_JSON", str(adc))
    c = MR.GeminiClient()
    assert c._client_id == "env-id"          # env overrides the file
    assert c._client_secret == "env-secret"


def test_no_hardcoded_google_client_secret_in_source():
    # Regression guard: a Google client_secret must never be committed.
    source = inspect.getsource(MR)
    assert "GOCSPX-" not in source, "a Google client_secret is hardcoded in model_router.py"
