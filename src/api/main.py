from uuid import UUID

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.evaluation.api.router import router as evaluation_router

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

pages_router = APIRouter(tags=["pages"])


@pages_router.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse("src/ui/static/index.html")


@pages_router.get("/evaluation/{evaluation_id}", include_in_schema=False)
async def evaluation_report_page(evaluation_id: UUID):
    return FileResponse("src/ui/static/evaluation.html")


@pages_router.get("/task-run/{task_run_id}", include_in_schema=False)
async def task_run_replay_page(task_run_id: UUID):
    return FileResponse("src/ui/static/task-run.html")


@pages_router.get("/create", include_in_schema=False)
async def create_evaluation_page():
    return FileResponse("src/ui/static/create.html")


@pages_router.get("/edit/{evaluation_id}", include_in_schema=False)
async def edit_evaluation_page(evaluation_id: UUID):
    return FileResponse("src/ui/static/edit.html")


app.include_router(pages_router)


# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok"}


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