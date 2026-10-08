"""Central configuration for MERLIN (LM Studio, voice pipeline, paths, safety)."""
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
    # --- LLM (LM Studio, OpenAI-compatible local server) ---
    lm_studio_base_url: str = os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
    lm_studio_api_key: str = os.getenv("LM_STUDIO_API_KEY", "")
    model_name: str = os.getenv("MERLIN_MODEL", "google/gemma-4-e4b")
    temperature: float = 0.1
    llm_timeout_seconds: int = 600   # CPU inference: thinking models are slow
    llm_max_tokens: int = 1500       # reasoning tokens + answer tokens

    # --- Voice pipeline (bilingual, offline) ---
    wake_words: tuple = ("merlin", "arthur")
    whisper_model_size: str = "small"  # "small" | "medium" (CPU int8)
    piper_voices: dict = field(
        default_factory=lambda: {
            "es": "es_ES-sharpest-medium",
            "en": "en_GB-alan-medium",  # British English per user requirement
        }
    )
    default_language: str = "en"
    record_seconds: int = 8

    # --- Behaviour ---
    max_chat_history_messages: int = 12
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


@lru_cache(maxsize=1)
def get_config() -> MerlinConfig:
    return MerlinConfig()


def ensure_dirs() -> None:
    """Create runtime directories (logs/) if missing."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
