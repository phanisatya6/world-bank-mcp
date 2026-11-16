"""Unit tests for the World Bank API wrapper (no live network)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from server.schemas import DocumentFilter, FacetRequest, SearchRequest
from server.world_bank_api import (
    WorldBankAPIError,
    WorldBankClient,
    normalise_document,
)


def _doc(doc_id: str = "1", **overrides: Any) -> dict[str, Any]:
    document = {
        "id": doc_id,
        "display_title": "Climate Resilience in Kenya",
        "docdt": "2021-07-01T04:00:00Z",
        "count": "Kenya",
        "admreg": "Eastern and Southern Africa",
        "docty": "Report",
        "lang": "English",
        "projn": "KE-Climate Project -- P145559",
        "repnb": "SFG5535",
        "abstracts": {"cdata!": "A project supporting climate resilience."},
        "pdfurl": "http://documents.worldbank.org/curated/en/1/pdf/report.pdf",
        "url": "http://documents.worldbank.org/curated/en/1",
        "guid": "1",
        "majdocty": "Sector Work",
    }
    document.update(overrides)
    return document


def _client(handler, **kwargs: Any) -> WorldBankClient:
    return WorldBankClient(
        transport=httpx.MockTransport(handler),
        max_attempts=kwargs.pop("max_attempts", 3),
        retry_backoff=kwargs.pop("retry_backoff", 0.0),
        **kwargs,
    )


async def test_search_normalises_documents_and_pagination():
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "total": 2,
                "documents": {"D1": _doc(), "facets": {}},
            },
        )

    result = await _client(handler).search_documents(
        SearchRequest(qterm="climate Kenya", language="English", rows=1, offset=0)
    )

    assert seen[0]["qterm"] == "climate Kenya"
    assert seen[0]["lang_exact"] == "English"
    assert seen[0]["rows"] == "1"
    assert seen[0]["os"] == "0"
    assert seen[0]["format"] == "json"
    assert result["total"] == 2
    assert result["returned"] == 1
    assert result["has_more"] is True
    assert result["next_offset"] == 1

    document = result["documents"][0]
    assert document["id"] == "1"
    assert document["title"] == "Climate Resilience in Kenya"
    assert document["date"] == "2021-07-01"
    assert document["url"].startswith("https://")
    assert document["pdf_url"].startswith("https://")
    assert document["abstract"] == "A project supporting climate resilience."


async def test_newest_sort_uses_srt_docdt_desc():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"total": 0, "documents": {"facets": {}}})

    result = await _client(handler).search_documents(
        SearchRequest(qterm="Indonesia economic", sort="newest")
    )
    assert seen["srt"] == "docdt"
    assert seen["order"] == "desc"
    assert result["sort"] == "newest"


async def test_empty_search_has_helpful_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 0, "documents": {"facets": {}}})

    result = await _client(handler).search_documents(SearchRequest(qterm="nothing"))
    assert result["documents"] == []
    assert result["has_more"] is False
    assert "No documents matched" in result["message"]


async def test_filter_expands_sub_saharan_africa_and_years():
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"total": 1, "documents": {"D1": _doc()}})

    result = await _client(handler).filter_documents(
        DocumentFilter(
            country="Sub-Saharan Africa",
            topic="Education",
            start_date="2019",
            end_date="2022",
            rows=5,
        )
    )

    assert seen[0]["admreg_exact"] == (
        "Eastern and Southern Africa^Western and Central Africa"
    )
    assert seen[0]["strdate"] == "2019-01-01"
    assert seen[0]["enddate"] == "2022-12-31"
    assert seen[0]["topic_exact"] == "Education"
    assert "count_exact" not in seen[0]
    assert result["filters"]["admreg_exact"].startswith("Eastern and Southern Africa")
    assert any("interpreted as a World Bank region" in note for note in result["notes"])


async def test_filter_topic_falls_back_to_legacy_topic():
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        if "topic_exact" in params:
            return httpx.Response(200, json={"total": 0, "documents": {"facets": {}}})
        if "teratopic_exact" in params:
            return httpx.Response(200, json={"total": 1, "documents": {"D1": _doc()}})
        return httpx.Response(500, text="unexpected")

    result = await _client(handler).filter_documents(
        DocumentFilter(topic="Education", rows=3)
    )

    assert len(calls) == 2
    assert result["total"] == 1
    assert result["filters"]["teratopic_exact"] == "Education"
    assert "topic_exact" not in result["filters"]
    assert any("legacy topic" in note for note in result["notes"])


async def test_filter_topic_falls_back_to_sector():
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if "sectr_exact" in params:
            return httpx.Response(200, json={"total": 1, "documents": {"D1": _doc()}})
        return httpx.Response(200, json={"total": 0, "documents": {"facets": {}}})

    result = await _client(handler).filter_documents(DocumentFilter(topic="Education"))
    assert result["total"] == 1
    assert result["filters"]["sectr_exact"] == "Education"
    assert any("sector field" in note for note in result["notes"])


async def test_multiple_countries_use_caret_separator():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"total": 1, "documents": {"D1": _doc()}})

    await _client(handler).filter_documents(DocumentFilter(country="Kenya; Uganda"))
    assert seen["count_exact"] == "Kenya^Uganda"


async def test_get_document_strips_prefix_and_includes_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["id"] == "33295056"
        return httpx.Response(
            200,
            json={
                "total": 1,
                "documents": {
                    "D33295056": _doc("33295056", majdocty="Sector Work", theme="Climate")
                },
            },
        )

    document = await _client(handler).get_document("33295056")
    assert document["id"] == "33295056"
    assert document["metadata"]["majdocty"] == "Sector Work"
    assert document["metadata"]["theme"] == "Climate"
    assert "display_title" not in document["metadata"]


async def test_get_document_accepts_url_guid_as_fallback():
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        if "guid" in params:
            return httpx.Response(200, json={"total": 1, "documents": {"D1": _doc()}})
        return httpx.Response(200, json={"total": 0, "documents": {"facets": {}}})

    document = await _client(handler).get_document("633061627382758577")
    assert document["id"] == "1"
    assert seen[0]["id"] == "633061627382758577"
    assert seen[1]["guid"] == "633061627382758577"


async def test_get_document_not_found_raises_informative_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 0, "documents": {"facets": {}}})

    with pytest.raises(WorldBankAPIError, match="No World Bank document found"):
        await _client(handler).get_document("999999")


async def test_facets_are_sorted_and_limited():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["fct"] == "lang_exact"
        assert request.url.params["rows"] == "0"
        return httpx.Response(
            200,
            json={
                "total": 10,
                "documents": {
                    "facets": {
                        "lang_exact": {
                            "0": {"count": 5, "name": "French"},
                            "1": {"count": 10, "name": "English"},
                        }
                    }
                },
            },
        )

    result = await _client(handler).get_facets(FacetRequest(field="languages", limit=1))
    assert result["field"] == "language"
    assert result["api_field"] == "lang_exact"
    assert result["values"] == [{"value": "English", "count": 10}]
    assert result["total_values"] == 2
    assert result["has_more"] is True


async def test_topic_facet_falls_back_to_legacy_topic():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["fct"] == "teratopic_exact":
            return httpx.Response(
                200,
                json={
                    "total": 10,
                    "documents": {
                        "facets": {
                            "teratopic_exact": {
                                "0": {"count": 3, "name": "Water Resources"}
                            }
                        }
                    },
                },
            )
        return httpx.Response(200, json={"total": 10, "documents": {"facets": {}}})

    result = await _client(handler).get_facets(FacetRequest(field="topic"))
    assert result["api_field"] == "teratopic_exact"
    assert result["values"][0]["value"] == "Water Resources"
    assert any("legacy topic" in note for note in result["notes"])


async def test_http_500_retries_then_fails():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, text="internal error")

    with pytest.raises(WorldBankAPIError) as exc_info:
        await _client(handler).search_documents(SearchRequest(qterm="climate"))
    assert attempts == 3
    assert exc_info.value.status_code == 500
    assert "HTTP 500" in str(exc_info.value)


async def test_http_429_retries_then_succeeds():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json={"total": 0, "documents": {"facets": {}}})

    result = await _client(handler).search_documents(SearchRequest(qterm="climate"))
    assert attempts == 2
    assert result["total"] == 0


async def test_non_json_response_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(WorldBankAPIError, match="non-JSON"):
        await _client(handler).search_documents(SearchRequest(qterm="climate"))


def test_normalise_document_handles_nested_api_values():
    document = normalise_document(
        {
            "id": "D42",
            "display_title": "Title",
            "docdt": "2020-01-02",
            "authors": {"0": {"author": "Alice"}, "1": {"author": "Bob"}},
            "count": "Kenya",
        },
        include_all=True,
    )
    assert document["id"] == "42"
    assert document["metadata"]["authors"] == "Alice; Bob"


def test_facet_request_rejects_unknown_field():
    with pytest.raises(ValueError, match="Unsupported facet field"):
        FacetRequest(field="not_a_field")


def test_document_filter_rejects_empty_scope():
    with pytest.raises(ValueError, match="at least one filter"):
        DocumentFilter()


def test_document_filter_rejects_reversed_dates():
    with pytest.raises(ValueError, match="on or before"):
        DocumentFilter(start_date="2022-01-01", end_date="2019-01-01")
