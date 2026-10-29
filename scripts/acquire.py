#!/usr/bin/env python3
"""Turn screened sweep candidates into PDFs we hold, plus a want-list for a person with library access.

Splits in-scope papers into:
  * open access  -> fetched automatically into candidates/, no human effort;
  * everything else -> data/wanted.csv, ranked, for institutional access.

This script NEVER attempts to bypass a paywall. It downloads only from an
open-access location reported by OpenAlex or Unpaywall. A paper without one is
listed and left alone -- that is the end of the automated path for it.

  uv run scripts/acquire.py                 # plan + write wanted.csv
  uv run scripts/acquire.py --fetch         # also download OA PDFs
  uv run scripts/acquire.py --fetch --limit 200
"""

from __future__ import annotations

import argparse
import csv
import difflib
import html
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

from common import (
    load_screening,
    CANDIDATES, DATA, MAILTO, PREPRINT_DOI, compact_title, is_conference, normalise_title, read_json,
    surname_slug, write_json,
)
from prescreen import JOVE_DOI, OSF_PROJECT_DOI

UNPAYWALL = "https://api.unpaywall.org/v2/{doi}"
BATCH = 100  # want-list is worked in batches of this size


def unpaywall_pdf(client: httpx.Client, doi: str) -> tuple[str | None, str | None]:
    """Best open-access PDF URL for a DOI, and its licence, or (None, None)."""
    try:
        r = client.get(UNPAYWALL.format(doi=doi), params={"email": MAILTO})
        if r.status_code != 200:
            return None, None
        j = r.json()
        loc = j.get("best_oa_location") or {}
        return loc.get("url_for_pdf"), loc.get("license")
    except Exception:
        return None, None


ACCESS_LABEL = {
    "paywalled": ("needs login", "Institutional access required."),
    "oa_but_publisher_blocks_automated_access":
        ("open access", "Free to read - the publisher blocks automated fetching, "
                        "but it opens normally in a browser."),
    "landing_page_not_pdf":
        ("open access", "Free to read - the link resolves to a landing page rather "
                        "than a direct PDF."),
    "http_404": ("dead link", "The open-access link 404s. Try the DOI."),
    "to_screen": ("fetch to screen", "No abstract anywhere, so it cannot be screened "
                                     "until its full text is held."),
}


_STOP = set("the a an of in on and for with to by from using via as at is are its into "
            "we our this that were was be".split())


def _words(text: str | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for w in re.findall(r"[a-z]{3,}", (text or "").lower()):
        if w not in _STOP:
            out[w] = out.get(w, 0) + 1
    return out


def _cosine(a: dict[str, int], b: dict[str, int]) -> float:
    num = sum(v * b[k] for k, v in a.items() if k in b)
    den = (sum(v * v for v in a.values()) * sum(v * v for v in b.values())) ** 0.5
    return num / den if den else 0.0


def _surname(name: str | None) -> str:
    return re.sub(r"[^a-z]", "", (name or "").split(",")[0].strip().split(" ")[-1].lower())


def preprint_identity(client: httpx.Client, doi: str) -> dict:
    """Published version, authors and abstract of a preprint, from the server that holds it.

    bioRxiv and medRxiv record the published DOI themselves; Crossref carries
    is-preprint-of for Research Square, SSRN and some others. An HTTP error is
    returned as {"error": ...}, never read as "not published".
    """
    out: dict = {"published": None, "how": None, "authors": set(), "text": ""}
    if (m_ := re.match(r"^10\.2196/preprints\.(\d+)$", doi)):
        # JMIR gives a preprint and its article the same number and no Crossref link.
        out["published"], out["how"] = f"10.2196/{m_.group(1)}", "JMIR numbering"
    try:
        if doi.startswith(("10.1101/", "10.64898/")):
            for server in ("biorxiv", "medrxiv"):
                r = client.get(f"https://api.biorxiv.org/details/{server}/{doi}")
                r.raise_for_status()
                coll = r.json().get("collection") or []
                if coll:
                    x = coll[-1]
                    out["authors"] = {_surname(a) for a in x.get("authors", "").split(";") if a.strip()}
                    out["text"] = f"{x.get('title', '')} {x.get('abstract', '')}"
                    pub = [c.get("published") for c in coll if c.get("published") not in (None, "", "NA")]
                    if pub:
                        out["published"], out["how"] = pub[-1].lower(), f"{server} records it"
                    break
        r = client.get(f"https://api.crossref.org/works/{doi}")
        if r.status_code == 200:
            m = r.json()["message"]
            if not out["authors"]:
                out["authors"] = {_surname(a.get("family")) for a in m.get("author", []) if a.get("family")}
                out["text"] = f"{(m.get('title') or [''])[0]} {re.sub(r'<[^>]+>', ' ', m.get('abstract') or '')}"
            for it in (m.get("relation") or {}).get("is-preprint-of", []) or []:
                if not out["published"] and (it.get("id") or "").lower().startswith("10."):
                    out["published"], out["how"] = it["id"].lower(), "Crossref is-preprint-of"
        elif r.status_code != 404:
            out["error"] = f"Crossref HTTP {r.status_code}"
    except (httpx.HTTPError, ValueError, KeyError) as e:
        out["error"] = type(e).__name__
    return out


def write_wanted_html(rows: list[dict]) -> None:
    """A clickable, batched want-list for working through by hand.

    Progress ticks are stored in localStorage so the page remembers what has been
    fetched across reloads. Best-effort only: browsers restrict storage on
    file:// URLs, so every access is guarded and the page works without it.
    """
    def esc(s) -> str:
        return html.escape(str(s if s is not None else ""), quote=True)

    # Two sections: a screened-relevant paper becomes a record once fetched; a fetch-to-screen
    # paper has no verdict yet.
    relevant = [r for r in rows if r.get("access") != "to_screen"]
    to_screen = [r for r in rows if r.get("access") == "to_screen"]
    sections = [
        (f"Screened relevant, not held ({len(relevant)})",
         "Each passed the inclusion bar on its abstract and becomes a record once its PDF is in candidates/.",
         relevant),
        (f"Fetch to screen ({len(to_screen)})",
         "Not yet judged: no abstract exists anywhere, so the full text decides whether each is in. "
         "Many will turn out to be out of scope.",
         to_screen),
    ]

    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("access", "paywalled")] = counts.get(r.get("access", "paywalled"), 0) + 1
    summary = " · ".join(
        f"{n} {ACCESS_LABEL.get(k, (k, ''))[0]}" for k, n in
        sorted(counts.items(), key=lambda kv: -kv[1]))

    parts = [f"""<!doctype html>
<meta charset="utf-8">
<title>TUS want-list ({len(relevant)} relevant, {len(to_screen)} to screen)</title>
<style>
 :root {{ --bg:#fff; --fg:#1a1a1a; --mut:#666; --line:#e4e4e7; --acc:#1d4ed8;
          --ok:#047857; --okbg:#ecfdf5; --warn:#92400e; --warnbg:#fffbeb;
          --dead:#9f1239; --deadbg:#fff1f2; }}
 @media (prefers-color-scheme:dark) {{
  :root {{ --bg:#111214; --fg:#e8e8ea; --mut:#9a9aa2; --line:#2a2b30; --acc:#93b4ff;
           --ok:#6ee7b7; --okbg:#052e24; --warn:#fcd34d; --warnbg:#3b2f0b;
           --dead:#fda4af; --deadbg:#3f1220; }} }}
 body {{ background:var(--bg); color:var(--fg); margin:0 auto; padding:2rem 1.25rem 5rem;
         max-width:60rem; font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
 h1 {{ font-size:1.35rem; margin:0 0 .25rem; }}
 h2 {{ font-size:1rem; margin:2.5rem 0 .5rem; padding-bottom:.35rem;
       border-bottom:1px solid var(--line); }}
 .sub {{ color:var(--mut); font-size:.875rem; margin:0 0 .5rem; }}
 ol {{ list-style:none; padding:0; margin:0; }}
 li {{ display:grid; grid-template-columns:1.5rem 2.5rem 1fr; gap:.6rem;
       padding:.6rem .4rem; border-bottom:1px solid var(--line); align-items:start; }}
 li.done {{ opacity:.4; }}
 .rank {{ color:var(--mut); font-variant-numeric:tabular-nums; font-size:.8rem;
          text-align:right; padding-top:.15rem; }}
 a {{ color:var(--acc); text-decoration:none; font-weight:500; }}
 a:hover {{ text-decoration:underline; }}
 .meta {{ color:var(--mut); font-size:.8rem; margin-top:.15rem; }}
 .tag {{ display:inline-block; font-size:.7rem; padding:.08rem .4rem; border-radius:3px;
         margin-right:.4rem; white-space:nowrap; }}
 .t-open {{ color:var(--ok); background:var(--okbg); }}
 .t-login {{ color:var(--warn); background:var(--warnbg); }}
 .t-dead {{ color:var(--dead); background:var(--deadbg); }}
 input[type=checkbox] {{ margin-top:.25rem; width:1rem; height:1rem; cursor:pointer; }}
 .note {{ background:var(--warnbg); color:var(--warn); padding:.75rem 1rem;
          border-radius:6px; font-size:.85rem; margin:1rem 0 0; }}
</style>
<h1>TUS want-list &mdash; {len(relevant)} relevant, {len(to_screen)} to screen</h1>
<p class="sub">{esc(summary)}. Ranked by citations per year, so the most-used papers come first.
Ticks are remembered in this browser.</p>
<p class="note"><strong>Green &ldquo;open access&rdquo; rows are free to read.</strong>
The automated fetcher got a 403 because the publisher blocks bots &mdash; the link opens
normally for you. Drop what you collect into <code>candidates/</code> and re-run the ingest.</p>
"""]

    rank = 0
    for heading, blurb, group in sections:
        if not group:
            continue
        parts.append(f"<h2>{esc(heading)}</h2>\n<p class=\"sub\">{esc(blurb)}</p>\n<ol>")
        for r in group:
            rank += 1
            acc = r.get("access", "paywalled")
            label, _ = ACCESS_LABEL.get(acc, ("needs login", ""))
            cls = {"open access": "t-open", "dead link": "t-dead"}.get(label, "t-login")
            bits = [f'<span class="tag {cls}">{esc(label)}</span>']
            if r.get("year"):
                bits.append(esc(r["year"]))
            if r.get("journal"):
                bits.append(f"<em>{esc(r['journal'])}</em>")
            if r.get("cited_by"):
                bits.append(f"{esc(r['cited_by'])} cites")
            if r.get("record_class") and r["record_class"] != "primary_study":
                bits.append(esc(r["record_class"].replace("_", " ")))
            if r.get("check"):
                bits.append(f'<strong>{esc(r["check"])}</strong>')
            parts.append(
                f'<li data-doi="{esc(r["doi"])}">'
                f'<input type="checkbox">'
                f'<span class="rank">{rank}</span>'
                f'<span><a href="{esc(r["publisher_url"])}" target="_blank" '
                f'rel="noopener">{esc(r["title"])}</a>'
                f'<div class="meta">{" &middot; ".join(bits)}</div></span></li>')
        parts.append("</ol>")

    parts.append("""
<script>
 // file:// storage is unreliable, so never let a failure break the page.
 const KEY = "tus-wanted-done";
 let done = {};
 try { done = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
 for (const li of document.querySelectorAll("li[data-doi]")) {
   const doi = li.dataset.doi, box = li.querySelector("input");
   if (done[doi]) { box.checked = true; li.classList.add("done"); }
   box.addEventListener("change", () => {
     li.classList.toggle("done", box.checked);
     done[doi] = box.checked;
     try { localStorage.setItem(KEY, JSON.stringify(done)); } catch (e) {}
   });
 }
</script>""")

    (DATA / "wanted.html").write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote data/wanted.html ({len(relevant)} relevant, {len(to_screen)} to screen)")


# Record classes that INCLUSION.md excludes outright, without a judgement call: E3 removes
# anything reporting no original exposure of the authors' own, and E8 removes conference
# output as a class. Applied only to verdicts not made under the current criteria; a screener
# that saw the current criteria and still said relevant is trusted over this table.
CLASS_FAILS_BAR = {
    "review": "E3", "systematic_review": "E3", "meta_analysis": "E3",
    "letter_commentary": "E3", "editorial": "E3", "commentary": "E3",
    "book": "E3", "book_chapter": "E3", "erratum": "E3",
    "conference_abstract": "E8", "proceedings_article": "E8",
}

# Wiley publishes a cover-feature record beside the real article: same authors,
# and a title of the form "<Cover topic>: <the real title>". Its DOI carries a 7
# where the article's carries a 0 -- adbi.2018|7|0071 against adbi.2018|0|0041 --
# and it has no page range. Six sit in the sweep, one of them plainly titled
# "Front Cover:". The PDF behind such a DOI is the cover image, so a request to
# fetch one can never be satisfied; it must not reach the want-list at all.
WILEY_COVER_DOI = re.compile(r"^10\.1002/[a-z]+\.\d{4}7\d{4}$")


def safe_stem(rec: dict, doi: str) -> str:
    first = (rec.get("first_author") or "").strip()
    if not first:
        first = re.sub(r"[^a-z0-9]", "", doi.split("/")[-1])[:12] or "paper"
    yr = rec.get("year") or "nd"
    return f"{surname_slug(first).capitalize()}_{yr}_{compact_title(rec.get('title') or 'Untitled')}"


TITLE_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "using", "after",
    "into", "over", "under", "their", "between", "during", "among", "versus",
    "study", "effects", "effect", "based", "novel", "role", "case", "review",
}


def pdf_is_the_paper(pdf: Path, title: str) -> tuple[bool, str]:
    """Does the fetched PDF actually contain the paper we asked for?

    Content-type said application/pdf and the bytes parsed as one, yet an OA
    link for a middle-cerebral-artery-occlusion paper served a Bio-Rad
    instrument manual. Every check up to that point passed, so without this the
    manual would have been filed under the paper's title. Compare the first two
    pages against the title we expect.

    Reads five pages, not one. The manual's cover page carried 65 characters,
    so a page-1 check read it as "an image-only scan, cannot judge" and passed
    it. By page 5 there were 3,632 characters, none of them the paper's title.

    Deliberately one-sided: it only rejects when there is enough text to judge.
    A genuine image-only scan has no text layer at all, and discarding those
    would throw away exactly the old papers hardest to replace.
    """
    want = {w for w in re.findall(r"[a-z]{4,}", (title or "").lower())
            if w not in TITLE_STOPWORDS}
    if not want:
        return True, "no title to check against"
    try:
        txt = subprocess.run(
            ["pdftotext", "-f", "1", "-l", "5", str(pdf), "-"],
            capture_output=True, text=True, timeout=60,
        ).stdout.lower()
    except Exception:
        return True, "could not extract text"
    if len(txt.strip()) < 200:
        return True, "too little text to judge (scan?)"
    hit = sum(1 for w in want if w in txt)
    return hit / len(want) >= 0.4, f"{hit}/{len(want)} title words on pages 1-5"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="download open-access PDFs")
    ap.add_argument("--limit", type=int, help="cap downloads this run")
    args = ap.parse_args()

    blob = read_json(DATA / "sweep-candidates.json")
    if not blob:
        sys.exit("Run scripts/sweep.py first.")
    cands = blob["candidates"]
    verdicts = load_screening()
    if not verdicts:
        sys.exit("No screening ledger at data/screening.jsonl")

    # Papers already filed are not candidates for acquisition. Checking only
    # candidates/ for an existing file is not enough: once filed, a PDF lives in
    # library/ under a renamed stem, so every re-run would download it again.
    held = {p["doi"] for p in
            (read_json(DATA / "manifest.json", {}) or {}).get("papers", {}).values()
            if p.get("doi")}
    held |= {p["doi"] for p in
             (read_json(DATA / "excluded.json", {}) or {}).get("papers", [])
             if p.get("doi")}
    # Also held: preprints merged into a published version, and identified PDFs in candidates/.
    held |= {o["doi"].lower() for p in
             (read_json(DATA / "manifest.json", {}) or {}).get("papers", {}).values()
             for o in [p.get("preprint") or {}] + (p.get("other_preprints") or []) if o.get("doi")}
    in_hand = {(r.get("doi") or "").lower()
               for name, r in ((read_json(DATA / "ingest.json", {}) or {}).get("records") or {}).items()
               if r.get("doi") and (CANDIDATES / name).exists()}
    held |= in_hand
    if in_hand:
        print(f"skipping {len(in_hand)} already in candidates/, waiting to be screened and filed")

    # Also exclude by TITLE. DOI dedup alone leaves a preprint, a versioned
    # record or a re-deposit on the want-list even though we hold the paper --
    # 17 such entries were found by hand, including bioRxiv versions of Nature
    # papers already in the library. Asking a person to fetch a paper already held is the
    # most annoying failure this list can have.
    held_titles = {
        normalise_title(p.get("title")): p.get("doi")
        for p in (read_json(DATA / "manifest.json", {}) or {}).get("papers", {}).values()
        if p.get("title")
    }
    man_doi = {ck: p_.get("doi") for ck, p_ in
               (read_json(DATA / "manifest.json", {}) or {}).get("papers", {}).items()}
    dupe_of_held = {}
    vor_of_held_preprint: dict[str, str] = {}
    for d, v in verdicts.items():
        if v["verdict"] != "relevant" or d in held or d not in cands:
            continue
        nt = normalise_title(cands[d].get("title"))
        if not nt:
            continue
        m = difflib.get_close_matches(nt, list(held_titles), n=1, cutoff=0.90)
        if m:
            # Held as a preprint and this is the published version: that is the one we
            # want, so it stays on the list and replaces the preprint when it arrives.
            if PREPRINT_DOI.match(held_titles[m[0]] or "") and not PREPRINT_DOI.match(d):
                vor_of_held_preprint[d] = held_titles[m[0]]
                continue
            dupe_of_held[d] = held_titles[m[0]]

    off_bar: dict[str, tuple[str, str]] = {}
    covers: list[str] = []

    def passes_current_bar(doi: str, v: dict) -> bool:
        """Re-apply the parts of the bar that need no judgement.

        The verdict may predate the current criteria.
        Rather than re-screen thousands of candidates with a model, drop the
        ones the current bar excludes on their record class alone.
        """
        cls = (v.get("record_class") or "").lower()
        if not v.get("under_current_criteria") and cls in CLASS_FAILS_BAR:
            off_bar[doi] = (cls, CLASS_FAILS_BAR[cls])
            return False
        if WILEY_COVER_DOI.match(doi):
            covers.append(doi)
            return False
        # Rules the prescreen applies to undecided candidates, re-applied to verdicts that
        # predate them: an OSF registration or foundation project report is not a
        # publication (E7), and conference output is out as a class (E8).
        if JOVE_DOI.match(doi):
            off_bar[doi] = ("JoVE methods article", "E3")
            return False
        if OSF_PROJECT_DOI.match(doi):
            off_bar[doi] = ("grey literature", "E7")
            return False
        if is_conference(doi, (cands.get(doi) or {}).get("journal")):
            off_bar[doi] = ("conference output", "E8")
            return False
        return True

    relevant = {d: v for d, v in verdicts.items()
                if v["verdict"] == "relevant" and d in cands
                and d not in held and d not in dupe_of_held
                and passes_current_bar(d, v)}
    # Preprints: is the paper already held, or on this list, under its published DOI?
    # Followed links are acted on; a resemblance to a held paper (shared authors, a
    # similar abstract) only drops the entry when it is near-certain, and otherwise
    # marks it to be checked, because two papers by one group can look alike.
    held_text = {}
    for ck, p_ in (read_json(DATA / "manifest.json", {}) or {}).get("papers", {}).items():
        ab_ = read_json(DATA / "abstracts" / f"{ck}.json", {}) or {}
        held_text[ck] = ({_surname(a.get("family")) for a in (p_.get("authors") or [])},
                         _words(f"{p_.get('title') or ''} {ab_.get('text') or ''}"))
    preprint_client = httpx.Client(timeout=40, follow_redirects=True,
                                   headers={"User-Agent": f"tus-paper-archive/0.1 (mailto:{MAILTO})"})
    preprint_note: dict[str, str] = {d: f"published version of held preprint {p}: fetch to replace it"
                                     for d, p in vor_of_held_preprint.items()}
    listed_as_vor = 0
    for d in sorted(d for d in relevant if PREPRINT_DOI.match(d)):
        info = preprint_identity(preprint_client, d)
        time.sleep(0.1)
        vor = info["published"]
        if vor and (vor in held or vor in dupe_of_held):
            dupe_of_held[d] = vor
            continue
        if vor and vor in relevant:
            dupe_of_held[d] = vor
            listed_as_vor += 1
            continue
        if vor and not is_conference(vor) and (verdicts.get(vor) or {}).get("verdict") != "not_relevant":
            preprint_note[d] = f"published as {vor}: fetch that version"
        elif vor and (verdicts.get(vor) or {}).get("verdict") == "not_relevant":
            preprint_note[d] = f"published as {vor}, which was excluded ({verdicts[vor].get('criterion')}): fetch it to settle scope"
        best = None
        words = _words(info["text"])
        for ck, (ha, hw) in held_text.items():
            ov = len(info["authors"] & ha) / max(1, min(len(info["authors"]), len(ha))) if info["authors"] and ha else 0.0
            sim = _cosine(words, hw)
            if ov >= 0.8 and sim >= 0.6:
                best = ("held", ck)
                break
            if ov >= 0.5 and sim >= 0.4 and not best:
                best = ("similar", ck)
        if best and best[0] == "held":
            dupe_of_held[d] = man_doi.get(best[1], best[1])
        elif best:
            preprint_note.setdefault(d, f"check first: same authors as held {best[1]}")
        if info.get("error"):
            preprint_note.setdefault(d, f"published-version lookup failed ({info['error']})")
    relevant = {d: v for d, v in relevant.items() if d not in dupe_of_held}
    if listed_as_vor:
        print(f"dropping {listed_as_vor} preprints whose published version is on the list")

    if off_bar:
        print(f"dropping {len(off_bar)} screened before the current criteria whose "
              f"record class the current bar excludes outright:")
        for cls in sorted({c for c, _ in off_bar.values()}):
            hits = [k for c, k in off_bar.values() if c == cls]
            print(f"    {len(hits):4}  {cls.replace('_', ' '):22} [{hits[0]}]")
    if covers:
        print(f"dropping {len(covers)} Wiley cover-feature DOIs -- these are "
              f"cover images, not papers")
    if dupe_of_held:
        print(f"skipping {len(dupe_of_held)} whose paper is already held under a "
              f"different DOI (preprint, versioned record, re-deposit)")
    if held:
        already = sum(1 for d, v in verdicts.items()
                      if v["verdict"] == "relevant" and d in held)
        print(f"skipping {already} already held or already decided")
    unspecified = [d for d, v in verdicts.items() if v["verdict"] == "unspecified"]
    print(f"screened {len(verdicts)}  ->  relevant {len(relevant)}, "
          f"unspecified {len(unspecified)}, "
          f"excluded {len(verdicts) - len(relevant) - len(unspecified)}")

    client = httpx.Client(
        timeout=45.0, follow_redirects=True,
        headers={"User-Agent": f"tus-paper-archive/0.1 (mailto:{MAILTO})"},
    )

    open_rows, paywalled = [], []
    for doi, v in relevant.items():
        c = cands[doi]
        pdf = c.get("oa_pdf_url")
        lic = None
        if not pdf:
            pdf, lic = unpaywall_pdf(client, doi)
            time.sleep(0.08)
        row = {
            "doi": doi,
            "title": c.get("title"), "year": c.get("year"),
            "journal": c.get("journal"), "cited_by": c.get("cited_by"),
            "record_class": v.get("record_class"),
            "reason": v.get("reason"), "criterion": v.get("criterion"),
            "oa_pdf_url": pdf, "license": lic,
            "publisher_url": c.get("publisher_url") or f"https://doi.org/{doi}",
            "check": preprint_note.get(doi),
        }
        (open_rows if pdf else paywalled).append(row)

    # Rank by citations per year since publication, so a person with limited time
    # fetches the most-used papers first. The rank used to come from a ranking.json
    # beside the verdict files, which went when the verdicts moved into the ledger.
    this_year = time.gmtime().tm_year
    by_use = sorted(open_rows + paywalled, key=lambda r: -(r["cited_by"] or 0)
                    / max(1, this_year - (r["year"] or this_year) + 1))
    for i, r in enumerate(by_use, 1):
        r["rank"] = i
    open_rows.sort(key=lambda r: r["rank"])
    paywalled.sort(key=lambda r: r["rank"])
    for r in paywalled:
        r["access"] = "paywalled"

    # Fetch outcomes persist across runs. Without this, a run WITHOUT --fetch
    # produces a shorter want-list than one with it, because the open-access
    # papers we already know we cannot download silently drop off the list.
    attempts = read_json(DATA / "fetch-attempts.json", {}) or {}
    fetched: dict[str, str] = read_json(DATA / "fetched-files.json", {}) or {}
    known_bad = [r for r in open_rows if attempts.get(r["doi"], {}).get("failed")]
    for r in known_bad:
        r["access"] = attempts[r["doi"]]["reason"]
    open_rows = [r for r in open_rows if not attempts.get(r["doi"], {}).get("failed")]

    print(f"  open access (auto-fetchable): {len(open_rows)}")
    print(f"  known unfetchable (listed):   {len(known_bad)}")
    print(f"  needs institutional access:   {len(paywalled)}")

    def write_wanted(rows: list[dict]) -> None:
        """Ranked and batched so it can be worked down 100 at a time. Nothing is
        truncated: the whole list is written, `batch` just says where to start.

        Written twice: a CSV for tooling, and an HTML page for actually doing the
        work, because clicking 177 links out of a spreadsheet is miserable.
        """
        rows = sorted(rows, key=lambda r: r["rank"])
        with (DATA / "wanted.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["batch", "rank", "doi", "title", "year", "journal",
                        "cited_by", "record_class", "access", "publisher_url",
                        "link", "why_included", "check"])
            for i, r in enumerate(rows):
                url = r["publisher_url"]
                w.writerow([i // BATCH + 1, i + 1, r["doi"], r["title"], r["year"],
                            r["journal"], r["cited_by"], r["record_class"],
                            r.get("access", "paywalled"), url,
                            # Renders as a clickable link in Excel and Numbers.
                            f'=HYPERLINK("{url}","open")',
                            r["reason"], r.get("check") or ""])
        print(f"wrote data/wanted.csv ({len(rows)} papers, "
              f"{(len(rows) + BATCH - 1) // BATCH} batches of {BATCH})")
        write_wanted_html(rows)

    # A paper with no abstract anywhere can never be screened relevant, so it would
    # never reach this list. data/fetch-to-screen.json names those whose titles make
    # them worth fetching; each is screened from its full text once it arrives, and
    # drops off here as soon as it is held or decided.
    to_screen = []
    for d, e in ((read_json(DATA / "fetch-to-screen.json", {}) or {}).get("papers") or {}).items():
        if d in held or (verdicts.get(d) or {}).get("verdict") in ("relevant", "not_relevant"):
            continue
        c = cands.get(d) or {}
        to_screen.append({
            "doi": d, "title": c.get("title") or e.get("title"),
            "year": c.get("year") or e.get("year"), "journal": c.get("journal") or e.get("journal"),
            "cited_by": None, "record_class": None, "reason": e.get("why"), "criterion": None,
            "publisher_url": c.get("publisher_url") or f"https://doi.org/{d}",
            "access": "to_screen", "rank": len(by_use) + len(to_screen) + 1,
        })
    if to_screen:
        print(f"  fetch to screen (no abstract): {len(to_screen)}")

    write_wanted(paywalled + known_bad + to_screen)

    if unspecified:
        with (DATA / "needs-decision.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["doi", "title", "year", "journal", "reason"])
            for d in unspecified:
                c = cands.get(d, {})
                w.writerow([d, c.get("title"), c.get("year"), c.get("journal"),
                            verdicts[d].get("reason")])
        print(f"wrote data/needs-decision.csv ({len(unspecified)} for a person to resolve)")

    if not args.fetch:
        print("\nNo --fetch: nothing downloaded. Re-run with --fetch to pull "
              f"the {len(open_rows)} open-access PDFs into candidates/.")
        return 0

    # --- fetch open access ----------------------------------------------------
    CANDIDATES.mkdir(parents=True, exist_ok=True)
    got = skipped = 0
    unfetched: list[dict] = []
    todo = open_rows[: args.limit] if args.limit else open_rows

    for i, r in enumerate(todo, 1):
        stem = safe_stem(r, r["doi"])
        dest = CANDIDATES / f"{stem}.pdf"
        if dest.exists():
            skipped += 1
            continue
        why = None
        try:
            resp = client.get(r["oa_pdf_url"])
            ctype = resp.headers.get("content-type", "")
            if resp.status_code == 403:
                # Open access, but the publisher blocks automated requests
                # (observed on Elsevier journal sites, OUP, BMJ, IOP). That is an
                # access control; we do not work around it. A human with a
                # browser can simply open the link.
                why = "oa_but_publisher_blocks_automated_access"
            elif resp.status_code != 200:
                why = f"http_{resp.status_code}"
            elif "pdf" not in ctype.lower():
                why = "landing_page_not_pdf"
            else:
                dest.write_bytes(resp.content)
                ok, detail = pdf_is_the_paper(dest, r.get("title") or "")
                if ok:
                    got += 1
                    # Record WHICH FILE this DOI became. Only failures were
                    # recorded before, so a paper we downloaded ourselves --
                    # whose DOI is therefore known for certain -- landed in
                    # candidates/ with nothing linking the two, and identify.py
                    # had to re-derive it from the PDF. An arXiv preprint carries
                    # no DOI in its text at all, so that derivation just failed.
                    fetched[r["doi"]] = dest.name
                else:
                    dest.unlink()
                    why = f"fetched_wrong_document ({detail})"
        except Exception as e:
            why = f"error_{type(e).__name__}"

        if why:
            r["access"] = why
            unfetched.append(r)
        if i % 25 == 0:
            print(f"  {i}/{len(todo)}  got={got} unfetched={len(unfetched)} skipped={skipped}")
        time.sleep(0.2)

    print(f"\nfetched {got} PDFs into candidates/ "
          f"(could not fetch {len(unfetched)}, already had {skipped})")

    if unfetched:
        # These are in scope and open access but we could not download them.
        # They must land on the want-list -- otherwise they are neither fetched
        # nor listed, and silently disappear from the archive.
        for r in unfetched:
            attempts[r["doi"]] = {"failed": True, "reason": r["access"]}
        for r in todo:
            if r not in unfetched:
                attempts.pop(r["doi"], None)      # succeeded: forget any old failure
        write_json(DATA / "fetch-attempts.json", attempts)
    if fetched:
        write_json(DATA / "fetched-files.json", fetched)
        print(f"adding {len(unfetched)} unfetched open-access papers to the want-list")
        write_wanted(paywalled + known_bad + unfetched + to_screen)

    print("Next: uv run scripts/extract_text.py && uv run scripts/identify.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
