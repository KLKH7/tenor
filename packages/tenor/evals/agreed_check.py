"""E2: was it agreed? (docs/tracks/ANSWERS.md, "E2").

E1 listed every item from the transcript. Shown as a "+N more said in the meeting" line under
Granola Chat's answer, those extras raised recall but were noisy, and the wrong ones were
over-inclusion ("proposed but not decided", "not assigned"), not invention. E2 asks of each
E1 item a different question from E1's check: not "was it said?" but "was it agreed / assigned
/ raised as a problem?".

Per question, from E1's cached record (no new listing or judge calls):

1. **Classify** each E1 item (``agreed_check.v3``, one call per item): the question, the item,
   its quote and ±:data:`WINDOW` Granola transcript lines around its cited lines. Output: a
   status (:data:`STATUSES`), whether it answers the question as asked, a 0-100 confidence and
   a one-line reason.
2. **Keep** an item as an extra only if its status is the question's target
   (:data:`TARGET`) and it answers the question.
3. **Dedupe against Chat** with E1's judge mapping: an item is already in Chat's answer when it
   states at least one gold item and every gold item it states is one Chat's answer covers.
   This is an oracle (a product has no gold); it is also exactly how E1's extras were counted,
   so the comparison isolates the classifier. A product needs its own dedupe step instead.
4. **Cap** at K, ranked by confidence (ties: transcript order). K = 2, 3 and uncapped.

Scoring reuses E1's per-item judge verdicts: an extra is right (lenient) if the judge mapped it
to a gold item or marked it true per AMI's reference transcript; strictly right if it maps to
gold. Baselines: ``U`` every non-duplicate E1 item; ``V`` the ones E1's checker confirmed (the
"+N more" line E1 measured: ~7.6 extras per question, ~66% right).
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tenor.evals.list_check import Context, Line, bootstrap_ci, sign_test, wilson_ci
from tenor.llm.client import LLMClient, LLMRequest, cost_usd, load_prompt

# v1 (pilot, DEV) labelled agreed tasks "agreed_decision", so action questions lost them, and
# judged team-wide tasks as not answering a question about one role. v2's fix sent a group's
# agreed next step (a gold decision) to "assigned_action" (Opus, DEV); v3 keeps those decisions.
PROMPT = "agreed_check.v3"
STAGE = "e2.agreed"
WINDOW = 8  # transcript lines shown either side of the cited lines
STATUSES = (
    "agreed_decision",
    "assigned_action",
    "problem_raised",
    "proposed_only",
    "mentioned_only",
    "other_topic",
)
Status = Literal[
    "agreed_decision",
    "assigned_action",
    "problem_raised",
    "proposed_only",
    "mentioned_only",
    "other_topic",
]
TARGET = {
    "decision": "agreed_decision",
    "action_what": "assigned_action",
    "problem": "problem_raised",
}
CAPS: tuple[tuple[str, int | None], ...] = (("A", None), ("A3", 3), ("A2", 2))
C, U, V = "C", "U", "V"
VARIANTS = (C, U, V, *(n for n, _ in CAPS))
LABELS = {
    C: "Chat alone",
    U: "Chat + every E1 extra (unfiltered)",
    V: "Chat + E1-checked extras (the '+N more' line)",
    "A": "Chat + agreed extras, uncapped",
    "A3": "Chat + agreed extras, top 3",
    "A2": "Chat + agreed extras, top 2",
}
# Each agreed variant against both E1 baselines.
PAIRS = tuple((base, name) for name, _ in CAPS for base in (U, V))
METRICS = ("recall", "n_extras", "precision_any", "precision_gold", "gold_dropped")
TEST_METRICS = ("recall", "n_extras", "precision_any")
PILOT_MAX_USD = 0.50
MARGIN = 1.5
ASSUMED_OUT = (150.0, 300.0)  # output tokens per call (expected, high); ASSUMED until a pilot
OVERHEAD = 250  # structured-output tokens per call (as list_check.Assumed.overhead)
# Small, so a batch's worst case (input + max_tokens output per item) fits a pilot cap; E1's
# verifier never wrote more than 190 output tokens, the v2 pilot on Haiku 226.
MAX_TOKENS = 500


# -- E1 items, as E2 reads them ---------------------------------------------------------------


@dataclass(frozen=True)
class E1Item:
    """One E1 listed item with E1's verdicts. ``redundant``: already in Chat's answer (it states
    a gold item and every gold item it states is one Chat covers; the judge's mapping)."""

    id: str
    order: int  # position in E1's list (transcript order of first mention)
    text: str
    quote: str
    lines: tuple[int, ...]
    gold: tuple[str, ...]
    in_transcript: bool
    confirmed: bool  # E1's checker (verify_item.v2) said supported
    redundant: bool

    @property
    def right(self) -> bool:
        """E1's lenient verdict: states a gold item or is true per the reference transcript."""
        return bool(self.gold) or self.in_transcript


def chat_covered(rec: Mapping[str, Any]) -> set[str]:
    """Gold IDs Chat's answer covers, per E1's judge."""
    return {g for j in rec["judged"]["C"] for g in j["gold"]}


def e1_items(rec: Mapping[str, Any]) -> list[E1Item]:
    """An E1 record's L items joined with the judge's verdicts and the checker's."""
    cov = chat_covered(rec)
    judged = {j["id"]: j for j in rec["judged"]["L"]}
    out = []
    for n, it in enumerate(rec["items"]):
        j = judged[it["id"]]
        gold = tuple(j["gold"])
        out.append(
            E1Item(
                id=it["id"],
                order=n,
                text=it["text"],
                quote=it["quote"],
                lines=tuple(it["lines"]),
                gold=gold,
                in_transcript=bool(j["in_transcript"]),
                confirmed=bool(rec.get("confirmed", {}).get(it["id"], False)),
                redundant=bool(gold) and set(gold) <= cov,
            )
        )
    return out


# -- the classifier ---------------------------------------------------------------------------


class AgreedOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    status: Status
    answers_question: bool
    confidence: int


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "status": {"type": "string", "enum": list(STATUSES)},
        "answers_question": {"type": "boolean"},
        "confidence": {"type": "integer"},
    },
    "required": ["reason", "status", "answers_question", "confidence"],
    "additionalProperties": False,
}


def window(cited: Sequence[int], transcript: Sequence[Line], k: int = WINDOW) -> list[Line]:
    """The cited lines plus ``k`` lines either side of each, in transcript order."""
    wanted = {i for c in cited for i in range(c - k, c + k + 1)}
    return [x for x in transcript if x.index in wanted]


def agreed_request(
    question: str, item: E1Item, transcript: Sequence[Line], k: int = WINDOW
) -> LLMRequest:
    shown = window(item.lines, transcript, k)
    cited = ", ".join(f"L{i:04d}" for i in item.lines) or "none"
    body = "\n".join(x.render() for x in shown) or "(no lines cited)"
    return LLMRequest(
        stage=STAGE,
        prompt_id=PROMPT,
        system=load_prompt(PROMPT),
        user=(
            f'Question: {question}\n\nItem: {item.text}\n\nQuote: "{item.quote}"\n\n'
            f"Cited lines: {cited}. Transcript window:\n{body}"
        ),
        schema=SCHEMA,
        max_tokens=MAX_TOKENS,
        effort="low",
    )


@dataclass(frozen=True)
class Verdict:
    status: str
    answers_question: bool
    confidence: int  # clamped to 0-100
    reason: str


def classify(reader: LLMClient, ctx: Context, items: Sequence[E1Item]) -> dict[str, Verdict]:
    """One classifier call per item: item id -> :class:`Verdict`."""
    out = {}
    for it in items:
        v = reader.structured(agreed_request(ctx.question, it, ctx.transcript), AgreedOut)
        out[it.id] = Verdict(v.status, v.answers_question, max(0, min(100, v.confidence)), v.reason)
    return out


# -- filter, rank, cap ------------------------------------------------------------------------


def keeps(category: str, v: Verdict) -> bool:
    """An item stays an extra if its status is the question's target and it answers it."""
    return v.status == TARGET[category] and v.answers_question


def agreed_extras(
    items: Sequence[E1Item], verdicts: Mapping[str, Verdict], category: str, k: int | None
) -> list[str]:
    """Non-redundant items the classifier keeps, by confidence (ties: E1 order), top ``k``."""
    kept = [it for it in items if not it.redundant and keeps(category, verdicts[it.id])]
    kept.sort(key=lambda it: (-verdicts[it.id].confidence, it.order))
    return [it.id for it in (kept if k is None else kept[:k])]


def baseline_extras(items: Sequence[E1Item], variant: str) -> list[str]:
    """``U``: every non-redundant item; ``V``: those E1's checker confirmed."""
    if variant not in (U, V):
        raise ValueError(f"not a baseline: {variant!r}")
    return [it.id for it in items if not it.redundant and (variant == U or it.confirmed)]


# -- scoring ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtrasScore:
    """Chat's answer plus some extras, scored with E1's verdicts. Precision is of the extras
    only (None with no extras); ``gold_dropped``: gold items U's extras add that these don't."""

    n_gold: int
    n_extras: int
    covered: int
    right: int
    gold_right: int
    gold_dropped: int

    @property
    def recall(self) -> float:
        return self.covered / self.n_gold if self.n_gold else 0.0

    @property
    def precision_any(self) -> float | None:
        return self.right / self.n_extras if self.n_extras else None

    @property
    def precision_gold(self) -> float | None:
        return self.gold_right / self.n_extras if self.n_extras else None

    def row(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "recall": self.recall,
            "precision_any": self.precision_any,
            "precision_gold": self.precision_gold,
        }


def score_extras(
    items: Sequence[E1Item],
    extras: Iterable[str],
    gold_ids: Sequence[str],
    chat: set[str],
) -> ExtrasScore:
    """Score Chat (covering ``chat``) plus ``extras`` against ``gold_ids``."""
    by = {it.id: it for it in items}
    picked = [by[i] for i in extras]
    gold = set(gold_ids)
    added = {g for it in picked for g in it.gold} - chat
    could = {g for it in items if not it.redundant for g in it.gold} - chat
    return ExtrasScore(
        n_gold=len(gold),
        n_extras=len(picked),
        covered=len((chat | added) & gold),
        right=sum(it.right for it in picked),
        gold_right=sum(bool(it.gold) for it in picked),
        gold_dropped=len((could - added) & gold),
    )


def score_question(
    rec: Mapping[str, Any], verdicts: Mapping[str, Verdict]
) -> dict[str, dict[str, Any]]:
    """Every variant's score row for one E1 record."""
    items = e1_items(rec)
    gids = [g["id"] for g in rec["gold"]]
    chat = chat_covered(rec)
    sets: dict[str, list[str]] = {C: [], U: baseline_extras(items, U), V: baseline_extras(items, V)}
    for name, k in CAPS:
        sets[name] = agreed_extras(items, verdicts, rec["category"], k)
    return {
        name: {**score_extras(items, ids, gids, chat).row(), "extras": ids}
        for name, ids in sets.items()
    }


def run_question(rec: Mapping[str, Any], ctx: Context, reader: LLMClient) -> dict[str, Any]:
    """Classify one question's E1 items and score every variant; a JSON-ready record (holds
    item text: local only)."""
    items = e1_items(rec)
    verdicts = classify(reader, ctx, items)
    return {
        "qid": rec["qid"],
        "split": rec["split"],
        "category": rec["category"],
        "items": [asdict(it) for it in items],
        "verdicts": {i: asdict(v) for i, v in verdicts.items()},
        "variants": score_question(rec, verdicts),
    }


# -- summary ----------------------------------------------------------------------------------


def _variant_summary(rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    out: dict[str, Any] = {"n_questions": len(rows)}
    for m in METRICS:
        vals = [float(r[m]) for r in rows if r[m] is not None]
        out[f"mean_{m}"] = statistics.fmean(vals) if vals else None
        out[f"ci_{m}"] = bootstrap_ci(vals, seed)
    n, right = sum(r["n_extras"] for r in rows), sum(r["right"] for r in rows)
    out["micro_extras"] = n
    out["micro_precision_any"] = right / n if n else None
    out["ci_micro_precision_any"] = wilson_ci(right, n)
    out["gold_dropped_total"] = sum(r["gold_dropped"] for r in rows)
    return out


def diagnostics(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How the classifier's keep / drop lines up with E1's judge, over every item and over the
    non-redundant ones (the candidates for extras); and the status counts."""
    statuses: dict[str, int] = dict.fromkeys(STATUSES, 0)
    cells = {
        scope: {k: {"items": 0, "right": 0, "gold": 0} for k in ("kept", "dropped")}
        for scope in ("all", "non_redundant")
    }
    for r in records:
        for it in r["items"]:
            v = r["verdicts"][it["id"]]
            statuses[v["status"]] += 1
            kept = "kept" if keeps(r["category"], Verdict(**v)) else "dropped"
            right = bool(it["gold"]) or it["in_transcript"]
            for scope in ("all", "non_redundant"):
                if scope == "non_redundant" and it["redundant"]:
                    continue
                c = cells[scope][kept]
                c["items"] += 1
                c["right"] += int(right)
                c["gold"] += int(bool(it["gold"]))
    gold_kept = {s: cells[s]["kept"]["gold"] for s in cells}
    gold_all = {s: cells[s]["kept"]["gold"] + cells[s]["dropped"]["gold"] for s in cells}
    return {
        "statuses": statuses,
        "keep_vs_judge": cells,
        "gold_items_kept": {s: f"{gold_kept[s]} / {gold_all[s]}" for s in cells},
    }


def summarize(records: Sequence[Mapping[str, Any]], seed: int = 42) -> dict[str, Any]:
    """Per split (dev, test, all): each variant's means with 95% bootstrap CIs over questions,
    micro precision with a Wilson CI, gold items dropped; paired sign tests (:data:`PAIRS`);
    classifier diagnostics."""
    out: dict[str, Any] = {}
    for split in ("dev", "test", "all"):
        rs = [r for r in records if split == "all" or r["split"] == split]
        if not rs:
            continue
        per = {v: _variant_summary([r["variants"][v] for r in rs], seed) for v in VARIANTS}
        paired = {
            f"{b} vs {a}": {
                m: sign_test(
                    {r["qid"]: r["variants"][a][m] for r in rs},
                    {r["qid"]: r["variants"][b][m] for r in rs},
                )
                for m in TEST_METRICS
            }
            for a, b in PAIRS
        }
        out[split] = {"variants": per, "paired": paired, "diagnostics": diagnostics(rs)}
    return out


# -- quote and reprice ------------------------------------------------------------------------


@dataclass
class Quote:
    calls: int = 0
    cached: int = 0
    input_tokens: int = 0
    usd_expected: float = 0.0
    usd_high: float = 0.0
    per_question: dict[str, int] = field(default_factory=dict)  # uncached calls


def quote(
    pairs: Sequence[tuple[Mapping[str, Any], Context]],
    reader: LLMClient,
    counter: Any,
    out: tuple[float, float] = ASSUMED_OUT,
    *,
    batch: bool = False,
) -> Quote:
    """Price classifying every item of ``pairs`` (E1 record, context). Input is exact
    (``counter``: count_tokens, free) plus :data:`OVERHEAD`; output is ASSUMED. Cached calls
    cost $0; ``batch`` applies the Message Batches discount."""
    q = Quote()
    for rec, ctx in pairs:
        reqs = [agreed_request(ctx.question, it, ctx.transcript) for it in e1_items(rec)]
        todo = [r for r in reqs if not reader.is_cached(r)]
        q.calls += len(reqs)
        q.cached += len(reqs) - len(todo)
        q.per_question[rec["qid"]] = len(todo)
        if not todo:
            continue
        tin = sum(counter([f"{r.system}\n\n{r.user}" for r in todo])) + OVERHEAD * len(todo)
        q.input_tokens += tin
        q.usd_expected += cost_usd(reader.model, tin, round(out[0] * len(todo)), batch=batch)
        q.usd_high += cost_usd(reader.model, tin, round(out[1] * len(todo)), batch=batch)
    return q


def reprice(
    ledger_records: Sequence[Mapping[str, Any]], full_uncached_calls: int, margin: float = MARGIN
) -> dict[str, Any]:
    """The full run priced from the pilot's ledger: mean recorded cost per classifier call
    (cached calls at their original cost) times the full run's uncached calls, times
    ``margin``."""
    # A batch run records each call twice: once as "<stage>.batch" (billed) and once as the
    # cached replay under STAGE (same price). Only STAGE rows are counted.
    costs = [float(r["cost_usd"]) for r in ledger_records if r["stage"] == STAGE]
    per = statistics.fmean(costs) if costs else 0.0
    usd = per * full_uncached_calls
    return {
        "pilot_calls": len(costs),
        "usd_per_call": per,
        "full_uncached_calls": full_uncached_calls,
        "full_usd": usd,
        "margin": margin,
        "full_usd_with_margin": usd * margin,
    }
