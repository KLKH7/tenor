"""The product's chat against the writer: claim splitting, hidden labels, scoring."""

from __future__ import annotations

from pathlib import Path

from tenor.evals import chat_followthrough as C

ANSWER = """Compared with the earlier meeting, these items weren't revisited today:
- **Intel or IBM** support was not followed up.  [[2]](https://notes.granola.ai/d/x)
- Put materials in the shared folder.   [[2]](https://notes.granola.ai/d/x) [[1]](https://notes.granola.ai/d/y)

The prototype *was* presented.  [[1]](https://notes.granola.ai/d/y)
"""


def test_claims_drop_lead_in_links_and_emphasis() -> None:
    assert C.claims(ANSWER) == (
        "Intel or IBM support was not followed up.",
        "Put materials in the shared folder.",
        "The prototype was presented.",
    )


def test_questions_read_from_frozen_prompt() -> None:
    text = Path(f"prompts/{C.QUESTIONS_PROMPT}.md").read_text(encoding="utf-8")
    qs = C.questions(text)
    assert set(qs) == {"q1", "q2"}
    assert qs["q2"] == "What did we say we'd do last time that didn't come up today?"


def test_labels_are_seeded_and_cover_all_sources() -> None:
    sources = ["q1__r1", "q2__r1", "writer"]
    a = C.labels(sources, 42, "P")
    assert a == C.labels(list(reversed(sources)), 42, "P")
    assert sorted(a.values()) == ["A", "B", "C"]


def test_score_counts_verdicts_and_silent_items() -> None:
    answers = [C.Answer("q1__r1", ("x", "y z")), C.Answer("writer", ("w",))]
    key = {"q1__r1": "A", "writer": "B"}
    rows = {
        "a": [
            {"answer": "A", "claim": 1, "verdict": "yes", "x_items": ["A4"]},
            {"answer": "A", "claim": 2, "verdict": "no", "x_items": ["D1"]},
            {"answer": "B", "claim": 1, "verdict": "yes", "x_items": ["A4"]},
        ],
        "b": [
            {"answer": "A", "claim": 1, "verdict": "partly", "x_items": ["A4", "D2"]},
            {"answer": "A", "claim": 2, "verdict": "no", "x_items": []},
            {"answer": "B", "claim": 1, "verdict": "yes", "x_items": ["A4"]},
        ],
    }
    out = C.score(answers, key, rows, ["A4", "D1", "D2"])
    assert out["q1__r1"]["claims"] == 2
    assert out["q1__r1"]["words"] == 3
    assert out["q1__r1"]["true_both"] == 0
    assert out["q1__r1"]["false_both"] == 1
    assert out["q1__r1"]["silent_named"] == {"a": ["A4"], "b": ["A4", "D2"]}
    assert out["q1__r1"]["silent_named_both"] == ["A4"]
    assert out["writer"]["true_both"] == 1
