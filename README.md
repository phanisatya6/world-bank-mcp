# World Bank Documents MCP Server + Groq Agent

A local [MCP](https://modelcontextprotocol.io) server that exposes the public
[World Bank Documents & Reports API](https://documents.worldbank.org/en/publication/documents-reports/api),
plus a CLI agent that uses a free LLM (Groq or Google Gemini) to answer
natural-language questions grounded in real World Bank publications.

The deliverable covers the full loop required by the task:

```
question -> LLM picks tools -> MCP server calls the World Bank API
         -> results returned to the LLM -> cited final answer
```

No World Bank authentication, database, frontend, or deployment is involved.

---

## Contents

- [What is included](#what-is-included)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Setup](#setup)
- [Configuration](#configuration)
- [LLM choice](#llm-choice)
- [Running the agent](#running-the-agent)
- [MCP tools](#mcp-tools)
- [How the agent loop works](#how-the-agent-loop-works)
- [Data handling and API fallbacks](#data-handling-and-api-fallbacks)
- [Error handling and limits](#error-handling-and-limits)
- [Example questions and outputs](#example-questions-and-outputs)
- [Tests](#tests)
- [Project layout](#project-layout)
- [Design notes and limitations](#design-notes-and-limitations)

---

## What is included

| Path | Purpose |
| --- | --- |
| `server/wb_docs_mcp.py` | FastMCP server with the four required tools, stdio transport |
| `server/world_bank_api.py` | Async HTTP client, response normalisation, pagination, sorting, retries, filter fallbacks |
| `server/schemas.py` | Pydantic request models, annotated tool parameter types, date parsing, facet aliases |
| `client/agent.py` | MCP stdio client, MCP-to-Groq schema conversion, bounded tool-calling loop, CLI |
| `client/groq_client.py` | `.env` loading and provider setup (Groq or Google Gemini) |
| `tests/` | Offline unit tests plus opt-in live integration tests |
| `scripts/smoke_test.py` | Manual live smoke test of the MCP tools without an LLM key |
| `requirements.txt` | Pinned dependency ranges |
| `.env.example` | Environment variable template |
| `docs/presentation/` | Slide deck (PPTX + PDF) explaining the project, with captured terminal outputs |

---

## Architecture

```
                 +--------------------------------------+
                 |  client/agent.py (CLI / REPL)        |
                 |                                      |
  user question  |  1. start server subprocess (stdio)  |
  ------------>  |  2. list_tools -> Groq functions     |
                 |  3. chat.completions(tools=...)      |
                 |  4. execute tool_calls via MCP       |
                 |  5. feed results back, repeat        |
                 +------------------+-------------------+
                                    |  MCP over stdio
                                    v
                 +--------------------------------------+
                 |  server/wb_docs_mcp.py (FastMCP)     |
                 |  search_documents                    |
                 |  filter_documents                    |
                 |  get_document                        |
                 |  get_facets                          |
                 +------------------+-------------------+
                                    |  HTTPS / JSON
                                    v
                 +--------------------------------------+
                 |  server/world_bank_api.py            |
                 |  httpx, retries, normalisation       |
                 +------------------+-------------------+
                                    |
                                    v
        https://search.worldbank.org/api/v3/wds
```

The client launches the server itself with
`python -m server.wb_docs_mcp`, so the server does not need to be started
manually for normal use. The server subprocess deliberately does **not** inherit
`GROQ_API_KEY`; only the client needs it.

---

## Requirements

- Python **3.12+**
- One free LLM API key:
  - Groq: <https://console.groq.com/keys> (default), or
  - Google Gemini: <https://aistudio.google.com/apikey>
- Internet access to `search.worldbank.org`
- Optional: Node.js if you want to use the MCP Inspector

The project was developed and tested with:

- Python 3.12
- FastMCP 4.2
- MCP Python SDK 2.3
- Groq Python SDK 1.7
- OpenAI Python SDK 2.54 (used for Gemini's OpenAI-compatible endpoint)
- HTTPX 0.28
- Pydantic 2.14

---

## Setup

### Windows (PowerShell)

```powershell
cd world-bank-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

### macOS / Linux

```bash
cd world-bank-mcp
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and set the key for the provider you want to use.

Groq (default):

```dotenv
GROQ_API_KEY=gsk_your_key_here
```

Google Gemini:

```dotenv
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_key_here
```

Then verify the setup in two steps.

1. Confirm the MCP server starts and advertises its tools. This does **not**
   call the LLM and does not need an API key:

```bash
python -m client.agent --list-tools
```

Expected tool names:

```
search_documents
filter_documents
get_document
get_facets
```

2. Confirm the LLM key and model work. This sends one tiny request to the
   configured provider and does not start the MCP server:

```bash
python -m client.agent --check-llm
```

Expected output:

```
Provider: Gemini
Model: gemini-2.5-flash
Response: OK
LLM connection OK.
```

---

## Configuration

All settings are optional except the API key for your chosen provider.

| Variable | Default | Description |
| --- | --- | --- |
| `LLM_PROVIDER` | auto | `groq` or `gemini`. If unset, Gemini is used when only `GEMINI_API_KEY` is present; otherwise Groq. |
| `GROQ_API_KEY` | none | Free Groq key. Required for the Groq provider. |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model used for tool calling (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`, ...). |
| `GROQ_BASE_URL` | none | Optional OpenAI-compatible endpoint for the Groq SDK path (for example Ollama at `http://localhost:11434/v1`). When set, a Groq key is not required by this client. |
| `GEMINI_API_KEY` | none | Free Google AI Studio key. Required for the Gemini provider. |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model used for tool calling (for example `gemini-3.6-flash`). |
| `GEMINI_BASE_URL` | Gemini OpenAI endpoint | Override only if you proxy Gemini yourself. |
| `AGENT_MAX_ITERATIONS` | `6` | Maximum LLM/tool rounds before a forced final answer. Clamped to 1-20. |
| `AGENT_TEMPERATURE` | `0.1` | Sampling temperature. Low value keeps answers factual. |
| `AGENT_MAX_TOKENS` | `2048` | Maximum tokens in each model response. |
| `MAX_CONTEXT_TOKENS` | `6000` | Token budget for the whole conversation (estimated as characters / 4). Older tool results are trimmed before each request to stay under it. |
| `MAX_TOOL_RESULT_CHARS` | `12000` | A single tool result larger than this is compacted/truncated before being sent back to the model. |
| `MAX_ABSTRACT_CHARS` | `320` | Document abstracts are truncated to this length in tool results sent to the model. |

`.env` is loaded from the project root by `client/groq_client.py`. Real
environment variables take precedence over `.env` values.

### Staying within free-tier rate limits

Free Groq and Gemini tiers cap **tokens per minute** (the Groq free tier allows
8,000 TPM per model). The client is built to stay inside that budget:

- Document results are slimmed before being sent back to the model (short
  abstracts, no embedded facet blocks).
- The whole conversation is kept under `MAX_CONTEXT_TOKENS`; older tool results
  are replaced with a one-line note while the most recent result is preserved.
- If the provider still returns `413` (request too large) or `429` (rate limit),
  the agent trims the context, waits for the provider's `Retry-After` when given,
  and retries up to three times before reporting an informative error.

If you see repeated rate-limit retries, lower `MAX_CONTEXT_TOKENS` or ask for
fewer results (`rows`).

---

## LLM choice

**Default backend: Groq, model `openai/gpt-oss-120b`. Optional backend:
Google Gemini through its OpenAI-compatible endpoint.**

Both providers are free to start and support native tool calling. The client
talks to each through its OpenAI-compatible `chat.completions` API, so the agent
loop, tool schemas, and message history are identical.

### Why Groq by default

- **Free and fast.** Groq's free tier is sufficient for this task and the
  low-latency inference keeps a multi-round tool loop responsive.
- **Native function/tool calling.** The task requires a model that can emit
  structured tool calls; `openai/gpt-oss-120b` supports the OpenAI-compatible
  `tools` API used here. `openai/gpt-oss-20b` is a faster, lighter alternative
  and `qwen/qwen3.8-27b` also works.
- **Good enough for tool selection.** The four tools have explicit,
  LLM-oriented descriptions and narrow schemas, so a general-purpose open model
  reliably chooses between search, structured filtering, facet discovery, and
  document lookup.
- **Small and cheap to run repeatedly** during development and evaluation.

### Using Google Gemini instead

Gemini exposes an OpenAI-compatible endpoint at
`https://generativelanguage.googleapis.com/v1beta/openai/`. Add a free Google AI
Studio key and select the provider:

```dotenv
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_key_here
GEMINI_MODEL=gemini-2.5-flash
```

The client detects the provider automatically when only `GEMINI_API_KEY` is set,
so `LLM_PROVIDER` is optional. Any Gemini model that supports function calling
can be used, for example `gemini-2.5-flash`, `gemini-2.5-flash-lite`,
`gemini-3.6-flash`, or the `gemini-flash-latest` alias.

### Other OpenAI-compatible endpoints

The Groq SDK path accepts any OpenAI-compatible base URL. A fully offline setup
can use Ollama with a tool-capable model:

```dotenv
LLM_PROVIDER=groq
GROQ_BASE_URL=http://localhost:11434/v1
GROQ_MODEL=qwen2.5:3b
# GROQ_API_KEY can stay empty for a local server
```

No client code changes are required. If no usable key or base URL is
configured, the client exits with a clear configuration error.

The exact model can be overridden per run:

```bash
python -m client.agent --model llama-3.3-70b-versatile "What is the newest report on Indonesia?"
```

---

## Running the agent

### One-shot question

```bash
python -m client.agent "What World Bank reports exist on climate resilience in Kenya?"
```

The final answer is printed to **stdout**. Diagnostics, such as tool progress
when `--show-tools` is used, go to **stderr**.

### Interactive mode

```bash
python -m client.agent --interactive
```

```
World Bank Documents agent. Type a question, or 'exit' to quit.

Question> Find project documents about water and sanitation in Brazil.
...
Question> exit
```

The MCP subprocess and Groq client stay open between questions. Each question
starts a fresh conversation, so answers are not contaminated by earlier turns.

### Inspect tool calls

```bash
python -m client.agent --show-tools "What document types are most common?"
```

Example stderr trace:

```
[tool] search_documents({"qterm": "document types", "rows": 5})
[tool] search_documents -> ok (7421 chars)
```

### Other CLI options

```bash
python -m client.agent --list-tools                 # print discovered schemas
python -m client.agent --check-llm                  # verify the provider key/model
python -m client.agent --show-tools "..."           # trace tool calls
python -m client.agent --max-iterations 8 "..."     # change the loop bound
python -m client.agent --model <model-id> "..."     # override the provider model
```

### Run the server directly

For MCP Inspector or another MCP client:

```bash
python -m server.wb_docs_mcp
```

The server speaks MCP over stdio. It is not an HTTP server, so running it in a
terminal will simply wait for JSON-RPC messages on stdin.

With the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector python -m server.wb_docs_mcp
```

### Smoke test without Groq

```bash
python scripts/smoke_test.py
```

This starts the real stdio server and calls all four tools against the live
World Bank API. It requires no LLM key.

---

## MCP tools

All parameters are validated with Pydantic. Invalid values are rejected before
an HTTP request is made, and the error message names the offending field.

### `search_documents`

Full-text search with optional language filtering, pagination, and sorting.

| Parameter | Type | Default | Notes |
| --- | --- | --- | --- |
| `qterm` | string, 1-300 chars | required | Keywords across titles, abstracts, countries, project names, report numbers |
| `language` | string or null | `null` | Exact language name, e.g. `English`; discover with `get_facets("language")` |
| `rows` | integer 1-50 | `10` | Results per page |
| `offset` | integer >= 0 | `0` | Zero-based offset; use `next_offset` from a previous response |
| `sort` | `relevance` / `newest` / `oldest` | `relevance` | `newest` sorts by publication date descending |

Returns `total`, `offset`, `rows`, `returned`, `has_more`, `next_offset`, `sort`,
and a `documents` list.

### `filter_documents`

Structured filtering by country, region, topic, document type, and/or date
range. At least one filter (or `qterm`) is required, so the tool never returns an
unbounded result set.

| Parameter | Type | Default | Notes |
| --- | --- | --- | --- |
| `qterm` | string or null | `null` | Optional keywords combined with the filters |
| `country` | string or null | `null` | Exact country or regional grouping, e.g. `Kenya`, `Sub-Saharan Africa` |
| `region` | string or null | `null` | World Bank region, e.g. `South Asia`; discover with `get_facets("region")` |
| `topic` | string or null | `null` | Topic or sector, e.g. `Education`, `Health`, `Water Resources` |
| `document_type` | string or null | `null` | Exact type, e.g. `Project Appraisal Document` |
| `start_date` | date or null | `null` | Inclusive. `YYYY-MM-DD` preferred; a bare year such as `2019` is accepted |
| `end_date` | date or null | `null` | Inclusive. A bare year such as `2022` expands to `2022-12-31` |
| `rows` | integer 1-50 | `10` | Results per page |
| `offset` | integer >= 0 | `0` | Pagination offset |
| `sort` | `relevance` / `newest` / `oldest` | `relevance` | Date sorting when requested |

Returns the same pagination/document structure as `search_documents`, plus:

- `filters`: the exact World Bank API parameters that were used.
- `notes`: human-readable explanations when a fallback was applied.

Date order is validated locally: `start_date > end_date` is an error.

### `get_document`

Retrieves full metadata for one document.

| Parameter | Type | Notes |
| --- | --- | --- |
| `document_id` | string | Numeric World Bank ID such as `33295056`. A leading `D` is accepted. The numeric GUID in a `documents.worldbank.org` URL also works. |

Returns the normalised summary fields plus a `metadata` dictionary with the
remaining API fields (authors, theme, sector, major document type, disclosure
date, volume, and so on). Unknown IDs produce an informative tool error rather
than an empty object.

### `get_facets`

Discovers distinct values for a filter field.

| Parameter | Type | Default | Notes |
| --- | --- | --- | --- |
| `field` | string | required | `language`, `country`, `document_type`, `major_document_type`, `region`, `topic`, `theme`, `subtopic`, `sector`, `disclosure_status`, `collection`, or `report_number` |
| `query` | string or null | `null` | Optional keyword scope, e.g. `climate Kenya` |
| `limit` | integer 0-1000 | `0` | Maximum values, sorted by document count. `0` returns all values the API provides |

Returns `field`, `api_field` (the actual WDS facet used),
`total_matching_documents`, `total_values`, `returned`, `has_more`, and a
`values` list of `{"value", "count"}` objects.

The four tools are intentionally named and described for an LLM, not for a human
API wrapper. Each docstring states when to use the tool, what every argument
means, and what the result contains.

---

## How the agent loop works

`client/agent.py` implements the required MCP client behaviour:

1. **Start the MCP server as a subprocess.** `open_mcp_session()` uses
   `mcp.client.stdio.stdio_client` with `sys.executable -m server.wb_docs_mcp`,
   sets the project root on `PYTHONPATH`, and initialises a `ClientSession`.
2. **Discover tools dynamically.** `session.list_tools()` is called at runtime;
   no tool names or schemas are hard-coded in the client.
3. **Convert MCP schemas to Groq functions.** Each MCP tool becomes
   `{"type": "function", "function": {"name", "description", "parameters"}}`.
   `anyOf: [X, null]` optional fields are flattened because small models handle
   the simpler schema more reliably.
4. **Send the conversation to the LLM provider** (Groq or Gemini) with
   `tools=...` and `tool_choice="auto"`.
5. **Preserve assistant tool-call messages.** The assistant message is appended
   with its `tool_calls` array exactly as returned, including each call `id`.
6. **Execute every requested tool** through the MCP session. Arguments are
   parsed from the model's JSON string into a dict. Tool errors are converted to
   JSON and fed back to the model instead of crashing the loop.
7. **Return each result by `tool_call_id`.** Every tool result is a
   `{"role": "tool", "tool_call_id": ..., "content": ...}` message. The `name`
   field is intentionally omitted, matching the current Groq/OpenAI tool
   protocol.
8. **Repeat** until the model returns a message with no tool calls, or the
   iteration limit is reached.
9. **Bound the loop.** After `AGENT_MAX_ITERATIONS` rounds, one final
   completion is requested with `tools` disabled and an explicit instruction to
   answer from the results already gathered. This prevents infinite tool loops.

Oversized tool results are compacted before being sent back: document abstracts
are truncated, embedded facet blocks are dropped, strings are shortened, and
lists are capped. The final serialised result is truncated to
`MAX_TOOL_RESULT_CHARS` while remaining valid JSON, and the whole conversation is
kept under `MAX_CONTEXT_TOKENS` by replacing older tool results with a short
note (see *Staying within free-tier rate limits* above).

The system prompt reinforces grounding: use tool results only, never invent
titles/IDs/URLs, use structured filters for structured questions, use
`sort="newest"` for "latest" questions, use facets before exact filtering, and
cite title, date, country, and URL.

---

## Data handling and API fallbacks

The live WDS API has several quirks that the server handles so the LLM does not
have to:

- **`documents` is an object, not a list.** It also contains a `facets` key.
  The normaliser filters out `facets` before iterating.
- **Modern topic fields are empty.** `topic_exact` often returns zero results
  for current records. `filter_documents` therefore retries in order with
  `topic_exact`, then the legacy `teratopic_exact`, then `sectr_exact`, and
  reports the fallback in `notes`.
- **Region names are not country names.** `country="Sub-Saharan Africa"` is
  expanded to `admreg_exact=Eastern and Southern Africa^Western and Central Africa`.
  Other region-like country values are also detected and mapped.
- **Document URLs use GUIDs, not IDs.** `get_document` first looks up the
  numeric WDS `id`; if that finds nothing, it retries with `guid`. Both a tool
  result ID and the number in a document URL work.
- **ISO dates are required by the API.** Compact dates such as `20240101` can
  produce an HTTP 500. The schema accepts `YYYY-MM-DD`, common slash/dash
  variants, and bare years, then sends ISO dates to WDS.
- **Sorting needs explicit parameters.** WDS honours `srt=docdt&order=desc|asc`
  for date sorting. Without a query, the API already tends to return the newest
  records first, but the tool makes the intent explicit via `sort`.

Normalised document fields include: `id`, `title`, `date`, `country`, `region`,
`document_type`, `language`, `project`, `report_number`, a short `abstract`,
`url`, and `pdf_url`.

---

## Error handling and limits

| Situation | Behaviour |
| --- | --- |
| Invalid tool arguments | Pydantic validation runs before the HTTP call; the MCP tool raises a `ToolError` naming the field and problem. |
| Malformed JSON from the model | The loop reports the parse error back to the model as a tool result, so it can retry with valid arguments. |
| World Bank 4xx | Returned immediately as an informative error (status, short response detail, URL). |
| World Bank 429 / 5xx | Retried up to 3 times with exponential backoff (`0.6s`, then `1.2s`). |
| Timeout / network failure | Clear "timed out" or "could not reach" message; retried with the same backoff. |
| Non-JSON response | Reported as an unexpected API response instead of a JSON decode traceback. |
| Zero results | Not an error. The agent is instructed to broaden keywords or relax filters and to say when nothing matches. |
| Unknown document ID | `get_document` raises a tool error explaining that no document was found. |
| Missing provider API key | The client prints a configuration error and exits with status 2. |
| Provider 401/403 | Explains that the credentials were rejected and points to `.env`. |
| Provider 413 | Explains that the request exceeded the per-minute budget and that older tool results are trimmed automatically. |
| Provider 429 | Explains the rate limit, trims the context, and retries after the provider's `Retry-After` delay. |
| Tool-call limit reached | One final answer is requested without tools; the run fails only if that answer is empty. |

Input bounds: `rows` is 1-50, `offset` is capped at 100,000, `qterm` is at most
300 characters, and facet `limit` is 0-1000. These limits keep individual tool
results small enough for a small-context, small-model loop.

---

## Example questions and outputs

> **Data note:** the tool results below were captured from the live World Bank
> API. The answers are written in the agent's required style; with a Groq or
> Gemini key in `.env` the same questions produce the same grounding data with
> model-written prose. The *Other suggested questions verified live* section
> below includes runs captured verbatim with Groq `openai/gpt-oss-120b`.

### 1. Climate resilience reports in Kenya

**Question**

```bash
python -m client.agent "What World Bank reports exist on climate resilience in Kenya?"
```

**Tool call**

```json
{
  "name": "search_documents",
  "arguments": {"qterm": "climate resilience Kenya", "rows": 5}
}
```

**Tool result (summary)**

```text
total: 962
returned: 5
documents:
  33295056 | 2021-07-01 | Resettlement Plan | Kenya
  ...      | 2015-07-29 | Auditing Document | Kenya
  ...      | 2021-02-01 | Resettlement Plan | Kenya
  ...      | 2020-12-15 | Project Paper | Kenya,World
  ...      | 2015-12-02 | Implementation Status and Results Report | Kenya
```

**Answer**

The World Bank API returns **962 documents** for climate resilience work in
Kenya. The most relevant results are:

1. **Kenya - Coastal Region Water Security and Climate Resilience Project:
   Resettlement Plan (Vol. 4 of 4): Resettlement Action Plan 2 for Mwache
   Multipurpose Dam Project** (Resettlement Plan, July 2021).
   <https://documents.worldbank.org/curated/en/633061627382758577>
2. **Kenya - Water Security and Climate Resilience Project: audited financial
   statement - year ending June 30, 2015** (Auditing Document, July 2015).
   <https://documents.worldbank.org/curated/en/948341438174804516>
3. **Kenya - Coastal Region Water Security and Climate Resilience Project:
   Resettlement Plan (Vol. 3 of 4)** (Resettlement Plan, February 2021).
   <https://documents.worldbank.org/curated/en/533081616664378297>
4. **Disclosable Restructuring Paper - Kenya Water Security and Climate
   Resilience Project - P117635** (Project Paper, December 2020).
   <https://documents.worldbank.org/curated/en/595061608021784702>

These are the top results among 962 matches; more pages are available with `rows`
and `next_offset`.

### 2. Education financing in Sub-Saharan Africa, 2019-2022

**Question**

```bash
python -m client.agent "Find documents about education financing in Sub-Saharan Africa published between 2019 and 2022."
```

**Tool call**

```json
{
  "name": "filter_documents",
  "arguments": {
    "country": "Sub-Saharan Africa",
    "topic": "Education",
    "qterm": "financing",
    "start_date": "2019",
    "end_date": "2022",
    "rows": 5
  }
}
```

**Tool result (summary)**

```text
total: 4148
filters: {
  "qterm": "financing",
  "admreg_exact": "Eastern and Southern Africa^Western and Central Africa",
  "strdate": "2019-01-01",
  "enddate": "2022-12-31",
  "teratopic_exact": "Education"
}
notes:
  - Country 'Sub-Saharan Africa' was interpreted as a World Bank region.
  - topic_exact returned no matches; retried with the legacy topic field (teratopic_exact).
```

**Answer**

There are **4,148 documents** matching education financing in Sub-Saharan
Africa between 2019 and 2022. The top results include:

1. **Ethiopia - Education Results Based Financing Project** (Implementation
   Completion and Results Report, 27 December 2019).
   <https://documents.worldbank.org/curated/en/278011579096308073>
2. **Mozambique - Can Results-Based Financing Ensure that Books Reach Students
   in Time for the Start of the School Year?** (Brief, April 2021).
   <https://documents.worldbank.org/curated/en/964841619414972572>
3. **South Africa - Results-Based Financing and the Book Supply Chain**
   (Brief, December 2019).
   <https://documents.worldbank.org/curated/en/330561577181324343>
4. Two Implementation Status and Results Reports for the **Ethiopia Education
   Results Based Financing Project (P163608)**, both from 2019.
   <https://documents.worldbank.org/curated/en/426241554676766545>
   <https://documents.worldbank.org/curated/en/874871561749593107>

"Sub-Saharan Africa" was mapped to the World Bank's Eastern and Southern Africa
and Western and Central Africa regions, and the legacy topic field was used
because the current `topic_exact` field returned no matches for modern records.

### 3. Most frequent document types

**Question**

```bash
python -m client.agent "What document types does the World Bank publish most frequently?"
```

**Tool call**

```json
{
  "name": "get_facets",
  "arguments": {"field": "document_type", "limit": 5}
}
```

**Tool result (summary)**

```text
total_matching_documents: 623114
total_values: 100
values:
  Procurement Plan                          144,407
  Implementation Status and Results Report   59,904
  Auditing Document                          30,490
  Working Paper                              27,069
  Brief                                      24,818
```

**Answer**

Across **623,114 documents** in the World Bank Documents & Reports API, the most
frequently published document types are:

| Rank | Document type | Documents |
| --- | --- | ---: |
| 1 | Procurement Plan | 144,407 |
| 2 | Implementation Status and Results Report | 59,904 |
| 3 | Auditing Document | 30,490 |
| 4 | Working Paper | 27,069 |
| 5 | Brief | 24,818 |

Together, Procurement Plans and Implementation Status and Results Reports make
up roughly one third of the collection. These counts come from the API's
`document_type` facet, which reports 100 distinct values in total.

### Other suggested questions verified live

| Question | Tool path | Verified result |
| --- | --- | --- |
| Find the most recent economic sector work on Indonesia. | `search_documents(qterm="economic sector work Indonesia", sort="newest")` | 2,483 matches; newest is *Indonesia - Country Private Sector Diagnostic* (5 October 2026), <https://documents.worldbank.org/curated/en/099753310052628862> |
| List all languages the World Bank publishes documents in. | `get_facets(field="language", limit=30)` | English 578,402; French 16,611; Spanish 10,408; Arabic 3,025; Portuguese 2,913; ... (100 distinct values) |
| Find project documents related to water and sanitation in Brazil. | `filter_documents(country="Brazil", qterm="water sanitation")` | 3,909 matches; top result *All Drops in the Bucket for Universalization: Public Expenditure Review of Water and Sanitation in Brazil* (March 2022), <https://documents.worldbank.org/curated/en/363231625052894706> |

### Verbatim runs with `openai/gpt-oss-120b`

Two complete agent runs captured against the live Groq API and the live World
Bank API (tool calls shown, then the model's answer).

**Q: What languages does the World Bank publish in? List the top few.**

Tool path: `get_facets(field="language", limit=5)`.

```text
The World Bank publishes its documents primarily in the following languages (top 5 by document count):

| Rank | Language   | Approx. number of documents |
|------|------------|----------------------------|
| 1    | English    | 578,410                    |
| 2    | French     | 16,611                     |
| 3    | Spanish    | 10,408                     |
| 4    | Arabic     | 3,025                      |
| 5    | Portuguese | 2,913                      |

Source: World Bank Documents & Reports API facet query for "language" (top 5 values) - total matching documents = 623,122.
```

**Q: Show the most recent World Bank reports on education in Sub-Saharan Africa.**

Tool path: `filter_documents(country="Sub-Saharan Africa", topic="Education", document_type="Report", sort="newest")` (with a search fallback).

```text
Most recent World Bank Reports on Education in Sub-Saharan Africa (newest first)

 1. Final Evaluation of the Transparency of the Mauritanian Education Budget (TOME) Project - August 2022 - Mauritania (Western and Central Africa) - https://documents.worldbank.org/curated/en/352401647610255802
 2. Understanding the Socio-Economic Conditions of Urban Refugees in Kenya (2020-2021 Urban Socio-economic Survey) - May 2022 - Kenya (Eastern and Southern Africa) - https://documents.worldbank.org/curated/en/184981636366231985
 3. Final Evaluation of the Transparency of the Mauritanian Education Budget (TOME) Project (French version) - February 2022 - Mauritania (Western and Central Africa) - https://documents.worldbank.org/curated/en/254021647610543043
 4. l'Emploi des Jeunes dans les Chaines de Valeur au Burkina Faso - November 2021 - Burkina Faso (Western and Central Africa) - https://documents.worldbank.org/curated/en/616721636445034078
 5. Cote d'Ivoire - Sustaining High, Inclusive, and Resilient Growth Post COVID-19 - October 2021 - Cote d'Ivoire (Western and Central Africa) - https://documents.worldbank.org/curated/en/877591635251242567
 ... (10 shown in total)

Summary: the query returned 208 matching reports; the list shows the 10 most recent, sorted by newest publication date. More results are available (the API indicates additional pages).
```

> Outputs vary slightly between runs (temperature 0.1) and may include a short
> rate-limit retry notice on the free tier, as described in
> *Staying within free-tier rate limits*.

---

## Tests

The offline suite mocks HTTP, the MCP session, and the LLM, so it runs without a
network connection or API key:

```bash
python -m pytest
```

Result at delivery time:

```text
66 passed, 2 skipped
```

The two skipped tests are the live integration tests. They exercise the real
World Bank API and a real stdio server subprocess:

```powershell
# Windows PowerShell
$env:RUN_WB_INTEGRATION = "1"
python -m pytest -m integration
```

```bash
# macOS / Linux
RUN_WB_INTEGRATION=1 python -m pytest -m integration
```

Result at delivery time:

```text
2 passed
```

Coverage includes:

- `tests/test_api.py` - URL parameter construction, normalisation, facets,
  sorting, retries, timeouts, error messages, GUID fallback, topic fallback.
- `tests/test_tools.py` - in-memory FastMCP `Client` calls, Pydantic validation,
  pagination metadata, facet aliases, document lookup errors.
- `tests/test_agent.py` - MCP-to-Groq schema conversion, `anyOf` flattening,
  `additionalProperties` removal, argument parsing, multi-round tool loops,
  assistant `tool_calls` retention, `tool_call_id` correlation, tool errors,
  truncation, iteration bounds, token-budget trimming, and 413/429 retries.
- `tests/test_llm_config.py` - provider auto-detection, explicit provider
  selection, model defaults/overrides, and configuration errors for Groq and
  Gemini.
- `tests/test_integration.py` - live WDS API and real stdio server startup
  (opt-in).

A separate end-to-end check was also run with the real Groq SDK, the real stdio
MCP server, and the live World Bank API, using a local OpenAI-compatible test
double in place of the remote model. It confirmed that the tool result is
returned under the correct `tool_call_id` and that the second LLM call receives
the full assistant-plus-tool message history. The same path was then run against
the live Groq API with `openai/gpt-oss-120b`; the rate-limit handling above was
added in response to the free tier's 8,000 TPM cap.

---

## Project layout

```
world-bank-mcp/
|-- client/
|   |-- __init__.py
|   |-- agent.py          # stdio session, schema conversion, agent loop, CLI
|   `-- groq_client.py    # .env loading, provider setup, env defaults
|-- server/
|   |-- __init__.py
|   |-- schemas.py        # Pydantic models and annotated tool parameter types
|   |-- wb_docs_mcp.py    # FastMCP server and the four tools
|   `-- world_bank_api.py # HTTP client, retries, normalisation, fallbacks
|-- scripts/
|   `-- smoke_test.py     # live tool smoke test without an LLM
|-- tests/
|   |-- conftest.py
|   |-- test_agent.py
|   |-- test_api.py
|   |-- test_integration.py
|   |-- test_llm_config.py
|   `-- test_tools.py
|-- .env.example
|-- .gitignore
|-- pytest.ini
|-- requirements.txt
`-- README.md
```

---

## Design notes and limitations

- **The MCP server is the unit of reuse.** The client knows nothing about the
  World Bank API; it only knows MCP tools. Any MCP-compatible client can use the
  server.
- **Tool descriptions are part of the interface.** Each docstring explains when
  to use the tool and what the output means, which matters more for a small
  model than implementation detail.
- **No caching.** Every question reflects the live API. This keeps the demo
  honest but means repeated questions make repeated HTTP requests.
- **Relevance ranking is the API's.** WDS relevance search can surface older
  documents first. The `sort` parameter exists specifically so "latest" and
  "newest" questions can request date order instead.
- **Facet values are API-capped.** The live API currently exposes 100 distinct
  values per facet, so `get_facets` reports `total_values: 100` even when more
  languages or document types may exist in the underlying collection.
- **Dates are document dates.** `docdt` is the disclosure/publication date used
  by WDS, not necessarily the date the underlying work was completed.
- **Model variability.** `openai/gpt-oss-120b` is free but can occasionally
  choose a broad query or over-explore with extra tool calls. The system prompt,
  the bounded loop, the token budget, and the structured tool output reduce that
  risk; a different Groq model can be selected with `--model` if desired.
- **No streaming, persistence, or UI.** These were explicitly out of scope for
  the task.
