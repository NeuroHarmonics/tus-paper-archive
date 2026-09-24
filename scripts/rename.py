#!/usr/bin/env python3
"""Stage 3: apply screening verdicts, deduplicate, assign citekeys, rename.

Turns the flat candidates/ pile into library/ with one file per paper under a
stable name, and records what was excluded and why.

Defaults to a dry run. Nothing moves without --apply.

  uv run scripts/rename.py          # preview
  uv run scripts/rename.py --apply  # do it
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from common import (
    load_screening,  # noqa: F401
    normalise_title,
    CANDIDATES, DATA, LIBRARY, citekey_base, paper_stem, read_json, surname_slug,
    write_json,
)

DUPES = LIBRARY / "_duplicates"
OUT_OF_SCOPE = LIBRARY / "_out_of_scope"
UNIDENTIFIED = LIBRARY / "_unidentified"

# A filename that tells you nothing: no author-year structure, just a nickname.
STUB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\.pdf$")


def first_author(rec: dict) -> str:
    a = rec.get("authors") or []
    if a and a[0].get("family"):
        return surname_slug(a[0]["family"]) or "anon"
    m = re.match(r"^(?:\d{4} - )?([A-Za-z][A-Za-z'\-]+)", rec.get("file", ""))
    return surname_slug(m.group(1)) if m else "anon"


def file_quality(name: str) -> tuple[int, int]:
    """Rank candidate files for the same paper. Higher is better.

    A descriptive filename is preferred over a nickname stub, because when a
    human opens library/ the name should identify the paper. Length breaks ties
    only as a proxy for descriptiveness.
    """
    return (0 if STUB_RE.match(name.lower()) else 1, len(name))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    ingest = read_json(DATA / "ingest.json")
    if not ingest:
        sys.exit("Run identify.py first.")
    records = ingest["records"]
    verdicts = load_screening()
    if not verdicts:
        sys.exit("No screening ledger at data/screening.jsonl")

    # Where an earlier run filed each paper, so a paper excluded on a LATER pass
    # can still be found and parked.
    prev_filed = {
        p["doi"]: (p.get("files") or {}).get("pdf")
        for p in (read_json(DATA / "manifest.json", {}) or {}).get("papers", {}).values()
        if p.get("doi")
    }

    # Key documents are NOT database records. They are a hand-curated reading
    # list (reviews, consensus statements, reporting standards) with no
    # parameters, kept out of every aggregate. Their PDFs stay in library/ so
    # they remain readable and greppable, but they get no manifest entry.
    key_docs = set((read_json(DATA / "key-documents.json", {}) or {}).get("documents", {}))

    resolved = {k: v for k, v in records.items()
                if v["status"] == "resolved" and v.get("doi") not in key_docs}
    # Papers we could not pin to a Crossref record. rename.py used to ignore
    # these entirely, so they sat in candidates/ forever and the folder never
    # drained -- which is exactly how you lose track of what has been processed.
    unidentified = {k: v for k, v in records.items()
                    if v["status"] == "resolved_no_doi"}
    supps = {k: v for k, v in records.items() if v["status"] == "supplementary"}

    if key_docs:
        print(f"{len(key_docs)} key documents held as a reading list, not records")

    missing = {r["doi"] for r in resolved.values()} - set(verdicts)
    if missing:
        print(f"ERROR: {len(missing)} resolved papers have no screening verdict:",
              file=sys.stderr)
        for d in sorted(missing)[:10]:
            print(f"    {d}", file=sys.stderr)
        sys.exit("Screen every paper before renaming; refusing to proceed.")

    # --- group files by paper (DOI) -------------------------------------------
    by_doi: dict[str, list[str]] = defaultdict(list)
    for name, rec in resolved.items():
        by_doi[rec["doi"]].append(name)

    # What the previous manifest knew that identify.py cannot: preprint pairs merged by
    # link_preprints.py (the preprint PDF stays in library/ as an alternate of the version of
    # record, and must not come back as a paper of its own), and the alternates list itself.
    prior = {p["doi"].lower(): p for p in (read_json(DATA / "manifest.json", {}) or {}).get("papers", {}).values() if p.get("doi")}
    merged_preprints = {p["preprint"]["doi"].lower(): d for d, p in prior.items() if p.get("preprint", {}).get("doi")}

    included, excluded, unresolved_verdicts = {}, [], []
    for doi, files in by_doi.items():
        if doi.lower() in merged_preprints:
            continue   # an alternate of a held paper, not a paper
        v = verdicts[doi]
        rec = records[files[0]]
        if v["verdict"] == "relevant":
            included[doi] = (rec, sorted(files, key=file_quality, reverse=True), v)
        elif v["verdict"] == "unspecified":
            unresolved_verdicts.append((doi, rec, v))
        else:
            excluded.append({
                "doi": doi, "title": rec.get("title"), "year": rec.get("year"),
                "journal": rec.get("journal"), "files": sorted(files),
                "verdict": v["verdict"], "reason": v.get("reason"),
                "criterion": v.get("criterion"), "screened_by": "paper-screener/haiku",
                "pdfs_parked_in": "library/_out_of_scope/",
            })

    # --- same-paper-different-DOI check ---------------------------------------
    # DOI dedup misses a paper indexed twice under different DOIs (a preprint and
    # its published version, a paper and a conference abstract of it, an erratum).
    # Only some of those should merge -- C16 errata and C19 conference abstracts
    # are legitimately separate records -- so warn rather than merge, and let
    # link_preprints.py handle the one case that is safe to collapse.
    seen_titles: dict[str, str] = {}
    collisions = []
    for doi, (rec, _, v) in included.items():
        key = f"{normalise_title(rec.get('title'))}|{first_author(rec)}"
        if not key.strip("|"):
            continue
        if key in seen_titles and seen_titles[key] != doi:
            collisions.append((seen_titles[key], doi, rec.get("title")))
        else:
            seen_titles[key] = doi
    if collisions:
        print(f"\n--- {len(collisions)} same-title/same-author pair(s), NOT auto-merged ---")
        for a, b, t in collisions:
            print(f"  {a}\n  {b}\n     {(t or '')[:66]}")
        print("  Run scripts/link_preprints.py to collapse any that are preprint pairs.")

    # --- citekeys -------------------------------------------------------------
    # Stable and collision-free: year+surname, with a suffix letter assigned in a
    # deterministic order (by DOI) so re-runs produce identical keys. Year first
    # to match the filename convention, so a citekey and a filename sort the same
    # way. 36 year+surname pairs in this corpus hold more than one paper, so the
    # suffix is load-bearing, not a formality.
    groups: dict[str, list[str]] = defaultdict(list)
    for doi, (rec, _, _) in included.items():
        groups[citekey_base(rec.get("year"), first_author(rec))].append(doi)

    # A citekey already in the manifest is never changed: it names a record in data/papers, an
    # abstract file and a site URL. On 2026-09-21 a newcomer sharing year+surname re-lettered ten
    # existing papers (2021cain -> 2021cainb, and 2021wangb was handed to a different paper), which
    # orphaned their records. Now existing keys are kept and a newcomer takes the first unused letter.
    existing = {(p.get("doi") or "").lower(): ck
                for ck, p in (read_json(DATA / "manifest.json", {}) or {}).get("papers", {}).items() if p.get("doi")}
    citekey: dict[str, str] = {}
    for base, dois in groups.items():
        taken = set()
        for doi in dois:
            if doi.lower() in existing and existing[doi.lower()].startswith(base):
                citekey[doi] = existing[doi.lower()]
                taken.add(citekey[doi])
        newcomers = sorted(d for d in dois if d not in citekey)
        if len(dois) == 1 and newcomers:
            citekey[newcomers[0]] = base
            continue
        for doi in newcomers:
            for i in range(26):
                cand = f"{base}{chr(ord('a') + i)}"
                if cand not in taken and (cand != base):
                    citekey[doi] = cand
                    taken.add(cand)
                    break

    # --- supplementary attachment --------------------------------------------
    # Match "2024 - Bancel - Supplementary.pdf" to its parent by the
    # "YYYY - Surname" prefix the corpus already uses.
    parent_of: dict[str, str] = {}
    orphan_supps = []
    for sname in supps:
        # Three naming schemes reach here, because a supplement can have been
        # filed under any convention this archive has used: the corpus's original
        # "2024 - Bancel - Supplementary.pdf", the old library convention
        # "Bancel_2024_Title_supp.pdf", and the current "2024_Bancel_Title_supp.pdf".
        # Only the first was understood once, and four supplements whose parents
        # sat in the same directory were reported as orphans on every run.
        yr = sur = None
        for pat, order in ((r"^(\d{4}) - ([A-Za-z'\-]+)", "ya"),
                           (r"^(\d{4})_([A-Za-z'\-]+)_", "ya"),
                           (r"^([A-Za-z'\-]+)_(\d{4})_", "ay")):
            if (m := re.match(pat, sname)):
                a, b = m.group(1), m.group(2)
                yr, sur = (int(a), b) if order == "ya" else (int(b), a)
                break
        hit = None
        if yr is not None:
            sur = surname_slug(sur)
            for doi, (rec, _, _) in included.items():
                if first_author(rec) == sur and rec.get("year") in (yr, yr - 1, yr + 1):
                    hit = doi
                    break
        if hit:
            parent_of[sname] = hit
        else:
            orphan_supps.append(sname)

    # --- plan the moves -------------------------------------------------------
    plan, seen_names, stem_for = [], {}, {}
    for doi, (rec, files, v) in sorted(included.items(), key=lambda kv: citekey[kv[0]]):
        ck = citekey[doi]
        author = (rec.get("authors") or [{}])[0].get("family") or first_author(rec)
        stem = paper_stem(rec.get("year"), author, rec.get("title"))
        if stem in seen_names:                       # extremely unlikely; stay safe
            stem = f"{stem}_{ck[-1]}"
        seen_names[stem] = doi
        stem_for[doi] = stem

        plan.append({"kind": "paper", "citekey": ck, "doi": doi,
                     "src": files[0], "dst": f"{stem}.pdf",
                     "record_class": v.get("record_class")})
        for dup in files[1:]:
            plan.append({"kind": "duplicate", "citekey": ck, "doi": doi,
                         "src": dup, "dst": f"_duplicates/{dup}"})
        for sname, pdoi in parent_of.items():
            if pdoi == doi:
                plan.append({"kind": "supplement", "citekey": ck, "doi": doi,
                             "src": sname, "dst": f"{stem}_supp.pdf"})

    # --- report ---------------------------------------------------------------
    print(f"papers included      {len(included)}")
    print(f"  files kept         {sum(1 for p in plan if p['kind'] == 'paper')}")
    print(f"  duplicates parked  {sum(1 for p in plan if p['kind'] == 'duplicate')}")
    print(f"  supplements filed  {sum(1 for p in plan if p['kind'] == 'supplement')}")
    print(f"papers excluded      {len(excluded)}")
    print(f"needing your call    {len(unresolved_verdicts)}")
    if orphan_supps:
        print(f"orphan supplements   {len(orphan_supps)}: {orphan_supps}")

    if unresolved_verdicts:
        print("\n--- unspecified: resolve these in INCLUSION.md as new C-cases ---")
        for doi, rec, v in unresolved_verdicts:
            print(f"  {doi}\n     {(rec.get('title') or '')[:74]}\n     {v.get('reason')}")

    if excluded:
        print("\n--- excluded ---")
        for e in sorted(excluded, key=lambda x: x["criterion"] or ""):
            print(f"  [{e['criterion']}] {(e['title'] or '')[:66]}\n        {e['reason']}")

    if not args.apply:
        print("\nDRY RUN. Re-run with --apply to move files.")
        return 0

    # --- execute --------------------------------------------------------------
    for d in (LIBRARY, DUPES, OUT_OF_SCOPE, UNIDENTIFIED):
        d.mkdir(parents=True, exist_ok=True)

    moved = renamed = 0
    for p in plan:
        src, dst = CANDIDATES / p["src"], LIBRARY / p["dst"]
        if not src.exists():
            if dst.exists():
                continue          # already applied; idempotent
            # Filed by an earlier run, under a name this run no longer computes.
            # Until the naming convention changed this could not happen, so the
            # loop only ever looked in candidates/ and reported MISSING -- which
            # meant a convention change renamed the papers being filed today and
            # silently left 371 already-filed ones on the old scheme.
            # Where is it now? ingest.json holds the name the file had when it
            # was first extracted, which goes stale the moment anything renames
            # it; the previous manifest holds where the last run actually put it.
            # Try both, newest knowledge first, or a second convention change
            # silently no-ops.
            #
            # ONLY for kind == "paper". prev_filed maps a DOI to the PRIMARY file
            # the last run kept, so consulting it for a "duplicate" entry -- whose
            # destination is _duplicates/ -- finds the primary and files the paper
            # itself as its own duplicate. organise_files.py then deletes it as
            # redundant. That destroyed three PDFs before this guard existed.
            candidates_ = [LIBRARY / p["src"]]
            if p.get("kind") == "paper" and prev_filed.get(p["doi"]):
                candidates_.insert(0, LIBRARY / prev_filed[p["doi"]])
            in_place = next((c for c in candidates_ if c.name and c.exists()),
                            LIBRARY / p["src"])
            if in_place.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(in_place), str(dst))
                renamed += 1
                continue
            print(f"  MISSING {p['src']}", file=sys.stderr)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved += 1

    # Excluded PDFs are parked, never deleted -- a later widening of the bar
    # restores them without re-acquiring anything through institutional access.
    parked = 0
    for e in excluded:
        for f in e["files"]:
            # A paper can be excluded on a LATER pass than the one that filed it
            # -- e.g. the scope audit catching MRgFUS papers already in library/.
            # Look in both places, or those PDFs stay in the library for good.
            for base in (CANDIDATES, LIBRARY):
                src = base / f
                if src.exists():
                    shutil.move(str(src), str(OUT_OF_SCOPE / f))
                    parked += 1
                    break
            else:
                # Filed by an earlier run under its renamed stem, so neither the
                # original filename nor this run's stem finds it. The previous
                # manifest is the only record of where it went.
                prev_name = prev_filed.get(e["doi"])
                if prev_name and (LIBRARY / prev_name).exists():
                    shutil.move(str(LIBRARY / prev_name),
                                str(OUT_OF_SCOPE / prev_name))
                    parked += 1

    # Supplementary files whose parent was excluded follow their parent.
    for sname, pdoi in parent_of.items():
        if pdoi not in included and (CANDIDATES / sname).exists():
            shutil.move(str(CANDIDATES / sname), str(OUT_OF_SCOPE / sname))
            parked += 1

    # The exclusion ledger is APPEND-ONLY. Once a paper's PDF moves to
    # library/_out_of_scope/ it leaves candidates/ and library/, so it drops out
    # of ingest.json on the next run -- overwriting the ledger from this run's
    # verdicts alone would silently erase every prior exclusion, and the monthly
    # sweep would then re-add all of them.
    prior = (read_json(DATA / "excluded.json", {}) or {}).get("papers", [])
    by_doi_excl = {p["doi"]: p for p in prior}
    by_doi_excl.update({p["doi"]: p for p in excluded})   # this run wins on conflict
    write_json(DATA / "excluded.json", {
        "note": "Screened out under INCLUSION.md. Append-only: retained so later "
                "sweeps cannot re-add them; not part of the published database.",
        "count": len(by_doi_excl),
        "papers": sorted(by_doi_excl.values(), key=lambda x: x["doi"]),
    })
    print(f"exclusion ledger: {len(prior)} prior + {len(excluded)} this run "
          f"= {len(by_doi_excl)} total")
    write_json(DATA / "manifest.json", {
        "note": "One entry per included paper: citekey, identity, scope class, files.",
        "count": len(included),
        "papers": {
            citekey[doi]: {
                "citekey": citekey[doi], "doi": doi,
                "title": rec.get("title"), "year": rec.get("year"),
                "journal": rec.get("journal"), "authors": rec.get("authors"),
                "volume": rec.get("volume"), "pages": rec.get("pages"),
                "url": rec.get("url"), "publisher": rec.get("publisher"),
                "record_flags": rec.get("flags", []),
                "record_class": v.get("record_class"),
                "screening": {"verdict": v["verdict"], "criterion": v.get("criterion"),
                              "reason": v.get("reason"),
                              "screened_by": "paper-screener/haiku",
                              "bar_version": "INCLUSION.md 1.0"},
                "erratum_of": [u["doi"] for u in rec.get("update_to", [])] or None,
                **({"preprint": prior[doi.lower()]["preprint"]} if prior.get(doi.lower(), {}).get("preprint") else {}),
                "files": {
                    **({"alternates": prior[doi.lower()]["files"]["alternates"]}
                       if prior.get(doi.lower(), {}).get("files", {}).get("alternates") else {}),
                    "pdf": next(p["dst"] for p in plan
                                if p["kind"] == "paper" and p["doi"] == doi),
                    # Re-derived from what is on disk, not just from this run's
                    # plan: a supplement filed by an earlier run is already in
                    # library/ and would otherwise silently lose its parent link.
                    "supplements": sorted(
                        {p["dst"] for p in plan
                         if p["kind"] == "supplement" and p["doi"] == doi}
                        | {q.name for q in LIBRARY.glob(f"{stem_for[doi]}_supp.*")}),
                    "duplicates": [p["dst"] for p in plan
                                   if p["kind"] == "duplicate" and p["doi"] == doi],
                },
            }
            for doi, (rec, _, v) in included.items()
        },
    })
    # Park the unidentifiable so candidates/ drains. Nothing is deleted; a
    # later pass with a better identifier can pick them back up.
    n_unid = 0
    for name in unidentified:
        src = CANDIDATES / name
        if src.exists():
            shutil.move(str(src), str(UNIDENTIFIED / name))
            n_unid += 1
    if n_unid:
        write_json(DATA / "unidentified.json", {
            "note": "Papers whose bibliographic identity could not be resolved. "
                    "PDFs in library/_unidentified/. Not in the database until "
                    "identified; re-run identify.py + the paper-identifier agent.",
            "count": len(unidentified),
            "papers": {k: {"title": v.get("title") or v.get("agent_title"),
                           "year": v.get("year"),
                           "note": v.get("agent_notes") or v.get("note")}
                       for k, v in sorted(unidentified.items())},
        })

    print(f"\nmoved {moved} files -> library/"
          + (f"; renamed {renamed} already-filed papers to the current convention"
             if renamed else ""))
    if n_unid:
        print(f"parked {n_unid} unidentifiable -> library/_unidentified/ "
              f"(see data/unidentified.json)")
    print(f"parked {parked} excluded files -> library/_out_of_scope/")
    print(f"wrote data/manifest.json ({len(included)} papers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
