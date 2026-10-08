"""Direct_Response_Node: general conversation with Gemma via LM Studio."""
from __future__ import annotations

import logging

from merlin.events import post_event
from merlin.langdetect import detect_language
from merlin.llm import get_llm
from merlin.state import GraphState

logger = logging.getLogger("merlin.agents.direct_response")


def direct_response_node(state: GraphState) -> dict:
    post_event("status", "direct_response")
    query = state.get("input_query", "")
    language = state.get("detected_language") or detect_language(query)
    updates: dict = {"active_agent": "direct_response",
                     "detected_language": language}

    system_prompt = state.get("system_prompt_override") or _load_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]
    for msg in state.get("chat_history") or []:
        role = "user" if msg.get("role") == "user" else "assistant"
        messages.append({"role": role, "content": msg.get("content", "")})
    messages.append({"role": "user", "content": query})

    fallback = ("I'm running offline right now (LM Studio unreachable), but I "
                f"heard you: \"{query}\". Please start the LM Studio server to "
                "enable full conversation.")
    updates["final_response"] = get_llm().chat(messages, fallback=fallback).strip()
    return updates


def _load_system_prompt() -> str:
    from merlin.config import get_config
    path = get_config().system_prompt_file
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "You are MERLIN, a local voice-controlled OS assistant."
