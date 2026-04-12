import asyncio
from datetime import datetime, timezone
from typing import Optional, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import EvaluationStatus
from src.database.repositories import EvaluationRepository, PersonaRepository
from src.evaluation.runner import EvaluationRunner, evaluation_cancellation
from src.evaluation.models.api_models import (
    EvaluationCreate, EvaluationSummary, EvaluationReport, PersonaResponse,
)
from src.evaluation.prompts import SUS_ITEMS
from src.storage import create_screenshot_storage
from src.log import get_logger

logger = get_logger(__name__)


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
            persona_ids=data.persona_ids,
            tasks_data=[t.model_dump() for t in data.tasks],
        )

        return {
            "evaluation_id": str(evaluation.id),
            "status": "created",
            "message": f"Evaluation created with {len(data.persona_ids)} personas and {len(data.tasks)} tasks",
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
                "persona_id": str(clone.persona_id),
                "persona_name": clone.persona.name,
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
                failed_action_count = metrics.failed_action_count if metrics else 0
                backtracking_count = metrics.backtracking_count if metrics else 0
                outcome_mismatch_count = metrics.outcome_mismatch_count if metrics else 0
                time_on_task_seconds = metrics.time_on_task_seconds if metrics else None

                task_runs_data.append({
                    "id": str(task_run.id),
                    "task_description": task_def.description if task_def else "Unknown",
                    "clone_uuid": str(clone.id),
                    "persona_name": clone.persona.name,
                    "status": task_run.status.value,
                    "steps_taken": steps_taken,
                    "seq_score": seq_score,
                    "seq_reasoning": seq_reasoning,
                    "verification_result": task_run.verification_result.value if task_run.verification_result else None,
                    "agent_done": task_run.agent_done,
                    "failed_action_count": failed_action_count,
                    "backtracking_count": backtracking_count,
                    "outcome_mismatch_count": outcome_mismatch_count,
                    "time_on_task_seconds": time_on_task_seconds,
                })

                if metrics:
                    all_metrics.append(metrics)

        total_tasks = len(task_runs_data)
        successful_tasks = sum(
            1 for tr in task_runs_data
            if tr["status"] == "VERIFIED" or (tr["status"] == "COMPLETED" and tr["agent_done"])
        )

        avg_steps = avg_seq = avg_sus = 0

        avg_time = None
        if all_metrics:
            avg_steps = sum(m.steps_taken for m in all_metrics) / len(all_metrics)
            seq_scores = [m.seq_score for m in all_metrics if m.seq_score is not None]
            if seq_scores:
                avg_seq = sum(seq_scores) / len(seq_scores)
            time_values = [m.time_on_task_seconds for m in all_metrics if m.time_on_task_seconds is not None]
            if time_values:
                avg_time = round(sum(time_values) / len(time_values))

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
            "avg_time_on_task_seconds": avg_time,
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
            "persona_ids": [c.persona_id for c in evaluation.clones if c.persona_id],
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
            persona_ids=data.persona_ids,
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
            logger.warning("Failed to delete screenshots: %s", e)

        await self.eval_repo.delete(evaluation)

        return {
            "evaluation_id": str(evaluation_id),
            "status": "deleted",
        }

    async def start_evaluation(self, evaluation_id: UUID, on_progress) -> Optional[dict]:
        evaluation = await self.eval_repo.get_with_relations(evaluation_id)

        if not evaluation:
            return None

        if evaluation.status != EvaluationStatus.PENDING:
            raise ValueError(f"Evaluation already {evaluation.status.value}")

        from src.config import get_settings
        evaluation_cancellation[evaluation_id] = False
        await self.eval_repo.update_status(evaluation, EvaluationStatus.RUNNING, started_at=datetime.now(timezone.utc).replace(tzinfo=None))
        asyncio.create_task(
            EvaluationRunner(
                error_policy=get_settings().error_policy,
                on_progress=on_progress,
            ).run(evaluation_id)
        )

        return {
            "evaluation_id": str(evaluation_id),
            "status": "started",
            "message": "Evaluation started in background. Connect to WebSocket for progress.",
        }

    async def list_personas(self) -> list[PersonaResponse]:
        personas = await PersonaRepository(self.session).list_all()
        return [PersonaResponse(id=p.id, name=p.name, description=p.description) for p in personas]

    async def copy_evaluation(self, evaluation_id: UUID) -> Optional[dict]:
        new_eval = await self.eval_repo.copy(evaluation_id)
        if not new_eval:
            return None
        return {"evaluation_id": str(new_eval.id), "status": "copied"}

    @staticmethod
    async def get_evaluation_progress(evaluation_id: UUID) -> float:
        return await EvaluationRunner._calculate_progress(evaluation_id)

    async def abort_evaluation(self, evaluation_id: UUID, on_progress) -> Optional[dict]:
        evaluation = await self.eval_repo.get_with_relations(evaluation_id)

        if not evaluation:
            return None

        if evaluation.status != EvaluationStatus.RUNNING:
            raise ValueError(f"Cannot abort evaluation with status {evaluation.status.value}")

        evaluation_cancellation[evaluation_id] = True
        await self.eval_repo.update_status(evaluation, EvaluationStatus.ABORTED)

        await on_progress(evaluation_id, {
            "type": "status",
            "status": "ABORTING",
            "message": "Aborting... (finishing current task)",
        })

        return {
            "evaluation_id": str(evaluation_id),
            "status": "aborting",
            "message": "Abort requested. Evaluation will stop after the current task completes.",
        }

