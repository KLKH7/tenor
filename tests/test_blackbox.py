"""Black-box track: product adapters, the read-only allowlist, recorded answers, the test-split
importer and the committable-report writer. Fakes only; Concentrate's server runs in-process."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from tenor.blackbox.base import (
    AllowlistCaller,
    McpProduct,
    ToolInfo,
    ToolNotAllowed,
    preflight,
)
from tenor.blackbox.kit import (
    NotCommittable,
    ProductSystem,
    TemplateError,
    check_committable,
    parse_test_markdown,
    write_committable,
)
from tenor.blackbox.products import (
    ConcentrateProduct,
    GranolaProduct,
    ProductSpec,
    SpecProduct,
)
from tenor.blackbox.transport import SyncMcpClient
from tenor.evals.real_slice import (
    CATEGORIES,
    RealQuestion,
    load_split,
    run_slice,
    save_questions,
)
from tenor.llm.client import Completion, LLMClient, LLMRequest

NOTE = "00000000-0000-4000-8000-000000000001"
GRANOLA_RAW = (
    "<transcript_note>This answer comes from meeting notes…</transcript_note>\n\n"
    f"Alex owns the launch. [[1]](https://notes.granola.ai/d/{NOTE})"
)
# Every write-capable tool Granola's MCP lists today, plus generic write-ish names.
WRITE_TOOLS = (
    "update_private_notes",
    "create_folder",
    "update_folder",
    "add_note_to_folder",
    "remove_note_from_folder",
    "generate_summary",
    "give_feedback",
    "sign_out",
    "delete_meeting",
    "create_note",
)


class FakeMcp:
    def __init__(self, reply: str = GRANOLA_RAW) -> None:
        self.reply = reply
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        self.calls.append((tool, arguments))
        return self.reply


class FakeJudge:
    def complete(self, request: LLMRequest, model: str) -> Completion:
        verdict = "correct" if "Alex" in request.user.split("System answer:")[-1] else "incorrect"
        return Completion(json.dumps({"verdict": verdict, "reason": "fake"}), model, 300, 20, "x")


def _spec(**kw: Any) -> ProductSpec:
    base: dict[str, Any] = {
        "name": "acme",
        "transport": "stdio",
        "command": "acme-mcp",
        "tool": "ask",
        "question_arg": "q",
        "extra_args": {"mode": "answer"},
        "answer_path": "result.text",
        "citation_path": "result.sources",
        "read_only_tools": ["ask"],
    }
    return ProductSpec(**(base | kw))


def _q(qid: str = "test-001", **kw: Any) -> RealQuestion:
    base: dict[str, Any] = {
        "id": qid,
        "category": "commitment_status",
        "question": "Who owns the launch for the autumn pilot?",
        "gold_answer": "Alex owns the launch",
        "key_terms": ["alex"],
        "evidence_note_ids": [NOTE],
        "as_of": date(2026, 11, 2),
        "status": "verified",
        "origin": "user",
        "split": "test",
    }
    return RealQuestion(**(base | kw))


# -- read-only by construction -------------------------------------------------------------------


@pytest.mark.parametrize(
    "make",
    [
        lambda c: GranolaProduct(c),
        lambda c: ConcentrateProduct(c),
        lambda c: SpecProduct(_spec(), c),
    ],
    ids=["granola", "concentrate", "spec"],
)
def test_every_adapter_refuses_write_tools_before_sending(make: Any) -> None:
    inner = FakeMcp()
    product = make(inner)
    assert len(product.read_only_tools) == 1  # least privilege: only the question tool
    for tool in WRITE_TOOLS:
        with pytest.raises(ToolNotAllowed):
            product.caller.call(tool, {})
    assert inner.calls == []
    assert isinstance(product.caller, AllowlistCaller)


def test_a_product_cannot_call_a_tool_it_did_not_allowlist() -> None:
    class Sneaky(McpProduct):
        name, tool, read_only_tools = "sneaky", "update_private_notes", frozenset({"ask"})

    with pytest.raises(ToolNotAllowed):
        Sneaky(FakeMcp())
    with pytest.raises(ValidationError, match="read_only_tools"):
        _spec(read_only_tools=["something_else"])
    with pytest.raises(ValidationError):
        _spec(transport="http", url=None)


def test_preflight_rejects_tools_the_server_marks_as_writes() -> None:
    p = SpecProduct(_spec(), FakeMcp())
    assert preflight(p, [ToolInfo(name="ask", read_only_hint=True)]) == []
    assert preflight(p, [ToolInfo(name="ask")]) == []  # unannotated: the allowlist decides
    assert preflight(p, [ToolInfo(name="ask", read_only_hint=False)])
    assert preflight(p, [ToolInfo(name="ask", destructive_hint=True)])
    assert preflight(p, [ToolInfo(name="other")]) == ["ask: not offered by the server"]


# -- adapters --------------------------------------------------------------------------------------


def test_granola_adapter_asks_query_granola_meetings_and_parses_citations() -> None:
    inner = FakeMcp()
    ticks = iter([1.0, 3.5])
    a = GranolaProduct(inner, clock=lambda: next(ticks)).ask("Who owns the launch?")
    assert inner.calls == [("query_granola_meetings", {"query": "Who owns the launch?"})]
    assert a.text.startswith("Alex owns") and "transcript_note" not in a.text
    assert a.cited_ids == [NOTE] and a.latency_s == 2.5 and a.abstained is None
    assert a.raw == GRANOLA_RAW


def test_granola_scoped_ask_passes_document_ids_and_waits_out_rate_limits() -> None:
    from tenor.sources.granola_mcp import McpToolError

    class Limited(FakeMcp):
        def call(self, tool: str, arguments: dict[str, Any]) -> str:
            self.calls.append((tool, arguments))
            if len(self.calls) == 1:
                raise McpToolError(tool, "rate_limit", retry_after=7.0)
            return self.reply

    inner, waits = Limited(), []
    ticks = iter([0.0, 10.0, 12.0])  # start (fails), start, end
    a = GranolaProduct(inner, clock=lambda: next(ticks)).ask_scoped(
        "What was decided?", ["n1"], sleep=waits.append
    )
    assert (
        inner.calls
        == [("query_granola_meetings", {"query": "What was decided?", "document_ids": ["n1"]})] * 2
    )
    assert waits == [7.0] and a.latency_s == 2.0 and a.cited_ids == [NOTE]


def test_spec_adapter_maps_arguments_and_json_paths() -> None:
    reply = json.dumps({"result": {"text": " Alex. ", "sources": ["m1", "m2", "m1"]}})
    inner = FakeMcp(reply)
    a = SpecProduct(_spec(), inner).ask("Who?")
    assert inner.calls == [("ask", {"mode": "answer", "q": "Who?"})]
    assert a.text == "Alex." and a.cited_ids == ["m1", "m2"]
    text = SpecProduct(
        _spec(answer_path=None, citation_path=None, citation_regex=r"\[(m\d)\]"),
        FakeMcp("Alex [m1] and [m2]"),
    ).ask("Who?")
    assert text.text == "Alex [m1] and [m2]" and text.cited_ids == ["m1", "m2"]


def test_concentrate_adapter_over_its_real_mcp_server(tmp_path: Path) -> None:
    from tenor.agents.concentrate_system import DB_FILE
    from tenor.mcp_server.server import build_server
    from tenor.mcp_server.service import Answerer, MemoryService
    from tenor.memory.entities import load_directory
    from tenor.memory.store import MemoryStore
    from tests.memory_helpers import ScriptedLLM
    from tests.test_concentrate_system import DIRECTORY, _notes, _script, _system

    system = _system(tmp_path, ScriptedLLM(_script()))
    system.ingest(_notes())
    system.store.close()
    svc = MemoryService(
        MemoryStore(system.store_dir(_notes()) / DB_FILE, read_only=True),
        load_directory(DIRECTORY, _notes()),
        _notes(),
        answerer=Answerer(
            LLMClient(model="claude-opus-5-5", cache_dir=tmp_path / "a"), backend=ScriptedLLM({})
        ),
    )
    with SyncMcpClient(build_server(svc)) as client:
        product = ConcentrateProduct(client)
        assert preflight(product, client.list_tools()) == []
        a = product.ask("What is the Teams price per seat now?")
    assert a.text and a.abstained is not None
    assert a.cited_ids and all(c.startswith("not_") and ":" not in c for c in a.cited_ids)
    assert json.loads(a.raw)["answer_model"] == "claude-opus-5-5"
    assert a.cost_usd == json.loads(a.raw)["answer_cost_usd"]  # billed to our key: reported


def test_the_documented_example_spec_is_valid() -> None:
    from tenor.blackbox.products import load_spec

    spec = load_spec(str(Path(__file__).parent.parent / "docs/method/product.example.json"))
    assert spec.read_only_tools == [spec.tool]


# -- recorded answers ------------------------------------------------------------------------------


def test_product_system_records_once_then_replays(tmp_path: Path) -> None:
    inner = FakeMcp()
    ticks = iter([10.0, 14.0])
    live = GranolaProduct(inner)
    s = ProductSystem("granola", live, tmp_path / "g", clock=lambda: next(ticks), legacy_dirs=[])
    first = s.run(_q())
    assert first.latency_s == 4.0 and first.answer.citations == [NOTE] and first.cost_usd == 0
    replay = ProductSystem("granola", None, tmp_path / "g", legacy_dirs=[]).run(_q())
    assert replay.answer == first.answer and replay.latency_s == first.latency_s
    assert len(inner.calls) == 1 and s.recorded([_q(), _q("test-002")]) == 1
    with pytest.raises(RuntimeError, match="no recorded"):
        ProductSystem("granola", None, tmp_path / "g", legacy_dirs=[]).run(_q("test-002"))


def test_pre_blackbox_granola_recordings_still_replay(tmp_path: Path) -> None:
    q = _q("real-001", split="dev")
    legacy = tmp_path / "granola"
    s = ProductSystem("granola", None, tmp_path / "answers", legacy_dirs=[legacy])
    legacy.mkdir()
    rec = {"qid": q.id, "question": q.question, "raw": GRANOLA_RAW, "latency_s": 7.0}
    (legacy / s.record_path(q).name).write_text(json.dumps(rec))
    a = s.answer(q)
    assert a.text.startswith("Alex owns") and a.cited_ids == [NOTE] and a.latency_s == 7.0


# -- splits and the test-question importer ---------------------------------------------------------

TEMPLATE = """# Fresh TEST questions

Fill in one block per question:

```
Q: <question>
A: <gold answer, or UNANSWERABLE>
KEY: <key term 1>; <key term 2>
KIND: who_said | decision | unanswerable
```

## Your questions

Q: Who proposed moving the pilot to October?
A: Priya
KEY: Priya
KIND: who said

Q: What did we decide about the vendor shortlist?
A: Cut it to two vendors
KEY: two; vendors
KIND: decision

Q: What did the board say about the budget?
A: UNANSWERABLE
KEY:
KIND:

Q:
A:
KEY:
KIND:

---

## Already used (avoid these topics)

Q: An old dev question that must not be imported?
A: No
KIND: decision
"""


def test_import_test_parses_the_template() -> None:
    out = parse_test_markdown(TEMPLATE, CATEGORIES)
    assert [o["category"] for o in out] == ["who_said", "decision", "unanswerable"]
    assert out[0] == {
        "category": "who_said",
        "question": "Who proposed moving the pilot to October?",
        "gold_answer": "Priya",
        "key_terms": ["Priya"],
    }
    assert out[1]["key_terms"] == ["two", "vendors"]
    assert out[2]["gold_answer"] == "UNANSWERABLE" and out[2]["key_terms"] == []


@pytest.mark.parametrize(
    ("block", "msg"),
    [
        ("Q: Who?\nKIND: decision", "no A:"),
        ("Q: Who?\nA: Sam\nKIND: vibes", "not one of the kinds"),
        ("Q: Who?\nA: Sam\nKIND: unanswerable", "UNANSWERABLE"),
        ("Q: Who?\nA: Sam", "not one of the kinds"),
    ],
)
def test_import_test_rejects_malformed_blocks(block: str, msg: str) -> None:
    with pytest.raises(TemplateError, match=msg):
        parse_test_markdown(block, CATEGORIES)


def test_import_test_cli_writes_the_test_split_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tenor.cli import app

    monkeypatch.chdir(tmp_path)
    save_questions([_q("real-001", split="dev")], Path("data/real/questions.jsonl"))
    md = tmp_path / "data" / "real" / "test_questions.md"
    md.write_text(TEMPLATE)
    runner = CliRunner()
    for _ in range(2):  # re-importing is idempotent
        res = runner.invoke(app, ["real-kit", "import-test", str(md)])
        assert res.exit_code == 0, res.output
    test = load_split("test", Path("data/real"))
    assert [q.id for q in test] == ["test-001", "test-002", "test-003"]
    assert all(q.split == "test" and q.origin == "user" and q.status == "verified" for q in test)
    assert [q.id for q in load_split("dev", Path("data/real"))] == ["real-001"]
    assert "Priya" not in res.output  # counts only, no question text on screen


def test_a_split_file_holding_the_other_split_is_refused(tmp_path: Path) -> None:
    save_questions([_q("test-001", split="dev")], tmp_path / "questions.test.jsonl")
    with pytest.raises(ValueError, match="another split"):
        load_split("test", tmp_path)
    legacy = _q("real-001").model_dump(exclude={"split"})  # dev rows written before splits
    assert RealQuestion.model_validate(legacy).split == "dev"


# -- privacy: only aggregates are committable ------------------------------------------------------


def _run(tmp_path: Path) -> tuple[dict[str, Any], ProductSystem, list[RealQuestion]]:
    client = LLMClient(FakeJudge(), model="claude-opus-5-5", cache_dir=tmp_path / "cache")
    s = ProductSystem("granola", GranolaProduct(FakeMcp()), tmp_path / "g", legacy_dirs=[])
    qs = [_q(), _q("test-002", question="Which day is the launch review meeting on?")]
    report = run_slice(
        [s],  # type: ignore[list-item]
        qs,
        client,
        seed=42,
        budget_usd=1.0,
        out_dir=tmp_path / "r",
        split="test",
        meta={"products": ["granola"], "category_counts": {"commitment_status": 2}},
    )
    return report, s, qs


def test_committable_writer_accepts_aggregates_of_a_real_run(tmp_path: Path) -> None:
    report, s, qs = _run(tmp_path)
    private = [t for q in qs for t in (q.question, q.gold_answer)] + [s.answer(qs[0]).text]
    out = write_committable(report, tmp_path / "docs" / "agg.json", private)
    agg = json.loads(out.read_text())
    assert agg["meta"]["split"] == "test" and agg["metrics"]["granola"]["n"] == 2
    text = out.read_text()
    assert NOTE not in text and "launch" not in text.lower()
    # The private per-question report has what the aggregate lacks.
    assert "Who owns the launch" in Path(report["paths"]["jsonl"]).read_text()


@pytest.mark.parametrize(
    "leak",
    [
        {"meta": {"date": "x"}, "metrics": {}, "rows": [{"qid": "test-001"}]},
        {"meta": {"note": "Who owns the launch for the autumn pilot?"}, "metrics": {}},
        {"meta": {"worst": f"https://notes.granola.ai/d/{NOTE}"}, "metrics": {}},
        {"meta": {"worst": "not_AbCdEf123456"}, "metrics": {}},
        {"meta": {}, "metrics": {"granola": {"answer": "Alex"}}},
        {"meta": {}, "metrics": {"granola": {"by_category": {"x" * 200: {"n": 1}}}}},
    ],
    ids=["per-question rows", "question text", "granola url", "note id", "answer", "long text"],
)
def test_committable_check_refuses_per_question_content(leak: dict[str, Any]) -> None:
    with pytest.raises(NotCommittable):
        check_committable(leak, ["Who owns the launch for the autumn pilot?"])


def test_committable_writer_refuses_without_writing(tmp_path: Path) -> None:
    report, _, qs = _run(tmp_path)
    report["metrics"]["granola"]["worst_question"] = qs[0].question
    out = tmp_path / "agg.json"
    with pytest.raises(NotCommittable):
        write_committable(report, out, [qs[0].question])
    assert not out.exists()
