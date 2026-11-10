"""Heard twice, wider scope: the two products' transcripts compared directly (no human truth).

    uv run --no-sync python scripts/heard_twice_wide.py [--seed 42] [--examples 10]

Three sets of recordings, each heard by Granola (hearing A) and Wispr Flow (hearing B):

- ``ami_test``: E4's eleven AMI meetings. The truth-free comparison is also checked against
  the truth-based result of ``scripts/heard_twice.py`` (recomputed here from the same inputs);
- ``ami_dev``: IS1008a;
- ``council``: the Seattle council segments of ``data/council/recording_ids.txt`` for which
  both products' transcripts are in the synced stores. For every segment, each product's
  transcript and notes are also checked against the checkable values of the official record
  (``data/council/legistar``: matter number, vote counts, numbers and dates in the title and
  the action text).

Writes under ``data/private/heard_twice/`` (private, gitignored): ``wide_pairs.jsonl`` (one row
per value of either hearing), ``wide_official.jsonl`` (one row per official value and text),
``wide_summary.json`` (counts, marks per recording and per 10 minutes, validation, git SHA,
seed, date) and ``wide_tables.md``. No LLM, no network; same input and seed, same bytes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tenor.ami.corpus import load_meeting  # noqa: E402
from tenor.evals import council_notes as C  # noqa: E402
from tenor.evals import heard_twice as H  # noqa: E402
from tenor.evals import speaker_naming as S  # noqa: E402

OUT = Path("data/private/heard_twice")
COUNCIL = Path("data/council")
LABEL = {"granola": "Granola", "wispr": "Wispr Flow"}
A, B = H.PRODUCTS
Rows = Sequence[Mapping[str, Any]]


def _mmss(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _marks(rows: Rows) -> int:
    return sum(r["counted"] and r["outcome"] == "different" for r in rows)


def ami(meetings: Sequence[str], validate: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pair rows for AMI meetings; with ``validate``, each meeting's rows against the
    truth-based result. Returns (rows, {durations, anchors, validation})."""
    notes = S.load_notes(meetings)
    rows: list[dict[str, Any]] = []
    info: dict[str, Any] = {"durations": {}, "anchors": {}, "swapped": {}, "validation": {}}
    for mid in meetings:
        meeting = load_meeting(mid)
        streams = {p: H.product_stream(S.product_lines(notes[(mid, p)])) for p in H.PRODUCTS}
        mine, n_anchor = H.pair_rows(mid, streams[A], streams[B])
        rows += mine
        info["durations"][mid] = float(meeting.duration_s)
        info["anchors"][mid] = n_anchor
        info["swapped"][mid] = _marks(H.pair_rows(mid, streams[B], streams[A])[0])
        if validate:
            human = H.human_stream(meeting.words)
            values = H.human_values(human)
            hearings = {
                p: H.classify(
                    values,
                    H.product_values(streams[p]),
                    H.anchors(human, streams[p]),
                    len(streams[p].words),
                )
                for p in H.PRODUCTS
            }
            info["validation"][mid] = H.validate_pairs(H.truth_links(values, hearings), mine)
    return rows, info


def council(
    granola_db: Path, wispr_db: Path
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Pair rows for the council segments both products recorded, and the official-value check
    of every transcript and note in the stores. Returns (rows, info, official rows)."""
    from tenor.core.store import read_notes
    from tenor.evals.wrong_facts import DROP_MIC, own_transcript

    ids = C.read_recording_ids(COUNCIL / "recording_ids.txt")
    dbs = {"granola": granola_db, "wispr": wispr_db}
    stores = {p: {n.note_id: n for n in read_notes(db)} for p, db in dbs.items()}
    manifest = json.loads((COUNCIL / "MANIFEST.json").read_text())
    seconds = {
        f"seattle_{f['legistar_event_id']}_{f['legistar_event_item_id']}": float(f["duration_s"])
        for f in manifest["files"]
    }
    rows: list[dict[str, Any]] = []
    official: list[dict[str, Any]] = []
    info: dict[str, Any] = {
        "durations": {}, "anchors": {}, "swapped": {}, "missing": [], "unchecked": {}
    }  # fmt: skip
    for seg, by_product in ids.items():
        streams: dict[str, H.Stream] = {}
        texts: dict[str, tuple[list[str], list[H.Value]]] = {}
        for p in H.PRODUCTS:
            note = stores[p].get(by_product.get(p, ""))
            if note is None:
                info["missing"].append(f"{seg}:{p}")
                continue
            own = own_transcript(note, drop_mic=DROP_MIC.get(p, True))
            streams[p] = H.product_stream(S.product_lines(own))
            texts[f"{p} transcript"] = (list(streams[p].words), H.hearing_values(streams[p]))
            texts[f"{p} notes"] = H.text_values(note.summary_markdown or note.summary_text or "")
        if len(streams) == 2:
            mine, n_anchor = H.pair_rows(seg, streams[A], streams[B])
            rows += mine
            info["durations"][seg] = seconds[seg]
            info["anchors"][seg] = n_anchor
            info["swapped"][seg] = _marks(H.pair_rows(seg, streams[B], streams[A])[0])
        item = json.loads((COUNCIL / "legistar" / seg / "event_item.json").read_text())["data"]
        votes = json.loads((COUNCIL / "legistar" / seg / "votes.json").read_text())["data"]
        tally = Counter(v["VoteValueName"] for v in votes)
        targets, skipped = H.official_targets(
            item["EventItemMatterFile"],
            item["EventItemTitle"],
            item["EventItemActionText"] or "",
            tally,
        )
        info["unchecked"][seg] = skipped
        for t in targets:
            for name, (words, values) in texts.items():
                found = H.find_target(t, words, values)
                official.append({"segment": seg, "label": t.label, "official": t.shown,
                                 "text": name, **found})  # fmt: skip
    return rows, info, official


def _count_row(name: str, c: Mapping[str, Any]) -> str:
    per10 = c["marks_per_10min"]
    rate = per10["rate"] if isinstance(per10, dict) else per10
    unit_only = c["marks"].get("unit_only", 0)
    neg = c["marks"].get("negation_word", 0)
    return (
        f"| {name} | {c['duration_s'] / 60:.1f} | {c['values_a']} | {c['values_b']} | "
        f"{c['equal']} | {c['different']} | {unit_only} | {neg} | {c['only_a']} | "
        f"{c['only_b']} | {rate} |"
    )


def _example(r: Mapping[str, Any]) -> str:
    return (
        f"- **{r['recording']} {_mmss(r['a']['time_s'])}** ({r['kind']}, {r['mark']}) "
        f'Granola `{r["a"]["value"]}` in "{r["a"]["words"]}" / '
        f'Wispr Flow `{r["b"]["value"]}` in "{r["b"]["words"]}"'
    )


def tables(summary: Mapping[str, Any], rows: Rows, official: Rows, shown: Rows) -> str:
    """The run as markdown (private: it quotes transcripts)."""
    head = [
        "| recording | minutes | values, Granola | values, Wispr Flow | in both, equal | "
        "in both, different (marks) | of which unit only | of which negation word | "
        "only Granola | only Wispr Flow | marks per 10 min |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    out: list[str] = []
    for n, (name, title) in enumerate(
        (("council", "Seattle council"), ("ami_test", "AMI TEST"), ("ami_dev", "AMI DEV")), 1
    ):
        s = summary["sets"][name]
        out += [f"## {n}. {title}: truth-free comparison per recording", "", *head]
        out += [_count_row(rec, c) for rec, c in s["recordings"].items()]
        out += [_count_row("all", s["pooled"]), ""]
        pm, p10 = s["pooled"]["marks_per_recording"], s["pooled"]["marks_per_10min"]
        hard = s["pooled"]["marks_no_negation_per_10min"]
        out += [
            f"Marks per recording: mean {pm['mean']}, range {pm['min']:g} to {pm['max']:g}, "
            f"95% CI {pm['ci']}. Per 10 minutes: {p10['rate']} {p10['ci']}; without negation "
            f"words: {hard['rate']} {hard['ci']}. Anchors per recording: {s['anchors']}. "
            f"Marks with Wispr Flow as hearing A: {s['marks_with_hearings_swapped']} "
            f"(total {sum(s['marks_with_hearings_swapped'].values())}).",
            "",
            "| type | in both, equal | in both, different | only Granola | only Wispr Flow |",
            "|---|---:|---:|---:|---:|",
        ]
        out += [
            f"| {k} | {c['equal']} | {c['different']} | {c['only_a']} | {c['only_b']} |"
            for k, c in s["pooled"]["by_kind"].items()
        ]
        out.append("")
    v = summary["validation"]
    out += ["## 4. Validation on AMI TEST: truth-free rows against the truth-based classes", ""]
    cols = ("equal", "different", "only_a", "only_b", "split", "none", "not_counted")
    out += ["| truth-based class | n | " + " | ".join(cols) + " |", "|---|---:|" + "---:|" * 7]
    for cls, seen in v["truth"].items():
        out.append(
            f"| {cls} | {sum(seen.values())} | " + " | ".join(str(seen.get(c, 0)) for c in cols)
            + " |"
        )  # fmt: skip
    out += ["", f"Truth-free marks by the truth-based class of the linked human value: "
            f"`{json.dumps(v['marks'])}`", "", "All truth-free marks on AMI TEST:", ""]  # fmt: skip
    for r in v["marks_rows"]:
        out.append(_example(r) + f" [{r['truth_class']}]")
    out += ["", "## 5. Council: official values", ""]
    out += ["| segment | official value | text | status | where |", "|---|---|---|---|---|"]
    for r in official:
        where = "; ".join(f'"{h}"' for h in r["hits"][:2])
        if r["others"]:
            where += " OTHER AT CUE: " + "; ".join(
                f'`{o["value"]}` in "{o["words"]}"' for o in r["others"][:4]
            )
        out.append(
            f"| {r['segment']} | {r['label']}: {r['official']} | {r['text']} | {r['status']} | "
            f"{where} |"
        )
    c = summary["council"]
    out += ["", f"Not recorded by a product: {c['missing']}. Title values left unchecked: "
            f"{c['unchecked']}.", ""]  # fmt: skip
    out += ["## 6. Seeded council marks", ""]
    out += [_example(r) for r in shown]
    out += ["", "## 7. All council marks", ""]
    out += [
        _example(r)
        for r in rows
        if r["recording"].startswith("seattle") and r["counted"] and r["outcome"] == "different"
    ]
    return "\n".join(out) + "\n"


def main() -> None:
    from tenor.cli import FACTS_DBS

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--examples", type=int, default=10)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--date", default=dt.date.today().isoformat())
    a = ap.parse_args()
    test_rows, test = ami(S.split_meetings("test"), validate=True)
    dev_rows, dev = ami(S.split_meetings("dev"), validate=False)
    council_rows, cinfo, official = council(FACTS_DBS["granola"], FACTS_DBS["wispr"])
    sets: dict[str, Any] = {}
    for name, rows, info in (("council", council_rows, cinfo), ("ami_test", test_rows, test),
                             ("ami_dev", dev_rows, dev)):  # fmt: skip
        sets[name] = {
            **H.pair_summary(rows, info["durations"], a.seed),
            "anchors": info["anchors"],
            "marks_with_hearings_swapped": info["swapped"],
        }
    truth: dict[str, Counter[str]] = {c: Counter() for c in H.CLASSES}
    marks: Counter[str] = Counter()
    mark_rows: list[dict[str, Any]] = []
    for v in test["validation"].values():
        for cls, seen in v["truth"].items():
            truth[cls].update(seen)
        marks.update(v["marks"])
        mark_rows += v["marks_rows"]
    status = Counter((r["text"], r["status"]) for r in official)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT)
    summary = {
        "version": H.VERSION,
        "seed": a.seed,
        "hearing_a": A,
        "hearing_b": B,
        "sets": sets,
        "validation": {
            "truth": {c: dict(sorted(truth[c].items())) for c in H.CLASSES},
            "marks": dict(sorted(marks.items())),
            "marks_rows": mark_rows,
        },
        "council": {
            "missing": cinfo["missing"],
            "unchecked": cinfo["unchecked"],
            "official": {f"{t}: {s}": n for (t, s), n in sorted(status.items())},
        },
        "run": {
            "git_sha": sha.stdout.strip(),
            "date": a.date,
            "model": None,
            "prompts": None,
            "llm_usd": 0,
        },
    }
    rng = random.Random(f"{a.seed}:heard_twice.wide.examples")
    cm = [r for r in council_rows if r["counted"] and r["outcome"] == "different"]
    shown = sorted(rng.sample(cm, min(a.examples, len(cm))), key=lambda r: r["id"])
    all_rows = council_rows + test_rows + dev_rows
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "wide_pairs.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in all_rows), encoding="utf-8"
    )
    (a.out / "wide_official.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in official), encoding="utf-8"
    )
    (a.out / "wide_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (a.out / "wide_tables.md").write_text(
        tables(summary, all_rows, official, shown), encoding="utf-8"
    )
    for name, s in sets.items():
        p = s["pooled"]
        print(
            f"{name}: {p['recordings']} recordings, values {p['values_a']}/{p['values_b']}, "
            f"equal {p['equal']}, marks {p['different']} {p['marks']}, "
            f"only {p['only_a']}/{p['only_b']}, per 10 min {p['marks_per_10min']['rate']}"
        )
    print(f"validation: {json.dumps(summary['validation']['truth'])}")
    print(f"marks by truth class: {dict(marks)}; council not recorded: {cinfo['missing']}")
    print(f"wrote {a.out}/wide_*")


if __name__ == "__main__":
    main()
