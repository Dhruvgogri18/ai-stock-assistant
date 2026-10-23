"""Router-facing session service: the only thing routers call for chat."""
from __future__ import annotations

import time

from backend.app.repositories import llm_call_repository, session_repository as repo
from backend.app.repositories.session_repository import DEFAULT_TITLE, Message, Session
from backend.app.schemas.session import ChatMessage, MessageResponse
from backend.app.services import llm_trace
from backend.app.services.orchestration.graph import run_turn


class SessionNotFoundError(KeyError):
    pass


class SessionBusyError(RuntimeError):
    pass


def start_session() -> Session:
    return repo.create_session()


def get_session_or_raise(session_id: str) -> Session:
    s = repo.get_session(session_id)
    if s is None:
        raise SessionNotFoundError(session_id)
    return s


def get_live_status(session_id: str) -> dict:
    """Cheap in-memory read; safe to poll every second while /message is in flight."""
    snap = llm_trace.get_status(session_id)
    if not snap:
        return {"busy": False}
    return {"busy": True, "stage": snap.get("stage"), "label": snap.get("label"), "updated_at": snap.get("updated_at")}


def to_chat_message(m: Message) -> ChatMessage:
    p = m.payload or {}
    return ChatMessage(
        id=m.id, role=m.role, content=m.content, created_at=m.created_at, feedback=m.feedback,
        blocks=p.get("blocks", []), follow_ups=p.get("follow_ups", []), steps=p.get("steps", []),
        sources=p.get("sources", []), usage=p.get("usage", {}), duration_ms=p.get("duration_ms"),
        error=p.get("error"),
    )


def send_message(session_id: str, message: str) -> MessageResponse:
    session = get_session_or_raise(session_id)
    if llm_trace.get_status(session_id):
        raise SessionBusyError("This chat is still answering the previous message.")
    if not session.messages and session.title == DEFAULT_TITLE:
        repo.set_title(session_id, message.strip()[:60])  # sidebar shows something while the model works

    t0 = time.perf_counter()
    rid = llm_trace.start_request(session_id=session_id, user_message=message)
    try:
        state = run_turn(session_id, message, request_id=rid)
    except Exception as exc:  # noqa: BLE001
        llm_trace.end_request(status="error", error=f"{type(exc).__name__}: {exc}",
                              duration_ms=round((time.perf_counter() - t0) * 1000, 1))
        raise
    llm_trace.end_request(status="error" if state.get("error") else "ok", error=state.get("error"),
                          duration_ms=round((time.perf_counter() - t0) * 1000, 1))

    fresh = repo.get_session(session_id)
    reply = next(m for m in reversed(fresh.messages) if m.id == state["message_id"])
    return MessageResponse(session_id=session_id, title=fresh.title, route=state.get("route"),
                           message=to_chat_message(reply), request_id=rid)


def usage(session_id: str) -> dict:
    get_session_or_raise(session_id)
    return llm_call_repository.usage_summary(session_id)
