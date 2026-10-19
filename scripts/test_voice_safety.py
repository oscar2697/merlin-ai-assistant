"""Uncertain voice transcripts must be rejected before intent routing."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.agents.voice_listener import voice_listener_node
from merlin.graph import _after_listener


def test_uncertain_transcript_requests_repeat_without_query():
    with patch("merlin.agents.voice_listener.voice_tools.listen_wake_word", return_value="jarvis"), \
         patch("merlin.agents.voice_listener.voice_tools.record_audio", return_value=Path("fake.wav")), \
         patch("merlin.agents.voice_listener.voice_tools.transcribe_audio_stt",
               return_value={"ok": False, "uncertain": True,
                             "error": "No te entendí con suficiente claridad. Repite la orden, por favor."}), \
         patch("merlin.agents.voice_listener.voice_tools.language_command") as language_command:
        update = voice_listener_node({"voice_mode": True, "error_logs": []})
    assert "input_query" not in update
    assert "Repite la orden" in update["final_response"]
    assert _after_listener(update) == "tts_responder"
    language_command.assert_not_called()


if __name__ == "__main__":
    test_uncertain_transcript_requests_repeat_without_query()
    print("PASS: uncertain voice transcript is spoken back and never routed")
