"""The "since last time" writer on annotated AMI pairs: inputs, and scoring against item fates.

``tenor.evals.follow_through`` tested the writer on a product's own notes for one series. Here
the same writer prompts run on the item-fates pairs (``tenor.evals.item_fates``): the earlier
meeting's human-annotated decisions and actions are laid out as that meeting's notes, and the
later meeting is AMI's human transcript. The fates of those items were read earlier by two
readers and a tie-break and are not shown to the writer or to the checkers.

1. :func:`units` turns a pair of AMI meetings into the two units the follow-through code works
   on (X's notes, Y's transcript) and the items under the fates' own ids (D1.., A1..).
2. :func:`writer_job` and :func:`check_job` are the whole input of a writer and of a checker.
   Both carry :data:`ADAPTATION`, which says how the file differs from what the prompts
   describe. The mechanical check of a writer line and the parsing of both outputs are
   ``follow_through``'s, unchanged.
3. :func:`score` counts, over ``follow_through.line_rows`` rows and the fates rows: sentences
   judged true, silent items covered (by kind of item, kind of silence and kind of sentence),
   pairs with and
   without a silent item that got a sentence, and sentences per pair; intervals resample teams.

No LLM and no network: agents read the job files and write JSONL.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from tenor.ami.corpus import AmiMeeting
from tenor.evals import bullet_cues as B
from tenor.evals import came_back as K
from tenor.evals import follow_through as T
from tenor.evals import item_fates as F
from tenor.evals.coverage_e4 import ratio_ci
from tenor.evals.list_check import Line
from tenor.evals.notes_completeness import Bullet

PRODUCT = "annotations"  # stands where a product's name would in the follow-through units
SECTIONS = {"D": "Decisions", "A": "Action items"}
KINDS = {"D": "decision", "A": "task"}
SILENT_KINDS = ("changed_silently", "not_mentioned")
N_BOOT = 2000

ADAPTATION = (
    "**How this file differs from what the instructions describe.** X's notes are not a "
    "product's notes: they are the decisions and actions a human annotator wrote down for "
    "meeting X, laid out as notes under two headings, one bullet per item. Y is a human "
    "transcript of the later meeting, one line per utterance, with speakers named by role (the "
    "same role is the same person in both meetings); it is not a machine transcript."
)

Row = Mapping[str, Any]


def parse_pairs(spec: str, available: Sequence[K.Pair]) -> list[K.Pair]:
    """Pairs named on the command line, in the order given: comma-separated, each written
    ``IS1008a-b`` or ``IS1008a__IS1008b``. Every pair must be in ``available``
    (``came_back.available_pairs``); duplicates are an error."""
    known = {p.name: p for p in available}
    out: list[K.Pair] = []
    for raw in (s.strip() for s in spec.split(",")):
        if not raw:
            continue
        name = raw if "__" in raw else ""
        if not name and len(raw) == 9 and raw[-2] == "-":
            name = f"{raw[:7]}__{raw[:6]}{raw[-1]}"
        if name not in known:
            raise ValueError(f"not an available pair: {raw!r}")
        if known[name] in out:
            raise ValueError(f"pair named twice: {raw!r}")
        out.append(known[name])
    if not out:
        raise ValueError("no pairs named")
    return out


def units(x: AmiMeeting, y: AmiMeeting) -> tuple[B.CueUnit, B.CueUnit, list[T.NoteItem]]:
    """X as notes (bullet ``B<k>`` per annotated item, decisions then actions, in the fates'
    order), Y as a transcript unit (``L0000`` = Y's first dialogue act, speaker = role), and
    the items under the fates' ids."""
    items = F.items(x)
    bullets = tuple(
        Bullet(f"B{k}", SECTIONS[it.item_id[0]], it.text) for k, it in enumerate(items, start=1)
    )
    note_items = [
        T.NoteItem(it.item_id, b.id, KINDS[it.item_id[0]], b.render())
        for it, b in zip(items, bullets, strict=True)
    ]
    lines = tuple(_line(y, i) for i in range(len(y.dialogue_acts)))
    xu = B.CueUnit(x.meeting_id, PRODUCT, "n/a", bullets, (), (), "")
    yu = B.CueUnit(y.meeting_id, PRODUCT, "n/a", (), lines, (), "")
    return xu, yu, note_items


def _line(y: AmiMeeting, i: int) -> Line:
    """Y's dialogue act ``i`` as a transcript line, with the role and clock ``came_back.line``
    prints (``L0042 [03:12] Role: words``)."""
    head = K.line(y, i).split("] ", 1)
    return Line(i, head[1].split(": ", 1)[0], head[0].split("[", 1)[1], y.dialogue_acts[i].text)


def _x_block(x: B.CueUnit) -> str:
    notes = "\n".join(b.render() for b in x.bullets) or "(the notes are empty)"
    return (
        f"## Meeting X ({x.meeting_id}): the notes, every bullet\n\n"
        f"`id [section: heading] text`.\n\n```\n{notes}\n```"
    )


def _y_block(y: B.CueUnit) -> str:
    body = "\n".join(T.y_lines(y))
    return f"## Meeting Y ({y.meeting_id}): the transcript\n\n```\n{body}\n```"


def writer_job(x: B.CueUnit, y: B.CueUnit, prompt_name: str, prompt: str, answer: str) -> str:
    """The writer's whole input for one pair. Same arguments -> same bytes."""
    parts = [
        f"# Since last time: {x.meeting_id} -> {y.meeting_id}",
        f"Prompt `{prompt_name}`. Write your answer (JSONL, at most {T.MAX_LINES} lines, "
        f"possibly empty) to `{answer}`. Use only this file.",
        prompt.strip(),
        "---",
        ADAPTATION,
        _x_block(x),
        _y_block(y),
    ]
    return "\n\n".join(parts) + "\n"


def check_job(
    x: B.CueUnit,
    y: B.CueUnit,
    items: Sequence[T.NoteItem],
    lines: Sequence[T.WriterLine],
    prompt: str,
    answer: str,
) -> str:
    """A checker's whole input: both documents, the item list without fates, and the sentences
    with the evidence each cites. Nothing says which writer wrote them. Same arguments -> same
    bytes."""
    shown = []
    for n, w in enumerate(lines, start=1):
        evidence = (
            f'cites {", ".join(w.y_lines)}; quote: "{w.quote}"'
            if w.kind == "changed"
            else "cites nothing (the claim is that Y never comes back to it)"
        )
        shown.append(f"{n}. ({w.kind}; about {', '.join(w.x_bullets)}) {w.sentence}\n   {evidence}")
    listed = "\n".join(f"- {it.item_id} ({it.kind}): {it.text}" for it in items)
    parts = [
        f"# Since last time, check: {x.meeting_id} -> {y.meeting_id}",
        f"Prompt `{T.CHECK_PROMPT}`. {len(lines)} sentences. Write your answer (JSONL, one line "
        f"per sentence) to `{answer}`. Use only this file.",
        prompt.strip(),
        "---",
        ADAPTATION,
        _x_block(x),
        f"## Meeting X ({x.meeting_id}): its decision and task items\n\n{listed}",
        _y_block(y),
        "## The sentences to check\n\n" + "\n".join(shown),
    ]
    return "\n\n".join(parts) + "\n"


def silent_kind(row: Row) -> str | None:
    """``changed_silently`` or ``not_mentioned`` for a silent item (see
    ``follow_through.is_silent``), else ``None``."""
    return str(row["fate"]) if T.is_silent(row) else None


def _rate(clusters: Mapping[str, tuple[float, float]], seed: int) -> dict[str, Any]:
    """sum(num) / sum(den) over teams, with a CI that resamples teams."""
    cells = [v for _, v in sorted(clusters.items())]
    a, b = sum(x for x, _ in cells), sum(y for _, y in cells)
    ci = ratio_ci(cells, seed, n=N_BOOT)
    return {
        "num": int(a),
        "den": int(b),
        "value": round(a / b, 4) if b else None,
        "ci": list(ci) if ci else None,
    }


def _by_series(
    rows: Sequence[Row], num: Callable[[Row], bool], series: Callable[[Row], str]
) -> dict[str, tuple[float, float]]:
    out: dict[str, list[float]] = {}
    for r in rows:
        cell = out.setdefault(series(r), [0.0, 0.0])
        cell[0] += float(num(r))
        cell[1] += 1.0
    return {k: (a, b) for k, (a, b) in out.items()}


def score(
    lines: Sequence[Row], fates: Sequence[Row], pairs: Sequence[str], seed: int
) -> dict[str, Any]:
    """Counts for one writer over ``pairs``.

    ``lines`` are ``follow_through.line_rows`` rows whose ``unit`` is the pair name; ``fates``
    are the item-fates rows of the same pairs (``pair``, ``series``, ``x_item``, ``item_kind``,
    ``fate``, ``needed_followup``). A sentence is *true* when both checkers say ``yes``. A
    silent item is *covered* when both checkers name it under a true sentence. Intervals are
    percentile bootstrap over teams (``series``), seeded."""
    series_of = {r["pair"]: r["series"] for r in fates}
    silent = {(r["pair"], r["x_item"]): r for r in fates if T.is_silent(r)}
    covered = {(r["unit"], i) for r in lines if r["strict"] for i in r["x_items"]} & set(silent)
    covered_by: dict[str, set[tuple[str, str]]] = {
        k: {(r["unit"], i) for r in lines if r["strict"] and r["kind"] == k for i in r["x_items"]}
        & set(silent)
        for k in ("changed", "not_mentioned")
    }
    checked = [r for r in lines if r["status"] == "ok"]
    per_pair = Counter(r["unit"] for r in lines)
    silent_pairs = {p for p, _ in silent}
    hit_pairs = {p for p, _ in covered}

    def line_series(r: Row) -> str:
        return str(series_of[r["unit"]])

    def cover(keep: Callable[[Row], bool], hit: set[tuple[str, str]] = covered) -> dict[str, Any]:
        rows = [r for k, r in sorted(silent.items()) if keep(r)]
        return _rate(
            _by_series(rows, lambda r: (r["pair"], r["x_item"]) in hit, lambda r: r["series"]),
            seed,
        )

    def pair_rate(group: set[str], num: Callable[[str], bool]) -> dict[str, Any]:
        cells: dict[str, list[float]] = {}
        for p in sorted(group):
            cell = cells.setdefault(series_of[p], [0.0, 0.0])
            cell[0] += float(num(p))
            cell[1] += 1.0
        return _rate({k: (a, b) for k, (a, b) in cells.items()}, seed)

    quiet = set(pairs) - silent_pairs
    true_about = Counter(
        (silent_kind(f) or f["fate"])
        for r in lines
        if r["strict"]
        for f in fates
        if f["pair"] == r["unit"] and f["x_item"] in r["x_items"]
    )
    return {
        "pairs": len(pairs),
        "teams": len({series_of[p] for p in pairs}),
        "sentences": len(lines),
        "sentences_ok_mechanically": len(checked),
        "sentences_by_kind": dict(sorted(Counter(r["kind"] for r in lines).items())),
        "true": _rate(_by_series(checked, lambda r: bool(r["strict"]), line_series), seed),
        "true_one_checker": sum(bool(r["lenient"]) for r in lines),
        "checkers_agree": sum(r["supported_a"] == r["supported_b"] for r in checked),
        "true_by_kind": {
            k: {
                "num": sum(bool(r["strict"]) for r in checked if r["kind"] == k),
                "den": sum(r["kind"] == k for r in checked),
            }
            for k in ("changed", "not_mentioned")
        },
        "true_sentences_by_fate_of_item": dict(sorted(true_about.items())),
        "true_sentences_naming_a_silent_item": sum(
            bool(r["strict"] and r["names_silent_item"]) for r in lines
        ),
        "silent_items": len(silent),
        "covered": cover(lambda r: True),
        "covered_by_item_kind": {
            k: cover(lambda r, k=k: r["item_kind"] == k)  # type: ignore[misc]
            for k in ("decision", "action")
        },
        "covered_by_silent_kind": {
            k: cover(lambda r, k=k: r["fate"] == k)  # type: ignore[misc]
            for k in SILENT_KINDS
        },
        "covered_by_sentence_kind": {
            k: cover(lambda r: True, covered_by[k]) for k in ("changed", "not_mentioned")
        },
        "not_mentioned_items_covered_by_not_mentioned_sentences": cover(
            lambda r: r["fate"] == "not_mentioned", covered_by["not_mentioned"]
        ),
        "pairs_with_a_silent_item": len(silent_pairs),
        "silent_pairs_with_a_sentence": pair_rate(silent_pairs, lambda p: per_pair[p] > 0),
        "silent_pairs_with_a_silent_item_covered": pair_rate(
            silent_pairs, lambda p: p in hit_pairs
        ),
        "pairs_without_a_silent_item": len(quiet),
        "quiet_pairs_with_a_sentence": pair_rate(quiet, lambda p: per_pair[p] > 0),
        "quiet_pairs_with_a_true_sentence": pair_rate(
            quiet, lambda p: any(r["strict"] for r in lines if r["unit"] == p)
        ),
        "sentences_per_pair": {
            str(n): sum(per_pair[p] == n for p in pairs) for n in range(T.MAX_LINES + 1)
        },
    }
