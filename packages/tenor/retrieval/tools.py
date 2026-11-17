"""Step 2: the agent's tools over notes and transcript chunks; each call logged with its output.

The tools mirror the shapes of Granola's MCP (search_meetings, search_transcripts,
get_meeting_transcript with chunk ids, get_meetings). Nothing here knows about MCP: these are
plain functions, so they can be tested directly; ``server.py`` and the CLI only wrap them.

Every call is appended to ``ToolBox.log`` as the text the agent actually received. Step 3's
"retrieved?" check reads that log: a fact counts as retrieved only if its passage is in it.
Tool and server wording live in ``WORDING`` under a version, because step 4 varies them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from tenor.memory.series_record import Line
from tenor.retrieval.chunks import BM25Index, Chunk, chunk_meeting

WORDING: dict[str, dict[str, str]] = {
    "v1": {
        "server": "Meeting notes and transcripts for one workspace. Notes summarise each meeting; "
        "transcripts hold what was actually said.",
        "search_meetings": "Find meetings by keywords in their notes. No keywords: list all.",
        "search_transcripts": "Find transcript passages by keywords. Returns only matching chunks.",
        "get_transcript": "Read specific transcript chunks of one meeting by chunk id.",
        "get_meetings": "Read the notes of one or more meetings by id.",
    },
}


class Meeting(BaseModel):
    """One meeting as the tools see it: an id, a title, its notes and its transcript lines."""

    model_config = ConfigDict(frozen=True)

    meeting_id: str
    title: str
    notes: str
    lines: Mapping[str, Line]


class ToolCall(BaseModel):
    """One logged call: the tool, its arguments and exactly the text returned to the agent."""

    model_config = ConfigDict(frozen=True)

    tool: str
    args: dict[str, object]
    output: str


class ToolBox:
    """The four tools over a fixed set of meetings, with a call log."""

    def __init__(
        self, meetings: Sequence[Meeting], chunk_size: int = 15, overlap: int = 3, k: int = 5
    ) -> None:
        self.meetings = {m.meeting_id: m for m in meetings}
        self.k = k
        self.chunks: list[Chunk] = [
            c for m in meetings for c in chunk_meeting(m.meeting_id, m.lines, chunk_size, overlap)
        ]
        self.by_id = {(c.meeting, c.chunk_id): c for c in self.chunks}
        self.transcript_index = BM25Index(self.chunks)
        self.note_index = BM25Index(
            [
                Chunk(
                    meeting=m.meeting_id,
                    chunk_id=0,
                    first_line="",
                    last_line="",
                    speakers=(),
                    text=f"{m.title}\n{m.notes}",
                    version="",
                )
                for m in meetings
            ]
        )
        self.log: list[ToolCall] = []

    def _record(self, tool: str, args: dict[str, object], output: str) -> str:
        self.log.append(ToolCall(tool=tool, args=args, output=output))
        return output

    def search_meetings(self, keywords: str | None = None) -> str:
        """Meeting ids and titles: all of them in id order, or those whose notes match."""
        if keywords:
            ids = [c.meeting for c, _ in self.note_index.search(keywords, k=len(self.meetings))]
        else:
            ids = sorted(self.meetings)
        out = "\n".join(f"{i}: {self.meetings[i].title}" for i in ids) or "No meetings matched."
        return self._record("search_meetings", {"keywords": keywords}, out)

    def search_transcripts(self, keywords: str, meeting_ids: Iterable[str] | None = None) -> str:
        """The top matching chunks, each with its meeting, chunk id, speakers and text."""
        ids = list(meeting_ids) if meeting_ids is not None else None
        hits = self.transcript_index.search(keywords, k=self.k, meetings=ids)
        out = "\n\n".join(_passage(c) for c, _ in hits) or "No passages matched."
        return self._record("search_transcripts", {"keywords": keywords, "meeting_ids": ids}, out)

    def get_transcript(self, meeting_id: str, chunk_ids: Sequence[int]) -> str:
        """The requested chunks of one meeting, in order; unknown ids are named, not guessed."""
        parts = []
        for cid in chunk_ids:
            c = self.by_id.get((meeting_id, cid))
            parts.append(_passage(c) if c else f"[{meeting_id} chunk {cid}: no such chunk]")
        args: dict[str, object] = {"meeting_id": meeting_id, "chunk_ids": list(chunk_ids)}
        return self._record("get_transcript", args, "\n\n".join(parts))

    def get_meetings(self, meeting_ids: Sequence[str]) -> str:
        """The notes of each meeting, headed by id and title."""
        parts = []
        for mid in meeting_ids:
            m = self.meetings.get(mid)
            parts.append(f"# {mid}: {m.title}\n{m.notes}" if m else f"# {mid}: no such meeting")
        return self._record("get_meetings", {"meeting_ids": list(meeting_ids)}, "\n\n".join(parts))

    def seen_text(self) -> str:
        """Everything the agent has been shown, joined: the input to the retrieved check."""
        return "\n".join(c.output for c in self.log)


def _passage(c: Chunk) -> str:
    head = (
        f"[{c.meeting} chunk {c.chunk_id} | {c.first_line}-{c.last_line} | {', '.join(c.speakers)}]"
    )
    return f"{head}\n{c.text}"


__all__ = ["WORDING", "Meeting", "ToolBox", "ToolCall"]
