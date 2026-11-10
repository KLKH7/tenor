"""Statement kind: do notes present an opinion or a proposal as fact or as decided? ($0, agents)

    uv run --no-sync python scripts/statement_kind_agents.py dump
    uv run --no-sync python scripts/statement_kind_agents.py score --run <RUN> [--seed 42]

Units are E4's TEST meetings whose AMI dialogue acts carry a type, x Granola and Wispr Flow.

``dump`` writes two job files per unit (same input, same bytes):

* ``data/private/reader/kind_jobs_notes/<m>__<p>.md``: prompt ``statement_kind_notes.v1`` and
  every notes bullet, no transcript. An agent answers one JSON line per bullet
  ``{"bullet_id", "presented_as", "attributed_to", "hedged"}`` into
  ``kind_agent_runs/<RUN>/notes/<m>__<p>.jsonl``.
* ``data/private/reader/kind_jobs_support/<m>__<p>.md``: prompt ``statement_kind_support.v1``,
  all notes, then per bullet with a hand-map span that span widened by ``bullet_cues.PAD``. An
  agent answers ``{"bullet_id", "support": ["T0123", ...]}`` into
  ``kind_agent_runs/<RUN>/support/<m>__<p>.jsonl``. Use different agents for the two passes.

It also prints bullets per unit and the human act-type distribution over all words of these
meetings (the base rate).

``score`` maps each support line to AMI words, dialogue acts and true speakers through the
speaker-truth files (``scripts/speaker_truth.py``; confident lines only), prints the measures
of ``tenor.evals.statement_kind`` and writes ``score.json``, ``rows.json`` and ``examples.md``
(12 seeded examples each for M1 and M2) into the run directory. Local data only, private
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

from tenor.ami.corpus import DA_GLOSS, load_meeting
from tenor.evals import bullet_cues as B
from tenor.evals import coverage_e4 as C4
from tenor.evals import speaker_naming as S
from tenor.evals import statement_kind as K

READERS = Path("data/private/reader")
MAPS = Path("data/private/feature_tests/map")
NOTES_JOBS = READERS / "kind_jobs_notes"
SUPPORT_JOBS = READERS / "kind_jobs_support"
RUNS = READERS / "kind_agent_runs"


def setup() -> tuple[dict[str, Any], list[str], list[B.CueUnit], dict[str, list[B.Job]]]:
    """The AMI meetings with typed acts, the TEST meetings skipped for having none, the units
    and each unit's support jobs (hand maps, usual padding)."""
    loaded = {m: load_meeting(m) for m in C4.TEST_MEETINGS}
    meetings = {m: x for m, x in loaded.items() if K.has_typed_acts(x)}
    skipped = [m for m in loaded if m not in meetings]
    units, align = B.split_units(READERS / "packets", MAPS, "test", list(meetings))
    return meetings, skipped, units, {u.key: B.jobs_for(u, align[u.key]) for u in units}


def name(u: B.CueUnit) -> str:
    return S.unit_name(u.meeting_id, u.product)


def type_distribution(meetings: dict[str, Any]) -> dict[str, Any]:
    """Words per human act type over all the meetings, and the non-minor group shares."""
    per = {m: K.type_words(x) for m, x in meetings.items()}
    total: Counter[str] = Counter()
    for c in per.values():
        total.update(c)
    return {
        "words": dict(sorted(total.items(), key=lambda kv: (-kv[1], kv[0]))),
        "group_shares": K.group_shares(total),
        "group_shares_by_meeting": {m: K.group_shares(c) for m, c in per.items()},
    }


def print_distribution(dist: dict[str, Any]) -> None:
    n = sum(dist["words"].values())
    print(f"human act types over all {n} aligned words of these meetings:")
    for t, k in dist["words"].items():
        print(f"  {t}\t{DA_GLOSS.get(t, '')}\t{k}\t{100 * k / n:.1f}%")
    print("shares of non-minor words in an act (the support shares' base rate):")
    print("  " + "; ".join(f"{g} {v:.3f}" for g, v in dist["group_shares"].items()))
    for m, sh in dist["group_shares_by_meeting"].items():
        print(f"  {m}: " + "; ".join(f"{g} {v:.2f}" for g, v in sh.items()))


def dump() -> None:
    meetings, skipped, units, jobs = setup()
    notes_text = Path(f"prompts/{K.NOTES_PROMPT}.md").read_text(encoding="utf-8")
    support_text = Path(f"prompts/{K.SUPPORT_PROMPT}.md").read_text(encoding="utf-8")
    for d in (NOTES_JOBS, SUPPORT_JOBS):
        d.mkdir(parents=True, exist_ok=True)
    print("unit\tbullets\tbullets_with_span")
    for u in units:
        (NOTES_JOBS / f"{name(u)}.md").write_text(K.notes_job_file(u, notes_text), "utf-8")
        body = K.support_job_file(u, jobs[u.key], support_text)
        (SUPPORT_JOBS / f"{name(u)}.md").write_text(body, "utf-8")
        print(f"{u.key}\t{len(u.bullets)}\t{len(jobs[u.key])}")
    print(
        f"{len(units)} units, {sum(len(u.bullets) for u in units)} bullets in {NOTES_JOBS}, "
        f"{sum(map(len, jobs.values()))} with a span in {SUPPORT_JOBS}"
    )
    print(f"skipped (no typed dialogue acts): {', '.join(skipped) or 'none'}")
    missing = [f"{m}:{p}" for m in meetings for p in K.PRODUCTS
               if f"{m}:{p}" not in {u.key for u in units}]  # fmt: skip
    print(f"no reader packet: {', '.join(missing) or 'none'}")
    print_distribution(type_distribution(meetings))


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def score(run: str, seed: int) -> None:
    meetings, skipped, units, jobs = setup()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    examples: dict[str, Any] = {}
    bad_picks = 0
    for u in units:
        npath = RUNS / run / "notes" / f"{name(u)}.jsonl"
        spath = RUNS / run / "support" / f"{name(u)}.jsonl"
        if not npath.exists() or not spath.exists():
            errors.append(f"{u.key}: no output ({npath if not npath.exists() else spath})")
            continue
        tpath = S.TRUTH_DIR / f"{name(u)}.json"
        truth_list, _, people = S.read_truth(json.loads(tpath.read_text(encoding="utf-8")))
        if [t.index for t in truth_list] != [x.index for x in u.transcript]:
            raise SystemExit(f"{u.key}: {tpath} does not match the packet's transcript lines")
        truth = {t.index: t for t in truth_list}
        acts = [K.Act(a.da_type, a.agent) for a in meetings[u.meeting_id].dialogue_acts]
        roles = {a.agent: a.role or a.name or f"Speaker {a.agent}" for a in people}
        labels: dict[str, K.NotesLabel] = {}
        got, errs = B.read_agent_lines(
            npath.read_text(encoding="utf-8"), [b.id for b in u.bullets], f"{u.key} notes"
        )
        errors += errs
        for bid, d in got:
            try:
                labels[bid] = K.NotesLabel.model_validate(d)
            except ValueError as e:
                errors.append(f"{u.key} notes {bid}: {e}")
        by_id = {j.bullet_id: j for j in jobs[u.key]}
        got, errs = B.read_agent_lines(spath.read_text(encoding="utf-8"), by_id, f"{u.key} support")
        errors += errs
        for bid, d in got:
            if bid not in labels:
                continue
            try:
                pick = K.SupportPick.model_validate(d)
            except ValueError as e:
                errors.append(f"{u.key} support {bid}: {e}")
                continue
            lines, problems = K.check_support(pick, by_id[bid])
            bad_picks += len(problems)
            errors += [f"{u.key} support {bid}: {p}" for p in problems]
            lab = labels[bid]
            rows.append(
                {"unit": u.key, "meeting": u.meeting_id, "product": u.product, "bullet_id": bid,
                 "presented_as": lab.presented_as, "attributed_to": lab.attributed_to,
                 "hedged": lab.hedged, **K.score_bullet(lines, truth, acts)}
            )  # fmt: skip
        examples[u.key] = (
            {b.id: b.text for b in u.bullets},
            {x.index: x.text for x in u.transcript},
            truth,
            acts,
            roles,
        )
    summary = K.summarise(rows, seed)
    out = RUNS / run
    out.mkdir(parents=True, exist_ok=True)
    scored_units = sorted({r["unit"] for r in rows})
    meta = {
        "score_version": K.SCORE_VERSION, "run_id": run, "git_sha": _git_sha(), "model": "agent",
        "prompts": [K.NOTES_PROMPT, K.SUPPORT_PROMPT], "seed": seed,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "truth_version": S.TRUTH_VERSION, "confident_at": S.CONFIDENT, "pad": B.PAD,
        "as_share": K.AS_SHARE, "one_speaker": K.ONE_SPEAKER,
        "meetings": list(meetings), "skipped_no_typed_acts": skipped,
        "units_scored": scored_units, "bad_picks": bad_picks,
        "errors": len(errors), "act_type_distribution": type_distribution(meetings),
    }  # fmt: skip
    (out / "score.json").write_text(
        json.dumps({"meta": meta, "summary": summary}, indent=1, ensure_ascii=False) + "\n"
    )
    (out / "rows.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    text = ["# Statement kind: examples (private; seeded sample, not hand-picked)", ""]
    for m in ("M1", "M2"):
        picked = K.pick_examples(rows, m, seed)
        text += [f"## {m}: {K.MEASURE_LABEL[m]} ({len(picked)} of the numerator)", ""]
        for r in picked:
            texts, lines, truth, acts, roles = examples[r["unit"]]
            text += [*K.example_block(r, texts[r["bullet_id"]], lines, truth, acts, roles), ""]
    (out / "examples.md").write_text("\n".join(text), encoding="utf-8")
    print(f"run {run}: {len(scored_units)}/{len(units)} units, seed {seed}")
    print(f"skipped (no typed dialogue acts): {', '.join(skipped) or 'none'}")
    print(K.render(summary))
    print(f"wrote {out}/score.json, rows.json, examples.md")
    for err in errors:
        print("ERROR", err)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dump")
    p = sub.add_parser("score")
    p.add_argument("--run", required=True)
    p.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    if a.cmd == "dump":
        dump()
    else:
        score(a.run, a.seed)
