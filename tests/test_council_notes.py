"""Council notes: gold claims from Legistar JSON, the recording-ID file, parsing the judge,
combining passes and the summary (no network)."""

from __future__ import annotations

import json
from pathlib import Path

from tenor.evals import council_notes as C
from tenor.evals.list_check import Line
from tenor.evals.notes_completeness import split_notes

RTF = (
    "{\\rtf1\\ansi\\ansicpg1252\\deff0{\\fonttbl{\\f0\\fnil\\fcharset0 Arial;}}\r\n"
    "\\viewkind4\\uc1\\pard\\fs24 Motion was made by Councilmember Nelson to postpone.\\par\r\n}"
)


def _legistar(tmp: Path, votes: list[tuple[str, str]]) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    item = {
        "EventItemMatterFile": "CB 1",
        "EventItemMatterType": "Ordinance (Ord)",
        "EventItemTitle": "An ordinance relating to land use; adding a definition; and more",
        "EventItemActionName": "passed as amended",
        "EventItemActionText": "The Council Bill (CB) was passed as amended by the following vote,"
        " and the President signed the Council Bill (CB):",
        "EventItemPassedFlagName": "Pass",
        "EventItemMinutesNote": RTF,
    }
    vs = [{"VotePersonName": n, "VoteValueName": v} for n, v in votes]
    meta = {"url": "u", "fetched_utc": "t"}
    (tmp / "event_item.json").write_text(json.dumps({**meta, "data": item}))
    (tmp / "votes.json").write_text(json.dumps({**meta, "data": vs}))
    return tmp


def test_gold_claims_split_vote(tmp_path: Path) -> None:
    d = _legistar(tmp_path / "s", [("A B", "In Favor"), ("C D", "Opposed"), ("E F", "In Favor")])
    g = C.gold_claims(d)
    assert [x.id for x in g] == ["G1", "G2", "G3"]
    assert (
        g[0].text
        == "CB 1 (Ordinance (Ord)): An ordinance relating to land use; adding a definition"
    )
    assert g[1].text == (
        "Outcome: passed as amended. Record: The Council Bill (CB) was passed as amended."
    )
    assert g[1].provenance["minutes_note"] == "Motion was made by Councilmember Nelson to postpone."
    assert g[2].text == "Vote: 2 In Favor, 1 Opposed. In Favor: A B, E F; Opposed: C D."
    assert g[2].provenance["split_vote"] is True


def test_gold_claims_unanimous(tmp_path: Path) -> None:
    d = _legistar(tmp_path / "s", [("A B", "In Favor"), ("C D", "In Favor")])
    assert C.gold_claims(d)[2].text == "Vote: 2 In Favor (unanimous). In Favor: A B, C D."


def test_read_recording_ids(tmp_path: Path) -> None:
    p = tmp_path / "ids.txt"
    p.write_text("# header\nseg_1 granola=g1 wispr=w1  # note\n\nseg_2 granola=g2\n")
    assert C.read_recording_ids(p) == {
        "seg_1": {"granola": "g1", "wispr": "w1"},
        "seg_2": {"granola": "g2"},
    }


def _unit(tmp_path: Path) -> C.Unit:
    gold = C.gold_claims(_legistar(tmp_path / "s", [("A B", "In Favor")]))
    lines = [Line(0, "Them", "00:00", "The bill passes nine in favor none opposed today")]
    return C.Unit("seg_1", "granola", split_notes("- CB 1 passed\n- Other"), gold, lines)


def _out(cov: str, contra: str = "no") -> C.JudgeOut:
    return C.JudgeOut.model_validate(
        {
            "outcome_bullets": [
                {"id": "B1", "gold": ["G2", "G9"], "contradicts": contra, "reason": "r"},
                {"id": "B7", "gold": [], "contradicts": "no", "reason": "r"},
            ],
            "gold": [
                {"id": "G1", "coverage": "full", "bullets": ["B1"],
                 "in_product_transcript": "yes", "quote": "x", "reason": "r"},
                {"id": "G2", "coverage": cov, "bullets": ["B1"] if cov != "none" else [],
                 "in_product_transcript": "yes", "quote": "nine in favor none opposed",
                 "reason": "r"},
            ],
        }
    )  # fmt: skip


def test_parse_repairs_and_quotes(tmp_path: Path) -> None:
    p = C.parse(_out("partial"), _unit(tmp_path))
    g = {x["id"]: x for x in p["gold_verdicts"]}
    assert g["G1"]["heard"] == "not_assessed" and g["G1"]["quote"] == ""
    assert g["G2"]["heard"] == "yes" and g["G2"]["quote_found"] is True
    assert g["G3"]["coverage"] == "none"
    assert [b["id"] for b in p["outcome_bullets"]] == ["B1"]
    assert p["outcome_bullets"][0]["gold"] == ["G2"]
    assert "gold G3 missing" in p["repairs"] and "bullet B7: unknown or repeated" in p["repairs"]


def test_combine_and_summarize(tmp_path: Path) -> None:
    u = _unit(tmp_path)
    passes = []
    for k, (cov, contra) in enumerate([("full", "yes"), ("partial", "yes"), ("none", "no")], 1):
        rec = {
            "segment": u.segment,
            "product": u.product,
            "pass": k,
            "bullets": [{"id": "B1", "text": "CB 1 passed"}],
            **C.parse(_out(cov, contra), u),
        }
        rec["scores"] = C.score_pass(rec)
        passes.append(rec)
    r = C.combine(passes)
    assert r["coverage"] == {"item": "full", "outcome": "partial", "tally": "none"}
    assert r["coverage_per_pass"]["outcome"] == ["full", "partial", "none"]
    assert r["heard"]["outcome"] == "yes"
    assert r["contradicting_bullets"] == ["B1"]
    s = C.summarize([r])["granola"]
    assert s["outcome"]["partial"] == 1 and s["segments_with_contradiction"] == 1
    assert C.segment_rows([r])[0]["heard"]["item"] == "n/a"
    assert "nine in favor none opposed" in C.private_texts([r])


def test_judge_parts_shared_record(tmp_path: Path) -> None:
    shared, product, marker = C.judge_parts(_unit(tmp_path), 2)
    assert shared.startswith("Gold items (official record):\nG1 (item). CB 1")
    assert "<product_transcript>" in product and "B1 [section: (top)] CB 1 passed" in product
    assert marker == "\n\nJudging pass 2."
