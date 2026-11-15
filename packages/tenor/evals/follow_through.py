"""Follow-through from a product's own notes: item fates and a "since last time" writer.

``tenor.evals.item_fates`` took the earlier meeting's items from AMI's human annotators and
read the later meeting in AMI's human transcript. This module asks the same question of what a
notes product actually holds: the items are the bullets of the product's notes of meeting X that
the notes present as a decision or a task, and meeting Y is the product's own machine
transcript. No LLM here: readers, the writer and the checkers are agents that read job files
(file I/O in ``scripts/follow_through.py``).

Part A (fates of notes items)

1. :func:`note_items`: the bullets a ``statement_kind_notes.v1`` reader labelled ``decision`` or
   ``task``, renamed D1.., A1.. in notes order.
2. :func:`fates_job_file`: the frozen prompt ``item_fates.v1`` unchanged, a note on how this
   input differs from the one the prompt describes, all of X's notes, the items, and Y's product
   transcript renumbered L0000... Answers are validated by ``item_fates.parse_answers`` and
   resolved by the same rule (a and b agree, else c, else ``unclear``): :func:`item_rows`.
3. :func:`distribution`, :func:`silent_items`, :func:`agreement`: counts only. With one meeting
   series there is nothing to resample, so no interval is computed.

Part B (the writer)

4. :func:`writer_job_file`: prompt :data:`WRITER_PROMPT`, X's notes, Y's product transcript.
   :func:`parse_writer` keeps at most :data:`MAX_LINES` lines and checks each one mechanically:
   known bullets, and for a ``changed`` line a quote of at least :data:`MIN_QUOTE_WORDS` words
   found in the Y lines it cites; a ``not_mentioned`` line cites nothing.
5. :func:`check_job_file`: prompt :data:`CHECK_PROMPT`, the same two documents, the Part A item
   list (ids and text, no fates) and the writer's lines. Two checkers answer; neither sees the
   other, the writer's reasoning or any fate. :func:`parse_verdicts` validates.
6. :func:`writer_score`: lines both checkers call supported (strict) or at least one does
   (lenient), over all lines written (a line that failed its mechanical check counts as not
   supported), and how many of Part A's silent items a strictly supported line names.

Known limits: one meeting series (four AMI scenario meetings, three pairs, two products);
readers, writer and checkers are agents of one model family; which bullets count as items is
itself a reader's label; a ``not_mentioned`` claim cannot be quoted, only checked by re-reading.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from tenor.evals import bullet_cues as B
from tenor.evals import came_back as K
from tenor.evals import item_fates as F
from tenor.evals.list_check import normalize
from tenor.evals.notes_completeness import Bullet

FATES_PROMPT = F.PROMPT
WRITER_PROMPT = "since_last_time.v1"
CHECK_PROMPT = "since_last_time_check.v1"
ITEM_LETTER = {"decision": "D", "task": "A"}  # statement-kind label -> item id letter
MAX_LINES = 3
MIN_QUOTE_WORDS = 4
CHECKERS = ("a", "b")
DEV_PAIRS = frozenset({"IS1008a__IS1008b"})  # the pair prompts may be developed on
SILENT_FATES = ("changed_silently", "not_mentioned")

ADAPTATION = (
    "**How this file differs from what the instructions describe.** The X items are not a human "
    "annotator's sentences. They are the bullets of a notes product's own notes of meeting X "
    'that the notes present as a decision or a task; read "the annotator\'s sentence" as "the '
    "notes bullet\". There is no linked passage of X: all of X's notes are given instead, for "
    "context. The notes are the record of what X agreed; do not second-guess them. Y is the "
    "product's own machine transcript of the later meeting, renumbered from L0000: speaker "
    "labels are the product's and may be generic or wrong, and names and terms may be misspelt."
)


@dataclass(frozen=True)
class NoteItem:
    """One decision or task bullet of a product's notes, under an item-fates id."""

    item_id: str  # D1, A2, ...
    bullet_id: str
    kind: str  # decision / task
    text: str  # the bullet as the packet renders it (id, section, parent, text)


def note_items(bullets: Sequence[Bullet], kinds: Mapping[str, str]) -> list[NoteItem]:
    """The bullets labelled ``decision`` or ``task`` in ``kinds`` (bullet id -> label), in notes
    order, numbered per kind from 1. A bullet with no label is not an item."""
    count: Counter[str] = Counter()
    out = []
    for b in bullets:
        letter = ITEM_LETTER.get(kinds.get(b.id, ""))
        if letter:
            count[letter] += 1
            out.append(NoteItem(f"{letter}{count[letter]}", b.id, kinds[b.id], b.render()))
    return out


def parse_kinds(text: str) -> dict[str, str]:
    """bullet id -> ``presented_as`` from a statement-kind notes answer (JSONL); lines that are
    not an object with both fields are skipped."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and isinstance(d.get("bullet_id"), str):
            out.setdefault(d["bullet_id"], str(d.get("presented_as", "")))
    return out


def y_lines(unit: B.CueUnit) -> list[str]:
    """Y's product transcript as the readers see it: ``L0042 [03:12] Them: text``, numbered by
    position from 0."""
    return [
        f"{K.line_id(i)} [{x.start}] {x.speaker}: {x.text}" for i, x in enumerate(unit.transcript)
    ]


def _notes(unit: B.CueUnit) -> str:
    return "\n".join(b.render() for b in unit.bullets) or "(the notes are empty)"


def _y_block(y: B.CueUnit) -> str:
    return (
        f"## Meeting Y ({y.meeting_id}): the product's own transcript\n\n```\n"
        + "\n".join(y_lines(y))
        + "\n```"
    )


def _x_notes_block(x: B.CueUnit) -> str:
    return (
        f"## Meeting X ({x.meeting_id}): the product's notes, every bullet\n\n"
        "`id [section: heading] (sub-point of parent) text`. Read a sub-point with its parent."
        f"\n\n```\n{_notes(x)}\n```"
    )


def _item_list(items: Sequence[NoteItem]) -> str:
    return "\n".join(f"- {it.item_id} ({it.kind}): {it.text}" for it in items)


def fates_job_file(
    x: B.CueUnit,
    y: B.CueUnit,
    items: Sequence[NoteItem],
    prompt: str,
    answer_path: str,
    only: Sequence[str] | None = None,
) -> str:
    """A fates reader's whole input for one pair and product; with ``only``, restricted to
    those items (the tie-break job). Same arguments -> same bytes."""
    shown = [it for it in items if only is None or it.item_id in only]
    scope = (
        f"all {len(items)} items"
        if only is None
        else f"only the {len(shown)} of {len(items)} items listed below"
    )
    parts = [
        f"# Item fates from product notes: {x.meeting_id} -> {y.meeting_id} ({x.product})",
        f"Prompt `{FATES_PROMPT}`. Classify {scope}: {', '.join(it.item_id for it in shown)}. "
        f"Write your answer (JSONL, one line per item) to `{answer_path}`. Use only this file.",
        prompt.strip(),
        "---",
        ADAPTATION,
        _x_notes_block(x),
        f"## Meeting X ({x.meeting_id}): the items to classify\n\n{_item_list(shown)}",
        _y_block(y),
    ]
    return "\n\n".join(parts) + "\n"


def item_rows(
    pair: K.Pair,
    product: str,
    items: Sequence[NoteItem],
    answers: Mapping[str, Mapping[str, F.Answer]],
) -> list[dict[str, Any]]:
    """One row per notes item: each reader's fate and need, and the final ones (what a and b
    agree on, else c's, else ``unclear``)."""
    rows = []
    for it in items:
        got = {r: answers.get(r, {}).get(it.item_id) for r in (*F.READERS, F.TIEBREAK)}
        fates = {r: (g.fate if g else None) for r, g in got.items()}
        needs = {r: (g.needed_followup if g else None) for r, g in got.items()}
        rows.append(
            {
                "pair": pair.name,
                "product": product,
                "x_item": it.item_id,
                "bullet_id": it.bullet_id,
                "item_kind": it.kind,
                "text": it.text,
                **{f"fate_{r}": f for r, f in fates.items()},
                **{f"needed_{r}": f for r, f in needs.items()},
                "fate": F.final(fates["a"], fates["b"], fates["c"]),
                "needed_followup": F.final(needs["a"], needs["b"], needs["c"]),
                "y_lines": sorted({s for g in got.values() if g for s in g.y_lines}),
            }
        )
    return rows


def is_silent(row: Mapping[str, Any]) -> bool:
    """Changed without comment, or never came back although the team would expect to hear."""
    return bool(
        row["fate"] == "changed_silently"
        or (row["fate"] == "not_mentioned" and row["needed_followup"] == "yes")
    )


def silent_items(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Ids of the silent items among ``rows`` (one pair and product), in order."""
    return [r["x_item"] for r in rows if is_silent(r)]


def distribution(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Items per final fate, every fate present (zero included), ``unclear`` last."""
    c = Counter(r["fate"] for r in rows)
    return {f: c.get(f, 0) for f in (*F.FATES, F.UNCLEAR)}


def agreement(rows: Sequence[Mapping[str, Any]], field: str = "fate") -> dict[str, Any]:
    """Readers a and b on ``field`` (``fate`` or ``needed``): items both answered, items they
    agree on, and Cohen's kappa (None when undefined)."""
    both = [
        (r[f"{field}_a"], r[f"{field}_b"])
        for r in rows
        if r[f"{field}_a"] is not None and r[f"{field}_b"] is not None
    ]
    n = len(both)
    same = sum(a == b for a, b in both)
    if not n:
        return {"n": 0, "agree": 0, "kappa": None}
    ca, cb = Counter(a for a, _ in both), Counter(b for _, b in both)
    expected = sum(ca[k] * cb[k] for k in ca) / (n * n)
    kappa = None if expected == 1 else round((same / n - expected) / (1 - expected), 2)
    return {"n": n, "agree": same, "kappa": kappa}


# -- Part B: the writer ---------------------------------------------------------------------


class WriterLine(BaseModel):
    """One sentence of the writer, as the writer prompt's output format defines it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    sentence: str
    kind: Literal["changed", "not_mentioned"]
    x_bullets: tuple[str, ...]
    y_lines: tuple[str, ...] = ()
    quote: str = ""
    # Writer v4 and later: whether the sentence names whole items or parts of items the later
    # meeting does take up. Absent for earlier writers; never shown to checkers.
    scope: Literal["item", "part"] | None = None


def writer_job_file(x: B.CueUnit, y: B.CueUnit, prompt: str, answer_path: str) -> str:
    """The writer's whole input for one pair and product. Same arguments -> same bytes."""
    parts = [
        f"# Since last time: {x.meeting_id} -> {y.meeting_id} ({x.product})",
        f"Prompt `{WRITER_PROMPT}`. Write your answer (JSONL, at most {MAX_LINES} lines, "
        f"possibly empty) to `{answer_path}`. Use only this file.",
        prompt.strip(),
        "---",
        _x_notes_block(x),
        _y_block(y),
    ]
    return "\n\n".join(parts) + "\n"


def _line_index(line_id: str, n: int) -> int | None:
    hit = K.LINE_ID.match(line_id)
    return int(hit[1]) if hit and int(hit[1]) < n else None


def check_line(w: WriterLine, bullet_ids: Sequence[str], y: B.CueUnit) -> str:
    """``ok``, or the first mechanical problem with a writer line."""
    if not w.sentence.strip():
        return "empty sentence"
    if not w.x_bullets or any(b not in bullet_ids for b in w.x_bullets):
        return "x_bullets missing or unknown"
    idx = [_line_index(s, len(y.transcript)) for s in w.y_lines]
    if any(i is None for i in idx):
        return "unknown y_lines"
    if w.kind == "not_mentioned":
        return "ok" if not w.y_lines and not w.quote.strip() else "not_mentioned with evidence"
    if not idx:
        return "changed without y_lines"
    quote = normalize(w.quote)
    if len(quote.split()) < MIN_QUOTE_WORDS:
        return "quote too short"
    cited = normalize(" ".join(y.transcript[i].text for i in idx if i is not None))
    return "ok" if quote in cited else "quote not in the cited lines"


def parse_writer(
    text: str, bullet_ids: Sequence[str], y: B.CueUnit
) -> tuple[list[dict[str, Any]], list[str]]:
    """The writer's lines in order, each ``{"n", "line", "status"}`` (``status`` from
    :func:`check_line`), and one message per rejected raw line. Lines past :data:`MAX_LINES`
    are kept with status ``over the cap``."""
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            w = WriterLine.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            errors.append(f"line {n}: {str(e).splitlines()[0]}")
            continue
        status = "over the cap" if len(out) >= MAX_LINES else check_line(w, bullet_ids, y)
        out.append({"n": len(out) + 1, "line": w, "status": status})
    return out, errors


def check_job_file(
    x: B.CueUnit,
    y: B.CueUnit,
    items: Sequence[NoteItem],
    lines: Sequence[WriterLine],
    prompt: str,
    answer_path: str,
) -> str:
    """A checker's whole input: both documents, the item list without fates, the sentences
    with the evidence each cites. Same arguments -> same bytes."""
    shown = []
    for n, w in enumerate(lines, start=1):
        evidence = (
            f'cites {", ".join(w.y_lines)}; quote: "{w.quote}"'
            if w.kind == "changed"
            else "cites nothing (the claim is that Y never comes back to it)"
        )
        shown.append(f"{n}. ({w.kind}; about {', '.join(w.x_bullets)}) {w.sentence}\n   {evidence}")
    parts = [
        f"# Since last time, check: {x.meeting_id} -> {y.meeting_id} ({x.product})",
        f"Prompt `{CHECK_PROMPT}`. {len(lines)} sentences. Write your answer (JSONL, one line "
        f"per sentence) to `{answer_path}`. Use only this file.",
        prompt.strip(),
        "---",
        _x_notes_block(x),
        f"## Meeting X ({x.meeting_id}): its decision and task items\n\n{_item_list(items)}",
        _y_block(y),
        "## The sentences to check\n\n" + "\n".join(shown),
    ]
    return "\n\n".join(parts) + "\n"


class Verdict(BaseModel):
    """One checker line, as the checker prompt's output format defines it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    line: int
    supported: Literal["yes", "partly", "no"]
    x_items: tuple[str, ...] = ()
    y_lines: tuple[str, ...] = ()
    reason: str = ""


def parse_verdicts(
    text: str, n_lines: int, item_ids: Sequence[str]
) -> tuple[dict[int, Verdict], list[str]]:
    """sentence number -> the checker's verdict, and one message per problem. Unknown item ids
    are dropped from ``x_items`` and reported; a second verdict for a sentence is rejected."""
    out: dict[int, Verdict] = {}
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            v = Verdict.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            errors.append(f"line {n}: {str(e).splitlines()[0]}")
            continue
        if not 1 <= v.line <= n_lines:
            errors.append(f"line {n}: no sentence {v.line}")
        elif v.line in out:
            errors.append(f"line {n}: second verdict for sentence {v.line}")
        else:
            unknown = [i for i in v.x_items if i not in item_ids]
            if unknown:
                errors.append(f"line {n}: unknown x_items {unknown} (dropped)")
                v = v.model_copy(update={"x_items": tuple(i for i in v.x_items if i in item_ids)})
            out[v.line] = v
    return out, errors


def line_rows(
    unit: str,
    written: Sequence[Mapping[str, Any]],
    verdicts: Mapping[str, Mapping[int, Verdict]],
    fate_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One row per writer line of a unit: its mechanical status, each checker's verdict, and
    the Part A fate of every item both checkers say it is about. ``verdicts[checker][k]`` is
    numbered over the lines with status ``ok`` only, in order (what the checkers were shown)."""
    fate = {r["x_item"]: r for r in fate_rows}
    rows = []
    k = 0
    for w in written:
        line: WriterLine = w["line"]
        row: dict[str, Any] = {
            "unit": unit,
            "n": w["n"],
            "kind": line.kind,
            "sentence": line.sentence,
            "x_bullets": list(line.x_bullets),
            "status": w["status"],
            "scope": line.scope,
            **{f"supported_{c}": None for c in CHECKERS},
            "x_items": [],
            "item_fates": [],
            "names_silent_item": False,
        }
        if w["status"] == "ok":
            k += 1
            got = [verdicts.get(c, {}).get(k) for c in CHECKERS]
            for c, v in zip(CHECKERS, got, strict=True):
                row[f"supported_{c}"] = v.supported if v else None
            if all(got):
                shared = sorted(set(got[0].x_items) & set(got[1].x_items))  # type: ignore[union-attr]
                row["x_items"] = shared
                row["item_fates"] = [fate[i]["fate"] for i in shared if i in fate]
                row["names_silent_item"] = any(is_silent(fate[i]) for i in shared if i in fate)
        row["strict"] = all(row[f"supported_{c}"] == "yes" for c in CHECKERS)
        row["lenient"] = any(row[f"supported_{c}"] == "yes" for c in CHECKERS)
        rows.append(row)
    return rows


def writer_score(
    rows: Sequence[Mapping[str, Any]], fate_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Counts over the writer's lines (``rows`` from :func:`line_rows``) and the Part A rows of
    the same units: lines written, lines that passed the mechanical check, lines supported
    (strict: both checkers ``yes``; lenient: at least one), and silent items covered (named by
    both checkers under a strictly supported line)."""
    silent = {(f"{r['pair']}:{r['product']}", r["x_item"]) for r in fate_rows if is_silent(r)}
    covered = {(r["unit"], i) for r in rows if r["strict"] for i in r["x_items"]} & silent
    units = sorted({f"{r['pair']}:{r['product']}" for r in fate_rows})
    return {
        "units": len(units),
        "units_with_a_line": len({r["unit"] for r in rows}),
        "lines": len(rows),
        "lines_ok_mechanically": sum(r["status"] == "ok" for r in rows),
        "supported_strict": sum(r["strict"] for r in rows),
        "supported_lenient": sum(r["lenient"] for r in rows),
        "checkers_agree": sum(
            r["supported_a"] == r["supported_b"] for r in rows if r["status"] == "ok"
        ),
        "strict_lines_naming_a_silent_item": sum(
            r["strict"] and r["names_silent_item"] for r in rows
        ),
        "silent_items": len(silent),
        "silent_items_covered": len(covered),
        "by_kind": {
            k: {
                "lines": sum(r["kind"] == k for r in rows),
                "supported_strict": sum(r["strict"] for r in rows if r["kind"] == k),
            }
            for k in ("changed", "not_mentioned")
        },
    }
