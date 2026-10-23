"""LLM turn router. One cheap structured call decides the next graph edge."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.app.services.llm import gateway_client as gw
from backend.app.services.orchestration.prompts import ROUTER_SYSTEM


class RouteDecision(BaseModel):
    route: Literal["research", "out_of_scope"] = "research"
    topic: str = Field(default="", max_length=80)
    title: str = Field(default="", max_length=80)
    rationale: str = ""


def route_turn(*, user_message: str, history: list[dict]) -> RouteDecision:
    """Never blocks the turn: any failure falls back to `research`."""
    recent = "\n".join(f"{m['role']}: {(m['content'] or '')[:300]}" for m in history[-4:]) or "(no prior messages)"
    messages = [
        {"role": "system", "content": ROUTER_SYSTEM},
        {"role": "user", "content": f"Recent conversation:\n{recent}\n\nNew user message:\n{user_message}"},
    ]
    try:
        data = gw.chat_json(messages, model=gw.fast_model(), temperature=0, max_tokens=200)
        return RouteDecision.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        from backend.app.services import llm_trace

        llm_trace.log_event("router_fallback", error=f"{type(exc).__name__}: {exc}", fallback="research")
        return RouteDecision(route="research", rationale="router unavailable; defaulted to research")
