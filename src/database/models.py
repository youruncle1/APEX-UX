from datetime import datetime, timezone
from typing import Optional, List
from uuid import UUID, uuid4
from sqlalchemy import Column, String, Integer, Boolean, Float, DateTime, ForeignKey, JSON, Text, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship, DeclarativeBase
import enum

class Base(DeclarativeBase):
    pass

class EvaluationStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"

class TaskRunStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    VERIFIED = "VERIFIED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    ABORTED = "ABORTED"

class VerificationResult(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"

class Evaluation(Base):
    __tablename__ = "evaluations"
    
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String, nullable=True)
    base_url = Column(String, nullable=False)
    browser = Column(String, default="chromium")
    viewport = Column(JSON, default=lambda: {"width": 1280, "height": 720})
    status = Column(SQLEnum(EvaluationStatus), default=EvaluationStatus.PENDING)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    
    total_pages = Column(Integer, nullable=True)

    # Relationships
    clones = relationship("EvaluationClone", back_populates="evaluation", cascade="all, delete-orphan")
    tasks = relationship("TaskDefinition", back_populates="evaluation", cascade="all, delete-orphan", order_by="TaskDefinition.position")

class Persona(Base):
    __tablename__ = "personas"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    narrative = Column(Text, nullable=False)


class EvaluationClone(Base):
    __tablename__ = "evaluation_clones"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    evaluation_id = Column(PGUUID(as_uuid=True), ForeignKey("evaluations.id"), nullable=False)
    persona_id = Column(PGUUID(as_uuid=True), ForeignKey("personas.id"), nullable=False)
    sus_score = Column(Float, nullable=True)
    sus_ratings = Column(JSON, nullable=True)
    sus_reasoning = Column(Text, nullable=True)

    # Relationships
    evaluation = relationship("Evaluation", back_populates="clones")
    persona = relationship("Persona")
    task_runs = relationship("TaskRun", back_populates="clone", cascade="all, delete-orphan")

class TaskDefinition(Base):
    __tablename__ = "task_definitions"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    evaluation_id = Column(PGUUID(as_uuid=True), ForeignKey("evaluations.id"), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    description = Column(Text, nullable=False)
    base_url_override = Column(String, nullable=True)
    max_steps = Column(Integer, default=25)
    verification_rules = Column(JSON, nullable=True)

    # Relationships
    evaluation = relationship("Evaluation", back_populates="tasks")
    runs = relationship("TaskRun", back_populates="task_definition", cascade="all, delete-orphan")

class TaskRun(Base):
    __tablename__ = "task_runs"
    
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    task_definition_id = Column(PGUUID(as_uuid=True), ForeignKey("task_definitions.id"), nullable=False)
    evaluation_clone_id = Column(PGUUID(as_uuid=True), ForeignKey("evaluation_clones.id"), nullable=False)
    
    status = Column(SQLEnum(TaskRunStatus), default=TaskRunStatus.PENDING)
    agent_done = Column(Boolean, default=False)
    verification_result = Column(SQLEnum(VerificationResult), nullable=True)
    verification_details = Column(JSON, nullable=True) 
    
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    failure_reason = Column(Text, nullable=True) 
    
    # Memory snapshots (JSON)
    final_action_history = Column(JSON, nullable=True)
    final_action_summary = Column(Text, nullable=True)
    final_outcome_mismatches = Column(JSON, nullable=True)
    
    # Relationships
    task_definition = relationship("TaskDefinition", back_populates="runs")
    clone = relationship("EvaluationClone", back_populates="task_runs")
    actions = relationship("ActionRecord", back_populates="task_run", cascade="all, delete-orphan")
    metrics = relationship("TaskRunMetrics", back_populates="task_run", uselist=False, cascade="all, delete-orphan")

class TaskRunMetrics(Base):
    __tablename__ = "task_run_metrics"
    
    task_run_id = Column(PGUUID(as_uuid=True), ForeignKey("task_runs.id"), primary_key=True)
    
    steps_taken = Column(Integer, default=0)
    failed_action_count = Column(Integer, default=0)
    outcome_mismatch_count = Column(Integer, default=0)
    backtracking_count = Column(Integer, default=0)
    seq_score = Column(Float, nullable=True)
    seq_reasoning = Column(Text, nullable=True)
    time_on_task_seconds = Column(Integer, nullable=True)
    visited_pages_total = Column(Integer, nullable=True)
    visited_pages_unique = Column(Integer, nullable=True)
    lostness = Column(Float, nullable=True)

    # Relationships
    task_run = relationship("TaskRun", back_populates="metrics")

class ActionRecord(Base):
    __tablename__ = "action_records"
    
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    task_run_id = Column(PGUUID(as_uuid=True), ForeignKey("task_runs.id"), nullable=False)
    
    step_number = Column(Integer, nullable=False)
    action_type = Column(String, nullable=False)
    target_description = Column(Text, nullable=True)
    input_value = Column(Text, nullable=True)
    reasoning = Column(Text, nullable=True)
    
    bounding_box = Column(JSON, nullable=True)  # {x1, y1, x2, y2}
    click_coordinates = Column(JSON, nullable=True)  # {x, y}
    
    execution_success = Column(Boolean, default=False)
    execution_feedback = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    screenshot = Column(String(500), nullable=True)  # URL/path to screenshot
    
    # Relationships
    task_run = relationship("TaskRun", back_populates="actions")