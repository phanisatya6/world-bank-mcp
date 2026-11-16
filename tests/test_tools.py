"""MCP tool registration and validation tests (in-memory, no live API)."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from server import wb_docs_mcp
from server.schemas import DocumentFilter, FacetRequest, SearchRequest
from server.world_bank_api import WorldBankAPIError


class FakeAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def search_documents(self, request: SearchRequest) -> dict[str, Any]:
        self.calls.append(("search_documents", request))
        return {
            "total": 1,
            "offset": request.offset,
            "rows": request.rows,
            "returned": 1,
            "has_more": False,
            "next_offset": None,
            "documents": [{"id": "1", "title": "Climate report"}],
        }

    async def filter_documents(self, request: DocumentFilter) -> dict[str, Any]:
        self.calls.append(("filter_documents", request))
        return {
            "total": 1,
            "offset": 0,
            "rows": request.rows,
            "returned": 1,
            "has_more": False,
            "next_offset": None,
            "documents": [{"id": "2", "title": "Education report"}],
            "filters": {"count_exact": "Kenya"},
        }

    async def get_document(self, document_id: str) -> dict[str, Any]:
        self.calls.append(("get_document", document_id))
        return {"id": document_id, "title": "A document", "metadata": {"theme": "Climate"}}

    async def get_facets(self, request: FacetRequest) -> dict[str, Any]:
        self.calls.append(("get_facets", request))
        return {
            "field": request.field,
            "api_field": request.api_field,
            "values": [{"value": "English", "count": 10}],
        }


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> FakeAPI:
    fake = FakeAPI()
    monkeypatch.setattr(wb_docs_mcp, "api", fake)
    return fake


async def _tools() -> dict[str, Any]:
    async with Client(wb_docs_mcp.mcp) as client:
        return {tool.name: tool for tool in await client.list_tools()}


async def test_all_required_tools_are_registered_with_descriptions():
    tools = await _tools()
    assert {"search_documents", "filter_documents", "get_document", "get_facets"} <= set(tools)
    for tool in tools.values():
        assert tool.description and len(tool.description) > 60

    search_schema = tools["search_documents"].input_schema
    assert search_schema["required"] == ["qterm"]
    assert search_schema["properties"]["rows"]["maximum"] == 50
    assert "keywords" in tools["search_documents"].description.lower()

    filter_schema = tools["filter_documents"].input_schema
    assert "start_date" in filter_schema["properties"]
    assert "document_type" in filter_schema["properties"]
    assert filter_schema.get("required", []) == []


async def test_search_tool_returns_fake_payload(fake_api: FakeAPI):
    async with Client(wb_docs_mcp.mcp) as client:
        result = await client.call_tool(
            "search_documents", {"qterm": "climate Kenya", "rows": 3}
        )
    payload = result.structured_content
    assert payload["documents"][0]["title"] == "Climate report"
    assert fake_api.calls[0][1].qterm == "climate Kenya"
    assert fake_api.calls[0][1].rows == 3


async def test_filter_tool_passes_validated_model(fake_api: FakeAPI):
    async with Client(wb_docs_mcp.mcp) as client:
        result = await client.call_tool(
            "filter_documents",
            {"country": "Kenya", "start_date": "2019", "end_date": "2022"},
        )
    assert result.structured_content["documents"][0]["id"] == "2"
    request: DocumentFilter = fake_api.calls[0][1]
    assert request.country == "Kenya"
    assert request.start_date.isoformat() == "2019-01-01"
    assert request.end_date.isoformat() == "2022-12-31"


async def test_get_document_tool(fake_api: FakeAPI):
    async with Client(wb_docs_mcp.mcp) as client:
        result = await client.call_tool("get_document", {"document_id": "D33295056"})
    assert result.structured_content["id"] == "33295056"
    assert fake_api.calls[0][1] == "33295056"


async def test_get_facets_tool_accepts_friendly_alias(fake_api: FakeAPI):
    async with Client(wb_docs_mcp.mcp) as client:
        result = await client.call_tool("get_facets", {"field": "languages"})
    assert result.structured_content["api_field"] == "lang_exact"
    assert fake_api.calls[0][1].field == "language"


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("search_documents", {"qterm": ""}, "at least 1 character"),
        ("search_documents", {"qterm": "climate", "rows": 500}, "less than or equal to 50"),
        ("filter_documents", {}, "at least one filter"),
        (
            "filter_documents",
            {"start_date": "2022-01-01", "end_date": "2019-01-01"},
            "on or before",
        ),
        ("filter_documents", {"start_date": "not-a-date"}, "valid date"),
        ("get_document", {"document_id": "ABC"}, "String should match pattern"),
        ("get_facets", {"field": "nonsense"}, "Unsupported facet field"),
    ],
)
async def test_invalid_inputs_return_informative_errors(
    tool: str, arguments: dict[str, Any], message: str
):
    async with Client(wb_docs_mcp.mcp) as client:
        with pytest.raises(ToolError, match=message):
            await client.call_tool(tool, arguments)


async def test_api_errors_are_wrapped_as_tool_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    class FailingAPI(FakeAPI):
        async def search_documents(self, request: SearchRequest) -> dict[str, Any]:
            raise WorldBankAPIError("World Bank API returned HTTP 503: unavailable")

    monkeypatch.setattr(wb_docs_mcp, "api", FailingAPI())
    async with Client(wb_docs_mcp.mcp) as client:
        with pytest.raises(ToolError, match="HTTP 503"):
            await client.call_tool("search_documents", {"qterm": "climate"})
