#!/usr/bin/env python3
"""Prepare the second pass: a blind re-read of the numeric fields by a second model.

For each record in data/papers this writes a skeleton that keeps the exposure structure
(label, target, device family and maker, waveform) and the study's model system, and replaces
every numeric leaf with "?". Quotes, protocol text, notes and flags are dropped so the second
reader sees no number from the first read. Batch files list `citekey  text_path  skeleton_path`,
five per file, and docs/second-pass-prompt.md is rendered from the template below.

  uv run scripts/build_second_pass.py --out <scratch>/second
  uv run scripts/build_second_pass.py --out <scratch>/second --citekeys 2022zeng 2019yoon
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from common import DATA, read_json
from validate import NUMERIC_LEAVES, PAPER_NUMERIC, set_path

PAPERS = DATA / "papers"
ROOT = DATA.parent
OUT_PROMPT = ROOT / "docs" / "second-pass-prompt.md"

TEMPLATE = r"""# The second-pass prompt

The prompt for the blind second read of the numeric fields. Rendered by `scripts/build_second_pass.py`; edit the template in that script, not this file. The second reader is a different model from the first read and never sees the first read's numbers, quotes or protocol text. It receives a skeleton per paper that fixes the exposure structure, so that `scripts/compare_records.py` can compare field by field, and fills every numeric leaf itself with its own quote. Copy verbatim, substituting `<REPO>`, `<BATCH>` and `<OUT_DIR>`.

---

```
Working directory: <REPO>

You are the independent second reader for the TUS Paper Archive, a public database of the transcranial ultrasound stimulation literature. A first model has already extracted each paper. You have NOT seen its numbers and must not look for them: do not open anything under data/papers. Your reading is compared with the first field by field, and every disagreement is shown to readers as a warning on the paper page. A wrong number is worse than a missing one.

Papers to read are listed in: <BATCH>
Each line is:  <citekey>  <path to the paper's full text>  <path to the skeleton>

For each paper, read the full text, open the skeleton, fill in every "?" and write the result as ONE file: <OUT_DIR>/<citekey>.json
Write every file, one per citekey, no extras. Reply only with the list of files written.

===================== THE SKELETON =====================
The skeleton is the record with every number removed. It fixes the exposure structure: exposures[] has one entry per distinct anatomical target x fundamental frequency, in a set order, each with a label, target terms and device maker. Keep that structure exactly: same number of exposures, same order, same labels. Do not add, remove or merge exposures. If the paper's arms do not fit the given exposures, fill what does fit and add a flag saying why.
Replace every "?" with a value. Leave everything else in the skeleton as it is. Add a "provenance" object with "source_quotes" and "flags" as described below.

===================== THREE STATES =====================
Every numeric field takes one of three values:
  a number or a list of numbers    the paper states it
  "not_reported"                   the field applies to this study but the paper does not state it
  null                             the field does not apply to this study
Never substitute a typical value, a value from another paper, a manufacturer datasheet, or arithmetic of your own. "not_reported" is a finding we publish.

===================== EVERY NUMBER NEEDS A QUOTE =====================
For every numeric field you fill (including lists), add provenance.source_quotes["<field path>"] = one VERBATIM, CONTIGUOUS sentence or table row from the text containing that number. Copy it exactly as it appears, including odd spacing, LaTeX or HTML table tags. Never abbreviate a quote with "..." or "[...]"; if the numbers are spread over two sentences, quote the one that contains this field's number. If you cannot quote it, write "not_reported". A script deletes any number whose quote is not in the text.
EVERY numeric field path needs its OWN entry, even when several fields come from the same sentence: repeat the sentence under each path. With two exposures that share a sentence, write it under exposures[0].X and again under exposures[1].X.
Field paths look like: "exposures[0].timing.duty_cycle_pct", "exposures[1].fundamental_frequency_khz", "n_subjects".
Locate quotes programmatically: one short Python script per paper that opens the .md, splits on "\n" (not splitlines(), because the text contains Unicode line separators), and prints the exact lines containing each number you intend to record. Copy quotes from that output, LaTeX and all. Never retype a quote.

===================== UNITS =====================
Convert to: kHz (frequency), ms (pulse duration), Hz (PRF), % (duty cycle), s (sonication duration), kPa (pressure), W/cm² (intensity). The quote keeps the paper's original units.
A stated RANGE ("20–120 s", "1.15–1.27 MPa") is recorded as a two-element list [min, max], never as a midpoint. A mean ± SD is recorded as the mean.
A parameter that varies within one exposure (several duty cycles at one target and frequency) is a LIST, e.g. "duty_cycle_pct": [5, 30, 50]. A numeric list holds numbers only.

===================== TIMING: FOUR NUMBERS =====================
timing.pulse_duration_ms, timing.pulse_repetition_frequency_hz, timing.duty_cycle_pct, timing.sonication_duration_s as the paper STATES them. Do not compute one from the others, even if the arithmetic is obvious, and do not correct a value that looks like a typo: record the text and add a flag. A script does the arithmetic.
Synonyms:
  fundamental_frequency_khz      fundamental, centre/center, carrier, operating frequency; f0; "a 500 kHz transducer"
  pulse_duration_ms              pulse duration, pulse width, pulse length, tone-burst duration, TBD; cycles per pulse ÷ frequency
  pulse_repetition_frequency_hz  PRF, pulse repetition rate, burst repetition frequency; a stated period converts ("a pulse every 200 ms" is 5 Hz, a unit conversion, not arithmetic you must avoid)
  duty_cycle_pct                 duty cycle, duty factor, DC
  sonication_duration_s          sonication / stimulus / stimulation / train duration; the length of one uninterrupted train; for continuous wave, the exposure duration
THE BURST RULE: papers use "burst" for both the short pulse and the long train. If the paper states duty cycle and PRF, pulse_duration = duty_cycle / PRF tells you which duration "burst" is. If you cannot resolve it, leave pulse_duration_ms "not_reported" and add a flag.
For continuous wave: pulse_duration_ms and pulse_repetition_frequency_hz are null; duty_cycle_pct is 100 or null. A single uninterrupted burst per trial with no internal pulsing (e.g. "a 100 ms tone burst") is continuous with sonication_duration_s = the burst length.

===================== PRESSURE AND INTENSITY: THREE DOMAINS =====================
free_field.*          values the paper says were measured or specified in water / free field
in_situ.*             values the paper says are in the brain or at the target: simulated, derated, or measured through skull
unspecified_domain.*  values the paper gives without saying where they apply
unspecified_domain.* fields are null unless you place a value there; they are never "not_reported". If the paper reports no pressure or intensity at all, free_field.* and in_situ.* are "not_reported" and unspecified_domain.* are null.
Never derate, never convert between domains, never copy a value from one block to another. When the paper reports the same quantity in two domains, fill both blocks. When a mean ± SD is given, record the mean and let the quote carry the spread. For a bath or dish with no skull or tissue path, every numeric field INSIDE in_situ is null.
Pressure is peak (rarefactional or peak-to-peak as the paper states it; record what is stated, do not halve or double). Isppa and Ispta as stated. A mechanical index is not a pressure.

===================== SUBJECTS =====================
n_subjects is the total number of subjects exposed to ultrasound. If the paper states only group sizes and never the total, give the group sizes as a list (e.g. [20, 17]) and do not add them. If enrolled and analysed differ, give enrolled and flag it.
n_sessions_per_subject is the number of ultrasound sessions each subject received, as stated; "not_reported" when the paper is silent; null for tissue and cell work.

===================== FLAGS =====================
provenance.flags is a list of {"field": "<field path>", "reason": "<one sentence>"} for: text and table disagree; a value appears only in a figure; units are ambiguous; the burst rule could not be resolved; the skeleton's exposures do not fit the paper. An empty list when nothing needs saying.

===================== CHECK =====================
When all files in the batch are written, run once:
  uv run scripts/check_second_pass.py --batch <BATCH> --dir <OUT_DIR> --no-strip
Fix what it reports and run it once more. Do not run it a third time: a value whose quote still fails is set to "not_reported" with a flag, which is the correct outcome for a number you cannot ground. Keep any helper scripts under <OUT_DIR>/_scratch/<citekey>/, never in the repository.
```
"""


def skeleton(rec: dict) -> dict:
    sk = {
        "citekey": rec["citekey"],
        "doi": rec["doi"],
        "model_system": rec["model_system"],
        "species": rec.get("species"),
        "subject_unit": rec.get("subject_unit"),
        "exposures": [],
    }
    for f in PAPER_NUMERIC:
        sk[f] = "?"
    for ex in rec["exposures"]:
        e = {
            "label": ex.get("label"),
            "target": ex["target"],
            "device": {"family": ex["device"]["family"], "manufacturer": ex["device"]["manufacturer"]},
            "fundamental_frequency_khz": "?",
            "free_field": {}, "in_situ": {}, "unspecified_domain": {},
            "timing": {"waveform": ex["timing"]["waveform"]},
        }
        for leaf in NUMERIC_LEAVES:
            if leaf != "fundamental_frequency_khz":
                set_path(e, leaf, "?")
        sk["exposures"].append(e)
    return sk


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", type=int, default=5)
    ap.add_argument("--citekeys", nargs="*")
    args = ap.parse_args()

    manifest = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})
    out = pathlib.Path(args.out)
    (out / "skeleton").mkdir(parents=True, exist_ok=True)
    (out / "records").mkdir(parents=True, exist_ok=True)
    for old in out.glob("batch_*.txt"):
        old.unlink()

    rows = []
    for f in sorted(PAPERS.glob("*.json")):
        rec = json.loads(f.read_text())
        ck = rec["citekey"]
        if args.citekeys and ck not in args.citekeys:
            continue
        pdf = (manifest.get(ck, {}).get("files") or {}).get("pdf")
        tp = DATA / "text" / (pathlib.Path(pdf).stem + ".md") if pdf else None
        if not tp or not tp.exists():
            print(f"no text for {ck}", file=sys.stderr)
            continue
        sp = out / "skeleton" / f"{ck}.json"
        sp.write_text(json.dumps(skeleton(rec), indent=1, ensure_ascii=False) + "\n")
        rows.append((ck, str(tp), str(sp)))

    for i in range(0, len(rows), args.size):
        chunk = rows[i:i + args.size]
        (out / f"batch_{i // args.size + 1:03d}.txt").write_text(
            "\n".join("\t".join(r) for r in chunk) + "\n")
    OUT_PROMPT.write_text(TEMPLATE.lstrip("\n"))
    n = (len(rows) + args.size - 1) // args.size
    print(f"{len(rows)} papers in {n} batches of {args.size} -> {out}; prompt -> {OUT_PROMPT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
