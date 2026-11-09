"""E4 scored by Claude Code reader agents instead of the API judge (``prompts/e4_reader.v1.md``).

The API judge was shown to be unreliable for this kind of reading (E3: 74% / kappa 0.55 against
a human-validated second reader, against 90% / kappa 0.82 for a Claude Code reader agent), so
E4's candidates (``coverage-e4 --generate-only``) are read by agents, one packet per unit.

* :func:`pool`: every condition's items for one unit, exact duplicates (after
  ``list_check.normalize``) merged, shuffled with a seed and the unit key, ids ``K1``..; the
  conditions never reach the packet (:func:`candidates_section`), only the private key.
* :class:`ReaderLine`: one verdict line of the reader's JSONL, validated.
* :func:`score`: per condition, per product and pooled: items shown, true new omissions,
  duplicates of the notes, false items, and missing gold recovered (gold the notes miss per the
  E3 reader run), with bootstrap CIs resampling meetings.

Pure functions: no LLM, no network. Same inputs, same outputs.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tenor.evals import coverage_e4 as C4
from tenor.evals import list_check as E

READER_PROMPT = "e4_reader.v1"
PACKET_VERSION = "e4_reader_packet.v1"
KEY_VERSION = "e4_reader_key.v1"
CONDITIONS = (C4.C3, C4.CU, C4.C3S, C4.CUS, C4.R, C4.RD, C4.Q)  # every shown condition
PRODUCTS = ("granola", "wispr")
GROUPS = (*PRODUCTS, "both")

Kind = Literal["decision", "action", "problem", "other"]
TrueV = Literal["yes", "partly", "no"]
InNotes = Literal["yes", "partial", "no"]
Confidence = Literal["high", "medium", "low"]


# -- the pool and the key ---------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    condition: str
    src: str  # "cov:I3", "regen:R5", "chat:2"
    rank: int  # 0-based position in the condition's display order


@dataclass(frozen=True)
class PoolItem:
    id: str  # K1..
    text: str
    sources: tuple[Source, ...]


def pool(
    conditions: Mapping[str, Sequence[Mapping[str, str]]], seed: int, key: str
) -> list[PoolItem]:
    """One unit's candidates: the union of every condition's items, texts equal after
    ``normalize`` merged (the first text seen, in :data:`CONDITIONS` order, is shown), ids
    ``K1``.. in an order shuffled by ``seed`` and the unit ``key`` (blind to the condition).
    Empty texts are dropped."""
    by: dict[str, tuple[str, list[Source]]] = {}
    for cond in CONDITIONS:
        for rank, it in enumerate(conditions.get(cond, ())):
            k = E.normalize(it["text"])
            if k:
                by.setdefault(k, (it["text"].strip(), []))[1].append(Source(cond, it["src"], rank))
    keys = sorted(by)
    random.Random(f"{seed}:e4.reader:{key}").shuffle(keys)
    return [PoolItem(f"K{n}", by[k][0], tuple(by[k][1])) for n, k in enumerate(keys, 1)]


def shown(items: Sequence[PoolItem]) -> dict[str, list[str]]:
    """Condition -> K ids in its display order (a condition's own duplicates once)."""
    out: dict[str, list[tuple[int, str]]] = {}
    for it in items:
        for s in it.sources:
            out.setdefault(s.condition, []).append((s.rank, it.id))
    return {c: list(dict.fromkeys(i for _, i in sorted(out[c]))) for c in CONDITIONS if c in out}


def key_unit(
    record: Mapping[str, Any],
    seed: int,
    bullet_ids: Sequence[str],
    gold_ids: Sequence[str],
) -> dict[str, Any]:
    """The private key for one unit of a ``.candidates.json`` record: K ids -> text and
    sources, each condition's K ids in order, which conditions exist (Q: Granola with a Chat
    answer), and the ids a verdict may cite."""
    unit = f"{record['meeting_id']}:{record['product']}"
    items = pool(record["conditions"], seed, unit)
    return {
        "meeting_id": record["meeting_id"],
        "product": record["product"],
        "conditions": [c for c in CONDITIONS if c in record["conditions"]],
        "bullet_ids": list(bullet_ids),
        "gold_ids": list(gold_ids),
        "items": {
            it.id: {
                "text": it.text,
                "sources": [
                    {"condition": s.condition, "src": s.src, "rank": s.rank} for s in it.sources
                ],
            }
            for it in items
        },
        "shown": shown(items),
    }


def candidates_section(items: Sequence[PoolItem] | Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Section 6 of a packet: the candidate items, ids and texts only (no condition)."""
    rows = (
        [(k, v["text"]) for k, v in items.items()]
        if isinstance(items, Mapping)
        else [(it.id, it.text) for it in items]
    )
    rows.sort(key=lambda r: int(r[0][1:]))
    out = [
        "## 6. Candidate items to score",
        "",
        "Items that might be missing from the notes, in random order. Each is one line `K<n>. "
        "text`. Score every one.",
        "",
    ]
    out += [f"{k}. {' '.join(t.split())}" for k, t in rows] or ["(no candidate items)"]
    return [*out, ""]


# -- the reader's verdicts --------------------------------------------------------------------


class GoldMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    match: Literal["full", "partial"]


class ReaderLine(BaseModel):
    """One JSONL line of an E4 reader: the verdict on one candidate item."""

    model_config = ConfigDict(extra="forbid")
    meeting: str
    product: Literal["granola", "wispr"]
    cand_id: str = Field(pattern=r"^K[1-9][0-9]*$")
    kind: Kind
    true: TrueV
    in_notes: InNotes
    notes_bullets: list[str]
    gold_match: list[GoldMatch]
    evidence_quote: str
    reason: str
    confidence: Confidence

    @model_validator(mode="after")
    def _consistent(self) -> ReaderLine:
        if (self.in_notes == "no") != (not self.notes_bullets):
            raise ValueError("notes_bullets must be [] exactly when in_notes is 'no'")
        if len(self.notes_bullets) != len(set(self.notes_bullets)):
            raise ValueError("notes_bullets repeats an id")
        ids = [g.id for g in self.gold_match]
        if len(ids) != len(set(ids)):
            raise ValueError("gold_match repeats an id")
        return self


Verdicts = dict[str, ReaderLine]


def parse_lines(lines: Iterable[str]) -> tuple[list[ReaderLine], list[str]]:
    """Validate JSONL lines (blank lines skipped). Returns the valid lines and one error
    message per invalid line."""
    out: list[ReaderLine] = []
    errors: list[str] = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            out.append(ReaderLine.model_validate_json(line))
        except ValueError as e:
            errors.append(f"line {n}: {str(e).splitlines()[0]}")
    return out, errors


def check_unit(unit: Mapping[str, Any], lines: Sequence[ReaderLine]) -> tuple[Verdicts, list[str]]:
    """One unit's verdicts by K id, and every problem: missing, extra or repeated ids,
    bullets or gold ids the packet does not have. An empty problem list means usable."""
    probs: list[str] = []
    got: Verdicts = {}
    bids, gids = set(unit["bullet_ids"]), set(unit["gold_ids"])
    for ln in lines:
        if ln.cand_id not in unit["items"]:
            probs.append(f"unknown item {ln.cand_id}")
        elif ln.cand_id in got:
            probs.append(f"item {ln.cand_id} twice")
        else:
            got[ln.cand_id] = ln
        if bad := [b for b in ln.notes_bullets if b not in bids]:
            probs.append(f"{ln.cand_id}: unknown bullets {bad}")
        if bad := [g.id for g in ln.gold_match if g.id not in gids]:
            probs.append(f"{ln.cand_id}: unknown gold {bad}")
    probs += [f"item {k} missing" for k in unit["items"] if k not in got]
    return got, probs


# -- scoring ----------------------------------------------------------------------------------


def missing_gold(notes_rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    """Unit key -> {gold id: notes verdict} for gold the notes do not fully cover (E3 reader
    JSONL: ``notes_verdict`` partial / no)."""
    out: dict[str, dict[str, str]] = {}
    for r in notes_rows:
        u = out.setdefault(f"{r['meeting']}:{r['product']}", {})
        if r["notes_verdict"] != "full":
            u[r["gold_id"]] = r["notes_verdict"]
    return out


def condition_ids(cond: str, unit: Mapping[str, Any], verdicts: Verdicts) -> list[str]:
    """K ids a condition shows. R applies its oracle filter to the reader's verdicts: a
    decision / action / problem not already in the notes (as the API-judged E4 did)."""
    ids: list[str] = list(unit["shown"].get(cond, []))
    if cond == C4.R:
        ids = [i for i in ids if verdicts[i].kind != "other" and verdicts[i].in_notes != "yes"]
    return ids


def is_true_new(v: ReaderLine) -> bool:
    """A true new omission: a decision / action / problem that happened as stated and that
    the notes do not have at all."""
    return v.kind != "other" and v.true == "yes" and v.in_notes == "no"


def is_true_new_lenient(v: ReaderLine) -> bool:
    """Lenient: a decision / action / problem, at least partly true, not fully in the notes."""
    return v.kind != "other" and v.true != "no" and v.in_notes != "yes"


COUNTS = ("n_shown", "true_new", "true_new_lenient", "duplicates", "partial_in_notes",
          "false", "partly_true", "other_kind", "n_missing", "n_missing_none", "recovered",
          "recovered_lenient", "recovered_none")  # fmt: skip


def unit_counts(
    ids: Sequence[str], verdicts: Verdicts, missing: Mapping[str, str]
) -> dict[str, Any]:
    """One condition on one unit. ``missing``: gold id -> notes verdict (partial / no) for
    gold the notes miss. Recovered: missing gold that a shown item not already in the notes
    states fully (lenient: or partly)."""
    vs = [verdicts[i] for i in ids]
    live = [v for v in vs if v.in_notes != "yes"]
    full = {g.id for v in live for g in v.gold_match if g.match == "full" and g.id in missing}
    part = {g.id for v in live for g in v.gold_match if g.id in missing}
    none = {g for g, nv in missing.items() if nv == "no"}
    return {
        "n_shown": len(vs),
        "true_new": sum(map(is_true_new, vs)),
        "true_new_lenient": sum(map(is_true_new_lenient, vs)),
        "duplicates": sum(v.in_notes == "yes" for v in vs),
        "partial_in_notes": sum(v.in_notes == "partial" for v in vs),
        "false": sum(v.true == "no" for v in vs),
        "partly_true": sum(v.true == "partly" for v in vs),
        "other_kind": sum(v.kind == "other" for v in vs),
        "n_missing": len(missing),
        "n_missing_none": len(none),
        "recovered": len(full),
        "recovered_lenient": len(part),
        "recovered_none": len(full & none),
        "ids": list(ids),
        "recovered_ids": sorted(full),
    }


RATES = (
    ("precision_new", "true_new", "n_shown"),
    ("precision_new_lenient", "true_new_lenient", "n_shown"),
    ("duplicates", "duplicates", "n_shown"),
    ("false", "false", "n_shown"),
    ("recall_missing", "recovered", "n_missing"),
    ("recall_missing_lenient", "recovered_lenient", "n_missing"),
    ("recall_missing_none", "recovered_none", "n_missing_none"),
)


def summarize_group(rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    """Pooled counts and rates over unit rows (each with ``meeting_id``), with percentile
    bootstrap CIs that resample meetings (a meeting's units move together)."""
    tot = {k: sum(int(r[k]) for r in rows) for k in COUNTS}
    by_m: dict[str, dict[str, int]] = {}
    for r in rows:
        m = by_m.setdefault(r["meeting_id"], dict.fromkeys(COUNTS, 0))
        for k in COUNTS:
            m[k] += int(r[k])
    out: dict[str, Any] = {"n_units": len(rows), "n_meetings": len(by_m), "totals": tot}
    for name, num, den in RATES:
        out[name] = C4._ratio(tot[num], tot[den])
        pairs = [(float(m[num]), float(m[den])) for _, m in sorted(by_m.items())]
        out[f"{name}_ci"] = C4.ratio_ci(pairs, seed)
    out["shown_per_unit"] = tot["n_shown"] / len(rows) if rows else None
    out["true_new_per_unit"] = tot["true_new"] / len(rows) if rows else None
    out["false_per_unit"] = tot["false"] / len(rows) if rows else None
    return out


def score(
    key: Mapping[str, Mapping[str, Any]],
    verdicts: Mapping[str, Verdicts],
    missing: Mapping[str, Mapping[str, str]],
    seed: int = 42,
) -> dict[str, Any]:
    """Every condition, per product and both, over the units in ``verdicts`` (unit key ->
    verdicts by K id; units the readers have not done are left out and listed)."""
    units: list[dict[str, Any]] = []
    for uk, unit in sorted(key.items()):
        if uk not in verdicts:
            continue
        for cond in unit["conditions"]:
            ids = condition_ids(cond, unit, verdicts[uk])
            units.append(
                {
                    "unit": uk,
                    "meeting_id": unit["meeting_id"],
                    "product": unit["product"],
                    "condition": cond,
                    **unit_counts(ids, verdicts[uk], missing.get(uk, {})),
                }
            )
    summary: dict[str, Any] = {}
    for g in GROUPS:
        per: dict[str, Any] = {}
        for cond in CONDITIONS:
            rows = [u for u in units if u["condition"] == cond and g in ("both", u["product"])]
            if rows:
                per[cond] = summarize_group(rows, seed)
        if per:
            summary[g] = per
    return {
        "units_scored": sorted(verdicts),
        "units_not_scored": sorted(set(key) - set(verdicts)),
        "summary": summary,
        "per_unit": units,
    }
