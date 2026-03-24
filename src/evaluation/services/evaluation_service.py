import asyncio
from datetime import datetime
from typing import Optional, List
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.connection import AsyncSessionLocal
from src.database.models import (
    ActionRecord, EvaluationClone, EvaluationStatus,
    TaskDefinition, TaskRun,
)
from src.database.repositories import EvaluationRepository
from src.evaluation.api.websocket import (
    active_connections,
    evaluation_cancellation,
    broadcast_progress,
)
from src.evaluation.models.api_models import (
    EvaluationCreate, EvaluationSummary, EvaluationReport,
)
from src.evaluation.prompts import SUS_ITEMS
from src.storage import create_screenshot_storage


class EvaluationService:

    def __init__(self, session: AsyncSession):
        self.session = session
        self.eval_repo = EvaluationRepository(session)

    async def list_evaluations(self) -> List[EvaluationSummary]:
        evaluations = await self.eval_repo.list_all()

        summaries = []
        for eval in evaluations:
            duration = None
            if eval.finished_at and eval.started_at:
                duration = int((eval.finished_at - eval.started_at).total_seconds())

            summaries.append(EvaluationSummary(
                id=eval.id,
                name=eval.name,
                base_url=eval.base_url,
                status=eval.status.value,
                created_at=eval.created_at,
                finished_at=eval.finished_at,
                task_count=len(eval.tasks),
                clone_count=len(eval.clones),
                duration_seconds=duration,
            ))

        return summaries

    async def create_evaluation(self, data: EvaluationCreate) -> dict:
        evaluation = await self.eval_repo.create(
            name=data.name,
            base_url=data.base_url,
            browser=data.browser,
            viewport=data.viewport,
            clone_count=data.clone_count,
            tasks_data=[t.model_dump() for t in data.tasks],
        )

        return {
            "evaluation_id": str(evaluation.id),
            "status": "created",
            "message": f"Evaluation created with {data.clone_count} clones and {len(data.tasks)} tasks",
        }

    async def get_evaluation_report(self, evaluation_id: UUID) -> Optional[dict]:
        evaluation = await self.eval_repo.get_with_clones_and_tasks(evaluation_id)

        if not evaluation:
            return None

        duration = None
        if evaluation.finished_at and evaluation.started_at:
            duration = int((evaluation.finished_at - evaluation.started_at).total_seconds())

        eval_summary = EvaluationSummary(
            id=evaluation.id,
            name=evaluation.name,
            base_url=evaluation.base_url,
            status=evaluation.status.value,
            created_at=evaluation.created_at,
            finished_at=evaluation.finished_at,
            task_count=len(evaluation.tasks),
            clone_count=len(evaluation.clones),
            duration_seconds=duration,
        )

        clones_data = []
        for clone in evaluation.clones:
            clone_dict = {
                "id": str(clone.id),
                "clone_id": clone.clone_id,
                "persona_name": clone.persona_cache.get("name", "Unknown"),
                "sus_score": clone.sus_score,
                "sus_ratings": clone.sus_ratings,
                "sus_reasoning": clone.sus_reasoning,
                "sus_response": None,
            }

            if clone.sus_score is not None and clone.sus_ratings:
                enriched_responses = []
                for i, rating in enumerate(clone.sus_ratings):
                    enriched_responses.append({
                        "item_number": i + 1,
                        "question": SUS_ITEMS[i],
                        "rating": rating,
                        "scale": "1 (Strongly Disagree) to 5 (Strongly Agree)",
                    })

                clone_dict["sus_response"] = {
                    "score": clone.sus_score,
                    "responses": enriched_responses,
                    "reasoning": clone.sus_reasoning,
                }

            clones_data.append(clone_dict)

        task_runs_data = []
        all_metrics = []

        for clone in evaluation.clones:
            for task_run in clone.task_runs:
                metrics = await self.eval_repo.get_task_run_metrics(task_run.id)
                task_def = await self.eval_repo.get_task_definition(task_run.task_definition_id)

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
                    "agent_done": task_run.agent_done,
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
            "avg_sus_score": round(avg_sus, 1),
        }

        # Include task definitions for PENDING evaluations (no runs yet)
        task_definitions = []
        for task_def in evaluation.tasks:
            task_definitions.append({
                "id": str(task_def.id),
                "description": task_def.description,
                "max_steps": task_def.max_steps,
                "base_url_override": task_def.base_url_override,
                "verification_rules": task_def.verification_rules,
            })

        return {
            "evaluation": eval_summary,
            "clones": clones_data,
            "task_runs": task_runs_data,
            "task_definitions": task_definitions,
            "metrics_summary": metrics_summary,
        }

    async def get_editable_evaluation(self, evaluation_id: UUID) -> Optional[dict]:
        evaluation = await self.eval_repo.get_with_tasks_and_clones(evaluation_id)

        if not evaluation:
            return None

        if evaluation.status != EvaluationStatus.PENDING:
            raise ValueError(f"Cannot edit evaluation with status {evaluation.status.value}")

        return {
            "id": str(evaluation.id),
            "name": evaluation.name,
            "base_url": evaluation.base_url,
            "browser": evaluation.browser or "chromium",
            "viewport": evaluation.viewport or {"width": 1280, "height": 720},
            "clone_count": len(evaluation.clones),
            "tasks": [
                {
                    "description": task.description,
                    "max_steps": task.max_steps,
                    "base_url_override": task.base_url_override,
                    "verification_rules": task.verification_rules,
                }
                for task in evaluation.tasks
            ],
        }

    async def update_evaluation(self, evaluation_id: UUID, data: EvaluationCreate) -> Optional[dict]:
        evaluation = await self.eval_repo.get_with_relations(evaluation_id)

        if not evaluation:
            return None

        if evaluation.status != EvaluationStatus.PENDING:
            raise ValueError(f"Cannot edit evaluation with status {evaluation.status.value}")

        await self.eval_repo.update(
            evaluation=evaluation,
            name=data.name,
            base_url=data.base_url,
            browser=data.browser,
            viewport=data.viewport,
            clone_count=data.clone_count,
            tasks_data=[t.model_dump() for t in data.tasks],
        )

        return {
            "evaluation_id": str(evaluation.id),
            "status": "updated",
            "message": "Evaluation updated successfully",
        }

    async def delete_evaluation(self, evaluation_id: UUID) -> Optional[dict]:
        evaluation = await self.eval_repo.get_with_relations(evaluation_id)

        if not evaluation:
            return None

        try:
            await create_screenshot_storage().delete_evaluation(str(evaluation_id))
        except Exception as e:
            print(f"[EvaluationService] Failed to delete screenshots: {e}")

        await self.eval_repo.delete(evaluation)

        return {
            "evaluation_id": str(evaluation_id),
            "status": "deleted",
        }

    async def start_evaluation(self, evaluation_id: UUID) -> Optional[dict]:
        evaluation = await self.eval_repo.get_with_relations(evaluation_id)

        if not evaluation:
            return None

        if evaluation.status != EvaluationStatus.PENDING:
            raise ValueError(f"Evaluation already {evaluation.status.value}")

        evaluation_cancellation[evaluation_id] = False
        await self.eval_repo.update_status(evaluation, EvaluationStatus.RUNNING, started_at=datetime.utcnow())
        asyncio.create_task(run_evaluation_background(evaluation_id))

        return {
            "evaluation_id": str(evaluation_id),
            "status": "started",
            "message": "Evaluation started in background. Connect to WebSocket for progress.",
        }

    async def abort_evaluation(self, evaluation_id: UUID) -> Optional[dict]:
        evaluation = await self.eval_repo.get_with_relations(evaluation_id)

        if not evaluation:
            return None

        if evaluation.status != EvaluationStatus.RUNNING:
            raise ValueError(f"Cannot abort evaluation with status {evaluation.status.value}")

        evaluation_cancellation[evaluation_id] = True
        await self.eval_repo.update_status(evaluation, EvaluationStatus.ABORTED)

        await broadcast_progress(evaluation_id, {
            "type": "status",
            "status": "ABORTING",
            "message": "Aborting... (finishing current task)",
        })

        return {
            "evaluation_id": str(evaluation_id),
            "status": "aborting",
            "message": "Abort requested. Evaluation will stop after the current task completes.",
        }


# ============================================================================
# PROGRESS CALCULATION
# ============================================================================

async def calculate_progress(evaluation_id: UUID) -> float:
    async with AsyncSessionLocal() as session:
        # Total expected = clones × tasks
        clone_count = (await session.execute(
            select(func.count(EvaluationClone.id))
            .where(EvaluationClone.evaluation_id == evaluation_id)
        )).scalar() or 0

        task_count = (await session.execute(
            select(func.count(TaskDefinition.id))
            .where(TaskDefinition.evaluation_id == evaluation_id)
        )).scalar() or 0

        total_expected = clone_count * task_count
        if total_expected == 0:
            return 0.0

        # Finished = any terminal status
        terminal = ("VERIFIED", "COMPLETED", "FAILED", "TIMEOUT", "ABORTED")
        finished = (await session.execute(
            select(func.count(TaskRun.id))
            .join(TaskDefinition, TaskRun.task_definition_id == TaskDefinition.id)
            .where(TaskDefinition.evaluation_id == evaluation_id)
            .where(TaskRun.status.in_(terminal))
        )).scalar() or 0

        return round(min(finished / total_expected * 100, 99.0), 1)


async def broadcast_progress_loop(evaluation_id: UUID):
    """
    Broadcast progress percentage to all connected WebSocket clients.
    Fires immediately on start, then every 10 seconds until cancelled.
    """
    while True:
        try:
            percentage = await calculate_progress(evaluation_id)
            await broadcast_progress(evaluation_id, {
                "type": "progress",
                "percentage": percentage,
            })
        except Exception as e:
            print(f"[Progress] Failed to broadcast progress: {e}")
        await asyncio.sleep(10)


# ============================================================================
# BACKGROUND TASK HELPERS
# ============================================================================

async def run_evaluation_background(evaluation_id: UUID):
    from src.evaluation.runner import EvaluationRunner

    runner = EvaluationRunner(error_policy="continue_on_error")

    # Start the periodic progress broadcaster alongside the runner
    progress_task = asyncio.create_task(broadcast_progress_loop(evaluation_id))

    try:
        await broadcast_progress(evaluation_id, {
            "type": "status",
            "status": "RUNNING",
            "message": "Evaluation started",
        })

        await runner.run(evaluation_id)

        # Check if we ended via abort/cleanup (runner broadcasts ABORTED_RESET itself)
        # or via normal completion
        if not evaluation_cancellation.get(evaluation_id, False):
            # Send 100% before the completion status
            await broadcast_progress(evaluation_id, {
                "type": "progress",
                "percentage": 100,
            })
            await broadcast_progress(evaluation_id, {
                "type": "status",
                "status": "COMPLETED",
                "message": "Evaluation completed successfully",
            })

    except Exception as e:
        await broadcast_progress(evaluation_id, {
            "type": "error",
            "message": f"Evaluation failed: {str(e)}",
        })

    finally:
        progress_task.cancel()
        if evaluation_id in evaluation_cancellation:
            del evaluation_cancellation[evaluation_id]