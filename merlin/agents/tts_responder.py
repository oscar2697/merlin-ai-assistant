"""TTS_Responder_Node: Piper speech + chat history + execution logging."""
from __future__ import annotations

import logging
import threading

from merlin.config import get_config
from merlin.events import post_event
from merlin.persistence import append_chat_message, append_execution_log, now_ms
from merlin.state import GraphState
from merlin.tools import voice_tools

logger = logging.getLogger("merlin.agents.tts_responder")

_TURN_START_MS = {}


def tts_responder_node(state: GraphState) -> dict:
    post_event("status", "tts_responder")
    response = state.get("final_response", "")
    language = state.get("detected_language") or get_config().default_language
    query = state.get("input_query", "")

    # Non-blocking TTS: speak in a daemon thread so the graph keeps flowing
    if state.get("voice_mode") and response:
        threading.Thread(
            target=voice_tools.synthesize_speech_tts,
            args=(response, language), daemon=True,
        ).start()

    if query:
        append_chat_message("user", query)
    if response:
        append_chat_message("merlin", response)
    append_execution_log({
        "query": query,
        "agent": state.get("active_agent", ""),
        "response": response,
        "errors": state.get("error_logs") or [],
        "duration_ms": now_ms() - _TURN_START_MS.get("value", now_ms()),
        "language": language,
    })

    post_event("merlin_response", response)

    # Reset per-turn fields for the next voice cycle
    return {
        "active_agent": "tts_responder",
        "input_query": "",
        "tool_calls": [],
        "tool_results": [],
        "error_logs": [],
    }


def mark_turn_start() -> None:
    """Called by the runtime just before invoking the graph."""
    _TURN_START_MS["value"] = now_ms()
