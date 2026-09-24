# The extraction prompt

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
brain
  cerebral_cortex  (cortex, neocortex)
    hemisphere_unspecified
    frontal_lobe  (frontal cortex)
      motor_cortex  (motor area)
        primary_motor_cortex  (M1, precentral gyrus, hand knob, hand motor area, BA4, area 4)
        premotor_cortex  (PMC, PMd, dorsal premotor, ventral premotor)
        supplementary_motor_area  (SMA, supplementary motor area, pre-SMA)
      prefrontal_cortex  (PFC, prefrontal)
        dorsolateral_prefrontal_cortex  (dlPFC, DLPFC)
        dorsomedial_prefrontal_cortex  (dmPFC)
        ventromedial_prefrontal_cortex  (vmPFC, medial prefrontal, mPFC)
        orbitofrontal_cortex  (OFC, orbital frontal)
        anterior_prefrontal_cortex  (aPFC, frontopolar, area 10, BA10)
      inferior_frontal_gyrus  (IFG, inferior frontal cortex, IFC, area 47/12o, ventrolateral prefrontal, vlPFC)
      frontal_eye_field  (FEF)
    sensorimotor_cortex  (sensorimotor, SMC)
    parietal_lobe  (parietal cortex)
      somatosensory_cortex  (somatosensory)
        primary_somatosensory_cortex  (S1, SI, postcentral gyrus, barrel cortex, hindlimb S1, forelimb S1)
        secondary_somatosensory_cortex  (S2, SII)
      posterior_parietal_cortex  (PPC, intraparietal sulcus, IPS, inferior parietal, superior parietal)
      precuneus
    occipital_lobe  (occipital cortex)
      visual_cortex
        primary_visual_cortex  (V1, striate cortex, BA17, area 17, calcarine)
        extrastriate_visual_cortex  (V2, V3, V4, V5, MT, area MT)
    temporal_lobe  (temporal cortex)
      auditory_cortex
        primary_auditory_cortex  (A1, Heschl)
      superior_temporal_gyrus  (STG, superior temporal sulcus, STS)
      middle_temporal_gyrus  (MTG)
      inferior_temporal_cortex  (inferior temporal gyrus, IT cortex, inferotemporal)
      temporal_pole
      entorhinal_cortex  (entorhinal)
      parahippocampal_cortex  (parahippocampal, perirhinal)
    cingulate_cortex  (cingulate, cingulate gyrus)
      anterior_cingulate_cortex  (ACC, anterior cingulate)
        dorsal_anterior_cingulate_cortex  (dACC, dorsal ACC, BA24, area 24)
        subgenual_anterior_cingulate_cortex  (sgACC, subgenual cingulate, SCC, subcallosal cingulate, BA25, area 25)
        pregenual_anterior_cingulate_cortex  (pgACC, pregenual, rostral ACC, rACC)
      posterior_cingulate_cortex  (PCC, posterior cingulate)
      retrosplenial_cortex  (retrosplenial, RSC)
    insular_cortex  (insula, insular)
      anterior_insula  (anterior insular)
      posterior_insula  (posterior insular, mid-insula, mid insula, middle insula)
    piriform_cortex  (piriform)
    temporoparietal_junction  (TPJ)
  subcortical
    hippocampal_formation
      hippocampus  (hippocampal)
        CA1  (CA1)
        CA3  (CA3)
        dentate_gyrus  (DG)
      subiculum
    amygdala  (amygdalar)
      basolateral_amygdala  (basolateral amygdala, BLA)
      central_amygdala  (central amygdala, CeA)
    septal_nuclei  (septal nuclei, medial septum, lateral septum)
    habenula  (lateral habenula, LHb)
    basal_ganglia  (basal ganglia)
      striatum  (striatal)
        caudate_nucleus  (caudate)
        putamen
        dorsal_striatum  (dorsolateral striatum, dorsomedial striatum)
        nucleus_accumbens  (NAc, NAcc, accumbens, ventral striatum)
      globus_pallidus  (pallidum, pallidal)
        globus_pallidus_internus  (GPi, globus pallidus internus, internal segment)
        globus_pallidus_externus  (GPe, globus pallidus externus)
      subthalamic_nucleus  (STN)
      substantia_nigra  (SNc, SNr, pars compacta, pars reticulata, nigral)
    zona_incerta  (ZI, posterior subthalamic area, PSA, caudal zona incerta, cZI)
    thalamus  (thalamic)
      ventral_intermediate_nucleus  (VIM, Vim, ventral intermediate nucleus, ventralis intermedius, ventral intermediate)
      ventral_lateral_nucleus  (VL, ventrolateral thalamus, ventral lateral thalamus, VLp, VLa, VoP)
      ventral_anterior_nucleus  (VA, ventral anterior)
      ventral_posterolateral_nucleus  (VPL, ventroposterolateral, ventral posterolateral)
      ventral_posteromedial_nucleus  (VPM, ventral posteromedial)
      ventral_posterior_nucleus  (VP, ventrobasal, VB, sensory thalamus, ventral posterior)
      mediodorsal_nucleus  (MD, mediodorsal, dorsomedial thalamus)
      centromedian_nucleus  (CM, centromedian, centre median, CM-Pf, intralaminar)
      anterior_thalamic_nucleus  (anterior thalamus, anterior thalamic, ANT)
      pulvinar  (pulvinar)
      lateral_geniculate_nucleus  (LGN, lateral geniculate, dLGN)
      medial_geniculate_nucleus  (MGN, medial geniculate, MGB)
      reticular_thalamic_nucleus  (thalamic reticular, TRN)
      posterior_thalamic_nucleus  (posterior thalamus, Po, posterior nucleus)
    hypothalamus  (hypothalamic)
      lateral_hypothalamus  (lateral hypothalamus, LH)
      paraventricular_nucleus  (paraventricular nucleus, PVN)
      arcuate_nucleus  (arcuate nucleus, ARC)
      ventromedial_hypothalamus  (ventromedial hypothalamus, VMH)
      suprachiasmatic_nucleus  (SCN)
    basal_forebrain  (BF, nucleus basalis, nucleus basalis of Meynert, medial septum, diagonal band, septal)
  whole_brain_or_unfocused  (whole brain, global, unfocused, whole-brain)
  brainstem  (brain stem)
    midbrain  (mesencephalon)
      superior_colliculus  (SC)
      inferior_colliculus  (IC)
      periaqueductal_gray  (PAG, periaqueductal grey)
      ventral_tegmental_area  (VTA)
      red_nucleus
      pretectal_area  (pretectal, pretectum)
    pons  (pontine)
      locus_coeruleus  (locus coeruleus, LC)
      pedunculopontine_nucleus  (pedunculopontine, PPN, PPTg)
      parabrachial_nucleus  (parabrachial)
    raphe_nuclei  (dorsal raphe, DRN, raphe)
    medulla  (medulla, medullary)
      nucleus_tractus_solitarius  (nucleus tractus solitarius, NTS, solitary nucleus)
      dorsal_motor_nucleus_of_vagus  (dorsal motor nucleus, DMV, DMNV)
      rostral_ventrolateral_medulla  (RVLM, rostral ventrolateral medulla)
    reticular_formation  (reticular activating)
  cerebellum  (cerebellar)
    cerebellar_cortex  (cerebellar hemisphere, vermis, lobule, Crus)
    deep_cerebellar_nuclei  (deep cerebellar nuclei, dentate nucleus, fastigial, interposed)
  white_matter  (white matter, tract, fiber bundle)
    corpus_callosum  (callosal)
    internal_capsule  (anterior limb of the internal capsule, ALIC, capsulotomy target)
    corticospinal_tract  (CST, pyramidal tract)
    dentatorubrothalamic_tract  (DRT, DRTT, dentato-rubro-thalamic, cerebellothalamic)
    cingulum_bundle  (cingulum, cingulum bundle)
    medial_forebrain_bundle  (MFB, slMFB)
spinal_cord
  spinal_dorsal_horn  (dorsal horn)
  cervical_spinal_cord  (cervical cord)
  thoracic_spinal_cord  (thoracic cord)
  lumbar_spinal_cord  (lumbar cord, lumbosacral)
peripheral_nervous_system
  peripheral_nerve  (peripheral nerve)
    vagus_nerve  (vagus, vagal, cervical vagus, auricular branch, subdiaphragmatic vagus)
    sciatic_nerve  (sciatic)
    median_nerve
    ulnar_nerve
    radial_nerve
    tibial_nerve  (posterior tibial)
    peroneal_nerve  (peroneal nerve, common peroneal, fibular nerve, CPN)
    femoral_nerve
    phrenic_nerve  (phrenic)
    trigeminal_nerve  (trigeminal, infraorbital nerve)
    facial_nerve
    optic_nerve
    auditory_nerve  (auditory nerve, cochlear nerve, vestibulocochlear)
    splanchnic_nerve  (splanchnic)
    saphenous_nerve
    digital_nerve  (finger nerve)
    nerve_injury_site  (crush site, transection site, lesion site)
  ganglion  (ganglia)
    dorsal_root_ganglion  (DRG)
    trigeminal_ganglion  (Gasserian)
    superior_cervical_ganglion  (SCG, sympathetic ganglion, stellate ganglion)
    nodose_ganglion  (inferior ganglion of vagus)
  nerve_plexus  (plexus, brachial plexus, lumbar plexus)
  enteric_nervous_system  (myenteric, enteric neurons, enteric)
  neuromuscular_junction  (NMJ, motor endplate, motor axon terminal)
sensory_organ
  retina  (retinal, retinal ganglion, photoreceptor)
  cochlea  (cochlear, hair cell, organ of Corti)
  vestibular_organ  (vestibular, semicircular canal, otolith)
  olfactory_epithelium  (olfactory)
tissue_preparation
  cultured_neurons  (cultured neurons, primary neurons, primary culture, neuronal culture, cortical neurons in culture, hippocampal neurons in culture)
  cultured_glia  (astrocytes, astrocyte culture, microglia, BV2, oligodendrocyte precursor, OPC)
  brain_slice  (brain slice, acute slice, hippocampal slice, cortical slice, organotypic, slice culture)
  retinal_explant  (retinal explant, isolated retina, ex vivo retina)
  excised_nerve  (excised nerve, isolated nerve, ex vivo nerve, nerve preparation, nerve bundle)
  brain_organoid  (organoid, cerebral organoid, brain organoid, spheroid)
  dorsal_root_ganglion_culture  (DRG neurons, cultured DRG, DRG culture)
invertebrate_nervous_system
  c_elegans_neurons  (C. elegans, Caenorhabditis, ASH neuron, AWC, PVD, mechanosensory neurons)
  drosophila_neurons  (Drosophila, fly brain, larval)
  aplysia_neurons  (Aplysia, abdominal ganglion, buccal ganglion)
  leech_neurons  (leech, Hirudo)
  earthworm_neurons  (earthworm, Lumbricus, ventral nerve cord, giant fiber, giant fibre, giant axon)
  crayfish_neurons  (crayfish, crab, lobster, stomatogastric)
other

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
