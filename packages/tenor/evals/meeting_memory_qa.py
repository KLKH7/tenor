"""Answering questions about a meeting series from its record or from raw transcripts.

Two conditions over the same questions, each answered by one agent per question:

* ``record``: the rendered series record, plus a logged lookup tool that opens transcript lines
  by pointer or searches a meeting's transcript (``scripts/meeting_memory.py lines|search``);
* ``raw``: the full human transcripts of the meetings the question names (oracle selection).

Tokens read are estimated as characters / :data:`CHARS_PER_TOKEN`: the job file the agent was
given plus, for ``record``, everything the lookup tool printed. This module holds the
deterministic parts: answer and lookup validation, the blind reader packets (two answers per
question under shuffled labels) and the score. No network, no model.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tenor.evals.stats import binom_two_sided_p

CONDITIONS: tuple[str, ...] = ("record", "raw")
LABELS: tuple[str, ...] = ("X", "Y")
CHARS_PER_TOKEN = 4
PROMPT = "memory_answer.v1"
READER_PROMPT = "mcp_reading_reader.v1"
Verdict = Literal["correct", "partially_correct", "incorrect"]


def tokens(chars: int) -> int:
    """Approximate tokens for ``chars`` characters (no tokenizer is called)."""
    return math.ceil(chars / CHARS_PER_TOKEN)


class Answer(BaseModel):
    """What one answering agent wrote."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    qid: str
    condition: Literal["record", "raw"]
    answer: str
    abstain: bool = False
    pointers_used: tuple[str, ...] = ()  # "IS1008a L0123" the answer rests on, if any


class Lookup(BaseModel):
    """One logged call of the record condition's lookup tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: Literal["lines", "search"]
    meeting: str
    arg: str
    lines_returned: int = Field(ge=0)
    chars_returned: int = Field(ge=0)


def read_cost(job_chars: int, lookups: Sequence[Lookup]) -> dict[str, int]:
    """Characters and approximate tokens one agent read: its job file plus lookup output."""
    extra = sum(lk.chars_returned for lk in lookups)
    return {
        "job_chars": job_chars,
        "lookup_chars": extra,
        "lookups": len(lookups),
        "tokens": tokens(job_chars + extra),
    }


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
    answers: Mapping[tuple[str, str], Answer],
    key: Mapping[str, Mapping[str, str]],
) -> str:
    """Question blocks for ``(qid, question, gold)`` rows: gold, then the two answers under
    their neutral labels. Nothing says how an answer was produced."""
    blocks = []
    for qid, question, gold in rows:
        lines = [f"### {qid}", f"Question: {question}", f"Gold answer: {gold}", ""]
        for label in LABELS:
            a = answers[(qid, key[qid][label])]
            text = a.answer.strip() or "(no answer)"
            lines.append(f"Answer {label}: {'(abstained) ' if a.abstain else ''}{text}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def settle(
    a: Mapping[tuple[str, str], str],
    b: Mapping[tuple[str, str], str],
    c: Mapping[tuple[str, str], str],
) -> tuple[dict[tuple[str, str], str], int]:
    """Final verdict per (qid, label): readers a and b when they agree, else reader c. Returns
    the verdicts and how many rows a and b agreed on. A row c did not settle raises."""
    out = {}
    agree = 0
    for k, va in a.items():
        vb = b[k]
        if va == vb:
            agree += 1
            out[k] = va
        elif k in c:
            out[k] = c[k]
        else:
            raise ValueError(f"readers split on {k} and reader c has no verdict")
    return out, agree


def _paired(a: Sequence[bool], b: Sequence[bool]) -> dict[str, Any]:
    a_only = sum(x and not y for x, y in zip(a, b, strict=True))
    b_only = sum(y and not x for x, y in zip(a, b, strict=True))
    p = binom_two_sided_p(min(a_only, b_only), a_only + b_only) if a_only + b_only else 1.0
    return {"record_only": a_only, "raw_only": b_only, "p": round(p, 4)}


def score(
    qids: Sequence[str],
    verdicts: Mapping[tuple[str, str], str],
    costs: Mapping[tuple[str, str], Mapping[str, int]],
    answers: Mapping[tuple[str, str], Answer],
) -> dict[str, Any]:
    """Per condition: correct and not-wrong counts, abstentions, tokens read (mean, median,
    total) and how many record answers used the lookup tool; then the paired comparison and
    one row per question."""
    qs = sorted(qids)
    missing = [(q, c) for q in qs for c in CONDITIONS if (q, c) not in verdicts]
    if missing:
        raise ValueError(f"{len(missing)} verdicts missing, e.g. {missing[0]}")
    out: dict[str, Any] = {"n": len(qs), "conditions": {}}
    correct = {c: [verdicts[(q, c)] == "correct" for q in qs] for c in CONDITIONS}
    not_wrong = {c: [verdicts[(q, c)] != "incorrect" for q in qs] for c in CONDITIONS}
    for c in CONDITIONS:
        toks = [costs[(q, c)]["tokens"] for q in qs]
        out["conditions"][c] = {
            "correct": sum(correct[c]),
            "not_wrong": sum(not_wrong[c]),
            "abstained": sum(answers[(q, c)].abstain for q in qs),
            "tokens_mean": round(statistics.mean(toks)),
            "tokens_median": round(statistics.median(toks)),
            "tokens_total": sum(toks),
            "used_lookup": sum(costs[(q, c)]["lookups"] > 0 for q in qs),
        }
    out["paired"] = {
        "correct": _paired(correct["record"], correct["raw"]),
        "not_wrong": _paired(not_wrong["record"], not_wrong["raw"]),
    }
    out["questions"] = [
        {
            "qid": q,
            **{f"{c}_verdict": verdicts[(q, c)] for c in CONDITIONS},
            **{f"{c}_tokens": costs[(q, c)]["tokens"] for c in CONDITIONS},
            "record_lookups": costs[(q, "record")]["lookups"],
        }
        for q in qs
    ]
    return out
