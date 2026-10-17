"""Synthetic tests for the microphone endpoint detector; no mic required."""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from merlin.tools.voice_tools import _endpoint_loop


def run(samples):
    it = iter(samples)
    chunks, spoke = _endpoint_loop(lambda: next(it, None), max_chunks=len(samples),
                                   chunk_ms=64, silence_limit_ms=256,
                                   floor_min=110.0, hard_speech=650.0)
    return chunks, spoke


def test_silence_not_speech():
    quiet = [np.full(1024, 80, dtype=np.int16) for _ in range(30)]
    chunks, spoke = run(quiet)
    assert not spoke and not chunks, "silence should not be treated as speech"


def test_sustained_speech_detected():
    quiet = [np.full(1024, 80, dtype=np.int16) for _ in range(5)]
    voice = [np.full(1024, 1800, dtype=np.int16) for _ in range(12)]
    silence = [np.full(1024, 60, dtype=np.int16) for _ in range(8)]
    chunks, spoke = run(quiet + voice + silence)
    assert spoke and chunks, "sustained voice should be detected"


if __name__ == "__main__":
    test_silence_not_speech()
    test_sustained_speech_detected()
    print("PASS: synthetic silence and speech endpoint tests")
