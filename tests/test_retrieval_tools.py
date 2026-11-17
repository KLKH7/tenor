"""Tests for step 2 (tenor.retrieval.tools): tools return the right text and log exactly it."""

from tenor.memory.series_record import Line
from tenor.retrieval.tools import Meeting, ToolBox


def meeting(mid: str, notes: str, texts: list[str]) -> Meeting:
    lines = {
        f"L{i:04d}": Line(line_id=f"L{i:04d}", speaker="PM" if i % 2 else "ID", text=t)
        for i, t in enumerate(texts)
    }
    return Meeting(meeting_id=mid, title=f"Meeting {mid}", notes=notes, lines=lines)


BOX_MEETINGS = [
    meeting(
        "M1",
        "Decided on a rubber case.",
        ["hello", "the battery cost is twelve euros"] + ["x"] * 20,
    ),
    meeting("M2", "Talked about colours.", ["we like yellow", "and blue"] + ["y"] * 20),
]


def box() -> ToolBox:
    return ToolBox(BOX_MEETINGS, chunk_size=5, overlap=1, k=2)


def test_search_meetings_lists_or_matches():
    b = box()
    assert b.search_meetings() == "M1: Meeting M1\nM2: Meeting M2"
    assert b.search_meetings("rubber case") == "M1: Meeting M1"
    assert b.search_meetings("kinetic") == "No meetings matched."


def test_search_transcripts_returns_only_matches_and_logs_them():
    b = box()
    out = b.search_transcripts("battery cost")
    assert out.startswith("[M1 chunk 0 | L0000-L0004 | ID, PM]")
    assert "battery cost is twelve euros" in out
    assert "yellow" not in out
    assert b.log[-1].tool == "search_transcripts" and b.log[-1].output == out


def test_search_transcripts_filter_by_meeting():
    assert box().search_transcripts("battery", meeting_ids=["M2"]) == "No passages matched."


def test_get_transcript_and_unknown_chunk():
    b = box()
    out = b.get_transcript("M2", [0, 99])
    assert "we like yellow" in out
    assert "[M2 chunk 99: no such chunk]" in out


def test_get_meetings_and_seen_text():
    b = box()
    b.get_meetings(["M1", "M9"])
    b.search_transcripts("yellow")
    seen = b.seen_text()
    assert (
        "Decided on a rubber case." in seen and "M9: no such meeting" in seen and "yellow" in seen
    )
    assert [c.tool for c in b.log] == ["get_meetings", "search_transcripts"]


def test_series_record_tool_tags_pointers_with_chunks():
    ms = [meeting("AB1000a", "notes", ["hello", "the battery cost"] + ["x"] * 10)]
    rec = 'E001 decision open: rubber case\n  - AB1000a L0001 "the battery cost"; AB1000a L0009 "x"'
    b = ToolBox(ms, chunk_size=5, overlap=1, k=2, records={"AB1000": rec})
    out = b.get_series_record("AB1000")
    assert 'AB1000a L0001 (chunk 0) "the battery cost"' in out
    assert 'AB1000a L0009 (chunk 2) "x"' in out
    assert b.get_series_record("X").startswith("No record for series X.")
    assert b.log[0].tool == "get_series_record"
