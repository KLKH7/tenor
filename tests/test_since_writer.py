"""The since-last-time writer on annotated pairs: units from AMI meetings, job files, and the
score against item fates (no network)."""

from __future__ import annotations

from typing import Any

from tenor.ami.corpus import AmiMeeting, DialogueAct, Speaker, SummarySentence
from tenor.evals import follow_through as T
from tenor.evals import since_writer as W


def _meeting(mid: str, texts: list[str], sentences: tuple[SummarySentence, ...] = ()) -> AmiMeeting:
    acts = tuple(
        DialogueAct(
            da_id=f"{mid}.da.{i}", agent="AB"[i % 2], start=61.0 * i, end=61.0 * i + 1, text=t
        )
        for i, t in enumerate(texts)
    )
    return AmiMeeting(
        meeting_id=mid,
        type="scenario",
        duration_s=600,
        speakers=(
            Speaker(agent="A", role="PM", participant="P1", channel=0),
            Speaker(agent="B", role="ID", participant="P2", channel=1),
        ),
        words=(),
        summary={},
        dialogue_acts=acts,
        sentences=sentences,
        has_summlinks=True,
    )


def _x() -> AmiMeeting:
    s = (
        SummarySentence(
            sentence_id="s1", section="actions", text="Dana will get two quotes.", links=()
        ),
        SummarySentence(
            sentence_id="s2", section="decisions", text="The menu has four things.", links=()
        ),
        SummarySentence(sentence_id="s3", section="actions", text="*NA*.", links=()),
    )
    return _meeting("XX1000a", ["x"], s)


def _y() -> AmiMeeting:
    return _meeting("XX1000b", ["here is the menu", "it has three things on it now"])


def test_units_lay_items_out_as_notes_under_the_fates_ids() -> None:
    x, y, items = W.units(_x(), _y())
    assert [b.render() for b in x.bullets] == [
        "B1 [section: Decisions] The menu has four things.",
        "B2 [section: Action items] Dana will get two quotes.",
    ]
    assert [(i.item_id, i.bullet_id, i.kind) for i in items] == [
        ("D1", "B1", "decision"),
        ("A1", "B2", "task"),
    ]
    assert T.y_lines(y) == [
        "L0000 [00:00] Project Manager: here is the menu",
        "L0001 [01:01] Industrial Designer: it has three things on it now",
    ]


def test_jobs_carry_the_adaptation_note_both_documents_and_no_fate() -> None:
    x, y, items = W.units(_x(), _y())
    job = W.writer_job(x, y, "since_last_time.v1", "PROMPT TEXT", "out.jsonl")
    assert job == W.writer_job(x, y, "since_last_time.v1", "PROMPT TEXT", "out.jsonl")
    for part in ("PROMPT TEXT", W.ADAPTATION, "B2 [section: Action items]", "L0001 [01:01]"):
        assert part in job
    line = T.WriterLine(
        sentence="Last time the menu had four things; today it has three.",
        kind="changed",
        x_bullets=("B1",),
        y_lines=("L0001",),
        quote="it has three things",
    )
    assert T.check_line(line, [b.id for b in x.bullets], y) == "ok"
    check = W.check_job(x, y, items, [line], "CHECK PROMPT", "c.jsonl")
    assert "- D1 (decision): B1 [section: Decisions] The menu has four things." in check
    assert "1. (changed; about B1) Last time the menu had four things" in check
    assert 'quote: "it has three things"' in check
    assert "changed_silently" not in check and "since_last_time.v1" not in check


def _fate(pair: str, item: str, fate: str, needed: str = "yes") -> dict[str, Any]:
    return {
        "pair": pair,
        "series": pair[:6],
        "x_item": item,
        "item_kind": "decision" if item[0] == "D" else "action",
        "fate": fate,
        "needed_followup": needed,
    }


def _line(
    pair: str, items: list[str], a: str, b: str, kind: str = "not_mentioned"
) -> dict[str, Any]:
    return {
        "unit": pair,
        "kind": kind,
        "status": "ok",
        "supported_a": a,
        "supported_b": b,
        "x_items": items,
        "strict": a == b == "yes",
        "lenient": "yes" in (a, b),
        "names_silent_item": False,
    }


def test_score_counts_truth_coverage_and_pairs() -> None:
    p1, p2, p3 = "AA1000a__AA1000b", "BB1000a__BB1000b", "CC1000a__CC1000b"
    fates = [
        _fate(p1, "D1", "changed_silently"),
        _fate(p1, "A1", "not_mentioned"),
        _fate(p1, "A2", "not_mentioned", "no"),  # not expected back: not silent
        _fate(p2, "A1", "not_mentioned"),
        _fate(p3, "D1", "reported"),
    ]
    lines = [
        _line(p1, ["A1"], "yes", "yes"),
        _line(p1, ["D1"], "yes", "no", "changed"),  # not true: D1 is not covered
        _line(p3, ["D1"], "no", "no"),
    ]
    got = W.score(lines, fates, [p1, p2, p3], 42)
    assert got == W.score(lines, fates, [p1, p2, p3], 42)
    assert (got["pairs"], got["teams"], got["sentences"]) == (3, 3, 3)
    assert (got["true"]["num"], got["true"]["den"], got["true_one_checker"]) == (1, 3, 2)
    assert got["true_by_kind"]["changed"] == {"num": 0, "den": 1}
    assert got["silent_items"] == 3
    assert (got["covered"]["num"], got["covered"]["den"]) == (1, 3)
    assert got["covered_by_item_kind"]["action"]["num"] == 1
    assert got["covered_by_item_kind"]["decision"]["den"] == 1
    assert got["covered_by_silent_kind"]["changed_silently"]["num"] == 0
    assert got["covered_by_silent_kind"]["not_mentioned"]["den"] == 2
    assert got["pairs_with_a_silent_item"] == 2
    assert got["silent_pairs_with_a_sentence"]["num"] == 1
    assert got["silent_pairs_with_a_silent_item_covered"]["num"] == 1
    assert got["pairs_without_a_silent_item"] == 1
    assert got["quiet_pairs_with_a_sentence"]["num"] == 1
    assert got["quiet_pairs_with_a_true_sentence"]["num"] == 0
    assert got["sentences_per_pair"] == {"0": 1, "1": 1, "2": 1, "3": 0}
    assert got["true_sentences_by_fate_of_item"] == {"not_mentioned": 1}
    by_kind = got["covered_by_sentence_kind"]
    assert (by_kind["not_mentioned"]["num"], by_kind["not_mentioned"]["den"]) == (1, 3)
    assert by_kind["changed"]["num"] == 0
    nm = got["not_mentioned_items_covered_by_not_mentioned_sentences"]
    assert (nm["num"], nm["den"]) == (1, 2)
