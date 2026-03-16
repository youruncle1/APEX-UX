from datetime import datetime
from uuid import UUID

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.evaluation.api.router import router as evaluation_router
from src.storage import create_screenshot_storage

# ============================================================================
# APPLICATION SETUP
# ============================================================================

app = FastAPI(
    title="APEX-UX API",
    description="Web UI API for APEX-UX automated website usability evaluation",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="src/ui/static"), name="static")

app.include_router(evaluation_router)


# ============================================================================
# FRONTEND ROUTES
# ============================================================================

@app.get("/")
async def dashboard():
    return FileResponse("src/ui/static/index.html")


@app.get("/evaluation/{evaluation_id}")
async def evaluation_report_page(evaluation_id: UUID):
    return FileResponse("src/ui/static/evaluation.html")


@app.get("/task-run/{task_run_id}")
async def task_run_replay_page(task_run_id: UUID):
    return FileResponse("src/ui/static/task-run.html")


@app.get("/create")
async def create_evaluation_page():
    return FileResponse("src/ui/static/create.html")


@app.get("/edit/{evaluation_id}")
async def edit_evaluation_page(evaluation_id: UUID):
    return FileResponse("src/ui/static/edit.html")


# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.get("/api/health")
async def health_check():
    storage_info = await create_screenshot_storage().get_storage_info()
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "storage": storage_info
    }


# ============================================================================
# STARTUP
# ============================================================================

@app.on_event("startup")
async def startup_event():
    print("=" * 60)
    print("APEX-UX Web UI Starting")
    print("=" * 60)
    print("Dashboard: http://localhost:8002")
    print("API Docs: http://localhost:8002/docs")
    print("=" * 60)