# src/agents/llm_client.py

"""
Shared LLM client factory.

Centralizes backend selection so planner, SEQ/SUS, and summarization
all respect the same configuration pattern.

Environment variables:
    PLANNER_BACKEND: "openai" or "qwen" — controls the planner (vision model)
    UTILITY_BACKEND: "openai" or "qwen" — controls SEQ, SUS, summarization (text-only)
                     Defaults to "openai" if not set.
    OPENAI_API_KEY: Required when any backend is "openai"
    PLANNER_SERVICE_URL: llama-server URL when backend is "qwen" (default: http://localhost:8001)
"""

import os
from typing import Optional
from openai import AsyncOpenAI


def get_planner_client(
    backend: Optional[str] = None,
    model: Optional[str] = None
) -> tuple[AsyncOpenAI, str]:
    """
    Get OpenAI client and model name for the planner (vision).

    Args:
        backend: Override for backend selection
        model: Override for model name

    Returns:
        (client, model_name) tuple
    """
    backend = (backend or os.getenv("PLANNER_BACKEND", "openai")).lower()
    return _build_client(backend, model, default_openai_model="gpt-4o")


def get_utility_client(
    backend: Optional[str] = None,
    model: Optional[str] = None
) -> tuple[AsyncOpenAI, str]:
    """
    Get OpenAI client and model name for utility tasks (SEQ, SUS, summarization).

    These are text-only tasks. When backend is "qwen", they use the same
    llama-server instance as the planner.

    Args:
        backend: Override for backend selection
        model: Override for model name

    Returns:
        (client, model_name) tuple
    """
    backend = (backend or os.getenv("UTILITY_BACKEND", "openai")).lower()
    return _build_client(backend, model, default_openai_model="gpt-4o-mini")


def _build_client(
    backend: str,
    model: Optional[str],
    default_openai_model: str
) -> tuple[AsyncOpenAI, str]:
    """Build client for a given backend."""
    if backend not in ("openai", "qwen"):
        raise ValueError(f"Unknown backend: {backend}. Use 'openai' or 'qwen'")

    if backend == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI backend")

        client = AsyncOpenAI(api_key=api_key)
        model_name = model or default_openai_model
        return client, model_name

    else:  # qwen
        service_url = os.getenv("PLANNER_SERVICE_URL", "http://localhost:8001")

        client = AsyncOpenAI(
            base_url=f"{service_url}/v1",
            api_key="not-needed"
        )
        model_name = model or "qwen3-vl"
        return client, model_name