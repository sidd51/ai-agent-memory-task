# Architecture Explanation

## Overview

The system is a FastAPI-based AI agent execution platform built around two task input modes:

1. Manual task creation
2. Conversational input

Every task is persisted, processed asynchronously, enriched with memory context, executed through a multi-step agent pipeline, and written back to the database with detailed history.

## Core Layers

### 1. Input Layer

- `POST /tasks` accepts manual task creation requests.
- `POST /conversations/{conversation_id}/messages` accepts conversational input and uses the LLM parser to convert actionable messages into queued tasks.
- `app/static/index.html` provides a lightweight browser UI for both input modes.

### 2. Persistent Storage Layer

SQLite is used through async SQLAlchemy models.

Key tables:

- `tasks`: master task record, execution state, retry metadata, dedupe key, final summary, recovery context
- `task_steps`: per-stage workflow history for memory loading, planning, execution, and verification
- `execution_events`: timestamped execution timeline
- `memory_entries`: persistent agent memory for requests, plans, results, summaries, and failures
- `conversations` and `conversation_messages`: chat history and conversational task intake

### 3. Agent Memory Layer

The memory layer persists information beyond a single request:

- request memory stores what the user asked for
- plan memory stores the generated multi-step workflow
- result memory stores the execution output
- summary memory stores the verified final result
- failure memory stores retry-relevant failure context

Relevant context retrieval works by:

1. loading recent memory entries and prior completed or failed tasks
2. ranking them using lexical similarity and token overlap
3. selecting the most relevant matches
4. summarizing them when the history becomes too large

This gives the agent continuity while keeping prompts bounded.

### 4. Multi-Step Agent Workflow

The execution pipeline is not a single prompt. It is a staged workflow:

1. `memory_loader`
2. `planner`
3. `executor`
4. `verifier`

Each stage writes:

- structured input
- structured output
- concise reasoning trace
- status

This makes execution inspectable and recoverable.

### 5. Async Execution Layer

The background worker runs inside the FastAPI lifespan and polls the database for queued or retryable tasks.

Processing behavior:

1. claim an eligible task
2. mark it `running`
3. execute the staged workflow
4. commit summaries, events, steps, and memory
5. retry or fail with preserved context when needed

This simulates a real operational agent instead of synchronous request-time execution.

## Reliability and Recovery Design

### Duplicate Prevention

Every task receives a deterministic fingerprint from its normalized title and description.

Before execution:

1. the agent checks for a prior completed or deduplicated task with the same fingerprint
2. if found, execution is skipped
3. the new task is marked `deduplicated`
4. the previous summary is reused and stored as the new task outcome

This prevents repeated actions and unnecessary cost.

### Retry-Aware Recovery

When a task fails:

1. the failure reason is stored
2. a failure memory entry is created
3. recovery context is preserved in `last_context`
4. the task is moved to `retrying` if retries remain
5. the worker picks it up again after `next_retry_at`

The retry attempt uses stored failure context to avoid repeating the same path blindly.

### Execution History

The system preserves three levels of history:

- coarse-grained state on the task record
- stage-level history in `task_steps`
- timeline events in `execution_events`

This supports auditability and debugging.

## LLM Usage

Gemini is used through the `openai` Python SDK against Google's OpenAI-compatible endpoint.

LLM responsibilities:

- conversational task parsing
- planning
- execution content generation
- verification
- memory summarization
- duplicate explanation phrasing

If no API key is configured, deterministic fallbacks keep the app runnable for local development, but the intended deployment path is with a real provider.

## Why This Meets the Brief

- persistent memory: `memory_entries` and task history are stored in the database
- execution history: `task_steps` and `execution_events`
- relevant retrieval: memory ranking and summarization
- duplicate avoidance: fingerprint-based dedupe
- multi-step workflow: memory loader, planner, executor, verifier
- failure recovery: retry scheduling and context preservation
- execution states: queued, running, retrying, completed, failed, deduplicated
- reasoning traces: concise stage rationales are stored and returned
- async processing: background worker loop
- Docker: included
- database: included
- FastAPI: included
- LLM provider: OpenAI-compatible layer included
