#!/usr/bin/env python3
"""Add the `derived` block to each exposure (SCHEMA.md section 5). Never extracted, always
recomputed, so it can be regenerated from the stored fields at any time.

Per exposure:
  duty_cycle_computed_pct   pulse_duration_ms * PRF / 10, where both are single numbers
  duty_cycle_mismatch       stated and computed differ by more than 5 % relative
  checks[]                  pd_exceeds_period, f0_out_of_range, intensity_pressure_inconsistent
  not_reported[]            the numeric leaves the paper did not state
  field_status{}            per numeric leaf: quote_verified | second_pass | tiebreak | flagged
                            second_pass is "agree" when two reads support the value: the first and second
                            read agreed, or a tie-break ruled and the value was set accordingly

  uv run scripts/derive.py                 # all of data/papers, in place
  uv run scripts/derive.py data/papers/2022zeng.json
"""

from __future__ import annotations

import json
import pathlib
import sys

from common import DATA, read_json
from validate import NUMERIC_LEAVES, alnum, get_path, is_number, quote_in_text, text_for

PAPERS = DATA / "papers"
RHO_C = 1.5e6  # Pa·s/m, water/soft tissue; I[W/m2] = p^2 / (2 rho c)

DEVICE_LEAVES = ["device.manufacturer", "device.model"]


def status_of(second: dict, tiebreak: dict, key: str):
    """Two reads behind the value -> "agree"; an unresolved dispute -> "disagree"; else None."""
    tb = tiebreak.get(key)
    if tb in ("first", "second", "neither"):
        return "agree"
    if tb in ("both_defensible", "unresolvable"):
        return "disagree"
    return second.get(key)


def scalar(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def as_list(v) -> list[float]:
    if isinstance(v, list):
        return [x for x in v if isinstance(x, (int, float))]
    return [v] if scalar(v) is not None else []


def apply(record: dict, manifest: dict) -> None:
    text = text_for(record["citekey"], manifest)
    text_alnum = alnum(text) if text else None
    quotes = record["provenance"].get("source_quotes", {})
    second = record["provenance"].get("second_pass", {})
    tiebreak = record["provenance"].get("tiebreak", {})
    flagged = {f["field"] for f in record["provenance"].get("flags", [])}

    for i, ex in enumerate(record["exposures"]):
        t = ex["timing"]
        d: dict = {"checks": [], "not_reported": [], "field_status": {}}

        pd, prf, dc = scalar(t["pulse_duration_ms"]), scalar(t["pulse_repetition_frequency_hz"]), scalar(t["duty_cycle_pct"])
        if pd is not None and prf is not None:
            d["duty_cycle_computed_pct"] = round(pd * prf / 10, 3)
            if dc is not None and dc > 0:
                d["duty_cycle_mismatch"] = abs(d["duty_cycle_computed_pct"] - dc) / dc > 0.05
            if pd > 1000 / prf * 1.0001:
                d["checks"].append("pd_exceeds_period")
        elif dc is not None and prf is not None and pd is None and t["pulse_duration_ms"] == "not_reported":
            d["pulse_duration_implied_ms"] = round(dc * 10 / prf, 4)

        for f0 in as_list(ex["fundamental_frequency_khz"]):
            if not (20 <= f0 <= 20000):
                d["checks"].append("f0_out_of_range")
                break

        for block in ("free_field", "in_situ", "unspecified_domain"):
            p = scalar(ex[block]["pressure_kpa"])
            isppa = scalar(ex[block]["isppa_w_cm2"])
            if p is not None and isppa is not None and p > 0:
                predicted = (p * 1e3) ** 2 / (2 * RHO_C) / 1e4
                if not (0.5 <= predicted / isppa <= 2.0):
                    d["checks"].append(f"intensity_pressure_inconsistent_{block}")

        for leaf in NUMERIC_LEAVES:
            v = get_path(ex, leaf)
            key = f"exposures[{i}].{leaf}"
            if v == "not_reported":
                d["not_reported"].append(leaf)
            if is_number(v):
                q = quotes.get(key)
                d["field_status"][leaf] = {
                    "quote_verified": bool(q) and (text_alnum is None or quote_in_text(q, text_alnum)),
                    "second_pass": status_of(second, tiebreak, key),
                    "tiebreak": tiebreak.get(key),
                    "flagged": key in flagged,
                }
        # device strings: a quote is optional (the extractor quotes numbers), so status is recorded
        # only when a quote exists; the paper page then offers "source" and the verified tick
        for leaf in DEVICE_LEAVES:
            v = get_path(ex, leaf)
            key = f"exposures[{i}].{leaf}"
            q = quotes.get(key)
            if isinstance(v, str) and v != "not_reported" and q:
                d["field_status"][leaf] = {
                    "quote_verified": text_alnum is None or quote_in_text(q, text_alnum),
                    "second_pass": status_of(second, tiebreak, key),
                    "tiebreak": tiebreak.get(key),
                    "flagged": key in flagged,
                }
        ex["derived"] = d


def main() -> int:
    manifest = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})
    files = [pathlib.Path(f) for f in sys.argv[1:]] or sorted(PAPERS.glob("*.json"))
    for f in files:
        rec = json.loads(f.read_text())
        apply(rec, manifest)
        f.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
    print(f"derived {len(files)} records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
