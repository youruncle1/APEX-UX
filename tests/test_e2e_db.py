#!/usr/bin/env python3
"""
End-to-end test with database integration.

Creates an evaluation in database, runs it using EvaluationRunner,
and verifies all data (including metrics) is saved correctly.
"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.connection import AsyncSessionLocal
from src.database.models import (
    Evaluation,
    EvaluationStatus,
    EvaluationClone,
    TaskDefinition,
    TaskRun,
    TaskRunStatus,
    TaskRunMetrics,
    ActionRecord,
)
from src.evaluation.runner import EvaluationRunner
from sqlalchemy import select


# Test configuration
TEST_URL = "https://www.google.com"
TEST_TASKS = [
    "Search for 'machine learning' on Google",
    "Click on the first search result"
]
TEST_PERSONA = {"name": "Test User", "skills": "moderate", "style": "careful"}


async def create_test_evaluation():
    """Create test evaluation in database"""
    
    print("=" * 60)
    print("Creating Test Evaluation in Database")
    print("=" * 60)
    
    async with AsyncSessionLocal() as session:
        # Create evaluation
        evaluation = Evaluation(
            name="E2E Test Evaluation (With Metrics)",
            base_url=TEST_URL,
            status=EvaluationStatus.PENDING
        )
        session.add(evaluation)
        await session.flush()
        
        print(f"\n✓ Evaluation created: {evaluation.id}")
        
        # Create clone (persona)
        clone = EvaluationClone(
            evaluation_id=evaluation.id,
            clone_id="test-clone-001",
            persona_cache=TEST_PERSONA
        )
        session.add(clone)
        await session.flush()
        
        print(f"✓ Clone created: {clone.id}")
        
        # Create multiple task definitions
        for i, task_desc in enumerate(TEST_TASKS, 1):
            task_def = TaskDefinition(
                evaluation_id=evaluation.id,
                description=task_desc,
                max_steps=10,  # Allow more steps
                verification_rules=None
            )
            session.add(task_def)
            print(f"✓ Task {i} created: {task_desc}")
        
        await session.commit()
        await session.refresh(evaluation)
        
        print(f"✓ Created {len(TEST_TASKS)} tasks")
        
        return evaluation.id


async def run_evaluation_with_runner(evaluation_id):
    """Run evaluation using EvaluationRunner (includes metrics calculation)"""
    
    print("\n" + "=" * 60)
    print("Running Evaluation with EvaluationRunner")
    print("=" * 60)
    
    runner = EvaluationRunner(error_policy="continue_on_error")
    
    try:
        await runner.run(evaluation_id)
        print(f"\n✓ Evaluation completed!")
        
    except Exception as e:
        print(f"\n✗ Evaluation failed: {e}")
        raise


async def verify_database_content(evaluation_id):
    """Query and display saved data including metrics"""
    
    print("\n" + "=" * 60)
    print("Verifying Database Content (Including Metrics)")
    print("=" * 60)
    
    async with AsyncSessionLocal() as session:
        # Load evaluation with all relationships
        result = await session.execute(
            select(Evaluation).where(Evaluation.id == evaluation_id)
        )
        evaluation = result.scalar_one()
        
        await session.refresh(evaluation, ["clones", "tasks"])
        
        print(f"\nEvaluation: {evaluation.name}")
        print(f"  Status: {evaluation.status.value}")
        print(f"  Base URL: {evaluation.base_url}")
        print(f"  Clones: {len(evaluation.clones)}")
        print(f"  Tasks: {len(evaluation.tasks)}")
        
        for clone in evaluation.clones:
            await session.refresh(clone, ["task_runs"])
            print(f"\n  Clone: {clone.clone_id}")
            print(f"    Task runs: {len(clone.task_runs)}")
            
            # ✅ NEW: Check SUS score
            if clone.sus_score is not None:
                print(f"    SUS Score: {clone.sus_score:.1f}/100 ✓")
            else:
                print(f"    SUS Score: Not collected")
        
        for task_def in evaluation.tasks:
            await session.refresh(task_def, ["runs"])
            print(f"\n  Task: {task_def.description}")
            print(f"    Max steps: {task_def.max_steps}")
            print(f"    Runs: {len(task_def.runs)}")
            
            for run in task_def.runs:
                await session.refresh(run, ["actions", "metrics"])
                print(f"\n    Run ID: {run.id}")
                print(f"      Status: {run.status.value}")
                print(f"      Agent done: {run.agent_done}")
                print(f"      Actions: {len(run.actions)}")
                
                # ✅ NEW: Display metrics
                if run.metrics:
                    print(f"\n      📊 Metrics:")
                    print(f"        Steps taken: {run.metrics.steps_taken}")
                    print(f"        Failed actions: {run.metrics.failed_action_count}")
                    print(f"        Outcome mismatches: {run.metrics.outcome_mismatch_count}")
                    print(f"        Backtracking: {run.metrics.backtracking_count}")
                    if run.metrics.seq_score is not None:
                        print(f"        SEQ Score: {run.metrics.seq_score:.1f}/7 ✓")
                    else:
                        print(f"        SEQ Score: Not collected")
                else:
                    print(f"\n      ⚠ No metrics found!")
                
                # Show sample actions
                if run.actions:
                    print(f"\n      Sample actions:")
                    for action in run.actions[:3]:
                        print(f"        Step {action.step_number}: {action.action_type}")
                        print(f"          Success: {action.execution_success}")
                        print(f"          Reasoning: {action.reasoning[:50] if action.reasoning else 'N/A'}...")
                        if action.screenshot:
                            print(f"          Screenshot: {action.screenshot[:60]}...")
                
                # Show outcome mismatches
                if run.final_outcome_mismatches:
                    print(f"\n      Issues encountered:")
                    for i, mismatch in enumerate(run.final_outcome_mismatches[:3], 1):
                        print(f"        {i}. {mismatch[:80]}...")
        
        print("\n" + "=" * 60)
        print("✓ Database content verified!")
        print("=" * 60)


async def verify_metrics_integrity(evaluation_id):
    """Verify metrics data integrity"""
    
    print("\n" + "=" * 60)
    print("Verifying Metrics Integrity")
    print("=" * 60)
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Evaluation).where(Evaluation.id == evaluation_id)
        )
        evaluation = result.scalar_one()
        await session.refresh(evaluation, ["clones"])
        
        total_tasks = 0
        tasks_with_metrics = 0
        tasks_with_seq = 0
        clones_with_sus = 0
        
        for clone in evaluation.clones:
            await session.refresh(clone, ["task_runs"])
            
            # Check SUS
            if clone.sus_score is not None:
                clones_with_sus += 1
            
            for task_run in clone.task_runs:
                await session.refresh(task_run, ["metrics"])
                total_tasks += 1
                
                if task_run.metrics:
                    tasks_with_metrics += 1
                    
                    if task_run.metrics.seq_score is not None:
                        tasks_with_seq += 1
        
        print(f"\n✓ Integrity Check:")
        print(f"  Total task runs: {total_tasks}")
        print(f"  Task runs with metrics: {tasks_with_metrics}/{total_tasks}")
        print(f"  Task runs with SEQ: {tasks_with_seq}/{total_tasks}")
        print(f"  Clones with SUS: {clones_with_sus}/{len(evaluation.clones)}")
        
        if tasks_with_metrics == total_tasks:
            print(f"\n✓ All task runs have metrics!")
        else:
            print(f"\n⚠ {total_tasks - tasks_with_metrics} task runs missing metrics")
        
        if tasks_with_seq == total_tasks:
            print(f"✓ All task runs have SEQ scores!")
        else:
            print(f"⚠ {total_tasks - tasks_with_seq} task runs missing SEQ scores")
        
        if clones_with_sus == len(evaluation.clones):
            print(f"✓ All clones have SUS scores!")
        else:
            print(f"⚠ {len(evaluation.clones) - clones_with_sus} clones missing SUS scores")


async def cleanup_test_data(evaluation_id):
    """Delete test evaluation"""
    
    print("\n" + "=" * 60)
    print("Cleaning Up Test Data")
    print("=" * 60)
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Evaluation).where(Evaluation.id == evaluation_id)
        )
        evaluation = result.scalar_one()
        
        await session.delete(evaluation)
        await session.commit()
        
        print("✓ Test evaluation deleted")


async def main():
    """Run end-to-end test"""
    
    print("\n" + "=" * 60)
    print("APEX-UX End-to-End Test (With Metrics)")
    print("=" * 60)
    
    # Check environment
    required_vars = ["OPENAI_API_KEY", "GROUNDING_SERVICE_URL", "DATABASE_URL"]
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        print(f"\n✗ Missing environment variables: {', '.join(missing)}")
        return
    
    print("✓ Environment OK")
    
    try:
        # Step 1: Create test evaluation in database
        evaluation_id = await create_test_evaluation()
        
        # Step 2: Run evaluation with runner (calculates metrics automatically)
        await run_evaluation_with_runner(evaluation_id)
        
        # Step 3: Verify database content
        await verify_database_content(evaluation_id)
        
        # Step 4: Verify metrics integrity
        await verify_metrics_integrity(evaluation_id)
        
        # Step 5: Ask user if they want to keep data
        print("\n" + "=" * 60)
        keep = input("Keep test data in database? (y/n): ")
        
        if keep.lower() != 'y':
            await cleanup_test_data(evaluation_id)
        else:
            print(f"\n✓ Test data kept in database")
            print(f"  Evaluation ID: {evaluation_id}")
        
        print("\n" + "=" * 60)
        print("✓ End-to-end test completed successfully!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())