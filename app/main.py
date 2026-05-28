from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_session, init_db
from app.llm import llm_service
from app.models import Conversation, ConversationMessage, MessageRole, Task, TaskSource, TaskStatus
from app.schemas import (
    ConversationCreate,
    ConversationMessageCreate,
    ConversationRead,
    ConversationTurnResponse,
    HealthResponse,
    TaskCreate,
    TaskDetail,
    TaskSummary,
)
from app.utils import fingerprint_text
from app.worker import worker_manager


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await worker_manager.start()
    try:
        yield
    finally:
        await worker_manager.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", app=settings.app_name)


@app.post("/tasks", response_model=TaskSummary, status_code=201)
async def create_task(payload: TaskCreate, session: AsyncSession = Depends(get_session)) -> Task:
    task = Task(
        title=payload.title,
        description=payload.description,
        source_type=TaskSource.manual,
        status=TaskStatus.queued,
        dedupe_key=fingerprint_text(f"{payload.title} {payload.description}"),
        max_retries=payload.max_retries,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@app.get("/tasks", response_model=list[TaskSummary])
async def list_tasks(session: AsyncSession = Depends(get_session)) -> list[Task]:
    statement = select(Task).order_by(desc(Task.created_at))
    return list((await session.execute(statement)).scalars().all())


@app.get("/tasks/{task_id}", response_model=TaskDetail)
async def get_task(task_id: str, session: AsyncSession = Depends(get_session)) -> Task:
    statement = (
        select(Task)
        .where(Task.id == task_id)
        .options(selectinload(Task.steps), selectinload(Task.events), selectinload(Task.memories))
    )
    task = (await session.execute(statement)).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/tasks/{task_id}/retry", response_model=TaskSummary)
async def retry_task(task_id: str, session: AsyncSession = Depends(get_session)) -> Task:
    task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = TaskStatus.queued
    task.failure_reason = None
    task.recovery_notes = "Task was manually re-queued."
    task.next_retry_at = None
    task.finished_at = None
    await session.commit()
    await session.refresh(task)
    return task


@app.post("/conversations", response_model=ConversationRead, status_code=201)
async def create_conversation(payload: ConversationCreate, session: AsyncSession = Depends(get_session)) -> Conversation:
    conversation = Conversation(title=payload.title or "Agent Conversation")
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    statement = select(Conversation).where(Conversation.id == conversation.id).options(selectinload(Conversation.messages))
    return (await session.execute(statement)).scalar_one()


@app.get("/conversations/{conversation_id}", response_model=ConversationRead)
async def get_conversation(conversation_id: str, session: AsyncSession = Depends(get_session)) -> Conversation:
    statement = select(Conversation).where(Conversation.id == conversation_id).options(selectinload(Conversation.messages))
    conversation = (await session.execute(statement)).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post("/conversations/{conversation_id}/messages", response_model=ConversationTurnResponse, status_code=201)
async def send_conversation_message(
    conversation_id: str,
    payload: ConversationMessageCreate,
    session: AsyncSession = Depends(get_session),
) -> ConversationTurnResponse:
    content = payload.content.strip()
    statement = select(Conversation).where(Conversation.id == conversation_id).options(selectinload(Conversation.messages))
    conversation = (await session.execute(statement)).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    user_message = ConversationMessage(conversation_id=conversation_id, role=MessageRole.user, content=content)
    session.add(user_message)
    parsed = await llm_service.parse_conversation_to_task(content)
    created_task = None
    if parsed["actionable"]:
        created_task = Task(
            title=parsed["title"] or "Conversation Task",
            description=parsed["description"] or content,
            source_type=TaskSource.conversation,
            status=TaskStatus.queued,
            dedupe_key=fingerprint_text(f"{parsed['title']} {parsed['description']}"),
            max_retries=2,
            conversation_id=conversation_id,
        )
        session.add(created_task)
    assistant_message = ConversationMessage(
        conversation_id=conversation_id,
        role=MessageRole.assistant,
        content=parsed["assistant_reply"],
    )
    session.add(assistant_message)
    await session.commit()
    refreshed = (
        await session.execute(
            select(Conversation).where(Conversation.id == conversation_id).options(selectinload(Conversation.messages))
        )
    ).scalar_one()
    if created_task is not None:
        await session.refresh(created_task)
    await session.refresh(assistant_message)
    return ConversationTurnResponse(conversation=refreshed, assistant_message=assistant_message, created_task=created_task)
