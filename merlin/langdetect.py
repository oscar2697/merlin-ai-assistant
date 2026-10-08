"""Heuristic Spanish/English detection used to select the Piper voice."""
from __future__ import annotations

_SPANISH_MARKERS = (
    "que", "qué", "cómo", "como", "dónde", "donde", "cuándo", "hora", "abre",
    "abrir", "busca", "buscar", "investiga", "es", "está", "mi", "mamá",
    "canción", "cancion", "descarga", "envía", "envia", "gracias", "por",
    "favor", "para", "haz", "dime", "hola", "batería", "bateria", "archivo",
    "carpeta", "sistema", "quiero", "puedes", "ayúdame", "ayudame",
)


def detect_language(text: str, default: str = "en") -> str:
    """Return 'es' or 'en' based on simple word markers in the text."""
    if not text:
        return default
    words = set(text.lower().replace("?", " ").replace("¡", " ").split())
    hits = sum(1 for marker in _SPANISH_MARKERS if marker in words)
    return "es" if hits >= 1 else default
