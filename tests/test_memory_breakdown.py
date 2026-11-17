"""Retrieved / retained / accurate breakdown on toy data (no network, no corpus)."""

from __future__ import annotations

import pytest

from tenor.evals import memory_breakdown as B


def test_facts_one_per_sentence_and_text_rule_without_links() -> None:
    fs = B.facts_for(
        "q1",
        [("s1", "The PM will ask.", [("ES1001a", "L0003")]), ("s2", "Budget too tight.", [])],
        [],
        "gold",
    )
    assert [(f.fact_id, f.rule, f.evidence) for f in fs] == [
        ("s1", "ids", (("ES1001a", "L0003"),)),
        ("s2", "text", ()),
    ]


def test_who_said_fact_is_the_gold_role() -> None:
    (f,) = B.facts_for("q2", [], [("ES1001a", "L0010")], "the Marketing Expert")
    assert f.fact_id == "q2#who" and f.text == "the Marketing Expert" and f.rule == "ids"


def test_rouge_l_recall_keeps_roles_and_order() -> None:
    assert B.rouge_l_recall("the Marketing Expert", "It was the Marketing Expert.") == 1.0
    assert B.rouge_l_recall("red green blue", "blue green red") == pytest.approx(1 / 3)
    assert B.rouge_l_recall("the of", "anything") == 1.0  # no content words
    assert B.rouge_l_recall("budget tight", "") == 0.0


def test_seen_lines_reads_blocks_lookups_and_pointers() -> None:
    text = "\n".join(
        [
            "## Meeting ES1001a: full human transcript",
            "```",
            "L0000 [00:00] Project Manager: hello",
            "```",
            '## E001 decision | first recorded: ES1001b: x [ES1001b L0007 "quote"; +2 more]',
            "L0099 [00:00] stray line outside a transcript block",
            "## Lookup output",
            "ES1001c L0004 [00:10] Marketing Expert: words",
            "ES1001b L0007 [00:12] Marketing Expert: now seen as a line",
        ]
    )
    seen = B.seen_lines(text)
    assert seen == {
        ("ES1001a", "L0000"): "line",
        ("ES1001b", "L0007"): "line",
        ("ES1001c", "L0004"): "line",
    }
    assert B.seen_lines('[ES1001b L0007 "q"]') == {("ES1001b", "L0007"): "pointer"}


def test_retrieve_by_ids_and_by_text_window() -> None:
    f = B.Fact(qid="q", fact_id="s", text="t", evidence=(("M", "L0001"),), rule="ids")
    assert B.retrieve(f, {("M", "L0001"): "pointer"}, []).via == "pointer"
    assert not B.retrieve(f, {("M", "L0002"): "line"}, []).retrieved
    g = B.Fact(qid="q", fact_id="s2", text="budget constrained group", evidence=(), rule="text")
    lines = ["x", "the budget", "we were constrained", "y", "z", "w", "v"]
    r = B.retrieve(g, {}, lines)
    assert r.retrieved and r.window_recall == pytest.approx(2 / 3, abs=1e-4)
    assert not B.retrieve(g, {}, ["nothing here"]).retrieved


def test_classify_first_failing_step() -> None:
    assert B.classify([False], [False], True) == "correct"
    assert B.classify([True, False], [True, True], False) == "not_retrieved"
    assert B.classify([True, True], [True, False], False) == "not_retained"
    assert B.classify([True], [True], False) == "retained_not_accurate"


def test_record_split_order() -> None:
    assert B.record_split(["in_record_wrong", "not_in_record"]) == "not_in_record"
    assert B.record_split(["in_record", "in_record_wrong"]) == "in_record_wrong"
    assert B.record_split(["in_record"]) == "in_record_unused"


def test_calibrate_picks_lower_middle_of_ties() -> None:
    cal = B.calibrate([0.1, 0.9], [False, True])
    # every threshold in (0.1, 0.9] agrees on both: 0.15 .. 0.90 -> lower middle
    tied = [t for t in B.GRID if 0.1 < t <= 0.9]
    assert cal.threshold == tied[(len(tied) - 1) // 2]
    assert (cal.agree, cal.tp, cal.tn, cal.kappa) == (2, 1, 1, 1.0)
    with pytest.raises(ValueError):
        B.calibrate([0.5], [])


def test_settle_uses_tiebreak_and_raises_when_missing() -> None:
    out, agree = B.settle({"a": "yes", "b": "no"}, {"a": "yes", "b": "yes"}, {"b": "no"})
    assert out == {"a": "yes", "b": "no"} and agree == 1
    with pytest.raises(ValueError):
        B.settle({"a": "yes"}, {"a": "no"}, {})


def test_table_counts_by_condition_group_and_split() -> None:
    rows = [
        B.Row(
            qid="q1",
            condition="record",
            group="single",
            verdict="incorrect",
            category="not_retrieved",
            facts=2,
            facts_retrieved=1,
            facts_retained=1,
            record_split="not_in_record",
        ),
        B.Row(
            qid="q2",
            condition="record",
            group="cross",
            verdict="correct",
            category="correct",
            facts=1,
            facts_retrieved=1,
            facts_retained=1,
        ),
    ]
    t = B.table(rows)["record"]
    assert t["all"] == {
        "n": 2,
        "correct": 1,
        "not_retrieved": 1,
        "not_retained": 0,
        "retained_not_accurate": 0,
    }
    assert t["cross"]["correct"] == 1 and t["facts"] == {"n": 3, "retrieved": 2, "retained": 2}
    assert t["record_split"]["not_in_record"] == 1
    assert t["record_split_cross"]["not_in_record"] == 0
    assert B.table(rows) == B.table(list(rows))  # deterministic
