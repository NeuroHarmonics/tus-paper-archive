# Text extraction

PDFs are converted to Markdown by [MinerU](https://github.com/opendatalab/MinerU), which is AGPL-3.0, installed into its own environment and run as a separate program; none of its code is in this repository. `scripts/pdf_markdown.py` wraps it, and `scripts/extract_text.py` runs it over the corpus into `data/text/*.md`. MinerU is used rather than `pdftotext` because it preserves reading order, section headings and tables; `pdftotext` remains as a fallback only.

```bash
uv run scripts/extract_text.py                    # whole corpus, resumable
uv run scripts/extract_text.py --force            # re-extract everything
uv run scripts/extract_text.py --only Legon_2014  # only matching filenames
uv run scripts/extract_text.py --limit 5          # stop after 5 extractions
uv run scripts/pdf_markdown.py paper.pdf          # one PDF to stdout
uv run scripts/pdf_markdown.py paper.pdf -o out.md
```

A full pass over the corpus takes several hours, at roughly 40 seconds a paper. The run is resumable: stopped and started again, it skips whatever already has text.

The first run creates a Python 3.13 virtual environment at `~/.cache/tus-paper-archive/mineru-venv` and downloads about 1 GB of models to `~/.cache/huggingface`. Both are kept outside the repository. MinerU does not support Python 3.14, so the 3.13 environment is pinned.

## Using the converter elsewhere

`pdf_markdown.py` does not depend on the rest of the archive. It takes a PDF and returns Markdown, so any project can import it:

```python
from pdf_markdown import MinerU, assess

with MinerU() as m:                    # starts one service and reuses it
    for pdf in pdfs:                   # keep the context open across papers;
        md = m.to_markdown(pdf)        # entering it costs about 20 s of model loading
        problems = assess(md)          # [] when the text looks sound
```

Filenames, page counts and images are handled inside `to_markdown()`; see the operational notes. The only archive-specific import is `common.DOI_BLOCKLIST`.

## What the Markdown contains

The Markdown is assembled by `pdf_markdown.render()` from MinerU's `_content_list.json`, not copied from the `.md` file MinerU writes, because three things are needed that the file does not give:

- Page boundaries. Pages are separated by a form feed (`\f`). `identify.py` reads DOIs from the first three pages by splitting on it.
- No images. Captions are kept, since they often carry parameters. Rendered images, layout PDFs and model dumps are deleted with the temporary directory.
- No page furniture. Running heads, footers and page-number lines are dropped. Two exceptions: headers on page 1 are kept, and so is any header on a later page that contains a DOI, because that slot is where the journal line and the DOI live.

A DOI that MinerU discards is restored. Some publishers set the DOI in a strip that the layout model marks as discarded, and discarded regions reach none of MinerU's output files. `restore_lost_doi()` re-reads page 1 only with pdftotext and appends any DOI the page lacks. Only page 1 is read because pages 2 and 3 hold reference lists, and a reference's DOI appended to page 1 would give `identify.py` another paper's identity. A clipped DOI fragment or a publisher placeholder in `common.DOI_BLOCKLIST` does not count as present.

Tables are inline HTML (`<table>`). Inline maths becomes LaTeX, so artefacts like `$\mathrm{Co.,}$` occur.

Soft hyphens (U+00AD) are stripped. Some publishers encode every hyphen as a hyphen followed by a soft hyphen, which breaks text search on the parameter that follows it.

## The quality gate

`pdf_markdown.assess()` reports out-of-order section headings, nearly empty pages, surviving soft hyphens, a total absence of headings, and implausibly short output. Warnings are recorded in `text-index.json` per file and printed during the run. A short, table-only supplement is exempt from the no-headings check.

### The index

`text-index.json` records, per paper, the text file, its size and SHA, the extractor that produced it, and any warnings. An unfiltered run has seen every PDF, so its index replaces the old one, which is what drops records for papers deleted or moved out of scope. A filtered run (`--only` or `--limit`) merges into the existing index instead, because it did not look at the papers its filter excluded.

## Operational notes

- One PDF per request. MinerU misreads unified memory as 1 GB of GPU memory and overcommits a multi-document batch.
- A service, not a process per file. `pdf_markdown.MinerU` starts one `mineru-api` and submits papers to it, which saves about 20 seconds of model loading per paper.
- Throughput is about 0.35 pages per second on an M-series Mac with MPS. Raising `MINERU_VIRTUAL_VRAM_SIZE` does not help.
- Papers are staged under a neutral filename. MinerU's client cannot handle a path holding `%` or runs of spaces, which publisher-default download names often contain. `to_markdown()` stages every paper as `doc.pdf`.
- Documents over 64 pages are submitted in slices. MinerU hands a whole document to the layout model at once, and long documents fail. `page_idx` is shifted back onto the document per slice, so ordering is preserved.
- pdftotext survives as a fallback only. If MinerU fails on a paper, degraded text is held rather than none. `text-index.json` records the extractor per file, so a fallback is never invisible.
