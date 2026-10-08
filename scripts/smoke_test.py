"""Smoke test: runs the compiled graph offline (works with or without LM Studio).

Run:  python scripts/smoke_test.py
"""
from __future__ import annotations

import sys
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
        "How much RAM am I using?",          # -> os_agent (psutil, real)
        "Search arxiv papers about langgraph",  # -> web_agent (real ArXiv API)
        "Tell me a fun fact about Merlin the wizard",  # -> direct_response
    ]
    failures = 0
    for query in queries:
        print(f"\nYou> {query}")
        runtime.submit_sync(query)
        response = collect_response()
        if response is None:
            print("  !! no response event captured")
            failures += 1
        else:
            print(f"MERLIN> {response[:300]}")
    print(f"\nSmoke test finished with {failures} failure(s).")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
