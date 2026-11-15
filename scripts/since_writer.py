"""The "since last time" writer on the item-fates pairs ($0, agents).

    uv run --no-sync python scripts/since_writer.py dump --run <RUN> --prompt <NAME>
        [--split test|dev|fresh|fresh2 | --pairs IS1008a-b,IS1008b-c,...] [--model NAME]
    uv run --no-sync python scripts/since_writer.py dump-check --run <RUN> [--model NAME]
    uv run --no-sync python scripts/since_writer.py score --run <RUN> [--model NAME] [--seed 42]

Pairs are the 36 seeded item-fates pairs (``scripts/fates.py``, seed 42; ``--split test``, the
default); ``--split dev`` (or ``--dev``) the 6 development pairs that study excluded, only for
developing a writer prompt; ``--split fresh`` the FRESH set (``item_fates.fresh_split``, up to
30 pairs none of the earlier writers ran on); ``--split fresh2`` the second FRESH set
(``item_fates.fresh2_split``, seed 43: up to 24 pairs used in no earlier run); ``--pairs``
any named available pairs (split ``pairs``, e.g. demo data: not a test set), kept in
``run.json``. Each split is scored against
its own fates run (:data:`FATES_RUNS`); named pairs against whichever fates run of
:data:`PAIR_FATES_RUNS` read each pair first. ``--model`` on ``dump`` records the writer
agents' model and on ``dump-check`` the checkers' model in ``run.json``. Everything is under
``data/private/reader``.

``dump`` writes one writer job per pair (``sw_jobs/<RUN>/writer/<pair>.md``: the prompt
``--prompt``, X's annotated decisions and actions laid out as notes, Y's human transcript) and
records the prompt in ``sw_runs/<RUN>/run.json``. One agent per pair answers into
``sw_runs/<RUN>/writer/<pair>.jsonl``. The fates are in no job file.

``dump-check`` validates the writer's lines mechanically and writes a checker job per pair
with at least one valid line (``sw_jobs/<RUN>/check/<pair>.md``: prompt
``since_last_time_check.v1``); checkers a and b answer into
``sw_runs/<RUN>/check_<a|b>/<pair>.jsonl``.

``score`` joins the lines with the fates of the split's fates run and prints sentences judged
true, silent items covered, pairs with and without a silent item that got a sentence, and
sentences per pair, with intervals that resample teams; it writes ``score.json`` and
``rows.json`` in the run directory.

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
FATES_RUNS = {
    "test": READERS / "fates_runs" / "fates_20261110",
    "dev": READERS / "fates_runs" / "fates_dev_20261114",
    "fresh": READERS / "fates_runs" / "fates_fresh_20261114",
    "fresh2": READERS / "fates_runs" / "fates_fresh2_20261115",
}
SPLITS = tuple(FATES_RUNS)
# Every fates run, searched in this order for the fates of pairs named with --pairs.
PAIR_FATES_RUNS = (*FATES_RUNS.values(), READERS / "fates_runs" / "fates_extra_20261110")
SEED = 42  # the item-fates selection seed


def pairs_of(split: str, names: list[str] | None = None) -> list[K.Pair]:
    """The 36 item-fates pairs (``test``), the 6 development pairs that study excluded
    (``dev``), the FRESH set (``fresh``), the second FRESH set (``fresh2``), or the pairs
    ``names`` (``pairs``)."""
    available = K.available_pairs(K.summary_counts(DEFAULT_ZIP))
    if split == "pairs":
        return W.parse_pairs(",".join(names or []), available)
    earlier = K.select(available, SEED)
    if split == "dev":
        return list(earlier.develop)
    empty = [p for p in available if not F.items(load_meeting(p.x))]
    test = F.select(available, [*earlier.develop, *empty], SEED)
    if split == "test":
        return test
    if split in ("fresh", "fresh2"):
        excluded, _, fresh = F.fresh_split(available, test, earlier.develop, empty, SEED)
        if split == "fresh":
            return fresh
        return F.fresh2_split(available, excluded, fresh, earlier.test, F.FRESH2_SEED)[2]
    raise SystemExit(f"unknown split {split!r}: one of {(*SPLITS, 'pairs')}")


def record_pairs(record: dict[str, Any]) -> list[K.Pair]:
    """The pairs of a recorded run."""
    return pairs_of(split_of(record), record.get("pair_names"))


def pair_fates(chosen: list[K.Pair]) -> tuple[list[dict[str, Any]], list[str]]:
    """The fates rows of named pairs, each pair from the first run of :data:`PAIR_FATES_RUNS`
    that read it, and the runs used."""
    rows: list[dict[str, Any]] = []
    used: list[str] = []
    todo = {p.name for p in chosen}
    for run in PAIR_FATES_RUNS:
        path = run / "rows.json"
        if not todo or not path.exists():
            continue
        items = json.loads(path.read_text(encoding="utf-8"))["items"]
        got = [r for r in items if r["pair"] in todo]
        if got:
            rows += got
            used.append(str(run))
            todo -= {r["pair"] for r in got}
    if todo:
        raise SystemExit(f"no fates for {sorted(todo)}")
    return rows, used


def split_of(record: dict[str, Any]) -> str:
    """A run's split; runs recorded before ``split`` existed have only ``dev``."""
    return str(record.get("split") or ("dev" if record.get("dev") else "test"))


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


def _save_record(run: str, record: dict[str, Any]) -> None:
    (RUNS / run / "run.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")


def dump(
    run: str, prompt_name: str, split: str, model: str | None, names: list[str] | None = None
) -> None:
    prompt = Path(f"prompts/{prompt_name}.md").read_text(encoding="utf-8")
    chosen = pairs_of(split, names)
    out = JOBS / run / "writer"
    out.mkdir(parents=True, exist_ok=True)
    (RUNS / run / "writer").mkdir(parents=True, exist_ok=True)
    for p in chosen:
        x, y, items = W.units(load_meeting(p.x), load_meeting(p.y))
        answer = str(RUNS / run / "writer" / f"{p.name}.jsonl")
        (out / f"{p.name}.md").write_text(
            W.writer_job(x, y, prompt_name, prompt, answer), encoding="utf-8"
        )
        print(f"{p.name}\t{len(items)} items\t{len(y.transcript)} lines")
    record: dict[str, Any] = {
        "run": run,
        "writer_prompt": prompt_name,
        "dev": split == "dev",
        "split": split,
        "pairs": len(chosen),
        "pair_names": [p.name for p in chosen],
        "models": {"writer": model or "unrecorded"},
    }
    _save_record(run, record)
    print(f"{len(chosen)} writer jobs in {out} (prompt {prompt_name})")


def read_writer(run: str, pair: K.Pair) -> tuple[list[dict[str, Any]], list[str]] | None:
    """The validated writer lines of a pair and the problems, or ``None`` with no output."""
    path = RUNS / run / "writer" / f"{pair.name}.jsonl"
    if not path.exists():
        return None
    x, y, _ = W.units(load_meeting(pair.x), load_meeting(pair.y))
    return T.parse_writer(path.read_text(encoding="utf-8"), [b.id for b in x.bullets], y)


def dump_check(run: str, model: str | None) -> None:
    record = _run_record(run)
    if model:
        record.setdefault("models", {}).update({f"checker_{c}": model for c in T.CHECKERS})
        record["checker_prompt"] = T.CHECK_PROMPT
        _save_record(run, record)
    prompt = Path(f"prompts/{T.CHECK_PROMPT}.md").read_text(encoding="utf-8")
    out = JOBS / run / "check"
    out.mkdir(parents=True, exist_ok=True)
    n_jobs = n_lines = 0
    for p in record_pairs(record):
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
    split = split_of(record)
    chosen = record_pairs(record)
    if split == "pairs":
        fates, fates_runs = pair_fates(chosen)
    else:
        fates_rows = FATES_RUNS[split] / "rows.json"
        if not fates_rows.exists():
            raise SystemExit(f"no fates for split {split}: {fates_rows} does not exist")
        fates = json.loads(fates_rows.read_text(encoding="utf-8"))["items"]
        fates_runs = [str(FATES_RUNS[split])]
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
        "models": record.get("models", {}),
        "split": split,
        "prompt_versions": [record["writer_prompt"], T.CHECK_PROMPT, F.PROMPT],
        "seed": seed,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "fates_run": fates_runs[0] if len(fates_runs) == 1 else fates_runs,
        "problems": errors,
    }
    out = RUNS / run
    (out / "rows.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
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
            which = p.add_mutually_exclusive_group()
            which.add_argument("--split", choices=SPLITS, default="test")
            which.add_argument("--dev", action="store_true", help="same as --split dev")
            which.add_argument(
                "--pairs", help="named pairs, comma-separated (IS1008a-b or IS1008a__IS1008b)"
            )
            p.add_argument("--note", help="a note kept in run.json (e.g. why these pairs)")
        if cmd in ("dump", "dump-check"):
            p.add_argument("--model", help="the agents' model, recorded in run.json")
        if cmd == "score":
            p.add_argument("--model", default="unrecorded")
            p.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    if a.cmd == "dump":
        names = a.pairs.split(",") if a.pairs else None
        split = "pairs" if a.pairs else ("dev" if a.dev else a.split)
        dump(a.run, a.prompt, split, a.model, names)
        if a.note:
            record = _run_record(a.run)
            record["note"] = a.note
            _save_record(a.run, record)
    elif a.cmd == "dump-check":
        dump_check(a.run, a.model)
    else:
        score(a.run, a.model, a.seed)


if __name__ == "__main__":
    main()
