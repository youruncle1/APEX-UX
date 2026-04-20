import io
import json
import os
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.connection import AsyncSessionLocal
from src.database.repositories import EvaluationRepository, PersonaRepository
from src.evaluation.api.router import router as evaluation_router
from src.evaluation.api.websocket import broadcast_progress
from src.evaluation.services.evaluation_service import EvaluationService
from src.evaluation.services.task_run_service import TaskRunService
from src.log import initialize_logging, get_logger
from src.ui.template_helpers import render_verification_rules

# Logging

initialize_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)

# App Setup

app = FastAPI(
    title="APEX-UX API",
    description="Web UI API for APEX-UX automated website usability evaluation",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="src/ui/static"), name="static")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    if request.url.path != "/health":
        logger.debug("%s %s", request.method, request.url.path)
    return await call_next(request)


# Constants

PERSONAS_DIR = Path("src/ui/static/personas")  # Persona profile picture dir path
_ALLOWED_IMAGE_EXTS = {
    ".webp",
    ".jpg",
    ".jpeg",
    ".png",
}  # Allowed image extensions for persona prof. pic.

# Jinja2 Template engine setup

templates = Jinja2Templates(directory="src/ui/templates")

templates.env.filters["format_duration"] = lambda s: (
    f"{s // 60}m {s % 60}s" if s is not None else "-"
)


# custom Jinja2 _tojson filter
def _tojson(v):
    def _default(obj):
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, BaseModel):
            return obj.model_dump()
        raise TypeError(f"Type {type(obj)} not serializable")

    if isinstance(v, BaseModel):
        return v.model_dump_json()
    return json.dumps(v, default=_default)


templates.env.filters["tojson"] = _tojson
templates.env.globals["render_verification_rules"] = render_verification_rules

# Database


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


# Shared helpers


async def _save_persona_image(image: UploadFile, persona_id: UUID) -> None:
    ext = Path(image.filename).suffix.lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        return
    content = await image.read()
    if not content:
        return
    try:
        img = Image.open(io.BytesIO(content))
        img.save(PERSONAS_DIR / f"{persona_id}.webp", "WEBP")
    except Exception:
        logger.warning(
            "Failed to save persona image for %s — invalid image data", persona_id
        )


async def _evaluations_template(request: Request, db: AsyncSession) -> HTMLResponse:
    evaluations = await EvaluationService(db).list_evaluations()
    return templates.TemplateResponse(
        request, "partials/evaluations.html", {"evaluations": evaluations}
    )


# Partials router
# HTMX partial endpoints

partials_router = APIRouter(prefix="/partials", tags=["partials"])


# Evaluations list fragment
@partials_router.get("/evaluations", response_class=HTMLResponse)
async def evaluations_partial(request: Request, db: AsyncSession = Depends(get_db)):
    return await _evaluations_template(request, db)


# Start evaluation run, returns updated evaluations list
@partials_router.post("/evaluations/{evaluation_id}/start", response_class=HTMLResponse)
async def start_evaluation_partial(
    request: Request, evaluation_id: UUID, db: AsyncSession = Depends(get_db)
):
    result = await EvaluationService(db).start_evaluation(
        evaluation_id, broadcast_progress
    )
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return await _evaluations_template(request, db)


# Abort a running evaluation, returns updated evaluations list
@partials_router.post("/evaluations/{evaluation_id}/abort", response_class=HTMLResponse)
async def abort_evaluation_partial(
    request: Request, evaluation_id: UUID, db: AsyncSession = Depends(get_db)
):
    try:
        result = await EvaluationService(db).abort_evaluation(
            evaluation_id, broadcast_progress
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return await _evaluations_template(request, db)


# Copy (duplicate) evaluation config, returns updated evaluations list
@partials_router.post("/evaluations/{evaluation_id}/copy", response_class=HTMLResponse)
async def copy_evaluation_partial(
    request: Request, evaluation_id: UUID, db: AsyncSession = Depends(get_db)
):
    new_eval = await EvaluationRepository(db).copy(evaluation_id)
    if not new_eval:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return await _evaluations_template(request, db)


# Delete evaluation, returns updated evaluations list
@partials_router.delete("/evaluations/{evaluation_id}", response_class=HTMLResponse)
async def delete_evaluation_partial(
    request: Request, evaluation_id: UUID, db: AsyncSession = Depends(get_db)
):
    result = await EvaluationService(db).delete_evaluation(evaluation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return await _evaluations_template(request, db)


# Delete evaluation from evaluation detail/report page, redirects to dashboard
@partials_router.delete("/evaluation/{evaluation_id}", response_class=Response)
async def delete_evaluation_from_detail(
    evaluation_id: UUID, db: AsyncSession = Depends(get_db)
):
    result = await EvaluationService(db).delete_evaluation(evaluation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return Response(status_code=200, headers={"HX-Redirect": "/"})


# Single persona card fragment
@partials_router.get("/persona/{persona_id}", response_class=HTMLResponse)
async def persona_partial(
    request: Request, persona_id: UUID, db: AsyncSession = Depends(get_db)
):
    persona = await PersonaRepository(db).get_by_id(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    return templates.TemplateResponse(
        request, "partials/persona_card.html", {"persona": persona}
    )


# Full persona pool fragment
@partials_router.get("/personas", response_class=HTMLResponse)
async def personas_pool_partial(request: Request, db: AsyncSession = Depends(get_db)):
    personas = await PersonaRepository(db).list_all()
    return templates.TemplateResponse(
        request, "partials/persona_pool.html", {"personas": personas}
    )


# Delete persona and its profile image, returns updated persona pool
@partials_router.delete("/personas/{persona_id}", response_class=HTMLResponse)
async def delete_persona_partial(
    request: Request, persona_id: UUID, db: AsyncSession = Depends(get_db)
):
    repo = PersonaRepository(db)
    ok, reason = await repo.delete(persona_id)
    if not ok:
        if reason == "in_use":
            raise HTTPException(
                status_code=409, detail="Persona is assigned to one or more evaluations"
            )
        raise HTTPException(status_code=404, detail="Persona not found")
    img_path = PERSONAS_DIR / f"{persona_id}.webp"
    if img_path.exists():
        img_path.unlink()  # delete image
    personas = await repo.list_all()
    return templates.TemplateResponse(
        request, "partials/persona_pool.html", {"personas": personas}
    )


# Verification rules result detail for a completed task run
@partials_router.get(
    "/task-run/{task_run_id}/verification", response_class=HTMLResponse
)
async def verification_partial(
    request: Request, task_run_id: UUID, db: AsyncSession = Depends(get_db)
):
    task_run = await TaskRunService(db).get_task_run_detail(task_run_id)
    if not task_run:
        raise HTTPException(status_code=404, detail="Task run not found")
    return templates.TemplateResponse(
        request, "partials/verification.html", {"task_run": task_run}
    )


# Verification rules definitions detail for a pending task run
@partials_router.get(
    "/task-definition/{task_def_id}/verification-rules", response_class=HTMLResponse
)
async def verification_rules_partial(
    request: Request, task_def_id: UUID, db: AsyncSession = Depends(get_db)
):
    task_def = await EvaluationRepository(db).get_task_definition(task_def_id)
    if not task_def:
        raise HTTPException(status_code=404, detail="Task definition not found")
    return templates.TemplateResponse(
        request,
        "partials/verification_rules.html",
        {
            "task_description": task_def.description,
            "verification_rules": task_def.verification_rules,
        },
    )


# Persona+task grid fragment for the evaluation detail page
@partials_router.get(
    "/evaluation/{evaluation_id}/personas", response_class=HTMLResponse
)
async def evaluation_personas_partial(
    request: Request, evaluation_id: UUID, db: AsyncSession = Depends(get_db)
):
    report = await EvaluationService(db).get_evaluation_report(evaluation_id)
    if not report:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    tasks_by_clone = {c["id"]: {"clone": c, "tasks": []} for c in report["clones"]}
    for t in report["task_runs"]:
        if t["clone_uuid"] in tasks_by_clone:
            tasks_by_clone[t["clone_uuid"]]["tasks"].append(t)
    return templates.TemplateResponse(
        request,
        "partials/evaluation_personas.html",
        {
            "evaluation": report["evaluation"],
            "personas": list(tasks_by_clone.values()),
        },
    )


# Pages router
# Full-page SSR routes
# Jinja2 templates rendered server-side.

pages_router = APIRouter(tags=["pages"])


# Main dashboard (static file)
@pages_router.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse("src/ui/static/index.html")


# Evaluation detail/report page
# aggregates metrics, clones, task results
@pages_router.get("/evaluation/{evaluation_id}", include_in_schema=False)
async def evaluation_report_page(
    request: Request, evaluation_id: UUID, db: AsyncSession = Depends(get_db)
):
    report = await EvaluationService(db).get_evaluation_report(evaluation_id)
    if not report:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    ev = report["evaluation"]
    task_runs = report["task_runs"]

    if ev.status != "PENDING" and task_runs:
        # Compute summary metrics from completed task runs
        total = len(task_runs)
        passed = sum(
            1
            for t in task_runs
            if t["verification_result"] == "PASS"
            or (t["status"] == "COMPLETED" and t["agent_done"])
        )
        ms = report["metrics_summary"]
        t = ms["avg_time_on_task_seconds"]
        avg_time_fmt = (
            f"{t // 60}m {t % 60}s"
            if t is not None and t >= 60
            else (f"{t}s" if t is not None else "-")
        )
        avg_lostness = ms["avg_lostness"]
        metrics = {
            "success_rate": f"{passed / total * 100:.0f}%",
            "tasks_passed": f"{passed}/{total}",
            "avg_steps": f"{ms['avg_steps_per_task']:.1f}",
            "avg_seq": f"{ms['avg_seq_score']:.1f}/7",
            "avg_sus": f"{ms['avg_sus_score']:.0f}/100",
            "avg_time": avg_time_fmt,
            "avg_lostness": f"{avg_lostness:.2f}" if avg_lostness is not None else None,
        }
    else:
        metrics = {
            k: "-"
            for k in (
                "success_rate",
                "tasks_passed",
                "avg_steps",
                "avg_seq",
                "avg_sus",
                "avg_time",
            )
        }
        metrics["avg_lostness"] = None

    tasks_by_clone = {c["id"]: {"clone": c, "tasks": []} for c in report["clones"]}
    for t in task_runs:
        if t["clone_uuid"] in tasks_by_clone:
            tasks_by_clone[t["clone_uuid"]]["tasks"].append(t)

    return templates.TemplateResponse(
        request,
        "pages/evaluation.html",
        {
            "evaluation": ev,
            "clones": report["clones"],
            "task_definitions": report["task_definitions"],
            "personas": list(tasks_by_clone.values()),
            "metrics": metrics,
        },
    )


# Task run page (action replay page)
@pages_router.get("/task-run/{task_run_id}", include_in_schema=False)
async def task_run_replay_page(
    request: Request, task_run_id: UUID, db: AsyncSession = Depends(get_db)
):
    task_run = await TaskRunService(db).get_task_run_detail(task_run_id)
    if not task_run:
        raise HTTPException(status_code=404, detail="Task run not found")
    return templates.TemplateResponse(
        request, "pages/task-run.html", {"task_run": task_run}
    )


# New evaluation form
@pages_router.get("/create", include_in_schema=False)
async def create_evaluation_page(request: Request):
    return templates.TemplateResponse(
        request, "pages/create-edit.html", {"mode": "create", "eval_id": None}
    )


# Edit existing evaluation form
@pages_router.get("/edit/{evaluation_id}", include_in_schema=False)
async def edit_evaluation_page(request: Request, evaluation_id: UUID):
    return templates.TemplateResponse(
        request,
        "pages/create-edit.html",
        {"mode": "edit", "eval_id": str(evaluation_id)},
    )


# New persona form
@pages_router.get("/personas/new", include_in_schema=False)
async def new_persona_page(request: Request):
    return templates.TemplateResponse(
        request,
        "pages/persona-form.html",
        {
            "mode": "create",
            "persona": None,
            "error": None,
            "cache_bust": 0,
        },
    )


# Edit persona form
@pages_router.get("/personas/{persona_id}/edit", include_in_schema=False)
async def edit_persona_page(
    request: Request, persona_id: UUID, db: AsyncSession = Depends(get_db)
):
    persona = await PersonaRepository(db).get_by_id(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    return templates.TemplateResponse(
        request,
        "pages/persona-form.html",
        {
            "mode": "edit",
            "persona": persona,
            "error": None,
            "cache_bust": int(time.time()),  # bust cached persona image on edit
        },
    )


# Create persona form submit
@pages_router.post("/personas", include_in_schema=False)
async def create_persona(
    request: Request,
    name: str = Form(...),
    description: str = Form(...),
    narrative: str = Form(...),
    image: UploadFile | None = File(default=None),
    db: AsyncSession = Depends(get_db),
):
    name, description, narrative = name.strip(), description.strip(), narrative.strip()
    if not name or not description or not narrative:
        return templates.TemplateResponse(
            request,
            "pages/persona-form.html",
            {
                "mode": "create",
                "persona": None,
                "error": "All fields are required.",
                "cache_bust": 0,
            },
            status_code=422,
        )

    persona = await PersonaRepository(db).create(name, description, narrative)

    if image and image.filename:
        await _save_persona_image(image, persona.id)

    return RedirectResponse("/#personas", status_code=303)


# Update persona form submit
@pages_router.post("/personas/{persona_id}", include_in_schema=False)
async def update_persona(
    request: Request,
    persona_id: UUID,
    name: str = Form(...),
    description: str = Form(...),
    narrative: str = Form(...),
    image: UploadFile | None = File(default=None),
    db: AsyncSession = Depends(get_db),
):
    name, description, narrative = name.strip(), description.strip(), narrative.strip()
    repo = PersonaRepository(db)

    if not name or not description or not narrative:
        persona = await repo.get_by_id(persona_id)
        return templates.TemplateResponse(
            request,
            "pages/persona-form.html",
            {
                "mode": "edit",
                "persona": persona,
                "error": "All fields are required.",
                "cache_bust": int(time.time()),
            },
            status_code=422,
        )

    persona = await repo.update(persona_id, name, description, narrative)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    if image and image.filename:
        await _save_persona_image(image, persona_id)

    return RedirectResponse("/#personas", status_code=303)


# --- Router registration ---

app.include_router(evaluation_router)
app.include_router(partials_router)
app.include_router(pages_router)

# --- Health ---


@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok"}
