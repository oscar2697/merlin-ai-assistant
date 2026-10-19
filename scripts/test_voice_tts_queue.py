"""Offline regression test for duplicate streamed TTS playback."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from merlin.tools.voice_tools import SpeechQueue


def main() -> None:
    queue = SpeechQueue(dry_run=True)
    queue.begin_turn()
    queue.enqueue("Hello.")
    queue.speak_remainder("Hello.", "en")
    assert queue._spoken.strip() == "Hello.", (
        f"final response was queued twice: {queue._spoken!r}")
    print("PASS: streamed final sentence is not spoken twice")


if __name__ == "__main__":
    main()
