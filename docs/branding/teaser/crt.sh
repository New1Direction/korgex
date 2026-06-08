#!/usr/bin/env bash
# CRT post-process for the launch teaser.
# Input : raw.mp4   (produced by `vhs banner.tape`)
# Output: teaser-crt.mp4  (CRT-shaded, social/README mp4)
#         teaser.gif       (palette-optimized GIF for the README)
#
# The CRT look = scanlines (darken every other row) + bloom (screen-blend a
# blurred copy) + a gentle barrel bulge + vignette. All in ffmpeg, no GUI.
set -euo pipefail
cd "$(dirname "$0")"

IN="${1:-raw.mp4}"
[ -f "$IN" ] || { echo "missing $IN — run: vhs banner.tape" >&2; exit 1; }

echo "→ CRT pass …"
ffmpeg -y -loglevel error -i "$IN" -filter_complex "
[0:v]format=rgb24,
geq=r='r(X,Y)*(0.82+0.18*mod(Y,2))':g='g(X,Y)*(0.82+0.18*mod(Y,2))':b='b(X,Y)*(0.82+0.18*mod(Y,2))'[sl];
[sl]split=2[base][bl];
[bl]gblur=sigma=3.5[bloom];
[base][bloom]blend=all_mode=screen:all_opacity=0.35[lit];
[lit]lenscorrection=k1=-0.04:k2=-0.008:fc=black@1.0,
vignette=angle=PI/5[out]
" -map "[out]" -c:v libx264 -pix_fmt yuv420p -crf 18 -movflags +faststart teaser-crt.mp4

echo "→ GIF (palette-optimized) …"
ffmpeg -y -loglevel error -i teaser-crt.mp4 -filter_complex "
fps=15,scale=800:-1:flags=lanczos,split[s0][s1];
[s0]palettegen=max_colors=128:stats_mode=diff[p];
[s1][p]paletteuse=dither=bayer:bayer_scale=4" teaser.gif

echo "done:"
ls -lh teaser-crt.mp4 teaser.gif
