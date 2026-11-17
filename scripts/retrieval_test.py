"""Test-series comparison: tools vs tools + running record, both with wording v2, on the five
AMI series the record never saw in development (ES2005, ES2006, ES2012, IS1006, TS3007).

Workspace (one, shared): the 20 test-series meetings plus 13 other AMI meetings as distractors
(IS1003, IS1008, TS3009 a-d; IB4003). Every meeting has stand-in AI notes written
by an agent from its anonymous transcript (standin_notes.v1), so both conditions read notes
first, as with Granola; speakers are anonymous. Questions: the 12 cross-meeting test questions
of meeting-memory phase 2. Runs: 3 per question and condition. Readers: two blind readers per
(series, condition) packet with the three runs shuffled as X, Y, Z; a third breaks ties.

  notes-jobs | setup | readers | score
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
from pathlib import Path

from tenor.ami.corpus import load_meeting
from tenor.ami.questions import AmiQuestion
from tenor.evals.meeting_memory import transcript
from tenor.retrieval.harness import score_trial, summarize
from tenor.retrieval.tools import WORDING, ToolCall

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from memory_breakdown import facts_of  # noqa: E402

OUT = REPO / "data/private/retrieval/test"
NOTES = REPO / "data/private/retrieval/notes"
SERIES = ["ES2005", "ES2006", "ES2012", "IS1006", "TS3007"]
# IB4005, IB4010 and IB4011 were dropped before any answer: their transcripts are empty in the
# AMI files held here, so they would be blank meetings.
DISTRACTORS = [
    *(f"IS1003{x}" for x in "abcd"),
    *(f"IS1008{x}" for x in "abcd"),
    *(f"TS3009{x}" for x in "abcd"),
    "IB4003",
]
CONDITIONS = ("tools", "tools_record")
RUNS = 3
SEED = 42
WORDING_VERSION = "v2"
TOOLS = ["search_meetings", "search_transcripts", "get_transcript", "get_meetings"]


def all_meetings() -> list[str]:
    return [f"{s}{x}" for s in SERIES for x in "abcd"] + DISTRACTORS


QSET = "cross"  # "cross": the 12 cross-meeting questions; "single": the 50 others; "all": 62
SUFFIX = ""


def questions() -> list[AmiQuestion]:
    rows = json.loads((REPO / "data/private/memory/qa/qa_v2_test/questions.json").read_text())[
        "questions"
    ]
    qs = [AmiQuestion.model_validate(r) for r in rows]
    if QSET == "single":
        qs = [q for q in qs if len(q.meetings) == 1]
    elif QSET != "all":
        qs = [q for q in qs if len(q.meetings) > 1]
    return sorted(qs, key=lambda q: q.id)


def tag(cond: str, qid: str, run: int) -> str:
    return f"{cond}__{qid}__run{run}"


def cmd_notes_jobs() -> None:
    prompt = (REPO / "prompts/standin_notes.v1.md").read_text().split("\n## Changelog")[0]
    (NOTES / "jobs").mkdir(parents=True, exist_ok=True)
    for mid in all_meetings():
        lines = transcript(load_meeting(mid))
        body = "\n".join(f"Them: {ln.text}" for ln in lines.values())
        (NOTES / "jobs" / f"{mid}.md").write_text(
            f"{prompt}\n\n## Your job\n\nMeeting: {mid}\n\nOutput file: {NOTES / (mid + '.md')}\n\n"
            f"## Transcript\n\n{body}\n"
        )
    print(len(all_meetings()), "note jobs")


def cmd_setup() -> None:
    missing = [m for m in all_meetings() if not (NOTES / f"{m}.md").is_file()]
    if missing:
        raise SystemExit(f"notes missing for {missing}")
    prompt = (REPO / "prompts/retrieval_answer.v2.md").read_text().split("\n## Changelog")[0]
    w = WORDING[WORDING_VERSION]
    for d in ("jobs", "logs", "answers", "readers"):
        (OUT / d).mkdir(parents=True, exist_ok=True)
    for cond in CONDITIONS:
        ws: dict[str, object] = {
            "meetings": [f"{s}{x}" for s in SERIES for x in "abcd"],
            "notes": "standin",
            "speakers": "anonymous",
            "distractors": DISTRACTORS,
        }
        tools = list(TOOLS)
        if cond == "tools_record":
            ws["records"] = {
                s: str(REPO / f"data/private/memory/{s}/v2/record_after_{s}d.json") for s in SERIES
            }
            tools.append("get_series_record")
        wpath = OUT / f"workspace_{cond}.json"
        wpath.write_text(json.dumps(ws, indent=1) + "\n")
        tool_list = "\n".join(f"- {t}: {w[t]}" for t in tools)
        for q in questions():
            for run in range(RUNS):
                t = tag(cond, q.id, run)
                cmd = f"uv run python scripts/retrieval_tools.py --workspace {wpath} --log {OUT / 'logs' / (t + '.jsonl')}"
                (OUT / "jobs" / f"{t}.md").write_text(
                    f"{prompt}\n\n## Tools\n\n{w['server']}\n\n{tool_list}\n\n## Your job\n\n"
                    f"Question: {q.question}\n\nTool command (run from {REPO}):\n\n    {cmd} <tool> '<json args>'\n\n"
                    f"Example:\n\n    {cmd} search_meetings '{{}}'\n\nAnswer file: {OUT / 'answers' / (t + '.txt')}\n"
                )
    print(len(list((OUT / "jobs").glob("*.md"))), "answer jobs")


def cmd_readers() -> None:
    prompt = (REPO / "prompts/mcp_reading_reader.v1.md").read_text().split("\n## Changelog")[0]
    rng = random.Random(SEED)
    key: dict[str, dict[str, dict[str, int]]] = {}
    for cond in CONDITIONS:
        key[cond] = {}
        for s in SERIES:
            qs = [q for q in questions() if q.meetings[0].startswith(s)]
            if not qs:
                continue
            parts = [prompt, "\n## Packet\n"]
            for mid in sorted({m for q in qs for m in q.meetings}):
                parts.append(f"\n### Meeting {mid}: full human transcript\n")
                parts.extend(
                    f"{k} {v.speaker}: {v.text}" for k, v in transcript(load_meeting(mid)).items()
                )
            for q in qs:
                runs = list(range(RUNS))
                rng.shuffle(runs)
                key[cond][q.id] = dict(zip("XYZ", runs, strict=True))
                parts.append(
                    f"\n### Question {q.id}\n{q.question}\n\nGold answer: {q.gold_answer}\n"
                )
                for lab, run in key[cond][q.id].items():
                    ans = (OUT / "answers" / f"{tag(cond, q.id, run)}.txt").read_text().strip()
                    parts.append(f"\nAnswer {lab}:\n{ans or '(abstained)'}\n")
            (OUT / "readers" / f"packet_{cond}_{s}{SUFFIX}.md").write_text("\n".join(parts) + "\n")
    (OUT / "readers" / f"key{SUFFIX}.json").write_text(json.dumps(key, indent=1) + "\n")


def settled(cond: str) -> dict[tuple[str, int], str]:
    key: dict[str, dict[str, int]] = {}
    for kf in ("key.json", "key_single.json"):
        if (OUT / "readers" / kf).is_file():
            key |= json.loads((OUT / "readers" / kf).read_text())[cond]
    by: dict[tuple[str, int], list[str]] = {}
    # Exact names only: "verdicts_tools_*" would also match "verdicts_tools_record_*".
    exact = re.compile(rf"^verdicts_{cond}_[A-Z]{{2}}\d{{4}}(_single)?_[A-Z]\.jsonl$")
    for f in sorted(p for p in (OUT / "readers").glob("verdicts_*.jsonl") if exact.match(p.name)):
        for line in f.read_text().splitlines():
            if line.strip():
                v = json.loads(line)
                by.setdefault((v["qid"], key[v["qid"]][v["label"]]), []).append(v["verdict"])
    return {
        k: vs[-1] if len(vs) > 2 else (vs[0] if len(set(vs)) == 1 else "disputed")
        for k, vs in by.items()
    }


def cmd_score() -> None:
    out: dict[str, object] = {
        "git": subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        ).stdout.strip(),
        "seed": SEED,
        "answer_prompt": "retrieval_answer.v2",
        "notes_prompt": "standin_notes.v1",
        "reader_prompt": "mcp_reading_reader.v1",
        "wording": WORDING_VERSION,
        "conditions": {},
    }
    for cond in CONDITIONS:
        ws = json.loads((OUT / f"workspace_{cond}.json").read_text())
        known = [*ws["meetings"], *ws["distractors"]]
        v = settled(cond)
        trials = []
        for q in questions():
            for run in range(RUNS):
                t = tag(cond, q.id, run)
                log = [
                    ToolCall.model_validate_json(x)
                    for x in (OUT / "logs" / f"{t}.jsonl").read_text().splitlines()
                    if x.strip()
                ]
                trials.append(
                    score_trial(
                        q.id,
                        run,
                        facts_of(q),
                        (OUT / "answers" / f"{t}.txt").read_text(),
                        log,
                        v.get((q.id, run)) == "correct",
                        known,
                        q.question,
                    )
                )
        out["conditions"][cond] = {  # type: ignore[index]
            "summary": summarize(trials)["all"].model_dump(),
            "verdicts": {f"{a}|{b}": x for (a, b), x in sorted(v.items())},
            "trials": [
                t.model_dump() | {"passed": t.passed, "category": t.category} for t in trials
            ],
        }
    (OUT / f"score_{QSET}.json" if QSET != "cross" else OUT / "score.json").write_text(
        json.dumps(out, indent=1) + "\n"
    )
    for cond, c in out["conditions"].items():  # type: ignore[attr-defined]
        print(cond, json.dumps(c["summary"]))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("cmd", choices=["notes-jobs", "setup", "readers", "score"])
    p.add_argument(
        "--set", default="cross", choices=["cross", "single", "all"], help="question set"
    )
    a = p.parse_args()
    global QSET, SUFFIX
    QSET = a.set
    SUFFIX = "_single" if a.set == "single" else ""
    cmds = {
        "notes-jobs": cmd_notes_jobs,
        "setup": cmd_setup,
        "readers": cmd_readers,
        "score": cmd_score,
    }
    cmds[a.cmd]()


if __name__ == "__main__":
    main()
