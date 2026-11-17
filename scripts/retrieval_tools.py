"""Call one retrieval tool from the command line, for Claude Code agents (each call is logged).

  uv run python scripts/retrieval_tools.py --meetings ES2005a,ES2005b --log calls.jsonl \\
      search_transcripts '{"keywords": "battery cost"}'
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tenor.retrieval.loader import ami_toolbox

TOOLS = ("search_meetings", "search_transcripts", "get_transcript", "get_meetings")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--meetings", required=True, help="comma-separated AMI meeting ids")
    p.add_argument("--log", type=Path, required=True, help="JSONL file each call is appended to")
    p.add_argument("tool", choices=TOOLS)
    p.add_argument("args", nargs="?", default="{}", help="tool arguments as a JSON object")
    a = p.parse_args()
    box = ami_toolbox(a.meetings.split(","))
    out = getattr(box, a.tool)(**json.loads(a.args))
    with a.log.open("a") as f:
        f.write(json.dumps(box.log[-1].model_dump()) + "\n")
    print(out)


if __name__ == "__main__":
    main()
