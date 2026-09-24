#!/usr/bin/env python3
"""Extract every PDF to markdown in data/text/ (gitignored).

  uv run scripts/extract_text.py [--force] [--limit N] [--only SUBSTR]

Idempotent: a paper whose markdown already exists is skipped. Output is never
committed -- it is a derivative of copyrighted PDFs.

Extraction is MinerU (see pdf_markdown.py for why, and for the measurements
that killed pdftotext as the primary). pdftotext survives here as a fallback
only: when MinerU fails on a paper we would rather hold degraded text than no
text, and the index records which one produced each file so the difference is
never invisible.

A full pass over 375 papers takes roughly four hours. It is resumable -- stop
it and run it again.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import time
from pathlib import Path

from common import (CANDIDATES, DATA, LIBRARY, TEXT, TEXT_INDEX, TEXT_SUFFIX, read_json,
                    text_path, write_json)
from pdf_markdown import MinerU, assess


def content_hash(path: Path) -> str:
    """Content hash, used to detect byte-identical duplicate PDFs."""
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def pdftotext_fallback(pdf: Path) -> str:
    """Last resort. Reading order, page breaks preserved as form feeds.

    Deliberately not `-layout`: on a two-column paper that leaves a gutter of
    spaces down every line and splits phrases across it, so a search for "pulse
    repetition frequency" misses papers containing it.
    """
    try:
        res = subprocess.run(["pdftotext", str(pdf), "-"],
                             capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError("pdftotext timeout")
    except FileNotFoundError:
        raise RuntimeError("pdftotext not found (brew install poppler)")
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "").strip()[:200] or f"exit {res.returncode}")
    return res.stdout


def fmt_eta(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-extract even if cached")
    ap.add_argument("--limit", type=int, help="stop after N extractions")
    ap.add_argument("--only", help="only PDFs whose name contains this substring")
    ap.add_argument("--keep-txt", action="store_true",
                    help="keep superseded pdftotext .txt files")
    args = ap.parse_args()

    TEXT.mkdir(parents=True, exist_ok=True)
    sources = sorted(list(CANDIDATES.glob("*.pdf")) + list(LIBRARY.glob("*.pdf")))
    if args.only:
        sources = [p for p in sources if args.only.lower() in p.name.lower()]
    if not sources:
        sys.exit(f"No PDFs found in {CANDIDATES} or {LIBRARY}")

    # Merge into the previous index, never replace it. A filtered run
    # (--only/--limit) enumerates a subset, and rewriting from that subset threw
    # away every other paper's record -- five --only re-extractions left an
    # index holding one file. What each paper's text is and what produced it
    # outlives any single run.
    prev = (read_json(TEXT_INDEX, {}) or {}).get("files", {})
    index: dict[str, dict] = {}
    warnings: list[dict] = []
    failed: list[dict] = []
    todo = [p for p in sources if args.force or not text_path(p.stem).exists()]
    done = skipped = fellback = 0
    started = time.time()

    print(f"{len(sources)} PDFs, {len(todo)} to extract", file=sys.stderr)

    with MinerU() as mineru:
        for pdf in sources:
            out = text_path(pdf.stem)
            index[pdf.name] = {"text_key": pdf.stem, "text_file": out.name,
                               "size": pdf.stat().st_size, "sha": content_hash(pdf)}

            if out.exists() and not args.force:
                # Carry forward what produced it; "cached" alone would erase the
                # distinction between a MinerU paper and a fallback one.
                old = prev.get(pdf.name, {})
                index[pdf.name]["extractor"] = old.get("extractor", "unknown")
                for k in ("chars", "warnings"):
                    if k in old:
                        index[pdf.name][k] = old[k]
                skipped += 1
                continue
            if args.limit and done >= args.limit:
                continue

            extractor = "mineru"
            try:
                md = mineru.to_markdown(pdf)
            except Exception as exc:  # noqa: BLE001 - any failure means try the fallback
                try:
                    md = pdftotext_fallback(pdf)
                    extractor = "pdftotext-fallback"
                    fellback += 1
                    warnings.append({"file": pdf.name,
                                     "warning": f"MinerU failed ({exc}); used pdftotext"})
                    print(f"  FALLBACK {pdf.name}: {exc}", file=sys.stderr)
                except Exception as exc2:  # noqa: BLE001
                    failed.append({"file": pdf.name, "error": f"{exc} | {exc2}"})
                    print(f"  FAIL {pdf.name}: {exc2}", file=sys.stderr)
                    continue

            out.write_text(md, encoding="utf-8")
            index[pdf.name]["extractor"] = extractor
            index[pdf.name]["chars"] = len(md)

            problems = assess(md)
            if problems:
                index[pdf.name]["warnings"] = problems
                for p in problems:
                    warnings.append({"file": pdf.name, "warning": p})
                print(f"  WARN {pdf.name}: {'; '.join(problems)}", file=sys.stderr)

            # The pdftotext-era .txt is now superseded. Leaving it behind means
            # two texts per paper and a coin toss over which one a reader greps.
            stale = TEXT / f"{pdf.stem}.txt"
            if stale.exists() and not args.keep_txt:
                stale.unlink()

            done += 1
            rate = (time.time() - started) / done
            left = len([p for p in todo if not text_path(p.stem).exists()])
            print(f"  [{done}/{len(todo)}] {pdf.stem[:58]}  "
                  f"({rate:.0f}s each, ~{fmt_eta(rate * left)} left)", file=sys.stderr)

    # An unfiltered run saw every PDF, so its index is complete and authoritative
    # -- take it whole, which drops records for papers that have since been
    # deleted or moved out of scope. Only a filtered run needs the merge, because
    # it never looked at the papers its filter excluded.
    merged = {**prev, **index} if (args.only or args.limit) else index
    write_json(TEXT_INDEX, {
        "files": merged, "warnings": warnings, "failed": failed,
        "note": f"Text is markdown ({TEXT_SUFFIX}) from MinerU; see docs/text-extraction.md. "
                f"'extractor' records what produced each file.",
    })

    print(f"\nextracted {done}, cached {skipped}, fallback {fellback}, "
          f"warnings {len(warnings)}, failed {len(failed)}", file=sys.stderr)
    if failed:
        print(f"{len(failed)} file(s) failed -- see text-index.json in the corpus repo", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
