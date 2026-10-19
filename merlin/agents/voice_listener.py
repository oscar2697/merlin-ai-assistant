"""Voice_Listener_Node: wake word + mic capture + bilingual STT.

In text mode (GUI/CLI) the input_query arrives pre-filled and the node is a
pass-through to the Router. In voice mode it blocks listening for the wake
word, records the utterance and transcribes it with faster-whisper.
"""
from __future__ import annotations

import logging

from merlin.events import post_event
from merlin.langdetect import detect_language
from merlin.state import GraphState, append_to
from merlin.tools import voice_tools

logger = logging.getLogger("merlin.agents.voice_listener")


def voice_listener_node(state: GraphState) -> dict:
    updates: dict = {"active_agent": "voice_listener"}

    # Text turn: query already provided by GUI/CLI -> straight to router.
    if state.get("input_query"):
        return updates

    if not state.get("voice_mode", False):
        return append_to(
            state, "error_logs",
            "Voice listener reached with no input in text mode; ending turn.")

    # Voice turn: wait for wake word -> record -> transcribe.
    post_event("status", "listening for wake word...")
    if voice_tools.listen_wake_word() is None:
        if not voice_tools.wake_word_available():
            return append_to(
                state, "error_logs",
                "Voice pipeline unavailable (openwakeword/pyaudio missing). "
                "Use the GUI text input or install the voice dependencies.")
        return append_to(state, "error_logs", "No wake word detected; ending turn.")

    post_event("status", "recording...")
    audio = voice_tools.record_audio()
    if audio is None:
        return append_to(state, "error_logs", "Microphone capture failed.")

    result = voice_tools.transcribe_audio_stt(str(audio))
    try:
        audio.unlink(missing_ok=True)
    except OSError:
        logger.debug("Could not remove temporary microphone recording", exc_info=True)
    if not result.get("ok"):
        logger.warning("Voice transcription failed: %s", result.get("error", "unknown"))
        if result.get("uncertain"):
            updates["final_response"] = result["error"]
            updates["detected_language"] = "es"
            return updates
        return append_to(state, "error_logs",
                         f"STT failed: {result.get('error', 'unknown')}")

    query = result.get("text", "")
    language_change = voice_tools.language_command(query)
    if language_change:
        updates["detected_language"] = language_change
        updates["final_response"] = (
            "De acuerdo, reconoceré inglés hasta que me pidas volver al español."
            if language_change == "en" else
            "De acuerdo, volveré a reconocer español.")
        return updates
    post_event("user_input", query)
    updates["input_query"] = query
    updates["detected_language"] = result.get("language") or detect_language(query)
    logger.info("Heard (%s): %s", updates["detected_language"], query)
    return updates
