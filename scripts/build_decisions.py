#!/usr/bin/env python3
"""Write the undecided papers as markdown with clickable DOI links.

`unspecified` verdicts are the ones a screener refused to guess on. They need a
human, and a human needs to open the paper -- so the deliverable is a markdown
file with a link per paper and the reason it could not be settled.

  uv run scripts/build_decisions.py
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

from common import DATA, LIBRARY, load_screening, read_json


def main() -> int:
    verdicts, seen = load_screening(), {}

    sw = (read_json(DATA / "sweep-candidates.json", {}) or {}).get("candidates", {})
    ing = (read_json(DATA / "ingest.json", {}) or {}).get("records", {})
    for r in ing.values():
        if r.get("doi"):
            seen[r["doi"]] = r
    man = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})
    for p in man.values():
        if p.get("doi"):
            seen[p["doi"]] = p

    filed = {r.get("doi") for r in ing.values()
             if r.get("doi") and (LIBRARY / (r.get("file") or "")).exists()}
    have_pdf = filed | {p.get("doi") for p in man.values() if p.get("doi")}

    rows = []
    for doi, v in verdicts.items():
        if v["verdict"] != "unspecified":
            continue
        meta = seen.get(doi) or sw.get(doi) or {}
        rows.append({
            "doi": doi,
            "title": meta.get("title") or v.get("citekey") or "(title unknown)",
            "year": meta.get("year"),
            "journal": meta.get("journal") or "",
            "cls": v.get("record_class") or meta.get("record_class") or "",
            "reason": v.get("reason") or "",
            # An `unspecified` paper is neither in nor out: rename.py gives it
            # no manifest entry, but its PDF may already be filed. What the person
            # deciding needs to know is whether it has to be fetched.
            "have_pdf": doi in have_pdf,
        })
    rows.sort(key=lambda r: (-(r["year"] or 0), r["title"].lower()))

    out = [
        "# Papers needing your decision",
        "",
        f"**{len(rows)} papers.** A screener returned `unspecified` on each — it could not settle "
        "one of the four conditions and refused to guess.",
        "",
        "The bar is [`INCLUSION.md`](../INCLUSION.md). The test is: **did the authors report an "
        "original ultrasound exposure delivered to neural tissue with the intent of modulating "
        "its function?** All four of A original, B ultrasound, C on neural tissue, D intent to "
        "modulate must hold.",
        "",
        "Reply with a verdict per DOI and I will apply them.",
        "",
        f"**{sum(1 for r in rows if r['have_pdf'])} of them we already have the PDF for** — "
        "those are unresolved records sitting in `library/` with no manifest entry. The rest "
        "would need fetching if you rule them in.",
        "",
        "---",
        "",
    ]
    for i, r in enumerate(rows, 1):
        bits = [str(r["year"] or "?")]
        if r["journal"]:
            bits.append(f"*{r['journal']}*")
        if r["cls"]:
            bits.append(r["cls"].replace("_", " "))
        bits.append("**PDF held**" if r["have_pdf"] else "PDF not held")
        out += [
            f"### {i}. [{r['title']}](https://doi.org/{r['doi']})",
            "",
            f"{' · '.join(bits)}  ",
            f"`{r['doi']}`",
            "",
            f"> **Why it could not be settled:** {r['reason']}",
            "",
        ]

    (DATA / "needs-decision.md").write_text("\n".join(out), encoding="utf-8")
    print(f"wrote data/needs-decision.md ({len(rows)} papers, "
          f"{sum(1 for r in rows if r['have_pdf'])} with PDFs held)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
