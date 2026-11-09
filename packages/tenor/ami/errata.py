"""Versioned corrections to the gold from AMI's abstractive summaries.

Files: ``data/ami_questions/gold_errata.v1.json`` and ``gold_errata.v2.json`` (a superset of v1).
Each correction names one AMI summary sentence (by ``sentence_id`` and its E3 item id), its
original text, the action (``correct_owner``: the owner is wrong, replace the text;
``correct_content``: what was decided / done / the problem is wrong, replace the text; ``drop``:
not a gold item) and the transcript evidence. Corrections are applied only where a caller asks,
and the caller records :attr:`Errata.gold_version` with its results, so results on the original
gold stay reproducible (:data:`GOLD_ORIGINAL`). Proposals without clear evidence sit in
``not_applied`` and are never applied.

E3 applies corrections per gold item (:func:`tenor.evals.notes_completeness.gold_items`). For
the question sets (E1, E2, the deletion study) :func:`question_effect` says what a correction
does to a question, by fixed rules (:data:`QUESTION_RULES`), and :func:`apply_to_question`
returns the question under the errata, or ``None`` when it is excluded.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

GOLD_ORIGINAL = "ami-1.6.2"  # AMI's summaries as published, no errata
ERRATA_V1 = Path("data/ami_questions/gold_errata.v1.json")
ERRATA_V2 = Path("data/ami_questions/gold_errata.v2.json")
ERRATA_LATEST = ERRATA_V2

Action = Literal["correct_owner", "correct_content", "drop"]
Effect = Literal["unchanged", "regold", "exclude"]

QUESTION_RULES = (
    "A question is affected when one of its gold_sentence_ids is corrected. "
    "(1) what_changed questions pair a sentence from session k with one from session k+1; "
    "any correction to either breaks the pair: exclude. "
    "(2) action_who questions quote the task and answer with its owner; any correction changes "
    "one of the two: exclude (a regenerated question would be a new question). "
    "(3) List questions (decision, action_what, problem): a drop removes the sentence; "
    "correct_content replaces its text; correct_owner removes it from an action_what question "
    "(the question names the old owner) and replaces its text elsewhere. A list question left "
    "with no gold sentence is excluded; otherwise it is regolded. "
    "(4) Excluded questions are left out of every metric and paired test under these errata. "
    "Regolded questions are re-scored against the new gold; an answer item credited only "
    "through a dropped gold item keeps credit only if it is true and answers the question."
)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    line: str  # "L0189": a line of the E3 judge's AMI reference transcript
    speaker: str  # AMI scenario role
    quote: str  # verbatim from that line


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    meeting_id: str
    item_id: str  # E3 gold item id before errata (D9, A2, P1)
    sentence_id: str  # AMI abstractive sentence id
    original_text: str
    action: Action
    corrected_text: str | None
    evidence: tuple[Evidence, ...]
    reason: str
    added_in: str | None = None  # gold_version that introduced it (v2 onwards)
    date: str | None = None
    question_ids: tuple[str, ...] = ()  # questions whose gold holds this sentence (v2 onwards)


class NotApplied(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    meeting_id: str
    item_id: str
    sentence_id: str
    original_text: str
    proposed_action: Action
    proposed_text: str | None
    evidence: tuple[Evidence, ...]
    reason: str
    added_in: str | None = None
    date: str | None = None
    question_ids: tuple[str, ...] = ()


class QuestionChange(BaseModel):
    """What the errata do to one question (v2 onwards; derived by :func:`question_effect`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    qid: str
    effect: Literal["regold", "exclude"]
    sentence_ids: tuple[str, ...]  # the corrected sentences in this question
    gold_answer: str | None  # the new gold for a regold; None when excluded


class Errata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    gold_version: str  # recorded in result metadata
    base: str
    source: str
    date: str
    status: str
    line_ids: str
    item_ids: str
    corrections: tuple[Correction, ...]
    not_applied: tuple[NotApplied, ...]
    affects_questions: dict[str, object] | None = None  # v1's hand-made list
    supersedes: str | None = None
    question_rules: str | None = None
    question_changes: tuple[QuestionChange, ...] = ()
    notes: tuple[str, ...] = ()

    def for_meeting(self, meeting_id: str) -> dict[str, Correction]:
        """This meeting's corrections, keyed by AMI sentence id."""
        return {c.sentence_id: c for c in self.corrections if c.meeting_id == meeting_id}

    def by_sentence(self) -> dict[str, Correction]:
        """All corrections, keyed by AMI sentence id."""
        return {c.sentence_id: c for c in self.corrections}

    def question_change(self, qid: str) -> QuestionChange | None:
        """The recorded change to question ``qid``, if the errata touch it."""
        return next((q for q in self.question_changes if q.qid == qid), None)


def load_errata(path: Path = ERRATA_V1) -> Errata:
    """Load and validate an errata file. A ``drop`` must have no corrected text and a correction
    must have one; a sentence may be corrected at most once. From v2 on every entry carries
    ``added_in`` and ``date``."""
    errata = Errata.model_validate_json(path.read_text())
    seen: set[str] = set()
    for c in errata.corrections:
        if (c.action == "drop") != (c.corrected_text is None):
            need = "no corrected_text" if c.action == "drop" else "a corrected_text"
            raise ValueError(f"{c.meeting_id} {c.item_id}: {c.action} needs {need}")
        if c.sentence_id in seen:
            raise ValueError(f"{c.sentence_id} corrected twice")
        seen.add(c.sentence_id)
    if errata.version != "gold_errata.v1":
        entries: list[Correction | NotApplied] = [*errata.corrections, *errata.not_applied]
        for e in entries:
            if not (e.added_in and e.date):
                raise ValueError(f"{e.meeting_id} {e.item_id}: needs added_in and date")
        if overlap := seen & {n.sentence_id for n in errata.not_applied}:
            raise ValueError(f"both applied and not applied: {sorted(overlap)}")
    return errata


def check_superset(old: Errata, new: Errata) -> None:
    """Raise unless ``new`` keeps every correction of ``old`` unchanged (apart from the metadata
    fields added in v2) and keeps every ``old`` proposal, applied or not."""
    meta = {"added_in", "date", "question_ids"}
    new_fix = {c.sentence_id: c for c in new.corrections}
    for c in old.corrections:
        n = new_fix.get(c.sentence_id)
        if n is None or n.model_dump(exclude=meta) != c.model_dump(exclude=meta):
            raise ValueError(f"{new.version} changes or loses {c.sentence_id}")
    kept = set(new_fix) | {n.sentence_id for n in new.not_applied}
    if lost := {n.sentence_id for n in old.not_applied} - kept:
        raise ValueError(f"{new.version} loses proposals {sorted(lost)}")


def _gold_text(gold_answer: str, fixes: Iterable[Correction]) -> str:
    text = gold_answer
    for c in fixes:
        if c.original_text not in text:
            raise ValueError(f"{c.sentence_id}: original text not in the question's gold answer")
        text = text.replace(c.original_text, c.corrected_text or "", 1)
    return re.sub(r"\s+", " ", text).strip()


def question_effect(
    question: Mapping[str, Any], errata: Errata
) -> tuple[Effect, tuple[Correction, ...]]:
    """What ``errata`` do to ``question`` (a questions.jsonl row: ``type``,
    ``gold_sentence_ids``), by :data:`QUESTION_RULES`, and the corrections that touch it."""
    fixes = errata.by_sentence()
    hit = tuple(fixes[s] for s in question["gold_sentence_ids"] if s in fixes)
    if not hit:
        return "unchanged", ()
    if question["type"] in ("what_changed", "action_who"):
        return "exclude", hit
    removed = _removed(question, hit)
    left = [s for s in question["gold_sentence_ids"] if s not in removed]
    return ("regold" if left else "exclude"), hit


def _removed(question: Mapping[str, Any], hit: Iterable[Correction]) -> set[str]:
    """Sentences a list question loses: drops, and owner fixes in an action_what question."""
    return {
        c.sentence_id
        for c in hit
        if c.action == "drop" or (c.action == "correct_owner" and question["type"] == "action_what")
    }


def apply_to_question(question: Mapping[str, Any], errata: Errata) -> dict[str, Any] | None:
    """``question`` under ``errata``: ``None`` if excluded, else a copy whose
    ``gold_sentence_ids`` and ``gold_answer`` reflect the corrections (unchanged if none)."""
    effect, hit = question_effect(question, errata)
    if effect == "exclude":
        return None
    out = dict(question)
    if effect == "regold":
        gone = _removed(question, hit)
        edits = [
            c.model_copy(update={"corrected_text": None}) if c.sentence_id in gone else c
            for c in hit
        ]
        out["gold_sentence_ids"] = [s for s in question["gold_sentence_ids"] if s not in gone]
        out["gold_answer"] = _gold_text(question["gold_answer"], edits)
    return out


def question_changes(
    questions: Iterable[Mapping[str, Any]], errata: Errata
) -> list[QuestionChange]:
    """Every question ``errata`` touch, in input order, deduplicated by id (the same question
    can sit in two files)."""
    out: dict[str, QuestionChange] = {}
    for q in questions:
        effect, hit = question_effect(q, errata)
        if effect == "unchanged" or q["id"] in out:
            continue
        new = apply_to_question(q, errata)
        out[q["id"]] = QuestionChange(
            qid=q["id"],
            effect=effect,
            sentence_ids=tuple(c.sentence_id for c in hit),
            gold_answer=new["gold_answer"] if new else None,
        )
    return list(out.values())
