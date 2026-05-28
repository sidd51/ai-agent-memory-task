from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.llm import llm_service
from app.memory import memory_service
from app.models import ExecutionEvent, MemoryScope, MemoryType, StepStatus, Task, TaskStatus, TaskStep
from app.utils import safe_json_dumps, utcnow


settings = get_settings()


class VerificationError(Exception):
    pass


class AgentService:
    async def process_task(self, session: AsyncSession, task_id: str) -> Task:
        task = await self._load_task(session, task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        duplicate = await memory_service.find_duplicate_task(session, task.dedupe_key, task.id)
        if duplicate is not None:
            summary = await llm_service.explain_duplicate(duplicate.latest_summary or duplicate.description)
            task.status = TaskStatus.deduplicated
            task.latest_summary = summary
            task.finished_at = utcnow()
            task.failure_reason = None
            task.recovery_notes = "Execution skipped because a matching task was already completed."
            await self._add_event(session, task, "task.deduplicated", {"duplicate_task_id": duplicate.id, "summary": summary})
            await memory_service.store_memory(
                session,
                content=summary,
                memory_type=MemoryType.summary,
                scope=MemoryScope.task,
                task_id=task.id,
                conversation_id=task.conversation_id,
                importance=2,
            )
            await session.commit()
            return task
        try:
            context = await self._memory_stage(session, task)
            plan = await self._planning_stage(session, task, context)
            execution = await self._execution_stage(session, task, context, plan)
            verification = await self._verification_stage(session, task, plan, execution)
            task.status = TaskStatus.completed
            task.latest_summary = verification["summary"]
            task.failure_reason = None
            task.recovery_notes = "Completed successfully with verified output."
            task.finished_at = utcnow()
            task.last_context = {
                "memory_summary": context["summary"],
                "plan": plan,
                "execution": execution,
                "verification": verification,
            }
            await memory_service.store_memory(
                session,
                content=task.description,
                memory_type=MemoryType.request,
                scope=MemoryScope.task,
                task_id=task.id,
                conversation_id=task.conversation_id,
                importance=2,
            )
            await memory_service.store_memory(
                session,
                content=safe_json_dumps(plan),
                memory_type=MemoryType.plan,
                scope=MemoryScope.task,
                task_id=task.id,
                conversation_id=task.conversation_id,
                summary=plan["reasoning_trace"],
                importance=2,
            )
            await memory_service.store_memory(
                session,
                content=execution["result"],
                memory_type=MemoryType.result,
                scope=MemoryScope.task,
                task_id=task.id,
                conversation_id=task.conversation_id,
                summary=verification["summary"],
                importance=3,
            )
            await memory_service.store_memory(
                session,
                content=verification["summary"],
                memory_type=MemoryType.summary,
                scope=MemoryScope.global_scope,
                task_id=task.id,
                conversation_id=task.conversation_id,
                importance=3,
            )
            await self._add_event(session, task, "task.completed", {"summary": verification["summary"]})
            await session.commit()
            return task
        except Exception as exc:
            await self._handle_failure(session, task, exc)
            await session.commit()
            return task

    async def _load_task(self, session: AsyncSession, task_id: str) -> Task | None:
        statement = (
            select(Task)
            .where(Task.id == task_id)
            .options(selectinload(Task.steps), selectinload(Task.events), selectinload(Task.memories))
        )
        return (await session.execute(statement)).scalar_one_or_none()

    async def _memory_stage(self, session: AsyncSession, task: Task) -> dict[str, Any]:
        step = await self._start_step(session, task, "memory_loader", {"task": task.description})
        context = await memory_service.get_relevant_context(session, task)
        step.status = StepStatus.completed
        step.output_payload = {"memory_count": len(context["items"]), "summary": context["summary"]}
        step.reasoning_trace = "Loaded the most relevant prior memory and execution history before planning."
        await self._add_event(session, task, "stage.memory_loader.completed", step.output_payload)
        await session.flush()
        return context

    async def _planning_stage(self, session: AsyncSession, task: Task, context: dict[str, Any]) -> dict[str, Any]:
        step = await self._start_step(session, task, "planner", {"memory_summary": context["summary"]})
        plan = await llm_service.create_plan(task.title, task.description, str(context["summary"]), task.retry_count)
        step.status = StepStatus.completed
        step.output_payload = plan
        step.reasoning_trace = plan["reasoning_trace"]
        await self._add_event(session, task, "stage.planner.completed", {"goal": plan["goal"], "steps": plan["steps"]})
        await session.flush()
        return plan

    async def _execution_stage(self, session: AsyncSession, task: Task, context: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        step = await self._start_step(
            session,
            task,
            "executor",
            {"memory_summary": context["summary"], "plan_steps": plan["steps"]},
        )
        prior_failure = task.failure_reason
        execution = await llm_service.execute_plan(task.title, task.description, plan, str(context["summary"]), prior_failure)
        step.status = StepStatus.completed
        step.output_payload = execution
        step.reasoning_trace = execution["reasoning_trace"]
        await self._add_event(session, task, "stage.executor.completed", {"actions_taken": execution["actions_taken"]})
        await session.flush()
        return execution

    async def _verification_stage(self, session: AsyncSession, task: Task, plan: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        step = await self._start_step(session, task, "verifier", {"plan": plan, "execution": execution})
        verification = await llm_service.verify_execution(task.title, plan, execution)
        if not verification["passed"]:
            step.status = StepStatus.failed
            step.output_payload = verification
            step.reasoning_trace = verification["reasoning_trace"]
            step.error_message = str(verification.get("failure_reason") or "Verification failed")
            await self._add_event(session, task, "stage.verifier.failed", verification)
            await session.flush()
            raise VerificationError(step.error_message)
        step.status = StepStatus.completed
        step.output_payload = verification
        step.reasoning_trace = verification["reasoning_trace"]
        await self._add_event(session, task, "stage.verifier.completed", {"summary": verification["summary"]})
        await session.flush()
        return verification

    async def _handle_failure(self, session: AsyncSession, task: Task, exc: Exception) -> None:
        task.failure_reason = str(exc)
        task.recovery_notes = "Execution failed. Context was preserved for retry and future diagnosis."
        task.last_context = {
            **(task.last_context or {}),
            "failed_at": utcnow().isoformat(),
            "failure_reason": str(exc),
            "retry_count": task.retry_count,
        }
        await memory_service.store_memory(
            session,
            content=str(exc),
            memory_type=MemoryType.failure,
            scope=MemoryScope.task,
            task_id=task.id,
            conversation_id=task.conversation_id,
            summary="Failure captured for retry-aware recovery.",
            importance=3,
        )
        if task.retry_count < task.max_retries:
            task.retry_count += 1
            task.status = TaskStatus.retrying
            task.next_retry_at = utcnow() + timedelta(seconds=settings.retry_delay_seconds)
            await self._add_event(
                session,
                task,
                "task.retry_scheduled",
                {"retry_count": task.retry_count, "next_retry_at": task.next_retry_at.isoformat(), "failure_reason": str(exc)},
            )
        else:
            task.status = TaskStatus.failed
            task.finished_at = utcnow()
            await self._add_event(
                session,
                task,
                "task.failed",
                {"retry_count": task.retry_count, "failure_reason": str(exc)},
            )

    async def _start_step(self, session: AsyncSession, task: Task, stage: str, input_payload: dict[str, Any]) -> TaskStep:
        step = TaskStep(task_id=task.id, stage=stage, status=StepStatus.running, attempt=task.retry_count, input_payload=input_payload)
        session.add(step)
        await self._add_event(session, task, f"stage.{stage}.started", input_payload)
        await session.flush()
        return step

    async def _add_event(self, session: AsyncSession, task: Task, event_type: str, payload: dict[str, Any]) -> None:
        session.add(ExecutionEvent(task_id=task.id, event_type=event_type, payload=payload))
        await session.flush()


agent_service = AgentService()
