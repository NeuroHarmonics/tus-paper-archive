#!/usr/bin/env python3
"""Tally the free text behind every `other` across the corpus (SCHEMA.md section 10).

Anything that recurs in three or more papers is a candidate for promotion to a term. Promotion
is a schema patch plus a rule here, applied by string match on the preserved free text; no
re-extraction.

  uv run scripts/vocab_report.py            # print the report
  uv run scripts/vocab_report.py --min 2
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict

from common import DATA

PAPERS = DATA / "papers"


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=3)
    args = ap.parse_args()

    tallies: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for f in sorted(PAPERS.glob("*.json")):
        r = json.loads(f.read_text())
        ck = r["citekey"]
        if "other" in r["conditions"] and r.get("condition_other"):
            k = norm(r["condition_other"]); tallies["conditions"][k] += 1; examples["conditions"][k].add(ck)
        if "other" in r["readouts"] and r.get("readout_other"):
            k = norm(r["readout_other"]); tallies["readouts"][k] += 1; examples["readouts"][k].add(ck)
        for ex in r["exposures"]:
            if "other" in ex["target"]["terms"] and isinstance(ex["target"]["label"], str):
                k = norm(ex["target"]["label"]); tallies["target"][k] += 1; examples["target"][k].add(ck)
            if ex["device"]["family"] in ("other_manufacturer", "custom_built") and isinstance(ex["device"]["manufacturer"], str):
                k = norm(ex["device"]["manufacturer"]); tallies["device.manufacturer"][k] += 1; examples["device.manufacturer"][k].add(ck)
        for field in ("sham_type", "auditory_control"):
            if "other" in r[field]:
                tallies[field]["(other; see notes)"] += 1; examples[field]["(other; see notes)"].add(ck)

    for field, c in tallies.items():
        # count PAPERS, not exposures: a paper with four exposures against one label is one paper
        c = Counter({k: len(examples[field][k]) for k in c})
        promote = [(k, n) for k, n in c.most_common() if n >= args.min]
        tail = [(k, n) for k, n in c.most_common() if n < args.min]
        print(f"\n== {field}: {sum(c.values())} papers with 'other' across {len(c)} distinct texts")
        if promote:
            print(f"   candidates for promotion (>= {args.min} papers):")
            for k, n in promote:
                print(f"     {n:3d}  {k}   e.g. {', '.join(sorted(examples[field][k])[:4])}")
        if tail:
            print(f"   long tail ({len(tail)}): " + "; ".join(f"{k} ({n})" for k, n in tail[:25]) + (" …" if len(tail) > 25 else ""))
    if not tallies:
        print("no records yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
