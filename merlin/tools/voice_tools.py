"""Voice pipeline skills: wake word, STT (bilingual), TTS (es_ES + en_GB).

Design (Hito 3):
- Capture via `sounddevice` (PortAudio) - PyAudio has no wheels for Python 3.13.
- STT: faster-whisper with temperature 0, VAD and filters against the typical
  Whisper hallucinations (background voices/videos, echoed hint prompt).
- TTS: piper `synthesize_wav` -> temp WAV -> playback via winsound (Windows),
  aplay (Linux) or afplay (macOS). Voices live in ~/merlin/voices (downloaded
  by scripts/download_voices.py; downloaded on demand if missing). The voice is
  chosen from the language of the TEXT being spoken; the turn's language is only
  a tie-breaker.
- SpeechQueue: in voice mode, sentences are spoken as they stream from the
  LLM instead of waiting for the full answer (perceived latency killer).
- Wake word: openwakeword pretrained models. Interim phrase: "hey jarvis"
  (project was codenamed JARVIS); a custom "Merlin" model is a pending
  training task (drop it in ~/merlin/wakewords/). All functions degrade
  gracefully (CONTEXT.md rule 3).
"""
from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

from merlin.config import get_config
from merlin.langdetect import detect_language

logger = logging.getLogger("merlin.tools.voice")

_SAMPLE_RATE = 16000
_WAKE_CHUNK = 1280  # 80 ms of int16 audio at 16 kHz (openwakeword frame)
_PIPER_LOCK = threading.Lock()  # onnx sessions are not thread-safe
_WAKE_LOCK = threading.Lock()
_WHISPER_LOCK = threading.Lock()
_PIPER_CACHE: dict = {}   # loaded Piper voices, keyed by model path
_WAKE_MODEL = None        # openWakeWord model, loaded once and reused
_WHISPER_MODEL = None     # faster-whisper model, loaded once and reused

# Custom openWakeWord .onnx models (e.g. merlin.onnx) are used instead of the
# pre-trained ones when present.
_WAKEWORD_DIR = Path.home() / "merlin" / "wakewords"

# Biases Whisper towards names it tends to mishear ("what's up" -> WhatsApp).
_STT_HINT = "Merlin, WhatsApp, Spotify, iTunes, Chrome, Discord, Visual Studio Code, Steam, YouTube"
_HINT_TOKENS = frozenset(re.findall(r"\w+", _STT_HINT.lower()))

# Whisper was trained on YouTube: with background voices or silence it tends to
# emit these. Never a real command.
_HALLUCINATION_RE = re.compile(
    r"subt[ií]tulos?\b|amara\.org|gracias por (?:ver|mirar)|thanks for watching|"
    r"thank you for watching|suscr[ií]be|subscribe to|like and subscribe|"
    r"no olvides suscribirte|hasta la pr[oó]xima|see you (?:next|in the next)",
    re.IGNORECASE)
_ALLOWED_LANGS = ("es", "en")

# Sentence boundaries for streaming TTS (., !, ? followed by space/end).
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


# ----------------------------------------------------------------- voices
def voice_dir() -> Path:
    d = Path.home() / "merlin" / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_voice_model(voice_name: str) -> Path | None:
    target = voice_dir() / f"{voice_name}.onnx"
    return target if target.exists() else None


def _ensure_voice(voice_name: str) -> Path:
    """Download the voice via scripts/download_voices.py if missing."""
    path = _find_voice_model(voice_name)
    if path is None:
        logger.info("Voice %s missing - attempting download", voice_name)
        script = Path(__file__).resolve().parent.parent.parent / "scripts" / "download_voices.py"
        if script.exists():
            subprocess.run([sys.executable, str(script)], check=False,
                           capture_output=True, timeout=600)
        path = _find_voice_model(voice_name)
    if path is None:
        raise FileNotFoundError(
            f"Piper voice '{voice_name}' not found in {voice_dir()}")
    return path


def _voice_for(language: str) -> str:
    """Voice name for 'es'/'en' from config.piper_voices (tolerates keys like 'es_ES')."""
    voices = get_config().piper_voices
    if language in voices:
        return voices[language]
    for key, name in voices.items():
        if key.lower().startswith(language) or name.lower().startswith(language):
            return name
    return voices.get("en") or next(iter(voices.values()))


# ------------------------------------------------------------------ wake word
def wake_word_available() -> bool:
    try:
        import openwakeword  # noqa: F401
        import sounddevice  # noqa: F401
        return True
    except ImportError:
        return False


def _get_wake_model():
    """Load the openWakeWord model once (ONNX: tflite-runtime has no Windows wheels)."""
    global _WAKE_MODEL
    with _WAKE_LOCK:
        if _WAKE_MODEL is None:
            from openwakeword.model import Model

            custom = sorted(_WAKEWORD_DIR.glob("*.onnx")) if _WAKEWORD_DIR.exists() else []
            if custom:
                model = Model(wakeword_models=[str(p) for p in custom],
                              inference_framework="onnx")
            else:
                model = Model(inference_framework="onnx")  # pretrained models
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
    """Block until a wake word is detected. Interim phrase: 'hey jarvis'.

    stop_event lets the GUI cancel the wait (hands-free toggle off).
    """
    if not wake_word_available():
        logger.warning("openwakeword/sounddevice missing - wake word disabled")
        return None
    try:
        import sounddevice as sd

        oww = _get_wake_model()
        oww.reset()  # drop scores left over from the previous listen
        config = get_config()
        targets = [w.lower() for w in config.wake_words]
        detected: dict = {}

        def callback(indata, _frames, _time, status) -> None:
            if status:
                logger.debug("mic status: %s", status)
            try:
                # openwakeword's predict() expects a numpy int16 array
                # (newer versions reject raw bytes)
                scores = oww.predict(indata[:, 0].copy())
            except Exception:
                logger.exception("wake word frame failed")
                return  # never raise inside the PortAudio callback
            for label, score in scores.items():
                if score > 0.5:
                    for word in targets:
                        if word in label.lower():
                            detected["word"] = label
                            return

        deadline = time.time() + timeout_seconds
        with sd.InputStream(samplerate=_SAMPLE_RATE, channels=1, dtype="int16",
                            blocksize=_WAKE_CHUNK, callback=callback):
            while not detected and time.time() < deadline:
                if stop_event is not None and stop_event.is_set():
                    return None
                time.sleep(0.1)
        return detected.get("word")
    except Exception as exc:
        logger.exception("listen_wake_word failed")
        return None


# ----------------------------------------------------------------- recording
def record_audio(seconds: int = None) -> Path | None:
    """Record N seconds of mic audio to a temp WAV file (16 kHz mono int16)."""
    try:
        import sounddevice as sd

        seconds = seconds or get_config().record_seconds
        data = sd.rec(int(_SAMPLE_RATE * seconds), samplerate=_SAMPLE_RATE,
                      channels=1, dtype="int16")
        sd.wait()
        tmp = Path(tempfile.gettempdir()) / f"merlin_mic_{int(time.time())}.wav"
        with wave.open(str(tmp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_SAMPLE_RATE)
            wf.writeframes(data.tobytes())
        return tmp
    except Exception as exc:
        logger.exception("record_audio failed")
        return None


# ------------------------------------------------------------------------ STT
def _get_whisper():
    """Load faster-whisper once; reloading it on every call is very slow on CPU."""
    global _WHISPER_MODEL
    with _WHISPER_LOCK:
        if _WHISPER_MODEL is None:
            from faster_whisper import WhisperModel
            _WHISPER_MODEL = WhisperModel(get_config().whisper_model_size,
                                          device="cpu", compute_type="int8")
        return _WHISPER_MODEL


def _run_whisper(model, audio, language: str | None):
    """One strict decoding pass: temperature 0 (no random fallback), VAD, and
    Whisper's own silence/low-confidence criteria. Returns (text, info)."""
    segments, info = model.transcribe(
        audio, language=language, task="transcribe",
        initial_prompt=_STT_HINT,
        beam_size=5,
        temperature=0.0,                    # no fallback to random sampling
        condition_on_previous_text=False,   # one utterance: no drifting context
        vad_filter=True,                    # ignore everything that is not speech
        vad_parameters={"min_silence_duration_ms": 500},
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4,
    )
    kept = []
    for seg in segments:  # decoding happens while iterating
        if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
            continue  # Whisper itself says this is silence/noise
        kept.append(seg.text.strip())
    return " ".join(t for t in kept if t).strip(), info


def _is_noise(text: str) -> bool:
    """True for empty text, YouTube-style hallucinations and echoes of the hint."""
    if not text:
        return True
    if _HALLUCINATION_RE.search(text):
        return True
    tokens = re.findall(r"\w+", text.lower())
    return len(tokens) >= 3 and all(t in _HINT_TOKENS for t in tokens)


def transcribe_audio_stt(audio_path: str) -> dict:
    """faster-whisper transcription with es/en detection.

    MERLIN_STT_LANGUAGE=es|en forces the language ("auto" by default)."""
    try:
        import numpy as np

        model = _get_whisper()
        with wave.open(str(audio_path), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        # int16 PCM -> float32 in [-1, 1]; passing an array avoids the `av` decoder
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

        forced = os.environ.get("MERLIN_STT_LANGUAGE", "auto").strip().lower()
        language = None if forced in ("", "auto") else forced
        text, info = _run_whisper(model, audio, language)
        if language is None and info.language not in _ALLOWED_LANGS:
            logger.info("Whisper detected '%s' (p=%.2f): retrying as Spanish",
                        info.language, getattr(info, "language_probability", 0.0))
            text, info = _run_whisper(model, audio, "es")
        if _is_noise(text):
            logger.info("Discarded transcript (silence/hallucination): %r", text)
            return {"ok": False,
                    "error": "No te entendí bien, repítelo por favor."}
        return {"ok": True, "text": text,
                "language": getattr(info, "language", None)}
    except ImportError:
        return {"ok": False, "error": "faster-whisper not installed - STT unavailable."}
    except Exception as exc:
        logger.exception("transcribe_audio_stt failed")
        return {"ok": False, "error": str(exc)}


# ------------------------------------------------------------------------ TTS
def _clean_for_speech(text: str, max_chars: int = 600) -> str:
    """Strip markdown and URLs so Piper does not read symbols out loud."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[`*_#>|~\[\]]", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def synthesize_speech_tts(text: str, language: str = "en") -> dict:
    """Piper TTS: Spanish es_ES-sharvard or British English en_GB-alan.

    The voice follows the language of `text`; `language` only breaks ties."""
    text = _clean_for_speech(text or "")
    if not text:
        return {"ok": False, "error": "Nothing to speak."}
    hint = language[:2].lower() if isinstance(language, str) else "en"
    lang = detect_language(text, default=hint if hint in ("es", "en") else "en")
    voice_name = _voice_for(lang)
    logger.info("TTS: language=%s (hint=%s) voice=%s", lang, hint, voice_name)
    try:
        model_path = _ensure_voice(voice_name)
        return _speak_with_piper(text, voice_name, model_path)
    except Exception as exc:
        logger.warning("TTS unavailable (%s) - text-only mode", exc)
        return {"ok": False, "error": f"TTS unavailable: {exc}"}


def _get_piper_voice(model_path: Path):
    key = str(model_path)
    if key not in _PIPER_CACHE:
        from piper import PiperVoice
        _PIPER_CACHE[key] = PiperVoice.load(model_path)
    return _PIPER_CACHE[key]


def _speak_with_piper(text: str, voice_name: str, model_path: Path) -> dict:
    tmp = Path(tempfile.gettempdir()) / f"merlin_tts_{int(time.time() * 1000)}.wav"
    try:
        with _PIPER_LOCK:
            voice = _get_piper_voice(model_path)
            with wave.open(str(tmp), "wb") as wav_file:
                voice.synthesize_wav(text, wav_file)
        _play_wav(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True, "voice": voice_name}


def _play_wav(path: Path) -> None:
    if sys.platform == "win32":
        import winsound
        winsound.PlaySound(str(path), winsound.SND_FILENAME)
    else:
        player = "aplay" if sys.platform.startswith("linux") else "afplay"
        subprocess.run([player, str(path)], check=False)


# --------------------------------------------------------------- SpeechQueue
class SpeechQueue:
    """Speaks sentences as they stream from the LLM (voice mode only).

    begin_turn() resets the queue; enqueue() adds a finished sentence;
    speak_remainder(final_text) speaks whatever was never streamed and waits
    until playback drains (prevents the mic hearing MERLIN's own answer).
    Set dry_run=True to test without audio (used by tests).
    """

    def __init__(self, dry_run: bool = False) -> None:
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._spoken = ""
        self._active = False
        self._dry_run = dry_run
        self._worker: threading.Thread | None = None

    def begin_turn(self) -> None:
        self._spoken = ""
        with self._queue.mutex:
            self._queue.queue.clear()
        self._active = True
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._worker_loop,
                                            daemon=True, name="merlin-tts")
            self._worker.start()

    def enqueue(self, sentence: str) -> None:
        sentence = (sentence or "").strip()
        if sentence and self._active:
            self._spoken += sentence + " "
            self._queue.put(sentence)

    def speak_remainder(self, final_text: str, language: str) -> None:
        """Speak the part of final_text that was never streamed, then drain."""
        if not self._active:
            # Agents that do not stream (os_agent, web_agent...) never call
            # begin_turn(); start the turn here so their answer is spoken too.
            self.begin_turn()
        final_text = (final_text or "").strip()
        if final_text.startswith(self._spoken.strip()) and len(final_text) > len(self._spoken):
            remainder = final_text[len(self._spoken):].strip()
        elif not self._spoken:
            remainder = final_text
        else:  # streamed text diverged from the final answer -> speak it all
            remainder = final_text
        if remainder:
            self.enqueue(remainder)
        self._queue.put(None)  # drain sentinel
        self._worker.join(timeout=300)

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._active = False
                self._queue.task_done()
                return
            try:
                if self._dry_run:
                    logger.info("[dry-run TTS] %s", item)
                else:
                    synthesize_speech_tts(item, language=self.current_language())
            except Exception:
                logger.exception("SpeechQueue worker failed")
            finally:
                self._queue.task_done()

    def current_language(self) -> str:
        return _LANG_STATE.get("value", "en")


_LANG_STATE: dict = {}


def set_current_language(language: str) -> None:
    _LANG_STATE["value"] = language


def split_sentences(partial: str, spoken_chars: int) -> tuple[list[str], int]:
    """Return complete sentences in partial[spoken_chars:] and the new offset.

    Used by the streaming agents: every finished sentence goes to the
    SpeechQueue while the LLM keeps generating the rest of the answer.
    """
    fresh = partial[spoken_chars:]
    matches = list(_SENTENCE_END.finditer(fresh))
    if not matches:
        return [], spoken_chars
    upto = matches[-1].end()  # include every fully finished sentence
    chunk = fresh[:upto]
    sentences = [s for s in _SENTENCE_END.split(chunk) if s]
    return sentences, spoken_chars + upto


_speech_queue: SpeechQueue | None = None


def get_speech_queue() -> SpeechQueue:
    global _speech_queue
    if _speech_queue is None:
        _speech_queue = SpeechQueue()
    return _speech_queue