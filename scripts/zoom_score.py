"""Source pointers: score the pointer agents and Granola's Zoom In against the same truth.

    uv run --no-sync python scripts/zoom_score.py pointers --run <RUN> [--model NAME]
    uv run --no-sync python scripts/zoom_score.py zoomin   --sheet <filled sheet .md | .json>
    uv run --no-sync python scripts/zoom_score.py compare  --run <RUN> --sheet <...>
    uv run --no-sync python scripts/zoom_score.py chance

Truth: ``data/private/reader/zoom_truth.json`` (``scripts/zoom_truth.py``; human AMI summary
links mapped to the product's transcript). Rule, the same for both tools
(``tenor.evals.zoom``): a bullet is at the right moment when any timestamp shown for it is
within 30 seconds of a true time range; a picked line counts as its start time. Also within 10
and 60 seconds, for the first timestamp alone, the share of timestamps inside a true range
(pointers: picked lines that are true lines), and by confidence. CIs are percentile bootstraps
that resample meetings (``--seed``, default 42).

- ``pointers`` reads ``data/private/reader/pointer_agent_runs/<RUN>/<meeting>.jsonl`` (one
  ``{"bullet_id","lines":[...],"confidence"}`` per line) and writes ``<RUN>/score.json``. A
  selected bullet with no answer is wrong.
- ``zoomin`` reads the filled click sheet (``data/private/ZOOM_CLICK_SHEET.md``) or a JSON
  list of ``{"meeting","bullet_id","timestamps":["mm:ss",...]}`` and writes
  ``<sheet>.score.json``. ``none`` (nothing shown) is wrong; an empty cell is not measured.
- ``compare`` pairs the two on the bullets both were measured on: both right, only pointers,
  only Zoom In, neither, with an exact two-sided sign test.
- ``chance``: the probability that a random timestamp is within the tolerance of a bullet's
  true ranges, averaged over the selected bullets.

Local data only: no LLM, no network. Outputs are private.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tenor.evals import zoom as Z  # noqa: E402

READERS = ROOT / "data/private/reader"
TRUTH = READERS / "zoom_truth.json"
RUNS = READERS / "pointer_agent_runs"
JOBS = READERS / "pointer_jobs"


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=ROOT
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def job_lines(job: str) -> dict[str, float]:
    """T id -> start seconds, read from a pointer job's transcript section."""
    out: dict[str, float] = {}
    for line in job.split("## Transcript", 1)[-1].splitlines():
        if line.startswith("[T") and "]" in line:
            tid, *_, stamp = line[1 : line.index("]")].split()
            out[tid] = Z.parse_timestamp(stamp)
    return out


def score_pointers(
    truth: dict[str, Any], run: Path, jobs: Path
) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows for every selected bullet from a pointer run, and the problems found."""
    shown: list[Z.Shown] = []
    errors: list[str] = []
    for mid, m in truth["meetings"].items():
        path = run / f"{mid}.jsonl"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if not path.exists():
            errors.append(f"{mid}: no answer file")
        lines = job_lines((jobs / f"{mid}.md").read_text(encoding="utf-8"))
        got, errs = Z.parse_pointers(text, mid, [b["id"] for b in m["bullets"]], lines)
        shown += got
        errors += errs
    rows, missing = Z.score_all(shown, truth)
    assert not missing  # parse_pointers answers every asked bullet
    return rows, errors


def score_zoomin(truth: dict[str, Any], sheet: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows for the selected bullets the sheet has a result for, and the problems found
    (unreadable cells, selected bullets not measured)."""
    shown, errors = Z.parse_sheet(sheet.read_text(encoding="utf-8"))
    rows, missing = Z.score_all(shown, truth)
    return rows, errors + [f"{x}: not measured" for x in missing]


def meta(truth: dict[str, Any], seed: int, **more: Any) -> dict[str, Any]:
    return {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "seed": seed,
        "truth_version": truth["meta"]["truth_version"],
        "truth_seed": truth["meta"]["seed"],
        "selected": truth["meta"]["n_selected"],
        "rule": f"any timestamp within {Z.PRIMARY_S} s of a true time range",
        "chance": Z.chance_baseline(truth),
        **more,
    }


def _fmt(cell: dict[str, Any]) -> str:
    if cell["rate"] is None:
        return "n/a"
    ci = f" [{cell['ci'][0]:.2f}, {cell['ci'][1]:.2f}]" if cell["ci"] else ""
    return f"{cell['rate']:.2f}{ci} ({cell['num']}/{cell['den']})"


def show(name: str, s: dict[str, Any], chance: dict[str, float]) -> None:
    print(f"{name}: {s['bullets']} bullets, {s['meetings']} meetings")
    for tol in Z.TOLERANCES_S:
        print(
            f"  right moment within {tol:>2} s: {_fmt(s[f'right_moment_{tol}s'])}   "
            f"first timestamp only: {_fmt(s[f'first_timestamp_{tol}s'])}   "
            f"chance {chance[f'{tol}s']:.3f}"
        )
    print(
        f"  nothing shown: {s['nothing_shown']}; "
        f"timestamps per bullet: {s['timestamps_per_bullet']}"
    )
    print(f"  timestamps inside a true range / true lines: {_fmt(s['timestamps_inside_true'])}")
    print(f"  timestamps within {Z.PRIMARY_S} s: {_fmt(s[f'timestamps_within_{Z.PRIMARY_S}s'])}")
    for group in ("by_confidence", "by_meeting"):
        for k, v in s[group].items():
            print(f"  {group[3:]} {k}: {_fmt(v)}")


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("mode", choices=("pointers", "zoomin", "compare", "chance"))
    ap.add_argument("--run", help="Pointer run name under pointer_agent_runs/.")
    ap.add_argument("--sheet", type=Path, help="Filled click sheet (.md) or JSON.")
    ap.add_argument("--truth", type=Path, default=TRUTH)
    ap.add_argument("--runs-dir", type=Path, default=RUNS)
    ap.add_argument("--jobs", type=Path, default=JOBS)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--model", default="Claude Code agents (model not recorded)")
    a = ap.parse_args(argv)
    truth = json.loads(a.truth.read_text(encoding="utf-8"))
    chance = Z.chance_baseline(truth)
    if a.mode == "chance":
        print(f"chance baseline over {truth['meta']['n_selected']} bullets: {chance}")
        return
    if a.mode in ("pointers", "compare") and not a.run:
        ap.error("--run is required")
    if a.mode in ("zoomin", "compare") and not a.sheet:
        ap.error("--sheet is required")
    result: dict[str, Any] = {}
    if a.run:
        p_rows, p_err = score_pointers(truth, a.runs_dir / a.run, a.jobs)
        result["pointers"] = {
            "summary": Z.summarise(p_rows, a.seed),
            "rows": p_rows,
            "problems": p_err,
        }
        show(f"pointers ({a.run})", result["pointers"]["summary"], chance)
    if a.sheet:
        z_rows, z_err = score_zoomin(truth, a.sheet)
        result["zoomin"] = {
            "summary": Z.summarise(z_rows, a.seed),
            "rows": z_rows,
            "problems": z_err,
        }
        show(f"Zoom In ({a.sheet.name})", result["zoomin"]["summary"], chance)
    if a.mode == "compare":
        result["compare"] = c = Z.compare(p_rows, z_rows, a.seed)
        print(f"paired on {c['bullets']} bullets, {c['meetings']} meetings")
        for tol in Z.TOLERANCES_S:
            t = c[f"{tol}s"]
            print(
                f"  within {tol:>2} s: both {t['both']}, only pointers {t['only_pointers']}, only "
                f"Zoom In {t['only_zoomin']}, neither {t['neither']}; sign test p = "
                f"{t['sign_test_p']:.2g}; pointers - Zoom In {t['difference']['rate']} "
                f"{t['difference']['ci']}"
            )
    for key in ("pointers", "zoomin"):
        for e in result.get(key, {}).get("problems", []):
            print(f"  problem ({key}): {e}")
    result["meta"] = meta(
        truth,
        a.seed,
        mode=a.mode,
        prompt=Z.PROMPT if a.run else None,
        pointer_model=a.model if a.run else None,
        run=a.run,
        sheet=str(a.sheet) if a.sheet else None,
    )
    if a.mode == "pointers":
        out = a.runs_dir / a.run / "score.json"
    elif a.mode == "zoomin":
        out = a.sheet.with_suffix(".score.json")
    else:
        out = a.runs_dir / a.run / "compare.json"
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
