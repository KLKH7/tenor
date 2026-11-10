"""Source pointers: the truth set, the Zoom In click sheet and the pointer jobs (no LLM).

    uv run --no-sync python scripts/zoom_truth.py [--n 50] [--seed 42]

Product: Granola only; meetings: the TEST meetings that have AMI summary links (IB4003 has
none and is left out). For every gold item the three-run E3 reader consensus
(``data/private/reader/runs/test_consensus3.jsonl``) judged ``full`` or ``partial``, its
``evidence_bullets`` are the note bullets that state it. A bullet's true moment is the union of
its gold items' AMI summary links (dialogue acts linked by AMI's annotators), mapped to the
product's transcript lines by the word alignment in ``data/private/reader/speaker_truth/``
(``tenor.evals.zoom``: true line, strong line, eligibility, selection).

Writes (all private, ``data/private`` is gitignored):

- ``data/private/reader/zoom_truth.json``: per meeting its length and the selected bullets
  (id, exact text, section heading, gold ids, true T lines, true time ranges);
- ``data/private/ZOOM_CLICK_SHEET.md``: what to click in the app, no truth in it;
- ``data/private/reader/pointer_jobs/<meeting>.md``: ``prompts/source_pointer.v1.md``, all
  note bullets, the whole product transcript, the bullet ids to answer. No gold, no AMI
  passage, no alignment.

Local data only; same input and seed, same bytes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import reader_packets as P  # noqa: E402
from tenor.evals import speaker_naming as S  # noqa: E402
from tenor.evals import zoom as Z  # noqa: E402

READERS = ROOT / "data/private/reader"
CONSENSUS = READERS / "runs/test_consensus3.jsonl"
TRUTH = READERS / "zoom_truth.json"
SHEET = ROOT / "data/private/ZOOM_CLICK_SHEET.md"
JOBS = READERS / "pointer_jobs"
RUNS = "data/private/reader/pointer_agent_runs"
PRODUCT = "granola"


def evidence(consensus: Path) -> dict[str, dict[str, dict[str, str]]]:
    """meeting -> bullet id -> gold id -> notes verdict, for Granola's full/partial items."""
    out: dict[str, dict[str, dict[str, str]]] = {}
    for raw in consensus.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        r = json.loads(raw)
        if r["product"] != PRODUCT or r["notes_verdict"] not in ("full", "partial"):
            continue
        for b in r["evidence_bullets"]:
            out.setdefault(r["meeting"], {}).setdefault(b, {})[r["gold_id"]] = r["notes_verdict"]
    return out


def build(n: int, seed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """The truth payload and, per meeting, what the sheet and the jobs need."""
    meetings = sorted(S.split_meetings("test"))
    inputs = {p.meeting_id: p for p in P.load_inputs(meetings) if p.product == PRODUCT}
    notes = S.load_notes(meetings)
    by_bullet = evidence(CONSENSUS)
    pools: dict[str, list[dict[str, Any]]] = {}
    extra: dict[str, Any] = {}
    counts: dict[str, dict[str, int]] = {}
    no_links = []
    for mid in meetings:
        p = inputs[mid]
        if not any(g.links for g in p.gold):
            no_links.append(mid)
            continue
        lines = [
            Z.Line(f"T{x.index:04d}", x.label, x.start, x.end, x.text)
            for x in S.product_lines(notes[(mid, PRODUCT)])
        ]
        rows = json.loads((READERS / f"speaker_truth/{mid}__{PRODUCT}.json").read_text())["lines"]
        if [r["line"] for r in rows] != [x.id for x in lines]:
            raise SystemExit(f"{mid}: speaker truth and transcript differ")
        links = {g.id: g.links for g in p.gold}
        parsed = {b.id: Z.parse_bullet(b.rendered) for b in p.bullets}
        by_id = {x.id: x for x in lines}
        records = []
        for bid, golds in sorted(by_bullet.get(mid, {}).items(), key=lambda kv: int(kv[0][1:])):
            if bid not in parsed:
                raise SystemExit(f"{mid} {bid}: evidence bullet not in the notes")
            gold = {g: (v, links.get(g, ())) for g, v in golds.items()}
            if not any(ls for _, ls in gold.values()):
                continue
            records.append(Z.bullet_truth(parsed[bid], gold, rows, by_id))
        pools[mid] = [r for r in records if Z.eligible(r)]
        counts[mid] = {
            "note_bullets": len(p.bullets),
            "evidence_bullets": len(records),
            "eligible": len(pools[mid]),
            "eligible_full": sum(r["verdict"] == "full" for r in pools[mid]),
        }
        extra[mid] = {
            "lines": lines,
            "bullets": [b.rendered for b in p.bullets],
            "parents": {k: str(v["text"]) for k, v in parsed.items()},
            "length_s": round(max(x.end for x in lines), 2),
        }
    picked = Z.select(pools, n, seed)
    truth = {
        "meta": {
            "truth_version": Z.TRUTH_VERSION,
            "product": PRODUCT,
            "seed": seed,
            "n_requested": n,
            "n_selected": sum(len(v) for v in picked.values()),
            "eligible": sum(c["eligible"] for c in counts.values()),
            "tolerances_s": list(Z.TOLERANCES_S),
            "strong_line": f">= {Z.STRONG_WORDS} words of a linked act, or >= "
            f"{Z.STRONG_SHARE:.0%} of the line",
            "consensus": str(CONSENSUS.relative_to(ROOT)),
            "gold": "ami-1.6.2 summary links (original gold ids)",
            "alignment": S.TRUTH_VERSION,
            "meetings_without_links": no_links,
            "per_meeting": counts,
            "clock": "seconds from the first transcript line (the transcript's mm:ss)",
        },
        "meetings": {
            mid: {
                "note_title": f"AMI {mid}",
                "length_s": extra[mid]["length_s"],
                "length": Z.mmss(extra[mid]["length_s"]),
                "bullets": rows_,
            }
            for mid, rows_ in picked.items()
        },
    }
    truth["meta"]["chance"] = Z.chance_baseline(truth)
    return truth, extra


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)
    truth, extra = build(a.n, a.seed)
    TRUTH.write_text(json.dumps(truth, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    SHEET.write_text(
        Z.render_sheet(truth, {m: extra[m]["parents"] for m in truth["meetings"]}),
        encoding="utf-8",
    )
    prompt = (ROOT / f"prompts/{Z.PROMPT}.md").read_text(encoding="utf-8")
    JOBS.mkdir(parents=True, exist_ok=True)
    for old in JOBS.glob("*.md"):
        old.unlink()
    for mid, m in truth["meetings"].items():
        text = Z.job_file(
            mid,
            prompt,
            extra[mid]["bullets"],
            extra[mid]["lines"],
            [b["id"] for b in m["bullets"]],
            f"{RUNS}/<RUN>/{mid}.jsonl",
        )
        (JOBS / f"{mid}.md").write_text(text, encoding="utf-8")
    meta = truth["meta"]
    print("meeting\tnote bullets\tevidence\teligible\tfull\tselected\tlength")
    for mid, c in meta["per_meeting"].items():
        m = truth["meetings"].get(mid, {"bullets": [], "length": "-"})
        print(
            f"{mid}\t{c['note_bullets']}\t{c['evidence_bullets']}\t{c['eligible']}\t"
            f"{c['eligible_full']}\t{len(m['bullets'])}\t{m['length']}"
        )
    print(f"eligible {meta['eligible']}, selected {meta['n_selected']}, chance {meta['chance']}")
    print(f"no summary links, left out: {meta['meetings_without_links']}")
    print(f"wrote {TRUTH.relative_to(ROOT)}, {SHEET.relative_to(ROOT)}, {JOBS.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
