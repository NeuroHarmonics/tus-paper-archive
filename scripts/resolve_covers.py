#!/usr/bin/env python3
"""Replace Wiley cover-feature DOIs in the sweep with the article they picture.

Wiley mints a second record for the issue cover: same authors, same title (often
with a "<Cover topic>: " prefix), no page range, and a DOI carrying a 7 where the
article's carries a 0 --

    cover   10.1002/advs.2022 7 0218
    article 10.1002/advs.2022 0 2345

The PDF behind a cover DOI is the cover image, so asking anyone to fetch one is
asking for something that does not exist. The want-list handed out the same cover DOI
repeatedly, each time sending someone to look for a paper that was, in one case, already
downloaded.

Dropping them is not enough: 10.1002/advs.202270218 fronts a real in-scope TUS
paper, so a rule that only deleted covers would have silently lost it. Resolve
to the article instead, and let the screener judge that on its merits.

  uv run scripts/resolve_covers.py            # dry run
  uv run scripts/resolve_covers.py --apply
"""

from __future__ import annotations

import argparse
import re
import sys
import time

import httpx

from common import DATA, normalise_title, read_json, write_json

COVER_DOI = re.compile(r"^10\.1002/([a-z]+)\.(\d{4})7\d{4}$")
MAILTO = "brad@neuroharmonics.com"


# A cover record's title is the article's, wrapped: an optional "<Cover topic>: "
# in front and the issue citation "(Adv. Sci. 34/2022)" behind. Both must come
# off before the two can be compared.
ISSUE_TAG = re.compile(r"\s*\((?:[A-Z][A-Za-z.]*\s*){1,4}\d+\s*/\s*\d{4}\)\s*$")


def article_title(title: str) -> str:
    """The article's own title, with the cover's wrapping removed.

    "Hippocampal Slices: On-Chip Ultrasound Modulation of ..." -> the part after
    the colon. Covers that reuse the article title verbatim have no prefix, so
    only split when the tail still looks like a title rather than a fragment.
    """
    title = ISSUE_TAG.sub("", title or "")
    if ":" in title:
        head, tail = title.split(":", 1)
        if len(tail.strip()) > 25 and len(head.split()) <= 6:
            return tail.strip()
    return title


def find_article(client: httpx.Client, cover_doi: str, title: str) -> str | None:
    """The sibling record with the same title and a non-cover DOI."""
    journal = COVER_DOI.match(cover_doi).group(1)
    want = normalise_title(article_title(title))
    try:
        r = client.get("https://api.crossref.org/works",
                       params={"query.bibliographic": article_title(title),
                               "rows": 8, "mailto": MAILTO})
        items = r.json()["message"]["items"]
    except Exception:
        return None
    for it in items:
        doi = (it.get("DOI") or "").lower()
        if doi == cover_doi or COVER_DOI.match(doi):
            continue
        # Same journal, and the same paper -- not merely a similar one.
        if not doi.startswith(f"10.1002/{journal}."):
            continue
        # Prefix, not equality. The sweep's stored title may be truncated, and
        # the cover's carries the issue tag; the article's title is what both
        # start with. Require a substantial overlap so a short title cannot
        # match an unrelated longer one.
        got = normalise_title(article_title((it.get("title") or [""])[0]))
        if not got or not want:
            continue
        shorter, longer = sorted((got, want), key=len)
        if len(shorter) >= 30 and longer.startswith(shorter):
            return doi
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    blob = read_json(DATA / "sweep-candidates.json", {}) or {}
    cands = blob.get("candidates", {})
    covers = {d: v for d, v in cands.items() if COVER_DOI.match(d)}
    if not covers:
        print("no Wiley cover DOIs in the sweep")
        return 0

    client = httpx.Client(timeout=45.0, follow_redirects=True,
                          headers={"User-Agent": f"TUS-Archive/1.0 (mailto:{MAILTO})"})
    resolved, unresolved = {}, []
    for doi, rec in covers.items():
        real = find_article(client, doi, rec.get("title") or "")
        if real:
            resolved[doi] = real
            print(f"  {doi}\n    -> {real}   {(rec.get('title') or '')[:58]}")
        else:
            unresolved.append(doi)
            print(f"  {doi}\n    -> UNRESOLVED  {(rec.get('title') or '')[:58]}")
        time.sleep(0.3)

    print(f"\n{len(resolved)} resolved, {len(unresolved)} unresolved "
          f"(of {len(covers)} cover DOIs)")
    if unresolved:
        # Never silently drop: an unresolved cover means a paper we cannot see.
        print("unresolved covers are left in place for a human to look at")

    if not args.apply:
        print("\nDRY RUN. Re-run with --apply.")
        return 0

    for cover, real in resolved.items():
        rec = dict(cands.pop(cover))
        rec["title"] = article_title(rec.get("title") or "")
        rec["doi"] = real
        rec["note"] = f"resolved from Wiley cover-feature DOI {cover}"
        # Only add if the article is not already a candidate in its own right.
        cands.setdefault(real, rec)
    blob["candidates"] = cands
    write_json(DATA / "sweep-candidates.json", blob)
    print(f"\nrewrote {len(resolved)} cover DOIs to their articles in "
          f"data/sweep-candidates.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
