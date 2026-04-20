import asyncio
from uuid import UUID
from typing import Literal, Callable, Awaitable, Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from src.database.connection import AsyncSessionLocal
from src.database.models import (
    Evaluation,
    EvaluationStatus,
    EvaluationClone,
    TaskDefinition,
    TaskRun,
    TaskRunStatus,
    VerificationResult as VR,
)
from src.database.repositories import (
    EvaluationRepository,
    TaskRunRepository,
    PersonaRepository,
)
from src.agents.logic.grounder import GroundingAgent
from src.browser_connector.connector import BrowserConnector
from src.controller.controller import TwoAgentController, TaskAbortedError
from src.evaluation.logic.metrics import MetricsCalculator
from src.evaluation.self_report import SelfReportCollector
from src.storage import create_screenshot_storage
from src.config import get_settings
from src.log import get_logger

logger = get_logger(__name__)

# Cancellation flags for in-progress evaluations. Written by EvaluationService, read by EvaluationRunner.
evaluation_cancellation: dict[UUID, bool] = {}


class TaskRunFailedError(Exception):
    """Wraps a task-run failure, carrying the task_run_id so the retry loop can delete the record."""

    def __init__(self, task_run_id: UUID, cause: Exception):
        super().__init__(str(cause))
        self.task_run_id = task_run_id
        self.cause = cause


# Type alias of async func to pass progress in WebSocket.
# keeps transport layer divided from runner
ProgressCallback = Callable[[UUID, dict], Awaitable[None]]


class EvaluationRunner:
    """
    Evaluation Runner implemenetation.
    Main orchestrator for running evaluations.
    """

    def __init__(
        self,
        error_policy: Literal["fail_fast", "continue_on_error"] = "continue_on_error",
        on_progress: Optional[ProgressCallback] = None,
    ):
        self.error_policy = error_policy  # Hard fail or keep evaluating
        self._on_progress = on_progress  # async function to call when emitting progress events (optional)
        self.metrics_calculator = MetricsCalculator()  # Computes metrics
        self.self_report_collector = (
            SelfReportCollector()
        )  # Collects SEQ and SUS Scores from personas
        self.screenshot_storage = (
            create_screenshot_storage()
        )  # Screenshot storage backend

    async def _emit(self, evaluation_id: UUID, message: dict):
        """
        Forward progress event to the injected callback / call on_progress with the event if one was provided
            - Dependency injection pattern
        """
        if self._on_progress:
            await self._on_progress(evaluation_id, message)

    def _is_cancelled(self, evaluation_id: UUID) -> bool:
        """
        Checks whether evaluation cancellation was set (abort)
        """
        return evaluation_cancellation.get(evaluation_id, False)

    async def run(self, evaluation_id: UUID):
        """
        Runs a complete evaluation
        """

        # Create background asyncio task for progress broadcast
        progress_task = asyncio.create_task(
            self._broadcast_progress_loop(evaluation_id)
        )
        try:
            # emit RUNNING status event on start
            await self._emit(
                evaluation_id,
                {
                    "type": "status",
                    "status": "RUNNING",
                    "message": "Evaluation started",
                },
            )
            # execute evaluation, returns True (failed), False (ok), or "aborted"
            evaluation_failed = await self._execute(evaluation_id)
            # aborted path skips final status — _abort_and_cleanup emits its own event
            if evaluation_failed != "aborted":
                # push 100% only when truly done, not on abort
                await self._emit(evaluation_id, {"type": "progress", "percentage": 100})
                if evaluation_failed:
                    # at least one clone had an unrecoverable error
                    await self._emit(
                        evaluation_id,
                        {
                            "type": "status",
                            "status": "FAILED",
                            "message": "Evaluation completed with errors",
                        },
                    )
                else:
                    await self._emit(
                        evaluation_id,
                        {
                            "type": "status",
                            "status": "COMPLETED",
                            "message": "Evaluation completed successfully",
                        },
                    )
        except Exception as e:
            # unexpected crash outside of clone error handling
            await self._emit(
                evaluation_id,
                {
                    "type": "error",
                    "message": f"Evaluation failed: {str(e)}",
                },
            )
        finally:
            # always stop the background progress loop
            progress_task.cancel()
            # clear the cancellation flag so a re-run starts clean
            if evaluation_id in evaluation_cancellation:
                del evaluation_cancellation[evaluation_id]

    @staticmethod
    async def _calculate_progress(evaluation_id: UUID) -> float:
        async with AsyncSessionLocal() as session:
            clone_count = (
                await session.execute(
                    select(func.count(EvaluationClone.id)).where(
                        EvaluationClone.evaluation_id == evaluation_id
                    )
                )
            ).scalar() or 0

            task_count = (
                await session.execute(
                    select(func.count(TaskDefinition.id)).where(
                        TaskDefinition.evaluation_id == evaluation_id
                    )
                )
            ).scalar() or 0

            # every clone must run every task
            total_expected = clone_count * task_count
            if total_expected == 0:
                return 0.0

            # any terminal status counts as done
            terminal = ("VERIFIED", "COMPLETED", "FAILED", "TIMEOUT", "ABORTED")
            finished = (
                await session.execute(
                    select(func.count(TaskRun.id))
                    .join(
                        TaskDefinition, TaskRun.task_definition_id == TaskDefinition.id
                    )
                    .where(TaskDefinition.evaluation_id == evaluation_id)
                    .where(TaskRun.status.in_(terminal))
                )
            ).scalar() or 0

            return round(min(finished / total_expected * 100, 99.0), 1)

    async def _broadcast_progress_loop(self, evaluation_id: UUID):
        """Emit a progress percentage every 10 seconds until cancelled."""
        while True:
            try:
                percentage = await EvaluationRunner._calculate_progress(evaluation_id)
                await self._emit(
                    evaluation_id,
                    {
                        "type": "progress",
                        "percentage": percentage,
                    },
                )
            except Exception as e:
                logger.warning("Failed to broadcast progress: %s", e)
            await asyncio.sleep(10)

    async def _execute(self, evaluation_id: UUID) -> bool:
        """Run a complete evaluation. Returns True if any clone failed."""

        async with AsyncSessionLocal() as session:
            eval_repo = EvaluationRepository(session)
            evaluation = await eval_repo.get_with_relations(evaluation_id)
            if not evaluation:
                raise ValueError(f"Evaluation {evaluation_id} not found")

            parallel = get_settings().parallel_clones
            logger.info("Starting evaluation: %s", evaluation.name or evaluation.id)
            logger.info(
                "Tasks: %d, Clones: %d", len(evaluation.tasks), len(evaluation.clones)
            )
            logger.info(
                "Error policy: %s | Parallel clones: %s", self.error_policy, parallel
            )

            # Snapshot clone ids, the ORM objects can't be shared across sessions
            # to be fetched back in _run_clone_isolated()
            clone_ids = [clone.id for clone in evaluation.clones]

        try:
            # parallel: all clones run at the same time, exceptions collected as results
            # sequential: clones run one by one, cancellation checked between each
            if parallel:
                results = await asyncio.gather(
                    *[
                        self._run_clone_isolated(evaluation_id, clone_id)
                        for clone_id in clone_ids
                    ],
                    return_exceptions=True,
                )
            else:
                results = []
                for clone_id in clone_ids:
                    if self._is_cancelled(evaluation_id):
                        break
                    result = await self._run_clone_isolated(evaluation_id, clone_id)
                    results.append(result)

            # if any clone was aborted, clean all data and reset to PENDING
            aborted = any(isinstance(r, TaskAbortedError) for r in results)
            if aborted:
                async with AsyncSessionLocal() as session:
                    await self._abort_and_cleanup(
                        evaluation_id, session, EvaluationRepository(session)
                    )
                return "aborted"

            # exceptions returned by gather count as clone failures
            evaluation_failed = any(isinstance(r, Exception) for r in results)

            # save final status and finished_at timestamp
            async with AsyncSessionLocal() as session:
                eval_repo = EvaluationRepository(session)
                evaluation = await eval_repo.get_with_relations(evaluation_id)

                if evaluation_failed:
                    final_status = EvaluationStatus.FAILED
                else:
                    final_status = EvaluationStatus.COMPLETED

                await eval_repo.update_status(
                    evaluation,
                    final_status,
                    finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )

            logger.info(
                "Evaluation completed %s",
                "with failures" if evaluation_failed else "successfully",
            )
            return evaluation_failed

        except Exception as e:
            logger.error("Evaluation failed due to exception: %s", e)
            async with AsyncSessionLocal() as session:
                eval_repo = EvaluationRepository(session)
                evaluation = await eval_repo.get_with_relations(evaluation_id)
                await eval_repo.update_status(
                    evaluation,
                    EvaluationStatus.FAILED,
                    finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            raise

    async def _run_clone_isolated(self, evaluation_id: UUID, clone_id: UUID):
        """
        Run a single clone in its own DB session.
        Middleware between _execute() and _run_clone()
        """
        # Open isolated DB session for clone
        async with AsyncSessionLocal() as session:
            eval_repo = EvaluationRepository(session)
            # re-fetch clones from _execute()
            evaluation = await eval_repo.get_with_relations(evaluation_id)
            if not evaluation:
                raise ValueError(f"Evaluation {evaluation_id} not found")

            # get clone for this isolated run
            clone = next((c for c in evaluation.clones if c.id == clone_id), None)
            if not clone:
                raise ValueError(f"Clone {clone_id} not found")

            # check cancellation before executing any work for this clone
            if self._is_cancelled(evaluation_id):
                logger.warning("Abort requested before clone %s, stopping.", clone_id)
                raise TaskAbortedError("Cancelled before clone started")

            # run the clone
            try:
                await self._run_clone(session, evaluation, clone, evaluation_id)
            except TaskAbortedError:
                # re-raise so _execute can trigger abort cleanup
                logger.warning("Clone %s aborted mid-task.", clone_id)
                raise
            except Exception as e:
                logger.error("Clone %s failed: %s", clone_id, e)
                # fail_fast stops everything, otherwise return the exception as a result
                if self.error_policy == "fail_fast":
                    raise
                return e

    async def _abort_and_cleanup(
        self,
        evaluation_id: UUID,
        session: AsyncSession,
        eval_repo: EvaluationRepository,
    ):
        """
        Clean up all task run data created during this evaluation run,
        delete screenshots, reset clone data, and return evaluation to PENDING.
        """
        logger.info("Aborting and cleaning up evaluation %s", evaluation_id)

        # Delete all task runs for this evaluation (cascades to ActionRecord + TaskRunMetrics)
        result = await session.execute(
            select(TaskRun)
            .join(TaskDefinition, TaskRun.task_definition_id == TaskDefinition.id)
            .where(TaskDefinition.evaluation_id == evaluation_id)
        )
        task_runs = result.scalars().all()

        for task_run in task_runs:
            await session.delete(task_run)

        # Reset SUS data on all clones
        result = await session.execute(
            select(EvaluationClone).where(
                EvaluationClone.evaluation_id == evaluation_id
            )
        )
        clones = result.scalars().all()
        for clone in clones:
            clone.sus_score = None
            clone.sus_ratings = None
            clone.sus_reasoning = None

        # Delete screenshots
        try:
            await self.screenshot_storage.delete_evaluation(str(evaluation_id))
            logger.debug("Screenshots deleted for evaluation %s", evaluation_id)
        except Exception as e:
            logger.warning("Failed to delete screenshots (non-fatal): %s", e)

        # Reset evaluation to PENDING
        result = await session.execute(
            select(Evaluation).where(Evaluation.id == evaluation_id)
        )
        evaluation = result.scalar_one_or_none()
        if evaluation:
            evaluation.status = EvaluationStatus.PENDING
            evaluation.finished_at = None

        await session.commit()

        # Emit that the evaluation was reset
        await self._emit(
            evaluation_id,
            {
                "type": "status",
                "status": "ABORTED_RESET",
                "message": "Evaluation aborted. All data cleared, returned to Pending.",
            },
        )

        # Clean up cancellation flag
        if evaluation_id in evaluation_cancellation:
            del evaluation_cancellation[evaluation_id]

        logger.info("Cleanup complete, evaluation reset to PENDING")

    @staticmethod
    def _task_outcome_label(status: TaskRunStatus, agent_done: bool) -> str:
        """
        Return a description of how a task ended.
        Used in collecting SEQ and SUS metrics.
        """
        if status == TaskRunStatus.VERIFIED:
            return "You successfully completed the task"
        if status == TaskRunStatus.COMPLETED:
            return "You completed the task (no automated check was run)"
        if status == TaskRunStatus.TIMEOUT:
            return "You did not finish the task within the allowed number of steps"
        if status == TaskRunStatus.FAILED:
            if agent_done:
                return "You believed you had finished the task, but the system could not confirm it"
            return "You were unable to complete the task"
        return "The task ended unexpectedly"

    async def _run_clone(
        self,
        session: AsyncSession,
        evaluation: Evaluation,
        clone: EvaluationClone,
        evaluation_id: UUID,
    ):
        """
        Run all tasks for a single clone, then collect SUS score.
        """

        # load persona narrative to use as context for the LLM agents
        persona = await PersonaRepository(session).get_by_id(clone.persona_id)
        if not persona:
            raise ValueError(f"Persona '{clone.persona_id}' not found")
        logger.info("Running clone: %s", persona.name)
        persona_context = persona.narrative

        # each clone gets its own browser instance and agent stack
        vp = evaluation.viewport or {"width": 1280, "height": 720}
        grounder = GroundingAgent(
            viewport_width=vp["width"], viewport_height=vp["height"]
        )
        browser = BrowserConnector(
            headless=get_settings().headless,
            browser_type=evaluation.browser or "chromium",
            viewport_width=vp["width"],
            viewport_height=vp["height"],
            stabilization_wait=get_settings().stabilization_wait,
        )

        controller = TwoAgentController(
            persona_context=persona_context,
            grounder=grounder,
            browser=browser,
            # pass cancellation check so the agent loop can abort between steps
            is_cancelled=lambda: self._is_cancelled(evaluation_id),
        )

        eval_repo = EvaluationRepository(session)

        try:
            for task_def in evaluation.tasks:
                # check cancellation before starting each task
                if self._is_cancelled(evaluation_id):
                    logger.warning(
                        "Abort requested before task '%s...', stopping.",
                        task_def.description[:40],
                    )
                    return  # Caller will handle cleanup

                # 3 attempts per task before giving up (clean record on each try)
                max_attempts = 3
                for attempt in range(1, max_attempts + 1):
                    try:
                        await self._run_task(
                            session=session,
                            evaluation_id=evaluation.id,
                            task_def=task_def,
                            clone=clone,
                            controller=controller,
                            persona_context=persona_context,
                        )
                        break  # success, move to next task
                    except TaskAbortedError:
                        logger.warning(
                            "Task '%s...' aborted mid-step.", task_def.description[:50]
                        )
                        raise  # re-raise so _run_clone_isolated can trigger cleanup
                    except TaskRunFailedError as exc:
                        if attempt < max_attempts:
                            logger.warning(
                                "Task '%s...' failed (attempt %d/%d), retrying: %s",
                                task_def.description[:50],
                                attempt,
                                max_attempts,
                                exc.cause,
                            )
                            # delete the failed record and screenshots before retrying
                            await TaskRunRepository(session).delete_by_id(
                                exc.task_run_id
                            )
                            try:
                                await self.screenshot_storage.delete_task_run(
                                    str(evaluation_id), str(exc.task_run_id)
                                )
                            except Exception as del_err:
                                logger.warning(
                                    "Failed to delete screenshots for retried task run (non-fatal): %s",
                                    del_err,
                                )
                            await asyncio.sleep(2)
                        else:
                            logger.error(
                                "Task '%s...' failed: %s",
                                task_def.description[:50],
                                exc.cause,
                            )
                            if self.error_policy == "fail_fast":
                                raise exc.cause from exc
                            # otherwise continue to next task

                # check cancellation after each task completes
                if self._is_cancelled(evaluation_id):
                    logger.warning(
                        "Abort requested after task '%s...', stopping.",
                        task_def.description[:40],
                    )
                    return  # Caller will handle cleanup

            # all tasks done, collect SUS score for this clone
            logger.debug("Collecting SUS for clone %s...", persona.name)

            # reload task runs with their relations (actions, metrics, task_definition)
            await eval_repo.refresh_clone_task_runs(clone)
            task_summaries = []

            # build a summary of each task run to pass to the SUS LLM call
            for task_run in clone.task_runs:
                await eval_repo.refresh_task_run_relations(task_run)

                actions = [
                    {
                        "step_number": action.step_number,
                        "action_type": action.action_type,
                        "reasoning": action.reasoning or "",
                    }
                    for action in sorted(task_run.actions, key=lambda a: a.step_number)
                ]

                task_summaries.append(
                    {
                        "description": task_run.task_definition.description,
                        "task_outcome": self._task_outcome_label(
                            task_run.status, task_run.agent_done
                        ),
                        "steps_taken": task_run.metrics.steps_taken
                        if task_run.metrics
                        else 0,
                        "seq_score": task_run.metrics.seq_score
                        if task_run.metrics
                        else None,
                        "actions": actions,
                        "outcome_mismatches": task_run.final_outcome_mismatches or [],
                    }
                )

            # SUS collection
            try:
                sus_result = await self.self_report_collector.collect_sus(
                    persona_context=persona_context,
                    task_summaries=task_summaries,
                )

                if sus_result:
                    await eval_repo.update_clone_sus(
                        clone,
                        sus_score=sus_result.sus_score,
                        sus_ratings=sus_result.ratings,
                        sus_reasoning=sus_result.reasoning,
                    )
                    logger.info("SUS score saved: %.1f/100", sus_result.sus_score)
                else:
                    logger.warning("SUS collection returned None (non-fatal)")

            except Exception as e:
                logger.warning("SUS collection failed (non-fatal): %s", e)

        finally:
            # close the browser
            await browser.close()

    async def _run_task(
        self,
        session: AsyncSession,
        evaluation_id: UUID,
        task_def: TaskDefinition,
        clone: EvaluationClone,
        controller: TwoAgentController,
        persona_context: str,
    ):
        """
        Run a single task for a clone.
        """

        logger.info("Task: %s...", task_def.description[:50])

        task_run_repo = TaskRunRepository(session)

        # create the task run record in DB before executing
        task_run = await task_run_repo.create(
            task_definition_id=task_def.id,
            evaluation_clone_id=clone.id,
        )

        try:
            # use task-level URL override if set, otherwise fall back to evaluation base URL
            start_url = task_def.base_url_override or task_def.evaluation.base_url

            # run the two-agent loop, TaskAbortedError propagates if cancelled mid-step
            result = await controller.execute_task(
                task_description=task_def.description,
                start_url=start_url,
                evaluation_id=evaluation_id,
                task_run_id=task_run.id,
                max_steps=task_def.max_steps,
            )

            task_run.agent_done = result.get("agent_done", False)

            # default to FAILED
            status = TaskRunStatus.FAILED
            verification_result = None
            verification_details = None

            if result["status"] == "COMPLETED":
                if task_def.verification_rules:
                    # run verification of rules
                    try:
                        verification = await controller.browser.verify(
                            task_def.verification_rules
                        )
                        verification_result = VR(verification.status.value)
                        verification_details = verification.details

                        if verification.status.value == "PASS":
                            status = TaskRunStatus.VERIFIED
                        elif verification.status.value == "FAIL":
                            status = TaskRunStatus.FAILED
                        elif verification.status.value == "SKIPPED":
                            status = TaskRunStatus.COMPLETED
                        else:
                            status = TaskRunStatus.COMPLETED

                    except Exception as e:
                        # verification crash is non-fatal, mark as COMPLETED with unknown result
                        logger.warning("Verification exception: %s", e)
                        status = TaskRunStatus.COMPLETED
                        verification_result = VR.UNKNOWN
                        verification_details = {
                            "type": "error",
                            "passed": False,
                            "evidence": f"Verification error: {str(e)}",
                        }
                else:
                    # no rules defined, agent DONE is taken as pass
                    status = TaskRunStatus.COMPLETED

            elif result["status"] == "TIMEOUT":
                # no verification run on timeout
                status = TaskRunStatus.TIMEOUT

            memory_state = result.get("memory_state", {})

            # save the task run outcome and final memory state to DB
            await task_run_repo.save_result(
                task_run=task_run,
                status=status,
                agent_done=result.get("agent_done", False),
                failure_reason=result.get("failure_reason"),
                verification_result=verification_result,
                verification_details=verification_details,
                final_action_history=memory_state.get("action_history"),
                final_action_summary=memory_state.get("action_summary"),
                final_outcome_mismatches=memory_state.get("outcome_mismatches"),
                finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )

            action_records = result.get("action_records", [])
            # calculate step count, failed actions, backtracking, outcome mismatches
            basic_metrics = self.metrics_calculator.calculate_basic_metrics(
                result=result,
                action_records=action_records,
            )

            logger.debug(
                "Basic metrics: steps=%d, failed=%d, mismatches=%d, backtrack=%d",
                basic_metrics["steps_taken"],
                basic_metrics["failed_action_count"],
                basic_metrics["outcome_mismatch_count"],
                basic_metrics["backtracking_count"],
            )

            # collect SEQ score using LLM call
            seq_result = None
            try:
                seq_actions = [
                    {
                        "step_number": action["step_number"],
                        "action_type": action["action_type"],
                        "reasoning": action.get("reasoning", ""),
                    }
                    for action in action_records
                ]

                seq_result = await self.self_report_collector.collect_seq(
                    task_description=task_def.description,
                    actions=seq_actions,
                    outcome_mismatches=memory_state.get("outcome_mismatches", []),
                    task_outcome=self._task_outcome_label(status, task_run.agent_done),
                    persona_context=persona_context,
                )
            except Exception as e:
                logger.warning("SEQ collection failed (non-fatal): %s", e)

            time_on_task_seconds = None
            if task_run.started_at and task_run.finished_at:
                time_on_task_seconds = int(
                    (task_run.finished_at - task_run.started_at).total_seconds()
                )

            visited_pages_total = result.get("visited_pages_total")
            visited_pages_unique = result.get("visited_pages_unique")
            # lostness requires min_pages to be defined on the task
            lostness = None
            if task_def.min_pages and visited_pages_total and visited_pages_unique:
                lostness = self.metrics_calculator.calculate_lostness(
                    N=visited_pages_total,
                    n=visited_pages_unique,
                    R=task_def.min_pages,
                )

            await task_run_repo.save_metrics(
                task_run_id=task_run.id,
                steps_taken=basic_metrics["steps_taken"],
                failed_action_count=basic_metrics["failed_action_count"],
                outcome_mismatch_count=basic_metrics["outcome_mismatch_count"],
                backtracking_count=basic_metrics["backtracking_count"],
                seq_score=seq_result.rating if seq_result else None,
                seq_reasoning=seq_result.reasoning if seq_result else None,
                time_on_task_seconds=time_on_task_seconds,
                visited_pages_total=visited_pages_total,
                visited_pages_unique=visited_pages_unique,
                lostness=lostness,
            )

            if seq_result:
                logger.info(
                    "SEQ: %s/7 - %s...",
                    seq_result.rating,
                    seq_result.reasoning[:60],
                )

            await task_run_repo.save_action_records(
                task_run_id=task_run.id,
                action_records=action_records,
            )

            await task_run_repo.commit()

            logger.info("Task completed: %s", task_run.status.value)

        except TaskAbortedError:
            # Mark this task run as aborted, then re-raise for the runner to handle cleanup
            await task_run_repo.mark_aborted(task_run, failure_reason="Aborted by user")
            await task_run_repo.commit()
            raise
        except Exception as e:
            logger.error("Task execution failed: %s", e)
            await task_run_repo.mark_aborted(task_run, failure_reason=str(e))
            await task_run_repo.commit()
            raise TaskRunFailedError(task_run.id, e) from e
