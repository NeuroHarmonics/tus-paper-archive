#!/usr/bin/env python3
"""Collapse preprint / version-of-record pairs into a single paper record.

A preprint and its published version are one paper. We want the preprint in the
archive as soon as it appears, then merged when the journal version lands --
keeping one entry, not two.

Detection, in order of authority:
  1. Crossref `relation.has-preprint` on the PUBLISHED record. This is the
     reliable direction: a bioRxiv DOI usually advertises no relation at all,
     while the journal record points back at it. 29 of our published records
     carry this.
  2. Crossref `relation.is-preprint-of` on the preprint, when present.
  3. Fallback: near-identical title + same first author + close year. Proposed
     for review, never merged automatically -- errata and conference abstracts
     also share a parent's title, and merging those would be wrong (C16, C19).

The version of record wins: it supplies the citekey, year and bibliographic
fields, and the preprint DOI is retained on the record.

  uv run scripts/link_preprints.py            # report
  uv run scripts/link_preprints.py --apply    # merge into data/manifest.json
"""

from __future__ import annotations

import argparse
import difflib
import sys

from common import DATA, LIBRARY, normalise_title, read_json, write_json

TITLE_SIM = 0.85   # fallback proposals only
YEAR_WINDOW = 3


def preprint_relations(cache: dict) -> dict[str, str]:
    """Map preprint DOI -> published DOI, from Crossref relation data."""
    link: dict[str, str] = {}
    for doi, msg in cache.items():
        if not isinstance(msg, dict):
            continue
        rel = msg.get("relation") or {}
        for item in rel.get("has-preprint", []) or []:
            if (pid := (item.get("id") or "").lower()).startswith("10."):
                link[pid] = doi.lower()          # published advertises its preprint
        for item in rel.get("is-preprint-of", []) or []:
            if (vid := (item.get("id") or "").lower()).startswith("10."):
                link[doi.lower()] = vid          # preprint advertises its VOR
    return link


def first_author(rec: dict) -> str:
    a = rec.get("authors") or []
    return (a[0].get("family") or "").lower() if a else ""


def author_list(rec: dict) -> tuple[str, ...]:
    return tuple((a.get("family") or "").lower() for a in (rec.get("authors") or []))


def is_preprint_record(rec: dict) -> bool:
    return "preprint" in (rec.get("record_flags") or []) or \
        str(rec.get("doi", "")).startswith(("10.1101/", "10.21203/", "10.48550/",
                                            "10.31234/", "10.2139/"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    man = read_json(DATA / "manifest.json")
    if not man:
        sys.exit("Run scripts/rename.py first.")
    papers = man["papers"]
    cache = read_json(DATA / "crossref-cache.json", {}) or {}

    by_doi = {p["doi"]: (ck, p) for ck, p in papers.items() if p.get("doi")}
    link = preprint_relations(cache)

    confirmed, proposed = [], []

    # 1-2. Authoritative: both halves of a Crossref-declared pair are held.
    for pre_doi, vor_doi in link.items():
        if pre_doi in by_doi and vor_doi in by_doi and pre_doi != vor_doi:
            confirmed.append((pre_doi, vor_doi, "crossref_relation"))

    # 3. Fallback proposals, for review only.
    seen = {p for p, _, _ in confirmed} | {v for _, v, _ in confirmed}
    keys = list(papers)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = papers[keys[i]], papers[keys[j]]
            da, db = a.get("doi"), b.get("doi")
            if not da or not db or da in seen or db in seen:
                continue
            # An erratum or conference abstract legitimately shares its parent's
            # title (C16, C19). Never propose merging those.
            if {a.get("record_class"), b.get("record_class")} & {
                    "erratum", "conference_abstract", "letter_commentary"}:
                continue
            if first_author(a) != first_author(b) or not first_author(a):
                continue
            ya, yb = a.get("year") or 0, b.get("year") or 0
            if abs(ya - yb) > YEAR_WINDOW:
                continue
            sim = difflib.SequenceMatcher(
                None, normalise_title(a.get("title")), normalise_title(b.get("title"))
            ).ratio()
            if sim < TITLE_SIM:
                continue
            # The earlier / preprint-flagged one is the preprint.
            pre, vor = (a, b) if ya <= yb else (b, a)
            if not is_preprint_record(pre) or is_preprint_record(vor):
                continue   # not a preprint -> journal progression

            # Crossref and OpenAlex frequently fail to link a preprint to its
            # published version (observed on Caulfield 2025/2026: no relation on
            # either record, separate OpenAlex works). So an identical author
            # list plus a near-identical title is treated as authoritative --
            # it is stronger evidence than the absent metadata link.
            if author_list(pre) and author_list(pre) == author_list(vor):
                confirmed.append((pre["doi"], vor["doi"],
                                  f"identical_authors+title_sim={sim:.2f}"))
            else:
                proposed.append((pre["doi"], vor["doi"], f"title_sim={sim:.2f}"))

    print(f"confirmed preprint pairs (Crossref relation): {len(confirmed)}")
    for p, v, _ in confirmed:
        print(f"  {p}\n    -> {v}  {(by_doi[v][1].get('title') or '')[:60]}")
    print(f"\nproposed pairs (need your confirmation):      {len(proposed)}")
    for p, v, why in proposed:
        pa, va = by_doi.get(p, (None, {}))[1], by_doi.get(v, (None, {}))[1]
        print(f"  [{why}]")
        print(f"    preprint? {pa.get('year')}  {p}  {(pa.get('title') or '')[:56]}")
        print(f"    VOR?      {va.get('year')}  {v}  {(va.get('title') or '')[:56]}")

    if not args.apply:
        print("\nReport only. Re-run with --apply to merge the confirmed pairs.")
        return 0

    merged = 0
    for pre_doi, vor_doi, how in confirmed:
        pre_ck, pre = by_doi[pre_doi]
        vor_ck, vor = by_doi[vor_doi]
        vor["preprint"] = {
            "doi": pre_doi, "year": pre.get("year"),
            "title": pre.get("title"), "linked_by": how,
            "pdf": pre.get("files", {}).get("pdf"),
        }
        # Keep the preprint PDF as an alternate rather than discarding it: the
        # preprint sometimes carries methods detail the published version cuts.
        vor.setdefault("files", {}).setdefault("alternates", []).append(
            pre.get("files", {}).get("pdf"))
        papers.pop(pre_ck, None)
        merged += 1
        print(f"  merged {pre_ck} into {vor_ck}")

    man["count"] = len(papers)
    write_json(DATA / "manifest.json", man)
    print(f"\nmerged {merged} preprint pair(s); manifest now {len(papers)} papers")
    print("Preprint PDFs kept in library/ and listed under files.alternates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
