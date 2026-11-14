"""Do agents read what was said? Answering AMI questions through a notes product's MCP.

Three conditions over the same questions, each answered by an agent that may use only the
product's read tools, scoped to the question's meetings:

* ``free``: any read tool, default tool descriptions, no hint about the transcript;
* ``notes``: the notes tool only;
* ``transcript``: told to search the transcript before answering.

This module holds what is deterministic: the jobs handed to the agents, the validation of what
they return, the blind reader packets (answers shuffled per question under neutral labels) and
the score. The agents and readers themselves run outside (agent sessions, no ``tenor.llm``).
No network here; nothing in it knows a meeting title or text beyond what the caller passes.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tenor.evals.stats import binom_two_sided_p

CONDITIONS: tuple[str, ...] = ("free", "notes", "transcript")
LABELS: tuple[str, ...] = ("X", "Y", "Z")
READ_TOOLS: frozenset[str] = frozenset(
    {"get_meetings", "search_transcripts", "get_meeting_transcript", "query_granola_meetings"}
)
TRANSCRIPT_TOOLS: frozenset[str] = frozenset({"search_transcripts", "get_meeting_transcript"})
ALLOWED: Mapping[str, frozenset[str]] = {
    "free": READ_TOOLS,
    "notes": frozenset({"get_meetings"}),
    "transcript": READ_TOOLS,
}
Verdict = Literal["correct", "partially_correct", "incorrect"]
PROMPT = "mcp_agent.v1"
READER_PROMPT = "mcp_reading_reader.v1"


class Job(BaseModel):
    """One agent run: a question, its condition and the product meeting ids it may read."""

    model_config = ConfigDict(frozen=True)

    qid: str
    condition: str
    question: str
    meeting_ids: tuple[str, ...]


class Run(BaseModel):
    """What one agent returned. ``tools`` is the order of its MCP calls, by bare tool name."""

    model_config = ConfigDict(frozen=True)

    qid: str
    condition: str
    answer: str
    abstain: bool = False
    tools: tuple[str, ...] = ()
    off_scope_results: int = Field(default=0, ge=0)

    @property
    def read_transcript(self) -> bool:
        return any(t in TRANSCRIPT_TOOLS for t in self.tools)


def jobs(
    questions: Sequence[tuple[str, str, Sequence[str]]],
    note_ids: Mapping[str, str],
    conditions: Sequence[str] = CONDITIONS,
) -> list[Job]:
    """Jobs for ``(qid, question, AMI meeting ids)`` rows, in question then condition order.
    A question naming a meeting with no recorded note raises: nothing is silently dropped."""
    out = []
    for qid, question, meetings in questions:
        ids = tuple(note_ids[m] for m in meetings)
        out += [Job(qid=qid, condition=c, question=question, meeting_ids=ids) for c in conditions]
    return out


def problems(run: Run) -> list[str]:
    """Why a run breaks its condition's rules (empty when it does not)."""
    out = []
    if run.condition not in ALLOWED:
        return [f"unknown condition {run.condition!r}"]
    extra = sorted(set(run.tools) - ALLOWED[run.condition])
    if extra:
        out.append(f"tools outside the condition: {', '.join(extra)}")
    if run.condition == "transcript" and "search_transcripts" not in run.tools:
        out.append("transcript condition without a search_transcripts call")
    if not run.answer.strip() and not run.abstain:
        out.append("empty answer without abstaining")
    return out


def packet_key(qids: Sequence[str], seed: int) -> dict[str, dict[str, str]]:
    """Per question, label -> condition, shuffled by a seeded RNG (same seed, same key)."""
    rng = random.Random(seed)
    key = {}
    for qid in sorted(qids):
        order = list(CONDITIONS)
        rng.shuffle(order)
        key[qid] = dict(zip(LABELS, order, strict=True))
    return key


def packet(
    rows: Sequence[tuple[str, str, str]],
    runs: Mapping[tuple[str, str], Run],
    key: Mapping[str, Mapping[str, str]],
) -> str:
    """The reader's question blocks for ``(qid, question, gold)`` rows: gold, then the answers
    under their neutral labels. An abstention is shown as the reader would see it."""
    blocks = []
    for qid, question, gold in rows:
        lines = [f"### {qid}", f"Question: {question}", f"Gold answer: {gold}", ""]
        for label in LABELS:
            run = runs[(qid, key[qid][label])]
            text = run.answer.strip() or "(no answer)"
            if run.abstain:
                text = f"(abstained) {text}"
            lines.append(f"Answer {label}: {text}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _ci(flags: Sequence[bool], seed: int, draws: int = 2000) -> list[float]:
    """95% percentile interval for a share, resampling questions."""
    rng = random.Random(seed)
    n = len(flags)
    shares = sorted(sum(flags[rng.randrange(n)] for _ in range(n)) / n for _ in range(draws))
    return [round(shares[int(0.025 * draws)], 3), round(shares[int(0.975 * draws) - 1], 3)]


def _paired(a: Sequence[bool], b: Sequence[bool]) -> dict[str, Any]:
    a_only = sum(x and not y for x, y in zip(a, b, strict=True))
    b_only = sum(y and not x for x, y in zip(a, b, strict=True))
    p = binom_two_sided_p(min(a_only, b_only), a_only + b_only) if a_only + b_only else 1.0
    return {"a_only": a_only, "b_only": b_only, "p": round(p, 4)}


def score(
    runs: Sequence[Run], verdicts: Mapping[tuple[str, str], str], seed: int = 42
) -> dict[str, Any]:
    """Per condition: correct and not-wrong counts with intervals, abstentions, how often the
    transcript was read and which tools were used; then the paired comparisons, and for the
    free condition the outcome split by whether the transcript was read."""
    by = {(r.qid, r.condition): r for r in runs}
    qids = sorted({r.qid for r in runs})
    missing = [
        (q, c) for q in qids for c in CONDITIONS if (q, c) not in by or (q, c) not in verdicts
    ]
    if missing:
        raise ValueError(f"{len(missing)} run or verdict missing, e.g. {missing[0]}")
    out: dict[str, Any] = {"n": len(qids), "seed": seed, "conditions": {}, "paired": {}}
    correct = {c: [verdicts[(q, c)] == "correct" for q in qids] for c in CONDITIONS}
    not_wrong = {c: [verdicts[(q, c)] != "incorrect" for q in qids] for c in CONDITIONS}
    for c in CONDITIONS:
        rs = [by[(q, c)] for q in qids]
        tools: dict[str, int] = {}
        for r in rs:
            for t in set(r.tools):
                tools[t] = tools.get(t, 0) + 1
        out["conditions"][c] = {
            "correct": {"k": sum(correct[c]), "ci95": _ci(correct[c], seed)},
            "not_wrong": {"k": sum(not_wrong[c]), "ci95": _ci(not_wrong[c], seed)},
            "abstained": sum(r.abstain for r in rs),
            "read_transcript": sum(r.read_transcript for r in rs),
            "questions_using_tool": dict(sorted(tools.items())),
            "mean_tool_calls": round(sum(len(r.tools) for r in rs) / len(rs), 2),
            "off_scope_results": sum(r.off_scope_results for r in rs),
        }
    for a, b in (("free", "notes"), ("transcript", "notes"), ("transcript", "free")):
        out["paired"][f"{a} vs {b}"] = {
            "correct": _paired(correct[a], correct[b]),
            "not_wrong": _paired(not_wrong[a], not_wrong[b]),
        }
    read = [by[(q, "free")].read_transcript for q in qids]
    out["free_by_reading"] = {
        name: {
            "n": sum(f == flag for f in read),
            "correct": sum(c for c, f in zip(correct["free"], read, strict=True) if f == flag),
            "not_wrong": sum(c for c, f in zip(not_wrong["free"], read, strict=True) if f == flag),
        }
        for name, flag in (("read", True), ("did_not_read", False))
    }
    out["changed_by_transcript"] = [
        {"qid": q, "notes": verdicts[(q, "notes")], "transcript": verdicts[(q, "transcript")]}
        for q in qids
        if verdicts[(q, "notes")] != verdicts[(q, "transcript")]
    ]
    return out
