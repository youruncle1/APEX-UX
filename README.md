# Setup

## Prerequisites

- Docker and Docker Compose

## Getting Started

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Start the application:
   ```bash
   docker compose up
   ```

The database is initialized automatically on first run. The app is available at **http://localhost:5000** (if not configured otherwise).

## Model Services

For evaluations to work, two model services must be reachable from the container:

**Grounder** - always local vision model (UI-Venus-Ground-7B). Must be listening on `0.0.0.0:<port>`. Configure the URL|port in `.env` via `GROUNDING_SERVICE_URL`.

**Planner** - can be Qwen (local) or OpenAI. Set `PLANNER_BACKEND=qwen` or `PLANNER_BACKEND=openai` in `.env`.
- Qwen: must be listening on `0.0.0.0:<port>`, configure URL|port via `PLANNER_SERVICE_URL`
- OpenAI: set `OPENAI_API_KEY` in `.env`, no URL needed.

**Utility model** (memory compression, self-report) - set via `UTILITY_BACKEND=qwen` or `UTILITY_BACKEND=openai`. If set to `qwen`, it reuses the same endpoint as the Qwen planner.

---

# Screenshot Storage Configuration

APEX-UX supports two screenshot storage backends: **local filesystem** and **S3-compatible object storage**. The backend is selected via the `SCREENSHOT_STORAGE_TYPE` environment variable in `.env`.

---

## Option 1: Local Storage (Default)

Screenshots are stored on the local filesystem inside a Docker volume.

```env
SCREENSHOT_STORAGE_TYPE=local
LOCAL_STORAGE_PATH=storage/screenshots
```

`LOCAL_STORAGE_PATH` is relative to the project root. Screenshots are organized as:

```yaml
storage/screenshots/
  {evaluation_id}/
    {task_run_id}/
      step_001.webp
      step_002.webp
      ...
```

---

## Option 2: S3-Compatible Object Storage

APEX-UX can store screenshots in any S3-compatible object storage service, including AWS S3, Cloudflare R2, MinIO, and others.

### Environment variables

```env
SCREENSHOT_STORAGE_TYPE=s3

S3_ENDPOINT_URL=http://<your-storage-host>:9000
S3_ACCESS_KEY=your_access_key
S3_SECRET_KEY=your_secret_key
S3_BUCKET_NAME=bucket_name
S3_REGION=eu-central-1
S3_PUBLIC_URL_BASE=
```

| Variable | Description |
|---|---|
| `S3_ENDPOINT_URL` | The base URL of the S3-compatible API. If using AWS S3, delete this variable entirely. |
| `S3_ACCESS_KEY` | Access key / username for authentication. |
| `S3_SECRET_KEY` | Secret key / password for authentication. |
| `S3_BUCKET_NAME` | The name of the bucket where screenshots will be stored (bucket must exist before starting application) |
| `S3_REGION` | AWS region identifier. For non-AWS providers this can be set to any value (e.g. `eu-central-1`). |
| `S3_PUBLIC_URL_BASE` | Optional. If set, screenshot URLs returned by the API will be constructed as `{S3_PUBLIC_URL_BASE}/{object_key}`. Leave empty to use internal `s3://` URIs (default). |