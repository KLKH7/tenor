"""The E3 second-reader tools: packet rendering (scripts/reader_packets.py) and the scorer
(scripts/reader_score.py). Synthetic inputs only: no AMI data, no product DBs, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import reader_packets as P
from scripts import reader_score as S

# -- packets ----------------------------------------------------------------------------------


def _turns(n: int, prefix: str = "line") -> list[P.Turn]:
    # merged reference turns: turn i starts at dialogue act 2 * i
    return [P.Turn(2 * i, "Project Manager", f"00:{i:02d}", f"{prefix} {i}") for i in range(n)]


def _packet(
    links: tuple[int, ...] = (21,), text: str = "We will use a yellow case."
) -> P.PacketInput:
    return P.PacketInput(
        meeting_id="XX0001a",
        product="granola",
        gold_version="ami-1.6.2",
        background=["Participants:", "- AMI speaker A: Project Manager"],
        gold=[
            P.PacketGold("D1", "decision", text, links),
            P.PacketGold("A1", "action", "The PM will send the slides.", (39,)),
        ],
        reference=_turns(30),
        bullets=[P.PacketBullet("B1", "B1 [section: Decisions] Yellow case agreed")],
        transcript=[P.Turn(0, "Them", "00:00", "yellow case it is")],
    )


def test_windows_merge_and_clip() -> None:
    assert P.windows([5], 30) == [(2, 15)]
    assert P.windows([0], 30) == [(0, 10)]
    assert P.windows([28], 30) == [(25, 29)]
    assert P.windows([5, 12], 30) == [(2, 22)]  # overlapping
    assert P.windows([5, 19], 30) == [(2, 29)]  # adjacent (15, 16)
    assert P.windows([5, 25], 40) == [(2, 15), (22, 35)]  # separate


def test_turn_of_maps_dialogue_act_to_merged_turn() -> None:
    ref = _turns(5)  # turns start at acts 0, 2, 4, 6, 8
    assert [P.turn_of(ref, i) for i in (0, 1, 2, 3, 9)] == [0, 0, 1, 1, 4]


def test_packet_passage_marks_linked_turn_with_context() -> None:
    text = P.render_packet(_packet())
    block = text.split("### D1")[1].split("### A1")[0]
    lines = [x for x in block.splitlines() if "[L" in x]
    assert lines[0].strip().startswith("[L0014")  # act 21 is in turn 10 (acts 20-21): 3 before
    assert lines[-1].strip().startswith("[L0040")  # 10 after
    assert [x for x in lines if x.startswith(">>")] == [">> [L0020 Project Manager 00:10] line 10"]


def test_packet_is_deterministic_and_has_no_verdicts() -> None:
    a, b = P.render_packet(_packet()), P.render_packet(_packet())
    assert a == b
    for word in ("full", "partial", "coverage", "verdict"):
        assert word not in a.lower()
    assert "3b. Full AMI reference transcript" not in a


def test_packet_without_links_includes_full_transcript_once() -> None:
    p = _packet(links=())
    p.gold = [P.PacketGold(g.id, g.category, g.text) for g in p.gold]
    text = P.render_packet(p)
    assert text.count("3b. Full AMI reference transcript") == 1
    assert text.count("line 29") == 1  # every turn, once


def test_empty_gold_item_does_not_pull_in_full_transcript() -> None:
    text = P.render_packet(_packet(links=(), text="*NA*."))
    assert "3b. Full AMI reference transcript" not in text
    assert "no content" in text


def test_write_packets_byte_identical(tmp_path: Path) -> None:
    first = P.write_packets([_packet()], tmp_path / "a")
    second = P.write_packets([_packet()], tmp_path / "b")
    assert first == second
    name = P.packet_name("XX0001a", "granola")
    assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


# -- scorer -----------------------------------------------------------------------------------


def _row(gold_id: str, verdict: str, meeting: str = "M1", product: str = "granola") -> str:
    return json.dumps(
        {
            "meeting": meeting,
            "product": product,
            "gold_id": gold_id,
            "notes_verdict": verdict,
            "transcript_verdict": "yes",
            "evidence_bullets": [] if verdict == "no" else ["B1"],
            "evidence_quote": "",
            "reason": "r",
            "confidence": "high",
        }
    )


def test_kappa_known_values() -> None:
    perfect = [("full", "full"), ("none", "none"), ("partial", "partial")]
    assert S.kappa(perfect, "none") == pytest.approx(1.0)
    # 2x2 textbook case inside the 3 classes: po = 0.7, pe = 0.5 -> kappa 0.4
    pairs = (
        [("full", "full")] * 4
        + [("none", "none")] * 3
        + [("full", "none")] * 2
        + [("none", "full")] * 1
    )
    assert S.kappa(pairs, "none") == pytest.approx(0.4)
    # with only the two extreme classes, linear weights equal unweighted
    assert S.kappa(pairs, "linear") == pytest.approx(0.4)
    assert S.kappa([], "none") is None
    assert S.kappa([("full", "full")], "none") is None  # chance disagreement 0


def test_linear_kappa_gives_half_credit_to_adjacent() -> None:
    pairs = [("full", "partial"), ("partial", "full"), ("none", "none"), ("full", "full")]
    # observed weighted disagreement = (0.5 + 0.5) / 4 = 0.25
    # marginals rows: none 1, partial 1, full 2; cols: none 1, partial 1, full 2
    rows = cols = {"none": 1, "partial": 1, "full": 2}
    idx = {"none": 0, "partial": 1, "full": 2}
    exp = sum(abs(idx[i] - idx[j]) / 2 * rows[i] * cols[j] for i in rows for j in cols) / 16
    assert S.kappa(pairs, "linear") == pytest.approx(1 - 0.25 / exp)


def test_recall_means_per_meeting_then_over_meetings() -> None:
    labels = {
        ("M1", "granola", "D1"): "full",
        ("M1", "granola", "D2"): "none",
        ("M1", "granola", "A1"): "partial",
        ("M1", "granola", "A2"): "full",  # M1: (1 + 0 + 0.5 + 1) / 4 = 0.625
        ("M2", "granola", "P1"): "partial",  # M2: 0.5
    }
    r = S.recall(labels)["granola"]
    assert r["all"] == pytest.approx((0.625 + 0.5) / 2)
    assert r["decision"] == pytest.approx(0.5)  # M1 only
    assert r["action"] == pytest.approx(0.75)
    assert r["problem"] == pytest.approx(0.5)  # M2 only


def test_agreement_confusion_and_breakdowns() -> None:
    a = {
        ("M1", "granola", "D1"): "full",
        ("M1", "wispr", "A1"): "partial",
        ("M2", "wispr", "P1"): "none",
    }
    b = {
        ("M1", "granola", "D1"): "full",
        ("M1", "wispr", "A1"): "none",
        ("M3", "wispr", "P9"): "none",
    }
    s = S.agreement(a, b)
    assert (s["n"], s["agree"], s["only_first"], s["only_second"]) == (2, 1, 1, 1)
    assert s["confusion"]["rows_first"] == [[0, 0, 0], [1, 0, 0], [0, 0, 1]]
    assert s["by_category"]["action"]["agree"] == 0
    assert s["by_product"]["granola"]["agreement"] == 1.0


def test_load_reader_validates_and_normalises(tmp_path: Path) -> None:
    f = tmp_path / "M1__granola.jsonl"
    f.write_text("\n".join([_row("D1", "full"), _row("A1", "no"), ""]))
    labels = S.reader_labels(S.load_reader([tmp_path]))
    assert labels == {("M1", "granola", "D1"): "full", ("M1", "granola", "A1"): "none"}
    f.write_text(_row("D1", "full") + "\n" + _row("D1", "partial"))
    with pytest.raises(ValueError, match="duplicate"):
        S.load_reader([f])
    f.write_text(_row("D1", "maybe"))
    with pytest.raises(ValueError, match=r"M1__granola\.jsonl:1"):
        S.load_reader([f])


def test_human_cards_and_adjudication_labels(tmp_path: Path) -> None:
    key = tmp_path / "key.json"
    key.write_text(
        json.dumps(
            {
                "cards": {
                    "C01": {
                        "meeting_id": "M1",
                        "product": "granola",
                        "item_id": "D1",
                        "agent": "Fully",
                    },
                    "C02": {
                        "meeting_id": "M1",
                        "product": "granola",
                        "item_id": "A1",
                        "agent": "No",
                    },
                }
            }
        )
    )
    adj = tmp_path / "adj.jsonl"
    adj.write_text(
        json.dumps(
            {
                "meeting": "M1",
                "product": "granola",
                "gold_id": "D1",
                "your_notes_verdict": "full",
                "judge_majority": "partial",
            }
        )
        + "\n"
    )
    human = S.load_human(key)
    agent, judge = S.load_adjudication(adj)
    reader = {("M1", "granola", "D1"): "full", ("M1", "granola", "A1"): "partial"}
    h = S.human_score(reader, human)
    assert (h["match"], h["judged"], h["cards"]) == (1, 2, 2)
    s = S.score(reader, (agent, judge), human, True, reader)
    assert s["vs_adjudication"]["agree"] == 1
    assert s["vs_judge"]["agree"] == 0
    assert s["vs_other_reader"]["agreement"] == 1.0
