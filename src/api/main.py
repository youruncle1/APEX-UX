import asyncio
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.connection import AsyncSessionLocal
from src.database.models import (
    Evaluation, EvaluationStatus, EvaluationClone,
    TaskDefinition, TaskRun, TaskRunStatus, TaskRunMetrics,
    ActionRecord, VerificationResult
)
from src.storage import create_screenshot_storage
from src.evaluation.runner import EvaluationRunner

# ============================================================================
# APPLICATION SETUP
# ============================================================================

app = FastAPI(
    title="APEX-UX API",
    description="Web UI API for APEX-UX automated website usability evaluation",
    version="1.0.0"
)

# CORS 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="src/ui/static"), name="static")

# Screenshot storage
screenshot_storage = create_screenshot_storage()

# WebSocket connections for live progress updates
active_connections: dict[UUID, list[WebSocket]] = {}

# Cancellation tokens for running evaluations
evaluation_cancellation: dict[UUID, bool] = {}


# ============================================================================
# DATABASE DEPENDENCY
# ============================================================================

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


# ============================================================================
# PYDANTIC MODELS (Request/Response Schemas)
# ============================================================================

class SEQResponse(BaseModel):
    score: float
    question: str
    scale: str
    reasoning: str


class SUSItemResponse(BaseModel):
    item_number: int
    question: str
    rating: int
    scale: str


class SUSResponse(BaseModel):
    score: float
    responses: List[SUSItemResponse]
    reasoning: str


class TaskDefinitionCreate(BaseModel):
    description: str
    base_url_override: Optional[str] = None
    max_steps: int = 25
    verification_rules: Optional[dict] = None


class EvaluationCreate(BaseModel):
    name: Optional[str] = None
    base_url: str
    clone_count: int = 3  # TODO: select specific clones
    tasks: List[TaskDefinitionCreate]


class EvaluationSummary(BaseModel):
    id: UUID
    name: Optional[str]
    base_url: str
    status: str
    created_at: datetime
    finished_at: Optional[datetime]
    task_count: int
    clone_count: int
    duration_seconds: Optional[int]


class TaskRunSummary(BaseModel):
    id: UUID
    task_description: str
    clone_id: str
    status: str
    steps_taken: int
    seq_score: Optional[float] = None
    seq_reasoning: Optional[str] = None  
    seq_response: Optional[SEQResponse] = None  
    verification_result: Optional[str] = None
    agent_done: bool


class EvaluationReport(BaseModel):
    evaluation: EvaluationSummary
    clones: List[dict] 
    task_runs: List[TaskRunSummary]
    metrics_summary: dict

class ActionRecordDetail(BaseModel):
    id: UUID
    step_number: int
    action_type: str
    target_description: Optional[str]
    input_value: Optional[str]
    reasoning: Optional[str]
    bounding_box: Optional[dict]
    click_coordinates: Optional[dict]
    execution_success: bool
    execution_feedback: Optional[str]
    timestamp: datetime
    screenshot_url: Optional[str]


class TaskRunDetail(BaseModel):
    id: UUID
    task_description: str
    clone_id: str
    status: str
    agent_done: bool
    verification_result: Optional[str]
    verification_evidence: Optional[str]
    verification_rules: Optional[dict] = None  
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    metrics: Optional[dict]
    actions: List[ActionRecordDetail]
    outcome_mismatches: Optional[List[dict]] = None


# ============================================================================
# FRONTEND ROUTES (Serve HTML Pages)
# ============================================================================

@app.get("/")
async def dashboard():
    """Serve dashboard page"""
    return FileResponse("src/ui/static/index.html")


@app.get("/evaluation/{evaluation_id}")
async def evaluation_report_page(evaluation_id: UUID):
    """Serve evaluation report page"""
    return FileResponse("src/ui/static/evaluation.html")


@app.get("/task-run/{task_run_id}")
async def task_run_replay_page(task_run_id: UUID):
    """Serve task run replay page"""
    return FileResponse("src/ui/static/task-run.html")


@app.get("/create")
async def create_evaluation_page():
    """Serve evaluation creation page"""
    return FileResponse("src/ui/static/create.html")


# ============================================================================
# API ROUTES - EVALUATIONS
# ============================================================================

@app.get("/api/evaluations", response_model=List[EvaluationSummary])
async def list_evaluations(db: AsyncSession = Depends(get_db)):
    """
    List all evaluations with summary statistics.
    Used by dashboard page.
    """
    
    result = await db.execute(
        select(Evaluation)
        .options(selectinload(Evaluation.clones), selectinload(Evaluation.tasks))
        .order_by(Evaluation.created_at.desc())
    )
    evaluations = result.scalars().all()
    
    summaries = []
    for eval in evaluations:
        # Calculate duration if finished
        duration = None
        if eval.finished_at and eval.created_at:
            duration = int((eval.finished_at - eval.created_at).total_seconds())
        
        summaries.append(EvaluationSummary(
            id=eval.id,
            name=eval.name,
            base_url=eval.base_url,
            status=eval.status.value,
            created_at=eval.created_at,
            finished_at=eval.finished_at,
            task_count=len(eval.tasks),
            clone_count=len(eval.clones),
            duration_seconds=duration
        ))
    
    return summaries


@app.post("/api/evaluations", response_model=dict)
async def create_evaluation(
    data: EvaluationCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new evaluation with tasks.
    Returns evaluation ID for starting.
    """
    
    # Create evaluation
    evaluation = Evaluation(
        name=data.name,
        base_url=data.base_url,
        status=EvaluationStatus.PENDING
    )
    db.add(evaluation)
    await db.flush()  # Get evaluation.id
    
    # Create placeholder clones (future: select specific personas)
    for i in range(data.clone_count):
        clone = EvaluationClone(
            evaluation_id=evaluation.id,
            clone_id=f"placeholder_clone_{i+1}",
            persona_cache={
                "name": f"Placeholder Persona {i+1}",
                "description": "Generic user persona "
            }
        )
        db.add(clone)
    
    # Create task definitions
    for task_data in data.tasks:
        task = TaskDefinition(
            evaluation_id=evaluation.id,
            description=task_data.description,
            base_url_override=task_data.base_url_override,
            max_steps=task_data.max_steps,
            verification_rules=task_data.verification_rules
        )
        db.add(task)
    
    await db.commit()
    await db.refresh(evaluation)
    
    return {
        "evaluation_id": str(evaluation.id),
        "status": "created",
        "message": f"Evaluation created with {data.clone_count} clones and {len(data.tasks)} tasks"
    }

@app.post("/api/evaluations/{evaluation_id}/start")
async def start_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    Start an evaluation run.
    Returns immediately and runs evaluation in background.
    WebSocket provides live progress updates.
    """
    
    # Verify evaluation exists
    result = await db.execute(
        select(Evaluation).where(Evaluation.id == evaluation_id)
    )
    evaluation = result.scalar_one_or_none()
    
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    
    if evaluation.status != EvaluationStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Evaluation already {evaluation.status.value}"
        )
    
    # Initialize cancellation token
    evaluation_cancellation[evaluation_id] = False
    
    # Start evaluation in background
    asyncio.create_task(run_evaluation_background(evaluation_id))
    
    return {
        "evaluation_id": str(evaluation_id),
        "status": "started",
        "message": "Evaluation started in background. Connect to WebSocket for progress."
    }


@app.post("/api/evaluations/{evaluation_id}/abort")
async def abort_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    Request abortion of a running evaluation.
    Sets cancellation flag - actual stop happens between tasks.
    """
    
    # Verify evaluation exists
    result = await db.execute(
        select(Evaluation).where(Evaluation.id == evaluation_id)
    )
    evaluation = result.scalar_one_or_none()
    
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    
    if evaluation.status != EvaluationStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot abort evaluation with status {evaluation.status.value}"
        )
    
    # Set cancellation flag
    evaluation_cancellation[evaluation_id] = True
    
    # Broadcast abort request
    await broadcast_progress(evaluation_id, {
        "type": "status",
        "status": "ABORTING",
        "message": "Abort requested - will stop after current task..."
    })
    
    return {
        "evaluation_id": str(evaluation_id),
        "status": "aborting",
        "message": "Abort requested. Evaluation will stop after current task completes."
    }


async def run_evaluation_background(evaluation_id: UUID):
    """
    Run evaluation in background and broadcast progress via WebSocket.
    Checks for cancellation between tasks.
    """
    
    runner = EvaluationRunner(error_policy="continue_on_error")
    
    try:
        # Broadcast start
        await broadcast_progress(evaluation_id, {
            "type": "status",
            "status": "RUNNING",
            "message": "Evaluation started"
        })
        
        # Check if cancelled before starting
        if evaluation_cancellation.get(evaluation_id, False):
            await mark_evaluation_aborted(evaluation_id)
            return
        
        # Run evaluation (this will take a while)
        # TODO: Modify runner to accept cancellation callback
        await runner.run(evaluation_id)
        
        # Broadcast completion
        await broadcast_progress(evaluation_id, {
            "type": "status",
            "status": "COMPLETED",
            "message": "Evaluation completed successfully"
        })
        
        # Clean up cancellation token
        if evaluation_id in evaluation_cancellation:
            del evaluation_cancellation[evaluation_id]
    
    except Exception as e:
        # Broadcast error
        await broadcast_progress(evaluation_id, {
            "type": "error",
            "message": f"Evaluation failed: {str(e)}"
        })
        
        # Clean up cancellation token
        if evaluation_id in evaluation_cancellation:
            del evaluation_cancellation[evaluation_id]


async def mark_evaluation_aborted(evaluation_id: UUID):
    """Mark evaluation as aborted in database"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Evaluation).where(Evaluation.id == evaluation_id)
        )
        evaluation = result.scalar_one_or_none()
        
        if evaluation:
            evaluation.status = EvaluationStatus.ABORTED
            evaluation.finished_at = datetime.utcnow()
            await session.commit()
            
            await broadcast_progress(evaluation_id, {
                "type": "status",
                "status": "ABORTED",
                "message": "Evaluation aborted by user"
            })
    
    # Clean up cancellation token
    if evaluation_id in evaluation_cancellation:
        del evaluation_cancellation[evaluation_id]

@app.get("/api/evaluation/{evaluation_id}", response_model=EvaluationReport)
async def get_evaluation_report(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    Get complete evaluation report with all clones and task runs.
    Includes enriched SUS/SEQ data with questions and scales.
    """
    
    # Load evaluation with all relationships
    result = await db.execute(
        select(Evaluation)
        .where(Evaluation.id == evaluation_id)
        .options(
            selectinload(Evaluation.clones).selectinload(EvaluationClone.task_runs),
            selectinload(Evaluation.tasks)
        )
    )
    evaluation = result.scalar_one_or_none()
    
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    
    # Build evaluation summary
    duration = None
    if evaluation.finished_at and evaluation.created_at:
        duration = int((evaluation.finished_at - evaluation.created_at).total_seconds())
    
    eval_summary = EvaluationSummary(
        id=evaluation.id,
        name=evaluation.name,
        base_url=evaluation.base_url,
        status=evaluation.status.value,
        created_at=evaluation.created_at,
        finished_at=evaluation.finished_at,
        task_count=len(evaluation.tasks),
        clone_count=len(evaluation.clones),
        duration_seconds=duration
    )
    
    # SUS Questions (constants)
    SUS_ITEMS = [
        "I think that I would like to use this website frequently.",
        "I found the website unnecessarily complex.",
        "I thought the website was easy to use.",
        "I think that I would need the support of a technical person to be able to use this website.",
        "I found the various functions in this website were well integrated.",
        "I thought there was too much inconsistency in this website.",
        "I would imagine that most people would learn to use this website very quickly.",
        "I found the website very cumbersome to use.",
        "I felt very confident using the website.",
        "I needed to learn a lot of things before I could get going with this website."
    ]
    
    # Build clone summaries with ENRICHED SUS data
    clones_data = []
    for clone in evaluation.clones:
        clone_dict = {
            "id": str(clone.id),
            "clone_id": clone.clone_id,
            "persona_name": clone.persona_cache.get("name", "Unknown"),
            "sus_score": clone.sus_score,
            "sus_ratings": clone.sus_ratings,  # Raw ratings array
            "sus_reasoning": clone.sus_reasoning,
            "sus_response": None
        }
        
        # Enrich SUS data with questions if available
        if clone.sus_score is not None and clone.sus_ratings:
            enriched_responses = []
            for i, rating in enumerate(clone.sus_ratings):
                enriched_responses.append({
                    "item_number": i + 1,
                    "question": SUS_ITEMS[i],
                    "rating": rating,
                    "scale": "1 (Strongly Disagree) to 5 (Strongly Agree)"
                })
            
            clone_dict["sus_response"] = {
                "score": clone.sus_score,
                "responses": enriched_responses,
                "reasoning": clone.sus_reasoning
            }
        
        clones_data.append(clone_dict)
    
    # Build task run summaries with ENRICHED SEQ data
    task_runs_data = []
    all_metrics = []
    
    for clone in evaluation.clones:
        for task_run in clone.task_runs:
            # Load metrics
            metrics_result = await db.execute(
                select(TaskRunMetrics)
                .where(TaskRunMetrics.task_run_id == task_run.id)
            )
            metrics = metrics_result.scalar_one_or_none()
            
            # Load task definition
            task_def_result = await db.execute(
                select(TaskDefinition)
                .where(TaskDefinition.id == task_run.task_definition_id)
            )
            task_def = task_def_result.scalar_one_or_none()
            
            steps_taken = metrics.steps_taken if metrics else 0
            seq_score = metrics.seq_score if metrics else None
            seq_reasoning = metrics.seq_reasoning if metrics else None
            
            task_runs_data.append({
                "id": str(task_run.id),
                "task_description": task_def.description if task_def else "Unknown",
                "clone_id": clone.clone_id,
                "status": task_run.status.value,
                "steps_taken": steps_taken,
                "seq_score": seq_score,
                "seq_reasoning": seq_reasoning,  # Add reasoning
                "verification_result": task_run.verification_result.value if task_run.verification_result else None,
                "agent_done": task_run.agent_done
            })
            
            if metrics:
                all_metrics.append(metrics)
    
    # Calculate aggregate metrics
    total_tasks = len(task_runs_data)
    successful_tasks = sum(1 for tr in task_runs_data if tr["status"] == "VERIFIED")
    
    avg_steps = 0
    avg_seq = 0
    avg_sus = 0
    
    if all_metrics:
        avg_steps = sum(m.steps_taken for m in all_metrics) / len(all_metrics)
        seq_scores = [m.seq_score for m in all_metrics if m.seq_score is not None]
        if seq_scores:
            avg_seq = sum(seq_scores) / len(seq_scores)
    
    sus_scores = [c.sus_score for c in evaluation.clones if c.sus_score is not None]
    if sus_scores:
        avg_sus = sum(sus_scores) / len(sus_scores)
    
    metrics_summary = {
        "total_tasks": total_tasks,
        "successful_tasks": successful_tasks,
        "success_rate": round(successful_tasks / total_tasks, 2) if total_tasks > 0 else 0,
        "avg_steps_per_task": round(avg_steps, 1),
        "avg_seq_score": round(avg_seq, 1),
        "avg_sus_score": round(avg_sus, 1)
    }
    
    return {
        "evaluation": eval_summary,
        "clones": clones_data,
        "task_runs": task_runs_data,
        "metrics_summary": metrics_summary
    }

@app.get("/edit/{evaluation_id}")
async def edit_evaluation_page(evaluation_id: UUID):
    """Serve evaluation edit page"""
    return FileResponse("src/ui/static/edit.html")

@app.get("/api/evaluation/{evaluation_id}/editable")
async def get_editable_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Get evaluation data for editing (only if PENDING)"""
    result = await db.execute(
        select(Evaluation)
        .where(Evaluation.id == evaluation_id)
        .options(selectinload(Evaluation.tasks), selectinload(Evaluation.clones))
    )
    evaluation = result.scalar_one_or_none()
    
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    
    if evaluation.status != EvaluationStatus.PENDING:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot edit evaluation with status {evaluation.status.value}"
        )
    
    return {
        "id": str(evaluation.id),
        "name": evaluation.name,
        "base_url": evaluation.base_url,
        "clone_count": len(evaluation.clones),
        "tasks": [
            {
                "description": task.description,
                "max_steps": task.max_steps,
                "base_url_override": task.base_url_override,
                "verification_rules": task.verification_rules
            }
            for task in evaluation.tasks
        ]
    }

from sqlalchemy import delete

@app.put("/api/evaluations/{evaluation_id}")
async def update_evaluation(
    evaluation_id: UUID,
    data: EvaluationCreate,  # Reuse existing Pydantic model
    db: AsyncSession = Depends(get_db)
):
    """Update evaluation (only if PENDING)"""
    result = await db.execute(
        select(Evaluation)
        .where(Evaluation.id == evaluation_id)
    )
    evaluation = result.scalar_one_or_none()
    
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    
    if evaluation.status != EvaluationStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot edit evaluation with status {evaluation.status.value}"
        )
    
    # Update basic fields
    evaluation.name = data.name
    evaluation.base_url = data.base_url
    
    # Delete old tasks
    await db.execute(
        delete(TaskDefinition).where(TaskDefinition.evaluation_id == evaluation_id)
    )
    
    # Delete old clones
    await db.execute(
        delete(EvaluationClone).where(EvaluationClone.evaluation_id == evaluation_id)
    )
    
    # Create new clones
    for i in range(data.clone_count):
        clone = EvaluationClone(
            evaluation_id=evaluation.id,
            clone_id=f"placeholder_clone_{i+1}",
            persona_cache={"name": f"Placeholder Persona {i+1}", "description": "Generic user"}
        )
        db.add(clone)
    
    # Create new tasks
    for task_data in data.tasks:
        task = TaskDefinition(
            evaluation_id=evaluation.id,
            description=task_data.description,
            base_url_override=task_data.base_url_override,
            max_steps=task_data.max_steps,
            verification_rules=task_data.verification_rules
        )
        db.add(task)
    
    await db.commit()
    
    return {
        "evaluation_id": str(evaluation.id),
        "status": "updated",
        "message": "Evaluation updated successfully"
    }

@app.delete("/api/evaluation/{evaluation_id}")
async def delete_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    Delete evaluation and all associated data.
    Also deletes screenshots from storage.
    """
    
    # Load evaluation
    result = await db.execute(
        select(Evaluation).where(Evaluation.id == evaluation_id)
    )
    evaluation = result.scalar_one_or_none()
    
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    
    # Delete screenshots from storage
    try:
        await screenshot_storage.delete_evaluation(str(evaluation_id))
    except Exception as e:
        print(f"[API] Failed to delete screenshots: {e}")
    
    # Delete from database (cascades to all related records)
    await db.delete(evaluation)
    await db.commit()
    
    return {
        "evaluation_id": str(evaluation_id),
        "status": "deleted"
    }
    
# ============================================================================
# API ROUTES - TASK RUNS
# ============================================================================

@app.get("/api/task-run/{task_run_id}", response_model=TaskRunDetail)
async def get_task_run_detail(
    task_run_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    Get complete task run details with all action records.
    Used by task run replay page.
    """
    
    # Load task run with all relationships
    result = await db.execute(
        select(TaskRun)
        .where(TaskRun.id == task_run_id)
        .options(
            selectinload(TaskRun.task_definition),
            selectinload(TaskRun.clone),
            selectinload(TaskRun.metrics),
            selectinload(TaskRun.actions)
        )
    )
    task_run = result.scalar_one_or_none()
    
    if not task_run:
        raise HTTPException(status_code=404, detail="Task run not found")

    # Build metrics dict with FULL SEQ response
    metrics_dict = None
    if task_run.metrics:
        metrics_dict = {
            "steps_taken": task_run.metrics.steps_taken,
            "failed_action_count": task_run.metrics.failed_action_count,
            "outcome_mismatch_count": task_run.metrics.outcome_mismatch_count,
            "backtracking_count": task_run.metrics.backtracking_count,
            "seq_score": task_run.metrics.seq_score,
            "seq_reasoning": task_run.metrics.seq_reasoning
        }
    
    # Build action records
    actions_data = []
    for action in sorted(task_run.actions, key=lambda a: a.step_number):
        actions_data.append(ActionRecordDetail(
            id=action.id,
            step_number=action.step_number,
            action_type=action.action_type,
            target_description=action.target_description,
            input_value=action.input_value,
            reasoning=action.reasoning,
            bounding_box=action.bounding_box,
            click_coordinates=action.click_coordinates,
            execution_success=action.execution_success,
            execution_feedback=action.execution_feedback,
            timestamp=action.timestamp,
            screenshot_url=action.screenshot
        ))
    
    return TaskRunDetail(
        id=task_run.id,
        task_description=task_run.task_definition.description,
        clone_id=task_run.clone.clone_id,
        status=task_run.status.value,
        agent_done=task_run.agent_done,
        verification_result=task_run.verification_result.value if task_run.verification_result else None,
        verification_evidence=task_run.verification_evidence,
        verification_rules=task_run.task_definition.verification_rules,  
        started_at=task_run.started_at,
        finished_at=task_run.finished_at,
        metrics=metrics_dict,
        actions=actions_data,
        outcome_mismatches=task_run.final_outcome_mismatches
    )


# ============================================================================
# API ROUTES - SCREENSHOTS
# ============================================================================

@app.get("/api/screenshot")
async def get_screenshot(url: str):
    """
    Retrieve screenshot as base64 PNG.
    Proxies request to screenshot storage (local or S3).
    """
    
    try:
        base64_data = await screenshot_storage.get(url)
        return JSONResponse({
            "data": base64_data,
            "format": "base64_png"
        })
    
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve screenshot: {str(e)}")

# ============================================================================
# WEBSOCKET - LIVE PROGRESS UPDATES
# ============================================================================

@app.websocket("/ws/evaluation/{evaluation_id}")
async def evaluation_progress_websocket(
    websocket: WebSocket,
    evaluation_id: UUID
):
    """
    WebSocket endpoint for real-time evaluation progress updates.
    
    Client receives messages in format:
    {
        "type": "status" | "clone_start" | "task_start" | "task_complete" | "error",
        "data": { ... }
    }
    """
    
    await websocket.accept()
    
    # Register connection
    if evaluation_id not in active_connections:
        active_connections[evaluation_id] = []
    active_connections[evaluation_id].append(websocket)
    
    try:
        # Keep connection alive and handle client messages (if any)
        while True:
            data = await websocket.receive_text()
            # Echo back for keep-alive
            await websocket.send_text(json.dumps({"type": "pong"}))
    
    except WebSocketDisconnect:
        # Remove connection on disconnect
        active_connections[evaluation_id].remove(websocket)
        if not active_connections[evaluation_id]:
            del active_connections[evaluation_id]


async def broadcast_progress(evaluation_id: UUID, message: dict):
    """
    Broadcast progress message to all connected WebSocket clients.
    """
    
    if evaluation_id not in active_connections:
        return
    
    message_json = json.dumps(message)
    
    # Send to all connections, remove dead ones
    dead_connections = []
    for websocket in active_connections[evaluation_id]:
        try:
            await websocket.send_text(message_json)
        except:
            dead_connections.append(websocket)
    
    # Clean up dead connections
    for ws in dead_connections:
        active_connections[evaluation_id].remove(ws)


# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    
    storage_info = await screenshot_storage.get_storage_info()
    
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "storage": storage_info
    }


# ============================================================================
# STARTUP EVENT
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    print("=" * 60)
    print("APEX-UX Web UI Starting")
    print("=" * 60)
    print("Dashboard: http://localhost:8002")
    print("API Docs: http://localhost:8002/docs")
    print("=" * 60)