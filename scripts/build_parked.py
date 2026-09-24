#!/usr/bin/env python3
"""List every paper parked by a reversible scope ruling, with clickable DOIs.

E9 excludes ultrasound whose intended effect is repair. That is a judgement about what
this archive is for, not a fact about the papers, so it has to be reversible
without a new sweep. Every E9 verdict carries `parked_class`, and this writes
them out grouped by that class.

To reverse a ruling: re-screen the parked DOIs, restore the worked
example in INCLUSION.md, and re-screen the DOIs listed here.

  uv run scripts/build_parked.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from common import DATA, load_screening, read_json


def main() -> int:
    parked = {d: v for d, v in load_screening().items() if v.get("parked_class")}

    cands = (read_json(DATA / "sweep-candidates.json", {}) or {}).get("candidates", {})
    excl = {p.get("doi"): p for p in
            (read_json(DATA / "excluded.json", {}) or {}).get("papers", []) if p.get("doi")}
    # A parked paper may be one we hold, one the sweep found, or one already in
    # the exclusion ledger. Only the manifest knows the first, and without it a
    # held paper renders as "(title not held locally)" -- unreadable for exactly
    # the papers most worth reviewing.
    held = {p.get("doi"): p for p in
            (read_json(DATA / "manifest.json", {}) or {}).get("papers", {}).values()
            if p.get("doi")}

    by_class: dict[str, list] = {}
    for doi, v in parked.items():
        by_class.setdefault(v["parked_class"], []).append((doi, v))

    L = ["# Parked papers", "",
         "Papers excluded by a **scope ruling** rather than by a fact about the paper. "
         "Each is held here so the "
         "ruling can be reversed without a new sweep.", ""]

    for cls, rows in sorted(by_class.items()):
        L += [f"## {cls.replace('_', ' ')}  ({len(rows)} papers)", ""]
        if cls == "neural_regeneration":
            L += ["**Excluded under E9**: the endpoint is tissue repaired or grown, not neural activity changed. "
                  "The reasoning: if regeneration is not a mechanism for primary ultrasound "
                  "neuromodulation, it does not belong, and at roughly 158 sweep candidates it "
                  "is large enough to reshape the archive if admitted.", "",
                  "Papers that *look* like this class but were read and kept: "
                  "`10.1016/0301-5629(90)90008-z` (axon excitability), "
                  "`10.1016/j.apmr.2004.12.035` (spinal nociceptive modulation), "
                  "`10.1002/jnr.70079` (FUS nerve blockade). All three measure activity.", ""]
        for doi, v in sorted(rows, key=lambda kv: kv[0]):
            meta = held.get(doi) or cands.get(doi) or excl.get(doi) or {}
            title = meta.get("title") or "(title not held locally)"
            year = meta.get("year") or ""
            L.append(f"- [{title}](https://doi.org/{doi})  \n  `{doi}`"
                     + (f" · {year}" if year else ""))
            L.append(f"  > {v.get('reason', '').split(' Parked under')[0]}")
        L.append("")

    L += ["---", "",
          "**To reverse:** re-screen the DOIs above and record the new verdicts in `data/screening.jsonl`, "
          "then restore the worked example in `INCLUSION.md`. No paper was deleted and no sweep state was discarded."]

    out = DATA / "parked-regeneration.md"
    out.write_text("\n".join(L))
    print(f"wrote {out.relative_to(DATA.parent)} "
          f"({len(parked)} papers, {len(by_class)} class(es))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
