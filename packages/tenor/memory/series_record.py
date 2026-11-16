"""A running record of one recurring meeting series: its decisions and commitments, each with a
status and the transcript lines it rests on.

The record is append-only. It changes one meeting at a time: an update (written by a model, see
``prompts/memory_update.v1.md``) gives one :class:`EventIn` per live entry and any new entries;
:func:`apply_update` folds it in. Status is never written directly: it is derived from an
entry's history (:func:`derive_status`), so a status can only move with a cited event. A
``not_mentioned`` event carries no lines but must say what was looked for, and never moves the
status. :mod:`tenor.memory.series_check` holds the invariants and the provenance check.

Nothing here knows AMI or any product: transcripts are plain ``line id -> Line`` maps
(:mod:`tenor.memory.series_ami` builds them from AMI). See ``docs/engineering/MEETING_MEMORY.md``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EntryKind = Literal["decision", "commitment"]
EventKind = Literal[
    "introduced",
    "reported",
    "reaffirmed",
    "changed_openly",
    "changed_without_comment",
    "dropped",
    "not_mentioned",
]
Status = Literal["open", "reported", "changed_openly", "changed_without_comment", "dropped"]
Origin = Literal["annotation", "transcript"]
EVENT_KINDS: tuple[str, ...] = EventKind.__args__  # type: ignore[attr-defined]
STATUSES: tuple[str, ...] = Status.__args__  # type: ignore[attr-defined]
# Entries an update must speak to: still standing, or changed but not closed.
LIVE: frozenset[str] = frozenset({"open", "changed_openly", "changed_without_comment"})
# What each event does to the status; ``None`` leaves it as it was.
STATUS_AFTER: Mapping[str, Status | None] = {
    "introduced": "open",
    "reaffirmed": "open",
    "reported": "reported",
    "changed_openly": "changed_openly",
    "changed_without_comment": "changed_without_comment",
    "dropped": "dropped",
    "not_mentioned": None,
}
CHANGES: frozenset[str] = frozenset({"changed_openly", "changed_without_comment"})


class Line(BaseModel):
    """One transcript line as the record's checker sees it."""

    model_config = ConfigDict(frozen=True)

    line_id: str  # "L0123"
    speaker: str  # a role or label; not used for provenance
    text: str


Transcript = Mapping[str, Line]  # line id -> line, for one meeting


class LineRef(BaseModel):
    """A pointer into a meeting's transcript with a verbatim quote from that line."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    meeting: str
    line: str
    quote: str


class Event(BaseModel):
    """What one meeting did to one entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    meeting: str
    kind: EventKind
    summary: str
    evidence: tuple[LineRef, ...] = ()
    searched: tuple[str, ...] = ()  # not_mentioned only: what was looked for
    current: str | None = None  # changed_* only: the item as it now stands


class Entry(BaseModel):
    """One decision or commitment and everything the series did to it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str  # "E001"
    kind: EntryKind
    text: str  # as first recorded
    owner: str | None = None
    introduced_in: str
    origin: Origin
    annotation_id: str | None = None  # "IS1008a:D1" when seeded from an annotator's item
    status: Status
    current: str  # the item as it now stands (``text`` until a change says otherwise)
    history: tuple[Event, ...]


class Step(BaseModel):
    """How one meeting was folded in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    meeting: str
    method: str  # "annotation_seed" or the update prompt version, e.g. "memory_update.v1"


class SeriesRecord(BaseModel):
    """The record after the meetings in ``meetings`` (in order) were folded in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    series: str
    meetings: tuple[str, ...] = ()
    steps: tuple[Step, ...] = ()
    entries: tuple[Entry, ...] = ()


# -- what an update step returns (the model's output) -------------------------------------------


class RefIn(BaseModel):
    """A pointer into the meeting being folded in (the meeting is implied)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    line: str
    quote: str


class EventIn(BaseModel):
    """One event for an existing entry, in the meeting being folded in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str
    kind: Literal[
        "reported",
        "reaffirmed",
        "changed_openly",
        "changed_without_comment",
        "dropped",
        "not_mentioned",
    ]
    summary: str
    evidence: tuple[RefIn, ...] = ()
    searched: tuple[str, ...] = ()
    current: str | None = None


class EntryIn(BaseModel):
    """A decision or commitment first made in the meeting being folded in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EntryKind
    text: str
    owner: str | None = None
    summary: str = ""
    evidence: tuple[RefIn, ...] = Field(min_length=1)


class Update(BaseModel):
    """One update step's output: events for existing entries and the meeting's new entries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    meeting: str
    events: tuple[EventIn, ...] = ()
    new_entries: tuple[EntryIn, ...] = ()


# -- deterministic folding ---------------------------------------------------------------------


def derive_status(history: Sequence[Event]) -> Status:
    """The status an entry's history implies: the last event that sets one."""
    status: Status | None = None
    for e in history:
        status = STATUS_AFTER[e.kind] or status
    if status is None:
        raise ValueError("a history must start with an event that sets a status")
    return status


def derive_current(text: str, history: Sequence[Event]) -> str:
    """The item as it now stands: the last change's ``current``, else the first text."""
    for e in reversed(history):
        if e.kind in CHANGES and e.current:
            return e.current
    return text


def entry_id(n: int) -> str:
    return f"E{n:03d}"


def make_entry(
    n: int,
    kind: EntryKind,
    text: str,
    owner: str | None,
    meeting: str,
    origin: Origin,
    evidence: Sequence[LineRef],
    summary: str,
    annotation_id: str | None = None,
) -> Entry:
    """A new entry with its ``introduced`` event."""
    first = Event(meeting=meeting, kind="introduced", summary=summary, evidence=tuple(evidence))
    return Entry(
        entry_id=entry_id(n),
        kind=kind,
        text=text,
        owner=owner,
        introduced_in=meeting,
        origin=origin,
        annotation_id=annotation_id,
        status="open",
        current=text,
        history=(first,),
    )


def apply_update(record: SeriesRecord, update: Update, method: str) -> SeriesRecord:
    """The record with ``update`` folded in. Structure only: run
    :func:`tenor.memory.series_check.update_problems` first; this raises on what it cannot fold
    (a meeting already in the record, an unknown entry, two events for one entry)."""
    m = update.meeting
    if m in record.meetings:
        raise ValueError(f"{m} is already in the record")
    by_entry: dict[str, EventIn] = {}
    for given in update.events:
        if given.entry_id in by_entry:
            raise ValueError(f"two events for {given.entry_id}")
        by_entry[given.entry_id] = given
    known = {e.entry_id for e in record.entries}
    unknown = sorted(set(by_entry) - known)
    if unknown:
        raise ValueError(f"events for unknown entries: {unknown}")
    entries = []
    for e in record.entries:
        ev = by_entry.get(e.entry_id)
        if ev is None:
            entries.append(e)
            continue
        event = Event(
            meeting=m,
            kind=ev.kind,
            summary=ev.summary,
            evidence=tuple(LineRef(meeting=m, line=r.line, quote=r.quote) for r in ev.evidence),
            searched=ev.searched,
            current=ev.current,
        )
        history = (*e.history, event)
        entries.append(
            e.model_copy(
                update={
                    "history": history,
                    "status": derive_status(history),
                    "current": derive_current(e.text, history),
                }
            )
        )
    for k, new in enumerate(update.new_entries, start=len(record.entries) + 1):
        refs = [LineRef(meeting=m, line=r.line, quote=r.quote) for r in new.evidence]
        summary = new.summary or f"First recorded in {m}."
        entries.append(make_entry(k, new.kind, new.text, new.owner, m, "transcript", refs, summary))
    return record.model_copy(
        update={
            "meetings": (*record.meetings, m),
            "steps": (*record.steps, Step(meeting=m, method=method)),
            "entries": tuple(entries),
        }
    )


# -- the view an agent reads -------------------------------------------------------------------


def _ref(r: LineRef) -> str:
    return f'{r.meeting} {r.line} "{r.quote}"'


def render(record: SeriesRecord) -> str:
    """The record as an agent reads it: one block per entry, newest status first in each
    block, every event with its pointers. Same record -> same text."""
    head = [
        f"# Series record: {record.series}",
        f"Meetings folded in, in order: {', '.join(record.meetings) or '(none)'}.",
        "Statuses: open | reported | changed_openly | changed_without_comment | dropped. "
        'Pointers are `<meeting> <line id> "<verbatim quote>"`.',
    ]
    blocks = []
    for e in record.entries:
        lines = [
            f"## {e.entry_id} {e.kind} | status: {e.status} | owner: {e.owner or '-'} | "
            f"first recorded: {e.introduced_in}",
            f"As first recorded: {e.text}",
        ]
        if e.current != e.text:
            lines.append(f"Now stands as: {e.current}")
        for ev in e.history:
            what = ev.kind.replace("_", " ")
            tail = ""
            if ev.evidence:
                tail = " [" + "; ".join(_ref(r) for r in ev.evidence) + "]"
            elif ev.searched:
                tail = " (looked for: " + ", ".join(ev.searched) + ")"
            lines.append(f"- {ev.meeting} {what}: {ev.summary}{tail}")
        blocks.append("\n".join(lines))
    return "\n\n".join(["\n".join(head), *blocks]) + "\n"


def dump(record: SeriesRecord) -> str:
    """Stable JSON for a record file."""
    return json.dumps(record.model_dump(mode="json"), indent=1, ensure_ascii=False) + "\n"
