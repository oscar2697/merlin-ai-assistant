"""Voice pipeline skills: wake word, STT (bilingual), TTS (es_ES + en_GB).

All functions degrade gracefully: if a library or model is missing they
return an error dict instead of crashing the graph (CONTEXT.md rule 3).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from merlin.config import get_config

logger = logging.getLogger("merlin.tools.voice")

_PIPER_LOCK = threading.Lock()
_WAKE_LOCK = threading.Lock()
_WHISPER_LOCK = threading.Lock()
_WAKE_MODEL = None      # openWakeWord model, loaded once and reused
_WHISPER_MODEL = None   # faster-whisper model, loaded once and reused

# Drop custom openWakeWord .onnx models here (e.g. merlin.onnx) to use them
# instead of the pre-trained ones (alexa, hey_jarvis, hey_mycroft, hey_rhasspy).
_WAKEWORD_DIR = Path.home() / "merlin" / "wakewords"


def wake_word_available() -> bool:
    try:
        import openwakeword  # noqa: F401
        import pyaudio  # noqa: F401
        return True
    except ImportError:
        return False


def _get_wake_model():
    """Load the openWakeWord model once; warn if it can never match wake_words."""
    global _WAKE_MODEL
    with _WAKE_LOCK:
        if _WAKE_MODEL is None:
            from openwakeword.model import Model as WakeModel

            custom = sorted(_WAKEWORD_DIR.glob("*.onnx"))
            if custom:
                model = WakeModel(wakeword_models=[str(p) for p in custom],
                                  inference_framework="onnx")
            else:
                model = WakeModel(inference_framework="onnx")  # pre-trained models
            names = list(model.models.keys())
            wanted = [w.lower() for w in get_config().wake_words]
            logger.info("Wake models loaded: %s | wake_words: %s", names, wanted)
            if not any(w in name.lower() for name in names for w in wanted):
                logger.warning(
                    "No loaded wake model matches wake_words=%s, so detection can "
                    "never trigger. Put a custom model in %s or change wake_words.",
                    wanted, _WAKEWORD_DIR)
            _WAKE_MODEL = model
        return _WAKE_MODEL


def listen_wake_word(timeout_seconds: int = 60,
                     stop_event: threading.Event | None = None) -> str | None:
    """Block until a wake word ('merlin'/'arthur') is detected, the timeout
    expires or stop_event is set. Returns the matched model name or None."""
    if not wake_word_available():
        logger.warning("openwakeword/pyaudio not installed - wake word disabled")
        return None
    pa = None
    stream = None
    try:
        import numpy as np
        import pyaudio

        config = get_config()
        targets = [w.lower() for w in config.wake_words]
        model = _get_wake_model()
        model.reset()  # drop scores left over from the previous listen
        chunk = 1280
        rate = 16000
        pa = pyaudio.PyAudio()
        stream = pa.open(format=pyaudio.paInt16, channels=1, rate=rate,
                         input=True, frames_per_buffer=chunk)
        deadline = time.time() + timeout_seconds
        best_word, best_score = "", 0.0
        while time.time() < deadline and not (stop_event and stop_event.is_set()):
            data = stream.read(chunk, exception_on_overflow=False)
            audio = np.frombuffer(data, dtype=np.int16)  # bytes -> ndarray int16
            scores = model.predict(audio)
            for word, score in scores.items():
                if score > best_score:
                    best_word, best_score = word, score
                if score > 0.5 and any(w in word.lower() for w in targets):
                    logger.info("Wake word detected: %s (%.2f)", word, score)
                    return word
        if not (stop_event and stop_event.is_set()):
            logger.info("Wake word timeout. Best score seen: %s = %.2f",
                        best_word, best_score)
    except Exception:
        logger.exception("listen_wake_word failed")
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        if pa is not None:
            pa.terminate()
    return None


def record_audio(seconds: int = None) -> Path | None:
    """Record N seconds of mic audio to a temp WAV file."""
    try:
        import pyaudio
        import wave

        seconds = seconds or get_config().record_seconds
        chunk, rate = 1024, 16000
        pa = pyaudio.PyAudio()
        stream = pa.open(format=pyaudio.paInt16, channels=1, rate=rate,
                         input=True, frames_per_buffer=chunk)
        frames = [stream.read(chunk, exception_on_overflow=False)
                  for _ in range(0, int(rate / chunk * seconds))]
        stream.stop_stream()
        stream.close()
        pa.terminate()

        tmp = Path(tempfile.gettempdir()) / f"merlin_mic_{int(time.time())}.wav"
        with wave.open(str(tmp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
            wf.setframerate(rate)
            wf.writeframes(b"".join(frames))
        return tmp
    except Exception as exc:
        logger.exception("record_audio failed")
        return None


def _get_whisper():
    """Load faster-whisper once; reloading it on every call is very slow on CPU."""
    global _WHISPER_MODEL
    with _WHISPER_LOCK:
        if _WHISPER_MODEL is None:
            from faster_whisper import WhisperModel
            _WHISPER_MODEL = WhisperModel(get_config().whisper_model_size,
                                          device="cpu", compute_type="int8")
        return _WHISPER_MODEL


def transcribe_audio_stt(audio_path: str) -> dict:
    """faster-whisper transcription with automatic es/en detection."""
    try:
        import wave
        import numpy as np

        model = _get_whisper()
        with wave.open(str(audio_path), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        # int16 PCM -> float32 in [-1, 1], the format faster-whisper expects
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

        segments, info = model.transcribe(audio, language=None, task="transcribe")
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return {"ok": bool(text), "text": text, "language": info.language}
    except ImportError:
        return {"ok": False, "error": "faster-whisper not installed - STT unavailable."}
    except Exception as exc:
        logger.exception("transcribe_audio_stt failed")
        return {"ok": False, "error": str(exc)}


def synthesize_speech_tts(text: str, language: str = "en") -> dict:
    """Piper TTS: Spanish es_ES-* or British English en_GB-* voice."""
    config = get_config()
    if not text:
        return {"ok": False, "error": "Nothing to speak."}
    voice_name = config.piper_voices.get(language, config.piper_voices["en"])
    with _PIPER_LOCK:
        try:
            return _speak_with_piper_api(text, voice_name)
        except ImportError:
            pass
        try:
            return _speak_with_piper_cli(text, voice_name)
        except Exception as exc:
            logger.warning("TTS unavailable (%s) - text-only mode", exc)
            return {"ok": False, "error": f"TTS unavailable: {exc}"}


def _find_voice_model(voice_name: str) -> Path | None:
    """Locate a downloaded Piper .onnx voice (model dir or HuggingFace cache)."""
    candidates = [
        Path.home() / "merlin" / "voices" / f"{voice_name}.onnx",
        Path.home() / ".cache" / "piper" / f"{voice_name}.onnx",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _speak_with_piper_api(text: str, voice_name: str) -> dict:
    from piper import PiperVoice  # piper-tts >= 1.3 python API
    import sounddevice

    model_path = _find_voice_model(voice_name)
    if model_path is None:
        raise FileNotFoundError(
            f"Piper voice '{voice_name}' not downloaded. Place the .onnx under "
            f"~/merlin/voices/ (es_ES for Spanish, en_GB for British English).")
    voice = PiperVoice.load(str(model_path))
    voice.speak(text, length_scale=1.0, speaker=sounddevice.default)
    return {"ok": True, "voice": voice_name}


def _speak_with_piper_cli(text: str, voice_name: str) -> dict:
    if not shutil.which("piper"):
        raise RuntimeError("'piper' CLI not found on PATH.")
    model_path = _find_voice_model(voice_name)
    if model_path is None:
        raise FileNotFoundError(f"Piper voice '{voice_name}' not downloaded.")
    proc = subprocess.run(
        ["piper", "--model", str(model_path.with_suffix(""))],
        input=text, capture_output=True, text=True, shell=(sys.platform == "win32"),
    )
    # piper CLI writes wav to stdout; player integration is milestone 2
    return {"ok": proc.returncode == 0, "voice": voice_name,
            "note": "Piper CLI produced audio (playback wiring is milestone 2)."}