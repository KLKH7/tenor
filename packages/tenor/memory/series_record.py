"""A running record of one recurring meeting series: its decisions and commitments, each with a
status and the transcript lines it rests on.

The record is append-only. It changes one meeting at a time: an update (written by a model, see
``prompts/memory_update.v2.md``; v1 records still load) gives one :class:`EventIn` per live entry
and any new entries; :func:`apply_update` folds it in. Since v2 an entry may list its **parts**
(the separate things a decision or commitment names: features, numbers, sub-tasks), and an event
on such an entry that brings it back gives each part its own outcome (:class:`PartEvent`), so a
part that quietly goes missing is visible. :func:`apply_quiet` replaces an event with the
quiet-change verifier's finding. Status is never written directly: it is derived from an
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

EntryKind = Literal["decision", "commitment", "issue"]
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
# Event kinds that bring an entry back and so give each of its parts an outcome.
RETURNS: frozenset[str] = frozenset(
    {"reported", "reaffirmed", "changed_openly", "changed_without_comment"}
)
# What a returning event says about one part.
PartOutcome = Literal[
    "stands", "changed_openly", "changed_without_comment", "dropped", "not_mentioned"
]
PART_OUTCOMES: tuple[str, ...] = PartOutcome.__args__  # type: ignore[attr-defined]
PartStatus = Literal["open", "changed_openly", "changed_without_comment", "dropped"]
PART_STATUS_AFTER: Mapping[str, PartStatus | None] = {
    "stands": "open",
    "changed_openly": "changed_openly",
    "changed_without_comment": "changed_without_comment",
    "dropped": "dropped",
    "not_mentioned": None,
}
CLOSED: frozenset[str] = frozenset({"reported", "dropped"})


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


class PartEvent(BaseModel):
    """What one meeting did to one part of an entry, inside an event that brings it back."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    part: str  # "P1"
    outcome: PartOutcome
    evidence: tuple[LineRef, ...] = ()
    current: str | None = None  # changed_* only: the part as it now stands


class Event(BaseModel):
    """What one meeting did to one entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    meeting: str
    kind: EventKind
    summary: str
    evidence: tuple[LineRef, ...] = ()
    searched: tuple[str, ...] = ()  # not_mentioned only: what was looked for
    current: str | None = None  # changed_* only: the item as it now stands
    parts: tuple[PartEvent, ...] = ()  # one per standing part when a multi-part entry returns
    checked_by: str | None = None  # set when the quiet-change verifier replaced the event


class Part(BaseModel):
    """One separately trackable part of an entry. Status and current text are derived from the
    part events of the entry's history, like the entry's own."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    part_id: str  # "P1"
    text: str  # as first recorded
    status: PartStatus = "open"
    current: str = ""  # the part as it now stands; ``text`` until a change says otherwise


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
    parts: tuple[Part, ...] = ()  # empty for a one-part entry (v1 records have none)


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


class PartEventIn(BaseModel):
    """One part's outcome in an event of the meeting being folded in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    part: str
    outcome: PartOutcome
    evidence: tuple[RefIn, ...] = ()
    current: str | None = None


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
    parts: tuple[PartEventIn, ...] = ()
    checked_by: str | None = None  # set by :func:`apply_quiet`, never by the writer


class EntryIn(BaseModel):
    """A decision or commitment first made in the meeting being folded in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EntryKind
    text: str
    owner: str | None = None
    summary: str = ""
    evidence: tuple[RefIn, ...] = Field(min_length=1)
    parts: tuple[str, ...] = ()  # the separate things it names; none, or at least two


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


def derive_parts(parts: Sequence[Part], history: Sequence[Event]) -> tuple[Part, ...]:
    """Each part's status and current text as the history's part events imply: the last
    outcome that sets a status, and the last change's ``current`` (else the first text)."""
    out = []
    for p in parts:
        status: PartStatus = "open"
        current = p.text
        for ev in history:
            for pe in ev.parts:
                if pe.part != p.part_id:
                    continue
                status = PART_STATUS_AFTER[pe.outcome] or status
                if pe.outcome in CHANGES and pe.current:
                    current = pe.current
        out.append(p.model_copy(update={"status": status, "current": current}))
    return tuple(out)


def standing_parts(entry: Entry) -> list[str]:
    """Ids of the parts a returning event must speak to: every part not dropped."""
    return [p.part_id for p in entry.parts if p.status != "dropped"]


def entry_id(n: int) -> str:
    return f"E{n:03d}"


def part_id(n: int) -> str:
    return f"P{n}"


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
    parts: Sequence[str] = (),
) -> Entry:
    """A new entry with its ``introduced`` event and its parts (``P1``... in order)."""
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
        parts=tuple(
            Part(part_id=part_id(i), text=t, current=t) for i, t in enumerate(parts, start=1)
        ),
    )


def _refs(meeting: str, refs: Sequence[RefIn]) -> tuple[LineRef, ...]:
    return tuple(LineRef(meeting=meeting, line=r.line, quote=r.quote) for r in refs)


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
            evidence=_refs(m, ev.evidence),
            searched=ev.searched,
            current=ev.current,
            parts=tuple(
                PartEvent(
                    part=pe.part,
                    outcome=pe.outcome,
                    evidence=_refs(m, pe.evidence),
                    current=pe.current,
                )
                for pe in ev.parts
            ),
            checked_by=ev.checked_by,
        )
        history = (*e.history, event)
        entries.append(
            e.model_copy(
                update={
                    "history": history,
                    "status": derive_status(history),
                    "current": derive_current(e.text, history),
                    "parts": derive_parts(e.parts, history),
                }
            )
        )
    for k, new in enumerate(update.new_entries, start=len(record.entries) + 1):
        refs = _refs(m, new.evidence)
        summary = new.summary or f"First recorded in {m}."
        entries.append(
            make_entry(
                k, new.kind, new.text, new.owner, m, "transcript", refs, summary, parts=new.parts
            )
        )
    return record.model_copy(
        update={
            "meetings": (*record.meetings, m),
            "steps": (*record.steps, Step(meeting=m, method=method)),
            "entries": tuple(entries),
        }
    )


# -- the quiet-change check (a second reader on returning multi-part entries) -----------------


class QuietPart(BaseModel):
    """A part the verifier found changed without comment, with the lines that show it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    part: str
    evidence: tuple[RefIn, ...] = Field(min_length=1)
    current: str  # the part as the meeting now has it ("absent" when it is left out)
    what_changed: str = ""


class Closest(BaseModel):
    """A passage the verifier found nearest to someone raising the change."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    line: str
    says: str = ""


class QuietFinding(BaseModel):
    """The verifier's answer for one returning multi-part entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str
    verdict: Literal["holds", "quiet_change"]
    parts: tuple[QuietPart, ...] = ()  # quiet_change only
    current: str | None = None  # quiet_change only: the whole item as it now stands
    terms_searched: tuple[str, ...] = ()
    closest: tuple[Closest, ...] = ()
    reason: str = ""


def quiet_candidates(record: SeriesRecord, update: Update) -> list[str]:
    """Entries the quiet-change check reads: the update brings them back as ``reported`` or
    ``reaffirmed`` and they have at least two standing parts."""
    by_id = {e.entry_id: e for e in record.entries}
    return [
        ev.entry_id
        for ev in update.events
        if ev.kind in ("reported", "reaffirmed")
        and ev.entry_id in by_id
        and len(standing_parts(by_id[ev.entry_id])) >= 2
    ]


def apply_quiet(update: Update, findings: Sequence[QuietFinding], method: str) -> Update:
    """``update`` with every checked event marked ``<method>:<verdict>`` and every
    ``quiet_change`` event replaced: kind ``changed_without_comment``, the flagged parts'
    outcomes and lines added, the verifier's ``current``. Run
    :func:`tenor.memory.series_check.quiet_problems` first and ``update_problems`` after."""
    found = {f.entry_id: f for f in findings}
    events = []
    for ev in update.events:
        f = found.get(ev.entry_id)
        if f is None:
            events.append(ev)
            continue
        if f.verdict == "holds":
            events.append(ev.model_copy(update={"checked_by": f"{method}:holds"}))
            continue
        flagged = {qp.part: qp for qp in f.parts}
        parts = []
        for pe in ev.parts:
            qp = flagged.get(pe.part)
            if qp is None:
                parts.append(pe)
                continue
            parts.append(
                PartEventIn(
                    part=pe.part,
                    outcome="changed_without_comment",
                    evidence=qp.evidence,
                    current=qp.current,
                )
            )
        evidence = list(ev.evidence)
        for qp in f.parts:
            evidence += [r for r in qp.evidence if r not in evidence]
        events.append(
            ev.model_copy(
                update={
                    "kind": "changed_without_comment",
                    "summary": f"{ev.summary} Quiet-change check: {f.reason}".strip(),
                    "evidence": tuple(evidence),
                    "current": f.current,
                    "parts": tuple(parts),
                    "checked_by": f"{method}:quiet_change",
                }
            )
        )
    return update.model_copy(update={"events": tuple(events)})


# -- the view an agent reads -------------------------------------------------------------------


QUOTE_WORDS = 12  # compact view: quotes cut to this many words
STALE_AFTER = 2  # compact view: an open entry not mentioned in this many meetings is one line


def _ref(r: LineRef, max_words: int | None = None) -> str:
    words = r.quote.split()
    quote = r.quote
    if max_words is not None and len(words) > max_words:
        quote = " ".join(words[:max_words]) + " ..."
    return f'{r.meeting} {r.line} "{quote}"'


def _refs_text(refs: Sequence[LineRef], compact: bool, limit: int = 1) -> str:
    shown = list(refs[:limit] if compact else refs)
    more = len(refs) - len(shown)
    tail = f"; +{more} more" if more > 0 else ""
    words = QUOTE_WORDS if compact else None
    return " [" + "; ".join(_ref(r, words) for r in shown) + tail + "]" if shown else ""


def _event_line(ev: Event, compact: bool) -> str:
    what = ev.kind.replace("_", " ")
    tail = ""
    if ev.evidence:
        tail = _refs_text(ev.evidence, compact)
    elif ev.searched and not compact:
        tail = " (looked for: " + ", ".join(ev.searched) + ")"
    summary = "" if compact and ev.kind == "introduced" else f": {ev.summary}"
    line = f"- {ev.meeting} {what}{summary}{tail}"
    moved = [pe for pe in ev.parts if pe.outcome != "stands"]
    if moved:
        line += " Parts: " + "; ".join(
            f"{pe.part} {pe.outcome.replace('_', ' ')}"
            + (f" ({pe.current})" if pe.current else "")
            + (_refs_text(pe.evidence, compact) if pe.evidence else "")
            for pe in moved
        )
    if ev.checked_by and not compact:
        line += f" (checked: {ev.checked_by})"
    return line


def _stale(e: Entry) -> str | None:
    """The meeting since which an open entry has not been mentioned, if that is at least
    :data:`STALE_AFTER` meetings, else None."""
    tail = list(e.history[-STALE_AFTER:])
    if e.status != "open" or len(tail) < STALE_AFTER:
        return None
    if not all(ev.kind == "not_mentioned" for ev in tail):
        return None
    last = next(ev for ev in reversed(e.history) if ev.kind != "not_mentioned")
    return last.meeting


def render_entry(e: Entry, compact: bool = False) -> str:
    """One entry's block. Compact: a closed entry is one line with the pointer that closed it,
    and so is an open entry not mentioned in the last :data:`STALE_AFTER` meetings; a live one
    keeps its parts and events, with one pointer per event, quotes cut to
    :data:`QUOTE_WORDS` words, and runs of ``not_mentioned`` folded into one line."""
    stale = _stale(e) if compact else None
    if compact and (e.status in CLOSED or stale):
        closer = next((ev for ev in reversed(e.history) if ev.kind in CLOSED), None)
        if stale:
            state = f"open, not mentioned since {stale}"
            closer = next(ev for ev in reversed(e.history) if ev.kind != "not_mentioned")
        else:
            state = f"{e.status} in {closer.meeting if closer else e.introduced_in}"
        ptr = _refs_text(closer.evidence, compact) if closer else ""
        parts = (
            f" Parts: {'; '.join(p.part_id + ' ' + p.current for p in e.parts)}." if e.parts else ""
        )
        return (
            f"## {e.entry_id} {e.kind} | {state} | owner: {e.owner or '-'} | first recorded: "
            f"{e.introduced_in}: {e.current}{parts}{ptr}"
        )
    lines = [
        f"## {e.entry_id} {e.kind} | status: {e.status} | owner: {e.owner or '-'} | "
        f"first recorded: {e.introduced_in}",
        f"As first recorded: {e.text}",
    ]
    if e.current != e.text:
        lines.append(f"Now stands as: {e.current}")
    if e.parts:
        lines.append("Parts:")
        for p in e.parts:
            state = "" if p.status == "open" else f" ({p.status.replace('_', ' ')})"
            now = f" -> now: {p.current}" if p.current != p.text else ""
            lines.append(f"  - {p.part_id}: {p.text}{state}{now}")
    unmentioned: list[str] = []
    for ev in e.history:
        if compact and ev.kind == "not_mentioned":
            unmentioned.append(ev.meeting)
            continue
        if unmentioned:
            lines.append(f"- not mentioned in: {', '.join(unmentioned)}")
            unmentioned = []
        lines.append(_event_line(ev, compact))
    if unmentioned:
        lines.append(f"- not mentioned in: {', '.join(unmentioned)}")
    return "\n".join(lines)


def render(record: SeriesRecord, compact: bool = False) -> str:
    """The record as an agent reads it: one block per entry, every event with its pointers.
    ``compact`` folds closed entries to one line each and trims pointers (see
    :func:`render_entry`); the full history of any entry stays in the record file. Same record
    -> same text."""
    head = [
        f"# Series record: {record.series}",
        f"Meetings folded in, in order: {', '.join(record.meetings) or '(none)'}.",
        "Statuses: open | reported | changed_openly | changed_without_comment | dropped. "
        'Pointers are `<meeting> <line id> "<verbatim quote>"`.',
    ]
    if compact:
        head.append(
            "Compact view: closed entries (reported, dropped) and open entries not mentioned in "
            f"the last {STALE_AFTER} meetings are one line each; other entries show one pointer "
            f"per event with quotes cut to {QUOTE_WORDS} words. The record file keeps every "
            "pointer and every event summary."
        )
    blocks = [render_entry(e, compact) for e in record.entries]
    return "\n\n".join(["\n".join(head), *blocks]) + "\n"


def dump(record: SeriesRecord) -> str:
    """Stable JSON for a record file."""
    return json.dumps(record.model_dump(mode="json"), indent=1, ensure_ascii=False) + "\n"
