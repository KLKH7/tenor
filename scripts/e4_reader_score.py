"""Score E4's conditions from Claude Code reader verdicts (``prompts/e4_reader.v1.md``).

    uv run --no-sync python scripts/e4_reader_score.py \
        --key data/private/reader/e4_keys/<run_id>.key.json \
        --runs data/private/reader/runs/e4_<reader_run>/ \
        [--notes-reader data/private/reader/runs/test_v2_all.jsonl] [--seed 42] [--out PATH]

Reads every ``*.jsonl`` under ``--runs`` (or the files given), validates each line
(``tenor.evals.e4_reader.ReaderLine``) and each unit against the key (every K id once, known
bullets and gold), maps K ids back to conditions, and reports per condition, per product and
pooled: items shown, true new omissions (a decision / action / problem that happened as
stated and is not in the notes), duplicates of the notes, false items, and missing gold
recovered (gold the notes miss per the E3 reader run that a shown item states), with
bootstrap 95% CIs resampling meetings. A unit with any problem is not scored (listed).
Local data only: no LLM, no network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "packages")]

from tenor.evals import e4_reader as R  # noqa: E402

NOTES_READER = ROOT / "data/private/reader/runs/test_v2_all.jsonl"


def read_runs(paths: Sequence[Path]) -> tuple[list[R.ReaderLine], list[str]]:
    lines: list[R.ReaderLine] = []
    errors: list[str] = []
    files = sorted(f for p in paths for f in (sorted(p.glob("*.jsonl")) if p.is_dir() else [p]))
    for f in files:
        got, errs = R.parse_lines(f.read_text(encoding="utf-8").splitlines())
        lines += got
        errors += [f"{f.name} {e}" for e in errs]
    return lines, errors


def table(summary: dict[str, Any]) -> list[str]:
    def pct(x: float | None) -> str:
        return "-" if x is None else f"{100 * x:.0f}%"

    def ci(x: Sequence[float] | None) -> str:
        return "" if x is None else f" [{100 * x[0]:.0f}, {100 * x[1]:.0f}]"

    out = []
    for g, per in summary.items():
        out += [f"## {g}", "",
                "| Cond | Units | Shown | True new | Dup | False | Missing gold recovered |",
                "|---|---|---|---|---|---|---|"]  # fmt: skip
        for c, s in per.items():
            t = s["totals"]
            out.append(
                f"| {c} | {s['n_units']} | {t['n_shown']} | {t['true_new']} "
                f"({pct(s['precision_new'])}{ci(s['precision_new_ci'])}) | {t['duplicates']} "
                f"({pct(s['duplicates'])}) | {t['false']} ({pct(s['false'])}) | "
                f"{t['recovered']}/{t['n_missing']} ({pct(s['recall_missing'])}"
                f"{ci(s['recall_missing_ci'])}) |"
            )
        out.append("")
    return out


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--key", type=Path, required=True)
    ap.add_argument("--runs", type=Path, nargs="+", required=True)
    ap.add_argument("--notes-reader", type=Path, default=NOTES_READER)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    key = json.loads(a.key.read_text(encoding="utf-8"))
    notes_rows = [
        json.loads(x) for x in a.notes_reader.read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    missing = R.missing_gold(notes_rows)
    lines, errors = read_runs(a.runs)
    by_unit: dict[str, list[R.ReaderLine]] = {}
    for ln in lines:
        by_unit.setdefault(f"{ln.meeting}:{ln.product}", []).append(ln)
    verdicts: dict[str, R.Verdicts] = {}
    problems: dict[str, list[str]] = {}
    for uk, ls in sorted(by_unit.items()):
        if uk not in key["units"]:
            problems[uk] = ["unit not in the key"]
            continue
        unit = key["units"][uk]
        v, probs = R.check_unit(unit, ls)
        if uk not in missing:
            probs.append("unit not in the notes-reader run")
        elif gaps := sorted(
            set(unit["gold_ids"])
            - {r["gold_id"] for r in notes_rows if f"{r['meeting']}:{r['product']}" == uk}
        ):
            probs.append(f"gold without a notes-reader verdict: {gaps}")  # fmt: skip
        if probs:
            problems[uk] = probs
        else:
            verdicts[uk] = v
    result = R.score(key["units"], verdicts, missing, a.seed)
    result["meta"] = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "reader_prompt": R.READER_PROMPT,
        "key": key["meta"],
        "seed": a.seed,
        "inputs": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()[:16]
            for p in [
                a.key,
                a.notes_reader,
                *sorted(
                    f for r in a.runs for f in (sorted(r.glob("*.jsonl")) if r.is_dir() else [r])
                ),
            ]
        },
        "line_errors": errors,
        "unit_problems": problems,
    }
    out = a.out or a.key.with_name(a.key.name.replace(".key.json", ".score.json"))
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for e in errors:
        print(f"invalid: {e}", file=sys.stderr)
    for uk, ps in problems.items():
        print(f"not scored {uk}: {'; '.join(ps[:5])}", file=sys.stderr)
    print("\n".join(table(result["summary"])))
    print(f"{len(verdicts)} units scored, {len(result['units_not_scored'])} not; {out}")


if __name__ == "__main__":
    main()
