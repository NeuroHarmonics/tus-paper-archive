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
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import subprocess
import sys

from common import ROOT, WEBSITE_DATA_REL, website_repo

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--version", help="override the computed version")
    ap.add_argument("--site-repo", type=pathlib.Path, default=website_repo(), help="the website repository checkout; default: $TUS_WEBSITE_REPO or ../website")
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

    if not args.apply:
        print("dry run; pass --apply to write VERSION, rebuild, update CHANGELOG.md, commit and tag")
        return 0

    VERSION.write_text(version + "\n")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_site.py"), "--out", str(data_dir)], cwd=ROOT, check=True)

    if since:
        entry = run(sys.executable, str(ROOT / "scripts" / "monthly_summary.py"), "--since", since, "--version", version, "--format", "changelog")
    else:
        entry = f"## {version} ({today.strftime('%B %Y')})\n\nFirst tagged release.\n"
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
