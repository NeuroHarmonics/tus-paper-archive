# Inclusion criteria

## The test

A paper is included when it reports an original ultrasound exposure delivered to neural tissue with the intent of modulating its function. A paper that does not is excluded. There is no other route in.

This is the same question as whether the paper contributes a row to the parameter tables. The archive exists to make acoustic dose and pulse timing comparable across the literature, and a paper that never sonicated neural tissue has no row to contribute. Tying inclusion to that makes the criterion checkable rather than a matter of taste, and it bounds the archive without a special rule for each adjacent field.

### The four conditions, all required

| | condition | what it excludes |
|---|---|---|
| A | Original. The authors performed the exposure themselves. | Reviews, meta-analyses, commentary, editorials, textbook chapters |
| B | Ultrasound. Acoustic energy at or above about 20 kHz, delivered as a sustained waveform at a carrier frequency. | Vibration, shockwave and transcranial pulse stimulation, magneto-acoustic, electrical, optical and audible-sound stimulation |
| C | Delivered to neural tissue. The acoustic focus was placed on neural tissue: neurons, nerves, ganglia, plexus, brain, spinal cord, retina or an invertebrate nervous system, in culture, slice, in vivo or in the clinic. | Devices, transducers, phantoms, water-tank characterisation, field simulation and computational models, and organs whose response is neurally mediated (see the note on C) |
| D | Intent is to modulate function. Not to destroy tissue, open a barrier, deliver an agent or image. | Ablation, MRgFUS thalamotomy, blood-brain barrier opening, drug and gene delivery, sonothrombolysis, imaging |

Note on C. The focus must be on the nerve, not on an organ the nerve serves. Ultrasound aimed at the spleen, gut, heart, kidney or bladder is excluded even when the effect is neurally mediated and the authors call it neuromodulation. Ultrasound aimed at the cervical vagus nerve, a ganglion or a nerve plexus is included. The line is where the focus sits, because that is what the parameter tables describe: an exposure to the spleen and an exposure to the vagus nerve are not comparable dosimetry even when they share a mechanism. For the same reason, unfocused therapeutic ultrasound applied over a body region in physiotherapy, a wrist, a shoulder or the lumbar spine, is not an exposure of a nerve that runs beneath it, even when nerve conduction is measured. C holds when the paper names the nerve as the target, focused or not, and fails when it names a region or a joint.

Note on A. An exposure is recorded once, from the first paper that reports it. A later paper that reanalyses the data of an exposure already reported, or pools reports of exposures already published, contributes no new parameter row and is excluded under E3, even when its authors performed the exposure. A paper that adds participants or sessions is a new exposure and is included. The main report of a trial is the record of its exposure, even when a secondary analysis was published first; once the main report is published, the earlier secondary analysis is excluded under E3. Until then, a secondary analysis is the first report of that exposure and is included.

One qualifier on C. The tissue must be native. Tissue engineered to respond to ultrasound, through sonogenetics, gas vesicles, piezoelectric or mechanoluminescent particles or activatable liposomes, belongs to a different field.

### Two things the test does not ask

- Whether the paper reported its parameters well. The test is whether the exposure was delivered, not whether it was written up properly. A study that sonicates and states nothing is included with every parameter `not_reported`. How often the field omits a parameter is one of the findings the archive exists to produce.
- Whether the paper is any good. Impact factor, sample size and author reputation play no part. Users judge quality from the parameters recorded.

### Why condition C carries most of the weight

Condition C does most of the work of bounding the archive.

Devices. Transcranial ultrasound hardware descends from HIFU, and the same transducers appear in ablation, blood-brain barrier and neuromodulation papers. A rule that admitted devices used for TUS would admit that whole lineage. Condition C never asks what a device was for, only whether this paper sonicated neural tissue with it. A CTX-500 characterised in a water tank is excluded; the same transducer applied to a motor cortex is included.

Modelling. Simulation of transcranial fields is an unbounded literature, and much of it serves ablation. A k-Wave study of skull aberration models a field; it does not sonicate a neuron, so it is excluded. This excludes some important work, including the SONIC and NICE neuron models, and that is an accepted loss, because "models of neuromodulation" has no natural edge.

Reviews. No original exposure, so excluded under condition A. The key documents list below is the one small exception.

Note on D. Repair is not modulation. Ultrasound that makes a nerve regrow, a stem cell proliferate or an oligodendrocyte myelinate changes what the tissue is, not what it is doing. That literature is large and is a different field from ultrasonic neuromodulation, with its own conduits, scaffolds and growth assays. It is excluded under E9.

The line is the purpose of the exposure, not the endpoint and not the disease. Ultrasound delivered to native neural tissue to change what it is doing satisfies D whatever the study measures and whatever condition it hopes to treat: this includes protecting tissue from injury, reducing its inflammation or pathology, and altering a cellular state such as autophagy or glial reactivity. These studies use the same exposures as the rest of the archive, often over many sessions, and their parameter rows are comparable dosimetry. A record whose paper measures no neural activity carries `direction_of_effect: not_assessed` and is found by its readouts. Compte 2023 is included because it sonicates the substantia nigra to protect dopaminergic neurons. Kim 2026 is included because repressing TRPA1-dependent astrocyte reactivity is a change in what the tissue is doing. Glia in the brain or in culture are neural tissue under C.

E9 is a judgement about the archive's scope rather than a fact about a paper, so every paper excluded under it is listed with its DOI in `data/parked-regeneration.md`. Reversing the ruling would be a re-screen of that list, not a new sweep.

---

## Exclusion codes

Each code is the negation of one condition, so exactly one applies to a given paper. The first that holds is cited.

- E1. No ultrasound. No acoustic energy applied at all. (Condition B.)
- E2. Nothing neural was sonicated. Phantom, water tank, excised skull, cadaver, non-neural tissue, simulation or model only, or a device characterised without application to neural tissue. (Condition C.)
- E3. Not the authors' own exposure. Review, systematic review, meta-analysis, perspective, commentary, editorial, a letter commenting on others' work, book chapter, a report summarising others' data, or a reanalysis or pooled report of an exposure already reported (see the note on A). Also any article in JoVE (Journal of Visualized Experiments), which demonstrates a protocol rather than giving the first report of an exposure. A research letter that reports the authors' own exposure is an original report and is judged on the four conditions like any other paper. (Condition A.)
- E4. The intent is not modulation. Thermal ablation, MRgFUS thalamotomy, capsulotomy, pallidotomy, HIFU lesioning, histotripsy, blood-brain barrier opening, drug or gene delivery, sonothrombolysis, lithotripsy, diagnostic or functional imaging, physiotherapy of non-neural tissue. Applies even when a neural or behavioural outcome is reported. E4 applies to an exposure whose stated purpose is to open the blood-brain barrier or to deliver an agent, and to any arm given with microbubbles or another agent; clearance of solutes or debris by ultrasound alone is judged under D like any other purpose. (Condition D.)
- E5. The stimulus is not ultrasound. Transcranial pulse stimulation and other shockwave methods, magneto-acoustic stimulation, mechanical vibration, membrane-tension steps, pipette pressure, audible sound or infrasound, electrical current, light. Also ultrasound used as a wireless power link to an electrically stimulating implant, or as the imaging modality for some other stimulus. (Condition B.)
- E6. Engineered sensitisation. The response depends on an introduced agent: sonogenetics, transgenes conferring ultrasound sensitivity, gas vesicles, nanobubbles, piezoelectric, Janus or mechanoluminescent particles, activatable liposomes. (Native-tissue qualifier.)
- E7. Not a citable publication. No DOI or equivalent stable identifier; slide decks, blog posts, marketing material. Dissertations and theses at any level, whatever identifier they carry: the work enters the archive when it is published as a paper. A repository copy of an article published elsewhere, and a data or code deposit, for the same reason: the article is the publication. Retracted papers are recorded with the retraction notice.
- E8. Conference output. Anything published through a conference: meeting abstracts, poster abstracts, proceedings summaries, and full peer-reviewed proceedings papers (IEEE EMBC, IUS, UFFC and MEMS, ACM, SPIE proceedings, Journal of Physics Conference Series). Excluded as a class, applied by `common.is_conference()` rather than by judgement; see the note below.
- E9. The purpose of the exposure is to make neural tissue grow or regrow: regeneration, remyelination, neurogenesis, proliferation, differentiation or neurite outgrowth stated as the aim of the exposure, or an exposure to a transected, crushed or conduit-bridged nerve, a spinal cord lesion, or a stem or progenitor cell culture. A growth measure among a paper's endpoints does not by itself make E9 apply; the stated aim does. Protection, anti-inflammatory and disease-modifying aims are not E9. (Condition D.)

Note on E8. Meeting abstracts rarely carry parameters, and many are superseded by a full paper already in the archive, so keeping them would double-count those studies in every aggregate. Proceedings PDFs also interleave abstracts, so the extracted text of one carries its neighbours'. Full proceedings papers often do report parameters, but a boundary that needs a decision per paper does not hold, so conference output is excluded as a class by a rule that needs no model. The cost is a small number of studies whose only publication is a proceedings paper. When such work appears as a full paper it enters with the parameters it needs.

Judge on the methods, not the title. Sorum 2024 is titled "Tension activation of mechanosensitive K2P channels" and applies 3.5 MHz ultrasound. Where only a title is available and it does not settle all four conditions, the verdict is `unspecified`.

---

## Key documents

A short list of reviews, consensus statements and reporting standards, kept by hand in [`data/key-documents.json`](data/key-documents.json). These are not database records: they carry no parameters, are not shown on the site, and are excluded from every aggregate. The sweep uses them as seeds for citation chaining, which needs review DOIs to chase references from without those reviews becoming records.

The list is added to by hand only. Nothing feeds it automatically, no sweep proposes entries for it, and it has a soft cap of about 30 entries. That is what keeps it from growing back into the review literature.

---

## Borderline cases

A screener that cannot settle all four conditions returns:

```json
{"verdict": "unspecified", "reason": "which condition could not be settled, and why"}
```

`unspecified` is a correct answer. It is logged for a person to resolve, and a resolution that recurs becomes a worked example below.

A screener does not guess. A wrong inclusion publishes a bad record; a wrong exclusion buries a paper in a ledger that is rarely re-read. Noticing that a paper resembles a worked example is a reason to return `unspecified`, not a reason to decide.

---

## Worked examples

Each of these looks at first as though it should go the other way.

| paper | verdict | which condition decides it |
|---|---|---|
| MRgFUS thalamotomy for essential tremor | exclude | D. Ultrasound to neural tissue with a tremor outcome, and still ablation. Applying ultrasound to the brain is never sufficient on its own. |
| Subthreshold test sonications during an MRgFUS thalamotomy, analysed afterwards for reversible tremor effects | exclude | D. The test sonications are part of the ablation procedure. They are included only when the paper shows they were neuromodulation-style pulses rather than standard thermal test sonications. |
| Sorum 2024, "Tension activation of K2P channels" | exclude | C. Applies 3.5 MHz ultrasound, but to proteoliposomes and Xenopus oocytes. No neural tissue. |
| Qiu 2019, Piezo1 in neurons | include | All four. The knockdown identifies a native channel's role; it does not confer sensitivity. |
| Hou 2021, gas vesicles as ultrasound actuators | exclude | Native-tissue qualifier. The agent is introduced to make the tissue respond. |
| Optoacoustically generated ultrasound on cortex | include | B is satisfied. The source is a laser, but what reaches the tissue is ordinary ultrasound. Transcranial pulse stimulation differs because the waveform itself differs. |
| MR-ARFI focal-spot localisation in a phantom | exclude | C. Nothing neural was sonicated. |
| The same MR-ARFI used to verify a focus during a neuromodulation session | include | C is satisfied: that session sonicated neural tissue. |
| Eldirdiri 2026, MR-ARFI in rat brain to evaluate targeting before neuromodulation, with no neural outcome | exclude | D. Neural tissue was sonicated, but to characterise the focus. No session modulated anything. |
| A TUS protocol given to people only to measure how audible it is | include | D. The pulse is a neuromodulation pulse and its auditory effect is a neural effect, as in the held auditory-confound studies (Braun 2020, Johnstone 2021, Kop 2024). |
| Airborne ultrasound pulses used as a sound stimulus for an animal that hears ultrasound (cricket, bat, rat) | exclude | D. Hearing research with ultrasound as the sound, not ultrasound delivered to modulate neural tissue. Pulsed ultrasound coupled to the head is a different case. |
| A stimulation system sonicating mouse brain only to show that it is safe | exclude | D. The purpose is device safety; any modulation is reported as unintended. |
| Pulsed ultrasound aimed at an intracerebral hematoma to fragment the clot | exclude | D. The exposure is aimed at the clot, as in sonothrombolysis, not at neural tissue. |
| Diagnostic B-mode or Doppler given to fetal or embryonic brain to test for functional harm | exclude | D. Diagnostic imaging pulses are excluded whatever is measured. A neuromodulation exposure at diagnostic-level intensity is included, and most neuromodulation papers state that they are within diagnostic limits. The pulse and the purpose decide, not the intensity. |
| Martin 2024, ITRUSST reporting consensus | exclude as a record; listed under key documents | A. No original exposure. |
| Nguyen 2025, a Brain Stimulation letter on mechanical artefacts during tFUS of rat hippocampus | include | A. A research letter reporting the authors' own exposure; the article type does not decide condition A. |
| LIPUS for remyelination after stroke | exclude | E9. The stated aim is myelin regrown. See the note on D. |
| LIPUS to a transected sciatic nerve in a conduit, judged on axon counts and walking track | exclude | E9. The stated aim is regrowth across a gap. |
| LIFU after middle cerebral artery occlusion, judged on infarct volume and IgG leakage | include | D. The purpose is to protect cortex, not to regrow it. `direction_of_effect` is `not_assessed`; the readouts are `histology_molecular`. |
| LIPUS to the hippocampus of an Alzheimer model, judged on plaque load, microglia and water-maze performance | include | D. Disease modification is a change in what the tissue is doing. A growth measure among the endpoints does not make it E9. |
| Ultrasound repressing TRPA1-dependent astrocyte reactivity | include | All four. A channel's response is what the tissue is doing, even where the aim is neuroprotection. |
| Pulsed 1 MHz ultrasound over the carpal tunnel for carpal tunnel syndrome, with median nerve conduction measured | exclude | Note on C. The target is the wrist, not the nerve. Compare Ahmed 2022. |
| Schachtner 2026, default-mode connectivity in the patients of Schachtner 2025 | exclude | E3. The exposure is already recorded from Schachtner 2025. See the note on A. |
| A laboratory's pooled symptom and safety report across its published studies | exclude | E3. Every exposure in it is already reported. |
| Liu 2023, FUS alone versus FUS with microbubbles | include | The FUS-alone arm satisfies all four. Only that arm is recorded; the microbubble arm fails D. |
| Xu 2023, ultrasound-powered nerve implant | exclude | E5. Ultrasound is the power link; the neural stimulus is electrical current. |
| Fry 1958, reversible changes in the CNS | include | All four. Age is not a criterion. |
| Ahmed 2022, splenic ultrasound neuromodulation | exclude | Note on C. The focus is on the spleen. The anti-inflammatory effect is neurally mediated and the paper calls it neuromodulation, but the focus is not on a nerve. |
| Ultrasound to the cervical vagus nerve | include | C. The focus is on the nerve itself. |
| Contact focused ultrasound on fingertip skin, with cutaneous receptors named as the target | include | C. Receptor endings are the nerve's own endings, as the retina is. |
| Mid-air haptics: an airborne focused beam on the palm or fingertip | exclude | E5. The beam acts on the skin surface as a pressure stimulus and does not deliver ultrasound into tissue. |
| Kiss 2025, multi-focus tremor abstract | exclude | E8, and it is superseded by Shrestha 2026, which is held. |

---

## Excluded papers

Each is recorded in `data/excluded.json` with DOI, title, the condition that failed and the date, and its PDF moves to `library/_out_of_scope/`. Nothing is deleted. The record serves two purposes: the monthly sweep skips DOIs already decided, and the file is an exclusion log in the PRISMA sense. Every paper in the archive has been screened against these criteria.
