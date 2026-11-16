"""Manual smoke test: exercise all four MCP tools over the real stdio transport.

Run from the project root:

    python scripts/smoke_test.py

This does not need a Groq key.  It is useful for checking the server before
connecting the LLM, and for capturing real tool output for the README.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from client.agent import open_mcp_session  # noqa: E402


async def main() -> int:
    async with open_mcp_session() as session:
        tools = (await session.list_tools()).tools
        print("Discovered tools:", ", ".join(tool.name for tool in tools))

        cases = [
            (
                "search_documents",
                {"qterm": "climate resilience Kenya", "rows": 3},
            ),
            (
                "filter_documents",
                {
                    "country": "Sub-Saharan Africa",
                    "topic": "Education",
                    "qterm": "financing",
                    "start_date": "2019",
                    "end_date": "2022",
                    "rows": 3,
                },
            ),
            ("get_facets", {"field": "language", "limit": 10}),
            ("get_facets", {"field": "document_type", "limit": 8}),
        ]

        for name, arguments in cases:
            print(f"\n=== {name} {json.dumps(arguments)} ===")
            result = await session.call_tool(name, arguments)
            if getattr(result, "isError", False) or getattr(result, "is_error", False):
                print("ERROR:", result.content)
                continue
            payload = getattr(result, "structured_content", None)
            if payload is None:
                payload = getattr(result, "structuredContent", None)
            print(json.dumps(payload, indent=2, ensure_ascii=False)[:6000])

        # get_document using a real ID from the first search.
        search = await session.call_tool(
            "search_documents", {"qterm": "climate resilience Kenya", "rows": 1}
        )
        payload = getattr(search, "structured_content", None) or getattr(
            search, "structuredContent", None
        )
        document_id = payload["documents"][0]["id"]
        print(f"\n=== get_document {document_id} ===")
        result = await session.call_tool("get_document", {"document_id": document_id})
        payload = getattr(result, "structured_content", None) or getattr(
            result, "structuredContent", None
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False)[:6000])

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
