"""When does a single-call LLM judge go wrong? Tables from data already on disk.

    uv run --no-sync python scripts/judge_anatomy.py report [--split test|dev] [--seed 42] \
        [--json OUT]
    uv run --no-sync python scripts/judge_anatomy.py sample [--split test|dev] [--seed 42] [--n 40]
    uv run --no-sync python scripts/judge_anatomy.py agree A.jsonl B.jsonl
    uv run --no-sync python scripts/judge_anatomy.py twostage [--split test|dev|all] [--seed 42] \
        [--json OUT]
    uv run --no-sync python scripts/judge_anatomy.py fresh RUN_DIR RUN_DIR RUN_DIR [--seed 42] \
        [--model NAME] [--json OUT]

Three studies, each with the judge's verdict and a careful reference per row:

* ``notes``: E3 notes completeness. Judge = ``notes_completeness.v4``, one call per meeting and
  product scoring every gold item against every bullet, 3 passes (data/ami/results/e3). Reference =
  majority of three frozen-reader runs (data/private/reader/runs/test_consensus3.jsonl); on DEV
  (IS1008a) the v2 reader run dev_C_v2.jsonl.
* ``lists``: E1 list answers. Judge = ``judge_items.v1``, one call per answer scoring all its
  items, 1 pass. Reference = the adjudicated re-read (data/private/JUDGE_REREAD_e1.jsonl).
* ``answers``: the deletion study. Judge = ``judge.v1``, one call per answer, 1 pass. Reference =
  ``ref_v_gold`` in data/private/JUDGE_REREAD_deletion.jsonl.

``report`` prints direction, load, item kind, verdict class and stability tables with 95% intervals
from resampling whole meetings (notes) or questions (lists, answers). ``sample`` writes a seeded
sample of judge errors as one job file for cause labelling (data/private/reader/anatomy_jobs/).
``agree`` scores two labellers' JSONL outputs against each other. ``twostage`` scores the policy
"keep the judge's top-credit calls, read the rest carefully" (``J.ACCEPT``): its agreement with
the reference, the share of rows that need the careful reading, and the headline numbers under
the judge alone, the two-stage policy and the reference. The careful reading is taken from the
reference itself, so agreement is exact wherever the judge withholds credit. ``fresh`` breaks
that circle for the notes study: the second stage is a new careful reading (three run directories
of frozen-reader JSONL, notes TEST), scored as the first run alone and as the majority of three
against the same reference and against the original v2 reader run. No LLM, no network.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tenor.evals import judge_anatomy as J
from tenor.evals.judge_check import cohen_kappa
from tenor.evals.list_check import _f1, sign_test

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "data/private"
E3_RESULTS = ("20261109T014826Z", "20261109T063610Z", "20261109T072942Z")
READER = {"test": "test_consensus3.jsonl", "dev": "dev_C_v2.jsonl"}
JOBS = PRIVATE / "reader/anatomy_jobs"
LABEL = {"full": "full", "partial": "partial", "partly": "partial", "no": "none", "none": "none"}
CAUSES = ("missed_content", "credited_absent", "strength", "owner", "part_missing", "gold", "other")
CAUSE_TEXT = """\
- missed_content: the notes (or the answer) do say it, in other words or in a different place, and
  the judge did not find it, matched the wrong bullet, or did not link it to its gold item.
- credited_absent: the judge gave credit for content that is not there, or that does not answer
  the item or question.
- strength: the dispute is how firm the statement is (a mention, proposal or self-stated intention
  counted as a decision, an assigned task or a problem, or the reverse).
- owner: the dispute is who owns a task or who said something (names vs roles, joint owners).
- part_missing: the item has several parts or a detail; the dispute is whether a missing or extra
  part should cost full credit.
- gold: the gold item or gold answer is itself wrong, vague or self-contradictory.
- other: none of the above; say what in the note.
"""


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def notes_rows(split: str) -> list[dict[str, Any]]:
    """One row per gold item and product: the judge's passes, the reference, the prompt's load."""
    ref = {
        (r["meeting"], r["product"], r["gold_id"]): r
        for r in jsonl(PRIVATE / "reader/runs" / READER[split])
    }
    rows: list[dict[str, Any]] = []
    for name in E3_RESULTS:
        data = json.loads((ROOT / f"data/ami/results/e3/{name}.json").read_text(encoding="utf-8"))
        for rec in data["records"]:
            if rec["split"] != split:
                continue
            first = rec["passes"][0]
            bullets = {b["id"]: b["text"] for b in first["bullets"]}
            gold = first["gold"]
            per_cat = Counter(g["category"] for g in gold)
            seen: Counter[str] = Counter()
            for i, g in enumerate(gold):
                key = (rec["meeting_id"], rec["product"], g["id"])
                passes = [
                    next(v for v in p["gold_verdicts"] if v["id"] == g["id"]) for p in rec["passes"]
                ]
                verdicts = [v["coverage"] for v in passes]
                reference = LABEL[ref[key]["notes_verdict"]]
                judge = J.median_verdict(verdicts)
                overlap = J.content_overlap(g["text"], list(bullets.values()))
                rows.append(
                    {
                        "study": "notes",
                        "cluster": rec["meeting_id"],
                        "unit": f"{rec['meeting_id']}__{rec['product']}",
                        "id": "|".join(key),
                        "category": g["category"],
                        "text": g["text"],
                        "judge": judge,
                        "reference": reference,
                        "direction": J.direction(judge, reference, J.COVERAGE),
                        "passes": verdicts,
                        "unanimous": len(set(verdicts)) == 1,
                        "n_items": len(gold),
                        "n_bullets": len(bullets),
                        "third": J.third(i, len(gold)),
                        "half_in_category": "first"
                        if seen[g["category"]] * 2 < per_cat[g["category"]]
                        else "second",
                        "overlap": overlap,
                        "overlap_bin": J.overlap_bin(overlap),
                        "judge_reason": passes[0]["reason"],
                        "judge_bullets": [
                            f"{b} {bullets.get(b, '')}" for b in passes[0]["bullets"]
                        ],
                        "reference_evidence": ref[key].get("evidence_quote", ""),
                        "reference_reason": ref[key].get("reason", ""),
                    }
                )
                seen[g["category"]] += 1
    return rows


def lists_rows(split: str) -> list[dict[str, Any]]:
    """One row per answer item of E1: the judge's lenient 'right' and gold link vs the reference."""
    rows = []
    for r in jsonl(PRIVATE / "JUDGE_REREAD_e1.jsonl"):
        if r["split"] != split:
            continue
        j_right = bool(r["judge"]["gold"]) or bool(r["judge"]["in_transcript"])
        ref_right = bool(r["reference"]["right"])
        j_link, ref_link = bool(r["judge"]["gold"]), bool(r["reference"]["gold"])
        if j_right != ref_right:
            kind = "harsh" if ref_right else "lenient"
        elif j_link != ref_link:
            kind = "harsh" if ref_link else "lenient"
        else:
            kind = "same"
        rows.append(
            {
                "study": "lists",
                "cluster": r["qid"],
                "id": r["key"],
                "category": r["category"],
                "condition": r["condition"],
                "text": r["text"],
                "wrong": bool(r["judge_wrong"]),
                "direction": kind if r["judge_wrong"] else "same",
                "truth_flip": j_right != ref_right,
                "n_items": r["n_items"],
                "third": J.third(r["position"], r["n_items"]),
                "old_cause": r["cause"],
                "judge_reason": r["judge"]["reason"],
                "reference_evidence": r["blind_reader"].get("evidence", ""),
                "reference_reason": r["blind_reader"].get("note", ""),
                "judge": f"gold {r['judge']['gold']}, in transcript {r['judge']['in_transcript']}",
                "reference": f"gold {r['reference']['gold']}, right {r['reference']['right']}",
            }
        )
    return rows


def answers_rows(split: str) -> list[dict[str, Any]]:
    """One row per judged answer of the deletion study. DEV = questions on meeting IS1008a."""
    rows = []
    for r in jsonl(PRIVATE / "JUDGE_REREAD_deletion.jsonl"):
        if ("IS1008a" in r["qid"]) != (split == "dev"):
            continue
        rows.append(
            {
                "study": "answers",
                "cluster": r["qid"],
                "id": f"{r['qid']}|{r['system']}",
                "category": r["category"],
                "system": r["system"],
                "text": r["qid"],
                "judge": r["judge"],
                "reference": r["ref_v_gold"],
                "direction": J.direction(r["judge"], r["ref_v_gold"], J.CORRECTNESS),
                "judge_reason": r["judge_reason"],
                "reference_evidence": r.get("evidence", ""),
                "reference_reason": r.get("note", ""),
            }
        )
    return rows


def is_wrong(r: dict[str, Any]) -> bool:
    return bool(r["wrong"]) if "wrong" in r else r["direction"] != "same"


def rate(rows: list[dict[str, Any]], seed: int, hit: Any = is_wrong) -> dict[str, Any]:
    hits, n, lo, hi = J.cluster_rate([(r["cluster"], bool(hit(r))) for r in rows], seed)
    return {"hits": hits, "n": n, "rate": hits / n if n else None, "ci": [lo, hi]}


def by(
    rows: list[dict[str, Any]], key: str, seed: int, order: list[Any] | None = None
) -> dict[str, Any]:
    values = order or sorted({r[key] for r in rows}, key=str)
    return {str(v): rate([r for r in rows if r[key] == v], seed) for v in values}


def bins(rows: list[dict[str, Any]], key: str, seed: int) -> dict[str, Any]:
    """Rows in three groups of units, cut at the terciles of ``key`` (a feature, not an outcome)."""
    values = sorted({(r.get("unit", r["cluster"]), r[key]) for r in rows}, key=lambda x: x[1])
    cut1, cut2 = values[len(values) // 3][1], values[2 * len(values) // 3][1]
    cuts: tuple[tuple[str, Callable[[Any], bool]], ...] = (
        (f"< {cut1}", lambda v: v < cut1),
        (f"{cut1} to {cut2 - 1}", lambda v: cut1 <= v < cut2),
        (f">= {cut2}", lambda v: v >= cut2),
    )
    out = {}
    for name, test in cuts:
        out[name] = rate([r for r in rows if test(r[key])], seed)
    return out


def report(split: str, seed: int) -> dict[str, Any]:
    notes, lists, answers = notes_rows(split), lists_rows(split), answers_rows(split)
    out: dict[str, Any] = {"split": split, "seed": seed}
    out["direction"] = {
        name: {
            "wrong": rate(rows, seed),
            "harsh": rate(rows, seed, lambda r: r["direction"] == "harsh"),
            "lenient": rate(rows, seed, lambda r: r["direction"] == "lenient"),
            "harsh_share_of_wrong": rate(
                [r for r in rows if is_wrong(r)], seed, lambda r: r["direction"] == "harsh"
            ),
        }
        for name, rows in (("notes", notes), ("lists", lists), ("answers", answers))
    }
    units: dict[str, list[dict[str, Any]]] = {}
    for r in notes:
        units.setdefault(r["unit"], []).append(r)
    unit_rate = [sum(is_wrong(r) for r in u) / len(u) for u in units.values()]
    out["load"] = {
        "notes_by_items_in_prompt": bins(notes, "n_items", seed),
        "notes_by_bullets_in_prompt": bins(notes, "n_bullets", seed),
        "notes_units": len(units),
        "notes_spearman_items": J.spearman(
            [float(u[0]["n_items"]) for u in units.values()], unit_rate
        ),
        "notes_spearman_bullets": J.spearman(
            [float(u[0]["n_bullets"]) for u in units.values()], unit_rate
        ),
        "notes_spearman_items_x_bullets": J.spearman(
            [float(u[0]["n_items"] * u[0]["n_bullets"]) for u in units.values()], unit_rate
        ),
        "notes_by_third": by(notes, "third", seed, ["first", "middle", "last"]),
        "notes_by_half_in_category": by(notes, "half_in_category", seed, ["first", "second"]),
        "lists_by_items_in_answer": bins(lists, "n_items", seed),
        "lists_by_third": by(lists, "third", seed, ["first", "middle", "last"]),
    }
    out["kind"] = {
        "notes_by_category": by(notes, "category", seed, ["decision", "action", "problem"]),
        "notes_by_overlap": by(notes, "overlap_bin", seed, ["low", "mid", "high"]),
        "notes_harsh_by_overlap": {
            b: rate(
                [r for r in notes if r["overlap_bin"] == b],
                seed,
                lambda r: r["direction"] == "harsh",
            )
            for b in ("low", "mid", "high")
        },
        "notes_overlap_by_reference": {
            ref: by(
                [r for r in notes if r["reference"] == ref],
                "overlap_bin",
                seed,
                ["low", "mid", "high"],
            )
            for ref in ("full", "partial", "none")
        },
        "lists_by_category": by(lists, "category", seed),
        "lists_by_condition": by(lists, "condition", seed),
        "answers_by_category": by(answers, "category", seed),
        "answers_by_system": by(answers, "system", seed),
    }
    conf = Counter((r["reference"], r["judge"]) for r in notes)
    conf_a = Counter((r["reference"], r["judge"]) for r in answers)
    wrong_notes = [r for r in notes if is_wrong(r)]
    wrong_answers = [r for r in answers if is_wrong(r)]
    out["verdict_class"] = {
        "notes_confusion_ref_judge": {f"{a}>{b}": n for (a, b), n in sorted(conf.items())},
        "notes_wrong_by_reference": by(notes, "reference", seed, ["full", "partial", "none"]),
        "notes_wrong_by_judge_verdict": by(notes, "judge", seed, ["full", "partial", "none"]),
        "notes_errors_touching_partial": rate(
            wrong_notes, seed, lambda r: "partial" in (r["judge"], r["reference"])
        ),
        "answers_confusion_ref_judge": {f"{a}>{b}": n for (a, b), n in sorted(conf_a.items())},
        "answers_wrong_by_reference": by(answers, "reference", seed, list(J.CORRECTNESS)),
        "answers_wrong_by_judge_verdict": by(answers, "judge", seed, list(J.CORRECTNESS)),
        "answers_errors_touching_partial": rate(
            wrong_answers, seed, lambda r: "partially_correct" in (r["judge"], r["reference"])
        ),
        "lists_errors_truth_flip": rate(
            [r for r in lists if is_wrong(r)], seed, lambda r: r["truth_flip"]
        ),
    }
    one_pass = [
        dict(r, direction=J.direction(r["passes"][0], r["reference"], J.COVERAGE)) for r in notes
    ]
    out["stability"] = {
        "unanimous": rate(notes, seed, lambda r: r["unanimous"]),
        "wrong_when_unanimous": rate([r for r in notes if r["unanimous"]], seed),
        "wrong_when_split": rate([r for r in notes if not r["unanimous"]], seed),
        "wrong_single_pass": rate(one_pass, seed),
        "wrong_majority_of_three": rate(notes, seed),
        "share_of_errors_that_were_unanimous": rate(wrong_notes, seed, lambda r: r["unanimous"]),
    }
    return out


def fmt(cell: dict[str, Any]) -> str:
    if not cell["n"]:
        return "none"
    lo, hi = cell["ci"]
    ci = f" [{lo:.0%}, {hi:.0%}]" if lo is not None else ""
    kappa = f", kappa {cell['kappa']:.2f}" if "kappa" in cell else ""
    return f"{cell['hits']} of {cell['n']} ({cell['rate']:.0%}){ci}{kappa}"


def show(node: Any, depth: int = 0) -> None:
    for k, v in node.items():
        if isinstance(v, dict) and "hits" in v:
            print(f"{'  ' * depth}{k}: {fmt(v)}")
        elif isinstance(v, dict):
            print(f"{'  ' * depth}{k}:")
            show(v, depth + 1)
        else:
            print(f"{'  ' * depth}{k}: {v if not isinstance(v, float) else round(v, 3)}")


def sample(split: str, seed: int, n: int) -> None:
    """A seeded sample of judge errors, in proportion across the three studies, as one job file."""
    pools = {
        "notes": [r for r in notes_rows(split) if is_wrong(r)],
        "lists": [r for r in lists_rows(split) if is_wrong(r)],
        "answers": [r for r in answers_rows(split) if is_wrong(r)],
    }
    total = sum(map(len, pools.values()))
    rng = random.Random(seed)
    picked: list[dict[str, Any]] = []
    for pool in pools.values():
        k = min(len(pool), max(1, round(n * len(pool) / total)))
        picked += rng.sample(sorted(pool, key=lambda r: r["id"]), k)
    rng.shuffle(picked)
    lines = [
        f"# Judge-error causes: {len(picked)} cases ({split}, seed {seed})",
        "",
        "Each case is one verdict where a single-call LLM judge differed from a careful reference",
        "reading. Take the reference as right. Decide the main reason the judge got it wrong, from",
        "this fixed list (pick exactly one):",
        "",
        CAUSE_TEXT,
        "Write one JSON line per case to your output file, in order:",
        '`{"case": "C01", "cause": "<one of the list>", "note": "<a few words>"}`',
        "Judge only from what is shown. Do not open other files.",
        "",
    ]
    key = []
    for i, r in enumerate(picked, 1):
        case = f"C{i:02d}"
        key.append(
            {"case": case, "study": r["study"], "id": r["id"], "old_cause": r.get("old_cause")}
        )
        lines += [
            f"## {case} ({r['study']})",
            "",
            f"- Item: {r['text']}",
            f"- Kind: {r['category']}",
            f"- Judge's verdict: {r['judge']}",
            f"- Judge's reason: {r['judge_reason']}",
        ]
        if r.get("judge_bullets"):
            lines.append("- Bullets the judge matched: " + " / ".join(r["judge_bullets"]))
        lines += [
            f"- Reference verdict: {r['reference']}",
            f"- Reference evidence: {r['reference_evidence']}",
            f"- Reference reason: {r['reference_reason']}",
            "",
        ]
    JOBS.mkdir(parents=True, exist_ok=True)
    stem = f"causes_{split}_seed{seed}"
    (JOBS / f"{stem}.md").write_text("\n".join(lines), encoding="utf-8")
    (JOBS / f"{stem}.key.json").write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")
    print(f"{len(picked)} cases ({Counter(k['study'] for k in key)}) in {JOBS / (stem + '.md')}")


def agree(a_path: Path, b_path: Path) -> None:
    a = {r["case"]: r["cause"] for r in jsonl(a_path)}
    b = {r["case"]: r["cause"] for r in jsonl(b_path)}
    cases = sorted(a.keys() & b.keys())
    bad = [c for c in cases if a[c] not in CAUSES or b[c] not in CAUSES]
    if bad or len(cases) != len(a) or len(cases) != len(b):
        raise SystemExit(f"labels do not line up or use unknown causes: {bad}")
    same = sum(a[c] == b[c] for c in cases)
    kappa = cohen_kappa([a[c] for c in cases], [b[c] for c in cases])
    print(f"agree on {same} of {len(cases)} ({same / len(cases):.0%}), kappa {kappa:.2f}")
    print("reader A:", dict(Counter(a.values()).most_common()))
    print("reader B:", dict(Counter(b.values()).most_common()))
    print("agreed:", dict(Counter(a[c] for c in cases if a[c] == b[c]).most_common()))


SYSTEMS = {
    "A": "granola_chat",
    "B": "notes_only",
    "B'": "notes_only_wispr",
    "C": "notes_facts",
    "D": "notes_facts_fallback",
}
DELETION_RUN = ROOT / "data/ami/results/deletion/20261105T211504Z.deletion.jsonl"
E1_RUN = ROOT / "data/ami/results/e1/20261106T002704Z.json"
VIEWS = ("judge", "two_stage", "reference")


def pick(split: str, loader: Any) -> list[dict[str, Any]]:
    """Rows of one split, or of both for 'all'."""
    return [r for s in (("dev", "test") if split == "all" else (split,)) for r in loader(s)]


def test_line(a: dict[str, float | None], b: dict[str, float | None]) -> str:
    t = sign_test(a, b)
    return f"{t['wins']}-{t['losses']}, p = {t['sign_p']:.3f}"


def notes_headline(rows: list[dict[str, Any]], view: str) -> dict[str, Any]:
    """E3 coverage per product and the per-meeting sign test, reading verdicts from ``view``."""
    per: dict[str, dict[str, float | None]] = {"granola": {}, "wispr": {}}
    for product, by_meeting in per.items():
        for m in sorted({r["cluster"] for r in rows}):
            by_meeting[m] = J.weighted_coverage(
                [r[view] for r in rows if r["unit"] == f"{m}__{product}"]
            )
    return {
        "coverage_granola": J.weighted_coverage(
            [r[view] for r in rows if r["unit"].endswith("__granola")]
        ),
        "coverage_wispr": J.weighted_coverage(
            [r[view] for r in rows if r["unit"].endswith("__wispr")]
        ),
        "wispr_vs_granola_by_meeting": test_line(per["granola"], per["wispr"]),
    }


def notes_two_stage(split: str, seed: int) -> dict[str, Any]:
    """E3 coverage per product and the per-meeting comparison under the three views."""
    rows = pick(split, notes_rows)
    for r in rows:
        r["route"] = J.two_stage(r["judge"], J.ACCEPT["notes"])
        r["two_stage"] = r["judge"] if r["route"] == "judge" else r["reference"]
    out: dict[str, Any] = {
        "agreement": rate(rows, seed, lambda r: r["two_stage"] == r["reference"]),
        "judge_agreement": rate(rows, seed, lambda r: r["judge"] == r["reference"]),
        "careful_share": rate(rows, seed, lambda r: r["route"] == "careful"),
        "headline": {},
    }
    for view in VIEWS:
        out["headline"][view] = notes_headline(rows, view)
    return out


def lists_two_stage(split: str, seed: int) -> dict[str, Any]:
    """E1 recall, lenient precision and F1 per condition, and the paired tests, under the views."""
    e1 = json.loads(E1_RUN.read_text(encoding="utf-8"))["records"]
    n_gold = {r["qid"]: len(r["gold"]) for r in e1}
    rows = [
        r for r in jsonl(PRIVATE / "JUDGE_REREAD_e1.jsonl") if split == "all" or r["split"] == split
    ]
    for r in rows:
        j = (r["judge"]["gold"], bool(r["judge"]["gold"]) or bool(r["judge"]["in_transcript"]))
        ref = (r["reference"]["gold"], bool(r["reference"]["right"]))
        cls = J.list_class(r["judge"]["gold"], bool(r["judge"]["in_transcript"]))
        r["route"] = J.two_stage(cls, J.ACCEPT["lists"])
        r["views"] = {
            "judge": j,
            "reference": ref,
            "two_stage": j if r["route"] == "judge" else ref,
        }
        r["cluster"] = r["qid"]
    qids = sorted({r["qid"] for r in rows})

    def metrics(view: str, cond: str) -> dict[str, dict[str, float | None]]:
        out: dict[str, dict[str, float | None]] = {"recall": {}, "precision": {}, "f1": {}}
        for q in qids:
            items = [
                r["views"][view]
                for r in rows
                if r["qid"] == q
                and (
                    r["condition"] == cond
                    or (cond == "LD" and r["condition"] == "L" and r["ld_kept"])
                )
            ]
            covered = {g for gold, _ in items for g in gold}
            recall = len(covered) / n_gold[q] if n_gold[q] else 0.0
            precision = sum(right for _, right in items) / len(items) if items else None
            out["recall"][q], out["precision"][q] = recall, precision
            out["f1"][q] = _f1(precision, recall)
        return out

    def mean(xs: dict[str, float | None]) -> float:
        vals = [x for x in xs.values() if x is not None]
        return sum(vals) / len(vals)

    same = lambda r: r["views"]["two_stage"] == r["views"]["reference"]  # noqa: E731
    out: dict[str, Any] = {
        "questions": len(qids),
        "agreement": rate(rows, seed, same),
        "judge_agreement": rate(rows, seed, lambda r: not r["judge_wrong"]),
        "careful_share": rate(rows, seed, lambda r: r["route"] == "careful"),
        "headline": {},
    }
    for view in VIEWS:
        m = {c: metrics(view, c) for c in ("C", "L", "LD")}
        out["headline"][view] = {
            **{f"{c}_{k}": mean(m[c][k]) for c in m for k in ("recall", "precision", "f1")},
            "L_vs_C_recall": test_line(m["C"]["recall"], m["L"]["recall"]),
            "L_vs_C_f1": test_line(m["C"]["f1"], m["L"]["f1"]),
            "LD_vs_C_f1": test_line(m["C"]["f1"], m["LD"]["f1"]),
        }
    return out


def answers_two_stage(split: str, seed: int) -> dict[str, Any]:
    """Deletion study: correct and not-wrong per system, paired tests, confident errors."""
    abstain = {(r["qid"], r["system"]): bool(r["answer"]["abstain"]) for r in jsonl(DELETION_RUN)}
    rows = pick(split, answers_rows)
    for r in rows:
        r["route"] = J.two_stage(r["judge"], J.ACCEPT["answers"])
        r["two_stage"] = r["judge"] if r["route"] == "judge" else r["reference"]
    out: dict[str, Any] = {
        "questions": len({r["cluster"] for r in rows}),
        "agreement": rate(rows, seed, lambda r: r["two_stage"] == r["reference"]),
        "judge_agreement": rate(rows, seed, lambda r: r["judge"] == r["reference"]),
        "careful_share": rate(rows, seed, lambda r: r["route"] == "careful"),
        "headline": {},
    }
    for view in VIEWS:
        correct: dict[str, dict[str, float | None]] = {}
        not_wrong: dict[str, dict[str, float | None]] = {}
        confident: dict[str, int] = {}
        for label, system in SYSTEMS.items():
            mine = [r for r in rows if r["system"] == system]
            correct[label] = {r["cluster"]: float(r[view] == "correct") for r in mine}
            not_wrong[label] = {r["cluster"]: float(r[view] != "incorrect") for r in mine}
            confident[label] = sum(
                r[view] == "incorrect" and (label == "A" or not abstain[(r["cluster"], system)])
                for r in mine
            )
        out["headline"][view] = {
            **{f"{k}_correct": sum(v.values()) / len(v) for k, v in correct.items()},  # type: ignore[arg-type]
            **{f"{k}_not_wrong": sum(v.values()) / len(v) for k, v in not_wrong.items()},  # type: ignore[arg-type]
            "A_vs_B_correct": test_line(correct["B"], correct["A"]),
            "D_vs_C_correct": test_line(correct["C"], correct["D"]),
            "A_vs_D_not_wrong": test_line(not_wrong["D"], not_wrong["A"]),
            "A_vs_D_correct": test_line(correct["D"], correct["A"]),
            "B_vs_Bprime_correct": test_line(correct["B'"], correct["B"]),
            "confident_errors": " / ".join(f"{k} {v}" for k, v in confident.items()),
        }
    return out


ORIGINAL_V2 = PRIVATE / "reader/runs/test_v2_all.jsonl"


def reader_run(path: Path) -> dict[str, str]:
    """Notes verdicts of one reader run (a JSONL file or a directory of them), by row id."""
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    out: dict[str, str] = {}
    for f in files:
        for r in jsonl(f):
            key = "|".join((r["meeting"], r["product"], r["gold_id"]))
            if key in out:
                raise SystemExit(f"{path}: {key} appears twice")
            out[key] = LABEL[r["notes_verdict"]]
    return out


def pair(rows: list[dict[str, Any]], a: str, b: str, seed: int) -> dict[str, Any]:
    """Agreement of two verdict columns over ``rows``, with Cohen's kappa."""
    cell = rate(rows, seed, lambda r: r[a] == r[b])
    cell["kappa"] = cohen_kappa([r[a] for r in rows], [r[b] for r in rows])
    return cell


def fresh(run_dirs: list[Path], seed: int) -> dict[str, Any]:
    """The notes two-stage policy with a new careful reading as its second stage (TEST)."""
    if len(run_dirs) != 3:
        raise SystemExit("need exactly three fresh reader runs")
    rows = notes_rows("test")
    runs = [reader_run(d) for d in run_dirs]
    original = reader_run(ORIGINAL_V2)
    names = [f"fresh_{i + 1}" for i in range(3)]
    for r in rows:
        r["route"] = J.two_stage(r["judge"], J.ACCEPT["notes"])
        r["original_v2"] = original[r["id"]]
    careful = [r for r in rows if r["route"] == "careful"]
    missing = {
        n: [r["id"] for r in careful if r["id"] not in run]
        for n, run in zip(names, runs, strict=True)
    }
    if any(missing.values()):
        raise SystemExit(f"fresh runs lack routed items: {missing}")
    for r in careful:
        for n, run in zip(names, runs, strict=True):
            r[n] = run[r["id"]]
        r["fresh_majority"] = J.median_verdict([r[n] for n in names])
    views = [*names, "fresh_majority"]
    for r in rows:
        for v in views:
            r[f"two_stage_{v}"] = J.two_stage_verdict(r["judge"], r.get(v), J.ACCEPT["notes"])
    accepted = [r for r in rows if r["route"] == "judge"]
    out: dict[str, Any] = {
        "seed": seed,
        "runs": [str(d.relative_to(ROOT)) if d.is_absolute() else str(d) for d in run_dirs],
        "items": {"all": len(rows), "careful": len(careful), "accepted": len(accepted)},
        "accepted_wrong": {
            **rate(accepted, seed),
            "directions": dict(Counter(r["direction"] for r in accepted if is_wrong(r))),
        },
        "two_stage_agreement_with_reference": {
            v: pair(rows, f"two_stage_{v}", "reference", seed) for v in views
        },
        "circular_two_stage_agreement": rate(
            rows,
            seed,
            lambda r: (r["judge"] if r["route"] == "judge" else r["reference"]) == r["reference"],
        ),
        "careful_items": {
            "vs_reference": {v: pair(careful, v, "reference", seed) for v in views},
            "vs_original_v2": {v: pair(careful, v, "original_v2", seed) for v in views},
            "original_v2_vs_reference": pair(careful, "original_v2", "reference", seed),
            "between_fresh_runs": {
                f"{a}_vs_{b}": pair(careful, a, b, seed)
                for i, a in enumerate(names)
                for b in names[i + 1 :]
            },
            "unanimous": rate(careful, seed, lambda r: len({r[n] for n in names}) == 1),
            "direction_vs_reference": {
                v: dict(Counter(J.direction(r[v], r["reference"], J.COVERAGE) for r in careful))
                for v in views
            },
            "direction_vs_original_v2": {
                v: dict(Counter(J.direction(r[v], r["original_v2"], J.COVERAGE) for r in careful))
                for v in views
            },
            "changes_vs_reference": dict(
                Counter(
                    f"{r['reference']} -> {r['fresh_majority']}"
                    for r in careful
                    if r["fresh_majority"] != r["reference"]
                )
            ),
        },
        "headline": {
            "judge": notes_headline(rows, "judge"),
            **{f"two_stage_{v}": notes_headline(rows, f"two_stage_{v}") for v in views},
            "reference": notes_headline(rows, "reference"),
        },
    }
    return out


def twostage(split: str, seed: int) -> dict[str, Any]:
    return {
        "split": split,
        "seed": seed,
        "rule": {k: sorted(v) for k, v in J.ACCEPT.items()},
        "notes": notes_two_stage(split, seed),
        "lists": lists_two_stage(split, seed),
        "answers": answers_two_stage(split, seed),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("report", "sample"):
        p = sub.add_parser(name)
        p.add_argument("--split", choices=("dev", "test"), default="test")
        p.add_argument("--seed", type=int, default=42)
    p = sub.add_parser("twostage")
    p.add_argument("--split", choices=("dev", "test", "all"), default="test")
    p.add_argument("--seed", type=int, default=42)
    for name in ("report", "twostage"):
        sub.choices[name].add_argument("--json", type=Path)
    sub.choices["sample"].add_argument("--n", type=int, default=40)
    p = sub.add_parser("fresh")
    p.add_argument("runs", type=Path, nargs=3)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--model", default="not recorded")
    p.add_argument("--json", type=Path)
    p = sub.add_parser("agree")
    p.add_argument("a", type=Path)
    p.add_argument("b", type=Path)
    args = ap.parse_args()
    if args.cmd in ("report", "twostage", "fresh"):
        if args.cmd == "fresh":
            out = fresh(args.runs, args.seed)
            out["reader"] = {"prompt": "notes_reader.v2", "model": args.model}
        else:
            out = (report if args.cmd == "report" else twostage)(args.split, args.seed)
        show(out)
        if args.json:
            sha = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
            ).stdout.strip()
            out["meta"] = {"git": sha, "date": datetime.now(UTC).isoformat(timespec="seconds")}
            args.json.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    elif args.cmd == "sample":
        sample(args.split, args.seed, args.n)
    else:
        agree(args.a, args.b)


if __name__ == "__main__":
    main()
