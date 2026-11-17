"""Where meeting-memory answers go wrong: retrieved, retained, accurate.

The breakdown follows the one in Granola's engineering post on agent retrieval (Nov 2026): for
every fact a correct answer needs, did the passage holding it reach the agent (retrieved), did
the answer use it (retained), and is the answer right (accurate). Applied to the phase-2
meeting-memory test run (``data/private/memory/qa/qa_v2_test``): three conditions (record,
search, named), 62 questions, verdicts already settled by two blind readers and a tie-break.

Frozen rules (written 2026-11-17, before any per-question verdict was joined to a fact; the
counts by condition were already known from the phase-2 write-up)
-----------------------------------------------------------------------------------------------

**Facts.** A question's facts come from its gold, not from any answer:

* A question built from abstractive summary sentences (``evidence_source`` ``summlink`` or
  ``none``) has one fact per gold sentence (``gold_sentence_ids``): the sentence's text, and as
  evidence the transcript lines of the dialogue acts the annotator linked to that sentence
  (``summlink.xml``), as ``(meeting, line id)``.
* A who-said question (``quoted_act``) has one fact: the gold answer (the speaker's role), with
  the quoted dialogue act's line as evidence.
* A sentence with no linked act is a fact with no evidence ids (the text rule below applies).

**What the agent saw** (its context), per answer:

* record: the record as rendered in the job (compact view), plus the replayed output of every
  logged lookup (``lines``, ``search``, ``entry``);
* search: the replayed output of every logged lookup;
* named: the full transcripts of the question's meetings in the job.

Lookups are logged as commands, not outputs; they are deterministic over the corpus and the
final record, so they are replayed, and each replay must reproduce the logged line and
character counts (a mismatch is an error, not a warning). The job's own instructions are not
context (their example pointer ``ES2006a L0123`` is not a line the agent saw).

**1. Retrieved** (deterministic), per fact:

* *Id rule* (fact has evidence ids): retrieved if any of its evidence lines appears in the
  context as a line id of that meeting: a transcript line (named transcript, ``lines`` or
  ``search`` output) or a record pointer (``<meeting> <line id> "<quote>"``; the compact view
  cuts the quote to 12 words). Which of the two carried it is recorded.
* *Text rule* (fact has no evidence ids): retrieved if the ROUGE-L recall of the fact's content
  words against the best window of :data:`WINDOW` consecutive context lines is at least
  :data:`TEXT_THRESHOLD` (fixed here, not tuned).

An answer is *retrieved* when every one of its question's facts is.

**2. Retained**, per fact: ROUGE-L recall of the fact's content words in the answer is at least
the calibrated threshold, OR the readers' settled verdict for the answer is ``correct`` (a
correct verdict says the answer states what the gold needs). The threshold is calibrated on a
held-aside set, never on test: every (answer, fact) pair of the two IS1008 development runs
(``qa_v2_dev`` and ``qa_v2_dev_draft1``, 78 answers), labelled "does this answer state this
fact, yes or no" by two blind agent readers (no condition, no verdict, order shuffled) with a
third on disagreements. The threshold is the value on the grid 0.05, 0.10, ..., 0.95 that
maximises agreement with those labels; ties go to the middle of the tied values (lower middle).
Agreement and Cohen's kappa at that threshold are reported. An answer is *retained* when every
fact is.

**3. Accurate**: the settled phase-2 verdict is ``correct`` (``partially_correct`` and
``incorrect`` are not).

**Classes**, per answer, first that applies: ``correct``; ``not_retrieved`` (some fact not
retrieved); ``not_retained`` (all retrieved, some fact not retained); ``retained_not_accurate``
(all retrieved and retained, verdict not correct).

**Record misses**, a second split of every record-condition answer that is not correct, from
blind agent labels of every gold fact of every test question against the final record (full
view and compact view, no answers, no verdicts; two readers and a tie-break): per fact
``in_record`` (an entry states it as the gold does), ``in_record_wrong`` (an entry covers it but
its text or status contradicts the gold) or ``not_in_record`` (the writer never recorded it).
An answer's split is the first failing link: ``not_in_record`` if any fact is, else
``in_record_wrong`` if any fact is, else ``in_record_unused``.

Content words: lower-case ``[a-z0-9]+`` tokens minus :data:`FUNCTION_WORDS` (role names are
kept: a who-said fact is a role). ROUGE-L recall = LCS(fact, text) / len(fact) over those
tokens, in order.

No network, no model, no I/O here: the script ``scripts/memory_breakdown.py`` reads the files.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

WINDOW = 5
TEXT_THRESHOLD = 0.5
GRID = tuple(round(0.05 * k, 2) for k in range(1, 20))

Category = Literal["correct", "not_retrieved", "not_retained", "retained_not_accurate"]
CATEGORIES: tuple[Category, ...] = (
    "correct",
    "not_retrieved",
    "not_retained",
    "retained_not_accurate",
)
RecordLabel = Literal["in_record", "in_record_wrong", "not_in_record"]
RECORD_LABELS: tuple[RecordLabel, ...] = ("in_record", "in_record_wrong", "not_in_record")
RecordSplit = Literal["not_in_record", "in_record_wrong", "in_record_unused"]
RECORD_SPLITS: tuple[RecordSplit, ...] = ("not_in_record", "in_record_wrong", "in_record_unused")

LineKey = tuple[str, str]  # (meeting id, line id), e.g. ("ES2005a", "L0101")

# fmt: off
FUNCTION_WORDS = frozenset((
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at", "by", "for", "with",
    "from", "as", "into", "about", "that", "this", "these", "those", "it", "its", "is", "are",
    "was", "were", "be", "been", "being", "will", "would", "should", "could", "can", "may",
    "might", "must", "shall", "do", "does", "did", "has", "have", "had", "he", "she", "they",
    "them", "their", "his", "her", "we", "our", "us", "you", "your", "i", "me", "my", "which",
    "who", "whom", "what", "there", "then", "than", "so", "also", "if", "not", "no", "all",
    "any", "some", "such", "very", "just", "s",
))
# fmt: on
_TOKEN = re.compile(r"[a-z0-9]+")
_REF = re.compile(r"\b([A-Z]{2}\d{4}[a-z]) (L\d{4})\b")
_HEADER = re.compile(r"^## Meeting (\S+): full human transcript")
_BARE = re.compile(r"^(L\d{4}) \[")


class Fact(BaseModel):
    """One fact a correct answer needs, with the transcript lines that hold it."""

    model_config = ConfigDict(frozen=True)

    qid: str
    fact_id: str  # gold sentence id, or "<qid>#who" for a who-said question
    text: str
    evidence: tuple[LineKey, ...]
    rule: Literal["ids", "text"]


def facts_for(
    qid: str,
    sentences: Sequence[tuple[str, str, Sequence[LineKey]]],
    quoted: Sequence[LineKey],
    gold_answer: str,
) -> list[Fact]:
    """A question's facts. ``sentences`` are ``(sentence id, text, linked lines)`` for its gold
    summary sentences; ``quoted`` the lines of a who-said question's quoted act."""
    if not sentences:
        return [
            Fact(
                qid=qid,
                fact_id=f"{qid}#who",
                text=gold_answer,
                evidence=tuple(quoted),
                rule="ids" if quoted else "text",
            )
        ]
    return [
        Fact(
            qid=qid,
            fact_id=sid,
            text=text,
            evidence=tuple(links),
            rule="ids" if links else "text",
        )
        for sid, text, links in sentences
    ]


def content(text: str) -> list[str]:
    """Content words in order (see module docstring)."""
    return [w for w in _TOKEN.findall(text.lower()) if w not in FUNCTION_WORDS]


def _lcs(a: Sequence[str], b: Sequence[str]) -> int:
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def rouge_l_recall(reference: str, text: str) -> float:
    """ROUGE-L recall of ``reference``'s content words in ``text`` (1.0 for an empty
    reference)."""
    ref = content(reference)
    if not ref:
        return 1.0
    return _lcs(ref, content(text)) / len(ref)


def best_window_recall(reference: str, lines: Sequence[str], window: int = WINDOW) -> float:
    """Highest ROUGE-L recall of ``reference`` against any ``window`` consecutive lines."""
    if not lines:
        return 0.0
    starts = range(max(1, len(lines) - window + 1))
    return max(rouge_l_recall(reference, "\n".join(lines[i : i + window])) for i in starts)


def seen_lines(text: str) -> dict[LineKey, str]:
    """Transcript lines visible in ``text`` -> how: ``"line"`` (the line itself, from a named
    transcript block or lookup output) or ``"pointer"`` (a record pointer). A line seen both
    ways is ``"line"``."""
    out: dict[LineKey, str] = {}
    meeting: str | None = None
    for raw in text.splitlines():
        head = _HEADER.match(raw)
        if head:
            meeting = head[1]
            continue
        if raw.startswith("## "):
            meeting = None
        bare = _BARE.match(raw)
        if bare and meeting:
            out[(meeting, bare[1])] = "line"
            continue
        full = re.match(r"^([A-Z]{2}\d{4}[a-z]) (L\d{4}) \[", raw)
        if full:
            out[(full[1], full[2])] = "line"
            continue
        for m, lid in _REF.findall(raw):
            out.setdefault((m, lid), "pointer")
    return out


class Retrieval(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_id: str
    rule: Literal["ids", "text"]
    retrieved: bool
    via: Literal["line", "pointer", "text", "none"]
    window_recall: float | None = None


def retrieve(fact: Fact, seen: Mapping[LineKey, str], lines: Sequence[str]) -> Retrieval:
    """Whether ``fact`` reached the agent, by the frozen rule for its kind."""
    if fact.rule == "ids":
        hows = {seen[k] for k in fact.evidence if k in seen}
        via: Literal["line", "pointer", "none"] = (
            "line" if "line" in hows else "pointer" if hows else "none"
        )
        return Retrieval(fact_id=fact.fact_id, rule="ids", retrieved=bool(hows), via=via)
    r = round(best_window_recall(fact.text, lines), 4)
    ok = r >= TEXT_THRESHOLD
    return Retrieval(
        fact_id=fact.fact_id,
        rule="text",
        retrieved=ok,
        via="text" if ok else "none",
        window_recall=r,
    )


def classify(retrieved: Sequence[bool], retained: Sequence[bool], correct: bool) -> Category:
    """An answer's class: the first that applies of correct, not retrieved, not retained,
    retained but not accurate."""
    if correct:
        return "correct"
    if not all(retrieved):
        return "not_retrieved"
    if not all(retained):
        return "not_retained"
    return "retained_not_accurate"


def record_split(labels: Sequence[RecordLabel]) -> RecordSplit:
    """A record miss's first failing link over its facts' record labels."""
    if "not_in_record" in labels:
        return "not_in_record"
    if "in_record_wrong" in labels:
        return "in_record_wrong"
    return "in_record_unused"


class Calibration(BaseModel):
    model_config = ConfigDict(frozen=True)

    threshold: float
    n: int
    agree: int
    tp: int
    fp: int
    fn: int
    tn: int
    kappa: float
    by_threshold: dict[str, int]


def _kappa(tp: int, fp: int, fn: int, tn: int) -> float:
    n = tp + fp + fn + tn
    if n == 0:
        return 0.0
    po = (tp + tn) / n
    pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n)
    return 0.0 if pe == 1 else round((po - pe) / (1 - pe), 4)


def calibrate(scores: Sequence[float], labels: Sequence[bool]) -> Calibration:
    """The grid threshold that best agrees with ``labels`` (a score >= threshold says "states
    the fact"); ties go to the lower middle of the tied thresholds."""
    if len(scores) != len(labels) or not scores:
        raise ValueError("need as many labels as scores, at least one")
    agree = {t: sum((s >= t) == y for s, y in zip(scores, labels, strict=True)) for t in GRID}
    best = max(agree.values())
    tied = [t for t in GRID if agree[t] == best]
    t = tied[(len(tied) - 1) // 2]
    tp = sum(s >= t and y for s, y in zip(scores, labels, strict=True))
    fp = sum(s >= t and not y for s, y in zip(scores, labels, strict=True))
    fn = sum(s < t and y for s, y in zip(scores, labels, strict=True))
    tn = len(scores) - tp - fp - fn
    return Calibration(
        threshold=t,
        n=len(scores),
        agree=best,
        tp=tp,
        fp=fp,
        fn=fn,
        tn=tn,
        kappa=_kappa(tp, fp, fn, tn),
        by_threshold={f"{k:.2f}": v for k, v in agree.items()},
    )


def settle(
    a: Mapping[str, str], b: Mapping[str, str], c: Mapping[str, str]
) -> tuple[dict[str, str], int]:
    """Final label per key: readers a and b when they agree, else reader c. Returns the labels
    and how many keys a and b agreed on. A split c did not settle raises."""
    if set(a) != set(b):
        raise ValueError(f"readers a and b labelled different keys: {sorted(set(a) ^ set(b))[:5]}")
    out: dict[str, str] = {}
    agree = 0
    for k in sorted(a):
        if a[k] == b[k]:
            agree += 1
            out[k] = a[k]
        elif k in c:
            out[k] = c[k]
        else:
            raise ValueError(f"readers split on {k} and reader c has no label")
    return out, agree


class Row(BaseModel):
    """One answer's breakdown."""

    model_config = ConfigDict(frozen=True)

    qid: str
    condition: str
    group: Literal["single", "cross"]
    verdict: str
    category: Category
    facts: int
    facts_retrieved: int
    facts_retained: int
    record_split: RecordSplit | None = None


def table(rows: Sequence[Row]) -> dict[str, Any]:
    """Counts of each class by condition, overall and by group, plus fact-level rates and the
    record-miss split."""
    out: dict[str, Any] = {}
    for cond in sorted({r.condition for r in rows}):
        rs = [r for r in rows if r.condition == cond]
        block: dict[str, Any] = {}
        for name, sub in (
            ("all", rs),
            ("single", [r for r in rs if r.group == "single"]),
            ("cross", [r for r in rs if r.group == "cross"]),
        ):
            counts = Counter(r.category for r in sub)
            block[name] = {"n": len(sub)} | {c: counts.get(c, 0) for c in CATEGORIES}
        block["facts"] = {
            "n": sum(r.facts for r in rs),
            "retrieved": sum(r.facts_retrieved for r in rs),
            "retained": sum(r.facts_retained for r in rs),
        }
        splits = Counter(r.record_split for r in rs if r.record_split)
        if splits:
            block["record_split"] = {s: splits.get(s, 0) for s in RECORD_SPLITS}
            for name in ("single", "cross"):
                sub_s = Counter(r.record_split for r in rs if r.record_split and r.group == name)
                block[f"record_split_{name}"] = {s: sub_s.get(s, 0) for s in RECORD_SPLITS}
        out[cond] = block
    return out
