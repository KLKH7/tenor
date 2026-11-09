"""Baseline question-answering systems Concentrate is measured against.

* **B0 top-k** - retrieve the k most relevant meetings (hybrid BM25 + embeddings over transcript
  chunks and summaries, RRF) and put their full notes + transcripts in context.
* **B1 summaries-only** - same retrieval, but only Granola's AI summaries and typed notes.
* **B2 full-context** - every meeting in context (the ceiling); the corpus sits in a cached
  system prompt so it is paid in full once per run, then read from cache.
* **B3 agentic** - an agent with tools mirroring Granola's current MCP (search_meetings,
  get_meetings, search_transcripts, get_meeting_transcript), per the live probe of 2026-11-02.

Every system returns the same structured ``Answer`` with citations, and per-question
accounting (tokens, cost, latency, calls) taken from the shared LLM ledger.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from tenor.agents import corpus as cv
from tenor.agents.retrieval import Embedder, HybridIndex, tokenize
from tenor.core.models import MeetingNote
from tenor.llm.client import (
    LLMClient,
    ToolBackend,
    TurnRequest,
    estimate_tokens,
    load_prompt,
    run_turn,
)

ANSWER_PROMPT = "answer.v1"
AGENT_PROMPT = "agent.v1"
DEFAULT_K = 20
AGENT_MAX_TURNS = 12


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str
    abstain: bool
    citations: list[str]


ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "abstain": {"type": "boolean"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "abstain", "citations"],
    "additionalProperties": False,
}


class QuestionLike(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def question(self) -> str: ...
    @property
    def as_of(self) -> date: ...


@dataclass
class SystemResult:
    answer: Answer
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    context_tokens_est: int = 0
    retrieved: list[str] = field(default_factory=list)  # note IDs put in context / read
    batched: bool = False  # served by a Message Batches result: latency_s is not a measurement
    extra: dict[str, Any] = field(default_factory=dict)  # system-specific (e.g. fallback fired)


@dataclass
class IngestStats:
    """Write-time cost of building a memory from the corpus (paid once, not per question)."""

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    cached: bool = False  # True when an existing on-disk memory was reused
    detail: dict[str, Any] = field(default_factory=dict)  # e.g. facts/nodes/edges created


class WriteTimeSystem(Protocol):
    """A system that builds a memory once (``ingest``) and answers from it (``run``).

    Mem0, Graphiti and Concentrate are write-time systems; B0-B3 are not. Ingestion must be
    idempotent and persisted under ``data/cache/<system>/<corpus hash>/`` so a rerun reuses it.
    """

    name: str
    prompt_ids: tuple[str, ...]

    def ingest(self, notes: list[MeetingNote]) -> IngestStats: ...

    def estimate_ingest(self, notes: list[MeetingNote]) -> tuple[int, int]:
        """(input, output) tokens ingestion would cost, without calling any model."""
        ...

    def run(self, q: QuestionLike) -> SystemResult: ...

    def estimate(self, q: QuestionLike) -> tuple[int, int]: ...


def _user_prompt(q: QuestionLike) -> str:
    return f"Today's date: {q.as_of.isoformat()}\n\nQuestion: {q.question}"


class System(Protocol):
    name: str
    prompt_ids: tuple[str, ...]

    def run(self, q: QuestionLike) -> SystemResult: ...

    def estimate(self, q: QuestionLike) -> tuple[int, int]:
        """(input, output) tokens this question would cost, without calling the model."""
        ...


@dataclass(frozen=True)
class _Prompt:
    """What a single-turn system sends for one question."""

    system: str
    user: str
    cache_system: bool
    retrieved: list[str]
    # Prompt-cache breakpoints inside ``user`` (character offsets): the end of a context block
    # other questions share. Not part of the disk-cache key.
    cache_breaks: tuple[int, ...] = ()


class _Base:
    """Single-turn systems: one cached ``answer.v1`` call per question (batchable)."""

    name = "base"
    prompt_ids: tuple[str, ...] = (ANSWER_PROMPT,)
    OUTPUT_TOKENS_EST = 300  # measured: ~250 output tokens per answer in the pilot

    def __init__(self, client: LLMClient, backend: ToolBackend | None = None) -> None:
        self.client = client
        self.backend = backend
        self.instructions = load_prompt(ANSWER_PROMPT)

    def _prompt(self, q: QuestionLike) -> _Prompt:
        raise NotImplementedError

    def _turn_request(self, p: _Prompt) -> TurnRequest:
        return TurnRequest(
            stage=f"eval.{self.name}",
            prompt_id=ANSWER_PROMPT,
            system=p.system,
            messages=({"role": "user", "content": p.user},),
            schema=ANSWER_SCHEMA,
            max_tokens=8000,
            cache_system=p.cache_system,
            cache_breaks=p.cache_breaks,
        )

    def turn_request(self, q: QuestionLike) -> TurnRequest:
        """The single Messages request ``run(q)`` sends, without sending it (batch prefetch)."""
        return self._turn_request(self._prompt(q))

    def run(self, q: QuestionLike) -> SystemResult:
        p = self._prompt(q)
        before = len(self.client.ledger.records)
        start = time.perf_counter()
        completion = run_turn(self.client, self._turn_request(p), self.backend)
        return _result(
            self.client,
            before,
            start,
            Answer.model_validate_json(completion.text),
            p.retrieved,
            context=estimate_tokens(p.system) + estimate_tokens(p.user),
        )


def _result(
    client: LLMClient,
    before: int,
    start: float,
    answer: Answer,
    retrieved: list[str],
    *,
    context: int,
    tool_calls: int = 0,
) -> SystemResult:
    records = client.ledger.records[before:]
    return SystemResult(
        answer=answer,
        input_tokens=sum(r.input_tokens for r in records),
        output_tokens=sum(r.output_tokens for r in records),
        cost_usd=sum(r.cost_usd for r in records),
        # Cached calls report the original call's latency, so reruns don't fake speedups.
        latency_s=sum(r.latency_s for r in records)
        if all(r.cached for r in records)
        else time.perf_counter() - start,
        llm_calls=len(records),
        tool_calls=tool_calls,
        context_tokens_est=context,
        retrieved=retrieved,
        batched=any(r.batch for r in records),
    )


class MeetingRetriever:
    """Ranks meetings for a question via hybrid search over transcript chunks + summaries."""

    def __init__(self, notes: list[MeetingNote], embedder: Embedder | None) -> None:
        self.notes = {m.note_id: m for m in notes}
        self.owners: list[str] = []
        docs: list[str] = []
        for m in notes:
            docs.append(f"{m.title}\n{cv.summary_view(m)}\n{m.private_notes_markdown or ''}")
            self.owners.append(m.note_id)
            for c in cv.chunks(m):
                docs.append(f"{m.title}\n{c.text}")
                self.owners.append(m.note_id)
        self.index = HybridIndex(docs, embedder)

    def top_meetings(self, query: str, k: int) -> list[MeetingNote]:
        ranked: list[str] = []
        for doc in self.index.search(query, k=len(self.index.docs)):
            nid = self.owners[doc]
            if nid not in ranked:
                ranked.append(nid)
            if len(ranked) == k:
                break
        return [self.notes[n] for n in ranked]


class TopKSystem(_Base):
    """B0: the k most relevant meetings, full notes + transcripts, stuffed into context."""

    name = "b0_topk"

    def __init__(
        self,
        client: LLMClient,
        retriever: MeetingRetriever,
        k: int = DEFAULT_K,
        backend: ToolBackend | None = None,
    ) -> None:
        super().__init__(client, backend)
        self.retriever, self.k = retriever, k

    def _context(self, q: QuestionLike) -> tuple[str, list[str]]:
        meetings = cv.by_date(self.retriever.top_meetings(q.question, self.k))
        body = "\n\n".join(cv.full_view(m) for m in meetings)
        return f"<meetings>\n{body}\n</meetings>\n\n{_user_prompt(q)}", [
            m.note_id for m in meetings
        ]

    def _prompt(self, q: QuestionLike) -> _Prompt:
        user, retrieved = self._context(q)
        return _Prompt(self.instructions, user, cache_system=False, retrieved=retrieved)

    def prompt_texts(self, q: QuestionLike) -> tuple[str, str]:
        """(system, user) exactly as sent, for exact token counting."""
        return self.instructions, self._context(q)[0]

    def estimate(self, q: QuestionLike) -> tuple[int, int]:
        user, _ = self._context(q)
        return estimate_tokens(self.instructions) + estimate_tokens(user), self.OUTPUT_TOKENS_EST


class SummariesOnlySystem(TopKSystem):
    """B1: same retrieval as B0, but only AI summaries and typed notes (no transcripts)."""

    name = "b1_summaries"

    def _context(self, q: QuestionLike) -> tuple[str, list[str]]:
        meetings = cv.by_date(self.retriever.top_meetings(q.question, self.k))
        body = "\n\n".join(cv.summary_only_view(m) for m in meetings)
        return f"<meetings>\n{body}\n</meetings>\n\n{_user_prompt(q)}", [
            m.note_id for m in meetings
        ]


class FullContextSystem(_Base):
    """B2: every meeting in a cached system prompt: the accuracy ceiling for retrieval."""

    name = "b2_full"

    def __init__(
        self, client: LLMClient, notes: list[MeetingNote], backend: ToolBackend | None = None
    ) -> None:
        super().__init__(client, backend)
        body = "\n\n".join(cv.full_view(m) for m in cv.by_date(notes))
        self.system = f"{self.instructions}\n\n<meetings>\n{body}\n</meetings>"
        self.ids = [m.note_id for m in notes]

    def _prompt(self, q: QuestionLike) -> _Prompt:
        return _Prompt(self.system, _user_prompt(q), cache_system=True, retrieved=self.ids)

    def prompt_texts(self, q: QuestionLike) -> tuple[str, str]:
        return self.system, _user_prompt(q)

    def estimate(self, q: QuestionLike) -> tuple[int, int]:
        return estimate_tokens(self.system) + 60, self.OUTPUT_TOKENS_EST


class OracleSystem(_Base):
    """B2o: only the meetings that contain the gold evidence: a ceiling for *reasoning* given
    perfect retrieval. Used when the corpus is too large for B2 (full context) to fit.

    Unanswerable questions have no gold meetings; the oracle then gets the retriever's top
    ``fallback_k`` meetings, so it can't infer "unanswerable" from an empty context.
    """

    name = "b2o_oracle"

    def __init__(
        self,
        client: LLMClient,
        notes: list[MeetingNote],
        retriever: MeetingRetriever,
        fallback_k: int = 5,
        backend: ToolBackend | None = None,
    ) -> None:
        super().__init__(client, backend)
        self.notes = {m.note_id: m for m in notes}
        self.retriever, self.fallback_k = retriever, fallback_k

    def _meetings(self, q: QuestionLike) -> list[MeetingNote]:
        gold: list[str] = list(getattr(q, "gold_utterance_ids", []) or [])
        ids = list(dict.fromkeys(u.split(":")[0] for u in gold))
        found = [self.notes[i] for i in ids if i in self.notes]
        return cv.by_date(found or self.retriever.top_meetings(q.question, self.fallback_k))

    def prompt_texts(self, q: QuestionLike) -> tuple[str, str]:
        body = "\n\n".join(cv.full_view(m) for m in self._meetings(q))
        return self.instructions, f"<meetings>\n{body}\n</meetings>\n\n{_user_prompt(q)}"

    def _prompt(self, q: QuestionLike) -> _Prompt:
        system, user = self.prompt_texts(q)
        ids = [m.note_id for m in self._meetings(q)]
        return _Prompt(system, user, cache_system=False, retrieved=ids)

    def estimate(self, q: QuestionLike) -> tuple[int, int]:
        system, user = self.prompt_texts(q)
        return estimate_tokens(system) + estimate_tokens(user), self.OUTPUT_TOKENS_EST


# -- B3: agentic retrieval mirroring Granola's MCP ----------------------------------------------


def _tool(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


_STR_LIST = {"type": "array", "items": {"type": "string"}}
_NULLABLE_STR = {"type": ["string", "null"]}
AGENT_TOOLS: tuple[dict[str, Any], ...] = (
    _tool(
        "search_meetings",
        "Find meetings by keywords in titles, AI summaries and typed notes (NOT transcripts), "
        "optionally filtered by participant (name/email substring) and ISO date range. Returns "
        "metadata only.",
        {
            "keywords": _STR_LIST,
            "participant": _NULLABLE_STR,
            "from_date": _NULLABLE_STR,
            "to_date": _NULLABLE_STR,
        },
        ["keywords", "participant", "from_date", "to_date"],
    ),
    _tool(
        "get_meetings",
        "Read up to 10 meetings' AI summaries, typed notes and participants. No transcripts.",
        {"meeting_ids": _STR_LIST},
        ["meeting_ids"],
    ),
    _tool(
        "search_transcripts",
        "Find transcript passages by keyword (alternatives OR'ed). Returns ~240-character "
        "passages WITHOUT speakers, with chunk_ids to read via get_meeting_transcript. Pass "
        "meeting_ids to restrict, or an empty list to search everything.",
        {"keywords": _STR_LIST, "meeting_ids": _STR_LIST},
        ["keywords", "meeting_ids"],
    ),
    _tool(
        "get_meeting_transcript",
        "Read transcript chunks (speaker labels + line IDs) of one meeting. Use chunk_ids from "
        "search_transcripts; [0] reads from the start.",
        {
            "meeting_id": {"type": "string"},
            "chunk_ids": {"type": "array", "items": {"type": "integer"}},
        },
        ["meeting_id", "chunk_ids"],
    ),
)


class AgentTools:
    """Executes the MCP-style tools over the note store (keyword search is BM25, like
    Granola's literal keyword matching; no embeddings)."""

    def __init__(self, notes: list[MeetingNote]) -> None:
        self.notes = {m.note_id: m for m in notes}
        self.order = [m.note_id for m in cv.by_date(notes)]
        self._meta = HybridIndex(
            [
                f"{m.title}\n{cv.summary_view(m)}\n{m.private_notes_markdown or ''}"
                for m in (self.notes[n] for n in self.order)
            ]
        )
        self._chunks = [c for n in self.order for c in cv.chunks(self.notes[n])]
        self._chunk_index = HybridIndex([c.text for c in self._chunks])

    def _when(self, m: MeetingNote) -> datetime:
        return m.scheduled_start or m.created_at

    def search_meetings(
        self,
        keywords: list[str],
        participant: str | None,
        from_date: str | None,
        to_date: str | None,
    ) -> str:
        hits = (
            self._meta.bm25_ranking(" ".join(keywords))
            if keywords
            else list(range(len(self.order)))
        )
        out = []
        for i in hits:
            m = self.notes[self.order[i]]
            day = self._when(m).date().isoformat()
            if from_date and day < from_date[:10]:
                continue
            if to_date and day > to_date[:10]:
                continue
            people = [f"{p.name or p.email} <{p.email}>" for p in m.attendees]
            if participant and not any(participant.lower() in p.lower() for p in people):
                continue
            out.append({"id": m.note_id, "title": m.title, "date": day, "participants": people})
        return json.dumps({"meetings": out[:15]})

    def get_meetings(self, meeting_ids: list[str]) -> str:
        found = [cv.summary_only_view(self.notes[i]) for i in meeting_ids[:10] if i in self.notes]
        missing = [i for i in meeting_ids[:10] if i not in self.notes]
        return "\n\n".join(found) + (f"\n\nNot found: {missing}" if missing else "")

    def search_transcripts(self, keywords: list[str], meeting_ids: list[str]) -> str:
        terms = [t for k in keywords for t in tokenize(k)]
        allowed = set(meeting_ids) if meeting_ids else None
        out = []
        for i in self._chunk_index.bm25_ranking(" ".join(keywords)):
            c = self._chunks[i]
            if allowed is not None and c.note_id not in allowed:
                continue
            text = c.text
            low = text.lower()
            pos = min((p for t in terms if (p := low.find(t)) >= 0), default=0)
            start = max(0, pos - 80)
            out.append(
                {
                    "meeting_id": c.note_id,
                    "title": self.notes[c.note_id].title,
                    "chunk_id": c.chunk_id,
                    "passage": text[start : start + 240],
                }
            )
            if len(out) == 10:
                break
        return json.dumps({"passages": out})

    def get_meeting_transcript(self, meeting_id: str, chunk_ids: list[int]) -> str:
        m = self.notes.get(meeting_id)
        if m is None:
            return json.dumps({"error": "Meeting not found"})
        chunks = cv.chunks(m)
        picked = [c for c in chunks if c.chunk_id in set(chunk_ids[:10])]
        return json.dumps(
            {
                "meeting_id": meeting_id,
                "title": m.title,
                "total_chunks": len(chunks),
                "chunks": [
                    {"chunk_id": c.chunk_id, "lines": cv.transcript_lines(c.utterances)}
                    for c in picked
                ],
            }
        )

    def dispatch(self, name: str, args: dict[str, Any]) -> str:
        handlers: dict[str, Callable[..., str]] = {
            "search_meetings": self.search_meetings,
            "get_meetings": self.get_meetings,
            "search_transcripts": self.search_transcripts,
            "get_meeting_transcript": self.get_meeting_transcript,
        }
        if name not in handlers:
            return json.dumps({"error": f"unknown tool {name}"})
        return handlers[name](**args)


class AgenticSystem:
    """B3: a tool-using agent over MCP-style tools (search → summaries → transcript chunks)."""

    name = "b3_agentic"
    prompt_ids: tuple[str, ...] = (AGENT_PROMPT,)
    # Pilot (40 meetings): 22.9k input / 0.85k output tokens per question. Larger corpora return
    # bigger search results, so plan for more until XL is measured.
    INPUT_TOKENS_EST, OUTPUT_TOKENS_EST = 32_000, 1_000

    def __init__(
        self,
        client: LLMClient,
        notes: list[MeetingNote],
        backend: ToolBackend | None = None,
        max_turns: int = AGENT_MAX_TURNS,
    ) -> None:
        self.client, self.backend, self.max_turns = client, backend, max_turns
        self.tools = AgentTools(notes)
        self.system = load_prompt(AGENT_PROMPT)

    def run(self, q: QuestionLike) -> SystemResult:
        before = len(self.client.ledger.records)
        start = time.perf_counter()
        messages: list[dict[str, Any]] = [{"role": "user", "content": _user_prompt(q)}]
        tool_calls, read = 0, list[str]()
        for turn in range(self.max_turns + 1):
            if turn == self.max_turns:
                messages.append(
                    {
                        "role": "user",
                        "content": "Tool budget exhausted: give your "
                        "final answer now, without calling tools.",
                    }
                )
            completion = run_turn(
                self.client,
                TurnRequest(
                    stage=f"eval.{self.name}",
                    prompt_id=AGENT_PROMPT,
                    system=self.system,
                    messages=tuple(messages),
                    tools=AGENT_TOOLS,
                    schema=ANSWER_SCHEMA,
                    max_tokens=16000,
                ),
                self.backend,
            )
            uses = completion.tool_uses
            if uses and turn == self.max_turns:  # ignored the budget: count as a failed answer
                failed = Answer(
                    answer="(no answer: tool budget exhausted)", abstain=True, citations=[]
                )
                return _result(
                    self.client, before, start, failed, read, context=0, tool_calls=tool_calls
                )
            if not uses:
                answer = Answer.model_validate_json(completion.text)
                return _result(
                    self.client, before, start, answer, read, context=0, tool_calls=tool_calls
                )
            messages.append({"role": "assistant", "content": completion.content})
            results = []
            for use in uses:
                tool_calls += 1
                args = use.get("input", {})
                for key in ("meeting_id", "meeting_ids"):
                    ids = args.get(key)
                    read += [ids] if isinstance(ids, str) else list(ids or [])
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": use["id"],
                        "content": self.tools.dispatch(use["name"], args),
                    }
                )
            messages.append({"role": "user", "content": results})
        raise RuntimeError("unreachable")  # pragma: no cover

    def estimate(self, q: QuestionLike) -> tuple[int, int]:
        return self.INPUT_TOKENS_EST, self.OUTPUT_TOKENS_EST
