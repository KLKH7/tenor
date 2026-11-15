"""A before-meeting brief against the after-meeting "since last time" line: how much goes stale.

A pre-meeting brief (a stand-in for a product's meeting briefs, not any product itself) is
written from the earlier meeting's notes alone (X's annotated decisions and actions laid out
as notes, as the since-last-time writers get them): at most :data:`MAX_BULLETS` bullets on what
is still open from last time. It cannot see the later meeting (Y). Readers then read each
bullet against Y's human transcript and say what Y did with it, with the outcome definitions
of the item-fates readers.

1. :func:`brief_job` is the brief writer's whole input; :func:`parse_brief` validates its
   output.
2. :func:`fate_job` is a fate reader's whole input (the bullets and Y, nothing else: no X, no
   fates, no word on who wrote the bullets); :func:`parse_fates` reads the answer.
   :func:`naming_job` asks a separate reader which of X's items each bullet names (prompt
   ``sentence_names_item.v1``); :func:`parse_naming` reads that answer.
3. :func:`resolve` takes the fate readers a and b agree on, else c's, else ``unclear``;
   :func:`bullet_rows` joins everything per bullet; :func:`score` counts, with intervals that
   resample teams: bullets the meeting dealt with anyway, bullets that went quiet, silent
   items the brief named against those the since-last-time line named, bullets shown in pairs
   where nothing went quiet against lines, and agreement with the earlier item fates.

No LLM and no network: agents read the job files and write JSONL.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from tenor.evals import bullet_cues as B
from tenor.evals import follow_through as T
from tenor.evals.coverage_e4 import ratio_ci

BRIEF_PROMPT = "brief_before.v1"
FATE_PROMPT = "brief_bullet_fate.v1"
NAMING_PROMPT = "sentence_names_item.v1"
MAX_BULLETS = 3
READERS = ("a", "b")
FATES = ("reported", "returned_unchanged", "changed_openly", "changed_silently", "not_mentioned")
# What the meeting did with a bullet, in three groups.
GROUP = {
    "reported": "dealt_with",
    "returned_unchanged": "dealt_with",
    "changed_openly": "changed_openly",
    "changed_silently": "quiet",
    "not_mentioned": "quiet",
    # An item-fates outcome only: the item was about X itself and Y does not come back to it.
    "not_expected": "quiet",
}
GROUPS = ("dealt_with", "changed_openly", "quiet")
N_BOOT = 2000

Row = Mapping[str, Any]

ADAPTATION = (
    "**How this file differs from what the instructions describe.** The notes are not a "
    "product's notes: they are the decisions and actions a human annotator wrote down for "
    "meeting X, laid out as notes under two headings, one bullet per item. Speakers are named "
    "by role (project manager, industrial designer and so on); the same role is the same person "
    "in every meeting of the team."
)
FATE_ADAPTATION = (
    "**About the transcript.** Y is a human transcript, one line per utterance, with speakers "
    "named by role (project manager, industrial designer and so on); the bullets name people "
    "by the same roles."
)


class BriefBullet(BaseModel):
    """One bullet of the brief, as the brief prompt's output format defines it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    bullet: str
    x_bullets: tuple[str, ...]


class BulletFate(BaseModel):
    """A fate reader's answer for one bullet."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    bullet: int
    fate: Literal[
        "reported", "returned_unchanged", "changed_openly", "changed_silently", "not_mentioned"
    ]
    expect_to_hear: Literal["yes", "no"]
    y_lines: tuple[str, ...] = ()
    what_changed: str = ""
    reason: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"


class Naming(BaseModel):
    """A naming reader's answer for one X item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    x_item: str
    named_by: tuple[int, ...] = ()
    reason: str = ""


def _x_block(x: B.CueUnit) -> str:
    notes = "\n".join(b.render() for b in x.bullets) or "(the notes are empty)"
    return (
        f"## Meeting X ({x.meeting_id}): the notes, every bullet\n\n"
        f"`id [section: heading] text`.\n\n```\n{notes}\n```"
    )


def brief_job(x: B.CueUnit, prompt_name: str, prompt: str, answer: str) -> str:
    """The brief writer's whole input: the prompt and X's notes, nothing of Y. Same
    arguments -> same bytes."""
    parts = [
        f"# Before the meeting: brief from {x.meeting_id}",
        f"Prompt `{prompt_name}`. Write your answer (JSONL, at most {MAX_BULLETS} lines, "
        f"possibly empty) to `{answer}`. Use only this file.",
        prompt.strip(),
        "---",
        ADAPTATION,
        _x_block(x),
    ]
    return "\n\n".join(parts) + "\n"


def parse_brief(text: str, bullet_ids: Sequence[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """The brief's bullets in order, each ``{"n", "bullet", "status"}`` (``ok``, ``empty``,
    ``x_bullets missing or unknown`` or ``over the cap``), and one message per unreadable raw
    line."""
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    for k, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            b = BriefBullet.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            errors.append(f"line {k}: {str(e).splitlines()[0]}")
            continue
        if len(out) >= MAX_BULLETS:
            status = "over the cap"
        elif not b.bullet.strip():
            status = "empty"
        elif not b.x_bullets or any(i not in bullet_ids for i in b.x_bullets):
            status = "x_bullets missing or unknown"
        else:
            status = "ok"
        out.append({"n": len(out) + 1, "bullet": b, "status": status})
    return out, errors


def _numbered(bullets: Sequence[tuple[int, str]]) -> str:
    return "\n".join(f"{n}. {text}" for n, text in bullets)


def fate_job(y: B.CueUnit, bullets: Sequence[tuple[int, str]], prompt: str, answer: str) -> str:
    """A fate reader's whole input: the numbered bullets and Y's transcript. No X, no fates,
    nothing on who wrote the bullets. Same arguments -> same bytes."""
    body = "\n".join(T.y_lines(y))
    parts = [
        f"# What the meeting did with a reminder: {y.meeting_id}",
        f"Prompt `{FATE_PROMPT}`. {len(bullets)} bullets. Write your answer (JSONL, one line "
        f"per bullet) to `{answer}`. Use only this file.",
        prompt.strip(),
        "---",
        FATE_ADAPTATION,
        f"## The bullets\n\n{_numbered(bullets)}",
        f"## Meeting Y ({y.meeting_id}): the transcript\n\n```\n{body}\n```",
    ]
    return "\n\n".join(parts) + "\n"


def parse_fates(text: str, numbers: Sequence[int]) -> tuple[dict[int, BulletFate], list[str]]:
    """A fate reader's answers by bullet number, and problems (unreadable lines, unknown or
    repeated numbers, missing bullets)."""
    out: dict[int, BulletFate] = {}
    errors: list[str] = []
    for k, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            f = BulletFate.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            errors.append(f"line {k}: {str(e).splitlines()[0]}")
            continue
        if f.bullet not in numbers:
            errors.append(f"line {k}: unknown bullet {f.bullet}")
        elif f.bullet in out:
            errors.append(f"line {k}: bullet {f.bullet} answered twice")
        else:
            out[f.bullet] = f
    errors += [f"no answer for bullet {n}" for n in numbers if n not in out]
    return out, errors


def naming_job(
    pair: str, items: Sequence[tuple[str, str]], bullets: Sequence[str], prompt: str, answer: str
) -> str:
    """A naming reader's whole input: X's items with ids and the brief's bullets as numbered
    sentences. Same arguments -> same bytes."""
    listed = "\n".join(f"- {i}: {text}" for i, text in items)
    parts = [
        f"# Does a sentence name the item: {pair}",
        f"Prompt `{NAMING_PROMPT}`. {len(items)} items. Write your answer (JSONL, one line per "
        f"item) to `{answer}`. Use only this file.",
        prompt.strip(),
        "---",
        f"## Items of meeting X\n\n{listed}",
        f"## The sentences\n\n{_numbered(list(enumerate(bullets, start=1)))}",
    ]
    return "\n\n".join(parts) + "\n"


def parse_naming(
    text: str, item_ids: Sequence[str], n_bullets: int
) -> tuple[dict[int, list[str]], list[str]]:
    """The items each bullet names (bullet number -> item ids in X's order), and problems."""
    named: dict[str, tuple[int, ...]] = {}
    errors: list[str] = []
    for k, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            a = Naming.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            errors.append(f"line {k}: {str(e).splitlines()[0]}")
            continue
        if a.x_item not in item_ids:
            errors.append(f"line {k}: unknown item {a.x_item}")
            continue
        bad = [n for n in a.named_by if not 1 <= n <= n_bullets]
        if bad:
            errors.append(f"line {k}: unknown sentence {bad}")
        named[a.x_item] = tuple(n for n in a.named_by if 1 <= n <= n_bullets)
    errors += [f"no answer for item {i}" for i in item_ids if i not in named]
    return {
        n: [i for i in item_ids if n in named.get(i, ())] for n in range(1, n_bullets + 1)
    }, errors


def resolve(a: str | None, b: str | None, c: str | None) -> str:
    """The value a and b agree on, else c's, else ``unclear``."""
    if a is not None and a == b:
        return a
    return c if c is not None else "unclear"


def needs_tiebreak(a: BulletFate | None, b: BulletFate | None) -> bool:
    """Whether readers a and b differ on a bullet's fate or on expect_to_hear."""
    if a is None or b is None:
        return True
    return a.fate != b.fate or a.expect_to_hear != b.expect_to_hear


def bullet_rows(
    pair: str,
    series: str,
    split: str,
    bullets: Sequence[tuple[int, str, Sequence[str]]],
    answers: Mapping[str, Mapping[int, BulletFate]],
    named: Mapping[int, Sequence[str]],
    fates: Sequence[Row],
) -> list[dict[str, Any]]:
    """One row per valid bullet of a pair. ``bullets`` are ``(n, text, cited item ids)``;
    ``answers`` the fate readers' answers by letter (a, b, and c for tie-breaks); ``named``
    the items the naming reader says each bullet names; ``fates`` the item-fates rows of the
    pair."""
    by_item = {r["x_item"]: r for r in fates}
    rows = []
    for n, text, cited in bullets:
        got = {k: answers.get(k, {}).get(n) for k in ("a", "b", "c")}
        fate = resolve(*(g.fate if g else None for g in got.values()))
        expect = resolve(*(g.expect_to_hear if g else None for g in got.values()))
        items = list(named.get(n, ()))
        rows.append(
            {
                "pair": pair,
                "series": series,
                "split": split,
                "n": n,
                "bullet": text,
                "cited_items": list(cited),
                "named_items": items,
                **{f"fate_{k}": g.fate if g else None for k, g in got.items()},
                **{f"expect_{k}": g.expect_to_hear if g else None for k, g in got.items()},
                "fate": fate,
                "group": GROUP.get(fate, "unclear"),
                "expect_to_hear": expect,
                "item_fates": {i: by_item[i]["fate"] for i in items if i in by_item},
                "names_silent_item": any(i in by_item and T.is_silent(by_item[i]) for i in items),
            }
        )
    return rows


def _rate(cells: Mapping[str, tuple[float, float]], seed: int) -> dict[str, Any]:
    """sum(num) / sum(den) over teams, with a percentile CI that resamples teams."""
    vals = [v for _, v in sorted(cells.items())]
    a, b = sum(x for x, _ in vals), sum(y for _, y in vals)
    ci = ratio_ci(vals, seed, n=N_BOOT)
    return {
        "num": int(a),
        "den": int(b),
        "value": round(a / b, 4) if b else None,
        "ci": list(ci) if ci else None,
    }


def _share(
    rows: Sequence[Row], num: Callable[[Row], bool], seed: int, team: str = "series"
) -> dict[str, Any]:
    cells: dict[str, list[float]] = {}
    for r in rows:
        cell = cells.setdefault(str(r[team]), [0.0, 0.0])
        cell[0] += float(num(r))
        cell[1] += 1.0
    return _rate({k: (x, y) for k, (x, y) in cells.items()}, seed)


def item_group(fates: Sequence[str]) -> str:
    """The group of a bullet as the earlier item fates have it: the items' common group, or
    ``mixed``."""
    groups = {GROUP[f] for f in fates}
    return groups.pop() if len(groups) == 1 else "mixed"


def score(
    bullets: Sequence[Row],
    fates: Sequence[Row],
    lines: Sequence[Row],
    pairs: Sequence[str],
    seed: int,
) -> dict[str, Any]:
    """Counts for the brief over ``pairs``, against the since-last-time line.

    ``bullets`` are :func:`bullet_rows` rows; ``fates`` the item-fates rows of the same pairs;
    ``lines`` the line rows of the since-last-time run on the same pairs
    (``follow_through.line_rows``: ``unit``, ``strict``, ``x_items``). A silent item is named
    by the brief when the naming reader puts it under a bullet, and covered by the line when
    both checkers name it under a true sentence (as ``since_writer.score`` counts it).
    Intervals are percentile bootstrap over teams (``series``), seeded."""
    series_of = {r["pair"]: r["series"] for r in fates}
    silent = {(r["pair"], r["x_item"]): r for r in fates if T.is_silent(r)}
    named = {(r["pair"], i) for r in bullets for i in r["named_items"]}
    cited = {(r["pair"], i) for r in bullets for i in r["cited_items"]}
    covered = {(r["unit"], i) for r in lines if r["strict"] for i in r["x_items"]}
    quiet_pairs = sorted(set(pairs) - {p for p, _ in silent})
    per_pair_bullets = Counter(r["pair"] for r in bullets)
    per_pair_lines = Counter(r["unit"] for r in lines)
    resolved = [r for r in bullets if r["group"] != "unclear"]
    silent_rows = [dict(r, key=(p, i)) for (p, i), r in sorted(silent.items())]

    def per_pair(counts: Counter[str], group: Sequence[str]) -> dict[str, Any]:
        return {
            "total": sum(counts[p] for p in group),
            "pairs_with_any": _share(
                [{"series": series_of[p], "p": p} for p in group],
                lambda r: counts[r["p"]] > 0,
                seed,
            ),
            "per_pair": {str(k): sum(counts[p] == k for p in group) for k in range(4)},
        }

    def single(r: Row) -> bool:
        return len(r["named_items"]) == 1 and r["named_items"][0] in r["item_fates"]

    agree_rows = [r for r in resolved if r["item_fates"]]
    exact = [r for r in resolved if single(r)]
    ab = [r for r in bullets if r["fate_a"] and r["fate_b"]]
    return {
        "pairs": len(pairs),
        "teams": len({series_of[p] for p in pairs}),
        "bullets": len(bullets),
        "bullets_per_pair": {
            str(k): sum(per_pair_bullets[p] == k for p in pairs) for k in range(4)
        },
        "unclear": len(bullets) - len(resolved),
        "fates": dict(sorted(Counter(r["fate"] for r in bullets).items())),
        "dealt_with": _share(resolved, lambda r: r["group"] == "dealt_with", seed),
        "came_up_anyway": _share(resolved, lambda r: r["group"] != "quiet", seed),
        "changed_openly": _share(resolved, lambda r: r["group"] == "changed_openly", seed),
        "quiet": _share(resolved, lambda r: r["group"] == "quiet", seed),
        "quiet_and_expected": _share(
            resolved, lambda r: r["group"] == "quiet" and r["expect_to_hear"] == "yes", seed
        ),
        "expect_to_hear_yes": sum(r["expect_to_hear"] == "yes" for r in bullets),
        "readers_agree_fate": {
            "n": len(ab),
            "same_fate": sum(r["fate_a"] == r["fate_b"] for r in ab),
            "same_group": sum(GROUP[r["fate_a"]] == GROUP[r["fate_b"]] for r in ab),
            "same_expect": sum(r["expect_a"] == r["expect_b"] for r in ab),
        },
        "silent_items": len(silent),
        "silent_named_by_brief": _share(silent_rows, lambda r: r["key"] in named, seed),
        "silent_cited_by_brief": _share(silent_rows, lambda r: r["key"] in cited, seed),
        "silent_covered_by_line": _share(silent_rows, lambda r: r["key"] in covered, seed),
        "quiet_pairs": len(quiet_pairs),
        "quiet_pairs_brief": per_pair(per_pair_bullets, quiet_pairs),
        "quiet_pairs_line": per_pair(per_pair_lines, quiet_pairs),
        "bullets_naming_no_item": sum(not r["named_items"] for r in bullets),
        "naming_matches_citation": sum(
            set(r["named_items"]) == set(r["cited_items"]) for r in bullets
        ),
        "cross_check": {
            "bullets_with_item_fates": len(agree_rows),
            "same_group": sum(
                r["group"] == item_group(list(r["item_fates"].values())) for r in agree_rows
            ),
            "mixed_items": sum(
                item_group(list(r["item_fates"].values())) == "mixed" for r in agree_rows
            ),
            "single_item_bullets": len(exact),
            "single_same_group": sum(
                r["group"] == GROUP[next(iter(r["item_fates"].values()))] for r in exact
            ),
            "single_same_fate": sum(
                r["fate"] == next(iter(r["item_fates"].values())) for r in exact
            ),
            "confusion": dict(
                sorted(
                    Counter(
                        f"{item_group(list(r['item_fates'].values()))}->{r['group']}"
                        for r in agree_rows
                    ).items()
                )
            ),
        },
    }
