import asyncio
import json
from datetime import datetime
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.connection import AsyncSessionLocal
from src.database.models import (
    Evaluation, EvaluationStatus, EvaluationClone,
    TaskDefinition, TaskRun, TaskRunMetrics,
)
from src.evaluation.models.api_models import (
    EvaluationCreate, EvaluationSummary, EvaluationReport,
    TaskRunDetail, ActionRecordDetail,
)
from src.storage import create_screenshot_storage

router = APIRouter(prefix="/api", tags=["evaluations"])

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
# WEBSOCKET HELPERS
# ============================================================================

async def broadcast_progress(evaluation_id: UUID, message: dict):
    if evaluation_id not in active_connections:
        return

    message_json = json.dumps(message)

    dead_connections = []
    for websocket in active_connections[evaluation_id]:
        try:
            await websocket.send_text(message_json)
        except:
            dead_connections.append(websocket)

    for ws in dead_connections:
        active_connections[evaluation_id].remove(ws)


# ============================================================================
# BACKGROUND TASK HELPERS
# ============================================================================

async def mark_evaluation_aborted(evaluation_id: UUID):
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

    if evaluation_id in evaluation_cancellation:
        del evaluation_cancellation[evaluation_id]


async def run_evaluation_background(evaluation_id: UUID):
    from src.evaluation.runner import EvaluationRunner

    runner = EvaluationRunner(error_policy="continue_on_error")

    try:
        await broadcast_progress(evaluation_id, {
            "type": "status",
            "status": "RUNNING",
            "message": "Evaluation started"
        })

        if evaluation_cancellation.get(evaluation_id, False):
            await mark_evaluation_aborted(evaluation_id)
            return

        await runner.run(evaluation_id)

        await broadcast_progress(evaluation_id, {
            "type": "status",
            "status": "COMPLETED",
            "message": "Evaluation completed successfully"
        })

        if evaluation_id in evaluation_cancellation:
            del evaluation_cancellation[evaluation_id]

    except Exception as e:
        await broadcast_progress(evaluation_id, {
            "type": "error",
            "message": f"Evaluation failed: {str(e)}"
        })

        if evaluation_id in evaluation_cancellation:
            del evaluation_cancellation[evaluation_id]


# ============================================================================
# EVALUATION ROUTES
# ============================================================================

@router.get("/evaluations", response_model=List[EvaluationSummary])
async def list_evaluations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Evaluation)
        .options(selectinload(Evaluation.clones), selectinload(Evaluation.tasks))
        .order_by(Evaluation.created_at.desc())
    )
    evaluations = result.scalars().all()

    summaries = []
    for eval in evaluations:
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


@router.post("/evaluations", response_model=dict)
async def create_evaluation(
    data: EvaluationCreate,
    db: AsyncSession = Depends(get_db)
):
    evaluation = Evaluation(
        name=data.name,
        base_url=data.base_url,
        status=EvaluationStatus.PENDING
    )
    db.add(evaluation)
    await db.flush()

    for i in range(data.clone_count):
        clone = EvaluationClone(
            evaluation_id=evaluation.id,
            clone_id=f"placeholder_clone_{i+1}",
            persona_cache={
                "name": f"Placeholder Persona {i+1}",
                "description": "Generic user persona"
            }
        )
        db.add(clone)

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


@router.post("/evaluations/{evaluation_id}/start")
async def start_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db)
):
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

    evaluation_cancellation[evaluation_id] = False
    asyncio.create_task(run_evaluation_background(evaluation_id))

    return {
        "evaluation_id": str(evaluation_id),
        "status": "started",
        "message": "Evaluation started in background. Connect to WebSocket for progress."
    }


@router.post("/evaluations/{evaluation_id}/abort")
async def abort_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db)
):
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

    evaluation_cancellation[evaluation_id] = True

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


@router.get("/evaluation/{evaluation_id}", response_model=EvaluationReport)
async def get_evaluation_report(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db)
):
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

    clones_data = []
    for clone in evaluation.clones:
        clone_dict = {
            "id": str(clone.id),
            "clone_id": clone.clone_id,
            "persona_name": clone.persona_cache.get("name", "Unknown"),
            "sus_score": clone.sus_score,
            "sus_ratings": clone.sus_ratings,
            "sus_reasoning": clone.sus_reasoning,
            "sus_response": None
        }

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

    task_runs_data = []
    all_metrics = []

    for clone in evaluation.clones:
        for task_run in clone.task_runs:
            metrics_result = await db.execute(
                select(TaskRunMetrics)
                .where(TaskRunMetrics.task_run_id == task_run.id)
            )
            metrics = metrics_result.scalar_one_or_none()

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
                "seq_reasoning": seq_reasoning,
                "verification_result": task_run.verification_result.value if task_run.verification_result else None,
                "agent_done": task_run.agent_done
            })

            if metrics:
                all_metrics.append(metrics)

    total_tasks = len(task_runs_data)
    successful_tasks = sum(
        1 for tr in task_runs_data
        if tr["status"] == "VERIFIED" or (tr["status"] == "COMPLETED" and tr["agent_done"])
    )

    avg_steps = avg_seq = avg_sus = 0

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


@router.get("/evaluation/{evaluation_id}/editable")
async def get_editable_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db)
):
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


@router.put("/evaluations/{evaluation_id}")
async def update_evaluation(
    evaluation_id: UUID,
    data: EvaluationCreate,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Evaluation).where(Evaluation.id == evaluation_id)
    )
    evaluation = result.scalar_one_or_none()

    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    if evaluation.status != EvaluationStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot edit evaluation with status {evaluation.status.value}"
        )

    evaluation.name = data.name
    evaluation.base_url = data.base_url

    await db.execute(
        delete(TaskDefinition).where(TaskDefinition.evaluation_id == evaluation_id)
    )
    await db.execute(
        delete(EvaluationClone).where(EvaluationClone.evaluation_id == evaluation_id)
    )

    for i in range(data.clone_count):
        clone = EvaluationClone(
            evaluation_id=evaluation.id,
            clone_id=f"placeholder_clone_{i+1}",
            persona_cache={"name": f"Placeholder Persona {i+1}", "description": "Generic user"}
        )
        db.add(clone)

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


@router.delete("/evaluation/{evaluation_id}")
async def delete_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Evaluation).where(Evaluation.id == evaluation_id)
    )
    evaluation = result.scalar_one_or_none()

    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    try:
        await screenshot_storage.delete_evaluation(str(evaluation_id))
    except Exception as e:
        print(f"[API] Failed to delete screenshots: {e}")

    await db.delete(evaluation)
    await db.commit()

    return {
        "evaluation_id": str(evaluation_id),
        "status": "deleted"
    }


# ============================================================================
# TASK RUN ROUTES
# ============================================================================

@router.get("/task-run/{task_run_id}", response_model=TaskRunDetail)
async def get_task_run_detail(
    task_run_id: UUID,
    db: AsyncSession = Depends(get_db)
):
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
        verification_rules=task_run.task_definition.verification_rules,
        verification_details=task_run.verification_details,
        started_at=task_run.started_at,
        finished_at=task_run.finished_at,
        metrics=metrics_dict,
        actions=actions_data,
        outcome_mismatches=task_run.final_outcome_mismatches
    )


# ============================================================================
# SCREENSHOT ROUTES
# ============================================================================

@router.get("/screenshot")
async def get_screenshot(url: str):
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
# WEBSOCKET
# ============================================================================

@router.websocket("/ws/evaluation/{evaluation_id}")
async def evaluation_progress_websocket(
    websocket: WebSocket,
    evaluation_id: UUID
):
    await websocket.accept()

    if evaluation_id not in active_connections:
        active_connections[evaluation_id] = []
    active_connections[evaluation_id].append(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        active_connections[evaluation_id].remove(websocket)
        if not active_connections[evaluation_id]:
            del active_connections[evaluation_id]