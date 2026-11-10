"""Came back different: did something agreed in one meeting return in the next with a part
quietly missing?

The idea under test (``data/private/IDEAS_P4_P7.md``, 7.1): a notes app stays silent after a
recurring meeting unless an item agreed last time came back with a part missing, changed or
contradicted and nobody said so; then it shows one line under the note. This module builds the
data for a blind test of that on AMI's scenario series (four meetings a-d by the same four
people) and scores it. No LLM here: a detector agent and three judge agents read files.

Pipeline (file I/O in ``scripts/cbd.py``):

1. :func:`available_pairs`: every consecutive pair X -> Y in one series where both meetings
   have a human transcript and X's human summary has at least one decision or action.
2. :func:`select`: the fixed split. Series with an odd number are DEVELOP candidates, even
   ones TEST candidates. DEVELOP = 6 a->b pairs; TEST = :data:`KNOWN` pairs whose series is
   even, plus seeded draws up to 12 pairs, one series each, mixing a->b, b->c and c->d
   (:data:`TEST_MIX`). Known pairs from an odd series would form a separate group.
3. :func:`job_file`: per pair, prompt :data:`PROMPT`, the roster, X's decisions and actions
   with their human-linked passage, and Y's full human transcript. No annotation of Y.
4. :func:`parse_claims`: the detector's JSONL, validated; :func:`judge_packet`: one blind
   packet per pair (prompt :data:`JUDGE_PROMPT`; no detector confidence).
5. :func:`majority` of three judges per claim; :func:`score`: per group, lines shown per pair,
   the share judged ``true_silent_drop``, the share of pairs with at least one, the same by
   detector confidence, with percentile bootstrap CIs that resample meeting series.

Transcript lines are AMI dialogue acts in time order; line ``L0123`` is
``meeting.dialogue_acts[123]``. Speakers are shown by scenario role, which is stable across a
series (the NXT letter is not).

Known limits: X's items are a human annotator's summary sentences, cleaner than a product's
notes; "true" is three agent judges, not a person; the judge excerpts are picked by key words.
"""

from __future__ import annotations

import json
import random
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from tenor.ami.corpus import ROLE_NAMES, AmiMeeting
from tenor.evals.coverage_e4 import ratio_ci

PROMPT = "came_back_different.v2"
JUDGE_PROMPT = "came_back_judge.v1"
KNOWN = ("IS1008b__IS1008c", "IS1008c__IS1008d")  # FEATURE_TESTS (B): locator, fair wages
N_DEVELOP = 6
N_TEST = 12
TEST_MIX = (("ab", 4), ("bc", 4), ("cd", 4))  # target pairs per kind in TEST, known ones included
ITEM_SECTIONS = (("decisions", "D"), ("actions", "A"))
X_CONTEXT = 3  # lines either side of an annotator-linked line in X's passage
Y_WINDOW = 15  # lines either side of the detector's cited Y lines in a judge packet
KEY_CONTEXT = 2  # lines either side of a key-word hit elsewhere in Y
KEY_MAX_LINES = 80  # cap on key-word lines per claim (hits on the missing part first)
VERDICTS = ("true_silent_drop", "changed_openly", "not_dropped", "item_not_returned", "unclear")
CONFIDENCES = ("high", "medium", "low")
JUDGES = ("a", "b", "c")
GROUPS = ("develop", "test", "known")

Verdict = Literal[
    "true_silent_drop", "changed_openly", "not_dropped", "item_not_returned", "unclear"
]
_SERIES = re.compile(r"^(?P<series>(?:ES|IS|TS)\d{4})(?P<letter>[a-d])$")
_WORDS_FILE = re.compile(r"words/(?P<mid>[A-Z]{2}\d{4}[a-z])\.[A-Z]\.words\.xml$")
_SUMM_FILE = re.compile(r"abstractive/(?P<mid>[A-Z]{2}\d{4}[a-z])\.abssumm\.xml$")
LINE_ID = re.compile(r"^L(\d{4})$")
_TOKEN = re.compile(r"[a-z][a-z'-]+")
# fmt: off
_STOP = frozenset((
    "about", "after", "also", "been", "before", "being", "both", "could", "does", "each",
    "from", "group", "have", "into", "just", "meeting", "more", "most", "much", "next",
    "only", "other", "over", "project", "same", "should", "some", "such", "team", "than",
    "that", "their", "them", "then", "there", "these", "they", "this", "those", "were",
    "what", "when", "which", "while", "will", "with", "would", "your", "decided", "agreed",
    "discussed", "include", "included", "including", "part", "parts", "came", "back", "today",
    "final", "design", "remote", "control", "prototype", "product", "function", "feature",
    "features",
))
# fmt: on


class Pair(BaseModel):
    """Two consecutive meetings of one scenario series."""

    model_config = ConfigDict(frozen=True)

    x: str
    y: str

    @property
    def name(self) -> str:
        return f"{self.x}__{self.y}"

    @property
    def series(self) -> str:
        return self.x[:-1]

    @property
    def kind(self) -> str:
        return self.x[-1] + self.y[-1]

    @property
    def parity(self) -> str:
        return "odd" if int(self.series[2:]) % 2 else "even"


class Selection(BaseModel):
    """The fixed split. ``known`` lists :data:`KNOWN` pairs that exist; ``known_extra`` those
    of them outside TEST (an extra labelled group, empty when their series is even)."""

    model_config = ConfigDict(frozen=True)

    develop: tuple[Pair, ...]
    test: tuple[Pair, ...]
    known: tuple[Pair, ...]
    known_extra: tuple[Pair, ...]

    def all_pairs(self) -> list[Pair]:
        return [*self.develop, *self.test, *self.known_extra]

    def groups(self, pair: Pair) -> list[str]:
        """Every score group the pair counts in (a known pair in TEST counts in both)."""
        out = [g for g, ps in (("develop", self.develop), ("test", self.test)) if pair in ps]
        return [*out, "known"] if pair in self.known else out


def summary_counts(zip_path: Path) -> dict[str, int | None]:
    """Scenario meeting with a human transcript -> number of decision + action sentences in its
    abstractive summary (``None`` if it has no summary). Reads only file names and summaries."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        out: dict[str, int | None] = {
            hit["mid"]: None
            for n in names
            if (hit := _WORDS_FILE.search(n)) and _SERIES.match(hit["mid"])
        }
        for n in names:
            hit = _SUMM_FILE.search(n)
            if not hit or hit["mid"] not in out:
                continue
            root = ET.fromstring(zf.read(n))
            out[hit["mid"]] = sum(
                bool(s.text and s.text.strip())
                for section, _ in ITEM_SECTIONS
                for el in root.findall(section)
                for s in el.iter("sentence")
            )
    return out


def available_pairs(counts: Mapping[str, int | None]) -> list[Pair]:
    """Consecutive pairs (a->b, b->c, c->d) where both meetings have a transcript and X has at
    least one human decision or action. Sorted by name."""
    out = []
    for x in sorted(counts):
        hit = _SERIES.match(x)
        if not hit or hit["letter"] == "d" or not counts[x]:
            continue
        y = hit["series"] + chr(ord(hit["letter"]) + 1)
        if y in counts:
            out.append(Pair(x=x, y=y))
    return out


def select(pairs: Sequence[Pair], seed: int) -> Selection:
    """The fixed split of :func:`available_pairs` (see the module docstring). Deterministic in
    ``seed``; each split draws from its own generator so one cannot move the other."""
    pairs = sorted(pairs, key=lambda p: p.name)
    dev_rng = random.Random(f"{seed}:cbd.develop")
    dev_pool = [p for p in pairs if p.parity == "odd" and p.kind == "ab"]
    develop = sorted(dev_rng.sample(dev_pool, min(N_DEVELOP, len(dev_pool))), key=lambda p: p.name)

    known = [p for p in pairs if p.name in KNOWN]
    test = [p for p in known if p.parity == "even"]
    rng = random.Random(f"{seed}:cbd.test")
    for kind, target in TEST_MIX:
        used = {p.series for p in test}
        pool = [p for p in pairs if p.parity == "even" and p.kind == kind and p.series not in used]
        need = max(0, target - sum(p.kind == kind for p in test))
        test += rng.sample(pool, min(need, len(pool)))
    test = sorted(test[:N_TEST], key=lambda p: p.name)
    return Selection(
        develop=tuple(develop),
        test=tuple(test),
        known=tuple(known),
        known_extra=tuple(p for p in known if p not in test),
    )


class Item(BaseModel):
    """One human-annotated decision or action of meeting X."""

    model_config = ConfigDict(frozen=True)

    item_id: str  # D1.. for decisions, A1.. for actions, in the summary's order
    text: str
    linked: tuple[int, ...]  # indices into X's dialogue acts the annotator linked, sorted


def items(m: AmiMeeting) -> list[Item]:
    """X's decisions then actions, each with the dialogue acts its summary links point at."""
    index = {a.da_id: i for i, a in enumerate(m.dialogue_acts)}
    out = []
    for section, letter in ITEM_SECTIONS:
        sentences = [s for s in m.sentences if s.section == section]
        for k, s in enumerate(sentences, start=1):
            linked = tuple(sorted({index[d] for d in s.links if d in index}))
            out.append(Item(item_id=f"{letter}{k}", text=s.text, linked=linked))
    return out


def line_id(i: int) -> str:
    return f"L{i:04d}"


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def _role(m: AmiMeeting, agent: str) -> str:
    role = next((s.role for s in m.speakers if s.agent == agent), None)
    return ROLE_NAMES.get(role or "", f"Speaker {agent}")


def line(m: AmiMeeting, i: int, prefix: str = "L") -> str:
    """One transcript line: id, start time, role, words."""
    a = m.dialogue_acts[i]
    return f"{prefix}{i:04d} [{_clock(a.start)}] {_role(m, a.agent)}: {a.text}"


def spans(centres: Sequence[int], pad: int, n: int) -> list[tuple[int, int]]:
    """Inclusive index ranges covering each centre +- ``pad`` within ``0..n-1``; touching or
    overlapping ranges are merged."""
    out: list[tuple[int, int]] = []
    for c in sorted(set(centres)):
        lo, hi = max(0, c - pad), min(n - 1, c + pad)
        if out and lo <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def passage(m: AmiMeeting, item: Item) -> str:
    """The item's linked X lines (marked ``*``) with context; gaps shown as ``...``."""
    if not item.linked:
        why = "no links for this sentence" if m.has_summlinks else "this meeting has no links"
        return f"(no passage: the annotator made {why})"
    blocks = []
    for lo, hi in spans(item.linked, X_CONTEXT, len(m.dialogue_acts)):
        blocks.append(
            "\n".join(
                ("* " if i in item.linked else "  ") + line(m, i, "X") for i in range(lo, hi + 1)
            )
        )
    return "\n  ...\n".join(blocks)


def _item_block(m: AmiMeeting, item: Item) -> str:
    return f"### {item.item_id}: {item.text}\n\n```\n{passage(m, item)}\n```"


def roster(x: AmiMeeting, y: AmiMeeting) -> str:
    """The roles present in X and in Y, with AMI's stable participant code."""
    rows = []
    for m in (x, y):
        people = sorted(
            (ROLE_NAMES.get(s.role or "", f"Speaker {s.agent}"), s.participant) for s in m.speakers
        )
        rows.append(f"- {m.meeting_id}: " + "; ".join(f"{r} ({p})" for r, p in people))
    return "\n".join(rows)


def job_file(x: AmiMeeting, y: AmiMeeting, prompt: str) -> str:
    """The detector's whole input for one pair. Same meetings and prompt -> same bytes."""
    pair = Pair(x=x.meeting_id, y=y.meeting_id)
    parts = [
        f"# Came back different: {pair.x} -> {pair.y}",
        f"Prompt `{PROMPT}`. Write your answer (JSONL, possibly empty) to "
        f"`data/private/reader/cbd_agent_runs/<RUN>/{pair.name}.jsonl`. Use only this file.",
        prompt.strip(),
        "---",
        "## Roster\n\nSpeakers are named by role; the same role is the same person in both "
        f"meetings.\n\n{roster(x, y)}",
        f"## Meeting X ({pair.x}): decisions and actions recorded by a human annotator\n\n"
        "Lines marked `*` are the ones the annotator linked to the item; the others are "
        "context. X line ids start with `X`.",
        *(_item_block(x, it) for it in items(x)),
        f"## Meeting Y ({pair.y}): full transcript\n\n```\n"
        + "\n".join(line(y, i) for i in range(len(y.dialogue_acts)))
        + "\n```",
    ]
    return "\n\n".join(parts) + "\n"


class Claim(BaseModel):
    """One detector line, as the prompt's output format defines it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    x_item: str
    parts: tuple[str, ...]
    came_back: tuple[str, ...]
    missing_or_changed: tuple[str, ...]
    y_lines: tuple[str, ...]
    x_quote: str
    line_for_user: str
    confidence: Literal["high", "medium", "low"]

    @field_validator("missing_or_changed", "y_lines")
    @classmethod
    def _non_empty(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("must not be empty")
        return v

    def y_indices(self) -> list[int]:
        return sorted({int(hit[1]) for s in self.y_lines if (hit := LINE_ID.match(s))})


def claim_id(pair: str, k: int) -> str:
    """Id of the ``k``-th (1-based) valid detector line of a pair."""
    return f"{pair}#{k}"


def parse_claims(
    text: str, item_ids: Sequence[str], n_y_lines: int
) -> tuple[list[Claim], list[str]]:
    """Valid detector lines and one message per rejected line. A line is rejected if it is not
    the output format, names an unknown X item or Y line, or repeats an X item."""
    claims: list[Claim] = []
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            c = Claim.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            errors.append(f"line {n}: {str(e).splitlines()[0]}")
            continue
        bad = [s for s in c.y_lines if not (h := LINE_ID.match(s)) or int(h[1]) >= n_y_lines]
        if c.x_item not in item_ids:
            errors.append(f"line {n}: unknown x_item {c.x_item!r}")
        elif bad:
            errors.append(f"line {n}: unknown y_lines {bad}")
        elif any(c.x_item == d.x_item for d in claims):
            errors.append(f"line {n}: second line for {c.x_item}")
        else:
            claims.append(c)
    return claims, errors


def _stem(token: str) -> str:
    for suffix in ("ing", "es", "ed", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def key_words(text: str) -> set[str]:
    """Crude content words of a text: lower-cased, 4+ letters, stop words out, suffix-stripped."""
    return {_stem(t) for t in _TOKEN.findall(text.lower()) if len(t) >= 4 and t not in _STOP}


def key_word_lines(y: AmiMeeting, item_text: str, claim: Claim, shown: set[int]) -> list[int]:
    """Y lines outside ``shown`` that mention the item: any key word of a missing or changed
    part, or two or more key words of the item text and its other parts. At most
    :data:`KEY_MAX_LINES`, hits on the missing part first, then earlier lines first."""
    strong = key_words(" ".join(claim.missing_or_changed))
    weak = key_words(" ".join((item_text, *claim.parts))) - strong
    hits: list[tuple[int, int]] = []
    for i, a in enumerate(y.dialogue_acts):
        if i in shown:
            continue
        words = key_words(a.text)
        if words & strong:
            hits.append((0, i))
        elif len(words & weak) >= 2:
            hits.append((1, i))
    return sorted(i for _, i in sorted(hits)[:KEY_MAX_LINES])


def _excerpt(y: AmiMeeting, ranges: Sequence[tuple[int, int]], marked: set[int]) -> str:
    return "\n  ...\n".join(
        "\n".join(("> " if i in marked else "  ") + line(y, i) for i in range(lo, hi + 1))
        for lo, hi in ranges
    )


def _claim_block(x: AmiMeeting, y: AmiMeeting, item: Item, cid: str, c: Claim) -> str:
    n = len(y.dialogue_acts)
    cited = c.y_indices()
    window = spans(cited, Y_WINDOW, n)
    shown = {i for lo, hi in window for i in range(lo, hi + 1)}
    hits = key_word_lines(y, item.text, c, shown)
    other = spans(hits, KEY_CONTEXT, n)
    claim = {
        "parts": list(c.parts),
        "came_back": list(c.came_back),
        "missing_or_changed": list(c.missing_or_changed),
        "y_lines": list(c.y_lines),
        "x_quote": c.x_quote,
        "line_for_user": c.line_for_user,
    }
    return "\n\n".join(
        [
            f"## Claim `{cid}`",
            f"### X item {item.item_id}: {item.text}\n\n```\n{passage(x, item)}\n```",
            "### The claim\n\n```json\n"
            + json.dumps(claim, indent=1, ensure_ascii=False)
            + "\n```",
            f"### Y lines cited (`>`), with {Y_WINDOW} lines either side\n\n```\n"
            + _excerpt(y, window, set(cited))
            + "\n```",
            "### Other Y passages sharing key words with the item (`>` = the line that matched)"
            + ("\n\n```\n" + _excerpt(y, other, set(hits)) + "\n```" if other else "\n\n(none)"),
        ]
    )


def judge_packet(
    x: AmiMeeting, y: AmiMeeting, claims: Sequence[Claim], prompt: str, judge_dirs: Sequence[str]
) -> str:
    """One pair's blind packet: every claim with its X item and passage, the cited Y lines in
    context and key-word passages from the rest of Y. The detector's confidence is left out.
    ``judge_dirs`` are the directories the judges answer into, named in the header."""
    pair = Pair(x=x.meeting_id, y=y.meeting_id)
    by_id = {it.item_id: it for it in items(x)}
    head = [
        f"# Came back different, judge packet: {pair.x} -> {pair.y}",
        f"Prompt `{JUDGE_PROMPT}`. {len(claims)} claim(s). Write one JSONL line per claim to "
        f"`<your judge directory>/{pair.name}.jsonl` (one of: {', '.join(judge_dirs)}). The "
        f"full transcript of {pair.y} is the last section of "
        f"`data/private/reader/cbd_jobs/{pair.name}.md`.",
        prompt.strip(),
        "---",
        f"## Roster\n\n{roster(x, y)}",
    ]
    blocks = [
        _claim_block(x, y, by_id[c.x_item], claim_id(pair.name, k), c)
        for k, c in enumerate(claims, start=1)
    ]
    return "\n\n".join([*head, *blocks]) + "\n"


class JudgeLine(BaseModel):
    """One judge's answer to one claim."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    claim_id: str
    verdict: Verdict
    reason: str = ""


def parse_verdicts(text: str) -> tuple[dict[str, str], list[str]]:
    """claim id -> verdict from one judge's JSONL, and a message per unreadable or repeated
    line (the first answer to a claim is kept)."""
    out: dict[str, str] = {}
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            j = JudgeLine.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            errors.append(f"line {n}: {str(e).splitlines()[0]}")
            continue
        if j.claim_id in out:
            errors.append(f"line {n}: second verdict for {j.claim_id}")
        else:
            out[j.claim_id] = j.verdict
    return out, errors


def majority(votes: Sequence[str]) -> str:
    """The verdict at least two of three judges gave; ``unclear`` when all three differ."""
    if len(votes) != len(JUDGES):
        raise ValueError(f"need {len(JUDGES)} votes, got {len(votes)}")
    verdict, n = Counter(votes).most_common(1)[0]
    return verdict if n >= 2 else "unclear"


def pair_row(
    pair: Pair, groups: Sequence[str], judged: Sequence[tuple[str, str]]
) -> dict[str, Any]:
    """One pair's score row from its claims' ``(confidence, majority verdict)``."""
    row: dict[str, Any] = {
        "pair": pair.name,
        "series": pair.series,
        "kind": pair.kind,
        "groups": list(groups),
        "one": 1,
        "lines": len(judged),
        "any_true": int(any(v == "true_silent_drop" for _, v in judged)),
    }
    for v in VERDICTS:
        row[v] = sum(w == v for _, w in judged)
    for c in CONFIDENCES:
        row[f"lines_{c}"] = sum(k == c for k, _ in judged)
        row[f"true_{c}"] = sum(k == c and v == "true_silent_drop" for k, v in judged)
    return row


def _rate(rows: Sequence[Mapping[str, Any]], num: str, den: str, seed: int) -> dict[str, Any]:
    """Pooled ``sum(num) / sum(den)`` with a CI that resamples meeting series."""
    by: dict[str, list[float]] = {}
    for r in rows:
        cell = by.setdefault(r["series"], [0.0, 0.0])
        cell[0] += float(r[num])
        cell[1] += float(r[den])
    clusters = [(a, b) for _, (a, b) in sorted(by.items())]
    a, b = sum(x for x, _ in clusters), sum(y for _, y in clusters)
    ci = ratio_ci(clusters, seed)
    return {
        "num": int(a),
        "den": int(b),
        "value": round(a / b, 4) if b else None,
        "ci": list(ci) if ci else None,
    }


def summarise(rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    """One group's measures over its pair rows."""
    return {
        "pairs": len(rows),
        "series": len({r["series"] for r in rows}),
        "lines_per_pair": _rate(rows, "lines", "one", seed),
        "share_true_silent_drop": _rate(rows, "true_silent_drop", "lines", seed),
        "share_pairs_with_true": _rate(rows, "any_true", "one", seed),
        "share_changed_openly": _rate(rows, "changed_openly", "lines", seed),
        "verdicts": {v: sum(r[v] for r in rows) for v in VERDICTS},
        "by_confidence": {c: _rate(rows, f"true_{c}", f"lines_{c}", seed) for c in CONFIDENCES},
    }


def score(rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    """Every group's :func:`summarise` (groups with no scored pair left out)."""
    out = {}
    for g in GROUPS:
        mine = [r for r in rows if g in r["groups"]]
        if mine:
            out[g] = summarise(mine, seed)
    return out
