#!/usr/bin/env python3
"""Build screening batches from the sweep, stratified by publication year.

Why stratified. The first pass ranked candidates purely by citations per year
and screened the top 600. That structurally starves recent work -- a 2026 paper
has almost no citations whatever its quality -- and the result was visible in the
library: ~28-35% coverage for 2019-2024 but 8-9% for 2025-2026, exactly the years
a living archive most needs. The field roughly doubled in 2025 (OpenAlex: 220
papers in 2024, 419 in 2025), so citation-ranked acquisition falls further behind
every year.

So: allocate each year a share of the batch proportional to how many unscreened
candidates it has, and rank by impact only WITHIN a year. Recent years then get
screened at the same rate as old ones.

  uv run scripts/build_batches.py --out <dir> [--batches 5] [--per-batch 120]
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import sys
from pathlib import Path

from common import load_screening, DATA, read_json

# Does this look like TUS work at all? A topical filter, not a verdict -- the
# screener decides, this only chooses what is worth a model call.
_TUS_PHRASE = re.compile(
    r"neuromodulat|neurostimulat|brain stimulat|transcranial ultraso"
    r"|focused ultrasound.*(brain|neur|cortex|thalam)|lifu|tfus\b|\btus\b", re.I)

# A specific neural target or preparation. Deliberately narrower than
# prescreen.NEURAL, which includes "stimulation" and "pain" and would match
# almost anything paired with the word ultrasound.
_NEURAL_TARGET = re.compile(
    r"neuron|cortex|cortical|thalam|hippocamp|amygdal|striat|cerebell|\bbrain\b"
    r"|nerve|neural|microglia|astrocyt|\bglia|spinal|retina|vagus|ganglion|synap"
    r"|axon|myelin|\bDRG\b|neurit", re.I)

_ULTRASOUND = re.compile(
    r"ultraso(?:und|nic|nograph)|sonicat|\bLIFU\b|\btFUS\b|\bFUS\b|\bLIPUS\b", re.I)


class _TusLike:
    """Matches a TUS phrase, OR an ultrasound term co-occurring with a neural target.

    The second arm exists because the phrase list alone was too narrow and was
    dropping papers silently. A random sample of 140 candidates it had rejected
    contained one genuine study -- "Ultrasound Stimulation Suppresses LPS-Induced
    Proinflammatory Responses" in microglia, which no phrase matched because
    "microglia" was not in the target vocabulary. Widening on that evidence
    recovers 1,233 candidates, among them "Ultrasound Concave 2D Ring Array for
    Retinal Stimulation" and "Auricular Ultrasonic Vagus Nerve Stimulation" --
    unambiguously in scope and never queued.

    Precision drops: nerve ultrasonography now reaches the screener and is
    excluded there as E4. That is the right trade. A filter that silently drops
    in-scope papers is a recall bug; one that passes out-of-scope papers to a
    screener only costs a model call.
    """

    def search(self, text: str):
        if (m := _TUS_PHRASE.search(text)):
            return m
        if _ULTRASOUND.search(text) and (m := _NEURAL_TARGET.search(text)):
            return m
        return None


TUS_LIKE = _TusLike()


def already_decided() -> set[str]:
    """Every DOI we hold, excluded, or have a verdict for."""
    out: set[str] = set()
    for p in (read_json(DATA / "manifest.json", {}) or {}).get("papers", {}).values():
        if p.get("doi"):
            out.add(p["doi"])
    for p in (read_json(DATA / "excluded.json", {}) or {}).get("papers", []):
        if p.get("doi"):
            out.add(p["doi"])
    out |= set(load_screening())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--batches", type=int, default=5)
    ap.add_argument("--per-batch", type=int, default=120)
    ap.add_argument("--years", help="comma-separated publication years; queue every TUS-like candidate "
                    "from these years instead of stratifying across all years (e.g. 2025,2026)")
    ap.add_argument("--all-topics", action="store_true",
                    help="with --years: also queue candidates the TUS_LIKE topical filter would defer, "
                    "so a year can be closed out completely")
    args = ap.parse_args()
    only_years = {int(y) for y in args.years.split(",")} if args.years else None

    sw = (read_json(DATA / "sweep-candidates.json", {}) or {}).get("candidates", {})
    if not sw:
        sys.exit("Run scripts/sweep.py first.")
    decided = already_decided()

    # OpenAlex sometimes attaches the WRONG abstract to conference-supplement
    # records: 728 candidates in this harvest share a duplicated abstract, one
    # repeated 13 times, including a TMS-titled record carrying a tUS abstract.
    # Screening on a wrong abstract yields a wrong verdict, so mark duplicates
    # as unreliable and let the screener fall back to the title (and to
    # `unspecified` when the title does not settle it).
    seen_abs: dict[str, int] = collections.Counter(
        v["abstract"][:200] for v in sw.values() if v.get("abstract"))

    pool = []
    not_tus_like = no_abstract = 0
    for doi, v in sw.items():
        if doi in decided or not v.get("title"):
            continue
        if not v.get("abstract"):
            no_abstract += 1
            continue
        if (v.get("journal") or "") in ("Figshare",
                                        "Zenodo (CERN European Organization for Nuclear Research)"):
            continue
        if not TUS_LIKE.search((v["title"] + " " + v["abstract"])[:1200]):
            if not (args.all_topics and only_years and (v.get("year") or 0) in only_years):
                not_tus_like += 1
                continue
        pool.append((v.get("year") or 0, v.get("cited_by") or 0, doi, v))

    # Never let a filter be silent. This one is topical, not a verdict: a
    # candidate that is undecided and NOT queued here has had nothing recorded
    # about it, which reads exactly like a finished queue. prescreen.py now
    # decides the clear-cut part of this population (E2, nothing neural named);
    # whatever is left is a real deferral and has to be said out loud.
    if not_tus_like:
        print(f"NOT QUEUED: {not_tus_like} undecided candidates do not match the "
              f"TUS_LIKE topical filter.")
        print("  These are deferred, not decided. Run prescreen.py first -- it "
              "records a verdict for the clear-cut ones.")
    if no_abstract:
        print(f"NOT QUEUED: {no_abstract} undecided candidates have no abstract "
              f"and cannot be screened on a title alone.")

    want = args.batches * args.per_batch
    by_year: dict[int, list] = collections.defaultdict(list)
    for yr, cites, doi, v in pool:
        by_year[yr].append((cites, doi, v))
    for rows in by_year.values():
        rows.sort(key=lambda r: -r[0])          # impact rank WITHIN the year
    if only_years:
        # A year-targeted wave: take the whole pool for those years, as many batches as it needs.
        by_year = {y: rows for y, rows in by_year.items() if y in only_years}
        pool = [r for r in pool if r[0] in only_years]
        want = len(pool)
        args.batches = -(-want // args.per_batch)

    # Proportional allocation, largest-remainder, so no year is starved.
    total = len(pool)
    quota = {y: len(rows) * want / total for y, rows in by_year.items()}
    take = {y: int(q) for y, q in quota.items()}
    for y in sorted(quota, key=lambda y: -(quota[y] - take[y])):
        if sum(take.values()) >= want:
            break
        take[y] += 1

    selected = []
    for y, n in take.items():
        selected += [(y, *r) for r in by_year[y][:n]]
    selected.sort(key=lambda s: (-s[0], -s[1]))   # newest first, then impact

    args.out.mkdir(parents=True, exist_ok=True)
    for b in range(args.batches):
        chunk = selected[b * args.per_batch:(b + 1) * args.per_batch]
        if not chunk:
            break
        with (args.out / f"cand_{b + 1}.md").open("w", encoding="utf-8") as fh:
            for yr, cites, doi, v in chunk:
                dup = seen_abs.get(v["abstract"][:200], 0) > 1
                warn = ("\nABSTRACT UNRELIABLE: this abstract text is duplicated across "
                        "several records in the source data and may belong to a "
                        "different paper. Screen on the TITLE; return `unspecified` "
                        "if the title alone does not settle it."
                        if dup else "")
                fh.write(f"## {doi}\ncited_as: {yr}\ntitle: {v['title']}\n"
                         f"year: {yr} | journal: {v.get('journal')} | "
                         f"type: {v.get('type')}{warn}\n"
                         f"abstract: {v['abstract'][:1100]}\n\n")
        print(f"  cand_{b + 1}.md: {len(chunk)}")

    print(f"\npool of unscreened TUS-like candidates: {total}")
    print(f"selected {len(selected)} across {args.batches} batches, stratified by year")
    print("\nyear   pool   selected")
    for y in sorted(by_year, reverse=True)[:10]:
        print(f"  {y}   {len(by_year[y]):4d}   {take.get(y, 0):4d}")
    remaining = total - len(selected)
    print(f"\n{remaining} candidates remain unscreened after this round.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
