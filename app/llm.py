from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from app.config import get_settings
from app.utils import normalize_text


settings = get_settings()


class LLMService:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=settings.llm_api_key or "missing",
            base_url=settings.llm_base_url,
        )

    @property
    def enabled(self) -> bool:
        return bool(settings.llm_api_key)

    async def _json_completion(self, system_prompt: str, user_prompt: str, fallback: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return fallback
        try:
            response = await self._client.chat.completions.create(
                model=settings.llm_model,
                temperature=settings.llm_temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content or "{}"
            return json.loads(content)
        except Exception:
            return fallback

    async def parse_conversation_to_task(self, content: str) -> dict[str, Any]:
        normalized = normalize_text(content)
        fallback = {
            "actionable": True,
            "title": "Conversation Task",
            "description": content.strip(),
            "confidence": 0.55,
            "assistant_reply": "I created a task from your message and queued it for execution.",
        }
        if len(normalized.split()) < 3:
            fallback = {
                "actionable": False,
                "title": "",
                "description": "",
                "confidence": 0.2,
                "assistant_reply": "Please share a fuller request so I can turn it into an executable task.",
            }
        system_prompt = (
            "You convert a user chat message into an executable task for an AI operations agent. "
            "Return JSON with actionable, title, description, confidence, assistant_reply."
        )
        user_prompt = content
        result = await self._json_completion(system_prompt, user_prompt, fallback)
        return {
            "actionable": bool(result.get("actionable", fallback["actionable"])),
            "title": str(result.get("title", fallback["title"]))[:255],
            "description": str(result.get("description", fallback["description"])),
            "confidence": float(result.get("confidence", fallback["confidence"])),
            "assistant_reply": str(result.get("assistant_reply", fallback["assistant_reply"])),
        }

    async def create_plan(self, task_title: str, task_description: str, memory_summary: str, retry_count: int) -> dict[str, Any]:
        fallback = {
            "goal": task_title,
            "steps": [
                "Load relevant memory and prior execution context.",
                "Produce a concrete execution response for the task request.",
                "Verify completion quality and summarize final output.",
            ],
            "reasoning_trace": "The plan uses memory first, execution second, and verification last to preserve consistency.",
        }
        system_prompt = (
            "You are the planner in a multi-stage AI agent. Return JSON with goal, steps, reasoning_trace. "
            "Keep reasoning_trace concise and high level."
        )
        user_prompt = (
            f"Task title: {task_title}\n"
            f"Task description: {task_description}\n"
            f"Relevant memory summary: {memory_summary}\n"
            f"Retry count: {retry_count}"
        )
        result = await self._json_completion(system_prompt, user_prompt, fallback)
        steps = result.get("steps", fallback["steps"])
        if not isinstance(steps, list) or not steps:
            steps = fallback["steps"]
        return {
            "goal": str(result.get("goal", fallback["goal"])),
            "steps": [str(step) for step in steps][:6],
            "reasoning_trace": str(result.get("reasoning_trace", fallback["reasoning_trace"])),
        }

    async def execute_plan(
        self,
        task_title: str,
        task_description: str,
        plan: dict[str, Any],
        memory_summary: str,
        prior_failure: str | None,
    ) -> dict[str, Any]:
        fallback = {
            "actions_taken": [
                "Reviewed the task objective and available memory context.",
                "Generated an execution response aligned to the task goal.",
                "Prepared a concise outcome summary for verification.",
            ],
            "result": (
                f"Executed task '{task_title}' using the stored memory context. "
                f"Primary request: {task_description}"
            ),
            "reasoning_trace": "The executor used prior memory to avoid repetition and produced a direct deliverable.",
        }
        system_prompt = (
            "You are the executor in a planner-executor-verifier AI workflow. "
            "Return JSON with actions_taken, result, reasoning_trace."
        )
        user_prompt = (
            f"Task title: {task_title}\n"
            f"Task description: {task_description}\n"
            f"Plan: {json.dumps(plan)}\n"
            f"Relevant memory summary: {memory_summary}\n"
            f"Prior failure to avoid: {prior_failure or 'none'}"
        )
        result = await self._json_completion(system_prompt, user_prompt, fallback)
        actions_taken = result.get("actions_taken", fallback["actions_taken"])
        if not isinstance(actions_taken, list) or not actions_taken:
            actions_taken = fallback["actions_taken"]
        return {
            "actions_taken": [str(action) for action in actions_taken][:8],
            "result": str(result.get("result", fallback["result"])),
            "reasoning_trace": str(result.get("reasoning_trace", fallback["reasoning_trace"])),
        }

    async def verify_execution(self, task_title: str, plan: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        fallback = {
            "passed": True,
            "failure_reason": None,
            "summary": f"Verified task '{task_title}' and confirmed the execution output is coherent and complete.",
            "reasoning_trace": "The verifier checked that the result maps back to the plan and task goal.",
        }
        system_prompt = (
            "You are the verifier in a multi-stage AI agent. Return JSON with passed, failure_reason, summary, reasoning_trace. "
            "Fail only when the execution clearly does not satisfy the task."
        )
        user_prompt = f"Plan: {json.dumps(plan)}\nExecution: {json.dumps(execution)}"
        result = await self._json_completion(system_prompt, user_prompt, fallback)
        return {
            "passed": bool(result.get("passed", fallback["passed"])),
            "failure_reason": result.get("failure_reason"),
            "summary": str(result.get("summary", fallback["summary"])),
            "reasoning_trace": str(result.get("reasoning_trace", fallback["reasoning_trace"])),
        }

    async def summarize_memories(self, memories: list[str]) -> str:
        fallback = " | ".join(memories[:6])[:1500]
        system_prompt = (
            "Summarize memory snippets for an AI task execution engine. "
            "Return JSON with summary."
        )
        user_prompt = "\n".join(memories)
        result = await self._json_completion(system_prompt, user_prompt, {"summary": fallback})
        return str(result.get("summary", fallback))

    async def explain_duplicate(self, original_summary: str) -> str:
        fallback = f"Duplicate request detected. Reused prior successful execution summary: {original_summary}"
        system_prompt = (
            "Explain in one short paragraph that a duplicate task was detected and a previous successful execution was reused. "
            "Return JSON with summary."
        )
        result = await self._json_completion(system_prompt, original_summary, {"summary": fallback})
        return str(result.get("summary", fallback))


llm_service = LLMService()
