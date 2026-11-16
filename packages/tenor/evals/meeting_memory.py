"""Meeting memory on AMI: transcripts and seeds for a series record, and its agreement with the
item-fates labels.

* :func:`transcript` / :func:`transcript_text`: an AMI meeting's human transcript as the
  record's checker sees it and as an agent reads it (``L0000 [mm:ss] Role: words``, the same
  lines the item-fates readers and the blind answer readers see).
* :func:`seed_record`: the record after the first meeting, built from the annotator's decisions
  and actions (``item_fates.items``) with the dialogue acts the annotator linked as pointers.
* :func:`match_items`: which record entry, if any, stands for each annotator item of a meeting,
  for entries the update step wrote itself (no ids in common), by line proximity; and
  :func:`match_job` / :func:`parse_matches` for the same mapping made by a reader agent
  (``memory_item_match.v1``) that sees the items and entries but no later meeting.
* :func:`fates_agreement`: the record's event in Y for each item of X against the item-fates
  readers' label for the same item.

No network and no model here.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from tenor.ami.corpus import ROLE_NAMES, AmiMeeting
from tenor.evals import came_back as K
from tenor.evals import item_fates as F
from tenor.memory.series_record import (
    Entry,
    EntryKind,
    Line,
    LineRef,
    SeriesRecord,
    Step,
    make_entry,
)

SEED_METHOD = "annotation_seed"
MATCH_PROMPT = "memory_item_match.v1"
MATCH_PAD = 3  # lines either side of an annotator-linked line that still count as the same place
_ROLE = re.compile("|".join(re.escape(r) for r in ROLE_NAMES.values()))
# item-fates label -> the record event kind that means the same thing
FATE_TO_EVENT: Mapping[str, tuple[str, ...]] = {
    "reported": ("reported",),
    "returned_unchanged": ("reaffirmed",),
    "changed_openly": ("changed_openly", "dropped"),
    "changed_silently": ("changed_without_comment",),
    "not_mentioned": ("not_mentioned",),
    "not_expected": ("not_mentioned",),
}
# coarser classes both sides map into
COARSE: Mapping[str, str] = {
    "reported": "came_back",
    "reaffirmed": "came_back",
    "returned_unchanged": "came_back",
    "changed_openly": "changed_openly",
    "dropped": "changed_openly",
    "changed_without_comment": "changed_silently",
    "changed_silently": "changed_silently",
    "not_mentioned": "absent",
    "not_expected": "absent",
}


def transcript(m: AmiMeeting) -> dict[str, Line]:
    """Line id -> line for every dialogue act, in order."""
    out = {}
    for i, a in enumerate(m.dialogue_acts):
        lid = K.line_id(i)
        out[lid] = Line(line_id=lid, speaker=K._role(m, a.agent), text=a.text)
    return out


def transcript_text(m: AmiMeeting) -> str:
    """The transcript as an agent reads it, one line per dialogue act."""
    return "\n".join(K.line(m, i) for i in range(len(m.dialogue_acts)))


def roster_line(m: AmiMeeting) -> str:
    return K.roster(m, m).splitlines()[0]


def owner_of(text: str) -> str | None:
    """The first role named in an annotator's sentence ("The Marketing Expert will ...")."""
    hit = _ROLE.search(text)
    return hit.group(0) if hit else None


def seed_record(series: str, m: AmiMeeting) -> SeriesRecord:
    """The record after ``m``, from the annotator's decisions and actions. Items the annotator
    linked to no dialogue act cannot be cited and raise."""
    lines = transcript(m)
    entries: list[Entry] = []
    for n, item in enumerate(F.items(m), start=1):
        if not item.linked:
            raise ValueError(f"{m.meeting_id} {item.item_id} has no linked lines to cite")
        refs = [
            LineRef(meeting=m.meeting_id, line=K.line_id(i), quote=lines[K.line_id(i)].text)
            for i in item.linked
        ]
        kind: EntryKind = "decision" if item.item_id.startswith("D") else "commitment"
        owner = owner_of(item.text) if kind == "commitment" else None
        entries.append(
            make_entry(
                n,
                kind,
                item.text,
                owner,
                m.meeting_id,
                "annotation",
                refs,
                f"Recorded by the annotator of {m.meeting_id}.",
                annotation_id=f"{m.meeting_id}:{item.item_id}",
            )
        )
    return SeriesRecord(
        series=series,
        meetings=(m.meeting_id,),
        steps=(Step(meeting=m.meeting_id, method=SEED_METHOD),),
        entries=tuple(entries),
    )


def _line_index(line_id: str) -> int:
    hit = K.LINE_ID.match(line_id)
    if hit is None:
        raise ValueError(f"not a line id: {line_id!r}")
    return int(hit[1])


def match_items(
    items: Sequence[K.Item], entries: Sequence[Entry], meeting: str, pad: int = MATCH_PAD
) -> dict[str, str | None]:
    """Annotator item id -> the entry introduced in ``meeting`` that stands for it, or None.

    Entries seeded from the annotation match by ``annotation_id``. Otherwise an entry matches
    an item when one of its ``introduced`` pointers lies within ``pad`` lines of a line the
    annotator linked to the item; the entry with most such pointers wins, then the one whose
    nearest pointer is closest, then the lower id. Proximity is a proxy: report it as such."""
    own = [e for e in entries if e.introduced_in == meeting]
    out: dict[str, str | None] = {}
    for item in items:
        tag = f"{meeting}:{item.item_id}"
        seeded = [e.entry_id for e in own if e.annotation_id == tag]
        if seeded:
            out[item.item_id] = seeded[0]
            continue
        best: tuple[int, int, str] | None = None
        for e in own:
            cited = [_line_index(r.line) for r in e.history[0].evidence]
            dists = [min(abs(c - x) for x in item.linked) for c in cited] if item.linked else []
            near = [d for d in dists if d <= pad]
            if not near:
                continue
            key = (-len(near), min(near), e.entry_id)
            if best is None or key < best:
                best = key
        out[item.item_id] = best[2] if best else None
    return out


def match_section(m: AmiMeeting, entries: Sequence[Entry]) -> str:
    """One meeting's block of a match job: the annotator's items with their linked lines, then
    the record entries first recorded in that meeting with their cited lines. Nothing after
    the meeting is shown (no events, no statuses)."""
    lines = transcript(m)
    items = []
    for it in F.items(m):
        linked = "\n".join(f"    {K.line_id(i)}: {lines[K.line_id(i)].text}" for i in it.linked)
        items.append(f"- {it.item_id} ({F.item_kind(it.item_id)}): {it.text}\n{linked}")
    own = []
    for e in entries:
        if e.introduced_in != m.meeting_id:
            continue
        cited = "\n".join(f'    {r.line}: "{r.quote}"' for r in e.history[0].evidence)
        own.append(f"- {e.entry_id} ({e.kind}, owner: {e.owner or '-'}): {e.text}\n{cited}")
    return (
        f"## Meeting {m.meeting_id}\n\n### Items (annotator)\n\n"
        + "\n".join(items)
        + "\n\n### Entries (note-keeper)\n\n"
        + ("\n".join(own) or "(none)")
    )


class Match(BaseModel):
    """One line of a match reader's answer."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    meeting: str
    item: str
    entry: str | None
    also: tuple[str, ...] = ()
    reason: str = ""


def parse_matches(
    text: str, expected: Mapping[str, Sequence[str]], entries: Mapping[str, Sequence[str]]
) -> tuple[dict[str, str | None], list[str]]:
    """``"<meeting>:<item>" -> entry id or None`` from a match reader's JSONL, and problems.
    ``expected``: meeting -> item ids; ``entries``: meeting -> entry ids it may name."""
    out: dict[str, str | None] = {}
    errors = []
    for n, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            got = Match.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            errors.append(f"line {n}: {str(e).splitlines()[0]}")
            continue
        key = f"{got.meeting}:{got.item}"
        if got.item not in expected.get(got.meeting, ()):
            errors.append(f"line {n}: unknown item {key}")
        elif got.entry is not None and got.entry not in entries.get(got.meeting, ()):
            errors.append(f"line {n}: {key} names {got.entry}, not an entry of {got.meeting}")
        elif key in out:
            errors.append(f"line {n}: second line for {key}")
        else:
            out[key] = got.entry
    for m, ids in expected.items():
        errors += [f"no line for {m}:{i}" for i in ids if f"{m}:{i}" not in out]
    return out, errors


def event_in(entry: Entry, meeting: str) -> str | None:
    """The kind of ``entry``'s event in ``meeting`` (None if it has none)."""
    return next((ev.kind for ev in entry.history if ev.meeting == meeting), None)


def fates_agreement(
    pairs: Sequence[tuple[str, str, Sequence[K.Item], Mapping[str, str]]],
    record: SeriesRecord,
    mapping: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """For ``(x, y, x_items, fates)`` rows (``fates``: item id -> final item-fates label), the
    record's event in ``y`` for the entry matched to each item, against the label: exact (the
    label's event kinds, :data:`FATE_TO_EVENT`) and coarse (:data:`COARSE`). Items with no
    matching entry are counted apart, not as disagreements. ``mapping`` (``"<x>:<item>" ->
    entry``) overrides :func:`match_items` for the items it names, except items seeded from
    the annotation, which always match their own entry."""
    by_id = {e.entry_id: e for e in record.entries}
    rows = []
    for x, y, items, fates in pairs:
        matched = match_items(items, record.entries, x)
        for item in items:
            tag = f"{x}:{item.item_id}"
            seeded = by_id.get(matched[item.item_id] or "")
            if mapping is not None and tag in mapping and not (seeded and seeded.annotation_id):
                matched[item.item_id] = mapping[tag]
        for item in items:
            label = fates.get(item.item_id)
            if label is None:
                continue
            eid = matched[item.item_id]
            got = event_in(by_id[eid], y) if eid else None
            rows.append(
                {
                    "pair": f"{x}__{y}",
                    "item": item.item_id,
                    "fate": label,
                    "entry": eid,
                    "record_event": got,
                    "exact": got in FATE_TO_EVENT[label] if got else None,
                    "coarse": COARSE[got] == COARSE[label] if got else None,
                }
            )
    scored = [r for r in rows if r["entry"] is not None and r["record_event"] is not None]
    confusion = Counter(f"{r['fate']} -> {r['record_event']}" for r in scored)
    return {
        "items": len(rows),
        "matched": len(scored),
        "unmatched": [f"{r['pair']}:{r['item']}" for r in rows if r["entry"] is None],
        "exact": sum(bool(r["exact"]) for r in scored),
        "coarse": sum(bool(r["coarse"]) for r in scored),
        "confusion": dict(sorted(confusion.items())),
        "rows": rows,
    }
