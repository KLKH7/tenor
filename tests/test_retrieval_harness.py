"""Tests for step 3 (tenor.retrieval.harness): scoring from a hand-made tool log, no network."""

from tenor.evals.memory_breakdown import Fact
from tenor.retrieval.harness import (
    opened_meetings,
    score_trial,
    seen_lines,
    summarize,
    unsupported_quotes,
)
from tenor.retrieval.tools import ToolCall

PASSAGE = (
    "[M1 chunk 3 | L0040-L0042 | ID, PM]\n"
    "L0040 ID: the battery will cost twelve euros\n"
    "L0041 PM: okay\n"
    "L0042 ID: and we use a rubber case"
)
LOG = [
    ToolCall(tool="search_meetings", args={}, output="M1: Meeting M1\nM2: Meeting M2"),
    ToolCall(tool="search_transcripts", args={}, output=PASSAGE),
    ToolCall(tool="get_meetings", args={}, output="# M3: Meeting M3\nSome notes."),
]
FACT = Fact(
    qid="q1",
    fact_id="f1",
    text="The battery costs twelve euros.",
    evidence=(("M1", "L0040"),),
    rule="ids",
)
MISSING = Fact(qid="q1", fact_id="f2", text="Blue case.", evidence=(("M2", "L0001"),), rule="ids")


def test_seen_and_opened():
    assert seen_lines(LOG) == {("M1", "L0040"), ("M1", "L0041"), ("M1", "L0042")}
    assert opened_meetings(LOG) == {"M1", "M3"}  # M2 only appeared in a list of titles


def test_unsupported_quotes():
    assert unsupported_quotes('He said "the battery will cost twelve euros".', LOG) == []
    assert unsupported_quotes('He said "the battery costs twelve euros".', LOG) == [
        "the battery costs twelve euros"
    ]
    assert unsupported_quotes('A "short one" is ignored.', LOG) == []


def test_passing_trial():
    t = score_trial(
        "q1", 0, [FACT], "M1: the battery will cost twelve euros.", LOG, True, ["M1", "M2", "M3"]
    )
    assert t.passed and t.category == "passed"
    assert t.facts[0].retrieved and t.facts[0].retained


def test_citing_an_unopened_meeting_fails_even_if_correct():
    t = score_trial(
        "q1", 0, [FACT], "In M2 the battery cost twelve euros.", LOG, True, ["M1", "M2", "M3"]
    )
    assert not t.passed and t.cited_unopened == ("M2",) and t.category == "hard_fail"


def test_not_retrieved_category():
    t = score_trial("q1", 0, [FACT, MISSING], "Twelve euros.", LOG, False, ["M1", "M2"])
    assert t.category == "not_retrieved"
    assert [f.retrieved for f in t.facts] == [True, False]


def test_summary_pass_rate_is_mean_over_questions():
    ok = score_trial("q1", 0, [FACT], "the battery will cost twelve euros", LOG, True, ["M1"])
    bad = score_trial("q1", 1, [FACT], "no idea", LOG, False, ["M1"])
    other = score_trial("q2", 0, [FACT], "the battery will cost twelve euros", LOG, True, ["M1"])
    s = summarize([ok, bad, other], groups={"q1": "single", "q2": "cross"})
    assert s["all"].pass_rate == 0.75  # q1 passes 1 of 2 runs, q2 1 of 1
    assert s["single"].trials == 2 and s["cross"].trials == 1
