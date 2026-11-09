"""Prompt caching in tenor.llm: block construction, breakpoint placement, disk-cache keys,
usage parsing and ledger pricing of cache reads / writes. Offline: the real SDK is driven
through ``httpx2.MockTransport``; batches use an in-memory fake."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel

from tenor.evals import list_check as E
from tenor.llm.client import (
    AnthropicBackend,
    BatchItemResult,
    CacheEstimate,
    LLMClient,
    LLMRequest,
    PromptShape,
    TurnRequest,
    breaks_after,
    cache_control,
    cost_usd,
    prefetch_structured,
    prefix_saving_usd,
    simulate_prompt_cache,
    structured_params,
    text_blocks,
    turn_cache_path,
    turn_params,
    usage_counts,
)
from tenor.llm.resilience import RetryPolicy, anthropic_client

OPUS = "claude-opus-5-5"  # $4 in, $20 out, reads $0.20, 512-token minimum
SONNET = "claude-sonnet-5-5"  # $2 in, reads $0.20
FAST = RetryPolicy(max_attempts=1, base_s=0.0, cap_s=0.0, deadline_s=5.0)


class Out(BaseModel):
    x: int


def _req(**kw: Any) -> LLMRequest:
    base: dict[str, Any] = {
        "stage": "t",
        "prompt_id": "t.v1",
        "system": "sys",
        "user": "SHARED|tail",
        "schema": {"type": "object"},
    }
    return LLMRequest(**{**base, **kw})


# -- block construction and breakpoint placement -----------------------------------------------


def test_text_blocks_split_at_breaks_and_concatenate_back() -> None:
    blocks = text_blocks("aaabbbcc", (3, 6), "5m")
    assert [b["text"] for b in blocks] == ["aaa", "bbb", "cc"]
    assert [b.get("cache_control") for b in blocks] == [
        {"type": "ephemeral"},
        {"type": "ephemeral"},
        None,  # the varying tail after the last break is never marked
    ]
    assert "".join(b["text"] for b in blocks) == "aaabbbcc"
    whole = text_blocks("abc", (3,), "1h")
    assert whole == [{"type": "text", "text": "abc", "cache_control": cache_control("1h")}]
    assert text_blocks("abc", (), "5m") == [{"type": "text", "text": "abc"}]


@pytest.mark.parametrize("breaks", [(0,), (4,), (2, 2), (3, 1)])
def test_text_blocks_reject_bad_breaks(breaks: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        text_blocks("abc", breaks, "5m")


def test_cache_control_ttls() -> None:
    assert cache_control("5m") == {"type": "ephemeral"}
    assert cache_control("1h") == {"type": "ephemeral", "ttl": "1h"}
    with pytest.raises(ValueError):
        cache_control("10m")


def test_breaks_after_puts_a_break_after_every_part_but_the_last() -> None:
    assert breaks_after("ab", "cd", "e") == ("abcde", (2, 4))
    assert breaks_after("ab", "", "e") == ("abe", (2,))  # an empty part adds no break
    assert breaks_after("only") == ("only", ())


def test_structured_params_cache_system_by_default_and_split_user() -> None:
    params = structured_params(_req(cache_breaks=(7,)), OPUS)
    assert params["system"] == [
        {"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}
    ]
    content = params["messages"][0]["content"]
    assert content == [
        {"type": "text", "text": "SHARED|", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "tail"},
    ]
    # No breaks: the user text goes as a plain string, as before.
    assert structured_params(_req(), OPUS)["messages"][0]["content"] == "SHARED|tail"


def test_structured_params_one_hour_ttl_and_caching_off() -> None:
    params = structured_params(_req(cache_breaks=(7,), cache_ttl="1h"), OPUS)
    assert params["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert params["messages"][0]["content"][0]["cache_control"]["ttl"] == "1h"
    off = structured_params(_req(cache_breaks=(7,), cache_ttl=None), OPUS)
    assert off["system"] == "sys" and off["messages"][0]["content"] == "SHARED|tail"
    legacy = structured_params(_req(cache_ttl=None, cache_system=True), OPUS)
    assert legacy["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_at_most_four_breakpoints() -> None:
    structured_params(_req(user="abcdef", cache_breaks=(1, 2, 3)), OPUS)  # system + 3 = 4
    with pytest.raises(ValueError, match="breakpoints"):
        structured_params(_req(user="abcdef", cache_breaks=(1, 2, 3, 4)), OPUS)


def test_turn_params_split_the_first_message() -> None:
    req = TurnRequest(
        "t", "t.v1", "sys", ({"role": "user", "content": "NOTES|question"},), cache_breaks=(6,)
    )
    params = turn_params(req, OPUS)
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    blocks = params["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "NOTES|", "cache_control": {"type": "ephemeral"}}
    assert blocks[1] == {"type": "text", "text": "question"}
    assert req.messages[0]["content"] == "NOTES|question"  # the request itself is untouched


# -- disk-cache keys are unchanged by cache markers -------------------------------------------


def test_cache_markers_do_not_change_disk_cache_keys(tmp_path: Path) -> None:
    client = LLMClient(model=OPUS, cache_dir=tmp_path)
    plain = _req()
    assert client.cache_path(plain) == client.cache_path(_req(cache_breaks=(7,)))
    assert client.cache_path(plain) == client.cache_path(_req(cache_ttl="1h"))
    assert client.cache_path(plain) == client.cache_path(_req(cache_ttl=None))
    turn = TurnRequest("t", "t.v1", "sys", ({"role": "user", "content": "NOTES|q"},))
    marked = TurnRequest(
        "t", "t.v1", "sys", ({"role": "user", "content": "NOTES|q"},), (), None, 16000,
        "medium", False, (6,), "1h",
    )  # fmt: skip
    assert turn_cache_path(client, turn) == turn_cache_path(client, marked)


# -- usage parsing and pricing ----------------------------------------------------------------


def test_usage_counts_read_cache_fields() -> None:
    usage = {
        "input_tokens": 40,
        "output_tokens": 10,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 600,
        "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 500},
    }
    assert usage_counts(usage) == {
        "input_tokens": 40,
        "output_tokens": 10,
        "cache_read_tokens": 900,
        "cache_write_tokens": 600,
        "cache_write_1h_tokens": 500,
    }
    assert usage_counts({"input_tokens": 1})["cache_read_tokens"] == 0


def test_cost_prices_reads_and_both_write_ttls() -> None:
    # Opus 5.5: 1M uncached $4, 1M reads $0.20, 1M 5-minute writes $5, 1M 1-hour writes $8.
    m = 1_000_000
    assert cost_usd(OPUS, m, 0) == pytest.approx(4.0)
    assert cost_usd(OPUS, 0, 0, cache_read_tokens=m) == pytest.approx(0.20)
    assert cost_usd(OPUS, 0, 0, cache_write_tokens=m) == pytest.approx(5.0)
    assert cost_usd(OPUS, 0, 0, cache_write_tokens=m, cache_write_1h_tokens=m) == pytest.approx(8.0)
    mixed = cost_usd(OPUS, 0, 0, 0, 2 * m, cache_write_1h_tokens=m)
    assert mixed == pytest.approx(13.0)
    assert cost_usd(OPUS, 0, 0, cache_read_tokens=m, batch=True) == pytest.approx(0.10)
    assert cost_usd("claude-haiku-5-5", 0, 0, cache_read_tokens=m) == pytest.approx(0.01)
    with pytest.raises(ValueError):
        cost_usd(OPUS, 0, 0, 0, 10, cache_write_1h_tokens=11)


def _sdk(reply: dict[str, Any], seen: list[dict[str, Any]]) -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=reply)

    return anthropic_client(
        api_key="test-key", http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )


def test_live_path_sends_markers_and_ledger_prices_cache_tokens(tmp_path: Path) -> None:
    reply = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": OPUS,
        "content": [{"type": "text", "text": '{"x": 1}'}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 50,
            "output_tokens": 10,
            "cache_read_input_tokens": 2000,
            "cache_creation_input_tokens": 1000,
            "cache_creation": {"ephemeral_5m_input_tokens": 1000, "ephemeral_1h_input_tokens": 0},
        },
    }
    seen: list[dict[str, Any]] = []
    backend = AnthropicBackend(FAST, sdk_client=_sdk(reply, seen), sleep=lambda s: None)
    client = LLMClient(backend, model=OPUS, cache_dir=tmp_path)
    assert client.structured(_req(cache_breaks=(7,)), Out).x == 1
    body = seen[0]
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    rec = client.ledger.records[0]
    assert (rec.cache_read_tokens, rec.cache_write_tokens, rec.input_tokens) == (2000, 1000, 50)
    want = (50 * 4 + 10 * 20 + 2000 * 0.20 + 1000 * 5.0) / 1_000_000
    assert rec.cost_usd == pytest.approx(want)
    stage = client.ledger.by_stage()["t"]
    assert stage["cache_read_tokens"] == 2000 and stage["cache_write_tokens"] == 1000
    # A replay from the disk cache keeps the original cost and cache split, and spends $0.
    replay = LLMClient(model=OPUS, cache_dir=tmp_path, offline=True)
    replay.structured(_req(cache_breaks=(7,)), Out)
    assert replay.ledger.records[0].cost_usd == pytest.approx(want)
    assert replay.ledger.records[0].cache_read_tokens == 2000
    assert replay.ledger.spent_usd() == 0.0


class _Batches:
    """One-shot fake batch backend whose replies report cache use."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, requests: list[dict[str, Any]]) -> str:
        self.requests = requests
        return "b1"

    def retrieve(self, batch_id: str) -> str:
        return "ended"

    def results(self, batch_id: str) -> Iterable[BatchItemResult]:
        for r in self.requests:
            usage = {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 0,
            }
            msg = {
                "model": OPUS,
                "content": [{"type": "text", "text": '{"x": 2}'}],
                "stop_reason": "end_turn",
                "usage": usage,
            }
            yield BatchItemResult(r["custom_id"], "succeeded", msg)


def test_batch_prefetch_keeps_cache_tokens_in_the_cached_completion(tmp_path: Path) -> None:
    client = LLMClient(model=OPUS, cache_dir=tmp_path)
    fake = _Batches()
    req = _req(cache_breaks=(7,))
    prefetch_structured(client, [req], fake, sleep=lambda s: None)
    sent = fake.requests[0]["params"]
    assert sent["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    replay = LLMClient(model=OPUS, cache_dir=tmp_path, offline=True)
    assert replay.structured(req, Out).x == 2
    rec = replay.ledger.records[0]
    assert rec.cache_read_tokens == 1000 and rec.batch
    assert rec.cost_usd == pytest.approx((10 * 4 + 5 * 20 + 1000 * 0.20) / 1e6 * 0.5)


# -- cache simulation for quotes --------------------------------------------------------------


def test_simulator_reads_shared_prefixes_and_skips_short_ones() -> None:
    sys = ("sys", 600)
    shapes = [
        PromptShape((sys, ("meeting-A", 5000)), tail_tokens=100),
        PromptShape((sys, ("meeting-A", 5000)), tail_tokens=100),
        PromptShape((sys, ("meeting-B", 4000)), tail_tokens=100),
    ]
    est = simulate_prompt_cache(OPUS, shapes)
    assert est.write == 5600 + 4000  # A's full prefix once, then only B's new segment
    assert est.read == 5600 + 600  # call 2 reads A's prefix; call 3 reads the system part
    assert est.uncached == 300
    assert est.total == sum(sum(t for _, t in s.segments) + s.tail_tokens for s in shapes)
    short = simulate_prompt_cache(OPUS, [PromptShape((("s", 300),))] * 3)
    assert (short.read, short.write, short.uncached) == (0, 0, 900)  # below 512: never cached


def test_simulator_writes_deeper_breakpoint_when_the_first_is_too_short() -> None:
    shapes = [PromptShape((("sys", 300), ("ctx", 300)))] * 2
    est = simulate_prompt_cache(OPUS, shapes)
    assert (est.write, est.read, est.uncached) == (600, 600, 0)


def test_saving_matches_hand_arithmetic() -> None:
    est = CacheEstimate(calls=3, uncached=0, read=20_000, write=10_000)
    plain = 30_000 * 2.0 / 1e6
    cached = (10_000 * 2.0 * 1.25 + 20_000 * 0.20) / 1e6
    assert est.saving_usd(SONNET) == pytest.approx(plain - cached)
    assert prefix_saving_usd(SONNET, 10_000, 3) == pytest.approx(plain - cached)
    assert prefix_saving_usd(SONNET, 10_000, 1) < 0  # a lone call pays the write premium
    assert prefix_saving_usd(SONNET, 100, 5) == 0.0  # below the minimum: nothing happens
    one_h = prefix_saving_usd(SONNET, 10_000, 3, ttl="1h")
    assert one_h == pytest.approx(plain - (10_000 * 2.0 * 2.0 + 20_000 * 0.20) / 1e6)


# -- E1's judge: static first, breakpoints at the shared context ------------------------------


def _ctx() -> tuple[str, E.Gold, list[E.Line]]:
    ref = [E.Line(1, "PM", "00:00", "we ship in Q3"), E.Line(2, "ID", "00:05", "agreed")]
    return "What was decided?", E.Gold((("G1", "Ship in Q3."),), "PM = Ana"), ref


def test_judge_v1_text_is_unchanged_and_breaks_before_the_answer() -> None:
    q, gold, ref = _ctx()
    req = E.judge_request(q, gold, ref, [("A1", "Ship in Q3.")])
    legacy = E.judge_context(q, gold, ref) + "\n\n" + E.judge_answer_block([("A1", "Ship in Q3.")])
    assert req.user == legacy  # same bytes as before caching: disk-cache keys still match
    assert req.user[: req.cache_breaks[0]].endswith("</reference_transcript>\n\n")
    assert req.user[req.cache_breaks[0] :].startswith("System answer")
    lone = E.judge_request(q, gold, ref, "free text", share=False)
    assert lone.cache_breaks == ()  # one judge call on the question: no write premium


def test_judge_v2_puts_the_transcript_first_with_its_own_breakpoint() -> None:
    q, gold, ref = _ctx()
    req = E.judge_request(q, gold, ref, "x", prompt_id=E.JUDGE_PROMPT_TRANSCRIPT_FIRST)
    assert req.user.startswith("<reference_transcript>") and len(req.cache_breaks) == 2
    first, second = req.cache_breaks
    assert req.user[:first].endswith("</reference_transcript>\n\n")
    assert req.user[first:second].startswith("Question: ")
    assert req.prompt_id == "judge_items.v2"
    lone = E.judge_request(
        q, gold, ref, "x", prompt_id=E.JUDGE_PROMPT_TRANSCRIPT_FIRST, share=False
    )
    assert lone.cache_breaks == (first,)  # the meeting-level breakpoint stays
    assert lone.user == req.user
