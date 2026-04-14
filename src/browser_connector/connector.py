import base64
from typing import Optional
from playwright.async_api import async_playwright, Browser, Page, Playwright
from src.browser_connector.models.data_models import Action, ActionType, ExecutionResult, VerificationResult
from src.browser_connector.logic.verification import Verifier
from src.log import get_logger

logger = get_logger(__name__)

class BrowserConnector:
    def __init__(
        self,
        headless: bool = True,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        timeout: int = 10000,
        stabilization_wait: int = 1000,
        browser_type: str = "chromium",
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
        """Launch browser and navigate to initial URL"""
        logger.debug("Launching %s browser (headless=%s)", self.browser_type, self.headless)
        self.playwright = await async_playwright().start()
        browser_launcher = getattr(self.playwright, self.browser_type)
        self.browser = await browser_launcher.launch(headless=self.headless)

        self.page = await self.browser.new_page(
            viewport={"width": self.viewport_width, "height": self.viewport_height}
        )

        self.page.set_default_timeout(self.timeout)

        await self.page.goto(url, wait_until="load")
        logger.debug("Navigated to %s", url)
    
    async def screenshot(self) -> str:
        """Screenshot current viewport as base64 string"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        screenshot_bytes = await self.page.screenshot(type="png")
        return base64.b64encode(screenshot_bytes).decode("utf-8")
    
    async def _wait_for_stabilization(self, action_type: ActionType) -> None:
        """
        Wait for page to stabilize after actions.
        """
        
        if action_type == ActionType.CLICK:
            await self.page.wait_for_load_state("load")
            await self.page.wait_for_timeout(self.stabilization_wait)

        elif action_type == ActionType.PRESS_KEY:
            await self.page.wait_for_load_state("load")
            await self.page.wait_for_timeout(self.stabilization_wait)

        elif action_type == ActionType.HOVER:
            await self.page.wait_for_timeout(self.stabilization_wait)

        elif action_type == ActionType.TYPE:
            await self.page.wait_for_timeout(self.stabilization_wait)
    
    async def execute_action(self, action: Action) -> ExecutionResult:
        """Execute an action and return feedback"""
        if not self.page:
            return ExecutionResult(
                success=False,
                feedback="Browser not started",
                error="Browser not started"
            )
        
        try:
            if action.action_type == ActionType.CLICK:
                await self.page.mouse.click(action.x, action.y)
                
                await self._wait_for_stabilization(ActionType.CLICK)
                
                return ExecutionResult(
                    success=True,
                    feedback=f"Clicked at ({action.x}, {action.y})"
                )
            
            elif action.action_type == ActionType.TYPE:
                await self.page.mouse.click(action.x, action.y)
                await self.page.wait_for_timeout(100)  # Focus delay

                await self.page.keyboard.press("Control+a")
                await self.page.keyboard.type(action.text)
                
                await self._wait_for_stabilization(ActionType.TYPE)
                
                return ExecutionResult(
                    success=True,
                    feedback=f"Clicked at ({action.x}, {action.y}) and typed: {action.text[:50] + '...' if len(action.text) > 50 else action.text}"
                )
            
            elif action.action_type == ActionType.SCROLL_DOWN:
                scroll_amount = self.viewport_height
                await self.page.mouse.wheel(0, scroll_amount)
                await self.page.wait_for_timeout(self.stabilization_wait)
                return ExecutionResult(
                    success=True,
                    feedback=f"Scrolled down {scroll_amount}px"
                )
            
            elif action.action_type == ActionType.SCROLL_UP:
                scroll_amount = self.viewport_height
                await self.page.mouse.wheel(0, -scroll_amount)
                await self.page.wait_for_timeout(self.stabilization_wait)
                return ExecutionResult(
                    success=True,
                    feedback=f"Scrolled up {scroll_amount}px"
                )
            
            elif action.action_type == ActionType.HOVER:
                await self.page.mouse.move(action.x, action.y)
                
                await self._wait_for_stabilization(ActionType.HOVER)
                
                return ExecutionResult(
                    success=True,
                    feedback=f"Hovered at ({action.x}, {action.y})"
                )
            
            elif action.action_type == ActionType.DRAG_AND_DROP:
                await self.page.mouse.move(action.x, action.y)
                await self.page.mouse.down()
                await self.page.mouse.move(action.x2, action.y2)
                await self.page.mouse.up()
                await self.page.wait_for_timeout(self.stabilization_wait)
                return ExecutionResult(
                    success=True,
                    feedback=f"Dragged from ({action.x}, {action.y}) to ({action.x2}, {action.y2})"
                )
            
            elif action.action_type == ActionType.PRESS_KEY:
                await self.page.keyboard.press(action.key)
                
                await self._wait_for_stabilization(ActionType.PRESS_KEY)
                
                return ExecutionResult(
                    success=True,
                    feedback=f"Pressed key: {action.key}"
                )
            
            elif action.action_type == ActionType.BACK:
                await self.page.go_back(wait_until="domcontentloaded")
                return ExecutionResult(
                    success=True,
                    feedback="Navigated back"
                )
            
            elif action.action_type == ActionType.FORWARD:
                await self.page.go_forward(wait_until="domcontentloaded")
                return ExecutionResult(
                    success=True,
                    feedback="Navigated forward"
                )
            
            elif action.action_type == ActionType.WAIT:
                try:
                    duration_s = min(float(action.text or "1"), 30)
                except (ValueError, TypeError):
                    duration_s = 1
                duration_s = max(duration_s, 0)
                await self.page.wait_for_timeout(duration_s * 1000)
                return ExecutionResult(
                    success=True,
                    feedback=f"Waited {duration_s:.1f}s"
                )

            elif action.action_type == ActionType.DONE:
                return ExecutionResult(
                    success=True,
                    feedback="Task marked as done"
                )
            
            else:
                return ExecutionResult(
                    success=False,
                    feedback=f"Unknown action type: {action.action_type}",
                    error="Unknown action type"
                )
        
        except Exception as e:
            return ExecutionResult(
                success=False,
                feedback=f"Action failed: {str(e)}",
                error=str(e)
            )
    
    async def get_current_url(self) -> str:
        """Get current page URL"""
        if not self.page:
            return ""
        return self.page.url
    
    async def get_visible_text(self) -> str:
        """Get all visible text on page (for verification)"""
        if not self.page:
            return ""
        return await self.page.inner_text("body")
    
    async def verify(self, rules: Optional[dict]) -> VerificationResult:
        """Verify task completion based on rules"""
        verifier = Verifier(rules)
        current_url = await self.get_current_url()
        page_text = await self.get_visible_text()
        return await verifier.verify(current_url, page_text)
    
    async def close(self) -> None:
        """Close browser"""
        if self.page:
            await self.page.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()