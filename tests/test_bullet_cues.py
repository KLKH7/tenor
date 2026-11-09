"""Bullet cues: packet parsing, alignment (hand ranges, word overlap, agreement), span widening,
request shape and shared cache prefix, quote checks, proxies, condition-blind packets, and one
unit end to end with a fake backend (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tenor.evals import bullet_cues as B
from tenor.evals import e4_reader as R
from tenor.llm.client import Completion, Ledger, LLMClient, LLMRequest

PACKET = """# Reader packet: IS0000a / Granola

Packet format reader_packet.v1.

## 1. Meeting background

Team of four.

## 2. Gold items

Decisions:
- D1 (decision): Use rubber.

Actions:
- A1 (action): The industrial designer will send the slides.

## 3. Where each gold item was said (AMI human reference transcript)

```
>> [L0001 Project Manager 00:01] rubber it is
```

## 4. The product's notes, split into bullets

```
B1 [section: Decisions] Materials discussed:
B2 [section: Decisions] (sub-point of B1) Rubber case agreed
B3 [section: Next steps] Send the slides
```

## 5. The product's own transcript

```
[T0000 Them 00:00] Hello everyone.
[T0001 Them 00:05] So we use rubber for the case, agreed.
[T0002 Speaker 2 00:09] Bo, could you send the slides to everyone tomorrow?
[T0003 Them 00:12] Sure, I will send the slides tomorrow.
[T0004 Them 00:15] We really do not have enough time for testing.
[T0005 Them 1:00:20] Bye.
```
"""


def _unit() -> B.CueUnit:
    return B.parse_packet(PACKET, "IS0000a", "granola", "test")


def test_parse_packet_reads_bullets_transcript_and_gold_ids_only() -> None:
    u = _unit()
    assert [b.id for b in u.bullets] == ["B1", "B2", "B3"]
    assert u.bullets[1].parent == "B1" and u.bullets[1].text == "Rubber case agreed"
    assert u.bullets[0].render() == "B1 [section: Decisions] Materials discussed:"
    assert [x.index for x in u.transcript] == [0, 1, 2, 3, 4, 5]
    assert u.transcript[2].speaker == "Speaker 2" and u.transcript[5].start == "1:00:20"
    assert u.gold_ids == ("D1", "A1")
    req = B.cue_request(B.Job(u, "B2", ((0, 5),)), "haiku")
    assert "rubber it is" not in req.user and "Use rubber." not in req.user  # no gold text


def test_ranges_widen_and_merge() -> None:
    assert B.parse_ranges(["T0010-T0013", "T0005-T0002", "T0045"]) == [(10, 13), (2, 5), (45, 45)]
    assert B.widen([(10, 13), (20, 21)], 0, 100, pad=3) == ((7, 24),)
    assert B.widen([(1, 2), (40, 41)], 0, 42, pad=5) == ((0, 7), (35, 42))


def test_overlap_align_and_agreement() -> None:
    u = _unit()
    a = B.overlap_align(u)
    assert a["B3"] == [(2, 3)]  # "send", "slides"
    assert a["B2"] == [(1, 1)]  # "rubber", "case", "agreed"
    assert a["B1"] == []  # "materials", "discussed": no hit
    hand = {"B1": [], "B2": [(1, 1)], "B3": [(30, 31)]}
    assert B.alignment_agreement(a, hand, pad=5) == {
        "agree": 1,
        "disagree": 1,
        "auto_empty": 0,
        "extra": 0,
    }


def test_requests_share_system_and_notes_prefix_with_breakpoint() -> None:
    u = _unit()
    jobs = B.jobs_for(u, {"B2": [(1, 1)], "B3": [(2, 3)], "B1": []}, pad=1)
    assert [j.bullet_id for j in jobs] == ["B2", "B3"] and jobs[0].spans == ((0, 2),)
    qs = [B.cue_request(j, "haiku") for j in jobs]
    pre = B.notes_block(u)
    for q in qs:
        assert q.user.startswith(pre) and q.cache_breaks == (len(pre),)
        assert q.system == qs[0].system and q.prompt_id == B.PROMPT
    assert "<bullet>\nB1 [section: Decisions] Materials discussed:\nB2" in qs[0].user
    assert "[T0002 Speaker 2 00:09] Bo, could you" in qs[0].user
    assert "[T0003" not in qs[0].user and qs[1].user.endswith("</span>\n")
    two = B.bullet_block(B.Job(u, "B3", ((0, 0), (4, 5))))
    assert "[T0000 Them 00:00] Hello everyone.\n...\n[T0004" in two


def _cue(text: str, quote: list[tuple[str, str]], kind: str = "action", owner: str = "") -> Any:
    return B.Cue.model_validate(
        {
            "kind": kind,
            "text": text,
            "owner": owner,
            "quote": [{"line": ln, "text": t} for ln, t in quote],
        }
    )


def test_check_cue_and_rows() -> None:
    j = B.Job(_unit(), "B3", ((1, 3),))
    assert B.check_cue(_cue("x", [("T0003", "I will SEND the slides")]), j) == "ok"
    assert B.check_cue(_cue("x", []), j) == "no_quote"
    assert B.check_cue(_cue("x", [("L0003", "send")]), j) == "bad_line"
    assert B.check_cue(_cue("x", [("T0099", "send")]), j) == "bad_line"
    assert B.check_cue(_cue("x", [("T0004", "enough time")]), j) == "outside_span"
    assert B.check_cue(_cue("x", [("T0002", "send the slides by Friday")]), j) == "text_not_found"
    out = B.CueOut(
        found=[],
        cues=[_cue(f"c{i}", [("T0001", "rubber")]) for i in range(4)]
        + [_cue("bad", [("T0001", "steel")])],
    )
    rows = B.cue_rows(j, out, cap=3)
    assert [r["status"] for r in rows] == ["ok", "ok", "ok", "over_cap", "text_not_found"]
    assert rows[0]["quote_lines"] == [1]


def test_cue_text_and_distinct_by_quote() -> None:
    assert B.cue_text({"text": "Bo  sends the slides.", "owner": "Bo"}) == "Bo sends the slides."
    assert B.cue_text({"text": "Send slides.", "owner": "Bo"}) == "Send slides. (owner: Bo)"
    rows = [
        {"kind": "action", "quote_lines": [2, 3]},
        {"kind": "action", "quote_lines": [3]},
        {"kind": "problem", "quote_lines": [3]},
        {"kind": "action", "quote_lines": [9]},
    ]
    assert B.distinct_by_quote(rows) == 3


def _reader(cid: str, gold: list[dict[str, str]], in_notes: str = "no") -> dict[str, Any]:
    return {
        "meeting": "IS0000a",
        "product": "granola",
        "cand_id": cid,
        "kind": "action",
        "true": "yes",
        "in_notes": in_notes,
        "notes_bullets": [] if in_notes == "no" else ["B3"],
        "gold_match": gold,
        "evidence_quote": "",
        "reason": "r",
        "confidence": "high",
    }


KEY_UNITS = {
    "IS0000a:granola": {"items": {"K1": {"text": "Bo sends the slides tomorrow."}}},
}
MAP = {"gold": [{"gold_id": "A1", "notes_verdict": "partial", "lines": ["T0002-T0003"]},
                {"gold_id": "D1", "notes_verdict": "full", "lines": ["T0001-T0001"]}]}  # fmt: skip


def _fake(payloads: list[dict[str, Any]]) -> Any:
    class Fake:
        def __init__(self) -> None:
            self.reqs: list[LLMRequest] = []

        def complete(self, req: LLMRequest, model: str) -> Completion:
            self.reqs.append(req)
            body = payloads[len(self.reqs) - 1]
            return Completion(json.dumps(body), model, 100, 20, "end_turn", cache_read_tokens=50)

    return Fake()


def test_end_to_end_proxies_and_packets(tmp_path: Path) -> None:
    u = _unit()
    jobs = B.jobs_for(u, {"B2": [(1, 1)], "B3": [(2, 3)]}, pad=0)
    payloads = [
        {"found": [], "cues": []},
        {
            "found": [
                {"item": "slides", "lines": ["T0002"], "notes": ["B3"], "verdict": "part_missing"}
            ],
            "cues": [
                {
                    "kind": "action",
                    "text": "Bo sends the slides tomorrow.",
                    "owner": "Bo",
                    "quote": [{"line": "T0002", "text": "Bo, could you send the slides"}],
                },
                {
                    "kind": "problem",
                    "text": "Not enough time for testing.",
                    "owner": "",
                    "quote": [{"line": "T0004", "text": "not have enough time for testing"}],
                },
                {
                    "kind": "decision",
                    "text": "Slides go to everyone.",
                    "owner": "",
                    "quote": [{"line": "T0003", "text": "Sure, I will send the slides tomorrow"}],
                },
            ],
        },
    ]
    fake = _fake(payloads)
    client = LLMClient(fake, model="claude-haiku-5-5", cache_dir=tmp_path, ledger=Ledger())
    rows = B.run_jobs(jobs, client, "haiku", lambda _s: None)
    assert [len(r["cues"]) for r in rows] == [0, 3]
    assert [c["status"] for c in rows[1]["cues"]] == ["ok", "outside_span", "ok"]
    assert all(q.cache_breaks == fake.reqs[0].cache_breaks for q in fake.reqs)
    labelled = B.labelled_items(KEY_UNITS, [_reader("K1", [{"id": "A1", "match": "full"}])])
    per = B.proxy_rows(rows, labelled, {u.key: MAP})
    p = per[0]
    assert (p["bullets"], p["returned"], p["ok"], p["bullets_with_cue"]) == (2, 3, 2, 1)
    assert p["failed"]["outside_span"] == 1 and p["exact_e4"] == 1 and p["exact_e4_true_new"] == 1
    assert p["exact_e4_gold"] == ["A1"] and p["near_missing_gold"] == ["A1"]
    assert p["found"] == {"part_missing": 1}
    s = B.summarize(per)["haiku"]["both"]
    assert s["quote_verified_rate"] == 2 / 3 and s["cues_per_unit"] == 2.0
    texts, keys = B.build_packets([u], rows, labelled, seed=42)
    k = keys[u.key]
    assert list(k["items"]) == ["K1"] and k["items"]["K1"]["text"] == "Slides go to everyone."
    assert k["prelabelled"]["haiku"][0]["e4_cand_id"] == "K1"
    page = texts["IS0000a__granola.md"]
    assert page.startswith("# E4 reader packet: IS0000a / Granola")
    assert "## 6. Candidate items to score" in page and "K1. Slides go to everyone." in page
    assert "haiku" not in page and "Bo sends the slides" not in page
    assert "## 5. The product's own transcript" in page
    line = _reader("K1", [])
    got, probs = R.check_unit(k, [R.ReaderLine.model_validate(line)])
    assert probs == [] and list(got) == ["K1"]
    assert B.build_packets([u], rows, labelled, seed=42) == (texts, keys)  # deterministic
    priv = B.private_texts(rows)
    assert "Slides go to everyone." in priv and "slides" in priv


def test_reprice_and_cache_evidence() -> None:
    led = [
        {"stage": "cues.haiku", "cached": False, "cost_usd": 0.001, "input_tokens": 10,
         "cache_read_tokens": 5, "cache_write_tokens": 0},
        {"stage": "cues.haiku", "cached": True, "cost_usd": 9.0, "input_tokens": 10,
         "cache_read_tokens": 0, "cache_write_tokens": 0},
        {"stage": "cues.sonnet", "cached": False, "cost_usd": 0.01, "input_tokens": 10,
         "cache_read_tokens": 0, "cache_write_tokens": 7},
    ]  # fmt: skip
    rp = B.reprice(led, {"haiku": 100, "sonnet": 10})
    assert rp["full_usd"] == {"haiku": 0.1, "sonnet": 0.1}
    assert abs(rp["full_usd_with_margin"]["haiku"] - 0.15) < 1e-9
    ev = B.cache_evidence(led)
    assert (
        ev["cues.haiku"]["calls_with_reads"] == 1 and ev["cues.sonnet"]["cache_write_tokens"] == 7
    )
