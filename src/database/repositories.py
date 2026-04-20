from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.models import (
    Evaluation,
    EvaluationStatus,
    EvaluationClone,
    TaskDefinition,
    TaskRun,
    TaskRunStatus,
    TaskRunMetrics,
    ActionRecord,
    VerificationResult as VR,
    Persona,
)


class EvaluationRepository:
    """
    Handles all database operations for evaluations, clones, tasks, and related records.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_with_relations(self, evaluation_id: UUID) -> Optional[Evaluation]:
        """
        Fetch evaluation with clones and tasks loaded, no task run history.
        """
        result = await self.session.execute(
            select(Evaluation).where(Evaluation.id == evaluation_id)
        )
        evaluation = result.scalar_one_or_none()
        if evaluation:
            await self.session.refresh(evaluation, ["clones", "tasks"])
        return evaluation

    async def get_with_clones_and_tasks(
        self, evaluation_id: UUID
    ) -> Optional[Evaluation]:
        """
        Fetch evaluation with full depth: clones, their task runs, personas, and tasks.
        Used for building the evaluation report.
        """
        result = await self.session.execute(
            select(Evaluation)
            .where(Evaluation.id == evaluation_id)
            .options(
                selectinload(Evaluation.clones).selectinload(EvaluationClone.task_runs),
                selectinload(Evaluation.clones).selectinload(EvaluationClone.persona),
                selectinload(Evaluation.tasks),
            )
        )
        return result.scalar_one_or_none()

    async def get_with_tasks_and_clones(
        self, evaluation_id: UUID
    ) -> Optional[Evaluation]:
        """
        Fetch evaluation with tasks and clones (with personas), but no task run history.
        Used for editing or copying an evaluation.
        """
        result = await self.session.execute(
            select(Evaluation)
            .where(Evaluation.id == evaluation_id)
            .options(
                selectinload(Evaluation.tasks),
                selectinload(Evaluation.clones).selectinload(EvaluationClone.persona),
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Evaluation]:
        """
        Fetch all evaluations with clones and tasks, ordered newest first.
        """
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
        """
        Create a new evaluation with one clone per persona and all task definitions.
        Validates that all persona IDs exist before writing anything.
        """
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

        # Flush to get evaluation.id before creating evaluation_clone and tasks that reference it
        await self.session.flush()

        # One clone per persona
        for persona_id in persona_ids:
            persona = by_id[persona_id]
            clone = EvaluationClone(
                evaluation_id=evaluation.id,
                persona_id=persona.id,
            )
            self.session.add(clone)

        # Position is the order the tasks were passed in
        for i, task_data in enumerate(tasks_data):
            task = TaskDefinition(
                evaluation_id=evaluation.id,
                position=i,
                description=task_data["description"],
                base_url_override=task_data.get("base_url_override"),
                max_steps=task_data.get("max_steps", 25),
                min_pages=task_data.get("min_pages"),
                verification_rules=task_data.get("verification_rules"),
            )
            self.session.add(task)

        await self.session.commit()
        await self.session.refresh(evaluation)
        return evaluation

    async def copy(self, evaluation_id: UUID) -> Optional[Evaluation]:
        """
        Duplicate an existing evaluation with all its tasks and clones.
        The copy has status PENDING with no run history.
        """
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

        # Flush to get new_eval.id before evaluation_clones and tasks reference it
        await self.session.flush()

        for clone in source.clones:
            self.session.add(
                EvaluationClone(
                    evaluation_id=new_eval.id,
                    persona_id=clone.persona_id,
                )
            )

        for task in source.tasks:
            self.session.add(
                TaskDefinition(
                    evaluation_id=new_eval.id,
                    description=task.description,
                    base_url_override=task.base_url_override,
                    max_steps=task.max_steps,
                    min_pages=task.min_pages,
                    verification_rules=task.verification_rules,
                    position=task.position,
                )
            )

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
        """
        Replace an evaluation's config by deleting and recreating all clones and tasks.
        Validates persona IDs before making any changes.
        """
        personas = await PersonaRepository(self.session).get_by_ids(persona_ids)
        by_id = {p.id: p for p in personas}
        missing = [pid for pid in persona_ids if pid not in by_id]
        if missing:
            raise ValueError(f"Persona(s) not found: {missing}")

        evaluation.name = name
        evaluation.base_url = base_url
        evaluation.browser = browser
        evaluation.viewport = viewport or {"width": 1280, "height": 720}

        # Delete and recreate clones and tasks
        await self.session.execute(
            delete(TaskDefinition).where(TaskDefinition.evaluation_id == evaluation.id)
        )
        await self.session.execute(
            delete(EvaluationClone).where(
                EvaluationClone.evaluation_id == evaluation.id
            )
        )

        for persona_id in persona_ids:
            persona = by_id[persona_id]
            clone = EvaluationClone(
                evaluation_id=evaluation.id,
                persona_id=persona.id,
            )
            self.session.add(clone)

        for i, task_data in enumerate(tasks_data):
            task = TaskDefinition(
                evaluation_id=evaluation.id,
                position=i,
                description=task_data["description"],
                base_url_override=task_data.get("base_url_override"),
                max_steps=task_data.get("max_steps", 25),
                min_pages=task_data.get("min_pages"),
                verification_rules=task_data.get("verification_rules"),
            )
            self.session.add(task)

        await self.session.commit()

    async def delete(self, evaluation: Evaluation) -> None:
        """
        Delete an evaluation and all its related records, cascade.
        """
        await self.session.delete(evaluation)
        await self.session.commit()

    async def update_status(
        self,
        evaluation: Evaluation,
        status: EvaluationStatus,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
    ) -> None:
        """
        Update evaluation status (optional set started/finished timestamps).
        """
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
        """
        Save SUS score, ratings and reasoning to a clone after evaluation completes.
        """
        clone.sus_score = sus_score
        clone.sus_ratings = sus_ratings
        clone.sus_reasoning = sus_reasoning
        await self.session.commit()

    async def refresh_clone_task_runs(self, clone: EvaluationClone) -> None:
        """
        Reload task_runs relation on a clone from the database.
        """
        await self.session.refresh(clone, ["task_runs"])

    async def refresh_task_run_relations(self, task_run: TaskRun) -> None:
        """
        Reload task_definition, metrics, and actions on a task run from the database.
        """
        await self.session.refresh(task_run, ["task_definition", "metrics", "actions"])

    async def get_task_run_metrics(self, task_run_id: UUID) -> Optional[TaskRunMetrics]:
        """
        Fetch metrics record for a given task run.
        """
        result = await self.session.execute(
            select(TaskRunMetrics).where(TaskRunMetrics.task_run_id == task_run_id)
        )
        return result.scalar_one_or_none()

    async def get_task_definition(
        self, task_definition_id: UUID
    ) -> Optional[TaskDefinition]:
        """
        Fetch a task definition by ID.
        """
        result = await self.session.execute(
            select(TaskDefinition).where(TaskDefinition.id == task_definition_id)
        )
        return result.scalar_one_or_none()


class TaskRunRepository:
    """
    Handles all database operations for task runs, their metrics and action records.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_with_relations(self, task_run_id: UUID) -> Optional[TaskRun]:
        """
        Fetch a task run with its task definition, clone (with persona), metrics, and actions.
        """
        result = await self.session.execute(
            select(TaskRun)
            .where(TaskRun.id == task_run_id)
            .options(
                selectinload(TaskRun.task_definition),
                selectinload(TaskRun.clone).selectinload(EvaluationClone.persona),
                selectinload(TaskRun.metrics),
                selectinload(TaskRun.actions),
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self, task_definition_id: UUID, evaluation_clone_id: UUID
    ) -> TaskRun:
        """
        Insert a new task run in RUNNING status and return it with a generated ID.
        Uses flush so the caller can continue batching before committing.
        """
        task_run = TaskRun(
            task_definition_id=task_definition_id,
            evaluation_clone_id=evaluation_clone_id,
            status=TaskRunStatus.RUNNING,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),  # Strip tzinfo
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
        """
        Write final outcome fields onto the task run object.
        Does not commit yet, commit() called after batching with save_metrics and save_action_records.
        """
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
        time_on_task_seconds: Optional[int] = None,
        visited_pages_total: Optional[int] = None,
        visited_pages_unique: Optional[int] = None,
        lostness: Optional[float] = None,
    ) -> None:
        """
        Create and stage a TaskRunMetrics record.
        Does not commit yet, commit() called after batching with save_result and save_action_records.
        """
        metrics = TaskRunMetrics(
            task_run_id=task_run_id,
            steps_taken=steps_taken,
            failed_action_count=failed_action_count,
            outcome_mismatch_count=outcome_mismatch_count,
            backtracking_count=backtracking_count,
            seq_score=seq_score,
            seq_reasoning=seq_reasoning,
            time_on_task_seconds=time_on_task_seconds,
            visited_pages_total=visited_pages_total,
            visited_pages_unique=visited_pages_unique,
            lostness=lostness,
        )
        self.session.add(metrics)

    async def save_action_records(
        self,
        task_run_id: UUID,
        action_records: list[dict],
    ) -> None:
        """
        Create one ActionRecord row per step from the controller's action history.
        Does not commit yet, commit() called after batching with save_result and save_metrics.
        """
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
                timestamp=datetime.fromisoformat(action_data["timestamp"]).replace(
                    tzinfo=None  # Strip tzinfo
                ),
                screenshot=action_data.get("screenshot"),
            )
            self.session.add(action_record)

    async def mark_aborted(self, task_run: TaskRun, failure_reason: str) -> None:
        """
        Mark a task run as aborted.
        """
        task_run.status = TaskRunStatus.ABORTED
        task_run.failure_reason = failure_reason
        task_run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)

    async def delete_by_id(self, task_run_id: UUID) -> None:
        """
        Delete a task run by ID if it exists.
        """
        result = await self.session.execute(
            select(TaskRun).where(TaskRun.id == task_run_id)
        )
        task_run = result.scalar_one_or_none()
        if task_run:
            await self.session.delete(task_run)
            await self.session.commit()

    async def commit(self) -> None:
        """
        Commit all staged changes. Called after batching save_result, save_metrics, and save_action_records.
        """
        await self.session.commit()


class PersonaRepository:
    """
    Handles all database operations for personas.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> list[Persona]:
        """
        Fetch all personas ordered by name.
        """
        result = await self.session.execute(select(Persona).order_by(Persona.name))
        return list(result.scalars().all())

    async def get_by_id(self, persona_id: UUID) -> Optional[Persona]:
        """
        Fetch a single persona by ID.
        """
        result = await self.session.execute(
            select(Persona).where(Persona.id == persona_id)
        )
        return result.scalar_one_or_none()

    async def get_by_ids(self, persona_ids: list[UUID]) -> list[Persona]:
        """
        Fetch multiple personas by ID.
        """
        result = await self.session.execute(
            select(Persona).where(Persona.id.in_(persona_ids))
        )
        return list(result.scalars().all())

    async def create(self, name: str, description: str, narrative: str) -> Persona:
        """
        Insert a new persona and return it with a generated ID.
        """
        persona = Persona(name=name, description=description, narrative=narrative)
        self.session.add(persona)
        await self.session.commit()
        await self.session.refresh(persona)
        return persona

    async def update(
        self, persona_id: UUID, name: str, description: str, narrative: str
    ) -> Optional[Persona]:
        """
        Update persona fields and return the updated record.
        """
        persona = await self.get_by_id(persona_id)
        if not persona:
            return None
        persona.name = name
        persona.description = description
        persona.narrative = narrative
        await self.session.commit()
        await self.session.refresh(persona)
        return persona

    async def delete(self, persona_id: UUID) -> tuple[bool, str]:
        """
        Delete a persona if it exists and is not assigned to any evaluation_clone.
        """
        # Check if any clone still references this persona before deleting
        result = await self.session.execute(
            select(EvaluationClone)
            .where(EvaluationClone.persona_id == persona_id)
            .limit(1)
        )
        if result.scalar_one_or_none():
            return False, "in_use"
        persona = await self.get_by_id(persona_id)
        if not persona:
            return False, "not_found"
        await self.session.delete(persona)
        await self.session.commit()
        return True, "ok"
