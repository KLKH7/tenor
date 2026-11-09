"""Gap check (G1 diff, G2 owner suggestions): request shape and shared cache prefix, kept-item
filtering, K-id mapping and G1 counts, G2 unit selection and suggestion scoring, summaries,
reprice and cache evidence, and one unit end to end with a fake backend (no network)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tenor.evals import e4_reader as R
from tenor.evals import gap_check as G
from tenor.evals import list_check as E
from tenor.evals import notes_completeness as N
from tenor.evals import owner_check as O
from tenor.llm.client import Completion, Ledger, LLMClient, LLMRequest

ROSTER = (("PM", "Ana"), ("ID", "Bo"), ("UI", "Cy"), ("ME", "Dee"))


@pytest.fixture(autouse=True)
def _roster(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(O, "series_roster", lambda _mid: ROSTER)


def _unit() -> N.Unit:
    gold = [
        N.GoldItem("D1", "decision", "Use rubber."),
        N.GoldItem("A1", "action", "The industrial designer will send the slides."),
        N.GoldItem("A2", "action", "The project manager will write the minutes."),
        N.GoldItem("A3", "action", "One team member will book a room."),
    ]
    bullets = [
        N.Bullet("B1", "Decisions", "Rubber case agreed"),
        N.Bullet("B2", "Next steps", "Send the slides"),
        N.Bullet("B3", "Next steps", "Ana: write the minutes"),
        N.Bullet("B4", "Next steps", "(Speaker 2) Book a room"),
    ]
    tr = [
        E.Line(0, "Them", "00:00", "So we use rubber for the case."),
        E.Line(1, "Them", "00:05", "Bo, could you send the slides to everyone tomorrow?"),
        E.Line(2, "Them", "00:09", "We really do not have enough time for testing."),
    ]
    return N.Unit("IS0000a", "granola", "test", bullets, gold, "", [], tr)


REGEN = (
    {"id": "R1", "section": "Decisions", "text": "Rubber case agreed."},
    {"id": "R2", "section": "Problems and open issues", "text": "Not enough time for testing."},
    {"id": "R3", "section": "Problems and open issues", "text": "Too little time to test."},
    {"id": "R4", "section": "Action items", "text": "Bo: send the slides tomorrow."},
)


def _ou(gid: str = "A1", bids: tuple[str, ...] = ("B2",), roles: tuple[str, ...] = ("ID",)):
    return G.OwnerUnit("IS0000a", "granola", "test", gid, "partial_owner", bids, roles)


# -- requests and the shared prefix -----------------------------------------------------------


def test_requests_share_system_and_prefix_with_breakpoints() -> None:
    u = _unit()
    d = G.diff_request(u, REGEN)
    s1 = G.owner_request(u, _ou(), "S1")
    s2 = G.owner_request(u, _ou(), "S2")
    pre = G.prefix(u)
    assert d.system == s1.system == s2.system
    for q in (d, s1, s2):
        assert q.user.startswith(pre) and q.cache_breaks[0] == len(pre)
    assert len(d.cache_breaks) == len(s1.cache_breaks) == 1 and len(s2.cache_breaks) == 2
    assert "<calendar_invite>" in s2.user and "<calendar_invite>" not in s1.user
    assert "Ana (Project Manager)" in s2.user
    assert "R3 [section: Problems and open issues]" in d.user
    assert d.user.index("<candidates>") > len(pre) and s1.user.endswith("</action>\n")
    assert s1.stage != s2.stage and d.prompt_id == G.DIFF_PROMPT


def test_attendee_block_natural_meeting_has_no_names() -> None:
    assert "names and roles not available" in G.attendee_block("IB4003")


# -- G1 ---------------------------------------------------------------------------------------


def _diff(*items: tuple[str, list[str], str]) -> G.DiffOut:
    return G.DiffOut(
        items=[G.DiffItem(reason="r", id=i, kind="problem", merged_ids=m, quote=q)
               for i, m, q in items]
    )  # fmt: skip


def test_kept_items_statuses_and_cap() -> None:
    out = _diff(
        ("R2", ["R3"], "do not have enough time"),
        ("R3", [], "do not have enough time"),  # merged above
        ("R9", [], "so we use rubber"),  # unknown id
        ("R4", [], "a quote nobody said"),
        ("R1", [], "So we use rubber"),
    )
    rows = G.kept_items(_unit(), REGEN, out, cap=1)
    assert [r["status"] for r in rows] == [
        "shown", "repeat", "unknown_id", "quote_not_found", "over_cap",
    ]  # fmt: skip
    assert rows[0]["text"] == "Not enough time for testing."


def _key_unit() -> dict[str, Any]:
    return {
        "items": {
            "K1": {"text": "Not enough time for testing.",
                   "sources": [{"condition": "R", "src": "regen:R2", "rank": 1}]},
            "K2": {"text": "Something else",
                   "sources": [{"condition": "R", "src": "regen:R1", "rank": 0}]},
        }
    }  # fmt: skip


def _line(cid: str, **kw: Any) -> R.ReaderLine:
    base: dict[str, Any] = dict(
        meeting="IS0000a", product="granola", cand_id=cid, kind="problem", true="yes",
        in_notes="no", notes_bullets=[], gold_match=[], evidence_quote="", reason="",
        confidence="high",
    )  # fmt: skip
    base.update(kw)
    return R.ReaderLine.model_validate(base)


def test_k_id_needs_source_and_equal_text() -> None:
    ku = _key_unit()
    assert G.k_id(ku, "R2", "not enough time, for testing") == "K1"  # normalize-equal
    assert G.k_id(ku, "R1", "Rubber case agreed.") is None  # text differs
    assert G.k_id(ku, "R3", "Not enough time for testing.") is None  # no such source


def test_score_g1_unit_counts_and_unlabelled() -> None:
    rows = [
        {"status": "shown", "id": "R2", "text": "Not enough time for testing."},
        {"status": "shown", "id": "R1", "text": "Rubber case agreed."},  # unlabelled
        {"status": "quote_not_found", "id": "R4", "text": "x"},
    ]
    v = {"K1": _line("K1", gold_match=[{"id": "P1", "match": "full"}])}
    s = G.score_g1_unit(rows, _key_unit(), v, {"P1": "no", "A1": "partial"})
    assert (s["n_shown"], s["true_new"], s["recovered"], s["n_missing"]) == (1, 1, 1, 2)
    assert (s["n_unlabelled"], s["n_quote_dropped"], s["n_shown_all"]) == (1, 1, 2)
    summ = G.summarize_g1([{"meeting_id": "IS0000a", "product": "granola", **s}], seed=42)
    assert summ["both"]["shown_all_per_unit"] == 2.0
    assert summ["granola"]["recall_missing"] == 0.5


# -- G2 ---------------------------------------------------------------------------------------


def test_bullet_has_owner() -> None:
    assert G.bullet_has_owner("Ana: write the minutes", ROSTER)
    assert not G.bullet_has_owner("Send the slides", ROSTER)
    assert not G.bullet_has_owner("(Speaker 2) Book a room", ROSTER)
    assert G.bullet_has_owner("Everyone to review the slides", ROSTER)


def test_select_owner_units() -> None:
    rows = [
        {"meeting": "IS0000a", "product": "granola", "gold_id": "A1", "notes_verdict":
         "partial", "evidence_bullets": ["B2"], "reason": "Task listed without an owner."},
        {"meeting": "IS0000a", "product": "granola", "gold_id": "A2", "notes_verdict": "full",
         "evidence_bullets": ["B3"], "reason": "Ana writes the minutes."},
        {"meeting": "IS0000a", "product": "granola", "gold_id": "A3", "notes_verdict": "full",
         "evidence_bullets": ["B4"], "reason": "Room booking there."},
        {"meeting": "IS0000a", "product": "granola", "gold_id": "D1", "notes_verdict":
         "partial", "evidence_bullets": ["B1"], "reason": "no owner"},
        {"meeting": "IS0000a", "product": "granola", "gold_id": "A2", "notes_verdict":
         "partial", "evidence_bullets": ["B3"], "reason": "condition missing (gold names no "
         "owner)"},
    ]  # fmt: skip
    units, left = G.select_owner_units([_unit()], rows)
    assert [(u.gold_id, u.source, u.gold_roles) for u in units] == [
        ("A1", "partial_owner", ("ID",))
    ]
    assert left == [{"unit": "IS0000a:granola", "gold_id": "A3", "source": "no_owner_bullet",
                     "why": "gold owner unspecified"}]  # fmt: skip


@pytest.mark.parametrize(
    ("owner", "found", "gold", "want"),
    [
        ("unsure", True, ("ID",), "none"),
        ("Bo", False, ("ID",), "none"),  # quote not in the transcript
        ("Bo", True, ("ID",), "correct"),
        ("the industrial designer", True, ("ID",), "correct"),
        ("Boo", True, ("ID",), "correct"),  # close spelling
        ("Ana", True, ("ID",), "wrong"),
        ("Bo and Cy", True, ("ID",), "partial"),
        ("everyone", True, ("group",), "correct"),
        ("Zed", True, ("ID",), "ambiguous"),
    ],
)
def test_judge_suggestion(owner: str, found: bool, gold: tuple[str, ...], want: str) -> None:
    assert G.judge_suggestion(owner, "q", found, "IS0000a", gold)[0] == want


def test_summarize_owner_and_rescore() -> None:
    u = _unit()
    rows = [
        G.owner_row(_ou(), "S1", G.OwnerOut(reason="r", owner="Bo", quote="could you send the "
                    "slides"), u),
        G.owner_row(_ou(), "S2", G.OwnerOut(reason="r", owner="unsure", quote=""), u),
        G.owner_row(_ou("A2", ("B3",), ("PM",)), "S1",
                    G.OwnerOut(reason="r", owner="Ana", quote="not said"), u),
    ]  # fmt: skip
    assert [r["verdict"] for r in rows] == ["correct", "none", "none"]
    assert rows[2]["verdict_ignoring_quote"] == "correct"
    s = G.summarize_owner(rows, seed=42)
    assert s["S1"]["both"]["coverage"]["k"] == 1 and s["S1"]["both"]["coverage"]["n"] == 2
    assert s["S1"]["both"]["accuracy_when_suggested"]["rate"] == 1.0
    assert s["S1"]["both"]["ignoring_quote_check"]["coverage"]["k"] == 2
    assert s["S1"]["both"]["quote_not_found"] == 1
    re = G.rescore(rows, {rows[0]["key"]: ("UI",)})
    assert re[0]["verdict"] == "wrong" and re[2]["verdict"] == "none"


# -- cost -------------------------------------------------------------------------------------


def test_reprice_and_cache_evidence() -> None:
    ledger = [
        {"stage": G.DIFF_STAGE, "cached": False, "cost_usd": 0.02, "input_tokens": 100,
         "cache_read_tokens": 0, "cache_write_tokens": 5000},
        {"stage": "gap.owner.s1", "cached": False, "cost_usd": 0.004, "input_tokens": 50,
         "cache_read_tokens": 5000, "cache_write_tokens": 0},
        {"stage": "gap.owner.s2", "cached": True, "cost_usd": 9.0, "input_tokens": 50,
         "cache_read_tokens": 5000, "cache_write_tokens": 0},
    ]  # fmt: skip
    r = G.reprice(ledger, {"diff": 10, "owner": 20})
    assert abs(r["full_usd"] - (0.2 + 0.08)) < 1e-9
    assert abs(r["full_usd_with_margin"] - 1.5 * r["full_usd"]) < 1e-9
    ev = G.cache_evidence(ledger)
    assert ev["gap.owner.s1"]["cache_read_tokens"] == 5000 and "gap.owner.s2" not in ev


def test_quote_cost_high_exceeds_expected() -> None:
    job = G.UnitJob(_unit(), REGEN, (_ou(),))

    def counter(texts: list[str]) -> list[int]:
        return [len(t) // 4 for t in texts]

    q = G.quote_cost([job], counter)
    assert q["calls"] == 3 and q["usd_high"] > q["usd_expected"] > 0


# -- end to end -------------------------------------------------------------------------------


class Fake:
    def __init__(self) -> None:
        self.calls: list[LLMRequest] = []

    def complete(self, request: LLMRequest, model: str) -> Completion:
        self.calls.append(request)
        if request.prompt_id == G.DIFF_PROMPT:
            out: Any = {"items": [
                {"reason": "r", "id": "R2", "kind": "problem", "merged_ids": ["R3"],
                 "quote": "do not have enough time"},
                {"reason": "r", "id": "R4", "kind": "action", "merged_ids": [],
                 "quote": "invented words"},
            ]}  # fmt: skip
        else:
            out = {"reason": "r", "owner": "Bo", "quote": "could you send the slides"}
        return Completion(json.dumps(out), model, 100, 20, "end_turn", cache_read_tokens=50)


def test_run_unit_end_to_end(tmp_path: Any) -> None:
    fake = Fake()
    client = LLMClient(fake, model=G.MODEL, cache_dir=tmp_path, ledger=Ledger(), max_usd=1.0)
    res = G.run_unit(G.UnitJob(_unit(), REGEN, (_ou(),)), client)
    assert [c.prompt_id for c in fake.calls] == [G.DIFF_PROMPT, G.OWNER_PROMPT, G.OWNER_PROMPT]
    assert [r["status"] for r in res["g1"]] == ["shown", "quote_not_found"]
    assert [r["verdict"] for r in res["g2"]] == ["correct", "correct"]
    again = G.run_unit(G.UnitJob(_unit(), REGEN, (_ou(),)), client)  # disk cache: same output
    assert again == res and len(fake.calls) == 3
    assert G.cache_evidence(client.ledger.to_json())[G.DIFF_STAGE]["calls_with_reads"] == 1
