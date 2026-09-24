#!/usr/bin/env python3
"""Second-pass resolution for PDFs that identify.py could not settle.

Takes titles transcribed by the paper-identifier agent and resolves them to
Crossref records by bibliographic search. The agent supplies only the transcribed
front matter; the authoritative record still comes from Crossref, so a
mis-transcription shows up as a low match score rather than as bad metadata.

Papers with no Crossref record at all (genuinely pre-DOI work) are kept with the
transcribed metadata and marked, rather than dropped.

  uv run scripts/resolve_missing.py --input <identified.json>
"""

from __future__ import annotations

import argparse
import re
import difflib
import sys
import time
from pathlib import Path

import httpx

from common import DATA, MAILTO, read_json, write_json
from identify import (
    Crossref, crossref_record_problems, record_from_crossref, title_agreement,
)

SEARCH = "https://api.crossref.org/works"
ACCEPT = 0.90          # title agreement required to accept a search hit
ACCEPT_WEAK = 0.80     # accepted only when the year also matches


def search_by_title(client: httpx.Client, title: str, rows: int = 5) -> list[dict]:
    """Crossref bibliographic search.

    No `select` filter: `subtype` is not selectable and a rejected field fails the
    whole query with a 400, which previously turned every search into a silent
    "not in Crossref". Full records cost more bytes and are worth it.
    """
    for attempt in range(4):
        try:
            r = client.get(SEARCH, params={
                "query.bibliographic": title, "rows": rows, "mailto": MAILTO,
            })
            if r.status_code == 429:                     # rate limited: back off
                time.sleep(2.0 * (attempt + 1))
                continue
            r.raise_for_status()
            time.sleep(0.15)
            return r.json().get("message", {}).get("items", []) or []
        except httpx.HTTPStatusError as e:
            print(f"    search HTTP {e.response.status_code} for {title[:50]!r}",
                  file=sys.stderr)
            return []
        except Exception as e:
            print(f"    search error {type(e).__name__}", file=sys.stderr)
            time.sleep(1.0)
    print(f"    search gave up (rate limited) for {title[:50]!r}", file=sys.stderr)
    return []


def resolve_filename(name: str | None, records: dict) -> str | None:
    """Match an agent-reported filename to a real one.

    Agents transcribe long filenames imperfectly (observed: 'VeryShortPulse' for
    'VeryShortPulses'). An exact-match-only join silently drops those records, so
    fall back to closest match -- but only when it is unambiguous.
    """
    if not name:
        return None
    if name in records:
        return name
    close = difflib.get_close_matches(name, list(records), n=2, cutoff=0.92)
    if len(close) == 1 or (len(close) == 2 and
                           difflib.SequenceMatcher(None, name, close[0]).ratio() -
                           difflib.SequenceMatcher(None, name, close[1]).ratio() > 0.05):
        print(f"  ~~   filename fuzzy-matched: {name[:44]!r} -> {close[0][:44]!r}")
        return close[0]
    return None


def parent_doi(doi: str | None) -> str | None:
    """Strip a registered sub-record suffix to get the article's own DOI.

    eLife registers its assessments and public reviews as separate Crossref
    records -- 10.7554/elife.100827.3.sa2 -- whose titles begin with the article
    title. A title search can therefore return six sub-records and no article.

    JoVE does the same for the video: 10.3791/58781-v carries the article's exact
    title, so a title search scores it 1.0 and it wins outright. Citing the video
    component instead of the paper is the same failure as citing a Wiley cover.
    """
    if not doi:
        return None
    m = re.match(r"^(10\.\d{4,9}/[^\s]+?)(?:\.v?\d+)?\.sa\d+$", doi)
    if m:
        return m.group(1)
    m = re.match(r"^(10\.3791/\d+)-v$", doi)      # JoVE video component
    return m.group(1) if m else None


def page_span(pages: str | None) -> int:
    """Number of pages a Crossref record spans, 0 if unstated.

    A single-page record for a multi-page paper is usually an abstract or a
    citation entry rather than the article.
    """
    if not pages:
        return 0
    m = re.match(r"\s*(\d+)\s*[-–]+\s*(\d+)", pages)
    if m:
        return max(1, int(m.group(2)) - int(m.group(1)) + 1)
    return 1 if re.match(r"\s*\d+\s*$", pages) else 0


def pick_best(scored: list[tuple[float, dict]], item: dict) -> tuple[dict, float] | None:
    """Choose among Crossref hits that all match the title.

    Title score alone is not enough: the same title appears in more than one
    Crossref record (a paper and the abstract of it, a preprint and the version
    of record, a reprint). Observed: Harvey 1929's Am J Physiol paper losing to a
    single-page 1930 American Heart Journal abstract of the same title.

    So rank by title score, then break ties on the evidence we have -- an exact
    year match against the printed page, then a multi-page span.
    """
    if not scored:
        return None
    agent_year = item.get("year")

    def key(entry: tuple[float, dict]):
        score, cand = entry
        yr = cand.get("year")
        return (
            round(score, 3),                                        # title agreement
            1 if (agent_year and yr == agent_year) else 0,          # exact year
            1 if page_span(cand.get("pages")) > 1 else 0,           # not a 1-page stub
            -abs((yr or 0) - agent_year) if agent_year and yr else 0,
        )

    score, cand = max(scored, key=key)
    return cand, score


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    args = ap.parse_args()

    identified = read_json(args.input)
    if not identified:
        sys.exit(f"No records in {args.input}")

    ingest = read_json(DATA / "ingest.json")
    records = ingest["records"]
    cr = Crossref()
    client = httpx.Client(
        timeout=25.0,
        headers={"User-Agent": f"tus-paper-archive/0.1 (mailto:{MAILTO})"},
        follow_redirects=True,
    )

    stats = {"by_doi": 0, "by_title": 0, "no_crossref": 0, "unmatched_file": 0}

    for item in identified:
        fname = resolve_filename(item.get("file"), records)
        if fname is None:
            print(f"  ?? no such file in ingest.json: {item.get('file')}", file=sys.stderr)
            stats["unmatched_file"] += 1
            continue

        rec = records[fname]
        title = (item.get("title") or "").strip()
        rec["agent_title"] = title or None
        rec["agent_confidence"] = item.get("confidence")
        rec["agent_notes"] = item.get("notes")

        # A conference abstract is a record in its own right (INCLUSION.md C19);
        # only true supplementary material is an attachment (C17). The agent
        # conflates these, so trust the filename-based flag set by identify.py.
        if item.get("is_supplementary") and not rec.get("is_supplementary"):
            rec["agent_notes"] = (
                (rec.get("agent_notes") or "") + " [agent suggested supplementary; "
                "overridden - filename does not indicate supplementary material]"
            ).strip()

        proj = None
        route = None

        # 1. A DOI the agent read off the page: authoritative, just look it up.
        if item.get("doi"):
            msg = cr.get(item["doi"].strip().lower())
            if msg:
                cand = record_from_crossref(msg)
                if not crossref_record_problems(cand):
                    proj, route = cand, "agent_doi"

        # 2. Otherwise search Crossref by the transcribed title.
        if proj is None and len(title) >= 12:
            scored, sub_records = [], set()
            for hit in search_by_title(client, title):
                cand = record_from_crossref(hit)
                if crossref_record_problems(cand):
                    # Registered sub-records (eLife assessments and reviews) can
                    # crowd out the article entirely. Remember their parent DOI.
                    if (b := parent_doi(cand.get("doi"))):
                        sub_records.add(b)
                    continue
                scored.append((title_agreement(cand["title"], title), cand))

            # Every hit was a sub-record: try the parent article directly.
            if not scored:
                for b in sorted(sub_records):
                    msg = cr.get(b)
                    if not msg:
                        continue
                    cand = record_from_crossref(msg)
                    if not crossref_record_problems(cand):
                        scored.append((title_agreement(cand["title"], title), cand))
                        print(f"  ..   recovered parent record {b}")

            best = pick_best(scored, item)
            if best:
                cand, score = best
                yr = item.get("year")
                year_ok = bool(yr) and cand.get("year") and abs(cand["year"] - yr) <= 1
                if score >= ACCEPT or (score >= ACCEPT_WEAK and year_ok):
                    proj, route = cand, "title_search"
                    rec["title_search_score"] = round(score, 3)
                else:
                    rec["title_search_best"] = {
                        "doi": cand.get("doi"),
                        "title": cand.get("title"),
                        "score": round(score, 3),
                    }

        if proj:
            rec.update(proj)
            rec["doi_source"] = route
            rec["status"] = "resolved"
            # Drop flags describing the *failed* attempt; this run superseded it.
            flags = [f for f in rec.get("flags", []) if f not in
                     ("title_mismatch", "crossref_type_journal", "crossref_type_component",
                      "crossref_title_is_not_an_article_title", "crossref_type_peer-review",
                      "no_crossref_record", "year_disagreement",
                      "unverified_no_usable_embedded_title")]
            flags.append(f"resolved_via_{route}")
            if proj.get("update_to"):
                flags.append("erratum")
            if proj.get("is_preprint"):
                flags.append("preprint")

            # A perfect title score is not proof of identity. The same title
            # recurs across venues -- reprints, a conference abstract later
            # published in full, a journal reissue. When the year on the page
            # disagrees with Crossref, we may have matched the wrong artefact.
            # Observed: Harvey's 1929 Am J Physiol paper matching a 1930
            # American Heart Journal record at score 1.0.
            agent_year = item.get("year")
            if agent_year and proj.get("year") and abs(proj["year"] - agent_year) > 1:
                rec["status"] = "needs_review"
                flags.append("year_disagreement")
                rec["note"] = (
                    f"Title matches exactly but the year does not: page says "
                    f"{agent_year}, Crossref record is {proj['year']} "
                    f"({proj.get('journal')}). Possible reprint, abstract, or "
                    f"same-title different-venue record."
                )

            rec["flags"] = sorted(set(flags))
            if rec["status"] == "resolved":
                rec.pop("note", None)
            stats["by_doi" if route == "agent_doi" else "by_title"] += 1
            print(f"  {'CHK ' if rec['status'] == 'needs_review' else 'OK  '} "
                  f"[{route}] {fname[:50]}")
        else:
            # Genuinely absent from Crossref (much pre-1960 work is). Keep the
            # transcription so the paper is not lost, and mark it clearly.
            rec["title"] = rec.get("title") or title or None
            rec["year"] = rec.get("year") or item.get("year")
            rec["journal"] = rec.get("journal") or item.get("journal")
            rec["doi"] = rec.get("doi")
            rec["status"] = "resolved_no_doi"
            rec["doi_source"] = "agent_transcription"
            rec["flags"] = sorted(set(rec.get("flags", []) + ["no_crossref_record"]))
            stats["no_crossref"] += 1
            print(f"  NODOI      {fname[:52]}")

    cr.save()

    by_status: dict[str, int] = {}
    for r in records.values():
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    ingest["counts"] = by_status
    write_json(DATA / "ingest.json", ingest)

    print("\n--- resolve_missing summary ---")
    for k, v in stats.items():
        print(f"  {k:16s} {v}")
    print("--- ingest status ---")
    for k, v in sorted(by_status.items()):
        print(f"  {k:22s} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
