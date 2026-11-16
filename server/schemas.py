"""Pydantic request models used by the World Bank MCP tools.

The models own input normalisation and cross-field validation.  FastMCP also
reads the ``Annotated[...]`` constraints placed on the tool signatures, so the
generated JSON Schema shown to the LLM carries the same limits.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

MAX_ROWS: Final[int] = 50
MAX_OFFSET: Final[int] = 100_000

# Friendly name -> World Bank Documents API facet field.
FACET_FIELDS: Final[dict[str, str]] = {
    "language": "lang_exact",
    "country": "count_exact",
    "document_type": "docty_exact",
    "major_document_type": "majdocty_exact",
    "region": "admreg_exact",
    "topic": "topic_exact",
    "theme": "theme_exact",
    "subtopic": "subtopic_exact",
    "sector": "sectr_exact",
    "disclosure_status": "disclstat_exact",
    "collection": "colti_exact",
    "report_number": "repnb",
}

_FACET_ALIASES: Final[dict[str, str]] = {
    "lang": "language",
    "languages": "language",
    "lang_exact": "language",
    "count": "country",
    "countries": "country",
    "count_exact": "country",
    "docty": "document_type",
    "doctypes": "document_type",
    "doc_type": "document_type",
    "document_types": "document_type",
    "docty_exact": "document_type",
    "majdocty": "major_document_type",
    "major_document_types": "major_document_type",
    "majdocty_exact": "major_document_type",
    "regions": "region",
    "admreg": "region",
    "admreg_exact": "region",
    "topics": "topic",
    "topic_exact": "topic",
    "legacy_topic": "topic",
    "teratopic": "topic",
    "teratopic_exact": "topic",
    "themes": "theme",
    "theme_exact": "theme",
    "subtopics": "subtopic",
    "subtopic_exact": "subtopic",
    "sectors": "sector",
    "sectr": "sector",
    "sectr_exact": "sector",
    "disclosure": "disclosure_status",
    "disclstat": "disclosure_status",
    "disclstat_exact": "disclosure_status",
    "collections": "collection",
    "series": "collection",
    "colti": "collection",
    "colti_exact": "collection",
    "report_numbers": "report_number",
    "repnb": "report_number",
    "repnb_exact": "report_number",
}

SUPPORTED_FACETS: Final[str] = ", ".join(FACET_FIELDS)


def resolve_facet_field(value: str) -> str:
    """Return the canonical friendly facet name, or raise ``ValueError``."""
    key = re.sub(r"[\s-]+", "_", value.strip().lower())
    key = re.sub(r"[^a-z0-9_]", "", key)
    key = _FACET_ALIASES.get(key, key)
    if key not in FACET_FIELDS:
        raise ValueError(
            f"Unsupported facet field '{value}'. Supported fields: {SUPPORTED_FACETS}."
        )
    return key


def _parse_flexible_date(value: object, field_name: str) -> object:
    if value is None or isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}", text):
        # A bare year is inclusive: 2019 -> 2019-01-01 / 2019-12-31.
        return f"{text}-01-01" if field_name == "start_date" else f"{text}-12-31"
    for fmt in ("%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return value  # Let Pydantic produce the final ISO-date error.


# Reusable annotated parameter types. Field descriptions end up in the MCP
# schema, which is the main signal the LLM uses for tool selection.
QTerm = Annotated[
    str,
    Field(
        min_length=1,
        max_length=300,
        description="Free-text keywords, e.g. 'climate resilience Kenya'. Searches titles, abstracts, countries and project names.",
    ),
]
OptionalQTerm = Annotated[
    str | None,
    Field(
        default=None,
        max_length=300,
        description="Optional free-text keywords to combine with the structured filters.",
    ),
]
Language = Annotated[
    str | None,
    Field(
        default=None,
        max_length=50,
        description="Exact language name, e.g. 'English', 'French', 'Spanish'. Use get_facets('language') for valid values.",
    ),
]
Rows = Annotated[
    int,
    Field(
        default=10,
        ge=1,
        le=MAX_ROWS,
        description=f"Results per page (1-{MAX_ROWS}).",
    ),
]
Offset = Annotated[
    int,
    Field(
        default=0,
        ge=0,
        le=MAX_OFFSET,
        description="Zero-based offset of the first result. Use next_offset from a previous response to page.",
    ),
]
SortOrder = Annotated[
    Literal["relevance", "newest", "oldest"],
    Field(
        default="relevance",
        description="Result order: 'relevance' for best keyword match, 'newest' for most recent publication date first, or 'oldest'.",
    ),
]
Country = Annotated[
    str | None,
    Field(
        default=None,
        max_length=100,
        description="Exact country or regional grouping, e.g. 'Kenya', 'Brazil', 'Sub-Saharan Africa'. Regions are mapped automatically.",
    ),
]
Region = Annotated[
    str | None,
    Field(
        default=None,
        max_length=100,
        description="World Bank administrative region, e.g. 'Eastern and Southern Africa', 'South Asia'. Use get_facets('region') for values.",
    ),
]
Topic = Annotated[
    str | None,
    Field(
        default=None,
        max_length=100,
        description="Topic or sector keyword, e.g. 'Education', 'Health', 'Climate Change', 'Water Resources'.",
    ),
]
DocumentType = Annotated[
    str | None,
    Field(
        default=None,
        max_length=100,
        description="Exact World Bank document type, e.g. 'Project Appraisal Document', 'Working Paper', 'Report'. Use get_facets('document_type').",
    ),
]
StartDate = Annotated[
    str | None,
    Field(
        default=None,
        max_length=20,
        description="Inclusive start date. Prefer YYYY-MM-DD; a bare year such as '2019' also works.",
    ),
]
EndDate = Annotated[
    str | None,
    Field(
        default=None,
        max_length=20,
        description="Inclusive end date. Prefer YYYY-MM-DD; a bare year such as '2022' also works.",
    ),
]
FacetField = Annotated[
    str,
    Field(
        min_length=1,
        max_length=60,
        description=(
            "Field to discover. One of: language, country, document_type, "
            "major_document_type, region, topic, theme, subtopic, sector, "
            "disclosure_status, collection, report_number."
        ),
    ),
]
FacetQuery = Annotated[
    str | None,
    Field(
        default=None,
        max_length=300,
        description="Optional keyword scope; only facet values occurring in documents matching these keywords are returned.",
    ),
]
FacetLimit = Annotated[
    int,
    Field(
        default=0,
        ge=0,
        le=1000,
        description="Maximum facet values to return, sorted by document count (0 = all values).",
    ),
]
DocumentId = Annotated[
    str,
    Field(
        min_length=3,
        max_length=30,
        pattern=r"^[Dd]?\d{3,}$",
        description="Numeric World Bank document ID (e.g. '33295056'), or the numeric GUID from a document URL. A leading 'D' is accepted.",
    ),
]


class SearchRequest(BaseModel):
    """Validated arguments for ``search_documents``."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    qterm: str = Field(min_length=1, max_length=300)
    language: str | None = Field(default=None, max_length=50)
    rows: int = Field(default=10, ge=1, le=MAX_ROWS)
    offset: int = Field(default=0, ge=0, le=MAX_OFFSET)
    sort: Literal["relevance", "newest", "oldest"] = "relevance"

    @field_validator("qterm")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("qterm must contain search keywords")
        return value

    @field_validator("language")
    @classmethod
    def _normalise_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value.lower() in {"", "any", "all", "none"}:
            return None
        return value


class DocumentFilter(BaseModel):
    """Validated arguments for ``filter_documents``."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    qterm: str | None = Field(default=None, max_length=300)
    country: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    topic: str | None = Field(default=None, max_length=100)
    document_type: str | None = Field(default=None, max_length=100)
    start_date: date | None = None
    end_date: date | None = None
    rows: int = Field(default=10, ge=1, le=MAX_ROWS)
    offset: int = Field(default=0, ge=0, le=MAX_OFFSET)
    sort: Literal["relevance", "newest", "oldest"] = "relevance"

    @field_validator("qterm", "country", "region", "topic", "document_type")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _flexible_date(cls, value: object, info: ValidationInfo) -> object:
        return _parse_flexible_date(value, info.field_name or "")

    @model_validator(mode="after")
    def _validate_range_and_scope(self) -> "DocumentFilter":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        has_filter = any(
            (
                self.qterm,
                self.country,
                self.region,
                self.topic,
                self.document_type,
                self.start_date,
                self.end_date,
            )
        )
        if not has_filter:
            raise ValueError(
                "Provide at least one filter (qterm, country, region, topic, "
                "document_type, start_date or end_date)."
            )
        return self


class DocumentIdRequest(BaseModel):
    """Validated/normalised document ID."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(min_length=3, max_length=30, pattern=r"^[Dd]?\d{3,}$")

    @field_validator("document_id")
    @classmethod
    def _strip_api_prefix(cls, value: str) -> str:
        return value[1:] if value[:1].lower() == "d" else value


class FacetRequest(BaseModel):
    """Validated arguments for ``get_facets``."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    field: str = Field(min_length=1, max_length=60)
    query: str | None = Field(default=None, max_length=300)
    limit: int = Field(default=0, ge=0, le=1000)

    @field_validator("field")
    @classmethod
    def _resolve_field(cls, value: str) -> str:
        return resolve_facet_field(value)

    @field_validator("query")
    @classmethod
    def _blank_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @property
    def api_field(self) -> str:
        return FACET_FIELDS[self.field]
