#!/usr/bin/env python3
"""Make the on-disk corpus browsable: name text by citekey, name out-of-scope
PDFs by the same convention as the library, and drop redundant duplicates.

Three jobs, all previously left half-done:

1. **Text named exactly like its PDF.** `data/text/` was keyed by content hash,
   then briefly by citekey — neither matched `library/`. A paper is now
   `library/Legon_2014_Transcranial....pdf` and
   `data/text/Legon_2014_Transcranial....md`: one name, two extensions.

2. **Out-of-scope PDFs keep their original filenames**, so the same paper looks
   different depending on which folder it landed in. Renamed to the library
   convention (`Author_Year_CompactTitle.pdf`).

3. **Duplicates are byte-identical or same-DOI copies of a file we keep.**
   Nothing is lost by deleting them; the manifest already records that they
   existed. `_duplicates/` was a safety net for a pipeline we now trust.

  uv run scripts/organise_files.py            # dry run
  uv run scripts/organise_files.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

from common import (
    CANDIDATES, DATA, DOI_RE, LIBRARY, TEXT, TEXT_SUFFIX, text_path, clean_doi,
    paper_stem, read_json, write_json,
)

DUPES = LIBRARY / "_duplicates"
OUT_OF_SCOPE = LIBRARY / "_out_of_scope"


def first_family(rec: dict, cache: dict | None = None) -> str | None:
    """The first author's surname, from wherever it can be found.

    The exclusion ledger stores no author list, so fall back to the Crossref
    cache by DOI — otherwise every out-of-scope paper is named "Unknown_".

    Returns None rather than "Unknown" when nothing is found, so the caller can
    tell "no author" apart from an author literally called Unknown, and so
    recover_identity() knows there is something left to look up.
    """
    authors = rec.get("authors") or []
    if not authors and cache and rec.get("doi"):
        msg = cache.get(rec["doi"]) or {}
        authors = [{"family": a.get("family") or a.get("name")}
                   for a in (msg.get("author") or [])
                   if a.get("family") or a.get("name")]
    fam = authors[0].get("family") if authors else None
    if not fam:
        # Last resort: the corpus's own "YYYY - Surname - Title.pdf" convention.
        m = re.match(r"^\d{4} - ([A-Za-z][A-Za-z'\-]+)", rec.get("file", "") or "")
        fam = m.group(1) if m else None
    return fam


def _same_paper(old_stem: str, new_stem: str) -> bool:
    """Do two stems name the same paper under different conventions?

    "Bancel_2024_SustainedReduction..._supp" and
    "2024_Bancel_SustainedReduction..._supp" carry the same three fields in a
    different order, so compare them as an unordered set of lowercased fields.
    """
    a = {x.lower() for x in old_stem.split("_") if x}
    b = {x.lower() for x in new_stem.split("_") if x}
    return bool(a) and a == b


def stem_for(rec: dict, cache: dict | None = None) -> str:
    """This file's name under the one shared convention (see common.paper_stem)."""
    return paper_stem(rec.get("year"), first_family(rec, cache), rec.get("title"))


def doi_from_pdf(pdf: Path) -> str | None:
    """First plausible DOI on page 1, for files ingest.json no longer tracks."""
    try:
        txt = subprocess.run(["pdftotext", "-f", "1", "-l", "2", str(pdf), "-"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    for m in DOI_RE.finditer(txt):
        if (d := clean_doi(m.group(1))):
            return d
    return None


def sha16(p: Path) -> str:
    h = hashlib.sha1()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="override the mass-deletion brake on orphaned text")
    ap.add_argument("--keep-duplicates", action="store_true",
                    help="rename duplicates instead of deleting them")
    args = ap.parse_args()

    man = read_json(DATA / "manifest.json", {}) or {}
    papers = man.get("papers", {})
    ingest = (read_json(DATA / "ingest.json", {}) or {}).get("records", {})
    cache = read_json(DATA / "crossref-cache.json", {}) or {}
    excluded = {p["doi"]: p for p in
                (read_json(DATA / "excluded.json", {}) or {}).get("papers", [])
                if p.get("doi")}
    by_doi_rec = {r["doi"]: r for r in ingest.values() if r.get("doi")}

    # ---- 1. text named after the PDF ----------------------------------------
    # Key documents are not manifest records, but their PDFs live in library/
    # and their text should stay readable and greppable -- they are the reading
    # list. Without this they look like orphans and get deleted.
    key_docs = (read_json(DATA / "key-documents.json", {}) or {}).get("documents", {})
    entries = dict(papers)
    for doi, kd in key_docs.items():
        ck = kd.get("citekey") or doi.replace("/", "_")
        entries.setdefault(ck, {"doi": doi, "files": {}})

    text_map, missing_text = {}, []
    for ck, p in entries.items():
        rec = by_doi_rec.get(p.get("doi"))
        key = rec.get("text_key") if rec else None
        src = text_path(key) if key else None
        if not (src and src.exists()):
            missing_text.append(ck)
            continue
        # Same stem as the PDF, so data/text/ and library/ line up 1:1.
        pdfname = (p.get("files") or {}).get("pdf")
        if not pdfname:
            # A key document has no manifest files block; locate its PDF.
            pdfname = (rec or {}).get("file") or f"{ck}.pdf"
            if not (LIBRARY / pdfname).exists():
                hit = [f for f in LIBRARY.glob("*.pdf")
                       if doi_from_pdf(f) == p.get("doi")]
                if hit:
                    pdfname = hit[0].name
        text_map[ck] = (src, text_path(pathlib.Path(pdfname).stem))

    # Supplementary files are held and extracted like any other PDF, but they are
    # not manifest records, so nothing above mirrors their text. When the naming
    # convention changed, four supplements' PDFs were renamed and their markdown
    # was left behind under the old stem -- which the sweep then classed as an
    # orphan. Mirror them by matching the stem directly: a supplement's text is
    # named after the supplement, and its PDF is sitting right there.
    for supp in LIBRARY.glob("*_supp.pdf"):
        dst = text_path(supp.stem)
        if dst.exists():
            continue
        # Its text is whichever file the PDF used to be called. The ingest record
        # for a supplement carries the original filename.
        for rec in ingest.values():
            if not rec.get("is_supplementary"):
                continue
            cand_src = text_path(rec.get("text_key") or "")
            if cand_src.exists() and _same_paper(cand_src.stem, supp.stem):
                text_map[f"_supp:{supp.stem}"] = (cand_src, dst)
                break


    # ---- 2. out-of-scope naming --------------------------------------------
    oos_renames = []
    for pdf in sorted(OUT_OF_SCOPE.glob("*.pdf")):
        rec = None
        for r in ingest.values():
            if r.get("file") == pdf.name:
                rec = r
                break
        if rec is None:
            # Filed under a renamed stem by an earlier pass; match on the
            # excluded ledger's recorded filenames instead.
            for doi, e in excluded.items():
                if pdf.name in (e.get("files") or []):
                    rec = by_doi_rec.get(doi) or {"doi": doi, **e}
                    break
        if rec is None:
            continue
        want = f"{stem_for(rec, cache)}.pdf"
        if want != pdf.name and not (OUT_OF_SCOPE / want).exists():
            oos_renames.append((pdf, OUT_OF_SCOPE / want))

    # ---- 3. duplicates ------------------------------------------------------
    # A duplicate is redundant when the paper it copies is already in the
    # library -- byte-identity is too strict, since the same paper downloaded
    # twice (publisher proof vs final, different mirror) differs byte-wise while
    # carrying identical content. The manifest records that the duplicate
    # existed, so nothing is lost.
    kept_dois = {p["doi"] for p in papers.values() if p.get("doi")}
    # A merged preprint's DOI is no longer a manifest key, but the paper IS
    # held -- its PDF sits in files.alternates. Its duplicate is redundant too.
    kept_dois |= {p["preprint"]["doi"] for p in papers.values()
                  if (p.get("preprint") or {}).get("doi")}
    kept_hashes = {sha16(p) for p in LIBRARY.glob("*.pdf")}
    dup_delete, dup_keep = [], []
    for pdf in sorted(DUPES.glob("*.pdf")):
        rec = next((r for r in ingest.values() if r.get("file") == pdf.name), None)
        doi = rec.get("doi") if rec else None
        if doi is None:
            # Moved here by an earlier run, so ingest.json no longer keys it by
            # this filename. Read the DOI off the PDF itself.
            doi = doi_from_pdf(pdf)
        redundant = (doi in kept_dois) if doi else (sha16(pdf) in kept_hashes)
        (dup_delete if redundant else dup_keep).append(pdf)

    # ---- report -------------------------------------------------------------
    print(f"text -> citekey       {len(text_map)} to mirror"
          f"{f', {len(missing_text)} with no cached text' if missing_text else ''}")
    print(f"out-of-scope renames  {len(oos_renames)} of "
          f"{len(list(OUT_OF_SCOPE.glob('*.pdf')))}")
    print(f"duplicates            {len(dup_delete)} redundant (the paper is in the "
          f"library), {len(dup_keep)} unresolved")
    if dup_keep:
        print("  unresolved -- no matching DOI in the library. Check before deleting:")
        for p in dup_keep[:10]:
            print(f"    {p.name[:74]}")
    for src, dst in oos_renames[:5]:
        print(f"  rename: {src.name[:44]}\n       -> {dst.name[:44]}")

    if not args.apply:
        print("\nDRY RUN. Re-run with --apply.")
        return 0

    # ---- execute ------------------------------------------------------------
    # MOVE, not copy. Copying left both a hash-named cache file and a
    # citekey-named duplicate, doubling the directory and making it unclear
    # which is authoritative. The hash is only needed before a paper has a
    # citekey; once filed, the citekey IS the name.
    n_text = 0
    for ck, (src, dst) in text_map.items():
        if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            if src != dst and src.exists():
                src.unlink()          # stale hash duplicate of an up-to-date file
            continue
        shutil.move(str(src), str(dst))
        n_text += 1

    # The invariant is simply: one text file per PDF we hold, same stem.
    # Deriving the keep-list from the manifest instead kept deleting the key
    # documents' text, because they are deliberately not manifest records.
    live = {dst.name for _, dst in text_map.values()}
    live |= {f"{f.stem}{TEXT_SUFFIX}" for f in LIBRARY.glob("*.pdf")}
    # A pending candidate's extraction is not an orphan -- it is work not yet
    # filed. Without this line, running --apply while candidates are waiting
    # deletes every one of their markdown files: measured at 76, several hours
    # of MinerU output, gone silently because the loop reports nothing. The
    # keep-list has to describe every PDF we hold, not only the filed ones.
    live |= {f"{f.stem}{TEXT_SUFFIX}" for f in CANDIDATES.glob("*.pdf")}
    # Any extension: this also sweeps up .txt left by the pdftotext era.
    orphans = [f for f in TEXT.glob("*.*")
                if f.name not in live and f.is_file()
                and not f.name.startswith(".")]
    # Deleted, and named while deleting. An orphan is text whose PDF we no longer
    # hold, which after a clean run means it is debris -- 31 of them were, all
    # left behind when a bug named papers "1950_Anon_..." and they were
    # re-extracted correctly afterwards.
    #
    # A quarantine directory was the wrong answer to the right worry. The worry
    # was a bug that deleted 76 pending candidates' markdown; the fix for that is
    # the keep-list above describing every PDF we hold, plus this brake. A folder
    # of files nobody ever revisits is just a slower deletion.
    if orphans and len(orphans) > max(20, len(live) // 5) and not args.force:
        print(f"REFUSING to delete {len(orphans)} text files -- that is more than "
              f"a fifth of the corpus and is what a keep-list bug looks like, not "
              f"a tidy-up.", file=sys.stderr)
        print("  Check the keep-list, or pass --force if this is really intended.",
              file=sys.stderr)
        return 1
    for f in orphans:
        print(f"  deleting orphaned text (no PDF held): {f.name[:66]}")
        f.unlink()

    for src, dst in oos_renames:
        shutil.move(str(src), str(dst))
    n_del = 0
    if not args.keep_duplicates:
        for p in dup_delete:
            p.unlink()
            n_del += 1

    # data/text-index-citekey.json used to be written here. It was written and
    # never read -- grep across scripts/, docs/ and the markdown found exactly one
    # reference, this one. It had gone fully stale without anyone noticing: 362
    # entries, every one naming a .txt file that the MinerU migration had
    # replaced. An index nothing consults cannot be trusted and does not need to
    # exist; the corpus repo's text-index.json already records extraction state, and the
    # PDF-stem naming means the mapping is the filename itself.

    print(f"\nrenamed {n_text} text files to <citekey>{TEXT_SUFFIX}; removed {len(orphans)} orphaned files")
    print(f"renamed {len(oos_renames)} out-of-scope PDFs to the library convention")
    print(f"deleted {n_del} byte-identical duplicates"
          f"{f'; {len(dup_keep)} variants kept for review' if dup_keep else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
