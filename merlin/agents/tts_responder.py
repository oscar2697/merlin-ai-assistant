"""TTS_Responder_Node: Piper speech + chat history + execution logging."""
from __future__ import annotations

import logging

from merlin.config import get_config
from merlin.events import post_event
from merlin.persistence import append_chat_message, append_execution_log, now_ms
from merlin.state import GraphState
from merlin.tools.voice_tools import get_speech_queue, set_current_language

logger = logging.getLogger("merlin.agents.tts_responder")

_TURN_START_MS = {}


def tts_responder_node(state: GraphState) -> dict:
    post_event("status", "tts_responder")
    response = state.get("final_response", "")
    language = state.get("detected_language") or get_config().default_language
    query = state.get("input_query", "")

    # Voice input (GUI) sets speak=True: if direct_response already streamed
    # sentences into the SpeechQueue, only the remainder is spoken here;
    # speak_remainder blocks until playback drains so the mic never hears
    # MERLIN's own answer. voice_mode (headless loop) implies speaking too.
    if (state.get("voice_mode") or state.get("speak")) and response:
        # Non-streamed agent responses have no merlin_partial events. Publish
        # the completed text before blocking on playback so the results panel
        # is visible as soon as speech starts, just like streamed answers.
        post_event("merlin_partial", response)
        set_current_language(language)
        get_speech_queue().speak_remainder(response, language)

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
