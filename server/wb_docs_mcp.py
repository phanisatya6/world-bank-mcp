"""FastMCP server exposing the World Bank Documents & Reports API.

Run directly (``python server/wb_docs_mcp.py``) or as a module
(``python -m server.wb_docs_mcp``).  Transport is stdio, as required by the
interview task.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Allow both ``python server/wb_docs_mcp.py`` and ``python -m server.wb_docs_mcp``.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from server.schemas import (
    Country,
    DocumentFilter,
    DocumentId,
    DocumentIdRequest,
    DocumentType,
    EndDate,
    FacetField,
    FacetLimit,
    FacetQuery,
    FacetRequest,
    Language,
    Offset,
    OptionalQTerm,
    QTerm,
    Region,
    Rows,
    SearchRequest,
    SortOrder,
    StartDate,
    Topic,
)
from server.world_bank_api import WorldBankAPIError, WorldBankClient

mcp = FastMCP(
    "World Bank Documents",
    instructions=(
        "Search and retrieve real World Bank publications through the public "
        "Documents & Reports API. Prefer tool results over prior knowledge, "
        "cite document titles and dates, and never invent document IDs or URLs."
    ),
)

# Shared, stateless HTTP wrapper. Tests replace this object with a fake.
api = WorldBankClient()


def _validation_error(exc: ValidationError) -> ToolError:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"] if part != "body")
        prefix = f"{location}: " if location else ""
        parts.append(f"{prefix}{error['msg']}")
    return ToolError("Invalid input. " + "; ".join(parts))


@mcp.tool()
async def search_documents(
    qterm: QTerm,
    language: Language = None,
    rows: Rows = 10,
    offset: Offset = 0,
    sort: SortOrder = "relevance",
) -> dict[str, Any]:
    """Search World Bank publications using free-text keywords.

    Use this when the user asks a topical or keyword question without strict
    structured filters, for example "What reports exist on climate resilience in
    Kenya?" or "Find recent economic sector work on Indonesia?".  The query
    searches titles, abstracts, countries, project names and report numbers.

    Args:
        qterm: Keywords to search for. Include place names and topic words
            together for best results (e.g. "climate resilience Kenya").
        language: Optional exact language name (e.g. "English", "French").
            Use get_facets("language") to discover valid values.
        rows: Number of results per page (1-50, default 10).
        offset: Zero-based index of the first result. Pass next_offset from a
            previous response to fetch the next page.
        sort: Result order. Use "newest" when the user asks for the most recent
            reports; "oldest" for the earliest; "relevance" (default) for best
            keyword match.

    Returns:
        A dictionary with total match count, pagination fields (offset, rows,
        returned, has_more, next_offset) and a documents list.  Each document
        contains id, title, date, country, region, document_type, language,
        project, report_number, a short abstract, url and pdf_url.

    Use filter_documents instead when the request is primarily structured
    (country, region, topic, document type or date range).
    """
    try:
        request = SearchRequest(
            qterm=qterm, language=language, rows=rows, offset=offset, sort=sort
        )
        return await api.search_documents(request)
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    except WorldBankAPIError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def filter_documents(
    qterm: OptionalQTerm = None,
    country: Country = None,
    region: Region = None,
    topic: Topic = None,
    document_type: DocumentType = None,
    start_date: StartDate = None,
    end_date: EndDate = None,
    rows: Rows = 10,
    offset: Offset = 0,
    sort: SortOrder = "relevance",
) -> dict[str, Any]:
    """Find World Bank documents using structured filters.

    Use this for requests such as "education financing in Sub-Saharan Africa
    between 2019 and 2022", "water and sanitation project documents in Brazil",
    or "Project Appraisal Documents for Kenya since 2020".  Combine any of the
    filters below; at least one filter (or qterm) is required so huge unbounded
    result sets are never returned.

    Args:
        qterm: Optional keywords to combine with the structured filters.
        country: Exact country or regional grouping, e.g. "Kenya", "Brazil",
            "Sub-Saharan Africa".  Sub-Saharan Africa is expanded to the two
            World Bank African regions automatically.
        region: World Bank administrative region, e.g. "Eastern and Southern
            Africa", "Western and Central Africa", "South Asia".  Use
            get_facets("region") for the complete list.
        topic: Topic or sector, e.g. "Education", "Health", "Water Resources",
            "Climate Change".  The tool tries the legacy topic field, then the
            legacy topic facet, then the sector classification.
        document_type: Exact document type, e.g. "Project Appraisal Document",
            "Working Paper", "Report", "Brief".  Use
            get_facets("document_type") for valid values.
        start_date: Inclusive start date (YYYY-MM-DD preferred; a bare year such
            as "2019" is accepted).
        end_date: Inclusive end date (YYYY-MM-DD preferred; a bare year such as
            "2022" is accepted).
        rows: Number of results per page (1-50, default 10).
        offset: Zero-based result offset for pagination.
        sort: Result order. Use "newest" when the user asks for the most recent
            documents; "oldest" for the earliest; "relevance" (default) for
            best keyword match.

    Returns:
        The same pagination/document structure as search_documents, plus a
        "filters" dictionary showing the exact API parameters used and optional
        "notes" explaining any fallback (e.g. topic -> sector).
    """
    try:
        request = DocumentFilter(
            qterm=qterm,
            country=country,
            region=region,
            topic=topic,
            document_type=document_type,
            start_date=start_date,
            end_date=end_date,
            rows=rows,
            offset=offset,
            sort=sort,
        )
        return await api.filter_documents(request)
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    except WorldBankAPIError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def get_document(document_id: DocumentId) -> dict[str, Any]:
    """Retrieve the complete metadata record for one World Bank document.

    Use this after a search when the user asks for details about a specific
    document, or when a document ID is already known.  Document IDs are numeric
    World Bank IDs such as "33295056"; a leading "D" is accepted and ignored.
    The numeric GUID in a documents.worldbank.org URL is also accepted.

    Args:
        document_id: Numeric World Bank document ID from a previous tool result
            (or the numeric GUID from a document URL).

    Returns:
        The normalised summary fields (title, date, country, region, document
        type, language, project, abstract, URLs) plus a "metadata" dictionary
        containing all remaining fields returned by the API (authors, theme,
        sector, major document type, disclosure date, volume, etc.).

    Raises:
        An informative tool error when the ID is malformed or no document
        exists with that ID.
    """
    try:
        request = DocumentIdRequest(document_id=document_id)
        return await api.get_document(request.document_id)
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    except WorldBankAPIError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def get_facets(
    field: FacetField,
    query: FacetQuery = None,
    limit: FacetLimit = 0,
) -> dict[str, Any]:
    """List distinct values for a World Bank filter field (facet discovery).

    Use this for discovery questions such as "What languages does the World Bank
    publish in?", "What document types are most common?", or "What topics exist
    for climate documents?".  Facet values are the valid inputs for
    filter_documents and search_documents language filters.

    Args:
        field: Field to discover. Supported values: language, country,
            document_type, major_document_type, region, topic, theme, subtopic,
            sector, disclosure_status, collection, report_number.
        query: Optional keyword scope.  When provided, only values appearing in
            documents matching these keywords are returned (e.g. "climate Kenya").
        limit: Maximum values to return, sorted by document count.  0 (default)
            returns all values.

    Returns:
        A dictionary with field, api_field (the actual WDS facet used),
        total_matching_documents, total_values, returned, has_more and a values
        list of {"value", "count"} objects.  Values are sorted by frequency.
    """
    try:
        request = FacetRequest(field=field, query=query, limit=limit)
        return await api.get_facets(request)
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    except WorldBankAPIError as exc:
        raise ToolError(str(exc)) from exc


def main() -> None:
    """Start the MCP server over stdio."""
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
