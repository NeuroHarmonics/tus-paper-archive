#!/usr/bin/env python3
"""Validate extracted records against the schema, the three-way rule, and the quote rule.

Exits non-zero if any record fails. Runs on every wave and inside build_site.py.

Checks, per record:
  1. JSON Schema conformance (schema/paper.schema.json), with target.terms validated against
     schema/targets.json.
  2. Three-way discipline: null only where SCHEMA.md says "does not apply".
  3. citekey is in the manifest, doi matches.
  4. Every numeric value inside exposures has a source quote, and the quote occurs in the
     paper's text (data/text/<stem>.md) after normalisation. MinerU emits LaTeX for inline
     maths, so the comparison is on letters and digits only.

  uv run scripts/validate.py                       # all of data/papers
  uv run scripts/validate.py data/papers/2022zeng.json
  uv run scripts/validate.py --strip-unquoted      # replace unquoted numbers with not_reported
                                                   #   and flag them, then re-validate

--strip-unquoted is how check_records.py enforces "no quote, no number" without a human.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import jsonschema

from common import DATA, TEXT, read_json

ROOT = DATA.parent
SCHEMA = ROOT / "schema" / "paper.schema.json"
TARGETS = ROOT / "schema" / "targets.json"
PAPERS = DATA / "papers"

NUMERIC_LEAVES = [
    "fundamental_frequency_khz",
    "free_field.pressure_kpa", "free_field.isppa_w_cm2", "free_field.ispta_w_cm2",
    "in_situ.pressure_kpa", "in_situ.isppa_w_cm2", "in_situ.ispta_w_cm2",
    "unspecified_domain.pressure_kpa", "unspecified_domain.isppa_w_cm2", "unspecified_domain.ispta_w_cm2",
    "timing.pulse_duration_ms", "timing.pulse_repetition_frequency_hz", "timing.duty_cycle_pct",
    "timing.sonication_duration_s",
]
PAPER_NUMERIC = ["n_subjects", "n_sessions_per_subject"]
TISSUE_ONLY = {"ex_vivo_tissue", "in_vitro_cell"}
HUMAN = {"human_healthy", "human_patient"}


def load_schema() -> dict:
    schema = json.loads(SCHEMA.read_text())
    ids = [t["id"] for t in json.loads(TARGETS.read_text())["terms"]]
    schema["$defs"]["exposure"]["properties"]["target"]["properties"]["terms"]["items"] = {"enum": ids}
    return schema


_LATEX = re.compile(r"\\(mathrm|mathbf|text|textit|mathit|operatorname|mu|pm|times|cdot|circ|sim|approx|le|ge|leq|geq|,|;|!| )")
_TAGS = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>\n]{0,120})?/?>")  # well-formed tags only; a bare "p < 0.05" is not a tag
_ELLIPSIS = re.compile(r"\[\s*(\.\.\.|…)\s*\]|\.\.\.|…")


def alnum(s: str) -> str:
    """Letters and digits only, after stripping HTML tags (MinerU tables) and LaTeX
    commands (MinerU inline maths), so `$\\mathrm{n}=10$` and `<td>10</td>` both compare as
    the text a reader sees."""
    s = _TAGS.sub(" ", s)
    s = _LATEX.sub(" ", s)
    return re.sub(r"[^a-z0-9]", "", s.lower())


def quote_in_text(quote: str, text_alnum: str) -> bool:
    """A quote counts as found if every ellipsis-separated piece occurs in the text, or, for a
    single piece, two of its three thirds do (one OCR hiccup tolerated)."""
    pieces = [alnum(p) for p in _ELLIPSIS.split(quote) if p and p.strip()]
    pieces = [p for p in pieces if p]
    if not pieces:
        return False
    if len(pieces) > 1:
        return all(len(p) >= 8 and p in text_alnum for p in pieces)
    q = pieces[0]
    if len(q) < 12:
        return False
    if q in text_alnum:
        return True
    n = len(q) // 3
    chunks = [q[:n], q[n:2 * n], q[2 * n:]]
    return sum(1 for c in chunks if len(c) >= 10 and c in text_alnum) >= 2


def get_path(obj, path: str):
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def set_path(obj, path: str, value):
    parts = path.split(".")
    for part in parts[:-1]:
        obj = obj[part]
    obj[parts[-1]] = value


def is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) or (
        isinstance(v, list) and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v))


def text_for(citekey: str, manifest: dict) -> str | None:
    entry = manifest.get(citekey)
    if not entry:
        return None
    pdf = (entry.get("files") or {}).get("pdf")
    if not pdf:
        return None
    p = TEXT / (pathlib.Path(pdf).stem + ".md")
    return p.read_text(errors="ignore") if p.exists() else None


def check(record: dict, validator, manifest: dict, strip: bool) -> tuple[list[str], bool]:
    """Return (problems, changed)."""
    problems: list[str] = []
    changed = False
    ck = record.get("citekey", "?")

    # tolerate the American spelling; the vocabulary is British
    if record.get("anaesthesia") in ("anesthetised", "anesthetized", "anaesthetized"):
        record["anaesthesia"] = "anaesthetised"
        changed = True

    for err in sorted(validator.iter_errors(record), key=lambda e: list(e.path)):
        path = "/".join(str(p) for p in err.path) or "<root>"
        problems.append(f"schema: {path}: {err.message[:160]}")
    if problems:
        return problems, changed  # structure is wrong; the rest would be noise

    # 3. identity
    man = manifest.get(ck)
    if man is None:
        problems.append("identity: citekey not in manifest")
    elif (man.get("doi") or "").lower() != (record.get("doi") or "").lower():
        problems.append(f"identity: doi {record.get('doi')} != manifest {man.get('doi')}")

    # 2. three-way discipline
    ms = set(record["model_system"])
    tissue = ms <= TISSUE_ONLY          # the whole study is tissue or cell work
    tissue_any = bool(ms & TISSUE_ONLY)  # some exposures may be a bath or dish
    for f in ("randomised", "blinding", "adverse_events"):
        if record[f] is None and not tissue:
            problems.append(f"three-way: {f} is null but the study is in vivo")
    if tissue and record.get("n_sessions_per_subject") == "not_reported":
        if strip:
            record["n_sessions_per_subject"] = None
            changed = True
        else:
            problems.append("three-way: n_sessions_per_subject is not_reported for tissue or cell work; it does not apply")
    if record["anaesthesia"] is None and not (tissue or ms & HUMAN):
        problems.append("three-way: anaesthesia is null for an animal study")
    if record["anaesthesia"] not in (None, "not_reported") and ms <= HUMAN:
        problems.append("three-way: anaesthesia set for a human-only study")
    for i, ex in enumerate(record["exposures"]):
        in_situ_leaves = ("method", "reported_as", "pressure_kpa", "isppa_w_cm2", "ispta_w_cm2")
        numerics_null = all(ex["in_situ"][l] is None for l in ("pressure_kpa", "isppa_w_cm2", "ispta_w_cm2"))
        if numerics_null and strip:
            # the block does not apply to this exposure (a bath or dish): the whole block is null
            for l in ("method", "reported_as"):
                if ex["in_situ"][l] is not None:
                    ex["in_situ"][l] = None
                    changed = True
        nulls = [l for l in in_situ_leaves if ex["in_situ"][l] is None]
        if 0 < len(nulls) < len(in_situ_leaves) and strip:
            # a block holding a number applies, so a lone null is a silence; a block with no number does not apply
            has_number = any(is_number(ex["in_situ"][l]) for l in ("pressure_kpa", "isppa_w_cm2", "ispta_w_cm2"))
            for l in in_situ_leaves:
                if has_number and ex["in_situ"][l] is None:
                    ex["in_situ"][l] = "not_reported"
                elif not has_number:
                    ex["in_situ"][l] = None
            changed = True
            nulls = [l for l in in_situ_leaves if ex["in_situ"][l] is None]
        if 0 < len(nulls) < len(in_situ_leaves):
            problems.append(f"three-way: exposures[{i}].in_situ is partly null ({', '.join(nulls)}); the block applies or it does not")
        has_number = any(is_number(ex["in_situ"][l]) for l in ("pressure_kpa", "isppa_w_cm2", "ispta_w_cm2"))
        for leaf in in_situ_leaves:
            if tissue and not has_number and ex["in_situ"][leaf] == "not_reported":
                # a bath or dish has no tissue path, so the field does not apply
                if strip:
                    ex["in_situ"][leaf] = None
                    changed = True
                else:
                    problems.append(f"three-way: exposures[{i}].in_situ.{leaf} is not_reported for tissue or cell work; it does not apply")
        for leaf in ("pressure_kpa", "isppa_w_cm2", "ispta_w_cm2"):
            if ex["unspecified_domain"][leaf] == "not_reported":
                # unspecified_domain holds only values the paper gave without a domain; it is never "silent"
                if strip:
                    ex["unspecified_domain"][leaf] = None
                    changed = True
                else:
                    problems.append(f"three-way: exposures[{i}].unspecified_domain.{leaf} is not_reported; it is null unless a value is placed there")
        if ex["timing"]["waveform"] == "continuous":
            for leaf in ("pulse_duration_ms", "pulse_repetition_frequency_hz", "duty_cycle_pct"):
                if ex["timing"][leaf] not in (None, 100) and not (leaf == "duty_cycle_pct" and ex["timing"][leaf] == 100):
                    if ex["timing"][leaf] != "not_reported":
                        problems.append(f"three-way: exposures[{i}].timing.{leaf} should be null for continuous wave")

    # 4. quotes
    quotes = record["provenance"]["source_quotes"]
    text = text_for(ck, manifest)
    text_alnum = alnum(text) if text else None
    if text is None:
        problems.append("quotes: no text file for this citekey; quotes cannot be checked")
    for f in PAPER_NUMERIC:
        v = record[f]
        if is_number(v) and f not in quotes:
            problems.append(f"quotes: {f} has a value but no source quote")
    for i, ex in enumerate(record["exposures"]):
        for leaf in NUMERIC_LEAVES:
            v = get_path(ex, leaf)
            if not is_number(v):
                continue
            key = f"exposures[{i}].{leaf}"
            q = quotes.get(key)
            if not q:
                # the same value in another exposure may carry the sentence (one sentence often
                # describes every arm); inherit it and record the inheritance
                for j, other in enumerate(record["exposures"]):
                    if j != i and get_path(other, leaf) == v and quotes.get(f"exposures[{j}].{leaf}"):
                        q = quotes[f"exposures[{j}].{leaf}"]
                        quotes[key] = q
                        changed = True
                        break
            ok = bool(q) and (text_alnum is None or quote_in_text(q, text_alnum))
            if not ok:
                why = "no source quote" if not q else "quote not found in text"
                if strip:
                    set_path(ex, leaf, "not_reported")
                    record["provenance"]["flags"].append({"field": key, "reason": f"value removed at validation: {why}"})
                    quotes.pop(key, None)
                    changed = True
                else:
                    problems.append(f"quotes: {key} = {v}: {why}")
    # a strip flag from an earlier pass is stale once the field carries a verified value again
    flags = record["provenance"]["flags"]
    keep = []
    for fl in flags:
        if fl.get("reason", "").startswith("value removed at validation"):
            path = fl.get("field", "")
            m = re.match(r"exposures\[(\d+)\]\.(.+)", path)
            if m:
                i, leaf = int(m.group(1)), m.group(2)
                if i < len(record["exposures"]):
                    v = get_path(record["exposures"][i], leaf)
                    q = quotes.get(path)
                    if is_number(v) and q and (text_alnum is None or quote_in_text(q, text_alnum)):
                        changed = True
                        continue
        keep.append(fl)
    flags[:] = keep
    return problems, changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--strip-unquoted", action="store_true", help="remove numbers whose quote is not in the text, and set fields that cannot apply to null")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    validator = jsonschema.Draft202012Validator(load_schema())
    manifest = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})
    files = [pathlib.Path(f) for f in args.files] or sorted(PAPERS.glob("*.json"))

    bad = 0
    for f in files:
        try:
            rec = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            print(f"FAIL {f.name}: not JSON: {e}")
            bad += 1
            continue
        problems, changed = check(rec, validator, manifest, args.strip_unquoted)
        if changed:
            f.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
            problems2, _ = check(rec, validator, manifest, False)
            problems = problems2
        if problems:
            bad += 1
            print(f"FAIL {f.name}")
            for p in problems:
                print(f"   - {p}")
        elif not args.quiet:
            print(f"ok   {f.name}")
    print(f"\n{len(files) - bad}/{len(files)} records valid")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
