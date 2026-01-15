from uuid import UUID
from typing import Optional, Literal
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.connection import AsyncSessionLocal
from src.database.models import (
    Evaluation, EvaluationStatus, EvaluationClone,
    TaskDefinition, TaskRun, TaskRunStatus,
    ActionRecord
)
from src.agents.planner import PlannerAgent
from src.agents.grounder import GroundingAgent
from src.browser_connector.connector import BrowserConnector
from src.controller.controller import TwoAgentController


# Placeholder persona for foundation development
PLACEHOLDER_PERSONA = """
You are a typical web user with moderate technical skills.
You navigate websites carefully and read interface elements before interacting.
You prefer clear labels and visible feedback from your actions.
"""


class EvaluationRunner:
    """
    Main orchestrator for running evaluations.

    Responsibilities:
    - Load evaluation configuration from database
    - Initialize and manage evaluation session lifecycle
    - Coordinate execution across clones and tasks
    - Manage database transactions for storing results
    - Handle error policies during execution

    Does NOT handle:
    - Metric calculations (future EvaluationModule)
    - Verification logic (Controller responsibility)
    - Report generation (future EvaluationModule)
    - Screenshot storage (Controller responsibility)
    """

    def __init__(
        self,
        error_policy: Literal["fail_fast", "continue_on_error"] = "continue_on_error"
    ):
        """
        Initialize the evaluation runner.

        Args:
            error_policy: How to handle task failures
                - "fail_fast": Stop entire evaluation on first task failure
                - "continue_on_error": Continue with remaining tasks if one fails
        """
        self.error_policy = error_policy
    
    async def run(self, evaluation_id: UUID):
        """
        Run a complete evaluation.

        Orchestration flow:
        1. Load evaluation from database
        2. For each clone:
            - Use placeholder persona context
            - Initialize controller with agents and browser
            - For each task:
                - Create TaskRun in DB
                - Execute task via Controller (which handles screenshot storage)
                - Save action records to DB (with screenshot URLs)
            - Clean up browser resources
        3. Mark evaluation as completed or aborted

        Args:
            evaluation_id: UUID of the evaluation to run

        Raises:
            ValueError: If evaluation not found
            Exception: Any unhandled error during execution (if error_policy is "fail_fast")
        """

        async with AsyncSessionLocal() as session:
            # Load evaluation
            evaluation = await self._load_evaluation(session, evaluation_id)
            if not evaluation:
                raise ValueError(f"Evaluation {evaluation_id} not found")

            print(f"[Runner] Starting evaluation: {evaluation.name or evaluation.id}")
            print(f"[Runner] Tasks: {len(evaluation.tasks)}, Clones: {len(evaluation.clones)}")
            print(f"[Runner] Error policy: {self.error_policy}")

            # Update status to running
            evaluation.status = EvaluationStatus.RUNNING
            await session.commit()

            evaluation_failed = False
            try:
                # Run for each clone
                for clone in evaluation.clones:
                    try:
                        await self._run_clone(session, evaluation, clone)
                    except Exception as e:
                        print(f"[Runner] Clone {clone.clone_id} failed: {e}")
                        if self.error_policy == "fail_fast":
                            raise
                        # Continue with next clone if policy allows
                        evaluation_failed = True

                # Mark evaluation as completed (even if some tasks failed)
                if evaluation_failed:
                    evaluation.status = EvaluationStatus.COMPLETED  # Partial completion
                    print(f"[Runner] Evaluation completed with failures")
                else:
                    evaluation.status = EvaluationStatus.COMPLETED
                    print(f"[Runner] Evaluation completed successfully")

                evaluation.finished_at = datetime.utcnow()
                await session.commit()

            except Exception as e:
                print(f"[Runner] Evaluation aborted: {e}")
                evaluation.status = EvaluationStatus.ABORTED
                evaluation.finished_at = datetime.utcnow()
                await session.commit()
                raise
    
    async def _run_clone(
        self,
        session: AsyncSession,
        evaluation: Evaluation,
        clone: EvaluationClone
    ):
        """
        Run all tasks for a single clone.

        Initializes a fresh controller with agents and browser for this clone,
        executes all tasks, and ensures proper cleanup.

        Args:
            session: Database session
            evaluation: Parent evaluation
            clone: Clone (persona) to execute tasks for

        Raises:
            Exception: Any error during task execution (based on error_policy)
        """

        print(f"\n[Runner] Running clone: {clone.clone_id}")

        # Use placeholder persona for now (future: fetch from PersonaClient)
        persona_context = PLACEHOLDER_PERSONA

        # Initialize agents (one set per clone for isolation)
        planner = PlannerAgent()
        grounder = GroundingAgent()
        browser = BrowserConnector(headless=True)

        controller = TwoAgentController(
            planner=planner,
            grounder=grounder,
            browser=browser
        )

        try:
            # Run each task
            for task_def in evaluation.tasks:
                try:
                    await self._run_task(session, evaluation.id, task_def, clone, controller, persona_context)
                except Exception as e:
                    print(f"[Runner] Task '{task_def.description[:50]}...' failed: {e}")
                    if self.error_policy == "fail_fast":
                        raise
                    # Continue with next task if policy allows

        finally:
            # Always clean up browser resources
            await browser.close()
    
    async def _run_task(
        self,
        session: AsyncSession,
        evaluation_id: UUID,
        task_def: TaskDefinition,
        clone: EvaluationClone,
        controller: TwoAgentController,
        persona_context: str
    ):
        """
        Run a single task for a clone.

        Creates a TaskRun record, executes the task via controller,
        and saves all action records (with screenshot URLs) to the database.

        Args:
            session: Database session
            evaluation_id: Evaluation UUID (for screenshot storage path)
            task_def: Task definition to execute
            clone: Clone executing the task
            controller: Controller to use for execution
            persona_context: Persona description for the planner agent

        Raises:
            Exception: Any error during task execution
        """

        print(f"\n[Runner] Task: {task_def.description[:50]}...")

        # Create TaskRun in DB
        task_run = TaskRun(
            task_definition_id=task_def.id,
            evaluation_clone_id=clone.id,
            status=TaskRunStatus.RUNNING,
            started_at=datetime.now(datetime.timezone.utc)
        )
        session.add(task_run)
        await session.commit()
        await session.refresh(task_run)

        try:
            # Determine start URL
            start_url = task_def.base_url_override or task_def.evaluation.base_url

            # Execute task via controller
            # Pass evaluation_id and task_run_id for screenshot storage
            result = await controller.execute_task(
                task_description=task_def.description,
                persona_context=persona_context,
                start_url=start_url,
                evaluation_id=evaluation_id,  
                task_run_id=task_run.id,      
                max_steps=task_def.max_steps,
                verification_rules=task_def.verification_rules
            )

            # Update TaskRun with basic results
            task_run.status = TaskRunStatus(result["status"])
            task_run.finished_at = datetime.now(datetime.timezone.utc)
            task_run.agent_done = result.get("agent_done", False)

            # Save failure reason if exists
            if "failure_reason" in result:
                task_run.failure_reason = result["failure_reason"]

            # Save memory state for qualitative analysis
            memory_state = result.get("memory_state", {})
            task_run.final_action_history = memory_state.get("action_history")
            task_run.final_action_summary = memory_state.get("action_summary")
            task_run.final_outcome_mismatches = memory_state.get("outcome_mismatches")

            # Save action records (screenshot field now contains URL, not base64!)
            for action_data in result.get("action_records", []):
                action_record = ActionRecord(
                    task_run_id=task_run.id,
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
                    screenshot=action_data.get("screenshot")  # URL from storage!
                )
                session.add(action_record)

            await session.commit()

            print(f"[Runner] Task completed: {task_run.status.value}")

        except Exception as e:
            print(f"[Runner] Task execution failed: {e}")
            task_run.status = TaskRunStatus.ABORTED
            task_run.failure_reason = str(e)
            task_run.finished_at = datetime.now(datetime.timezone.utc)
            await session.commit()
            raise
    
    async def _load_evaluation(
        self,
        session: AsyncSession,
        evaluation_id: UUID
    ) -> Optional[Evaluation]:
        """
        Load evaluation with all relationships from database.

        Args:
            session: Database session
            evaluation_id: UUID of evaluation to load

        Returns:
            Evaluation object with clones and tasks loaded, or None if not found
        """

        result = await session.execute(
            select(Evaluation)
            .where(Evaluation.id == evaluation_id)
        )
        evaluation = result.scalar_one_or_none()

        if evaluation:
            # Explicitly load relationships (clones and tasks)
            await session.refresh(evaluation, ["clones", "tasks"])

        return evaluation