"""Re-check the three front-page figures of the since-last-time writer with new readers.

The figures, as first reported:

1. ``not_mentioned`` sentences judged true: writer v3 on the FRESH pairs (``swfresh_v3``).
2. ``changed`` sentences judged true: writers v1 and v2 on the 36 pairs (``sw36_v1``, ``sw36_v2``).
3. Silent items a true sentence names: v3 on the FRESH pairs against ``fates_fresh``.

``dump`` writes the jobs, every one built from the existing job files of those runs, with
nothing in them from an earlier verdict:

- ``jobs/sentence/<id>.md``: one per sentence, the run's own checker job (prompt
  ``since_last_time_check.v1`` unchanged, X's items, Y's full transcript) cut down to that one
  sentence.
- ``jobs/fates/<pair>.md``: one per FRESH pair with a silent item, the pair's own fates job
  (prompt ``item_fates.v1`` unchanged, roster, Y's full transcript) cut down to its silent items.
- ``jobs/naming/<pair>.md``: one per such pair with a kept v3 sentence: the silent items and
  the pair's kept sentences, prompt ``sentence_names_item.v1``.

Two new agents (``a``, ``b``) answer every job, each told to use only its job file; answers go to
``<a|b>/<kind>/<name>.jsonl``. ``score`` reads them and writes ``rows.json`` and ``score.json``.
A figure re-checked is strict: both new readers say yes. No LLM and no network.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

READERS = Path("data/private/reader")
RUNS = READERS / "sw_runs"
SW_JOBS = READERS / "sw_jobs"
FATES_JOBS = READERS / "fates_jobs"
FATES_RUN = READERS / "fates_runs" / "fates_fresh_20261114"
OUT = READERS / "dots_recheck_20261115"
NAMING_PROMPT = "sentence_names_item.v1"
LETTERS = ("a", "b")
FIGURES = {  # figure -> (run, sentence kind)
    "not_mentioned_true": (("swfresh_v3_20261114", "not_mentioned"),),
    "changed_true": (("sw36_v1_20261114", "changed"), ("sw36_v2_20261114", "changed")),
}
FRESH_RUN = "swfresh_v3_20261114"
SILENT = "changed_silently"

Row = Mapping[str, Any]


def is_silent(fate: str, needed: str) -> bool:
    """Same rule as ``follow_through.is_silent``."""
    return fate == SILENT or (fate == "not_mentioned" and needed == "yes")


def sentence_id(run: str, row: Row) -> str:
    return f"{'_'.join(run.split('_')[:2])}__{row['unit']}__{row['n']}"


def sentence_blocks(job: str) -> dict[int, str]:
    """The numbered sentence blocks of a checker job, by number."""
    tail = job.split("## The sentences to check\n\n", 1)[1]
    parts = re.split(r"(?m)^(?=\d+\. \()", tail)
    return {int(p.split(".", 1)[0]): p.rstrip("\n") for p in parts if p.strip()}


def one_sentence_job(job: str, n: int, sentence: str, answer: str) -> str:
    """``job`` with only sentence ``n`` (renumbered 1) and the answer path replaced."""
    block = sentence_blocks(job)[n]
    if sentence not in block:
        raise ValueError(f"sentence {n} does not match the job file")
    head, _ = job.split("## The sentences to check\n\n", 1)
    head = re.sub(
        r"(?m)^Prompt `([^`]+)`\. \d+ sentences\. Write your answer \(JSONL, one line per "
        r"sentence\) to `[^`]+`\.",
        lambda m: (
            f"Prompt `{m.group(1)}`. 1 sentences. Write your answer (JSONL, one line "
            f"per sentence) to `{answer}`."
        ),
        head,
        count=1,
    )
    if answer not in head:
        raise ValueError("job header not in the expected form")
    return head + "## The sentences to check\n\n" + re.sub(r"^\d+\.", "1.", block) + "\n"


def item_sections(job: str) -> dict[str, tuple[str, str]]:
    """``### <id> (kind): text`` sections of a fates job: id -> (heading text, whole section)."""
    x_part = job.split("\n## Meeting Y", 1)[0]
    out: dict[str, tuple[str, str]] = {}
    for sec in re.split(r"(?m)^(?=### )", x_part)[1:]:
        m = re.match(r"### (\w+) \(\w+\): (.*)", sec)
        if not m:
            raise ValueError(f"unexpected item heading: {sec[:60]!r}")
        out[m.group(1)] = (m.group(2), sec.rstrip("\n") + "\n\n")
    return out


def silent_fates_job(job: str, ids: Sequence[str], answer: str) -> str:
    """``job`` with only the items ``ids`` and the answer path replaced."""
    secs = item_sections(job)
    pre = job.split("\n### ", 1)[0] + "\n\n"
    post = "## Meeting Y" + job.split("\n## Meeting Y", 1)[1]
    pre = re.sub(
        r"(?m)^Prompt `([^`]+)`\. Classify all \d+ items: [^.]*\. Write your answer "
        r"\(JSONL, one line per item\) to `[^`]+`\.",
        lambda m: (
            f"Prompt `{m.group(1)}`. Classify all {len(ids)} items: {', '.join(ids)}. "
            f"Write your answer (JSONL, one line per item) to `{answer}`."
        ),
        pre,
        count=1,
    )
    if answer not in pre:
        raise ValueError("fates job header not in the expected form")
    return pre + "".join(secs[i][1] for i in ids) + post


def naming_job(pair: str, items: Sequence[tuple[str, str]], sentences: Sequence[str]) -> str:
    prompt = Path(f"prompts/{NAMING_PROMPT}.md").read_text(encoding="utf-8").strip()
    answer = OUT / "<your reader letter: a or b>" / "naming" / f"{pair}.jsonl"
    listed = "\n".join(f"- {i}: {text}" for i, text in items)
    shown = "\n".join(f"{n}. {s}" for n, s in enumerate(sentences, start=1))
    return (
        f"# Does a sentence name the item: {pair}\n\nPrompt `{NAMING_PROMPT}`. "
        f"{len(items)} items. Write your answer (JSONL, one line per item) to `{answer}`. "
        f"Use only this file.\n\n{prompt}\n\n---\n\n## Items of meeting X\n\n{listed}\n\n"
        f"## The sentences\n\n{shown}\n"
    )


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def silent_items() -> list[Row]:
    return [
        r
        for r in load(FATES_RUN / "rows.json")["items"]
        if is_silent(r["fate"], r["needed_followup"])
    ]


def kept(run: str) -> list[Row]:
    return [r for r in load(RUNS / run / "rows.json") if r["status"] == "ok"]


def dump() -> None:
    for kind in ("sentence", "fates", "naming"):
        (OUT / "jobs" / kind).mkdir(parents=True, exist_ok=True)
        for c in LETTERS:
            (OUT / c / kind).mkdir(parents=True, exist_ok=True)
    n = 0
    for specs in FIGURES.values():
        for run, kind in specs:
            for r in kept(run):
                if r["kind"] != kind:
                    continue
                sid = sentence_id(run, r)
                job = (SW_JOBS / run / "check" / f"{r['unit']}.md").read_text(encoding="utf-8")
                answer = f"{OUT}/<your reader letter: a or b>/sentence/{sid}.jsonl"
                text = one_sentence_job(job, int(r["n"]), str(r["sentence"]), answer)
                (OUT / "jobs" / "sentence" / f"{sid}.md").write_text(text, encoding="utf-8")
                n += 1
    by_pair: dict[str, list[str]] = {}
    for r in silent_items():
        by_pair.setdefault(r["pair"], []).append(r["x_item"])
    fresh = kept(FRESH_RUN)
    n_naming = 0
    for pair, ids in sorted(by_pair.items()):
        job = (FATES_JOBS / f"{pair}.md").read_text(encoding="utf-8")
        answer = f"{OUT}/<your reader letter: a or b>/fates/{pair}.jsonl"
        (OUT / "jobs" / "fates" / f"{pair}.md").write_text(
            silent_fates_job(job, ids, answer), encoding="utf-8"
        )
        sents = [
            str(r["sentence"]) for r in sorted(fresh, key=lambda r: r["n"]) if r["unit"] == pair
        ]
        if sents:
            secs = item_sections(job)
            items = [(i, secs[i][0]) for i in ids]
            (OUT / "jobs" / "naming" / f"{pair}.md").write_text(
                naming_job(pair, items, sents), encoding="utf-8"
            )
            n_naming += 1
    print(f"{n} sentence jobs, {len(by_pair)} fates jobs, {n_naming} naming jobs in {OUT / 'jobs'}")


def answers(kind: str, name: str) -> dict[str, list[Row]]:
    out = {}
    for c in LETTERS:
        p = OUT / c / kind / f"{name}.jsonl"
        out[c] = [json.loads(s) for s in p.read_text(encoding="utf-8").splitlines() if s.strip()]
    return out


def score() -> None:
    rows: dict[str, list[dict[str, Any]]] = {"sentences": [], "silent_items": []}
    strict_new: dict[tuple[str, str, int], bool] = {}
    result: dict[str, Any] = {}
    for fig, specs in FIGURES.items():
        orig = new = agree = 0
        total = 0
        for run, kind in specs:
            for r in kept(run):
                if r["kind"] != kind:
                    continue
                sid = sentence_id(run, r)
                got = answers("sentence", sid)
                v = {c: got[c][0]["supported"] for c in LETTERS}
                row = {
                    "id": sid,
                    "run": run,
                    "unit": r["unit"],
                    "n": r["n"],
                    "sentence": r["sentence"],
                    "orig_a": r["supported_a"],
                    "orig_b": r["supported_b"],
                    "orig_strict": r["strict"],
                    "new_a": v["a"],
                    "new_b": v["b"],
                    "new_strict": v["a"] == v["b"] == "yes",
                    "new_x_items": {c: got[c][0].get("x_items", []) for c in LETTERS},
                    "new_y_lines": {c: got[c][0].get("y_lines", []) for c in LETTERS},
                    "new_reason": {c: got[c][0].get("reason", "") for c in LETTERS},
                }
                rows["sentences"].append(row)
                strict_new[(run, r["unit"], int(r["n"]))] = bool(row["new_strict"])
                total += 1
                orig += bool(r["strict"])
                new += bool(row["new_strict"])
                agree += v["a"] == v["b"]
        result[fig] = {
            "sentences": total,
            "orig_true": orig,
            "new_true": new,
            "new_readers_agree": agree,
        }
    fresh = kept(FRESH_RUN)
    covered_orig = {(r["unit"], i) for r in fresh if r["strict"] for i in r["x_items"]}
    still = covered_new = fate_agree = 0
    items = silent_items()
    for it in items:
        pair, xid = it["pair"], it["x_item"]
        f = {
            c: next(a for a in rows_ if a["x_item"] == xid)
            for c, rows_ in answers("fates", pair).items()
        }
        silent = {c: is_silent(f[c]["fate"], f[c]["needed_followup"]) for c in LETTERS}
        named: dict[str, list[int]] = {c: [] for c in LETTERS}
        if (OUT / "a" / "naming" / f"{pair}.jsonl").exists():
            nm = answers("naming", pair)
            named = {c: next(a for a in nm[c] if a["x_item"] == xid)["named_by"] for c in LETTERS}
        pair_ns = sorted(int(r["n"]) for r in fresh if r["unit"] == pair)
        both_named = set(named["a"]) & set(named["b"])
        true_named = [
            k for k in both_named if strict_new.get((FRESH_RUN, pair, pair_ns[k - 1]), False)
        ]
        both_silent = silent["a"] and silent["b"]
        cov = both_silent and bool(true_named)
        still += both_silent
        covered_new += cov
        fate_agree += f["a"]["fate"] == f["b"]["fate"]
        rows["silent_items"].append(
            {
                "pair": pair,
                "x_item": xid,
                "orig_fate": it["fate"],
                "orig_covered": (pair, xid) in covered_orig,
                "new_fate": {c: f[c]["fate"] for c in LETTERS},
                "new_needed": {c: f[c]["needed_followup"] for c in LETTERS},
                "new_silent": silent,
                "new_y_lines": {c: f[c].get("y_lines", []) for c in LETTERS},
                "new_reason": {c: f[c].get("reason", "") for c in LETTERS},
                "named_by": named,
                "new_covered": cov,
            }
        )
    result["silent_items_covered"] = {
        "silent_items": len(items),
        "orig_covered": sum(r["orig_covered"] for r in rows["silent_items"]),
        "still_silent_both_new_readers": still,
        "new_covered": covered_new,
        "new_covered_of_still_silent": f"{covered_new} of {still}",
        "new_readers_same_fate": fate_agree,
        "naming_readers_agree": sum(
            set(r["named_by"]["a"]) == set(r["named_by"]["b"]) for r in rows["silent_items"]
        ),
    }
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    meta = {
        "git_sha": sha,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": "claude-opus-5-5 (new agents, one per job per reader)",
        "prompt_versions": ["since_last_time_check.v1", "item_fates.v1", NAMING_PROMPT],
        "seed": None,
        "sources": [*(r for s in FIGURES.values() for r, _ in s), str(FATES_RUN)],
    }
    (OUT / "rows.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    (OUT / "score.json").write_text(
        json.dumps({"meta": meta, "result": result}, indent=1) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("dump", "score"))
    {"dump": dump, "score": score}[ap.parse_args().command]()


if __name__ == "__main__":
    main()
