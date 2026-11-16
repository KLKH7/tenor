"""Quiet-change lines, written then verified, on annotated AMI pairs.

The "since last time" writers (``tenor.evals.since_writer``) wrote two kinds of line. Lines
saying an item never came back held up; lines saying an item quietly changed were wrong about
one time in three. Here the quiet-change line gets its own pipeline:

1. a writer (``since_last_time_changed.v<N>``) proposes at most 3 lines per pair, each citing
   X's bullet(s) and quoting, verbatim and by line id, the lines of Y that show the different
   version (:class:`ChangedLine`, checked mechanically by :func:`parse_changed`);
2. a separate verifier (``quiet_change_verify.v<N>``) sees X's notes, all of Y and the proposed
   lines (:func:`verify_job`) and accepts a line only if the quotes show a different version,
   nobody in Y raises the change, and the line says no more than the quotes
   (:class:`Verification`, :func:`parse_verifications`);
3. the kept lines go to the unchanged "since last time" checkers as ``changed`` sentences
   (:func:`as_writer_line`, ``since_writer.check_job``);
4. :func:`line_rows` and :func:`score` join the lines, the verifier, the checkers and the item
   fates: kept lines judged true, quiet changes covered, kept lines whose item the fates call
   changed openly or unchanged, and proposed lines the verifier dropped.

No LLM and no network: agents read the job files and write JSONL.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from tenor.evals import bullet_cues as B
from tenor.evals import came_back as K
from tenor.evals import follow_through as T
from tenor.evals import since_writer as W
from tenor.evals.coverage_e4 import ratio_ci
from tenor.evals.list_check import normalize

MAX_LINES = T.MAX_LINES
MAX_QUOTES = 4
MIN_QUOTE_WORDS = 3
QUIET = "changed_silently"
# Fates under which a quiet-change line about the item is a false alarm.
NOT_QUIET = {
    "changed_openly": "changed openly",
    "returned_unchanged": "unchanged",
    "reported": "unchanged",
}
N_BOOT = 2000


class Quote(BaseModel):
    """A verbatim run of words from one line of Y."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    line: str
    text: str


class ChangedLine(BaseModel):
    """One line of the quiet-change writer, as its prompt's output format defines it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    sentence: str
    x_bullets: tuple[str, ...]
    before: str = ""
    after: str = ""
    quotes: tuple[Quote, ...]
    searched: tuple[str, ...] = ()


def _line_index(line_id: str, n: int) -> int | None:
    hit = K.LINE_ID.match(line_id)
    return int(hit[1]) if hit and int(hit[1]) < n else None


def check_changed(c: ChangedLine, bullet_ids: Sequence[str], y: B.CueUnit) -> str:
    """``ok``, or the first mechanical problem with a writer line: an empty sentence, unknown
    bullets, no quotes or more than :data:`MAX_QUOTES`, an unknown line, a quote shorter than
    :data:`MIN_QUOTE_WORDS` words, or a quote not found in the line it names."""
    if not c.sentence.strip():
        return "empty sentence"
    if not c.x_bullets or any(b not in bullet_ids for b in c.x_bullets):
        return "x_bullets missing or unknown"
    if not c.quotes:
        return "no quotes"
    if len(c.quotes) > MAX_QUOTES:
        return "too many quotes"
    for q in c.quotes:
        i = _line_index(q.line, len(y.transcript))
        if i is None:
            return f"unknown line {q.line}"
        words = normalize(q.text)
        if len(words.split()) < MIN_QUOTE_WORDS:
            return f"quote too short ({q.line})"
        if words not in normalize(y.transcript[i].text):
            return f"quote not in {q.line}"
    return "ok"


def parse_changed(
    text: str, bullet_ids: Sequence[str], y: B.CueUnit
) -> tuple[list[dict[str, Any]], list[str]]:
    """The writer's lines in order, each ``{"n", "line", "status"}`` (``status`` from
    :func:`check_changed`; lines past :data:`MAX_LINES` are ``over the cap``), and one message
    per raw line that is not a valid object."""
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            c = ChangedLine.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            errors.append(f"line {n}: {str(e).splitlines()[0]}")
            continue
        status = "over the cap" if len(out) >= MAX_LINES else check_changed(c, bullet_ids, y)
        out.append({"n": len(out) + 1, "line": c, "status": status})
    return out, errors


def as_writer_line(c: ChangedLine) -> T.WriterLine:
    """A writer line as the "since last time" checkers see a ``changed`` sentence: the cited
    lines in order of first quote, and the quotes joined by `` ... ``."""
    ids = tuple(dict.fromkeys(q.line for q in c.quotes))
    return T.WriterLine(
        sentence=c.sentence,
        kind="changed",
        x_bullets=c.x_bullets,
        y_lines=ids,
        quote=" ... ".join(q.text for q in c.quotes),
    )


def _proposed(lines: Sequence[ChangedLine]) -> str:
    shown = []
    for n, c in enumerate(lines, start=1):
        quotes = "\n".join(f'   - {q.line}: "{q.text}"' for q in c.quotes)
        shown.append(f"{n}. (about {', '.join(c.x_bullets)}) {c.sentence}\n   quotes:\n{quotes}")
    return "\n".join(shown)


def verify_job(
    x: B.CueUnit, y: B.CueUnit, lines: Sequence[ChangedLine], prompt: str, answer: str
) -> str:
    """The verifier's whole input for one pair: X's notes, all of Y, and the proposed lines
    with their quotes (not the writer's ``before``, ``after`` or search terms). Same arguments
    -> same bytes."""
    parts = [
        f"# Quiet change, verify: {x.meeting_id} -> {y.meeting_id}",
        f"{len(lines)} proposed lines. Write your answer (JSONL, one line per proposed line) to "
        f"`{answer}`. Use only this file.",
        prompt.strip(),
        "---",
        W.ADAPTATION,
        W._x_block(x),
        W._y_block(y),
        "## The proposed lines\n\n" + _proposed(lines),
    ]
    return "\n\n".join(parts) + "\n"


class Closest(BaseModel):
    """A passage of Y the verifier found nearest to raising the change."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    line: str
    says: str = ""


YesNo = Literal["yes", "no"]


class Verification(BaseModel):
    """One verifier line, as the verifier prompt's output format defines it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    line: int
    terms_searched: tuple[str, ...] = ()
    closest: tuple[Closest, ...] = ()
    a_shows_different_version: YesNo
    b_change_raised: YesNo
    c_says_more: YesNo
    verdict: Literal["accept", "reject"]
    reason: str = ""

    @property
    def keeps(self) -> bool:
        """Kept only if the verdict is ``accept`` and the three checks agree with it."""
        return (
            self.verdict == "accept"
            and self.a_shows_different_version == "yes"
            and self.b_change_raised == "no"
            and self.c_says_more == "no"
        )

    @property
    def failed(self) -> list[str]:
        """The checks that failed: ``a`` (no different version shown), ``b`` (the change was
        raised), ``c`` (the line says more than the quotes)."""
        return [
            k
            for k, bad in (
                ("a", self.a_shows_different_version == "no"),
                ("b", self.b_change_raised == "yes"),
                ("c", self.c_says_more == "yes"),
            )
            if bad
        ]


def parse_verifications(text: str, n_lines: int) -> tuple[dict[int, Verification], list[str]]:
    """line number -> the verifier's answer, and one message per problem (invalid object, no
    such line, a second answer for a line, or a verdict that contradicts the three checks)."""
    out: dict[int, Verification] = {}
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            v = Verification.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            errors.append(f"line {n}: {str(e).splitlines()[0]}")
            continue
        if not 1 <= v.line <= n_lines:
            errors.append(f"line {n}: no proposed line {v.line}")
        elif v.line in out:
            errors.append(f"line {n}: second answer for line {v.line}")
        else:
            if (v.verdict == "accept") != (not v.failed):
                errors.append(f"line {n}: verdict {v.verdict} with failed checks {v.failed}")
            out[v.line] = v
    return out, errors


def line_rows(
    pair: str,
    written: Sequence[Mapping[str, Any]],
    verified: Mapping[int, Verification] | None,
    checks: Mapping[str, Mapping[int, T.Verdict]],
    items: Sequence[T.NoteItem],
    fates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One row per proposed line of a pair.

    ``verified`` is numbered over the lines with status ``ok`` (what the verifier was shown),
    ``checks[checker]`` over the kept lines (what the checkers were shown). ``items`` maps the
    writer's bullets to item ids; ``fates`` are the pair's item-fates rows. A row records the
    writer's items and their fates, the verifier's answer, whether the line was kept, both
    checkers' verdicts and the items both name, and ``true`` (kept, both checkers ``yes``)."""
    fate = {r["x_item"]: r for r in fates}
    item_of = {i.bullet_id: i.item_id for i in items}
    rows = []
    k_ok = k_kept = 0
    for w in written:
        c: ChangedLine = w["line"]
        writer_items = [item_of[b] for b in c.x_bullets if b in item_of]
        row: dict[str, Any] = {
            "pair": pair,
            "series": next(iter(fate.values()))["series"] if fate else None,
            "n": w["n"],
            "sentence": c.sentence,
            "status": w["status"],
            "x_bullets": list(c.x_bullets),
            "quotes": [q.model_dump() for q in c.quotes],
            "writer_items": writer_items,
            "writer_item_fates": [fate[i]["fate"] for i in writer_items if i in fate],
            "verdict": None,
            "failed": [],
            "closest": [],
            "verify_reason": "",
            "kept": False,
            **{f"supported_{ch}": None for ch in T.CHECKERS},
            "x_items": [],
            "item_fates": [],
        }
        if w["status"] == "ok":
            k_ok += 1
            v = (verified or {}).get(k_ok)
            if v is not None:
                row.update(
                    verdict=v.verdict,
                    failed=v.failed,
                    closest=[x.model_dump() for x in v.closest],
                    verify_reason=v.reason,
                    kept=v.keeps,
                )
        if row["kept"]:
            k_kept += 1
            got = [checks.get(ch, {}).get(k_kept) for ch in T.CHECKERS]
            for ch, g in zip(T.CHECKERS, got, strict=True):
                row[f"supported_{ch}"] = g.supported if g else None
            if all(got):
                shared = sorted(set(got[0].x_items) & set(got[1].x_items))  # type: ignore[union-attr]
                row["x_items"] = shared
                row["item_fates"] = [fate[i]["fate"] for i in shared if i in fate]
        row["true"] = bool(row["kept"]) and all(
            row[f"supported_{ch}"] == "yes" for ch in T.CHECKERS
        )
        rows.append(row)
    return rows


def false_alarm(row: Mapping[str, Any]) -> str | None:
    """For a kept line: ``changed openly`` or ``unchanged`` when no item the writer cites is a
    quiet change by the fates and one is changed openly (first) or unchanged; else ``None``."""
    fates = list(row["writer_item_fates"])
    if not row["kept"] or QUIET in fates:
        return None
    for f in ("changed_openly", "returned_unchanged", "reported"):
        if f in fates:
            return NOT_QUIET[f]
    return None


def _rate(cells: Mapping[str, list[float]], seed: int) -> dict[str, Any]:
    vals = [(a, b) for _, (a, b) in sorted(cells.items())]
    num, den = sum(a for a, _ in vals), sum(b for _, b in vals)
    ci = ratio_ci(vals, seed, n=N_BOOT)
    return {
        "num": int(num),
        "den": int(den),
        "value": round(num / den, 4) if den else None,
        "ci": list(ci) if ci else None,
    }


def score(
    rows: Sequence[Mapping[str, Any]],
    fates: Sequence[Mapping[str, Any]],
    pairs: Sequence[str],
    seed: int,
) -> dict[str, Any]:
    """Counts over ``pairs``: proposed lines, lines that passed the mechanical check, lines the
    verifier dropped (by failed check), kept lines, kept lines judged true (both checkers
    ``yes``), quiet changes covered (both checkers name the item under a true kept line; also by
    the writer's own bullets), and kept lines that are false alarms by the fates. Intervals
    resample teams (``series``), seeded."""
    mine = [r for r in fates if r["pair"] in set(pairs)]
    series_of = {r["pair"]: r["series"] for r in mine}
    quiet = sorted((r["pair"], r["x_item"]) for r in mine if r["fate"] == QUIET)
    ok = [r for r in rows if r["status"] == "ok"]
    kept = [r for r in rows if r["kept"]]
    true = [r for r in kept if r["true"]]
    by_checkers = {(r["pair"], i) for r in true for i in r["x_items"]}
    by_writer = {(r["pair"], i) for r in true for i in r["writer_items"]}

    def cover(hit: set[tuple[str, str]]) -> dict[str, Any]:
        cells: dict[str, list[float]] = {}
        for p, i in quiet:
            cell = cells.setdefault(str(series_of[p]), [0.0, 0.0])
            cell[0] += float((p, i) in hit)
            cell[1] += 1.0
        return _rate(cells, seed)

    true_cells: dict[str, list[float]] = {}
    for r in kept:
        cell = true_cells.setdefault(str(series_of.get(r["pair"], r["pair"])), [0.0, 0.0])
        cell[0] += float(r["true"])
        cell[1] += 1.0
    dropped: dict[str, int] = {}
    for r in ok:
        if r["verdict"] is not None and not r["kept"]:
            for k in r["failed"] or ["verdict only"]:
                dropped[k] = dropped.get(k, 0) + 1
    alarms = [false_alarm(r) for r in kept]
    return {
        "pairs": len(pairs),
        "teams": len({series_of[p] for p in pairs if p in series_of}),
        "quiet_changes": len(quiet),
        "proposed": len(rows),
        "proposed_ok_mechanically": len(ok),
        "proposed_not_verified": sum(r["verdict"] is None for r in ok),
        "dropped_by_verifier": sum(r["verdict"] is not None and not r["kept"] for r in ok),
        "dropped_by_check": dict(sorted(dropped.items())),
        "kept": len(kept),
        "kept_true": _rate(true_cells, seed),
        "kept_true_one_checker": sum(
            any(r[f"supported_{c}"] == "yes" for c in T.CHECKERS) for r in kept
        ),
        "kept_by_writer_item_fate": _count(
            f for r in kept for f in r["writer_item_fates"] or ["?"]
        ),
        "false_alarms": {
            "changed openly": alarms.count("changed openly"),
            "unchanged": alarms.count("unchanged"),
        },
        "covered": cover(by_checkers),
        "covered_by_writer_bullets": cover(by_writer),
        "proposed_about_quiet_changes": sum(QUIET in r["writer_item_fates"] for r in rows),
        "dropped_about_quiet_changes": sum(
            QUIET in r["writer_item_fates"] and r["verdict"] is not None and not r["kept"]
            for r in ok
        ),
    }


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))
