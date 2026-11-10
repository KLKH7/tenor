"""Heard twice: the value normaliser, the alignment and the two-hearings classes (no network)."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import pytest

from tenor.evals import heard_twice as H
from tenor.evals.speaker_naming import ProdLine


def values(text: str) -> list[H.Value]:
    toks = H.clean_words(text)
    return H.extract([t for t, _ in toks], [b for _, b in toks])


def labels(text: str) -> list[tuple[str, str]]:
    return [(v.kind, v.shown) for v in values(text) if not v.excluded]


# -- words ------------------------------------------------------------------------------------


def test_clean_words_keeps_values_whole_and_marks_breaks() -> None:
    assert H.clean_words("It's €12.50, uh, twenty-five percent; don't!") == [
        ("it's", False),
        ("€12.50", True),
        ("twenty", False),
        ("five", False),
        ("percent", True),
        ("don't", True),
    ]
    assert [w for w, _ in H.clean_words("press F5 or 1p at 3:30 on the 1st")] == [
        "press", "f", "5", "or", "1", "p", "at", "3:30", "on", "the", "1st",
    ]  # fmt: skip


# -- the normaliser ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("twenty five", [("number", "25")]),
        ("twenty-five", [("number", "25")]),
        ("25", [("number", "25")]),
        ("a hundred and fifty", [("number", "150")]),
        ("two thousand five", [("number", "2005")]),
        ("three hundred thousand", [("number", "300000")]),
        ("fifty million", [("number", "50000000")]),
        ("50,000,000", [("number", "50000000")]),
        ("2 million", [("number", "2000000")]),
        ("two point five", [("number", "2.5")]),
        ("twelve fifty", [("number", "12.5|1250")]),
        ("nineteen ninety nine", [("number", "19.99|1999")]),
        ("twelve and a half", [("number", "12.5")]),
        ("two three", [("number", "2"), ("number", "3")]),
        ("seven eight nine ten eleven", [("number", str(n)) for n in (7, 8, 9, 10, 11)]),
        ("twenty, five", [("number", "20"), ("number", "5")]),
        ("fifteen to twenty five", [("number", "15"), ("number", "25")]),
        ("5-10", [("number", "5"), ("number", "10")]),
    ],
)
def test_cardinals(text: str, want: list[tuple[str, str]]) -> None:
    assert labels(text) == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("the third option", [("ordinal", "3 ord")]),
        ("twenty first", [("ordinal", "21 ord")]),
        ("the 19th", [("ordinal", "19 ord")]),
        ("twenty five Euros", [("money", "25 euro")]),
        ("€25", [("money", "25 euro")]),
        ("EUR 50,000,000", [("money", "50000000 euro")]),
        ("€50 million", [("money", "50000000 euro")]),
        ("twelve and a half euros", [("money", "12.5 euro")]),
        ("twelve Euros fifty", [("money", "12.5 euro")]),
        ("twelve euro and fifty cents", [("money", "12.5 euro")]),
        ("€12.50", [("money", "12.5 euro")]),
        ("fifty cents", [("money", "50 cent")]),
        ("eighty percent", [("percent", "80 %")]),
        ("80%", [("percent", "80 %")]),
        ("ten per cent", [("percent", "10 %")]),
        ("thirty minutes", [("duration", "30 minute")]),
        ("two weeks", [("duration", "2 week")]),
        ("one sec", [("duration", "1 second")]),
        ("two o'clock", [("time", "2 clock")]),
        ("11:00", [("time", "11 clock")]),
        ("12:13", [("number", "12"), ("number", "13")]),
        ("next Tuesday in March", [("date", "tuesday"), ("date", "march")]),
        ("we may go tomorrow", [("date", "tomorrow")]),
    ],
)
def test_units_and_dates(text: str, want: list[tuple[str, str]]) -> None:
    assert labels(text) == want


def test_negations() -> None:
    assert [v.shown for v in values("No, I don't. We cannot, never without it; not now")] == [
        "no", "not", "not", "never", "without", "not",
    ]  # fmt: skip
    assert labels("I know nothing") == []


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("this one is good", "pronoun_one"),
        ("the other one", "pronoun_one"),
        ("one of them", "pronoun_one"),
        ("just a second", "second_time_unit"),
        ("first of all", "first_discourse"),
        ("Speaker 2 agreed", "speaker_label"),
    ],
)
def test_figures_of_speech_are_flagged_not_counted(text: str, reason: str) -> None:
    assert [v.excluded for v in values(text)] == [reason]
    assert labels(text) == []


def test_counted_uses_of_one_second_first() -> None:
    assert labels("one remote") == [("number", "1")]
    assert labels("the second remote") == [("ordinal", "2 ord")]
    assert labels("the first design") == [("ordinal", "1 ord")]
    assert labels("one euro") == [("money", "1 euro")]


def test_restart_is_excluded_once() -> None:
    got = values("twelve twelve and a half euros")
    assert [(v.shown, v.excluded) for v in got] == [("12", "restart"), ("12.5 euro", "")]
    got = values("eight eight million units")
    assert [(v.shown, v.excluded) for v in got] == [("8", "restart"), ("8000000", "")]


def test_same_value_across_writings() -> None:
    def one(text: str) -> H.Value:
        (v,) = values(text)
        return v

    assert H.same(one("twelve fifty"), one("12.50"))
    assert H.same(one("twelve fifty"), one("1250"))
    assert H.same(one("twenty five Euros"), one("€25"))
    assert not H.same(one("twenty five Euros"), one("25"))
    assert H.same_number(one("twenty five Euros"), one("25"))
    assert H.same(one("five"), one("5:00"))
    assert H.same(one("don't"), one("not"))
    assert not H.same(one("no"), one("not"))
    assert not H.same(one("fifteen"), one("50"))
    assert one("a hundred").vals == frozenset({str(Fraction(100))})


# -- streams ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class W:
    agent: str
    start: float
    text: str
    punc: bool = False
    trunc: bool = False


def ami(*turns: tuple[str, str], step: float = 0.5) -> list[W]:
    out: list[W] = []
    t = 0.0
    for agent, text in turns:
        for raw in text.split():
            if raw in ",.":
                out.append(W(agent, t, raw, punc=True))
            else:
                out.append(W(agent, t, raw))
                t += step
    return out


def product(*lines: str, offset: float = 100.0, step: float = 0.5) -> H.Stream:
    out = []
    t = offset
    for i, text in enumerate(lines):
        n = len(text.split())
        out.append(ProdLine(i, "Them", t, t + n * step, text))
        t += n * step
    return H.product_stream(out)


def test_human_values_join_a_number_across_another_speakers_word() -> None:
    words = [
        W("A", 0.0, "costs"), W("A", 0.4, "twenty"), W("B", 0.5, "yeah"), W("A", 0.8, "five"),
        W("A", 1.2, "Euros"), W("A", 1.3, ".", punc=True), W("B", 1.6, "Okay"),
    ]  # fmt: skip
    stream = H.human_stream(words)
    assert stream.words == ("costs", "twenty", "yeah", "five", "euros", "okay")
    (v,) = H.human_values(stream)
    assert (v.kind, v.shown, v.start, v.end) == ("money", "25 euro", 1, 5)


def test_human_punctuation_breaks_a_number() -> None:
    stream = H.human_stream(ami(("A", "twenty , five")))
    assert [v.shown for v in H.human_values(stream)] == ["20", "5"]


FILL = "so the plan for the remote control is quite simple and we all agree on the basics"


def hear(said: str, *heard: str) -> tuple[list[H.Value], list[list[H.Hearing]]]:
    human = H.human_stream(ami(("A", f"{FILL} {said} {FILL}")))
    vals = H.human_values(human)
    out = []
    for text in heard:
        st = product(f"{FILL} {text} {FILL}")
        pairs = H.anchors(human, st)
        out.append(H.classify(vals, H.product_values(st), pairs, len(st.words)))
    return vals, out


def test_classify_correct_different_missing() -> None:
    vals, (a, b, c) = hear(
        "it costs fifteen Euros", "it costs €15", "it costs 50 euros", "it costs a lot"
    )
    assert [v.shown for v in vals] == ["15 euro"]
    assert (a[0].outcome, a[0].heard) == ("correct", "15 euro")
    assert (b[0].outcome, b[0].heard, b[0].number_same) == ("different", "50 euro", False)
    assert (c[0].outcome, c[0].heard) == ("missing", "")


def test_classify_unit_only_difference_and_negation() -> None:
    vals, (a, b) = hear("we don't have five minutes", "we do not have five", "we do have 5 minutes")
    assert [v.shown for v in vals] == ["not", "5 minute"]
    assert [h.outcome for h in a] == ["correct", "different"]
    assert a[1].number_same
    assert [h.outcome for h in b] == ["missing", "correct"]


def test_classify_two_numbers_do_not_steal_each_other() -> None:
    _, (a,) = hear("between two or three buttons", "between two or three buttons")
    assert [h.outcome for h in a] == ["correct", "correct"]
    _, (a,) = hear("between two or three buttons", "between to or three buttons")
    assert [h.outcome for h in a] == ["missing", "correct"]


def test_classify_pair_written_as_two_numbers_or_a_clock() -> None:
    _, (a, b) = hear("until eleven twenty five today", "until 11:25 today", "until 1125 today")
    assert [h.outcome for h in a] == ["correct", "correct"]
    assert [h.outcome for h in b] == ["correct", "correct"]


def test_pronoun_one_claims_its_word() -> None:
    # the human "this one" is not scored, and its "one" is not a wrong hearing of "fifteen"
    vals, (a,) = hear("this one costs fifteen", "this one costs")
    assert [(v.shown, v.excluded) for v in vals] == [("1", "pronoun_one"), ("15", "")]
    assert [h.outcome for h in a] == ["correct", "missing"]


# -- two hearings and the summary -------------------------------------------------------------


def h(outcome: str, heard: str = "") -> H.Hearing:
    return H.Hearing(outcome, heard, heard, 0)


def test_two_hearings_classes() -> None:
    right, miss = h("correct", "15"), h("missing")
    assert H.two_hearings(right, right) == "agree_right"
    assert H.two_hearings(h("different", "50"), h("different", "50")) == "agree_wrong"
    assert H.two_hearings(right, h("different", "50")) == "disagree_one_right"
    assert H.two_hearings(h("different", "5"), h("different", "50")) == "disagree_both_wrong"
    assert H.two_hearings(right, miss) == "missing"
    assert not H.flagged("missing", right, miss, broad=False)
    assert H.flagged("missing", right, miss, broad=True)
    assert not H.flagged("missing", miss, miss, broad=True)
    assert H.flagged("disagree_one_right", right, h("different", "50"), broad=False)
    assert not H.flagged("agree_wrong", right, right, broad=True)


def row(meeting: str, g: str, w: str, kind: str = "number", excluded: str = "") -> dict:
    def side(code: str) -> dict:
        outcome = {"c": "correct", "m": "missing"}.get(code, "different")
        heard = "" if code == "m" else "15" if code == "c" else code
        return {"outcome": outcome, "heard": heard, "number_same": False}

    a, b = (
        H.Hearing(side(g)["outcome"], side(g)["heard"], "", 0),
        H.Hearing(side(w)["outcome"], side(w)["heard"], "", 0),
    )
    cls = H.two_hearings(a, b)
    return {
        "id": f"{meeting}:{g}{w}",
        "meeting": meeting,
        "kind": kind,
        "excluded": excluded,
        "granola": side(g),
        "wispr": side(w),
        "class": cls,
        "flag_strict": H.flagged(cls, a, b, broad=False),
        "flag_broad": H.flagged(cls, a, b, broad=True),
    }


def test_summarise_counts_rates_and_stop_rules() -> None:
    rows = (
        [row("M1", "c", "c") for _ in range(6)]
        + [row("M1", "50", "50"), row("M1", "c", "50"), row("M1", "5", "50")]
        + [row("M2", "c", "m"), row("M2", "m", "m"), row("M2", "c", "c", "negation")]
        + [row("M2", "50", "c", excluded="pronoun_one")]
    )
    s = H.summarise(rows, ["M1", "M2"], seed=42)
    p = s["pooled"]
    assert p["n"] == 12 and s["excluded"] == {"pronoun_one": 1}
    assert s["counts"]["M1"]["number"] == 9 and s["counts"]["M2"]["negation"] == 1
    assert p["classes"] == {
        "agree_right": 7, "agree_wrong": 1, "disagree_one_right": 1, "disagree_both_wrong": 1,
        "missing": 2,
    }  # fmt: skip
    assert (p["agreed_wrong"]["num"], p["agreed_wrong"]["den"]) == (1, 8)
    # wrong values: 2 (agreed) + 1 + 2 = 5, of which 3 sit on disagreed values
    assert (p["catch_strict"]["num"], p["catch_strict"]["den"]) == (3, 5)
    # wrong or missing: 5 + 1 + 2 = 8; caught: the 3 above and the lone missing one
    assert (p["catch_broad"]["num"], p["catch_broad"]["den"]) == (4, 8)
    assert p["disagreed_hearing_right"]["rate"] == 0.25
    assert p["flag_strict_per_meeting"]["by_meeting"] == {"M1": 2, "M2": 0}
    assert p["flag_strict_per_meeting"]["mean"] == 1.0
    assert p["granola"]["correct"] == 9 and p["wispr"]["missing"] == 2
    rules = s["stop_rules"]
    assert rules["agreed_wrong"]["stop"] is True  # 12.5% > 3%
    assert rules["catch"]["stop"] is False and rules["catch"]["stop_broad"] is False
    assert rules["disagreed_per_meeting"]["stop"] is False
    assert rules["noise_share"]["stop"] is True  # 2 of 12 marked
    assert s["pooled_no_negation"]["n"] == 11
    assert H.summarise(rows, ["M1", "M2"], seed=42) == s


def test_examples_are_seeded_and_fill_from_one_sided_values() -> None:
    rows = [row("M1", "c", "50"), row("M1", "5", "50"), row("M2", "c", "m"), row("M2", "c", "c")]
    got = H.examples(rows, 3, seed=42)
    assert [r["class"] for r in got] == ["disagree_both_wrong", "disagree_one_right", "missing"]
    assert H.examples(rows, 3, seed=42) == got
    assert len(H.examples(rows, 1, seed=42)) == 1


# -- note-level check -------------------------------------------------------------------------


def test_note_values_match_at_the_moment_elsewhere_or_absent() -> None:
    human = H.human_stream(
        ami(("A", f"{FILL} it costs twenty five Euros {FILL} {FILL} {FILL} in two weeks {FILL}"))
    )
    own = product(f"{FILL} it costs 25 euros", f"{FILL} {FILL} {FILL}", f"in two weeks {FILL}")
    pairs = H.anchors(human, own)
    rows = H.note_values(
        "M1",
        "granola",
        [("B1", "Price: €25, not final"), ("B2", "Due in 2 weeks; 40% margin"), ("B3", "x 25")],
        {"B1": [(0, 0)], "B2": [(0, 0)], "B3": []},
        human,
        H.human_values(human),
        own,
        H.product_values(own),
        pairs,
    )
    got = {(r["bullet"], r["text"]): r["match"] for r in rows}
    assert got == {
        ("B1", "€25"): "at_moment",
        ("B1", "not"): "not_matched",
        ("B2", "2 weeks"): "elsewhere",
        ("B2", "40%"): "absent",
        ("B3", "25"): "no_moment",
    }
    s = H.note_summary(rows)["granola"]
    assert s["values"] == 5 and s["matched"] == 4 and s["elsewhere_same_unit"] == 1
    assert s["match"] == {"at_moment": 1, "elsewhere": 1, "absent": 1, "no_moment": 1}


# -- truth-free mode --------------------------------------------------------------------------


def pair(a_text: str, b_text: str, name: str = "R1") -> list[dict]:
    a = product(f"{FILL} {a_text} {FILL}", offset=0.0)
    b = product(f"{FILL} {b_text} {FILL}", offset=30.0)
    rows, n_anchor = H.pair_rows(name, a, b)
    assert n_anchor >= 2 * len(FILL.split())
    return rows


def outcomes(rows: list[dict]) -> list[tuple[str, str | None, str | None]]:
    return [
        (r["outcome"], r["a"] and r["a"]["value"], r["b"] and r["b"]["value"])
        for r in rows
        if r["counted"]
    ]


def test_pair_rows_equal_different_and_one_sided() -> None:
    rows = pair(
        "it costs fifteen Euros and we don't ship on Tuesday",
        "it costs €50 and we ship on Tuesday, maybe twice",
    )
    assert outcomes(rows) == [
        ("different", "15 euro", "50 euro"),
        ("only_a", "not", None),
        ("equal", "tuesday", "tuesday"),
    ]
    assert rows[0]["mark"] == "number"
    assert rows[0]["a"]["time_s"] < rows[0]["b"]["time_s"]  # each hearing's own clock
    assert [r["id"] for r in rows] == ["R1:p0000", "R1:p0001", "R1:p0002"]
    rows = pair("we have five minutes", "we have five dollars and no time")
    assert outcomes(rows) == [("different", "5 minute", "5 dollar"), ("only_b", None, "no")]
    assert rows[0]["mark"] == "unit_only"
    assert pair("well no bad", "well not bad")[0]["mark"] == "negation_word"
    assert pair("next Thursday then", "next Tuesday then")[0]["mark"] == "date"


def test_pair_rows_ways_of_writing_are_not_marks() -> None:
    # a digit string, a cardinal against an ordinal, a number written in pieces
    assert outcomes(pair("council bill one two one two eighty three", "council bill 121283")) == [
        ("equal", "121283", "121283")
    ]
    assert outcomes(pair("until September 19 then", "until September 19th then")) == [
        ("equal", "september", "september"),
        ("equal", "19", "19 ord"),
    ]
    assert outcomes(pair("project number 3018 378 type", "project number 3018378 type")) == [
        ("equal", "3018", "3018378")
    ]
    assert outcomes(pair("project number 3018378 type", "project number 3018 378 type")) == [
        ("equal", "3018378", "3018")
    ]
    # ... but a dropped digit is
    assert outcomes(pair("council bill one two one two eight", "council bill 121282")) == [
        ("different", "12128", "121282")
    ]


def test_digit_strings_need_three_number_words_and_no_punctuation() -> None:
    def joined(text: str) -> list[str]:
        _, vals = H.text_values(text)
        return [v.shown for v in vals]

    assert joined("resolution three two one four two passed") == ["32142"]
    assert joined("bill one two one one ninety") == ["121190"]
    assert joined("two or three") == ["2", "3"]
    assert joined("five six") == ["5", "6"]
    assert joined("one, two, three") == ["1", "2", "3"]
    assert joined("ten eleven twelve") == ["10", "11", "12"]  # fewer than two single digits
    assert joined("call 1 2 3") == ["1", "2", "3"]  # written digits are left alone


def test_pair_rows_figure_of_speech_is_not_counted() -> None:
    rows = pair("this one is fine", "this 1 is fine")
    assert [(r["outcome"], r["counted"], r["excluded"]) for r in rows] == [
        ("equal", False, "pronoun_one")
    ]
    assert H.pair_counts(rows)["not_counted"] == 1 and H.pair_counts(rows)["equal"] == 0


def test_pair_summary_counts_and_rates() -> None:
    rows = pair("it costs fifteen Euros and not more, about five minutes", "it costs €50", "R1")
    rows += pair("we have two weeks", "we have two weeks", "R2")
    s = H.pair_summary(rows, {"R1": 300.0, "R2": 900.0}, seed=42)
    r1, r2, pooled = s["recordings"]["R1"], s["recordings"]["R2"], s["pooled"]
    assert (r1["values_a"], r1["values_b"], r1["equal"], r1["different"]) == (3, 1, 0, 1)
    assert (r1["only_a"], r1["only_b"], r1["marks"]) == (2, 0, {"number": 1})
    assert r1["marks_per_10min"] == 2.0 and r2["marks_per_10min"] == 0.0
    assert r1["by_kind"]["money"] == {"equal": 0, "different": 1, "only_a": 0, "only_b": 0}
    assert (pooled["equal"], pooled["different"], pooled["recordings"]) == (1, 1, 2)
    assert pooled["marks_per_10min"]["rate"] == 0.5
    assert pooled["marks_per_recording"]["mean"] == 0.5
    assert H.pair_summary(rows, {"R1": 300.0, "R2": 900.0}, seed=42) == s


def test_validate_pairs_links_truth_classes_to_pair_rows() -> None:
    said = "it costs fifteen Euros and five minutes later we sold nine and not ten"
    human = H.human_stream(ami(("A", f"{FILL} {said} {FILL}")))
    vals = H.human_values(human)
    texts = {
        "granola": "it costs fifteen Euros and five minutes later we sold nine and ten",
        "wispr": "it costs fifty Euros and five minutes later we sold and not eleven",
    }
    streams = {p: product(f"{FILL} {t} {FILL}") for p, t in texts.items()}
    hearings = {
        p: H.classify(vals, H.product_values(st), H.anchors(human, st), len(st.words))
        for p, st in streams.items()
    }
    truth = H.truth_links(vals, hearings)
    assert [t["class"] for t in truth] == [
        "disagree_one_right", "agree_right", "missing", "missing", "disagree_one_right",
    ]  # fmt: skip
    rows, _ = H.pair_rows("M1", streams["granola"], streams["wispr"])
    v = H.validate_pairs(truth, rows)
    assert v["truth"]["disagree_one_right"] == {"different": 2}
    assert v["truth"]["agree_right"] == {"equal": 1}
    assert v["truth"]["missing"] == {"only_a": 1, "only_b": 1}
    assert v["marks"] == {"disagree_one_right": 2}
    # a mark where the human transcript has no value at all
    rows, _ = H.pair_rows(
        "M1", product(f"{FILL} about 20 of them {FILL}"), product(f"{FILL} about 30 of them {FILL}")
    )
    assert H.validate_pairs([], rows)["marks"] == {"no_human_value": 1}


# -- official values --------------------------------------------------------------------------


def test_official_targets_from_a_legistar_item() -> None:
    targets, skipped = H.official_targets(
        "CB 121282",
        "An ordinance amending Ordinance 127362, which adopted the 2026 budget; relating to "
        "Initiative 137 on the November 5, 2024, ballot; all by a 3/4 vote.",
        "The Council Bill (CB) was postponed to September 19, 2024",
        {"In Favor": 6, "Opposed": 3, "Absent": 0},
    )
    assert [(t.label, t.shown) for t in targets] == [
        ("matter number (CB)", "CB 121282"),
        ("votes in favor", "6"),
        ("votes opposed", "3"),
        ("number in title", "ordinance 127362"),
        ("number in title", "2026"),
        ("number in title", "initiative 137"),
        ("date in title", "november 5"),
        ("date in action text", "september 19"),
    ]
    assert skipped == [
        "title: year of the date november 5 2024",
        "title: by a 3 4 vote",
        "title: a 3 4 vote",
    ]  # the action text's 2024 is the title's value again


def status(target: H.Target, text: str) -> tuple[str, list[str]]:
    found = H.find_target(target, *H.text_values(text))
    return found["status"], [o["value"] for o in found["others"]]


def test_find_target_right_wrong_absent() -> None:
    (bill, favor, opposed, ordinance, *_), _ = H.official_targets(
        "CB 121282", "amending Ordinance 127362", "", {"In Favor": 6, "Opposed": 3}
    )
    assert status(bill, "council bill one two one two eight two relating to") == ("right", [])
    assert status(bill, "council bill one two one two eight relating to") == ("wrong", ["12128"])
    assert status(bill, "the bill passes") == ("absent", [])
    # another item's number in the same text is listed, the status stays right
    assert status(bill, "Council Bill 121281 passed. Council Bill 121282 passed.") == (
        "right",
        ["121281"],
    )
    assert status(favor, "six in favor, three opposed") == ("right", [])
    assert status(favor, "Passed 6-3.") == ("right", [])
    far = "we met six times last year and then again this spring before the bill passes"
    assert status(favor, far) == ("absent", [])  # no voting word within six words
    assert status(favor, "none in favor, none opposed") == ("wrong", ["none"])
    assert status(favor, "nine in favor") == ("wrong", ["9"])
    assert status(opposed, "six in favor, three opposed") == ("right", [])
    assert status(ordinance, "amending ordinance 127 362 which") == ("right", [])
    assert status(ordinance, "amending ordinance one two seven three two which") == (
        "wrong",
        ["12732"],
    )


def test_find_target_day_needs_its_month() -> None:
    (_, day), _ = H.official_targets("Res 1", "", "postponed to September 19, 2024", {})
    assert status(day, "postponed until September 19th") == ("right", [])
    assert status(day, "19 people spoke in September") == ("right", [])  # within six words
    assert status(day, "there were 19 speakers") == ("absent", [])
    assert status(day, "postponed to September 17") == ("wrong", ["17"])
