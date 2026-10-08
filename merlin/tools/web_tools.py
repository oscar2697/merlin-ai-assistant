"""Web & research skills: ArXiv search + web content fetching."""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

import httpx

logger = logging.getLogger("merlin.tools.web")

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def search_arxiv_papers(query: str, max_results: int = 3) -> dict:
    """Query the ArXiv REST API for research papers."""
    try:
        resp = httpx.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": f"all:{query}", "start": 0,
                    "max_results": max_results},
            timeout=30, follow_redirects=True,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        papers = []
        for entry in root.findall(f"{_ATOM_NS}entry"):
            papers.append({
                "title": " ".join((entry.findtext(f"{_ATOM_NS}title") or "").split()),
                "summary": ((entry.findtext(f"{_ATOM_NS}summary") or "")
                            .strip())[:300],
                "link": entry.findtext(f"{_ATOM_NS}id"),
            })
        return {"ok": True, "query": query, "papers": papers}
    except Exception as exc:
        logger.exception("search_arxiv_papers failed")
        return {"ok": False, "error": str(exc)}


def fetch_web_content(url_or_query: str) -> dict:
    """Fetch a URL's text, or try DuckDuckGo instant answers for a query."""
    target = (url_or_query or "").strip()
    try:
        if re.match(r"^https?://", target):
            resp = httpx.get(target, follow_redirects=True, timeout=30,
                             headers={"User-Agent": "Mozilla/5.0 (MERLIN)"})
            resp.raise_for_status()
            text = re.sub(r"<[^>]+>", " ", resp.text)
            text = re.sub(r"\s+", " ", text).strip()
            return {"ok": True, "url": target, "content": text[:2500]}
        resp = httpx.get("https://api.duckduckgo.com/",
                         params={"q": target, "format": "json", "no_html": 1},
                         timeout=20)
        data = resp.json()
        answer = (data.get("AbstractText")
                  or (data.get("Answer") if isinstance(data.get("Answer"), str) else "")
                  or "")
        return {"ok": bool(answer), "query": target,
                "content": answer or "No instant answer found; full web search arrives with the Tavily integration."}
    except Exception as exc:
        logger.exception("fetch_web_content failed")
        return {"ok": False, "error": str(exc)}
