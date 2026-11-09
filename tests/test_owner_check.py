"""Owner check (tenor.evals.owner_check): owner extraction, gold owners, labels, rule, scoring."""

from __future__ import annotations

from tenor.evals import owner_check as O
from tenor.evals.list_check import Line

ROSTER = (("PM", "Shrida Daseri"), ("ID", "Christine"), ("UI", "Agnes"), ("ME", "Ed"))
NAMES = [n for _, n in ROSTER]


def _span(text: str) -> str | None:
    return O.stated_owner(text, NAMES).span


def test_stated_owner_positions() -> None:
    assert _span("(Speaker 2) Work on the look and feel design") == "Speaker 2"
    assert _span("(Both designers) Build the prototype") == "Both designers"
    assert _span("Industrial designer: work on look and feel design") == "Industrial designer"
    assert _span("Project manager to coordinate with Intel") == "Project manager"
    assert _span("Prototype to be built before next meeting (Christine and Agnes)") == (
        "Christine and Agnes"
    )
    assert _span("Build a prototype — Christine and Agnes to produce a model.") == (
        "Christine and Agnes"
    )
    assert _span("After meeting: each member completes the questionnaire") == "each member"


def test_stated_owner_rejects_topics_and_sentence_starts() -> None:
    assert _span("User interface concept") is None
    assert _span("Marketing strategy to be developed alongside prototype") is None
    assert _span("Get cost estimate — Needed to decide whether to include a display.") is None
    assert _span("Send meeting slides to all attendees") is None


def test_resolve_names_roles_and_labels() -> None:
    r = O.resolve("Shreedhar", ROSTER)  # close spelling of Shrida
    assert r.roles == frozenset({"PM"}) and not r.unresolved
    r = O.resolve("Speaker 3", ROSTER)
    assert r.unresolved == ("Speaker 3",)
    assert O.resolve("One person", ROSTER).vague
    assert O.resolve("Mike, Thijs", (("UI", "Mike"),), {"thijs": "ID"}).roles == {"UI", "ID"}


def test_gold_owner() -> None:
    assert O.gold_owner("The interface specialist will look at the working design.") == {"UI"}
    assert O.gold_owner("The two designers will work together.") == {"ID", "UI"}
    assert O.gold_owner("Each team member will complete a questionnaire.") == {O.GROUP}
    assert O.gold_owner("Their personal coaches will give the rest") == "external"
    assert O.gold_owner("One team member will ask Maggie.") == "unspecified"
    assert O.gold_owner("He will also present the plan.", frozenset({"PM"})) == {"PM"}


def test_label() -> None:
    def lab(span: str | None, gold: frozenset[str] | str) -> str:
        return O.label(O.resolve(span, ROSTER), gold)

    assert lab("Ed", frozenset({"ME"})) == "correct"
    assert lab("Agnes", frozenset({"ID"})) == "wrong"
    assert lab("Christine", frozenset({"ID", "UI"})) == "partial"
    assert lab(None, frozenset({"PM"})) == "missing"
    assert lab("Speaker 1", frozenset({"PM"})) == "ambiguous"
    assert lab("Ed", "external") == "no_gold_owner"


def _item(stated: str | None, label: str = "correct") -> O.OwnerItem:
    return O.OwnerItem(
        "M", "granola", "test", "B1", "text", ("A1",), stated, "x", (), (), ("ME",), label
    )


def test_rule_flag() -> None:
    assert not O.rule_flag(_item("Ed"), ROSTER)
    assert not O.rule_flag(_item("Marketing expert"), ROSTER)
    assert O.rule_flag(_item("Speaker 2"), ROSTER)
    assert O.rule_flag(_item("Thijs"), ROSTER)
    assert not O.rule_flag(_item(None), ROSTER)


def test_anchors_deterministic_and_spaced() -> None:
    lines = [
        Line(i, "Them", "00:00", t)
        for i, t in enumerate(
            ["hello"] * 20
            + ["the display cost estimate"]
            + ["filler"] * 20
            + ["display cost again"]
        )
    ]
    a = O.anchors("Get a display cost estimate", lines)
    assert a == O.anchors("Get a display cost estimate", lines)
    assert abs(a[0] - 20) <= 1 and len(a) == 2 and abs(a[0] - a[1]) > O.WINDOW


def test_score_and_go() -> None:
    rows = [
        {"label": "wrong", "gold_roles": ["ID"], "flag": True, "sugg": ["ID"]},
        {"label": "wrong", "gold_roles": ["UI"], "flag": False, "sugg": []},
        {"label": "correct", "gold_roles": ["ME"], "flag": False, "sugg": ["ME"]},
        {"label": "correct", "gold_roles": ["PM"], "flag": True, "sugg": ["UI"]},
    ]
    s = O.score(rows, "flag", "sugg")
    assert s["recall"]["k"] == 1 and s["recall"]["n"] == 2
    assert s["precision"]["k"] == 1 and s["precision"]["n"] == 2
    assert s["false_flags"]["rate"] == 0.5
    assert s["suggested_accuracy_wrong"]["k"] == 1
    assert s["suggested_precision_wrong"]["n"] == 1
    rule = O.score([{**r, "flag": False} for r in rows], "flag", None)
    assert O.go(s, rule) == {
        "recall_ge_0.60": False,
        "false_flags_le_0.10": False,
        "beats_rule": False,
        "go": False,
    }
