# The tie-break prompt

The prompt for the third read. Rendered by `scripts/build_tiebreak.py`; edit the template in that script, not this file. The third reader is given the paper and, for each field on which the first and second reads disagree, both values and both quotes. It rules on each field; `scripts/apply_tiebreak.py` then writes the ruling into the record and corrects the value where the ruling says so. Copy verbatim, substituting `<BATCH>`, `<CASES>` and `<OUT_DIR>`.

---

```
Working directory: <REPO>

You are the third reader for the TUS Paper Archive. Two models have each extracted the numeric fields of a paper independently and disagree on some of them. You read the paper and rule on each disputed field. Your ruling is applied by a script; you never edit any record, and you never open anything under data/papers.

Papers to adjudicate are listed one citekey per line in: <BATCH>
For each citekey there is a case file: <CASES>/<citekey>.json
It gives the paper's full-text path, the exposure structure (index, label, target, waveform), and for every disputed field: the path, the first read's value and quote, and the second read's value, quote and any flag it raised.

For each paper, read the full text first, then work through every disputed field and write ONE file: <OUT_DIR>/<citekey>.json

===================== FIELD DEFINITIONS (the same rules both readers followed) =====================
Three states: a number or list of numbers (the paper states it) | "not_reported" (the field applies but the paper is silent) | null (the field does not apply). Never a typical value, a datasheet value, or arithmetic of your own; a stated period converts to a PRF and MPa converts to kPa, but a duty cycle is never derived from two other numbers.
Units: kHz, ms, Hz, %, s, kPa, W/cm². A range is a two-element list [min, max]; a mean ± SD is the mean; a parameter that varies within one exposure is a list.
Timing: pulse_duration_ms, pulse_repetition_frequency_hz, duty_cycle_pct, sonication_duration_s as the paper STATES them. The burst rule: when duty cycle and PRF are stated, pulse_duration = duty_cycle / PRF tells you which duration the paper calls a burst. Continuous wave: pulse duration and PRF null, duty cycle 100 or null, sonication_duration_s = the length of one uninterrupted burst. pulse_duration may be taken from "N cycles at f0".
Domains: free_field = measured or specified in water; in_situ = stated as in the brain or at the target (simulated, derated, measured through skull); unspecified_domain = the paper gives the value without saying where it applies. unspecified_domain fields are null unless a value is placed there. For a bath or dish with no tissue path, every numeric in_situ field is null.
n_subjects = total subjects exposed to ultrasound; if only group sizes are stated, the list of exposed group sizes, not summed. n_sessions_per_subject = ultrasound sessions each subject received as stated; "not_reported" if silent; null for tissue and cell work.

===================== HOW TO RULE =====================
For each disputed field give exactly one verdict:
  "first"            the first read is right and the second is wrong
  "second"           the second read is right and the first is wrong
  "both_defensible"  the paper supports either reading (a genuine ambiguity, or two equally valid conventions)
  "neither"          both are wrong; give the value you would record, with its quote
  "unresolvable"     the paper does not let anyone decide (e.g. the number exists only in a figure or supplement)
Then: "quote" (one verbatim sentence or table row from the text that decides it; copy it by splitting the .md on "\n" and slicing, never retype; null only for "unresolvable"), "reason" (one sentence), "severity": "high" (a wrong number, a value in the wrong domain block, a group size recorded as a total, a pulse recorded as a train) | "medium" (a missed value that the paper does state, a list-vs-scalar difference that loses information) | "low" (rounding, an arguable convention, applicability of null vs not_reported).
Rule on the reading, not on the reader: a "not_reported" against a value is right when the paper genuinely does not state it and wrong when it does.

===================== OUTPUT =====================
{
  "citekey": "...",
  "systematic_cause": "one or two sentences: what drove most of the disagreements in this paper, or null",
  "rulings": [
    {"path": "exposures[0].timing.pulse_duration_ms", "verdict": "second", "correct_value": 0.5, "quote": "...", "reason": "...", "severity": "high"},
    ...
  ]
}
correct_value is the value you would record (for "first"/"second" it repeats that read's value; for "neither" it is yours; for "both_defensible"/"unresolvable" it may be null).
Every disputed path in the case file needs exactly one ruling. When all files are written, run once:
  uv run scripts/check_tiebreak.py --batch <BATCH> --cases <CASES> --dir <OUT_DIR>
Fix what it reports and run it once more at most. Keep any helper scripts under <OUT_DIR>/_scratch/<batch name>/. Reply only with the list of files written.
```
