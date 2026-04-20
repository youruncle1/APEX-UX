from typing import Optional
from openai import AsyncOpenAI

from src.config import get_settings, require_openai_api_key


def get_planner_client(
    backend: Optional[str] = None,
    model: Optional[str] = None,
) -> tuple[AsyncOpenAI, str]:
    """Get OpenAI client and model name for the planner."""
    settings = get_settings()
    backend = (backend or settings.planner_backend).lower()
    model = model or settings.planner_model
    return _build_client(
        backend=backend,
        model=model,
        default_openai_model="gpt-5.4",
        planner_service_url=settings.planner_service_url,
        settings=settings,
    )


def get_utility_client(
    backend: Optional[str] = None,
    model: Optional[str] = None,
) -> tuple[AsyncOpenAI, str]:
    """Get OpenAI client and model name for utility tasks (SEQ, SUS, summarization)."""
    settings = get_settings()
    backend = (backend or settings.utility_backend).lower()
    model = model or settings.utility_model
    return _build_client(
        backend=backend,
        model=model,
        default_openai_model="gpt-4o-mini",
        planner_service_url=settings.planner_service_url,
        settings=settings,
    )


def _build_client(
    backend: str,
    model: Optional[str],
    default_openai_model: str,
    planner_service_url: str,
    settings,
) -> tuple[AsyncOpenAI, str]:
    """Build client for a given backend."""
    if backend not in ("openai", "local"):
        raise ValueError(f"Unknown backend: {backend!r}. Use 'openai' or 'local'.")

    if backend == "openai":
        api_key = require_openai_api_key(settings)
        client = AsyncOpenAI(api_key=api_key)
        model_name = model or default_openai_model
        return client, model_name

    # local — points at a self-hosted OpenAI-compatible server (llama.cpp, Ollama, vLLM, ...)
    if not model:
        raise ValueError(
            "PLANNER_MODEL / UTILITY_MODEL must be set when using the local backend. "
            "Add it to your .env file."
        )
    client = AsyncOpenAI(
        base_url=f"{planner_service_url}/v1",
        api_key="not-needed",
    )
    return client, model
