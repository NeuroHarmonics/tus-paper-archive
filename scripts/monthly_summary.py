#!/usr/bin/env python3
"""What changed in the archive between two git refs, as a post and a changelog entry.

Compares data/manifest.json, data/papers/ and data/screening.jsonl at two refs (by default the
latest release tag and HEAD) and reports:

  new papers        added to the manifest, published in the release year or the year before
  historical papers added to the manifest, published earlier
  corrected records data/papers files that exist at both refs and differ outside `derived`
  removed papers    dropped from the manifest (scope rulings)
  screened          screening verdicts added

Everything is computed from git, so the numbers cannot drift from the data.

  uv run scripts/monthly_summary.py                       # last tag .. HEAD, Markdown
  uv run scripts/monthly_summary.py --since v2026.09.0 --until v2026.10.0
  uv run scripts/monthly_summary.py --format linkedin     # plain text, no Markdown
  uv run scripts/monthly_summary.py --format changelog    # a CHANGELOG.md entry
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys

from common import ROOT


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def show_json(ref: str, path: str):
    r = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def latest_tag() -> str | None:
    tags = [t for t in git("tag", "--list", "v*", "--sort=-creatordate").split() if t]
    return tags[0] if tags else None


def strip_derived(rec: dict) -> dict:
    rec = json.loads(json.dumps(rec))
    for ex in rec.get("exposures", []):
        ex.pop("derived", None)
    return rec


def authors_short(authors):
    fam = [a.get("family") or a.get("name") or "" for a in (authors or [])]
    if not fam:
        return ""
    return fam[0] if len(fam) == 1 else f"{fam[0]} & {fam[1]}" if len(fam) == 2 else f"{fam[0]} et al."


def count_verdicts(ref: str) -> int:
    try:
        text = git("show", f"{ref}:data/screening.jsonl")
    except Exception:
        return 0
    return sum(1 for line in text.split("\n") if line.strip())


def collect(since: str, until: str, new_from_year: int) -> dict:
    man_a = (show_json(since, "data/manifest.json") or {}).get("papers", {})
    man_b = (show_json(until, "data/manifest.json") or {}).get("papers", {})
    added = sorted(set(man_b) - set(man_a))
    removed = sorted(set(man_a) - set(man_b))
    new = [man_b[c] for c in added if (man_b[c].get("year") or 0) >= new_from_year]
    historical = [man_b[c] for c in added if (man_b[c].get("year") or 0) < new_from_year]

    corrected = []
    for line in git("diff", "--name-status", since, until, "--", "data/papers").split("\n"):
        if not line.startswith("M"):
            continue
        path = line.split("\t", 1)[1]
        a, b = show_json(since, path), show_json(until, path)
        if a and b and strip_derived(a) != strip_derived(b):
            ck = b.get("citekey") or path.rsplit("/", 1)[1][:-5]
            corrected.append({"citekey": ck, "human_edited": bool(b.get("provenance", {}).get("human_edited"))})

    return {
        "since": since, "until": until,
        "new": new, "historical": historical, "removed": [man_a[c] for c in removed], "corrected": corrected,
        "n_papers": len(man_b), "n_papers_before": len(man_a),
        "screened": max(0, count_verdicts(until) - count_verdicts(since)),
    }


def line_for(p: dict) -> str:
    j = f", {p['journal']}" if p.get("journal") else ""
    return f"{authors_short(p.get('authors'))} ({p.get('year')}). {p.get('title')}{j}. https://doi.org/{p.get('doi')}"


def render_markdown(s: dict, version: str, month: str) -> str:
    out = [f"## {version} ({month})", ""]
    out.append(f"{s['n_papers']} papers ({s['n_papers_before']} in {s['since']}). {len(s['new'])} new, {len(s['historical'])} historical, {len(s['corrected'])} corrected, {len(s['removed'])} removed, {s['screened']} candidates screened.")
    if s["new"]:
        out += ["", "**New papers**", ""] + [f"- {line_for(p)}" for p in sorted(s["new"], key=lambda p: (-(p.get("year") or 0), p.get("title") or ""))]
    if s["historical"]:
        out += ["", "**Historical papers added**", ""] + [f"- {line_for(p)}" for p in sorted(s["historical"], key=lambda p: (p.get("year") or 0))]
    if s["removed"]:
        out += ["", "**Removed after a scope ruling**", ""] + [f"- {line_for(p)}" for p in s["removed"]]
    if s["corrected"]:
        out += ["", f"**Corrected records:** {', '.join(c['citekey'] for c in s['corrected'])}"]
    return "\n".join(out) + "\n"


def render_linkedin(s: dict, version: str, month: str) -> str:
    out = [f"TUS Paper Archive, {month} update (version {version})", ""]
    out.append(f"The archive now holds {s['n_papers']} papers on ultrasound neuromodulation, each with its acoustic and pulse-timing parameters recorded in the ITRUSST format and every number traceable to the sentence it came from.")
    out.append("")
    out.append("This month:")
    out.append(f"• {len(s['new'])} new papers added")
    out.append(f"• {len(s['historical'])} earlier papers added")
    out.append(f"• {len(s['corrected'])} records corrected")
    out.append(f"• {len(s['removed'])} papers removed after a scope review")
    out.append(f"• {s['screened']} candidate papers screened")
    if s["new"]:
        out += ["", "New this month:"]
        for p in sorted(s["new"], key=lambda p: (-(p.get("year") or 0), p.get("title") or "")):
            out.append(f"• {p.get('title')} ({authors_short(p.get('authors'))}, {p.get('journal') or 'preprint'}) https://doi.org/{p.get('doi')}")
    out += ["", "Explore the data: https://neuroharmonics.com/tus-archive", "Report a correction or propose a paper: https://github.com/NeuroHarmonics/tus-paper-archive/issues", "", "#ultrasound #neuromodulation #TUS #LIFU #openscience"]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="git ref to compare from; default: latest v* tag")
    ap.add_argument("--until", default="HEAD")
    ap.add_argument("--format", choices=["markdown", "linkedin", "changelog", "json"], default="markdown")
    ap.add_argument("--version", help="version label for the heading; default: VERSION file")
    ap.add_argument("--new-from-year", type=int, help="papers published in this year or later count as new; default: the release year minus one")
    args = ap.parse_args()

    since = args.since or latest_tag()
    if not since:
        print("no release tag yet; pass --since <ref>", file=sys.stderr)
        return 2
    version = args.version or ((ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "unreleased")
    today = dt.date.today()
    new_from = args.new_from_year or (today.year - 1)
    s = collect(since, args.until, new_from)
    month = today.strftime("%B %Y")
    if args.format == "json":
        print(json.dumps(s, indent=1, ensure_ascii=False))
    elif args.format == "linkedin":
        print(render_linkedin(s, version, month))
    else:
        print(render_markdown(s, version, month))
    return 0


if __name__ == "__main__":
    sys.exit(main())
