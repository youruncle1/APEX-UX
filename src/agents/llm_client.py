from typing import Optional
from openai import AsyncOpenAI

from src.config import get_settings, require_openai_api_key

# Factory layer for LLM client construction.
# Returns an AsyncOpenAI client and model name
# Two backends are supported:
#   "openai" - calls the real OpenAI API (requires OPENAI_API_KEY)
#   "local"  - calls any OpenAI-compatible server (llama.cpp, Ollama, vLLM, ...)
#              pointed at PLANNER_SERVICE_URL, no key needed


def get_planner_client(
    backend: Optional[str] = None,
    model: Optional[str] = None,
) -> tuple[AsyncOpenAI, str]:
    """Build client for the PlannerAgent"""
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


def get_grounding_client(
    model: Optional[str] = None,  # overrides UTILITY_MODEL from .env
) -> tuple[AsyncOpenAI, str]:
    """Build client for the GroundingAgent"""
    settings = get_settings()
    grounding_model = model or settings.grounding_model
    if not grounding_model:
        raise ValueError(
            "GROUNDING_MODEL must be set when using the grounding agent. "
            "Add it to your .env file."
        )
    client = AsyncOpenAI(
        base_url=f"{settings.grounding_service_url}/v1",
        api_key=settings.grounding_api_key,
    )
    return client, grounding_model


def get_utility_client(
    backend: Optional[str] = None,  # overrides UTILITY_BACKEND from .env
    model: Optional[str] = None,  # overrides UTILITY_MODEL from .env
) -> tuple[AsyncOpenAI, str]:
    """Build client for utility tasks (memory compression, SUS/SEQ)"""
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
    if backend not in ("openai", "local"):
        raise ValueError(f"Unknown backend: {backend!r}. Use 'openai' or 'local'.")

    # openai - Pointed to OpenAI API, needs api key
    if backend == "openai":
        api_key = require_openai_api_key(settings)
        client = AsyncOpenAI(api_key=api_key)
        model_name = (
            model or default_openai_model
        )  # fall back to default if not set in .env
        return client, model_name

    # local - AsyncOpenAI pointed at a server
    if not model:
        raise ValueError(
            "PLANNER_MODEL / UTILITY_MODEL must be set when using the local backend. "
            "Add it to your .env file."
        )
    client = AsyncOpenAI(
        base_url=f"{planner_service_url}/v1",
        api_key="not-needed",  # api_key is required by the constructor, placeholder
    )
    return client, model
