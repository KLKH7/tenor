"""Who is "Them"? Naming a note-taker's generic speaker labels from text alone, scored against
AMI's true speakers.

A product transcript labels remote speech generically (Granola: ``Them``; Wispr Flow:
``Speaker N``, sometimes a name it picked up), so action items lose their owners. A generator
(``prompts/speaker_naming.v1.md``, a Claude Code agent, no API) reads an attendee list and the
product transcript and names the speaker of every line or says ``unknown``. This module holds
the two deterministic halves around it; no LLM, no network.

**Truth** (:func:`align`, ``scripts/speaker_truth.py``). AMI's human transcript gives every
word a speaker and a time; the product transcript gives lines with times on its own clock.
Each product line gets its true speaker in three steps:

1. *Word anchors.* Both sides are tokenised the same way (:func:`tokens`: ``wer.normalise``
   plus fillers kept in one spelling, so a line that is only "Mm-hm." can still align). One
   global longest-matching-blocks alignment (``difflib.SequenceMatcher``) of the product's
   words, in line order, against AMI's words, all speakers merged by start time.
2. *Replay offset.* A product word's time is interpolated inside its line; ``offset`` =
   product time - AMI time. The product clock starts when the recording did, not when AMI's
   audio did, so the offset is estimated, not assumed: the median over anchors (reported),
   and a running median (:data:`SMOOTH` anchors) for local drift. Anchors more than
   :data:`TOLERANCE_S` from the running median are dropped (a common word matched to the
   wrong place), unless they sit in a run of :data:`LONG_BLOCK` matching words (Wispr's
   clock is not real time: it squeezes silences, so its offset jumps and drifts).
3. *Local matches.* A product word with no anchor takes an unused AMI word with the same
   token that lies within :data:`MAX_DEV_S` of its expected AMI time and no further than that
   outside its neighbouring anchors; the nearest wins. Overlapping speech, which the global
   alignment has to drop because AMI interleaves it, is recovered here.

A line's **true speaker** is the AMI speaker with most of its matched words; **confidence** is
that speaker's words over all the line's words (unmatched words count against it, so a badly
transcribed line is not confident). A line is **mixed** when a second speaker has at least
:data:`MIXED_MIN_WORDS` words and :data:`MIXED_SHARE` of the matched words, **unaligned** when
no word matched. Scoring uses *confident* lines only: confidence >= :data:`CONFIDENT` and not
mixed (:func:`is_confident`).

**Scoring** (:func:`score_unit`, :func:`summarise`). On confident lines: coverage (share given
a name), accuracy when named (overall, by the generator's confidence and basis), both also
weighted by words, and on lines of at least :data:`MIN_WORDS` words (a one-word line's truth
rests on one matched "yeah"). Baselines, all of which read the truth and so are upper bounds
for their strategy: every line = the most talkative attendee (:func:`majority_baseline`), and the
product's own labels mapped to people one-to-one in the best possible way
(:func:`label_baseline`; for ``Speaker N`` labels this is the ceiling of diarisation with no
text evidence), plus the same with several labels allowed per person (purity).

**Action owners** (:func:`action_owner`), the number that matters for a product. For each AMI
gold action whose owner is one role (``owner_check.gold_owner``) and that has summary links:

* **commit**: the confident product lines truly spoken by the owner that carry words of a
  linked dialogue act spoken by the owner (the owner says "I'll do it"). The owner is named
  *correct* when the generator's speaker for those lines, weighted by words, is the owner;
  *unnamed* when it is ``unknown``; else *wrong* (a tie takes the worse outcome).
* **addressed**: no such line, so the linked acts are someone else assigning the task. The
  reply is the owner's first confident line holding words of one of the :data:`REPLY_ACTS`
  dialogue acts (all speakers, in time order) after the last linked act; the item is scored
  on the generator's speaker for that line. With no such line the item is listed as
  ambiguous, not guessed. (The truth picks the line; the generator's name for it is scored.)

Items with no single owner, no links, or no confident line are listed with the reason.
"""

from __future__ import annotations

import bisect
import difflib
import itertools
import json
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tenor.ami import wer as W
from tenor.evals.coverage_e4 import TEST_MEETINGS, ratio_ci

DEV_MEETINGS = ("IS1008a",)
PRODUCTS = ("granola", "wispr")
READERS = Path("data/private/reader")
TRUTH_DIR = READERS / "speaker_truth"
RUNS_DIR = READERS / "speaker_agent_runs"

TRUTH_VERSION = "speaker_truth.v1"
PROMPT = "speaker_naming.v1"
CONFIDENT = 0.7  # a line's top speaker must hold this share of its words
MIXED_SHARE = 0.25  # second speaker's share of the matched words that makes a line mixed
MIXED_MIN_WORDS = 2
SMOOTH = 51  # anchors in the running median of the offset
TOLERANCE_S = 6.0  # an anchor further than this from the running median is dropped ...
LONG_BLOCK = 4  # ... unless it sits in a run of this many consecutive matching words
MAX_DEV_S = 8.0  # a local match: this close to the word's expected AMI time and its anchors
REPLY_ACTS = 8  # AMI dialogue acts after the linked passage in which the owner's reply counts
MIN_WORDS = 3  # robustness cut: lines this long, whose truth rests on more than a word or two
UNKNOWN = "unknown"
CONFIDENCES = ("high", "medium", "low")
BASES = ("intro", "addressed", "role_content", "turn_taking", "diarisation_label", "none")

# One spelling per hesitation / backchannel, so "Mm-hm." and AMI's "Mm-hmm" are the same token.
_FILLER = {
    **dict.fromkeys(("uh", "um", "uhm", "erm", "er", "ah", "eh"), "uh"),
    **dict.fromkeys(("hmm", "hm", "mm", "mmm", "huh"), "mm"),
    **dict.fromkeys(("mhm", "mhmm", "mm-hmm", "mm-hm", "mmhmm", "mmhm", "uh-huh"), "mhm"),
}
_STRIP = ".,!?;:\"()[]…-'"


def tokens(text: str) -> list[str]:
    """Alignment tokens: ``wer.normalise`` word by word (lower case, numbers spelled out, no
    punctuation), with fillers kept in one spelling each instead of dropped."""
    out: list[str] = []
    for raw in text.lower().split():
        filler = _FILLER.get(raw.strip(_STRIP))
        if filler is not None:
            out.append(filler)
        else:
            out.extend(W.normalise(raw))
    return out


# -- inputs -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RefWord:
    """One token of AMI's human transcript."""

    agent: str  # AMI speaker letter
    start: float  # seconds on AMI's clock
    token: str
    act: int = -1  # index of the dialogue act holding it (-1: none)


@dataclass(frozen=True)
class ProdLine:
    """One line of a product transcript (``T<index>`` in the packets and job files)."""

    index: int
    label: str  # the product's own speaker label
    start: float  # seconds on the product's clock
    end: float
    text: str


@dataclass(frozen=True)
class LineTruth:
    """The true speaker of one product line."""

    index: int
    n_words: int
    speaker: str | None  # AMI agent with most matched words; None when unaligned
    confidence: float  # that speaker's words / n_words
    mixed: bool
    words: Mapping[str, int]  # matched words per AMI agent
    acts: Mapping[int, int]  # matched words per dialogue-act index

    @property
    def status(self) -> str:
        if self.speaker is None:
            return "unaligned"
        if self.mixed:
            return "mixed"
        return "confident" if self.confidence >= CONFIDENT else "low"


def is_confident(t: LineTruth) -> bool:
    return t.status == "confident"


@dataclass(frozen=True)
class Alignment:
    lines: tuple[LineTruth, ...]
    offset_s: float | None  # median (product time - AMI time) over kept anchors
    offset_iqr_s: float | None
    anchors: int  # kept global anchors
    dropped: int  # anchors dropped by the time check
    local: int  # words matched locally


# -- alignment --------------------------------------------------------------------------------


def _running_median(values: Sequence[float], width: int) -> list[float]:
    half = width // 2
    return [statistics.median(values[max(0, i - half) : i + half + 1]) for i in range(len(values))]


def _line_truth(index: int, n_words: int, hits: Sequence[RefWord]) -> LineTruth:
    by_agent = Counter(h.agent for h in hits)
    acts = Counter(h.act for h in hits if h.act >= 0)
    if not by_agent:
        return LineTruth(index, n_words, None, 0.0, False, {}, {})
    ranked = sorted(by_agent.items(), key=lambda kv: (-kv[1], kv[0]))
    top, n_top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    mixed = second >= MIXED_MIN_WORDS and second / len(hits) >= MIXED_SHARE
    return LineTruth(
        index,
        n_words,
        top,
        round(n_top / n_words, 3),
        mixed,
        dict(ranked),
        dict(sorted(acts.items())),
    )


def align(ref: Sequence[RefWord], lines: Sequence[ProdLine]) -> Alignment:
    """True speaker per product line (module docstring). ``ref`` must be sorted by start time.
    Deterministic: same input, same output."""
    owner: list[int] = []  # product token -> position in ``lines``
    ptok: list[str] = []
    ptime: list[float] = []
    counts: list[int] = []
    for pos, ln in enumerate(lines):
        toks = tokens(ln.text)
        counts.append(len(toks))
        span = max(0.0, ln.end - ln.start)
        for k, tok in enumerate(toks):
            owner.append(pos)
            ptok.append(tok)
            ptime.append(ln.start + span * (k + 0.5) / len(toks))
    sm = difflib.SequenceMatcher(None, [r.token for r in ref], ptok, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    pairs = [(b.a + k, b.b + k) for b in blocks for k in range(b.size)]
    long = [b.size >= LONG_BLOCK for b in blocks for _ in range(b.size)]
    diffs = [ptime[p] - ref[r].start for r, p in pairs]
    smooth = _running_median(diffs, SMOOTH)
    # (ref word, product word, local offset): the running median, or the anchor's own offset
    # when only its run length vouches for it
    kept = [
        (r, p, s if abs(d - s) <= TOLERANCE_S else d)
        for (r, p), d, s, run in zip(pairs, diffs, smooth, long, strict=True)
        if abs(d - s) <= TOLERANCE_S or run
    ]
    match: dict[int, int] = {p: r for r, p, _ in kept}  # product token -> ref word
    used = set(match.values())
    by_token: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for i, r in enumerate(ref):
        by_token[r.token].append((r.start, i))
    kept_p = [p for _, p, _ in kept]
    local = 0
    for p, tok in enumerate(ptok):
        if p in match or not kept or tok not in by_token:
            continue
        j = bisect.bisect_left(kept_p, p)  # kept[j-1] is before p, kept[j] after
        prev, nxt = (kept[j - 1] if j else None), (kept[j] if j < len(kept) else None)
        near = min((x for x in (prev, nxt) if x is not None), key=lambda x: abs(x[1] - p))
        expected = ptime[p] - near[2]
        lo = ref[prev[0]].start - MAX_DEV_S if prev else expected - MAX_DEV_S
        hi = ref[nxt[0]].start + MAX_DEV_S if nxt else expected + MAX_DEV_S
        lo, hi = max(lo, expected - MAX_DEV_S), min(hi, expected + MAX_DEV_S)
        cands = by_token[tok]
        best: tuple[float, int] | None = None
        for start, i in cands[bisect.bisect_left(cands, (lo, -1)) :]:
            if start > hi:
                break
            if i not in used and (best is None or abs(start - expected) < best[0]):
                best = (abs(start - expected), i)
        if best is not None:
            match[p] = best[1]
            used.add(best[1])
            local += 1
    hits: list[list[RefWord]] = [[] for _ in lines]
    for p, ri in match.items():
        hits[owner[p]].append(ref[ri])
    kept_diffs = sorted(ptime[p] - ref[r].start for r, p, _ in kept)
    iqr = (
        kept_diffs[(3 * len(kept_diffs)) // 4] - kept_diffs[len(kept_diffs) // 4]
        if kept_diffs
        else None
    )
    return Alignment(
        lines=tuple(
            _line_truth(ln.index, n, h) for ln, n, h in zip(lines, counts, hits, strict=True)
        ),
        offset_s=round(statistics.median(kept_diffs), 2) if kept_diffs else None,
        offset_iqr_s=round(iqr, 2) if iqr is not None else None,
        anchors=len(kept),
        dropped=len(pairs) - len(kept),
        local=local,
    )


def alignment_counts(lines: Iterable[LineTruth]) -> dict[str, int]:
    """Lines per status (confident / low / mixed / unaligned) and in total."""
    c = Counter(t.status for t in lines)
    return {
        "lines": sum(c.values()),
        **{k: c.get(k, 0) for k in ("confident", "low", "mixed", "unaligned")},
    }


# -- attendees and predictions ----------------------------------------------------------------


@dataclass(frozen=True)
class Attendee:
    """One person on the simulated invite. ``label`` is what the generator is told to write."""

    agent: str
    role: str | None  # "Project Manager"; None when AMI gives no role
    name: str | None  # as introduced somewhere in the AMI series; None when never said

    @property
    def label(self) -> str | None:
        return self.name or self.role

    @property
    def listing(self) -> str | None:
        if self.name and self.role:
            return f"{self.name} ({self.role})"
        return self.label


def usable(attendees: Sequence[Attendee]) -> bool:
    """Whether there is anyone to name: at least one attendee has a name or a role."""
    return any(a.label for a in attendees)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def resolve_speaker(value: str, attendees: Sequence[Attendee]) -> str | None:
    """The AMI agent a generator's ``speaker`` value names, or None. Accepted, ignoring case and
    punctuation: an attendee's name, first name, role, or ``Name (Role)``. A value that fits
    two attendees fits none."""
    want = _norm(value)
    if not want or want == UNKNOWN:
        return None
    found = set()
    for a in attendees:
        keys = {_norm(k) for k in (a.name, a.role, a.listing) if k}
        if a.name:
            keys.add(_norm(a.name).split(" ")[0])
        if want in keys:
            found.add(a.agent)
    return next(iter(found)) if len(found) == 1 else None


@dataclass(frozen=True)
class Prediction:
    line: int
    speaker: str  # as written
    confidence: str
    basis: str


_LINE_ID = re.compile(r"^T(\d+)$")


def parse_predictions(text: str, n_lines: int) -> tuple[dict[int, Prediction], list[str]]:
    """A generator's JSONL as line index -> prediction, and what is wrong with it: bad JSON,
    missing keys, values outside :data:`CONFIDENCES` / :data:`BASES`, unknown, repeated or
    missing lines. A bad row is left out; the first row for a line wins."""
    out: dict[int, Prediction] = {}
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
            hit = _LINE_ID.match(str(row["line"]))
            pred = Prediction(
                int(hit.group(1)) if hit else -1,
                str(row["speaker"]),
                str(row["confidence"]),
                str(row["basis"]),
            )
        except (ValueError, KeyError, TypeError) as e:
            errors.append(f"row {n}: {type(e).__name__}: {e}")
            continue
        if not 0 <= pred.line < n_lines:
            errors.append(f"row {n}: no such line {row['line']!r}")
        elif pred.confidence not in CONFIDENCES or pred.basis not in BASES:
            errors.append(f"row {n}: bad confidence or basis")
        elif pred.line in out:
            errors.append(f"row {n}: T{pred.line:04d} repeated")
        else:
            out[pred.line] = pred
    if missing := [i for i in range(n_lines) if i not in out]:
        errors.append(f"{len(missing)} lines without a row (first T{missing[0]:04d})")
    return out, errors


# -- baselines --------------------------------------------------------------------------------


def majority_baseline(truth: Sequence[LineTruth]) -> dict[str, Any]:
    """Every line named as the most talkative attendee (most words on confident lines; the pick
    reads the truth, so this is the ceiling of naming one person throughout)."""
    conf = [t for t in truth if is_confident(t)]
    words = Counter[str]()
    for t in conf:
        words[t.speaker or ""] += t.n_words
    if not words:
        return {"speaker": None, "lines": 0, "correct": 0, "words": 0, "correct_words": 0}
    top = min(words, key=lambda a: (-words[a], a))
    return {
        "speaker": top,
        "lines": len(conf),
        "correct": sum(t.speaker == top for t in conf),
        "words": sum(words.values()),
        "correct_words": words[top],
    }


def label_baseline(
    truth: Sequence[LineTruth], labels: Mapping[int, str], agents: Sequence[str]
) -> dict[str, Any]:
    """The product's own labels turned into people with the truth in hand. ``one_to_one``: the
    assignment of distinct labels to distinct people with most confident lines correct (lines
    under an unassigned label stay unnamed); ``many_to_one``: every label named as the person
    who speaks most of its lines (purity). Both are ceilings for labels alone."""
    conf = [t for t in truth if is_confident(t)]
    table: dict[str, Counter[str]] = defaultdict(Counter)
    for t in conf:
        table[labels[t.index]][t.speaker or ""] += 1
    names = sorted(table)
    best: tuple[int, tuple[str | None, ...]] = (-1, ())
    slots: list[str | None] = [*names, *([None] * len(agents))]
    for perm in sorted(set(itertools.permutations(slots, len(agents))), key=repr):
        got = sum(table[lab][a] for a, lab in zip(agents, perm, strict=True) if lab is not None)
        if got > best[0]:
            best = (got, perm)
    mapping = {lab: a for a, lab in zip(agents, best[1], strict=True) if lab is not None}
    return {
        "labels": len(names),
        "lines": len(conf),
        "one_to_one": {
            "mapping": mapping,
            "named": sum(sum(table[lab].values()) for lab in mapping),
            "correct": max(best[0], 0),
        },
        "many_to_one": {"correct": sum(max(c.values()) for c in table.values())},
    }


# -- action owners ----------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldAction:
    id: str
    text: str
    owner: str | None  # AMI agent of the single owner; None with ``why`` set
    links: tuple[int, ...]  # dialogue-act indexes
    why: str = ""  # why there is no single owner


def action_owner(
    action: GoldAction,
    truth: Sequence[LineTruth],
    act_agent: Mapping[int, str],
    predicted: Mapping[int, str | None],
) -> dict[str, Any]:
    """One gold action scored by the module docstring's rule. ``act_agent``: dialogue-act index
    -> AMI agent; ``predicted``: line index -> the agent the generator named (None: unknown or
    unresolvable). Returns ``kind`` (commit / addressed / ambiguous), the lines used, and for
    scored items ``outcome`` (correct / wrong / unnamed)."""
    base: dict[str, Any] = {"id": action.id, "text": action.text}
    if action.owner is None:
        return {**base, "kind": "ambiguous", "why": action.why or "no single owner"}
    if not action.links:
        return {**base, "kind": "ambiguous", "why": "no linked passage in AMI"}
    linked = set(action.links)
    carrying = [t for t in truth if linked & set(t.acts)]
    commit = [
        t
        for t in carrying
        if is_confident(t)
        and t.speaker == action.owner
        and any(act_agent.get(a) == action.owner for a in linked & set(t.acts))
    ]
    if commit:
        votes: Counter[str] = Counter()
        for t in commit:
            got = predicted.get(t.index)
            votes["unnamed" if got is None else "correct" if got == action.owner else "wrong"] += (
                t.n_words
            )
        worst_first = ("wrong", "unnamed", "correct")
        outcome = max(worst_first, key=lambda k: (votes[k], -worst_first.index(k)))
        return {
            **base,
            "kind": "commit",
            "lines": [t.index for t in commit],
            "outcome": outcome,
        }
    if not carrying:
        return {**base, "kind": "ambiguous", "why": "linked passage not in the product transcript"}
    after = max(linked)
    for t in truth:
        later = [a for a in t.acts if after < a <= after + REPLY_ACTS]
        if later and is_confident(t) and t.speaker == action.owner:
            got = predicted.get(t.index)
            outcome = "unnamed" if got is None else "correct" if got == action.owner else "wrong"
            return {**base, "kind": "addressed", "lines": [t.index], "outcome": outcome}
    return {
        **base,
        "kind": "ambiguous",
        "why": f"owner is addressed and has no confident line in the next {REPLY_ACTS} "
        "dialogue acts",
    }


# -- scoring ----------------------------------------------------------------------------------


def score_unit(
    truth: Sequence[LineTruth],
    labels: Mapping[int, str],
    attendees: Sequence[Attendee],
    preds: Mapping[int, Prediction],
    actions: Sequence[GoldAction] = (),
    act_agent: Mapping[int, str] | None = None,
) -> dict[str, Any]:
    """One unit's counts (lines and words): confident lines, named, correct; the same by the
    generator's confidence and basis; values that name no attendee; the baselines; the action
    owners. A confident line with no prediction row counts as not named."""
    conf = [t for t in truth if is_confident(t)]
    resolved: dict[int, str | None] = {}
    unresolved = 0
    for i, p in preds.items():
        resolved[i] = resolve_speaker(p.speaker, attendees)
        unresolved += resolved[i] is None and _norm(p.speaker) != UNKNOWN
    cells: dict[str, Counter[str]] = defaultdict(Counter)
    for t in conf:
        pred = preds.get(t.index)
        named = pred is not None and _norm(pred.speaker) != UNKNOWN
        right = named and resolved.get(t.index) == t.speaker
        keys = ["all"] + (["min_words"] if t.n_words >= MIN_WORDS else [])
        if pred is not None:
            keys += [f"confidence:{pred.confidence}", f"basis:{pred.basis}"]
        for k in keys:
            c = cells[k]
            c["lines"] += 1
            c["words"] += t.n_words
            c["named"] += named
            c["named_words"] += t.n_words * named
            c["correct"] += right
            c["correct_words"] += t.n_words * right
    agents = [a.agent for a in attendees]
    owners = [action_owner(a, truth, act_agent or {}, resolved) for a in actions]
    return {
        "alignment": alignment_counts(truth),
        "cells": {k: dict(v) for k, v in sorted(cells.items())},
        "unresolved_names": unresolved,
        "majority": majority_baseline(truth),
        "labels": label_baseline(truth, labels, agents),
        "actions": owners,
    }


def _rate(pairs: Sequence[tuple[float, float]], seed: int) -> dict[str, Any]:
    num, den = sum(n for n, _ in pairs), sum(d for _, d in pairs)
    return {
        "num": num,
        "den": den,
        "rate": round(num / den, 4) if den else None,
        "ci": ratio_ci(list(pairs), seed),
    }


def summarise(rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    """Pooled rates over unit rows (each :func:`score_unit` output plus ``meeting``), with
    percentile bootstrap CIs that resample meetings (a meeting's units stay together)."""
    meetings = sorted({r["meeting"] for r in rows})

    def per_meeting(num: Any, den: Any) -> list[tuple[float, float]]:
        return [
            (
                float(sum(num(r) for r in rows if r["meeting"] == m)),
                float(sum(den(r) for r in rows if r["meeting"] == m)),
            )
            for m in meetings
        ]

    def cell(key: str, num: str, den: str) -> dict[str, Any]:
        return _rate(
            per_meeting(
                lambda r: r["cells"].get(key, {}).get(num, 0),
                lambda r: r["cells"].get(key, {}).get(den, 0),
            ),
            seed,
        )

    def acts(r: Mapping[str, Any], *outcomes: str) -> int:
        return sum(a.get("outcome") in outcomes for a in r["actions"])

    keys = sorted({k for r in rows for k in r["cells"] if ":" in k})
    scored = ("correct", "wrong", "unnamed")
    return {
        "meetings": len(meetings),
        "units": len(rows),
        "coverage": cell("all", "named", "lines"),
        "accuracy_when_named": cell("all", "correct", "named"),
        "correct_of_all": cell("all", "correct", "lines"),
        "coverage_words": cell("all", "named_words", "words"),
        "accuracy_when_named_words": cell("all", "correct_words", "named_words"),
        "correct_of_all_words": cell("all", "correct_words", "words"),
        "coverage_min_words": cell("min_words", "named", "lines"),
        "accuracy_when_named_min_words": cell("min_words", "correct", "named"),
        "by": {
            k: {"lines": cell(k, "lines", "lines")["num"], "named": cell(k, "correct", "named")}
            for k in keys
        },
        "unresolved_names": sum(r["unresolved_names"] for r in rows),
        "baseline_majority": _rate(
            per_meeting(lambda r: r["majority"]["correct"], lambda r: r["majority"]["lines"]), seed
        ),
        "baseline_majority_words": _rate(
            per_meeting(lambda r: r["majority"]["correct_words"], lambda r: r["majority"]["words"]),
            seed,
        ),
        "baseline_labels_one_to_one": _rate(
            per_meeting(
                lambda r: r["labels"]["one_to_one"]["correct"], lambda r: r["labels"]["lines"]
            ),
            seed,
        ),
        "baseline_labels_many_to_one": _rate(
            per_meeting(
                lambda r: r["labels"]["many_to_one"]["correct"], lambda r: r["labels"]["lines"]
            ),
            seed,
        ),
        "action_owner_correct": _rate(
            per_meeting(lambda r: acts(r, "correct"), lambda r: acts(r, *scored)), seed
        ),
        "action_owner_wrong": _rate(
            per_meeting(lambda r: acts(r, "wrong"), lambda r: acts(r, *scored)), seed
        ),
        "actions": {
            "gold": sum(len(r["actions"]) for r in rows),
            **{
                k: sum(a["kind"] == k for r in rows for a in r["actions"])
                for k in ("commit", "addressed", "ambiguous")
            },
        },
    }


# -- loading (local AMI annotations and the synced product DBs) -------------------------------


def split_meetings(split: str) -> tuple[str, ...]:
    """DEV is IS1008a; TEST is E4's eleven meetings. Design and examples use DEV only."""
    return DEV_MEETINGS if split == "dev" else TEST_MEETINGS


def jobs_dir(split: str) -> Path:
    return READERS / ("speaker_jobs" if split == "test" else f"speaker_jobs_{split}")


def unit_name(meeting_id: str, product: str) -> str:
    return f"{meeting_id}__{product}"


def ref_words(meeting: Any) -> list[RefWord]:
    """AMI's spoken words (``corpus.AmiMeeting``) as alignment tokens in start-time order, each
    with the index of the dialogue act of the same speaker whose time span holds it."""
    spans: dict[str, list[tuple[float, float, int]]] = defaultdict(list)
    for i, d in enumerate(meeting.dialogue_acts):
        spans[d.agent].append((d.start, d.end, i))
    for v in spans.values():
        v.sort()
    out: list[RefWord] = []
    for w in meeting.spoken():
        own = spans.get(w.agent, [])
        j = bisect.bisect_right(own, (w.start, float("inf"), 0)) - 1
        act = own[j][2] if j >= 0 and w.start <= own[j][1] else -1
        out += [RefWord(w.agent, w.start, tok, act) for tok in tokens(w.text)]
    return out


def attendees_of(meeting: Any) -> list[Attendee]:
    """The simulated invite: each AMI speaker's scenario role and the name they introduce
    themselves by anywhere in the meeting's series (``facts.spoken_names``; participant codes
    are stable across sessions a-d). AMI is anonymised, so a name nobody says does not exist."""
    from tenor.ami.corpus import ROLE_NAMES, load_meeting
    from tenor.ami.facts import spoken_names
    from tenor.evals.owner_check import series_of

    mid = meeting.meeting_id
    series = series_of(mid)
    sessions = [meeting]
    for s in "abcd" if series != mid else "":
        if series + s != mid:
            try:
                sessions.append(load_meeting(series + s))
            except KeyError:
                continue
    names = spoken_names(sorted(sessions, key=lambda m: str(m.meeting_id)))
    return [
        Attendee(s.agent, ROLE_NAMES.get(s.role or ""), names.get(s.participant))
        for s in sorted(meeting.speakers, key=lambda s: str(s.agent))
    ]


def product_lines(note: Any) -> list[ProdLine]:
    """A product note's transcript as lines ``T0000..`` in the packets' numbering (utterances
    in index order), each with the product's own label: the name it gave, else its diarisation
    label (``Speaker 2``), else the audio channel (``Me`` / ``Them``). Times are seconds from
    the first line; a missing time takes the previous line's."""
    from tenor.core.models import Channel

    utts = sorted(note.utterances, key=lambda u: u.index)
    t0 = next((u.start_time for u in utts if u.start_time), None)
    out: list[ProdLine] = []
    last = 0.0
    for i, u in enumerate(utts):
        start = (u.start_time - t0).total_seconds() if u.start_time and t0 else last
        end = (u.end_time - t0).total_seconds() if u.end_time and t0 else start
        last = max(start, end)
        channel = "Me" if u.channel is Channel.ME else "Them"
        label = u.speaker_name or u.diarization_label or channel
        out.append(ProdLine(i, str(label), start, max(start, end), u.text))
    return out


def load_notes(meetings: Sequence[str]) -> dict[tuple[str, str], Any]:
    """(meeting, product) -> the product's note as E3 and the reader packets see it
    (``deletion_ami.reader_notes``: Granola's microphone turns and Wispr's banner dropped)."""
    from tenor.cli import FACTS_DBS, FACTS_OVERRIDES
    from tenor.core.store import read_notes
    from tenor.evals import deletion_ami as DA

    recs = DA.load_recordings(dict(FACTS_DBS), FACTS_OVERRIDES, read_notes)
    out: dict[tuple[str, str], Any] = {}
    for p in PRODUCTS:
        by_id = {(n.title or "").removeprefix("AMI "): n for n in DA.reader_notes(recs[p], p)}
        for m in meetings:
            if m in by_id:
                out[(m, p)] = by_id[m]
    return out


def gold_actions(meeting: Any, errata: Any = None) -> tuple[list[GoldAction], dict[int, str]]:
    """The meeting's gold ACTION items (E3's ids, ``errata`` applied) with their single owner as
    an AMI agent and their summary links as dialogue-act indexes; and act index -> agent."""
    from tenor.evals import notes_completeness as N
    from tenor.evals.owner_check import ROLE_LABEL, gold_owners

    da = {d.da_id: i for i, d in enumerate(meeting.dialogue_acts)}
    links: dict[str, tuple[int, ...]] = {}
    count: dict[str, int] = {}
    for s in meeting.sentences:
        if s.section in N.SECTIONS:
            cat, prefix = N.SECTIONS[s.section]
            count[cat] = count.get(cat, 0) + 1
            links[f"{prefix}{count[cat]}"] = tuple(sorted(da[x] for x in s.links if x in da))
    gold = [g for g in N.gold_items(meeting.sentences, errata, meeting.meeting_id)]
    actions = [g for g in gold if g.category == "action"]
    by_role = {s.role: s.agent for s in meeting.speakers if s.role}
    out = []
    for g, owner in zip(actions, gold_owners(actions).values(), strict=True):
        agent, why = None, ""
        if isinstance(owner, str):
            why = f"gold owner is {owner}"
        elif len(owner) != 1 or next(iter(owner)) not in by_role:
            why = "gold owner is " + " + ".join(sorted(ROLE_LABEL.get(r, r) for r in owner))
        else:
            agent = by_role[next(iter(owner))]
        out.append(GoldAction(g.id, g.text, agent, links.get(g.id, ()), why))
    return out, {i: d.agent for i, d in enumerate(meeting.dialogue_acts)}


# -- files ------------------------------------------------------------------------------------


def truth_payload(
    meeting_id: str,
    product: str,
    attendees: Sequence[Attendee],
    lines: Sequence[ProdLine],
    result: Alignment,
) -> dict[str, Any]:
    """``speaker_truth/<meeting>__<product>.json``: per line the true speaker (AMI agent, role,
    name), confidence, mixed flag, matched words per speaker and per dialogue act."""
    who = {a.agent: a for a in attendees}
    rows = []
    for ln, t in zip(lines, result.lines, strict=True):
        a = who.get(t.speaker or "")
        rows.append(
            {
                "line": f"T{t.index:04d}",
                "label": ln.label,
                "n_words": t.n_words,
                "status": t.status,
                "speaker": t.speaker,
                "role": a.role if a else None,
                "name": a.name if a else None,
                "confidence": t.confidence,
                "mixed": t.mixed,
                "words": dict(t.words),
                "acts": {str(k): v for k, v in t.acts.items()},
            }
        )
    return {
        "meta": {
            "truth_version": TRUTH_VERSION,
            "meeting": meeting_id,
            "product": product,
            "confident_at": CONFIDENT,
            "offset_s": result.offset_s,
            "offset_iqr_s": result.offset_iqr_s,
            "anchors": result.anchors,
            "anchors_dropped": result.dropped,
            "local_matches": result.local,
            **alignment_counts(result.lines),
        },
        "attendees": [
            {"agent": a.agent, "role": a.role, "name": a.name, "label": a.label} for a in attendees
        ],
        "lines": rows,
    }


def read_truth(
    payload: Mapping[str, Any],
) -> tuple[list[LineTruth], dict[int, str], list[Attendee]]:
    """A truth file back as line truths, line index -> product label, and the attendees."""
    truth, labels = [], {}
    for r in payload["lines"]:
        i = int(r["line"][1:])
        labels[i] = r["label"]
        truth.append(
            LineTruth(
                i,
                r["n_words"],
                r["speaker"],
                r["confidence"],
                r["mixed"],
                r["words"],
                {int(k): v for k, v in r["acts"].items()},
            )
        )
    people = [Attendee(a["agent"], a["role"], a["name"]) for a in payload["attendees"]]
    return truth, labels, people


def _mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def attendee_block(attendees: Sequence[Attendee]) -> list[str]:
    """The invite as the generator reads it, in alphabetical order (AMI's speaker letters are
    not shown). Roles only when no name is known; ``unknown`` when there is neither."""
    listed = sorted(a.listing for a in attendees if a.listing)
    if not listed:
        return ["Attendee list: unknown (no invite). Number of people: not given."]
    missing = len(attendees) - len(listed)
    out = [f"Attendee list ({len(attendees)} people):", *(f"- {x}" for x in listed)]
    if missing:
        out.append(f"- ({missing} more, name and role not known)")
    labels = sorted(a.label for a in attendees if a.label)
    out += ["", "Write a speaker exactly as one of: " + "; ".join(labels) + f"; {UNKNOWN}."]
    return out


def job_file(
    meeting_id: str,
    product: str,
    prompt: str,
    attendees: Sequence[Attendee],
    lines: Sequence[ProdLine],
) -> str:
    """One generator job: the prompt, the attendee list, the product transcript with the
    product's own labels. No truth, no AMI transcript, no gold items."""
    name = unit_name(meeting_id, product)
    return "\n".join(
        [
            prompt.rstrip(),
            "",
            "---",
            "",
            f"# Job: {name}",
            "",
            f"Write your answer to `{RUNS_DIR}/<RUN>/{name}.jsonl`: one JSON object per line, "
            f"one for every transcript line T0000 to T{len(lines) - 1:04d} ({len(lines)} lines).",
            "",
            "## Attendees",
            "",
            *attendee_block(attendees),
            "",
            "## Transcript",
            "",
            "```",
            *(
                f"[T{x.index:04d} {x.label} {_mmss(x.start)}] {' '.join(x.text.split())}"
                for x in lines
            ),
            "```",
            "",
        ]
    )
