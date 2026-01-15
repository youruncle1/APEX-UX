import os
from typing import Optional
from datetime import datetime
from uuid import UUID

from src.agents.planner import PlannerAgent
from src.agents.grounder import GroundingAgent
from src.browser_connector.connector import BrowserConnector
from src.browser_connector.actions import Action, ActionType
from .memory import MemoryManager

class TwoAgentController:
    """
    Orchestrates the planner-grounder-browser loop for task execution.
    """
    
    def __init__(
        self,
        planner: PlannerAgent,
        grounder: GroundingAgent,
        browser: BrowserConnector,
        max_recent_actions: int = 5
    ):
        self.planner = planner
        self.grounder = grounder
        self.browser = browser
        self.max_recent_actions = max_recent_actions
    
    async def execute_task(
        self,
        task_description: str,
        persona_context: str,
        start_url: str,
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
        memory = MemoryManager(max_recent_actions=self.max_recent_actions)
        
        # Action records for database
        action_records = []
        
        # Main interaction loop
        for step in range(1, max_steps + 1):
            print(f"\n[Controller] Step {step}/{max_steps}")
            
            # Get screenshot
            screenshot = await self.browser.screenshot()
            
            # Plan next action (with retry on parse failure)
            planner_action = None
            for retry in range(3):  # Max 3 retries for invalid output
                planner_action = await self.planner.plan(
                    task_description=task_description,
                    persona_context=persona_context,
                    screenshot_base64=screenshot,
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
                    # Continue anyway, will likely fail in execution
                
                bbox = await self.grounder.ground(
                    screenshot_base64=screenshot,
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
                        "screenshot": screenshot
                    })
                    
                    continue  # Skip to next step
            
            # Build browser action
            browser_action = self._build_browser_action(planner_action, coordinates)
            
            # Execute action
            result = await self.browser.execute_action(browser_action)
            print(f"[Controller] Execution: {result.feedback}")
            
            # Record action
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
                "timestamp": datetime.utcnow().isoformat(),
                "screenshot": screenshot
            }
            action_records.append(action_record)
            
            # Update memory
            memory.add_action({
                "action_type": planner_action.action_type,
                "reasoning": planner_action.reasoning
            })
            
            if planner_action.outcome_mismatch:
                memory.add_outcome_mismatch(planner_action.outcome_mismatch)
            
            # Compress memory if needed
            await memory.maybe_compress()
        
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