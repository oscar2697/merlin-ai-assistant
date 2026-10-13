"""OS_Agent_Node: near-total laptop control (apps, terminal, files, media, WhatsApp).

The agent plans a tool call with Gemma (structured JSON); when the LLM is
offline it falls back to deterministic keyword heuristics so the graph never
dead-ends. Obvious commands ("open X and play", "how much RAM", "search X") are
planned with keywords and answered locally, so they need zero LLM calls.

Sending a WhatsApp message asks for a spoken yes/no first (MERLIN_CONFIRM_SEND=0
disables it): voice transcription is never 100% reliable and a message to the
wrong person cannot be undone.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import re
import time

from merlin import confirm
from merlin.events import post_event
from merlin.langdetect import detect_language
from merlin.llm import get_llm
from merlin.state import GraphState, append_to
from merlin.tools import (linkedin_tools, system_tools, whatsapp_tools,
                          writing_tools)

logger = logging.getLogger("merlin.agents.os_agent")

# LLM profile that writes the text pasted into documents. "fast" is the profile
# already used below for short answers; set to None to use the default model.
_WRITER_PROFILE = "fast"

_WRITER_SYSTEM = (
    "You write text that will be pasted straight into a document. Reply in the "
    "same language as the request. Output ONLY the requested text: no title, no "
    "preface, no markdown, no quotes, no closing remarks.")


def _generate_text(instruction: str) -> str:
    llm = get_llm(_WRITER_PROFILE) if _WRITER_PROFILE else get_llm()
    text = (llm.chat(
        [{"role": "system", "content": _WRITER_SYSTEM},
         {"role": "user", "content": instruction}],
        fallback="") or "").strip()
    text = re.sub(r"\*\*|__|`", "", text)                # stray markdown
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    return text.strip()


def _write_text(app_name: str = "", instruction: str = "", text: str = "") -> dict:
    """Write `text` (or text generated for `instruction`) into an app window."""
    text = (text or "").strip()
    if not text:
        if not (instruction or "").strip():
            return {"ok": False, "error": "Nothing to write: no text or instruction."}
        text = _generate_text(instruction)
        if not text:
            return {"ok": False,
                    "error": "I could not generate the text (is the LLM running?)."}
    return writing_tools.type_into_app(app_name, text)


_OS_TOOLS = {
    "control_system_resource": system_tools.control_system_resource,
    "execute_terminal_command": system_tools.execute_terminal_command,
    "open_app": system_tools.open_app,
    "interact_gui_app": system_tools.interact_gui_app,
    "manage_files": system_tools.manage_files,
    "download_file": system_tools.download_file,
    "take_screenshot": system_tools.take_screenshot,
    "control_media_player": system_tools.control_media_player,
    "send_whatsapp_message": whatsapp_tools.send_whatsapp_message,
    "send_whatsapp_media": whatsapp_tools.send_whatsapp_media,
    "call_whatsapp_contact": whatsapp_tools.call_whatsapp_contact,
    "post_linkedin": linkedin_tools.post_linkedin,
    "search_web": system_tools.search_web,
    "write_text": _write_text,
}

# Tools that need a spoken yes/no before running (irreversible/interruptive).
_CONFIRM_TOOLS = {"send_whatsapp_message", "send_whatsapp_media",
                  "call_whatsapp_contact"}

# Tools that stage work on screen and confirm AFTER (draft visible to review).
_STAGE_CONFIRM_TOOLS = {"post_linkedin"}

# Only these tools need the LLM to explain a successful result (their output is
# free-form). Everything else is answered with a local one-liner.
_LLM_EXPLAIN_TOOLS = {"execute_terminal_command", "manage_files"}

# Seconds to wait after opening an app before running the next chained step.
_APP_LAUNCH_WAIT = 4.0

# Names small models often use instead of the real parameter names.
_ARG_ALIASES = {"app": "app_name", "application": "app_name", "name": "app_name",
                "cmd": "command", "path": "source",
                "track": "track_name", "song": "track_name",
                "topic": "instruction", "prompt": "instruction"}

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

# "escribe/describe un párrafo sobre X (en Word)" -> write_text
_WRITE_VERB_RE = re.compile(r"\b(?:escrib\w+|redact\w+|describ\w+|write|draft|compose)\b")
_WRITE_NOUN_RE = re.compile(
    r"\b(?:p[aá]rrafo|paragraph|texto|text|resumen|summary|ensayo|essay|carta|letter|"
    r"poema|poem|correo|email|art[ií]culo|article|historia|story)\b")
# (what the user says, text expected in the window title)
_WRITE_APPS = (("wordpad", "WordPad"), ("word", "Word"),
               ("bloc de notas", "Bloc de notas"), ("notepad", "Notepad"),
               ("onenote", "OneNote"), ("outlook", "Outlook"),
               ("excel", "Excel"), ("powerpoint", "PowerPoint"))

# WhatsApp: "dile a mamá que ya voy", "envía un mensaje por WhatsApp a Juan diciendo hola"
_WA_PATTERNS = (
    re.compile(r"\b(?:dile|d[ií]gale|escr[ií]bele|m[aá]ndale|env[ií]ale)\s+(?:a\s+)?"
               r"(?P<contact>.+?)\s+(?:que|diciendo|preguntando|:)\s*(?P<message>.+)$",
               re.IGNORECASE),
    re.compile(r"\b(?:env[ií]a\w*|manda\w*|escrib\w+)\s+(?:un\s+|el\s+)?(?:mensaje|whatsapp)"
               r"(?:\s+(?:de|por|en)\s+whatsapp)?\s+(?:a|para)\s+(?P<contact>.+?)\s+"
               r"(?:diciendo|preguntando|que(?:\s+(?:diga|dice))?|:)\s*(?P<message>.+)$",
               re.IGNORECASE),
    re.compile(r"\bsend\s+(?:a\s+)?(?:whatsapp\s+)?(?:message\s+)?(?:on\s+whatsapp\s+)?to\s+"
               r"(?P<contact>.+?)\s+(?:saying|that\s+says|asking|:)\s*(?P<message>.+)$",
               re.IGNORECASE),
)

# WhatsApp calls: "llama a mamá", "llámale a papá", "hazle una videollamada a liz",
# "call mom on whatsapp". Accented forms ("llámale") are matched with ll[aá]m.
_WA_CALL_VERB_RE = re.compile(
    r"\b(?:ll[aá]m\w+|marcar?|marc[aá]le|call|ring|videollam\w+)\b", re.IGNORECASE)
_WA_CALL_PATTERNS = (
    re.compile(r"\b(?:videollam(?:ada|ar|a)|video\s*call|video\s*llam(?:ada|ar))\b"
               r".*?\b(?:a|to)\s+(?P<contact>.+?)"
               r"(?:\s+(?:por|de|en|on|via)\s+whatsapp)?\s*$", re.IGNORECASE),
    re.compile(r"\b(?:ll[aá]m(?:a|ar|ame|ale|ala|alo|ela)|marcar|marcale|call|ring)\b"
               r"(?:\s+(?:a\s+)?(?:mamá|papá|un|una|le|la|lo))?"
               r"(?:\s+(?:por|de|en|on|via)\s+whatsapp)?\s*"
               r"(?:\s+a\s+|\s+to\s+)(?P<contact>.+?)\s*$", re.IGNORECASE),
    re.compile(r"\b(?:haz(?:le)?|make|realiza)\s+(?:una?\s+)?"
               r"(?P<vcall>videollamada|llamada|call)"
               r".*?\b(?:a|to)\s+(?P<contact>.+?)"
               r"(?:\s+(?:por|de|en|on|via)\s+whatsapp)?\s*$", re.IGNORECASE),
    re.compile(r"\b(?:call|ring|phone)\s+(?P<contact>.+?)"
               r"(?:\s+(?:por|de|en|on|via)\s+whatsapp)?\s*$", re.IGNORECASE),
)

_PLANNER_PROMPT = (
    "You are the action planner of MERLIN's OS agent. Choose ONE tool to "
    "satisfy the user request and return STRICT JSON, no prose, no markdown. "
    "Put the arguments at the top level next to \"tool\", exactly as named below. "
    "To write or type text into an app ALWAYS use write_text, never interact_gui_app.\n"
    "Available tools and required args:\n"
    '- control_system_resource: {{"tool": "control_system_resource", "action": "ram"|"cpu"|"battery"|"processes"}}\n'
    '- execute_terminal_command: {{"tool": "execute_terminal_command", "command": str, "timeout_seconds": int}}\n'
    '- open_app: {{"tool": "open_app", "app_name": str}}\n'
    '- interact_gui_app: {{"tool": "interact_gui_app", "app_name": str, "action": "focus"|"type"|"hotkey", "payload": str}}\n'
    '- manage_files: {{"tool": "manage_files", "action": "list"|"move"|"copy"|"rename"|"delete", "source": str, "destination": str}}\n'
    '- download_file: {{"tool": "download_file", "url": str, "destination_folder": str}}\n'
    '- take_screenshot: {{"tool": "take_screenshot", "target": str ("screen" or a window name e.g. "chrome")}}\n'
    '- control_media_player: {{"tool": "control_media_player", "action": "play"|"pause"|"next"|"previous"|"play_track"|"add_to_library", "media_path": str, "shuffle": bool, "track_name": str (song/artist/album for play_track), "search_field": "all"|"song"|"artist"|"album"}}\n'
    '- send_whatsapp_message: {{"tool": "send_whatsapp_message", "contact": str, "message": str}}\n'
    '- send_whatsapp_media: {{"tool": "send_whatsapp_media", "contact": str, "image_path": str ("last" = latest screenshot)}}\n'
    '- call_whatsapp_contact: {{"tool": "call_whatsapp_contact", "contact": str, "video": bool (true = video call, false = voice call)}}\n'
    '- post_linkedin: {{"tool": "post_linkedin", "instruction": str (what the post is about), "image_path": str (image to attach, "last" = latest screenshot)}}\n'
    '- search_web: {{"tool": "search_web", "query": str}}\n'
    '- write_text: {{"tool": "write_text", "app_name": str (window to write in, e.g. "Word"; "" = the window the user was in), "instruction": str (what to write, e.g. "a paragraph about computer networks"), "text": str (exact text, optional)}}\n'
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
    # WhatsApp tools: the model may call the recipient "name", "person", "to"...
    if plan.get("tool") in ("call_whatsapp_contact", "send_whatsapp_message") \
            and "contact" not in plan:
        for key in ("app_name", "person", "to", "recipient", "contact_name"):
            if key in plan:
                plan["contact"] = plan.pop(key)
                break
    return plan


def _plan_with_llm(query: str) -> dict | None:
    answer = get_llm("fast").chat(          
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


def _whatsapp_plan(query: str) -> dict | None:
    """'dile a mamá que ya voy' -> send_whatsapp_message(contact='mamá', message='ya voy')."""
    q = query.strip()
    for pattern in _WA_PATTERNS:
        m = pattern.search(q)
        if not m:
            continue
        contact = re.sub(r"\s+(?:por|en|de|on|via)\s+whatsapp\b.*$", "",
                         m.group("contact"), flags=re.IGNORECASE)
        contact = re.sub(r"^(?:mi|mis|el|la|my)\s+", "",
                         contact.strip(" ,.:;\"'"), flags=re.IGNORECASE).strip()
        message = m.group("message").strip(" \"'«»").rstrip(" .")
        if contact and message:
            return {"tool": "send_whatsapp_message", "contact": contact,
                    "message": message}
    return None


def _call_plan(query: str) -> dict | None:
    """'llama a mamá' / 'hazle una videollamada a liz' -> call_whatsapp_contact."""
    if not _WA_CALL_VERB_RE.search(query):
        return None
    video = bool(re.search(r"\bvideollam|video\s*call|video\s*llamada",
                           query, re.IGNORECASE))
    for pattern in _WA_CALL_PATTERNS:
        m = pattern.search(query.strip(" ?.,!¿¡"))
        if not m:
            continue
        contact = re.sub(r"\s+(?:por|de|en|on|via)\s+whatsapp\b.*$", "",
                          m.group("contact"), flags=re.IGNORECASE)
        # "llama a mamá y dile..." -> keep only the call; the trailing clause
        # (", and tell her...") is not part of the contact name
        contact = re.sub(r"\s+(?:y|and)\s+.*$", "", contact, flags=re.IGNORECASE)
        contact = re.sub(r"^(?:mi|mis|el|la|my)\s+", "",
                         contact.strip(" ,.:;\"'"), flags=re.IGNORECASE).strip()
        if contact:
            return {"tool": "call_whatsapp_contact", "contact": contact,
                    "video": video}
    return None


def _write_plan(query: str) -> dict | None:
    """'...puedes describir un párrafo sobre las redes' (+ Word) -> write_text."""
    q = query.lower()
    verb = _WRITE_VERB_RE.search(q)
    if not verb:
        return None
    app = ""
    for key, title in _WRITE_APPS:
        if re.search(rf"\b{re.escape(key)}\b", q):
            app = title
            break
    if not app and not _WRITE_NOUN_RE.search(q):
        return None
    instruction = query[verb.start():].strip(" ?.,!¿¡")
    return {"tool": "write_text", "app_name": app, "instruction": instruction}


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


def _linkedin_plan(q: str) -> dict | None:
    """'publica en linkedin...' / 'post ... on linkedin' -> post_linkedin."""
    if not re.search(r"\blinkedin\b", q, re.IGNORECASE):
        return None
    rest = re.sub(r"\blinkedin\b", " ", q, flags=re.IGNORECASE)
    rest = re.sub(r"^\s*(?:publica\w*|postea\w*|post|comparte\w*|pon|haz)\s+", "",
                  rest.strip(), flags=re.IGNORECASE)
    rest = re.sub(r"^(?:una?\s+)?(?:publicaci[oó]n|publicación|post)?\s*", "",
                  rest.strip(), flags=re.IGNORECASE)
    rest = re.sub(r"^(?:en|on|in)\s+", "", rest.strip(), flags=re.IGNORECASE)
    rest = re.sub(r"^(?:con|about|sobre)\s+", "", rest.strip(), flags=re.IGNORECASE)
    instruction = rest.strip(" ,.:;")
    if instruction.lower() in ("", "en", "in", "on", "esto", "it"):
        instruction = "comparte el avance de mi proyecto"
    plan = {"tool": "post_linkedin", "instruction": instruction}
    if re.search(r"\b(?:captura|screenshot|imagen|foto)\b", q, re.IGNORECASE):
        plan["image_path"] = "last"
    return plan


# "envía la captura a mamá (por whatsapp)" -> send the LATEST screenshot
_WA_MEDIA_RE = re.compile(
    r"\b(?:env[ií]a\w*|manda\w*|comparte\w*|send|share)\s+"
    r"(?:la\s+|el\s+|un[a]?\s+|this\s+)?"
    r"(?P<what>captura(?:\s+de\s+pantalla)?|screenshot|pantallazo|imagen|foto|image|screenshot)"
    r"\s+(?:a|para|al|to)\s+(?P<contact>.+?)"
    r"(?:\s+(?:por|de|en|via|on)\s+whatsapp)?\s*$",
    re.IGNORECASE)


def _whatsapp_media_plan(query: str) -> dict | None:
    m = _WA_MEDIA_RE.search(query.strip(" ?.,!¿¡"))
    if not m:
        return None
    contact = re.sub(r"^(?:mi|mis|el|la|my)\s+", "",
                     m.group("contact").strip(" ,.:;\"'"),
                     flags=re.IGNORECASE).strip()
    if not contact:
        return None
    return {"tool": "send_whatsapp_media", "contact": contact,
            "image_path": "last"}


# "envía (la captura) a <contacto> (por whatsapp)" WITHOUT repeating the media
# noun: only meaningful right after a screenshot step ("toma una captura y
# envíala a dome"); on its own it would be ambiguous with a text message.
_WA_SEND_TO_RE = re.compile(
    r"\b(?:env[ií]a\w*|m[aá]nd\w*|comparte\w*|pasa\w*|send|share)\s+"
    r"(?:la\s+|el\s+|un[a]?\s+|this\s+|eso\s+)?"
    r"(?:captura(?:\s+de\s+pantalla)?|screenshot|pantallazo|imagen|foto|image)?"
    r"\s*(?:a|para|al|to)\s+(?P<contact>.+?)"
    r"(?:\s+(?:por|de|en|via|on)\s+whatsapp)?\s*$",
    re.IGNORECASE)


def _followup_media_plan(clause: str, prev_plans: list) -> dict | None:
    """'...y envíala a dome' after a screenshot step -> send THAT screenshot."""
    if not prev_plans or prev_plans[-1].get("tool") != "take_screenshot":
        return None
    m = _WA_SEND_TO_RE.search(clause.strip(" ?.,!¿¡"))
    if not m:
        return None
    contact = re.sub(r"^(?:mi|mis|el|la|my)\s+", "",
                     m.group("contact").strip(" ,.:;\"'"),
                     flags=re.IGNORECASE).strip()
    if not contact:
        return None
    return {"tool": "send_whatsapp_media", "contact": contact,
            "image_path": "last"}


def _screenshot_plan(q: str) -> dict | None:
    """'toma una captura (de pantalla)' -> take_screenshot.

    Requires a capture VERB: 'envía la captura a mamá' is sending media,
    not taking a new screenshot."""
    takes = re.search(
        r"\b(?:toma\w*|tomar|haz|hacer|realiza|captura(?:r)?)\s+"
        r"(?:una?\s+|el\s+|la\s+)?(?:captura|screenshot|pantallazo|pantalla)\b",
        q, re.IGNORECASE)
    if not takes and not re.search(r"\bscreenshots?\b", q, re.IGNORECASE):
        return None
    target = "screen"
    m = re.search(r"\b(?:de|del|de la|de el)\s+(?:la\s+)?(?:ventana|window)\s+(.+?)\s*$",
                  q, re.IGNORECASE)
    if m and m.group(1).strip():
        target = m.group(1).strip(" ?.,!¿¡")
    return {"tool": "take_screenshot", "target": target}


def _plan_with_keywords(query: str) -> dict:
    q = query.lower()
    wa = _whatsapp_plan(query)
    if wa:
        return wa
    media = _whatsapp_media_plan(query)
    if media:
        return media
    call = _call_plan(query)
    if call:
        return call
    shot = _screenshot_plan(q)
    if shot:
        return shot
    linkedin = _linkedin_plan(q)
    if linkedin:
        return linkedin
    url = re.search(r"https?://\S+", query)
    if url and ("download" in q or "descarga" in q):
        return {"tool": "download_file", "url": url.group(0),
                "destination_folder": "Downloads"}
    writing = _write_plan(query)
    if writing:
        return writing
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
    if tool in ("control_system_resource", "search_web", "write_text",
                "send_whatsapp_message", "send_whatsapp_media",
                "call_whatsapp_contact", "take_screenshot", "post_linkedin"):
        return plan
    if tool == "control_media_player" and plan.get("action") != "add_to_library" \
            and (words <= 7 or (plan.get("action") == "play_track" and words <= 12)):
        return plan
    return None


def _fast_steps(query: str) -> list | None:
    """Split 'open X and play' into clauses; return plans only if EVERY clause is
    understood without the LLM (otherwise None -> the LLM plans the whole thing)."""
    q = query.lower()
    wa = _whatsapp_plan(query)  # the message may contain "y"/"and": don't split it
    if wa:
        return [wa]
    call = _call_plan(query)  # "llama a mamá y dile que..." stays one plan
    if call:
        return [call]
    linkedin = _linkedin_plan(q)  # the post instruction may contain "y"/"and"
    if linkedin and not _STEP_SPLIT.search(q):
        return [linkedin]  # "y"-chains fall through and split: capture + post
    if not re.search(r"\b(?:abre|abrir|open|launch)\b", q):
        writing = _write_plan(query)  # the text to write may contain "y"/"and"
        if writing:
            return [writing]
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
            plan = _followup_media_plan(clause, plans)
        if plan is None:
            return None
        plans.append(plan)
    return plans


def _extract_after(text: str, markers: list) -> str:
    for marker in markers:
        if marker in text:
            return text.split(marker, 1)[1].strip()[:200]
    return ""


def _confirm_send_enabled() -> bool:
    return os.environ.get("MERLIN_CONFIRM_SEND", "1") != "0"


def _ask_staged_confirmation(plan: dict, result: dict, language: str) -> str | None:
    """Staged actions (e.g. the LinkedIn draft on screen): ask to finalise."""
    es = str(language).lower().startswith("es")
    confirm.set_pending({"plan": {**plan, "publish": True}, "language": language})
    if plan.get("tool") == "post_linkedin":
        draft = (result.get("draft") or "").strip()
        short = draft if len(draft) <= 220 else draft[:217] + "..."
        if es:
            return (f"Preparé este post de LinkedIn, lo tienes en pantalla: "
                    f"\"{short}\". ¿Lo publico? Dime sí o no.")
        return (f"I staged this LinkedIn post, it's on screen: \"{short}\". "
                f"Shall I publish it? Say yes or no.")
    return None


def _ask_action_confirmation(plan: dict, language: str) -> str | None:
    """Store the action as pending and return the spoken question.
    Returns None when the tool should just run (it explains a missing contact).

    Covers WhatsApp messages and (video) calls - both are irreversible.

    The spoken contact name is kept in the plan: the in-app search must use
    whatever the user calls the contact (chats are listed by the user's names).
    """
    es = str(language).lower().startswith("es")
    contact = plan.get("contact", "")
    found = whatsapp_tools.resolve_contact(contact)
    if found is None and not whatsapp_tools.search_enabled():
        return None  # the tool itself explains that the number is missing
    confirm.set_pending({"plan": plan, "language": language})
    if plan.get("tool") == "send_whatsapp_media":
        if es:
            return (f"Voy a enviar a {contact} la última captura por WhatsApp. "
                    f"¿La envío? Dime sí o no.")
        return (f"I'm about to send {contact} the latest screenshot on "
                f"WhatsApp. Shall I send it? Say yes or no.")
    if plan.get("tool") == "call_whatsapp_contact":
        kind = "videollamada" if plan.get("video") else "llamada"
        if es:
            return (f"Voy a hacerle una {kind} por WhatsApp a {contact}. "
                    f"¿Llamo? Dime sí o no.")
        kind = "video call" if plan.get("video") else "call"
        return (f"I'm about to make a WhatsApp {kind} to {contact}. "
                f"Shall I call? Say yes or no.")
    message = plan.get("message", "")
    if es:
        return (f"Voy a enviar a {contact} el mensaje: {message}. "
                f"¿Lo envío? Dime sí o no.")
    return (f"I'm about to send {contact} the message: {message}. "
            f"Shall I send it? Say yes or no.")


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

    # A pending yes/no (WhatsApp send confirmation) takes priority over any
    # new plan; anything else the user says cancels the pending action.
    steps = None
    confirmed = False
    pending = confirm.peek_pending()
    if pending:
        reply = confirm.classify_reply(query)
        confirm.clear_pending()  # any other request replaces the pending action
        if reply:
            language = pending.get("language") or language
        if reply == "yes":
            steps = [pending["plan"]]
            confirmed = True
        elif reply == "no":
            es = str(language).lower().startswith("es")
            updates["final_response"] = ("De acuerdo, lo cancelé." if es
                                         else "Okay, I cancelled it.")
            return updates

    if steps is None:
        steps = _fast_steps(query)
        if steps is None:
            steps = [_plan_with_llm(query) or _plan_with_keywords(query)]

    if steps[0].get("tool", "none") == "none":
        updates["final_response"] = (
            "I could not map that request to a system action. "
            "Try naming the app, resource or command explicitly.")
        return updates

    calls, results, summaries = [], [], []
    pending_ask = None
    for i, plan in enumerate(steps):
        if i and calls[-1]["tool"] == "open_app" and results[-1].get("ok"):
            time.sleep(_APP_LAUNCH_WAIT)  # let the app finish opening
        # "toma una captura y publícala/envíala": attach the fresh screenshot
        if (i and calls[-1]["tool"] == "take_screenshot"
                and plan.get("tool") in ("post_linkedin", "send_whatsapp_media")
                and not plan.get("image_path")):
            plan["image_path"] = "last"
        # Irreversible step (message / media / call): confirm BEFORE running it.
        # Earlier steps in the chain (e.g. the screenshot) already ran, so the
        # confirmation references fresh data.
        if (not confirmed and _confirm_send_enabled()
                and plan.get("tool") in _CONFIRM_TOOLS):
            pending_ask = _ask_action_confirmation(plan, language)
            if pending_ask:
                break  # ask the user, and stop here
        tool_name, args, result = _run_step(plan)
        logger.info("OS step: %s %s -> %s", tool_name, args,
                    {k: v for k, v in result.items()
                     if k in ("ok", "method", "error", "window", "chars", "sent",
                              "clicked", "cursor")})
        calls.append({"tool": tool_name, "args": args})
        results.append(result)
        summaries.append(_summarize(tool_name, result, language))
        if not result.get("ok"):
            break  # don't run later steps after a failure

    updates["tool_calls"] = list(state.get("tool_calls") or []) + calls
    updates["tool_results"] = list(state.get("tool_results") or []) + results

    if pending_ask:
        updates["final_response"] = " ".join(summaries + [pending_ask]).strip()
        return updates

    # Staged tools (LinkedIn): the draft is ON SCREEN -> ask sí/no to publish.
    last_tool, last_result = calls[-1]["tool"], results[-1]
    if (not confirmed and last_result.get("needs_confirmation")
            and last_tool in _STAGE_CONFIRM_TOOLS):
        ask = _ask_staged_confirmation(steps[-1], last_result, language)
        if ask:
            updates["final_response"] = ask
            return updates

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
    if tool == "write_text":
        where = result.get("app") or result.get("window") or ""
        if es:
            return f"Listo, escribí el texto en {where}." if where else "Listo, escribí el texto."
        return f"Done, I wrote the text in {where}." if where else "Done, I wrote the text."
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
    if tool == "send_whatsapp_media":
        who = result.get("contact")
        sent = result.get("sent", True)
        if result.get("ok"):
            if es:
                return (f"Captura enviada a {who}." if sent else
                        f"Dejé la captura lista para {who}; solo falta enviarla.")
            return (f"Screenshot sent to {who}." if sent else
                    f"I left the screenshot ready for {who}; it is ready to send.")
        return f"No pude enviar la captura: {result.get('error', 'error')}"
    if tool == "send_whatsapp_message":
        who = result.get("contact")
        sent = result.get("sent", True)
        if es:
            return (f"Mensaje enviado a {who}." if sent else
                    f"Dejé el mensaje escrito para {who}; solo falta enviarlo.")
        return (f"Message sent to {who}." if sent else
                f"I left the message typed for {who}; it is ready to send.")
    if tool == "call_whatsapp_contact":
        who = result.get("contact")
        kind = "videollamada" if result.get("video") else "llamada"
        if result.get("ok"):
            if result.get("clicked") is False:  # dry run: nothing was dialled
                return ("Modo de prueba: puse el cursor sobre el botón de "
                        "llamada, sin llamar." if es else
                        "Test mode: I placed the cursor on the call button "
                        "without calling.")
            if es:
                return f"Haciéndole una {kind} por WhatsApp a {who}."
            kind = "video call" if result.get("video") else "call"
            return f"Making a WhatsApp {kind} to {who}."
        if es:
            return f"No pude iniciar la {kind} a {who}: {result.get('error', 'error')}"
        return f"Could not start the call to {who}: {result.get('error', 'error')}"
    if tool == "post_linkedin":
        if result.get("published"):
            return ("¡Publicado en LinkedIn!" if es else "Published on LinkedIn!")
        if result.get("staged"):
            return ("Tengo el post de LinkedIn listo en pantalla." if es
                    else "The LinkedIn post is staged on screen.")
        if result.get("need_login"):
            return ("Abre la ventana del navegador e inicia sesión en LinkedIn "
                    "(solo esta vez)." if es else
                    "Log in to LinkedIn in the opened browser window (one time only).")
        err = result.get("error", "")
        return f"No pude preparar el post de LinkedIn: {err}" if es else \
            f"Could not prepare the LinkedIn post: {err}"
    if tool == "take_screenshot":
        where = ("" if result.get("target") in ("screen", "pantalla", "full")
                 else f" de {result.get('target')}")
        return (f"Captura{where} guardada en {result.get('path')}." if es else
                f"Screenshot{where and ' of ' + result.get('target', '')} saved to "
                f"{result.get('path')}.")
    if tool == "download_file":
        return (f"Downloaded to {result.get('path')} "
                f"({result.get('size_mb')} MB).")
    return f"Done. {json.dumps(result, default=str)[:300]}"