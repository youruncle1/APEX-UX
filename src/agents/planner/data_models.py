from typing import Optional
from dataclasses import dataclass


@dataclass
class PlannerAction:
    action_type: str
    target_description: Optional[str] = None
    input_value: Optional[str] = None
    reasoning: str = ""
    outcome_mismatch: Optional[str] = None