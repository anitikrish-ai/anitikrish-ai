#!/usr/bin/env python3
"""Generate the Neofetch-style developer card (info-card.svg).

Content comes from data/profile.json so you can edit it without touching code.
Only put facts you can verify there. Each line fades in with a short stagger
(CSS only, plays once). Lines are visible by default, so renderers without
animation support show the finished card.

Wrapping assumes about 7.8 px per character at 13 px monospace. Fonts differ a
little, so leave some slack in the panel width.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
CHAR_W = 7.8


def build_lines(profile: dict, wrap: int) -> list[dict]:
    user = profile["username"]
    host = profile.get("host", "github")
    lines: list[dict] = [
        {"kind": "title", "user": user, "host": host},
        {"kind": "rule", "text": "-" * len(f"{user}@{host}")},
    ]
    for block in profile["blocks"]:
        kind = block.get("type")
        if kind in ("kv", "item"):
            label = block["key"] if kind == "kv" else block["name"]
            parts = textwrap.wrap(block["value"], wrap) or [""]
            for i, part in enumerate(parts):
                lines.append({"kind": kind, "key": label if i == 0 else "", "text": part})
        elif kind == "gap":
            lines.append({"kind": "gap"})
        elif kind == "heading":
            lines.append({"kind": "heading", "text": block["text"]})
        else:
            raise ValueError(f"unknown block type: {kind!r}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--input", type=Path, default=ROOT / "data" / "profile.json")
    ap.add_argument("--output", type=Path, default=ROOT / "info-card.svg")
    ap.add_argument("--width", type=int, default=560)
    ap.add_argument("--height", type=int, default=0, help="minimum height; use the portrait height to align panels")
    ap.add_argument("--padding", type=int, default=24)
    ap.add_argument("--line-height", type=int, default=20)
    ap.add_argument("--font-size", type=int, default=13)
    ap.add_argument("--key-width", type=int, default=120, help="pixels reserved for the key column")
    args = ap.parse_args()

    try:
        profile = json.loads(args.input.read_text(encoding="utf-8"))
        wrap = int((args.width - args.padding * 2 - args.key_width) / CHAR_W) - 2
        lines = build_lines(profile, wrap)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Cannot build card from {args.input}: {exc}", file=sys.stderr)
        return 2

    pad, lh, key_w = args.padding, args.line_height, args.key_width
    val_x = pad + key_w
    height = max(args.height, pad * 2 + len(lines) * lh)

    out: list[str] = []
    n = 0
    for i, line in enumerate(lines):
        kind = line["kind"]
        if kind == "gap":
            continue
        y = pad + i * lh + lh * 0.75
        delay = 0.2 + n * 0.07
        n += 1
        head = f'<text class="ln" x="{pad}" y="{y:.1f}" style="animation-delay:{delay:.2f}s">'
        if kind == "title":
            out.append(
                f'{head}<tspan class="usr">{escape(line["user"])}</tspan>'
                f'<tspan class="dim">@</tspan><tspan class="usr">{escape(line["host"])}</tspan></text>'
            )
        elif kind == "rule":
            out.append(f'{head}<tspan class="dim">{escape(line["text"])}</tspan></text>')
        elif kind == "heading":
            out.append(f'{head}<tspan class="hd">{escape(line["text"])}</tspan></text>')
        else:
            key_cls = "key" if kind == "kv" else "nm"
            key = f'<tspan class="{key_cls}">{escape(line["key"])}</tspan>' if line["key"] else ""
            out.append(f'{head}{key}<tspan class="val" x="{val_x}">{escape(line["text"])}</tspan></text>')

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {args.width} {height}" width="{args.width}" height="{height}" role="img" aria-labelledby="t d">
<title id="t">Developer card for {escape(profile["username"])}</title>
<desc id="d">Terminal-style card listing role, focus, languages, interests and public repositories.</desc>
<style>
.bg{{fill:#0d1117;stroke:#30363d}}
text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;font-size:{args.font_size}px}}
.usr,.key{{fill:#7ee787}}
.usr,.hd{{font-weight:700}}
.nm,.hd{{fill:#e6edf3}}
.val{{fill:#c9d1d9}}
.dim{{fill:#6e7681}}
.ln{{animation:in .5s ease-out both}}
@keyframes in{{from{{opacity:0;transform:translateX(-6px)}}to{{opacity:1;transform:translateX(0)}}}}
@media (prefers-reduced-motion:reduce){{.ln{{animation:none}}}}
</style>
<rect class="bg" x="0.5" y="0.5" width="{args.width - 1}" height="{height - 1}" rx="10"/>
{chr(10).join(out)}
</svg>
"""

    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(".svg.tmp")
    tmp.write_text(svg, encoding="utf-8")
    os.replace(tmp, args.output)
    print(f"wrote {args.output}  ({args.width} x {height}, {n} animated lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
