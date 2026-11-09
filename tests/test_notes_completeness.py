"""E3 notes completeness: splitting notes into bullets, gold items, the judge request and its
parsing, scoring math, the summary and sign tests, quote and reprice, and an end-to-end run with
a fake backend (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tenor.ami.corpus import AmiMeeting, DialogueAct, Speaker, SummarySentence, Word, meeting_note
from tenor.evals import list_check as E
from tenor.evals import notes_completeness as N
from tenor.llm.client import Completion, Ledger, LLMClient, LLMRequest, cost_usd, text_blocks

# -- splitting --------------------------------------------------------------------------------

NOTES = """Intro sentence about the meeting.
It continues here.

# Overview

- **Product**: a remote
- Goal: easy to use
  - sub-point one
  - sub-point two
    - deeper
- lazy bullet
continued on the next line

**Next Steps**

1. Write the plan (Ana)

   Due in two weeks.
2. Send slides
-

---
"""


def test_split_notes_sections_nesting_continuations() -> None:
    bs = N.split_notes(NOTES)
    texts = [b.text for b in bs]
    assert texts == [
        "Intro sentence about the meeting. It continues here.",
        "Product: a remote",
        "Goal: easy to use",
        "sub-point one",
        "sub-point two",
        "deeper",
        "lazy bullet continued on the next line",
        "Write the plan (Ana) — Due in two weeks.",
        "Send slides",
    ]
    assert [b.id for b in bs] == [f"B{i}" for i in range(1, 10)]
    by = {b.text: b for b in bs}
    assert by["Intro sentence about the meeting. It continues here."].section == "(top)"
    assert by["Intro sentence about the meeting. It continues here."].paragraph
    assert by["Product: a remote"].section == "Overview"
    assert by["sub-point one"].parent == "B3" and by["sub-point two"].parent == "B3"
    assert by["deeper"].parent == "B5"
    assert by["lazy bullet continued on the next line"].parent is None
    assert by["Send slides"].section == "Next Steps"  # an all-bold line is a heading
    assert by["Send slides"].render() == "B9 [section: Next Steps] Send slides"
    assert by["deeper"].render().startswith("B6 [section: Overview] (sub-point of B5) ")


def test_split_notes_drops_empty_items_and_remaps_parents() -> None:
    bs = N.split_notes("- **\n- parent\n  - child\n")
    assert [(b.id, b.text, b.parent) for b in bs] == [("B1", "parent", None), ("B2", "child", "B1")]
    assert N.split_notes("") == []


def test_split_notes_is_deterministic() -> None:
    assert N.split_notes(NOTES) == N.split_notes(NOTES)


# -- gold and the product transcript ----------------------------------------------------------


def _sent(section: str, text: str, i: int) -> SummarySentence:
    return SummarySentence(sentence_id=f"s{i}", section=section, text=text, links=())


def test_gold_items_numbered_per_category_abstract_dropped() -> None:
    sents = [
        _sent("abstract", "The meeting was about a remote.", 0),
        _sent("problems", "Not enough  time.", 1),
        _sent("decisions", "Use rubber.", 2),
        _sent("actions", "The PM sends slides.", 3),
        _sent("decisions", "Two colours.", 4),
    ]
    gold = N.gold_items(sents)
    assert [(g.id, g.category, g.text) for g in gold] == [
        ("D1", "decision", "Use rubber."),
        ("D2", "decision", "Two colours."),
        ("A1", "action", "The PM sends slides."),
        ("P1", "problem", "Not enough time."),
    ]
    text = N.render_gold(gold, "Roster: PM = Ana.")
    assert "Decisions:\nD1. Use rubber.\nD2. Two colours." in text
    assert text.endswith("Roster: PM = Ana.")
    assert "Problems:\nP1." in text
    assert "Actions:\n(none)" in N.render_gold(gold[:2])


def test_paragraphs_join_same_speaker_up_to_max() -> None:
    lines = [
        E.Line(0, "Them", "", "a" * 5),
        E.Line(1, "Them", "", "b" * 5),
        E.Line(2, "Ed", "", "c"),
        E.Line(3, "Them", "", "d" * 5),
        E.Line(4, "Them", "", "e" * 20),
    ]
    assert N.paragraphs(lines, max_chars=12) == [
        "Them: aaaaa bbbbb",
        "Ed: c",
        "Them: ddddd",
        "Them: " + "e" * 20,  # a long line stays whole
    ]


# -- a unit -----------------------------------------------------------------------------------


def _unit(transcript: list[E.Line] | None = None, product: str = "granola") -> N.Unit:
    gold = [
        N.GoldItem("D1", "decision", "Use rubber."),
        N.GoldItem("A1", "action", "Ana writes the plan."),
        N.GoldItem("A2", "action", "Bo sends slides."),
        N.GoldItem("P1", "problem", "Not enough time."),
    ]
    bullets = [
        N.Bullet("B1", "Decisions", "Rubber case agreed"),
        N.Bullet("B2", "Next steps", "Ana: write the plan"),
        N.Bullet("B3", "Next steps", "Everyone shares documents"),
        N.Bullet("B4", "Overview", "Team of four"),
        N.Bullet("B5", "Decisions", "Budget is 20 euros"),
    ]
    tr = (
        transcript if transcript is not None else [E.Line(0, "Them", "", "Bo will send the slides")]
    )
    ref = [E.Line(0, "Project Manager", "00:00", "We use rubber.")]
    return N.Unit("IS0000a", product, "dev", bullets, gold, "Roster: PM = Ana.", ref, tr)


def _judge_json(**over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "bullets": [
            {"id": "B1", "kind": "decision", "gold": ["D1"], "supported": "yes", "reason": "r"},
            {"id": "B2", "kind": "action", "gold": ["A1"], "supported": "partly", "reason": "r"},
            {"id": "B3", "kind": "action", "gold": [], "supported": "yes", "reason": "r"},
            {"id": "B4", "kind": "other", "gold": [], "supported": "yes", "reason": "r"},
            {"id": "B5", "kind": "decision", "gold": [], "supported": "no", "reason": "r"},
        ],
        "gold": [
            {"id": "D1", "coverage": "full", "bullets": ["B1"], "in_product_transcript": "yes",
             "quote": "x", "reason": "r"},
            {"id": "A1", "coverage": "partial", "bullets": ["B2"],
             "in_product_transcript": "no", "quote": "", "reason": "r"},
            {"id": "A2", "coverage": "none", "bullets": [], "in_product_transcript": "yes",
             "quote": "Bo will  send the slides.", "reason": "r"},
            {"id": "P1", "coverage": "none", "bullets": [], "in_product_transcript": "yes",
             "quote": "not in the transcript", "reason": "r"},
        ],
    }  # fmt: skip
    out.update(over)
    return out


def test_judge_user_blocks_and_transcript_fit(monkeypatch: Any) -> None:
    u = _unit()
    text = N.judge_user(u)
    assert "<reference_transcript>\n[L0000 Project Manager 00:00] We use rubber." in text
    assert "<product_transcript>\nThem: Bo will send the slides\n</product_transcript>" in text
    assert text.endswith("B5 [section: Decisions] Budget is 20 euros\n\nJudging pass 1.")
    assert "granola" not in text.lower()  # the judge is not told which product
    monkeypatch.setattr(N, "MAX_PRODUCT_CHARS", 5)
    assert not u.includes_transcript
    assert "Product transcript: not included" in N.judge_user(u)
    assert not _unit(transcript=[]).includes_transcript


def test_parse_valid_output() -> None:
    v = N.parse(N.JudgeOut.model_validate(_judge_json()), _unit())
    assert v.repairs == ()
    g = {x.id: x for x in v.gold}
    assert g["D1"].heard == "not_assessed" and g["D1"].quote == ""  # full: not assessed
    assert g["A2"].heard == "yes" and g["A2"].quote_found  # normalized match
    assert g["P1"].heard == "yes" and not g["P1"].quote_found
    b = {x.id: x for x in v.bullets}
    assert b["B4"].supported == "n/a"  # other: no support verdict
    assert b["B1"].section == "Decisions"
    assert b["B2"].right and b["B3"].right and not b["B5"].right
    assert N.link_disagreements(v) == 0


def test_parse_repairs() -> None:
    raw = _judge_json()
    raw["gold"] = [
        {**raw["gold"][0], "bullets": ["B1", "B1", "B9"]},
        raw["gold"][0],  # D1 twice
        {**raw["gold"][1], "in_product_transcript": "not_assessed"},
        {**raw["gold"][2], "id": "X9"},  # unknown; A2 then missing
        {**raw["gold"][3], "coverage": "partial", "bullets": []},
    ]
    raw["bullets"] = [
        {**raw["bullets"][0], "gold": ["D1", "Z1"]},
        raw["bullets"][1],
        {**raw["bullets"][2], "supported": "n/a"},
        raw["bullets"][3],
    ]  # B5 missing
    v = N.parse(N.JudgeOut.model_validate(raw), _unit())
    assert v.repairs == (
        "gold D1 twice",
        "unknown gold id X9",
        "gold D1: unknown or repeated bullet ids",
        "gold A1: transcript not assessed",
        "gold A2 missing",
        "gold P1: partial without bullets",
        "bullet B1: unknown or repeated gold ids",
        "bullet B3: action without a support verdict",
        "bullet B5 missing",
    )
    g = {x.id: x for x in v.gold}
    assert g["D1"].bullets == ("B1",) and g["A2"].coverage == "none"
    b = {x.id: x for x in v.bullets}
    assert b["B1"].gold == ("D1",) and b["B5"].kind == "other"
    assert N.link_disagreements(v) == 0


def test_parse_forces_not_assessed_without_transcript(monkeypatch: Any) -> None:
    monkeypatch.setattr(N, "MAX_PRODUCT_CHARS", 5)
    v = N.parse(N.JudgeOut.model_validate(_judge_json()), _unit())
    assert all(g.heard == "not_assessed" and not g.quote for g in v.gold)
    assert v.repairs == ()
    s = N.score_category(v, N.ALL)
    assert s.said_not_noted == 0 and s.unassessed == 3


def test_link_disagreements_counts_one_sided_links() -> None:
    raw = _judge_json()
    raw["bullets"][2] = {**raw["bullets"][2], "gold": ["A2"]}  # A2 says none
    assert N.link_disagreements(N.parse(N.JudgeOut.model_validate(raw), _unit())) == 1


# -- scoring ----------------------------------------------------------------------------------


def test_score_categories() -> None:
    v = N.parse(N.JudgeOut.model_validate(_judge_json()), _unit())
    sc = N.score_unit(v)
    d, a, p, al = sc["decision"], sc["action"], sc["problem"], sc["all"]
    assert (d["n_gold"], d["full"], d["recall"], d["n_bullets"], d["right"]) == (1, 1, 1.0, 2, 1)
    assert d["precision"] == 0.5 and d["precision_true"] == 0.5 and d["precision_gold"] == 0.5
    assert a["recall"] == 0.25 and a["recall_full"] == 0.0  # A1 partial, A2 none
    assert a["precision"] == 1.0 and a["precision_true"] == 0.5  # B2 gold but partly
    assert a["f1"] == 2 * 1.0 * 0.25 / 1.25
    assert a["said_not_noted"] == 1 and a["said_not_noted_quoted"] == 1
    assert p["n_bullets"] == 0 and p["precision"] is None and p["f1"] == 0.0
    assert p["said_not_noted"] == 1 and p["said_not_noted_quoted"] == 0
    assert al["n_gold"] == 4 and al["recall"] == (1 + 0.5) / 4 and al["n_bullets"] == 4
    assert al["right"] == 3 and al["supported_no"] == 1 and al["supported_partly"] == 1


def test_f1_none_without_gold() -> None:
    s = N.CatScore(0, 0, 0, 2, 1, 0, 1, 0, 1, 0, 0, 0, 0)
    assert s.recall is None and s.f1 is None and s.precision == 0.5


def _record(mid: str, product: str, split: str, recall: float, precision: float) -> dict[str, Any]:
    row = {k: 0 for k in N._COUNTS}
    row.update(n_gold=4, full=round(recall * 4), n_bullets=2, right=round(precision * 2))
    s = N.CatScore(**row)
    return {
        "meeting_id": mid,
        "product": product,
        "split": split,
        "product_transcript_included": True,
        "repairs": [],
        "link_disagreements": 0,
        "scores": {c: s.row() for c in (*N.CATEGORIES, N.ALL)},
    }


def test_summarize_pairs_products_by_meeting() -> None:
    recs = [
        _record("M1", "granola", "dev", 0.5, 1.0),
        _record("M1", "wispr", "dev", 0.75, 0.5),
        _record("M2", "granola", "test", 0.25, 1.0),
        _record("M2", "wispr", "test", 0.5, 1.0),
        _record("M3", "granola", "test", 0.5, 0.5),  # Wispr missing: unpaired
    ]
    out = N.summarize(recs, seed=1)
    assert set(out) == {"dev", "test", "all"}
    g = out["all"]["products"]["granola"]["all"]
    assert g["n_meetings"] == 3 and g["micro"]["n_gold"] == 12
    assert g["mean_recall"] == (0.5 + 0.25 + 0.5) / 3
    t = out["all"]["paired"]["wispr vs granola: all"]
    assert t["recall"]["n"] == 2 and t["recall"]["wins"] == 2
    assert t["precision"] == {"n": 2, "wins": 0, "losses": 1, "ties": 1, "sign_p": 1.0}
    assert N.summarize(recs, seed=1) == out  # deterministic
    agg = N.aggregate({"meta": {"x": 1, "ledger_by_stage": {}}, "summary": out})
    assert agg["meta"] == {"x": 1}


# -- quote, reprice, end to end ---------------------------------------------------------------


class Fake:
    """Returns the canned judge output for every call."""

    def __init__(self) -> None:
        self.calls: list[LLMRequest] = []

    def complete(self, request: LLMRequest, model: str) -> Completion:
        self.calls.append(request)
        return Completion(json.dumps(_judge_json()), model, 10_000, 1_500, "end_turn")


def test_run_unit_end_to_end_and_replay(tmp_path: Path) -> None:
    fake = Fake()
    judge = LLMClient(fake, model=N.JUDGE_MODEL, cache_dir=tmp_path, ledger=Ledger())
    rec = N.run_unit(_unit(), judge)
    assert len(fake.calls) == 1
    req = fake.calls[0]
    assert req.prompt_id == N.PROMPT and req.stage == N.STAGE
    assert list(req.schema["properties"]) == ["bullets", "gold"]  # bullets judged first
    assert rec["scores"]["all"]["n_gold"] == 4 and rec["repairs"] == []
    assert rec["product_transcript_included"]
    again = N.run_unit(_unit(), judge)  # replay from cache: no new call, same record
    assert len(fake.calls) == 1 and again == rec
    assert judge.ledger.spent_usd() == cost_usd(N.JUDGE_MODEL, 10_000, 1_500)
    texts = N.private_texts([rec])
    assert "Ana: write the plan" in texts and "Bo will  send the slides." in texts


def test_quote_and_reprice(tmp_path: Path) -> None:
    judge = LLMClient(Fake(), model=N.JUDGE_MODEL, cache_dir=tmp_path, ledger=Ledger())
    u = _unit()

    def counter(texts: list[str]) -> list[int]:
        return [1000 for _ in texts]

    q = N.quote([u], judge, counter)
    n = N.items_of(u)
    assert (q.calls, q.cached, q.input_tokens, q.items) == (1, 0, 1000 + N.OVERHEAD, n)
    exp = N.ASSUMED_OUT_BASE[0] + N.ASSUMED_OUT_PER_ITEM[0] * n
    assert q.usd_expected == cost_usd(N.JUDGE_MODEL, 1000 + N.OVERHEAD, round(exp))
    assert q.usd_high > q.usd_expected
    N.run_unit(u, judge)
    q2 = N.quote([u], judge, counter)
    assert (q2.cached, q2.usd_expected, q2.usd_high) == (1, 0.0, 0.0)

    ledger = [
        {"stage": N.STAGE, "model": N.JUDGE_MODEL, "input_tokens": 10_000,
         "output_tokens": 2_000, "cost_usd": cost_usd(N.JUDGE_MODEL, 10_000, 2_000)},
        {"stage": "other", "model": N.JUDGE_MODEL, "input_tokens": 1, "output_tokens": 1,
         "cost_usd": 9.0},
    ]  # fmt: skip
    r = N.reprice(ledger, 10_000, 10, 30_000, 40)
    assert r["pilot_calls"] == 1 and r["scale_input"] == 3.0 and r["scale_output"] == 4.0
    want = cost_usd(N.JUDGE_MODEL, 30_000, 0) + cost_usd(N.JUDGE_MODEL, 0, 8_000)
    assert abs(r["full_usd"] - want) < 1e-12
    assert abs(r["full_usd_with_margin"] - want * N.MARGIN) < 1e-12


def test_make_unit_from_ami_meeting() -> None:
    words = (Word(agent="A", start=0.0, end=1.0, text="hi"),)
    acts = (
        DialogueAct(da_id="a1", agent="A", start=0.0, end=1.0, text="We use rubber."),
        DialogueAct(da_id="a2", agent="A", start=2.0, end=3.0, text="Agreed."),
    )
    m = AmiMeeting(
        meeting_id="IS1008a",
        type="scenario",
        duration_s=10,
        speakers=(Speaker(agent="A", role="PM", participant="P1", channel=0),),
        words=words,
        summary={},
        dialogue_acts=acts,
        sentences=(_sent("decisions", "Use rubber.", 1), _sent("abstract", "About.", 2)),
    )
    note = meeting_note(m).model_copy(update={"summary_markdown": "# D\n- Rubber agreed"})
    u = N.make_unit(m, note, "wispr", "Roster.")
    assert u.split == "dev" and u.key == "IS1008a:wispr"
    assert [g.id for g in u.gold] == ["D1"]
    assert [b.text for b in u.bullets] == ["Rubber agreed"]
    assert len(u.reference) == 1  # one speaker's acts merged into one turn
    assert u.reference[0].speaker == "Project Manager"
    assert [x.speaker for x in u.transcript] == ["Project Manager", "Project Manager"]
    other = m.model_copy(update={"meeting_id": "IS1008b"})
    assert N.make_unit(other, note, "granola").split == "test"


# -- passes, prompt-cache breakpoints, combining ------------------------------------------------


def test_judge_request_breaks_and_pass_markers(tmp_path: Path) -> None:
    u = _unit()
    shared, product, marker = N.judge_parts(u, 2)
    req = N.judge_request(u, 2)
    assert req.user == shared + product + marker and marker == "\n\nJudging pass 2."
    assert req.cache_breaks == (len(shared), len(shared) + len(product))
    blocks = text_blocks(req.user, req.cache_breaks, "5m")
    assert blocks[0]["text"].endswith("</reference_transcript>\n\n")
    assert blocks[1]["text"].startswith("<product_transcript>") and "cache_control" in blocks[1]
    assert blocks[2] == {"type": "text", "text": marker}  # the varying tail: never cached
    other = _unit(product="wispr")
    other.transcript = [E.Line(0, "Ed", "", "different")]
    assert N.judge_parts(other, 1)[0] == shared  # both products share the meeting part
    judge = LLMClient(Fake(), model=N.JUDGE_MODEL, cache_dir=tmp_path, ledger=Ledger())
    assert judge.cache_path(N.judge_request(u, 1)) != judge.cache_path(N.judge_request(u, 2))


def _pass(rec: dict[str, Any], k: int, **gold_cov: str) -> dict[str, Any]:
    """``rec`` as pass ``k`` with some gold coverages changed (scores recomputed)."""
    out = json.loads(json.dumps(rec))
    out["pass"] = k
    for g in out["gold_verdicts"]:
        if g["id"] in gold_cov:
            g["coverage"] = gold_cov[g["id"]]
            if g["coverage"] == "full":
                g["heard"], g["quote_found"] = "not_assessed", False
    v = N.Verdicts(
        tuple(N.GoldVerdict(**{**g, "bullets": tuple(g["bullets"])}) for g in out["gold_verdicts"]),
        tuple(N.BulletVerdict(**{**b, "gold": tuple(b["gold"])}) for b in out["bullet_verdicts"]),
        (),
    )
    out["scores"] = N.score_unit(v)
    return out


def test_combine_mean_spread_and_majority(tmp_path: Path) -> None:
    judge = LLMClient(Fake(), model=N.JUDGE_MODEL, cache_dir=tmp_path, ledger=Ledger())
    base = N.run_unit(_unit(), judge, 1)
    passes = [base, _pass(base, 2, A1="full"), _pass(base, 3, A1="none", A2="full")]
    r = N.combine(passes)
    assert r["n_passes"] == 3 and [p["pass"] for p in r["passes"]] == [1, 2, 3]
    act = [p["scores"]["action"]["recall"] for p in passes]
    assert act == [0.25, 0.5, 0.5]
    assert r["scores"]["action"]["recall"] == sum(act) / 3
    sp = r["spread"]["action"]["recall"]
    assert (sp["min"], sp["max"], sp["range"]) == (0.25, 0.5, 0.25)
    assert r["spread"]["problem"]["precision"] is None  # no problem bullets in any pass
    # Majority: A1 partial / full / none -> median partial; A2 none / none / full -> none.
    mv = N.majority_verdicts(passes)
    cov = {g.id: g.coverage for g in mv.gold}
    assert cov == {"D1": "full", "A1": "partial", "A2": "none", "P1": "none"}
    a2 = next(g for g in mv.gold if g.id == "A2")
    assert a2.heard == "yes" and a2.quote_found  # the two "none" passes heard it, quote found
    assert r["majority"]["action"]["recall"] == 0.25
    assert {b.id: b.supported for b in mv.bullets}["B2"] == "partly"
    assert N.combine(passes) == r  # deterministic


def test_vote_and_majority_ids() -> None:
    assert N._vote(["no", "yes", "yes"], ("no", "yes")) == "yes"
    assert N._vote(["yes", "no"], ("no", "yes")) == "no"  # tie: earliest in order
    assert N._majority_ids([["B1", "B2"], ["B2"], ["B3", "B2"]]) == ("B2",)
    assert N._majority_ids([["B1"], ["B1"], []]) == ("B1",)


def test_summaries_over_combined_units(tmp_path: Path) -> None:
    judge = LLMClient(Fake(), model=N.JUDGE_MODEL, cache_dir=tmp_path, ledger=Ledger())
    base = N.run_unit(_unit(), judge, 1)
    g = N.combine([base, _pass(base, 2, A1="full")])
    w_base = {**base, "product": "wispr"}
    w = N.combine([w_base, _pass(w_base, 2, A2="partial")])
    recs = [g, w]
    s = N.summarize(recs, seed=3)
    assert s["dev"]["products"]["granola"]["action"]["micro"]["full"] == 0.5  # pass mean
    m = N.summarize(recs, seed=3, key="majority")
    assert m["dev"]["products"]["granola"]["action"]["n_meetings"] == 1
    ps = N.pass_spread(recs)
    pooled = ps["granola"]["action"]["pooled"]["micro_recall"]
    assert pooled["per_pass"] == [0.25, 0.5] and pooled["spread"]["range"] == 0.25
    assert ps["wispr"]["action"]["unit"]["recall"]["max_range"] == 0.25  # A2 none -> partial
    agg = N.aggregate({"meta": {}, "summary": s, "pass_spread": ps, "records": recs})
    assert [u["product"] for u in agg["units"]] == ["granola", "wispr"]
    assert agg["units"][0]["range"]["action"]["recall"] == 0.25
    pa = agg["pass_agreement"]["granola"]
    assert (pa["gold_items"], pa["gold_unanimous"], pa["gold_three_labels"]) == (4, 3, 0)
    assert pa["bullets"] == 5 and pa["kind_unanimous"] == 5 and pa["dap_bullets"] == 4
    assert "Ana: write the plan" in N.private_texts(recs)


def test_cost_summary_counts_live_calls_and_cache_saving() -> None:
    m = N.JUDGE_MODEL
    rows = [
        {"stage": N.STAGE, "model": m, "cached": False, "input_tokens": 100,
         "cache_read_tokens": 9000, "cache_write_tokens": 0, "output_tokens": 1000,
         "cost_usd": cost_usd(m, 100, 1000, 9000, 0)},
        {"stage": N.STAGE, "model": m, "cached": True, "input_tokens": 1,
         "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 1, "cost_usd": 5.0},
    ]  # fmt: skip
    c = N.cost_summary(rows)
    live = c["live"]
    assert (live["calls"], c["replayed_calls"], live["cache_read_tokens"]) == (1, 1, 9000)
    assert live["usd_without_prompt_cache"] == cost_usd(m, 9100, 1000)
    assert live["prompt_cache_saving_usd"] > 0
    assert c["all_calls"]["calls"] == 2 and c["all_calls"]["usd"] == live["usd"] + 5.0


def test_quote_with_passes_simulates_prompt_cache(tmp_path: Path) -> None:
    judge = LLMClient(Fake(), model=N.JUDGE_MODEL, cache_dir=tmp_path, ledger=Ledger())
    u = _unit()
    w = _unit(transcript=[E.Line(0, "Ed", "", "another transcript")], product="wispr")

    def counter(texts: list[str]) -> list[int]:
        return [max(1, len(t)) * 10 for t in texts]  # big enough to pass the cache minimum

    q = N.quote([u, w], judge, counter, passes=3)
    assert (q.calls, q.cached) == (6, 0)
    assert q.cache_read_tokens > 0 and q.cache_write_tokens > 0
    assert q.usd_expected_cached < q.usd_expected and q.usd_high_cached < q.usd_high
    N.run_unit(u, judge, 2)
    assert N.quote([u, w], judge, counter, passes=3).cached == 1
