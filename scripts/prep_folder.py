#!/usr/bin/env python3
"""Name and extract a folder of PDFs outside the archive.

For a loose folder of papers in some other project: each PDF is renamed to the
archive's year-first convention and converted to Markdown in DIR/text/. Nothing
in the archive is read or written beyond the code it reuses, and no paper is
screened, given a citekey or filed in library/.

  uv run scripts/prep_folder.py DIR                 # preview the renames
  uv run scripts/prep_folder.py DIR --apply         # rename, then extract
  uv run scripts/prep_folder.py DIR --apply --no-extract
  uv run scripts/prep_folder.py DIR --apply --force # re-extract existing text

Identification is the same as identify.py: DOIs from the first pages in order,
resolved through Crossref, year from the print date. A DOI is only accepted
when the Crossref title appears in the PDF's own text, so a reference-list DOI
cannot rename a paper after somebody else's. A PDF with no corroborated DOI,
typically one from before 2003, keeps its name and is still extracted.

The Crossref cache is read but never saved: these papers are not the archive's.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from common import normalise_title, paper_stem
from identify import SUPP_PAT, Crossref, find_dois, record_from_crossref, year_from_doi

# Enough of the title to be distinctive, short enough to survive a title that
# the PDF breaks across a column or sets with a subtitle Crossref omits.
TITLE_PROBE = 40


def first_pages(pdf: Path, n: int = 3) -> list[str]:
    """pdftotext of the first n pages, split on form feeds.

    pdftotext rather than MinerU so that a preview costs seconds, not a model
    load, and so the name is settled before the Markdown is written under it.
    """
    try:
        res = subprocess.run(["pdftotext", "-l", str(n), str(pdf), "-"],
                             capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        sys.exit("pdftotext not found (brew install poppler)")
    except subprocess.TimeoutExpired:
        return []
    return res.stdout.split("\f")[:n]


def identify(pdf: Path, cr: Crossref) -> dict | None:
    """The Crossref record whose title occurs in the PDF, or None."""
    pages = first_pages(pdf)
    text = normalise_title(" ".join(pages))
    for doi in find_dois(pages):
        msg = cr.get(doi)
        if not msg:
            continue
        rec = record_from_crossref(msg)
        probe = normalise_title(rec["title"])[:TITLE_PROBE]
        if probe and probe in text:
            return rec
    return None


def target_name(pdf: Path, rec: dict) -> str:
    surname = rec["authors"][0]["family"] if rec["authors"] else None
    stem = paper_stem(rec["year"] or year_from_doi(rec["doi"]), surname, rec["title"])
    if SUPP_PAT.search(pdf.stem):
        stem += "_supp"
    return stem + ".pdf"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dir", type=Path)
    ap.add_argument("--apply", action="store_true", help="rename and extract (default: preview)")
    ap.add_argument("--no-extract", action="store_true", help="rename only")
    ap.add_argument("--force", action="store_true", help="re-extract text that already exists")
    args = ap.parse_args()

    d = args.dir.expanduser().resolve()
    pdfs = sorted(d.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"no PDFs in {d}")

    cr = Crossref()
    final: list[Path] = []
    taken = {p.name for p in pdfs}
    for pdf in pdfs:
        rec = identify(pdf, cr)
        if not rec:
            print(f"keep    {pdf.name}  (no DOI corroborated by the text)")
            final.append(pdf)
            continue
        new = d / target_name(pdf, rec)
        if new == pdf:
            print(f"ok      {pdf.name}")
        elif new.name in taken:
            print(f"CLASH   {pdf.name}\n     -> {new.name} already exists; left alone")
            new = pdf
        else:
            print(f"rename  {pdf.name}\n     -> {new.name}  [{rec['doi']}]")
            taken.discard(pdf.name)
            taken.add(new.name)
            if args.apply:
                pdf.rename(new)
        final.append(new if args.apply else pdf)

    if not args.apply:
        print("\npreview only; --apply to rename and extract")
        return 0
    if args.no_extract:
        return 0

    out = d / "text"
    todo = [p for p in final if args.force or not (out / f"{p.stem}.md").exists()]
    if not todo:
        print(f"\ntext already in {out}")
        return 0

    from pdf_markdown import MinerU, assess

    out.mkdir(exist_ok=True)
    print(f"\nextracting {len(todo)} to {out}")
    with MinerU() as m:
        for pdf in todo:
            md = m.to_markdown(pdf)
            (out / f"{pdf.stem}.md").write_text(md, encoding="utf-8")
            print(f"  {pdf.stem}.md ({len(md)} chars)")
            for w in assess(md):
                print(f"    WARN {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
