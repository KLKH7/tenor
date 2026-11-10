"""Meeting ledger: job files (no notes, no gold), attendee list, entry validation and quote
check, reader packets and key, reader consensus and the scorer's maths (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import ledger_agents as A
from scripts import ledger_score as S
from tenor.evals import bullet_cues as B
from tenor.evals import e4_reader as R
from tenor.evals import ledger as L
from tenor.evals.list_check import normalize
from tests.test_bullet_cues import PACKET

ROSTER = (
    "Participants:\n- AMI speaker A: Project Manager\n- AMI speaker B: Industrial Designer\n"
    "- AMI speaker C: Marketing Expert\n\n"
    "Speaker roster (from the meeting's own introductions; products may spell names "
    "differently, so treat close spellings as the same person): Project Manager = Ann Lee; "
    "Industrial Designer = Bo.\n"
)
WITH_ROSTER = PACKET.replace("Team of four.\n", ROSTER)


def _unit(packet: str = PACKET, meeting: str = "IS0000a", split: str = "test") -> B.CueUnit:
    return B.parse_packet(packet, meeting, "granola", split)


def _entry(eid: str, **kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": eid,
        "kind": "decision",
        "status": "agreed",
        "statement": "The team agreed to use rubber for the case.",
        "owner": "",
        "owner_basis": "",
        "quote": [{"line": "T0001", "text": "we use RUBBER for the case"}],
        "history": ["T0001 agreed"],
    }
    return d | kw


def _jsonl(*entries: dict[str, Any]) -> str:
    return "\n".join(json.dumps(e) for e in entries) + "\n"


def _line(k: str, product: str = "granola", meeting: str = "IS0000a", **kw: Any) -> R.ReaderLine:
    d: dict[str, Any] = {
        "meeting": meeting,
        "product": product,
        "cand_id": k,
        "kind": "decision",
        "true": "yes",
        "in_notes": "no",
        "notes_bullets": [],
        "gold_match": [],
        "evidence_quote": "",
        "reason": "",
        "confidence": "high",
    }
    return R.ReaderLine.model_validate(d | kw)


# -- job files --------------------------------------------------------------------------------


def test_attendees_from_roster_and_none() -> None:
    assert L.attendees(PACKET) is None
    assert L.NO_ATTENDEES in L.attendee_block(None)
    people = L.attendees(WITH_ROSTER)
    assert people == [
        ("Ann Lee", "Project Manager"),
        ("Bo", "Industrial Designer"),
        ("", "Marketing Expert"),
    ]
    block = L.attendee_block(people)
    assert "Simulated" in block and "- Ann Lee: Project Manager" in block
    assert "- (name not on the list): Marketing Expert" in block


def test_job_file_has_transcript_and_attendees_but_no_notes_or_gold() -> None:
    job = L.job_file(_unit(WITH_ROSTER), "INSTRUCTIONS", size=4)
    assert "INSTRUCTIONS" in job and "- Bo: Industrial Designer" in job
    assert "### Segment 1 of 2 (T0000-T0003)" in job and "### Segment 2 of 2 (T0004-T0005)" in job
    assert "[T0002 Speaker 2 00:09] Bo, could you send the slides to everyone tomorrow?" in job
    for leak in ("Use rubber.", "will send the slides.", "Rubber case agreed", "L0001", "B2 ["):
        assert leak not in job
    assert L.job_file(_unit(WITH_ROSTER), "INSTRUCTIONS", size=4) == job
    assert L.NO_ATTENDEES in L.job_file(_unit(), "X")


# -- ingest -----------------------------------------------------------------------------------


def test_quote_check_is_case_and_punctuation_insensitive_and_line_bound() -> None:
    lines = {x.index: normalize(x.text) for x in _unit().transcript}
    assert L.quote_ok(L.Quote(line="T0001", text="So we use rubber, for the CASE agreed"), lines)
    assert not L.quote_ok(L.Quote(line="T0000", text="we use rubber"), lines)  # another line
    assert not L.quote_ok(L.Quote(line="T0099", text="we use rubber"), lines)
    assert not L.quote_ok(L.Quote(line="1", text="we use rubber"), lines)
    assert not L.quote_ok(L.Quote(line="T0001", text=" ... "), lines)


def test_ingest_unit_drops_and_counts() -> None:
    text = (
        _jsonl(
            _entry("E1"),
            _entry("E2", quote=[{"line": "T0001", "text": "plastic"}]),  # no valid quote
            _entry(
                "E3",
                kind="action",
                owner="Bo",
                owner_basis="said",
                statement="Bo will send the slides tomorrow.",
                quote=[
                    {"line": "T0003", "text": "I will send the slides tomorrow"},
                    {"line": "T0003", "text": "next week"},
                ],
            ),
            _entry("E1"),  # repeated id
            _entry("E4", status="decided"),  # invalid status
            _entry("E5", quote=[]),
        )
        + "not json\n\n"
    )
    entries, stats, errors = L.ingest_unit(_unit(), text)
    assert [e.id for e in entries] == ["E1", "E3"]
    assert [q.text for q in entries[1].quote] == ["I will send the slides tomorrow"]
    assert stats == {
        "lines": 7,
        "invalid": 2,
        "repeated_id": 1,
        "no_valid_quote": 2,
        "quotes": 4,
        "quotes_bad": 2,
        "kept": 2,
    }
    assert len(errors) == 5


def test_build_packet_is_blind_merges_duplicates_and_keys_entries() -> None:
    u = _unit()
    entries, _, _ = L.ingest_unit(
        u,
        _jsonl(
            _entry("E1"),
            _entry("E2", statement="the team agreed to use rubber for the case"),  # duplicate
            _entry("E3", kind="problem", status="raised", statement="Too little time to test.",
                   quote=[{"line": "T0004", "text": "not have enough time for testing"}]),
        ),
    )  # fmt: skip
    text, key = L.build_packet(u, entries, 42)
    assert L.build_packet(u, entries, 42) == (text, key)
    assert text.startswith("# E4 reader packet: IS0000a / Granola")
    assert "## 6. Candidate items to score" in text and "## 5. The product's own transcript" in text
    assert len(key["items"]) == 2 and key["n_entries"] == 3
    merged = next(v for v in key["items"].values() if v["entry_ids"] == ["E1", "E2"])
    assert merged | {"text": ""} == {
        "text": "",
        "entry_id": "E1",
        "kind": "decision",
        "status": "agreed",
        "owner": "",
        "owner_basis": "",
        "entry_ids": ["E1", "E2"],
    }
    section6 = text.split("## 6. ")[1]
    for private in ("E1", 'agreed"', "raised", "ledger"):
        assert private not in section6
    assert key["gold_ids"] == ["D1", "A1"] and key["bullet_ids"] == ["B1", "B2", "B3"]
    # the E4 reader's own unit check accepts the key
    got, probs = R.check_unit(key, [_line(k) for k in key["items"]])
    assert not probs and set(got) == set(key["items"])


def _readers(tmp_path: Path) -> Path:
    readers = tmp_path / "reader"
    (readers / "packets").mkdir(parents=True)
    (readers / "packets" / "IS1008a__granola.md").write_text(WITH_ROSTER, encoding="utf-8")
    return readers


def test_dump_and_ingest_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    readers = _readers(tmp_path)
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "p.v1.md").write_text("DO IT\n", encoding="utf-8")
    assert A.dump("dev", "p.v1", readers, prompts) == 1
    job = (readers / "ledger_jobs_dev" / "IS1008a__granola.md").read_text(encoding="utf-8")
    assert "DO IT" in job and "Use rubber." not in job
    assert A.dump("test", "p.v1", readers, prompts) == 0  # no TEST packets here

    run = readers / "ledger_agent_runs" / "r1"
    run.mkdir(parents=True)
    (run / "IS1008a__granola.jsonl").write_text(
        _jsonl(_entry("E1"), _entry("E2", quote=[])), encoding="utf-8"
    )
    out = A.ingest("dev", "r1", readers=readers)
    assert out["totals"]["kept"] == 1 and out["totals"]["no_valid_quote"] == 1
    assert out["totals"]["items"] == 1 and out["missing"] == []
    key = json.loads((readers / "ledger_keys" / "r1.key.json").read_text(encoding="utf-8"))
    assert key["meta"]["prompt"] == L.PROMPT and key["meta"]["seed"] == 42
    assert key["units"]["IS1008a:granola"]["items"]["K1"]["entry_id"] == "E1"
    assert (readers / "ledger_packets" / "r1" / "IS1008a__granola.md").exists()
    assert "no_valid_quote" in capsys.readouterr().out


def test_ingest_fails_loudly_when_entries_make_no_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readers = _readers(tmp_path)
    run = readers / "ledger_agent_runs" / "r1"
    run.mkdir(parents=True)
    (run / "IS1008a__granola.jsonl").write_text(_jsonl(_entry("E1")), encoding="utf-8")
    monkeypatch.setattr(R, "pool", lambda *a, **k: [])
    with pytest.raises(SystemExit, match="1 valid entries but 0 packet items"):
        A.ingest("dev", "r1", readers=readers)


def test_ingest_reports_units_without_output(tmp_path: Path) -> None:
    out = A.ingest("dev", "nothing", readers=_readers(tmp_path))
    assert out["missing"] == ["IS1008a:granola"] and out["units"] == 0


# -- consensus --------------------------------------------------------------------------------


def _gm(**kw: str) -> list[dict[str, str]]:
    return [{"id": g, "match": m} for g, m in kw.items()]


def test_dedupe_keeps_the_first_line() -> None:
    by, dropped = L.dedupe([_line("K1", true="no"), _line("K1"), _line("K2")])
    assert dropped == 1 and by["IS0000a:granola"]["K1"].true == "no"
    assert set(by["IS0000a:granola"]) == {"K1", "K2"}


def test_consensus_item_medians_majority_and_gold() -> None:
    v = L.consensus_item(
        [
            _line("K1", true="yes", kind="action", gold_match=_gm(A1="full", D1="partial")),
            _line("K1", true="partly", kind="action", in_notes="partial", notes_bullets=["B1"],
                  gold_match=_gm(A1="partial", P9="full")),
            _line("K1", true="no", kind="decision", in_notes="yes", notes_bullets=["B1"],
                  gold_match=_gm(A1="full", P9="full")),
        ],
        ["A1", "D1"],
    )  # fmt: skip
    assert (v.kind, v.true, v.in_notes, v.n_runs) == ("action", "partly", "partial", 3)
    assert v.gold == {"A1": "full"}  # D1 in one run only; P9 is not in the packet
    two = L.consensus_item(
        [
            _line("K1", true="yes", kind="problem", gold_match=_gm(A1="full")),
            _line("K1", true="partly", kind="decision", gold_match=_gm(A1="partial")),
        ],
        ["A1"],
    )
    # an even count takes the lower middle value; a kind tie takes the first of KIND_ORDER
    assert (two.true, two.kind, two.gold) == ("partly", "decision", {"A1": "partial"})


def test_consensus_needs_two_runs_per_item() -> None:
    key = {"IS0000a:granola": {"items": {"K1": {}, "K2": {}}, "gold_ids": ["D1"]}}
    r1, _ = L.dedupe([_line("K1"), _line("K2"), _line("K9")])
    r2, _ = L.dedupe([_line("K1")])
    out, problems = L.consensus(key, [r1, r2])
    assert out == {} and problems["IS0000a:granola"] == [
        "run 1: unknown items ['K9']",
        "item K2: read by 1 run(s)",
    ]
    r3, _ = L.dedupe([_line("K1"), _line("K2")])
    out, problems = L.consensus(key, [r3, r2, r3])
    assert not problems and out["IS0000a:granola"]["K1"].n_runs == 3
    empty, problems = L.consensus({"m:granola": {"items": {}, "gold_ids": []}}, [r1])
    assert empty == {"m:granola": {}} and not problems


# -- scoring ----------------------------------------------------------------------------------


def _v(
    true: str = "yes", in_notes: str = "no", kind: str = "decision", **gold: str
) -> L.ItemVerdict:
    return L.ItemVerdict(kind=kind, true=true, in_notes=in_notes, gold=gold, n_runs=3)


def _item(kind: str = "decision", status: str = "agreed", owner: str = "", basis: str = "") -> Any:
    return {"text": "t", "entry_id": "E1", "kind": kind, "status": status, "owner": owner,
            "owner_basis": basis, "entry_ids": ["E1"]}  # fmt: skip


def test_coverage_credit_and_categories() -> None:
    gold = ["D1", "D2", "A1", "P1"]
    assert L.coverage({"D1": "full", "A1": "partial", "X": "full"}, gold) == {
        "all": 0.375,
        "D": 0.5,
        "A": 0.5,
        "P": 0.0,
    }
    assert L.coverage({}, ["D1"])["A"] is None
    best = L.best_match({"K1": _v(D1="partial"), "K2": _v(true="no", D1="full", A1="partial")})
    assert best == {"D1": "full", "A1": "partial"}
    assert L.best_match({"K1": _v(D1="partial"), "K2": _v(true="no", D1="full")}, True) == {
        "D1": "partial"
    }


def _key() -> dict[str, Any]:
    return {
        "m1:granola": {
            "meeting_id": "m1",
            "product": "granola",
            "gold_ids": ["D1", "D2", "A1", "P1"],
            "items": {
                "K1": _item(),
                "K2": _item("action", "agreed", "Bo", "said"),
                "K3": _item("action", "leaning"),
                "K4": _item("problem", "open"),
            },
        },
        "m1:wispr": {
            "meeting_id": "m1",
            "product": "wispr",
            "gold_ids": ["D1", "D2", "A1", "P1"],
            "items": {"K1": _item()},
        },
        "m2:granola": {
            "meeting_id": "m2",
            "product": "granola",
            "gold_ids": ["D1", "A1"],
            "items": {},
        },
        "m3:granola": {"meeting_id": "m3", "product": "granola", "gold_ids": ["D1"], "items": {}},
    }


def _verdicts() -> dict[str, dict[str, L.ItemVerdict]]:
    return {
        "m1:granola": {
            "K1": _v(D1="full", in_notes="yes"),
            "K2": _v(kind="action", A1="full", D2="partial"),
            "K3": _v(true="partly", kind="action", in_notes="partial"),
            "K4": _v(true="no", kind="other", P1="partial"),
        },
        "m1:wispr": {"K1": _v(D1="partial")},
        "m2:granola": {},
    }


NOTES = {
    "m1:granola": {"D1": "full", "D2": "no", "A1": "partial", "P1": "no"},
    "m1:wispr": {"D1": "no", "D2": "full", "A1": "full", "P1": "full"},
    "m2:granola": {"D1": "full", "A1": "partial"},
    "m3:granola": {"D1": "full"},
}


def test_unit_row_counts() -> None:
    row = L.unit_row(_key()["m1:granola"], _verdicts()["m1:granola"], NOTES["m1:granola"])
    assert (row["true_yes"], row["true_partly"], row["true_no"]) == (2, 1, 1)
    assert (row["in_notes_yes"], row["in_notes_partial"], row["in_notes_no"]) == (1, 1, 2)
    assert row["cov_ledger"] == {"all": 0.75, "D": 0.75, "A": 1.0, "P": 0.5}
    assert row["cov_ledger_true"]["all"] == 0.625 and row["cov_ledger_true"]["P"] == 0.0
    assert row["cov_notes"] == {"all": 0.375, "D": 0.5, "A": 0.5, "P": 0.0}
    assert (row["miss_partial"], row["miss_partial_full"], row["miss_partial_any"]) == (1, 1, 1)
    assert (row["miss_no"], row["miss_no_full"], row["miss_no_any"]) == (2, 0, 2)
    assert (row["status_agreed"], row["status_agreed_true_yes"]) == (2, 2)
    assert (row["status_open"], row["status_open_true_no"]) == (1, 1)
    assert (row["actions"], row["actions_owned"], row["owner_said"]) == (2, 1, 1)
    assert row["actions_owned_true_yes"] == 1 and row["reader_kind_other"] == 1


def test_score_groups_means_and_skips() -> None:
    out = L.score(_key(), _verdicts(), NOTES, seed=42)
    assert out["units_not_scored"] == {"m3:granola": "no reader consensus"}
    assert L.score(_key(), _verdicts(), NOTES, seed=42) == out
    g, both = out["summary"]["granola"], out["summary"]["both"]
    assert (g["n_units"], g["n_meetings"], both["n_units"], both["n_meetings"]) == (2, 2, 3, 2)
    assert g["items_per_unit"]["value"] == 2.0  # (4 + 0) / 2: an empty ledger counts
    # mean per unit, then over units: (0.75 + 0) / 2 against the notes' (0.375 + 0.75) / 2
    assert g["coverage"]["all"]["ledger"]["value"] == pytest.approx(0.375)
    assert g["coverage"]["all"]["notes"]["value"] == pytest.approx(0.5625)
    assert g["coverage"]["all"]["ledger_minus_notes"]["value"] == pytest.approx(-0.1875)
    assert g["coverage"]["P"]["ledger"]["n"] == 1  # m2 has no problem gold
    assert both["coverage"]["all"]["ledger"]["value"] == pytest.approx((0.75 + 0.125 + 0) / 3)
    assert g["true_yes"]["value"] == 0.5 and g["true_no"]["num"] == 1
    miss = g["notes_miss"]
    assert (miss["all"]["full"]["num"], miss["all"]["full"]["den"]) == (1, 4)
    assert miss["all"]["incl_partly"]["num"] == 3
    assert (miss["notes_no"]["full"]["num"], miss["notes_no"]["incl_partly"]["num"]) == (0, 2)
    assert g["by_status"]["agreed"]["true_yes"]["value"] == 1.0
    assert g["by_status"]["raised"]["true_yes"]["value"] is None
    assert g["owners"]["actions_with_owner"]["value"] == 0.5
    assert g["owners"]["basis_said"]["value"] == 1.0
    lo, hi = g["coverage"]["all"]["ledger"]["ci"]
    assert 0.0 <= lo <= 0.375 <= hi <= 0.75
    skipped = L.score(_key(), _verdicts(), {k: v for k, v in NOTES.items() if k != "m2:granola"})
    assert skipped["units_not_scored"]["m2:granola"].startswith("gold without a notes verdict")
    table = "\n".join(L.table(out["summary"]))
    assert "| Metric | granola | wispr | both |" in table
    assert "| Coverage, all gold: ledger | 0.38" in table


def test_score_script_end_to_end(tmp_path: Path) -> None:
    key = tmp_path / "r.key.json"
    key.write_text(L.key_json({"run_id": "r"}, _key()), encoding="utf-8")
    notes = tmp_path / "notes.jsonl"
    notes.write_text(
        "".join(
            json.dumps({"meeting": u.split(":")[0], "product": u.split(":")[1], "gold_id": g,
                        "notes_verdict": v}) + "\n"
            for u, per in NOTES.items()
            for g, v in per.items()
        ),
        encoding="utf-8",
    )  # fmt: skip
    runs = []
    for name in ("a", "b", "c"):
        d = tmp_path / name
        d.mkdir()
        lines = [
            _line("K1", meeting="m1", gold_match=_gm(D1="full")),
            _line("K1", meeting="m1", true="no"),  # a repeat: dropped
            _line("K2", meeting="m1", kind="action"),
            _line("K3", meeting="m1", true="partly" if name == "a" else "yes"),
            _line("K4", meeting="m1", true="no"),
            _line("K1", meeting="m1", product="wispr"),
        ]
        (d / "m1.jsonl").write_text(
            "".join(ln.model_dump_json() + "\n" for ln in lines) + "{bad\n", encoding="utf-8"
        )
        runs.append(d)
    out = S.run_score(key, runs, notes, 42)
    assert out["units_not_scored"] == {}  # m2 and m3 have no items: scored as empty ledgers
    assert out["consensus"]["m1:granola"]["K1"]["gold"] == {"D1": "full"}
    assert out["consensus"]["m1:granola"]["K3"]["true"] == "yes"
    assert list(out["meta"]["repeated_lines_dropped"].values()) == [1, 1, 1]
    assert len(out["meta"]["line_errors"]) == 3 and out["meta"]["seed"] == 42
    assert out["summary"]["both"]["n_units"] == 4
    assert out["meta"]["units_without_ledger"] == []
