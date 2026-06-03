# korgex brand kit — Y2K / PS2 / Frutiger-Aero

**The look:** a dark Y2K-techno spine (PS2-boot navy→black, chrome type, particles, CRT
scanlines, lens flare) + Frutiger-Aero gloss accents (glassy ✓, aqua highlights) + bold-ad
energy (loud stickers, high contrast). Rule of thumb: **loud brand skin, sober proof underneath** —
the banner earns the click, the clean green ✓ on the `verify` shots closes the trust.

## Palette

| Role | Hex |
|---|---|
| Background — PS2 navy-black | `#060B16` |
| Surface / panel | `#0A1120` |
| Chrome / foreground text | `#D6ECFF` |
| Accent — cyan (primary) | `#2DE2FF` |
| Accent — electric blue | `#2D9BFF` |
| Success / ledger hashes — phosphor green | `#4DFFA6` |
| Sticker yellow | `#FFD23F` |
| Alert red | `#FF5566` |
| Holo magenta | `#B48CFF` |

## Type

- **Display (wordmark, headers):** chrome/techno — Eurostile / Microgramma, Bank Gothic, or a
  chromed bold grotesque. The banner wordmark is the reference.
- **Pixel / Y2K accents:** [Departure Mono](https://departuremono.com/) (free) or
  [Px437 IBM VGA](https://int10h.org/oldschool-pc-fonts/) for hard pixel.
- **Body / UI (the Aero touch):** Frutiger or Myriad.

## Terminal theme — so screenshots + the demo GIF match the banner

1. **Colors:** iTerm2 → Settings → Profiles → Colors → Color Presets → **Import…** →
   `korgex-y2k.itermcolors`. For kitty/Alacritty/Ghostty, use the palette above
   (bg `#060B16`, fg `#D6ECFF`, cyan `#2DE2FF`, green `#4DFFA6`).
2. **Font:** Departure Mono, ~18–22pt.
3. **The CRT look (the move):** record in
   [`cool-retro-term`](https://github.com/Swordfish90/cool-retro-term) → Settings →
   background `#060B16`, font tint `#D6ECFF`, enable **Scanlines** + a little **Bloom/Glow**,
   frame = "No Frame". One step and a plain capture reads like the banner.

## Screenshot recipe

- **`demo.gif`** → `vhs demo.tape` (needs `brew install vhs` + an API key set; tune the prompt + sleeps inside the tape).
- **`verify.png`** → run `korgex verify` (green ✓). Tamper shot: change one byte in
  `.korg/journal.jsonl`, re-run → red ✗ with the bad `seq_id`. Screenshot both, stack them.
- **`audit.png`** → `korgex audit --html audit.html`, open in a browser, screenshot the
  "✓ INTACT" report + its live tamper-test button.
- Keep the proof shots legible and serious. Save the loud treatment for the banner + social card.

> The hand-made hero (`../images/banner.jpg`) is AI-generated (Grok) and intentionally edgy.
> Swap in a revised file at the same path anytime — the README points at `docs/images/banner.jpg`.
