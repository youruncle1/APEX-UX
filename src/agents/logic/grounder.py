import math
import re
from typing import Optional

from src.agents.llm_client import get_grounding_client
from src.agents.models.data_models import BoundingBox
from src.config import get_settings
from src.log import get_logger

logger = get_logger(__name__)

_PROMPT_TEMPLATE = (
    "Outline the position corresponding to the instruction: {}. "
    "The output should be only [x1,y1,x2,y2]."
)


def _smart_resize(
    height: int, width: int, min_pixels: int, max_pixels: int, factor: int = 28
) -> tuple[int, int]:
    """
    Exact copy of qwen_vl_utils.vision_process.smart_resize.
    Taken from https://github.com/QwenLM/Qwen3-VL/blob/main/qwen-vl-utils/src/qwen_vl_utils/vision_process.py, as original repository does not exist anymore.
    Needed to compute the model's internal image dimensions for correct coordinate normalization.
    This is used as UI-Venus-ground-7B is based off Qwen2.5-VL.
    """
    h_bar = max(factor, round(height / factor) * factor)
    w_bar = max(factor, round(width / factor) * factor)
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = max(factor, math.floor(height / beta / factor) * factor)
        w_bar = max(factor, math.floor(width / beta / factor) * factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = max(factor, math.ceil(height * beta / factor) * factor)
        w_bar = max(factor, math.ceil(width * beta / factor) * factor)
    return h_bar, w_bar


class GroundingAgent:
    """
    Grounding agent logic implementation.

    Expects model input/output schema of UI-Venus-Ground-7B to correctly function
        - https://huggingface.co/inclusionAI/UI-Venus-Ground-7B
    """

    def __init__(self, viewport_width: int = 1280, viewport_height: int = 720):
        settings = get_settings()
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.min_pixels = settings.grounding_min_pixels
        self.max_pixels = settings.grounding_max_pixels
        self.client, self.model = get_grounding_client()

        # Pre-compute the model's internal image resolution (deterministic from viewport + pixel budget).
        # The model outputs [x1,y1,x2,y2] in this space, so we need it to normalize correctly.
        self._input_height, self._input_width = _smart_resize(
            viewport_height, viewport_width, self.min_pixels, self.max_pixels
        )
        logger.info(
            "Model: %s | internal dims: %dx%d",
            self.model,
            self._input_width,
            self._input_height,
        )

    async def ground(
        self,
        screenshot_base64: str,  # Screenshot in Base64 encoding
        target_description: str,  # Description of the (target) element to be grounded by the model
    ) -> Optional[BoundingBox]:
        """
        Ground (locate) the target element.

        Returns its bounding box coordinates [x1, y1, x2, y2] in converted viewport pixelspace,
            or 'None' if grounding fails or the model is unreachable.
        """
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/webp;base64,{screenshot_base64}",
                            "min_pixels": self.min_pixels,
                            "max_pixels": self.max_pixels,
                        },
                    },
                    {
                        "type": "text",
                        "text": _PROMPT_TEMPLATE.format(target_description),
                    },
                ],
            }
        ]

        def to_px(val: float, internal_dim: int, viewport_dim: int) -> int:
            return max(0, min(int(val / internal_dim * viewport_dim), viewport_dim))

        for attempt in range(1, 4):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_completion_tokens=64,
                    temperature=0.0,
                )
                text = (response.choices[0].message.content or "").strip()

                # Model outputs "[x1,y1,x2,y2]", parse first match
                match = re.search(
                    r"\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]",
                    text,
                )
                if not match:
                    logger.warning(
                        "Could not parse bounding box (attempt %d/3): %s",
                        attempt,
                        text[:120],
                    )
                    continue

                box = [float(match.group(i)) for i in range(1, 5)]
                x1 = to_px(box[0], self._input_width, self.viewport_width)
                y1 = to_px(box[1], self._input_height, self.viewport_height)
                x2 = to_px(box[2], self._input_width, self.viewport_width)
                y2 = to_px(box[3], self._input_height, self.viewport_height)

                logger.debug("Located at (%d, %d, %d, %d)", x1, y1, x2, y2)
                return BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)

            except Exception as e:
                logger.warning("Grounding error (attempt %d/3): %s", attempt, e)

        logger.error("Grounding failed after 3 attempts")
        return None
