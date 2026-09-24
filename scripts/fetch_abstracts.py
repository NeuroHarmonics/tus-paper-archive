#!/usr/bin/env python3
"""Recover missing abstracts for sweep candidates, so they can be screened.

INCLUSION.md forbids screening on a title alone, so a candidate with no abstract
is not screenable and would otherwise be dropped for a reason that has nothing to
do with its scope. Tries Crossref then Europe PMC.

Abstracts are held locally only -- never republished (see README "Publishing").

  uv run scripts/fetch_abstracts.py
"""

from __future__ import annotations

import html
import re
import sys
import time

import httpx

from common import DATA, MAILTO, read_json, write_json


def strip_tags(s: str | None) -> str | None:
    if not s:
        return None
    txt = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())
    return txt[:1400] or None


def main() -> int:
    path = DATA / "sweep-candidates.json"
    blob = read_json(path)
    if not blob:
        sys.exit("Run scripts/sweep.py first.")
    cands = blob["candidates"]
    todo = [d for d, v in cands.items() if not v.get("abstract")]
    print(f"{len(todo)} candidates without an abstract")

    client = httpx.Client(
        timeout=25.0, follow_redirects=True,
        headers={"User-Agent": f"tus-paper-archive/0.1 (mailto:{MAILTO})"},
    )
    got_cr = got_epmc = 0

    for i, doi in enumerate(todo, 1):
        abstract = None
        try:
            r = client.get(f"https://api.crossref.org/works/{doi}",
                           params={"mailto": MAILTO})
            if r.status_code == 200:
                abstract = strip_tags(r.json().get("message", {}).get("abstract"))
                if abstract:
                    got_cr += 1
            time.sleep(0.1)
        except Exception:
            pass

        if not abstract:
            try:
                r = client.get(
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    params={"query": f'DOI:"{doi}"', "format": "json",
                            "resultType": "core", "pageSize": 1},
                )
                if r.status_code == 200:
                    res = r.json().get("resultList", {}).get("result", [])
                    if res:
                        abstract = strip_tags(res[0].get("abstractText"))
                        if abstract:
                            got_epmc += 1
                time.sleep(0.1)
            except Exception:
                pass

        if abstract:
            cands[doi]["abstract"] = abstract
            cands[doi]["abstract_source"] = "crossref" if got_cr else "epmc"
        if i % 50 == 0:
            print(f"  {i}/{len(todo)}  crossref={got_cr} epmc={got_epmc}")

    write_json(path, blob)
    still = sum(1 for v in cands.values() if not v.get("abstract"))
    print(f"\nrecovered {got_cr + got_epmc} abstracts "
          f"(crossref {got_cr}, europepmc {got_epmc})")
    print(f"{still} candidates still have no abstract and remain unscreenable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
