"""Hybrid intent router (keyword map first, optional Gemma classification fallback)."""
from __future__ import annotations

import logging
import os
import re

from merlin import confirm
from merlin.llm import get_llm
from merlin.state import GraphState

logger = logging.getLogger("merlin.router")

ROUTE_OS = "os_agent"
ROUTE_OPENCODE = "opencode_agent"
ROUTE_WEB = "web_agent"
ROUTE_DIRECT = "direct_response"
VALID_ROUTES = (ROUTE_OS, ROUTE_OPENCODE, ROUTE_WEB, ROUTE_DIRECT)

# Bilingual keyword map: English base + Spanish synonyms (per AGENTS.md).
# Keywords of 4 chars or fewer must match a whole word ("ram" != "programa");
# longer ones match as a word prefix ("descarga" -> "descargar", "reprodu" ->
# "reproduce"/"reproduzca"/"reproducir").
KEYWORD_MAP = {
    ROUTE_OPENCODE: (
        "opencode", "code", "coding", "código", "codigo", "prompt",
        "desarrollo", "develop", "development", "refactor", "vscode",
    ),
    ROUTE_OS: (
        "terminal", "shell", "command", "ram", "cpu", "battery", "batería",
        "bateria", "open", "abre", "abrir", "launch", "start", "system",
        "sistema", "file", "archivo", "folder", "carpeta", "download",
        "descarga", "music", "música", "musica", "itunes", "whatsapp",
        "volume", "process", "apaga", "enciende", "reprodu", "canción",
        "cancion", "artista", "artist", "song", "playlist", "play",
        "pause", "pausa",
    ),
    ROUTE_WEB: (
        "arxiv", "paper", "artículo", "articulo", "search", "buscar",
        "busca", "web", "investiga", "research", "internet", "noticias",
        "news",
    ),
}

# "dile a mamá que...", "envía un mensaje a Juan...", "send a message to ..."
# -> send a WhatsApp message (os_agent).
_MESSAGE_INTENT = re.compile(
    r"\b(?:dile|d[ií]gale|escr[ií]bele|m[aá]ndale|env[ií]ale)\s+a\b"
    r"|\b(?:env[ií]a\w*|manda\w*)\s+(?:un\s+)?(?:mensaje|whatsapp)\b"
    r"|\bsend\s+(?:a\s+)?(?:whatsapp\s+)?message\s+to\b",
    re.IGNORECASE)

# "escribe/redacta/describe ... en Word" -> write text into a desktop app.
# Both parts are required: a bare "describe cómo funciona X" is just a question.
_WRITE_VERB = re.compile(
    r"\b(?:escrib\w+|redact\w+|describ\w+|write|draft|compose)\b", re.IGNORECASE)
_WRITE_APP = re.compile(
    r"\b(?:word|wordpad|bloc\s+de\s+notas|notepad|onenote|outlook|excel|powerpoint)\b",
    re.IGNORECASE)

# "busca ... / search ..." goes to the web agent, unless the request is really
# about files, music or WhatsApp ("busca el archivo...", "busca la canción...").
_SEARCH_INTENT = re.compile(
    r"\b(?:busca\w*|b[uú]scame|investiga\w*|search|look\s+up|googlea\w*)\b",
    re.IGNORECASE)
_NOT_WEB_NOUNS = re.compile(
    r"\b(?:archivos?|files?|carpetas?|folders?|itunes|whatsapp|spotify|"
    r"canci[oó]n|canciones|songs?|artista|m[uú]sica|music)\b", re.IGNORECASE)


def _compile(keywords: tuple) -> re.Pattern:
    parts = []
    for kw in keywords:
        escaped = re.escape(kw)
        parts.append(rf"{escaped}(?:s|es)?\b" if len(kw) <= 4 else escaped)
    return re.compile(r"\b(?:" + "|".join(parts) + ")", re.IGNORECASE)


_PATTERNS = {route: _compile(kws) for route, kws in KEYWORD_MAP.items()}

_CLASSIFIER_PROMPT = (
    "You are the intent router of MERLIN, a local voice assistant with "
    "near-total control of the user's laptop.\n"
    "Classify the user request into exactly ONE label:\n"
    "- os_agent: OS control, open/launch apps, terminal commands, system "
    "resources (ram/cpu/battery), files, downloads, music/iTunes, WhatsApp\n"
    "- opencode_agent: software development, dictating or injecting coding "
    "prompts into OpenCode/VSCode\n"
    "- web_agent: web search, research, ArXiv papers, fetching web content\n"
    "- direct_response: general conversation, questions, chit-chat\n"
    "Answer with ONLY the label, nothing else.\n\n"
    "User request: {query}\nLabel:"
)


def route_query(query: str) -> str:
    """Step 1: keyword match (zero tokens). Step 2 (opt-in): LLM classification."""
    q = (query or "").lower()
    if confirm.has_pending():
        if confirm.classify_reply(query):
            logger.info("Router: yes/no to a pending action -> %s", ROUTE_OS)
            return ROUTE_OS
        confirm.clear_pending()  # any other request cancels the pending action
    if _MESSAGE_INTENT.search(q):
        logger.info("Router: message intent -> %s", ROUTE_OS)
        return ROUTE_OS
    if _WRITE_VERB.search(q) and _WRITE_APP.search(q):
        logger.info("Router: write-into-app intent -> %s", ROUTE_OS)
        return ROUTE_OS
    if _SEARCH_INTENT.search(q) and not _NOT_WEB_NOUNS.search(q):
        logger.info("Router: search intent -> %s", ROUTE_WEB)
        return ROUTE_WEB
    for route, pattern in _PATTERNS.items():
        hit = pattern.search(q)
        if hit:
            logger.info("Router keyword hit: '%s' -> %s", hit.group(0), route)
            return route
    # An LLM call just to classify costs minutes on a slow local model, so it is
    # off by default: unmatched requests are treated as conversation.
    if os.environ.get("MERLIN_ROUTER_LLM", "0") == "1":
        return _classify_with_llm(query)
    logger.info("Router: no keyword hit -> %s", ROUTE_DIRECT)
    return ROUTE_DIRECT


def _classify_with_llm(query: str) -> str:
    llm = get_llm()
    answer = llm.chat(
        [{"role": "user", "content": _CLASSIFIER_PROMPT.format(query=query)}],
        fallback=ROUTE_DIRECT,
    )
    label = (answer or "").strip().lower()
    for route in VALID_ROUTES:
        if route in label:
            return route
    return ROUTE_DIRECT


def router_node(state: GraphState) -> dict:
    route = route_query(state.get("input_query", ""))
    return {"active_agent": route}