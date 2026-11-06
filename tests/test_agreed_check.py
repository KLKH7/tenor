"""E2 "was it agreed?": E1 item loading and the gold dedupe, filter / rank / cap, scoring of
Chat + extras, the summary and sign tests, the quote and reprice (no network: a fake backend)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tenor.evals import agreed_check as A
from tenor.evals import list_check as E
from tenor.llm.client import Completion, Ledger, LLMClient, LLMRequest


def _rec(
    qid: str = "q1", split: str = "dev", category: str = "decision", chat: tuple[str, ...] = ("G1",)
) -> dict[str, Any]:
    """An E1 record: gold G1, G2; Chat covers ``chat``. Items:
    I1 states G1 (redundant when Chat has G1), I2 states G2, I3 true not gold, I4 false,
    I5 true not gold and unconfirmed by E1's checker."""
    items = [
        ("I1", ["G1"], True, True, [1]),
        ("I2", ["G2"], True, True, [3]),
        ("I3", [], True, True, [5]),
        ("I4", [], False, True, [7]),
        ("I5", [], True, False, [9]),
    ]
    return {
        "qid": qid,
        "split": split,
        "category": category,
        "gold": [{"id": "G1", "text": "g one"}, {"id": "G2", "text": "g two"}],
        "items": [
            {"id": i, "text": f"item {i}", "quote": f"quote {i}", "lines": ls}
            for i, _, _, _, ls in items
        ],
        "judged": {
            "C": [{"id": "A1", "gold": list(chat), "in_transcript": True}],
            "L": [{"id": i, "gold": g, "in_transcript": t} for i, g, t, _, _ in items],
        },
        "confirmed": {i: c for i, _, _, c, _ in items},
    }


def _v(status: str, answers: bool = True, conf: int = 50) -> A.Verdict:
    return A.Verdict(status, answers, conf, "r")


# -- E1 items and the dedupe ------------------------------------------------------------------


def test_e1_items_join_verdicts_and_dedupe_by_gold() -> None:
    items = A.e1_items(_rec())
    by = {it.id: it for it in items}
    assert [it.order for it in items] == [0, 1, 2, 3, 4]
    assert by["I1"].redundant and not by["I2"].redundant  # Chat covers G1 only
    assert not by["I3"].redundant  # no gold: never redundant
    assert by["I4"].right is False and by["I3"].right and by["I2"].right
    assert not by["I5"].confirmed
    # Redundant only if *every* gold item it states is covered.
    rec = _rec(chat=("G1",))
    rec["judged"]["L"][0]["gold"] = ["G1", "G2"]
    assert not A.e1_items(rec)[0].redundant


def test_baselines() -> None:
    items = A.e1_items(_rec())
    assert A.baseline_extras(items, A.U) == ["I2", "I3", "I4", "I5"]
    assert A.baseline_extras(items, A.V) == ["I2", "I3", "I4"]
    with pytest.raises(ValueError):
        A.baseline_extras(items, "A")


# -- filter, rank, cap ------------------------------------------------------------------------


def test_keeps_needs_target_status_and_answering() -> None:
    assert A.keeps("decision", _v("agreed_decision"))
    assert not A.keeps("decision", _v("agreed_decision", answers=False))
    assert not A.keeps("decision", _v("proposed_only"))
    assert not A.keeps("decision", _v("assigned_action"))  # true, but not what was asked
    assert A.keeps("action_what", _v("assigned_action"))
    assert A.keeps("problem", _v("problem_raised"))


def test_agreed_extras_filter_rank_cap() -> None:
    items = A.e1_items(_rec())
    verdicts = {
        "I1": _v("agreed_decision", conf=99),  # redundant: never an extra
        "I2": _v("agreed_decision", conf=60),
        "I3": _v("agreed_decision", conf=90),
        "I4": _v("agreed_decision", conf=60),  # ties I2: E1 order breaks it
        "I5": _v("proposed_only", conf=95),
    }
    assert A.agreed_extras(items, verdicts, "decision", None) == ["I3", "I2", "I4"]
    assert A.agreed_extras(items, verdicts, "decision", 2) == ["I3", "I2"]
    assert A.agreed_extras(items, verdicts, "action_what", None) == []
    # E1's checker verdict ranks first: unconfirmed I5, even at confidence 99, comes last.
    verdicts["I5"] = _v("agreed_decision", conf=99)
    assert A.agreed_extras(items, verdicts, "decision", None) == ["I3", "I2", "I4", "I5"]


def test_window_is_cited_lines_plus_k_each_side() -> None:
    t = [E.Line(i, "Them", "", str(i)) for i in range(40)]
    assert [x.index for x in A.window([10], t, 2)] == [8, 9, 10, 11, 12]
    assert [x.index for x in A.window([1, 30], t, 2)] == [0, 1, 2, 3, 28, 29, 30, 31, 32]
    assert A.window([], t, 2) == []


# -- scoring ----------------------------------------------------------------------------------


def test_score_extras_recall_precision_dropped() -> None:
    items = A.e1_items(_rec())
    gids, chat = ["G1", "G2"], {"G1"}
    s = A.score_extras(items, [], gids, chat)
    assert (s.recall, s.n_extras, s.precision_any, s.gold_dropped) == (0.5, 0, None, 1)
    s = A.score_extras(items, ["I2", "I4"], gids, chat)
    assert s.recall == 1.0 and s.precision_any == 0.5 and s.precision_gold == 0.5
    assert s.gold_dropped == 0
    s = A.score_extras(items, ["I3"], gids, chat)
    assert s.recall == 0.5 and s.precision_any == 1.0 and s.gold_dropped == 1


def test_score_question_all_variants() -> None:
    rec = _rec()
    verdicts = {i: _v("agreed_decision") for i in ("I1", "I2", "I3", "I4", "I5")}
    verdicts["I4"] = _v("mentioned_only")
    rows = A.score_question(rec, verdicts)
    assert set(rows) == set(A.VARIANTS)
    assert rows["C"]["recall"] == 0.5 and rows["C"]["n_extras"] == 0
    assert rows["U"]["n_extras"] == 4 and rows["U"]["precision_any"] == 0.75
    assert rows["V"]["n_extras"] == 3
    assert rows["A"]["extras"] == ["I2", "I3", "I5"] and rows["A"]["precision_any"] == 1.0
    assert rows["A2"]["n_extras"] == 2 and rows["A3"]["n_extras"] == 3
    assert rows["A"]["recall"] == 1.0 and rows["A"]["gold_dropped"] == 0


# -- end to end with a fake backend ------------------------------------------------------------


class Fake:
    """Items whose quote mentions I2 or I3 are agreed decisions (I3 more confident); the rest
    are proposed only."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, request: LLMRequest, model: str) -> Completion:
        self.calls.append(request.user)
        m = re.search(r'Quote: "quote (I\d)"', request.user)
        assert m, request.user
        status = "agreed_decision" if m.group(1) in ("I2", "I3") else "proposed_only"
        conf = {"I3": 150, "I2": 70}.get(m.group(1), 40)  # 150: clamped to 100
        out = {"reason": "r", "status": status, "answers_question": True, "confidence": conf}
        return Completion(json.dumps(out), model, 1000, 100, "end_turn")


def _ctx(qid: str = "q1", split: str = "dev") -> E.Context:
    t = [E.Line(i, "Them", "", f"line {i}") for i in range(12)]
    return E.Context(qid, "What was decided?", "decision", split, t, [], E.Gold(()))


def test_run_question_end_to_end_and_replay(tmp_path: Path) -> None:
    backend = Fake()
    reader = LLMClient(backend, model="claude-haiku-5-5", cache_dir=tmp_path, ledger=Ledger())
    rec = A.run_question(_rec(), _ctx(), reader)
    assert len(backend.calls) == 5
    assert "[L0001 Them ] line 1" in backend.calls[0] and "Cited lines: L0001" in backend.calls[0]
    assert rec["verdicts"]["I3"]["confidence"] == 100
    assert rec["variants"]["A"]["extras"] == ["I3", "I2"]
    again = A.run_question(_rec(), _ctx(), reader)
    assert len(backend.calls) == 5 and again == rec  # cached: no new calls, same record


def test_summarize_splits_pairs_and_diagnostics(tmp_path: Path) -> None:
    reader = LLMClient(Fake(), model="claude-haiku-5-5", cache_dir=tmp_path, ledger=Ledger())
    recs = [
        A.run_question(_rec("q1", "dev"), _ctx("q1", "dev"), reader),
        A.run_question(_rec("q2", "test", chat=()), _ctx("q2", "test"), reader),
    ]
    s = A.summarize(recs, seed=1)
    assert set(s) == {"dev", "test", "all"}
    assert s["all"]["variants"]["U"]["n_questions"] == 2
    assert set(s["all"]["paired"]) == {f"{b} vs {a}" for a, b in A.PAIRS}
    p = s["all"]["paired"]["A vs U"]["n_extras"]
    assert p["losses"] == 2 and p["wins"] == 0  # fewer extras on both questions
    d = s["all"]["diagnostics"]
    assert d["statuses"]["agreed_decision"] == 4 and d["statuses"]["proposed_only"] == 6
    # Gold items: I1 and I2 on each question; only I2 is kept. q1's I1 is redundant.
    assert d["gold_items_kept"] == {"all": "2 / 4", "non_redundant": "2 / 3"}
    assert set(s["all"]["by_category"]) == {"decision"}
    assert s == A.summarize(recs, seed=1)  # deterministic
    agg = A.aggregate({"meta": {"questions": ["q1", "q2"], "ledger_by_stage": {}}, "summary": s})
    assert agg["meta"]["n_questions"] == 2 and "questions" not in agg["meta"]
    from tenor.blackbox.kit import check_committable

    check_committable(agg, A.private_texts(recs, ["What was decided in that meeting?"]))


def test_quote_counts_uncached_and_reprice(tmp_path: Path) -> None:
    reader = LLMClient(Fake(), model="claude-opus-5-5", cache_dir=tmp_path, ledger=Ledger())

    def counter(texts: list[str]) -> list[int]:
        return [100] * len(texts)

    pairs = [(_rec(), _ctx())]
    q = A.quote(pairs, reader, counter)
    assert (q.calls, q.cached, q.input_tokens) == (5, 0, 5 * (100 + A.OVERHEAD))
    assert q.usd_high > q.usd_expected > 0
    half = A.quote(pairs, reader, counter, batch=True)
    assert half.usd_expected == pytest.approx(q.usd_expected / 2)
    A.run_question(_rec(), _ctx(), reader)
    assert A.quote(pairs, reader, counter).per_question == {"q1": 0}
    r = A.reprice(reader.ledger.to_json(), full_uncached_calls=20)
    assert r["pilot_calls"] == 5
    assert r["full_usd_with_margin"] == pytest.approx(r["usd_per_call"] * 20 * A.MARGIN)
