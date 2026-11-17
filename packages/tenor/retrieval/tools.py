"""Step 2: the agent's tools over notes and transcript chunks; each call logged with its output.

The tools mirror the shapes of Granola's MCP (search_meetings, search_transcripts,
get_meeting_transcript with chunk ids, get_meetings). Nothing here knows about MCP: these are
plain functions, so they can be tested directly; ``server.py`` and the CLI only wrap them.

Every call is appended to ``ToolBox.log`` as the text the agent actually received. Step 3's
"retrieved?" check reads that log: a fact counts as retrieved only if its passage is in it.
Tool and server wording live in ``WORDING`` under a version, because step 4 varies them.
"""

from __future__ import annotations

import re
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
        "get_series_record": "Read the running record of a meeting series: every decision and "
        "commitment, what happened to it in each later meeting, and the transcript lines behind "
        "it (with chunk ids for get_transcript).",
    },
}
# v2: v1 plus the post's fix for reworded quotes, in the server instructions (one change only).
WORDING["v2"] = {
    **WORDING["v1"],
    "server": WORDING["v1"]["server"] + " Paraphrase by default; quote words directly only when "
    "the person asks for a direct quote.",
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
        self,
        meetings: Sequence[Meeting],
        chunk_size: int = 15,
        overlap: int = 3,
        k: int = 5,
        records: Mapping[str, str] | None = None,
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
        self.records = dict(records or {})
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

    def chunk_of(self, meeting_id: str, line_id: str) -> int | None:
        """The first chunk of a meeting that holds a line (chunks overlap)."""
        for c in self.chunks:
            if c.meeting == meeting_id and c.first_line <= line_id <= c.last_line:
                return c.chunk_id
        return None

    def get_series_record(self, series: str) -> str:
        """A series' running record, each `<meeting> <line>` pointer followed by its chunk id."""
        text = self.records.get(series)
        if text is None:
            names = ", ".join(sorted(self.records)) or "none"
            out = f"No record for series {series}. Records exist for: {names}."
        else:

            def tag(m: re.Match[str]) -> str:
                cid = self.chunk_of(m.group(1), m.group(2))
                return m.group(0) if cid is None else f"{m.group(0)} (chunk {cid})"

            out = _POINTER.sub(tag, text)
        return self._record("get_series_record", {"series": series}, out)

    def seen_text(self) -> str:
        """Everything the agent has been shown, joined: the input to the retrieved check."""
        return "\n".join(c.output for c in self.log)


_POINTER = re.compile(r"\b([A-Z]{2}\d{4}[a-z]) (L\d{4})\b")


def _passage(c: Chunk) -> str:
    head = (
        f"[{c.meeting} chunk {c.chunk_id} | {c.first_line}-{c.last_line} | {', '.join(c.speakers)}]"
    )
    return f"{head}\n{c.text}"


__all__ = ["WORDING", "Meeting", "ToolBox", "ToolCall"]
