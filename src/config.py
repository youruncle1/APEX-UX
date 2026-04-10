# Inspired by Lakmoos's backend config.py
import functools
from typing import Optional

import pydantic
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "apex_ux"
    db_user: str = "apex"
    db_password: Optional[pydantic.SecretStr] = None

    # if present, overrides other db variables and uses url.
    database_url: Optional[str] = None

    @pydantic.model_validator(mode="after")
    def get_database_url(self) -> "Settings":
        if self.database_url:
            return self

        if self.db_password is None:
            return self

        pwd = self.db_password.get_secret_value()
        object.__setattr__(
            self,
            "database_url",
            f"postgresql+asyncpg://{self.db_user}:{pwd}@{self.db_host}:{self.db_port}/{self.db_name}",
        )
        return self

    # Application server
    app_host: str = "0.0.0.0"
    app_port: int = 5000

    # AI backends
    # Error policy for the evaluation runner: "continue_on_error" | "fail_fast"
    # continue_on_error: clone errors are logged and skipped, evaluation ends as FAILED
    # fail_fast: first clone error stops the entire evaluation immediately
    error_policy: str = "continue_on_error"

    # Which LLM backend to use for the planner agent: "openai" | "local"
    planner_backend: str = "openai"

    # Which LLM backend to use for utility tasks (SEQ, SUS, memory compression):
    # "openai" | "local"
    utility_backend: str = "openai"

    # OpenAI API key - required when either backend is "openai"
    openai_api_key: Optional[pydantic.SecretStr] = None

    # Model names - required when using the "local" backend, optional for "openai"
    # (openai defaults to gpt-5.4 for planner and gpt-4o-mini for utility)
    # PLANNER_BACKEND=local requires a vision-capable model (multimodal)
    # UTILITY_BACKEND=local works with any text model
    planner_model: Optional[str] = None
    utility_model: Optional[str] = None

    # URL of the local OpenAI-compatible server (used when backend = "local")
    # Must expose an OpenAI-compatible API (e.g. llama.cpp, Ollama, vLLM)
    planner_service_url: str = "http://localhost:8001"

    # Grounding service
    grounding_service_url: str = "http://localhost:8000"

    # Memory compression threshold:
    # - prevents context overflow during the two-agent navigation loop.
    # - Once the action history for a running task exceeds this number of steps,
    #   the oldest half is summarised by LLM and replaced with summarisation,
    #   keeping only the most recent (threshold / 2) actions in full detail.
    memory_compression_threshold: int = 10

    # SUS action compression threshold:
    # - prevents context overflow when collecting SUS scores.
    # - If total action/steps count across tasks exceed the threshold, 
    #   any individual task whose action count exceeds (threshold / num_tasks) 
    #   gets its action log summarised by LLM.
    sus_action_cumulative_threshold: int = 400

    # Browser
    headless: bool = True

    # Screenshot storage
    # "local" or "s3"
    screenshot_storage_type: str = "local"

    # Local storage
    local_storage_path: str = "storage/screenshots"

    # S3-compatible storage
    s3_endpoint_url: Optional[str] = None
    s3_access_key: Optional[pydantic.SecretStr] = None
    s3_secret_key: Optional[pydantic.SecretStr] = None
    s3_bucket_name: Optional[str] = None
    s3_region: str = "eu-central-1"
    s3_public_url_base: Optional[str] = None

    # pydantic-settings config
    model_config = SettingsConfigDict(
        extra="ignore",
        env_file=(".env", "env.prod"),
        env_file_encoding="utf-8",
        frozen=True,  # Immutable after construction
    )


@functools.lru_cache()
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def require_openai_api_key(settings: Settings) -> str:
    """
    Return the OpenAI API key as a plain string, or raise if it is absent /
    looks like a placeholder.
    """
    secret = settings.openai_api_key
    value = secret.get_secret_value().strip() if secret is not None else ""
    if value and value.lower() not in {"", "your_openai_api_key", "<your-api-key>", "na"}:
        return value
    raise ValueError(
        "OPENAI_API_KEY must be set to a real key when using the OpenAI backend. "
        "Add it to your .env file or pass it as an environment variable."
    )


def require_database_url(settings: Settings) -> str:
    """
    Return the database URL as a plain string, or raise if it could not be
    assembled (missing password and no DATABASE_URL override).
    """
    if settings.database_url:
        return settings.database_url
    raise ValueError(
        "Database URL could not be determined. "
        "Either set DATABASE_URL directly, or provide DB_PASSWORD (plus optionally "
        "DB_HOST, DB_PORT, DB_USER, DB_NAME) in your environment / .env file."
    )


def require_s3_config(settings: Settings) -> dict:
    """
    Validate and return S3 configuration as a plain dict, or raise if any
    required field is missing.
    """
    missing = []
    for field in ("s3_endpoint_url", "s3_access_key", "s3_secret_key", "s3_bucket_name"):
        if getattr(settings, field) is None:
            missing.append(field.upper())
    if missing:
        raise ValueError(
            f"S3 storage requires the following env vars: {', '.join(missing)}"
        )
    return {
        "endpoint_url": settings.s3_endpoint_url,
        "access_key": settings.s3_access_key.get_secret_value(),  # type: ignore[union-attr]
        "secret_key": settings.s3_secret_key.get_secret_value(),  # type: ignore[union-attr]
        "bucket_name": settings.s3_bucket_name,
        "region": settings.s3_region,
        "public_url_base": settings.s3_public_url_base,
    }