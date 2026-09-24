#!/usr/bin/env python3
"""Fill in abstracts for sweep candidates that have none.

Screening a paper on its title alone is guesswork, and a screener told to guess
returns `unspecified`, which costs a model call and answers nothing. 5,519 of
23,328 candidates had no abstract, and 4,597 of those came from PubMed alone --
because the harvester reads PubMed through esummary, which does not carry
abstracts, and sets the field to None.

Europe PMC has them, keyed by the PMID we already hold, and its EXT_ID query
takes 40 ids at a time. That is ~115 requests to make 4,597 records screenable,
against ~115 model calls to have an agent shrug at them one batch at a time.

  uv run scripts/enrich_abstracts.py            # dry run
  uv run scripts/enrich_abstracts.py --apply
"""

from __future__ import annotations

import argparse
import re
import sys
import time

import httpx

from common import DATA, read_json, write_json

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
MAILTO = "brad@neuroharmonics.com"
BATCH = 40


def strip_tags(s: str | None) -> str | None:
    if not s:
        return None
    import html
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())[:1400] or None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, help="stop after this many batches")
    args = ap.parse_args()

    blob = read_json(DATA / "sweep-candidates.json", {}) or {}
    cands = blob.get("candidates", {})

    todo = [(d, str(v["pmid"])) for d, v in cands.items()
            if not v.get("abstract") and str(v.get("pmid") or "").isdigit()]
    no_pmid = sum(1 for v in cands.values()
                  if not v.get("abstract") and not str(v.get("pmid") or "").isdigit())

    print(f"candidates            {len(cands)}")
    print(f"missing an abstract   {sum(1 for v in cands.values() if not v.get('abstract'))}")
    print(f"  of which have a PMID we can look up  {len(todo)}")
    print(f"  of which have no PMID (cannot fix)   {no_pmid}")

    if not args.apply:
        print(f"\nDRY RUN. {(len(todo) + BATCH - 1) // BATCH} requests. Re-run with --apply.")
        return 0

    client = httpx.Client(timeout=60.0, follow_redirects=True,
                          headers={"User-Agent": f"tus-paper-archive/0.1 (mailto:{MAILTO})"})
    by_pmid = {p: d for d, p in todo}
    filled = failed = 0
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    if args.limit:
        batches = batches[: args.limit]

    for n, batch in enumerate(batches, 1):
        q = " OR ".join(f"EXT_ID:{p}" for _, p in batch)
        # A Europe PMC reply without hitCount is a failed request, not an empty result (seen
        # intermittently on batched queries). Never let the two look the same: retry once, then
        # count the batch as failed.
        rows = None
        for attempt in range(2):
            try:
                r = client.get(EPMC, params={
                    "query": f"({q})", "format": "json",
                    "pageSize": 100, "resultType": "core",
                })
                j = r.json() if r.status_code == 200 else {}
                if "hitCount" in j:
                    rows = (j.get("resultList") or {}).get("result") or []
                    break
                print(f"  batch {n}: HTTP {r.status_code}, no hitCount; {'retrying' if attempt == 0 else 'giving up'}", file=sys.stderr)
            except Exception as e:
                print(f"  batch {n}: {type(e).__name__}; {'retrying' if attempt == 0 else 'giving up'}", file=sys.stderr)
            time.sleep(1.5)
        if rows is None:
            failed += len(batch)
            continue
        for w in rows:
            doi = by_pmid.get(str(w.get("pmid") or ""))
            if not doi or doi not in cands:
                continue
            rec = cands[doi]
            if (abst := strip_tags(w.get("abstractText"))):
                rec["abstract"] = abst
                filled += 1
            # Fill the other gaps while we are here; esummary is thin.
            rec.setdefault("journal", (w.get("journalInfo") or {}).get("journal", {}).get("title"))
            if w.get("isOpenAccess") == "Y":
                rec["is_oa"] = True
        if n % 20 == 0:
            print(f"  {n}/{len(batches)} batches, {filled} abstracts filled")
        time.sleep(0.15)

    blob["candidates"] = cands
    write_json(DATA / "sweep-candidates.json", blob)
    still = sum(1 for v in cands.values() if not v.get("abstract"))
    print(f"\nfilled {filled} abstracts ({failed} lookups failed); "
          f"{still} candidates still have none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
