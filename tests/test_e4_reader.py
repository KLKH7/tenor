"""E4 reader scoring: the blind candidate pool and key, the reader's JSONL schema, unit checks,
scoring per condition (R's oracle filter, missing gold recovered, bootstrap CIs), and the packet
and score scripts end to end on tiny inputs (no network, no LLM)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import e4_reader_packets as EP
from scripts import e4_reader_score as ES
from scripts import reader_packets as P
from tenor.evals import coverage_e4 as C4
from tenor.evals import e4_reader as R


def _conds() -> dict[str, list[dict[str, str]]]:
    a = {"src": "cov:I1", "text": "Bo sends the slides."}
    b = {"src": "cov:I2", "text": "Use rubber"}
    return {
        C4.CU: [a, b],
        C4.C3: [a, b],
        C4.CUS: [b],
        C4.C3S: [b],
        C4.R: [
            {"src": "regen:R1", "text": "bo sends the slides"},
            {"src": "regen:R2", "text": "Meeting lasted 25 minutes"},
            {"src": "regen:R3", "text": "  "},
        ],
        C4.RD: [{"src": "regen:R1", "text": "bo sends the slides"}],
        C4.Q: [{"src": "chat:1", "text": "Price is open"}],
    }


def _record(product: str = "granola", mid: str = "IS0000a") -> dict[str, Any]:
    conds = _conds()
    if product != "granola":
        del conds[C4.Q]
    return {"meeting_id": mid, "product": product, "conditions": conds, "bullets_sha": "x"}


def test_pool_merges_exact_duplicates_and_is_seeded() -> None:
    items = R.pool(_conds(), 42, "IS0000a:granola")
    assert len(items) == 4  # slides (x4 sources), rubber, 25 minutes, price; blank dropped
    assert [i.id for i in items] == ["K1", "K2", "K3", "K4"]
    slides = next(i for i in items if "slides" in i.text.lower())
    assert slides.text == "Bo sends the slides."  # first text seen, in CONDITIONS order
    assert {s.condition for s in slides.sources} == {C4.C3, C4.CU, C4.R, C4.RD}
    assert R.pool(_conds(), 42, "IS0000a:granola") == items
    orders = {tuple(i.text for i in R.pool(_conds(), s, "IS0000a:granola")) for s in range(20)}
    assert len(orders) > 1
    shown = R.shown(items)
    assert shown[C4.CU] == [slides.id, next(i.id for i in items if i.text == "Use rubber")]
    assert len(shown[C4.R]) == 2


def test_key_and_section_hide_the_conditions() -> None:
    uk = R.key_unit(_record(), 42, ["B1", "B2"], ["D1", "A1"])
    assert uk["conditions"] == list(R.CONDITIONS)
    assert R.key_unit(_record("wispr"), 42, [], [])["conditions"] == [
        c for c in R.CONDITIONS if c != C4.Q
    ]
    text = "\n".join(R.candidates_section(uk["items"]))
    assert "K1. " in text and "K4. " in text
    for leak in ("cov:", "regen:", "chat:", "C3", "CUS", "RD"):
        assert leak not in text


def _line(cid: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        meeting="IS0000a", product="granola", cand_id=cid, kind="action", true="yes",
        in_notes="no", notes_bullets=[], gold_match=[], evidence_quote="T0001 \"x\"",
        reason="r", confidence="high",
    )  # fmt: skip
    base.update(kw)
    return base


def test_reader_line_schema() -> None:
    ok, errs = R.parse_lines([json.dumps(_line("K1")), "", "{not json"])
    assert len(ok) == 1 and len(errs) == 1 and errs[0].startswith("line 3")
    bad = [
        _line("K1", in_notes="yes"),  # no bullets
        _line("K1", notes_bullets=["B1"]),  # bullets with in_notes no
        _line("X1"),
        _line("K1", true="maybe"),
        _line("K1", extra=1),
        _line("K1", gold_match=[{"id": "A1", "match": "full"}, {"id": "A1", "match": "partial"}]),
    ]
    ok, errs = R.parse_lines(json.dumps(b) for b in bad)
    assert not ok and len(errs) == len(bad)


def test_check_unit_lists_every_problem() -> None:
    uk = R.key_unit(_record(), 42, ["B1"], ["A1"])
    lines, _ = R.parse_lines(
        json.dumps(x)
        for x in [
            _line("K1"),
            _line("K1"),
            _line("K9"),
            _line("K2", in_notes="yes", notes_bullets=["B7"]),
            _line("K3", gold_match=[{"id": "D9", "match": "full"}]),
        ]
    )
    got, probs = R.check_unit(uk, lines)
    assert set(got) == {"K1", "K2", "K3"}
    assert "item K1 twice" in probs and "unknown item K9" in probs and "item K4 missing" in probs
    assert any("unknown bullets" in p for p in probs) and any("unknown gold" in p for p in probs)


def _verdicts(uk: dict[str, Any]) -> R.Verdicts:
    """Slides: true, new, states missing A1; rubber: duplicate; 25 minutes: other; price:
    false."""
    out: R.Verdicts = {}
    for k, it in uk["items"].items():
        t = it["text"].lower()
        if "slides" in t:
            d = _line(k, gold_match=[{"id": "A1", "match": "full"}])
        elif "rubber" in t:
            d = _line(k, kind="decision", in_notes="yes", notes_bullets=["B1"],
                      gold_match=[{"id": "D1", "match": "full"}])  # fmt: skip
        elif "minutes" in t:
            d = _line(k, kind="other")
        else:
            d = _line(k, kind="problem", true="no")
        out[k] = R.ReaderLine.model_validate(d)
    return out


def test_score_per_condition_with_r_oracle_and_recovered_gold() -> None:
    key = {
        f"{m}:{p}": R.key_unit(_record(p, m), 42, ["B1"], ["D1", "A1", "P1"])
        for m in ("IS0000a", "IS0000b")
        for p in ("granola", "wispr")
    }
    verdicts = {k: _verdicts(u) for k, u in key.items() if k != "IS0000b:wispr"}
    missing = {k: {"A1": "no", "P1": "partial", "D1": "partial"} for k in key}
    res = R.score(key, verdicts, missing, seed=42)
    assert res["units_not_scored"] == ["IS0000b:wispr"]
    s = res["summary"]
    cu = s["granola"][C4.CU]["totals"]  # 2 granola units
    assert cu["n_shown"] == 4 and cu["true_new"] == 2 and cu["duplicates"] == 2
    assert cu["recovered"] == 2 and cu["n_missing"] == 6  # D1 is stated only by a duplicate
    assert cu["recovered_none"] == 2 and cu["n_missing_none"] == 2
    r = s["granola"][C4.R]["totals"]
    assert r["n_shown"] == 2 and r["other_kind"] == 0  # 25 minutes is kind other: filtered
    q = s["granola"][C4.Q]["totals"]
    assert q["n_shown"] == 2 and q["false"] == 2 and q["true_new"] == 0
    assert C4.Q not in s["wispr"]
    both = s["both"][C4.CU]
    assert both["n_units"] == 3 and both["n_meetings"] == 2
    assert both["precision_new"] == 0.5 and both["precision_new_ci"] == (0.5, 0.5)
    assert R.score(key, verdicts, missing, seed=42) == res


def test_missing_gold_from_the_notes_reader() -> None:
    rows = [
        {"meeting": "M", "product": "granola", "gold_id": "D1", "notes_verdict": "full"},
        {"meeting": "M", "product": "granola", "gold_id": "A1", "notes_verdict": "partial"},
        {"meeting": "M", "product": "granola", "gold_id": "P1", "notes_verdict": "no"},
    ]
    assert R.missing_gold(rows) == {"M:granola": {"A1": "partial", "P1": "no"}}


# -- the scripts --------------------------------------------------------------------------------


def _packet_input(product: str = "granola") -> P.PacketInput:
    return P.PacketInput(
        meeting_id="IS0000a",
        product=product,
        gold_version="ami-1.6.2",
        background=["A meeting."],
        gold=[
            P.PacketGold("D1", "decision", "Use rubber.", (0,)),
            P.PacketGold("A1", "action", "Bo sends slides.", (0,)),
            P.PacketGold("P1", "problem", "Price open.", (0,)),
        ],
        reference=[P.Turn(0, "Project Manager", "00:00", "We use rubber.")],
        bullets=[P.PacketBullet("B1", "B1 [section: Decisions] Rubber case agreed")],
        transcript=[P.Turn(0, "Them", "00:00", "Bo will send the slides.")],
    )


def test_packets_and_score_scripts_end_to_end(tmp_path: Path) -> None:
    pi = _packet_input()
    recs = [dict(_record(), bullets_sha=EP.bullets_sha(pi))]
    texts, units = EP.build({"records": recs}, [pi], 42)
    (name, text), = texts.items()  # fmt: skip
    assert name == "IS0000a__granola.md"
    assert text.startswith("# E4 reader packet: IS0000a / Granola")
    assert "## 5. The product's own transcript" in text and text.index("## 6.") > text.index(
        "## 5."
    )
    assert "cov:" not in text and "regen:" not in text
    assert EP.build({"records": recs}, [pi], 42) == (texts, units)
    with pytest.raises(SystemExit):
        EP.build({"records": [dict(recs[0], bullets_sha="other")]}, [pi], 42)

    key_path = tmp_path / "run.key.json"
    key_path.write_text(json.dumps({"meta": {"run_id": "run"}, "units": units}))
    notes = tmp_path / "notes.jsonl"
    rows = (("D1", "full"), ("A1", "no"), ("P1", "no"))
    lines = [
        {"meeting": "IS0000a", "product": "granola", "gold_id": g, "notes_verdict": v}
        for g, v in rows
    ]
    notes.write_text("\n".join(json.dumps(x) for x in lines))
    runs = tmp_path / "runs"
    runs.mkdir()
    vs = _verdicts(units["IS0000a:granola"])
    (runs / name.replace(".md", ".jsonl")).write_text(
        "\n".join(v.model_dump_json() for v in vs.values())
    )
    ES.main(["--key", str(key_path), "--runs", str(runs), "--notes-reader", str(notes)])
    out = json.loads((tmp_path / "run.score.json").read_text())
    assert out["units_scored"] == ["IS0000a:granola"] and not out["meta"]["unit_problems"]
    assert out["summary"]["granola"][C4.C3]["totals"]["recovered"] == 1
