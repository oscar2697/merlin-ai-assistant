"""Pending-action confirmation: risky actions (sending a message) wait for a yes/no.

The os_agent stores the action with set_pending(); the router sends the next
utterance back to the os_agent when it is a clear yes/no; any other request
cancels the pending action, and it expires after _TTL_SECONDS.
"""
from __future__ import annotations

import re
import threading
import time
import unicodedata

_TTL_SECONDS = 60
_LOCK = threading.Lock()
_PENDING: dict = {}

_YES = frozenset("""
si claro confirmo confirmalo envialo enviar mandalo manda hazlo adelante dale
ok okay yes send it do go ahead correcto exacto afirmativo
""".split())
_NO = frozenset("""
no cancela cancelar cancelalo dejalo olvidalo mejor para detente stop cancel
never mind nada negativo
""".split())
# Neutral fillers that may surround a yes/no (Spanish examples are preserved).
_FILLERS = frozenset("por favor porfa gracias please pues bueno entonces".split())


def _tokens(text: str) -> list:
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z]+", text)


def classify_reply(text: str) -> str | None:
    """'yes', 'no' or None (anything that is not a short, clear confirmation)."""
    tokens = [t for t in _tokens(text) if t not in _FILLERS]
    if not tokens or len(tokens) > 4:
        return None
    if all(t in _YES for t in tokens):
        return "yes"
    if all(t in _NO for t in tokens):
        return "no"
    return None


def set_pending(data: dict) -> None:
    with _LOCK:
        _PENDING.clear()
        _PENDING.update(data)
        _PENDING["_time"] = time.time()


def peek_pending() -> dict | None:
    with _LOCK:
        if not _PENDING:
            return None
        if time.time() - _PENDING.get("_time", 0) > _TTL_SECONDS:
            _PENDING.clear()
            return None
        return dict(_PENDING)


def has_pending() -> bool:
    return peek_pending() is not None


def clear_pending() -> None:
    with _LOCK:
        _PENDING.clear()
