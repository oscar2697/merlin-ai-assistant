"""Offline regression checks for Spanish voice command routing and Word workflow."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.agents.os_agent import _plan_with_keywords, _write_plan
from merlin.tools.system_tools import _app_candidates


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    plan = _write_plan("Abre Word y empieza a escribir sobre las matrices de Pauli")
    check(plan is not None, "combined Word + writing command was not routed")
    check(plan["app_name"] == "Word", "Word target was not detected")
    check(plan["create_new_document"] is True, "new-document intent was not detected")
    check("matrices de Pauli" in plan["instruction"], "topic was lost")
    generic = _write_plan("Escribe sobre las matrices de Pauli")
    check(generic is not None and generic["app_name"] == "Word",
          "generic write request should target Word, not the foreground window")
    check(generic["create_new_document"] is True,
          "a new writing request should create a new document by default")

    itunes = _plan_with_keywords("Abre iTunes")
    check(itunes["tool"] == "open_app", "iTunes command was not routed to open_app")
    check(any(x.lower() == "itunes" for x in _app_candidates("itunes")),
          "iTunes alias candidates missing")

    print("PASS: combined Word-writing command, default Word target, and iTunes routing")


if __name__ == "__main__":
    main()
