from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm import llm_service
from app.models import MemoryEntry, MemoryScope, MemoryType, Task, TaskStatus
from app.utils import fingerprint_text, normalize_text


settings = get_settings()


class MemoryService:
    async def store_memory(
        self,
        session: AsyncSession,
        *,
        content: str,
        memory_type: MemoryType,
        scope: MemoryScope = MemoryScope.global_scope,
        task_id: str | None = None,
        conversation_id: str | None = None,
        summary: str | None = None,
        importance: int = 1,
    ) -> MemoryEntry:
        memory = MemoryEntry(
            task_id=task_id,
            conversation_id=conversation_id,
            scope=scope,
            memory_type=memory_type,
            content=content,
            summary=summary,
            fingerprint=fingerprint_text(content),
            importance=importance,
        )
        session.add(memory)
        await session.flush()
        return memory

    async def find_duplicate_task(self, session: AsyncSession, dedupe_key: str, exclude_task_id: str) -> Task | None:
        statement = (
            select(Task)
            .where(Task.dedupe_key == dedupe_key, Task.id != exclude_task_id, Task.status.in_([TaskStatus.completed, TaskStatus.deduplicated]))
            .order_by(desc(Task.finished_at), desc(Task.updated_at))
            .limit(1)
        )
        return (await session.execute(statement)).scalar_one_or_none()

    async def get_relevant_context(self, session: AsyncSession, task: Task) -> dict[str, object]:
        memories = (await session.execute(select(MemoryEntry).order_by(desc(MemoryEntry.created_at)).limit(50))).scalars().all()
        tasks = (
            await session.execute(
                select(Task)
                .where(Task.id != task.id, Task.status.in_([TaskStatus.completed, TaskStatus.failed, TaskStatus.deduplicated]))
                .order_by(desc(Task.updated_at))
                .limit(20)
            )
        ).scalars().all()
        scored_memories: list[tuple[float, str]] = []
        task_text = f"{task.title} {task.description}"
        for memory in memories:
            text = memory.summary or memory.content
            score = self._similarity(task_text, text) + (memory.importance * 0.05)
            scored_memories.append((score, text))
        for prior_task in tasks:
            text = f"{prior_task.title}. {prior_task.latest_summary or prior_task.description}"
            score = self._similarity(task_text, text)
            scored_memories.append((score, text))
        scored_memories.sort(key=lambda item: item[0], reverse=True)
        selected = [text for score, text in scored_memories if score > 0.08][: settings.memory_relevance_limit]
        combined = "\n".join(selected)
        if len(combined) > settings.memory_summary_char_threshold:
            combined = await llm_service.summarize_memories(selected)
        if not combined:
            combined = "No highly relevant prior memory was found."
        return {"items": selected, "summary": combined}

    def _similarity(self, left: str, right: str) -> float:
        normalized_left = normalize_text(left)
        normalized_right = normalize_text(right)
        if not normalized_left or not normalized_right:
            return 0.0
        lexical = SequenceMatcher(a=normalized_left, b=normalized_right).ratio()
        left_terms = set(normalized_left.split())
        right_terms = set(normalized_right.split())
        overlap = len(left_terms & right_terms) / max(1, len(left_terms | right_terms))
        return (lexical * 0.6) + (overlap * 0.4)


memory_service = MemoryService()
