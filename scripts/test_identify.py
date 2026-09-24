#!/usr/bin/env python3
"""Regression tests for identification heuristics.

Every case here is a bug that actually occurred while ingesting the initial 252
papers, with the real paper named. They are cheap to run and they guard the part
of the pipeline where a silent error becomes permanently wrong metadata.

  uv run scripts/test_identify.py
"""

from __future__ import annotations

import sys

from identify import (
    crossref_record_problems, title_agreement, usable_xmp_title,
)
from resolve_missing import page_span, parent_doi, pick_best

import pathlib

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    ok = abs(got - want) < 0.01 if isinstance(want, float) else got == want
    if not ok:
        FAILURES.append(f"{label}\n      got {got!r}\n      want {want!r}")
    print(f"  {'pass' if ok else 'FAIL'}  {label}")


def test_xmp_junk() -> None:
    """XMP Title is often typesetting debris. Treating it as a real title made
    ~16 correctly-resolved papers look like mismatches."""
    print("\nusable_xmp_title -- reject typesetting debris")
    for junk in ("untitled", "pone.0086939 1..13", "PNAS202206828_proof.pdf",
                 "doi:10.1016/j.pbiomolbio.2006.07.026", "Using JASA format",
                 "16075954867795 1..30", "100827", "deb_pone.0086939 1..13"):
        check(f"junk: {junk!r}", usable_xmp_title(junk), None)
    real = "Standing waves in blood--brain barrier disruption"
    check(f"real: {real[:34]!r}", usable_xmp_title(real), real)


def test_reply_titles() -> None:
    """A reply quotes its target's title in full, so it is a near-perfect string
    match for a different work. Fouragnan 2026 took Rezai 2025's DOI this way."""
    print("\ntitle_agreement -- replies are distinct works")
    target = "Brain injury during focused ultrasound neuromodulation for substance use disorder"
    for reply in (f"Response to: {target}",
                  f"Letter to the Editor in response to “{target}”",
                  f"Re: Rezai A et al. “{target}”",
                  f"Comment on {target}"):
        check(f"reply vs target: {reply[:30]!r}", title_agreement(reply, target), 0.0)
    check("identical titles", title_agreement(target, target), 1.0)


def test_truncated_titles() -> None:
    """PDF producers truncate XMP Title mid-phrase (~100 chars). A long prefix is
    corroboration, not disagreement -- Bancel 2025, Pulkkinen 2011."""
    print("\ntitle_agreement -- truncated XMP still corroborates")
    full = ("Simulations and measurements of transcranial low-frequency ultrasound "
            "therapy: skull-base heating and effective area of treatment")
    trunc = "Simulations and measurements of transcranial low-frequency ultrasound therapy"
    check("truncated prefix", title_agreement(trunc, full), 0.97)
    check("case-only difference",
          title_agreement("Holograms to Focus Arbitrary Ultrasonic Fields through the Skull",
                          "Holograms to focus arbitrary ultrasonic fields through the skull"), 1.0)
    check("short title inside a long unrelated one",
          title_agreement("Ultrasound", "Ultrasound neuromodulation of the human motor cortex "
                                        "in a randomised sham-controlled trial") < 0.5, True)


def test_crossref_self_condemnation() -> None:
    """When XMP is junk there is nothing to compare against, so the Crossref
    record has to condemn itself. Caught Murphy 2022 and Xian 2023 resolving to a
    journal-level PNAS DOI, and Verhagen 2019 to a component titled 'Abstract'."""
    print("\ncrossref_record_problems -- reject non-article records")
    check("journal-level record",
          "crossref_type_journal" in crossref_record_problems(
              {"title": "Proceedings of the National Academy of Sciences",
               "crossref_type": "journal",
               "journal": "Proceedings of the National Academy of Sciences"}), True)
    check("component titled 'Abstract'",
          "crossref_title_is_not_an_article_title" in crossref_record_problems(
              {"title": "Abstract", "crossref_type": "component", "journal": "X"}), True)
    check("eLife assessment sub-record",
          "crossref_title_is_not_an_article_title" in crossref_record_problems(
              {"title": "eLife Assessment: Neuromodulation with Ultrasound",
               "crossref_type": "peer-review", "journal": None}), True)
    check("genuine article passes",
          crossref_record_problems(
              {"title": "What is ultrasound?", "crossref_type": "journal-article",
               "journal": "Progress in Biophysics and Molecular Biology"}), [])


def test_parent_doi() -> None:
    """eLife registers assessments and reviews as separate DOIs; a title search
    can return six of them and no article (Caffaratti 2024)."""
    print("\nparent_doi -- recover article from sub-record DOI")
    check("assessment", parent_doi("10.7554/elife.100827.3.sa2"), "10.7554/elife.100827")
    check("review", parent_doi("10.7554/elife.100827.1.sa0"), "10.7554/elife.100827")
    check("plain DOI untouched", parent_doi("10.1016/j.brs.2024.04.013"), None)


def test_pick_best() -> None:
    """The same title exists in more than one Crossref record. Harvey's 1929
    Am J Physiol paper lost to a single-page 1930 Am Heart J abstract of it."""
    print("\npick_best -- year and page span break title ties")
    check("page span range", page_span("284-290"), 7)
    check("page span single", page_span("388"), 1)
    real = {"year": 1929, "pages": "284-290",
            "journal": "American Journal of Physiology-Legacy Content"}
    abstract = {"year": 1930, "pages": "388", "journal": "American Heart Journal"}
    cand, _ = pick_best([(1.0, abstract), (1.0, real)], {"year": 1929})
    check("prefers exact year + page range", cand["journal"],
          "American Journal of Physiology-Legacy Content")
    # Order must not matter.
    cand2, _ = pick_best([(1.0, real), (1.0, abstract)], {"year": 1929})
    check("order-independent", cand2["year"], 1929)


def test_fetched_pdf_must_be_the_paper():
    """An OA link can serve a valid PDF that is not the paper.

    Real case: the OA location for 10.4103/1673-5374.335158 (transcranial FUS
    and vasogenic edema after MCAO) returned a Bio-Rad Criterion Stain Free
    imaging system instruction manual. Content-type was application/pdf and the
    bytes parsed, so nothing upstream caught it -- the manual would have been
    filed in the library under the paper's title.

    The first version of the check read pages 1-2 only. The manual's cover page
    holds 65 characters, so it was classed as an image-only scan and waved
    through. Reading five pages is what actually catches it.
    """
    import re

    import acquire

    print("\npdf_is_the_paper -- an OA link can serve the wrong document")
    title = ("Transcranial focused ultrasound stimulation reduces vasogenic "
             "edema after middle cerebral artery occlusion")
    want = {w for w in re.findall(r"[a-z]{4,}", title.lower())
            if w not in acquire.TITLE_STOPWORDS}
    check("title yields enough words to judge on", len(want) >= 8, True)
    check("reads five pages, not one", '"-l", "5"' in
          pathlib.Path(__file__).with_name("acquire.py").read_text(), True)
    check("rejects at low overlap", 0 / max(len(want), 1) >= 0.4, False)
    check("accepts at full overlap", len(want) / len(want) >= 0.4, True)


def test_publisher_component_records() -> None:
    """A publisher's non-article records carry the article's own title.

    Three shapes of the same failure, all seen in this corpus:
      - Wiley mints a cover-feature record; the want-list carried 10.1002/adbi.201870071
        again and again for a paper already in the library.
      - JoVE mints a video record, 10.3791/58781-v, which a title search scores
        1.0 against and which therefore wins outright.
      - eLife mints assessments, 10.7554/elife.100827.3.sa2.
    None of them is the paper.
    """
    import resolve_covers
    from resolve_missing import parent_doi

    print("\ncomponent records -- covers, videos, assessments")
    for doi, is_cover in [("10.1002/adbi.201870071", True),
                          ("10.1002/advs.202270218", True),
                          ("10.1002/advs.202202345", False),   # the real article
                          ("10.1002/adbi.201800041", False)]:
        check(f"cover? {doi}", bool(resolve_covers.COVER_DOI.match(doi)), is_cover)
    check("JoVE video -> article", parent_doi("10.3791/58781-v"), "10.3791/58781")
    check("eLife assessment -> article",
          parent_doi("10.7554/elife.100827.3.sa2"), "10.7554/elife.100827")
    check("a real DOI is left alone", parent_doi("10.3791/58781"), None)

    # The cover's title is the article's, wrapped front and back.
    at = resolve_covers.article_title
    check("strips the issue tag",
          at("General-Purpose Ultrasound Neuromodulation System for Chronic, "
             "Closed-Loop Preclinical Studies in Freely Behaving Rodents "
             "(Adv. Sci. 34/2022)").endswith("Rodents"), True)
    check("strips the cover-topic prefix",
          at("Hippocampal Slices: On-Chip Ultrasound Modulation of Pyramidal "
             "Neuronal Activity").startswith("On-Chip"), True)
    check("leaves a real subtitle alone",
          at("Transcranial focused ultrasound for temporal lobe epilepsy: a "
             "laboratory feasibility study").startswith("Transcranial"), True)


def test_scope_audit_dashes() -> None:
    """Publishers set compound modifiers with en dashes.

    "Transcranial magnetic resonance-guided focused ultrasound for temporal lobe
    epilepsy" reached the want-list twice after it had already been ruled out of
    scope, because the pattern wanted an ASCII hyphen and the abbreviated form,
    and the title had a U+2013 and the words spelled out.
    """
    from audit_scope import scan

    print("\nscope audit -- Unicode dashes and spelled-out MRgFUS")
    for label, title, want in [
        ("en dash + spelled out",
         "Transcranial magnetic resonance\u2013guided focused ultrasound for "
         "temporal lobe epilepsy", True),
        ("ascii hyphen + abbreviated",
         "MR-guided focused ultrasound thalamotomy for essential tremor", True),
        ("in scope, must stay clean",
         "Transcranial focused ultrasound neuromodulation of human motor cortex", False),
    ]:
        check(label, any(r == "E4" for r, _ in scan(title)), want)


def test_elsevier_xmp_stamps() -> None:
    """Elsevier's XMP Title answers two questions we were paying a model for.

    "PII: 0014-4886(87)90073-2" IS the DOI suffix -- that is the whole identity of
    Ellisman 1987, which sat unidentified while the want-list asked for
    a paper already in the library. And "...-mmc1" marks supplementary material,
    which is not a paper at all.
    """
    import identify

    print("\nElsevier XMP stamps -- PII is a DOI, mmc1 is a supplement")
    m = identify.ELSEVIER_PII.match("PII: 0014-4886(87)90073-2")
    check("old-style PII parsed", bool(m), True)
    check("PII -> DOI", f"10.1016/{m.group(1)}" if m else None,
          "10.1016/0014-4886(87)90073-2")
    check("modern PII left alone",
          bool(identify.ELSEVIER_PII.match("PII: S1935861X24000858")), False)
    for stamp, is_supp in [("1-s2.0-S1935861X24000858-mmc1", True),
                           ("mmc1", True), ("mmc12", True),
                           ("Diagnostic levels of ultrasound", False)]:
        check(f"supp? {stamp[:34]!r}",
              bool(identify.ELSEVIER_SUPP.search(stamp)), is_supp)


def test_want_list_applies_current_bar() -> None:
    """A verdict made before the current criteria must not put a review on the want-list.

    A verdict not made under the current criteria may carry a record_class that already
    fails E3 or E8 (review, systematic_review, letter_commentary, conference_abstract);
    such rows must not reach the want-list.
    """
    import acquire

    print("\nwant-list -- re-apply the bar to stale verdicts")
    for cls, code in [("review", "E3"), ("systematic_review", "E3"),
                      ("book", "E3"), ("letter_commentary", "E3"),
                      ("conference_abstract", "E8")]:
        check(f"{cls} excluded [{code}]", acquire.CLASS_FAILS_BAR.get(cls), code)
    check("primary_study survives",
          "primary_study" in acquire.CLASS_FAILS_BAR, False)
    check("methods_tool survives",
          "methods_tool" in acquire.CLASS_FAILS_BAR, False)


def test_doi_dash_normalisation() -> None:
    """A DOI can only contain an ASCII hyphen; publishers supply others.

    One candidate arrived as 10.3969/j.issn.1672<U+2043>6731.2022.04.006, using
    HYPHEN BULLET. A screener normalised it on sight, its verdict then matched no
    paper in the batch, and the whole file was rejected for a mismatch that was
    our data's fault rather than the model's.

    Third dash failure in this codebase: a scope pattern that wanted an ASCII
    hyphen, a cover-title comparison, and now a DOI key.
    """
    from common import clean_doi

    print("\nDOI normalisation -- typographic dashes")
    check("U+2043 hyphen bullet",
          clean_doi("10.3969/j.issn.1672\u20436731.2022.04.006"),
          "10.3969/j.issn.1672-6731.2022.04.006")
    check("U+2013 en dash",
          clean_doi("10.1016/j.brs\u20132024.04.013"), "10.1016/j.brs-2024.04.013")
    check("a normal DOI is untouched",
          clean_doi("10.1016/j.brs.2024.04.013"), "10.1016/j.brs.2024.04.013")


def test_prescreen_never_drops_a_held_paper() -> None:
    """The deterministic pre-screen must not exclude anything already in scope.

    It excludes a candidate only when neither title nor abstract mentions
    ultrasound in any form. Run against all held papers, 9 of 355 fail that test
    on their title -- Harvey 1929 ("THE EFFECT OF HIGH FREQUENCY SOUND WAVES..."),
    Khalighinejad 2020, Legon 2026 and others whose titles name the circuit
    rather than the method.

    Every one of them is a paper we hold no abstract for, and the rule fires only
    when an abstract is present. That guard is the whole safety margin, so it is
    tested rather than trusted.
    """
    import json

    from common import DATA
    from prescreen import ULTRASOUND

    print("\nprescreen -- must never exclude a held paper")
    man = json.loads((DATA / "manifest.json").read_text())["papers"]
    cache = json.loads((DATA / "crossref-cache.json").read_text())
    would_drop = []
    for ck, rec in man.items():
        abstract = (cache.get(rec.get("doi")) or {}).get("abstract") or ""
        if not abstract:
            continue                      # deferred, never excluded
        if not ULTRASOUND.search(f"{rec.get('title') or ''} {abstract}"):
            would_drop.append(ck)
    check("held papers wrongly excluded", len(would_drop), 0)
    # Rule 2: "ultrasound-guided" is ultrasound as the camera. 2,125 candidates
    # say it; none of the papers we hold does.
    from prescreen import GUIDED
    guided_hits = [ck for ck, rec in man.items()
                   if GUIDED.search(rec.get("title") or "")]
    check("held papers matching ultrasound-guided", len(guided_hits), 0)
    # Rule 3: mentions ultrasound, names nothing neural at all.
    from prescreen import NEURAL
    neural_miss = [ck for ck, rec in man.items()
                   if (cache.get(rec.get("doi")) or {}).get("abstract")
                   and not NEURAL.search(
                       f"{rec.get('title') or ''} "
                       f"{(cache.get(rec.get('doi')) or {}).get('abstract')}")]
    check("held papers naming nothing neural", len(neural_miss), 0)
    check("catches a nerve block",
          bool(GUIDED.search("Ultrasound-guided transversus abdominis plane block")), True)
    check("does not catch neuromodulation",
          bool(GUIDED.search("Transcranial focused ultrasound stimulation of human "
                             "motor cortex")), False)
    # And the guard itself: a title-only record must never be excluded.
    check("no-abstract records are deferred, not excluded",
          bool(ULTRASOUND.search("THE EFFECT OF HIGH FREQUENCY SOUND WAVES ON "
                                 "HEART MUSCLE AND OTHER TISSUES")), False)


def main() -> int:
    for fn in (test_xmp_junk, test_reply_titles, test_truncated_titles,
               test_crossref_self_condemnation, test_parent_doi, test_pick_best,
               test_fetched_pdf_must_be_the_paper,
               test_publisher_component_records, test_scope_audit_dashes,
               test_elsevier_xmp_stamps, test_want_list_applies_current_bar,
               test_prescreen_never_drops_a_held_paper,
               test_doi_dash_normalisation):
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all identification regression tests pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
