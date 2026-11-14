"""Council follow-through: caption parsing, extractor checks, matching, jobs, scoring."""

from __future__ import annotations

import json

from tenor.evals import council_follow as CF
from tenor.evals import item_fates as F

SRT = """1
00:00:01,000 --> 00:00:03,000
<i>Good afternoon.</i>
The meeting will come to order.

2
00:04:10,500 --> 00:04:14,000
I move to hold the tree ordinance until the twelfth.

3
00:04:15,000 --> 00:04:16,000

4
01:02:03,000 --> 01:02:05,000
We will send it to the committee.
"""


def lines() -> list[CF.Line]:
    return CF.parse_srt(SRT)


def row(**kw: object) -> str:
    base = {
        "kind": "return",
        "text": "The tree ordinance is held until the twelfth.",
        "lines": ["L0001"],
        "quote": "hold the tree ordinance",
        "matter": "",
        "due": "later",
    }
    return json.dumps(base | kw)


def test_parse_srt_joins_cue_text_and_drops_empty_cues() -> None:
    got = lines()
    assert [ln.seconds for ln in got] == [1, 250, 3723]
    assert got[0].text == "Good afternoon. The meeting will come to order."
    assert CF.render(got, 2) == "L0002 [62:03] We will send it to the committee."


def test_parse_extracted_checks_lines_and_quote() -> None:
    text = "\n".join([
        row(),
        row(lines=["L0009"]),
        row(quote="hold the park ordinance"),
        row(quote="I move to HOLD the tree ordinance, until the twelfth!"),
        row(lines=[]),
        "not json",
        row(kind="vote"),
    ])  # fmt: skip
    ok, errors = CF.parse_extracted(text, lines())
    assert len(ok) == 2
    assert [e.split(":")[0] for e in errors] == ["line 2", "line 3", "line 5", "line 6", "line 7"]


def ext(text: str, at: int, kind: str = "return") -> CF.Extracted:
    return CF.Extracted(kind=kind, text=text, lines=(f"L{at:04d}",), quote="x")  # type: ignore[arg-type]


def test_match_items_needs_near_lines_and_shared_words() -> None:
    a = [ext("The tree ordinance is held until the twelfth.", 10),
         ext("Parks will send the committee last year's permit numbers.", 40),
         ext("The clerk will circulate the amended budget table.", 70)]  # fmt: skip
    b = [ext("Parks department to send permit numbers to the committee.", 42, "info_request"),
         ext("Tree ordinance held to the twelfth.", 11),
         ext("The clerk will circulate the amended budget table.", 200)]  # fmt: skip
    pairs, counts = CF.match_items(a, b)
    assert pairs == [(0, 1), (1, 0)]
    assert counts == {"a": 3, "b": 3, "both": 2}
    kept, _ = CF.kept_items(a, b)
    assert [(k.item_id, k.lines, k.kind, k.kind_b) for k in kept] == [
        ("K1", (10, 11), "return", "return"),
        ("K2", (40, 42), "return", "info_request"),
    ]


def test_match_items_uses_each_item_once() -> None:
    a = [ext("The tree ordinance is held until the twelfth.", 10)]
    b = [ext("The tree ordinance is held until the twelfth.", 10),
         ext("Tree ordinance held until the twelfth.", 11)]  # fmt: skip
    assert CF.match_items(a, b)[0] == [(0, 0)]


def test_fate_job_is_stable_and_keeps_the_frozen_prompt_whole() -> None:
    x = y = lines()
    items = [CF.Item(item_id="K1", kind="return", text="Held until the twelfth.", lines=(1,))]
    job = CF.fate_job("m1", "m2", items, x, y, "PROMPT BODY", "out.jsonl")
    assert job == CF.fate_job("m1", "m2", items, x, y, "PROMPT BODY", "out.jsonl")
    assert "PROMPT BODY" in job and "### K1 (return): Held until the twelfth." in job
    assert "* X0001 [4:10] I move to hold" in job and "L0002 [62:03]" in job
    only = CF.fate_job("m1", "m2", items, x, y, "P", "out.jsonl", only=[])
    assert "only the 0 of 1 items" in only


def answers(fate: str, need: str = "yes") -> dict[str, F.Answer]:
    return {"K1": F.Answer(x_item="K1", fate=fate, needed_followup=need, confidence="high")}  # type: ignore[arg-type]


def test_rows_score_with_item_fates_and_resample_pairs() -> None:
    item = [CF.Item(item_id="K1", kind="return", text="t", lines=(1,), matter="CB 1")]
    rows = (
        CF.item_rows("a__b", item, {"a": answers("reported"), "b": answers("reported")})
        + CF.item_rows("b__c", item, {"a": answers("not_mentioned"), "b": answers("reported"),
                                      "c": answers("not_mentioned")})
    )  # fmt: skip
    got = CF.score(rows, seed=42)
    assert got["items"] == 2 and got["pairs"] == 2
    assert got["all_items"]["fates"]["reported"]["num"] == 1
    assert got["silent_per_pair"]["silent_any"]["pairs_with_at_least_1"]["num"] == 1
    assert got["by_kind"]["return"]["items"] == 2
    assert got == CF.score(rows, seed=42)


def test_official_record_matches_file_numbers_in_any_spelling() -> None:
    agenda = [{"EventItemMatterFile": "CB 121214", "EventItemActionName": "passed"},
              {"EventItemMatterFile": None}]  # fmt: skip
    assert CF.official_return("cb121214", agenda) == {"on_agenda": True, "action": "passed"}
    assert CF.official_return("Res 32202", agenda) == {"on_agenda": False, "action": None}
    assert CF.official_return("the budget", agenda)["on_agenda"] is None
    rows = [{"fate": "reported", "on_agenda": True}, {"fate": "not_mentioned", "on_agenda": True},
            {"fate": "not_expected", "on_agenda": False}, {"fate": "unclear", "on_agenda": False},
            {"fate": "reported", "on_agenda": None}]  # fmt: skip
    got = CF.record_agreement(rows)
    assert (got["checked"], got["agree"]) == (4, 2)
    assert got["cells"]["away_on_agenda"] == 1 and got["cells"]["unclear"] == 1
