from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.agent import agent_service
from app.config import get_settings
from app.database import SessionLocal
from app.models import Task, TaskStatus
from app.utils import utcnow


settings = get_settings()


class WorkerManager:
    def __init__(self) -> None:
        self._loop_task: asyncio.Task | None = None
        self._active: set[str] = set()
        self._stopping = False

    async def start(self) -> None:
        if self._loop_task is None:
            self._stopping = False
            self._loop_task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

    async def _run(self) -> None:
        while not self._stopping:
            try:
                while len(self._active) < settings.worker_max_concurrency:
                    task_id = await self._claim_next_task()
                    if task_id is None:
                        break
                    self._active.add(task_id)
                    asyncio.create_task(self._execute(task_id))
                await asyncio.sleep(settings.worker_poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(settings.worker_poll_interval_seconds)

    async def _claim_next_task(self) -> str | None:
        async with SessionLocal() as session:
            statement = (
                select(Task)
                .where(
                    Task.status.in_([TaskStatus.queued, TaskStatus.retrying]),
                    ((Task.next_retry_at.is_(None)) | (Task.next_retry_at <= utcnow())),
                )
                .order_by(Task.created_at.asc())
                .limit(1)
            )
            candidate = (await session.execute(statement)).scalar_one_or_none()
            if candidate is None:
                return None
            candidate.status = TaskStatus.running
            if candidate.started_at is None:
                candidate.started_at = utcnow()
            candidate.next_retry_at = None
            await session.commit()
            return candidate.id

    async def _execute(self, task_id: str) -> None:
        try:
            async with SessionLocal() as session:
                await agent_service.process_task(session, task_id)
        finally:
            self._active.discard(task_id)


worker_manager = WorkerManager()
