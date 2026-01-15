import os
import aiohttp
from typing import Optional
from dataclasses import dataclass

@dataclass
class BoundingBox:
    x1: int
    y1: int
    x2: int
    y2: int
    
    def center(self) -> tuple[int, int]:
        """Get center point of bounding box"""
        cx = (self.x1 + self.x2) // 2
        cy = (self.y1 + self.y2) // 2
        return cx, cy

class GroundingAgent:
    """
    Grounding agent that calls remote UI-Venus-Ground service.
    Converts normalized coordinates [0, 1] to viewport pixels.
    """
    
    def __init__(self, viewport_width: int = 1280, viewport_height: int = 720):
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.service_url = os.getenv("GROUNDING_SERVICE_URL", "http://localhost:8000")
        self.timeout = aiohttp.ClientTimeout(total=60)  # 60s for model inference
    
    async def ground(
        self,
        screenshot_base64: str,
        target_description: str
    ) -> Optional[BoundingBox]:
        """
        Locate target element and return bounding box in viewport pixels.
        
        Args:
            screenshot_base64: Base64-encoded PNG screenshot
            target_description: Natural language description of target element
            
        Returns:
            BoundingBox with pixel coordinates, or None if grounding fails
        """
        
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                payload = {
                    "image_base64": screenshot_base64,
                    "instruction": target_description
                }
                
                async with session.post(
                    f"{self.service_url}/ground",
                    json=payload
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"[Grounder] Service error {response.status}: {error_text}")
                        return None
                    
                    result = await response.json()
                    
                    if not result.get("success", False):
                        print(f"[Grounder] Grounding failed: {result.get('error')}")
                        return None
                    
                    # Extract normalized coordinates [0, 1]
                    bbox_norm = result["bbox"]  # [x1, y1, x2, y2] normalized
                    
                    # Convert to viewport pixels - from UI-Venus-Ground format
                    x1 = int(bbox_norm[0] * self.viewport_width)
                    y1 = int(bbox_norm[1] * self.viewport_height)
                    x2 = int(bbox_norm[2] * self.viewport_width)
                    y2 = int(bbox_norm[3] * self.viewport_height)
                    
                    x1 = max(0, min(x1, self.viewport_width))
                    y1 = max(0, min(y1, self.viewport_height))
                    x2 = max(0, min(x2, self.viewport_width))
                    y2 = max(0, min(y2, self.viewport_height))
                    
                    print(f"[Grounder] Located at ({x1}, {y1}, {x2}, {y2})")
                    
                    return BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)
        
        except aiohttp.ClientError as e:
            print(f"[Grounder] Network error: {e}")
            return None
        except Exception as e:
            print(f"[Grounder] Unexpected error: {e}")
            return None
