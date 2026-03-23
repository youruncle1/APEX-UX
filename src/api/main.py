from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.connection import AsyncSessionLocal
from src.database.repositories import EvaluationRepository
from src.evaluation.api.router import router as evaluation_router
from src.evaluation.services.evaluation_service import EvaluationService

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

templates = Jinja2Templates(directory="src/ui/templates")
templates.env.filters["format_duration"] = lambda s: f"{s // 60}m {s % 60}s" if s is not None else "-"


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def _evaluations_template(request: Request, db: AsyncSession) -> HTMLResponse:
    evaluations = await EvaluationService(db).list_evaluations()
    return templates.TemplateResponse(
        request, "partials/evaluations.html", {"evaluations": evaluations}
    )


# ============================================================================
# PARTIALS
# ============================================================================

partials_router = APIRouter(prefix="/partials", tags=["partials"])


@partials_router.get("/evaluations", response_class=HTMLResponse)
async def evaluations_partial(request: Request, db: AsyncSession = Depends(get_db)):
    return await _evaluations_template(request, db)


@partials_router.post("/evaluations/{evaluation_id}/start", response_class=Response)
async def start_evaluation_partial(evaluation_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await EvaluationService(db).start_evaluation(evaluation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return Response(status_code=200, headers={"HX-Redirect": f"/evaluation/{evaluation_id}"})


@partials_router.post("/evaluations/{evaluation_id}/abort", response_class=Response)
async def abort_evaluation_partial(evaluation_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        result = await EvaluationService(db).abort_evaluation(evaluation_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return Response(status_code=200, headers={"HX-Redirect": f"/evaluation/{evaluation_id}"})


@partials_router.post("/evaluations/{evaluation_id}/copy", response_class=HTMLResponse)
async def copy_evaluation_partial(
    request: Request, evaluation_id: UUID, db: AsyncSession = Depends(get_db)
):
    new_eval = await EvaluationRepository(db).copy(evaluation_id)
    if not new_eval:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return await _evaluations_template(request, db)


@partials_router.delete("/evaluations/{evaluation_id}", response_class=HTMLResponse)
async def delete_evaluation_partial(
    request: Request, evaluation_id: UUID, db: AsyncSession = Depends(get_db)
):
    result = await EvaluationService(db).delete_evaluation(evaluation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return await _evaluations_template(request, db)


app.include_router(partials_router)


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