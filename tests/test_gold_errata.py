"""AMI gold errata (data/ami_questions/gold_errata.v1.json, .v2.json): validation, how E3 applies
them (ids kept, text checked, unknown sentences rejected), gold_version in pooling, v2 as a
superset of v1, the question rules (exclude / regold) and the recorded question changes, and,
when the AMI zip is present, that every entry's original text and evidence quotes are in AMI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tenor.ami.corpus import DEFAULT_ZIP, SummarySentence, load_meeting, meeting_note
from tenor.ami.errata import (
    ERRATA_LATEST,
    ERRATA_V1,
    ERRATA_V2,
    GOLD_ORIGINAL,
    Errata,
    apply_to_question,
    check_superset,
    load_errata,
    question_changes,
    question_effect,
)
from tenor.evals import notes_completeness as N
from tenor.evals.list_check import merge_turns, transcript_lines


def _errata(corrections: list[dict[str, Any]]) -> Errata:
    raw = json.loads(ERRATA_V1.read_text())
    return Errata.model_validate(raw | {"corrections": corrections})


def _fix(sid: str, item: str, text: str, action: str, new: str | None) -> dict[str, Any]:
    return {
        "meeting_id": "M1",
        "item_id": item,
        "sentence_id": sid,
        "original_text": text,
        "action": action,
        "corrected_text": new,
        "evidence": [],
        "reason": "test",
    }


SENTS = [
    SummarySentence(sentence_id="s1", section="decisions", text="Use rubber.", links=()),
    SummarySentence(sentence_id="s2", section="decisions", text="Standard chip.", links=()),
    SummarySentence(sentence_id="s3", section="decisions", text="Two colours.", links=()),
    SummarySentence(sentence_id="s4", section="actions", text="The ID does X.", links=()),
]


def test_v1_file_loads_and_is_e3_ready() -> None:
    e = load_errata()
    assert e.gold_version == "ami-1.6.2+errata.v1" and e.base == GOLD_ORIGINAL
    keys = {(c.meeting_id, c.item_id, c.action) for c in e.corrections}
    assert keys == {
        ("ES2006a", "A2", "correct_owner"),
        ("ES2006a", "A3", "correct_owner"),
        ("TS3009c", "D9", "drop"),
        ("ES2006c", "P1", "drop"),
    }
    assert [(n.meeting_id, n.item_id) for n in e.not_applied] == [("IS1000a", "A2")]
    assert all(c.evidence for c in e.corrections if c.item_id != "P1")


def test_gold_items_with_errata_keep_ids_drop_and_correct() -> None:
    e = _errata(
        [
            _fix("s2", "D2", "Standard chip.", "drop", None),
            _fix("s4", "A1", "The ID does X.", "correct_owner", "The UI does X."),
        ]
    )
    gold = N.gold_items(SENTS, e, "M1")
    assert [(g.id, g.text) for g in gold] == [
        ("D1", "Use rubber."),
        ("D3", "Two colours."),  # not renumbered
        ("A1", "The UI does X."),
    ]
    assert N.gold_items(SENTS, e, "M2") == N.gold_items(SENTS)  # other meetings untouched
    assert N.gold_version(e) == e.gold_version and N.gold_version(None) == GOLD_ORIGINAL


def test_gold_items_errata_must_match() -> None:
    with pytest.raises(ValueError, match="does not match"):
        N.gold_items(SENTS, _errata([_fix("s2", "D2", "Advanced chip.", "drop", None)]), "M1")
    with pytest.raises(ValueError, match="does not match"):
        N.gold_items(SENTS, _errata([_fix("s2", "D9", "Standard chip.", "drop", None)]), "M1")
    with pytest.raises(ValueError, match="unknown sentences"):
        N.gold_items(SENTS, _errata([_fix("s9", "D9", "Gone.", "drop", None)]), "M1")


def test_load_errata_rejects_inconsistent_actions(tmp_path: Path) -> None:
    raw = json.loads(ERRATA_V1.read_text())
    for bad in (
        [_fix("s2", "D2", "x", "drop", "y")],
        [_fix("s2", "D2", "x", "correct_owner", None)],
        [_fix("s2", "D2", "x", "drop", None), _fix("s2", "D2", "x", "drop", None)],
    ):
        path = tmp_path / "e.json"
        path.write_text(json.dumps(raw | {"corrections": bad}))
        with pytest.raises(ValueError):
            load_errata(path)


def test_pool_treats_missing_gold_version_as_original() -> None:
    assert N._pool_meta({}, "gold_version") == GOLD_ORIGINAL
    assert N._pool_meta({"gold_version": "x"}, "gold_version") == "x"
    assert "gold_version" in N.POOL_KEYS


@pytest.mark.skipif(not DEFAULT_ZIP.exists(), reason="AMI annotations zip not downloaded")
@pytest.mark.parametrize("path", [ERRATA_V1, ERRATA_V2])
def test_errata_match_ami_and_quotes_are_verbatim(path: Path) -> None:
    e = load_errata(path)
    for c in (*e.corrections, *e.not_applied):
        m = load_meeting(c.meeting_id)
        sent = {s.sentence_id: s for s in m.sentences}[c.sentence_id]
        assert " ".join(sent.text.split()) == c.original_text
        gold = {g.text: g.id for g in N.gold_items(m.sentences)}
        assert gold[c.original_text] == c.item_id
        lines = merge_turns(transcript_lines(meeting_note(m), lambda u: u.speaker_name or "?"))
        by_id = {f"L{x.index:04d}": x for x in lines}
        for ev in c.evidence:
            assert by_id[ev.line].speaker == ev.speaker
            assert ev.quote in by_id[ev.line].text, (c.meeting_id, ev.line)
    for mid in {c.meeting_id for c in e.corrections}:
        N.gold_items(load_meeting(mid).sentences, e, mid)  # applies cleanly


# -- v2 ---------------------------------------------------------------------------------------

V2_NEW = {
    ("IS1008d", "D2", "correct_content"),
    ("IS1008d", "D5", "correct_content"),
    ("IS1008d", "A4", "correct_content"),
    ("IS1008d", "P1", "correct_content"),
    ("IB4003", "D2", "drop"),
    ("IB4003", "D3", "drop"),
    ("IS1008b", "D2", "drop"),
    ("IS1008c", "D4", "drop"),
}


def _questions() -> list[dict[str, Any]]:
    files = ("questions.jsonl", "replay_extension.jsonl")
    return [
        json.loads(line)
        for f in files
        for line in (Path("data/ami_questions") / f).read_text().splitlines()
    ]


def test_v2_is_a_superset_of_v1_and_e3_ready() -> None:
    v1, v2 = load_errata(ERRATA_V1), load_errata(ERRATA_V2)
    assert ERRATA_LATEST == ERRATA_V2
    assert v2.gold_version == "ami-1.6.2+errata.v2" and v2.base == GOLD_ORIGINAL
    check_superset(v1, v2)
    keys = {(c.meeting_id, c.item_id, c.action) for c in v2.corrections}
    old = {(c.meeting_id, c.item_id, c.action) for c in v1.corrections}
    assert keys - old == V2_NEW
    added = {c.added_in for c in v2.corrections if (c.meeting_id, c.item_id, c.action) in V2_NEW}
    assert added == {"ami-1.6.2+errata.v2"}
    assert {(n.meeting_id, n.item_id) for n in v2.not_applied} == {
        ("IS1000a", "A2"),
        ("IS1008b", "A3"),  # the marketing-concept gloss: not applied, as in v1
        ("IS1008c", "D2"),
    }
    assert all(c.evidence for c in v2.corrections if c.meeting_id != "ES2006c")  # *NA* has none
    assert all(c.question_ids for c in v2.corrections if c.added_in == "ami-1.6.2+errata.v2")


def test_v2_question_changes_match_the_rules() -> None:
    e = load_errata(ERRATA_V2)
    derived = question_changes(_questions(), e)
    assert [c.model_dump() for c in derived] == [c.model_dump() for c in e.question_changes]
    effect = {c.qid: c.effect for c in e.question_changes}
    assert effect["ami-IB4003-decision-02"] == "exclude"  # its only sentence dropped
    assert effect["ami-IS1008b-decision-02"] == "exclude"
    assert effect["ami-IS1008b-IS1008c-what_changed-04"] == "exclude"  # pair broken
    assert effect["ami-IS1008c-IS1008d-what_changed-03"] == "exclude"
    assert effect["ami-IB4003-decision-01"] == "regold"
    q = e.question_change("ami-IB4003-decision-01")
    assert q is not None and q.gold_answer is not None and "cafeteria" not in q.gold_answer
    for c in e.corrections:  # every question holding a corrected sentence is recorded
        assert set(c.question_ids) <= set(effect), c.sentence_id


def _q(qtype: str, sids: list[str], gold: str) -> dict[str, Any]:
    return {"id": "q", "type": qtype, "gold_sentence_ids": sids, "gold_answer": gold}


def test_question_rules() -> None:
    e = _errata(
        [
            _fix("s2", "D2", "Standard chip.", "drop", None),
            _fix("s3", "D3", "Two colours.", "correct_content", "Three colours."),
            _fix("s4", "A1", "The ID does X.", "correct_owner", "The UI does X."),
        ]
    )
    dec = _q("decision", ["s1", "s2", "s3"], "Use rubber. Standard chip. Two colours.")
    assert apply_to_question(dec, e) == dec | {
        "gold_sentence_ids": ["s1", "s3"],
        "gold_answer": "Use rubber. Three colours.",
    }
    assert question_effect(_q("decision", ["s2"], "Standard chip."), e)[0] == "exclude"
    assert apply_to_question(_q("decision", ["s2"], "Standard chip."), e) is None
    untouched = _q("decision", ["s1"], "Use rubber.")
    assert question_effect(untouched, e) == ("unchanged", ())
    assert apply_to_question(untouched, e) == untouched
    assert question_effect(_q("what_changed", ["s1", "s3"], "x"), e)[0] == "exclude"
    assert question_effect(_q("action_who", ["s4"], "The ID."), e)[0] == "exclude"
    # an owner fix takes the item away from the old owner's action_what question
    assert apply_to_question(_q("action_what", ["s4"], "The ID does X."), e) is None
    assert apply_to_question(_q("problem", ["s4"], "The ID does X."), e) == _q(
        "problem", ["s4"], "The UI does X."
    )
    with pytest.raises(ValueError, match="not in the question's gold answer"):
        apply_to_question(_q("decision", ["s3"], "Two colors."), e)


def test_check_superset_and_v2_validation(tmp_path: Path) -> None:
    v1 = load_errata(ERRATA_V1)
    changed = v1.corrections[0].model_copy(update={"reason": "other"})
    with pytest.raises(ValueError, match="changes or loses"):
        check_superset(v1, v1.model_copy(update={"corrections": (changed, *v1.corrections[1:])}))
    with pytest.raises(ValueError, match="loses proposals"):
        check_superset(v1, v1.model_copy(update={"not_applied": ()}))
    raw = json.loads(ERRATA_V2.read_text())
    path = tmp_path / "e.json"
    no_date = [c | {"date": None} for c in raw["corrections"]]
    path.write_text(json.dumps(raw | {"corrections": no_date}))
    with pytest.raises(ValueError, match="added_in and date"):
        load_errata(path)
    both = raw["not_applied"] + [
        raw["corrections"][0] | {"proposed_action": "drop", "proposed_text": None}
    ]
    for n in both:
        n.pop("action", None), n.pop("corrected_text", None)
    path.write_text(json.dumps(raw | {"not_applied": both}))
    with pytest.raises(ValueError, match="both applied and not applied"):
        load_errata(path)
