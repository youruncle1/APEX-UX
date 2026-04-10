from uuid import UUID
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.repositories import TaskRunRepository
from src.evaluation.models.api_models import TaskRunDetail, ActionRecordDetail


class TaskRunService:

    def __init__(self, session: AsyncSession):
        self.session = session
        self.task_run_repo = TaskRunRepository(session)

    async def get_task_run_detail(self, task_run_id: UUID) -> Optional[TaskRunDetail]:
        task_run = await self.task_run_repo.get_with_relations(task_run_id)

        if not task_run:
            return None

        metrics_dict = None
        if task_run.metrics:
            metrics_dict = {
                "steps_taken": task_run.metrics.steps_taken,
                "failed_action_count": task_run.metrics.failed_action_count,
                "outcome_mismatch_count": task_run.metrics.outcome_mismatch_count,
                "backtracking_count": task_run.metrics.backtracking_count,
                "seq_score": task_run.metrics.seq_score,
                "seq_reasoning": task_run.metrics.seq_reasoning,
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
                screenshot_url=action.screenshot,
            ))

        return TaskRunDetail(
            id=task_run.id,
            task_description=task_run.task_definition.description,
            persona_name=task_run.clone.persona.name,
            status=task_run.status.value,
            agent_done=task_run.agent_done,
            verification_result=task_run.verification_result.value if task_run.verification_result else None,
            verification_rules=task_run.task_definition.verification_rules,
            verification_details=task_run.verification_details,
            started_at=task_run.started_at,
            finished_at=task_run.finished_at,
            metrics=metrics_dict,
            actions=actions_data,
            outcome_mismatches=task_run.final_outcome_mismatches,
        )