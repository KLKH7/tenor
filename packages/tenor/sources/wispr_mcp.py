"""``WisprMCPSource``: read Wispr Flow Notetaker meetings through Wispr's official MCP server.

Why: TENOR compares meeting-memory products on the *same* meetings. Wispr Flow is the
second real product; this source reads it through the same ``Source`` protocol as Granola, so
sync, storage and every track work unchanged.

How: only three read tools are called, through a :class:`AllowlistCaller` that refuses anything
else before it is sent (the server is read-only too, per Wispr's help centre):

* ``search_meetings`` (no query: newest first, cursor-paginated, ``since``/``until`` filters);
* ``get_meeting`` (bounded markdown ``content``, full ``summary``, attendees without emails,
  and the plaintext transcript via ``view_transcript {start_char, char_limit}``, read in
  ranges of at most 40,000 characters, following the continuation offset);
* ``get_meeting_participants_enriched`` (owned meetings only: the account holder and every
  participant with emails, organiser and role; get_meeting omits emails).

Tool names and arguments are VERIFIED from the server's published tool schemas; the response
*shapes* had not been observed when this was written, so parsing is lenient and every guess is
an ASSUMED row in docs/engineering/SCHEMA_NOTES.md ("Wispr Flow MCP"). All Wispr timestamps are
UTC (VERIFIED, tool descriptions).
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from tenor.core import granola
from tenor.sources.allowlist import AllowlistCaller
from tenor.sources.base import NoteNotReady
from tenor.sources.granola_mcp import EPOCH, WORDS_PER_SECOND, McpCaller, McpToolError
from tenor.sources.ratelimit import Clock, RateLimiter, SystemClock

log = logging.getLogger(__name__)

# ASSUMED default: the URL Wispr's help centre gives for "other MCP clients". The app shows the
# authoritative one (Settings → MCP → "All other apps"); override with WISPR_MCP_URL.
WISPR_MCP_URL = os.environ.get("WISPR_MCP_URL") or "https://api.wisprflow.ai/connect/mcp"
MAX_CHARS = 40_000  # server-enforced max per content/transcript range (VERIFIED, tool schema)
MAX_RANGES = 500  # guard against a server that never stops returning continuation offsets
RATE_LIMIT_WAIT_S = 60.0  # help centre: on "Too many requests", wait about a minute

# Every read tool Wispr's MCP offers (VERIFIED, 2026-11-04). The black-box adapters use subsets.
WISPR_READ_TOOLS = frozenset(
    {
        "search_meetings",
        "get_meeting",
        "get_meeting_participants_enriched",
        "list_folders",
        "list_folder_meetings",
        "list_meeting_series",
        "get_meeting_by_calendar_id",
        "search_calendar_events",
        "get_calendar_event",
        "list_upcoming_meetings",
        "get_upcoming_meeting",
        "get_scratchpad_note",
        "search_scratchpad_notes",
        "resolve_share_link",
        "resolve_calendar_link",
    }
)
SOURCE_TOOLS = frozenset({"search_meetings", "get_meeting", "get_meeting_participants_enriched"})


# -- response helpers (lenient: shapes are ASSUMED) -------------------------------------------


def _json(text: str) -> dict[str, Any]:
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("meeting"), dict):
        return {**data, **data["meeting"]}
    return data if isinstance(data, dict) else {"items": data}


def _obj(d: dict[str, Any], key: str) -> dict[str, Any]:
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def _first(d: dict[str, Any], *keys: str) -> Any:
    return next((d[k] for k in keys if d.get(k) not in (None, "")), None)


def _time(d: dict[str, Any], *keys: str) -> datetime | None:
    v = _first(d, *keys)
    if not isinstance(v, str):
        return None
    dt = datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # Wispr timestamps are UTC


def _start(d: dict[str, Any]) -> datetime | None:
    return _time(d, "start", "start_time", "started_at", "start_at", "starts_at")


def _end(d: dict[str, Any]) -> datetime | None:
    return _time(d, "end", "end_time", "ended_at", "end_at", "ends_at")


def _updated(d: dict[str, Any]) -> datetime | None:
    return _time(d, "updated_at", "modified_at", "last_modified_at")


def _rows(page: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _first(page, "meetings", "results", "items", "data") or []
    return [r for r in rows if isinstance(r, dict)]


# The marker may be followed by the transcript's closing ``<<<END TRANSCRIPT>>>`` banner
# (observed 2026-11-09); the banner of a truncated range is dropped with the marker.
_MARKER = re.compile(
    r"\s*[\[(<][^\[\]()<>]*?\bstart_char\b\D{0,24}(?P<next>\d+)[^\[\]()<>]*[\])>]"
    r"(?:\s*<<<[^<>]*>>>)?\s*\Z",
    re.I,
)


# Every range of a transcript starts with the ``<<<…>>>`` safety banner (VERIFIED 2026-11-09),
# so a continuation range's copy would split the line the previous range ended in.
_BANNER_HEAD = re.compile(r"\A<<<[^<>\n]*>>>\n?")


def split_range(value: Any) -> tuple[str, int | None]:
    """A bounded content/transcript range → (text, next start_char or None when complete).

    The continuation offset is either a field (``{"text", "next_start_char"}``, ASSUMED) or a
    bracketed marker appended to the text that names ``start_char`` and a number (VERIFIED:
    ``(...truncated, N chars remaining; continue with view_transcript.start_char=40000...)``,
    then the ``<<<END TRANSCRIPT>>>`` banner).
    """
    if isinstance(value, dict):
        text = str(_first(value, "text", "content", "transcript") or "")
        nxt = _first(value, "next_start_char", "next_offset", "continuation_offset")
        if nxt is not None:
            return text, int(nxt)
        value = text
    text = str(value or "")
    m = _MARKER.search(text)
    if m:
        return text[: m.start()], int(m.group("next"))
    return text, None


# -- transcript --------------------------------------------------------------------------------

_TS = r"\d{1,2}:\d{2}(?::\d{2})?"
_LINE = re.compile(
    rf"^(?:\[(?P<ts1>{_TS})\]|(?P<ts0>{_TS})(?=\s))?\s*"
    rf"(?:(?P<who>[^\s:\[\]()][^:\[\]\n]{{0,59}}?)\s*(?:[\[(](?P<ts2>{_TS})[\])])?\s*:\s+)?"
    r"(?P<text>\S.*)$"
)
_DIARIZED = re.compile(r"^(?:Speaker|Participant|Unknown)\s*\w{0,3}$", re.I)
_MAX_NAME_WORDS = 5  # a "name" longer than this is a sentence with a colon in it


def _offset(ts: str) -> timedelta:
    parts = [int(p) for p in ts.split(":")]
    h, m, s = ([0, *parts] if len(parts) == 2 else parts)[:3]
    return timedelta(hours=h, minutes=m, seconds=s)


@dataclass
class Turn:
    """One parsed transcript turn (speaker label as written, optional offset from start)."""

    who: str | None
    text: str
    offset: timedelta | None


def parse_transcript(text: str) -> list[Turn]:
    """Wispr's plaintext transcript → turns.

    Recognised (ASSUMED until observed): ``Name: text``, ``[mm:ss] Name: text``,
    ``mm:ss Name: text``, ``Name (mm:ss): text``. A line without a speaker continues the
    previous speaker's turn; if no speaker has been seen yet, each line is its own turn.
    """
    turns: list[Turn] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LINE.match(line)
        if m is None:  # pragma: no cover - the pattern matches any non-blank line
            continue
        ts = m.group("ts1") or m.group("ts0") or m.group("ts2")
        who = m.group("who")
        body = m.group("text")
        if who and len(who.split()) > _MAX_NAME_WORDS:
            who, body = None, line[m.start("who") :]
        offset = _offset(ts) if ts else None
        if who is None and turns and turns[-1].who is not None and offset is None:
            turns[-1].text += " " + body
            continue
        turns.append(Turn(who=who.strip() if who else None, text=body, offset=offset))
    return turns


def turns_to_transcript(
    turns: list[Turn],
    *,
    start: datetime,
    end: datetime | None,
    me_names: set[str],
) -> list[granola.TranscriptItem]:
    """Turns → wire transcript items.

    Wispr has no capture channel (Granola's microphone / system audio). ASSUMED mapping: a turn
    whose speaker is the account holder (``me_names``, lower-case) is ``microphone``; every
    other turn is ``speaker``. ``Speaker 1``-style labels become ``diarization_label``. Without
    offsets, start times are spread across the meeting by character share (synthetic, ordered).
    """
    if not turns:
        return []
    total = sum(len(t.text) for t in turns)
    span = (end - start) if end and end > start else None
    starts: list[datetime] = []
    done = 0
    for t in turns:
        if t.offset is not None:
            starts.append(start + t.offset)
        elif span is not None:
            starts.append(start + span * (done / total))
        else:
            starts.append(start + timedelta(seconds=done / 5 / WORDS_PER_SECOND))
        done += len(t.text)
    items = []
    for i, t in enumerate(turns):
        s = max(starts[i], starts[i - 1]) if i else starts[i]
        starts[i] = s
        nxt = starts[i + 1] if i + 1 < len(turns) else None
        estimate = s + timedelta(seconds=max(0.5, len(t.text.split()) / WORDS_PER_SECOND))
        e = min(nxt, estimate) if nxt and nxt > s else estimate
        label = t.who
        mine = label is not None and label.lower() in me_names
        speaker: dict[str, Any] = {"source": "microphone" if mine else "speaker"}
        if label and _DIARIZED.match(label):
            speaker["diarization_label"] = label
        elif label:
            speaker["name"] = label
        items.append(
            granola.TranscriptItem(
                speaker=granola.Speaker(**speaker), text=t.text, start_time=s, end_time=e
            )
        )
    return items


# -- source -------------------------------------------------------------------------------------


class WisprMCPSource:
    """Implements :class:`tenor.sources.base.Source` over Wispr Flow's MCP server.

    ``include_shared=False`` (default) skips meetings other people shared with the account:
    their transcripts and private notes are withheld by sharing policy, and they are not the
    account holder's recordings, so they can never be paired with a Granola note.
    """

    name = "wispr-mcp"

    def __init__(
        self,
        caller: McpCaller,
        *,
        clock: Clock | None = None,
        max_retries: int = 3,
        include_shared: bool = False,
        page_size: int = 100,
    ) -> None:
        self._caller = AllowlistCaller(caller, SOURCE_TOOLS, "wispr-mcp source")
        self._clock = clock or SystemClock()
        # Rate limit undocumented (ASSUMED): pace at 60/min, burst 5.
        self._limiter = RateLimiter(self._clock, burst=5, window_s=5.0, rate_per_s=1.0)
        self._max_retries = max_retries
        self._include_shared = include_shared
        self._page_size = page_size

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            self._limiter.acquire()
            try:
                return _json(self._caller.call(tool, arguments))
            except McpToolError as exc:
                wait = exc.retry_after
                if wait is None and "too many requests" in str(exc).lower():
                    wait = RATE_LIMIT_WAIT_S
                if wait is None or attempt == self._max_retries:
                    raise
                log.warning("%s rate limited; retrying in %.0fs", tool, wait)
                self._limiter.penalize(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def list_notes(
        self,
        *,
        updated_after: datetime | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        folder_id: str | None = None,
    ) -> Iterator[granola.WisprNoteSummary]:
        """Yield every owned meeting. ``updated_after`` has no server-side equivalent, so the
        whole list is re-read and the poller skips rows whose ``updated_at`` is unchanged."""
        if folder_id is not None:
            raise NotImplementedError("folder filtering is not implemented for Wispr")
        args: dict[str, Any] = {"limit": self._page_size}
        if created_after:
            args["since"] = created_after.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if created_before:
            args["until"] = created_before.astimezone(UTC).isoformat().replace("+00:00", "Z")
        cursor: str | None = None
        while True:
            page = self._call("search_meetings", {**args, **({"cursor": cursor} if cursor else {})})
            for row in _rows(page):
                if row.get("shared") and not self._include_shared:
                    continue
                start = _start(row) or _updated(row) or EPOCH
                owner = _obj(row, "owner")
                yield granola.WisprNoteSummary(
                    id=str(row["id"]),
                    object="note",
                    title=row.get("title"),
                    owner=granola.User(name=owner.get("name"), email=owner.get("email") or ""),
                    created_at=start,  # ASSUMED: no creation time; the meeting start is closest
                    updated_at=_updated(row) or _end(row) or start,
                    deleted_at=None,  # deleted meetings are simply absent (ASSUMED)
                )
            if page.get("truncated"):
                log.warning("search_meetings hit its 1000-result cap; narrow with since/until")
            if not page.get("has_more") or not page.get("next_cursor"):
                return
            cursor = str(page["next_cursor"])

    def _ranges(self, meeting_id: str, view: str, field: str, first: Any) -> str:
        """Concatenate a bounded field, following continuation offsets from ``first``."""
        text, nxt = split_range(first)
        parts = [text]
        for _ in range(MAX_RANGES):
            if nxt is None:
                break
            page = self._call(
                "get_meeting",
                {"meeting_id": meeting_id, view: {"start_char": nxt, "char_limit": MAX_CHARS}},
            )
            text, following = split_range(page.get(field))
            parts.append(_BANNER_HEAD.sub("", text, count=1))
            if following is not None and following <= nxt:
                log.warning("%s: %s offset did not advance; stopping", meeting_id, field)
                break
            nxt = following
        return "".join(parts)

    def _participants(self, meeting_id: str) -> dict[str, Any]:
        try:
            return self._call("get_meeting_participants_enriched", {"meeting_id": meeting_id})
        except McpToolError as exc:
            log.warning("%s: no enriched participants (%s)", meeting_id, type(exc).__name__)
            return {}

    def get_note(self, note_id: str) -> granola.WisprNote:
        """Fetch one meeting: notes, summary, participants and the full transcript."""
        full = {"start_char": 0, "char_limit": MAX_CHARS}
        try:
            m = self._call(
                "get_meeting",
                {"meeting_id": note_id, "view_content": full, "view_transcript": full},
            )
        except McpToolError as exc:
            if "not found" in str(exc).lower():
                raise NoteNotReady(note_id) from None
            raise
        if not m or m.get("finalized") is False:
            raise NoteNotReady(note_id)  # still processing: retry on a later sync
        start = _start(m) or _updated(m) or EPOCH
        end = _end(m)
        content = self._ranges(note_id, "view_content", "content", m.get("content")) or None
        transcript_text = self._ranges(
            note_id, "view_transcript", "transcript", m.get("transcript")
        )
        shared = bool(m.get("shared"))
        enriched = {} if shared else self._participants(note_id)
        user = _obj(enriched, "user")
        people = [p for p in enriched.get("participants") or [] if isinstance(p, dict)]
        me_names = {str(user["name"]).lower()} if user.get("name") else set()
        me_names |= {str(p["name"]).lower() for p in people if p.get("role") == "self"}
        attendees = (
            [
                granola.User(name=p.get("name"), email=next(iter(p.get("emails") or []), ""))
                for p in people
                if p.get("role") != "self"
            ]
            if people
            else [
                granola.User(name=a if isinstance(a, str) else a.get("name"), email="")
                for a in m.get("attendees") or []
            ]
        )
        organiser = next(
            (next(iter(p.get("emails") or []), None) for p in people if p.get("organizer")), None
        )
        owner_row = _obj(m, "owner")
        owner = granola.User(
            name=user.get("name") or owner_row.get("name"),
            email=user.get("email") or owner_row.get("email") or "",
        )
        has_event = _start(m) is not None
        folders = m.get("folders") or []
        return granola.WisprNote(
            id=str(m.get("id") or note_id),
            object="note",
            title=m.get("title"),
            owner=owner,
            created_at=start,
            updated_at=_updated(m) or end or start,
            deleted_at=None,
            web_url=str(m.get("share_link") or ""),
            calendar_event=granola.CalendarEvent(
                event_title=m.get("title"),
                invitees=[granola.CalendarInvitee(email=a.email) for a in attendees if a.email],
                organiser=organiser,
                calendar_event_id=None,
                scheduled_start_time=start,
                scheduled_end_time=end,
            )
            if has_event
            else None,
            attachments=[],
            attendees=attendees,
            folder_membership=[
                granola.McpFolder(
                    id=str(f["id"]),
                    object="folder",
                    name=str(f.get("name") or ""),
                    parent_folder_id=None,
                )
                for f in folders
                if isinstance(f, dict) and f.get("id")
            ],
            summary_text=str(m.get("summary") or ""),
            summary_markdown=m.get("summary"),
            private_notes_text=content,
            private_notes_markdown=content,
            transcript=turns_to_transcript(
                parse_transcript(transcript_text), start=start, end=end, me_names=me_names
            ),
        )

    def list_folders(self) -> Iterator[granola.Folder]:
        """No folders: sync only needs meetings (each meeting carries its own folder list)."""
        return iter(())
