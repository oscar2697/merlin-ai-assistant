"""Hybrid intent router (keyword map first, Gemma classification fallback)."""
from __future__ import annotations

import logging

from merlin.llm import get_llm
from merlin.state import GraphState

logger = logging.getLogger("merlin.router")

ROUTE_OS = "os_agent"
ROUTE_OPENCODE = "opencode_agent"
ROUTE_WEB = "web_agent"
ROUTE_DIRECT = "direct_response"
VALID_ROUTES = (ROUTE_OS, ROUTE_OPENCODE, ROUTE_WEB, ROUTE_DIRECT)

# Bilingual keyword map: English base + Spanish synonyms (per AGENTS.md)
KEYWORD_MAP = {
    ROUTE_OPENCODE: (
        "opencode", "code", "coding", "código", "codigo", "prompt",
        "desarrollo", "develop", "development", "refactor", "vscode",
    ),
    ROUTE_OS: (
        "terminal", "shell", "command", "ram", "cpu", "battery", "batería",
        "bateria", "open", "abre", "abrir", "launch", "start", "system",
        "sistema", "file", "archivo", "folder", "carpeta", "download",
        "descarga", "music", "itunes", "whatsapp", "volume", "process",
        "apaga", "enciende", "reproduce", "canción", "cancion",
    ),
    ROUTE_WEB: (
        "arxiv", "paper", "papers", "search", "buscar", "busca", "web",
        "investiga", "research", "internet", "noticias", "news",
        "artículo", "articulo",
    ),
}

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
    """Step 1: keyword match (zero tokens). Step 2: LLM classification fallback."""
    q = (query or "").lower()
    for route, keywords in KEYWORD_MAP.items():
        for kw in keywords:
            if kw in q:
                logger.debug("Router keyword hit: '%s' -> %s", kw, route)
                return route
    return _classify_with_llm(query)


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
