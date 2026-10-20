"""Optional boot-time prompt refinement from execution logs.

Runs once per process (guarded), reads recent execution logs, asks the
configured local model to analyze failures/slow calls, and rewrites the
guidelines section of system_prompt.txt. MerlinRuntime starts it outside the
graph and only when MERLIN_SELF_IMPROVEMENT=1.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from merlin.config import ensure_dirs, get_config
from merlin.events import post_event
from merlin.llm import get_llm
from merlin.persistence import load_execution_logs
from merlin.state import GraphState

logger = logging.getLogger("merlin.agents.self_improvement")

_ALREADY_RAN = False
_ANALYSIS_PROMPT = (
    "You are MERLIN's self-improvement module. Below are recent execution log "
    "entries (failures marked in 'errors', durations in ms). Write 3-5 short, "
    "actionable guidelines to improve future behaviour. Plain bullet lines "
    "starting with '- ', in English, no preamble.\n\n"
    "Log entries:\n{logs}\nGuidelines:"
)


def self_improvement_node(state: GraphState) -> dict:
    global _ALREADY_RAN
    updates: dict = {"active_agent": "self_improvement"}
    if _ALREADY_RAN:
        return updates  # graph entry runs on every turn; analyse only at boot
    _ALREADY_RAN = True

    post_event("status", "self_improvement")
    ensure_dirs()
    config = get_config()
    logs = load_execution_logs()[-30:]

    issues = []
    for entry in logs:
        if entry.get("errors"):
            issues.append(f"- failure in {entry.get('agent')}: "
                          f"{'; '.join(entry['errors'])[:200]}")
        if entry.get("duration_ms", 0) > 60000:
            issues.append(f"- slow turn ({entry['duration_ms']} ms) in "
                          f"{entry.get('agent')}")
    issues = issues[-15:]

    try:
        if issues:
            guidelines = get_llm().chat(
                [{"role": "user",
                  "content": _ANALYSIS_PROMPT.format(logs="\n".join(issues))}],
                fallback="- Retry failed tools once before reporting errors.",
            ).strip()
            guidelines = "\n".join(
                line for line in guidelines.splitlines()
                if line.strip().startswith(("-", "*"))
            ) or "- Retry failed tools once before reporting errors."
        else:
            guidelines = "- No failures recorded yet. Default behaviour applies."

        _write_system_prompt(config, guidelines)
        logger.info("Self-improvement updated %s (%d issues analysed)",
                    config.system_prompt_file, len(issues))
        post_event("info", "Self-improvement: system prompt refreshed "
                            f"({len(issues)} issues analysed).")
    except Exception as exc:
        logger.exception("Self-improvement failed")
        updates["error_logs"] = list(state.get("error_logs") or []) + [
            f"self_improvement failed: {exc}"]
    return updates


def _write_system_prompt(config, guidelines: str) -> None:
    base = _BASE_PROMPT_TEMPLATE.format(date=datetime.now(timezone.utc).date())
    config.system_prompt_file.write_text(base + guidelines + "\n",
                                         encoding="utf-8")


_BASE_PROMPT_TEMPLATE = """You are MERLIN, a local, voice-activated AI assistant
and operating-system controller running fully offline on the user's laptop.
You have near-total control of the machine: you may open apps, run terminal
commands, manage files, download content, control media, and send messages on
spoken request.

Core rules:
- Answer in the SAME language the user speaks (Spanish or English).
- Keep spoken answers short and conversational (2-4 sentences max).
- Do not repeat a claim or paragraph. Finish the response clearly and do not
  restart from the beginning after already giving part of the answer.
- Report tool results clearly (e.g. RAM usage, file paths, command output).
- If a tool fails, apologize briefly, state what failed, and suggest the
  next step instead of dumping raw errors.
- Only irreversible actions (disk formatting, recursive deletion, firmware
  writes) are forbidden; everything else is allowed.

## Self-Improvement Guidelines (rewritten automatically at boot, {date})

"""
