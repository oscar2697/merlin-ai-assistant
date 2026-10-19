"""Offline tests for STT language preference and retry selection."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.tools import voice_tools


def test_language_modes_and_explicit_switch():
    with patch.dict(os.environ, {}, clear=True):
        assert voice_tools._language_mode() == "es"
        with patch.dict(os.environ, {"MERLIN_STT_LANGUAGE": "en"}):
            assert voice_tools._language_mode() == "en"
        voice_tools.set_stt_language("en")
        assert voice_tools._language_mode() == "en"
        assert voice_tools.language_command("switch to English") == "en"
        assert voice_tools._language_mode() == "en"
        assert voice_tools.language_command("cambia a español") == "es"
        assert voice_tools._language_mode() == "es"
        voice_tools._STT_LANGUAGE_OVERRIDE = None


def test_auto_retries_spanish_when_language_is_uncertain():
    auto = ("strange output", SimpleNamespace(language="pt", language_probability=0.3), 0.7)
    spanish = ("Escribe un ensayo sobre relatividad", SimpleNamespace(language="es", language_probability=0.9), 0.65)
    with patch.object(voice_tools, "_run_whisper", side_effect=[auto, spanish]) as run:
        selected = voice_tools._select_transcription(object(), object(), "auto")
    assert selected == spanish
    assert run.call_count == 2
    assert run.call_args_list[1].args[2] == "es"


def test_confident_english_does_not_run_a_slow_spanish_retry():
    english = ("Switch to English", SimpleNamespace(language="en", language_probability=0.80), 0.72)
    with patch.object(voice_tools, "_run_whisper", return_value=english) as run:
        assert voice_tools._select_transcription(object(), object(), "auto") == english
    assert run.call_count == 1


def test_explicit_language_uses_one_pass():
    english = ("Write an essay", SimpleNamespace(language="en", language_probability=0.9), 0.8)
    with patch.object(voice_tools, "_run_whisper", return_value=english) as run:
        assert voice_tools._select_transcription(object(), object(), "en") == english
    assert run.call_count == 1


def test_known_mixed_language_artifact_is_rejected():
    assert voice_tools._is_incoherent(
        "LaReward es escribiendo ensayos sobre las redes neuronales.")
    assert not voice_tools._is_incoherent(
        "Escribe un ensayo sobre las redes neuronales.")


if __name__ == "__main__":
    test_language_modes_and_explicit_switch()
    test_auto_retries_spanish_when_language_is_uncertain()
    test_confident_english_does_not_run_a_slow_spanish_retry()
    test_explicit_language_uses_one_pass()
    test_known_mixed_language_artifact_is_rejected()
    print("PASS: STT language mode, Spanish retry, and explicit English mode")
