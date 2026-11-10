"""Came back different: data for a blind test on AMI's scenario series, and its score ($0, agents).

    uv run --no-sync python scripts/cbd.py dump [--seed 42]
    uv run --no-sync python scripts/cbd.py judge-packets --run <RUN>
    uv run --no-sync python scripts/cbd.py score --run <RUN> [--seed 42] [--model NAME]

``dump`` lists every consecutive meeting pair that qualifies, applies the fixed split of
``tenor.evals.came_back.select`` and writes one detector job per selected pair to
``data/private/reader/cbd_jobs/<X>__<Y>.md`` (prompt ``came_back_different.v2``, roster, X's
human decisions and actions with their linked passage, Y's full human transcript) plus
``cbd_jobs/pairs.json`` (the split). Same seed, same bytes. A detector agent reads one job file
and writes JSONL (possibly empty) to ``data/private/reader/cbd_agent_runs/<RUN>/<X>__<Y>.jsonl``.

``judge-packets`` validates each detector file and writes one blind packet per pair with at
least one claim to ``data/private/reader/cbd_packets/<RUN>/<X>__<Y>.md`` (prompt
``came_back_judge.v1``; no detector confidence), and ``claims.json`` (the key) into the run
directory. Three judge agents, each given only packets, answer into
``data/private/reader/runs/cbd_judge_{a,b,c}/<X>__<Y>.jsonl``. Those directories are not per
run: clear or move them before judging another run (``score`` refuses verdicts for claim ids
the run does not have).

``score`` takes the majority of the three verdicts per claim and prints, per group (develop,
test, known), lines shown per pair, the share judged ``true_silent_drop``, the share of pairs
with at least one, and the same by detector confidence, with meeting-series bootstrap CIs. It
writes ``score.json`` and ``rows.json`` into the run directory. Local data only, private
outputs: no LLM, no network.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tenor.ami.corpus import DEFAULT_ZIP, AmiMeeting, load_meeting
from tenor.evals import came_back as K

READERS = Path("data/private/reader")
JOBS = READERS / "cbd_jobs"
RUNS = READERS / "cbd_agent_runs"
PACKETS = READERS / "cbd_packets"
JUDGE_DIRS = {j: READERS / "runs" / f"cbd_judge_{j}" for j in K.JUDGES}


def selection(seed: int) -> tuple[list[K.Pair], K.Selection]:
    pairs = K.available_pairs(K.summary_counts(DEFAULT_ZIP))
    return pairs, K.select(pairs, seed)


def _prompt(name: str) -> str:
    return Path(f"prompts/{name}.md").read_text(encoding="utf-8")


def dump(seed: int) -> None:
    pairs, sel = selection(seed)
    kinds = Counter((p.parity, p.kind) for p in pairs)
    print(f"{len(pairs)} consecutive pairs with a transcript on both sides and >= 1 decision or")
    print("action in X's human summary; by series parity and kind:")
    for (parity, kind), n in sorted(kinds.items()):
        print(f"  {parity}\t{kind[0]}->{kind[1]}\t{n}")
    JOBS.mkdir(parents=True, exist_ok=True)
    prompt = _prompt(K.PROMPT)
    loaded: dict[str, AmiMeeting] = {}
    print("group\tpair\tx_items\tx_items_no_passage\ty_lines\tsame_people")
    for p in sel.all_pairs():
        x, y = (loaded.setdefault(m, load_meeting(m)) for m in (p.x, p.y))
        (JOBS / f"{p.name}.md").write_text(K.job_file(x, y, prompt), encoding="utf-8")
        its = K.items(x)
        same = {(s.role, s.participant) for s in x.speakers} == {
            (s.role, s.participant) for s in y.speakers
        }
        print(
            f"{'+'.join(sel.groups(p))}\t{p.name}\t{len(its)}\t"
            f"{sum(not i.linked for i in its)}\t{len(y.dialogue_acts)}\t{same}"
        )
    split = {
        "seed": seed,
        "prompt": K.PROMPT,
        "pairs_available": len(pairs),
        "develop": [p.name for p in sel.develop],
        "test": [p.name for p in sel.test],
        "known": [p.name for p in sel.known],
        "known_outside_test": [p.name for p in sel.known_extra],
    }
    (JOBS / "pairs.json").write_text(json.dumps(split, indent=1) + "\n", encoding="utf-8")
    for g in ("develop", "test", "known"):
        print(f"{g.upper()}:\n  " + "\n  ".join(split[g]))
    print(f"{len(sel.all_pairs())} job files in {JOBS}")


def read_claims(run: str, seed: int) -> tuple[K.Selection, dict[str, list[K.Claim]], list[str]]:
    """Each selected pair's valid detector lines (pairs with no output file left out), and
    every problem found."""
    _, sel = selection(seed)
    out: dict[str, list[K.Claim]] = {}
    errors: list[str] = []
    for p in sel.all_pairs():
        path = RUNS / run / f"{p.name}.jsonl"
        if not path.exists():
            errors.append(f"{p.name}: no detector output ({path})")
            continue
        x, y = load_meeting(p.x), load_meeting(p.y)
        ids = [i.item_id for i in K.items(x)]
        claims, bad = K.parse_claims(path.read_text(encoding="utf-8"), ids, len(y.dialogue_acts))
        out[p.name] = claims
        errors += [f"{p.name}: {e}" for e in bad]
    return sel, out, errors


def judge_packets(run: str, seed: int) -> None:
    sel, claims, errors = read_claims(run, seed)
    out_dir = PACKETS / run
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt = _prompt(K.JUDGE_PROMPT)
    key: dict[str, Any] = {}
    print("pair\tclaims")
    for p in sel.all_pairs():
        mine = claims.get(p.name)
        if mine is None:
            continue
        print(f"{p.name}\t{len(mine)}")
        for k, c in enumerate(mine, start=1):
            key[K.claim_id(p.name, k)] = {"pair": p.name, **c.model_dump(mode="json")}
        if mine:
            body = K.judge_packet(
                load_meeting(p.x),
                load_meeting(p.y),
                mine,
                prompt,
                [str(d) for d in JUDGE_DIRS.values()],
            )
            (out_dir / f"{p.name}.md").write_text(body, encoding="utf-8")
    (RUNS / run / "claims.json").write_text(
        json.dumps(key, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(key)} claims in {sum(bool(v) for v in claims.values())} packets in {out_dir}")
    for e in errors:
        print(f"PROBLEM {e}")


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _fmt(r: dict[str, Any], pct: bool = True) -> str:
    if r["value"] is None:
        return f"n/a ({r['num']}/{r['den']})"
    ci = f" [{r['ci'][0]:.2f}, {r['ci'][1]:.2f}]" if r["ci"] else ""
    return f"{r['value']:.2f}{ci} ({r['num']}/{r['den']})"


def score(run: str, seed: int, model: str) -> None:
    if not (RUNS / run).is_dir():
        raise SystemExit(f"no detector outputs for run {run}: {RUNS / run} does not exist")
    sel, claims, errors = read_claims(run, seed)
    known_ids = {K.claim_id(p, k) for p, cs in claims.items() for k in range(1, len(cs) + 1)}
    votes: dict[str, dict[str, str]] = {}
    for j, d in JUDGE_DIRS.items():
        for path in sorted(d.glob("*.jsonl")):
            got, bad = K.parse_verdicts(path.read_text(encoding="utf-8"))
            errors += [f"judge {j} {path.name}: {e}" for e in bad]
            stray = sorted(set(got) - known_ids)
            if stray:
                raise SystemExit(
                    f"judge {j} {path.name}: verdicts for claims not in run {run}: {stray}"
                )
            for cid, v in got.items():
                votes.setdefault(cid, {})[j] = v
    unjudged = sorted(c for c in known_ids if len(votes.get(c, {})) < len(K.JUDGES))
    if unjudged:
        raise SystemExit(f"{len(unjudged)} claims lack three verdicts: {unjudged}")
    rows: list[dict[str, Any]] = []
    detail: list[dict[str, Any]] = []
    agree = Counter[int]()
    for p in sel.all_pairs():
        if p.name not in claims:
            continue
        judged = []
        for k, c in enumerate(claims[p.name], start=1):
            cid = K.claim_id(p.name, k)
            v = [votes[cid][j] for j in K.JUDGES]
            agree[max(Counter(v).values())] += 1
            judged.append((c.confidence, K.majority(v)))
            detail.append(
                {
                    "claim_id": cid,
                    "x_item": c.x_item,
                    "confidence": c.confidence,
                    "votes": v,
                    "verdict": judged[-1][1],
                    "line": c.line_for_user,
                }
            )
        rows.append(K.pair_row(p, sel.groups(p), judged))
    result = K.score(rows, seed)
    for g, s in result.items():
        print(f"== {g.upper()}: {s['pairs']} pairs, {s['series']} series ==")
        print(f"  lines shown per pair          {_fmt(s['lines_per_pair'])}")
        print(f"  share true_silent_drop        {_fmt(s['share_true_silent_drop'])}")
        print(f"  share of pairs with >= 1 true {_fmt(s['share_pairs_with_true'])}")
        print(f"  share changed_openly          {_fmt(s['share_changed_openly'])}")
        print("  verdicts: " + ", ".join(f"{v} {n}" for v, n in s["verdicts"].items()))
        for c, r in s["by_confidence"].items():
            print(f"  confidence {c:<6} true        {_fmt(r)}")
    print("lines in the known pairs:")
    for row in detail:
        if row["claim_id"].split("#")[0] in K.KNOWN:
            print(
                f"  {row['claim_id']} {row['x_item']} {row['verdict']} {row['votes']}: "
                f"{row['line']}"
            )
    print(f"judge agreement (claims by size of the largest bloc): {dict(sorted(agree.items()))}")
    for e in errors:
        print(f"PROBLEM {e}")
    meta = {
        "run": run,
        "git_sha": _git_sha(),
        "model": model,
        "prompt_versions": [K.PROMPT, K.JUDGE_PROMPT],
        "seed": seed,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "pairs_scored": len(rows),
        "problems": errors,
        "judge_bloc_sizes": dict(sorted(agree.items())),
    }
    out = RUNS / run
    (out / "score.json").write_text(
        json.dumps({"meta": meta, "groups": result}, indent=1) + "\n", encoding="utf-8"
    )
    (out / "rows.json").write_text(
        json.dumps({"pairs": rows, "claims": detail}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out / 'score.json'} and {out / 'rows.json'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--seed", type=int, default=42)
    j = sub.add_parser("judge-packets")
    j.add_argument("--run", required=True)
    j.add_argument("--seed", type=int, default=42)
    s = sub.add_parser("score")
    s.add_argument("--run", required=True)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--model", default="unrecorded", help="the agents' model, for score.json")
    args = ap.parse_args()
    if args.cmd == "dump":
        dump(args.seed)
    elif args.cmd == "judge-packets":
        judge_packets(args.run, args.seed)
    else:
        score(args.run, args.seed, args.model)


if __name__ == "__main__":
    main()
