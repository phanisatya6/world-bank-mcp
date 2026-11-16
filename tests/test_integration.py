"""Live integration tests.

Enabled explicitly to keep the default suite fast and offline:

    $env:RUN_WB_INTEGRATION="1"
    python -m pytest -m integration
"""

from __future__ import annotations

import os

import pytest

from client.agent import discover_mcp_tools, open_mcp_session
from server.schemas import DocumentFilter, FacetRequest, SearchRequest
from server.world_bank_api import WorldBankClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_WB_INTEGRATION") != "1",
        reason="set RUN_WB_INTEGRATION=1 to hit the live World Bank API",
    ),
]


async def test_live_search_filter_document_and_facets():
    client = WorldBankClient(timeout=60.0)

    search = await client.search_documents(
        SearchRequest(qterm="climate resilience Kenya", rows=3)
    )
    assert search["total"] > 0
    assert search["documents"]
    first = search["documents"][0]
    assert first["title"] and first["id"] and first["url"]

    filtered = await client.filter_documents(
        DocumentFilter(
            country="Sub-Saharan Africa",
            topic="Education",
            qterm="financing",
            start_date="2019",
            end_date="2022",
            rows=3,
        )
    )
    assert filtered["total"] > 0
    assert any("admreg_exact" in key for key in filtered["filters"])
    assert all(document["date"] for document in filtered["documents"])

    document = await client.get_document(first["id"])
    assert document["id"] == first["id"]
    assert document["metadata"]

    facets = await client.get_facets(FacetRequest(field="language"))
    assert facets["values"]
    assert any(value["value"] == "English" for value in facets["values"])

    document_types = await client.get_facets(
        FacetRequest(field="document_type", limit=5)
    )
    assert 1 <= len(document_types["values"]) <= 5


async def test_stdio_server_lists_and_calls_tools():
    async with open_mcp_session() as session:
        tools = await discover_mcp_tools(session)
        names = {tool["function"]["name"] for tool in tools}
        assert {"search_documents", "filter_documents", "get_document", "get_facets"} <= names

        result = await session.call_tool(
            "search_documents", {"qterm": "water sanitation Brazil", "rows": 2}
        )
        payload = getattr(result, "structured_content", None)
        if payload is None:
            payload = getattr(result, "structuredContent", None)
        assert payload, "expected structured content from the MCP tool"
        assert payload["returned"] <= 2
        assert payload["documents"]
