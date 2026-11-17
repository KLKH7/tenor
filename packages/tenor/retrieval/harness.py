"""Step 3: score an agent's answer the way Granola's MCP eval does, from the tool-call log.

For every fact a correct answer needs (one per gold summary sentence, with the transcript lines
the AMI annotator linked to it; see ``tenor.evals.memory_breakdown.Fact``):

1. Retrieved: did the fact reach the agent in any tool response, a transcript passage or a
   note? Deterministic, from the log, never from what the agent says it read. Either one of the
   fact's evidence lines was shown, or the fact's words are in some 5-line window of any output
   (ROUGE-L recall >= TEXT_THRESHOLD, 0.5): notes carry no line ids, so they need the text rule.
2. Retained: did the answer use it? ROUGE-L recall of the fact's content words in the answer
   >= RETAINED_THRESHOLD (0.30, calibrated against 84 blind reader labels in the breakdown run).
3. Accurate: the answer's verdict from blind readers (passed in), plus two checks that do not
   trust any reader, each failing the trial outright (as in the post):
   - it cites a meeting the agent never opened (no notes or transcript of it in the log);
   - it puts quotation marks around words not found in any tool response (or in the question
     itself: quoting the person's own words back is not an invented quote).

A trial passes only if the verdict is correct and neither hard check fails. Questions are run
``n`` times; a question's pass rate is the share of its trials that pass.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from statistics import mean

from pydantic import BaseModel, ConfigDict

from tenor.evals.memory_breakdown import TEXT_THRESHOLD, Fact, best_window_recall, rouge_l_recall
from tenor.retrieval.tools import ToolCall

RETAINED_THRESHOLD = 0.30
MIN_QUOTE_WORDS = 3

_PASSAGE = re.compile(r"^\[(\S+) chunk \d+ \|", re.M)
_NOTES = re.compile(r"^# (\S+): ", re.M)
_LINE = re.compile(r"^(L\d{4}) ", re.M)
_QUOTE = re.compile(r"[\"“]([^\"”]+)[\"”]")
_WS = re.compile(r"[^a-z0-9]+")


def seen_lines(log: Sequence[ToolCall]) -> set[tuple[str, str]]:
    """Every (meeting, line id) shown to the agent inside a transcript passage."""
    seen: set[tuple[str, str]] = set()
    for call in log:
        for block in call.output.split("\n\n"):
            head = _PASSAGE.match(block)
            if head:
                seen.update((head.group(1), lid) for lid in _LINE.findall(block))
    return seen


def opened_meetings(log: Sequence[ToolCall]) -> set[str]:
    """Meetings whose notes or transcript the agent was shown (a title in a list does not count)."""
    opened: set[str] = set()
    for call in log:
        opened.update(_PASSAGE.findall(call.output))
        if call.tool == "get_meetings":
            opened.update(m for m in _NOTES.findall(call.output))
    return opened


def cited_meetings(answer: str, known: Iterable[str]) -> set[str]:
    """Meeting ids from `known` that the answer names."""
    return {m for m in known if re.search(rf"\b{re.escape(m)}\b", answer)}


def _norm(text: str) -> str:
    return _WS.sub(" ", text.lower()).strip()


def unsupported_quotes(answer: str, log: Sequence[ToolCall], question: str = "") -> list[str]:
    """Quoted spans of >= MIN_QUOTE_WORDS words found in no tool response and not in the
    question (after lowercasing and dropping punctuation)."""
    seen = _norm("\n".join([question, *(c.output for c in log)]))
    bad = []
    for q in _QUOTE.findall(answer):
        if len(_norm(q).split()) >= MIN_QUOTE_WORDS and _norm(q) not in seen:
            bad.append(q)
    return bad


class FactCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_id: str
    retrieved: bool
    retained: bool
    via: str  # "line", "text" or "none": how the fact was retrieved
    recall: float


class Trial(BaseModel):
    """One run of one question."""

    model_config = ConfigDict(frozen=True)

    qid: str
    run: int
    facts: tuple[FactCheck, ...]
    verdict_correct: bool
    cited_unopened: tuple[str, ...]
    unsupported: tuple[str, ...]
    chars_read: int

    @property
    def passed(self) -> bool:
        return self.verdict_correct and not self.cited_unopened and not self.unsupported

    @property
    def category(self) -> str:
        """First that applies: passed, hard fail, not retrieved, not retained, not accurate."""
        if self.passed:
            return "passed"
        if self.cited_unopened or self.unsupported:
            return "hard_fail"
        if not all(f.retrieved for f in self.facts):
            return "not_retrieved"
        if not all(f.retained for f in self.facts):
            return "not_retained"
        return "not_accurate"


def score_trial(
    qid: str,
    run: int,
    facts: Sequence[Fact],
    answer: str,
    log: Sequence[ToolCall],
    verdict_correct: bool,
    meeting_ids: Iterable[str],
    question: str = "",
) -> Trial:
    """Score one answer against its facts and the log of what its agent was shown."""
    seen = seen_lines(log)
    shown = [ln for c in log for ln in c.output.splitlines() if ln.strip()]
    checks = []
    for f in facts:
        r = rouge_l_recall(f.text, answer)
        if any(k in seen for k in f.evidence):
            via = "line"
        elif best_window_recall(f.text, shown) >= TEXT_THRESHOLD:
            via = "text"
        else:
            via = "none"
        checks.append(
            FactCheck(
                fact_id=f.fact_id,
                retrieved=via != "none",
                via=via,
                retained=verdict_correct or r >= RETAINED_THRESHOLD,
                recall=round(r, 4),
            )
        )
    unopened = sorted(cited_meetings(answer, meeting_ids) - opened_meetings(log))
    return Trial(
        qid=qid,
        run=run,
        facts=tuple(checks),
        verdict_correct=verdict_correct,
        cited_unopened=tuple(unopened),
        unsupported=tuple(unsupported_quotes(answer, log, question)),
        chars_read=sum(len(c.output) for c in log),
    )


class Summary(BaseModel):
    model_config = ConfigDict(frozen=True)

    questions: int
    trials: int
    pass_rate: float  # mean over questions of each question's pass rate across its runs
    categories: dict[str, int]  # over all trials
    facts_retrieved: float  # share of fact checks retrieved
    facts_retained: float
    hard_fails: dict[str, int]
    mean_chars_read: float


def summarize(
    trials: Sequence[Trial], groups: Mapping[str, str] | None = None
) -> dict[str, Summary]:
    """Pass rates and failure categories, overall and per group (e.g. single vs cross-meeting)."""
    buckets: dict[str, list[Trial]] = {"all": list(trials)}
    for t in trials:
        if groups and t.qid in groups:
            buckets.setdefault(groups[t.qid], []).append(t)
    out = {}
    for name, ts in buckets.items():
        by_q: dict[str, list[Trial]] = {}
        for t in ts:
            by_q.setdefault(t.qid, []).append(t)
        facts = [f for t in ts for f in t.facts]
        cats: dict[str, int] = {}
        for t in ts:
            cats[t.category] = cats.get(t.category, 0) + 1
        out[name] = Summary(
            questions=len(by_q),
            trials=len(ts),
            pass_rate=round(mean(mean(t.passed for t in q) for q in by_q.values()), 4)
            if by_q
            else 0.0,
            categories=dict(sorted(cats.items())),
            facts_retrieved=round(mean(f.retrieved for f in facts), 4) if facts else 0.0,
            facts_retained=round(mean(f.retained for f in facts), 4) if facts else 0.0,
            hard_fails={
                "cited_unopened": sum(bool(t.cited_unopened) for t in ts),
                "unsupported_quote": sum(bool(t.unsupported) for t in ts),
            },
            mean_chars_read=round(mean(t.chars_read for t in ts), 1) if ts else 0.0,
        )
    return out


__all__ = [
    "RETAINED_THRESHOLD",
    "FactCheck",
    "Summary",
    "Trial",
    "cited_meetings",
    "opened_meetings",
    "score_trial",
    "seen_lines",
    "summarize",
    "unsupported_quotes",
]
