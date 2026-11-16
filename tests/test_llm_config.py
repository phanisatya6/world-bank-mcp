"""Tests for LLM provider selection (Groq vs Google Gemini)."""

from __future__ import annotations

import pytest

from client import groq_client

PROVIDER_ENV = (
    "LLM_PROVIDER",
    "GROQ_API_KEY",
    "GROQ_BASE_URL",
    "GROQ_MODEL",
    "GEMINI_API_KEY",
    "GEMINI_BASE_URL",
    "GEMINI_MODEL",
)


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)


def test_provider_defaults_to_groq() -> None:
    assert groq_client.resolve_provider() == "groq"
    assert groq_client.default_model() == "openai/gpt-oss-120b"


def test_gemini_is_auto_detected_from_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert groq_client.resolve_provider() == "gemini"
    assert groq_client.default_model() == "gemini-2.5-flash"


def test_explicit_provider_overrides_auto_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    assert groq_client.resolve_provider() == "groq"
    assert groq_client.default_model() == "openai/gpt-oss-120b"


def test_provider_model_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")
    assert groq_client.default_model() == "gemini-3.6-flash"


def test_gemini_client_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(groq_client.ConfigurationError, match="GEMINI_API_KEY"):
        groq_client.create_llm_client()


def test_groq_client_requires_a_key_or_base_url() -> None:
    with pytest.raises(groq_client.ConfigurationError, match="GROQ_API_KEY"):
        groq_client.create_llm_client()


def test_gemini_client_points_at_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = groq_client.create_llm_client()
    assert "generativelanguage.googleapis.com" in str(client.base_url)


def test_groq_client_is_created_with_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    client = groq_client.create_llm_client()
    assert client.api_key == "test-key"
