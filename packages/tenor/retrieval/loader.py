"""Build a ToolBox over AMI meetings, with switches for how close it is to a real Granola workspace.

Realism switches (defaults are the realistic choice where one exists):

- notes:
  - "none" (default): no notes. Honest but harsher than Granola, whose agents read notes first.
  - "product": Granola's own notes for the meeting, captured earlier by playing the recording
    into Granola (data/private/reader/*/<id>__granola.md). Most realistic; only some meetings.
  - "ami_summary": AMI's human summary. Debug only: the facts the harness scores ARE sentences
    of this summary, so reading these notes leaks the answer key.
- speakers:
  - "roles" (default): AMI's role labels ("Project Manager"). Easier than reality.
  - "anonymous": every speaker shown as "Them". Granola transcripts are channel-attributed
    (Me/Them) with no names; in AMI nobody is the note owner, so everyone is "Them".
- distractors: extra meetings in the same workspace. A series alone is 4 meetings; a real
  workspace holds hundreds, which is what makes finding the right meeting hard.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Literal

from tenor.ami.corpus import load_meeting, meeting_note
from tenor.evals.meeting_memory import transcript
from tenor.memory.series_record import Line, SeriesRecord, render
from tenor.retrieval.tools import Meeting, ToolBox

NotesSource = Literal["none", "product", "ami_summary"]
Speakers = Literal["roles", "anonymous"]
PACKETS = Path("data/private/reader")
_BULLET = re.compile(r"^B\d+ \[section: ([^\]]+)\] (?:\(sub-point of B\d+\) )?(.*)$")


@cache
def product_notes(meeting_id: str) -> str | None:
    """Granola's notes for an AMI meeting, rebuilt as markdown from a saved reader packet."""
    found = [PACKETS / d / f"{meeting_id}__granola.md" for d in ("packets", "e4_packets")]
    packets = [p for p in found if p.is_file()]
    if not packets:
        return None
    text = packets[0].read_text()
    m = re.search(r"## 4\. The product's notes.*?```\n(.*?)```", text, re.S)
    if not m:
        return None
    out, section = [], None
    for raw in m.group(1).splitlines():
        b = _BULLET.match(raw.strip())
        if not b:
            continue
        if b.group(1) != section:
            section = b.group(1)
            out.append(f"\n### {section}")
        indent = "  " if "(sub-point of" in raw else ""
        out.append(f"{indent}- {b.group(2)}")
    return "\n".join(out).strip()


def ami_meeting(
    meeting_id: str, notes: NotesSource = "none", speakers: Speakers = "roles"
) -> Meeting:
    """One AMI meeting as the tools see it. Title: the series and its stage (a to d)."""
    m = load_meeting(meeting_id)
    stage = {"a": "kick-off", "b": "functional design", "c": "conceptual design"}
    title = f"{meeting_id[:-1]} {stage.get(meeting_id[-1], 'detailed design')}"
    lines = transcript(m)
    if speakers == "anonymous":
        lines = {k: Line(line_id=v.line_id, speaker="Them", text=v.text) for k, v in lines.items()}
    if notes == "product":
        text = product_notes(meeting_id) or ""
    elif notes == "ami_summary":
        text = meeting_note(m).summary_markdown or ""
    else:
        text = ""
    return Meeting(meeting_id=meeting_id, title=title, notes=text, lines=lines)


def series_record_text(path: Path) -> str:
    """A saved series record (SeriesRecord JSON) in the compact view an agent reads."""
    record = SeriesRecord.model_validate_json(path.read_text(encoding="utf-8"))
    return render(record, compact=True)


def ami_toolbox(
    meeting_ids: Sequence[str],
    notes: NotesSource = "none",
    speakers: Speakers = "roles",
    distractors: Sequence[str] = (),
    k: int = 5,
    records: Mapping[str, Path] | None = None,
) -> ToolBox:
    """A ToolBox over these meetings plus `distractors` (other meetings in the same workspace),
    with an optional running record per series (series id -> record file)."""
    ids = list(dict.fromkeys([*meeting_ids, *distractors]))
    texts = {s: series_record_text(p) for s, p in (records or {}).items()}
    return ToolBox([ami_meeting(i, notes, speakers) for i in ids], k=k, records=texts)
