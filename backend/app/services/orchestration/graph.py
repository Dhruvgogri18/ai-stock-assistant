"""LangGraph turn graph.

    START -> load_context -> route_turn -+-> research ------+
                                         |                  +-> finalize -> persist -> END
                                         +-> out_of_scope --+

The session DB is the source of truth for history, so no checkpointer is used.
"""
from __future__ import annotations

import time
import uuid

from langgraph.graph import END, START, StateGraph

from backend.app.services.orchestration import nodes
from backend.app.services.orchestration.state import GraphState


def build_turn_graph():
    g = StateGraph(GraphState)
    g.add_node("load_context", nodes.load_context)
    g.add_node("route_turn", nodes.route_turn)
    g.add_node("research", nodes.research)
    g.add_node("out_of_scope", nodes.out_of_scope)
    g.add_node("finalize", nodes.finalize)
    g.add_node("persist", nodes.persist)

    g.add_edge(START, "load_context")
    g.add_edge("load_context", "route_turn")
    g.add_conditional_edges("route_turn", nodes.after_router, {"research": "research", "out_of_scope": "out_of_scope"})
    g.add_edge("research", "finalize")
    g.add_edge("out_of_scope", "finalize")
    g.add_edge("finalize", "persist")
    g.add_edge("persist", END)
    return g.compile()


_compiled = None


def get_turn_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_turn_graph()
    return _compiled


def run_turn(session_id: str, message: str, request_id: str | None = None) -> dict:
    return get_turn_graph().invoke({
        "session_id": session_id,
        "request_id": request_id or uuid.uuid4().hex[:12],
        "user_message": message,
        "t_start": time.perf_counter(),
    })
