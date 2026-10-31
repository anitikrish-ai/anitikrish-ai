#!/usr/bin/env python3
"""Render data/contributions.json as an animated contribution heatmap SVG.

* 53-week grid (Sunday-first rows), rounded cells, 5-level palette.
* Diagonal reveal via CSS animation; plays once, then settles. Cells are
  visible by default, so renderers without animation show the final frame.
* Statistics are computed only from the data. Anything the data cannot support
  is shown as "n/a" (never as 0).
* Refuses to render when the data file is missing, empty or not status "ok", and
  writes atomically, so a good existing SVG is never replaced by a bad one.

Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
PITCH = 14
CELL = 11
PAD = 20
LEFT = 30
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class Day:
    date: dt.date
    level: int
    count: int | None


def load(path: Path) -> tuple[dict, list[Day]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Cannot read {path}: {exc}")
    if data.get("status") != "ok" or not data.get("days"):
        raise SystemExit(
            f"{path} has no fetched data (status={data.get('status')!r}). "
            "Run scripts/fetch_contributions.py first. Nothing was rendered."
        )
    days = [Day(dt.date.fromisoformat(d["date"]), int(d["level"]), d.get("count")) for d in data["days"]]
    days.sort(key=lambda d: d.date)
    return data, days


def streaks(days: list[Day]) -> tuple[int, int]:
    longest = run = 0
    for d in days:
        run = run + 1 if d.level > 0 else 0
        longest = max(longest, run)
    i = len(days) - 1
    if i >= 0 and days[i].level == 0:
        i -= 1  # today may simply not have contributions yet
    current = 0
    while i >= 0 and days[i].level > 0:
        current += 1
        i -= 1
    return longest, current


def compute_stats(data: dict, days: list[Day]) -> list[tuple[str, str]]:
    have_counts = all(d.count is not None for d in days)
    longest, current = streaks(days)
    active = sum(1 for d in days if d.level > 0)
    stats: list[tuple[str, str]] = []

    if have_counts:
        total = sum(d.count for d in days)  # type: ignore[misc]
        stats.append(("total", f"{total:,}"))
    elif data.get("total_reported") is not None:
        stats.append(("total", f"{data['total_reported']:,}"))
    else:
        stats.append(("total", "n/a"))
    stats.append(("active days", f"{active}"))
    stats.append(("longest streak", f"{longest} d"))
    stats.append(("current streak", f"{current} d"))

    if have_counts:
        best = max(days, key=lambda d: d.count)  # type: ignore[arg-type,return-value]
        stats.append(("best day", f"{best.count} ({best.date.isoformat()})" if best.count else "n/a"))
        monthly: dict[tuple[int, int], int] = defaultdict(int)
        for d in days:
            monthly[(d.date.year, d.date.month)] += d.count  # type: ignore[operator]
        (y, m), n = max(monthly.items(), key=lambda kv: kv[1])
        stats.append(("busiest month", f"{MONTHS[m - 1]} {y} ({n})" if n else "n/a"))
    else:
        stats.append(("best day", "n/a"))
        stats.append(("busiest month", "n/a"))
    return stats


def tspans(pairs: list[tuple[str, str]]) -> str:
    out = []
    for i, (k, v) in enumerate(pairs):
        dx = ' dx="16"' if i else ""
        out.append(f'<tspan class="dim"{dx}>{escape(k)} </tspan><tspan class="hi">{escape(v)}</tspan>')
    return "".join(out)


def render(data: dict, days: list[Day]) -> str:
    first = days[0].date
    first_row = (first.weekday() + 1) % 7  # Sunday = 0
    start = first - dt.timedelta(days=first_row)

    cells: list[str] = []
    month_labels: list[tuple[int, str]] = []
    last_label_col = -10
    max_col = 0
    for d in days:
        offset = (d.date - start).days
        col, row = divmod(offset, 7)
        max_col = max(max_col, col)
        if d.date.day == 1 and col - last_label_col >= 3:
            month_labels.append((col, MONTHS[d.date.month - 1]))
            last_label_col = col
        x = PAD + LEFT + col * PITCH
        y = PAD + 18 + row * PITCH
        delay = 0.15 + (col + row) * 0.018
        cells.append(
            f'<rect class="c l{d.level}" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2.5" '
            f'style="animation-delay:{delay:.3f}s"/>'
        )

    ncols = max_col + 1
    width = PAD * 2 + LEFT + ncols * PITCH
    grid_bottom = PAD + 18 + 7 * PITCH - (PITCH - CELL)
    stats_y = grid_bottom + 24
    height = stats_y + 3 * 18 + PAD - 6

    labels = [
        f'<text class="t dim" x="{PAD + LEFT + col * PITCH}" y="{PAD + 10}">{name}</text>'
        for col, name in month_labels
    ]
    for row, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        labels.append(f'<text class="t dim" x="{PAD}" y="{PAD + 18 + row * PITCH + 9}">{name}</text>')

    stats = compute_stats(data, days)
    line1 = tspans(stats[:4])
    line2 = tspans(stats[4:])
    fetched = str(data.get("fetched_at", ""))[:10] or "unknown"
    note1 = "source: public contribution calendar (scraped HTML, about the last year); not the authenticated API"
    note2 = f"range {data.get('range_from', days[0].date)} to {data.get('range_to', days[-1].date)} | fetched {fetched}"

    block_x0 = width - PAD - 27 - 6 - (5 * PITCH - (PITCH - CELL))
    legend = [f'<text class="t dim" x="{block_x0 - 6}" y="{stats_y}" text-anchor="end">Less</text>']
    for i in range(5):
        legend.append(
            f'<rect class="l{i}" x="{block_x0 + i * PITCH}" y="{stats_y - 10}" width="{CELL}" height="{CELL}" rx="2.5"/>'
        )
    legend.append(f'<text class="t dim" x="{width - PAD}" y="{stats_y}" text-anchor="end">More</text>')

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-labelledby="t d">
<title id="t">GitHub contribution heatmap for {escape(str(data.get("username", "")))}</title>
<desc id="d">Contribution calendar from {days[0].date} to {days[-1].date}. {escape('; '.join(f'{k}: {v}' for k, v in stats))}.</desc>
<style>
.bg{{fill:#0d1117;stroke:#30363d}}
.t{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;font-size:11px}}
text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;font-size:11px}}
.dim{{fill:#8b949e}}
.hi{{fill:#e6edf3}}
.l0{{fill:#161b22}}.l1{{fill:#0e4429}}.l2{{fill:#006d32}}.l3{{fill:#26a641}}.l4{{fill:#39d353}}
.c{{animation:fade .45s ease-out both}}
.f{{animation:fade .6s ease-out 1.4s both}}
@keyframes fade{{from{{opacity:0}}to{{opacity:1}}}}
@media (prefers-reduced-motion:reduce){{.c,.f{{animation:none}}}}
</style>
<rect class="bg" x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10"/>
{chr(10).join(labels)}
{chr(10).join(cells)}
<g class="f">
<text x="{PAD}" y="{stats_y}">{line1}</text>
<text x="{PAD}" y="{stats_y + 18}">{line2}</text>
<text class="dim" x="{PAD}" y="{stats_y + 36}" style="font-size:10px">{escape(note1)}</text>
<text class="dim" x="{PAD}" y="{stats_y + 50}" style="font-size:10px">{escape(note2)}</text>
{chr(10).join(legend)}
</g>
</svg>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--input", type=Path, default=ROOT / "data" / "contributions.json")
    ap.add_argument("--output", type=Path, default=ROOT / "contrib-heatmap.svg")
    args = ap.parse_args()

    data, days = load(args.input)
    svg = render(data, days)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(".svg.tmp")
    tmp.write_text(svg, encoding="utf-8")
    os.replace(tmp, args.output)
    print(f"wrote {args.output} ({len(days)} days)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
