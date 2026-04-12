import io
import json
import os
import time
from pathlib import Path
from uuid import UUID

from src.log import initialize_logging, get_logger

initialize_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.connection import AsyncSessionLocal
from src.database.repositories import EvaluationRepository, PersonaRepository
from src.evaluation.api.router import router as evaluation_router
from src.evaluation.api.websocket import broadcast_progress
from src.evaluation.services.evaluation_service import EvaluationService
from src.evaluation.services.task_run_service import TaskRunService

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

PERSONAS_DIR = Path("src/ui/static/personas")
_ALLOWED_IMAGE_EXTS = {".webp", ".jpg", ".jpeg", ".png"}


async def _save_persona_image(image: UploadFile, persona_id: UUID) -> None:
    ext = Path(image.filename).suffix.lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        return
    content = await image.read()
    if not content:
        return
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(content))
        img.save(PERSONAS_DIR / f"{persona_id}.webp", "WEBP")
    except Exception:
        logger.warning("Failed to save persona image for %s — invalid image data", persona_id)

app.include_router(evaluation_router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    if request.url.path != "/health":
        logger.debug("%s %s", request.method, request.url.path)
    return await call_next(request)

templates = Jinja2Templates(directory="src/ui/templates")
templates.env.filters["format_duration"] = lambda s: f"{s // 60}m {s % 60}s" if s is not None else "-"


def _tojson(v):
    def _default(obj):
        from uuid import UUID
        from datetime import datetime
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


def _render_verification_rules(details, depth=0) -> Markup:
    if not details:
        return Markup('<div class="text-sm italic" style="color:#8A8A9A">No verification details available</div>')
    indent = depth * 20
    t = details.get("type")
    passed = details.get("passed")
    color = "#24C875" if passed else "#FF4D4D"
    icon = "✓" if passed else "✗"

    if t == "url_contains":
        v = escape(details.get("value", ""))
        label = "PASSED" if passed else "FAILED"
        return Markup(f'<div class="flex items-start gap-2 py-2" style="margin-left:{indent}px">'
                      f'<span style="color:{color};font-weight:600;font-size:1.125rem">{icon}</span>'
                      f'<div><span class="text-sm font-medium" style="color:#2C2C3A">URL contains:</span>'
                      f'<code class="ml-2 px-2 py-0.5 rounded text-xs ibm-mono" style="background:#F8F8FA;border:1px solid #E4E4EC">{v}</code>'
                      f'<span class="ml-2 text-xs font-semibold" style="color:{color}">{label}</span></div></div>')

    if t == "text_any":
        texts = escape(", ".join(details.get("texts", [])))
        label = "PASSED" if passed else "FAILED"
        return Markup(f'<div class="flex items-start gap-2 py-2" style="margin-left:{indent}px">'
                      f'<span style="color:{color};font-weight:600;font-size:1.125rem">{icon}</span>'
                      f'<div><span class="text-sm font-medium" style="color:#2C2C3A">Page shows text:</span>'
                      f'<code class="ml-2 px-2 py-0.5 rounded text-xs ibm-mono" style="background:#F8F8FA;border:1px solid #E4E4EC">{texts}</code>'
                      f'<span class="ml-2 text-xs font-semibold" style="color:{color}">{label}</span></div></div>')

    if t in ("any_of", "all_of"):
        group_name = escape(details.get("name", "OR Group") if t == "any_of" else "AND Group")
        desc = "(any one must pass)" if t == "any_of" else "(all must pass)"
        border_color = "#24C875" if passed else "#FF4D4D"
        title_color = "#0A66FF" if t == "any_of" else "#090947"
        pass_label = f'{"✓ GROUP PASSED" if passed else "✗ GROUP FAILED"}'
        children = Markup("").join(
            _render_verification_rules(child, depth + 1)
            for child in details.get("children", [])
        )
        return Markup(f'<div class="border-l-2 pl-3 py-2" style="margin-left:{indent}px;border-color:{border_color}">'
                      f'<div class="text-sm font-semibold mb-2" style="color:{title_color}">{group_name} '
                      f'<span class="text-xs font-normal" style="color:#8A8A9A">{desc}</span> '
                      f'<span class="text-xs font-semibold" style="color:{color}">{pass_label}</span></div>'
                      f'{children}</div>')

    return Markup('<div class="text-sm italic" style="color:#8A8A9A">Unknown rule type</div>')


def _render_verification_rules_def(rules, depth=0) -> Markup:
    """Render verification rules definition (no pass/fail status — for PENDING tasks)."""
    if not rules:
        return Markup('<div class="text-sm italic" style="color:#8A8A9A">No verification rules defined</div>')
    indent = depth * 20
    t = rules.get("type")

    if t == "url_contains":
        v = escape(rules.get("value", ""))
        return Markup(f'<div class="flex items-start gap-2 py-2" style="margin-left:{indent}px">'
                      f'<span style="color:#8A8A9A;font-weight:600;font-size:1.125rem">○</span>'
                      f'<div><span class="text-sm font-medium" style="color:#2C2C3A">URL contains:</span>'
                      f'<code class="ml-2 px-2 py-0.5 rounded text-xs ibm-mono" style="background:#F8F8FA;border:1px solid #E4E4EC">{v}</code>'
                      f'</div></div>')

    if t == "text_any":
        texts = escape(", ".join(rules.get("texts", [])))
        return Markup(f'<div class="flex items-start gap-2 py-2" style="margin-left:{indent}px">'
                      f'<span style="color:#8A8A9A;font-weight:600;font-size:1.125rem">○</span>'
                      f'<div><span class="text-sm font-medium" style="color:#2C2C3A">Page shows text:</span>'
                      f'<code class="ml-2 px-2 py-0.5 rounded text-xs ibm-mono" style="background:#F8F8FA;border:1px solid #E4E4EC">{texts}</code>'
                      f'</div></div>')

    if t in ("any_of", "all_of"):
        group_name = escape(rules.get("name", "OR Group") if t == "any_of" else "AND Group")
        desc = "(any one must pass)" if t == "any_of" else "(all must pass)"
        title_color = "#0A66FF" if t == "any_of" else "#090947"
        children = Markup("").join(
            _render_verification_rules_def(child, depth + 1)
            for child in rules.get("conditions", [])
        )
        return Markup(f'<div class="border-l-2 pl-3 py-2" style="margin-left:{indent}px;border-color:#E4E4EC">'
                      f'<div class="text-sm font-semibold mb-2" style="color:{title_color}">{group_name} '
                      f'<span class="text-xs font-normal" style="color:#8A8A9A">{desc}</span></div>'
                      f'{children}</div>')

    return Markup('<div class="text-sm italic" style="color:#8A8A9A">Unknown rule type</div>')


templates.env.filters["tojson"] = _tojson
templates.env.globals["render_verification_rules"] = _render_verification_rules
templates.env.globals["render_verification_rules_def"] = _render_verification_rules_def


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


@partials_router.post("/evaluations/{evaluation_id}/start", response_class=HTMLResponse)
async def start_evaluation_partial(request: Request, evaluation_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await EvaluationService(db).start_evaluation(evaluation_id, broadcast_progress)
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return await _evaluations_template(request, db)


@partials_router.post("/evaluations/{evaluation_id}/abort", response_class=HTMLResponse)
async def abort_evaluation_partial(request: Request, evaluation_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        result = await EvaluationService(db).abort_evaluation(evaluation_id, broadcast_progress)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return await _evaluations_template(request, db)


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


@partials_router.delete("/evaluation/{evaluation_id}", response_class=Response)
async def delete_evaluation_from_detail(evaluation_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await EvaluationService(db).delete_evaluation(evaluation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return Response(status_code=200, headers={"HX-Redirect": "/"})


@partials_router.get("/persona/{persona_id}", response_class=HTMLResponse)
async def persona_partial(
    request: Request, persona_id: UUID, db: AsyncSession = Depends(get_db)
):
    persona = await PersonaRepository(db).get_by_id(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    return templates.TemplateResponse(request, "partials/persona_card.html", {"persona": persona})


@partials_router.get("/personas", response_class=HTMLResponse)
async def personas_pool_partial(request: Request, db: AsyncSession = Depends(get_db)):
    personas = await PersonaRepository(db).list_all()
    return templates.TemplateResponse(request, "partials/persona_pool.html", {"personas": personas})


@partials_router.delete("/personas/{persona_id}", response_class=HTMLResponse)
async def delete_persona_partial(
    request: Request, persona_id: UUID, db: AsyncSession = Depends(get_db)
):
    repo = PersonaRepository(db)
    ok, reason = await repo.delete(persona_id)
    if not ok:
        if reason == "in_use":
            raise HTTPException(status_code=409, detail="Persona is assigned to one or more evaluations")
        raise HTTPException(status_code=404, detail="Persona not found")
    img_path = PERSONAS_DIR / f"{persona_id}.webp"
    if img_path.exists():
        img_path.unlink()
    personas = await repo.list_all()
    return templates.TemplateResponse(request, "partials/persona_pool.html", {"personas": personas})


@partials_router.get("/task-run/{task_run_id}/verification", response_class=HTMLResponse)
async def verification_partial(
    request: Request, task_run_id: UUID, db: AsyncSession = Depends(get_db)
):
    task_run = await TaskRunService(db).get_task_run_detail(task_run_id)
    if not task_run:
        raise HTTPException(status_code=404, detail="Task run not found")
    return templates.TemplateResponse(request, "partials/verification.html", {"task_run": task_run})


@partials_router.get("/task-definition/{task_def_id}/verification-rules", response_class=HTMLResponse)
async def verification_rules_partial(
    request: Request, task_def_id: UUID, db: AsyncSession = Depends(get_db)
):
    task_def = await EvaluationRepository(db).get_task_definition(task_def_id)
    if not task_def:
        raise HTTPException(status_code=404, detail="Task definition not found")
    return templates.TemplateResponse(request, "partials/verification_rules.html", {
        "task_description": task_def.description,
        "verification_rules": task_def.verification_rules,
    })


@partials_router.get("/evaluation/{evaluation_id}/personas", response_class=HTMLResponse)
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
    return templates.TemplateResponse(request, "partials/evaluation_personas.html", {
        "evaluation": report["evaluation"],
        "personas":   list(tasks_by_clone.values()),
    })


app.include_router(partials_router)


# ============================================================================
# FRONTEND ROUTES
# ============================================================================

pages_router = APIRouter(tags=["pages"])


@pages_router.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse("src/ui/static/index.html")


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
        total = len(task_runs)
        passed = sum(
            1 for t in task_runs
            if t["verification_result"] == "PASS" or (t["status"] == "COMPLETED" and t["agent_done"])
        )
        ms = report["metrics_summary"]
        metrics = {
            "success_rate": f"{passed / total * 100:.0f}%",
            "tasks_passed": f"{passed}/{total}",
            "avg_steps":    f"{ms['avg_steps_per_task']:.1f}",
            "avg_seq":      f"{ms['avg_seq_score']:.1f}/7",
            "avg_sus":      f"{ms['avg_sus_score']:.0f}/100",
        }
    else:
        metrics = {k: "-" for k in ("success_rate", "tasks_passed", "avg_steps", "avg_seq", "avg_sus")}

    tasks_by_clone = {c["id"]: {"clone": c, "tasks": []} for c in report["clones"]}
    for t in task_runs:
        if t["clone_uuid"] in tasks_by_clone:
            tasks_by_clone[t["clone_uuid"]]["tasks"].append(t)

    return templates.TemplateResponse(request, "pages/evaluation.html", {
        "evaluation":       ev,
        "clones":           report["clones"],
        "task_definitions": report["task_definitions"],
        "personas":         list(tasks_by_clone.values()),
        "metrics":          metrics,
    })


@pages_router.get("/task-run/{task_run_id}", include_in_schema=False)
async def task_run_replay_page(
    request: Request, task_run_id: UUID, db: AsyncSession = Depends(get_db)
):
    task_run = await TaskRunService(db).get_task_run_detail(task_run_id)
    if not task_run:
        raise HTTPException(status_code=404, detail="Task run not found")
    return templates.TemplateResponse(request, "pages/task-run.html", {"task_run": task_run})


@pages_router.get("/create", include_in_schema=False)
async def create_evaluation_page(request: Request):
    return templates.TemplateResponse(request, "pages/create-edit.html", {"mode": "create", "eval_id": None})


@pages_router.get("/edit/{evaluation_id}", include_in_schema=False)
async def edit_evaluation_page(request: Request, evaluation_id: UUID):
    return templates.TemplateResponse(request, "pages/create-edit.html", {"mode": "edit", "eval_id": str(evaluation_id)})


@pages_router.get("/personas/new", include_in_schema=False)
async def new_persona_page(request: Request):
    return templates.TemplateResponse(request, "pages/persona-form.html", {
        "mode": "create", "persona": None, "error": None, "cache_bust": 0,
    })


@pages_router.get("/personas/{persona_id}/edit", include_in_schema=False)
async def edit_persona_page(request: Request, persona_id: UUID, db: AsyncSession = Depends(get_db)):
    persona = await PersonaRepository(db).get_by_id(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    return templates.TemplateResponse(request, "pages/persona-form.html", {
        "mode": "edit", "persona": persona, "error": None, "cache_bust": int(time.time()),
    })


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
        return templates.TemplateResponse(request, "pages/persona-form.html", {
            "mode": "create", "persona": None, "error": "All fields are required.", "cache_bust": 0,
        }, status_code=422)

    persona = await PersonaRepository(db).create(name, description, narrative)

    if image and image.filename:
        await _save_persona_image(image, persona.id)

    return RedirectResponse("/#personas", status_code=303)


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
        return templates.TemplateResponse(request, "pages/persona-form.html", {
            "mode": "edit", "persona": persona, "error": "All fields are required.", "cache_bust": int(time.time()),
        }, status_code=422)

    persona = await repo.update(persona_id, name, description, narrative)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    if image and image.filename:
        await _save_persona_image(image, persona_id)

    return RedirectResponse("/#personas", status_code=303)


app.include_router(pages_router)


# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok"}