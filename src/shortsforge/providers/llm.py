"""LLM provider — BYOK wrapper supporting OpenAI and Azure OpenAI."""

from __future__ import annotations

import os
from typing import Any, Literal

import structlog
from openai import AsyncAzureOpenAI, AsyncOpenAI

from shortsforge.security.rate_limit import LLM_BUCKET

logger = structlog.get_logger(__name__)


def _get_client() -> AsyncOpenAI | AsyncAzureOpenAI:
    """Return an async OpenAI client from environment variables."""
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_key = os.getenv("AZURE_OPENAI_KEY")

    if azure_endpoint and azure_key:
        return AsyncAzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=azure_key,
            api_version="2024-02-01",
        )

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No LLM credentials found. Set OPENAI_API_KEY or "
            "AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_KEY."
        )
    return AsyncOpenAI(api_key=api_key)


def _get_model() -> str:
    return os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv("OPENAI_MODEL", "gpt-4o")


async def complete(
    system: str,
    user: str,
    *,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    response_format: Literal["text", "json_object"] = "text",
) -> str:
    """Run a single chat completion, respecting the global LLM rate limit."""
    LLM_BUCKET.consume(1)

    client = _get_client()
    model = _get_model()

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format == "json_object":
        kwargs["response_format"] = {"type": "json_object"}

    logger.debug("llm.request", model=model, temperature=temperature)
    response = await client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content or ""
    logger.debug(
        "llm.response", tokens=response.usage.total_tokens if response.usage else 0
    )
    return content
