"""HTTP schemas for chat sessions."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class StartSessionResponse(BaseModel):
    session_id: str


class MessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class ChatMessage(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: str
    feedback: Literal["up", "down"] | None = None
    # assistant extras
    blocks: list[dict[str, Any]] = []
    follow_ups: list[str] = []
    steps: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    duration_ms: int | None = None
    error: str | None = None


class MessageResponse(BaseModel):
    session_id: str
    title: str
    route: str | None = None
    message: ChatMessage          # the assistant reply, same shape as in history
    request_id: str | None = None


class SessionSummary(BaseModel):
    session_id: str
    title: str
    created_at: str
    updated_at: str


class SessionDetail(SessionSummary):
    messages: list[ChatMessage]


class RenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class FeedbackRequest(BaseModel):
    message_id: int
    value: Literal["up", "down"] | None = None
