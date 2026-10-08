"""Web_Agent_Node: ArXiv research + web content fetching + synthesis."""
from __future__ import annotations

import logging
import re

from merlin.events import post_event
from merlin.langdetect import detect_language
from merlin.llm import get_llm
from merlin.state import GraphState
from merlin.tools import web_tools

logger = logging.getLogger("merlin.agents.web_agent")


def web_agent_node(state: GraphState) -> dict:
    post_event("status", "web_agent")
    query = state.get("input_query", "")
    updates: dict = {"active_agent": "web_agent",
                     "detected_language": state.get("detected_language")
                     or detect_language(query)}

    is_arxiv = bool(re.search(r"arxiv|papers?|artículos?|articulo", query, re.I))
    if is_arxiv:
        topic = re.sub(
            r"\b(?:arxiv|papers?|artículos?|articulo|about|sobre|search|searching"
            r"|busca|buscar|investiga|investigar|research|find|dame|los|las|el|la)\b",
            "", query, flags=re.I).strip(" :,.-")
        result = web_tools.search_arxiv_papers(topic or query, max_results=3)
        summary = _summarize_papers(result)
    else:
        url = re.search(r"https?://\S+", query)
        result = web_tools.fetch_web_content(url.group(0) if url else query)
        summary = (result.get("content") or
                   f"Fetch failed: {result.get('error', 'no content')}")[:600]

    updates["tool_calls"] = list(state.get("tool_calls") or []) + [
        {"tool": "search_arxiv_papers" if is_arxiv else "fetch_web_content",
         "args": {"query": query}}]
    updates["tool_results"] = list(state.get("tool_results") or []) + [result]

    updates["final_response"] = get_llm().chat(
        [{"role": "system",
          "content": "You are MERLIN's research agent. Summarize the findings "
                     "for the user in their language, max 4 sentences. "
                     "Cite titles/URLs when relevant."},
         {"role": "user", "content": f"Request: {query}\nFindings: {summary}"}],
        fallback=summary)
    return updates


def _summarize_papers(result: dict) -> str:
    if not result.get("ok"):
        return f"ArXiv search failed: {result.get('error')}"
    papers = result.get("papers") or []
    if not papers:
        return "No papers found for that query."
    lines = [f"- {p['title']}: {p['summary']}... ({p['link']})" for p in papers]
    return "\n".join(lines)
