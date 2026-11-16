"""Agent-loop tests using a fake MCP session and a fake Groq client.

These tests verify the evaluation-critical behaviour without needing a Groq key
or making network calls: multi-turn history, tool_call_id correlation, error
feedback, schema conversion, and the iteration bound.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from client import agent
from client.agent import (
    AgentError,
    WorldBankAgent,
    fit_messages,
    estimate_tokens,
    mcp_tools_to_groq,
    parse_tool_arguments,
    serialize_tool_payload,
)

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "search_documents",
        "description": "Search documents",
        "parameters": {
            "type": "object",
            "properties": {"qterm": {"type": "string"}},
            "required": ["qterm"],
        },
    },
}


@dataclass
class FakeToolResult:
    structured_content: Any = None
    content: list[Any] = field(default_factory=list)
    is_error: bool = False


class FakeSession:
    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeLLM:
    def __init__(self, responses: list[Any]) -> None:
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


def _response(content: str | None = None, calls: list[Any] | None = None) -> Any:
    message = SimpleNamespace(content=content, tool_calls=calls or [])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _call(call_id: str, name: str, arguments: Any) -> Any:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _agent(
    session: FakeSession,
    llm: FakeLLM,
    *,
    max_iterations: int = 6,
    tools: list[dict[str, Any]] | None = None,
) -> WorldBankAgent:
    return WorldBankAgent(
        session,  # type: ignore[arg-type]
        llm,
        tools=tools if tools is not None else [TOOL_DEFINITION],
        model="test-model",
        max_iterations=max_iterations,
    )


async def test_tool_call_roundtrip_preserves_history_and_ids():
    session = FakeSession(
        [
            FakeToolResult(
                structured_content={
                    "total": 1,
                    "documents": [{"id": "1", "title": "Climate report"}],
                }
            )
        ]
    )
    llm = FakeLLM(
        [
            _response(calls=[_call("call_1", "search_documents", '{"qterm": "climate"}')]),
            _response("Kenya has a climate report (2021)."),
        ]
    )

    result = await _agent(session, llm).answer("What climate reports exist for Kenya?")

    assert result.answer == "Kenya has a climate report (2021)."
    assert result.iterations == 2
    assert [(call.name, call.arguments) for call in result.invocations] == [
        ("search_documents", {"qterm": "climate"})
    ]
    assert result.invocations[0].ok is True
    assert session.calls == [("search_documents", {"qterm": "climate"})]

    second_request = llm.completions.requests[1]
    messages = second_request["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assistant = messages[2]
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert assistant["tool_calls"][0]["function"]["name"] == "search_documents"
    tool_message = messages[3]
    assert tool_message["tool_call_id"] == "call_1"
    assert json.loads(tool_message["content"])["documents"][0]["id"] == "1"
    assert second_request["tool_choice"] == "auto"


async def test_malformed_arguments_are_fed_back_without_crashing():
    session = FakeSession([])
    llm = FakeLLM(
        [
            _response(calls=[_call("call_1", "search_documents", "{not json")]),
            _response("I could not run the tool."),
        ]
    )

    result = await _agent(session, llm).answer("Question")

    assert session.calls == []
    assert result.invocations[0].ok is False
    assert "Invalid tool arguments" in (result.invocations[0].error or "")
    tool_message = llm.completions.requests[1]["messages"][-1]
    assert "Invalid tool arguments" in json.loads(tool_message["content"])["error"]


async def test_unknown_tool_is_reported_to_model():
    session = FakeSession([])
    llm = FakeLLM(
        [
            _response(calls=[_call("call_1", "delete_everything", "{}")]),
            _response("That tool does not exist."),
        ]
    )

    result = await _agent(session, llm).answer("Question")

    assert session.calls == []
    assert result.invocations[0].ok is False
    assert "Unknown tool" in (result.invocations[0].error or "")
    tool_message = llm.completions.requests[1]["messages"][-1]
    assert "search_documents" in json.loads(tool_message["content"])["error"]


async def test_mcp_tool_error_is_returned_to_model():
    session = FakeSession(
        [
            FakeToolResult(
                is_error=True,
                content=[SimpleNamespace(text="No document found with that ID.")],
            )
        ]
    )
    llm = FakeLLM(
        [
            _response(calls=[_call("call_1", "search_documents", '{"qterm": "x"}')]),
            _response("The tool could not find it."),
        ]
    )

    result = await _agent(session, llm).answer("Question")

    assert result.invocations[0].ok is False
    assert "No document found" in (result.invocations[0].error or "")
    assert result.answer == "The tool could not find it."


async def test_mcp_execution_exception_is_returned_to_model():
    session = FakeSession([RuntimeError("connection reset")])
    llm = FakeLLM(
        [
            _response(calls=[_call("call_1", "search_documents", '{"qterm": "x"}')]),
            _response("The search failed."),
        ]
    )
    result = await _agent(session, llm).answer("Question")
    assert result.invocations[0].ok is False
    assert "connection reset" in (result.invocations[0].error or "")


async def test_iteration_limit_forces_final_answer_without_tools():
    session = FakeSession([FakeToolResult(structured_content={"total": 0})])
    llm = FakeLLM(
        [
            _response(calls=[_call("call_1", "search_documents", '{"qterm": "x"}')]),
            _response("No results were found."),
        ]
    )

    result = await _agent(session, llm, max_iterations=1).answer("Question")

    assert result.answer == "No results were found."
    assert result.iterations == 2
    final_request = llm.completions.requests[1]
    assert "tools" not in final_request
    assert "tool-call limit" in final_request["messages"][-1]["content"]


async def test_llm_failure_is_wrapped_in_agent_error():
    llm = FakeLLM([RuntimeError("network down")])
    with pytest.raises(AgentError, match="network down"):
        await _agent(FakeSession([]), llm).answer("Question")


def test_mcp_schema_is_converted_for_groq():
    tool = SimpleNamespace(
        name="search_documents",
        description="Search documents",
        input_schema={
            "type": "object",
            "properties": {
                "qterm": {"type": "string", "description": "Query"},
                "language": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "description": "Language",
                },
            },
            "required": ["qterm"],
            "additionalProperties": False,
        },
    )
    converted = mcp_tools_to_groq([tool])[0]
    assert converted["type"] == "function"
    assert converted["function"]["name"] == "search_documents"
    parameters = converted["function"]["parameters"]
    assert "additionalProperties" not in parameters
    language = parameters["properties"]["language"]
    assert language["type"] == "string"
    assert "anyOf" not in language
    assert language["description"] == "Language"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{}", {}),
        ('{"qterm": "climate"}', {"qterm": "climate"}),
        (None, {}),
        ({"qterm": "already parsed"}, {"qterm": "already parsed"}),
    ],
)
def test_parse_tool_arguments(raw: Any, expected: dict[str, Any]):
    assert parse_tool_arguments(raw) == expected


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '{"a": '])
def test_parse_tool_arguments_rejects_invalid_payloads(raw: str):
    with pytest.raises(ValueError):
        parse_tool_arguments(raw)


def test_oversized_tool_result_is_truncated_to_valid_json():
    payload = {"documents": [{"abstract": "x" * 100_000}] * 100}
    text = serialize_tool_payload(payload, max_chars=600)
    assert len(text) <= 600
    decoded = json.loads(text)
    assert decoded["_truncated"] is True


def test_small_tool_result_is_unchanged():
    payload = {"total": 1, "documents": [{"id": "1"}]}
    assert json.loads(serialize_tool_payload(payload)) == payload


class _FakeCompletions:
    async def create(self, **kwargs: Any) -> Any:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="OK", tool_calls=None))
            ]
        )


async def test_check_llm_reports_provider_and_model(monkeypatch, capsys):
    fake = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    monkeypatch.setattr(agent, "create_llm_client", lambda: fake)
    monkeypatch.setattr(agent, "default_model", lambda: "test-model")
    monkeypatch.setattr(agent, "provider_display_name", lambda: "Gemini")

    assert await agent._check_llm() == 0
    output = capsys.readouterr().out
    assert "Provider: Gemini" in output
    assert "Model: test-model" in output
    assert "LLM connection OK." in output


async def test_check_llm_wraps_provider_errors(monkeypatch):
    class _FailingCompletions:
        async def create(self, **kwargs: Any) -> Any:
            raise RuntimeError("boom")

    fake = SimpleNamespace(chat=SimpleNamespace(completions=_FailingCompletions()))
    monkeypatch.setattr(agent, "create_llm_client", lambda: fake)
    monkeypatch.setattr(agent, "default_model", lambda: "test-model")

    with pytest.raises(AgentError, match="boom"):
        await agent._check_llm()


def test_estimate_tokens_scales_with_content():
    messages = [{"role": "user", "content": "x" * 400}]
    assert estimate_tokens(messages) == 100


def test_fit_messages_stubs_older_tool_results_and_keeps_the_latest():
    big = "x" * 8000
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "t", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": big},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "b", "type": "function", "function": {"name": "t", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "b", "content": big},
    ]

    fitted = fit_messages(messages, budget=3000)

    assert estimate_tokens(fitted) <= 3000
    assert fitted[3]["content"] == agent.TOOL_RESULT_OMITTED
    assert fitted[5]["content"] == big  # most recent result is preserved
    # The original message list must not be mutated.
    assert messages[3]["content"] == big


def test_slim_tool_payload_truncates_abstracts_and_drops_facets():
    payload = {
        "total": 1,
        "documents": [{"id": "1", "title": "T", "abstract": "a" * 5000}],
        "facets": {"lang": [{"value": "English", "count": 1}]},
    }

    slim = agent._slim_tool_payload(payload)

    assert "facets" not in slim
    abstract = slim["documents"][0]["abstract"]
    assert len(abstract) <= agent.MAX_ABSTRACT_CHARS
    assert abstract.endswith("…")
    assert slim["documents"][0]["title"] == "T"


def test_unwrap_exception_group_returns_the_leaf():
    leaf = AgentError("boom")
    group = BaseExceptionGroup(
        "outer", [BaseExceptionGroup("inner", [leaf])]
    )
    assert agent._unwrap_exception_group(group) is leaf


def test_413_and_429_produce_informative_messages():
    class _Status(Exception):
        def __init__(self, status_code: int) -> None:
            super().__init__("boom")
            self.status_code = status_code

    assert "too large" in agent._llm_error_message(_Status(413))
    assert "rate limit" in agent._llm_error_message(_Status(429))


async def test_413_retry_trims_context_and_recovers():
    class _TooLarge(Exception):
        status_code = 413

    session = FakeSession([])
    llm = FakeLLM([_TooLarge("request too large"), _response("Recovered answer")])

    result = await _agent(session, llm).answer("Question")

    assert result.answer == "Recovered answer"
    assert len(llm.completions.requests) == 2
    # The retry used a tighter token budget than the first attempt.
    assert agent.estimate_tokens(llm.completions.requests[1]["messages"]) <= 3000


async def test_truncated_answer_is_flagged():
    session = FakeSession([])
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="partial answer", tool_calls=[]),
                finish_reason="length",
            )
        ]
    )
    llm = FakeLLM([response])

    result = await _agent(session, llm).answer("Question")

    assert result.answer.startswith("partial answer")
    assert "cut off" in result.answer
