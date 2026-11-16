"""Invariants of a series record and its provenance check.

* **Provenance.** Every pointer names a meeting in the record and a line that exists in that
  meeting's transcript, and its quote is in that line: at least :data:`MIN_QUOTE_WORDS`
  consecutive words, or the whole line when it is shorter, compared after lower-casing and
  dropping punctuation.
* **Every event is backed.** An event other than ``not_mentioned`` cites at least one line of
  its own meeting. ``not_mentioned`` cites none and lists what was looked for. A change says
  what the item now is.
* **Statuses are derived.** An entry's stored status and current text equal what its history
  implies, so a status only moves with a cited event.
* **Append-only and complete.** Histories are in meeting order, one event per meeting, starting
  with ``introduced``. Every entry that was live before a meeting has an event in it: an
  update may not skip an entry.

All checks return a list of problems (empty when clean); nothing raises on bad input.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from tenor.memory.series_record import (
    CHANGES,
    LIVE,
    STATUS_AFTER,
    Event,
    SeriesRecord,
    Transcript,
    Update,
    derive_current,
    derive_status,
    entry_id,
)

MIN_QUOTE_WORDS = 3
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")


def words(text: str) -> list[str]:
    """Lower-case words without punctuation, the unit quotes are compared in."""
    return _WORD.findall(text.lower())


def quote_problem(quote: str, line_text: str) -> str | None:
    """Why ``quote`` does not stand as a verbatim quote of ``line_text`` (None if it does)."""
    q, t = words(quote), words(line_text)
    if not q:
        return "empty quote"
    if len(q) < MIN_QUOTE_WORDS and q != t:
        return f"quote shorter than {MIN_QUOTE_WORDS} words and not the whole line"
    joined = " " + " ".join(t) + " "
    if " " + " ".join(q) + " " not in joined:
        return "quote not found in the line"
    return None


def ref_problems(
    where: str, meeting: str, line: str, quote: str, transcripts: Mapping[str, Transcript]
) -> list[str]:
    """Problems with one pointer; ``where`` prefixes each message."""
    if meeting not in transcripts:
        return [f"{where}: no transcript for meeting {meeting}"]
    got = transcripts[meeting].get(line)
    if got is None:
        return [f"{where}: {meeting} has no line {line}"]
    why = quote_problem(quote, got.text)
    return [f"{where}: {meeting} {line}: {why}"] if why else []


def _event_shape(
    where: str, kind: str, n_refs: int, searched: Sequence[str], current: str | None
) -> list[str]:
    out = []
    if kind == "not_mentioned":
        if n_refs:
            out.append(f"{where}: not_mentioned must cite no lines")
        if not [s for s in searched if s.strip()]:
            out.append(f"{where}: not_mentioned must list what was looked for")
    elif n_refs == 0:
        out.append(f"{where}: {kind} cites no line")
    if kind in CHANGES and not (current or "").strip():
        out.append(f"{where}: {kind} must say what the item now is (current)")
    if kind not in CHANGES and current:
        out.append(f"{where}: current is only for changes")
    return out


def update_problems(record: SeriesRecord, update: Update, transcript: Transcript) -> list[str]:
    """Problems with an update step before it is folded into ``record``: ``transcript`` is the
    new meeting's."""
    m = update.meeting
    out = []
    if m in record.meetings:
        out.append(f"{m} is already in the record")
    by_id = {e.entry_id: e for e in record.entries}
    seen: set[str] = set()
    for k, ev in enumerate(update.events):
        where = f"event {k} ({ev.entry_id})"
        if ev.entry_id not in by_id:
            out.append(f"{where}: unknown entry")
            continue
        if ev.entry_id in seen:
            out.append(f"{where}: second event for this entry")
        seen.add(ev.entry_id)
        if not ev.summary.strip():
            out.append(f"{where}: empty summary")
        out += _event_shape(where, ev.kind, len(ev.evidence), ev.searched, ev.current)
        for r in ev.evidence:
            out += ref_problems(where, m, r.line, r.quote, {m: transcript})
    missing = [e.entry_id for e in record.entries if e.status in LIVE and e.entry_id not in seen]
    if missing:
        out.append(f"live entries with no event for {m}: {', '.join(missing)}")
    for k, new in enumerate(update.new_entries):
        where = f"new entry {k}"
        if not new.text.strip():
            out.append(f"{where}: empty text")
        for r in new.evidence:
            out += ref_problems(where, m, r.line, r.quote, {m: transcript})
    return out


def _history_problems(where: str, history: Sequence[Event], order: Mapping[str, int]) -> list[str]:
    out = []
    if not history or history[0].kind != "introduced":
        out.append(f"{where}: history must start with introduced")
    if sum(e.kind == "introduced" for e in history) > 1:
        out.append(f"{where}: introduced twice")
    positions = [order.get(e.meeting, -1) for e in history]
    if any(p < 0 for p in positions):
        out.append(f"{where}: event in a meeting not folded in")
    elif positions != sorted(set(positions)):
        out.append(f"{where}: events not one per meeting in meeting order")
    return out


def record_problems(record: SeriesRecord, transcripts: Mapping[str, Transcript]) -> list[str]:
    """Every invariant of a whole record, provenance included."""
    out = []
    order = {m: i for i, m in enumerate(record.meetings)}
    if len(order) != len(record.meetings):
        out.append("a meeting is folded in twice")
    if [s.meeting for s in record.steps] != list(record.meetings):
        out.append("steps do not match meetings")
    for n, e in enumerate(record.entries, start=1):
        where = e.entry_id
        if e.entry_id != entry_id(n):
            out.append(f"{where}: expected id {entry_id(n)}")
        problems = _history_problems(where, e.history, order)
        out += problems
        if problems:
            continue
        if e.history[0].meeting != e.introduced_in:
            out.append(f"{where}: introduced_in does not match its first event")
        if e.status != derive_status(e.history):
            out.append(f"{where}: status {e.status} but history says {derive_status(e.history)}")
        if e.current != derive_current(e.text, e.history):
            out.append(f"{where}: current text does not match its history")
        events = {ev.meeting: ev for ev in e.history}
        status = None
        for m in record.meetings[order[e.introduced_in] :]:
            ev = events.get(m)
            if ev is None and status in LIVE:
                out.append(f"{where}: live in {m} but no event there")
            if ev is not None:
                status = STATUS_AFTER[ev.kind] or status
        for k, ev in enumerate(e.history):
            w = f"{where} event {k} ({ev.meeting} {ev.kind})"
            if ev.kind != "introduced":
                out += _event_shape(w, ev.kind, len(ev.evidence), ev.searched, ev.current)
            elif not ev.evidence:
                out.append(f"{w}: introduced cites no line")
            for r in ev.evidence:
                if r.meeting != ev.meeting:
                    out.append(f"{w}: cites {r.meeting}, not its own meeting")
                out += ref_problems(w, r.meeting, r.line, r.quote, transcripts)
    return out
