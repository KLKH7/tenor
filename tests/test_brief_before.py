"""The before-meeting brief against the since-last-time line: jobs, parsing, tie-breaks and
the score (no network)."""

from __future__ import annotations

import json
from typing import Any

from tenor.evals import brief_before as BB
from tenor.evals import since_writer as W
from tests.test_since_writer import _fate, _line, _x, _y


def test_brief_job_has_x_notes_and_nothing_of_y() -> None:
    x, _, _ = W.units(_x(), _y())
    job = BB.brief_job(x, "brief_before.v1", "PROMPT TEXT", "out.jsonl")
    assert job == BB.brief_job(x, "brief_before.v1", "PROMPT TEXT", "out.jsonl")
    for part in ("PROMPT TEXT", BB.ADAPTATION, "B2 [section: Action items] Dana will get"):
        assert part in job
    assert "XX1000b" not in job and "here is the menu" not in job


def test_parse_brief_checks_citations_and_the_cap() -> None:
    raw = [
        {"bullet": "Dana was going to get two quotes.", "x_bullets": ["B2"]},
        {"bullet": "The menu has four things.", "x_bullets": ["B9"]},
        {"bullet": " ", "x_bullets": ["B1"]},
        {"bullet": "A fourth one.", "x_bullets": ["B1"]},
    ]
    text = "\n".join(json.dumps(r) for r in raw) + "\nnot json\n"
    got, bad = BB.parse_brief(text, ["B1", "B2"])
    assert [b["status"] for b in got] == [
        "ok",
        "x_bullets missing or unknown",
        "empty",
        "over the cap",
    ]
    assert len(bad) == 1 and bad[0].startswith("line 5")


def test_fate_job_is_blind_to_x_and_keeps_bullet_numbers() -> None:
    _, y, _ = W.units(_x(), _y())
    job = BB.fate_job(y, [(1, "First."), (3, "Third.")], "FATE PROMPT", "a.jsonl")
    assert "1. First.\n3. Third." in job
    assert "L0001 [01:01] Industrial Designer: it has three things on it now" in job
    assert "Dana will get two quotes" not in job and "XX1000a" not in job


def test_parse_fates_and_naming() -> None:
    line = {"bullet": 1, "fate": "reported", "expect_to_hear": "yes", "y_lines": ["L0001"]}
    got, bad = BB.parse_fates(json.dumps(line) + "\n" + json.dumps(dict(line, bullet=7)), [1, 3])
    assert got[1].fate == "reported"
    assert bad == ["line 2: unknown bullet 7", "no answer for bullet 3"]
    one, bad = BB.parse_fates(json.dumps(line), [3])
    assert one[3].fate == "reported" and bad == []
    naming = "\n".join(
        json.dumps(r)
        for r in (
            {"x_item": "D1", "named_by": [2]},
            {"x_item": "A1", "named_by": [1, 2]},
        )
    )
    by_bullet, bad = BB.parse_naming(naming, ["D1", "A1"], 2)
    assert by_bullet == {1: ["A1"], 2: ["D1", "A1"]} and bad == []


def test_resolve_and_tiebreak() -> None:
    assert BB.resolve("reported", "reported", None) == "reported"
    assert BB.resolve("reported", "not_mentioned", "not_mentioned") == "not_mentioned"
    assert BB.resolve("reported", "not_mentioned", None) == "unclear"
    a = BB.BulletFate(bullet=1, fate="reported", expect_to_hear="yes")
    assert not BB.needs_tiebreak(a, a)
    assert BB.needs_tiebreak(a, a.model_copy(update={"expect_to_hear": "no"}))
    assert BB.needs_tiebreak(a, None)


def _answers(fates: dict[int, str]) -> dict[int, BB.BulletFate]:
    return {
        n: BB.BulletFate(bullet=n, fate=f, expect_to_hear="yes")  # type: ignore[arg-type]
        for n, f in fates.items()
    }


def test_score_counts_staleness_naming_and_quiet_pairs() -> None:
    p1, p2 = "AA1000a__AA1000b", "BB1000a__BB1000b"
    fates = [
        _fate(p1, "D1", "reported"),
        _fate(p1, "A1", "not_mentioned"),  # silent
        _fate(p2, "D1", "returned_unchanged"),
    ]
    rows: list[dict[str, Any]] = []
    rows += BB.bullet_rows(
        p1,
        "AA1000",
        "fresh",
        [(1, "D1 bullet", ["D1"]), (2, "A1 bullet", ["A1"])],
        {
            "a": _answers({1: "reported", 2: "not_mentioned"}),
            "b": _answers({1: "reported", 2: "changed_openly"}),
            "c": _answers({2: "not_mentioned"}),
        },
        {1: ["D1"], 2: ["A1"]},
        [f for f in fates if f["pair"] == p1],
    )
    rows += BB.bullet_rows(
        p2,
        "BB1000",
        "fresh",
        [(1, "D1 bullet", ["D1"])],
        {"a": _answers({1: "returned_unchanged"}), "b": _answers({1: "returned_unchanged"})},
        {1: ["D1"]},
        [f for f in fates if f["pair"] == p2],
    )
    assert [r["fate"] for r in rows] == ["reported", "not_mentioned", "returned_unchanged"]
    lines = [_line(p1, ["A1"], "yes", "yes")]
    got = BB.score(rows, fates, lines, [p1, p2], 42)
    assert got == BB.score(rows, fates, lines, [p1, p2], 42)
    assert (got["dealt_with"]["num"], got["dealt_with"]["den"]) == (2, 3)
    assert got["quiet"]["num"] == 1
    assert got["silent_items"] == 1
    assert got["silent_named_by_brief"]["num"] == 1
    assert got["silent_covered_by_line"]["num"] == 1
    assert (got["other_items_named_by_brief"]["num"], got["other_items_named_by_brief"]["den"]) == (
        2,
        2,
    )
    assert got["quiet_pairs"] == 1
    assert got["quiet_pairs_brief"]["total"] == 1 and got["quiet_pairs_line"]["total"] == 0
    assert got["cross_check"]["single_same_fate"] == 3
    assert got["readers_agree_fate"] == {
        "n": 3,
        "same_fate": 2,
        "same_group": 2,
        "same_expect": 3,
    }
