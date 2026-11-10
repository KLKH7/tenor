"""Item fates: the seeded selection, reader and tie-break job files, answer validation, the
final-fate rule and the series-cluster score (no network)."""

from __future__ import annotations

import json
from collections import Counter

import pytest

from tenor.ami.corpus import AmiMeeting, DialogueAct, Speaker, SummarySentence
from tenor.evals import came_back as K
from tenor.evals import item_fates as F


def _all_pairs() -> list[K.Pair]:
    series = [f"ES{n}" for n in range(2002, 2017)] + [f"IS{n}" for n in range(1000, 1010)]
    return [K.Pair(x=s + a, y=s + b) for s in series for a, b in ("ab", "bc", "cd")]


def test_select_fills_each_position_within_the_series_cap() -> None:
    pairs = _all_pairs()
    develop = K.select(pairs, 42).develop
    got = F.select(pairs, develop, 42)
    assert got == F.select(list(reversed(pairs)), develop, 42)  # input order does not matter
    assert got == sorted(got, key=lambda p: p.name) and len(set(got)) == 36
    assert Counter(p.kind for p in got) == dict(F.MIX)
    assert max(Counter(p.series for p in got).values()) <= F.MAX_PER_SERIES
    assert not set(got) & set(develop)
    assert F.select(pairs, develop, 7) != got


def test_select_comes_up_short_rather_than_break_a_rule() -> None:
    few = [K.Pair(x="ES2002" + a, y="ES2002" + b) for a, b in ("ab", "bc", "cd")]
    assert len(F.select(few, [], 42)) == F.MAX_PER_SERIES


def test_parse_pairs_takes_consecutive_pairs_in_the_order_given() -> None:
    got = F.parse_pairs("TS3009c__TS3009d, IS1008a__IS1008b")
    assert [p.name for p in got] == ["TS3009c__TS3009d", "IS1008a__IS1008b"]
    assert [p.kind for p in got] == ["cd", "ab"]
    for bad in (
        "IS1008a__IS1008c",  # not consecutive
        "IS1008b__IS1008a",  # backwards
        "IS1008a__IS1009b",  # two series
        "IS1008a_IS1008b",
        "IS1008a__IS1008b,IS1008a__IS1008b",
        "",
    ):
        with pytest.raises(ValueError):
            F.parse_pairs(bad)


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
            sentence_id="s2",
            section="decisions",
            text="The menu will have sourdough and a vegan pastry.",
            links=("XX1000a.da.5",),
        ),
        SummarySentence(
            sentence_id="s3", section="decisions", text="Open at seven.", links=("XX1000a.da.0",)
        ),
        SummarySentence(sentence_id="s4", section="actions", text="*NA*.", links=()),
    )
    return _meeting("XX1000a", [f"x line {i}" for i in range(12)], s)


def _y() -> AmiMeeting:
    return _meeting("XX1000b", ["here is the menu", "sourdough at the top", "send it to print"])


def test_items_drop_the_annotators_placeholder_and_keep_ids() -> None:
    assert [i.item_id for i in K.items(_x())] == ["D1", "D2", "A1", "A2"]
    assert [i.item_id for i in F.items(_x())] == ["D1", "D2", "A1"]
    assert all(F._PLACEHOLDER.match(t) for t in ("NA.", "*NA*", "*NA*.", "NA"))
    assert not F._PLACEHOLDER.match("NA is the supplier.")


def test_job_file_lists_every_item_with_kind_passage_and_all_of_y() -> None:
    job = F.job_file(_x(), _y(), "PROMPT TEXT\n", "out/a/XX1000a__XX1000b.jsonl")
    assert job == F.job_file(_x(), _y(), "PROMPT TEXT\n", "out/a/XX1000a__XX1000b.jsonl")
    assert job.startswith("# Item fates: XX1000a -> XX1000b\n")
    assert "Classify all 3 items: D1, D2, A1." in job and "`out/a/XX1000a__XX1000b.jsonl`" in job
    assert "PROMPT TEXT" in job and "Project Manager (P1)" in job
    assert "### D1 (decision): The menu will have sourdough and a vegan pastry." in job
    assert "### A1 (action): Dana will get two quotes." in job
    # The linked line is starred, with three lines of context either side and no more.
    assert "* X0005 [05:05] Industrial Designer: x line 5" in job
    assert "  X0002 [02:02]" in job and "  X0008 [08:08]" in job
    assert "X0001 [" not in job.split("### D2")[0] and "X0009" not in job
    assert "no links for this sentence" in job  # A1
    for i, text in enumerate(("here is the menu", "sourdough at the top", "send it to print")):
        assert f"L{i:04d} [0{i}:0{i}]" in job and text in job


def test_tiebreak_job_shows_only_the_disputed_items_and_no_answers() -> None:
    job = F.job_file(_x(), _y(), "PROMPT TEXT\n", "out/c/p.jsonl", only=["A1", "D2"])
    assert "Classify only the 2 of 3 items listed below: D2, A1." in job
    assert "### D2 (decision)" in job and "### A1 (action)" in job and "### D1" not in job
    assert "L0002 [02:02]" in job
    assert not any(f in job for f in F.FATES)  # the prompt here is a stub: no fate leaks in


def _line(item: str, fate: str, needed: object = "yes", **extra: object) -> str:
    row = {
        "x_item": item,
        "fate": fate,
        "needed_followup": needed,
        "y_lines": ["L0001"],
        "what_changed": "pastry: absent" if fate.startswith("changed_") else "",
        "reason": "r",
        "confidence": "high",
    }
    return json.dumps(row | extra)


def test_parse_answers_validates_each_line() -> None:
    text = "\n".join(
        [
            _line("D1", "changed_silently"),
            "",
            _line("D2", "reported", True),
            _line("A1", "not_mentioned", " No ", y_lines=[]),
            _line("D1", "reported"),  # repeat
            _line("D9", "reported"),  # unknown item
            _line("D2", "vanished"),  # unknown fate
            _line("D2", "reported", "maybe"),
            _line("D2", "reported", y_lines=["L0003"]),  # Y has 3 lines: L0000-L0002
            _line("D2", "reported", y_lines=["X0001"]),
            "not json",
        ]
    )
    got, errors = F.parse_answers(text, ["D1", "D2", "A1"], 3)
    assert {k: (a.fate, a.needed_followup) for k, a in got.items()} == {
        "D1": ("changed_silently", "yes"),
        "D2": ("reported", "yes"),
        "A1": ("not_mentioned", "no"),
    }
    assert [e.split(":")[0] for e in errors] == [f"line {n}" for n in (5, 6, 7, 8, 9, 10, 11)]
    assert "second line for D1" in errors[0] and "unknown x_item 'D9'" in errors[1]
    assert "unknown y_lines ['L0003']" in errors[4]


def test_parse_answers_keeps_but_reports_a_change_with_no_description() -> None:
    got, errors = F.parse_answers(_line("D1", "changed_openly", what_changed=" "), ["D1"], 3)
    assert got["D1"].fate == "changed_openly"
    assert errors == ["line 1: D1 is changed_openly with empty what_changed (kept)"]


def _answers(text: str) -> dict[str, F.Answer]:
    return F.parse_answers(text, ["D1", "D2", "A1"], 3)[0]


def test_disputed_and_final_fate() -> None:
    a = _answers(
        "\n".join([_line("D1", "reported"), _line("D2", "reported"), _line("A1", "reported")])
    )
    b = _answers("\n".join([_line("D1", "reported"), _line("D2", "not_mentioned", "no")]))
    assert F.disputed(["D1", "D2", "A1"], a, b) == ["D2", "A1"]  # A1: reader b left it out
    assert F.final("x", "x", "y") == "x" and F.final("x", "y", "z") == "z"
    assert F.final("x", "y", None) == F.UNCLEAR and F.final(None, None, None) == F.UNCLEAR
    pair = K.Pair(x="XX1000a", y="XX1000b")
    c = _answers(_line("D2", "changed_openly", "no"))
    rows = {r["x_item"]: r for r in F.item_rows(pair, ["D1", "D2", "A1"], {"a": a, "b": b, "c": c})}
    assert (rows["D1"]["fate"], rows["D1"]["needed_followup"]) == ("reported", "yes")
    assert (rows["D2"]["fate"], rows["D2"]["needed_followup"]) == ("changed_openly", "no")
    assert (rows["A1"]["fate"], rows["A1"]["fate_b"]) == (F.UNCLEAR, None)
    assert rows["A1"]["item_kind"] == "action" and rows["D2"]["position"] == "ab"
    assert rows["D2"]["what_changed"] == {"c": "pastry: absent"}


def _row(pair: str, item: str, a: str, b: str, c: str | None = None, na: str = "yes",
         nb: str = "yes") -> dict[str, object]:  # fmt: skip
    p = K.Pair(x=pair[:7], y=pair[:6] + chr(ord(pair[6]) + 1))
    return {
        "pair": p.name,
        "series": p.series,
        "position": p.kind,
        "x_item": item,
        "item_kind": F.item_kind(item),
        "fate_a": a,
        "fate_b": b,
        "fate_c": c,
        "needed_a": na,
        "needed_b": nb,
        "needed_c": None,
        "fate": F.final(a, b, c),
        "needed_followup": F.final(na, nb, None),
    }


def _rows() -> list[dict[str, object]]:
    return [
        _row("ES2002a", "D1", "reported", "reported"),
        _row("ES2002a", "A1", "changed_silently", "changed_silently"),
        _row("ES2002a", "A2", "not_mentioned", "reported", "not_mentioned"),
        _row("ES2002b", "D1", "not_mentioned", "not_mentioned", na="no", nb="no"),
        _row("ES2004c", "D1", "changed_silently", "changed_openly", "changed_silently"),
        _row("ES2004c", "D2", "changed_silently", "changed_silently"),
        _row("ES2004c", "A1", "reported", "not_expected", na="yes", nb="no"),
        _row("IS1000a", "A1", "reported", "reported"),
    ]


def test_score_distributions_pairs_agreement_and_detector_recall() -> None:
    rows = _rows()
    flagged = {"ES2002a__ES2002b": ["A1", "D1"], "ES2004c__ES2004d": []}
    s = F.score(rows, 42, flagged)
    assert s == F.score(rows, 42, flagged)  # seeded
    assert (s["items"], s["pairs"], s["series"]) == (8, 4, 3)
    assert s["fate_resolved_by"] == {"agree": 5, "tiebreak": 2, "unclear": 1}
    assert s["needed_followup"] == {"no": 1, "unclear": 1, "yes": 6}
    fates = s["all_items"]["overall"]["fates"]
    assert {f: r["num"] for f, r in fates.items()} == {
        "reported": 2, "returned_unchanged": 0, "changed_openly": 0, "changed_silently": 3,
        "not_mentioned": 2, "not_expected": 0, "unclear": 1,
    }  # fmt: skip
    assert fates["changed_silently"]["den"] == 8 and fates["changed_silently"]["value"] == 0.375
    lo, hi = fates["changed_silently"]["ci"]
    assert 0 <= lo <= 0.375 <= hi <= 1
    assert s["all_items"]["by_item_kind"]["action"]["items"] == 4
    assert s["all_items"]["by_position"]["cd"]["fates"]["changed_silently"]["num"] == 2
    assert s["all_items"]["by_position"]["bc"]["items"] == 1
    needed = s["needed_followup_yes"]["overall"]
    assert needed["items"] == 6 and needed["fates"]["not_mentioned"]["num"] == 1
    assert F.pair_rows(rows)[:2] == [
        {"pair": "ES2002a__ES2002b", "series": "ES2002", "position": "ab", "items": 3,
         "changed_silently": 1, "not_mentioned_needed": 1, "silent_any": 2},
        {"pair": "ES2002b__ES2002c", "series": "ES2002", "position": "bc", "items": 1,
         "changed_silently": 0, "not_mentioned_needed": 0, "silent_any": 0},
    ]  # fmt: skip
    silent = s["silent_per_pair"]
    assert silent["changed_silently"]["pairs_with_at_least_1"]["num"] == 2
    assert silent["changed_silently"]["pairs_with_at_least_2"]["num"] == 1
    assert silent["not_mentioned_needed"]["pairs_with_at_least_1"]["num"] == 1
    assert (silent["silent_any"]["items"], silent["silent_any"]["per_pair_mean"]) == (4, 1.0)
    assert silent["silent_any"]["pairs_with_at_least_2"] | {"ci": None} == {
        "num": 2, "den": 4, "value": 0.5, "ci": None,
    }  # fmt: skip
    assert s["silent_per_pair_by_position"]["cd"]["silent_any"]["items"] == 2
    agree = s["reader_agreement"]["fate"]
    assert (agree["agreement"]["num"], agree["agreement"]["den"]) == (5, 8)
    assert agree["confusion"]["changed_silently|changed_openly"] == 1
    assert s["reader_agreement"]["needed_followup"]["agreement"]["num"] == 7
    det = s["detector_recall"]
    assert det["pairs"] == ["ES2002a__ES2002b", "ES2004c__ES2004d"]
    assert (det["recall"]["num"], det["recall"]["den"]) == (1, 3)
    assert {"pair": "ES2002a__ES2002b", "x_item": "D1", "fate": "reported"} in det["detector_lines"]
    assert F.score(rows, 42)["detector_recall"] is None


def test_kappa() -> None:
    assert F.kappa([]) is None and F.kappa([("x", "x")]) is None  # expected agreement 1
    assert F.kappa([("x", "x"), ("y", "y")]) == 1.0
    assert F.kappa([("x", "y"), ("y", "x")]) == -1.0
    # po = 0.7, pe = 0.5 * 0.6 + 0.5 * 0.4 = 0.5
    labels = [("x", "x")] * 4 + [("x", "y")] + [("y", "x")] * 2 + [("y", "y")] * 3
    assert abs((F.kappa(labels) or 0) - 0.4) < 1e-9
    rows = _rows()
    assert F.agreement(rows, "fate", 42)["kappa"] == F.agreement(rows, "fate", 42)["kappa"]
