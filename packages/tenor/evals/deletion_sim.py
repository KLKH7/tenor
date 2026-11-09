"""The real-meeting deletion simulation (docs/tracks/RETENTION.md).

Four conditions answer the same real questions; each later one adds one layer:

* ``granola`` (A): Granola's own agent through its public MCP (``query_granola_meetings``), a
  black box, recorded once and replayed (:class:`~tenor.evals.real_slice.GranolaAgentSystem`).
* ``notes_only`` (B, "after deletion"): Granola's own notes (AI summary + typed notes) for every
  meeting held by the question's as-of date, and nothing else, answered by ``answer.v1`` on the
  same answer model as every other system. The notes are the copy synced from the public MCP
  (``get_meetings``) into ``data/real.db``, so no live call is made. This *approximates* what
  Granola's chat has after transcript auto-deletion; it is not Granola's post-deletion agent.
* ``notes_facts`` (C): B plus Concentrate's verified facts, commitments and digests, with every
  transcript deleted (``retire_after_days`` = :data:`RETIRE_ALL`): what a fact layer extracted
  before deletion would keep.
* ``notes_facts_fallback`` (D): C plus the transcript fallback (``on_abstain``) reading the
  transcripts, i.e. deletion of the hot copy with the transcript kept cold and read only when
  the facts fall short. For reference: it needs transcripts to exist.

Per answer we record the primary verdict (``judge.v1``), an optional second judge (same prompt,
different model), the question's tags (from P2-T's tag file, if any) and
:func:`confident_error`. Scoring reuses :func:`~tenor.evals.real_slice.run_slice`
unchanged; this module annotates its rows and summarises per condition, category and tag.

:func:`quote` prices a run before anything is spent: input tokens are counted exactly with the
free token-counting endpoint plus a structured-output overhead measured on cached calls, cached
requests cost $0, and output tokens come from the recorded outputs of earlier real-slice calls.
"""

from __future__ import annotations

import json
import re
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from tenor.agents import corpus as cv
from tenor.agents.baselines import (
    QuestionLike,
    _Base,
    _Prompt,
    _user_prompt,
)
from tenor.agents.concentrate_system import ConcentrateSystem
from tenor.core.models import MeetingNote
from tenor.evals.faithfulness import wilson
from tenor.evals.retirement import meeting_day
from tenor.evals.scoring import JudgeOutput, bootstrap_ci, judge_request_for
from tenor.evals.stats import binom_two_sided_p
from tenor.llm.client import (
    LLMClient,
    LLMRequest,
    TurnCompletion,
    TurnRequest,
    cost_usd,
    estimate_tokens,
    turn_cache_path,
)
from tenor.memory.fallback import Passages, cited_line_ids, says_not_covered
from tenor.memory.query import Context

CONDITIONS: tuple[str, ...] = ("granola", "notes_only", "notes_facts", "notes_facts_fallback")
CONDITION_LABELS = {
    "granola": "A: Granola full",
    "notes_only": "B: notes only (after deletion)",
    "notes_facts": "C: notes + fact layer",
    "notes_facts_fallback": "D: notes + facts + transcript fallback",
}
# Report names: Granola keeps its existing ``granola_agent`` name so recorded answers line up.
SYSTEM_NAMES = {"granola": "granola_agent"} | {c: c for c in CONDITIONS[1:]}
# Each rung is compared with the one before it (paired, same questions).
LADDER: tuple[tuple[str, str], ...] = (
    ("notes_only", "granola"),
    ("notes_facts", "notes_only"),
    ("notes_facts_fallback", "notes_facts"),
)
# (report key, later system name, earlier system name): what ``summarize_deletion`` pairs.
Pair = tuple[str, str, str]
PAIRS: tuple[Pair, ...] = tuple(
    (f"{later} vs {earlier}", SYSTEM_NAMES[later], SYSTEM_NAMES[earlier])
    for later, earlier in LADDER
)
# ``build_context`` retires meetings with day < as_of - N; N = -1 retires every meeting held on
# or before the as-of date, including one held that same day (N = 0 would keep its transcript).
RETIRE_ALL = -1
_RETIRED_NOTE = re.compile(r"older than -1 days are retired")


def held_notes(notes: Sequence[MeetingNote], as_of: Any) -> list[MeetingNote]:
    """Meetings held on or before ``as_of``, oldest first."""
    return [m for m in cv.by_date(list(notes)) if meeting_day(m) <= as_of]


def notes_block(notes: Sequence[MeetingNote], as_of: Any) -> str:
    """Granola's notes (header, AI summary, typed notes) for every meeting held by ``as_of``:
    the same ``summary_only_view`` B1 and the retirement curve use. No transcript lines."""
    body = "\n\n".join(cv.summary_only_view(m) for m in held_notes(notes, as_of))
    return f"<meetings>\n{body or '(no meetings)'}\n</meetings>"


class NotesOnlySystem(_Base):
    """B: every held meeting's notes, nothing else, in one ``answer.v1`` call. The corpus is
    small (4 meetings, ~4k tokens of notes), so this is all notes, not a retrieval: an upper
    bound on what a notes-only agent can see (it can't miss a note by searching badly)."""

    name = "notes_only"

    def __init__(self, client: LLMClient, notes: Sequence[MeetingNote], **kw: Any) -> None:
        super().__init__(client, **kw)
        self.notes = list(notes)

    def _context(self, q: QuestionLike) -> tuple[str, list[str]]:
        held = held_notes(self.notes, q.as_of)
        return f"{notes_block(self.notes, q.as_of)}\n\n{_user_prompt(q)}", [m.note_id for m in held]

    def _prompt(self, q: QuestionLike) -> _Prompt:
        user, retrieved = self._context(q)
        # The notes block comes first and is shared by every question on the same corpus: a
        # cache breakpoint at its end lets those questions read it from the prompt cache.
        tail = _user_prompt(q)
        breaks = (len(user) - len(tail),) if user.endswith(tail) and len(user) > len(tail) else ()
        return _Prompt(
            self.instructions, user, cache_system=False, retrieved=retrieved, cache_breaks=breaks
        )

    def prompt_texts(self, q: QuestionLike) -> tuple[str, str]:
        """(system, user) exactly as sent, for exact token counting."""
        return self.instructions, self._context(q)[0]

    def estimate(self, q: QuestionLike) -> tuple[int, int]:
        system, user = self.prompt_texts(q)
        return estimate_tokens(system) + estimate_tokens(user), self.OUTPUT_TOKENS_EST


class NotesFactsSystem(ConcentrateSystem):
    """C: Granola's notes plus Concentrate's memory context with every transcript deleted.

    The memory is the existing real build (same store, same config hash), read with
    ``retire_after_days=RETIRE_ALL``: facts and digests stay, their line IDs stay as pointers,
    and no transcript line is shown. The notes block comes first, then the memory context."""

    def __init__(
        self,
        client: LLMClient,
        *,
        name: str = "notes_facts",
        fallback: str = "off",
        **kw: Any,
    ) -> None:
        super().__init__(client, retire_after_days=RETIRE_ALL, fallback=fallback, name=name, **kw)

    def _context(self, q: QuestionLike) -> Context:
        ctx = super()._context(q)
        memory = _RETIRED_NOTE.sub("are deleted", ctx.text)
        held = [m.note_id for m in held_notes(self._notes, q.as_of)]
        return replace(
            ctx,
            text=f"{notes_block(self._notes, q.as_of)}\n\n{memory}",
            meetings=list(dict.fromkeys([*held, *ctx.meetings])),
        )

    def prompt_texts(self, q: QuestionLike) -> tuple[str, str]:
        """(system, user) of the first answer call, exactly as sent."""
        return self.instructions, self._user(q)[0]

    def fallback_texts(self, q: QuestionLike) -> tuple[str, str] | None:
        """(system, user) of the second (fallback) call, or None when there is none."""
        if self.fallback != "on_abstain":
            return None
        return self.instructions, self._user(q, with_passages=True)[0]


class NotesFactsFallbackSystem(NotesFactsSystem):
    """D: C, plus transcript passages when the first answer abstains or says the material doesn't
    cover it. The passages ignore the deletion (``retire_after_days=None``): this is the
    "transcripts kept cold, read on demand" design, not deletion."""

    def __init__(self, client: LLMClient, **kw: Any) -> None:
        super().__init__(client, name="notes_facts_fallback", fallback="on_abstain", **kw)

    def _passages(self, q: QuestionLike, ctx: Context) -> Passages:
        assert self._fallback is not None
        return self._fallback.passages(
            q.question,
            q.as_of,
            budget_tokens=self.fallback_tokens,
            retire_after_days=None,
            skip_lines=cited_line_ids(ctx.text),
        )


# -- per-answer annotations ------------------------------------------------------------------------


def confident_error(verdict: str, answer: str, abstained: bool) -> bool:
    """Wrong and said with confidence: judged ``incorrect`` while neither abstaining nor saying
    the material doesn't cover it (:func:`~tenor.memory.fallback.says_not_covered`, which
    also reads Granola's prose, since Granola has no abstain flag). A hedged "the notes don't
    say" that the judge marks incorrect is a miss, not a confident error."""
    return verdict == "incorrect" and not says_not_covered(answer, abstained)


def load_tags(path: Path | None) -> dict[str, list[str]]:
    """Question tags from P2-T's tag file: ``{qid: [tag, ...] | tag}`` JSON, or JSONL rows with
    ``qid`` and ``tags`` (list) or ``tag`` (string). A missing file means no tags."""
    if path is None or not path.exists():
        return {}
    text = path.read_text()
    rows: list[dict[str, Any]]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        if isinstance(data, dict):
            return {str(k): _as_list(v) for k, v in data.items()}
        rows = list(data)
    out: dict[str, list[str]] = {}
    for r in rows:
        out[str(r["qid"])] = _as_list(r.get("tags", r.get("tag", [])))
    return out


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    return [str(v)] if isinstance(v, str) else [str(x) for x in v]


def annotate(
    rows: list[dict[str, Any]],
    tags: dict[str, list[str]],
    second: dict[tuple[str, str], str] | None = None,
) -> list[dict[str, Any]]:
    """Report ``.jsonl`` rows plus ``tags``, ``confident_error`` and ``judge2_verdict``."""
    out = []
    for r in rows:
        answer = r.get("answer", {})
        text = answer.get("answer", "") if isinstance(answer, dict) else str(answer)
        abstained = bool(r.get("abstained"))
        j2 = (second or {}).get((r["system"], r["qid"]))
        out.append(
            {
                **r,
                "tags": tags.get(r["qid"], []),
                "confident_error": confident_error(r["verdict"], text, abstained),
                "judge2_verdict": j2,
            }
        )
    return out


# -- summary (aggregates only: safe to commit) -----------------------------------------------------


def _rates(rows: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    n = len(rows)
    correct = [1.0 if r["verdict"] == "correct" else 0.0 for r in rows]
    ce = [1.0 if r["confident_error"] else 0.0 for r in rows]
    out: dict[str, Any] = {
        "n": n,
        "accuracy": sum(correct) / n if n else None,
        "accuracy_ci": list(bootstrap_ci(correct, seed)) if n else None,
        "accuracy_wilson": list(wilson(round(sum(correct)), n)) if n else None,
        "partial": sum(r["verdict"] == "partially_correct" for r in rows) / n if n else None,
        "confident_error": sum(ce) / n if n else None,
        "confident_error_ci": list(bootstrap_ci(ce, seed)) if n else None,
        "confident_error_wilson": list(wilson(round(sum(ce)), n)) if n else None,
        # P2-T's definition (metrics.confident_error): not abstained and judged incorrect, with
        # no hedge detection. An upper bound for Granola, which has no abstain flag.
        "confident_error_flag": sum(
            not r.get("abstained") and r["verdict"] == "incorrect" for r in rows
        )
        / n
        if n
        else None,
        "hedged_miss": sum(r["verdict"] != "correct" and not r["confident_error"] for r in rows) / n
        if n
        else None,
    }
    j2 = [r for r in rows if r.get("judge2_verdict")]
    if j2:
        out["judge2_n"] = len(j2)
        out["judge2_accuracy"] = sum(r["judge2_verdict"] == "correct" for r in j2) / len(j2)
        out["judge_agreement"] = sum(r["judge2_verdict"] == r["verdict"] for r in j2) / len(j2)
    fired = [r for r in rows if "fallback_fired" in r]
    if fired:
        out["fallback_fired"] = sum(bool(r["fallback_fired"]) for r in fired) / len(fired)
    return out


def summarize_deletion(
    rows: list[dict[str, Any]], seed: int, pairs: Sequence[Pair] = PAIRS
) -> dict[str, Any]:
    """Per condition: accuracy, confident-error rate (bootstrap and Wilson CIs), second-judge
    agreement, by category and by tag; plus paired wins/losses for each of ``pairs``
    (default: along :data:`LADDER`)."""
    by_sys: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_sys.setdefault(r["system"], []).append(r)
    conditions: dict[str, Any] = {}
    for name, rs in by_sys.items():
        cats: dict[str, list[dict[str, Any]]] = {}
        tagged: dict[str, list[dict[str, Any]]] = {}
        for r in rs:
            cats.setdefault(r["category"], []).append(r)
            for t in r.get("tags") or ["(untagged)"]:
                tagged.setdefault(t, []).append(r)
        conditions[name] = {
            **_rates(rs, seed),
            "by_category": {c: _rates(v, seed) for c, v in sorted(cats.items())},
            "by_tag": {t: _rates(v, seed) for t, v in sorted(tagged.items())},
        }
    paired: dict[str, Any] = {}
    for key, later, earlier in pairs:
        a, b = by_sys.get(later), by_sys.get(earlier)
        if not a or not b:
            continue
        va = {r["qid"]: r["verdict"] == "correct" for r in a}
        vb = {r["qid"]: r["verdict"] == "correct" for r in b}
        tags = {r["qid"]: r.get("tags") or ["(untagged)"] for r in a}
        both = sorted(set(va) & set(vb))
        groups: dict[str, list[str]] = {"all": both}
        for q in both:
            for t in tags[q]:
                groups.setdefault(t, []).append(q)
        cell = {g: _paired(va, vb, qs) for g, qs in groups.items()}
        paired[key] = {**cell.pop("all"), "by_tag": cell}
    return {"conditions": conditions, "paired": paired}


def _paired(va: dict[str, bool], vb: dict[str, bool], qs: list[str]) -> dict[str, Any]:
    """Wins/losses of the later condition on ``qs``, accuracy delta, exact two-sided sign test
    on the discordant pairs (ties dropped)."""
    wins = sum(va[q] and not vb[q] for q in qs)
    losses = sum(vb[q] and not va[q] for q in qs)
    n = len(qs)
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "delta": (sum(va[q] for q in qs) - sum(vb[q] for q in qs)) / n if n else None,
        "sign_p": binom_two_sided_p(wins, wins + losses) if wins + losses else 1.0,
    }


# -- running ---------------------------------------------------------------------------------------


def second_judge(
    client: LLMClient,
    rows: list[dict[str, Any]],
    questions: dict[str, Any],
    over_budget: Callable[[], bool] = lambda: False,
) -> dict[tuple[str, str], str]:
    """Re-judge rows with ``client`` (a different model), same ``judge.v1`` request, stopping
    (rows left unjudged) as soon as ``over_budget()``."""
    out: dict[tuple[str, str], str] = {}
    for r in rows:
        if over_budget():
            break
        q = questions[r["qid"]]
        req = judge_request_for(
            q.question, q.gold_answer, q.unanswerable, r["answer"]["answer"], r["abstained"]
        )
        out[(r["system"], r["qid"])] = client.structured(req, JudgeOutput).verdict
    return out


def run_deletion_sim(
    systems: list[Any],
    questions: list[Any],
    client: LLMClient,
    *,
    judge2: LLMClient | None,
    tags: dict[str, list[str]],
    seed: int,
    budget_usd: float,
    out_dir: Path,
    split: str,
    meta: dict[str, Any] | None = None,
    spent: Callable[[], float] | None = None,
    pairs: Sequence[Pair] = PAIRS,
) -> dict[str, Any]:
    """``run_slice`` (answer + primary judge), then the second judge, tags and confident-error.
    ``client`` is the primary judge (each system brings its own answer client).

    Writes ``<stamp>.deletion.jsonl`` (PRIVATE, per answer) next to the run_slice files and adds
    the aggregate ``deletion`` summary to the report and its ``.json``."""
    from tenor.evals.real_slice import run_slice

    total = spent or client.ledger.spent_usd
    report = run_slice(
        systems,
        questions,
        client,
        seed=seed,
        budget_usd=budget_usd,
        out_dir=out_dir,
        meta={"conditions": [s.name for s in systems], **(meta or {})},
        spent=spent,
        split=split,  # type: ignore[arg-type]
    )
    jsonl = Path(report["paths"]["jsonl"])
    rows = [json.loads(line) for line in jsonl.read_text().splitlines() if line]
    second: dict[tuple[str, str], str] = {}
    if judge2 is not None:
        second = second_judge(
            judge2, rows, {q.id: q for q in questions}, lambda: total() > budget_usd
        )
        report["meta"]["judge2_model"] = judge2.model
        report["meta"]["judge2_spent_usd"] = judge2.ledger.spent_usd()
        report["meta"]["judge2_judged"] = f"{len(second)}/{len(rows)}"
    annotated = annotate(rows, tags, second)
    report["meta"]["tagged_questions"] = len({r["qid"] for r in annotated if r["tags"]})
    report["deletion"] = summarize_deletion(annotated, seed, pairs)
    jsonl.with_suffix(".deletion.jsonl").write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in annotated)
    )
    saved = {k: v for k, v in report.items() if k != "paths"}
    Path(report["paths"]["json"]).write_text(json.dumps(saved, indent=2, default=str))
    return report


# -- the exact cost quote --------------------------------------------------------------------------

Counter = Callable[[list[str]], list[int]]


@dataclass(frozen=True)
class Calibration:
    """Measured on cached real-slice calls: what token counting alone doesn't see.

    ``*_overhead``: recorded input tokens minus the counted ``system + user`` text (the
    structured-output schema and message framing), median over the cached calls.
    ``*_out``: recorded output tokens (including thinking), mean and 90th percentile.
    ``answer_text``: counted tokens of a recorded answer's text, mean (what the judge reads)."""

    answer_overhead: int
    judge_overhead: int
    answer_out: tuple[float, float]
    judge_out: tuple[float, float]
    answer_text: float
    fire_rate: float  # share of first answers that trigger the fallback (D)
    samples: dict[str, int] = field(default_factory=dict)


def _p90(xs: list[float]) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, round(0.9 * (len(s) - 1)))]


def calibrate(
    answer_pairs: list[tuple[str, int, int]],
    judge_pairs: list[tuple[str, int, int]],
    answer_texts: list[str],
    counter: Counter,
    fire_rate: float,
) -> Calibration:
    """``*_pairs`` are (system + user text, recorded input tokens, recorded output tokens)."""
    if not answer_pairs or not judge_pairs or not answer_texts:
        raise ValueError("calibration needs cached answer calls, judge calls and answer texts")
    a_counts = counter([t for t, _, _ in answer_pairs])
    j_counts = counter([t for t, _, _ in judge_pairs])
    texts = counter(answer_texts)
    return Calibration(
        answer_overhead=round(
            statistics.median(
                rec - c for (_, rec, _), c in zip(answer_pairs, a_counts, strict=True)
            )
        ),
        judge_overhead=round(
            statistics.median(rec - c for (_, rec, _), c in zip(judge_pairs, j_counts, strict=True))
        ),
        answer_out=(
            statistics.fmean(o for _, _, o in answer_pairs),
            _p90([o for _, _, o in answer_pairs]),
        ),
        judge_out=(
            statistics.fmean(o for _, _, o in judge_pairs),
            _p90([o for _, _, o in judge_pairs]),
        ),
        answer_text=statistics.fmean(texts),
        fire_rate=fire_rate,
        samples={"answer": len(answer_pairs), "judge": len(judge_pairs), "texts": len(texts)},
    )


def cached_turn(client: LLMClient, req: TurnRequest) -> TurnCompletion | None:
    """The recorded completion of ``req`` in ``client``'s turn cache, if any."""
    path = turn_cache_path(client, req)
    if not path.exists():
        return None
    return TurnCompletion(**json.loads(path.read_text())["completion"])


def judge_cache_pairs(cache_dir: Path, model: str) -> list[tuple[str, int, int]]:
    """(system + user, recorded input, recorded output) of every cached ``judge.v1`` call."""
    out = []
    for p in sorted(cache_dir.glob("*.json")):
        d = json.loads(p.read_text())
        req = d.get("request") or {}
        if req.get("prompt_id") != "judge.v1" or d.get("model") != model:
            continue
        c = d["completion"]
        out.append((f"{req['system']}\n\n{req['user']}", c["input_tokens"], c["output_tokens"]))
    return out


@dataclass
class QuoteLine:
    """One condition and split. Token counts are totals over the questions; usd is a range."""

    condition: str
    split: str
    questions: int
    answer_calls: int  # uncached answer calls (first calls; D's fallback calls are separate)
    answer_cached: int
    answer_in: int  # exact (counted + measured overhead)
    fallback_calls_max: int = 0
    fallback_in_max: int = 0  # exact, if every first answer triggered the fallback
    judge_calls: int = 0
    judge_cached: int = 0
    judge_in: int = 0  # exact except the answer text when the answer isn't recorded yet
    judge2_calls: int = 0
    judge2_cached: int = 0
    judge2_in: int = 0
    usd_expected: float = 0.0
    usd_high: float = 0.0
    note: str = ""


def _usd(model: str, tin: float, tout: float) -> float:
    return cost_usd(model, round(tin), round(tout))


def quote_condition(
    system: Any,
    condition: str,
    questions: list[Any],
    client: LLMClient,
    judge2: LLMClient | None,
    counter: Counter,
    cal: Calibration,
    *,
    split: str,
    granola_answer: Callable[[Any], tuple[str, bool] | None] | None = None,
    shared: set[Path] | None = None,
    judge: LLMClient | None = None,
) -> QuoteLine:
    """Price one condition on ``questions`` (answer calls + primary judge + second judge).

    Answer and fallback inputs are counted exactly from the requests the system would send;
    requests already in the cache are $0. Judge inputs are exact when the answer is recorded
    (Granola's), else counted with an empty answer plus the mean recorded answer length.
    Expected output = calibrated mean; high = 90th percentile, and every fallback firing.
    ``shared`` collects the answer requests already priced for an earlier condition of the same
    run: an identical request (D's first call is C's, byte for byte) is a cache hit by then.
    ``judge`` is the primary judge when it is not the answer ``client``."""
    line = QuoteLine(condition, split, len(questions), 0, 0, 0)
    exp = high = 0.0
    model = client.model
    if condition != "granola":
        firsts, seconds = [], []
        for q in questions:
            req = system.turn_request(q)
            path = turn_cache_path(client, req)
            if cached_turn(client, req) is not None or (shared is not None and path in shared):
                line.answer_cached += 1
            else:
                firsts.append("\n\n".join(system.prompt_texts(q)))
                if shared is not None:
                    shared.add(path)
            more = getattr(system, "fallback_texts", lambda _q: None)(q)
            if more is not None:
                seconds.append("\n\n".join(more))
        a_in = sum(counter(firsts)) + cal.answer_overhead * len(firsts)
        line.answer_calls, line.answer_in = len(firsts), a_in
        exp += _usd(model, a_in, cal.answer_out[0] * len(firsts))
        high += _usd(model, a_in, cal.answer_out[1] * len(firsts))
        if seconds:
            f_in = sum(counter(seconds)) + cal.answer_overhead * len(seconds)
            line.fallback_calls_max, line.fallback_in_max = len(seconds), f_in
            exp += cal.fire_rate * _usd(model, f_in, cal.answer_out[0] * len(seconds))
            high += _usd(model, f_in, cal.answer_out[1] * len(seconds))
    for jclient, attr in ((judge or client, "judge"), (judge2, "judge2")):
        if jclient is None:
            continue
        texts, pad, cached = [], 0.0, 0
        for q in questions:
            known = granola_answer(q) if granola_answer and condition == "granola" else None
            answer, abstained = known if known is not None else ("", False)
            jreq: LLMRequest = judge_request_for(
                q.question, q.gold_answer, q.unanswerable, answer, abstained
            )
            if known is not None and jclient.is_cached(jreq):
                cached += 1
                continue
            texts.append(f"{jreq.system}\n\n{jreq.user}")
            pad += 0 if known is not None else cal.answer_text
        j_in = round(sum(counter(texts)) + pad) + cal.judge_overhead * len(texts)
        setattr(line, f"{attr}_calls", len(texts))
        setattr(line, f"{attr}_cached", cached)
        setattr(line, f"{attr}_in", j_in)
        exp += _usd(jclient.model, j_in, cal.judge_out[0] * len(texts))
        high += _usd(jclient.model, j_in, cal.judge_out[1] * len(texts))
    line.usd_expected, line.usd_high = exp, high
    return line


def scale_line(line: QuoteLine, n: int, split: str) -> QuoteLine:
    """``line`` priced per question and scaled to ``n`` questions (for a split not written yet).

    ``line`` must have been priced with no judge cache hits (a new split's answers are new). An
    answer call shared with an earlier condition (D's first call is C's) stays shared."""
    if line.judge_cached or line.judge2_cached:
        raise ValueError("scale a quote priced without cache hits")
    if line.questions == 0:
        return replace(line, split=split, questions=n)
    f = n / line.questions
    fields = (
        "answer_calls answer_cached answer_in fallback_calls_max fallback_in_max "
        "judge_calls judge_in judge2_calls judge2_in"
    )
    scaled: dict[str, Any] = {k: round(getattr(line, k) * f) for k in fields.split()}
    return replace(
        line,
        **scaled,
        split=split,
        questions=n,
        usd_expected=line.usd_expected * f,
        usd_high=line.usd_high * f,
        note=f"scaled from {line.split} ({line.questions} q) to {n}; {line.note}".strip("; "),
    )


def quote_markdown(lines: list[QuoteLine], cal: Calibration, model: str, judge2: str) -> str:
    """The quote as a table (aggregates only)."""
    out = [
        f"Answer + judge model `{model}`, second judge `{judge2}`. Inputs counted exactly "
        f"(count_tokens) + overhead measured on cached calls (answer +{cal.answer_overhead}, "
        f"judge +{cal.judge_overhead} tokens/call; {cal.samples}). Output: recorded mean "
        f"(answer {cal.answer_out[0]:.0f}, judge {cal.judge_out[0]:.0f}) for 'expected', p90 "
        f"(answer {cal.answer_out[1]:.0f}, judge {cal.judge_out[1]:.0f}) for 'high'. "
        f"Fallback fire rate {cal.fire_rate:.0%} expected, 100% high.",
        "",
        "| condition | split | q | answer calls (cached) | answer in | fallback in (max) | "
        "judge calls (cached) | judge in | judge2 calls (cached) | judge2 in | expected | high |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for x in lines:
        out.append(
            f"| {CONDITION_LABELS.get(x.condition, x.condition)} | {x.split} | {x.questions} | "
            f"{x.answer_calls} ({x.answer_cached}) | {x.answer_in:,} | {x.fallback_in_max:,} | "
            f"{x.judge_calls} ({x.judge_cached}) | {x.judge_in:,} | "
            f"{x.judge2_calls} ({x.judge2_cached}) | {x.judge2_in:,} | "
            f"${x.usd_expected:.2f} | ${x.usd_high:.2f} |"
        )
    for split in sorted({x.split for x in lines}):
        e = sum(x.usd_expected for x in lines if x.split == split)
        h = sum(x.usd_high for x in lines if x.split == split)
        out.append(f"| **total** | {split} | | | | | | | | | **${e:.2f}** | **${h:.2f}** |")
    notes = [f"- {x.condition}/{x.split}: {x.note}" for x in lines if x.note]
    return "\n".join(out + (["", *notes] if notes else [])) + "\n"
