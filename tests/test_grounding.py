"""Grounding checker and its AMI validation: offline, a tiny NXT zip and a scripted fake LLM."""

from __future__ import annotations

import json
import random
import zipfile
from pathlib import Path

import pytest

from tenor.ami.corpus import load_meeting, meeting_note
from tenor.core.models import Channel
from tenor.evals import grounding as G
from tenor.evals import grounding_ami as V
from tenor.evals.faithfulness import Claim
from tenor.llm.client import Completion, LLMClient, LLMRequest
from tests.memory_helpers import make_note

NS = 'xmlns:nite="http://nite.sourceforge.net/"'
MEETINGS = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<nite:root {NS}>
 <meeting observation="XX0001a" type="scenario" duration="20">
  <speaker nxt_agent="A" role="PM" global_name="P1" channel="0"/>
  <speaker nxt_agent="B" role="ME" global_name="P2" channel="1"/>
 </meeting>
</nite:root>"""
WORDS_A = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<nite:root {NS}>
 <w nite:id="XX0001a.A.words0" starttime="1.0" endtime="1.2">Budget</w>
 <w nite:id="XX0001a.A.words1" starttime="1.2" endtime="1.4">is</w>
 <w nite:id="XX0001a.A.words2" starttime="1.4" endtime="1.6">fixed</w>
 <w nite:id="XX0001a.A.words3" starttime="1.6" endtime="1.6" punc="true">.</w>
 <vocalsound nite:id="XX0001a.A.words4" starttime="1.7" endtime="1.8" type="laugh"/>
 <w nite:id="XX0001a.A.words5" starttime="5.0" endtime="5.2" trunc="true">th</w>
 <w nite:id="XX0001a.A.words6" starttime="5.2" endtime="5.5">Okay</w>
</nite:root>"""
WORDS_B = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<nite:root {NS}>
 <w nite:id="XX0001a.B.words0" starttime="3.0" endtime="3.3">Yes</w>
</nite:root>"""
ACTS_A = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<nite:root {NS}>
 <dact nite:id="XX0001a.A.da.1">
  <nite:child href="XX0001a.A.words.xml#id(XX0001a.A.words0)..id(XX0001a.A.words4)"/>
 </dact>
 <dact nite:id="XX0001a.A.da.2">
  <nite:child href="XX0001a.A.words.xml#id(XX0001a.A.words5)..id(XX0001a.A.words6)"/>
 </dact>
</nite:root>"""
ACTS_B = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<nite:root {NS}>
 <dact nite:id="XX0001a.B.da.1"><nite:child href="XX0001a.B.words.xml#id(XX0001a.B.words0)"/></dact>
</nite:root>"""
SUMMARY = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<nite:root {NS}>
 <abstract><sentence nite:id="XX0001a.s.1">The Project Manager fixed the budget.</sentence>
 </abstract>
 <decisions><sentence nite:id="XX0001a.s.2">The budget will not change.</sentence></decisions>
</nite:root>"""
LINKS = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<nite:root {NS}>
 <summlink>
  <nite:pointer role="extractive" href="XX0001a.A.dialog-act.xml#id(XX0001a.A.da.1)"/>
  <nite:pointer role="abstractive" href="XX0001a.abssumm.xml#id(XX0001a.s.1)"/>
 </summlink>
 <summlink>
  <nite:pointer role="extractive" href="XX0001a.B.dialog-act.xml#id(XX0001a.B.da.1)"/>
  <nite:pointer role="abstractive" href="XX0001a.abssumm.xml#id(XX0001a.s.1)"/>
 </summlink>
</nite:root>"""


@pytest.fixture
def ami_zip(tmp_path: Path) -> Path:
    path = tmp_path / "ami.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("corpusResources/meetings.xml", MEETINGS)
        zf.writestr("words/XX0001a.A.words.xml", WORDS_A)
        zf.writestr("words/XX0001a.B.words.xml", WORDS_B)
        zf.writestr("dialogueActs/XX0001a.A.dialog-act.xml", ACTS_A)
        zf.writestr("dialogueActs/XX0001a.B.dialog-act.xml", ACTS_B)
        zf.writestr("abstractive/XX0001a.abssumm.xml", SUMMARY)
        zf.writestr("extractive/XX0001a.summlink.xml", LINKS)
    return path


def test_dialogue_acts_and_summary_links(ami_zip: Path) -> None:
    m = load_meeting("XX0001a", ami_zip)
    # A range may end on a non-word event; punctuation is glued on; fragments are dropped.
    assert [(a.da_id, a.text) for a in m.dialogue_acts] == [
        ("XX0001a.A.da.1", "Budget is fixed."),
        ("XX0001a.B.da.1", "Yes"),
        ("XX0001a.A.da.2", "Okay"),
    ]
    assert m.dialogue_acts[2].start == 5.0 and m.has_summlinks
    s1, s2 = m.sentences
    assert (s1.section, s1.links) == ("abstract", ("XX0001a.A.da.1", "XX0001a.B.da.1"))
    assert (s2.section, s2.links) == ("decisions", ())
    assert m.summary == {
        "abstract": ("The Project Manager fixed the budget.",),
        "decisions": ("The budget will not change.",),
    }


def test_untimed_word_does_not_break_time_order(tmp_path: Path) -> None:
    """AMI's TS3011d has one full stop with no times, opening a dialogue act: the act takes the
    span of its timed words and both lists stay sorted."""
    untimed = '<w nite:id="XX0001a.A.words5" punc="true">.</w>'
    words_a = "\n".join(untimed if "words5" in ln else ln for ln in WORDS_A.splitlines())
    assert untimed in words_a
    path = tmp_path / "ami.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("corpusResources/meetings.xml", MEETINGS)
        zf.writestr("words/XX0001a.A.words.xml", words_a)
        zf.writestr("words/XX0001a.B.words.xml", WORDS_B)
        zf.writestr("dialogueActs/XX0001a.A.dialog-act.xml", ACTS_A)
        zf.writestr("dialogueActs/XX0001a.B.dialog-act.xml", ACTS_B)
    m = load_meeting("XX0001a", path)
    assert [w.text for w in m.words] == ["Budget", "is", "fixed", ".", ".", "Yes", "Okay"]
    assert [a.agent for a in m.dialogue_acts] == ["A", "B", "A"]
    assert (m.dialogue_acts[2].start, m.dialogue_acts[2].end) == (5.2, 5.5)


def test_meeting_note_from_ami(ami_zip: Path) -> None:
    note = meeting_note(load_meeting("XX0001a", ami_zip))
    u = note.utterances
    assert [x.utterance_id for x in u] == ["XX0001a:0000", "XX0001a:0001", "XX0001a:0002"]
    assert {x.channel for x in u} == {Channel.THEM}
    assert [x.speaker_name for x in u] == ["Project Manager", "Marketing Expert", "Project Manager"]
    assert note.summary_markdown == (
        "## Abstract\n- The Project Manager fixed the budget.\n"
        "## Decisions\n- The budget will not change."
    )


# -- the checker ---------------------------------------------------------------------------------


def _note() -> object:
    return make_note(
        "n1",
        "2026-01-05",
        "Pricing",
        [
            ("me", None, "Morning all."),
            ("them", None, "We agreed to raise the price to twenty five dollars."),
            ("them", None, "Weather is nice."),
            ("me", None, "Dana will send the pricing deck on Friday."),
            ("them", None, "Thanks."),
        ],
    )


class _Fake:
    """Excerpt pass: c001 supported (line 1), c002 unsupported. Full pass: c002 contradicted
    (line 3) and c003 supported with no line (must become unsupported)."""

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest, model: str) -> Completion:
        self.requests.append(request)
        full = "<scope>full</scope>" in request.system
        ids = [line.split(" ", 1)[0] for line in request.user.splitlines()[1:]]
        table = {
            False: {"c001": ("supported", [1, 99]), "c002": ("unsupported", [])},
            True: {"c002": ("contradicted", [3]), "c003": ("supported", [])},
        }[full]
        verdicts = [
            {"claim_id": i, "reason": "r", "label": table[i][0], "lines": table[i][1]}
            for i in ids
            if i in table
        ]
        return Completion(json.dumps({"verdicts": verdicts}), model, 100, 20, "end_turn")


def test_plan_retrieves_candidates_and_excerpt_marks_gaps() -> None:
    note = _note()
    claims = [Claim("c001", "", "", "Price raised to $25"), Claim("c002", "", "", "zebra")]
    p = G.plan(note, claims, k=1, window=1)  # type: ignore[arg-type]
    assert p.candidates == {"c001": [1], "c002": []}
    assert p.excerpt == [0, 1, 2]
    assert p.prechecks["c001"]["numbers"] == []  # "twenty five" is 25
    text = G.transcript_lines(note, p.excerpt)  # type: ignore[arg-type]
    assert text.splitlines()[0].startswith("[0] Me:") and text.splitlines()[-1] == "…"
    assert G.transcript_lines(note, [1, 3]).count("…") == 3  # type: ignore[arg-type]


def test_ground_escalates_and_returns_evidence(tmp_path: Path) -> None:
    note = _note()
    claims = [
        Claim("c001", "", "", "Price raised to $25"),
        Claim("c002", "", "", "Priya will send the pricing deck"),
        Claim("c003", "", "", "zebra crossing"),  # no candidate lines: full pass only
    ]
    fake = _Fake()
    client = LLMClient(fake, model="claude-haiku-5-5", cache_dir=tmp_path)  # type: ignore[arg-type]
    res = G.ground_claims(note, claims, client, k=2, window=0)  # type: ignore[arg-type]
    assert res.calls == 2 and res.escalated == 2
    assert "<scope>excerpt</scope>" in fake.requests[0].system
    assert "c003" not in fake.requests[0].user and "c001" not in fake.requests[1].user
    c1, c2, c3 = res.claims
    assert (c1.verdict, c1.decided_by, c1.lines, c1.evidence) == (
        "supported",
        "excerpt",
        [1],
        ["n1:0001"],
    )
    assert c1.quote == "We agreed to raise the price to twenty five dollars."
    assert c1.quote_id == "n1:0001"
    assert (c2.verdict, c2.excerpt_verdict, c2.decided_by, c2.lines) == (
        "contradicted",
        "unsupported",
        "full",
        [3],
    )
    assert (c3.verdict, c3.uncited_support, c3.excerpt_verdict) == ("unsupported", True, None)
    assert res.counts == {"supported": 1, "unsupported": 1, "contradicted": 1}
    # Same inputs, warm cache: same output, no new calls.
    again = G.ground_claims(note, claims, client, k=2, window=0)  # type: ignore[arg-type]
    assert again == res and len(fake.requests) == 2


def test_no_escalation_keeps_excerpt_verdicts(tmp_path: Path) -> None:
    note = _note()
    claims = [Claim("c001", "", "", "Price raised to $25"), Claim("c002", "", "", "Dana deck")]
    client = LLMClient(_Fake(), model="claude-haiku-5-5", cache_dir=tmp_path)  # type: ignore[arg-type]
    res = G.ground_claims(note, claims, client, escalate=False, k=2, window=0)  # type: ignore[arg-type]
    assert res.calls == 1 and [c.verdict for c in res.claims] == ["supported", "unsupported"]


def test_quote_prices_excerpt_and_worst_case() -> None:
    note = _note()
    q = G.quote(note, [Claim("c001", "", "", "Price raised to $25")], k=1, window=0)  # type: ignore[arg-type]
    assert q.excerpt_lines == 1 and q.transcript_lines == 5
    assert 0 < q.usd(escalate_share=0.0) < q.usd(escalate_share=1.0)


def test_span_retrieval_scores_adjacent_lines_together() -> None:
    """A point split over two short turns beats one line repeating a single query word."""
    note = make_note(
        "n2",
        "2026-10-07",
        "Remote",
        [
            ("them", None, "The remote case."),
            ("them", None, "Weather is nice."),
            ("them", None, "Make the case from wood."),
            ("them", None, "With rubber buttons for grip."),
            ("them", None, "Lunch now."),
        ],
    )
    weights = G.term_weights(note.utterances)
    query = "Case made of wood with rubber buttons"
    assert G.nearest_spans(query, note.utterances, 1, 2, weights=weights) == [2, 3]
    # span=1 is exactly the single-line retrieval used so far (cache keys unchanged)
    one = G.nearest_spans(query, note.utterances, 2, 1, weights=weights)
    assert one == G.nearest_lines(query, note.utterances, 2, weights=weights, stemmed=True)
    # Next best: "rubber buttons" (3-4), then "case ... wood" (1-2); each run adds a new line
    assert G.nearest_spans(query, note.utterances, 3, 2, weights=weights) == [1, 2, 3, 4]
    p = G.plan(note, [Claim("c001", "", "", query)], k=1, window=0, span=2)  # type: ignore[arg-type]
    assert p.candidates["c001"] == [2, 3] and p.excerpt == [2, 3]


def test_labels_note_is_input_only_and_off_by_default() -> None:
    note = _note()
    claims = [Claim("c001", "", "", "Price raised to $25")]
    plain = G.request(note, claims, None)  # type: ignore[arg-type]
    assert "<speaker_labels>" not in plain.system
    req = G.request(note, claims, None, labels_note="Labels are arbitrary.")  # type: ignore[arg-type]
    assert "<speaker_labels>\nLabels are arbitrary.\n</speaker_labels>" in req.system
    assert req.prompt_id == plain.prompt_id == "grounding.v1"
    assert req.system.index("</calendar>") < req.system.index("<speaker_labels>")
    assert req.system.index("</speaker_labels>") < req.system.index("<transcript>")
    q0 = G.quote(note, claims)  # type: ignore[arg-type]
    q1 = G.quote(note, claims, labels_note="Labels are arbitrary.")  # type: ignore[arg-type]
    assert q1.full_input > q0.full_input


# -- perturbations and scoring -------------------------------------------------------------------


def test_perturbations() -> None:
    rng = random.Random(0)
    swapped = V.swap_role("The Project Manager asked the Marketing Expert.", rng)
    assert swapped is not None and swapped.startswith("The ") and "Project Manager" not in swapped
    assert "Marketing Expert" in swapped  # not swapped to a role the sentence already names
    assert V.swap_role("The group met.", rng) is None
    assert (
        V.swap_person("Ann and Bo will call.", ["Ann", "Bo", "Cy"], rng) == "Cy and Bo will call."
    )
    assert V.swap_person("Ann and Bo will call.", ["Ann", "Bo"], rng) is None
    assert V.change_number("We need three rooms.", random.Random(1)) in {
        "We need four rooms.",
        "We need five rooms.",
        "We need six rooms.",
    }
    assert V.change_number("Meet at 2 pm.", random.Random(1)) in {
        "Meet at 3 pm.",
        "Meet at 4 pm.",
    }
    assert V.change_number("The one we liked.", rng) is None
    assert V.negate("The group will discuss it.") == "The group will not discuss it."
    assert V.negate("There will not be a cafeteria.") == "There will be a cafeteria."
    assert V.negate("It would probably not work.") == "It would probably work."
    assert V.negate("The PM decided to wait.") == "The PM did not decide to wait."
    assert V.negate("They discussed whether it will fit.") is None
    assert V.people(["Ask Gisella and the Project Manager on Tuesday."], "gisella said") == [
        "Gisella"
    ]


def _scored(kind: str, verdict: str, sid: str, lines: list[int], links: tuple[int, ...]) -> object:
    item = V.Item(f"M.{kind}.{sid}", "M", kind, "abstract", "t", "t", "M", sid, links, "test")
    res = G.ClaimResult(
        claim_id="c001",
        section="",
        context="",
        text="t",
        verdict=verdict,  # type: ignore[arg-type]
        evidence=[],
        lines=lines,
        quote="",
        quote_id="",
        reason="",
        decided_by="excerpt",
        excerpt_verdict=None,
        uncited_support=False,
        candidates=[],
        precheck={},
    )
    return V.Scored(item, res)


def test_score_recall_paired_precision_and_evidence() -> None:
    s = [
        _scored("original", "supported", "s1", [5], (5, 6)),
        _scored("original", "supported", "s2", [9], (2,)),
        _scored("original", "unsupported", "s3", [], (1,)),
        _scored("negate", "contradicted", "s1", [5], ()),
        _scored("negate", "supported", "s2", [9], ()),
        _scored("negate", "unsupported", "s3", [], ()),
    ]
    out = V.score(s, seed=0)["test"]  # type: ignore[arg-type]
    pos = out["positives"]
    assert pos["labels"]["supported"]["k"] == 2
    assert pos["evidence"]["cited_overlaps_linked_act"]["k"] == 1
    assert pos["evidence"]["cited_overlaps_linked_act"]["n"] == 2
    neg = out["negatives"]["negate"]
    assert (neg["recall"]["k"], neg["recall"]["n"]) == (2, 3)
    # Pairs are each negation's own source: s3 was flagged, so 1 false positive.
    assert (neg["precision_paired"]["k"], neg["precision_paired"]["n"]) == (2, 3)
    assert neg["contradicted_cites_lines"]["k"] == 1
