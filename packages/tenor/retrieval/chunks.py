"""Step 1: cut transcripts into overlapping chunks and rank them with BM25 (by hand, no library).

Granola's post: each workspace has a BM25 namespace; when a meeting ends, the transcript is cut
into chunks, each tagged with the attendees, who spoke in that chunk and a version string.
This module is that, locally.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from tenor.memory.series_record import Line

LINE_ID = re.compile(r"l\d{4}")
STOPWORDS: frozenset[str] = frozenset(
    {
        *("a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "for", "from", "has"),
        *("have", "i", "if", "in", "is", "it", "its", "of", "on", "or", "so", "that", "the"),
        *("this", "to", "uh", "um", "was", "we", "were", "what", "will", "with", "you", "yeah"),
        *("okay", "ok"),
    }
)


class Chunk(BaseModel):
    """A run of consecutive transcript lines from one meeting."""

    model_config = ConfigDict(frozen=True)

    meeting: str  # e.g. "ES2005a"
    chunk_id: int  # 0, 1, 2, ... in transcript order
    first_line: str  # "L0000"
    last_line: str  # "L0014"
    speakers: tuple[str, ...]  # roles who spoke in this chunk, sorted, no repeats
    text: str  # one line per transcript line: "L0003 Project Manager: words"
    version: str  # the meeting's transcript version (see transcript_version)


def transcript_version(lines: Mapping[str, Line]) -> str:
    """A short, stable hash of a transcript: the first 12 hex characters of a sha256.

    Lines are hashed in line-id order, so the same lines always give the same string, and any
    edit (a word, a speaker) gives a different one: that is how an index knows to re-chunk.
    """
    h = hashlib.sha256()
    for line_id in sorted(lines):
        line = lines[line_id]
        h.update(f"{line_id}\t{line.speaker}\t{line.text}\n".encode())
    return h.hexdigest()[:12]


def chunk_meeting(
    meeting: str, lines: Mapping[str, Line], size: int = 15, overlap: int = 3
) -> list[Chunk]:
    """Cut a transcript into chunks of `size` lines; each starts `size - overlap` after the last.

    The overlap means a fact said across a boundary still lands whole in one chunk. The last
    chunk may be shorter. Raises ValueError unless size >= 1 and 0 <= overlap < size.
    """
    if size < 1 or not 0 <= overlap < size:
        raise ValueError(f"need size >= 1 and 0 <= overlap < size, got {size=}, {overlap=}")
    ids = sorted(lines)
    version = transcript_version(lines)
    chunks: list[Chunk] = []
    for start in range(0, len(ids), size - overlap):
        window = ids[start : start + size]
        chunks.append(
            Chunk(
                meeting=meeting,
                chunk_id=len(chunks),
                first_line=window[0],
                last_line=window[-1],
                speakers=tuple(sorted({lines[i].speaker for i in window})),
                text="\n".join(f"{i} {lines[i].speaker}: {lines[i].text}" for i in window),
                version=version,
            )
        )
        if start + size >= len(ids):  # reached the last line; another chunk would only repeat
            break
    return chunks


def tokenize(text: str) -> list[str]:
    """Lowercase words and numbers, without stopwords or line ids; order and repeats kept."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in STOPWORDS and not LINE_ID.fullmatch(w)]


class BM25Index:
    """BM25 over chunks. k1: how fast repeats of a word stop helping; b: length normalisation."""

    def __init__(self, chunks: Sequence[Chunk], k1: float = 1.2, b: float = 0.75) -> None:
        """Count once: each chunk's terms and length, the average length, each term's chunks."""
        self.chunks = list(chunks)
        self.k1 = k1
        self.b = b
        self.term_counts = [Counter(tokenize(c.text)) for c in self.chunks]
        self.lengths = [sum(tc.values()) for tc in self.term_counts]
        self.avg_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        self.doc_freq: Counter[str] = Counter()
        for tc in self.term_counts:
            self.doc_freq.update(tc.keys())

    def idf(self, term: str) -> float:
        """ln(1 + (N - n_t + 0.5) / (n_t + 0.5)): rare terms count more than common ones."""
        n, n_t = len(self.chunks), self.doc_freq.get(term, 0)
        return math.log(1 + (n - n_t + 0.5) / (n_t + 0.5))

    def score(self, query: str, chunk_index: int) -> float:
        """BM25 score of one chunk: summed over the query's distinct terms."""
        counts, length = self.term_counts[chunk_index], self.lengths[chunk_index]
        norm = self.k1 * (1 - self.b + self.b * length / self.avg_length) if self.avg_length else 0
        total = 0.0
        for term in set(tokenize(query)):
            tf = counts.get(term, 0)
            if tf:
                total += self.idf(term) * tf * (self.k1 + 1) / (tf + norm)
        return total

    def search(
        self, query: str, k: int = 5, meetings: Iterable[str] | None = None
    ) -> list[tuple[Chunk, float]]:
        """Top-k chunks with a positive score, best first; ties by (meeting, chunk_id).

        `meetings` limits the search to those meetings (Granola filters by workspace the same way).
        """
        allowed = set(meetings) if meetings is not None else None
        hits = [
            (chunk, self.score(query, i))
            for i, chunk in enumerate(self.chunks)
            if allowed is None or chunk.meeting in allowed
        ]
        hits = [h for h in hits if h[1] > 0]
        hits.sort(key=lambda h: (-h[1], h[0].meeting, h[0].chunk_id))
        return hits[:k]


__all__ = ["STOPWORDS", "BM25Index", "Chunk", "chunk_meeting", "tokenize", "transcript_version"]
