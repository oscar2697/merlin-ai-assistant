"""Non-streamed responses reach the results panel before voice playback."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.agents import tts_responder


def test_panel_event_is_posted_before_tts_playback():
    order = []
    speech_queue = Mock()
    speech_queue.speak_remainder.side_effect = lambda *_args: order.append("speak")

    def record_event(name, _data):
        order.append(name)

    with patch.object(tts_responder, "post_event", side_effect=record_event), \
            patch.object(tts_responder, "set_current_language"), \
            patch.object(tts_responder, "get_speech_queue", return_value=speech_queue), \
            patch.object(tts_responder, "append_chat_message"), \
            patch.object(tts_responder, "append_execution_log"), \
            patch.object(tts_responder, "now_ms", return_value=10):
        tts_responder.tts_responder_node({
            "final_response": "La relatividad general explica la gravedad.",
            "input_query": "Explica relatividad general",
            "detected_language": "es",
            "speak": True,
        })

    assert order.index("merlin_partial") < order.index("speak")
    assert order.index("speak") < order.index("merlin_response")


if __name__ == "__main__":
    test_panel_event_is_posted_before_tts_playback()
    print("PASS: results panel receives text before speech playback")
