"""Item fates: what happens in the next meeting to every decision and action of this one?

``tenor.evals.came_back`` tested a narrow detector (flag only items that return with a part
quietly missing) and found it silent in 9 of 12 pairs. This module measures the base rates
behind that: for a larger sample of consecutive AMI scenario pairs X -> Y, two reader agents
classify the fate in Y of **every** human-annotated decision and action of X, a third reader
breaks ties, and the score reports how often each fate occurs. No LLM here: readers are agents
that read job files (file I/O in ``scripts/fates.py``).

1. :func:`select`: :data:`MIX` pairs per position (a->b, b->c, c->d) drawn with a seed from
   ``came_back.available_pairs``, at most :data:`MAX_PER_SERIES` per series, without the pairs
   the earlier detector was tuned on and without pairs whose X has only placeholder items
   (:func:`items`).
2. :func:`job_file`: prompt :data:`PROMPT`, roster, each X item (id, kind, text, linked passage
   with context) and Y's full human transcript. No annotation of Y. With ``only`` it is the
   tie-break job: the same file restricted to some items, with no reader's answer in it.
3. :func:`parse_answers`: one reader's JSONL, validated. :func:`item_rows`: per item, the final
   fate = what readers a and b agree on, else reader c's answer, else ``unclear`` (the same
   rule, separately, for ``needed_followup``).
4. :func:`score`: distribution of fates overall, by item kind and by pair position, the same
   among items with ``needed_followup`` = yes, silent items per pair, reader a-b agreement and
   Cohen's kappa, and the earlier detector's recall on ``changed_silently`` items; percentile
   bootstrap CIs that resample meeting series.

Known limits: X's items are a human annotator's summary sentences, cleaner than a product's
notes; fates are agent readers' judgements, not a person's; reader c sees only disputed items,
so an item a and b agree on wrongly stays wrong; AMI scenario meetings are role-played design
meetings by four people, one project per series.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from tenor.ami.corpus import AmiMeeting
from tenor.evals import came_back as K
from tenor.evals.coverage_e4 import ratio_ci

PROMPT = "item_fates.v1"
MIX = (("ab", 12), ("bc", 12), ("cd", 12))  # pairs per position
MAX_PER_SERIES = 2
FATES = (
    "reported",
    "returned_unchanged",
    "changed_openly",
    "changed_silently",
    "not_mentioned",
    "not_expected",
)
UNCLEAR = "unclear"  # readers a and b differ and reader c gave no answer
READERS = ("a", "b")
TIEBREAK = "c"
ITEM_KINDS = {"D": "decision", "A": "action"}
SILENT = ("changed_silently", "not_mentioned_needed", "silent_any")
N_BOOT = 2000

_PLACEHOLDER = re.compile(r"^\W*NA\W*$")
_PAIR_NAME = re.compile(
    r"^(?P<sx>(?:ES|IS|TS)\d{4})(?P<lx>[a-d])__(?P<sy>(?:ES|IS|TS)\d{4})(?P<ly>[a-d])$"
)

Fate = Literal[
    "reported",
    "returned_unchanged",
    "changed_openly",
    "changed_silently",
    "not_mentioned",
    "not_expected",
]


def select(pairs: Sequence[K.Pair], exclude: Sequence[K.Pair], seed: int) -> list[K.Pair]:
    """:data:`MIX` pairs per position from ``pairs`` minus ``exclude``, at most
    :data:`MAX_PER_SERIES` per series: one seeded shuffle, then first fit. Sorted by name.
    Deterministic in ``seed`` and independent of input order; shorter than the target only if
    the pool cannot fill a position."""
    pool = sorted(set(pairs) - set(exclude), key=lambda p: p.name)
    random.Random(f"{seed}:fates.select").shuffle(pool)
    want = dict(MIX)
    per_series = Counter[str]()
    out: list[K.Pair] = []
    for p in pool:
        if want.get(p.kind, 0) > 0 and per_series[p.series] < MAX_PER_SERIES:
            want[p.kind] -= 1
            per_series[p.series] += 1
            out.append(p)
    return sorted(out, key=lambda p: p.name)


def parse_pairs(spec: str) -> list[K.Pair]:
    """Pairs named on the command line (``X__Y,X__Y``), in the order given, for a run outside
    the seeded selection. Raises ``ValueError`` for a name that is not two consecutive
    meetings of one scenario series (a->b, b->c or c->d) or that is given twice."""
    out: list[K.Pair] = []
    for name in (s.strip() for s in spec.split(",")):
        hit = _PAIR_NAME.match(name)
        if not hit or hit["sx"] != hit["sy"] or ord(hit["ly"]) - ord(hit["lx"]) != 1:
            raise ValueError(f"{name!r} is not a consecutive pair such as IS1008a__IS1008b")
        pair = K.Pair(x=hit["sx"] + hit["lx"], y=hit["sy"] + hit["ly"])
        if pair in out:
            raise ValueError(f"{name!r} is given twice")
        out.append(pair)
    return out


def items(m: AmiMeeting) -> list[K.Item]:
    """``came_back.items`` (same ids) without the annotator's "nothing to record" placeholder
    sentences ("NA.", "*NA*"), which are not decisions or actions."""
    return [it for it in K.items(m) if not _PLACEHOLDER.match(it.text)]


def item_kind(item_id: str) -> str:
    """``decision`` or ``action`` from an item id (D1, A2, ...)."""
    return ITEM_KINDS[item_id[0]]


def _item_block(m: AmiMeeting, item: K.Item) -> str:
    return (
        f"### {item.item_id} ({item_kind(item.item_id)}): {item.text}\n\n"
        f"```\n{K.passage(m, item)}\n```"
    )


def job_file(
    x: AmiMeeting, y: AmiMeeting, prompt: str, answer_path: str, only: Sequence[str] | None = None
) -> str:
    """A reader's whole input for one pair; with ``only``, restricted to those X items (the
    tie-break job). Same meetings, prompt and arguments -> same bytes."""
    pair = K.Pair(x=x.meeting_id, y=y.meeting_id)
    every = items(x)
    shown = [it for it in every if only is None or it.item_id in only]
    scope = (
        f"all {len(every)} items"
        if only is None
        else f"only the {len(shown)} of {len(every)} items listed below"
    )
    parts = [
        f"# Item fates: {pair.x} -> {pair.y}",
        f"Prompt `{PROMPT}`. Classify {scope}: {', '.join(it.item_id for it in shown)}. Write "
        f"your answer (JSONL, one line per item) to `{answer_path}`. Use only this file.",
        prompt.strip(),
        "---",
        "## Roster\n\nSpeakers are named by role; the same role is the same person in both "
        f"meetings.\n\n{K.roster(x, y)}",
        f"## Meeting X ({pair.x}): decisions and actions recorded by a human annotator\n\n"
        f"Lines marked `*` are the ones the annotator linked to the item; the others are "
        f"context ({K.X_CONTEXT} lines either side). X line ids start with `X`.",
        *(_item_block(x, it) for it in shown),
        f"## Meeting Y ({pair.y}): full transcript\n\n```\n"
        + "\n".join(K.line(y, i) for i in range(len(y.dialogue_acts)))
        + "\n```",
    ]
    return "\n\n".join(parts) + "\n"


class Answer(BaseModel):
    """One reader line, as the prompt's output format defines it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    x_item: str
    fate: Fate
    needed_followup: Literal["yes", "no"]
    y_lines: tuple[str, ...] = ()
    what_changed: str = ""
    reason: str = ""
    confidence: Literal["high", "medium", "low"]

    @field_validator("needed_followup", mode="before")
    @classmethod
    def _yes_no(cls, v: object) -> object:
        if isinstance(v, bool):
            return "yes" if v else "no"
        return v.strip().lower() if isinstance(v, str) else v


def parse_answers(
    text: str, item_ids: Sequence[str], n_y_lines: int
) -> tuple[dict[str, Answer], list[str]]:
    """item id -> the reader's valid answer, and one message per problem. A line is rejected if
    it is not the output format, names an unknown X item or Y line, or repeats an X item (the
    first answer is kept). A ``changed_*`` answer that does not say what changed is kept and
    reported."""
    out: dict[str, Answer] = {}
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            a = Answer.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            errors.append(f"line {n}: {str(e).splitlines()[0]}")
            continue
        bad = [s for s in a.y_lines if not (h := K.LINE_ID.match(s)) or int(h[1]) >= n_y_lines]
        if a.x_item not in item_ids:
            errors.append(f"line {n}: unknown x_item {a.x_item!r}")
        elif bad:
            errors.append(f"line {n}: unknown y_lines {bad}")
        elif a.x_item in out:
            errors.append(f"line {n}: second line for {a.x_item}")
        else:
            out[a.x_item] = a
            if a.fate.startswith("changed_") and not a.what_changed.strip():
                errors.append(f"line {n}: {a.x_item} is {a.fate} with empty what_changed (kept)")
    return out, errors


def disputed(
    item_ids: Sequence[str], a: Mapping[str, Answer], b: Mapping[str, Answer]
) -> list[str]:
    """Items on which readers a and b do not give the same fate (an item either reader left
    out counts), in the order given."""
    return [i for i in item_ids if i not in a or i not in b or a[i].fate != b[i].fate]


def final(a: str | None, b: str | None, c: str | None) -> str:
    """What a and b agree on, else c's answer, else ``unclear``."""
    if a is not None and a == b:
        return a
    return c if c is not None else UNCLEAR


def item_rows(
    pair: K.Pair,
    item_ids: Sequence[str],
    answers: Mapping[str, Mapping[str, Answer]],
) -> list[dict[str, Any]]:
    """One score row per X item from the readers' answers (``answers[reader][item]``)."""
    rows = []
    for i in item_ids:
        got = {r: answers.get(r, {}).get(i) for r in (*READERS, TIEBREAK)}
        fates = {r: (g.fate if g else None) for r, g in got.items()}
        needs = {r: (g.needed_followup if g else None) for r, g in got.items()}
        rows.append(
            {
                "pair": pair.name,
                "series": pair.series,
                "position": pair.kind,
                "x_item": i,
                "item_kind": item_kind(i),
                **{f"fate_{r}": f for r, f in fates.items()},
                **{f"needed_{r}": f for r, f in needs.items()},
                "fate": final(fates["a"], fates["b"], fates["c"]),
                "needed_followup": final(needs["a"], needs["b"], needs["c"]),
                "y_lines": {r: list(g.y_lines) for r, g in got.items() if g},
                "what_changed": {r: g.what_changed for r, g in got.items() if g and g.what_changed},
            }
        )
    return rows


Row = Mapping[str, Any]


def _rate(rows: Sequence[Row], num: Callable[[Row], bool], seed: int) -> dict[str, Any]:
    """Share of ``rows`` where ``num`` holds, with a CI that resamples meeting series."""
    by: dict[str, list[float]] = {}
    for r in rows:
        cell = by.setdefault(r["series"], [0.0, 0.0])
        cell[0] += float(num(r))
        cell[1] += 1.0
    clusters = [(a, b) for _, (a, b) in sorted(by.items())]
    a, b = sum(x for x, _ in clusters), sum(y for _, y in clusters)
    ci = ratio_ci(clusters, seed, n=N_BOOT)
    return {
        "num": int(a),
        "den": int(b),
        "value": round(a / b, 4) if b else None,
        "ci": list(ci) if ci else None,
    }


def distribution(rows: Sequence[Row], seed: int) -> dict[str, Any]:
    """Share of each fate (and ``unclear``) among ``rows``."""
    return {
        "items": len(rows),
        "pairs": len({r["pair"] for r in rows}),
        "series": len({r["series"] for r in rows}),
        "fates": {
            f: _rate(rows, lambda r, f=f: r["fate"] == f, seed)  # type: ignore[misc]
            for f in (*FATES, UNCLEAR)
        },
    }


def breakdown(rows: Sequence[Row], seed: int) -> dict[str, Any]:
    """:func:`distribution` overall, by item kind and by pair position."""
    return {
        "overall": distribution(rows, seed),
        "by_item_kind": {
            k: distribution([r for r in rows if r["item_kind"] == k], seed)
            for k in ITEM_KINDS.values()
        },
        "by_position": {
            k: distribution([r for r in rows if r["position"] == k], seed) for k, _ in MIX
        },
    }


def pair_rows(rows: Sequence[Row]) -> list[dict[str, Any]]:
    """Per meeting pair: items, and how many fall in each silent category
    (``changed_silently``; ``not_mentioned`` with ``needed_followup`` = yes; either)."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        p = out.setdefault(
            r["pair"],
            {"pair": r["pair"], "series": r["series"], "position": r["position"], "items": 0}
            | dict.fromkeys(SILENT, 0),
        )
        p["items"] += 1
        cs = r["fate"] == "changed_silently"
        nm = r["fate"] == "not_mentioned" and r["needed_followup"] == "yes"
        p["changed_silently"] += cs
        p["not_mentioned_needed"] += nm
        p["silent_any"] += cs or nm
    return [out[k] for k in sorted(out)]


def pair_shares(pairs: Sequence[Row], seed: int) -> dict[str, Any]:
    """Per silent category: items per pair, and the share of pairs with >= 1 and >= 2."""
    out: dict[str, Any] = {}
    for cat in SILENT:
        counts = [int(p[cat]) for p in pairs]
        out[cat] = {
            "items": sum(counts),
            "per_pair_mean": round(sum(counts) / len(counts), 4) if counts else None,
            "pairs_with_at_least_1": _rate(pairs, lambda p, c=cat: p[c] >= 1, seed),  # type: ignore[misc]
            "pairs_with_at_least_2": _rate(pairs, lambda p, c=cat: p[c] >= 2, seed),  # type: ignore[misc]
        }
    return out


def kappa(labels: Sequence[tuple[str, str]]) -> float | None:
    """Cohen's kappa of two raters' labels; ``None`` when undefined (no items, or expected
    agreement 1)."""
    n = len(labels)
    if not n:
        return None
    po = sum(a == b for a, b in labels) / n
    ca, cb = Counter(a for a, _ in labels), Counter(b for _, b in labels)
    pe = sum(ca[k] * cb[k] for k in ca) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)


def _kappa_ci(
    clusters: Sequence[Sequence[tuple[str, str]]], seed: int, alpha: float = 0.05
) -> list[float] | None:
    """Percentile CI of :func:`kappa`, resampling clusters (series) with replacement."""
    rng = random.Random(f"{seed}:fates.kappa:{len(clusters)}")
    stats = []
    for _ in range(N_BOOT):
        draw = [x for c in rng.choices(clusters, k=len(clusters)) for x in c]
        k = kappa(draw)
        if k is not None:
            stats.append(k)
    if not stats:
        return None
    stats.sort()
    m = len(stats)
    return [
        round(stats[int(alpha / 2 * m)], 4),
        round(stats[min(m - 1, int((1 - alpha / 2) * m))], 4),
    ]


def agreement(rows: Sequence[Row], field: str, seed: int) -> dict[str, Any]:
    """Readers a and b on ``field`` (``fate`` or ``needed``): raw agreement, Cohen's kappa and
    the confusion counts, over items both answered."""
    both = [r for r in rows if r[f"{field}_a"] is not None and r[f"{field}_b"] is not None]
    by: dict[str, list[tuple[str, str]]] = {}
    for r in both:
        by.setdefault(r["series"], []).append((r[f"{field}_a"], r[f"{field}_b"]))
    labels = [x for s in sorted(by) for x in by[s]]
    k = kappa(labels)
    return {
        "items_both_answered": len(both),
        "items_missing_a_reader": len(rows) - len(both),
        "agreement": _rate(both, lambda r: r[f"{field}_a"] == r[f"{field}_b"], seed),
        "kappa": round(k, 4) if k is not None else None,
        "kappa_ci": _kappa_ci([by[s] for s in sorted(by)], seed) if labels else None,
        "confusion": {f"{a}|{b}": n for (a, b), n in sorted(Counter(labels).items())},
    }


def detector_recall(
    rows: Sequence[Row], flagged: Mapping[str, Sequence[str]], seed: int
) -> dict[str, Any]:
    """The earlier detector against these fates, on pairs it ran on (``flagged``: pair -> the X
    items it output a line for): how many ``changed_silently`` items it flagged, and the final
    fate of every item it flagged."""
    mine = [r for r in rows if r["pair"] in flagged]
    silent = [r for r in mine if r["fate"] == "changed_silently"]
    return {
        "pairs": sorted({r["pair"] for r in mine}),
        "recall": _rate(silent, lambda r: r["x_item"] in flagged[r["pair"]], seed),
        "changed_silently_items": [
            {"pair": r["pair"], "x_item": r["x_item"], "flagged": r["x_item"] in flagged[r["pair"]]}
            for r in silent
        ],
        "detector_lines": [
            {"pair": r["pair"], "x_item": r["x_item"], "fate": r["fate"]}
            for r in mine
            if r["x_item"] in flagged[r["pair"]]
        ],
    }


def score(
    rows: Sequence[Row], seed: int, flagged: Mapping[str, Sequence[str]] | None = None
) -> dict[str, Any]:
    """Every measure of the module docstring over the item rows of :func:`item_rows`."""
    pairs = pair_rows(rows)
    resolved = Counter(
        "agree" if r["fate_a"] is not None and r["fate_a"] == r["fate_b"]
        else ("tiebreak" if r["fate_c"] is not None else UNCLEAR)
        for r in rows
    )  # fmt: skip
    return {
        "items": len(rows),
        "pairs": len(pairs),
        "series": len({r["series"] for r in rows}),
        "fate_resolved_by": dict(sorted(resolved.items())),
        "needed_followup": dict(sorted(Counter(r["needed_followup"] for r in rows).items())),
        "all_items": breakdown(rows, seed),
        "needed_followup_yes": breakdown([r for r in rows if r["needed_followup"] == "yes"], seed),
        "silent_per_pair": pair_shares(pairs, seed),
        "silent_per_pair_by_position": {
            k: pair_shares([p for p in pairs if p["position"] == k], seed) for k, _ in MIX
        },
        "reader_agreement": {
            "fate": agreement(rows, "fate", seed),
            "needed_followup": agreement(rows, "needed", seed),
        },
        "detector_recall": detector_recall(rows, flagged, seed) if flagged is not None else None,
    }
