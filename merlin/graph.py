"""Main LangGraph assembly: hybrid routing + voice loop cycle.

Topology (AGENTS.md):
voice_listener -> router -> {os_agent | opencode_agent |
web_agent | direct_response} -> tts_responder -> (voice_listener | END)

self_improvement no longer lives in the graph: MerlinRuntime runs it once in a
background thread at boot, so it never blocks the first user turn.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from merlin.agents import (
    direct_response_node,
    opencode_agent_node,
    os_agent_node,
    router_node,
    tts_responder_node,
    voice_listener_node,
    web_agent_node,
)
from merlin.router import ROUTE_DIRECT, ROUTE_OS, ROUTE_OPENCODE, ROUTE_WEB
from merlin.state import GraphState


def _after_listener(state: GraphState) -> str:
    return "router" if state.get("input_query") else END


def _after_router(state: GraphState) -> str:
    return state.get("active_agent", ROUTE_DIRECT)


def _after_tts(state: GraphState) -> str:
    return "voice_listener" if state.get("voice_mode") else END


def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("voice_listener", voice_listener_node)
    graph.add_node("router", router_node)
    graph.add_node("os_agent", os_agent_node)
    graph.add_node("opencode_agent", opencode_agent_node)
    graph.add_node("web_agent", web_agent_node)
    graph.add_node("direct_response", direct_response_node)
    graph.add_node("tts_responder", tts_responder_node)

    graph.set_entry_point("voice_listener")

    graph.add_conditional_edges(
        "voice_listener", _after_listener, {"router": "router", END: END})

    graph.add_conditional_edges(
        "router", _after_router,
        {ROUTE_OS: "os_agent", ROUTE_OPENCODE: "opencode_agent",
         ROUTE_WEB: "web_agent", ROUTE_DIRECT: "direct_response"})

    for agent in ("os_agent", "opencode_agent", "web_agent", "direct_response"):
        graph.add_edge(agent, "tts_responder")

    graph.add_conditional_edges(
        "tts_responder", _after_tts,
        {"voice_listener": "voice_listener", END: END})

    return graph.compile()