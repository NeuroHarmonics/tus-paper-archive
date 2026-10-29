#!/usr/bin/env python3
"""Cut a release: bump VERSION and CITATION.cff, rebuild the site data, write the changelog entry, commit, tag.

The site data is written into the website repository checkout (build_site.py --out) and staged
there; committing it, on a branch and through a PR, is left to you, because that repo picks its own
branch names. The website deploy is the release going live.

Versions are calendar-based, YYYY.MM.N: the year and month of the release, and N counting
releases within that month from 0. Tags are v<version>. A monthly release is therefore
v2026.10.0, and a correction pushed later the same month is v2026.10.1.

  uv run scripts/release.py                # show what would happen, and which website checkout it found
  uv run scripts/release.py --apply        # do it (commit + annotated tag here; stage data in the website repo)
  uv run scripts/release.py --apply --site-repo ~/Documents/Local-Repos/website
  uv run scripts/release.py --apply --accept-gaps   # release anyway; the counts go into the changelog

A release is refused while TUS-like candidates are unscreened or undecided or relevant papers are
not held; --accept-gaps releases anyway and writes the counts into the changelog entry.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import pathlib
import subprocess
import sys

from build_batches import TUS_LIKE, already_decided
from common import DATA, ROOT, WEBSITE_DATA_REL, load_screening, read_json, website_repo

VERSION = ROOT / "VERSION"
CHANGELOG = ROOT / "CHANGELOG.md"


def run(*args: str, check: bool = True) -> str:
    return subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True, check=check).stdout


def next_version(today: dt.date) -> str:
    """The VERSION file names the release being prepared. If it is already tagged, bump within the
    month; if the month has moved on, start the new month at .0."""
    cur = VERSION.read_text().strip() if VERSION.exists() else ""
    ym = f"{today.year}.{today.month:02d}"
    tagged = f"v{cur}" in run("git", "tag", "--list", f"v{cur}").split() if cur else False
    if cur.startswith(ym + "."):
        return f"{ym}.{int(cur.rsplit('.', 1)[1]) + 1}" if tagged else cur
    return f"{ym}.0"


def coverage_gaps() -> dict[str, int]:
    """What this release would ship without having looked at. Each paper is counted once: a
    fetch-to-screen paper only as that. The want-list is read from acquire.py's wanted.csv, which
    must be newer than the ledger and manifest."""
    cands = (read_json(DATA / "sweep-candidates.json", {}) or {}).get("candidates", {})
    held = {(p.get("doi") or "").lower()
            for p in (read_json(DATA / "manifest.json", {}) or {}).get("papers", {}).values()}
    decided = already_decided()
    to_fetch = set(((read_json(DATA / "fetch-to-screen.json", {}) or {}).get("papers") or {}))

    def tus_like(d: str) -> bool:
        c = cands.get(d) or {}
        return bool(TUS_LIKE.search(f"{c.get('title') or ''} {c.get('abstract') or ''}"))

    wanted = DATA / "wanted.csv"
    if not wanted.exists() or any(wanted.stat().st_mtime < (DATA / f).stat().st_mtime
                                  for f in ("screening.jsonl", "manifest.json")):
        sys.exit("data/wanted.csv is missing or older than the screening ledger or manifest; "
                 "run scripts/acquire.py first")
    with wanted.open(newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["doi"].lower() not in held]
    return {
        "unscreened": sum(1 for d in cands if d not in decided and d not in to_fetch and tus_like(d)),
        "undecided": sum(1 for d, v in load_screening().items()
                         if v.get("verdict") == "unspecified" and d not in held and d not in to_fetch
                         and tus_like(d)),
        "unfetched": sum(1 for r in rows if r.get("access") != "to_screen"),
        "fetch_to_screen": sum(1 for r in rows if r.get("access") == "to_screen"),
    }


def gaps_note(g: dict[str, int]) -> str:
    return (f"Released with {g['unscreened']} TUS-like sweep candidates not yet screened, "
            f"{g['undecided']} screened but undecided, {g['unfetched']} relevant papers not yet held, "
            f"and {g['fetch_to_screen']} papers to fetch before they can be screened.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--version", help="override the computed version")
    ap.add_argument("--site-repo", type=pathlib.Path, default=website_repo(), help="the website repository checkout; default: $TUS_WEBSITE_REPO or ../website")
    ap.add_argument("--accept-gaps", action="store_true",
                    help="release although candidates are unscreened or relevant papers unfetched; "
                         "the counts are written into the changelog entry")
    args = ap.parse_args()

    if args.site_repo is None or not (args.site_repo / "package.json").exists():
        print("no website checkout: pass --site-repo or set TUS_WEBSITE_REPO", file=sys.stderr)
        return 2
    data_dir = args.site_repo / WEBSITE_DATA_REL

    if run("git", "status", "--porcelain").strip() and args.apply:
        print("working tree is not clean; commit or stash first", file=sys.stderr)
        return 2

    today = dt.date.today()
    version = args.version or next_version(today)
    tags = run("git", "tag", "--list", "v*", "--sort=-creatordate").split()
    since = tags[0] if tags else None
    print(f"release {version}  (previous: {since or 'none'})")
    print(f"site data -> {data_dir}")

    gaps = coverage_gaps()
    if any(gaps.values()):
        print(gaps_note(gaps))
        if not args.accept_gaps:
            print("refusing to release: screen and fetch these, or pass --accept-gaps to release "
                  "anyway with the counts in the changelog", file=sys.stderr)
            return 2

    if not args.apply:
        print("dry run; pass --apply to write VERSION, rebuild, update CHANGELOG.md, commit and tag")
        return 0

    VERSION.write_text(version + "\n")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_site.py"), "--out", str(data_dir)], cwd=ROOT, check=True)

    if since:
        entry = run(sys.executable, str(ROOT / "scripts" / "monthly_summary.py"), "--since", since, "--version", version, "--format", "changelog")
    else:
        entry = f"## {version} ({today.strftime('%B %Y')})\n\nFirst tagged release.\n"
    if any(gaps.values()):
        entry = entry.rstrip("\n") + f"\n\n{gaps_note(gaps)}\n"
    head = "# Changelog\n\nReleases are calendar-versioned, YYYY.MM.N, and tagged vYYYY.MM.N. Each entry lists what changed in the data since the previous tag.\n\n"
    body = CHANGELOG.read_text()[len(head):] if CHANGELOG.exists() and CHANGELOG.read_text().startswith(head) else (CHANGELOG.read_text() if CHANGELOG.exists() else "")
    CHANGELOG.write_text(head + entry + "\n" + body)

    cff = ROOT / "CITATION.cff"
    if cff.exists():
        import re
        text = re.sub(r"(?m)^version: .*$", f"version: {version}", cff.read_text())
        text = re.sub(r"(?m)^date-released: .*$", f"date-released: {today.isoformat()}", text)
        cff.write_text(text)

    run("git", "add", "VERSION", "CHANGELOG.md", "CITATION.cff")
    run("git", "commit", "-m", f"Release {version}")
    run("git", "tag", "-a", f"v{version}", "-m", f"TUS Paper Archive {version}")
    print(f"committed and tagged v{version}; push with: git push && git push --tags")

    subprocess.run(["git", "-C", str(args.site_repo), "add", str(WEBSITE_DATA_REL)], check=True)
    print(f"staged {WEBSITE_DATA_REL} in {args.site_repo}; commit it on a branch and open a PR:\n"
          f"  git -C {args.site_repo} checkout -b tus-archive-{version} && git -C {args.site_repo} commit -m \"TUS Paper Archive data {version}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
