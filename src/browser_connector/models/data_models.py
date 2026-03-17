from dataclasses import dataclass
from typing import Optional
from enum import Enum

class ActionType(str, Enum):
    CLICK = "CLICK"
    TYPE = "TYPE"
    SCROLL_UP = "SCROLL_UP"
    SCROLL_DOWN = "SCROLL_DOWN"
    HOVER = "HOVER"
    DRAG_AND_DROP = "DRAG_AND_DROP"
    PRESS_KEY = "PRESS_KEY"
    BACK = "BACK"
    FORWARD = "FORWARD"
    DONE = "DONE"
    
class VerificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"

@dataclass
class VerificationResult:
    status: VerificationStatus
    details: Optional[dict] = None

@dataclass
class Action:
    action_type: ActionType
    x: Optional[int] = None
    y: Optional[int] = None
    text: Optional[str] = None
    key: Optional[str] = None
    x2: Optional[int] = None
    y2: Optional[int] = None

@dataclass
class ExecutionResult:
    success: bool
    feedback: str
    error: Optional[str] = None