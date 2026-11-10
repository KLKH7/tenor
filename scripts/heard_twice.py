"""Heard twice: hard values in two hearings of the same audio, against AMI's human transcript.

    uv run --no-sync python scripts/heard_twice.py [--split test|dev] [--seed 42] [--examples 15]

For each meeting of the split (TEST: E4's eleven meetings; DEV: IS1008a, used to write the
normaliser), reads AMI's human transcript, Granola's transcript and Wispr Flow's transcript
(the synced store, checked line by line against the reader packets), finds the hard values in
the human transcript (``tenor.evals.heard_twice``: numbers, ordinals, money, percentages,
times, durations, dates, negations), and records what each product heard at that position.

Writes under ``data/private/heard_twice/`` (``<split>_`` prefix; private, gitignored):

- ``values.jsonl``: one row per human value (both hearings, the two-hearings class, flags);
- ``summary.json``: counts, per-product accuracy, agree / disagree classes with
  meeting-cluster bootstrap 95% CIs, stop rules, note-level summary, git SHA, seed, date;
- ``notes_values.jsonl``: every hard value in the products' note bullets with its match class;
- ``tables.md``: the same as markdown tables, the seeded examples and the lists to read.

No LLM, no network. Same input and seed, same bytes (``--date`` fixes the recorded date).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tenor.ami.corpus import load_meeting  # noqa: E402
from tenor.evals import bullet_cues as B  # noqa: E402
from tenor.evals import heard_twice as H  # noqa: E402
from tenor.evals import speaker_naming as S  # noqa: E402

OUT = Path("data/private/heard_twice")
PACKETS = S.READERS / "packets"
LABEL = {"granola": "Granola", "wispr": "Wispr Flow"}


def _packet(meeting_id: str, product: str, lines: Sequence[S.ProdLine]) -> B.CueUnit:
    """The unit's reader packet (note bullets); stops if its transcript is not ``lines``."""
    path = PACKETS / f"{S.unit_name(meeting_id, product)}.md"
    if not path.exists():
        raise SystemExit(f"{path}: no reader packet")
    unit = B.parse_packet(path.read_text(encoding="utf-8"), meeting_id, product, "")
    if [" ".join(x.text.split()) for x in unit.transcript] != [
        " ".join(x.text.split()) for x in lines
    ]:
        raise SystemExit(f"{meeting_id}:{product}: transcript differs from the reader packet")
    return unit


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _ci(r: Mapping[str, Any]) -> str:
    if r["rate"] is None:
        return "n/a"
    ci = f" [{100 * r['ci'][0]:.1f}, {100 * r['ci'][1]:.1f}]" if r["ci"] else ""
    return f"{100 * r['rate']:.1f}%{ci} ({r['num']:g}/{r['den']:g})"


def _mmss(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


Rows = Sequence[Mapping[str, Any]]


def tables(summary: Mapping[str, Any], shown: Rows, rows: Rows, notes: Rows) -> str:
    """The summary as markdown (private: it quotes transcripts)."""
    meetings = summary["meetings"]
    blocks = {**summary["by_kind"], "pooled": summary["pooled"],
              "pooled, no negations": summary["pooled_no_negation"]}  # fmt: skip
    out = ["## 1. Hard values in the human transcript, per meeting", ""]
    out += ["| meeting | " + " | ".join(H.KINDS) + " | all |", "|---|" + "---:|" * 9]
    for m in meetings:
        c = summary["counts"][m]
        out.append(f"| {m} | " + " | ".join(str(c[k]) for k in H.KINDS) + f" | {sum(c.values())} |")
    tot = {k: sum(summary["counts"][m][k] for m in meetings) for k in H.KINDS}
    out.append("| all | " + " | ".join(str(tot[k]) for k in H.KINDS) + f" | {sum(tot.values())} |")
    out += ["", f"Excluded figures of speech (not in any rate): {summary['excluded']}", ""]
    out += ["## 2. Per product: what was heard at each human value", ""]
    out += ["| type | n | product | correct | different | missing | accuracy [95% CI] |",
            "|---|---:|---|---:|---:|---:|---|"]  # fmt: skip
    for name, b in blocks.items():
        for p in H.PRODUCTS:
            x = b[p]
            out.append(
                f"| {name} | {b['n']} | {LABEL[p]} | {x['correct']} | {x['different']} | "
                f"{x['missing']} | {_ci(x['accuracy'])} |"
            )
    out += ["", "## 3. Two hearings", ""]
    out += ["| type | n | agree, right | agree, wrong | disagree, one right | "
            "disagree, both wrong | one or both missing |",
            "|---|---:|---:|---:|---:|---:|---:|"]  # fmt: skip
    for name, b in blocks.items():
        c = b["classes"]
        out.append(f"| {name} | {b['n']} | " + " | ".join(str(c[k]) for k in H.CLASSES) + " |")
    out += ["", "| type | wrong when the hearings agree | a hearing is right when they disagree | "
            "wrong values caught, strict | wrong or missing caught, broad | "
            "disagreed per meeting (mean [CI], min-max) | share of values marked, strict / broad |",
            "|---|---|---|---|---|---|---|"]  # fmt: skip
    for name, b in blocks.items():
        pm = b["flag_strict_per_meeting"]
        ci = f" [{pm['ci'][0]:.1f}, {pm['ci'][1]:.1f}]" if pm["ci"] else ""
        out.append(
            f"| {name} | {_ci(b['agreed_wrong'])} | {_ci(b['disagreed_hearing_right'])} | "
            f"{_ci(b['catch_strict'])} | {_ci(b['catch_broad'])} | "
            f"{pm['mean']}{ci}, {pm['min']}-{pm['max']} | "
            f"{_pct(b['flag_strict_share']['rate'])} / {_pct(b['flag_broad_share']['rate'])} |"
        )
    strict = summary["pooled"]["flag_strict_per_meeting"]["by_meeting"]
    broad = summary["pooled"]["flag_broad_per_meeting"]["by_meeting"]
    per = ", ".join(f"{m} {strict[m]}/{broad[m]}" for m in meetings)
    out += ["", f"Marked values per meeting (strict / broad): {per}", ""]
    out += ["## 4. Stop rules (pooled; then without negations)", ""]
    for key in ("stop_rules", "stop_rules_no_negation"):
        out.append(f"- {key}: `{json.dumps(summary[key])}`")
    out += ["", "## 5. Note-level check", ""]
    out += ["| product | values in notes | by type | checked | at the moment | elsewhere only | "
            "no aligned moment | of those two, same unit said | absent from human transcript |",
            "|---|---:|---|---:|---:|---:|---:|---:|---:|"]  # fmt: skip
    for p in H.PRODUCTS:
        n = summary["notes"][p]
        m = n["match"]
        out.append(
            f"| {LABEL[p]} | {n['values']} | {n['by_kind']} | {n['matched']} | "
            f"{m.get('at_moment', 0)} | {m.get('elsewhere', 0)} | {m.get('no_moment', 0)} | "
            f"{n['elsewhere_same_unit']} | {m.get('absent', 0)} |"
        )
    out += ["", "Note values not found at the aligned moment (to read, not guessed):", ""]
    for r in notes:
        if r["match"] in ("elsewhere", "no_moment", "absent"):
            out.append(
                f"- {r['meeting']} {LABEL[r['product']]} {r['bullet']} `{r['text']}` "
                f"({r['kind']}): {r['match']}; same unit said: {r['same_unit_in_human']}; "
                f"own transcript: {r['own_transcript']}; "
                f"bullet: {r['bullet_text'][:160]}"
            )
    n_strict = sum(bool(r["flag_strict"]) for r in shown)
    out += ["", f"## 6. Seeded examples: {n_strict} disagreed values, then "
            f"{len(shown) - n_strict} that only one hearing has", ""]  # fmt: skip
    for r in shown:
        out += [
            f"- **{r['meeting']} {_mmss(r['time_s'])}** ({r['kind']}, {r['class']}) "
            f"human: `{r['value']}` in \"{r['human_words']}\"",
            *(f"  - {LABEL[p]}: `{r[p]['heard']}` ({r[p]['outcome']}) in \"{r[p]['words']}\""
              for p in H.PRODUCTS),
        ]  # fmt: skip
    out += ["", "## 7. Agreed and wrong (all, to read)", ""]
    for r in rows:
        if not r["excluded"] and r["class"] == "agree_wrong":
            out += [
                f"- {r['meeting']} {_mmss(r['time_s'])} ({r['kind']}) human `{r['value']}` in "
                f'"{r["human_words"]}"; both heard `{r["granola"]["heard"]}`: '
                f'"{r["granola"]["words"]}" / "{r["wispr"]["words"]}"'
            ]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--split", choices=("dev", "test"), default="test")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--examples", type=int, default=15)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--date", default=dt.date.today().isoformat())
    a = ap.parse_args()
    meetings = list(S.split_meetings(a.split))
    notes = S.load_notes(meetings)
    rows: list[dict[str, Any]] = []
    note_rows: list[dict[str, Any]] = []
    for mid in meetings:
        human = H.human_stream(load_meeting(mid).words)
        values = H.human_values(human)
        hearings: dict[str, list[H.Hearing]] = {}
        streams: dict[str, H.Stream] = {}
        for p in H.PRODUCTS:
            if (mid, p) not in notes:
                raise SystemExit(f"{mid}:{p}: not recorded")
            lines = S.product_lines(notes[(mid, p)])
            unit = _packet(mid, p, lines)
            streams[p] = H.product_stream(lines)
            own = H.product_values(streams[p])
            pairs = H.anchors(human, streams[p])
            hearings[p] = H.classify(values, own, pairs, len(streams[p].words))
            note_rows += H.note_values(
                mid, p, [(b.id, b.text) for b in unit.bullets], B.overlap_align(unit),
                human, values, streams[p], own, pairs,
            )  # fmt: skip
            print(f"{mid}:{p}\thuman words {len(human.words)}\tproduct words "
                  f"{len(streams[p].words)}\tanchors {len(pairs)}")  # fmt: skip
        rows += H.value_rows(mid, human, values, hearings, streams)
    summary = H.summarise(rows, meetings, a.seed)
    summary["notes"] = H.note_summary(note_rows)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT)
    summary["run"] = {"git_sha": sha.stdout.strip(), "date": a.date, "split": a.split,
                      "model": None, "prompts": None, "llm_usd": 0}  # fmt: skip
    shown = H.examples(rows, a.examples, a.seed)
    a.out.mkdir(parents=True, exist_ok=True)
    pre = f"{a.split}_"
    (a.out / f"{pre}values.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    (a.out / f"{pre}notes_values.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in note_rows), encoding="utf-8"
    )
    (a.out / f"{pre}summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (a.out / f"{pre}tables.md").write_text(
        tables(summary, shown, rows, note_rows), encoding="utf-8"
    )
    print(f"values {len(rows)} (counted {summary['pooled']['n']}); wrote {a.out}/{pre}*")
    print(json.dumps(summary["stop_rules"], indent=1))


if __name__ == "__main__":
    main()
