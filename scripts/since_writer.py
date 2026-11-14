"""The "since last time" writer on the item-fates pairs ($0, agents).

    uv run --no-sync python scripts/since_writer.py dump --run <RUN> --prompt <NAME> [--dev]
    uv run --no-sync python scripts/since_writer.py dump-check --run <RUN> [--dev]
    uv run --no-sync python scripts/since_writer.py score --run <RUN> [--model NAME] [--seed 42]

Pairs are the 36 seeded item-fates pairs (``scripts/fates.py``, seed 42); ``--dev`` uses
instead the 6 development pairs that study excluded, which have no fates and are only for
developing a writer prompt. Everything is under ``data/private/reader``.

``dump`` writes one writer job per pair (``sw_jobs/<RUN>/writer/<pair>.md``: the prompt
``--prompt``, X's annotated decisions and actions laid out as notes, Y's human transcript) and
records the prompt in ``sw_runs/<RUN>/run.json``. One agent per pair answers into
``sw_runs/<RUN>/writer/<pair>.jsonl``. The fates are in no job file.

``dump-check`` validates the writer's lines mechanically and writes a checker job per pair
with at least one valid line (``sw_jobs/<RUN>/check/<pair>.md``: prompt
``since_last_time_check.v1``); checkers a and b answer into
``sw_runs/<RUN>/check_<a|b>/<pair>.jsonl``.

``score`` joins the lines with the fates of run ``fates_20261110`` and prints sentences judged
true, silent items covered, pairs with and without a silent item that got a sentence, and
sentences per pair, with intervals that resample teams; it writes ``score.json`` and
``rows.json`` in the run directory. With ``--dev`` it prints the lines and verdicts only.

Same inputs, same bytes. Local data only, private outputs: no LLM, no network.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tenor.ami.corpus import DEFAULT_ZIP, load_meeting
from tenor.evals import came_back as K
from tenor.evals import follow_through as T
from tenor.evals import item_fates as F
from tenor.evals import since_writer as W

READERS = Path("data/private/reader")
JOBS = READERS / "sw_jobs"
RUNS = READERS / "sw_runs"
FATES_ROWS = READERS / "fates_runs" / "fates_20261110" / "rows.json"
SEED = 42  # the item-fates selection seed


def pairs_of(dev: bool) -> list[K.Pair]:
    """The 36 item-fates pairs, or the 6 development pairs that study excluded."""
    available = K.available_pairs(K.summary_counts(DEFAULT_ZIP))
    earlier = K.select(available, SEED)
    if dev:
        return list(earlier.develop)
    empty = [p for p in available if not F.items(load_meeting(p.x))]
    return F.select(available, [*earlier.develop, *empty], SEED)


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _run_record(run: str) -> dict[str, Any]:
    path = RUNS / run / "run.json"
    if not path.exists():
        raise SystemExit(f"no run record {path}: run dump first")
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return record


def dump(run: str, prompt_name: str, dev: bool) -> None:
    prompt = Path(f"prompts/{prompt_name}.md").read_text(encoding="utf-8")
    out = JOBS / run / "writer"
    out.mkdir(parents=True, exist_ok=True)
    (RUNS / run / "writer").mkdir(parents=True, exist_ok=True)
    chosen = pairs_of(dev)
    for p in chosen:
        x, y, items = W.units(load_meeting(p.x), load_meeting(p.y))
        answer = str(RUNS / run / "writer" / f"{p.name}.jsonl")
        (out / f"{p.name}.md").write_text(
            W.writer_job(x, y, prompt_name, prompt, answer), encoding="utf-8"
        )
        print(f"{p.name}\t{len(items)} items\t{len(y.transcript)} lines")
    record = {"run": run, "writer_prompt": prompt_name, "dev": dev, "pairs": len(chosen)}
    (RUNS / run / "run.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"{len(chosen)} writer jobs in {out} (prompt {prompt_name})")


def read_writer(run: str, pair: K.Pair) -> tuple[list[dict[str, Any]], list[str]] | None:
    """The validated writer lines of a pair and the problems, or ``None`` with no output."""
    path = RUNS / run / "writer" / f"{pair.name}.jsonl"
    if not path.exists():
        return None
    x, y, _ = W.units(load_meeting(pair.x), load_meeting(pair.y))
    return T.parse_writer(path.read_text(encoding="utf-8"), [b.id for b in x.bullets], y)


def dump_check(run: str) -> None:
    record = _run_record(run)
    prompt = Path(f"prompts/{T.CHECK_PROMPT}.md").read_text(encoding="utf-8")
    out = JOBS / run / "check"
    out.mkdir(parents=True, exist_ok=True)
    n_jobs = n_lines = 0
    for p in pairs_of(bool(record["dev"])):
        got = read_writer(run, p)
        if got is None:
            print(f"PROBLEM {p.name}: no writer output")
            continue
        written, bad = got
        for e in bad:
            print(f"PROBLEM {p.name} writer: {e}")
        for w in written:
            if w["status"] != "ok":
                print(f"REJECTED {p.name} #{w['n']}: {w['status']}: {w['line'].sentence}")
        ok = [w["line"] for w in written if w["status"] == "ok"]
        if not ok:
            continue
        x, y, items = W.units(load_meeting(p.x), load_meeting(p.y))
        answer = str(RUNS / run / "check_<your checker letter: a or b>" / f"{p.name}.jsonl")
        (out / f"{p.name}.md").write_text(
            W.check_job(x, y, items, ok, prompt, answer), encoding="utf-8"
        )
        n_jobs += 1
        n_lines += len(ok)
    for c in T.CHECKERS:
        (RUNS / run / f"check_{c}").mkdir(parents=True, exist_ok=True)
    print(f"{n_jobs} checker jobs ({n_lines} sentences) in {out}")


def line_rows(
    run: str, chosen: list[K.Pair], fates: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """One row per writer line of the run (``follow_through.line_rows``), and the problems."""
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for p in chosen:
        got = read_writer(run, p)
        if got is None:
            errors.append(f"{p.name}: no writer output")
            continue
        written, bad = got
        errors += [f"{p.name} writer: {e}" for e in bad]
        n_ok = sum(w["status"] == "ok" for w in written)
        ids = [i.item_id for i in W.units(load_meeting(p.x), load_meeting(p.y))[2]]
        verdicts: dict[str, dict[int, T.Verdict]] = {}
        for c in T.CHECKERS:
            path = RUNS / run / f"check_{c}" / f"{p.name}.jsonl"
            if not path.exists():
                if n_ok:
                    errors.append(f"{p.name}: no answer from checker {c}")
                continue
            v, bad = T.parse_verdicts(path.read_text(encoding="utf-8"), n_ok, ids)
            verdicts[c] = v
            errors += [f"{p.name} checker {c}: {e}" for e in bad]
            errors += [f"{p.name} checker {c}: no verdict for sentence {k}"
                       for k in range(1, n_ok + 1) if k not in v]  # fmt: skip
        mine = [r for r in fates if r["pair"] == p.name]
        rows += T.line_rows(p.name, written, verdicts, mine)
    return rows, errors


def score(run: str, model: str, seed: int) -> None:
    record = _run_record(run)
    dev = bool(record["dev"])
    chosen = pairs_of(dev)
    fates: list[dict[str, Any]] = (
        [] if dev else json.loads(FATES_ROWS.read_text(encoding="utf-8"))["items"]
    )
    rows, errors = line_rows(run, chosen, fates)
    for r in rows:
        print(
            f"{r['unit']} #{r['n']} [{r['kind']}] status={r['status']} a={r['supported_a']} "
            f"b={r['supported_b']} items={r['x_items']} fates={r['item_fates']}\n"
            f"    {r['sentence']}"
        )
    for e in errors:
        print(f"PROBLEM {e}")
    meta = {
        "run": run,
        "git_sha": _git_sha(),
        "model": model,
        "prompt_versions": [record["writer_prompt"], T.CHECK_PROMPT, F.PROMPT],
        "seed": seed,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "fates_run": None if dev else str(FATES_ROWS.parent),
        "problems": errors,
    }
    out = RUNS / run
    (out / "rows.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    if dev:
        print(f"development run: {len(rows)} sentences over {len(chosen)} pairs; no fates")
        return
    result = W.score(rows, fates, [p.name for p in chosen], seed)
    print(json.dumps(result, indent=1))
    (out / "score.json").write_text(json.dumps({"meta": meta, "result": result}, indent=1) + "\n")
    print(f"wrote {out / 'score.json'} and {out / 'rows.json'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("dump", "dump-check", "score"):
        p = sub.add_parser(cmd)
        p.add_argument("--run", required=True)
        if cmd == "dump":
            p.add_argument("--prompt", required=True)
            p.add_argument("--dev", action="store_true")
        if cmd == "score":
            p.add_argument("--model", default="unrecorded")
            p.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    if a.cmd == "dump":
        dump(a.run, a.prompt, a.dev)
    elif a.cmd == "dump-check":
        dump_check(a.run)
    else:
        score(a.run, a.model, a.seed)


if __name__ == "__main__":
    main()
