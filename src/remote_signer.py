"""Fail-closed HTTP client for an authorized remote signing service.

The supported pattern is a signer service we own/control: POST a journal tip to a
small HTTP endpoint, receive ``{pubkey, tip, sig}``, then verify the signature before
using it. This module intentionally does not implement or document mobile app
injection. Treat arbitrary in-app crypto RPC bridges as unsafe unless the app owner
explicitly authorized that surface.
"""
from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

from src import signing

_HEX_32_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_HEX_64_RE = re.compile(r"^[0-9a-fA-F]{128}$")


class RemoteSignerError(ValueError):
    """Raised when a remote signer request is not safe or not verifiable."""


def _require_hex(value: object, regex: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not regex.fullmatch(value):
        raise RemoteSignerError(f"{label} must be {32 if regex is _HEX_32_RE else 64}-byte hex")
    return value.lower()


def _is_loopback(host: str) -> bool:
    """True for localhost / 127.0.0.0-8 / ::1 — the only hosts where plaintext http is safe."""
    h = (host or "").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def is_plaintext_remote(url: str) -> bool:
    """True iff the URL ships data in the clear to a non-loopback host (bearer-token-leak risk)."""
    parsed = urlparse(url)
    return parsed.scheme == "http" and bool(parsed.hostname) and not _is_loopback(parsed.hostname)


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only within the allowlist, and never carry the bearer token to a new host.

    Raw urllib follows 3xx automatically and re-sends ``Authorization`` across hosts without
    re-checking the allowlist — so a hostile or on-path redirect could leak the signing token
    to a host you never approved. This keeps the *ability* to follow a redirect, but only to an
    allowlisted host, and drops the token whenever the host changes.
    """

    def __init__(self, allowed: set[str]):
        super().__init__()
        self._allowed = {h.lower() for h in allowed}

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlparse(newurl)
        host = (target.hostname or "").lower()
        if host not in self._allowed:
            raise RemoteSignerError(
                f"remote signer redirected to off-allowlist host '{target.hostname}'"
            )
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            origin = (urlparse(req.full_url).hostname or "").lower()
            if host != origin:
                for key in [k for k in new.headers if k.lower() == "authorization"]:
                    del new.headers[key]
        return new


def _validate_url(
    url: str,
    allowed_hosts: list[str] | tuple[str, ...] | set[str],
    *,
    require_https: bool = False,
) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RemoteSignerError("remote signer URL must use http or https")
    if not parsed.hostname:
        raise RemoteSignerError("remote signer URL must include a host")
    if not allowed_hosts:
        raise RemoteSignerError("remote signer host allowlist is required")
    allowed = {h.lower() for h in allowed_hosts}
    if parsed.hostname.lower() not in allowed:
        raise RemoteSignerError(f"remote signer host '{parsed.hostname}' is not in allowlist")
    if require_https and parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise RemoteSignerError(
            f"remote signer host '{parsed.hostname}' requires https "
            "(KORGEX_REMOTE_SIGNER_REQUIRE_HTTPS is set); plaintext http is allowed only to loopback"
        )
    return url


def _post_json(
    url: str,
    payload: dict,
    bearer_token: str,
    timeout: float,
    allowed_hosts: list[str] | tuple[str, ...] | set[str],
) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    opener = urllib.request.build_opener(_AllowlistRedirectHandler(set(allowed_hosts)))
    try:
        with opener.open(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:200]
        raise RemoteSignerError(f"remote signer HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RemoteSignerError(f"remote signer request failed: {e.reason}") from e
    except TimeoutError as e:
        raise RemoteSignerError("remote signer request timed out") from e

    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RemoteSignerError("remote signer returned invalid JSON") from e
    if not isinstance(decoded, dict):
        raise RemoteSignerError("remote signer returned non-object JSON")
    return decoded


def sign_tip_via_http(
    url: str,
    tip_hex: str,
    *,
    bearer_token: str,
    allowed_hosts: list[str] | tuple[str, ...] | set[str],
    expected_pubkeys: list[str] | tuple[str, ...] | set[str] | None = None,
    require_https: bool = False,
    timeout: float = 10.0,
) -> dict:
    """Ask an authorized HTTP signer to sign a 32-byte journal tip.

    Security gates:
    - URL scheme is http(s) only.
    - Host must be explicitly allowlisted by the caller.
    - Bearer token is mandatory.
    - Tip/pubkey/signature are strict hex lengths.
    - Redirects stay on the allowlist and never carry the token to a new host.
    - The returned signature must verify locally before this function returns it.
    - If ``expected_pubkeys`` is given, the signer's key must be one of them. The local verify
      alone is self-referential — it proves the sig matches the *returned* key, not that it's a
      key you trust — so a hostile signer could hand back a valid sig under a key it minted.
      Pinning closes that. It is optional so existing callers keep working; pass it to
      authenticate *who* signed.
    - If ``require_https`` is set, plaintext http is rejected for non-loopback hosts.
    """
    tip = _require_hex(tip_hex, _HEX_32_RE, "tip")
    endpoint = _validate_url(url, allowed_hosts, require_https=require_https)
    if not bearer_token:
        raise RemoteSignerError("remote signer bearer token is required")

    result = _post_json(endpoint, {"tip": tip}, bearer_token, timeout, allowed_hosts)
    pubkey = _require_hex(result.get("pubkey"), _HEX_32_RE, "pubkey")
    returned_tip = _require_hex(result.get("tip"), _HEX_32_RE, "tip")
    sig = _require_hex(result.get("sig"), _HEX_64_RE, "signature")

    if returned_tip != tip:
        raise RemoteSignerError("remote signer returned a signature for a different tip")
    if not signing.verify_tip(pubkey, tip, sig):
        raise RemoteSignerError("remote signer signature did not verify")
    if expected_pubkeys:
        pins = {p.lower() for p in expected_pubkeys}
        if pubkey not in pins:
            raise RemoteSignerError(
                "remote signer pubkey is not in the pinned allowlist (KORGEX_REMOTE_SIGNER_PUBKEY)"
            )
    return {"pubkey": pubkey, "tip": tip, "sig": sig}
