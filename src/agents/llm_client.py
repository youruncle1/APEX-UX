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
    return _build_client(
        backend=backend,
        model=model,
        default_openai_model="gpt-4o",
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
    if backend not in ("openai", "qwen"):
        raise ValueError(f"Unknown backend: {backend!r}. Use 'openai' or 'qwen'.")

    if backend == "openai":
        api_key = require_openai_api_key(settings)
        client = AsyncOpenAI(api_key=api_key)
        model_name = model or default_openai_model
        return client, model_name

    # qwen — points at the self-hosted llama-server
    client = AsyncOpenAI(
        base_url=f"{planner_service_url}/v1",
        api_key="not-needed",
    )
    model_name = model or "qwen3-vl"
    return client, model_name