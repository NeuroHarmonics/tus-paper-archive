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
  uv run scripts/link_preprints.py --apply --confirm <preprint DOI> ...   # merge proposed pairs too
  uv run scripts/link_preprints.py --find-published
  uv run scripts/link_preprints.py --similar

--similar finds the pairs no metadata links: a held preprint and a held paper that
share most authors and much of their title and abstract. A journal often retitles
the paper, so neither Crossref, bioRxiv nor a title match connects the two; five
such pairs were published in v2026.09.0. Two papers by one group can look alike,
so each pair is read before it is merged with --confirm PREPRINT=PUBLISHED.

Merging needs both halves held. --find-published looks for the half we do not
hold: for each preprint record it asks Crossref afresh (the cache is as old as
the record) for `is-preprint-of`, then searches journal articles by title and
first author. A version of record found this way is to be obtained and filed
into candidates/; once identified and renamed, the run above merges it. Run it
at every update: the preprint is filed once and nothing else looks again.
"""

from __future__ import annotations

import argparse
import difflib
import sys
import time

import httpx

from common import DATA, LIBRARY, MAILTO, normalise_title, read_json, write_json

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


def find_published(papers: dict) -> int:
    """Report a version of record for each preprint record that has one."""
    held = {(p.get("doi") or "").lower() for p in papers.values()}
    found = 0
    with httpx.Client(timeout=40, headers={"User-Agent": f"tus-paper-archive/1.0 (mailto:{MAILTO})"}) as c:
        for ck, p in sorted(papers.items()):
            if not is_preprint_record(p):
                continue
            hits: dict[str, str] = {}
            # An HTTP error is reported, never read as "not published".
            try:
                r = c.get(f"https://api.crossref.org/works/{p['doi']}")
                if r.status_code != 404:
                    r.raise_for_status()
                    rel = r.json()["message"].get("relation") or {}
                    for item in rel.get("is-preprint-of", []) or []:
                        if (vid := (item.get("id") or "").lower()).startswith("10."):
                            hits[vid] = "crossref_relation"
                r = c.get("https://api.crossref.org/works", params={
                    "rows": 5, "filter": "type:journal-article",
                    "query.bibliographic": p.get("title") or "", "query.author": first_author(p)})
                r.raise_for_status()
                for w in r.json()["message"].get("items", []):
                    sim = difflib.SequenceMatcher(None, normalise_title(p.get("title")),
                                                  normalise_title((w.get("title") or [""])[0])).ratio()
                    fams = [(a.get("family") or "").lower() for a in w.get("author", [])]
                    if sim >= TITLE_SIM and first_author(p) and first_author(p) in fams:
                        hits.setdefault(w["DOI"].lower(), f"title_sim={sim:.2f}+first_author")
            except (httpx.HTTPError, ValueError, KeyError) as e:
                print(f"  {ck}: lookup failed ({type(e).__name__}); not checked")
                continue
            for vor, why in hits.items():
                found += 1
                print(f"  {ck:16} {p['doi']}\n    -> {vor}  [{why}]{'  (held)' if vor in held else ''}")
            time.sleep(0.3)
    print(f"\n{found} published versions found")
    return 0


def similar_pairs(papers: dict) -> int:
    """Held preprints that resemble another held paper by authors and text."""
    from acquire import _cosine, _surname, _words

    def text(ck, p):
        ab = read_json(DATA / "abstracts" / f"{ck}.json", {}) or {}
        return _words(f"{p.get('title') or ''} {ab.get('text') or ''}")
    info = {ck: ({_surname(a.get("family")) for a in (p.get("authors") or [])}, text(ck, p))
            for ck, p in papers.items()}
    n = 0
    for ck, p in sorted(papers.items()):
        if not is_preprint_record(p):
            continue
        a, w = info[ck]
        for other, (b, v) in info.items():
            if other == ck or not a or not b:
                continue
            ov = len(a & b) / min(len(a), len(b))
            sim = _cosine(w, v)
            if ov >= 0.6 and sim >= 0.45:
                n += 1
                print(f"  {ck:20} ~ {other:20} authors {ov:.2f}  text {sim:.2f}\n"
                      f"      {(p.get('title') or '')[:70]}\n      {(papers[other].get('title') or '')[:70]}")
    print(f"\n{n} pairs to read")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm", nargs="+", default=[], metavar="PREPRINT_DOI[=PUBLISHED_DOI]",
                    help="treat these proposed pairs as confirmed, after a person has looked; "
                         "PREPRINT=PUBLISHED names a pair the title test never proposes, as when "
                         "the journal retitled the paper")
    ap.add_argument("--similar", action="store_true",
                    help="report held preprints that resemble another held paper")
    ap.add_argument("--find-published", action="store_true",
                    help="look up a version of record for every preprint record")
    args = ap.parse_args()

    man = read_json(DATA / "manifest.json")
    if not man:
        sys.exit("Run scripts/rename.py first.")
    papers = man["papers"]
    if args.find_published:
        return find_published(papers)
    if args.similar:
        return similar_pairs(papers)
    cache = read_json(DATA / "crossref-cache.json", {}) or {}

    by_doi = {p["doi"]: (ck, p) for ck, p in papers.items() if p.get("doi")}
    link = preprint_relations(cache)

    confirmed, proposed = [], []

    # 1-2. Authoritative: both halves of a Crossref-declared pair are held.
    for pre_doi, vor_doi in link.items():
        if pre_doi in by_doi and vor_doi in by_doi and pre_doi != vor_doi:
            confirmed.append((pre_doi, vor_doi, "crossref_relation"))

    # A pair a person has named outright, both halves held.
    for item in args.confirm:
        if "=" in item:
            pre_, vor_ = (x.strip().lower() for x in item.split("=", 1))
            if pre_ in by_doi and vor_ in by_doi and (pre_, vor_) not in {(p, v) for p, v, _ in confirmed}:
                confirmed.append((pre_, vor_, "confirmed_by_hand"))

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
                if pre["doi"].lower() in {d.lower() for d in args.confirm}:
                    confirmed.append((pre["doi"], vor["doi"], f"confirmed_by_hand+title_sim={sim:.2f}"))
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
        link_ = {
            "doi": pre_doi, "year": pre.get("year"),
            "title": pre.get("title"), "linked_by": how,
            "pdf": pre.get("files", {}).get("pdf"),
        }
        # A paper can have more than one preprint (bioRxiv and Research Square, say).
        # Overwriting the first link would leave its DOI looking unheld, and the next
        # rename.py would file that preprint again as a paper of its own.
        if vor.get("preprint") and vor["preprint"].get("doi") != pre_doi:
            vor.setdefault("other_preprints", []).append(link_)
        else:
            vor["preprint"] = link_
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
