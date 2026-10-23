"""Chat session routes. Thin: validate -> call the service -> map errors to HTTP."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from backend.app.repositories import llm_call_repository, session_repository as repo
from backend.app.schemas.session import (
    FeedbackRequest, MessageRequest, MessageResponse, RenameRequest, SessionDetail, SessionSummary,
    StartSessionResponse,
)
from backend.app.services import llm_trace
from backend.app.services.orchestration import service

router = APIRouter(tags=["session"])


def _or_404(session_id: str):
    try:
        return service.get_session_or_raise(session_id)
    except service.SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Unknown session_id") from None


@router.post("/session", response_model=StartSessionResponse)
def start_session() -> StartSessionResponse:
    return StartSessionResponse(session_id=service.start_session().session_id)


@router.get("/sessions", response_model=list[SessionSummary])
def list_sessions() -> list[dict]:
    return repo.list_sessions()


@router.get("/session/{session_id}", response_model=SessionDetail)
def get_session(session_id: str) -> SessionDetail:
    s = _or_404(session_id)
    return SessionDetail(session_id=s.session_id, title=s.title, created_at=s.created_at, updated_at=s.updated_at,
                         messages=[service.to_chat_message(m) for m in s.messages])


@router.patch("/session/{session_id}", response_model=SessionSummary)
def rename_session(session_id: str, body: RenameRequest) -> dict:
    _or_404(session_id)
    repo.set_title(session_id, body.title)
    s = repo.get_session(session_id, with_messages=False)
    return {"session_id": s.session_id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at}


@router.delete("/session/{session_id}")
def delete_session(session_id: str) -> dict:
    _or_404(session_id)
    repo.delete_session(session_id)
    return {"ok": True}


@router.post("/session/{session_id}/message", response_model=MessageResponse)
def send_message(session_id: str, body: MessageRequest) -> MessageResponse:
    try:
        return service.send_message(session_id, body.message.strip())
    except service.SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Unknown session_id") from None
    except service.SessionBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/session/{session_id}/status")
def session_status(session_id: str) -> dict:
    """Poll while /message is in flight. Never 404s, so a client can start polling immediately."""
    return service.get_live_status(session_id)


@router.post("/session/{session_id}/feedback")
def feedback(session_id: str, body: FeedbackRequest) -> dict:
    _or_404(session_id)
    if not repo.set_feedback(session_id, body.message_id, body.value):
        raise HTTPException(status_code=404, detail="Unknown message_id")
    return {"ok": True}


# ---- LLM observability -------------------------------------------------------
@router.get("/session/{session_id}/llm-calls")
def llm_calls(session_id: str, prompts: bool = True) -> list[dict]:
    """Every model call in this chat: stage, model, exact messages sent, raw reply, tokens, ms, errors."""
    _or_404(session_id)
    return llm_call_repository.list_calls(session_id, include_prompts=prompts)


@router.get("/session/{session_id}/usage")
def usage(session_id: str) -> dict:
    return service.usage(session_id)


@router.get("/session/{session_id}/log", response_class=PlainTextResponse)
def transcript(session_id: str) -> str:
    """The human-readable transcript (logs/sessions/<id>.log)."""
    _or_404(session_id)
    p = llm_trace.session_log_path(session_id)
    return p.read_text(encoding="utf-8") if p.exists() else "(no log yet)"
