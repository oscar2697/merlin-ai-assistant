"""Offline regression test for duplicate streamed TTS playback."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from merlin.tools.voice_tools import SpeechQueue, split_sentences


def main() -> None:
    queue = SpeechQueue(dry_run=True)
    queue.begin_turn()
    queue.enqueue("Hello.")
    queue.speak_remainder("Hello.", "en")
    assert queue._spoken.strip() == "Hello.", (
        f"final response was queued twice: {queue._spoken!r}")

    # LLM output often has paragraph breaks while the queued sentence text has
    # normalized spaces. The finalizer must still continue after what was spoken.
    queue = SpeechQueue(dry_run=True)
    queue.begin_turn()
    partial = "Primera frase.\n\nSegunda frase sigue"
    sentences, consumed = split_sentences(partial, 0)
    for sentence in sentences:
        queue.enqueue(sentence)
    queue.set_streamed_prefix(partial, consumed)
    queue.speak_remainder("Primera frase.\n\nSegunda frase sigue completa.", "es")
    assert queue._spoken.strip() == "Primera frase. Segunda frase sigue completa.", (
        f"streamed answer was replayed or truncated: {queue._spoken!r}")

    # If a fallback diverges completely, never replay the already spoken prefix.
    queue = SpeechQueue(dry_run=True)
    queue.begin_turn()
    queue.enqueue("Ya dije esto.")
    queue.set_streamed_prefix("Ya dije esto.", len("Ya dije esto."))
    queue.speak_remainder("Una respuesta alternativa.", "es")
    assert queue._spoken.strip() == "Ya dije esto.", (
        f"divergent response was replayed: {queue._spoken!r}")
    print("PASS: streamed final sentence is not spoken twice")


if __name__ == "__main__":
    main()
