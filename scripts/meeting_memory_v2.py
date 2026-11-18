"""Meeting memory, phase 2: the v2 record (parts, issues, compact view, quiet-change check)
built for whole AMI series by agents, checked by code, then compared on questions with raw
transcripts.

Building a series' record (``data/private/memory/<series>/v2/``), one meeting at a time:

    uv run --no-sync python scripts/meeting_memory_v2.py start --series S
    uv run --no-sync python scripts/meeting_memory_v2.py update-job --series S --meeting M
    uv run --no-sync python scripts/meeting_memory_v2.py check-update --series S --meeting M
    uv run --no-sync python scripts/meeting_memory_v2.py quiet-job --series S --meeting M
    uv run --no-sync python scripts/meeting_memory_v2.py check-quiet --series S --meeting M
    uv run --no-sync python scripts/meeting_memory_v2.py check|costs --series S

An update agent writes ``updates/<M>.json`` and runs ``check-update``: it validates the update
and folds it into ``record_after_<M>.writer.json``. If the update brings back a multi-part entry
as reported or reaffirmed, a separate verifier agent (``memory_quiet_check.v1``) reads those
entries against the transcript, writes ``quiet/<M>.jsonl`` and runs ``check-quiet``, which
applies its findings and writes ``updates/<M>.final.json`` and ``record_after_<M>.json``.
Without candidates ``check-update`` writes the final files itself.

Questions (``data/private/memory/qa/<run>/``), three conditions (``memory_answer.v2``):

    ... qa-select --run R --series S1,S2 [--split test|dev] [--per-type 2] [--seed 42]
    ... qa-jobs|packets|tiebreak|score --run R
    ... log-agents --run-json P --role R --model M JOB...  (who ran which job, on which model)
    ... lines --log P --series S --meeting M --lines L0010-L0020
    ... search --log P --series S [--meeting M] --words "a b"
    ... entry --log P --series S --id E005

Agreement with item fates: ``match-job --series S`` and ``fates --series S --readers a,b``.
Local data only; no network, no API.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tenor.ami.corpus import AmiMeeting, load_meeting
from tenor.ami.questions import AmiQuestion, build_questions, load_questions
from tenor.evals import came_back as K
from tenor.evals import item_fates as F
from tenor.evals import meeting_memory as MM
from tenor.evals import meeting_memory_qa as Q
from tenor.memory import series_check as C
from tenor.memory import series_record as R

# Absolute paths: an agent may start in another directory, and a relative path in its job file
# would then write somewhere else.
REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "data/private/memory"
UPDATE_PROMPT = "memory_update.v2"
QUIET_PROMPT = "memory_quiet_check.v1"
FATES_RUNS = tuple(
    REPO / "data/private/reader/fates_runs" / r
    for r in (
        "fates_20261110",
        "fates_extra_20261110",
        "fates_fresh_20261114",
        "fates_fresh2_20261115",
    )
)
VERDICTS = ("correct", "partially_correct", "incorrect")
SEARCH_MAX = 20
SEED = 42
CMD = f"cd {REPO} && uv run --no-sync python scripts/meeting_memory_v2.py"
# --anon: the record is written from transcripts with every speaker shown as "Them" (as in a
# channel-attributed product transcript), into v2_anon/, with memory_update.v3. Set in __main__.
ANON = False


def meetings(series: str) -> list[str]:
    return [series + x for x in "abcd"]


def chain_dir(series: str) -> Path:
    return ROOT / series / ("v2_anon" if ANON else "v2")


def update_prompt() -> str:
    return "memory_update.v3" if ANON else UPDATE_PROMPT


def final_method() -> str:
    return f"{update_prompt()}+{QUIET_PROMPT}"


def cmd() -> str:
    return CMD + (" --anon" if ANON else "")


def record_path(series: str, after: str | None, writer: bool = False) -> Path:
    tail = ".writer" if writer else ""
    return chain_dir(series) / f"record_after_{after or 'start'}{tail}.json"


def load_record(path: Path) -> R.SeriesRecord:
    return R.SeriesRecord.model_validate_json(path.read_text(encoding="utf-8"))


def final_record(series: str) -> R.SeriesRecord:
    return load_record(record_path(series, meetings(series)[-1]))


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


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def prompt_text(name: str) -> str:
    """A prompt as an agent gets it: without its changelog, which is for maintainers and may
    name development data."""
    text = (REPO / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    return text.split("\n## Changelog")[0].strip()


def transcript_block(mid: str) -> str:
    m = meeting(mid)
    if ANON:
        body = "\n".join(
            f"{ln.line_id} [{K._clock(m.dialogue_acts[i].start)}] Them: {ln.text}"
            for i, ln in enumerate(MM.transcript(m).values())
        )
        return f"## Meeting {mid}: full transcript (speakers not identified)\n\n```\n{body}\n```"
    return (
        f"## Meeting {mid}: full human transcript\n\nRoster: {MM.roster_line(m)}\n\n```\n"
        + MM.transcript_text(m)
        + "\n```"
    )


# -- building the record ---------------------------------------------------------------------


def cmd_start(series: str) -> None:
    chain_dir(series).mkdir(parents=True, exist_ok=True)
    path = record_path(series, None)
    path.write_text(R.dump(R.SeriesRecord(series=series)), encoding="utf-8")
    print(f"empty record -> {path}")


def cmd_update_job(series: str, mid: str) -> None:
    prev = load_record(record_path(series, previous(series, mid)))
    base = chain_dir(series)
    for sub in ("jobs", "updates", "quiet"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    update = base / "updates" / f"{mid}.json"
    check = f"{cmd()} check-update --series {series} --meeting {mid}"
    text = "\n\n".join(
        [
            f"# Record update job: {series}, fold in {mid}",
            f"Prompt `{update_prompt()}`. Write the update to `{update}`. Then run, from the "
            f"repository root, `{check}` and fix the update until it passes.",
            prompt_text(update_prompt()),
            "---",
            "## The record before this meeting\n\n" + R.render(prev, compact=True),
            transcript_block(mid),
        ]
    )
    path = base / "jobs" / f"update_{mid}.md"
    path.write_text(text + "\n", encoding="utf-8")
    print(f"{path}: {len(text)} chars, ~{Q.tokens(len(text))} tokens")


def _fold(series: str, mid: str, update: R.Update, method: str, out: Path) -> R.SeriesRecord:
    prev = load_record(record_path(series, previous(series, mid)))
    problems = C.update_problems(prev, update, MM.transcript(meeting(mid)))
    if problems:
        raise SystemExit("\n".join(f"PROBLEM: {p}" for p in problems))
    rec = R.apply_update(prev, update, method)
    problems = C.record_problems(rec, {m: MM.transcript(meeting(m)) for m in rec.meetings})
    if problems:
        raise SystemExit("\n".join(f"PROBLEM (record): {p}" for p in problems))
    out.write_text(R.dump(rec), encoding="utf-8")
    return rec


def cmd_check_update(series: str, mid: str) -> None:
    base = chain_dir(series)
    raw = (base / "updates" / f"{mid}.json").read_text(encoding="utf-8")
    try:
        update = R.Update.model_validate_json(raw)
    except ValueError as e:
        raise SystemExit(f"PROBLEM: the update is not valid JSON of the right shape:\n{e}") from e
    if update.meeting != mid:
        raise SystemExit(f"PROBLEM: update.meeting is {update.meeting!r}, expected {mid!r}")
    if any(ev.checked_by for ev in update.events):
        raise SystemExit("PROBLEM: checked_by is set by the quiet check, not by the update")
    _fold(series, mid, update, update_prompt(), record_path(series, mid, writer=True))
    prev = load_record(record_path(series, previous(series, mid)))
    cands = R.quiet_candidates(prev, update)
    (base / "quiet" / f"{mid}.candidates.json").write_text(json.dumps(cands) + "\n")
    kinds = Counter(ev.kind for ev in update.events)
    print(f"OK: {len(update.events)} events {dict(kinds)}, {len(update.new_entries)} new")
    if not cands:
        (base / "updates" / f"{mid}.final.json").write_text(update.model_dump_json(indent=1))
        _fold(series, mid, update, final_method(), record_path(series, mid))
        print(f"no quiet-check candidates; final record -> {record_path(series, mid)}")
    else:
        print(f"quiet check needed for {len(cands)} entries: {', '.join(cands)}")


def _writer_event_text(ev: R.EventIn, entry: R.Entry) -> str:
    texts = {p.part_id: p.text for p in entry.parts}
    lines = [f"Note-keeper's event: {ev.kind}. {ev.summary}"]
    lines += [f'  cites {r.line} "{r.quote}"' for r in ev.evidence]
    for pe in ev.parts:
        refs = "; ".join(f'{r.line} "{r.quote}"' for r in pe.evidence)
        lines.append(
            f"  {pe.part} ({texts.get(pe.part, '?')}): {pe.outcome}"
            + (f" [{refs}]" if refs else "")
        )
    return "\n".join(lines)


def cmd_quiet_job(series: str, mid: str) -> None:
    base = chain_dir(series)
    prev = load_record(record_path(series, previous(series, mid)))
    update = R.Update.model_validate_json((base / "updates" / f"{mid}.json").read_text())
    cands = R.quiet_candidates(prev, update)
    if not cands:
        raise SystemExit("no candidates")
    by_id = {e.entry_id: e for e in prev.entries}
    events = {ev.entry_id: ev for ev in update.events}
    blocks = [
        "### "
        + c
        + "\n\n"
        + R.render_entry(by_id[c])
        + "\n\n"
        + _writer_event_text(events[c], by_id[c])
        for c in cands
    ]
    answer = base / "quiet" / f"{mid}.jsonl"
    check = f"{cmd()} check-quiet --series {series} --meeting {mid}"
    text = "\n\n".join(
        [
            f"# Quiet-change check job: {series}, meeting {mid}",
            f"Prompt `{QUIET_PROMPT}`. Write your JSONL to `{answer}`. Then run, from the "
            f"repository root, `{check}` and fix the file until it passes.",
            prompt_text(QUIET_PROMPT),
            "---",
            f"## Entries to check ({len(cands)}), in order\n\n" + "\n\n".join(blocks),
            transcript_block(mid),
        ]
    )
    path = base / "jobs" / f"quiet_{mid}.md"
    path.write_text(text + "\n", encoding="utf-8")
    print(f"{path}: {len(cands)} entries, ~{Q.tokens(len(text))} tokens")


def cmd_check_quiet(series: str, mid: str) -> None:
    base = chain_dir(series)
    prev = load_record(record_path(series, previous(series, mid)))
    update = R.Update.model_validate_json((base / "updates" / f"{mid}.json").read_text())
    findings = []
    for n, line in enumerate((base / "quiet" / f"{mid}.jsonl").read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            findings.append(R.QuietFinding.model_validate_json(line))
        except ValueError as e:
            raise SystemExit(f"PROBLEM: line {n} is not a valid finding:\n{e}") from e
    problems = C.quiet_problems(prev, update, findings)
    if problems:
        raise SystemExit("\n".join(f"PROBLEM: {p}" for p in problems))
    final = R.apply_quiet(update, findings, QUIET_PROMPT)
    _fold(series, mid, final, final_method(), record_path(series, mid))
    (base / "updates" / f"{mid}.final.json").write_text(final.model_dump_json(indent=1))
    verdicts = Counter(f.verdict for f in findings)
    print(f"OK: {dict(verdicts)} -> {record_path(series, mid)}")


def cmd_check(series: str) -> None:
    for mid in meetings(series):
        path = record_path(series, mid)
        if not path.exists():
            print(f"{mid}: no record")
            continue
        rec = load_record(path)
        problems = C.record_problems(rec, {m: MM.transcript(meeting(m)) for m in rec.meetings})
        status = Counter(e.status for e in rec.entries)
        print(
            f"{mid}: {len(rec.entries)} entries, statuses {dict(status)}, {len(problems)} "
            f"problems, compact ~{Q.tokens(len(R.render(rec, compact=True)))} tokens, full "
            f"~{Q.tokens(len(R.render(rec)))}"
        )
        for p in problems:
            print("  ", p)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def cmd_costs(series: str) -> None:
    base = chain_dir(series)
    rows = []
    for mid in meetings(series):
        path = record_path(series, mid)
        if not path.exists():
            continue
        rec = load_record(path)
        job = base / "jobs" / f"update_{mid}.md"
        qjob = base / "jobs" / f"quiet_{mid}.md"
        final = R.Update.model_validate_json((base / "updates" / f"{mid}.final.json").read_text())
        findings = _jsonl(base / "quiet" / f"{mid}.jsonl")
        rows.append(
            {
                "after": mid,
                "transcript_tokens": Q.tokens(len(MM.transcript_text(meeting(mid)))),
                "record_tokens_compact": Q.tokens(len(R.render(rec, compact=True))),
                "record_tokens_full": Q.tokens(len(R.render(rec))),
                "entries": len(rec.entries),
                "entries_by_kind": dict(Counter(e.kind for e in rec.entries)),
                "statuses": dict(Counter(e.status for e in rec.entries)),
                "multi_part_entries": sum(len(e.parts) >= 2 for e in rec.entries),
                "update_job_tokens": Q.tokens(len(job.read_text())) if job.exists() else None,
                "quiet_job_tokens": Q.tokens(len(qjob.read_text())) if qjob.exists() else 0,
                "new_entries": len(final.new_entries),
                "events": dict(Counter(ev.kind for ev in final.events)),
                "quiet_candidates": len(findings),
                "quiet_changes_added": sum(f["verdict"] == "quiet_change" for f in findings),
            }
        )
    path = base / "costs.json"
    path.write_text(json.dumps(rows, indent=1) + "\n")
    for r in rows:
        print(json.dumps(r))


# -- lookups (record and search conditions) ---------------------------------------------------


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


def _check_meeting(series: str, mid: str) -> None:
    if mid not in meetings(series):
        raise SystemExit(f"{mid} is not a meeting of {series}: {', '.join(meetings(series))}")


def cmd_lines(log: Path, series: str, mid: str, spec: str) -> None:
    _check_meeting(series, mid)
    m = meeting(mid)
    text = "\n".join(f"{mid} {K.line(m, i)}" for i in _indices(spec, len(m.dialogue_acts)))
    print(text)
    n = text.count("\n") + 1 if text else 0
    _log(
        log,
        Q.Lookup(
            command="lines", meeting=mid, arg=spec, lines_returned=n, chars_returned=len(text)
        ),
    )


def cmd_search(log: Path, series: str, mid: str | None, query: str) -> None:
    if mid:
        _check_meeting(series, mid)
    want = C.words(query)
    hits = []
    for m_id in [mid] if mid else meetings(series):
        m = meeting(m_id)
        hits += [
            f"{m_id} {K.line(m, i)}"
            for i, a in enumerate(m.dialogue_acts)
            if want and all(w in C.words(a.text) for w in want)
        ]
    shown = hits[:SEARCH_MAX]
    text = "\n".join(shown) or "(no line has all of these words)"
    if len(hits) > SEARCH_MAX:
        text += f"\n({len(hits) - SEARCH_MAX} more lines match; narrow the search)"
    print(text)
    _log(
        log,
        Q.Lookup(
            command="search",
            meeting=mid or "*",
            arg=query,
            lines_returned=len(shown),
            chars_returned=len(text),
        ),
    )


def cmd_entry(log: Path, series: str, eid: str) -> None:
    rec = final_record(series)
    got = next((e for e in rec.entries if e.entry_id == eid), None)
    text = R.render_entry(got) if got else f"(no entry {eid})"
    print(text)
    _log(
        log,
        Q.Lookup(
            command="entry",
            meeting="*",
            arg=eid,
            lines_returned=text.count("\n") + 1,
            chars_returned=len(text),
        ),
    )


# -- questions --------------------------------------------------------------------------------


def qa_dir(run: str) -> Path:
    return ROOT / "qa" / run


def series_of(q: AmiQuestion) -> str:
    return q.meetings[0][:-1]


def group_of(q: AmiQuestion) -> str:
    return "cross" if len(q.meetings) > 1 else "single"


def built_questions(series: str) -> list[AmiQuestion]:
    """Every question the fixed templates give for the series (seed 42), checked against the
    published set when the series is in it."""
    built = build_questions([meeting(m) for m in meetings(series)], SEED)
    published = [q for q in load_questions() if series_of(q) == series]
    if published and [q.model_dump() for q in published] != [q.model_dump() for q in built]:
        raise SystemExit(f"{series}: rebuilt questions differ from questions.jsonl")
    return built


def select(qs: list[AmiQuestion], per_type: int, seed: int) -> list[AmiQuestion]:
    """Every cross-meeting question, and per series and single-meeting type up to
    ``per_type`` questions drawn with a seeded shuffle."""
    out = [q for q in qs if group_of(q) == "cross"]
    cells: dict[tuple[str, str], list[AmiQuestion]] = {}
    for q in qs:
        if group_of(q) == "single":
            cells.setdefault((series_of(q), q.type), []).append(q)
    for (s, t), cell in sorted(cells.items()):
        pool = sorted(cell, key=lambda q: q.id)
        random.Random(f"{seed}:memory_v2.select:{s}:{t}").shuffle(pool)
        out += pool[:per_type]
    return sorted(out, key=lambda q: q.id)


def load_run_questions(run: str) -> list[AmiQuestion]:
    rows = json.loads((qa_dir(run) / "questions.json").read_text())["questions"]
    return [AmiQuestion.model_validate(r) for r in rows]


def cmd_qa_select(run: str, series: list[str], split: str, per_type: int, seed: int) -> None:
    pool: list[AmiQuestion] = []
    for s in series:
        pool += [q for q in built_questions(s) if q.split == split]
    chosen = pool if split == "dev" else select(pool, per_type, seed)
    base = qa_dir(run)
    base.mkdir(parents=True, exist_ok=True)
    meta = {
        "run": run,
        "series": series,
        "split": split,
        "per_type": per_type,
        "seed": seed,
        "rule": "every cross-meeting question; per series and single-meeting type up to per_type "
        "questions by a seeded shuffle"
        if split == "test"
        else "every question of the split",
        "pool": len(pool),
        "pool_by_group": dict(Counter(group_of(q) for q in pool)),
        "chosen_by_group": dict(Counter(group_of(q) for q in chosen)),
        "chosen_by_type": dict(Counter(q.type for q in chosen)),
        "chosen_by_series": dict(Counter(series_of(q) for q in chosen)),
        "questions": [q.model_dump(mode="json") for q in chosen],
    }
    (base / "questions.json").write_text(json.dumps(meta, indent=1) + "\n")
    print({k: v for k, v in meta.items() if k != "questions"})


def _meeting_list(series: str) -> str:
    rows = [f"- {m} ({len(meeting(m).dialogue_acts)} lines)" for m in meetings(series)]
    return f"## Meetings of series {series}, in order\n\n" + "\n".join(rows)


def _condition_block(template: str, condition: str) -> str:
    head, *blocks = template.split("\n--- condition: ")
    block = next(b.split("\n", 1)[1].strip() for b in blocks if b.startswith(condition + "\n"))
    return head.replace("{condition}", block.strip()).strip()


def qa_job(run: str, q: AmiQuestion, condition: str, record_text: dict[str, str]) -> str:
    base = qa_dir(run)
    name = f"{q.id}__{condition}"
    s = series_of(q)
    answer = base / "answers" / f"{name}.json"
    template = prompt_text(Q.PROMPT_V2)
    parts = [
        f"# Answer job {name}",
        f"Prompt `{Q.PROMPT_V2}`, condition `{condition}`. Write your answer to `{answer}`.",
        _condition_block(template, condition),
    ]
    log = base / "lookups" / f"{name}.jsonl"
    tools = [
        f"- open lines: `{CMD} lines --log {log} --series {s} --meeting <meeting id> --lines "
        "L0120-L0130` (single ids and comma-separated ranges also work)",
        f'- search: `{CMD} search --log {log} --series {s} --words "<words>"` (lines of any '
        f"meeting of the series that contain all the words, at most {SEARCH_MAX}; add "
        "`--meeting <meeting id>` to search one meeting)",
    ]
    if condition == "record":
        tools.append(
            f"- one entry's full history: `{CMD} entry --log {log} --series {s} --id E005`"
        )
    if condition in ("record", "search"):
        parts.append(
            "## Lookup commands (run from the repository root; each call is logged)\n\n"
            + "\n".join(tools)
        )
        parts.append(_meeting_list(s))
    if condition == "record":
        parts += ["---", record_text[s]]
    if condition == "named":
        parts += [transcript_block(m) for m in q.meetings]
    parts.append(f"## Question ({q.id})\n\n{q.question}")
    return "\n\n".join(parts) + "\n"


def cmd_qa_jobs(run: str) -> None:
    qs = load_run_questions(run)
    base = qa_dir(run)
    for sub in ("jobs", "answers", "lookups"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    record_text = {s: R.render(final_record(s), compact=True) for s in {series_of(q) for q in qs}}
    for q in qs:
        for c in Q.CONDITIONS_V2:
            (base / "jobs" / f"{q.id}__{c}.md").write_text(qa_job(run, q, c, record_text))
    meta = {
        "run": run,
        "prompt": Q.PROMPT_V2,
        "update_prompt": UPDATE_PROMPT,
        "quiet_prompt": QUIET_PROMPT,
        "records": {s: str(record_path(s, meetings(s)[-1])) for s in sorted(record_text)},
        "record_tokens_compact": {s: Q.tokens(len(t)) for s, t in sorted(record_text.items())},
        "conditions": list(Q.CONDITIONS_V2),
        "questions": [q.id for q in qs],
        "git": git_sha(),
        "date": now(),
    }
    (base / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"{len(qs)} questions x {len(Q.CONDITIONS_V2)} conditions -> {base / 'jobs'}")


def load_answers(run: str) -> dict[tuple[str, str], Q.Answer]:
    out = {}
    for path in sorted((qa_dir(run) / "answers").glob("*.json")):
        a = Q.Answer.model_validate_json(path.read_text(encoding="utf-8"))
        if path.stem != f"{a.qid}__{a.condition}":
            raise ValueError(f"{path.name} holds {a.qid} {a.condition}")
        out[(a.qid, a.condition)] = a
    return out


def _packet(
    name: str,
    qs: list[AmiQuestion],
    answers: dict[tuple[str, str], Q.Answer],
    key: dict[str, dict[str, str]],
) -> str:
    ms = sorted({m for q in qs for m in q.meetings})
    rows = [(q.id, q.question, q.gold_answer) for q in qs]
    return (
        "\n\n".join(
            [
                f"# Reader job {name}",
                f"Prompt `{Q.READER_PROMPT}`. You get the transcripts of every meeting these "
                "questions are about; each question names its meetings. Grade all "
                f"{len(qs) * 3} answers below (three per question). Write your JSONL to the "
                "answer file you were given. Use only this file.",
                prompt_text(Q.READER_PROMPT),
                "---",
                *(transcript_block(m) for m in ms),
                "## Questions and answers",
                Q.packet(rows, answers, key),
            ]
        )
        + "\n"
    )


def cmd_packets(run: str, seed: int) -> None:
    base = qa_dir(run)
    answers = load_answers(run)
    qs = load_run_questions(run)
    missing = [(q.id, c) for q in qs for c in Q.CONDITIONS_V2 if (q.id, c) not in answers]
    if missing:
        raise SystemExit(f"answers missing: {missing}")
    key = Q.packet_key([q.id for q in qs], seed, Q.CONDITIONS_V2)
    out = base / "packets"
    out.mkdir(parents=True, exist_ok=True)
    for s in sorted({series_of(q) for q in qs}):
        sub = [q for q in qs if series_of(q) == s]
        (out / f"{s}.md").write_text(_packet(s, sub, answers, key))
        print(f"{s}: {len(sub)} questions")
    (base / "key.json").write_text(
        json.dumps(
            {"run": run, "seed": seed, "reader_prompt": Q.READER_PROMPT, "key": key}, indent=1
        )
        + "\n"
    )


def reader_rows(run: str, reader: str) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for path in sorted((qa_dir(run) / "readers" / reader).glob("*.jsonl")):
        for d in _jsonl(path):
            if d["verdict"] not in VERDICTS:
                raise ValueError(f"{path.name}: verdict {d['verdict']!r}")
            out[(d["qid"], d["label"])] = d["verdict"]
    return out


def cmd_tiebreak(run: str) -> None:
    base = qa_dir(run)
    a, b = reader_rows(run, "a"), reader_rows(run, "b")
    if set(a) != set(b):
        raise SystemExit(f"readers a and b graded different rows: {sorted(set(a) ^ set(b))[:5]}")
    split = sorted(k for k in a if b[k] != a[k])
    (base / "tiebreak.json").write_text(json.dumps([list(k) for k in split], indent=1) + "\n")
    key = json.loads((base / "key.json").read_text())["key"]
    answers = load_answers(run)
    qs = {q.id: q for q in load_run_questions(run)}
    out = base / "packets_tiebreak"
    out.mkdir(parents=True, exist_ok=True)
    need = sorted({q for q, _ in split})
    for s in sorted({series_of(qs[q]) for q in need}):
        sub = [qs[q] for q in need if series_of(qs[q]) == s]
        (out / f"{s}.md").write_text(_packet(f"{s} (tie-break)", sub, answers, key))
    print(
        f"readers agree on {len(a) - len(split)} of {len(a)}; {len(split)} rows in "
        f"{len(need)} questions for the tie-break"
    )


def costs_of(run: str) -> dict[tuple[str, str], dict[str, int]]:
    base = qa_dir(run)
    out = {}
    for path in sorted((base / "jobs").glob("*.md")):
        qid, cond = path.stem.split("__")
        lookups = [
            Q.Lookup.model_validate(d) for d in _jsonl(base / "lookups" / f"{path.stem}.jsonl")
        ]
        out[(qid, cond)] = Q.read_cost(len(path.read_text(encoding="utf-8")), lookups)
    return out


def cmd_score(run: str, seed: int) -> None:
    base = qa_dir(run)
    answers = load_answers(run)
    key = json.loads((base / "key.json").read_text())["key"]
    a, b = reader_rows(run, "a"), reader_rows(run, "b")
    c = reader_rows(run, "c") if (base / "readers" / "c").exists() else {}
    settled, agree = Q.settle(a, b, c)
    verdicts = {(q, key[q][label]): v for (q, label), v in settled.items()}
    qs = load_run_questions(run)
    groups = {q.id: group_of(q) for q in qs}
    result = Q.score_v2([q.id for q in qs], groups, verdicts, costs_of(run), answers, seed=seed)
    by_series: dict[str, Any] = {}
    for s in sorted({series_of(q) for q in qs}):
        ids = [q.id for q in qs if series_of(q) == s]
        by_series[s] = {
            cnd: sum(verdicts[(q, cnd)] == "correct" for q in ids) for cnd in Q.CONDITIONS_V2
        } | {"n": len(ids)}
    by_type: dict[str, Any] = {}
    for t in sorted({q.type for q in qs}):
        ids = [q.id for q in qs if q.type == t]
        by_type[t] = {
            cnd: sum(verdicts[(q, cnd)] == "correct" for q in ids) for cnd in Q.CONDITIONS_V2
        } | {"n": len(ids)}
    meta = json.loads((base / "meta.json").read_text())
    run_json = base / "run.json"
    result["by_series_correct"] = by_series
    result["by_type_correct"] = by_type
    result["meta"] = {
        **meta,
        "reader_prompt": Q.READER_PROMPT,
        "reader_agreement": {"agree": agree, "of": len(a), "tiebreak_rows": len(a) - agree},
        "bootstrap": {"seed": seed, "n": Q.N_BOOT, "resamples": "questions"},
        "chars_per_token": Q.CHARS_PER_TOKEN,
        "all_transcripts_tokens": {
            s: Q.tokens(sum(len(MM.transcript_text(meeting(m))) for m in meetings(s)))
            for s in sorted({series_of(q) for q in qs})
        },
        "agents": json.loads(run_json.read_text()) if run_json.exists() else None,
        "scored_git": git_sha(),
        "scored": now(),
    }
    result["verdicts"] = {f"{q}|{cnd}": v for (q, cnd), v in sorted(verdicts.items())}
    (base / "score.json").write_text(json.dumps(result, indent=1) + "\n")
    for name, block in [("overall", result["overall"]), *result["groups"].items()]:
        print(f"== {name} (n={block['n']})")
        for cnd, st in block["conditions"].items():
            print(f"  {cnd:7} {json.dumps(st)}")
        print("  paired", json.dumps(block["paired"]))
    print(json.dumps(result["meta"]["reader_agreement"]))


# -- which agent did what ---------------------------------------------------------------------


def cmd_log_agents(path: Path, role: str, model: str, jobs: list[str]) -> None:
    """Append agent runs (role, model, job file) to a run.json list."""
    rows = json.loads(path.read_text()) if path.exists() else []
    rows += [{"role": role, "model": model, "job": j, "logged": now()} for j in jobs]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=1) + "\n")
    print(f"{len(jobs)} added, {len(rows)} in {path}")


# -- agreement with item fates ----------------------------------------------------------------


def fates_labels(series: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for run in FATES_RUNS:
        for row in json.loads((run / "rows.json").read_text())["items"]:
            if row["pair"].startswith(series):
                if row["x_item"] in out.get(row["pair"], {}):
                    raise SystemExit(f"{row['pair']} {row['x_item']} labelled twice")
                out.setdefault(row["pair"], {})[row["x_item"]] = row["fate"]
    return out


def labelled_pairs(series: str) -> list[tuple[str, str, list[K.Item], dict[str, str]]]:
    labels = fates_labels(series)
    return [
        (x, y, F.items(meeting(x)), labels[f"{x}__{y}"])
        for x, y in itertools.pairwise(meetings(series))
        if f"{x}__{y}" in labels
    ]


def cmd_match_job(series: str) -> None:
    rec = final_record(series)
    out = chain_dir(series) / "match"
    out.mkdir(parents=True, exist_ok=True)
    xs = [x for x, _, _, _ in labelled_pairs(series)]
    body = "\n\n".join(
        [
            f"# Item match job: {series} (v2)",
            f"Prompt `{MM.MATCH_PROMPT}`. Write your JSONL to the answer file you were given. "
            "Use only this file.",
            prompt_text(MM.MATCH_PROMPT),
            "---",
            *(MM.match_section(meeting(x), rec.entries) for x in xs),
        ]
    )
    (out / "job.md").write_text(body + "\n", encoding="utf-8")
    print(f"{out / 'job.md'}: meetings {xs}")


def load_matches(series: str, reader: str) -> dict[str, str | None]:
    rec = final_record(series)
    xs = [x for x, _, _, _ in labelled_pairs(series)]
    expected = {x: [it.item_id for it in F.items(meeting(x))] for x in xs}
    entries = {x: [e.entry_id for e in rec.entries if e.introduced_in == x] for x in xs}
    path = chain_dir(series) / "match" / f"{reader}.jsonl"
    got, errors = MM.parse_matches(path.read_text(encoding="utf-8"), expected, entries)
    if errors:
        raise SystemExit("\n".join(f"{path}: {e}" for e in errors))
    return got


def cmd_fates(series: str, readers: list[str]) -> None:
    rec = final_record(series)
    pairs = labelled_pairs(series)
    maps = {r: load_matches(series, r) for r in readers}
    results: dict[str, Any] = {}
    for r, mapping in maps.items():
        results[f"reader_{r}"] = MM.fates_agreement(pairs, rec, mapping)
    if len(maps) == 2:
        a, b = maps.values()
        agreed: dict[str, str | None] = {k: v if b.get(k) == v else None for k, v in a.items()}
        results["readers_agreed"] = MM.fates_agreement(pairs, rec, agreed)
        results["reader_mapping_agreement"] = {
            "agree": sum(a[k] == b.get(k) for k in a),
            "of": len(a),
            "split": sorted(k for k in a if a[k] != b.get(k)),
        }
    results["record_quiet_events"] = MM.quiet_events(rec)
    results["meta"] = {
        "chain": "v2",
        "record": str(record_path(series, meetings(series)[-1])),
        "fates_runs": [str(r) for r in FATES_RUNS],
        "match_prompt": MM.MATCH_PROMPT,
        "git": git_sha(),
        "date": now(),
    }
    path = chain_dir(series) / "fates_agreement.json"
    path.write_text(json.dumps(results, indent=1) + "\n")
    for name, res in results.items():
        if name in ("meta", "reader_mapping_agreement", "record_quiet_events"):
            print(name, json.dumps(res))
            continue
        print(
            f"{name}: {res['items']} items, {res['matched']} matched, exact {res['exact']}, "
            f"coarse {res['coarse']}, unmatched {len(res['unmatched'])}"
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--anon", dest="anon_top", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("start", "check", "costs", "match-job"):
        sub.add_parser(name).add_argument("--series", required=True)
    p = sub.add_parser("fates")
    p.add_argument("--series", required=True)
    p.add_argument("--readers", default="a,b")
    for name in ("update-job", "check-update", "quiet-job", "check-quiet"):
        p = sub.add_parser(name)
        p.add_argument("--series", required=True)
        p.add_argument("--meeting", required=True)
    p = sub.add_parser("lines")
    for flag in ("--log", "--series", "--meeting", "--lines"):
        p.add_argument(flag, required=True)
    p = sub.add_parser("search")
    for flag in ("--log", "--series", "--words"):
        p.add_argument(flag, required=True)
    p.add_argument("--meeting")
    p = sub.add_parser("entry")
    for flag in ("--log", "--series", "--id"):
        p.add_argument(flag, required=True)
    p = sub.add_parser("qa-select")
    p.add_argument("--run", required=True)
    p.add_argument("--series", required=True)
    p.add_argument("--split", choices=("test", "dev"), default="test")
    p.add_argument("--per-type", type=int, default=2)
    p.add_argument("--seed", type=int, default=SEED)
    for name in ("qa-jobs", "packets", "tiebreak", "score"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        p.add_argument("--seed", type=int, default=SEED)
    p = sub.add_parser("log-agents")
    p.add_argument("--run-json", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("jobs", nargs="+")
    for p in sub.choices.values():
        p.add_argument("--anon", action="store_true", help="anonymous speakers, into v2_anon/")
    a = ap.parse_args()
    ANON = a.anon or a.anon_top
    if a.cmd == "log-agents":
        cmd_log_agents(Path(a.run_json), a.role, a.model, a.jobs)
    elif a.cmd == "start":
        cmd_start(a.series)
    elif a.cmd == "update-job":
        cmd_update_job(a.series, a.meeting)
    elif a.cmd == "check-update":
        cmd_check_update(a.series, a.meeting)
    elif a.cmd == "quiet-job":
        cmd_quiet_job(a.series, a.meeting)
    elif a.cmd == "check-quiet":
        cmd_check_quiet(a.series, a.meeting)
    elif a.cmd == "check":
        cmd_check(a.series)
    elif a.cmd == "costs":
        cmd_costs(a.series)
    elif a.cmd == "lines":
        cmd_lines(Path(a.log), a.series, a.meeting, a.lines)
    elif a.cmd == "search":
        cmd_search(Path(a.log), a.series, a.meeting, a.words)
    elif a.cmd == "entry":
        cmd_entry(Path(a.log), a.series, a.id)
    elif a.cmd == "qa-select":
        cmd_qa_select(a.run, a.series.split(","), a.split, a.per_type, a.seed)
    elif a.cmd == "qa-jobs":
        cmd_qa_jobs(a.run)
    elif a.cmd == "packets":
        cmd_packets(a.run, a.seed)
    elif a.cmd == "tiebreak":
        cmd_tiebreak(a.run)
    elif a.cmd == "score":
        cmd_score(a.run, a.seed)
    elif a.cmd == "match-job":
        cmd_match_job(a.series)
    else:
        cmd_fates(a.series, [r for r in a.readers.split(",") if r])
