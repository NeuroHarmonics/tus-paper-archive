#!/usr/bin/env python3
"""Verify tie-break verdict files against their case files. Every disputed path needs exactly one
ruling with a valid verdict and severity, and every quote given must occur in the paper's text.

  uv run scripts/check_tiebreak.py --batch <dir>/batch_001.txt --cases <dir>/cases --dir <dir>/verdicts
"""
from __future__ import annotations
import argparse, json, pathlib, sys
from common import DATA, read_json
from validate import alnum, quote_in_text, text_for

VERDICTS = {"first", "second", "both_defensible", "neither", "unresolvable"}
SEV = {"high", "medium", "low"}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", action="append", default=[])
    ap.add_argument("--batches")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--dir", required=True)
    a = ap.parse_args()
    files = [pathlib.Path(b) for b in a.batch]
    if a.batches:
        files += sorted(pathlib.Path(a.batches).glob("batch_*.txt"))
    manifest = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})
    failed = n = ok = 0
    for b in files:
        for ck in [l.strip() for l in b.read_text().splitlines() if l.strip()]:
            n += 1
            f = pathlib.Path(a.dir) / f"{ck}.json"
            if not f.exists():
                print(f"MISSING  {ck}"); failed += 1; continue
            try:
                v = json.loads(f.read_text())
            except json.JSONDecodeError as e:
                print(f"BAD JSON {ck}: {e}"); failed += 1; continue
            case = json.loads((pathlib.Path(a.cases) / f"{ck}.json").read_text())
            want = [d["path"] for d in case["disputed"]]
            got = [r.get("path") for r in v.get("rulings", [])]
            probs = []
            for p in want:
                if got.count(p) != 1:
                    probs.append(f"{p}: {got.count(p)} rulings (need 1)")
            for p in got:
                if p not in want:
                    probs.append(f"{p}: not a disputed path")
            text = text_for(ck, manifest); ta = alnum(text) if text else None
            for r in v.get("rulings", []):
                if r.get("verdict") not in VERDICTS:
                    probs.append(f"{r.get('path')}: bad verdict {r.get('verdict')!r}")
                if r.get("severity") not in SEV:
                    probs.append(f"{r.get('path')}: bad severity {r.get('severity')!r}")
                q = r.get("quote")
                if r.get("verdict") != "unresolvable" and not q:
                    probs.append(f"{r.get('path')}: quote required for verdict {r.get('verdict')}")
                if q and ta is not None and not quote_in_text(q, ta):
                    probs.append(f"{r.get('path')}: quote not found in text")
            if probs:
                failed += 1; print(f"FAIL     {ck}")
                for p in probs: print(f"           {p}")
            else:
                ok += 1
    print(f"\n{ok}/{n} files pass")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
