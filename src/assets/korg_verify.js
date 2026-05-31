// korg-ledger@v1 reference verifier — runs in the browser AND in node.
//
// Must match src/ledger_spec.py (Python) and the Rust core byte-for-byte.
// Canonicalization: JSON with keys sorted by code point, separators (",",":"),
// non-ASCII escaped to \uXXXX (incl. surrogate pairs); preimage = event minus
// `entry_hash`; digest = SHA-256. Proven against the frozen conformance vectors
// (basic-intact tip 7418b910…). A skeptic opening an audit report runs THIS.

function jsonString(s) {
  let out = '"';
  for (const ch of s) {
    const cp = ch.codePointAt(0);
    if (ch === '"') out += '\\"';
    else if (ch === '\\') out += '\\\\';
    else if (ch === '\n') out += '\\n';
    else if (ch === '\r') out += '\\r';
    else if (ch === '\t') out += '\\t';
    else if (cp === 0x08) out += '\\b';
    else if (cp === 0x0c) out += '\\f';
    else if (cp >= 0x20 && cp <= 0x7e) out += ch;
    else if (cp > 0xffff) {
      const v = cp - 0x10000;
      out += '\\u' + (0xd800 + (v >> 10)).toString(16).padStart(4, '0');
      out += '\\u' + (0xdc00 + (v & 0x3ff)).toString(16).padStart(4, '0');
    } else out += '\\u' + cp.toString(16).padStart(4, '0');
  }
  return out + '"';
}

function canonical(v) {
  if (v === null) return 'null';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number') return String(v);
  if (typeof v === 'string') return jsonString(v);
  if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']';
  return '{' + Object.keys(v).sort().map((k) => jsonString(k) + ':' + canonical(v[k])).join(',') + '}';
}

const GENESIS = '0'.repeat(64);

async function chainHash(event) {
  const pre = {};
  for (const k of Object.keys(event)) if (k !== 'entry_hash') pre[k] = event[k];
  const bytes = new TextEncoder().encode(canonical(pre)); // canonical() is ASCII-only
  const buf = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

// Returns [] when intact; otherwise one entry per detected problem, localized by seq.
// With `expectedTip` (a genuine tip hash anchored externally — a public timestamp,
// a signed post, a git tag), the chain's actual tip is compared to it. That closes
// the unkeyed-regeneration hole: a forger can edit a body and re-link + re-hash the
// whole chain so every per-event check passes, but the resulting tip can't match a
// tip published before the forgery. Without an anchor, regeneration is undetectable.
async function verifyChain(events, expectedTip) {
  const errs = [];
  let expected = GENESIS;
  for (const e of events) {
    if (e.prev_hash !== expected) errs.push({ seq: e.seq_id, why: 'broken link (insert/delete/reorder)' });
    if ((await chainHash(e)) !== e.entry_hash) errs.push({ seq: e.seq_id, why: 'content tampered' });
    expected = e.entry_hash;
  }
  if (expectedTip != null) {
    const actualTip = events.length ? events[events.length - 1].entry_hash : null;
    if (actualTip !== expectedTip) {
      errs.push({ seq: null, why: 'tip does not match the anchored tip (chain may have been regenerated/forged)' });
    }
  }
  return errs;
}

// ── sealed envelope (commit-reveal) — mirror of src/sealed_envelope.py ──
// commit = SHA-256 over the canonical encoding of {payload, salt}. A reveal recomputes
// it here, in the viewer's browser, byte-for-byte identical to the Python/Rust commit.
async function sha256Canonical(value) {
  const bytes = new TextEncoder().encode(canonical(value)); // canonical() is ASCII-only
  const buf = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}
async function sealCommit(payload, salt) {
  return sha256Canonical({ payload, salt });
}
// hash raw file bytes (an ArrayBuffer/TypedArray) — the drag-in "proof of custody" verify.
// The file never leaves the browser; only this fingerprint is compared to the sealed one.
async function sha256Bytes(buf) {
  const d = await crypto.subtle.digest('SHA-256', buf);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

// ── Ed25519-over-tip — verify "who" in the viewer's browser (mirror of src/signing.py) ──
// The agent IS its public key. This confirms the holder of that key signed this exact
// chain tip — author-authenticity with zero trust in korg, via WebCrypto Ed25519.
function _hexBytes(h) { return Uint8Array.from(h.match(/../g).map((b) => parseInt(b, 16))); }
async function verifyTipSig(pubHex, tipHex, sigHex) {
  try {
    const key = await crypto.subtle.importKey('raw', _hexBytes(pubHex), { name: 'Ed25519' }, false, ['verify']);
    return await crypto.subtle.verify('Ed25519', key, _hexBytes(sigHex), _hexBytes(tipHex));
  } catch (e) {
    return false; // browser without Ed25519 support, or a bad signature
  }
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { jsonString, canonical, chainHash, verifyChain, sha256Canonical, sealCommit, sha256Bytes, verifyTipSig, GENESIS };
}
