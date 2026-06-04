"""Render a self-verifying, shareable HTML audit report from a korg-ledger@v1 journal.

The report embeds the ledger events + the reference JS verifier and RE-CHECKS the
hash chain in the recipient's own browser — so they need not trust the tool that
made it. A live "tamper test" flips one event to show the chain break, turning
tamper-evidence from a claim into something you can feel.

Self-contained: no external fonts, scripts, or network calls (an audit artifact
must not phone home). The embedded verifier is the exact, conformance-tested
`assets/korg_verify.js` — proven against the frozen korg-ledger@v1 vectors.
"""
from __future__ import annotations

import json
from pathlib import Path

_ASSET = Path(__file__).resolve().parent / "assets" / "korg_verify.js"


def _embed_json(value) -> str:
    """JSON safe to inline inside a <script> tag (prevent </script> breakout, U+2028/9)."""
    return (
        json.dumps(value)
        .replace("</", "<\\/")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def _h(s) -> str:
    """Escape for HTML text and double-quoted attribute values."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _og_tags(share: dict) -> str:
    """Open Graph + Twitter-card tags so a shared link unfurls as a proof card."""
    rows: list[str] = []

    def tag(prop: str, val, attr: str = "property") -> None:
        if val:
            rows.append(f'<meta {attr}="{prop}" content="{_h(val)}">')

    tag("og:type", "website")
    tag("og:title", share.get("title"))
    tag("og:description", share.get("description"))
    tag("og:image", share.get("image"))
    tag("og:url", share.get("url"))
    tag("twitter:card", "summary_large_image" if share.get("image") else "summary", attr="name")
    tag("twitter:title", share.get("title"), attr="name")
    tag("twitter:description", share.get("description"), attr="name")
    tag("twitter:image", share.get("image"), attr="name")
    return "\n".join(rows)


def _share_block(meta: dict) -> str:
    """An optional 'verify it yourself, another way' panel — the signer, the exact pip/Rust
    commands, and a button to download the embedded receipt JSON. Rendered only for
    receipt-backed pages; a plain journal audit gets nothing here (output unchanged)."""
    signed_by = meta.get("signed_by")
    verify = meta.get("verify") or {}
    has_receipt = bool(meta.get("receipt"))
    if not (signed_by or verify or has_receipt):
        return ""
    parts = ['<h2>Verify it yourself, another way</h2>', '<div class="verify-another">']
    if signed_by:
        parts.append(
            f'<div class="signer">Signed by <code>{_h(signed_by)}</code> '
            '<span class="dim">— Ed25519 over the chain tip (authorship)</span></div>')
    parts.append(
        '<p class="dim">This page already re-checked the hash chain in your browser. To check it '
        'independently — including the signature — download the receipt and run one of:</p>')
    if verify.get("pip"):
        parts.append(f'<pre class="cmd">{_h(verify["pip"])}</pre>')
    if verify.get("cargo"):
        parts.append(f'<pre class="cmd">{_h(verify["cargo"])}</pre>')
    if has_receipt:
        parts.append('<button id="btnDownload">⬇ Download receipt (.json)</button>')
    parts.append("</div>")
    return "\n".join(parts)


def render_html(events: list, meta: dict | None = None) -> str:
    """Return a single self-contained HTML document that re-verifies `events` in-browser.

    Optional `meta` keys make it a *shareable* proof page: ``share`` (title/description/
    image/url → Open Graph + Twitter card), ``signed_by`` (surfaced to the reader),
    ``verify`` (pip/cargo commands) and ``receipt`` (embedded so the reader can download
    the exact JSON). Without those, the output is the plain audit page as before."""
    meta = dict(meta or {})
    meta.setdefault("session", "session")
    meta.setdefault("vendor", "unknown")
    meta.setdefault("spec", "korg-ledger@v1")
    meta["event_count"] = len(events)

    share = meta.get("share") or {}
    page_title = share.get("title") or "korg-ledger · forensic audit"
    receipt = meta.get("receipt")
    embed_meta = {k: v for k, v in meta.items() if k != "receipt"}  # receipt rides in RECEIPT — don't double-embed

    verifier_js = _ASSET.read_text(encoding="utf-8")
    return (
        _TEMPLATE
        .replace("__OG_TAGS__", _og_tags(share) if share else "")
        .replace("__PAGE_TITLE__", _h(page_title))
        .replace("<!--__SHARE_BLOCK__-->", _share_block(meta))
        .replace("/*__VERIFIER_JS__*/", verifier_js)
        .replace('"__RECEIPT_JSON__"', _embed_json(receipt) if receipt else "null")
        .replace('"__EVENTS_JSON__"', _embed_json(events))
        .replace('"__META_JSON__"', _embed_json(embed_meta))
    )


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
__OG_TAGS__
<title>__PAGE_TITLE__</title>
<style>
  :root{
    --bg:#0a0c0f; --panel:#0f1318; --line:#1d2733; --ink:#c7d2dd; --dim:#6b7a8a;
    --ok:#37e0a0; --ok-dim:#10543c; --bad:#ff5b5b; --bad-dim:#5a1620;
    --accent:#7cc5ff; --hash:#8a7cff;
    --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--mono);font-size:14px;line-height:1.5}
  body{
    background-image:radial-gradient(1200px 600px at 80% -10%, #11202e 0%, transparent 60%),
                     radial-gradient(900px 500px at -10% 10%, #16121f 0%, transparent 55%);
    padding:0 0 64px;
  }
  .wrap{max-width:860px;margin:0 auto;padding:0 22px}
  header{padding:54px 0 26px;border-bottom:1px solid var(--line)}
  .eyebrow{font-size:11px;letter-spacing:.32em;text-transform:uppercase;color:var(--dim)}
  .eyebrow b{color:var(--accent);font-weight:600}
  h1{font-family:var(--sans);font-weight:700;font-size:30px;letter-spacing:-.01em;margin:14px 0 4px;
     color:#eef4fa;word-break:break-all}
  .sub{color:var(--dim)}
  .sub .k{color:var(--ink)}
  /* verdict */
  .verdict{margin:26px 0;border:1px solid var(--line);border-radius:14px;padding:22px 24px;
           background:linear-gradient(180deg,var(--panel),#0b0e12);position:relative;overflow:hidden}
  .verdict::before{content:"";position:absolute;inset:0 auto 0 0;width:4px;background:var(--dim)}
  .verdict.ok::before{background:var(--ok)} .verdict.bad::before{background:var(--bad)}
  .verdict .tag{font-family:var(--sans);font-weight:700;font-size:22px;letter-spacing:-.01em;display:flex;align-items:center;gap:12px}
  .verdict.ok .tag{color:var(--ok)} .verdict.bad .tag{color:var(--bad)}
  .verdict .why{color:var(--dim);margin-top:8px;max-width:62ch}
  .dot{width:11px;height:11px;border-radius:50%;background:currentColor;box-shadow:0 0 0 0 currentColor;animation:pulse 2.4s infinite}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(120,255,200,.35)}70%{box-shadow:0 0 0 12px rgba(120,255,200,0)}100%{box-shadow:0 0 0 0 rgba(120,255,200,0)}}
  .verdict.bad .dot{animation:none}
  /* sections */
  h2{font-family:var(--sans);font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:var(--dim);
     margin:38px 0 14px;font-weight:600}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--line);
        border:1px solid var(--line);border-radius:12px;overflow:hidden}
  .cell{background:var(--panel);padding:14px 16px}
  .cell .n{font-size:24px;color:#eef4fa;font-variant-numeric:tabular-nums}
  .cell .l{color:var(--dim);font-size:11px;letter-spacing:.1em;text-transform:uppercase;margin-top:3px}
  .narr{list-style:none;margin:0;padding:0}
  .narr li{display:flex;gap:12px;padding:9px 0;border-bottom:1px solid #131a22;align-items:baseline}
  .narr .op{color:var(--accent);min-width:64px;font-size:12px}
  .narr .op.write{color:var(--ok)} .narr .op.run{color:#ffcf6b} .narr .op.ask{color:var(--hash)}
  .narr .body{color:var(--ink);word-break:break-all}
  /* chain */
  #chain{display:flex;flex-direction:column;gap:0;margin-top:4px}
  .blk{display:flex;align-items:center;gap:14px;background:var(--panel);border:1px solid var(--line);
       border-radius:10px;padding:11px 14px}
  .blk .seq{color:var(--dim);min-width:34px;text-align:right;font-variant-numeric:tabular-nums}
  .blk .tool{color:#eef4fa;min-width:120px}
  .blk .h{color:var(--hash);font-size:12px;margin-left:auto}
  .blk.broken{border-color:var(--bad);background:linear-gradient(90deg,var(--bad-dim),var(--panel) 40%)}
  .blk.broken .tool{color:var(--bad)}
  .link{height:16px;width:2px;margin-left:31px;background:var(--line)}
  .link.broken{background:var(--bad);box-shadow:0 0 8px var(--bad)}
  /* tamper test */
  .tamper{border:1px dashed var(--line);border-radius:14px;padding:20px 22px;background:#0c1116}
  .tamper p{color:var(--dim);margin:0 0 14px;max-width:64ch}
  .btns{display:flex;gap:10px;flex-wrap:wrap}
  button{font-family:var(--mono);font-size:13px;cursor:pointer;border-radius:9px;padding:10px 16px;
         border:1px solid var(--line);background:#141b22;color:var(--ink);transition:.15s}
  button:hover{border-color:var(--accent);color:#fff}
  button.danger{border-color:var(--bad-dim);color:#ffb4b4}
  button.danger:hover{border-color:var(--bad);background:var(--bad-dim)}
  footer{margin-top:44px;padding-top:20px;border-top:1px solid var(--line);color:var(--dim);font-size:12px;max-width:70ch}
  footer b{color:var(--ink)} a{color:var(--accent)}
  .mono-note{color:var(--dim);font-size:12px;margin-top:10px}
  .verify-another{border:1px solid var(--line);border-radius:14px;padding:18px 20px;background:#0c1116}
  .verify-another .signer{margin-bottom:12px;word-break:break-all}
  .verify-another code{color:var(--hash)} .dim{color:var(--dim)}
  pre.cmd{background:#0a0e12;border:1px solid var(--line);border-radius:8px;padding:10px 12px;
          color:var(--ink);font-size:12.5px;margin:8px 0;white-space:pre-wrap;word-break:break-all}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="eyebrow"><b>korg-ledger@v1</b> &nbsp;·&nbsp; forensic cognition audit</div>
    <h1 id="sessionTitle">session</h1>
    <div class="sub">
      <span class="k" id="vendor">—</span> session &nbsp;·&nbsp;
      <span class="k" id="evCount">0</span> ledger events &nbsp;·&nbsp;
      verified <span class="k">in your browser</span>, not by the tool that made this
    </div>
  </header>

  <div class="verdict" id="verdict">
    <div class="tag"><span class="dot"></span><span id="verdictTag">verifying…</span></div>
    <div class="why" id="verdictWhy">Recomputing the SHA-256 hash chain locally…</div>
  </div>

  <h2>At a glance</h2>
  <div class="grid" id="stats"></div>

  <h2>What the agent did</h2>
  <ul class="narr" id="narrative"></ul>

  <h2>The chain</h2>
  <div id="chain"></div>
  <div class="mono-note">Each event links to the previous by hash. Edit, delete, reorder, or splice any
    one and its link — and every link after it — breaks. ↓ prove it yourself.</div>

  <h2>Tamper test</h2>
  <div class="tamper">
    <p>This isn't a screenshot of a claim. Press the button: it edits one recorded event in memory and
      re-runs the same verifier. Watch the verdict flip to <b style="color:var(--bad)">TAMPERED</b> and
      pinpoint exactly which event broke — then reset.</p>
    <div class="btns">
      <button class="danger" id="btnTamper">Tamper with an event &amp; re-verify</button>
      <button id="btnReset">Reset to the original</button>
    </div>
  </div>

  <!--__SHARE_BLOCK__-->

  <footer>
    This page re-derived every <b>entry_hash</b> from the recorded events using the
    <b>korg-ledger@v1</b> reference verifier — entirely in your browser, with no network calls. It did
    not trust the tool that generated it. The same algorithm runs in Python, Rust, and JS against frozen
    conformance vectors. Spec: <a href="https://github.com/New1Direction/korgex">korg-ledger@v1</a>.
  </footer>
</div>

<script>
/*__VERIFIER_JS__*/
</script>
<script>
const EVENTS = "__EVENTS_JSON__";
const META = "__META_JSON__";
const RECEIPT = "__RECEIPT_JSON__";
let working = structuredClone(EVENTS);

const $ = (id) => document.getElementById(id);
const short = (h) => (h ? h.slice(0, 8) + '…' + h.slice(-6) : '—');
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

$('sessionTitle').textContent = META.session || 'session';
$('vendor').textContent = META.vendor || 'unknown';
$('evCount').textContent = (META.event_count ?? EVENTS.length).toLocaleString();

function stats(evts) {
  const tools = {};
  let writes = 0, cmds = 0, asks = 0, rounds = 0, tokens = 0;
  for (const e of evts) {
    tools[e.tool_name] = (tools[e.tool_name] || 0) + 1;
    const t = e.tool_name || '';
    if (/^(Write|Edit|MultiEdit|NotebookEdit)$/.test(t)) writes++;
    else if (t === 'Bash') cmds++;
    else if (t === 'user_prompt' || t === 'user_message') asks++;
    else if (t === 'llm_inference') {
      rounds++;
      tokens += ((e.args && e.args.prompt_tokens) || 0) + ((e.result && e.result.completion_tokens) || 0);
    }
  }
  const distinct = Object.keys(tools).length;
  const cells = [
    [evts.length.toLocaleString(), 'events'],
    [rounds.toLocaleString(), 'thinking rounds'],
    [tokens.toLocaleString(), 'tokens'],
    [writes.toLocaleString(), 'file edits'],
    [cmds.toLocaleString(), 'commands'],
    [distinct.toLocaleString(), 'distinct tools'],
  ];
  $('stats').innerHTML = cells.map(([n, l]) => `<div class="cell"><div class="n">${n}</div><div class="l">${l}</div></div>`).join('');
}

function narrative(evts) {
  const rows = [];
  for (const e of evts) {
    const a = e.args || {};
    const t = e.tool_name || '';
    if (/^(Write|Edit|MultiEdit|NotebookEdit)$/.test(t)) rows.push(['write', a.file_path || a.path || a.notebook_path || '(file)']);
    else if (t === 'Bash') rows.push(['run', a.command || a.cmd || '(command)']);
    else if (t === 'user_prompt' || t === 'user_message') rows.push(['ask', a.prompt || a.text || a.content || '(prompt)']);
    else if (t === 'Read') rows.push(['read', a.file_path || a.path || '(file)']);
  }
  const shown = rows.slice(0, 40);
  $('narrative').innerHTML = shown.map(([op, body]) =>
    `<li><span class="op ${op === 'write' ? 'write' : op === 'run' ? 'run' : op === 'ask' ? 'ask' : ''}">${op}</span><span class="body">${esc(String(body).slice(0, 160))}</span></li>`
  ).join('') + (rows.length > shown.length ? `<li><span class="op"></span><span class="body" style="color:var(--dim)">…and ${rows.length - shown.length} more</span></li>` : '');
}

function renderChain(evts, errs) {
  const brokenLink = new Set(errs.filter((e) => e.why.includes('link')).map((e) => e.seq));
  const brokenContent = new Set(errs.filter((e) => e.why.includes('tamper')).map((e) => e.seq));
  const el = $('chain'); el.innerHTML = '';
  evts.forEach((e, i) => {
    if (i > 0) {
      const link = document.createElement('div');
      link.className = 'link' + (brokenLink.has(e.seq_id) ? ' broken' : '');
      el.appendChild(link);
    }
    const broken = brokenLink.has(e.seq_id) || brokenContent.has(e.seq_id);
    const blk = document.createElement('div');
    blk.className = 'blk' + (broken ? ' broken' : '');
    blk.innerHTML = `<span class="seq">#${e.seq_id}</span><span class="tool">${esc(e.tool_name || '?')}</span><span class="h">${short(e.entry_hash)}</span>`;
    el.appendChild(blk);
  });
}

async function verifyAndRender() {
  stats(working);
  narrative(working);
  const errs = await verifyChain(working, META.anchored_tip);  // anchored tip (if published) catches a fully-regenerated chain
  renderChain(working, errs);
  const v = $('verdict'), tag = $('verdictTag'), why = $('verdictWhy');
  if (errs.length === 0) {
    v.className = 'verdict ok'; tag.textContent = 'CHAIN INTACT';
    why.textContent = 'Every event hashes to its recorded entry_hash and links unbroken from genesis. This record was not edited, deleted, reordered, or spliced after the fact — re-verified locally just now.';
  } else {
    v.className = 'verdict bad'; tag.textContent = 'TAMPERED — ' + errs.length + ' issue' + (errs.length > 1 ? 's' : '');
    const where = [...new Set(errs.map((e) => '#' + e.seq))].join(', ');
    why.textContent = 'The local hash chain does not match the recorded hashes. First break at event ' + where + '. ' + errs.map((e) => '#' + e.seq + ': ' + e.why).slice(0, 3).join('  ·  ');
  }
}

$('btnTamper').onclick = async () => {
  if (!working.length) return;
  const i = Math.min(1, working.length - 1); // pick an event mid-chain when possible
  working = structuredClone(EVENTS);
  working[i] = JSON.parse(JSON.stringify(working[i]));
  working[i].args = Object.assign({}, working[i].args, { _injected: 'an attacker edited this event' });
  // entry_hash left unchanged on purpose → the recomputed hash will no longer match
  await verifyAndRender();
};
$('btnReset').onclick = async () => { working = structuredClone(EVENTS); await verifyAndRender(); };

if (RECEIPT && $('btnDownload')) {
  $('btnDownload').onclick = () => {
    const blob = new Blob([JSON.stringify(RECEIPT, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    const slug = (RECEIPT.claim || 'receipt').replace(/[^a-z0-9]+/gi, '-').replace(/^-+|-+$/g, '').slice(0, 48) || 'receipt';
    a.download = slug + '.korgreceipt.json';
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
  };
}

verifyAndRender();
</script>
</body>
</html>
"""
