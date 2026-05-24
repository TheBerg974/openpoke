"""
routes/apm.py — APM chat endpoint.

POST /api/v1/apm/chat
  Accepts user_id, optional thread_id, and message.
  Routes through LangGraph (GlobalRouter → SubAgentExecutor → ContextPruner).
  Persists history to Postgres and caches state in Redis.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ..apm.cache import (
    cache_meta_registry,
    cache_thread_state,
    get_cached_thread_state,
)
from ..apm.database import (
    append_thread_history,
    fetch_all_thread_metas,
    fetch_thread_history,
    init_db,
    upsert_thread_meta,
)
from ..apm.graph import AgentState, agent_graph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apm", tags=["apm"])


class ApmChatRequest(BaseModel):
    user_id: str
    thread_id: Optional[str] = None
    message: str


class ApmChatResponse(BaseModel):
    thread_id: str
    reply: str


@router.on_event("startup")
async def _apm_startup() -> None:
    logger.info("[APM] Initialising database tables…")
    await init_db()
    metas = await fetch_all_thread_metas()
    await cache_meta_registry([m.model_dump(mode="json") for m in metas])
    logger.info("[APM] Startup complete (%d threads in registry).", len(metas))


async def _sync_to_db(
    thread_id: str,
    user_message: str,
    assistant_reply: str,
    final_state: AgentState,
) -> None:
    try:
        await upsert_thread_meta(
            thread_id=thread_id,
            title=f"Thread {thread_id[:8]}",
            summary=assistant_reply[:200],
        )
        await append_thread_history(thread_id, "user", user_message)
        await append_thread_history(thread_id, "assistant", assistant_reply)
        await cache_thread_state(thread_id, dict(final_state))
        metas = await fetch_all_thread_metas()
        await cache_meta_registry([m.model_dump(mode="json") for m in metas])
    except Exception as exc:
        logger.error("[APM] DB sync failed for thread '%s': %s", thread_id, exc)


@router.post("/chat", response_model=ApmChatResponse)
async def apm_chat(
    request: ApmChatRequest, background_tasks: BackgroundTasks
) -> ApmChatResponse:
    thread_id = request.thread_id or str(uuid.uuid4())

    # Hydrate: Redis → Postgres → empty
    state: Optional[AgentState] = await get_cached_thread_state(thread_id)

    if state is None and request.thread_id:
        logger.info("[APM] Cache miss for thread '%s'. Rehydrating from DB.", thread_id)
        rows = await fetch_thread_history(thread_id)
        messages = [{"role": r.role, "content": r.content} for r in rows]
        state = AgentState(
            messages=messages,
            current_thread_id=thread_id,
            active_tools=[],
            _raw_tool_output=None,
            _active_tool_schema=None,
        )
        await cache_thread_state(thread_id, dict(state))
    elif state is None:
        state = AgentState(
            messages=[],
            current_thread_id=thread_id,
            active_tools=[],
            _raw_tool_output=None,
            _active_tool_schema=None,
        )

    state["messages"] = state["messages"] + [
        {"role": "user", "content": request.message}
    ]
    state["current_thread_id"] = thread_id

    final_state: AgentState = await agent_graph.ainvoke(state)

    assistant_reply = next(
        (m["content"] for m in reversed(final_state["messages"]) if m["role"] == "assistant"),
        "I'm sorry, I couldn't generate a response.",
    )

    background_tasks.add_task(
        _sync_to_db, thread_id, request.message, assistant_reply, final_state
    )

    return ApmChatResponse(thread_id=thread_id, reply=assistant_reply)
