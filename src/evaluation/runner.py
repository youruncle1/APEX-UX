from uuid import UUID
from typing import Optional, Literal
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from src.database.connection import AsyncSessionLocal
from src.database.models import (
    Evaluation, EvaluationStatus, EvaluationClone,
    TaskDefinition, TaskRun, TaskRunStatus,
    VerificationResult as VR,
)
from src.database.repositories import EvaluationRepository, TaskRunRepository
from src.agents.logic.grounder import GroundingAgent
from src.browser_connector.connector import BrowserConnector
from src.controller.controller import TwoAgentController
from src.evaluation.logic.metrics import MetricsCalculator
from src.evaluation.self_report import SelfReportCollector
from src.evaluation.api.websocket import evaluation_cancellation, broadcast_progress
from src.storage import create_screenshot_storage


# Placeholder persona
PLACEHOLDER_PERSONA = """You are a typical web user with moderate technical skills.
You navigate websites carefully and read interface elements before interacting.
You prefer clear labels and visible feedback from your actions.
When encountering issues, you try alternative approaches rather than giving up immediately."""


class EvaluationRunner:
    """
    Main orchestrator for running evaluations.
    """

    def __init__(
        self,
        error_policy: Literal["fail_fast", "continue_on_error"] = "continue_on_error"
    ):
        self.error_policy = error_policy
        self.metrics_calculator = MetricsCalculator()
        self.self_report_collector = SelfReportCollector()
        self.screenshot_storage = create_screenshot_storage()

    def _is_cancelled(self, evaluation_id: UUID) -> bool:
        return evaluation_cancellation.get(evaluation_id, False)

    async def run(self, evaluation_id: UUID):
        """Run a complete evaluation."""

        async with AsyncSessionLocal() as session:
            eval_repo = EvaluationRepository(session)

            evaluation = await eval_repo.get_with_relations(evaluation_id)
            if not evaluation:
                raise ValueError(f"Evaluation {evaluation_id} not found")

            print(f"[Runner] Starting evaluation: {evaluation.name or evaluation.id}")
            print(f"[Runner] Tasks: {len(evaluation.tasks)}, Clones: {len(evaluation.clones)}")
            print(f"[Runner] Error policy: {self.error_policy}")

            await eval_repo.update_status(evaluation, EvaluationStatus.RUNNING, started_at=datetime.utcnow(),)

            evaluation_failed = False
            try:
                for clone in evaluation.clones:
                    # Check cancellation between clones
                    if self._is_cancelled(evaluation_id):
                        print(f"[Runner] Abort requested before clone {clone.clone_id}, stopping.")
                        await self._abort_and_cleanup(evaluation_id, session, eval_repo)
                        return

                    try:
                        await self._run_clone(session, evaluation, clone, evaluation_id)
                    except Exception as e:
                        print(f"[Runner] Clone {clone.clone_id} failed: {e}")
                        if self.error_policy == "fail_fast":
                            raise
                        evaluation_failed = True

                    # Check cancellation after each clone completes
                    if self._is_cancelled(evaluation_id):
                        print(f"[Runner] Abort requested after clone {clone.clone_id}, stopping.")
                        await self._abort_and_cleanup(evaluation_id, session, eval_repo)
                        return

                await eval_repo.update_status(
                    evaluation,
                    EvaluationStatus.COMPLETED,
                    finished_at=datetime.utcnow(),
                )
                print(f"[Runner] Evaluation completed {'with failures' if evaluation_failed else 'successfully'}")

            except Exception as e:
                print(f"[Runner] Evaluation aborted due to exception: {e}")
                await eval_repo.update_status(
                    evaluation,
                    EvaluationStatus.ABORTED,
                    finished_at=datetime.utcnow(),
                )
                raise

    async def _abort_and_cleanup(
        self,
        evaluation_id: UUID,
        session: AsyncSession,
        eval_repo: EvaluationRepository,
    ):
        """
        Clean up all task run data created during this evaluation run,
        delete screenshots, reset clone SUS data, and return evaluation to PENDING.
        """
        print(f"[Runner] Aborting and cleaning up evaluation {evaluation_id}")

        # Delete all task runs for this evaluation (cascades to ActionRecord + TaskRunMetrics)
        # We need the task run IDs first to know which ones belong to this evaluation
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
            select(EvaluationClone).where(EvaluationClone.evaluation_id == evaluation_id)
        )
        clones = result.scalars().all()
        for clone in clones:
            clone.sus_score = None
            clone.sus_ratings = None
            clone.sus_reasoning = None

        # Delete screenshots
        try:
            await self.screenshot_storage.delete_evaluation(str(evaluation_id))
            print(f"[Runner] Screenshots deleted for evaluation {evaluation_id}")
        except Exception as e:
            print(f"[Runner] Failed to delete screenshots (non-fatal): {e}")

        # Reset evaluation to PENDING
        result = await session.execute(
            select(Evaluation).where(Evaluation.id == evaluation_id)
        )
        evaluation = result.scalar_one_or_none()
        if evaluation:
            evaluation.status = EvaluationStatus.PENDING
            evaluation.finished_at = None

        await session.commit()

        # Broadcast reset to clients
        await broadcast_progress(evaluation_id, {
            "type": "status",
            "status": "ABORTED_RESET",
            "message": "Evaluation aborted. All data cleared, returned to Pending.",
        })

        # Clean up cancellation flag
        if evaluation_id in evaluation_cancellation:
            del evaluation_cancellation[evaluation_id]

        print(f"[Runner] Cleanup complete, evaluation reset to PENDING")

    async def _run_clone(
        self,
        session: AsyncSession,
        evaluation: Evaluation,
        clone: EvaluationClone,
        evaluation_id: UUID,
    ):
        """Run all tasks for a single clone, then collect SUS score."""

        print(f"\n[Runner] Running clone: {clone.clone_id}")

        persona_context = PLACEHOLDER_PERSONA

        grounder = GroundingAgent()
        browser = BrowserConnector(headless=True)

        controller = TwoAgentController(
            persona_context=persona_context,
            grounder=grounder,
            browser=browser,
        )

        eval_repo = EvaluationRepository(session)
        task_run_repo = TaskRunRepository(session)

        try:
            for task_def in evaluation.tasks:
                # Check cancellation between tasks
                if self._is_cancelled(evaluation_id):
                    print(f"[Runner] Abort requested before task '{task_def.description[:40]}...', stopping.")
                    return  # Caller (_run_clone) returns, run() will handle cleanup

                try:
                    await self._run_task(
                        session=session,
                        evaluation_id=evaluation.id,
                        task_def=task_def,
                        clone=clone,
                        controller=controller,
                        persona_context=persona_context,
                    )
                except Exception as e:
                    print(f"[Runner] Task '{task_def.description[:50]}...' failed: {e}")
                    if self.error_policy == "fail_fast":
                        raise

                # Check cancellation after each task completes
                if self._is_cancelled(evaluation_id):
                    print(f"[Runner] Abort requested after task '{task_def.description[:40]}...', stopping.")
                    return  # Caller will handle cleanup

            # Only collect SUS if we completed all tasks without abort
            print(f"\n[Runner] Collecting SUS for clone {clone.clone_id}...")

            await eval_repo.refresh_clone_task_runs(clone)
            task_summaries = []

            for task_run in clone.task_runs:
                await eval_repo.refresh_task_run_relations(task_run)

                full_action_history = [
                    {
                        "action_type": action.action_type,
                        "reasoning": action.reasoning or "",
                    }
                    for action in sorted(task_run.actions, key=lambda a: a.step_number)
                ]

                task_summaries.append({
                    "description": task_run.task_definition.description,
                    "status": task_run.status.value,
                    "steps_taken": task_run.metrics.steps_taken if task_run.metrics else 0,
                    "seq_score": task_run.metrics.seq_score if task_run.metrics else None,
                    "action_history": full_action_history,
                    "outcome_mismatches": task_run.final_outcome_mismatches or [],
                })

            try:
                sus_result = await self.self_report_collector.collect_sus(
                    persona_context=persona_context,
                    task_summaries=task_summaries,
                )

                if sus_result:
                    await eval_repo.update_clone_sus(
                        clone,
                        sus_score=sus_result["sus_score"],
                        sus_ratings=sus_result["ratings"],
                        sus_reasoning=sus_result["reasoning"],
                    )
                    print(f"[Runner] SUS score saved: {sus_result['sus_score']:.1f}/100")
                else:
                    print("[Runner] SUS collection returned None (non-fatal)")

            except Exception as e:
                print(f"[Runner] SUS collection failed (non-fatal): {e}")

        finally:
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
        """Run a single task for a clone."""

        print(f"\n[Runner] Task: {task_def.description[:50]}...")

        task_run_repo = TaskRunRepository(session)

        task_run = await task_run_repo.create(
            task_definition_id=task_def.id,
            evaluation_clone_id=clone.id,
        )

        try:
            start_url = task_def.base_url_override or task_def.evaluation.base_url

            result = await controller.execute_task(
                task_description=task_def.description,
                start_url=start_url,
                evaluation_id=evaluation_id,
                task_run_id=task_run.id,
                max_steps=task_def.max_steps,
                verification_rules=task_def.verification_rules,
            )

            task_run.agent_done = result.get("agent_done", False)

            status = TaskRunStatus.FAILED
            verification_result = None
            verification_details = None

            if result["status"] == "COMPLETED":
                if task_def.verification_rules:
                    try:
                        verification = await controller.browser.verify(task_def.verification_rules)
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
                        print(f"[Runner] Verification exception: {e}")
                        status = TaskRunStatus.COMPLETED
                        verification_result = VR.UNKNOWN
                        verification_details = {
                            "type": "error",
                            "passed": False,
                            "evidence": f"Verification error: {str(e)}",
                        }
                else:
                    status = TaskRunStatus.COMPLETED

            elif result["status"] == "TIMEOUT":
                status = TaskRunStatus.TIMEOUT

            memory_state = result.get("memory_state", {})

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
                finished_at=datetime.utcnow(),
            )

            action_records = result.get("action_records", [])
            basic_metrics = self.metrics_calculator.calculate_basic_metrics(
                result=result,
                action_records=action_records,
            )

            print(f"[Runner] Basic metrics: steps={basic_metrics['steps_taken']}, "
                  f"failed={basic_metrics['failed_action_count']}, "
                  f"mismatches={basic_metrics['outcome_mismatch_count']}, "
                  f"backtrack={basic_metrics['backtracking_count']}")

            seq_result = None
            try:
                full_action_history = [
                    {
                        "action_type": action["action_type"],
                        "reasoning": action.get("reasoning", ""),
                    }
                    for action in action_records
                ]

                complete_memory_state = {
                    "action_history": full_action_history,
                    "action_summary": None,
                    "outcome_mismatches": memory_state.get("outcome_mismatches", []),
                }

                seq_result = await self.self_report_collector.collect_seq(
                    task_description=task_def.description,
                    memory_state=complete_memory_state,
                    persona_context=persona_context,
                )
            except Exception as e:
                print(f"[Runner] SEQ collection failed (non-fatal): {e}")

            await task_run_repo.save_metrics(
                task_run_id=task_run.id,
                steps_taken=basic_metrics["steps_taken"],
                failed_action_count=basic_metrics["failed_action_count"],
                outcome_mismatch_count=basic_metrics["outcome_mismatch_count"],
                backtracking_count=basic_metrics["backtracking_count"],
                seq_score=seq_result["rating"] if seq_result else None,
                seq_reasoning=seq_result["reasoning"] if seq_result else None,
            )

            if seq_result:
                print(f"[Runner] SEQ: {seq_result['rating']}/7 - {seq_result['reasoning'][:60]}...")

            await task_run_repo.save_action_records(
                task_run_id=task_run.id,
                action_records=action_records,
            )

            await task_run_repo.commit()

            print(f"[Runner] Task completed: {task_run.status.value}")

        except Exception as e:
            print(f"[Runner] Task execution failed: {e}")
            await task_run_repo.mark_aborted(task_run, failure_reason=str(e))
            await task_run_repo.commit()
            raise