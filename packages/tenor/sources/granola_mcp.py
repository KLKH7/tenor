"""``GranolaMCPSource``: read Granola notes through the official MCP server.

Why: the public REST API needs a Business/Enterprise plan; the MCP works on every plan
(free plans: last 30 days). This source lets Concentrate run on real notes for anyone with a
Granola account, through the same ``Source`` protocol as the API and fixtures.

How: only read-only tools are called: ``list_meetings`` (with ``updated_after``, so rows carry
``updated_at``), ``get_meetings`` (notes, summary, participants) and ``get_meeting_transcript``
(paged by chunk, which yields structured passages with speaker labels and start times).
Responses are agent-oriented text (XML-like for meetings, JSON for transcripts and folders), so
parsing is deliberately lenient; every mapping we had to infer is tagged in
docs/engineering/SCHEMA_NOTES.md ("MCP source mapping").

Rate limit: Granola documents "around 100 requests per minute across all tools"; we pace at
that rate with a small burst, and honour ``retry_after_seconds`` on rate-limit errors.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from tenor.core import granola
from tenor.sources.base import NoteNotReady, SourceError
from tenor.sources.ratelimit import Clock, RateLimiter, SystemClock

log = logging.getLogger(__name__)

GRANOLA_MCP_URL = "https://mcp.granola.ai/mcp"
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)  # "all accessible history" lower bound
TRANSCRIPT_BATCH = 10  # get_meeting_transcript reads at most 10 chunks per call
WORDS_PER_SECOND = 2.6  # only used to estimate the end of a passage with no successor


class McpCaller(Protocol):
    """Calls one MCP tool and returns its text content. Raises ``McpToolError`` on errors."""

    def call(self, tool: str, arguments: dict[str, Any]) -> str: ...


class McpToolError(SourceError):
    def __init__(self, tool: str, message: str, retry_after: float | None = None) -> None:
        super().__init__(f"MCP tool {tool} failed: {message[:300]}")
        self.retry_after = retry_after


# -- parsing ------------------------------------------------------------------------------------

# ``<summary status="...">`` values meaning "no AI summary" (observed 2026-11-05 and -09).
NO_SUMMARY = frozenset({"transcript_only", "empty"})
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')
_MEETING = re.compile(r"<meeting\s([^>]*)>(.*?)</meeting>", re.S)
_PARTICIPANT = re.compile(
    r"\s*(?P<name>[^,(\n]+?)(?P<creator>\s\(note creator\))?(?:\sfrom\s[^(\n]+?)?"
    r"\s\((?P<email>[^()\s]+@[^()\s]+)\)"
)
_SPEAKER = re.compile(r"^(?P<source>Microphone|System audio)(?:\s\((?P<name>.+)\))?$")
_KNOWN_LABEL = re.compile(r"^(?:Microphone|System audio(?: \(.+\))?|Speaker \w+)$")


def _attrs(s: str) -> dict[str, str]:
    return {k: html.unescape(v) for k, v in _ATTR.findall(s)}


def _section(body: str, tag: str) -> str | None:
    """Inner text of ``<tag ...>...</tag>``; None if absent or self-closing."""
    m = re.search(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", body, re.S)
    return html.unescape(m.group(1)).strip() if m else None


def parse_participants(block: str) -> list[tuple[str | None, str, bool]]:
    """``known_participants`` text → (name, email, is_note_creator).

    Format seen live: ``Name (note creator) from Org (email), Name (email), ...``. A participant
    without a display name is rendered with their email handle as the name (e.g. ``Az3021``),
    which we keep verbatim: it's what the MCP knows.
    """
    out = []
    for m in _PARTICIPANT.finditer(block):
        name = m.group("name").strip().strip(",").strip() or None
        out.append((name, m.group("email"), m.group("creator") is not None))
    return out


def parse_meeting_rows(text: str) -> list[dict[str, Any]]:
    """``list_meetings`` / ``get_meetings`` output → one dict per ``<meeting>`` element."""
    rows = []
    for attrs, body in _MEETING.findall(text):
        row: dict[str, Any] = _attrs(attrs)
        row["participants"] = parse_participants(_section(body, "known_participants") or "")
        tag = re.search(r"<summary(\s[^>]*)?>", body)
        status = _attrs(tag.group(1) or "").get("status") if tag else None
        # A note with no AI summary carries a placeholder sentence, not notes (VERIFIED).
        row["summary"] = None if status in NO_SUMMARY else _section(body, "summary")
        row["private_notes"] = _section(body, "private_notes")
        row["folders"] = [_attrs(a) for a in re.findall(r"<folder\s([^>]*?)/?>", body)]
        rows.append(row)
    return rows


def _pagination(text: str) -> dict[str, str]:
    m = re.search(r"<pagination\s([^>]*)/?>", text)
    return _attrs(m.group(1)) if m else {}


def _owner(row: dict[str, Any], fallback: granola.User) -> granola.User:
    for name, email, creator in row["participants"]:
        if creator:
            return granola.User(name=name, email=email)
    return fallback


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _speaker(label: str | None) -> granola.Speaker:
    """``Microphone`` / ``System audio`` / ``System audio (Name)`` / ``Speaker A`` → Speaker."""
    label = (label or "").strip()
    m = _SPEAKER.match(label)
    if m:
        source = "microphone" if m.group("source") == "Microphone" else "speaker"
        name = m.group("name")
        return granola.Speaker(source=source, **({"name": name} if name else {}))
    if label:  # mobile/imported transcripts keep diarization labels (e.g. "Speaker A")
        return granola.Speaker(source="microphone", diarization_label=label)
    return granola.Speaker(source="speaker")


def passages_to_transcript(passages: list[dict[str, Any]]) -> list[granola.TranscriptItem]:
    """MCP passages carry a start time only; end = next passage's start (or a length estimate)."""
    items: list[granola.TranscriptItem] = []
    times: list[datetime | None] = [
        datetime.fromisoformat(p["time"]) if p.get("time") else None for p in passages
    ]
    last: datetime | None = None
    for i, p in enumerate(passages):
        start = times[i] or last
        if start is None:
            continue  # untimed leading passage: no defensible timestamp, skip
        nxt = next((t for t in times[i + 1 :] if t is not None and t > start), None)
        estimate = start + timedelta(seconds=max(0.5, len(p["text"].split()) / WORDS_PER_SECOND))
        end = min(nxt, estimate) if nxt else estimate
        items.append(
            granola.TranscriptItem(
                speaker=_speaker(p.get("speaker")), text=p["text"], start_time=start, end_time=end
            )
        )
        last = start
    return items


_RENDERED_LINE = re.compile(
    r"^(?:\[(?:(?P<h>\d+):)?(?P<m>\d+):(?P<s>\d{2})\]\s+)?(?P<label>[^:\n]{1,80}?):\s(?P<text>.*)$"
)


def rendered_to_passages(text: str, base: datetime) -> list[dict[str, Any]]:
    """Parse a chunk's rendered ``transcript`` text into passage dicts.

    Seen live on 2026-11-05: chunks carry one ``transcript`` string of ``[m:ss] Label: text``
    lines (blank-line separated) instead of a ``passages`` list. Offsets count from the
    transcript's earliest timed block, so ``base`` (the note's creation time) anchors them;
    absolute times are therefore approximate. Lines without a ``Label:`` prefix continue the
    previous passage.
    """
    passages: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _RENDERED_LINE.match(line.strip())
        if m is None or (m["m"] is None and not _KNOWN_LABEL.match(m["label"])):
            if passages:
                passages[-1]["text"] += " " + line.strip()
            continue
        time = None
        if m["m"] is not None:
            secs = int(m["h"] or 0) * 3600 + int(m["m"]) * 60 + int(m["s"])
            time = (base + timedelta(seconds=secs)).isoformat()
        passages.append({"speaker": m["label"], "time": time, "text": m["text"]})
    return passages


# -- source -------------------------------------------------------------------------------------


class GranolaMCPSource:
    """Implements :class:`tenor.sources.base.Source` over Granola's MCP server."""

    name = "granola-mcp"

    def __init__(
        self,
        caller: McpCaller,
        *,
        clock: Clock | None = None,
        max_retries: int = 3,
    ) -> None:
        self._caller = caller
        self._clock = clock or SystemClock()
        # ~100 requests/minute across all tools (Granola MCP docs), small burst.
        self._limiter = RateLimiter(self._clock, burst=10, window_s=6.0, rate_per_s=100 / 60)
        self._max_retries = max_retries
        self._account: granola.User | None = None

    def _call(self, tool: str, arguments: dict[str, Any]) -> str:
        for attempt in range(self._max_retries + 1):
            self._limiter.acquire()
            try:
                return self._caller.call(tool, arguments)
            except McpToolError as exc:
                if exc.retry_after is None or attempt == self._max_retries:
                    raise
                log.warning("%s rate limited; retrying in %.1fs", tool, exc.retry_after)
                self._limiter.penalize(exc.retry_after)
        raise AssertionError("unreachable")  # pragma: no cover

    def account(self) -> granola.User:
        """The connected Granola user (used as owner when a note names no creator)."""
        if self._account is None:
            info = json.loads(self._call("get_account_info", {}))
            self._account = granola.User(name=None, email=info["email"])
        return self._account

    def list_notes(
        self,
        *,
        updated_after: datetime | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        folder_id: str | None = None,
    ) -> Iterator[granola.McpNoteSummary]:
        """Yield every accessible note. ``updated_after`` is always sent (see module doc)."""
        args: dict[str, Any] = {
            "updated_after": _iso(updated_after or EPOCH),
            "max_participants": 50,
        }
        if folder_id:
            args["folder_id"] = folder_id
        cursor: str | None = None
        while True:
            text = self._call("list_meetings", {**args, **({"cursor": cursor} if cursor else {})})
            for row in parse_meeting_rows(text):
                meeting_at = datetime.fromisoformat(row["meeting_at"])
                # ASSUMED: list rows carry no created_at; meeting_at is "calendar start, else
                # creation", the closest available value. get_note() has the real created_at.
                if created_after and not meeting_at > created_after:
                    continue
                if created_before and not meeting_at < created_before:
                    continue
                yield granola.McpNoteSummary(
                    id=row["id"],
                    object="note",
                    title=row.get("title"),
                    owner=_owner(row, self.account()),
                    created_at=meeting_at,
                    updated_at=datetime.fromisoformat(row.get("updated_at") or row["meeting_at"]),
                    deleted_at=None,  # the MCP never returns deleted notes
                )
            page = _pagination(text)
            if page.get("has_more") != "true" or not page.get("next_cursor"):
                return
            cursor = page["next_cursor"]

    def get_note(self, note_id: str) -> granola.McpNote:
        """Fetch one note (notes, summary, participants) plus its full transcript."""
        try:
            text = self._call("get_meetings", {"meeting_ids": [note_id], "attachments": "none"})
        except McpToolError as exc:
            if "not found" in str(exc).lower():
                raise NoteNotReady(note_id) from None
            raise
        rows = parse_meeting_rows(text)
        if not rows:
            raise NoteNotReady(note_id)
        row = rows[0]
        participants = row["participants"]
        meeting_at = datetime.fromisoformat(row["meeting_at"])
        created_at = datetime.fromisoformat(row.get("created_at") or row["meeting_at"])
        summary = row["summary"]
        # ASSUMED: known_participants come from the calendar invite (+ manual adds + creator), so
        # a meeting whose meeting_at differs from created_at had a calendar event.
        has_event = row.get("created_at") is not None and meeting_at != created_at
        event = (
            granola.CalendarEvent(
                event_title=row.get("title"),
                invitees=[granola.CalendarInvitee(email=e) for _, e, _ in participants],
                organiser=None,  # not exposed by the MCP
                calendar_event_id=None,  # not exposed by the MCP
                scheduled_start_time=meeting_at,
                scheduled_end_time=None,
            )
            if has_event
            else None
        )
        return granola.McpNote(
            id=row["id"],
            object="note",
            title=row.get("title"),
            owner=_owner(row, self.account()),
            created_at=created_at,
            updated_at=datetime.fromisoformat(row.get("updated_at") or row["meeting_at"]),
            deleted_at=None,
            web_url=row.get("url") or f"https://notes.granola.ai/d/{row['id']}",
            calendar_event=event,
            attachments=[],
            attendees=[granola.User(name=n, email=e) for n, e, _ in participants],
            folder_membership=[
                granola.McpFolder(
                    id=f["id"],
                    object="folder",
                    name=f.get("title") or f.get("name") or "",
                    parent_folder_id=f.get("parent_folder_id") or None,
                )
                for f in row["folders"]
                if f.get("id")
            ],
            summary_text=summary or "",
            summary_markdown=summary,
            private_notes_text=row["private_notes"] or None,
            private_notes_markdown=row["private_notes"] or None,
            transcript=self._transcript(row["id"], created_at),
        )

    def _transcript(self, note_id: str, base: datetime) -> list[granola.TranscriptItem]:
        passages: list[dict[str, Any]] = []
        version: str | None = None
        start, total = 0, None
        while total is None or start < total:
            args: dict[str, Any] = {
                "meeting_id": note_id,
                "chunk_ids": list(range(start, start + TRANSCRIPT_BATCH)),
            }
            if version:
                args["transcript_version"] = version
            page = json.loads(self._call("get_meeting_transcript", args))
            if page.get("status") != "ok":  # unavailable / pending / out_of_range: no transcript
                return passages_to_transcript(passages)
            version = page.get("transcript_version") or version
            total = int(page.get("total_chunks") or 0)
            for chunk in sorted(page.get("chunks", []), key=lambda c: c["chunk_id"]):
                if "passages" in chunk:  # format seen until 2026-11-04
                    passages.extend(chunk["passages"])
                else:  # rendered text, seen from 2026-11-05
                    passages.extend(rendered_to_passages(chunk.get("transcript", ""), base))
            start += TRANSCRIPT_BATCH
        return passages_to_transcript(passages)

    def list_folders(self) -> Iterator[granola.Folder]:
        """Yield folders. Field names beyond ``folders``/``count`` are ASSUMED (none seen yet)."""
        data = json.loads(self._call("list_meeting_folders", {}))
        for f in data.get("folders", []):
            if not f.get("id"):
                continue
            yield granola.McpFolder(
                id=str(f["id"]),
                object="folder",
                name=str(f.get("title") or f.get("name") or ""),
                parent_folder_id=f.get("parent_folder_id"),
            )
