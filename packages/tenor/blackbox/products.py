"""The products the black-box track can ask: Granola's agent, Concentrate's server, and any
other meeting MCP described by a :class:`ProductSpec` (the template; see
docs/method/BLACKBOX.md, "Adding a product").

Each adapter calls exactly one question-answering tool and allowlists only that tool.
"""

from __future__ import annotations

import json
import re
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tenor.blackbox.base import McpProduct, Product, ProductAnswer, ToolNotAllowed, preflight
from tenor.sources.granola_mcp import McpToolError

# -- Granola's agent (query_granola_meetings) ------------------------------------------------------

_GRANOLA_CITATION = re.compile(r"notes\.granola\.ai/d/([A-Za-z0-9_-]+)")
_BOILERPLATE = re.compile(r"<transcript_note>.*?</transcript_note>", re.S)


def parse_granola_answer(raw: str) -> tuple[str, list[str]]:
    """(answer text without the standing transcript disclaimer, cited meeting IDs in order)."""
    text = _BOILERPLATE.sub("", raw).strip()
    cited: list[str] = []
    for m in _GRANOLA_CITATION.finditer(raw):
        if m.group(1) not in cited:
            cited.append(m.group(1))
    return text, cited


class GranolaProduct(McpProduct):
    """Granola's own agent, through its public MCP. Granola returns prose with markdown citation
    links and no abstain flag, so ``abstained`` is None (the judge reads the text)."""

    name = "granola"
    tool = "query_granola_meetings"
    read_only_tools = frozenset({"query_granola_meetings"})

    def arguments(self, question: str) -> dict[str, Any]:
        return {"query": question}

    def parse(self, raw: str, latency_s: float) -> ProductAnswer:
        text, cited = parse_granola_answer(raw)
        return ProductAnswer(text=text, cited_ids=cited, latency_s=latency_s, raw=raw)

    def ask_scoped(
        self,
        question: str,
        note_ids: list[str],
        *,
        sleep: Any = None,
        max_retries: int = 5,
    ) -> ProductAnswer:
        """Ask with the context limited to ``note_ids`` (the tool's ``document_ids``, VERIFIED
        in its schema). A rate-limit error carrying ``retry_after`` is waited out and retried;
        the latency excludes the waits."""
        import time

        wait = sleep or time.sleep
        args = {"query": question, "document_ids": list(note_ids)}
        for attempt in range(max_retries + 1):
            start = self.clock()
            try:
                raw = self.caller.call(self.tool, args)
            except McpToolError as exc:
                if exc.retry_after is None or attempt == max_retries:
                    raise
                wait(exc.retry_after)
                continue
            return self.parse(raw, self.clock() - start)
        raise AssertionError("unreachable")  # pragma: no cover


# -- Concentrate's own MCP server (ask_memory) -----------------------------------------------------


class ConcentrateProduct(McpProduct):
    """Concentrate's MCP server (``tenor real-kit serve``), via ``ask_memory`` with
    ``answer=true`` so the server writes the answer, as Granola's agent does. Citations are line
    IDs (``not_x:0042``); ``cited_ids`` reduces them to meeting IDs, in order."""

    name = "concentrate"
    tool = "ask_memory"
    read_only_tools = frozenset({"ask_memory"})

    def arguments(self, question: str) -> dict[str, Any]:
        return {"question": question, "answer": True}

    def parse(self, raw: str, latency_s: float) -> ProductAnswer:
        out = json.loads(raw)
        if "answer" not in out:
            raise McpToolError(self.tool, out.get("answer_error", "no answer in the response"))
        a = out["answer"]
        cited: list[str] = []
        for c in a.get("citations", []):
            note = str(c).strip("[] ").split(":")[0]
            if note and note not in cited:
                cited.append(note)
        return ProductAnswer(
            text=a["answer"],
            cited_ids=cited,
            latency_s=latency_s,
            raw=raw,
            abstained=bool(a.get("abstain")),
            cost_usd=float(out.get("answer_cost_usd", 0.0)),
        )


# -- any other meeting MCP (the template) ----------------------------------------------------------


class ProductSpec(BaseModel):
    """A meeting MCP described in JSON, so adding a product needs no code. See
    ``docs/method/product.example.json`` and the checklist in docs/method/BLACKBOX.md."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    transport: Literal["stdio", "http"]
    command: str | None = None  # stdio: executable
    args: list[str] = Field(default_factory=list)  # stdio: its arguments
    env: list[str] = Field(default_factory=list)  # stdio: env var NAMES to pass through
    url: str | None = None  # http: Streamable HTTP endpoint (no auth; see BLACKBOX.md)
    tool: str  # the one question-answering tool
    question_arg: str = "query"  # the argument the question goes in
    extra_args: dict[str, Any] = Field(default_factory=dict)  # fixed extra arguments
    answer_path: str | None = None  # JSON response: dotted path to the answer text
    citation_path: str | None = None  # JSON response: dotted path to a list of cited IDs
    citation_regex: str | None = None  # text response: regex whose group 1 is a cited ID
    read_only_tools: list[str]  # MUST contain ``tool``; each one checked by hand (checklist)

    @model_validator(mode="after")
    def _check(self) -> ProductSpec:
        if self.tool not in self.read_only_tools:
            raise ValueError(f"tool {self.tool!r} must be listed in read_only_tools")
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio products need a command")
        if self.transport == "http" and not self.url:
            raise ValueError("http products need a url")
        return self


def _dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


class SpecProduct(McpProduct):
    """A product built from a :class:`ProductSpec`."""

    def __init__(self, spec: ProductSpec, caller: Any, **kw: Any) -> None:
        self.spec = spec
        self.name = spec.name
        self.tool = spec.tool
        self.read_only_tools = frozenset(spec.read_only_tools)
        super().__init__(caller, **kw)

    def arguments(self, question: str) -> dict[str, Any]:
        return {**self.spec.extra_args, self.spec.question_arg: question}

    def parse(self, raw: str, latency_s: float) -> ProductAnswer:
        s = self.spec
        cited: list[str] = []
        if s.answer_path or s.citation_path:
            data = json.loads(raw)
            text = str(_dig(data, s.answer_path)) if s.answer_path else raw
            if s.citation_path:
                cited = [str(c) for c in _dig(data, s.citation_path)]
        else:
            text = raw
        if s.citation_regex:
            cited += [m.group(1) for m in re.finditer(s.citation_regex, raw)]
        return ProductAnswer(
            text=text.strip(), cited_ids=list(dict.fromkeys(cited)), latency_s=latency_s, raw=raw
        )


# -- opening a product by name ---------------------------------------------------------------------

BUILT_IN = ("granola", "concentrate")
SPEC_DIR = Path("data/real/products")  # private: a spec may name an account-specific URL


def load_spec(name_or_path: str) -> ProductSpec:
    """A spec from a path, or ``data/real/products/<name>.json``."""
    p = Path(name_or_path)
    if p.suffix != ".json":
        p = SPEC_DIR / f"{name_or_path}.json"
    if not p.exists():
        raise FileNotFoundError(f"no product {name_or_path!r}: built-in {BUILT_IN}, or a spec {p}")
    return ProductSpec.model_validate_json(p.read_text())


def product_name(name_or_path: str) -> str:
    """The name answers are recorded under, without opening any connection."""
    if name_or_path in BUILT_IN:
        return name_or_path
    return load_spec(name_or_path).name


def open_product(name_or_path: str, stack: ExitStack) -> Product:
    """Connect to a product; connections close with ``stack``. Live: may open a browser
    (Granola sign-in) or start a subprocess (stdio servers)."""
    import os

    from tenor.blackbox.transport import SyncMcpClient

    if name_or_path == "granola":
        from tenor.sources.mcp_transport import LiveMcpCaller

        return GranolaProduct(stack.enter_context(LiveMcpCaller()))
    if name_or_path == "concentrate":
        serve = ["-c", "from tenor.cli import app; app()", "real-kit", "serve"]
        env = {k: v for k, v in os.environ.items() if k in ("ANTHROPIC_API_KEY", "PATH", "HOME")}
        client = stack.enter_context(SyncMcpClient.stdio(sys.executable, serve, env))
        return _checked(ConcentrateProduct(client), client)
    spec = load_spec(name_or_path)
    if spec.transport == "stdio":
        env = {k: os.environ[k] for k in spec.env if k in os.environ}
        client = SyncMcpClient.stdio(spec.command or "", spec.args, env or None)
    else:
        client = SyncMcpClient(spec.url)
    return _checked(SpecProduct(spec, stack.enter_context(client)), client)


def _checked(product: McpProduct, client: Any) -> McpProduct:
    """Refuse to use a product whose server contradicts its allowlist (see ``preflight``)."""
    problems = preflight(product, client.list_tools())
    if problems:
        raise ToolNotAllowed(f"{product.name}: " + "; ".join(problems))
    return product
