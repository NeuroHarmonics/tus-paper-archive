#!/usr/bin/env python3
"""Flag included papers whose title advertises an excluded application.

A backstop against the failure this catches for real: the screener matched I1a
("ultrasound applied to neural tissue" -- which MRgFUS thalamotomy literally is)
and never applied E3. Fifteen ablation, BBB-opening and nanoparticle papers
reached the want-list, and four reached the library.

Deterministic and cheap, so it can run on every ingest and every monthly sweep.
It does not decide anything -- it forces a second look, because a model that has
already answered "relevant" will not spontaneously reconsider.

  uv run scripts/audit_scope.py                   # audit manifest + want-list
  uv run scripts/audit_scope.py --json out.json   # machine-readable
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys

from common import DATA, read_json

# Each pattern names an INCLUSION.md rule. Title-level evidence only: strong
# enough to demand review, never strong enough to decide on its own (E6/E7-note).
SIGNALS: list[tuple[str, str, str]] = [
    ("E4", "MRgFUS / ablation / functional neurosurgery",
     r"\bMRgFUS\b|MR[- ]guided focused ultrasound"
     # "magnetic resonance-guided" spelled out was missing, so
     # "Transcranial magnetic resonance-guided focused ultrasound for temporal
     # lobe epilepsy" passed the audit twice after it had already been ruled
     # out of scope by title.
     r"|magnetic resonance[- ]guided"
     r"|thalamotom|capsulotom|pallidotom"
     r"|subthalamotom|\bablat|incisionless|lesioning|functional neurosurgery"
     r"|high[- ‐]?intensity focused ultrasound|\bHIFU\b"),
    ("E4", "blood-brain barrier opening",
     r"blood[\s–-]?brain barrier|\bBBB\b|barrier opening|barrier disruption"),
    ("E4", "drug or gene delivery",
     r"drug deliver|gene deliver|drug release|sonoporation|therapeutic deliver"),
    ("E4", "histotripsy", r"histotrips|microtrips"),
    ("E4", "sonothrombolysis", r"thromboly|sonothromb|clot lysis"),
    ("E5", "imaging as the subject",
     r"elastograph|photoacoustic imag|functional ultrasound imag|\bfUSI\b"),
    ("E5", "transcranial pulse stimulation / shockwave (a different modality)",
     r"transcranial pulse stimulation|\bTPS\b|shock ?wave|lithotrips"),
    ("E3", "general NIBS review, not TUS-specific",
     r"non[- ]invasive brain stimulation(?!.*ultrasound)"
     r"|\b(TMS|tDCS|tACS|rTMS)\b.*\b(and|versus|vs)\b"
     r"|neuromodulation techniques|brain stimulation techniques"),
    ("E6", "engineered sensitisation (genetic or nanomaterial)",
     r"sonogenet|sonothermogenet|optogenet|piezoelectric nanoparticle"
     r"|mechanoluminescen|nanodroplet|activatable liposome|targeted microbubble"
     r"|nanoparticle[- ]?(assisted|mediated|modulated)"),
]


# Publishers set compound modifiers with en dashes, non-breaking hyphens and
# soft hyphens. "magnetic resonance-guided" in a Crossref title is very often
# "magnetic resonance\u2013guided", which no pattern written with an ASCII hyphen
# will ever match. Fold them all to "-" before scanning.
DASHES = str.maketrans({c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u00ad"})


def scan(title: str) -> list[tuple[str, str]]:
    t = (title or "").translate(DASHES)
    return [(rule, label) for rule, label, pat in SIGNALS
            if re.search(pat, t, re.I)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write findings to this path")
    args = ap.parse_args()

    findings = []

    man = read_json(DATA / "manifest.json", {}) or {}
    for ck, p in (man.get("papers") or {}).items():
        for rule, label in scan(p.get("title", "")):
            findings.append({
                "where": "library", "citekey": ck, "doi": p.get("doi"),
                "year": p.get("year"), "title": p.get("title"),
                "rule": rule, "signal": label,
                "screened_as": (p.get("screening") or {}).get("criterion"),
            })

    wanted = DATA / "wanted.csv"
    if wanted.exists():
        with wanted.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                for rule, label in scan(r.get("title", "")):
                    findings.append({
                        "where": "want-list", "rank": r.get("rank"),
                        "doi": r.get("doi"), "year": r.get("year"),
                        "title": r.get("title"), "rule": rule, "signal": label,
                    })

    # Papers reviewed against the bar and deliberately kept. Without this the
    # audit fires on the same known false positives forever and can never gate
    # the monthly run. Each entry records WHY, so the decision is auditable and
    # a future reader is not left guessing.
    ack = read_json(DATA / "scope-audit-acknowledged.json", {}) or {}
    new = [f for f in findings if f["doi"] not in ack]
    suppressed = len(findings) - len(new)
    if suppressed:
        print(f"({suppressed} flag(s) previously reviewed and kept -- see "
              f"data/scope-audit-acknowledged.json)\n")
    findings = new

    if not findings:
        print("No unreviewed paper advertises an excluded application. Clean.")
        return 0

    lib = [f for f in findings if f["where"] == "library"]
    want = [f for f in findings if f["where"] == "want-list"]
    print(f"{len(findings)} scope-audit flag(s): {len(lib)} in library, "
          f"{len(want)} in want-list\n")
    for group, rows in (("LIBRARY", lib), ("WANT-LIST", want)):
        if not rows:
            continue
        print(f"--- {group} ---")
        for f in sorted(rows, key=lambda x: (x["rule"], x.get("year") or 0)):
            where = f.get("citekey") or f"rank {f.get('rank')}"
            print(f"  [{f['rule']}] {where}  {f.get('year')}")
            print(f"       {f['signal']}")
            print(f"       {(f.get('title') or '')[:76]}")
        print()

    print("These are title-level signals, not verdicts. Re-screen each against")
    print("INCLUSION.md -- exclusions override inclusions.")

    if args.json:
        json.dump(findings, open(args.json, "w"), indent=2)
        print(f"\nwrote {args.json}")
    return 1   # non-zero: this should fail a monthly run until resolved


if __name__ == "__main__":
    sys.exit(main())
