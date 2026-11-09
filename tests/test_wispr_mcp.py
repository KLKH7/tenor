"""WisprMCPSource against a fake Wispr MCP (no network). Response shapes are ASSUMED."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest

from tenor.core import granola
from tenor.core.models import Channel
from tenor.core.store import NoteStore
from tenor.sources.allowlist import ToolNotAllowed
from tenor.sources.base import NoteNotReady
from tenor.sources.poller import sync
from tenor.sources.ratelimit import FakeClock
from tenor.sources.wispr_mcp import (
    SOURCE_TOOLS,
    WISPR_READ_TOOLS,
    WisprMCPSource,
    parse_transcript,
    split_range,
)
from tests.fake_wispr import FakeWispr, meeting

# Wispr's MCP is read-only server-side, but a careless or injected name must still never be sent.
WRITE_OR_UNKNOWN = (
    "create_meeting",
    "update_meeting",
    "delete_meeting",
    "share_meeting",
    "update_sharing",
    "create_scratchpad_note",
    "sign_out",
    "query_meetings",
    "",
)


@pytest.fixture
def fake() -> FakeWispr:
    return FakeWispr(meetings=[meeting("m-1"), meeting("m-2", title="Retro"), meeting("m-3")])


@pytest.fixture
def source(fake: FakeWispr) -> WisprMCPSource:
    return WisprMCPSource(fake, clock=FakeClock())


def test_source_tools_are_a_subset_of_the_read_tools() -> None:
    assert SOURCE_TOOLS <= WISPR_READ_TOOLS


@pytest.mark.parametrize("tool", (*WRITE_OR_UNKNOWN, "list_folder_meetings"))
def test_write_unknown_and_unneeded_tools_are_refused_before_sending(
    fake: FakeWispr, tool: str
) -> None:
    src = WisprMCPSource(fake, clock=FakeClock())
    with pytest.raises(ToolNotAllowed):
        src._caller.call(tool, {})
    assert fake.calls == []


def test_list_notes_pages_through_every_meeting(fake: FakeWispr, source: WisprMCPSource) -> None:
    ids = [n.id for n in source.list_notes()]
    assert ids == ["m-1", "m-2", "m-3"]
    assert [c[0] for c in fake.calls] == ["search_meetings"] * 2
    assert fake.calls[1][1]["cursor"] == "2"


def test_list_notes_skips_shared_meetings_by_default(fake: FakeWispr) -> None:
    fake.meetings.append(meeting("m-shared", shared=True, owner={"name": "Someone Else"}))
    assert "m-shared" not in [n.id for n in WisprMCPSource(fake, clock=FakeClock()).list_notes()]
    both = WisprMCPSource(fake, clock=FakeClock(), include_shared=True)
    assert "m-shared" in [n.id for n in both.list_notes()]


def test_created_after_maps_to_since(fake: FakeWispr, source: WisprMCPSource) -> None:
    list(source.list_notes(created_after=datetime(2026, 11, 1, tzinfo=UTC)))
    assert fake.calls[0][1]["since"] == "2026-11-01T00:00:00Z"


def test_get_note_maps_fields(source: WisprMCPSource) -> None:
    note = source.get_note("m-1")
    assert isinstance(note, granola.WisprNote)
    assert note.summary_markdown and "Next steps" in note.summary_markdown
    assert note.private_notes_markdown and "October" in note.private_notes_markdown
    assert note.owner.email == "kian@example.com"
    assert [a.email for a in note.attendees] == ["priya@example.com"]
    assert note.calendar_event is not None
    assert note.calendar_event.organiser == "priya@example.com"
    assert note.calendar_event.scheduled_start_time == datetime(2026, 11, 3, 15, tzinfo=UTC)
    assert note.folder_membership[0].name == "Pilots"
    assert note.web_url.endswith("/m-1")


def test_transcript_speakers_and_channels(source: WisprMCPSource) -> None:
    items = source.get_note("m-1").transcript or []
    assert [i.speaker.name for i in items] == ["Kian Lakhani", "Priya Shah"] * 2
    # the account holder's turns are "microphone" (= Me), everyone else "speaker" (= Them)
    assert [i.speaker.source for i in items[:2]] == ["microphone", "speaker"]
    assert items[-1].text.endswith("That should be quick.")  # continuation line merged
    starts = [i.start_time for i in items]
    assert starts == sorted(starts)
    assert all(i.end_time <= j.start_time for i, j in itertools.pairwise(items))
    assert starts[0] == datetime(2026, 11, 3, 15, tzinfo=UTC)


def test_long_transcript_and_content_follow_continuation_offsets(fake: FakeWispr) -> None:
    fake.char_limit = 50
    note = WisprMCPSource(fake, clock=FakeClock()).get_note("m-1")
    full = meeting()
    assert note.private_notes_markdown == full["content"]
    rejoined = " ".join(i.text for i in note.transcript or [])
    assert rejoined.replace(" ", "") == "".join(
        line.split(": ", 1)[-1] for line in full["transcript"].splitlines()
    ).replace(" ", "")
    views = [c[1] for c in fake.calls if c[0] == "get_meeting"]
    assert any(v.get("view_transcript", {}).get("start_char") == 50 for v in views)
    assert all(
        v.get("view_transcript", v.get("view_content", {}))["char_limit"] <= 40000 for v in views
    )


def test_live_ranges_rejoin_without_repeated_banners(fake: FakeWispr) -> None:
    """The live server opens every range with the banner and closes a truncated one with the
    marker plus ``<<<END TRANSCRIPT>>>``: the whole transcript comes back, one banner only."""
    fake.char_limit, fake.live_banners = 50, True
    note = WisprMCPSource(fake, clock=FakeClock()).get_note("m-1")
    texts = [i.text for i in note.transcript or []]
    assert sum("<<<" in t and "END" not in t for t in texts) == 1
    assert not any("truncated" in t for t in texts)
    rejoined = " ".join(texts).replace("<<<END TRANSCRIPT>>>", "")
    words = "".join(line.split(": ", 1)[-1] for line in meeting()["transcript"].splitlines())
    assert words.replace(" ", "") in rejoined.replace(" ", "")


def test_unfinished_and_missing_meetings_are_not_ready(fake: FakeWispr) -> None:
    fake.meetings.append(meeting("m-new", finalized=False))
    src = WisprMCPSource(fake, clock=FakeClock())
    with pytest.raises(NoteNotReady):
        src.get_note("m-new")
    with pytest.raises(NoteNotReady):
        src.get_note("nope")


def test_too_many_requests_waits_and_retries(fake: FakeWispr) -> None:
    clock = FakeClock()
    fake.too_many = 1
    assert len(list(WisprMCPSource(fake, clock=clock).list_notes())) == 3
    assert clock.now() >= 60


def test_meeting_without_transcript_has_empty_transcript(fake: FakeWispr) -> None:
    fake.meetings.append(meeting("m-quiet", transcript="", has_transcript=False))
    assert WisprMCPSource(fake, clock=FakeClock()).get_note("m-quiet").transcript == []


def test_sync_stores_wispr_notes_and_reloads_them(source: WisprMCPSource, tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = NoteStore(tmp_path / "real_wispr.db")
    report = sync(source, store)
    assert (report.listed, report.fetched) == (3, 3)
    again = sync(source, store)
    assert again.unchanged == 3 and again.fetched == 0
    note = store.get_note("m-1")
    assert note is not None
    assert note.utterances[0].channel is Channel.ME
    assert note.utterances[1].channel is Channel.THEM
    assert note.utterances[0].utterance_id == "m-1:0000"


@pytest.mark.parametrize(
    ("line", "who", "secs"),
    [
        ("Priya Shah: hello there", "Priya Shah", None),
        ("[01:05] Priya Shah: hello there", "Priya Shah", 65),
        ("01:05 Priya: hello there", "Priya", 65),
        ("Speaker 2 (1:02:03): hello there", "Speaker 2", 3723),
        ("just a plain line of text", None, None),
        ("This is a very long sentence that happens: to contain a colon", None, None),
    ],
)
def test_transcript_line_formats(line: str, who: str | None, secs: int | None) -> None:
    (turn,) = parse_transcript(line)
    assert turn.who == who
    assert (turn.offset.total_seconds() if turn.offset else None) == secs
    assert turn.text.endswith(("hello there", "plain line of text", "contain a colon"))


def test_lines_without_any_speaker_become_one_turn_each() -> None:
    turns = parse_transcript("first line\nsecond line\n")
    assert [t.text for t in turns] == ["first line", "second line"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("abc", ("abc", None)),
        ("abc\n[truncated: continue with start_char=120]", ("abc", 120)),
        ("abc (more: start_char 40000)", ("abc", 40000)),
        (  # observed shape (2026-11-09): marker, then the closing banner
            "The f (...truncated, 5359 chars remaining; continue with "
            "view_transcript.start_char=40000...) <<<END TRANSCRIPT>>>",
            ("The f", 40000),
        ),
        ({"text": "abc", "next_start_char": 7}, ("abc", 7)),
        (None, ("", None)),
    ],
)
def test_split_range(value: object, expected: tuple[str, int | None]) -> None:
    assert split_range(value) == expected
