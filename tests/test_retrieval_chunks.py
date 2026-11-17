"""Tests for step 1 (tenor.retrieval.chunks): tiny hand-made transcripts, no network."""

import math

import pytest

from tenor.memory.series_record import Line
from tenor.retrieval.chunks import BM25Index, Chunk, chunk_meeting, tokenize, transcript_version


def lines(*rows: tuple[str, str]) -> dict[str, Line]:
    return {
        f"L{i:04d}": Line(line_id=f"L{i:04d}", speaker=s, text=t) for i, (s, t) in enumerate(rows)
    }


T = lines(*[("PM" if i % 2 else "ID", f"line number {i} about the remote") for i in range(20)])


# ---- transcript_version --------------------------------------------------------------


def test_version_is_stable_and_short():
    assert transcript_version(T) == transcript_version(dict(reversed(list(T.items()))))
    assert len(transcript_version(T)) == 12


def test_version_changes_when_a_word_changes():
    changed = dict(T)
    changed["L0003"] = Line(line_id="L0003", speaker="PM", text="something else")
    assert transcript_version(changed) != transcript_version(T)


# ---- chunk_meeting -------------------------------------------------------------------


def test_chunks_cover_every_line_with_overlap():
    chunks = chunk_meeting("M1", T, size=8, overlap=2)
    assert [(c.first_line, c.last_line) for c in chunks] == [
        ("L0000", "L0007"),
        ("L0006", "L0013"),
        ("L0012", "L0019"),
    ]
    assert [c.chunk_id for c in chunks] == [0, 1, 2]


def test_no_chunk_that_only_repeats_the_overlap():
    chunks = chunk_meeting("M1", T, size=10, overlap=0)
    assert [(c.first_line, c.last_line) for c in chunks] == [("L0000", "L0009"), ("L0010", "L0019")]


def test_chunk_fields():
    c = chunk_meeting("M1", T, size=8, overlap=2)[0]
    assert c.meeting == "M1"
    assert c.speakers == ("ID", "PM")
    assert c.version == transcript_version(T)
    assert c.text.splitlines()[1] == "L0001 PM: line number 1 about the remote"


def test_bad_sizes_raise():
    with pytest.raises(ValueError):
        chunk_meeting("M1", T, size=5, overlap=5)
    with pytest.raises(ValueError):
        chunk_meeting("M1", T, size=0, overlap=0)


# ---- tokenize ------------------------------------------------------------------------


def test_tokenize():
    assert tokenize("L0012 PM: Okay, the Battery is 12.5 euros, the battery!") == [
        "pm",
        "battery",
        "12",
        "5",
        "euros",
        "battery",
    ]


# ---- BM25 ----------------------------------------------------------------------------


def chunk(meeting: str, i: int, text: str) -> Chunk:
    return Chunk(
        meeting=meeting,
        chunk_id=i,
        first_line="L0000",
        last_line="L0000",
        speakers=(),
        text=text,
        version="v",
    )


DOCS = [
    chunk("A", 0, "battery cost battery cost"),
    chunk("A", 1, "the remote design and colour"),
    chunk("B", 0, "battery"),
    chunk("B", 1, "kinetic battery charger with a long rambling sentence about many other things"),
]


def test_idf_matches_formula():
    idx = BM25Index(DOCS)
    n, n_t = 4, 3  # "battery" is in three of four chunks
    assert idx.idf("battery") == pytest.approx(math.log(1 + (n - n_t + 0.5) / (n_t + 0.5)))
    assert idx.idf("colour") > idx.idf("battery")  # rarer term counts more
    assert idx.idf("missing") == pytest.approx(math.log(1 + (n + 0.5) / 0.5))


def test_search_ranks_and_filters():
    idx = BM25Index(DOCS)
    top = [(c.meeting, c.chunk_id) for c, _ in idx.search("battery cost", k=3)]
    assert top[0] == ("A", 0)  # both terms, repeated
    assert ("A", 1) not in top  # no query term: score 0, never returned
    only_b = [(c.meeting, c.chunk_id) for c, _ in idx.search("battery", meetings=["B"])]
    assert only_b[0] == ("B", 0)  # same word once: the shorter chunk wins


def test_length_normalisation():
    idx = BM25Index(DOCS)
    assert idx.score("battery", 2) > idx.score("battery", 3)  # same tf, the long chunk is penalised


def test_search_is_deterministic_on_ties():
    same = [chunk("Z", 1, "battery"), chunk("Y", 0, "battery"), chunk("Y", 2, "battery")]
    got = [(c.meeting, c.chunk_id) for c, _ in BM25Index(same).search("battery", k=3)]
    assert got == [("Y", 0), ("Y", 2), ("Z", 1)]
