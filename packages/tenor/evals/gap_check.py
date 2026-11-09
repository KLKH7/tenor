"""Gap check: the feature in ``data/private/FEATURE_DECISION.md`` (docs/tracks/NOTES.md, "Gap
check (G1, G2; Preliminary)"). Two simulated parts, one Sonnet 5.5 call each, scored at $0 from
existing Claude Code reader labels (no API judge).

**G1, "diff, not rewrite".** Unit: one product's notes for one AMI meeting (E4's units). Input:
E4's regenerated bullets (condition R, ``coverage-e4 --generate-only``). One call
(:data:`DIFF_PROMPT`) returns only the candidates that are a decision / action / open problem,
not already in the notes even partly, merged when two say the same thing, at most :data:`CAP`,
ranked, each with a quote from the product's transcript. Items whose quote is not in the
transcript (``list_check.quote_found``) are dropped. Each kept bullet is an R bullet, so it maps to
its E4 reader item (K id) through the private key (source ``regen:R<n>`` and equal text); the
3-run consensus labels then give true / in_notes / kind / gold_match (:func:`score_g1`, the same
counts as ``e4_reader.unit_counts``). A kept bullet with no K id is counted as unlabelled.

**G2, owner suggestions.** Unit: one E3 gold action item (:func:`select_owner_units`): (A) the
notes' consensus verdict is partial and the reason says the owner is missing or unmatched, or (B)
the notes cover it (fully or partly) but no evidence bullet states a usable owner (no owner span,
a vague one, or only "Speaker N"). One call per unit and setting (:data:`OWNER_PROMPT`; the
varying part is the evidence bullets): S1 transcript and notes only; S2 adds a calendar-style
attendee list built from the AMI roster (AMI has no real invite). Scoring (:func:`score_owner`):
the suggestion is resolved to AMI roles with ``owner_check.resolve`` (roster + hand-resolved
names) and is correct if it equals the gold owner's roles; "unsure" or a quote not in the
transcript counts as no suggestion. Unresolvable names are listed as ambiguous.

**Prompt cache.** Every call starts with the same system prompt (:data:`SYSTEM_PROMPT`) and the
same prefix (product transcript + notes) with a cache breakpoint after it; the task text and the
varying part come last. A unit's G1 call and its G2 calls run back to back, so they share the
prefix (S2's attendee list is a second breakpoint after it).

Pure functions apart from :func:`run_unit`, which calls the injected ``LLMClient``.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tenor.evals import coverage_e4 as C4
from tenor.evals import e4_reader as R
from tenor.evals import notes_completeness as N
from tenor.evals import owner_check as O
from tenor.evals.list_check import normalize, quote_found, wilson_ci
from tenor.llm.client import LLMClient, LLMRequest, breaks_after, cost_usd, load_prompt

SYSTEM_PROMPT = "gap_system.v1"
DIFF_PROMPT = "gap_diff.v1"
OWNER_PROMPT = "owner_suggest.v1"
MODEL = "claude-sonnet-5-5"
DIFF_STAGE = "gap.diff"
SETTINGS = ("S1", "S2")  # S1 transcript + notes; S2 + attendee list (AMI roster)
OWNER_STAGE = {"S1": "gap.owner.s1", "S2": "gap.owner.s2"}
CAP = 5
MAX_TOKENS_DIFF = 8000
MAX_TOKENS_OWNER = 3000
PILOT_MEETING = "IS1008a"  # DEV
PILOT_MAX_USD = 0.30
MARGIN = 1.5
# Output tokens per call (expected, high), ASSUMED until the pilot's ledger.
ASSUMED_OUT = {"diff": (1200.0, 3000.0), "owner": (250.0, 800.0)}
TEST_RUN = "20261109T092238Z"  # E4 candidates (TEST)
DEV_RUN = "20261109T101313Z"  # E4 candidates (DEV)
UNSURE = "unsure"

# E4 TEST rows as published in docs/tracks/NOTES.md (agent-reader consensus), for comparison.
E4_ROWS: dict[str, dict[str, Any]] = {
    "C3": {
        "shown": 66,
        "per_unit": 3.0,
        "true_new": 0.30,
        "duplicates": 0.18,
        "false": 0.03,
        "recovered": 8,
        "n_missing": 104,
    },
    "R": {
        "shown": 264,
        "per_unit": 12.0,
        "true_new": 0.26,
        "duplicates": 0.0,
        "false": 0.02,
        "recovered": 40,
        "n_missing": 104,
    },
    "RD": {
        "shown": 312,
        "per_unit": 14.18,
        "true_new": 0.21,
        "duplicates": 0.17,
        "false": 0.02,
        "recovered": 37,
        "n_missing": 104,
    },
}

# G2 unit selection: the E3 consensus reason says the owner is missing / unmatched ...
OWNER_REASON = re.compile(r"\bowner\b|unmatched|not named|not tied to|'one person'", re.I)
# ... unless it says the gold itself names no owner.
NO_OWNER_NEEDED = re.compile(r"gold names no|no owner needed", re.I)

Source = Literal["partial_owner", "no_owner_bullet"]


# -- shared prefix ----------------------------------------------------------------------------


def prefix(unit: N.Unit) -> str:
    """The cached prefix every call of a unit starts with: product transcript, then notes."""
    notes = "\n".join(b.render() for b in unit.bullets) or "(the notes are empty)"
    return f"<transcript>\n{unit.product_text()}\n</transcript>\n\n<notes>\n{notes}\n</notes>\n\n"


# -- G1: the diff -----------------------------------------------------------------------------


class DiffItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    id: str
    kind: Literal["decision", "action", "problem"]
    merged_ids: list[str]
    quote: str


class DiffOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[DiffItem]


DIFF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "id": {"type": "string"},
                    "kind": {"type": "string", "enum": ["decision", "action", "problem"]},
                    "merged_ids": {"type": "array", "items": {"type": "string"}},
                    "quote": {"type": "string"},
                },
                "required": ["reason", "id", "kind", "merged_ids", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def candidates_block(regen_bullets: Sequence[Mapping[str, Any]]) -> str:
    rows = [f"{b['id']} [section: {b['section']}] {b['text']}" for b in regen_bullets]
    return "<candidates>\n" + ("\n".join(rows) or "(no candidates)") + "\n</candidates>\n"


def diff_request(unit: N.Unit, regen_bullets: Sequence[Mapping[str, Any]]) -> LLMRequest:
    """G1's one call per unit: prefix (breakpoint), then the task and the R bullets."""
    task = load_prompt(DIFF_PROMPT).strip() + "\n\n" + candidates_block(regen_bullets)
    user, breaks = breaks_after(prefix(unit), task)
    return LLMRequest(
        stage=DIFF_STAGE,
        prompt_id=DIFF_PROMPT,
        system=load_prompt(SYSTEM_PROMPT),
        user=user,
        schema=DIFF_SCHEMA,
        max_tokens=MAX_TOKENS_DIFF,
        effort="medium",
        cache_breaks=breaks,
    )


def kept_items(
    unit: N.Unit, regen_bullets: Sequence[Mapping[str, Any]], out: DiffOut, cap: int = CAP
) -> list[dict[str, Any]]:
    """The model's items in its order, each with what happened to it: ``shown`` (kept),
    ``unknown_id``, ``repeat`` (an id already returned or merged), ``quote_not_found``, or
    ``over_cap``. A shown item's text is the R bullet's text, verbatim."""
    by_id = {b["id"]: b for b in regen_bullets}
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    n_shown = 0
    for it in out.items:
        row: dict[str, Any] = {**it.model_dump(), "text": by_id.get(it.id, {}).get("text")}
        row["found"] = quote_found(it.quote, unit.transcript)
        if it.id not in by_id:
            row["status"] = "unknown_id"
        elif it.id in seen:
            row["status"] = "repeat"
        elif not row["found"]:
            row["status"] = "quote_not_found"
        elif n_shown >= cap:
            row["status"] = "over_cap"
        else:
            row["status"] = "shown"
            n_shown += 1
        seen |= {it.id, *it.merged_ids}
        rows.append(row)
    return rows


def k_id(key_unit: Mapping[str, Any], rid: str, text: str) -> str | None:
    """The E4 reader item an R bullet became: source ``regen:<rid>`` and the same text after
    ``normalize``. None if no item matches exactly (scored as unlabelled)."""
    for kid, it in key_unit["items"].items():
        srcs = {s["src"] for s in it["sources"] if s["condition"] == C4.R}
        if f"regen:{rid}" in srcs and normalize(it["text"]) == normalize(text):
            return str(kid)
    return None


G1_EXTRA = ("n_returned", "n_quote_dropped", "n_unlabelled", "n_shown_all")


def score_g1_unit(
    rows: Sequence[Mapping[str, Any]],
    key_unit: Mapping[str, Any],
    verdicts: R.Verdicts,
    missing: Mapping[str, str],
) -> dict[str, Any]:
    """One unit's G1 counts: ``e4_reader.unit_counts`` over the shown items that map to a
    labelled K id, plus returned / quote-dropped / unlabelled counts."""
    shown = [r for r in rows if r["status"] == "shown"]
    ids: list[str] = []
    unlabelled = 0
    for r in shown:
        kid = k_id(key_unit, r["id"], r["text"])
        if kid is None or kid not in verdicts:
            unlabelled += 1
        else:
            ids.append(kid)
    return {
        **R.unit_counts(ids, verdicts, missing),
        "n_returned": len(rows),
        "n_quote_dropped": sum(r["status"] == "quote_not_found" for r in rows),
        "n_unlabelled": unlabelled,
        "n_shown_all": len(shown),
    }


def summarize_g1(unit_rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    """Per product and both: ``e4_reader.summarize_group`` plus items shown per unit (with a
    meeting-cluster CI), unlabelled and quote-dropped totals, and the partly-in-notes rate."""
    out: dict[str, Any] = {}
    for g in R.GROUPS:
        rows = [r for r in unit_rows if g in ("both", r["product"])]
        if not rows:
            continue
        s = R.summarize_group(rows, seed)
        s["extra_totals"] = {k: sum(int(r[k]) for r in rows) for k in G1_EXTRA}
        s["shown_all_per_unit"] = s["extra_totals"]["n_shown_all"] / len(rows)
        s["shown_all_per_unit_ci"] = C4.ratio_ci(
            _pairs([{**r, "one": 1} for r in rows], "n_shown_all", "one"), seed
        )
        s["partial_in_notes_rate"] = C4._ratio(
            s["totals"]["partial_in_notes"], s["totals"]["n_shown"]
        )
        s["partial_in_notes_ci"] = C4.ratio_ci(_pairs(rows, "partial_in_notes", "n_shown"), seed)
        out[g] = s
    return out


def _pairs(rows: Sequence[Mapping[str, Any]], num: str, den: str) -> list[tuple[float, float]]:
    """(sum num, sum den) per meeting, meetings sorted (for :func:`C4.ratio_ci`)."""
    by_m: dict[str, list[float]] = {}
    for r in rows:
        m = by_m.setdefault(r["meeting_id"], [0.0, 0.0])
        m[0] += float(r[num])
        m[1] += float(r[den])
    return [(a, b) for _, (a, b) in sorted(by_m.items())]


# -- G2: owner suggestions --------------------------------------------------------------------


@dataclass(frozen=True)
class OwnerUnit:
    meeting_id: str
    product: str
    split: str
    gold_id: str
    source: Source
    bullet_ids: tuple[str, ...]
    gold_roles: tuple[str, ...]  # PM / ID / UI / ME / group

    @property
    def unit_key(self) -> str:
        return f"{self.meeting_id}:{self.product}"

    @property
    def key(self) -> str:
        return f"{self.unit_key}:{self.gold_id}"


def bullet_has_owner(text: str, roster: Sequence[tuple[str, str]]) -> bool:
    """Whether a notes bullet states a usable owner: an owner span (``owner_check``'s position
    rules) that is not vague and names a role, a group or a name other than "Speaker N"."""
    so = O.stated_owner(text, [n for _, n in roster])
    if so.span is None:
        return False
    r = O.resolve(so.span, roster)
    if r.vague:
        return False
    names = [u for u in r.unresolved if not O._SPEAKER.fullmatch(u)]
    return bool(r.roles or names)


def select_owner_units(
    units: Sequence[N.Unit], consensus: Iterable[Mapping[str, Any]]
) -> tuple[list[OwnerUnit], list[dict[str, str]]]:
    """G2's units from E3 reader verdicts (``notes_verdict``, ``reason``, ``evidence_bullets``)
    on gold ACTION items: (A) partial with an owner reason (:data:`OWNER_REASON`, not
    :data:`NO_OWNER_NEEDED`); (B) full or partial, not (A), and no evidence bullet states a
    usable owner (:func:`bullet_has_owner`). Gold with no person as owner is left out. Returns
    the units and every left-out candidate with why (gold ids only, no text)."""
    by_key = {u.key: u for u in units}
    out: list[OwnerUnit] = []
    left: list[dict[str, str]] = []
    for r in consensus:
        if not str(r["gold_id"]).startswith("A") or r["notes_verdict"] == "no":
            continue
        unit = by_key.get(f"{r['meeting']}:{r['product']}")
        if unit is None:
            continue
        roster = O.series_roster(unit.meeting_id)
        bullets = {b.id: b for b in unit.bullets}
        bids = tuple(b for b in r["evidence_bullets"] if b in bullets)
        reason = str(r["reason"])
        source: Source | None = None
        if r["notes_verdict"] == "partial" and OWNER_REASON.search(reason):
            source = None if NO_OWNER_NEEDED.search(reason) else "partial_owner"
        elif bids and not any(bullet_has_owner(bullets[b].text, roster) for b in bids):
            source = "no_owner_bullet"
        if source is None:
            continue
        ref = {"unit": unit.key, "gold_id": str(r["gold_id"]), "source": source}
        gold = O.gold_owners([g for g in unit.gold if g.id.startswith("A")]).get(r["gold_id"])
        if gold is None or isinstance(gold, str):
            left.append({**ref, "why": f"gold owner {gold or 'not found'}"})
        elif not bids:
            left.append({**ref, "why": "no evidence bullet"})
        else:
            out.append(
                OwnerUnit(
                    unit.meeting_id,
                    unit.product,
                    unit.split,
                    str(r["gold_id"]),
                    source,
                    bids,
                    tuple(sorted(gold)),
                )
            )
    return out, left


def attendee_block(meeting_id: str) -> str:
    """S2's simulated calendar invite: the AMI roster's names and roles (AMI has no real
    invite; a role whose holder never says their name is listed without one)."""
    if O.series_of(meeting_id) == meeting_id:  # a natural meeting: AMI gives no roles
        lines = ["- four participants (names and roles not available)"]
    else:
        known = dict(O.series_roster(meeting_id))
        lines = [f"- {known.get(r, '(name unknown)')} ({O.ROLE_LABEL[r]})" for r in O.ROLES]
    return "<calendar_invite>\nAttendees:\n" + "\n".join(lines) + "\n</calendar_invite>\n\n"


class OwnerOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    owner: str
    quote: str


OWNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "owner": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["reason", "owner", "quote"],
    "additionalProperties": False,
}


def owner_request(unit: N.Unit, ou: OwnerUnit, setting: str) -> LLMRequest:
    """One G2 call: prefix (breakpoint), [S2: attendee list (breakpoint)], task + bullets."""
    bullets = {b.id: b for b in unit.bullets}
    action = "\n".join(bullets[b].render() for b in ou.bullet_ids)
    task = load_prompt(OWNER_PROMPT).strip() + f"\n\n<action>\n{action}\n</action>\n"
    parts = (
        (prefix(unit), task)
        if setting == "S1"
        else (prefix(unit), attendee_block(unit.meeting_id), task)
    )
    user, breaks = breaks_after(*parts)
    return LLMRequest(
        stage=OWNER_STAGE[setting],
        prompt_id=OWNER_PROMPT,
        system=load_prompt(SYSTEM_PROMPT),
        user=user,
        schema=OWNER_SCHEMA,
        max_tokens=MAX_TOKENS_OWNER,
        effort="low",
        cache_breaks=breaks,
    )


Verdict = Literal["none", "correct", "partial", "wrong", "ambiguous"]


def judge_suggestion(
    owner: str, quote: str, found: bool, meeting_id: str, gold_roles: Sequence[str]
) -> tuple[Verdict, tuple[str, ...], tuple[str, ...]]:
    """Score one suggestion: (verdict, roles, unresolved names). "unsure" / empty / vague, or a
    quote not in the transcript: ``none``. Otherwise roles via ``owner_check.resolve`` with the
    roster and the hand-resolved names; any unresolved name: ``ambiguous`` (listed); roles equal
    to the gold's: ``correct``; overlapping: ``partial``; else ``wrong``."""
    o = owner.strip().strip(".")
    if not o or o.lower() == UNSURE or not found:
        return "none", (), ()
    span = re.sub(r"\beveryone\b|\beverybody\b", "everyone", o, flags=re.I)
    extra = O.HAND_NAMES.get(O.series_of(meeting_id), {})
    r = O.resolve(span, O.series_roster(meeting_id), extra)
    if r.vague:
        return "none", (), ()
    roles = tuple(sorted(r.roles))
    if r.unresolved:
        return "ambiguous", roles, r.unresolved
    if not roles:
        return "ambiguous", roles, (o,)
    gold = set(gold_roles)
    if set(roles) == gold:
        return "correct", roles, ()
    return ("partial" if set(roles) & gold else "wrong"), roles, ()


def owner_row(ou: OwnerUnit, setting: str, out: OwnerOut, unit: N.Unit) -> dict[str, Any]:
    found = bool(out.quote.strip()) and quote_found(out.quote, unit.transcript)
    v, roles, unresolved = judge_suggestion(
        out.owner, out.quote, found, ou.meeting_id, ou.gold_roles
    )
    raw_v, _, _ = judge_suggestion(out.owner, out.quote, True, ou.meeting_id, ou.gold_roles)
    return {
        **asdict(ou),
        "key": ou.key,
        "setting": setting,
        "owner": out.owner,
        "quote": out.quote,
        "reason": out.reason,
        "quote_found": found,
        "verdict": v,
        "verdict_ignoring_quote": raw_v,
        "roles": list(roles),
        "unresolved": list(unresolved),
    }


def _rate_ci(rows: Sequence[Mapping[str, Any]], num: str, den: str, seed: int) -> dict[str, Any]:
    k = sum(int(r[num]) for r in rows)
    n = sum(int(r[den]) for r in rows)
    return {
        "k": k,
        "n": n,
        "rate": C4._ratio(k, n),
        "wilson": wilson_ci(k, n),
        "cluster_ci": C4.ratio_ci(_pairs(rows, num, den), seed),
    }


def summarize_owner(rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    """Per setting, per product / both and per source: coverage (suggested / all), accuracy
    when suggested (correct / suggested), correct / all, verdict counts; also ignoring the
    quote check. CIs: Wilson and meeting-cluster bootstrap."""
    out: dict[str, Any] = {}
    for s in SETTINGS:
        per: dict[str, Any] = {}
        for g in (*R.GROUPS, "partial_owner", "no_owner_bullet"):
            xs = [r for r in rows if r["setting"] == s and g in ("both", r["product"], r["source"])]
            if not xs:
                continue
            flat = [
                {
                    "meeting_id": r["meeting_id"],
                    "all": 1,
                    "suggested": int(r["verdict"] != "none"),
                    "correct": int(r["verdict"] == "correct"),
                    "suggested_raw": int(r["verdict_ignoring_quote"] != "none"),
                    "correct_raw": int(r["verdict_ignoring_quote"] == "correct"),
                }
                for r in xs
            ]
            per[g] = {
                "n": len(xs),
                "coverage": _rate_ci(flat, "suggested", "all", seed),
                "accuracy_when_suggested": _rate_ci(flat, "correct", "suggested", seed),
                "correct_of_all": _rate_ci(flat, "correct", "all", seed),
                "verdicts": dict(sorted(Counter(r["verdict"] for r in xs).items())),
                "ignoring_quote_check": {
                    "coverage": _rate_ci(flat, "suggested_raw", "all", seed),
                    "accuracy_when_suggested": _rate_ci(flat, "correct_raw", "suggested_raw", seed),
                },
                "quote_not_found": sum(
                    1 for r in xs if r["owner"].strip().lower() != UNSURE and not r["quote_found"]
                ),
            }
        out[s] = per
    return out


# -- running ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitJob:
    unit: N.Unit
    regen_bullets: tuple[Mapping[str, Any], ...]
    owners: tuple[OwnerUnit, ...]

    def requests(self) -> list[LLMRequest]:
        """In run order: G1, then each owner unit's S1 and S2 (back to back: shared prefix)."""
        reqs = [diff_request(self.unit, self.regen_bullets)]
        for ou in self.owners:
            reqs += [owner_request(self.unit, ou, s) for s in SETTINGS]
        return reqs


def run_unit(job: UnitJob, client: LLMClient) -> dict[str, Any]:
    """Run one unit's calls in order. Returns its G1 rows and G2 rows (local: holds text)."""
    out = client.structured(diff_request(job.unit, job.regen_bullets), DiffOut)
    g1 = kept_items(job.unit, job.regen_bullets, out)
    g2: list[dict[str, Any]] = []
    for ou in job.owners:
        for s in SETTINGS:
            o = client.structured(owner_request(job.unit, ou, s), OwnerOut)
            g2.append(owner_row(ou, s, o, job.unit))
    return {
        "unit": job.unit.key,
        "meeting_id": job.unit.meeting_id,
        "product": job.unit.product,
        "g1": g1,
        "g2": g2,
    }


def quote_cost(
    jobs: Sequence[UnitJob], counter: Callable[[list[str]], list[int]], model: str = MODEL
) -> dict[str, float]:
    """Price ``jobs`` (token counts exact via ``counter``; output ASSUMED, :data:`ASSUMED_OUT`).
    ``usd_high``: every input token uncached at the full price, high output. ``usd_expected``:
    the unit's first call writes the prefix, later calls read it; expected output."""
    calls = 0
    exp = high = 0.0
    for job in jobs:
        reqs = job.requests()
        pre = counter([reqs[0].system + prefix(job.unit)])[0]
        for i, q in enumerate(reqs):
            n = counter([q.system + q.user])[0]
            kind = "diff" if q.stage == DIFF_STAGE else "owner"
            o_exp, o_high = ASSUMED_OUT[kind]
            calls += 1
            high += cost_usd(model, n, int(o_high))
            rest = max(0, n - pre)
            if i == 0:
                exp += cost_usd(model, rest, int(o_exp), cache_write_tokens=pre)
            else:
                exp += cost_usd(model, rest, int(o_exp), cache_read_tokens=pre)
    return {"calls": calls, "usd_expected": exp, "usd_high": high}


def reprice(ledger: Sequence[Mapping[str, Any]], full: Mapping[str, int]) -> dict[str, Any]:
    """The full run from the pilot's ledger: mean live cost per call by kind (G1 / G2) times
    the full run's call counts, and x :data:`MARGIN`."""
    by: dict[str, list[float]] = {"diff": [], "owner": []}
    for r in ledger:
        if not r["cached"]:
            by["diff" if r["stage"] == DIFF_STAGE else "owner"].append(float(r["cost_usd"]))
    mean = {k: (sum(v) / len(v) if v else None) for k, v in by.items()}
    usd = sum((mean[k] or 0.0) * full.get(k, 0) for k in by)
    return {
        "mean_usd_per_call": mean,
        "full_calls": dict(full),
        "full_usd": usd,
        "full_usd_with_margin": usd * MARGIN,
    }


def cache_evidence(ledger: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Prompt-cache reads and writes in a ledger (live calls only), per stage."""
    out: dict[str, Any] = {}
    for r in ledger:
        if r["cached"]:
            continue
        s = out.setdefault(
            r["stage"],
            {
                "calls": 0,
                "input_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "calls_with_reads": 0,
            },
        )
        s["calls"] += 1
        s["input_tokens"] += int(r["input_tokens"])
        s["cache_read_tokens"] += int(r["cache_read_tokens"])
        s["cache_write_tokens"] += int(r["cache_write_tokens"])
        s["calls_with_reads"] += int(r["cache_read_tokens"] > 0)
    return out


def private_texts(results: Sequence[Mapping[str, Any]]) -> list[str]:
    """Texts that must not reach the committed aggregate."""
    out: list[str] = []
    for u in results:
        for r in u["g1"]:
            out += [r["quote"], r["reason"], r["text"] or ""]
        for r in u["g2"]:
            out += [r["quote"], r["reason"], r["owner"]]
    return out


# -- inputs -----------------------------------------------------------------------------------


def read_jsonl(path: Any) -> list[dict[str, Any]]:
    import json
    from pathlib import Path

    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def load_verdicts(rows: Iterable[Mapping[str, Any]]) -> dict[str, R.Verdicts]:
    """E4 reader lines -> unit key -> verdicts by K id (validated as ``e4_reader.ReaderLine``)."""
    out: dict[str, R.Verdicts] = {}
    for r in rows:
        v = R.ReaderLine.model_validate(r)
        out.setdefault(f"{v.meeting}:{v.product}", {})[v.cand_id] = v
    return out


def regen_by_unit(candidates: Mapping[str, Any]) -> dict[str, tuple[Mapping[str, Any], ...]]:
    return {
        f"{r['meeting_id']}:{r['product']}": tuple(r["regen_bullets"])
        for r in candidates["records"]
    }


def errata_changes(
    owners: Sequence[OwnerUnit], errata_units: Sequence[N.Unit]
) -> dict[str, tuple[str, ...]]:
    """G2 units whose gold owner differs under the errata gold (same id): key -> errata roles
    (sensitivity only; the primary scoring keeps the original gold the E3 labels used)."""
    by = {u.key: u for u in errata_units}
    out: dict[str, tuple[str, ...]] = {}
    for ou in owners:
        u = by.get(ou.unit_key)
        if u is None:
            continue
        g = O.gold_owners([x for x in u.gold if x.id.startswith("A")]).get(ou.gold_id)
        if g is not None and not isinstance(g, str) and tuple(sorted(g)) != ou.gold_roles:
            out[ou.key] = tuple(sorted(g))
    return out


def rescore(
    rows: Sequence[Mapping[str, Any]], gold: Mapping[str, tuple[str, ...]]
) -> list[dict[str, Any]]:
    """``rows`` with the gold owner replaced for the keys in ``gold`` and re-judged."""
    out = []
    for r in rows:
        if r["key"] not in gold:
            out.append(dict(r))
            continue
        g = gold[r["key"]]
        v, roles, unres = judge_suggestion(
            r["owner"], r["quote"], r["quote_found"], r["meeting_id"], g
        )
        raw, _, _ = judge_suggestion(r["owner"], r["quote"], True, r["meeting_id"], g)
        out.append(
            {
                **r,
                "gold_roles": list(g),
                "verdict": v,
                "verdict_ignoring_quote": raw,
                "roles": list(roles),
                "unresolved": list(unres),
            }
        )
    return out


# -- the run ----------------------------------------------------------------------------------


def main(
    units: Sequence[N.Unit],
    *,
    errata_units: Sequence[N.Unit],
    candidates: Mapping[str, Mapping[str, Any]],  # "test" / "dev" -> .candidates.json
    keys: Mapping[str, Mapping[str, Any]],  # "test" / "dev" -> reader key
    e4_labels: Mapping[str, Sequence[Mapping[str, Any]]],  # "test" / "dev" -> reader lines
    e3_labels: Mapping[str, Sequence[Mapping[str, Any]]],  # "test" / "dev" -> E3 reader lines
    out_dir: Any,
    report: Any,
    quote_only: bool,
    pilot: bool,
    max_usd: float | None,
    seed: int,
    confirm: Callable[[str], bool],
    echo: Callable[[str], None],
    label_sources: Mapping[str, str],
) -> int:
    """``tenor ami gap-check``: build the jobs, quote, run (pilot: DEV, else TEST) under the
    cap, score G1 and G2 at $0, write the local result and (TEST) the numbers-only report.
    Returns an exit code."""
    import functools
    from datetime import UTC, datetime
    from pathlib import Path

    from tenor.blackbox.kit import check_committable
    from tenor.evals.list_check import write_json
    from tenor.evals.runner import _git
    from tenor.llm.client import BudgetExceeded, Ledger, count_tokens

    split = "dev" if pilot else "test"
    regen = {s: regen_by_unit(c) for s, c in candidates.items()}
    for s in ("test", "dev"):
        for u in units:
            rec = next(
                (
                    r
                    for r in candidates[s]["records"]
                    if f"{r['meeting_id']}:{r['product']}" == u.key
                ),
                None,
            )
            if rec is not None and rec["bullets_sha"] != C4.bullets_sha(u):
                echo(f"{u.key}: notes differ from the E4 candidates' notes")
                return 2

    def jobs_for(s: str) -> tuple[list[UnitJob], list[OwnerUnit], list[dict[str, str]]]:
        us = [u for u in units if u.key in regen[s] and u.split == s]
        owners, left = select_owner_units(us, e3_labels[s])
        jobs = [
            UnitJob(u, regen[s][u.key], tuple(o for o in owners if o.unit_key == u.key)) for u in us
        ]
        return jobs, owners, left

    full_jobs, full_owners, full_left = jobs_for("test")
    pilot_jobs, pilot_owners, _ = jobs_for("dev")
    src = Counter(o.source for o in full_owners)
    echo(
        f"G1: {len(full_jobs)} TEST units, {len(pilot_jobs)} DEV. G2: {len(full_owners)} TEST "
        f"gold actions ({dict(src)}), {len(full_left)} left out; DEV {len(pilot_owners)}"
    )
    cap = max_usd if max_usd is not None else (PILOT_MAX_USD if pilot else None)
    if cap is None and not quote_only:
        echo("paid runs need --max-usd (the approved amount); --pilot first")
        return 2
    ledger = Ledger()
    client = LLMClient(model=MODEL, ledger=ledger, max_usd=cap, offline=quote_only)
    counter = functools.partial(count_tokens, model=MODEL, cache_dir=client.cache_dir)
    fq, pq = quote_cost(full_jobs, counter), quote_cost(pilot_jobs, counter)
    for lab, q in (("full TEST", fq), ("pilot DEV", pq)):
        echo(
            f"{lab}: {q['calls']:.0f} calls, ${q['usd_expected']:.3f} expected (prompt cache), "
            f"${q['usd_high']:.3f} high (no cache, output ASSUMED {ASSUMED_OUT})"
        )
    if quote_only:
        echo("no money spent (token counting is free)")
        return 0
    assert cap is not None
    jobs, q = (pilot_jobs, pq) if pilot else (full_jobs, fq)
    if q["usd_high"] > cap:
        echo(f"high estimate ${q['usd_high']:.2f} exceeds the cap ${cap:.2f}")
        return 1
    if not confirm(
        f"{'PILOT: ' if pilot else ''}{q['calls']:.0f} gap-check calls on {split} "
        f"(hard cap ${cap:.2f}, quoted high ${q['usd_high']:.2f})?"
    ):
        return 1
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    results: list[dict[str, Any]] = []
    stopped = None
    try:
        for job in jobs:
            results.append(run_unit(job, client))
            echo(
                f"{job.unit.key}: G1 {sum(r['status'] == 'shown' for r in results[-1]['g1'])} "
                f"shown, G2 {len(job.owners)} actions (spent ${ledger.spent_usd():.4f})"
            )
    except BudgetExceeded as e:
        stopped = str(e)
    # -- score at $0
    key_units = keys[split]["units"]
    verdicts = load_verdicts(e4_labels[split])
    missing = R.missing_gold(e3_labels[split])
    g1_rows = []
    for res in results:
        uk = res["unit"]
        g1_rows.append(
            {
                "unit": uk,
                "meeting_id": res["meeting_id"],
                "product": res["product"],
                **score_g1_unit(
                    res["g1"], key_units[uk], verdicts.get(uk, {}), missing.get(uk, {})
                ),
            }
        )
    g2_rows = [r for res in results for r in res["g2"]]
    owners = pilot_owners if pilot else full_owners
    changes = errata_changes(owners, errata_units)
    ambiguous = [
        {
            "key": r["key"],
            "setting": r["setting"],
            "owner": r["owner"],
            "unresolved": r["unresolved"],
        }
        for r in g2_rows
        if r["verdict"] == "ambiguous"
    ]
    summary = {
        "g1": summarize_g1(g1_rows, seed),
        "g2": summarize_owner(g2_rows, seed),
        "g2_errata_v1": summarize_owner(rescore(g2_rows, changes), seed) if changes else None,
        "g2_errata_v1_changed": len(changes),
        "g2_ambiguous": len(ambiguous),
        "g2_units": {
            "n": len(owners),
            "by_source": dict(Counter(o.source for o in owners)),
            "distinct_bullet_sets": len({(o.unit_key, o.bullet_ids) for o in owners}),
            "left_out": len(full_left) if not pilot else None,
        },
    }
    meta: dict[str, Any] = {
        "date": now.isoformat(timespec="seconds"),
        "git": _git(),
        "seed": seed,
        "model": MODEL,
        "prompts": {"system": SYSTEM_PROMPT, "g1": DIFF_PROMPT, "g2": OWNER_PROMPT},
        "effort": {"g1": "medium", "g2": "low"},
        "cap_items": CAP,
        "e4_candidates": {s: c["meta"]["run_id"] for s, c in candidates.items()},
        "labels": dict(label_sources),
        "gold_version": "ami-1.6.2 (original; the E3 / E4 reader labels used it); errata v1 "
        "as G2 sensitivity",
        "s2_attendees": "SIMULATED from the AMI roster (self-introductions); AMI has no "
        "calendar invite",
        "pilot": pilot,
        "split": split,
        "max_usd": cap,
        "stopped_early": stopped,
        "spent_usd": ledger.spent_usd(),
        "cost_all_calls_usd": sum(float(r["cost_usd"]) for r in ledger.to_json()),
        "ledger_by_stage": ledger.by_stage(),
        "cache_evidence": cache_evidence(ledger.to_json()),
        "quote": {"full": fq, "pilot": pq},
    }
    if pilot:
        full_calls = {"diff": len(full_jobs), "owner": len(full_owners) * len(SETTINGS)}
        meta["reprice"] = reprice(ledger.to_json(), full_calls)
    out = Path(out_dir)
    suffix = ".pilot" if pilot else ""
    write_json(
        out / f"{stamp}{suffix}.json",
        {
            "meta": meta,
            "summary": summary,
            "units": results,
            "g1_rows": g1_rows,
            "g2_left_out": full_left if not pilot else [],
            "g2_errata_changes": changes,
            "g2_ambiguous": ambiguous,
            "ledger": ledger.to_json(),
        },
    )
    agg = {"meta": meta, "summary": summary, "e4_comparison_rows": E4_ROWS}
    check_committable(agg, private_texts(results))
    if not pilot and not stopped:
        write_json(Path(report), agg)
        echo(f"report (numbers only): {report}")
    echo(f"spent ${ledger.spent_usd():.4f} (cap ${cap:.2f}) -> {out / (stamp + suffix)}.json")
    echo(f"cache: {meta['cache_evidence']}")
    if pilot:
        rp = meta["reprice"]
        echo(
            f"full run re-priced from the pilot ledger: ${rp['full_usd']:.3f}, "
            f"${rp['full_usd_with_margin']:.3f} with x{MARGIN}"
        )
    if stopped:
        echo(f"STOPPED: {stopped}")
        return 1
    return 0
