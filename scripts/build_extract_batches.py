#!/usr/bin/env python3
"""Write extraction batch files: N citekeys per file, each line `citekey  doi  text_path`.

Skips citekeys that already have a record in data/papers (any file; validity is check_records'
job) unless --force. The batch file is what the extractor reads and what check_records.py
verifies against, so nothing outside a batch can be extracted by accident.

  uv run scripts/build_extract_batches.py --out /tmp/batches                 # everything pending
  uv run scripts/build_extract_batches.py --out /tmp/pilot --citekeys 2022zeng 2019yoon ...
  uv run scripts/build_extract_batches.py --out /tmp/batches --size 5 --limit 40
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from common import DATA, TEXT, read_json

PAPERS = DATA / "papers"


def text_path(entry: dict) -> pathlib.Path | None:
    pdf = (entry.get("files") or {}).get("pdf")
    if not pdf:
        return None
    p = TEXT / (pathlib.Path(pdf).stem + ".md")
    return p if p.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", type=int, default=5)
    ap.add_argument("--citekeys", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true", help="include citekeys that already have a record")
    args = ap.parse_args()

    manifest = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})
    keys = args.citekeys or sorted(manifest)
    rows = []
    skipped_done = skipped_notext = 0
    for ck in keys:
        e = manifest.get(ck)
        if e is None:
            print(f"not in manifest: {ck}", file=sys.stderr)
            return 1
        if not args.force and (PAPERS / f"{ck}.json").exists():
            skipped_done += 1
            continue
        tp = text_path(e)
        if tp is None:
            skipped_notext += 1
            print(f"no text for {ck}", file=sys.stderr)
            continue
        rows.append((ck, e["doi"], str(tp)))
    if args.limit:
        rows = rows[: args.limit]

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("batch_*.txt"):
        old.unlink()
    for i in range(0, len(rows), args.size):
        chunk = rows[i:i + args.size]
        (out / f"batch_{i // args.size + 1:03d}.txt").write_text(
            "\n".join(f"{ck}\t{doi}\t{tp}" for ck, doi, tp in chunk) + "\n")
    n = (len(rows) + args.size - 1) // args.size
    print(f"{len(rows)} papers in {n} batches of {args.size} -> {out}   "
          f"(skipped {skipped_done} already extracted, {skipped_notext} without text)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
