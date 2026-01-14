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

@dataclass
class Action:
    action_type: ActionType     # Action type
    x: Optional[int] = None     # x coordinate
    y: Optional[int] = None     # y coordinate
    text: Optional[str] = None  # Text to type
    key: Optional[str] = None   # Key to press 
    x2: Optional[int] = None    # Drag target x2
    y2: Optional[int] = None    # Drag target y2

@dataclass
class ExecutionResult:
    success: bool
    feedback: str
    error: Optional[str] = None