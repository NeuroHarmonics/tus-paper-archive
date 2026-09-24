#!/usr/bin/env python3
"""Prepare the tie-break: a third read of every field on which the first and second reads disagree.

For each record with at least --min disagreements in provenance.second_pass, writes a case file
giving the paper's text path, the exposure structure, and for each disputed field both reads'
values and quotes (the second read's records are in --second). Batch files list citekeys, packed
so that each holds about --fields disputed fields. docs/tiebreak-prompt.md is rendered from the
template below.

  uv run scripts/build_tiebreak.py --second <dir with second-read records> --out <scratch>/tiebreak
  uv run scripts/build_tiebreak.py --second ... --out ... --min 10
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from common import DATA, read_json
from validate import get_path

PAPERS = DATA / "papers"
ROOT = DATA.parent
OUT_PROMPT = ROOT / "docs" / "tiebreak-prompt.md"

TEMPLATE = r"""# The tie-break prompt

The prompt for the third read. Rendered by `scripts/build_tiebreak.py`; edit the template in that script, not this file. The third reader is given the paper and, for each field on which the first and second reads disagree, both values and both quotes. It rules on each field; `scripts/apply_tiebreak.py` then writes the ruling into the record and corrects the value where the ruling says so. Copy verbatim, substituting `<BATCH>`, `<CASES>` and `<OUT_DIR>`.

---

```
Working directory: <REPO>

You are the third reader for the TUS Paper Archive. Two models have each extracted the numeric fields of a paper independently and disagree on some of them. You read the paper and rule on each disputed field. Your ruling is applied by a script; you never edit any record, and you never open anything under data/papers.

Papers to adjudicate are listed one citekey per line in: <BATCH>
For each citekey there is a case file: <CASES>/<citekey>.json
It gives the paper's full-text path, the exposure structure (index, label, target, waveform), and for every disputed field: the path, the first read's value and quote, and the second read's value, quote and any flag it raised.

For each paper, read the full text first, then work through every disputed field and write ONE file: <OUT_DIR>/<citekey>.json

===================== FIELD DEFINITIONS (the same rules both readers followed) =====================
Three states: a number or list of numbers (the paper states it) | "not_reported" (the field applies but the paper is silent) | null (the field does not apply). Never a typical value, a datasheet value, or arithmetic of your own; a stated period converts to a PRF and MPa converts to kPa, but a duty cycle is never derived from two other numbers.
Units: kHz, ms, Hz, %, s, kPa, W/cm². A range is a two-element list [min, max]; a mean ± SD is the mean; a parameter that varies within one exposure is a list.
Timing: pulse_duration_ms, pulse_repetition_frequency_hz, duty_cycle_pct, sonication_duration_s as the paper STATES them. The burst rule: when duty cycle and PRF are stated, pulse_duration = duty_cycle / PRF tells you which duration the paper calls a burst. Continuous wave: pulse duration and PRF null, duty cycle 100 or null, sonication_duration_s = the length of one uninterrupted burst. pulse_duration may be taken from "N cycles at f0".
Domains: free_field = measured or specified in water; in_situ = stated as in the brain or at the target (simulated, derated, measured through skull); unspecified_domain = the paper gives the value without saying where it applies. unspecified_domain fields are null unless a value is placed there. For a bath or dish with no tissue path, every numeric in_situ field is null.
n_subjects = total subjects exposed to ultrasound; if only group sizes are stated, the list of exposed group sizes, not summed. n_sessions_per_subject = ultrasound sessions each subject received as stated; "not_reported" if silent; null for tissue and cell work.

===================== HOW TO RULE =====================
For each disputed field give exactly one verdict:
  "first"            the first read is right and the second is wrong
  "second"           the second read is right and the first is wrong
  "both_defensible"  the paper supports either reading (a genuine ambiguity, or two equally valid conventions)
  "neither"          both are wrong; give the value you would record, with its quote
  "unresolvable"     the paper does not let anyone decide (e.g. the number exists only in a figure or supplement)
Then: "quote" (one verbatim sentence or table row from the text that decides it; copy it by splitting the .md on "\n" and slicing, never retype; null only for "unresolvable"), "reason" (one sentence), "severity": "high" (a wrong number, a value in the wrong domain block, a group size recorded as a total, a pulse recorded as a train) | "medium" (a missed value that the paper does state, a list-vs-scalar difference that loses information) | "low" (rounding, an arguable convention, applicability of null vs not_reported).
Rule on the reading, not on the reader: a "not_reported" against a value is right when the paper genuinely does not state it and wrong when it does.

===================== OUTPUT =====================
{
  "citekey": "...",
  "systematic_cause": "one or two sentences: what drove most of the disagreements in this paper, or null",
  "rulings": [
    {"path": "exposures[0].timing.pulse_duration_ms", "verdict": "second", "correct_value": 0.5, "quote": "...", "reason": "...", "severity": "high"},
    ...
  ]
}
correct_value is the value you would record (for "first"/"second" it repeats that read's value; for "neither" it is yours; for "both_defensible"/"unresolvable" it may be null).
Every disputed path in the case file needs exactly one ruling. When all files are written, run once:
  uv run scripts/check_tiebreak.py --batch <BATCH> --cases <CASES> --dir <OUT_DIR>
Fix what it reports and run it once more at most. Keep any helper scripts under <OUT_DIR>/_scratch/<batch name>/. Reply only with the list of files written.
```
"""


def value_at(rec: dict, path: str):
    if path.startswith("exposures["):
        i = int(path[10:path.index("]")])
        return get_path(rec["exposures"][i], path[path.index("].") + 2:])
    return rec.get(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--second", required=True, help="directory of second-read records")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min", type=int, default=1, help="minimum disagreements for a paper to be included")
    ap.add_argument("--fields", type=int, default=15, help="target disputed fields per batch")
    ap.add_argument("--citekeys", nargs="*")
    args = ap.parse_args()

    manifest = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})
    out = pathlib.Path(args.out)
    (out / "cases").mkdir(parents=True, exist_ok=True)
    (out / "verdicts").mkdir(parents=True, exist_ok=True)
    for old in out.glob("batch_*.txt"):
        old.unlink()

    cases = []
    for f in sorted(PAPERS.glob("*.json")):
        a = json.loads(f.read_text())
        ck = a["citekey"]
        if args.citekeys and ck not in args.citekeys:
            continue
        done = a["provenance"].get("tiebreak") or {}
        paths = [p for p, v in (a["provenance"].get("second_pass") or {}).items() if v == "disagree" and p not in done]
        if len(paths) < args.min or not paths:
            continue
        bf = pathlib.Path(args.second) / f"{ck}.json"
        if not bf.exists():
            print(f"no second-read record for {ck}", file=sys.stderr)
            continue
        b = json.loads(bf.read_text())
        pdf = (manifest.get(ck, {}).get("files") or {}).get("pdf")
        tp = (DATA / "text" / (pathlib.Path(pdf).stem + ".md")).resolve() if pdf else None
        case = {
            "citekey": ck, "text_path": str(tp),
            "exposures": [{"index": i, "label": e.get("label"), "target": e["target"].get("label"), "waveform": e["timing"]["waveform"]}
                          for i, e in enumerate(a["exposures"])],
            "disputed": [],
        }
        for p in paths:
            case["disputed"].append({
                "path": p,
                "first": {"value": value_at(a, p), "quote": a["provenance"]["source_quotes"].get(p)},
                "second": {"value": value_at(b, p), "quote": b["provenance"]["source_quotes"].get(p),
                           "flags": [fl["reason"] for fl in b["provenance"].get("flags", []) if fl.get("field") == p]},
            })
        (out / "cases" / f"{ck}.json").write_text(json.dumps(case, indent=1, ensure_ascii=False))
        cases.append((ck, len(paths)))

    # pack batches to roughly --fields disputed fields each, largest papers first
    cases.sort(key=lambda c: -c[1])
    batches: list[list[str]] = []
    load: list[int] = []
    for ck, n in cases:
        for i, l in enumerate(load):
            if l + n <= args.fields:
                batches[i].append(ck); load[i] += n
                break
        else:
            batches.append([ck]); load.append(n)
    for i, b in enumerate(batches):
        (out / f"batch_{i + 1:03d}.txt").write_text("\n".join(b) + "\n")
    OUT_PROMPT.write_text(TEMPLATE.lstrip("\n"))
    print(f"{len(cases)} papers, {sum(n for _, n in cases)} disputed fields, {len(batches)} batches -> {out}; prompt -> {OUT_PROMPT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
