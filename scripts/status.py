#!/usr/bin/env python3
"""Print the current state of the archive.

Every number here used to be written into the handoff documents by hand,
where it went stale within days: one claimed 398 papers when there were 413,
described citekeys in a format that had been replaced, and devoted three sections
to an API problem that had since been designed away. A fact that a script can
compute does not belong in prose.

  uv run scripts/status.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import Counter

from common import load_screening, DATA, LIBRARY, CANDIDATES, TEXT, TEXT_SUFFIX, read_json

OOS = LIBRARY / "_out_of_scope"


def main() -> int:
    man = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})
    cands = (read_json(DATA / "sweep-candidates.json", {}) or {}).get("candidates", {})
    kd = (read_json(DATA / "key-documents.json", {}) or {}).get("documents", {})
    v = load_screening()
    held = {(p.get("doi") or "").lower() for p in man.values()}

    print("ARCHIVE")
    print(f"  papers in the manifest      {len(man)}")
    for cls, n in Counter(p.get("record_class") for p in man.values()).most_common():
        print(f"      {cls or 'unclassified':22} {n}")
    print(f"  key documents (reading list){len(kd):>5}")
    print(f"  parked by a scope ruling    {sum(1 for x in v.values() if x.get('parked_class')):>5}"
          f"   (data/parked-regeneration.md)")

    print("\nFILES")
    lib = {p.stem for p in LIBRARY.glob("*.pdf")}
    txt = {p.stem for p in TEXT.glob(f"*{TEXT_SUFFIX}")}
    print(f"  library PDFs                {len(lib)}")
    print(f"  extracted markdown          {len(txt)}"
          f"   {'MISMATCH ' + str(len(lib ^ txt)) if lib ^ txt else '(1:1, clean)'}")
    print(f"  out of scope (parked PDFs)  {len(list(OOS.glob('*.pdf')))}")
    print(f"  candidates waiting          {len(list(CANDIDATES.glob('*.pdf')))}")

    print("\nSCREENING")
    print(f"  decisions recorded          {len(v)}")
    print(f"  sweep candidates harvested  {len(cands)}")
    undecided = [d for d in cands if d not in v and d not in held]
    print(f"  still unscreened            {len(undecided)}")
    unspec = [d for d, x in v.items() if x.get("verdict") == "unspecified"]
    print(f"  awaiting a decision         {len(unspec)}"
          f"   (data/needs-decision.md)")

    # No want-list number here, deliberately. acquire.py owns that calculation --
    # it dedups against held titles as well as DOIs, and re-applies the parts of
    # the bar that need no judgement to verdicts made before the current criteria. A
    # second implementation here disagreed with it (32 against 3) because it
    # skipped both, and two copies of a rule is two rules. Reading the CSV
    # instead is no better: it claimed 62 when 61 had already been filed.
    print("  want-list                   run acquire.py, then open data/wanted.html")

    print("\nEXTRACTION")
    recs = []
    for f in sorted((DATA / "papers").glob("*.json")):
        try:
            recs.append(json.loads(f.read_text()))
        except json.JSONDecodeError:
            print(f"  UNREADABLE {f.name}")
    print(f"  records in data/papers      {len(recs)} of {len(man)} papers")
    if recs:
        exposures = sum(len(r.get("exposures", [])) for r in recs)
        flags = sum(len(r.get("provenance", {}).get("flags", [])) for r in recs)
        stripped = sum(1 for r in recs for fl in r.get("provenance", {}).get("flags", []) if fl.get("reason", "").startswith("value removed"))
        second = [v for r in recs for v in (r.get("provenance", {}).get("second_pass") or {}).values()]
        human = sum(1 for r in recs if r.get("provenance", {}).get("human_edited"))
        print(f"  exposures                   {exposures}")
        print(f"  extractor flags             {flags}   (of which {stripped} values removed for a missing quote)")
        if second:
            dis = sum(1 for x in second if x == "disagree")
            print(f"  second pass                 {len(second)} fields, {dis} disagree ({100 * dis / len(second):.1f}%)")
            tb = [v for r in recs for v in (r.get("provenance", {}).get("tiebreak") or {}).values()]
            if tb:
                fixed = sum(1 for x in tb if x in ("second", "neither"))
                open_ = sum(1 for x in tb if x in ("both_defensible", "unresolvable"))
                print(f"  tie-break                   {len(tb)} fields ruled, {fixed} values corrected, {open_} left open")
        else:
            print("  second pass                 not run")
        print(f"  human-edited records        {human}")
        abstracts = sum(1 for r in recs if (read_json(DATA / "abstracts" / f"{r['citekey']}.json", {}) or {}).get("text"))
        print(f"  with an abstract            {abstracts}")
        print("  vocabulary tail             run vocab_report.py")
    print("  site data                   run build_site.py (writes into the website checkout); then `npm run build` there")

    return 0


if __name__ == "__main__":
    sys.exit(main())
