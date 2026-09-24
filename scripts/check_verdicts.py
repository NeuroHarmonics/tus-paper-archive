#!/usr/bin/env python3
"""Validate a screener's output against the batch it was given, before it counts.

A screening wave is the one place in this pipeline where a model writes directly
into the permanent record, so its output is checked rather than trusted. The
failures this catches:

  - extra or missing keys, from an agent working to a stale output contract;
  - a verdict with no `doi`, which names no paper and cannot be applied;
  - fewer verdicts than the batch has papers, with nothing to say which were
    dropped, or verdicts for papers that are not in the batch at all.

Exits non-zero if any file fails, so a wave cannot be applied on the assumption
that it worked.

  uv run scripts/check_verdicts.py --batches DIR --verdicts <scratch>/verdicts_w1_*.json
  uv run scripts/check_verdicts.py --batches DIR --verdicts <scratch>/verdicts_w1_*.json --apply

With --apply, every file that passes is recorded in data/screening.jsonl under its own
name as the run; nothing is recorded if any file fails.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from common import record_screening

REQUIRED = {"doi", "verdict", "reason"}
VERDICTS = {"relevant", "not_relevant", "unspecified"}


def batch_dois(path: Path) -> list[str]:
    return re.findall(r"^## (10\.\S+)\s*$", path.read_text(), re.M)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", required=True, help="directory of cand_*.md")
    ap.add_argument("--verdicts", nargs="+", help="verdict json files")
    ap.add_argument("--apply", action="store_true",
                    help="record the verdicts of every passing file in data/screening.jsonl")
    ap.add_argument("--fix", action="store_true",
                    help="relabel E1 verdicts whose own reason says ultrasound was "
                         "applied for imaging: they are E4, not E1")
    args = ap.parse_args()

    bdir = Path(args.batches)
    ok_files, bad_files = [], []

    for vf in sorted(Path(p) for p in args.verdicts):
        # cand_3.md <-> verdicts_w1_3.json
        m = re.search(r"(\d+)(?!.*\d)", vf.stem)
        batch = bdir / f"cand_{m.group(1)}.md" if m else None
        problems, notes = [], []
        try:
            rows = json.loads(vf.read_text())
        except Exception as e:
            print(f"FAIL {vf.name}: unreadable ({type(e).__name__})")
            bad_files.append(vf)
            continue
        if not isinstance(rows, list):
            problems.append("not a JSON array")
            rows = []

        missing_fields = sorted({f for r in rows if isinstance(r, dict)
                                 for f in REQUIRED - set(r)})
        if missing_fields:
            problems.append(f"every record missing: {missing_fields}"
                            if all(REQUIRED - set(r) for r in rows if isinstance(r, dict))
                            else f"some records missing: {missing_fields}")
        # E1 needs care, because the word means two different things.
        # prescreen.py's E1 is TEXTUAL: the title and abstract never say
        # "ultrasound", so there is nothing to screen. A screener's E1 is
        # EVIDENTIAL: the paper discusses ultrasound and applies none -- a
        # chemogenetics study in an ultrasound journal, an MRI segmentation
        # paper. Those are correct E1s and must not be rejected.
        #
        # The real error mode is narrower: 56 exclusions in wave 2 cited E1 for
        # ultrasound IMAGING papers -- fetal lung, cortical bone, prostate --
        # where ultrasound plainly was applied and the failure is intent (E4) or
        # target (E2). Those say so in their own reason text, so that is what is
        # checked. A file is not failed for an E1 whose reason is consistent.
        IMAGING = re.compile(r"imag|diagnos|detect|sonograph|elastograph|B-mode"
                             r"|screening ultrasound|measur\w* .{0,20}thickness", re.I)
        e1 = [r for r in rows if isinstance(r, dict) and r.get("criterion") == "E1"]
        mislabelled = [r for r in e1 if IMAGING.search(r.get("reason") or "")]
        if mislabelled and args.fix:
            # Repaired in code because it has recurred in four consecutive waves
            # despite the prompt naming it explicitly. The condition is exact --
            # criterion E1 and a reason that itself describes imaging -- so there
            # is nothing to judge, and doing it by hand four times was the error.
            for r in mislabelled:
                r["criterion"] = "E4"
                r["reason"] = (r.get("reason", "").rstrip(".") +
                               ". (Relabelled from E1: ultrasound WAS applied, as "
                               "the imaging modality, so the failure is intent, "
                               "not absence.)")
            vf.write_text(json.dumps(rows, indent=2))
            notes.append(f"{len(mislabelled)} E1 verdicts relabelled to E4 (--fix)")
        elif mislabelled:
            problems.append(
                f"{len(mislabelled)} E1 verdicts describe ultrasound imaging, so "
                f"ultrasound WAS applied -- these are E4 (intent) or E2 (target). "
                f"Re-run with --fix to relabel them.")
        elif e1:
            notes.append(f"{len(e1)} E1 verdict(s) -- ultrasound discussed but not "
                         f"applied; check the reason says so")

        bad_verdict = {r.get("verdict") for r in rows
                       if isinstance(r, dict) and r.get("verdict") not in VERDICTS}
        if bad_verdict:
            problems.append(f"bad verdict values: {sorted(map(str, bad_verdict))}")

        if batch and batch.exists():
            want = batch_dois(batch)
            got = [r.get("doi", "").lower() for r in rows if isinstance(r, dict)]
            wl = {d.lower() for d in want}
            gl = set(got)
            if len(rows) != len(want):
                problems.append(f"{len(rows)} verdicts for {len(want)} papers")
            # A DOI the screener re-typed rather than copied. Real case:
            # "10.1186/s13089-016-0044-x" came back as
            # "10.1186/s13089-2016-0044-x" -- the year part expanded. The verdict
            # is sound; only the key is wrong. Repaired ONLY when exactly one
            # unjudged batch DOI is a near match, so a genuinely invented DOI
            # still fails.
            if args.fix and (gl - wl):
                import difflib
                fixed = 0
                for r in rows:
                    d = (r.get("doi") or "").lower()
                    if d in wl or not d:
                        continue
                    unjudged = [w for w in want if w.lower() not in gl]
                    m = difflib.get_close_matches(d, [u.lower() for u in unjudged],
                                                  n=2, cutoff=0.95)
                    if len(m) == 1:
                        r["doi"] = next(u for u in unjudged if u.lower() == m[0])
                        gl = {x.get("doi", "").lower() for x in rows}
                        fixed += 1
                if fixed:
                    vf.write_text(json.dumps(rows, indent=2))
                    notes.append(f"{fixed} mistyped DOI(s) matched back to the batch "
                                 f"(--fix)")
                    got = [x.get("doi", "").lower() for x in rows]
                    gl = set(got)

            if missed := wl - gl:
                problems.append(f"{len(missed)} papers never judged, e.g. "
                                f"{sorted(missed)[:2]}")
            if extra := gl - wl - {""}:
                problems.append(f"{len(extra)} verdicts for papers not in the batch")
            if len(got) != len(gl):
                problems.append("duplicate DOIs in the output")
        elif batch:
            problems.append(f"batch file {batch.name} not found")

        if problems:
            bad_files.append(vf)
            print(f"FAIL {vf.name}")
            for p in problems:
                print(f"       {p}")
        else:
            ok_files.append(vf)
            print(f"ok   {vf.name}  ({len(rows)} verdicts)")
            for n in notes:
                print(f"       note: {n}")

    print(f"\n{len(ok_files)} usable, {len(bad_files)} rejected")
    if bad_files:
        print("Rejected files must be re-run. Do not apply a wave that did not "
              "fully validate.")
        return 1
    if args.apply:
        added = replaced = 0
        for vf in ok_files:
            a, r = record_screening(json.loads(vf.read_text()), run=vf.stem)
            added += a
            replaced += r
        print(f"recorded {added} verdicts in data/screening.jsonl" + (f", {replaced} replaced earlier verdicts" if replaced else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
