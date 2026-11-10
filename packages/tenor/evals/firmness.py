"""Firmness check: how firmly each notes bullet records what its stretch of the meeting settled.

Why: agent bullet cues (``bullet_cue.v2``, ``docs/tracks/NOTES.md`` "Bullet cues") recovered
4/104 missing gold, mostly because the generator called softened items "stated" (a decision
recorded as "to investigate", an owner missing or wrong, "discussed" vs agreed); 61 of the 104
are partial, so the topic is in the notes and only its firmness or owner is off. This feature
asks per bullet for the status on both sides and flags only when the meeting is firmer or names
an owner the notes lack or differ on.

* Jobs are the cue jobs (:func:`tenor.evals.bullet_cues.jobs_for`, same notes block, bullet
  block and widened span), answered with ``prompts/firmness_check.v1.md``; one JSON line per
  bullet in the :class:`FirmOut` shape.
* :func:`flag_rows` turns a flag into a reader candidate in the cue-row shape (text =
  ``flag_text``; kind from ``meeting_status``: decided -> decision, assigned -> action,
  open -> problem) and checks its quote with :func:`~tenor.evals.bullet_cues.check_cue`, so
  :func:`~tenor.evals.bullet_cues.build_packets` builds condition-blind reader packets from
  flags exactly as from cues (read with ``prompts/e4_reader.v2.md``).
* A flag whose ``meeting_status`` is soft (``discussed`` / ``info``) contradicts the rule and is
  kept out of the packets with status ``soft_meeting``; an empty ``flag_text`` is ``no_text``.

Pure functions; local data only, no LLM, no network.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tenor.evals import bullet_cues as B

PROMPT = "firmness_check.v1"
CONDITION = "firmness"  # the reader key's condition name for flags
Firmness = Literal["decided", "assigned", "open", "discussed", "info"]
Mismatch = Literal["none", "softer", "owner_missing", "owner_differs", "value_differs"]
KIND: dict[str, Literal["decision", "action", "problem"]] = {
    "decided": "decision",
    "assigned": "action",
    "open": "problem",
}


class FirmOut(BaseModel):
    """One bullet's answer (without ``bullet_id``, which :func:`B.read_agent_lines` strips)."""

    model_config = ConfigDict(extra="forbid")
    notes_status: Firmness
    notes_owner: str
    meeting_status: Firmness
    meeting_owner: str
    mismatch: Mismatch
    flag_text: str
    quote: list[B.QuoteLine]


def flag_rows(job: B.Job, out: FirmOut) -> list[dict[str, Any]]:
    """The bullet's flag as cue rows (``[]`` when ``mismatch`` is ``none``): one row with the
    cue fields (``kind``, ``text``, ``owner``, ``quote``), ``status`` (the quote check, or
    ``soft_meeting`` / ``no_text``), ``quote_lines`` and ``mismatch``. The owner is kept only
    for actions."""
    if out.mismatch == "none":
        return []
    kind = KIND.get(out.meeting_status)
    text = " ".join(out.flag_text.split())
    cue = B.Cue(
        kind=kind or "problem",
        text=text,
        owner=out.meeting_owner if kind == "action" else "",
        quote=out.quote,
    )
    (row,) = B.cue_rows(job, B.CueOut(found=[], cues=[cue]), cap=1)
    if kind is None:
        row["status"] = "soft_meeting"
    elif not text:
        row["status"] = "no_text"
    return [{**row, "mismatch": out.mismatch}]


def ingest_unit(
    unit: B.CueUnit, jobs: Sequence[B.Job], text: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """One unit's answer file -> one row per answered bullet (``unit``, ``bullet_id``,
    ``model`` = :data:`CONDITION`, the validated ``firmness`` answer and its ``cues``), plus
    errors (bad JSON, schema, unknown / repeated / unanswered bullets)."""
    by_id = {j.bullet_id: j for j in jobs}
    lines, errors = B.read_agent_lines(text, by_id, unit.key)
    rows: list[dict[str, Any]] = []
    for bid, d in lines:
        try:
            out = FirmOut.model_validate(d)
        except ValueError as e:
            errors.append(f"{unit.key} {bid}: {e}")
            continue
        rows.append(
            {
                "unit": unit.key,
                "bullet_id": bid,
                "model": CONDITION,
                "firmness": out.model_dump(),
                "cues": flag_rows(by_id[bid], out),
            }
        )
    return rows, errors


def summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts over answered bullets: mismatch kinds, notes vs meeting status pairs, and flag
    statuses (``ok`` flags reach the reader packets)."""
    mm = Counter(str(r["firmness"]["mismatch"]) for r in rows)
    pairs = Counter(
        f"{r['firmness']['notes_status']}->{r['firmness']['meeting_status']}" for r in rows
    )
    st = Counter(str(c["status"]) for r in rows for c in r["cues"])
    return {
        "bullets": len(rows),
        "mismatch": dict(sorted(mm.items())),
        "notes_to_meeting": dict(sorted(pairs.items())),
        "flags": dict(sorted(st.items())),
        "flags_ok": st["ok"],
    }
