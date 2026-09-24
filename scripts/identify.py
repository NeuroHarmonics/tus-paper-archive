#!/usr/bin/env python3
"""Resolve each candidate PDF to an authoritative bibliographic record.

Deterministic path: DOI from the PDF text -> Crossref. No model involved, because
bibliographic metadata is a lookup, not a judgement. ~92% of this corpus resolves
this way; the rest are listed for the paper-identifier agent or a human.

Two things this script must get right, because nothing downstream can recover them:
  * a DOI scraped from a *reference* rather than the paper itself -- caught by
    checking the Crossref title against the PDF's own embedded title;
  * errata and supplementary files, which share a parent's title and would
    otherwise be silently deduplicated into it (finding F2).

  uv run scripts/identify.py [--limit N] [--refresh]
"""

from __future__ import annotations

import argparse
import difflib
import html
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

def _clean_text(s):
    """Crossref serves titles and journal names with HTML entities and tags ("&amp;", "<i>", "<scp>")."""
    if not isinstance(s, str):
        return s
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split()) or None


from common import (  # noqa: F401
    compact_title,
    CANDIDATES, DATA, DOI_RE, LIBRARY, MAILTO, TEXT, TEXT_INDEX, text_path,
    clean_doi, normalise_title, read_json, write_json,
)

CROSSREF = "https://api.crossref.org/works/{doi}"
CACHE_PATH = DATA / "crossref-cache.json"

SUPP_PAT = re.compile(
    r"supplement(?:ary|al)?|supporting[ _-]?information|^appendix\b", re.I
)


def find_pdf(name: str) -> Path | None:
    """Locate a PDF by filename in either drop zone.

    Files move from candidates/ to library/ once filed. Looking only in
    candidates/ meant an already-filed paper had no embedded title to check
    against, which silently disabled the whole title-verification path -- and
    re-resolved Fouragnan 2026 and Zubair 2026 to the DOI of the paper they
    reply to.
    """
    for base in (CANDIDATES, LIBRARY, LIBRARY / "_duplicates",
                 LIBRARY / "_out_of_scope"):
        p = base / name
        if p.exists():
            return p
    return None


def xmp_title(pdf: Path | None) -> str | None:
    """The PDF's own embedded title. Present and correct in ~231/252 files here,
    which makes it a good independent check on a scraped DOI."""
    if pdf is None:
        return None
    try:
        out = subprocess.run(
            ["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=30
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if line.startswith("Title:"):
            t = line.split(":", 1)[1].strip()
            return t or None
    return None


def page_texts(text_key: str, n: int = 3) -> list[str]:
    """First n pages, split on the form feeds that separate them.

    The text is MinerU markdown now rather than pdftotext output, but the page
    separator is unchanged: pdf_markdown.render() writes a form feed between
    pages precisely so this function keeps working. Journal headers on page 1
    are kept for the same reason -- that line is where most DOIs live.

    Falls back to a legacy .txt, then to any same-stem file, so an ingest.json
    written before the markdown switch still resolves. A missing text file
    silently yields "no DOI found", which is the failure mode this guards
    against.
    """
    p = text_path(text_key)
    if not p.exists():
        alt = [TEXT / f"{text_key}.txt", *(f for f in TEXT.glob("*") if f.stem == text_key)]
        alt = [f for f in alt if f.exists()]
        if not alt:
            return []
        p = alt[0]
    return p.read_text(encoding="utf-8", errors="replace").split("\f")[:n]


def find_dois(pages: list[str]) -> list[str]:
    """DOIs in page order, deduplicated, page 1 first.

    Order matters: a paper's own DOI is almost always in the page-1 header or
    footer, whereas reference-list DOIs appear later. We try candidates in this
    order and stop at the first that corroborates the embedded title.
    """
    seen, out = set(), []
    for page in pages:
        for m in DOI_RE.finditer(page):
            d = clean_doi(m.group(1))
            if d and d not in seen:
                seen.add(d)
                out.append(d)
    return out


class Crossref:
    def __init__(self) -> None:
        self.cache: dict = read_json(CACHE_PATH, {}) or {}
        self.client = httpx.Client(
            timeout=25.0,
            headers={"User-Agent": f"tus-paper-archive/0.1 (mailto:{MAILTO})"},
            follow_redirects=True,
        )
        self.calls = 0

    def get(self, doi: str) -> dict | None:
        if doi in self.cache:
            return self.cache[doi]
        try:
            r = self.client.get(CROSSREF.format(doi=doi), params={"mailto": MAILTO})
            self.calls += 1
            time.sleep(0.12)  # polite pool; well inside Crossref's limits
            if r.status_code == 404:
                self.cache[doi] = None
                return None
            r.raise_for_status()
            msg = r.json().get("message")
        except Exception as e:
            print(f"    crossref error {doi}: {type(e).__name__}", file=sys.stderr)
            return None
        self.cache[doi] = msg
        return msg

    def save(self) -> None:
        write_json(CACHE_PATH, self.cache)


def year_from_doi(doi: str | None) -> int | None:
    """A plausible publication year embedded in the DOI suffix.

    Last resort only. Some conference records carry no `issued` date at all --
    10.1109/iembs.2004.1404176 is one -- and a paper with no year is filed as
    "nd_Nd_..." and sorts nowhere. The year is right there in the DOI.
    """
    if not doi:
        return None
    for m in re.finditer(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)", doi):
        return int(m.group(1))
    return None


def record_from_crossref(msg: dict) -> dict:
    """Project a Crossref work down to the fields we keep."""
    def first(key):
        v = msg.get(key) or []
        return v[0] if v else None

    issued = (msg.get("issued") or {}).get("date-parts") or [[None]]
    year = issued[0][0] if issued and issued[0] else None
    # Some records date only the online-first version; published-print is truer
    # to how the paper is cited.
    for alt in ("published-print", "published"):
        parts = (msg.get(alt) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break

    authors = []
    for a in msg.get("author") or []:
        fam, giv = a.get("family"), a.get("given")
        if fam:
            authors.append({"family": fam, "given": giv})
        elif a.get("name"):
            authors.append({"family": a["name"], "given": None})

    return {
        "doi": (msg.get("DOI") or "").lower() or None,
        "title": _clean_text(first("title")),
        "authors": authors,
        "journal": _clean_text(first("container-title")),
        "year": year,
        "volume": msg.get("volume"),
        "pages": msg.get("page"),
        "publisher": msg.get("publisher"),
        "crossref_type": msg.get("type"),
        "url": msg.get("URL"),
        "is_preprint": msg.get("subtype") == "preprint" or msg.get("type") == "posted-content",
        # 'update-to' is how Crossref marks an erratum pointing at its parent.
        # This is the deterministic erratum signal that finding F2 requires.
        "update_to": [
            {"doi": (u.get("DOI") or "").lower(), "type": u.get("type")}
            for u in (msg.get("update-to") or [])
            if u.get("DOI")
        ],
        "abstract_present": bool(msg.get("abstract")),
    }


# XMP Title fields are frequently typesetting junk rather than the paper's title.
# Every pattern here was observed in this corpus; comparing against these produces
# false mismatches that bury good records in review.
XMP_JUNK = re.compile(
    r"""^\s*(
          untitled | none | unknown | microsoft\ word.* | using\ [a-z]+\ format
        | doi:.* | .*\.(pdf|indd|tex|dvi|qxd)\s*$
        | [a-z]{0,6}[._-]?\d{4,}.*          # pone.0086939 1..13, PNAS202206828, 16075954867795
        | \d[\d\s.]*                        # bare numbers, e.g. 100827
        | .*\b\d+\.\.\d+\s*$                # trailing page ranges "1..30"
        )\s*$""",
    re.I | re.X,
)

# Crossref titles that indicate we resolved something other than the article:
# a journal-level record, or one of eLife's sub-records which carry their own DOIs.
CR_TITLE_SUSPECT = re.compile(
    r"^\s*(abstract|untitled|front matter|back matter|editorial board"
    r"|elife assessment|author response|peer review|reviewer \#|editor'?s evaluation)\b",
    re.I,
)

CR_TYPES_OK = {
    "journal-article", "posted-content", "proceedings-article",
    "book-chapter", "report", "dissertation", "other",
}


# Elsevier stamps the XMP Title of a supplementary file with its internal name,
# "1-s2.0-S1935861X24000858-mmc1" or plain "mmc1". Four such files sat in the
# needs_identifier_agent queue -- a queue that costs a model call each -- when the
# filename pattern had not caught them but their own metadata said plainly what
# they were. Their parents were held and they were already filed as _supp.pdf.
ELSEVIER_SUPP = re.compile(r"(?:^|[-_])mmc\d+$", re.I)

# The other Elsevier stamp is the PII of the article itself. For pre-2003
# articles the PII *is* the DOI suffix: "PII: 0014-4886(87)90073-2" is exactly
# 10.1016/0014-4886(87)90073-2. That is the whole identity of Ellisman 1987,
# sitting unread in the metadata while the want-list kept asking for
# a paper already in the library.
#
# Only the old bracketed form is derivable. The modern "S1935861X24000858" form
# needs a lookup we cannot do key-free, so it is left alone.
# The trailing character is a check character, and it is not restricted to
# digits: "PII: 0301-5629(90)90008-Z" is a real one from this corpus, and an
# earlier [\dXx] class rejected it, sending a paper whose identity was written
# plainly in its own metadata to the fallback queue.
ELSEVIER_PII = re.compile(
    r"^PII:\s*(\d{4}-\d{3}[\dXx]\(\d{2}\)\d{5}-[\dA-Za-z])\s*$")


# Publisher names that turn up where a title belongs. Matched case-insensitively
# and anywhere in the string, because the stamp is sometimes decorated.
PUBLISHER_XMP = re.compile(
    r"lippincott|williams\s*(&|and)\s*wilkins|wolters\s*kluwer|elsevier"
    r"|springer|wiley|taylor\s*(&|and)\s*francis|sage\s+publications"
    r"|oxford\s+university\s+press|cambridge\s+university\s+press"
    r"|lww\b|karger|mary\s+ann\s+liebert|frontiers\s+media|mdpi|hindawi",
    re.I)


def _key_doc_for(filename: str, key_by_title: dict) -> dict | None:
    """Match a filed PDF to a key document by the title inside its filename."""
    parts = Path(filename).stem.split("_", 2)
    return key_by_title.get(parts[2].lower()) if len(parts) == 3 else None


def usable_xmp_title(t: str | None) -> str | None:
    """The embedded title, or None when it is typesetting junk.

    Returning None means "cannot verify", which is honest. Treating junk as a real
    title would mean 'disagreement' and would flag ~16 correctly-resolved papers.
    """
    if not t:
        return None
    t = t.strip()
    if len(t) < 12 or XMP_JUNK.match(t):
        return None
    # A real title has several word-like tokens.
    if len([w for w in re.findall(r"[A-Za-z]{2,}", t)]) < 3:
        return None
    # Some publishers stamp their own name into XMP Title. It passes every test
    # above -- long enough, several words, not obviously debris -- and then
    # scores ~0.15 against the real title, so a correctly-resolved paper is
    # reported as a wrong-DOI grab. "LIPPINCOTT WILLIAMS AND WILKINS" did exactly
    # that to a J ECT paper whose DOI was printed correctly on its own front page.
    if PUBLISHER_XMP.search(t):
        return None
    return t


def crossref_record_problems(proj: dict) -> list[str]:
    """Sanity-check the Crossref record itself.

    Necessary because when the embedded title is junk we have nothing to compare
    against -- so the record must be able to condemn itself. This is what catches
    a journal-level DOI or an eLife assessment DOI scraped from the front page.
    """
    problems = []
    title = (proj.get("title") or "").strip()
    if not title:
        problems.append("crossref_no_title")
    elif CR_TITLE_SUSPECT.match(title):
        problems.append("crossref_title_is_not_an_article_title")
    elif len(title) < 15:
        problems.append("crossref_title_implausibly_short")
    if proj.get("journal") and normalise_title(title) == normalise_title(proj["journal"]):
        problems.append("crossref_title_equals_journal_name")
    if proj.get("crossref_type") and proj["crossref_type"] not in CR_TYPES_OK:
        problems.append(f"crossref_type_{proj['crossref_type']}")
    return problems


# A reply, comment or correction quotes its target's title in full, so it is a
# near-perfect string match for a *different* work. This corpus contains an entire
# letter exchange of them (the substance-use-disorder adverse-event thread), so the
# distinction has to be explicit.
REPLY_PREFIX = re.compile(
    r"""^\s*(?:
          (?: response | reply | comment(?:ary)? | correction | corrigendum
            | erratum | letter | author\s+response )\b [\s:,-]*
          (?: to | on | re | regarding | concerning )? \b
        | re (?= \s*[:.] )        # bare "Re:" -- e.g. Zubair 2026
        )""",
    re.I | re.X,
)


def title_agreement(a: str | None, b: str | None) -> float:
    """How well two titles agree, 0..1, or -1 when one side is unavailable.

    Used to reject a DOI scraped from a reference: its Crossref title will not
    match this paper's own title.
    """
    na, nb = normalise_title(a), normalise_title(b)
    if not na or not nb:
        return -1.0  # unknown, not disagreement

    # "Response to: X" versus "X" are different works. Without this, the
    # containment check below scores them 1.0 and we adopt the target's DOI --
    # observed with Fouragnan 2026 taking Rezai 2025's DOI.
    if bool(REPLY_PREFIX.match(a or "")) != bool(REPLY_PREFIX.match(b or "")):
        return 0.0

    if na == nb:
        return 1.0

    if na.startswith(nb) or nb.startswith(na):
        short, long = sorted((na, nb), key=len)
        if len(short) / len(long) >= 0.90:
            return 1.0
        # PDF producers routinely truncate the XMP Title mid-phrase (observed at
        # ~100 chars on Bancel 2025 and Pulkkinen 2011). A long prefix is
        # corroboration, not disagreement -- and the reply-prefix check above has
        # already separated out "Response to: X" versus "X", which is the case
        # this would otherwise wave through.
        if len(short) >= 40:
            return 0.97
    return difflib.SequenceMatcher(None, na, nb).ratio()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refresh", action="store_true", help="ignore existing ingest.json")
    args = ap.parse_args()

    tindex = read_json(TEXT_INDEX)
    if not tindex:
        sys.exit("Run scripts/extract_text.py first.")
    files = tindex["files"]

    prev = {} if args.refresh else (read_json(DATA / "ingest.json", {}) or {}).get("records", {})
    # The manifest is the curated, verified record of what each filed paper IS.
    # ingest.json is a cache in front of it, and a cache can be cleared -- when it
    # was, ten already-filed papers fell back to re-derivation and four of them
    # could not be re-derived, because their embedded metadata is junk
    # ("PNAS202206828_proof.pdf", "The Journal of Neuroscience Template"). Their
    # identity was never in doubt; it was sitting in the manifest.
    # A file WE fetched has a DOI known for certain, so it needs no derivation.
    # acquire.py records the mapping; without it an arXiv preprint, which carries
    # no DOI anywhere in its text, cannot be identified at all.
    sweep = (read_json(DATA / "sweep-candidates.json", {}) or {}).get("candidates", {})
    fetched_by_name = {v: k for k, v in
                       (read_json(DATA / "fetched-files.json", {}) or {}).items()}
    filed = {
        (p_.get("files") or {}).get("pdf"): p_
        for p_ in (read_json(DATA / "manifest.json", {}) or {}).get("papers", {}).values()
        if (p_.get("files") or {}).get("pdf")
    }
    # Key documents are deliberately NOT manifest records -- they are the reading
    # list -- so the map above cannot see them. Their identity is just as settled,
    # so index them by the compact title their filename is built from.
    key_by_title = {
        compact_title(kd.get("title") or "").lower(): {"doi": doi, **kd}
        for doi, kd in (read_json(DATA / "key-documents.json", {}) or {})
        .get("documents", {}).items() if kd.get("title")
    }
    cr = Crossref()
    records: dict[str, dict] = {}
    names = sorted(files)
    if args.limit:
        names = names[: args.limit]

    for i, name in enumerate(names, 1):
        if name in prev and prev[name].get("status") == "resolved" and not args.refresh:
            records[name] = prev[name]
            continue

        # Already filed and already verified? Take the curated identity and stop.
        # Re-deriving it is not free and not safe: a title search on Harvey 1929
        # returns the 1930 single-page abstract of the same title, and on Zou 2020
        # returns the SSRN preprint. pick_best() guards against exactly that with a
        # year tie-break, but only when it is given a year to break on. The
        # manifest already knows the answer.
        if name in fetched_by_name and name not in prev and name not in filed:
            doi_ = fetched_by_name[name]
            proj_ = None
            if (msg_ := cr.get(doi_)):
                proj_ = record_from_crossref(msg_)
            if not proj_:
                # arXiv, Zenodo and Dryad DOIs are registered with DataCite, not
                # Crossref, so a Crossref lookup returns nothing for them. The
                # sweep already holds the title and year -- that is the record.
                cand_ = sweep.get(doi_) or {}
                if cand_.get("title"):
                    proj_ = {k: cand_.get(k) for k in ("title", "year", "journal")
                             if cand_.get(k)}
            if proj_:
                rec_ = {"file": name, "text_key": files[name]["text_key"],
                        "status": "resolved", "doi_source": "acquire_fetch_log",
                        "flags": ["identity_from_fetch_log"],
                        "is_supplementary": False, **proj_}
                rec_["doi"] = doi_
                records[name] = rec_
                print(f"[{i}/{len(names)}] OK   {name[:56]}")
                continue

        if name in filed and name not in prev:
            m_ = filed[name]
            rec_ = {
                "file": name, "text_key": files[name]["text_key"],
                "status": "resolved", "doi_source": "manifest",
                "flags": ["identity_from_manifest"],
                "is_supplementary": False,
                **{k: m_[k] for k in ("doi", "title", "year", "journal", "authors")
                   if m_.get(k)},
            }
            # Authors matter beyond display: first_author() feeds both the
            # filename and the citekey, and without them a paper becomes
            # "1950_Anon_..." / "1950anon". The Crossref cache already holds them
            # locally, keyed by DOI, so there is no reason to lose them.
            if not rec_.get("authors") and rec_.get("doi"):
                # cr.get(), not cr.cache.get(): the cache is warm for papers this
                # run resolved from text, and cold for exactly the pre-DOI papers
                # that most need the lookup. Crossref has Harvey 1929 and Fry 1951
                # even though neither PDF carries a DOI.
                msg_ = cr.get(rec_["doi"]) or {}
                auth = [{"family": a.get("family") or a.get("name"),
                         "given": a.get("given")}
                        for a in (msg_.get("author") or [])
                        if a.get("family") or a.get("name")]
                if auth:
                    rec_["authors"] = auth
                if not rec_.get("year"):
                    parts_ = (msg_.get("issued") or {}).get("date-parts") or [[None]]
                    rec_["year"] = parts_[0][0] or year_from_doi(rec_.get("doi"))
                if not rec_.get("journal"):
                    rec_["journal"] = (msg_.get("container-title") or [None])[0]
            records[name] = rec_
            print(f"[{i}/{len(names)}] OK   {name[:56]}")
            continue

        pdf = find_pdf(name)
        key = files[name]["text_key"]
        pages = page_texts(key)
        xt_raw = xmp_title(pdf)
        xt = usable_xmp_title(xt_raw)
        is_supp = bool(SUPP_PAT.search(name))

        rec: dict = {
            "file": name,
            "text_key": key,
            "xmp_title": xt_raw,
            "xmp_title_usable": xt is not None,
            "is_supplementary": is_supp,
            "status": "unresolved",
        }

        # Supplementary files carry their parent's DOI. Resolving them as papers
        # would create a duplicate record, so identity is deferred to rename.py,
        # which attaches them to the parent (INCLUSION.md C17).
        if not is_supp and xt_raw and ELSEVIER_SUPP.search(xt_raw.strip()):
            is_supp = True
            rec["is_supplementary"] = True
            rec["note_supp"] = f"Elsevier supplementary stamp in XMP: {xt_raw!r}"

        if is_supp:
            rec["status"] = "supplementary"
            rec["note"] = "attach to parent in rename.py; not a record of its own"
            records[name] = rec
            print(f"[{i}/{len(names)}] SUPP {name[:60]}")
            continue

        candidates = find_dois(pages)
        rec["dois_seen"] = candidates[:6]

        # Try DOIs in page order, preferring one that both looks like a sound
        # article record and corroborates the embedded title.
        best = None
        for d in candidates[:6]:
            msg = cr.get(d)
            if not msg:
                continue
            proj = record_from_crossref(msg)
            problems = crossref_record_problems(proj)
            score = title_agreement(proj["title"], xt)
            cand = (proj, score, d, problems)
            if not problems and score >= 0.85:
                best = cand           # verified: stop looking
                break
            if not problems and score < 0 and best is None:
                best = cand           # plausible, unverifiable: keep looking for better
            elif best is None:
                best = cand

        if best:
            proj, score, used, problems = best
            # A DOI that resolves to the wrong record is not the end of the road.
            # The paper's own title is usually right there, and searching on it
            # finds the article the scraped DOI was standing in front of. Without
            # this the run stops at needs_review and the paper silently leaves the
            # manifest -- which is what happened to four already-filed papers when
            # re-extraction changed their page text: an eLife component DOI, a
            # journal-level PNAS DOI, and two title mismatches.
            if (problems or (0 <= score < 0.85)) and \
                    (xt2 := usable_xmp_title(rec.get("xmp_title"))):
                from resolve_missing import parent_doi, pick_best, search_by_title
                items = search_by_title(cr.client, xt2, rows=5)
                scored = [(title_agreement(xt2, (it.get("title") or [""])[0]), it)
                          for it in items]
                alt = pick_best(scored, {"title": xt2})
                if alt and alt[1] >= 0.90:
                    amsg = alt[0]
                    if (par := parent_doi((amsg.get("DOI") or "").lower())):
                        amsg = cr.get(par) or amsg
                    aproj = record_from_crossref(amsg)
                    if not crossref_record_problems(aproj):
                        rec.update(aproj)
                        rec["status"] = "resolved"
                        rec["doi_source"] = "title_search_after_doi_mismatch"
                        rec["title_agreement"] = round(alt[1], 3)
                        rec["flags"] = ["recovered_from_bad_doi", f"rejected:{used}"]
                        records[name] = rec
                        print(f"[{i}/{len(names)}] OK   {name[:56]}")
                        continue
            rec.update(proj)
            rec["doi_source"] = "text+crossref"
            rec["title_agreement"] = None if score < 0 else round(score, 3)
            flags: list[str] = []

            if problems:
                # The record condemns itself -- a journal-level DOI, an eLife
                # assessment, or similar. This is the case the embedded-title
                # check cannot catch when the embedded title is junk.
                rec["status"] = "needs_review"
                flags += problems
                rec["note"] = (
                    f"DOI {used} resolves to a record that does not look like this "
                    f"article ({', '.join(problems)})."
                )
            elif score >= 0.85:
                rec["status"] = "resolved"
            elif score < 0:
                # Nothing to verify against. Accepted, but marked so a human can
                # sample it: this is the residual-risk bucket.
                rec["status"] = "resolved"
                flags.append("unverified_no_usable_embedded_title")
            else:
                # A resolving DOI whose title disagrees is the dangerous case:
                # most likely scraped from a reference. Never accept silently.
                rec["status"] = "needs_review"
                flags.append("title_mismatch")
                rec["note"] = (
                    f"DOI {used} resolves to a different title "
                    f"(agreement {score:.2f}); likely scraped from a reference."
                )

            if proj.get("update_to"):
                flags.append("erratum")          # deterministic, per finding F2
            if proj.get("is_preprint"):
                flags.append("preprint")
            if flags:
                rec["flags"] = flags
        elif (pii := ELSEVIER_PII.match((rec.get("xmp_title") or "").strip())):
            # The DOI is the PII with Elsevier's prefix in front. Still verified
            # against Crossref and the title, exactly as a DOI found in the text
            # would be -- derived is not the same as trusted.
            cand = f"10.1016/{pii.group(1).lower()}"
            msg = cr.get(cand)
            proj = record_from_crossref(msg) if msg else None
            if proj and not crossref_record_problems(proj):
                rec.update(proj)
                rec["status"] = "resolved"
                rec["doi_source"] = "elsevier_pii"
                rec["flags"] = ["resolved_via_elsevier_pii"]
                records[name] = rec
                print(f"[{i}/{len(names)}] OK   {name[:56]}")
                continue
            rec["status"] = "needs_identifier_agent"
            rec["note"] = f"XMP PII {cand} did not resolve in Crossref"

        elif (xt := usable_xmp_title(rec.get("xmp_title"))):
            # No DOI in the text, but the PDF's own XMP title is usable. Search
            # Crossref with it before spending a model call: this is exactly the
            # deterministic-first rule the project runs on, and the routing here
            # skipped straight past it. NIH author manuscripts are the common
            # case -- "nihms-1560530.pdf" carries no publisher DOI on page 1 but
            # does carry a correct XMP title, and sat in needs_identifier_agent
            # while the answer was already in the record.
            # Imported here, not at module scope: resolve_missing imports from
            # this module, so a top-level import is circular.
            from resolve_missing import parent_doi, pick_best, search_by_title

            items = search_by_title(cr.client, xt, rows=5)
            scored = [(title_agreement(xt, (it.get("title") or [""])[0]), it)
                      for it in items]
            best = pick_best(scored, {"title": xt})
            if best and best[1] >= 0.90:
                msg, score = best
                # A component record -- a JoVE video, an eLife assessment --
                # carries the article's exact title and so scores 1.0. Follow it
                # up to the article the component belongs to.
                if (par := parent_doi((msg.get("DOI") or "").lower())):
                    if (pmsg := cr.get(par)):
                        msg = pmsg
                proj = record_from_crossref(msg)
                if not crossref_record_problems(proj):
                    rec.update(proj)
                    rec["status"] = "resolved"
                    rec["doi_source"] = "xmp_title_search"
                    rec["title_agreement"] = round(score, 3)
                    rec["flags"] = ["resolved_via_xmp_title_search"]
                    records[name] = rec
                    print(f"[{i}/{len(names)}] OK   {name[:56]}")
                    continue
            rec["status"] = "needs_identifier_agent"
            rec["note"] = ("no DOI resolved and the XMP title did not match a "
                           "Crossref record; route to paper-identifier")
        else:
            rec["status"] = "needs_identifier_agent"
            rec["note"] = "no DOI resolved; route to paper-identifier then Crossref title search"

        # Last resort before giving up: this file is already filed under a name
        # the manifest knows, so take the identity the manifest already holds.
        if rec.get("status") in ("needs_review", "needs_identifier_agent") \
                and (name in filed or _key_doc_for(name, key_by_title)):
            m_ = filed.get(name) or _key_doc_for(name, key_by_title)
            rec.update({k: m_[k] for k in ("doi", "title", "year", "journal")
                        if m_.get(k)})
            rec["status"] = "resolved"
            rec["doi_source"] = "manifest"
            rec["flags"] = sorted(set(rec.get("flags", []) + ["identity_from_manifest"]))
            rec["note"] = ("Embedded metadata could not confirm this paper, but it "
                           "is already filed and the manifest holds its verified "
                           "identity.")

        records[name] = rec
        tag = {
            "resolved": "OK  ", "needs_review": "CHK ",
            "needs_identifier_agent": "MISS", "supplementary": "SUPP",
        }.get(rec["status"], "??  ")
        print(f"[{i}/{len(names)}] {tag} {name[:58]}")

    cr.save()

    by_status: dict[str, int] = {}
    for r in records.values():
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    write_json(DATA / "ingest.json", {
        "generated_by": "scripts/identify.py",
        "counts": by_status,
        "records": records,
    })

    print("\n--- identify summary ---")
    for k, v in sorted(by_status.items()):
        print(f"  {k:26s} {v}")
    print(f"  crossref calls made      {cr.calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
