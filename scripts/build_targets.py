#!/usr/bin/env python3
"""Build schema/targets.json: the controlled neuroanatomical vocabulary for `target.terms`.

The hierarchy is ours -- shallow, built from what the corpus actually stimulates -- and each
entry is annotated with its UBERON id (looked up once via the EBI OLS API) so the terms are
interoperable. The seed list below is the curated part; the script resolves ids, counts how
many full texts mention each term or synonym, and writes the JSON. Re-run after adding a term
(SCHEMA.md section 10). Network is needed only for ids; `--offline` keeps the ids already in the
existing file.

  uv run scripts/build_targets.py            # resolve ids, count mentions, write
  uv run scripts/build_targets.py --offline  # no OLS calls
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

import httpx

from common import DATA, TEXT, read_json

OUT = DATA.parent / "schema" / "targets.json"
OLS = "https://www.ebi.ac.uk/ols4/api/search"

# (id, parent, uberon_query_or_None, synonyms...)  -- the id doubles as the display label
# once underscores become spaces. `uberon_query` is the label OLS is asked for; None means no
# UBERON term is expected (our own grouping, or a clinical name like VIM).
SEED: list[tuple] = [
    # roots
    ("brain", None, "brain"),
    ("cerebral_cortex", "brain", "cerebral cortex", "cortex", "neocortex"),
    ("subcortical", "brain", None),
    ("spinal_cord", None, "spinal cord"),
    ("peripheral_nervous_system", None, "peripheral nervous system"),
    ("sensory_organ", None, None),
    ("tissue_preparation", None, None),
    ("invertebrate_nervous_system", None, None),
    ("whole_brain_or_unfocused", "brain", None, "whole brain", "global", "unfocused", "whole-brain"),
    ("hemisphere_unspecified", "cerebral_cortex", None),
    # frontal
    ("frontal_lobe", "cerebral_cortex", "frontal lobe", "frontal cortex"),
    ("motor_cortex", "frontal_lobe", "motor cortex", "motor area"),
    ("primary_motor_cortex", "motor_cortex", "primary motor cortex", "M1", "precentral gyrus", "hand knob", "hand motor area", "BA4", "area 4"),
    ("premotor_cortex", "motor_cortex", "premotor cortex", "PMC", "PMd", "dorsal premotor", "ventral premotor"),
    ("supplementary_motor_area", "motor_cortex", "supplementary motor cortex", "SMA", "supplementary motor area", "pre-SMA"),
    ("sensorimotor_cortex", "cerebral_cortex", "sensorimotor cortex", "sensorimotor", "SMC"),
    ("prefrontal_cortex", "frontal_lobe", "prefrontal cortex", "PFC", "prefrontal"),
    ("dorsolateral_prefrontal_cortex", "prefrontal_cortex", "dorsolateral prefrontal cortex", "dlPFC", "DLPFC"),
    ("dorsomedial_prefrontal_cortex", "prefrontal_cortex", "dorsomedial prefrontal cortex", "dmPFC"),
    ("ventromedial_prefrontal_cortex", "prefrontal_cortex", "ventromedial prefrontal cortex", "vmPFC", "medial prefrontal", "mPFC"),
    ("orbitofrontal_cortex", "prefrontal_cortex", "orbitofrontal cortex", "OFC", "orbital frontal"),
    ("anterior_prefrontal_cortex", "prefrontal_cortex", "frontal pole", "aPFC", "frontopolar", "area 10", "BA10"),
    ("inferior_frontal_gyrus", "frontal_lobe", "inferior frontal gyrus", "IFG", "inferior frontal cortex", "IFC", "area 47/12o", "ventrolateral prefrontal", "vlPFC"),
    ("frontal_eye_field", "frontal_lobe", "frontal eye field", "FEF"),
    # parietal
    ("parietal_lobe", "cerebral_cortex", "parietal lobe", "parietal cortex"),
    ("somatosensory_cortex", "parietal_lobe", "somatosensory cortex", "somatosensory"),
    ("primary_somatosensory_cortex", "somatosensory_cortex", "primary somatosensory cortex", "S1", "SI", "postcentral gyrus", "barrel cortex", "hindlimb S1", "forelimb S1", "BA3"),
    ("secondary_somatosensory_cortex", "somatosensory_cortex", "secondary somatosensory cortex", "S2", "SII"),
    ("posterior_parietal_cortex", "parietal_lobe", "posterior parietal cortex", "PPC", "intraparietal sulcus", "IPS", "inferior parietal", "superior parietal"),
    ("precuneus", "parietal_lobe", "precuneus"),
    # occipital
    ("occipital_lobe", "cerebral_cortex", "occipital lobe", "occipital cortex"),
    ("visual_cortex", "occipital_lobe", "visual cortex"),
    ("primary_visual_cortex", "visual_cortex", "primary visual cortex", "V1", "striate cortex", "BA17", "area 17", "calcarine"),
    ("extrastriate_visual_cortex", "visual_cortex", "extrastriate cortex", "V2", "V3", "V4", "V5", "MT", "area MT", "middle temporal visual"),
    # temporal
    ("temporal_lobe", "cerebral_cortex", "temporal lobe", "temporal cortex"),
    ("auditory_cortex", "temporal_lobe", "auditory cortex"),
    ("primary_auditory_cortex", "auditory_cortex", "primary auditory cortex", "A1", "Heschl"),
    ("superior_temporal_gyrus", "temporal_lobe", "superior temporal gyrus", "STG", "superior temporal sulcus", "STS"),
    ("middle_temporal_gyrus", "temporal_lobe", "middle temporal gyrus", "MTG"),
    ("inferior_temporal_cortex", "temporal_lobe", "inferior temporal cortex", "inferior temporal gyrus", "IT cortex", "inferotemporal"),
    ("temporal_pole", "temporal_lobe", "temporal pole"),
    ("entorhinal_cortex", "temporal_lobe", "entorhinal cortex", "entorhinal"),
    ("parahippocampal_cortex", "temporal_lobe", "parahippocampal gyrus", "parahippocampal", "perirhinal"),
    # cingulate / insula / other cortex
    ("cingulate_cortex", "cerebral_cortex", "cingulate cortex", "cingulate", "cingulate gyrus"),
    ("anterior_cingulate_cortex", "cingulate_cortex", "anterior cingulate cortex", "ACC", "anterior cingulate"),
    ("dorsal_anterior_cingulate_cortex", "anterior_cingulate_cortex", "dorsal anterior cingulate cortex", "dACC", "dorsal ACC", "BA24", "area 24"),
    ("subgenual_anterior_cingulate_cortex", "anterior_cingulate_cortex", "subgenual anterior cingulate cortex", "sgACC", "subgenual cingulate", "SCC", "subcallosal cingulate", "BA25", "area 25", "subgenual"),
    ("pregenual_anterior_cingulate_cortex", "anterior_cingulate_cortex", "pregenual anterior cingulate cortex", "pgACC", "pregenual", "rostral ACC", "rACC"),
    ("posterior_cingulate_cortex", "cingulate_cortex", "posterior cingulate cortex", "PCC", "posterior cingulate"),
    ("retrosplenial_cortex", "cingulate_cortex", "retrosplenial cortex", "retrosplenial", "RSC"),
    ("insular_cortex", "cerebral_cortex", "insular cortex", "insula", "insular"),
    ("anterior_insula", "insular_cortex", "anterior insula", "anterior insular"),
    ("posterior_insula", "insular_cortex", "posterior insula", "posterior insular", "mid-insula", "mid insula", "middle insula"),
    ("piriform_cortex", "cerebral_cortex", "piriform cortex", "piriform"),
    ("temporoparietal_junction", "cerebral_cortex", "temporoparietal junction", "TPJ"),
    # hippocampus / amygdala
    ("hippocampal_formation", "subcortical", "hippocampal formation"),
    ("hippocampus", "hippocampal_formation", "hippocampus", "hippocampal"),
    ("CA1", "hippocampus", "CA1 field of hippocampus", "CA1"),
    ("CA3", "hippocampus", "CA3 field of hippocampus", "CA3"),
    ("dentate_gyrus", "hippocampus", "dentate gyrus", "DG"),
    ("subiculum", "hippocampal_formation", "subiculum"),
    ("amygdala", "subcortical", "amygdala", "amygdalar"),
    ("basolateral_amygdala", "amygdala", "basolateral amygdaloid nuclear complex", "basolateral amygdala", "BLA"),
    ("central_amygdala", "amygdala", "central amygdaloid nucleus", "central amygdala", "CeA"),
    ("septal_nuclei", "subcortical", "septal nuclear complex", "septal nuclei", "medial septum", "lateral septum"),
    ("habenula", "subcortical", "habenula", "lateral habenula", "LHb"),
    # basal ganglia
    ("basal_ganglia", "subcortical", "basal ganglion", "basal ganglia"),
    ("striatum", "basal_ganglia", "striatum", "striatal"),
    ("caudate_nucleus", "striatum", "caudate nucleus", "caudate"),
    ("putamen", "striatum", "putamen"),
    ("dorsal_striatum", "striatum", "dorsal striatum", "dorsolateral striatum", "dorsomedial striatum"),
    ("nucleus_accumbens", "striatum", "nucleus accumbens", "NAc", "NAcc", "accumbens", "ventral striatum"),
    ("globus_pallidus", "basal_ganglia", "globus pallidus", "pallidum", "pallidal"),
    ("globus_pallidus_internus", "globus_pallidus", "internal globus pallidus", "GPi", "globus pallidus internus", "internal segment"),
    ("globus_pallidus_externus", "globus_pallidus", "external globus pallidus", "GPe", "globus pallidus externus"),
    ("subthalamic_nucleus", "basal_ganglia", "subthalamic nucleus", "STN"),
    ("substantia_nigra", "basal_ganglia", "substantia nigra", "SNc", "SNr", "pars compacta", "pars reticulata", "nigral"),
    ("zona_incerta", "subcortical", "zona incerta", "ZI", "posterior subthalamic area", "PSA", "caudal zona incerta", "cZI"),
    # thalamus
    ("thalamus", "subcortical", "thalamus", "thalamic"),
    ("ventral_intermediate_nucleus", "thalamus", None, "VIM", "Vim", "ventral intermediate nucleus", "ventralis intermedius", "ventral intermediate"),
    ("ventral_lateral_nucleus", "thalamus", "ventral lateral nucleus of thalamus", "VL", "ventrolateral thalamus", "ventral lateral thalamus", "VLp", "VLa", "VoP"),
    ("ventral_anterior_nucleus", "thalamus", "ventral anterior nucleus of thalamus", "VA", "ventral anterior"),
    ("ventral_posterolateral_nucleus", "thalamus", "ventral posterolateral nucleus", "VPL", "ventroposterolateral", "ventral posterolateral"),
    ("ventral_posteromedial_nucleus", "thalamus", "ventral posteromedial nucleus of thalamus", "VPM", "ventral posteromedial"),
    ("ventral_posterior_nucleus", "thalamus", "ventral posterior nucleus of thalamus", "VP", "ventrobasal", "VB", "sensory thalamus", "ventral posterior"),
    ("mediodorsal_nucleus", "thalamus", "medial dorsal nucleus of thalamus", "MD", "mediodorsal", "dorsomedial thalamus"),
    ("centromedian_nucleus", "thalamus", "centromedian nucleus", "CM", "centromedian", "centre median", "CM-Pf", "intralaminar"),
    ("anterior_thalamic_nucleus", "thalamus", "anterior nuclear group of thalamus", "anterior thalamus", "anterior thalamic", "ANT"),
    ("pulvinar", "thalamus", "pulvinar nucleus", "pulvinar"),
    ("lateral_geniculate_nucleus", "thalamus", "lateral geniculate body", "LGN", "lateral geniculate", "dLGN"),
    ("medial_geniculate_nucleus", "thalamus", "medial geniculate body", "MGN", "medial geniculate", "MGB"),
    ("reticular_thalamic_nucleus", "thalamus", "reticular nucleus of thalamus", "thalamic reticular", "TRN"),
    ("posterior_thalamic_nucleus", "thalamus", "posterior thalamic nuclear group", "posterior thalamus", "Po", "posterior nucleus"),
    # hypothalamus
    ("hypothalamus", "subcortical", "hypothalamus", "hypothalamic"),
    ("lateral_hypothalamus", "hypothalamus", "lateral hypothalamic area", "lateral hypothalamus", "LH"),
    ("paraventricular_nucleus", "hypothalamus", "paraventricular nucleus of hypothalamus", "paraventricular nucleus", "PVN"),
    ("arcuate_nucleus", "hypothalamus", "arcuate nucleus of hypothalamus", "arcuate nucleus", "ARC"),
    ("ventromedial_hypothalamus", "hypothalamus", "ventromedial nucleus of hypothalamus", "ventromedial hypothalamus", "VMH"),
    ("suprachiasmatic_nucleus", "hypothalamus", "suprachiasmatic nucleus", "SCN"),
    # basal forebrain (3 papers wrote it under other)
    ("basal_forebrain", "subcortical", "basal forebrain", "BF", "nucleus basalis", "nucleus basalis of Meynert", "medial septum", "diagonal band", "septal"),
    # brainstem
    ("brainstem", "brain", "brainstem", "brain stem"),
    ("midbrain", "brainstem", "midbrain", "mesencephalon"),
    ("superior_colliculus", "midbrain", "superior colliculus", "SC"),
    ("inferior_colliculus", "midbrain", "inferior colliculus", "IC"),
    ("periaqueductal_gray", "midbrain", "periaqueductal gray", "PAG", "periaqueductal grey"),
    ("ventral_tegmental_area", "midbrain", "ventral tegmental area", "VTA"),
    ("red_nucleus", "midbrain", "red nucleus"),
    ("pretectal_area", "midbrain", "pretectal region", "pretectal", "pretectum"),
    ("pons", "brainstem", "pons", "pontine"),
    ("locus_coeruleus", "pons", "locus ceruleus", "locus coeruleus", "LC"),
    ("pedunculopontine_nucleus", "pons", "pedunculopontine tegmental nucleus", "pedunculopontine", "PPN", "PPTg"),
    ("parabrachial_nucleus", "pons", "parabrachial nucleus", "parabrachial"),
    ("raphe_nuclei", "brainstem", "raphe nuclei", "dorsal raphe", "DRN", "raphe"),
    ("medulla", "brainstem", "medulla oblongata", "medulla", "medullary"),
    ("nucleus_tractus_solitarius", "medulla", "nucleus of solitary tract", "nucleus tractus solitarius", "NTS", "solitary nucleus"),
    ("dorsal_motor_nucleus_of_vagus", "medulla", "dorsal motor nucleus of vagus nerve", "dorsal motor nucleus", "DMV", "DMNV"),
    ("rostral_ventrolateral_medulla", "medulla", "ventrolateral medulla", "RVLM", "rostral ventrolateral medulla"),
    ("reticular_formation", "brainstem", "reticular formation", "reticular activating"),
    # cerebellum
    ("cerebellum", "brain", "cerebellum", "cerebellar"),
    ("cerebellar_cortex", "cerebellum", "cerebellar cortex", "cerebellar hemisphere", "vermis", "lobule", "Crus"),
    ("deep_cerebellar_nuclei", "cerebellum", "cerebellar nuclear complex", "deep cerebellar nuclei", "dentate nucleus", "fastigial", "interposed"),
    # white matter
    ("white_matter", "brain", "white matter of brain", "white matter", "tract", "fiber bundle"),
    ("corpus_callosum", "white_matter", "corpus callosum", "callosal"),
    ("internal_capsule", "white_matter", "internal capsule", "anterior limb of the internal capsule", "ALIC", "capsulotomy target"),
    ("corticospinal_tract", "white_matter", "corticospinal tract", "CST", "pyramidal tract"),
    ("dentatorubrothalamic_tract", "white_matter", "dentatorubrothalamic tract", "DRT", "DRTT", "dentato-rubro-thalamic", "cerebellothalamic"),
    ("cingulum_bundle", "white_matter", "cingulum of brain", "cingulum", "cingulum bundle"),
    ("medial_forebrain_bundle", "white_matter", "medial forebrain bundle", "MFB", "slMFB"),
    # spinal
    ("spinal_dorsal_horn", "spinal_cord", "dorsal horn of spinal cord", "dorsal horn"),
    ("cervical_spinal_cord", "spinal_cord", "cervical spinal cord", "cervical cord"),
    ("thoracic_spinal_cord", "spinal_cord", "thoracic spinal cord", "thoracic cord"),
    ("lumbar_spinal_cord", "spinal_cord", "lumbar spinal cord", "lumbar cord", "lumbosacral"),
    # peripheral
    ("peripheral_nerve", "peripheral_nervous_system", "nerve", "peripheral nerve"),
    ("vagus_nerve", "peripheral_nerve", "vagus nerve", "vagus", "vagal", "cervical vagus", "auricular branch", "subdiaphragmatic vagus"),
    ("sciatic_nerve", "peripheral_nerve", "sciatic nerve", "sciatic"),
    ("median_nerve", "peripheral_nerve", "median nerve"),
    ("ulnar_nerve", "peripheral_nerve", "ulnar nerve"),
    ("radial_nerve", "peripheral_nerve", "radial nerve"),
    ("tibial_nerve", "peripheral_nerve", "tibial nerve", "posterior tibial"),
    ("peroneal_nerve", "peripheral_nerve", "common fibular nerve", "peroneal nerve", "common peroneal", "fibular nerve", "CPN"),
    ("femoral_nerve", "peripheral_nerve", "femoral nerve"),
    ("phrenic_nerve", "peripheral_nerve", "phrenic nerve", "phrenic"),
    ("trigeminal_nerve", "peripheral_nerve", "trigeminal nerve", "trigeminal", "infraorbital nerve"),
    ("facial_nerve", "peripheral_nerve", "facial nerve"),
    ("optic_nerve", "peripheral_nerve", "optic nerve"),
    ("auditory_nerve", "peripheral_nerve", "cochlear nerve", "auditory nerve", "cochlear nerve", "vestibulocochlear"),
    ("splanchnic_nerve", "peripheral_nerve", "splanchnic nerve", "splanchnic"),
    ("saphenous_nerve", "peripheral_nerve", "saphenous nerve"),
    ("digital_nerve", "peripheral_nerve", "digital nerve", "finger nerve"),
    ("ganglion", "peripheral_nervous_system", "ganglion", "ganglia"),
    ("dorsal_root_ganglion", "ganglion", "dorsal root ganglion", "DRG"),
    ("trigeminal_ganglion", "ganglion", "trigeminal ganglion", "Gasserian"),
    ("superior_cervical_ganglion", "ganglion", "superior cervical ganglion", "SCG", "sympathetic ganglion", "stellate ganglion"),
    ("nodose_ganglion", "ganglion", "nodose ganglion", "inferior ganglion of vagus"),
    ("nerve_plexus", "peripheral_nervous_system", "nerve plexus", "plexus", "brachial plexus", "lumbar plexus"),
    ("enteric_nervous_system", "peripheral_nervous_system", "enteric nervous system", "myenteric", "enteric neurons", "enteric"),
    ("neuromuscular_junction", "peripheral_nervous_system", "neuromuscular junction", "NMJ", "motor endplate", "motor axon terminal"),
    ("nerve_injury_site", "peripheral_nerve", None, "crush site", "transection site", "lesion site"),
    # sensory organs
    ("retina", "sensory_organ", "retina", "retinal", "retinal ganglion", "photoreceptor"),
    ("cochlea", "sensory_organ", "cochlea", "cochlear", "hair cell", "organ of Corti"),
    ("vestibular_organ", "sensory_organ", "vestibular system", "vestibular", "semicircular canal", "otolith"),
    ("olfactory_epithelium", "sensory_organ", "olfactory epithelium", "olfactory"),
    # preparations
    ("cultured_neurons", "tissue_preparation", None, "cultured neurons", "primary neurons", "primary culture", "neuronal culture", "cortical neurons in culture", "hippocampal neurons in culture", "dissociated neurons", "iPSC-derived neurons", "neuronal cell line", "SH-SY5Y", "PC12", "Neuro-2a", "N2a", "HT22"),
    ("cultured_glia", "tissue_preparation", None, "astrocytes", "astrocyte culture", "microglia", "BV2", "oligodendrocyte precursor", "OPC", "Schwann cell"),
    ("brain_slice", "tissue_preparation", None, "brain slice", "acute slice", "hippocampal slice", "cortical slice", "organotypic", "slice culture"),
    ("retinal_explant", "tissue_preparation", None, "retinal explant", "isolated retina", "ex vivo retina"),
    ("excised_nerve", "tissue_preparation", None, "excised nerve", "isolated nerve", "ex vivo nerve", "nerve preparation", "nerve bundle"),
    ("brain_organoid", "tissue_preparation", None, "organoid", "cerebral organoid", "brain organoid", "spheroid"),
    ("dorsal_root_ganglion_culture", "tissue_preparation", None, "DRG neurons", "cultured DRG", "DRG culture"),
    # invertebrate
    ("c_elegans_neurons", "invertebrate_nervous_system", None, "C. elegans", "Caenorhabditis", "ASH neuron", "AWC", "PVD", "mechanosensory neurons"),
    ("drosophila_neurons", "invertebrate_nervous_system", None, "Drosophila", "fly brain", "larval"),
    ("aplysia_neurons", "invertebrate_nervous_system", None, "Aplysia", "abdominal ganglion", "buccal ganglion"),
    ("leech_neurons", "invertebrate_nervous_system", None, "leech", "Hirudo"),
    ("earthworm_neurons", "invertebrate_nervous_system", None, "earthworm", "Lumbricus", "ventral nerve cord", "giant fiber", "giant fibre", "giant axon", "MGF", "LGF"),
    ("crayfish_neurons", "invertebrate_nervous_system", None, "crayfish", "crab", "lobster", "stomatogastric"),
    # escape
    ("other", None, None),
]


def ols_lookup(query: str, client: httpx.Client) -> str | None:
    """Return the UBERON obo_id whose label equals the query (case-insensitive), else the
    first UBERON hit, else None. Never lets an HTTP error look like 'no term'."""
    r = client.get(OLS, params={"q": query, "ontology": "uberon", "rows": 10, "exact": "false"})
    r.raise_for_status()
    docs = [d for d in r.json()["response"]["docs"] if str(d.get("obo_id", "")).startswith("UBERON:")]
    for d in docs:
        if d.get("label", "").lower() == query.lower():
            return d["obo_id"]
        for syn in d.get("synonym", []) or []:
            if syn.lower() == query.lower():
                return d["obo_id"]
    return docs[0]["obo_id"] if docs else None


def mention_count(patterns: list[str], texts: dict[str, str]) -> int:
    rx = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(p) for p in patterns) + r")(?![a-z])", re.I)
    return sum(1 for t in texts.values() if rx.search(t))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    existing = {e["id"]: e for e in (read_json(OUT, {}) or {}).get("terms", [])}
    texts = {p.name: p.read_text(errors="ignore") for p in TEXT.glob("*.md")}
    print(f"{len(texts)} texts", file=sys.stderr)

    terms = []
    with httpx.Client(timeout=30, headers={"User-Agent": "tus-paper-archive (mailto:brad@neuroharmonics.com)"}) as client:
        for entry in SEED:
            tid, parent, uq, *syns = entry
            label = tid.replace("_", " ")
            uberon = existing.get(tid, {}).get("uberon")
            if not args.offline and uq and not uberon:
                try:
                    uberon = ols_lookup(uq, client)
                except httpx.HTTPError as e:
                    print(f"  OLS error for {tid}: {e}", file=sys.stderr)
                    uberon = existing.get(tid, {}).get("uberon")
                time.sleep(0.15)
            patterns = [label] + list(syns) + ([uq] if uq else [])
            patterns = [p for p in patterns if len(p) > 1]
            n = mention_count(patterns, texts) if tid != "other" else 0
            terms.append({"id": tid, "label": label, "parent": parent, "uberon": uberon,
                          "synonyms": list(syns), "mentioned_in_texts": n})

    ids = {t["id"] for t in terms}
    bad = [t["id"] for t in terms if t["parent"] and t["parent"] not in ids]
    if bad:
        print(f"parents not defined: {bad}", file=sys.stderr)
        return 1

    OUT.write_text(json.dumps({
        "note": "Controlled vocabulary for target.terms (SCHEMA.md section 4.1). Our hierarchy, "
                "annotated with UBERON ids. Extend per SCHEMA.md section 10; rebuild with "
                "scripts/build_targets.py. mentioned_in_texts is a regex count over data/text and "
                "is only a guide to what the corpus talks about, not a target count.",
        "count": len(terms),
        "terms": terms,
    }, indent=1, ensure_ascii=False) + "\n")
    unresolved = [t["id"] for t in terms if t["uberon"] is None and t["parent"] and t["id"] != "other"]
    print(f"wrote {OUT} with {len(terms)} terms; {len(unresolved)} without a UBERON id: {unresolved}", file=sys.stderr)
    zero = [t["id"] for t in terms if t["mentioned_in_texts"] == 0 and t["id"] != "other"]
    print(f"{len(zero)} terms never mentioned in the corpus: {zero}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
