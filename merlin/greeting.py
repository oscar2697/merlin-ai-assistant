"""Startup greeting: a formal, time-aware salutation shown in the chat and spoken.

Environment switches:
  MERLIN_GREETING=0         no greeting
  MERLIN_GREETING_SPEAK=0   show it in the chat only (no voice)
  MERLIN_GREETING_LANG=en   greeting language (default "es")
  MERLIN_USER_NAME=...      name used in the greeting ("" = no name)
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from datetime import datetime

from merlin.events import post_event

logger = logging.getLogger("merlin.greeting")

# Name used in the greeting. Change it here or with MERLIN_USER_NAME.
USER_NAME = os.environ.get("MERLIN_USER_NAME", "Oscar")

_TEMPLATES = {
    "en": (
        "{salutation}{name}. Merlin at your service. How may I assist you?",
        "{salutation}{name}. It is a pleasure to see you. What would you like me to do?",
        "{salutation}{name}. Merlin, ready to assist. What are your orders?",
    ),
    "es": (
        "{salutation}{name}. Merlin a su servicio. ¿En qué puedo ayudarle?",
        "{salutation}{name}. Es un placer saludarle. ¿Qué desea que haga por usted?",
        "{salutation}{name}. Merlin, listo para asistirle. ¿Cuáles son sus órdenes?",
    ),
}


def _salutation(hour: int, lang: str) -> str:
    if lang == "en":
        return ("Good morning" if 5 <= hour < 12 else
                "Good afternoon" if 12 <= hour < 19 else "Good evening")
    return ("Buenos días" if 5 <= hour < 12 else
            "Buenas tardes" if 12 <= hour < 19 else "Buenas noches")


def greeting_text(now: datetime | None = None, lang: str | None = None) -> str:
    lang = lang or os.environ.get("MERLIN_GREETING_LANG", "en")
    if lang not in _TEMPLATES:
        lang = "en"
    hour = (now or datetime.now()).hour
    name = f", {USER_NAME}" if USER_NAME else ""
    return random.choice(_TEMPLATES[lang]).format(
        salutation=_salutation(hour, lang), name=name)


def _greet(delay: float) -> None:
    time.sleep(delay)  # let the GUI come up first
    lang = os.environ.get("MERLIN_GREETING_LANG", "en")
    text = greeting_text(lang=lang)
    post_event("merlin_response", text)  # shows "MERLIN: ..." in the chat
    if os.environ.get("MERLIN_GREETING_SPEAK", "1") == "0":
        return
    try:
        from merlin.tools.voice_tools import synthesize_speech_tts
        result = synthesize_speech_tts(text, language=lang)
        if not result.get("ok"):
            logger.info("Greeting not spoken: %s", result.get("error"))
    except Exception:
        logger.exception("Greeting speech failed")


def greet_async(delay: float = 1.5) -> None:
    """Show (and speak) the greeting in a background thread."""
    if os.environ.get("MERLIN_GREETING", "1") == "0":
        return
    threading.Thread(target=_greet, args=(delay,), daemon=True,
                     name="merlin-greeting").start()