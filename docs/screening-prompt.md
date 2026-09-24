# The screening prompt

The canonical prompt for a `paper-screener` wave. Copy it verbatim, substituting
`<BATCH>` and `<OUT>`.

The output contract lives here rather than only in the agent definition because a running
session reads the agent file once; a change to the prompt reaches every agent launched after it.

Always validate before applying:

```
uv run scripts/check_verdicts.py --batches <DIR> --verdicts <OUT_DIR>/verdicts_wN_*.json --apply
```

It exits non-zero if any file fails and records nothing; with `--apply` it records every passing file in `data/screening.jsonl`.

---

```
Working directory: <REPO>

Read INCLUSION.md there first. It is the only authority.

Screen every paper in: <BATCH>

OUTPUT CONTRACT: this overrides any format in your agent definition.

Write a JSON array to: <OUT>

Each object MUST have exactly these keys:
  "doi"          - copied EXACTLY, character for character, from the `## <doi>`
                   line. Never normalise, correct or reformat it, and never
                   write a DOI that is not in this batch file. This is how the
                   verdict is matched to the paper.
  "verdict"      - "relevant" | "not_relevant" | "unspecified"
  "record_class" - primary_study | review | systematic_review | meta_analysis |
                   conference_abstract | methods_tool | simulation_only |
                   case_report | letter_commentary | consensus_guideline |
                   erratum | protocol | book | other
  "criterion"    - "E1".."E8" for an exclusion, null when relevant
  "reason"       - one sentence naming the condition or exclusion that decided it
  "resolved_by"  - "agent"
  "under_current_criteria" - true

Do NOT emit any other key.

COMPLETENESS: one object for every `## <doi>` in the batch: no more, no fewer,
no duplicates, none from anywhere else. Extract the DOI list first
(`grep '^## ' <file>`) and work from it. Check your array length before writing.

The test is: did the authors report an ORIGINAL ultrasound exposure delivered to
NEURAL TISSUE with the INTENT of modulating its function? All four must hold. If
you cannot decide, return "unspecified": never guess.

E9 in particular: ultrasound whose intended effect is REPAIR is out. Regeneration,
remyelination, proliferation, differentiation, neurite outgrowth, nerve conduits,
recovery from injury. The line is the ENDPOINT, not the disease: axon counts,
myelin thickness or functional recovery is out; firing rate, excitability, evoked
potentials, BOLD, behaviour under stimulation or a channel response is in,
whatever the study hopes to treat.

Reply with one line: count and the relevant/not_relevant/unspecified split.
```

The reply line is not evidence. Validate the file.
