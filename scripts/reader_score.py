"""Score an E3 second-reader run (prompts/notes_reader.v1.md) against reference verdicts.

    uv run --no-sync python scripts/reader_score.py READER [READER ...] \
        [--adjudication data/private/E3_ADJUDICATION.jsonl] \
        [--human-key data/private/E3_CHECK_key.json] [--judge] \
        [--other OTHER_READER ...] [--prompt notes_reader.v1] [--json OUT]

READER: reader JSONL files or directories of them (``*.jsonl``, read in name order). Reports,
on the notes verdict (full / partial / none; the reader's ``no`` = ``none``):

* against the first adjudication (``your_notes_verdict`` in E3_ADJUDICATION.jsonl): agreement,
  Cohen's kappa (3-class unweighted and linear-weighted), confusion matrix, by category and by
  product;
* against the 15 human-checked cards (E3_CHECK_key.json; the human agreed with the ``agent``
  field on all 15, so that is the human label): x / 15;
* with ``--judge``: against the old judge majority (``judge_majority`` in the adjudication);
* with ``--other``: between two readers (the DEV two-reader check), same statistics;
* recall per product: full = 1, partial = 0.5, mean per meeting, then mean over meetings
  (overall and per category), for the reader and every reference it is compared with, on the
  items both have.

No LLM, no network. Deterministic apart from the run date in ``--json`` meta.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

ROOT = Path(__file__).resolve().parent.parent
ADJUDICATION = ROOT / "data/private/E3_ADJUDICATION.jsonl"
HUMAN_KEY = ROOT / "data/private/E3_CHECK_key.json"
PROMPT = "notes_reader.v1"
CLASSES = ("none", "partial", "full")  # ordered: linear weights use the distance
CATEGORY_OF = {"D": "decision", "A": "action", "P": "problem"}
CATEGORIES = ("decision", "action", "problem")
CREDIT = {"none": 0.0, "partial": 0.5, "full": 1.0}
LABEL = {
    "full": "full",
    "fully": "full",
    "partial": "partial",
    "partly": "partial",
    "no": "none",
    "none": "none",
}

Key = tuple[str, str, str]  # (meeting, product, gold_id)


class ReaderRow(BaseModel):
    """One line of a reader's JSONL output (the schema in prompts/notes_reader.v1.md)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    meeting: str
    product: Literal["granola", "wispr"]
    gold_id: str
    notes_verdict: Literal["full", "partial", "no"]
    transcript_verdict: Literal["yes", "partly", "no"]
    evidence_bullets: tuple[str, ...]
    evidence_quote: str
    reason: str
    confidence: Literal["high", "medium", "low"]

    @field_validator("gold_id")
    @classmethod
    def _gold_id(cls, v: str) -> str:
        if len(v) < 2 or v[0] not in CATEGORY_OF or not v[1:].isdigit():
            raise ValueError(f"gold_id must look like D1 / A2 / P3, got {v!r}")
        return v

    @field_validator("evidence_bullets")
    @classmethod
    def _bullets(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if bad := [b for b in v if not (b.startswith("B") and b[1:].isdigit())]:
            raise ValueError(f"evidence_bullets must be B<n> ids, got {bad}")
        return v

    @property
    def key(self) -> Key:
        return (self.meeting, self.product, self.gold_id)


def norm_label(label: str) -> str:
    """A notes verdict in any of the files' spellings as ``none`` / ``partial`` / ``full``."""
    try:
        return LABEL[label.strip().lower()]
    except KeyError:
        raise ValueError(f"unknown verdict {label!r}") from None


def category(key: Key) -> str:
    return CATEGORY_OF[key[2][0]]


# -- loading ----------------------------------------------------------------------------------


def _jsonl_files(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        out += sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
    return out


def load_reader(paths: Iterable[Path]) -> dict[Key, ReaderRow]:
    """Reader rows by key from JSONL files / directories. Raises ``ValueError`` on a bad line or
    a duplicate key, naming the file and line."""
    rows: dict[Key, ReaderRow] = {}
    for f in _jsonl_files(paths):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = ReaderRow.model_validate_json(line)
            except ValueError as e:
                raise ValueError(f"{f}:{n}: {e}") from e
            if row.key in rows:
                raise ValueError(f"{f}:{n}: duplicate item {row.key}")
            rows[row.key] = row
    return rows


def reader_labels(rows: Mapping[Key, ReaderRow]) -> dict[Key, str]:
    return {k: norm_label(r.notes_verdict) for k, r in rows.items()}


def load_adjudication(path: Path) -> tuple[dict[Key, str], dict[Key, str]]:
    """(second-reader labels, judge-majority labels) by key from E3_ADJUDICATION.jsonl."""
    agent: dict[Key, str] = {}
    judge: dict[Key, str] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        k = (r["meeting"], r["product"], r["gold_id"])
        agent[k] = norm_label(r["your_notes_verdict"])
        judge[k] = norm_label(r["judge_majority"])
    return agent, judge


def load_human(path: Path) -> dict[Key, str]:
    """Human labels by key from E3_CHECK_key.json: each card's ``agent`` field (the human agreed
    with the agent on every card)."""
    cards = json.loads(path.read_text())["cards"]
    return {
        (c["meeting_id"], c["product"], c["item_id"]): norm_label(c["agent"])
        for c in cards.values()
    }


# -- statistics -------------------------------------------------------------------------------


def confusion(pairs: Sequence[tuple[str, str]]) -> list[list[int]]:
    """3x3 counts, rows = first label, columns = second, both in :data:`CLASSES` order."""
    idx = {c: i for i, c in enumerate(CLASSES)}
    m = [[0] * len(CLASSES) for _ in CLASSES]
    for a, b in pairs:
        m[idx[a]][idx[b]] += 1
    return m


def kappa(pairs: Sequence[tuple[str, str]], weights: Literal["none", "linear"]) -> float | None:
    """Cohen's kappa over :data:`CLASSES`; ``linear``: disagreement weight |i - j| / (k - 1).
    ``None`` when undefined (no pairs, or chance disagreement is zero)."""
    n = len(pairs)
    if not n:
        return None
    k = len(CLASSES)
    m = confusion(pairs)
    rows = [sum(r) for r in m]
    cols = [sum(m[i][j] for i in range(k)) for j in range(k)]

    def w(i: int, j: int) -> float:
        return abs(i - j) / (k - 1) if weights == "linear" else float(i != j)

    observed = sum(w(i, j) * m[i][j] for i in range(k) for j in range(k)) / n
    expected = sum(w(i, j) * rows[i] * cols[j] for i in range(k) for j in range(k)) / n**2
    return None if expected == 0 else 1 - observed / expected


def _stats(pairs: Sequence[tuple[str, str]]) -> dict[str, Any]:
    agree = sum(a == b for a, b in pairs)
    return {
        "n": len(pairs),
        "agree": agree,
        "agreement": agree / len(pairs) if pairs else None,
        "kappa": kappa(pairs, "none"),
        "kappa_linear": kappa(pairs, "linear"),
    }


def agreement(a: Mapping[Key, str], b: Mapping[Key, str]) -> dict[str, Any]:
    """Agreement of label sets ``a`` and ``b`` on their shared keys: overall (with the confusion
    matrix, rows = ``a``), by category and by product; plus keys only one side has."""
    shared = sorted(set(a) & set(b))
    pairs = [(a[k], b[k]) for k in shared]
    out = _stats(pairs)
    out["confusion"] = {"classes": list(CLASSES), "rows_first": confusion(pairs)}
    out["by_category"] = {
        c: _stats([(a[k], b[k]) for k in shared if category(k) == c]) for c in CATEGORIES
    }
    products = sorted({k[1] for k in shared})
    out["by_product"] = {p: _stats([(a[k], b[k]) for k in shared if k[1] == p]) for p in products}
    out["only_first"] = len(set(a) - set(b))
    out["only_second"] = len(set(b) - set(a))
    return out


def recall(labels: Mapping[Key, str]) -> dict[str, dict[str, float | None]]:
    """Per product: recall (full = 1, partial = 0.5) as the mean over meetings of each meeting's
    mean credit; overall (``all``) and per category (meetings with no item of it skipped)."""
    out: dict[str, dict[str, float | None]] = {}
    for p in sorted({k[1] for k in labels}):
        row: dict[str, float | None] = {}
        for cat in ("all", *CATEGORIES):
            per_meeting: dict[str, list[float]] = {}
            for k, v in labels.items():
                if k[1] == p and cat in ("all", category(k)):
                    per_meeting.setdefault(k[0], []).append(CREDIT[v])
            means = [sum(xs) / len(xs) for xs in per_meeting.values()]
            row[cat] = sum(means) / len(means) if means else None
        out[p] = row
    return out


def human_score(reader: Mapping[Key, str], human: Mapping[Key, str]) -> dict[str, Any]:
    """Reader vs the human cards: matches out of the cards the reader judged, and misses."""
    judged = sorted(set(human) & set(reader))
    wrong = [
        {"item": "/".join(k), "reader": reader[k], "human": human[k]}
        for k in judged
        if reader[k] != human[k]
    ]
    return {
        "cards": len(human),
        "judged": len(judged),
        "match": len(judged) - len(wrong),
        "mismatches": wrong,
    }


# -- report -----------------------------------------------------------------------------------


def _f(x: float | None, nd: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def format_agreement(title: str, s: Mapping[str, Any]) -> list[str]:
    if not s["n"]:
        return [f"{title}: no shared items"]
    out = [
        f"{title}: {s['agree']}/{s['n']} agree ({100 * s['agreement']:.1f}%), kappa "
        f"{_f(s['kappa'], 2)}, linear-weighted {_f(s['kappa_linear'], 2)}"
        f" [only first {s['only_first']}, only second {s['only_second']}]",
        "  confusion (rows: first; cols: second)  " + "  ".join(f"{c:>7}" for c in CLASSES),
    ]
    for c, r in zip(CLASSES, s["confusion"]["rows_first"], strict=True):
        out.append(f"  {c:>38}  " + "  ".join(f"{v:>7}" for v in r))
    for group in ("by_category", "by_product"):
        for name, g in s[group].items():
            if g["n"]:
                out.append(
                    f"  {name:<9} {g['agree']}/{g['n']} ({100 * g['agreement']:.1f}%), kappa "
                    f"{_f(g['kappa'], 2)}, linear {_f(g['kappa_linear'], 2)}"
                )
    return out


def format_recall(title: str, r: Mapping[str, Mapping[str, float | None]]) -> list[str]:
    return [f"{title} recall:"] + [
        f"  {p:<8} all {_f(v['all'])}  " + "  ".join(f"{c} {_f(v[c])}" for c in CATEGORIES)
        for p, v in r.items()
    ]


def score(
    reader: Mapping[Key, str],
    adjudication: tuple[Mapping[Key, str], Mapping[Key, str]] | None,
    human: Mapping[Key, str] | None,
    with_judge: bool,
    other: Mapping[Key, str] | None,
) -> dict[str, Any]:
    """Every comparison as a JSON-able dict (``reader`` vs each reference that is given)."""
    out: dict[str, Any] = {"items": len(reader), "recall": {"reader": recall(reader)}}
    if adjudication is not None:
        agent, judge = adjudication
        out["vs_adjudication"] = agreement(reader, agent)
        out["recall"]["adjudication"] = recall({k: agent[k] for k in reader if k in agent})
        if with_judge:
            out["vs_judge"] = agreement(reader, judge)
            out["recall"]["judge_majority"] = recall({k: judge[k] for k in reader if k in judge})
    if human is not None:
        out["vs_human"] = human_score(reader, human)
    if other is not None:
        out["vs_other_reader"] = agreement(reader, other)
        out["recall"]["other_reader"] = recall(other)
    return out


def report(s: Mapping[str, Any]) -> str:
    out = [f"reader items: {s['items']}"]
    for key, title in (
        ("vs_adjudication", "reader vs first adjudication"),
        ("vs_judge", "reader vs judge majority"),
        ("vs_other_reader", "reader vs other reader"),
    ):
        if key in s:
            out += format_agreement(title, s[key])
    if "vs_human" in s:
        h = s["vs_human"]
        out.append(
            f"reader vs human cards: {h['match']}/{h['judged']} (of {h['cards']} cards)"
            + "".join(f"\n  miss {m['item']}: reader {m['reader']}, human {m['human']}"
                      for m in h["mismatches"])
        )  # fmt: skip
    for name, r in s["recall"].items():
        out += format_recall(name, r)
    return "\n".join(out)


def _sha(paths: Iterable[Path]) -> dict[str, str]:
    return {
        str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()[:16]
        for p in paths
    }


def _git() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("reader", nargs="+", type=Path, help="Reader JSONL files or directories.")
    ap.add_argument("--adjudication", type=Path, default=ADJUDICATION)
    ap.add_argument("--no-adjudication", action="store_true")
    ap.add_argument("--human-key", type=Path, default=HUMAN_KEY)
    ap.add_argument("--no-human", action="store_true")
    ap.add_argument("--judge", action="store_true", help="Also compare with the judge majority.")
    ap.add_argument("--other", nargs="+", type=Path, default=None, help="A second reader run.")
    ap.add_argument("--prompt", default=PROMPT, help="Reader prompt version (recorded).")
    ap.add_argument("--json", type=Path, default=None, help="Also write the scores here.")
    a = ap.parse_args(argv)

    files = _jsonl_files(a.reader)
    reader = reader_labels(load_reader(files))
    adjudication = None if a.no_adjudication else load_adjudication(a.adjudication)
    human = None if a.no_human else load_human(a.human_key)
    other_files = _jsonl_files(a.other) if a.other else []
    other = reader_labels(load_reader(other_files)) if a.other else None
    s = score(reader, adjudication, human, a.judge, other)
    print(report(s))
    if a.json is not None:
        refs = [p for p, off in ((a.adjudication, a.no_adjudication), (a.human_key, a.no_human))
                if not off]  # fmt: skip
        s["meta"] = {
            "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git": _git(),
            "reader_prompt": a.prompt,
            "inputs": _sha([*files, *other_files, *refs]),
        }
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(s, indent=2, sort_keys=True) + "\n")
        print(f"wrote {a.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
