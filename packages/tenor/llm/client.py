"""The single gateway for every LLM call in Concentrate.

Responsibilities:

* **Structured output.** Every call returns JSON validated against a Pydantic model, requested
  via ``output_config.format`` (forced tool use is rejected by current Claude models).
* **Reproducibility.** A disk cache keyed by a hash of (model, prompt id, system, user, schema,
  max_tokens, effort). Re-running an eval or the synthetic generator with a warm cache makes no
  API calls and returns byte-identical outputs.
* **Accounting.** Every call (cached or not) is recorded in a :class:`Ledger` with tokens, cost
  and latency, aggregated per pipeline stage.
* **Robustness.** Explicit timeouts and our own logged retries with exponential backoff
  (:mod:`tenor.llm.resilience`); server-side refusal fallback; refusals and truncation
  raise typed errors instead of returning partial JSON. A call that fails or times out writes
  nothing to the cache and nothing to the ledger. Every reply beats the stall watchdog.
* **Prompt caching.** The system prompt is a ``cache_control`` breakpoint by default (tools
  and the structured-output schema render before it, so they ride along), and a request can add
  breakpoints inside its user text (``cache_breaks``) after a large shared prefix such as a
  transcript. Cache reads and writes (5-minute and 1-hour) are priced at their real rates.
  Breakpoints are a transport detail: they are not part of the disk-cache key.
* **Batch prefetch.** :func:`prefetch_turns` / :func:`prefetch_structured` send many requests
  through the Message Batches API (50% price) and write each result into the *same* cache file
  the interactive path would, so the normal code then replays it with no divergence. Batch
  completions are flagged (``batch=True``): priced at the batch rate, never used for latency.

The backend is pluggable so tests run with a fake and never touch the network.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypedDict, TypeVar

import httpx2
from pydantic import BaseModel

from tenor.llm.resilience import (
    DEFAULT_POLICY,
    MIN_READ_TIMEOUT_S,
    RetryPolicy,
    anthropic_client,
    call_with_retries,
    timeout_for,
)
from tenor.watchdog import beat

DEFAULT_MODEL = "claude-opus-5-5"

# USD per 1M tokens (input, output). Source: Anthropic model table, cached 2026-10-06.
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

M = TypeVar("M", bound=BaseModel)


class LLMError(Exception):
    """Base error for LLM calls."""


class LLMRefusal(LLMError):
    """The model (and its fallback) declined the request."""


class LLMTruncated(LLMError):
    """Output hit ``max_tokens``; the JSON is incomplete."""


class BudgetExceeded(LLMError):
    """A live call would take this run past its approved spend cap (``max_usd``)."""


# Prompt caching (claude-api skill, shared/prompt-caching.md + model table, read 2026-11-08):
# reads cost ~0.1x the input price, except Opus 5.5 ($0.20/MTok = 0.05x), Sonnet 5.5 ($0.20) and
# Fable 5.1 ($0.25 = 0.025x). Haiku 5.5's read price is not listed there: 0.1x is ASSUMED.
# Writes cost 1.25x the input price with the default 5-minute TTL, 2x with the 1-hour TTL.
CACHE_READ_PER_MTOK: dict[str, float] = {
    "claude-fable-5-1": 0.25,
    "claude-opus-5-5": 0.20,
    "claude-sonnet-5-5": 0.20,
}
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_WRITE_1H_MULTIPLIER = 2.0
# Shortest prefix the API will cache, per model (same source). A shorter prefix with a marker
# is silently not cached: no error, no write charge, ``cache_creation_input_tokens == 0``.
MIN_CACHEABLE_TOKENS: dict[str, int] = {
    "claude-fable-5-1": 512,
    "claude-opus-5-5": 512,
    "claude-opus-5": 512,
    "claude-sonnet-5-5": 512,
    "claude-haiku-5-5": 512,
    "claude-opus-4-8": 1024,
    "claude-sonnet-5": 1024,
}
MAX_CACHE_BREAKPOINTS = 4  # per request, across tools, system and messages
CACHE_TTLS = ("5m", "1h")
# Message Batches API: "All usage is charged at 50 percent of the standard API prices"
# (platform.claude.com/docs/en/build-with-claude/batch-processing, checked 2026-11-02).
BATCH_DISCOUNT = 0.5


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    *,
    batch: bool = False,
    cache_write_1h_tokens: int = 0,
) -> float:
    """Price a call. ``input_tokens`` excludes cached tokens, as the API reports it.

    ``cache_write_tokens`` is every token written to the prompt cache
    (``cache_creation_input_tokens``); ``cache_write_1h_tokens`` is the part of it written with
    the 1-hour TTL (``cache_creation.ephemeral_1h_input_tokens``), priced at 2x instead of 1.25x.
    ``batch`` applies the Message Batches discount to every token class. Unknown models raise
    so we never silently report $0.
    """
    if model not in PRICING:
        raise KeyError(f"no pricing for model {model!r}; add it to tenor.llm.client.PRICING")
    if not 0 <= cache_write_1h_tokens <= cache_write_tokens:
        raise ValueError("cache_write_1h_tokens must be part of cache_write_tokens")
    p_in, p_out = PRICING[model]
    full = (
        input_tokens * p_in
        + output_tokens * p_out
        + cache_read_tokens * cache_read_price(model)
        + (cache_write_tokens - cache_write_1h_tokens) * p_in * CACHE_WRITE_MULTIPLIER
        + cache_write_1h_tokens * p_in * CACHE_WRITE_1H_MULTIPLIER
    ) / 1_000_000
    return full * BATCH_DISCOUNT if batch else full


def cache_read_price(model: str) -> float:
    """USD per 1M cache-read tokens for ``model``."""
    return CACHE_READ_PER_MTOK.get(model, PRICING[model][0] * 0.1)


def min_cacheable(model: str) -> int:
    """The shortest cacheable prefix for ``model`` (unknown models: the conservative 4096)."""
    return MIN_CACHEABLE_TOKENS.get(model, 4096)


def prefix_saving_usd(
    model: str, prefix_tokens: int, calls: int, *, ttl: str = "5m", batch: bool = False
) -> float:
    """What caching a ``prefix_tokens`` prefix saves over ``calls`` back-to-back calls that
    share it: the first call writes it, the others read it (a warm cache: the calls start
    within the TTL of each other). Negative for a single call (the write premium); 0 when the
    prefix is below the model's cacheable minimum (the API then caches nothing, charges
    nothing extra). Input side only: output is unaffected by caching."""
    if calls <= 0 or prefix_tokens < min_cacheable(model):
        return 0.0
    write_1h = prefix_tokens if ttl == "1h" else 0
    plain = cost_usd(model, prefix_tokens * calls, 0, batch=batch)
    cached = cost_usd(
        model,
        0,
        0,
        prefix_tokens * (calls - 1),
        prefix_tokens,
        batch=batch,
        cache_write_1h_tokens=write_1h,
    )
    return plain - cached


@dataclass(frozen=True)
class PromptShape:
    """One call as the prompt cache sees it: the cacheable prefix as ordered segments, each
    ``(identity, tokens)`` and each ending at a breakpoint, then ``tail_tokens`` never cached.
    Two calls share a segment when the identities of it and of every segment before it match
    (the cache is a prefix match)."""

    segments: tuple[tuple[str, int], ...]
    tail_tokens: int = 0


@dataclass
class CacheEstimate:
    """Input tokens of a sequence of calls split as the prompt cache would bill them."""

    calls: int = 0
    uncached: int = 0
    read: int = 0
    write: int = 0

    @property
    def total(self) -> int:
        return self.uncached + self.read + self.write

    @property
    def read_share(self) -> float:
        """Share of all input tokens served from the cache."""
        return self.read / self.total if self.total else 0.0

    def saving_usd(self, model: str, *, ttl: str = "5m", batch: bool = False) -> float:
        """Input cost without caching minus input cost with it (negative: writes not repaid)."""
        plain = cost_usd(model, self.total, 0, batch=batch)
        w1h = self.write if ttl == "1h" else 0
        cached = cost_usd(
            model, self.uncached, 0, self.read, self.write, batch=batch, cache_write_1h_tokens=w1h
        )
        return plain - cached


def simulate_prompt_cache(model: str, shapes: Iterable[PromptShape]) -> CacheEstimate:
    """Replay ``shapes`` in order against an always-warm prompt cache. Each call reads the
    longest prefix already written (ending at a breakpoint), writes every deeper breakpoint
    whose cumulative prefix reaches the model's minimum, and pays plain input for the rest.

    ASSUMED: calls sharing a prefix start within the TTL of each other (true for calls grouped
    by question / meeting and run back to back; a batch's hits are best-effort, so for batches
    this is an upper bound on the saving)."""
    floor = min_cacheable(model)
    seen: set[tuple[str, ...]] = set()
    est = CacheEstimate()
    for shape in shapes:
        est.calls += 1
        key: tuple[str, ...] = ()
        cum, cached_upto, read_upto = 0, 0, 0
        for ident, tokens in shape.segments:
            key += (ident,)
            cum += tokens
            if key in seen:
                read_upto = cached_upto = cum
            elif cum >= floor:
                est.write += cum - cached_upto
                cached_upto = cum
                seen.add(key)
        est.read += read_upto
        est.uncached += cum - cached_upto + shape.tail_tokens
    return est


def cache_control(ttl: str) -> dict[str, str]:
    """The ``cache_control`` value for a breakpoint with ``ttl`` (``5m`` default, or ``1h``)."""
    if ttl not in CACHE_TTLS:
        raise ValueError(f"cache TTL must be one of {CACHE_TTLS}, not {ttl!r}")
    return {"type": "ephemeral"} if ttl == "5m" else {"type": "ephemeral", "ttl": "1h"}


def text_blocks(text: str, breaks: Sequence[int], ttl: str) -> list[dict[str, Any]]:
    """``text`` as ordered text blocks, with a cache breakpoint ending at each offset in
    ``breaks`` (character offsets, strictly increasing, in ``(0, len(text)]``). The blocks
    concatenate back to ``text`` exactly; the tail after the last break has no marker."""
    if not breaks:
        return [{"type": "text", "text": text}]
    bounds = list(breaks)
    if any(b <= a for a, b in zip([0, *bounds], bounds, strict=False)) or bounds[-1] > len(text):
        raise ValueError(f"cache breaks must increase within (0, {len(text)}]: {bounds}")
    blocks: list[dict[str, Any]] = []
    start = 0
    for b in bounds:
        blocks.append({"type": "text", "text": text[start:b], "cache_control": cache_control(ttl)})
        start = b
    if start < len(text):
        blocks.append({"type": "text", "text": text[start:]})
    return blocks


def breaks_after(*parts: str) -> tuple[str, tuple[int, ...]]:
    """Join ``parts`` into one text with a cache breakpoint after every part but the last:
    ``user, breaks = breaks_after(shared_context, question)``. Empty parts add no break."""
    breaks: list[int] = []
    pos = 0
    for p in parts[:-1]:
        pos += len(p)
        if p and (not breaks or breaks[-1] != pos):
            breaks.append(pos)
    return "".join(parts), tuple(breaks)


def _system_blocks(system: str, ttl: str | None) -> Any:
    """The system prompt as sent: a plain string when caching is off, else one block marked
    as a breakpoint (tools and the output schema render before it and are cached with it)."""
    if ttl is None or not system:
        return system
    return [{"type": "text", "text": system, "cache_control": cache_control(ttl)}]


def _check_breakpoints(system: str, breaks: Sequence[int], ttl: str | None) -> None:
    n = (1 if ttl is not None and system else 0) + (len(breaks) if ttl is not None else 0)
    if n > MAX_CACHE_BREAKPOINTS:
        raise ValueError(f"{n} cache breakpoints; the API allows {MAX_CACHE_BREAKPOINTS}")


class UsageCounts(TypedDict):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cache_write_1h_tokens: int


def usage_counts(usage: dict[str, Any]) -> UsageCounts:
    """Token counts from a Messages API ``usage`` object (as JSON): uncached input, output,
    cache reads, cache writes (all TTLs) and the 1-hour part of the writes."""
    creation = usage.get("cache_creation") or {}
    return UsageCounts(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        cache_write_1h_tokens=int(creation.get("ephemeral_1h_input_tokens") or 0),
    )


def estimate_tokens(text: str) -> int:
    """Cheap offline token estimate (~3.5 chars/token for English prose). Planning only."""
    return max(1, round(len(text) / 3.5))


@dataclass(frozen=True)
class LLMRequest:
    stage: str  # pipeline stage for accounting, e.g. "synth.polish"
    prompt_id: str  # versioned prompt file stem, e.g. "synth_polish.v1"
    system: str
    user: str
    schema: dict[str, Any]
    max_tokens: int = 16000
    effort: str = "medium"
    # Legacy: part of existing disk-cache keys when True, so kept. The system prompt is now a
    # cache breakpoint whenever ``cache_ttl`` is set, whatever this says.
    cache_system: bool = False
    # Character offsets in ``user`` after which to place a cache breakpoint (the end of a large
    # prefix shared with other calls: a transcript, a notes block). At most 3 (system is the 4th).
    cache_breaks: tuple[int, ...] = ()
    cache_ttl: str | None = "5m"  # "5m", "1h", or None for no cache_control at all


@dataclass(frozen=True)
class Completion:
    """Raw backend output."""

    text: str
    model: str  # model that actually served the request (may differ after a fallback)
    input_tokens: int
    output_tokens: int
    stop_reason: str
    batch: bool = False  # produced by the Message Batches API (batch price, no latency)
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0  # all TTLs
    cache_write_1h_tokens: int = 0  # the 1-hour-TTL part of cache_write_tokens

    def cost(self) -> float:
        """What this completion cost (or originally cost, if replayed from the disk cache)."""
        return cost_usd(
            self.model,
            self.input_tokens,
            self.output_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
            batch=self.batch,
            cache_write_1h_tokens=self.cache_write_1h_tokens,
        )


class Backend(Protocol):
    def complete(self, request: LLMRequest, model: str) -> Completion: ...


def structured_params(request: LLMRequest, model: str) -> dict[str, Any]:
    """Messages API params for a single-turn structured call (shared by interactive and batch).

    Static content first: system (a breakpoint), then the user text, split into blocks at
    ``cache_breaks`` (each block ending at a break is a breakpoint)."""
    ttl = request.cache_ttl
    _check_breakpoints(request.system, request.cache_breaks, ttl)
    user: Any = request.user
    if ttl is not None and request.cache_breaks:
        user = text_blocks(request.user, request.cache_breaks, ttl)
    return {
        "model": model,
        "max_tokens": request.max_tokens,
        "system": _system_blocks(request.system, ttl or ("5m" if request.cache_system else None)),
        "messages": [{"role": "user", "content": user}],
        "output_config": {
            "effort": request.effort,
            "format": {"type": "json_schema", "schema": request.schema},
        },
    }


class AnthropicBackend:
    """Calls the Claude Messages API with structured output and refusal fallback.

    Each call gets a timeout scaled to its ``max_tokens`` and is retried under ``policy``
    (SDK retries are off; see :mod:`tenor.llm.resilience`).
    """

    def __init__(
        self,
        policy: RetryPolicy = DEFAULT_POLICY,
        *,
        sdk_client: Any | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        # The SDK is imported lazily: tests and offline runs never need it configured.
        self._client = sdk_client if sdk_client is not None else anthropic_client()
        self._policy, self._sleep = policy, sleep

    def complete(self, request: LLMRequest, model: str) -> Completion:
        kwargs = structured_params(request, model)
        if model in _FALLBACK_MODELS:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        kwargs["timeout"] = timeout_for(request.max_tokens)
        response = call_with_retries(
            lambda: self._client.beta.messages.create(**kwargs),
            what=f"{request.stage} ({request.prompt_id})",
            policy=self._policy,
            sleep=self._sleep,
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        usage = response.usage.model_dump(mode="json", exclude_none=True)
        return Completion(
            text=text,
            model=response.model,
            stop_reason=str(response.stop_reason),
            **usage_counts(usage),
        )


@dataclass
class CallRecord:
    stage: str
    prompt_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float  # what the call cost (or originally cost, if served from cache)
    cached: bool
    latency_s: float
    key: str
    batch: bool = False  # completion came from the Message Batches API: latency is not real
    # Prompt-cache tokens. ``input_tokens`` keeps its historical meaning per path (uncached only
    # for :meth:`LLMClient.structured`, uncached + cached for :func:`run_turn`); these two say
    # how much of the prompt was read from / written to the cache.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class Ledger:
    """Accumulates per-call records; summarises tokens and spend by stage."""

    records: list[CallRecord] = field(default_factory=list)

    def add(self, record: CallRecord) -> None:
        self.records.append(record)

    def spent_usd(self) -> float:
        """Money actually spent in this process (cache hits are free)."""
        return sum(r.cost_usd for r in self.records if not r.cached)

    def by_stage(self) -> dict[str, dict[str, float]]:
        """Per stage: calls, disk-cache replays (``cached``), tokens (incl. prompt-cache reads
        and writes) and USD actually spent in this process."""
        out: dict[str, dict[str, float]] = defaultdict(
            lambda: {
                "calls": 0,
                "cached": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "usd": 0.0,
            }
        )
        for r in self.records:
            s = out[r.stage]
            s["calls"] += 1
            s["cached"] += int(r.cached)
            s["input_tokens"] += r.input_tokens
            s["output_tokens"] += r.output_tokens
            s["cache_read_tokens"] += r.cache_read_tokens
            s["cache_write_tokens"] += r.cache_write_tokens
            s["usd"] += 0.0 if r.cached else r.cost_usd
        return dict(out)

    def to_json(self) -> list[dict[str, Any]]:
        return [asdict(r) for r in self.records]


class LLMClient:
    """Structured, cached, accounted LLM calls. Construct once per run."""

    def __init__(
        self,
        backend: Backend | None = None,
        *,
        model: str | None = None,
        cache_dir: Path | None = None,
        ledger: Ledger | None = None,
        offline: bool = False,
        max_usd: float | None = None,
    ) -> None:
        self.model = model or os.environ.get("CONCENTRATE_MODEL") or DEFAULT_MODEL
        self._backend = backend
        self._cache_dir = cache_dir or Path(os.environ.get("CONCENTRATE_CACHE", "data/cache/llm"))
        self.ledger = ledger or Ledger()
        self.offline = offline  # cache-only: raise instead of calling the API on a miss
        # Hard spend cap for this process: set it to the amount Kian approved. A live call is
        # refused once spend reaches it, so a run can overshoot by at most one call; batches are
        # checked against a worst-case estimate before they are submitted.
        env_cap = os.environ.get("CONCENTRATE_MAX_USD")
        self.max_usd = max_usd if max_usd is not None else (float(env_cap) if env_cap else None)

    def check_budget(self, next_usd: float = 0.0) -> None:
        """Raise :class:`BudgetExceeded` if spending ``next_usd`` more would pass ``max_usd``."""
        if self.max_usd is None:
            return
        spent = self.ledger.spent_usd()
        if spent >= self.max_usd or spent + next_usd > self.max_usd:
            raise BudgetExceeded(
                f"spend cap ${self.max_usd:.2f} reached: spent ${spent:.4f}, "
                f"next would add ~${next_usd:.4f}"
            )

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def _key(self, req: LLMRequest) -> str:
        payload = {
            "model": self.model,
            "prompt_id": req.prompt_id,
            "system": req.system,
            "user": req.user,
            "schema": req.schema,
            "max_tokens": req.max_tokens,
            "effort": req.effort,
        }
        if req.cache_system:  # only when set, so existing cache keys are unchanged
            payload["cache_system"] = True
        # cache_breaks / cache_ttl are deliberately left out: prompt-cache markers do not change
        # what the model reads, so a warm disk cache replays across them.
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()

    def cache_path(self, req: LLMRequest) -> Path:
        return self._cache_dir / f"{self._key(req)}.json"

    def is_cached(self, req: LLMRequest) -> bool:
        return self.cache_path(req).exists()

    def write_cache(self, req: LLMRequest, completion: Completion) -> None:
        """Persist ``completion`` exactly as :meth:`structured` does after a live call."""
        path = self.cache_path(req)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"request": asdict(req), "model": self.model, "completion": asdict(completion)}
        path.write_text(json.dumps(entry, ensure_ascii=False, indent=1))

    def structured(self, req: LLMRequest, out_type: type[M]) -> M:
        """Run ``req`` and validate the JSON reply as ``out_type``."""
        key = self._key(req)
        path = self.cache_path(req)
        start = time.perf_counter()
        if path.exists():
            entry = json.loads(path.read_text())
            completion = Completion(**entry["completion"])
            cached = True
        else:
            if self.offline:
                raise LLMError(f"cache miss in offline mode for {req.prompt_id} ({key[:12]})")
            self.check_budget()
            if self._backend is None:
                self._backend = AnthropicBackend()
            completion = self._backend.complete(req, self.model)
            if completion.stop_reason == "refusal":
                raise LLMRefusal(f"{req.prompt_id}: model declined the request")
            if completion.stop_reason == "max_tokens":
                raise LLMTruncated(f"{req.prompt_id}: output truncated at {req.max_tokens}")
            self.write_cache(req, completion)
            cached = False
        self.ledger.add(
            CallRecord(
                stage=req.stage,
                prompt_id=req.prompt_id,
                model=completion.model,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                cost_usd=completion.cost(),
                cached=cached,
                latency_s=0.0 if completion.batch else time.perf_counter() - start,
                key=key,
                batch=completion.batch,
                cache_read_tokens=completion.cache_read_tokens,
                cache_write_tokens=completion.cache_write_tokens,
            )
        )
        beat()
        return out_type.model_validate_json(completion.text)


def load_prompt(prompt_id: str, root: Path | None = None) -> str:
    """Read ``prompts/<prompt_id>.md`` (e.g. ``synth_polish.v1``)."""
    base = root or Path(__file__).resolve().parents[3] / "prompts"
    return (base / f"{prompt_id}.md").read_text()


# -- multi-turn tool use (agentic baselines) ----------------------------------------------------


@dataclass(frozen=True)
class TurnRequest:
    """One Messages API call in a (possibly multi-turn, tool-using) conversation."""

    stage: str
    prompt_id: str
    system: str
    messages: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...] = ()
    schema: dict[str, Any] | None = None  # JSON schema for the final text answer
    max_tokens: int = 16000
    effort: str = "medium"
    # Legacy, part of the disk-cache key (as every field above). The system prompt is a cache
    # breakpoint whenever ``cache_ttl`` is set.
    cache_system: bool = False
    # Not part of the disk-cache key (see :func:`_turn_key`): character offsets in the first
    # message's string content after which to place a breakpoint, and the TTL (None: no caching).
    cache_breaks: tuple[int, ...] = ()
    cache_ttl: str | None = "5m"


@dataclass(frozen=True)
class TurnCompletion:
    content: list[dict[str, Any]]  # assistant content blocks, replayable verbatim
    text: str
    stop_reason: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0  # all TTLs
    latency_s: float = 0.0  # of the original (uncached) call; 0 for batch results
    batch: bool = False  # produced by the Message Batches API (batch price, no latency)
    cache_write_1h_tokens: int = 0  # the 1-hour-TTL part of cache_write_tokens

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


class ToolBackend(Protocol):
    def turn(self, request: TurnRequest, model: str) -> TurnCompletion: ...


def turn_params(request: TurnRequest, model: str) -> dict[str, Any]:
    """Messages API params for one turn (shared by the interactive and batch paths).

    The system prompt is a breakpoint (tools render before it); ``cache_breaks`` split the
    first message's string content into blocks, each ending at a break marked as one."""
    ttl = request.cache_ttl
    _check_breakpoints(request.system, request.cache_breaks, ttl)
    system: list[dict[str, Any]] = [{"type": "text", "text": request.system}]
    if request.cache_system or (ttl is not None and request.system):
        system[0]["cache_control"] = cache_control(ttl or "5m")
    messages = list(request.messages)
    if ttl is not None and request.cache_breaks:
        first = messages[0]
        if not isinstance(first.get("content"), str):
            raise ValueError("cache_breaks need the first message's content as a string")
        messages[0] = {**first, "content": text_blocks(first["content"], request.cache_breaks, ttl)}
    output_config: dict[str, Any] = {"effort": request.effort}
    if request.schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": request.schema}
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": request.max_tokens,
        "system": system,
        "messages": messages,
        "output_config": output_config,
    }
    if request.tools:
        kwargs["tools"] = list(request.tools)
    return kwargs


def turn_completion(
    message: dict[str, Any], *, latency_s: float = 0.0, batch: bool = False
) -> TurnCompletion:
    """Build a :class:`TurnCompletion` from a Messages API ``Message`` serialised to JSON."""
    content: list[dict[str, Any]] = list(message.get("content") or [])
    return TurnCompletion(
        content=content,
        text="".join(b.get("text", "") for b in content if b.get("type") == "text"),
        stop_reason=str(message.get("stop_reason")),
        model=str(message["model"]),
        latency_s=latency_s,
        batch=batch,
        **usage_counts(message.get("usage") or {}),
    )


def _anthropic_turn(
    client: Any,
    request: TurnRequest,
    model: str,
    policy: RetryPolicy = DEFAULT_POLICY,
    sleep: Callable[[float], None] | None = None,
) -> TurnCompletion:
    kwargs = turn_params(request, model)
    if model in _FALLBACK_MODELS:
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"
    kwargs["timeout"] = timeout_for(request.max_tokens)
    holder: dict[str, float] = {}

    def attempt() -> Any:
        holder["start"] = time.perf_counter()  # latency of the attempt that succeeded
        return client.beta.messages.create(**kwargs)

    what = f"{request.stage} ({request.prompt_id})"
    response = call_with_retries(attempt, what=what, policy=policy, sleep=sleep)
    latency = time.perf_counter() - holder["start"]
    return turn_completion(response.model_dump(mode="json", exclude_none=True), latency_s=latency)


def _turn_key(model: str, request: TurnRequest) -> str:
    payload = {"model": model, **asdict(request)}
    payload.pop("stage")  # accounting label only; must not split the cache
    # Prompt-cache markers don't change what the model reads; keys stay as before they existed.
    payload.pop("cache_breaks")
    payload.pop("cache_ttl")
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


def turn_cache_path(client: LLMClient, request: TurnRequest) -> Path:
    """Where :func:`run_turn` caches ``request`` for ``client``'s model."""
    return client.cache_dir / "turns" / f"{_turn_key(client.model, request)}.json"


def write_turn_cache(client: LLMClient, request: TurnRequest, completion: TurnCompletion) -> None:
    """Persist ``completion`` exactly as :func:`run_turn` does after a live call."""
    path = turn_cache_path(client, request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"completion": asdict(completion)}, ensure_ascii=False))


def run_turn(
    client: LLMClient, request: TurnRequest, backend: ToolBackend | None = None
) -> TurnCompletion:
    """Run one cached, accounted Messages call (with optional tools) through ``client``."""
    key = _turn_key(client.model, request)
    path = turn_cache_path(client, request)
    if path.exists():
        completion = TurnCompletion(**json.loads(path.read_text())["completion"])
        cached = True
    else:
        if client.offline:
            raise LLMError(f"cache miss in offline mode for {request.prompt_id} ({key[:12]})")
        client.check_budget()
        if backend is None:
            backend = _SdkToolBackend(anthropic_client())
        completion = backend.turn(request, client.model)
        if completion.stop_reason == "refusal":
            raise LLMRefusal(f"{request.prompt_id}: model declined the request")
        if completion.stop_reason == "max_tokens":
            raise LLMTruncated(f"{request.prompt_id}: output truncated at {request.max_tokens}")
        write_turn_cache(client, request, completion)
        cached = False
    client.ledger.add(
        CallRecord(
            stage=request.stage,
            prompt_id=request.prompt_id,
            model=completion.model,
            input_tokens=completion.total_input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=_turn_cost(completion),
            cached=cached,
            latency_s=completion.latency_s,
            key=key,
            batch=completion.batch,
            cache_read_tokens=completion.cache_read_tokens,
            cache_write_tokens=completion.cache_write_tokens,
        )
    )
    beat()
    return completion


def _turn_cost(c: TurnCompletion) -> float:
    return cost_usd(
        c.model,
        c.input_tokens,
        c.output_tokens,
        c.cache_read_tokens,
        c.cache_write_tokens,
        batch=c.batch,
        cache_write_1h_tokens=c.cache_write_1h_tokens,
    )


class _SdkToolBackend:
    def __init__(
        self,
        sdk_client: Any,
        policy: RetryPolicy = DEFAULT_POLICY,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._client, self._policy, self._sleep = sdk_client, policy, sleep

    def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
        return _anthropic_turn(self._client, request, model, self._policy, self._sleep)


def count_tokens(texts: list[str], model: str, cache_dir: Path) -> list[int]:
    """Exact input-token counts via the free ``messages.count_tokens`` endpoint (cached).

    Counts include a few tokens of message framing per text, which is what a call would pay.
    """
    out: list[int] = []
    sdk: Any = None
    for text in texts:
        key = hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()
        path = cache_dir / "tokens" / f"{key}.txt"
        if path.exists():
            out.append(int(path.read_text()))
            continue
        if sdk is None:
            sdk = anthropic_client(max_tokens=0)
        messages: list[dict[str, Any]] = [{"role": "user", "content": text}]
        result = call_with_retries(
            functools.partial(sdk.messages.count_tokens, model=model, messages=messages),
            what="count_tokens",
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(result.input_tokens))
        out.append(result.input_tokens)
    return out


# -- Message Batches prefetch -------------------------------------------------------------------

# API limits (platform.claude.com/docs/en/api/messages/batches/create, checked 2026-11-02):
# custom_id must match ^[a-zA-Z0-9_-]{1,64}$; a batch holds <= 100,000 requests and <= 256 MB.
# The cache keys are 64-char sha256 hex digests, so they are valid custom_ids as-is.
BATCH_MAX_REQUESTS = 100_000
BATCH_MAX_BYTES = 200 * 1024 * 1024  # stay well under the 256 MB cap (JSON framing overhead)
_CUSTOM_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass(frozen=True)
class BatchItemResult:
    """One line of a batch's results, SDK-independent."""

    custom_id: str
    type: str  # succeeded | errored | canceled | expired
    message: dict[str, Any] | None = None  # the Messages API Message (JSON) when succeeded
    error: str = ""


class BatchBackend(Protocol):
    """The three Message Batches operations prefetch needs (fakeable in tests)."""

    def create(self, requests: list[dict[str, Any]]) -> str:
        """Submit ``[{custom_id, params}]``; return the batch id."""
        ...

    def retrieve(self, batch_id: str) -> str:
        """Return the batch's ``processing_status`` (``in_progress``/``canceling``/``ended``)."""
        ...

    def results(self, batch_id: str) -> Iterable[BatchItemResult]:
        """Stream an ended batch's results (any order)."""
        ...


class AnthropicBatchBackend:
    """Message Batches over the official SDK (``client.messages.batches``, no beta header).

    The server-side ``fallbacks`` parameter is rejected by the Batches API, so batch params
    never carry it; a refused item is simply not cached and the run falls back to the
    interactive call (which does use ``fallbacks``).
    """

    def __init__(self, policy: RetryPolicy = DEFAULT_POLICY, *, sdk_client: Any = None) -> None:
        # Batch bodies can be ~200 MB: allow a long write; replies are small, so reads stay short.
        self._client = sdk_client if sdk_client is not None else anthropic_client(max_tokens=0)
        self._policy = policy
        self._upload_timeout = httpx2.Timeout(MIN_READ_TIMEOUT_S, connect=10.0, write=600.0)

    def create(self, requests: list[dict[str, Any]]) -> str:
        # Not idempotent: a create whose reply is lost may have made a batch server-side. Retrying
        # can then submit it twice (both billed); we accept that over hanging, and log it.
        batch = call_with_retries(
            lambda: self._client.messages.batches.create(
                requests=requests,
                timeout=self._upload_timeout,
            ),
            what=f"batches.create ({len(requests)} requests)",
            policy=self._policy,
        )
        return str(batch.id)

    def retrieve(self, batch_id: str) -> str:
        batch = call_with_retries(
            lambda: self._client.messages.batches.retrieve(batch_id),
            what=f"batches.retrieve {batch_id}",
            policy=self._policy,
        )
        return str(batch.processing_status)

    def results(self, batch_id: str) -> Iterable[BatchItemResult]:
        # Opening the stream is retried; a drop mid-stream raises (rerunning the command resumes:
        # items already written to the cache are skipped).
        stream = call_with_retries(
            lambda: self._client.messages.batches.results(batch_id),
            what=f"batches.results {batch_id}",
            policy=self._policy,
        )
        for item in stream:
            r = item.result
            if r.type == "succeeded":
                message = r.message.model_dump(mode="json", exclude_none=True)
                yield BatchItemResult(item.custom_id, "succeeded", message)
            elif r.type == "errored":
                err = r.error.model_dump(mode="json")
                detail = err.get("error", err)
                yield BatchItemResult(
                    item.custom_id,
                    "errored",
                    error=f"{detail.get('type', '')}: {detail.get('message', '')}",
                )
            else:
                yield BatchItemResult(item.custom_id, str(r.type))


@dataclass
class BatchReport:
    """What a prefetch did. ``fallback`` items were not cached: the run calls them live."""

    requested: int = 0  # distinct requests asked for
    already_cached: int = 0  # skipped: the cache already had them
    submitted: int = 0
    succeeded: int = 0  # written to the cache
    fallback: dict[str, int] = field(default_factory=dict)  # reason -> count (not cached)
    batch_ids: list[str] = field(default_factory=list)
    seconds: float = 0.0  # submit -> all results written
    cost_usd: float = 0.0  # billed (all succeeded items, incl. refused/truncated), batch price


@dataclass(frozen=True)
class _Item:
    key: str
    params: dict[str, Any]
    stage: str
    prompt_id: str
    write: Callable[[dict[str, Any]], None]  # write the cache entry from a usable Message


def _worst_case_usd(model: str, params: dict[str, Any]) -> float:
    """Upper-bound price of one batch item: estimated input, full ``max_tokens`` output."""
    text = json.dumps(
        {k: params.get(k) for k in ("system", "messages", "tools")}, ensure_ascii=False
    )
    return cost_usd(model, estimate_tokens(text), int(params.get("max_tokens", 0)), batch=True)


def _chunks(items: list[_Item]) -> list[list[_Item]]:
    out: list[list[_Item]] = [[]]
    size = 0
    for it in items:
        n = len(json.dumps({"custom_id": it.key, "params": it.params}, ensure_ascii=False))
        if out[-1] and (len(out[-1]) >= BATCH_MAX_REQUESTS or size + n > BATCH_MAX_BYTES):
            out.append([])
            size = 0
        out[-1].append(it)
        size += n
    return [c for c in out if c]


def _run_batch(
    client: LLMClient,
    items: list[_Item],
    already_cached: int,
    backend: BatchBackend | None,
    sleep: Callable[[float], None],
    poll_s: float,
    max_poll_s: float,
) -> BatchReport:
    report = BatchReport(requested=len(items) + already_cached, already_cached=already_cached)
    if not items or client.offline:
        return report
    for it in items:
        if not _CUSTOM_ID.match(it.key):
            raise ValueError(f"cache key {it.key!r} is not a valid batch custom_id")
    client.check_budget(sum(_worst_case_usd(client.model, it.params) for it in items))
    backend = backend or AnthropicBatchBackend()
    by_id = {it.key: it for it in items}
    start = time.perf_counter()
    for chunk in _chunks(items):
        reqs = [{"custom_id": it.key, "params": it.params} for it in chunk]
        report.batch_ids.append(backend.create(reqs))
        report.submitted += len(chunk)
    pending = list(report.batch_ids)
    interval = poll_s
    while pending:
        pending = [b for b in pending if backend.retrieve(b) != "ended"]
        beat()  # a batch can legitimately take hours: polling is liveness
        if pending:
            sleep(interval)
            interval = min(max_poll_s, interval * 1.5)
    seen: set[str] = set()
    fallback: Counter[str] = Counter()
    for batch_id in report.batch_ids:
        for res in backend.results(batch_id):
            item = by_id.get(res.custom_id)
            if item is None or res.custom_id in seen:
                continue
            seen.add(res.custom_id)
            if res.type != "succeeded" or res.message is None:
                fallback[res.type] += 1  # errored/canceled/expired items are not billed
                continue
            # A succeeded item is billed even when we can't use it, so it is always recorded.
            c = turn_completion(res.message, batch=True)
            usd = _turn_cost(c)
            report.cost_usd += usd
            client.ledger.add(
                CallRecord(
                    stage=f"{item.stage}.batch",
                    prompt_id=item.prompt_id,
                    model=c.model,
                    input_tokens=c.total_input_tokens,
                    output_tokens=c.output_tokens,
                    cost_usd=usd,
                    cached=False,
                    latency_s=0.0,
                    key=item.key,
                    batch=True,
                    cache_read_tokens=c.cache_read_tokens,
                    cache_write_tokens=c.cache_write_tokens,
                )
            )
            if c.stop_reason in ("refusal", "max_tokens"):  # as the interactive path: no cache
                fallback[c.stop_reason] += 1
                continue
            item.write(res.message)
            report.succeeded += 1
    missing = len(by_id) - len(seen)
    if missing:
        fallback["missing"] += missing
    report.fallback = dict(fallback)
    report.seconds = time.perf_counter() - start
    return report


def prefetch_turns(
    client: LLMClient,
    requests: Sequence[TurnRequest],
    backend: BatchBackend | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 5.0,
    max_poll_s: float = 60.0,
) -> BatchReport:
    """Batch every uncached ``TurnRequest`` and write results where :func:`run_turn` reads them.

    Already-cached and duplicate requests are skipped. Errored, expired, canceled, refused or
    truncated items are left uncached, so :func:`run_turn` calls them interactively later.
    """
    items: dict[str, _Item] = {}
    cached = 0
    for req in requests:
        key = _turn_key(client.model, req)
        if key in items:
            continue
        if turn_cache_path(client, req).exists():
            cached += 1
            continue

        def write(message: dict[str, Any], req: TurnRequest = req) -> None:
            write_turn_cache(client, req, turn_completion(message, batch=True))

        items[key] = _Item(key, turn_params(req, client.model), req.stage, req.prompt_id, write)
    return _run_batch(client, list(items.values()), cached, backend, sleep, poll_s, max_poll_s)


def prefetch_structured(
    client: LLMClient,
    requests: Sequence[LLMRequest],
    backend: BatchBackend | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 5.0,
    max_poll_s: float = 60.0,
) -> BatchReport:
    """Batch every uncached ``LLMRequest`` and write results where
    :meth:`LLMClient.structured` reads them (same skip/fallback rules as :func:`prefetch_turns`).
    """
    items: dict[str, _Item] = {}
    cached = 0
    for req in requests:
        key = client._key(req)
        if key in items:
            continue
        if client.is_cached(req):
            cached += 1
            continue

        def write(message: dict[str, Any], req: LLMRequest = req) -> None:
            t = turn_completion(message, batch=True)
            # Same text rule as AnthropicBackend.complete: the first text block.
            text = next((b.get("text", "") for b in t.content if b.get("type") == "text"), "")
            c = Completion(
                text,
                t.model,
                t.input_tokens,
                t.output_tokens,
                t.stop_reason,
                True,
                t.cache_read_tokens,  # previously dropped: batch replays under-priced cache use
                t.cache_write_tokens,
                t.cache_write_1h_tokens,
            )
            client.write_cache(req, c)

        items[key] = _Item(
            key, structured_params(req, client.model), req.stage, req.prompt_id, write
        )
    return _run_batch(client, list(items.values()), cached, backend, sleep, poll_s, max_poll_s)
