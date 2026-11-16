"""Quiet-change lines: the writer's mechanical check, the verifier's answers, the rows joining
writer, verifier, checkers and fates, and the score (no network)."""

from __future__ import annotations

import json
from typing import Any

from tenor.evals import follow_through as T
from tenor.evals import quiet_change as Q
from tenor.evals import since_writer as W
from tests.test_since_writer import _x, _y

P = "XX1000a__XX1000b"


def _units() -> tuple[Any, Any, list[T.NoteItem]]:
    return W.units(_x(), _y())


def _changed(line: str = "L0001", text: str = "three things on it", bullets: str = "B1") -> str:
    return json.dumps(
        {
            "sentence": "Last time the plan had four things; today it has three.",
            "x_bullets": [bullets],
            "quotes": [{"line": line, "text": text}],
        }
    )


def test_parse_changed_checks_each_quote_against_its_own_line() -> None:
    x, y, _ = _units()
    ids = [b.id for b in x.bullets]
    raw = "\n".join(
        [
            _changed(),
            _changed(text="here is the menu"),  # words of L0000, not of L0001
            _changed(text="three things"),  # too short
            _changed(bullets="B9"),
            "not json",
            _changed(line="L0009"),
        ]
    )
    got, errors = Q.parse_changed(raw, ids, y)
    assert [w["status"] for w in got] == [
        "ok",
        "quote not in L0001",
        "quote too short (L0001)",
        "over the cap",
        "over the cap",
    ]
    assert len(errors) == 1


def test_as_writer_line_gives_the_checkers_a_changed_sentence() -> None:
    c = Q.ChangedLine.model_validate(
        {
            "sentence": "s",
            "x_bullets": ["B1"],
            "quotes": [
                {"line": "L0001", "text": "it has three"},
                {"line": "L0000", "text": "here is the"},
                {"line": "L0001", "text": "things on it"},
            ],
        }
    )
    w = Q.as_writer_line(c)
    assert (w.kind, w.y_lines) == ("changed", ("L0001", "L0000"))
    assert w.quote == "it has three ... here is the ... things on it"


def test_verify_job_shows_quotes_but_not_the_writers_notes() -> None:
    x, y, _ = _units()
    c = Q.ChangedLine.model_validate(
        {**json.loads(_changed()), "before": "SECRET-BEFORE", "searched": ["SECRET-TERM"]}
    )
    job = Q.verify_job(x, y, [c], "VERIFY PROMPT", "v.jsonl")
    assert job == Q.verify_job(x, y, [c], "VERIFY PROMPT", "v.jsonl")
    assert '- L0001: "three things on it"' in job and "VERIFY PROMPT" in job
    assert "L0000 [00:00] Project Manager: here is the menu" in job
    assert "SECRET" not in job


def _verdict(n: int, a: str, b: str, c: str, verdict: str) -> str:
    return json.dumps(
        {
            "line": n,
            "a_shows_different_version": a,
            "b_change_raised": b,
            "c_says_more": c,
            "verdict": verdict,
        }
    )


def test_a_line_is_kept_only_when_all_three_checks_pass_and_the_verdict_agrees() -> None:
    raw = "\n".join(
        [
            _verdict(1, "yes", "no", "no", "accept"),
            _verdict(2, "yes", "yes", "no", "reject"),
            _verdict(3, "yes", "no", "yes", "accept"),  # contradicts its own checks
            _verdict(4, "yes", "no", "no", "accept"),
        ]
    )
    got, errors = Q.parse_verifications(raw, 3)
    assert [got[k].keeps for k in (1, 2, 3)] == [True, False, False]
    assert got[2].failed == ["b"]
    assert len(errors) == 2  # line 3 contradicts itself; there is no proposed line 4


def _fate(item: str, fate: str) -> dict[str, Any]:
    return {"pair": P, "series": "XX1000", "x_item": item, "fate": fate}


def test_rows_number_verifier_and_checkers_over_what_each_saw() -> None:
    x, y, items = _units()
    raw = "\n".join([_changed(text="here is the menu"), _changed(), _changed(bullets="B2")])
    written, _ = Q.parse_changed(raw, [b.id for b in x.bullets], y)
    verified, _ = Q.parse_verifications(
        "\n".join(
            [_verdict(1, "yes", "no", "no", "accept"), _verdict(2, "no", "no", "no", "reject")]
        ),
        2,
    )
    check = '{"line": 1, "supported": "yes", "x_items": ["D1"]}'
    checks = {c: T.parse_verdicts(check, 1, ["D1", "A1"])[0] for c in T.CHECKERS}
    fates = [_fate("D1", "changed_silently"), _fate("A1", "changed_openly")]
    rows = Q.line_rows(P, written, verified, checks, items, fates)
    assert [(r["status"], r["verdict"], r["kept"], r["true"]) for r in rows] == [
        ("quote not in L0001", None, False, False),
        ("ok", "accept", True, True),
        ("ok", "reject", False, False),
    ]
    assert rows[1]["writer_items"] == ["D1"] and rows[1]["x_items"] == ["D1"]
    assert rows[2]["writer_item_fates"] == ["changed_openly"] and rows[2]["failed"] == ["a"]

    got = Q.score(rows, fates, [P], 42)
    assert got == Q.score(rows, fates, [P], 42)
    assert (got["proposed"], got["proposed_ok_mechanically"], got["kept"]) == (3, 2, 1)
    assert got["dropped_by_verifier"] == 1 and got["dropped_by_check"] == {"a": 1}
    assert (got["kept_true"]["num"], got["kept_true"]["den"]) == (1, 1)
    assert (got["covered"]["num"], got["covered"]["den"]) == (1, 1)
    assert got["false_alarms"] == {"changed openly": 0, "unchanged": 0}


def test_false_alarm_reads_the_fates_of_the_writers_items() -> None:
    row = {"kept": True, "writer_item_fates": ["changed_openly"]}
    assert Q.false_alarm(row) == "changed openly"
    assert Q.false_alarm({**row, "writer_item_fates": ["reported"]}) == "unchanged"
    assert (
        Q.false_alarm({**row, "writer_item_fates": ["changed_openly", "changed_silently"]}) is None
    )
    assert Q.false_alarm({**row, "kept": False}) is None
