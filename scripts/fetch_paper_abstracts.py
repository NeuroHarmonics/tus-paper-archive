#!/usr/bin/env python3
"""Fetch the abstract of every included paper into data/abstracts/<citekey>.json.

Abstracts are published on paper pages and in the search index (decision of 2026-09-18), with the
source recorded so a takedown is one edit. They are kept out of the bulk dataset download.

Order: Europe PMC by DOI, then the local Crossref cache, then PubMed by DOI. Papers with no
abstract anywhere (mostly pre-2000) get text: null so the miss is recorded and not retried
every run unless --retry-missing.

  uv run scripts/fetch_paper_abstracts.py            # only papers without a file yet
  uv run scripts/fetch_paper_abstracts.py --retry-missing
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET

import httpx

from common import DATA, read_json

OUT = DATA / "abstracts"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
MAILTO = "brad@neuroharmonics.com"


def clean(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"<jats:title>.*?</jats:title>", " ", s, flags=re.S)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = " ".join(s.split())
    s = re.sub(r"^(abstract|summary)[:.\s]+", "", s, flags=re.I)
    return s or None


def from_epmc(doi: str, client: httpx.Client) -> tuple[str | None, str | None]:
    r = client.get(EPMC, params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core", "pageSize": 3})
    r.raise_for_status()
    for w in (r.json().get("resultList") or {}).get("result") or []:
        if (w.get("doi") or "").lower() == doi.lower() and w.get("abstractText"):
            return clean(w["abstractText"]), f"PMID:{w.get('pmid')}" if w.get("pmid") else w.get("id")
    return None, None


def from_pubmed(doi: str, client: httpx.Client) -> tuple[str | None, str | None]:
    r = client.get(ESEARCH, params={"db": "pubmed", "term": f"{doi}[doi]", "retmode": "json", "email": MAILTO})
    r.raise_for_status()
    ids = r.json().get("esearchresult", {}).get("idlist") or []
    if not ids:
        return None, None
    r = client.get(EFETCH, params={"db": "pubmed", "id": ids[0], "retmode": "xml", "email": MAILTO})
    r.raise_for_status()
    root = ET.fromstring(r.text)
    parts = ["".join(a.itertext()) for a in root.iter("AbstractText")]
    return clean(" ".join(parts)), f"PMID:{ids[0]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--retry-missing", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    manifest = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})
    cache = read_json(DATA / "crossref-cache.json", {}) or {}
    cache_by_doi = {(v.get("DOI") or k).lower(): v for k, v in cache.items() if isinstance(v, dict)}

    todo = []
    for ck, e in sorted(manifest.items()):
        f = OUT / f"{ck}.json"
        if f.exists():
            if not args.retry_missing or json.loads(f.read_text()).get("text"):
                continue
        todo.append((ck, e["doi"]))
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} papers to fetch", file=sys.stderr)

    got = {"europepmc": 0, "crossref": 0, "pubmed": 0, "none": 0}
    with httpx.Client(timeout=40, follow_redirects=True,
                      headers={"User-Agent": f"tus-paper-archive/1.0 (mailto:{MAILTO})"}) as client:
        for n, (ck, doi) in enumerate(todo, 1):
            text = source = source_id = None
            for name, fn in (("europepmc", lambda: from_epmc(doi, client)),
                             ("crossref", lambda: (clean((cache_by_doi.get(doi.lower()) or {}).get("abstract")), doi)),
                             ("pubmed", lambda: from_pubmed(doi, client))):
                try:
                    text, source_id = fn()
                except (httpx.HTTPError, ET.ParseError, ValueError) as e:
                    print(f"  {ck} {name}: {type(e).__name__}", file=sys.stderr)
                    text = None
                if text and len(text) > 80:
                    source = name
                    break
                text = None
            got[source or "none"] += 1
            (OUT / f"{ck}.json").write_text(json.dumps({
                "citekey": ck, "doi": doi, "text": text, "source": source, "source_id": source_id,
                "fetched": dt.date.today().isoformat(),
            }, indent=1, ensure_ascii=False) + "\n")
            if n % 25 == 0:
                print(f"  {n}/{len(todo)}  {got}", file=sys.stderr)
            time.sleep(0.25)
    print(f"done: {got}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
