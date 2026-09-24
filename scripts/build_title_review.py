#!/usr/bin/env python3
"""List the candidates that only a person can decide: no abstract anywhere, a title that looks like
TUS work, and not decided by the prescreen. The bar forbids a model screening on a title alone, so
these wait for a human glance. Writes data/title-review.md, newest first, one line per paper with
the DOI as a link.

  uv run scripts/build_title_review.py                  # 2025 onwards
  uv run scripts/build_title_review.py --from-year 2018
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import sys

from build_batches import TUS_LIKE, already_decided
from common import DATA, read_json

OUT = DATA / "title-review.md"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-year", type=int, default=2025)
    args = ap.parse_args()
    sw = (read_json(DATA / "sweep-candidates.json", {}) or {}).get("candidates", {})
    decided = already_decided()
    rows = [(doi, v) for doi, v in sw.items()
            if doi not in decided and v.get("title") and not v.get("abstract")
            and (v.get("year") or 0) >= args.from_year and TUS_LIKE.search(v["title"])]
    rows.sort(key=lambda r: (-(r[1].get("year") or 0), (r[1].get("journal") or ""), r[1]["title"]))
    by_year = collections.Counter(v.get("year") for _, v in rows)

    lines = [
        "# Title-only candidates for a human decision",
        "",
        f"**{len(rows)} papers**, generated {dt.date.today()} by `scripts/build_title_review.py`. Each has no abstract in Europe PMC, PubMed, Crossref or OpenAlex, has a title that looks like ultrasound neuromodulation, and was not settled by the prescreen's type and title rules. The bar forbids screening on a title alone, so these wait for a person.",
        "",
        "Reply with a verdict per DOI (relevant / not relevant / needs the paper) and it will be recorded as a verdict file. Anything relevant goes onto the want-list.",
        "",
        "Counts by year: " + ", ".join(f"{y}: {n}" for y, n in sorted(by_year.items(), reverse=True)),
        "",
        "| | year | journal | title | DOI |",
        "|---|---|---|---|---|",
    ]
    for doi, v in rows:
        t = " ".join(v["title"].split()).replace("|", "/")
        j = (v.get("journal") or v.get("type") or "").replace("|", "/")
        lines.append(f"| ☐ | {v.get('year')} | {j[:40]} | {t} | [{doi}](https://doi.org/{doi}) |")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(rows)} papers; {dict(sorted(by_year.items(), reverse=True))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
