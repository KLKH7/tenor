"""Meeting ledger: a structured record of every decision, task and open question of a meeting,
built from the product's own transcript before any notes exist; notes, chat and follow-ups would
be views of it.

Why: the notes checks so far each patch one symptom after the notes are written (an omitted
item, a decision softened to "explore", a task with no owner or the wrong one, a bullet firmer
than the meeting). All four are the same loss: the notes are written straight from the
transcript, with no record of what the items are, how settled each is at the end, who owns it
and which words it rests on. The ledger is that record. This module tests whether a ledger
built item by item covers more of the AMI gold than the product's notes do, and how many of its
entries are wrong.

* :func:`job_file`: what a generator agent sees: the instructions (``prompts/meeting_ledger``),
  a simulated attendee list (:func:`attendees`, from the packet's section 1 roster) and the
  product's transcript in segments of :data:`SEGMENT` lines. No notes, no gold, no AMI human
  transcript.
* :class:`Entry` and :func:`ingest_unit`: the agent's JSONL validated; every quote checked
  against its ``T`` line (:func:`quote_ok`); entries with no valid quote dropped and counted.
* :func:`build_packet`: a condition-blind reader packet in the E4 layout (the E3 packet's
  sections 1-5, then the entries' statements as candidates ``K1``.., exact duplicates merged,
  seeded shuffle) and the private key (K id -> entry id, kind, status, owner, owner basis), so
  ``prompts/e4_reader.v2.md`` readers work unchanged.
* :func:`consensus` and :func:`score`: the reader runs merged per item (medians, majority
  kind, gold matched in >= 2 runs), then per product and pooled: entries per unit, true rates,
  coverage of all gold next to the notes' coverage computed the same way, gold the notes miss
  that the ledger states, entries already in the notes, true rate by status, owners of
  actions; bootstrap CIs resample meetings.

Pure functions: no LLM, no network. Same inputs, same outputs.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tenor.evals import coverage_e4 as C4
from tenor.evals import e4_reader as R
from tenor.evals.bullet_cues import PRODUCT_LABEL, CueUnit
from tenor.evals.list_check import normalize

PROMPT = "meeting_ledger.v1"
READER_PROMPT = "e4_reader.v2"
PACKET_VERSION = "ledger_reader_packet.v1"
KEY_VERSION = "ledger_reader_key.v1"
CONDITION = "ledger"
SEGMENT = 60  # transcript lines per segment of the job file
PRODUCTS = ("granola", "wispr")
GROUPS = (*PRODUCTS, "both")
NO_ATTENDEES = "no attendee list"

Kind = Literal["decision", "action", "problem"]
Status = Literal["agreed", "leaning", "raised", "open"]
OwnerBasis = Literal["said", "attendee_list", "inferred", ""]


# -- the job file -----------------------------------------------------------------------------

_SEC1 = re.compile(r"^## 1\. .*?\n(.*?)(?=^## 2\. )", re.M | re.S)
_ROSTER = re.compile(r"^Speaker roster \(.*?\): (.+?)\.?$", re.M)
_PARTICIPANT = re.compile(r"^- AMI speaker [A-Z]: (.+)$", re.M)


def attendees(packet: str) -> list[tuple[str, str]] | None:
    """(name, role) pairs from the packet's section 1, as a calendar invite would list them.
    ``None`` when the meeting has no roster. A role of the participants list that the roster
    does not name is given with an empty name (the invite is incomplete, not guessed)."""
    m = _SEC1.search(packet)
    sec = m.group(1) if m else ""
    r = _ROSTER.search(sec)
    if not r:
        return None
    by_role = {}
    for part in r.group(1).split(";"):
        role, _, name = part.partition("=")
        if name.strip():
            by_role[role.strip()] = name.strip()
    roles = [x.strip() for x in _PARTICIPANT.findall(sec)]
    out = [(by_role[x], x) for x in roles if x in by_role]
    out += [(n, x) for x, n in by_role.items() if x not in roles]
    out += [("", x) for x in roles if x not in by_role]
    return out


def attendee_block(people: Sequence[tuple[str, str]] | None) -> str:
    """The attendee section of a job file. Says it is simulated; ``no attendee list`` when the
    meeting has no roster."""
    if people is None:
        return (
            f"{NO_ATTENDEES} (this meeting has no roster; owners can only come from what is "
            "said in the transcript)\n"
        )
    lines = [
        "Simulated: a real calendar invite was not available, so this list is built from the "
        "benchmark's roster for the meeting (names and roles). The transcript may spell a name "
        "differently; close spellings are the same person.",
        "",
    ]
    lines += [f"- {name or '(name not on the list)'}: {role}" for name, role in people]
    return "\n".join(lines) + "\n"


def segments(unit: CueUnit, size: int = SEGMENT) -> list[list[str]]:
    """The transcript as rendered lines ``[T0123 Speaker 12:34] text``, in order, ``size`` lines
    per segment."""
    rows = [f"[T{x.index:04d} {x.speaker} {x.start}] {x.text}" for x in unit.transcript]
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def job_file(unit: CueUnit, instructions: str, size: int = SEGMENT) -> str:
    """One unit's generator job: instructions, attendee list, transcript in segments. Built
    only from the packet's sections 1 (roster) and 5 (the product's transcript)."""
    segs = segments(unit, size)
    out = [
        f"# Meeting ledger job: {unit.key} ({len(unit.transcript)} transcript lines, "
        f"{len(segs)} segments)",
        "",
        "## Instructions",
        "",
        instructions.rstrip("\n"),
        "",
        "## Attendee list",
        "",
        attendee_block(attendees(unit.packet)),
        "## Transcript",
        "",
        f"The {PRODUCT_LABEL.get(unit.product, unit.product)} transcript of meeting "
        f"{unit.meeting_id}, one line per utterance as `[T0123 Speaker 12:34] text`.",
        "",
    ]
    for n, seg in enumerate(segs, 1):
        first, last = seg[0].split(" ", 1)[0][1:], seg[-1].split(" ", 1)[0][1:]
        out += [f"### Segment {n} of {len(segs)} ({first}-{last})", "", "```", *seg, "```", ""]
    return "\n".join(out)


# -- the agent's entries ----------------------------------------------------------------------


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line: str
    text: str


class Entry(BaseModel):
    """One ledger entry: one JSONL line of a generator agent."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^E[1-9][0-9]*$")
    kind: Kind
    status: Status
    statement: str = Field(min_length=1)
    owner: str
    owner_basis: OwnerBasis
    quote: list[Quote]
    history: list[str]


_T_ID = re.compile(r"^T(\d+)$")


def quote_ok(q: Quote, lines: Mapping[int, str]) -> bool:
    """Whether the quote's words are in the transcript line it names: a substring after
    ``normalize`` (case and punctuation ignored). Empty quotes and unknown lines fail."""
    m = _T_ID.match(q.line.strip())
    words = normalize(q.text)
    return bool(m and words and int(m.group(1)) in lines and words in lines[int(m.group(1))])


STATS = ("lines", "invalid", "repeated_id", "no_valid_quote", "quotes", "quotes_bad", "kept")


def ingest_unit(unit: CueUnit, text: str) -> tuple[list[Entry], dict[str, int], list[str]]:
    """An agent's answer file for one unit: the entries that validate and rest on at least one
    quote found in its line (bad quotes of a kept entry are removed), counts by outcome
    (:data:`STATS`), and one error message per rejected line."""
    lines = {x.index: normalize(x.text) for x in unit.transcript}
    stats = dict.fromkeys(STATS, 0)
    errors: list[str] = []
    seen: set[str] = set()
    out: list[Entry] = []
    for n, ln in enumerate(text.splitlines(), 1):
        if not ln.strip():
            continue
        stats["lines"] += 1
        try:
            e = Entry.model_validate_json(ln)
        except ValueError as err:
            stats["invalid"] += 1
            errors.append(f"{unit.key} line {n}: {str(err).splitlines()[0]}")
            continue
        if e.id in seen:
            stats["repeated_id"] += 1
            errors.append(f"{unit.key} line {n}: entry {e.id} twice")
            continue
        seen.add(e.id)
        good = [q for q in e.quote if quote_ok(q, lines)]
        stats["quotes"] += len(e.quote)
        stats["quotes_bad"] += len(e.quote) - len(good)
        if not good:
            stats["no_valid_quote"] += 1
            errors.append(f"{unit.key} line {n}: entry {e.id} has no quote found in its line")
            continue
        out.append(e.model_copy(update={"quote": good}))
    stats["kept"] = len(out)
    return out, stats, errors


# -- reader packets and the key ---------------------------------------------------------------


def build_packet(unit: CueUnit, entries: Sequence[Entry], seed: int) -> tuple[str, dict[str, Any]]:
    """The reader packet (E4 layout) and the key's unit for one unit's entries. Statements
    equal after ``normalize`` are one candidate; the key lists every entry behind it and takes
    kind, status and owner from the first."""
    by_id = {e.id: e for e in entries}
    items = R.pool(
        {CONDITION: [{"text": e.statement, "src": e.id} for e in entries]},
        seed,
        f"ledger:{unit.key}",
        (CONDITION,),
    )
    key_items: dict[str, Any] = {}
    for it in items:
        ids = [s.src for s in it.sources]
        first = by_id[ids[0]]
        key_items[it.id] = {
            "text": it.text,
            "entry_id": first.id,
            "kind": first.kind,
            "status": first.status,
            "owner": first.owner,
            "owner_basis": first.owner_basis,
            "entry_ids": ids,
        }
    head = [
        f"# E4 reader packet: {unit.meeting_id} / {PRODUCT_LABEL.get(unit.product, unit.product)}",
        "",
        f"Ledger packet format {PACKET_VERSION} (the E4 packet layout), reader instructions "
        f"`{READER_PROMPT}`. {len(items)} candidate items (section 6). `K7` = a candidate item. "
        "Sections 1-5 are the E3 reader packet; section 6 is at the end.",
    ]
    body = unit.packet.split("\n", 1)[1]
    text = "\n".join([*head, body.rstrip("\n"), "", *R.candidates_section(items)])
    key = {
        "meeting_id": unit.meeting_id,
        "product": unit.product,
        "bullet_ids": [b.id for b in unit.bullets],
        "gold_ids": list(unit.gold_ids),
        "n_entries": len(entries),
        "items": key_items,
    }
    return text, key


# -- consensus of the reader runs -------------------------------------------------------------

TRUE_RANK = {"no": 0, "partly": 1, "yes": 2}
NOTES_RANK = {"no": 0, "partial": 1, "yes": 2}
MATCH_RANK = {"partial": 1, "full": 2}
KIND_ORDER = ("decision", "action", "problem", "other")
MIN_RUNS = 2


def _median(values: Sequence[str], rank: Mapping[str, int]) -> str:
    """The median on an ordered scale; the lower of the two middle values for an even count."""
    return sorted(values, key=rank.__getitem__)[(len(values) - 1) // 2]


@dataclass(frozen=True)
class ItemVerdict:
    """The readers' consensus on one candidate."""

    kind: str
    true: str
    in_notes: str
    gold: dict[str, str]  # gold id -> full | partial
    n_runs: int


def dedupe(lines: Iterable[R.ReaderLine]) -> tuple[dict[str, dict[str, R.ReaderLine]], int]:
    """One run's lines by unit key and K id; a repeated (unit, K id) keeps its first line.
    Returns the lines and the number of repeats dropped."""
    out: dict[str, dict[str, R.ReaderLine]] = {}
    dropped = 0
    for ln in lines:
        u = out.setdefault(f"{ln.meeting}:{ln.product}", {})
        if ln.cand_id in u:
            dropped += 1
        else:
            u[ln.cand_id] = ln
    return out, dropped


def consensus_item(lines: Sequence[R.ReaderLine], gold_ids: Iterable[str]) -> ItemVerdict:
    """One item's verdict over the runs that read it: median ``true`` and ``in_notes`` (no <
    partly/partial < yes), majority kind (ties: the first of :data:`KIND_ORDER`), and each gold
    id matched in at least :data:`MIN_RUNS` runs at the median level of the runs that matched
    it. Gold ids the packet does not have are ignored."""
    known = set(gold_ids)
    kinds: Counter[str] = Counter(ln.kind for ln in lines)
    kind = max(KIND_ORDER, key=lambda k: (kinds[k], -KIND_ORDER.index(k)))
    levels: dict[str, list[str]] = {}
    for ln in lines:
        for g in ln.gold_match:
            if g.id in known:
                levels.setdefault(g.id, []).append(g.match)
    gold = {g: _median(ms, MATCH_RANK) for g, ms in sorted(levels.items()) if len(ms) >= MIN_RUNS}
    return ItemVerdict(
        kind=kind,
        true=_median([ln.true for ln in lines], TRUE_RANK),
        in_notes=_median([ln.in_notes for ln in lines], NOTES_RANK),
        gold=gold,
        n_runs=len(lines),
    )


def consensus(
    key_units: Mapping[str, Mapping[str, Any]],
    runs: Sequence[Mapping[str, Mapping[str, R.ReaderLine]]],
) -> tuple[dict[str, dict[str, ItemVerdict]], dict[str, list[str]]]:
    """Unit key -> K id -> consensus, for units whose every item was read by at least
    :data:`MIN_RUNS` runs; the other units with their problems. A unit with no items is scored
    (an empty ledger)."""
    out: dict[str, dict[str, ItemVerdict]] = {}
    problems: dict[str, list[str]] = {}
    for uk, unit in sorted(key_units.items()):
        probs: list[str] = []
        per: dict[str, ItemVerdict] = {}
        for n, run in enumerate(runs, 1):
            extra = sorted(set(run.get(uk, {})) - set(unit["items"]))
            if extra:
                probs.append(f"run {n}: unknown items {extra}")
        for k in unit["items"]:
            got = [run[uk][k] for run in runs if k in run.get(uk, {})]
            if len(got) < MIN_RUNS:
                probs.append(f"item {k}: read by {len(got)} run(s)")
            else:
                per[k] = consensus_item(got, unit["gold_ids"])
        if probs:
            problems[uk] = probs
        else:
            out[uk] = per
    return out, problems


# -- scoring ----------------------------------------------------------------------------------

CREDIT = {"full": 1.0, "partial": 0.5}
CATEGORIES = ("D", "A", "P")


def notes_by_unit(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    """Unit key -> gold id -> the notes' verdict (full / partial / no), from the E3 reader
    consensus JSONL."""
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        out.setdefault(f"{r['meeting']}:{r['product']}", {})[r["gold_id"]] = r["notes_verdict"]
    return out


def best_match(verdicts: Mapping[str, ItemVerdict], drop_false: bool = False) -> dict[str, str]:
    """Gold id -> the best level any ledger item states it at (``drop_false``: items the
    readers judged not true do not count)."""
    best: dict[str, str] = {}
    for v in verdicts.values():
        if drop_false and v.true == "no":
            continue
        for g, m in v.gold.items():
            if MATCH_RANK[m] > MATCH_RANK.get(best.get(g, ""), 0):
                best[g] = m
    return best


def coverage(levels: Mapping[str, str], gold_ids: Sequence[str]) -> dict[str, float | None]:
    """Mean credit over a unit's gold (full = 1, partial = 0.5, else 0): ``all`` and per
    category ``D`` / ``A`` / ``P`` (``None`` when the unit has no gold of that category)."""
    out: dict[str, float | None] = {}
    for name in ("all", *CATEGORIES):
        ids = [g for g in gold_ids if name == "all" or g.startswith(name)]
        out[name] = sum(CREDIT.get(levels.get(g, ""), 0.0) for g in ids) / len(ids) if ids else None
    return out


def unit_row(
    unit: Mapping[str, Any], verdicts: Mapping[str, ItemVerdict], notes: Mapping[str, str]
) -> dict[str, Any]:
    """Everything the summary needs from one unit: counts over its items, coverage of its gold
    by the ledger and by the notes, and the gold the notes miss that the ledger states."""
    gold = list(unit["gold_ids"])
    best = best_match(verdicts)
    row: dict[str, Any] = {
        "meeting_id": unit["meeting_id"],
        "product": unit["product"],
        "n_items": len(verdicts),
        "n_gold": len(gold),
    }
    vs = list(verdicts.values())
    for t in TRUE_RANK:
        row[f"true_{t}"] = sum(v.true == t for v in vs)
    for t in NOTES_RANK:
        row[f"in_notes_{t}"] = sum(v.in_notes == t for v in vs)
    row["reader_kind_other"] = sum(v.kind == "other" for v in vs)
    for s in ("agreed", "leaning", "raised", "open"):
        ks = [k for k, it in unit["items"].items() if it["status"] == s]
        row[f"status_{s}"] = len(ks)
        row[f"status_{s}_true_yes"] = sum(verdicts[k].true == "yes" for k in ks)
        row[f"status_{s}_true_no"] = sum(verdicts[k].true == "no" for k in ks)
    acts = [it for it in unit["items"].values() if it["kind"] == "action"]
    owned = [it for it in acts if it["owner"].strip()]
    row["actions"] = len(acts)
    row["actions_owned"] = len(owned)
    for b in ("said", "attendee_list", "inferred"):
        row[f"owner_{b}"] = sum(it["owner_basis"] == b for it in owned)
    act_ids = [k for k, it in unit["items"].items() if it["kind"] == "action" and it["owner"]]
    row["actions_owned_true_yes"] = sum(verdicts[k].true == "yes" for k in act_ids)
    row["cov_ledger"] = coverage(best, gold)
    row["cov_ledger_true"] = coverage(best_match(verdicts, drop_false=True), gold)
    row["cov_notes"] = coverage(notes, gold)
    for nv in ("partial", "no"):
        miss = [g for g in gold if notes.get(g) == nv]
        row[f"miss_{nv}"] = len(miss)
        row[f"miss_{nv}_full"] = sum(best.get(g) == "full" for g in miss)
        row[f"miss_{nv}_any"] = sum(g in best for g in miss)
    row["best"] = best
    return row


def _clusters(rows: Sequence[Mapping[str, Any]], num: str, den: str) -> list[tuple[float, float]]:
    by: dict[str, list[float]] = {}
    for r in rows:
        m = by.setdefault(r["meeting_id"], [0.0, 0.0])
        m[0] += float(r[num])
        m[1] += float(r[den])
    return [(a, b) for _, (a, b) in sorted(by.items())]


def _rate(rows: Sequence[Mapping[str, Any]], num: str, den: str, seed: int) -> dict[str, Any]:
    """Pooled ``sum(num) / sum(den)`` with a CI that resamples meetings."""
    pairs = _clusters(rows, num, den)
    a, b = sum(x for x, _ in pairs), sum(y for _, y in pairs)
    ci = C4.ratio_ci(pairs, seed)
    return {"num": a, "den": b, "value": a / b if b else None, "ci": list(ci) if ci else None}


def _mean(rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    """Mean of the rows' ``x`` (rows with ``None`` left out) with a meeting-cluster CI."""
    live = [
        {"meeting_id": r["meeting_id"], "x": r["x"], "one": 1} for r in rows if r["x"] is not None
    ]
    out = _rate(live, "x", "one", seed)
    return {"n": len(live), "value": out["value"], "ci": out["ci"]}


def summarize(rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    """One group's table: every metric over its unit rows."""

    def mean_of(pick: Any) -> dict[str, Any]:
        return _mean([{"meeting_id": r["meeting_id"], "x": pick(r)} for r in rows], seed)

    out: dict[str, Any] = {
        "n_units": len(rows),
        "n_meetings": len({r["meeting_id"] for r in rows}),
        "n_items": sum(r["n_items"] for r in rows),
        "items_per_unit": mean_of(lambda r: float(r["n_items"])),
    }
    for t in TRUE_RANK:
        out[f"true_{t}"] = _rate(rows, f"true_{t}", "n_items", seed)
    for t in NOTES_RANK:
        out[f"in_notes_{t}"] = _rate(rows, f"in_notes_{t}", "n_items", seed)
    out["reader_kind_other"] = _rate(rows, "reader_kind_other", "n_items", seed)
    cov: dict[str, Any] = {}
    for c in ("all", *CATEGORIES):
        cov[c] = {
            "ledger": mean_of(lambda r, c=c: r["cov_ledger"][c]),
            "ledger_true_only": mean_of(lambda r, c=c: r["cov_ledger_true"][c]),
            "notes": mean_of(lambda r, c=c: r["cov_notes"][c]),
            "ledger_minus_notes": mean_of(
                lambda r, c=c: (
                    None if r["cov_ledger"][c] is None else r["cov_ledger"][c] - r["cov_notes"][c]
                )
            ),
        }
    out["coverage"] = cov
    rows2 = [{**r, "miss": r["miss_partial"] + r["miss_no"],
              "miss_full": r["miss_partial_full"] + r["miss_no_full"],
              "miss_any": r["miss_partial_any"] + r["miss_no_any"]} for r in rows]  # fmt: skip
    out["notes_miss"] = {
        "all": {"full": _rate(rows2, "miss_full", "miss", seed),
                "incl_partly": _rate(rows2, "miss_any", "miss", seed)},
        "notes_partial": {"full": _rate(rows, "miss_partial_full", "miss_partial", seed),
                          "incl_partly": _rate(rows, "miss_partial_any", "miss_partial", seed)},
        "notes_no": {"full": _rate(rows, "miss_no_full", "miss_no", seed),
                     "incl_partly": _rate(rows, "miss_no_any", "miss_no", seed)},
    }  # fmt: skip
    out["by_status"] = {
        s: {
            "true_yes": _rate(rows, f"status_{s}_true_yes", f"status_{s}", seed),
            "true_no": _rate(rows, f"status_{s}_true_no", f"status_{s}", seed),
        }
        for s in ("agreed", "leaning", "raised", "open")
    }
    out["owners"] = {
        "actions_with_owner": _rate(rows, "actions_owned", "actions", seed),
        "owned_actions_true_yes": _rate(rows, "actions_owned_true_yes", "actions_owned", seed),
        **{f"basis_{b}": _rate(rows, f"owner_{b}", "actions_owned", seed)
           for b in ("said", "attendee_list", "inferred")},
    }  # fmt: skip
    return out


def score(
    key_units: Mapping[str, Mapping[str, Any]],
    verdicts: Mapping[str, Mapping[str, ItemVerdict]],
    notes: Mapping[str, Mapping[str, str]],
    seed: int = 42,
) -> dict[str, Any]:
    """Per product and pooled, over units that have consensus verdicts and a notes verdict for
    every gold id; the other units are listed with the reason."""
    rows: list[dict[str, Any]] = []
    skipped: dict[str, str] = {}
    for uk, unit in sorted(key_units.items()):
        if uk not in verdicts:
            skipped[uk] = "no reader consensus"
        elif gaps := sorted(set(unit["gold_ids"]) - set(notes.get(uk, {}))):
            skipped[uk] = f"gold without a notes verdict: {gaps}"
        else:
            rows.append({"unit": uk, **unit_row(unit, verdicts[uk], notes[uk])})
    summary = {
        g: summarize(sel, seed)
        for g in GROUPS
        if (sel := [r for r in rows if g in ("both", r["product"])])
    }
    return {"summary": summary, "units_not_scored": skipped, "per_unit": rows}


def table(summary: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """The report as a Markdown table, one column per group."""
    groups = list(summary)

    def pct(x: Mapping[str, Any], count: bool = True) -> str:
        if x["value"] is None:
            return "-"
        ci = f" [{100 * x['ci'][0]:.0f}, {100 * x['ci'][1]:.0f}]" if x.get("ci") else ""
        n = f" ({x['num']:.0f}/{x['den']:.0f})" if count and "num" in x else ""
        return f"{100 * x['value']:.0f}%{ci}{n}"

    def num(x: Mapping[str, Any]) -> str:
        if x["value"] is None:
            return "-"
        ci = f" [{x['ci'][0]:.2f}, {x['ci'][1]:.2f}]" if x.get("ci") else ""
        return f"{x['value']:.2f}{ci}"

    def row(label: str, cell: Any) -> str:
        return f"| {label} | " + " | ".join(cell(summary[g]) for g in groups) + " |"

    out = ["| Metric | " + " | ".join(groups) + " |", "|---|" + "---|" * len(groups)]
    out.append(row("Units (meetings)", lambda s: f"{s['n_units']} ({s['n_meetings']})"))
    out.append(row("Entries per unit", lambda s: f"{s['items_per_unit']['value']:.1f}"))
    for t in ("yes", "partly", "no"):
        out.append(row(f"Entries true: {t}", lambda s, t=t: pct(s[f"true_{t}"])))
    for c, name in (("all", "all gold"), ("D", "decisions"), ("A", "actions"), ("P", "problems")):
        out.append(row(f"Coverage, {name}: ledger", lambda s, c=c: num(s["coverage"][c]["ledger"])))
        out.append(row(f"Coverage, {name}: notes", lambda s, c=c: num(s["coverage"][c]["notes"])))
    out.append(row("Coverage, all gold: ledger - notes",
                   lambda s: num(s["coverage"]["all"]["ledger_minus_notes"])))  # fmt: skip
    out.append(row("Coverage, all gold: ledger, entries judged false left out",
                   lambda s: num(s["coverage"]["all"]["ledger_true_only"])))  # fmt: skip
    for k, name in (("all", "notes miss"), ("notes_partial", "notes partial"),
                    ("notes_no", "notes no")):  # fmt: skip
        out.append(row(f"Gold the {name}: ledger full",
                       lambda s, k=k: pct(s["notes_miss"][k]["full"])))  # fmt: skip
        out.append(row(f"Gold the {name}: ledger full or partly",
                       lambda s, k=k: pct(s["notes_miss"][k]["incl_partly"])))  # fmt: skip
    for t in ("yes", "partial", "no"):
        out.append(row(f"Entries in the notes: {t}", lambda s, t=t: pct(s[f"in_notes_{t}"])))
    for st in ("agreed", "leaning", "raised", "open"):
        out.append(row(f"Status '{st}': true yes",
                       lambda s, st=st: pct(s["by_status"][st]["true_yes"])))  # fmt: skip
    out.append(row("Status 'agreed': true no", lambda s: pct(s["by_status"]["agreed"]["true_no"])))
    out.append(row("Action entries with an owner",
                   lambda s: pct(s["owners"]["actions_with_owner"])))  # fmt: skip
    for b in ("said", "attendee_list", "inferred"):
        out.append(row(f"Owner basis: {b}", lambda s, b=b: pct(s["owners"][f"basis_{b}"])))
    out.append(row("Owned action entries: true yes",
                   lambda s: pct(s["owners"]["owned_actions_true_yes"])))  # fmt: skip
    return out


def read_reader_lines(text: str) -> tuple[list[R.ReaderLine], list[str]]:
    """A reader JSONL file's valid lines and one error per invalid line."""
    return R.parse_lines(text.splitlines())


def key_json(meta: Mapping[str, Any], units: Mapping[str, Any]) -> str:
    """The key file's text."""
    return (
        json.dumps({"meta": dict(meta), "units": dict(units)}, indent=1, ensure_ascii=False) + "\n"
    )
