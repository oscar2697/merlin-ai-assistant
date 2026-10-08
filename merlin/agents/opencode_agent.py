"""OpenCode_Agent_Node: dictation + prompt injection into coding sessions."""
from __future__ import annotations

import logging

from merlin.events import post_event
from merlin.langdetect import detect_language
from merlin.llm import get_llm
from merlin.state import GraphState
from merlin.tools import opencode_tools

logger = logging.getLogger("merlin.agents.opencode")

_REWRITE_PROMPT = (
    "You prepare development prompts for OpenCode. Convert the user's spoken "
    "request into a concise, actionable development prompt (bullet list of "
    "concrete tasks, no preamble). Keep the user's language.\n\n"
    "User request: {query}\nPrompt:"
)


def opencode_agent_node(state: GraphState) -> dict:
    post_event("status", "opencode_agent")
    query = state.get("input_query", "")
    updates: dict = {"active_agent": "opencode_agent",
                     "detected_language": state.get("detected_language")
                     or detect_language(query)}

    prompt_content = get_llm().chat(
        [{"role": "user", "content": _REWRITE_PROMPT.format(query=query)}],
        fallback=query,
    ).strip() or query

    import os
    project_path = os.environ.get("MERLIN_WORKSPACE", ".")

    result = opencode_tools.inject_opencode_prompt(
        project_path=project_path, prompt_content=prompt_content, launch=True)

    updates["tool_calls"] = list(state.get("tool_calls") or []) + [
        {"tool": "inject_opencode_prompt",
         "args": {"project_path": project_path}}]
    updates["tool_results"] = list(state.get("tool_results") or []) + [result]

    if result.get("ok"):
        summary = (f"Development prompt written to {result.get('prompt_file')}"
                   + (" and OpenCode launched." if result.get("launched") else "."))
    else:
        summary = f"Could not inject the prompt: {result.get('error')}"
    updates["final_response"] = get_llm().chat(
        [{"role": "system",
          "content": "You are MERLIN's OpenCode agent. Confirm to the user, "
                     "in their language, what was done. Max 2 sentences."},
         {"role": "user", "content": f"Request: {query}\nResult: {result}"}],
        fallback=summary)
    return updates
