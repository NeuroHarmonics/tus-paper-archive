#!/usr/bin/env python3
"""Decide, without a model, the candidates that need no judgement.

The sweep's boolean queries are deliberately broad -- (TITLE:"ultrasound" AND
TITLE:"nerve") returns 3,792 records, most of them nerve blocks and diagnostic
imaging -- because a narrow query set is what lost papers in the first place.
Breadth on the way in has to be paid for on the way out, and paying for it with
a model call per record is the expensive way.

Two rules are applied here, and only where the evidence is complete:

  1. A record whose title AND abstract never mention ultrasound in any form
     cannot report an ultrasound exposure. Condition B fails. E1.

  2. A record whose TITLE says "ultrasound-guided" is using ultrasound as the
     imaging modality for some other intervention -- a nerve block, a biopsy, an
     injection. Condition B fails. E5. 2,125 candidates say this, and not one of
     the 355 papers we hold does.

  6. A component record -- a Frontiers "Video_3_...", an eLife "Reviewer #2
     (Public Review)", an "Author response for ...". Publishers mint DOIs for
     these and they carry the ARTICLE's abstract, which is why they screen as
     relevant. Excluded under E7 for the same reason as a data deposit: the
     article is the publication, and counting both double-counts one study.
     They are also what the ABSTRACT UNRELIABLE flag was really detecting --
     not corrupted metadata, but a paper sitting beside its own components.

  5. A data deposit -- Dryad, Zenodo, figshare, a Dataverse -- is not a
     publication. These carry the ARTICLE's abstract, so a screener reads them as
     relevant and the study gets counted twice. Excluded under E7, and the
     article itself is what belongs in the archive. OSF is deliberately NOT in
     this list: it hosts genuine preprints as well as project pages, so those are
     judged on their record type.

  4. Conference output of any kind -- abstract, poster or full proceedings paper.
     E8. Deterministic, because when it was left to judgement it was applied both
     ways in the same day.

  3. A record that mentions ultrasound but names nothing neural anywhere -- no
     nerve, no brain region, no neural readout, not even "stimulation" -- did not
     sonicate neural tissue. Condition C fails. E2. This one exists because
     build_batches.py was already skipping these on a topical filter and saying
     nothing about it, which is the silent cap this project keeps warning itself
     about. Deciding them records a verdict; skipping them records nothing.

  7. Records that are not papers by their registered TYPE or their title's first
     words, and so need no abstract to decide: peer-review decision letters and
     author responses registered as versioned DOIs (E7, a component of the
     article); book chapters (E3: a chapter summarises others' work); corrections,
     errata, retractions, letters, comments, replies and editorials (E3); and
     numbered items in the meeting-abstract supplements of journals such as
     Biological Psychiatry and Brain Stimulation (E8). These were 195 of the 389
     abstract-less 2025-2026 candidates on 2026-09-21, sitting in the queue
     because no source carries an abstract for a record that has none.

  8. Grey literature that is not a publication under E7, added after
     a pass through the want-list: OSF registrations and project pages (10.17605, including
     Open MIND preregistrations) and Focused Ultrasound Foundation project reports on OSF
     (10.31225); dissertations and theses by registered type, DOI prefix or repository name;
     DANDI deposits. PsyArXiv, OSF Preprints, bioRxiv and arXiv stay in: they are preprints.

  9. Meeting-supplement abstracts that carry no number: a title beginning "ID#" (NANS
     abstracts in Neuromodulation), an all-capitals title in a journal that publishes its
     congress as a supplement (Ultrasound in Medicine & Biology), and, with --crossref, an
     Elsevier journal-article with no abstract whose Crossref record has zero references and
     a single supplement page ("S12", "567"), from 2010 onwards: measured on 2026-09-22, every supplement item
     had references-count 0 and every article had more.

Deliberately narrow. The text rules fire only when an abstract is present, because absence
of an abstract is absence of evidence, not evidence of absence -- and it makes
no attempt to judge intent, target or study type, which are the parts that
genuinely need reading. Everything else goes to a screener.

Verdicts are recorded in data/screening.jsonl like any other, so a wrong call here is
visible, auditable and reversible in the same way as any other.

  uv run scripts/prescreen.py            # dry run
  uv run scripts/prescreen.py --apply
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

from common import load_screening, record_screening, DATA, is_conference, read_json

# Every way this literature names the thing, including the ones that appear only
# in an abstract: LIPUS, tFUS, LIFU, sonication, acoustic.
ULTRASOUND = re.compile(
    r"ultraso(?:und|nic|nograph)|sonicat|sonograph|sonothera|sonogenet|sonoporation"
    r"|\bLIFU\b|\bLIFUP\b|\btFUS\b|\bFUS\b|\bLIPUS\b|\bHIFU\b|\bMRgFUS\b|\bTUS\b"
    r"|acoustic|piezo|transducer|megahertz|\bMHz\b|\bkHz\b",
    re.I,
)

# Anything this literature might call the target. Deliberately broad -- it
# includes "stimulation", "pain" and "motor" -- because rule 3 below only fires
# when NONE of it appears, and a false negative there buries a paper.
NEURAL = re.compile(
    r"neuro|neural|neuron|nerve|nervous|brain|cerebr|cortex|cortic|thalam|hippocamp"
    r"|amygdal|striat|axon|synap|spinal|retina|vagus|glia|astrocyt|myelin|ganglion"
    r"|\bEEG\b|\bMEP\b|\bfMRI\b|BOLD|behavio|cognit|epilep|parkinson|alzheim|tremor"
    r"|depress|anxiet|analgesi|nocicept|pain|sensor|motor|stimulat|modulat",
    re.I,
)

# Publisher-minted DOIs for parts of an article rather than the article.
# The separator must be an UNDERSCORE. Publishers name these files
# "Video_1_Title" and "DataSheet_2_Title"; requiring a space instead would match
# "Image quality control in breast ultrasound", which is a real paper.
COMPONENT = re.compile(
    r"^(video|table|image|datasheet|data_sheet|presentation|audio|figure)_"
    r"|^reviewer #\d|^author response for\b|^elife assessment\b"
    r"|^supplementary (material|data|information)\b", re.I)


# Repositories that hold data rather than papers. OSF is excluded from this list
# on purpose: it hosts real preprints (Open MIND among them) alongside project
# pages, so an OSF DOI is judged on its record type, not its prefix.
DATA_DEPOSIT = re.compile(
    r"^10\.5061/dryad|^10\.5281/zenodo|^10\.6084/m9\.figshare|^10\.7910/dvn"
    r"|^10\.18112|^10\.15468|^10\.48324/dandi|^10\.60893/figshare", re.I)


# "Ultrasound-guided" names ultrasound as the camera, never as the stimulus.
# The (TITLE:"ultrasound" AND TITLE:"nerve") query is the single largest source
# of candidates and most of what it returns is nerve blocks, so without this the
# first screening batches were almost entirely regional anaesthesia.
GUIDED = re.compile(
    r"ultraso(?:und|nograph)[a-z]*[- ]guided|ultrasound guidance"
    r"|echo[- ]guided|sonographic(?:ally)? guided",
    re.I,
)


# Rule 7. A decision letter or author response registered as its own DOI
# ("10.1002/brb3.70318/v3/decision1"), or typed peer-review by the registry.
DECISION_DOI = re.compile(r"/v\d+/(decision|review|response)\d*$", re.I)
DECISION_TITLE = re.compile(r"^(decision letter|author response|review(er)? report|peer review)\b", re.I)

# A chapter DOI: Springer and Elsevier ISBN-based prefixes.
CHAPTER_DOI = re.compile(r"^10\.(1007|1016)/(978|b978)", re.I)

# Front matter. Anchored to the start of the title so "Response of ... to ultrasound" survives.
FRONT_MATTER = re.compile(
    r"^(correction|corrigendum|erratum|errata|retraction|retracted|expression of concern"
    r"|comment on|commentary on|commentary:|letter to the editor|letter:|letter regarding|reply to|reply:|response to|in reply"
    r"|editorial|author'?s'? reply|authors'? response)\b", re.I)

# Meeting-abstract supplements: a numbered title ("241. Transcranial ...", "P12 ...", "168P ...") in a
# journal that publishes its society meeting as a supplement.
SUPPLEMENT_JOURNALS = re.compile(
    r"^(biological psychiatry|brain stimulation|clinical neurophysiology|neuromodulation"
    r"|annals of oncology|archives of physical medicine and rehabilitation|journal of the neurological sciences"
    r"|neurosurgery|epilepsia|movement disorders|european psychiatry|neuropsychopharmacology|journal of pain"
    r"|the journal of pain|ultrasound in medicine (&|and) biology)", re.I)
SUPPLEMENT_TITLE = re.compile(r"^(\d{1,4}[A-Z]?[.:]\s|P\d{1,4}[.:]?\s|[A-Z]{1,3}-?\d{2,4}[.:]?\s)")


# Rule 8. Grey literature.
OSF_PROJECT_DOI = re.compile(r"^10\.(17605|31225)/", re.I)
THESIS_DOI = re.compile(r"^10\.(25365/thesis|34973|5167/uzh|3929/ethz|17863/cam|25560|14279|18452|6100|32657)/?", re.I)
THESIS_VENUE = re.compile(r"repository|\bthesis\b|\btheses\b|dissertation|proquest|circle \(university", re.I)

# Rule 9. Supplement abstracts without a number.
ID_TITLE = re.compile(r"^ID#\s*\d+", re.I)


def is_all_caps(title: str) -> bool:
    letters = [c for c in title if c.isalpha()]
    return len(letters) > 25 and sum(c.isupper() for c in letters) / len(letters) > 0.9


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--crossref", action="store_true",
                    help="rule 9c: look up abstract-less Elsevier articles in Crossref (network)")
    args = ap.parse_args()
    cr = None
    if args.crossref:
        from identify import Crossref
        cr = Crossref()

    cands = (read_json(DATA / "sweep-candidates.json", {}) or {}).get("candidates", {})
    decided = set(load_screening())
    held = {(p.get("doi") or "").lower()
            for p in (read_json(DATA / "manifest.json", {}) or {}).get("papers", {}).values()}

    out, deferred_no_abstract, to_screen = [], 0, 0
    for doi, v in cands.items():
        if doi in decided or doi in held:
            continue
        title = v.get("title") or ""
        abstract = v.get("abstract") or ""
        # Rule 6: a component record is not the publication.
        if COMPONENT.match(title):
            out.append({
                "doi": doi, "verdict": "not_relevant", "record_class": "other",
                "criterion": "E7",
                "reason": ("A component record (supplementary video, reviewer "
                           "report, author response), not the article. It carries "
                           "the article's abstract, so it reads as relevant; the "
                           "article is what belongs in the archive."),
                "resolved_by": "prescreen", "under_current_criteria": True,
            })
            continue

        rtype = (v.get("type") or "").lower()
        journal = v.get("journal") or ""

        # Rule 8: grey literature that is not a publication (E7).
        if OSF_PROJECT_DOI.match(doi):
            out.append({
                "doi": doi, "verdict": "not_relevant", "record_class": "other", "criterion": "E7",
                "reason": ("An OSF registration, preregistration, project page or foundation project report, "
                           "not a publication."),
                "resolved_by": "prescreen", "under_current_criteria": True,
            })
            continue
        if rtype in ("dissertation", "thesis") or THESIS_DOI.match(doi) or THESIS_VENUE.search(journal):
            out.append({
                "doi": doi, "verdict": "not_relevant", "record_class": "other", "criterion": "E7",
                "reason": "A dissertation or thesis in a university repository, not a citable publication under the bar.",
                "resolved_by": "prescreen", "under_current_criteria": True,
            })
            continue

        # Rule 9: supplement abstracts that carry no number.
        year = v.get("year") or 0
        if ID_TITLE.match(title) or (is_all_caps(title) and year >= 2000 and SUPPLEMENT_JOURNALS.match(journal)):
            out.append({
                "doi": doi, "verdict": "not_relevant", "record_class": "conference_abstract", "criterion": "E8",
                "reason": ("A meeting-supplement abstract by its title form (an ID# prefix, or an all-capitals "
                           "congress title in a journal that publishes its meeting as a supplement). E8."),
                "resolved_by": "prescreen", "under_current_criteria": True,
            })
            continue
        # Only from 2010: older Elsevier records lack reference lists for other reasons.
        if cr is not None and not abstract and doi.startswith("10.1016/") and year >= 2010 and rtype in ("article", "journal-article", ""):
            msg = cr.get(doi) or {}
            page = str(msg.get("page") or "")
            if msg and msg.get("references-count", 1) == 0 and not msg.get("article-number") \
                    and re.match(r"^S?\d{1,4}$", page):
                out.append({
                    "doi": doi, "verdict": "not_relevant", "record_class": "conference_abstract", "criterion": "E8",
                    "reason": (f"A meeting-supplement abstract: the Crossref record has no references and a single "
                               f"supplement page ({page}), which no article in this corpus has. E8."),
                    "resolved_by": "prescreen", "under_current_criteria": True,
                })
                continue

        # Rule 7: not a paper by registered type or title, so no abstract is needed.
        if rtype == "peer-review" or DECISION_DOI.search(doi) or DECISION_TITLE.match(title):
            out.append({
                "doi": doi, "verdict": "not_relevant", "record_class": "other", "criterion": "E7",
                "reason": ("A peer-review decision letter or author response registered as its own "
                           "DOI; a component of the article, not a publication."),
                "resolved_by": "prescreen", "under_current_criteria": True,
            })
            continue
        if rtype == "book-chapter" or CHAPTER_DOI.match(doi) or journal.lower().endswith("ebooks"):
            out.append({
                "doi": doi, "verdict": "not_relevant", "record_class": "book", "criterion": "E3",
                "reason": "A book chapter, by registered type or ISBN-based DOI: it summarises others' work.",
                "resolved_by": "prescreen", "under_current_criteria": True,
            })
            continue
        if FRONT_MATTER.match(title):
            out.append({
                "doi": doi, "verdict": "not_relevant", "record_class": "letter_commentary", "criterion": "E3",
                "reason": ("Front matter by its title: a correction, erratum, retraction, letter, comment, "
                           "reply or editorial. Not an original exposure."),
                "resolved_by": "prescreen", "under_current_criteria": True,
            })
            continue
        if SUPPLEMENT_TITLE.match(title) and SUPPLEMENT_JOURNALS.match(journal):
            out.append({
                "doi": doi, "verdict": "not_relevant", "record_class": "conference_abstract", "criterion": "E8",
                "reason": ("A numbered item in a society meeting supplement of the journal: a conference "
                           "abstract, excluded as a class under E8."),
                "resolved_by": "prescreen", "under_current_criteria": True,
            })
            continue

        # Rule 5: a data deposit is not a publication.
        if DATA_DEPOSIT.match(doi) or (v.get("type") or "").lower() == "dataset" \
                or (v.get("title") or "").lower().startswith("data from:"):
            out.append({
                "doi": doi, "verdict": "not_relevant", "record_class": "other",
                "criterion": "E7",
                "reason": ("A data deposit, not a publication. It carries the "
                           "article's abstract, so it reads as relevant; the "
                           "article is what belongs in the archive."),
                "resolved_by": "prescreen", "under_current_criteria": True,
            })
            continue

        # Rule 4 needs no text at all, so it runs first.
        if is_conference(doi, v.get("journal"), v.get("type")):
            out.append({
                "doi": doi, "verdict": "not_relevant",
                "record_class": "conference_abstract", "criterion": "E8",
                "reason": ("Conference output -- abstract, poster or proceedings "
                           "paper. Excluded as a class under E8."),
                "resolved_by": "prescreen", "under_current_criteria": True,
            })
            continue

        # Rule 2 needs only the title, so it runs before the abstract guard.
        if GUIDED.search(title):
            out.append({
                "doi": doi, "verdict": "not_relevant", "record_class": None,
                "criterion": "E5",
                "reason": ("The title says ultrasound-guided: ultrasound is the "
                           "imaging modality for another intervention, not the "
                           "stimulus. Condition B fails. Decided "
                           "deterministically from the title."),
                "resolved_by": "prescreen", "under_current_criteria": True,
            })
            continue
        if not abstract:
            deferred_no_abstract += 1
            continue
        if ULTRASOUND.search(f"{title} {abstract}"):
            if not NEURAL.search(f"{title} {abstract}"):
                out.append({
                    "doi": doi, "verdict": "not_relevant", "record_class": None,
                    "criterion": "E2",
                    "reason": ("Mentions ultrasound but names nothing neural in "
                               "title or abstract -- no nerve, no brain region, no "
                               "neural readout -- so nothing neural was sonicated. "
                               "Condition C fails. Decided deterministically from a "
                               "complete title and abstract."),
                    "resolved_by": "prescreen", "under_current_criteria": True,
                })
                continue
            to_screen += 1
            continue
        out.append({
            "doi": doi,
            "verdict": "not_relevant",
            "record_class": None,
            "criterion": "E1",
            "reason": ("Neither the title nor the abstract mentions ultrasound in "
                       "any form, so the paper reports no ultrasound exposure. "
                       "Condition B fails. Decided deterministically from a "
                       "complete title and abstract, not by a screener."),
            "resolved_by": "prescreen",
            "under_current_criteria": True,
        })

    print(f"undecided candidates        {len(cands) - len(decided & set(cands))}")
    by_rule = {}
    for o in out:
        by_rule[o["criterion"]] = by_rule.get(o["criterion"], 0) + 1
    print(f"  excluded here             {len(out)}  {by_rule}")
    print(f"  need a screener           {to_screen}")
    print(f"  deferred, no abstract     {deferred_no_abstract}")

    if not args.apply:
        print("\nDRY RUN. Re-run with --apply.")
        return 0

    run = f"prescreen_{datetime.date.today().isoformat()}"
    added, replaced = record_screening(out, run)
    print(f"\nrecorded {added} verdicts in data/screening.jsonl as {run}" + (f", {replaced} replaced" if replaced else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
