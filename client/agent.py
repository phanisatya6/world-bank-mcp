"""MCP stdio client and Groq-powered multi-turn tool-calling agent.

The client starts ``server/wb_docs_mcp.py`` as a subprocess, discovers its
tools, converts the MCP JSON Schemas into Groq function definitions, executes
the model's tool calls through the MCP session, and feeds the results back to
the model until a final answer is produced.

Usage:
    python -m client.agent "What reports exist on climate resilience in Kenya?"
    python -m client.agent --interactive
    python -m client.agent --list-tools
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from client.groq_client import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    PROJECT_ROOT,
    ConfigurationError,
    create_llm_client,
    default_model,
    max_iterations_from_env,
    provider_display_name,
)

MAX_TOOL_RESULT_CHARS = int(os.getenv("MAX_TOOL_RESULT_CHARS", "12000") or "12000")
# Free LLM tiers (Groq, Gemini) cap tokens per minute, so the whole conversation
# is kept under a budget and oversized/old tool results are trimmed before each
# request.  Tokens are estimated as characters / 4.
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "6000") or "6000")
# Abstracts dominate the size of a document result; keep a short preview.
MAX_ABSTRACT_CHARS = int(os.getenv("MAX_ABSTRACT_CHARS", "320") or "320")

SYSTEM_PROMPT = """You are a World Bank research assistant with access to the \
World Bank Documents & Reports API through MCP tools.

Rules:
- Answer only from the documents returned by tools. Never invent titles, dates,
  document IDs, countries, or URLs.
- Use search_documents for keyword/topic questions. It accepts only qterm,
  language, rows, offset and sort - never country, topic, document_type or
  dates. Use filter_documents for any structured filter.
- Use filter_documents for structured requests: country, region, topic, document
  type, or a date range. Combine filters when the user gives several. Treat
  "Sub-Saharan Africa" as a region.
- Keep rows small (the default 10 is usually enough). Fetch more only when the
  user asks for many results.
- When the user asks for the "most recent", "latest", or "newest" documents,
  pass sort="newest" to search_documents or filter_documents.
- Use get_facets before filtering when the user asks which languages, document
  types, countries, regions, topics, or themes exist. Facet values are the
  valid exact filter values.
- Use get_document when the user asks for details about a specific document ID.
- If a search returns no results, try broader keywords, fewer filters, or a facet
  lookup. Never fill gaps with outside knowledge.
- Cite each relevant result once, with title, publication date, country, and the
  source URL. Do not repeat the same document. Mention the total number of
  matches and whether more pages exist.
- Convert ISO dates to readable dates (for example, 2021-07-01 -> July 2021).
- Prefer a final answer as soon as you have enough results; avoid extra tool
  calls. Be concise but complete. If the results do not answer the question, say
  so.
"""


class AgentError(RuntimeError):
    """Raised when the MCP/Groq agent loop cannot complete."""


@dataclass
class ToolInvocation:
    """Record of one tool call made by the model."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    error: str | None = None


@dataclass
class AgentResult:
    """Final answer plus the tool trace for debugging/evaluation."""

    answer: str
    iterations: int
    invocations: list[ToolInvocation] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)


def _tool_attr(tool: Any, snake: str, camel: str | None = None) -> Any:
    value = getattr(tool, snake, None)
    if value is None and camel:
        value = getattr(tool, camel, None)
    return value


def _sanitize_property(prop: dict[str, Any]) -> dict[str, Any]:
    """Flatten ``anyOf: [X, null]`` because small models handle it poorly."""
    prop = copy.deepcopy(prop)
    # ``additionalProperties`` is unnecessary for the model and some providers
    # (notably Gemini's OpenAI-compatible endpoint) are stricter about schemas.
    prop.pop("additionalProperties", None)
    any_of = prop.get("anyOf")
    if isinstance(any_of, list):
        non_null = [
            item
            for item in any_of
            if isinstance(item, dict) and item.get("type") != "null"
        ]
        if len(non_null) == 1:
            merged = _sanitize_property(non_null[0])
            for key, value in prop.items():
                if key in {"anyOf", "default"} or value is None:
                    continue
                merged[key] = value
            if prop.get("default") is not None:
                merged["default"] = prop["default"]
            return merged

    properties = prop.get("properties")
    if isinstance(properties, dict):
        prop["properties"] = {
            name: _sanitize_property(value)
            for name, value in properties.items()
            if isinstance(value, dict)
        }
    items = prop.get("items")
    if isinstance(items, dict):
        prop["items"] = _sanitize_property(items)
    return prop


def _sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    schema = copy.deepcopy(schema or {})
    schema.pop("additionalProperties", None)
    schema.setdefault("type", "object")
    properties = schema.get("properties")
    if isinstance(properties, dict):
        schema["properties"] = {
            name: _sanitize_property(value)
            for name, value in properties.items()
            if isinstance(value, dict)
        }
    schema.setdefault("required", [])
    return schema


def mcp_tools_to_groq(tools: list[Any]) -> list[dict[str, Any]]:
    """Convert MCP tools (name/description/input schema) into Groq functions."""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        name = _tool_attr(tool, "name")
        if not name:
            continue
        description = _tool_attr(tool, "description") or f"Call the {name} MCP tool."
        schema = _tool_attr(tool, "input_schema", "inputSchema") or {
            "type": "object",
            "properties": {},
        }
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": _sanitize_schema(schema),
                },
            }
        )
    return converted


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Parse a model-generated arguments payload into a JSON object."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"arguments were not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("arguments must be a JSON object")
    return parsed


def _content_text(result: Any) -> str:
    parts: list[str] = []
    for item in _tool_attr(result, "content") or []:
        if isinstance(item, dict):
            text = item.get("text")
        else:
            text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def _result_payload(result: Any) -> tuple[bool, Any]:
    """Return ``(ok, payload)`` from an MCP CallToolResult or a fake result."""
    is_error = bool(
        _tool_attr(result, "is_error", "isError")
        or _tool_attr(result, "isError")
    )
    if isinstance(result, dict):
        is_error = bool(result.get("isError") or result.get("is_error"))
        structured = result.get("structuredContent", result.get("structured_content"))
    else:
        structured = _tool_attr(result, "structured_content", "structuredContent")

    if is_error:
        text = _content_text(result)
        return False, text or "The MCP tool reported an error."

    if structured is not None:
        return True, structured

    text = _content_text(result)
    if not text:
        return True, {}
    try:
        return True, json.loads(text)
    except json.JSONDecodeError:
        return True, text


def _slim_document(document: Any) -> Any:
    """Shorten the bulky fields of one normalised document record."""
    if not isinstance(document, dict):
        return document
    slim: dict[str, Any] = {}
    for key, value in document.items():
        if key == "abstract" and isinstance(value, str) and len(value) > MAX_ABSTRACT_CHARS:
            slim[key] = value[: MAX_ABSTRACT_CHARS - 1].rstrip() + "…"
        else:
            slim[key] = value
    return slim


def _slim_tool_payload(payload: Any) -> Any:
    """Reduce a tool result to what the model needs before it is serialised.

    Search/filter responses are the main token cost: each document carries a
    long abstract and search responses can also embed a large ``facets`` block.
    Facets are dropped from document results (``get_facets`` is the dedicated
    tool for them) and abstracts are truncated to a short preview.
    """
    if not isinstance(payload, dict):
        return payload
    slim = dict(payload)
    if isinstance(slim.get("documents"), list):
        slim["documents"] = [_slim_document(doc) for doc in slim["documents"]]
        # The documents payload already carries the paging metadata; an embedded
        # facets block only inflates the request.
        slim.pop("facets", None)
    return slim


def _compact(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 1200 else value[:1199].rstrip() + "…"
    if depth >= 8:
        return str(value)[:200]
    if isinstance(value, dict):
        return {str(key): _compact(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_compact(item, depth + 1) for item in list(value)[:30]]
        if len(value) > 30:
            items.append(f"... {len(value) - 30} more items omitted")
        return items
    return value


def serialize_tool_payload(payload: Any, max_chars: int | None = None) -> str:
    """Serialise a tool result for the model, truncating oversized results.

    The output always remains parseable text; when a hard truncation is needed
    the structured result is replaced by a small JSON object containing a
    preview string.
    """
    limit = max_chars if max_chars is not None else MAX_TOOL_RESULT_CHARS
    payload = _slim_tool_payload(payload)
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = json.dumps({"result": str(payload)}, ensure_ascii=False)
    if len(text) <= limit:
        return text

    compacted = json.dumps(_compact(payload), ensure_ascii=False, default=str)
    if len(compacted) <= limit:
        return compacted

    preview = text[: max(0, limit // 2)]
    for _ in range(20):
        candidate = json.dumps(
            {
                "_truncated": True,
                "note": "Tool result too large; preview only.",
                "preview": preview,
            },
            ensure_ascii=False,
        )
        if len(candidate) <= limit or not preview:
            return candidate
        preview = preview[: int(len(preview) * 0.8)]
    return json.dumps(
        {"_truncated": True, "note": "Tool result too large."}, ensure_ascii=False
    )


TOOL_RESULT_OMITTED = json.dumps(
    {"note": "Earlier tool result omitted to stay within the model token limit."}
)


def _message_chars(message: dict[str, Any]) -> int:
    total = len(str(message.get("content") or ""))
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        total += len(str(function.get("arguments") or ""))
    return total


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token estimate (characters / 4) for a message list."""
    return sum(_message_chars(message) for message in messages) // 4


def fit_messages(
    messages: list[dict[str, Any]], budget: int
) -> list[dict[str, Any]]:
    """Return a copy of ``messages`` trimmed to roughly ``budget`` tokens.

    The system prompt, the original question and the most recent tool results
    are always preserved; older tool results are replaced with a short note so
    the model still knows a tool ran without resending the full payload.
    """
    fitted = [dict(message) for message in messages]
    if estimate_tokens(fitted) <= budget:
        return fitted

    for index, message in enumerate(fitted):
        if estimate_tokens(fitted) <= budget:
            break
        if message.get("role") != "tool":
            continue
        content = str(message.get("content") or "")
        if content == TOOL_RESULT_OMITTED:
            continue
        # Keep the most recent tool result intact; stub older ones first.
        if index == max(
            (i for i, m in enumerate(fitted) if m.get("role") == "tool"), default=-1
        ):
            continue
        fitted[index] = {**message, "content": TOOL_RESULT_OMITTED}

    if estimate_tokens(fitted) > budget:
        # Still too large: shrink remaining tool results to a short preview.
        for index, message in enumerate(fitted):
            if estimate_tokens(fitted) <= budget:
                break
            if message.get("role") != "tool":
                continue
            content = str(message.get("content") or "")
            if len(content) <= 400:
                continue
            fitted[index] = {**message, "content": content[:399].rstrip() + "…"}

    return fitted


def _retry_after_seconds(exc: Exception) -> float:
    """Best-effort ``Retry-After`` lookup, capped so a run cannot stall."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return 0.0
    try:
        raw = headers.get("retry-after") or headers.get("x-ratelimit-reset-requests")
    except Exception:
        return 0.0
    if not raw:
        return 0.0
    try:
        value = float(str(raw).strip().rstrip("s"))
    except ValueError:
        return 0.0
    return max(0.0, min(value, 30.0))


def _llm_error_message(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    provider = provider_display_name()
    if status in (401, 403):
        return (
            f"{provider} rejected the credentials. Check the API key in .env "
            "and that the key is active."
        )
    if status == 413:
        return (
            f"{provider} rejected the request because it was too large for the "
            "current rate limit. The agent trims older tool results automatically; "
            "retry, or ask a narrower question."
        )
    if status == 429:
        return (
            f"{provider} rate limit reached. Wait a moment and retry, or reduce "
            "the number of results (rows)."
        )
    detail = str(exc).strip().replace("\n", " ")[:400]
    return f"{provider} request failed ({type(exc).__name__}): {detail}"


@asynccontextmanager
async def open_mcp_session() -> AsyncIterator[ClientSession]:
    """Start the FastMCP server over stdio and yield an initialised session."""
    server_env = os.environ.copy()
    # The server never needs the LLM key; do not pass secrets to the subprocess.
    server_env.pop("GROQ_API_KEY", None)
    server_env["PYTHONIOENCODING"] = "utf-8"
    server_env["PYTHONUNBUFFERED"] = "1"
    server_env["FASTMCP_LOG_LEVEL"] = "WARNING"
    server_env["FASTMCP_SHOW_SERVER_BANNER"] = "false"
    existing_path = server_env.get("PYTHONPATH", "")
    server_env["PYTHONPATH"] = (
        f"{PROJECT_ROOT}{os.pathsep}{existing_path}" if existing_path else str(PROJECT_ROOT)
    )

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "server.wb_docs_mcp"],
        cwd=str(PROJECT_ROOT),
        env=server_env,
    )
    async with stdio_client(params, errlog=sys.stderr) as (read_stream, write_stream):
        async with ClientSession(
            read_stream, write_stream, read_timeout_seconds=90.0
        ) as session:
            await session.initialize()
            yield session


async def discover_mcp_tools(session: ClientSession) -> list[dict[str, Any]]:
    result = await session.list_tools()
    return mcp_tools_to_groq(list(result.tools))


class WorldBankAgent:
    """A bounded multi-turn tool-calling loop over an MCP session."""

    def __init__(
        self,
        session: ClientSession,
        llm: Any,
        *,
        tools: list[dict[str, Any]],
        model: str | None = None,
        max_iterations: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_context_tokens: int | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.session = session
        self.llm = llm
        self.tools = tools
        self.tool_names = {
            tool.get("function", {}).get("name") for tool in tools if tool
        }
        self.model = model or default_model()
        self.max_iterations = max(
            1, max_iterations if max_iterations is not None else DEFAULT_MAX_ITERATIONS
        )
        self.temperature = (
            temperature if temperature is not None else DEFAULT_TEMPERATURE
        )
        self.max_tokens = max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS
        self.max_context_tokens = (
            max_context_tokens
            if max_context_tokens is not None
            else MAX_CONTEXT_TOKENS
        )
        self.on_event = on_event

    def _emit(self, event: dict[str, Any]) -> None:
        if not self.on_event:
            return
        try:
            self.on_event(event)
        except Exception:  # A UI callback must never break the agent loop.
            pass

    async def _create_completion(
        self, messages: list[dict[str, Any]], *, with_tools: bool
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if with_tools and self.tools:
            kwargs["tools"] = self.tools
            kwargs["tool_choice"] = "auto"

        budget = self.max_context_tokens
        last_error: Exception | None = None
        for attempt in range(1, 4):
            payload = fit_messages(messages, budget)
            try:
                return await self.llm.chat.completions.create(
                    messages=payload, **kwargs
                )
            except AgentError:
                raise
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                # 413 = request larger than the per-minute budget, 429 = the
                # budget is exhausted. Trim the context and retry a couple of
                # times, waiting briefly when the provider asks us to.
                if status in (413, 429) and attempt < 3:
                    last_error = exc
                    budget = max(1500, budget // 2)
                    wait = _retry_after_seconds(exc) or (5.0 if status == 429 else 0.0)
                    print(
                        f"[agent] {provider_display_name()} rate limit hit; "
                        f"trimming context and retrying in {wait:.0f}s "
                        f"(attempt {attempt}/3).",
                        file=sys.stderr,
                    )
                    if wait:
                        await asyncio.sleep(wait)
                    continue
                raise AgentError(_llm_error_message(exc)) from exc
        raise AgentError(_llm_error_message(last_error or RuntimeError("LLM failure")))

    async def _execute_tool(
        self, call_id: str, name: str, arguments: dict[str, Any]
    ) -> tuple[bool, str, str | None]:
        """Execute one MCP tool and return ``(ok, content_for_model, error)``."""
        if name not in self.tool_names:
            error = (
                f"Unknown tool '{name}'. Available tools: "
                f"{', '.join(sorted(name for name in self.tool_names if name))}."
            )
            return False, json.dumps({"error": error}), error

        self._emit({"type": "tool_call", "id": call_id, "name": name, "arguments": arguments})
        try:
            result = await self.session.call_tool(name, arguments)
        except Exception as exc:
            error = f"MCP tool execution failed: {type(exc).__name__}: {exc}"
            self._emit({"type": "tool_result", "name": name, "ok": False, "chars": 0})
            return False, json.dumps({"error": error}), error

        ok, payload = _result_payload(result)
        if not ok:
            error = str(payload)
            content = json.dumps({"error": error}, ensure_ascii=False)
        else:
            error = None
            content = serialize_tool_payload(payload)
        self._emit(
            {
                "type": "tool_result",
                "name": name,
                "ok": ok,
                "chars": len(content),
            }
        )
        return ok, content, error

    async def answer(self, question: str) -> AgentResult:
        """Run the tool-calling loop until the model returns a final answer."""
        question = question.strip()
        if not question:
            raise AgentError("The question must not be empty.")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        invocations: list[ToolInvocation] = []
        iterations = 0

        for round_number in range(1, self.max_iterations + 1):
            iterations = round_number
            response = await self._create_completion(messages, with_tools=True)
            message = response.choices[0].message
            content = getattr(message, "content", None) or ""
            raw_calls = getattr(message, "tool_calls", None) or []

            if not raw_calls:
                answer = str(content).strip()
                if not answer:
                    raise AgentError("The model returned an empty response.")
                if getattr(response.choices[0], "finish_reason", None) == "length":
                    answer += (
                        "\n\n_[Note: the answer was cut off by the model's "
                        "output-token limit.]_"
                    )
                return AgentResult(
                    answer=answer,
                    iterations=iterations,
                    invocations=invocations,
                    messages=messages,
                )

            calls: list[dict[str, Any]] = []
            for index, call in enumerate(raw_calls, start=1):
                function = getattr(call, "function", None)
                call_id = getattr(call, "id", None) or f"call_{round_number}_{index}"
                calls.append(
                    {
                        "id": call_id,
                        "type": getattr(call, "type", None) or "function",
                        "function": {
                            "name": getattr(function, "name", "") or "",
                            "arguments": getattr(function, "arguments", "") or "{}",
                        },
                    }
                )

            # Preserve the assistant tool-call turn exactly as Groq expects it.
            messages.append(
                {
                    "role": "assistant",
                    "content": str(content),
                    "tool_calls": calls,
                }
            )

            for call in calls:
                name = call["function"]["name"]
                arguments: dict[str, Any] = {}
                try:
                    arguments = parse_tool_arguments(call["function"]["arguments"])
                except ValueError as exc:
                    error = f"Invalid tool arguments: {exc}"
                    ok = False
                    content_for_model = json.dumps({"error": error})
                    self._emit(
                        {"type": "tool_result", "name": name, "ok": False, "chars": len(content_for_model)}
                    )
                else:
                    ok, content_for_model, error = await self._execute_tool(
                        call["id"], name, arguments
                    )

                invocations.append(
                    ToolInvocation(
                        name=name,
                        arguments=arguments,
                        ok=ok,
                        error=error,
                    )
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": content_for_model,
                    }
                )

        # Bounded loop exhausted: ask for a final answer without more tools.
        messages.append(
            {
                "role": "user",
                "content": (
                    "You have reached the tool-call limit. Do not call more tools. "
                    "Give your best final answer now using only the tool results "
                    "above, including document titles, dates and URLs where available."
                ),
            }
        )
        response = await self._create_completion(messages, with_tools=False)
        message = response.choices[0].message
        answer = str(getattr(message, "content", None) or "").strip()
        iterations += 1
        if not answer:
            raise AgentError(
                "The model did not produce a final answer within the tool-call limit."
            )
        return AgentResult(
            answer=answer,
            iterations=iterations,
            invocations=invocations,
            messages=messages,
        )


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _print_event(event: dict[str, Any]) -> None:
    if event.get("type") == "tool_call":
        arguments = json.dumps(event.get("arguments", {}), ensure_ascii=False)
        print(f"\n[tool] {event['name']}({arguments})", file=sys.stderr)
    elif event.get("type") == "tool_result":
        status = "ok" if event.get("ok") else "error"
        print(
            f"[tool] {event['name']} -> {status} ({event.get('chars', 0)} chars)",
            file=sys.stderr,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="world-bank-agent",
        description="Ask natural-language questions about World Bank publications.",
    )
    parser.add_argument(
        "question",
        nargs="*",
        help="Question to ask. Omit it (or use --interactive) for a REPL.",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Keep the MCP session and LLM client open for multiple questions.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print the MCP tools discovered from the server (no API key needed).",
    )
    parser.add_argument(
        "--check-llm",
        action="store_true",
        help=(
            "Send one tiny request to the configured LLM to verify the API key "
            "and model. Does not start the MCP server."
        ),
    )
    parser.add_argument(
        "--show-tools",
        action="store_true",
        help="Print each tool call and result summary to stderr.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=max_iterations_from_env(),
        help=f"Maximum tool-call rounds (default: {max_iterations_from_env()}).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the provider model (GROQ_MODEL or GEMINI_MODEL) for this run.",
    )
    return parser


def _make_agent(
    session: ClientSession,
    llm: Any,
    tools: list[dict[str, Any]],
    args: argparse.Namespace,
) -> WorldBankAgent:
    return WorldBankAgent(
        session,
        llm,
        tools=tools,
        model=args.model,
        max_iterations=args.max_iterations,
        temperature=float(os.getenv("AGENT_TEMPERATURE", str(DEFAULT_TEMPERATURE))),
        max_tokens=int(os.getenv("AGENT_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
        max_context_tokens=int(os.getenv("MAX_CONTEXT_TOKENS", str(MAX_CONTEXT_TOKENS))),
        on_event=_print_event if args.show_tools else None,
    )


async def _run(args: argparse.Namespace) -> int:
    """Run the agent, unwrapping MCP task-group errors.

    When an error escapes the ``open_mcp_session`` body, the MCP session's
    ``anyio`` task group re-raises it inside an ``ExceptionGroup``. Unwrapping
    keeps the informative ``AgentError``/``ConfigurationError`` message instead
    of a bare "unhandled errors in a TaskGroup".
    """
    try:
        return await _run_inner(args)
    except BaseExceptionGroup as group:
        raise _unwrap_exception_group(group) from None


def _unwrap_exception_group(exc: BaseException) -> BaseException:
    """Return the first leaf exception from a (possibly nested) group."""
    current: BaseException = exc
    seen: set[int] = set()
    while isinstance(current, BaseExceptionGroup):
        if id(current) in seen or not current.exceptions:
            break
        seen.add(id(current))
        current = current.exceptions[0]
    return current


async def _run_inner(args: argparse.Namespace) -> int:
    question = " ".join(args.question).strip()

    async with open_mcp_session() as session:
        tools = await discover_mcp_tools(session)

        if args.list_tools:
            if not tools:
                print("No tools were discovered.", file=sys.stderr)
            for tool in tools:
                function = tool["function"]
                print(f"{function['name']}: {function['description'].splitlines()[0]}")
                print(json.dumps(function["parameters"], indent=2))
                print()
            if not question and not args.interactive:
                return 0

        if not tools:
            raise AgentError("The MCP server exposed no tools.")

        llm = create_llm_client()

        if question and not args.interactive:
            result = await _make_agent(session, llm, tools, args).answer(question)
            print(result.answer)
            return 0

        print(
            "World Bank Documents agent. Type a question, or 'exit' to quit.",
            file=sys.stderr,
        )
        while True:
            try:
                prompt = input("\nQuestion> ").strip()
            except (EOFError, KeyboardInterrupt):
                print(file=sys.stderr)
                return 0
            if not prompt:
                continue
            if prompt.lower() in {"exit", "quit"}:
                return 0
            try:
                result = await _make_agent(session, llm, tools, args).answer(prompt)
            except AgentError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                continue
            print(result.answer)


async def _check_llm() -> int:
    """Send one tiny completion to verify the configured provider and key."""
    model = default_model()
    llm = create_llm_client()
    try:
        response = await llm.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: OK"}],
            temperature=0.0,
            max_tokens=256,
        )
    except Exception as exc:
        raise AgentError(_llm_error_message(exc)) from exc

    message = response.choices[0].message
    reply = (getattr(message, "content", None) or "").strip()
    if not reply:
        # Reasoning models (for example gpt-oss) may spend the whole budget on
        # hidden reasoning and return no visible text.
        reply = str(getattr(message, "reasoning", None) or "").strip()
    print(f"Provider: {provider_display_name()}")
    print(f"Model: {model}")
    print(f"Response: {reply or '(no text returned)'}")
    print("LLM connection OK.")
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.max_iterations < 1:
        parser.error("--max-iterations must be at least 1")

    try:
        if args.check_llm:
            return asyncio.run(_check_llm())
        return asyncio.run(_run(args))
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except AgentError as exc:
        print(f"Agent error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # Includes MCP subprocess/startup failures.
        print(f"Fatal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
