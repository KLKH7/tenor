"""Dedupe, shuffle and closest-passage logic of scripts/writer_check_build.py (no data files)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "writer_check_build", ROOT / "scripts/writer_check_build.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["writer_check_build"] = mod
    spec.loader.exec_module(mod)
    return mod


W = _load()


def _s(unit: str, n: int, kind: str, sentence: str, bullets: list[str]) -> dict[str, Any]:
    return {
        "unit": unit,
        "n": n,
        "kind": kind,
        "sentence": sentence,
        "x_bullets": bullets,
        "y_lines": ["L0001"] if kind == "changed" else [],
    }


BIN = "Sam was going to ask the landlord about the bin store; it did not come up today."
BIN2 = "Sam was going to ask the landlord about the bin store; it never came up today."
MENU = "Last time the menu had four dishes; today's printed menu has three."
MENU2 = "Last time the menu had four dishes; the menu never came up today."
CASE = "Last time the case was double-curved; today it was costed single-curved."
CASE2 = "The case was to be double-curved; a flat rubber shell was chosen and the curve dropped."

V1 = [
    _s("A__B", 1, "not_mentioned", BIN, ["B2"]),
    _s("A__B", 2, "changed", MENU, ["B4"]),
    _s("C__D", 1, "changed", CASE, ["B6"]),
]
V2 = [
    _s("A__B", 1, "not_mentioned", BIN2, ["B2"]),  # near-identical to V1[0]: merged
    _s("A__B", 2, "not_mentioned", MENU2, ["B4"]),  # same item, other kind of claim: apart
    _s("C__D", 1, "changed", CASE2, ["B6"]),  # same item and kind, other claim: apart
    _s("C__D", 2, "changed", CASE, ["B7"]),  # same words, other bullet: apart
]


def test_dedupe_merges_only_near_identical_claims_and_keeps_earlier_wording() -> None:
    claims = W.dedupe({"v1": V1, "v2": V2})
    assert len(claims) == 6
    merged = [c for c in claims if len(c["versions"]) > 1]
    assert len(merged) == 1
    m = merged[0]
    assert m["sentence"] == V1[0]["sentence"]
    assert m["runs"] == ["v1", "v2"]
    assert [v["sentence"] for v in m["versions"]] == [V1[0]["sentence"], V2[0]["sentence"]]


def test_dedupe_never_merges_two_sentences_of_one_run() -> None:
    twin = dict(V1[0], n=2)
    claims = W.dedupe({"v1": [V1[0], twin]})
    assert len(claims) == 2


def test_dedupe_unions_cited_lines() -> None:
    a = dict(V1[2], y_lines=["L0010", "L0003"])
    b = dict(V1[2], y_lines=["L0010", "L0020"])
    (c,) = W.dedupe({"v1": [a], "v2": [b]})
    assert c["y_lines"] == ["L0003", "L0010", "L0020"]


def test_shuffle_is_seeded_and_independent_of_input_order() -> None:
    claims = W.dedupe({"v1": V1, "v2": V2})
    one = [c["sentence"] for c in W.shuffled(claims, 42)]
    two = [c["sentence"] for c in W.shuffled(list(reversed(claims)), 42)]
    assert one == two
    assert sorted(one) == sorted(c["sentence"] for c in claims)
    orders = {tuple(c["sentence"] for c in W.shuffled(claims, s)) for s in range(10)}
    assert len(orders) > 1


def _lines(texts: list[str]) -> list[dict[str, str]]:
    return [{"id": f"L{i:04d}", "t": "0:00", "who": "PM", "text": t} for i, t in enumerate(texts)]


def test_closest_prefers_rare_words_and_spreads_picks() -> None:
    filler = ["the remote is nice"] * 40
    texts = [*filler[:5], "we spoke to legal about lawsuits", *filler[5:], "R_S_I_ again"]
    lines = _lines(texts)
    keys = W.stems("legal lawsuits RSI remote")
    picks = W.closest(lines, keys, pad=8, n=3)
    assert picks[0] == 5 and len(lines) - 1 in picks
    assert all(abs(a - b) > 16 for a in picks for b in picks if a != b)


def test_expand_keys_matches_typos_only_for_missing_keys() -> None:
    lines = _lines(["we hit our profit target", "the remote and the remot"])
    keys = W.expand_keys({"proft", "remote"}, lines)
    assert "profit" in keys and "remot" not in keys


def test_windows_merge_overlaps() -> None:
    assert W.windows([5, 8, 40], 3, 42) == [(2, 12), (37, 42)]
