#!/usr/bin/env python3
"""Fetch the PUBLIC GitHub contribution calendar for one user (no token).

Source: https://github.com/users/<user>/contributions (the HTML fragment the
profile page loads). This is scraped HTML, not the authenticated GraphQL API:
it only covers roughly the last year, shows what GitHub exposes publicly
(private contributions appear only if the user enabled that setting), and its
markup can change without notice.

Safety rules:
  * Never writes fabricated or empty data. On any failure it exits non-zero and
    leaves the existing data file untouched.
  * Writes atomically, and skips the write when nothing changed so the daily
    workflow does not create empty commits.

Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib import error, request

DEFAULT_USER = "anitikrish-ai"
URL_TEMPLATE = "https://github.com/users/{user}/contributions"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "contributions.json"
USER_AGENT = "anitikrish-ai-profile-readme/1.0 (+https://github.com/anitikrish-ai/anitikrish-ai)"
MAX_ATTEMPTS = 4
MIN_DAYS = 330
MAX_DAYS = 400
COUNT_RE = re.compile(r"^\s*(No|\d[\d,]*)\s+contributions?\b", re.IGNORECASE)
TOTAL_RE = re.compile(
    r"([\d,]+)\s+contributions?\s+in\s+(?:the\s+last\s+year|\d{4})", re.IGNORECASE
)


class FetchError(RuntimeError):
    """Raised for any condition where we must not write output."""


class CalendarParser(HTMLParser):
    """Collects day cells (data-date / data-level) and their tooltips."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[dict] = []
        self.tips: dict[str, str] = {}
        self._cell: dict | None = None
        self._tip_for: str | None = None
        self._tip_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "td" and a.get("data-date"):
            self._cell = {
                "id": a.get("id"),
                "date": a.get("data-date"),
                "level": a.get("data-level"),
                "count": a.get("data-count"),
                "text": [],
            }
            self.cells.append(self._cell)
        elif tag == "tool-tip" and a.get("for"):
            self._tip_for = a["for"]
            self._tip_parts = []

    def handle_endtag(self, tag):
        if tag == "td":
            self._cell = None
        elif tag == "tool-tip" and self._tip_for is not None:
            self.tips[self._tip_for] = "".join(self._tip_parts).strip()
            self._tip_for = None

    def handle_data(self, data):
        if self._tip_for is not None:
            self._tip_parts.append(data)
        elif self._cell is not None:
            self._cell["text"].append(data)


def count_from_text(text: str | None) -> int | None:
    if not text:
        return None
    m = COUNT_RE.match(text)
    if not m:
        return None
    word = m.group(1)
    return 0 if word.lower() == "no" else int(word.replace(",", ""))


def extract(html: str) -> tuple[list[dict], int | None]:
    parser = CalendarParser()
    parser.feed(html)
    parser.close()
    if not parser.cells:
        raise FetchError(
            "no contribution cells found: the markup changed, the user does not "
            "exist, or GitHub returned a non-calendar page"
        )

    days: dict[str, dict] = {}
    for cell in parser.cells:
        try:
            date = dt.date.fromisoformat(cell["date"])
            level = int(cell["level"])
        except (TypeError, ValueError):
            raise FetchError(f"unparseable day cell: {cell['date']!r} / level {cell['level']!r}")
        if not 0 <= level <= 4:
            raise FetchError(f"contribution level out of range on {date}: {level}")

        count: int | None = None
        raw = cell.get("count")
        if raw is not None and str(raw).strip().isdigit():
            count = int(raw)
        if count is None:
            count = count_from_text(parser.tips.get(cell["id"] or "")) 
        if count is None:
            count = count_from_text("".join(cell["text"]))

        if count is not None and ((level == 0) != (count == 0)):
            raise FetchError(f"level {level} conflicts with count {count} on {date}; markup likely changed")
        key = date.isoformat()
        if key in days:
            raise FetchError(f"duplicate date in calendar: {key}")
        days[key] = {"date": key, "level": level, "count": count}

    ordered = [days[k] for k in sorted(days)]
    if not MIN_DAYS <= len(ordered) <= MAX_DAYS:
        raise FetchError(f"unexpected number of days: {len(ordered)}")
    for prev, cur in zip(ordered, ordered[1:]):
        gap = (dt.date.fromisoformat(cur["date"]) - dt.date.fromisoformat(prev["date"])).days
        if gap != 1:
            raise FetchError(f"gap in calendar between {prev['date']} and {cur['date']}")

    total = None
    m = TOTAL_RE.search(html)
    if m:
        total = int(m.group(1).replace(",", ""))
    return ordered, total


def retry_after_seconds(exc: error.HTTPError) -> int | None:
    value = exc.headers.get("Retry-After") if exc.headers else None
    if value and value.strip().isdigit():
        return int(value.strip())
    return None


def fetch_html(url: str) -> str:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        try:
            with request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            if exc.code == 404:
                raise FetchError(f"404 from {url}: user not found")
            retryable = exc.code in (403, 429, 500, 502, 503, 504)
            if retryable and attempt < MAX_ATTEMPTS:
                wait = min(retry_after_seconds(exc) or 5 * 2**attempt, 120)
                print(f"HTTP {exc.code}; retrying in {wait}s (attempt {attempt}/{MAX_ATTEMPTS})", file=sys.stderr)
                time.sleep(wait)
                continue
            raise FetchError(f"HTTP {exc.code} from {url} (rate limited or blocked?)")
        except (error.URLError, TimeoutError) as exc:
            if attempt < MAX_ATTEMPTS:
                wait = 3 * 2**attempt
                print(f"network error ({exc}); retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise FetchError(f"network error fetching {url}: {exc}")
    raise FetchError("exhausted retries")


def load_existing(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def serialise(meta: dict, days: list[dict]) -> str:
    lines = ["{"]
    for key, value in meta.items():
        lines.append(f"  {json.dumps(key)}: {json.dumps(value)},")
    lines.append('  "days": [')
    lines.append(",\n".join("    " + json.dumps(d, separators=(",", ":")) for d in days))
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    url = URL_TEMPLATE.format(user=args.user)
    try:
        html = fetch_html(url)
        days, total = extract(html)
    except FetchError as exc:
        print(f"ERROR: {exc}. Existing data left untouched.", file=sys.stderr)
        return 1

    counts_available = all(d["count"] is not None for d in days)
    if counts_available and total is not None:
        summed = sum(d["count"] for d in days)
        if summed != total:
            print(f"warning: day counts sum to {summed} but page reports {total}", file=sys.stderr)

    existing = load_existing(args.output)
    if (
        existing
        and existing.get("status") == "ok"
        and existing.get("username") == args.user
        and existing.get("days") == days
        and existing.get("total_reported") == total
    ):
        print("No change in contribution data; file not rewritten.")
        return 0

    meta = {
        "schema_version": 1,
        "status": "ok",
        "username": args.user,
        "source_url": url,
        "source_note": "Scraped public HTML contribution calendar (about the last year); not the authenticated GraphQL API.",
        "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "range_from": days[0]["date"],
        "range_to": days[-1]["date"],
        "total_reported": total,
        "counts_available": counts_available,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(".json.tmp")
    tmp.write_text(serialise(meta, days), encoding="utf-8")
    os.replace(tmp, args.output)
    print(f"wrote {args.output}: {len(days)} days, {days[0]['date']} to {days[-1]['date']}, total_reported={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
