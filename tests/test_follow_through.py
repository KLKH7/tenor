"""Follow-through from product notes: items from labelled bullets, job files, the final-fate
rule, the writer's mechanical check and the writer score (no network)."""

from __future__ import annotations

import json

from tenor.evals import bullet_cues as B
from tenor.evals import came_back as K
from tenor.evals import follow_through as T
from tenor.evals import item_fates as F
from tenor.evals.list_check import Line
from tenor.evals.notes_completeness import Bullet

PAIR = K.Pair(x="IS1008a", y="IS1008b")


def _unit(meeting: str) -> B.CueUnit:
    bullets = (
        Bullet("B1", "Overview", "A remote for television"),
        Bullet("B2", "Decisions", "The case will be rubber"),
        Bullet("B3", "Next steps", "Ana to cost the two soups"),
        Bullet("B4", "Next steps", "Sam to ask about the bin store", parent="B3"),
    )
    lines = (
        Line(0, "Them", "00:01", "Morning everybody."),
        Line(1, "Them", "00:09", "So the case is going to be hard plastic, in yellow."),
        Line(2, "Them", "00:20", "I costed the soups, both under a pound."),
    )
    return B.CueUnit(meeting, "granola", "n/a", bullets, lines, (), "")


KINDS = {"B1": "fact", "B2": "decision", "B3": "task", "B4": "task"}


def _answer(item: str, fate: str, needed: str = "yes") -> F.Answer:
    return F.Answer(x_item=item, fate=fate, needed_followup=needed, confidence="high")  # type: ignore[arg-type]


def test_note_items_are_the_decision_and_task_bullets_numbered_per_kind() -> None:
    items = T.note_items(_unit("IS1008a").bullets, KINDS)
    assert [(i.item_id, i.bullet_id, i.kind) for i in items] == [
        ("D1", "B2", "decision"),
        ("A1", "B3", "task"),
        ("A2", "B4", "task"),
    ]
    assert "(sub-point of B3)" in items[2].text
    assert T.note_items(_unit("IS1008a").bullets, {}) == []


def test_parse_kinds_skips_lines_that_are_not_labels() -> None:
    text = '{"bullet_id": "B1", "presented_as": "task"}\nnot json\n[1]\n{"presented_as": "x"}\n'
    assert T.parse_kinds(text) == {"B1": "task"}


def test_fates_job_file_is_deterministic_and_the_tiebreak_shows_only_its_items() -> None:
    x, y = _unit("IS1008a"), _unit("IS1008b")
    items = T.note_items(x.bullets, KINDS)
    full = T.fates_job_file(x, y, items, "PROMPT", "out.jsonl")
    assert full == T.fates_job_file(x, y, items, "PROMPT", "out.jsonl")
    assert "all 3 items: D1, A1, A2" in full and T.ADAPTATION in full
    assert "L0001 [00:09] Them: So the case is going to be hard plastic" in full
    only = T.fates_job_file(x, y, items, "PROMPT", "out.jsonl", only=["A2"])
    assert "only the 1 of 3 items" in only
    assert "- A2 (task)" in only and "- D1 (decision)" not in only
    assert "B2 [section: Decisions] The case will be rubber" in only  # the notes stay whole


def test_item_rows_take_agreement_then_the_tiebreak_then_unclear() -> None:
    items = T.note_items(_unit("IS1008a").bullets, KINDS)
    answers = {
        "a": {
            "D1": _answer("D1", "changed_silently"),
            "A1": _answer("A1", "reported"),
            "A2": _answer("A2", "not_mentioned"),
        },
        "b": {
            "D1": _answer("D1", "changed_silently"),
            "A1": _answer("A1", "returned_unchanged"),
            "A2": _answer("A2", "changed_openly", "no"),
        },
        "c": {"A1": _answer("A1", "reported")},
    }
    rows = T.item_rows(PAIR, "granola", items, answers)
    assert [r["fate"] for r in rows] == ["changed_silently", "reported", F.UNCLEAR]
    assert rows[2]["needed_followup"] == F.UNCLEAR
    assert T.silent_items(rows) == ["D1"]
    assert T.distribution(rows)["reported"] == 1 and T.distribution(rows)[F.UNCLEAR] == 1
    assert T.agreement(rows) == {"n": 3, "agree": 1, "kappa": 0.25}


def test_not_mentioned_is_silent_only_when_follow_up_was_needed() -> None:
    assert T.is_silent({"fate": "not_mentioned", "needed_followup": "yes"})
    assert not T.is_silent({"fate": "not_mentioned", "needed_followup": "no"})
    assert not T.is_silent({"fate": "changed_openly", "needed_followup": "yes"})


def _line(**kw: object) -> str:
    base = {
        "sentence": "The case was to be rubber; today it is hard plastic.",
        "kind": "changed",
        "x_bullets": ["B2"],
        "y_lines": ["L0001"],
        "quote": "going to be hard plastic",
    }
    return json.dumps({**base, **kw})


def test_writer_lines_are_checked_mechanically() -> None:
    x, y = _unit("IS1008a"), _unit("IS1008b")
    ids = [b.id for b in x.bullets]
    text = "\n".join(
        [
            _line(),
            _line(quote="going to be soft rubber"),
            _line(kind="not_mentioned", x_bullets=["B4"], y_lines=[], quote=""),
            _line(x_bullets=["B9"]),
            "not json",
        ]
    )
    written, errors = T.parse_writer(text, ids, y)
    assert [w["status"] for w in written] == [
        "ok",
        "quote not in the cited lines",
        "ok",
        "over the cap",
    ]
    assert len(errors) == 1
    one = T.parse_writer(_line(quote="hard plastic"), ids, y)[0][0]
    assert one["status"] == "quote too short"
    bad = T.parse_writer(_line(kind="not_mentioned", x_bullets=["B4"]), ids, y)[0][0]
    assert bad["status"] == "not_mentioned with evidence"
    assert T.parse_writer(_line(y_lines=["L0009"]), ids, y)[0][0]["status"] == "unknown y_lines"


def test_check_job_file_shows_items_without_fates_and_the_cited_evidence() -> None:
    x, y = _unit("IS1008a"), _unit("IS1008b")
    items = T.note_items(x.bullets, KINDS)
    written, _ = T.parse_writer(_line(), [b.id for b in x.bullets], y)
    body = T.check_job_file(x, y, items, [written[0]["line"]], "PROMPT", "out.jsonl")
    assert "1. (changed; about B2)" in body and 'quote: "going to be hard plastic"' in body
    assert "- D1 (decision): B2" in body and "changed_silently" not in body


def test_verdicts_are_validated_against_the_sentences_and_the_items() -> None:
    text = "\n".join(
        [
            '{"line": 1, "supported": "yes", "x_items": ["D1", "Z9"]}',
            '{"line": 1, "supported": "no"}',
            '{"line": 5, "supported": "yes"}',
            '{"line": 2, "supported": "maybe"}',
        ]
    )
    got, errors = T.parse_verdicts(text, 2, ["D1", "A1"])
    assert list(got) == [1] and got[1].x_items == ("D1",)
    assert len(errors) == 4


def test_writer_score_counts_supported_lines_and_covered_silent_items() -> None:
    x, y = _unit("IS1008a"), _unit("IS1008b")
    items = T.note_items(x.bullets, KINDS)
    fate_rows = T.item_rows(
        PAIR,
        "granola",
        items,
        {
            r: {
                "D1": _answer("D1", "changed_silently"),
                "A1": _answer("A1", "reported"),
                "A2": _answer("A2", "not_mentioned"),
            }
            for r in F.READERS
        },
    )
    text = "\n".join(
        [
            _line(),
            _line(kind="not_mentioned", x_bullets=["B3"], y_lines=[], quote=""),
            _line(quote="nothing like this was ever said"),
        ]
    )
    written, _ = T.parse_writer(text, [b.id for b in x.bullets], y)
    yes1 = T.Verdict(line=1, supported="yes", x_items=("D1",))
    verdicts = {
        "a": {1: yes1, 2: T.Verdict(line=2, supported="no", x_items=("A1",))},
        "b": {1: yes1, 2: T.Verdict(line=2, supported="yes", x_items=("A1",))},
    }
    rows = T.line_rows("IS1008a__IS1008b:granola", written, verdicts, fate_rows)
    assert [(r["strict"], r["lenient"]) for r in rows] == [
        (True, True),
        (False, True),
        (False, False),
    ]
    assert rows[0]["item_fates"] == ["changed_silently"] and rows[0]["names_silent_item"]
    assert rows[2]["status"] != "ok" and rows[2]["supported_a"] is None
    score = T.writer_score(rows, fate_rows)
    assert score["lines"] == 3 and score["lines_ok_mechanically"] == 2
    assert score["supported_strict"] == 1 and score["supported_lenient"] == 2
    assert score["silent_items"] == 2 and score["silent_items_covered"] == 1
    assert score["checkers_agree"] == 1
    assert score["by_kind"]["changed"] == {"lines": 2, "supported_strict": 1}
