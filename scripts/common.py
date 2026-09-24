"""Shared helpers. Paths, JSON I/O, citekeys, text normalisation."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = ROOT / "candidates"
LIBRARY = ROOT / "library"
DATA = ROOT / "data"
TEXT = DATA / "text"
# The extraction ledger (which PDF became which text, with hashes) lives beside the text in the
# private corpus repo, reached through the data/text symlink. Falls back to data/ when the corpus
# is not linked in; that copy is ignored by git.
TEXT_INDEX = (TEXT.resolve().parent if TEXT.is_symlink() else DATA) / "text-index.json"
PAPERS = DATA / "papers"
SCREENING = DATA / "screening.jsonl"  # one screening verdict per DOI; see load_screening()


def website_repo() -> Path | None:
    """The website repository checkout that receives the compiled site data.

    The archive site lives in that repo (pages/tus-archive) since 2026-09-22; build_site.py writes
    into its public/tus-archive/data/. $TUS_WEBSITE_REPO first, then a clone named `website` beside
    this repo. None if neither holds a package.json.
    """
    env = os.environ.get("TUS_WEBSITE_REPO")
    for cand in ([Path(env).expanduser()] if env else []) + [ROOT.parent / "website"]:
        if (cand / "package.json").exists():
            return cand
    return None


# Where build_site.py writes, relative to the website repo. Served at neuroharmonics.com/tus-archive/data/.
WEBSITE_DATA_REL = Path("public") / "tus-archive" / "data"

# Extracted full text is markdown, produced by MinerU. It was plain .txt from
# pdftotext until 2026-08-31; see docs/text-extraction.md for why that changed.
# Pages inside the markdown are still separated by form feeds, so anything that
# needs "the first three pages" splits on "\f" exactly as it always did.
TEXT_SUFFIX = ".md"


def text_path(stem: str):
    """The extracted text for a PDF stem. One name, two extensions."""
    return TEXT / f"{stem}{TEXT_SUFFIX}"


MAILTO = "brad@neuroharmonics.com"  # polite-pool identification for Crossref/OpenAlex

# A DOI in running text. Trailing punctuation is stripped separately: a naive greedy
# match swallows the full stop that ends the sentence, and sometimes a following word.
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)")

# Publisher boilerplate that looks like a DOI but identifies the journal or a
# reference, not this paper. Checked as a prefix against the captured DOI.
DOI_BLOCKLIST = (
    "10.1016/j.brs.0000",  # placeholder seen in some Elsevier proofs
)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    """Write pretty, stable JSON. Sorted keys so diffs are meaningful in review."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# Every dash Unicode offers that is not the ASCII hyphen a DOI is allowed.
DASHES = str.maketrans({c: "-" for c in
                        "\u2010\u2011\u2012\u2013\u2014\u2015\u2043\u2212\u00ad\ufe58\ufe63\uff0d"})


def clean_doi(raw: str) -> str | None:
    """Normalise a DOI captured from PDF text.

    PDF text extraction mangles DOIs in predictable ways, and each fix here
    corresponds to a real failure seen in this corpus.
    """
    d = raw.strip().lower()
    # Typographic dashes where a DOI can only contain an ASCII hyphen. Publishers
    # and PDF extraction both introduce them: one candidate arrived as
    # "10.3969/j.issn.1672\u20436731.2022.04.006" with U+2043 HYPHEN BULLET, which
    # resolves nowhere and matched nothing. Dashes have now caused three separate
    # failures here -- a scope pattern, a cover-title comparison, and this.
    d = d.translate(DASHES)
    d = d.rstrip(").,;:'\"]}>")           # sentence punctuation swallowed by the match
    d = re.sub(r"\s+", "", d)              # line-wrap inside the suffix
    # Trailing words glued on by a missing space, e.g. ".../j.brs.2024.04.013Received"
    d = re.sub(r"(received|accepted|published|available|downloaded|http).*$", "", d)
    if d.endswith("."):
        d = d[:-1]
    if any(d.startswith(b) for b in DOI_BLOCKLIST):
        return None
    if not re.fullmatch(r"10\.\d{4,9}/\S+", d):
        return None
    if len(d) < 12:  # implausibly short: almost certainly a truncated capture
        return None
    return d


def normalise_title(t: str | None) -> str:
    """Fold a title to a comparison key: lowercase alphanumerics only.

    Used for duplicate detection when a DOI is unavailable, and to cross-check a
    Crossref hit against the PDF's own embedded title.
    """
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", t.lower())


def surname_slug(surname: str) -> str:
    """ASCII, lowercase, alphabetic-only surname for a citekey.

    Hyphens and particles collapse: "Butts Pauly" -> buttspauly,
    "Jiménez-Gambín" -> jimenezgambin.
    """
    s = unicodedata.normalize("NFKD", surname)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", s.lower())


def compact_title(title: str, max_words: int = 8) -> str:
    """CamelCase compact title for filenames, matching the corpus convention."""
    stop = {
        "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on",
        "or", "the", "to", "with", "is", "are", "its", "via", "using", "based",
    }
    t = unicodedata.normalize("NFKD", title)
    t = "".join(c for c in t if not unicodedata.combining(c))
    # A title set entirely in capitals is a typesetting choice, not a title full
    # of acronyms. Left alone, the acronym rule below keeps every short word
    # uppercase and produces
    # "EFFECTHIGHFREQUENCYSOUNDWAVESHEARTMUSCLE", which cannot be read at all.
    # Lowercase it first so the normal capitalisation applies; mixed-case titles
    # are untouched, so a real acronym in one still survives.
    if t.isupper():
        t = t.title()
    words = re.findall(r"[A-Za-z0-9]+", t)
    kept = [w for w in words if w.lower() not in stop] or words
    out = []
    for w in kept[:max_words]:
        out.append(w if w.isupper() and len(w) <= 6 else w[:1].upper() + w[1:])
    return "".join(out)


def paper_stem(year, surname: str | None, title: str | None) -> str:
    """The filename for a paper, and for its extracted markdown.

        2021_Collins_InhibitoryThermalEffectsFocusedUltrasound

    Year first, so a directory listing sorts chronologically -- which is how this
    corpus is actually read.

    This is deliberately the ONLY place the convention is written. It used to be
    spelled out twice, in rename.py for in-scope papers and organise_files.py for
    out-of-scope ones, and the two drifted: 18 files reached library/_out_of_scope
    named "Unknown_" because only one of the copies knew how to fall back to the
    Crossref cache for an author. Two copies of a naming rule is two conventions
    wearing one name.

    Underscore separates the fields and never appears inside one: surname_slug
    collapses "Butts-Pauly" to "buttspauly" and compact_title strips punctuation,
    so a stem always splits into exactly three parts. `_supp` is appended by the
    caller for supplementary files.
    """
    fam = surname_slug(surname or "").capitalize() or "Unknown"
    return f"{year or 'nd'}_{fam}_{compact_title(title or 'Untitled')}"


def citekey_base(year, surname: str | None) -> str:
    """The citekey stem: 2014legon. A collision suffix is added by the caller.

    Year first to match paper_stem, so a citekey and a filename sort the same way
    and can be read against each other at a glance.
    """
    return f"{year or 'nd'}{surname_slug(surname or '') or 'anon'}"


# --- conference output -------------------------------------------------------
# All conference output is out (E8), for a clean boundary.
# Abstracts were already out under E8; this extends it to full proceedings papers,
# which were being split by accident -- 13 marked relevant against 161 excluded,
# and a 2004 IEMBS paper excluded by hand the same day another EMBC paper was
# included. A boundary that needs judgement gets applied inconsistently, so this
# is deterministic.

# IEEE conference series. IEEE also publishes journals (TBME, TUFFC, TMI) under
# the same 10.1109 prefix, so the series name is what distinguishes them.
_CONF_DOI = re.compile(
    r"^10\.1109/(embc|iembs|ius|ultsym|uffc|smc|ner|bhi|embs|mems|isbi)"
    r"|^10\.1145/"          # ACM
    r"|^10\.1117/12\."      # SPIE proceedings; SPIE JOURNALS are 10.1117/1.*
    r"|^10\.32470/ccn",      # CCN
    re.I)

_CONF_VENUE = re.compile(
    r"\bconference\b|\bsymposium\b|\bcongress\b|annual meeting|\bworkshop\b"
    r"|proceedings of (the )?(ieee|spie|\d+)|conference series|meeting abstracts?\b",
    re.I)

# Venues whose names contain a conference word but are ordinary journals. Without
# this, "Proceedings of the National Academy of Sciences" reads as a conference
# and four PNAS papers are thrown out -- which a first pass at this did.
_NOT_CONF_VENUE = re.compile(r"national academy of sciences|neurophotonics", re.I)


def is_conference(doi: str | None = None, journal: str | None = None,
                  record_class: str | None = None) -> bool:
    """Is this conference output -- abstract, poster or full proceedings paper?"""
    if journal and _NOT_CONF_VENUE.search(journal):
        return False
    # Our own classes, and the registry types OpenAlex and Crossref use.
    if (record_class or "").lower() in ("conference_abstract", "proceedings_article", "conference-paper",
                                         "conference-abstract", "proceedings-article", "conference_paper"):
        return True
    return bool(_CONF_DOI.match(doi or "")) or bool(_CONF_VENUE.search(journal or ""))


# ---------------------------------------------------------------------------
# The screening ledger
# ---------------------------------------------------------------------------

def load_screening() -> dict[str, dict]:
    """The screening ledger, keyed by lower-case DOI: one row per paper ever screened.

    Each row carries doi, verdict (relevant | not_relevant | unspecified), criterion, reason,
    record_class, the run that decided it, and, where an earlier run decided differently,
    a `history` list of the superseded {run, verdict, criterion}.
    """
    out: dict[str, dict] = {}
    if SCREENING.exists():
        for line in SCREENING.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out[row["doi"].lower()] = row
    return out


def write_screening(ledger: dict[str, dict]) -> None:
    lines = [json.dumps(ledger[k], ensure_ascii=False, sort_keys=True) for k in sorted(ledger)]
    SCREENING.write_text("\n".join(lines) + "\n", encoding="utf-8")


def record_screening(rows, run: str, ledger: dict[str, dict] | None = None, write: bool = True) -> tuple[int, int]:
    """Merge verdict rows into the ledger under the name of the run that produced them.

    A row for a DOI already present replaces it, and the old verdict is kept under `history`
    unless it is the same run and verdict. Rows without a doi or a verdict are ignored.
    Returns (added, replaced).
    """
    ledger = load_screening() if ledger is None else ledger
    added = replaced = 0
    for r in rows:
        if not isinstance(r, dict) or not r.get("doi") or not r.get("verdict"):
            continue
        doi = r["doi"].lower()
        new = {k: v for k, v in r.items() if k != "history"}
        new["doi"] = doi
        new["run"] = run
        old = ledger.get(doi)
        if old is not None:
            hist = old.get("history", [])
            same = old.get("run") == run and old.get("verdict") == new.get("verdict") and old.get("criterion") == new.get("criterion")
            if not same:
                hist = hist + [{k: old[k] for k in ("run", "verdict", "criterion") if old.get(k) is not None}]
                replaced += 1
            if hist:
                new["history"] = hist
        else:
            added += 1
        ledger[doi] = new
    if write:
        write_screening(ledger)
    return added, replaced
