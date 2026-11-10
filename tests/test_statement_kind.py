"""Statement kind: dialogue-act types from the AMI zip, answer validation, job files, the
per-bullet support maths, the pre-stated measures and the seeded examples (no network)."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest

from tenor.ami.corpus import DA_GLOSS, DA_MINOR, DialogueAct, load_meeting
from tenor.evals import bullet_cues as B
from tenor.evals import speaker_naming as S
from tenor.evals import statement_kind as K
from tests.test_bullet_cues import PACKET
from tests.test_grounding import MEETINGS, NS, SUMMARY, WORDS_A, WORDS_B

TYPES = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<da-type nite:id="cmrda" name="da-type" {NS}>
 <da-type nite:id="ami_daclass_0" name="minor" gloss="Minor">
  <da-type nite:id="ami_da_1" name="bck" gloss="Backchannel"/>
 </da-type>
 <da-type nite:id="ami_daclass_1" name="task" gloss="Task">
  <da-type nite:id="ami_da_4" name="inf" gloss="Inform"/>
  <da-type nite:id="ami_da_9" name="ass" gloss="Assess"/>
 </da-type>
</da-type>"""
ACTS_A = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<nite:root {NS}>
 <dact nite:id="XX0001a.A.da.1" addressee="B">
  <nite:pointer role="da-aspect"  href="da-types.xml#id(ami_da_4)"/>
  <nite:child href="XX0001a.A.words.xml#id(XX0001a.A.words0)..id(XX0001a.A.words4)"/>
 </dact>
 <dact nite:id="XX0001a.A.da.2">
  <nite:child href="XX0001a.A.words.xml#id(XX0001a.A.words5)..id(XX0001a.A.words6)"/>
 </dact>
</nite:root>"""
ACTS_B = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<nite:root {NS}>
 <dact nite:id="XX0001a.B.da.1">
  <nite:pointer role="da-aspect"  href="da-types.xml#id(ami_da_9)"/>
  <nite:child href="XX0001a.B.words.xml#id(XX0001a.B.words0)"/>
 </dact>
</nite:root>"""


def _zip(tmp_path: Path, ontology: bool = True) -> Path:
    path = tmp_path / "ami.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("corpusResources/meetings.xml", MEETINGS)
        zf.writestr("words/XX0001a.A.words.xml", WORDS_A)
        zf.writestr("words/XX0001a.B.words.xml", WORDS_B)
        zf.writestr("dialogueActs/XX0001a.A.dialog-act.xml", ACTS_A)
        zf.writestr("dialogueActs/XX0001a.B.dialog-act.xml", ACTS_B)
        zf.writestr("abstractive/XX0001a.abssumm.xml", SUMMARY)
        if ontology:
            zf.writestr("ontologies/da-types.xml", TYPES)
    return path


def test_dialogue_act_type_and_addressee(tmp_path: Path) -> None:
    m = load_meeting("XX0001a", _zip(tmp_path))
    assert [(a.da_id, a.da_type, a.addressee) for a in m.dialogue_acts] == [
        ("XX0001a.A.da.1", "inf", "B"),
        ("XX0001a.B.da.1", "ass", ""),
        ("XX0001a.A.da.2", "", ""),  # no type pointer
    ]
    assert K.has_typed_acts(m)
    # tokens per type: "budget is fixed" inf, "yes" ass, "okay" untyped
    assert K.type_words(m) == {"inf": 3, "ass": 1, "(untyped)": 1}
    assert K.group_shares(K.type_words(m))["inform"] == 0.6


def test_types_absent_without_ontology_and_defaults(tmp_path: Path) -> None:
    m = load_meeting("XX0001a", _zip(tmp_path, ontology=False))
    assert {a.da_type for a in m.dialogue_acts} == {""} and not K.has_typed_acts(m)
    a = DialogueAct(da_id="x", agent="A", start=0.0, end=1.0, text="hi")
    assert (a.da_type, a.addressee) == ("", "")
    assert set(DA_MINOR) <= set(DA_GLOSS) and DA_GLOSS["ass"] == "Assess"


def test_groups() -> None:
    assert [K.group_of(t) for t in ("inf", "sug", "ass", "off", "el.ass", "be.pos", "", "bck")] == [
        "inform", "suggest", "assess", "offer", "elicit", "other", "other", "minor",
    ]  # fmt: skip
    assert {t for t, g in K.GROUP_OF.items() if g == K.MINOR} == set(DA_MINOR)
    assert set(K.GROUP_OF) <= set(DA_GLOSS)


# -- answers and job files --------------------------------------------------------------------


def _unit() -> B.CueUnit:
    return B.parse_packet(PACKET, "IS0000a", "granola", "test")


def test_notes_label_validation() -> None:
    lab = K.NotesLabel.model_validate(
        {"presented_as": "fact", "attributed_to": " Ana ", "hedged": "Yes"}
    )
    assert (lab.presented_as, lab.attributed_to, lab.hedged) == ("fact", "Ana", True)
    assert K.NotesLabel.model_validate({"presented_as": "open", "hedged": "no"}).hedged is False
    for bad in (
        {"presented_as": "rumour", "hedged": "no"},
        {"presented_as": "fact", "hedged": "maybe"},
        {"presented_as": "fact", "hedged": "no", "extra": 1},
    ):
        with pytest.raises(ValueError):
            K.NotesLabel.model_validate(bad)


def test_job_files_and_support_check() -> None:
    u = _unit()
    notes = K.notes_job_file(u, "INSTRUCTIONS")
    assert "INSTRUCTIONS" in notes and "B3 [section: Next steps] Send the slides" in notes
    assert "[T0001" not in notes and "rubber it is" not in notes  # no transcript, no gold
    jobs = B.jobs_for(u, {"B2": [(1, 1)], "B3": [(2, 3)]}, pad=1)
    support = K.support_job_file(u, jobs, "INSTRUCTIONS")
    assert support == B.job_file(u, jobs, "INSTRUCTIONS", "Statement-kind jobs (support)")
    assert "## Job B2" in support and "[T0002 Speaker 2 00:09]" in support
    assert "## 2. Gold items" not in support
    job = jobs[0]  # B2: T0000-T0002 shown
    pick = K.SupportPick(support=["T0002", "T0001", "T0001", "T0004", "L1", "T0001-T0002"])
    lines, problems = K.check_support(pick, job)
    assert lines == [1, 2] and len(problems) == 3
    many = B.jobs_for(u, {"B1": [(0, 5)]}, pad=0)[0]
    lines, problems = K.check_support(K.SupportPick(support=[f"T{i:04d}" for i in range(6)]), many)
    assert lines == [0, 1, 2, 3, 4, 5] and not problems
    assert K.check_support(K.SupportPick(support=[]), job) == ([], [])


# -- the support maths ------------------------------------------------------------------------

# acts: 0 inform by A, 1 suggest by B, 2 assess by B, 3 backchannel by C, 4 elicit by A
ACTS = [K.Act("inf", "A"), K.Act("sug", "B"), K.Act("ass", "B"), K.Act("bck", "C"),
        K.Act("el.inf", "A")]  # fmt: skip


def _line(i: int, acts: dict[int, int], confident: bool = True) -> S.LineTruth:
    n = sum(acts.values())
    return S.LineTruth(i, n, "A", 1.0 if confident else 0.5, False, {"A": n}, acts)


TRUTH = {
    0: _line(0, {0: 10}),  # 10 inform words (A)
    1: _line(1, {1: 6, 3: 2}),  # 6 suggest (B), 2 backchannel (C)
    2: _line(2, {2: 3, 0: 1}),  # 3 assess (B), 1 inform (A)
    3: _line(3, {1: 8}, confident=False),
    4: _line(4, {3: 4}),  # backchannel only
    5: _line(5, {4: 2, 1: 2}),  # tie: 2 elicit (A), 2 suggest (B)
    6: _line(6, {2: 4}),  # 4 assess (B)
}


def test_score_bullet_shares_dominant_and_speakers() -> None:
    r = K.score_bullet([1, 2], TRUTH, ACTS)
    assert r["status"] == "scored" and r["words"] == 10 and r["minor_words"] == 2
    assert r["by_type"] == {"ass": 3, "bck": 2, "inf": 1, "sug": 6}
    assert r["shares"] == {"inform": 0.1, "suggest": 0.6, "assess": 0.3, "offer": 0.0,
                           "elicit": 0.0, "other": 0.0}  # fmt: skip
    assert (r["dominant"], r["as_share"]) == ("suggest", 0.9)
    # the backchannel speaker (C) is not a speaker of the support
    assert (r["speakers"], r["top_speaker"], r["top_share"]) == (2, "B", 0.9)
    assert K.is_view(r) and K.is_one_persons_view(r)
    mixed = K.score_bullet([0, 1], TRUTH, ACTS)  # 10 inform (A) + 6 suggest (B)
    assert (mixed["dominant"], mixed["as_share"], mixed["top_share"]) == ("inform", 0.375, 0.625)
    assert not K.is_view(mixed)
    tie = K.score_bullet([5], TRUTH, ACTS)
    assert tie["dominant"] == "suggest" and tie["top_speaker"] == "A"  # earlier group, letter


def test_score_bullet_thresholds_are_inclusive() -> None:
    truth = {0: _line(0, {1: 3, 0: 2}), 1: _line(1, {1: 4, 0: 1})}
    r = K.score_bullet([0], truth, ACTS)  # 3 suggest (B) of 5: exactly 0.6
    assert r["as_share"] == K.AS_SHARE and K.is_view(r) and not K.is_one_persons_view(r)
    r = K.score_bullet([1], truth, ACTS)  # 4 of 5 from B: exactly 0.8
    assert r["top_share"] == K.ONE_SPEAKER and K.is_one_persons_view(r)


def test_score_bullet_statuses() -> None:
    assert K.score_bullet([], TRUTH, ACTS)["status"] == "not_located"
    lost = K.score_bullet([3, 99], TRUTH, ACTS)  # low confidence; a line with no truth
    assert lost["status"] == "lost_alignment" and lost["lines_used"] == []
    minor = K.score_bullet([4], TRUTH, ACTS)
    assert minor["status"] == "no_content" and minor["minor_words"] == 4
    assert "dominant" not in minor and not K.is_view(minor)
    part = K.score_bullet([0, 3], TRUTH, ACTS)  # the unconfident line adds nothing
    assert part["lines"] == ["T0000", "T0003"] and part["lines_used"] == ["T0000"]
    assert part["shares"]["inform"] == 1.0


# -- measures ---------------------------------------------------------------------------------


def _row(meeting: str, product: str, n: int, presented: str, lines: list[int],
         hedged: bool = False, who: str = "") -> dict[str, Any]:  # fmt: skip
    return {"unit": f"{meeting}:{product}", "meeting": meeting, "product": product,
            "bullet_id": f"B{n}", "presented_as": presented, "attributed_to": who,
            "hedged": hedged, **K.score_bullet(lines, TRUTH, ACTS)}  # fmt: skip


ROWS = [
    _row("M1", "granola", 1, "fact", [0]),  # inform: fine
    _row("M1", "granola", 2, "fact", [1, 2]),  # one person's view as fact: M1, M3
    _row("M1", "granola", 3, "fact", [1, 2], hedged=True),  # hedged: out of M1; M3 (no name)
    _row("M1", "granola", 4, "decision", [1, 2], who="Bo"),  # M2; M3 den only (named)
    _row("M1", "wispr", 1, "decision", [0]),  # M2 den only
    _row("M2", "granola", 1, "decision", [0, 1]),  # 37% view: M2 den only
    _row("M2", "wispr", 1, "fact", []),  # not located: unscored
    _row("M2", "wispr", 2, "opinion", [6], who="Bo"),  # assess, named: M3 den only
    _row("M2", "wispr", 3, "fact", [4]),  # minor only: unscored
]


def test_summarise_measures_and_matrix() -> None:
    s = K.summarise(ROWS, seed=42)
    p = s["pooled"]
    assert p["bullets"] == 9 and p["meetings"] == 2
    assert p["status"] == {"scored": 7, "not_located": 1, "lost_alignment": 0, "no_content": 1}
    got = {k: (v["num"], v["den"]) for k, v in p["measures"].items()}
    assert got == {"M1": (1, 2), "M2": (1, 3), "M3": (2, 4)}
    assert p["measures"]["M1"]["rate"] == 0.5 and p["measures"]["M3"]["rate"] == 0.5
    g = {k: (v["num"], v["den"]) for k, v in s["granola"]["measures"].items()}
    w = {k: (v["num"], v["den"]) for k, v in s["wispr"]["measures"].items()}
    assert g == {"M1": (1, 2), "M2": (1, 2), "M3": (2, 3)}
    assert w == {"M1": (0, 0), "M2": (0, 1), "M3": (0, 1)}
    assert s["wispr"]["measures"]["M1"]["rate"] is None
    fact = p["matrix"]["fact"]
    assert fact["n"] == 3
    assert {k: c["num"] for k, c in fact["cells"].items() if c["num"]} == {
        "inform": 1,
        "suggest": 2,
    }
    assert fact["cells"]["suggest"]["rate"] == 0.6667
    assert sum(c["num"] for row in p["matrix"].values() for c in row["cells"].values()) == 7
    b = p["base_rates"]
    assert (b["presented_as"]["decision"]["num"], b["presented_as"]["decision"]["den"]) == (3, 7)
    assert (b["dominant"]["suggest"]["num"], b["dominant"]["assess"]["num"]) == (3, 1)
    assert (b["hedged_by_dominant"]["suggest"]["num"], b["hedged"]["num"]) == (1, 1)
    assert b["attributed"]["num"] == 2
    lo, hi = p["measures"]["M3"]["ci"]
    assert 0.0 <= lo <= 0.5 <= hi <= 1.0
    assert K.summarise(ROWS, seed=42) == s  # seeded
    text = K.render(s)
    assert "M1 opinion or proposal presented as fact: 0.50" in text and "(1/2)" in text
    assert "n/a (0/0)" in text


def test_examples_are_a_seeded_sample() -> None:
    rows = [_row(f"M{i % 3}", "granola", i, "fact", [1, 2]) for i in range(1, 31)]
    a = K.pick_examples(rows, "M1", seed=42)
    assert len(a) == K.N_EXAMPLES and a == K.pick_examples(rows, "M1", seed=42)
    assert a == K.pick_examples(list(reversed(rows)), "M1", seed=42)  # input order is irrelevant
    assert a != K.pick_examples(rows, "M1", seed=7)
    assert K.pick_examples(rows, "M2", seed=42) == []  # none presented as a decision
    assert len(K.pick_examples(ROWS, "M1", seed=42)) == 1
    block = K.example_block(
        ROWS[1], "Rubber case", {1: "let's use rubber", 2: "rubber is nice"}, TRUTH, ACTS,
        {"A": "Project Manager"},
    )  # fmt: skip
    text = "\n".join(block)
    assert "### M1:granola B2" in text and "presents it as" not in text
    assert "T0001 [Project Manager; sug 6, bck 2] let's use rubber" in text
