#!/usr/bin/env python3
"""Regression tests for citekey assignment, and the stability check on the real manifest.

A released citekey is a public identifier: a site URL and anyone's citation of a record. These
cases are the ways a key has actually moved, or could, with the real papers named.

  uv run scripts/test_citekeys.py
"""

from __future__ import annotations

import sys

from common import DATA, assign_citekeys, read_json, released_manifest

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    if not ok:
        FAILURES.append(f"{label}\n      got {got!r}\n      want {want!r}")
    print(f"  {'pass' if ok else 'FAIL'}  {label}")


def test_suffix_crossing_into_another_surname() -> None:
    """October 2026: the eighth 2026 Chen paper took suffix g, which is 2026cheng, the key of the
    Cheng paper released in v2026.09.0. The working manifest then held the Chen paper at 2026cheng
    and Cheng at 2026chengb; the release has to win that back."""
    print("\nassign_citekeys -- chen + g is not cheng")
    chen = [f"10.chen/{c}" for c in "abcdefh"] + ["10.3390/ijms27198498"]
    cheng = ["10.3389/fnhum.2026.1884060", "10.3389/fpsyt.2026.1748927"]
    released = {"10.3389/fnhum.2026.1884060": "2026cheng"}
    working = {f"10.chen/{c}": f"2026chen{c}" for c in "abcdefh"}
    working |= {"10.3390/ijms27198498": "2026cheng", "10.3389/fnhum.2026.1884060": "2026chengb",
                "10.3389/fpsyt.2026.1748927": "2026chenga"}
    got = assign_citekeys({"2026chen": chen, "2026cheng": cheng}, released, working)
    check("released Cheng keeps 2026cheng", got["10.3389/fnhum.2026.1884060"], "2026cheng")
    check("Chen newcomer skips g, takes next free", got["10.3390/ijms27198498"], "2026cheni")
    check("second Cheng keeps its working key", got["10.3389/fpsyt.2026.1748927"], "2026chenga")

    got = assign_citekeys({"2026chen": chen, "2026cheng": cheng}, {}, {})
    check("fresh run: no Chen key equals the Cheng base",
          "2026cheng" in {got[d] for d in chen}, False)


def test_li_never_reaches_liu() -> None:
    print("\nassign_citekeys -- a large Li group skips Liu")
    li = [f"10.li/{i:02d}" for i in range(22)]
    got = assign_citekeys({"2024li": li, "2024liu": ["10.liu/1"]}, {}, {})
    check("Liu keeps its bare key", got["10.liu/1"], "2024liu")
    check("22nd Li paper skips u", got["10.li/21"], "2024liw")
    check("keys unique", len(set(got.values())), len(got))


def test_released_crossing_key_stays() -> None:
    """If a Chen paper was released at 2026cheng before any Cheng paper existed, the later Cheng
    paper takes a suffix; the released key does not move."""
    print("\nassign_citekeys -- a released key that spells another base is kept")
    got = assign_citekeys({"2026chen": ["10.chen/g"], "2026cheng": ["10.cheng/new"]},
                          {"10.chen/g": "2026cheng"}, {"10.chen/g": "2026cheng"})
    check("released Chen keeps 2026cheng", got["10.chen/g"], "2026cheng")
    check("Cheng newcomer takes a suffix", got["10.cheng/new"], "2026chenga")


def test_retired_key_not_reused() -> None:
    """2026legonb shipped in v2026.09.0 and is excluded in October; its key is retired."""
    print("\nassign_citekeys -- a retired released key is never reissued")
    released = {"10.legon/a": "2026legona", "10.legon/b": "2026legonb", "10.legon/c": "2026legonc"}
    got = assign_citekeys({"2026legon": ["10.legon/a", "10.legon/c", "10.legon/new"]},
                          released, released)
    check("newcomer skips the retired b", got["10.legon/new"], "2026legond")
    got = assign_citekeys({"2025yang": ["10.48550/arxiv.2511.14188"]},
                          {"10.48550/arxiv.2511.14188": "2025anon", "10.other": "2025yang"}, {})
    check("a lone newcomer does not take a retired bare key", got["10.48550/arxiv.2511.14188"],
          "2025yanga")


def test_released_keys_still_name_their_paper() -> None:
    """Every key in the previous release that is still in the manifest names the same DOI.
    A key may disappear (exclusion, preprint merge); it may never point at another paper."""
    print("\nmanifest -- released keys are stable")
    released = released_manifest()
    current = (read_json(DATA / "manifest.json", {}) or {}).get("papers", {})
    if not released:
        print("  skip  no release tag")
        return
    moved = sorted(f"{ck}: {p['doi']} -> {current[ck]['doi']}" for ck, p in released.items()
                   if ck in current and current[ck]["doi"].lower() != p["doi"].lower())
    check(f"{len(released)} released keys, none moved", moved, [])


if __name__ == "__main__":
    test_suffix_crossing_into_another_surname()
    test_li_never_reaches_liu()
    test_released_crossing_key_stays()
    test_retired_key_not_reused()
    test_released_keys_still_name_their_paper()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s):")
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)
    print("\nall pass")
