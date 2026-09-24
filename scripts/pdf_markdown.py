#!/usr/bin/env python3
"""PDF -> markdown. The archive's text extractor, built on MinerU.

Why not pdftotext. It ran the corpus for months and reported 375/375 clean,
because the only check was ">500 characters". Measured against the PDFs it had
in fact scrambled 54 of the 85 papers with numbered methods sections into the
wrong reading order -- in Xie 2023, "2.4 Ultrasound stimulation parameters", the
section Stage 5 exists to read, arrived before 2.3. It also flattened every
table into a column of orphaned cell values and left 6,260 soft hyphens across
75 files. MinerU is layout-aware: on the six worst papers it fixed every
reading-order inversion (5, 5, 4, 2 -> 0), every soft hyphen, and recovered the
tables as HTML. See docs/text-extraction.md for the measurements.

Reusable two ways:

    from pdf_markdown import MinerU
    with MinerU() as m:
        md = m.to_markdown(Path("paper.pdf"))

    uv run scripts/pdf_markdown.py paper.pdf -o paper.md

The markdown is assembled here from MinerU's `_content_list.json`, not taken
from the `.md` it writes, because we need three things that file does not give:
page boundaries, no images, and control over page furniture. Everything else
MinerU produces -- rendered images, layout PDFs, model dumps -- is deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import DOI_BLOCKLIST

# The MinerU venv lives outside the repo: a venv is tens of thousands of small files that do not
# belong in a checkout. It is pinned to Python 3.13 because MinerU does not support 3.14.
VENV = Path.home() / ".cache" / "tus-paper-archive" / "mineru-venv"
MINERU_SPEC = "mineru[core]"
PY_VERSION = "3.13"

# Page furniture MinerU labels for us. Dropping it is most of why the markdown
# reads cleanly: no running heads interrupting a sentence, no "Page 4 of 10"
# stranded mid-paragraph.
FURNITURE = {"header", "footer", "page_number"}

# Above this, submit the PDF in slices. MinerU plans one batch per document and
# hands the whole thing to the layout model at once: a 923-page book (a Methods
# in Molecular Biology volume that arrived in candidates/) killed the request
# outright. Slicing bounds peak memory and lets a long document make progress.
#
# 64, not something larger, because the ceiling was measured rather than guessed
# and it is lower than it looks: on that book a 150-page slice still failed,
# while 100 took 125s and 50 took 62s. 64 leaves room for a slice denser than
# a book page -- a run of full-page figures or tables costs far more than plain
# text, so the page count alone does not bound the work.
CHUNK_PAGES = 64

# Floor for the halving retry: below this a failure is the content, not the batch.
MIN_CHUNK_PAGES = 8

DOI_HINT = re.compile(r"10\.\d{4,9}/", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)")


def _dois(text: str) -> list[str]:
    """Plausible DOIs in `text`, in order, deduplicated.

    The length floor mirrors common.clean_doi: MinerU sometimes clips a DOI at a
    block boundary, and Clennell 2023 arrived as "10.1016/j.b" -- long enough to
    look like a DOI to a naive check, too short for anything to resolve it.
    """
    seen, out = set(), []
    for m in DOI_RE.finditer(text):
        d = m.group(1).rstrip(").,;:'\"]}>")
        if len(d) < 12 or any(d.lower().startswith(b) for b in DOI_BLOCKLIST):
            continue
        if d.lower() not in seen:
            seen.add(d.lower())
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def ensure_venv(quiet: bool = False) -> Path:
    """Provision the MinerU venv on first use. Idempotent."""
    exe = VENV / "bin" / "mineru"
    if exe.exists():
        return exe
    if not shutil.which("uv"):
        sys.exit("uv not found. Install it: brew install uv")
    if not quiet:
        print(f"Provisioning MinerU into {VENV} (one-off, ~1 GB of models on first run)",
              file=sys.stderr)
    VENV.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["uv", "venv", "--python", PY_VERSION, str(VENV)], check=True)
    subprocess.run(["uv", "pip", "install", "-U", MINERU_SPEC],
                   check=True, env={**os.environ, "VIRTUAL_ENV": str(VENV)})
    if not exe.exists():
        sys.exit(f"MinerU install finished but {exe} is missing")
    return exe


def page_count(pdf: Path) -> int:
    """Pages in a PDF, or 0 if pdfinfo cannot say (callers treat 0 as one slice)."""
    try:
        out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True,
                             text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    for line in out.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return 0
    return 0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MinerU:
    """A running MinerU service plus the conversion call.

    The service exists purely to amortise model loading. Converting one paper
    through a fresh `mineru` process takes ~50s, of which ~20s is loading the
    models; against a warm service the same paper takes ~29s. Over a 375-paper
    pass that difference is about two hours.

    It also sidesteps a real bug: passing several PDFs to one `mineru`
    invocation failed 2 of 6 on the bench set with an empty error message, and
    both succeeded when submitted alone. MinerU misreads unified memory as
    "GPU Memory: 1 GB" and overcommits the batch. One PDF per request.
    """

    def __init__(self, quiet: bool = False):
        self.exe = ensure_venv(quiet)
        self.quiet = quiet
        self.port = _free_port()
        self.proc: subprocess.Popen | None = None
        self.workdir: str | None = None

    def __enter__(self) -> MinerU:
        # The service writes a scratch `output/` tree -- uploaded PDF copies and
        # rendered page images, ~10 MB a paper -- relative to its own working
        # directory, ignoring the -o we pass per request. Left at the default it
        # fills the repo with copies of
        # copyrighted PDFs. Give it a temp directory of its own instead.
        self.workdir = tempfile.mkdtemp(prefix="mineru-server-")
        self.proc = subprocess.Popen(
            [str(self.exe.parent / "mineru-api"), "--host", "127.0.0.1",
             "--port", str(self.port)],
            cwd=self.workdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 300
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"mineru-api exited with {self.proc.returncode}")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/docs", timeout=2)
                return self
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                time.sleep(2)
        raise RuntimeError("mineru-api did not become ready within 300s")

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)
            self.workdir = None

    def to_markdown(self, pdf: Path, timeout: int | None = None) -> str:
        """Convert one PDF. Raises RuntimeError on failure."""
        n = page_count(pdf)
        blocks: list[dict] = []
        # Submit under a neutral filename. MinerU's client cannot handle a path
        # holding "%" or runs of spaces -- publisher-default download names like
        # "Low%E2%80%90Intensity ... .pdf" and "Immunity Inflam   Disease - ...
        # .pdf" failed outright, one of them by burning a 900s timeout on a
        # 12-page paper. Both converted in ~40s once staged as "doc.pdf". The
        # exact offending character class is unclear and not worth pinning down:
        # staging every paper costs a local copy and removes the whole class.
        with tempfile.TemporaryDirectory(prefix="mineru-src-") as staging:
            src = Path(staging) / f"doc{pdf.suffix or '.pdf'}"
            shutil.copy2(pdf, src)
            for start in range(0, max(n, 1), CHUNK_PAGES):
                end = min(start + CHUNK_PAGES, n) - 1 if n else None
                blocks.extend(self._convert_span(src, start, end, timeout))
        return restore_lost_doi(pdf, render(blocks))

    def _convert_span(self, src: Path, start: int, end: int | None,
                      timeout: int | None) -> list[dict]:
        """One slice, halving on failure. Returns absolutely-indexed blocks.

        Slice size alone does not bound the work: on the 923-page book, pages
        0-191 convert happily in 64-page slices at ~82s each while 192-255 fails
        at any size -- a dense run of full-page figures costs far more than the
        page count suggests. Halving lets a document survive a bad patch instead
        of losing all 900 good pages to it.

        Below MIN_CHUNK_PAGES the failure is the content, not the batch, so give
        up and let the caller fall back rather than grind through single pages.
        """
        span = (end - start + 1) if end is not None else CHUNK_PAGES
        try:
            got = self._convert_range(src, start, end,
                                      timeout or max(600, span * 8))
        except (RuntimeError, subprocess.TimeoutExpired):
            if end is None or span <= MIN_CHUNK_PAGES:
                raise
            mid = start + span // 2
            return (self._convert_span(src, start, mid - 1, timeout)
                    + self._convert_span(src, mid, end, timeout))
        # page_idx restarts at 0 in every slice; shift it back onto the document
        # so render() lays the pages out in the right order.
        for b in got:
            try:
                b["page_idx"] = int(b.get("page_idx", 0)) + start
            except (TypeError, ValueError):
                b["page_idx"] = start
        return got

    def _convert_range(self, pdf: Path, start: int, end: int | None,
                       timeout: int) -> list[dict]:
        cmd = [str(self.exe), "-p", str(pdf), "-b", "pipeline", "-m", "auto",
               "--api-url", f"http://127.0.0.1:{self.port}"]
        if end is not None:
            cmd += ["-s", str(start), "-e", str(end)]
        with tempfile.TemporaryDirectory(prefix="mineru-") as tmp:
            res = subprocess.run(cmd + ["-o", tmp], capture_output=True,
                                 text=True, timeout=timeout)
            hits = list(Path(tmp).rglob("*_content_list.json"))
            if not hits:
                tail = (res.stderr or res.stdout or "").strip().splitlines()[-3:]
                raise RuntimeError("; ".join(tail)[:300] or f"exit {res.returncode}")
            blocks = json.loads(hits[0].read_text(encoding="utf-8"))
        self._prune_server_scratch()
        return blocks
        # TemporaryDirectory takes the images, layout PDFs and model dumps with it.

    def _prune_server_scratch(self) -> None:
        """Empty the service's own output tree. Per paper, not per run: over a
        375-paper pass it would otherwise reach several GB."""
        if not self.workdir:
            return
        shutil.rmtree(Path(self.workdir) / "output", ignore_errors=True)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _captions(block: dict, *names: str) -> list[str]:
    out = []
    for n in names:
        v = block.get(n)
        if isinstance(v, str):
            v = [v] if v.strip() else []
        out.extend(x for x in (v or []) if isinstance(x, str) and x.strip())
    return out


def render(blocks: list[dict]) -> str:
    """Assemble markdown from MinerU's block list, one page at a time.

    Pages are separated by a form feed. That is not decorative: identify.py
    reads DOIs from "the first three pages", which it gets by splitting on the
    form feeds pdftotext used to emit. Keeping the separator means the whole
    identification stage carries over unchanged. Form feeds are invisible to
    grep and to every markdown renderer.
    """
    pages: dict[int, list[str]] = {}
    for b in blocks:
        try:
            page = int(b.get("page_idx", 0))
        except (TypeError, ValueError):
            page = 0
        chunk = _render_block(b, page)
        if chunk:
            pages.setdefault(page, []).append(chunk)

    out = "\f".join(
        "\n\n".join(pages.get(p, [])) + "\n"
        for p in range(max(pages) + 1 if pages else 0)
    )
    # Soft hyphens are a typesetting hint, never content, and they break search
    # silently because they are invisible. Some publishers encode every hyphen as
    # "hyphen + U+00AD", so Kim 2024 held "30-\xadHz" and a grep for "30-Hz" found
    # nothing. Strip them: there is no case where keeping one helps a reader.
    return out.replace("\u00ad", "")


def _render_block(b: dict, page: int) -> str:
    kind = b.get("type")
    text = (b.get("text") or "").strip()

    if kind in FURNITURE:
        # Running heads are noise on every page but the first, where the same
        # slot carries the journal line -- and with it the DOI that identify.py
        # depends on. Keep page 1, and keep any later line bearing a DOI.
        if kind == "page_number":
            return ""
        if page == 0 or DOI_HINT.search(text):
            return text
        return ""

    if kind == "text":
        if not text:
            return ""
        level = b.get("text_level")
        if level:
            try:
                return "#" * min(int(level) + 1, 6) + " " + text
            except (TypeError, ValueError):
                pass
        return text

    if kind == "page_footnote":
        return text

    if kind == "list":
        items = b.get("list_items") or []
        if isinstance(items, str):
            items = [items]
        joined = "\n".join(str(i).strip() for i in items if str(i).strip())
        return joined or text

    if kind == "table":
        parts = _captions(b, "table_caption")
        body = (b.get("table_body") or "").strip()
        if body:
            parts.append(body)
        parts.extend(_captions(b, "table_footnote"))
        return "\n\n".join(parts)

    if kind in ("image", "chart"):
        # No image files: the figure is in the PDF if
        # anyone needs it. The caption is the part that carries parameters.
        parts = _captions(b, "image_caption", "chart_caption",
                          "image_footnote", "chart_footnote")
        content = (b.get("content") or "").strip()
        if content:
            parts.append(content)
        return "\n\n".join(parts)

    return text


def restore_lost_doi(pdf: Path, md: str) -> str:
    """Put back a first-page DOI that MinerU's layout model threw away.

    Some publishers set the DOI in a strip that MinerU classifies as
    "discarded", and discarded regions never reach `content_list.json` --
    nor `_middle.json`, nor `_model.json`. The text is simply gone, so no
    amount of care in render() recovers it. Measured on the first full pass:
    15 of 383 papers lost a DOI that pdftotext finds, which would quietly
    degrade identify.py from ~100% to ~96%.

    Deliberately narrow, because the failure mode of getting this wrong is
    worse than the bug:

    - **Page 1 only.** Pages 2-3 carry reference lists, and a reference's DOI
      appended to page 1 would make identify.py confidently adopt some other
      paper's identity.
    - **Only when page 1 has no usable DOI.** If MinerU kept one, it is the
      article's own and we leave well alone. "Usable" excludes clipped
      fragments and the publisher placeholders in common.DOI_BLOCKLIST, both of
      which look like DOIs but resolve to nothing.
    """
    pages = md.split("\f")
    if not pages or _dois(pages[0]):
        return md
    try:
        out = subprocess.run(["pdftotext", "-f", "1", "-l", "1", str(pdf), "-"],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return md

    found = _dois(out)
    if not found:
        return md

    pages[0] = (pages[0].rstrip() + "\n\n"
                + "\n".join(f"https://doi.org/{d}" for d in found) + "\n")
    return "\f".join(pages)


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------

SECTION = re.compile(r"^#*\s*(\d{1,2})\.(\d{1,2})\.?\s+([A-Z][A-Za-z][A-Za-z ,\-]{6,})\s*$")
UNIT = re.compile(r"\b(W/cm|MHz|kHz|MPa|Mpa|mm|ms|Hz|%)\b")


def assess(md: str) -> list[str]:
    """Warnings about an extraction. Empty list means it looks sound.

    The old gate was `len(text) > 500`, which is why a corpus of scrambled text
    reported itself clean for months. These checks look at whether the document
    holds together, not whether bytes arrived.
    """
    warnings = []
    pages = md.split("\f")
    body = md.replace("\f", "")

    if len(body.strip()) < 500:
        warnings.append(f"only {len(body.strip())} chars - may be image-only or failed")
        return warnings  # everything below is meaningless on an empty document

    empty = sum(1 for p in pages if len(p.strip()) < 40)
    if len(pages) > 2 and empty / len(pages) > 0.25:
        warnings.append(f"{empty}/{len(pages)} pages nearly empty")

    secs = [(int(m.group(1)), int(m.group(2)))
            for line in body.splitlines()
            if (m := SECTION.match(line.strip())) and not UNIT.search(line)]
    inv = sum(1 for a, b in zip(secs, secs[1:]) if b < a)
    if inv:
        warnings.append(f"{inv} out-of-order section heading(s) - reading order suspect")

    if soft := body.count("­"):
        warnings.append(f"{soft} soft hyphens survived")

    if not re.search(r"^#{1,6}\s", body, re.M):
        # A supplement that is nothing but tables genuinely has no headings, and
        # flagging it trains the reader to ignore this gate -- which is how the
        # old >500-character check managed to certify a scrambled corpus.
        tabular_supplement = body.count("<table") >= 1 and len(body) < 8000
        if not tabular_supplement:
            warnings.append("no headings detected - structure may be lost")

    return warnings


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="write here (default: stdout)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not args.pdf.exists():
        sys.exit(f"not found: {args.pdf}")

    with MinerU(quiet=args.quiet) as m:
        md = m.to_markdown(args.pdf)

    for w in assess(md):
        print(f"WARN {args.pdf.name}: {w}", file=sys.stderr)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"wrote {args.out} ({len(md)} chars)", file=sys.stderr)
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
