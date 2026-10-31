#!/usr/bin/env python3
"""Convert the prepared portrait into a monochrome, self-typing ASCII SVG.

Input : assets/generated/portrait_gray.png (+ portrait_mask.png) from prep_photo.py
Output: avi-ascii.svg

Animation: each row appears in sequence with a small block cursor that follows
the typing, then every element settles and the cursor disappears. Animation is
plain CSS inside the SVG (no JavaScript, no external files). Elements are
visible by default, so renderers without animation support show the finished
portrait.

Each row uses textLength so glyph widths stay consistent across fonts.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
GEN_DIR = ROOT / "assets" / "generated"
RAMP = " .:-=+*#%@"  # sparse -> dense


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--input", type=Path, default=GEN_DIR / "portrait_gray.png")
    ap.add_argument("--mask", type=Path, default=GEN_DIR / "portrait_mask.png")
    ap.add_argument("--output", type=Path, default=ROOT / "avi-ascii.svg")
    ap.add_argument("--cols", type=int, default=96, help="characters per row")
    ap.add_argument("--font-size", type=float, default=8.4)
    ap.add_argument("--char-width", type=float, default=5.04, help="advance per character (about 0.6 x font size)")
    ap.add_argument("--char-height", type=float, default=9.0, help="row pitch")
    ap.add_argument("--padding", type=float, default=20.0)
    ap.add_argument("--duration", type=float, default=4.0, help="total typing time in seconds")
    ap.add_argument("--gamma", type=float, default=0.85, help="<1 brightens mid-tones")
    ap.add_argument(
        "--invert",
        action="store_true",
        help="dense characters for LIGHT pixels instead of dark ones",
    )
    args = ap.parse_args()

    if not args.input.is_file():
        print(f"Missing {args.input}. Run scripts/prep_photo.py first.", file=sys.stderr)
        return 2

    gray = Image.open(args.input).convert("L")
    if args.mask.is_file():
        mask = Image.open(args.mask).convert("L")
        if mask.size != gray.size:
            mask = mask.resize(gray.size, Image.Resampling.BILINEAR)
    else:
        mask = Image.new("L", gray.size, 255)

    cols = args.cols
    cw, ch, fs, pad = args.char_width, args.char_height, args.font_size, args.padding
    rows = max(1, round(cols * gray.height / gray.width * (cw / ch)))

    small = gray.resize((cols, rows), Image.Resampling.LANCZOS)
    small_mask = mask.resize((cols, rows), Image.Resampling.BOX)

    lum = np.asarray(small, dtype=np.float32) / 255.0
    subject = (np.asarray(small_mask, dtype=np.float32) / 255.0) >= 0.5
    density = lum if args.invert else 1.0 - lum

    values = density[subject]
    if values.size == 0:
        print("Subject mask is empty; nothing to draw.", file=sys.stderr)
        return 1
    lo, hi = np.percentile(values, [2, 98])
    if hi - lo < 1e-3:
        hi = lo + 1e-3
    density = np.clip((density - lo) / (hi - lo), 0.0, 1.0) ** args.gamma
    idx = np.rint(density * (len(RAMP) - 1)).astype(int)
    idx[~subject] = 0

    width = cols * cw + 2 * pad
    height = rows * ch + 2 * pad
    step = args.duration / rows
    start = 0.25

    body: list[str] = []
    for i in range(rows):
        row = "".join(RAMP[j] for j in idx[i])
        trimmed = row.strip(" ")
        if not trimmed:
            continue
        lead = len(row) - len(row.lstrip(" "))
        x = pad + lead * cw
        y = pad + i * ch + fs
        delay = start + i * step
        body.append(
            f'<text class="r" x="{x:.2f}" y="{y:.2f}" textLength="{len(trimmed) * cw:.2f}" '
            f'lengthAdjust="spacing" xml:space="preserve" style="animation-delay:{delay:.3f}s">'
            f"{escape(trimmed)}</text>"
        )
        cx = x + len(trimmed) * cw + 1
        body.append(
            f'<rect class="k" x="{cx:.2f}" y="{pad + i * ch + 1:.2f}" width="{cw * 0.7:.2f}" '
            f'height="{ch - 2:.2f}" opacity="0" '
            f'style="animation:cur {max(step, 0.05):.3f}s linear {delay:.3f}s both"/>'
        )

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" height="{height:.0f}" role="img" aria-labelledby="t d">
<title id="t">ASCII portrait</title>
<desc id="d">Monochrome ASCII-art portrait generated from the profile owner's own image.</desc>
<style>
.bg{{fill:#0d1117;stroke:#30363d}}
.r{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;font-size:{fs}px;fill:#c9d1d9;animation:show .01s steps(1,end) both}}
.k{{fill:#7ee787}}
@keyframes show{{from{{opacity:0}}to{{opacity:1}}}}
@keyframes cur{{0%{{opacity:0}}1%{{opacity:1}}99%{{opacity:1}}100%{{opacity:0}}}}
@media (prefers-reduced-motion:reduce){{.r,.k{{animation:none}}}}
</style>
<rect class="bg" x="0.5" y="0.5" width="{width - 1:.0f}" height="{height - 1:.0f}" rx="10"/>
{chr(10).join(body)}
</svg>
"""

    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(".svg.tmp")
    tmp.write_text(svg, encoding="utf-8")
    os.replace(tmp, args.output)
    print(f"wrote {args.output}  ({width:.0f} x {height:.0f}, {cols} cols x {rows} rows)")
    print("Tip: pass --height {:.0f} to make_info_card.py to match this panel.".format(height))
    return 0


if __name__ == "__main__":
    sys.exit(main())
