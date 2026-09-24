#!/usr/bin/env python3
"""Stage 7: find TUS papers the archive does not yet hold.

Two strategies, because neither alone has adequate recall:
  1. Query harvest across OpenAlex, Europe PMC and PubMed. The field's
     terminology is fragmented (TUS/tFUS/LIFU/LIFUP/ultrasonic neuromodulation),
     so a single phrase misses badly.
  2. Citation chaining from the reviews and consensus documents already held.
     This finds papers whose titles contain none of those phrases, which is
     exactly what query harvesting cannot do.

Output is a candidate list for screening. This script never fetches a PDF and
never attempts to bypass a paywall.

  uv run scripts/sweep.py [--no-chain] [--since YYYY-MM-DD] [--max-per-query N]
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time

import httpx

from common import DATA, MAILTO, read_json, write_json

OPENALEX = "https://api.openalex.org/works"
EPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
EPMC = f"{EPMC_BASE}/search"
PUBMED = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_SUM = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

# Neuromodulation phrases only. Deliberately no skull-acoustics, cavitation or
# transducer-design terms: INCLUSION.md E7 puts that enabling physics out of
# scope, and it is shared infrastructure across ablation, BBB opening, imaging
# and sonothrombolysis -- searching it pulls in all of those fields.
# Sonogenetics is likewise omitted (C5, excluded).
QUERIES = [
    "transcranial ultrasound stimulation",
    "transcranial focused ultrasound",
    "low-intensity focused ultrasound",
    "low intensity transcranial ultrasound",
    "ultrasound neuromodulation",
    "ultrasonic neuromodulation",
    "ultrasonic neurostimulation",
    "transcranial pulsed ultrasound",
    "focused ultrasound neuromodulation",
    "ultrasound brain stimulation",
    "acoustic neuromodulation",
    "transcranial ultrasonic stimulation",
    "low intensity focused ultrasound pulsation",
    "ultrasound neural stimulation",
    "focused ultrasound brain modulation",
    # Bare, so it also catches "Transcranial ultrasound (TUS) effects on ...",
    # which the longer exact phrases cannot match.
    "transcranial ultrasound",
]

# Europe PMC only: boolean title queries, which the other two sources do not
# take in this syntax. Exact phrases require the words adjacent and in order, so
# no phrase can reach "Focused ultrasound effects on nerve action potential" or
# "Ultrasound can modulate neuronal development". Each of these was measured
# against the papers the phrase-only sweep failed to re-find, and kept only if it
# actually recovered one; a dozen plausible-sounding phrases recovered nothing
# and were dropped rather than added on intuition.
EPMC_BOOLEAN = [
    '(TITLE:"ultrasound" AND TITLE:"stimulation")',
    '(TITLE:"ultrasound" AND TITLE:"nerve")',
    '(TITLE:"ultrasound" AND TITLE:"neural")',
    '(TITLE:"ultrasound" AND TITLE:"neuronal")',
    '(TITLE:"ultrasound" AND TITLE:"retinal")',
    '(TITLE:"ultrasound" AND TITLE:"cortex")',
]


def norm_doi(d: str | None) -> str | None:
    if not d:
        return None
    d = d.strip().lower()
    d = re.sub(r"^(https?://(dx\.)?doi\.org/)", "", d)
    return d if d.startswith("10.") else None


def strip_tags(s: str | None) -> str | None:
    if not s:
        return None
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())[:1400]


def inverted_to_text(inv: dict | None) -> str | None:
    """OpenAlex serves abstracts as an inverted index; rebuild the prose."""
    if not inv:
        return None
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))[:1400] or None


class Harvester:
    def __init__(self, max_per_query: int) -> None:
        self.client = httpx.Client(
            timeout=40.0, follow_redirects=True,
            headers={"User-Agent": f"tus-paper-archive/0.1 (mailto:{MAILTO})"},
        )
        self.max = max_per_query
        self.hits: dict[str, dict] = {}
        self.no_doi: list[dict] = []
        self.openalex_exhausted = False
        self.incomplete: list[str] = []
        self.coverage: list[dict] = []
        # Per query, what each source says it HAS -- so a harvest can be
        # compared against the truth instead of against our own limit.
        self.available: dict[str, dict] = {}

    def add(self, rec: dict, found_by: str) -> None:
        doi = norm_doi(rec.get("doi"))
        if not doi:
            rec["found_by"] = [found_by]
            self.no_doi.append(rec)
            return
        if doi in self.hits:
            fb = self.hits[doi]["found_by"]
            if found_by not in fb:
                fb.append(found_by)
            # Keep whichever copy has an abstract; screening needs one.
            if not self.hits[doi].get("abstract") and rec.get("abstract"):
                self.hits[doi]["abstract"] = rec["abstract"]
            return
        rec["doi"] = doi
        rec["found_by"] = [found_by]
        self.hits[doi] = rec

    # -- sources ------------------------------------------------------------
    def openalex(self, query: str, since: str | None) -> int:
        if self.openalex_exhausted:
            return 0
        n, cursor = 0, "*"
        flt = f"title_and_abstract.search:{query}"
        if since:
            flt += f",from_publication_date:{since}"
        while cursor and n < self.max:
            j = None
            for attempt in range(4):
                try:
                    r = self.client.get(OPENALEX, params={
                        "filter": flt, "per-page": 200, "cursor": cursor,
                        "mailto": MAILTO,
                    })
                    if r.status_code == 429:
                        # OpenAlex is metered (daily budget, resets midnight UTC).
                        # A quota failure must NOT look like "no more results" --
                        # that is how the first uncapped run silently returned
                        # less than the capped one.
                        if "budget" in r.text.lower():
                            self.openalex_exhausted = True
                            print("    *** OpenAlex DAILY BUDGET EXHAUSTED -- "
                                  "results incomplete. Resets midnight UTC. ***",
                                  file=sys.stderr)
                            return n
                        time.sleep(3.0 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    j = r.json()
                    break
                except httpx.HTTPStatusError as e:
                    if e.response.status_code >= 500:
                        time.sleep(2.0 * (attempt + 1))
                        continue
                    print(f"    openalex HTTP {e.response.status_code}", file=sys.stderr)
                    return n
                except Exception as e:
                    print(f"    openalex {type(e).__name__}, retrying", file=sys.stderr)
                    time.sleep(2.0 * (attempt + 1))
            if j is None:
                print(f"    openalex gave up on {query!r} after retries", file=sys.stderr)
                self.incomplete.append(f"openalex:{query}")
                break
            for w in j.get("results", []):
                loc = w.get("best_oa_location") or w.get("primary_location") or {}
                self.add({
                    "doi": w.get("doi"),
                    "title": w.get("display_name"),
                    "year": w.get("publication_year"),
                    "journal": ((w.get("primary_location") or {}).get("source") or {}
                                ).get("display_name"),
                    "type": w.get("type"),
                    "abstract": inverted_to_text(w.get("abstract_inverted_index")),
                    "oa_pdf_url": (w.get("best_oa_location") or {}).get("pdf_url"),
                    "is_oa": bool(w.get("open_access", {}).get("is_oa")),
                    "publisher_url": loc.get("landing_page_url"),
                    "cited_by": w.get("cited_by_count"),
                }, f"openalex:{query}")
                n += 1
            cursor = (j.get("meta") or {}).get("next_cursor")
            time.sleep(0.15)
        return n

    def europepmc(self, query: str) -> int:
        """Page with cursorMark. `page` is silently ignored by this API.

        Measured: pages 1, 6, 12 and 30 of the same query all return the
        identical first 100 records. The old loop therefore harvested 100
        records, re-added those same 100 fifty times because the counter counted
        results rather than new records, reached max_per_query and stamped the
        query "CAPPED, RESULTS TRUNCATED". Every one of the fifteen queries
        recorded epmc=5000. The truth was 100 each, against real hitCounts of
        178 to 1384 -- so the flag said "we saw too much to fit" when what
        happened was we saw the first fifth and stopped.

        cursorMark is the documented deep-paging mechanism and terminates
        exactly on hitCount. Counting only records new to this run is what makes
        the cap flag mean something.
        """
        n, cursor, seen_pages = 0, "*", 0
        self.available[query] = self.available.get(query, {})
        while n < self.max:
            try:
                r = self.client.get(EPMC, params={
                    # A boolean query is passed through as written; a plain
                    # phrase is quoted so it stays an exact-phrase search.
                    "query": query if query.startswith("(") else f'"{query}"',
                    "format": "json",
                    "pageSize": 100, "cursorMark": cursor, "resultType": "core",
                })
                r.raise_for_status()
                j = r.json()
                res = j.get("resultList", {}).get("result", [])
            except Exception as e:
                print(f"    epmc error {type(e).__name__}", file=sys.stderr)
                break
            self.available[query]["epmc"] = j.get("hitCount")
            if not res:
                break
            for w in res:
                self.add({
                    "doi": w.get("doi"),
                    "title": w.get("title"),
                    "year": int(w["pubYear"]) if str(w.get("pubYear", "")).isdigit() else None,
                    "journal": (w.get("journalInfo") or {}).get("journal", {}).get("title"),
                    "type": w.get("pubType"),
                    "abstract": strip_tags(w.get("abstractText")),
                    "pmid": w.get("pmid"),
                    "is_oa": w.get("isOpenAccess") == "Y",
                }, f"epmc:{query}")
                n += 1
            nxt = j.get("nextCursorMark")
            seen_pages += 1
            # No cursor, or a cursor that does not move, means the end. Without
            # this the loop is the bug it replaced.
            if not nxt or nxt == cursor:
                break
            cursor = nxt
            time.sleep(0.15)
        return n

    def pubmed(self, query: str) -> int:
        # retmax was pinned at 400, and several queries returned exactly 400 --
        # the signature of a self-inflicted cap, not of a small result set.
        # esearch reports the true count, so page with retstart until we have it.
        ids: list[str] = []
        try:
            while len(ids) < self.max:
                r = self.client.get(PUBMED, params={
                    "db": "pubmed", "term": f'"{query}"', "retmode": "json",
                    "retmax": 500, "retstart": len(ids),
                })
                r.raise_for_status()
                esr = r.json()["esearchresult"]
                self.available.setdefault(query, {})["pubmed"] = int(esr.get("count", 0))
                batch = esr.get("idlist", [])
                if not batch:
                    break
                ids.extend(batch)
                if len(ids) >= int(esr.get("count", 0)):
                    break
                time.sleep(0.2)
        except Exception as e:
            print(f"    pubmed error {type(e).__name__}", file=sys.stderr)
            if not ids:
                return 0
        n = 0
        for i in range(0, len(ids), 200):
            chunk = ids[i:i + 200]
            try:
                r = self.client.get(PUBMED_SUM, params={
                    "db": "pubmed", "id": ",".join(chunk), "retmode": "json",
                })
                r.raise_for_status()
                res = r.json().get("result", {})
            except Exception:
                continue
            for pmid in chunk:
                w = res.get(pmid)
                if not isinstance(w, dict):
                    continue
                doi = next((a["value"] for a in w.get("articleids", [])
                            if a.get("idtype") == "doi"), None)
                yr = re.match(r"(\d{4})", w.get("pubdate", "") or "")
                self.add({
                    "doi": doi, "title": w.get("title"), "pmid": pmid,
                    "year": int(yr.group(1)) if yr else None,
                    "journal": w.get("fulljournalname"),
                    "type": ", ".join(w.get("pubtype", []) or []),
                    "abstract": None,
                }, f"pubmed:{query}")
                n += 1
            time.sleep(0.2)
        return n

    # -- citation chaining, via Europe PMC ----------------------------------

    def _epmc_id(self, doi: str) -> tuple[str, str] | None:
        """The Europe PMC (source, id) pair for a DOI."""
        try:
            j = self.client.get(EPMC, params={
                "query": f'DOI:"{doi}"', "format": "json", "pageSize": 1,
            }).json()
            r = (j.get("resultList") or {}).get("result") or []
        except Exception:
            return None
        if not r:
            return None
        src, rid = r[0].get("source"), r[0].get("id")
        return (src, rid) if src and rid else None

    def _linked_ids(self, src: str, rid: str, endpoint: str) -> set[str]:
        """PMIDs of a paper's references, or of the papers citing it.

        Unlike /search, these endpoints honour `page` -- measured on a paper with
        52 references: pages of 20, 20 and 12, each with a different first id.
        Entries with no id are references Europe PMC does not index (books,
        theses); they are skipped rather than counted as failures.
        """
        out: set[str] = set()
        key = "referenceList" if endpoint == "references" else "citationList"
        item = "reference" if endpoint == "references" else "citation"
        for page in range(1, 8):                      # 700 links per direction
            try:
                j = self.client.get(
                    f"{EPMC_BASE}/{src}/{rid}/{endpoint}",
                    params={"format": "json", "pageSize": 100, "page": page},
                ).json()
                rows = (j.get(key) or {}).get(item) or []
            except Exception as e:
                k = f"{endpoint}_error_{type(e).__name__}"
                self.chain_failures[k] = self.chain_failures.get(k, 0) + 1
                break
            if not rows:
                break
            for x in rows:
                i = str(x.get("id") or "")
                if i.isdigit():
                    out.add(i)
            if len(rows) < 100:
                break
            time.sleep(0.1)
        return out

    def chain(self, seed_dois: list[str]) -> int:
        """References and citing works of the seeds, via Europe PMC.

        The higher-recall half of the sweep: it reaches papers that use none of
        our query phrases in their titles or abstracts -- Fry 1958 is titled
        "Production of Reversible Changes in the Central Nervous System" and
        contains neither "ultrasound" nor any phrase we search for.

        This used OpenAlex, which is metered: the free tier runs out part-way
        through a single sweep and returns 429 for the rest of the day, so
        chaining either never ran or ran on a fraction of the seeds. Europe PMC's
        reference and citation endpoints are key-free and unmetered, which makes
        chaining something that happens every run rather than something that
        happens if the budget holds.
        """
        self.chain_failures: dict[str, int] = {}
        pmids: set[str] = set()
        for seed in seed_dois:
            hit = self._epmc_id(seed)
            if not hit:
                self.chain_failures["seed_not_in_epmc"] = (
                    self.chain_failures.get("seed_not_in_epmc", 0) + 1)
                continue
            src, rid = hit
            pmids |= self._linked_ids(src, rid, "references")
            pmids |= self._linked_ids(src, rid, "citations")
            time.sleep(0.1)

        # Resolve the PMIDs to records in batches. One search per 40 ids rather
        # than one per id.
        ids = sorted(pmids)
        n = 0
        for i in range(0, len(ids), 40):
            batch = ids[i:i + 40]
            q = " OR ".join(f"EXT_ID:{x}" for x in batch)
            try:
                j = self.client.get(EPMC, params={
                    "query": f"({q})", "format": "json",
                    "pageSize": 100, "resultType": "core",
                }).json()
                rows = (j.get("resultList") or {}).get("result") or []
            except Exception as e:
                k = f"resolve_error_{type(e).__name__}"
                self.chain_failures[k] = self.chain_failures.get(k, 0) + 1
                continue
            for w in rows:
                self.add({
                    "doi": w.get("doi"),
                    "title": w.get("title"),
                    "year": int(w["pubYear"]) if str(w.get("pubYear", "")).isdigit() else None,
                    "journal": (w.get("journalInfo") or {}).get("journal", {}).get("title"),
                    "type": w.get("pubType"),
                    "abstract": strip_tags(w.get("abstractText")),
                    "pmid": w.get("pmid"),
                    "is_oa": w.get("isOpenAccess") == "Y",
                }, "epmc-chain")
                n += 1
            time.sleep(0.15)
        return n

    def _fetch_ids(self, pipe_ids: str, found_by: str) -> int:
        try:
            r = self.client.get(OPENALEX, params={
                "filter": f"openalex_id:{pipe_ids}", "per-page": 50, "mailto": MAILTO,
            })
            if r.status_code != 200:
                return 0
            for w in r.json().get("results", []):
                self._add_openalex(w, found_by)
            time.sleep(0.15)
            return len(r.json().get("results", []))
        except Exception:
            return 0

    def _add_openalex(self, w: dict, found_by: str) -> None:
        loc = w.get("best_oa_location") or w.get("primary_location") or {}
        self.add({
            "doi": w.get("doi"), "title": w.get("display_name"),
            "year": w.get("publication_year"),
            "journal": ((w.get("primary_location") or {}).get("source") or {}
                        ).get("display_name"),
            "type": w.get("type"),
            "abstract": inverted_to_text(w.get("abstract_inverted_index")),
            "oa_pdf_url": (w.get("best_oa_location") or {}).get("pdf_url"),
            "is_oa": bool(w.get("open_access", {}).get("is_oa")),
            "publisher_url": loc.get("landing_page_url"),
            "cited_by": w.get("cited_by_count"),
        }, found_by)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="ISO date; incremental sweep for the monthly run")
    ap.add_argument("--no-chain", action="store_true")
    ap.add_argument("--replace", action="store_true",
                    help="discard the previous harvest instead of merging into it")
    ap.add_argument("--use-openalex", action="store_true",
                    help="opt in to OpenAlex. Off by default: the free tier is "
                         "metered, runs out part-way through one sweep and then "
                         "returns 429 for the rest of the day, so it made "
                         "coverage depend on the time of day. Europe PMC and "
                         "PubMed carry the harvest; Europe PMC carries chaining.")
    ap.add_argument("--skip-openalex", action="store_true",
                    help="Europe PMC + PubMed only. Use when the OpenAlex "
                         "daily budget is spent.")
    ap.add_argument("--max-per-query", type=int, default=5000,
                    help="per-query result cap. The default is deliberately high: "
                         "a low cap silently biases coverage toward older, "
                         "more-cited work.")
    args = ap.parse_args()

    ingest = read_json(DATA / "ingest.json", {}) or {}
    manifest = read_json(DATA / "manifest.json", {}) or {}
    excluded = read_json(DATA / "excluded.json", {}) or {}

    held = {r["doi"] for r in ingest.get("records", {}).values() if r.get("doi")}
    held |= {p["doi"] for p in manifest.get("papers", {}).values() if p.get("doi")}
    decided = {p["doi"] for p in excluded.get("papers", [])}
    print(f"already held: {len(held)}   previously excluded: {len(decided)}")

    h = Harvester(args.max_per_query)

    print("\n--- query harvest ---")
    capped = []
    for q in QUERIES:
        a = h.openalex(q, args.since) if args.use_openalex else 0
        b = h.europepmc(q)
        c = h.pubmed(q)
        avail = h.available.get(q, {})
        # A cap is real only when a source says it holds more than we took.
        # Comparing the count against our own max_per_query is what produced
        # fifteen queries all reporting epmc=5000 while each had fetched 100.
        short = {src: avail[src] for src, got in (("epmc", b), ("pubmed", c))
                 if avail.get(src) and got < avail[src]}
        hit_cap = bool(short) or a >= args.max_per_query
        h.coverage.append({"query": q, "openalex": a, "epmc": b, "pubmed": c,
                           "available": avail, "hit_cap": hit_cap,
                           "short_of": short})
        if hit_cap:
            capped.append(q)
        flag = ("  << SHORT: " + ", ".join(f"{k} {short[k]} available" for k in short)
                if short else ("  << OPENALEX CAP" if hit_cap else ""))
        print(f"  {q:44s} oa={a:4d} epmc={b:4d} pm={c:4d}  "
              f"unique={len(h.hits)}{flag}")

    print("\n--- europe pmc boolean title queries ---")
    for q in EPMC_BOOLEAN:
        before = len(h.hits)
        b = h.europepmc(q)
        avail = h.available.get(q, {})
        h.coverage.append({"query": q, "openalex": 0, "epmc": b, "pubmed": 0,
                           "available": avail, "hit_cap": False,
                           "epmc_only": True})
        print(f"  {q:52s} epmc={b:5d}  new={len(h.hits) - before:4d}  "
              f"unique={len(h.hits)}")

    # A silent cap reads as complete coverage. It is not: OpenAlex returns
    # results in its own order, which is not year-neutral, so truncating drops
    # papers non-randomly -- and in practice it drops the least-cited, which
    # means the most recent. The first run capped 14 of 15 queries at 600 while
    # single queries genuinely return 2,500-4,000 works, and the resulting
    # library covered 2019-2024 at ~30% but 2025-2026 at ~8%.
    if capped:
        print(f"\n  *** {len(capped)} of {len(QUERIES)} queries HIT THE CAP of "
              f"{args.max_per_query}. Coverage is incomplete and biased. ***")
        print("  Re-run with a much higher --max-per-query (or no cap) before")
        print("  treating this harvest as representative.")

    if not args.no_chain:
        # Seeds, in order of reference-list density, merged rather than tried in turn: the
        # title grep alone finds too few documents to chain from, since reviews are not records.
        kd = (read_json(DATA / "key-documents.json", {}) or {}).get("documents", {})
        seeds = list(kd)                       # the reviews and consensus papers
        for p_ in manifest.get("papers", {}).values():
            d = p_.get("doi")
            if d and d not in seeds and (
                    p_.get("scope_class") == "supporting_safety_dosimetry"
                    or re.search(r"review|consensus|systematic|guide|meta-analys",
                                 (p_.get("title") or ""), re.I)):
                seeds.append(d)
        # Heavily-cited papers are chained TO by the work we are looking for.
        for p_ in sorted((x for x in manifest.get("papers", {}).values() if x.get("doi")),
                         key=lambda x: x.get("cited_by") or 0, reverse=True)[:30]:
            if p_["doi"] not in seeds:
                seeds.append(p_["doi"])

        print(f"\n--- citation chaining from {len(seeds)} seed papers ---")
        n = h.chain(seeds)
        print(f"  visited {n} related works; total unique now {len(h.hits)}")
        if getattr(h, "chain_failures", None):
            print(f"  seed lookups that failed: {h.chain_failures}")

    new = {d: r for d, r in h.hits.items() if d not in held and d not in decided}
    print(f"\nunique DOIs seen   {len(h.hits)}")
    print(f"already held/decided {len(h.hits) - len(new)}")
    print(f"NEW to screen      {len(new)}")
    print(f"hits with no DOI   {len(h.no_doi)} (not carried forward)")

    # Recall check. Measured against INCLUDED papers only: papers we screened out
    # (skull acoustics, cavitation, sonogenetics) are ones the query set is
    # *supposed* to miss, so counting them would manufacture a permanent warning.
    included = {p["doi"] for p in manifest.get("papers", {}).values() if p.get("doi")}
    target = included or held
    label = "included" if included else "held (manifest not built yet)"
    # Measured against this run's hits, deliberately. The accumulated candidate
    # file is the to-screen queue and EXCLUDES everything already held, so
    # scoring recall against it returns 0/350 and means nothing. A run where one
    # source is out of budget will score low, and should: that is the fact the
    # check exists to surface.
    seen_dois = set(h.hits)
    refound = len(target & seen_dois)
    missed = sorted(target - seen_dois)

    if target:
        pct = 100 * refound / len(target)
        print(f"\nRECALL CHECK: re-found {refound}/{len(target)} {label} papers ({pct:.0f}%)")
        if pct < 95:
            print("  WARNING: below the 95% target -- the query set is too narrow.")
        # Naming the misses is what makes this actionable; a bare percentage is not.
        if missed:
            print(f"  {len(missed)} not re-found:")
            byname = {p["doi"]: p for p in manifest.get("papers", {}).values()}
            for d in missed[:20]:
                t = (byname.get(d, {}).get("title") or "")[:62]
                print(f"    {d}  {t}")

    # ACCUMULATE, never replace. Sources are complementary, not redundant:
    # Europe PMC's exact-phrase search finds papers OpenAlex misses and vice
    # versa (a PMC+PubMed-only run found 566 OpenAlex had not, while its recall
    # against our own library was 68% vs OpenAlex's 93%). A partial run --
    # OpenAlex out of budget, a source timing out -- must not destroy prior
    # harvest. Pass --replace only to deliberately start over.
    if not args.replace:
        prior = (read_json(DATA / "sweep-candidates.json", {}) or {}).get("candidates", {})
        merged = 0
        for d, rec in prior.items():
            if d in new:
                continue
            if d not in held and d not in decided:
                new[d] = rec
                merged += 1
        if merged:
            print(f"merged {merged} candidates from the previous harvest "
                  f"(total now {len(new)})")

    write_json(DATA / "sweep-candidates.json", {
        "note": "Raw sweep output awaiting screening, ACCUMULATED across runs. "
                "Not a want-list yet.",
        "coverage": h.coverage,
        "recall_check": {
            "measured_against": label, "target": len(target),
            "refound": refound, "missed": missed,
        },
        "count": len(new),
        "candidates": dict(sorted(new.items())),
    })
    print(f"\nwrote data/sweep-candidates.json ({len(new)} to screen)")
    print("Next: screen with paper-hunter/paper-screener, then write data/wanted.csv.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
