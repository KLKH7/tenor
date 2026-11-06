"""AMI parser and transcript scorer, on a tiny hand-made NXT zip (no corpus files, no network)."""

from __future__ import annotations

import random
import zipfile
from pathlib import Path

import pytest

from tenor.ami.corpus import load_meeting
from tenor.ami.products import (
    AMI_EXTENDED,
    AMI_EXTENSION,
    AMI_SET,
    ami_tag,
    audio_path,
    match_notes,
    replay_state,
    score_note,
    wer_report,
)
from tenor.ami.wer import content_recall, edit_distance, normalise, score, spell_number
from tests.memory_helpers import make_note

NS = 'xmlns:nite="http://nite.sourceforge.net/"'
MEETINGS = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<nite:root {NS}>
 <meeting observation="XX0001a" type="scenario" duration="61.5">
  <speaker nxt_agent="B" role="ME" global_name="P2" channel="1"/>
  <speaker nxt_agent="A" role="PM" global_name="P1" channel="0"/>
 </meeting>
</nite:root>"""
WORDS_A = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<nite:root {NS}>
 <w starttime="1.0" endtime="1.2">Hello</w>
 <w starttime="1.2" endtime="1.2" punc="true">,</w>
 <vocalsound starttime="1.3" endtime="1.4" type="laugh"/>
 <w starttime="1.5" endtime="1.6" trunc="true">th</w>
 <w starttime="3.0" endtime="3.4">budget</w>
 <disfmarker starttime="3.5" endtime="3.5"/>
</nite:root>"""
WORDS_B = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<nite:root {NS}>
 <w starttime="2.0" endtime="2.3">twenty</w>
 <w starttime="2.3" endtime="2.6">five</w>
 <w starttime="2.6" endtime="2.9">Euros</w>
</nite:root>"""
SUMMARY = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<nite:root {NS}>
 <abstract><sentence>The team   met.</sentence></abstract>
 <decisions><sentence>Budget is 25 Euros.</sentence></decisions>
</nite:root>"""


@pytest.fixture
def ami_zip(tmp_path: Path) -> Path:
    path = tmp_path / "ami.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("corpusResources/meetings.xml", MEETINGS)
        zf.writestr("words/XX0001a.A.words.xml", WORDS_A)
        zf.writestr("words/XX0001a.B.words.xml", WORDS_B)
        zf.writestr("abstractive/XX0001a.abssumm.xml", SUMMARY)
    return path


def test_load_meeting(ami_zip: Path) -> None:
    m = load_meeting("XX0001a", ami_zip)
    assert m.type == "scenario" and m.duration_s == 62
    assert [(s.agent, s.role, s.participant) for s in m.speakers] == [
        ("A", "PM", "P1"),
        ("B", "ME", "P2"),
    ]
    # Non-word events dropped; punctuation and fragments kept but flagged; merged by time.
    assert [w.text for w in m.words] == ["Hello", ",", "th", "twenty", "five", "Euros", "budget"]
    assert m.reference_text() == "Hello twenty five Euros budget"
    assert m.summary == {"abstract": ("The team met.",), "decisions": ("Budget is 25 Euros.",)}


def test_unknown_meeting(ami_zip: Path) -> None:
    with pytest.raises(KeyError):
        load_meeting("XX9999z", ami_zip)


def test_normalise() -> None:
    assert normalise("Okay, uh, it's €25 - 12.5% of 1,200. Mm-hmm.") == [
        *["okay", "it's", "twenty", "five", "euros", "twelve", "point", "five", "percent", "of"],
        *["one", "thousand", "two", "hundred"],
    ]
    assert normalise("OK alright") == ["okay", "all", "right"]
    assert spell_number(0) == "zero"
    assert spell_number(1_000_005) == "one million five"


def _naive(a: list[str], b: list[str]) -> int:
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        diag, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            diag, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, diag + (x != y))
    return row[-1]


def test_edit_distance_matches_naive_dp() -> None:
    rng = random.Random(7)
    for _ in range(300):
        a = [rng.choice("abcd") for _ in range(rng.randint(0, 10))]
        b = [rng.choice("abcde") for _ in range(rng.randint(0, 10))]
        assert edit_distance(a, b) == _naive(a, b)


def test_score_and_order() -> None:
    ref = "the budget is twenty five euros and the deadline is friday"
    assert score(ref, ref).wer == 0.0
    s = score(ref, "The budget is €25, and the deadline's Friday.")
    assert s.edits == 2 and s.ref_words == 11  # "deadline's" for "deadline is"
    # Swapping two overlapping speakers' sentences costs WER but not content recall.
    swapped = "and the deadline is friday the budget is twenty five euros"
    assert score(ref, swapped).wer > 0
    assert content_recall(normalise(ref), normalise(swapped)) == 1.0


def test_match_and_score(ami_zip: Path) -> None:
    note = make_note(
        "n1",
        "2026-11-05",
        "AMI XX0001a",
        [("them", None, "Hello, 25 euros on the budget."), ("me", None, "room chatter")],
    )
    other = make_note("n2", "2026-11-05", "Weekly sync", [("me", None, "hi")])
    assert ami_tag(note) == "XX0001a" and ami_tag(other) is None
    matched = match_notes([note, other])
    assert list(matched) == ["XX0001a"]
    r = score_note("XX0001a", matched["XX0001a"], "granola", ami_zip)
    assert r.score.ref_words == 5 and r.score.hyp_words == 7
    assert r.score.content_recall == 1.0
    assert (r.product_speakers, r.reference_speakers) == (0, 2)
    assert (r.mic_utterances, r.owner_labelled) == (1, 1)  # mic line dropped, not scored
    kept = score_note("XX0001a", matched["XX0001a"], "wispr", ami_zip, drop_mic=False)
    assert (kept.mic_utterances, kept.owner_labelled) == (0, 1)
    assert kept.score.hyp_words == r.score.hyp_words + 2  # "room chatter" now scored
    with pytest.raises(ValueError):
        match_notes([note, note])


def test_wer_report_pools_counts_not_ratios() -> None:
    a = score("one two three four", "one two three four")  # 0 edits / 4 words
    b = score("alpha beta", "gamma delta")  # 2 edits / 2 words
    rows = [
        {
            "meeting_id": m,
            "product": "p",
            "score": s.model_dump(),
            "product_speakers": 0,
            "mic_utterances": 0,
            "owner_labelled": 0,
            "reference_speakers": 2,
        }
        for m, s in (("M1", a), ("M2", b))
    ]
    out = wer_report({"p": {"meta": {"drop_mic": True}, "meetings": rows}})["products"]["p"]
    assert out["pooled_wer"] == round(2 / 6, 4)  # not the mean of 0.0 and 1.0
    assert out["wer_range"] == [0.0, 1.0] and out["n_meetings"] == 2
    assert out["pooled_content_recall"] == round(
        a.content_found / (a.content_words + b.content_words), 4
    )


def test_replay_sets_keep_the_original_seven() -> None:
    # Published scores run on AMI_SET: adding meetings must not change it.
    assert AMI_SET == ("IS1008a", "IS1008b", "IS1008c", "IS1008d", "IB4003", "EN2002b", "ES2006a")
    assert AMI_EXTENDED[: len(AMI_SET)] == AMI_SET and len(set(AMI_EXTENDED)) == 14
    # One meeting per scenario team: no cross-meeting question can skip a session.
    assert len({m[:-1] for m in AMI_EXTENSION}) == len(AMI_EXTENSION)


def test_replay_state(tmp_path: Path) -> None:
    assert audio_path("XX0001a", tmp_path) == tmp_path / "XX0001a" / "XX0001a.Mix-Headset.wav"
    assert replay_state(True, []) == "downloaded, not recorded"
    assert replay_state(False, []) == "not downloaded"
    assert replay_state(True, ["wispr", "granola"]) == "recorded (granola, wispr)"
