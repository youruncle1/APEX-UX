# src/controller/controller.py

import os
from typing import Optional
from datetime import datetime
from uuid import UUID

from src.agents.planner import PlannerAgent
from src.agents.grounder import GroundingAgent
from src.browser_connector.connector import BrowserConnector
from src.browser_connector.actions import Action, ActionType
from src.storage import create_screenshot_storage
from .memory import MemoryManager

class TwoAgentController:
    """
    Orchestrates the planner-grounder-browser loop for task execution.
    """
    
    def __init__(
        self,
        persona_context: str,
        grounder: GroundingAgent,
        browser: BrowserConnector,
        compression_threshold: int = 5,
        planner_backend: Optional[str] = None  # NEW: Allow backend override
    ):
        """
        Initialize controller with persona context.
        
        Args:
            persona_context: Persona description for planner
            grounder: Grounding agent instance
            browser: Browser connector instance
            compression_threshold: History compression threshold
            planner_backend: Override for planner backend ("openai" or "qwen")
        """
        # Initialize planner with backend selection
        self.planner = PlannerAgent(
            persona_context=persona_context,
            backend=planner_backend  # Uses env var if None
        )
        self.grounder = grounder
        self.browser = browser
        self.compression_threshold = compression_threshold
        self.screenshot_storage = create_screenshot_storage()
    
    # ... rest of the controller remains the same
    async def execute_task(
        self,
        task_description: str,
        start_url: str,
        evaluation_id: UUID, 
        task_run_id: UUID,   
        max_steps: int = 50,
        verification_rules: Optional[dict] = None
    ) -> dict:
        """
        Execute a complete task run.
        
        Returns a dict with:
        - status: "VERIFIED" | "FAILED" | "TIMEOUT"
        - steps_taken: int
        - action_records: list[dict]
        - memory_state: dict
        - agent_done: bool
        - verification_result: dict (if rules provided)
        """
        
        # Initialize browser
        await self.browser.start(start_url)
        
        # Initialize memory
        memory = MemoryManager(compression_threshold=self.compression_threshold)
        
        # Action records for database
        action_records = []
        
        # Main interaction loop
        for step in range(1, max_steps + 1):
            print(f"\n[Controller] Step {step}/{max_steps}")
            
            # Capture screenshot (base64 PNG)
            screenshot_base64 = await self.browser.screenshot()
            
            # Save screenshot to storage and get URL
            screenshot_url = await self.screenshot_storage.save(
                evaluation_id=str(evaluation_id),
                task_run_id=str(task_run_id),
                step=step,
                screenshot_base64=screenshot_base64
            )
            print(f"[Controller] Screenshot saved: {screenshot_url}")
            
            # Maybe compress memory if threshold reached
            await memory.maybe_compress()
            
            # Plan next action (with retry logic for robustness)
            planner_action = None
            for retry in range(3):  # Max 3 retries for invalid output
                planner_action = await self.planner.plan(
                    task_description=task_description,  
                    screenshot_base64=screenshot_base64,
                    action_history=memory.action_history,
                    action_summary=memory.action_summary,
                    outcome_mismatches=memory.outcome_mismatches
                )
                
                if planner_action:
                    break
                
                print(f"[Controller] Planner output invalid, retry {retry + 1}/3")
            
            if not planner_action:
                print("[Controller] Failed to get valid action from planner, aborting")
                return {
                    "status": "FAILED",
                    "steps_taken": step,
                    "action_records": action_records,
                    "memory_state": memory.get_state(),
                    "failure_reason": "Planner failed to produce valid action",
                    "agent_done": False
                }
            
            print(f"[Controller] Planner: {planner_action.action_type} - {planner_action.reasoning}")
            
            # Check if task is done
            if planner_action.action_type == "DONE":
                # Verify if rules provided
                if verification_rules:
                    verification = await self.browser.verify(verification_rules)
                    
                    return {
                        "status": "VERIFIED" if verification.status == "PASS" else "FAILED",
                        "agent_done": True,
                        "steps_taken": step,
                        "action_records": action_records,
                        "memory_state": memory.get_state(),
                        "verification_result": {
                            "status": verification.status,
                            "evidence": verification.evidence
                        }
                    }
                else:
                    # No verification, trust agent
                    return {
                        "status": "VERIFIED",
                        "steps_taken": step,
                        "agent_done": True,
                        "action_records": action_records,
                        "memory_state": memory.get_state()
                    }
            
            # Ground if needed
            coordinates = None
            bounding_box = None
            
            if self._needs_grounding(planner_action.action_type):
                if not planner_action.target_description:
                    print(f"[Controller] Warning: {planner_action.action_type} needs target_description but none provided")
                
                bbox = await self.grounder.ground(
                    screenshot_base64=screenshot_base64,
                    target_description=planner_action.target_description or ""
                )
                
                if bbox:
                    coordinates = bbox.center()
                    bounding_box = {"x1": bbox.x1, "y1": bbox.y1, "x2": bbox.x2, "y2": bbox.y2}
                    print(f"[Controller] Grounder: {coordinates}")
                else:
                    print("[Controller] Grounding failed, skipping action")
                    # Record failed grounding
                    action_records.append({
                        "step_number": step,
                        "action_type": planner_action.action_type,
                        "target_description": planner_action.target_description,
                        "reasoning": planner_action.reasoning,
                        "bounding_box": None,
                        "click_coordinates": None,
                        "execution_success": False,
                        "execution_feedback": "Grounding failed",
                        "timestamp": datetime.utcnow().isoformat(),
                        "screenshot": screenshot_url
                    })
                    
                    # Add to memory as failed action
                    memory.add_action({
                        "action_type": planner_action.action_type,
                        "reasoning": planner_action.reasoning
                    })
                    memory.add_outcome_mismatch(
                        f"Failed to locate element: {planner_action.target_description}"
                    )
                    
                    continue  # Skip to next step
            
            # Build browser action
            browser_action = self._build_browser_action(planner_action, coordinates)
            
            # Execute action
            result = await self.browser.execute_action(browser_action)
            print(f"[Controller] Execution: {result.feedback}")
            
            # Record action (only store URL, not base64!)
            action_record = {
                "step_number": step,
                "action_type": planner_action.action_type,
                "target_description": planner_action.target_description,
                "input_value": planner_action.input_value,
                "reasoning": planner_action.reasoning,
                "bounding_box": bounding_box,
                "click_coordinates": {"x": coordinates[0], "y": coordinates[1]} if coordinates else None,
                "execution_success": result.success,
                "execution_feedback": result.feedback,
                "timestamp": datetime.now().isoformat(),
                "screenshot": screenshot_url
            }
            action_records.append(action_record)
            
            # Update memory
            memory.add_action({
                "action_type": planner_action.action_type,
                "reasoning": planner_action.reasoning
            })
            
            # Add outcome mismatch if reported by planner
            if planner_action.outcome_mismatch:
                memory.add_outcome_mismatch(planner_action.outcome_mismatch)
        
        # Max steps reached
        return {
            "status": "TIMEOUT",
            "steps_taken": max_steps,
            "action_records": action_records,
            "memory_state": memory.get_state(),
            "failure_reason": "Maximum steps reached",
            "agent_done": False
        }
    
    def _needs_grounding(self, action_type: str) -> bool:
        """Check if action needs grounding"""
        return action_type in ["CLICK", "HOVER", "DRAG_AND_DROP"]
    
    def _build_browser_action(self, planner_action, coordinates: Optional[tuple[int, int]]) -> Action:
        """Convert planner action to browser action"""
        
        return Action(
            action_type=ActionType(planner_action.action_type),
            x=coordinates[0] if coordinates else None,
            y=coordinates[1] if coordinates else None,
            text=planner_action.input_value,
            key=planner_action.input_value,  # For PRESS_KEY
        )