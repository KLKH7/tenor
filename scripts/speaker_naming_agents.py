"""Speaker naming by Claude Code agents (no API): job files, and the score against the truth.

    uv run --no-sync python scripts/speaker_naming_agents.py dump  --split dev|test
    uv run --no-sync python scripts/speaker_naming_agents.py score --split dev|test --run <RUN>

``dump`` writes one job file per (meeting, product) of the split
(``data/private/reader/speaker_jobs_dev/`` or ``speaker_jobs/``): ``prompts/speaker_naming.v1.md``,
the attendee list a calendar invite would carry (simulated from the AMI roster: names the
people introduce themselves by anywhere in the meeting's series, roles; roles only or
``unknown`` where AMI has less), and the product's transcript with the product's own speaker
labels (Wispr's ``Speaker N``, which the reader packets do not show). No truth, no AMI
transcript, no gold items. One agent per job writes
``data/private/reader/speaker_agent_runs/<RUN>/<meeting>__<product>.jsonl``.

``score`` needs the truth files (``scripts/speaker_truth.py``) and reports, per product and
pooled, with meeting-cluster bootstrap CIs (``--seed``, default 42): coverage, accuracy when
named (by confidence and basis; by lines and by words), the baselines, and the share of gold
action items whose owner the predicted speakers would name (``tenor.evals.speaker_naming``:
the rule and what counts as ambiguous). Gold: AMI 1.6.2 with errata v2 (corrected owners).
Units with no usable roster are reported apart, not pooled. Writes
``speaker_agent_runs/<RUN>/score_<split>.json`` (local: it quotes gold action text).
Local data only: no LLM, no network.

``--attendees names`` is the names-only condition: a real calendar invite has names and no
roles. ``dump --split test --attendees names --prompt speaker_naming.v2`` writes
``speaker_jobs_names/`` for the meetings whose roster has a name for every participant, the
list in alphabetical order with no roles; ``score ... --attendees names`` scores those units
the same way, a prediction being a name (a role written as a speaker names nobody), and
writes ``score_<split>_names.json``. Give a names-only run its own ``<RUN>``. The default
(``--attendees roles``, ``speaker_naming.v1``) is the first run's behaviour, unchanged.

``--attendees names --anchors one`` is the one-tap condition: the user is simulated to have
labelled one line per person. ``dump --split test --attendees names --anchors one --prompt
speaker_naming.v3`` writes ``speaker_jobs_onetap/`` for every unit of the split, IB4003
included: the list is each participant's name, or ``Person A``.. (in order of first speech in
AMI, never a role) where the roster has none, alphabetical; a section "Lines the user has
labelled" gives one ``T-id -> label`` per participant (``speaker_naming.one_tap_anchors``:
fixed seed 42, the rule). Those lines are the only truth in the job. The picks are written to
``speaker_onetap_keys/`` (truth: not for the generator). ``score ... --attendees names
--anchors one`` checks the picks against the key files, leaves the labelled lines out of every
metric (baselines and action owners too), and reports per unit, per product, pooled, and apart
for IB4003, for the meetings with a neutral label for at least one person (IB4003 among them)
and for the full-name meetings; it writes ``score_<split>_names_onetap.json``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tenor.ami import errata as E
from tenor.ami.corpus import load_meeting
from tenor.evals import speaker_naming as S


def _first_speech(meeting: Any) -> list[str]:
    """AMI agents in order of their first spoken word."""
    first: dict[str, float] = {}
    for w in meeting.spoken():
        first[w.agent] = min(w.start, first.get(w.agent, w.start))
    return sorted(first, key=lambda a: (first[a], a))


def _one_tap(
    name: str, meeting: Any, truth: list[S.LineTruth], roster: list[S.Attendee]
) -> tuple[list[S.Attendee], dict[str, S.Anchor | None], dict[str, Any]]:
    """The one-tap people and labelled lines of a unit, and the key-file payload for them."""
    people = S.one_tap_people(roster, _first_speech(meeting))
    anchors = S.one_tap_anchors(truth, [a.agent for a in people], name)
    key = {
        "unit": name,
        "truth_version": S.TRUTH_VERSION,
        "seed": S.ANCHOR_SEED,
        "neutral_labels": sum(not a.name for a in roster),
        "people": {a.agent: a.label for a in people},
        "anchors": {
            g: None if x is None else {"line": f"T{x.line:04d}", "rule": x.rule, "words": x.n_words}
            for g, x in anchors.items()
        },
    }
    return people, anchors, key


def _read_truth(name: str) -> tuple[list[S.LineTruth], dict[int, str], list[S.Attendee]]:
    path = S.TRUTH_DIR / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"{path}: run scripts/speaker_truth.py first")
    return S.read_truth(json.loads(path.read_text()))


def dump_one_tap(split: str, prompt_name: str) -> None:
    """Job files of the one-tap condition (module docstring), and the key files."""
    meetings = S.split_meetings(split)
    notes = S.load_notes(meetings)
    out = S.jobs_dir(split, "names", "one")
    out.mkdir(parents=True, exist_ok=True)
    S.ONE_TAP_KEYS.mkdir(parents=True, exist_ok=True)
    prompt = Path(f"prompts/{prompt_name}.md").read_text(encoding="utf-8")
    n = 0
    for mid in meetings:
        meeting = load_meeting(mid)
        for product in S.PRODUCTS:
            if (mid, product) not in notes:
                print(f"{mid}:{product}: not recorded")
                continue
            name = S.unit_name(mid, product)
            lines = S.product_lines(notes[(mid, product)])
            truth, _, roster = _read_truth(name)
            if len(truth) != len(lines):
                raise SystemExit(f"{name}: truth has {len(truth)} lines, transcript {len(lines)}")
            people, anchors, key = _one_tap(name, meeting, truth, roster)
            job = S.job_file(mid, product, prompt, people, lines, S.anchor_block(anchors, people))
            (out / f"{name}.md").write_text(job, encoding="utf-8")
            (S.ONE_TAP_KEYS / f"{name}.json").write_text(json.dumps(key, indent=1) + "\n")
            n += 1
            print(
                f"{name}: {len(lines)} lines, {key['neutral_labels']} neutral labels, "
                f"{sum(x is not None for x in anchors.values())}/{len(people)} labelled lines"
            )
            who = {a.agent: a.label for a in people}
            for g, x in anchors.items():
                if x is None:
                    print(f"  NO ANCHOR: {who[g]} (agent {g}): no line at confidence >= 0.9")
                elif x.rule != f"{S.ANCHOR_WORDS[0]}+ words":
                    print(f"  relaxed: {who[g]} (agent {g}): T{x.line:04d}, {x.rule}")
    print(f"{n} jobs in {out} (prompt {prompt_name}, one tap); keys in {S.ONE_TAP_KEYS}")


def dump(
    split: str, attendees: str = "roles", prompt_name: str | None = None, anchors: str = "none"
) -> None:
    if anchors == "one":
        dump_one_tap(split, prompt_name or S.ONE_TAP_PROMPT)
        return
    prompt_name = prompt_name or S.PROMPTS[attendees]
    meetings = S.split_meetings(split)
    notes = S.load_notes(meetings)
    out = S.jobs_dir(split, attendees)
    out.mkdir(parents=True, exist_ok=True)
    prompt = Path(f"prompts/{prompt_name}.md").read_text(encoding="utf-8")
    for mid in meetings:
        people = S.attendees_of(load_meeting(mid))
        named = sum(a.name is not None for a in people)
        if attendees == "names":
            if not S.all_named(people):
                print(f"{mid}: skipped, {named}/{len(people)} names")
                continue
            people = S.names_only(people)
        roster = (
            "no usable roster"
            if not S.usable(people)
            else f"{named}/{len(people)} names, {sum(a.role is not None for a in people)} roles"
        )
        for product in S.PRODUCTS:
            if (mid, product) not in notes:
                print(f"{mid}:{product}: not recorded")
                continue
            lines = S.product_lines(notes[(mid, product)])
            path = out / f"{S.unit_name(mid, product)}.md"
            path.write_text(S.job_file(mid, product, prompt, people, lines), encoding="utf-8")
            labels = len({x.label for x in lines})
            print(f"{mid}:{product}: {len(lines)} lines, {labels} labels, roster: {roster}")
    print(f"jobs in {out} (prompt {prompt_name}, attendees {attendees})")


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _show(name: str, s: dict[str, Any]) -> None:
    def pct(key: str) -> str:
        r = s[key]
        if r["rate"] is None:
            return "n/a"
        ci = f" [{r['ci'][0]:.0%}, {r['ci'][1]:.0%}]" if r["ci"] else ""
        return f"{r['rate']:.1%}{ci} ({r['num']:.0f}/{r['den']:.0f})"

    print(f"\n== {name}: {s['units']} units, {s['meetings']} meetings ==")
    for label, key in (
        ("coverage (confident lines named)", "coverage"),
        ("accuracy when named", "accuracy_when_named"),
        ("correct of all confident lines", "correct_of_all"),
        ("coverage, by words", "coverage_words"),
        ("accuracy when named, by words", "accuracy_when_named_words"),
        ("correct of all, by words", "correct_of_all_words"),
        (f"coverage, lines of {S.MIN_WORDS}+ words", "coverage_min_words"),
        (f"accuracy when named, lines of {S.MIN_WORDS}+ words", "accuracy_when_named_min_words"),
        ("baseline: all = most talkative", "baseline_majority"),
        ("baseline: all = most talkative, by words", "baseline_majority_words"),
        ("baseline: product labels, best one-to-one", "baseline_labels_one_to_one"),
        ("baseline: product labels, many-to-one", "baseline_labels_many_to_one"),
        ("action owners named correctly", "action_owner_correct"),
        ("action owners named wrongly", "action_owner_wrong"),
    ):
        print(f"  {label}: {pct(key)}")
    for k, v in s["by"].items():
        r = v["named"]
        acc = "n/a" if r["rate"] is None else f"{r['rate']:.1%} ({r['num']:.0f}/{r['den']:.0f})"
        print(f"  {k}: {v['lines']:.0f} lines, accuracy when named {acc}")
    print(f"  speaker values naming no attendee: {s['unresolved_names']}; actions: {s['actions']}")


def _show_units(rows: list[dict[str, Any]]) -> None:
    print("\nPer unit (labelled lines left out):")
    for r in rows:
        c = r["cells"].get("all", {})
        lines, named, right = c.get("lines", 0), c.get("named", 0), c.get("correct", 0)
        acts = [a.get("outcome") for a in r["actions"] if a.get("outcome")]
        print(
            f"  {r['unit']}: {r['neutral_labels']} neutral, {r['labelled_lines']} labelled, "
            f"named {named}/{lines} ({named / max(lines, 1):.0%}), "
            f"right when named {right}/{named} ({right / max(named, 1):.0%}), "
            f"right of all {right / max(lines, 1):.0%}, labels best mapping "
            f"{r['labels']['one_to_one']['correct'] / max(r['labels']['lines'], 1):.0%}, "
            f"owners {acts.count('correct')}/{len(acts)}"
        )


def score_one_tap(split: str, run: str, seed: int, prompt_name: str) -> None:
    """Score a one-tap run (module docstring): labelled lines are left out of everything."""
    errata = E.load_errata(E.ERRATA_V2)
    run_dir = S.RUNS_DIR / run
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for mid in S.split_meetings(split):
        meeting = load_meeting(mid)
        actions, act_agent = S.gold_actions(meeting, errata)
        for product in S.PRODUCTS:
            name = S.unit_name(mid, product)
            truth, labels, roster = _read_truth(name)
            people, anchors, key = _one_tap(name, meeting, truth, roster)
            key_path = S.ONE_TAP_KEYS / f"{name}.json"
            if not key_path.exists() or json.loads(key_path.read_text()) != key:
                raise SystemExit(f"{key_path}: missing or not the lines picked now; dump again")
            pred_path = run_dir / f"{name}.jsonl"
            if not pred_path.exists():
                errors.append(f"{name}: no output")
                continue
            preds, errs = S.parse_predictions(
                pred_path.read_text(encoding="utf-8"), len(truth), S.ONE_TAP_BASES
            )
            errors += [f"{name}: {e}" for e in errs]
            given = {x.line: x.agent for x in anchors.values() if x is not None}
            echoed = sum(
                i in preds and S.resolve_speaker(preds[i].speaker, people) == g
                for i, g in given.items()
            )
            rest = [t for t in truth if t.index not in given]
            row = S.score_unit(rest, labels, people, preds, actions, act_agent)
            rows.append(
                {
                    "unit": name,
                    "meeting": mid,
                    "product": product,
                    "neutral_labels": key["neutral_labels"],
                    "labelled_lines": len(given),
                    "labelled_lines_echoed": echoed,
                    "anchors": key["anchors"],
                    **row,
                }
            )
    groups = {
        "IB4003": [r for r in rows if r["meeting"] == "IB4003"],
        "neutral label for at least one person (IB4003 included)": [
            r for r in rows if r["neutral_labels"]
        ],
        "full names": [r for r in rows if not r["neutral_labels"]],
    }
    summary: dict[str, Any] = {
        p: S.summarise([r for r in rows if r["product"] == p], seed) for p in S.PRODUCTS
    } | {"pooled": S.summarise(rows, seed)}
    for g, rs in groups.items():
        for p in (*S.PRODUCTS, "pooled"):
            summary[f"{g}: {p}"] = S.summarise(
                [r for r in rs if p == "pooled" or r["product"] == p], seed
            )
    _show_units(rows)
    for name, s in summary.items():
        _show(name, s)
    given_n = sum(r["labelled_lines"] for r in rows)
    print(
        f"\nlabelled lines (not scored): {given_n}; the generator wrote the given label on "
        f"{sum(r['labelled_lines_echoed'] for r in rows)}"
    )
    for e in errors:
        print("ERROR", e)
    if run_dir.is_dir():
        out = run_dir / f"score_{split}_names_onetap.json"
        payload = {
            "meta": {
                "run": run,
                "split": split,
                "seed": seed,
                "anchor_seed": S.ANCHOR_SEED,
                "prompt": prompt_name,
                "attendees": "names",
                "anchors": "one",
                "truth_version": S.TRUTH_VERSION,
                "gold": "ami-1.6.2+errata.v2",
                "generator": "Claude Code agent (model as launched; not an API call)",
                "git_sha": _git_sha(),
                "date": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            "summary": summary,
            "units": rows,
            "errors": errors,
        }
        out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {out}")


def score(
    split: str,
    run: str,
    seed: int,
    attendees: str = "roles",
    prompt_name: str | None = None,
    anchors: str = "none",
) -> None:
    if anchors == "one":
        score_one_tap(split, run, seed, prompt_name or S.ONE_TAP_PROMPT)
        return
    prompt_name = prompt_name or S.PROMPTS[attendees]
    errata = E.load_errata(E.ERRATA_V2)
    run_dir = S.RUNS_DIR / run
    rows: list[dict[str, Any]] = []
    apart: list[dict[str, Any]] = []
    errors: list[str] = []
    for mid in S.split_meetings(split):
        meeting = load_meeting(mid)
        actions, act_agent = S.gold_actions(meeting, errata)
        for product in S.PRODUCTS:
            name = S.unit_name(mid, product)
            truth_path = S.TRUTH_DIR / f"{name}.json"
            pred_path = run_dir / f"{name}.jsonl"
            if not truth_path.exists():
                raise SystemExit(f"{truth_path}: run scripts/speaker_truth.py first")
            truth, labels, people = S.read_truth(json.loads(truth_path.read_text()))
            if attendees == "names":
                if not S.all_named(people):
                    continue
                people = S.names_only(people)
            if not pred_path.exists():
                errors.append(f"{name}: no output")
                continue
            preds, errs = S.parse_predictions(pred_path.read_text(encoding="utf-8"), len(truth))
            errors += [f"{name}: {e}" for e in errs]
            if not S.usable(people):
                named = sum(p.speaker.strip().lower() != S.UNKNOWN for p in preds.values())
                apart.append({"unit": name, "lines": len(truth), "named": named})
                continue
            row = S.score_unit(truth, labels, people, preds, actions, act_agent)
            rows.append({"unit": name, "meeting": mid, "product": product, **row})
    summary = {p: S.summarise([r for r in rows if r["product"] == p], seed) for p in S.PRODUCTS} | {
        "pooled": S.summarise(rows, seed)
    }
    for name, s in summary.items():
        _show(name, s)
    print("\nAction items not scored (listed, not guessed):")
    for r in rows:
        for a in r["actions"]:
            if a["kind"] == "ambiguous":
                print(f"  {r['unit']} {a['id']}: {a['why']} | {a['text']}")
    for u in apart:
        print(f"no usable roster, not pooled: {u['unit']}: {u['named']}/{u['lines']} lines named")
    for e in errors:
        print("ERROR", e)
    out = run_dir / (
        f"score_{split}.json" if attendees == "roles" else f"score_{split}_{attendees}.json"
    )
    if run_dir.is_dir():
        payload = {
            "meta": {
                "run": run,
                "split": split,
                "seed": seed,
                "prompt": prompt_name,
                "attendees": attendees,
                "truth_version": S.TRUTH_VERSION,
                "gold": "ami-1.6.2+errata.v2",
                "generator": "Claude Code agent (model as launched; not an API call)",
                "git_sha": _git_sha(),
                "date": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            "summary": summary,
            "units": rows,
            "no_roster": apart,
            "errors": errors,
        }
        out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("dump", "score"):
        p = sub.add_parser(cmd)
        p.add_argument("--split", choices=("dev", "test"), required=True)
        p.add_argument("--attendees", choices=S.ATTENDEE_MODES, default="roles")
        p.add_argument("--anchors", choices=S.ANCHOR_MODES, default="none")
        p.add_argument("--prompt", default=None, help="prompts/<name>.md; default by --attendees")
        if cmd == "score":
            p.add_argument("--run", required=True)
            p.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    if a.anchors == "one" and a.attendees != "names":
        ap.error("--anchors one needs --attendees names")
    if a.cmd == "dump":
        dump(a.split, a.attendees, a.prompt, a.anchors)
    else:
        score(a.split, a.run, a.seed, a.attendees, a.prompt, a.anchors)
