from typing import Optional, Callable
from datetime import datetime, timezone
from uuid import UUID
from urllib.parse import urlparse

from src.agents.logic.planner import PlannerAgent
from src.agents.logic.grounder import GroundingAgent
from src.browser_connector.connector import BrowserConnector
from src.browser_connector.models.data_models import Action, ActionType
from src.storage import create_screenshot_storage
from src.log import get_logger
from .memory import MemoryManager

logger = get_logger(__name__)


class TaskAbortedError(Exception):
    """Raised when a task is aborted mid-execution via cancellation."""

    pass


class TwoAgentController:
    """
    Orchestrates the planner-grounder-browser loop for task execution.
    """

    def __init__(
        self,
        persona_context: str,
        grounder: GroundingAgent,
        browser: BrowserConnector,
        compression_threshold: Optional[int] = None,
        planner_backend: Optional[str] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ):
        """
        Initialize controller with persona context.

        Args:
            persona_context: Persona description for planner
            grounder: Grounding agent instance
            browser: Browser connector instance
            compression_threshold: History compression threshold
            planner_backend: Override for planner backend ("openai" or "local")
            is_cancelled: Optional callback that returns True if the run should abort
        """
        # Initialize planner with backend selection
        self.planner = PlannerAgent(
            persona_context=persona_context, backend=planner_backend
        )
        self.grounder = grounder
        self.browser = browser
        self.compression_threshold = compression_threshold
        self.screenshot_storage = create_screenshot_storage()
        self._is_cancelled = is_cancelled or (lambda: False)

    def _check_cancelled(self):
        """Check cancellation flag and raise if cancelled."""
        if self._is_cancelled():
            raise TaskAbortedError("Task aborted by user")

    async def execute_task(
        self,
        task_description: str,
        start_url: str,
        evaluation_id: UUID,
        task_run_id: UUID,
        max_steps: int = 50,
    ) -> dict:
        """
        Execute a complete task run.

        Returns a dict with:
        - status: "VERIFIED" | "FAILED" | "TIMEOUT" | "ABORTED"
        - steps_taken: int
        - action_records: list[dict]
        - memory_state: dict
        - agent_done: bool
        - verification_result: dict (if rules provided)

        Raises:
            TaskAbortedError: If is_cancelled() returns True during execution.
        """

        # Initialize browser
        await self.browser.start(start_url)

        # Initialize memory
        memory = MemoryManager(compression_threshold=self.compression_threshold)

        # Action records for database
        action_records = []

        # URL tracking for Lostness metric
        _initial_url = self._normalize_url(await self.browser.get_current_url())
        _visited_urls: set[str] = {_initial_url}
        _visited_pages_total: int = 1
        _last_url: str = _initial_url

        # Main interaction loop
        for step in range(1, max_steps + 1):
            # --- CANCELLATION CHECK at top of every step ---
            self._check_cancelled()

            logger.debug("Step %d/%d", step, max_steps)

            # Capture screenshot (base64)
            screenshot_base64 = await self.browser.screenshot()

            # Save screenshot to storage and get URL
            screenshot_url = await self.screenshot_storage.save(
                evaluation_id=str(evaluation_id),
                task_run_id=str(task_run_id),
                step=step,
                screenshot_base64=screenshot_base64,
            )
            logger.debug("Screenshot saved: %s", screenshot_url)

            # compress memory if threshold reached
            await memory.maybe_compress(task_description=task_description)

            # Plan next action
            planner_action = None
            for retry in range(3):  # Max 3 retries for invalid output
                self._check_cancelled()  # Check before each LLM call

                planner_action = await self.planner.plan(
                    task_description=task_description,
                    screenshot_base64=screenshot_base64,
                    action_history=memory.action_history,
                    action_summary=memory.action_summary,
                    outcome_mismatches=memory.get_outcome_mismatches_for_planner(),
                )

                if planner_action:
                    break

                logger.warning("Planner output invalid, retry %d/3", retry + 1)

            if not planner_action:
                logger.error("Failed to get valid action from planner, aborting")
                return {
                    "status": "FAILED",
                    "steps_taken": step,
                    "action_records": action_records,
                    "memory_state": memory.get_state(),
                    "failure_reason": "Planner failed to produce valid action",
                    "agent_done": False,
                    "visited_pages_total": _visited_pages_total,
                    "visited_pages_unique": len(_visited_urls),
                }

            logger.info("Planner: %s", planner_action.action_type)
            logger.debug("Planner reasoning: %s", planner_action.reasoning)

            # Check if task is done
            if planner_action.action_type == "DONE":
                action_records.append(
                    {
                        "step_number": step,
                        "action_type": "DONE",
                        "target_description": None,
                        "input_value": None,
                        "reasoning": planner_action.reasoning,
                        "bounding_box": None,
                        "click_coordinates": None,
                        "execution_success": True,
                        "execution_feedback": "Task marked as complete by agent",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "screenshot": screenshot_url,
                    }
                )

                # Add to memory
                memory.add_action(
                    {
                        "action_type": "DONE",
                        "target_description": None,
                        "reasoning": planner_action.reasoning,
                    }
                )

                return {
                    "status": "COMPLETED",
                    "agent_done": True,
                    "steps_taken": step,
                    "action_records": action_records,
                    "memory_state": memory.get_state(),
                    "visited_pages_total": _visited_pages_total,
                    "visited_pages_unique": len(_visited_urls),
                }

            # Ground if needed (CLICK, HOVER, DRAG_AND_DROP, TYPE)
            coordinates = None
            coordinates2 = None
            bounding_box = None

            if self._needs_grounding(planner_action.action_type):
                if not planner_action.target_description:
                    logger.warning(
                        "%s needs target_description but none provided",
                        planner_action.action_type,
                    )

                self._check_cancelled()  # Check before grounding call

                logger.debug("Grounding: %s", planner_action.target_description)
                bbox = await self.grounder.ground(
                    screenshot_base64=screenshot_base64,
                    target_description=planner_action.target_description or "",
                )

                # For DRAG_AND_DROP, also ground the destination (stored in input_value)
                if bbox and planner_action.action_type == "DRAG_AND_DROP":
                    self._check_cancelled()
                    bbox2 = await self.grounder.ground(
                        screenshot_base64=screenshot_base64,
                        target_description=planner_action.input_value or "",
                    )
                    if bbox2:
                        coordinates2 = bbox2.center()
                        logger.debug("Grounder destination: %s", coordinates2)
                    else:
                        bbox = None  # treat whole action as failed
                        logger.warning("Grounding failed for drag destination")

                if bbox:
                    coordinates = bbox.center()
                    bounding_box = {
                        "x1": bbox.x1,
                        "y1": bbox.y1,
                        "x2": bbox.x2,
                        "y2": bbox.y2,
                    }
                    logger.debug("Grounder: %s", coordinates)
                else:
                    logger.warning("Grounding failed, skipping action")

                    memory.add_action(
                        {
                            "action_type": planner_action.action_type,
                            "target_description": planner_action.target_description,
                            "reasoning": planner_action.reasoning,
                        }
                    )

                    # Record failed grounding
                    action_records.append(
                        {
                            "step_number": step,
                            "action_type": planner_action.action_type,
                            "target_description": planner_action.target_description,
                            "input_value": planner_action.input_value,
                            "reasoning": planner_action.reasoning,
                            "bounding_box": None,
                            "click_coordinates": None,
                            "execution_success": False,
                            "execution_feedback": "Grounding failed",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "screenshot": screenshot_url,
                        }
                    )

                    # Add outcome mismatch
                    memory.add_outcome_mismatch(
                        f"Failed to locate element: {planner_action.target_description}"
                    )

                    continue

            # Build browser action
            browser_action = self._build_browser_action(
                planner_action, coordinates, coordinates2
            )

            # Execute action
            result = await self.browser.execute_action(browser_action)
            logger.debug("Execution: %s", result.feedback)

            # Track URL change for Lostness metric
            try:
                _current_url = self._normalize_url(await self.browser.get_current_url())
                if _current_url != _last_url:
                    _visited_pages_total += 1
                    _visited_urls.add(_current_url)
                    _last_url = _current_url
            except Exception:
                pass  # Non-fatal — URL tracking failure should never break task execution

            memory.add_action(
                {
                    "action_type": planner_action.action_type,
                    "target_description": planner_action.target_description,
                    "reasoning": planner_action.reasoning,
                }
            )

            # Record action
            action_record = {
                "step_number": step,
                "action_type": planner_action.action_type,
                "target_description": planner_action.target_description,
                "input_value": planner_action.input_value,
                "reasoning": planner_action.reasoning,
                "bounding_box": bounding_box,
                "click_coordinates": (
                    {
                        "x": coordinates[0],
                        "y": coordinates[1],
                        "x2": coordinates2[0],
                        "y2": coordinates2[1],
                    }
                    if planner_action.action_type == "DRAG_AND_DROP" and coordinates2
                    else {"x": coordinates[0], "y": coordinates[1]}
                )
                if coordinates
                else None,
                "execution_success": result.success,
                "execution_feedback": result.feedback,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "screenshot": screenshot_url,
            }
            action_records.append(action_record)

            # Add outcome mismatch
            if planner_action.outcome_mismatch:
                memory.add_outcome_mismatch(planner_action.outcome_mismatch)

        # Max steps reached
        logger.info("Max steps reached (%d), task timed out", max_steps)
        return {
            "status": "TIMEOUT",
            "steps_taken": max_steps,
            "action_records": action_records,
            "memory_state": memory.get_state(),
            "failure_reason": "Maximum steps reached",
            "agent_done": False,
            "visited_pages_total": _visited_pages_total,
            "visited_pages_unique": len(_visited_urls),
        }

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Strip query params and fragment — same page regardless of sort/filter state."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    def _needs_grounding(self, action_type: str) -> bool:
        """Check if action needs grounding - now includes TYPE"""
        return action_type in ["CLICK", "HOVER", "DRAG_AND_DROP", "TYPE"]

    def _build_browser_action(
        self,
        planner_action,
        coordinates: Optional[tuple[int, int]],
        coordinates2: Optional[tuple[int, int]] = None,
    ) -> Action:
        """Convert planner action to browser action"""

        return Action(
            action_type=ActionType(planner_action.action_type),
            x=coordinates[0] if coordinates else None,
            y=coordinates[1] if coordinates else None,
            x2=coordinates2[0] if coordinates2 else None,
            y2=coordinates2[1] if coordinates2 else None,
            text=planner_action.input_value,
            key=planner_action.input_value,
        )
