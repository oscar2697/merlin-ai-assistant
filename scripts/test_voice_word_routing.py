"""Offline regression checks for Spanish voice command routing and Word workflow."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.agents.os_agent import _fast_steps, _plan_with_keywords, _write_plan
from merlin.router import ROUTE_DIRECT, ROUTE_OS, route_query
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

    # Real-world compound phrasing from the voice log: "create an essay" was
    # not recognized as a writing verb, so the LLM only opened Word.
    for utterance in (
        "Abre un archivo nuevo en Word y crea un ensayo sobre la relatividad.",
        "Abre Word y crea un archivo nuevo y haz un ensayo.",
        "Abre Word, crea un documento nuevo y escribe un ensayo sobre la relatividad",
        "Libre un archivo nuevo en Word y crea un ensayo sobre la relatividad. (Aquí dije: Abre)",
    ):
        compound = _write_plan(utterance)
        check(compound is not None, f"compound Word-writing command not recognized: {utterance}")
        check(compound["tool"] == "write_text", "compound command should invoke write_text")
        check(compound["app_name"] == "Word", "compound command should target Word")
        check(compound["create_new_document"] is True,
              "compound writing command should create a fresh document")
        check(_fast_steps(utterance) == [compound],
              "compound writing command should execute as one writing workflow")
    generic = _write_plan("Escribe sobre las matrices de Pauli")
    check(generic is not None and generic["app_name"] == "Word",
          "generic write request should target Word, not the foreground window")
    check(generic["create_new_document"] is True,
          "a new writing request should create a new document by default")
    essay = "Escribe un ensayo sobre las redes neuronales del cerebro."
    check(route_query(essay) == ROUTE_OS,
          "Spanish writing request without an app name should reach os_agent")
    essay_plan = _plan_with_keywords(essay)
    check(essay_plan["tool"] == "write_text" and essay_plan["app_name"] == "Word",
          "Spanish essay request should target Word writing")
    relativity = "Escribe un ensayo sobre la relatividad general."
    check(route_query(relativity) == ROUTE_OS,
          "general-relativity voice criterion should reach os_agent")
    relativity_plan = _plan_with_keywords(relativity)
    check(relativity_plan["tool"] == "write_text"
          and "relatividad general" in relativity_plan["instruction"],
          "general-relativity topic must be preserved for document generation")
    voseo = "Escribí un ensayo sobre la relatividad general con fuentes verificables."
    check(route_query(voseo) == ROUTE_OS,
          "Latin American voseo imperative should route to the writer")
    check(_plan_with_keywords(voseo).get("tool") == "write_text",
          "voseo imperative should produce a Word-writing plan")
    incoherent = "LaReward es escribiendo ensayos sobre las redes neuronales."
    check(route_query(incoherent) == ROUTE_DIRECT,
          "a descriptive, garbled gerund must not trigger document writing")
    check(_plan_with_keywords(incoherent).get("tool") != "write_text",
          "a non-imperative transcript must never create a Word-writing plan")
    general_as_noun = "Describiendo qué es la relatividad general, su origen, historia, etcétera."
    check(route_query(general_as_noun) == ROUTE_DIRECT,
          "the noun 'general' must not be mistaken for the verb 'genera'")
    check(_write_plan(general_as_noun) is None,
          "a descriptive fragment must not generate a truncated writing plan")
    generated = _write_plan("Genera un ensayo sobre la relatividad general")
    check(generated is not None and "relatividad general" in generated["instruction"],
          "the imperative 'genera' should remain supported with 'general' in its topic")

    itunes = _plan_with_keywords("Abre iTunes")
    check(itunes["tool"] == "open_app", "iTunes command was not routed to open_app")
    check(any(x.lower() == "itunes" for x in _app_candidates("itunes")),
          "iTunes alias candidates missing")

    print("PASS: combined Word-writing command, default Word target, and iTunes routing")


if __name__ == "__main__":
    main()
