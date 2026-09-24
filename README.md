# TUS Paper Archive

[![Version](https://img.shields.io/github/v/tag/NeuroHarmonics/tus-paper-archive?label=version&sort=semver)](https://github.com/NeuroHarmonics/tus-paper-archive/releases) [![Data licence](https://img.shields.io/badge/data-CC%20BY%204.0-blue)](LICENSE-DATA) [![Code licence](https://img.shields.io/badge/code-MIT-blue)](LICENSE)

A structured record of the primary transcranial ultrasound stimulation literature. The archive records every published study that delivered ultrasound to neural tissue in order to modulate its function.

For each record, a fixed set of meta-data is stored, including what was stimulated, the device used, and the acoustic and pulse-timing parameters in line with the ITRUSST reporting standard ([Martin et al. 2024](https://doi.org/10.1016/j.brs.2024.04.013)). Each numerical value is stored with the sentence of the paper it was extracted from.

The data can be browsed and filtered at [neuroharmonics.com/tus-archive](https://neuroharmonics.com/tus-archive) and is held in this repository as one JSON file per paper. The archive is maintained by NeuroHarmonics.

## What is recorded

A paper is included when it reports an original ultrasound exposure delivered to neural tissue with the intent of modulating its function. Reviews, device characterisation, simulation, ablation, blood-brain barrier opening, drug delivery, engineered sensitisation and tissue repair are excluded. The full criteria, with the reasoning for each boundary and worked examples, are in [INCLUSION.md](INCLUSION.md) and [neuroharmonics.com/tus-archive/about](https://neuroharmonics.com/tus-archive/about).

For each included paper the record holds:

- Bibliography (title, authors, journal, year, DOI) taken from Crossref, and the abstract taken from Europe PMC, PubMed or Crossref with its source recorded.
- Study classification: model system, species, the conditions studied, the readouts used and when they were measured, anaesthesia, design (randomisation, blinding, sham and auditory controls), the direction of the reported effect, and adverse events.
- One or more exposures. A separate exposure is recorded for each combination of anatomical target and fundamental frequency. Each exposure records the target (a term from a controlled vocabulary built on UBERON, plus the paper's own words), the device (family, manufacturer and model), the fundamental frequency, pressure and intensity in three separate blocks (free field, in situ, and stated without a domain), the waveform, pulse duration, pulse repetition frequency, duty cycle and sonication duration, and the paper's own description of the protocol.
- Provenance: which model extracted the record and when, the verbatim sentence supporting every number, and any flags the extractor raised.

Every field takes one of three states: a value, `not_reported` (the field applies and the paper is silent in its main text) or `null` (the field does not apply). The rate at which the literature omits a parameter is itself a result of interest, so `not_reported` is kept distinct from `null`.

Several things are not recorded by design. Values are not derated or converted between the free-field and in-situ domains. No value is taken from a device datasheet, a supplement, a figure or another paper by the same group. Timing is recorded as four numbers and a quoted description; the archive does not reconstruct the hierarchical ITRUSST timing table. The field definitions, vocabularies, units and the reasoning behind these choices are in [SCHEMA.md](SCHEMA.md).

## Data files

| path | contents |
|---|---|
| `data/papers/<citekey>.json` | one record per paper, schema 1.0 |
| `data/abstracts/<citekey>.json` | the abstract, with the service it came from |
| `data/manifest.json` | bibliography and file identity for every included paper |
| `schema/paper.schema.json` | JSON Schema for a record |
| `schema/targets.json` | the target vocabulary: label, UBERON id, parents, synonyms |
| `data/screening.jsonl` | one screening verdict per DOI for every paper ever screened, with the run that decided it and any earlier verdict it replaced |
| `data/excluded.json` | papers screened out, with the criterion that failed |
| `data/key-documents.json` | reviews and standards used as citation-chaining seeds; not records |

The site offers the same records as a single download, `tus-paper-archive-v1.json`, without abstracts.

The PDFs and the full-text extractions are not in this repository. The PDFs are copyrighted and the extracted text is derived from them, so both are held privately. The scripts expect them at `library/` and `data/text/`, which are excluded from version control. Abstracts are published, as PubMed and Europe PMC publish them, and short attributed quotations are published under the quotation exception. Neither is in the bulk download.

## How the archive is built

The pipeline is a set of Python scripts, one per job, with language models used at the three judgement points: screening, recovering the identity of a paper without a DOI, and extraction. Every step that can be done by a script is done by a script. The output of each model step is checked by a script before it is accepted.

**Finding papers.** `scripts/sweep.py` harvests candidates in two ways: boolean queries over Europe PMC and PubMed, accounting for the field's fragemented terminology (TUS, tFUS, LIFU, ultrasonic neuromodulation), and citation chaining from the reviews and consensus documents in `data/key-documents.json`. OpenAlex is available but off by default because its free quota is small. Crossref is used for known-item lookup only. Candidates without an abstract are filled from Europe PMC, PubMed, OpenAlex and Crossref. No papers are screened on title alone.

**Screening.** `scripts/prescreen.py` applies the exclusions that don't need a judgement call: conference output, grey literature, supplement abstracts and decision letters. The remaining candidates are screened by a language model in year-stratified batches against the inclusion criteria, which returns one verdict per paper naming the criterion that failed. `scripts/check_verdicts.py` compares each verdict file against its batch and rejects any that are incomplete or refer to papers not in the batch. A verdict of `unspecified` means the model could not settle all four conditions from the abstract; those are listed for a person to decide. Every verdict is recorded in `data/screening.jsonl`, one row per DOI; a later verdict for the same paper replaces the earlier one and keeps it in the row's history.

**Obtaining papers.** `scripts/acquire.py` fetches what is legitimately open access, using the location Unpaywall reports, and writes everything else to a want-list for retrieval through institutional access. No attempt is made to bypass a paywall; an HTTP 403 from a publisher is treated as an access restriction.

**Identification.** `scripts/identify.py` reads the DOI from each PDF and resolves it through Crossref. For the papers that have no DOI, mostly those before 2003, a small model reads the first page and returns title, first author, year and journal, which `scripts/resolve_missing.py` then resolves. Each paper receives a citekey of the form year plus first-author surname (`2021collins`, with a letter suffix when that repeats), and `scripts/rename.py` files the PDF under a year-first name.

**Full text.** PDFs are converted to Markdown by MinerU through `scripts/pdf_markdown.py`. MinerU preserves reading order, section headings and tables; `pdftotext` scrambled the reading order of many papers with numbered methods sections. The measurements behind that choice are in [docs/text-extraction.md](docs/text-extraction.md).

**Extraction.** `scripts/build_prompt.py` renders the extraction prompt from SCHEMA.md and the target vocabulary, so the output format is specified in one place. A model reads each paper's Markdown in batches of five and writes one record per paper. `scripts/check_records.py` then checks the files against the batch, validates each against the JSON Schema, and confirms that every quoted sentence occurs in the paper's text after whitespace normalisation. A number whose quote cannot be found is removed and the removal is flagged. Only records that pass are copied into `data/papers/`.

**Checks on every record.** `scripts/validate.py` enforces the schema, the three-state rule and the quote rule, and runs inside the site build so nothing invalid is published. Two applicability rules are mechanical and are applied by the validator's strip mode: a field that cannot apply (an in situ block for a bath or dish, an unspecified-domain slot the paper never filled) is null rather than not reported, and an in situ block is either wholly null or has no nulls. `scripts/derive.py` adds a `derived` block that is never extracted: the duty cycle recomputed from pulse duration and pulse repetition frequency, with a flag when it differs from the stated value by more than five percent; consistency checks on frequency, pulse timing and the relation between pressure and intensity; and a status for each field. These are displayed on the paper page; the stored values are not altered.

**Vocabularies.** Every closed vocabulary has an `other` value paired with free text carrying the paper's own words. `scripts/vocab_report.py` tallies that free text across the corpus, and a value that recurs in three or more papers becomes a candidate term. Promotion is an edit to SCHEMA.md and the JSON Schema plus a script that relabels the affected records by string match; nothing is re-extracted. The changelog at the end of SCHEMA.md lists each patch, and CHANGELOG.md lists each release.

**Publication.** `scripts/build_site.py` compiles the records, manifest and abstracts into the data files the site reads and writes them into a checkout of the website repository, from which the site is built and deployed. Releases are made with `scripts/release.py` with calendar versions (`2026.10.0` is October's release, `2026.10.1` a correction later that month), a git tag, and a changelog entry computed from the diff between tags.

## Reliability

Extracted values are not checked by hand. Three checks are independent of the model that did the extraction: every number must be supported by a sentence found in the paper's text, the duty cycle is recomputed from pulse duration and pulse repetition frequency and a mismatch is flagged, and the extractor flags any value that is figure-only or where the text and a table disagree.

Every record's numeric fields are then read a second time by a different model that sees the paper but not the first read's values. Where the two reads disagree, a third model reads the paper with both readings and rules on each field; a script writes the ruling into the record and corrects the value where the ruling goes against it. Each number on the paper page shows whether two reads stand behind it.

All records that pass validation are published, with their quotes and flags shown. Corrections are made in response to reader reports.

## Corrections and additions

Each paper page on the site links to a GitHub issue form with the paper already filled in. The form asks for the field, the value shown, the correct value and the sentence of the paper that states it. A second form, for proposing a paper, asks for the DOI and how the paper meets the four conditions in INCLUSION.md. A third takes general suggestions: a field, a filter, a vocabulary term, a change to the criteria, or an error in the documentation. All three are in `.github/ISSUE_TEMPLATE/`.

A correction is an ordinary edit to `data/papers/<citekey>.json` with `provenance.human_edited` set to `true`, which protects the record from being overwritten by a later re-extraction. Corrections take effect at the next release, and every change is recorded in the git history.

## Running the pipeline

Requires [uv](https://docs.astral.sh/uv/). The full-text step provisions MinerU into `~/.cache/tus-paper-archive/mineru-venv` on first run and downloads about a gigabyte of models; a full pass over the corpus takes several hours and is resumable. No API keys are needed: Crossref, Europe PMC, PubMed and Unpaywall are used with a `mailto` identification only.

The scripts that read PDFs or full text expect them at `library/` and `data/text/`; those that work on the records alone need nothing beyond this repository.

```bash
uv sync
uv run scripts/status.py                        # the current state of everything

# find and screen
uv run scripts/sweep.py                         # harvest candidates
uv run scripts/enrich_abstracts.py --apply      # fill missing abstracts
uv run scripts/prescreen.py --apply --crossref  # deterministic decisions
uv run scripts/build_batches.py --out <dir>     # screening batches
uv run scripts/check_verdicts.py --batches <dir> --verdicts <scratch>/verdicts_wN_*.json --apply
uv run scripts/build_decisions.py               # data/needs-decision.md
uv run scripts/acquire.py                       # want-list; --fetch also downloads open-access PDFs

# ingest PDFs dropped into candidates/
uv run scripts/extract_text.py                  # PDF -> data/text/*.md
uv run scripts/identify.py                      # DOI -> Crossref -> data/ingest.json
uv run scripts/build_candidate_batch.py --out <dir>   # screen hand-added PDFs
uv run scripts/rename.py --apply                # apply verdicts, deduplicate, assign citekeys, file
uv run scripts/fetch_paper_abstracts.py         # data/abstracts/

# extract
uv run scripts/build_prompt.py                  # docs/extraction-prompt.md
uv run scripts/build_extract_batches.py --out <dir>
#   run one paper-extractor per batch with the rendered prompt, writing to a scratch directory
uv run scripts/check_records.py --batches <dir> --dir <scratch> --promote-to data/papers
uv run scripts/validate.py
uv run scripts/derive.py
uv run scripts/vocab_report.py

# publish
uv run scripts/build_site.py --out <dir>        # site data files; without --out, into the website checkout
uv run scripts/release.py --apply               # VERSION, CHANGELOG.md, commit, tag
uv run scripts/monthly_summary.py               # what changed since the last tag
```

The procedures for running a screening or extraction wave, the conventions, and the operating rules are in [docs/operations.md](docs/operations.md).

## Repository layout

```
README.md  INCLUSION.md  SCHEMA.md      overview, inclusion criteria, field definitions
schema/                                 JSON Schema and the target vocabulary
data/papers/  data/abstracts/           the records and abstracts
data/manifest.json  data/screening.jsonl  data/excluded.json   identity, screening decisions, exclusions
data/scope-recheck.json                 papers held off the site pending a scope ruling
scripts/                                one script per job; scripts/common.py holds shared paths and naming
docs/                                   operations, the extraction and screening prompts, design records
.claude/agents/                         the model agents used for screening, identification and extraction
library/  data/text/                    PDFs and full text, held privately; not tracked
VERSION                                 the release being prepared
LICENSE  LICENSE-DATA                   MIT for the code, CC BY 4.0 for the data
```

## Versions and citation

Each release is a git tag of the form `vYYYY.MM.N`, so an analysis can cite an exact revision of the data. The version is stamped into every data file and shown on the site.

## Licence

The scripts, agent definitions and site code are released under the MIT licence ([LICENSE](LICENSE)). The records, vocabularies, manifest, screening decisions and other data files are released under the Creative Commons Attribution 4.0 International licence ([LICENSE-DATA](LICENSE-DATA)); attribution is to the TUS Paper Archive, with the version used. Abstracts and the quoted sentences remain the copyright of their publishers and authors, are reproduced under the quotation exception and fair dealing, and are not covered by the data licence.
