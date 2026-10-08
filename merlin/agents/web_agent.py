"""Web_Agent_Node: web search (always opens the browser) + summary, ArXiv, URLs."""
from __future__ import annotations

import logging
import os
import re
import urllib.parse

from merlin.events import post_event
from merlin.langdetect import detect_language
from merlin.llm import get_llm
from merlin.state import GraphState
from merlin.tools import system_tools, web_tools

logger = logging.getLogger("merlin.agents.web_agent")

# "busca información sobre X en Google Chrome y dame un resumen" -> X
_TOPIC_RE = re.compile(
    r"\b(?:search(?:\s+for)?|look\s+up|busca(?:r|me)?|b[uú]scame|investiga(?:r)?|googlea(?:r)?)\s+"
    r"(?:(?:informaci[oó]n|info|information)\s+(?:sobre|de|acerca\s+de|about|on)\s+)?"
    r"(.+?)"
    r"(?:\s+(?:en|in|on|con)\s+(?:google\s+chrome|google|chrome|internet|la\s+web|the\s+web))?"
    r"(?:\s+(?:y|and)\s+(?:dame|d[ií]me|give\s+me|tell\s+me|res[uú]melo|resume|summari[sz]e)\b.*)?"
    r"\s*$",
    re.IGNORECASE)

_SYSTEM_PROMPT = {
    "es": ("Eres el agente de investigación de MERLIN. Resume los hallazgos para "
           "el usuario en español, máximo 3 frases, en tono conversacional (se "
           "leerá en voz alta). Menciona la fuente cuando ayude. No inventes "
           "datos que no estén en los hallazgos."),
    "en": ("You are MERLIN's research agent. Summarize the findings for the user "
           "in English, max 3 sentences, conversational tone (it will be read "
           "aloud). Mention the source when it helps. Do not invent facts that "
           "are not in the findings."),
}


def _extract_topic(query: str) -> str:
    q = query.strip(" ?.,!¿¡")
    m = _TOPIC_RE.search(q)
    topic = (m.group(1) if m else q).strip(" ?.,!¿¡")
    return topic or q


def _web_search(topic: str, max_results: int = 5) -> dict:
    """DuckDuckGo text search (no API key). Needs: pip install ddgs"""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS  # older package name
    except ImportError:
        return {"ok": False, "error": "ddgs not installed (pip install ddgs)."}
    try:
        hits = DDGS().text(topic, max_results=max_results) or []
        results = [{"title": h.get("title", ""), "url": h.get("href", ""),
                    "snippet": h.get("body", "")} for h in hits]
        if not results:
            return {"ok": False, "error": "no results found."}
        return {"ok": True, "results": results}
    except Exception as exc:
        logger.exception("web search failed")
        return {"ok": False, "error": str(exc)}


def _domain(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc
    return host[4:] if host.startswith("www.") else host


def _format_results(result: dict) -> str:
    if not result.get("ok"):
        return f"Search failed: {result.get('error')}"
    return "\n".join(
        f"- {r['title']} ({_domain(r['url'])}): {r['snippet'][:250]}"
        for r in result.get("results", []))


def _local_summary(result: dict, es: bool) -> str:
    """Used when the LLM is offline or returns nothing."""
    if not result.get("ok"):
        return ""
    items = [f"{r['title']} ({_domain(r['url'])})" for r in result["results"][:3]]
    head = "Encontré, entre otros: " if es else "Top results: "
    return head + "; ".join(items) + "."


def web_agent_node(state: GraphState) -> dict:
    post_event("status", "web_agent")
    query = state.get("input_query", "")
    updates: dict = {"active_agent": "web_agent",
                     "detected_language": state.get("detected_language")
                     or detect_language(query)}
    es = str(updates["detected_language"] or "en").lower().startswith("es")

    url_match = re.search(r"https?://\S+", query)
    is_arxiv = bool(re.search(r"arxiv|papers?|artículos?|articulo", query, re.I))
    opened = False
    failure = ""

    if is_arxiv:
        topic = re.sub(
            r"\b(?:arxiv|papers?|artículos?|articulo|about|sobre|search|searching"
            r"|busca|buscar|investiga|investigar|research|find|dame|los|las|el|la)\b",
            "", query, flags=re.I).strip(" :,.-") or query
        result = web_tools.search_arxiv_papers(topic, max_results=3)
        findings = _summarize_papers(result)
        tool, args = "search_arxiv_papers", {"query": topic}
        fallback = findings[:600]
        if not result.get("ok"):
            failure = findings
    elif url_match:
        result = web_tools.fetch_web_content(url_match.group(0))
        content = (result.get("content") or "").strip()
        findings = content[:800]
        tool, args = "fetch_web_content", {"url": url_match.group(0)}
        fallback = findings[:600]
        if not content:
            failure = f"Fetch failed: {result.get('error', 'no content')}"
    else:
        topic = _extract_topic(query)
        # Default: open the Google results in the browser while Merlin
        # summarizes them. Disable with MERLIN_OPEN_BROWSER_ON_SEARCH=0.
        if os.environ.get("MERLIN_OPEN_BROWSER_ON_SEARCH", "1") == "1":
            opened = bool(system_tools.search_web(topic).get("ok"))
        result = _web_search(topic)
        findings = _format_results(result)
        tool, args = "web_search", {"query": topic}
        fallback = _local_summary(result, es)
        if opened:
            findings += ("\n(Results were also opened in the browser.)" if not es
                         else "\n(Los resultados también se abrieron en el navegador.)")
        if not result.get("ok"):
            note = ((" Abrí la búsqueda en el navegador." if es
                      else " I opened the search in the browser.") if opened else "")
            failure = ((f"No pude obtener resultados ({result.get('error')})." if es
                        else f"I couldn't get results ({result.get('error')}).")
                       + note)

    updates["tool_calls"] = list(state.get("tool_calls") or []) + [
        {"tool": tool, "args": args}]
    updates["tool_results"] = list(state.get("tool_results") or []) + [result]

    if failure:
        updates["final_response"] = failure
    else:
        # Short synthesis -> fast model, streamed to the GUI
        updates["final_response"] = get_llm("fast").chat(
            [{"role": "system", "content": _SYSTEM_PROMPT["es" if es else "en"]},
             {"role": "user", "content": f"Request: {query}\nFindings:\n{findings}"}],
            fallback=fallback,
            on_token=lambda partial: post_event("merlin_partial", partial))
    return updates


def _summarize_papers(result: dict) -> str:
    if not result.get("ok"):
        return f"ArXiv search failed: {result.get('error')}"
    papers = result.get("papers") or []
    if not papers:
        return "No papers found for that query."
    lines = [f"- {p['title']}: {p['summary']}... ({p['link']})" for p in papers]
    return "\n".join(lines)