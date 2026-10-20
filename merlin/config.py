"""Central configuration for MERLIN (Ollama, voice, paths, and safety)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
PROMPTS_DIR = PACKAGE_DIR / "prompts"
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "system_prompt.txt"
LOGS_DIR = PROJECT_ROOT / "logs"

load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class MerlinConfig:
    # --- LLM (Ollama local HTTP API) ---
    ollama_base_url: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    model_name: str = os.getenv("MERLIN_MODEL", "qwen2.5-coder:3b")
    # Fast non-thinking model for short syntheses (tool/web summaries).
    # Empty = use the main model everywhere.
    fast_model_name: str = os.getenv("MERLIN_FAST_MODEL", "qwen2.5-coder:3b")
    temperature: float = 0.1
    llm_timeout_seconds: int = 600   # CPU inference: thinking models are slow
    llm_max_tokens: int = 1500       # reasoning tokens + answer tokens

    # --- Voice pipeline (bilingual, offline) ---
    # Interim wake phrase: "hey jarvis" (openwakeword pretrained; the project
    # was codenamed JARVIS). A custom "Merlin"/"Arthur" model is a pending
    # openwakeword training task.
    wake_words: tuple = ("jarvis",)  # pretrained model; Merlin/Arthur need a custom model
    whisper_model_size: str = os.getenv("MERLIN_WHISPER_SIZE", "small")
    piper_voices: dict = field(
        default_factory=lambda: {
            "es": "es_ES-sharvard-medium",
            "en": "en_GB-alan-medium",  # British English per user requirement
        }
    )
    default_language: str = os.getenv("MERLIN_STT_LANGUAGE", "es")
    # Endpointing: record until ~silence_end_ms of silence after speech.
    # record_seconds is now only the legacy/fixed-window fallback maximum.
    record_seconds: int = 8
    record_max_seconds: int = 25      # hard cap for an endpointed utterance
    silence_end_ms: int = 1300        # how long quiet means "he finished"
    pre_speech_buffer_ms: int = 480   # pre-roll so the first word is not clipped

    # --- Behaviour ---
    max_chat_history_messages: int = 6  # shorter prompts = less thinking + faster prefill
    terminal_timeout_seconds: int = 30

    # --- Paths ---
    logs_dir: Path = LOGS_DIR
    system_prompt_file: Path = SYSTEM_PROMPT_FILE
    opencode_prompt_filename: str = "PROMPT_OPENCODE.md"

    # --- Safety: minimal blacklist of irreversible actions only ---
    command_blacklist: tuple = (
        "rm -rf /",
        "rm -rf ~",
        "rm -rf /*",
        "mkfs",
        "dd if=",
        "format c:",
        "diskpart",
        "reg delete hklm",
        "del /f /s /q",
        "/dev/sda",
    )

    @property
    def lm_studio_base_url(self) -> str:
        """Backward-compatible alias for callers using the former field name."""
        return self.ollama_base_url


@lru_cache(maxsize=1)
def get_config() -> MerlinConfig:
    return MerlinConfig()


def ensure_dirs() -> None:
    """Create runtime directories (logs/) if missing."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
