"""Smoke test: runs the compiled graph (requires Ollama for full responses).

Run:  python scripts/smoke_test.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.events import next_event  # noqa: E402
from merlin.runtime import MerlinRuntime  # noqa: E402


def collect_response(timeout: float = 5.0) -> str | None:
    while (event := next_event(timeout=timeout)) is not None:
        if event.type == "merlin_response":
            return event.data
        if event.type == "error":
            print(f"  [event error] {event.data}")
    return None


def main() -> None:
    runtime = MerlinRuntime()
    queries = [
        "How much RAM am I using?",              # -> os_agent fast path (0 LLM)
        "search information about langgraph tutorial",  # -> web_agent + browser
        "Tell me a fun fact about Merlin the wizard",  # -> direct (gemma, streamed)
    ]
    failures = 0
    for query in queries:
        t0 = time.time()
        print(f"\nYou> {query}")
        runtime.submit_sync(query)
        response = collect_response(timeout=180)
        elapsed = time.time() - t0
        if response is None:
            print(f"  !! no response event captured ({elapsed:.1f}s)")
            failures += 1
        else:
            print(f"MERLIN> {response[:300]}")
            print(f"  [turn took {elapsed:.1f}s]")
    print(f"\nSmoke test finished with {failures} failure(s).")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
