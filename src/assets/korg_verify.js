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
async function verifyChain(events) {
  const errs = [];
  let expected = GENESIS;
  for (const e of events) {
    if (e.prev_hash !== expected) errs.push({ seq: e.seq_id, why: 'broken link (insert/delete/reorder)' });
    if ((await chainHash(e)) !== e.entry_hash) errs.push({ seq: e.seq_id, why: 'content tampered' });
    expected = e.entry_hash;
  }
  return errs;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { jsonString, canonical, chainHash, verifyChain, GENESIS };
}
