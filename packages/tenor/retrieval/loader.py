"""Build a ToolBox over AMI meetings: the note is AMI's human summary, the transcript its lines."""

from __future__ import annotations

from collections.abc import Sequence

from tenor.ami.corpus import load_meeting, meeting_note
from tenor.evals.meeting_memory import transcript
from tenor.retrieval.tools import Meeting, ToolBox


def ami_meeting(meeting_id: str) -> Meeting:
    """One AMI meeting as the tools see it. Title: the series and its position (a to d)."""
    m = load_meeting(meeting_id)
    note = meeting_note(m)
    position = {"a": "kick-off", "b": "functional design", "c": "conceptual design"}
    title = f"{meeting_id[:-1]} {position.get(meeting_id[-1], 'detailed design')}"
    return Meeting(
        meeting_id=meeting_id,
        title=title,
        notes=note.summary_markdown or "",
        lines=transcript(m),
    )


def ami_toolbox(meeting_ids: Sequence[str], k: int = 5) -> ToolBox:
    """A ToolBox over these AMI meetings."""
    return ToolBox([ami_meeting(i) for i in meeting_ids], k=k)
