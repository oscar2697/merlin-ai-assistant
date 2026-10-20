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
_STT_LANGUAGE_OVERRIDE: str | None = None

# Custom openWakeWord .onnx models (e.g. merlin.onnx) are used instead of the
# pre-trained ones when present.
_WAKEWORD_DIR = Path.home() / "merlin" / "wakewords"

# Biases Whisper towards names it tends to mishear ("what's up" -> WhatsApp).
_STT_HINT = (
    "MERLIN, WhatsApp, Spotify, iTunes, Apple Music, Chrome, Discord, "
    "Microsoft Word, Excel, PowerPoint, Bloc de notas, Windows; física, "
    "matrices de Pauli, mecánica cuántica, operador, espín, vector, matriz, "
    "autovalor, autovector, ecuación, derivada, integral, relatividad, "
    "simulación, Python, LaTeX."
)
# Only filter a transcript that is effectively an echo of the application-name
# hint. Never use the full science vocabulary here: real requests such as
# "matrices de Pauli" must not be discarded as hallucinations.
_HINT_TOKENS = frozenset(re.findall(
    r"\w+", "merlin whatsapp spotify itunes chrome discord steam youtube word excel powerpoint".lower()))

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
def _endpoint_loop(read_chunk, max_chunks: int, chunk_ms: int = 64,
                   silence_limit_ms: int = None, floor_min: float = 110.0,
                   hard_speech: float = 650.0):
    """State machine: pull audio chunks until the user stops talking.

    - Calibrates the noise floor while waiting for speech (running minimum).
    - Speech = RMS above max(noise_floor * 3.5, floor_min), or above the hard
      `hard_speech` level (clear voice near the mic) so that talking with no
      quiet lead-in still counts as speech even before a floor exists.
    - Keeps a pre-roll buffer so the first word is never clipped.
    - Stops after `silence_limit_ms` of continuous silence once speech was seen.
    Returns (chunks, spoke) without touching the microphone: `read_chunk` is
    injectable, so the logic is unit-testable with synthetic audio.
    """
    import numpy as np
    from collections import deque

    config = get_config()
    silence_limit = max(1, int((silence_limit_ms or config.silence_end_ms) / chunk_ms))
    pre_roll = deque(maxlen=max(1, int(config.pre_speech_buffer_ms / chunk_ms)))
    min_speech_chunks = max(1, int(320 / chunk_ms))  # allow short commands without accepting single clicks

    out: list = []
    pre_roll.clear()
    spoke = False
    speech_chunks = 0
    silence_run = 0
    floor = None
    quiet_wait = 0

    for _ in range(max_chunks):
        chunk = read_chunk()
        if chunk is None:
            break
        rms = float(np.sqrt(np.mean(np.square(chunk.astype(np.float32)))))
        is_speech = (rms > max((floor or 0) * 3.5, floor_min)
                     or rms > hard_speech)

        if not spoke:
            pre_roll.append(chunk)
            if floor is None:
                floor = rms
            else:
                floor = min(floor, rms)  # noise floor only goes down pre-speech
            quiet_wait += 1
            if is_speech:
                speech_chunks = 1
                # require a sustained burst: single clicks (keys, bumps) are not speech
                for old in list(pre_roll)[:-1]:
                    out.append(old)
                out.append(chunk)
                spoke = True
                quiet_wait = 0
            elif quiet_wait >= max_chunks // 2:
                break  # nothing said at all: stop waiting
            continue

        out.append(chunk)
        if is_speech:
            speech_chunks += 1
            silence_run = 0
        else:
            silence_run += 1
            if silence_run >= silence_limit and speech_chunks >= min_speech_chunks:
                break

    if not spoke or speech_chunks < min_speech_chunks:
        return [], False
    return out, True


def record_audio(seconds: int = None) -> Path | None:
    """Record the user's utterance with silence endpointing: MERLIN stops
    listening ~1 s after you stop talking (hard cap: record_max_seconds).
    Returns None if no speech was detected (or the mic is unavailable)."""
    try:
        import numpy as np
        import sounddevice as sd

        config = get_config()
        chunk_frames = 1024  # 64 ms at 16 kHz
        max_s = seconds or config.record_max_seconds
        max_chunks = int(max_s * _SAMPLE_RATE / chunk_frames)

        with sd.InputStream(samplerate=_SAMPLE_RATE, channels=1, dtype="int16",
                            blocksize=chunk_frames) as stream:
            def read_chunk():
                data, _overflowed = stream.read(chunk_frames)
                return np.asarray(data)[:, 0]
            chunks, spoke = _endpoint_loop(read_chunk, max_chunks)

        if not spoke:
            logger.info("record_audio: no speech detected")
            return None
        audio = np.concatenate(chunks)
        tmp = Path(tempfile.gettempdir()) / f"merlin_mic_{time.time_ns()}.wav"
        with wave.open(str(tmp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        if os.environ.get("MERLIN_STT_DEBUG_AUDIO", "0") == "1":
            debug_dir = Path.home() / "merlin" / "debug_audio"
            debug_dir.mkdir(parents=True, exist_ok=True)
            import shutil
            debug_path = debug_dir / tmp.name
            shutil.copy2(tmp, debug_path)
            logger.info("STT debug audio saved to %s (contains private audio)", debug_path)
        logger.info("record_audio: %.1f s captured", len(audio) / _SAMPLE_RATE)
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
        best_of=5,
        temperature=0.0,                    # no fallback to random sampling
        condition_on_previous_text=False,   # one utterance: no drifting context
        vad_filter=True,                    # ignore everything that is not speech
        vad_parameters={"min_silence_duration_ms": 650, "speech_pad_ms": 250},
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4,
    )
    kept = []
    confidences = []
    for seg in segments:  # decoding happens while iterating
        if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
            continue  # Whisper itself says this is silence/noise
        kept.append(seg.text.strip())
        if seg.text.strip():
            # avg_logprob is negative; exp() maps it to an intuitive 0..1
            # segment score without adding another model or dependency.
            import math
            confidences.append(math.exp(min(0.0, float(seg.avg_logprob))))
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return " ".join(t for t in kept if t).strip(), info, confidence


def _is_noise(text: str) -> bool:
    """True for empty text, YouTube-style hallucinations and echoes of the hint."""
    if not text:
        return True
    if _HALLUCINATION_RE.search(text):
        return True
    tokens = re.findall(r"\w+", text.lower())
    return len(tokens) >= 3 and all(t in _HINT_TOKENS for t in tokens)


def _is_incoherent(text: str) -> bool:
    """Catch a common Spanish/English decoding artifact before it can act."""
    low = (text or "").lower()
    # Whisper sometimes combines the copula with a Spanish gerund
    # ("es escribiendo"), a strong sign that the recognized words drifted.
    return bool(re.search(
        r"\b(?:es|est[aá])\s+(?:escribiendo|redactando|describiendo|"
        r"generando|preparando|elaborando)\b", low))


def _language_mode() -> str:
    """Return the explicitly configured STT language; Spanish is the default."""
    if _STT_LANGUAGE_OVERRIDE in ("es", "en"):
        return _STT_LANGUAGE_OVERRIDE
    value = os.environ.get("MERLIN_STT_LANGUAGE", "es").strip().lower()
    return value if value in ("es", "en", "auto") else "es"


def set_stt_language(language: str) -> None:
    """Set the in-session language after an explicit spoken language command."""
    global _STT_LANGUAGE_OVERRIDE
    if language in ("es", "en"):
        _STT_LANGUAGE_OVERRIDE = language


def language_command(text: str) -> str | None:
    """Recognize an explicit request to use English or return to Spanish."""
    low = (text or "").lower()
    if re.search(r"\b(?:switch|change|cambia|cambiar|cámbiame)\b.{0,24}\b(?:to\s+)?(?:english|ingl[eé]s)\b", low):
        set_stt_language("en")
        return "en"
    if re.search(r"\b(?:switch|change|cambia|cambiar|cámbiame)\b.{0,24}\b(?:to\s+)?(?:spanish|espa[nñ]ol)\b", low):
        set_stt_language("es")
        return "es"
    return None


def _select_transcription(model, audio, mode: str):
    """Decode one explicit language, or compare auto and Spanish candidates.

    In auto mode Spanish wins close scores, reflecting MERLIN's primary
    language while still allowing English when its decoding is clearly better.
    """
    if mode in ("es", "en"):
        return _run_whisper(model, audio, mode)

    first = _run_whisper(model, audio, None)
    text, info, confidence = first
    detected = getattr(info, "language", None)
    probability = float(getattr(info, "language_probability", 0.0) or 0.0)
    if detected == "es" and probability >= 0.55 and confidence >= 0.35:
        return first
    if detected == "en" and probability >= 0.75 and confidence >= 0.60:
        # A confident English utterance is probably an intentional language
        # switch; don't spend another full CPU decode forcing it to Spanish.
        return first

    spanish = _run_whisper(model, audio, "es")
    # Compare acoustic decoder confidence. Favor Spanish on close scores;
    # retain a clearly stronger English auto result.
    if spanish[0] and (not text or spanish[2] >= confidence - 0.08):
        return spanish
    return first


def transcribe_audio_stt(audio_path: str) -> dict:
    """Transcribe speech with faster-whisper.

    Spanish is the default because MERLIN's primary interaction language is
    Spanish. Set MERLIN_STT_LANGUAGE=auto for bilingual auto-detection or =en
    for English-only use. Returns text, language, and language_probability.
    """
    try:
        import numpy as np

        model = _get_whisper()
        with wave.open(str(audio_path), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

        mode = _language_mode()
        text, info, confidence = _select_transcription(model, audio, mode)

        if _is_noise(text):
            logger.info("Discarded transcript (silence/hallucination): %r", text)
            return {"ok": False, "uncertain": True,
                    "error": "No te entendí bien. Habla un poco más cerca del micrófono o repite la instrucción."}
        if _is_incoherent(text):
            logger.info("Rejected incoherent transcript: %r", text)
            return {"ok": False, "uncertain": True,
                    "error": "No reconocí una orden coherente. Repite la instrucción, por favor."}

        language = getattr(info, "language", None)
        probability = float(getattr(info, "language_probability", 0.0) or 0.0)
        if confidence < 0.35 or probability < 0.20:
            logger.info("Rejected uncertain STT: mode=%s language=%s probability=%.2f confidence=%.2f",
                        mode, language, probability, confidence)
            return {"ok": False, "uncertain": True,
                    "error": "No te entendí con suficiente claridad. Repite la orden, por favor."}
        logger.info("STT result: mode=%s language=%s probability=%.2f confidence=%.2f chars=%d text=%r",
                    mode, language, probability, confidence, len(text), text)
        return {"ok": True, "text": text, "language": language,
                "language_probability": probability, "confidence": confidence}
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
        self._streamed_prefix = ""
        self._active = False
        self._dry_run = dry_run
        self._worker: threading.Thread | None = None

    def begin_turn(self) -> None:
        self._spoken = ""
        self._streamed_prefix = ""
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

    def set_streamed_prefix(self, text: str, consumed_chars: int) -> None:
        """Remember the exact LLM prefix already queued for speech.

        The final response can contain paragraph breaks that are absent from
        the normalized sentence strings in the queue, so comparing `_spoken`
        directly with `startswith` can replay the whole response.
        """
        self._streamed_prefix = (text or "")[:max(0, consumed_chars)]

    def speak_remainder(self, final_text: str, language: str) -> None:
        """Speak the part of final_text that was never streamed, then drain."""
        if not self._active:
            # Agents that do not stream (os_agent, web_agent...) never call
            # begin_turn(); start the turn here so their answer is spoken too.
            self.begin_turn()
        final_text = (final_text or "").strip()
        spoken_prefix = self._streamed_prefix or self._spoken
        if not spoken_prefix:
            remainder = final_text
        else:
            remainder = _text_after_normalized_prefix(final_text, spoken_prefix)
            if remainder is None:
                # Prefix matching failed, so replaying the final response could
                # repeat content already spoken. Keep the visible final answer,
                # but fail closed for audio rather than replaying it wholesale.
                logger.info("Final TTS text does not match streamed prefix; "
                            "skipping audio remainder to avoid duplicate speech")
                remainder = ""
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


def _text_after_normalized_prefix(text: str, prefix: str) -> str | None:
    """Return the suffix after `prefix`, ignoring whitespace-only differences."""
    normalized_prefix = " ".join((prefix or "").split())
    if not normalized_prefix:
        return (text or "").strip()
    normalized_chars = []
    raw_ends = []
    index = 0
    source = text or ""
    while index < len(source):
        if source[index].isspace():
            while index < len(source) and source[index].isspace():
                index += 1
            normalized_chars.append(" ")
            raw_ends.append(index)
        else:
            normalized_chars.append(source[index])
            index += 1
            raw_ends.append(index)
    untrimmed = "".join(normalized_chars)
    # Trimming the normalized text also trims its raw-index map at the edges.
    leading = len(untrimmed) - len(untrimmed.lstrip())
    trailing = len(untrimmed.rstrip())
    normalized_text = untrimmed.strip()
    raw_ends = raw_ends[leading:trailing]
    if not normalized_text.startswith(normalized_prefix):
        return None
    raw_cut = raw_ends[len(normalized_prefix) - 1]
    return source[raw_cut:].strip()


_speech_queue: SpeechQueue | None = None


def get_speech_queue() -> SpeechQueue:
    global _speech_queue
    if _speech_queue is None:
        _speech_queue = SpeechQueue()
    return _speech_queue
