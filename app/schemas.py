from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import MemoryScope, MemoryType, MessageRole, StepStatus, TaskSource, TaskStatus


class TaskCreate(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    description: str = Field(min_length=5)
    max_retries: int = Field(default=2, ge=0, le=5)


class TaskSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str
    source_type: TaskSource
    status: TaskStatus
    max_retries: int
    retry_count: int
    latest_summary: str | None
    failure_reason: str | None
    recovery_notes: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    next_retry_at: datetime | None


class TaskStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    stage: str
    status: StepStatus
    attempt: int
    input_payload: dict[str, Any]
    output_payload: dict[str, Any]
    reasoning_trace: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ExecutionEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class MemoryEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: MemoryScope
    memory_type: MemoryType
    content: str
    summary: str | None
    importance: int
    created_at: datetime


class TaskDetail(TaskSummary):
    steps: list[TaskStepRead]
    events: list[ExecutionEventRead]
    memories: list[MemoryEntryRead]


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class ConversationMessageCreate(BaseModel):
    content: str = Field(min_length=3)


class ConversationMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: MessageRole
    content: str
    created_at: datetime


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[ConversationMessageRead]


class ConversationTurnResponse(BaseModel):
    conversation: ConversationRead
    assistant_message: ConversationMessageRead
    created_task: TaskSummary | None


class HealthResponse(BaseModel):
    status: str
    app: str
