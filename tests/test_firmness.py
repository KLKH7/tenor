"""Firmness check and the agent-run helpers: split alignment, job files, answer-line parsing,
flags as reader candidates (kind, owner, quote check, soft/no-text rejects), and condition-blind
packets built from flags pooled with cues (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tenor.evals import bullet_cues as B
from tenor.evals import firmness as F
from tests.test_bullet_cues import PACKET


def _unit(split: str = "dev") -> B.CueUnit:
    return B.parse_packet(PACKET, "IS0000a", "granola", split)


def _jobs() -> list[B.Job]:
    return B.jobs_for(_unit(), {"B2": [(1, 1)], "B3": [(2, 3)]}, pad=1)


def _ans(bid: str, **kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "bullet_id": bid,
        "notes_status": "assigned",
        "notes_owner": "",
        "meeting_status": "assigned",
        "meeting_owner": "",
        "mismatch": "none",
        "flag_text": "",
        "quote": [],
    }
    return d | kw


def _out(**kw: Any) -> F.FirmOut:
    d = _ans("B3", **kw)
    d.pop("bullet_id")
    return F.FirmOut.model_validate(d)


def test_split_alignment_dev_overlap_test_hand_map() -> None:
    dev, test = _unit("dev"), _unit("test")
    hand = {test.key: {"bullet_map": {"B3": [(30, 31)]}}}
    al = B.split_alignment([dev], {})
    assert al[dev.key] == B.overlap_align(dev)
    assert B.split_alignment([test], hand)[test.key] == {"B3": [(30, 31)]}
    with pytest.raises(KeyError):
        B.split_alignment([test], {})


def test_split_units_rejects_unknown_split(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        B.split_units(tmp_path, tmp_path, "train", ())


def test_job_file_has_instructions_notes_once_and_each_job() -> None:
    u = _unit()
    text = B.job_file(u, _jobs(), "PROMPT TEXT", "Firmness jobs")
    assert text.startswith("# Firmness jobs: IS0000a:granola (2 bullets)\n\n## Instructions\n")
    assert text.count("<notes>") == 1 and "PROMPT TEXT" in text
    assert "## Job B2\n\n<bullet>" in text and "## Job B3\n\n<bullet>" in text
    assert "Use rubber." not in text  # no gold text


def test_read_agent_lines_reports_every_problem() -> None:
    lines = [
        json.dumps(_ans("B3")),
        "",
        "{not json",
        json.dumps(_ans("B9")),
        json.dumps(_ans("B3")),
        json.dumps({"no_id": 1}),
    ]
    got, errors = B.read_agent_lines("\n".join(lines), ["B2", "B3"], "u")
    assert [b for b, _ in got] == ["B3"] and "bullet_id" not in got[0][1]
    assert len(errors) == 5
    assert any("unknown or repeated bullet B9" in e for e in errors)
    assert any("unknown or repeated bullet B3" in e for e in errors)
    assert errors[-1] == "u: 1 bullets unanswered (B2)"


def test_flag_rows_kind_owner_and_checks() -> None:
    j = _jobs()[1]  # B3, span T0001-T0004
    assert F.flag_rows(j, _out()) == []
    q = [{"line": "T0003", "text": "I will send the slides tomorrow"}]
    (r,) = F.flag_rows(
        j,
        _out(
            mismatch="owner_missing",
            meeting_owner="Bo",
            flag_text="Bo will send  the slides tomorrow; notes say 'Send the slides'",
            quote=q,
        ),
    )
    assert r["status"] == "ok" and r["kind"] == "action" and r["owner"] == "Bo"
    assert r["mismatch"] == "owner_missing" and r["quote_lines"] == [3]
    assert r["text"] == "Bo will send the slides tomorrow; notes say 'Send the slides'"
    assert B.cue_text(r) == r["text"]  # owner already named
    (d,) = F.flag_rows(
        j,
        _out(
            notes_status="discussed",
            meeting_status="decided",
            meeting_owner="Bo",
            mismatch="softer",
            flag_text="Rubber case decided; notes say 'discussed'",
            quote=[{"line": "T0001", "text": "we use rubber"}],
        ),
    )
    assert d["kind"] == "decision" and d["owner"] == "" and d["status"] == "ok"
    (p,) = F.flag_rows(j, _out(meeting_status="open", mismatch="softer", flag_text="x", quote=q))
    assert p["kind"] == "problem"
    soft = _out(meeting_status="discussed", mismatch="softer", flag_text="x", quote=q)
    assert F.flag_rows(j, soft)[0]["status"] == "soft_meeting"
    assert F.flag_rows(j, _out(mismatch="softer", quote=q))[0]["status"] == "no_text"
    bad = [{"line": "T0005", "text": "Bye"}]
    assert F.flag_rows(j, _out(mismatch="softer", flag_text="x", quote=bad))[0]["status"] == (
        "outside_span"
    )


def test_ingest_unit_and_packets_pool_flags_with_cues() -> None:
    u = _unit()
    jobs = _jobs()
    flag = _ans(
        "B3",
        mismatch="owner_missing",
        meeting_owner="Bo",
        flag_text="Bo will send the slides tomorrow; notes say 'Send the slides'",
        quote=[{"line": "T0002", "text": "Bo, could you send the slides"}],
    )
    text = "\n".join([json.dumps(_ans("B2")), json.dumps(flag | {"extra": 1}), ""])
    rows, errors = F.ingest_unit(u, jobs, text)
    assert [r["bullet_id"] for r in rows] == ["B2"] and len(errors) == 1
    assert "extra" in errors[0]
    rows, errors = F.ingest_unit(u, jobs, json.dumps(_ans("B2")) + "\n" + json.dumps(flag))
    assert errors == [] and rows[1]["model"] == F.CONDITION
    assert rows[1]["firmness"]["mismatch"] == "owner_missing"
    s = F.summary(rows)
    assert s["mismatch"] == {"none": 1, "owner_missing": 1} and s["flags_ok"] == 1
    assert s["notes_to_meeting"] == {"assigned->assigned": 2}
    cue = {"unit": u.key, "bullet_id": "B2", "model": "agent",
           "cues": [{"kind": "decision", "text": "Rubber case chosen", "owner": "",
                     "quote": [], "status": "ok", "quote_lines": [1]}]}  # fmt: skip
    texts, key = B.build_packets([u], [*rows, cue], {}, seed=1)
    packet = texts["IS0000a__granola.md"]
    assert "Bo will send the slides tomorrow" in packet and "Rubber case chosen" in packet
    assert F.CONDITION not in packet and "agent" not in packet.split("## 6.")[-1]
    assert key[u.key]["conditions"] == ["agent", "firmness"]
    assert {s["condition"] for it in key[u.key]["items"].values() for s in it["sources"]} == {
        "agent",
        "firmness",
    }
