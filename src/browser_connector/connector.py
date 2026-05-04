import base64
from typing import Optional
from playwright.async_api import async_playwright, Browser, Page, Playwright
from src.browser_connector.models.data_models import (
    Action,
    ActionType,
    ExecutionResult,
    VerificationResult,
)
from src.browser_connector.logic.verification import Verifier
from src.log import get_logger

logger = get_logger(__name__)


class BrowserConnector:
    def __init__(
        self,
        headless: bool = True,  # Headless browser settings (False requires XServer setup or similar!)
        viewport_width: int = 1280,  # Browser viewport width
        viewport_height: int = 720,  # Browser viewport height
        timeout: int = 30000,  # timeout (ms) safety net for playwright executions
        stabilization_wait: int = 2000,  # Stabilization wait time (ms) - used after some actions
        browser_type: str = "chromium",  # Browser environment
    ):
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.timeout = timeout
        self.headless = headless
        self.stabilization_wait = stabilization_wait
        self.browser_type = browser_type

    async def start(self, url: str) -> None:
        """
        Launches browser and navigate to initial URL
        """
        logger.debug(
            "Launching %s browser (headless=%s)", self.browser_type, self.headless
        )
        self.playwright = await async_playwright().start()
        browser_launcher = getattr(self.playwright, self.browser_type)
        self.browser = await browser_launcher.launch(headless=self.headless)
        self.page = await self.browser.new_page(
            viewport={"width": self.viewport_width, "height": self.viewport_height}
        )

        # safety timeout
        self.page.set_default_timeout(self.timeout)

        await self.page.goto(url, wait_until="load")
        logger.debug("Navigated to %s", url)

    async def screenshot(self) -> str:
        """
        Screenshots current viewport and encodes it into Base64 string
        """
        if not self.page:
            raise RuntimeError("Browser not started")

        screenshot_bytes = await self.page.screenshot(type="png")
        return base64.b64encode(screenshot_bytes).decode("utf-8")

    async def _wait_for_stabilization(self) -> None:
        """
        Waits for page to stabilize after action.
        """
        try:
            await self.page.wait_for_load_state(
                "networkidle", timeout=3000
            )  # networkidle stabilization
        except Exception:
            pass
        await self.page.wait_for_timeout(
            self.stabilization_wait
        )  # more wait time in case page shoots network activity late

    async def execute_action(self, action: Action) -> ExecutionResult:
        """
        Executes an action and returns feedback
        """
        if not self.page:
            return ExecutionResult(
                success=False,
                feedback="Browser not started",
                error="Browser not started",
            )

        try:
            # CLICK Action
            if action.action_type == ActionType.CLICK:
                await self.page.mouse.click(action.x, action.y)

                await self._wait_for_stabilization()

                return ExecutionResult(
                    success=True, feedback=f"Clicked at ({action.x}, {action.y})"
                )

            # DOUBLE_CLICK Action
            elif action.action_type == ActionType.DOUBLE_CLICK:
                await self.page.mouse.dblclick(action.x, action.y)

                await self._wait_for_stabilization()

                return ExecutionResult(
                    success=True, feedback=f"Double-clicked at ({action.x}, {action.y})"
                )

            # TYPE Action
            elif action.action_type == ActionType.TYPE:
                await self.page.mouse.click(action.x, action.y)
                await self.page.wait_for_timeout(100)

                await self.page.keyboard.press("Control+a")
                await self.page.keyboard.press(
                    "Backspace"
                )  # clear even when selection is ignored
                await self.page.keyboard.type(action.text, delay=30)

                await self._wait_for_stabilization()

                return ExecutionResult(
                    success=True,
                    feedback=f"Clicked at ({action.x}, {action.y}) and typed: {action.text[:50] + '...' if len(action.text) > 50 else action.text}",
                )

            # SCROLL_DOWN Action
            elif action.action_type == ActionType.SCROLL_DOWN:
                scroll_amount = self.viewport_height
                await self.page.mouse.wheel(0, scroll_amount)
                await self.page.wait_for_timeout(self.stabilization_wait)
                return ExecutionResult(
                    success=True, feedback=f"Scrolled down {scroll_amount}px"
                )

            # SCROLL_UP Action
            elif action.action_type == ActionType.SCROLL_UP:
                scroll_amount = self.viewport_height
                await self.page.mouse.wheel(0, -scroll_amount)
                await self.page.wait_for_timeout(self.stabilization_wait)
                return ExecutionResult(
                    success=True, feedback=f"Scrolled up {scroll_amount}px"
                )

            # HOVER Action
            elif action.action_type == ActionType.HOVER:
                await self.page.mouse.move(action.x, action.y)

                await self._wait_for_stabilization()

                return ExecutionResult(
                    success=True, feedback=f"Hovered at ({action.x}, {action.y})"
                )

            # DRAG_AND_DROP Action
            elif action.action_type == ActionType.DRAG_AND_DROP:
                await self.page.mouse.move(
                    action.x, action.y
                )  # Move cursor to initial position
                await self.page.mouse.down()  # Put mouse down (hold click)
                await self.page.mouse.move(
                    action.x2, action.y2
                )  # Move cursor to target position
                await self.page.mouse.up()  # Put mouse up (let go of click)
                await self.page.wait_for_timeout(self.stabilization_wait)
                return ExecutionResult(
                    success=True,
                    feedback=f"Dragged from ({action.x}, {action.y}) to ({action.x2}, {action.y2})",
                )

            # PRESS_KEY Action
            elif action.action_type == ActionType.PRESS_KEY:
                await self.page.keyboard.press(action.key)

                await self._wait_for_stabilization()

                return ExecutionResult(
                    success=True, feedback=f"Pressed key: {action.key}"
                )

            # BACK Action
            elif action.action_type == ActionType.BACK:
                await self.page.go_back(wait_until="load")
                return ExecutionResult(success=True, feedback="Navigated back")

            # FORWARD Action
            elif action.action_type == ActionType.FORWARD:
                await self.page.go_forward(wait_until="load")
                return ExecutionResult(success=True, feedback="Navigated forward")

            # WAIT Action
            elif action.action_type == ActionType.WAIT:
                duration_s = max(0, min(float(action.text or "1"), 30))  # max 30s
                await self.page.wait_for_timeout(duration_s * 1000)
                return ExecutionResult(
                    success=True, feedback=f"Waited {duration_s:.1f}s"
                )

            # DONE Action
            elif action.action_type == ActionType.DONE:
                return ExecutionResult(success=True, feedback="Task marked as done")

            # Unknown - error
            else:
                return ExecutionResult(
                    success=False,
                    feedback=f"Unknown action type: {action.action_type}",
                    error="Unknown action type",
                )

        except Exception as e:
            return ExecutionResult(
                success=False, feedback=f"Action failed: {str(e)}", error=str(e)
            )

    async def get_current_url(self) -> str:
        """
        Get current page URL (for verification and lostness tracking)
        """
        if not self.page:
            return ""
        return self.page.url

    async def get_visible_text(self) -> str:
        """
        Get all visible text on page (for verification)
        """
        if not self.page:
            return ""
        return await self.page.inner_text("body")

    async def verify(self, rules: Optional[dict]) -> VerificationResult:
        """
        Verify task completion based on rules
        """
        verifier = Verifier(rules)
        current_url = await self.get_current_url()
        page_text = await self.get_visible_text()
        return await verifier.verify(current_url, page_text)

    async def close(self) -> None:
        """
        Close browser and cleanup
        """
        if self.page:
            await self.page.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
