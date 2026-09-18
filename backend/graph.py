"""LangGraph orchestration of the same pipeline.

The plain-Python pipeline in pipeline.py is the source of truth for behavior;
this graph makes the control flow explicit and inspectable — exactly how an
enterprise would visualize and audit agent routing:

    intake -> knowledge -> decision -> (auto) draft -> quality -> execute
                                     -> (human) draft -> escalate

LangGraph is used with plain dict state and typed reducers, no magic.
"""
from __future__ import annotations

from typing import Annotated, Any, TypedDict

from . import pipeline
from .models import Ticket


def _merge_dict(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


class PipelineState(TypedDict, total=False):
    ticket: dict[str, Any]
    ticket_obj: Ticket
    intake: dict | None
    knowledge: dict | None
    decision: dict | None
    draft: dict | None
    quality: dict | None
    route: str
    result: dict | None


def node_intake(state: PipelineState) -> dict:
    trace, usage = [], []
    intake = pipeline.run_intake(state["ticket_obj"], trace, usage)
    return {"intake": intake.model_dump()}


def node_knowledge(state: PipelineState) -> dict:
    from .models import IntakeResult
    trace = []
    knowledge = pipeline.run_knowledge(IntakeResult(**state["intake"]), state["ticket_obj"], trace)
    return {"knowledge": knowledge.model_dump()}


def node_decision(state: PipelineState) -> dict:
    from .models import IntakeResult, KnowledgeResult
    trace = []
    decision = pipeline.run_decision(
        IntakeResult(**state["intake"]), KnowledgeResult(**state["knowledge"]),
        state["ticket_obj"], trace)
    return {"decision": decision.model_dump()}


def route_after_decision(state: PipelineState) -> str:
    d = state["decision"] or {}
    return "auto" if d.get("can_auto_resolve") else "human"


def node_draft_auto(state: PipelineState) -> dict:
    from .models import IntakeResult, KnowledgeResult
    trace, usage = [], []
    draft = pipeline.run_draft(state["ticket_obj"], IntakeResult(**state["intake"]),
                               KnowledgeResult(**state["knowledge"]), trace, usage)
    return {"draft": draft.model_dump()}


def node_quality(state: PipelineState) -> dict:
    from .models import DraftResponse, KnowledgeResult
    trace, usage = [], []
    quality = pipeline.run_quality(DraftResponse(**state["draft"]),
                                   KnowledgeResult(**state["knowledge"]),
                                   state["ticket_obj"], trace, usage)
    return {"quality": quality.model_dump()}


def node_execute(state: PipelineState) -> dict:
    from .models import DecisionResult, IntakeResult
    trace = []
    decision = DecisionResult(**state["decision"])
    intake = IntakeResult(**state["intake"])
    text = state["draft"]["text"]
    actions = pipeline.run_actions(state["ticket_obj"], intake, decision, text, trace)
    return {"route": "executed", "result": {"actions": actions, "response": text}}


def node_escalate(state: PipelineState) -> dict:
    from .models import DecisionResult, DraftResponse
    trace = []
    review = pipeline.run_escalation(
        state["ticket_obj"], DecisionResult(**state["decision"]),
        DraftResponse(**(state["draft"] or {"text": "", "grounded_in": []})), trace)
    return {"route": "human_review", "result": {"review_id": review.id}}


def build_graph():
    from langgraph.graph import END, StateGraph
    g = StateGraph(PipelineState)
    g.add_node("intake", node_intake)
    g.add_node("knowledge", node_knowledge)
    g.add_node("decision", node_decision)
    g.add_node("draft_auto", node_draft_auto)
    g.add_node("quality", node_quality)
    g.add_node("execute", node_execute)
    g.add_node("escalate", node_escalate)

    g.set_entry_point("intake")
    g.add_edge("intake", "knowledge")
    g.add_edge("knowledge", "decision")
    g.add_conditional_edges("decision", route_after_decision, {"auto": "draft_auto", "human": "escalate"})
    g.add_edge("draft_auto", "quality")
    g.add_edge("quality", "execute")
    g.add_edge("execute", END)
    g.add_edge("escalate", END)
    return g.compile()


graph_app = build_graph()


def run_via_graph(ticket: Ticket) -> dict:
    """Run the LangGraph version; returns final state."""
    state: PipelineState = {"ticket": ticket.model_dump(), "ticket_obj": ticket}
    return graph_app.invoke(state)
