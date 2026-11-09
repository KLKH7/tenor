"""E4: coverage head-to-head (docs/tracks/NOTES.md, "E4: coverage head-to-head").

Can a "+N missed items" line under a product's notes surface what the notes left out, precisely
enough to be worth showing, and does it beat simple alternatives? Unit: one product's AI notes
for one AMI meeting (E3's units). Conditions, all compared against the same notes:

* ``N``: notes alone. E3's judge (replayed from the disk cache) gives the gold items the notes
  do not fully cover: the "missing gold".
* ``CU``: Coverage, uncapped. List decisions / actions / problems from the product's own
  transcript with quotes (E1's map + merge, one combined question per unit), drop items whose
  quote is not in the transcript, keep what E2's check (``agreed_check.v3``, Haiku) calls an
  agreed decision / assigned action / raised problem, then a real "already in the notes?"
  dedupe against the notes' bullets (:data:`DEDUPE_PROMPT`, Haiku); rank (:func:`rank`).
* ``C3``: ``CU`` capped at :data:`K`.
* ``R``: regenerate fuller notes (:data:`REGEN_PROMPT`, Sonnet) from the product transcript;
  its shown items are its decision / action / problem bullets that the judge finds not already
  in the notes (an oracle filter: favours R).
* ``Q``: Granola Chat asked :data:`CHAT_QUESTION`, scoped to the meeting's note (read-only
  ``query_granola_meetings``; Granola only); its items are the answer's bullets.

**Judge** (:data:`JUDGE_PROMPT`, Sonnet, E3's machinery: same gold, roster and AMI reference
transcript, prompt-cached per meeting): the union of every condition's items, blind to the
condition, in a seeded order; per item its kind, the gold items it states, whether AMI's
transcript supports it, whether the notes already have it, and, for actions, whether its owner is
right. Several passes, combined per item by majority (:func:`majority`).

**Generation only** (:func:`generate_unit`): every stage but the judge, saving each
condition's candidates for Claude Code reader agents (``prompts/e4_reader.v1.md``,
``tenor.evals.e4_reader``), which replace the API judge.

**Metrics** (:func:`score_items`): precision of shown items as true omissions (lenient: not
already in the notes, a decision / action / problem, and states a missing gold item or is fully
supported; strict: states a missing gold item), recall of missing gold, duplicates of notes
content, wrong-owner rate among actions that name an owner, false items per unit and per
category where the notes already cover every gold item. Go thresholds: :data:`GO`.
"""

from __future__ import annotations

import functools
import random
import re
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tenor.evals import agreed_check as A
from tenor.evals import list_check as E
from tenor.evals import notes_completeness as N
from tenor.llm.client import (
    LLMClient,
    LLMError,
    LLMRequest,
    PromptShape,
    breaks_after,
    cost_usd,
    load_prompt,
    simulate_prompt_cache,
)

DEDUPE_PROMPT = "coverage_dedupe.v1"
REGEN_PROMPT = "coverage_regen.v1"
JUDGE_PROMPT = "coverage_judge.v1"
DEDUPE_STAGE, REGEN_STAGE, JUDGE_STAGE = "e4.dedupe", "e4.regen", "e4.judge"
LIST_STAGES = ("e1.enumerate", "e1.merge")
LIST_MODEL = "claude-opus-5-5"  # E1's reader (the model that listed in E1)
CHECK_MODEL = "claude-haiku-5-5"  # E2's check and the dedupe
REGEN_MODEL = N.JUDGE_MODEL
JUDGE_MODEL = N.JUDGE_MODEL
E3_PASSES = 3  # E3's baseline runs (replayed from the disk cache)
K = 3
PILOT_MEETING = N.PILOT_MEETING
PILOT_MAX_USD = 0.50
MARGIN = 1.5
TEST_MEETINGS = (
    "IS1008b", "IS1008c", "IS1008d", "IB4003", "ES2006a",  # E3 run 1
    "TS3009c", "ES2008b", "ES2015c",  # run 2
    "ES2006c", "IS1000a", "TS3006b",  # run 3
)  # fmt: skip
CHAT_QUESTION = (
    "What decisions, action items or open problems from this meeting are missing from my notes?"
)
N_, C3, CU, C3S, CUS, R, RD, Q = "N", "C3", "CU", "C3S", "CUS", "R", "RD", "Q"
CONDITIONS = (N_, C3, CU, C3S, CUS, R, RD, Q)
SHOWN = (C3, CU, C3S, CUS, R, RD, Q)
LABELS = {
    N_: "N: notes alone (E3)",
    C3: "C3: Coverage, top 3",
    CU: "CU: Coverage, uncapped (collapsed)",
    C3S: "C3S: Coverage, top 3, strict dedupe (only items absent from the notes)",
    CUS: "CUS: Coverage, uncapped, strict dedupe",
    R: "R: regenerated fuller notes, extra items (judge-filtered: an oracle)",
    RD: "RD: regenerated notes' decision / action / problem bullets, real dedupe",
    Q: "Q: ask Granola Chat what is missing",
}
# Sign tests per unit: wins = the second higher.
PAIRS = ((R, C3), (RD, C3), (Q, C3), (C3, CU), (C3, C3S), (RD, CU))
BASELINES = (R, RD, Q)  # what Coverage must beat
CATEGORY_OF = {
    "agreed_decision": "decision",
    "assigned_action": "action",
    "problem_raised": "problem",
}
TIER = {"action": 0, "decision": 0, "problem": 1}  # decisions and actions before problems
MAX_TOKENS_DEDUPE = 500
MAX_TOKENS_REGEN = 12000
MAX_TOKENS_JUDGE = 16000
OVERHEAD = 250  # structured-output tokens per call (as list_check.Assumed.overhead)
# Go / no-go (data/private/FEATURE_CANDIDATES.md, section 4.4).
GO = {
    "precision": 0.70,
    "precision_ci_lo": 0.60,
    "recall_missing": 0.35,
    "duplicates": 0.10,
    "wrong_owner": 0.10,
    "false_per_complete_cell": 1.0,
}

Counter = Callable[[list[str]], list[int]]
InNotes = Literal["yes", "partly", "no"]
Kind = Literal["decision", "action", "problem", "other"]
Support = Literal["yes", "partly", "no", "n/a"]
Owner = Literal["correct", "wrong", "none", "n/a"]


def list_question(meeting_id: str) -> str:
    """The one listing question per unit (E1 asked one per question type and role)."""
    return (
        f"In meeting {meeting_id}, what did the group decide, what action items were assigned "
        "and to whom, and what problems or open issues came up?"
    )


# -- gold errata ------------------------------------------------------------------------------


def load_gold_errata() -> tuple[Any, str]:
    """The gold errata E3 uses (``tenor.ami.errata``, ``gold_errata.v1.json``) if both exist,
    and the gold version to record; else ``(None, "ami (no errata)")``."""
    try:
        from tenor.ami import errata as ER  # type: ignore[attr-defined, unused-ignore]
    except ImportError:
        return None, "ami (no errata)"
    path: Path = ER.ERRATA_V1
    if not path.is_file():
        return None, str(ER.GOLD_ORIGINAL)
    e = ER.load_errata(path)
    return e, str(e.gold_version)


# -- Coverage: list, check, dedupe, rank ------------------------------------------------------


def list_context(unit: N.Unit) -> E.Context:
    """E1's context for listing from the product's own transcript (no gold: listing never
    reads it)."""
    return E.Context(
        qid=f"e4:{unit.key}",
        question=list_question(unit.meeting_id),
        category="all",
        split=unit.split,
        transcript=unit.transcript,
        reference=[],
        gold=E.Gold(()),
    )


def as_e1_item(it: E.Item, order: int) -> A.E1Item:
    """An E1 listed item in the shape E2's check reads (no judge verdicts exist here)."""
    return A.E1Item(it.id, order, it.text, it.quote, it.lines, (), False, False, False)


class DedupeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    in_notes: InNotes
    bullets: list[str]


DEDUPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "in_notes": {"type": "string", "enum": ["yes", "partly", "no"]},
        "bullets": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["reason", "in_notes", "bullets"],
    "additionalProperties": False,
}


def notes_block(unit: N.Unit) -> str:
    notes = "\n".join(b.render() for b in unit.bullets) or "(the notes are empty)"
    return f"Notes (bullets):\n{notes}\n\n"


def dedupe_request(unit: N.Unit, category: str, text: str, quote: str) -> LLMRequest:
    """One "already in the notes?" call. The notes are a prompt-cache breakpoint, shared by
    every item of the unit (calls run back to back)."""
    user, breaks = breaks_after(notes_block(unit), f'Item ({category}): {text}\n\nQuote: "{quote}"')
    return LLMRequest(
        stage=DEDUPE_STAGE,
        prompt_id=DEDUPE_PROMPT,
        system=load_prompt(DEDUPE_PROMPT),
        user=user,
        schema=DEDUPE_SCHEMA,
        max_tokens=MAX_TOKENS_DEDUPE,
        effort="low",
        cache_breaks=breaks,
    )


@dataclass(frozen=True)
class CovItem:
    """One listed item through the Coverage pipeline. ``status`` etc. are None when the step
    did not run (quote not found: no check; not kept: no dedupe)."""

    id: str
    order: int
    text: str
    quote: str
    lines: tuple[int, ...]
    found: bool
    status: str | None = None
    answers: bool | None = None
    confidence: int | None = None
    check_reason: str = ""
    in_notes: str | None = None
    dedupe_bullets: tuple[str, ...] = ()
    dedupe_reason: str = ""

    @property
    def category(self) -> str | None:
        return CATEGORY_OF.get(self.status or "")

    @property
    def kept(self) -> bool:
        """Quote found, and checked as an agreed decision / assigned action / raised problem."""
        return self.found and self.category is not None and bool(self.answers)

    @property
    def shown(self) -> bool:
        """Kept and not already in the notes: what C3 / CU show."""
        return self.kept and self.in_notes in ("partly", "no")

    @property
    def shown_strict(self) -> bool:
        """Kept and absent from the notes (not even partly): what C3S / CUS show."""
        return self.kept and self.in_notes == "no"


def rank(items: Iterable[CovItem]) -> list[CovItem]:
    """Shown items in display order: decisions and actions before problems, then items the
    notes do not mention at all before ones they partly have, then the check's confidence,
    then transcript order (deterministic)."""
    shown = [it for it in items if it.shown]
    return sorted(
        shown,
        key=lambda it: (
            TIER[it.category or "problem"],
            it.in_notes == "partly",
            -(it.confidence or 0),
            it.order,
        ),
    )


def regen_dap(bullets: Sequence[N.Bullet]) -> list[N.Bullet]:
    """R's bullets under its Decisions / Action items / Problems headings (the format
    :data:`REGEN_PROMPT` asks for), with the category each heading gives."""
    out = []
    for b in bullets:
        sec = b.section.lower().lstrip("# ")
        if sec.startswith(("decision", "action", "problem")):
            out.append(b)
    return out


def regen_category(b: N.Bullet) -> str:
    sec = b.section.lower().lstrip("# ")
    return next(c for c in N.CATEGORIES if sec.startswith(c))


def dedupe_regen(unit: N.Unit, bullets: Sequence[N.Bullet], checker: LLMClient) -> dict[str, str]:
    """RD's real dedupe: :func:`dedupe_request` on each decision / action / problem bullet of
    the regenerated notes (no quote: the bullet is the item). Bullet id -> in_notes."""
    out: dict[str, str] = {}
    for b in regen_dap(bullets):
        d = checker.structured(dedupe_request(unit, regen_category(b), b.text, ""), DedupeOut)
        out[b.id] = d.in_notes
    return out


def cap(ranked: Sequence[CovItem], k: int | None) -> list[CovItem]:
    return list(ranked if k is None else ranked[:k])


def coverage(unit: N.Unit, lister: LLMClient, checker: LLMClient) -> list[CovItem]:
    """Coverage's pipeline on one unit: E1 list (map + merge), E2 check on items whose quote
    is in the transcript, dedupe on the kept ones. Returns every listed item."""
    ctx = list_context(unit)
    _cands, items = E.list_items(lister, ctx)
    out: list[CovItem] = []
    for n, it in enumerate(items):
        ci = CovItem(it.id, n, it.text, it.quote, it.lines, it.found)
        if ci.found:
            req = A.agreed_request(ctx.question, as_e1_item(it, n), unit.transcript)
            v = checker.structured(req, A.AgreedOut)
            ci = replace(
                ci,
                status=v.status,
                answers=v.answers_question,
                confidence=max(0, min(100, v.confidence)),
                check_reason=v.reason,
            )
        out.append(ci)
    for i, ci in enumerate(out):  # dedupe calls back to back: they share the notes prefix
        if ci.kept:
            d = checker.structured(
                dedupe_request(unit, ci.category or "", ci.text, ci.quote), DedupeOut
            )
            bids = {b.id for b in unit.bullets}
            out[i] = replace(
                ci,
                in_notes=d.in_notes,
                dedupe_bullets=tuple(b for b in d.bullets if b in bids),
                dedupe_reason=d.reason,
            )
    return out


# -- R: regenerate fuller notes ---------------------------------------------------------------


class RegenOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes_markdown: str


REGEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"notes_markdown": {"type": "string"}},
    "required": ["notes_markdown"],
    "additionalProperties": False,
}


def regen_request(unit: N.Unit) -> LLMRequest:
    return LLMRequest(
        stage=REGEN_STAGE,
        prompt_id=REGEN_PROMPT,
        system=load_prompt(REGEN_PROMPT),
        user=f"<transcript>\n{unit.product_text()}\n</transcript>",
        schema=REGEN_SCHEMA,
        max_tokens=MAX_TOKENS_REGEN,
        effort="medium",
    )


def regen(unit: N.Unit, client: LLMClient) -> tuple[str, list[N.Bullet]]:
    """R's notes (markdown) and their bullets, split as E3 splits notes, IDs ``R1``.."""
    md = client.structured(regen_request(unit), RegenOut).notes_markdown
    return md, [replace(b, id="R" + b.id[1:], parent=_rid(b.parent)) for b in N.split_notes(md)]


def _rid(bid: str | None) -> str | None:
    return None if bid is None else "R" + bid[1:]


# -- Q: Granola Chat --------------------------------------------------------------------------

_CITE = re.compile(r"\[\[?\d+\]?\]\([^)]*\)|\[\d+\]")


def chat_items(answer: str) -> list[str]:
    """Granola Chat's answer as items: its bullets (citation links and Granola's standing
    transcript note dropped), leaving out
    bullets that only introduce sub-points (they end with ":" and have children) and, when
    there is any bullet, free paragraphs (preamble, sign-off). Without bullets, every
    paragraph is an item. Deterministic."""
    from tenor.blackbox.products import parse_granola_answer

    text, _cited = parse_granola_answer(answer)  # drops Granola's standing transcript note
    bullets = N.split_notes(_CITE.sub("", text))
    parents = {b.parent for b in bullets if b.parent}
    items = [b for b in bullets if not b.paragraph]
    if not items:
        items = bullets
    keep = [b for b in items if not (b.id in parents and b.text.rstrip().endswith(":"))]
    return [b.text.strip() for b in keep if b.text.strip()]


def ask_chat(product: Any, note_id: str, question: str = CHAT_QUESTION) -> dict[str, Any]:
    """Ask Granola Chat ``question`` scoped to one note, through the same product path as
    ``tenor ami chat-answers`` (``GranolaProduct.ask_scoped``: read-only
    ``query_granola_meetings``, rate limits waited out). A JSON-ready row."""
    a = product.ask_scoped(question, [note_id])
    return {"question": question, "answer": a.raw, "latency_s": round(a.latency_s, 1),
            "scope": [note_id]}  # fmt: skip


# -- the judge --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One item the judge reads: ``X1``.. in a seeded order, with every condition that shows
    it (texts equal after normalization are one candidate)."""

    id: str
    text: str
    sources: tuple[str, ...]  # "cov:I3", "regen:R5", "chat:2"


def candidates(
    cov: Sequence[CovItem],
    regen_bullets: Sequence[N.Bullet],
    chat: Sequence[str],
    seed: int,
    key: str,
) -> list[Candidate]:
    """The union of every condition's items, one per normalized text, IDs ``X1``.. in an
    order seeded by ``seed`` and the unit ``key`` (blind to the condition)."""
    by_text: dict[str, tuple[str, list[str]]] = {}
    rows = (
        [(f"cov:{c.id}", c.text) for c in cov if c.shown]
        + [(f"regen:{b.id}", b.text) for b in regen_bullets]
        + [(f"chat:{i}", t) for i, t in enumerate(chat, 1)]
    )
    for src, text in rows:
        k = E.normalize(text)
        if not k:
            continue
        by_text.setdefault(k, (text, []))[1].append(src)
    keys = sorted(by_text)
    random.Random(f"{seed}:e4.order:{key}").shuffle(keys)
    return [Candidate(f"X{n}", by_text[k][0], tuple(by_text[k][1])) for n, k in enumerate(keys, 1)]


def judge_parts(unit: N.Unit, cands: Sequence[Candidate], pass_no: int) -> tuple[str, str, str]:
    """The judge's user text in three parts: the meeting (gold + roster + AMI reference: E3's
    shared part, the same for both products and every pass), the product's notes, then the
    candidates and the pass marker."""
    shared = N.judge_parts(unit)[0]
    items = "\n".join(f"{c.id}. {c.text}" for c in cands) or "(no candidate items)"
    return (
        shared,
        f"Product notes (bullets):\n{_notes(unit)}\n\n",
        f"Candidate items:\n{items}\n\nJudging pass {pass_no}.",
    )


def _notes(unit: N.Unit) -> str:
    return "\n".join(b.render() for b in unit.bullets) or "(the notes are empty)"


class _ItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    reason: str
    kind: Kind
    gold: list[str]
    supported: Support
    in_notes: InNotes
    notes_bullets: list[str]
    owner: Owner


class JudgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[_ItemOut]


_STR = {"type": "string"}
JUDGE_SCHEMA: dict[str, Any] = N._obj(
    {
        "items": {
            "type": "array",
            "items": N._obj(
                {
                    "id": _STR,
                    "reason": _STR,
                    "kind": {"type": "string", "enum": ["decision", "action", "problem", "other"]},
                    "gold": {"type": "array", "items": _STR},
                    "supported": {"type": "string", "enum": ["yes", "partly", "no", "n/a"]},
                    "in_notes": {"type": "string", "enum": ["yes", "partly", "no"]},
                    "notes_bullets": {"type": "array", "items": _STR},
                    "owner": {"type": "string", "enum": ["correct", "wrong", "none", "n/a"]},
                }
            ),
        }
    }
)


def judge_request(unit: N.Unit, cands: Sequence[Candidate], pass_no: int = 1) -> LLMRequest:
    user, breaks = breaks_after(*judge_parts(unit, cands, pass_no))
    return LLMRequest(
        stage=JUDGE_STAGE,
        prompt_id=JUDGE_PROMPT,
        system=load_prompt(JUDGE_PROMPT),
        user=user,
        schema=JUDGE_SCHEMA,
        max_tokens=MAX_TOKENS_JUDGE,
        effort="medium",
        cache_breaks=breaks,
    )


@dataclass(frozen=True)
class ItemVerdict:
    id: str
    kind: str
    gold: tuple[str, ...]
    supported: str
    in_notes: str
    notes_bullets: tuple[str, ...]
    owner: str
    reason: str = ""


def parse(
    out: JudgeOut, unit: N.Unit, cands: Sequence[Candidate]
) -> tuple[dict[str, ItemVerdict], list[str]]:
    """Validate the judge's output: unknown IDs dropped, duplicates keep the first, a missing
    item becomes kind "other" / in_notes "no"; support is "n/a" for kind other and owner "n/a"
    for non-actions. Every fix is listed (should be none)."""
    repairs: list[str] = []
    gids = {g.id for g in unit.gold}
    bids = {b.id for b in unit.bullets}
    got: dict[str, _ItemOut] = {}
    for it in out.items:
        if it.id not in {c.id for c in cands}:
            repairs.append(f"unknown item id {it.id}")
        elif it.id in got:
            repairs.append(f"item {it.id} twice")
        else:
            got[it.id] = it
    verdicts: dict[str, ItemVerdict] = {}
    for c in cands:
        o = got.get(c.id)
        if o is None:
            repairs.append(f"item {c.id} missing")
            verdicts[c.id] = ItemVerdict(c.id, "other", (), "n/a", "no", (), "n/a")
            continue
        gl = tuple(g for g in dict.fromkeys(o.gold) if g in gids)
        nb = tuple(b for b in dict.fromkeys(o.notes_bullets) if b in bids)
        if len(gl) != len(o.gold) or len(nb) != len(o.notes_bullets):
            repairs.append(f"item {c.id}: unknown or repeated ids")
        sup: str = "n/a" if o.kind == "other" else o.supported
        if o.kind != "other" and sup == "n/a":
            repairs.append(f"item {c.id}: {o.kind} without a support verdict")
        owner: str = o.owner if o.kind == "action" else "n/a"
        if o.kind == "action" and owner == "n/a":
            repairs.append(f"item {c.id}: action without an owner verdict")
            owner = "none"
        verdicts[c.id] = ItemVerdict(c.id, o.kind, gl, sup, o.in_notes, nb, owner, o.reason)
    return verdicts, repairs


IN_NOTES_RANK = {"no": 0, "partly": 1, "yes": 2}


def majority(passes: Sequence[Mapping[str, ItemVerdict]]) -> dict[str, ItemVerdict]:
    """One verdict per item from several passes: kind and owner by vote (ties: decision,
    action, problem, other; wrong, none, correct: the cautious side), gold links that most
    passes give, support the lower median of no < partly < yes over passes that gave one,
    in_notes the upper median of no < partly < yes (ties count against the item)."""
    out: dict[str, ItemVerdict] = {}
    for iid in passes[0]:
        vs = [p[iid] for p in passes]
        kind = N._vote([v.kind for v in vs], (*N.CATEGORIES, "other"))
        sup = "n/a"
        if kind != "other":
            ranks = sorted(N.SUPPORT_RANK[v.supported] for v in vs if v.kind != "other")
            r = statistics.median_low(ranks) if ranks else 0
            sup = next(k for k in ("no", "partly", "yes") if N.SUPPORT_RANK[k] == r)
        notes_r = statistics.median_high(sorted(IN_NOTES_RANK[v.in_notes] for v in vs))
        in_notes = next(k for k, r in IN_NOTES_RANK.items() if r == notes_r)
        owner = "n/a"
        if kind == "action":
            owners = [v.owner for v in vs if v.kind == "action"] or ["none"]
            owner = N._vote(owners, ("wrong", "none", "correct", "n/a"))
        out[iid] = ItemVerdict(
            iid,
            kind,
            N._majority_ids([v.gold for v in vs]),
            sup,
            in_notes,
            N._majority_ids([v.notes_bullets for v in vs]),
            owner,
        )
    return out


# -- scoring ----------------------------------------------------------------------------------


def is_true(v: ItemVerdict, missing: set[str], strict: bool = False) -> bool:
    """A true omission: not already in the notes, a decision / action / problem, and states a
    gold item the notes do not fully cover (strict) or that, or fully supported by AMI's
    transcript (lenient)."""
    if v.in_notes == "yes" or v.kind == "other":
        return False
    gold_missing = any(g in missing for g in v.gold)
    return gold_missing if strict else gold_missing or v.supported == "yes"


def score_items(
    shown: Sequence[str],
    verdicts: Mapping[str, ItemVerdict],
    gold: Sequence[N.GoldItem],
    coverage_of: Mapping[str, str],
) -> dict[str, Any]:
    """One condition on one unit. ``shown``: candidate IDs the condition shows, in order;
    ``coverage_of``: gold id -> E3's coverage (full / partial / none). Counts plus per-category
    cells where the notes cover every gold item ("complete"): items shown of that kind there,
    and how many are not true omissions."""
    missing = {g for g, c in coverage_of.items() if c != "full"}
    none = {g for g, c in coverage_of.items() if c == "none"}
    vs = [verdicts[i] for i in shown]
    recovered = {g for v in vs if v.in_notes != "yes" for g in v.gold if g in missing}
    cells: dict[str, dict[str, int]] = {}
    for cat in N.CATEGORIES:
        gs = [g for g in gold if g.category == cat]
        if gs and not any(g.id in missing for g in gs):
            of_kind = [v for v in vs if v.kind == cat]
            cells[cat] = {
                "shown": len(of_kind),
                "false": sum(not is_true(v, missing) for v in of_kind),
            }
    actions = [v for v in vs if v.kind == "action"]
    return {
        "n_shown": len(vs),
        "true": sum(is_true(v, missing) for v in vs),
        "true_strict": sum(is_true(v, missing, strict=True) for v in vs),
        "true_new": sum(is_true(v, missing) and v.in_notes == "no" for v in vs),
        "duplicates": sum(v.in_notes == "yes" for v in vs),
        "partly_in_notes": sum(v.in_notes == "partly" for v in vs),
        "other_kind": sum(v.kind == "other" for v in vs),
        "unsupported": sum(v.kind != "other" and v.supported == "no" for v in vs),
        "actions": len(actions),
        "actions_named": sum(v.owner in ("correct", "wrong") for v in actions),
        "wrong_owner": sum(v.owner == "wrong" for v in actions),
        "n_missing": len(missing),
        "n_missing_none": len(none),
        "recovered": len(recovered),
        "recovered_none": len(recovered & none),
        "recovered_ids": sorted(recovered),
        "complete_cells": cells,
    }


def _ratio(a: float, b: float) -> float | None:
    return a / b if b else None


def unit_metrics(s: Mapping[str, Any]) -> dict[str, float | None]:
    """Per-unit rates from :func:`score_items` counts (None when undefined)."""
    return {
        "precision": _ratio(s["true"], s["n_shown"]),
        "precision_strict": _ratio(s["true_strict"], s["n_shown"]),
        "precision_new": _ratio(s["true_new"], s["n_shown"]),
        "recall_missing": _ratio(s["recovered"], s["n_missing"]),
        "duplicates": _ratio(s["duplicates"], s["n_shown"]),
        "wrong_owner": _ratio(s["wrong_owner"], s["actions_named"]),
        "false_items": float(s["n_shown"] - s["true"]),
        "n_shown": float(s["n_shown"]),
    }


def shown_ids(
    cov: Sequence[CovItem],
    regen_bullets: Sequence[N.Bullet],
    chat: Sequence[str] | None,
    cands: Sequence[Candidate],
    verdicts: Mapping[str, ItemVerdict],
    k: int = K,
    regen_dedupe: Mapping[str, str] | None = None,
) -> dict[str, list[str]]:
    """Candidate IDs each condition shows. C3 / CU: Coverage's ranked items (top ``k`` /
    all); C3S / CUS: the same ranking, only items the dedupe found absent from the notes.
    R: regenerated bullets the judge calls a decision / action / problem not already in the
    notes (the oracle filter). RD: regenerated bullets under a decision / action / problem
    heading that the real dedupe (``regen_dedupe``) did not find in the notes. Q: every chat
    item (absent when Chat was not asked)."""
    of: dict[str, str] = {s: c.id for c in cands for s in c.sources}
    ranked_items = [c for c in rank(cov) if f"cov:{c.id}" in of]
    ranked = _uniq(of[f"cov:{c.id}"] for c in ranked_items)
    strict = _uniq(of[f"cov:{c.id}"] for c in ranked_items if c.shown_strict)
    out = {CU: ranked, C3: ranked[:k], CUS: strict, C3S: strict[:k]}
    r = [of[f"regen:{b.id}"] for b in regen_bullets if f"regen:{b.id}" in of]
    out[R] = _uniq(x for x in r if verdicts[x].kind != "other" and verdicts[x].in_notes != "yes")
    if regen_dedupe is not None:
        out[RD] = _uniq(
            of[f"regen:{b.id}"]
            for b in regen_bullets
            if f"regen:{b.id}" in of and regen_dedupe.get(b.id, "yes") != "yes"
        )
    if chat is not None:
        out[Q] = _uniq(of[f"chat:{i}"] for i in range(1, len(chat) + 1) if f"chat:{i}" in of)
    return out


def _uniq(xs: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(xs))


# -- one meeting ------------------------------------------------------------------------------


def e3_pass(unit: N.Unit, judge: LLMClient, pass_no: int, prompt: str | None) -> dict[str, Any]:
    """One E3 judge pass (the baseline; replays from the disk cache when E3 ran it). A prompt
    other than E3's current one needs E3's ``prompt`` parameter."""
    if prompt is None or prompt == N.PROMPT:
        return N.run_unit(unit, judge, pass_no)
    return N.run_unit(unit, judge, pass_no, prompt)  # type: ignore[call-arg, unused-ignore]


def e3_quote(unit: N.Unit, judge: LLMClient, counter: Counter, prompt: str | None) -> N.Quote:
    if prompt is None or prompt == N.PROMPT:
        return N.quote([unit], judge, counter, E3_PASSES)
    return N.quote([unit], judge, counter, E3_PASSES, prompt)  # type: ignore[call-arg, unused-ignore]


# -- one meeting (judged) --------------------------------------------------------------------------


@dataclass
class Clients:
    lister: LLMClient
    checker: LLMClient
    regen: LLMClient
    judge: LLMClient


def run_meeting(
    units: Sequence[N.Unit],
    clients: Clients,
    chat_answers: Mapping[str, str],
    passes: int,
    seed: int,
    k: int = K,
    log: Callable[[str], None] = lambda _s: None,
    e3_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """Every condition on one meeting's units (both products), judged. Each product's
    pipeline calls run first; then the judge, products back to back, passes back to back, so
    the meeting part of its prompt stays in the prompt cache. ``chat_answers``: unit key ->
    Granola Chat's raw answer. ``e3_prompt``: E3's judge prompt for the baseline (default
    E3's current one). JSON-ready records (hold product text: local only)."""
    prep: list[dict[str, Any]] = []
    for u in units:
        e3 = [e3_pass(u, clients.judge, p, e3_prompt) for p in range(1, E3_PASSES + 1)]
        cov = coverage(u, clients.lister, clients.checker)
        md, rb = regen(u, clients.regen)
        rd = dedupe_regen(u, rb, clients.checker)
        raw = chat_answers.get(u.key)
        chat = chat_items(raw) if raw is not None else None
        cands = candidates(cov, rb, chat or [], seed, u.key)
        prep.append({"unit": u, "e3": e3, "cov": cov, "md": md, "rb": rb, "rd": rd, "raw": raw,
                     "chat": chat, "cands": cands})  # fmt: skip
        log(f"{u.key}: {len(cov)} listed, {sum(c.kept for c in cov)} kept, "
            f"{sum(c.shown for c in cov)} shown; R {len(rb)} bullets; "
            f"Q {'-' if chat is None else len(chat)} items; {len(cands)} to judge")  # fmt: skip
    records = []
    for p in prep:
        u, cands = p["unit"], p["cands"]
        runs, repairs = [], []
        for k_ in range(1, passes + 1):
            v, rep = parse(
                clients.judge.structured(judge_request(u, cands, k_), JudgeOut), u, cands
            )
            runs.append(v)
            repairs += [f"pass {k_}: {x}" for x in rep]
        verdicts = majority(runs)
        e3m = N.majority_verdicts(p["e3"])
        coverage_of = {g.id: g.coverage for g in e3m.gold}
        shown = shown_ids(p["cov"], p["rb"], p["chat"], cands, verdicts, k, p["rd"])
        scores = {c: score_items(ids, verdicts, u.gold, coverage_of) for c, ids in shown.items()}
        e3_recall = statistics.fmean(float(x["scores"]["all"]["recall"]) for x in p["e3"])
        r_all = [c.id for c in cands if any(s.startswith("regen:") for s in c.sources)]
        r_linked = {g for i in r_all for g in verdicts[i].gold}
        records.append(
            {
                "meeting_id": u.meeting_id,
                "product": u.product,
                "split": u.split,
                "passes": passes,
                "e3": {
                    "prompt": e3_prompt or N.PROMPT,
                    "passes": len(p["e3"]),
                    "recall": e3_recall,
                    "coverage": coverage_of,
                },
                "n_gold": len(u.gold),
                "n_bullets": len(u.bullets),
                "coverage_items": [asdict(c) for c in p["cov"]],
                "regen_markdown": p["md"],
                "regen_bullets": [asdict(b) for b in p["rb"]],
                "regen_dedupe": p["rd"],
                "regen_stats": {
                    "bullets": len(p["rb"]),
                    "dap_bullets": sum(verdicts[i].kind != "other" for i in r_all),
                    "gold_linked": len(r_linked),
                    "gold_recall_linked": _ratio(len(r_linked), len(u.gold)),
                    "duplicates_of_notes": sum(verdicts[i].in_notes == "yes" for i in r_all),
                },
                "chat_raw": p["raw"],
                "chat_items": p["chat"],
                "candidates": [asdict(c) for c in cands],
                "verdicts": {i: asdict(v) for i, v in verdicts.items()},
                "pass_verdicts": [{i: asdict(v) for i, v in r.items()} for r in runs],
                "repairs": repairs,
                "shown": shown,
                "scores": scores,
                "metrics": {c: unit_metrics(s) for c, s in scores.items()},
            }
        )
        log(f"{u.key}: " + ", ".join(
            f"{c} {s['true']}/{s['n_shown']} true, rec {s['recovered']}/{s['n_missing']}"
            for c, s in scores.items()))  # fmt: skip
    return records


# -- generation only (scored by Claude Code reader agents, prompts/e4_reader.v1.md) -----------

GENERATION_STAGES = (*LIST_STAGES, A.STAGE, DEDUPE_STAGE, REGEN_STAGE)


def generation_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Quote rows of the generation stages only (no E4 judge, no E3 baseline)."""
    return [r for r in rows if not str(r["stage"]).startswith(("judge", "E3"))]


def bullets_sha(unit: N.Unit) -> str:
    """Fingerprint of the notes' bullets as the dedupe saw them (packets check it)."""
    import hashlib

    text = "\n".join(b.render() for b in unit.bullets)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def condition_items(
    cov: Sequence[CovItem],
    regen_bullets: Sequence[N.Bullet],
    regen_dedupe: Mapping[str, str],
    chat: Sequence[str] | None,
    k: int = K,
) -> dict[str, list[dict[str, str]]]:
    """Each condition's items in display order, as ``{"src", "text"}``, with no judge.
    C3 / CU / C3S / CUS as :func:`shown_ids`. ``R`` holds every regenerated bullet: R's
    oracle filter (a decision / action / problem not already in the notes) is applied to the
    reader's verdicts at scoring. RD: :func:`dedupe_regen`'s survivors. Q only when Chat was
    asked (Granola)."""
    ranked = rank(cov)
    strict = [c for c in ranked if c.shown_strict]

    def cv(items: Sequence[CovItem]) -> list[dict[str, str]]:
        return [{"src": f"cov:{c.id}", "text": c.text} for c in items]

    out = {CU: cv(ranked), C3: cv(ranked[:k]), CUS: cv(strict), C3S: cv(strict[:k])}
    out[R] = [{"src": f"regen:{b.id}", "text": b.text} for b in regen_bullets]
    out[RD] = [
        {"src": f"regen:{b.id}", "text": b.text}
        for b in regen_dap(regen_bullets)
        if regen_dedupe.get(b.id, "yes") != "yes"
    ]
    if chat is not None:
        out[Q] = [{"src": f"chat:{i}", "text": t} for i, t in enumerate(chat, 1)]
    return out


def generate_unit(
    unit: N.Unit, clients: Clients, chat_raw: str | None, k: int = K
) -> dict[str, Any]:
    """Every generation stage on one unit (list, check, dedupe, regenerate, RD's dedupe) and
    the candidates each condition shows; no judge. JSON-ready (holds product text: local
    only)."""
    cov = coverage(unit, clients.lister, clients.checker)
    md, rb = regen(unit, clients.regen)
    rd = dedupe_regen(unit, rb, clients.checker)
    chat = chat_items(chat_raw) if chat_raw is not None else None
    return {
        "meeting_id": unit.meeting_id,
        "product": unit.product,
        "split": unit.split,
        "n_bullets": len(unit.bullets),
        "bullets_sha": bullets_sha(unit),
        "coverage_items": [asdict(c) for c in cov],
        "regen_markdown": md,
        "regen_bullets": [asdict(b) for b in rb],
        "regen_dedupe": rd,
        "chat_raw": chat_raw,
        "chat_items": chat,
        "conditions": condition_items(cov, rb, rd, chat, k),
    }


# -- summary ----------------------------------------------------------------------------------


def ratio_ci(
    pairs: Sequence[tuple[float, float]], seed: int, n: int = 2000, alpha: float = 0.05
) -> tuple[float, float] | None:
    """Percentile cluster-bootstrap CI of sum(num) / sum(den) over units (resampled with
    replacement; draws with den = 0 skipped). Seeded, deterministic."""
    if not pairs or not sum(d for _, d in pairs):
        return None
    rng = random.Random(f"{seed}:e4.bootstrap:{len(pairs)}")
    stats = []
    for _ in range(n):
        draw = rng.choices(pairs, k=len(pairs))
        den = sum(d for _, d in draw)
        if den:
            stats.append(sum(x for x, _ in draw) / den)
    stats.sort()
    m = len(stats)
    return round(stats[int(alpha / 2 * m)], 4), round(
        stats[min(m - 1, int((1 - alpha / 2) * m))], 4
    )


_SUMS = ("n_shown", "true", "true_strict", "true_new", "duplicates", "partly_in_notes",
         "other_kind", "unsupported", "actions", "actions_named", "wrong_owner", "n_missing",
         "n_missing_none", "recovered", "recovered_none")  # fmt: skip


def _cond_summary(rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    tot = {k: sum(int(r[k]) for r in rows) for k in _SUMS}
    cells = [c for r in rows for c in r["complete_cells"].values()]
    out: dict[str, Any] = {"n_units": len(rows), "totals": tot}
    for name, num, den in (
        ("precision", "true", "n_shown"),
        ("precision_strict", "true_strict", "n_shown"),
        ("precision_new", "true_new", "n_shown"),
        ("recall_missing", "recovered", "n_missing"),
        ("recall_missing_none", "recovered_none", "n_missing_none"),
        ("duplicates", "duplicates", "n_shown"),
        ("wrong_owner", "wrong_owner", "actions_named"),
    ):
        out[name] = _ratio(tot[num], tot[den])
        out[f"{name}_wilson"] = E.wilson_ci(tot[num], tot[den])
        out[f"{name}_ci"] = ratio_ci([(float(r[num]), float(r[den])) for r in rows], seed)
    out["shown_per_unit"] = statistics.fmean(r["n_shown"] for r in rows) if rows else None
    out["false_per_unit"] = (
        statistics.fmean(r["n_shown"] - r["true"] for r in rows) if rows else None
    )
    out["complete_cells"] = len(cells)
    out["shown_per_complete_cell"] = statistics.fmean(c["shown"] for c in cells) if cells else None
    out["false_per_complete_cell"] = statistics.fmean(c["false"] for c in cells) if cells else None
    return out


def go_checks(s: Mapping[str, Any], beats: Mapping[str, bool | None]) -> dict[str, Any]:
    """The section 4.4 thresholds on one condition's summary (None: not measurable here)."""

    def le(x: float | None, t: float) -> bool | None:
        return None if x is None else x <= t

    ci = s["precision_ci"]
    return {
        "precision>=0.70": None if s["precision"] is None else s["precision"] >= GO["precision"],
        "precision_ci_lo>=0.60": None if ci is None else ci[0] >= GO["precision_ci_lo"],
        "recall_missing>=0.35": None
        if s["recall_missing"] is None
        else s["recall_missing"] >= GO["recall_missing"],
        "duplicates<=0.10": le(s["duplicates"], GO["duplicates"]),
        "wrong_owner<=0.10": le(s["wrong_owner"], GO["wrong_owner"]),
        "false_per_complete_cell<=1": le(
            s["false_per_complete_cell"], GO["false_per_complete_cell"]
        ),
        **{f"beats_{k}": v for k, v in beats.items()},
        "stability": None,  # 3 runs: later
    }


def _beats(a: Mapping[str, Any], b: Mapping[str, Any] | None) -> bool | None:
    """``a`` beats ``b``: higher precision at equal or higher recall of missing gold."""
    if b is None or b["n_units"] == 0 or a["precision"] is None:
        return None
    if b["precision"] is None:
        return True
    ra, rb = a["recall_missing"] or 0.0, b["recall_missing"] or 0.0
    return a["precision"] > b["precision"] and ra >= rb


def summarize(records: Sequence[Mapping[str, Any]], seed: int = 42) -> dict[str, Any]:
    """Per split (dev, test, all) and product (granola, wispr, both): N's baseline (E3 recall,
    missing gold), each shown condition's pooled rates with Wilson and cluster-bootstrap CIs,
    paired sign tests per unit (:data:`PAIRS`), and the go checks for C3 and CU."""
    out: dict[str, Any] = {}
    for split in ("dev", "test", "all"):
        rs = [r for r in records if split == "all" or r["split"] == split]
        if not rs:
            continue
        per: dict[str, Any] = {}
        for prod in (*N.PRODUCTS, "both"):
            prs = [r for r in rs if prod == "both" or r["product"] == prod]
            if not prs:
                continue
            conds: dict[str, Any] = {
                N_: {
                    "n_units": len(prs),
                    "e3_recall_mean": statistics.fmean(r["e3"]["recall"] for r in prs),
                    "missing_gold": sum(
                        sum(c != "full" for c in r["e3"]["coverage"].values()) for r in prs
                    ),
                    "n_gold": sum(r["n_gold"] for r in prs),
                }
            }
            for c in SHOWN:
                rows = [r["scores"][c] for r in prs if c in r["scores"]]
                if rows:
                    conds[c] = _cond_summary(rows, seed)
            paired = {}
            for a, b in PAIRS:
                both = [r for r in prs if a in r["metrics"] and b in r["metrics"]]
                if both:
                    paired[f"{b} vs {a}"] = {
                        m: E.sign_test(
                            {f"{r['meeting_id']}:{r['product']}": r["metrics"][a][m] for r in both},
                            {f"{r['meeting_id']}:{r['product']}": r["metrics"][b][m] for r in both},
                        )
                        for m in ("precision", "recall_missing")
                    }
            go = {
                c: go_checks(conds[c], {b: _beats(conds[c], conds.get(b)) for b in BASELINES})
                for c in (C3, CU, C3S, CUS)
                if c in conds
            }
            per[prod] = {"conditions": conds, "paired": paired, "go": go}
        out[split] = {"products": per, "repairs": sum(len(r["repairs"]) for r in rs)}
    return out


def aggregate(result: Mapping[str, Any]) -> dict[str, Any]:
    """Numbers only: meta (no per-call ledger, question texts or unit list) and the
    summary."""
    meta = dict(result["meta"])
    for k in ("ledger_by_stage", "list_question", "chat_question", "units"):
        meta.pop(k, None)  # long question texts and meeting IDs stay in the local result
    return {"meta": meta, "summary": result["summary"]}


def private_texts(records: Sequence[Mapping[str, Any]]) -> list[str]:
    """Strings an aggregate must not contain: items, quotes, notes, answers, reasons."""
    out: list[str] = []
    for r in records:
        out += [c["text"] for c in r["candidates"]]
        out += [c["quote"] for c in r["coverage_items"]] + [r["regen_markdown"]]
        out += [r["chat_raw"] or ""] + [v["reason"] for p in r["pass_verdicts"] for v in p.values()]
    return out


# -- quote and reprice ------------------------------------------------------------------------


@dataclass(frozen=True)
class Assumed:
    """ASSUMED for the quote only (pairs: expected, high); the pilot's ledger replaces them."""

    map_out: tuple[float, float] = (600.0, 1400.0)  # one combined question: more per chunk
    cands_per_chunk: tuple[float, float] = (8.0, 14.0)
    cand_tokens: int = 70
    merge_out_per_cand: tuple[float, float] = (40.0, 80.0)
    items_per_unit: tuple[float, float] = (14.0, 24.0)  # after merging
    kept_share: tuple[float, float] = (0.6, 0.8)  # quote found and checked as agreed etc.
    check_in: int = 1000  # item + quote + window, beyond the system prompt
    check_out: tuple[float, float] = (150.0, 300.0)
    dedupe_item: int = 120
    dedupe_out: tuple[float, float] = (120.0, 250.0)
    regen_out: tuple[float, float] = (2500.0, 5000.0)
    regen_bullets: tuple[float, float] = (25.0, 40.0)
    shown_share: tuple[float, float] = (0.6, 0.8)  # of kept items, after dedupe
    chat_items: tuple[float, float] = (6.0, 12.0)
    cand_item_tokens: int = 40
    judge_out_base: tuple[float, float] = (800.0, 1600.0)
    judge_out_per_item: tuple[float, float] = (90.0, 180.0)


ASSUMED = Assumed()


@dataclass
class StageQuote:
    stage: str
    model: str
    calls: float = 0.0
    cached: int = 0
    tin: list[float] = field(default_factory=lambda: [0.0, 0.0])
    tout: list[float] = field(default_factory=lambda: [0.0, 0.0])
    assumed: bool = False  # some calls' input or count is ASSUMED
    shapes: list[PromptShape] = field(default_factory=list)
    # A stage priced by its own module (E3's quote): [expected, high, expected cached, high cached]
    fixed: list[float] | None = None

    def add(self, tin: tuple[float, float], tout: tuple[float, float], n: float = 1.0) -> None:
        self.calls += n
        for i in (0, 1):
            self.tin[i] += tin[i] * n
            self.tout[i] += tout[i] * n

    def add_split(
        self, n: tuple[float, float], tin: float, tout: tuple[float, float], shape: PromptShape
    ) -> None:
        """``n`` calls (expected, high count), each ``tin`` input and ``tout`` output; the
        expected count's shapes go to the prompt-cache estimate."""
        self.assumed = True
        self.calls += (n[0] + n[1]) / 2
        for i in (0, 1):
            self.tin[i] += n[i] * tin
            self.tout[i] += n[i] * tout[i]
        self.shapes += [shape] * round(n[0])

    def row(self) -> dict[str, Any]:
        if self.fixed is not None:
            exp, high, exp_c, high_c = self.fixed
        else:
            exp = cost_usd(self.model, round(self.tin[0]), round(self.tout[0]))
            high = cost_usd(self.model, round(self.tin[1]), round(self.tout[1]))
            saving = simulate_prompt_cache(self.model, self.shapes).saving_usd(self.model)
            exp_c, high_c = exp - saving, high - saving
        return {
            "stage": self.stage, "model": self.model, "calls": round(self.calls, 1),
            "cached": self.cached, "input_tokens": round(self.tin[0]), "assumed": self.assumed,
            "usd_expected": exp, "usd_high": high, "usd_expected_cached": exp_c,
            "usd_high_cached": high_c,
        }  # fmt: skip


def _try(fn: Callable[[], Any]) -> Any:
    """``fn()`` on offline clients, or None on a cache miss."""
    try:
        return fn()
    except LLMError:
        return None


def offline(c: LLMClient) -> LLMClient:
    """A cache-only twin of ``c`` (own ledger): replays, never calls the API."""
    return E._offline(c)


def quote(
    units: Sequence[N.Unit],
    clients: Clients,
    counters: Mapping[str, Counter],
    chat_answers: Mapping[str, str],
    passes: int,
    seed: int,
    e3_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """Price every call of a run on ``units`` (meeting by meeting), stage by stage. A step
    whose inputs are already in the disk cache is replayed offline and priced exactly (cached
    calls cost $0); otherwise counts and output sizes are :class:`Assumed`. Input is exact
    (``counters[model]``: count_tokens, free) where the text exists. The cached price replays
    the uncached calls through a warm prompt cache (system; notes for the dedupe; meeting and
    notes for the judge). The E3 baseline is priced by E3's own quote."""
    a = ASSUMED
    off = Clients(*(offline(c) for c in (clients.lister, clients.checker, clients.regen,
                                         clients.judge)))  # fmt: skip
    st = {
        "map": StageQuote("L map (e1.enumerate)", clients.lister.model),
        "merge": StageQuote("L merge (e1.merge)", clients.lister.model),
        "check": StageQuote("E2 check (e2.agreed)", clients.checker.model),
        "dedupe": StageQuote("dedupe, Coverage + RD (e4.dedupe)", clients.checker.model),
        "regen": StageQuote("R regenerate (e4.regen)", clients.regen.model),
        "judge": StageQuote(f"judge x{passes} (e4.judge)", clients.judge.model),
        "e3": StageQuote(
            f"E3 baseline x{E3_PASSES} ({e3_prompt or N.PROMPT})", clients.judge.model
        ),
    }
    st["e3"].fixed = [0.0, 0.0, 0.0, 0.0]

    def tok(model: str, text: str) -> int:
        return counters[model]([text])[0]

    def sys_t(client: LLMClient, prompt: str) -> int:
        return tok(client.model, load_prompt(prompt)) + OVERHEAD

    def exact(s: StageQuote, req: LLMRequest, client: LLMClient, out: tuple[float, float],
              segs: Sequence[tuple[str, str]] = ()) -> None:  # fmt: skip
        if client.is_cached(req):
            s.calls += 1
            s.cached += 1
            return
        tin = tok(client.model, f"{req.system}\n\n{req.user}") + OVERHEAD
        s.add((tin, tin), out)
        seg = [("sys:" + req.prompt_id, sys_t(client, req.prompt_id))]
        seg += [(ident, tok(client.model, text)) for ident, text in segs]
        s.shapes.append(PromptShape(tuple(seg), max(0, tin - sum(t for _, t in seg))))

    for u in units:
        ctx = list_context(u)
        chunks = E.chunk_lines(u.transcript)
        for i, ch in enumerate(chunks):
            exact(st["map"], E.enumerate_request(ctx.question, ch, i, len(chunks)),
                  clients.lister, a.map_out)  # fmt: skip
        listed = _try(functools.partial(E.list_items, off.lister, ctx))
        cov = _try(functools.partial(coverage, u, off.lister, off.checker))
        n_found: tuple[float, float]
        if listed is not None:
            cands_l, items = listed
            if len(cands_l) > 1:
                n = len(cands_l)
                exact(st["merge"], E.merge_request(ctx.question, cands_l), clients.lister,
                      (a.merge_out_per_cand[0] * n, a.merge_out_per_cand[1] * n))  # fmt: skip
            for n_, it in enumerate(items):
                if it.found:
                    req = A.agreed_request(ctx.question, as_e1_item(it, n_), u.transcript)
                    exact(st["check"], req, clients.checker, a.check_out)
            nf = float(sum(it.found for it in items))
            n_found = (nf, nf)
        else:
            n_c = (len(chunks) * a.cands_per_chunk[0], len(chunks) * a.cands_per_chunk[1])
            s_m = sys_t(clients.lister, E.MERGE_PROMPT)
            st["merge"].assumed = True
            st["merge"].add(
                (s_m + n_c[0] * a.cand_tokens, s_m + n_c[1] * a.cand_tokens),
                (n_c[0] * a.merge_out_per_cand[0], n_c[1] * a.merge_out_per_cand[1]),
            )
            n_found = a.items_per_unit
            s_c = sys_t(clients.checker, A.PROMPT)
            st["check"].add_split(n_found, s_c + a.check_in, a.check_out,
                                  PromptShape((("sys:check", s_c),), a.check_in))  # fmt: skip
        notes_t = tok(clients.checker.model, notes_block(u))
        if cov is not None:
            for c in cov:
                if c.kept:
                    exact(st["dedupe"], dedupe_request(u, c.category or "", c.text, c.quote),
                          clients.checker, a.dedupe_out,
                          [(f"notes:{u.key}", notes_block(u))])  # fmt: skip
            n_shown = (float(sum(c.shown for c in cov)),) * 2
        else:
            s_d = sys_t(clients.checker, DEDUPE_PROMPT)
            n_kept = (n_found[0] * a.kept_share[0], n_found[1] * a.kept_share[1])
            st["dedupe"].add_split(
                n_kept, s_d + notes_t + a.dedupe_item, a.dedupe_out,
                PromptShape((("sys:dedupe", s_d), (f"notes:{u.key}", notes_t)), a.dedupe_item),
            )  # fmt: skip
            n_shown = (n_kept[0] * a.shown_share[0], n_kept[1] * a.shown_share[1])
        exact(st["regen"], regen_request(u), clients.regen, a.regen_out)
        e3q = e3_quote(u, clients.judge, counters[clients.judge.model], e3_prompt)
        st["e3"].calls += e3q.calls
        st["e3"].cached += e3q.cached
        st["e3"].tin[0] += e3q.input_tokens
        assert st["e3"].fixed is not None
        for i, v in enumerate((e3q.usd_expected, e3q.usd_high, e3q.usd_expected_cached,
                               e3q.usd_high_cached)):  # fmt: skip
            st["e3"].fixed[i] += v
        # the judge: exact when every input is cached, else ASSUMED item counts
        rgn = _try(functools.partial(regen, u, off.regen))
        if rgn is not None:  # RD's dedupe of the regenerated bullets
            for b in regen_dap(rgn[1]):
                req = dedupe_request(u, regen_category(b), b.text, "")
                exact(st["dedupe"], req, clients.checker, a.dedupe_out,
                      [(f"notes:{u.key}", notes_block(u))])  # fmt: skip
        else:
            s_d = sys_t(clients.checker, DEDUPE_PROMPT)
            n_rd = (a.regen_bullets[0] * 0.75, a.regen_bullets[1] * 0.75)
            st["dedupe"].add_split(
                n_rd, s_d + notes_t + a.dedupe_item, a.dedupe_out,
                PromptShape((("sys:dedupe", s_d), (f"notes:{u.key}", notes_t)), a.dedupe_item),
            )  # fmt: skip
        raw = chat_answers.get(u.key)
        chat = chat_items(raw) if raw is not None else None
        if cov is not None and rgn is not None:
            cands = candidates(cov, rgn[1], chat or [], seed, u.key)
            shared, notes, _ = judge_parts(u, cands, 1)
            n_c2 = len(cands)
            for k_ in range(1, passes + 1):
                exact(st["judge"], judge_request(u, cands, k_), clients.judge,
                      (a.judge_out_base[0] + a.judge_out_per_item[0] * n_c2,
                       a.judge_out_base[1] + a.judge_out_per_item[1] * n_c2),
                      [(f"m:{u.meeting_id}", shared), (f"p:{u.key}", notes)])  # fmt: skip
            if raw is None and u.product == "granola":
                st["judge"].assumed = True  # Chat's items not recorded yet: not counted
        else:
            shared, notes, _ = judge_parts(u, [], 1)
            s_j = sys_t(clients.judge, JUDGE_PROMPT)
            t_sh, t_no = tok(clients.judge.model, shared), tok(clients.judge.model, notes)
            n_chat = (
                (float(len(chat)),) * 2 if chat is not None
                else a.chat_items if u.product == "granola" else (0.0, 0.0)
            )  # fmt: skip
            n_rb = (float(len(rgn[1])),) * 2 if rgn is not None else a.regen_bullets
            for i in (0, 1):
                n_items = n_shown[i] + n_rb[i] + n_chat[i]
                tail = n_items * a.cand_item_tokens
                st["judge"].assumed = True
                st["judge"].calls += passes / 2
                st["judge"].tin[i] += passes * (s_j + t_sh + t_no + tail)
                st["judge"].tout[i] += passes * (
                    a.judge_out_base[i] + a.judge_out_per_item[i] * n_items
                )
                if i == 0:
                    seg = (("sys:judge", s_j), (f"m:{u.meeting_id}", t_sh),
                           (f"p:{u.key}", t_no))  # fmt: skip
                    st["judge"].shapes += [PromptShape(seg, round(tail))] * passes
    return [s.row() for s in st.values()]


def totals(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    keys = ("usd_expected", "usd_high", "usd_expected_cached", "usd_high_cached")
    return {k: sum(float(r[k]) for r in rows) for k in keys}


def quote_markdown(sections: Sequence[tuple[str, Sequence[Mapping[str, Any]]]]) -> str:
    lines = ["# E4 quote", ""]
    for title, rows in sections:
        lines += [f"## {title}", "",
                  "| Stage | Model | Calls | Cached | Input tokens | Exact? | Expected | High | "
                  "Expected (prompt cache) | High (prompt cache) |",
                  "|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
        for r in rows:
            lines.append(
                f"| {r['stage']} | {r['model']} | {r['calls']} | {r['cached']} | "
                f"{r['input_tokens']:,} | {'partly ASSUMED' if r['assumed'] else 'yes'} | "
                f"${r['usd_expected']:.3f} | ${r['usd_high']:.3f} | "
                f"${r['usd_expected_cached']:.3f} | ${r['usd_high_cached']:.3f} |"
            )
        t = totals(rows)
        lines += [
            f"| **Total** | | | | | | **${t['usd_expected']:.2f}** | **${t['usd_high']:.2f}** | "
            f"**${t['usd_expected_cached']:.2f}** | **${t['usd_high_cached']:.2f}** |",
            "",
        ]
    return "\n".join(lines)


STAGE_GROUPS = {
    "list": LIST_STAGES,
    "check": (A.STAGE,),
    "dedupe": (DEDUPE_STAGE,),
    "regen": (REGEN_STAGE,),
    "judge": (JUDGE_STAGE,),
    "e3": (N.STAGE,),
}


def unit_size(unit: N.Unit, counter: Counter) -> dict[str, int]:
    """Exact sizes the stages scale with: the product transcript (listing, check, dedupe and
    regenerating grow with it) and the judge's fixed part (meeting + notes)."""
    shared, notes, _ = judge_parts(unit, [], 1)
    t_tr, t_sh, t_no = counter([unit.product_text(), shared, notes])
    return {"transcript": t_tr, "judge": t_sh + t_no}


def reprice(
    ledger_records: Sequence[Mapping[str, Any]],
    pilot_sizes: Mapping[str, int],
    full_sizes: Mapping[str, int],
    pilot_passes: int,
    full_passes: int,
    e3_full_usd: float,
    margin: float = MARGIN,
) -> dict[str, Any]:
    """The full run priced from the pilot's ledger, stage by stage: the pilot's recorded cost
    (as billed, prompt-cache reads and writes included; disk-cache replays at their original
    cost) scaled by product-transcript tokens (list, check, dedupe, regen) or by the judge's
    fixed part times passes (judge); the E3 baseline as quoted (``e3_full_usd``: its uncached
    calls). Then times ``margin``."""
    by: dict[str, float] = {}
    for g, stages in STAGE_GROUPS.items():
        by[g] = sum(float(r["cost_usd"]) for r in ledger_records if r["stage"] in stages)
    k_tr = full_sizes["transcript"] / pilot_sizes["transcript"] if pilot_sizes["transcript"] else 0
    k_j = (full_sizes["judge"] * full_passes) / (pilot_sizes["judge"] * pilot_passes or 1)
    full = {g: by[g] * k_tr for g in ("list", "check", "dedupe", "regen")}
    full["judge"] = by["judge"] * k_j
    full["e3"] = e3_full_usd
    usd = sum(full.values())
    return {
        "pilot_usd_by_group": by,
        "pilot_usd": sum(by.values()),
        "scale_transcript": k_tr,
        "scale_judge": k_j,
        "full_usd_by_group": full,
        "full_usd": usd,
        "margin": margin,
        "full_usd_with_margin": usd * margin,
    }


def write_json(path: Path, obj: Any) -> None:
    E.write_json(path, obj)
