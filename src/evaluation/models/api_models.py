from datetime import datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel


class SEQResponse(BaseModel):
    score: float
    question: str
    scale: str
    reasoning: str


class SUSItemResponse(BaseModel):
    item_number: int
    question: str
    rating: int
    scale: str


class SUSResponse(BaseModel):
    score: float
    responses: List[SUSItemResponse]
    reasoning: str


class TaskDefinitionCreate(BaseModel):
    description: str
    base_url_override: Optional[str] = None
    max_steps: int = 25
    verification_rules: Optional[dict] = None


class EvaluationCreate(BaseModel):
    name: Optional[str] = None
    base_url: str
    clone_count: int = 3
    tasks: List[TaskDefinitionCreate]


class EvaluationSummary(BaseModel):
    id: UUID
    name: Optional[str]
    base_url: str
    status: str
    created_at: datetime
    finished_at: Optional[datetime]
    task_count: int
    clone_count: int
    duration_seconds: Optional[int]


class TaskRunSummary(BaseModel):
    id: UUID
    task_description: str
    clone_id: str
    status: str
    steps_taken: int
    seq_score: Optional[float] = None
    seq_reasoning: Optional[str] = None
    seq_response: Optional[SEQResponse] = None
    verification_result: Optional[str] = None
    agent_done: bool


class EvaluationReport(BaseModel):
    evaluation: EvaluationSummary
    clones: List[dict]
    task_runs: List[TaskRunSummary]
    metrics_summary: dict


class ActionRecordDetail(BaseModel):
    id: UUID
    step_number: int
    action_type: str
    target_description: Optional[str]
    input_value: Optional[str]
    reasoning: Optional[str]
    bounding_box: Optional[dict]
    click_coordinates: Optional[dict]
    execution_success: bool
    execution_feedback: Optional[str]
    timestamp: datetime
    screenshot_url: Optional[str]


class TaskRunDetail(BaseModel):
    id: UUID
    task_description: str
    clone_id: str
    status: str
    agent_done: bool
    verification_result: Optional[str]
    verification_rules: Optional[dict] = None
    verification_details: Optional[dict] = None
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    metrics: Optional[dict]
    actions: List[ActionRecordDetail]
    outcome_mismatches: Optional[List[dict]] = None