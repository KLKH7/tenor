"""Meeting memory: the series record's update step, its invariants and provenance check, the AMI
seed, the item matching and fates agreement, and the question-comparison scoring (no network)."""

from __future__ import annotations

import json

import pytest

from tenor.ami.corpus import AmiMeeting, DialogueAct, Speaker, SummarySentence
from tenor.evals import came_back as K
from tenor.evals import meeting_memory as MM
from tenor.evals import meeting_memory_qa as Q
from tenor.memory import series_check as C
from tenor.memory import series_record as R


def _lines(texts: list[str]) -> dict[str, R.Line]:
    return {
        f"L{i:04d}": R.Line(line_id=f"L{i:04d}", speaker="PM", text=t) for i, t in enumerate(texts)
    }


T1 = _lines(["we will paint the shed blue", "Dana will get two quotes for the awning", "okay"])
T2 = _lines(
    [
        "here are the two awning quotes from Dana",
        "the shed is going to be green now, blue was sold out",
        "nothing else today",
    ]
)


def _first() -> R.SeriesRecord:
    upd = R.Update(
        meeting="m1",
        new_entries=(
            R.EntryIn(
                kind="decision",
                text="The shed will be painted blue.",
                evidence=(R.RefIn(line="L0000", quote="paint the shed blue"),),
            ),
            R.EntryIn(
                kind="commitment",
                text="Dana will get two awning quotes.",
                owner="Dana",
                evidence=(R.RefIn(line="L0001", quote="Dana will get two quotes"),),
            ),
        ),
    )
    rec = R.SeriesRecord(series="s")
    assert C.update_problems(rec, upd, T1) == []
    return R.apply_update(rec, upd, "memory_update.v1")


def _second_update(**over: object) -> R.Update:
    events = [
        {
            "entry_id": "E001",
            "kind": "changed_openly",
            "summary": "Green instead of blue.",
            "evidence": [{"line": "L0001", "quote": "going to be green now"}],
            "current": "The shed will be painted green.",
        },
        {
            "entry_id": "E002",
            "kind": "reported",
            "summary": "Quotes delivered.",
            "evidence": [{"line": "L0000", "quote": "two awning quotes"}],
        },
    ]
    return R.Update.model_validate({"meeting": "m2", "events": events, **over})


def test_apply_update_derives_status_and_current_and_stays_append_only() -> None:
    rec = _first()
    assert [e.entry_id for e in rec.entries] == ["E001", "E002"]
    assert all(e.status == "open" for e in rec.entries)
    upd = _second_update()
    assert C.update_problems(rec, upd, T2) == []
    rec2 = R.apply_update(rec, upd, "memory_update.v1")
    e1, e2 = rec2.entries
    assert (e1.status, e1.current, e1.text) == (
        "changed_openly",
        "The shed will be painted green.",
        "The shed will be painted blue.",
    )
    assert e2.status == "reported"
    assert e1.history[0] == rec.entries[0].history[0]  # earlier history untouched
    assert rec2.meetings == ("m1", "m2") and [s.meeting for s in rec2.steps] == ["m1", "m2"]
    assert C.record_problems(rec2, {"m1": T1, "m2": T2}) == []
    assert R.render(rec2) == R.render(R.SeriesRecord.model_validate_json(R.dump(rec2)))
    with pytest.raises(ValueError, match="already"):
        R.apply_update(rec2, upd, "memory_update.v1")


def test_not_mentioned_keeps_the_status_and_must_say_what_was_searched() -> None:
    rec = _first()
    quiet = {"entry_id": "E002", "kind": "not_mentioned", "summary": "Not raised."}
    bad = _second_update(events=[_second_update().events[0].model_dump(), quiet])
    assert any("looked for" in p for p in C.update_problems(rec, bad, T2))
    ok_event = {**quiet, "searched": ["awning", "quote"]}
    ok = _second_update(events=[_second_update().events[0].model_dump(), ok_event])
    assert C.update_problems(rec, ok, T2) == []
    assert R.apply_update(rec, ok, "v").entries[1].status == "open"
    assert R.derive_status([*rec.entries[1].history]) == "open"


def test_update_problems_catch_skipped_entries_bad_pointers_and_bad_shapes() -> None:
    rec = _first()
    skipped = _second_update(events=[_second_update().events[0].model_dump()])
    assert any("no event for m2: E002" in p for p in C.update_problems(rec, skipped, T2))
    ev = _second_update().events
    wrong_line = ev[1].model_copy(update={"evidence": (R.RefIn(line="L0099", quote="x y z"),)})
    wrong_quote = ev[1].model_copy(
        update={"evidence": (R.RefIn(line="L0000", quote="three quotes from Sam"),)}
    )
    short = ev[1].model_copy(update={"evidence": (R.RefIn(line="L0000", quote="quotes"),)})
    no_current = ev[0].model_copy(update={"current": None})
    for bad, needle in (
        (wrong_line, "has no line"),
        (wrong_quote, "not found"),
        (short, "shorter than"),
        (no_current, "now is"),
    ):
        events = [e for e in ev if e.entry_id != bad.entry_id] + [bad]
        upd = R.Update(meeting="m2", events=tuple(events))
        assert any(needle in p for p in C.update_problems(rec, upd, T2)), needle
    unknown = R.Update(meeting="m2", events=(*ev, ev[0].model_copy(update={"entry_id": "E009"})))
    assert any("unknown entry" in p for p in C.update_problems(rec, unknown, T2))


def test_quote_check_ignores_case_and_punctuation_but_not_words() -> None:
    assert C.quote_problem("Paint the SHED, blue!", "we will paint the shed blue") is None
    assert C.quote_problem("okay", "Okay.") is None  # a whole short line
    assert C.quote_problem("the shed blue we", "we will paint the shed blue") is not None
    assert C.quote_problem("", "anything") == "empty quote"


def test_record_problems_catch_tampering() -> None:
    rec = R.apply_update(_first(), _second_update(), "v")
    ts = {"m1": T1, "m2": T2}
    e1 = rec.entries[0]
    lied = rec.model_copy(
        update={"entries": (e1.model_copy(update={"status": "open"}), *rec.entries[1:])}
    )
    assert any("history says" in p for p in C.record_problems(lied, ts))
    cut = e1.model_copy(update={"history": e1.history[:1], "status": "open", "current": e1.text})
    skipped = rec.model_copy(update={"entries": (cut, *rec.entries[1:])})
    assert any("live in m2 but no event" in p for p in C.record_problems(skipped, ts))
    ref = R.LineRef(meeting="m1", line="L0000", quote="paint the shed blue")
    moved = e1.history[1].model_copy(update={"evidence": (ref,)})
    other = rec.model_copy(
        update={
            "entries": (e1.model_copy(update={"history": (e1.history[0], moved)}), *rec.entries[1:])
        }
    )
    assert any("not its own meeting" in p for p in C.record_problems(other, ts))
    assert any("no transcript" in p for p in C.record_problems(rec, {"m1": T1}))


def _ami(mid: str, texts: list[str], sentences: tuple[SummarySentence, ...] = ()) -> AmiMeeting:
    acts = tuple(
        DialogueAct(
            da_id=f"{mid}.da.{i}", agent="AB"[i % 2], start=10.0 * i, end=10.0 * i + 1, text=t
        )
        for i, t in enumerate(texts)
    )
    return AmiMeeting(
        meeting_id=mid,
        type="scenario",
        duration_s=600,
        speakers=(
            Speaker(agent="A", role="PM", participant="P1", channel=0),
            Speaker(agent="B", role="ME", participant="P2", channel=1),
        ),
        words=(),
        summary={},
        dialogue_acts=acts,
        sentences=sentences,
        has_summlinks=True,
    )


def _x() -> AmiMeeting:
    texts = [f"talk number {i}" for i in range(20)]
    texts[4] = "let us make the case out of wood"
    texts[15] = "can you price the display please"
    sentences = (
        SummarySentence(
            sentence_id="s1",
            section="decisions",
            text="The case will be wood.",
            links=("XX0001a.da.4",),
        ),
        SummarySentence(
            sentence_id="s2",
            section="actions",
            text="The Marketing Expert will price the display.",
            links=("XX0001a.da.15",),
        ),
    )
    return _ami("XX0001a", texts, sentences)


def test_seed_record_cites_the_annotators_linked_lines_and_passes_the_checker() -> None:
    x = _x()
    rec = MM.seed_record("XX0001", x)
    assert [(e.kind, e.owner, e.annotation_id) for e in rec.entries] == [
        ("decision", None, "XX0001a:D1"),
        ("commitment", "Marketing Expert", "XX0001a:A1"),
    ]
    assert rec.entries[0].history[0].evidence[0].line == "L0004"
    assert C.record_problems(rec, {"XX0001a": MM.transcript(x)}) == []
    assert MM.transcript_text(x).splitlines()[4] == K.line(x, 4)


def _entry(n: int, meeting: str, lines: list[str], text: str = "t") -> R.Entry:
    refs = [R.LineRef(meeting=meeting, line=ln, quote="q q q") for ln in lines]
    return R.make_entry(n, "decision", text, None, meeting, "transcript", refs, "s")


def test_match_items_by_proximity_prefers_more_and_closer_pointers() -> None:
    items = K.items(_x())  # D1 linked to 4, A1 linked to 15
    entries = [
        _entry(1, "XX0001a", ["L0006"]),
        _entry(2, "XX0001a", ["L0004", "L0005"]),
        _entry(3, "XX0001a", ["L0019"]),
        _entry(4, "XX0001b", ["L0015"]),  # another meeting never matches
    ]
    assert MM.match_items(items, entries, "XX0001a") == {"D1": "E002", "A1": None}
    assert MM.match_items(items, entries, "XX0001a", pad=4) == {"D1": "E002", "A1": "E003"}


def test_parse_matches_and_fates_agreement() -> None:
    text = "\n".join(
        json.dumps(d)
        for d in (
            {"meeting": "XX0001a", "item": "D1", "entry": "E001", "reason": "same"},
            {"meeting": "XX0001a", "item": "A1", "entry": "E777"},
            {"meeting": "XX0001a", "item": "A1", "entry": None},
        )
    )
    got, errors = MM.parse_matches(text, {"XX0001a": ["D1", "A1"]}, {"XX0001a": ["E001", "E002"]})
    assert got == {"XX0001a:D1": "E001", "XX0001a:A1": None}
    assert any("E777" in e for e in errors)
    e1 = _entry(1, "XX0001a", ["L0004"])
    later = R.Event(
        meeting="XX0001b",
        kind="changed_without_comment",
        summary="s",
        evidence=(R.LineRef(meeting="XX0001b", line="L0001", quote="q q q"),),
        current="c",
    )
    e1 = e1.model_copy(update={"history": (*e1.history, later)})
    rec = R.SeriesRecord(series="XX0001", meetings=("XX0001a", "XX0001b"), entries=(e1,))
    pairs = [("XX0001a", "XX0001b", K.items(_x()), {"D1": "changed_silently", "A1": "reported"})]
    res = MM.fates_agreement(pairs, rec, mapping=got)
    assert (res["items"], res["matched"], res["exact"], res["coarse"]) == (2, 1, 1, 1)
    assert res["unmatched"] == ["XX0001a__XX0001b:A1"]


def test_qa_packets_are_blind_and_seeded_and_the_score_counts_tokens() -> None:
    key = Q.packet_key(["q2", "q1"], 42)
    assert key == Q.packet_key(["q1", "q2"], 42)
    assert all(sorted(v.values()) == ["raw", "record"] for v in key.values())
    answers = {
        ("q1", "record"): Q.Answer(qid="q1", condition="record", answer="Blue."),
        ("q1", "raw"): Q.Answer(qid="q1", condition="raw", answer="Green."),
        ("q2", "record"): Q.Answer(qid="q2", condition="record", answer="", abstain=True),
        ("q2", "raw"): Q.Answer(qid="q2", condition="raw", answer="Dana."),
    }
    text = Q.packet([("q1", "Colour?", "Blue"), ("q2", "Who?", "Dana")], answers, key)
    assert "record" not in text and "raw" not in text and "(abstained) (no answer)" in text
    a = {
        ("q1", "X"): "correct",
        ("q1", "Y"): "incorrect",
        ("q2", "X"): "correct",
        ("q2", "Y"): "incorrect",
    }
    b = {**a, ("q2", "Y"): "partially_correct"}
    with pytest.raises(ValueError, match="reader c"):
        Q.settle(a, b, {})
    settled, agree = Q.settle(a, b, {("q2", "Y"): "incorrect"})
    assert agree == 3
    verdicts = {(q, key[q][lab]): v for (q, lab), v in settled.items()}
    lk = Q.Lookup(command="lines", meeting="m", arg="L1", lines_returned=1, chars_returned=40)
    costs = {
        (q, c): Q.read_cost(400, [lk] if c == "record" else [])
        for q in ("q1", "q2")
        for c in Q.CONDITIONS
    }
    assert costs[("q1", "record")] == {
        "job_chars": 400,
        "lookup_chars": 40,
        "lookups": 1,
        "tokens": 110,
    }
    out = Q.score(["q1", "q2"], verdicts, costs, answers)
    total = sum(out["conditions"][c]["correct"] for c in Q.CONDITIONS)
    assert total == 2 and out["conditions"]["record"]["used_lookup"] == 2
    assert out["conditions"]["record"]["abstained"] == 1
