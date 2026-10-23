"""Typed state that flows through the turn graph (one dict, nodes return partial updates)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from typing_extensions import TypedDict

RouteName = Literal["research", "out_of_scope"]


class GraphState(TypedDict, total=False):
    # --- input ---
    session_id: str
    request_id: str
    user_message: str
    t_start: float

    # --- load_context ---
    history: list[dict]          # prior turns as role/content (LLM-visible)
    is_first_turn: bool

    # --- route_turn ---
    route: Optional[RouteName]
    route_rationale: Optional[str]
    topic: Optional[str]         # "Current Market Overview" -> status label "Analysing ..."
    title: Optional[str]         # chat title proposed by the router (first turn only)

    # --- research ---
    blocks: list[dict[str, Any]]     # structured tool results the UI renders (tables, charts)
    steps: list[dict[str, Any]]      # "Thinking completed" trail: stage / tool / ms
    answer: str
    follow_ups: list[str]
    error: Optional[str]

    # --- finalize / persist ---
    sources: list[dict[str, Any]]
    usage: dict[str, Any]
    duration_ms: int
    message_id: int
