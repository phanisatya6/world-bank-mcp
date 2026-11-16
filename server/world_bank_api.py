"""Async client for the public World Bank Documents & Reports API.

Only HTTP/response concerns live here; MCP tool declarations belong in
``wb_docs_mcp.py``.  Every response is normalised into an LLM-friendly shape
while keeping the API's total/pagination metadata.

API reference: https://documents.worldbank.org/en/publication/documents-reports/api
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Final

import httpx

from server.schemas import DocumentFilter, FacetRequest, SearchRequest

LOGGER = logging.getLogger(__name__)

BASE_URL: Final[str] = "https://search.worldbank.org/api/v3/wds"
USER_AGENT: Final[str] = "world-bank-docs-mcp/1.0 (public-research-assistant)"
DEFAULT_TIMEOUT: Final[float] = 30.0
MAX_ATTEMPTS: Final[int] = 3

SUMMARY_FIELDS: Final[str] = ",".join(
    (
        "display_title",
        "docdt",
        "count",
        "admreg",
        "docty",
        "lang",
        "projn",
        "repnb",
        "abstracts",
        "pdfurl",
        "url",
        "guid",
    )
)

# Raw fields already mapped into the normalised summary.
_SUMMARY_RAW_KEYS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "display_title",
        "docdt",
        "count",
        "admreg",
        "docty",
        "lang",
        "projn",
        "repnb",
        "abstracts",
        "pdfurl",
        "url",
        "guid",
    }
)

# World Bank administrative regions.  "Sub-Saharan Africa" is a common user
# grouping that spans the two African admin regions, so expand it to the API's
# multi-value syntax (^).
REGION_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "sub saharan africa": ("Eastern and Southern Africa", "Western and Central Africa"),
    "subsaharan africa": ("Eastern and Southern Africa", "Western and Central Africa"),
    "ssa": ("Eastern and Southern Africa", "Western and Central Africa"),
    "africa": ("Eastern and Southern Africa", "Western and Central Africa"),
    "mena": ("Middle East and North Africa",),
    "middle east": ("Middle East and North Africa",),
    "lac": ("Latin America and Caribbean",),
    "latin america": ("Latin America and Caribbean",),
    "eap": ("East Asia and Pacific",),
    "east asia": ("East Asia and Pacific",),
    "eca": ("Europe and Central Asia",),
    "europe": ("Europe and Central Asia",),
    "sar": ("South Asia",),
}


class WorldBankAPIError(RuntimeError):
    """Raised when the World Bank API cannot satisfy a request."""

    def __init__(self, message: str, *, status_code: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


def _region_key(value: str) -> str:
    key = value.casefold().replace("&", " and ")
    key = re.sub(r"[^a-z ]", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def _expand_region(value: str) -> str:
    """Map friendly region names to a World Bank ``admreg_exact`` value."""
    alias = REGION_ALIASES.get(_region_key(value))
    if alias:
        return "^".join(alias)
    return value.strip()


def _looks_like_region(value: str) -> bool:
    return _region_key(value) in REGION_ALIASES


def _multi_value(value: str) -> str:
    """Allow ``A; B`` / ``A^B`` lists; leave commas inside country names alone."""
    parts = [part.strip() for part in re.split(r"[;^]", value) if part.strip()]
    return "^".join(parts) if parts else value.strip()


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _flatten(value: Any) -> str:
    """Turn the API's nested ``{"0": {...}}`` structures into readable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        if "cdata!" in value:
            return _flatten(value["cdata!"])
        parts = [_flatten(item) for item in value.values()]
        return "; ".join(part for part in parts if part)
    if isinstance(value, (list, tuple, set)):
        parts = [_flatten(item) for item in value]
        return "; ".join(part for part in parts if part)
    return str(value).strip()


def _https(url: str | None) -> str | None:
    if not url:
        return None
    url = str(url).strip()
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _normalise_date(value: Any) -> str | None:
    text = _flatten(value)
    if not text:
        return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else text


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _total(payload: dict[str, Any]) -> int:
    return _as_int(payload.get("total"), 0)


def _sort_params(sort: str, *, has_query: bool) -> dict[str, Any]:
    # WDS uses ``srt`` + ``order`` (its documentation example says ``sort``,
    # but the live API honours ``srt``).  Relevance is the default with qterm.
    if sort == "newest":
        return {"srt": "docdt", "order": "desc"}
    if sort == "oldest":
        return {"srt": "docdt", "order": "asc"}
    if not has_query:
        # The API already sorts by date descending when no qterm is supplied.
        return {}
    return {}


def _documents(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return document records, excluding the API's special ``facets`` key."""
    raw = payload.get("documents")
    if isinstance(raw, dict):
        return [
            value
            for key, value in raw.items()
            if key != "facets" and isinstance(value, dict)
        ]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _facet_bucket(payload: dict[str, Any], api_field: str) -> dict[str, Any]:
    raw = payload.get("documents")
    facets = raw.get("facets") if isinstance(raw, dict) else None
    if not isinstance(facets, dict):
        facets = payload.get("facets")
    if not isinstance(facets, dict):
        return {}
    bucket = facets.get(api_field)
    if isinstance(bucket, dict):
        return bucket
    # Some API responses return a single facet under an unexpected key.
    for value in facets.values():
        if isinstance(value, dict):
            return value
    return {}


def _facet_values(payload: dict[str, Any], api_field: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for key, item in _facet_bucket(payload, api_field).items():
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("label") or str(key)
        values.append({"value": str(name), "count": _as_int(item.get("count"), 0)})
    values.sort(key=lambda item: (-item["count"], item["value"].casefold()))
    return values


def normalise_document(raw: dict[str, Any], *, include_all: bool = False) -> dict[str, Any]:
    """Convert one raw WDS record into a stable, readable document summary."""
    title = (
        _flatten(raw.get("display_title"))
        or _flatten(raw.get("docna"))
        or _flatten(raw.get("repnme"))
        or "Untitled World Bank document"
    )
    abstract = _flatten(raw.get("abstracts"))
    guid = _flatten(raw.get("guid"))
    url = _https(_flatten(raw.get("url")))
    pdf_url = _https(_flatten(raw.get("pdfurl")))
    if not url and guid:
        url = f"https://documents.worldbank.org/curated/en/{guid}"

    document: dict[str, Any] = {
        "id": _flatten(raw.get("id")).lstrip("Dd"),
        "title": title,
        "date": _normalise_date(raw.get("docdt")) or _normalise_date(raw.get("disclosure_date")),
        "country": _flatten(raw.get("count")) or None,
        "region": _flatten(raw.get("admreg")) or None,
        "document_type": _flatten(raw.get("docty")) or None,
        "language": _flatten(raw.get("lang")) or None,
        "project": _flatten(raw.get("projn")) or None,
        "report_number": _flatten(raw.get("repnb")) or None,
        "abstract": _truncate(abstract, 500) if abstract else None,
        "url": url,
        "pdf_url": pdf_url,
    }

    if include_all:
        metadata: dict[str, Any] = {}
        for key, value in raw.items():
            if key in _SUMMARY_RAW_KEYS:
                continue
            text = _flatten(value)
            if text:
                metadata[key] = _truncate(text, 3000)
        document["metadata"] = metadata

    return document


def _search_payload(
    payload: dict[str, Any], *, offset: int, rows: int, sort: str = "relevance"
) -> dict[str, Any]:
    documents = _documents(payload)
    items = [normalise_document(document) for document in documents[:rows]]
    total = _total(payload)
    next_offset = offset + len(items)
    has_more = next_offset < total
    result: dict[str, Any] = {
        "total": total,
        "offset": offset,
        "rows": rows,
        "returned": len(items),
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
        "sort": sort,
        "documents": items,
    }
    if total == 0:
        result["message"] = (
            "No documents matched. Try broader keywords, remove filters, or use "
            "get_facets to discover valid country/language/document-type values."
        )
    return result


class WorldBankClient:
    """Small async wrapper around the WDS API.

    A fresh ``httpx.AsyncClient`` is used per request, so the wrapper is safe to
    hold as a module-level object without managing event-loop lifetimes.
    """

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        retry_backoff: float = 0.6,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.transport = transport
        self.max_attempts = max(1, max_attempts)
        self.retry_backoff = max(0.0, retry_backoff)

    async def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in params.items():
            if value is None or value == "":
                continue
            clean[key] = value.isoformat() if hasattr(value, "isoformat") else value
        clean["format"] = "json"

        last_error: WorldBankAPIError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    transport=self.transport,
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                ) as client:
                    response = await client.get(self.base_url, params=clean)
            except httpx.TimeoutException as exc:
                last_error = WorldBankAPIError(
                    f"World Bank API timed out after {self.timeout:g}s. Try again.",
                    url=self.base_url,
                )
                LOGGER.debug("WDS timeout: %s", exc)
            except httpx.HTTPError as exc:
                last_error = WorldBankAPIError(
                    f"Could not reach the World Bank API: {exc.__class__.__name__}: {exc}",
                    url=self.base_url,
                )
                LOGGER.debug("WDS transport error: %s", exc)
            else:
                status = response.status_code
                if status < 400:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise WorldBankAPIError(
                            "World Bank API returned a non-JSON response.",
                            status_code=status,
                            url=str(response.url),
                        ) from exc
                    if not isinstance(payload, dict):
                        raise WorldBankAPIError(
                            "World Bank API returned an unexpected JSON payload.",
                            status_code=status,
                            url=str(response.url),
                        )
                    return payload

                detail = re.sub(r"\s+", " ", response.text or "").strip()[:300]
                message = f"World Bank API returned HTTP {status}"
                if detail:
                    message += f": {detail}"
                last_error = WorldBankAPIError(
                    message, status_code=status, url=str(response.url)
                )
                # Retry rate limits and server errors only.
                if status != 429 and status < 500:
                    raise last_error

            if attempt < self.max_attempts:
                await asyncio.sleep(self.retry_backoff * (2 ** (attempt - 1)))

        raise last_error or WorldBankAPIError("World Bank API request failed.")

    async def search_documents(self, request: SearchRequest) -> dict[str, Any]:
        payload = await self._get(
            {
                "qterm": request.qterm,
                "lang_exact": request.language,
                "rows": request.rows,
                "os": request.offset,
                "fl": SUMMARY_FIELDS,
                **_sort_params(request.sort, has_query=True),
            }
        )
        return _search_payload(
            payload, offset=request.offset, rows=request.rows, sort=request.sort
        )

    async def filter_documents(self, request: DocumentFilter) -> dict[str, Any]:
        params: dict[str, Any] = {
            "rows": request.rows,
            "os": request.offset,
            "fl": SUMMARY_FIELDS,
            **_sort_params(request.sort, has_query=bool(request.qterm)),
        }
        filters_used: dict[str, Any] = {}
        notes: list[str] = []

        if request.qterm:
            params["qterm"] = request.qterm
            filters_used["qterm"] = request.qterm

        region = request.region
        if request.country and not region and _looks_like_region(request.country):
            region = request.country
            notes.append(
                f"Country '{request.country}' was interpreted as a World Bank region."
            )
        elif request.country and region and _looks_like_region(request.country):
            notes.append(
                f"Country '{request.country}' looks like a region; the explicit "
                f"region filter '{region}' took precedence."
            )
        elif request.country:
            params["count_exact"] = _multi_value(request.country)
            filters_used["count_exact"] = params["count_exact"]

        if region:
            params["admreg_exact"] = _expand_region(region)
            filters_used["admreg_exact"] = params["admreg_exact"]

        if request.document_type:
            params["docty_exact"] = request.document_type
            filters_used["docty_exact"] = request.document_type

        if request.start_date:
            params["strdate"] = request.start_date.isoformat()
            filters_used["strdate"] = request.start_date.isoformat()
        if request.end_date:
            params["enddate"] = request.end_date.isoformat()
            filters_used["enddate"] = request.end_date.isoformat()

        topic = request.topic
        if topic:
            params["topic_exact"] = topic
            filters_used["topic_exact"] = topic

        payload = await self._get(params)

        # The legacy ``topic_exact`` field is empty for most modern records.
        # Fall back to the legacy topic and then the sector classification so a
        # natural request such as topic="Education" still returns useful data.
        if topic and _total(payload) == 0:
            fallback = dict(params)
            fallback.pop("topic_exact", None)
            fallback["teratopic_exact"] = topic
            legacy_payload = await self._get(fallback)
            if _total(legacy_payload) > 0:
                payload = legacy_payload
                filters_used.pop("topic_exact", None)
                filters_used["teratopic_exact"] = topic
                notes.append(
                    "topic_exact returned no matches; retried with the legacy "
                    "topic field (teratopic_exact)."
                )
            else:
                fallback.pop("teratopic_exact", None)
                fallback["sectr_exact"] = topic
                sector_payload = await self._get(fallback)
                if _total(sector_payload) > 0:
                    payload = sector_payload
                    filters_used.pop("topic_exact", None)
                    filters_used["sectr_exact"] = topic
                    notes.append(
                        "topic_exact and teratopic_exact returned no matches; "
                        "retried with the sector field (sectr_exact)."
                    )
                else:
                    notes.append(
                        f"No matches for topic '{topic}' in topic, legacy-topic "
                        "or sector classifications with the other filters."
                    )

        result = _search_payload(
            payload, offset=request.offset, rows=request.rows, sort=request.sort
        )
        result["filters"] = filters_used
        if notes:
            result["notes"] = notes
        return result

    async def get_document(self, document_id: str) -> dict[str, Any]:
        payload = await self._get({"id": document_id})
        documents = _documents(payload)
        if not documents:
            # Document URLs contain the WDS ``guid`` rather than the record id;
            # accept that numeric value too so the LLM can follow a source link.
            payload = await self._get({"guid": document_id})
            documents = _documents(payload)
        if not documents:
            raise WorldBankAPIError(
                f"No World Bank document found with ID or GUID '{document_id}'. "
                "Use the numeric id from a search result (e.g. 33295056)."
            )
        return normalise_document(documents[0], include_all=True)

    async def get_facets(self, request: FacetRequest) -> dict[str, Any]:
        params: dict[str, Any] = {"fct": request.api_field, "rows": 0}
        if request.query:
            params["qterm"] = request.query

        payload = await self._get(params)
        used_field = request.api_field
        values = _facet_values(payload, used_field)
        notes: list[str] = []

        if not values and used_field == "topic_exact":
            legacy = dict(params)
            legacy["fct"] = "teratopic_exact"
            legacy_payload = await self._get(legacy)
            values = _facet_values(legacy_payload, "teratopic_exact")
            if values:
                used_field = "teratopic_exact"
                payload = legacy_payload
                notes.append(
                    "topic_exact is empty in the current API data; returned the "
                    "legacy topic facet (teratopic_exact)."
                )
            else:
                sector = dict(params)
                sector["fct"] = "sectr_exact"
                sector_payload = await self._get(sector)
                values = _facet_values(sector_payload, "sectr_exact")
                if values:
                    used_field = "sectr_exact"
                    payload = sector_payload
                    notes.append(
                        "topic_exact and teratopic_exact are empty; returned the "
                        "sector facet (sectr_exact) instead."
                    )

        if request.limit:
            returned_values = values[: request.limit]
        else:
            returned_values = values

        result: dict[str, Any] = {
            "field": request.field,
            "api_field": used_field,
            "query": request.query,
            "total_matching_documents": _total(payload),
            "total_values": len(values),
            "returned": len(returned_values),
            "has_more": len(returned_values) < len(values),
            "values": returned_values,
        }
        if not values:
            result["message"] = (
                f"The World Bank API returned no facet values for '{used_field}'. "
                "The field may be unpopulated; try another field or omit the query scope."
            )
        if notes:
            result["notes"] = notes
        return result
