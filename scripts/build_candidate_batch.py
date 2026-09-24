#!/usr/bin/env python3
"""Write a screening batch for the candidate PDFs that resolved to a DOI but have no verdict yet.

Papers a person drops into candidates/ by hand were never in a sweep batch, so rename.py refuses
them until they are screened. This builds the batch the paper-screener expects: the Crossref or
sweep abstract, or, when neither exists, the opening of the paper's own extracted text, marked
as such. Then launch a paper-screener on it with docs/screening-prompt.md.

  uv run scripts/build_candidate_batch.py --out <dir>       # writes <dir>/cand_1.md
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

import httpx

from build_batches import already_decided
from common import DATA, MAILTO, read_json, text_path


def clean(s: str | None) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    recs = (read_json(DATA / "ingest.json", {}) or {}).get("records", {})
    decided = already_decided()
    sw = (read_json(DATA / "sweep-candidates.json", {}) or {}).get("candidates", {})
    client = httpx.Client(timeout=30.0, headers={"User-Agent": f"tus-paper-archive/0.1 (mailto:{MAILTO})"})
    out, seen = [], set()
    for fname, r in recs.items():
        doi = (r.get("doi") or "").lower()
        if r.get("status") != "resolved" or not doi or doi in decided or doi in seen:
            continue
        if not (DATA.parent / "candidates" / Path(fname).name).exists():
            continue
        seen.add(doi)
        try:
            m = client.get(f"https://api.crossref.org/works/{doi}", params={"mailto": MAILTO}).json()["message"]
        except Exception:
            m = {}
        title = clean((m.get("title") or [r.get("title") or ""])[0])
        year = ((m.get("issued") or {}).get("date-parts") or [[r.get("year")]])[0][0]
        journal = (m.get("container-title") or [r.get("journal") or ""])[0]
        abst = clean(m.get("abstract")) or clean((sw.get(doi) or {}).get("abstract"))
        if not abst:
            t = text_path(r.get("text_key") or Path(fname).stem)
            if t.exists():
                abst = "[no abstract in Crossref; the opening of the paper's own text follows] " + " ".join(t.read_text()[:2500].split())
        out.append(f"## {doi}\ncited_as: {year}\ntitle: {title}\nyear: {year} | journal: {journal} | type: {m.get('type')}\nabstract: {abst[:1800]}\n\n")
    if not out:
        print("nothing to screen: every resolved candidate has a verdict")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "cand_1.md").write_text("".join(out), encoding="utf-8")
    print(f"wrote {args.out / 'cand_1.md'} ({len(out)} papers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
