#!/usr/bin/env python3
"""
End-to-end test with database integration.

Creates an evaluation in database, runs it, and verifies data is saved correctly.
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

try:
    from src.database.connection import AsyncSessionLocal
except Exception:
    from src.database.models import AsyncSessionLocal

from src.database.models import (
    Evaluation,
    EvaluationStatus,
    EvaluationClone,
    TaskDefinition,
    TaskRun,
    TaskRunStatus,
    ActionRecord,
)
from src.agents.grounder import GroundingAgent
from src.browser_connector.connector import BrowserConnector
from src.controller.controller import TwoAgentController
from sqlalchemy import select


# Test configuration
TEST_URL = "https://www.google.com"
TEST_TASK = "Search for 'machine learning' on Google"
TEST_PERSONA = {"name": "Test User", "skills": "moderate", "style": "careful"}


async def create_test_evaluation():
    """Create test evaluation in database"""
    
    print("=" * 60)
    print("Creating Test Evaluation in Database")
    print("=" * 60)
    
    async with AsyncSessionLocal() as session:
        # Create evaluation
        evaluation = Evaluation(
            name="E2E Test Evaluation",
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
        
        # Create task definition
        task_def = TaskDefinition(
            evaluation_id=evaluation.id,
            description=TEST_TASK,
            max_steps=5,  # Short test
            verification_rules=None
        )
        session.add(task_def)
        await session.flush()
        
        print(f"✓ Task created: {task_def.id}")
        
        # Create task run
        task_run = TaskRun(
            task_definition_id=task_def.id,
            evaluation_clone_id=clone.id,
            status=TaskRunStatus.PENDING
        )
        session.add(task_run)
        
        await session.commit()
        
        # Refresh to get all IDs
        await session.refresh(evaluation)
        await session.refresh(clone)
        await session.refresh(task_def)
        await session.refresh(task_run)
        
        print(f"✓ Task run created: {task_run.id}")
        
        return evaluation.id, clone.id, task_def.id, task_run.id


async def run_task_with_controller(evaluation_id, task_run_id):
    """Run task using controller and return result"""
    
    print("\n" + "=" * 60)
    print("Executing Task with Controller")
    print("=" * 60)
    
    # Initialize components
    grounder = GroundingAgent()
    browser = BrowserConnector(headless=True)
    
    # BREAKING CHANGE: Controller now takes persona_context at initialization
    # Planner is created internally with persona baked into system prompt
    controller = TwoAgentController(
        persona_context=str(TEST_PERSONA),  # Moved here from execute_task
        grounder=grounder,
        browser=browser
    )
    
    try:
        # BREAKING CHANGE: execute_task no longer accepts persona_context
        result = await controller.execute_task(
            task_description=TEST_TASK,
            start_url=TEST_URL,
            evaluation_id=evaluation_id,
            task_run_id=task_run_id,
            max_steps=10,
            verification_rules=None
        )
        
        print(f"\n✓ Execution completed: {result['status']}")
        print(f"✓ Steps taken: {result['steps_taken']}")
        print(f"✓ Action records: {len(result['action_records'])}")
        
        return result
        
    finally:
        await browser.close()


async def save_results_to_database(task_run_id, result):
    """Save execution results to database"""
    
    print("\n" + "=" * 60)
    print("Saving Results to Database")
    print("=" * 60)
    
    async with AsyncSessionLocal() as session:
        # Get task run
        db_result = await session.execute(
            select(TaskRun).where(TaskRun.id == task_run_id)
        )
        task_run = db_result.scalar_one()
        
        # Update task run
        task_run.status = TaskRunStatus(result['status'])
        task_run.started_at = datetime.now()
        task_run.finished_at = datetime.now()
        task_run.agent_done = result.get('agent_done', False)
        
        if 'failure_reason' in result:
            task_run.failure_reason = result['failure_reason']
        
        # Save memory state
        memory_state = result.get('memory_state', {})
        task_run.final_action_history = memory_state.get('action_history')
        task_run.final_action_summary = memory_state.get('action_summary')
        task_run.final_outcome_mismatches = memory_state.get('outcome_mismatches')
        
        print(f"\n✓ Task run updated: {task_run.status.value}")
        
        # Save action records
        action_count = 0
        for action_data in result.get('action_records', []):
            action = ActionRecord(
                task_run_id=task_run.id,
                step_number=action_data['step_number'],
                action_type=action_data['action_type'],
                target_description=action_data.get('target_description'),
                input_value=action_data.get('input_value'),
                reasoning=action_data.get('reasoning'),
                bounding_box=action_data.get('bounding_box'),
                click_coordinates=action_data.get('click_coordinates'),
                execution_success=action_data['execution_success'],
                execution_feedback=action_data.get('execution_feedback'),
                timestamp=datetime.fromisoformat(action_data['timestamp']),
                screenshot=action_data.get('screenshot')
            )
            session.add(action)
            action_count += 1
        
        await session.commit()
        
        print(f"✓ Saved {action_count} action records")


async def verify_database_content(evaluation_id):
    """Query and display saved data"""
    
    print("\n" + "=" * 60)
    print("Verifying Database Content")
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
        print(f"  Clones: {len(evaluation.clones)}")
        print(f"  Tasks: {len(evaluation.tasks)}")
        
        for clone in evaluation.clones:
            await session.refresh(clone, ["task_runs"])
            print(f"\n  Clone: {clone.clone_id}")
            print(f"    Task runs: {len(clone.task_runs)}")
        
        for task_def in evaluation.tasks:
            await session.refresh(task_def, ["runs"])
            print(f"\n  Task: {task_def.description[:50]}...")
            print(f"    Runs: {len(task_def.runs)}")
            
            for run in task_def.runs:
                await session.refresh(run, ["actions"])
                print(f"\n    Run: {run.id}")
                print(f"      Status: {run.status.value}")
                print(f"      Agent done: {run.agent_done}")
                print(f"      Actions: {len(run.actions)}")
                
                if run.actions:
                    print(f"\n      Sample actions:")
                    for action in run.actions[:3]:
                        print(f"        Step {action.step_number}: {action.action_type}")
                        print(f"          Success: {action.execution_success}")
                        if action.screenshot:
                            print(f"          Screenshot: {action.screenshot}")
                
                if run.final_outcome_mismatches:
                    print(f"\n      Outcome mismatches: {len(run.final_outcome_mismatches)}")
        
        print("\n" + "=" * 60)
        print("✓ Database content verified!")
        print("=" * 60)


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
    print("APEX-UX End-to-End Database Test")
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
        evaluation_id, clone_id, task_def_id, task_run_id = await create_test_evaluation()
        
        # Step 2: Run task with controller
        result = await run_task_with_controller(evaluation_id, task_run_id)
        
        # Step 3: Save results to database
        await save_results_to_database(task_run_id, result)
        
        # Step 4: Verify database content
        await verify_database_content(evaluation_id)
        
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