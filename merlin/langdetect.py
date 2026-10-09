"""Heuristic Spanish/English detection used to select the Piper voice."""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-záéíóúüñ]+")
_SPANISH_CHARS = re.compile(r"[¿¡ñáéíóúü]")

# Ambiguous words ("no", "me", "a", "sin") are deliberately left out.
_SPANISH_MARKERS = frozenset("""
que qué cómo como dónde donde cuándo cuando cuál cual cuánto cuanto quién quien
hora día dia hoy abre abrir cierra busca buscar búsqueda busqueda investiga
es está esta están estoy soy eres mi mis tu tus su sus mamá canción cancion
canciones artista música musica pon ponme reproduce reproduzca reproducir
reproduciendo pausa pausar siguiente anterior descarga envía envia manda
mensaje gracias por favor para haz hazme dime dame muéstrame enséñame hola
buenos tardes noches batería bateria archivo carpeta sistema quiero necesito
puedes puedo tengo tienes hay ayúdame ayudame ayuda abriendo abrí listo
información informacion sobre también tambien pero porque muy más mas con
desde hasta entre el la los las del un una y en lo al se de
""".split())

_ENGLISH_MARKERS = frozenset("""
the and is are to of in it you for with on that this was be have has i my your
not but opening playing done found results about also can there here what
what's whats how which now sure okay please open play pause next previous
search show tell give hello hi thanks thank who where when why do does did will
would could should from by at an
""".split())


def detect_language(text: str, default: str = "en") -> str:
    """Return 'es' or 'en'; `default` only breaks ties (no clear evidence)."""
    if not text:
        return default
    low = text.lower()
    words = _WORD_RE.findall(low)
    es = sum(1 for w in words if w in _SPANISH_MARKERS)
    en = sum(1 for w in words if w in _ENGLISH_MARKERS)
    if _SPANISH_CHARS.search(low):
        es += 2  # ñ, tildes, ¿ and ¡ are strong Spanish signals
    if es > en:
        return "es"
    if en > es:
        return "en"
    return default