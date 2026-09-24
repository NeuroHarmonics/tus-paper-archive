#!/usr/bin/env python3
"""Give every stated device a source sentence.

The extractor was told to quote numbers, not device strings, so the paper page showed the maker
and model with no "source" link (review of 2026-09-22, on the Blatek transducers). This finds the
sentence in the paper's text that names the manufacturer, or failing that the model, and stores
it under `exposures[i].device.manufacturer` (or `.model`) in `provenance.source_quotes`. The
sentence is lifted verbatim from the text, so `derive.py` marks it quote-verified.

Search key: the first word of the manufacturer string with four or more letters that is not a
generic word (Inc, Ltd, Co, Corporation, Industries, Instruments ...), then the model string's
first token with a digit in it. Nothing is written when neither is found; the field stays as it
was, unsourced. Idempotent: existing quotes are never replaced.

  uv run scripts/backfill_device_quotes.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from common import DATA
from validate import text_for

GENERIC = {"inc", "ltd", "llc", "corp", "corporation", "company", "co", "industries", "industry", "instruments",
           "instrument", "technology", "technologies", "medical", "systems", "system", "group", "gmbh", "s.a.",
           "the", "and", "custom", "custom-built", "transducer", "transducers", "element", "not_reported", "usa",
           "china", "korea", "japan", "france", "germany", "canada", "uk", "united", "states", "kingdom",
           "university", "institute", "institutes", "laboratory", "lab", "department", "dept", "research",
           "healthcare", "electronic", "electronics", "science", "sciences", "ultrasonic", "ultrasonics", "piezo",
           "ceramic", "ceramics", "sold", "supplied", "driving", "drive", "driver", "imaging", "array", "sonic"}
TAGS = re.compile(r"<[^>]+>")
MAX_LEN = 320
WS = re.compile(r"[ \t]+")


def keys_for(manufacturer: str) -> list[str]:
    out = []
    for w in re.split(r"[\s,;/()\[\].]+", manufacturer):
        w = w.strip("-–")
        if len(w) >= 4 and w.lower() not in GENERIC and not w.isdigit():
            out.append(w)
    # "Sonic Concepts" -> "Concepts"; "Sonic" alone matches "sonication"
    return out


def model_keys(model: str) -> list[str]:
    return [t for t in re.split(r"[\s,;/()\[\]]+", model) if any(c.isdigit() for c in t) and len(t) >= 3][:2]


def sentence_around(text: str, key: str) -> str | None:
    m = re.search(re.escape(key), text, re.I)
    if not m:
        return None
    # sentence bounds: previous ". " or newline, next ". " or newline; capped so a table row stays short
    start = max(text.rfind("\n", 0, m.start()), text.rfind(". ", 0, m.start()) + 1)
    start = max(start, m.start() - 350)
    end_candidates = [i for i in (text.find(". ", m.end()), text.find("\n", m.end())) if i != -1]
    end = min(end_candidates) + 1 if end_candidates else min(len(text), m.end() + 350)
    end = min(end, m.end() + 350)
    s = WS.sub(" ", text[start:end]).strip()
    s = s.lstrip(".;:| ").strip()
    if len(s) > MAX_LEN:  # a flattened table row: keep a window round the key, cut at word boundaries
        k = s.lower().find(key.lower())
        lo, hi = max(0, k - MAX_LEN // 2), min(len(s), k + len(key) + MAX_LEN // 2)
        if lo > 0:
            lo = s.find(" ", lo) + 1
        if hi < len(s):
            hi = s.rfind(" ", 0, hi)
        s = s[lo:hi].strip()
    return s if len(s) >= 12 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    manifest = json.loads((DATA / "manifest.json").read_text())["papers"]
    added = 0
    missing: list[str] = []
    no_text = 0
    changed = 0
    for p in sorted((DATA / "papers").glob("*.json")):
        r = json.loads(p.read_text())
        quotes = r["provenance"]["source_quotes"]
        raw = text_for(r["citekey"], manifest)
        if raw is None:
            no_text += 1
            continue
        text = TAGS.sub(" ", raw)
        before = json.dumps(quotes, sort_keys=True)
        for i, ex in enumerate(r["exposures"]):
            d = ex["device"]
            km, kmo = f"exposures[{i}].device.manufacturer", f"exposures[{i}].device.model"
            if len(quotes.get(km, "")) > MAX_LEN:
                quotes.pop(km)
            if len(quotes.get(kmo, "")) > MAX_LEN:
                quotes.pop(kmo)
            if isinstance(d["manufacturer"], str) and d["manufacturer"] != "not_reported" and km not in quotes:
                for k in keys_for(d["manufacturer"]) + (model_keys(d["model"]) if isinstance(d["model"], str) else []):
                    s = sentence_around(text, k)
                    if s:
                        quotes[km] = s
                        added += 1
                        break
                else:
                    missing.append(f"{r['citekey']} [{i}] {d['manufacturer']} | {d['model']}")
            if isinstance(d["model"], str) and d["model"] != "not_reported" and kmo not in quotes:
                if km in quotes and any(k.lower() in quotes[km].lower() for k in model_keys(d["model"])):
                    quotes[kmo] = quotes[km]
                else:
                    for k in model_keys(d["model"]):
                        s = sentence_around(text, k)
                        if s:
                            quotes[kmo] = s
                            added += 1
                            break
        if json.dumps(quotes, sort_keys=True) != before:
            changed += 1
            if not a.dry_run:
                p.write_text(json.dumps(r, indent=1, ensure_ascii=False) + "\n")
    print(f"added {added} device quotes in {changed} records; {no_text} records without text")
    print(f"{len(missing)} stated manufacturers not found in the text:")
    print("\n".join("  " + m for m in missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
