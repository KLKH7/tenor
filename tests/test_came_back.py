"""Came back different: pair listing and the fixed split, job files, detector-line validation,
blind judge packets, majority of three and the series-cluster score (no network)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tenor.ami.corpus import AmiMeeting, DialogueAct, Speaker, SummarySentence
from tenor.evals import came_back as K

SUMM = """<?xml version="1.0"?><nite:root xmlns:nite="http://nite.sourceforge.net/">
<abstract><sentence nite:id="s.1">They met.</sentence></abstract>
<decisions>{d}</decisions><actions>{a}</actions></nite:root>"""


def _zip(tmp_path: Path) -> Path:
    """ES2003 a-d (a has a decision, b an action, c an empty summary), IS1002 b-d (no a; b has
    no summary file), a natural meeting, and a word file with no following meeting."""
    path = tmp_path / "ami.zip"
    one = '<sentence nite:id="s.2">Do it.</sentence>'
    with zipfile.ZipFile(path, "w") as zf:
        for mid in ("ES2003a", "ES2003b", "ES2003c", "ES2003d", "IS1002b", "IS1002c", "IS1002d",
                    "EN2001a", "TS3004a"):  # fmt: skip
            zf.writestr(f"words/{mid}.A.words.xml", "<x/>")
        zf.writestr("abstractive/ES2003a.abssumm.xml", SUMM.format(d=one, a=""))
        zf.writestr("abstractive/ES2003b.abssumm.xml", SUMM.format(d="", a=one + one))
        zf.writestr("abstractive/ES2003c.abssumm.xml", SUMM.format(d="", a=""))
        zf.writestr("abstractive/IS1002c.abssumm.xml", SUMM.format(d=one, a=one))
        zf.writestr("abstractive/TS3004a.abssumm.xml", SUMM.format(d=one, a=""))
        zf.writestr("abstractive/TS3004b.abssumm.xml", SUMM.format(d=one, a=""))
    return path


def test_available_pairs_need_transcripts_and_an_item_in_x(tmp_path: Path) -> None:
    counts = K.summary_counts(_zip(tmp_path))
    assert counts == {"ES2003a": 1, "ES2003b": 2, "ES2003c": 0, "ES2003d": None, "IS1002b": None,
                      "IS1002c": 2, "IS1002d": None, "TS3004a": 1}  # fmt: skip
    names = [p.name for p in K.available_pairs(counts)]
    assert names == ["ES2003a__ES2003b", "ES2003b__ES2003c", "IS1002c__IS1002d"]


def _all_pairs() -> list[K.Pair]:
    series = [f"ES{n}" for n in range(2002, 2017)] + [f"IS{n}" for n in range(1000, 1010)]
    return [K.Pair(x=s + a, y=s + b) for s in series for a, b in ("ab", "bc", "cd")]


def test_select_split_by_parity_with_known_pairs_in_test() -> None:
    sel = K.select(_all_pairs(), 42)
    assert sel == K.select(list(reversed(_all_pairs())), 42)  # input order does not matter
    assert len(sel.develop) == K.N_DEVELOP and len(sel.test) == K.N_TEST
    assert all(p.parity == "odd" and p.kind == "ab" for p in sel.develop)
    assert all(p.parity == "even" for p in sel.test)
    assert {p.name for p in sel.known} == set(K.KNOWN) <= {p.name for p in sel.test}
    assert sel.known_extra == ()
    assert {k: sum(p.kind == k for p in sel.test) for k in ("ab", "bc", "cd")} == dict(K.TEST_MIX)
    others = [p.series for p in sel.test if p.name not in K.KNOWN]
    assert len(set(others)) == len(others) and "IS1008" not in others  # one pair per series
    assert sel.groups(sel.known[0]) == ["test", "known"]
    assert K.select(_all_pairs(), 7).develop != sel.develop


def test_select_known_pairs_outside_test_form_an_extra_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(K, "KNOWN", ("IS1007b__IS1007c",))
    sel = K.select(_all_pairs(), 42)
    assert [p.name for p in sel.known_extra] == ["IS1007b__IS1007c"]
    assert len(sel.test) == K.N_TEST and sel.groups(sel.known_extra[0]) == ["known"]
    assert sel.known_extra[0] in sel.all_pairs()


def _meeting(mid: str, texts: list[str], sentences: tuple[SummarySentence, ...] = ()) -> AmiMeeting:
    agents = "AB"
    acts = tuple(
        DialogueAct(
            da_id=f"{mid}.da.{i}", agent=agents[i % 2], start=61.0 * i, end=61.0 * i + 1, text=t
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
        SummarySentence(sentence_id="s1", section="abstract", text="They met.", links=()),
        SummarySentence(
            sentence_id="s3", section="actions", text="Dana will get two quotes.", links=()
        ),
        SummarySentence(
            sentence_id="s2",
            section="decisions",
            text="The menu will have sourdough, two soups and a vegan pastry.",
            links=("XX1000a.da.9", "XX1000a.da.1", "nowhere"),
        ),
    )
    return _meeting("XX1000a", [f"x talk {i}" for i in range(12)], s)


def _y() -> AmiMeeting:
    texts = [f"filler {i}" for i in range(60)]
    texts[3] = "what about the pastries, the vegan ones"
    texts[30] = "here is the menu, sourdough at the top"
    texts[31] = "then the two soups"
    texts[55] = "the soups and sourdough both sold out"
    return _meeting("XX1000b", texts)


def test_items_and_job_file() -> None:
    x, y = _x(), _y()
    its = K.items(x)
    assert [(i.item_id, i.linked) for i in its] == [("D1", (1, 9)), ("A1", ())]
    job = K.job_file(x, y, "PROMPT TEXT\n")
    assert job == K.job_file(x, y, "PROMPT TEXT\n")
    assert "PROMPT TEXT" in job and "cbd_agent_runs/<RUN>/XX1000a__XX1000b.jsonl" in job
    assert "- XX1000a: Industrial Designer (P2); Project Manager (P1)" in job
    assert "* X0001 [01:01] Industrial Designer: x talk 1" in job
    assert "  X0004 [04:04] Project Manager: x talk 4\n  ...\n  X0006" in job
    assert "X0011" in job and "### A1: Dana will get two quotes." in job
    assert "(no passage: the annotator made no links for this sentence)" in job
    y_part = job.split("## Meeting Y")[1]
    assert y_part.count("\nL") == 60 and "L0031 [31:31] Industrial Designer: then the two" in y_part
    assert "They met." not in job  # X's abstract is not an item; Y has no annotation at all


def _claim(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "x_item": "D1",
        "parts": ["sourdough", "two soups", "vegan pastry"],
        "came_back": ["sourdough", "two soups"],
        "missing_or_changed": ["vegan pastry"],
        "y_lines": ["L0030", "L0031"],
        "x_quote": "x talk 9",
        "line_for_user": "Agreed with three parts; two came back. Not on the menu: the pastry.",
        "confidence": "high",
    }
    return {**base, **over}


def test_parse_claims_rejects_bad_lines() -> None:
    lines = [
        json.dumps(_claim()),
        "",
        "not json",
        json.dumps(_claim(x_item="A1", confidence="certain")),
        json.dumps(_claim(x_item="D9")),
        json.dumps(_claim(x_item="A1", y_lines=["L0060"])),
        json.dumps(_claim(x_item="A1", y_lines=["31"])),
        json.dumps(_claim()),
        json.dumps(_claim(x_item="A1", missing_or_changed=[])),
        json.dumps(_claim(x_item="A1", extra=1)),
        json.dumps(_claim(x_item="A1", confidence="low")),
    ]
    claims, errors = K.parse_claims("\n".join(lines), ["D1", "A1"], 60)
    assert [(c.x_item, c.confidence) for c in claims] == [("D1", "high"), ("A1", "low")]
    assert [e.split(":")[0] for e in errors] == [f"line {n}" for n in (3, 4, 5, 6, 7, 8, 9, 10)]
    assert "unknown x_item 'D9'" in errors[2] and "second line for D1" in errors[5]
    assert K.parse_claims("", ["D1"], 60) == ([], [])  # an empty file is a valid answer


def test_judge_packet_is_blind_and_shows_context_and_key_word_passages() -> None:
    x, y = _x(), _y()
    claims, _ = K.parse_claims(json.dumps(_claim()), ["D1", "A1"], 60)
    packet = K.judge_packet(x, y, claims, "JUDGE PROMPT", ["dir_a", "dir_b"])
    assert "## Claim `XX1000a__XX1000b#1`" in packet and "JUDGE PROMPT" in packet
    assert "confidence" not in packet and "high" not in packet
    assert "cbd_jobs/XX1000a__XX1000b.md" in packet and "dir_a, dir_b" in packet
    assert "* X0009" in packet and '"line_for_user"' in packet
    assert "> L0030 [30:30]" in packet and "> L0031" in packet
    assert "  L0015 " in packet and "  L0046 " in packet and "L0014 " not in packet
    assert "L0047" not in packet
    # outside the window: the missing part's key word alone matches; two item words match
    assert "> L0003 [03:03] Industrial Designer: what about the pastries" in packet
    assert "  L0001 " in packet and "  L0005 " in packet and "L0006 " not in packet
    assert "> L0055 " in packet and "  L0057 " in packet
    assert K.key_words("The pastries, including two soups") == {"pastri", "soup"}


def test_majority_and_verdict_parsing() -> None:
    t, c, n = "true_silent_drop", "changed_openly", "not_dropped"
    assert K.majority([t, t, n]) == t and K.majority([c, n, c]) == c
    assert K.majority([t, c, n]) == "unclear"
    with pytest.raises(ValueError):
        K.majority([t, t])
    text = "\n".join([
        json.dumps({"claim_id": "p#1", "verdict": t, "reason": "r"}),
        json.dumps({"claim_id": "p#2", "verdict": "maybe", "reason": "r"}),
        json.dumps({"claim_id": "p#1", "verdict": n, "reason": "r"}),
    ])  # fmt: skip
    got, errors = K.parse_verdicts(text)
    assert got == {"p#1": t} and len(errors) == 2


def test_score_groups_and_series_clusters() -> None:
    t, c, n = "true_silent_drop", "changed_openly", "not_dropped"
    rows = [
        K.pair_row(K.Pair(x="IS1008b", y="IS1008c"), ["test", "known"], [("high", t), ("low", n)]),
        K.pair_row(K.Pair(x="IS1008c", y="IS1008d"), ["test", "known"], [("high", t)]),
        K.pair_row(K.Pair(x="ES2002a", y="ES2002b"), ["test"], []),
        K.pair_row(K.Pair(x="ES2004a", y="ES2004b"), ["test"], [("medium", c)]),
        K.pair_row(K.Pair(x="ES2003a", y="ES2003b"), ["develop"], []),
    ]
    out = K.score(rows, 42)
    assert out == K.score(rows, 42) and list(out) == ["develop", "test", "known"]
    test = out["test"]
    assert (test["pairs"], test["series"]) == (4, 3)
    assert test["lines_per_pair"]["value"] == 1.0
    assert (test["share_true_silent_drop"]["num"], test["share_true_silent_drop"]["den"]) == (2, 4)
    assert test["share_pairs_with_true"]["value"] == 0.5
    assert test["share_changed_openly"]["value"] == 0.25
    assert test["verdicts"] == {t: 2, c: 1, n: 1, "item_not_returned": 0, "unclear": 0}
    assert test["by_confidence"]["high"]["value"] == 1.0
    assert test["by_confidence"]["low"]["value"] == 0.0
    lo, hi = test["share_true_silent_drop"]["ci"]
    assert 0.0 <= lo <= 0.5 <= hi <= 1.0
    # both IS1008 pairs are one cluster: resampling one series gives no spread
    assert out["known"]["share_true_silent_drop"]["ci"] == [0.6667, 0.6667]
    dev = out["develop"]
    assert dev["lines_per_pair"]["value"] == 0.0 and dev["share_true_silent_drop"]["value"] is None
    assert dev["share_true_silent_drop"]["ci"] is None
