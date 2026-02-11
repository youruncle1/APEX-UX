from uuid import UUID
from typing import Optional, Literal
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.connection import AsyncSessionLocal
from src.database.models import (
    Evaluation, EvaluationStatus, EvaluationClone,
    TaskDefinition, TaskRun, TaskRunStatus, TaskRunMetrics,
    ActionRecord
)
from src.agents.grounder import GroundingAgent
from src.browser_connector.connector import BrowserConnector
from src.controller.controller import TwoAgentController
from src.evaluation.metrics import MetricsCalculator
from src.evaluation.self_report import SelfReportCollector


# Placeholder persona
PLACEHOLDER_PERSONA = """You are a typical web user with moderate technical skills.
You navigate websites carefully and read interface elements before interacting.
You prefer clear labels and visible feedback from your actions.
When encountering issues, you try alternative approaches rather than giving up immediately."""


class EvaluationRunner:
    """
    Main orchestrator for running evaluations.

    Responsibilities:
    - Load evaluation configuration from database
    - Initialize and manage evaluation session lifecycle
    - Coordinate execution across clones and tasks
    - Calculate metrics and collect SEQ scores
    - Manage database transactions for storing results
    - Handle error policies during execution

    Does NOT handle:
    - SUS collection (future - after all tasks per clone)
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
        self.metrics_calculator = MetricsCalculator()
        self.self_report_collector = SelfReportCollector()
    
    async def run(self, evaluation_id: UUID):
        """
        Run a complete evaluation.
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
                        
                        evaluation_failed = True

                # Mark evaluation as completed
                if evaluation_failed:
                    evaluation.status = EvaluationStatus.COMPLETED  
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
        Run all tasks for a single clone, then collect SUS score.
        """

        print(f"\n[Runner] Running clone: {clone.clone_id}")

        # Get persona context (placeholder)
        persona_context = PLACEHOLDER_PERSONA

        # Initialize agents
        grounder = GroundingAgent()
        browser = BrowserConnector(headless=True)

        # Initialize controller
        controller = TwoAgentController(
            persona_context=persona_context, 
            grounder=grounder,
            browser=browser
        )

        try:
            # Run each task
            for task_def in evaluation.tasks:
                try:
                    await self._run_task(
                        session=session,
                        evaluation_id=evaluation.id,
                        task_def=task_def,
                        clone=clone,
                        controller=controller,
                        persona_context=persona_context  # for SEQ collection
                    )
                except Exception as e:
                    print(f"[Runner] Task '{task_def.description[:50]}...' failed: {e}")
                    if self.error_policy == "fail_fast":
                        raise
                    
            print(f"\n[Runner] Collecting SUS for clone {clone.clone_id}...")
            
            # Collect task data for SUS prompt
            await session.refresh(clone, ["task_runs"])
            task_summaries = []
            
            for task_run in clone.task_runs:
                await session.refresh(task_run, ["task_definition", "metrics", "actions"])
                
                # Collect action history for this task from databasse
                full_action_history = [
                    {
                        "action_type": action.action_type,
                        "reasoning": action.reasoning or ""
                    }
                    for action in sorted(task_run.actions, key=lambda a: a.step_number)
                ]
                
                task_summaries.append({
                    "description": task_run.task_definition.description,
                    "status": task_run.status.value,
                    "steps_taken": task_run.metrics.steps_taken if task_run.metrics else 0,
                    "seq_score": task_run.metrics.seq_score if task_run.metrics else None,
                    "action_history": full_action_history, 
                    "outcome_mismatches": task_run.final_outcome_mismatches or []
                })
            
            # Collect SUS score
            try:
                sus_result = await self.self_report_collector.collect_sus(
                    persona_context=persona_context,
                    task_summaries=task_summaries  
                )
                
                if sus_result:
                    clone.sus_score = sus_result["sus_score"]
                    clone.sus_ratings = sus_result["ratings"]
                    clone.sus_reasoning = sus_result["reasoning"]
                    await session.commit()
                    print(f"[Runner] SUS score saved: {sus_result['sus_score']:.1f}/100")
                    print(f"[Runner] SUS ratings: {sus_result['ratings']}")
                    print(f"[Runner] SUS reasoning: {sus_result['reasoning'][:100]}...")
                else:
                    print("[Runner] SUS collection returned None (non-fatal)")
                    
            except Exception as e:
                print(f"[Runner] SUS collection failed (non-fatal): {e}")

        finally:
            # clean up browser resources
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

        Creates TaskRun record, executes using controller,
        calculates metrics, collects SEQ score, saves to database.

        Args:
            session: Database session
            evaluation_id: Evaluation UUID (for screenshot storage)
            task_def: Task definition to execute
            clone: Clone executing the task
            controller: Controller instance
            persona_context: Persona description (for SEQ collection)
        """

        print(f"\n[Runner] Task: {task_def.description[:50]}...")

        # Create TaskRun in DB
        task_run = TaskRun(
            task_definition_id=task_def.id,
            evaluation_clone_id=clone.id,
            status=TaskRunStatus.RUNNING,
            started_at=datetime.utcnow()
        )
        session.add(task_run)
        await session.flush()  
        await session.refresh(task_run)

        try:
            # Determine start URL
            start_url = task_def.base_url_override or task_def.evaluation.base_url

            # Execute task via controller
            result = await controller.execute_task(
                task_description=task_def.description,
                start_url=start_url,
                evaluation_id=evaluation_id,
                task_run_id=task_run.id,
                max_steps=task_def.max_steps,
                verification_rules=task_def.verification_rules
            )

            # Determine final status based on agent completion and verification
            task_run.agent_done = result.get("agent_done", False)
            
            if result["status"] == "COMPLETED":
                # Agent called DONE
                if task_def.verification_rules:
                    try:
                        verification = await controller.browser.verify(task_def.verification_rules)
                        
                        from src.database.models import VerificationResult as VR
                        task_run.verification_result = VR(verification.status.value)
                        task_run.verification_details = verification.details
                        
                        if verification.status.value == "PASS":
                            task_run.status = TaskRunStatus.VERIFIED
                        elif verification.status.value == "FAIL":
                            task_run.status = TaskRunStatus.FAILED
                        else:
                            task_run.status = TaskRunStatus.COMPLETED
                            # Log details instead of evidence
                            print(f"[Runner] Verification status: {verification.status.value}")

                    except Exception as e:
                        print(f"[Runner] Verification exception: {e}")
                        task_run.status = TaskRunStatus.COMPLETED
                        from src.database.models import VerificationResult as VR
                        task_run.verification_result = VR.UNKNOWN
                        task_run.verification_details = {
                            'type': 'error',
                            'passed': False,
                            'evidence': f"Verification error: {str(e)}"
                        }
                    
                    except Exception as e:
                        # Verification system error
                        print(f"[Runner] Verification exception: {e}")
                        task_run.status = TaskRunStatus.COMPLETED
                        from src.database.models import VerificationResult as VR
                        task_run.verification_result = VR.UNKNOWN
                        task_run.verification_evidence = f"Verification error: {str(e)}"
                else:
                    # No verification
                    task_run.status = TaskRunStatus.COMPLETED
            
            elif result["status"] == "TIMEOUT":
                # Max steps reached, agent never called DONE
                task_run.status = TaskRunStatus.TIMEOUT
                task_run.failure_reason = "Maximum steps reached"
            
            else:
                # Other failure (should not happen in normal flow)
                task_run.status = TaskRunStatus.FAILED
                task_run.failure_reason = result.get("failure_reason", "Unknown failure")

            task_run.finished_at = datetime.utcnow()

            # Save memory state
            memory_state = result.get("memory_state", {})
            task_run.final_action_history = memory_state.get("action_history")
            task_run.final_action_summary = memory_state.get("action_summary")
            task_run.final_outcome_mismatches = memory_state.get("outcome_mismatches")

            # METRICS CALCULATION
            # Calculate basic metrics
            action_records = result.get("action_records", [])
            basic_metrics = self.metrics_calculator.calculate_basic_metrics(
                result=result,
                action_records=action_records
            )
            
            print(f"[Runner] Basic metrics: steps={basic_metrics['steps_taken']}, "
                  f"failed={basic_metrics['failed_action_count']}, "
                  f"mismatches={basic_metrics['outcome_mismatch_count']}, "
                  f"backtrack={basic_metrics['backtracking_count']}")
            
            # Collect SEQ score
            seq_result = None
            try:
                # Collect full action history from action_records
                full_action_history = [
                    {
                        "action_type": action["action_type"],
                        "reasoning": action.get("reasoning", "")
                    }
                    for action in action_records
                ]
                
                # Build complete memory state for SEQ
                complete_memory_state = {
                    "action_history": full_action_history,  # ALL actions, not compressed
                    "action_summary": None,  # No summary needed with full history
                    "outcome_mismatches": memory_state.get("outcome_mismatches", [])
                }
                
                seq_result = await self.self_report_collector.collect_seq(
                    task_description=task_def.description,
                    memory_state=complete_memory_state,  # Use complete history
                    persona_context=persona_context
                )
            except Exception as e:
                print(f"[Runner] SEQ collection failed (non-fatal): {e}")
            
            # Create TaskRunMetrics
            metrics = TaskRunMetrics(
                task_run_id=task_run.id,
                steps_taken=basic_metrics["steps_taken"],
                failed_action_count=basic_metrics["failed_action_count"],
                outcome_mismatch_count=basic_metrics["outcome_mismatch_count"],
                backtracking_count=basic_metrics["backtracking_count"],
                seq_score=seq_result["rating"] if seq_result else None,
                seq_reasoning=seq_result["reasoning"] if seq_result else None
            )
            session.add(metrics)
            
            if seq_result:
                print(f"[Runner] SEQ: {seq_result['rating']}/7 - {seq_result['reasoning'][:60]}...")

            # Save action records
            for action_data in action_records:
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
                    screenshot=action_data.get("screenshot")
                )
                session.add(action_record)

            # Commit everything
            await session.commit()

            print(f"[Runner] Task completed: {task_run.status.value}")

        except Exception as e:
            print(f"[Runner] Task execution failed: {e}")
            task_run.status = TaskRunStatus.ABORTED
            task_run.failure_reason = str(e)
            task_run.finished_at = datetime.utcnow()
            await session.commit()
            raise
    
    async def _load_evaluation(
        self,
        session: AsyncSession,
        evaluation_id: UUID
    ) -> Optional[Evaluation]:
        """
        Load evaluation with all relationships from database.
        """

        result = await session.execute(
            select(Evaluation)
            .where(Evaluation.id == evaluation_id)
        )
        evaluation = result.scalar_one_or_none()

        if evaluation:
            await session.refresh(evaluation, ["clones", "tasks"])

        return evaluation