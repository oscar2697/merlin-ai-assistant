"""Shared language patterns for recognizing explicit writing commands."""
from __future__ import annotations

import re

# Explicit forms prevent prefixes like ``genera\\w*`` matching the noun
# ``general`` and turning a descriptive sentence into an action.
WRITE_ACTION_PATTERN = (
    r"escribe|escriba|escrib[ií]|escribir|escriban|escribid|escr[ií]beme|"
    r"redacta|redact[eé]|redactar|redacten|redactad|red[aá]ctame|"
    r"describe|describa|describ[ií]|describir|describan|describid|"
    r"write|writes|draft|drafts|compose|composes|"
    r"haz(?:me)?|hacer|haga|hagan|produce|producir|produzca|produzcan|"
    r"crea|cre[aá]|crear|cree|creen|cread|crearme|"
    r"genera|gener[aá]|generar|genere|generen|generad|gen[eé]rame|"
    r"elabora|elabor[aá]|elaborar|elabore|elaboren|"
    r"prepara|prepar[aá]|preparar|prepare|preparen"
)
WRITE_VERB_PATTERN = rf"(?:{WRITE_ACTION_PATTERN})"
WRITE_ACTION_RE = re.compile(rf"\b(?:{WRITE_ACTION_PATTERN})\b", re.IGNORECASE)
WRITE_VERB_RE = re.compile(rf"\b{WRITE_VERB_PATTERN}\b", re.IGNORECASE)
