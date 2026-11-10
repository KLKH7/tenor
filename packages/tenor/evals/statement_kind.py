"""Statement kind: do AI notes present one person's opinion or proposal as fact or as decided?

Notes write every bullet in the same confident voice. This test asks how often a bullet the
notes present as a FACT or a DECISION rests on meeting talk that AMI's human annotators labelled
Assess (a value judgement) or Suggest (a proposal), and how often one person's view carries
nobody's name. Ground truth is human: AMI's dialogue-act types (``corpus.DialogueAct.da_type``).

Two agent passes per unit (meeting x product), both from job files, no API:

* **notes** (prompt ``statement_kind_notes.v1``): the notes bullets only, no transcript. Per
  bullet: ``presented_as`` (:data:`PRESENTED`), ``attributed_to``, ``hedged``. The labeller
  cannot see what was said, so its label is how the notes read, not a guess at the truth.
* **support** (prompt ``statement_kind_support.v1``): per bullet with an aligned span (hand
  maps, widened by ``bullet_cues.PAD``: the cue jobs' layout), the 1 to 6 product-transcript
  lines its content rests on, or ``[]``. No judgement about status.

Scoring is arithmetic (:func:`score_bullet`): each support line maps to AMI words through the
speaker-truth alignment (``speaker_naming.align``; only confidently aligned lines are used),
each word to the dialogue act holding it, each act to its human type and true speaker. Minor
acts (Backchannel, Stall, Fragment) are counted but left out of every share. A bullet's
*dominant* type is the :data:`GROUPS` entry with most support words.

Measures, stated before any run (:func:`summarise`):

* **M1** opinion or proposal presented as fact: of bullets presented as ``fact`` and not
  hedged, the share whose support words are >= :data:`AS_SHARE` Assess + Suggest.
* **M2** proposal or opinion presented as decision: of bullets presented as ``decision`` and not
  hedged, the share whose support is >= :data:`AS_SHARE` Assess + Suggest and comes
  >= :data:`ONE_SPEAKER` from one speaker.
* **M3** whose view is lost: of bullets whose support is >= :data:`AS_SHARE` Assess + Suggest
  and >= :data:`ONE_SPEAKER` one speaker, the share with ``attributed_to`` empty.
* **M4** base rates: scored bullets by ``presented_as`` and by dominant human type; hedged rate
  by dominant human type.

CIs are percentile bootstraps that resample meetings (``coverage_e4.ratio_ci``; a meeting's two
products stay together in the pooled figure).

Known limits (also printed by the script): an act type is what the speaker did at that moment,
not what the group did next: a Suggest the group then accepts is still Suggest, so M1/M2 count
"rests on proposal talk", not "was never agreed" (M2's one-speaker condition removes support
where someone else's words are in the picked lines, but only if the picker chose them). Inform
covers reported opinions ("users said it is ugly"). Support lines are an agent's choice.

Pure functions; file I/O lives in ``scripts/statement_kind_agents.py``.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from tenor.evals import bullet_cues as B
from tenor.evals import speaker_naming as S
from tenor.evals.coverage_e4 import ratio_ci

NOTES_PROMPT = "statement_kind_notes.v1"
SUPPORT_PROMPT = "statement_kind_support.v1"
SCORE_VERSION = "statement_kind.v1"
PRESENTED = ("fact", "decision", "task", "opinion", "proposal", "open", "info")
# Human act types grouped for reporting; a tie for dominant goes to the earlier group.
GROUPS = ("inform", "suggest", "assess", "offer", "elicit", "other")
MINOR = "minor"
GROUP_OF = {
    "inf": "inform",
    "sug": "suggest",
    "ass": "assess",
    "off": "offer",
    "el.inf": "elicit",
    "el.sug": "elicit",
    "el.ass": "elicit",
    "el.und": "elicit",
    "bck": MINOR,
    "stl": MINOR,
    "fra": MINOR,
}  # und, be.pos, be.neg, oth and untyped acts are "other"
AS_SHARE = 0.6  # Assess + Suggest share of support words that makes support "opinion/proposal"
ONE_SPEAKER = 0.8  # top speaker's share of support words that makes it "one person's"
MAX_SUPPORT = 6
N_EXAMPLES = 12
PRODUCTS = B.PRODUCTS
SCOPES = (*PRODUCTS, "pooled")
Status = Literal["scored", "not_located", "lost_alignment", "no_content"]


def group_of(da_type: str) -> str:
    """The reporting group of an AMI act type (``""`` and unknown names are ``other``)."""
    return GROUP_OF.get(da_type, "other")


# -- agent answers ----------------------------------------------------------------------------


class NotesLabel(BaseModel):
    """One line of a notes-side answer file (without ``bullet_id``)."""

    model_config = ConfigDict(extra="forbid")

    presented_as: Literal["fact", "decision", "task", "opinion", "proposal", "open", "info"]
    attributed_to: str = ""
    hedged: bool

    @field_validator("hedged", mode="before")
    @classmethod
    def _yes_no(cls, v: Any) -> Any:
        if isinstance(v, str):
            low = v.strip().lower()
            if low in ("yes", "no"):
                return low == "yes"
            raise ValueError("hedged must be yes or no")
        return v

    @field_validator("attributed_to")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class SupportPick(BaseModel):
    """One line of a support-side answer file (without ``bullet_id``)."""

    model_config = ConfigDict(extra="forbid")

    support: list[str]


def check_support(pick: SupportPick, job: B.Job) -> tuple[list[int], list[str]]:
    """The picked line indexes that are usable, in transcript order, and one problem per pick
    that is not: not a ``T`` id, not in the span shown, or beyond :data:`MAX_SUPPORT`."""
    shown = {i for a, b in job.spans for i in range(a, b + 1)}
    shown &= {x.index for x in job.unit.transcript}
    good: list[int] = []
    problems: list[str] = []
    for raw in pick.support:
        try:
            ((a, b),) = B.parse_ranges([raw])
        except ValueError:
            problems.append(f"{raw!r}: not a line id")
            continue
        if a != b or a not in shown:
            problems.append(f"{raw}: not a line of the span")
        elif a not in good:
            good.append(a)
    good.sort()
    if len(good) > MAX_SUPPORT:
        problems.append(f"{len(good)} lines picked; the first {MAX_SUPPORT} kept")
        good = good[:MAX_SUPPORT]
    return good, problems


# -- job files --------------------------------------------------------------------------------


def notes_job_file(unit: B.CueUnit, instructions: str) -> str:
    """The notes-side job file: the instructions and every bullet. No transcript."""
    return (
        f"# Statement-kind jobs (notes): {unit.key} ({len(unit.bullets)} bullets)\n\n"
        f"## Instructions\n\n{instructions}\n\n## Notes\n\n{B.notes_block(unit)}"
    )


def support_job_file(unit: B.CueUnit, jobs: Sequence[B.Job], instructions: str) -> str:
    """The support-side job file, in the cue jobs' layout: instructions, all notes, then per
    bullet its parents and its aligned, widened span."""
    return B.job_file(unit, jobs, instructions, "Statement-kind jobs (support)")


# -- one bullet -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Act:
    """What scoring needs of one AMI dialogue act (index = position in the meeting's acts)."""

    da_type: str
    agent: str


def score_bullet(
    support: Sequence[int], truth: Mapping[int, S.LineTruth], acts: Sequence[Act]
) -> dict[str, Any]:
    """One bullet's support turned into human act types and true speakers.

    ``support`` are product line indexes; only lines whose alignment is confident
    (``speaker_naming.is_confident``) contribute. Words are the aligned AMI words of those
    lines that sit in a dialogue act. ``status``:

    * ``not_located``: no support lines were picked;
    * ``lost_alignment``: lines were picked but none is confidently aligned;
    * ``no_content``: the aligned words are all in minor acts (or in no act);
    * ``scored``: shares, dominant type and speakers are set.
    """
    used = [i for i in support if i in truth and S.is_confident(truth[i])]
    by_type: Counter[str] = Counter()
    by_agent: Counter[str] = Counter()
    for i in used:
        for act_index, n in truth[i].acts.items():
            act = acts[act_index]
            by_type[act.da_type] += n
            if group_of(act.da_type) != MINOR:
                by_agent[act.agent] += n
    groups: Counter[str] = Counter()
    for t, n in by_type.items():
        groups[group_of(t)] += n
    minor = groups.pop(MINOR, 0)
    total = sum(groups.values())
    status: Status
    if not support:
        status = "not_located"
    elif not used:
        status = "lost_alignment"
    elif not total:
        status = "no_content"
    else:
        status = "scored"
    row: dict[str, Any] = {
        "status": status,
        "lines": [f"T{i:04d}" for i in support],
        "lines_used": [f"T{i:04d}" for i in used],
        "words": total,
        "minor_words": minor,
        "by_type": dict(sorted(by_type.items())),
    }
    if status != "scored":
        return row
    top_agent, top_n = sorted(by_agent.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    row |= {
        "shares": {g: round(groups.get(g, 0) / total, 4) for g in GROUPS},
        "dominant": max(GROUPS, key=lambda g: (groups.get(g, 0), -GROUPS.index(g))),
        "as_share": round((groups.get("assess", 0) + groups.get("suggest", 0)) / total, 4),
        "speakers": len(by_agent),
        "top_speaker": top_agent,
        "top_share": round(top_n / total, 4),
    }
    return row


def is_view(row: Mapping[str, Any]) -> bool:
    """Support that is mostly Assess + Suggest (>= :data:`AS_SHARE` of its words)."""
    return bool(row["status"] == "scored" and row["as_share"] >= AS_SHARE)


def is_one_persons_view(row: Mapping[str, Any]) -> bool:
    """:func:`is_view` and >= :data:`ONE_SPEAKER` of the support words from one speaker."""
    return is_view(row) and row["top_share"] >= ONE_SPEAKER


# -- measures ---------------------------------------------------------------------------------

Row = Mapping[str, Any]


def _m1(r: Row) -> tuple[bool, bool]:
    return r["presented_as"] == "fact" and not r["hedged"], is_view(r)


def _m2(r: Row) -> tuple[bool, bool]:
    return r["presented_as"] == "decision" and not r["hedged"], is_one_persons_view(r)


def _m3(r: Row) -> tuple[bool, bool]:
    return is_one_persons_view(r), not r["attributed_to"]


MEASURES = {"M1": _m1, "M2": _m2, "M3": _m3}
MEASURE_LABEL = {
    "M1": "opinion or proposal presented as fact",
    "M2": "one person's proposal or opinion presented as decision",
    "M3": "one person's view with nobody's name on it",
}


def _rate(
    rows: Sequence[Row], meetings: Sequence[str], den: Any, num: Any, seed: int
) -> dict[str, Any]:
    """sum(num) / sum(den) over ``rows`` with a meeting-cluster bootstrap CI. ``den`` and
    ``num`` are predicates on a row; ``num`` only counts where ``den`` holds."""
    pairs = []
    for m in meetings:
        own = [r for r in rows if r["meeting"] == m and den(r)]
        pairs.append((float(sum(1 for r in own if num(r))), float(len(own))))
    n, d = int(sum(p[0] for p in pairs)), int(sum(p[1] for p in pairs))
    ci = ratio_ci(pairs, seed)
    return {
        "num": n,
        "den": d,
        "rate": round(n / d, 4) if d else None,
        "ci": list(ci) if ci else None,
    }


def _scope_summary(rows: Sequence[Row], seed: int) -> dict[str, Any]:
    meetings = sorted({r["meeting"] for r in rows})
    scored = [r for r in rows if r["status"] == "scored"]
    status = Counter(r["status"] for r in rows)

    def rate(den: Any, num: Any) -> dict[str, Any]:
        return _rate(scored, meetings, den, num, seed)

    measures = {}
    for name, f in MEASURES.items():
        measures[name] = rate(lambda r, f=f: f(r)[0], lambda r, f=f: f(r)[1])
    matrix = {
        p: {
            "n": sum(1 for r in scored if r["presented_as"] == p),
            "cells": {
                g: rate(
                    lambda r, p=p: r["presented_as"] == p,
                    lambda r, g=g: r["dominant"] == g,
                )
                for g in GROUPS
            },
        }
        for p in PRESENTED
    }
    return {
        "meetings": len(meetings),
        "bullets": len(rows),
        "status": {
            s: status.get(s, 0) for s in ("scored", "not_located", "lost_alignment", "no_content")
        },
        "measures": measures,
        "matrix": matrix,
        "base_rates": {
            "presented_as": {
                p: rate(lambda r: True, lambda r, p=p: r["presented_as"] == p) for p in PRESENTED
            },
            "dominant": {
                g: rate(lambda r: True, lambda r, g=g: r["dominant"] == g) for g in GROUPS
            },
            "hedged_by_dominant": {
                g: rate(lambda r, g=g: r["dominant"] == g, lambda r: bool(r["hedged"]))
                for g in GROUPS
            },
            "hedged": rate(lambda r: True, lambda r: bool(r["hedged"])),
            "attributed": rate(lambda r: True, lambda r: bool(r["attributed_to"])),
        },
    }


def summarise(rows: Sequence[Row], seed: int) -> dict[str, Any]:
    """Per product and pooled: status counts, M1-M3, the presented_as x dominant-type matrix
    (counts, row shares) and M4's base rates, each rate with a meeting-cluster bootstrap CI.
    ``rows`` are :func:`score_bullet` outputs plus ``meeting``, ``product``, ``presented_as``,
    ``attributed_to`` and ``hedged``."""
    out = {p: _scope_summary([r for r in rows if r["product"] == p], seed) for p in PRODUCTS}
    out["pooled"] = _scope_summary(rows, seed)
    return out


def pick_examples(rows: Sequence[Row], measure: str, seed: int, k: int = N_EXAMPLES) -> list[Row]:
    """Up to ``k`` bullets counted in a measure's numerator, by seeded sample (never by hand),
    returned in unit and bullet order."""
    f = MEASURES[measure]
    hits = [r for r in rows if r["status"] == "scored" and all(f(r))]
    hits.sort(key=lambda r: (r["meeting"], r["product"], int(r["bullet_id"][1:])))
    rng = random.Random(f"{seed}:statement_kind.examples:{measure}")
    chosen = rng.sample(range(len(hits)), min(k, len(hits)))
    return [hits[i] for i in sorted(chosen)]


# -- base rates of the human labels -----------------------------------------------------------


def type_words(meeting: Any) -> Counter[str]:
    """Aligned-token counts of a meeting's AMI words per dialogue-act type (``(no act)`` for
    words outside every act, ``(untyped)`` for acts with no type): the base rate the support
    shares are read against. Tokens are ``speaker_naming.ref_words``, the scoring's own unit."""
    acts = meeting.dialogue_acts
    out: Counter[str] = Counter()
    for w in S.ref_words(meeting):
        out["(no act)" if w.act < 0 else (acts[w.act].da_type or "(untyped)")] += 1
    return out


def has_typed_acts(meeting: Any) -> bool:
    """True when at least one of the meeting's dialogue acts carries a type."""
    return any(a.da_type for a in meeting.dialogue_acts)


def group_shares(words: Mapping[str, int]) -> dict[str, float]:
    """Shares of :data:`GROUPS` over the non-minor words that sit in a dialogue act."""
    groups: Counter[str] = Counter()
    for t, n in words.items():
        if t != "(no act)":
            groups[group_of("" if t == "(untyped)" else t)] += n
    groups.pop(MINOR, None)
    total = sum(groups.values())
    return {g: round(groups.get(g, 0) / total, 4) if total else 0.0 for g in GROUPS}


# -- report text ------------------------------------------------------------------------------


def _fmt(r: Mapping[str, Any]) -> str:
    if r["rate"] is None:
        return f"n/a (0/{r['den']})"
    ci = f" [{r['ci'][0]:.2f}, {r['ci'][1]:.2f}]" if r["ci"] else ""
    return f"{r['rate']:.2f}{ci} ({r['num']}/{r['den']})"


def render(summary: Mapping[str, Any]) -> str:
    """The summary as text: per scope, status counts, M1-M3, the matrix, the base rates."""
    out: list[str] = []
    for scope in SCOPES:
        s = summary[scope]
        st = s["status"]
        out += [
            f"== {scope}: {s['meetings']} meetings, {s['bullets']} bullets with a span ==",
            f"scored {st['scored']}; not located by the picker {st['not_located']}; "
            f"all support lost to alignment {st['lost_alignment']}; "
            f"only minor acts {st['no_content']}",
        ]
        out += [f"{k} {MEASURE_LABEL[k]}: {_fmt(v)}" for k, v in s["measures"].items()]
        out.append("presented_as x dominant human type: count (row %)")
        out.append("\t".join(["presented_as", "n", *GROUPS]))
        for p in PRESENTED:
            row = s["matrix"][p]
            cells = [f"{c['num']} ({100 * (c['rate'] or 0):.0f}%)" for c in row["cells"].values()]
            out.append("\t".join([p, str(row["n"]), *cells]))
        b = s["base_rates"]
        for title, key in (
            ("presented_as", "presented_as"),
            ("dominant human type", "dominant"),
            ("hedged by dominant type", "hedged_by_dominant"),
        ):
            out.append(f"M4 {title}: " + "; ".join(f"{k} {_fmt(v)}" for k, v in b[key].items()))
        out.append(f"M4 hedged overall: {_fmt(b['hedged'])}; attributed: {_fmt(b['attributed'])}")
        out.append("")
    return "\n".join(out)


def example_block(
    row: Row,
    bullet_text: str,
    lines: Mapping[int, str],
    truth: Mapping[int, S.LineTruth],
    acts: Sequence[Act],
    roles: Mapping[str, str],
) -> list[str]:
    """One example for the private file: the bullet, its labels, and each support line with its
    true speaker's role and the human act types of its aligned words."""
    out = [
        f"### {row['meeting']}:{row['product']} {row['bullet_id']}",
        f"- bullet: {bullet_text}",
        f"- notes present it as **{row['presented_as']}**, hedged: "
        f"{'yes' if row['hedged'] else 'no'}, attributed to: {row['attributed_to'] or '(nobody)'}",
        f"- support: Assess+Suggest {row['as_share']:.2f}, dominant {row['dominant']}, "
        f"{row['speakers']} speaker(s), top speaker share {row['top_share']:.2f}",
    ]
    for tid in row["lines"]:
        i = int(tid[1:])
        t = truth.get(i)
        if t is None or not S.is_confident(t):
            out.append(f"  - {tid} (not confidently aligned; unused) {lines.get(i, '')}")
            continue
        types: Counter[str] = Counter()
        for a, n in t.acts.items():
            types[acts[a].da_type or "(untyped)"] += n
        kinds = ", ".join(f"{k} {n}" for k, n in sorted(types.items(), key=lambda kv: -kv[1]))
        role = roles.get(t.speaker or "", t.speaker or "?")
        out.append(f"  - {tid} [{role}; {kinds or 'no act'}] {lines.get(i, '')}")
    return out
