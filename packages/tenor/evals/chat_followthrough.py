"""The notes product's own chat against our "since last time" writer, on the same pairs.

The product's chat (Granola Chat through the read-only MCP tool ``query_granola_meetings``,
restricted to the two notes of a pair) is asked the frozen questions of
:data:`QUESTIONS_PROMPT`. Its raw answers and the writer's sentences of the follow-through run
are split into numbered claims and put, under shuffled letters, into one blind reader job per
pair (:data:`READER_PROMPT`). Two readers judge every claim and name the notes items it is
about. :func:`score` turns their rows into claims judged true, false or split, and the silent
items (from the follow-through run's fates) that a true claim names. No LLM here.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

QUESTIONS_PROMPT = "granola_chat_followthrough.v1"
READER_PROMPT = "chat_followthrough_reader.v1"
READERS = ("a", "b")
VERDICTS = ("yes", "partly", "no")
WRITER = "writer"

_LINK = re.compile(r"\s*\[\[\d+\]\]\([^)]*\)")
_BULLET = re.compile(r"^\s*[-*]\s+")
_QUESTION = re.compile(r"^--- (q\d+)\n(.+?)\n", re.M)


@dataclass(frozen=True)
class Answer:
    """One output to be judged: a chat answer (``q1__r2``) or the writer's sentences."""

    source: str
    claims: tuple[str, ...]


def questions(prompt_text: str) -> dict[str, str]:
    """Question id -> exact question, from the questions prompt file."""
    return {m[1]: m[2].strip() for m in _QUESTION.finditer(prompt_text)}


def claims(answer_text: str) -> tuple[str, ...]:
    """An answer split into claims: one per non-empty line (a bullet or a paragraph), with
    citation links and markdown emphasis removed. A lead-in line ending in a colon ("the later
    meeting did not follow up on:") claims nothing by itself and is dropped."""
    out = []
    for raw in answer_text.splitlines():
        text = _BULLET.sub("", _LINK.sub("", raw)).replace("**", "").replace("*", "").strip()
        if text and not text.endswith(":"):
            out.append(text)
    return tuple(out)


def labels(sources: Sequence[str], seed: int, pair: str) -> dict[str, str]:
    """Source -> hidden letter, shuffled per pair. Same arguments -> same letters."""
    order = sorted(sources)
    random.Random(f"{seed}:{pair}").shuffle(order)
    return {s: chr(ord("A") + i) for i, s in enumerate(order)}


def answers_block(answers: Sequence[Answer], key: Mapping[str, str]) -> str:
    """The answers as the readers see them: by letter, claims numbered from 1."""
    parts = []
    for a in sorted(answers, key=lambda a: key[a.source]):
        body = "\n".join(f"{i}. {c}" for i, c in enumerate(a.claims, 1))
        parts.append(f"### Answer {key[a.source]}\n\n{body}")
    return "\n\n".join(parts)


def parse_rows(text: str) -> list[dict[str, Any]]:
    """A reader's JSONL rows; lines that are not a claim object are skipped."""
    out = []
    for raw in text.splitlines():
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and isinstance(d.get("answer"), str) and "claim" in d:
            out.append(d)
    return out


def score(
    answers: Sequence[Answer],
    key: Mapping[str, str],
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
    silent: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Per source: claims, words, verdicts by each reader, claims both readers call true
    (``yes``) or false (``no``), and silent items named by a claim each reader / both readers
    judge at least ``partly`` true."""
    out: dict[str, dict[str, Any]] = {}
    for a in answers:
        letter = key[a.source]
        per: dict[str, dict[int, Mapping[str, Any]]] = {
            r: {int(x["claim"]): x for x in rows[r] if x["answer"] == letter} for r in READERS
        }
        verdicts = {
            r: [
                str(per[r].get(i, {}).get("verdict", "missing"))
                for i in range(1, len(a.claims) + 1)
            ]
            for r in READERS
        }
        named: dict[str, set[str]] = {r: set() for r in READERS}
        for r in READERS:
            for row in per[r].values():
                if row.get("verdict") in ("yes", "partly"):
                    named[r] |= {str(x) for x in row.get("x_items", [])}
        both = list(zip(verdicts["a"], verdicts["b"], strict=True))
        out[a.source] = {
            "claims": len(a.claims),
            "words": sum(len(c.split()) for c in a.claims),
            "verdicts": verdicts,
            "true_both": sum(1 for x, y in both if x == y == "yes"),
            "false_both": sum(1 for x, y in both if x == y == "no"),
            "false_any": sum(1 for x, y in both if "no" in (x, y)),
            "agree": sum(1 for x, y in both if x == y),
            "silent_named": {r: sorted(named[r] & set(silent)) for r in READERS},
            "silent_named_both": sorted(named["a"] & named["b"] & set(silent)),
        }
    return out
