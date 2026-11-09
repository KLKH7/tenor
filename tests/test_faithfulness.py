"""Notes faithfulness track: claim splitting, pre-check, bleed, verdicts and aggregation.

All offline: synthetic notes, a scripted fake backend, no network.
"""

from __future__ import annotations

import json

import pytest

from tenor.blackbox.kit import check_committable
from tenor.evals import faithfulness as A
from tenor.llm.client import Completion, LLMClient, LLMRequest, structured_params
from tests.memory_helpers import make_note

NOTES = """# Pricing review

- Team agreed to move the launch to Q3
- Options discussed:
  - Keep the free tier
  - Raise the price to $25
- **Marcus Chen**

  Will send the deck by Friday. He owns the follow-up.

# Next steps

1. Priya to draft the email, e.g. for the board. Then share it.
- Second top-level bullet
"""


def test_split_drops_headings_and_lead_ins_and_keeps_them_as_context() -> None:
    sp = A.split_claims(NOTES)
    texts = [c.text for c in sp.claims]
    assert texts == [
        "Team agreed to move the launch to Q3",
        "Keep the free tier",
        "Raise the price to $25",
        "Will send the deck by Friday.",
        "He owns the follow-up.",
        "Priya to draft the email, e.g. for the board.",
        "Then share it.",
        "Second top-level bullet",
    ]
    assert (sp.headings, sp.lead_ins) == (2, 2)
    by_text = {c.text: c for c in sp.claims}
    assert by_text["Keep the free tier"].context == "Options discussed"
    assert by_text["Will send the deck by Friday."].context == "Marcus Chen"
    assert by_text["Then share it."].section == "Next steps"
    # A lead-in stops applying at the next item at its own depth.
    assert by_text["Team agreed to move the launch to Q3"].context == ""
    assert by_text["Second top-level bullet"].context == ""
    assert [c.claim_id for c in sp.claims][:2] == ["c001", "c002"]


def test_strip_markdown_and_sentences() -> None:
    assert (
        A.strip_markdown("**Bold** and [a link](http://x) and `code`") == "Bold and a link and code"
    )
    assert A.sentences("Ship it vs. wait. Decide Monday! Ok?") == [
        "Ship it vs. wait.",
        "Decide Monday!",
        "Ok?",
    ]


TRANSCRIPT = (
    "we said twenty five dollars a seat and maybe five thousand users by the third of june "
    "marcus will check it on thursday and we can revisit in Q2 with 30 percent"
)


@pytest.mark.parametrize(
    ("claim", "numbers", "dates"),
    [
        ("Price is $25 per seat", (), ()),  # spelled out in speech
        ("Target 5k users", (), ()),  # 5k = five thousand
        ("Target 5,000 users by June 3", (), ()),  # ordinal "third"
        ("Revisit in Q2 at 30%", (), ()),  # Q2 is a quarter, 30 is spoken as digits
        ("Ship on June 3rd", (), ()),  # an ordinal suffix is still the number 3
        ("Price is $40 per seat", ("40",), ()),
        ("Check it on Friday", (), ("friday",)),
        ("Check it on Thursdays", (), ()),
        ("Launch in October", (), ("october",)),
        ("We may revisit", (), ()),  # "may" the verb is not a month
    ],
)
def test_precheck_numbers_and_dates(
    claim: str, numbers: tuple[str, ...], dates: tuple[str, ...]
) -> None:
    pc = A.precheck(claim, TRANSCRIPT)
    assert (pc.numbers, pc.dates) == (numbers, dates)


def test_precheck_names_split_spoken_calendar_and_unknown() -> None:
    ctx = "Title: Pricing\nInvited: Priya Raman, Marcus Chen"
    assert A.precheck("Follow-up owned by Marcus", TRANSCRIPT, ctx).names == ()
    pc = A.precheck("Follow-up owned by Priya", TRANSCRIPT, ctx)
    assert (pc.names, pc.names_calendar_only, pc.flagged) == ((), ("priya",), False)
    pc = A.precheck("Follow-up owned by Dana", TRANSCRIPT, ctx)
    assert (pc.names, pc.flagged) == (("dana",), True)
    # The first word of a sentence and common acronyms are not names.
    assert A.precheck("Pricing was set. The API is ready", TRANSCRIPT).names == ()
    # Only the capitalised half of a hyphenated compound is a name candidate.
    assert A.precheck("An AI-generated, Marcus-style deck", TRANSCRIPT).names == ()
    assert A.precheck("An Acme-style deck", TRANSCRIPT).names == ("acme",)


def _note() -> A.MeetingNote:
    lines = [
        ("me", None, "I think we should move the launch to Q3"),
        ("them", None, "Agreed, Q3 it is, I will send the deck by Friday"),
        ("them", None, "The free tier stays"),
    ]
    base = make_note("n1", "2026-03-02", "Pricing", lines)
    return base.model_copy(update={"summary_markdown": NOTES})


def test_check_request_puts_transcript_in_cached_system_and_claims_in_user() -> None:
    note = _note()
    claims = A.split_claims(NOTES).claims
    req = A.check_request(note, claims)
    assert "[1] Them: Agreed, Q3 it is" in req.system
    assert "Note owner (Me): Priya Raman" in req.system and "Marcus Chen" in req.system
    assert "c004 [Pricing review > Marcus Chen]: Will send the deck by Friday." in req.user
    assert req.cache_system and req.effort == "low"
    params = structured_params(req, "m")
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["output_config"]["format"]["schema"] is A.AUDIT_SCHEMA


def test_cache_flag_leaves_existing_cache_keys_unchanged(tmp_path: object) -> None:
    client = LLMClient(model="m", cache_dir=tmp_path)  # type: ignore[arg-type]
    plain = LLMRequest("s", "p.v1", "sys", "user", {})
    # Caching off: the system prompt goes as a plain string; by default it is a breakpoint.
    off = LLMRequest("s", "p.v1", "sys", "user", {}, cache_ttl=None)
    assert structured_params(off, "m")["system"] == "sys"
    assert structured_params(plain, "m")["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert client.cache_path(off) == client.cache_path(plain)  # markers are not in the key
    cached = LLMRequest("s", "p.v1", "sys", "user", {}, cache_system=True)
    assert client.cache_path(plain) != client.cache_path(cached)
    # The key of a plain request is the one it had before the flag existed.
    import hashlib

    payload = {
        "model": "m",
        "prompt_id": "p.v1",
        "system": "sys",
        "user": "user",
        "schema": {},
        "max_tokens": 16000,
        "effort": "medium",
    }
    want = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    assert client.cache_path(plain).stem == want


def _system(*extra: A.MeetingNote) -> A.NotesSystem:
    note = _note()
    pool = ((note, NOTES), *((n, n.summary_markdown or "") for n in extra))
    return A.NotesSystem("toy", "test system", False, ((note, NOTES),), pool)


def _meeting() -> A.Meeting:
    return A.prepare(_system())[0]


def _reply(*verdicts: tuple[str, str, str, list[int]]) -> A.AuditReply:
    return A.AuditReply(
        verdicts=[
            A.ClaimVerdict(claim_id=c, reason="r", label=lab, claim_type=t, lines=ls)  # type: ignore[arg-type]
            for c, lab, t, ls in verdicts
        ]
    )


def test_apply_verdicts_requires_a_valid_line_for_supported() -> None:
    m = _meeting()
    reply = _reply(
        ("c001", "supported", "decision", [0, 1]),
        ("c002", "supported", "fact", [99]),  # out of range: no valid citation
        ("c003", "inferred", "fact", []),
        ("c001", "contradicted", "fact", []),  # duplicate id: ignored
        ("c999", "supported", "fact", [0]),  # unknown id: ignored
    )
    left = A.apply_verdicts(m.records, reply, len(m.note.utterances))
    r1, r2, r3 = m.records[:3]
    assert (r1.label, r1.lines) == ("supported", [0, 1])
    assert (r2.label, r2.uncited_support) == ("unsupported", True)
    assert r3.label == "inferred"
    assert left == [r.claim.claim_id for r in m.records[3:]]
    A.apply_verdicts(m.records[:1], _reply(("c001", "inferred", "fact", [])), 3, "recheck")
    A.apply_verdicts(m.records[:1], _reply(("c001", "supported", "fact", [1])), 3, "strict")
    assert (r1.label, r1.recheck_label, r1.strict_label, r1.strict_lines) == (
        "supported",
        "inferred",
        "supported",
        [1],
    )


def test_recheck_sample_is_seeded_and_thirty_percent() -> None:
    recs = _meeting().records * 4  # 32 records
    a = A.recheck_sample(recs, seed=1)
    assert len(a) == 10  # ceil(0.3 * 32)
    assert [id(r) for r in a] == [id(r) for r in A.recheck_sample(recs, seed=1)]


def test_requests_one_call_per_meeting_and_kind() -> None:
    m = _meeting()
    reqs = A.requests([m], seed=0)
    assert [k for *_, k in reqs] == ["check", "recheck", "strict"]
    assert [len(rs) for _, _, rs, _ in reqs] == [8, 3, 8]
    assert {r.stage for _, r, _, _ in reqs} == {
        "faithfulness.check",
        "faithfulness.recheck",
        "faithfulness.strict",
    }


def test_wilson_and_rates() -> None:
    lo, hi = A.wilson(0, 100)
    assert lo == 0.0 and 0.03 < hi < 0.04
    r = A.rates(["supported"] * 8 + ["inferred", "contradicted"])
    assert r["n"] == 10 and r["supported"]["k"] == 8 and r["ungrounded"]["k"] == 2
    assert r["unsupported"]["rate"] == 0.0


def test_aggregate_is_committable_and_counts_right() -> None:
    m = _meeting()
    labels = ["supported", "inferred", "supported", "contradicted"] + ["supported"] * 4
    types = ["decision", "action_item", "fact", "decision"] + ["fact"] * 4
    for r, lab, t in zip(m.records, labels, types, strict=True):
        r.label, r.claim_type, r.lines = lab, t, [0]  # type: ignore[assignment]
        r.strict_label, r.strict_type = "supported", t  # type: ignore[assignment]
    m.records[0].recheck_label = "inferred"
    m.records[1].recheck_label = "inferred"
    agg = A.aggregate(m.records, {"M1": m.split})
    assert agg["claims_total"] == 8 and agg["by_meeting"]["M1"]["claims"] == 8
    assert agg["overall"]["ungrounded"]["k"] == 2
    assert agg["by_type"]["decision"]["contradicted"]["k"] == 1
    assert agg["types_ranked_by_ungrounded"][0]["type"] in {"decision", "action_item"}
    rel = agg["reliability"]
    assert rel["check_vs_recheck_sample"]["n"] == 2
    assert rel["check_vs_recheck_sample"]["confusion"] == {
        "inferred->inferred": 1,
        "supported->inferred": 1,
    }
    assert rel["check_vs_strict_all"]["n"] == 8
    assert agg["strict"]["overall"]["supported"]["k"] == 8
    check_committable(agg, [r.claim.text for r in m.records])
    assert "Marcus" not in json.dumps(agg)


def test_compare_kappa_matches_definition() -> None:
    c = A.compare(
        ["supported", "supported", "inferred", "inferred"], ["supported"] * 3 + ["inferred"]
    )
    assert c["agreement_4way"] == 0.75 and c["kappa_4way"] == 0.5


def test_nearest_lines_and_spotcheck_lists_only_non_supported() -> None:
    m = _meeting()
    assert A.nearest_lines("send the deck Friday", m.note.utterances, k=1) == [1]
    assert A.nearest_lines("zebra", m.note.utterances) == []
    for r in m.records:
        r.label = r.strict_label = "supported"
    m.records[1].strict_label = "inferred"
    md = A.spotcheck_markdown(m.records, {"M1": m.note}, seed=0)
    assert md.count("\n## ") == 1 and "Keep the free tier" in md
    assert "[2] Them: The free tier stays" in md


class _FakeBackend:
    def complete(self, request: LLMRequest, model: str) -> Completion:
        ids = [line.split(" ", 1)[0] for line in request.user.splitlines()[1:]]
        verdicts = [
            {"claim_id": i, "reason": "x", "label": "supported", "claim_type": "fact", "lines": [0]}
            for i in ids
        ]
        return Completion(json.dumps({"verdicts": verdicts}), model, 100, 50, "end_turn")


def test_end_to_end_with_fake_backend(tmp_path: object) -> None:
    m = _meeting()
    client = LLMClient(_FakeBackend(), model="claude-haiku-5-5", cache_dir=tmp_path)  # type: ignore[arg-type]
    for _m, req, recs, kind in A.requests([m], seed=0):
        assert A.apply_verdicts(recs, client.structured(req, A.AuditReply), 3, kind) == []
    agg = A.aggregate(m.records, {"M1": m.split})
    assert agg["overall"]["supported"]["rate"] == 1.0
    assert agg["reliability"]["check_vs_strict_all"]["agreement_4way"] == 1.0


def test_cross_meeting_terms_names_numbers_and_rare_words() -> None:
    own = "we will ship the beta on friday"
    others = {
        "P1": "Dana owns the migration plan, budget 4200",
        "P2": "the migration is late",
        "P3": "nothing relevant here",
        "P4": "also nothing",
    }
    got = A.cross_meeting_terms("Ship the beta with Dana; budget 4200", own, others)
    # "budget" is a rare word too: it appears in only one other meeting.
    assert got == {"P1": ["number:4200", "word:budget", "name:dana"]}
    # A long word counts only if rare: "migration" is in 2 of 4 others (cap is 1).
    assert A.cross_meeting_terms("Plan the migration", own, others) == {}
    assert A.cross_meeting_terms("Ship the beta on Friday", own, others) == {}  # all own


def test_prepare_runs_bleed_against_the_pool_and_aggregates_it() -> None:
    other = make_note("n2", "2026-03-03", "Other", [("them", None, "Zanzibar is the codename")])
    other = other.model_copy(update={"summary_markdown": "- Codename Zanzibar chosen"})
    note = _note().model_copy(
        update={"summary_markdown": "- Launch under the Zanzibar codename\n- Move to Q3"}
    )
    system = A.NotesSystem(
        "toy", "t", False, ((note, note.summary_markdown or ""),), ((note, ""), (other, ""))
    )
    m = A.prepare(system)[0]
    first, second = m.records
    assert (
        first.cross_meeting == {"P2": ["word:codename", "name:zanzibar"]}
        and second.cross_meeting == {}
    )
    first.label, second.label = "unsupported", "supported"
    bleed = A.aggregate(m.records, {"M1": m.split})["cross_meeting_bleed"]
    assert bleed["ungrounded_claims"]["k"] == 1 and bleed["ungrounded_claims_strong"]["k"] == 1
    assert bleed["all_claims_baseline"]["n"] == 2


def test_northwind_adapter_is_seeded_and_uses_summaries() -> None:
    notes = [
        make_note(f"n{i}", f"2026-03-{i + 1:02d}", f"T{i}", [("me", None, "hi")]) for i in range(6)
    ]
    a = A.northwind_system(notes, 3, seed=1)
    assert len(a.entries) == 3 and len(a.pool) == 6 and not a.private
    assert [n.note_id for n, _ in a.entries] == [
        n.note_id for n, _ in A.northwind_system(notes, 3, seed=1).entries
    ]
    assert all(md == n.summary_markdown for n, md in a.entries)
