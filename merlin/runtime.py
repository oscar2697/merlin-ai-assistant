"""MerlinRuntime: wraps the compiled graph, runs turns in background threads."""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from merlin.agents.self_improvement import self_improvement_node
from merlin.agents.tts_responder import mark_turn_start
from merlin.config import ensure_dirs, get_config
from merlin.events import post_event
from merlin.graph import build_graph
from merlin.langdetect import detect_language
from merlin.persistence import load_recent_chat
from merlin.state import GraphState

logger = logging.getLogger("merlin.runtime")


class MerlinRuntime:
    def __init__(self) -> None:
        ensure_dirs()
        self.graph = build_graph()
        self._busy = threading.Lock()
        logger.info("MERLIN graph compiled")
        # Off by default: with a slow local model the rewrite costs minutes of
        # LM Studio time and delays the first user turn. Opt in with
        # MERLIN_SELF_IMPROVEMENT=1.
        if os.environ.get("MERLIN_SELF_IMPROVEMENT", "0") == "1":
            threading.Thread(target=self._boot_self_improvement, daemon=True,
                             name="merlin-self-improvement").start()

    def _boot_self_improvement(self) -> None:
        try:
            self_improvement_node({})
        except Exception:
            logger.exception("Boot self-improvement crashed")

    def is_busy(self) -> bool:
        return self._busy.locked()

    def _initial_state(self, query: str, voice_mode: bool) -> GraphState:
        config = get_config()
        return {
            "input_query": query,
            "voice_mode": voice_mode,
            "chat_history": load_recent_chat(config.max_chat_history_messages),
            "error_logs": [],
            "detected_language": detect_language(query, config.default_language),
        }

    def submit(self, query: str, voice_mode: bool = False,
               on_done: Optional[callable] = None) -> bool:
        """Run one graph turn in a daemon thread. Returns False if busy."""
        if not self._busy.acquire(blocking=False):
            post_event("error", "MERLIN is still processing the previous request.")
            return False

        def worker() -> None:
            try:
                mark_turn_start()
                self.graph.invoke(self._initial_state(query, voice_mode))
            except Exception as exc:
                logger.exception("Graph turn crashed")
                post_event("error", f"Internal error: {exc}")
            finally:
                self._busy.release()
                if on_done:
                    on_done()

        threading.Thread(target=worker, daemon=True, name="merlin-turn").start()
        return True

    def submit_sync(self, query: str, voice_mode: bool = False) -> dict:
        """Synchronous invocation (CLI / smoke tests)."""
        mark_turn_start()
        return self.graph.invoke(self._initial_state(query, voice_mode))