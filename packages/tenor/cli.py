"""``tenor`` command-line interface (``concentrate`` is a deprecated alias)."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from dotenv import load_dotenv

from tenor.core.store import NoteStore
from tenor.llm.client import LLMClient
from tenor.sources.base import Source
from tenor.sources.fixtures import FixtureSource
from tenor.sources.granola_api import GranolaAPISource
from tenor.sources.granola_mcp import GranolaMCPSource
from tenor.sources.poller import sync as run_sync

app = typer.Typer(no_args_is_help=True, add_completion=False)


def deprecated_alias() -> None:
    """Entry point for the old ``concentrate`` command: warn on stderr, then run ``tenor``."""
    typer.echo("concentrate: deprecated, use `tenor` (same commands)", err=True)
    app(prog_name="tenor")


DEFAULT_FIXTURES = Path("data/fixtures/sample")
STALL_MINUTES = 15.0  # > the longest silent wait one API call can have (~6 min; HARDENING.md)


class SourceKind(StrEnum):
    fixtures = "fixtures"
    granola = "granola"  # public REST API (Business/Enterprise; GRANOLA_API_KEY)
    granola_mcp = "granola-mcp"  # official MCP server (any plan; browser sign-in)
    wispr_mcp = "wispr-mcp"  # Wispr Flow's official MCP (browser sign-in; WISPR_MCP_URL)


def _db_path(db: Path | None) -> Path:
    return db or Path(os.environ.get("CONCENTRATE_DB") or "data/concentrate.db")


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    """TENOR: an open benchmark for meeting note-takers (plus the archived Concentrate memory)."""
    load_dotenv()
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request lines at INFO; keep it quiet so nothing sensitive leaks by default.
    logging.getLogger("httpx").setLevel(logging.WARNING)


@app.command()
def sync(
    source: Annotated[SourceKind, typer.Option(help="Where to read notes from.")] = (
        SourceKind.fixtures
    ),
    fixtures_dir: Annotated[Path, typer.Option(help="Fixture set root.")] = DEFAULT_FIXTURES,
    db: Annotated[Path | None, typer.Option(help="SQLite path (env CONCENTRATE_DB).")] = None,
    full: Annotated[bool, typer.Option(help="Ignore the watermark and re-list all notes.")] = False,
) -> None:
    """Pull notes from Granola (API, MCP), Wispr Flow (MCP) or fixtures into SQLite.

    Real meetings belong under gitignored paths, e.g. ``--db data/real_wispr.db``."""
    with ExitStack() as stack:
        src: Source
        if source is SourceKind.fixtures:
            src = FixtureSource(fixtures_dir)
        elif source is SourceKind.granola:
            api = GranolaAPISource()
            stack.callback(api.close)
            src = api
        elif source is SourceKind.wispr_mcp:
            from tenor.sources.mcp_transport import wispr_caller
            from tenor.sources.wispr_mcp import WisprMCPSource

            src = WisprMCPSource(stack.enter_context(wispr_caller()))
        else:
            from tenor.sources.mcp_transport import LiveMcpCaller

            src = GranolaMCPSource(stack.enter_context(LiveMcpCaller()))
        store = NoteStore(_db_path(db))
        stack.callback(store.close)
        report = run_sync(src, store, full=full)
        counts = store.counts()
    typer.echo(
        f"listed={report.listed} fetched={report.fetched} unchanged={report.unchanged} "
        f"deleted={report.deleted} recovered={report.recovered} pending={len(report.pending)}"
    )
    typer.echo(f"watermark={report.watermark.isoformat() if report.watermark else '-'}")
    typer.echo("store: " + " ".join(f"{k}={v}" for k, v in counts.items()))


NORTHWIND = Path("data/fixtures/northwind")
NORTHWIND_XL = Path("data/fixtures/large/northwind-xl")  # gitignored: several MB
BUDGET_CONFIRM_USD = 2.0


@app.command()
def synth(
    seed: Annotated[int, typer.Option(help="RNG seed; same seed → same dataset.")] = 42,
    out: Annotated[Path, typer.Option(help="Output fixture directory.")] = NORTHWIND,
    polish: Annotated[bool, typer.Option(help="LLM-rewrite wording (hybrid rendering).")] = False,
    offline: Annotated[bool, typer.Option(help="Polish from the LLM cache only.")] = False,
    estimate: Annotated[
        bool, typer.Option(help="Print the polish cost estimate and exit.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 cost confirmation.")] = False,
    scale: Annotated[str, typer.Option(help="base (40 meetings) or xl (~185 meetings).")] = "base",
    extras: Annotated[
        bool, typer.Option(help="v2.1: add hybrid rooms, identity mess, corrections, etc.")
    ] = False,
) -> None:
    """Generate the synthetic Northwind Labs dataset and benchmark questions."""
    if scale == "xl" and out == NORTHWIND:
        out = NORTHWIND_XL
    from tenor.synth.pipeline import (
        build,
        estimate_polish_usd,
        generate_dataset,
        generator_version,
    )

    version = generator_version(extras)

    manifest = out / "manifest.json"
    if scale == "xl" and manifest.exists() and not estimate:
        import json as _json

        old = _json.loads(manifest.read_text()).get("generator")
        if old != version:
            raise typer.BadParameter(
                f"{out} holds a {old} corpus that memories and results may be built on; "
                f"this generator is {version}. Write elsewhere with --out (e.g. "
                f"{out}-v2), or delete {out} first."
            )

    llm = None
    if polish or estimate:
        llm = LLMClient(offline=offline)
        tin, tout, usd = estimate_polish_usd(build(seed, scale, extras), llm.model)
        typer.echo(
            f"polish estimate ({llm.model}): ~{tin:,} input + ~{tout:,} output tokens "
            f"≈ ${usd:.2f} (uncached; heuristic counts incl. 30% thinking allowance)"
        )
        if estimate:
            return
        if usd > BUDGET_CONFIRM_USD and not offline and not yes:
            typer.confirm(
                f"Estimated cost ${usd:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
            )
    result = generate_dataset(seed, out, llm=llm, scale=scale, extras=extras)
    v = result.validation
    typer.echo(
        f"wrote {result.meetings} notes, {result.utterances:,} utterances, "
        f"{result.words:,} words, {result.questions} questions → {out}"
    )
    typer.echo("questions: " + ", ".join(f"{k}={n}" for k, n in sorted(v.by_category.items())))
    typer.echo("split: " + ", ".join(f"{k}={n}" for k, n in sorted(v.by_split.items())))
    if result.polish:
        p = result.polish
        typer.echo(
            f"polish: {p.accepted}/{p.turns} turns accepted, rejected={p.rejected}, "
            f"missing={p.missing}, spent=${result.llm_spent_usd:.2f}"
        )
    if v.errors:
        typer.echo(f"VALIDATION FAILED ({len(v.errors)} errors):", err=True)
        for e in v.errors[:20]:
            typer.echo(f"  {e}", err=True)
        raise typer.Exit(1)
    typer.echo("validation: all gold answers are stated in their gold utterances ✓")


@app.command("eval")
def eval_cmd(
    systems: Annotated[
        str, typer.Option(help="Comma-separated: b0,b1,b2,b2o,b3,b4,b5 (default depends on set).")
    ] = "",
    xl: Annotated[bool, typer.Option("--xl", help="Use the Northwind-XL benchmark.")] = False,
    split: Annotated[str, typer.Option(help="dev | test | all. Tune only on dev.")] = "dev",
    limit: Annotated[int, typer.Option(help="Max questions (0 = all in split).")] = 0,
    category: Annotated[
        list[str] | None, typer.Option(help="Restrict to categories (repeatable).")
    ] = None,
    k: Annotated[int, typer.Option(help="Meetings retrieved by b0/b1.")] = 20,
    seed: Annotated[int, typer.Option(help="Seed for bootstrap CIs.")] = 42,
    fixtures: Annotated[Path, typer.Option(help="Benchmark fixture set.")] = NORTHWIND,
    out: Annotated[Path, typer.Option(help="Report directory.")] = Path("evals/reports"),
    embeddings: Annotated[bool, typer.Option(help="Use dense embeddings in b0/b1.")] = True,
    estimate_only: Annotated[
        bool, typer.Option("--estimate", help="Estimate cost and exit.")
    ] = False,
    offline: Annotated[bool, typer.Option(help="Use cached LLM results only.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 confirmation.")] = False,
    batch: Annotated[
        bool,
        typer.Option(
            "--batch/--no-batch",
            help="Run single-turn systems and the judge via the Message Batches API (50% off).",
        ),
    ] = True,
    latency_sample: Annotated[
        int,
        typer.Option(help="With --batch: first N questions run interactively to measure latency."),
    ] = 10,
    stall_minutes: Annotated[
        float,
        typer.Option(help="Exit 75 if no progress for this many minutes (0 = never)."),
    ] = STALL_MINUTES,
) -> None:
    """Run the baselines on the benchmark and write a report."""
    from tenor.agents.retrieval import SentenceTransformerEmbedder
    from tenor.evals.runner import (
        build_systems,
        estimate,
        load_corpus,
        load_questions,
        run_eval,
    )

    if xl and fixtures == NORTHWIND:
        fixtures = NORTHWIND_XL
    default = "b0,b1,b2o,b3" if fixtures == NORTHWIND_XL else "b0,b1,b2,b3"
    names = tuple(s.strip() for s in (systems or default).split(",") if s.strip())
    client = LLMClient(offline=offline)
    budget = float(os.environ.get("CONCENTRATE_BUDGET_USD") or 25)
    notes = load_corpus(fixtures)
    questions = load_questions(fixtures, split, limit, tuple(category or ()))
    needs_index = {"b0", "b1", "b2o", "c"} & set(names)
    embedder = SentenceTransformerEmbedder() if embeddings and needs_index else None
    built = build_systems(names, client, notes, embedder, k, fixtures=fixtures)
    ests = estimate(
        built,
        questions,
        client.model,
        client=client,
        notes=notes,
        batch=batch,
        latency_sample=latency_sample,
    )
    typer.echo(
        f"{fixtures}: {len(notes)} meetings · {len(questions)} questions ({split}) · "
        f"{len(built)} systems · {client.model}"
    )
    if batch:
        typer.echo(
            f"  batch mode: Message Batches API at 50% for single-turn systems and the judge, "
            f"except the first {min(latency_sample, len(questions))} questions (interactive, "
            "full price, the only latency source)"
        )
    for e in ests:
        tag = "exact" if e.exact else "est. "
        ingest = f", ingest ${e.ingest_usd:.2f}" if e.ingest_usd else ""
        disc = ", batch 50%" if e.batch else ""
        typer.echo(
            f"  {e.system:<13} ~{e.input_tokens / max(1, e.questions):>8,.0f} in-tok/q ({tag}) "
            f"≈ ${e.usd:6.2f}  (incl. judge{ingest}{disc})"
        )
    total = sum(e.usd for e in ests)
    typer.echo(f"  estimated total ≈ ${total:.2f} (before cache reuse) · cap ${budget:.2f}")
    if estimate_only:
        return
    if not offline and not os.environ.get("ANTHROPIC_API_KEY"):
        raise typer.BadParameter("ANTHROPIC_API_KEY is not set (put it in .env)")
    if total > BUDGET_CONFIRM_USD and not offline and not yes:
        typer.confirm(
            f"Estimated ${total:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
        )
    with _stall_guard(stall_minutes, "eval"):
        report = run_eval(
            built,
            questions,
            client,
            seed=seed,
            split=split,
            budget_usd=budget,
            out_dir=out,
            notes=notes,
            batch=batch,
            latency_sample=latency_sample,
            fixtures=fixtures,
            meta={"k": k, "embedder": getattr(embedder, "name", None), "fixtures": str(fixtures)},
        )
    typer.echo((Path(report["paths"]["md"])).read_text())
    typer.echo(f"spent ${report['meta']['spent_usd']:.2f} · report: {report['paths']['md']}")


@app.command()
def ingest(
    systems: Annotated[str, typer.Option(help="Write-time systems to build: b4,b5.")] = "b4,b5",
    xl: Annotated[bool, typer.Option("--xl", help="Use the Northwind-XL benchmark.")] = False,
    fixtures: Annotated[Path, typer.Option(help="Benchmark fixture set.")] = NORTHWIND,
    estimate_only: Annotated[
        bool, typer.Option("--estimate", help="Estimate cost and exit.")
    ] = False,
    stall_minutes: Annotated[
        float,
        typer.Option(help="Exit 75 if no progress for this many minutes (0 = never)."),
    ] = STALL_MINUTES,
) -> None:
    """Build write-time memories once (Mem0, Graphiti, ...). Resumable; reruns are free.

    A build that makes no progress for ``--stall-minutes`` exits with code 75; rerun it (e.g.
    in a shell loop) and it resumes from ``progress.jsonl``. See docs/engineering/HARDENING.md.
    """
    import json as _json
    from dataclasses import asdict

    from tenor.evals.runner import build_systems, estimate, load_corpus

    if xl and fixtures == NORTHWIND:
        fixtures = NORTHWIND_XL
    client = LLMClient()
    notes = load_corpus(fixtures)
    names = tuple(s.strip() for s in systems.split(",") if s.strip())
    built = build_systems(names, client, notes, None, 0, fixtures=fixtures)
    for e in estimate(built, [], client.model, notes=notes):
        typer.echo(f"{e.system}: ingest ≈ ${e.ingest_usd:.2f} for {len(notes)} meetings")
    if estimate_only:
        return
    with _stall_guard(stall_minutes, f"ingest {systems}"):
        for s in built:
            stats = s.ingest(notes)  # type: ignore[attr-defined]
            typer.echo(f"{s.name}: " + _json.dumps(asdict(stats), default=str))


def _report_paths(report: Path) -> tuple[Path, Path]:
    """``evals/reports/<stamp>`` (any extension or none) -> its ``.json`` and ``.jsonl``."""
    stem = report.with_suffix("") if report.suffix in {".json", ".jsonl", ".md"} else report
    js, jl = stem.with_suffix(".json"), stem.with_suffix(".jsonl")
    if not jl.exists():
        raise typer.BadParameter(f"{jl} not found")
    return js, jl


@app.command("judge-check")
def judge_check(
    report: Annotated[Path, typer.Argument(help="A report from `eval` (any of its files).")],
    model: Annotated[str, typer.Option(help="Second judge model.")] = "claude-sonnet-5-5",
    batch: Annotated[bool, typer.Option("--batch/--no-batch", help="Batch API (50% off).")] = True,
    estimate_only: Annotated[
        bool, typer.Option("--estimate", help="Estimate cost and exit.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 confirmation.")] = False,
) -> None:
    """Re-judge a saved report with a second model and report agreement with the first."""
    import json as _json

    from tenor.evals.judge_check import judge_check_markdown, load_report, rejudge_request
    from tenor.evals.runner import JUDGE_TOKENS_EST
    from tenor.evals.scoring import JudgeOutput
    from tenor.llm.client import cost_usd, estimate_tokens, prefetch_structured

    js, jl = _report_paths(report)
    primary = _json.loads(js.read_text())["meta"]["model"] if js.exists() else "unknown"
    if model == primary:
        raise typer.BadParameter(f"the second judge must differ from the primary ({primary})")
    answers = load_report(jl)
    client = LLMClient(model=model)
    reqs = [rejudge_request(a) for a in answers]
    todo = [r for r in reqs if not client.is_cached(r)]
    usd = sum(
        cost_usd(
            model,
            estimate_tokens(r.system) + estimate_tokens(r.user),
            JUDGE_TOKENS_EST[1],
            batch=batch,
        )
        for r in todo
    )
    typer.echo(f"{jl.name}: {len(answers)} answers · {len(todo)} uncached · {model} ≈ ${usd:.2f}")
    if estimate_only:
        return
    if usd > BUDGET_CONFIRM_USD and not yes:
        typer.confirm(
            f"Estimated ${usd:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
        )
    batch_usd = prefetch_structured(client, todo).cost_usd if batch and todo else 0.0
    second = [client.structured(r, JudgeOutput) for r in reqs]
    md = judge_check_markdown(
        answers, second, primary_model=primary, second_model=model, report_name=jl.stem
    )
    out = jl.with_name(f"{jl.stem}.judgecheck.{model}.md")
    out.write_text(md)
    typer.echo(md)
    typer.echo(f"spent ${batch_usd + client.ledger.spent_usd():.2f} · written: {out}")


@app.command("hand-check")
def hand_check(
    report: Annotated[Path, typer.Argument(help="A report from `eval` (any of its files).")],
    n: Annotated[int, typer.Option(help="Answers to sample for hand-labelling.")] = 50,
    seed: Annotated[int, typer.Option(help="Sampling seed.")] = 42,
    score: Annotated[
        bool, typer.Option("--score", help="Score a filled-in sheet instead of exporting one.")
    ] = False,
    second_model: Annotated[
        str, typer.Option(help="Also score this judge's verdicts (from `judge-check`'s cache).")
    ] = "claude-sonnet-5-5",
) -> None:
    """Export a blind sample for a person to label, then score the judges against it. Free."""
    from tenor.evals.judge_check import (
        hand_sample,
        load_report,
        rejudge_request,
        score_hand_check,
        write_hand_check,
    )
    from tenor.evals.scoring import JudgeOutput

    _, jl = _report_paths(report)
    sheet, key = (
        jl.with_name(f"{jl.stem}.handcheck.csv"),
        jl.with_name(f"{jl.stem}.handcheck.key.json"),
    )
    answers = load_report(jl)
    if not score:
        if sheet.exists():
            raise typer.BadParameter(f"{sheet} exists; label it and pass --score")
        write_hand_check(hand_sample(answers, n, seed), sheet, key)
        typer.echo(
            f"Wrote {sheet}. Fill `your_verdict` with c / p / i (correct / partially / "
            f"incorrect) comparing `answer` to `gold` only, then rerun with --score."
        )
        return
    judges = {"primary": {a.key: a.verdict for a in answers}}
    second = LLMClient(model=second_model, offline=True)
    cached = {a.key: a for a in answers if second.is_cached(rejudge_request(a))}
    if cached:
        judges[second_model] = {
            k: second.structured(rejudge_request(a), JudgeOutput).verdict for k, a in cached.items()
        }
    for name, s in score_hand_check(sheet, key, judges).items():
        typer.echo(
            f"{name:<20} vs human: {s['agreement']:.1%} agree on correct/not "
            f"(kappa {s['kappa']:.2f}, n={s['n']}) · 3-way {s['agreement_3way']:.1%}"
        )


# -- stall watchdog (workstream I) -------------------------------------------------------------


@contextmanager
def _stall_guard(minutes: float, what: str) -> Iterator[None]:
    """Run a long command under the stall watchdog (off when ``minutes`` is 0) and graceful
    SIGTERM/SIGINT handling; a requested stop closes stores cleanly and exits 75."""
    from tenor import watchdog

    watchdog.reset_stop()
    with ExitStack() as stack:
        stack.enter_context(watchdog.graceful_signals())
        if minutes > 0:
            stack.enter_context(watchdog.Watchdog(minutes * 60, what=what))
        try:
            yield
        except watchdog.StopRequested as exc:
            typer.echo(f"{what}: stopped cleanly ({exc}); rerun to resume", err=True)
            raise typer.Exit(watchdog.STALL_EXIT_CODE) from None


@app.command()
def analyze(
    report: Annotated[Path, typer.Argument(help="A report from `eval` (any of its files).")],
    fixtures: Annotated[
        Path | None,
        typer.Option(help="Ground truth for the failure taxonomy (default: the report's own)."),
    ] = None,
    volume: Annotated[
        int, typer.Option(help="Questions per corpus over which ingest cost is amortised.")
    ] = 1_000,
    seed: Annotated[int | None, typer.Option(help="Bootstrap seed (default: report's).")] = None,
    llm: Annotated[
        bool, typer.Option("--llm", help="Refine generic failure buckets with an LLM classifier.")
    ] = False,
    llm_model: Annotated[str, typer.Option(help="Model for --llm.")] = "claude-haiku-5-5",
    estimate_only: Annotated[
        bool, typer.Option("--estimate", help="With --llm: estimate its cost and exit.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 confirmation.")] = False,
    price_model: Annotated[
        str, typer.Option(help="Re-price every answer at this model's interactive price.")
    ] = "claude-opus-5-5",
    real_questions: Annotated[
        Path | None,
        typer.Option(help="Real-slice questions.jsonl: gold meetings for a real-slice report."),
    ] = None,
    aggregate_only: Annotated[
        bool,
        typer.Option(help="Counts and rates only: no question IDs, signals or failures file."),
    ] = False,
    out: Annotated[
        Path | None, typer.Option(help="Output stem (default: next to the report).")
    ] = None,
) -> None:
    """Report v2: paired significance, power, Pareto frontier, failure taxonomy and the $0
    metrics (docs/method/METRICS.md). Free unless --llm."""
    import json as _json

    from tenor.evals.analyze import load_rows, write_analysis
    from tenor.evals.failures import (
        GENERIC_TYPES,
        GroundTruth,
        LLMFailure,
        failures,
        llm_request,
    )
    from tenor.evals.metrics import gold_from_questions, gold_from_real
    from tenor.llm.client import PRICING, cost_usd, estimate_tokens

    if price_model not in PRICING:
        raise typer.BadParameter(f"unknown model {price_model!r}; known: {', '.join(PRICING)}")
    js, jl = _report_paths(report)
    rep = _json.loads(js.read_text())
    rows = load_rows(jl)
    fx = fixtures or Path(rep["meta"].get("fixtures") or NORTHWIND)
    gt = None
    if real_questions is None:
        gt = GroundTruth.load(fx) if (fx / "questions.jsonl").exists() else None
    gold = gold_from_questions(gt.questions) if gt is not None else None
    if real_questions is not None:
        from tenor.evals.real_slice import load_questions as load_real

        gold = gold_from_real(load_real(real_questions))
        gt_note = "Ground truth: real-slice questions (gold meetings only; no world model)."
    elif gt is None:
        gt_note = f"Ground truth: **not found** at `{fx}`; only report-row signals were used."
    else:
        qids = {r["qid"]: r["question"] for r in rows}
        ok = sum(gt.questions.get(q) is not None and gt.questions[q].question == t
                 for q, t in qids.items())  # fmt: skip
        gt_note = f"Ground truth: `{fx}` ({ok}/{len(qids)} report questions matched)."
        if ok < len(qids):
            typer.echo(f"warning: {len(qids) - ok} questions don't match {fx}", err=True)
    fails = failures(rows, gt)
    if llm:
        by_key = {(r["system"], r["qid"]): r for r in rows}
        todo = [f for f in fails if f.primary in GENERIC_TYPES]
        reqs = [llm_request(by_key[(f.system, f.qid)], f) for f in todo]
        client = LLMClient(model=llm_model)
        usd = sum(
            cost_usd(llm_model, estimate_tokens(r.system) + estimate_tokens(r.user), 120)
            for r in reqs
            if not client.is_cached(r)
        )
        typer.echo(f"LLM classifier: {len(reqs)} answers · {llm_model} ≈ ${usd:.3f}")
        if estimate_only:
            return
        if usd > BUDGET_CONFIRM_USD and not yes:
            typer.confirm(
                f"Estimated ${usd:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
            )
        for f, req in zip(todo, reqs, strict=True):
            res = client.structured(req, LLMFailure)
            f.llm_type, f.llm_reason = res.type, res.reason
        typer.echo(f"LLM classifier spent ${client.ledger.spent_usd():.4f}")
    stem = out or jl.with_suffix("")
    stem.parent.mkdir(parents=True, exist_ok=True)
    paths = write_analysis(
        rep, rows, fails, stem, seed=seed if seed is not None else int(rep["meta"].get("seed", 0)),
        volume=volume, gt_note=gt_note, gold=gold, price_model=price_model,
        aggregate_only=aggregate_only,
    )  # fmt: skip
    typer.echo("written: " + ", ".join(str(p) for p in paths.values()))


# -- MCP server (workstream D) --------------------------------------------------------------------


@app.command("mcp-serve")
def mcp_serve(
    xl: Annotated[bool, typer.Option("--xl", help="Serve the Northwind-XL memory.")] = False,
    fixtures: Annotated[
        Path | None, typer.Option(help="Fixture set the memory was built from.")
    ] = None,
    store: Annotated[
        Path | None, typer.Option(help="A build directory (holds memory.sqlite); default: find it.")
    ] = None,
    allow_partial: Annotated[
        bool, typer.Option(help="Serve an unfinished build if no finished one exists.")
    ] = False,
    embeddings: Annotated[
        bool, typer.Option(help="Dense embeddings in retrieval, as in the eval (slower start).")
    ] = True,
) -> None:
    """Serve a built Concentrate memory as a read-only MCP server over stdio.

    stdout carries the MCP protocol, so this command prints nothing to it; errors go to stderr.
    See docs/archive/concentrate/MCP_SERVER.md for the Claude Desktop / Claude Code config.
    """
    from tenor.mcp_server.server import build_server
    from tenor.mcp_server.service import Answerer, open_memory

    root = fixtures or (NORTHWIND_XL if xl else NORTHWIND)
    embedder = None
    if embeddings:
        from tenor.agents.retrieval import SentenceTransformerEmbedder

        embedder = SentenceTransformerEmbedder()
    try:
        svc = open_memory(
            root,
            store_dir=store,
            allow_partial=allow_partial,
            embedder=embedder,
            answerer=Answerer(LLMClient()),
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    logging.getLogger(__name__).warning(
        "concentrate MCP: serving %s (%d meetings)", svc.info["store"], len(svc.meetings)
    )
    build_server(svc).run("stdio")


# -- pre-meeting brief (workstream H) --------------------------------------------------------

BRIEF_STORE_ROOT = Path("data/cache/concentrate")


def _brief_store(store: Path | None) -> Path:
    """The given memory store, else the most recently finished one under data/cache."""
    if store is not None:
        return store
    done = sorted(BRIEF_STORE_ROOT.glob("*/*/complete.json"), key=lambda p: p.stat().st_mtime)
    if not done:
        raise typer.BadParameter("no finished memory store found; build one or pass --store")
    return done[-1].parent / "memory.sqlite"


def _brief_world(fixtures: Path) -> tuple[object, object, dict[str, str], dict[str, str]]:
    import json as _json

    from tenor.evals.brief import GoldBuilder, name_index
    from tenor.evals.runner import load_corpus
    from tenor.synth.world import World

    world = World.model_validate_json((fixtures / "world.json").read_text())
    notes = load_corpus(fixtures)
    names, ids = name_index(_json.loads((fixtures / "directory.json").read_text()))
    return GoldBuilder(world, notes), notes, names, ids


@app.command()
def brief(
    note_id: Annotated[str, typer.Argument(help="The meeting to brief for (its note ID).")],
    fixtures: Annotated[Path, typer.Option(help="Fixture set with world.json.")] = NORTHWIND_XL,
    store: Annotated[Path | None, typer.Option(help="Memory store (memory.sqlite).")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the structured brief.")] = False,
) -> None:
    """Print the pre-meeting brief for a meeting, from memory as of its start."""
    from tenor.evals.brief import GoldBuilder
    from tenor.memory.brief import build_brief, to_markdown
    from tenor.memory.store import MemoryStore

    gold, _, names, _ = _brief_world(fixtures)
    assert isinstance(gold, GoldBuilder)
    meeting = next((m for m in gold.w.meetings if m.note_id == note_id), None)
    if meeting is None:
        raise typer.BadParameter(f"{note_id} is not a meeting in {fixtures}")
    path = _brief_store(store)
    b = build_brief(MemoryStore(path), gold.upcoming(meeting))
    typer.echo(b.model_dump_json(indent=1) if as_json else to_markdown(b, names))


@app.command("brief-eval")
def brief_eval(
    fixtures: Annotated[Path, typer.Option(help="Fixture set with world.json.")] = NORTHWIND_XL,
    store: Annotated[Path | None, typer.Option(help="Memory store (memory.sqlite).")] = None,
    split: Annotated[str, typer.Option(help="dev, test or all.")] = "dev",
    limit: Annotated[int, typer.Option(help="Meetings to sample (0 = all).")] = 0,
    baseline: Annotated[bool, typer.Option(help="Also run the top-k LLM baseline.")] = False,
    model: Annotated[str, typer.Option(help="Baseline model.")] = "claude-sonnet-5-5",
    k: Annotated[int, typer.Option(help="Chunks the baseline retrieves.")] = 24,
    seed: Annotated[int, typer.Option(help="Seed for the dev/test split.")] = 42,
    estimate: Annotated[bool, typer.Option(help="Print the baseline estimate and exit.")] = False,
    offline: Annotated[bool, typer.Option(help="Baseline from the LLM cache only.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 cost confirmation.")] = False,
    out: Annotated[Path, typer.Option(help="Report directory.")] = Path("evals/reports"),
) -> None:
    """Score pre-meeting briefs against the world's ground truth (memory vs top-k baseline)."""
    import hashlib
    import subprocess
    from datetime import UTC, datetime

    from tenor.agents.retrieval import SentenceTransformerEmbedder
    from tenor.evals.brief import (
        BASELINE_PROMPT,
        GoldBuilder,
        TopKBriefBaseline,
        dumps,
        evaluate,
        sample_targets,
    )
    from tenor.llm.client import load_prompt
    from tenor.memory.store import MemoryStore

    gold, notes, names, ids = _brief_world(fixtures)
    assert isinstance(gold, GoldBuilder) and isinstance(notes, list)
    gold.seed = seed
    targets = sample_targets(gold, split, limit)
    path = _brief_store(store)
    typer.echo(f"store={path} split={split} meetings={len(targets)}")
    base = None
    client = None
    if baseline:
        client = LLMClient(model=model, offline=offline)
        base = TopKBriefBaseline(client, notes, names, SentenceTransformerEmbedder(), k=k)
        usd = sum(base.estimate_usd(gold.upcoming(m)) for m in targets)
        typer.echo(f"baseline estimate: ${usd:.2f} ({model}, {len(targets)} calls, k={k})")
        if estimate:
            return
        if usd > BUDGET_CONFIRM_USD and not offline and not yes:
            typer.confirm(
                f"Estimated cost ${usd:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
            )
    report = evaluate(MemoryStore(path), gold, targets, base, ids)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    prompt_hash = hashlib.sha256(load_prompt(BASELINE_PROMPT).encode()).hexdigest()[:8]
    report["meta"] = {
        "git_sha": sha.stdout.strip(),
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "fixtures": str(fixtures),
        "store": str(path),
        "split": split,
        "limit": limit,
        "seed": seed,
        "baseline": {"model": model, "prompt": BASELINE_PROMPT, "prompt_sha": prompt_hash, "k": k}
        if baseline
        else None,
        "spent_usd": client.ledger.spent_usd() if client else 0.0,
    }
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"brief-{split}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    dest.write_text(dumps(report))
    for system, agg in report["summary"].items():
        typer.echo(f"{system}: last-meeting {agg['last_meeting_accuracy']:.0%}")
        for sec in ("commitments", "commitments_any_owner", "changes", "requests"):
            r = agg[sec]

            def pct(x: float | None) -> str:
                return "-" if x is None else f"{x:.0%}"

            typer.echo(
                f"  {sec:<22} P {pct(r['precision'])} R {pct(r['recall'])} "
                f"stale {pct(r['staleness'])} cite {pct(r['citation_precision'])} "
                f"(shown {r['shown']}, gold {r['gold']})"
            )
    typer.echo(f"spent ${report['meta']['spent_usd']:.2f} · report {dest}")


# -- fidelity (workstream G) -------------------------------------------------------------------


@app.command()
def fidelity(
    target: Annotated[
        str, typer.Argument(help="Fixture directory, or a NoteStore .db (e.g. data/real.db).")
    ],
) -> None:
    """Aggregate style statistics of a corpus (docs/archive/northwind/FIDELITY_V2.md).

    Prints no transcript text.
    """
    import json

    from tenor.synth.fidelity import fixture_corpus, report, store_corpus

    path = Path(target)
    corpus = store_corpus(path) if path.suffix == ".db" else fixture_corpus(path)
    typer.echo(json.dumps(report(corpus), indent=1))


# -- tune-c: Concentrate variants and answer-time ablations on the XL dev split (workstream L) --

TUNE_VARIANTS: dict[str, dict[str, object]] = {
    "full": {},
    "no_entities": {"query_entities": False},  # question parser resolves no entities
    "no_supersession": {"supersession": False},  # append-only fact log, no validity/state
    "no_digests": {"k_digests": 0},
    "fallback_on_abstain": {"fallback": "on_abstain"},  # docs/archive/concentrate/fallback.md
    "fallback_always": {"fallback": "always"},
}


@app.command("tune-c")
def tune_c(
    variants: Annotated[
        str, typer.Option(help=f"Comma-separated: {', '.join(TUNE_VARIANTS)}.")
    ] = "full",
    split: Annotated[str, typer.Option(help="Tuning uses dev only.")] = "dev",
    limit: Annotated[int, typer.Option(help="Max questions (0 = all in split).")] = 0,
    seed: Annotated[int, typer.Option(help="Seed for bootstrap CIs.")] = 42,
    out: Annotated[Path, typer.Option(help="Report directory.")] = Path("evals/reports"),
    estimate_only: Annotated[
        bool, typer.Option("--estimate", help="Estimate cost and exit.")
    ] = False,
    offline: Annotated[bool, typer.Option(help="Use cached LLM results only.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 confirmation.")] = False,
    batch: Annotated[bool, typer.Option("--batch/--no-batch")] = True,
    latency_sample: Annotated[int, typer.Option()] = 10,
) -> None:
    """Run Concentrate variants (answer-time ablations) on the XL benchmark, same harness."""
    from tenor.agents.baselines import System
    from tenor.agents.concentrate_system import COMPLETE_MARKER, ConcentrateSystem
    from tenor.agents.retrieval import SentenceTransformerEmbedder
    from tenor.evals.runner import estimate, load_corpus, load_questions, run_eval

    if split == "test":
        raise typer.BadParameter("tune-c never runs on test; use `tenor eval` for that")
    names = [v.strip() for v in variants.split(",") if v.strip()]
    unknown = [v for v in names if v not in TUNE_VARIANTS]
    if unknown:
        raise typer.BadParameter(f"unknown variants {unknown}; choose from {list(TUNE_VARIANTS)}")
    client = LLMClient(offline=offline)
    notes = load_corpus(NORTHWIND_XL)
    questions = load_questions(NORTHWIND_XL, split, limit)
    embedder = SentenceTransformerEmbedder()
    built = [
        ConcentrateSystem(
            client,
            embedder=embedder,
            directory_path=NORTHWIND_XL / "directory.json",
            name="concentrate" if v == "full" else f"c_{v}",
            **TUNE_VARIANTS[v],  # type: ignore[arg-type]
        )
        for v in names
    ]
    for s in built:  # answer-time variants share the cached store; never build one here
        if not (s.store_dir(notes) / COMPLETE_MARKER).exists():
            raise typer.BadParameter(f"no finished store at {s.store_dir(notes)}; build it first")
        s.ingest(notes)
    systems: list[System] = list(built)
    ests = estimate(
        systems, questions, client.model, client=client, batch=batch, latency_sample=latency_sample
    )
    total = sum(e.usd for e in ests)
    for e in ests:
        typer.echo(f"  {e.system:<20} ≈ ${e.usd:6.2f} (incl. judge)")
    typer.echo(f"{len(questions)} questions ({split}) · estimated total ≈ ${total:.2f}")
    if estimate_only:
        return
    if total > BUDGET_CONFIRM_USD and not offline and not yes:
        typer.confirm(
            f"Estimated ${total:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
        )
    report = run_eval(
        systems,
        questions,
        client,
        seed=seed,
        split=split,
        budget_usd=float(os.environ.get("CONCENTRATE_BUDGET_USD") or 25),
        out_dir=out,
        notes=notes,
        batch=batch,
        latency_sample=latency_sample,
        meta={"variants": names, "fixtures": str(NORTHWIND_XL)},
    )
    typer.echo((Path(report["paths"]["md"])).read_text())
    typer.echo(f"spent ${report['meta']['spent_usd']:.2f} · report: {report['paths']['md']}")


# -- workstream C: transcript-retirement curve --


@app.command("retire-curve")
def retire_curve(
    settings: Annotated[
        str, typer.Option(help="Concentrate retirement settings N (days); inf = none.")
    ] = "inf,30,14,7,0",
    b0_settings: Annotated[
        str, typer.Option(help="Settings for b0 top-k with transcripts deleted ('' = skip).")
    ] = "inf,14,0",
    b1: Annotated[bool, typer.Option("--b1/--no-b1", help="Include b1 (summaries only).")] = True,
    split: Annotated[str, typer.Option(help="dev | test | all. Tune only on dev.")] = "dev",
    limit: Annotated[int, typer.Option(help="Max questions (0 = all).")] = 0,
    k: Annotated[int, typer.Option(help="Meetings retrieved by b0/b1.")] = 20,
    seed: Annotated[int, typer.Option(help="Seed for bootstrap CIs.")] = 42,
    fixtures: Annotated[Path, typer.Option(help="Benchmark fixture set.")] = NORTHWIND_XL,
    out: Annotated[Path, typer.Option(help="Report directory.")] = Path("evals/reports/retirement"),
    estimate_only: Annotated[
        bool, typer.Option("--estimate", help="Estimate cost and exit.")
    ] = False,
    evidence_only: Annotated[
        bool, typer.Option("--evidence-only", help="Gold-line coverage of contexts only ($0).")
    ] = False,
    offline: Annotated[bool, typer.Option(help="Use cached LLM results only.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 confirmation.")] = False,
    latency_sample: Annotated[
        int, typer.Option(help="First N questions run interactively (latency sample).")
    ] = 5,
) -> None:
    """Sweep transcript retirement (Concentrate vs b0 with deleted transcripts vs b1)."""
    import json

    from tenor.agents.baselines import MeetingRetriever, SummariesOnlySystem, System
    from tenor.agents.concentrate_system import COMPLETE_MARKER, ConcentrateSystem
    from tenor.agents.retrieval import SentenceTransformerEmbedder
    from tenor.evals import retirement as rt
    from tenor.evals.runner import estimate, load_corpus, load_questions, run_eval

    client = LLMClient(offline=offline)
    budget = float(os.environ.get("CONCENTRATE_BUDGET_USD") or 25)
    notes = load_corpus(fixtures)
    questions = load_questions(fixtures, split, limit)
    embedder = SentenceTransformerEmbedder()
    c_ns, b0_ns = rt.parse_settings(settings), rt.parse_settings(b0_settings)

    concentrates: list[ConcentrateSystem] = []
    for n in c_ns:
        c = ConcentrateSystem(
            client,
            embedder=embedder,
            directory_path=fixtures / "directory.json",
            retire_after_days=n,
        )
        c.name = f"concentrate@{rt.label(n)}"
        concentrates.append(c)
    systems: list[System] = list(concentrates)
    store_dir = concentrates[0].store_dir(notes) if concentrates else None
    complete = store_dir is None or (store_dir / COMPLETE_MARKER).exists()
    if not complete and not estimate_only:
        raise typer.BadParameter(
            f"Concentrate store {store_dir} is not complete; this command never builds memory "
            "(run `tenor eval --systems c` or wait for the build)"
        )
    if complete:  # a finished store is a free cache hit: load it so estimates are exact
        for c in concentrates:
            c.ingest(notes)
    retriever = MeetingRetriever(notes, embedder) if b0_ns or b1 else None
    for n in b0_ns:
        assert retriever is not None
        systems.append(rt.RetiredTopKSystem(client, retriever, k, retire_after_days=n))
    if b1:
        assert retriever is not None
        systems.append(SummariesOnlySystem(client, retriever, k))

    ests = estimate(
        systems,
        questions,
        client.model,
        client=client,
        notes=notes,
        batch=True,
        latency_sample=latency_sample,
    )
    typer.echo(
        f"{fixtures}: {len(notes)} meetings · {len(questions)} questions ({split}) · "
        f"{len(systems)} systems · {client.model} · batch mode"
    )
    usd = {e.system: e.usd - (e.ingest_usd if complete else 0.0) for e in ests}
    for e in ests:
        typer.echo(
            f"  {e.system:<18} ~{e.input_tokens / max(1, e.questions):>8,.0f} in-tok/q "
            f"≈ ${usd[e.system]:6.2f}  (incl. judge)"
        )
    total = sum(usd.values())
    typer.echo(f"  estimated total ≈ ${total:.2f} (before cache reuse) · cap ${budget:.2f}")
    if estimate_only:
        return
    if evidence_only:  # free: build every context, count gold lines, no LLM calls
        for text in (True, False):
            typer.echo(
                f"\nGold transcript lines {'shown as text' if text else 'referenced by ID'}:"
            )
            ev = rt.context_evidence(systems, questions, text=text)
            typer.echo(rt.evidence_markdown(ev, questions))
        return
    if evidence_only:  # free: build every context, count gold lines, no LLM calls
        for text in (True, False):
            typer.echo(
                f"\nGold transcript lines {'shown as text' if text else 'referenced by ID'}:"
            )
            ev = rt.context_evidence(systems, questions, text=text)
            typer.echo(rt.evidence_markdown(ev, questions))
        return
    if not offline and not os.environ.get("ANTHROPIC_API_KEY"):
        raise typer.BadParameter("ANTHROPIC_API_KEY is not set (put it in .env)")
    if total > BUDGET_CONFIRM_USD and not offline and not yes:
        typer.confirm(
            f"Estimated ${total:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
        )
    out.mkdir(parents=True, exist_ok=True)
    report = run_eval(
        systems,
        questions,
        client,
        seed=seed,
        split=split,
        budget_usd=budget,
        out_dir=out,
        notes=notes,
        batch=True,
        latency_sample=latency_sample,
        meta={"k": k, "embedder": embedder.name, "fixtures": str(fixtures), "retirement": True},
    )

    # Post-process: gold evidence in each context, tokens kept, retired share, curve.
    evidence = rt.context_evidence(systems, questions)
    pointers = rt.context_evidence(systems, questions, text=False)
    rows = rt.load_rows(Path(report["paths"]["jsonl"]))
    all_ns = list(dict.fromkeys([*c_ns, *b0_ns]))
    store_meetings = concentrates[0].store.meetings() if concentrates else []
    as_ofs = [q.as_of for q in questions]
    result = {
        "meta": {
            **{k_: report["meta"][k_] for k_ in ("date", "git", "model", "seed", "split")},
            "questions": len(questions),
            "prompts": report["meta"]["prompts"],
            "spent_usd": report["meta"]["spent_usd"],
            "store": str(store_dir),
            "eval_report": report["paths"]["md"],
        },
        "settings": {
            s.name: getattr(s, "retire_after_days", None)
            for s in systems
            if not isinstance(s, SummariesOnlySystem)
        },
        "order": [rt.label(n) for n in rt.ordered(all_ns)],
        "kept": {rt.label(n): rt.tokens_kept(store_meetings, as_ofs, n) for n in all_ns},
        "retired_share": {rt.label(n): rt.retired_share(notes, as_ofs, n) for n in all_ns},
        "curve": rt.curve(rows, evidence, seed, pointers),
    }
    stem = out / f"{report['meta']['date']}.curve"
    Path(f"{stem}.json").write_text(json.dumps(result, indent=2, default=str))
    Path(f"{stem}.md").write_text(rt.to_markdown(result))
    cats = sorted({r["category"] for r in rows})
    Path(f"{stem}.svg").write_text(rt.to_svg(result, cats))
    typer.echo(rt.to_markdown(result))
    typer.echo(f"spent ${report['meta']['spent_usd']:.2f} · curve: {stem}.md / .svg")


# -- workstream F: unit economics --------------------------------------------------------------


def _latest_report() -> Path:
    reports = sorted(Path("evals/reports").glob("*.jsonl"))
    if not reports:
        raise typer.BadParameter("no eval report in evals/reports; pass --report")
    return reports[-1]


@app.command("economics")
def economics_cmd(
    report: Annotated[
        Path | None,
        typer.Option(help="Eval report (.jsonl) for per-question costs; default latest."),
    ] = None,
    cache_root: Annotated[Path, typer.Option(help="Where memory builds live.")] = Path(
        "data/cache"
    ),
    meetings: Annotated[float, typer.Option(help="Meetings per user per month (GUESS).")] = 40,
    questions: Annotated[float, typer.Option(help="Questions per user per month (GUESS).")] = 30,
    ingest_model: Annotated[
        str | None, typer.Option(help="Re-price ingest on this model (default: as built).")
    ] = None,
    answer_model: Annotated[
        str | None, typer.Option(help="Re-price answers on this model (default: the report's).")
    ] = None,
    batch_ingest: Annotated[bool, typer.Option("--batch-ingest/--no-batch-ingest")] = True,
    batch_queries: Annotated[bool, typer.Option("--batch-queries/--no-batch-queries")] = False,
    batch_discount: Annotated[
        float, typer.Option(help="Batch API discount (0.5 = 50% off).")
    ] = 0.5,
    cache_hit: Annotated[
        float | None, typer.Option(help="Prompt-cache hit rate 0-1 (default: as measured).")
    ] = None,
    q_max: Annotated[float, typer.Option(help="X range of the break-even chart.")] = 150,
    out: Annotated[Path, typer.Option(help="Output directory.")] = Path("evals/economics"),
) -> None:
    """$/user/month per system and write-vs-query break-even, from measured runs. Free."""
    import json
    from dataclasses import asdict

    from tenor.evals import economics as ec
    from tenor.evals.svgchart import line_chart
    from tenor.llm.client import PRICING

    for m in (ingest_model, answer_model):
        if m is not None and m not in PRICING:
            raise typer.BadParameter(f"unknown model {m!r}; known: {', '.join(PRICING)}")
    report = report or _latest_report()
    sc = ec.Scenario(
        meetings,
        questions,
        ingest_model,
        answer_model,
        batch_ingest,
        batch_queries,
        batch_discount,
        cache_hit,
    )
    builds = ec.pick_builds(ec.load_ingest(cache_root))
    model, answers = ec.load_answers(report)
    costs = ec.compute(builds, answers, model, sc)
    sens = ec.sensitivity(builds, answers, model, sc)
    out.mkdir(parents=True, exist_ok=True)
    stem = report.stem
    md = ec.to_markdown(costs, builds, sens, sc, str(report))
    (out / f"{stem}.md").write_text(md)
    (out / f"{stem}.json").write_text(
        json.dumps(
            {
                "scenario": asdict(sc),
                "report": str(report),
                "costs": [asdict(c) for c in costs],
                "sensitivity": sens,
                "builds": {k: asdict(v) for k, v in builds.items()},
            },
            indent=2,
            default=str,
        )
    )
    chart = line_chart(
        {
            c.system: pts
            for c, pts in zip(costs, ec.curve(costs, meetings, q_max).values(), strict=True)
        },
        title=f"$/user/month vs questions ({meetings:g} meetings/month)",
        xlabel="questions per user per month",
        ylabel="$ per user per month",
        dashed=[c.system for c in costs if c.kind == "query-time"],
    )
    (out / f"{stem}.breakeven.svg").write_text(chart)
    typer.echo(md)
    typer.echo(f"written: {out / stem}.{{md,json,breakeven.svg}}")


# -- workstream F: scale curve -----------------------------------------------------------------


def _concentrate_source_build(full_dir: Path) -> Path:
    """The current-config Concentrate build of the full corpus if complete, else the most
    recently finished build of the same corpus (older prompts; reported in the meta)."""
    if (full_dir / "complete.json").exists():
        return full_dir
    done = sorted(full_dir.parent.glob("*/complete.json"), key=lambda p: p.stat().st_mtime)
    if not done:
        raise typer.BadParameter(f"no complete Concentrate build under {full_dir.parent}")
    return done[-1].parent


@app.command("scale-curve")
def scale_curve_cmd(
    systems: Annotated[str, typer.Option(help="Comma-separated systems.")] = "b0,b3,c",
    fractions: Annotated[str, typer.Option(help="Nested prefix sizes.")] = "0.25,0.5,1.0",
    anchor: Annotated[float, typer.Option(help="Questions = dev Qs eligible at this size.")] = 0.5,
    n: Annotated[int, typer.Option(help="Stratified sample size of the question set.")] = 40,
    k: Annotated[int, typer.Option(help="Meetings retrieved by b0.")] = 20,
    seed: Annotated[int, typer.Option(help="Sampling + bootstrap seed.")] = 42,
    model: Annotated[
        str | None, typer.Option(help="Answer + judge model (default CONCENTRATE_MODEL / Opus).")
    ] = None,
    concentrate_build: Annotated[
        Path | None, typer.Option(help="Concentrate store dir to rewind (default: auto).")
    ] = None,
    out: Annotated[Path, typer.Option(help="Output root.")] = Path("evals/scale"),
    estimate_only: Annotated[bool, typer.Option("--estimate", help="Estimate and exit.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 confirmation.")] = False,
    budget: Annotated[float, typer.Option(help="Hard cap for the whole curve, USD.")] = 12.0,
) -> None:
    """Accuracy and $/q vs corpus size on nested prefixes of Northwind-XL (batch mode)."""
    import json
    from datetime import UTC, datetime

    from tenor.agents.concentrate_system import ConcentrateSystem
    from tenor.agents.retrieval import SentenceTransformerEmbedder
    from tenor.evals import scale_curve as scv
    from tenor.evals.runner import (
        _git,
        build_systems,
        estimate,
        load_corpus,
        load_questions,
        run_eval,
    )
    from tenor.evals.svgchart import line_chart

    names = tuple(s.strip() for s in systems.split(",") if s.strip())
    fixtures = NORTHWIND_XL
    notes = load_corpus(fixtures)
    plan = scv.plan(
        notes,
        load_questions(fixtures, "dev"),
        [float(f) for f in fractions.split(",")],
        anchor=anchor,
        n=n,
        seed=seed,
    )
    client = LLMClient(model=model)
    embedder = SentenceTransformerEmbedder() if {"b0", "c"} & set(names) else None
    source = None
    if "c" in names:
        probe = ConcentrateSystem(client, directory_path=fixtures / "directory.json")
        source = concentrate_build or _concentrate_source_build(probe.store_dir(notes))
    scratch = Path("data/cache/scale-curve/concentrate")
    built_by_size = {}
    total = 0.0
    typer.echo(f"{client.model} · k={k} · Concentrate rewound from {source}")
    for f, (sub, qs) in plan.items():
        base = [s for s in names if s != "c"]
        built = build_systems(tuple(base), client, sub, embedder, k, fixtures=fixtures)
        if "c" in names and source is not None:
            c = ConcentrateSystem(
                client,
                embedder=embedder,
                cache_root=scratch,
                directory_path=fixtures / "directory.json",
            )
            dst = c.store_dir(sub)
            if not (dst / scv.COMPLETE_MARKER).exists():
                scv.place_rewound(source, dst, {m.note_id for m in sub})
            c.ingest(sub)
            built.append(c)
        built_by_size[f] = built
        ests = estimate(built, qs, client.model, client=client, batch=True, latency_sample=0)
        usd = sum(e.usd for e in ests)
        total += usd
        typer.echo(
            f"  {f:>5.0%}: {len(sub):>3} meetings · {len(qs):>2} questions · "
            + " · ".join(f"{e.system} ${e.usd:.2f}" for e in ests)
            + f" = ${usd:.2f}"
        )
    typer.echo(f"  estimated total ≈ ${total:.2f} (before LLM-cache reuse) · cap ${budget:.2f}")
    if estimate_only:
        return
    if total > budget:
        raise typer.BadParameter(f"estimate ${total:.2f} is over --budget ${budget:.2f}")
    if total > BUDGET_CONFIRM_USD and not yes:
        typer.confirm(
            f"Estimated ${total:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = out / stamp
    results = {}
    spent = 0.0
    for f, (sub, qs) in plan.items():
        rep = run_eval(
            built_by_size[f],
            qs,
            client,
            seed=seed,
            split="dev",
            budget_usd=budget,
            out_dir=root / f"p{round(f * 100):03d}",
            notes=sub,
            batch=True,
            latency_sample=0,
            meta={"k": k, "fraction": f, "meetings": len(sub), "concentrate_source": str(source)},
        )
        spent = rep["meta"]["spent_usd"]  # one client across sizes: its ledger is cumulative
        rows = [json.loads(line) for line in Path(rep["paths"]["jsonl"]).read_text().splitlines()]
        results[f] = {"meetings": len(sub), "rows": rows, "report": rep["paths"]["json"]}
        typer.echo(f"  {f:.0%} done · spent so far ${spent:.2f}")
    summary = scv.summarize_curve(results)
    meta = {
        "model": client.model,
        "k": k,
        "seed": seed,
        "git": _git()["sha"],
        "date": stamp,
        "anchor": anchor,
        "spent_usd": spent,
        "concentrate_source": str(source),
        "reports": {str(f): r["report"] for f, r in results.items()},
    }
    (root / "scale.json").write_text(json.dumps({"meta": meta, **summary}, indent=2))
    md = scv.to_markdown(summary, meta)
    (root / "scale.md").write_text(md)
    for metric, label, key in (
        ("accuracy", "accuracy on questions common to all sizes", "accuracy_common"),
        ("cost", "$ per question", "cost_per_q"),
    ):
        series: dict[str, list[tuple[float, float]]] = {
            s: [
                (p["meetings"], p[key] * (100 if metric == "accuracy" else 1))
                for p in d["points"]
                if p[key] is not None
            ]
            for s, d in summary["systems"].items()
        }
        (root / f"scale_{metric}.svg").write_text(
            line_chart(
                series,
                title=f"{label} vs corpus size",
                xlabel="meetings in corpus",
                ylabel="% correct" if metric == "accuracy" else "$/question",
                markers=True,
                y_max=100 if metric == "accuracy" else None,
            )
        )
    typer.echo(md)
    typer.echo(f"spent ${spent:.2f} · written: {root}")


# -- real-data slice (workstream B) -------------------------------------------------------------
# Everything these commands read or write about the user's meetings stays under data/real/
# (gitignored). Granola is reached only through the read-only allowlist in evals.real_slice.

real_kit = typer.Typer(no_args_is_help=True, help="Write and verify questions about real meetings.")
app.add_typer(real_kit, name="real-kit")
REAL_DB = Path("data/real.db")


def _real_notes(db: Path) -> list:  # type: ignore[type-arg]
    if not db.exists():
        raise typer.BadParameter(f"{db} not found: run `tenor sync --source granola-mcp`")
    store = NoteStore(db)
    try:
        return list(store.iter_notes())
    finally:
        store.close()


def _real_client(offline: bool = False, model: str | None = None) -> LLMClient:
    from tenor.evals.real_slice import REAL_DIR

    return LLMClient(model=model, cache_dir=REAL_DIR / "cache" / "llm", offline=offline)


class SplitName(StrEnum):
    dev = "dev"  # the questions already seen while building and tuning
    test = "test"  # hand-written fresh questions, held out (import-test)


SplitOpt = Annotated[SplitName, typer.Option("--split", help="dev (seen) or test (held out).")]


@real_kit.command("status")
def real_status(
    split: SplitOpt = SplitName.dev,
    product: Annotated[
        list[str] | None,
        typer.Option("--product", help="Also count recorded answers from this product."),
    ] = None,
) -> None:
    """Questions per category: drafted, verified, rejected vs the ~30-question target."""
    from tenor.blackbox.kit import ProductSystem
    from tenor.blackbox.products import product_name
    from tenor.evals.real_slice import coverage, load_split

    qs = load_split(split.value)
    typer.echo(f"split: {split.value}")
    typer.echo(f"{'category':<18} draft verified rejected target")
    for c, v in coverage(qs).items():
        typer.echo(
            f"{c:<18} {v['draft']:>5} {v['verified']:>8} {v['rejected']:>8} {v['target']:>6}"
        )
    typer.echo(f"verified: {sum(q.status == 'verified' for q in qs)} of {len(qs)}")
    verified = [q for q in qs if q.status == "verified"]
    for p in product or []:
        name = product_name(p)
        typer.echo(f"{name}: {ProductSystem(name, None).recorded(verified)} answers recorded")


@real_kit.command("directory")
def real_directory_cmd(db: Annotated[Path, typer.Option()] = REAL_DB) -> None:
    """Write data/real/directory.json (people from attendee metadata) for Concentrate."""
    import json as _json

    from tenor.evals.real_slice import REAL_DIR, real_directory

    out = REAL_DIR / "directory.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(real_directory(_real_notes(db)), indent=1))
    typer.echo(f"wrote {out} (private; edit names freely)")


@real_kit.command("draft")
def real_draft(
    db: Annotated[Path, typer.Option()] = REAL_DB,
    extra: Annotated[float, typer.Option(help="Draft this many x the gap (for rejections).")] = 1.5,
    estimate_only: Annotated[bool, typer.Option("--estimate")] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """LLM-draft candidate questions for categories still short of their target (dev split
    only: the test split is hand-written, see `real-kit import-test`)."""
    import math
    from datetime import date as _date

    from tenor.evals.real_slice import (
        DraftOutput,
        coverage,
        draft_request,
        drafts_to_questions,
        load_questions,
        save_questions,
    )
    from tenor.llm.client import cost_usd, estimate_tokens

    notes = _real_notes(db)
    qs = load_questions()
    wanted = {
        c: max(0, math.ceil((v["target"] - v["verified"] - v["draft"]) * extra))
        for c, v in coverage(qs).items()
    }
    if not any(wanted.values()):
        typer.echo("every category has enough drafts or verified questions")
        return
    today = _date.today()
    client = _real_client()
    req = draft_request(notes, wanted, today)
    usd = cost_usd(
        client.model, estimate_tokens(req.system) + estimate_tokens(req.user), req.max_tokens // 2
    )
    typer.echo(f"{len(notes)} meetings · drafting {sum(wanted.values())} questions ≈ ${usd:.2f}")
    if estimate_only:
        return
    if usd > BUDGET_CONFIRM_USD and not yes:
        typer.confirm(
            f"Estimated ${usd:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
        )
    new = drafts_to_questions(client.structured(req, DraftOutput), qs, today)
    save_questions([*qs, *new])
    typer.echo(f"added {len(new)} drafts · spent ${client.ledger.spent_usd():.2f}")
    typer.echo("next: `uv run tenor real-kit review` (you verify every gold answer)")


@real_kit.command("review")
def real_review(
    show_evidence: Annotated[bool, typer.Option("--evidence/--no-evidence")] = True,
    split: SplitOpt = SplitName.dev,
) -> None:
    """Walk through drafts: [a]ccept, [e]dit, [r]eject, [s]kip, [q]uit. Saves after each."""
    from tenor.evals.real_slice import load_split, questions_file, save_questions

    qs = load_split(split.value)
    path = questions_file(split.value)
    todo = [i for i, q in enumerate(qs) if q.status == "draft"]
    typer.echo(f"{len(todo)} drafts to review. Check each gold answer against your memory/notes.")
    for n, i in enumerate(todo, 1):
        q = qs[i]
        typer.echo(f"\n[{n}/{len(todo)}] {q.id} · {q.category}")
        typer.echo(f"  Q: {q.question}")
        typer.echo(f"  Gold: {q.gold_answer}")
        typer.echo(f"  Key terms: {', '.join(q.key_terms) or '-'}")
        if show_evidence and q.evidence:
            typer.echo(f"  Evidence: {q.evidence}")
        choice = typer.prompt("  [a]ccept/[e]dit/[r]eject/[s]kip/[q]uit", default="s").lower()
        if choice.startswith("q"):
            break
        if choice.startswith("a"):
            qs[i] = q.model_copy(update={"status": "verified"})
        elif choice.startswith("r"):
            note = typer.prompt("  why (optional)", default="")
            qs[i] = q.model_copy(update={"status": "rejected", "reviewer_note": note})
        elif choice.startswith("e"):
            question = typer.prompt("  question", default=q.question)
            gold = typer.prompt("  gold answer (UNANSWERABLE if none)", default=q.gold_answer)
            terms = typer.prompt(
                "  key terms, comma-separated", default=", ".join(q.key_terms) or "-"
            )
            key_terms = [] if terms.strip() == "-" else [t.strip() for t in terms.split(",")]
            qs[i] = q.model_copy(
                update={
                    "question": question,
                    "gold_answer": gold,
                    "key_terms": [t for t in key_terms if t],
                    "status": "verified",
                    "reviewer_note": "edited",
                }
            )
        save_questions(qs, path)
    typer.echo(f"\nverified: {sum(q.status == 'verified' for q in qs)} of {len(qs)}")


@real_kit.command("add")
def real_add(split: SplitOpt = SplitName.dev) -> None:
    """Write your own question (counts as verified: you are the source of the gold answer)."""
    from datetime import date as _date

    from tenor.evals.real_slice import (
        CATEGORIES,
        RealQuestion,
        load_split,
        questions_file,
        save_questions,
    )

    qs = load_split(split.value)
    category = typer.prompt(f"category ({' | '.join(CATEGORIES)})")
    if category not in CATEGORIES:
        raise typer.BadParameter(f"unknown category {category!r}")
    question = typer.prompt("question")
    gold = "UNANSWERABLE" if category == "unanswerable" else typer.prompt("gold answer")
    terms = (
        "" if category == "unanswerable" else typer.prompt("key terms, comma-separated", default="")
    )
    prefix = "test" if split is SplitName.test else "real"
    q = RealQuestion(
        id=f"{prefix}-{len(qs) + 1:03d}",
        category=category,
        question=question,
        gold_answer=gold,
        key_terms=[t.strip() for t in terms.split(",") if t.strip()],
        as_of=_date.today(),
        status="verified",
        origin="user",
        split=split.value,
    )
    save_questions([*qs, q], questions_file(split.value))
    typer.echo(f"added {q.id}")


@app.command("real-slice")
def real_slice_cmd(
    systems: Annotated[
        str,
        typer.Option(
            help="granola (Granola's real agent), c, c_on_abstain, c_always (Concentrate with "
            "the transcript fallback), b0, b3; comma-separated."
        ),
    ] = "granola,c,b0,b3",
    db: Annotated[Path, typer.Option()] = REAL_DB,
    k: Annotated[int, typer.Option(help="Meetings b0 retrieves.")] = 2,
    seed: Annotated[int, typer.Option()] = 42,
    budget: Annotated[float, typer.Option(help="Hard cap on API spend (USD).")] = 5.0,
    estimate_only: Annotated[bool, typer.Option("--estimate")] = False,
    offline: Annotated[
        bool, typer.Option(help="Cached LLM results / recorded Granola only.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    qids_from: Annotated[
        Path | None,
        typer.Option(help="Only the questions answered in this earlier report (.jsonl)."),
    ] = None,
    split: SplitOpt = SplitName.dev,
) -> None:
    """Answer the verified real questions with every system and judge them (judge.v1)."""
    import json as _json
    from dataclasses import asdict

    from tenor.agents.baselines import AgenticSystem, MeetingRetriever, System, TopKSystem
    from tenor.agents.concentrate_system import ConcentrateSystem
    from tenor.agents.retrieval import SentenceTransformerEmbedder
    from tenor.evals.real_slice import (
        REAL_DIR,
        GranolaAgentSystem,
        aggregate_only,
        load_split,
        run_slice,
    )
    from tenor.evals.runner import estimate

    names = [s.strip() for s in systems.split(",") if s.strip()]
    notes = _real_notes(db)
    questions = [q for q in load_split(split.value) if q.status == "verified"]
    if qids_from is not None:  # e.g. re-run a variant on exactly the questions of a past run
        keep = {_json.loads(line)["qid"] for line in qids_from.read_text().splitlines() if line}
        questions = [q for q in questions if q.id in keep]
    if not questions:
        raise typer.BadParameter("no verified questions: run `tenor real-kit review` first")
    directory = REAL_DIR / "directory.json"
    if any(n.startswith("c") for n in names) and not directory.exists():
        raise typer.BadParameter(f"{directory} missing: run `tenor real-kit directory`")
    client = _real_client(offline)
    embedder = (
        SentenceTransformerEmbedder()
        if {"c", "c_on_abstain", "c_always", "b0"} & set(names)
        else None
    )
    built: list[System] = []
    concentrate: ConcentrateSystem | None = None
    with ExitStack() as stack:
        for n in names:
            if n == "granola":
                caller = None
                if not offline and not estimate_only:
                    from tenor.sources.mcp_transport import LiveMcpCaller

                    caller = stack.enter_context(LiveMcpCaller())
                built.append(GranolaAgentSystem(caller))
            elif n in ("c", "c_on_abstain", "c_always"):  # c_*: transcript fallback modes
                mode = n.removeprefix("c_") if n != "c" else "off"
                variant = ConcentrateSystem(
                    client,
                    embedder=embedder,
                    directory_path=directory,
                    cache_root=REAL_DIR / "cache" / "concentrate",
                    fallback=mode,
                    name=None if n == "c" else f"concentrate_{mode}",
                )
                concentrate = concentrate or variant
                built.append(variant)
            elif n == "b0":
                built.append(TopKSystem(client, MeetingRetriever(notes, embedder), k))
            elif n == "b3":
                built.append(AgenticSystem(client, notes))
            else:
                raise typer.BadParameter(f"unknown system {n!r}")
        ests = estimate(built, questions, client.model, notes=notes)  # type: ignore[arg-type]
        typer.echo(f"{len(notes)} meetings · {len(questions)} verified questions · {client.model}")
        for e in ests:
            ingest = f", ingest ${e.ingest_usd:.2f}" if e.ingest_usd else ""
            typer.echo(f"  {e.system:<14} ≈ ${e.usd:5.2f} (incl. judge{ingest})")
        total = sum(e.usd for e in ests)
        typer.echo(f"  estimated total ≈ ${total:.2f} (before cache reuse) · cap ${budget:.2f}")
        if estimate_only:
            return
        if total > BUDGET_CONFIRM_USD and not offline and not yes:
            typer.confirm(
                f"Estimated ${total:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
            )
        ingested: dict[str, object] = {}
        if concentrate is not None:
            stats = concentrate.ingest(notes)
            ingested["concentrate"] = {k2: v for k2, v in asdict(stats).items() if k2 != "detail"}
            for s in built:  # fallback variants share the (now cached) store
                if isinstance(s, ConcentrateSystem) and s is not concentrate:
                    s.ingest(notes)

        def spent() -> float:
            extra = concentrate.ingest_ledger.spent_usd() if concentrate is not None else 0.0
            return client.ledger.spent_usd() + extra

        cats: dict[str, int] = {}
        for q in questions:
            cats[q.category] = cats.get(q.category, 0) + 1
        report = run_slice(
            built,
            questions,
            client,
            seed=seed,
            budget_usd=budget,
            out_dir=REAL_DIR / "reports",
            spent=spent,
            split=split.value,
            meta={
                "meetings": len(notes),
                "k": k,
                "category_counts": cats,
                "ingest": ingested,
                "embedder": getattr(embedder, "name", None),
            },
        )
    agg = REAL_DIR / "reports" / f"{report['meta']['date']}.aggregate.json"
    agg.write_text(_json.dumps(aggregate_only(report), indent=2, default=str))
    typer.echo(Path(report["paths"]["md"]).read_text())
    typer.echo(f"spent ${spent():.2f} · report: {report['paths']['md']} · aggregate: {agg}")


@real_kit.command("probe")
def real_probe(
    probes: Annotated[Path, typer.Argument(help="JSON list of probes (keep it in data/real/).")],
) -> None:
    """Run a read-only probe of Granola's MCP; raw output -> data/real/probe/<stamp>.jsonl."""
    import json as _json
    import statistics
    from datetime import UTC, datetime

    from tenor.evals.real_slice import REAL_DIR, run_probes
    from tenor.sources.mcp_transport import LiveMcpCaller

    spec = _json.loads(probes.read_text())
    out = REAL_DIR / "probe" / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.jsonl"
    with LiveMcpCaller() as live:
        res = run_probes(live, spec, out)
    by_tool: dict[str, list[float]] = {}
    for r in res:
        if not r["error"]:
            by_tool.setdefault(r["tool"], []).append(r["latency_s"])
    for tool, xs in sorted(by_tool.items()):
        typer.echo(
            f"{tool:<24} n={len(xs):>2} median {statistics.median(xs):5.1f}s  max {max(xs):5.1f}s"
        )
    typer.echo(f"errors: {sum(bool(r['error']) for r in res)} · raw: {out}")


@real_kit.command("serve")
def real_serve(
    db: Annotated[Path, typer.Option()] = REAL_DB,
    store: Annotated[Path | None, typer.Option(help="Build dir; default: newest complete.")] = None,
    embeddings: Annotated[bool, typer.Option(help="Dense embeddings, as in the eval.")] = True,
) -> None:
    """Serve Concentrate's memory of the REAL meetings over MCP stdio (WS-D's read-only server).

    Same tools as `mcp-serve`; ask_memory(answer=true) caches under data/real/ (private).
    """
    from tenor.evals.real_slice import open_real_memory
    from tenor.mcp_server.server import build_server
    from tenor.mcp_server.service import Answerer

    embedder = None
    if embeddings:
        from tenor.agents.retrieval import SentenceTransformerEmbedder

        embedder = SentenceTransformerEmbedder()
    try:
        svc = open_real_memory(
            _real_notes(db), store_dir=store, embedder=embedder, answerer=Answerer(_real_client())
        )
    except FileNotFoundError as exc:
        typer.echo(f"{exc}\nBuild it first: `tenor real-slice --systems c` (or ingest).", err=True)
        raise typer.Exit(1) from exc
    logging.getLogger(__name__).warning(
        "concentrate MCP: serving real memory %s (%d meetings)",
        svc.info["store"],
        len(svc.meetings),
    )
    build_server(svc).run("stdio")


@real_kit.command("spotcheck")
def real_spotcheck(
    reports: Annotated[list[Path], typer.Argument(help="Report .jsonl files to combine.")],
    split: SplitOpt = SplitName.dev,
    product: Annotated[
        list[str] | None,
        typer.Option("--product", help="Only these products/systems (repeatable)."),
    ] = None,
) -> None:
    """Write data/real/reports/spotcheck[.test].md: where any system disagrees with the gold."""
    import json as _json

    from tenor.evals.real_slice import REAL_DIR, load_split, spotcheck_markdown

    rows = [_json.loads(x) for p in reports for x in p.read_text().splitlines() if x]
    if product:
        rows = [
            r
            for r in rows
            if any(r["system"] == p or r["system"].startswith(f"{p}_") for p in product)
        ]
    out = REAL_DIR / "reports" / ("spotcheck.md" if split is SplitName.dev else "spotcheck.test.md")
    out.write_text(spotcheck_markdown(rows, load_split(split.value)))
    typer.echo(f"wrote {out} (private)")


# -- black-box track (P1-B): any meeting-memory MCP, public interfaces only ----------------------
# Products: `granola` (query_granola_meetings), `concentrate` (ask_memory over stdio), or a JSON
# spec (docs/method/product.example.json). Answers, questions and per-question reports stay
# under data/real/; only the aggregate passes the committable-report check.


@real_kit.command("import-test")
def real_import_test(
    md: Annotated[
        Path, typer.Argument(help="The filled-in test template (keep it in data/real/).")
    ],
) -> None:
    """Parse hand-written Q/A/KEY/KIND blocks into the held-out test split (re-runs skip dupes)."""
    from datetime import date as _date

    from tenor.blackbox.kit import TemplateError, parse_test_markdown
    from tenor.evals.real_slice import (
        CATEGORIES,
        TEST_QUESTIONS_FILE,
        RealQuestion,
        load_split,
        save_questions,
    )

    try:
        parsed = parse_test_markdown(md.read_text(), CATEGORIES)
    except TemplateError as exc:
        raise typer.BadParameter(f"{md}: {exc}") from exc
    qs = load_split("test")
    seen = {q.question.strip().lower() for q in qs}
    new: list[RealQuestion] = []
    for p in parsed:
        if p["question"].strip().lower() in seen:
            continue
        seen.add(p["question"].strip().lower())
        new.append(
            RealQuestion(
                id=f"test-{len(qs) + len(new) + 1:03d}",
                as_of=_date.today(),
                status="verified",
                origin="user",
                split="test",
                **p,
            )
        )
    save_questions([*qs, *new], TEST_QUESTIONS_FILE)
    cats: dict[str, int] = {}
    for q in [*qs, *new]:
        cats[q.category] = cats.get(q.category, 0) + 1
    typer.echo(
        f"{len(parsed)} blocks parsed · {len(new)} new · test split now {len(qs) + len(new)}"
    )
    typer.echo("  " + ", ".join(f"{c} {n}" for c, n in sorted(cats.items())))


@real_kit.command("run")
def real_run(
    product: Annotated[
        list[str],
        typer.Option("--product", help="granola, concentrate, or a spec name/path (repeatable)."),
    ],
    split: SplitOpt = SplitName.dev,
    seed: Annotated[int, typer.Option()] = 42,
    budget: Annotated[float, typer.Option(help="Hard cap on judge spend (USD).")] = 2.0,
    estimate_only: Annotated[bool, typer.Option("--estimate")] = False,
    offline: Annotated[
        bool, typer.Option(help="Recorded answers and cached judge verdicts only.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    commit_to: Annotated[
        Path | None,
        typer.Option(help="Also write the aggregate here (checked: no per-question content)."),
    ] = None,
) -> None:
    """Ask every verified question of the split to each product over MCP, judge (judge.v1)."""
    from tenor.blackbox.kit import ProductSystem, write_committable
    from tenor.blackbox.products import open_product, product_name
    from tenor.evals.real_slice import REAL_DIR, load_split, run_slice
    from tenor.evals.runner import estimate

    questions = [q for q in load_split(split.value) if q.status == "verified"]
    if not questions:
        raise typer.BadParameter(f"no verified {split.value} questions (status / import-test)")
    client = _real_client(offline)
    with ExitStack() as stack:
        systems = []
        for p in product:
            name = product_name(p)
            recorded = ProductSystem(name, None)
            missing = len(questions) - recorded.recorded(questions)
            live = open_product(p, stack) if missing and not (offline or estimate_only) else None
            systems.append(ProductSystem(name, live))
            typer.echo(f"  {name:<14} {missing} of {len(questions)} to ask live")
        skipped = 0
        if offline:  # score only what every product has a recorded answer for, and say so
            kept = [q for q in questions if all(s.recorded([q]) for s in systems)]
            skipped, questions = len(questions) - len(kept), kept
            typer.echo(f"  offline: {skipped} questions skipped (no recorded answer)")
            if not questions:
                raise typer.BadParameter("nothing recorded yet: run once without --offline")
        ests = estimate(systems, questions, client.model)  # type: ignore[arg-type]
        total = sum(e.usd for e in ests)
        typer.echo(
            f"{len(questions)} {split.value} questions · judge {client.model} "
            f"≈ ${total:.2f} before cache reuse · cap ${budget:.2f}"
        )
        if estimate_only:
            return
        if total > BUDGET_CONFIRM_USD and not offline and not yes:
            typer.confirm(
                f"Estimated ${total:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
            )
        cats: dict[str, int] = {}
        for q in questions:
            cats[q.category] = cats.get(q.category, 0) + 1
        report = run_slice(
            systems,  # type: ignore[arg-type]
            questions,
            client,
            seed=seed,
            budget_usd=budget,
            out_dir=REAL_DIR / "reports",
            split=split.value,
            meta={
                "products": [s.name for s in systems],
                "category_counts": cats,
                "skipped_unrecorded": skipped,
            },
        )
    private = [t for q in questions for t in (q.question, q.gold_answer)]
    private += [s.answer(q).text for s in systems for q in questions if s.recorded([q])]
    agg = write_committable(
        report, REAL_DIR / "reports" / f"{report['meta']['date']}.aggregate.json", private
    )
    if commit_to is not None:
        write_committable(report, commit_to, private)
    typer.echo(Path(report["paths"]["md"]).read_text())
    typer.echo(f"spent ${client.ledger.spent_usd():.2f} · report: {report['paths']['md']}")
    typer.echo(f"aggregate (safe to commit): {commit_to or agg}")


# -- figures (P1-F) ----------------------------------------------------------------------------

_REAL_REPORTS = Path("data/real/reports")


@app.command("figures")
def figures_cmd(
    stamp: Annotated[str, typer.Argument(help="Synthetic report stamp, e.g. 20261103T002456Z.")],
    out: Annotated[Path, typer.Option(help="Output directory.")] = Path("docs/figures"),
    check: Annotated[
        bool, typer.Option("--check", help="Don't write; fail if committed figures differ.")
    ] = False,
    refresh_real: Annotated[
        bool,
        typer.Option(
            "--refresh-real",
            help="Rebuild docs/figures/data/real_slice.json from the private data/real reports.",
        ),
    ] = False,
    refresh_ami: Annotated[
        bool,
        typer.Option(
            "--refresh-ami",
            help="Rebuild evals/reports/ami/notes_check.json (counts only) from the gitignored "
            "data/ami/results/facts files.",
        ),
    ] = False,
    real_main: Annotated[Path, typer.Option(help="Real run: Granola agent + Concentrate.")] = (
        _REAL_REPORTS / "20261103T004019Z.jsonl"
    ),
    real_topk: Annotated[Path, typer.Option(help="Real run: b0 top-k on all questions.")] = (
        _REAL_REPORTS / "20261103T013240Z.jsonl"
    ),
    real_fallback: Annotated[Path, typer.Option(help="Real fallback replay (DEV).")] = (
        _REAL_REPORTS / "fallback_dev_replay.jsonl"
    ),
) -> None:
    """Render every results figure as deterministic SVG into docs/figures/ (PLAN.md section 5)."""
    import json as _json

    from tenor.evals import figures as F

    if refresh_real:
        snap = F.snapshot_real(real_main, real_topk, real_fallback)
        F.REAL_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        F.REAL_SNAPSHOT.write_text(_json.dumps(snap, indent=2, sort_keys=True) + "\n")
        typer.echo(f"wrote {F.REAL_SNAPSHOT} (aggregates only)")
    if refresh_ami:
        from tenor.evals import figures_ami as FA

        facts = Path("data/ami/results/facts")
        snap = FA.snapshot_notes_check(
            facts / "stage2.json",
            facts / "human_verification_v3.jsonl",
            facts / "stage2.claims.jsonl",
        )
        FA.NOTES_CHECK.parent.mkdir(parents=True, exist_ok=True)
        FA.NOTES_CHECK.write_text(_json.dumps(snap, indent=2, sort_keys=True) + "\n")
        typer.echo(f"wrote {FA.NOTES_CHECK} (counts only)")
    rendered, skipped = F.render(F.load_inputs(stamp))
    for note in skipped:
        typer.echo(f"skipped {note}", err=True)
    if check:
        bad = F.check(rendered, out)
        if bad:
            typer.echo(
                "figures out of date: " + ", ".join(bad) + f"\nrun `tenor figures {stamp}`",
                err=True,
            )
            raise typer.Exit(1)
        typer.echo(f"figures: {len(rendered)} files match {out}")
        return
    for p in F.write(rendered, out):
        typer.echo(f"wrote {p}")


# -- notes-sufficient vs transcript-needed (Phase 2, $0) ---------------------------------------


@app.command("notes-vs-transcript")
def notes_vs_transcript_cmd(
    seed: Annotated[int, typer.Option(help="Bootstrap seed.")] = 0,
    real: Annotated[
        bool, typer.Option(help="Recompute the real-slice aggregates (needs private data/real).")
    ] = True,
) -> None:
    """Tag questions notes-sufficient / transcript-needed and re-analyse saved runs by tag ($0)."""
    from tenor.evals import notes_vs_transcript as N

    for p in N.write(seed, real=real):
        typer.echo(f"wrote {p}")


# -- deletion simulation (P2-D) ----------------------------------------------------------------


@app.command("deletion-sim")
def deletion_sim_cmd(
    systems: Annotated[
        str,
        typer.Option(
            help="granola (A), notes_only (B), notes_facts (C), notes_facts_fallback (D); "
            "comma-separated. See docs/tracks/RETENTION.md."
        ),
    ] = "granola,notes_only,notes_facts,notes_facts_fallback",
    split: SplitOpt = SplitName.dev,
    tags: Annotated[
        Path | None, typer.Option(help="P2-T's question tag file (JSON or JSONL); optional.")
    ] = None,
    judge2: Annotated[
        str, typer.Option(help="Second judge model (same judge.v1 prompt), or 'none'.")
    ] = "claude-sonnet-5-5",
    db: Annotated[Path, typer.Option()] = REAL_DB,
    seed: Annotated[int, typer.Option()] = 42,
    budget: Annotated[float, typer.Option(help="Hard cap on API spend (USD).")] = 5.0,
    quote_only: Annotated[
        bool,
        typer.Option(
            "--quote",
            help="Exact cost quote (free token counting, cached LLM results only), no spend.",
        ),
    ] = False,
    test_n: Annotated[
        int, typer.Option(help="--quote: test questions to price when none are imported yet.")
    ] = 30,
    offline: Annotated[
        bool, typer.Option(help="Cached LLM results / recorded Granola only.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    commit_to: Annotated[
        Path | None,
        typer.Option(help="Also write the aggregate here (checked: no per-question content)."),
    ] = None,
) -> None:
    """Simulate transcript deletion on the real questions: Granola vs notes-only vs notes+facts
    vs notes+facts+fallback, same answer model and judge. Nothing paid runs without --yes or a
    confirmation; start with --quote."""
    import functools
    import json as _json
    import statistics

    from tenor.agents.baselines import Answer, MeetingRetriever, System, TopKSystem
    from tenor.agents.concentrate_system import COMPLETE_MARKER, ConcentrateSystem
    from tenor.agents.retrieval import SentenceTransformerEmbedder
    from tenor.blackbox.kit import write_committable
    from tenor.evals import deletion_sim as D
    from tenor.evals.real_slice import (
        REAL_DIR,
        GranolaAgentSystem,
        load_split,
    )
    from tenor.llm.client import count_tokens
    from tenor.memory.fallback import says_not_covered

    names = [s.strip() for s in systems.split(",") if s.strip()]
    unknown = [n for n in names if n not in D.CONDITIONS]
    if unknown:
        raise typer.BadParameter(f"unknown conditions {unknown}; choose from {D.CONDITIONS}")
    notes = _real_notes(db)
    questions = [q for q in load_split(split.value) if q.status == "verified"]
    if not questions and not (quote_only and split is SplitName.test):
        raise typer.BadParameter(
            f"no verified {split.value} questions"
            + (": `tenor real-kit import-test data/real/test_questions.md`" if split else "")
        )
    directory = REAL_DIR / "directory.json"
    client = _real_client(offline or quote_only)
    j2 = (
        None
        if judge2 == "none"
        else LLMClient(
            model=judge2, cache_dir=REAL_DIR / "cache" / "llm", offline=offline or quote_only
        )
    )
    if j2 is not None and j2.model == client.model:
        raise typer.BadParameter(f"the second judge must differ from the primary ({client.model})")
    needs_memory = {"notes_facts", "notes_facts_fallback"} & set(names)
    embedder = SentenceTransformerEmbedder() if needs_memory or quote_only else None
    tag_map = D.load_tags(tags)
    with ExitStack() as stack:
        built: list[System] = []
        for n in names:
            if n == "granola":
                caller = None
                if not offline and not quote_only:
                    from tenor.sources.mcp_transport import LiveMcpCaller

                    caller = stack.enter_context(LiveMcpCaller())
                built.append(GranolaAgentSystem(caller))
            elif n == "notes_only":
                built.append(D.NotesOnlySystem(client, notes))
            else:
                cls = D.NotesFactsSystem if n == "notes_facts" else D.NotesFactsFallbackSystem
                s = cls(
                    client,
                    embedder=embedder,
                    directory_path=directory,
                    cache_root=REAL_DIR / "cache" / "concentrate",
                )
                if not (s.store_dir(notes) / COMPLETE_MARKER).exists():
                    raise typer.BadParameter(
                        "no complete Concentrate build of data/real.db (building it costs money): "
                        "run `tenor real-slice --systems c --estimate` and ask first"
                    )
                s.ingest(notes)  # cached build: free
                built.append(s)

        if quote_only:
            counter = functools.partial(
                count_tokens, model=client.model, cache_dir=client.cache_dir
            )
            dev = [q for q in load_split("dev") if q.status == "verified"]
            # Calibrate on the real-slice calls already cached: Concentrate (c) and top-k (b0).
            ref_c = ConcentrateSystem(
                client,
                embedder=embedder,
                directory_path=directory,
                cache_root=REAL_DIR / "cache" / "concentrate",
            )
            ref_c.ingest(notes)
            ref_b0 = TopKSystem(client, MeetingRetriever(notes, embedder), 2)
            pairs, texts, fired = [], [], []
            for ref in (ref_c, ref_b0):
                for q in dev:
                    req = ref.turn_request(q)
                    done = D.cached_turn(client, req)
                    if done is None:
                        continue
                    user = req.messages[0]["content"]
                    pairs.append(
                        (f"{req.system}\n\n{user}", done.total_input_tokens, done.output_tokens)
                    )
                    ans = Answer.model_validate_json(done.text)
                    texts.append(ans.answer)
                    if ref is ref_c:
                        fired.append(says_not_covered(ans.answer, ans.abstain))
            cal = D.calibrate(
                pairs,
                D.judge_cache_pairs(client.cache_dir, client.model),
                texts,
                counter,
                fire_rate=statistics.fmean(fired) if fired else 0.5,
            )
            granola = next((s for s in built if isinstance(s, GranolaAgentSystem)), None)

            def recorded(q: object) -> tuple[str, bool] | None:
                if granola is None or not granola.recorded([q]):
                    return None
                a = granola.answer(q)
                return a.text, bool(a.abstained)

            lines = []
            shared: set[Path] = set()  # identical requests across conditions are paid once
            by_cond = dict(zip(names, built, strict=True))
            for n in names:
                if questions:
                    lines.append(
                        D.quote_condition(
                            by_cond[n],
                            n,
                            questions,
                            client,
                            j2,
                            counter,
                            cal,
                            split=split.value,
                            granola_answer=recorded,
                            shared=shared,
                        )
                    )
                else:  # test split not imported yet: price per dev question, scaled
                    base = D.quote_condition(
                        by_cond[n],
                        n,
                        dev,
                        client,
                        j2,
                        counter,
                        cal,
                        split="dev",
                        granola_answer=None,
                        shared=shared,
                    )
                    lines.append(D.scale_line(base, test_n, "test"))
                if n == "granola":
                    missing = len(questions) - granola.recorded(questions) if granola else 0
                    lines[-1].note = (
                        f"Granola answers $0 to us (hosted); {missing} to ask live over MCP"
                        if questions
                        else f"Granola answers $0 to us; all {test_n} asked live over MCP"
                    )
            md = D.quote_markdown(lines, cal, client.model, j2.model if j2 else "none")
            out = REAL_DIR / "reports" / f"deletion_quote.{split.value}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md)
            typer.echo(md)
            typer.echo(f"quote written: {out} (no money spent: token counting is free)")
            return

        if not yes and not offline:
            typer.confirm(
                f"Run {len(built)} conditions on {len(questions)} {split.value} questions "
                f"(cap ${budget:.2f}; see `--quote` first)?",
                abort=True,
            )
        memory = next((s for s in built if isinstance(s, ConcentrateSystem)), None)

        def spent() -> float:
            extra = memory.ingest_ledger.spent_usd() if memory is not None else 0.0
            more = j2.ledger.spent_usd() if j2 is not None else 0.0
            return client.ledger.spent_usd() + extra + more

        cats: dict[str, int] = {}
        for q in questions:
            cats[q.category] = cats.get(q.category, 0) + 1
        report = D.run_deletion_sim(
            built,
            questions,
            client,
            judge2=j2,
            tags=tag_map,
            seed=seed,
            budget_usd=budget,
            out_dir=REAL_DIR / "reports",
            split=split.value,
            spent=spent,
            meta={"meetings": len(notes), "category_counts": cats, "track": "deletion-sim"},
        )
    agg = {**report, "metrics": {"judge1": report["metrics"], "deletion": report["deletion"]}}
    # "UNANSWERABLE" is a placeholder gold, not content (it would match the category name).
    private = [q.question for q in questions] + [
        q.gold_answer for q in questions if not q.unanswerable
    ]
    for line in Path(report["paths"]["jsonl"]).read_text().splitlines():
        private.append(_json.loads(line)["answer"]["answer"])
    path = REAL_DIR / "reports" / f"{report['meta']['date']}.deletion.aggregate.json"
    write_committable(agg, path, private)
    if commit_to is not None:
        write_committable(agg, commit_to, private)
    typer.echo(_json.dumps(report["deletion"]["paired"], indent=1))
    typer.echo(f"spent ${spent():.2f} · report: {report['paths']['md']} · aggregate: {path}")


# -- agent-interface quality track --------------------------------------------------------------


@app.command("agent-interface")
def agent_interface_cmd(
    products: Annotated[
        str,
        typer.Option(
            help="Comma-separated: granola, concentrate, or any product recorded by "
            "`real-kit run --product <name>` (scored against --report)."
        ),
    ] = "granola,concentrate",
    report: Annotated[
        Path | None,
        typer.Option(help="Report .jsonl with verdicts, for products other than the built-ins."),
    ] = None,
    tags: Annotated[Path, typer.Option(help="Question tag file.")] = Path(
        "data/real/tags.p2t.json"
    ),
    db: Annotated[Path, typer.Option()] = REAL_DB,
    sample: Annotated[int, typer.Option(help="Questions to re-ask for stability.")] = 12,
    repeats: Annotated[int, typer.Option(help="Extra asks per sampled question.")] = 2,
    seed: Annotated[int, typer.Option()] = 42,
    live: Annotated[
        str | None,
        typer.Option(
            help="Record missing repeats for this one product through its read-only adapter. "
            "Free for Granola; Concentrate's ask_memory(answer=true) costs an LLM call each."
        ),
    ] = None,
    max_calls: Annotated[int, typer.Option(help="Hard cap on live calls.")] = 24,
    out: Annotated[Path, typer.Option(help="Committable aggregate.")] = Path(
        "evals/reports/agent-interface.aggregate.json"
    ),
) -> None:
    """Score meeting products' MCP answers on the agent-interface track: four answer types,
    evidence granularity, payload signals, stability, latency. No LLM calls unless --live names
    a product that makes them; see docs/tracks/AGENT_INTERFACE.md."""
    import json as _json
    import subprocess
    from contextlib import ExitStack
    from datetime import UTC, datetime

    from tenor.blackbox.kit import check_committable
    from tenor.evals import agent_interface as AI
    from tenor.evals.deletion_sim import load_tags
    from tenor.evals.real_slice import load_questions

    names = [n.strip() for n in products.split(",") if n.strip()]
    if live == "concentrate":
        raise typer.BadParameter(
            "Concentrate repeats cost LLM calls; price them first (budget rule)"
        )
    entries = []
    for n in names:
        if n == "granola":
            entries.append(AI.granola_entry())
        elif n == "concentrate":
            entries.append(AI.concentrate_entry())
        elif report is None:
            raise typer.BadParameter(f"{n}: pass --report with its judged black-box report")
        else:
            entries.append(AI.recorded_entry(n, report))
    questions = load_questions()
    tag_map = load_tags(tags)
    calls = 0
    if live is not None:
        from tenor.blackbox.products import open_product

        e = next((x for x in entries if x.product == AI.product_key(live)), None)
        if e is None:
            raise typer.BadParameter(f"--live {live}: not among --products")
        rows = AI.build_rows(e, questions, tag_map, db)
        with ExitStack() as stack:
            prod = open_product(live, stack)
            calls = AI.record_repeats(
                prod, questions, AI.stratified_sample(rows, sample, seed), repeats, max_calls
            )
    results = {}
    for e in entries:
        rows, stab, _ = AI.score_entry(e, questions, tag_map, db, sample, repeats, seed)
        results[e.product] = (e, rows, stab)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    meta = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": sha.stdout.strip(),
        "split": "real dev",
        "seed": seed,
        "sample": sample,
        "repeats": repeats,
        "llm_calls": 0,
        "live_calls_this_run": calls,
    }
    pairs = []
    if {"granola", "concentrate"} <= set(results):
        # Same judge run for both: the real-slice report holds Granola's answers too.
        g = AI.granola_entry(AI.CONCENTRATE_REPORT)
        pairs.append(("granola", "concentrate", g, results["concentrate"][0]))
    agg = AI.aggregate(results, meta, pairs, questions, tag_map, db)
    private = [q.question for q in questions] + [
        q.gold_answer for q in questions if not q.unanswerable
    ]
    private += [a.text for e, _, _ in results.values() for a in e.answers.values()]
    check_committable(agg, private)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(agg, indent=2) + "\n")
    for n, b in agg["products"].items():
        ft = b["four_types"]["overall"]
        typer.echo(f"{n}: n={b['questions']} " + " ".join(f"{k}={v['k']}" for k, v in ft.items()))
    typer.echo(f"live calls: {calls} · aggregate: {out}")


# -- notes faithfulness track: are a system's notes grounded in the transcript? -------------


class FaithSystem(StrEnum):
    granola = "granola"
    northwind_synth = "northwind-synth"


@app.command("notes-faithfulness")
def notes_faithfulness_cmd(
    system: Annotated[FaithSystem, typer.Option(help="Which system's notes to score.")] = (
        FaithSystem.granola
    ),
    meetings: Annotated[int, typer.Option(help="Synthetic only: meetings to sample.")] = 4,
    seed: Annotated[int, typer.Option(help="Seed for the 30% re-check (and synthetic pick).")] = 0,
    quote: Annotated[bool, typer.Option(help="Count tokens and price the run; spend $0.")] = False,
    budget: Annotated[float, typer.Option(help="Refuse to run above this quote (USD).")] = 0.80,
    yes: Annotated[bool, typer.Option(help="Skip the confirmation prompt.")] = False,
    db: Annotated[Path, typer.Option(help="Private store of the real sync (granola).")] = Path(
        "data/real.db"
    ),
    fixtures: Annotated[Path, typer.Option(help="Synthetic corpus.")] = Path(
        "data/fixtures/northwind"
    ),
) -> None:
    """Score one system's meeting notes against their transcripts (notes faithfulness track).

    Private systems (granola) write per-claim output and the owner's spot-check list to
    data/real/ only, and the aggregate is privacy-checked before it is committed. Synthetic
    systems write per-claim output next to the aggregate.
    """
    import json as _json
    import re as _re
    import subprocess
    from datetime import UTC, datetime

    from tenor.blackbox.kit import check_committable
    from tenor.evals import faithfulness as F
    from tenor.evals.runner import load_corpus
    from tenor.llm.client import PRICING, count_tokens

    if system is FaithSystem.granola:
        entry = F.granola_system(db)
    else:
        entry = F.northwind_system(load_corpus(fixtures), meetings, seed)
    ms = F.prepare(entry)
    reqs = F.requests(ms, seed)
    # Output tokens per claim: ~65 observed for both models (reason + label + lines); x1.5 pad.
    out_per_claim = {F.CHECK_MODEL: 100, F.RECHECK_MODEL: 100}
    cache = Path(os.environ.get("CONCENTRATE_CACHE", "data/cache/llm"))
    lines, total = [], 0.0
    tokens: dict[str, dict[str, int]] = {}
    for m, req, _, kind in reqs:
        model = F.CHECK_MODEL if kind == "check" else F.RECHECK_MODEL
        sys_t, user_t = count_tokens([req.system, req.user], model, cache)
        n_claims = len(req.user.splitlines()) - 1
        p_in, p_out = PRICING[model]
        est_out = out_per_claim[model] * n_claims
        usd = (sys_t * p_in * 1.25 + user_t * p_in + est_out * p_out) / 1e6
        hit = LLMClient(model=model).is_cached(req)
        total += 0.0 if hit else usd
        t = tokens.setdefault(model, {"system_cached": 0, "user": 0, "est_output": 0})
        t["system_cached"] += sys_t
        t["user"] += user_t
        t["est_output"] += est_out
        lines.append(
            f"{m.label} {kind:<7} {model:<18} claims={n_claims:>3} system={sys_t:>6} "
            f"user={user_t:>5} est_out={est_out:>5} ${usd:.4f}{' (cached: $0)' if hit else ''}"
        )
    typer.echo("\n".join(lines))
    typer.echo(f"quote: ${total:.4f} new spend (input exact via count_tokens; output estimated)")
    if quote:
        return
    if total > budget:
        typer.echo(f"quote ${total:.2f} exceeds --budget ${budget:.2f}", err=True)
        raise typer.Exit(1)
    if not yes and total > 0:
        typer.confirm(f"Spend about ${total:.2f}?", abort=True)
    clients = {mod: LLMClient(model=mod) for mod in (F.CHECK_MODEL, F.RECHECK_MODEL)}
    missing: dict[str, int] = {}
    for m, req, sample, kind in reqs:
        client = clients[F.CHECK_MODEL if kind == "check" else F.RECHECK_MODEL]
        reply = client.structured(req, F.AuditReply)
        left = F.apply_verdicts(sample, reply, len(m.note.utterances), kind)
        if left:
            missing[f"{m.label}.{kind}"] = len(left)
    records = [r for m in ms for r in m.records]
    agg = F.aggregate(records, {m.label: m.split for m in ms})
    git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    spent = sum(c.ledger.spent_usd() for c in clients.values())
    # What every call cost when it was made (cache hits replay their original cost).
    cost = sum(r.cost_usd for c in clients.values() for r in c.ledger.records)
    report = {
        "meta": {
            "track": "notes-faithfulness",
            "system": entry.name,
            "system_description": entry.description,
            "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git_sha": git.stdout.strip(),
            "check_model": F.CHECK_MODEL,
            "check_effort": "low",
            "recheck_model": F.RECHECK_MODEL,
            "recheck_fraction": F.RECHECK_FRACTION,
            "strict_pass": f"{F.RECHECK_MODEL} on every claim",
            "prompt": F.PROMPT_ID,
            "seed": seed,
            "meetings": len(ms),
            "pool_meetings": len(entry.pool),
            "quote_usd": round(total, 4),
            "cost_usd": round(cost, 4),
            "spent_this_invocation_usd": round(spent, 4),
            "tokens_quoted": tokens,
            "missing_verdicts": missing,
            "by_stage": {k: v for c in clients.values() for k, v in c.ledger.by_stage().items()},
        },
        **agg,
    }
    out_dir = Path("evals/reports/notes-faithfulness")
    out_dir.mkdir(parents=True, exist_ok=True)
    claims_dir = Path("data/real/notes_faithfulness") if entry.private else out_dir
    claims_dir.mkdir(parents=True, exist_ok=True)
    if entry.private:
        private = F.private_texts(ms)
        check_committable(report, private)
        blob = _json.dumps(report).lower()
        names = {
            w.lower()
            for m in ms
            for p in (m.note.owner, *m.note.attendees)
            for w in _re.findall(r"[A-Za-z]{3,}", p.name or "")
        }
        leaked = sorted(n for n in names if _re.search(rf"\b{_re.escape(n)}\b", blob))
        if leaked:
            typer.echo(f"refusing to write: aggregate has {len(leaked)} attendee name(s)", err=True)
            raise typer.Exit(1)
        spot = F.spotcheck_markdown(records, {m.label: m.note for m in ms}, seed)
        (claims_dir / f"{entry.name}.spotcheck.md").write_text(spot)
    with (claims_dir / f"{entry.name}.claims.jsonl").open("w") as fh:
        for r in records:
            fh.write(_json.dumps(r.to_json(), ensure_ascii=False) + "\n")
    agg_path = out_dir / f"{entry.name}.aggregate.json"
    agg_path.write_text(_json.dumps(report, indent=2) + "\n")
    typer.echo(_json.dumps(agg["overall"]["ungrounded"]))
    typer.echo(f"spent ${spent:.4f} · aggregate {agg_path} · per-claim {claims_dir}")


# -- notes grounding checker (docs/method/GROUNDING_CHECKER.md) -----------------------------


def _provenance(**extra: object) -> dict[str, object]:
    """Date, git SHA and whatever else identifies a result."""
    import subprocess
    from datetime import UTC, datetime

    git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return {
        "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": git.stdout.strip(),
        **extra,
    }


@app.command("ground")
def ground_cmd(
    notes: Annotated[
        Path | None, typer.Option(help="Notes markdown. Default: the meeting's summary_markdown.")
    ] = None,
    meeting: Annotated[Path | None, typer.Option(help="A MeetingNote as JSON.")] = None,
    db: Annotated[Path | None, typer.Option(help="A NoteStore database (with --note-id).")] = None,
    note_id: Annotated[str | None, typer.Option(help="Note to read from --db.")] = None,
    ami: Annotated[
        str | None, typer.Option(help="An AMI meeting id (manual transcript), e.g. IS1008a.")
    ] = None,
    out: Annotated[Path, typer.Option(help="Where to write the JSON result.")] = Path(
        "grounding.json"
    ),
    model: Annotated[str, typer.Option(help="Checker model.")] = "claude-haiku-5-5",
    escalate: Annotated[
        bool, typer.Option(help="Re-check non-supported claims on the full transcript.")
    ] = True,
    k: Annotated[int, typer.Option(help="Candidate lines per claim.")] = 8,
    window: Annotated[int, typer.Option(help="Neighbours added around each candidate.")] = 1,
    quote: Annotated[bool, typer.Option(help="Estimate the cost; spend $0.")] = False,
    budget: Annotated[float, typer.Option(help="Refuse to run above this estimate (USD).")] = 2.0,
    yes: Annotated[bool, typer.Option(help="Skip the confirmation prompt.")] = False,
) -> None:
    """Check one meeting's notes against its transcript: a verdict and evidence per claim.

    Give the meeting as --meeting (MeetingNote JSON), --db with --note-id, or --ami. The notes
    are --notes (markdown), else the meeting's own summary_markdown.
    """
    import json as _json

    from tenor.ami.corpus import load_meeting, meeting_note
    from tenor.core.models import MeetingNote
    from tenor.evals import grounding as G
    from tenor.evals.faithfulness import split_claims

    if sum(x is not None for x in (meeting, db, ami)) != 1:
        raise typer.BadParameter("give exactly one of --meeting, --db (with --note-id), --ami")
    if meeting is not None:
        note = MeetingNote.model_validate_json(meeting.read_text())
    elif db is not None:
        found = NoteStore(db).get_note(note_id or "")
        if found is None:
            raise typer.BadParameter(f"note {note_id!r} not in {db}")
        note = found
    else:
        note = meeting_note(load_meeting(ami or ""))
    markdown = notes.read_text() if notes else (note.summary_markdown or "")
    claims = split_claims(markdown).claims
    if not claims:
        raise typer.BadParameter("no claims in the notes")
    q = G.quote(note, claims, k=k, window=window)
    worst, best = q.usd(model, 1.0 if escalate else 0.0), q.usd(model, 0.0)
    typer.echo(
        f"{len(claims)} claims; excerpt {q.excerpt_lines}/{q.transcript_lines} lines; "
        f"estimate ${best:.4f} (no escalation) to ${worst:.4f} (every claim escalates)"
    )
    if quote:
        return
    if worst > budget:
        typer.echo(f"estimate ${worst:.2f} exceeds --budget ${budget:.2f}", err=True)
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"Spend up to about ${worst:.2f}?", abort=True)
    client = LLMClient(model=model)
    result = G.ground_claims(note, claims, client, escalate=escalate, k=k, window=window)
    meta = _provenance(
        tool="grounding-checker",
        model=model,
        effort=G.EFFORT,
        prompt=G.PROMPT_ID,
        cost_usd=round(sum(r.cost_usd for r in client.ledger.records), 5),
        spent_usd=round(client.ledger.spent_usd(), 5),
    )
    out.write_text(_json.dumps({"meta": meta, **result.model_dump()}, indent=2) + "\n")
    typer.echo(f"{result.counts} · {result.calls} call(s) · {out}")


@app.command("ground-ami")
def ground_ami_cmd(
    seed: Annotated[int, typer.Option(help="Seed for the perturbations.")] = 0,
    quote: Annotated[bool, typer.Option(help="Write the $0 parts and the estimate only.")] = False,
    budget: Annotated[float, typer.Option(help="Refuse to run above this estimate (USD).")] = 2.0,
    yes: Annotated[bool, typer.Option(help="Skip the confirmation prompt.")] = False,
    model: Annotated[str, typer.Option(help="Checker model.")] = "claude-haiku-5-5",
    zip_path: Annotated[Path, typer.Option(help="AMI manual annotations zip.")] = Path(
        "data/ami/annotations/ami_public_manual_1.6.2.zip"
    ),
    out_dir: Annotated[Path, typer.Option(help="Report directory.")] = Path(
        "evals/reports/grounding"
    ),
) -> None:
    """Validate the grounding checker on AMI's human summaries and seeded perturbations of them.

    Always writes the $0 parts (perturbation counts, retrieval recall against AMI's summary
    links, the pre-check baseline, the cost estimate). With a confirmed spend under --budget,
    also runs the checker and scores it.
    """
    import json as _json

    from tenor.evals import grounding as G
    from tenor.evals import grounding_ami as V

    corpus = V.load(zip_path)
    items = V.items(corpus, seed)
    est = V.quote(corpus, items, model)
    zero = {
        "meta": _provenance(
            track="grounding-checker-ami", seed=seed, k=G.K, window=G.WINDOW, prompt=G.PROMPT_ID
        ),
        "meetings": list(V.MEETINGS),
        "dev": list(V.DEV),
        "items": V.kinds_count(items),
        "retrieval_recall": V.retrieval_recall(corpus, items, G.K, G.WINDOW),
        "precheck_baseline": V.precheck_baseline(corpus, items),
        "estimate": est,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ami-zero-cost.json").write_text(_json.dumps(zero, indent=2) + "\n")
    typer.echo(
        f"{len(items)} items; estimate {model}: ${est['usd_no_escalation']:.4f} (no escalation) "
        f"to ${est['usd_worst']:.4f} (every claim escalates), up to {est['calls_max']} calls"
    )
    if quote:
        return
    if est["usd_worst"] > budget:
        typer.echo(f"estimate ${est['usd_worst']:.2f} exceeds --budget ${budget:.2f}", err=True)
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"Spend up to about ${est['usd_worst']:.2f}?", abort=True)
    client = LLMClient(model=model)
    scored: list[V.Scored] = []
    calls = escalated = 0
    for (mid, _kind), group in sorted(V.groups(items).items()):
        res = G.ground_claims(corpus.notes[mid], V.claims_for(group), client)
        calls, escalated = calls + res.calls, escalated + res.escalated
        scored += [V.Scored(it, r) for it, r in zip(group, res.claims, strict=True)]
    report = {
        "meta": _provenance(
            track="grounding-checker-ami",
            model=model,
            effort=G.EFFORT,
            prompt=G.PROMPT_ID,
            seed=seed,
            k=G.K,
            window=G.WINDOW,
            calls=calls,
            escalated_claims=escalated,
            estimate_usd_worst=est["usd_worst"],
            cost_usd=round(sum(r.cost_usd for r in client.ledger.records), 5),
            spent_usd=round(client.ledger.spent_usd(), 5),
            source="AMI Meeting Corpus manual annotations 1.6.2 (CC BY 4.0)",
        ),
        **V.score(scored, seed),
    }
    stem = f"ami-validation.{model}"
    (out_dir / f"{stem}.json").write_text(_json.dumps(report, indent=2) + "\n")
    with (out_dir / f"{stem}.items.jsonl").open("w") as fh:
        for row in V.item_rows(scored):
            fh.write(_json.dumps(row, ensure_ascii=False) + "\n")
    test = report["test"]
    typer.echo(_json.dumps(test["positives"]["labels"]["supported"]))
    for kind, v in test["negatives"].items():
        typer.echo(f"{kind}: recall {v['recall']['k']}/{v['recall']['n']}")
    typer.echo(f"spent ${client.ledger.spent_usd():.4f} · {out_dir / stem}.json")


# -- cross-product pairing (docs/method/HARNESS.md) ----------------------------------------

PAIR_OVERRIDES = Path("data/real/pairs.override.json")
PAIRS_OUT = Path("data/real/pairs.json")


@app.command("pair")
def pair_cmd(
    granola_db: Annotated[Path, typer.Option(help="Granola sync (product A).")] = Path(
        "data/real.db"
    ),
    wispr_db: Annotated[Path, typer.Option(help="Wispr Flow sync (product B).")] = Path(
        "data/real_wispr.db"
    ),
    overrides: Annotated[Path, typer.Option(help="Manual pairs / never-pairs.")] = PAIR_OVERRIDES,
    out: Annotated[Path, typer.Option(help="Where to write the pairing (private).")] = PAIRS_OUT,
    window_min: Annotated[float, typer.Option(help="Start-time window, ± minutes.")] = 10.0,
) -> None:
    """Pair meetings recorded by both Granola and Wispr Flow. Prints IDs and scores only."""
    from datetime import timedelta

    from tenor.blackbox.pairing import Overrides, pair_meetings

    for db in (granola_db, wispr_db):
        if not db.exists():
            raise typer.BadParameter(f"{db} not found: sync it first (see PRODUCTS.md)")
    stores = [NoteStore(granola_db), NoteStore(wispr_db)]
    try:
        a, b = (list(s.iter_notes()) for s in stores)
    finally:
        for s in stores:
            s.close()
    result = pair_meetings(
        a, b, window=timedelta(minutes=window_min), overrides=Overrides.load(overrides)
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.model_dump_json(indent=1) + "\n")
    for p in result.pairs:
        typer.echo(f"{p.a}  {p.b}  score={p.score:.2f}  {p.method}")
    typer.echo(
        f"paired={len(result.pairs)} unpaired_granola={len(result.unpaired_a)} "
        f"unpaired_wispr={len(result.unpaired_b)} · {out}"
    )


# -- AMI: public meetings played into the products (docs/method/AMI_REPLAY.md) ---------------

ami_app = typer.Typer(no_args_is_help=True, help="AMI corpus: ground truth and transcript scoring.")
app.add_typer(ami_app, name="ami")
AMI_RESULTS = Path("data/ami/results")


@ami_app.command("status")
def ami_status(
    db: Annotated[
        list[Path] | None, typer.Option(help="Synced AMI-account DB(s) to check for recordings.")
    ] = None,
) -> None:
    """Show each AMI meeting's ground truth, whether its audio is downloaded, and which synced
    DBs already hold a recording (default: data/ami_granola.db and data/ami_wispr.db, if present;
    recording pins in data/ami/results/recording_ids.txt apply, as for `ami wer`)."""
    from tenor.ami.corpus import load_meeting
    from tenor.ami.products import (
        AMI_EXTENDED,
        AMI_SET,
        audio_path,
        match_recordings,
        read_overrides,
        replay_state,
    )
    from tenor.core.store import read_notes

    paths = db or [
        p for p in (Path("data/ami_granola.db"), Path("data/ami_wispr.db")) if p.is_file()
    ]
    pins = read_overrides(AMI_RESULTS / "recording_ids.txt")
    found: dict[str, set[str]] = {}
    for path in paths:
        product = path.stem.removeprefix("ami_")
        found[product] = set(match_recordings(read_notes(path), pins.get(product)))
    for mid in AMI_EXTENDED:
        m = load_meeting(mid)
        sections = " ".join(f"{k}={len(v)}" for k, v in m.summary.items()) or "no summary"
        state = replay_state(audio_path(mid).is_file(), (p for p, s in found.items() if mid in s))
        typer.echo(
            f"{mid:<7}  {'original' if mid in AMI_SET else 'extension':<9}  {m.type:<11} "
            f"{m.duration_s // 60:>2} min  speakers={len(m.speakers)}  "
            f"words={len(m.spoken()):>5}  {sections}  {state}"
        )


@ami_app.command("wer")
def ami_wer(
    db: Annotated[Path, typer.Option(help="Synced DB of the product's dedicated AMI account.")],
    product: Annotated[str, typer.Option(help="Label for the results, e.g. granola or wispr.")],
    drop_mic: Annotated[
        bool,
        typer.Option(
            "--drop-mic/--keep-mic",
            help="Drop microphone turns. Use --keep-mic for products with no capture channel "
            "(Wispr), where 'me' is only a speaker-name guess.",
        ),
    ] = True,
) -> None:
    """Score a product's transcripts of the AMI meetings: WER and content-word recall."""
    import json
    import subprocess
    from datetime import UTC, datetime

    from tenor.ami.products import (
        AMI_SET,
        match_recordings,
        read_overrides,
        score_note,
        wer_report,
    )
    from tenor.ami.wer import NORMALISER_VERSION

    if not db.exists():
        raise typer.BadParameter(f"{db} not found: sync the AMI account first")
    pins = read_overrides(AMI_RESULTS / "recording_ids.txt").get(product)
    store = NoteStore(db)
    try:
        notes = match_recordings(store.iter_notes(), pins)
    finally:
        store.close()
    rows = [
        score_note(mid, notes[mid], product, drop_mic=drop_mic) for mid in AMI_SET if mid in notes
    ]
    for r in rows:
        s = r.score
        typer.echo(
            f"{r.meeting_id}  WER={s.wer:.1%}  content_recall={s.content_recall:.1%}  "
            f"words ref={s.ref_words} hyp={s.hyp_words}  speakers {r.product_speakers}"
            f"/{r.reference_speakers}"
            + (f"  ⚠ {r.mic_utterances} mic turns dropped" if r.mic_utterances else "")
            + (f"  {r.owner_labelled} turns named as you" if r.owner_labelled else "")
        )
    missing = [mid for mid in AMI_SET if mid not in notes]
    if missing:
        typer.echo(f"not recorded yet: {' '.join(missing)}")
    if not rows:
        return
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    meta = {
        "git_sha": sha.stdout.strip(),
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "product": product,
        "normaliser_version": NORMALISER_VERSION,
        "drop_mic": drop_mic,
        "llm": None,
    }
    AMI_RESULTS.mkdir(parents=True, exist_ok=True)
    out = AMI_RESULTS / f"wer_{product}.json"
    payload = {"meta": meta, "meetings": [r.model_dump() for r in rows]}
    out.write_text(json.dumps(payload, indent=1) + "\n")
    typer.echo(f"wrote {out}")
    runs = {
        p.stem.removeprefix("wer_"): json.loads(p.read_text())
        for p in AMI_RESULTS.glob("wer_*.json")
    }
    AMI_WER_OUT.write_text(json.dumps(wer_report(runs), indent=1) + "\n")
    typer.echo(f"wrote {AMI_WER_OUT} (numbers only, {', '.join(sorted(runs))})")


AMI_WER_OUT = Path("evals/reports/ami/wer.json")  # committed: numbers only
AMI_COVERAGE_OUT = Path("evals/reports/ami/coverage.json")  # committed: numbers only


@ami_app.command("coverage")
def ami_coverage(
    granola_db: Annotated[
        Path, typer.Option(help="Synced Granola AMI account (read-only).")
    ] = Path("data/ami_granola.db"),
    wispr_db: Annotated[Path, typer.Option(help="Synced Wispr AMI account (read-only).")] = Path(
        "data/ami_wispr.db"
    ),
    out: Annotated[Path, typer.Option(help="Committed result (numbers only).")] = AMI_COVERAGE_OUT,
    stress: Annotated[
        Path | None,
        typer.Option(
            help="Sidecar JSON from `ami stress-audio`: score that stress recording on its "
            "shifted timeline instead (writes evals/reports/ami/stress_<cond>_<id>.json).",
        ),
    ] = None,
) -> None:
    """Per-15 s capture coverage of the AMI meetings, per product, with a wrong-meeting control.

    No LLM calls. Writes numbers and AMI meeting IDs only: no transcript text, note IDs or titles.
    """
    import json
    import subprocess
    from datetime import UTC, datetime

    from tenor.ami import coverage as C
    from tenor.ami.corpus import load_meeting
    from tenor.ami.products import AMI_SET, match_recordings, read_overrides, transcript_text
    from tenor.ami.wer import NORMALISER_VERSION
    from tenor.core.store import read_notes

    pins = read_overrides(AMI_RESULTS / "recording_ids.txt")
    if stress is not None:
        _stress_coverage(stress, {"granola": granola_db, "wispr": wispr_db}, pins,
                         None if out == AMI_COVERAGE_OUT else out)  # fmt: skip
        return
    dbs = {"granola": granola_db, "wispr": wispr_db}
    recs = {p: match_recordings(read_notes(db), pins.get(p)) for p, db in dbs.items()}
    meets = {mid: load_meeting(mid) for mid in AMI_SET}
    rows = []
    for mid, m in meets.items():
        texts = {
            p: transcript_text(r[mid], drop_mic=(p == "granola")) if mid in r else None
            for p, r in recs.items()
        }
        row = C.score_meeting(m, texts, meets[C.control_for(mid, AMI_SET)])
        rows.append(row)
        typer.echo(
            f"{mid}  control={row.control.coverage:.0%}  "
            + "  ".join(
                f"{p}={c.coverage:.0%} gaps={len(c.gaps)}" if c else f"{p}=not recorded"
                for p, c in row.products.items()
            )
        )
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    payload = {
        "meta": {
            "git_sha": sha.stdout.strip(),
            "date": datetime.now(UTC).isoformat(timespec="seconds"),
            "window_s": C.WINDOW_S,
            "threshold": C.THRESHOLD,
            "min_gap_s": C.MIN_GAP_S,
            "min_bigrams": C.MIN_BIGRAMS,
            "normaliser_version": NORMALISER_VERSION,
            "method": C.METHOD,
            "corpus": "AMI Meeting Corpus manual annotations 1.6.2 (CC BY 4.0)",
            "llm": None,
        },
        "summary": {
            p: {
                "coverage": C.pooled(rows, p, C.THRESHOLD),
                "meetings": sum(r.products.get(p) is not None for r in rows),
            }
            for p in dbs
        },
        "meetings": [r.model_dump() for r in rows],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1) + "\n")
    typer.echo(f"wrote {out}")


def _git_sha() -> str:
    import subprocess

    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return sha.stdout.strip()


def _stress_coverage(
    sidecar: Path, dbs: dict[str, Path], pins: dict[str, dict[str, str]], out: Path | None
) -> None:
    """Score each product's stress recording against the sidecar's shifted timeline."""
    import json
    from datetime import UTC, datetime

    from tenor.ami import coverage as C
    from tenor.ami import stress as S
    from tenor.ami.corpus import load_meeting
    from tenor.ami.products import match_stress, transcript_text
    from tenor.ami.wer import NORMALISER_VERSION
    from tenor.core.store import read_notes

    side = S.Sidecar.model_validate_json(sidecar.read_text())
    words = load_meeting(side.meeting_id).spoken()
    results: dict[str, Any] = {}
    for p, db in dbs.items():
        pin = pins.get(f"{p}_{side.condition.lower()}", {}).get(side.meeting_id)
        note = match_stress(read_notes(db), side.meeting_id, side.condition, pin)
        if note is None:
            typer.echo(f"{p}: no note titled 'AMI {side.meeting_id} {side.condition}' (or pin)")
            results[p] = None
            continue
        r = S.score_stress(words, transcript_text(note, drop_mic=(p == "granola")), side)
        results[p] = r.model_dump()
        resume = f"{r.time_to_resume_s:.0f} s" if r.time_to_resume_s is not None else "never"
        typer.echo(
            f"{p}: before {r.coverage_before:.0%} (n={r.windows_before})  after "
            f"{r.coverage_after:.0%} (n={r.windows_after})  resumed={r.resumed} ({resume})  "
            f"gaps>=30s={[(round(a), round(n)) for a, n in r.gaps]}"
        )
    payload = {
        "meta": {
            "git_sha": _git_sha(),
            "date": datetime.now(UTC).isoformat(timespec="seconds"),
            "condition": side.condition,
            "meeting_id": side.meeting_id,
            "splice_at_s": side.splice_at_s,
            "silence_s": side.silence_s,
            "output_sha256": side.output_sha256,
            "window_s": C.WINDOW_S,
            "threshold": C.THRESHOLD,
            "min_gap_s": C.MIN_GAP_S,
            "normaliser_version": NORMALISER_VERSION,
            "timeline": "stressed (AMI time + silence_s after the splice)",
            "llm": None,
        },
        "products": results,
    }
    dest = out or Path(f"evals/reports/ami/stress_{side.condition}_{side.meeting_id}.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=1) + "\n")
    typer.echo(f"wrote {dest}")


@ami_app.command("stress-audio")
def ami_stress_audio(
    condition: Annotated[
        str,
        typer.Option(help="A1: silence past a 15-min no-audio timeout; A1c: 12-min control."),
    ] = "A1",
    meeting: Annotated[
        str | None,
        typer.Option(help="AMI meeting ID. Default: shortest with >= 10 min of speech after."),
    ] = None,
    silence_min: Annotated[
        float | None,
        typer.Option(help="Minutes of digital silence. Default: 17 for A1, 12 for A1c."),
    ] = None,
    at_min: Annotated[
        float | None,
        typer.Option(help="Cut near this minute (AMI time). Default: middle of the speech."),
    ] = None,
    audio_dir: Annotated[Path, typer.Option(help="AMI audio root.")] = Path("data/ami"),
    out_dir: Annotated[Path, typer.Option(help="Output (gitignored).")] = Path("data/ami/stress"),
) -> None:
    """Build a stress WAV plus a sidecar JSON of its exact splice offsets ($0, deterministic).

    The cut lands at a turn boundary taken from AMI's word timings (docs/tracks/CAPTURE.md, A1).
    """
    from tenor.ami import stress as S
    from tenor.ami.corpus import load_meeting
    from tenor.ami.products import AMI_SET

    if condition not in S.CONDITIONS:
        raise typer.BadParameter(f"--condition must be one of {', '.join(S.CONDITIONS)}")
    mid = meeting or S.default_meeting({m: load_meeting(m) for m in AMI_SET})
    src = audio_dir / mid / f"{mid}.Mix-Headset.wav"
    if not src.is_file():
        raise typer.BadParameter(f"{src} not found")
    side = S.build_a1(
        load_meeting(mid),
        src,
        out_dir,
        silence_s=(silence_min or S.CONDITIONS[condition]) * 60,
        at_s=None if at_min is None else at_min * 60,
        condition=condition,
    )
    m, s = divmod(side.splice_at_s, 60)
    typer.echo(
        f"{mid}: {side.silence_s / 60:g} min of silence at {int(m)}:{s:05.2f} "
        f"(sample {side.splice_sample}); {side.speech_after_s / 60:.1f} min of speech after; "
        f"output {side.output_duration_s / 60:.1f} min"
    )
    typer.echo(f"wrote {side.output_wav}\nwrote {out_dir / f'{mid}.{condition}.json'}")


AMI_DETECTOR_OUT = Path("evals/reports/ami/detector.json")  # committed: numbers only


@ami_app.command("detector")
def ami_detector(
    seed: Annotated[int, typer.Option(help="Fault-injection seed.")] = 0,
    trials: Annotated[int, typer.Option(help="Injected faults per meeting.")] = 40,
    granola_db: Annotated[
        Path, typer.Option(help="Synced Granola AMI account (read-only).")
    ] = Path("data/ami_granola.db"),
    audio_dir: Annotated[Path, typer.Option(help="AMI audio root.")] = Path("data/ami"),
    out: Annotated[Path, typer.Option(help="Committed result (numbers only).")] = AMI_DETECTOR_OUT,
) -> None:
    """Capture-health detector: speech heard but transcript not growing => alert ($0, no LLM).

    Tunes on DEV meetings only, reports TEST; energy VAD and an oracle VAD (AMI word timings).
    Granola only: Wispr's synced transcripts carry no real timing (docs/tracks/CAPTURE_HEALTH.md).
    """
    import json
    from datetime import UTC, datetime

    from tenor.ami import detector as D
    from tenor.ami.corpus import load_meeting
    from tenor.ami.products import AMI_SET, match_recordings, read_overrides
    from tenor.ami.questions import DEV_MEETINGS
    from tenor.core.store import read_notes

    pins = read_overrides(AMI_RESULTS / "recording_ids.txt").get("granola")
    notes = match_recordings(read_notes(granola_db), pins)
    sigs = {}
    for mid in AMI_SET:
        if mid not in notes:
            typer.echo(f"{mid}: no Granola recording, skipped")
            continue
        wav = audio_dir / mid / f"{mid}.Mix-Headset.wav"
        sigs[mid] = D.load_signals(load_meeting(mid), wav, notes[mid])
    dev = [s for m, s in sigs.items() if m in DEV_MEETINGS]
    test = [s for m, s in sigs.items() if m not in DEV_MEETINGS]
    faults = {m: D.make_faults(s, seed, trials) for m, s in sigs.items()}
    variants: dict[str, Any] = {}
    chosen: dict[str, Any] = {}
    for v in D.VARIANTS:
        params, rows = D.tune(dev, faults, v)
        chosen[v] = params
        variants[v] = {
            "params": params.model_dump(),
            "dev": D.score_split(dev, faults, v, params),
            "test": D.score_split(test, faults, v, params),
            "test_by_t_not_used_for_selection": D.sensitivity(test, faults, v, params),
            "dev_grid": rows,
        }
        for split in ("dev", "test"):
            r = variants[v][split]
            typer.echo(
                f"{v:<12} {split:<4} T={params.t_s}s min_speech={params.min_speech} "
                f"margin={params.margin_db}  recall={r['recall']} "
                f"({r['detected']}/{r['faults_confirmed']})  delay median={r['delay_s_median']}s "
                f"p90={r['delay_s_p90']}s  false alarms={r['false_alarms']} "
                f"({r['false_alarms_per_hour']}/h over {r['hours']:.2f} h)"
            )
    margin = chosen["energy/start"].margin_db
    ex_fault = next(
        (f for s in test for f in faults[s.meeting_id] if f.confirmed and 60 <= f.length <= 180),
        None,
    )
    payload = {
        "meta": {
            "git_sha": _git_sha(),
            "date": datetime.now(UTC).isoformat(timespec="seconds"),
            "seed": seed,
            "trials_per_meeting": trials,
            "fault_length_s": [D.FAULT_MIN_S, D.FAULT_MAX_S],
            "product": "granola",
            "product_timing": "transcript block start times ([m:ss], 1 s), one constant shift "
            "per meeting onto AMI time; growth 'start' = block starts only (recorded), 'span' = "
            "also each second of a block's estimated speaking time (ASSUMED 2.6 words/s)",
            "variants": "VAD/growth: energy or oracle VAD; start or span transcript growth",
            "split": {"dev": [s.meeting_id for s in dev], "test": [s.meeting_id for s in test]},
            "split_rule": "tenor.ami.questions.DEV_MEETINGS",
            "fixed": {
                "frame_s": D.FRAME_S,
                "floor_window_s": D.FLOOR_WINDOW_S,
                "floor_percentile": D.FLOOR_PCTL,
                "min_dbfs": D.MIN_DBFS,
                "coverage_window_s": 15.0,
                "coverage_min_gap_s": 30.0,
            },
            "grid": {
                "t_s": D.GRID_T,
                "min_speech": D.GRID_MIN_SPEECH,
                "margin_db": D.GRID_MARGIN_DB,
            },
            "selection": f"DEV: false alarms <= {D.FA_BUDGET_PER_H}/h, then max recall, then "
            "min median delay, then larger T / min_speech / margin",
            "method": D.METHOD,
            "corpus": "AMI Meeting Corpus manual annotations 1.6.2 and Mix-Headset audio "
            "(CC BY 4.0)",
            "llm": None,
        },
        "alignment": {
            s.meeting_id: {
                "shift_s": s.shift_s,
                "blocks_matched": s.matched,
                "blocks": len(s.blocks),
            }
            for s in sigs.values()
        },
        "vad_agreement": {
            "dev": D.vad_agreement(dev, margin or 0.0),
            "test": D.vad_agreement(test, margin or 0.0),
        },
        "variants": variants,
        "example": (D.example(sigs[ex_fault.meeting_id], ex_fault, chosen) if ex_fault else None),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1) + "\n")
    typer.echo(f"wrote {out}")


# -- anatomy of a wrong fact (docs/method/WRONG_FACTS.md) ----------------------------------

facts_app = typer.Typer(
    no_args_is_help=True,
    help="Anatomy of a wrong fact: transcription, note-writing or answering? (AMI)",
)
app.add_typer(facts_app, name="facts")
FACTS_OUT = AMI_RESULTS / "facts"  # gitignored: may hold short product quotes
FACTS_DBS = {"granola": Path("data/ami_granola.db"), "wispr": Path("data/ami_wispr.db")}
FACTS_OVERRIDES = AMI_RESULTS / "recording_ids.txt"

GranolaDbOpt = Annotated[Path, typer.Option(help="Synced Granola AMI account (read-only).")]
WisprDbOpt = Annotated[Path, typer.Option(help="Synced Wispr AMI account (read-only).")]
OverridesOpt = Annotated[
    Path, typer.Option(help="Manual pins: lines like 'IB4003 wispr=<uuid> granola=<uuid>'.")
]
ProductOpt = Annotated[str, typer.Option(help="granola, wispr or all.")]
ZipOpt = Annotated[Path, typer.Option(help="AMI manual annotations zip.")]
OutDirOpt = Annotated[Path, typer.Option(help="Results folder (local, gitignored).")]
DEFAULT_AMI_ZIP = Path("data/ami/annotations/ami_public_manual_1.6.2.zip")


def _facts_recordings(
    granola_db: Path, wispr_db: Path, overrides: Path, product: str
) -> dict[str, dict[str, Any]]:
    """Product -> AMI meeting ID -> MeetingNote, read-only, for the recorded AMI set."""
    from tenor.ami.products import AMI_SET, match_recordings, read_overrides
    from tenor.core.store import read_notes

    if product not in ("all", *FACTS_DBS):
        raise typer.BadParameter(f"--product must be all, {' or '.join(FACTS_DBS)}")
    pins = read_overrides(overrides)
    out: dict[str, dict[str, Any]] = {}
    for name, db in (("granola", granola_db), ("wispr", wispr_db)):
        if product not in ("all", name):
            continue
        if not db.is_file():
            typer.echo(f"{name}: {db} not found, skipped (sync it first)", err=True)
            continue
        found = match_recordings(read_notes(db), pins.get(name))
        out[name] = {mid: found[mid] for mid in AMI_SET if mid in found}
        missing = [mid for mid in AMI_SET if mid not in found]
        typer.echo(
            f"{name}: {len(out[name])} recording(s)"
            + (f"; not found: {' '.join(missing)}" if missing else "")
        )
    if not any(out.values()):
        raise typer.BadParameter("no AMI recordings found in the given databases")
    return out


def _cap(max_usd: float | None, pilot: bool, worst: float, yes: bool) -> float:
    """The hard spend cap for a paid run: --max-usd, or PILOT_USD for a pilot."""
    from tenor.evals.wrong_facts import PILOT_USD

    cap = max_usd if max_usd is not None else (PILOT_USD if pilot else None)
    if cap is None:
        typer.echo("paid runs need --max-usd (the approved amount); pilot first", err=True)
        raise typer.Exit(1)
    if worst > cap:
        typer.echo(f"worst-case estimate ${worst:.4f} exceeds the cap ${cap:.2f}", err=True)
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"Spend up to ${cap:.2f} (worst-case estimate ${worst:.4f})?", abort=True)
    return cap


def _projection(client: LLMClient, run_worst: float, full_worst: float) -> dict[str, Any]:
    """Measured spend vs the quote, and the full run re-priced from it with a 1.5x margin."""
    from tenor.evals.wrong_facts import SAFETY

    measured = sum(r.cost_usd for r in client.ledger.records)
    ratio = measured / run_worst if run_worst else None
    return {
        "measured_usd": round(measured, 5),
        "spent_usd": round(client.ledger.spent_usd(), 5),
        "quoted_worst_usd": round(run_worst, 5),
        "measured_over_quoted": round(ratio, 4) if ratio is not None else None,
        "full_run_projected_usd": round(full_worst * ratio * SAFETY, 4) if ratio else None,
    }


@facts_app.command("stage1")
def facts_stage1(
    granola_db: GranolaDbOpt = FACTS_DBS["granola"],
    wispr_db: WisprDbOpt = FACTS_DBS["wispr"],
    overrides: OverridesOpt = FACTS_OVERRIDES,
    product: ProductOpt = "all",
    zip_path: ZipOpt = DEFAULT_AMI_ZIP,
    out_dir: OutDirOpt = FACTS_OUT,
) -> None:
    """Stage 1 ($0): fact-token recall, substitutions and name insertions per transcript.

    Deterministic, no LLM and no randomness (so no --seed).
    """
    import json

    from tenor.ami import facts as F
    from tenor.ami.corpus import load_meeting
    from tenor.ami.wer import NORMALISER_VERSION
    from tenor.evals.wrong_facts import DROP_MIC, own_transcript

    recs = _facts_recordings(granola_db, wispr_db, overrides, product)
    results = []
    for name, by_mid in recs.items():
        drop = DROP_MIC[name]
        for mid, note in by_mid.items():
            own = own_transcript(note, drop_mic=drop)
            r = F.score_transcript(
                load_meeting(mid, zip_path),
                [u.text for u in own.utterances],
                name,
                drop_mic=drop,
                owner_name=note.owner.name,
            )
            results.append(r)
            cells = "  ".join(
                f"{k}={v.recall:.0%}({v.human}) sub={v.substitutions}"
                for k, v in r.kinds.items()
                if v.recall is not None
            )
            typer.echo(
                f"{name:<8}{mid:<8} {cells}  name_ins={r.name_insertions}"
                f" owner={r.owner_name_insertions}"
            )
    meta = _provenance(
        track="wrong-facts.stage1",
        facts_version=F.FACTS_VERSION,
        normaliser_version=NORMALISER_VERSION,
        drop_mic=DROP_MIC,
        llm=None,
        source="AMI Meeting Corpus manual annotations 1.6.2 (CC BY 4.0)",
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "stage1.json"
    payload = {"meta": meta, "results": [r.model_dump() for r in results]}
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    typer.echo(f"wrote {path}")


@facts_app.command("stage2")
def facts_stage2(
    granola_db: GranolaDbOpt = FACTS_DBS["granola"],
    wispr_db: WisprDbOpt = FACTS_DBS["wispr"],
    overrides: OverridesOpt = FACTS_OVERRIDES,
    product: ProductOpt = "all",
    quote: Annotated[bool, typer.Option(help="Price it and stop ($0).")] = False,
    pilot: Annotated[
        bool, typer.Option(help="Sandbox: the DEV meeting only, cap $0.05 by default.")
    ] = False,
    max_usd: Annotated[
        float | None, typer.Option(help="Hard spend cap (the approved amount).")
    ] = None,
    yes: Annotated[bool, typer.Option(help="Skip the confirmation prompt.")] = False,
    model: Annotated[str, typer.Option(help="Grounding-checker model.")] = "claude-haiku-5-5",
    zip_path: ZipOpt = DEFAULT_AMI_ZIP,
    out_dir: OutDirOpt = FACTS_OUT,
) -> None:
    """Stage 2: check each product note claim against its own and AMI's human transcript.

    Deterministic given the LLM cache (no randomness, so no --seed). The checker input is
    rendered as ``wrong_facts.RENDER`` says; ``--quote`` prices a run with that rendering.
    """
    import json

    from tenor.ami.corpus import load_meeting
    from tenor.ami.facts import human_note, spoken_names
    from tenor.ami.products import AMI_SET
    from tenor.evals import grounding as G
    from tenor.evals import wrong_facts as W
    from tenor.llm.client import BudgetExceeded

    recs = _facts_recordings(granola_db, wispr_db, overrides, product)
    mids = sorted({m for by_mid in recs.values() for m in by_mid})
    # Names come from self-introductions anywhere in the AMI set (IS1008a names IS1008b-d).
    meetings = {m: load_meeting(m, zip_path) for m in sorted({*AMI_SET, *mids})}
    names = spoken_names(meetings.values())
    human = {m: human_note(meetings[m], names) for m in mids}
    jobs = W.stage2_jobs(recs, human)
    full = W.quote_stage2(jobs, model)
    run = W.pilot(jobs) if pilot else jobs
    est = W.quote_stage2(run, model) if pilot else full
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stage2.quote.json").write_text(json.dumps(full, indent=1) + "\n")
    typer.echo(
        f"full run ({W.RENDER}): {full['jobs']} notes, {full['claims']} claims, {model} "
        f"${full['usd_no_escalation']:.4f} (no escalation) to ${full['usd_worst']:.4f} (worst)"
    )
    if pilot:
        typer.echo(f"pilot: {len(run)} notes, worst ${est['usd_worst']:.4f}")
    if quote:
        return
    cap = _cap(max_usd, pilot, est["usd_worst"], yes)
    client = LLMClient(model=model, max_usd=cap)
    rows: list[W.ClaimRow] = []
    stopped = None
    for job in run:
        try:
            rows += W.run_stage2(job, client)
        except BudgetExceeded as e:
            stopped = str(e)
            break
    stem = "stage2.pilot" if pilot else "stage2"
    meta = _provenance(
        track="wrong-facts.stage2",
        model=model,
        effort=G.EFFORT,
        prompt=G.PROMPT_ID,
        render=W.RENDER,
        k=W.K,
        span=W.SPAN,
        window=W.WINDOW,
        spoken_names=len(names),
        drop_mic=W.DROP_MIC,
        max_usd=cap,
        stopped=stopped,
        **_projection(client, est["usd_worst"], full["usd_worst"]),
    )
    agg = {"meta": meta, "products": W.aggregate_stage2(rows)}
    (out_dir / f"{stem}.json").write_text(json.dumps(agg, indent=1) + "\n")
    W.write_jsonl(rows, out_dir / f"{stem}.claims.jsonl")
    for name, a in agg["products"].items():
        shares = "  ".join(f"{c}={v['k']}" for c, v in a["classes"].items() if v["k"])
        typer.echo(f"{name}: {a['claims']} claims  {shares}")
    typer.echo(
        f"spent ${meta['spent_usd']:.4f}; full run projected ${meta['full_run_projected_usd']}"
        f" (x{W.SAFETY}) · {out_dir / stem}.json" + (f" · STOPPED: {stopped}" if stopped else "")
    )


@facts_app.command("reclassify")
def facts_reclassify(
    claims: Annotated[Path, typer.Option(help="A stage-2 claims JSONL.")] = FACTS_OUT
    / "stage2.claims.jsonl",
    out_dir: OutDirOpt = FACTS_OUT,
) -> None:
    """Re-apply the current stage-2 class rules to stored verdicts ($0, no LLM calls).

    Writes ``<stem>.reclassified.json`` (aggregate only) next to the results.
    """
    import json
    from collections import Counter

    from tenor.evals import wrong_facts as W

    old = W.load_claim_rows(claims)
    new = W.reclassify(old)
    moved = Counter(f"{a.cls}->{b.cls}" for a, b in zip(old, new, strict=True) if a.cls != b.cls)
    meta = _provenance(track="wrong-facts.stage2.reclassify", source=str(claims), llm=None)
    products = W.aggregate_stage2(new)
    agg = {"meta": meta, "moved": dict(moved), "products": products}
    stem = claims.name.removesuffix(".claims.jsonl")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.reclassified.json"
    path.write_text(json.dumps(agg, indent=1) + "\n")
    for name, a in products.items():
        shares = "  ".join(f"{c}={v['k']}" for c, v in a["classes"].items() if v["k"])
        typer.echo(f"{name}: {a['claims']} claims  {shares}")
    typer.echo(f"moved: {dict(moved)} · {path}")


@facts_app.command("stage3")
def facts_stage3(
    answers: Annotated[
        Path | None, typer.Option(help="JSONL of {question_id, product, answer}.")
    ] = None,
    granola_db: GranolaDbOpt = FACTS_DBS["granola"],
    wispr_db: WisprDbOpt = FACTS_DBS["wispr"],
    overrides: OverridesOpt = FACTS_OVERRIDES,
    product: ProductOpt = "all",
    quote: Annotated[
        bool, typer.Option(help="Price it and stop ($0); without --answers, assumes lengths.")
    ] = False,
    pilot: Annotated[
        bool, typer.Option(help="Sandbox: 3 seeded DEV answers, cap $0.05 by default.")
    ] = False,
    seed: Annotated[int, typer.Option(help="Seed for the pilot sample.")] = 0,
    llm_notes: Annotated[
        bool,
        typer.Option(
            "--llm-notes/--lexical-notes",
            help="Check notes with the grounding checker, or only the $0 lexical recall.",
        ),
    ] = True,
    max_usd: Annotated[
        float | None, typer.Option(help="Hard spend cap (the approved amount).")
    ] = None,
    yes: Annotated[bool, typer.Option(help="Skip the confirmation prompt.")] = False,
    judge_model: Annotated[str, typer.Option(help="Judge model.")] = "claude-haiku-5-5",
    model: Annotated[str, typer.Option(help="Notes-check model.")] = "claude-haiku-5-5",
    questions_file: Annotated[Path, typer.Option("--questions")] = Path(
        "data/ami_questions/questions.jsonl"
    ),
    zip_path: ZipOpt = DEFAULT_AMI_ZIP,
    out_dir: OutDirOpt = FACTS_OUT,
) -> None:
    """Stage 3: judge product answers; pin each wrong one on transcription, notes or answering."""
    import json

    from tenor.ami.corpus import load_meeting
    from tenor.ami.questions import RECORDED, load_questions
    from tenor.evals import grounding as G
    from tenor.evals import wrong_facts as W
    from tenor.evals.scoring import JUDGE_PROMPT
    from tenor.llm.client import BudgetExceeded

    qs = W.recorded(load_questions(questions_file))
    recs = _facts_recordings(granola_db, wispr_db, overrides, product)
    ev = W.Evidence({m: load_meeting(m, zip_path) for m in RECORDED}, recs)
    if answers is not None:
        rows_in = W.load_answers(answers)
        W.check_answers(rows_in, qs)
    elif quote:
        rows_in = W.placeholder_answers(qs, sorted(recs))
        typer.echo(f"no --answers: quoting {len(rows_in)} placeholder answers (ASSUMED lengths)")
    else:
        raise typer.BadParameter("--answers is required unless --quote")
    run = W.pilot_answers(rows_in, qs, seed) if pilot else rows_in
    full = W.quote_stage3(rows_in, qs, ev, judge_model=judge_model, check_model=model)
    est = W.quote_stage3(run, qs, ev, judge_model=judge_model, check_model=model)
    worst = est["usd_worst"] if llm_notes else est["judge_usd"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stage3.quote.json").write_text(json.dumps(full, indent=1) + "\n")
    typer.echo(
        f"full run: {full['answers']} answers; judge ${full['judge_usd']:.4f} + notes check "
        f"${full['notes_check_usd_worst']:.4f} (worst: every answer wrong) = "
        f"${full['usd_worst']:.4f}"
    )
    if pilot:
        typer.echo(f"pilot: {len(run)} answers, worst ${worst:.4f}")
    if quote:
        return
    cap = _cap(max_usd, pilot, worst, yes)
    judge = LLMClient(model=judge_model, max_usd=cap)
    checker = LLMClient(model=model, ledger=judge.ledger, max_usd=cap) if llm_notes else None
    rows: list[W.AnswerRow] = []
    stopped = None
    for a in run:
        try:
            rows += W.run_stage3([a], qs, ev, judge, checker)
        except BudgetExceeded as e:
            stopped = str(e)
            break
    stem = "stage3.pilot" if pilot else "stage3"
    meta = _provenance(
        track="wrong-facts.stage3",
        judge_model=judge_model,
        check_model=model if llm_notes else None,
        prompts={"judge": JUDGE_PROMPT, "notes_check": G.PROMPT_ID if llm_notes else None},
        seed=seed,
        pilot=pilot,
        max_usd=cap,
        stopped=stopped,
        **_projection(judge, worst, full["usd_worst"] if llm_notes else full["judge_usd"]),
    )
    agg = {"meta": meta, "products": W.aggregate_stage3(rows)}
    (out_dir / f"{stem}.json").write_text(json.dumps(agg, indent=1) + "\n")
    W.write_jsonl(rows, out_dir / f"{stem}.rows.jsonl")
    for name, a in agg["products"].items():
        s = a["all"]
        stages = "  ".join(f"{k}={v['k']}" for k, v in s["stage"].items() if v["k"])
        typer.echo(f"{name}: {s['answers']} answers, {s['wrong']} wrong  {stages}")
    typer.echo(
        f"spent ${meta['spent_usd']:.4f}; full run projected ${meta['full_run_projected_usd']}"
        f" (x{W.SAFETY}) · {out_dir / stem}.json" + (f" · STOPPED: {stopped}" if stopped else "")
    )


# -- deletion simulation on AMI (docs/tracks/RETENTION.md, "AMI replication") -------------


@ami_app.command("deletion-sim")
def ami_deletion_sim(
    conditions: Annotated[
        str,
        typer.Option(
            help="granola_chat (A), notes_only (B), notes_only_wispr (B'), notes_facts (C), "
            "notes_facts_fallback (D); comma-separated."
        ),
    ] = "granola_chat,notes_only,notes_only_wispr,notes_facts,notes_facts_fallback",
    split: Annotated[str, typer.Option(help="all, dev or test.")] = "all",
    quote_only: Annotated[
        bool,
        typer.Option("--quote", help="Price the full run and the pilot; spends nothing."),
    ] = False,
    pilot: Annotated[
        bool, typer.Option(help="Sandbox: --pilot-n DEV questions x every condition, tiny cap.")
    ] = False,
    pilot_n: Annotated[int, typer.Option(help="--pilot: questions.")] = 5,
    scope: Annotated[
        str,
        typer.Option(
            help="question: each condition reads only the question's own meetings, as Granola "
            "Chat (A) was asked (default). all: every recording's notes (upper bound)."
        ),
    ] = "question",
    max_usd: Annotated[
        float | None,
        typer.Option(help="Hard spend cap (the approved amount); a pilot defaults to $1.00."),
    ] = None,
    yes: Annotated[bool, typer.Option(help="Skip the confirmation prompt.")] = False,
    reader_model: Annotated[
        str | None, typer.Option(help="Answer model; default: the original run's (its meta).")
    ] = None,
    judge_model: Annotated[str, typer.Option(help="Judge (stage 3's).")] = "claude-sonnet-5-5",
    judge2: Annotated[str, typer.Option(help="Second judge model, or 'none'.")] = "none",
    exact: Annotated[
        bool,
        typer.Option(
            "--exact/--heuristic",
            help="Count input tokens with the free count_tokens endpoint, or chars/3.5 offline.",
        ),
    ] = True,
    seed: Annotated[int, typer.Option()] = 42,
    answers: Annotated[
        Path, typer.Option(help="Granola Chat answers (stage 3's cleaned JSONL).")
    ] = Path("data/ami/results/facts/answers_granola.clean.jsonl"),
    granola_db: GranolaDbOpt = FACTS_DBS["granola"],
    wispr_db: WisprDbOpt = FACTS_DBS["wispr"],
    overrides: OverridesOpt = FACTS_OVERRIDES,
    questions_file: Annotated[Path, typer.Option("--questions")] = Path(
        "data/ami_questions/questions.jsonl"
    ),
    out_dir: OutDirOpt = AMI_RESULTS / "deletion",
    commit_to: Annotated[
        Path | None,
        typer.Option(help="Also write the aggregate here (checked: numbers only). Not a pilot."),
    ] = None,
) -> None:
    """Deletion simulation on public AMI recordings: Granola Chat vs notes only (Granola and
    Wispr) vs notes + fact layer vs + transcript fallback. Start with --quote, then --pilot."""
    import functools
    import json as _json

    from tenor.ami.questions import load_questions
    from tenor.blackbox.kit import check_committable
    from tenor.core.store import read_notes
    from tenor.evals import deletion_ami as DA
    from tenor.evals import deletion_sim as D
    from tenor.evals.real_slice import real_directory
    from tenor.evals.wrong_facts import load_answers
    from tenor.llm.client import BudgetExceeded, Ledger, count_tokens, estimate_tokens

    names = [c.strip() for c in conditions.split(",") if c.strip()]
    if unknown := [n for n in names if n not in DA.CONDITIONS]:
        raise typer.BadParameter(f"unknown conditions {unknown}; choose from {DA.CONDITIONS}")
    if scope not in DA.SCOPES:
        raise typer.BadParameter(f"--scope must be one of {DA.SCOPES}")
    if split not in ("all", "dev", "test"):
        raise typer.BadParameter("--split must be all, dev or test")
    if pilot and quote_only:
        raise typer.BadParameter("--quote already prices the pilot; drop --pilot")
    recs = DA.load_recordings({"granola": granola_db, "wispr": wispr_db}, overrides, read_notes)
    notes = {p: DA.reader_notes(r, p) for p, r in recs.items()}
    chat = {a.question_id: a.answer for a in load_answers(answers) if a.product == "granola"}
    picked, skipped = DA.select_questions(
        load_questions(questions_file), {p: list(r) for p, r in recs.items()}, chat, names
    )
    as_of = DA.as_of_day(n for ns in notes.values() for n in ns)
    qs = [DA.adapt(q, as_of) for q in picked if split in ("all", q.split)]
    if not qs:
        raise typer.BadParameter(f"no usable questions (skipped: {skipped})")
    reader = reader_model or DA.original_reader_model()
    pilot_qs = DA.pilot_questions(qs, pilot_n, seed)
    run_qs = pilot_qs if pilot else qs
    typer.echo(
        f"{len(qs)} questions on {len({m for q in qs for m in q.meetings})} AMI meetings "
        f"(recordings: {', '.join(f'{p} {len(r)}' for p, r in recs.items())}); "
        f"skipped {skipped or 'none'}; reader {reader}, judge {judge_model}; "
        f"scope: {DA.SCOPE_META[scope]}"
    )
    cap = max_usd if max_usd is not None else (DA.PILOT_MAX_USD if pilot else None)
    if cap is None and os.environ.get("CONCENTRATE_MAX_USD"):
        cap = float(os.environ["CONCENTRATE_MAX_USD"])
    if cap is None and not quote_only:
        raise typer.BadParameter("paid runs need --max-usd (the approved amount); --pilot first")
    ledger = Ledger()  # one ledger: the cap covers answers, judges and the fact-layer build
    reader_c = LLMClient(model=reader, ledger=ledger, max_usd=cap, offline=quote_only)
    judge_c = LLMClient(model=judge_model, ledger=ledger, max_usd=cap, offline=quote_only)
    j2 = (
        None
        if judge2 == "none"
        else LLMClient(model=judge2, ledger=ledger, max_usd=cap, offline=quote_only)
    )
    if j2 is not None and j2.model == judge_c.model:
        raise typer.BadParameter("the second judge must differ from the primary judge")
    out_dir.mkdir(parents=True, exist_ok=True)
    directory = out_dir / "directory.json"  # people from attendee metadata, as the original
    directory.write_text(_json.dumps(real_directory(notes.get("granola", [])), indent=1))

    embedder: Any = None

    def probe() -> Any:
        return DA.AmiNotesFacts(reader_c, directory_path=directory)

    def systems_for(for_qs: list[Any], build: bool) -> tuple[dict[str, Any], Any, bool]:
        """Systems by condition, the fact-layer corpora (one per meeting set under question
        scope) and whether every one is built. ``build``: ingest now (paid unless cached)."""
        nonlocal embedder
        from tenor.agents.concentrate_system import COMPLETE_MARKER

        out: dict[str, Any] = {}
        corp = DA.corpora(for_qs, notes.get("granola", []), scope)
        p = probe()
        built = all((p.store_dir(c) / COMPLETE_MARKER).exists() for c in corp.values())
        for n in names:
            if n == DA.GRANOLA_CHAT:
                out[n] = DA.RecordedChatSystem(chat)
            elif n in ("notes_only", "notes_only_wispr"):
                product = "wispr" if n.endswith("wispr") else "granola"
                out[n] = DA.notes_only(reader_c, notes[product], n, scope)
            elif built or build:
                if embedder is None:
                    from tenor.agents.retrieval import SentenceTransformerEmbedder

                    embedder = SentenceTransformerEmbedder()
                cls = DA.AmiNotesFacts if n == "notes_facts" else DA.AmiNotesFactsFallback
                builds = {}
                for key, corpus in corp.items():
                    s = cls(reader_c, embedder=embedder, directory_path=directory)
                    s.ingest(corpus)  # free if built; otherwise the paid build, under the cap
                    builds[key] = s
                out[n] = DA.ScopedMemory(builds, scope)
        return out, corp, built

    counter: Any
    if exact:
        counter = functools.partial(count_tokens, model=reader, cache_dir=reader_c.cache_dir)
        how = f"exact (count_tokens, {reader})"
    else:

        def counter(texts: list[str]) -> list[int]:
            return [estimate_tokens(t) for t in texts]

        how = "offline chars/3.5 (not exact)"
    jcount = (
        functools.partial(count_tokens, model=judge_model, cache_dir=judge_c.cache_dir)
        if exact
        else counter
    )
    cal = DA.calibration(D.judge_cache_pairs(judge_c.cache_dir, judge_model), jcount)
    b_granola = DA.notes_only(reader_c, notes.get("granola", []), "notes_only", scope)

    def quote(for_pilot: bool) -> list[Any]:
        label, for_qs = ("pilot", pilot_qs) if for_pilot else (split, qs)
        systems, corp, built = systems_for(for_qs, build=False)
        return DA.quote_lines(
            names, for_qs, systems, b_granola, reader_c, judge_c, j2, counter, cal,
            split=label, built=built, build=DA.quote_build(probe, corp, label),
        )  # fmt: skip

    full_lines = quote(False)
    pilot_lines = quote(True)
    md = DA.quote_markdown(
        [
            (f"Full run: {len(qs)} questions ({split})", full_lines),
            (f"Pilot: {len(pilot_qs)} DEV questions", pilot_lines),
        ],
        cal,
        reader=reader,
        judge=judge_model,
        judge2=judge2,
        how=how,
        scope=scope,
    )
    (out_dir / "deletion_quote.md").write_text(md)
    fe, fh = DA.totals(full_lines)
    pe, ph = DA.totals(pilot_lines)
    typer.echo(md)
    typer.echo(
        f"full run: ${fe:.2f} expected, ${fh:.2f} high · pilot: ${pe:.2f} expected, "
        f"${ph:.2f} high · {out_dir / 'deletion_quote.md'}"
    )
    if quote_only:
        typer.echo("no money spent (token counting is free)")
        return
    assert cap is not None
    high = ph if pilot else fh
    if high > cap:
        typer.echo(f"high estimate ${high:.2f} exceeds the cap ${cap:.2f}", err=True)
        raise typer.Exit(1)
    if not yes:
        typer.confirm(
            f"{'PILOT: ' if pilot else ''}Run {len(names)} conditions on {len(run_qs)} "
            f"questions (hard cap ${cap:.2f}, quoted high ${high:.2f})?",
            abort=True,
        )
    stopped = None
    report: dict[str, Any] | None = None
    memory: DA.ScopedMemory | None = None
    try:
        systems, _corp, _ = systems_for(run_qs, build=True)
        memory = next((s for s in systems.values() if isinstance(s, DA.ScopedMemory)), None)
        report = D.run_deletion_sim(
            [systems[n] for n in names],
            run_qs,
            judge_c,
            judge2=j2,
            tags=DA.question_tags(run_qs),
            seed=seed,
            budget_usd=cap,
            out_dir=out_dir,
            split=split,
            spent=ledger.spent_usd,
            pairs=DA.PAIRS,
            meta={
                "track": "deletion-sim.ami",
                "reader_model": reader,
                "pilot": pilot,
                "scope": DA.SCOPE_META[scope],
            },
        )
    except BudgetExceeded as e:
        stopped = str(e)
    typer.echo(f"spent ${ledger.spent_usd():.4f} (cap ${cap:.2f})")
    if report is None:
        typer.echo(f"STOPPED: {stopped}. Cached calls replay at $0 on a re-run.", err=True)
        raise typer.Exit(1)
    info = DA.RunInfo(
        reader_model=reader,
        judge_model=judge_model,
        judge2_model=j2.model if j2 else None,
        ingest_model=memory.ingest_model if memory is not None else None,
        pilot=pilot,
        skipped=skipped,
        recordings={p: len(r) for p, r in recs.items()},
        build=DA.build_stats(memory.builds.values()) if memory is not None else {},
        scope=scope,
    )
    agg = DA.aggregate(report, run_qs, info)
    rows = Path(report["paths"]["jsonl"])
    check_committable(agg, DA.private_texts(run_qs, rows))
    path = out_dir / f"{report['meta']['date']}.aggregate.json"
    path.write_text(_json.dumps(agg, indent=2, default=str) + "\n")
    if commit_to is not None and not pilot:
        commit_to.parent.mkdir(parents=True, exist_ok=True)
        commit_to.write_text(_json.dumps(agg, indent=2, default=str) + "\n")
    typer.echo(_json.dumps(report["deletion"]["paired"], indent=1)[:3000])
    if pilot and pe:
        ratio = ledger.spent_usd() / pe
        typer.echo(
            f"pilot spent / pilot expected = {ratio:.2f}; full run re-priced: "
            f"${fe * ratio * 1.5:.2f} (x1.5 margin). Ask before any run over $2."
        )
    typer.echo(f"report: {report['paths']['md']} · aggregate: {path}")


# -- E1: list then check (docs/tracks/ANSWERS.md, "E1") -----------------------------------


@dataclass
class _E1Inputs:
    """What E1 and E2 read: Granola / Wispr notes, Chat's recorded answers (cleaned as stage 3
    judged them), the list questions and one :class:`~tenor.evals.list_check.Context` each."""

    notes: dict[str, list[Any]]
    chat_text: dict[str, str]
    n_usable: int
    skipped: Any
    full: list[Any]
    contexts: list[Any]


def _e1_inputs(
    questions_file: Path, answers: Path, granola_db: Path, wispr_db: Path, overrides: Path
) -> _E1Inputs:
    """The deletion run's question filter (meetings recorded by Granola and Wispr, answered by
    Chat), narrowed to list questions, each with Granola's transcript, AMI's reference and gold."""
    from tenor.ami.corpus import load_meeting, meeting_note
    from tenor.ami.questions import load_questions
    from tenor.core.store import read_notes
    from tenor.evals import deletion_ami as DA
    from tenor.evals import list_check as E
    from tenor.evals.wrong_facts import clean_answer, load_answers, roster

    recs = DA.load_recordings({"granola": granola_db, "wispr": wispr_db}, overrides, read_notes)
    notes = {p: DA.reader_notes(r, p) for p, r in recs.items()}
    chat = {a.question_id: a.answer for a in load_answers(answers) if a.product == "granola"}
    chat_text = {qid: clean_answer(a) for qid, a in chat.items()}  # as stage 3 judged it
    picked, skipped = DA.select_questions(
        load_questions(questions_file), {p: list(r) for p, r in recs.items()}, chat, DA.CONDITIONS
    )
    full = E.list_questions(picked)
    by_title = {n.title: n for n in notes.get("granola", [])}
    ami: dict[str, Any] = {}

    def context(q: Any) -> Any:
        (mid,) = q.meetings  # list questions are single-meeting
        if mid not in ami:
            ami[mid] = load_meeting(mid)
        m = ami[mid]
        ref_note = meeting_note(m)
        return E.Context(
            qid=q.id,
            question=q.question,
            category=q.type,
            split=q.split,
            transcript=E.transcript_lines(by_title[f"AMI {mid}"]),
            reference=E.merge_turns(E.transcript_lines(ref_note, lambda u: u.speaker_name or "?")),
            gold=E.gold_for(q, {s.sentence_id: s.text for s in m.sentences}, roster(q.meetings)),
        )

    return _E1Inputs(notes, chat_text, len(picked), skipped, full, [context(q) for q in full])


@ami_app.command("list-check")
def ami_list_check(
    conditions: Annotated[
        str,
        typer.Option(
            help="C (Granola Chat, recorded), N (notes), L (list), LD (list+check, drop), "
            "LM (mark)."
        ),
    ] = "C,N,L,LD,LM",
    quote_only: Annotated[
        bool, typer.Option("--quote", help="Price the full run and the pilot; spends nothing.")
    ] = False,
    pilot: Annotated[bool, typer.Option(help="Sandbox: --pilot-n DEV list questions.")] = False,
    pilot_n: Annotated[int, typer.Option(help="--pilot: questions.")] = 3,
    only: Annotated[
        str | None,
        typer.Option(help="--pilot: run only these pilot question IDs (comma-separated)."),
    ] = None,
    max_usd: Annotated[
        float | None,
        typer.Option(help="Hard spend cap (the approved amount); a pilot defaults to $0.50."),
    ] = None,
    yes: Annotated[bool, typer.Option(help="Skip the confirmation prompt.")] = False,
    reader_model: Annotated[
        str | None, typer.Option(help="Reader; default: the deletion run's (its meta).")
    ] = None,
    judge_model: Annotated[str, typer.Option(help="Item judge.")] = "claude-sonnet-5-5",
    judge_prompt: Annotated[
        str,
        typer.Option(
            help="judge_items.v1 (question first, as E1 ran) or judge_items.v2 (reference "
            "transcript first: one prompt-cached transcript per meeting)."
        ),
    ] = "judge_items.v1",
    exact: Annotated[
        bool,
        typer.Option("--exact/--heuristic", help="count_tokens (free) or chars/3.5 offline."),
    ] = True,
    seed: Annotated[int, typer.Option()] = 42,
    answers: Annotated[Path, typer.Option(help="Granola Chat answers (question filter).")] = Path(
        "data/ami/results/facts/answers_granola.clean.jsonl"
    ),
    granola_db: GranolaDbOpt = FACTS_DBS["granola"],
    wispr_db: WisprDbOpt = FACTS_DBS["wispr"],
    overrides: OverridesOpt = FACTS_OVERRIDES,
    questions_file: Annotated[Path, typer.Option("--questions")] = Path(
        "data/ami_questions/questions.jsonl"
    ),
    out_dir: OutDirOpt = AMI_RESULTS / "e1",
    commit_to: Annotated[
        Path | None,
        typer.Option(help="Also write the numbers-only aggregate here (checked). Not a pilot."),
    ] = None,
    stage: Annotated[
        str,
        typer.Option(
            help="Staged run: 'map' (map calls only), 'merge' (map + merge), or 'all'. Each "
            "stage caches its calls, so the next is priced exactly."
        ),
    ] = "all",
) -> None:
    """E1: list then check on AMI list questions (notes only vs list every item vs list +
    check, drop or mark). Item-level recall / precision. Start with --quote, then --pilot."""
    import dataclasses
    import functools
    from datetime import UTC, datetime

    from tenor.agents.baselines import Answer
    from tenor.blackbox.kit import check_committable
    from tenor.evals import deletion_ami as DA
    from tenor.evals import deletion_sim as D
    from tenor.evals import list_check as E
    from tenor.evals.runner import _git
    from tenor.llm.client import (
        BudgetExceeded,
        Ledger,
        LLMError,
        count_tokens,
        estimate_tokens,
    )

    names = [c.strip() for c in conditions.split(",") if c.strip()]
    if unknown := [n for n in names if n not in E.CONDITIONS]:
        raise typer.BadParameter(f"unknown conditions {unknown}; choose from {E.CONDITIONS}")
    if pilot and quote_only:
        raise typer.BadParameter("--quote already prices the pilot; drop --pilot")
    if judge_prompt not in E.JUDGE_PROMPTS:
        raise typer.BadParameter(f"--judge-prompt: choose from {E.JUDGE_PROMPTS}")
    inp = _e1_inputs(questions_file, answers, granola_db, wispr_db, overrides)
    notes, chat_text, full, full_ctx = inp.notes, inp.chat_text, inp.full, inp.contexts
    pilot_qs = E.pilot_questions(full, pilot_n, seed)
    if only:
        wanted = {x.strip() for x in only.split(",") if x.strip()}
        if missing := wanted - {q.id for q in pilot_qs}:
            raise typer.BadParameter(f"--only: not pilot questions: {sorted(missing)}")
        pilot_qs = [q for q in pilot_qs if q.id in wanted]
    n_dev = sum(1 for q in full if q.split == "dev")
    as_of = DA.as_of_day(n for ns in notes.values() for n in ns)  # as the deletion run
    reader = reader_model or DA.original_reader_model()
    typer.echo(
        f"{len(full)} list questions ({n_dev} DEV) of {inp.n_usable} usable (skipped "
        f"{inp.skipped or 'none'}); pilot "
        f"{len(pilot_qs)}: {', '.join(q.id for q in pilot_qs)}; reader {reader}, judge "
        f"{judge_model}"
    )
    pilot_ctx = [c for c in full_ctx if c.qid in {q.id for q in pilot_qs}]
    cap = max_usd if max_usd is not None else (E.PILOT_MAX_USD if pilot else None)
    if cap is None and not quote_only:
        raise typer.BadParameter("paid runs need --max-usd (the approved amount); --pilot first")
    ledger = Ledger()
    reader_c = LLMClient(model=reader, ledger=ledger, max_usd=cap, offline=quote_only)
    judge_c = LLMClient(model=judge_model, ledger=ledger, max_usd=cap, offline=quote_only)
    notes_sys = DA.notes_only(reader_c, notes.get("granola", []), "notes_only", "question")
    dq = {q.id: DA.adapt(q, as_of) for q in full}

    counter: Any
    if exact:
        counter = functools.partial(count_tokens, model=reader, cache_dir=reader_c.cache_dir)
        jcount: Any = functools.partial(
            count_tokens, model=judge_model, cache_dir=judge_c.cache_dir
        )
        how = f"exact (count_tokens, {reader} / {judge_model})"
    else:

        def counter(texts: list[str]) -> list[int]:
            return [estimate_tokens(t) for t in texts]

        jcount, how = counter, "offline chars/3.5 (not exact)"

    def notes_answer(system: Any, qid: str) -> Any:
        def get() -> str:
            a = system.run(dq[qid]).answer
            return f"(abstained) {a.answer}" if a.abstain else a.answer

        return get

    def fully_cached(c: Any) -> bool:
        """Every call this question makes is already cached (replayed offline, $0)."""
        off = Ledger()
        r = LLMClient(model=reader, ledger=off, offline=True, cache_dir=reader_c.cache_dir)
        j = LLMClient(model=judge_model, ledger=off, offline=True, cache_dir=judge_c.cache_dir)
        n_sys = DA.notes_only(r, notes.get("granola", []), "notes_only", "question")
        try:
            E.run_question(
                c, r, j, notes_answer(n_sys, c.qid), names, chat_answer=chat_text.get(c.qid),
                judge_prompt=judge_prompt,
            )  # fmt: skip
        except LLMError:
            return False
        return True

    def cached_notes_text(qid: str) -> str | None:
        """N's answer if its call is cached (as judged: abstentions prefixed)."""
        done = D.cached_turn(reader_c, notes_sys.turn_request(dq[qid]))
        if done is None:
            return None
        a = Answer.model_validate_json(done.text)
        return f"(abstained) {a.answer}" if a.abstain else a.answer

    def quote(ctxs: list[Any]) -> list[Any]:
        todo = [c for c in ctxs if not fully_cached(c)]
        reqs = [notes_sys.turn_request(dq[c.qid]) for c in todo] if "N" in names else []
        texts = {c.qid: t for c in todo if (t := cached_notes_text(c.qid)) is not None}
        return E.quote(
            todo, reader_c, judge_c, counter, jcount, reqs,
            lambda r: D.cached_turn(reader_c, r) is not None, names, notes_texts=texts,
            chat_texts=chat_text, judge_prompt=judge_prompt,
        )  # fmt: skip

    full_lines, pilot_lines = quote(full_ctx), quote(pilot_ctx)
    n_cached = {
        k: sum(fully_cached(c) for c in v) for k, v in (("full", full_ctx), ("pilot", pilot_ctx))
    }
    md = E.quote_markdown(
        [
            (
                f"Full run: {len(full_ctx)} list questions ({n_cached['full']} fully cached, $0)",
                full_lines,
            ),
            (
                f"Pilot: {len(pilot_ctx)} DEV list questions "
                f"({n_cached['pilot']} fully cached, $0)",
                pilot_lines,
            ),
        ],
        how,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "quote.md").write_text(md)
    fe, fh = E.totals(full_lines)
    pe, ph = E.totals(pilot_lines)
    typer.echo(md)
    typer.echo(f"full: ${fe:.2f} expected, ${fh:.2f} high · pilot: ${pe:.2f}, ${ph:.2f} high")
    (fec, fhc), (pec, phc) = E.totals_cached(full_lines), E.totals_cached(pilot_lines)
    typer.echo(
        f"with prompt caching: full ${fec:.2f} expected, ${fhc:.2f} high · pilot ${pec:.2f}, "
        f"${phc:.2f} high (the cap is checked against the uncached high)"
    )
    if quote_only:
        typer.echo("no money spent (token counting is free)")
        return
    assert cap is not None
    run_ctx = pilot_ctx if pilot else full_ctx
    high = ph if pilot else fh
    if stage not in E.STAGES:
        raise typer.BadParameter(f"--stage must be one of {E.STAGES}")
    if stage != "all":
        part = [
            x for x in (pilot_lines if pilot else full_lines)
            if x.stage.startswith(E.STAGE_LINES[stage])
        ]  # fmt: skip
        high = E.totals(part)[1]
        typer.echo(f"stage {stage}: ${E.totals(part)[0]:.2f} expected, ${high:.2f} high")
    if high > cap:
        typer.echo(f"high estimate ${high:.2f} exceeds the cap ${cap:.2f}", err=True)
        raise typer.Exit(1)
    if not yes:
        typer.confirm(
            f"{'PILOT: ' if pilot else ''}Run {names} on {len(run_ctx)} questions "
            f"(hard cap ${cap:.2f}, quoted high ${high:.2f})?",
            abort=True,
        )

    if stage != "all":
        try:
            for c in run_ctx:
                E.run_stage(reader_c, c, stage)
        except BudgetExceeded as e:
            typer.echo(f"STOPPED: {e}", err=True)
        by = {k: round(v["usd"], 4) for k, v in ledger.by_stage().items()}
        typer.echo(f"stage {stage}: spent ${ledger.spent_usd():.4f} (cap ${cap:.2f}); {by}")
        return

    n_line = next((x for x in full_lines if x.stage.startswith("N answer")), None)
    free = ["eval.notes_only"] if n_line is not None and n_line.calls == 0 else []
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    records: list[dict[str, Any]] = []
    stopped = None
    try:
        for c in run_ctx:
            records.append(
                E.run_question(
                    c,
                    reader_c,
                    judge_c,
                    notes_answer(notes_sys, c.qid),
                    names,
                    chat_answer=chat_text.get(c.qid),
                    judge_prompt=judge_prompt,
                )
            )
            typer.echo(f"{c.qid}: {_json_scores(records[-1])} (spent ${ledger.spent_usd():.4f})")
    except BudgetExceeded as e:
        stopped = str(e)
    meta = E.RunMeta(
        date=stamp, git=_git(), seed=seed, reader_model=reader, judge_model=judge_model,
        pilot=pilot, max_usd=cap, questions=[c.qid for c in run_ctx],
        prompts={**E.PROMPTS, "judge": [judge_prompt]},
    )  # fmt: skip
    result = {
        "meta": {**dataclasses.asdict(meta), "stopped_early": stopped,
                 "spent_usd": ledger.spent_usd(), "ledger_by_stage": ledger.by_stage()},
        "summary": E.summarize(records, seed),
        "reprice": E.reprice(ledger.to_json(), run_ctx, full_ctx, free_stages=free)
        if pilot
        else None,
        "records": records,
        "ledger": ledger.to_json(),
    }  # fmt: skip
    path = out_dir / f"{stamp}{'.pilot' if pilot else ''}.json"
    E.write_json(path, result)
    typer.echo(f"spent ${ledger.spent_usd():.4f} (cap ${cap:.2f}) · {path}")
    agg = E.aggregate(result)
    check_committable(agg, E.private_texts(records, [c.question for c in run_ctx]))
    E.write_json(out_dir / f"{stamp}{'.pilot' if pilot else ''}.aggregate.json", agg)
    if commit_to is not None and not pilot and not stopped:
        E.write_json(commit_to, agg)
        typer.echo(f"aggregate (numbers only): {commit_to}")
    if stopped:
        typer.echo(f"STOPPED: {stopped}. Cached calls replay at $0 on a re-run.", err=True)
        raise typer.Exit(1)
    reprice = result["reprice"]
    if pilot and isinstance(reprice, dict):
        r = reprice
        typer.echo(
            f"full run re-priced from the pilot ledger: ${r['full_usd']:.2f}, "
            f"${r['full_usd_with_margin']:.2f} with x{E.MARGIN} margin. Ask before any run over $2."
        )


def _json_scores(rec: dict[str, Any]) -> str:
    """One line per question: each condition's recall and lenient precision."""

    def f(x: float | None) -> str:
        return "-" if x is None else f"{x:.2f}"

    return ", ".join(
        f"{c} R={f(s['recall'])} P={f(s['precision_any'])}" for c, s in rec["scores"].items()
    )


# -- E2: was it agreed? (docs/tracks/ANSWERS.md, "E2") ------------------------------------


def _latest_e1(folder: Path) -> Path:
    """The newest full (non-pilot) E1 result in ``folder``."""
    runs = sorted(
        p for p in folder.glob("*.json") if not p.name.endswith((".pilot.json", ".aggregate.json"))
    )
    if not runs:
        raise typer.BadParameter(f"no E1 result in {folder}; run `tenor ami list-check` first")
    return runs[-1]


@ami_app.command("agreed-check")
def ami_agreed_check(
    e1: Annotated[
        Path | None,
        typer.Option(help="E1 result (default: the newest full run in data/ami/results/e1)."),
    ] = None,
    quote_only: Annotated[
        bool, typer.Option("--quote", help="Price the full run and the pilot; spends nothing.")
    ] = False,
    pilot: Annotated[bool, typer.Option(help="Sandbox: the DEV questions only.")] = False,
    max_usd: Annotated[
        float | None,
        typer.Option(help="Hard spend cap (the approved amount); a pilot defaults to $0.50."),
    ] = None,
    yes: Annotated[bool, typer.Option(help="Skip the confirmation prompt.")] = False,
    model: Annotated[
        str | None, typer.Option(help="Classifier; default: E1's reader (the model that listed).")
    ] = None,
    batch: Annotated[
        bool, typer.Option(help="Message Batches (half price, minutes of latency).")
    ] = False,
    seed: Annotated[int, typer.Option()] = 42,
    answers: Annotated[Path, typer.Option(help="Granola Chat answers (question filter).")] = Path(
        "data/ami/results/facts/answers_granola.clean.jsonl"
    ),
    granola_db: GranolaDbOpt = FACTS_DBS["granola"],
    wispr_db: WisprDbOpt = FACTS_DBS["wispr"],
    overrides: OverridesOpt = FACTS_OVERRIDES,
    questions_file: Annotated[Path, typer.Option("--questions")] = Path(
        "data/ami_questions/questions.jsonl"
    ),
    out_dir: OutDirOpt = AMI_RESULTS / "e2",
    commit_to: Annotated[
        Path | None,
        typer.Option(help="Also write the numbers-only aggregate here (checked). Not a pilot."),
    ] = None,
) -> None:
    """E2: classify each E1 listed item as agreed / assigned / raised (or only proposed,
    mentioned, off topic); keep matching extras under Chat's answer, capped at K. Reuses E1's
    items and judge verdicts. Start with --quote, then --pilot."""
    import functools
    import json as _json
    from datetime import UTC, datetime

    from tenor.blackbox.kit import check_committable
    from tenor.evals import agreed_check as A
    from tenor.evals import list_check as E
    from tenor.evals.runner import _git
    from tenor.llm.client import BudgetExceeded, Ledger, count_tokens, prefetch_structured

    if pilot and quote_only:
        raise typer.BadParameter("--quote already prices the pilot; drop --pilot")
    src = e1 or _latest_e1(AMI_RESULTS / "e1")
    e1_run = _json.loads(src.read_text())
    reader = model or str(e1_run["meta"]["reader_model"])  # default: the model that listed
    inp = _e1_inputs(questions_file, answers, granola_db, wispr_db, overrides)
    ctx_by = {c.qid: c for c in inp.contexts}
    if missing := [r["qid"] for r in e1_run["records"] if r["qid"] not in ctx_by]:
        raise typer.BadParameter(f"E1 questions without a context: {missing}")
    full = [(r, ctx_by[r["qid"]]) for r in e1_run["records"]]
    pilot_pairs = [(r, c) for r, c in full if r["split"] == "dev"]
    cap = max_usd if max_usd is not None else (A.PILOT_MAX_USD if pilot else None)
    if cap is None and not quote_only:
        raise typer.BadParameter("paid runs need --max-usd (the approved amount); --pilot first")
    ledger = Ledger()
    reader_c = LLMClient(model=reader, ledger=ledger, max_usd=cap, offline=quote_only)
    counter = functools.partial(count_tokens, model=reader, cache_dir=reader_c.cache_dir)
    fq = A.quote(full, reader_c, counter, batch=batch)
    pq = A.quote(pilot_pairs, reader_c, counter, batch=batch)
    for label, n, q in (("full", len(full), fq), ("pilot (DEV)", len(pilot_pairs), pq)):
        typer.echo(
            f"{label}: {n} questions, {q.calls} classifier calls ({q.cached} cached), "
            f"{q.input_tokens:,} input tokens (exact) -> ${q.usd_expected:.3f} expected, "
            f"${q.usd_high:.3f} high ({reader}{', batch' if batch else ''}; "
            f"output ASSUMED {A.ASSUMED_OUT}); with prompt caching ${q.usd_expected_cached:.3f} "
            f"expected, ${q.usd_high_cached:.3f} high ({q.cache_read_tokens:,} tokens read "
            f"from the cache{', best-effort in a batch' if batch else ''})"
        )
    if quote_only:
        typer.echo(f"source: {src}. No money spent (token counting is free).")
        return
    assert cap is not None
    run = pilot_pairs if pilot else full
    high = pq.usd_high if pilot else fq.usd_high
    if high > cap:
        typer.echo(f"high estimate ${high:.2f} exceeds the cap ${cap:.2f}", err=True)
        raise typer.Exit(1)
    if not yes:
        typer.confirm(
            f"{'PILOT: ' if pilot else ''}Classify the items of {len(run)} questions "
            f"(hard cap ${cap:.2f}, quoted high ${high:.2f})?",
            abort=True,
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    records: list[dict[str, Any]] = []
    stopped = None
    try:
        if batch:  # results land in the cache; the loop below replays them
            reqs = [
                A.agreed_request(c.question, it, c.transcript)
                for r, c in run
                for it in A.e1_items(r)
            ]
            br = prefetch_structured(reader_c, reqs)
            typer.echo(f"batch: {br.succeeded} of {br.submitted} cached, fallback {br.fallback}")
        for rec, ctx in run:
            records.append(A.run_question(rec, ctx, reader_c))
            v = records[-1]["variants"]
            typer.echo(
                f"{rec['qid']}: "
                + ", ".join(f"{k} R={v[k]['recall']:.2f} x{v[k]['n_extras']}" for k in A.VARIANTS)
                + f" (spent ${ledger.spent_usd():.4f})"
            )
    except BudgetExceeded as e:
        stopped = str(e)
    after = A.quote(full, LLMClient(model=reader, offline=True), counter, batch=batch)
    result = {
        "meta": {
            "date": stamp,
            "git": _git(),
            "seed": seed,
            "classifier_model": reader,
            "batch": batch,
            "prompt": A.PROMPT,
            "window_lines": A.WINDOW,
            "caps": dict(A.CAPS),
            "dedupe": "gold mapping (E1 judge): oracle, as E1's extras were counted",
            "pilot": pilot,
            "max_usd": cap,
            "e1_source": str(src),
            "e1_git": e1_run["meta"].get("git"),
            "questions": [r["qid"] for r in records],
            "stopped_early": stopped,
            "spent_usd": ledger.spent_usd(),
            "ledger_by_stage": ledger.by_stage(),
        },
        "summary": A.summarize(records, seed),
        "reprice": A.reprice(ledger.to_json(), sum(after.per_question.values())) if pilot else None,
        "records": records,
        "ledger": ledger.to_json(),
    }
    path = out_dir / f"{stamp}{'.pilot' if pilot else ''}.json"
    E.write_json(path, result)
    typer.echo(f"spent ${ledger.spent_usd():.4f} (cap ${cap:.2f}) · {path}")
    agg = A.aggregate(result)
    check_committable(agg, A.private_texts(records, [c.question for _r, c in run]))
    E.write_json(out_dir / f"{stamp}{'.pilot' if pilot else ''}.aggregate.json", agg)
    if commit_to is not None and not pilot and not stopped:
        E.write_json(commit_to, agg)
        typer.echo(f"aggregate (numbers only): {commit_to}")
    if stopped:
        typer.echo(f"STOPPED: {stopped}. Cached calls replay at $0 on a re-run.", err=True)
        raise typer.Exit(1)
    if pilot and isinstance(result["reprice"], dict):
        r = result["reprice"]
        typer.echo(
            f"full run re-priced from the pilot ledger: {r['full_uncached_calls']} calls x "
            f"${r['usd_per_call']:.4f} = ${r['full_usd']:.2f}; ${r['full_usd_with_margin']:.2f} "
            f"with x{A.MARGIN} margin. Ask before any run over $2."
        )


# -- E3: notes completeness (docs/tracks/NOTES.md, "E3") ------------------------------------


def _e3_units(granola_db: Path, wispr_db: Path, overrides: Path) -> tuple[list[Any], list[str]]:
    """One unit per product per AMI_SET meeting both products recorded and AMI summarised."""
    from tenor.ami.corpus import load_meeting
    from tenor.ami.products import AMI_SET
    from tenor.core.store import read_notes
    from tenor.evals import deletion_ami as DA
    from tenor.evals import notes_completeness as N
    from tenor.evals.wrong_facts import roster

    recs = DA.load_recordings({"granola": granola_db, "wispr": wispr_db}, overrides, read_notes)
    notes = {
        p: {(n.title or "").removeprefix("AMI "): n for n in DA.reader_notes(r, p)}
        for p, r in recs.items()
    }
    units: list[Any] = []
    skipped: list[str] = []
    for mid in AMI_SET:
        m = load_meeting(mid)
        if not N.gold_items(m.sentences):
            skipped.append(f"{mid}: no AMI decisions/actions/problems")
            continue
        if missing := [p for p in N.PRODUCTS if mid not in notes.get(p, {})]:
            skipped.append(f"{mid}: not recorded by {', '.join(missing)}")
            continue
        r = roster([mid])
        units += [N.make_unit(m, notes[p][mid], p, r) for p in N.PRODUCTS]
    return units, skipped


@ami_app.command("notes-completeness")
def ami_notes_completeness(
    quote_only: Annotated[
        bool, typer.Option("--quote", help="Price the full run and the pilot; spends nothing.")
    ] = False,
    pilot: Annotated[
        bool, typer.Option(help="Sandbox: the DEV meeting (IS1008a), both products.")
    ] = False,
    max_usd: Annotated[
        float | None,
        typer.Option(help="Hard spend cap (the approved amount); a pilot defaults to $0.50."),
    ] = None,
    yes: Annotated[bool, typer.Option(help="Skip the confirmation prompt.")] = False,
    judge_model: Annotated[str, typer.Option(help="Judge (E1's).")] = "claude-sonnet-5-5",
    seed: Annotated[int, typer.Option()] = 42,
    granola_db: GranolaDbOpt = FACTS_DBS["granola"],
    wispr_db: WisprDbOpt = FACTS_DBS["wispr"],
    overrides: OverridesOpt = FACTS_OVERRIDES,
    out_dir: OutDirOpt = AMI_RESULTS / "e3",
) -> None:
    """E3: how much of AMI's human decisions / actions / problems each product's notes contain,
    and whether the notes' decision / action / problem bullets are true. One judge call per
    product per meeting. Start with --quote, then --pilot. Reads synced DBs only (no sync)."""
    import functools
    from datetime import UTC, datetime

    from tenor.blackbox.kit import check_committable
    from tenor.evals import list_check as E
    from tenor.evals import notes_completeness as N
    from tenor.evals.runner import _git
    from tenor.llm.client import BudgetExceeded, Ledger, count_tokens

    if pilot and quote_only:
        raise typer.BadParameter("--quote already prices the pilot; drop --pilot")
    units, skipped = _e3_units(granola_db, wispr_db, overrides)
    pilot_units = [u for u in units if u.meeting_id == N.PILOT_MEETING]
    typer.echo(
        f"{len(units)} units ({len(units) // 2} meetings x {len(N.PRODUCTS)} products); "
        f"skipped: {skipped or 'none'}; pilot: {[u.key for u in pilot_units]}; judge {judge_model}"
    )
    cap = max_usd if max_usd is not None else (N.PILOT_MAX_USD if pilot else None)
    if cap is None and not quote_only:
        raise typer.BadParameter("paid runs need --max-usd (the approved amount); --pilot first")
    ledger = Ledger()
    judge_c = LLMClient(model=judge_model, ledger=ledger, max_usd=cap, offline=quote_only)
    counter = functools.partial(count_tokens, model=judge_model, cache_dir=judge_c.cache_dir)
    fq, pq = N.quote(units, judge_c, counter), N.quote(pilot_units, judge_c, counter)
    for label, q in (("full", fq), ("pilot (DEV)", pq)):
        typer.echo(
            f"{label}: {q.calls} judge calls ({q.cached} cached), {q.input_tokens:,} input "
            f"tokens (exact) -> ${q.usd_expected:.3f} expected, ${q.usd_high:.3f} high "
            f"({judge_model}; output ASSUMED {N.ASSUMED_OUT_BASE} + {N.ASSUMED_OUT_PER_ITEM}"
            f"/item)"
        )
    if quote_only:
        typer.echo("no money spent (token counting is free)")
        return
    assert cap is not None
    run = pilot_units if pilot else units
    high = (pq if pilot else fq).usd_high
    if high > cap:
        typer.echo(f"high estimate ${high:.2f} exceeds the cap ${cap:.2f}", err=True)
        raise typer.Exit(1)
    if not yes:
        typer.confirm(
            f"{'PILOT: ' if pilot else ''}Judge {len(run)} units (hard cap ${cap:.2f}, quoted "
            f"high ${high:.2f})?",
            abort=True,
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    records: list[dict[str, Any]] = []
    stopped = None
    try:
        for u in run:
            records.append(N.run_unit(u, judge_c))
            a = records[-1]["scores"]["all"]
            typer.echo(
                f"{u.key}: recall {a['recall']}, precision {a['precision']}, "
                f"repairs {len(records[-1]['repairs'])} (spent ${ledger.spent_usd():.4f})"
            )
    except BudgetExceeded as e:
        stopped = str(e)

    def counted(us: list[Any]) -> tuple[int, int]:
        """Exact input tokens (+ overhead) and items over ``us``, cached or not."""
        reqs = [N.judge_request(u) for u in us]
        tin = sum(counter([f"{r.system}\n\n{r.user}" for r in reqs])) + N.OVERHEAD * len(us)
        return tin, sum(N.items_of(u) for u in us)

    reprice = None
    if pilot:
        (pin, pit), (fin, fit) = counted(pilot_units), counted(units)
        rest = [u for u in units if not judge_c.is_cached(N.judge_request(u))]
        rin, rit = counted(rest) if rest else (0, 0)
        reprice = {
            "all_units": N.reprice(ledger.to_json(), pin, pit, fin, fit),
            "uncached_units": {
                "n": len(rest),
                **N.reprice(ledger.to_json(), pin, pit, rin, rit),
            },
        }
    result = {
        "meta": {
            "date": stamp,
            "git": _git(),
            "seed": seed,
            "judge_model": judge_model,
            "prompt": N.PROMPT,
            "pilot": pilot,
            "max_usd": cap,
            "units": [u.key for u in run],
            "skipped": skipped,
            "max_product_chars": N.MAX_PRODUCT_CHARS,
            "paragraph_chars": N.PARAGRAPH_CHARS,
            "stopped_early": stopped,
            "spent_usd": ledger.spent_usd(),
            "ledger_by_stage": ledger.by_stage(),
        },
        "summary": N.summarize(records, seed),
        "reprice": reprice,
        "records": records,
        "ledger": ledger.to_json(),
    }
    suffix = ".pilot" if pilot else ""
    path = out_dir / f"{stamp}{suffix}.json"
    E.write_json(path, result)
    typer.echo(f"spent ${ledger.spent_usd():.4f} (cap ${cap:.2f}) · {path}")
    agg = N.aggregate(result)
    check_committable(agg, N.private_texts(records))
    E.write_json(out_dir / f"{stamp}{suffix}.aggregate.json", agg)
    if stopped:
        typer.echo(f"STOPPED: {stopped}. Cached calls replay at $0 on a re-run.", err=True)
        raise typer.Exit(1)
    if reprice is not None:
        for k, r in reprice.items():
            typer.echo(
                f"full run re-priced from the pilot ledger ({k}): ${r['full_usd']:.2f}, "
                f"${r['full_usd_with_margin']:.2f} with x{N.MARGIN} margin. Ask before any "
                "run over $2."
            )


# -- MCP contract check (packages/tenor/sources/contract.py) -----------------------------

CONTRACT_BASELINE = Path("evals/contracts/granola_mcp.json")


@ami_app.command("questions")
def ami_questions(
    seed: Annotated[int, typer.Option()] = 42,
    target: Annotated[int, typer.Option(help="Add whole series until this many questions.")] = 300,
    out: Annotated[Path, typer.Option(help="Output folder (committed: AMI is CC BY 4.0).")] = Path(
        "data/ami_questions"
    ),
) -> None:
    """Build the evidence-compression question set from AMI's human annotations (no LLM), and
    the closed-book control prompts. Same seed, byte-identical files."""
    from tenor.ami.corpus import DEFAULT_ZIP, load_meeting
    from tenor.ami.questions import (
        build_questions,
        closed_book_prompts,
        counts,
        select_meetings,
        write_jsonl,
    )
    from tenor.llm.client import load_prompt

    meetings, sel = select_meetings(DEFAULT_ZIP, load_meeting, seed, target)
    qs = build_questions(meetings, seed)
    write_jsonl(qs, out / "questions.jsonl")
    write_jsonl(closed_book_prompts(qs, load_prompt("closed_book.v1")), out / "closed_book.jsonl")
    (out / "meetings.json").write_text(sel.model_dump_json(indent=1) + "\n")
    typer.echo(
        f"{len(qs)} questions from {len(meetings)} meetings -> {out}/questions.jsonl "
        f"(+ closed_book.jsonl, meetings.json); added series {sel.added_series}, "
        f"non-scenario {sel.added_nonscenario}"
    )
    for t, c in counts(qs).items():
        typer.echo(
            f"  {t:<13} dev={c['dev']:>3} test={c['test']:>3} "
            f"recorded={c['recorded']:>3} annotation_only={c['annotation_only']:>3}"
        )


@ami_app.command("extension-questions")
def ami_extension_questions(
    seed: Annotated[int, typer.Option()] = 42,
    out: Annotated[Path, typer.Option(help="Output folder (committed: AMI is CC BY 4.0).")] = Path(
        "data/ami_questions"
    ),
) -> None:
    """Questions for the seven replay-extension meetings, in their own files so the original set
    never changes (no LLM; same seed, byte-identical). DEV/TEST by the existing rule."""
    from tenor.ami.corpus import load_meeting
    from tenor.ami.products import AMI_EXTENSION
    from tenor.ami.questions import (
        EXTENSION_CLOSED_BOOK_FILE,
        EXTENSION_FILE,
        build_extension_questions,
        closed_book_prompts,
        counts,
        write_jsonl,
    )
    from tenor.llm.client import load_prompt

    qs = build_extension_questions([load_meeting(m) for m in AMI_EXTENSION], seed)
    write_jsonl(qs, out / EXTENSION_FILE)
    write_jsonl(
        closed_book_prompts(qs, load_prompt("closed_book.v1")), out / EXTENSION_CLOSED_BOOK_FILE
    )
    typer.echo(f"{len(qs)} questions from {len(AMI_EXTENSION)} meetings -> {out}/{EXTENSION_FILE}")
    for t, c in counts(qs).items():
        typer.echo(f"  {t:<13} dev={c['dev']:>3} test={c['test']:>3}")
    for mid in AMI_EXTENSION:
        typer.echo(f"  {mid}  {sum(mid in q.meetings for q in qs)} questions")


# -- evidence compression (thesis A) ------------------------------------------------------------


@app.command("compression")
def compression_cmd(
    split: Annotated[str, typer.Option(help="dev, test or all.")] = "dev",
    conditions: Annotated[
        str, typer.Option(help="Comma-separated; default: the $0 conditions plus closed_book.")
    ] = "",
    questions_file: Annotated[Path, typer.Option("--questions")] = Path(
        "data/ami_questions/questions.jsonl"
    ),
    model: Annotated[str, typer.Option(help="Reader model.")] = "claude-haiku-5-5",
    judge_model: Annotated[str, typer.Option()] = "claude-haiku-5-5",
    judge2: Annotated[str, typer.Option(help="Second judge on 30%, or 'none'.")] = (
        "claude-sonnet-5-5"
    ),
    seed: Annotated[int, typer.Option()] = 42,
    max_usd: Annotated[
        float | None,
        typer.Option(
            "--max-usd",
            help="Hard spend cap for the whole run (reader + judges share one ledger); "
            "default CONCENTRATE_MAX_USD. Required for paid runs; a pilot defaults to $0.10.",
        ),
    ] = None,
    quote_only: Annotated[
        bool, typer.Option("--quote", help="Price the full plan (§5); spends nothing.")
    ] = False,
    pilot: Annotated[
        bool,
        typer.Option(
            "--pilot",
            help="Sandbox run: a few DEV questions x 2 conditions under a tiny cap; prints the "
            "real per-call cost and the full plan extrapolated with a 1.5x margin.",
        ),
    ] = False,
    pilot_n: Annotated[int, typer.Option(help="--pilot: questions.")] = 3,
    breadth: Annotated[
        bool,
        typer.Option(
            "--breadth",
            help="H4: breadth arms (8k/16k/32k x each layer in --conditions, default the $0 "
            "ones) on the detail questions, meetings chosen by the shared retriever.",
        ),
    ] = False,
    mask_ids: Annotated[
        bool,
        typer.Option(
            "--mask-ids/--keep-ids",
            help="Breadth retriever: drop meeting IDs from the query (default; with them it is "
            "an oracle).",
        ),
    ] = True,
    exact: Annotated[
        bool,
        typer.Option(help="--quote: exact input counts via the free count_tokens endpoint."),
    ] = False,
    sizes_only: Annotated[
        bool, typer.Option("--sizes", help="Kept size, verbatim share, evidence recall; $0.")
    ] = False,
    out: Annotated[Path, typer.Option()] = Path("data/cache/compression"),
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """Evidence compression on AMI (docs/method/EVIDENCE_COMPRESSION.md): one reader, notes
    plus a kept layer built before any question is seen. Start with --sizes and --quote."""
    import dataclasses
    import functools
    import json as _json

    from tenor.ami.corpus import load_meeting
    from tenor.ami.questions import load_questions, load_selection
    from tenor.evals import compression as C
    from tenor.evals.deletion_sim import judge_cache_pairs
    from tenor.evals.runner import HEURISTIC_TO_EXACT
    from tenor.llm.client import Ledger, count_tokens, estimate_tokens

    questions = load_questions(questions_file)
    materials = C.ami_materials(load_meeting(m) for m in load_selection().meetings)
    layers = C.build_layers(materials, C.zero_cost_compressors(seed))  # before any question
    retriever = C.SharedRetriever(materials, mask_ids=mask_ids)
    detail = [q for q in questions if q.type in C.DETAIL_TYPES]
    if sizes_only:
        cover = C.breadth_coverage(detail, retriever, materials, layers, C.ZERO_COST_CONDITIONS)
        typer.echo(
            _json.dumps(
                {"layers": C.sizes(layers, questions, materials), "breadth": cover}, indent=1
            )
        )
        return
    cache = Path(os.environ.get("CONCENTRATE_CACHE", "data/cache/llm"))
    a = C.QuoteAssumptions(reader=model, judge=judge_model)
    cal = C.judge_output_calibration(judge_cache_pairs(cache, judge_model), a.judge_out)
    a = C.QuoteAssumptions(
        reader=model,
        judge=judge_model,
        judge2=a.judge2 if judge2 == "none" else judge2,
        judge_out=cal,
    )

    def heuristic(texts: list[str]) -> list[int]:
        return [round(estimate_tokens(t) * HEURISTIC_TO_EXACT) for t in texts]

    counter: C.Counter = heuristic
    if exact:
        counter = functools.partial(count_tokens, model=model, cache_dir=cache)
        how = f"exact (count_tokens, {model}) + measured schema overhead"
    else:
        how = f"offline chars/3.5 x {HEURISTIC_TO_EXACT} (measured undercount on transcripts)"
    if quote_only:
        breadth_rows = C.quote_breadth(questions, materials, layers, counter, retriever, a)
        full = C.quote_markdown(
            C.quote(questions, materials, layers, counter, a) + breadth_rows, how
        )
        breadth_now = C.quote_markdown(
            C.quote_breadth(
                questions, materials, layers, counter, retriever, a, C.ZERO_COST_CONDITIONS
            ),
            how,
        )
        now = C.quote_markdown(
            C.quote(
                questions,
                materials,
                layers,
                counter,
                dataclasses.replace(a, real_questions=0),
                conditions=C.ZERO_COST_CONDITIONS,
                optional=False,
            ),
            how,
        )
        md = (
            f"## Full plan (§5, with H4 breadth)\n\n{full}\n## Next step only: the $0 "
            f"conditions + closed-book on all {len(questions)} AMI questions\n\n{now}\n"
            f"## H4 breadth only, $0 layers, {len(detail)} detail questions\n\n{breadth_now}"
        )
        path = out / "compression_quote.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md)
        typer.echo(md)
        typer.echo(f"quote written: {path} (no money spent)")
        return
    if pilot:
        picked = C.pilot_questions(questions, pilot_n, seed)
        names = [c for c in conditions.split(",") if c] or list(C.PILOT_CONDITIONS)
        cap = max_usd if max_usd is not None else C.PILOT_MAX_USD
    else:
        picked = [q for q in questions if split == "all" or q.split == split]
        names = [c for c in conditions.split(",") if c] or [*C.ZERO_COST_CONDITIONS, C.CLOSED]
        env_cap = os.environ.get("CONCENTRATE_MAX_USD")
        if max_usd is None and not env_cap:
            raise typer.BadParameter(
                "paid runs need a hard cap: pass --max-usd (the approved amount) or set "
                "CONCENTRATE_MAX_USD; run --pilot first"
            )
        cap = max_usd if max_usd is not None else float(env_cap or 0)
    ledger = Ledger()  # one ledger: the cap covers reader + judge + second judge together
    reader = LLMClient(model=model, ledger=ledger, max_usd=cap)
    judge = LLMClient(model=judge_model, ledger=ledger, max_usd=cap)
    j2 = None if judge2 == "none" else LLMClient(model=judge2, ledger=ledger, max_usd=cap)
    if breadth and not pilot:
        layers_for = [c for c in conditions.split(",") if c] or list(C.ZERO_COST_CONDITIONS)
        names = [C.breadth_name(b, c) for b in C.BREADTH_BUDGETS for c in layers_for]
        picked = [q for q in picked if q.type in C.DETAIL_TYPES]

    def make(n: str) -> Any:
        if n == C.CLOSED:
            return C.ClosedBookReader(reader)
        if (bl := C.parse_breadth(n)) is not None:
            return C.BreadthReader(reader, bl[0], bl[1], materials, layers, retriever)
        return C.LayerReader(reader, n, materials, layers)

    readers = [make(n) for n in names]
    what = "PILOT: " if pilot else ""
    if not yes:
        typer.confirm(
            f"{what}Run {len(readers)} conditions on {len(picked)} "
            f"{'dev' if pilot else split} questions (hard cap ${cap:.2f}; run --quote first)?",
            abort=True,
        )
    report = C.run_compression(
        readers,
        picked,
        judge,
        layers=layers,
        materials=materials,
        seed=seed,
        budget_usd=cap,
        out_dir=out,
        spent=ledger.spent_usd,
        judge2=j2,
        judge2_share=1.0 if pilot else C.SECOND_JUDGE_SHARE,
        meta={"split": "dev" if pilot else split, "conditions": names, "pilot": pilot},
    )
    if report["meta"]["stopped_early"]:
        typer.echo(f"stopped early: {report['meta']['stopped_early']}")
    if not pilot:
        typer.echo(_json.dumps(report["summary"], indent=1, default=str)[:4000])
        return
    rows = sorted(out.glob(f"{report['meta']['date']}.compression.jsonl"))
    for line in rows[0].read_text().splitlines() if rows else []:
        x = _json.loads(line)
        typer.echo(
            f"{x['qid']:<28} {x['system']:<16} {x['verdict']:<18} "
            f"j2={x.get('judge2_verdict') or '-':<18} {x['answer']['answer'][:70]!r}"
        )
    pq = C.quote(
        picked,
        materials,
        layers,
        counter,
        dataclasses.replace(a, real_questions=0, judge2_share=1.0),
        conditions=names,
        optional=False,
    )
    measured = C.measure(ledger.records, pq, j2.model if j2 else None)
    plan = C.quote(questions, materials, layers, counter, a) + C.quote_breadth(
        questions, materials, layers, counter, retriever, a
    )
    md, total = C.extrapolate_markdown(plan, measured)
    path = out / "compression_pilot.md"
    path.write_text(
        f"Pilot {report['meta']['date']}: {len(picked)} DEV q x {names}, spent "
        f"${ledger.spent_usd():.4f} (cap ${cap:.2f}). Quote counting: {how}.\n\n{md}"
    )
    typer.echo(md)
    typer.echo(f"pilot spent ${ledger.spent_usd():.4f}; full plan x{C.PILOT_MARGIN}: ${total:.2f}")
    typer.echo(f"written: {path}. Ask before any run over $2.")


@app.command("mcp-contract")
def mcp_contract(
    baseline: Annotated[Path, typer.Option(help="Committed shape baseline.")] = CONTRACT_BASELINE,
    update: Annotated[bool, typer.Option(help="Write the current shapes as the baseline.")] = False,
) -> None:
    """Check Granola's MCP responses still have the shapes we rely on. Exit 1 on drift.

    Calls read tools only (allowlisted), on the newest meeting with a transcript. Prints and
    stores shapes (field names, types, line formats), never values.
    """
    import json

    from tenor.sources.allowlist import AllowlistCaller
    from tenor.sources.contract import READ_TOOLS, diff, run
    from tenor.sources.mcp_transport import LiveMcpCaller

    with LiveMcpCaller() as live:
        current = run(AllowlistCaller(live, READ_TOOLS, "Granola"))
    if update or not baseline.exists():
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
        typer.echo(f"baseline written: {baseline}")
        return
    changes = diff(json.loads(baseline.read_text()), current)
    if not changes:
        typer.echo(f"no drift: {len(current['tools'])} probes match {baseline}")
        return
    typer.echo(f"DRIFT: {len(changes)} change(s) vs {baseline}")
    for line in changes:
        typer.echo(f"  {line}")
    raise typer.Exit(1)
