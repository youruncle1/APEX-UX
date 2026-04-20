from typing import Optional
from dataclasses import dataclass


@dataclass
class BoundingBox:
    x1: int
    y1: int
    x2: int
    y2: int

    def center(self) -> tuple[int, int]:
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2


@dataclass
class PlannerAction:
    action_type: str
    target_description: Optional[str] = None
    input_value: Optional[str] = None
    reasoning: str = ""
    outcome_mismatch: Optional[str] = None
