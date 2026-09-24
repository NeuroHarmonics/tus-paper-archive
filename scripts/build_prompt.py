#!/usr/bin/env python3
"""Render docs/extraction-prompt.md from the template below and schema/targets.json.

The prompt carries the whole output contract because a running session reads an agent
definition once; the prompt is read at launch. Re-run whenever SCHEMA.md or targets.json
changes so the two cannot drift.

  uv run scripts/build_prompt.py
"""

from __future__ import annotations

import json
import sys

from common import DATA

ROOT = DATA.parent
TARGETS = ROOT / "schema" / "targets.json"
OUT = ROOT / "docs" / "extraction-prompt.md"

TEMPLATE = r"""# The extraction prompt

The canonical prompt for a `paper-extractor` run. Rendered by `scripts/build_prompt.py` from `SCHEMA.md` v1.0 and `schema/targets.json`; edit the template in that script, not this file. Copy verbatim, substituting `<REPO>`, `<BATCH>` and `<OUT_DIR>`.

Always run `uv run scripts/check_records.py --batch <BATCH>` before applying a wave. It exits non-zero on any failure and strips any number whose quote is not in the paper.

---

```
Working directory: <REPO>

You extract structured records for the TUS Paper Archive, a public database of the transcranial ultrasound stimulation literature. Your output is validated by a script and published with every number shown next to the sentence it came from. A wrong number is worse than a missing one.

Papers to extract are listed in: <BATCH>
Each line is:  <citekey>  <doi>  <path to the paper's full text>

For each paper, read the full text and write ONE file: <OUT_DIR>/<citekey>.json
Write every file, one per citekey, no extras. Reply only with the list of files written.

===================== THE THREE-WAY RULE =====================
Every field takes a value, or "not_reported", or null:
  a value         the paper states it in its main text (tables count; figures alone do not)
  "not_reported"  the field applies to this study and the paper does not state it
  null            the field does not apply to this study
Never substitute a typical value, a value from another paper, a manufacturer datasheet, or arithmetic of your own. "not_reported" is a finding we publish.

===================== EVERY NUMBER NEEDS A QUOTE =====================
For every numeric field you fill (including lists), add provenance.source_quotes["<field path>"] = one VERBATIM, CONTIGUOUS sentence or table row from the text containing that number. Copy it exactly as it appears, including odd spacing, LaTeX or HTML table tags. Never abbreviate a quote with "..." or "[...]"; if the numbers are spread over two sentences, quote the one that contains this field's number. If you cannot quote it, write "not_reported". A script deletes any number whose quote is not in the text.
EVERY numeric field path needs its OWN entry, even when several fields come from the same sentence: repeat the sentence under each path. With two exposures that share a sentence, write it under exposures[0].X and again under exposures[1].X.
Field paths look like: "exposures[0].timing.duty_cycle_pct", "exposures[1].fundamental_frequency_khz", "n_subjects".

===================== UNITS =====================
Convert to: kHz (frequency), ms (pulse duration), Hz (PRF), % (duty cycle), s (sonication duration), kPa (pressure), W/cm² (intensity). The quote keeps the paper's original units.
A stated RANGE ("20–120 s", "1.15–1.27 MPa") is recorded as a two-element list [min, max], never as a midpoint. A mean ± SD is recorded as the mean.

===================== EXPOSURES =====================
exposures[] has one entry per distinct anatomical target × fundamental frequency. Do NOT open a new entry for a change in intensity, timing or session; a parameter that varies within one target and frequency is a LIST, e.g. "duty_cycle_pct": [5, 30, 50]. Exception: when the paper sweeps frequency at one target and states no other parameter per frequency (a frequency-response curve), keep ONE exposure with fundamental_frequency_khz as a list rather than several near-empty entries. Sham and control conditions are not exposures; describe them in sham_type and auditory_control.

===================== TIMING: FOUR NUMBERS AND A DESCRIPTION =====================
timing.pulse_duration_ms, timing.pulse_repetition_frequency_hz, timing.duty_cycle_pct, timing.sonication_duration_s as the paper STATES them. Do not compute one from the others, even if the arithmetic is obvious, and do not correct a value that looks like a typo: record the text and add a flag. A script does the arithmetic.
timing.protocol_description: the paper's own description of the stimulation protocol, quoted or lightly trimmed, one to three sentences. Intervals between sonications, trial counts, blocks, ramps, nested structure and session timing all go here, not in numeric fields.
timing.waveform: "continuous" (one uninterrupted tone burst for the whole sonication, no PRF, duty cycle 100 %) | "pulsed" (any periodic on/off gating within the sonication, INCLUDING nested burst-in-train structures the paper does not call theta-burst) | "theta_burst" (the paper calls it theta burst or tbTUS) | "other_patterned" (only: non-periodic or randomised pulse timing, an amplitude-modulated carrier, or single-cycle optoacoustic transients) | "not_reported", or a list of those if the paper uses more than one.

Synonyms:
  fundamental_frequency_khz      fundamental, centre/center, carrier, operating frequency; f0; "a 500 kHz transducer"
  pulse_duration_ms              pulse duration, pulse width, pulse length, tone-burst duration, TBD; cycles per pulse ÷ frequency
  pulse_repetition_frequency_hz  PRF, pulse repetition rate, burst repetition frequency; a stated period converts ("a pulse every 200 ms" is 5 Hz, a unit conversion, not arithmetic you must avoid)
  duty_cycle_pct                 duty cycle, duty factor, DC
  sonication_duration_s          sonication / stimulus / stimulation / train duration; the length of one uninterrupted train; for continuous wave, the exposure duration
THE BURST RULE: papers use "burst" for both the short pulse and the long train. If the paper states duty cycle and PRF, pulse_duration = duty_cycle / PRF tells you which duration "burst" is. If you cannot resolve it, leave pulse_duration_ms "not_reported", put both durations in protocol_description, and add a flag.
For continuous wave: pulse_duration_ms, pulse_repetition_frequency_hz are null; duty_cycle_pct is 100 or null. A single uninterrupted burst per trial with no internal pulsing (e.g. "a 100 ms tone burst") is waveform "continuous" with sonication_duration_s = the burst length. "pulsed" means the sonication is made of repeated short pulses at a PRF.

===================== PRESSURE AND INTENSITY: THREE DOMAINS =====================
free_field.*          values the paper says were measured or specified in water / free field
in_situ.*             values the paper says are in the brain or at the target: simulated, derated, or measured through skull
unspecified_domain.*  values the paper gives without saying where they apply
unspecified_domain.* fields are null unless you place a value there; they are never "not_reported". If the paper reports no pressure or intensity at all, free_field.* and in_situ.* are "not_reported" and unspecified_domain.* are null.
Never derate, never convert between domains, never copy a value from one block to another. When the paper reports the same quantity in two domains, fill both blocks. For in_situ: method = simulation | derating | measurement | "not_reported"; reported_as = single_value | mean_or_range_across_subjects | "not_reported"; when a mean ± SD is given, record the mean and let the quote carry the spread; a range is a two-element list. For a bath or dish with no skull or tissue path, every field INSIDE in_situ is null (the in_situ object itself is still present with its five keys).

n_subjects is the total number of subjects exposed to ultrasound. If the paper states only group sizes and never the total, give the group sizes as a list (e.g. [20, 17]) and do not add them. If enrolled and analysed differ, give enrolled and flag it.

===================== VOCABULARIES (closed; use "other" + the free text when nothing fits) =====================
Fields marked [] are ALWAYS JSON arrays, even with one value: "sham_type": ["none"], never "sham_type": "none". Arrays never contain null or "not_reported" unless the vocabulary lists it (only auditory_control has "not_reported"). A numeric list holds numbers only.
anaesthesia is null for any study whose model_system is only human, tissue or cell work; "awake" is for animals.
A sham whose mechanism is not described is sham_type ["undescribed"]; use "other" only for a mechanism none of the terms names, and say what it was in a flag; a paper that never mentions auditory confounds is auditory_control ["not_reported"].
model_system[]      human_healthy | human_patient | nonhuman_primate | large_animal (sheep, pig, rabbit, dog) | rodent | other_vertebrate | invertebrate | ex_vivo_tissue | in_vitro_cell
conditions[]        healthy | essential_tremor | parkinsons_disease | depression | anxiety | epilepsy | chronic_pain | disorders_of_consciousness | alzheimers_disease | stroke | schizophrenia | substance_use_disorder | traumatic_brain_injury | vascular_dementia (chronic cerebral hypoperfusion models) | retinal_degeneration (RCS, P23H and similar) | systemic_inflammation (LPS endotoxaemia, sepsis-like) | myocardial_infarction (as the disease model for nerve or hypothalamic stimulation) | other  (+ condition_other text). For an animal model, the disease modelled.
subject_unit        participant | animal | preparation | culture
randomised          true | false | "not_reported" | null (tissue and cell work)
blinding            none | single | double | "not_reported" | null (tissue and cell work)
sham_type[]         none (no sham or control arm at all) | inactive_transducer (transducer in place but no acoustic output: powered off, blocked, damped, flipped, or aimed away) | active_control_site (real ultrasound to a different site) | sound_only | unfocused_or_detuned (real output but unfocused, plane-wave, or a minimal dose) | no_treatment_control (a comparison group that received no device and no procedure) | undescribed (the paper has a sham arm but never says how it was produced) | other
auditory_control[]  none (the paper says no control was used) | "not_reported" (the paper is silent on auditory confounds) | masking_sound | ramped_pulses | sound_only_sham | deafened_subjects (incl. ear plugs, ear defenders) | matched_device_sound (active and sham arms produce the same audible device sound) | control_experiment (a separate experiment records from or stimulates the auditory pathway or an off-target site to test an auditory route) | post_hoc_check (subjects asked afterwards or the sound analysed afterwards; no control during the experiment) | other (say what in a flag)
readouts[]          fmri | other_mri (MRS, DTI, ARFI, thermometry, structural) | pet | eeg_meg (incl. evoked potentials) | invasive_electrophysiology (LFP, units, iEEG, patch clamp) | emg_mep | behaviour | clinical_scale | autonomic_physiology | cellular_imaging | histology_molecular | cerebral_haemodynamics (non-MRI CBF/CBV/oxygenation: laser speckle, intrinsic optical signal, functional ultrasound, NIRS, transcranial Doppler) | none_reported | other
readout_other       the specific instruments, e.g. "MDS-UPDRS-III; HAM-D", "stop-signal task", "c-Fos immunostaining"; null if nothing to add
readout_timing      online (measured during sonication) | offline (after) | both | "not_reported"
anaesthesia         awake | anaesthetised | both | "not_reported" | null (human, tissue and cell work)
direction_of_effect excitatory | inhibitory | bidirectional (both, depending on parameters) | no_effect | mixed_or_unclear | not_assessed
direction_notes     one or two sentences: what changed and under which condition
adverse_events      "not_reported" | none_observed | observed | null (tissue and cell work);  safety_notes: what was observed or how safety was assessed, or null
device.family       by MANUFACTURER, even for a custom or one-off model: neurofus (the NeuroFUS system: CTX transducer and TPO driver, one product made by Sonic Concepts and sold by Brainbox; any of NeuroFUS, CTX or TPO named) | sonic_concepts (any other Sonic Concepts transducer, H-series or custom) | brainsonix | insightec | openwater | attune | igt_imasonic | olympus_panametrics (Olympus NDT, Panametrics, Olympus USA) | ultran (The Ultran Group) | blatek | philips_research (TIPS system) | mettler (ME740) | neurosona | other_manufacturer (a named commercial maker not in this list: Precision Acoustics, Hagisonic, Mana, etc.) | custom_built (the paper says the transducer was built or assembled in-house, or names only a piezo element supplier) | "not_reported" (no maker named and nothing said about who built it). The family is the TRANSDUCER's maker. Driving electronics (a Verasonics Vantage scanner, a TPO, a function generator and amplifier) never set it, even when the paper credits the transducer to them; name them in device.model if the paper does
device.manufacturer, device.model   as written; "not_reported" if absent. For neurofus write manufacturer "Sonic Concepts (sold by Brainbox)". Quote the sentence that names the maker or model under exposures[i].device.manufacturer / .model.
species             common English name in lower case, with the strain or Latin binomial in parentheses where the paper gives one: "human", "rat (Sprague-Dawley)", "mouse (C57BL/6J)", "rhesus macaque (Macaca mulatta)"; several, separated by "; "
target.terms[]      one or more ids from the list below, chosen by matching the paper's words and the synonyms; "other" if nothing fits
target.label        the paper's own words for the target, verbatim

TARGET IDS (schema/targets.json; a child implies its parents, so pick the most specific):
%%TARGETS%%

===================== OUTPUT SHAPE (exactly these keys) =====================
{
  "schema_version": "1.0",
  "citekey": "<from the batch line>",
  "doi": "<from the batch line, character for character>",
  "model_system": [...], "species": "...",
  "conditions": [...], "condition_other": null,
  "n_subjects": 0, "subject_unit": "...", "n_sessions_per_subject": 0,
  "randomised": ..., "blinding": "...",
  "sham_type": [...], "auditory_control": [...],
  "readouts": [...], "readout_other": null, "readout_timing": "...", "anaesthesia": ...,
  "direction_of_effect": "...", "direction_notes": "...",
  "adverse_events": ..., "safety_notes": null,
  "exposures": [
    {
      "label": "the paper's name for this condition or a short description",
      "target": {"terms": ["..."], "label": "..."},
      "device": {"family": "...", "manufacturer": "...", "model": "..."},
      "fundamental_frequency_khz": 0,
      "free_field": {"pressure_kpa": ..., "isppa_w_cm2": ..., "ispta_w_cm2": ...},
      "in_situ": {"method": ..., "reported_as": ..., "pressure_kpa": ..., "isppa_w_cm2": ..., "ispta_w_cm2": ...},
      "unspecified_domain": {"pressure_kpa": ..., "isppa_w_cm2": ..., "ispta_w_cm2": ...},
      "timing": {"waveform": "...", "pulse_duration_ms": ..., "pulse_repetition_frequency_hz": ..., "duty_cycle_pct": ..., "sonication_duration_s": ..., "protocol_description": "..."}
    }
  ],
  "provenance": {
    "extracted": {"by": "paper-extractor", "model": "<your model id>", "date": "<today, YYYY-MM-DD>"},
    "source_quotes": {"<field path>": "<verbatim>", ...},
    "flags": [{"field": "<field path>", "reason": "<why a human might look>"}],
    "human_edited": false,
    "notes": null
  }
}

Flag (in provenance.flags) whenever: text and a table disagree; a value is readable only from a figure; units are ambiguous; a number looks like a typo or OCR error; you resolved the burst rule by arithmetic; the protocol did not fit the four numbers; you were unsure which domain a pressure belongs to.

Do NOT write: title, authors, year, journal, abstract, or any "derived" block. A script adds them.
Check your output parses as JSON before writing it.
```
"""


def main() -> int:
    terms = json.loads(TARGETS.read_text())["terms"]
    by_parent: dict[str | None, list[dict]] = {}
    for t in terms:
        by_parent.setdefault(t["parent"], []).append(t)

    lines: list[str] = []

    def walk(parent, depth):
        for t in by_parent.get(parent, []):
            syn = ", ".join(t["synonyms"][:6])
            lines.append("  " * depth + t["id"] + (f"  ({syn})" if syn else ""))
            walk(t["id"], depth + 1)

    walk(None, 0)
    OUT.write_text(TEMPLATE.replace("%%TARGETS%%", "\n".join(lines)))
    print(f"wrote {OUT} ({len(terms)} target ids)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
