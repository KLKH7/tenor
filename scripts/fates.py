"""Item fates: what the next meeting does with every decision and action of this one ($0, agents).

    uv run --no-sync python scripts/fates.py dump [--seed 42] [--pairs X__Y,...]
    uv run --no-sync python scripts/fates.py disagreements --run <RUN> [--pairs X__Y,...]
    uv run --no-sync python scripts/fates.py score --run <RUN> [--seed 42] [--model NAME]
        [--pairs X__Y,...]

``dump`` selects 36 consecutive AMI scenario pairs (``tenor.evals.item_fates.select``: 12 per
position, at most 2 per series, without the 6 DEVELOP pairs of ``scripts/cbd.py`` and pairs
whose X has only "NA" placeholder items) and writes
one reader job per pair to ``data/private/reader/fates_jobs/<X>__<Y>.md`` (prompt
``item_fates.v1``, roster, every decision and action of X with its linked passage, Y's full
human transcript) plus ``fates_jobs/pairs.json`` (the selection and its overlap with the earlier
TEST set). Same seed, same bytes. Readers a and b each read one job file and write JSONL to
``data/private/reader/fates_runs/<RUN>/<a|b>/<X>__<Y>.jsonl``.

``disagreements`` prints, as JSON on stdout, the pairs where a and b differ on the fate of at
least one item, and writes a tie-break job per such pair to
``data/private/reader/fates_tiebreak/<RUN>/<X>__<Y>.md``: the same job restricted to the
disputed items, with neither reader's answer. Reader c answers into
``data/private/reader/fates_runs/<RUN>/c/<X>__<Y>.jsonl``.

``score`` takes the fate a and b agree on, else c's, else ``unclear``, and prints the
distribution of fates (overall, by item kind, by pair position; again among items with
needed_followup = yes), silent items per pair, a-b agreement and kappa, and the recall of the
earlier detector (``cbd_agent_runs/cbd_20261110``) on overlapping pairs, with meeting-series
bootstrap CIs. It writes ``score.json`` and ``rows.json`` into the run directory. Local data
only, private outputs: no LLM, no network.

``--pairs X__Y,...`` runs the same steps on the named pairs instead of the seeded 36, for an
extra run under its own run id: ``dump`` writes only those job files (same prompt, same
directory) and leaves the 36 job files and ``pairs.json`` alone; ``disagreements`` and ``score``
must be given the same ``--pairs``. A named pair whose X has no annotated decision or action,
or with a meeting that has no transcript, is reported and left out. Named pairs are not checked
against the DEVELOP set or the series cap: they are outside the test's sample, so report them
apart from it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tenor.ami.corpus import DEFAULT_ZIP, load_meeting
from tenor.evals import came_back as K
from tenor.evals import item_fates as F

READERS = Path("data/private/reader")
JOBS = READERS / "fates_jobs"
RUNS = READERS / "fates_runs"
TIEBREAKS = READERS / "fates_tiebreak"
DETECTOR_RUN = READERS / "cbd_agent_runs" / "cbd_20261110"
TARGET = sum(n for _, n in F.MIX)


def empty_pairs(pairs: list[K.Pair]) -> list[K.Pair]:
    """Pairs whose X has only the annotator's "NA" placeholder as decisions and actions."""
    return [p for p in pairs if not F.items(load_meeting(p.x))]


def selection(seed: int) -> tuple[list[K.Pair], K.Selection, list[K.Pair]]:
    """Every available pair, the earlier split, and this test's pairs."""
    pairs = K.available_pairs(K.summary_counts(DEFAULT_ZIP))
    earlier = K.select(pairs, seed)
    chosen = F.select(pairs, [*earlier.develop, *empty_pairs(pairs)], seed)
    if len(chosen) != TARGET:
        raise SystemExit(f"selected {len(chosen)} pairs, wanted {TARGET}")
    return pairs, earlier, chosen


def named_pairs(spec: str) -> tuple[list[K.Pair], list[str]]:
    """The pairs of ``--pairs`` that can be read, and why each of the others cannot."""
    try:
        wanted = F.parse_pairs(spec)
    except ValueError as e:
        raise SystemExit(f"--pairs: {e}") from e
    counts = K.summary_counts(DEFAULT_ZIP)
    ok: list[K.Pair] = []
    problems: list[str] = []
    for p in wanted:
        missing = [m for m in (p.x, p.y) if m not in counts]
        if missing:
            problems.append(f"{p.name}: no transcript for {', '.join(missing)}")
        elif not F.items(load_meeting(p.x)):
            problems.append(f"{p.name}: {p.x} has no annotated decision or action")
        else:
            ok.append(p)
    return ok, problems


def run_pairs(seed: int, spec: str | None) -> tuple[list[K.Pair], list[str]]:
    """The pairs of a run: the seeded selection, or the readable pairs of ``--pairs``."""
    if spec is None:
        return selection(seed)[2], []
    return named_pairs(spec)


def _prompt() -> str:
    return Path(f"prompts/{F.PROMPT}.md").read_text(encoding="utf-8")


def _answer_path(run: str, reader: str, pair: str) -> str:
    return str(RUNS / run / reader / f"{pair}.jsonl")


def dump_named(seed: int, spec: str) -> None:
    """Job files for the pairs of ``--pairs`` only; nothing else in ``JOBS`` is touched."""
    chosen, problems = named_pairs(spec)
    in_selection = {p.name for p in selection(seed)[2]}
    JOBS.mkdir(parents=True, exist_ok=True)
    prompt = _prompt()
    print("pair\tposition\tdecisions\tactions\tno_passage\ty_lines\tin_selection")
    for p in chosen:
        x, y = load_meeting(p.x), load_meeting(p.y)
        where = _answer_path("<RUN>", "<your reader letter: a or b>", p.name)
        (JOBS / f"{p.name}.md").write_text(F.job_file(x, y, prompt, where), encoding="utf-8")
        its = F.items(x)
        print(
            f"{p.name}\t{p.kind}\t{sum(i.item_id[0] == 'D' for i in its)}\t"
            f"{sum(i.item_id[0] == 'A' for i in its)}\t{sum(not i.linked for i in its)}\t"
            f"{len(y.dialogue_acts)}\t{p.name in in_selection}"
        )
    for e in problems:
        print(f"NOT DUMPED {e}")
    print(f"{len(chosen)} extra job files in {JOBS}; pairs.json and the selection unchanged")


def dump(seed: int) -> None:
    pairs, earlier, chosen = selection(seed)
    JOBS.mkdir(parents=True, exist_ok=True)
    prompt = _prompt()
    earlier_test = {p.name for p in earlier.test}
    print(
        f"{len(pairs)} pairs available; excluded: {len(earlier.develop)} DEVELOP pairs and "
        f'{[p.name for p in empty_pairs(pairs)]} (X has only "NA" placeholder items)'
    )
    print("pair\tposition\tdecisions\tactions\tno_passage\ty_lines\tearlier_test")
    n_items = 0
    for p in chosen:
        x, y = load_meeting(p.x), load_meeting(p.y)
        where = _answer_path("<RUN>", "<your reader letter: a or b>", p.name)
        (JOBS / f"{p.name}.md").write_text(F.job_file(x, y, prompt, where), encoding="utf-8")
        its = F.items(x)
        n_items += len(its)
        print(
            f"{p.name}\t{p.kind}\t{sum(i.item_id[0] == 'D' for i in its)}\t"
            f"{sum(i.item_id[0] == 'A' for i in its)}\t{sum(not i.linked for i in its)}\t"
            f"{len(y.dialogue_acts)}\t{p.name in earlier_test}"
        )
    record = {
        "seed": seed,
        "prompt": F.PROMPT,
        "pairs_available": len(pairs),
        "excluded_develop": [p.name for p in earlier.develop],
        "excluded_no_real_item": [p.name for p in empty_pairs(pairs)],
        "pairs": [p.name for p in chosen],
        "by_position": {k: [p.name for p in chosen if p.kind == k] for k, _ in F.MIX},
        "series": len({p.series for p in chosen}),
        "overlap_earlier_test": [p.name for p in chosen if p.name in earlier_test],
        "items": n_items,
    }
    (JOBS / "pairs.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"overlap with the earlier TEST set: {record['overlap_earlier_test']}")
    print(f"{len(chosen)} job files, {n_items} items, {record['series']} series, in {JOBS}")


def read_run(
    run: str, seed: int, spec: str | None = None
) -> tuple[
    list[K.Pair], dict[str, list[str]], dict[str, dict[str, dict[str, F.Answer]]], list[str]
]:
    """The run's pairs (:func:`run_pairs`), each pair's X item ids,
    ``answers[pair][reader][item]`` for every reader file that exists, and every problem found."""
    if not (RUNS / run).is_dir():
        raise SystemExit(f"no reader outputs for run {run}: {RUNS / run} does not exist")
    chosen, errors = run_pairs(seed, spec)
    ids: dict[str, list[str]] = {}
    answers: dict[str, dict[str, dict[str, F.Answer]]] = {}
    for p in chosen:
        x, y = load_meeting(p.x), load_meeting(p.y)
        ids[p.name] = [i.item_id for i in F.items(x)]
        answers[p.name] = {}
        for r in (*F.READERS, F.TIEBREAK):
            path = Path(_answer_path(run, r, p.name))
            if not path.exists():
                if r in F.READERS:
                    errors.append(f"{p.name}: no answer from reader {r} ({path})")
                continue
            got, bad = F.parse_answers(
                path.read_text(encoding="utf-8"), ids[p.name], len(y.dialogue_acts)
            )
            answers[p.name][r] = got
            errors += [f"{p.name} reader {r}: {e}" for e in bad]
            if r in F.READERS:
                missing = [i for i in ids[p.name] if i not in got]
                if missing:
                    errors.append(f"{p.name} reader {r}: no valid answer for {missing}")
    return chosen, ids, answers, errors


def disagreements(run: str, seed: int, spec: str | None = None) -> None:
    chosen, ids, answers, errors = read_run(run, seed, spec)
    out_dir = TIEBREAKS / run
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.md"):
        stale.unlink()
    prompt = _prompt()
    names = []
    n_items = 0
    for p in chosen:
        got = answers[p.name]
        if any(r not in got for r in F.READERS):
            continue  # reported as a problem; nothing to compare
        open_items = F.disputed(ids[p.name], got["a"], got["b"])
        if not open_items:
            continue
        names.append(p.name)
        n_items += len(open_items)
        body = F.job_file(
            load_meeting(p.x),
            load_meeting(p.y),
            prompt,
            _answer_path(run, F.TIEBREAK, p.name),
            only=open_items,
        )
        (out_dir / f"{p.name}.md").write_text(body, encoding="utf-8")
    print(json.dumps(names))
    print(f"{n_items} disputed items in {len(names)} pairs; jobs in {out_dir}", file=sys.stderr)
    for e in errors:
        print(f"PROBLEM {e}", file=sys.stderr)


def detector_flags(pairs: list[K.Pair]) -> dict[str, list[str]]:
    """pair -> X items the earlier detector output a line for, for the pairs it ran on."""
    out: dict[str, list[str]] = {}
    for p in pairs:
        path = DETECTOR_RUN / f"{p.name}.jsonl"
        if not path.exists():
            continue
        x, y = load_meeting(p.x), load_meeting(p.y)
        claims, _ = K.parse_claims(
            path.read_text(encoding="utf-8"),
            [i.item_id for i in K.items(x)],
            len(y.dialogue_acts),
        )
        out[p.name] = [c.x_item for c in claims]
    return out


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _fmt(r: dict[str, Any]) -> str:
    if r["value"] is None:
        return f"n/a ({r['num']}/{r['den']})"
    ci = f" [{r['ci'][0]:.2f}, {r['ci'][1]:.2f}]" if r["ci"] else ""
    return f"{r['value']:.2f}{ci} ({r['num']}/{r['den']})"


def _print_distribution(label: str, d: dict[str, Any]) -> None:
    print(f"  {label}: {d['items']} items, {d['pairs']} pairs, {d['series']} series")
    for f, r in d["fates"].items():
        if r["num"] or f != F.UNCLEAR:
            print(f"    {f:<20}{_fmt(r)}")


def _print_breakdown(title: str, b: dict[str, Any]) -> None:
    print(f"== {title} ==")
    _print_distribution("overall", b["overall"])
    for k, d in b["by_item_kind"].items():
        _print_distribution(k, d)
    for k, d in b["by_position"].items():
        _print_distribution(f"{k[0]}->{k[1]}", d)


def _print_silent(label: str, s: dict[str, Any]) -> None:
    for cat, v in s.items():
        print(
            f"  {label} {cat:<22}{v['items']} items, mean/pair {v['per_pair_mean']}; "
            f">=1: {_fmt(v['pairs_with_at_least_1'])}; >=2: {_fmt(v['pairs_with_at_least_2'])}"
        )


def score(run: str, seed: int, model: str, spec: str | None = None) -> None:
    chosen, ids, answers, errors = read_run(run, seed, spec)
    rows: list[dict[str, Any]] = []
    for p in chosen:
        if any(r not in answers[p.name] for r in F.READERS):
            continue
        rows += F.item_rows(p, ids[p.name], answers[p.name])
    if not rows:
        raise SystemExit(f"run {run}: no pair has answers from both readers")
    scored = [p for p in chosen if any(r["pair"] == p.name for r in rows)]
    result = F.score(rows, seed, detector_flags(scored))
    print(
        f"{result['items']} items in {result['pairs']} pairs, {result['series']} series; fate "
        f"resolved by {result['fate_resolved_by']}; needed_followup {result['needed_followup']}"
    )
    _print_breakdown("All items: share of each fate", result["all_items"])
    _print_breakdown("Items with needed_followup = yes", result["needed_followup_yes"])
    print("== Silent items per meeting pair ==")
    print("pair\titems\tchanged_silently\tnot_mentioned_needed")
    for p in F.pair_rows(rows):
        print(f"{p['pair']}\t{p['items']}\t{p['changed_silently']}\t{p['not_mentioned_needed']}")
    _print_silent("all pairs", result["silent_per_pair"])
    for k, s in result["silent_per_pair_by_position"].items():
        _print_silent(f"{k[0]}->{k[1]}", s)
    print("== Readers a and b ==")
    for field, a in result["reader_agreement"].items():
        ci = a["kappa_ci"]
        print(
            f"  {field:<16}agreement {_fmt(a['agreement'])}, kappa {a['kappa']}"
            + (f" [{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "")
        )
    det = result["detector_recall"]
    print(f"== Earlier detector (cbd_20261110) on {len(det['pairs'])} overlapping pairs ==")
    print(f"  recall on changed_silently items: {_fmt(det['recall'])}")
    for it in det["changed_silently_items"]:
        print(f"  {it['pair']} {it['x_item']}: {'flagged' if it['flagged'] else 'not flagged'}")
    for it in det["detector_lines"]:
        print(f"  detector line {it['pair']} {it['x_item']}: final fate {it['fate']}")
    for e in errors:
        print(f"PROBLEM {e}")
    meta = {
        "run": run,
        "git_sha": _git_sha(),
        "model": model,
        "prompt_versions": [F.PROMPT],
        "seed": seed,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "pair_source": "seeded selection" if spec is None else "--pairs",
        "pair_names": [p.name for p in chosen],
        "pairs_selected": len(chosen),
        "pairs_scored": len(scored),
        "detector_run": str(DETECTOR_RUN),
        "problems": errors,
    }
    out = RUNS / run
    (out / "score.json").write_text(
        json.dumps({"meta": meta, "result": result}, indent=1) + "\n", encoding="utf-8"
    )
    (out / "rows.json").write_text(
        json.dumps({"items": rows, "pairs": F.pair_rows(rows)}, indent=1, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out / 'score.json'} and {out / 'rows.json'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--seed", type=int, default=42)
    g = sub.add_parser("disagreements")
    g.add_argument("--run", required=True)
    g.add_argument("--seed", type=int, default=42)
    s = sub.add_parser("score")
    s.add_argument("--run", required=True)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--model", default="unrecorded", help="the agents' model, for score.json")
    for cmd in (d, g, s):
        cmd.add_argument(
            "--pairs", help="comma-separated X__Y pairs to use instead of the seeded selection"
        )
    args = ap.parse_args()
    if args.cmd == "dump":
        if args.pairs is None:
            dump(args.seed)
        else:
            dump_named(args.seed, args.pairs)
    elif args.cmd == "disagreements":
        disagreements(args.run, args.seed, args.pairs)
    else:
        score(args.run, args.seed, args.model, args.pairs)


if __name__ == "__main__":
    main()
