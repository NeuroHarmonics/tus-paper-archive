#!/usr/bin/env python3
"""Compile the site data from data/papers, the manifest, the abstracts and the vocabularies.

Runs derive.py and validate.py first; refuses to build if any record fails. Writes into the
website repository checkout, public/tus-archive/data/ (committed there, so the website build
needs only Node; served at neuroharmonics.com/tus-archive/data/):

  index.json              one row per exposure, flattened for the table and facets; abstracts in
  papers/<citekey>.json   the full record + bibliography + abstract, for the paper page
  stats.json              papers per year, reportability per field, composition counts
  targets.json            the target vocabulary, for the tree facet
  tus-paper-archive-v1.json    the bulk download: all records + bibliography, NO abstracts

  uv run scripts/build_site.py                                # into $TUS_WEBSITE_REPO or ../website
  uv run scripts/build_site.py --out ~/Documents/Local-Repos/website/public/tus-archive/data
  uv run scripts/build_site.py --papers /tmp/pilot15/run1     # build from a scratch set
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import re
import subprocess
import sys
from collections import Counter, defaultdict

from common import DATA, WEBSITE_DATA_REL, read_json, website_repo
from validate import NUMERIC_LEAVES, get_path

ROOT = DATA.parent


def authors_short(authors: list[dict] | None) -> str:
    if not authors:
        return ""
    fam = [a.get("family") or a.get("name") or "" for a in authors]
    if len(fam) == 1:
        return fam[0]
    if len(fam) == 2:
        return f"{fam[0]} & {fam[1]}"
    return f"{fam[0]} et al."


def clean_text(s):
    """Crossref titles and journal names arrive with HTML entities and tags ("&amp;", "<i>")."""
    if not isinstance(s, str):
        return s
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split()) or None


def authors_all(authors: list[dict] | None) -> str:
    """Every author's name, for the search index: "Pooja Gaur; Kerriann M. Casey; ... Kim Butts Pauly"."""
    return "; ".join(" ".join(x for x in (a.get("given"), a.get("family") or a.get("name")) if x) for a in (authors or []))


def read_version() -> str:
    """The release version from the VERSION file at the repo root; 'unreleased' if absent."""
    p = ROOT / "VERSION"
    return p.read_text().strip() if p.exists() else "unreleased"


def num_summary(v):
    """min/max for range filters; None for not_reported/null."""
    if isinstance(v, list):
        xs = [x for x in v if isinstance(x, (int, float))]
        return {"min": min(xs), "max": max(xs), "n": len(xs)} if xs else None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return {"min": v, "max": v, "n": 1}
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", default=str(DATA / "papers"))
    ap.add_argument("--skip-validate", action="store_true")
    ap.add_argument("--out", type=pathlib.Path, help="output directory; default: <website repo>/public/tus-archive/data")
    args = ap.parse_args()
    papers_dir = pathlib.Path(args.papers)

    site_repo = website_repo()
    out = args.out or (site_repo / WEBSITE_DATA_REL if site_repo else None)
    if out is None:
        print("no --out given and no website checkout found: set TUS_WEBSITE_REPO to the website repository clone, "
              "or clone it beside this repo as ../website", file=sys.stderr)
        return 2

    recheck = set(((read_json(DATA / "scope-recheck.json", {}) or {}).get("papers") or {}).keys())
    files = [f for f in sorted(papers_dir.glob("*.json")) if f.stem not in recheck]
    if recheck:
        print(f"skipping {len(recheck)} records awaiting a scope ruling (data/scope-recheck.json)", file=sys.stderr)
    if not files:
        print("no records", file=sys.stderr)
        return 1
    if not args.skip_validate:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "derive.py"), *map(str, files)], capture_output=True, text=True)
        if r.returncode:
            print(r.stdout, r.stderr, file=sys.stderr)
            return 1
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate.py"), "--quiet", *map(str, files)], capture_output=True, text=True)
        if r.returncode:
            print(r.stdout, file=sys.stderr)
            print("refusing to build: records fail validation", file=sys.stderr)
            return 1

    manifest = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})
    targets = json.loads((ROOT / "schema" / "targets.json").read_text())
    tmap = {t["id"]: t for t in targets["terms"]}

    def ancestors(tid: str) -> list[str]:
        out = []
        p = tmap.get(tid, {}).get("parent")
        while p:
            out.append(p)
            p = tmap[p]["parent"]
        return out

    out.mkdir(parents=True, exist_ok=True)
    (out / "papers").mkdir(exist_ok=True)
    for old in (out / "papers").glob("*.json"):
        old.unlink()

    rows = []
    bulk = []
    per_year = Counter()
    nr_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # leaf -> [not_reported, applicable]
    comp = {"model_system": Counter(), "readouts": Counter(), "conditions": Counter(), "device_family": Counter(),
            "target_top": Counter(), "waveform": Counter(), "direction_of_effect": Counter(), "sham_type": Counter()}
    n_flags = 0
    # Subjects summed by unit over papers stating a number; a swept/list n is summed. Reported alongside
    # how many papers state no n, so the total is never read as complete.
    subjects = {"participant": 0, "animal": 0, "preparation": 0, "culture": 0}
    subjects_papers = Counter()
    subjects_not_reported = 0

    for f in files:
        rec = json.loads(f.read_text())
        ck = rec["citekey"]
        man = manifest.get(ck, {})
        abstract = read_json(DATA / "abstracts" / f"{ck}.json", {}) or {}
        biblio = {
            "title": clean_text(man.get("title")), "authors": man.get("authors") or [], "authors_short": authors_short(man.get("authors")),
            "authors_all": authors_all(man.get("authors")),
            "year": man.get("year"), "journal": clean_text(man.get("journal")), "volume": man.get("volume"), "pages": man.get("pages"),
            "doi": rec["doi"], "record_class": man.get("record_class"),
        }
        per_year[man.get("year")] += 1
        n_flags += len(rec["provenance"]["flags"])
        n_subj = rec["n_subjects"]
        if n_subj == "not_reported":
            subjects_not_reported += 1
        else:
            total = sum(x for x in n_subj if isinstance(x, (int, float))) if isinstance(n_subj, list) else n_subj
            if isinstance(total, (int, float)) and not isinstance(total, bool):
                subjects[rec["subject_unit"]] = subjects.get(rec["subject_unit"], 0) + total
                subjects_papers[rec["subject_unit"]] += 1
        for k in ("model_system", "readouts", "conditions", "sham_type"):
            for v in rec[k]:
                comp[k][v] += 1
        comp["direction_of_effect"][rec["direction_of_effect"]] += 1

        (out / "papers" / f"{ck}.json").write_text(json.dumps({
            **biblio, "abstract": abstract.get("text"), "abstract_source": abstract.get("source"), "record": rec,
        }, ensure_ascii=False) + "\n")
        bulk.append({**biblio, "record": rec})

        for i, ex in enumerate(rec["exposures"]):
            terms = ex["target"]["terms"]
            anc = sorted({a for t in terms for a in ancestors(t)})
            for t in terms:
                chain = [t] + ancestors(t)
                top = next((c for c in chain if tmap.get(c, {}).get("parent") in (None, "brain", "subcortical", "cerebral_cortex", "peripheral_nervous_system")), chain[-1])
                comp["target_top"][top] += 1
            comp["device_family"][ex["device"]["family"]] += 1
            wf = ex["timing"]["waveform"]
            for w in (wf if isinstance(wf, list) else [wf]):
                comp["waveform"][w] += 1
            for leaf in NUMERIC_LEAVES:
                if leaf.startswith("unspecified_domain."):
                    continue  # null by rule unless used, so 'reported' would be meaningless
                v = get_path(ex, leaf)
                if v is None:
                    continue
                nr_counts[leaf][1] += 1
                if v == "not_reported":
                    nr_counts[leaf][0] += 1
            # a pressure or intensity stated in ANY domain, which is what a reader first asks
            for q in ("pressure_kpa", "isppa_w_cm2", "ispta_w_cm2"):
                vals = [ex["free_field"][q], ex["in_situ"][q], ex["unspecified_domain"][q]]
                if all(v is None for v in vals):
                    continue
                nr_counts[f"any_domain.{q}"][1] += 1
                if not any(isinstance(v, (int, float, list)) and not isinstance(v, bool) for v in vals):
                    nr_counts[f"any_domain.{q}"][0] += 1
            d = ex.get("derived", {})
            rows.append({
                "citekey": ck, "exposure": i, "n_exposures": len(rec["exposures"]),
                "title": biblio["title"], "authors_short": biblio["authors_short"], "authors_all": biblio["authors_all"], "year": biblio["year"],
                "journal": biblio["journal"], "doi": rec["doi"], "abstract": abstract.get("text"),
                "model_system": rec["model_system"], "species": rec["species"], "conditions": rec["conditions"],
                "n_subjects": rec["n_subjects"], "subject_unit": rec["subject_unit"], "n_sessions": rec["n_sessions_per_subject"],
                "randomised": rec["randomised"], "blinding": rec["blinding"], "sham_type": rec["sham_type"],
                "auditory_control": rec["auditory_control"], "readouts": rec["readouts"], "readout_other": rec["readout_other"],
                "readout_timing": rec["readout_timing"], "anaesthesia": rec["anaesthesia"],
                "direction": rec["direction_of_effect"], "direction_notes": rec["direction_notes"],
                "adverse_events": rec["adverse_events"],
                "label": ex["label"], "target_terms": terms, "target_all": sorted(set(terms) | set(anc)), "target_label": ex["target"]["label"],
                "device_family": ex["device"]["family"], "device_manufacturer": ex["device"]["manufacturer"], "device_model": ex["device"]["model"],
                "f0": ex["fundamental_frequency_khz"],
                "waveform": wf, "pd": ex["timing"]["pulse_duration_ms"], "prf": ex["timing"]["pulse_repetition_frequency_hz"],
                "dc": ex["timing"]["duty_cycle_pct"], "sd": ex["timing"]["sonication_duration_s"],
                "protocol": ex["timing"]["protocol_description"],
                "ff": ex["free_field"], "is": {k: ex["in_situ"][k] for k in ("pressure_kpa", "isppa_w_cm2", "ispta_w_cm2")},
                "is_method": ex["in_situ"]["method"], "is_reported_as": ex["in_situ"]["reported_as"], "un": ex["unspecified_domain"],
                "ranges": {k: num_summary(v) for k, v in {
                    "f0": ex["fundamental_frequency_khz"], "pd": ex["timing"]["pulse_duration_ms"], "prf": ex["timing"]["pulse_repetition_frequency_hz"],
                    "dc": ex["timing"]["duty_cycle_pct"], "sd": ex["timing"]["sonication_duration_s"],
                    "ff_p": ex["free_field"]["pressure_kpa"], "ff_isppa": ex["free_field"]["isppa_w_cm2"], "ff_ispta": ex["free_field"]["ispta_w_cm2"],
                    "is_p": ex["in_situ"]["pressure_kpa"], "is_isppa": ex["in_situ"]["isppa_w_cm2"], "is_ispta": ex["in_situ"]["ispta_w_cm2"],
                    "un_p": ex["unspecified_domain"]["pressure_kpa"], "un_isppa": ex["unspecified_domain"]["isppa_w_cm2"], "un_ispta": ex["unspecified_domain"]["ispta_w_cm2"],
                }.items()},
                "swept": [k for k, v in {"f0": ex["fundamental_frequency_khz"], "pd": ex["timing"]["pulse_duration_ms"], "prf": ex["timing"]["pulse_repetition_frequency_hz"], "dc": ex["timing"]["duty_cycle_pct"], "sd": ex["timing"]["sonication_duration_s"]}.items() if isinstance(v, list)],
                "dc_computed": d.get("duty_cycle_computed_pct"), "dc_mismatch": d.get("duty_cycle_mismatch", False),
                "checks": d.get("checks", []), "n_flags": len(rec["provenance"]["flags"]),
                "status": d.get("field_status", {}),
            })

    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    version = read_version()
    (out / "index.json").write_text(json.dumps({
        "generated": generated, "version": version, "schema_version": "1.0", "n_papers": len(files), "n_exposures": len(rows), "rows": rows,
    }, ensure_ascii=False) + "\n")
    (out / "stats.json").write_text(json.dumps({
        "generated": generated, "version": version, "n_papers": len(files), "n_exposures": len(rows), "n_flags": n_flags,
        "subjects": {"by_unit": subjects, "papers_by_unit": dict(subjects_papers), "papers_not_reported": subjects_not_reported},
        "papers_per_year": sorted(((y, n) for y, n in per_year.items() if y), key=lambda x: x[0]),
        "reportability": {leaf: {"not_reported": nr, "applicable": ap_, "share_reported": round(1 - nr / ap_, 3) if ap_ else None}
                          for leaf, (nr, ap_) in sorted(nr_counts.items())},
        "composition": {k: sorted(c.items(), key=lambda x: -x[1]) for k, c in comp.items()},
    }, ensure_ascii=False, indent=1) + "\n")
    (out / "targets.json").write_text(json.dumps(targets, ensure_ascii=False) + "\n")
    (out / "tus-paper-archive-v1.json").write_text(json.dumps({
        "generated": generated, "version": version, "schema_version": "1.0", "schema": "https://github.com/NeuroHarmonics/tus-paper-archive/blob/main/SCHEMA.md",
        "licence_note": "Structured fields and one-sentence attributed source quotes. Abstracts are not included in this file.",
        "n_papers": len(files), "papers": bulk,
    }, ensure_ascii=False) + "\n")

    sizes = {p.name: f"{p.stat().st_size / 1024:.0f} kB" for p in out.glob("*.json")}
    print(f"built {len(files)} papers, {len(rows)} exposure rows -> {out}  {sizes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
