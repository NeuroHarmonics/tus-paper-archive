#!/usr/bin/env python3
"""Verify an extraction wave's files against their batch. Never trust the agent's report.

For every batch file given: each citekey must have a record in the output directory; the
record's citekey and doi must match the batch line; validate.py's checks must pass after
unquoted numbers are stripped; derive.py is applied. Extras in the output directory that are
in no batch are reported (they are not deleted). Exits non-zero if anything failed.

  uv run scripts/check_records.py --batches /tmp/pilot            # all batch_*.txt in the dir
  uv run scripts/check_records.py --batch /tmp/pilot/batch_001.txt
  uv run scripts/check_records.py --batches /tmp/pilot --dir /tmp/pilot/out   # a scratch output dir
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import jsonschema

from common import DATA, read_json
from validate import check, load_schema
import derive

PAPERS = DATA / "papers"


def read_batch(p: pathlib.Path) -> list[tuple[str, str, str]]:
    rows = []
    for line in p.read_text().splitlines():
        if line.strip():
            ck, doi, tp = line.split("\t")
            rows.append((ck, doi, tp))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", action="append", default=[])
    ap.add_argument("--batches")
    ap.add_argument("--dir", default=str(PAPERS))
    ap.add_argument("--no-strip", action="store_true", help="report unquoted values instead of removing them (pilot use)")
    ap.add_argument("--promote-to", help="copy each record that passes into this directory (e.g. data/papers)")
    args = ap.parse_args()

    batch_files = [pathlib.Path(b) for b in args.batch]
    if args.batches:
        batch_files += sorted(pathlib.Path(args.batches).glob("batch_*.txt"))
    if not batch_files:
        ap.error("give --batch or --batches")
    out_dir = pathlib.Path(args.dir)

    validator = jsonschema.Draft202012Validator(load_schema())
    manifest = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})

    expected: dict[str, str] = {}
    for b in batch_files:
        for ck, doi, _ in read_batch(b):
            expected[ck] = doi

    failed = 0
    stripped_total = 0
    for ck, doi in sorted(expected.items()):
        f = out_dir / f"{ck}.json"
        if not f.exists():
            print(f"MISSING {ck}")
            failed += 1
            continue
        try:
            rec = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            print(f"FAIL {ck}: not JSON ({e})")
            failed += 1
            continue
        if rec.get("citekey") != ck or (rec.get("doi") or "").lower() != doi.lower():
            print(f"FAIL {ck}: identity mismatch (citekey={rec.get('citekey')}, doi={rec.get('doi')})")
            failed += 1
            continue
        before = len(rec.get("provenance", {}).get("flags", []))
        problems, changed = check(rec, validator, manifest, strip=not args.no_strip)
        if changed:
            stripped_total += len(rec["provenance"]["flags"]) - before
        if problems:
            print(f"FAIL {ck}")
            for p in problems:
                print(f"   - {p}")
            failed += 1
            if changed:
                f.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
            continue
        derive.apply(rec, manifest)
        f.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
        if args.promote_to:
            dest = pathlib.Path(args.promote_to)
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f.name).write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
        print(f"ok   {ck}" + (f"   ({len(rec['provenance']['flags']) - before} unquoted values stripped)" if changed else ""))

    extras = sorted(p.stem for p in out_dir.glob("*.json") if p.stem not in expected and p.stem in manifest) if out_dir != PAPERS else []
    if extras:
        print(f"note: {len(extras)} records in {out_dir} are not in these batches (left alone)")

    print(f"\n{len(expected) - failed}/{len(expected)} records ok; {stripped_total} unquoted values stripped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
