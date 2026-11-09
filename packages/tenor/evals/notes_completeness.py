"""E3: notes completeness (docs/tracks/NOTES.md, "E3: notes completeness").

What share of what was decided, assigned and raised in a meeting appears in a product's notes
(not Chat), and is what the notes say about it true?

* **Unit**: one product's notes for one AMI meeting (the replay set, both products recorded,
  and AMI has an abstractive summary for it).
* **Gold**: AMI's human DECISIONS / ACTIONS / PROBLEMS sentences for the meeting, one item per
  sentence (``D1``, ``A1``, ``P1``, ...), plus the speaker roster.
* **Notes items**: the product's AI notes (``summary_markdown``) split deterministically into
  bullets (:func:`split_notes`), each with the heading it sat under; a sub-point keeps its
  parent. No LLM in the split, so a bullet may hold more than one claim.
* **Judge** (one call per unit, Sonnet as in E1, :data:`PROMPT`): first each bullet's kind
  (decision / action / problem / other), the gold items it states, and whether AMI's reference
  transcript supports it (yes / partly / no); then each gold item covered fully / partly / not
  by which bullets and, if not fully, whether the product's own transcript has it (with a
  verbatim quote, checked here by string match).

**Metrics** per unit and category: recall (full = 1, partial = 0.5; and full only), precision
of the bullets of that kind (right = states a gold item or fully supported; strict: fully
supported only), F1 (lenient precision with weighted recall); "said, not
noted" = gold items missing from the notes that the product's transcript has. Pooled over
meetings with bootstrap CIs; Granola vs Wispr paired per meeting (sign test).
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tenor.core.models import Channel, MeetingNote
from tenor.evals.list_check import (
    Line,
    bootstrap_ci,
    merge_turns,
    quote_found,
    sign_test,
    transcript_lines,
)
from tenor.llm.client import LLMClient, LLMRequest, cost_usd, load_prompt

# v1 (pilot, DEV): gold-side and bullet-side links disagreed, a bullet about a different task was
# linked to a gold item, and the owner rule for action coverage was implicit. v2 judges the
# bullets first, then the gold items, and states the consistency and owner rules. v2 (pilot) still
# read a generic label the notes themselves define ("Speaker 2 is project manager") as unmatched,
# and marked bullets "partly" for an unmatched label; v3 says how to read labels. v3 (pilot): its
# example of a non-match was taken from the DEV meeting and made the judge reject a real match
# there, and "an owner nobody confirmed" still sent unmatched labels to "partly"; v4 uses a
# neutral example and drops that phrase.
PROMPT = "notes_completeness.v4"
STAGE = "e3.judge"
JUDGE_MODEL = "claude-sonnet-5-5"  # E1's judge
PILOT_MEETING = "IS1008a"  # the one DEV meeting (ami/questions.DEV_MEETINGS)
PILOT_MAX_USD = 0.50
MARGIN = 1.5
MAX_TOKENS = 16000
OVERHEAD = 250  # structured-output tokens per call (as list_check.Assumed.overhead)
MAX_PRODUCT_CHARS = 150_000  # product transcript included only up to this size (~40k tokens)
PARAGRAPH_CHARS = 1200  # product transcript: same-speaker lines joined up to this length
PRODUCTS = ("granola", "wispr")
CATEGORIES = ("decision", "action", "problem")
ALL = "all"
SECTIONS = {
    "decisions": ("decision", "D"),
    "actions": ("action", "A"),
    "problems": ("problem", "P"),
}
METRICS = ("recall", "recall_full", "precision", "precision_true", "precision_gold", "f1")
TEST_METRICS = ("recall", "recall_full", "precision", "f1")
# Output tokens per call (expected, high): base + per gold item and bullet. ASSUMED until a
# pilot's ledger replaces them (medium effort: thinking counts as output).
ASSUMED_OUT_BASE = (1500.0, 5000.0)
ASSUMED_OUT_PER_ITEM = (60.0, 120.0)

Coverage = Literal["full", "partial", "none"]
Heard = Literal["yes", "no", "not_assessed"]
Kind = Literal["decision", "action", "problem", "other"]
Support = Literal["yes", "partly", "no", "n/a"]


# -- splitting the notes ----------------------------------------------------------------------


@dataclass(frozen=True)
class Bullet:
    """One notes item: a bullet (with any continuation lines) or a free paragraph."""

    id: str  # "B1", "B2", ... in document order
    section: str  # the heading it sat under; "(top)" before any heading
    text: str
    parent: str | None = None  # the bullet it is a sub-point of
    paragraph: bool = False  # a free paragraph, not a bullet

    def render(self) -> str:
        sub = f" (sub-point of {self.parent})" if self.parent else ""
        return f"{self.id} [section: {self.section}]{sub} {self.text}"


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_BOLD_LINE = re.compile(r"^\s*(?:\*\*|__)(.+?)(?:\*\*|__):?\s*$")
_BULLET = re.compile(r"^(\s*)(?:[-*+\u2022]|\d+[.)])(?:\s+(.*))?$")
_RULE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")  # a horizontal rule: "---", "* * *"
_EMPH = re.compile(r"\*\*|__|`")


def _clean(text: str) -> str:
    return " ".join(_EMPH.sub("", text).split())


def _indent(raw: str) -> int:
    return len(raw.expandtabs(4)) - len(raw.expandtabs(4).lstrip(" "))


def split_notes(markdown: str) -> list[Bullet]:
    """Split notes markdown into :class:`Bullet` s, deterministically.

    Headings (``#``..``######``, or a line that is all bold) set the section. A bullet line
    (``-``, ``*``, ``+``, ``•``, ``1.``) starts an item; a more indented bullet is a sub-point of
    the nearest less indented one. An indented plain line, or a plain line right under a bullet
    with no blank line between, continues that bullet. Any other plain text is a free paragraph
    item (consecutive lines joined). Horizontal rules are ignored; emphasis markers are dropped
    and whitespace collapsed.
    """
    items: list[dict[str, Any]] = []
    section = "(top)"
    stack: list[tuple[int, int]] = []  # (indent, item index) of open bullets
    prev: str = "blank"  # blank | heading | bullet | para

    for raw in markdown.splitlines():
        if not raw.strip() or _RULE.match(raw):
            prev = "blank"
            continue
        head = _HEADING.match(raw) or _BOLD_LINE.match(raw)
        if head and not _BULLET.match(raw):
            section = _clean(head.group(1)) or section
            stack, prev = [], "heading"
            continue
        bullet = _BULLET.match(raw)
        if bullet:
            ind = _indent(bullet.group(1))
            while stack and stack[-1][0] >= ind:
                stack.pop()
            parent = stack[-1][1] if stack else None
            items.append(
                {"section": section, "text": bullet.group(2) or "", "parent": parent, "para": False}
            )
            stack.append((ind, len(items) - 1))
            prev = "bullet"
            continue
        if stack and (_indent(raw) >= 2 or prev == "bullet"):
            sep = " \u2014 " if prev == "blank" else " "  # a paragraph under a bullet
            items[stack[-1][1]]["text"] += sep + raw.strip()
            continue
        if prev == "para":
            items[-1]["text"] += " " + raw.strip()
            continue
        items.append({"section": section, "text": raw.strip(), "parent": None, "para": True})
        stack, prev = [], "para"

    return _renumber(items)


def _renumber(items: Sequence[Mapping[str, Any]]) -> list[Bullet]:
    """Raw items as :class:`Bullet` s: empty ones dropped, IDs ``B1``.. over the kept items in
    order, parent references remapped (a dropped parent leaves none)."""
    kept = [i for i, it in enumerate(items) if _clean(it["text"])]
    new_id = {i: f"B{n}" for n, i in enumerate(kept, 1)}
    out = []
    for i in kept:
        it = items[i]
        p = it["parent"]
        out.append(Bullet(new_id[i], it["section"], _clean(it["text"]), new_id.get(p), it["para"]))
    return out


# -- gold -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldItem:
    id: str  # D1, A1, P1, ...
    category: str  # decision / action / problem
    text: str


def gold_items(sentences: Iterable[Any]) -> list[GoldItem]:
    """AMI's DECISIONS / ACTIONS / PROBLEMS sentences (``corpus.SummarySentence``-like: has
    ``.section`` and ``.text``) as gold items, one per sentence, numbered per category in
    document order. ABSTRACT sentences are not gold."""
    out: list[GoldItem] = []
    count: dict[str, int] = {}
    for s in sentences:
        if s.section not in SECTIONS:
            continue
        cat, prefix = SECTIONS[s.section]
        count[cat] = count.get(cat, 0) + 1
        out.append(GoldItem(f"{prefix}{count[cat]}", cat, " ".join(s.text.split())))
    order = {c: i for i, c in enumerate(CATEGORIES)}
    return sorted(out, key=lambda g: (order[g.category], int(g.id[1:])))


def render_gold(gold: Sequence[GoldItem], roster: str = "") -> str:
    parts = []
    for cat in CATEGORIES:
        rows = [g for g in gold if g.category == cat]
        body = "\n".join(f"{g.id}. {g.text}" for g in rows) or "(none)"
        parts.append(f"{cat.capitalize()}s:\n{body}")
    out = "\n\n".join(parts)
    return f"{out}\n\n{roster}" if roster else out


# -- the product transcript -------------------------------------------------------------------


def paragraphs(lines: Sequence[Line], max_chars: int = PARAGRAPH_CHARS) -> list[str]:
    """``Speaker: text`` paragraphs: consecutive lines by one speaker joined until a paragraph
    would pass ``max_chars`` (a longer single line stays whole). Deterministic."""
    out: list[tuple[str, str]] = []
    for x in lines:
        if out and out[-1][0] == x.speaker and len(out[-1][1]) + 1 + len(x.text) <= max_chars:
            out[-1] = (x.speaker, f"{out[-1][1]} {x.text}")
        else:
            out.append((x.speaker, x.text))
    return [f"{s}: {t}" for s, t in out]


# -- one unit ---------------------------------------------------------------------------------


@dataclass
class Unit:
    """One product's notes for one AMI meeting, with everything the judge reads."""

    meeting_id: str
    product: str
    split: str  # dev / test
    bullets: list[Bullet]
    gold: list[GoldItem]
    roster: str
    reference: list[Line]  # AMI's human transcript (merged turns, role labels)
    transcript: list[Line]  # the product's own transcript

    @property
    def key(self) -> str:
        return f"{self.meeting_id}:{self.product}"

    def product_text(self) -> str:
        return "\n".join(paragraphs(self.transcript))

    @property
    def includes_transcript(self) -> bool:
        """The product transcript goes in the prompt only if it fits :data:`MAX_PRODUCT_CHARS`."""
        text = self.product_text()
        return bool(text) and len(text) <= MAX_PRODUCT_CHARS


def _speaker(u: Any) -> str:
    """A product transcript's speaker label: its own name if any, else the audio channel."""
    return str(u.speaker_name or ("Me" if u.channel is Channel.ME else "Them"))


def make_unit(meeting: Any, note: MeetingNote, product: str, roster: str = "") -> Unit:
    """One unit from an AMI meeting (``corpus.AmiMeeting``) and a product's note for it. The
    note's transcript should already be the one scored elsewhere (``deletion_ami.reader_notes``:
    Granola's microphone turns and Wispr's banner dropped). Notes = ``summary_markdown`` (else
    ``summary_text``); typed private notes are not the product's notes and are left out."""
    from tenor.ami.corpus import meeting_note
    from tenor.ami.questions import DEV_MEETINGS

    ref = merge_turns(transcript_lines(meeting_note(meeting), lambda u: u.speaker_name or "?"))
    return Unit(
        meeting_id=meeting.meeting_id,
        product=product,
        split="dev" if meeting.meeting_id in DEV_MEETINGS else "test",
        bullets=split_notes(note.summary_markdown or note.summary_text or ""),
        gold=gold_items(meeting.sentences),
        roster=roster,
        reference=ref,
        transcript=transcript_lines(note, _speaker),
    )


def judge_user(unit: Unit) -> str:
    ref = "\n".join(x.render() for x in unit.reference)
    if unit.includes_transcript:
        own = f"<product_transcript>\n{unit.product_text()}\n</product_transcript>"
    else:
        own = "Product transcript: not included (mark in_product_transcript not_assessed)."
    notes = "\n".join(b.render() for b in unit.bullets) or "(the notes are empty)"
    return (
        f"Gold items:\n{render_gold(unit.gold, unit.roster)}\n\n"
        f"<reference_transcript>\n{ref}\n</reference_transcript>\n\n{own}\n\n"
        f"Product notes (bullets):\n{notes}"
    )


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
    kind: Kind
    gold: list[str]
    supported: Support
    reason: str


class JudgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bullets: list[_BulletOut]  # first: the gold coverage is judged after the bullets
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
        "bullets": {
            "type": "array",
            "items": _obj(
                {
                    "id": _STR,
                    "kind": {"type": "string", "enum": ["decision", "action", "problem", "other"]},
                    "gold": _IDS,
                    "supported": {"type": "string", "enum": ["yes", "partly", "no", "n/a"]},
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


def judge_request(unit: Unit) -> LLMRequest:
    return LLMRequest(
        stage=STAGE,
        prompt_id=PROMPT,
        system=load_prompt(PROMPT),
        user=judge_user(unit),
        schema=SCHEMA,
        max_tokens=MAX_TOKENS,
        effort="medium",
    )


# -- parsing the judge ------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldVerdict:
    id: str
    category: str
    coverage: str  # full / partial / none
    bullets: tuple[str, ...]
    heard: str  # yes / no / not_assessed (product transcript has it)
    quote: str
    quote_found: bool  # the quote occurs in the product transcript (normalized match)
    reason: str


@dataclass(frozen=True)
class BulletVerdict:
    id: str
    section: str
    kind: str  # decision / action / problem / other
    gold: tuple[str, ...]
    supported: str  # yes / partly / no / n/a
    reason: str

    @property
    def right(self) -> bool:
        """States a gold item or is fully supported by the reference transcript."""
        return bool(self.gold) or self.supported == "yes"


@dataclass(frozen=True)
class Verdicts:
    gold: tuple[GoldVerdict, ...]
    bullets: tuple[BulletVerdict, ...]
    repairs: tuple[str, ...]  # what the parser had to fix (should be empty)


def parse(out: JudgeOut, unit: Unit) -> Verdicts:
    """Validate the judge's output against the unit. Unknown IDs are dropped, duplicates keep
    the first entry; a missing gold item becomes "none", a missing bullet "other". The product
    transcript verdict is forced to not_assessed for fully covered items and when the
    transcript was not in the prompt; a bullet of kind other has support "n/a". Every fix is
    listed in ``repairs``, as is a covered gold item that cites no bullet (kept as judged)."""
    repairs: list[str] = []
    gids = {g.id: g for g in unit.gold}
    bids = {b.id: b for b in unit.bullets}
    included = unit.includes_transcript

    got_g: dict[str, _GoldOut] = {}
    for go in out.gold:
        if go.id not in gids:
            repairs.append(f"unknown gold id {go.id}")
        elif go.id in got_g:
            repairs.append(f"gold {go.id} twice")
        else:
            got_g[go.id] = go
    gv: list[GoldVerdict] = []
    for item in unit.gold:
        g = got_g.get(item.id)
        if g is None:
            repairs.append(f"gold {item.id} missing")
            gv.append(
                GoldVerdict(item.id, item.category, "none", (), "not_assessed", "", False, "")
            )
            continue
        bl = tuple(b for b in dict.fromkeys(g.bullets) if b in bids)
        if len(bl) != len(g.bullets):
            repairs.append(f"gold {item.id}: unknown or repeated bullet ids")
        if g.coverage != "none" and not bl:
            repairs.append(f"gold {item.id}: {g.coverage} without bullets")
        heard: str = g.in_product_transcript
        if g.coverage == "full" or not included:
            heard = "not_assessed"
        elif heard == "not_assessed":
            repairs.append(f"gold {item.id}: transcript not assessed")
        quote = g.quote.strip() if heard == "yes" else ""
        found = bool(quote) and quote_found(quote, unit.transcript)
        gv.append(
            GoldVerdict(item.id, item.category, g.coverage, bl, heard, quote, found, g.reason)
        )

    got_b: dict[str, _BulletOut] = {}
    for bo in out.bullets:
        if bo.id not in bids:
            repairs.append(f"unknown bullet id {bo.id}")
        elif bo.id in got_b:
            repairs.append(f"bullet {bo.id} twice")
        else:
            got_b[bo.id] = bo
    bv: list[BulletVerdict] = []
    for bullet in unit.bullets:
        b = got_b.get(bullet.id)
        if b is None:
            repairs.append(f"bullet {bullet.id} missing")
            bv.append(BulletVerdict(bullet.id, bullet.section, "other", (), "n/a", ""))
            continue
        gl = tuple(x for x in dict.fromkeys(b.gold) if x in gids)
        if len(gl) != len(b.gold):
            repairs.append(f"bullet {bullet.id}: unknown or repeated gold ids")
        sup: str = b.supported
        if b.kind == "other":
            sup = "n/a"
        elif sup == "n/a":
            repairs.append(f"bullet {bullet.id}: {b.kind} without a support verdict")
        bv.append(BulletVerdict(bullet.id, bullet.section, b.kind, gl, sup, b.reason))
    return Verdicts(tuple(gv), tuple(bv), tuple(repairs))


def link_disagreements(v: Verdicts) -> int:
    """(gold, bullet) links stated on one side of the judge's output but not the other."""
    from_gold = {(g.id, b) for g in v.gold for b in g.bullets}
    from_bullets = {(g, b.id) for b in v.bullets for g in b.gold}
    return len(from_gold ^ from_bullets)


# -- scoring ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CatScore:
    """One unit, one category (or :data:`ALL`). Recall is None with no gold items, precision
    None with no bullets of that kind."""

    n_gold: int
    full: int
    partial: int
    n_bullets: int
    right: int  # bullets that state a gold item or are fully supported
    gold_linked: int  # bullets that state a gold item
    supported_yes: int
    supported_partly: int
    supported_no: int
    said_not_noted: int  # gold "none", product transcript has it (judge)
    said_not_noted_quoted: int  # ... and its quote was found in the product transcript
    said_partly_noted: int  # gold "partial", product transcript has it (judge)
    unassessed: int  # gold not fully covered, product transcript not assessed

    @property
    def recall(self) -> float | None:
        return (self.full + 0.5 * self.partial) / self.n_gold if self.n_gold else None

    @property
    def recall_full(self) -> float | None:
        return self.full / self.n_gold if self.n_gold else None

    @property
    def precision(self) -> float | None:
        return self.right / self.n_bullets if self.n_bullets else None

    @property
    def precision_true(self) -> float | None:
        """Strict: only bullets the reference transcript fully supports (a gold-linked bullet
        judged "partly" counts as wrong here)."""
        return self.supported_yes / self.n_bullets if self.n_bullets else None

    @property
    def precision_gold(self) -> float | None:
        return self.gold_linked / self.n_bullets if self.n_bullets else None

    @property
    def f1(self) -> float | None:
        """Harmonic mean of precision and (weighted) recall; None without gold, 0 with gold but
        no bullets of that kind."""
        r, p = self.recall, self.precision
        if r is None:
            return None
        return 0.0 if not p or not r else 2 * p * r / (p + r)

    def row(self) -> dict[str, Any]:
        return {**asdict(self), **{m: getattr(self, m) for m in METRICS}}


def score_category(v: Verdicts, category: str) -> CatScore:
    """Score one category (``all``: the three pooled) of a unit's verdicts."""
    cats = set(CATEGORIES) if category == ALL else {category}
    gs = [g for g in v.gold if g.category in cats]
    bs = [b for b in v.bullets if b.kind in cats]
    missing = [g for g in gs if g.coverage == "none"]
    return CatScore(
        n_gold=len(gs),
        full=sum(g.coverage == "full" for g in gs),
        partial=sum(g.coverage == "partial" for g in gs),
        n_bullets=len(bs),
        right=sum(b.right for b in bs),
        gold_linked=sum(bool(b.gold) for b in bs),
        supported_yes=sum(b.supported == "yes" for b in bs),
        supported_partly=sum(b.supported == "partly" for b in bs),
        supported_no=sum(b.supported == "no" for b in bs),
        said_not_noted=sum(g.heard == "yes" for g in missing),
        said_not_noted_quoted=sum(g.heard == "yes" and g.quote_found for g in missing),
        said_partly_noted=sum(g.heard == "yes" for g in gs if g.coverage == "partial"),
        unassessed=sum(g.heard == "not_assessed" for g in gs if g.coverage != "full"),
    )


def score_unit(v: Verdicts) -> dict[str, dict[str, Any]]:
    return {c: score_category(v, c).row() for c in (*CATEGORIES, ALL)}


def run_unit(unit: Unit, judge: LLMClient) -> dict[str, Any]:
    """Judge and score one unit; a JSON-ready record (holds notes and gold text: local only)."""
    out = judge.structured(judge_request(unit), JudgeOut)
    v = parse(out, unit)
    return {
        "meeting_id": unit.meeting_id,
        "product": unit.product,
        "split": unit.split,
        "product_transcript_included": unit.includes_transcript,
        "bullets": [asdict(b) for b in unit.bullets],
        "gold": [asdict(g) for g in unit.gold],
        "gold_verdicts": [asdict(g) for g in v.gold],
        "bullet_verdicts": [asdict(b) for b in v.bullets],
        "repairs": list(v.repairs),
        "link_disagreements": link_disagreements(v),
        "scores": score_unit(v),
    }


# -- summary ----------------------------------------------------------------------------------

_COUNTS = (
    "n_gold", "full", "partial", "n_bullets", "right", "gold_linked", "supported_yes",
    "supported_partly", "supported_no", "said_not_noted", "said_not_noted_quoted",
    "said_partly_noted", "unassessed",
)  # fmt: skip


def _cat_summary(rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    tot = {k: sum(int(r[k]) for r in rows) for k in _COUNTS}
    out: dict[str, Any] = {"n_meetings": len(rows), "micro": tot}
    out["micro_recall"] = (
        (tot["full"] + 0.5 * tot["partial"]) / tot["n_gold"] if tot["n_gold"] else None
    )
    out["micro_recall_full"] = tot["full"] / tot["n_gold"] if tot["n_gold"] else None
    out["micro_precision"] = tot["right"] / tot["n_bullets"] if tot["n_bullets"] else None
    for m in METRICS:
        vals = [float(r[m]) for r in rows if r[m] is not None]
        out[f"n_{m}"] = len(vals)
        out[f"mean_{m}"] = statistics.fmean(vals) if vals else None
        out[f"ci_{m}"] = bootstrap_ci(vals, seed)
    return out


def summarize(records: Sequence[Mapping[str, Any]], seed: int = 42) -> dict[str, Any]:
    """Per split (dev, test, all), per product and category: means over meetings with 95%
    bootstrap CIs, micro totals; Granola vs Wispr sign tests per category over meetings both
    recorded (wins = Wispr higher); parser repairs and link disagreements."""
    out: dict[str, Any] = {}
    for split in ("dev", "test", "all"):
        rs = [r for r in records if split == "all" or r["split"] == split]
        if not rs:
            continue
        per: dict[str, Any] = {}
        for p in PRODUCTS:
            prs = [r for r in rs if r["product"] == p]
            if prs:
                per[p] = {
                    c: _cat_summary([r["scores"][c] for r in prs], seed) for c in (*CATEGORIES, ALL)
                }
        paired: dict[str, Any] = {}
        if all(p in per for p in PRODUCTS):
            a, b = PRODUCTS
            for c in (*CATEGORIES, ALL):
                paired[f"{b} vs {a}: {c}"] = {
                    m: sign_test(
                        {r["meeting_id"]: r["scores"][c][m] for r in rs if r["product"] == a},
                        {r["meeting_id"]: r["scores"][c][m] for r in rs if r["product"] == b},
                    )
                    for m in TEST_METRICS
                }
        out[split] = {
            "products": per,
            "paired": paired,
            "repairs": sum(len(r["repairs"]) for r in rs),
            "link_disagreements": sum(int(r["link_disagreements"]) for r in rs),
            "units_without_product_transcript": sum(
                not r["product_transcript_included"] for r in rs
            ),
        }
    return out


def aggregate(result: Mapping[str, Any]) -> dict[str, Any]:
    """Numbers only: the run's meta (no ledger) and summary."""
    meta = dict(result["meta"])
    meta.pop("ledger_by_stage", None)
    return {"meta": meta, "summary": result["summary"]}


def private_texts(records: Sequence[Mapping[str, Any]]) -> list[str]:
    """Strings an aggregate must not contain: bullets, gold, quotes, reasons."""
    out: list[str] = []
    for r in records:
        out += [b["text"] for b in r["bullets"]] + [g["text"] for g in r["gold"]]
        out += [g["quote"] for g in r["gold_verdicts"]] + [g["reason"] for g in r["gold_verdicts"]]
        out += [b["reason"] for b in r["bullet_verdicts"]]
    return out


# -- quote and reprice ------------------------------------------------------------------------


@dataclass
class Quote:
    calls: int = 0
    cached: int = 0
    input_tokens: int = 0  # uncached calls, exact (count_tokens) + OVERHEAD each
    items: int = 0  # gold items + bullets over uncached calls
    usd_expected: float = 0.0
    usd_high: float = 0.0


def items_of(unit: Unit) -> int:
    return len(unit.gold) + len(unit.bullets)


def quote(units: Sequence[Unit], judge: LLMClient, counter: Any) -> Quote:
    """Price judging ``units``: exact input (``counter``: count_tokens, free) plus
    :data:`OVERHEAD`; output ASSUMED (:data:`ASSUMED_OUT_BASE` + per item). Cached: $0."""
    q = Quote()
    for u in units:
        req = judge_request(u)
        q.calls += 1
        if judge.is_cached(req):
            q.cached += 1
            continue
        tin = counter([f"{req.system}\n\n{req.user}"])[0] + OVERHEAD
        n = items_of(u)
        q.input_tokens += tin
        q.items += n
        for i, attr in enumerate(("usd_expected", "usd_high")):
            out = ASSUMED_OUT_BASE[i] + ASSUMED_OUT_PER_ITEM[i] * n
            setattr(q, attr, getattr(q, attr) + cost_usd(judge.model, tin, round(out)))
    return q


def reprice(
    ledger_records: Sequence[Mapping[str, Any]],
    pilot_input_tokens: int,
    pilot_items: int,
    full_input_tokens: int,
    full_items: int,
    margin: float = MARGIN,
) -> dict[str, Any]:
    """The full run priced from the pilot's ledger: the pilot's recorded input cost scaled by
    exact input tokens (full / pilot, both counted), its output cost by gold items + bullets
    (output grows with the items judged), times ``margin``. Cached pilot calls count at their
    original cost."""
    rows = [r for r in ledger_records if r["stage"] == STAGE]
    usd_in = sum(cost_usd(str(r["model"]), int(r["input_tokens"]), 0) for r in rows)
    usd_out = sum(cost_usd(str(r["model"]), 0, int(r["output_tokens"])) for r in rows)
    k_in = full_input_tokens / pilot_input_tokens if pilot_input_tokens else 0.0
    k_out = full_items / pilot_items if pilot_items else 0.0
    usd = usd_in * k_in + usd_out * k_out
    return {
        "pilot_calls": len(rows),
        "pilot_usd_recorded": sum(float(r["cost_usd"]) for r in rows),
        "pilot_input_tokens": sum(int(r["input_tokens"]) for r in rows),
        "pilot_output_tokens": sum(int(r["output_tokens"]) for r in rows),
        "scale_input": k_in,
        "scale_output": k_out,
        "full_usd": usd,
        "margin": margin,
        "full_usd_with_margin": usd * margin,
    }
