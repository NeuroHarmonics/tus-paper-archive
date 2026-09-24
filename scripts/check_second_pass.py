#!/usr/bin/env python3
"""Verify a second-pass batch's files. Never trust the agent's report.

For every batch file: each citekey must have a file in the output directory whose citekey and
exposure count match the skeleton; every numeric leaf must be a number, a list of numbers,
"not_reported" or null, never "?"; every number must carry a quote that occurs in the paper's
text (validate.quote_in_text). A number whose quote fails is set to "not_reported" and flagged,
unless --no-strip, which only reports. Exits non-zero if any file is missing or malformed.

  uv run scripts/check_second_pass.py --batch <dir>/batch_001.txt --dir <dir>/records --no-strip
  uv run scripts/check_second_pass.py --batches <dir> --dir <dir>/records
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from common import DATA, read_json
from validate import NUMERIC_LEAVES, PAPER_NUMERIC, alnum, get_path, is_number, quote_in_text, set_path, text_for


def read_batch(p: pathlib.Path) -> list[tuple[str, str, str]]:
    return [tuple(line.split("\t")) for line in p.read_text().splitlines() if line.strip()]


def check_one(rec: dict, sk: dict, text_alnum: str | None, strip: bool) -> tuple[list[str], list[str], bool]:
    """Return (problems, stripped, changed). Problems fail the file; stripped values are fixed."""
    problems, stripped = [], []
    changed = False
    if rec.get("citekey") != sk["citekey"]:
        problems.append(f"citekey {rec.get('citekey')!r} != skeleton {sk['citekey']!r}")
    exps = rec.get("exposures")
    if not isinstance(exps, list) or len(exps) != len(sk["exposures"]):
        problems.append(f"exposure count {len(exps) if isinstance(exps, list) else 'missing'} != skeleton {len(sk['exposures'])}")
        return problems, stripped, changed
    prov = rec.setdefault("provenance", {})
    quotes = prov.setdefault("source_quotes", {})
    flags = prov.setdefault("flags", [])

    def leaf_check(container, leaf: str, key: str):
        nonlocal changed
        v = get_path(container, leaf)
        if v == "?":
            problems.append(f"{key}: still '?'")
            return
        if not (v is None or v == "not_reported" or is_number(v)):
            problems.append(f"{key}: bad value {v!r}")
            return
        if not is_number(v):
            return
        q = quotes.get(key)
        if not q:
            for j, other in enumerate(exps):
                ok2 = f"exposures[{j}].{leaf}"
                if ok2 != key and get_path(other, leaf) == v and quotes.get(ok2):
                    q = quotes[ok2]
                    quotes[key] = q
                    changed = True
                    break
        ok = bool(q) and (text_alnum is None or quote_in_text(q, text_alnum))
        if not ok:
            why = "no source quote" if not q else "quote not found in text"
            if strip:
                set_path(container, leaf, "not_reported")
                quotes.pop(key, None)
                flags.append({"field": key, "reason": f"second pass: value removed at check, {why}"})
                stripped.append(f"{key}: {why}, removed")
                changed = True
            else:
                stripped.append(f"{key}: {why}")

    for f in PAPER_NUMERIC:
        leaf_check(rec, f, f)
    for i, ex in enumerate(exps):
        for leaf in NUMERIC_LEAVES:
            leaf_check(ex, leaf, f"exposures[{i}].{leaf}")
    return problems, stripped, changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", action="append", default=[])
    ap.add_argument("--batches")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--no-strip", action="store_true")
    args = ap.parse_args()
    batch_files = [pathlib.Path(b) for b in args.batch]
    if args.batches:
        batch_files += sorted(pathlib.Path(args.batches).glob("batch_*.txt"))
    if not batch_files:
        ap.error("give --batch or --batches")
    out_dir = pathlib.Path(args.dir)
    manifest = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})

    failed = 0
    n_ok = n_files = n_stripped = 0
    for b in batch_files:
        for ck, _tp, sp in read_batch(b):
            n_files += 1
            f = out_dir / f"{ck}.json"
            if not f.exists():
                print(f"MISSING  {ck}  ({b.name})")
                failed += 1
                continue
            try:
                rec = json.loads(f.read_text())
            except json.JSONDecodeError as e:
                print(f"BAD JSON {ck}: {e}")
                failed += 1
                continue
            sk = json.loads(pathlib.Path(sp).read_text())
            text = text_for(ck, manifest)
            problems, stripped, changed = check_one(rec, sk, alnum(text) if text else None, not args.no_strip)
            for s in stripped:
                print(f"  {ck:22s} {s}")
            n_stripped += len(stripped)
            if problems:
                failed += 1
                print(f"FAIL     {ck}")
                for p in problems:
                    print(f"           {p}")
            elif stripped and args.no_strip:
                failed += 1
            else:
                n_ok += 1
            if changed:
                f.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
    print(f"\n{n_ok}/{n_files} files pass, {n_stripped} unquoted values" + (" reported" if args.no_strip else " removed"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
