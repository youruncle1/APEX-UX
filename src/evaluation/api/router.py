import json
from typing import List
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.connection import AsyncSessionLocal
from src.evaluation.api.websocket import active_connections, broadcast_progress
from src.evaluation.models.api_models import (
    EvaluationCreate,
    EvaluationSummary,
    EvaluationReport,
    TaskRunDetail,
    PersonaResponse,
)
from src.evaluation.services.evaluation_service import EvaluationService
from src.evaluation.services.task_run_service import TaskRunService
from src.storage import create_screenshot_storage
from src.log import get_logger

logger = get_logger(__name__)

# Router setup

router = APIRouter(prefix="/api", tags=["evaluations"])

screenshot_storage = create_screenshot_storage()


# Database


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


# Persona endpoints


@router.get("/personas", response_model=List[PersonaResponse])
async def list_personas(db: AsyncSession = Depends(get_db)):
    return await EvaluationService(db).list_personas()


# Evaluation endpoints


@router.get("/evaluations", response_model=List[EvaluationSummary])
async def list_evaluations(db: AsyncSession = Depends(get_db)):
    return await EvaluationService(db).list_evaluations()


@router.post("/evaluations", response_model=dict)
async def create_evaluation(
    data: EvaluationCreate,
    db: AsyncSession = Depends(get_db),
):
    return await EvaluationService(db).create_evaluation(data)


@router.post("/evaluations/{evaluation_id}/copy", response_model=dict)
async def copy_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await EvaluationService(db).copy_evaluation(evaluation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return result


# Runs EvaluationRunner as a background task, returns immediately
@router.post("/evaluations/{evaluation_id}/start")
async def start_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await EvaluationService(db).start_evaluation(
        evaluation_id, broadcast_progress
    )
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return result


# Sets cancellation flag, evaluation stops after the current steps complete
@router.post("/evaluations/{evaluation_id}/abort")
async def abort_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await EvaluationService(db).abort_evaluation(
            evaluation_id, broadcast_progress
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return result


@router.get("/evaluation/{evaluation_id}", response_model=EvaluationReport)
async def get_evaluation_report(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await EvaluationService(db).get_evaluation_report(evaluation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return result


# Returns evaluation config for the edit form, raises 400 if not PENDING
@router.get("/evaluation/{evaluation_id}/editable")
async def get_editable_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await EvaluationService(db).get_editable_evaluation(evaluation_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return result


# Replaces evaluation config, raises 400 if not PENDING
@router.put("/evaluations/{evaluation_id}")
async def update_evaluation(
    evaluation_id: UUID,
    data: EvaluationCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await EvaluationService(db).update_evaluation(evaluation_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return result


@router.delete("/evaluation/{evaluation_id}")
async def delete_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await EvaluationService(db).delete_evaluation(evaluation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return result


# Task run endpoint


@router.get("/task-run/{task_run_id}", response_model=TaskRunDetail)
async def get_task_run_detail(
    task_run_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await TaskRunService(db).get_task_run_detail(task_run_id)
    if not result:
        raise HTTPException(status_code=404, detail="Task run not found")
    return result


# Screenshot endpoint


# Fetches screenshot by storage URL and returns it as base64, cached for 24h
@router.get("/screenshot")
async def get_screenshot(url: str):
    try:
        base64_data = await screenshot_storage.get(url)
        return JSONResponse(
            {
                "data": base64_data,
                "format": "base64_png",
            },
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve screenshot: {str(e)}"
        )


# WebSocket endpoint


# Streams evaluation progress events to the client
@router.websocket("/ws/evaluation/{evaluation_id}")
async def evaluation_progress_websocket(
    websocket: WebSocket,
    evaluation_id: UUID,
):
    await websocket.accept()
    logger.debug("WebSocket connected: evaluation %s", evaluation_id)

    if evaluation_id not in active_connections:
        active_connections[evaluation_id] = []
    active_connections[evaluation_id].append(websocket)

    # Push current progress immediately so the client doesn't wait for the next broadcast
    percentage = await EvaluationService.get_evaluation_progress(evaluation_id)
    await websocket.send_text(
        json.dumps({"type": "progress", "percentage": percentage})
    )

    try:
        while True:
            await websocket.receive_text()
            await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        active_connections[evaluation_id].remove(websocket)
        if not active_connections[evaluation_id]:
            del active_connections[evaluation_id]
        logger.debug("WebSocket disconnected: evaluation %s", evaluation_id)
