from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.models import (
    Evaluation, EvaluationStatus, EvaluationClone,
    TaskDefinition, TaskRun, TaskRunStatus, TaskRunMetrics,
    ActionRecord, VerificationResult as VR, Persona,
)


class EvaluationRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_with_relations(self, evaluation_id: UUID) -> Optional[Evaluation]:
        result = await self.session.execute(
            select(Evaluation).where(Evaluation.id == evaluation_id)
        )
        evaluation = result.scalar_one_or_none()
        if evaluation:
            await self.session.refresh(evaluation, ["clones", "tasks"])
        return evaluation

    async def get_with_clones_and_tasks(self, evaluation_id: UUID) -> Optional[Evaluation]:
        result = await self.session.execute(
            select(Evaluation)
            .where(Evaluation.id == evaluation_id)
            .options(
                selectinload(Evaluation.clones).selectinload(EvaluationClone.task_runs),
                selectinload(Evaluation.tasks),
            )
        )
        return result.scalar_one_or_none()

    async def get_with_tasks_and_clones(self, evaluation_id: UUID) -> Optional[Evaluation]:
        result = await self.session.execute(
            select(Evaluation)
            .where(Evaluation.id == evaluation_id)
            .options(selectinload(Evaluation.tasks), selectinload(Evaluation.clones))
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Evaluation]:
        result = await self.session.execute(
            select(Evaluation)
            .options(selectinload(Evaluation.clones), selectinload(Evaluation.tasks))
            .order_by(Evaluation.created_at.desc())
        )
        return list(result.scalars().all())

    async def create(
        self,
        name: Optional[str],
        base_url: str,
        persona_ids: list[str],
        tasks_data: list[dict],
        browser: str = "chromium",
        viewport: Optional[dict] = None,
    ) -> Evaluation:
        personas = await PersonaRepository(self.session).get_by_ids(persona_ids)
        by_id = {p.id: p for p in personas}
        missing = [pid for pid in persona_ids if pid not in by_id]
        if missing:
            raise ValueError(f"Persona(s) not found: {missing}")

        evaluation = Evaluation(
            name=name,
            base_url=base_url,
            browser=browser,
            viewport=viewport or {"width": 1280, "height": 720},
            status=EvaluationStatus.PENDING,
        )
        self.session.add(evaluation)
        await self.session.flush()

        for persona_id in persona_ids:
            persona = by_id[persona_id]
            clone = EvaluationClone(
                evaluation_id=evaluation.id,
                clone_id=persona.id,
                persona_id=persona.id,
                persona_cache={"id": persona.id, "name": persona.name},
            )
            self.session.add(clone)

        for task_data in tasks_data:
            task = TaskDefinition(
                evaluation_id=evaluation.id,
                description=task_data["description"],
                base_url_override=task_data.get("base_url_override"),
                max_steps=task_data.get("max_steps", 25),
                verification_rules=task_data.get("verification_rules"),
            )
            self.session.add(task)

        await self.session.commit()
        await self.session.refresh(evaluation)
        return evaluation

    async def copy(self, evaluation_id: UUID) -> Optional[Evaluation]:
        source = await self.get_with_tasks_and_clones(evaluation_id)
        if not source:
            return None

        new_name = f"{source.name} (Copy)" if source.name else "(Copy)"

        new_eval = Evaluation(
            name=new_name,
            base_url=source.base_url,
            browser=source.browser,
            viewport=source.viewport,
            status=EvaluationStatus.PENDING,
        )
        self.session.add(new_eval)
        await self.session.flush()

        for clone in source.clones:
            self.session.add(EvaluationClone(
                evaluation_id=new_eval.id,
                clone_id=clone.clone_id,
                persona_id=clone.persona_id,
                persona_cache=clone.persona_cache,
            ))

        for task in source.tasks:
            self.session.add(TaskDefinition(
                evaluation_id=new_eval.id,
                description=task.description,
                base_url_override=task.base_url_override,
                max_steps=task.max_steps,
                verification_rules=task.verification_rules,
            ))

        await self.session.commit()
        await self.session.refresh(new_eval)
        return new_eval

    async def update(
        self,
        evaluation: Evaluation,
        name: Optional[str],
        base_url: str,
        persona_ids: list[str],
        tasks_data: list[dict],
        browser: str = "chromium",
        viewport: Optional[dict] = None,
    ) -> None:
        personas = await PersonaRepository(self.session).get_by_ids(persona_ids)
        by_id = {p.id: p for p in personas}
        missing = [pid for pid in persona_ids if pid not in by_id]
        if missing:
            raise ValueError(f"Persona(s) not found: {missing}")

        evaluation.name = name
        evaluation.base_url = base_url
        evaluation.browser = browser
        evaluation.viewport = viewport or {"width": 1280, "height": 720}

        await self.session.execute(
            delete(TaskDefinition).where(TaskDefinition.evaluation_id == evaluation.id)
        )
        await self.session.execute(
            delete(EvaluationClone).where(EvaluationClone.evaluation_id == evaluation.id)
        )

        for persona_id in persona_ids:
            persona = by_id[persona_id]
            clone = EvaluationClone(
                evaluation_id=evaluation.id,
                clone_id=persona.id,
                persona_id=persona.id,
                persona_cache={"id": persona.id, "name": persona.name},
            )
            self.session.add(clone)

        for task_data in tasks_data:
            task = TaskDefinition(
                evaluation_id=evaluation.id,
                description=task_data["description"],
                base_url_override=task_data.get("base_url_override"),
                max_steps=task_data.get("max_steps", 25),
                verification_rules=task_data.get("verification_rules"),
            )
            self.session.add(task)

        await self.session.commit()

    async def delete(self, evaluation: Evaluation) -> None:
        await self.session.delete(evaluation)
        await self.session.commit()

    async def update_status(
        self,
        evaluation: Evaluation,
        status: EvaluationStatus,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
    ) -> None:
        evaluation.status = status
        if started_at is not None:
            evaluation.started_at = started_at
        if finished_at is not None:
            evaluation.finished_at = finished_at
        await self.session.commit()

    async def update_clone_sus(
        self,
        clone: EvaluationClone,
        sus_score: float,
        sus_ratings: list[int],
        sus_reasoning: str,
    ) -> None:
        clone.sus_score = sus_score
        clone.sus_ratings = sus_ratings
        clone.sus_reasoning = sus_reasoning
        await self.session.commit()

    async def refresh_clone_task_runs(self, clone: EvaluationClone) -> None:
        await self.session.refresh(clone, ["task_runs"])

    async def refresh_task_run_relations(self, task_run: TaskRun) -> None:
        await self.session.refresh(task_run, ["task_definition", "metrics", "actions"])

    async def get_task_run_metrics(self, task_run_id: UUID) -> Optional[TaskRunMetrics]:
        result = await self.session.execute(
            select(TaskRunMetrics).where(TaskRunMetrics.task_run_id == task_run_id)
        )
        return result.scalar_one_or_none()

    async def get_task_definition(self, task_definition_id: UUID) -> Optional[TaskDefinition]:
        result = await self.session.execute(
            select(TaskDefinition).where(TaskDefinition.id == task_definition_id)
        )
        return result.scalar_one_or_none()


class TaskRunRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_with_relations(self, task_run_id: UUID) -> Optional[TaskRun]:
        result = await self.session.execute(
            select(TaskRun)
            .where(TaskRun.id == task_run_id)
            .options(
                selectinload(TaskRun.task_definition),
                selectinload(TaskRun.clone),
                selectinload(TaskRun.metrics),
                selectinload(TaskRun.actions),
            )
        )
        return result.scalar_one_or_none()

    async def create(self, task_definition_id: UUID, evaluation_clone_id: UUID) -> TaskRun:
        task_run = TaskRun(
            task_definition_id=task_definition_id,
            evaluation_clone_id=evaluation_clone_id,
            status=TaskRunStatus.RUNNING,
            started_at=datetime.utcnow(),
        )
        self.session.add(task_run)
        await self.session.flush()
        await self.session.refresh(task_run)
        return task_run

    async def save_result(
        self,
        task_run: TaskRun,
        status: TaskRunStatus,
        agent_done: bool,
        failure_reason: Optional[str],
        verification_result: Optional[VR],
        verification_details: Optional[dict],
        final_action_history: Optional[list],
        final_action_summary: Optional[str],
        final_outcome_mismatches: Optional[list],
        finished_at: datetime,
    ) -> None:
        task_run.status = status
        task_run.agent_done = agent_done
        task_run.failure_reason = failure_reason
        task_run.verification_result = verification_result
        task_run.verification_details = verification_details
        task_run.final_action_history = final_action_history
        task_run.final_action_summary = final_action_summary
        task_run.final_outcome_mismatches = final_outcome_mismatches
        task_run.finished_at = finished_at

    async def save_metrics(
        self,
        task_run_id: UUID,
        steps_taken: int,
        failed_action_count: int,
        outcome_mismatch_count: int,
        backtracking_count: int,
        seq_score: Optional[float],
        seq_reasoning: Optional[str],
    ) -> None:
        metrics = TaskRunMetrics(
            task_run_id=task_run_id,
            steps_taken=steps_taken,
            failed_action_count=failed_action_count,
            outcome_mismatch_count=outcome_mismatch_count,
            backtracking_count=backtracking_count,
            seq_score=seq_score,
            seq_reasoning=seq_reasoning,
        )
        self.session.add(metrics)

    async def save_action_records(
        self,
        task_run_id: UUID,
        action_records: list[dict],
    ) -> None:
        for action_data in action_records:
            action_record = ActionRecord(
                task_run_id=task_run_id,
                step_number=action_data["step_number"],
                action_type=action_data["action_type"],
                target_description=action_data.get("target_description"),
                input_value=action_data.get("input_value"),
                reasoning=action_data.get("reasoning"),
                bounding_box=action_data.get("bounding_box"),
                click_coordinates=action_data.get("click_coordinates"),
                execution_success=action_data["execution_success"],
                execution_feedback=action_data.get("execution_feedback"),
                timestamp=datetime.fromisoformat(action_data["timestamp"]),
                screenshot=action_data.get("screenshot"),
            )
            self.session.add(action_record)

    async def mark_aborted(self, task_run: TaskRun, failure_reason: str) -> None:
        task_run.status = TaskRunStatus.ABORTED
        task_run.failure_reason = failure_reason
        task_run.finished_at = datetime.utcnow()

    async def commit(self) -> None:
        await self.session.commit()


class PersonaRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> list[Persona]:
        result = await self.session.execute(select(Persona).order_by(Persona.name))
        return list(result.scalars().all())

    async def get_by_id(self, persona_id: str) -> Optional[Persona]:
        result = await self.session.execute(select(Persona).where(Persona.id == persona_id))
        return result.scalar_one_or_none()

    async def get_by_ids(self, persona_ids: list[str]) -> list[Persona]:
        result = await self.session.execute(select(Persona).where(Persona.id.in_(persona_ids)))
        return list(result.scalars().all())