---
name: summarize
description: Use this skill to summarize ANY content at a chosen length — web pages, articles, YouTube/podcasts, PDFs, audio/video, RSS, or piped text — and record the summary with a verifiable link to its source. Triggers: "summarize this", "tl;dr", a URL/file/video/podcast to condense, "what's in this <link/pdf/episode>", "give me the gist".
origin: adapted from steipete/summarize (MIT) — media pipeline delegated to that CLI; provenance + compression are korgex-native
---

# Summarize — content extraction + summarization, with provenance

Summarize anything into a length the user asked for, then make the summary **traceable to its
exact source** (the korgex value-add over a plain summarizer). Pick the lightest path that
handles the input — don't reach for heavy media tooling when a fetch will do.

## When to Use
- The user shares a URL, file, video, podcast, or PDF and wants the gist.
- Condensing a long transcript / article / thread before acting on it.
- "tl;dr", "summarize", "what's in this".

## Routing — lightest path first
1. **Plain text / stdin / a file already in context** → summarize directly with the model. No tools.
2. **Web page / article URL** → korgex `WebFetch`; if the page is JS-heavy or blocked, fall back to the **browser** tool (`browser.py`) to render, then extract the main content and summarize.
3. **Media** — YouTube, podcasts, Spotify/Apple episodes, audio/video files, image/PDF needing OCR, RSS → **delegate to the `summarize` CLI** (steipete/summarize) if it's installed (`command -v summarize`). It owns yt-dlp / Whisper transcription / ffmpeg / tesseract — do **not** reimplement that. Run e.g. `summarize "<url-or-file>" --length <len> --format markdown`. If the CLI is absent, say so and ask for a transcript/text, or offer to summarize a text fallback.

## Options (match `summarize`'s ergonomics)
- **length**: `short` · `medium` (default) · `long` · `xl`
- **format**: `markdown` (default) · `json` · `text`
- **language**: default = the source's language unless asked otherwise
- **smart default**: if the source is already shorter than the requested summary, **return it unchanged** (don't pad) unless the user forces a summary.

## korgex value-add — what makes this more than running a summarizer
1. **Provenance as a verifiable event.** After summarizing, emit a ledger event recording
   `{ source (URL or content hash), length, format, model, timestamp }`. Any summary is then
   traceable to the exact source it came from — "this summary was produced from this input,"
   checkable later. This is the korg-native bit: a summary is a cognition event, not a throwaway.
2. **Never lose the source (context compression).** Seal the *full* extracted content once as a
   content-ref; the summary is the compact in-context view; the agent can `Retrieve(ref)` the exact
   bytes when a detail is needed. The summary saves tokens without discarding the original.
3. **Report cost + timing** of the run (extraction + LLM) so the user sees what it took.

## Steps
1. **Classify** the input: text / web-url / media.
2. **Extract** via the lightest path above (model-only → WebFetch → browser → `summarize` CLI).
3. **Summarize** at the requested length + format (honor the smart default).
4. **Seal** the full source as a content-ref and **record** the `summary.created` ledger event.
5. **Return** the summary, plus the source ref so the user (or a later turn) can verify or expand it.

## Notes
- Prefer the model directly for short text — invoking media tooling on a paragraph is waste.
- For paywalled/blocked pages, the browser tool or the CLI's Firecrawl fallback is the escalation.
- Keep summaries faithful: a summary preserves proportions of the source; it is not a fold or a
  cherry-pick. If the user wants only the load-bearing point, that's a different ask (use logic-folding).
