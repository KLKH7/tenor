"""The notes product's chat against our "since last time" writer, on the IS1008 test pairs ($0).

    uv run --no-sync python scripts/chat_followthrough.py jobs --run <RUN> [--seed 42]
    uv run --no-sync python scripts/chat_followthrough.py score --run <RUN> [--seed 42]

Chat answers were fetched by an agent with the read-only MCP tool ``query_granola_meetings``,
``document_ids`` set to the pair's two notes, and saved verbatim (tool preamble removed) to
``data/private/reader/chat_ft_runs/<RUN>/raw/<X>__<Y>__<q>__r<n>.md`` with ``meta.json``.

``jobs`` writes one blind reader job per TEST pair (``chat_ft_runs/<RUN>/jobs/<X>__<Y>.md``,
prompt ``chat_followthrough_reader.v1``): X's notes, X's decision and task items, X's and Y's
product transcripts, then every chat answer of the pair and the writer's sentences of the
follow-through run (``ft_runs/ft_20261114/writer``) split into claims under shuffled letters.
Readers a and b answer into ``chat_ft_runs/<RUN>/readers/<a|b>/<X>__<Y>.jsonl``. ``score``
counts per source and writes ``score.json`` and ``key.json``. Local data only, no LLM.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tenor.evals import bullet_cues as B
from tenor.evals import chat_followthrough as C
from tenor.evals import follow_through as T

READERS = Path("data/private/reader")
PACKETS = READERS / "packets"
FT_RUN = READERS / "ft_runs" / "ft_20261114"
KIND_RUN = READERS / "kind_agent_runs" / "kind_20261110" / "notes"
RUNS = READERS / "chat_ft_runs"
PRODUCT = "granola"
TEST_PAIRS = ("IS1008b__IS1008c", "IS1008c__IS1008d")


def load(meeting: str) -> B.CueUnit:
    text = (PACKETS / f"{meeting}__{PRODUCT}.md").read_text(encoding="utf-8")
    return B.parse_packet(text, meeting, PRODUCT, "n/a")


def items(x: B.CueUnit) -> list[T.NoteItem]:
    for d in (FT_RUN / "kind", KIND_RUN):
        path = d / f"{x.meeting_id}__{PRODUCT}.jsonl"
        if path.exists():
            return T.note_items(x.bullets, T.parse_kinds(path.read_text(encoding="utf-8")))
    raise SystemExit(f"no statement-kind labels for {x.key}")


def answers(run: str, pair: str) -> list[C.Answer]:
    out = [
        C.Answer(p.stem.split("__", 2)[2], C.claims(p.read_text(encoding="utf-8")))
        for p in sorted((RUNS / run / "raw").glob(f"{pair}__q*__r*.md"))
    ]
    path = FT_RUN / "writer" / f"{pair}__{PRODUCT}.jsonl"
    lines = [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]
    out.append(C.Answer(C.WRITER, tuple(str(d["sentence"]) for d in lines)))
    return out


def silent(pair: str) -> list[str]:
    rows = json.loads((FT_RUN / "rows.json").read_text(encoding="utf-8"))
    return [
        r["x_item"]
        for r in rows
        if r["pair"] == pair and r["product"] == PRODUCT and T.is_silent(r)
    ]


def transcript(unit: B.CueUnit, prefix: str) -> str:
    return "\n".join(
        f"{prefix}{i:04d} [{x.start}] {x.speaker}: {x.text}" for i, x in enumerate(unit.transcript)
    )


def job(run: str, pair: str, seed: int) -> tuple[str, dict[str, str]]:
    xm, ym = pair.split("__")
    x, y = load(xm), load(ym)
    ans = answers(run, pair)
    key = C.labels([a.source for a in ans], seed, pair)
    prompt = Path(f"prompts/{C.READER_PROMPT}.md").read_text(encoding="utf-8").strip()
    notes = "\n".join(b.render() for b in x.bullets)
    item_list = "\n".join(f"- {it.item_id} ({it.kind}): {it.text}" for it in items(x))
    n = sum(len(a.claims) for a in ans)
    parts = [
        f"# Follow-through answers: {xm} -> {ym}",
        f"Prompt `{C.READER_PROMPT}`. Judge all {n} claims of the {len(ans)} answers below. "
        f"Write your JSONL to the answer file you were given. Use only this file.",
        prompt,
        "---",
        f"## Meeting X ({xm}): the product's notes, every bullet\n\n"
        "`id [section: heading] (sub-point of parent) text`. Read a sub-point with its parent."
        f"\n\n```\n{notes}\n```",
        f"## Meeting X ({xm}): the items (decisions and tasks of the notes)\n\n{item_list}",
        f"## Meeting X ({xm}): the product's own transcript (lines E0000...)\n\n"
        f"```\n{transcript(x, 'E')}\n```",
        f"## Meeting Y ({ym}): the product's own transcript (lines L0000...)\n\n"
        f"```\n{transcript(y, 'L')}\n```",
        "## The answers",
        C.answers_block(ans, key),
    ]
    return "\n\n".join(parts) + "\n", key


def cmd_jobs(run: str, seed: int) -> None:
    out = RUNS / run / "jobs"
    out.mkdir(parents=True, exist_ok=True)
    keys = {}
    for pair in TEST_PAIRS:
        text, keys[pair] = job(run, pair, seed)
        (out / f"{pair}.md").write_text(text, encoding="utf-8")
        print(f"{pair}: {len(text)} chars, answers {keys[pair]}")
    meta = {"run": run, "seed": seed, "reader_prompt": C.READER_PROMPT, "key": keys}
    (RUNS / run / "key.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def cmd_score(run: str, seed: int, model: str) -> None:
    result: dict[str, Any] = {}
    for pair in TEST_PAIRS:
        ans = answers(run, pair)
        key = C.labels([a.source for a in ans], seed, pair)
        rows = {
            r: C.parse_rows(
                (RUNS / run / "readers" / r / f"{pair}.jsonl").read_text(encoding="utf-8")
            )
            for r in C.READERS
        }
        sil = silent(pair)
        result[pair] = {"silent_items": sil, "by_source": C.score(ans, key, rows, sil)}
        for src, s in result[pair]["by_source"].items():
            print(
                f"{pair} {src:8} claims {s['claims']:2} words {s['words']:3} "
                f"true(both) {s['true_both']} false(both) {s['false_both']} "
                f"false(any) {s['false_any']} agree {s['agree']} "
                f"silent a {s['silent_named']['a']} b {s['silent_named']['b']} "
                f"both {s['silent_named_both']} of {len(sil)}"
            )
    meta = {
        "run": run,
        "git_sha": _git_sha(),
        "model": model,
        "prompt_versions": [C.QUESTIONS_PROMPT, C.READER_PROMPT, T.WRITER_PROMPT],
        "seed": seed,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "pairs": list(TEST_PAIRS),
        "product": PRODUCT,
    }
    out = {"meta": meta, "result": result}
    (RUNS / run / "score.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("jobs", "score"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        p.add_argument("--seed", type=int, default=42)
        if name == "score":
            p.add_argument("--model", default="claude-opus-5-5")
    a = ap.parse_args()
    if a.cmd == "jobs":
        cmd_jobs(a.run, a.seed)
    else:
        cmd_score(a.run, a.seed, a.model)


if __name__ == "__main__":
    main()
