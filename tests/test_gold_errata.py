"""AMI gold errata (data/ami_questions/gold_errata.v1.json): validation, how E3 applies them
(ids kept, text checked, unknown sentences rejected), gold_version in pooling, and, when the AMI
zip is present, that every correction's original text and evidence quotes are in AMI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tenor.ami.corpus import DEFAULT_ZIP, SummarySentence, load_meeting, meeting_note
from tenor.ami.errata import ERRATA_V1, GOLD_ORIGINAL, Errata, load_errata
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
def test_v1_matches_ami_and_quotes_are_verbatim() -> None:
    e = load_errata()
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
