"""Versioned corrections to the gold from AMI's abstractive summaries.

File: ``data/ami_questions/gold_errata.v1.json``. Each correction names one AMI summary
sentence (by ``sentence_id`` and its E3 item id), its original text, the action
(``correct_owner``: replace the text; ``drop``: not a gold item) and the transcript evidence.
Corrections are applied only where a caller asks (E3 for now), and the caller records
:attr:`Errata.gold_version` with its results, so results on the original gold stay reproducible
(:data:`GOLD_ORIGINAL`). Proposals without clear evidence sit in ``not_applied`` and are never
applied.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

GOLD_ORIGINAL = "ami-1.6.2"  # AMI's summaries as published, no errata
ERRATA_V1 = Path("data/ami_questions/gold_errata.v1.json")


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
    action: Literal["correct_owner", "drop"]
    corrected_text: str | None
    evidence: tuple[Evidence, ...]
    reason: str


class NotApplied(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    meeting_id: str
    item_id: str
    sentence_id: str
    original_text: str
    proposed_action: Literal["correct_owner", "drop"]
    proposed_text: str | None
    evidence: tuple[Evidence, ...]
    reason: str


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
    affects_questions: dict[str, object]

    def for_meeting(self, meeting_id: str) -> dict[str, Correction]:
        """This meeting's corrections, keyed by AMI sentence id."""
        return {c.sentence_id: c for c in self.corrections if c.meeting_id == meeting_id}


def load_errata(path: Path = ERRATA_V1) -> Errata:
    """Load and validate an errata file. A ``correct_owner`` needs a corrected text and a
    ``drop`` must have none; a sentence may be corrected at most once."""
    errata = Errata.model_validate_json(path.read_text())
    seen: set[str] = set()
    for c in errata.corrections:
        if (c.action == "drop") != (c.corrected_text is None):
            need = "no corrected_text" if c.action == "drop" else "a corrected_text"
            raise ValueError(f"{c.meeting_id} {c.item_id}: {c.action} needs {need}")
        if c.sentence_id in seen:
            raise ValueError(f"{c.sentence_id} corrected twice")
        seen.add(c.sentence_id)
    return errata
