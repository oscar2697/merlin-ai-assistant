"""OS_Agent_Node: near-total laptop control (apps, terminal, files, media, WhatsApp).

The agent plans a tool call with Gemma (structured JSON); when the LLM is
offline it falls back to deterministic keyword heuristics so the graph never
dead-ends. Obvious commands ("open X and play", "how much RAM", "search X") are
planned with keywords and answered locally, so they need zero LLM calls.
"""
from __future__ import annotations

import inspect
import json
import logging
import re
import time

from merlin.events import post_event
from merlin.langdetect import detect_language
from merlin.llm import get_llm
from merlin.state import GraphState, append_to
from merlin.tools import system_tools

logger = logging.getLogger("merlin.agents.os_agent")

_OS_TOOLS = {
    "control_system_resource": system_tools.control_system_resource,
    "execute_terminal_command": system_tools.execute_terminal_command,
    "open_app": system_tools.open_app,
    "interact_gui_app": system_tools.interact_gui_app,
    "manage_files": system_tools.manage_files,
    "download_file": system_tools.download_file,
    "control_media_player": system_tools.control_media_player,
    "send_whatsapp_message": system_tools.send_whatsapp_message,
    "search_web": system_tools.search_web,
}

# Only these tools need the LLM to explain a successful result (their output is
# free-form). Everything else is answered with a local one-liner.
_LLM_EXPLAIN_TOOLS = {"execute_terminal_command", "manage_files"}

# Seconds to wait after opening an app before running the next chained step.
_APP_LAUNCH_WAIT = 4.0

# Names small models often use instead of the real parameter names.
_ARG_ALIASES = {"app": "app_name", "application": "app_name", "name": "app_name",
                "cmd": "command", "path": "source",
                "track": "track_name", "song": "track_name"}

_STEP_SPLIT = re.compile(
    r"\s+(?:y luego|y después|y despues|and then|then|luego|después|despues|y|and)\s+")

# Verb forms: "reproduce", "reproduzca", "reproducir"... (Spanish subjunctive too)
_PLAY_RE = re.compile(r"\b(play|reprodu[cz]\w*|pon la|pon m[uú]sica|pon algo)\b")
_PAUSE_RE = re.compile(r"\b(pause|pausa\w*|det[eé]n\w*)\b")
_NEXT_RE = re.compile(r"\b(next|siguiente|skip|salta\w*)\b")
_PREV_RE = re.compile(r"\b(previous|anterior)\b")
_SHUFFLE_RE = re.compile(r"aleatori|shuffle|random|al azar")

# "play <something>": extract a song/artist/album name for play_track.
_PLAY_SPECIFIC_RE = re.compile(
    r"\b(?:reprodu[cz]\w*|play|ponme|pon|esc[uú]chame|escucha(?:r)?|listen\s+to|"
    r"quiero\s+escuchar|i\s+want\s+to\s+hear)\s+"
    r"(?:la\s+canci[oó]n\s+(?:de\s+|titulada\s+)?|canci[oó]n\s+|the\s+song\s+|song\s+|"
    r"al?\s+artista\s+(?:de\s+)?|artista\s+|el\s+[aá]lbum\s+(?:de\s+)?|[aá]lbum\s+|album\s+)?"
    r"(.+)",
    re.IGNORECASE)
# Generic names that mean "play anything", not a specific track.
_GENERIC_PLAY_RE = re.compile(
    r"^(?:m[uú]sica|music|algo|algo\s+de\s+m[uú]sica|canciones|songs|una\s+canci[oó]n)\b.*$",
    re.IGNORECASE)
_SEARCH_RE = re.compile(
    r"\b(?:search(?:\s+for)?|look\s+up|busca(?:r|me)?|investiga|googlea)\s+"
    r"(?:(?:informaci[oó]n|info|information)\s+(?:sobre|de|acerca\s+de|about|on)\s+)?"
    r"(.+?)"
    r"(?:\s+(?:en|in|on|con)\s+(?:google\s+chrome|google|chrome|internet|la\s+web|the\s+web))?\s*$")

_PLANNER_PROMPT = (
    "You are the action planner of MERLIN's OS agent. Choose ONE tool to "
    "satisfy the user request and return STRICT JSON, no prose, no markdown. "
    "Put the arguments at the top level next to \"tool\", exactly as named below.\n"
    "Available tools and required args:\n"
    '- control_system_resource: {{"tool": "control_system_resource", "action": "ram"|"cpu"|"battery"|"processes"}}\n'
    '- execute_terminal_command: {{"tool": "execute_terminal_command", "command": str, "timeout_seconds": int}}\n'
    '- open_app: {{"tool": "open_app", "app_name": str}}\n'
    '- interact_gui_app: {{"tool": "interact_gui_app", "app_name": str, "action": "focus"|"type"|"hotkey", "payload": str}}\n'
    '- manage_files: {{"tool": "manage_files", "action": "list"|"move"|"copy"|"rename"|"delete", "source": str, "destination": str}}\n'
    '- download_file: {{"tool": "download_file", "url": str, "destination_folder": str}}\n'
    '- control_media_player: {{"tool": "control_media_player", "action": "play"|"pause"|"next"|"previous"|"play_track"|"add_to_library", "media_path": str, "shuffle": bool, "track_name": str (song/artist/album for play_track), "search_field": "all"|"song"|"artist"|"album"}}\n'
    '- send_whatsapp_message: {{"tool": "send_whatsapp_message", "contact": str, "message": str}}\n'
    '- search_web: {{"tool": "search_web", "query": str}}\n'
    'If nothing fits, return {{"tool": "none"}}.\n\n'
    "User request: {query}\nJSON:"
)


def _normalize_plan(plan: dict) -> dict:
    """Flatten nested args and fix tool/argument names the model got slightly wrong."""
    plan = dict(plan)
    for key in ("args", "arguments", "parameters", "params"):
        nested = plan.pop(key, None)
        if isinstance(nested, dict):
            plan.update(nested)
    tool = plan.get("tool") or plan.pop("name", None) or plan.pop("tool_name", None)
    if isinstance(tool, str):
        plan["tool"] = tool.strip()
    for wrong, right in _ARG_ALIASES.items():
        if wrong in plan and right not in plan:
            plan[right] = plan.pop(wrong)
    return plan


def _plan_with_llm(query: str) -> dict | None:
    answer = get_llm().chat(
        [{"role": "user", "content": _PLANNER_PROMPT.format(query=query)}],
        fallback="",
    )
    match = re.search(r"\{.*\}", answer or "", re.DOTALL)
    if not match:
        return None
    try:
        plan = _normalize_plan(json.loads(match.group(0)))
        if isinstance(plan.get("tool"), str) and plan["tool"] in _OS_TOOLS:
            return plan
    except json.JSONDecodeError:
        pass
    return None


def _search_plan(q: str) -> dict | None:
    """'busca información sobre X en Chrome' -> search_web(X)."""
    m = _SEARCH_RE.search(q.strip(" ?.,!¿¡"))
    if not m:
        return None
    topic = m.group(1).strip(" ?.,!¿¡")
    return {"tool": "search_web", "query": topic} if topic else None


def _play_specific_plan(q: str) -> dict | None:
    """'reproduce Bohemian Rhapsody' / 'pon la canción X de Queen' -> play_track."""
    m = _PLAY_SPECIFIC_RE.search(q.strip(" ?.,!¿¡"))
    if not m:
        return None
    name = m.group(1).strip(" ?.,!¿¡\"'")
    name = re.sub(r"\s+(?:en|in|on)\s+(?:itunes|spotify|apple\s*music|la\s+app)"
                  r"\b.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+(?:por\s+favor|please|ahora|now)\b.*$", "", name,
                  flags=re.IGNORECASE)
    name = re.sub(r"^(?:a|al|the)\s+", "", name, flags=re.IGNORECASE)
    name = name.strip(" ?.,!¿¡\"'")
    if not name or _GENERIC_PLAY_RE.match(name):
        return None  # "pon música/algo" -> generic play
    prefix = m.group(0)[:m.start(1) - m.start(0)]  # category words before the name
    if re.search(r"\bcanci[oó]n|\bsongs?\b|\btracks?\b", prefix, re.IGNORECASE):
        field = "song"
    elif re.search(r"\bartistas?\b", prefix, re.IGNORECASE):
        field = "artist"
    elif re.search(r"\b[aá]lbum(?:es)?\b|\balbums?\b", prefix, re.IGNORECASE):
        field = "album"
    else:
        field = "all"
    return {"tool": "control_media_player", "action": "play_track",
            "track_name": name, "search_field": field}


def _plan_with_keywords(query: str) -> dict:
    q = query.lower()
    if "whatsapp" in q:
        contact = _extract_after(q, ["to ", "a ", "para "]) or "contact"
        message = _extract_after(q, ["saying ", "asking ", "que ", "preguntando "])
        if "message" in q or "mensaje" in q or message:
            return {"tool": "send_whatsapp_message",
                    "contact": contact.title(), "message": message or query}
    url = re.search(r"https?://\S+", query)
    if url and ("download" in q or "descarga" in q):
        return {"tool": "download_file", "url": url.group(0),
                "destination_folder": "Downloads"}
    search = _search_plan(q)
    if search:
        return search
    for resource in ("ram", "cpu", "battery", "batería", "bateria", "processes", "procesos"):
        if re.search(rf"\b{resource}\b", q):  # whole word: "ram" != "programa"
            action = "battery" if resource in ("batería", "bateria") else (
                "processes" if resource == "procesos" else resource)
            return {"tool": "control_system_resource", "action": action}
    open_match = re.search(
        r"(?:open|launch|start|abre|abrir|enciende)\s+(?:the\s+)?(?:app\s+)?(.+)", q)
    if open_match and "file" not in q and "archivo" not in q:
        app = open_match.group(1).strip().strip("?.,!¿¡ ")
        if app:
            return {"tool": "open_app", "app_name": app}
    if _NEXT_RE.search(q):
        return {"tool": "control_media_player", "action": "next"}
    if _PREV_RE.search(q):
        return {"tool": "control_media_player", "action": "previous"}
    if _PAUSE_RE.search(q):
        return {"tool": "control_media_player", "action": "pause"}
    specific = _play_specific_plan(q)
    if specific:
        return specific
    if _PLAY_RE.search(q):
        plan = {"tool": "control_media_player", "action": "play"}
        if _SHUFFLE_RE.search(q):
            plan["shuffle"] = True
        return plan
    return {"tool": "none"}


def _fast_clause_plan(clause: str) -> dict | None:
    """Keyword plan for ONE unambiguous clause, or None if the LLM should decide."""
    plan = _plan_with_keywords(clause)
    tool = plan.get("tool")
    words = len(clause.split())
    if tool == "open_app" and len(plan.get("app_name", "").split()) <= 3:
        return plan
    if tool in ("control_system_resource", "search_web"):
        return plan
    if tool == "control_media_player" and plan.get("action") != "add_to_library" \
            and (words <= 7 or (plan.get("action") == "play_track" and words <= 12)):
        return plan
    return None


def _fast_steps(query: str) -> list | None:
    """Split 'open X and play' into clauses; return plans only if EVERY clause is
    understood without the LLM (otherwise None -> the LLM plans the whole thing)."""
    q = query.lower()
    single = _search_plan(q)  # a search topic may contain "y"/"and": don't split it
    if single:
        return [single]
    clauses = [c.strip(" ?.,!¿¡") for c in _STEP_SPLIT.split(q) if c.strip()]
    if not clauses or len(clauses) > 4:
        return None
    plans = []
    for clause in clauses:
        plan = _fast_clause_plan(clause)
        if plan is None:
            return None
        plans.append(plan)
    return plans


def _extract_after(text: str, markers: list) -> str:
    for marker in markers:
        if marker in text:
            return text.split(marker, 1)[1].strip()[:200]
    return ""


def _run_step(plan: dict) -> tuple[str, dict, dict]:
    """Execute one planned tool call -> (tool_name, args, result)."""
    tool_name = plan.get("tool", "none")
    # Keep only the arguments the tool really accepts; log what was dropped.
    allowed = set(inspect.signature(_OS_TOOLS[tool_name]).parameters)
    args = {k: v for k, v in plan.items() if k != "tool" and k in allowed}
    dropped = [k for k in plan if k != "tool" and k not in allowed]
    if dropped:
        logger.warning("Dropped unknown args for %s: %s", tool_name, dropped)
    try:
        result = _OS_TOOLS[tool_name](**args)
    except TypeError as exc:
        logger.exception("Bad arguments for %s: %s", tool_name, args)
        result = {"ok": False, "error": f"Bad arguments for {tool_name}: {exc}"}
    except Exception as exc:  # never crash the graph
        logger.exception("OS tool crashed")
        result = {"ok": False, "error": str(exc)}
    return tool_name, args, result


def os_agent_node(state: GraphState) -> dict:
    post_event("status", "os_agent")
    query = state.get("input_query", "")
    updates: dict = {"active_agent": "os_agent",
                     "detected_language": state.get("detected_language")
                     or detect_language(query)}
    language = updates["detected_language"] or "en"

    steps = _fast_steps(query)
    if steps is None:
        steps = [_plan_with_llm(query) or _plan_with_keywords(query)]

    if steps[0].get("tool", "none") == "none":
        updates["final_response"] = (
            "I could not map that request to a system action. "
            "Try naming the app, resource or command explicitly.")
        return updates

    calls, results, summaries = [], [], []
    for i, plan in enumerate(steps):
        if i and calls[-1]["tool"] == "open_app" and results[-1].get("ok"):
            time.sleep(_APP_LAUNCH_WAIT)  # let the app finish opening
        tool_name, args, result = _run_step(plan)
        logger.info("OS step: %s %s -> %s", tool_name, args,
                    {k: v for k, v in result.items() if k in ("ok", "method", "error")})
        calls.append({"tool": tool_name, "args": args})
        results.append(result)
        summaries.append(_summarize(tool_name, result, language))
        if not result.get("ok"):
            break  # don't run later steps after a failure

    updates["tool_calls"] = list(state.get("tool_calls") or []) + calls
    updates["tool_results"] = list(state.get("tool_results") or []) + results

    summary = " ".join(summaries)
    last_tool, last_result = calls[-1]["tool"], results[-1]
    if len(calls) == 1 and last_result.get("ok") and last_tool in _LLM_EXPLAIN_TOOLS:
        messages = [
            {"role": "system",
             "content": "You are MERLIN's OS agent. Explain the tool result to the "
                         "user in their language, briefly (max 3 sentences)."},
            {"role": "user",
             "content": f"Request: {query}\nTool: {last_tool}\nResult: {last_result}"},
        ]
        # Short explanation -> fast model, streamed to the GUI
        updates["final_response"] = get_llm("fast").chat(
            messages, fallback=summary,
            on_token=lambda partial: post_event("merlin_partial", partial))
    else:
        updates["final_response"] = summary  # no LLM call needed
    return updates


def _summarize(tool: str, result: dict, language: str) -> str:
    es = str(language).lower().startswith("es")
    if not result.get("ok"):
        err = result.get("error", "unknown error")
        return f"La acción falló: {err}" if es else f"Action failed: {err}"
    data = result.get("data", {})
    if tool == "control_system_resource":
        action = result.get("action")
        if action == "ram":
            return (f"RAM: {data.get('used_gb')} de {data.get('total_gb')} GB en uso "
                    f"({data.get('percent')}%)." if es else
                    f"RAM: {data.get('used_gb')} of {data.get('total_gb')} GB in use "
                    f"({data.get('percent')}%).")
        if action == "cpu":
            return (f"CPU al {data.get('percent')}% en {data.get('cores')} núcleos."
                    if es else
                    f"CPU at {data.get('percent')}% across {data.get('cores')} cores.")
        if action == "battery":
            plugged = data.get("plugged")
            if es:
                return (f"Batería al {data.get('percent')}%, "
                        f"{'cargando' if plugged else 'sin cargador'}.")
            return (f"Battery at {data.get('percent')}%, "
                    f"{'plugged in' if plugged else 'unplugged'}.")
        if action == "processes":
            top = ", ".join(p["name"] for p in data.get("top", [])[:3])
            return (f"Procesos que más memoria usan: {top}." if es
                    else f"Top memory users: {top}.")
    if tool == "open_app":
        app = result.get("app")
        return f"Abriendo {app}." if es else f"Opening {app}."
    if tool == "search_web":
        q = result.get("query")
        return (f"Abrí la búsqueda de «{q}» en Chrome." if es
                else f"Opened a search for \"{q}\" in Chrome.")
    if tool == "control_media_player":
        action = result.get("action")
        if action == "play_track":
            if result.get("ok"):
                return (f'Reproduciendo "{result.get("track")}" de '
                        f'{result.get("artist")}.' if es else
                        f'Playing "{result.get("track")}" by '
                        f'{result.get("artist")}.')
            return (f'No encontré "{result.get("track_name")}" en tu librería '
                    f'de iTunes.' if es else
                    f'Couldn\'t find "{result.get("track_name")}" in your iTunes '
                    f'library.')
        if action == "play" and result.get("shuffle"):
            return "Reproduciendo en modo aleatorio." if es else "Playing on shuffle."
        return f"Listo: {action}." if es else f"Done: {action}."
    if tool == "send_whatsapp_message":
        return (f"WhatsApp message sent to {result.get('contact')}: "
                f"\"{result.get('message')}\"")
    if tool == "download_file":
        return (f"Downloaded to {result.get('path')} "
                f"({result.get('size_mb')} MB).")
    return f"Done. {json.dumps(result, default=str)[:300]}"