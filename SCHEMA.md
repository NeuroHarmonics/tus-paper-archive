# Schema: what is recorded about each included paper

**Version 1.0.** Every record carries `schema_version: "1.0"`, and `schema/paper.schema.json` is the machine-checkable form of this document.

The constraint that shaped the schema is that values are not checked by hand. A field is in version 1 only if a model reads it correctly nearly every time, or if an error in it is caught by arithmetic, or if it is free text where the paper's own words are the value. A field that would need a person to fit it is left out, and the paper's own protocol description stands in for it. Adding a field later is a single-field backfill; only the structural choices in section 1 are fixed.

Naming follows Martin et al. 2024 (ITRUSST) where a field has an ITRUSST name. The parameters are recorded; the ITRUSST hierarchical timing table is not reconstructed.

---

## 1. Structural decisions

**1.1 Timing is four numbers and a description.** Fundamental frequency, pulse duration, pulse repetition frequency, duty cycle and sonication duration are recorded as the paper states them. Everything else about the protocol, including inter-sonication intervals, trial counts, nested levels, ramps and session structure, goes in `protocol_description` as the paper's own words. The reader gets the exact sentence, uninterpreted. The duty cycle serves as a check: where the paper states it, `pulse_duration × PRF` must reproduce it, and a mismatch is flagged at build. That arithmetic catches the most common systematic timing error, confusing the pulse with the train.

**1.2 One exposure per distinct target and frequency; sweeps stay in one row.** A new `exposures[]` entry opens only when the anatomical target or the fundamental frequency changes. A swept parameter holds a list. The site filters on any value in the list and excludes swept rows from joint-distribution charts. Per-condition rows are a v2 job.

**1.3 Sham, control, direction and safety are paper-level.** Sham arms are not exposures. What was done, what was found and whether anyone was harmed are each one categorical field with a free-text note.

**1.4 Pressure and intensity are tagged by domain, by structure.** Free-field (water), in-situ (brain) and unspecified are three separate blocks. Values are never derated, never converted between domains and never copied from one block to another. Many papers populate only `unspecified_domain`, and the site's intensity filter offers free-field, in-situ, or any.

**1.5 Three-way values.** Every field is a value, `"not_reported"` (applies to this study and the paper is silent in its main text) or `null` (does not apply). `not_reported` is a finding in its own right. Only the main text is read, so a value given only in a supplement or a figure is recorded as `not_reported`; the site says so.

**1.6 Every number carries a verbatim quote, and the quote is checked.** Quotes are one sentence, attributed to the paper, and shown collapsed under the number. Short attributed quotation of a published work is the quotation exception in UK law and fair use in the US. `provenance.source_quotes` maps each numeric field to the sentence it came from, and `check_records.py` confirms the quote occurs in the paper's text. A number without a verifiable quote is deleted at validation. This check is deterministic and is what prevents an invented value from being published.

**1.7 Bibliography and abstracts are not extracted.** Title, authors, year, journal and DOI come from Crossref via `data/manifest.json`. Abstracts come from Europe PMC, PubMed or Crossref into `data/abstracts/` with the source recorded, and are published on paper pages.

---

## 2. How it is used, and the queries the schema must answer

The site is a filter over structured values that returns a list of exposures and the papers they belong to, with the numbers side by side in fixed units. Every field a query below touches is an enum or a number, never free text. Source quotes exist so that the numbers can be published without being checked by hand; they are collapsed under each value on the paper page and take no part in search.

| reader | the query, in their words | fields it runs on |
|---|---|---|
| Researcher designing a study | "Human studies that targeted the thalamus: what frequency, duty cycle and intensity, and did anything happen?" | `model_system`, `target.terms`, `fundamental_frequency_khz`, `duty_cycle_pct`, intensities by domain, `direction_of_effect` |
| Same | "Everything done with a NeuroFUS system at 500 kHz, as one table I can compare." | `device.family`, `fundamental_frequency_khz`, then every numeric column |
| Same | "Theta-burst protocols in humans, and how long the sonication was." | `waveform = theta_burst`, `model_system`, `sonication_duration_s` |
| Clinician or trialist | "Sham-controlled, double-blind depression trials with five or more sessions." | `conditions`, `sham_type`, `blinding`, `n_sessions_per_subject` |
| Same | "Any adverse events reported in patient studies above 5 W/cm² in situ?" | `adverse_events`, `model_system`, `in_situ.isppa_w_cm2` |
| Device developer or regulatory reviewer | "What in-situ pressures and intensities have humans received, and how was in situ estimated?" | `in_situ.pressure_kpa`, `in_situ.isppa_w_cm2`, `in_situ.method`, `model_system` |
| Methodologist | "Pulse duration against PRF across the literature, and the share of papers that report each." | numeric fields, `not_reported` counts, swept rows excluded from joint plots |
| Same | "Awake versus anaesthetised animal studies with electrophysiology readouts." | `anaesthesia`, `readouts`, `model_system` |
| Same | "Online effects only, measured during sonication." | `readout_timing` |
| Newcomer | "Search: amygdala." | title, abstract, `substructure`, `protocol_description` |

Four facets exist because these queries need them and each is a reliable categorical read: `device.family`, `waveform` extended with `theta_burst`, `readout_timing`, `anaesthesia`.

Not recorded in version 1: mechanical index and thermal metrics, which few papers state and which are often regulatory ceilings rather than measurements; transducer aperture, focal depth and focal width; the reconstructed ITRUSST hierarchy; inter-sonication intervals as numbers; trial counts; total exposure duration; per-condition effects. Safety is the categorical `adverse_events`. The device model string carries the geometry where the paper gives it.

**Units are fixed** so columns compare: kHz, ms, Hz, %, s, kPa, W/cm². The extractor converts from the paper's units and the quote shows the original, so a conversion slip is visible on the paper page and caught by the sanity checks in §5.

---

## 3. Paper-level fields

| field | type | applies | vocabulary / notes |
|---|---|---|---|
| `model_system` | enum[] (≥1) | always | `human_healthy` `human_patient` `nonhuman_primate` `large_animal` (non-rodent, non-primate mammal) `rodent` `other_vertebrate` `invertebrate` `ex_vivo_tissue` (slice, excised nerve) `in_vitro_cell` |
| `species` | text | always | common English name in lower case, with the strain or Latin binomial in parentheses where the paper gives one: `human`, `rat (Sprague-Dawley)`, `mouse (C57BL/6J)`, `rhesus macaque (Macaca mulatta)`; several separated by `; ` |
| `conditions` | enum[] (≥1) | always | `healthy` `essential_tremor` `parkinsons_disease` `depression` `anxiety` `epilepsy` `chronic_pain` `disorders_of_consciousness` `alzheimers_disease` `stroke` `schizophrenia` `substance_use_disorder` `traumatic_brain_injury` `vascular_dementia` (chronic cerebral hypoperfusion models) `retinal_degeneration` (RCS, P23H and similar photoreceptor-degeneration models) `systemic_inflammation` (LPS endotoxaemia and sepsis-like models) `myocardial_infarction` (ischaemia or infarction as the disease model for nerve or hypothalamic stimulation) `other`. For an animal model, the disease modelled. `other` requires `condition_other`. |
| `condition_other` | text | when `other` | |
| `n_subjects` | int | always | subjects exposed to ultrasound |
| `subject_unit` | enum | always | `participant` `animal` `preparation` `culture` |
| `n_sessions_per_subject` | int | always | `1` for a single visit |
| `randomised` | bool | in vivo | `null` for tissue and cell work |
| `blinding` | enum | in vivo | `none` `single` `double` `not_reported`; `null` for tissue and cell work |
| `sham_type` | enum[] (≥1) | always | `none` (no sham or control arm) `inactive_transducer` `active_control_site` `sound_only` `unfocused_or_detuned` (real output, unfocused or minimal) `no_treatment_control` (no device, no procedure) `undescribed` (a sham arm whose mechanism is never stated) `other` |
| `auditory_control` | enum[] (≥1) | always | `none` (the paper says no control was used) `not_reported` (the paper is silent) `masking_sound` `ramped_pulses` `sound_only_sham` `deafened_subjects` (includes ear plugs and ear defenders) `matched_device_sound` (active and sham arms produce the same audible device sound) `control_experiment` (a separate experiment records from or stimulates the auditory pathway or an off-target site to test an auditory route) `post_hoc_check` (subjects asked afterwards, or the sound analysed afterwards, with no control applied during the experiment) `other` |
| `readouts` | enum[] (≥1) | always | §3.1 |
| `readout_other` | text | always | the specific instruments: "MDS-UPDRS-III", "stop-signal task", "GFAP immunostaining". `null` if nothing to add. |
| `readout_timing` | enum | always | `online` (measured during sonication) `offline` (after) `both` `not_reported` |
| `anaesthesia` | enum | animal in vivo | `awake` `anaesthetised` `both`; `null` for human, tissue and cell work |
| `direction_of_effect` | enum | always | `excitatory` `inhibitory` `bidirectional` (parameter-dependent) `no_effect` `mixed_or_unclear` `not_assessed` |
| `direction_notes` | text | always | one or two sentences on what changed and under which condition |
| `adverse_events` | enum | in vivo | `not_reported` `none_observed` `observed`; `null` for tissue and cell work |
| `safety_notes` | text | always | what was observed, or how safety was assessed. `null` if nothing. |

### 3.1 `readouts[]` vocabulary

Thirteen coarse terms. A model confuses fine ones and a reader filters on coarse ones; the instrument goes in `readout_other`.

| value | covers |
|---|---|
| `fmri` | BOLD, ASL, CBF |
| `other_mri` | MRS, DTI, structural safety scans, MR-ARFI, MR thermometry |
| `pet` | |
| `eeg_meg` | EEG, MEG, ERP and all evoked potentials |
| `invasive_electrophysiology` | LFP, single and multi-unit, intracranial EEG, patch clamp |
| `emg_mep` | EMG, TMS-evoked MEP |
| `behaviour` | any task, reaction time, detection, choice, memory, motor, pain report, mood scale |
| `clinical_scale` | UPDRS, CRST, MADRS, HAM-D and the like |
| `autonomic_physiology` | heart rate, HRV, blood pressure, skin conductance, pupil, temperature |
| `cellular_imaging` | calcium imaging, fibre photometry |
| `histology_molecular` | histology, immunohistochemistry, c-Fos, gene expression |
| `cerebral_haemodynamics` | non-MRI cerebral blood flow, volume or oxygenation: laser speckle, intrinsic optical signal, functional ultrasound and power Doppler, NIRS, transcranial Doppler |
| `none_reported` / `other` | |

---

## 4. Per-exposure fields

One entry per distinct **target × fundamental frequency** (§1.2).

| field | type | applies | vocabulary / notes |
|---|---|---|---|
| `label` | text | always | the paper's own name for the condition, or a short description |
| `target.terms` | vocab[] (≥1) | always | one or more terms from the controlled neuroanatomical vocabulary in §4.1, chosen by matching the paper's own words and synonyms. Search runs over the term and every ancestor, so "thalamus" finds VIM. |
| `target.label` | text | always | the paper's own words, verbatim: "left M1 hand knob", "VPL nucleus of the thalamus", "cervical vagus nerve" |
| `device.family` | enum | always | `neurofus` (the NeuroFUS system: CTX transducer and TPO driver, one product made by Sonic Concepts and sold by Brainbox) `sonic_concepts` (any other Sonic Concepts transducer, H-series or custom) `brainsonix` `insightec` `openwater` `attune` `igt_imasonic` `olympus_panametrics` `ultran` `blatek` `philips_research` `mettler` `neurosona` `other_manufacturer` (a named commercial maker not listed) `custom_built` (the paper says the transducer was built or assembled in-house, or names only a piezo element supplier) `not_reported` (no maker named and nothing said about who built it). The facet a reader filters on; the model string carries the detail. The family is the transducer's maker. Driving electronics (a Verasonics Vantage, a TPO, a function generator and power amplifier) never set it, whatever the paper credits them with; they go in the model string when the paper names them. |
| `device.manufacturer` | text | always | as written, or `"custom"`. One exception: for the `neurofus` family it is always `"Sonic Concepts (sold by Brainbox)"`, because papers credit either the maker or the distributor and some split one system into two makers. A device quote (the sentence naming maker or model) is stored when the extractor or `scripts/backfill_device_quotes.py` finds one; it is optional, unlike the quote for a number. |
| `device.model` | text | always | verbatim |
| `fundamental_frequency_khz` | number | always | synonyms §4.2 |
| `free_field.pressure_kpa` | number | always | in water, as stated |
| `free_field.isppa_w_cm2` | number | always | |
| `free_field.ispta_w_cm2` | number | always | |
| `in_situ.method` | enum | skull or tissue path | `simulation` `derating` `measurement` `not_reported`; `null` for a bath or dish |
| `in_situ.reported_as` | enum | skull or tissue path | `single_value` `mean_or_range_across_subjects` `not_reported` |
| `in_situ.pressure_kpa` | number | skull or tissue path | the stated value, or the mean where a distribution is given; the quote carries the spread |
| `in_situ.isppa_w_cm2` | number | skull or tissue path | |
| `in_situ.ispta_w_cm2` | number | skull or tissue path | |
| `unspecified_domain.pressure_kpa` | number | always | a value the paper gives without saying where it applies; `null` when the paper did say |
| `unspecified_domain.isppa_w_cm2` | number | always | |
| `unspecified_domain.ispta_w_cm2` | number | always | |
| `timing.waveform` | enum | always | `continuous` (one uninterrupted tone burst for the whole sonication; no PRF, duty cycle 100 %) `pulsed` (any periodic on/off gating within the sonication, including nested burst-in-train structures the paper does not call theta-burst) `theta_burst` (the paper calls it theta-burst or tbTUS) `other_patterned` (non-periodic or randomised pulse timing, an amplitude-modulated carrier, or single-cycle transients from an optoacoustic source) `not_reported` |
| `timing.pulse_duration_ms` | number | pulsed | synonyms §4.2 |
| `timing.pulse_repetition_frequency_hz` | number | pulsed | |
| `timing.duty_cycle_pct` | number | pulsed | **as stated by the paper.** A computed value is compared at build (§5). |
| `timing.sonication_duration_s` | number | always | one uninterrupted train, or the CW exposure |
| `timing.protocol_description` | text | always | **the paper's own description of the stimulation protocol**, one to three sentences, quoted or lightly trimmed. Intervals, trials, blocks, ramps, nested levels, session structure all live here. Displayed and searchable, never filtered. |

Any numeric field may hold a **list** when the paper sweeps it.

### 4.1 Target vocabulary

Targets are recorded against `schema/targets.json`, a curated subset of **UBERON**, the cross-species anatomy ontology, which is the only standard that covers human cortex, macaque thalamic nuclei, rodent brainstem nuclei, peripheral nerves, retina and cultured tissue in one hierarchy with stable identifiers. Each entry carries a short label, the UBERON id, the parent chain, and the synonyms papers use (M1, dlPFC, sgACC, VIM, VPL, LGN, PAG, NTS and so on). The extractor picks from this list; anything it cannot place goes to `other` with the paper's words in `target.label`, and a recurring `other` is how the list grows.

The list holds the region terms that appear in the corpus, on the order of 150 entries, and grows through the `other` route in section 10. Searching at any level of the hierarchy makes "who has stimulated the thalamus" and "who has stimulated VIM" each a single query.

### 4.2 Synonym table, carried verbatim into the extraction prompt

| field | the paper may say |
|---|---|
| `fundamental_frequency_khz` | fundamental, centre/center, carrier or operating frequency; f₀; "a 500 kHz transducer" |
| `pulse_duration_ms` | pulse duration, pulse width, pulse length, tone-burst duration, TBD; cycles per pulse divided by frequency; **burst duration** only when `duty_cycle / PRF` says it is the short one |
| `pulse_repetition_frequency_hz` | PRF, pulse repetition rate, burst repetition frequency; the inverse of a pulse repetition period |
| `duty_cycle_pct` | duty cycle, duty factor, DC |
| `sonication_duration_s` | sonication, stimulus, stimulation or train duration; **burst duration** when it is the long one; "80 s of tbTUS" |

**The burst rule.** If the paper states a duty cycle and a PRF, `pulse_duration = duty_cycle / PRF` and the paper's "burst" is whichever duration the arithmetic matches. If it cannot be resolved, leave `pulse_duration_ms` as `not_reported`, put both numbers in `protocol_description`, and flag.

---

## 5. Derived at build, never extracted

`scripts/derive.py` adds a `derived` block to each exposure. Extractors do not write it.

- `duty_cycle_computed_pct = pulse_duration_ms × PRF / 10`, and `duty_cycle_mismatch: true` when it differs from the stated value by more than 5 % relative. Displayed on the paper page as a note; not corrected.
- Sanity flags: `pulse_duration ≤ 1/PRF`; `20 kHz ≤ f₀ ≤ 20 MHz`; where pressure and I_sppa are both given in one domain, `I ≈ p²/2ρc` within a factor of two.
- Reportability: per field, whether the value is `not_reported`, aggregated on the site.
- Per-field status for badging: `quote_verified`, `second_pass`, `tiebreak`, `flagged`. `second_pass` is `agree` when two reads stand behind the value: either the first and second read agreed, or a tie-break ruled and the value was set to the ruling. It is `disagree` when the reads differ and the tie-break found both readings defensible or the paper undecidable.

---

## 6. Provenance

| field | notes |
|---|---|
| `extracted.by / model / date` | agent name, model id, ISO date |
| `source_quotes` | `{field_path: verbatim}` for every numeric field. Checked against the text; a number whose quote fails the check is removed. |
| `second_pass` | `{field_path: "agree" \| "disagree"}` from an independent re-read of the numeric fields by a second model that saw the paper and the exposure structure but none of the first read's values or quotes |
| `tiebreak` | `{field_path: "first" \| "second" \| "both_defensible" \| "neither" \| "unresolvable"}` from a third model that read the paper with both readings in front of it and ruled on each disputed field. `second` and `neither` mean the value was corrected to the ruling, with the ruling's quote; `first` means the value stood; the other two leave the value and the disagreement badge in place |
| `flags[]` | `[{field, reason}]` written by the extractor when text and table disagree, a value is figure-only, units are ambiguous, or the protocol did not fit |
| `human_edited` | `true` once a person has edited the record; protects it from re-extraction |
| `notes` | free text |

**Everything that passes validation is published.** A field on which the reads still disagree, or that carries an extractor flag, is shown with its badge, not withheld. Disagreements between the first and second read are settled by the tie-break, not by a person: the third reader's ruling is written into the record by `scripts/apply_tiebreak.py`, and where it corrects a value the new value carries the ruling's quote and passes the same quote check as any other. Corrections from readers arrive through GitHub issues and land as edits with `human_edited: true`, which protects the record from re-extraction and from the tie-break.

---

## 7. Reliability by field tier

| tier | fields | expectation | what catches errors |
|---|---|---|---|
| categorical | everything in §3 except the notes; `target.terms`; `device.family`; `in_situ.method`; `waveform` | near-perfect agreement between two reads | second pass on a sample |
| unambiguous numbers | `fundamental_frequency_khz`, `duty_cycle_pct`, `pulse_repetition_frequency_hz`, `n_subjects` | high; named unambiguously in text | quote check |
| numbers needing a reading | `pulse_duration_ms`, `sonication_duration_s`, intensities and pressures by domain | good, not perfect | quote check, duty-cycle arithmetic, second pass, tie-break, badge |
| free text | `protocol_description`, `substructure`, `direction_notes`, `safety_notes`, `readout_other` | the paper's own words, so accuracy is copying | reader |

---

## 8. Never extracted

- Bibliography and abstracts (§1.7).
- A review's own table of other studies, which would enter the archive as unattributed duplicates.
- Device-implied values. Nothing is filled in from a device's datasheet or from another paper using the same device.
- Anything taken from the same group's other papers, or a typical value for the device.

---

## 9. Deferred to v2

Each is a single-field backfill on records below the version that introduces it: mechanical index with an is-limit flag, thermal rise and thermal index; transducer aperture, focal depth and focal width with its definition; the ITRUSST hierarchical timing table; inter-sonication interval, trial count and total exposure duration as numbers; per-condition exposure rows and per-condition direction; radius of curvature, element count and array geometry; the ITRUSST free-field geometry block; operating vs centre frequency; ramp shape and target; I_spta averaging level; coupling; target laterality and coordinates; age and sex; trial registration; effect duration; concurrent modality; in-situ spread as numbers.

---

## 10. Extending a vocabulary

Every closed vocabulary (`conditions`, `readouts`, `sham_type`, `auditory_control`, `device.family`, `target.terms`, `waveform`) has an `other` value and a free-text companion that carries the paper's own words. Extension follows a fixed procedure:

1. The extractor never invents a term. It picks from the list or writes `other` plus the free text.
2. After each wave, `scripts/vocab_report.py` tallies the free text behind every `other` across the corpus, normalised for case and punctuation, and lists anything appearing in **three or more papers**.
3. Promotion is an edit to `SCHEMA.md` and `paper.schema.json` (a patch version, `1.0 → 1.1`), plus a rule in `scripts/vocab_report.py --apply` that re-labels the affected records by string match on the preserved free text. **No re-extraction.** The free text stays on the record, so promotion is reversible.
4. For targets, promotion means adding a UBERON entry with its synonyms to `schema/targets.json`; the extractor's `target.label` is what the match runs on.
5. Nothing is demoted or renamed in a patch version. Removing or renaming a term is a minor version and is batched.

The report also lists free text that occurs in only one or two papers, so the long tail is visible without being promoted.

---

## Changelog

- **1.0** (2026-09-22). Initial public version.
