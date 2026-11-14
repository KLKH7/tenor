"""Agents reading through the product's MCP: jobs, rule checks, blind packets and the score."""

from __future__ import annotations

import pytest

from tenor.evals import mcp_reading as M


def _run(qid: str, cond: str, tools: tuple[str, ...] = ("get_meetings",), **kw: object) -> M.Run:
    return M.Run(qid=qid, condition=cond, answer=kw.pop("answer", "an answer"), tools=tools, **kw)  # type: ignore[arg-type]


def test_jobs_cover_every_condition_and_map_ids() -> None:
    js = M.jobs(
        [("q1", "Who?", ["IS1"]), ("q2", "What?", ["IS1", "IS2"])], {"IS1": "a", "IS2": "b"}
    )
    assert [(j.qid, j.condition) for j in js[:3]] == [("q1", c) for c in M.CONDITIONS]
    assert js[3].meeting_ids == ("a", "b")
    with pytest.raises(KeyError):
        M.jobs([("q", "?", ["none"])], {})


def test_problems_flag_rule_breaks() -> None:
    assert M.problems(_run("q", "notes")) == []
    assert "outside" in M.problems(_run("q", "notes", ("get_meetings", "search_transcripts")))[0]
    assert M.problems(_run("q", "transcript", ("get_meetings",)))
    assert M.problems(_run("q", "free", ("search_meetings",)))
    assert M.problems(_run("q", "free", (), answer=""))
    assert M.problems(_run("q", "free", (), answer="", abstain=True)) == []


def test_read_transcript_and_key_determinism() -> None:
    assert _run("q", "free", ("get_meetings", "get_meeting_transcript")).read_transcript
    assert not _run("q", "free", ("query_granola_meetings",)).read_transcript
    key = M.packet_key(["b", "a"], 42)
    assert key == M.packet_key(["a", "b"], 42)
    assert sorted(key["a"].values()) == sorted(M.CONDITIONS)


def test_packet_hides_conditions() -> None:
    runs = {("q", c): _run("q", c, answer=f"text-{i}") for i, c in enumerate(M.CONDITIONS)}
    key = {"q": {"X": "transcript", "Y": "free", "Z": "notes"}}
    text = M.packet([("q", "Who?", "The gold")], runs, key)
    assert "Answer X: text-2" in text and "Answer Z: text-1" in text
    assert not any(c in text for c in M.CONDITIONS)


def test_score_counts_pairs_and_changes() -> None:
    runs, verdicts = [], {}
    table = {
        "q1": ("correct", "incorrect", "correct"),
        "q2": ("partially_correct", "partially_correct", "correct"),
        "q3": ("incorrect", "incorrect", "incorrect"),
    }
    for qid, vs in table.items():
        for cond, v in zip(M.CONDITIONS, vs, strict=True):
            tools = ("get_meetings",) if cond == "notes" else ("get_meetings", "search_transcripts")
            if (qid, cond) == ("q3", "free"):
                tools = ("query_granola_meetings",)
            runs.append(_run(qid, cond, tools))
            verdicts[(qid, cond)] = v
    s = M.score(runs, verdicts, seed=42)
    assert s["conditions"]["free"]["correct"]["k"] == 1
    assert s["conditions"]["transcript"]["not_wrong"]["k"] == 2
    assert s["conditions"]["free"]["read_transcript"] == 2
    assert s["paired"]["transcript vs notes"]["correct"] == {"a_only": 2, "b_only": 0, "p": 0.5}
    assert s["free_by_reading"]["did_not_read"] == {"n": 1, "correct": 0, "not_wrong": 0}
    assert [c["qid"] for c in s["changed_by_transcript"]] == ["q1", "q2"]
    assert s == M.score(runs, verdicts, seed=42)
    with pytest.raises(ValueError):
        M.score(runs[:-1], verdicts)
