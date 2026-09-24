#!/usr/bin/env python3
"""Write tie-break rulings into the records.

For every verdict file: provenance.tiebreak[path] = verdict. Where the verdict is "second" the
field takes the second read's value and quote (from the case file); where it is "neither" it takes
the third reader's value and quote. "first", "both_defensible" and "unresolvable" leave the value
alone. A number written without a quote that occurs in the text is refused. Records with
human_edited are never touched. Run derive.py and validate.py afterwards.

  uv run scripts/apply_tiebreak.py --cases <dir>/cases --verdicts <dir>/verdicts
  uv run scripts/apply_tiebreak.py --cases ... --verdicts ... --dry-run
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

from common import DATA, read_json
from validate import alnum, is_number, quote_in_text, set_path, text_for

PAPERS = DATA / "papers"


def set_at(rec: dict, path: str, value) -> None:
    if path.startswith("exposures["):
        i = int(path[10:path.index("]")])
        set_path(rec["exposures"][i], path[path.index("].") + 2:], value)
    else:
        rec[path] = value


def settle(rec: dict) -> None:
    """Two consequences a ruling has for the rest of its exposure. An in_situ block whose three
    numbers are all null does not apply, so its method and reported_as are null too. An exposure
    recorded as continuous wave that now carries a PRF, a pulse duration or a duty cycle other than
    100 is pulsed by the archive's definition."""
    for i, ex in enumerate(rec["exposures"]):
        s = ex["in_situ"]
        if all(s[l] is None for l in ("pressure_kpa", "isppa_w_cm2", "ispta_w_cm2")):
            s["method"] = None
            s["reported_as"] = None
        t = ex["timing"]
        wf = t["waveform"]
        cw = wf == "continuous" or wf == ["continuous"]
        gated = is_number(t["pulse_repetition_frequency_hz"]) or is_number(t["pulse_duration_ms"]) or (
            is_number(t["duty_cycle_pct"]) and t["duty_cycle_pct"] != 100)
        if cw and gated:
            t["waveform"] = ["pulsed"] if isinstance(wf, list) else "pulsed"
            rec["provenance"]["flags"].append({"field": f"exposures[{i}].timing.waveform",
                                               "reason": "set to pulsed by the tie-break: the ruled timing has a pulse repetition or duty cycle"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--verdicts", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    manifest = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})

    tally: Counter = Counter()
    changed_records = 0
    refused: list[str] = []
    for vf in sorted(pathlib.Path(args.verdicts).glob("*.json")):
        v = json.loads(vf.read_text())
        ck = v["citekey"]
        rf = PAPERS / f"{ck}.json"
        if not rf.exists():
            print(f"no record for {ck}", file=sys.stderr)
            continue
        rec = json.loads(rf.read_text())
        if rec["provenance"].get("human_edited"):
            print(f"skip {ck}: human_edited")
            continue
        case = json.loads((pathlib.Path(args.cases) / f"{ck}.json").read_text())
        second = {d["path"]: d["second"] for d in case["disputed"]}
        text = text_for(ck, manifest)
        ta = alnum(text) if text else None
        quotes = rec["provenance"]["source_quotes"]
        tb = rec["provenance"].setdefault("tiebreak", {})
        changed = False
        for r in v["rulings"]:
            path, verdict = r["path"], r["verdict"]
            tb[path] = verdict
            tally[verdict] += 1
            if verdict == "second":
                new, quote = second[path]["value"], second[path]["quote"]
            elif verdict == "neither":
                new, quote = r.get("correct_value"), r.get("quote")
            else:
                continue
            if is_number(new):
                if not quote or (ta is not None and not quote_in_text(quote, ta)):
                    refused.append(f"{ck} {path}: number without a quote in the text; left unchanged")
                    tally[verdict] -= 1
                    tally["refused"] += 1
                    tb[path] = "unresolvable"
                    continue
                quotes[path] = quote
            else:
                quotes.pop(path, None)
            set_at(rec, path, new)
            changed = True
            tally["corrected"] += 1
        if changed:
            settle(rec)
        if not args.dry_run:
            rf.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
        changed_records += changed
    for line in refused:
        print("  " + line)
    print(f"{'would change' if args.dry_run else 'changed'} {changed_records} records; rulings: " +
          ", ".join(f"{k} {n}" for k, n in tally.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
