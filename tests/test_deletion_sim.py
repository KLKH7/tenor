"""Deletion simulation: what each condition shows the answer model, confident-error, tags, the
second judge and the exact quote (no network: scripted fakes, a fake token counter)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tenor.blackbox.kit import write_committable
from tenor.core.models import MeetingNote
from tenor.evals import deletion_sim as D
from tenor.evals.real_slice import GranolaAgentSystem
from tenor.llm.client import LLMClient, write_turn_cache
from tenor.memory.enhance_stub import STUB_ENHANCER
from tests.memory_helpers import ScriptedLLM, completion
from tests.test_concentrate_system import DIRECTORY, A, _notes, _q, _script
from tests.test_fallback import PASSAGES, AbstainingLLM
from tests.test_real_slice import FakeJudge, FakeMcp
from tests.test_real_slice import _q as _real_q

LINE_ROW = re.compile(r"^\[not_[A-Za-z0-9]+:\d{4}\] ", re.M)  # a shown transcript line


def _client(tmp_path: Path) -> LLMClient:
    return LLMClient(FakeJudge(), model="claude-opus-5-5", cache_dir=tmp_path / "llm")


def _memory(tmp_path: Path, cls: type[D.NotesFactsSystem], llm: ScriptedLLM) -> D.NotesFactsSystem:
    s = cls(
        _client(tmp_path),
        backend=llm,
        ingest_backend=llm,
        ingest_model="claude-sonnet-5-5",
        directory_path=DIRECTORY,
        cache_root=tmp_path / "concentrate",
        enhancer=STUB_ENHANCER,
    )
    s.ingest(_notes())
    return s


def _transcript_text(notes: list[MeetingNote]) -> list[str]:
    return [u.text for m in notes for u in m.utterances]


def test_notes_block_has_held_notes_and_no_transcript() -> None:
    notes = _notes()
    block = D.notes_block(notes, _q("x", "2026-09-30").as_of)
    held = D.held_notes(notes, _q("x", "2026-09-30").as_of)
    assert held and len(held) < len(notes)  # a later meeting is not held yet
    assert all(f"Summary of {m.title}" in block for m in held)
    assert not any(m.note_id in block for m in notes if m not in held)
    assert not LINE_ROW.search(block)
    assert not any(t in block for t in _transcript_text(notes))


def test_notes_only_sends_only_notes(tmp_path: Path) -> None:
    llm = ScriptedLLM({})
    s = D.NotesOnlySystem(_client(tmp_path), _notes(), backend=llm)
    q = _q("What is the price for Teams?", "2026-10-24")
    res = s.run(q)
    user = llm.requests[0].messages[0]["content"]
    assert "Summary of Pricing review" in user and "Question: What is the price" in user
    assert not LINE_ROW.search(user)
    assert not any(t in user for t in _transcript_text(_notes()))
    assert res.retrieved == [m.note_id for m in D.held_notes(_notes(), q.as_of)]
    assert s.prompt_texts(q)[1] == user
    # Prompt cache: one breakpoint at the end of the notes block, shared by every question.
    (cut,) = llm.requests[0].cache_breaks
    assert user[:cut].endswith("</meetings>\n\n") and user[cut:].startswith("Today's date:")


def test_notes_facts_deletes_every_transcript_even_same_day(tmp_path: Path) -> None:
    s = _memory(tmp_path, D.NotesFactsSystem, ScriptedLLM(_script()))
    assert s.retire_after_days == D.RETIRE_ALL and s.fallback == "off"
    same_day = max(m.scheduled_start or m.created_at for m in _notes()).date().isoformat()
    for as_of in ("2026-10-24", same_day):
        _, user = s.prompt_texts(_q("What is the current price for Northwind Teams?", as_of))
        assert user.startswith("<meetings>") and "Summary of Pricing review" in user
        assert "$20 per seat" in user  # the fact layer is there
        assert not LINE_ROW.search(user)  # ...but no transcript line is shown
        assert "older than -1 days" not in user
    assert s.fallback_texts(_q("x", "2026-10-24")) is None


def test_fallback_condition_reads_transcripts_despite_deletion(tmp_path: Path) -> None:
    llm = AbstainingLLM(_script())
    s = _memory(tmp_path, D.NotesFactsFallbackSystem, llm)
    q = _q("Who mentioned the zebra launch twenty dollars a seat?", "2026-10-24")
    res = s.run(q)
    assert res.extra["fallback_fired"] is True and res.llm_calls == 2
    second = str(llm.requests[-1].messages[0]["content"])
    assert PASSAGES in second and "<meetings>" in second
    assert re.search(r"\[" + A + r":\d{4}\]", second.split(PASSAGES, 1)[1])
    texts = s.fallback_texts(q)
    assert texts is not None and PASSAGES in texts[1]


def test_confident_error_separates_hedged_misses() -> None:
    assert D.confident_error("incorrect", "Marcus proposed it.", False)
    assert not D.confident_error("incorrect", "The notes don't mention who proposed it.", False)
    assert not D.confident_error("incorrect", "Marcus.", True)
    assert not D.confident_error("partially_correct", "Marcus, maybe.", False)
    assert not D.confident_error("correct", "Marcus.", False)


def test_load_tags_formats(tmp_path: Path) -> None:
    assert D.load_tags(None) == {} and D.load_tags(tmp_path / "missing.json") == {}
    j = tmp_path / "t.json"
    j.write_text(json.dumps({"real-001": ["long_tail"], "real-002": "needs_transcript"}))
    assert D.load_tags(j) == {"real-001": ["long_tail"], "real-002": ["needs_transcript"]}
    rows = tmp_path / "t.jsonl"
    rows.write_text('{"qid": "a", "tags": ["x", "y"]}\n{"qid": "b", "tag": "z"}\n')
    assert D.load_tags(rows) == {"a": ["x", "y"], "b": ["z"]}
    lst = tmp_path / "l.json"
    lst.write_text(json.dumps([{"qid": "a", "tags": ["x"]}]))
    assert D.load_tags(lst) == {"a": ["x"]}


def _row(system: str, qid: str, verdict: str, answer: str = "x", **kw: Any) -> dict[str, Any]:
    return {
        "system": system,
        "qid": qid,
        "category": "who_said",
        "verdict": verdict,
        "abstained": False,
        "answer": {"answer": answer},
        **kw,
    }


def test_summary_rates_tags_and_paired_ladder() -> None:
    rows = [
        _row("granola_agent", "q1", "correct"),
        _row("granola_agent", "q2", "correct"),
        _row("notes_only", "q1", "incorrect", "Marcus did."),  # confident error
        _row("notes_only", "q2", "incorrect", "The notes don't say."),  # hedged
        _row("notes_facts", "q1", "correct"),
        _row("notes_facts", "q2", "incorrect", "Marcus did."),
    ]
    tags = {"q1": ["long_tail"]}
    second = {("notes_only", "q1"): "incorrect", ("notes_only", "q2"): "partially_correct"}
    s = D.summarize_deletion(D.annotate(rows, tags, second), seed=1)
    b = s["conditions"]["notes_only"]
    assert b["accuracy"] == 0.0 and b["confident_error"] == 0.5 and b["hedged_miss"] == 0.5
    assert b["judge2_n"] == 2 and b["judge_agreement"] == 0.5
    assert set(b["by_tag"]) == {"long_tail", "(untagged)"}
    assert b["by_tag"]["long_tail"]["confident_error"] == 1.0
    ba = s["paired"]["notes_only vs granola"]
    assert (ba["n"], ba["wins"], ba["losses"], ba["delta"]) == (2, 0, 2, -1.0)
    assert ba["sign_p"] == 0.5 and ba["by_tag"]["long_tail"]["losses"] == 1
    cb = s["paired"]["notes_facts vs notes_only"]
    assert (cb["wins"], cb["losses"], cb["sign_p"]) == (1, 0, 1.0)
    assert b["confident_error_flag"] == 1.0  # P2-T's flag-only definition counts the hedge
    assert "notes_facts_fallback vs notes_facts" not in s["paired"]


def test_run_deletion_sim_end_to_end_and_aggregate_is_committable(tmp_path: Path) -> None:
    client = _client(tmp_path)
    judge2 = LLMClient(FakeJudge(), model="claude-sonnet-5-5", cache_dir=tmp_path / "llm")
    notes_only = D.NotesOnlySystem(client, _notes(), backend=ScriptedLLM({}))
    granola = GranolaAgentSystem(FakeMcp(), record_dir=tmp_path / "g")
    q = _real_q()
    report = D.run_deletion_sim(
        [granola, notes_only],
        [q],
        client,
        judge2=judge2,
        tags={q.id: ["long_tail"]},
        seed=42,
        budget_usd=1.0,
        out_dir=tmp_path / "r",
        split="dev",
    )
    c = report["deletion"]["conditions"]
    assert c["granola_agent"]["accuracy"] == 1.0 and c["granola_agent"]["judge2_accuracy"] == 1.0
    assert c["notes_only"]["accuracy"] == 0.0 and c["notes_only"]["confident_error"] == 0.0
    assert report["deletion"]["paired"]["notes_only vs granola"]["losses"] == 1
    assert report["meta"]["judge2_model"] == "claude-sonnet-5-5"
    jsonl = Path(report["paths"]["jsonl"]).with_suffix(".deletion.jsonl")
    rows = [json.loads(x) for x in jsonl.read_text().splitlines()]
    assert {r["judge2_verdict"] for r in rows} == {"correct", "incorrect"}
    assert all(r["tags"] == ["long_tail"] for r in rows)
    saved = json.loads(Path(report["paths"]["json"]).read_text())
    assert saved["deletion"] == json.loads(json.dumps(report["deletion"]))
    agg = {**report, "metrics": {"judge1": report["metrics"], "deletion": report["deletion"]}}
    out = write_committable(agg, tmp_path / "agg.json", [q.question, q.gold_answer])
    assert "Who owns the launch" not in out.read_text()


def _counter(texts: list[str]) -> list[int]:
    return [len(t.split()) for t in texts]  # deterministic stand-in for count_tokens


def _cal(**kw: Any) -> D.Calibration:
    base: dict[str, Any] = {
        "answer_overhead": 100,
        "judge_overhead": 50,
        "answer_out": (400.0, 900.0),
        "judge_out": (80.0, 150.0),
        "answer_text": 60.0,
        "fire_rate": 0.5,
    }
    return D.Calibration(**(base | kw))


def test_calibrate_measures_overhead_and_outputs() -> None:
    cal = D.calibrate(
        [("a b c", 103, 300), ("a b", 102, 500)],
        [("x y", 52, 70)],
        ["one two three"],
        _counter,
        fire_rate=0.25,
    )
    assert cal.answer_overhead == 100 and cal.judge_overhead == 50
    assert cal.answer_out == (400.0, 500) and cal.judge_out == (70.0, 70)
    assert cal.answer_text == 3.0 and cal.samples == {"answer": 2, "judge": 1, "texts": 1}


def test_quote_counts_exactly_and_cached_calls_are_free(tmp_path: Path) -> None:
    client = _client(tmp_path)
    s = D.NotesOnlySystem(client, _notes())
    qs = [_q("What is the price?", "2026-10-24"), _q("Who owns it?", "2026-10-24")]
    for q in qs:
        q.gold_answer, q.unanswerable = "Alex", False
    write_turn_cache(client, s.turn_request(qs[0]), completion('{"answer": "x"}', client.model))
    line = D.quote_condition(s, "notes_only", qs, client, None, _counter, _cal(), split="dev")
    want = _counter(["\n\n".join(s.prompt_texts(qs[1]))])[0] + 100
    assert (line.answer_calls, line.answer_cached, line.answer_in) == (1, 1, want)
    assert line.judge_calls == 2 and line.judge2_calls == 0
    assert line.usd_high > line.usd_expected > 0
    fresh = D.quote_condition(s, "notes_only", qs[1:], client, None, _counter, _cal(), split="dev")
    scaled = D.scale_line(fresh, 20, "test")
    assert scaled.questions == 20 and scaled.answer_calls == 20 and scaled.judge_calls == 20
    assert scaled.answer_in == fresh.answer_in * 20
    assert abs(scaled.usd_expected - fresh.usd_expected * 20) < 1e-9
    md = D.quote_markdown([line, scaled], _cal(), client.model, "none")
    assert "**total** | dev" in md and "**total** | test" in md


def test_quote_prices_the_fallback_range_and_granola_judges_only(tmp_path: Path) -> None:
    client = _client(tmp_path)
    s = _memory(tmp_path, D.NotesFactsFallbackSystem, ScriptedLLM(_script()))
    q = _q("What is the current price for Northwind Teams?", "2026-10-24")
    q.gold_answer, q.unanswerable = "$20", False
    line = D.quote_condition(
        s, "notes_facts_fallback", [q], client, None, _counter, _cal(), split="dev"
    )
    assert line.fallback_calls_max == 1 and line.fallback_in_max > line.answer_in
    g = D.quote_condition(
        None,
        "granola",
        [q],
        client,
        None,
        _counter,
        _cal(),
        split="dev",
        granola_answer=lambda _q: ("Twenty dollars.", False),
    )
    assert g.answer_calls == 0 and g.answer_in == 0 and g.judge_calls == 1
    judged = D.judge_request_for(q.question, q.gold_answer, False, "Twenty dollars.", False)
    client.structured(judged, D.JudgeOutput)  # Granola's judge verdict is now cached
    g2 = D.quote_condition(
        None,
        "granola",
        [q],
        client,
        None,
        _counter,
        _cal(),
        split="dev",
        granola_answer=lambda _q: ("Twenty dollars.", False),
    )
    assert (g2.judge_calls, g2.judge_cached) == (0, 1)
    with pytest.raises(ValueError):
        D.scale_line(g2, 20, "test")  # a cached judge call would understate the rate
    # ...but another condition's judge call is never priced from Granola's recorded answer
    other = D.quote_condition(
        s,
        "notes_facts_fallback",
        [q],
        client,
        None,
        _counter,
        _cal(),
        split="dev",
        granola_answer=lambda _q: ("Twenty dollars.", False),
    )
    assert (other.judge_calls, other.judge_cached) == (1, 0)


def test_quote_pays_an_identical_request_once_across_conditions(tmp_path: Path) -> None:
    client = _client(tmp_path)
    c = _memory(tmp_path, D.NotesFactsSystem, ScriptedLLM(_script()))
    d = _memory(tmp_path, D.NotesFactsFallbackSystem, ScriptedLLM(_script()))
    q = _q("What is the current price for Northwind Teams?", "2026-10-24")
    q.gold_answer, q.unanswerable = "$20", False
    shared: set[Path] = set()
    lc = D.quote_condition(
        c, "notes_facts", [q], client, None, _counter, _cal(), split="dev", shared=shared
    )
    ld = D.quote_condition(
        d, "notes_facts_fallback", [q], client, None, _counter, _cal(), split="dev", shared=shared
    )
    assert (lc.answer_calls, ld.answer_calls, ld.answer_cached) == (1, 0, 1)
    assert ld.fallback_calls_max == 1
