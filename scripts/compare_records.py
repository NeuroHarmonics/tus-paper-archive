#!/usr/bin/env python3
"""Compare two independent extractions of the same papers, field by field.

Used for the pilot (two full reads) and for the second pass (a blind re-read of the numeric
fields). Numbers agree within 2 % relative; lists agree as sets; enums agree exactly after
normalisation. Prints per-field agreement and, with --write-second-pass, records
`provenance.second_pass[path] = agree|disagree` into the records in --a.

  uv run scripts/compare_records.py --a /tmp/pilot/run1 --b /tmp/pilot/run2
  uv run scripts/compare_records.py --a data/papers --b /tmp/second --write-second-pass
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import defaultdict

from validate import NUMERIC_LEAVES, get_path

PAPER_CAT = ["model_system", "conditions", "subject_unit", "randomised", "blinding", "sham_type",
             "auditory_control", "readouts", "readout_timing", "anaesthesia", "direction_of_effect", "adverse_events"]
PAPER_NUM = ["n_subjects", "n_sessions_per_subject"]
EXPO_CAT = ["target.terms", "device.family", "device.manufacturer", "device.model", "in_situ.method",
            "in_situ.reported_as", "timing.waveform"]


def norm(v):
    if isinstance(v, list):
        return tuple(sorted(norm(x) for x in v))
    if isinstance(v, str):
        return re.sub(r"[^a-z0-9.]", "", v.lower())
    return v


def same(a, b) -> bool:
    a, b = norm(a), norm(b)
    # a list with one distinct value is that value
    if isinstance(a, tuple) and len(set(a)) == 1:
        a = a[0]
    if isinstance(b, tuple) and len(set(b)) == 1:
        b = b[0]
    if isinstance(a, tuple) or isinstance(b, tuple):
        if isinstance(a, tuple) and isinstance(b, tuple):
            return tuple(sorted(set(a))) == tuple(sorted(set(b)))
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) <= 0.02 * max(abs(a), abs(b), 1e-9)
    # free text (device model, manufacturer): the shorter is a prefix or substring of the longer
    if isinstance(a, str) and isinstance(b, str) and a and b and a not in ("notreported",) and b not in ("notreported",):
        if len(a) >= 3 and len(b) >= 3 and (a in b or b in a):
            return True
    return a == b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--write-second-pass", action="store_true")
    ap.add_argument("--numeric-only", action="store_true")
    args = ap.parse_args()
    A, B = pathlib.Path(args.a), pathlib.Path(args.b)

    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    disagreements: list[str] = []
    n_papers = 0
    for fa in sorted(A.glob("*.json")):
        fb = B / fa.name
        if not fb.exists():
            continue
        ra, rb = json.loads(fa.read_text()), json.loads(fb.read_text())
        n_papers += 1
        second: dict[str, str] = {}

        def cmp(path: str, va, vb, group: str):
            ok = same(va, vb)
            tally[group][0] += ok
            tally[group][1] += 1
            if path.startswith("exposures") or path in PAPER_NUM:
                second[path] = "agree" if ok else "disagree"
            if not ok:
                disagreements.append(f"  {ra['citekey']:20s} {path:48s} {str(va)[:34]:34s} | {str(vb)[:34]}")

        for f in PAPER_NUM:
            cmp(f, ra.get(f), rb.get(f), "paper numeric")
        if not args.numeric_only:
            for f in PAPER_CAT:
                cmp(f, ra.get(f), rb.get(f), "paper categorical")
        if len(ra["exposures"]) != len(rb["exposures"]):
            tally["exposure count"][1] += 1
            disagreements.append(f"  {ra['citekey']:20s} exposure count {len(ra['exposures'])} | {len(rb['exposures'])}")
        else:
            tally["exposure count"][0] += 1
            tally["exposure count"][1] += 1
            for i, (ea, eb) in enumerate(zip(ra["exposures"], rb["exposures"])):
                for leaf in NUMERIC_LEAVES:
                    cmp(f"exposures[{i}].{leaf}", get_path(ea, leaf), get_path(eb, leaf), "exposure numeric")
                if not args.numeric_only:
                    for leaf in EXPO_CAT:
                        cmp(f"exposures[{i}].{leaf}", get_path(ea, leaf), get_path(eb, leaf), "exposure categorical")
        if args.write_second_pass:
            ra["provenance"]["second_pass"] = second
            fa.write_text(json.dumps(ra, indent=1, ensure_ascii=False) + "\n")

    print(f"{n_papers} papers compared\n")
    for g, (ok, n) in tally.items():
        print(f"  {g:22s} {ok:4d}/{n:<4d} {100 * ok / n:5.1f}%" if n else f"  {g}: n/a")
    if disagreements:
        print("\ndisagreements:")
        print("\n".join(disagreements))
    return 0


if __name__ == "__main__":
    sys.exit(main())
