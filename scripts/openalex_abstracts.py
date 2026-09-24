#!/usr/bin/env python3
"""Recover missing abstracts from OpenAlex, 50 DOIs per request.

2,339 sweep candidates have no abstract and so cannot be screened -- the bar
forbids screening on a title alone, and rightly: Sorum 2024 is titled "Tension
activation of ..." and applies 3.5 MHz ultrasound. Europe PMC had abstracts for 4
of them. Crossref had them for 0 of 40 sampled. OpenAlex has them, but OpenAlex is
metered and a per-DOI loop would cost 2,339 requests -- weeks of daily budget.

The `filter=doi:a|b|c` form takes 50 DOIs in one request, which turns the whole
job into ~47. That is the difference between "not worth the budget" and "done
this afternoon".

Highest-value DOIs go first: a candidate whose TITLE already looks like TUS work
is far more likely to be worth screening than one that does not, so if the budget
dies part-way the papers most likely to matter are the ones already fetched.

Resumable and honest about stopping. A quota failure is detected explicitly and
reported as a quota failure -- it must never look like "no more abstracts", which
is the mistake that once hid citation chaining entirely.

  uv run scripts/openalex_abstracts.py             # dry run: what it would do
  uv run scripts/openalex_abstracts.py --apply
  uv run scripts/openalex_abstracts.py --apply --max-requests 20
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

from common import DATA, MAILTO, read_json, write_json

OPENALEX = "https://api.openalex.org/works"
BATCH = 50


def rebuild(inverted: dict | None) -> str | None:
    """OpenAlex stores abstracts as {token: [positions]}. Put them back in order."""
    if not inverted:
        return None
    pos: dict[int, str] = {}
    for token, places in inverted.items():
        for i in places:
            pos[i] = token
    return " ".join(pos[i] for i in sorted(pos))[:1400] or None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max-requests", type=int, default=200,
                    help="stop after this many requests (default 200)")
    args = ap.parse_args()

    blob = read_json(DATA / "sweep-candidates.json", {}) or {}
    cands = blob.get("candidates", {})

    # Rank by whether the title alone already looks like TUS work.
    from build_batches import TUS_LIKE
    todo = [d for d, v in cands.items() if not v.get("abstract")]
    todo.sort(key=lambda d: (0 if TUS_LIKE.search(cands[d].get("title") or "") else 1, d))
    tus_like = sum(1 for d in todo if TUS_LIKE.search(cands[d].get("title") or ""))

    print(f"candidates with no abstract   {len(todo)}")
    print(f"  of those, TUS-like by title {tus_like}   (fetched first)")
    print(f"  requests needed             {(len(todo) + BATCH - 1) // BATCH} "
          f"at {BATCH} DOIs each")

    if not args.apply:
        print("\nDRY RUN. Re-run with --apply.")
        return 0

    client = httpx.Client(timeout=60.0, follow_redirects=True,
                          headers={"User-Agent": f"tus-paper-archive/0.1 (mailto:{MAILTO})"})
    filled = missing = 0
    exhausted = False
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]

    for n, batch in enumerate(batches[: args.max_requests], 1):
        try:
            r = client.get(OPENALEX, params={
                "filter": "doi:" + "|".join(batch),
                "select": "doi,abstract_inverted_index",
                "per-page": BATCH, "mailto": MAILTO,
            })
            if r.status_code == 429:
                exhausted = True
                print(f"\nOpenAlex budget exhausted after {n - 1} requests "
                      f"({r.text[:120]})")
                break
            r.raise_for_status()
            results = r.json().get("results", [])
        except Exception as e:
            print(f"  batch {n}: {type(e).__name__}", file=sys.stderr)
            continue

        for w in results:
            doi = (w.get("doi") or "").lower().replace("https://doi.org/", "")
            if doi not in cands:
                continue
            if (abst := rebuild(w.get("abstract_inverted_index"))):
                cands[doi]["abstract"] = abst
                cands[doi]["abstract_source"] = "openalex"
                filled += 1
            else:
                missing += 1
        if n % 10 == 0:
            print(f"  {n}/{len(batches)} requests, {filled} abstracts recovered")
        time.sleep(0.2)

    blob["candidates"] = cands
    write_json(DATA / "sweep-candidates.json", blob)

    still = sum(1 for v in cands.values() if not v.get("abstract"))
    print(f"\nrecovered {filled} abstracts; OpenAlex has none for {missing} of those asked")
    print(f"{still} candidates still have no abstract")
    if exhausted:
        print("Budget resets at midnight UTC. Re-run then -- it picks up where it "
              "stopped, because it only asks about candidates that still have none.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
