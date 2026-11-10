"""Bullet cues: a small cue on a notes bullet that expands to a decision / action / problem said
in the stretch of the meeting the bullet summarises but missing from the notes, with a quote.

Why: the meeting-map test (``data/private/FEATURE_TESTS.md``) found 76% of missing and 89% of
partial gold in transcript windows the notes *did* cover, so omission is local to a bullet's
span, not regional. This tests a cue at the bullet.

* Inputs are the E3 reader packets (``data/private/reader/packets/<m>__<p>.md``): section 4
  (the notes as bullets ``B1``..) and section 5 (the product's own transcript, ``T`` ids). The
  packet's gold (sections 2-3) never reaches the model; only the gold ids go to the reader key.
* Alignment (bullet -> T line ranges): TEST uses the hand maps of the meeting-map test
  (``data/private/feature_tests/map/<m>.jsonl``, ``bullet_map``). DEV (IS1008a) has none and
  uses :func:`overlap_align`, a word-overlap window (documented, deterministic; its agreement
  with the hand maps on TEST is reported by :func:`alignment_agreement`).
* One call per bullet with a non-empty span (:func:`cue_request`, prompt ``bullet_cue.v1``):
  system + all notes bullets (the cached prefix, a breakpoint after the notes), then the
  bullet (with its parents) and its span widened :data:`PAD` lines (the varying part). A
  unit's calls run back to back so the prefix is read from the prompt cache.
* Each cue's quote is checked (:func:`check_cue`): every quoted line must be in the span shown
  and its text a substring of that line (after ``list_check.normalize``). Failures are dropped
  and counted by reason.
* Scoring at $0: proxies (:func:`summarize`): cues per unit, quote-verified rate, distinct
  cues by shared quote lines, an exact-text match to an E4 reader item with consensus labels,
  and a location proxy (quote lines near a gold item the notes missed, from the map files).
  The real score comes from Claude Code reader agents on condition-blind packets
  (:func:`build_packets`, the E4 packet layout, read with ``prompts/e4_reader.v2.md``).

Pure functions apart from :func:`run_jobs` (calls the injected ``LLMClient``) and :func:`main`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tenor.evals import e4_reader as R
from tenor.evals.list_check import Line, normalize
from tenor.evals.notes_completeness import Bullet
from tenor.llm.client import LLMClient, LLMRequest, breaks_after, cost_usd, load_prompt

PROMPT = "bullet_cue.v2"  # v1: no `found` step; 0 cues on DEV, even on known omissions
MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
STAGE = {"haiku": "cues.haiku", "sonnet": "cues.sonnet"}
PAD = 5  # span widened by this many transcript lines each side
MAX_CUES = 3  # kept per bullet (the prompt asks for at most 3)
MAX_TOKENS = 4000
EFFORT = "low"
PILOT_MEETING = "IS1008a"  # DEV
PILOT_MAX_USD = 0.30
PILOT_SONNET_BULLETS = 8  # Sonnet in the pilot: the first N bullets of each DEV unit
MARGIN = 1.5
# Output tokens per call (expected, high), ASSUMED until the pilot's ledger.
ASSUMED_OUT = {"haiku": (200.0, 1000.0), "sonnet": (200.0, 1000.0)}
GOLD_NEAR = 3  # location proxy: a quote line within this many lines of gold evidence
PACKET_VERSION = "cue_reader_packet.v1"
KEY_VERSION = "cue_reader_key.v1"
READER_PROMPT = "e4_reader.v2"
PRODUCTS = ("granola", "wispr")
PRODUCT_LABEL = {"granola": "Granola", "wispr": "Wispr Flow"}

Range = tuple[int, int]
Status = Literal["ok", "no_quote", "bad_line", "outside_span", "text_not_found", "over_cap"]


# -- units from the reader packets ------------------------------------------------------------


@dataclass(frozen=True)
class CueUnit:
    """One product's notes and own transcript for one AMI meeting (from its reader packet)."""

    meeting_id: str
    product: str
    split: str
    bullets: tuple[Bullet, ...]
    transcript: tuple[Line, ...]
    gold_ids: tuple[str, ...]
    packet: str  # the packet text (sections 1-5), for the reader packets

    @property
    def key(self) -> str:
        return f"{self.meeting_id}:{self.product}"


_SECTION = re.compile(r"^## (\d)\. ", re.M)
_BULLET_LINE = re.compile(r"^(B\d+) \[section: (.*?)\](?: \(sub-point of (B\d+)\))? ?(.*)$")
_T_LINE = re.compile(r"^\[T(\d+) (.+) (\d+(?::\d{2})+)\] ?(.*)$")
_GOLD_LINE = re.compile(r"^- ([DAP]\d+) \(")


def _sections(text: str) -> dict[str, str]:
    marks = list(_SECTION.finditer(text))
    return {
        m.group(1): text[m.end() : (marks[i + 1].start() if i + 1 < len(marks) else len(text))]
        for i, m in enumerate(marks)
    }


def parse_packet(text: str, meeting_id: str, product: str, split: str) -> CueUnit:
    """A unit from an E3 reader packet: bullets (section 4), transcript lines (section 5) and
    gold ids (section 2; the gold text is not kept)."""
    sec = _sections(text)
    bullets = []
    for ln in sec["4"].splitlines():
        if m := _BULLET_LINE.match(ln):
            bullets.append(Bullet(m.group(1), m.group(2), m.group(4), parent=m.group(3)))
    lines = []
    for ln in sec["5"].splitlines():
        if m := _T_LINE.match(ln):
            lines.append(Line(int(m.group(1)), m.group(2), m.group(3), m.group(4)))
    gold = tuple(m.group(1) for ln in sec["2"].splitlines() if (m := _GOLD_LINE.match(ln)))
    return CueUnit(meeting_id, product, split, tuple(bullets), tuple(lines), gold, text)


def load_units(
    packets_dir: Path, meetings: Sequence[str], dev: Iterable[str] = (PILOT_MEETING,)
) -> list[CueUnit]:
    """Every (meeting, product) packet that exists, meetings in the given order."""
    dev_set = set(dev)
    out = []
    for m in meetings:
        for p in PRODUCTS:
            path = packets_dir / f"{m}__{p}.md"
            if path.exists():
                split = "dev" if m in dev_set else "test"
                out.append(parse_packet(path.read_text(encoding="utf-8"), m, p, split))
    return out


# -- alignment --------------------------------------------------------------------------------

_RANGE = re.compile(r"^T(\d+)(?:-T(\d+))?$")


def parse_ranges(specs: Iterable[str]) -> list[Range]:
    """``["T0010-T0013", "T0045"]`` -> ``[(10, 13), (45, 45)]``."""
    out = []
    for s in specs:
        m = _RANGE.match(s.strip())
        if m is None:
            raise ValueError(f"bad line range {s!r}")
        a = int(m.group(1))
        b = int(m.group(2) or a)
        out.append((min(a, b), max(a, b)))
    return out


def load_maps(map_dir: Path) -> dict[str, dict[str, Any]]:
    """Unit key -> the meeting-map record (``bullet_map`` as ranges, ``gold`` as given)."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(map_dir.glob("*.jsonl")):
        for ln in path.read_text().splitlines():
            if not ln.strip():
                continue
            r = json.loads(ln)
            out[f"{r['meeting']}:{r['product']}"] = {
                "bullet_map": {b: parse_ranges(v) for b, v in r["bullet_map"].items()},
                "gold": r.get("gold", []),
            }
    return out


_STOP_WORDS = (
    "about above after again also because been before being between both could does doing "
    "down during each every from further have having here into just like more most much must "
    "need only other over same should some such than that their them then there these they "
    "this those through under until very want were what when where which while will with "
    "would your yeah okay think know going really maybe something things thing gonna well "
    "right said says next meeting"
)
_STOP = frozenset(_STOP_WORDS.split())
ALIGN_WINDOW = 5  # lines
ALIGN_MIN_HITS = 2  # distinct bullet words in the window (or all of them, if fewer)


def content_words(text: str) -> set[str]:
    """Lower-cased words of 4+ letters (or any number) that are not stop words."""
    return {w for w in normalize(text).split() if (len(w) >= 4 or w.isdigit()) and w not in _STOP}


def overlap_align(unit: CueUnit) -> dict[str, list[Range]]:
    """Word-overlap alignment (DEV, where no hand map exists): for each bullet, the
    :data:`ALIGN_WINDOW`-line window of the transcript that holds the most distinct content
    words of the bullet's own text (earliest on ties), trimmed to its first and last line with
    a hit. Empty when the best window has fewer than :data:`ALIGN_MIN_HITS` (or than all of the
    bullet's words, if it has fewer) or the bullet has no content words."""
    tr = unit.transcript
    lw = [content_words(x.text) for x in tr]
    out: dict[str, list[Range]] = {}
    for b in unit.bullets:
        words = content_words(b.text)
        need = min(ALIGN_MIN_HITS, len(words))
        best, best_i = 0, -1
        for i in range(max(0, len(tr) - ALIGN_WINDOW + 1)):
            hit: set[str] = set()
            for x in lw[i : i + ALIGN_WINDOW]:
                hit |= words & x
            if len(hit) > best:
                best, best_i = len(hit), i
        if not words or best_i < 0 or best < need:
            out[b.id] = []
            continue
        idx = [j for j in range(best_i, best_i + ALIGN_WINDOW) if words & lw[j]]
        out[b.id] = [(tr[idx[0]].index, tr[idx[-1]].index)]
    return out


def _near(a: Sequence[Range], b: Sequence[Range], pad: int) -> bool:
    return any(x0 - pad <= y1 and y0 <= x1 + pad for x0, x1 in a for y0, y1 in b)


def alignment_agreement(
    auto: Mapping[str, Sequence[Range]], hand: Mapping[str, Sequence[Range]], pad: int = PAD
) -> dict[str, int]:
    """How :func:`overlap_align` compares with a hand map, over bullets the hand map aligns:
    ``agree`` (an auto range within ``pad`` lines of a hand range), ``disagree``, ``auto_empty``;
    plus ``extra`` (auto aligns a bullet the hand map left empty)."""
    c = Counter[str]()
    for b, h in hand.items():
        a = auto.get(b, [])
        if not h:
            c["extra"] += int(bool(a))
        elif not a:
            c["auto_empty"] += 1
        else:
            c["agree" if _near(a, h, pad) else "disagree"] += 1
    return {k: c[k] for k in ("agree", "disagree", "auto_empty", "extra")}


def widen(ranges: Sequence[Range], lo: int, hi: int, pad: int = PAD) -> tuple[Range, ...]:
    """``ranges`` widened by ``pad`` each side, clipped to ``[lo, hi]``, sorted, overlapping or
    touching ranges merged."""
    rs = sorted((max(lo, a - pad), min(hi, b + pad)) for a, b in ranges)
    out: list[Range] = []
    for a, b in rs:
        if a > b:
            continue
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return tuple(out)


# -- the call ---------------------------------------------------------------------------------


class QuoteLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line: str
    text: str


class Cue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["decision", "action", "problem"]
    text: str
    owner: str
    quote: list[QuoteLine]


class Found(BaseModel):
    """The model's inventory of the span before it writes cues (a visible reasoning step)."""

    model_config = ConfigDict(extra="forbid")
    item: str
    lines: list[str]
    notes: list[str]
    verdict: Literal["stated", "part_missing", "missing", "unclear"]


class CueOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    found: list[Found]
    cues: list[Cue]


_FOUND_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "item": {"type": "string"},
            "lines": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "array", "items": {"type": "string"}},
            "verdict": {
                "type": "string",
                "enum": ["stated", "part_missing", "missing", "unclear"],
            },
        },
        "required": ["item", "lines", "notes", "verdict"],
        "additionalProperties": False,
    },
}

CUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "found": _FOUND_SCHEMA,
        "cues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["decision", "action", "problem"]},
                    "text": {"type": "string"},
                    "owner": {"type": "string"},
                    "quote": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "line": {"type": "string"},
                                "text": {"type": "string"},
                            },
                            "required": ["line", "text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["kind", "text", "owner", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["found", "cues"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Job:
    """One bullet of one unit with its widened span."""

    unit: CueUnit
    bullet_id: str
    spans: tuple[Range, ...]

    @property
    def n_lines(self) -> int:
        have = {x.index for x in self.unit.transcript}
        return sum(1 for a, b in self.spans for i in range(a, b + 1) if i in have)


def jobs_for(unit: CueUnit, align: Mapping[str, Sequence[Range]], pad: int = PAD) -> list[Job]:
    """The unit's bullets with a non-empty aligned span, in notes order."""
    if not unit.transcript:
        return []
    lo, hi = unit.transcript[0].index, unit.transcript[-1].index
    out = []
    for b in unit.bullets:
        rs = align.get(b.id, [])
        if rs:
            out.append(Job(unit, b.id, widen(rs, lo, hi, pad)))
    return out


def render_line(x: Line) -> str:
    return f"[T{x.index:04d} {x.speaker} {x.start}] {x.text}"


def notes_block(unit: CueUnit) -> str:
    """The cached prefix after the system prompt: every notes bullet."""
    rows = "\n".join(b.render() for b in unit.bullets) or "(the notes are empty)"
    return f"<notes>\n{rows}\n</notes>\n\n"


def bullet_block(job: Job) -> str:
    """The varying part: the bullet with its parents (top-down), then its span."""
    by = {b.id: b for b in job.unit.bullets}
    chain = [by[job.bullet_id]]
    while chain[0].parent and chain[0].parent in by and len(chain) < 10:
        chain.insert(0, by[chain[0].parent])
    lines = {x.index: x for x in job.unit.transcript}
    body: list[str] = []
    for n, (a, b) in enumerate(job.spans):
        if n:
            body.append("...")
        body += [render_line(lines[i]) for i in range(a, b + 1) if i in lines]
    return (
        "<bullet>\n"
        + "\n".join(x.render() for x in chain)
        + "\n</bullet>\n\n<span>\n"
        + "\n".join(body)
        + "\n</span>\n"
    )


def cue_request(job: Job, model_key: str) -> LLMRequest:
    """System (breakpoint), notes (breakpoint), then the bullet and its span."""
    user, breaks = breaks_after(notes_block(job.unit), bullet_block(job))
    return LLMRequest(
        stage=STAGE[model_key],
        prompt_id=PROMPT,
        system=load_prompt(PROMPT),
        user=user,
        schema=CUE_SCHEMA,
        max_tokens=MAX_TOKENS,
        effort=EFFORT,
        cache_breaks=breaks,
    )


_T_ID = re.compile(r"^T(\d+)$")


def check_cue(cue: Cue, job: Job) -> Status:
    """``ok`` if every quoted line is a ``T`` id inside the span shown and its text occurs in
    that line (normalized); else the first failure."""
    if not cue.quote:
        return "no_quote"
    lines = {x.index: x for x in job.unit.transcript}
    for q in cue.quote:
        m = _T_ID.match(q.line.strip())
        if m is None or int(m.group(1)) not in lines:
            return "bad_line"
        i = int(m.group(1))
        if not any(a <= i <= b for a, b in job.spans):
            return "outside_span"
        t = normalize(q.text)
        if not t or t not in normalize(lines[i].text):
            return "text_not_found"
    return "ok"


def cue_rows(job: Job, out: CueOut, cap: int = MAX_CUES) -> list[dict[str, Any]]:
    """The model's cues in order with a ``status`` (:func:`check_cue`; ``over_cap`` once
    ``cap`` cues are ok) and the quoted line numbers."""
    rows = []
    n_ok = 0
    for c in out.cues:
        st: Status = check_cue(c, job)
        if st == "ok":
            if n_ok >= cap:
                st = "over_cap"
            else:
                n_ok += 1
        lines = sorted(
            {int(m.group(1)) for q in c.quote if (m := _T_ID.match(q.line.strip())) is not None}
        )
        rows.append({**c.model_dump(), "status": st, "quote_lines": lines})
    return rows


def run_jobs(
    jobs: Sequence[Job], client: LLMClient, model_key: str, echo: Callable[[str], None]
) -> list[dict[str, Any]]:
    """Run the jobs in order (a unit's bullets back to back). One row per bullet."""
    out = []
    last = None
    for job in jobs:
        res = client.structured(cue_request(job, model_key), CueOut)
        rows = cue_rows(job, res)
        out.append(
            {
                "unit": job.unit.key,
                "meeting_id": job.unit.meeting_id,
                "product": job.unit.product,
                "split": job.unit.split,
                "model": model_key,
                "bullet_id": job.bullet_id,
                "spans": [list(s) for s in job.spans],
                "n_span_lines": job.n_lines,
                "found": [f.model_dump() for f in res.found],
                "cues": rows,
            }
        )
        if job.unit.key != last:
            last = job.unit.key
            echo(f"{model_key} {job.unit.key}: started (spent ${client.ledger.spent_usd():.4f})")
    return out


# -- cost -------------------------------------------------------------------------------------


def quote_cost(
    jobs: Sequence[Job], counter: Callable[[list[str]], list[int]], model_key: str
) -> dict[str, float]:
    """Price ``jobs`` for one model. Token counts exact via ``counter`` (prefix = system + notes
    once per unit, varying part per bullet); output ASSUMED (:data:`ASSUMED_OUT`).
    ``usd_high``: every input token uncached at full price, high output. ``usd_expected``: a
    unit's first call writes the prefix, later calls read it; expected output."""
    model = MODELS[model_key]
    o_exp, o_high = ASSUMED_OUT[model_key]
    by_unit: dict[str, list[Job]] = {}
    for j in jobs:
        by_unit.setdefault(j.unit.key, []).append(j)
    exp = high = 0.0
    for js in by_unit.values():
        system = load_prompt(PROMPT)
        pre = counter([system + notes_block(js[0].unit)])[0]
        var = counter([bullet_block(j) for j in js])
        for i, v in enumerate(var):
            high += cost_usd(model, pre + v, int(o_high))
            if i == 0:
                exp += cost_usd(model, v, int(o_exp), cache_write_tokens=pre)
            else:
                exp += cost_usd(model, v, int(o_exp), cache_read_tokens=pre)
    return {"calls": float(len(jobs)), "usd_expected": exp, "usd_high": high}


def reprice(ledger: Sequence[Mapping[str, Any]], full_calls: Mapping[str, int]) -> dict[str, Any]:
    """The full run from the pilot's ledger: mean live cost per call by model times the full
    run's call counts, and x :data:`MARGIN`."""
    by: dict[str, list[float]] = {k: [] for k in MODELS}
    for r in ledger:
        for k, st in STAGE.items():
            if r["stage"] == st and not r["cached"]:
                by[k].append(float(r["cost_usd"]))
    mean = {k: (sum(v) / len(v) if v else None) for k, v in by.items()}
    usd = {k: (mean[k] or 0.0) * full_calls.get(k, 0) for k in MODELS}
    return {
        "mean_usd_per_call": mean,
        "full_calls": dict(full_calls),
        "full_usd": usd,
        "full_usd_with_margin": {k: v * MARGIN for k, v in usd.items()},
    }


# -- proxies at $0 ----------------------------------------------------------------------------


def cue_text(row: Mapping[str, Any]) -> str:
    """The cue as shown to a reader: its text, with the owner appended when the text does not
    already name them."""
    text = " ".join(str(row["text"]).split())
    owner = " ".join(str(row.get("owner") or "").split())
    if owner and normalize(owner) not in normalize(text):
        text = f"{text} (owner: {owner})"
    return text


def distinct_by_quote(rows: Sequence[Mapping[str, Any]]) -> int:
    """Kept cues of one unit and model, counted once per group of cues of the same kind whose
    quoted lines overlap (the same item seen from neighbouring bullets)."""
    groups: list[tuple[str, set[int]]] = []
    for r in rows:
        ls = set(r["quote_lines"])
        hits = [g for g in groups if g[0] == r["kind"] and g[1] & ls]
        merged = set(ls)
        for g in hits:
            merged |= g[1]
            groups.remove(g)
        groups.append((str(r["kind"]), merged))
    return len(groups)


def labelled_items(
    key_units: Mapping[str, Mapping[str, Any]], consensus: Iterable[Mapping[str, Any]]
) -> dict[str, dict[str, R.ReaderLine]]:
    """Unit key -> normalized E4 item text -> its consensus reader line (TEST)."""
    verdicts: dict[str, R.Verdicts] = {}
    for r in consensus:
        v = R.ReaderLine.model_validate(r)
        verdicts.setdefault(f"{v.meeting}:{v.product}", {})[v.cand_id] = v
    out: dict[str, dict[str, R.ReaderLine]] = {}
    for uk, u in key_units.items():
        vs = verdicts.get(uk, {})
        out[uk] = {normalize(it["text"]): vs[k] for k, it in u["items"].items() if k in vs}
    return out


def missing_gold_lines(map_rec: Mapping[str, Any]) -> dict[str, tuple[str, list[Range]]]:
    """Gold id -> (notes verdict, evidence ranges) for gold the notes miss (no / partial)."""
    return {
        g["gold_id"]: (g["notes_verdict"], parse_ranges(g["lines"]))
        for g in map_rec.get("gold", [])
        if g["notes_verdict"] in ("no", "partial") and g["lines"]
    }


def unit_proxy(
    rows: Sequence[Mapping[str, Any]],
    labelled: Mapping[str, R.ReaderLine],
    gold: Mapping[str, tuple[str, list[Range]]],
) -> dict[str, Any]:
    """One unit and model: counts over its bullet rows."""
    cues = [c for r in rows for c in r["cues"]]
    ok = [c for c in cues if c["status"] == "ok"]
    st = Counter(c["status"] for c in cues)
    lab = [labelled[normalize(cue_text(c))] for c in ok if normalize(cue_text(c)) in labelled]
    near: set[str] = set()
    n_near = 0
    for c in ok:
        q = [(i, i) for i in c["quote_lines"]]
        hit = {g for g, (_, rs) in gold.items() if _near(q, rs, GOLD_NEAR)}
        near |= hit
        n_near += int(bool(hit))
    return {
        "bullets": len(rows),
        "bullets_with_cue": sum(any(c["status"] == "ok" for c in r["cues"]) for r in rows),
        "returned": len(cues),
        "ok": len(ok),
        "failed": {k: st[k] for k in ("no_quote", "bad_line", "outside_span", "text_not_found")},
        "over_cap": st["over_cap"],
        "distinct_by_quote": distinct_by_quote(ok),
        "by_kind": dict(sorted(Counter(c["kind"] for c in ok).items())),
        "found": dict(
            sorted(Counter(f["verdict"] for r in rows for f in r.get("found", [])).items())
        ),
        "exact_e4": len(lab),
        "exact_e4_true_new": sum(map(R.is_true_new, lab)),
        "exact_e4_gold": sorted({g.id for v in lab for g in v.gold_match if g.id in gold}),
        "near_missing_gold_cues": n_near,
        "near_missing_gold": sorted(near),
        "n_missing_gold": len(gold),
        "n_missing_gold_no": sum(1 for v, _ in gold.values() if v == "no"),
        "near_missing_gold_no": sorted(g for g in near if gold[g][0] == "no"),
    }


SUM_KEYS = ("bullets", "bullets_with_cue", "returned", "ok", "over_cap", "distinct_by_quote",
            "exact_e4", "exact_e4_true_new", "near_missing_gold_cues", "n_missing_gold",
            "n_missing_gold_no")  # fmt: skip


def summarize(per_unit: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per model, per product and both: totals, verified rate, cues per unit and per bullet,
    failures by reason, missing gold near a cue (location proxy)."""
    out: dict[str, Any] = {}
    for m in sorted({r["model"] for r in per_unit}):
        per: dict[str, Any] = {}
        for g in (*PRODUCTS, "both"):
            rows = [r for r in per_unit if r["model"] == m and g in ("both", r["product"])]
            if not rows:
                continue
            tot = {k: sum(int(r[k]) for r in rows) for k in SUM_KEYS}
            fails = Counter[str]()
            kinds = Counter[str]()
            found = Counter[str]()
            for r in rows:
                fails.update(r["failed"])
                kinds.update(r["by_kind"])
                found.update(r["found"])
            near = sum(len(r["near_missing_gold"]) for r in rows)
            near_no = sum(len(r["near_missing_gold_no"]) for r in rows)
            checked = tot["returned"] - tot["over_cap"]
            per[g] = {
                "units": len(rows),
                **tot,
                "failed": dict(sorted(fails.items())),
                "by_kind": dict(sorted(kinds.items())),
                "found": dict(sorted(found.items())),
                "quote_verified_rate": (tot["ok"] / checked) if checked else None,
                "cues_per_unit": tot["ok"] / len(rows),
                "cues_per_bullet": (tot["ok"] / tot["bullets"]) if tot["bullets"] else None,
                "missing_gold_near_a_cue": near,
                "missing_gold_no_near_a_cue": near_no,
            }
        out[m] = per
    return out


# -- reader packets ---------------------------------------------------------------------------


def build_packets(
    units: Sequence[CueUnit],
    rows: Sequence[Mapping[str, Any]],
    labelled: Mapping[str, Mapping[str, R.ReaderLine]],
    seed: int,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Condition-blind packets (the E4 layout: the E3 packet's sections 1-5, then section 6
    with the cues ``K1``.. pooled over models, exact duplicates merged, seeded shuffle) and the
    key's units. Cues whose text exactly equals an E4 item with consensus labels are left out
    of the packet and recorded in the key as ``prelabelled``."""
    texts: dict[str, str] = {}
    key_units: dict[str, Any] = {}
    for u in units:
        by_model: dict[str, list[dict[str, str]]] = {}
        pre: dict[str, list[dict[str, str]]] = {}
        for r in rows:
            if r["unit"] != u.key:
                continue
            for n, c in enumerate(r["cues"]):
                if c["status"] != "ok":
                    continue
                t = cue_text(c)
                src = {"text": t, "src": f"{r['bullet_id']}:{n}"}
                if normalize(t) in labelled.get(u.key, {}):
                    v = labelled[u.key][normalize(t)]
                    pre.setdefault(r["model"], []).append({**src, "e4_cand_id": v.cand_id})
                else:
                    by_model.setdefault(r["model"], []).append(src)
        order = sorted(set(MODELS) | set(by_model) | set(pre))  # agents label rows "agent"
        items = R.pool(by_model, seed, f"cues:{u.key}", order)
        key_units[u.key] = {
            "meeting_id": u.meeting_id,
            "product": u.product,
            "conditions": [m for m in order if m in by_model or m in pre],
            "bullet_ids": [b.id for b in u.bullets],
            "gold_ids": list(u.gold_ids),
            "items": {
                it.id: {
                    "text": it.text,
                    "sources": [
                        {"condition": s.condition, "src": s.src, "rank": s.rank} for s in it.sources
                    ],
                }
                for it in items
            },
            "shown": R.shown(items, order),
            "prelabelled": pre,
        }
        texts[f"{u.meeting_id}__{u.product}.md"] = render_packet(u, items)
    return texts, key_units


def render_packet(u: CueUnit, items: Sequence[R.PoolItem]) -> str:
    """Header, the E3 packet without its title, then section 6 (E4's candidates section)."""
    body = u.packet.split("\n", 1)[1]
    head = [
        f"# E4 reader packet: {u.meeting_id} / {PRODUCT_LABEL.get(u.product, u.product)}",
        "",
        f"Cue packet format {PACKET_VERSION} (the E4 packet layout), reader instructions "
        f"`{READER_PROMPT}`. {len(items)} candidate items (section 6). `K7` = a candidate item. "
        "Sections 1-5 are the E3 reader packet; section 6 is at the end.",
    ]
    return "\n".join([*head, body.rstrip("\n"), "", *R.candidates_section(items)])


def private_texts(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Texts that must not reach the committed aggregate."""
    out: list[str] = []
    for r in rows:
        for c in r["cues"]:
            out += [c["text"], c["owner"], *(q["text"] for q in c["quote"])]
        out += [f["item"] for f in r.get("found", [])]
    return out


# -- agent-generated runs (Claude Code agents answer job files; no API) ---------------------


def split_alignment(
    units: Sequence[CueUnit], maps: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, list[Range]]]:
    """Unit key -> bullet alignment, as the API runner aligns: TEST units use the hand maps
    (``KeyError`` if one is missing), DEV units (IS1008a, no hand map) use
    :func:`overlap_align`."""
    out: dict[str, dict[str, list[Range]]] = {}
    for u in units:
        if u.split == "dev":
            out[u.key] = overlap_align(u)
        else:
            out[u.key] = {b: list(rs) for b, rs in maps[u.key]["bullet_map"].items()}
    return out


def split_units(
    packets_dir: Path, map_dir: Path, split: str, test_meetings: Sequence[str]
) -> tuple[list[CueUnit], dict[str, dict[str, list[Range]]]]:
    """The units of one split and their alignment (:func:`split_alignment`). ``dev`` is
    :data:`PILOT_MEETING` (IS1008a, word-overlap alignment); ``test`` is ``test_meetings``
    (hand maps from ``map_dir``, which DEV never reads)."""
    if split not in ("dev", "test"):
        raise ValueError(f"split must be dev or test, not {split!r}")
    meetings = [PILOT_MEETING] if split == "dev" else list(test_meetings)
    units = [u for u in load_units(packets_dir, meetings) if u.split == split]
    maps = load_maps(map_dir) if split == "test" else {}
    return units, split_alignment(units, maps)


def job_file(unit: CueUnit, jobs: Sequence[Job], instructions: str, title: str) -> str:
    """One unit's job file: the instructions (a prompt file), every notes bullet once, then
    per job its bullet block (:func:`bullet_block`): exactly what the API call would send."""
    parts = [f"# {title}: {unit.key} ({len(jobs)} bullets)\n\n## Instructions\n\n{instructions}\n",
             "## Notes (shared by every job)\n\n" + notes_block(unit)]  # fmt: skip
    parts += [f"## Job {j.bullet_id}\n\n" + bullet_block(j) for j in jobs]
    return "\n".join(parts)


def read_agent_lines(
    text: str, job_ids: Iterable[str], label: str
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    """An agent's answer file: one JSON object per line, each with a ``bullet_id`` of a job.
    Returns ``(bullet_id, object without bullet_id)`` in file order and one error per bad,
    unknown or repeated line and for unanswered jobs. Blank lines are skipped."""
    ids = list(job_ids)
    known = set(ids)
    seen: set[str] = set()
    out: list[tuple[str, dict[str, Any]]] = []
    errors: list[str] = []
    for n, ln in enumerate(text.splitlines(), 1):
        if not ln.strip():
            continue
        try:
            d = json.loads(ln)
            bid = str(d.pop("bullet_id"))
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            errors.append(f"{label} line {n}: {e!r}")
            continue
        if bid not in known or bid in seen:
            errors.append(f"{label} line {n}: unknown or repeated bullet {bid}")
            continue
        seen.add(bid)
        out.append((bid, d))
    if missing := [b for b in ids if b not in seen]:
        errors.append(f"{label}: {len(missing)} bullets unanswered ({', '.join(missing)})")
    return out, errors


# -- the run ----------------------------------------------------------------------------------


def cache_evidence(ledger: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Prompt-cache reads and writes per stage (live calls only)."""
    out: dict[str, Any] = {}
    for r in ledger:
        if r["cached"]:
            continue
        s = out.setdefault(
            r["stage"],
            {"calls": 0, "input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
             "calls_with_reads": 0},
        )  # fmt: skip
        s["calls"] += 1
        s["input_tokens"] += int(r["input_tokens"])
        s["cache_read_tokens"] += int(r["cache_read_tokens"])
        s["cache_write_tokens"] += int(r["cache_write_tokens"])
        s["calls_with_reads"] += int(r["cache_read_tokens"] > 0)
    return out


def proxy_rows(
    rows: Sequence[Mapping[str, Any]],
    labelled: Mapping[str, Mapping[str, R.ReaderLine]],
    maps: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """:func:`unit_proxy` per (unit, model), in run order."""
    keys = list(dict.fromkeys((r["unit"], r["model"]) for r in rows))
    out = []
    for uk, m in keys:
        rs = [r for r in rows if r["unit"] == uk and r["model"] == m]
        out.append(
            {
                "unit": uk,
                "meeting_id": rs[0]["meeting_id"],
                "product": rs[0]["product"],
                "model": m,
                **unit_proxy(rs, labelled.get(uk, {}), missing_gold_lines(maps.get(uk, {}))),
            }
        )
    return out


def main(
    units: Sequence[CueUnit],
    *,
    maps: Mapping[str, Mapping[str, Any]],
    e4_key_units: Mapping[str, Mapping[str, Any]],
    e4_consensus: Sequence[Mapping[str, Any]],
    models: Sequence[str],
    out_dir: Path,
    packets_dir: Path,
    key_dir: Path,
    report: Path,
    quote_only: bool,
    pilot: bool,
    max_usd: float | None,
    seed: int,
    confirm: Callable[[str], bool],
    echo: Callable[[str], None],
) -> int:
    """``tenor ami bullet-cues``: align, quote, run (pilot: DEV, Sonnet on the first
    :data:`PILOT_SONNET_BULLETS` bullets per unit; else TEST), check quotes, compute the $0
    proxies, write the local result, and (TEST) the reader packets, the key and the
    numbers-only report. Returns an exit code."""
    import functools
    from datetime import UTC, datetime

    from tenor.blackbox.kit import check_committable
    from tenor.evals.list_check import write_json
    from tenor.evals.runner import _git
    from tenor.llm.client import BudgetExceeded, Ledger, count_tokens

    bad = [m for m in models if m not in MODELS]
    if bad or not models:
        echo(f"unknown model(s) {bad}; use {sorted(MODELS)}")
        return 2
    test_units = [u for u in units if u.split == "test"]
    dev_units = [u for u in units if u.split == "dev"]
    missing_map = [u.key for u in test_units if u.key not in maps]
    if missing_map:
        echo(f"no hand alignment for {missing_map}")
        return 2
    align = {u.key: maps[u.key]["bullet_map"] for u in test_units}
    align |= {u.key: overlap_align(u) for u in dev_units}
    agreement = Counter[str]()
    for u in test_units:
        agreement.update(alignment_agreement(overlap_align(u), maps[u.key]["bullet_map"]))
    echo(f"DEV alignment = word overlap; on TEST it agrees with the hand maps: {dict(agreement)}")
    full_jobs = [j for u in test_units for j in jobs_for(u, align[u.key])]
    dev_jobs = [j for u in dev_units for j in jobs_for(u, align[u.key])]
    pilot_jobs = {
        "haiku": dev_jobs,
        "sonnet": [j for u in dev_units for j in jobs_for(u, align[u.key])[:PILOT_SONNET_BULLETS]],
    }
    echo(f"TEST: {len(test_units)} units, {len(full_jobs)} bullets with a span; "
         f"DEV: {len(dev_units)} units, {len(dev_jobs)} bullets")  # fmt: skip
    cap = max_usd if max_usd is not None else (PILOT_MAX_USD if pilot else None)
    if cap is None and not quote_only:
        echo("paid runs need --max-usd (the approved amount); --pilot first")
        return 2
    ledger = Ledger()
    clients = {
        m: LLMClient(model=MODELS[m], ledger=ledger, max_usd=cap, offline=quote_only)
        for m in MODELS
    }
    quotes: dict[str, dict[str, dict[str, float]]] = {}
    for m in MODELS:
        counter = functools.partial(count_tokens, model=MODELS[m], cache_dir=clients[m].cache_dir)
        quotes[m] = {
            "full": quote_cost(full_jobs, counter, m),
            "pilot": quote_cost(pilot_jobs[m], counter, m),
        }
        for lab in ("full", "pilot"):
            q = quotes[m][lab]
            echo(f"{m} {lab}: {q['calls']:.0f} calls, ${q['usd_expected']:.3f} expected "
                 f"(prompt cache), ${q['usd_high']:.3f} high (no cache, output ASSUMED "
                 f"{ASSUMED_OUT[m]})")  # fmt: skip
    if quote_only:
        echo("no money spent (token counting is free)")
        return 0
    assert cap is not None
    split = "dev" if pilot else "test"
    run_jobs_by = {m: (pilot_jobs[m] if pilot else full_jobs) for m in models}
    high = sum(quotes[m]["pilot" if pilot else "full"]["usd_high"] for m in models)
    if high > cap:
        echo(f"high estimate ${high:.2f} exceeds the cap ${cap:.2f}")
        return 1
    n_calls = sum(len(v) for v in run_jobs_by.values())
    if not confirm(f"{'PILOT: ' if pilot else ''}{n_calls} cue calls on {split} with {models} "
                   f"(hard cap ${cap:.2f}, quoted high ${high:.2f})?"):  # fmt: skip
        return 1
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    rows: list[dict[str, Any]] = []
    stopped = None
    try:
        for m in models:
            rows += run_jobs(run_jobs_by[m], clients[m], m, echo)
    except BudgetExceeded as e:
        stopped = str(e)
    labelled = labelled_items(e4_key_units, e4_consensus) if not pilot else {}
    per_unit = proxy_rows(rows, labelled, maps if not pilot else {})
    summary = summarize(per_unit)
    run_units = dev_units if pilot else test_units
    texts, key_units = build_packets(run_units, rows, labelled, seed)
    n_read = sum(len(u["items"]) for u in key_units.values())
    n_pre = sum(len(v) for u in key_units.values() for v in u["prelabelled"].values())
    meta: dict[str, Any] = {
        "date": now.isoformat(timespec="seconds"),
        "git": _git(),
        "seed": seed,
        "models": {m: MODELS[m] for m in models},
        "prompt": PROMPT,
        "effort": EFFORT,
        "pad_lines": PAD,
        "max_cues_per_bullet": MAX_CUES,
        "alignment": {
            "test": "hand maps (data/private/feature_tests/map, bullet_map)",
            "dev": f"word overlap ({ALIGN_WINDOW}-line window, >= {ALIGN_MIN_HITS} "
            "distinct content words)",
            "overlap_vs_hand_on_test": dict(agreement),
        },
        "labels": "E4 TEST consensus (data/private/reader/runs/e4_test_consensus), exact text "
        "match only; location proxy from the map files' gold lines (+/- "
        f"{GOLD_NEAR} lines)",
        "pilot": pilot,
        "split": split,
        "max_usd": cap,
        "stopped_early": stopped,
        "spent_usd": ledger.spent_usd(),
        "ledger_by_stage": ledger.by_stage(),
        "cache_evidence": cache_evidence(ledger.to_json()),
        "quote": quotes,
        "reader": {
            "prompt": READER_PROMPT,
            "packet_version": PACKET_VERSION,
            "items_to_read": n_read,
            "prelabelled_exact": n_pre,
        },
    }
    if pilot:
        meta["reprice"] = reprice(ledger.to_json(), {m: len(full_jobs) for m in MODELS})
    suffix = ".pilot" if pilot else ""
    write_json(
        out_dir / f"{stamp}{suffix}.json",
        {"meta": meta, "summary": summary, "per_unit": per_unit, "rows": rows,
         "ledger": ledger.to_json()},
    )  # fmt: skip
    echo(f"spent ${ledger.spent_usd():.4f} (cap ${cap:.2f}) -> {out_dir / (stamp + suffix)}.json")
    echo(f"cache: {json.dumps(meta['cache_evidence'])}")
    for r in per_unit:
        echo(f"  {r['model']:6} {r['unit']:18} bullets {r['bullets']:3} ok {r['ok']:3} "
             f"failed {sum(r['failed'].values())} distinct {r['distinct_by_quote']}")  # fmt: skip
    if pilot:
        rp = meta["reprice"]
        echo(f"TEST re-priced from the pilot ledger (x{MARGIN}): "
             f"{ {k: round(v, 3) for k, v in rp['full_usd_with_margin'].items()} }")  # fmt: skip
    if stopped:
        echo(f"STOPPED: {stopped}")
        return 1
    if not pilot:
        packets_dir.mkdir(parents=True, exist_ok=True)
        for name, text in texts.items():
            (packets_dir / name).write_text(text, encoding="utf-8")
        key = {
            "meta": {"key_version": KEY_VERSION, "packet_version": PACKET_VERSION,
                     "reader_prompt": READER_PROMPT, "seed": seed, "run_id": stamp,
                     "models": list(models), "prompt": PROMPT},
            "units": key_units,
        }  # fmt: skip
        key_dir.mkdir(parents=True, exist_ok=True)
        kpath = key_dir / f"{stamp}.key.json"
        kpath.write_text(json.dumps(key, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        echo(f"packets: {packets_dir} ({len(texts)}), {n_read} items to read, {n_pre} "
             f"prelabelled by exact E4 match; key: {kpath}")  # fmt: skip
        agg = {"meta": meta, "summary": summary}
        check_committable(agg, private_texts(rows))
        write_json(report, agg)
        echo(f"report (numbers only): {report}")
    return 0
