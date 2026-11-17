"""The retrieval tools as an MCP server (stdio), shaped like Granola's MCP.

Any MCP client (Claude Desktop, Claude Code, Cursor) can connect to it. Every call is appended
to a JSONL log (TENOR_RETRIEVAL_LOG) so the eval harness can see exactly what the agent read.
Over stdio, stdout is the protocol channel: nothing else may print to it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from tenor.retrieval.tools import WORDING, ToolBox

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


def build_server(box: ToolBox, wording: str = "v1", log_path: Path | None = None) -> MCPServer:
    """An MCP server over `box`, with tool descriptions from WORDING[wording]."""
    w = WORDING[wording]
    mcp: MCPServer = MCPServer(name="tenor-retrieval", instructions=w["server"], version="0.1.0")

    def logged(out: str) -> str:
        if log_path is not None:
            with log_path.open("a") as f:
                f.write(json.dumps(box.log[-1].model_dump()) + "\n")
        return out

    @mcp.tool(annotations=READ_ONLY, description=w["search_meetings"])
    def search_meetings(
        keywords: Annotated[
            str | None, Field(description="Words to look for; omit to list.")
        ] = None,
    ) -> str:
        return logged(box.search_meetings(keywords))

    @mcp.tool(annotations=READ_ONLY, description=w["search_transcripts"])
    def search_transcripts(
        keywords: Annotated[str, Field(description="Words to look for in what was said.")],
        meeting_ids: Annotated[
            list[str] | None, Field(description="Only these meetings; omit for all.")
        ] = None,
    ) -> str:
        return logged(box.search_transcripts(keywords, meeting_ids))

    @mcp.tool(annotations=READ_ONLY, description=w["get_transcript"])
    def get_transcript(
        meeting_id: Annotated[str, Field(description="A meeting id, e.g. ES2005b.")],
        chunk_ids: Annotated[list[int], Field(description="Chunk ids from a search result.")],
    ) -> str:
        return logged(box.get_transcript(meeting_id, chunk_ids))

    @mcp.tool(annotations=READ_ONLY, description=w["get_meetings"])
    def get_meetings(
        meeting_ids: Annotated[list[str], Field(description="Meeting ids, e.g. ['ES2005a'].")],
    ) -> str:
        return logged(box.get_meetings(meeting_ids))

    return mcp


def main() -> None:
    """Serve the meetings in TENOR_RETRIEVAL_MEETINGS (comma-separated AMI ids) over stdio."""
    from tenor.retrieval.loader import ami_toolbox

    ids = os.environ.get("TENOR_RETRIEVAL_MEETINGS", "ES2005a,ES2005b,ES2005c,ES2005d").split(",")
    log = os.environ.get("TENOR_RETRIEVAL_LOG")
    build_server(
        ami_toolbox(ids),
        os.environ.get("TENOR_RETRIEVAL_WORDING", "v1"),
        Path(log) if log else None,
    ).run("stdio")


if __name__ == "__main__":
    main()
