import os
from typing import Optional

import aiohttp

from src.agents.models.data_models import BoundingBox


class GroundingAgent:
    """
    Grounding agent that calls the remote UI-Venus-Ground service.
    Converts normalized coordinates [0, 1] to viewport pixels.
    """

    def __init__(self, viewport_width: int = 1280, viewport_height: int = 720):
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.service_url = os.getenv("GROUNDING_SERVICE_URL", "http://localhost:8000")
        self.timeout = aiohttp.ClientTimeout(total=60)

    async def ground(
        self,
        screenshot_base64: str,
        target_description: str,
    ) -> Optional[BoundingBox]:
        """
        Locate a target element and return its bounding box in viewport pixels.

        Returns None if grounding fails or the service is unreachable.
        """
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                payload = {
                    "image_base64": screenshot_base64,
                    "instruction": target_description,
                }

                async with session.post(f"{self.service_url}/ground", json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"[Grounder] Service error {response.status}: {error_text}")
                        return None

                    result = await response.json()

                    if not result.get("success", False):
                        print(f"[Grounder] Grounding failed: {result.get('error')}")
                        return None

                    # bbox is [x1, y1, x2, y2] normalized to [0, 1]
                    bbox_norm = result["bbox"]

                    def to_px(val: float, dim: int) -> int:
                        return max(0, min(int(val * dim), dim))

                    x1 = to_px(bbox_norm[0], self.viewport_width)
                    y1 = to_px(bbox_norm[1], self.viewport_height)
                    x2 = to_px(bbox_norm[2], self.viewport_width)
                    y2 = to_px(bbox_norm[3], self.viewport_height)

                    print(f"[Grounder] Located at ({x1}, {y1}, {x2}, {y2})")
                    return BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)

        except aiohttp.ClientError as e:
            print(f"[Grounder] Network error: {e}")
            return None
        except Exception as e:
            print(f"[Grounder] Unexpected error: {e}")
            return None