"""Council notes: do a product's notes of a real city council agenda item record its official
outcome? (docs/tracks/NOTES.md, "Real meetings (Seattle council)").

* **Unit**: one product's AI notes for one Seattle City Council agenda-item segment
  (``data/council``) played into the product.
* **Gold**: built by code from the city's Legistar JSON (no LLM, :func:`gold_claims`): G1 the
  item (matter number and short title), G2 the outcome (Legistar's action and action text), G3
  the vote (tally and named voters from the votes endpoint).
* **Judge** (E3's machinery: :func:`~tenor.evals.notes_completeness.split_notes`, the same
  judge model, three passes with a neutral pass marker, prompt-cache breakpoints; prompt
  :data:`PROMPT`, derived from ``notes_completeness.v4``): each gold claim covered fully /
  partly / not, and, if not fully, whether the product's own transcript has it (quote checked
  by string match); every bullet about the outcome or vote, and whether it contradicts the
  record.

Per unit the passes are combined item by item (median coverage, lower on a tie; a bullet
contradicts if most passes say so). No reference transcript exists for these meetings, so
there is no precision over the notes' other bullets.
"""

from __future__ import annotations

import json
import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tenor.core.models import Channel, MeetingNote
from tenor.evals.list_check import Line, quote_found, transcript_lines
from tenor.evals.notes_completeness import (
    COVERAGE_RANK,
    MAX_PRODUCT_CHARS,
    Bullet,
    paragraphs,
    split_notes,
)
from tenor.llm.client import (
    LLMClient,
    LLMRequest,
    PromptShape,
    breaks_after,
    cost_usd,
    load_prompt,
    simulate_prompt_cache,
)

PROMPT = "council_notes.v1"
STAGE = "council.judge"
JUDGE_MODEL = "claude-sonnet-5-5"  # E1's and E3's judge
PILOT_SEGMENT = "seattle_6910_126474"  # the one split vote; both products have notes
PILOT_MAX_USD = 0.20
MAX_TOKENS = 8000
OVERHEAD = 250  # structured-output tokens per call (as E3)
PRODUCTS = ("granola", "wispr")
KINDS = ("item", "outcome", "tally")
# Output tokens per call (expected, high), medium effort (thinking counts as output). ASSUMED
# from E3's DEV pilot (600 + 35 per item judged): 3 gold items plus the few outcome bullets
# listed; high is twice expected. Re-priced from the pilot's ledger before the full run.
ASSUMED_OUT = (900.0, 1800.0)

Coverage = Literal["full", "partial", "none"]
Heard = Literal["yes", "no", "not_assessed"]


# -- gold from Legistar -----------------------------------------------------------------------


@dataclass(frozen=True)
class CouncilGold:
    id: str  # G1, G2, G3
    kind: str  # item / outcome / tally
    text: str
    provenance: dict[str, Any] = field(default_factory=dict)


def _data(seg_dir: Path, name: str) -> Any:
    return json.loads((seg_dir / f"{name}.json").read_text())


def short_title(title: str, clauses: int = 2) -> str:
    """A Legistar title's first ``clauses`` clauses (split at semicolons), whitespace collapsed:
    the first alone is often only the code chapter ("relating to land use and zoning")."""
    return "; ".join(" ".join(c.split()) for c in title.split(";")[:clauses])


_RTF_CTRL = re.compile(r"\\[a-z]+-?\d* ?|\\'[0-9a-f]{2}|[{}]")


def rtf_text(rtf: str | None) -> str:
    """Plain text of a Legistar RTF minutes note (control words and the font table dropped)."""
    if not rtf:
        return ""
    body = _RTF_CTRL.sub("", rtf.replace("\\par", "\n"))
    lines = [ln.strip() for ln in body.splitlines()]
    return "\n".join(ln for ln in lines if ln and not ln.endswith(";")).strip()


_VOTE_TAIL = re.compile(r"\s*(?:by the following vote|, and the President signed).*$", re.I)


def gold_claims(seg_dir: Path) -> list[CouncilGold]:
    """The official record of one agenda item as three claims, from Legistar JSON only:
    ``event_item.json`` (matter, action, action text, minutes note) and ``votes.json``."""
    ei_doc, votes_doc = _data(seg_dir, "event_item"), _data(seg_dir, "votes")
    ei = ei_doc["data"]
    votes = [(v["VotePersonName"], v["VoteValueName"]) for v in votes_doc["data"]]
    src = {"event_item": ei_doc["url"], "fetched_utc": ei_doc["fetched_utc"]}
    item = f"{ei['EventItemMatterFile']} ({ei['EventItemMatterType']}): " + short_title(
        ei["EventItemTitle"]
    )
    action_text = _VOTE_TAIL.sub("", ei["EventItemActionText"] or "").strip()
    outcome = f"Outcome: {ei['EventItemActionName']}. Record: {action_text}."
    by_value: dict[str, list[str]] = {}
    for name, value in votes:
        by_value.setdefault(value, []).append(name)
    counts = ", ".join(f"{len(v)} {k}" for k, v in sorted(by_value.items(), key=lambda kv: kv[0]))
    split = len(by_value) > 1
    named = "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(by_value.items()))
    tally = f"Vote: {counts}{'' if split else ' (unanimous)'}. {named}."
    return [
        CouncilGold("G1", "item", item, {**src, "fields": ["EventItemMatterFile", "Title"]}),
        CouncilGold(
            "G2",
            "outcome",
            outcome,
            {
                **src,
                "fields": ["EventItemActionName", "EventItemActionText"],
                "passed_flag": ei["EventItemPassedFlagName"],
                "minutes_note": rtf_text(ei["EventItemMinutesNote"]),
            },
        ),
        CouncilGold(
            "G3",
            "tally",
            tally,
            {
                "votes": votes_doc["url"],
                "fetched_utc": votes_doc["fetched_utc"],
                "split_vote": split,
                "counts": {k: len(v) for k, v in by_value.items()},
            },
        ),
    ]


def gold_json(gold: Mapping[str, Sequence[CouncilGold]]) -> dict[str, Any]:
    """``data/council/gold.json`` (gitignored): segment -> claims with provenance."""
    return {"source": "Legistar Web API JSON in data/council/legistar (no LLM)",
            "segments": {s: [asdict(g) for g in gs] for s, gs in gold.items()}}  # fmt: skip


def read_recording_ids(path: Path) -> dict[str, dict[str, str]]:
    """``recording_ids.txt``: segment -> product -> note ID (comments and blank lines skipped)."""
    out: dict[str, dict[str, str]] = {}
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        seg, *pairs = line.split()
        out[seg] = dict(p.split("=", 1) for p in pairs)
    return out


# -- one unit ---------------------------------------------------------------------------------


@dataclass
class Unit:
    segment: str
    product: str
    bullets: list[Bullet]
    gold: list[CouncilGold]
    transcript: list[Line]

    @property
    def key(self) -> str:
        return f"{self.segment}:{self.product}"

    def product_text(self) -> str:
        return "\n".join(paragraphs(self.transcript))

    @property
    def includes_transcript(self) -> bool:
        text = self.product_text()
        return bool(text) and len(text) <= MAX_PRODUCT_CHARS


def make_unit(segment: str, note: MeetingNote, product: str, gold: list[CouncilGold]) -> Unit:
    """One unit. ``note``'s transcript should be the scored one (``wrong_facts.own_transcript``:
    Granola's microphone turns and Wispr's banner dropped)."""

    def speaker(u: Any) -> str:
        return str(u.speaker_name or ("Me" if u.channel is Channel.ME else "Them"))

    return Unit(
        segment=segment,
        product=product,
        bullets=split_notes(note.summary_markdown or note.summary_text or ""),
        gold=gold,
        transcript=transcript_lines(note, speaker),
    )


def judge_parts(unit: Unit, pass_no: int = 1) -> tuple[str, str, str]:
    """User text in three parts (as E3): the official record (shared by both products and every
    pass), the product's transcript + notes (shared by its passes), the pass marker."""
    gold = "\n".join(f"{g.id} ({g.kind}). {g.text}" for g in unit.gold)
    if unit.includes_transcript:
        own = f"<product_transcript>\n{unit.product_text()}\n</product_transcript>"
    else:
        own = "Product transcript: not included (mark in_product_transcript not_assessed)."
    notes = "\n".join(b.render() for b in unit.bullets) or "(the notes are empty)"
    shared = f"Gold items (official record):\n{gold}\n\n"
    return shared, f"{own}\n\nProduct notes (bullets):\n{notes}", f"\n\nJudging pass {pass_no}."


class _GoldOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    coverage: Coverage
    bullets: list[str]
    in_product_transcript: Heard
    quote: str
    reason: str


class _BulletOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    gold: list[str]
    contradicts: Literal["yes", "no"]
    reason: str


class JudgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome_bullets: list[_BulletOut]
    gold: list[_GoldOut]


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_IDS = {"type": "array", "items": _STR}
SCHEMA: dict[str, Any] = _obj(
    {
        "outcome_bullets": {
            "type": "array",
            "items": _obj(
                {
                    "id": _STR,
                    "gold": _IDS,
                    "contradicts": {"type": "string", "enum": ["yes", "no"]},
                    "reason": _STR,
                }
            ),
        },
        "gold": {
            "type": "array",
            "items": _obj(
                {
                    "id": _STR,
                    "coverage": {"type": "string", "enum": ["full", "partial", "none"]},
                    "bullets": _IDS,
                    "in_product_transcript": {
                        "type": "string",
                        "enum": ["yes", "no", "not_assessed"],
                    },
                    "quote": _STR,
                    "reason": _STR,
                }
            ),
        },
    }
)


def judge_request(unit: Unit, pass_no: int = 1) -> LLMRequest:
    user, breaks = breaks_after(*judge_parts(unit, pass_no))
    return LLMRequest(
        stage=STAGE,
        prompt_id=PROMPT,
        system=load_prompt(PROMPT),
        user=user,
        schema=SCHEMA,
        max_tokens=MAX_TOKENS,
        effort="medium",
        cache_breaks=breaks,
    )


# -- parsing and scoring ----------------------------------------------------------------------


def parse(out: JudgeOut, unit: Unit) -> dict[str, Any]:
    """Validate one pass against the unit (as E3's parser): unknown IDs dropped, duplicates keep
    the first, a missing gold item is "none"; "heard" forced to not_assessed for full coverage
    or no transcript; quotes checked by string match. Every fix is listed in ``repairs``."""
    repairs: list[str] = []
    gids = {g.id: g for g in unit.gold}
    bids = {b.id for b in unit.bullets}
    got: dict[str, _GoldOut] = {}
    for go in out.gold:
        if go.id not in gids or go.id in got:
            repairs.append(f"gold {go.id}: unknown or repeated")
        else:
            got[go.id] = go
    gold: list[dict[str, Any]] = []
    for item in unit.gold:
        g = got.get(item.id)
        if g is None:
            repairs.append(f"gold {item.id} missing")
            gold.append(
                {"id": item.id, "kind": item.kind, "coverage": "none", "bullets": [],
                 "heard": "not_assessed", "quote": "", "quote_found": False, "reason": ""}
            )  # fmt: skip
            continue
        bl = [b for b in dict.fromkeys(g.bullets) if b in bids]
        if len(bl) != len(g.bullets):
            repairs.append(f"gold {item.id}: unknown or repeated bullet ids")
        if g.coverage != "none" and not bl:
            repairs.append(f"gold {item.id}: {g.coverage} without bullets")
        heard: str = g.in_product_transcript
        if g.coverage == "full" or not unit.includes_transcript:
            heard = "not_assessed"
        elif heard == "not_assessed":
            repairs.append(f"gold {item.id}: transcript not assessed")
        q = g.quote.strip() if heard == "yes" else ""
        gold.append(
            {"id": item.id, "kind": item.kind, "coverage": g.coverage, "bullets": bl,
             "heard": heard, "quote": q, "quote_found": bool(q) and quote_found(q, unit.transcript),
             "reason": g.reason}
        )  # fmt: skip
    seen: set[str] = set()
    bullets: list[dict[str, Any]] = []
    for bo in out.outcome_bullets:
        if bo.id not in bids or bo.id in seen:
            repairs.append(f"bullet {bo.id}: unknown or repeated")
            continue
        seen.add(bo.id)
        gl = [x for x in dict.fromkeys(bo.gold) if x in gids]
        bullets.append(
            {"id": bo.id, "gold": gl, "contradicts": bo.contradicts == "yes", "reason": bo.reason}
        )
    return {"gold_verdicts": gold, "outcome_bullets": bullets, "repairs": repairs}


def score_pass(p: Mapping[str, Any]) -> dict[str, Any]:
    """One pass: coverage per gold kind and the number of contradicting bullets."""
    cov = {g["kind"]: g["coverage"] for g in p["gold_verdicts"]}
    heard = {g["kind"]: g["heard"] for g in p["gold_verdicts"]}
    n = sum(b["contradicts"] for b in p["outcome_bullets"])
    return {"coverage": cov, "heard": heard, "contradicting_bullets": n}


def run_unit(unit: Unit, judge: LLMClient, pass_no: int = 1) -> dict[str, Any]:
    """Judge (pass ``pass_no``) one unit; a JSON-ready record (holds notes text: local only)."""
    out = judge.structured(judge_request(unit, pass_no), JudgeOut)
    rec = {
        "segment": unit.segment,
        "product": unit.product,
        "pass": pass_no,
        "product_transcript_included": unit.includes_transcript,
        "bullets": [asdict(b) for b in unit.bullets],
        **parse(out, unit),
    }
    rec["scores"] = score_pass(rec)
    return rec


def combine(passes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One unit's passes: per gold kind the median coverage (lower on a tie) and the per-pass
    labels; "heard" voted over the passes that did not call it full (tie: "no"); a bullet
    contradicts if most passes flag it."""
    n = len(passes)
    coverage: dict[str, str] = {}
    heard: dict[str, str] = {}
    for kind in KINDS:
        labels = [p["scores"]["coverage"][kind] for p in passes]
        r = statistics.median_low(sorted(COVERAGE_RANK[c] for c in labels))
        coverage[kind] = next(k for k, v in COVERAGE_RANK.items() if v == r)
        votes = [
            p["scores"]["heard"][kind] for p in passes if p["scores"]["coverage"][kind] != "full"
        ]
        if coverage[kind] == "full" or not votes:
            heard[kind] = "not_assessed"
        else:
            heard[kind] = "yes" if votes.count("yes") * 2 > len(votes) else "no"
    flagged: dict[str, int] = {}
    for p in passes:
        for b in p["outcome_bullets"]:
            flagged[b["id"]] = flagged.get(b["id"], 0) + int(b["contradicts"])
    contra = sorted(b for b, k in flagged.items() if k * 2 > n)
    return {
        "segment": passes[0]["segment"],
        "product": passes[0]["product"],
        "n_passes": n,
        "coverage": coverage,
        "coverage_per_pass": {k: [p["scores"]["coverage"][k] for p in passes] for k in KINDS},
        "heard": heard,
        "contradicting_bullets": contra,
        "contradiction_flags_per_pass": [p["scores"]["contradicting_bullets"] for p in passes],
        "repairs": [f"pass {p['pass']}: {x}" for p in passes for x in p["repairs"]],
        "passes": list(passes),
    }


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per product: per gold kind how many segments are covered full / partial / none
    (combined verdict), heard-not-full, and segments with a contradicting bullet."""
    out: dict[str, Any] = {}
    for p in PRODUCTS:
        rs = [r for r in records if r["product"] == p]
        if not rs:
            continue
        row: dict[str, Any] = {"n_segments": len(rs)}
        for kind in KINDS:
            row[kind] = {c: sum(r["coverage"][kind] == c for r in rs) for c in COVERAGE_RANK}
            row[kind]["not_full_but_heard"] = sum(r["heard"][kind] == "yes" for r in rs)
            row[kind]["unanimous_across_passes"] = sum(
                len(set(r["coverage_per_pass"][kind])) == 1 for r in rs
            )
        row["segments_with_contradiction"] = sum(bool(r["contradicting_bullets"]) for r in rs)
        row["segments_with_any_pass_flagging_contradiction"] = sum(
            any(r["contradiction_flags_per_pass"]) for r in rs
        )
        row["repairs"] = sum(len(r["repairs"]) for r in rs)
        out[p] = row
    return out


def segment_rows(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Numbers and labels only, per unit (segment IDs are public Legistar IDs)."""
    return [
        {
            "segment": r["segment"],
            "product": r["product"],
            "coverage": r["coverage"],
            "coverage_per_pass": r["coverage_per_pass"],
            # "n/a" for not_assessed (which the committable check reads as a note ID)
            "heard": {k: "n/a" if v == "not_assessed" else v for k, v in r["heard"].items()},
            "contradicting_bullets": len(r["contradicting_bullets"]),
            "contradiction_flags_per_pass": r["contradiction_flags_per_pass"],
        }
        for r in records
    ]


def private_texts(records: Sequence[Mapping[str, Any]]) -> list[str]:
    """Strings an aggregate must not contain: notes bullets, quotes, reasons (every pass)."""
    out: list[str] = []
    for unit in records:
        for r in unit["passes"]:
            out += [b["text"] for b in r["bullets"]]
            out += [g["quote"] for g in r["gold_verdicts"]] + [
                g["reason"] for g in r["gold_verdicts"]
            ]
            out += [b["reason"] for b in r["outcome_bullets"]]
    return out


# -- quote ------------------------------------------------------------------------------------


@dataclass
class Quote:
    calls: int = 0
    cached: int = 0
    input_tokens: int = 0
    usd_expected: float = 0.0
    usd_high: float = 0.0
    usd_expected_cached: float = 0.0
    usd_high_cached: float = 0.0


def quote(units: Sequence[Unit], judge: LLMClient, counter: Any, passes: int) -> Quote:
    """Price ``passes`` passes per unit in run order: input exact (``counter``) + overhead,
    output ASSUMED (:data:`ASSUMED_OUT`); disk-cached calls $0; the cached price replays the
    uncached calls through a warm prompt cache (system, record, product part)."""
    q = Quote()
    shapes: list[PromptShape] = []
    sys_tokens = counter([load_prompt(PROMPT)])[0] + OVERHEAD
    for u in units:
        shared, product, _ = judge_parts(u)
        t_shared, t_product = counter([shared, product])
        for k in range(1, passes + 1):
            req = judge_request(u, k)
            q.calls += 1
            if judge.is_cached(req):
                q.cached += 1
                continue
            tin = counter([f"{req.system}\n\n{req.user}"])[0] + OVERHEAD
            q.input_tokens += tin
            q.usd_expected += cost_usd(judge.model, tin, round(ASSUMED_OUT[0]))
            q.usd_high += cost_usd(judge.model, tin, round(ASSUMED_OUT[1]))
            seg = (("sys", sys_tokens), (f"s:{u.segment}", t_shared), (f"p:{u.key}", t_product))
            shapes.append(PromptShape(seg, max(0, tin - sys_tokens - t_shared - t_product)))
    saving = simulate_prompt_cache(judge.model, shapes).saving_usd(judge.model)
    q.usd_expected_cached = q.usd_expected - saving
    q.usd_high_cached = q.usd_high - saving
    return q
