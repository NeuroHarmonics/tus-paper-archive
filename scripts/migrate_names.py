#!/usr/bin/env python3
"""Bring every PDF and its markdown onto the year-author-title convention.

rename.py renames papers it has a manifest record for, and organise_files.py
renames out-of-scope papers it has an ingest or ledger record for. Between them
they miss three groups, all of which a reader still has to look at:

  * key documents -- deliberately not manifest records, so rename.py skips them;
  * out-of-scope papers whose record was lost when an earlier pass renamed them;
  * library/_unidentified/, which neither script has ever touched.

For anything already named "Surname_YYYY_Title" the migration is a reorder, and
needs no lookup at all -- the three fields are right there. Only names that carry
no author need identity recovery, and that reuses the machinery that resolves
everything else: the DOI printed on page 1, then a Crossref title search held to
the same agreement threshold.

  uv run scripts/migrate_names.py            # dry run
  uv run scripts/migrate_names.py --apply
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import httpx

from common import (DATA, LIBRARY, MAILTO, TEXT_SUFFIX, compact_title,
                    paper_stem, read_json, text_path)
from identify import title_agreement
from organise_files import doi_from_pdf

DIRS = [LIBRARY, LIBRARY / "_out_of_scope", LIBRARY / "_unidentified"]

NEW = re.compile(r"^(\d{4}|nd)_[A-Za-z]")
# "2014_Unknown_Title" satisfies NEW but still names no author, so a run that
# failed to recover one and fell back to it would never look at that file again.
# Placeholder authors are always revisited.
PLACEHOLDER = re.compile(r"^(\d{4}|nd)_(Unknown|Anon)_(.+)$")
OLD = re.compile(r"^([A-Za-z][A-Za-z'\-]*)_(\d{4}|nd)_(.+)$")
NO_AUTHOR = re.compile(r"^(?:Unknown|Anon|)_(\d{4}|nd)_(.+)$")


def crossref_authors(client: httpx.Client, doi: str) -> tuple[str | None, str | None, int | None]:
    """(surname, title, year) for a DOI, or (None, None, None)."""
    try:
        m = client.get(f"https://api.crossref.org/works/{doi}",
                       params={"mailto": MAILTO}).json()["message"]
    except Exception:
        return None, None, None
    fam = next((a.get("family") or a.get("name")
                for a in (m.get("author") or []) if a.get("family") or a.get("name")), None)
    title = (m.get("title") or [None])[0]
    parts = (m.get("issued") or {}).get("date-parts") or [[None]]
    return fam, title, parts[0][0]


def recover(client: httpx.Client, pdf: Path, known_title: str | None) -> tuple[str, str, str] | None:
    """(year, surname, title) recovered from the PDF, or None."""
    if (doi := doi_from_pdf(pdf)):
        fam, title, year = crossref_authors(client, doi)
        if fam:
            return str(year or "nd"), fam, title or known_title or pdf.stem
    if known_title:
        # Same route identify.py uses when no DOI is printed.
        from resolve_missing import pick_best, search_by_title
        items = search_by_title(client, known_title, rows=5)
        # The query was rebuilt from a compact filename, so it has already lost
        # "of", "for" and "the". Scoring it against a full Crossref title can
        # never reach the 0.90 threshold: "Application Focused Ultrasound
        # Stimulation Neural Structures" against "Application of focused
        # ultrasound for the stimulation of neural structures" is the same paper
        # and scores well under it. Compact BOTH sides and compare like with
        # like -- which then matches exactly.
        want = compact_title(known_title).lower()
        scored = [(1.0 if compact_title((it.get("title") or [""])[0]).lower() == want
                   else title_agreement(known_title, (it.get("title") or [""])[0]), it)
                  for it in items]
        best = pick_best(scored, {"title": known_title})
        if best and best[1] >= 0.90:
            msg = best[0]
            fam = next((a.get("family") or a.get("name")
                        for a in (msg.get("author") or [])
                        if a.get("family") or a.get("name")), None)
            if fam:
                parts = (msg.get("issued") or {}).get("date-parts") or [[None]]
                return (str(parts[0][0] or "nd"), fam,
                        (msg.get("title") or [known_title])[0])
    return None


def spaced_title(compact: str) -> str:
    """"UseFocusedUltrasound" -> "Use Focused Ultrasound", for a title search."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", compact).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    client = httpx.Client(timeout=45.0, follow_redirects=True,
                          headers={"User-Agent": f"tus-paper-archive/0.1 (mailto:{MAILTO})"})
    plan, unresolved = [], []

    for d in DIRS:
        if not d.exists():
            continue
        for pdf in sorted(d.glob("*.pdf")):
            if NEW.match(pdf.stem) and not PLACEHOLDER.match(pdf.stem):
                continue
            stem = pdf.stem
            supp = stem.endswith("_supp")
            core = stem[:-5] if supp else stem

            if (m := PLACEHOLDER.match(core)):
                year, compact = m.group(1), m.group(3)
                got = recover(client, pdf, spaced_title(compact))
                if not got:
                    unresolved.append(pdf)
                    continue
                new = paper_stem(*got)
                if new != stem:
                    plan.append((pdf, d / f"{new}{'_supp' if supp else ''}.pdf"))
                continue

            if (m := NO_AUTHOR.match(core)) and not OLD.match(core.lstrip("_")):
                year, compact = m.group(1), m.group(2)
                got = recover(client, pdf, spaced_title(compact))
                if got:
                    new = paper_stem(got[0], got[1], got[2])
                else:
                    unresolved.append(pdf)
                    continue
            elif (m := OLD.match(core)):
                # Already the three fields, wrong order. Pure reorder, no lookup:
                # re-deriving the title from Crossref would silently rewrite names
                # that are already correct.
                sur, year, rest = m.group(1), m.group(2), m.group(3)
                if sur in ("Unknown", "Anon"):
                    got = recover(client, pdf, spaced_title(rest))
                    new = paper_stem(*got) if got else f"{year}_{sur}_{rest}"
                    if not got:
                        unresolved.append(pdf)
                else:
                    new = f"{year}_{sur}_{rest}"
            else:
                got = recover(client, pdf, None)
                if not got:
                    unresolved.append(pdf)
                    continue
                new = paper_stem(*got)

            new += "_supp" if supp else ""
            if new != stem:
                plan.append((pdf, d / f"{new}.pdf"))

    print(f"{len(plan)} file(s) to rename, {len(unresolved)} unresolved")
    for src, dst in plan[:8]:
        print(f"  {src.parent.name}/{src.stem[:52]}\n       -> {dst.stem[:52]}")
    for u in unresolved[:8]:
        print(f"  UNRESOLVED  {u.parent.name}/{u.name[:60]}")

    if not args.apply:
        print("\nDRY RUN. Re-run with --apply.")
        return 0

    n_pdf = n_txt = 0
    for src, dst in plan:
        if dst.exists():
            print(f"  SKIP (target exists) {dst.name}", file=sys.stderr)
            continue
        shutil.move(str(src), str(dst))
        n_pdf += 1
        # The markdown follows its PDF. One name, two extensions.
        t_src, t_dst = text_path(src.stem), text_path(dst.stem)
        if t_src.exists() and not t_dst.exists():
            shutil.move(str(t_src), str(t_dst))
            n_txt += 1
    print(f"\nrenamed {n_pdf} PDFs and {n_txt} markdown files")
    if unresolved:
        print(f"{len(unresolved)} left alone -- no author recoverable. Listed above; "
              f"never invent one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
