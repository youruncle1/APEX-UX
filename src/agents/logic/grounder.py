from typing import Optional

import aiohttp

from src.agents.models.data_models import BoundingBox
from src.config import get_settings
from src.log import get_logger

logger = get_logger(__name__)


class GroundingAgent:
    """
    Grounding agent logic implementation.

    Expects model input/output schema of UI-Venus-Ground-7B to correctly function
        - https://huggingface.co/inclusionAI/UI-Venus-Ground-7B
    """

    def __init__(self, viewport_width: int = 1280, viewport_height: int = 720):
        self.viewport_width = viewport_width  # viewport width size in pixels
        self.viewport_height = viewport_height  # viewport height size in pixels
        self.service_url = get_settings().grounding_service_url  # Grounding service URL
        self.timeout = aiohttp.ClientTimeout(
            total=60
        )  # Connection default timeout time

    async def ground(
        self,
        screenshot_base64: str,  # Screenshot in Base64 encoding
        target_description: str,  # Description of the (target) element to be grounded by the model
    ) -> Optional[BoundingBox]:
        """
        Ground (locate) the target element.

        Returns its bounding box coordinates [x1, y1, x2, y2] in converted viewport pixelspace,
            or 'None' if grounding fails or the service is unreachable.
        """
        try:
            # Init HTTP session manager to handle connection
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                payload = {
                    "image_base64": screenshot_base64,
                    "instruction": target_description,
                }

                # Send POST request to the grounding service, wait for Header
                async with session.post(
                    f"{self.service_url}/ground", json=payload
                ) as response:
                    # Check Header 200 OK return status
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(
                            "Service error %d: %s", response.status, error_text
                        )
                        return None

                    # Parse JSON response asynchronously
                    result = await response.json()

                    # Verify if model was able to find element
                    if not result.get("success", False):
                        logger.warning("Grounding failed: %s", result.get("error"))
                        return None

                    # Get bounding box coordinates [x1, y1, x2, y2]
                    # ! each value was normalized by model to [0.0 to 1.0] floats
                    bbox_norm = result["bbox"]

                    # helper function: Convert normalized floats to screen pixels
                    def to_px(val: float, dim: int) -> int:
                        return max(0, min(int(val * dim), dim))

                    # Map normalized coordinates back to viewport size
                    x1 = to_px(bbox_norm[0], self.viewport_width)
                    y1 = to_px(bbox_norm[1], self.viewport_height)
                    x2 = to_px(bbox_norm[2], self.viewport_width)
                    y2 = to_px(bbox_norm[3], self.viewport_height)

                    logger.debug("Located at (%d, %d, %d, %d)", x1, y1, x2, y2)
                    return BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)

        except aiohttp.ClientError as e:
            logger.error("Network error: %s", e)
            return None
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            return None
