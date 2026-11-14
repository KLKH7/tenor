"""Follow-through on real meetings: what a public body's next meeting does with what this one
left open (agents, $0; see ``scripts/council_follow.py``).

The AMI study (:mod:`tenor.evals.item_fates`) took the earlier meeting's items from human
annotations. A council meeting has none, so the items are listed by two independent
extractors (prompt ``council_items.v1``) and only items both found are kept
(:func:`match_items`). The fate of each kept item in the next meeting is then classified by
the frozen ``item_fates.v1`` reader, unchanged (:func:`fate_job`; answers are parsed and
scored with :mod:`tenor.evals.item_fates`, resampling whole meeting pairs). Where an item
names a legislative matter, the body's official record (Legistar event items of the next
meeting) says independently whether the matter came back (:func:`official_return`,
:func:`record_agreement`).

A meeting is a list of :class:`Line` from any source: a caption file (:func:`parse_srt`) or a
product's transcript. Nothing here calls an LLM or the network.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from tenor.evals import came_back as K
from tenor.evals import item_fates as F

EXTRACT_PROMPT = "council_items.v1"
FATE_PROMPT = F.PROMPT
KINDS = ("commitment", "info_request", "return", "referral", "scheduled")
EXTRACTORS = ("a", "b")
MAX_LINES = 6  # line ids an extractor may cite per item
MAX_QUOTE_WORDS = 25
NEAR = 3  # two extractors' items can be the same one if cited lines lie this close
MIN_OVERLAP = 0.15  # and their sentences share at least this Jaccard of content words
X_CONTEXT = 3  # lines either side of a cited line in an item's passage
CAME_BACK = ("reported", "returned_unchanged", "changed_openly", "changed_silently")
STAYED_AWAY = ("not_mentioned", "not_expected")

_SRT_TIME = re.compile(r"^(\d+):(\d\d):(\d\d)[,.]\d+\s*-->")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_NORM = re.compile(r"[^a-z0-9]+")
_MATTER = re.compile(r"^\s*([A-Za-z]+)\.?\s*(\d+)\s*$")


class Line(BaseModel):
    """One transcript line: seconds from the start, the speaker label if any, the words."""

    model_config = ConfigDict(frozen=True)

    seconds: float
    speaker: str = ""
    text: str


def parse_srt(text: str) -> list[Line]:
    """Caption cues of an SRT file as lines, in file order. Markup tags are dropped, a cue's
    text lines are joined, and empty cues are left out. Captions carry no speaker."""
    out: list[Line] = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        rows = [r for r in block.split("\n") if r.strip()]
        at = next((i for i, r in enumerate(rows) if _SRT_TIME.match(r.strip())), None)
        if at is None:
            continue
        h, m, s = (int(x) for x in _SRT_TIME.match(rows[at].strip()).groups())  # type: ignore[union-attr]
        words = _SPACE.sub(" ", _TAG.sub("", " ".join(rows[at + 1 :]))).strip()
        if words:
            out.append(Line(seconds=h * 3600 + m * 60 + s, text=words))
    return out


def render(lines: Sequence[Line], i: int, prefix: str = "L") -> str:
    """Line ``i`` as a job file shows it: ``L0012 [04:10] Speaker 2: words``."""
    ln = lines[i]
    clock = f"{int(ln.seconds) // 60}:{int(ln.seconds) % 60:02d}"
    who = f" {ln.speaker}:" if ln.speaker else ""
    return f"{prefix}{i:04d} [{clock}]{who} {ln.text}"


def transcript_block(lines: Sequence[Line], prefix: str = "L") -> str:
    return "```\n" + "\n".join(render(lines, i, prefix) for i in range(len(lines))) + "\n```"


# -- extraction ---------------------------------------------------------------------------------


class Extracted(BaseModel):
    """One extractor line, as ``council_items.v1`` defines it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    kind: Literal["commitment", "info_request", "return", "referral", "scheduled"]
    text: str
    lines: tuple[str, ...]
    quote: str
    matter: str = ""
    due: Literal["next_meeting", "later", "unstated"] = "unstated"

    def indexes(self) -> list[int]:
        return [int(s[1:]) for s in self.lines]


def _norm(text: str) -> str:
    return _NORM.sub(" ", text.lower()).strip()


def extract_job(meeting: str, header: str, lines: Sequence[Line], prompt: str, answer: str) -> str:
    """An extractor's whole input for one meeting. Same arguments -> same bytes."""
    parts = [
        f"# Council follow-up items: {meeting}",
        f"Prompt `{EXTRACT_PROMPT}`. Write your answer (JSONL, possibly empty) to `{answer}`. "
        "Use only this file.",
        prompt.strip(),
        "---",
        f"## Meeting {meeting}\n\n{header}".rstrip(),
        f"## Transcript\n\n{transcript_block(lines)}",
    ]
    return "\n\n".join(parts) + "\n"


def parse_extracted(text: str, lines: Sequence[Line]) -> tuple[list[Extracted], list[str]]:
    """An extractor's valid items in file order, and one message per rejected line. A line is
    rejected if it is not the output format, cites no line, more than :data:`MAX_LINES`, or an
    unknown line, or if its quote is longer than :data:`MAX_QUOTE_WORDS` words or is not found
    (case and punctuation aside) in one of the cited lines."""
    out: list[Extracted] = []
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            e = Extracted.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as err:
            errors.append(f"line {n}: {str(err).splitlines()[0]}")
            continue
        bad = [s for s in e.lines if not (h := K.LINE_ID.match(s)) or int(h[1]) >= len(lines)]
        quote = _norm(e.quote)
        if not e.lines or len(e.lines) > MAX_LINES:
            errors.append(f"line {n}: cites {len(e.lines)} lines (1 to {MAX_LINES} allowed)")
        elif bad:
            errors.append(f"line {n}: unknown lines {bad}")
        elif not quote or len(e.quote.split()) > MAX_QUOTE_WORDS:
            errors.append(f"line {n}: quote is empty or over {MAX_QUOTE_WORDS} words")
        elif not any(quote in _norm(lines[i].text) for i in e.indexes()):
            errors.append(f"line {n}: quote not found in a cited line")
        else:
            out.append(e)
    return out, errors


def _near(a: Extracted, b: Extracted) -> bool:
    return any(abs(i - j) <= NEAR for i in a.indexes() for j in b.indexes())


def _overlap(a: Extracted, b: Extracted) -> float:
    x, y = K.key_words(a.text), K.key_words(b.text)
    return len(x & y) / len(x | y) if x | y else 0.0


def match_items(
    a: Sequence[Extracted], b: Sequence[Extracted]
) -> tuple[list[tuple[int, int]], dict[str, int]]:
    """Pairs ``(index in a, index in b)`` of items both extractors found, and the counts. Two
    items are the same when they cite lines within :data:`NEAR` of each other and their
    sentences share :data:`MIN_OVERLAP` of content words; each item is used once, best overlap
    first (ties: earlier in ``a``, then earlier in ``b``). Pairs are returned in ``a`` order."""
    cands = sorted(
        (
            (-_overlap(x, y), i, j)
            for i, x in enumerate(a)
            for j, y in enumerate(b)
            if _near(x, y) and _overlap(x, y) >= MIN_OVERLAP
        ),
    )
    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, i, j in cands:
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
            pairs.append((i, j))
    counts = {"a": len(a), "b": len(b), "both": len(pairs)}
    return sorted(pairs), counts


class Item(BaseModel):
    """A kept item: found by both extractors. The sentence, kind, matter and due are
    extractor a's; the cited lines are the union of both."""

    model_config = ConfigDict(frozen=True)

    item_id: str
    kind: str
    text: str
    lines: tuple[int, ...]
    matter: str = ""
    due: str = "unstated"
    kind_b: str = ""


def kept_items(a: Sequence[Extracted], b: Sequence[Extracted]) -> tuple[list[Item], dict[str, int]]:
    """The items both extractors found, numbered ``K1..`` in transcript order."""
    pairs, counts = match_items(a, b)
    out = []
    for n, (i, j) in enumerate(pairs, start=1):
        x, y = a[i], b[j]
        out.append(
            Item(
                item_id=f"K{n}",
                kind=x.kind,
                text=x.text,
                lines=tuple(sorted({*x.indexes(), *y.indexes()})),
                matter=x.matter or y.matter,
                due=x.due,
                kind_b=y.kind,
            )
        )
    return out, counts


# -- fates --------------------------------------------------------------------------------------


def passage(lines: Sequence[Line], item: Item) -> str:
    """The item's cited lines of X, marked ``*``, with :data:`X_CONTEXT` lines either side."""
    rows = []
    for lo, hi in K.spans(list(item.lines), X_CONTEXT, len(lines)):
        rows += [("* " if i in item.lines else "  ") + render(lines, i, "X") for i in range(lo, hi)]
    return "\n".join(rows)


def fate_job(
    x: str,
    y: str,
    items: Sequence[Item],
    x_lines: Sequence[Line],
    y_lines: Sequence[Line],
    prompt: str,
    answer: str,
    only: Sequence[str] | None = None,
) -> str:
    """A fate reader's whole input for one meeting pair, in the layout of
    :func:`tenor.evals.item_fates.job_file`; with ``only``, restricted to those items (the
    tie-break job). The prompt is ``item_fates.v1`` as frozen; one note says where the items
    come from, because here no human annotator listed them."""
    shown = [it for it in items if only is None or it.item_id in only]
    scope = (
        f"all {len(items)} items"
        if only is None
        else f"only the {len(shown)} of {len(items)} items listed below"
    )
    parts = [
        f"# Item fates: {x} -> {y}",
        f"Prompt `{FATE_PROMPT}`. Classify {scope}: {', '.join(it.item_id for it in shown)}. "
        f"Write your answer (JSONL, one line per item) to `{answer}`. Use only this file.",
        prompt.strip(),
        "---",
        "## Note on this job\n\nThe two meetings are consecutive meetings of the same public "
        "body, not of a project team. X's items were not written by a human annotator: two "
        "extractors each listed the follow-up items of X on their own, and the items below are "
        "the ones both found. Treat each item's sentence as the record of what X left open, as "
        "the prompt says of the annotator's sentence. Captions may carry no speaker names.",
        f"## Meeting X ({x}): follow-up items\n\nLines marked `*` are the ones the extractors "
        f"cited; the others are context ({X_CONTEXT} lines either side). X line ids start "
        "with `X`.",
        *(f"### {it.item_id} ({it.kind}): {it.text}\n\n```\n{passage(x_lines, it)}\n```"
          for it in shown),
        f"## Meeting Y ({y}): full transcript\n\n{transcript_block(y_lines)}",
    ]  # fmt: skip
    return "\n\n".join(parts) + "\n"


def item_rows(
    pair: str, items: Sequence[Item], answers: Mapping[str, Mapping[str, F.Answer]]
) -> list[dict[str, Any]]:
    """One score row per kept item, in the shape :mod:`tenor.evals.item_fates` scores. The
    resampling unit (``series``) is the meeting pair."""
    rows = []
    for it in items:
        got = {r: answers.get(r, {}).get(it.item_id) for r in (*F.READERS, F.TIEBREAK)}
        fates = {r: (g.fate if g else None) for r, g in got.items()}
        needs = {r: (g.needed_followup if g else None) for r, g in got.items()}
        rows.append(
            {
                "pair": pair,
                "series": pair,
                "position": "",
                "x_item": it.item_id,
                "item_kind": it.kind,
                "matter": it.matter,
                "due": it.due,
                **{f"fate_{r}": f for r, f in fates.items()},
                **{f"needed_{r}": f for r, f in needs.items()},
                "fate": F.final(fates["a"], fates["b"], fates["c"]),
                "needed_followup": F.final(needs["a"], needs["b"], needs["c"]),
                "y_lines": {r: list(g.y_lines) for r, g in got.items() if g},
            }
        )
    return rows


def score(rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    """The item-fates measures over council rows: the distribution of fates (all items, items
    expected back, by kind), silent items per meeting pair, and reader agreement. Intervals
    resample whole meeting pairs."""
    pairs = F.pair_rows(rows)
    expected = [r for r in rows if r["needed_followup"] == "yes"]
    return {
        "items": len(rows),
        "pairs": len(pairs),
        "all_items": F.distribution(rows, seed),
        "needed_followup_yes": F.distribution(expected, seed),
        "by_kind": {
            k: F.distribution([r for r in rows if r["item_kind"] == k], seed) for k in KINDS
        },
        "silent_per_pair": F.pair_shares(pairs, seed),
        "reader_agreement": {
            "fate": F.agreement(rows, "fate", seed),
            "needed_followup": F.agreement(rows, "needed", seed),
        },
    }


# -- the official record --------------------------------------------------------------------------


def matter_key(text: str) -> str | None:
    """A matter number in one spelling: ``"cb 121214"`` and ``"CB121214"`` -> ``"CB 121214"``.
    ``None`` when the text is not a letters-then-digits file number."""
    m = _MATTER.match(text)
    return f"{m[1].upper()} {m[2]}" if m else None


def official_return(matter: str, y_event_items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Whether the next meeting's official agenda (Legistar event items, the ``data`` rows)
    carries the matter, and the action recorded there. ``on_agenda`` is ``None`` when the item
    names no file number, so the record cannot speak."""
    key = matter_key(matter)
    if key is None:
        return {"on_agenda": None, "action": None}
    for row in y_event_items:
        if matter_key(str(row.get("EventItemMatterFile") or "")) == key:
            return {"on_agenda": True, "action": row.get("EventItemActionName")}
    return {"on_agenda": False, "action": None}


def record_agreement(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Reader against record, over rows that carry ``on_agenda`` (True or False): the reader
    agrees when a came-back fate meets a matter on the next agenda, or a stayed-away fate meets
    one that is not. ``unclear`` fates are counted apart."""
    checked = [r for r in rows if r.get("on_agenda") is not None]
    cells = {"came_back_on_agenda": 0, "came_back_off_agenda": 0,
             "away_on_agenda": 0, "away_off_agenda": 0, "unclear": 0}  # fmt: skip
    for r in checked:
        side = "on_agenda" if r["on_agenda"] else "off_agenda"
        if r["fate"] in CAME_BACK:
            cells[f"came_back_{side}"] += 1
        elif r["fate"] in STAYED_AWAY:
            cells[f"away_{side}"] += 1
        else:
            cells["unclear"] += 1
    agree = cells["came_back_on_agenda"] + cells["away_off_agenda"]
    return {"checked": len(checked), "agree": agree, "cells": cells}
