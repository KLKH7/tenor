"""Meeting memory, phase 1: a running record for one AMI series, built one meeting at a time by
agents, checked by code, then used to answer questions.

    uv run --no-sync python scripts/meeting_memory.py seed --chain main|transcript_only
    uv run --no-sync python scripts/meeting_memory.py update-job --chain C --meeting IS1008b
    uv run --no-sync python scripts/meeting_memory.py check-update --chain C --meeting IS1008b
    uv run --no-sync python scripts/meeting_memory.py check --chain C
    uv run --no-sync python scripts/meeting_memory.py lines --log P --meeting M --lines L0010-L0020
    uv run --no-sync python scripts/meeting_memory.py search --log P --meeting M --words "a b"
    uv run --no-sync python scripts/meeting_memory.py qa-jobs --run R [--chain transcript_only]
    uv run --no-sync python scripts/meeting_memory.py packets|tiebreak|score --run R [--seed 42]
    uv run --no-sync python scripts/meeting_memory.py match-job --chain C
    uv run --no-sync python scripts/meeting_memory.py fates --chain C [--readers a,b]
    uv run --no-sync python scripts/meeting_memory.py costs --chain C

Two chains over the same meetings, under ``data/private/memory/<series>/``:

* ``main``: the first meeting is seeded from the annotator's decisions and actions; each later
  meeting is folded in by an agent with ``memory_update.v1``;
* ``transcript_only`` (``transcript_only/``): every meeting, the first included, is folded in by
  an agent from the transcript alone, starting from an empty record. The question comparison
  uses this chain: the AMI questions about the first meeting are built from the same
  annotations the ``main`` seed copies.

An agent writes ``updates/<meeting>.json`` and runs ``check-update``, which validates it, folds
it in and writes ``record_after_<meeting>.json``. Local data only; no network, no API.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tenor.ami.corpus import AmiMeeting, load_meeting
from tenor.ami.questions import AmiQuestion, load_questions
from tenor.evals import came_back as K
from tenor.evals import item_fates as F
from tenor.evals import meeting_memory as MM
from tenor.evals import meeting_memory_qa as Q
from tenor.memory import series_check as C
from tenor.memory import series_record as R

ROOT = Path("data/private/memory")
UPDATE_PROMPT = "memory_update.v1"
CHAINS = ("main", "transcript_only")
FATES_RUNS = (
    Path("data/private/reader/fates_runs/fates_20261110"),
    Path("data/private/reader/fates_runs/fates_extra_20261110"),
)
VERDICTS = ("correct", "partially_correct", "incorrect")
SEARCH_MAX = 20
CMD = "uv run --no-sync python scripts/meeting_memory.py"


def meetings(series: str) -> list[str]:
    return [series + x for x in "abcd"]


def chain_dir(series: str, chain: str) -> Path:
    return ROOT / series if chain == "main" else ROOT / series / chain


def record_path(series: str, chain: str, after: str | None) -> Path:
    return chain_dir(series, chain) / f"record_after_{after or 'start'}.json"


def load_record(path: Path) -> R.SeriesRecord:
    return R.SeriesRecord.model_validate_json(path.read_text(encoding="utf-8"))


_MEETINGS: dict[str, AmiMeeting] = {}


def meeting(mid: str) -> AmiMeeting:
    if mid not in _MEETINGS:
        _MEETINGS[mid] = load_meeting(mid)
    return _MEETINGS[mid]


def previous(series: str, mid: str) -> str | None:
    ms = meetings(series)
    i = ms.index(mid)
    return ms[i - 1] if i else None


def git_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return out.stdout.strip()


# -- building the record ---------------------------------------------------------------------


def cmd_seed(series: str, chain: str) -> None:
    out = chain_dir(series, chain)
    out.mkdir(parents=True, exist_ok=True)
    if chain == "main":
        first = meetings(series)[0]
        rec = MM.seed_record(series, meeting(first))
        problems = C.record_problems(rec, {first: MM.transcript(meeting(first))})
        if problems:
            raise SystemExit("\n".join(problems))
        path = record_path(series, chain, first)
    else:
        rec = R.SeriesRecord(series=series)
        path = record_path(series, chain, None)
    path.write_text(R.dump(rec), encoding="utf-8")
    print(f"{len(rec.entries)} entries -> {path}")


def update_job(series: str, chain: str, mid: str) -> str:
    prev = load_record(record_path(series, chain, previous(series, mid)))
    m = meeting(mid)
    base = chain_dir(series, chain)
    update = base / "updates" / f"{mid}.json"
    check = f"{CMD} check-update --chain {chain} --meeting {mid}"
    prompt = Path(f"prompts/{UPDATE_PROMPT}.md").read_text(encoding="utf-8").strip()
    parts = [
        f"# Record update job: {series}, fold in {mid}",
        f"Prompt `{UPDATE_PROMPT}`. Write the update to `{update}`. Then run, from the "
        f"repository root, `{check}` and fix the update until it passes.",
        prompt,
        "---",
        "## The record before this meeting\n\n" + R.render(prev),
        f"## Meeting {mid}: full human transcript\n\nRoster: {MM.roster_line(m)}\n\n```\n"
        + MM.transcript_text(m)
        + "\n```",
    ]
    return "\n\n".join(parts) + "\n"


def cmd_update_job(series: str, chain: str, mid: str) -> None:
    out = chain_dir(series, chain) / "jobs"
    out.mkdir(parents=True, exist_ok=True)
    (chain_dir(series, chain) / "updates").mkdir(exist_ok=True)
    text = update_job(series, chain, mid)
    path = out / f"update_{mid}.md"
    path.write_text(text, encoding="utf-8")
    print(f"{path}: {len(text)} chars, ~{Q.tokens(len(text))} tokens")


def transcripts_for(rec: R.SeriesRecord) -> dict[str, R.Transcript]:
    return {m: MM.transcript(meeting(m)) for m in rec.meetings}


def cmd_check_update(series: str, chain: str, mid: str) -> None:
    prev = load_record(record_path(series, chain, previous(series, mid)))
    raw = (chain_dir(series, chain) / "updates" / f"{mid}.json").read_text(encoding="utf-8")
    try:
        update = R.Update.model_validate_json(raw)
    except ValueError as e:
        raise SystemExit(f"PROBLEM: the update is not valid JSON of the right shape:\n{e}") from e
    if update.meeting != mid:
        raise SystemExit(f"PROBLEM: update.meeting is {update.meeting!r}, expected {mid!r}")
    problems = C.update_problems(prev, update, MM.transcript(meeting(mid)))
    if problems:
        raise SystemExit("\n".join(f"PROBLEM: {p}" for p in problems))
    rec = R.apply_update(prev, update, UPDATE_PROMPT)
    problems = C.record_problems(rec, transcripts_for(rec))
    if problems:
        raise SystemExit("\n".join(f"PROBLEM (record): {p}" for p in problems))
    path = record_path(series, chain, mid)
    path.write_text(R.dump(rec), encoding="utf-8")
    kinds: dict[str, int] = {}
    for ev in update.events:
        kinds[ev.kind] = kinds.get(ev.kind, 0) + 1
    print(f"OK: {len(update.events)} events {kinds}, {len(update.new_entries)} new -> {path}")


def cmd_check(series: str, chain: str) -> None:
    for mid in meetings(series):
        path = record_path(series, chain, mid)
        if not path.exists():
            print(f"{mid}: no record")
            continue
        rec = load_record(path)
        problems = C.record_problems(rec, transcripts_for(rec))
        refs = sum(len(ev.evidence) for e in rec.entries for ev in e.history)
        status: dict[str, int] = {}
        for e in rec.entries:
            status[e.status] = status.get(e.status, 0) + 1
        print(
            f"{mid}: {len(rec.entries)} entries, {refs} pointers, statuses {status}, "
            f"{len(problems)} problems, render ~{Q.tokens(len(R.render(rec)))} tokens"
        )
        for p in problems:
            print("  ", p)


# -- the lookup tool for the record condition -------------------------------------------------


def _log(path: Path, lk: Q.Lookup) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(lk.model_dump_json() + "\n")


def _indices(spec: str, n: int) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        lo, _, hi = part.strip().partition("-")
        a = int(lo.strip().lstrip("L"))
        b = int(hi.strip().lstrip("L")) if hi else a
        out += [i for i in range(a, b + 1) if 0 <= i < n]
    return sorted(set(out))


def cmd_lines(log: Path, mid: str, spec: str) -> None:
    m = meeting(mid)
    text = "\n".join(K.line(m, i) for i in _indices(spec, len(m.dialogue_acts)))
    print(text)
    n = text.count("\n") + 1 if text else 0
    _log(
        log,
        Q.Lookup(
            command="lines", meeting=mid, arg=spec, lines_returned=n, chars_returned=len(text)
        ),
    )


def cmd_search(log: Path, mid: str, query: str) -> None:
    m = meeting(mid)
    want = C.words(query)
    hits = [
        i for i, a in enumerate(m.dialogue_acts) if want and all(w in C.words(a.text) for w in want)
    ][:SEARCH_MAX]
    text = "\n".join(K.line(m, i) for i in hits) or "(no line has all of these words)"
    print(text)
    _log(
        log,
        Q.Lookup(
            command="search",
            meeting=mid,
            arg=query,
            lines_returned=len(hits),
            chars_returned=len(text),
        ),
    )


# -- the question comparison ------------------------------------------------------------------


def qa_dir(series: str, run: str) -> Path:
    return ROOT / series / "qa" / run


def series_questions(series: str) -> list[AmiQuestion]:
    """The development-split AMI questions about the series, in id order."""
    return sorted(
        (
            q
            for q in load_questions()
            if q.split == "dev" and all(m.startswith(series) for m in q.meetings)
        ),
        key=lambda q: q.id,
    )


def _condition_block(template: str, condition: str) -> str:
    head, *blocks = template.split("\n--- condition: ")
    block = next(b.split("\n", 1)[1].strip() for b in blocks if b.startswith(condition + "\n"))
    return head.replace("{condition}", block).strip()


def qa_job(series: str, run: str, q: AmiQuestion, condition: str, record_text: str) -> str:
    base = qa_dir(series, run)
    name = f"{q.id}__{condition}"
    answer = base / "answers" / f"{name}.json"
    template = Path(f"prompts/{Q.PROMPT}.md").read_text(encoding="utf-8")
    parts = [
        f"# Answer job {name}",
        f"Prompt `{Q.PROMPT}`, condition `{condition}`. Write your answer to `{answer}`.",
        _condition_block(template, condition),
    ]
    if condition == "record":
        log = base / "lookups" / f"{name}.jsonl"
        parts.append(
            "## Lookup commands (run from the repository root; each call is logged)\n\n"
            f"- open lines: `{CMD} lines --log {log} --meeting <meeting id> --lines L0120-L0130`"
            " (single ids and comma-separated ranges also work)\n"
            f"- search one meeting: `{CMD} search --log {log} --meeting <meeting id> --words "
            f'"<words>"` (lines that contain all the words, at most {SEARCH_MAX})'
        )
        parts += ["---", record_text]
    else:
        for mid in q.meetings:
            m = meeting(mid)
            parts.append(
                f"## Meeting {mid}: full human transcript\n\nRoster: {MM.roster_line(m)}\n\n"
                "```\n" + MM.transcript_text(m) + "\n```"
            )
    parts.append(f"## Question ({q.id})\n\n{q.question}")
    return "\n\n".join(parts) + "\n"


def cmd_qa_jobs(series: str, run: str, chain: str) -> None:
    rec = load_record(record_path(series, chain, meetings(series)[-1]))
    record_text = R.render(rec)
    base = qa_dir(series, run)
    (base / "jobs").mkdir(parents=True, exist_ok=True)
    (base / "answers").mkdir(exist_ok=True)
    qs = series_questions(series)
    for q in qs:
        for c in Q.CONDITIONS:
            text = qa_job(series, run, q, c, record_text)
            (base / "jobs" / f"{q.id}__{c}.md").write_text(text, encoding="utf-8")
    meta = {
        "run": run,
        "series": series,
        "chain": chain,
        "record": str(record_path(series, chain, meetings(series)[-1])),
        "prompt": Q.PROMPT,
        "update_prompt": UPDATE_PROMPT,
        "questions": [q.id for q in qs],
        "git": git_sha(),
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (base / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"{len(qs)} questions x {len(Q.CONDITIONS)} conditions -> {base / 'jobs'}")


def load_answers(series: str, run: str) -> dict[tuple[str, str], Q.Answer]:
    out = {}
    for path in sorted((qa_dir(series, run) / "answers").glob("*.json")):
        a = Q.Answer.model_validate_json(path.read_text(encoding="utf-8"))
        if path.stem != f"{a.qid}__{a.condition}":
            raise ValueError(f"{path.name} holds {a.qid} {a.condition}")
        out[(a.qid, a.condition)] = a
    return out


def cmd_packets(series: str, run: str, seed: int) -> None:
    base = qa_dir(series, run)
    answers = load_answers(series, run)
    qs = {q.id: q for q in series_questions(series)}
    qids = sorted({q for q, _ in answers})
    missing = [(q, c) for q in qids for c in Q.CONDITIONS if (q, c) not in answers]
    if missing:
        raise SystemExit(f"answers missing: {missing}")
    key = Q.packet_key(qids, seed)
    prompt = Path(f"prompts/{Q.READER_PROMPT}.md").read_text(encoding="utf-8").strip()
    groups: dict[tuple[str, ...], list[str]] = {}
    for qid in qids:
        groups.setdefault(tuple(qs[qid].meetings), []).append(qid)
    out = base / "packets"
    out.mkdir(parents=True, exist_ok=True)
    for ms, ids in sorted(groups.items()):
        name = "__".join(ms)
        rows = [(q, qs[q].question, qs[q].gold_answer) for q in ids]
        transcripts = [
            f"## Meeting {mid}: full human transcript\n\nRoster: {MM.roster_line(meeting(mid))}"
            f"\n\n```\n{MM.transcript_text(meeting(mid))}\n```"
            for mid in ms
        ]
        body = "\n\n".join(
            [
                f"# Reader job {name}",
                f"Prompt `{Q.READER_PROMPT}`. This job has two answers per question, labelled X "
                f"and Y (there is no Z). Grade all {len(ids) * 2} answers below. Write your JSONL "
                "to the answer file you were given. Use only this file.",
                prompt,
                "---",
                *transcripts,
                "## Questions and answers",
                Q.packet(rows, answers, key),
            ]
        )
        (out / f"{name}.md").write_text(body, encoding="utf-8")
        print(f"{name}: {len(ids)} questions")
    meta = {"run": run, "seed": seed, "reader_prompt": Q.READER_PROMPT, "key": key}
    (base / "key.json").write_text(json.dumps(meta, indent=1) + "\n")


def reader_rows(series: str, run: str, reader: str) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for path in sorted((qa_dir(series, run) / "readers" / reader).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d["verdict"] not in VERDICTS:
                raise ValueError(f"{path.name}: verdict {d['verdict']!r}")
            out[(d["qid"], d["label"])] = d["verdict"]
    return out


def cmd_tiebreak(series: str, run: str) -> None:
    a, b = reader_rows(series, run, "a"), reader_rows(series, run, "b")
    split = sorted(k for k in a if b.get(k) != a[k])
    path = qa_dir(series, run) / "tiebreak.json"
    path.write_text(json.dumps([list(k) for k in split], indent=1) + "\n")
    print(f"readers agree on {len(a) - len(split)} of {len(a)}; {len(split)} for the tie-break")


def costs_of(series: str, run: str) -> dict[tuple[str, str], dict[str, int]]:
    base = qa_dir(series, run)
    out = {}
    for path in sorted((base / "jobs").glob("*.md")):
        qid, cond = path.stem.split("__")
        log = base / "lookups" / f"{path.stem}.jsonl"
        lookups = (
            [Q.Lookup.model_validate_json(x) for x in log.read_text().splitlines() if x.strip()]
            if log.exists()
            else []
        )
        out[(qid, cond)] = Q.read_cost(len(path.read_text(encoding="utf-8")), lookups)
    return out


def cmd_score(series: str, run: str) -> None:
    base = qa_dir(series, run)
    answers = load_answers(series, run)
    key = json.loads((base / "key.json").read_text())["key"]
    a, b = reader_rows(series, run, "a"), reader_rows(series, run, "b")
    c = reader_rows(series, run, "c") if (base / "readers" / "c").exists() else {}
    settled, agree = Q.settle(a, b, c)
    verdicts = {(q, key[q][label]): v for (q, label), v in settled.items()}
    costs = costs_of(series, run)
    qids = sorted({q for q, _ in answers})
    result: dict[str, Any] = Q.score(qids, verdicts, costs, answers)
    every = sum(len(MM.transcript_text(meeting(m))) for m in meetings(series))
    meta = json.loads((base / "meta.json").read_text())
    result["meta"] = {
        **meta,
        "reader_prompt": Q.READER_PROMPT,
        "reader_agreement": {"agree": agree, "of": len(a), "tiebreaks": len(a) - agree},
        "scored_git": git_sha(),
        "scored": datetime.now(UTC).isoformat(timespec="seconds"),
        "all_transcripts_tokens": Q.tokens(every),
        "chars_per_token": Q.CHARS_PER_TOKEN,
    }
    result["verdicts"] = {f"{q}|{cnd}": v for (q, cnd), v in sorted(verdicts.items())}
    (base / "score.json").write_text(json.dumps(result, indent=1) + "\n")
    for cnd, s in result["conditions"].items():
        print(f"{cnd:7} {json.dumps(s)}")
    print(json.dumps(result["paired"]), json.dumps(result["meta"]["reader_agreement"]))


# -- agreement with item fates and costs ------------------------------------------------------


def fates_labels(series: str) -> dict[str, dict[str, str]]:
    """pair name -> item id -> final item-fates label, for the series' pairs."""
    out: dict[str, dict[str, str]] = {}
    for run in FATES_RUNS:
        for row in json.loads((run / "rows.json").read_text())["items"]:
            if row["pair"].startswith(series):
                out.setdefault(row["pair"], {})[row["x_item"]] = row["fate"]
    return out


def labelled_pairs(series: str) -> list[tuple[str, str, list[K.Item], dict[str, str]]]:
    ms = meetings(series)
    labels = fates_labels(series)
    return [
        (x, y, F.items(meeting(x)), labels[f"{x}__{y}"])
        for x, y in itertools.pairwise(ms)
        if f"{x}__{y}" in labels
    ]


def to_match(series: str, chain: str) -> list[str]:
    """The earlier meetings of labelled pairs whose entries were not seeded from annotations."""
    rec = load_record(record_path(series, chain, meetings(series)[-1]))
    seeded = {e.introduced_in for e in rec.entries if e.annotation_id}
    return [x for x, _, _, _ in labelled_pairs(series) if x not in seeded]


def cmd_match_job(series: str, chain: str) -> None:
    rec = load_record(record_path(series, chain, meetings(series)[-1]))
    out = chain_dir(series, chain) / "match"
    out.mkdir(parents=True, exist_ok=True)
    prompt = Path(f"prompts/{MM.MATCH_PROMPT}.md").read_text(encoding="utf-8").strip()
    body = "\n\n".join(
        [
            f"# Item match job: {series} ({chain})",
            f"Prompt `{MM.MATCH_PROMPT}`. Write your JSONL to the answer file you were given. "
            "Use only this file.",
            prompt,
            "---",
            *(MM.match_section(meeting(x), rec.entries) for x in to_match(series, chain)),
        ]
    )
    (out / "job.md").write_text(body + "\n", encoding="utf-8")
    print(f"{out / 'job.md'}: meetings {to_match(series, chain)}")


def load_matches(series: str, chain: str, reader: str) -> dict[str, str | None]:
    rec = load_record(record_path(series, chain, meetings(series)[-1]))
    xs = to_match(series, chain)
    expected = {x: [it.item_id for it in F.items(meeting(x))] for x in xs}
    entries = {x: [e.entry_id for e in rec.entries if e.introduced_in == x] for x in xs}
    path = chain_dir(series, chain) / "match" / f"{reader}.jsonl"
    got, errors = MM.parse_matches(path.read_text(encoding="utf-8"), expected, entries)
    if errors:
        raise SystemExit("\n".join(f"{path}: {e}" for e in errors))
    return got


def cmd_fates(series: str, chain: str, readers: list[str]) -> None:
    rec = load_record(record_path(series, chain, meetings(series)[-1]))
    pairs = labelled_pairs(series)
    results: dict[str, Any] = {"proximity": MM.fates_agreement(pairs, rec)}
    maps = {r: load_matches(series, chain, r) for r in readers}
    for r, mapping in maps.items():
        results[f"reader_{r}"] = MM.fates_agreement(pairs, rec, mapping)
    if len(maps) == 2:
        a, b = maps.values()
        results["reader_mapping_agreement"] = {
            "agree": sum(a[k] == b.get(k) for k in a),
            "of": len(a),
            "split": sorted(k for k in a if a[k] != b.get(k)),
        }
    results["meta"] = {
        "chain": chain,
        "record": str(record_path(series, chain, meetings(series)[-1])),
        "fates_runs": [str(r) for r in FATES_RUNS],
        "match_pad": MM.MATCH_PAD,
        "match_prompt": MM.MATCH_PROMPT,
        "git": git_sha(),
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path = chain_dir(series, chain) / "fates_agreement.json"
    path.write_text(json.dumps(results, indent=1) + "\n")
    for name, res in results.items():
        if name in ("meta", "reader_mapping_agreement"):
            print(name, json.dumps(res))
            continue
        print(
            f"{name}: {res['items']} items, {res['matched']} matched, exact {res['exact']}, "
            f"coarse {res['coarse']}, unmatched {res['unmatched']}"
        )
        for row in res["rows"]:
            print(
                f"  {row['pair']} {row['item']}: fate {row['fate']} | {row['entry']} "
                f"{row['record_event']}"
            )


def cmd_costs(series: str, chain: str) -> None:
    rows = []
    for mid in meetings(series):
        job = chain_dir(series, chain) / "jobs" / f"update_{mid}.md"
        rec_path = record_path(series, chain, mid)
        if not rec_path.exists():
            continue
        rec = load_record(rec_path)
        rows.append(
            {
                "after": mid,
                "update_job_tokens": Q.tokens(len(job.read_text())) if job.exists() else None,
                "transcript_tokens": Q.tokens(len(MM.transcript_text(meeting(mid)))),
                "record_tokens_after": Q.tokens(len(R.render(rec))),
                "entries_after": len(rec.entries),
            }
        )
    path = chain_dir(series, chain) / "costs.json"
    path.write_text(json.dumps(rows, indent=1) + "\n")
    for r in rows:
        print(json.dumps(r))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default="IS1008")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("seed", "check", "fates", "costs", "match-job"):
        p = sub.add_parser(name)
        p.add_argument("--chain", choices=CHAINS, required=True)
        if name == "fates":
            p.add_argument("--readers", default="")
    for name in ("update-job", "check-update"):
        p = sub.add_parser(name)
        p.add_argument("--chain", choices=CHAINS, required=True)
        p.add_argument("--meeting", required=True)
    p = sub.add_parser("lines")
    p.add_argument("--log", type=Path, required=True)
    p.add_argument("--meeting", required=True)
    p.add_argument("--lines", required=True)
    p = sub.add_parser("search")
    p.add_argument("--log", type=Path, required=True)
    p.add_argument("--meeting", required=True)
    p.add_argument("--words", required=True)
    p = sub.add_parser("qa-jobs")
    p.add_argument("--run", required=True)
    p.add_argument("--chain", choices=CHAINS, default="transcript_only")
    for name in ("packets", "tiebreak", "score"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        if name == "packets":
            p.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    s = a.series
    if a.cmd == "seed":
        cmd_seed(s, a.chain)
    elif a.cmd == "update-job":
        cmd_update_job(s, a.chain, a.meeting)
    elif a.cmd == "check-update":
        cmd_check_update(s, a.chain, a.meeting)
    elif a.cmd == "check":
        cmd_check(s, a.chain)
    elif a.cmd == "lines":
        cmd_lines(a.log, a.meeting, a.lines)
    elif a.cmd == "search":
        cmd_search(a.log, a.meeting, a.words)
    elif a.cmd == "qa-jobs":
        cmd_qa_jobs(s, a.run, a.chain)
    elif a.cmd == "packets":
        cmd_packets(s, a.run, a.seed)
    elif a.cmd == "tiebreak":
        cmd_tiebreak(s, a.run)
    elif a.cmd == "score":
        cmd_score(s, a.run)
    elif a.cmd == "fates":
        cmd_fates(s, a.chain, [r for r in a.readers.split(",") if r])
    elif a.cmd == "match-job":
        cmd_match_job(s, a.chain)
    else:
        cmd_costs(s, a.chain)
