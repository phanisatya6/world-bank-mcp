"""LLM provider configuration for the agent.

Two OpenAI-compatible providers are supported:

* **Groq** (default) through the ``groq`` SDK.
* **Google Gemini** through its OpenAI-compatible endpoint, using the
  ``openai`` SDK pointed at
  ``https://generativelanguage.googleapis.com/v1beta/openai/``.

Both providers expose the same ``chat.completions.create`` interface, so the
agent loop in ``client/agent.py`` is provider-agnostic.  Choose a provider with
``LLM_PROVIDER`` (``groq`` or ``gemini``) or let it be auto-detected from the
API keys that are present.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import AsyncGroq

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MAX_ITERATIONS = 6
DEFAULT_TEMPERATURE = 0.1
DEFAULT_MAX_TOKENS = 2048

SUPPORTED_PROVIDERS = ("groq", "gemini")


class ConfigurationError(RuntimeError):
    """Raised when required LLM configuration is missing."""


def resolve_provider() -> str:
    """Return the configured provider name: ``"groq"`` or ``"gemini"``.

    An explicit ``LLM_PROVIDER`` always wins.  Otherwise Gemini is selected when
    ``GEMINI_API_KEY`` is set and no ``GROQ_API_KEY`` is present; Groq is the
    default in every other case.
    """
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    if explicit in SUPPORTED_PROVIDERS:
        return explicit
    if os.getenv("GEMINI_API_KEY", "").strip() and not os.getenv(
        "GROQ_API_KEY", ""
    ).strip():
        return "gemini"
    return "groq"


def provider_display_name() -> str:
    """Human-readable provider name used in error messages."""
    return "Gemini" if resolve_provider() == "gemini" else "Groq"


def default_model() -> str:
    """Default model for the active provider, overridable by environment."""
    if resolve_provider() == "gemini":
        return (
            os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
            or DEFAULT_GEMINI_MODEL
        )
    return os.getenv("GROQ_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _create_groq_client() -> AsyncGroq:
    """Create an async Groq client, with an optional OpenAI-compatible base URL."""
    base_url = os.getenv("GROQ_BASE_URL", "").strip() or None
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        if not base_url:
            raise ConfigurationError(
                "GROQ_API_KEY is not set. Create a free key at "
                "https://console.groq.com/keys and add it to .env "
                "(copy .env.example to .env). Alternatively set "
                "GEMINI_API_KEY and LLM_PROVIDER=gemini to use Google Gemini."
            )
        api_key = "ollama"  # Local OpenAI-compatible servers accept any key.

    return AsyncGroq(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=2)


def _create_gemini_client() -> Any:
    """Create an async OpenAI client pointed at Gemini's OpenAI-compatible API."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    base_url = os.getenv("GEMINI_BASE_URL", "").strip() or GEMINI_BASE_URL
    if not api_key:
        raise ConfigurationError(
            "GEMINI_API_KEY is not set. Create a free key at "
            "https://aistudio.google.com/apikey and add it to .env "
            "(copy .env.example to .env), or set LLM_PROVIDER=groq with a "
            "GROQ_API_KEY."
        )

    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=60.0,
        max_retries=2,
    )


def create_llm_client() -> Any:
    """Create the async LLM client for the resolved provider."""
    if resolve_provider() == "gemini":
        return _create_gemini_client()
    return _create_groq_client()


def max_iterations_from_env() -> int:
    raw = os.getenv("AGENT_MAX_ITERATIONS", str(DEFAULT_MAX_ITERATIONS)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_ITERATIONS
    return max(1, min(value, 20))
