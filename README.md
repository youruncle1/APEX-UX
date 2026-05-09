# APEX-UX

APEX-UX is an automated web usability evaluation system using persona-driven AI agents. It uses a two-agent loop (a **Planner** and a **Grounder**) to autonomously navigate websites, execute predefined tasks, and collect usability metrics (TSR, SUS, SEQ...). Multiple simulated user personas (clones) run concurrently, each completing the same task set, with results aggregated into a structured evaluation report.

---

## Setup

### Prerequisites

- Docker and Docker Compose

### Getting Started

1. Copy the example environment file and fill in required values:
   ```bash
   cp .env.example .env
   ```

2. Start the application:
   ```bash
   docker compose up
   ```

The database is initialized automatically on first run. The app is available at **http://localhost:8002** by default (configurable via `APP_PORT` in `.env`).

> Adminer (database browser) is available at **http://localhost:8080**.

---

## Model Services

For evaluations to work, two model services must be reachable from the container.

### Grounder

Always a local vision model (`UI-Venus-Ground-7B`). The service must listen on `0.0.0.0:<port>`. Configure the URL in `.env` via `GROUNDING_SERVICE_URL`.

The grounding service implementation is located in `grounding-service-SERVER/` and has its own `requirements.txt` with GPU dependencies (PyTorch, flash-attn).

### Planner

Set `PLANNER_BACKEND=local` or `PLANNER_BACKEND=openai` in `.env`.

- **`openai`**: uses the OpenAI API. Set `OPENAI_API_KEY` in `.env`. `PLANNER_MODEL` is optional (defaults to `gpt-5.4`).
- **`local`**: any vision-capable (multimodal) model behind an OpenAI-compatible API server. Set `PLANNER_MODEL=<model-name>` and `PLANNER_SERVICE_URL`. The server must support `response_format` with `json_schema` (llama.cpp b3670+, Ollama 0.5+, vLLM 0.4+).

### Utility Model

Used for memory compression and self-report collection (SUS/SEQ). Set via `UTILITY_BACKEND=local` or `UTILITY_BACKEND=openai`. When `local`, it reuses the same server endpoint as the planner. `UTILITY_MODEL` can be any text model; vision capability is not required.

---

## Screenshot Storage

APEX-UX supports two screenshot storage backends selected via `SCREENSHOT_STORAGE_TYPE` in `.env`.

### Local (Default)

Screenshots are stored inside a Docker volume on the local filesystem.

```env
SCREENSHOT_STORAGE_TYPE=local
LOCAL_STORAGE_PATH=storage/screenshots
```

Files are organized as:

```
storage/screenshots/
  {evaluation_id}/
    {task_run_id}/
      step_001.webp
      step_002.webp
      ...
```

### S3-Compatible Object Storage

Supports AWS S3, Cloudflare R2, MinIO, and any S3-compatible provider.

```env
SCREENSHOT_STORAGE_TYPE=s3

S3_ENDPOINT_URL=http://<your-storage-host>:9000
S3_ACCESS_KEY=your_access_key
S3_SECRET_KEY=your_secret_key
S3_BUCKET_NAME=bucket_name
S3_REGION=eu-central-1
S3_PUBLIC_URL_BASE=
```

The bucket must exist before starting the application. If `S3_PUBLIC_URL_BASE` is set, screenshot URLs are constructed as `{S3_PUBLIC_URL_BASE}/{object_key}`, which is useful for CDN delivery. Leave it empty to use internal `s3://` URIs.

---

## Environment Variables

All variables are documented in `.env.example`. The table below describes each one.

| Variable | Default | Description |
|---|---|---|
| `APP_PORT` | `5000` | Port the application listens on. |
| `LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| **Database** | | |
| `DB_HOST` | `localhost` | PostgreSQL host. |
| `DB_PORT` | `5432` | PostgreSQL port. |
| `DB_NAME` | `apex_ux` | Database name. |
| `DB_USER` | `apex` | Database user. |
| `DB_PASSWORD` | `apex_dev_password` | Database password. |
| `DATABASE_URL` | (none) | If set, overrides all other `DB_*` variables. Format: `postgresql+asyncpg://user:pass@host:port/db`. |
| `DB_POOL_SIZE` | `20` | SQLAlchemy connection pool size. Increase when running many parallel clones. |
| `DB_MAX_OVERFLOW` | `10` | Maximum connections allowed above `DB_POOL_SIZE`. |
| `ADMINER_PORT` | `8080` | Port for the Adminer database browser. |
| **Planner** | | |
| `PLANNER_BACKEND` | `openai` | Backend for the planner agent: `openai` or `local`. |
| `PLANNER_MODEL` | `gpt-5.4` | Model name. For `local`, must match the model loaded on the server. |
| `PLANNER_SERVICE_URL` | `http://host.docker.internal:8001` | URL of the local OpenAI-compatible planner server. Used when `PLANNER_BACKEND=local`. |
| `PLANNER_TEMPERATURE` | `0.7` | Sampling temperature for the planner and persona self-report. Higher values produce more varied, persona-authentic responses. |
| **Utility model** | | |
| `UTILITY_BACKEND` | `local` | Backend for the utility model: `openai` or `local`. |
| `UTILITY_MODEL` | `gpt-4o-mini` | Model name. Any text model works; vision is not required. |
| `UTILITY_TEMPERATURE` | `0.3` | Sampling temperature for utility tasks (memory compression). |
| **OpenAI** | | |
| `OPENAI_API_KEY` | (none) | Required when `PLANNER_BACKEND=openai` or `UTILITY_BACKEND=openai`. |
| **Grounding service** | | |
| `GROUNDING_SERVICE_URL` | `http://host.docker.internal:8000` | URL of the grounding vision model service. |
| `GROUNDING_MODEL` | `inclusionAI/UI-Venus-Ground-7B` | Grounding model identifier (as on Hugging Face). |
| `GROUNDING_MIN_PIXELS` | `2000000` | Minimum image pixel budget passed to the grounding model. Must match the server configuration. |
| `GROUNDING_MAX_PIXELS` | `4800000` | Maximum image pixel budget passed to the grounding model. Must match the server configuration. |
| **Evaluation behaviour** | | |
| `PARALLEL_CLONES` | `true` | `true`: all clones run concurrently (faster, higher resource use). `false`: clones run sequentially (easier to debug). |
| `ERROR_POLICY` | `continue_on_error` | `continue_on_error`: clone errors are logged and the evaluation continues. `fail_fast`: the first clone error stops the entire evaluation. |
| `STABILIZATION_WAIT` | `1000` | Milliseconds to wait after each browser action for the page to settle. Increase if screenshots capture mid-animation states. |
| **Memory / context** | | |
| `MEMORY_COMPRESSION_THRESHOLD` | `20` | Once a task's action history exceeds this step count, the oldest half is summarised by LLM to prevent context overflow. |
| `SUS_ACTION_CUMULATIVE_THRESHOLD` | `400` | If the total step count across all tasks exceeds this value, individual task logs that exceed their per-task share are summarised before SUS collection. |
| **Browser** | | |
| `HEADLESS` | `true` | `true`: browser runs headless. `false`: browser window is shown (needs additional set-up outside of this app to work). |
| **Screenshot storage** | | |
| `SCREENSHOT_STORAGE_TYPE` | `local` | Storage backend: `local` or `s3`. |
| `LOCAL_STORAGE_PATH` | `storage/screenshots` | Filesystem path for local screenshot storage (relative to project root). |
| `S3_ENDPOINT_URL` | `http://localhost:9000` | S3-compatible API base URL. Omit entirely when using AWS S3. |
| `S3_ACCESS_KEY` | (none) | S3 access key. |
| `S3_SECRET_KEY` | (none) | S3 secret key. |
| `S3_BUCKET_NAME` | (none) | Target S3 bucket (must exist before starting the application). |
| `S3_REGION` | `eu-central-1` | AWS region. For non-AWS providers this can be set to any value. |
| `S3_PUBLIC_URL_BASE` | (none) | Optional CDN base URL. If set, screenshot URLs are constructed as `{S3_PUBLIC_URL_BASE}/{object_key}`. |
