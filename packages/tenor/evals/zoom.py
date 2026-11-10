"""Source pointers: does a tool show the right moment of the meeting for a note bullet?

A head-to-head between a product's shipped "where did this come from" feature (Granola's Zoom
In, read off the app by hand) and a stored-pointer alternative (an agent that, given the notes
and the product's transcript, names the transcript lines a bullet comes from). Both are scored
against the same truth with the same rule; this module is the truth's maths, the selection, the
click sheet, the job file, the parsers and the scorer. No LLM, no network, no file I/O.

**Truth (human, not ours).** AMI's annotators linked every summary sentence (a gold decision,
action or problem) to the dialogue acts that carry it. The E3 reader consensus says which note
bullets state which gold item (``evidence_bullets`` of a ``full`` or ``partial`` verdict). A
bullet's true moment is the union, over its gold items, of their linked dialogue acts. Those
acts are mapped onto the product's own transcript by ``speaker_naming.align`` (word alignment
of the product transcript to AMI's timed words): a product line is a **true line** when it
holds at least one word of a linked act, and a **strong** true line when it holds
:data:`STRONG_WORDS` such words or they are at least half of the line. A bullet is eligible
when it has a strong true line. True time ranges are the true lines' ``[start, end]`` on the
product's clock (seconds from the first transcript line, the ``mm:ss`` the transcript shows),
merged where they touch.

**Rule (the same for both tools).** Every answer is reduced to timestamps on the product's
clock: a picked line counts as its start time, a Zoom In timestamp as itself. A bullet is at
the **right moment** when any of its timestamps lies within :data:`PRIMARY_S` seconds of a
true range (0 inside one); also reported at every :data:`TOLERANCES_S`, for the first
timestamp alone, and as the share of timestamps inside a true range (for line picks: that are
true lines). A bullet a tool gave nothing for is wrong; a sheet row left empty is not measured.
CIs are percentile bootstraps that resample meetings (``coverage_e4.ratio_ci``).

**Chance.** The probability that a timestamp drawn uniformly over the meeting falls within the
tolerance of the bullet's true ranges, averaged over bullets.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tenor.evals.coverage_e4 import ratio_ci
from tenor.evals.stats import binom_two_sided_p

TRUTH_VERSION = "zoom_truth.v1"
PROMPT = "source_pointer.v1"
STRONG_WORDS = 3  # words of a linked act that make a line a strong true line ...
STRONG_SHARE = 0.5  # ... or this share of the line's words
TOLERANCES_S = (10, 30, 60)
PRIMARY_S = 30
MAX_LINES = 6  # most lines a pointer may name
CONFIDENCES = ("high", "medium", "low")
NONE_WORDS = frozenset({"none", "nothing", "n/a", "na", "-", "—"})  # sheet: nothing was shown

_BULLET = re.compile(
    r"^(?P<id>B\d+) \[section: (?P<section>.*?)\](?: \(sub-point of (?P<parent>B\d+)\))? "
    r"?(?P<text>.*)$",
    re.S,
)
_LINE_ID = re.compile(r"^T\d{4,}$")
_STAMP = re.compile(r"^(?:(\d+):)?(\d{1,3}):([0-5]\d)$")
_MEETING = re.compile(r"^## AMI (\S+)")
_CELL = re.compile(r"(?<!\\)\|")

Range = tuple[float, float]


# -- truth ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """One line of the product's transcript (``T<index>``), times in seconds on its clock."""

    id: str
    label: str
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TrueLine:
    id: str
    linked_words: int  # matched words that belong to a linked dialogue act
    n_words: int

    @property
    def strong(self) -> bool:
        return self.linked_words >= STRONG_WORDS or (
            self.n_words > 0 and self.linked_words / self.n_words >= STRONG_SHARE
        )


def true_lines(rows: Iterable[Mapping[str, Any]], links: Iterable[int]) -> list[TrueLine]:
    """The lines of a speaker-truth file (``rows``: its ``lines``) that hold words of the
    dialogue acts ``links`` (act indexes), in transcript order."""
    want = {str(i) for i in links}
    out = []
    for r in rows:
        n = sum(v for k, v in r["acts"].items() if k in want)
        if n:
            out.append(TrueLine(r["line"], n, r["n_words"]))
    return out


def merge_ranges(ranges: Iterable[Range]) -> list[Range]:
    """Sorted ranges with overlapping or touching ones merged."""
    out: list[Range] = []
    for lo, hi in sorted(ranges):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def distance(t: float, ranges: Sequence[Range]) -> float:
    """Seconds from ``t`` to the nearest range (0 inside one; infinite with no ranges)."""
    return min((max(lo - t, t - hi, 0.0) for lo, hi in ranges), default=float("inf"))


def chance(ranges: Sequence[Range], length: float, tolerance: float) -> float:
    """Probability that a timestamp uniform on ``[0, length]`` is within ``tolerance`` seconds
    of ``ranges``."""
    if length <= 0:
        return 0.0
    wide = merge_ranges(
        (max(0.0, lo - tolerance), min(length, hi + tolerance)) for lo, hi in ranges
    )
    return sum(max(0.0, hi - lo) for lo, hi in wide) / length


def mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def parse_bullet(rendered: str) -> dict[str, str | None]:
    """``Bullet.render()`` output back into id, section, parent id (or None) and text."""
    m = _BULLET.match(rendered)
    if m is None:
        raise ValueError(f"not a rendered bullet: {rendered[:60]!r}")
    return {
        "id": m["id"],
        "section": m["section"],
        "parent": m["parent"],
        "text": m["text"].strip(),
    }


def bullet_truth(
    bullet: Mapping[str, str | None],
    gold: Mapping[str, tuple[str, Sequence[int]]],
    rows: Sequence[Mapping[str, Any]],
    lines: Mapping[str, Line],
) -> dict[str, Any]:
    """One bullet's truth record. ``gold``: gold id -> (E3 notes verdict for which this bullet
    is evidence, the item's linked dialogue-act indexes); items without links are skipped."""
    links = sorted({i for _, ls in gold.values() for i in ls})
    found = true_lines(rows, links)
    held = {k for r in rows for k in r["acts"]}
    ranges = merge_ranges((lines[t.id].start, lines[t.id].end) for t in found)
    return {
        **bullet,
        "gold_ids": sorted(g for g, (_, ls) in gold.items() if ls),
        "verdict": "full" if any(v == "full" for v, ls in gold.values() if ls) else "partial",
        "linked_acts": len(links),
        "linked_acts_found": sum(str(i) in held for i in links),
        "true_lines": [t.id for t in found],
        "strong_lines": [t.id for t in found if t.strong],
        "true_ranges_s": [[round(lo, 2), round(hi, 2)] for lo, hi in ranges],
        "true_ranges": [f"{mmss(lo)}-{mmss(hi)}" for lo, hi in ranges],
    }


def eligible(record: Mapping[str, Any]) -> bool:
    """A bullet with at least one strong true line."""
    return bool(record["strong_lines"])


def _bullet_no(bullet_id: str) -> int:
    return int(bullet_id[1:])


def select(
    by_meeting: Mapping[str, Sequence[Mapping[str, Any]]], n: int, seed: int
) -> dict[str, list[Mapping[str, Any]]]:
    """``n`` bullets, as equal per meeting as the meetings allow: within a meeting the
    eligible bullets are shuffled (seeded per meeting) and those with a ``full`` verdict put
    first; meetings then take turns in id order until ``n`` are picked. Returned per meeting
    in notes order. Deterministic."""
    queues: dict[str, list[Mapping[str, Any]]] = {}
    for mid in sorted(by_meeting):
        pool = sorted(by_meeting[mid], key=lambda r: _bullet_no(r["id"]))
        random.Random(f"{seed}:zoom.select:{mid}").shuffle(pool)
        queues[mid] = sorted(pool, key=lambda r: r["verdict"] != "full")
    picked: dict[str, list[Mapping[str, Any]]] = {mid: [] for mid in queues}
    left = n
    while left and any(queues.values()):
        for mid, q in queues.items():
            if left and q:
                picked[mid].append(q.pop(0))
                left -= 1
    return {
        mid: sorted(rows, key=lambda r: _bullet_no(r["id"])) for mid, rows in picked.items() if rows
    }


# -- the click sheet (Zoom In, read off the app) ----------------------------------------------

SHEET_HEAD = (
    "| # | Bullet | Section heading | Bullet text to find | Zoom In timestamps | Remarks |",
    "|---|---|---|---|---|---|",
)


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def render_sheet(truth: Mapping[str, Any], parents: Mapping[str, Mapping[str, str]]) -> str:
    """The click sheet: per meeting, in notes order, the note to open, and per bullet its
    section heading and exact text, with an empty cell for the timestamps Zoom In shows. It
    shows no true line, time or gold item. ``parents``: meeting -> bullet id -> text, for the
    parent of a sub-point."""
    n = sum(len(m["bullets"]) for m in truth["meetings"].values())
    out = [
        "# Zoom In click sheet",
        "",
        f"{n} note bullets in {len(truth['meetings'])} Granola notes. For each bullet: open the "
        "note named in the heading, find the bullet under its section heading, use Zoom In on "
        "it, and type into the `Zoom In timestamps` cell every transcript timestamp the Zoom In "
        "box shows, in the order shown, exactly as shown (`mm:ss`, comma-separated: "
        "`12:05, 12:41`). If Zoom In opens and shows no transcript timestamp, type `none`. "
        "Leave the cell empty only if you could not try the bullet (say why under Remarks). Do "
        "not look anything up in the transcript yourself and do not change any other cell.",
        "",
        "If the app shows times of day instead of `mm:ss` from the start of the transcript, "
        "stop and say so: the scorer reads `mm:ss` (or `h:mm:ss`) from the first transcript line.",
        "",
    ]
    for mid, m in truth["meetings"].items():
        out += [f"## AMI {mid}", "", f'Granola note title: "AMI {mid}"', "", *SHEET_HEAD]
        for k, b in enumerate(m["bullets"], 1):
            text = _cell(b["text"])
            if b.get("parent"):
                under = _cell(parents.get(mid, {}).get(b["parent"], ""))
                text += f" *(sub-point under: {under})*"
            out.append(f"| {k} | {b['id']} | {_cell(b['section'])} | {text} |  |  |")
        out.append("")
    return "\n".join(out)


def parse_timestamp(text: str) -> float:
    """``mm:ss`` or ``h:mm:ss`` as seconds. Raises ``ValueError`` on anything else."""
    m = _STAMP.match(text.strip())
    if m is None:
        raise ValueError(f"not a timestamp: {text!r}")
    return int(m[1] or 0) * 3600 + int(m[2]) * 60 + int(m[3])


def parse_stamps(cell: str) -> list[float] | None:
    """A sheet cell: ``None`` when empty (not measured), ``[]`` for ``none`` (Zoom In showed
    no timestamp), else the timestamps in order."""
    cell = cell.strip().strip("`")
    if not cell:
        return None
    if cell.lower() in NONE_WORDS:
        return []
    return [parse_timestamp(x) for x in re.split(r"[,;\s]+", cell) if x]


@dataclass(frozen=True)
class Shown:
    """What one tool showed for one bullet. ``times`` is None when not measured."""

    meeting: str
    bullet_id: str
    times: tuple[float, ...] | None
    lines: tuple[str, ...] = ()  # pointer picks, parallel to ``times``
    confidence: str | None = None


def parse_sheet(text: str) -> tuple[list[Shown], list[str]]:
    """A filled click sheet, or a JSON list of ``{"meeting", "bullet_id", "timestamps"}``
    (``timestamps`` null = not measured), as :class:`Shown` rows plus the problems found (a
    row with an unreadable timestamp is reported and left not measured)."""
    rows: list[Shown] = []
    errors: list[str] = []
    if text.lstrip().startswith(("[", "{")):
        data = json.loads(text)
        for r in data["rows"] if isinstance(data, dict) else data:
            raw = r.get("timestamps")
            cell = "" if raw is None else (", ".join(raw) or "none")
            rows.append(_shown(r["meeting"], r["bullet_id"], cell, errors))
        return rows, errors
    meeting = None
    for line in text.splitlines():
        if m := _MEETING.match(line):
            meeting = m[1]
            continue
        cells = [c.strip() for c in _CELL.split(line.strip())[1:-1]]
        if meeting and len(cells) >= 5 and re.fullmatch(r"B\d+", cells[1]):
            rows.append(_shown(meeting, cells[1], cells[4], errors))
    return rows, errors


def _shown(meeting: str, bullet_id: str, cell: str, errors: list[str]) -> Shown:
    try:
        times = parse_stamps(cell)
    except ValueError as e:
        errors.append(f"{meeting} {bullet_id}: {e}")
        times = None
    return Shown(meeting, bullet_id, None if times is None else tuple(times))


# -- the pointer job and its answers ----------------------------------------------------------


def job_file(
    meeting_id: str,
    prompt: str,
    bullets: Sequence[str],
    lines: Sequence[Line],
    ask: Sequence[str],
    out_path: str,
) -> str:
    """One pointer job: the prompt, every note bullet as rendered for E3, the product's whole
    transcript with T ids and its own timestamps, and the bullet ids to answer. No gold item,
    no AMI passage, no alignment, no truth."""
    return "\n".join(
        [
            prompt.rstrip(),
            "",
            "---",
            "",
            f"# Job: {meeting_id}",
            "",
            f"Write your answer to `{out_path}`: one JSON object per line, one for each of the "
            f'{len(ask)} bullets listed under "Bullets to answer", nothing else in the file.',
            "",
            "## Notes",
            "",
            "Every bullet of the notes: `id [section: heading] (sub-point of parent) text`. "
            "Read a sub-point together with its parent.",
            "",
            "```",
            *bullets,
            "```",
            "",
            "## Transcript",
            "",
            "```",
            *(f"[{x.id} {x.label} {mmss(x.start)}] {' '.join(x.text.split())}" for x in lines),
            "```",
            "",
            "## Bullets to answer",
            "",
            ", ".join(ask),
            "",
        ]
    )


def parse_pointers(
    text: str, meeting: str, ask: Iterable[str], lines: Mapping[str, float]
) -> tuple[list[Shown], list[str]]:
    """A pointer run's JSONL for one meeting as :class:`Shown` rows for every bullet in
    ``ask`` (a bullet with no usable answer gets no times: wrong, not unmeasured), plus the
    problems found. ``lines``: T id -> start seconds. Unknown line ids are dropped, picks past
    :data:`MAX_LINES` are cut, the first answer for a bullet wins."""
    want = list(ask)
    got: dict[str, Shown] = {}
    errors: list[str] = []
    for k, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
            bid, picks = str(rec["bullet_id"]), [str(x) for x in rec["lines"]]
        except (ValueError, KeyError, TypeError) as e:
            errors.append(f"{meeting} line {k}: unreadable ({type(e).__name__})")
            continue
        if bid not in want:
            errors.append(f"{meeting} {bid}: not asked")
            continue
        if bid in got:
            errors.append(f"{meeting} {bid}: answered twice, first kept")
            continue
        bad = [x for x in picks if not _LINE_ID.match(x) or x not in lines]
        if bad:
            errors.append(f"{meeting} {bid}: unknown lines {bad}")
        keep = list(dict.fromkeys(x for x in picks if x in lines))
        if len(keep) > MAX_LINES:
            errors.append(f"{meeting} {bid}: {len(keep)} lines, first {MAX_LINES} kept")
            keep = keep[:MAX_LINES]
        conf = rec.get("confidence")
        if conf not in CONFIDENCES:
            errors.append(f"{meeting} {bid}: confidence {conf!r}")
            conf = None
        got[bid] = Shown(meeting, bid, tuple(lines[x] for x in keep), tuple(keep), conf)
    for bid in want:
        if bid not in got:
            errors.append(f"{meeting} {bid}: no answer")
    return [got.get(b, Shown(meeting, b, ())) for b in want], errors


# -- scoring ----------------------------------------------------------------------------------


def score_bullet(shown: Shown, truth: Mapping[str, Any]) -> dict[str, Any] | None:
    """One bullet's row (None when not measured): number of timestamps, right moment at each
    tolerance (any timestamp, and the first one), and how many timestamps are inside a true
    range (line picks: are true lines)."""
    if shown.times is None:
        return None
    ranges = [(float(lo), float(hi)) for lo, hi in truth["true_ranges_s"]]
    dist = [distance(t, ranges) for t in shown.times]
    true = set(truth["true_lines"])
    inside = sum(x in true for x in shown.lines) if shown.lines else sum(d == 0 for d in dist)
    row: dict[str, Any] = {
        "meeting": shown.meeting,
        "bullet_id": shown.bullet_id,
        "verdict": truth["verdict"],
        "confidence": shown.confidence,
        "n": len(dist),
        "inside": inside,
        "nearest_s": round(min(dist), 1) if dist else None,
    }
    for tol in TOLERANCES_S:
        row[f"hit{tol}"] = any(d <= tol for d in dist)
        row[f"first{tol}"] = bool(dist) and dist[0] <= tol
        row[f"within{tol}"] = sum(d <= tol for d in dist)
    return row


def score_all(
    shown: Iterable[Shown], truth: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows for every selected bullet a tool was measured on, in truth order, and the
    selected bullets it was not measured on (``meeting bullet``). Answers for bullets outside
    the truth set are ignored."""
    by_key = {(s.meeting, s.bullet_id): s for s in shown}
    rows, missing = [], []
    for mid, m in truth["meetings"].items():
        for b in m["bullets"]:
            s = by_key.get((mid, b["id"]))
            row = score_bullet(s, b) if s is not None else None
            if row is None:
                missing.append(f"{mid} {b['id']}")
            else:
                rows.append(row)
    return rows, missing


def _rate(
    rows: Sequence[Mapping[str, Any]], num: str, den: str | None, seed: int
) -> dict[str, Any]:
    """sum(num) / sum(den) (``den`` None: per bullet) with a meeting-cluster bootstrap CI."""
    pairs = [
        (
            float(sum(r[num] for r in rows if r["meeting"] == m)),
            float(sum(r[den] if den else 1 for r in rows if r["meeting"] == m)),
        )
        for m in sorted({r["meeting"] for r in rows})
    ]
    n, d = sum(p[0] for p in pairs), sum(p[1] for p in pairs)
    ci = ratio_ci(pairs, seed)
    return {
        "num": int(n),
        "den": int(d),
        "rate": round(n / d, 4) if d else None,
        "ci": list(ci) if ci else None,
    }


def summarise(rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    """A tool's report over its measured bullets."""
    out: dict[str, Any] = {"bullets": len(rows), "meetings": len({r["meeting"] for r in rows})}
    for tol in TOLERANCES_S:
        out[f"right_moment_{tol}s"] = _rate(rows, f"hit{tol}", None, seed)
        out[f"first_timestamp_{tol}s"] = _rate(rows, f"first{tol}", None, seed)
    out["nothing_shown"] = sum(r["n"] == 0 for r in rows)
    out["timestamps_per_bullet"] = round(sum(r["n"] for r in rows) / len(rows), 2) if rows else None
    out["timestamps_inside_true"] = _rate(rows, "inside", "n", seed)
    out[f"timestamps_within_{PRIMARY_S}s"] = _rate(rows, f"within{PRIMARY_S}", "n", seed)
    for field in ("confidence", "verdict", "meeting"):
        values = sorted({r[field] for r in rows if r[field] is not None})
        out[f"by_{field}"] = {
            v: _rate([r for r in rows if r[field] == v], f"hit{PRIMARY_S}", None, seed)
            for v in values
        }
    return out


def chance_baseline(truth: Mapping[str, Any]) -> dict[str, float]:
    """Mean over the selected bullets of :func:`chance`, per tolerance."""
    per: dict[int, list[float]] = {tol: [] for tol in TOLERANCES_S}
    for m in truth["meetings"].values():
        for b in m["bullets"]:
            ranges = [(float(lo), float(hi)) for lo, hi in b["true_ranges_s"]]
            for tol in TOLERANCES_S:
                per[tol].append(chance(ranges, float(m["length_s"]), tol))
    return {f"{tol}s": round(sum(v) / len(v), 4) if v else 0.0 for tol, v in per.items()}


def compare(
    pointers: Sequence[Mapping[str, Any]], zoomin: Sequence[Mapping[str, Any]], seed: int
) -> dict[str, Any]:
    """Paired table on the bullets both tools were measured on: per tolerance both right,
    only pointers, only Zoom In, neither, the exact two-sided sign test on the discordant
    pairs, and the difference in right-moment rate with a meeting-cluster bootstrap CI."""
    a = {(r["meeting"], r["bullet_id"]): r for r in pointers}
    b = {(r["meeting"], r["bullet_id"]): r for r in zoomin}
    keys = [k for k in a if k in b]
    out: dict[str, Any] = {"bullets": len(keys), "meetings": len({k[0] for k in keys})}
    for tol in TOLERANCES_S:
        pa = [bool(a[k][f"hit{tol}"]) for k in keys]
        pb = [bool(b[k][f"hit{tol}"]) for k in keys]
        only_a = sum(x and not y for x, y in zip(pa, pb, strict=True))
        only_b = sum(y and not x for x, y in zip(pa, pb, strict=True))
        both = sum(x and y for x, y in zip(pa, pb, strict=True))
        out[f"{tol}s"] = {
            "both": both,
            "only_pointers": only_a,
            "only_zoomin": only_b,
            "neither": len(keys) - both - only_a - only_b,
            "sign_test_p": binom_two_sided_p(only_a, only_a + only_b),
            "difference": _diff(keys, pa, pb, seed),
        }
    return out


def _diff(
    keys: Sequence[tuple[str, str]], pa: Sequence[bool], pb: Sequence[bool], seed: int
) -> dict[str, Any]:
    """Pointers minus Zoom In, meetings resampled (a meeting's bullets stay together)."""
    if not keys:
        return {"rate": None, "ci": None}
    meetings = sorted({k[0] for k in keys})
    per = {m: [0, 0] for m in meetings}
    for k, x, y in zip(keys, pa, pb, strict=True):
        per[k[0]][0] += int(x) - int(y)
        per[k[0]][1] += 1
    rng = random.Random(f"{seed}:zoom.diff:{len(meetings)}")
    stats = []
    for _ in range(2000):
        draw = rng.choices(meetings, k=len(meetings))
        stats.append(sum(per[m][0] for m in draw) / sum(per[m][1] for m in draw))
    stats.sort()
    point = sum(v[0] for v in per.values()) / len(keys)
    return {
        "rate": round(point, 4),
        "ci": [round(stats[50], 4), round(stats[1949], 4)],
    }
