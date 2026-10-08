"""GraphState schema shared by all LangGraph nodes (see AGENTS.md)."""
from __future__ import annotations

from typing import List, TypedDict


class GraphState(TypedDict, total=False):
    input_query: str            # user's spoken or text prompt
    chat_history: List[dict]    # previous turns: {"role": "user"|"merlin", "content": str}
    active_agent: str          # currently active execution node
    tool_calls: List[dict]     # tools invoked during the turn
    tool_results: List[dict]   # outputs returned by tools
    final_response: str        # synthesized final answer text
    error_logs: List[str]      # captured exceptions or failures
    system_prompt_override: str  # dynamic prompt adjusted by self-improvement
    detected_language: str     # "es" | "en" (drives Piper voice selection)
    voice_mode: bool           # True => voice loop continues after TTS


def append_to(state: dict, key: str, item) -> dict:
    """Return a partial-state update that appends `item` to a list field."""
    new_list = list(state.get(key) or [])
    new_list.append(item)
    return {key: new_list}
