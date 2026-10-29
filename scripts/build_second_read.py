#!/usr/bin/env python3
"""Batches for the second screening read: the papers the first screener could not settle.

The first read is a small model on a title and abstract. Every `unspecified` verdict, and every
candidate listed in data/title-review.md, goes to a second read on a stronger model with the
fullest text that is free to fetch: the abstract by DOI (Europe PMC, PubMed, Crossref, DataCite)
and, where Europe PMC holds an open-access copy, the full text with the methods first. A paper
with no text anywhere is still `unspecified` after this read and goes on the want-list or to a
person; the bar forbids deciding it on a title.

With --audit N it also writes batches of N of the first read's exclusions from the named verdict
files, chosen at random with a fixed seed and shown exactly as the first screener saw them. A
stronger model screening those with the ordinary prompt measures how often the first read
excludes a paper it should not have. On 2026-10-26 it found none in 100.

  uv run scripts/build_second_read.py --out <dir>
  uv run scripts/build_second_read.py --out <dir> --audit 100 --audit-from <batch dir>
  uv run scripts/build_second_read.py --out <dir> --unscreened --no-unspecified

--unscreened adds the TUS-like candidates that were never screened at all because
the sweep found no abstract for them. build_batches.py defers those, and
title-review.md lists only recent years, so 349 of all years had sat unread
until 2026-10-27.
"""

from __future__ import annotations

import argparse
import glob
import json
import random
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from build_batches import TUS_LIKE, already_decided
from common import DATA, MAILTO, datacite, load_screening, read_json
from fetch_paper_abstracts import clean, from_pubmed

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
BATCH = 20
FULLTEXT_CHARS = 12000


def methods_first(xml: str) -> str:
    """Methods, then results, then the rest, from a JATS full text."""
    body = ET.fromstring(xml).find(".//body")
    if body is None:
        return ""
    secs = []
    for s in body.findall("./sec"):
        title = (s.findtext("title") or "").strip()
        text = " ".join(" ".join(s.itertext()).split())
        rank = 0 if re.search(r"method|material|procedure|protocol", title, re.I) else \
            1 if re.search(r"result", title, re.I) else 2
        secs.append((rank, f"[{title}] {text}"))
    return " ".join(t for _, t in sorted(secs, key=lambda x: x[0]))[:FULLTEXT_CHARS]


def fetch(c: httpx.Client, doi: str) -> dict:
    """Abstract and open-access full text for one DOI. A failed lookup is reported, never read
    as "no abstract": the record then says the lookup failed."""
    out: dict = {"abstract": None, "source": None, "fulltext": None, "title": None, "errors": []}
    try:
        r = c.get(f"{EPMC}/search", params={"query": f'DOI:"{doi}"', "format": "json",
                                             "resultType": "core", "pageSize": 3})
        r.raise_for_status()
        for w in r.json().get("resultList", {}).get("result", []):
            if (w.get("doi") or "").lower() != doi:
                continue
            out["title"] = w.get("title")
            if w.get("abstractText"):
                out["abstract"], out["source"] = clean(w["abstractText"]), "europepmc"
            if w.get("pmcid") and w.get("isOpenAccess") == "Y":
                x = c.get(f"{EPMC}/{w['pmcid']}/fullTextXML")
                if x.status_code == 200:
                    out["fulltext"] = methods_first(x.text)
            break
    except (httpx.HTTPError, ValueError, ET.ParseError) as e:
        out["errors"].append(f"europepmc {type(e).__name__}")
    if not out["abstract"]:
        try:
            a, _ = from_pubmed(doi, c)
            if a:
                out["abstract"], out["source"] = a, "pubmed"
        except (httpx.HTTPError, ET.ParseError, ValueError) as e:
            out["errors"].append(f"pubmed {type(e).__name__}")
    if not out["abstract"]:
        try:
            r = c.get(f"https://api.crossref.org/works/{doi}")
            if r.status_code == 200:
                m = r.json()["message"]
                out["title"] = out["title"] or (m.get("title") or [None])[0]
                if (a := clean(m.get("abstract"))):
                    out["abstract"], out["source"] = a, "crossref"
            elif r.status_code != 404:
                out["errors"].append(f"crossref HTTP {r.status_code}")
        except (httpx.HTTPError, ValueError) as e:
            out["errors"].append(f"crossref {type(e).__name__}")
    if not out["abstract"] and (dc := datacite(doi)):
        out["title"] = out["title"] or dc.get("title")
        if (a := clean(dc.get("abstract"))):
            out["abstract"], out["source"] = a, "datacite"
    return out


def write_batches(out: Path, blocks: list[str], first: int) -> int:
    for i in range(0, len(blocks), BATCH):
        (out / f"cand_{first + i // BATCH}.md").write_text("\n".join(blocks[i:i + BATCH]))
    return (len(blocks) + BATCH - 1) // BATCH


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--audit", type=int, default=0, help="also sample this many exclusions")
    ap.add_argument("--audit-from", type=Path,
                    help="the first read's batch directory: cand_*.md, with verdicts_*.json beside them or in verdicts/")
    ap.add_argument("--seed", type=int, default=20261026)
    ap.add_argument("--unscreened", action="store_true",
                    help="also TUS-like candidates never screened because they had no abstract")
    ap.add_argument("--no-unspecified", action="store_true",
                    help="leave out the unspecified verdicts and the title-review list")
    args = ap.parse_args()
    if args.audit and not args.audit_from:
        ap.error("--audit needs --audit-from")

    led = load_screening()
    sweep = (read_json(DATA / "sweep-candidates.json", {}) or {}).get("candidates", {})
    title_only = re.findall(r"https://doi\.org/(\S+?)\)",
                            (DATA / "title-review.md").read_text() if (DATA / "title-review.md").exists() else "")
    dois = [] if args.no_unspecified else list(dict.fromkeys(
        [d for d, r in led.items() if r.get("verdict") == "unspecified"] + [d.lower() for d in title_only]))
    if args.unscreened:
        decided = already_decided()
        dois += [d for d, c in sweep.items() if d not in decided and d not in dois
                 and not c.get("abstract") and TUS_LIKE.search(c.get("title") or "")]
    args.out.mkdir(parents=True, exist_ok=True)

    blocks, n_abs, n_ft, failed = [], 0, 0, []
    with httpx.Client(timeout=40, follow_redirects=True,
                      headers={"User-Agent": f"tus-paper-archive/1.0 (mailto:{MAILTO})"}) as c:
        for i, d in enumerate(dois, 1):
            cand = sweep.get(d) or {}
            f = fetch(c, d)
            n_abs += bool(f["abstract"])
            n_ft += bool(f["fulltext"])
            if f["errors"] and not f["abstract"]:
                failed.append(d)
            first = (led.get(d) or {}).get("reason") or "never screened: no abstract in the sweep"
            blocks.append(
                f"## {d}\ntitle: {cand.get('title') or f['title']}\n"
                f"year: {cand.get('year')} | journal: {cand.get('journal')}\n"
                f"first read: {first}\n"
                f"abstract ({f['source'] or 'none found'}): {f['abstract'] or '(none)'}\n"
                + (f"open-access full text, methods first (Europe PMC): {f['fulltext']}\n"
                   if f["fulltext"] else ""))
            if i % 25 == 0:
                print(f"  {i}/{len(dois)}")
            time.sleep(0.2)
    n = write_batches(args.out, blocks, 1)
    print(f"{len(dois)} undecided: {n_abs} with an abstract, {n_ft} with full text; "
          f"{n} batches, cand_1..cand_{n}")
    if failed:
        print(f"  {len(failed)} lookups failed and found nothing; re-run before trusting "
              f"'none found' for: {', '.join(failed[:5])}")

    if args.audit:
        excluded, seen = [], {}
        for f in (glob.glob(str(args.audit_from / "verdicts" / "*.json"))
                  + glob.glob(str(args.audit_from / "verdicts_*.json"))):
            excluded += [r["doi"] for r in json.loads(Path(f).read_text())
                         if r["verdict"] == "not_relevant"]
        for f in glob.glob(str(args.audit_from / "cand_*.md")):
            for b in re.split(r"(?m)^## ", Path(f).read_text())[1:]:
                seen[b.split("\n")[0].strip()] = "## " + b
        pool = sorted(set(excluded) & set(seen))
        random.seed(args.seed)
        sample = random.sample(pool, min(args.audit, len(pool)))
        m = write_batches(args.out, [seen[d] for d in sample], n + 1)
        (args.out / "audit_sample.json").write_text(json.dumps(sample, indent=1))
        print(f"audit: {len(sample)} of {len(pool)} exclusions in cand_{n + 1}..cand_{n + m}; "
              f"screen these with the ordinary prompt, not the second-read one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
