"""E4 coverage head-to-head: dedupe request, rank and cap, chat-answer parsing, candidates,
judge parsing and majority, item scoring and metrics, the summary and go checks, quote and
reprice, and an end-to-end meeting with a fake backend (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tenor.evals import coverage_e4 as C4
from tenor.evals import list_check as E
from tenor.evals import notes_completeness as N
from tenor.llm.client import Completion, Ledger, LLMClient, LLMRequest

# -- fixtures ---------------------------------------------------------------------------------


def _unit(product: str = "granola", split: str = "test") -> N.Unit:
    gold = [
        N.GoldItem("D1", "decision", "Use rubber."),
        N.GoldItem("A1", "action", "Ana writes the plan."),
        N.GoldItem("A2", "action", "Bo sends slides."),
        N.GoldItem("P1", "problem", "Not enough time."),
    ]
    bullets = [
        N.Bullet("B1", "Decisions", "Rubber case agreed"),
        N.Bullet("B2", "Next steps", "Ana: write the plan"),
    ]
    tr = [
        E.Line(0, "Them", "00:00", "So we use rubber for the case."),
        E.Line(1, "Them", "00:05", "Bo will send the slides tomorrow."),
        E.Line(2, "Me", "00:09", "We really do not have enough time for this."),
        E.Line(3, "Them", "00:12", "Maybe a solar panel would be nice."),
    ]
    ref = [E.Line(0, "Project Manager", "00:00", "We use rubber. Bo, send the slides.")]
    return N.Unit("IS0000a", product, split, bullets, gold, "Roster: PM = Ana.", ref, tr)


def _cov(i: int, **kw: Any) -> C4.CovItem:
    base: dict[str, Any] = dict(
        id=f"I{i}", order=i, text=f"item {i}", quote="q", lines=(i,), found=True,
        status="agreed_decision", answers=True, confidence=80, in_notes="no",
    )  # fmt: skip
    base.update(kw)
    return C4.CovItem(**base)


def _v(iid: str, **kw: Any) -> C4.ItemVerdict:
    base: dict[str, Any] = dict(
        id=iid, kind="decision", gold=(), supported="yes", in_notes="no", notes_bullets=(),
        owner="n/a",
    )  # fmt: skip
    base.update(kw)
    return C4.ItemVerdict(**base)


# -- the Coverage pipeline's pure parts -------------------------------------------------------


def test_dedupe_request_caches_the_notes_and_names_the_item() -> None:
    u = _unit()
    req = C4.dedupe_request(u, "action", "Bo sends the slides", "Bo will send the slides")
    assert req.prompt_id == C4.DEDUPE_PROMPT and req.stage == C4.DEDUPE_STAGE
    (brk,) = req.cache_breaks
    assert req.user[:brk] == C4.notes_block(u)  # the notes: the shared, cached prefix
    assert "B2 [section: Next steps] Ana: write the plan" in req.user[:brk]
    assert req.user[brk:].startswith("Item (action): Bo sends the slides")
    other = C4.dedupe_request(u, "decision", "Use rubber", "rubber")
    assert other.user[: other.cache_breaks[0]] == req.user[:brk]


def test_kept_and_shown() -> None:
    assert _cov(1).kept and _cov(1).shown
    assert not _cov(1, found=False).kept  # quote not in the transcript
    assert not _cov(1, status="proposed_only").kept
    assert not _cov(1, answers=False).kept
    assert _cov(1, in_notes="partly").shown
    assert not _cov(1, in_notes="yes").shown  # a duplicate of the notes
    assert not _cov(1, in_notes=None).shown  # never deduped
    assert _cov(1, status="assigned_action").category == "action"


def test_rank_puts_decisions_and_actions_first_then_new_then_confidence() -> None:
    items = [
        _cov(1, status="problem_raised", confidence=99),
        _cov(2, status="agreed_decision", confidence=50),
        _cov(3, status="assigned_action", confidence=90),
        _cov(4, status="agreed_decision", confidence=95, in_notes="partly"),
        _cov(5, status="agreed_decision", confidence=50),  # tie with 2: transcript order
        _cov(6, in_notes="yes", confidence=100),  # not shown
        _cov(7, found=False),  # not shown
    ]
    ranked = C4.rank(items)
    assert [c.id for c in ranked] == ["I3", "I2", "I5", "I4", "I1"]
    assert [c.id for c in C4.cap(ranked, 3)] == ["I3", "I2", "I5"]
    assert C4.cap(ranked, None) == ranked
    assert C4.cap(ranked[:2], 3) == ranked[:2]


def test_regen_bullets_get_r_ids(tmp_path: Path) -> None:
    class Fake:
        def complete(self, request: LLMRequest, model: str) -> Completion:
            md = "## Decisions\n- Use rubber\n  - for the case\n## Action items\n- Bo: slides"
            return Completion(json.dumps({"notes_markdown": md}), model, 100, 50, "end_turn")

    c = LLMClient(Fake(), model=C4.REGEN_MODEL, cache_dir=tmp_path)
    _md, bs = C4.regen(_unit(), c)
    assert [b.id for b in bs] == ["R1", "R2", "R3"]
    assert bs[1].parent == "R1" and bs[2].section == "Action items"


def test_chat_items_bullets_only_without_citations_and_intro_parents() -> None:
    answer = (
        "Here is what your notes leave out [[1]](https://notes.granola.ai/d/abc):\n\n"
        "**Decisions**\n"
        "- The case will be rubber [[1]](https://notes.granola.ai/d/abc)\n"
        "- Action items:\n"
        "  - Bo sends the slides\n"
        "  - Ana books the room\n\n"
        "Let me know if you want more."
    )
    assert C4.chat_items(answer) == [
        "The case will be rubber",
        "Bo sends the slides",
        "Ana books the room",
    ]
    assert C4.chat_items("Nothing important is missing from your notes.") == [
        "Nothing important is missing from your notes."
    ]
    assert C4.chat_items("") == []


# -- candidates, judge parsing, majority ------------------------------------------------------


def test_candidates_union_dedupes_text_and_order_is_seeded() -> None:
    cov = [_cov(1, text="Bo sends the slides"), _cov(2, text="Hidden", in_notes="yes")]
    rb = [N.Bullet("R1", "Action items", "Bo sends the slides."), N.Bullet("R2", "D", "Rubber")]
    chat = ["Rubber", "Solar panel idea"]
    a = C4.candidates(cov, rb, chat, 42, "m:granola")
    assert len(a) == 3  # "Hidden" not shown; the slides and "Rubber" appear twice
    by_text = {c.text: c.sources for c in a}
    assert by_text["Bo sends the slides"] == ("cov:I1", "regen:R1")
    assert by_text["Rubber"] == ("regen:R2", "chat:1")
    assert [c.id for c in a] == ["X1", "X2", "X3"]
    assert C4.candidates(cov, rb, chat, 42, "m:granola") == a  # deterministic
    orders = {tuple(c.text for c in C4.candidates(cov, rb, chat, s, "k")) for s in range(20)}
    assert len(orders) > 1  # the order depends on the seed


def _judge_item(iid: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id=iid, reason="r", kind="decision", gold=[], supported="yes", in_notes="no",
        notes_bullets=[], owner="n/a",
    )  # fmt: skip
    base.update(kw)
    return base


def test_parse_repairs_unknown_missing_and_inconsistent() -> None:
    u = _unit()
    cands = [C4.Candidate(f"X{i}", f"t{i}", ()) for i in (1, 2, 3)]
    out = C4.JudgeOut.model_validate(
        {
            "items": [
                _judge_item("X1", gold=["D1", "D9"], notes_bullets=["B1", "B7"]),
                _judge_item("X1"),
                _judge_item("X9"),
                _judge_item("X2", kind="other", supported="yes", owner="wrong"),
            ]
        }
    )
    v, repairs = C4.parse(out, u, cands)
    assert v["X1"].gold == ("D1",) and v["X1"].notes_bullets == ("B1",)
    assert v["X2"].supported == "n/a" and v["X2"].owner == "n/a"
    assert v["X3"].kind == "other" and v["X3"].in_notes == "no"
    assert repairs == [
        "item X1 twice",
        "unknown item id X9",
        "item X1: unknown or repeated ids",
        "item X3 missing",
    ]


def test_majority_votes_per_field() -> None:
    p1 = {"X1": _v("X1", kind="action", owner="correct", gold=("A1",), in_notes="no")}
    p2 = {"X1": _v("X1", kind="action", owner="wrong", gold=("A1",), in_notes="yes")}
    p3 = {"X1": _v("X1", kind="decision", gold=(), in_notes="partly", supported="partly")}
    m = C4.majority([p1, p2, p3])["X1"]
    assert m.kind == "action" and m.gold == ("A1",)
    assert m.owner == "wrong"  # 1-1 tie between the action passes: the cautious side
    assert m.in_notes == "partly"  # median of no, partly, yes
    assert m.supported == "yes"  # the two action passes said yes (lower median of yes, yes)
    one = C4.majority([p2])["X1"]
    assert one.in_notes == "yes" and one.owner == "wrong"
    two = C4.majority([p1, p2])["X1"]
    assert two.in_notes == "yes"  # a tie counts against the item (upper median)


# -- scoring ----------------------------------------------------------------------------------


def test_is_true_lenient_and_strict() -> None:
    missing = {"A2", "P1"}
    assert C4.is_true(_v("x", gold=("A2",), supported="partly"), missing)
    assert C4.is_true(_v("x", gold=("A2",), supported="partly"), missing, strict=True)
    assert C4.is_true(_v("x", gold=(), supported="yes"), missing)  # true but not gold
    assert not C4.is_true(_v("x", gold=(), supported="yes"), missing, strict=True)
    assert not C4.is_true(_v("x", gold=("A2",), in_notes="yes"), missing)  # duplicate
    assert not C4.is_true(_v("x", kind="other", gold=("A2",)), missing)
    assert not C4.is_true(_v("x", gold=("D1",), supported="partly"), missing)  # covered gold


def test_score_items_counts_and_complete_cells() -> None:
    u = _unit()
    cov = {"D1": "full", "A1": "partial", "A2": "none", "P1": "none"}
    vs = {
        "X1": _v("X1", kind="action", gold=("A2",), owner="correct"),
        "X2": _v("X2", kind="action", gold=("A1",), owner="wrong", supported="partly"),
        "X3": _v("X3", kind="decision", gold=("D1",), in_notes="yes"),  # duplicate
        "X4": _v("X4", kind="decision", supported="no"),  # false, in a complete cell
        "X5": _v("X5", kind="other", supported="n/a"),
        "X6": _v("X6", kind="action", owner="none", supported="yes"),
    }
    s = C4.score_items(list(vs), vs, u.gold, cov)
    assert (s["n_shown"], s["true"], s["true_strict"]) == (6, 3, 2)
    assert s["duplicates"] == 1 and s["other_kind"] == 1 and s["unsupported"] == 1
    assert (s["actions"], s["actions_named"], s["wrong_owner"]) == (3, 2, 1)
    assert (s["n_missing"], s["n_missing_none"]) == (3, 2)
    assert (s["recovered"], s["recovered_none"], s["recovered_ids"]) == (2, 1, ["A1", "A2"])
    assert s["complete_cells"] == {"decision": {"shown": 2, "false": 2}}
    m = C4.unit_metrics(s)
    assert m["precision"] == 0.5 and m["recall_missing"] == 2 / 3
    assert m["wrong_owner"] == 0.5 and m["duplicates"] == 1 / 6 and m["false_items"] == 3.0
    empty = C4.unit_metrics(C4.score_items([], vs, u.gold, cov))
    assert empty["precision"] is None and empty["recall_missing"] == 0.0


def test_shown_ids_per_condition() -> None:
    cov = [
        _cov(1, text="a", status="problem_raised"),
        _cov(2, text="b"),
        _cov(3, text="c"),
        _cov(4, text="d", in_notes="partly"),
        _cov(5, text="e", in_notes="yes"),
    ]
    rb = [N.Bullet("R1", "D", "b"), N.Bullet("R2", "O", "context"), N.Bullet("R3", "D", "f")]
    cands = C4.candidates(cov, rb, ["g"], 1, "k")
    of = {c.text: c.id for c in cands}
    vs = {c.id: _v(c.id) for c in cands}
    vs[of["context"]] = _v(of["context"], kind="other")
    vs[of["f"]] = _v(of["f"], in_notes="yes")
    s = C4.shown_ids(cov, rb, ["g"], cands, vs, k=3)
    assert s[C4.CU] == [of[t] for t in ("b", "c", "d", "a")]
    assert s[C4.C3] == [of[t] for t in ("b", "c", "d")]
    assert s[C4.CUS] == [of[t] for t in ("b", "c", "a")]  # "d" is partly in the notes
    assert s[C4.C3S] == s[C4.CUS]
    assert C4.RD not in s  # no real dedupe of R given
    assert s[C4.R] == [of["b"]]  # "context" is other, "f" already in the notes
    assert s[C4.Q] == [of["g"]]
    assert C4.Q not in C4.shown_ids(cov, rb, None, cands, vs)
    rd = C4.shown_ids(cov, rb, None, cands, vs, regen_dedupe={"R1": "partly", "R3": "yes"})
    assert rd[C4.RD] == [of["b"]]  # R2 never deduped (not under a D/A/P heading)


# -- summary and go checks --------------------------------------------------------------------


def _record(mid: str, product: str, true: int, shown: int, rec: int, miss: int) -> dict[str, Any]:
    s = {
        "n_shown": shown, "true": true, "true_strict": true, "true_new": true, "duplicates": 0,
        "partly_in_notes": 0, "other_kind": 0, "unsupported": 0, "actions": 1,
        "actions_named": 1, "wrong_owner": 0, "n_missing": miss, "n_missing_none": miss,
        "recovered": rec, "recovered_none": rec, "recovered_ids": [],
        "complete_cells": {"problem": {"shown": 0, "false": 0}},
    }  # fmt: skip
    worse = dict(s, true=max(0, true - 1), recovered=max(0, rec - 1))
    scores = {C4.C3: s, C4.CU: s, C4.R: worse}
    return {
        "meeting_id": mid, "product": product, "split": "test",
        "e3": {"recall": 0.6, "coverage": {"D1": "full", "A1": "none"}}, "n_gold": 2,
        "scores": scores, "metrics": {c: C4.unit_metrics(v) for c, v in scores.items()},
        "repairs": [],
    }  # fmt: skip


def test_summarize_pools_tests_and_go_checks() -> None:
    recs = [_record(f"M{i}", p, 3, 4, 2, 4) for i in range(5) for p in ("granola", "wispr")]
    out = C4.summarize(recs, seed=1)
    g = out["test"]["products"]["granola"]
    c3 = g["conditions"][C4.C3]
    assert c3["precision"] == 0.75 and c3["recall_missing"] == 0.5
    assert c3["precision_ci"] == (0.75, 0.75)  # every unit the same
    assert c3["false_per_complete_cell"] == 0.0 and c3["complete_cells"] == 5
    assert g["conditions"][C4.N_]["missing_gold"] == 5
    assert g["paired"]["C3 vs R"]["precision"]["wins"] == 5
    go = g["go"][C4.C3]
    assert go["precision>=0.70"] and go["precision_ci_lo>=0.60"] and go["recall_missing>=0.35"]
    assert go["beats_R"] is True and go["beats_Q"] is None and go["stability"] is None
    assert out["test"]["products"]["both"]["conditions"][C4.C3]["n_units"] == 10
    assert C4.summarize(recs, seed=1) == out  # deterministic


def test_ratio_ci_is_seeded_and_handles_empty() -> None:
    pairs = [(1.0, 2.0), (0.0, 3.0), (3.0, 3.0), (2.0, 4.0)]
    a = C4.ratio_ci(pairs, 7)
    assert a is not None and a[0] <= 6 / 12 <= a[1]
    assert C4.ratio_ci(pairs, 7) == a
    assert C4.ratio_ci([], 7) is None and C4.ratio_ci([(0.0, 0.0)], 7) is None


def test_reprice_scales_by_stage() -> None:
    ledger = [
        {"stage": "e1.enumerate", "cost_usd": 0.10},
        {"stage": "e1.merge", "cost_usd": 0.02},
        {"stage": "e2.agreed", "cost_usd": 0.01},
        {"stage": "e4.judge", "cost_usd": 0.20},
        {"stage": "e3.judge", "cost_usd": 0.0},
    ]
    r = C4.reprice(
        ledger, {"transcript": 100, "judge": 50}, {"transcript": 1000, "judge": 500}, 1, 3, 0.5
    )
    assert abs(r["full_usd_by_group"]["list"] - 1.2) < 1e-9
    assert abs(r["full_usd_by_group"]["judge"] - 6.0) < 1e-9  # x10 size, x3 passes
    assert r["full_usd_by_group"]["e3"] == 0.5
    assert abs(r["full_usd_with_margin"] - 1.5 * r["full_usd"]) < 1e-9


# -- end to end with a fake backend -----------------------------------------------------------


class Fake:
    """Canned replies per prompt."""

    def __init__(self) -> None:
        self.calls: list[LLMRequest] = []

    def complete(self, request: LLMRequest, model: str) -> Completion:
        self.calls.append(request)
        p = request.prompt_id
        if p == E.ENUMERATE_PROMPT:
            out: Any = {
                "items": [
                    {"item": "Use rubber", "quote": "we use rubber for the case", "lines": [0]},
                    {"item": "Bo sends slides", "quote": "Bo will send the slides", "lines": [1]},
                    {"item": "Too little time", "quote": "not have enough time", "lines": [2]},
                    {"item": "Solar panel", "quote": "a quote nobody said", "lines": [3]},
                ]
            }
        elif p == E.MERGE_PROMPT:
            out = {"groups": [{"members": [i], "item": ""} for i in range(4)]}
        elif p == C4.A.PROMPT:
            status = "assigned_action" if "Bo" in request.user.split("Quote:")[0] else (
                "problem_raised" if "time" in request.user.split("Quote:")[0]
                else "agreed_decision"
            )  # fmt: skip
            out = {"reason": "r", "status": status, "answers_question": True, "confidence": 70}
        elif p == C4.DEDUPE_PROMPT:
            dup = "ubber" in request.user.split("Item (")[1]
            out = {"reason": "r", "in_notes": "yes" if dup else "no",
                   "bullets": ["B1"] if dup else []}  # fmt: skip
        elif p == C4.REGEN_PROMPT:
            md = "## Decisions\n- Rubber case agreed\n## Action items\n- Bo sends the slides"
            out = {"notes_markdown": md}
        elif p == C4.JUDGE_PROMPT:
            ids = [ln.split(".")[0] for ln in request.user.split("Candidate items:\n")[1]
                   .split("\n\nJudging")[0].splitlines()]  # fmt: skip
            out = {"items": [_judge_item(i, gold=["A2"] if "slides" in request.user else [])
                             for i in ids]}  # fmt: skip
            for it, line in zip(out["items"], request.user.split("Candidate items:\n")[1]
                                .splitlines(), strict=False):  # fmt: skip
                it["gold"] = ["A2"] if "slides" in line else []
                it["in_notes"] = "yes" if "Rubber" in line else "no"
        elif p.startswith("notes_completeness"):
            out = {
                "bullets": [
                    {"id": "B1", "kind": "decision", "gold": ["D1"], "supported": "yes",
                     "reason": "r"},
                    {"id": "B2", "kind": "action", "gold": ["A1"], "supported": "yes",
                     "reason": "r"},
                ],
                "gold": [
                    {"id": "D1", "coverage": "full", "bullets": ["B1"],
                     "in_product_transcript": "not_assessed", "quote": "", "reason": "r"},
                    {"id": "A1", "coverage": "full", "bullets": ["B2"],
                     "in_product_transcript": "not_assessed", "quote": "", "reason": "r"},
                    {"id": "A2", "coverage": "none", "bullets": [],
                     "in_product_transcript": "yes", "quote": "Bo will send the slides",
                     "reason": "r"},
                    {"id": "P1", "coverage": "none", "bullets": [],
                     "in_product_transcript": "yes", "quote": "x", "reason": "r"},
                ],
            }  # fmt: skip
        else:
            raise AssertionError(p)
        return Completion(json.dumps(out), model, 1000, 200, "end_turn")


def test_run_meeting_end_to_end_and_replay(tmp_path: Path) -> None:
    fake = Fake()
    led = Ledger()

    def mk(model: str) -> LLMClient:
        return LLMClient(fake, model=model, cache_dir=tmp_path, ledger=led)

    clients = C4.Clients(mk(C4.LIST_MODEL), mk(C4.CHECK_MODEL), mk(C4.REGEN_MODEL),
                         mk(C4.JUDGE_MODEL))  # fmt: skip
    units = [_unit("granola"), _unit("wispr")]
    chat = {"IS0000a:granola": "- Bo sends the slides\n- The room is booked"}
    recs = C4.run_meeting(units, clients, chat, passes=2, seed=3)
    assert [r["product"] for r in recs] == ["granola", "wispr"]
    g = recs[0]
    cov = {c["text"]: c for c in g["coverage_items"]}
    assert cov["Solar panel"]["status"] is None  # quote not found: never checked
    assert cov["Use rubber"]["in_notes"] == "yes"  # deduped away
    assert set(g["shown"]) == {C4.C3, C4.CU, C4.C3S, C4.CUS, C4.R, C4.RD, C4.Q}
    assert g["regen_dedupe"] == {"R1": "yes", "R2": "no"}  # rubber is in the notes
    assert g["scores"][C4.RD]["n_shown"] == 1
    assert C4.Q not in recs[1]["shown"]  # Wispr: no Chat
    assert g["e3"]["coverage"] == {"D1": "full", "A1": "full", "A2": "none", "P1": "none"}
    c3 = g["scores"][C4.C3]
    assert c3["n_shown"] == 2 and c3["recovered_ids"] == ["A2"]
    assert g["scores"][C4.R]["n_shown"] == 1  # the rubber bullet is in the notes
    judge_calls = [c for c in fake.calls if c.prompt_id == C4.JUDGE_PROMPT]
    assert len(judge_calls) == 4  # 2 units x 2 passes
    assert judge_calls[0].user.endswith("Judging pass 1.")
    n = len(fake.calls)
    assert C4.run_meeting(units, clients, chat, passes=2, seed=3) == recs  # replays
    assert len(fake.calls) == n
    summary = C4.summarize(recs, seed=1)
    agg = C4.aggregate({"meta": {"ledger_by_stage": {}, "date": "x"}, "summary": summary})
    assert "ledger_by_stage" not in agg["meta"]
    assert "Bo sends the slides" in C4.private_texts(recs)
