# Operating the archive

How the pipeline is run day to day, the conventions it relies on, and the operating rules. The README explains what the archive is and what each script does; this document is for whoever is running it.

For the current state, run `uv run scripts/status.py`. Counts are never written into documents by hand.

## Environment

- `uv` for Python. `pdftotext` (poppler) is the fallback text extractor only.
- MinerU is provisioned by `scripts/extract_text.py` on first run into `~/.cache/tus-paper-archive/mineru-venv`, outside the repository. See [text-extraction.md](text-extraction.md).
- No API keys. Crossref, Europe PMC, PubMed and Unpaywall are key-free and are sent a `mailto` (`MAILTO` in `scripts/common.py`). OpenAlex is metered, so it is off by default (`--use-openalex`) and nothing depends on it. Crossref is a known-item lookup only, not a discovery source.
- The PDFs and text are held in a private repository and reached through the `library/` and `data/text/` symlinks; its README has the two `ln -s` lines. Commit there after any ingest.

## Screening wave

1. `uv run scripts/sweep.py` then `uv run scripts/enrich_abstracts.py --apply` and `uv run scripts/prescreen.py --apply --crossref`.
2. `uv run scripts/build_batches.py --out <dir>` writes year-stratified batches, so recent papers are screened alongside well-cited older ones.
3. Launch one `paper-screener` agent per batch with the prompt in [screening-prompt.md](screening-prompt.md), substituting the batch path and an output path in a scratch directory. The output contract is in the prompt, not only in the agent definition.
4. `uv run scripts/check_verdicts.py --batches <dir> --verdicts <files> --apply`. It exits non-zero on a file that is short, has extras, or omits a DOI, and records nothing in that case; when every file passes, `--apply` records the verdicts in `data/screening.jsonl` under the file's name. Never trust the agent's own report of what it wrote.
5. `uv run scripts/build_decisions.py` lists the `unspecified` verdicts for a person. `uv run scripts/build_title_review.py` lists candidates with no abstract anywhere and a title that looks relevant; the bar forbids a model deciding those. Human decisions are written as a small verdict file and recorded the same way, so they are tracked like any other and win over the earlier model verdict.
6. `uv run scripts/acquire.py --fetch` downloads the open-access papers and writes `data/wanted.html`, the list for a person with institutional access. Papers obtained by hand go into `candidates/`. A Safari `.pdf.download` bundle is a directory holding the finished PDF; move the PDF out.

## Ingest

`uv run scripts/extract_text.py`, `uv run scripts/identify.py`, then `uv run scripts/build_candidate_batch.py --out <dir>` for hand-added PDFs that have no verdict yet, screen them, and `uv run scripts/rename.py --apply`. `rename.py` preserves existing citekeys and keeps a merged preprint as an alternate of its version of record. `uv run scripts/fetch_paper_abstracts.py` fetches abstracts for the new records. Then `uv run scripts/audit_scope.py`, the deterministic scope backstop; it must stay clean, and a paper it flags that has been reviewed and kept goes into `data/scope-audit-acknowledged.json`.

## Extraction wave

1. `uv run scripts/build_extract_batches.py --out <scratch>/bulk --size 5` writes one batch file per five citekeys that have no record yet.
2. Launch one `paper-extractor` agent per batch, about ten at a time. The agent prompt is short: read `docs/extraction-prompt.md`, substitute `<REPO>`, `<BATCH>` and `<OUT_DIR>` (a scratch directory, never `data/papers`), write one file per citekey, run `check_records.py --batch <BATCH> --dir <OUT_DIR> --no-strip` at most twice and fix between runs, keep helper scripts in a per-batch scratch subdirectory, and reply with the file list. Tell it to copy quotes from the Markdown by splitting on the newline character, not `splitlines()`: MinerU emits Unicode line separators that `splitlines()` treats as line breaks.
3. When the wave's files exist: `uv run scripts/check_records.py --batches <scratch>/bulk --dir <scratch>/bulk/out --promote-to data/papers`. Only records that pass are copied. A killed agent leaves no file and the promote step finds it missing; re-run the same command after relaunching.

A paper the extractor judges out of scope on reading the full text goes into `data/scope-recheck.json` by hand; `build_site.py` keeps those off the site until a ruling, which is either removing the entry (keep) or moving the paper to `excluded.json` and deleting its record (exclude).

## Second pass and tie-break

Every numeric field is read twice by different models, and a third model settles the fields on which the two disagree. Nothing in this section needs a person; a person edits a record only in response to a reader's report, and that edit carries `human_edited: true`.

1. `uv run scripts/build_second_pass.py --out <scratch>/second` writes one skeleton per record (the exposure structure with every number replaced by `?`, and no quotes, protocol text or flags), batch files of five, and renders `docs/second-pass-prompt.md`. With `--citekeys` it does so for the named records only, which is how records from a monthly wave are handled.
2. Launch one `paper-verifier` agent per batch, about ten at a time, with that prompt and its three placeholders substituted. The agent is a different model from the first reader, never opens `data/papers`, and runs `check_second_pass.py --batch <BATCH> --dir <OUT_DIR> --no-strip` at most twice. Give each agent its own `_scratch/batch_NNN/` folder.
3. `uv run scripts/check_second_pass.py --batches <scratch>/second --dir <scratch>/second/records` removes any second-read number whose quote is not in the text. Then `uv run scripts/validate.py --strip-unquoted` applies the mechanical applicability rules to the records, and `uv run scripts/compare_records.py --a data/papers --b <scratch>/second/records --numeric-only --write-second-pass` writes `provenance.second_pass` into every record.
4. `uv run scripts/build_tiebreak.py --second <scratch>/second/records --out <scratch>/tiebreak` writes a case file for every record with an unruled disagreement, giving both values and both quotes per field, packs batches of about fifteen disputed fields, and renders `docs/tiebreak-prompt.md`. Launch one agent per batch on the strongest model available, again about ten at a time; it runs `check_tiebreak.py` at most twice.
5. `uv run scripts/apply_tiebreak.py --cases <scratch>/tiebreak/cases --verdicts <scratch>/tiebreak/verdicts` writes each ruling into `provenance.tiebreak` and corrects the value where the ruling went against the published one. It settles two consequences within the exposure: an in situ block whose numbers are all null has null method fields, and a continuous-wave exposure that now carries a PRF or duty cycle is pulsed. Then `uv run scripts/derive.py` and `uv run scripts/validate.py`.
6. The second reader's records and the verdict files are kept outside the public repository, in the private corpus repository under `second-pass-<date>/`; they are the only place the losing reading survives.

## Vocabulary patches

After a wave, `uv run scripts/vocab_report.py` tallies the free text behind every `other`. Promotion follows SCHEMA.md section 10: edit SCHEMA.md and `schema/paper.schema.json`, write an idempotent `scripts/promote_vocab_<version>.py` that relabels the affected records by string match on their free text and prints every change, run it, and add the changelog line.

## Release

`uv run scripts/release.py` shows the version it would cut; `--apply` writes `VERSION`, rebuilds the site data into the website checkout, prepends the changelog entry, commits and tags. Then push with tags, and in the website repository commit `public/tus-archive/data` on a branch, merge and deploy. `uv run scripts/monthly_summary.py --format linkedin` writes the text for a post from the same diff, so the post cannot disagree with the data.

## Conventions

- Filenames and citekeys are year first: `2021_Collins_InhibitoryThermalEffects….pdf` and `2021collins`, with a letter suffix when a year and surname repeat. `common.paper_stem()` is the only place the convention is written.
- One name, two extensions: `library/<stem>.pdf` and `data/text/<stem>.md`. `status.py` checks the one-to-one invariant.
- Three-state values: a value, `not_reported` (applies, paper is silent) or `null` (does not apply).
- Every number needs a verbatim `source_quote`. No quote, no number. Device strings carry a quote where one was found (`scripts/backfill_device_quotes.py`); it is optional for them.
- `provenance.human_edited = true` protects a record from re-extraction.
- Never screen on a title alone. Sorum 2024 is titled "Tension activation of mechanosensitive K2P channels" and applies 3.5 MHz ultrasound. Return `unspecified` when only a title exists.
- Track what costs money or judgement to produce (the screening ledger, manifest, the exclusion file, records). Ignore what a script rebuilds (want-lists, review lists, sweep output).

## Operating rules

Each of these guards against a failure that produces wrong data without an error.

**An error path and an empty result must never produce the same value.** Crossref answers an unsupported `select` field with HTTP 400 and a rate limit with 429; either read as "no results" turns every lookup into a false negative.

**Verify an agent's file, never its report.** A verdict or record file can be short, carry papers not in its batch, or omit the DOI, with nothing in the agent's reply to say so. `check_verdicts.py` and `check_records.py` exist for this.

**The output contract goes in the prompt.** A running session reads the agent definition once; a change to the prompt reaches every agent launched after it.

**Exclusions override inclusions, and a model will not infer that.** MRgFUS thalamotomy, blood-brain barrier opening and nanoparticle stimulation all satisfy "ultrasound applied to neural tissue" literally. `audit_scope.py` is the deterministic backstop.

**Derived state must not overwrite durable state.** `excluded.json` is append-only. Rebuilding it from the screening ledger would lose the exclusions whose PDFs have since been parked.

**A sweep never bypasses a paywall.** Open access is fetched; everything else is listed for a person.

**PDFs and extracted text are never committed.** `.gitignore` enforces it. Abstracts are different: they are published on paper pages with their source recorded, as PubMed and Europe PMC do, and kept out of the bulk download.

**Nothing that cannot be re-acquired for free is deleted by a script.** Excluded PDFs are parked in `library/_out_of_scope/`. `organise_files.py` refuses to delete more than a fifth of the extracted text in one run.

## Ground truth for the timing fields

Martin et al. 2024, Tables 2.1 to 2.3, give worked ITRUSST timing examples for Johnstone, Zeng and Gaur/Mohammadjavadi. All three papers are in the corpus. Any change to the extraction prompt should be checked against them first.
